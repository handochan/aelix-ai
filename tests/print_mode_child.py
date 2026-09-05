"""THE repo's only real ``--mode json`` print child, shared by its two consumers.

It was `tests/cli/test_print_mode_json_contract.py`'s private ``_CHILD`` until
#220 needed a second reader. Two tests now drive the SAME interpreter script:

* ``tests/cli/test_print_mode_json_contract.py`` — ADR-0198 §D7's mandatory wire
  contract, asserting the event sequence and the snake_case field names on bytes
  that crossed a real pipe;
* ``tests/agents_ext/test_print_channel_spawn.py::
  test_a_real_print_child_reports_the_signal_exit_code`` — #220's guard for the
  signal exit path, which needs a child that really installs
  ``print_mode``'s handlers and really parks in a turn.

ONE FILE ON PURPOSE. The two tests measure opposite ends of the same process:
the first that the child's stdout is the contract, the second that the child's
EXIT CODE is a signal number. A private copy in each would let ``print_mode``'s
startup drift under one of them while the other stayed green, and the wire
contract is exactly the gate ADR-0198 §D1 says a pi-port silently breaks.

THE DEFAULT PATH IS BYTE-FOR-BYTE WHAT THE CONTRACT TEST ALWAYS RAN. The #220
extension is two argv flags and one branch taken only under ``--wedge``; with
neither flag present, not one event on the wire changes. That is the whole
reason the extension is parameterised rather than forked.

``--wedge`` makes the model turn never finish, which is the state #220 needs and
the contract test must never see:

* the stream loops ``await asyncio.sleep(0.05)`` rather than sleeping once for a
  long time, and the step size is the #202 measurement, not taste: on Windows a
  ``CTRL_BREAK_EVENT`` does not wake a sleeping thread (only the SIGINT event
  does), so the Python-level ``SIGBREAK`` handler runs when the loop next gets
  control. A single long sleep would push that past any grace a test can afford;
* ``--ready <path>`` writes a breadcrumb from INSIDE the stream, which is the
  only place that attests to the whole precondition at once — ``run_print_mode``
  installs its signal handlers before it prompts, so a stream that has begun is
  a child whose handler is armed and whose turn is in flight. It replaces
  "assume a fresh interpreter got there in 300 ms", the assumption that produced
  a real flake in this suite before (S13).
"""

from __future__ import annotations

import textwrap

CHILD_SOURCE = textwrap.dedent(
    '''
    """Driven as a REAL child process by tests/print_mode_child.py's consumers."""

    import asyncio
    import os
    import sys
    from collections.abc import AsyncIterator

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.runtime import AgentSessionRuntime
    from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
    from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
    from aelix_ai.streaming import (
        AssistantEndEvent,
        AssistantStartEvent,
        Model,
    )
    from aelix_ai.tools import Tool, ToolResult
    from aelix_coding_agent.modes.print_mode import run_print_mode

    WITH_TOOL = "--with-tool" in sys.argv
    ERRORED = "--errored" in sys.argv
    # #220. Absent for the wire-contract consumer, so every line below the two
    # branches they guard runs exactly as it did before this file was shared.
    WEDGED = "--wedge" in sys.argv
    READY = (
        sys.argv[sys.argv.index("--ready") + 1] if "--ready" in sys.argv else None
    )

    _turns = {"n": 0}


    def _stream():
        async def fn(model, context, options) -> AsyncIterator:
            _turns["n"] += 1
            yield AssistantStartEvent(partial=AssistantMessage(content=[]))
            if WEDGED:
                # Announced from HERE and nowhere earlier: reaching this line
                # means run_print_mode already installed its signal handlers
                # (it does that before it prompts) and the turn is really in
                # flight. O_CREAT + close, so the parent's exists() check can
                # never see a half-written file.
                if READY is not None:
                    os.close(os.open(READY, os.O_WRONLY | os.O_CREAT, 0o600))
                # 0.05 s steps, not one long sleep: on Windows a CTRL_BREAK does
                # not wake a sleeping thread, so the SIGBREAK handler runs only
                # when the loop gets control (#202). Plain asyncio.sleep, so the
                # CancelledError dispose() raises here propagates — a stream that
                # SWALLOWED it would never return and would never report the
                # exit code at all (measured shape; that is what the reaper's
                # escalation is for).
                while True:
                    await asyncio.sleep(0.05)
            if ERRORED:
                yield AssistantEndEvent(
                    message=AssistantMessage(
                        content=[],
                        stop_reason="error",
                        error_message="the provider said no",
                        usage=None,
                        provider="stub",
                        model="stub-1",
                    )
                )
                return
            if WITH_TOOL and _turns["n"] == 1:
                yield AssistantEndEvent(
                    message=AssistantMessage(
                        content=[
                            ToolCallContent(
                                tool_call_id="tc-1",
                                tool_name="probe",
                                input={"path": "README.md"},
                            )
                        ],
                        stop_reason="tool_use",
                        usage={"input": 11, "output": 3},
                        provider="stub",
                        model="stub-1",
                    )
                )
                return
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[TextContent(text="the complete answer")],
                    stop_reason="end_turn",
                    usage={"input": 20, "output": 5, "total_tokens": 25},
                    provider="stub",
                    model="stub-1",
                )
            )

        return fn


    async def _probe(args, ctx):
        return ToolResult(content=[TextContent(text="probe ran")])


    async def main() -> None:
        harness = AgentHarness(
            AgentHarnessOptions(
                model=Model(id="stub-1", provider="stub"),
                stream_fn=_stream(),
                tools=[
                    Tool(
                        name="probe",
                        description="A probe.",
                        parameters={"type": "object"},
                        execute=_probe,
                    )
                ],
            )
        )

        async def _noop(_s):
            return harness

        runtime = AgentSessionRuntime(
            harness,
            _noop,
            repo=JsonlSessionRepo(fs=LocalFileSystem()),
            fs=LocalFileSystem(),
        )
        code = await run_print_mode(
            runtime, mode="json", messages=[], initial_message="Task: go"
        )
        sys.exit(code)


    asyncio.run(main())
    '''
)
"""The child script, as text. Written out and executed as a separate
interpreter, so what a consumer asserts is what a parent would actually read off
fd 1 — and, under ``--wedge``, what a parent would actually see as an exit
status after a signal."""


__all__ = ["CHILD_SOURCE"]
