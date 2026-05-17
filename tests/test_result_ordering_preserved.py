"""Sprint 3c G.7 — tool-result message order = source order regardless of completion.

Slow at index 0, fast at 1 + 2. ``ToolResultMessage`` list MUST be in
[t0, t1, t2] order even though completion order is t1, t2, t0.
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


async def test_result_messages_in_source_order_even_when_slow_finishes_last() -> None:
    slow_release = asyncio.Event()
    fast_done = {"n": 0}

    async def slow_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        await slow_release.wait()
        return ToolResult(content=[TextContent(text="slow:0")])

    async def fast_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        fast_done["n"] += 1
        if fast_done["n"] >= 2:
            slow_release.set()
        return ToolResult(content=[TextContent(text=f"fast:{args.get('i')}")])

    tools = [
        AgentTool(name="slow", execute=slow_execute),
        AgentTool(name="fast_1", execute=fast_execute),
        AgentTool(name="fast_2", execute=fast_execute),
    ]
    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t0", tool_name="slow", input={}),
            ToolCallContent(tool_call_id="t1", tool_name="fast_1", input={"i": 1}),
            ToolCallContent(tool_call_id="t2", tool_name="fast_2", input={"i": 2}),
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="d")], stop_reason="end_turn"
    )

    async def emit(_: Any) -> None:
        pass

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

    tr = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert [m.tool_call_id for m in tr] == ["t0", "t1", "t2"]
    assert tr[0].content[0].text == "slow:0"
    assert tr[1].content[0].text == "fast:1"
    assert tr[2].content[0].text == "fast:2"
