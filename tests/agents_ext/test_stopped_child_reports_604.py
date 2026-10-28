"""#604 phase 0, second half — a stopped or failed child tells the parent what it did.

ADR-0276 D8–D14, D17, D18. Until #604 a child that did not finish came back as
whatever its stderr said — on a stop, a real child's "Request aborted" — with
the partial answer, the tool calls, the files it wrote and its session all
missing from what the parent's model read; and a child cancelled by Esc came
back as nothing at all. These tests pin each piece, at the level it lives at:

* the reducer (``stream.py``): the written files and the final error;
* ``runtime._run``: ``started``, the session, and the paths resolved from the
  child's directory to the parent's;
* ``envelope.build_result``: the reason (D10) and the resolved error (D17);
* the two renderers: the D11 single template, the D12 member block, and D18 —
  every result that is not a started failure renders exactly as before;
* D14 end to end, through a real ``AgentHarness``: Esc during a delegation
  queues a report the next prompt carries.

Platform-neutral throughout: the one real child is a stdlib-only script that
``stop_all`` ends the same way on every platform (its stderr line is written
before it sleeps, not from a signal handler), paths are compared in the ``/``
form ``runtime`` renders on every platform, and nothing waits on a clock.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import sys
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agents.aggregate import (
    BATCH_OUTPUT_BUDGET_BYTES,
    CANCEL_REPORT_FENCE_CLOSE,
    CANCEL_REPORT_FENCE_NOTE,
    CANCEL_REPORT_FENCE_OPEN,
    CANCEL_REPORT_HEAD,
    MEMBER_HEAD_BYTES,
    MemberOutcome,
    member_output_budget,
    render_batch_result,
    render_cancel_report,
)
from aelix_agents.envelope import (
    LINGERED_REASON,
    MAX_ERROR_NOTE_BYTES,
    MAX_REASON_CHARS,
    NO_OUTPUT,
    REASON_CANCELLED,
    REASON_OWN_ABORT,
    REASON_STOPPED,
    build_result,
    describe_exit,
    error_repeats_summary,
    render_tool_trail,
)
from aelix_agents.print_channel import PrintChannel, RunningChild, SpawnPlan, resolve_child_cwd
from aelix_agents.runtime import (
    _SESSION_DRAINING,
    MAX_LIVE_CHILDREN,
    SpawnRecordMeta,
    SubagentHost,
    _display_path,
    _SubagentRuntimeImpl,
    _written_files,
)
from aelix_agents.stream import (
    MAX_FILE_PATH_CHARS,
    MAX_FILE_PATHS,
    WRITER_TOOLS,
    _StreamState,
    path_for_display,
    reduce_event,
    written_paths,
)
from aelix_agents.tool import (
    DO_NOT_REDELEGATE,
    MAX_FILE_LIST_BYTES,
    NO_TEXT_YET,
    _usage_line,
    render_subagent_result,
)
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.builtin.guardrail import _WRITE_TOOLS
from aelix_coding_agent.subagent_contract import SubagentResult, SubagentUsage

from tests.agents_ext.test_child_session_records import _grant, _parent, _resolved
from tests.agents_ext.test_tool_and_security import _bench, _call, _text, _write_profile
from tests.event_waits import wait_until

# === Stream events, as a real child writes them ===============================


def _start(call_id: str, tool: str, **args: Any) -> dict[str, Any]:
    return {
        "type": "tool_execution_start",
        "tool_call_id": call_id,
        "tool_name": tool,
        "args": args,
    }


def _end(call_id: str, *, failed: bool = False) -> dict[str, Any]:
    return {"type": "tool_execution_end", "tool_call_id": call_id, "is_error": failed}


def _message(
    text: str = "",
    *,
    stop: str = "tool_use",
    error: str | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop,
        "usage": {"input": 5, "output": 1, "total_tokens": 6},
    }
    if error is not None:
        message["error_message"] = error
    return {"type": "message_end", "message": message}


def _reduced(*events: dict[str, Any]) -> _StreamState:
    state = _StreamState()
    for event in events:
        reduce_event(state, event)
    return state


def _pre_604_render(result: SubagentResult) -> str:
    """``render_subagent_result``'s text as it was before the D11 template.

    The D18 oracle. A copy of the renderer at the lane-A state of #604 (with
    D4's ``Note:``), so "unchanged" is asserted against the old code itself
    rather than against hand-written strings that could drift with it.
    """

    body = result.summary or "(no output)"
    notes: list[str] = []
    if result.error and not error_repeats_summary(body, result.error, truncated=result.truncated):
        notes.append(f"Error: {result.error}")
    if result.note:
        notes.append(f"Note: {result.note}.")
    if result.dropped_tools:
        notes.append(
            "Tools not granted to this agent: "
            + ", ".join(result.dropped_tools)
            + " (it holds no tool you do not hold)."
        )
    if result.dropped_lines:
        notes.append(f"{result.dropped_lines} oversize output line(s) were dropped.")
    return "\n\n".join([body, *notes, _usage_line(result)])


def _rendered(result: SubagentResult) -> str:
    return render_subagent_result(result).content[0].text  # type: ignore[union-attr]


# === D8 — the written files, in the reducer ===================================


def test_the_writer_list_is_the_guardrails_own() -> None:
    """Shared by import, never re-spelled: one list of tools that write a path."""

    assert WRITER_TOOLS is _WRITE_TOOLS
    assert {"edit", "write"} <= WRITER_TOOLS
    assert "bash" not in WRITER_TOOLS


def test_a_confirmed_write_is_changed_an_open_one_unconfirmed_a_failed_one_neither() -> None:
    state = _reduced(
        _start("c1", "write", path="done.py", content="x"),
        _end("c1"),
        _start("c2", "edit", path="cut_off.py"),
        _start("c3", "write", path="refused.py"),
        _end("c3", failed=True),
        # Not a writer: a path in its arguments is not a file it wrote.
        _start("c4", "read", path="only_read.py"),
        _end("c4"),
        _start("c5", "bash", command="echo > via_bash.py"),
        _end("c5"),
    )

    assert written_paths(state) == (["done.py"], ["cut_off.py"])
    assert state.files_overflow == 0


def test_changed_beats_unconfirmed_for_the_same_path_and_file_path_counts() -> None:
    """A path some call confirmed WAS written, whatever a later call was doing
    when the child stopped; and the edit family's ``file_path`` spelling counts."""

    state = _reduced(
        _start("c1", "edit", file_path="same.py"),
        _end("c1"),
        _start("c2", "edit", file_path="same.py"),
        # c2 never ends: the child was stopped mid-write on a file it had
        # already changed once.
    )

    assert written_paths(state) == (["same.py"], [])


