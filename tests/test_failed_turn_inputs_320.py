"""Failed turns retain committed inputs; unstarted turns restore pending inputs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.harness.hooks import BeforeAgentStartResult, MessageEndEventResult
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_agent_core.types import AgentEvent, AgentMessage, AgentTool
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.tools import ToolExecutionContext, ToolResult


def _texts(messages: list[Any]) -> list[str]:
    return [
        block.text
        for message in messages
        for block in message.content
        if isinstance(block, TextContent)
    ]


async def _harness(tmp_path: Path, stream: Any, attached: bool) -> tuple[AgentHarness, Path | None]:
    path = tmp_path / "session.jsonl" if attached else None
    session = None
    if path is not None:
        storage = await JsonlSessionStorage.create(
            LocalFileSystem(), str(path), cwd=str(tmp_path), session_id="issue-320"
        )
        session = Session(storage)
    harness = AgentHarness(AgentHarnessOptions(stream_fn=stream, session=session))
    harness.state.auto_retry_enabled = False
    harness.state.auto_compaction_enabled = False
    return harness, path


async def _disk(path: Path) -> list[AgentMessage]:
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))
    return list((await reopened.build_context()).messages)


@pytest.mark.parametrize("attached", [False, True])
async def test_a_thrown_provider_preserves_inputs_once_and_the_next_request_uses_them(
    tmp_path: Path, attached: bool
) -> None:
    seen: list[list[str]] = []

    async def stream(
        _model: Model, context: Context, _options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(_texts(context.messages))
        if len(seen) == 1:
            raise RuntimeError("provider blew up")
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="recovered")]))

    harness, path = await _harness(tmp_path, stream, attached)
    events: list[AgentEvent] = []
    harness.subscribe(lambda event: events.append(event))
    await harness.next_turn("queued")
    with pytest.raises(RuntimeError, match="provider blew up"):
        await harness.prompt("live")

    expected = ["queued", "live", "[error] provider blew up"]
    assert seen == [["queued", "live"]]
    assert _texts(harness.state.messages) == expected
    assert harness._next_turn_queue == []
    end = next(event for event in events if event.type == "agent_end")
    assert end.messages == harness.state.messages
    if path is not None:
        assert _texts(await _disk(path)) == expected

    await harness.prompt("next")
    assert seen[-1] == [*expected, "next"]
    assert _texts(harness.state.messages) == [*expected, "next", "recovered"]
    if path is not None:
        assert _texts(await _disk(path)) == _texts(harness.state.messages)


@pytest.mark.parametrize("attached", [False, True])
async def test_message_end_replacements_commit_once_on_success_and_failure(
    tmp_path: Path, attached: bool
) -> None:
    calls = 0

    async def stream(
        _model: Model, _context: Context, _options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="answer")]))
        else:
            raise RuntimeError("second failed")

    harness, path = await _harness(tmp_path, stream, attached)

    def rewrite(event: Any, _ctx: Any) -> MessageEndEventResult:
        message = event.message
        return MessageEndEventResult(
            message=replace(message, content=[TextContent(text="reduced " + _texts([message])[0])])
        )

    harness.hooks.on("message_end", rewrite)
    result = await harness.prompt("live")
    assert _texts(result) == ["reduced live", "reduced answer"]
    assert _texts(harness.state.messages) == _texts(result)
    with pytest.raises(RuntimeError, match="second failed"):
        await harness.prompt("other")
    assert _texts(harness.state.messages) == [
        "reduced live",
        "reduced answer",
        "reduced other",
        "reduced [error] second failed",
    ]
    if path is not None:
        assert _texts(await _disk(path)) == _texts(harness.state.messages)


@pytest.mark.parametrize("attached", [False, True])
async def test_completed_tool_turn_survives_a_later_provider_failure(
    tmp_path: Path, attached: bool
) -> None:
    calls = 0

    async def stream(
        _model: Model, _context: Context, _options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[
                        TextContent(text="partial answer"),
                        ToolCallContent(tool_call_id="call-1", tool_name="echo", input={}),
                    ],
                    stop_reason="tool_use",
                )
            )
        else:
            raise RuntimeError("later request failed")

    async def execute(_args: dict[str, Any], _ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="tool result")])

    harness, path = await _harness(tmp_path, stream, attached)
    harness.state.tools = [AgentTool(name="echo", execute=execute)]
    with pytest.raises(RuntimeError, match="later request failed"):
        await harness.prompt("live")
    assert _texts(harness.state.messages) == [
        "live",
        "partial answer",
        "tool result",
        "[error] later request failed",
    ]
    assert sum(isinstance(message, ToolResultMessage) for message in harness.state.messages) == 1
    if path is not None:
        assert _texts(await _disk(path)) == _texts(harness.state.messages)


async def test_adapter_reported_partial_error_is_retained_without_duplicate_input(
    tmp_path: Path,
) -> None:
    async def stream(
        _model: Model, _context: Context, _options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        message = AssistantMessage(
            content=[TextContent(text="partial model answer")],
            stop_reason="error",
            error_message="permission denied",
        )
        yield AssistantErrorEvent(
            reason="error", error=message, error_message=message.error_message
        )

    harness, path = await _harness(tmp_path, stream, True)
    result = await harness.prompt("live")
    assert _texts(result) == ["live", "partial model answer"]
    assert _texts(harness.state.messages) == _texts(result)
    assert path is not None
    assert _texts(await _disk(path)) == _texts(result)


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_context_build_restores_live_and_queued_inputs_before_late_arrivals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    seen: list[list[str]] = []

    async def stream(
        _model: Model, context: Context, _options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(_texts(context.messages))
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="answer")]))

    harness, path = await _harness(tmp_path, stream, True)
    session = harness.session
    assert session is not None
    original_build = session.build_context
    entered = asyncio.Event()
    release = asyncio.Event()

    async def broken_build() -> Any:
        entered.set()
        await release.wait()
        raise RuntimeError("storage unavailable")

    def inject(_event: Any, _ctx: Any) -> BeforeAgentStartResult:
        return BeforeAgentStartResult(
            messages=[UserMessage(content=[TextContent(text="generated")])]
        )

    harness.hooks.on("before_agent_start", inject)
    await harness.next_turn("queued")
    monkeypatch.setattr(session, "build_context", broken_build)
    task = asyncio.create_task(harness.prompt("live"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    await harness.next_turn("late")
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        release.set()
        with pytest.raises(RuntimeError, match="storage unavailable"):
            await task
    assert seen == []
    assert harness.state.messages == []
    assert _texts(harness._next_turn_queue) == ["queued", "live", "late"]
    await asyncio.wait_for(harness.wait_for_idle(), timeout=1)
    assert path is not None
    assert await _disk(path) == []

    monkeypatch.setattr(session, "build_context", original_build)
    await harness.prompt("next")
    assert seen == [["generated", "queued", "live", "late", "next"]]
    assert harness._next_turn_queue == []
    assert _texts(await _disk(path)) == [*seen[0], "answer"]


@pytest.mark.parametrize("abort", [False, True])
async def test_cancel_after_provider_receives_input_preserves_it_without_requeue(
    tmp_path: Path, abort: bool
) -> None:
    entered = asyncio.Event()

    async def stream(
        _model: Model, _context: Context, _options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        entered.set()
        await asyncio.Event().wait()
        yield AssistantEndEvent(message=AssistantMessage())

    harness, path = await _harness(tmp_path, stream, True)
    await harness.next_turn("queued")
    task = asyncio.create_task(harness.prompt("live"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    if abort:
        await harness.abort()
        assert await task == []
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert _texts(harness.state.messages) == ["queued", "live"]
    assert harness._next_turn_queue == []
    assert path is not None
    assert _texts(await _disk(path)) == ["queued", "live"]


async def test_model_refusal_keeps_inputs_unwritten_and_existing_queue_intact(
    tmp_path: Path,
) -> None:
    async def stream(
        _model: Model, _context: Context, _options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        raise AssertionError("refused model must not stream")
        yield AssistantEndEvent(message=AssistantMessage())

    harness, path = await _harness(tmp_path, stream, True)
    harness.set_prompt_check(lambda _model: "model cannot run")
    await harness.next_turn("queued")
    with pytest.raises(AgentHarnessError, match="model cannot run"):
        await harness.prompt("live")
    assert harness.state.messages == []
    assert _texts(harness._next_turn_queue) == ["queued"]
    assert path is not None
    assert await _disk(path) == []


@pytest.mark.parametrize("seam", ["agent_start", "message_start", "message_end"])
async def test_cancel_before_input_commit_restores_only_uncommitted_messages(
    tmp_path: Path, seam: str
) -> None:
    seen: list[list[str]] = []

    async def stream(
        _model: Model, context: Context, _options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(_texts(context.messages))
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="answer")]))

    harness, path = await _harness(tmp_path, stream, True)
    entered = asyncio.Event()
    parked_once = False

    async def park(event: Any, _ctx: Any) -> None:
        nonlocal parked_once
        is_live = seam == "agent_start" or _texts([event.message]) == ["live"]
        if is_live and not parked_once:
            parked_once = True
            entered.set()
            await asyncio.Event().wait()

    harness.hooks.on(seam, park)
    await harness.next_turn("queued")
    task = asyncio.create_task(harness.prompt("live"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    await harness.next_turn("late")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    committed = [] if seam == "agent_start" else ["queued"]
    restored = ["queued", "live", "late"] if seam == "agent_start" else ["live", "late"]
    assert seen == []
    assert _texts(harness.state.messages) == committed
    assert _texts(harness._next_turn_queue) == restored
    assert path is not None
    assert _texts(await _disk(path)) == committed
    await harness.prompt("next")
    assert seen == [["queued", "live", "late", "next"]]
    assert _texts(harness.state.messages) == [*seen[0], "answer"]
    assert _texts(await _disk(path)) == _texts(harness.state.messages)


async def test_context_failure_during_retry_does_not_requeue_committed_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def stream(
        _model: Model, _context: Context, _options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        nonlocal calls
        calls += 1
        message = AssistantMessage(stop_reason="error", error_message="502 Bad Gateway")
        yield AssistantErrorEvent(
            reason="error", error=message, error_message=message.error_message
        )

    harness, _path = await _harness(tmp_path, stream, True)
    harness.set_auto_retry_enabled(True)
    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1)
    session = harness.session
    assert session is not None
    original = session.build_context
    reads = 0

    async def build() -> Any:
        nonlocal reads
        reads += 1
        if reads == 2:
            raise RuntimeError("retry storage unavailable")
        return await original()

    monkeypatch.setattr(session, "build_context", build)
    await harness.next_turn("queued")
    with pytest.raises(RuntimeError, match="retry storage unavailable"):
        await harness.prompt("live")
    assert calls == 1
    assert _texts(harness.state.messages) == ["queued", "live"]
    assert harness._next_turn_queue == []
