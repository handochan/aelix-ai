"""Sprint 3c G.5 — D.1.5 args-reference contract under parallelism.

§F.2 invariant: per tool_call_id the args dict is the SAME REFERENCE across
``tool_call`` hook, ``tool.execute`` and ``after_tool_call``. Across
different tool_call_ids, args dicts are DISJOINT — mutating one MUST NOT
affect another, even when they run concurrently.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
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


async def test_per_tool_args_mutations_are_isolated() -> None:
    """Mutate args["text"] in the tool_call hook for each call independently."""

    seen_in_execute: dict[str, dict[str, Any]] = {}

    async def echo_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        seen_in_execute[ctx.tool_call_id] = dict(args)
        return ToolResult(content=[TextContent(text=str(args.get("text", "")))])

    echo = AgentTool(name="echo", execute=echo_execute)

    def mutator_factory(aelix: ExtensionAPI) -> None:
        def on_tool_call(event: Any, ctx: ExtensionContext) -> None:
            # Mutation should affect only THIS tool_call_id's args dict.
            event.args["text"] = f"patched:{event.tool_call_id}"

        aelix.on("tool_call", on_tool_call)

    result = await load_extensions([mutator_factory])

    turn1 = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="t0", tool_name="echo", input={"text": "orig0"}
            ),
            ToolCallContent(
                tool_call_id="t1", tool_name="echo", input={"text": "orig1"}
            ),
            ToolCallContent(
                tool_call_id="t2", tool_name="echo", input={"text": "orig2"}
            ),
        ],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="done")], stop_reason="end_turn"
    )

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="m"),
            extensions=result.extensions,
            runtime=result.runtime,
            tools=[echo],
            stream_fn=_make_mock_stream([turn1, turn2]),
        )
    )

    msgs = await h.prompt("go")

    # Per-tool isolation: each id sees only its own patch.
    assert seen_in_execute["t0"]["text"] == "patched:t0"
    assert seen_in_execute["t1"]["text"] == "patched:t1"
    assert seen_in_execute["t2"]["text"] == "patched:t2"

    tr = [m for m in msgs if isinstance(m, ToolResultMessage)]
    # Source order preserved.
    assert tr[0].content[0].text == "patched:t0"
    assert tr[1].content[0].text == "patched:t1"
    assert tr[2].content[0].text == "patched:t2"