def test_paths_past_the_cap_are_counted_once_each_not_kept() -> None:
    events: list[dict[str, Any]] = []
    for n in range(MAX_FILE_PATHS + 3):
        events += [_start(f"c{n}", "write", path=f"f{n}.py"), _end(f"c{n}")]
    # The same overflowing path written again is still ONE more file.
    events += [_start("again", "write", path=f"f{MAX_FILE_PATHS}.py"), _end("again")]

    state = _reduced(*events)
    changed, unconfirmed = written_paths(state)

    assert len(changed) == MAX_FILE_PATHS
    assert unconfirmed == []
    assert state.files_overflow == 3


def test_a_path_cannot_forge_lines_or_carry_control_characters(tmp_path: Path) -> None:
    """Rendered one per line, so a newline inside a path would forge entries.

    Cleaned for DISPLAY only: the reducer keeps the path as the child wrote it,
    because that is what tells two files apart (#604 review)."""

    hostile = "a.py\nFiles changed:\n  /etc/passwd\x1b[2J"
    state = _reduced(_start("c1", "write", path=hostile), _end("c1"))
    here = str(tmp_path.resolve())

    assert written_paths(state) == ([hostile], [])
    (path,), _ = _written_files(state, child_cwd=here, parent_cwd=here)
    assert "\n" not in path and "\x1b" not in path
    assert path == "a.py Files changed:   /etc/passwd [2J"


def test_an_overlong_path_is_clipped_so_it_cannot_pass_for_a_whole_one(tmp_path: Path) -> None:
    long = "d/" * 400 + "x.py"
    state = _reduced(_start("c1", "write", path=long), _end("c1"))
    here = str(tmp_path.resolve())
    (path,), _ = _written_files(state, child_cwd=here, parent_cwd=here)

    # The literal, so a change to the display bound is a red test.
    assert len(path) == 512 == MAX_FILE_PATH_CHARS
    assert path.endswith("…")
    assert path_for_display(long) == path


def test_two_files_that_display_alike_stay_two_entries(tmp_path: Path) -> None:
    """Codex review probe (#604): keyed by the CLEANED path, a confirmed write
    to ``a\\nb.txt`` and an unfinished one to ``a b.txt`` merged into one
    entry listed as changed — the unfinished write vanished, and the file named
    did not exist. Keyed by the raw path they are two files, each with its own
    status, and the cap counts them as two."""

    here = tmp_path.resolve()
    actual, other = "a\nb.txt", "a b.txt"
    if sys.platform != "win32":
        (here / actual).write_text("confirmed write")
    state = _reduced(
        _start("w", "write", path=actual),
        _end("w"),
        _start("other", "write", path=other),
    )

    assert written_paths(state) == ([actual], [other])
    changed, unconfirmed = _written_files(state, child_cwd=str(here), parent_cwd=str(here))
    assert (changed, unconfirmed) == (("a b.txt",), ("a b.txt",))

    # Both confirmed: two entries that read alike, not one.
    both = _reduced(
        _start("w1", "write", path=actual),
        _end("w1"),
        _start("w2", "write", path=other),
        _end("w2"),
    )
    assert _written_files(both, child_cwd=str(here), parent_cwd=str(here)) == (
        ("a b.txt", "a b.txt"),
        (),
    )

    # The cap counts raw paths: 64 that all display alike fill it, the 65th
    # overflows.
    crowded = _StreamState()
    for n in range(65):
        reduce_event(crowded, _start(f"c{n}", "write", path="x" + "\n" * n))
        reduce_event(crowded, _end(f"c{n}"))
    assert len(written_paths(crowded)[0]) == 64
    assert crowded.files_overflow == 1


# === D17 — the final error, in the reducer ====================================


def test_the_final_error_is_the_last_messages_and_a_recovered_one_is_cleared() -> None:
    """The 429 the harness's auto-retry recovered from stays in ``error_message``
    (last non-empty wins) and must NOT read as the run's error (critique 4)."""

    state = _reduced(
        {"type": "agent_start"},
        _message(stop="error", error="Error code: 429 - rate limited"),
        {"type": "agent_end"},
        {"type": "agent_start"},
        _message("I edited the parser and was about to run the tests"),
    )

    assert state.error_message == "Error code: 429 - rate limited"
    assert state.final_error is None

    failing = _reduced(_message("text"), _message(stop="error", error="boom"))
    assert failing.final_error == "boom"


