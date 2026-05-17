"""Sprint 3c G.1 — parallel tool execution proof.

Proves true concurrency: 3 parallel tools observed at peak in_flight == 3
simultaneously.

Also verifies §A.4 default flip: omitting ``tool_execution=`` from
:class:`AgentLoopConfig` selects parallel.
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
        if i >= len(turn_finals):
            raise AssertionError(f"stream_fn ran out at idx={i}")
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=turn_finals[i])

    return fn


def _basic_config(**overrides: Any) -> AgentLoopConfig:
    return AgentLoopConfig(
        model=Model(id="mock", provider="mock"),
        convert_to_llm=default_convert_to_llm,
        **overrides,
    )


async def test_three_parallel_tools_execute_concurrently() -> None:
    """Peak ``in_flight_count`` MUST reach 3 — proves true concurrency."""

    in_flight = {"now": 0, "peak": 0}
    gate = asyncio.Event()

    async def slow_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        in_flight["now"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        try:
            await gate.wait()
            return ToolResult(content=[TextContent(text=f"done:{args.get('i')}")])
        finally:
            in_flight["now"] -= 1

    tools = [
        AgentTool(name=f"tool_{i}", execute=slow_execute) for i in range(3)
    ]

    turn1 = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id=f"t{i}", tool_name=f"tool_{i}", input={"i": i}
            )
            for i in range(3)
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="done")], stop_reason="end_turn"
    )

    async def loop_emit(_: Any) -> None:
        # Set the gate eagerly after the start events have fired and all
        # three coros have had a chance to enter ``slow_execute``.
        pass

    # Start the gate after a tiny delay so all 3 coros enter their try block first.
    async def releaser() -> None:
        for _ in range(10):
            await asyncio.sleep(0)
            if in_flight["now"] >= 3:
                break
        gate.set()

    release_task = asyncio.create_task(releaser())

    new_messages = await agent_loop(
        [UserMessage(content=[TextContent(text="hi")])],
        AgentContext(tools=tools),
        _basic_config(),
        emit=loop_emit,
        stream_fn=_make_mock_stream([turn1, turn2]),
    )
    await release_task

    assert in_flight["peak"] == 3, (
        f"expected peak in_flight=3 (proof of parallelism); got {in_flight['peak']}"
    )
    tr = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert len(tr) == 3
    # Source order must be preserved.
    for i, msg in enumerate(tr):
        assert msg.tool_call_id == f"t{i}"


async def test_default_tool_execution_is_parallel() -> None:
    """§A.4 regression guard: no explicit ``tool_execution=`` → parallel path."""

    cfg = _basic_config()
    assert cfg.tool_execution == "parallel"
