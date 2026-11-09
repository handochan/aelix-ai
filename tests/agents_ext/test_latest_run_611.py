"""A delegated child's status follows its LATEST run (#611 S1a, Codex r5 finding 6).

One child process can make several runs: an auto-retry is a second run
(``agent_start`` → ``agent_end`` → ``agent_start``), and so is the harness's
overflow recovery. The envelope judged the terminator by the latched
``saw_agent_end``, which the FIRST ``agent_end`` sets for good, so a child whose
last run was cut off read as finished. Measured on main ``fc8f3048`` by feeding
the first case's events through the real reducer and ``build_result``::

    {"probe": "open-latest-run", "ok": true, "status": "ok",
     "summary": "run 1 answer", "error": null}

The reducer now opens a run at each ``agent_start``, and the terminator that
counts is the latest run's (:func:`~aelix_agents.stream.latest_run_closed`).

The stop reason is the latest run's too (Codex review of #721, finding 3, and
the verify round's should-fix 1). ``agent_start`` resets it, and ``turn_end``
carries it, because the harness's abort close-out ends a run with ``turn_end``
holding an ``aborted`` message of its own. A real child prints that close-out
after its run's user ``message_end`` when the abort lands mid-stream, and after
the tool result's when it lands in a tool; an ``aborted`` ``turn_end`` is read
in both (fix round 2 of #721). Measured on ``ed76ea76``
through the real print channel, Codex's sequence (run 1 answers, run 2 is
aborted, the child then lingers) came back ``ok=True status='ok'
stop_reason='end_turn'``; the verify round's (run 1 errors, run 2 is aborted,
exit 0) came back ``error`` only because run 1's ``error`` was still standing.
ADR-0276 D4's linger success is unchanged for a closed latest run: the versioned
``print_done`` that would tighten it is #611 S1c, behind the jobs gate, so a
legacy child that closed its last run and then lingered stays ``ok`` with its
note.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import aelix_agents.print_channel as pc
import pytest
from aelix_agents.envelope import (
    LINGERED_NOTE,
    LINGERED_REASON,
    NO_OUTPUT,
    REASON_OWN_ABORT,
    STREAM_ENDED_EARLY,
    build_result,
)
from aelix_agents.stream import _StreamState, latest_run_closed, reduce_event, reduce_line
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent

from tests.agents_ext.test_print_channel_spawn import _plan, _stub, _stub_channel


def _answer(text: str, stop_reason: str = "stop") -> dict[str, Any]:
    message = AssistantMessage(content=[TextContent(text=text)], stop_reason=stop_reason)  # type: ignore[arg-type]
    if stop_reason == "error":
        message = dataclasses.replace(message, error_message=text)
    return {"type": "message_end", "message": dataclasses.asdict(message)}


_START: tuple[dict[str, Any], ...] = ({"type": "agent_start"}, {"type": "turn_start"})
_END: dict[str, Any] = {"type": "agent_end", "messages": []}


def _fold(*events: dict[str, Any]) -> _StreamState:
    state = _StreamState()
    for event in events:
        reduce_event(state, event)
    return state


def _run_one_closed() -> tuple[dict[str, Any], ...]:
    return (*_START, _answer("run 1 answer"), _END)


def test_each_agent_start_opens_a_new_run_with_no_verdict_yet() -> None:
    state = _fold(*_run_one_closed(), *_START)

    assert state.run_open is True
    assert state.saw_agent_end is True, "the latched flag keeps its meaning"
    assert latest_run_closed(state) is False
    assert (state.stop_reason, state.summary) == (None, "run 1 answer"), (
        "the verdict is reset, the summary is not"
    )
    reduce_event(state, _END)
    assert latest_run_closed(state) is True


def test_open_latest_run_is_not_success() -> None:
    """Codex r5 probe 3: run 1 closed, run 2 opened, the stream ends, exit 0."""

    state = _fold(*_run_one_closed(), *_START)

    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=0)

    assert (result.ok, result.status) == (False, "error")
    assert result.error == STREAM_ENDED_EARLY
    assert result.summary == "run 1 answer", "the partial answer is still shown"


def test_an_open_latest_run_with_its_own_answer_is_still_partial() -> None:
    """Run 2 got as far as a ``message_end``: its text is the summary, and without
    its own ``agent_end`` it is a partial answer, as a single cut run always was."""

    state = _fold(*_run_one_closed(), *_START, _answer("run 2 partial"))

    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=0)

    assert (result.ok, result.status, result.summary) == (False, "error", "run 2 partial")


def test_legacy_linger_after_closed_latest_run_stays_success_without_protocol() -> None:
    """ADR-0276 D4: a child that closed its LAST run and then would not exit is a
    finished answer; the exit code is the reaper's. Two runs, both closed."""

    state = _fold(*_run_one_closed(), *_START, _answer("run 2 answer"), _END)

    result = build_result(
        id="c1", profile="p", state=state, outcome="ok", exit_code=-15, lingered=True
    )

    assert (result.ok, result.status, result.note) == (True, "ok", LINGERED_NOTE)
    assert result.summary == "run 2 answer"
    assert result.error is None