# === D8 — the paths, as the PARENT reads them =================================


def test_a_child_in_another_directory_reports_paths_relative_to_the_parent(
    tmp_path: Path,
) -> None:
    """Critique 8: ``agent(..., cwd="packages/api")`` edits ``src/app.py``; the
    parent must read ``packages/api/src/app.py`` or it opens the wrong file."""

    parent = tmp_path / "repo"
    child = parent / "packages" / "api"
    child.mkdir(parents=True)
    elsewhere = str(tmp_path / "elsewhere" / "notes.md")
    state = _reduced(
        _start("c1", "edit", path="src/app.py"),
        _end("c1"),
        _start("c2", "write", path="./src/app.py"),  # the same file, spelled again
        _start("c3", "write", path="../../README.md"),
        _end("c3"),
        _start("c4", "write", path=elsewhere),
        _end("c4"),
        _start("c5", "write", path="../../../outside.txt"),
        _end("c5"),
    )

    changed, unconfirmed = _written_files(
        state, child_cwd=str(child.resolve()), parent_cwd=str(parent)
    )

    assert changed == (
        "packages/api/src/app.py",
        "README.md",
        # Outside the parent's directory: an absolute path stays as written…
        elsewhere,
        # …and a relative one that climbed out is spelled from the parent.
        "../outside.txt",
    )
    assert unconfirmed == (), "the second spelling of a changed file is not unconfirmed"


def test_a_symlinked_parent_directory_does_not_turn_paths_into_climbs(tmp_path: Path) -> None:
    """The child's directory is RESOLVED (``resolve_child_cwd``) and the host's
    cwd is the spelling the user started in; compared raw, ``pkg/a.py`` would
    read ``../real/pkg/a.py`` under any linked checkout."""

    real = tmp_path / "real"
    (real / "pkg").mkdir(parents=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    state = _reduced(_start("c1", "write", path="a.py"), _end("c1"))

    changed, _ = _written_files(
        state, child_cwd=resolve_child_cwd("pkg", str(link)), parent_cwd=str(link)
    )

    assert changed == ("pkg/a.py",)


def test_a_home_relative_path_outside_the_parent_is_not_expanded(tmp_path: Path) -> None:
    """``~`` stays ``~``: expanding it would spell the user's home directory
    into the model's context, which the child itself never did."""

    shown = _display_path("~/notes.md", child_cwd=str(tmp_path), parent_cwd=str(tmp_path))

    assert shown == "~/notes.md"


# === D10 / D17 — the reason and the resolved error ============================


def _built(**kwargs: Any) -> SubagentResult:
    base: dict[str, Any] = {"id": "sub-1", "profile": "scout", "state": _StreamState()}
    base.update(kwargs)
    return build_result(**base)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"outcome": "aborted", "exit_code": -15}, REASON_STOPPED),
        (
            {"state": _StreamState(stop_reason="aborted", saw_agent_end=True), "exit_code": 0},
            REASON_OWN_ABORT,
        ),
        ({"outcome": "aborted", "reason": REASON_CANCELLED}, REASON_CANCELLED),
        (
            {"state": _StreamState(summary="half"), "exit_code": -9, "lingered": True},
            LINGERED_REASON,
        ),
        (
            {"outcome": "error", "error": "first line\nsecond line", "exit_code": 1},
            "first line",
        ),
    ],
    ids=["stop_all", "own-abort", "esc", "lingering", "error-first-line"],
)
def test_the_reason_table(kwargs: dict[str, Any], reason: str) -> None:
    result = _built(**kwargs)

    assert result.ok is False
    assert result.reason == reason


def test_a_success_has_no_reason_even_when_one_is_offered() -> None:
    result = _built(
        state=_StreamState(summary="done", stop_reason="end_turn", saw_agent_end=True),
        exit_code=0,
        reason=REASON_CANCELLED,
    )

    assert (result.ok, result.reason) == (True, None)


def test_a_reason_is_one_line_of_at_most_three_hundred_characters() -> None:
    result = _built(outcome="error", error="\x1b[31m" + "x" * 5000 + "\nnext", exit_code=1)

    assert result.reason is not None
    assert len(result.reason) == MAX_REASON_CHARS
    assert "\n" not in result.reason and "\x1b" not in result.reason


def test_a_recovered_429_is_not_the_reason_or_the_error_of_a_later_failure() -> None:
    """Critique 4, end to end through the reducer: turn 1 got a 429 and
    retried, turn 2 wrote partial text, and then the child was SIGKILLed (an
    OOM kill leaves exactly this: exit -9, no stderr, no error of its own)."""

    state = _reduced(
        {"type": "agent_start"},
        _message(stop="error", error="Error code: 429 - rate limited"),
        {"type": "agent_end"},
        {"type": "agent_start"},
        _message("I edited the parser and was about to run the tests"),
    )

    result = build_result(id="sub-1", profile="scout", state=state, exit_code=-9)

    assert result.status == "error"
    assert result.summary == "I edited the parser and was about to run the tests"
    assert result.error == "killed by signal SIGKILL"
    assert result.reason == "killed by signal SIGKILL"
    assert "429" not in _rendered(dataclasses.replace(result, started=True))
    # Kept as evidence, where the model does not read it.
    assert result.details is not None and "Error code: 429" in result.details


