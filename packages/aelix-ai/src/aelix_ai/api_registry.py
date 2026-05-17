"""Provider registry for :func:`aelix_ai.streaming.stream_simple`.

Phase 1.4 dispatch shell — Pi parity (``packages/ai/src/api-registry.ts`` at
SHA ``734e08edf82ff315bc3d96472a6ebfa69a1d8016``). The registry maps an
``api`` string (``"anthropic"``, ``"openai"``, ...) onto a
:class:`~aelix_ai.streaming.StreamFn` implementation. Phase 4 lands the
adapter modules under ``aelix_ai.providers``; until then the registry stays
empty and :func:`stream_simple` raises :class:`StreamSimpleError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_ai.streaming import StreamFn


_PROVIDERS: dict[str, StreamFn] = {}


def register_provider(api: str, fn: StreamFn) -> None:
    """Register a provider implementation for ``model.api == api``.

    Subsequent registrations for the same ``api`` overwrite the previous one
    (matches Pi: ``registerApiProvider`` replaces by api key).
    """

    _PROVIDERS[api] = fn


def unregister_provider(api: str) -> None:
    """Remove the provider for ``api``. No-op if absent.

    Pi equivalent: ``unregisterApiProviders(sourceId)`` (Pi keys by sourceId;
    Aelix keys by api for the Phase 1.4 shell — sourceId arrives in Phase 4
    when multiple adapters per api become real).
    """

    _PROVIDERS.pop(api, None)


def get_registered_providers() -> dict[str, StreamFn]:
    """Return a shallow copy of the registry.

    Read-only; mutating the result does not affect the registry.
    """

    return dict(_PROVIDERS)


def clear_providers() -> None:
    """Remove every registered provider. Pi: ``clearApiProviders()``."""

    _PROVIDERS.clear()


def _resolve_provider(api: str) -> StreamFn:
    fn = _PROVIDERS.get(api)
    if fn is None:
        from aelix_ai.streaming import StreamSimpleError

        raise StreamSimpleError(
            "no_provider_registered",
            (
                f"No provider registered for api={api!r}. "
                "Phase 4 will land Anthropic/OpenAI/OpenRouter adapters; "
                "until then, pass a mock stream_fn explicitly to the agent loop."
            ),
        )
    return fn


__all__ = [
    "clear_providers",
    "get_registered_providers",
    "register_provider",
    "unregister_provider",
]
