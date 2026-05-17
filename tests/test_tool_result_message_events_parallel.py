"""Sprint 3d / Phase 2.1.4 §E.3 — parallel path emits message events in source order.

Three tools with inverted latency (``t0`` slow, ``t2`` fast) execute under
the parallel dispatch. ``tool_execution_end`` fires in completion order
(``t2`` before ``t0``) but ``message_start`` for the tool-result messages
fires in source order (``t0`` before ``t1`` before ``t2``). Pi parity
``agent-loop.ts:495-499`` (source-order Phase 3 emit).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core import (
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    agent_loop,
    default_convert_to_llm,
)
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.tools import ToolExecutionContext, ToolResult


def _make_mock_stream(turn_finals: list[AssistantMessage]) -> Any:
    idx = {"i": 0}

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        i = idx["i"]
        idx["i"] += 1
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=turn_finals[i])

    return fn


async def test_parallel_message_events_in_source_order_despite_completion_order() -> None:
    fast_done = asyncio.Event()
    slow_release = asyncio.Event()
    fast_count = {"n": 0}

    async def slow_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        await fast_done.wait()
        await slow_release.wait()
        return ToolResult(content=[TextContent(text="slow")])

    async def fast_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        fast_count["n"] += 1
        if fast_count["n"] >= 2:
            fast_done.set()
            slow_release.set()
        return ToolResult(content=[TextContent(text=f"fast:{args.get('i')}")])

    tools = [
        AgentTool(name="slow_0", execute=slow_execute),
        AgentTool(name="fast_1", execute=fast_execute),
        AgentTool(name="fast_2", execute=fast_execute),
    ]
    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t0", tool_name="slow_0", input={}),
            ToolCallContent(tool_call_id="t1", tool_name="fast_1", input={"i": 1}),
            ToolCallContent(tool_call_id="t2", tool_name="fast_2", input={"i": 2}),
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="d")], stop_reason="end_turn"
    )

    events: list[Any] = []

    async def emit(ev: Any) -> None:
        events.append(ev)

    await agent_loop(
        [UserMessage(content=[TextContent(text="x")])],
        AgentContext(tools=tools),
        AgentLoopConfig(
            model=Model(id="m", provider="m"),
            convert_to_llm=default_convert_to_llm,
        ),
        emit=emit,
        stream_fn=_make_mock_stream([turn1, turn2]),
    )

    end_ids = [
        e.tool_call_id for e in events if e.type == "tool_execution_end"
    ]
    # Completion order: slow t0 finishes last; t1/t2 fast finish before.
    assert end_ids[-1] == "t0", f"slow must end last; got {end_ids}"
    assert set(end_ids[:2]) == {"t1", "t2"}

    # Source order for tool-result message events.
    def _tc_id(e: Any) -> str | None:
        msg = getattr(e, "message", None)
        return getattr(msg, "tool_call_id", None) if isinstance(msg, ToolResultMessage) else None

    start_ids = [
        _tc_id(e)
        for e in events
        if e.type == "message_start" and _tc_id(e) is not None
    ]
    end_msg_ids = [
        _tc_id(e)
        for e in events
        if e.type == "message_end" and _tc_id(e) is not None
    ]
    assert start_ids == ["t0", "t1", "t2"], (
        f"message_start for tool-results must be source-order; got {start_ids}"
    )
    assert end_msg_ids == ["t0", "t1", "t2"], (
        f"message_end for tool-results must be source-order; got {end_msg_ids}"
    )

    # Cross-check: tool_execution_end for t2 precedes message_start for t0
    # (completion order on end events; source order on message events).
    t2_end_idx = next(
        i
        for i, e in enumerate(events)
        if e.type == "tool_execution_end" and e.tool_call_id == "t2"
    )
    t0_msg_start_idx = next(
        i
        for i, e in enumerate(events)
        if e.type == "message_start" and _tc_id(e) == "t0"
    )
    assert t2_end_idx < t0_msg_start_idx, (
        "Phase 3 source-order emit must happen after Phase 2 gather completes"
    )