def test_a_stderr_only_diagnosis_beside_a_partial_answer_reaches_the_model() -> None:
    """Critique 3 (b): after D9 the partial answer leads, so a stderr-only
    diagnosis must come back through ``error`` or it is visible nowhere."""

    stderr = 'Traceback (most recent call last):\n  File "x.py", line 1\nValueError: bad config'
    result = build_result(
        id="sub-1",
        profile="scout",
        state=_StreamState(summary="I read the config and", saw_agent_start=True),
        exit_code=1,
        stderr_tail=stderr,
    )
    text = _rendered(dataclasses.replace(result, started=True))

    assert result.summary == "I read the config and"
    assert result.error == stderr
    assert "Partial result:\nI read the config and" in text
    assert f"Error: {stderr}" in text


@pytest.mark.parametrize(
    ("code", "words"),
    [
        (-9, "killed by signal SIGKILL"),
        (-15, "killed by signal SIGTERM"),
        (3, "exited with code 3"),
        (-64, "killed by signal 64"),
    ],
)
def test_an_exit_status_is_described_the_same_on_every_platform(code: int, words: str) -> None:
    assert describe_exit(code) == words


def test_a_stop_adds_no_error_of_its_own() -> None:
    """On ``aborted`` the reason says who stopped it; "Request aborted" (a real
    child's SIGTERM stderr) and the parent's own signal are not a diagnosis,
    and they no longer displace the partial answer either (D9)."""

    result = _built(
        state=_StreamState(summary="partial work done"),
        outcome="aborted",
        exit_code=-15,
        stderr_tail="Request aborted\n",
    )

    assert result.summary == "partial work done"
    assert result.error is None
    assert result.reason == REASON_STOPPED


# === D11 — what the model reads for a started child that did not finish =======


def _unfinished(**kwargs: Any) -> SubagentResult:
    base: dict[str, Any] = {
        "id": "sub-1",
        "profile": "scout",
        "ok": False,
        "status": "aborted",
        "summary": "partial work done",
        "started": True,
        "reason": REASON_STOPPED,
        "session_id": "0b8a2a8e-0000-4000-8000-000000000001",
        "session_path": "/home/someone/.aelix/sessions/--repo--/p/sub-1.jsonl",
        "tool_trail": "write(notes/a.md) ok\nedit(b.py) (no result)",
        "changed_files": ("notes/a.md",),
        "unconfirmed_files": ("b.py",),
        "permission_mode": "plan",
    }
    base.update(kwargs)
    return SubagentResult(**base)


def test_the_unfinished_template_in_full() -> None:
    text = _rendered(_unfinished(error="Error code: 500"))

    assert text == (
        f"The delegated agent did not finish: {REASON_STOPPED}\n"
        f"{DO_NOT_REDELEGATE}\n"
        "\n"
        "Partial result:\n"
        "partial work done\n"
        "\n"
        "Error: Error code: 500\n"
        "\n"
        "Tool calls (2):\n"
        "write(notes/a.md) ok\n"
        "edit(b.py) (no result)\n"
        "\n"
        "Files changed:\n"
        "  notes/a.md\n"
        "Unconfirmed (stopped mid-write):\n"
        "  b.py\n"
        "(files changed through bash or other tools are not listed)\n"
        "\n"
        "Session: 0b8a2a8e-0000-4000-8000-000000000001\n"
        "\n"
        "[agent scout · aborted · plan · 0.0s]"
    )
    assert render_subagent_result(_unfinished()).is_error is True


def test_the_session_path_never_reaches_the_model() -> None:
    """ADR-0243 §5: a path would put the home directory and the user name into
    the model's context. The id names the file; hosts read the path."""

    result = _unfinished()
    text = _rendered(result)

    assert result.session_path is not None and result.session_path not in text
    assert "/home/someone" not in text
    assert f"Session: {result.session_id}" in text


def test_a_child_that_wrote_no_text_says_so_rather_than_quoting_its_error() -> None:
    """The fallback chain's summary is not the child's text: a summary that IS
    the final error reads as "no text", and the error is printed as the error."""

    result = _unfinished(status="error", summary="model not found", error="model not found")
    text = _rendered(result)

    assert f"Partial result:\n{NO_TEXT_YET}" in text
    assert "Error: model not found" in text


def test_the_retry_guidance_needs_work_to_point_at() -> None:
    """A child that failed on its first request did nothing: no tool call, no
    file. "It already did the work below" would be false of it (critique 5)."""

    idle = _unfinished(
        status="error",
        summary=NO_OUTPUT,
        error="exited with code 1",
        reason="exited with code 1",
        tool_trail=None,
        changed_files=(),
        unconfirmed_files=(),
    )
    text = _rendered(idle)

    assert DO_NOT_REDELEGATE not in text
    assert "Files changed" not in text
    assert text.startswith("The delegated agent did not finish: exited with code 1\n\n")
    assert DO_NOT_REDELEGATE in _rendered(_unfinished())


def test_the_single_mode_error_note_is_capped_at_four_kib() -> None:
    """D17: a dead RPC child's ``RpcServerExited`` carries up to 64 KiB of
    stderr, and it became the note verbatim."""

    huge = "e" * (64 * 1024)
    started = _rendered(_unfinished(status="error", error=huge))
    refused = _rendered(
        SubagentResult(
            id="s", profile="scout", ok=False, status="error", summary=NO_OUTPUT, error=huge
        )
    )

    for text in (started, refused):
        assert text.count("e" * 100) > 0
        assert text.count("e") <= MAX_ERROR_NOTE_BYTES + 64, "the note is capped"
        assert f"{64 * 1024 - MAX_ERROR_NOTE_BYTES} bytes omitted" in text


