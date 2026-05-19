"""Demo entry point for the Aelix runtime.

Run with ``uv run aelix`` (or ``python -m aelix``). Two modes:

- ``--mode interactive`` (default) — wires the ``echo`` example tool into an
  :class:`Agent` with a mock ``stream_fn`` that plays a two-turn script.
- ``--mode rpc`` — Sprint 6d (Phase 4.4) headless JSONL protocol — spawns an
  :class:`AgentHarness` and routes stdin/stdout through
  :func:`aelix_coding_agent.rpc.run_rpc_mode` for non-Python client embedding.

No LLM provider or API key is required for the interactive demo. Phase 2
swapped the mock for real providers under :mod:`aelix_ai.providers`; the
RPC mode reuses the same mock so the wire surface is end-to-end testable
without external credentials.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core import Agent, AgentEvent, AgentOptions, AgentState
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.examples.echo.echo import echo_tool
from aelix_coding_agent.rpc import run_rpc_mode


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


# Backwards-compatible alias for ``_run`` — the legacy demo entry point.
# Sprint 6d (Phase 4.4) split the interactive demo into ``_run_interactive``
# alongside the new ``_run_rpc`` entry; existing regressions reference the
# original name so we preserve it as an alias.


async def _run_interactive() -> None:
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


async def _run_rpc() -> None:
    """Sprint 6d (Phase 4.4) — headless JSONL protocol entry.

    Builds a minimal :class:`AgentHarness` and hands stdin/stdout to
    :func:`run_rpc_mode`. The harness has the same mock ``stream_fn`` as
    the interactive demo so the RPC wire surface is testable end-to-end
    without external credentials. RpcClient drives everything through the
    9 supported commands (prompt, abort, new_session, get_state,
    get_messages, compact, bash, set_thinking_level, set_session_name).
    """

    options = AgentHarnessOptions(
        model=Model(id="mock-echo", provider="mock"),
        stream_fn=_make_mock_stream_fn(),
    )
    harness = AgentHarness(options)
    await run_rpc_mode(harness)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="aelix")
    parser.add_argument(
        "--mode",
        choices=["interactive", "rpc"],
        default="interactive",
        help=(
            "Run as a local demo (default) or in RPC mode (Phase 4.4 — "
            "JSONL stdin/stdout protocol for non-Python embedding)."
        ),
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Optional provider identifier (forwarded by RpcClient).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model id (forwarded by RpcClient).",
    )
    # ``parse_known_args`` so RpcClient's extra ``args`` pass-through
    # doesn't break the CLI when future flags are added downstream.
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.mode == "rpc":
        asyncio.run(_run_rpc())
        return
    asyncio.run(_run_interactive())


# Legacy alias preserved for tests that pre-date the Phase 4.4 split
# (``tests/test_agent_regression.py::test_existing_demo_runs_clean``).
_run = _run_interactive


if __name__ == "__main__":
    main()
