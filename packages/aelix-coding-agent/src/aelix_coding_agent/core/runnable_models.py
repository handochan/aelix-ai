"""Filter/guard models to those whose ``api`` has a registered adapter (WP-8 follow-up).

The bundled catalog (``aelix_ai/models_generated.json``) can declare models for
APIs this build does NOT implement. Selecting such a model fails at the first turn
with a cryptic ``No provider registered for api=...`` raised by the PROTECTED
``aelix_ai.api_registry``. At startup ``cli/runtime_bootstrap.register_providers``
registers ``openai-completions`` + ``anthropic-messages`` + ``openai-responses``
+ ``google-generative-ai`` + ``google-vertex`` + ``openai-codex-responses``
(the middle three un-hidden in #15 Workflow B, surfacing OpenAI / GitHub Copilot
gpt-5.x / cloudflare-ai-gateway / opencode / Gemini Developer API / Vertex AI
models; ``openai-codex-responses`` surfaces the ChatGPT Plus/Pro Codex models —
before it was registered they showed in ``/scoped-models`` but were hidden here);
any remaining catalog api without an adapter stays blocked. ``google-vertex`` models additionally stay
hidden until GCP auth is resolvable (see :func:`_vertex_config_missing`).

This helper lets the TUI **hide** unrunnable models from the ``/model`` picker and
**guard** an explicit ``/model <id>`` / picker selection with a clear, actionable
message instead of the cryptic provider error. It reads the live registered-API
set from ``aelix_ai.api_registry.get_registered_providers`` — when that set is
empty (providers not wired yet, e.g. headless/tests) it treats every model as
runnable so it never over-filters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


def supported_apis() -> set[str]:
    """The set of ``api`` ids that currently have a registered provider adapter.

    Empty when the registry is unpopulated (providers not yet registered) — the
    callers treat an empty set as "can't tell → don't filter".
    """

    try:
        from aelix_ai.api_registry import get_registered_providers

        return set(get_registered_providers().keys())
    except Exception:  # noqa: BLE001 — introspection must never break a flow
        return set()


def _base_url_unconfigured(model: Any) -> bool:
    """True when ``model.base_url`` still carries an unexpanded ``{ENV_VAR}``.

    A cloudflare-ai-gateway base_url is templated
    (``…/{CLOUDFLARE_ACCOUNT_ID}/{CLOUDFLARE_GATEWAY_ID}/openai``); the client
    builders expand those tokens from the environment. When the required env
    vars are NOT set the token survives, so the model would hit a malformed URL
    at the first turn — it is treated as not runnable (kept hidden) until
    configured. Introspection-only; never raises.
    """

    base_url = getattr(model, "base_url", None)
    if not base_url:
        return False
    try:
        from aelix_ai.providers._base_url import has_unexpanded_placeholders

        return has_unexpanded_placeholders(base_url)
    except Exception:  # noqa: BLE001 — introspection must never break a flow
        return False


def _base_url_missing(model: Any) -> bool:
    """True when ``model`` DECLARES an empty ``base_url`` (no host at all).

    Every adapter resolves its host as ``base_url or None`` (``providers/
    anthropic.py``, ``openai_responses.py``, ``openai_completions.py``), so an
    empty string collapses to the SDK's built-in FIRST-PARTY host — AsyncAnthropic
    → api.anthropic.com, AsyncOpenAI → api.openai.com. For a model whose
    ``provider`` is not that vendor, that silently ships the provider's
    credentials to a third party (#98): an extension calling
    ``register_provider("mycorp", ...models={"corp-x": Model(api="anthropic-messages")})``
    omits base_url (the dataclass default is ``""``) and ``ModelRegistry._load_models``
    step 3b merges it VERBATIM — no host is ever injected.

    A model with no host is therefore treated as required-config-missing, exactly
    like the unexpanded-placeholder case above. This is also the rule
    ``models.json`` already enforces at load: ``models_json.py`` drops any custom
    model whose baseUrl resolves empty (``if not base_url: continue``) rather than
    letting it run — which is why models.json cannot reach this and an extension
    ``register_provider`` is the one live vector.

    Distinguishes ABSENT (:data:`None` → an object that never declares a host,
    e.g. a duck-typed stand-in → unprovable, stays runnable) from DECLARED-EMPTY
    (``""`` → the real :class:`~aelix_ai.streaming.Model` dataclass default →
    blocked). Introspection-only; never raises.
    """

    return getattr(model, "base_url", None) == ""


_GOOGLE_VERTEX_API = "google-vertex"

# The ``aelix_ai.streaming.Model`` dataclass default for ``api`` — a sentinel for
# "resolution never named a protocol", NOT a real api id (#98). Keep in sync with
# that default.
_UNRESOLVED_API = "unknown"

# Hint naming the env var(s) that satisfy Vertex auth (for ``unsupported_message``).
_VERTEX_CONFIG_HINT = (
    "set GOOGLE_CLOUD_API_KEY, or both a project "
    "(GOOGLE_CLOUD_PROJECT / GCLOUD_PROJECT) and GOOGLE_CLOUD_LOCATION"
)


def _vertex_config_missing(model: Any) -> bool:
    """True when a ``google-vertex`` model has no resolvable GCP auth.

    Mirrors pi's Vertex auth resolution (``google-vertex.ts`` ``resolveApiKey``
    / ``resolveProject`` / ``resolveLocation``): client construction succeeds
    only with a valid ``GOOGLE_CLOUD_API_KEY`` (NOT a ``<...>`` placeholder nor
    the ``gcp-vertex-credentials`` marker), OR Application Default Credentials
    backed by a project (``GOOGLE_CLOUD_PROJECT`` / ``GCLOUD_PROJECT``) AND a
    location (``GOOGLE_CLOUD_LOCATION``). When neither is satisfiable a vertex
    model would raise at the first turn, so it is treated as not runnable (kept
    hidden) — the cloudflare required-config precedent. Non-vertex models are
    never gated here. Introspection-only; never raises.

    NOTE: vertex catalog models carry a ``https://{location}-aiplatform…``
    base_url whose ``{location}`` token is filled by the SDK from the resolved
    project/location (``_resolve_vertex_custom_base_url`` ignores it), NOT from
    an env var — so the generic ``_base_url_unconfigured`` placeholder guard
    must be bypassed for vertex (its caller does so) and replaced by this one.
    """

    if getattr(model, "api", None) != _GOOGLE_VERTEX_API:
        return False
    try:
        import os

        from aelix_ai.providers.google_vertex import _resolve_vertex_api_key

        if _resolve_vertex_api_key(os.environ.get("GOOGLE_CLOUD_API_KEY")):
            return False
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get(
            "GCLOUD_PROJECT"
        )
        location = os.environ.get("GOOGLE_CLOUD_LOCATION")
        return not (project and location)
    except Exception:  # noqa: BLE001 — introspection must never break a flow
        return False


def is_runnable(model: Any, apis: set[str] | None = None) -> bool:
    """True if ``model.api`` has a registered adapter (or the set is unknown).

    A model with no ``api`` attribute is treated as runnable (we can't prove it
    isn't). When ``apis`` is empty we cannot tell which adapters exist, so we
    never over-filter. A model whose ``base_url`` still holds an unexpanded
    ``{ENV_VAR}`` placeholder, or which declares no ``base_url`` at all
    (required config missing), is NOT runnable even when its api is supported —
    see :func:`_base_url_missing` for why a hostless model is a credential-egress
    hazard rather than a mere misconfiguration. A ``google-vertex`` model is NOT
    runnable unless GCP auth is resolvable (its templated ``{location}`` base_url
    is filled by the SDK, so it uses :func:`_vertex_config_missing` instead of
    the generic placeholder guard).
    """

    apis = supported_apis() if apis is None else apis
    if not apis:
        return True
    api = getattr(model, "api", None)
    if api == _GOOGLE_VERTEX_API:
        if _vertex_config_missing(model):
            return False
        return api in apis
    if _base_url_unconfigured(model) or _base_url_missing(model):
        return False
    return api is None or api in apis


def partition_runnable(models: Iterable[Any]) -> tuple[list[Any], list[Any]]:
    """Split ``models`` into ``(runnable, blocked)`` by registered-API support.

    When no APIs are registered (headless/tests) everything is runnable (the
    blocked list is empty) so existing behaviour is unchanged.
    """

    apis = supported_apis()
    if not apis:
        return list(models), []
    runnable: list[Any] = []
    blocked: list[Any] = []
    for model in models:
        (runnable if is_runnable(model, apis) else blocked).append(model)
    return runnable, blocked


# Reason ids returned by :func:`blocked_reason`. They exist so a caller that has
# to describe MANY blocked models at once (the first-run cascade) can group them
# by *why* instead of asserting one reason for all of them.
BLOCKED_UNRESOLVED_API = "unresolved-api"
BLOCKED_NO_ADAPTER = "no-adapter"
BLOCKED_VERTEX_CONFIG = "vertex-config-missing"
BLOCKED_CONFIG_MISSING = "config-missing"
BLOCKED_NO_HOST = "no-host"

# Returned by :func:`provider_block_reason` when EVERY model of a provider is
# blocked but NOT all for the same reason. There is no such provider in the
# bundled catalog today (each of the six blocked ones is unanimous), but an
# extension that registers a mixed bag would otherwise force a caller to assert
# one reason for models that do not share it.
#
# It is a SUMMARY, not a reason: it names none of the reasons it stands for, so
# nothing about the provider can be decided from it. A caller that has to decide
# something — the ``/login`` label, say — must read
# :func:`provider_block_reasons` instead. It is never a MEMBER of that set.
BLOCKED_MIXED = "mixed"

# The reason ids the USER can act on. The other two are dead ends: no amount of
# configuration conjures an adapter this build does not ship, and an api that
# never resolved names no protocol to configure.
_RECOVERABLE_REASONS = frozenset(
    {BLOCKED_CONFIG_MISSING, BLOCKED_VERTEX_CONFIG, BLOCKED_NO_HOST}
)

# Label-sized restatement of :data:`_VERTEX_CONFIG_HINT` (which is a sentence
# fragment sized for :func:`unsupported_message`). Kept adjacent to it so the
# two never drift: both name GOOGLE_CLOUD_API_KEY and the project+location pair.
_VERTEX_ENV_SHORT = (
    "set GOOGLE_CLOUD_API_KEY, or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION"
)


def blocked_reason(model: Any, apis: set[str] | None = None) -> str:
    """Why :func:`is_runnable` rejects ``model`` — ``""`` when it does not.

    :func:`is_runnable` blocks a model for FIVE distinct reasons, and only one of
    them is "this build ships no adapter for that api". The other four are
    required-config failures on APIs that ARE registered: unexpanded ``{ENV_VAR}``
    base-URL placeholders (cloudflare), missing GCP auth (vertex), a hostless
    model (#98), and an api that never resolved at all. Any caller that prints a
    single reason for a whole SET of blocked models needs this split — otherwise
    it names the same api as both unsupported and supported in one sentence.

    Ordering differs from :func:`unsupported_message` in exactly one deliberate
    way: an api with no registered adapter is classified BEFORE the config
    branches, because no amount of configuration can conjure an adapter, so
    "set CLOUDFLARE_ACCOUNT_ID" / "set a baseUrl" would be useless advice there.
    ``azure-openai-responses`` is the live case — its rows declare an empty
    ``baseUrl`` *and* an api this build does not implement.
    """

    apis = supported_apis() if apis is None else apis
    if not apis or is_runnable(model, apis):
        return ""
    api = getattr(model, "api", None)
    if api == _UNRESOLVED_API:
        return BLOCKED_UNRESOLVED_API
    if api is not None and api not in apis:
        return BLOCKED_NO_ADAPTER
    if _vertex_config_missing(model):
        return BLOCKED_VERTEX_CONFIG
    if _base_url_unconfigured(model):
        return BLOCKED_CONFIG_MISSING
    if _base_url_missing(model):
        return BLOCKED_NO_HOST
    # Unreachable via the current predicate; classified as a config problem
    # rather than invented as an adapter problem.
    return BLOCKED_CONFIG_MISSING


def provider_block_reasons(
    models: Iterable[Any], apis: set[str] | None = None
) -> frozenset[str]:
    """EVERY distinct reason a provider's models are blocked — empty when one runs.

    :func:`blocked_reason` answers for one model; the ``/login`` provider picker
    has to answer for a whole provider BEFORE any credential exists, so it can
    tell the user that a key stored here will never run anything. That question
    is answerable pre-login precisely because :func:`is_runnable` is
    credential-blind: it tests adapter registration and base-URL/GCP config only.

    This is the LOSSLESS form, and the one any caller that has to DECIDE
    something must use. :func:`provider_block_reason` collapses this set to a
    single string, and that collapse is where a decision goes wrong: two
    different but equally fixable reasons collapse to :data:`BLOCKED_MIXED`,
    which names neither, so a caller reading the sentinel cannot tell a provider
    one env var away from a provider this build can never run.

    FAILS OPEN in three distinct ways, because "unknown" is not "broken":

    1. No registered adapters (headless/tests) → empty. Matches
       :func:`partition_runnable`, which calls everything runnable there.
    2. ``models`` EMPTY → empty. An extension may register a provider whose
       models arrive later (#77); a provider we know nothing about must never be
       labelled unusable.
    3. ANY runnable model → empty, even when most rows are blocked.

    :data:`BLOCKED_MIXED` is never a member: it is a summary of this set, not an
    element of it.
    """

    apis = supported_apis() if apis is None else apis
    if not apis:
        return frozenset()
    rows = list(models)
    if not rows:
        return frozenset()
    reasons: set[str] = set()
    for model in rows:
        if is_runnable(model, apis):
            return frozenset()
        reasons.add(blocked_reason(model, apis))
    return frozenset(reasons)


def blocked_summary(models: Iterable[Any], apis: set[str] | None = None) -> str:
    """One dim line describing WHY a whole SET of models is blocked — ``""`` if none.

    :func:`provider_block_hint` answers for one provider; a picker that has just
    hidden (or annotated) a mixed pile of models from SEVERAL providers has to
    say why in one line, grouped by reason so it never asserts one reason for
    models that do not share it (#153). Shape::

        set CLOUDFLARE_ACCOUNT_ID (cloudflare-workers-ai 8); set
        CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_GATEWAY_ID (cloudflare-ai-gateway 35);
        no adapter in this build (mistral 1)

    Groups by (reason, HINT), not by reason alone. Two providers can share the
    reason ``config-missing`` and need DIFFERENT variables: grouping on the
    reason unions their names, so the line told a ``cloudflare-workers-ai`` user
    to set ``CLOUDFLARE_GATEWAY_ID``, which that provider does not use::

        workers-ai alone: set CLOUDFLARE_ACCOUNT_ID (cloudflare-workers-ai 8)
        both together   : set CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_GATEWAY_ID
                          (cloudflare-ai-gateway 35, cloudflare-workers-ai 8)

    Never WRONG — setting the union does fix every provider in the group — but
    over-broad, and it disagreed with the per-row hints, which are precise. It
    matters more now that this line is the ONLY place the variable names appear
    (#153 round 2: the names no longer fit on a picker row).

    Groups are emitted in sorted (reason, hint) order and providers in sorted
    name order, so the line is deterministic. Runnable models are skipped; an
    empty ``apis`` (headless/tests) yields ``""`` because nothing is blocked
    there.

    This exists because the two live call sites both used to hardcode ONE
    example — ``model_picker``'s hidden-count line said "e.g. openai-responses /
    Copilot gpt-5.x" long after ``openai-responses`` gained an adapter, so it
    named a supported api as the reason models were hidden.
    """

    apis = supported_apis() if apis is None else apis
    if not apis:
        return ""
    rows = list(models)
    # (reason id, hint) → provider → count. The hint is derived PER MODEL — for
    # ``config-missing`` it is that model's own unexpanded base-URL placeholders
    # — so models needing different variables land in different groups.
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for model in rows:
        reason = blocked_reason(model, apis)
        if not reason:
            continue
        hint = _single_block_hint(reason, [model], apis) or reason
        provider = getattr(model, "provider", None) or "?"
        by_provider = counts.setdefault((reason, hint), {})
        by_provider[provider] = by_provider.get(provider, 0) + 1
    if not counts:
        return ""
    parts: list[str] = []
    for key in sorted(counts):
        providers = ", ".join(f"{p} {n}" for p, n in sorted(counts[key].items()))
        parts.append(f"{key[1]} ({providers})")
    return "; ".join(parts)


def provider_block_reason(models: Iterable[Any], apis: set[str] | None = None) -> str:
    """Why NOTHING a provider offers can run — ``""`` when something can (#151).

    A one-string SUMMARY of :func:`provider_block_reasons`, kept for callers that
    only need to name the situation (logs, a single-reason assertion). It returns
    :data:`BLOCKED_MIXED` when the rows disagree, so no caller has to pick a
    winner among reasons that contradict each other.

    Do NOT branch user-facing wording on this: the sentinel discards exactly the
    information such a branch needs. Read :func:`provider_block_reasons` and pass
    the set to :func:`is_recoverable_block` / :func:`provider_block_hint`.
    """

    reasons = provider_block_reasons(models, apis)
    if not reasons:
        return ""
    if len(reasons) == 1:
        return next(iter(reasons))
    return BLOCKED_MIXED


def is_recoverable_block(reasons: str | Iterable[str]) -> bool:
    """True when EVERY reason given names something the USER can fix.

    The split the ``/login`` picker needs: "set CLOUDFLARE_ACCOUNT_ID" is a next
    step, "this build ships no adapter" is a dead end, and presenting them
    identically would send a user hunting for a config knob that does not exist.

    Accepts one reason id or a whole set of them (:func:`provider_block_reasons`),
    and answers from the reasons themselves — never from a summary of them. An
    earlier version took only a single string and hardcoded :data:`BLOCKED_MIXED`
    to ``False`` on the claim that a mixed set "contains at least one reason that
    is not recoverable". That claim was never true: a provider whose rows are
    blocked by ``config-missing`` AND ``no-host`` has two reasons, both fixable,
    and was still labelled a dead end while its own detail panel named the env
    var to set. Passing the sentinel here still yields ``False`` — it is not in
    :data:`_RECOVERABLE_REASONS` and cannot be, because it names no reason at all.

    Empty → ``False``: nothing is blocked, so there is nothing to recover from,
    and the callers gate on "is it blocked" first.
    """

    ids = frozenset({reasons} if isinstance(reasons, str) else reasons)
    return bool(ids) and ids <= _RECOVERABLE_REASONS


def dead_end_reasons(reasons: str | Iterable[str]) -> frozenset[str]:
    """The subset of ``reasons`` that NO configuration can rescue — #156.

    :func:`is_recoverable_block` is all-or-nothing by design, and three of its
    five call sites pass a single reason where that is exactly right. The one
    thing it cannot express is a MIXED provider: eight rows blocked on a
    missing env var and one on a missing adapter answer ``False``, which is
    true — the provider is not wholly recoverable — but callers then reached for
    the all-dead-end WORDING, and told the user that nothing the provider
    offers can run in this build. Measured false: setting one env var took that
    provider from 0 runnable models to 8.

    Returning the subset rather than adding a third boolean is what keeps the
    vocabulary flat. Callers get all three states from
    ``(bool(dead), bool(ids - dead))`` — all-recoverable, all-dead-end, mixed —
    without a new enum to keep in sync with :data:`_RECOVERABLE_REASONS`.

    Note this deliberately does NOT widen :func:`is_recoverable_block`'s return
    type. That function is compared with ``is`` at one call site
    (:func:`provider_block_sample`), where a non-bool would silently stop
    matching; and its own docstring records a previous over-reach on this same
    predicate. A new function costs nothing and cannot break the old one.

    :data:`BLOCKED_MIXED` is a summary sentinel that names no reason, so it is
    reported as a dead end here for the same reason it is not in
    :data:`_RECOVERABLE_REASONS` — it carries no evidence that anything is
    fixable. Callers with real reason sets should never pass it.
    """

    ids = frozenset({reasons} if isinstance(reasons, str) else reasons)
    return ids - _RECOVERABLE_REASONS


def provider_block_sample(
    models: Iterable[Any], recoverable: bool, apis: set[str] | None = None
) -> Any:
    """A blocked row whose OWN reason agrees with the provider-level label.

    The picker shows a provider-level label ("needs setup" / "unusable") and,
    beside it, the full sentence for ONE of that provider's models. When the rows
    disagree, an arbitrary row (the first) can carry the opposite verdict — a
    panel reading "set EXT_ACCOUNT_ID" under a label reading "unusable", or the
    reverse. This picks a row that argues for the same conclusion the user is
    reading.

    Falls back to the first row when none matches (or when nothing is blocked),
    so a caller always has something to show.
    """

    apis = supported_apis() if apis is None else apis
    rows = list(models)
    if not rows:
        return None
    for model in rows:
        reason = blocked_reason(model, apis)
        if reason and is_recoverable_block(reason) is recoverable:
            return model
    return rows[0]


def provider_block_hint(
    reasons: str | Iterable[str],
    models: Iterable[Any] = (),
    apis: set[str] | None = None,
) -> str:
    """A short, label-sized restatement of ``reasons`` — ``""`` when unknown.

    The long form is :func:`unsupported_message`, which is model-scoped and
    sentence-length; a picker ROW cannot carry that, so this returns the few
    words that fit beside a provider id.

    Accepts one reason id or a whole set (:func:`provider_block_reasons`). For a
    set that disagrees, the hint must agree with the label the caller derives
    from the SAME set via :func:`is_recoverable_block`:

    * all-fixable → the individual hints joined (deterministic, sorted by reason
      id), because each one is a real next step and dropping any of them hides
      work the user still has to do;
    * all-dead-end → the dead-end phrasing, accurate because no reason can be
      configured away;
    * MIXED (#156) → the dead-end SUBSET named, then the fixable next steps.
      This case used to fall into the branch above and tell the user nothing
      the provider offers can run in this build — disproved by setting one env
      var and watching 8 of its 9 models become runnable. The provider-level
      verdict stays "unusable" (a #151 decision: one unrescuable model is
      enough to earn it), but the sentence now says which models it applies to.

    ``models`` supplies the concrete env var names for
    :data:`BLOCKED_CONFIG_MISSING` (read off the unexpanded base-URL
    placeholders, the same source ``unsupported_message`` reads) — the union
    across the rows that are blocked FOR THAT REASON, so a provider whose models
    template different vars names all of them, and a row blocked for some other
    reason cannot contribute a name that would not help. ``apis`` is the
    registered-adapter set used to re-derive each row's own reason; it defaults
    to the live one. Introspection-only; never raises.
    """

    ids = frozenset({reasons} if isinstance(reasons, str) else reasons)
    if not ids:
        return ""
    if len(ids) > 1:
        dead = dead_end_reasons(ids)
        if dead == ids:
            # Every reason is a dead end — the literal is simply true.
            return "nothing it offers can run in this build"
        if dead:
            # MIXED (#156). The old code took this path into the branch above
            # and claimed nothing could run, while the fixable majority ran
            # fine as soon as one env var was set. Name the dead-end SUBSET and
            # keep the fixable next steps beside it, because both are true and
            # the user needs both: some models need configuration, others
            # cannot be rescued at all.
            fixable = "; ".join(
                part
                for part in dict.fromkeys(
                    _single_block_hint(reason_id, models, apis)
                    for reason_id in sorted(ids - dead)
                )
                if part
            )
            dead_part = "; ".join(
                part
                for part in dict.fromkeys(
                    _single_block_hint(reason_id, models, apis)
                    for reason_id in sorted(dead)
                )
                if part
            )
            if fixable and dead_part:
                # Colons rather than verbs: the two halves come from
                # ``_single_block_hint``, whose outputs are a mix of noun
                # phrases ("no adapter in this build") and imperatives ("set
                # CLOUDFLARE_ACCOUNT_ID"). No single connective verb reads
                # correctly against both, and a row label is not the place to
                # start conjugating.
                return f"some models: {dead_part}; others: {fixable}"
            return dead_part or fixable
        parts: list[str] = []
        for reason_id in sorted(ids):
            part = _single_block_hint(reason_id, models, apis)
            if part and part not in parts:
                parts.append(part)
        return "; ".join(parts)
    return _single_block_hint(next(iter(ids)), models, apis)


def _rows_blocked_for(
    reason: str, models: Iterable[Any], apis: set[str] | None
) -> list[Any]:
    """The subset of ``models`` whose OWN :func:`blocked_reason` is ``reason``.

    A hint built from every row of a provider can name something that belongs to
    a DIFFERENT row's problem. Live case: a ``google-vertex`` row carries a
    ``https://{location}-aiplatform…`` base_url whose ``{location}`` is filled by
    the SDK, not by an env var — unioned into the ``config-missing`` hint beside a
    real cloudflare var it produced ``set CLOUDFLARE_ACCOUNT_ID, location``, and
    there is no ``location`` variable to set. Only rows that are blocked for the
    reason being described may speak to it.

    Falls back to ALL rows if the re-derivation matches nothing, so a caller that
    passes a hand-built reason with duck-typed rows still gets its old answer.
    """

    rows = list(models)
    try:
        matched = [m for m in rows if blocked_reason(m, apis) == reason]
    except Exception:  # noqa: BLE001 — introspection must never break a flow
        return rows
    return matched or rows


def _single_block_hint(
    reason: str, models: Iterable[Any] = (), apis: set[str] | None = None
) -> str:
    """The one-reason body of :func:`provider_block_hint` — ``""`` when unknown."""

    if reason == BLOCKED_NO_ADAPTER:
        return "no adapter in this build"
    if reason == BLOCKED_UNRESOLVED_API:
        return "no resolvable API protocol"
    if reason == BLOCKED_VERTEX_CONFIG:
        return _VERTEX_ENV_SHORT
    if reason == BLOCKED_NO_HOST:
        return "no base URL configured"
    if reason == BLOCKED_MIXED:
        return "nothing it offers can run in this build"
    if reason == BLOCKED_CONFIG_MISSING:
        names: list[str] = []
        try:
            from aelix_ai.providers._base_url import unexpanded_placeholder_names

            for model in _rows_blocked_for(reason, models, apis):
                base_url = getattr(model, "base_url", None)
                if not base_url:
                    continue
                for name in unexpanded_placeholder_names(base_url):
                    if name not in names:
                        names.append(name)
        except Exception:  # noqa: BLE001 — introspection must never break a flow
            names = []
        return f"set {', '.join(names)}" if names else "required configuration missing"
    return ""


def _unresolved_api_message(model: Any) -> str:
    """The ``api == "unknown"`` branch of :func:`unsupported_message` (#98).

    Extracted so :func:`blocked_message` can reach it by REASON rather than by
    re-running ``unsupported_message``'s different branch ordering.
    """

    model_id = getattr(model, "id", None) or "?"
    provider = getattr(model, "provider", None)
    where = f"provider '{provider}'" if provider else "no provider"
    return (
        f"model '{model_id}' ({where}) could not be resolved to a known API "
        "protocol, so this build has no adapter to run it. Check the model id "
        "and provider spelling, or define the provider in models.json with an "
        'explicit "api" and "baseUrl".'
    )


def _no_adapter_message(model: Any) -> str:
    """The "this build ships no adapter for that api" branch, extracted.

    See :func:`_unresolved_api_message` for why the branches are addressable
    individually.
    """

    model_id = getattr(model, "id", None) or "?"
    api = getattr(model, "api", None) or "?"
    supported = ", ".join(sorted(supported_apis())) or "(none registered)"
    return (
        f"model '{model_id}' uses the '{api}' API, which this build has no adapter "
        f"for (supported: {supported}). Pick a model on a supported API (e.g. an "
        "openai-completions, openai-responses or anthropic-messages model)."
    )


def blocked_message(model: Any, apis: set[str] | None = None) -> str:
    """The reason sentence that AGREES with :func:`blocked_reason` — ``""`` if runnable.

    :func:`unsupported_message` orders its branches config-first; :func:`blocked_reason`
    orders adapter-first, deliberately (no configuration conjures an adapter). For
    most models the two land on the same branch, but ``azure-openai-responses`` is
    a live counter-example: its rows declare BOTH an empty ``baseUrl`` and an api
    this build does not implement, so ``blocked_reason`` says "no adapter" while
    ``unsupported_message`` says "set a baseUrl" — advice that cannot help.

    Anything showing a reason id and its prose SIDE BY SIDE (the #151 ``/login``
    picker: a short label suffix plus a detail panel) must not contradict itself,
    so this dispatches on the reason and delegates to ``unsupported_message`` only
    for the reasons where the two orderings provably coincide.
    """

    reason = blocked_reason(model, apis)
    if not reason:
        return ""
    if reason == BLOCKED_NO_ADAPTER:
        return _no_adapter_message(model)
    if reason == BLOCKED_UNRESOLVED_API:
        return _unresolved_api_message(model)
    return unsupported_message(model)


def unsupported_message(model: Any) -> str:
    """A one-line, actionable reason a model can't run (for a committed error)."""

    model_id = getattr(model, "id", None) or "?"
    # Vertex GCP-config case: the api IS supported, but no GCP auth is
    # resolvable (no key, no project+location) — name the env var(s) to set.
    if _vertex_config_missing(model):
        return (
            f"model '{model_id}' needs Google Cloud configuration before it can "
            f"run: {_VERTEX_CONFIG_HINT}, then re-select it."
        )
    # Config-missing case: the api IS supported, but the templated
    # base_url has unexpanded ``{ENV_VAR}`` tokens (e.g. cloudflare-ai-gateway).
    if _base_url_unconfigured(model):
        base_url = getattr(model, "base_url", None)
        try:
            from aelix_ai.providers._base_url import unexpanded_placeholder_names

            missing = ", ".join(unexpanded_placeholder_names(base_url)) or "(unknown)"
        except Exception:  # noqa: BLE001 — introspection must never break a flow
            missing = "(unknown)"
        return (
            f"model '{model_id}' needs configuration before it can run: set the "
            f"environment variable(s) {missing} to fill the base-URL "
            "placeholder(s), then re-select it."
        )
    api = getattr(model, "api", None) or "?"
    # Unresolved-api case (#98): ``api`` is still the ``Model`` dataclass default,
    # which means resolution FAILED to name a protocol for this provider/model
    # pair — the model does not "use the 'unknown' API", and telling the user to
    # "pick a model on a supported API" misdescribes a choice they never made.
    # Reached from an uncatalogued provider (a typo, a models.json custom, an
    # extension ``register_provider``), an unresolvable bare id, or a provider
    # whose catalog spans several apis. Deliberately does NOT name ``/model``:
    # the non-interactive callers have no such command, so each caller appends
    # its own instruction.
    if api == _UNRESOLVED_API:
        return _unresolved_api_message(model)
    # Hostless case (#98): the api IS supported, but the model declares no
    # base_url — so the adapter would silently fall back to its SDK's first-party
    # vendor host and send THIS provider's credentials there. Ordered AFTER the
    # unresolved-api branch on purpose: a bare model carries api="unknown" AND an
    # empty base_url, and "we could not resolve it" is the accurate half.
    if _base_url_missing(model):
        provider = getattr(model, "provider", None) or "?"
        return (
            f"model '{model_id}' (provider '{provider}') declares no base URL, so "
            f"a request would fall back to the built-in vendor host of the '{api}' "
            f"adapter and send {provider}'s credentials there. Set an explicit "
            '"baseUrl" for the provider (models.json), or have the registering '
            "extension pass base_url on the model."
        )
    return _no_adapter_message(model)


__all__ = [
    "BLOCKED_CONFIG_MISSING",
    "BLOCKED_MIXED",
    "BLOCKED_NO_ADAPTER",
    "BLOCKED_NO_HOST",
    "BLOCKED_UNRESOLVED_API",
    "BLOCKED_VERTEX_CONFIG",
    "blocked_message",
    "blocked_reason",
    "blocked_summary",
    "dead_end_reasons",
    "is_recoverable_block",
    "is_runnable",
    "partition_runnable",
    "provider_block_hint",
    "provider_block_reason",
    "provider_block_reasons",
    "provider_block_sample",
    "supported_apis",
    "unsupported_message",
]