def test_the_file_lists_share_two_kib_and_say_how_many_they_left_out() -> None:
    changed = tuple(f"src/module_{n:03d}/implementation_file.py" for n in range(60))
    result = _unfinished(changed_files=changed, unconfirmed_files=("late.py",), files_overflow=4)
    text = _rendered(result)
    (block,) = [part for part in text.split("\n\n") if part.startswith("Files changed:")]

    listed = [line for line in block.splitlines() if line.startswith("  ")]
    listed_bytes = sum(len(line.encode()) + 1 for line in block.splitlines()[:-1])
    hidden = 60 + 1 + 4 - len(listed)
    assert listed_bytes <= MAX_FILE_LIST_BYTES
    assert block.splitlines()[-1] == (
        f"(+{hidden} more files; files changed through bash or other tools are not listed)"
    )


# === D9 / D11 / D8 end to end — test 7, a stop through a real child ============

_STOPPABLE_CHILD = textwrap.dedent(
    """
    import json, sys, time

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    emit({"type": "agent_start"})
    emit({"type": "turn_start"})
    emit({"type": "tool_execution_start", "tool_call_id": "c1", "tool_name": "write",
          "args": {"path": "notes/a.md", "content": "x"}})
    emit({"type": "tool_execution_end", "tool_call_id": "c1", "is_error": False})
    emit({"type": "message_end", "message": {"role": "assistant",
          "content": [{"type": "text", "text": "partial work done"}],
          "stop_reason": "tool_use", "usage": {"input": 4, "output": 2, "total_tokens": 6}}})
    # What a real child prints when the parent stops it (measured at #220),
    # written up front so the test does not depend on a signal handler.
    sys.stderr.write("Request aborted\\n")
    sys.stderr.flush()
    emit({"type": "tool_execution_start", "tool_call_id": "c2", "tool_name": "edit",
          "args": {"path": "b.py"}})
    time.sleep(60)
    """
)


async def test_a_child_stopped_by_stop_all_tells_the_parent_what_it_did(tmp_path: Path) -> None:
    """Design test 7. A REAL child, stopped by ``stop_all`` mid-edit:

    * the reason is the first line;
    * its "Request aborted" stderr does not displace the partial answer (D9);
    * the trail, the session id and the written files are in the text, and the
      session's path is not (D8, D11).
    """

    session, _repo, _path = await _parent(tmp_path)
    project = tmp_path / "p"

    def _build(*_args: Any, **_kwargs: Any) -> list[str]:
        return [sys.executable, "-c", _STOPPABLE_CHILD]

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(project), session=lambda: session),
        channel=PrintChannel(grace=0.5, argv_builder=_build),
    )
    task = asyncio.ensure_future(
        runtime.spawn_granted(
            _grant(),
            _resolved(),
            "go",
            record=SpawnRecordMeta(tool_call_id="tc-7", mode="single", index=0),
        )
    )
    await wait_until(
        lambda: runtime.list() and runtime.list()[0]["current_tool"] == "edit",
        what="the child to start its second write",
        detail=lambda: f"rows: {runtime.list()!r}",
    )
    await runtime.stop_all()
    result = await asyncio.wait_for(task, 30)
    text = _rendered(result)

    assert (result.status, result.started, result.reason) == ("aborted", True, REASON_STOPPED)
    assert result.summary == "partial work done"
    assert result.changed_files == ("notes/a.md",)
    assert result.unconfirmed_files == ("b.py",)
    assert result.session_id is not None and result.session_path is not None
    assert text.splitlines()[0] == f"The delegated agent did not finish: {REASON_STOPPED}"
    assert "Partial result:\npartial work done" in text
    assert "write(notes/a.md) ok" in text and "edit(b.py) (no result)" in text
    assert "Files changed:\n  notes/a.md\nUnconfirmed (stopped mid-write):\n  b.py" in text
    assert f"Session: {result.session_id}" in text
    assert result.session_path not in text and str(tmp_path) not in text


# === D18 / test 9 / test 10 — everything else renders exactly as before ========


def test_a_success_carrying_every_new_field_renders_byte_for_byte_as_before() -> None:
    """Critique 15: the old success pin had no session, trail or files, so it
    could not catch them leaking into a SUCCESS's text."""

    result = SubagentResult(
        id="sub-1",
        profile="scout",
        ok=True,
        status="ok",
        summary="the answer",
        started=True,
        session_id="0b8a2a8e-0000-4000-8000-000000000001",
        session_path="/home/someone/.aelix/sessions/x.jsonl",
        tool_trail="read(a.py) ok\nedit(a.py) ok",
        changed_files=("a.py",),
        unconfirmed_files=("b.py",),
        files_overflow=2,
        permission_mode="plan",
        usage=SubagentUsage(input=10, output=2, turns=1),
    )

    assert _rendered(result) == _pre_604_render(result)
    assert (
        _rendered(result)
        == "the answer\n\n[agent scout · ok · plan · 1 turns · 10 in / 2 out · 0.0s]"
    )


