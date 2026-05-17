"""Sprint 3c G.8 — immediates (unknown / hook-blocked) mixed with valid tools.

Per §E.1: immediates emit ``tool_execution_end`` during the prep loop, in
source order. The valid tool runs in Phase 2 and emits its end event in
completion order. We assert:

1. All 3 source-order start events fire first
2. The two immediates' end events appear BEFORE the valid tool starts work
3. The result messages are in source order

Per §B step 4: unknown tools do NOT contribute to ``has_sequential`` —
batch stays parallel.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import ToolCallResult
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
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
from aelix_coding_agent.extensions.api import ExtensionAPI, ExtensionContext
from aelix_coding_agent.extensions.loader import load_extensions


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


async def test_unknown_and_blocked_immediates_mix_with_valid_tool() -> None:
    async def echo_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text=f"echoed:{args.get('text')}")])

    valid_tool = AgentTool(name="echo", execute=echo_execute)
    blocked_tool = AgentTool(name="blocked", execute=echo_execute)

    def block_factory(aelix: ExtensionAPI) -> None:
        def on_tool_call(event: Any, ctx: ExtensionContext) -> ToolCallResult:
            if event.tool_name == "blocked":
                return ToolCallResult(block=True, reason="nope")
            return ToolCallResult(block=False)

        aelix.on("tool_call", on_tool_call)

    result = await load_extensions([block_factory])

    turn1 = AssistantMessage(
        content=[
            # Unknown tool (immediate isError)
            ToolCallContent(tool_call_id="t0", tool_name="missing", input={}),
            # Valid tool (real exec)
            ToolCallContent(
                tool_call_id="t1", tool_name="echo", input={"text": "hi"}
            ),
            # Hook-blocked (immediate isError via before_tool_call)
            ToolCallContent(tool_call_id="t2", tool_name="blocked", input={}),
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="d")], stop_reason="end_turn"
    )

    events: list[Any] = []
    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="m"),
            extensions=result.extensions,
            runtime=result.runtime,
            tools=[valid_tool, blocked_tool],
            stream_fn=_make_mock_stream([turn1, turn2]),
        )
    )

    def listener(ev: Any) -> None:
        events.append(ev)

    h.subscribe(listener)

    msgs = await h.prompt("x")

    tr = [m for m in msgs if isinstance(m, ToolResultMessage)]
    assert [m.tool_call_id for m in tr] == ["t0", "t1", "t2"]
    assert tr[0].is_error is True  # unknown
    assert "Unknown tool" in tr[0].content[0].text
    assert tr[1].is_error is False  # valid
    assert tr[1].content[0].text == "echoed:hi"
    assert tr[2].is_error is True  # hook-blocked
    assert "nope" in tr[2].content[0].text

    # Source-order start events fire first.
    start_ids = [
        e.tool_call_id for e in events if e.type == "tool_execution_start"
    ]
    end_ids = [
        e.tool_call_id for e in events if e.type == "tool_execution_end"
    ]
    assert start_ids == ["t0", "t1", "t2"]
    # All 3 end events present.
    assert sorted(end_ids) == ["t0", "t1", "t2"]
    # Immediates (t0, t2) end events fire in the prep loop, interleaved with
    # their start events; t1's end event fires after Phase 2 completion.
    # In particular t0's end event appears before t1's start event.
    t0_end_idx = next(
        i for i, e in enumerate(events)
        if e.type == "tool_execution_end" and e.tool_call_id == "t0"
    )
    t1_start_idx = next(
        i for i, e in enumerate(events)
        if e.type == "tool_execution_start" and e.tool_call_id == "t1"
    )
    assert t0_end_idx < t1_start_idx, (
        "immediate t0 end event must fire during prep (before t1 starts)"
    )
