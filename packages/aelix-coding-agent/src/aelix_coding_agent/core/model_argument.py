"""Resolve an explicit ``/model <argument>`` against the LIVE model registry (#134).

``cli.runtime_bootstrap.resolve_model`` resolves a model for LAUNCH, where the
environment is the strongest signal available: with ``OPENROUTER_API_KEY`` set
and no explicit ``--provider`` it treats ANY argument as an OpenRouter id and
returns ``openrouter/<argument>`` on the OpenRouter host, falling back to a bare
model for ids its catalog never saw. That is right for a flag parsed before any
registry exists — and wrong for ``/model <id>`` typed INSIDE a session, where it
silently re-stamps the environment's provider onto an id that provider does not
serve: the switch reports success, the footer updates, the pair is persisted as
the default, and the only symptom is the provider's ``400 … is not a valid model
ID`` on the NEXT send (#134).

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
(``model-resolver.ts:160-174``, ported at ``core/model_resolver.py:278-302``),
which pi reaches ONLY when ``--provider`` explicitly names a provider — the same
"an explicitly named provider licenses an uncatalogued id" rule, applied to the
in-session command. Ours is strictly stricter: pi's does not check auth at all,
and pi's spreads a sibling model (fabricating its context window and pricing),
while ours builds the bare shape. pi has no in-session equivalent — its ``/model``
miss path just reopens the picker pre-filtered (``interactive-mode.ts:3966-4005``).

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
    3. that provider's siblings must be UNANIMOUS on ``api`` and on a non-empty
       ``base_url``.

    Rule 3 is ``cli.runtime_bootstrap._sibling_backfill``'s rule and exists for
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
    if not sep or not rest.strip() or not prefix.strip():
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

    return _Model(id=rest.strip(), provider=canonical, api=api, base_url=base_url)


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
    Bare ids never reach it.
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

    from aelix_coding_agent.core.runnable_models import partition_runnable

    runnable, _blocked = partition_runnable(pool)
    # Never over-filter to nothing: an all-blocked pool still produces a better
    # message from the diagnosis below than a bare "no such model".
    pool = runnable or pool

    from aelix_coding_agent.core.model_resolver import find_exact_model_reference_match

    match = find_exact_model_reference_match(reference, pool)
    if match is not None:
        return ArgumentResolution(model=match)

    candidates = _candidates(pool, reference)
    current_provider = getattr(current_model, "provider", None) or ""
    if current_provider and "/" not in reference:
        on_current = [m for m in candidates if m.provider == current_provider]
        if len(on_current) == 1:
            return ArgumentResolution(model=on_current[0])
    if candidates:
        listed = ", ".join(sorted({f"{m.provider}/{m.id}" for m in candidates}))
        return ArgumentResolution(
            error=(
                f"model '{reference}' is served by several providers — name one: "
                f"{listed}"
            )
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

    # Not offered. Distinguish "the registry has never heard of this" from "it
    # exists, but not for you right now": telling a logged-out user their id is
    # unknown sends them to fix the wrong thing.
    elsewhere = sorted(
        {getattr(m, "provider", "") or "?" for m in _candidates(known, reference)}
    )
    if elsewhere:
        return ArgumentResolution(
            error=(
                f"no model '{reference}' is available in this session — it is "
                f"served by {', '.join(elsewhere)}, which this session has no "
                "configured credentials for (or which is filtered out by "
                "/scoped-models). Run /login, or use <provider>/<id>."
            )
        )
    # Teach the #136 hatch by SHAPE, never by example. Naming a concrete
    # candidate here is unsafe: for a bare ``claude-haiku-4-5`` on an
    # OpenRouter-only session the only candidate to suggest is
    # ``openrouter/claude-haiku-4-5``, and for ``anthropic/claude-fable-5`` it is
    # ``openrouter/anthropic/claude-fable-5`` — i.e. the refusal would hand the
    # user the exact string #134 is about and the backfill would then honour it.
    # The shape alone is actionable and cannot be pasted back verbatim.
    return ArgumentResolution(
        error=(
            f"no model '{reference}' is available in this session — run /model "
            "with no argument to pick from the available models. To use an id "
            "this build's catalog does not know, write it as <provider>/<id> "
            "with a provider you are logged in to."
        )
    )


__all__ = ["ArgumentResolution", "resolve_model_argument"]