async def test_a_success_through_the_runtime_renders_as_before(tmp_path: Path) -> None:
    """The same pin one layer up: ``_run`` fills the new fields from a stream
    that wrote a file and a session that exists, and the text does not move."""

    session, _repo, _path = await _parent(tmp_path)

    class _Writes:
        async def run(
            self, plan: SpawnPlan, *, child: RunningChild | None = None, on_stream: Any = None
        ) -> SubagentResult:
            assert child is not None
            child.proc = _NO_PROCESS
            for event in (_start("c1", "write", path="a.py"), _end("c1")):
                reduce_event(child.stream, event)
            child.state = "done"
            return SubagentResult(
                id=plan.id,
                profile=plan.resolved.name,
                ok=True,
                status="ok",
                summary="the answer",
                permission_mode=plan.permission_mode.value,
                tool_trail=render_tool_trail(child.stream),
            )

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path / "p"), session=lambda: session),
        channel=_Writes(),
    )
    result = await runtime.spawn_granted(_grant(), _resolved(), "go")

    assert (result.started, result.changed_files) == (True, ("a.py",))
    assert result.session_id is not None
    assert _rendered(result) == "the answer\n\n[agent scout · ok · plan · 0.0s]"


async def test_refusals_and_declines_render_exactly_as_before(tmp_path: Path) -> None:
    """D18: the drain, the live cap, the budget and a decline produced no child,
    and "it already did the work below" would be false of every one of them."""

    session, _repo, _path = await _parent(tmp_path)
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path / "p"), session=lambda: session),
        channel=_NeverCalled(),
    )
    results: list[SubagentResult] = []

    runtime._closed = True
    results.append(await runtime.spawn_granted(_grant(), _resolved(), "go"))
    runtime._closed = False
    for n in range(MAX_LIVE_CHILDREN):
        runtime._children[f"held-{n}"] = RunningChild(id=f"held-{n}", profile="x")
    results.append(await runtime.spawn_granted(_grant(), _resolved(), "go"))
    runtime._children.clear()
    runtime._delegations_this_prompt = 10**6
    results.append(await runtime.spawn_granted(_grant(), _resolved(), "go"))
    declined = dataclasses.replace(_grant(), consented=False)
    results.append(await runtime.spawn_granted(declined, _resolved(), "go"))

    assert results[0].summary == _SESSION_DRAINING
    for result in results:
        assert result.started is False
        assert _rendered(result) == _pre_604_render(result)
        assert "did not finish" not in _rendered(result)


async def test_a_spawn_that_failed_inside_the_channel_is_not_started(tmp_path: Path) -> None:
    """The channel ran but no process ever existed: not "started" (D8), so the
    model is not told the child did any work (D18)."""

    missing = str(tmp_path / "no-such-interpreter")
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path)),
        channel=PrintChannel(grace=0.5, argv_builder=lambda *_a, **_k: [missing]),
    )
    result = await runtime.spawn_granted(_grant(), _resolved(), "go")

    assert (result.ok, result.started) == (False, False)
    assert _rendered(result) == _pre_604_render(result)


def test_a_batch_member_that_never_got_a_process_keeps_the_old_block() -> None:
    """Admitted (``MemberOutcome.ran``) but never spawned: the old failed block."""

    result = SubagentResult(
        id="s", profile="scout", ok=False, status="error", summary=NO_OUTPUT, error="spawn failed"
    )
    text = _batch_text([MemberOutcome.ran(result)])

    assert "[1/1 error] (no output)\nError: spawn failed" in text
    assert "did not finish" not in text


# === D12 — a failed member of a parallel call ==================================


def _batch_text(members: list[MemberOutcome]) -> str:
    result = render_batch_result("scout", "parallel", members, not_run=0, wall_ms=1000)
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def test_a_failed_member_carries_its_reason_calls_session_and_files() -> None:
    trail = "\n".join(f"read(f{n}.py) ok" for n in range(8))
    member = MemberOutcome.ran(_unfinished(tool_trail=trail, error="Error code: 500"))
    block = _batch_text([member, MemberOutcome.ran(_unfinished(id="sub-2"))]).split("\n\n")[1]
    lines = block.splitlines()

    assert lines[0] == (
        f"[1/2 aborted] did not finish: {REASON_STOPPED}"
        " · session 0b8a2a8e-0000-4000-8000-000000000001"
    )
    assert lines[1] == "Partial result: partial work done"
    assert lines[2] == "Error: Error code: 500"
    # The LAST five calls — where it stopped — of eight.
    assert lines[3] == "Tool calls (8, last 5):"
    assert lines[4:9] == [f"read(f{n}.py) ok" for n in range(3, 8)]
    assert "Files changed:\n  notes/a.md\nUnconfirmed (stopped mid-write):\n  b.py" in block
    assert "/home/someone" not in block
    assert lines[-1] == "[agent scout · aborted · plan · 0.0s]"


def _worst_member(index: int) -> MemberOutcome:
    """A failed member at every cap at once: a capped summary, a 64 KiB error,
    a full trail and the full file list."""

    state = _StreamState()
    for n in range(260):
        reduce_event(state, _start(f"c{n}", "read", path=f"src/a/really/quite/deep/path/{n}.py"))
        reduce_event(state, _end(f"c{n}"))
    summary = "x" * 51_200
    return MemberOutcome.ran(
        _unfinished(
            id=f"sub-{index}",
            status="error",
            summary=summary + "\n\n[Output truncated: 1000 bytes omitted.]",
            truncated=True,
            error="E" * (64 * 1024),
            reason="E" * MAX_REASON_CHARS,
            tool_trail=render_tool_trail(state),
            changed_files=tuple(f"src/module_{n:02d}/file_{n:02d}.py" for n in range(64)),
            unconfirmed_files=("late.py",),
            files_overflow=9,
        )
    )


