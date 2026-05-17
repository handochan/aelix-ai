"""Tests for the low-level agent loop (``aelix.agent.loop``).

The loop is driven by a mock ``stream_fn`` that yields ``AssistantStartEvent``
followed by ``AssistantEndEvent`` for each call, with a final
:class:`AssistantMessage` pulled from a precomputed list. The mock raises if
the loop calls it more times than the script expects, which lets us assert
loop-termination invariants like ``terminate`` / ``should_stop_after_turn``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix.agent import (
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    BeforeToolCallResult,
    agent_loop,
    default_convert_to_llm,
)
from aelix.ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix.ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix.ai.tools import ToolExecutionContext, ToolResult


def _make_mock_stream(
    turn_finals: list[AssistantMessage],
) -> Any:
    """Return a stream_fn that yields one start+end per turn from ``turn_finals``."""

    idx = {"i": 0}

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        i = idx["i"]
        idx["i"] += 1
        if i >= len(turn_finals):
            raise AssertionError(
                f"mock stream_fn ran out of turns at idx={i} "
                f"(script length={len(turn_finals)}); loop took an extra turn"
            )
        final = turn_finals[i]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=final)

    return fn


def _emit_collector() -> tuple[list, Any]:
    events: list = []

    async def emit(event: Any) -> None:
        events.append(event)

    return events, emit


def _basic_config() -> AgentLoopConfig:
    return AgentLoopConfig(
        model=Model(id="mock", provider="mock"),
        convert_to_llm=default_convert_to_llm,
    )


# === Tests ===


async def test_single_turn_no_tools() -> None:
    final = AssistantMessage(
        content=[TextContent(text="hi")],
        stop_reason="end_turn",
    )
    events, emit = _emit_collector()
    stream_fn = _make_mock_stream([final])

    user = UserMessage(content=[TextContent(text="hello")])
    new_messages = await agent_loop(
        [user],
        AgentContext(),
        _basic_config(),
        emit=emit,
        stream_fn=stream_fn,
    )

    types_seen = [e.type for e in events]
    assert types_seen[0] == "agent_start"
    assert types_seen[-1] == "agent_end"
    assert "turn_end" in types_seen

    assert len(new_messages) == 2
    assert new_messages[0] is user
    assert isinstance(new_messages[1], AssistantMessage)
    assert new_messages[1].content[0].text == "hi"


async def test_tool_call_execution() -> None:
    """LLM calls echo tool → tool result → final assistant text."""

    async def echo_execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        return ToolResult(
            content=[TextContent(text=f"echoed: {args['text']}")]
        )

    echo = AgentTool(name="echo", execute=echo_execute)

    turn1 = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="t1",
                tool_name="echo",
                input={"text": "ping"},
            )
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="done")],
        stop_reason="end_turn",
    )
    events, emit = _emit_collector()
    stream_fn = _make_mock_stream([turn1, turn2])

    new_messages = await agent_loop(
        [UserMessage(content=[TextContent(text="echo ping")])],
        AgentContext(tools=[echo]),
        _basic_config(),
        emit=emit,
        stream_fn=stream_fn,
    )

    # Expected sequence: user, assistant(tool_call), toolResult, assistant(text)
    assert len(new_messages) == 4
    assert isinstance(new_messages[2], ToolResultMessage)
    assert new_messages[2].content[0].text == "echoed: ping"

    tool_end_events = [e for e in events if e.type == "tool_execution_end"]
    assert len(tool_end_events) == 1
    assert not tool_end_events[0].result.is_error


async def test_terminate_hint_stops_loop() -> None:
    """A tool returning ``terminate=True`` ends the loop after the batch."""

    async def stop_execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        return ToolResult(
            content=[TextContent(text="stopping")],
            terminate=True,
        )

    stopper = AgentTool(name="stop", execute=stop_execute)

    only_turn = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t1", tool_name="stop", input={})
        ],
        stop_reason="tool_use",
    )
    events, emit = _emit_collector()
    # Only ONE turn is allowed; if loop tries another, mock raises.
    stream_fn = _make_mock_stream([only_turn])

    new_messages = await agent_loop(
        [UserMessage(content=[TextContent(text="stop please")])],
        AgentContext(tools=[stopper]),
        _basic_config(),
        emit=emit,
        stream_fn=stream_fn,
    )

    # user + assistant(tool_call) + tool_result
    assert len(new_messages) == 3
    assert events[-1].type == "agent_end"


async def test_unknown_tool_returns_error_result() -> None:
    only_turn = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t1", tool_name="missing", input={})
        ],
        stop_reason="tool_use",
    )
    final_turn = AssistantMessage(
        content=[TextContent(text="ok")],
        stop_reason="end_turn",
    )
    events, emit = _emit_collector()
    stream_fn = _make_mock_stream([only_turn, final_turn])

    new_messages = await agent_loop(
        [UserMessage(content=[TextContent(text="try")])],
        AgentContext(),  # no tools registered
        _basic_config(),
        emit=emit,
        stream_fn=stream_fn,
    )

    tr_messages = [
        m for m in new_messages if isinstance(m, ToolResultMessage)
    ]
    assert len(tr_messages) == 1
    assert tr_messages[0].is_error
    assert "Unknown tool" in tr_messages[0].content[0].text


async def test_before_tool_call_block() -> None:
    """``before_tool_call`` returning ``block=True`` prevents execution."""

    executed = {"count": 0}

    async def run_execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        executed["count"] += 1
        return ToolResult(content=[TextContent(text="never")])

    run = AgentTool(name="run", execute=run_execute)

    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t1", tool_name="run", input={})
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="ok")],
        stop_reason="end_turn",
    )
    events, emit = _emit_collector()
    stream_fn = _make_mock_stream([turn1, turn2])

    async def block_all(_ctx: Any) -> BeforeToolCallResult:
        return BeforeToolCallResult(block=True, reason="not allowed")

    config = AgentLoopConfig(
        model=Model(),
        convert_to_llm=default_convert_to_llm,
        before_tool_call=block_all,
    )

    new_messages = await agent_loop(
        [UserMessage(content=[TextContent(text="run")])],
        AgentContext(tools=[run]),
        config,
        emit=emit,
        stream_fn=stream_fn,
    )

    assert executed["count"] == 0
    tool_results = [
        m for m in new_messages if isinstance(m, ToolResultMessage)
    ]
    assert len(tool_results) == 1
    assert tool_results[0].is_error
    assert "not allowed" in tool_results[0].content[0].text


async def test_should_stop_after_turn() -> None:
    final = AssistantMessage(
        content=[TextContent(text="ok")],
        stop_reason="end_turn",
    )
    events, emit = _emit_collector()
    # Allow up to two turns in the script; should_stop_after_turn should cut us off after the first.
    stream_fn = _make_mock_stream([final, final])

    calls = {"n": 0}

    async def stop_after_first(_ctx: Any) -> bool:
        calls["n"] += 1
        return True

    config = AgentLoopConfig(
        model=Model(),
        convert_to_llm=default_convert_to_llm,
        should_stop_after_turn=stop_after_first,
    )

    await agent_loop(
        [UserMessage(content=[TextContent(text="hi")])],
        AgentContext(),
        config,
        emit=emit,
        stream_fn=stream_fn,
    )
    assert calls["n"] == 1
    assert events[-1].type == "agent_end"


async def test_tool_execution_end_event_carries_tool_name_and_is_error() -> None:
    """ToolExecutionEndEvent must carry tool_name and is_error (P-2)."""

    async def echo_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="echoed")])

    echo = AgentTool(name="echo", execute=echo_execute)

    turn1 = AssistantMessage(
        content=[ToolCallContent(tool_call_id="t1", tool_name="echo", input={"text": "hi"})],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="done")],
        stop_reason="end_turn",
    )
    events, emit = _emit_collector()
    stream_fn = _make_mock_stream([turn1, turn2])

    await agent_loop(
        [UserMessage(content=[TextContent(text="echo hi")])],
        AgentContext(tools=[echo]),
        _basic_config(),
        emit=emit,
        stream_fn=stream_fn,
    )

    end_events = [e for e in events if e.type == "tool_execution_end"]
    assert len(end_events) == 1
    evt = end_events[0]
    assert evt.tool_name == "echo"
    assert evt.is_error is False


async def test_tool_result_message_carries_timestamp() -> None:
    """ToolResultMessage must always have a non-None timestamp after execution (P-18)."""

    async def noop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="done")])

    noop = AgentTool(name="noop", execute=noop_execute)

    turn1 = AssistantMessage(
        content=[ToolCallContent(tool_call_id="t1", tool_name="noop", input={})],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="finished")],
        stop_reason="end_turn",
    )
    events, emit = _emit_collector()
    stream_fn = _make_mock_stream([turn1, turn2])

    new_messages = await agent_loop(
        [UserMessage(content=[TextContent(text="run noop")])],
        AgentContext(tools=[noop]),
        _basic_config(),
        emit=emit,
        stream_fn=stream_fn,
    )

    tool_results = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    assert tool_results[0].timestamp is not None, "ToolResultMessage.timestamp must not be None"

    # Also verify the assistant message got a timestamp stamped by the loop.
    assistant_messages = [m for m in new_messages if isinstance(m, AssistantMessage)]
    for am in assistant_messages:
        assert am.timestamp is not None, f"AssistantMessage.timestamp must not be None, got {am}"
