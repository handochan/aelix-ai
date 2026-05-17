"""Sprint 3c G.9 — batch terminates iff EVERY result has terminate=True.

Pi parity: ``agent-loop.ts:533-535`` — ``terminate=True`` aggregation is AND
across all tool results in the batch.
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


async def test_all_terminate_stops_loop() -> None:
    """All 2 tools return terminate=True → loop ends after batch."""

    async def stop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="s")], terminate=True)

    tools = [
        AgentTool(name="s_a", execute=stop_execute),
        AgentTool(name="s_b", execute=stop_execute),
    ]
    only_turn = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t0", tool_name="s_a", input={}),
            ToolCallContent(tool_call_id="t1", tool_name="s_b", input={}),
        ],
        stop_reason="tool_use",
    )

    events: list[Any] = []

    async def emit(ev: Any) -> None:
        events.append(ev)

    # Only ONE turn allowed; the mock raises if the loop tries another.
    await agent_loop(
        [UserMessage(content=[TextContent(text="x")])],
        AgentContext(tools=tools),
        AgentLoopConfig(
            model=Model(id="m", provider="m"),
            convert_to_llm=default_convert_to_llm,
        ),
        emit=emit,
        stream_fn=_make_mock_stream([only_turn]),
    )
    assert events[-1].type == "agent_end"


async def test_mixed_terminate_continues_loop() -> None:
    """1 tool with terminate=True + 1 without → loop continues."""

    async def stop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="s")], terminate=True)

    async def go_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="g")], terminate=False)

    tools = [
        AgentTool(name="s_a", execute=stop_execute),
        AgentTool(name="g_b", execute=go_execute),
    ]
    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t0", tool_name="s_a", input={}),
            ToolCallContent(tool_call_id="t1", tool_name="g_b", input={}),
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="continuing")], stop_reason="end_turn"
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
    # The loop must have taken a second turn because not all results terminated.
    end_events = [e for e in events if e.type == "agent_end"]
    assert len(end_events) == 1
    # And we got a final text message from turn 2 (proves second turn ran).
    turn_ends = [e for e in events if e.type == "turn_end"]
    assert len(turn_ends) == 2
