"""Demo entry point for the Aelix runtime.

Run with ``uv run aelix`` (or ``python -m aelix``). The demo wires the
``echo`` example tool into an :class:`Agent` with a mock ``stream_fn`` that
plays a two-turn script:

1. The model "decides" to call the ``echo`` tool with a fixed argument.
2. After receiving the tool result, the model returns a final text response
   and stops with ``stop_reason="end_turn"``.

No LLM provider or API key is required. Phase 2 will swap the mock for real
providers under :mod:`aelix.ai.providers`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from aelix.agent import Agent, AgentEvent, AgentOptions, AgentState
from aelix.ai.messages import AssistantMessage, TextContent, ToolCallContent
from aelix.ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix.examples.echo import echo_tool


def _make_mock_stream_fn() -> Any:
    """Return a stateful mock ``stream_fn`` that plays the two-turn demo."""

    turn_index = {"value": 0}

    async def mock_stream_fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        idx = turn_index["value"]
        turn_index["value"] += 1
        partial = AssistantMessage(content=[])
        yield AssistantStartEvent(partial=partial)
        if idx == 0:
            final = AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id="call_1",
                        tool_name="echo",
                        input={"text": "Aelix runtime is online."},
                    )
                ],
                stop_reason="tool_use",
            )
        else:
            final = AssistantMessage(
                content=[
                    TextContent(text="Echoed: Aelix runtime is online.")
                ],
                stop_reason="end_turn",
            )
        yield AssistantEndEvent(message=final)

    return mock_stream_fn


def _print_listener(event: AgentEvent) -> None:
    if event.type == "message_end":
        msg = event.message
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextContent):
                    print(f"[assistant] {block.text}")
                elif isinstance(block, ToolCallContent):
                    print(f"[tool call ] {block.tool_name}({block.input})")
    elif event.type == "tool_execution_end":
        for block in event.result.content:
            if isinstance(block, TextContent):
                print(f"[tool ret  ] {block.text}")


async def _run() -> None:
    state = AgentState(
        system_prompt="You are an echo bot.",
        model=Model(id="mock-echo", provider="mock"),
        tools=[echo_tool],
    )
    agent = Agent(
        AgentOptions(
            initial_state=state,
            stream_fn=_make_mock_stream_fn(),
        )
    )
    agent.subscribe(_print_listener)

    await agent.prompt("Echo this: Aelix runtime is online.")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
