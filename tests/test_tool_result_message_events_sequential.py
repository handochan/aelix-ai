"""Sprint 3d / Phase 2.1.4 §E.2 — sequential path emits message events.

Pi parity ``agent-loop.ts:434-438``: per tool the order is
``tool_execution_end`` → ``message_start(toolresult)`` →
``message_end(toolresult)``. The previous Aelix sequential path appended the
message before the end event and never emitted ``message_start`` /
``message_end`` for tool-result messages at all — this test guards the
fixed order.
"""

from __future__ import annotations

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


async def test_sequential_emits_tool_result_message_events_after_end() -> None:
    async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text=f"out:{ctx.tool_call_id}")])

    tools = [
        AgentTool(name="t_a", execute=execute, execution_mode="sequential"),
        AgentTool(name="t_b", execute=execute, execution_mode="sequential"),
    ]

    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="tc0", tool_name="t_a", input={}),
            ToolCallContent(tool_call_id="tc1", tool_name="t_b", input={}),
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

    # Filter to events that pertain to the tool-result messages (which are
    # ToolResultMessage instances, distinct from the AssistantMessage on
    # turn 2 and the UserMessage on turn 0).
    def _tc_id(e: Any) -> str | None:
        msg = getattr(e, "message", None)
        return getattr(msg, "tool_call_id", None) if isinstance(msg, ToolResultMessage) else None

    for tc_id in ("tc0", "tc1"):
        end_idx = next(
            i
            for i, e in enumerate(events)
            if e.type == "tool_execution_end" and e.tool_call_id == tc_id
        )
        start_idx = next(
            i
            for i, e in enumerate(events)
            if e.type == "message_start" and _tc_id(e) == tc_id
        )
        end_msg_idx = next(
            i
            for i, e in enumerate(events)
            if e.type == "message_end" and _tc_id(e) == tc_id
        )
        # Per-tool order: end → message_start → message_end (Pi parity
        # ``agent-loop.ts:434-438``).
        assert end_idx < start_idx < end_msg_idx, (
            f"tc={tc_id}: end={end_idx}, start={start_idx}, msg_end={end_msg_idx}"
        )

    # Source-order also between tools.
    tc0_start = next(
        i
        for i, e in enumerate(events)
        if e.type == "message_start" and _tc_id(e) == "tc0"
    )
    tc1_start = next(
        i
        for i, e in enumerate(events)
        if e.type == "message_start" and _tc_id(e) == "tc1"
    )
    assert tc0_start < tc1_start, "sequential path must preserve source order"