def test_a_linger_with_an_open_latest_run_is_unfinished() -> None:
    state = _fold(*_run_one_closed(), *_START)

    result = build_result(
        id="c1", profile="p", state=state, outcome="ok", exit_code=-15, lingered=True
    )

    assert (result.ok, result.status, result.reason) == (False, "error", LINGERED_REASON)
    assert result.note is None


def test_an_auto_retry_that_succeeded_is_ok() -> None:
    """Run 1 ended in a provider error, run 2 answered: the latest run decides."""

    state = _fold(
        *_START, _answer("429 rate limited", "error"), _END, *_START, _answer("fine"), _END
    )

    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=0)

    assert (result.ok, result.status, result.summary) == (True, "ok", "fine")
    assert result.stop_reason == "stop"


def test_a_dropped_line_still_withholds_the_verdict_on_the_terminator() -> None:
    """Unchanged guard: once a line was dropped, the missing terminator may be the
    dropped line, so the run is judged on its exit code alone."""

    state = _fold(*_run_one_closed(), *_START, _answer("run 2 answer"))
    state.dropped_lines = 1

    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=0)

    assert (result.ok, result.status) == (True, "ok")


def test_reduce_line_opens_runs_from_raw_stdout() -> None:
    state = _StreamState()
    for event in (*_run_one_closed(), *_START):
        reduce_line(state, json.dumps(event))

    assert (state.run_open, state.stop_reason) == (True, None)


# === the stop reason is the latest run's (Codex review of #721, finding 3) ==========


def _abort_close_out() -> dict[str, Any]:
    """What the harness emits when an abort ends a run: ``harness/core.py``'s
    abort close-out, a ``turn_end`` with an ``aborted`` message of its own, then
    ``agent_end``. An aborted auto-retry prints it with no ``message_end`` before
    it in the run; a run given a prompt prints the user's first
    (:func:`_user_end`)."""

    message = AssistantMessage(content=[], stop_reason="aborted")
    return {"type": "turn_end", "message": dataclasses.asdict(message), "tool_results": []}


def _silent_answer(text: str) -> dict[str, Any]:
    """An answer whose adapter reported no stop reason."""

    event = _answer(text)
    event["message"]["stop_reason"] = None
    return event


def test_a_run_aborted_after_a_finished_run_that_then_lingers_is_not_success() -> None:
    """Codex's sequence: run 1 answers, run 2 is aborted, the child lingers.

    On ``ed76ea76``: ``ok=True status='ok' stop_reason='stop'`` — run 1's stop
    reason stood for run 2, and the linger rule read it as a finished answer."""

    state = _fold(*_run_one_closed(), *_START, _abort_close_out(), _END)

    result = build_result(
        id="c1", profile="p", state=state, outcome="ok", exit_code=-15, lingered=True
    )

    assert (result.ok, result.status, result.stop_reason) == (False, "error", "aborted")
    assert result.reason == LINGERED_REASON and result.note is None


