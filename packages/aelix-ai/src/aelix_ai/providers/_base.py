"""Provider Protocol — Sprint 6a (ADR-0045).

Pi parity: ``packages/ai/src/types.ts`` (``StreamFunction`` +
``Provider`` shape, SHA ``734e08e``). Aelix exposes the contract as a
:class:`Provider` Protocol so adapters can implement two shapes — full
``stream`` and the simpler ``stream_simple`` — without coupling to a
concrete base class. The Sprint 6a Anthropic adapter (and future
OpenAI/OpenRouter adapters in Sprint 6b/6c) satisfy this Protocol.

Phase 1.4 back-compat: bare ``StreamFn`` callables registered via
:func:`aelix_ai.api_registry.register_provider(api, fn)` are wrapped into
a thin :class:`_BareStreamFnProvider` so the dispatcher continues to
receive an object that quacks like a :class:`Provider`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aelix_ai.streaming import (
        AssistantMessageEvent,
        Context,
        Model,
        SimpleStreamOptions,
        StreamFn,
    )


@runtime_checkable
class Provider(Protocol):
    """Pi ``Provider`` shape (ADR-0045 / Sprint 6a).

    Adapters MUST provide:
    - ``api`` — the registered API id (e.g. ``"anthropic-messages"``)
    - ``stream`` — async generator yielding :class:`AssistantMessageEvent`

    They MAY provide ``stream_simple`` for callers that want to bypass
    the simple-shape adapter; when omitted the dispatcher uses
    ``stream`` for both code paths.
    """

    api: str

    def stream(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]: ...


class _BareStreamFnProvider:
    """Phase 1.4 back-compat shim — wraps a bare :class:`StreamFn`.

    :func:`aelix_ai.api_registry.register_provider(api, fn)` is the
    legacy single-fn registration path. Sprint 6a routes everything
    through :class:`Provider` objects internally — this shim keeps that
    surface working for any adapter (or test) still registering a bare
    callable.
    """

    __slots__ = ("api", "_fn", "source_id")

    def __init__(self, api: str, fn: StreamFn, source_id: str | None = None) -> None:
        self.api = api
        self._fn = fn
        self.source_id = source_id

    def stream(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return self._fn(model, context, options)

    # Pi parity: simple == full when adapter exposes only one shape.
    stream_simple = stream


__all__ = ["Provider", "_BareStreamFnProvider"]
