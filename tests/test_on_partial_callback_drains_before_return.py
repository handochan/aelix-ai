"""Sprint 3d / Phase 2.1.4 §E.4 — ``on_partial`` drains before tool return.

Pi parity: ``executePreparedToolCall`` (``agent-loop.ts:604-639``) collects
every emit task into a local ``updateEvents`` list and awaits
``Promise.all(updateEvents)`` BEFORE returning the final result. Aelix
mirrors that with ``asyncio.gather(*update_events)`` inside
``_execute_and_finalize``.

This test wires a slow ``emit`` and proves that all 5 emissions are observed
before the ``tool_execution_end`` for the tool fires (which itself happens
before the ``after_tool_call`` / ``tool_result`` hook bridges would run in
the parallel + sequential paths).
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


async def test_on_partial_drains_before_tool_execution_end() -> None:
    completed_updates: list[int] = []

    async def slow_emit(ev: Any) -> None:
        # Async emit handlers: each scheduled task yields control then
        # records ordering. The drain MUST complete before the
        # tool_execution_end event surfaces.
        if ev.type == "tool_execution_update":
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            completed_updates.append(ev.partial_result.details)

    events: list[Any] = []

    async def emit(ev: Any) -> None:
        await slow_emit(ev)
        events.append(ev)

    async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        assert ctx.on_partial is not None
        for i in range(5):
            ctx.on_partial(ToolResult(details=i))
        return ToolResult(content=[TextContent(text="done")])

    tools = [AgentTool(name="drainer", execute=execute)]
    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t1", tool_name="drainer", input={})
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="d")], stop_reason="end_turn"
    )

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

    # All 5 partials must have completed (drained) before any
    # tool_execution_end event for t1 was appended.
    end_idx = next(
        i
        for i, e in enumerate(events)
        if e.type == "tool_execution_end" and e.tool_call_id == "t1"
    )
    update_indices = [
        i for i, e in enumerate(events) if e.type == "tool_execution_update"
    ]
    assert len(update_indices) == 5, (
        f"expected 5 update events appended; got {len(update_indices)}"
    )
    assert max(update_indices) < end_idx, (
        "all partial emits must drain before tool_execution_end"
    )
    assert completed_updates == [0, 1, 2, 3, 4], (
        f"partial fan-out must complete in order; got {completed_updates}"
    )