def test_a_run_aborted_after_an_errored_run_is_aborted_by_its_own_close_out() -> None:
    """The verify round's sequence: run 1 errors, run 2 is aborted, exit 0. On
    ``ed76ea76`` it read ``error`` only because run 1's ``error`` was stale."""

    state = _fold(
        *_START, _answer("429 rate limited", "error"), _END, *_START, _abort_close_out(), _END
    )

    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=0)

    assert (result.ok, result.status, result.stop_reason) == (False, "aborted", "aborted")


def test_a_run_that_answers_without_a_stop_reason_is_not_an_earlier_runs_error() -> None:
    """Codex's third sequence: run 1 errors, run 2 answers with no stop reason. On
    ``ed76ea76``: ``status='error' summary='new'``."""

    state = _fold(
        *_START, _answer("429 rate limited", "error"), _END, *_START, _silent_answer("new"), _END
    )

    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=0)

    assert (result.ok, result.status, result.summary) == (True, "ok", "new")
    assert result.stop_reason is None


def test_within_one_run_an_error_is_not_erased_by_a_message_with_no_stop_reason() -> None:
    """Non-empty precedence is kept INSIDE a run: only ``agent_start`` resets."""

    state = _fold(*_START, _answer("boom", "error"), _silent_answer("after"), _END)

    assert state.stop_reason == "error"
    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=0)
    assert (result.ok, result.status) == (False, "error")


def test_a_retry_cut_off_is_not_explained_by_the_error_it_retried() -> None:
    """Run 1 hit a 429, run 2 — the retry — was cut off before it said anything.
    The reason it did not finish is the cut, not the 429 the retry recovered from
    (ADR-0276 D17): ``final_error`` belongs to the run that reported it too."""

    state = _fold(*_START, _answer("429 rate limited", "error"), _END, *_START)

    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=0)

    assert (result.ok, result.status) == (False, "error")
    assert result.error == STREAM_ENDED_EARLY


def test_an_abort_close_out_after_an_error_in_the_same_run_leaves_no_final_error() -> None:
    """``final_error`` names the LAST assistant message's error (ADR-0276 D17), and
    the close-out's ``aborted`` message, in the run's next turn, is the last one:
    it clears it, as a ``message_end`` would have."""

    state = _fold(
        *_START, _answer("429", "error"), {"type": "turn_start"}, _abort_close_out(), _END
    )

    assert (state.stop_reason, state.final_error, state.error_message) == ("aborted", None, "429")


@pytest.mark.parametrize(
    ("printed", "rewritten", "final_error"),
    [("error", "stop", "refused"), ("stop", "error", None)],
    ids=["error-rewritten-to-stop", "stop-rewritten-to-error"],
)
def test_a_turn_end_after_the_turns_own_message_end_does_not_change_its_verdict(
    printed: str, rewritten: str, final_error: str | None
) -> None:
    """A ``message_end`` hook can rewrite the message the harness keeps, and that is
    the one ``turn_end`` carries; the child's own ``message_end`` is what printed
    the verdict (``test_child_ownership_365.py``'s ``fatal-*`` cases rewrite an
    ``error`` to ``stop`` and must still fail). Only ``aborted`` is taken from such
    a ``turn_end``: no hook rewrite decides the verdict either way."""

    state = _fold(
        *_START,
        _answer("refused", printed),
        {"type": "turn_end", "message": _answer("refused", rewritten)["message"], "tool_results": []},
        _END,
    )

    assert (state.stop_reason, state.final_error) == (printed, final_error)


def test_an_aborted_turn_end_after_a_finished_answer_reads_aborted() -> None:
    """``aborted`` is taken from a ``turn_end`` whatever the turn printed, not only
    after a ``tool_use``: an abort that lands after the answer (a ``turn_end``
    listener) or a ``message_end`` hook that rewrites the answer to ``aborted``
    makes the child exit 1 for that ``aborted``, so the run reads ``aborted``
    (verify round 3 of #721: a rule that let ``aborted`` override only a
    ``tool_use`` verdict passed every other test)."""

    state = _fold(
        *_START,
        _answer("done", "stop"),
        {"type": "turn_end", "message": _answer("done", "aborted")["message"], "tool_results": []},
        _END,
    )

    assert (state.stop_reason, state.final_error) == ("aborted", None)


