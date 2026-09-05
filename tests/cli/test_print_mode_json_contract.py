"""THE WIRE CONTRACT — ADR-0198 §D7, and it is mandatory.

ADR-0198 heads its §D7 *"``tests/cli/test_print_mode_json_contract.py`` is
mandatory"* and the P2 plan lists this file as a required WS-D deliverable. It
was never written (P2 review, MEDIUM #9): ``ls tests/cli/ | grep -i print``
returned only ``test_print_mode.py``, whose JSON coverage
(``test_json_mode_emits_events_one_per_line``) asserts nothing but
*parseability*. An accepted ADR asserted a gate that did not exist.

WHY THE OTHER TWO TESTS DO NOT COVER IT.
``tests/cli/test_print_mode.py`` proves each line is valid JSON — a stream that
renamed every key would pass. ``tests/agents_ext/test_reduce_consumes_real_print
_mode_output.py`` proves the reducer and the emitter AGREE — and they agree just
as happily after both move together, which is the one refactor this file has to
catch. Concretely: rename ``stop_reason`` to ``stopReason`` in the kernel and
mirror it in ``stream.py``, and both of those stay green while every
already-installed parent reads ``None`` for every field of every child. That is
verbatim the pi-port trap ADR-0198 §D1 exists to prevent.

WHAT MAKES THIS DIFFERENT: THE PRODUCER IS A REAL CHILD PROCESS.
Every assertion below is made on bytes that crossed a real pipe from a real
second interpreter — ``asyncio.create_subprocess_exec`` → ``--mode json``
emitter → ``PIPE`` → this process — assembled by the SAME
:class:`~aelix_agents.stream.LineAssembler` a delegation uses. Nothing is
captured in-process and nothing is mocked below the process boundary, because
the contract being pinned is a WIRE format and an in-process capture cannot
fail the way a wire can (encoding, buffering, line framing, a `print` landing on
the wrong stream).

The provider is stubbed and nothing else is: a real child needs a real model, and
every test in this repo is offline (``tests/conftest.py``'s download guard is an
in-process ``monkeypatch`` and does not reach a child interpreter — finding I10).
So the child script drives the real ``run_print_mode(runtime, mode="json", …)``
with a scripted ``stream_fn``, which is the same seam
``tests/cli/test_print_mode.py`` uses. Everything from ``run_print_mode``
outward — ``_event_to_dict``, ``_dataclass_to_dict``, ``_write_raw_stdout``, the
kernel's own event dataclasses — is production code.

WHAT IT PINS (ADR-0198 §D7's own list):

* the event SEQUENCE — ``agent_start`` / ``turn_start`` / ``message_start`` /
  ``message_end`` / ``turn_end`` / ``agent_end``, in order, with ``agent_end``
  last;
* ``message_end.message`` carries ``role`` / ``stop_reason`` / ``error_message``
  / ``usage`` / ``model`` / ``provider`` in **snake_case**;
* the content discriminators are ``"text"`` and ``"toolCall"`` — the ONE
  camelCase island on an otherwise snake_case wire, because it is a
  ``Literal`` default on the dataclass rather than a field name;
* a tool-call block carries ``tool_name`` and ``input`` (NOT ``args`` — the
  ``tool_execution_start`` event uses ``args`` for the same data, and reading
  the wrong one yields an empty dict rather than an error);
* the typeless session header: present only when the child HAS a session, so a
  consumer must key on ``event.get("type")`` and must never index line 0.

It deliberately does NOT pin ``usage``'s inner keys — those are provider-shaped
and ADR-0198 §D2 makes them a dual-read on the consumer side.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.stream import LineAssembler

# ``_CHILD`` lives in ``tests/print_mode_child.py`` since #220, shared with
# ``tests/agents_ext/test_print_channel_spawn.py`` so the wire contract and the
# signal-exit-code guard can never drift onto two different children. Its
# default path — no ``--wedge``, no ``--ready`` — is byte-for-byte what this
# file always ran.
from tests.env_sandbox import child_env
from tests.print_mode_child import CHILD_SOURCE as _CHILD


def _child_env(tmp_path: Path) -> dict[str, str]:
    """A child interpreter that can import aelix and can reach no network.

    ``tests/conftest.py``'s download guard is an in-process
    ``monkeypatch.setattr`` and does not cross a process boundary (finding I10),
    so hermeticity has to be stated explicitly here. The stubbed ``stream_fn``
    means no provider is ever reached, and no API key is passed.
    """

    return child_env(tmp_path / "home", PI_OFFLINE="1", PYTHONUNBUFFERED="1")


async def _run_child(tmp_path: Path, *args: str) -> tuple[list[dict[str, Any]], int]:
    """Spawn the child, read fd 1 through the delegation's own line assembler.

    ``LineAssembler`` rather than ``splitlines()`` on purpose: it is the code a
    real parent uses (``PrintChannel._pump_stdout``), and framing is part of the
    contract. Chunked reads for the same reason — a line that only survives
    because the whole stream arrived in one ``read`` is not pinned.
    """

    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        *args,
        cwd=str(tmp_path),
        env=_child_env(tmp_path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assembler = LineAssembler()
    lines: list[str] = []
    stderr = bytearray()

    async def _pump_err() -> None:
        while True:
            chunk = await proc.stderr.read(65536)  # type: ignore[union-attr]
            if not chunk:
                break
            stderr.extend(chunk)

    async def _pump_out() -> None:
        while True:
            chunk = await proc.stdout.read(4096)  # type: ignore[union-attr]
            if not chunk:
                break
            lines.extend(assembler.feed(chunk))
        lines.extend(assembler.flush())

    await asyncio.wait_for(asyncio.gather(_pump_out(), _pump_err()), 90)
    exit_code = await asyncio.wait_for(proc.wait(), 30)
    assert lines, (
        "the child produced no stdout; stderr was:\n"
        + bytes(stderr).decode("utf-8", "replace")
    )
    return [json.loads(line) for line in lines if line.strip()], exit_code


def _types(events: list[dict[str, Any]]) -> list[str | None]:
    return [event.get("type") for event in events]


def _first(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(event for event in events if event.get("type") == kind)


def _assistant_ends(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event["message"]
        for event in events
        if event.get("type") == "message_end"
        and (event.get("message") or {}).get("role") == "assistant"
    ]


# === the sequence =============================================================


async def test_the_event_sequence_and_its_terminator(tmp_path: Path) -> None:
    """ADR-0198 §D7's ordering clause, on bytes from a real child.

    ``agent_end`` last is what makes "the child finished talking" decidable
    without waiting on the process, and the ``turn_start`` → ``message_start`` →
    ``message_end`` → ``turn_end`` nesting is what makes turn counting possible
    at all. A reordering here silently changes both for every installed parent.
    """

    events, exit_code = await _run_child(tmp_path)
    kinds = _types(events)

    assert exit_code == 0
    for required in (
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ):
        assert required in kinds, f"{required!r} vanished from the wire: {kinds}"

    assert kinds[0] == "agent_start"
    assert kinds[-1] == "agent_end"
    assert kinds.count("agent_start") == 1
    assert kinds.count("agent_end") == 1
    assert kinds.index("turn_start") < kinds.index("message_start")
    assert kinds.index("message_start") < kinds.index("message_end")
    assert kinds.index("message_end") < kinds.index("turn_end")


async def test_the_first_line_is_agent_start_when_the_child_has_no_session(
    tmp_path: Path,
) -> None:
    """MEASURED, and it corrects ADR-0198 §D7's own wording.

    §D7 says "the first line has no ``type``", meaning the session-metadata
    header. ``print_mode.py`` guards that emit with ``if session is not None``,
    and a subagent runs ``--no-session`` — so for the delegation case, which is
    the case ADR-0198 exists for, there is NO typeless preamble and the first
    line is ``agent_start``.

    Both facts matter to a consumer and neither may be assumed: read
    ``event.get("type")``, never ``event["type"]``, and never index line 0.
    """

    events, _ = await _run_child(tmp_path)
    assert events[0].get("type") == "agent_start"
    assert all("type" in event for event in events)


# === the field names ==========================================================


async def test_message_end_carries_snake_case_fields(tmp_path: Path) -> None:
    """THE PI-PORT TRAP (ADR-0198 §D1), pinned on the wire itself.

    ``--mode json`` emits the kernel's own event dataclasses through
    ``dataclasses.asdict``, so the field names are Python's, not pi's. A
    line-for-line port of pi's ``stopReason`` / ``errorMessage`` parser reads
    ``None`` for every field and every failure looks like a success with zero
    usage — which is exactly what a green parseability test cannot catch.
    """

    events, _ = await _run_child(tmp_path)
    message = _assistant_ends(events)[-1]

    for field in (
        "role",
        "stop_reason",
        "error_message",
        "usage",
        "model",
        "provider",
        "content",
    ):
        assert field in message, f"{field!r} missing from message_end.message"

    assert message["role"] == "assistant"
    assert message["stop_reason"] == "end_turn"
    assert message["error_message"] is None
    assert message["provider"] == "stub"
    assert message["model"] == "stub-1"

    # The camelCase spellings must be ABSENT, not merely unused: a wire carrying
    # both would let a wrong reader look right until the day the alias went.
    raw = "\n".join(json.dumps(event) for event in events)
    for camel in ('"stopReason"', '"errorMessage"', '"toolName"', '"messageEnd"'):
        assert camel not in raw, f"{camel} appeared on a snake_case wire"


async def test_usage_is_present_but_its_inner_keys_are_not_pinned(
    tmp_path: Path,
) -> None:
    """ADR-0198 §D2 — ``usage`` is provider-shaped, so only the SLOT is contract.

    ``AssistantMessage.usage`` is ``dict[str, Any] | None``: adapters disagree
    about the inner spelling and the kernel itself dual-reads
    (``session/compaction.py:1036-1043``). Pinning the inner keys here would
    make this test fail for a correct new provider, so it pins that the slot
    exists, survives the wire, and is ``null`` — not absent, not ``{}`` — on an
    errored message.
    """

    events, _ = await _run_child(tmp_path)
    usage = _assistant_ends(events)[-1]["usage"]
    assert isinstance(usage, dict)
    assert usage["input"] == 20

    errored, exit_code = await _run_child(tmp_path, "--errored")
    message = _assistant_ends(errored)[-1]
    assert message["usage"] is None
    assert message["stop_reason"] == "error"
    assert message["error_message"] == "the provider said no"
    # And the exit code is NOT evidence: the ``stop_reason → exit 1`` branch in
    # print_mode is guarded by ``if mode == "text"``. The STREAM is the evidence,
    # which is why ``envelope.build_result`` may tighten a proposed outcome.
    assert exit_code == 0


# === the content discriminators ===============================================


async def test_text_and_toolcall_discriminators(tmp_path: Path) -> None:
    """``"text"`` and ``"toolCall"`` — the one camelCase island, and it is load-bearing.

    Both are ``Literal`` DEFAULTS on the content dataclasses rather than field
    names, which is why they survive ``dataclasses.asdict`` unconverted while
    every field around them is snake_case. A consumer that "corrected" either to
    ``tool_call`` would silently classify every tool call as unknown content.

    The tool-call block's argument key is ``input``. ``tool_execution_start``
    carries the SAME data under ``args``, so reading the wrong one gets an empty
    dict rather than an error — a wrong answer that looks like a quiet child.
    """

    events, exit_code = await _run_child(tmp_path, "--with-tool")
    assert exit_code == 0

    blocks = [
        block
        for message in _assistant_ends(events)
        for block in message["content"]
    ]
    kinds = {block["type"] for block in blocks}
    assert "toolCall" in kinds
    assert "text" in kinds

    call = next(block for block in blocks if block["type"] == "toolCall")
    assert call["tool_name"] == "probe"
    assert call["input"] == {"path": "README.md"}
    assert call["tool_call_id"] == "tc-1"

    text = next(block for block in blocks if block["type"] == "text")
    assert text["text"] == "the complete answer"

    # The sibling event that duplicates it under a DIFFERENT key.
    started = _first(events, "tool_execution_start")
    assert started["tool_name"] == "probe"
    assert started["args"] == {"path": "README.md"}


async def test_a_tool_turn_keeps_the_sequence_intact(tmp_path: Path) -> None:
    """Two turns, and ``agent_end`` still terminates exactly once.

    A tool call means a second ``turn_start``; the summary lives on the LAST
    assistant ``message_end`` and ``agent_end`` re-emits the whole message array
    rather than adding a turn. Counting ``agent_end`` as a turn would double
    every delegation's turn count and, on a stream whose final assistant turn was
    tool-calls-only, would replace a good summary with an empty one.
    """

    events, _ = await _run_child(tmp_path, "--with-tool")
    kinds = _types(events)

    assert kinds.count("turn_start") == 2
    assert kinds.count("agent_end") == 1
    assert kinds[-1] == "agent_end"
    assert "messages" in events[-1], "agent_end re-emits the whole array"

    tool_kinds = [k for k in kinds if k and k.startswith("tool_execution")]
    assert tool_kinds == ["tool_execution_start", "tool_execution_end"]


# === the framing ==============================================================


async def test_every_line_is_one_complete_json_object(tmp_path: Path) -> None:
    """Line-delimited, one object per line, nothing else on fd 1.

    The parent reads fixed-size CHUNKS and reassembles (finding B3 — an explicit
    8 MiB ``limit=`` and a ``LineAssembler``, because ``readline()``'s 64 KiB
    ceiling raises unrecoverably on the ~207 KB ``message_end`` a routine file
    read produces). That only works if the producer never emits a bare line, a
    partial object, or a banner. ``_run_child`` above already parsed each line —
    this asserts the framing property that made it possible.
    """

    events, _ = await _run_child(tmp_path, "--with-tool")
    assert len(events) >= 8
    assert all(isinstance(event, dict) for event in events)


@pytest.mark.parametrize("args", [(), ("--with-tool",), ("--errored",)])
async def test_the_reducer_agrees_with_the_wire_for_every_shape(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    """The join between this file and the delegation that consumes it.

    This file pins the SHAPE and
    ``tests/agents_ext/test_reduce_consumes_real_print_mode_output.py`` pins that
    the reducer reads it; neither alone catches a coordinated rename. Folding the
    real child's bytes through the real reducer here closes the triangle: shape,
    reader, and the actual process boundary in one assertion.
    """

    from aelix_agents.stream import _StreamState, reduce_line

    events, _ = await _run_child(tmp_path, *args)
    state = _StreamState()
    for event in events:
        reduce_line(state, json.dumps(event))

    assert state.saw_agent_start is True
    assert state.saw_agent_end is True
    assert state.provider == "stub"
    assert state.model == "stub-1"
    if "--errored" in args:
        assert state.stop_reason == "error"
    else:
        assert state.summary == "the complete answer"
        assert state.stop_reason == "end_turn"