def test_eight_failed_members_at_every_cap_stay_inside_the_batch_budget() -> None:
    """Critique 9: four parts and a summary must fit the share they split.

    Each part is held to its own share — the heading 300 B, the note a quarter,
    the calls an eighth, the files an eighth — and the whole call to the 64 KiB
    budget plus the frame every member already had: the tag, the labels and
    markers the caps append, and the usage line."""

    members = [_worst_member(n) for n in range(8)]
    text = _batch_text(members)
    budget = member_output_budget(8)
    # Split at each member's heading: a truncation marker inside a block is a
    # blank line too, so a plain "\n\n" split would cut blocks apart.
    body = text.split("\n\n[total] ", 1)[0]
    blocks = re.split(r"\n\n(?=\[\d+/8 )", body)[1:]

    assert len(blocks) == 8
    for block in blocks:
        lines = block.splitlines()
        head = lines[0].split("] ", 1)[1]
        assert len(head.encode()) <= MEMBER_HEAD_BYTES
        error = next(line for line in lines if line.startswith("Error: "))
        assert len(error.encode()) <= budget // 4 + 120
        calls_at = next(i for i, line in enumerate(lines) if line.startswith("Tool calls ("))
        files_at = next(i for i, line in enumerate(lines) if line == "Files changed:")
        calls = lines[calls_at:files_at]
        assert sum(len(line.encode()) + 1 for line in calls) <= budget // 8
        files = lines[files_at:-1]
        assert sum(len(line.encode()) + 1 for line in files[:-1]) <= budget // 8
        assert files[-1].startswith("(+")
        assert "files changed through bash or other tools are not listed" in files[-1]
    frame_per_member = 600
    assert len(text.encode()) <= BATCH_OUTPUT_BUDGET_BYTES + 8 * frame_per_member


def test_a_member_whose_trail_was_cut_claims_no_position_for_its_calls() -> None:
    """A trail cut at its byte cap kept its FIRST calls; "last" would be false."""

    trail = "read(a.py) ok\nread(b.py) ok\n… 40 more"
    block = _batch_text([MemberOutcome.ran(_unfinished(tool_trail=trail))]).split("\n\n")[1]

    assert "Tool calls (42, 2 shown):\nread(a.py) ok\nread(b.py) ok" in block


# === D14 — Esc during a delegation is reported on the next turn ===============

_NO_PROCESS = object()
"""Stands in for ``RunningChild.proc``: "a process existed". A bare object on
purpose — anything that tried to signal it would fail on a missing ``pid``
rather than reach a real process."""


class _NeverCalled:
    async def run(self, plan: SpawnPlan, **_kwargs: Any) -> SubagentResult:
        raise AssertionError("a refused spawn reached the channel")


class _Parking:
    """A channel whose child does some work and then parks until cancelled.

    A task text of ``"finish"`` instead returns a finished result straight away,
    so a batch can hold a member that ended before the cancel beside one that
    was running when it landed. ``spawn=False`` parks BEFORE any process
    exists: a cancel then cancels a child that never started.
    """

    def __init__(self, *, spawn: bool = True) -> None:
        self.parked = 0
        self.arrived = asyncio.Event()
        self._spawn = spawn

    async def run(
        self, plan: SpawnPlan, *, child: RunningChild | None = None, on_stream: Any = None
    ) -> SubagentResult:
        assert child is not None
        if self._spawn:
            child.proc = _NO_PROCESS
            child.state = "running"
            for event in (
                {"type": "agent_start"},
                _start(f"{plan.task}-w", "write", path=f"{plan.task}.md"),
                _end(f"{plan.task}-w"),
                _message(f"{plan.task}: halfway"),
                _start(f"{plan.task}-e", "edit", path="shared.py"),
            ):
                reduce_event(child.stream, event)
        if plan.task == "finish":
            child.state = "done"
            return SubagentResult(
                id=plan.id,
                profile=plan.resolved.name,
                ok=True,
                status="ok",
                summary="finished before the cancel",
                permission_mode=plan.permission_mode.value,
            )
        self.parked += 1
        self.arrived.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable: the turn is cancelled first")


def _tool_call(args: dict[str, Any]) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCallContent(tool_call_id="call-1", tool_name="agent", input=args)],
        stop_reason="tool_use",
    )


def _scripted(first: AssistantMessage, seen: list[list[Any]]) -> Any:
    async def stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(list(context.messages))
        reply = (
            first
            if len(seen) == 1
            else AssistantMessage(content=[TextContent(text="noted")], stop_reason="end_turn")
        )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=reply)

    return stream


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    return "".join(getattr(block, "text", "") or "" for block in content or [])