# === the real close-out's event order (fix round 2 of #721) =========================


def _user_end() -> dict[str, Any]:
    """The user prompt's ``message_end``, which a real child prints at the start of
    every run it was given a prompt for, before anything the model says."""

    return {
        "type": "message_end",
        "message": {"role": "user", "content": [{"type": "text", "text": "go"}]},
    }


def _tool_call_end() -> tuple[dict[str, Any], ...]:
    """An assistant turn that asked for a tool, the tool, and its result: what a real
    child prints before an abort lands in the tool."""

    asked = AssistantMessage(
        content=[ToolCallContent(tool_call_id="tc-1", tool_name="stall", input={})],
        stop_reason="tool_use",  # type: ignore[arg-type]
    )
    return (
        {"type": "message_end", "message": dataclasses.asdict(asked)},
        {"type": "tool_execution_start", "tool_call_id": "tc-1", "tool_name": "stall"},
        {"type": "tool_execution_end", "tool_call_id": "tc-1", "tool_name": "stall"},
        {"type": "message_end", "message": {"role": "toolResult", "content": []}},
    )


def test_a_real_abort_close_out_is_read_past_its_runs_user_message_end() -> None:
    """A real print-mode child aborted mid-stream (the verify round's ``child.py``,
    ``ok-then-abort-stream``) prints run 2 as ``agent_start, turn_start,
    message_end(user), turn_end(aborted), agent_end``. Only an ASSISTANT
    ``message_end`` reports the turn: counted for the user's, the close-out's
    ``turn_end`` is ignored and the run reads ``error`` with no stop reason
    (the verify round's mutant R17)."""

    state = _fold(*_run_one_closed(), *_START, _user_end(), _abort_close_out(), _END)

    assert (state.stop_reason, state.turn_reported) == ("aborted", False)
    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=1)
    assert (result.ok, result.status, result.stop_reason) == (False, "aborted", "aborted")
    assert result.reason == REASON_OWN_ABORT


def test_an_abort_during_a_tool_reads_aborted_after_the_turns_own_message_end() -> None:
    """The real child's ``ok-then-abort-tool``: run 2's assistant ``message_end``
    says ``tool_use``, the abort lands in the tool, and the close-out's
    ``turn_end(aborted)`` follows the tool result. The child exits 1 for that
    ``aborted``. On ``ed76ea76`` and on ``93522182`` this read ``status='error'
    stop_reason='tool_use'`` with ``error='exited with code 1'``; an abort is
    ``aborted`` wherever in the turn it lands."""

    state = _fold(
        *_run_one_closed(), *_START, _user_end(), *_tool_call_end(), _abort_close_out(), _END
    )

    assert state.stop_reason == "aborted"
    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=1)
    assert (result.ok, result.status, result.stop_reason) == (False, "aborted", "aborted")
    assert (result.reason, result.error) == (REASON_OWN_ABORT, None)
    assert result.summary == "run 1 answer", "the partial answer is still shown"


def test_an_aborted_auto_retry_keeps_the_error_it_retried_in_details_only() -> None:
    """The real child's ``err-then-abort-stream``: run 1 ends in a 429, the
    retry is aborted. On ``ed76ea76`` the 429 was still the run's final error, so
    it read ``error`` with the 429 as its summary. The retry's verdict is its own
    now, and ADR-0276 D17 keeps an error the harness went on from out of the
    summary and the error: it is in ``details``, as any stale error is."""

    limited = AssistantMessage(
        content=[], stop_reason="error", error_message="429 rate limited"  # type: ignore[arg-type]
    )
    state = _fold(
        *_START, _user_end(), {"type": "message_end", "message": dataclasses.asdict(limited)}, _END,
        *_START, _abort_close_out(), _END,
    )

    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=1)

    assert (result.ok, result.status, result.summary) == (False, "aborted", NO_OUTPUT)
    assert result.error is None
    assert "429 rate limited" in (result.details or "")


