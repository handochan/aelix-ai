"""Sprint 3c G.3 — abort() cancels the in-flight gather task.

Aelix additive over Pi (§C.2): when a turn task is in-flight, ``abort()``
calls ``task.cancel()``. This unwinds any tool that ignored the cooperative
signal. Pi does NOT cancel ``Promise.all`` on tool error; Aelix's behaviour
is a strict superset.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
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


async def test_abort_during_parallel_tool_call_cancels_turn_task() -> None:
    cancelled = {"hit": 0}
    in_flight = asyncio.Event()

    async def hang_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        in_flight.set()
        try:
            # Ignore the cooperative signal — abort() must still unwind us
            # via task.cancel().
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["hit"] += 1
            raise
        return ToolResult(content=[TextContent(text="never")])

    tool_a = AgentTool(name="hang_a", execute=hang_execute)
    tool_b = AgentTool(name="hang_b", execute=hang_execute)

    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t0", tool_name="hang_a", input={}),
            ToolCallContent(tool_call_id="t1", tool_name="hang_b", input={}),
        ],
        stop_reason="tool_use",
    )

    async def streamer(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        # Only one turn ever runs to completion; the second call (if any) is
        # never expected.
        yield AssistantEndEvent(message=turn1)

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            tools=[tool_a, tool_b],
            stream_fn=streamer,
        )
    )

    task = asyncio.create_task(h.prompt("go"))
    # Wait until at least one tool is in-flight.
    await in_flight.wait()
    # Give the second tool a chance to start too.
    for _ in range(5):
        await asyncio.sleep(0)
    await h.abort()
    await task

    # At least one of the two parallel tools must have received CancelledError.
    assert cancelled["hit"] >= 1, (
        f"expected abort to cancel in-flight tool(s); got cancelled={cancelled}"
    )
    assert h.phase == "idle"
