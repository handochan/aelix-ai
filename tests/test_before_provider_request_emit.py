"""Sprint 6a (Phase 4.1, §D) — ``before_provider_request`` emit-site tests.

The harness's production-path ``_make_stream_fn`` emits this event
before delegating to ``stream_simple``. Handlers may patch the request's
``stream_options`` via the deep-merge reducer (P-41).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    BeforeProviderRequestHookEvent,
    BeforeProviderRequestResult,
)
from aelix_ai import (
    AssistantDoneEvent,
    AssistantMessage,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    clear_providers,
    register_provider,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    clear_providers()
    yield
    clear_providers()


def _make_dummy_provider() -> Any:
    """Register a dummy provider so ``stream_simple`` resolves cleanly."""

    captured: dict[str, Any] = {}

    async def fake(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        captured["options"] = options
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(stop_reason="stop"),
        )

    register_provider("anthropic-messages", fake)
    return captured


async def test_before_provider_request_emits_on_production_path() -> None:
    """The harness emits ``before_provider_request`` exactly once per turn."""

    captured = _make_dummy_provider()
    events: list[BeforeProviderRequestHookEvent] = []

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages", id="claude-3", provider="anthropic"),
            get_api_key_and_headers=lambda _m: {"apiKey": "test-key"},
        )
    )

    def handler(event: BeforeProviderRequestHookEvent, _ctx: Any) -> None:
        events.append(event)
        return None

    h.hooks.on("before_provider_request", handler)
    await h.prompt("hello")

    assert len(events) == 1
    assert events[0].model is not None
    assert events[0].model.api == "anthropic-messages"
    assert captured["options"].api_key == "test-key"


async def test_before_provider_request_chains_patch_with_deep_merge() -> None:
    """Handler patch flows through ``_apply_stream_options_patch``."""

    captured = _make_dummy_provider()

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def patcher(event: BeforeProviderRequestHookEvent, _ctx: Any) -> BeforeProviderRequestResult:
        return BeforeProviderRequestResult(
            stream_options={"transport": "sse", "headers": {"x-custom": "1"}}
        )

    h.hooks.on("before_provider_request", patcher)
    await h.prompt("hi")
    opts = captured["options"]
    assert opts.transport == "sse"
    assert opts.headers.get("x-custom") == "1"


async def test_before_provider_request_carries_session_id() -> None:
    """The emit carries the turn-state session id."""

    _make_dummy_provider()
    seen: list[str] = []

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def handler(event: BeforeProviderRequestHookEvent, _ctx: Any) -> None:
        seen.append(event.session_id)
        return None

    h.hooks.on("before_provider_request", handler)
    await h.prompt("hi")
    assert seen == [""] or seen[0] is not None


async def test_before_provider_request_uses_state_stream_options_baseline() -> None:
    """``state.stream_options`` flows into the snapshot baseline."""

    _make_dummy_provider()
    seen: list[dict] = []

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            stream_options={"transport": "auto"},
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def handler(event: BeforeProviderRequestHookEvent, _ctx: Any) -> None:
        seen.append(dict(event.stream_options))
        return None

    h.hooks.on("before_provider_request", handler)
    await h.prompt("hi")
    # Baseline carries the state-level transport option.
    assert seen[0].get("transport") == "auto"


async def test_before_provider_request_not_emitted_when_stream_fn_injected() -> None:
    """Explicit ``stream_fn`` injection bypasses ``_make_stream_fn``."""

    events: list[Any] = []

    async def mock_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(stop_reason="stop"),
        )

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            stream_fn=mock_stream,
        )
    )

    def handler(event: BeforeProviderRequestHookEvent, _ctx: Any) -> None:
        events.append(event)
        return None

    h.hooks.on("before_provider_request", handler)
    await h.prompt("hi")
    # No emit — the test-injection path preserves Sprint 1-5 behavior.
    assert events == []