@pytest.mark.parametrize(
    "before",
    ["after-the-users-message_end", "in-the-turn-after-an-answered-one"],
)
def test_a_turn_end_of_a_turn_that_printed_no_answer_gives_its_verdict(before: str) -> None:
    """A turn that printed no ASSISTANT ``message_end`` takes any verdict, and its
    error message, from its ``turn_end``. Synthetic: in the product only the abort
    close-out ends a turn that way, and it says ``aborted``, which is read
    wherever it lands; every other ``turn_end`` follows its turn's assistant
    ``message_end`` (``loop.py:260``, ``:283``; the hook-fail close-out,
    ``harness/core.py:6213-6217``). So this pins the reducer's rule and is
    what the verify round's mutants R15 (``turn_start`` keeps the flag), R17 (a
    user ``message_end`` sets it) and R19 (no error message) break."""

    errored = AssistantMessage(
        content=[], stop_reason="error", error_message="boom"  # type: ignore[arg-type]
    )
    turn_end = {"type": "turn_end", "message": dataclasses.asdict(errored), "tool_results": []}
    if before == "after-the-users-message_end":
        prefix: tuple[dict[str, Any], ...] = (*_START, _user_end())
    else:
        prefix = (*_START, *_tool_call_end(), {"type": "turn_start"})

    state = _fold(*prefix, turn_end, _END)

    assert (state.stop_reason, state.final_error) == ("error", "boom")


_USER_END = (
    'emit({"type": "message_end", "message": {"role": "user", "content": '
    '[{"type": "text", "text": "go"}]}})'
)
_TOOL_TURN = (
    'emit({"type": "message_end", "message": {"role": "assistant", "content": '
    '[{"type": "tool_call", "tool_call_id": "tc-1", "tool_name": "stall", "input": {}}], '
    '"stop_reason": "tool_use", "provider": "stub", "model": "stub-1"}})\n'
    'emit({"type": "tool_execution_start", "tool_call_id": "tc-1", "tool_name": "stall"})\n'
    'emit({"type": "tool_execution_end", "tool_call_id": "tc-1", "tool_name": "stall"})\n'
    'emit({"type": "message_end", "message": {"role": "toolResult", "content": []}})'
)
_ABORTED_TURN_END = (
    'emit({"type": "turn_end", "message": {"role": "assistant", "content": [], '
    '"stop_reason": "aborted"}, "tool_results": []})'
)
_ERRORED = (
    'emit({"type": "message_end", "message": {"role": "assistant", "content": [], '
    '"stop_reason": "error", "error_message": "429", "provider": "stub", "model": "stub-1"}})'
)
_SILENT = (
    'emit({"type": "message_end", "message": {"role": "assistant", "content": '
    '[{"type": "text", "text": "new"}], "stop_reason": None, "provider": "stub", '
    '"model": "stub-1"}})'
)
_AGAIN = 'emit({"type": "agent_start"})\nemit({"type": "turn_start"})'
_CLOSE_OUT = f"{_USER_END}\n{_ABORTED_TURN_END}"
_LINGER = "os.close(1)\nos.close(2)\nfor _ in range(2400):\n    time.sleep(0.05)"


