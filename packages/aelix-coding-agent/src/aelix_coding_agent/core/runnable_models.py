"""Filter/guard models to those whose ``api`` has a registered adapter (WP-8 follow-up).

The bundled catalog (``aelix_ai/models_generated.json``) declares models for APIs
this build does NOT implement — notably ``openai-responses`` (OpenAI / GitHub
Copilot gpt-5.x), for which ``aelix_ai`` ships **no** adapter (only
``openai-completions`` + ``anthropic-messages`` register at startup, see
``cli/runtime_bootstrap.register_providers``). Selecting such a model fails at the
first turn with a cryptic ``No provider registered for api=...`` raised by the
PROTECTED ``aelix_ai.api_registry``. A user who OAuth-signs-in to GitHub Copilot
(``/login``) and picks a gpt-5.x model hits exactly that.

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


def is_runnable(model: Any, apis: set[str] | None = None) -> bool:
    """True if ``model.api`` has a registered adapter (or the set is unknown).

    A model with no ``api`` attribute is treated as runnable (we can't prove it
    isn't). When ``apis`` is empty we cannot tell which adapters exist, so we
    never over-filter.
    """

    apis = supported_apis() if apis is None else apis
    if not apis:
        return True
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

    api = getattr(model, "api", None) or "?"
    model_id = getattr(model, "id", None) or "?"
    supported = ", ".join(sorted(supported_apis())) or "(none registered)"
    return (
        f"model '{model_id}' uses the '{api}' API, which this build has no adapter "
        f"for (supported: {supported}). Pick a model on a supported API "
        "(e.g. an openai-completions or anthropic-messages model)."
    )


__all__ = [
    "is_runnable",
    "partition_runnable",
    "supported_apis",
    "unsupported_message",
]
