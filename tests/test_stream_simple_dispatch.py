"""Tests for the Phase 1.4 ``stream_simple`` dispatch shell (Section A).

Pi parity reference: ``packages/ai/src/stream.ts:42-46`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016`` (see ADR-0034, ADR-0038).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from aelix_ai import (
    AssistantEndEvent,
    AssistantMessage,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    StreamSimpleError,
    clear_providers,
    get_registered_providers,
    register_provider,
    stream_simple,
    unregister_provider,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Guarantee registry isolation between tests."""

    clear_providers()
    yield
    clear_providers()


async def test_stream_simple_no_provider_raises() -> None:
    """Empty registry → ``StreamSimpleError("no_provider_registered")``."""

    model = Model(api="anthropic")
    with pytest.raises(StreamSimpleError) as ei:
        it = await stream_simple(model, Context(), SimpleStreamOptions())
        async for _ in it:
            pass
    assert ei.value.code == "no_provider_registered"
    assert "anthropic" in str(ei.value)


async def test_stream_simple_no_provider_raises_eagerly() -> None:
    """Pi parity: error fires at call-time, not at iteration-time.

    Pi ``streamSimple`` is a synchronous ``function`` that throws before
    returning the iterator (``stream.ts:42-46`` at SHA ``734e08e…``). The
    Aelix port matches that semantic by resolving the provider before
    returning the async iterator.
    """

    model = Model(api="nonexistent")
    with pytest.raises(StreamSimpleError) as ei:
        await stream_simple(model, Context(), SimpleStreamOptions())
    assert ei.value.code == "no_provider_registered"


async def test_stream_simple_routes_to_registered_provider() -> None:
    """Registering a provider routes ``stream_simple`` to it."""

    msg = AssistantMessage(content=[])
    yielded = [AssistantStartEvent(partial=msg), AssistantEndEvent(message=msg)]

    async def fake_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        for ev in yielded:
            yield ev

    register_provider("fake-api", fake_stream)

    collected: list[AssistantMessageEvent] = []
    it = await stream_simple(Model(api="fake-api"), Context(), SimpleStreamOptions())
    async for ev in it:
        collected.append(ev)

    assert collected == yielded


async def test_unregister_provider_removes_it() -> None:
    """``unregister_provider`` removes the registration."""

    async def fake(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent()

    register_provider("x", fake)
    unregister_provider("x")
    # Second unregister is a no-op.
    unregister_provider("x")

    with pytest.raises(StreamSimpleError) as ei:
        await stream_simple(Model(api="x"), Context(), SimpleStreamOptions())
    assert ei.value.code == "no_provider_registered"


async def test_get_registered_providers_returns_copy() -> None:
    """Mutating the returned dict must not affect the registry."""

    async def fake(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent()

    register_provider("a", fake)
    snapshot = get_registered_providers()
    assert "a" in snapshot
    snapshot.clear()
    assert "a" in get_registered_providers()


async def test_register_overwrites_same_api() -> None:
    """Re-registering the same api key overwrites the previous fn."""

    called: list[str] = []

    async def first(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        called.append("first")
        yield AssistantStartEvent()

    async def second(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        called.append("second")
        yield AssistantStartEvent()

    register_provider("x", first)
    register_provider("x", second)

    it = await stream_simple(Model(api="x"), Context(), SimpleStreamOptions())
    async for _ in it:
        pass

    assert called == ["second"]


async def test_clear_providers_empties_registry() -> None:
    """``clear_providers`` removes every registration."""

    async def fake(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent()

    register_provider("a", fake)
    register_provider("b", fake)
    clear_providers()
    assert get_registered_providers() == {}
