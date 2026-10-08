"""#197 — recovery closes at the assistant response, before the remaining tool work."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import MessageEndEventResult
from aelix_agent_core.types import AgentTool, AutoRetryEndEvent, AutoRetryStartEvent
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
from aelix_ai.oauth.auth_storage import OAuthRefreshError
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.tools import ToolExecutionContext, ToolResult


def _error(text: str = "rate limit exceeded", *, reason: str = "error") -> AssistantMessage:
    return AssistantMessage(content=[], stop_reason=reason, error_message=text)


def _tool_response(call_id: str) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCallContent(tool_call_id=call_id, tool_name="work", input={})],
        stop_reason="tool_use",
    )


def _harness(
    messages: list[AssistantMessage], *, tools: list[AgentTool] | None = None
) -> AgentHarness:
    responses = iter(messages)

    async def stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        message = next(responses)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if message.stop_reason in ("error", "aborted"):
            yield AssistantErrorEvent(
                reason=message.stop_reason, error=message, error_message=message.error_message
            )
        else:
            yield AssistantEndEvent(message=message)

    harness = AgentHarness(AgentHarnessOptions(stream_fn=stream, tools=tools or []))
    harness._state.auto_compaction_enabled = False
    return harness


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1)


async def test_recovery_closes_before_a_tool_finishes_and_the_prompt_settles() -> None:
    tool_entered = asyncio.Event()
    release_tool = asyncio.Event()

    async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        tool_entered.set()
        await release_tool.wait()
        return ToolResult(content=[TextContent(text="done")])

    harness = _harness(
        [_error(), _tool_response("recovered"), AssistantMessage(stop_reason="stop")],
        tools=[AgentTool(name="work", execute=execute)],
    )
    events: list[Any] = []
    counters_at_end: list[int] = []

    def observe(event: Any) -> None:
        events.append(event)
        if isinstance(event, AutoRetryEndEvent):
            counters_at_end.append(harness._retry_attempt)

    harness.subscribe(observe)
    prompt = asyncio.create_task(harness.prompt("do work"))
    try:
        await asyncio.wait_for(tool_entered.wait(), timeout=30)
        assert harness.phase == "turn"
        assert not prompt.done(), "the user request must still be running"
        ends = [event for event in events if isinstance(event, AutoRetryEndEvent)]
        assert len(ends) == 1, "recovery must be observable while the tool is still running"
        assert (ends[0].success, ends[0].attempt) == (True, 1)
        assert harness._retry_attempt == 0
        assert counters_at_end == [0], "subscribers must see the reset counter"
        types = [event.type for event in events]
        assert types.index("auto_retry_end") < types.index("tool_execution_start")
        recovered_end = next(
            i
            for i, event in enumerate(events)
            if event.type == "message_end"
            and isinstance(event.message, AssistantMessage)
            and event.message.stop_reason == "tool_use"
        )
        assert recovered_end < types.index("auto_retry_end")
    finally:
        release_tool.set()
        await asyncio.wait_for(prompt, timeout=30)
        await harness.dispose()
    assert len([event for event in events if isinstance(event, AutoRetryEndEvent)]) == 1


async def test_a_later_llm_failure_gets_a_fresh_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_MAX_ATTEMPTS", 1)

    async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="done")])

    harness = _harness(
        [
            _error(),
            _tool_response("first-recovery"),
            _error(),
            _tool_response("second-recovery"),
            AssistantMessage(stop_reason="stop"),
        ],
        tools=[AgentTool(name="work", execute=execute)],
    )
    events: list[Any] = []
    harness.subscribe(events.append)
    try:
        result = await harness.prompt("do both pieces of work")
    finally:
        await harness.dispose()
    assert isinstance(result[-1], AssistantMessage)
    assert result[-1].stop_reason == "stop"
    retries = [
        event for event in events if isinstance(event, (AutoRetryStartEvent, AutoRetryEndEvent))
    ]
    assert [(event.type, event.attempt) for event in retries] == [
        ("auto_retry_start", 1),
        ("auto_retry_end", 1),
        ("auto_retry_start", 1),
        ("auto_retry_end", 1),
    ]
    assert all(event.success for event in retries if isinstance(event, AutoRetryEndEvent))


@pytest.mark.parametrize("reason", ["error", "aborted"])
async def test_a_failed_or_aborted_retry_is_never_reported_as_recovered(reason: str) -> None:
    harness = _harness([_error(), _error("permission denied", reason=reason)])
    events: list[Any] = []
    harness.subscribe(events.append)
    try:
        await harness.prompt("try once")
    finally:
        await harness.dispose()
    ends = [event for event in events if isinstance(event, AutoRetryEndEvent)]
    assert len(ends) == 1
    assert (ends[0].success, ends[0].attempt) == (False, 1)
    assert harness._retry_attempt == 0


async def test_recovery_uses_the_message_left_by_the_hook_reducer() -> None:
    harness = _harness([_error(), AssistantMessage(stop_reason="stop")])

    async def replace(event: Any, ctx: Any) -> MessageEndEventResult | None:
        if isinstance(event.message, AssistantMessage) and event.message.stop_reason == "stop":
            return MessageEndEventResult(
                message=dataclasses.replace(
                    event.message, stop_reason="error", error_message="permission denied"
                )
            )
        return None

    harness.hooks.on("message_end", replace)
    events: list[Any] = []
    harness.subscribe(events.append)
    try:
        await harness.prompt("try once")
    finally:
        await harness.dispose()
    ends = [event for event in events if isinstance(event, AutoRetryEndEvent)]
    assert len(ends) == 1
    assert ends[0].success is False


async def test_a_response_without_a_retry_has_no_recovery_event() -> None:
    harness = _harness([AssistantMessage(stop_reason="stop")])
    events: list[Any] = []
    harness.subscribe(events.append)
    try:
        await harness.prompt("ordinary request")
    finally:
        await harness.dispose()
    assert not any(isinstance(event, AutoRetryEndEvent) for event in events)


async def test_a_rebuilt_setup_failure_keeps_its_authoritative_retry_decision() -> None:
    calls = 0

    async def stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        nonlocal calls
        calls += 1
        if calls == 2:
            async for event in harness._setup_error_events(model, "transient setup", "HTTP 502"):
                yield event
            return
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if calls == 1:
            message = _error()
            yield AssistantErrorEvent(
                reason="error", error=message, error_message=message.error_message
            )
        else:
            yield AssistantEndEvent(message=AssistantMessage(stop_reason="stop"))

    harness = AgentHarness(AgentHarnessOptions(stream_fn=stream))
    harness._state.auto_compaction_enabled = False

    async def replace(event: Any, ctx: Any) -> MessageEndEventResult | None:
        if (
            isinstance(event.message, AssistantMessage)
            and event.message.error_message == "transient setup"
        ):
            return MessageEndEventResult(
                message=dataclasses.replace(event.message, stop_reason="stop", error_message=None)
            )
        return None

    harness.hooks.on("message_end", replace)
    events: list[Any] = []
    harness.subscribe(events.append)
    try:
        await harness.prompt("recover from provider and setup errors")
    finally:
        await harness.dispose()
    assert calls == 3
    retries = [
        event for event in events if isinstance(event, (AutoRetryStartEvent, AutoRetryEndEvent))
    ]
    assert [(event.type, event.attempt) for event in retries] == [
        ("auto_retry_start", 1),
        ("auto_retry_start", 2),
        ("auto_retry_end", 2),
    ]
    assert isinstance(retries[-1], AutoRetryEndEvent) and retries[-1].success


async def test_a_rebuilt_refused_setup_failure_closes_as_failure() -> None:
    calls = 0

    async def stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        nonlocal calls
        calls += 1
        if calls == 1:
            message = _error()
            yield AssistantStartEvent(partial=AssistantMessage(content=[]))
            yield AssistantErrorEvent(
                reason="error", error=message, error_message=message.error_message
            )
            return
        async for event in harness._setup_error_events(model, "refused refresh", None):
            yield event

    harness = AgentHarness(AgentHarnessOptions(stream_fn=stream))
    harness._state.auto_compaction_enabled = False

    async def replace(event: Any, ctx: Any) -> MessageEndEventResult | None:
        if (
            isinstance(event.message, AssistantMessage)
            and event.message.error_message == "refused refresh"
        ):
            return MessageEndEventResult(
                message=dataclasses.replace(event.message, stop_reason="stop", error_message=None)
            )
        return None

    harness.hooks.on("message_end", replace)
    events: list[Any] = []
    harness.subscribe(events.append)
    try:
        await harness.prompt("a hook cannot turn a refused refresh into recovery")
    finally:
        await harness.dispose()
    assert calls == 2
    assert [
        (event.success, event.attempt) for event in events if isinstance(event, AutoRetryEndEvent)
    ] == [(False, 1)]
    assert harness._retry_attempt == 0


async def test_a_rebuilt_oauth_callback_refusal_closes_as_failure() -> None:
    request = httpx.Request("POST", "https://oauth.invalid/token")
    transient = OAuthRefreshError(
        "retry-test",
        httpx.HTTPStatusError(
            "token endpoint 502", request=request, response=httpx.Response(502, request=request)
        ),
    )
    refused = OAuthRefreshError(
        "retry-test",
        httpx.HTTPStatusError(
            "invalid_grant", request=request, response=httpx.Response(401, request=request)
        ),
    )
    assert transient.retry_reason is not None
    assert refused.retry_reason is None
    failures = [transient, refused]
    calls: list[str] = []

    async def callback(model: Model) -> Any:
        calls.append(model.id)
        raise failures.pop(0)

    model = Model(
        id="retry-model",
        provider="retry-test",
        api="anthropic-messages",
        base_url="http://127.0.0.1:1",
    )
    harness = AgentHarness(AgentHarnessOptions(model=model, get_api_key_and_headers=callback))
    harness._state.auto_compaction_enabled = False

    async def replace(event: Any, ctx: Any) -> MessageEndEventResult | None:
        if isinstance(event.message, AssistantMessage) and "invalid_grant" in (
            event.message.error_message or ""
        ):
            return MessageEndEventResult(
                message=dataclasses.replace(event.message, stop_reason="stop", error_message=None)
            )
        return None

    harness.hooks.on("message_end", replace)
    events: list[Any] = []
    harness.subscribe(events.append)
    try:
        await harness.prompt("transient refresh then refused OAuth refresh")
    finally:
        await harness.dispose()
    assert calls == ["retry-model", "retry-model"]
    assert failures == []
    assert [
        (event.success, event.attempt) for event in events if isinstance(event, AutoRetryEndEvent)
    ] == [(False, 1)]
    assert harness._retry_attempt == 0
