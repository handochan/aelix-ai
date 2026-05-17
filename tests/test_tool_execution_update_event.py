"""Sprint 3d / Phase 2.1.4 §E.1 — ``tool_execution_update`` fires per partial.

A tool calls ``ctx.on_partial(p1)``, ``on_partial(p2)``, ``on_partial(p3)``
then returns. Assert:

1. Exactly 3 :class:`ToolExecutionUpdateEvent` were emitted in order with
   correct payloads.
2. All 3 update events are observed BEFORE the
   :class:`ToolExecutionEndEvent` for the same tool — Pi-parity drain
   semantics (``agent-loop.ts:630`` ``await Promise.all(updateEvents)``).
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


async def test_three_partials_emitted_in_order_before_tool_execution_end() -> None:
    partials = [
        ToolResult(content=[TextContent(text="p1")]),
        ToolResult(content=[TextContent(text="p2")]),
        ToolResult(content=[TextContent(text="p3")]),
    ]

    async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        assert ctx.on_partial is not None, (
            "harness must register on_partial callback"
        )
        for p in partials:
            ctx.on_partial(p)
        return ToolResult(content=[TextContent(text="final")])

    tools = [AgentTool(name="partial_tool", execute=execute)]
    turn1 = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="t1",
                tool_name="partial_tool",
                input={"k": "v"},
            )
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

    updates = [e for e in events if e.type == "tool_execution_update"]
    assert len(updates) == 3, f"expected 3 partials; got {len(updates)}"
    for i, ev in enumerate(updates):
        assert ev.tool_call_id == "t1"
        assert ev.tool_name == "partial_tool"
        assert ev.args == {"k": "v"}
        assert ev.partial_result.content[0].text == f"p{i + 1}"

    # All updates MUST precede the end event for the same tool (Pi parity
    # drain semantics at ``agent-loop.ts:630``).
    end_idx = next(
        i
        for i, e in enumerate(events)
        if e.type == "tool_execution_end" and e.tool_call_id == "t1"
    )
    update_indices = [
        i for i, e in enumerate(events) if e.type == "tool_execution_update"
    ]
    assert all(ui < end_idx for ui in update_indices), (
        f"updates must precede end; update_indices={update_indices}, end={end_idx}"
    )
