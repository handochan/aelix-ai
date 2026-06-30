"""Filter/guard models to those whose ``api`` has a registered adapter (WP-8 follow-up).

The bundled catalog (``aelix_ai/models_generated.json``) can declare models for
APIs this build does NOT implement. Selecting such a model fails at the first turn
with a cryptic ``No provider registered for api=...`` raised by the PROTECTED
``aelix_ai.api_registry``. At startup ``cli/runtime_bootstrap.register_providers``
registers ``openai-completions`` + ``anthropic-messages`` + ``openai-responses``
(the last un-hidden in #15 Workflow B, surfacing OpenAI / GitHub Copilot gpt-5.x /
cloudflare-ai-gateway / opencode models); any remaining catalog api without an
adapter stays blocked.

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


def is_runnable(model: Any, apis: set[str] | None = None) -> bool:
    """True if ``model.api`` has a registered adapter (or the set is unknown).

    A model with no ``api`` attribute is treated as runnable (we can't prove it
    isn't). When ``apis`` is empty we cannot tell which adapters exist, so we
    never over-filter. A model whose ``base_url`` still holds an unexpanded
    ``{ENV_VAR}`` placeholder (required config missing) is NOT runnable even
    when its api is supported.
    """

    apis = supported_apis() if apis is None else apis
    if not apis:
        return True
    if _base_url_unconfigured(model):
        return False
    api = getattr(model, "api", None)
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
    # Config-missing case first: the api IS supported, but the templated
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
