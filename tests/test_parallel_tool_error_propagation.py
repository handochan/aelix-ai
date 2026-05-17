"""Sprint 3c G.4 — one tool raises → isError; siblings complete.

Pi parity (`agent-loop.ts:609-637, 651-680`): per-tool ``try/except`` makes
``Promise.all`` reject path unreachable. Aelix uses
``asyncio.gather(*coros, return_exceptions=False)``; siblings MUST complete.
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


async def test_one_tool_raises_siblings_still_complete() -> None:
    """Tool 1 raises; tools 0 and 2 produce normal results."""

    async def ok_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text=f"ok:{args.get('i')}")])

    async def raising_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        raise RuntimeError("boom")

    tools = [
        AgentTool(name="ok_0", execute=ok_execute),
        AgentTool(name="raise_1", execute=raising_execute),
        AgentTool(name="ok_2", execute=ok_execute),
    ]
    turn1 = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="t0", tool_name="ok_0", input={"i": 0}),
            ToolCallContent(tool_call_id="t1", tool_name="raise_1", input={}),
            ToolCallContent(tool_call_id="t2", tool_name="ok_2", input={"i": 2}),
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="final")], stop_reason="end_turn"
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
    assert len(tr) == 3
    assert tr[0].is_error is False
    assert tr[0].content[0].text == "ok:0"
    assert tr[1].is_error is True
    assert "boom" in tr[1].content[0].text
    assert tr[2].is_error is False
    assert tr[2].content[0].text == "ok:2"
