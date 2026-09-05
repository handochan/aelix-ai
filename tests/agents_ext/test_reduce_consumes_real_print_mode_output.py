"""The reducer against the REAL emitter — finding I7, ADR-0198.

Every other test of :func:`~aelix_agents.stream.reduce_line` feeds it a fixture
someone typed. That makes the field map and the emitter two agreeing FICTIONS:
if both were wrong in the same direction — pi's camelCase names being the
obvious way to be wrong — every test would pass while every real delegation
reported an empty summary and zero usage.

So this file takes the other end of the wire. It drives the actual
``run_print_mode(runtime, mode="json", …)`` in-process, captures the bytes it
writes to stdout, and folds them through ``reduce_line`` exactly as
``PrintChannel._pump_stdout`` does. The harness helpers are the ones
``tests/cli/test_print_mode.py`` uses, so the emitter under test is the emitter
a child actually runs.

WHAT IT PROVES, concretely:

* the first line is the session-metadata header and has NO ``type`` key
  (``print_mode.py:237-248``) — hence ``event.get("type")``, never
  ``event["type"]``;
* the wire is snake_case ``dataclasses.asdict`` of the kernel's own event
  dataclasses, NOT pi's camelCase;
* ``message_end`` is where the summary and the usage live, and ``agent_end`` is
  a terminator that carries the whole message array again — which is why the
  reducer treats it as an end marker and nothing more.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_agents.stream import LineAssembler, _StreamState, reduce_line
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.modes.print_mode import run_print_mode

_REPLY = "I read three files and found the bug in envelope.py."

_USAGE = {
    "input": 1234,
    "output": 56,
    "cache_read": 7,
    "cache_write": 8,
    "total_tokens": 1290,
}


def _stream(
    *, stop_reason: str = "end_turn", usage: dict[str, Any] | None = None
) -> Any:
    """The mock ``stream_fn``, shaped like ``tests/cli/test_print_mode.py``'s."""

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=_REPLY)],
                stop_reason=stop_reason,
                usage=dict(usage) if usage is not None else None,
                provider="mock",
                model="mock-1",
            )
        )

    return fn


def _new_harness(stream_fn: Any) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=stream_fn,
        )
    )


def _new_runtime(harness: AgentHarness) -> AgentSessionRuntime:
    async def _noop(_s: Any) -> AgentHarness:
        return harness

    return AgentSessionRuntime(
        harness,
        _noop,
        repo=JsonlSessionRepo(fs=LocalFileSystem()),
        fs=LocalFileSystem(),
    )


async def _emit(
    capsys: pytest.CaptureFixture[str], **stream_kwargs: Any
) -> tuple[str, int]:
    harness = _new_harness(_stream(**stream_kwargs))
    runtime = _new_runtime(harness)
    exit_code = await run_print_mode(
        runtime,
        mode="json",
        messages=[],
        initial_message="Task: find the bug",
    )
    return capsys.readouterr().out, exit_code


def _reduce_through_the_pump(out: str) -> _StreamState:
    """Fold the captured bytes exactly as ``PrintChannel._pump_stdout`` does.

    Through :class:`LineAssembler`, and in CHUNKS rather than lines, because the
    chunking is part of what is under test: the assembler is the only reason
    "drop the oversize line and resync" is implementable at all (finding B3),
    and it must not mangle ordinary output on the way.
    """

    state = _StreamState()
    assembler = LineAssembler()
    raw = out.encode("utf-8")
    for start in range(0, len(raw), 97):  # a deliberately awkward chunk size
        for line in assembler.feed(raw[start : start + 97]):
            reduce_line(state, line)
    for line in assembler.flush():
        reduce_line(state, line)
    return state


async def test_reduce_line_over_real_emitter_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE I7 PIN — the parser and the emitter agree about a real stream."""

    out, exit_code = await _emit(capsys, usage=_USAGE)
    assert exit_code == 0

    state = _reduce_through_the_pump(out)

    assert state.summary == _REPLY
    assert state.turns >= 1
    assert state.input == _USAGE["input"]
    assert state.output == _USAGE["output"]
    assert state.cache_read == _USAGE["cache_read"]
    assert state.cache_write == _USAGE["cache_write"]
    assert state.tokens == _USAGE["total_tokens"]
    assert state.stop_reason == "end_turn"
    assert state.saw_agent_start is True
    assert state.saw_agent_end is True
    assert state.dropped_lines == 0


async def test_the_typeless_header_is_absent_under_no_session_and_harmless(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """MEASURED, and it corrects a plausible assumption about the wire.

    ``print_mode.py``'s JSON header emit is guarded by
    ``if session is not None`` and wrapped in a best-effort ``try/except``. A
    subagent runs with ``--no-session``, so the typeless session-metadata line
    is NOT the first line of a child's stream — the first line is
    ``agent_start``. A reducer that treated the header as a required preamble
    (or that indexed the first line at all) would be wrong for every delegation.

    The reducer therefore depends on neither its presence nor its absence: every
    read goes through ``event.get("type")``, and a line with no ``type`` folds
    into nothing rather than raising.
    """

    import json

    out, _ = await _emit(capsys, usage=_USAGE)
    lines = [line for line in out.splitlines() if line.strip()]
    assert json.loads(lines[0])["type"] == "agent_start"

    # And the header, when a sessioned child DOES emit one, is inert.
    state = _StreamState()
    reduce_line(state, json.dumps({"id": "sess-1", "created_at": "now"}))
    assert state.summary == ""
    assert state.saw_agent_start is False


async def test_the_wire_is_snake_case_not_pi_camel_case(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE PI-PORT TRAP, observed on real bytes.

    ``--mode json`` emits raw kernel ``AgentEvent`` dataclasses through
    ``dataclasses.asdict`` (``print_mode.py:221-228``), so the field names are
    Python's. A line-for-line port of pi's parser would read ``None`` for every
    field and every failure would look like a success with zero usage.
    """

    out, _ = await _emit(capsys, usage=_USAGE)
    assert '"stop_reason"' in out
    assert '"stopReason"' not in out
    assert '"message_end"' in out
    assert '"total_tokens"' in out


async def test_an_errored_run_still_yields_a_readable_stop_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A JSON-mode child whose model errored EXITS 0 with empty stderr.

    ``print_mode.py``'s ``stop_reason in ("error","aborted") → exit_code = 1``
    is guarded by ``if mode == "text"``. So the exit code is not evidence, the
    STREAM is — which is the whole reason ``envelope.build_result`` may tighten
    a caller's proposed outcome but never loosen it.
    """

    out, exit_code = await _emit(capsys, stop_reason="error", usage=None)
    state = _reduce_through_the_pump(out)

    assert exit_code == 0
    assert state.stop_reason == "error"
    # ``usage`` is null on an errored message; the dual-read helper must not
    # turn that into an exception or a bogus count.
    assert state.input == 0
    assert state.output == 0


async def test_agent_end_is_a_terminator_not_a_second_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``agent_end`` carries the whole message array on one line.

    Counting it as another turn would double every delegation's turn count and,
    on a stream whose last assistant turn was tool-calls-only, would silently
    replace a good summary with an empty one.
    """

    out, _ = await _emit(capsys, usage=_USAGE)
    state = _reduce_through_the_pump(out)

    lines = [line for line in out.splitlines() if line.strip()]
    assert '"agent_end"' in lines[-1]
    assert state.turns == 1
    assert state.summary == _REPLY
