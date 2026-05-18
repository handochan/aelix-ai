"""Provider registry for :func:`aelix_ai.streaming.stream_simple`.

Phase 1.4 dispatch shell — Pi parity (``packages/ai/src/api-registry.ts`` at
SHA ``734e08edf82ff315bc3d96472a6ebfa69a1d8016``). The registry maps an
``api`` string (``"anthropic-messages"``, ``"openai-completions"``, ...)
onto a :class:`~aelix_ai.providers._base.Provider` implementation.

Sprint 6a (Phase 4.1, ADR-0045) extends the Phase 1.4 dispatch shell with:

- :func:`register_provider_object(provider, source_id=None)` — register a
  :class:`Provider` Protocol implementer. Sprint 6a's Anthropic adapter
  registers itself via this path.
- :func:`unregister_providers_by_source(source_id)` — Pi parity with
  ``unregisterApiProviders(sourceId)``. Removes every provider previously
  registered with the given ``source_id``.

The Phase 1.4 :func:`register_provider(api, fn)` continues to work — it
now wraps the bare callable in :class:`_BareStreamFnProvider` so the
dispatcher only ever sees a :class:`Provider`-shaped object.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from aelix_ai.providers._base import Provider, _BareStreamFnProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from aelix_ai.streaming import (
        AssistantMessageEvent,
        Context,
        Model,
        SimpleStreamOptions,
        StreamFn,
    )


# Internal registry — Sprint 6a switches the value type to ``Provider`` (the
# Phase 1.4 ``StreamFn`` shape is now wrapped in ``_BareStreamFnProvider``).
_PROVIDERS: dict[str, Provider] = {}


def register_provider(api: str, fn: StreamFn, source_id: str | None = None) -> None:
    """Register a bare :class:`StreamFn` callable for ``model.api == api``.

    Phase 1.4 back-compat path. The callable is wrapped in
    :class:`_BareStreamFnProvider` so internal callers always see a
    :class:`Provider`-shaped object.

    Subsequent registrations for the same ``api`` overwrite the previous
    one (matches Pi: ``registerApiProvider`` replaces by api key).
    """

    _PROVIDERS[api] = _BareStreamFnProvider(api=api, fn=fn, source_id=source_id)


def register_provider_object(
    provider: Provider, source_id: str | None = None
) -> None:
    """Sprint 6a (ADR-0045) — register a :class:`Provider` Protocol object.

    The provider's ``api`` field is the registry key. ``source_id``
    propagates onto the registry entry so
    :func:`unregister_providers_by_source` can find it later.
    """

    # Some providers expose a settable ``source_id`` attribute; we set
    # it opportunistically so the source-id-based unregister works even
    # when the adapter omitted it from its own construction. Frozen
    # dataclasses / read-only Protocols swallow the assignment — the
    # registry's ``getattr(..., None)`` lookup handles that case.
    with contextlib.suppress(AttributeError, TypeError):
        provider.source_id = source_id
    _PROVIDERS[provider.api] = provider


def unregister_provider(api: str) -> None:
    """Remove the provider for ``api``. No-op if absent.

    Pi equivalent: ``unregisterApiProvider(api)``.
    """

    _PROVIDERS.pop(api, None)


def unregister_providers_by_source(source_id: str) -> None:
    """Sprint 6a (ADR-0045) — Pi ``unregisterApiProviders(sourceId)`` parity.

    Removes every registered provider whose ``source_id`` matches.
    Providers registered without a ``source_id`` are left alone.
    """

    to_remove = [
        api
        for api, prov in _PROVIDERS.items()
        if getattr(prov, "source_id", None) == source_id
    ]
    for api in to_remove:
        _PROVIDERS.pop(api, None)


def get_registered_providers() -> dict[str, Provider]:
    """Return a shallow copy of the registry.

    Read-only; mutating the result does not affect the registry.
    """

    return dict(_PROVIDERS)


def clear_providers() -> None:
    """Remove every registered provider. Pi: ``clearApiProviders()``."""

    _PROVIDERS.clear()


def _resolve_provider(api: str) -> StreamFn:
    """Internal dispatcher helper — returns a :class:`StreamFn`-callable.

    Sprint 6a: provider entries are now :class:`Provider` objects; we
    return a thin closure that calls ``provider.stream`` to match the
    Phase 1.4 :class:`StreamFn` signature without changing the
    ``stream_simple`` shell.
    """

    provider = _PROVIDERS.get(api)
    if provider is None:
        from aelix_ai.streaming import StreamSimpleError

        raise StreamSimpleError(
            "no_provider_registered",
            (
                f"No provider registered for api={api!r}. "
                "Sprint 6a ships the Anthropic adapter under "
                "``aelix_ai.providers.anthropic`` — call "
                "``aelix_ai.providers.anthropic.register_all()`` to wire "
                "it up, OR pass a mock stream_fn explicitly to the agent loop."
            ),
        )

    def _stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        # Prefer ``stream_simple`` when the adapter exposes it
        # explicitly (Pi parity); fall through to ``stream``.
        fn = getattr(provider, "stream_simple", None) or provider.stream
        return fn(model, context, options)

    return _stream


__all__ = [
    "clear_providers",
    "get_registered_providers",
    "register_provider",
    "register_provider_object",
    "unregister_provider",
    "unregister_providers_by_source",
]
