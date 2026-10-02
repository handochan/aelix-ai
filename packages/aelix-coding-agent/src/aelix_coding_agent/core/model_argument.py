"""Resolve an explicit ``/model <argument>`` against the LIVE model registry (#134).

``cli.runtime_bootstrap.resolve_route`` resolves a model for LAUNCH (pi's
``resolveCliModel`` order, ADR-0250). Before ADR-0250 it was an
OpenRouter-from-env rung that, with ``OPENROUTER_API_KEY`` set, re-stamped the
environment's provider onto any argument — right enough for a flag parsed
before any session exists, and wrong for ``/model <id>`` typed INSIDE a session,
where the switch reported success, the footer updated, the pair was persisted
as the default, and the only symptom was the provider's ``400 … is not a valid
model ID`` on the NEXT send (#134).

This module is the in-session counterpart. It resolves the argument the way the
``/model`` picker does — against ``ModelRegistry`` narrowed by configured auth
and the ``/scoped-models`` allow-list — so the result is a properly scoped
``(provider, id)`` pair carrying that provider's real ``api``/``base_url``, or an
explicit refusal. It never guesses across providers.

BOTH argument forms go through that pool, including ``<provider>/<id>``. An
earlier revision of this module exempted the slash form on the theory that under
an ``OPENROUTER_API_KEY`` it is how OpenRouter's own canonical ids are written.
That exemption preserved #134 verbatim: OpenRouter's Anthropic ids are
DOT-versioned (``anthropic/claude-sonnet-4.5``), so a dash-versioned
``anthropic/claude-haiku-4-5`` missed the OpenRouter catalog and came back as a
fully-formed ``openrouter/anthropic/claude-haiku-4-5`` — moving a session that
was ALREADY on Anthropic, with the user's fully-qualified id, onto OpenRouter's
host and credentials.

:func:`find_exact_model_reference_match` (pi's own resolver) distinguishes the
two namespaces correctly against the real catalog: it tries the canonical
``provider/id`` key first, then a ``provider``/``id`` split, then the whole
string as a bare id. So ``anthropic/claude-haiku-4-5`` finds the Anthropic model,
``openrouter/auto`` finds OpenRouter's ``auto``, and OpenRouter's vendor-slashed
``anthropic/claude-sonnet-4.5`` still resolves as an OpenRouter model when no
Anthropic provider is available to claim it — each with its own base_url.

ONE EXCEPTION, and only one (#136). The registry is a BUILD-TIME snapshot, so a
model a gateway shipped after this build was cut is unreachable by name: measured
against OpenRouter's live ``/v1/models``, 179 of 400 ids (45%) are absent from
the bundled catalog. :func:`_gateway_backfill` restores the escape hatch for the
``<provider>/<id>`` form ONLY, and only when ``<provider>`` is one this session
has configured credentials for (it must appear in the offered pool). It then
carries that provider's own unanimous ``api``/``base_url``.

That cannot reopen #134, because the credentials used are the ones the user
NAMED. #134 was the environment silently substituting a provider nobody asked
for; here the prefix plays the role of ``--provider`` and no vendor is ever
crossed. A bare id gets NO backfill — no prefix means no licence — which is why
``claude-haiku-4-5`` on an OpenRouter-only session is still refused rather than
becoming ``openrouter/claude-haiku-4-5``. The accepted cost is that the bare
vendor-slashed gateway form (``moonshotai/kimi-k3-brand-new``) stays refused:
``moonshotai`` is itself a catalogued first-party provider, so reading a known
provider prefix as a gateway VENDOR segment would read
``anthropic/claude-haiku-4-5`` the same way — that IS #134. Distinguishing the
two needs the gateway's live model list, i.e. network I/O inside ``/model``, and
is deliberately out of scope. The user pays one extra ``openrouter/`` prefix.

This mirrors pi's own escape hatch, ``buildFallbackModel``
(``model-resolver.ts:160-174``, ported at ``core/model_resolver.py:421-445``),
which pi reaches ONLY when ``--provider`` explicitly names a provider — the same
"an explicitly named provider licenses an uncatalogued id" rule, applied to the
in-session command. Ours is strictly stricter: pi's does not check auth at all,
and pi's spreads a sibling model (fabricating its context window and pricing),
while ours builds the bare shape. pi has no in-session equivalent — its ``/model``
miss path just reopens the picker pre-filtered (``interactive-mode.ts:3966-4005``).

ADR-0250 guard 1, in-session (#362). The pool is ``get_available()``, which
counts a credential a cwd ``.env`` supplied — so a planted vendor key decided
what an OpenRouter user's ``/model openai/gpt-4o-mini`` switched to, and the
switch is then PERSISTED for every later launch (measured on the design
prototype: ``['openai', 'gpt-4o-mini']`` under a ``.env`` ``OPENAI_API_KEY``).
:func:`_route_aware_pool` therefore removes every provider only a ``.env``
authenticates whenever the session holds a credential of the user's own (an
``--api-key`` runtime override included), so a cwd ``.env`` key never chooses
the destination — where it would, ``/model`` refuses (fail-closed) — with no
exemption for the session's current provider (Codex's cross-review of
``854bf319``, C1/C2). :func:`_user_defined_owner` applies the launch's provider
case rule: a prefix naming a provider the user defined stays inside it, and
since the prefix already chose that destination, the provider is kept whatever
authenticates it, a ``.env`` key included, as ``--model`` keeps it (ADR-0250
§2.8; the round-2 verification of ``ecb4e0bc``, B1). A session whose only
credentials came from a ``.env`` keeps this module's answer (ADR-0250 §6 —
nothing of the user's own competes there).

Degradation: no registry, an empty registry, or a failed introspection returns
UNDECIDED so the caller keeps its previous behaviour (headless, RPC, test
doubles). Resolution must never be the thing that breaks a session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from aelix_ai.streaming import Model


@dataclass(frozen=True)
class ArgumentResolution:
    """Outcome of resolving one ``/model <argument>``.

    One of four states:

    * ``model`` set — a scoped ``(provider, id)`` the caller should switch to;
    * ``model`` AND ``caution`` set (#136) — switch, but the id was NOT in the
      registry: it was backfilled from an explicitly named, credentialled
      provider, so the caller must report it as a CAUTION rather than the green
      success line. ``caution`` is a ready-to-print one-liner;
    * ``error`` set — an actionable refusal to commit; the caller must NOT
      switch, persist, or print success;
    * all :data:`None` (UNDECIDED) — this module has no opinion; the caller
      falls back to :func:`aelix_coding_agent.cli.runtime_bootstrap.resolve_model`.
    """

    model: Model | None = None
    error: str | None = None
    caution: str | None = None


_UNDECIDED = ArgumentResolution()


def _candidates(models: list[Any], argument: str) -> list[Any]:
    """Every model in ``models`` that ``argument`` could name, case-folded.

    Mirrors the three readings :func:`find_exact_model_reference_match` tries, so
    the diagnosis below reports the same set that resolution just failed to
    collapse to one: the canonical ``provider/id`` key, a ``provider``/``id``
    split, and the whole argument as a bare id. A vendor-slashed gateway id
    (OpenRouter's ``anthropic/claude-sonnet-4.5``) is found by the third reading
    while a real provider-scoped reference is found by the second — which is why
    both must be tried here rather than only the one that "looks" right.
    """

    lowered = argument.lower()
    provider, _, rest = argument.partition("/")
    provider, rest = provider.lower(), rest.lower()
    found: list[Any] = []
    for model in models:
        model_id = (getattr(model, "id", "") or "").lower()
        model_provider = (getattr(model, "provider", "") or "").lower()
        if (
            model_id == lowered
            or f"{model_provider}/{model_id}" == lowered
            or (rest and model_provider == provider and model_id == rest)
        ):
            found.append(model)
    return found


def _is_catalogued(provider: str, model_id: str, known: list[Any]) -> bool:
    """Does ``known`` hold ``model_id`` under exactly ``provider``?

    The precise ``(provider, id)`` question, deliberately narrower than
    :func:`_candidates`: that one case-folds three READINGS of an argument to
    diagnose a miss, whereas this asks whether the pair the backfill is about to
    mint already exists. Case-folded on the id only, since the catalog's provider
    ids are canonical by construction and ``provider`` here is always one the
    pool supplied.
    """

    wanted = model_id.lower()
    return any(
        (getattr(m, "provider", "") or "") == provider
        and (getattr(m, "id", "") or "").lower() == wanted
        for m in known
    )


def _gateway_backfill(
    reference: str, pool: list[Any], known: list[Any]
) -> Model | None:
    """A ``<provider>/<id>`` the catalog never saw, under a CREDENTIALLED provider.

    The registry is a build-time snapshot, so an id a provider shipped after this
    build is unreachable by name (#136). This restores the escape hatch under the
    narrowest rule that cannot become #134:

    1. the argument must have a prefix — a bare id names no provider, so nothing
       licenses guessing one (that guess IS #134);
    2. the prefix must name a provider present in ``pool``. ``pool`` is already
       auth-filtered (``get_available``) AND ``/scoped-models``-narrowed, so this
       one membership test buys both gates for free and stays in lock-step with
       what the picker offers. A provider the session cannot use, or that the
       allow-list excluded, therefore backfills nothing;
    3. the id must be ABSENT from ``known`` for that provider. Rule 2 is
       provider-granular but ``/scoped-models`` is a ``(provider, id)`` allow-list,
       so a provider can be in the pool while THIS id was excluded from it. Such
       an id is not uncatalogued — it is catalogued-and-de-scoped, and backfilling
       it would resurrect a model the user switched off, in the bare shape, for a
       model whose real ``context_window``/``cost`` the catalog holds. Measured on
       the bundled catalog: ``openrouter/anthropic/claude-sonnet-4.5`` under the
       allow-list ``["openrouter/qwen/qwen3-max"]`` came back with
       ``context_window`` 1,000,000 → 0 and cost 3.0/15.0 → 0.0, under a caution
       line asserting it "is not in this build's catalog" — a statement the
       catalog itself contradicts. #136 is about ids the catalog NEVER SAW; this
       rule keeps the hatch to exactly those;
    4. that provider's siblings must be UNANIMOUS on ``api`` and on a non-empty
       ``base_url``.

    Rule 4 is ``cli.runtime_bootstrap._sibling_backfill``'s rule and exists for
    its reason: a ``siblings[0]`` guess routed a github-copilot id to the ANTHROPIC
    adapter, and that adapter's ``base_url or None`` collapses to the SDK default
    host — so a Copilot OAuth bearer left the process for ``api.anthropic.com``
    (#98). Six catalog providers span several apis (github-copilot, opencode,
    opencode-go, cloudflare-ai-gateway, fireworks, amazon-bedrock); every one of
    them is declined here. We go further than ``_sibling_backfill`` and decline a
    non-unanimous ``base_url`` too, rather than emitting ``base_url=""`` — an empty
    base_url is the #98 credential-egress shape itself.

    Siblings come from ``known`` (the registry's ``get_all``), NOT from
    ``aelix_ai.models.get_models`` the way ``_sibling_backfill`` does: the static
    catalog knows nothing about a ``models.json`` custom provider or an extension
    ``register_provider``, so sourcing from it would make the hatch silently never
    fire for exactly the users who hand-configured a provider. Auth is still
    enforced against ``pool``; ``known`` only supplies the protocol/host.

    Returns the BARE :class:`~aelix_ai.streaming.Model` shape — no
    ``dataclasses.replace`` of a sibling. Measured: replacing an OpenRouter
    sibling's id inherits ``context_window=256000`` and ``cost`` 2.0/8.0 in, for a
    model nothing is known about, so ``/cost`` would report invented dollars
    (regressing "report tools, stats and cost honestly"). The bare shape's zeros
    are also exactly what the SAME session gets after a restart, when the
    persisted pair goes back through ``runtime_bootstrap.resolve_model`` — so the
    two paths agree instead of disagreeing by 256k tokens. The zeros do disable
    the context meter and zero ``/cost``; the caller's caution line says so.
    """

    prefix, sep, rest = reference.partition("/")
    if not sep or not prefix.strip():
        return None
    model_id = rest.strip()
    # Every path segment must be non-empty: ``openrouter//brand-new`` would
    # otherwise mint the id ``/brand-new``. No provider serves such an id, and
    # google/google-vertex interpolate ``model.id`` into a URL PATH, so a
    # segment-malformed id is the one shape worth refusing outright rather than
    # forwarding for the provider to reject.
    if not model_id or any(not segment.strip() for segment in model_id.split("/")):
        return None

    # Auth + allow-list gate, in the pool's own casing (provider ids are lowercase
    # in the catalog, but a user types what they like).
    offered = {
        (getattr(m, "provider", "") or "").lower(): getattr(m, "provider", "")
        for m in pool
    }
    canonical = offered.get(prefix.lower())
    if not canonical:
        return None

    # Rule 3 — catalogued for this provider means DE-SCOPED, not uncatalogued.
    # The caller turns this into a refusal that names the real cause; returning a
    # backfill here would both resurrect the model and lie about why.
    if _is_catalogued(canonical, model_id, known):
        return None

    siblings = [m for m in known if getattr(m, "provider", "") == canonical]
    if not siblings:
        return None
    apis = {getattr(m, "api", None) for m in siblings}
    if len(apis) != 1:
        return None
    api = next(iter(apis))
    if not api or api == "unknown":
        return None
    base_urls = {getattr(m, "base_url", "") or "" for m in siblings}
    if len(base_urls) != 1:
        return None
    base_url = next(iter(base_urls))
    if not base_url:
        return None

    from aelix_ai.streaming import Model as _Model

    return _Model(id=model_id, provider=canonical, api=api, base_url=base_url)


async def resolve_model_argument(
    argument: str,
    *,
    registry: Any,
    current_model: Any = None,
    settings_manager: Any = None,
    warn: Callable[[str], None] | None = None,
) -> ArgumentResolution:
    """Resolve a ``/model`` argument to a scoped model, or refuse.

    :param argument: the raw argument — a bare id (``claude-haiku-4-5``) or a
        provider-scoped reference (``anthropic/claude-haiku-4-5``).
    :param registry: the live :class:`~aelix_coding_agent.model_registry.ModelRegistry`
        (duck-typed: ``get_all`` / ``get_available``); :data:`None` → UNDECIDED.
    :param current_model: the session's current model, used ONLY as the bare-id
        tie-break below.
    :param settings_manager: threaded into
        :func:`~aelix_coding_agent.core.scoped_models_filter.scoped_available` so
        a ``/scoped-models`` allow-list narrows this the same way it narrows the
        picker.
    :param warn: one-line sink for the allow-list empty-match warning.

    The offered pool mirrors ``run_model_picker``: auth-filtered
    (``get_available``) → allow-list → :func:`partition_runnable`. Matching is
    pi's :func:`find_exact_model_reference_match`.

    When that returns :data:`None` because SEVERAL providers serve the argument,
    one tie-break applies, and only to a BARE id: the provider the session is
    already on wins, since "switch model" inside a github-copilot session means
    "switch within my seat", not "move vendors". A ``<provider>/<id>`` argument
    gets no tie-break — it NAMES a provider, so silently substituting a different
    one is the exact failure this module exists to prevent. Any remaining
    ambiguity is reported with the candidates listed, never guessed: the bundled
    catalog serves ``gpt-5.4`` from six providers and ``anthropic/claude-sonnet-4.5``
    from two, and picking one would send that provider's credentials to whichever
    sorted first.

    Last, before refusing a ``<provider>/<id>`` outright, :func:`_gateway_backfill`
    gets a turn (#136): a build-time catalog cannot know a model released after
    it, so an id under a provider the user NAMED and is credentialled for
    resolves to that provider's own ``api``/``base_url`` with ``caution`` set.
    Bare ids never reach it, and neither does an id the catalog already holds for
    that provider — that one is DE-SCOPED rather than unknown, so it is refused
    with the cause named instead of resurrected in a degraded shape.
    """

    reference = argument.strip()
    if not reference or registry is None:
        return _UNDECIDED

    try:
        known = list(registry.get_all())
    except Exception:  # noqa: BLE001 — introspection must never break /model
        return _UNDECIDED
    # An empty registry cannot prove an argument wrong (a stub, or a registry
    # that failed to load) — stay out of the way rather than refuse everything.
    if not known:
        return _UNDECIDED

    try:
        from aelix_coding_agent.core.scoped_models_filter import scoped_available

        pool = list(await scoped_available(registry, settings_manager, warn=warn))
    except Exception:  # noqa: BLE001 — a settings/registry read must never lock us out
        try:
            pool = list(registry.get_available())
        except Exception:  # noqa: BLE001
            # Do NOT fall back to get_all(): it spans providers with no
            # configured credentials, so an id unique among THOSE would resolve
            # and switch, trading a clear refusal for a confusing auth failure.
            # ``known`` stays available for the diagnosis text only.
            return _UNDECIDED

    # The provider case rule (ADR-0249 §2.1, ADR-0250 §2.1 step 1): a
    # ``<prefix>/<id>`` whose prefix names a provider the USER defined stays
    # inside it, as ``--model`` does — never another provider's id that happens
    # to read the same (OpenRouter's ``openai/gpt-4o-mini`` against a
    # models.json ``OpenAI``).
    owned = _user_defined_owner(reference, registry)
    if isinstance(owned, ArgumentResolution):
        return owned
    if owned is not None:
        # The prefix IS the destination, chosen by the user who defined the
        # provider, so guard 1 has nothing to decide: the provider stays in the
        # pool whatever authenticates it, and a cwd ``.env`` key may then
        # authenticate the route the user chose — the launch's step 3 (ADR-0250
        # §2.1, §2.8). The round-2 verification of ``ecb4e0bc`` (B1): dropping
        # it refused ``/model mygw/m1``, and the late switch to a
        # ``session_start`` provider whose key is in the ``.env``, both of which
        # ``--model`` runs.
        dropped: frozenset[str] = frozenset()
        pool = [m for m in pool if (getattr(m, "provider", "") or "") == owned]
        if not pool:
            return ArgumentResolution(
                error=(
                    f"model '{reference}' names '{owned}', a provider you defined, which "
                    "this session does not offer (no credential for it, or "
                    "/scoped-models excludes it). Run /model with no argument to pick one."
                )
            )
    else:
        # ADR-0250 guard 1, in-session (#362): a cwd ``.env`` key never chooses
        # the destination. Whenever the user holds a route-authenticating
        # credential of their own anywhere, a provider that only a ``.env`` key
        # authenticates leaves the pool before anything below reads it — the
        # match, the current-provider tie-break, the backfill licence and the
        # diagnosis alike.
        pool, dropped = _route_aware_pool(pool, registry)

    from aelix_coding_agent.core.runnable_models import partition_runnable

    runnable, _blocked = partition_runnable(pool)
    # Never over-filter to nothing: an all-blocked pool still produces a better
    # message from the diagnosis below than a bare "no such model".
    pool = runnable or pool

    from aelix_coding_agent.core.model_resolver import find_exact_model_reference_match

    current_provider = getattr(current_model, "provider", None) or ""

    match = find_exact_model_reference_match(reference, pool)
    if match is not None:
        return ArgumentResolution(model=match)

    candidates = _candidates(pool, reference)
    if current_provider and "/" not in reference:
        on_current = [m for m in candidates if m.provider == current_provider]
        if len(on_current) == 1:
            return ArgumentResolution(model=on_current[0])
    if candidates:
        listed = ", ".join(sorted({f"{m.provider}/{m.id}" for m in candidates}))
        return ArgumentResolution(
            error=(f"model '{reference}' is served by several providers — name one: {listed}")
        )

    # #136 — a `<provider>/<id>` under a provider this session IS credentialled
    # for. Placed AFTER the ambiguity block so a genuinely multi-provider id
    # keeps its better "name one" message, and BEFORE the diagnosis below so an
    # explicitly named, usable provider outranks "it lives elsewhere".
    backfilled = _gateway_backfill(reference, pool, known)
    if backfilled is not None:
        return ArgumentResolution(
            model=backfilled,
            caution=(
                f"'{backfilled.id}' is not in this build's catalog for "
                f"{backfilled.provider} — if the id is wrong, the provider will "
                "reject it on the next send. Context-window and cost figures are "
                "unknown for it, so the context meter and /cost read zero. Add it "
                "to models.json (or /login → custom provider) to make it "
                "first-class."
            ),
        )
    if owned is not None:
        # Inside a provider the user defined. When it LISTS the id, the pool
        # simply does not offer it — say so, as the "IS logged in to" refusal
        # below does; blaming disagreeing siblings named the wrong cause
        # (Codex's second cross-review of ``a0edf615``, F5: MyGw lists m1 and
        # m2 on one host, the allow-list keeps only MyGw/m1).
        lists_it = find_exact_model_reference_match(
            reference, [m for m in known if (getattr(m, "provider", "") or "") == owned]
        )
        if lists_it is not None:
            return ArgumentResolution(
                error=(
                    f"model '{reference}' is one '{owned}' (a provider you defined) "
                    "lists, but it is not offered here: /scoped-models excludes it, or "
                    "it is not runnable in this environment. Run /scoped-models to "
                    "re-enable it, or /model with no argument to see what is offered."
                )
            )
        # It neither lists the id nor agrees on one api/base_url to send an
        # unlisted one to. Naming the providers elsewhere that serve the string
        # would invite exactly the crossing the case rule forbids.
        return ArgumentResolution(
            error=(
                f"model '{reference}' is not one '{owned}' (a provider you defined) "
                "offers, and its models do not agree on one api and base URL to send "
                "an unlisted id to. Add it to that provider's models in models.json, "
                "or run /model with no argument to pick one."
            )
        )

    # Not offered. Distinguish "the registry has never heard of this" from "it
    # exists, but not for you right now": telling a logged-out user their id is
    # unknown sends them to fix the wrong thing. And split that second case in
    # two, because its two causes need OPPOSITE actions — a provider with no
    # credentials wants /login, while a provider the session is already using
    # means this id specifically was dropped by /scoped-models (or is not
    # runnable here), and telling that user to log in is simply false.
    offered_providers = {getattr(m, "provider", "") or "" for m in pool}
    elsewhere = sorted(
        {getattr(m, "provider", "") or "?" for m in _candidates(known, reference)}
    )
    excluded = [p for p in elsewhere if p in offered_providers]
    unauthed = [p for p in elsewhere if p not in offered_providers]
    planted = [p for p in unauthed if p in dropped]
    if excluded:
        return ArgumentResolution(
            error=(
                f"model '{reference}' is in this build's catalog for "
                f"{', '.join(excluded)}, which this session IS logged in to — but "
                "the model itself is not offered here: /scoped-models excludes it, "
                "or it is not runnable in this environment. Run /scoped-models to "
                "re-enable it, or /model with no argument to see what is offered."
                + _dotenv_hint(planted)
            )
        )
    if unauthed:
        return ArgumentResolution(
            error=(
                f"no model '{reference}' is available in this session — it is "
                f"served by {', '.join(unauthed)}, which this session has no "
                "configured credentials for (or which is filtered out by "
                "/scoped-models). Run /login, or use <provider>/<id>." + _dotenv_hint(planted)
            )
        )
    # Teach the #136 hatch by SHAPE, never by example. Naming a concrete
    # candidate here is unsafe: for a bare ``claude-haiku-4-5`` on an
    # OpenRouter-only session the only candidate to suggest is
    # ``openrouter/claude-haiku-4-5``, and for ``anthropic/claude-fable-5`` it is
    # ``openrouter/anthropic/claude-fable-5`` — i.e. the refusal would hand the
    # user the exact string #134 is about and the backfill would then honour it.
    # The shape alone is actionable and cannot be pasted back verbatim.
    #
    # An id the catalog does not hold, under a prefix naming a provider guard 1
    # set aside, lands here too (``anthropic/claude-new-9`` with the Anthropic
    # key only in a cwd ``.env``): ``_candidates`` finds nothing, so the branch
    # above never names it. The user believes they are logged in to that
    # provider — say why it was set aside (the round-3 verification of #362).
    head = reference.split("/", 1)[0].lower() if "/" in reference else ""
    return ArgumentResolution(
        error=(
            f"no model '{reference}' is available in this session — run /model "
            "with no argument to pick from the available models. To use an id "
            "this build's catalog does not know, write it as <provider>/<id> "
            "with a provider you are logged in to."
            + _dotenv_hint(sorted(p for p in dropped if head and p.lower() == head))
        )
    )


def _dotenv_hint(planted: list[str]) -> str:
    """The refusal's note naming the providers guard 1 set aside, or ``""``."""

    if not planted:
        return ""
    return (
        f" ({', '.join(planted)}: the key came from a project .env, which "
        "does not choose a provider while you hold a credential of your "
        "own; export it or /login to use it.)"
    )


def _route_aware_pool(pool: list[Any], registry: Any) -> tuple[list[Any], frozenset[str]]:
    """``pool`` without the providers only a cwd ``.env`` authenticates — when that matters.

    ADR-0250 guard 1 for ``/model`` (§2.8). ``pool`` came from
    ``get_available()``, where a credential a cwd ``.env`` supplied counts. When
    the session holds a route-authenticating credential of the user's own for
    ANY provider (:func:`~aelix_coding_agent.cli.runtime_bootstrap.holds_route_auth`
    — not read off this pool, since an allow-list must not turn a
    user-with-their-own-key into a ``.env``-only session, nor off model rows at
    all, since a provider holding the user's key may serve none: Codex's second
    cross-review of ``a0edf615``, F3), every provider that is not
    route-authenticated leaves the pool,
    so a ``.env`` key cannot choose the destination; where it would have, the
    caller refuses. That is usually the answer the same session gives with no
    ``.env``, not always: an allow-list naming only the ``.env`` provider's
    models, or a models.json / ``auth.json`` key NAMING a variable only the
    ``.env`` sets, refuse (or leave the string to the user's own keys) where the
    no-``.env`` session resolves (ADR-0250 §2.8). A runtime override
    (``--api-key``) is the user's own on both reads. The caller skips this for a
    prefix naming a provider the user defined.

    There is no exemption for the session's CURRENT provider. Codex's
    cross-review of ``854bf319`` measured the one ``854bf319`` had: with
    ``OPENROUTER_API_KEY`` exported and the session on ``openai``, a cwd ``.env``
    ``OPENAI_API_KEY`` moved ``/model openai/gpt-4o-mini`` from ``openrouter.ai``
    (the user's key) to ``api.openai.com`` (the file's key) — the session being
    "on" a provider only the file authenticates is no licence.

    A session with no credential of the user's own keeps its pool: nothing of
    theirs competes (ADR-0250 §6, the stated residual). Returns the pool and the
    providers taken out of it (for the refusal's hint). Fails open to the pool
    as given — a guard must never break ``/model`` — except that a provider
    whose check raised is treated as not route-authenticated.

    The other implicit choosers use this same helper, so one rule answers all
    of them: the post-``/login`` pick (``tui.shell._RouteAuthView``) and RPC
    ``cycle_model``'s rotation (``rpc.rpc_mode._handle_cycle_model``; Codex's
    second cross-review of ``a0edf615``, F1 and F2).
    """

    try:
        from aelix_coding_agent.cli.runtime_bootstrap import holds_route_auth, route_authenticated
    except Exception:  # noqa: BLE001 — a guard must never break /model
        return pool, frozenset()
    if not holds_route_auth(registry):
        return pool, frozenset()
    pooled = {getattr(m, "provider", "") or "" for m in pool}
    dropped = frozenset(p for p in pooled if not route_authenticated(registry, p))
    if not dropped:
        return pool, dropped
    return [m for m in pool if (getattr(m, "provider", "") or "") not in dropped], dropped


def _user_defined_owner(reference: str, registry: Any) -> str | ArgumentResolution | None:
    """The user-defined provider a ``<prefix>/<id>`` names, by aelix's case rule.

    ``--model`` matches a user-defined provider first and alone (ADR-0249 §2.1,
    kept by the owner in ADR-0250 §2.1 step 1), and a user-defined provider never
    lets the string leave it. ``/model`` used the pool's own case-folded match,
    so with a models.json ``OpenAI`` next to a credentialled built-in ``openai``
    (or OpenRouter's verbatim ``openai/gpt-4o-mini``) the same string landed on a
    provider the user had not meant. Returns the provider's name, a refusal when
    two user-defined names differ only in case and the prefix spells neither
    (the launch's rule), or ``None`` — a bare id, or a prefix no user-defined
    provider answers to.
    """

    prefix, sep, rest = reference.partition("/")
    if not (sep and prefix.strip() and rest.strip()):
        return None
    try:
        from aelix_coding_agent.cli.runtime_bootstrap import _user_defined_prefix

        owner, clash = _user_defined_prefix(prefix.strip(), _case_rule_providers(registry))
    except Exception:  # noqa: BLE001 — a guard must never break /model
        return None
    if clash:
        names = " and ".join(f"'{name}'" for name in clash)
        return ArgumentResolution(
            error=(
                f"model '{reference}': the provider prefix '{prefix}' matches the "
                f"providers you defined {names}, which differ only in case. Spell the "
                "prefix exactly as one of them."
            )
        )
    return owner


def _case_rule_providers(registry: Any) -> frozenset[str]:
    """The providers ``/model``'s case rule treats as user-defined.

    The launch's set (``runtime_bootstrap.user_defined_providers``), plus
    ``openrouter`` when models.json re-points it. The launch drops
    ``openrouter`` from its set because there an ``openrouter/<id>`` is an
    explicit route anyway (ADR-0250 §2.5) and "user-defined" would only keep
    it from guard 2; ``/model`` has no such explicit-route rule (§2.8), so
    without this a re-pointed ``openrouter`` — a provider whose endpoint the
    user chose, like every other re-pointed built-in — was refused on a
    ``.env`` key that ``--model openrouter/auto`` runs on (Codex's second
    cross-review of ``a0edf615``, F6). A ``openrouter`` the user did NOT
    re-point stays route-deciding: ``/model openrouter/<id>`` is refused while
    a ``.env`` alone authenticates it and the user holds a key elsewhere (F7).
    """

    from aelix_coding_agent.cli.runtime_bootstrap import user_defined_providers

    found = user_defined_providers(registry)
    getter = getattr(registry, "get_user_defined_providers", None)
    if callable(getter):
        try:
            returned: Any = getter()
            if "openrouter" in frozenset(str(name) for name in returned):
                found = found | {"openrouter"}
        except Exception:  # noqa: BLE001 — a guard must never break /model
            pass
    return found


__all__ = ["ArgumentResolution", "resolve_model_argument"]
