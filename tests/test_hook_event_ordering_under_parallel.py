"""Sprint 3c G.6 — §E ordering matrix verified with controllable latency.

Expected per §E:

- ``tool_execution_start`` events: SOURCE order, before any execution begins
- ``tool_execution_end`` events: COMPLETION order
- tool-result messages: SOURCE order

Tool 0 is slow, tools 1 and 2 are fast. Completion order should be 1, 2, 0
but source order for start events is 0, 1, 2 and for tool-result messages
also 0, 1, 2.
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


async def test_start_source_order_end_completion_order_msg_source_order() -> None:
    fast_done = asyncio.Event()
    slow_release = asyncio.Event()

    async def slow_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        # Wait for both fast tools to finish before completing.
        await fast_done.wait()
        await slow_release.wait()
        return ToolResult(content=[TextContent(text="slow")])

    fast_count = {"n": 0}

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

    new_messages = await agent_loop(
        [UserMessage(content=[TextContent(text="x")])],
        AgentContext(tools=tools),
        AgentLoopConfig(
            model=Model(id="m", provider="m"),
            convert_to_llm=default_convert_to_llm,
        ),
        emit=emit,
        stream_fn=_make_mock_stream([turn1, turn2]),
    )

    start_ids = [
        e.tool_call_id for e in events if e.type == "tool_execution_start"
    ]
    end_ids = [
        e.tool_call_id for e in events if e.type == "tool_execution_end"
    ]

    # §E row "tool_execution_start": SOURCE order.
    assert start_ids == ["t0", "t1", "t2"], f"start order wrong: {start_ids}"
    # §E row "tool_execution_end": COMPLETION order — slow (t0) finishes last.
    assert end_ids[-1] == "t0", f"slow tool must end last; got end order: {end_ids}"
    assert set(end_ids[:2]) == {"t1", "t2"}, (
        f"fast tools must end before slow; got: {end_ids}"
    )

    # §E row "message_start/message_end (tool-result msg)": SOURCE order.
    tr = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert [m.tool_call_id for m in tr] == ["t0", "t1", "t2"]
