"""Pi #5573 — late ``on_partial`` callbacks are ignored once the call settles.

Pi fix (``agent-loop.ts:634, 645, 656, 660``): ``executePreparedToolCall``
tracks an ``acceptingUpdates`` flag. The instant ``tool.execute`` resolves (or
raises) the flag is flipped, and the partial callback short-circuits with
``if (!acceptingUpdates) return;``. A tool that fires its ``on_partial``
callback AFTER it has returned — e.g. from leftover async work it spawned —
must therefore NOT schedule a stale :class:`ToolExecutionUpdateEvent`.

Aelix mirrors this in ``_execute_and_finalize`` with a ``settled`` flag. These
tests prove:

1. In-flight partials emitted BEFORE the tool returns still surface as
   ``tool_execution_update`` events (normal progress unbroken).
2. A late call to the captured ``on_partial`` callback — made after
   ``agent_loop`` has fully drained and settled the tool call — emits NO
   additional ``tool_execution_update`` event.
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


def _make_turns(tool_name: str) -> tuple[AssistantMessage, AssistantMessage]:
    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t1", tool_name=tool_name, input={})
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="d")], stop_reason="end_turn"
    )
    return turn1, turn2


async def test_late_on_partial_after_settle_emits_no_update_event() -> None:
    # The tool captures its ``on_partial`` callback, fires two IN-FLIGHT
    # partials, then returns. We invoke the captured callback AGAIN after
    # ``agent_loop`` has settled the call to simulate leftover async work.
    holder: dict[str, Any] = {}

    async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        assert ctx.on_partial is not None
        holder["cb"] = ctx.on_partial
        ctx.on_partial(ToolResult(details="inflight-1"))
        ctx.on_partial(ToolResult(details="inflight-2"))
        return ToolResult(content=[TextContent(text="done")])

    tools = [AgentTool(name="leftover", execute=execute)]
    turn1, turn2 = _make_turns("leftover")

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

    # In-flight progress before settle still emits.
    updates_before = [e for e in events if e.type == "tool_execution_update"]
    assert len(updates_before) == 2, (
        f"in-flight partials must still emit; got {len(updates_before)}"
    )
    assert [u.partial_result.details for u in updates_before] == [
        "inflight-1",
        "inflight-2",
    ]

    # Leftover async work fires the still-referenced callback AFTER the call
    # settled — this must be a silent no-op (Pi #5573).
    assert "cb" in holder
    holder["cb"](ToolResult(details="late-stale"))
    # Give any (erroneously) scheduled task a chance to run; there must be none.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    updates_after = [e for e in events if e.type == "tool_execution_update"]
    assert len(updates_after) == 2, (
        "late on_partial after settle must not emit a stale update event; "
        f"got {len(updates_after)} ({[u.partial_result.details for u in updates_after]})"
    )


async def test_late_on_partial_after_tool_error_emits_no_update_event() -> None:
    # Same guard applies on the error path: a tool that raises but later fires
    # its captured callback must not emit a stale update event.
    holder: dict[str, Any] = {}

    async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        assert ctx.on_partial is not None
        holder["cb"] = ctx.on_partial
        ctx.on_partial(ToolResult(details="inflight-err"))
        raise RuntimeError("boom")

    tools = [AgentTool(name="boomer", execute=execute)]
    turn1, turn2 = _make_turns("boomer")

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

    updates_before = [e for e in events if e.type == "tool_execution_update"]
    assert len(updates_before) == 1, (
        f"in-flight partial before raise must emit; got {len(updates_before)}"
    )

    holder["cb"](ToolResult(details="late-after-error"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    updates_after = [e for e in events if e.type == "tool_execution_update"]
    assert len(updates_after) == 1, (
        "late on_partial after error-settle must not emit a stale update event"
    )