async def _cancel_a_delegation(
    tmp_path: Path, channel: _Parking, args: dict[str, Any], *, parked: int
) -> tuple[AgentHarness, list[list[Any]], Any]:
    """Prompt a real harness whose model delegates, then Esc while it runs."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, channel=channel)  # type: ignore[arg-type]
    session, _repo, _path = await _parent(tmp_path)
    bench.ext.session = lambda: session
    seen: list[list[Any]] = []
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            extensions=[bench.extension],
            runtime=bench.runtime,
            stream_fn=_scripted(_tool_call(args), seen),
        )
    )
    turn = asyncio.ensure_future(harness.prompt("delegate it"))
    await wait_until(
        lambda: channel.parked >= parked or turn.done(),
        what="the delegation to be running",
        detail=lambda: f"parked={channel.parked} turn_done={turn.done()}",
    )
    assert not turn.done(), "the turn ended before it could be cancelled"
    await harness.abort()
    await asyncio.wait_for(turn, 30)
    return harness, seen, session


async def test_esc_during_a_delegation_queues_its_report_for_the_next_prompt(
    tmp_path: Path,
) -> None:
    """Design test 13, single mode, through the real harness and extension.

    ``abort()`` clears the steer and follow-up queues and NOT the next-turn
    queue, and the next ``prompt()`` puts what that queue holds AHEAD of the
    user's own message — both asserted against the harness itself, not
    assumed."""

    channel = _Parking()
    harness, seen, _session = await _cancel_a_delegation(
        tmp_path, channel, {"profile": "scout", "task": "look"}, parked=1
    )

    queued = list(harness._next_turn_queue)
    assert len(queued) == 1, queued
    report = _message_text(queued[0])
    # The head first and outside; everything the child produced fenced as DATA.
    assert report.startswith(f"{CANCEL_REPORT_HEAD}\n\n{CANCEL_REPORT_FENCE_OPEN}\n")
    assert report.endswith(f"\n{CANCEL_REPORT_FENCE_CLOSE}\n{CANCEL_REPORT_FENCE_NOTE}")
    assert f"The delegated agent did not finish: {REASON_CANCELLED}" in report
    assert "Partial result:\nlook: halfway" in report
    assert "write(look.md) ok\nedit(shared.py) (no result)" in report
    assert "Files changed:\n  look.md\nUnconfirmed (stopped mid-write):\n  shared.py" in report
    assert "Session: " in report and str(tmp_path) not in report

    await harness.prompt("what happened?")

    texts = [_message_text(message) for message in seen[1]]
    report_at = next(i for i, text in enumerate(texts) if text.startswith(CANCEL_REPORT_HEAD))
    prompt_at = next(i for i, text in enumerate(texts) if text == "what happened?")
    assert report_at < prompt_at
    assert harness._next_turn_queue == []


async def test_esc_during_a_batch_reports_each_member_that_had_a_child(tmp_path: Path) -> None:
    """Batch: one block per member that had a child — the one that finished
    before the cancel AND the one that was running — under its own position."""

    channel = _Parking()
    harness, _seen, _session = await _cancel_a_delegation(
        tmp_path,
        channel,
        {"profile": "scout", "mode": "parallel", "tasks": ["finish", "park"]},
        parked=1,
    )

    (queued,) = harness._next_turn_queue
    report = _message_text(queued)
    assert report.startswith(
        f"{CANCEL_REPORT_HEAD}\n\n{CANCEL_REPORT_FENCE_OPEN}\nagent scout · parallel · 2 tasks"
    )
    assert "[1/2 ok] finished before the cancel" in report
    assert f"[2/2 aborted] did not finish: {REASON_CANCELLED} · session " in report
    assert "Partial result: park: halfway" in report
    assert "had no agent running" not in report


async def test_nothing_is_queued_when_no_child_had_started(tmp_path: Path) -> None:
    """A cancel that landed before any process existed cancelled nothing that
    did work, and a report would describe work that never happened."""

    channel = _Parking(spawn=False)
    harness, _seen, _session = await _cancel_a_delegation(
        tmp_path, channel, {"profile": "scout", "task": "look"}, parked=1
    )

    assert harness._next_turn_queue == []


async def test_a_call_that_returns_leaves_no_report(tmp_path: Path) -> None:
    """The collector is read on the cancel path only: a delegation that came
    back normally is in its own ``ToolResult`` and queues nothing — even though
    its child DID start, so a report would otherwise have something to say."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, channel=_Parking())  # type: ignore[arg-type]
    queued: list[Any] = []
    bench.ext._api = _QueueSpy(bench.ext._api, queued)

    result = await _call(bench, {"profile": "scout", "task": "finish"})

    assert result.is_error is False
    assert _text(result).startswith("finished before the cancel")
    assert queued == []


class _QueueSpy:
    """Records ``send_message`` instead of sending it; everything else passes."""

    def __init__(self, inner: Any, sink: list[Any]) -> None:
        self._inner = inner
        self._sink = sink

    def send_message(self, message: Any, **kwargs: Any) -> None:
        self._sink.append((message, kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_the_report_renders_nothing_without_a_started_child() -> None:
    never = SubagentResult(id="s", profile="scout", ok=False, status="error", summary="x")

    assert render_cancel_report("scout", "single", 1, {}) is None
    assert render_cancel_report("scout", "parallel", 2, {0: never}) is None


def test_a_batch_report_names_the_positions_that_had_no_child() -> None:
    running = _unfinished(reason=REASON_CANCELLED)
    report = render_cancel_report("scout", "chain", 3, {1: running})

    assert report is not None
    assert "[2/3 aborted] did not finish: the turn was cancelled" in report
    # aelix's own statement, after the fence rather than inside it.
    assert report.endswith(
        f"{CANCEL_REPORT_FENCE_NOTE}\n\n"
        "Steps 1, 3 of 3 had no agent running when the turn was cancelled: "
        "not started, or not reached."
    )
