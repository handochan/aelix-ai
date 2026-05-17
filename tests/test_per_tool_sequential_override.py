"""Sprint 3c G.2 — per-tool ``execution_mode="sequential"`` override.

Mix one sequential tool with two parallel tools in the same batch. Per
§B / Pi ``agent-loop.ts:380-387``, a single sequential tool downgrades the
WHOLE batch to sequential. We prove this by recording per-tool start times:
under sequential dispatch each tool's start time is strictly greater than
the previous tool's end time; under parallel they would overlap.
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


async def test_one_sequential_tool_downgrades_batch_to_sequential() -> None:
    """1 sequential + 2 parallel tools → batch runs sequentially."""

    timeline: list[tuple[str, str]] = []  # (event, tool_name)

    def make_tool(name: str, *, sequential: bool = False) -> AgentTool:
        async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
            timeline.append(("start", name))
            # Yield to the loop to give other coros a chance to enter if parallel.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            timeline.append(("end", name))
            return ToolResult(content=[TextContent(text=name)])

        return AgentTool(
            name=name,
            execute=execute,
            execution_mode="sequential" if sequential else None,
        )

    tools = [
        make_tool("seq_a", sequential=True),
        make_tool("par_b"),
        make_tool("par_c"),
    ]

    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t0", tool_name="seq_a", input={}),
            ToolCallContent(tool_call_id="t1", tool_name="par_b", input={}),
            ToolCallContent(tool_call_id="t2", tool_name="par_c", input={}),
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="done")], stop_reason="end_turn"
    )

    async def emit(_: Any) -> None:
        pass

    new_messages = await agent_loop(
        [UserMessage(content=[TextContent(text="go")])],
        AgentContext(tools=tools),
        AgentLoopConfig(
            model=Model(id="m", provider="m"),
            convert_to_llm=default_convert_to_llm,
        ),
        emit=emit,
        stream_fn=_make_mock_stream([turn1, turn2]),
    )

    # Per-tool start times strictly increasing — sequential semantic.
    # Expected: start a, end a, start b, end b, start c, end c.
    expected = [
        ("start", "seq_a"), ("end", "seq_a"),
        ("start", "par_b"), ("end", "par_b"),
        ("start", "par_c"), ("end", "par_c"),
    ]
    assert timeline == expected, f"expected sequential interleave; got {timeline}"
    tr = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert len(tr) == 3
