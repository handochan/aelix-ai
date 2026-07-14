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
        provider = getattr(model, "provider", None)
        where = f"provider '{provider}'" if provider else "no provider"
        return (
            f"model '{model_id}' ({where}) could not be resolved to a known API "
            "protocol, so this build has no adapter to run it. Check the model id "
            "and provider spelling, or define the provider in models.json with an "
            'explicit "api" and "baseUrl".'
        )
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
    supported = ", ".join(sorted(supported_apis())) or "(none registered)"
    return (
        f"model '{model_id}' uses the '{api}' API, which this build has no adapter "
        f"for (supported: {supported}). Pick a model on a supported API (e.g. an "
        "openai-completions, openai-responses or anthropic-messages model)."
    )


__all__ = [
    "is_runnable",
    "partition_runnable",
    "supported_apis",
    "unsupported_message",
]
