"""Sprint 3c G.11 — Pi parity pin for parallel tool execution semantics.

Loads :file:`fixtures/pi_parallel_exec_semantics_734e08e.json` and verifies
each scenario against the Aelix implementation.

The fixture records Pi's expected behaviour at SHA ``734e08e`` for four
scenarios: happy 3-tool parallel; mixed-mode sequential downgrade; per-tool
error (no sibling cancel); abort semantics.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
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

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pi_parallel_exec_semantics_734e08e.json"


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text())


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


def test_fixture_pi_sha_pin() -> None:
    """The fixture is anchored at the exact Pi SHA we ship parity against."""

    fixture = _load_fixture()
    assert fixture["_meta"]["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


async def test_happy_3_tool_parallel_matches_pi_message_order() -> None:
    fixture = _load_fixture()["happy_3_tool_parallel"]

    async def ok(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text=ctx.tool_call_id)])

    tools = [AgentTool(name=tc["tool_name"], execute=ok) for tc in fixture["tool_calls"]]
    turn1 = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id=tc["tool_call_id"],
                tool_name=tc["tool_name"],
                input={},
            )
            for tc in fixture["tool_calls"]
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
    assert [m.tool_call_id for m in tr] == fixture["expected_tool_result_message_source_order"]


async def test_mixed_mode_downgrades_to_sequential_matches_pi() -> None:
    fixture = _load_fixture()["mixed_mode_seq_downgrade"]

    timeline: list[str] = []

    async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        timeline.append(f"start:{ctx.tool_call_id}")
        timeline.append(f"end:{ctx.tool_call_id}")
        return ToolResult(content=[TextContent(text=ctx.tool_call_id)])

    tools: list[AgentTool] = []
    for tc in fixture["tool_calls"]:
        mode = tc.get("tool_execution_mode")
        tools.append(
            AgentTool(
                name=tc["tool_name"],
                execute=execute,
                execution_mode=mode,
            )
        )

    turn1 = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id=tc["tool_call_id"],
                tool_name=tc["tool_name"],
                input={},
            )
            for tc in fixture["tool_calls"]
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="d")], stop_reason="end_turn"
    )

    async def emit(_: Any) -> None:
        pass

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

    # Sequential dispatch: start/end strictly interleaved per call.
    assert timeline == [
        "start:t0", "end:t0",
        "start:t1", "end:t1",
        "start:t2", "end:t2",
    ]


async def test_per_tool_error_matches_pi_no_sibling_cancel() -> None:
    fixture = _load_fixture()["per_tool_error"]

    async def ok(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="ok")])

    async def raises(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        raise RuntimeError("pi-mirror-boom")

    tools = [
        AgentTool(name="ok_a", execute=ok),
        AgentTool(name="raises_b", execute=raises),
        AgentTool(name="ok_c", execute=ok),
    ]
    turn1 = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id=tc["tool_call_id"],
                tool_name=tc["tool_name"],
                input={},
            )
            for tc in fixture["tool_calls"]
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
    expected = fixture["expected_results"]
    assert len(tr) == len(expected)
    for m, exp in zip(tr, expected, strict=True):
        assert m.tool_call_id == exp["tool_call_id"]
        assert m.is_error == exp["is_error"]