@pytest.mark.parametrize(
    ("body", "lingers", "expected"),
    [
        pytest.param(
            f'start()\nsay("old answer")\ndone()\n{_AGAIN}\n{_CLOSE_OUT}\ndone()\n{_LINGER}',
            True,
            (False, "error", "aborted"),
            id="run1-answers_run2-aborted_lingers",
            # The post-EOF linger shape is POSIX-only here, as in
            # ``test_print_channel_spawn.py``'s two cases that use it: on
            # windows-latest this case did not finish within its 30 s bound on
            # any of 3.11-3.14 (CI run 42410160294), and that path has not been
            # measured on Windows.
            marks=pytest.mark.skipif(
                sys.platform == "win32",
                reason="the post-EOF linger path is not measured on win32",
            ),
        ),
        pytest.param(
            f"start()\n{_ERRORED}\ndone()\n{_AGAIN}\n{_CLOSE_OUT}\ndone()",
            False,
            (False, "aborted", "aborted"),
            id="run1-errors_run2-aborted_exit0",
        ),
        pytest.param(
            f'start()\nsay("old answer")\ndone()\n{_AGAIN}\n{_CLOSE_OUT}\ndone()\nsys.exit(1)',
            False,
            (False, "aborted", "aborted"),
            id="run1-answers_run2-aborted-in-stream_exit1",
        ),
        pytest.param(
            f'start()\nsay("old answer")\ndone()\n{_AGAIN}\n{_USER_END}\n{_TOOL_TURN}\n'
            f"{_ABORTED_TURN_END}\ndone()\nsys.exit(1)",
            False,
            (False, "aborted", "aborted"),
            id="run1-answers_run2-aborted-in-a-tool_exit1",
        ),
        pytest.param(
            f"start()\n{_ERRORED}\ndone()\n{_AGAIN}\n{_SILENT}\ndone()",
            False,
            (True, "ok", None),
            id="run1-errors_run2-answers-without-stop-reason_exit0",
        ),
    ],
)
async def test_through_the_print_channel_the_stop_reason_is_the_latest_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    lingers: bool,
    expected: tuple[bool, str, str | None],
) -> None:
    """The sequences above, from a stub child process through the real channel,
    each run 2 aborted in the event order a real print-mode child prints (its
    user ``message_end`` first). On ``ed76ea76``, and the same on main
    ``fc8f3048``, ``run1-answers_run2-aborted_lingers`` came back ``(True, 'ok',
    'end_turn')``, and ``run1-errors_run2-aborted_exit0`` and
    ``run1-errors_run2-answers-without-stop-reason_exit0`` came back ``(False,
    'error', 'error')``. Of the two that exit 1, as a real aborted child does,
    the one aborted in stream came back ``(False, 'error', 'end_turn')`` there,
    and the one aborted in a tool ``(False, 'error', 'tool_use')`` there and on
    ``93522182`` too."""

    monkeypatch.setattr(pc, "POST_EOF_EXIT_BOUND_SECONDS", 0.3)

    result = await asyncio.wait_for(
        _stub_channel(_stub(body), grace=0.5).run(_plan(tmp_path)), 30
    )

    assert (result.ok, result.status, result.stop_reason) == expected, result
    if lingers:
        assert result.exit_code != 0 and result.reason == LINGERED_REASON
    else:
        assert result.exit_code == (1 if "sys.exit(1)" in body else 0)


async def test_through_the_print_channel_a_child_cut_inside_its_second_run_is_an_error(
    tmp_path: Path,
) -> None:
    """The real channel and a real child process: run 1 closes, run 2 opens, and
    the child exits 0 without run 2's terminator. On main this came back ``ok``."""

    child = _stub(
        """
        start()
        say("run 1 answer", input=1, output=1)
        done()
        emit({"type": "agent_start"})
        emit({"type": "turn_start"})
        """
    )

    result = await asyncio.wait_for(_stub_channel(child, grace=0.5).run(_plan(tmp_path)), 30)

    assert result.exit_code == 0
    assert (result.ok, result.status) == (False, "error")
    assert result.error == STREAM_ENDED_EARLY


# === #611 S1c — the versioned ``print_done`` (contract §2.4, Codex r5 finding 6) ===
#
# A run the parent asked for the line (``AELIX_JOBS_PROTOCOL=1`` — today only a
# background job's) needs it before a linger counts as finished; a legacy run is
# judged as above whatever it printed. Fails on ``6caaae09``: ``build_result``
# takes no ``print_done_required`` and the reducer ignores the line.

_PRINT_DONE: dict[str, Any] = {"type": "aelix_print_done"}


def test_protocol_run_lingering_without_print_done_fails() -> None:
    """The latest run closed, then the child wedged BEFORE its print run drained: not finished."""

    state = _fold(*_run_one_closed())

    result = build_result(
        id="c1",
        profile="p",
        state=state,
        outcome="ok",
        exit_code=-15,
        lingered=True,
        print_done_required=True,
    )

    assert (result.ok, result.status, result.reason) == (False, "error", LINGERED_REASON)
    assert result.note is None


