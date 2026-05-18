"""Sprint 6a (Phase 4.1, ADR-0045 §A) — Provider Protocol + registry tests.

Verifies:

- :func:`register_provider_object` accepts any Provider-shaped object.
- :func:`unregister_providers_by_source` removes only entries with the
  matching ``source_id``.
- :func:`register_provider` (Phase 1.4 bare-callable shim) keeps working
  alongside the new Protocol object path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from aelix_ai import (
    AssistantDoneEvent,
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
    register_provider_object,
    stream_simple,
    unregister_providers_by_source,
)


class _FakeProvider:
    """Provider Protocol implementer for tests."""

    api: str

    def __init__(self, api: str, source_id: str | None = None) -> None:
        self.api = api
        self.source_id = source_id

    async def stream(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantDoneEvent(
            reason="stop", message=AssistantMessage(stop_reason="stop")
        )

    async def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        async for ev in self.stream(model, context, options):
            yield ev


@pytest.fixture(autouse=True)
def _reset_registry():
    clear_providers()
    yield
    clear_providers()


async def test_register_provider_object_routes_streams() -> None:
    """Provider objects route through ``stream_simple``."""

    provider = _FakeProvider(api="fake-api")
    register_provider_object(provider, source_id="aelix-ai.builtin")

    it = await stream_simple(Model(api="fake-api"), Context(), SimpleStreamOptions())
    events = [ev async for ev in it]
    assert any(ev.type == "start" for ev in events)
    assert any(ev.type == "done" for ev in events)


async def test_unregister_providers_by_source_removes_matching_only() -> None:
    """Only entries with the given source_id are removed."""

    register_provider_object(
        _FakeProvider(api="api-a"), source_id="aelix-ai.builtin"
    )
    register_provider_object(
        _FakeProvider(api="api-b"), source_id="aelix-ai.builtin"
    )
    register_provider_object(
        _FakeProvider(api="api-c"), source_id="third-party"
    )

    unregister_providers_by_source("aelix-ai.builtin")
    remaining = get_registered_providers()
    assert "api-a" not in remaining
    assert "api-b" not in remaining
    assert "api-c" in remaining


async def test_register_provider_back_compat_bare_callable() -> None:
    """The Phase 1.4 bare-callable path keeps working."""

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent()

    register_provider("legacy-api", fn)
    assert "legacy-api" in get_registered_providers()
    # Provider object is the bare-fn shim.
    entry = get_registered_providers()["legacy-api"]
    assert entry.api == "legacy-api"


async def test_unregister_providers_by_source_skips_bare_callables() -> None:
    """Bare callables registered without a source_id are not removed."""

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent()

    register_provider("legacy-api", fn)
    register_provider_object(
        _FakeProvider(api="builtin-api"), source_id="aelix-ai.builtin"
    )

    unregister_providers_by_source("aelix-ai.builtin")
    remaining = get_registered_providers()
    assert "legacy-api" in remaining
    assert "builtin-api" not in remaining


async def test_register_provider_object_overwrites_same_api() -> None:
    """Re-registering an api overwrites the previous entry (Pi parity)."""

    p1 = _FakeProvider(api="x")
    p2 = _FakeProvider(api="x")
    register_provider_object(p1)
    register_provider_object(p2)
    entry = get_registered_providers()["x"]
    assert entry is p2


async def test_resolve_unknown_api_raises() -> None:
    """Resolving an unregistered api still raises StreamSimpleError."""

    with pytest.raises(StreamSimpleError) as ei:
        await stream_simple(Model(api="missing"), Context(), SimpleStreamOptions())
    assert ei.value.code == "no_provider_registered"
