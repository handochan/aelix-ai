"""Sprint 3c G.13 — P-7 verdict: tool error does NOT cancel siblings.

Pi parity: ``Promise.all`` reject path is unreachable because every tool
exception is caught per-tool (``agent-loop.ts:609-637, 651-680``). Pi NEVER
cancels siblings. Aelix uses ``asyncio.gather(*coros,
return_exceptions=False)`` (NOT ``TaskGroup`` — TaskGroup's mandatory
sibling-cancel would be Pi-divergence).

Proof: while tool A is raising, tools B and C are still in-flight and MUST
complete normally. We assert by counting per-tool "completed" markers.
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


async def test_raising_tool_does_not_cancel_siblings() -> None:
    completed = {"t0": False, "t1": False, "t2": False}
    error_gate = asyncio.Event()

    async def ok(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        # Wait until the raising tool has had a chance to raise — if siblings
        # were cancelled, ``completed`` would never flip True for this id.
        await error_gate.wait()
        completed[ctx.tool_call_id] = True
        return ToolResult(content=[TextContent(text=ctx.tool_call_id)])

    async def raises(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        # Let the gather schedule the other coros first, then explode.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        error_gate.set()
        raise RuntimeError("pi-says-siblings-survive")

    tools = [
        AgentTool(name="ok_0", execute=ok),
        AgentTool(name="raises_1", execute=raises),
        AgentTool(name="ok_2", execute=ok),
    ]
    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t0", tool_name="ok_0", input={}),
            ToolCallContent(tool_call_id="t1", tool_name="raises_1", input={}),
            ToolCallContent(tool_call_id="t2", tool_name="ok_2", input={}),
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

    # P-7 verdict: siblings survive the raising tool.
    assert completed["t0"] is True, "sibling t0 must not have been cancelled"
    assert completed["t2"] is True, "sibling t2 must not have been cancelled"

    tr = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert len(tr) == 3
    assert tr[1].is_error is True