def test_protocol_run_lingering_after_print_done_is_finished() -> None:
    state = _fold(*_run_one_closed(), _PRINT_DONE)

    assert state.print_done is True
    result = build_result(
        id="c1",
        profile="p",
        state=state,
        outcome="ok",
        exit_code=-15,
        lingered=True,
        print_done_required=True,
    )

    assert (result.ok, result.status, result.note) == (True, "ok", LINGERED_NOTE)


def test_legacy_linger_is_judged_as_before_with_or_without_the_line() -> None:
    """Finding 6 closed: the ungated linger success never depends on the gated line."""

    for events in ((*_run_one_closed(),), (*_run_one_closed(), _PRINT_DONE)):
        result = build_result(
            id="c1", profile="p", state=_fold(*events), outcome="ok", exit_code=-15, lingered=True
        )
        assert (result.ok, result.status, result.note) == (True, "ok", LINGERED_NOTE)


def test_a_protocol_run_that_exits_on_its_own_does_not_need_the_line() -> None:
    """``print_done`` decides only a linger: a child that exited 0 is judged by its stream."""

    result = build_result(
        id="c1",
        profile="p",
        state=_fold(*_run_one_closed()),
        outcome="ok",
        exit_code=0,
        print_done_required=True,
    )

    assert (result.ok, result.status) == (True, "ok")


def test_reduce_line_reads_the_print_done_line_from_raw_stdout() -> None:
    state = _fold(*_run_one_closed())
    reduce_line(state, json.dumps(_PRINT_DONE))
    assert state.print_done is True


def test_an_earlier_runs_print_done_never_vouches_for_a_later_run() -> None:
    """Codex #11: a later ``agent_start`` takes the marker back; the later run lingered without one.

    Fails on ``b387b4b5``: ``print_done=True status=ok ok=True``.
    """

    state = _fold(*_run_one_closed(), _PRINT_DONE, *_START, _answer("run 2 answer"), _END)
    assert state.print_done is False
    result = build_result(
        id="c1",
        profile="p",
        state=state,
        outcome="ok",
        exit_code=-15,
        lingered=True,
        print_done_required=True,
    )
    assert (result.ok, result.status, result.reason) == (False, "error", LINGERED_REASON)


@pytest.mark.parametrize(
    "line",
    [
        {"type": "aelix_print_done", "v": 999, "unexpected": True},
        {"type": "aelix_print_done", "v": 1},
    ],
    ids=["unknown-keys", "a-version-field"],
)
def test_a_print_done_of_another_shape_is_not_the_marker(line: dict[str, Any]) -> None:
    """Codex #11: only the producer's exact line counts, and a bad one takes a good one back.

    Fails on ``b387b4b5``: ``accepted=True``.
    """

    assert _fold(*_run_one_closed(), line).print_done is False
    assert _fold(*_run_one_closed(), _PRINT_DONE, line).print_done is False


def test_a_print_done_inside_an_open_run_is_not_the_marker() -> None:
    """The producer writes it only after the run drained: one before ``agent_end`` breaks the order."""

    assert _fold(*_START, _PRINT_DONE).print_done is False
    assert _fold(_PRINT_DONE).print_done is False
    assert _fold(*_START, _PRINT_DONE, _answer("x"), _END).print_done is False


def test_a_print_done_inside_a_later_run_is_not_the_marker() -> None:
    """An earlier run's ``agent_end`` is latched: a line written inside the run
    after it is still inside an open run, and does not vouch for that run once
    it closes and lingers.

    A guard on ``fa742f77``: kills verify-r2's P03 (dropping ``not
    state.run_open`` from the check), under which the second run lingers
    "finished" on a line it wrote before its own ``agent_end``.
    """

    state = _fold(*_run_one_closed(), *_START, _PRINT_DONE)
    assert state.print_done is False
    state = _fold(*_run_one_closed(), *_START, _PRINT_DONE, _answer("run 2 answer"), _END)
    assert state.print_done is False
    result = build_result(
        id="c1",
        profile="p",
        state=state,
        outcome="ok",
        exit_code=-15,
        lingered=True,
        print_done_required=True,
    )
    assert (result.ok, result.status, result.reason) == (False, "error", LINGERED_REASON)
