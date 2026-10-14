"""Sprint 3 cooperative-abort — AbortSignal unit tests + bash exec abort tests.

Covers:
- :class:`~aelix_coding_agent.tools._abort.AbortSignal` API contract.
- :meth:`~aelix_coding_agent.tools.bash._LocalBashOperations.exec` with
  signal abort: the call returns within 15 s of the abort with exit_code None,
  and the process-group case's child is gone or a zombie after it.
- :meth:`~aelix_coding_agent.tools.bash._LocalBashOperations.exec` with
  asyncio.CancelledError: CancelledError propagates (is NOT swallowed).
- The same, for a cancellation that lands in the WATCHER TEARDOWN rather than
  in ``_wait`` — the window #234 closed, plus the explicit retrieval that keeps
  a watcher's own failure out of the loop's GC report.

That an abort or a cancel kills the command's whole tree is asserted against
real trees in ``test_bash_tool_containment.py``, not here (see below).

Since #222 the kill at both legs is a :class:`~aelix_ai.utils._process_tree.ProcessTree`
ladder rather than the pid-only ``_kill_group`` these cases were written
against (that one-line delegate is deleted). The cases below are unchanged
because their SUBJECT is unchanged — the abort ends the call with the right
label, and in the process-group case the child it handed the command to; what
the ladder buys beyond the pid is asserted in ``test_bash_tool_containment.py``.

THE BOUNDS ON A REAL SHELL (#471). The eight bash cases from
``test_bash_exec_signal_abort_kills_child`` to
``test_bash_exec_cancel_watcher_teardown_catches_exception`` run one (``pwsh``
on windows-latest) under :class:`tests.rpc._bash_phases.BashPhases`, #449's
phase instrumentation: a bound that expires fails with the call's timeline and
names its slowest phase, and a call slower than 2 s warns with it. Each of
their bounds is one of two kinds and says which:

* A HANG GUARD, :data:`~tests.rpc._bash_phases.HANG_GUARD_SECONDS` (30 s), where
  the case asserts a result, a ``CancelledError`` or "no exception" and nothing
  about time: an ordinary call, a cancelled one, the abort 0.2 s into ``true``,
  and the wait for a command to start before it is aborted or cancelled. The
  number only has to stay out of a working call's reach.
* A VERDICT bound where the time is the property: an aborted call must return
  within :data:`~tests.rpc._bash_phases.ABORT_ENDS_CALL_SECONDS` (15 s) of the
  ABORT. ``exit_code is None`` comes from ``_signal_aborted``, so it holds when
  the kill does nothing, and an abort that kills nothing leaves the command to
  run out its ``sleep 30`` and the call to return ~30 s later, which this bound
  fails.

What the verdict checks is that the CALL RETURNED, not that the command ended.
An abort that kills only the shell returns at once with ``exit_code=None`` and
passes it while the command keeps running wherever the shell forked it
(review round 2, darwin ``/bin/bash`` 3.2, which forks the last command of
``echo started; sleep 30``: both aborted cases passed, and that ``sleep 30``
outlived the first). That the tree died is what
``test_bash_tool_containment.py``'s
``test_the_abort_signal_ends_a_tree_whose_middle_parent_already_exited``
checks, and it failed under that kill. The process-group case here looks at its
pid-file child too, but on POSIX the shell execs that single command, so the
child IS the shell and a shell-only kill ends it.

THE TRADE THE 15 s VERDICT MAKES, decided: an abort honoured up to ~15 s late
now passes here, with a warning that names the slow phase, where the 5 s bound
it replaced failed it. Measured on darwin: an abort whose kill started 12 s
late passed both aborted cases here (a 12.007 s warning naming the kill) and
failed both in the 529b997e file (review rounds 1 and 2); one that waited 9 s
for a SIGINT first passed 6 runs in 6 here and failed 5 in 6 there (round 1).
The reason is that a working abort on windows-latest needed more than 5 s
once: 5.17 s, 5.08 s of it inside the kill ladder, whose ``taskkill.exe`` runs
under a 5 s timeout (run 38508575549, py3.11 under load). The ladder's bounded
parts alone add up to 6.35 s on win32, so a verdict under that fails working
aborts on a slow runner, which is the flake #471 removes. Lateness is still
bounded elsewhere: the containment case above asserts at most 2.5 s from the
abort to the return (7.5 s on win32), and it failed the 9 s mutant 3 times in
3 (round 1) and the 12 s one (round 2).

TWO CASES RUN REAL SHELLS OUTSIDE BashPhases, deliberately:
``test_a_turn_cancel_landing_in_the_watcher_teardown_is_not_swallowed`` (three
arms, one call each) and
``test_a_signal_that_blows_up_while_being_cancelled_leaves_nothing_for_the_gc``
(three calls in one case). #471 left their 60 s bounds as they were, twice the
hang guard, over ``echo ok`` and over a python sleeper under a 0.3 s timeout.
What they assert is which branch a cancellation takes inside the watcher
teardown and what reaches the loop's GC handler, not how long a call takes.
BashPhases also stamps only a case's FIRST spawn, so the GC case's second and
third calls would carry no stamps. A stall past 60 s there fails without a
timeline: "[arm] exec did not return within 60s" in the teardown case, and in
the GC case "arm 2: exec did not return within 60s" or, for arms 1 and 3, a
bare ``TimeoutError``.

What the bounds are sized from, measured on windows-latest by #449: a bare
``pwsh -NoProfile`` once took 5.97 s from process creation to its first
statement, and one ``proc.wait`` returned 2.18 s after an ordinary exit (run
38439411854). Product calls there had a median of 0.38-0.65 s and a max of
2.70 s over 3000. An abort landing after the first output returned in a median
0.063 s, p99 0.381 s, max 0.547 s (run 38453296707, 900 calls). So 30 s is 5x
the worst stall and 11x the slowest call, and 15 s covers the kill ladder's
bounded parts on win32 (6.35 s) plus one 5.97 s stall, at half a non-killing
abort's ~30 s. The 5 s bounds these replaced failed twice on windows-latest with
a bare ``TimeoutError`` from inside ``proc.wait`` (job 115589770821, py3.13;
run 37942222504, py3.11).

This file's own calls on windows-latest, before #471's review round 2 changed
the helper's start wait and expiry stamp (py3.11-3.13, idle and under three
busy loops; no case failed in the plain rounds of any of the three runs):

* Run 38502661220: 40 rounds of the 529b997e file and of a pre-final version
  of this one that polled the pid file every 0.01 s, 1920 case records each.
  The shell's start (wait entered to first output): a median 0.38 s both, max
  2.48 s (529b997e) and 1.55 s (this one). An ordinary call: at most 2.66 s
  and 1.66 s. Abort of a running command to return: at most 0.38 s and
  0.44 s. Cancel to return: at most 0.38 s here; the 529b997e file stamps no
  cancel, and from its kill's start it was 0.95 s at most.
* Run 38505018889: this file as first committed for #471, the same matrix,
  1920 records. The start: at most 1.43 s. Abort to return: at most 0.43 s.
  Cancel to return: at most 0.45 s.
* Run 38508575549: the same version, 30 rounds per job, 1440 records. The
  start: at most 3.71 s. An ordinary call: at most 3.58 s. Cancel to return:
  at most 1.28 s. Abort to return: at most 5.17 s, the case above.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import os
import pathlib
import re
import shlex
import sys
import time
import types
import warnings
import weakref
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.tools._abort import AbortSignal
from aelix_coding_agent.tools.bash import ExecExitResult, create_local_bash_operations

from tests import event_waits
from tests.process_probe import STATE_GONE, STATE_ZOMBIE, await_dead_or_zombie
from tests.rpc import _bash_phases
from tests.rpc._bash_phases import ABORT_ENDS_CALL_SECONDS, HANG_GUARD_SECONDS, BashPhases


@pytest.fixture
def phases(monkeypatch: pytest.MonkeyPatch) -> BashPhases:
    """#449's stamps on the bash module, for a case that runs a real shell (#471)."""

    return BashPhases().install(monkeypatch)


# ---------------------------------------------------------------------------
# AbortSignal unit tests
# ---------------------------------------------------------------------------


async def test_abort_signal_initial_state() -> None:
    sig = AbortSignal()
    assert sig.aborted is False


async def test_abort_signal_after_abort() -> None:
    sig = AbortSignal()
    sig.abort()
    assert sig.aborted is True


async def test_abort_signal_abort_is_idempotent() -> None:
    sig = AbortSignal()
    sig.abort()
    sig.abort()
    assert sig.aborted is True


async def test_abort_signal_wait_returns_after_abort() -> None:
    sig = AbortSignal()
    sig.abort()
    # Already aborted — wait() must return immediately without blocking.
    await asyncio.wait_for(sig.wait(), timeout=1.0)
    assert sig.aborted is True


async def test_abort_signal_wait_woken_by_abort() -> None:
    # ORDERING, NOT TIME (#330): park the waiter, then wake it — not a 0.05 s timer.
    sig = AbortSignal()
    waiter = asyncio.ensure_future(sig.wait())
    await asyncio.sleep(0)
    assert not waiter.done(), "wait() returned before anything aborted the signal"
    sig.abort()
    await asyncio.wait_for(waiter, timeout=2.0)
    assert sig.aborted is True


# ---------------------------------------------------------------------------
# bash exec — signal abort path
# ---------------------------------------------------------------------------


async def test_bash_exec_signal_abort_kills_child(tmp_path: Path, phases: BashPhases) -> None:
    """Aborting via AbortSignal kills the subprocess group and returns exit_code=None.

    What this case checks is the label and the call's return within 15 s of
    the abort. That the group died is checked by test_bash_tool_containment.py
    (the module docstring says which case, and why the return cannot show it).
    """

    ops = create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    async def _exec_task() -> ExecExitResult:
        return await ops.exec(
            _STARTED_THEN_SLEEPS,
            str(tmp_path),
            on_data=chunks.append,
            signal=sig,
        )

    phases.stamp("call")
    task = asyncio.create_task(_exec_task())

    # The command is running, observed; then abort.
    await _await_started(chunks, phases, task)
    phases.stamp("abort")
    sig.abort()

    # VERDICT, from the abort (#471): see _WHY_THE_ABORT_HAS_A_VERDICT.
    result = await phases.guard(
        task,
        f"exec after its abort signal ({_WHY_THE_ABORT_HAS_A_VERDICT})",
        bound=ABORT_ENDS_CALL_SECONDS,
        kind="abort-to-return bound",
    )
    assert result.exit_code is None, "Aborted exec must return exit_code=None"


async def test_bash_exec_signal_abort_kills_process_group(
    tmp_path: Path, phases: BashPhases
) -> None:
    """Signal abort kills the process the shell handed the command to.

    Strategy: the shell runs a Python child that writes its own PID to a file
    and then sleeps.  After sig.abort() we verify that child is dead or zombie.
    Measured (POSIX): bash execs the single simple command, so the recorded
    PID IS the direct child and the pgid leader — the old ``sh -c 'echo $$ …'``
    form had the same shape.  What this pins is that the abort reaches the
    process holding the command, through the watcher's ``_end_the_tree`` ladder
    (``_kill_group`` until #222); that the ladder reaches a DESCENDANT whose own
    parent has already exited is asserted in test_bash_tool_containment.py, by
    ``test_the_abort_signal_ends_a_tree_whose_middle_parent_already_exited``.

    The child is ``sys.executable`` rather than ``sh -c 'echo $$ …'`` because
    ``exec`` hands ``command`` to the resolved shell, which is pwsh on Windows
    — no ``sh`` and no ``$$``.  The two shells quote a command differently:
    pwsh needs the call operator to run a quoted path (``& "C:/…/python.exe"``),
    bash needs ``shlex.quote``; the script and pid-file arguments are
    double-quoted, which both accept.
    """
    import tempfile

    pid_file = tempfile.mktemp(suffix=".pid")  # noqa: S306 — test-only
    interpreter = (
        f'& "{sys.executable}"' if sys.platform == "win32" else shlex.quote(sys.executable)
    )
    # The pid reaches the test WHOLE, from both ends (#406, the race that made
    # tests/agents_ext/test_print_channel_spawn.py flaky): ``open(...).write``
    # created the empty file before the digits, and ``exists()`` was true of it.
    # The child runs :data:`_ANNOUNCE_OWN_PID` and the test waits for
    # :func:`_whole_pid`, under the start's hang guard; both are pinned below.
    command = (
        f'{interpreter} -c "import os, pathlib, sys, time; {_ANNOUNCE_OWN_PID}; '
        f'time.sleep(30)" "{pid_file}"'
    )

    ops = create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    phases.stamp("call")
    task = asyncio.create_task(
        ops.exec(command, str(tmp_path), on_data=chunks.append, signal=sig)
    )

    # Wait for a whole pid in the pid_file (child is up and running). A HANG
    # GUARD that names what it waited for (#330) and, since #471, the shell's
    # phases: it holds pwsh's start-up and then python's, and was a 10 s bound
    # over a pwsh start #449 measured at up to 5.97 s. Polled every 0.05 s, as
    # #406 did: polled every 0.01 s on windows-latest, one open of the file
    # just renamed into place failed with PermissionError (1 of 258 calls, run
    # 38502661220), and none of 258 at 0.05 s. A call that returns before the
    # file is whole (the interpreter did not start) fails the wait at once,
    # with the shell's output.
    try:
        await phases.shell_started(
            "the child to write its PID file (spawn failed?)",
            until=lambda: _whole_pid(pid_file) is not None,
            detail=lambda: f"output so far: {b''.join(chunks)!r}",
            interval=0.05,
            call=task,
        )
        child_pid = _whole_pid(pid_file)
        assert child_pid is not None
    finally:
        for leftover in (pid_file, pid_file + ".tmp"):
            with contextlib.suppress(OSError):
                os.unlink(leftover)

    # Fire the abort signal — must kill the process group. VERDICT, from the
    # abort (#471): see _WHY_THE_ABORT_HAS_A_VERDICT.
    phases.stamp("abort")
    sig.abort()
    result = await phases.guard(
        task,
        f"exec after its abort signal ({_WHY_THE_ABORT_HAS_A_VERDICT})",
        bound=ABORT_ENDS_CALL_SECONDS,
        kind="abort-to-return bound",
    )
    assert result.exit_code is None

    # Poll until the child is gone or a zombie.
    #
    # #203 — this poll used to read the ABSENCE of ``/proc/<pid>/status`` as
    # "fully reaped, definitely dead".  macOS and Windows have no procfs, so
    # off Linux it took that branch on its first iteration and asserted
    # nothing.  ``await_dead_or_zombie`` asks a real question per platform
    # (``os.kill(pid, 0)`` on POSIX, ``OpenProcess``/``GetExitCodeProcess`` on
    # win32 — where signal 0 is a kill, not a probe) and resolves "cannot
    # tell" to ALIVE, so a child that outlived the abort fails below on every
    # platform.
    last_state = await await_dead_or_zombie(child_pid, timeout=3.0)

    assert last_state in (STATE_GONE, STATE_ZOMBIE), (
        f"Child process {child_pid} still in state '{last_state}' "
        f"after signal abort (expected {STATE_GONE}/{STATE_ZOMBIE} — "
        f"group kill failed?)"
    )


#: How :func:`test_bash_exec_signal_abort_kills_process_group`'s child hands the
#: test its pid (#406): a temp sibling in the same directory, then ``os.replace``,
#: so the name appears only once it holds the whole line. ``chr(10)``, not a
#: backslash escape, and no double quote, because it is spliced into a ``-c "..."``
#: that passes through bash AND pwsh. It expects ``os``, ``pathlib`` and ``sys``.
_ANNOUNCE_OWN_PID = (
    "p = sys.argv[1]; "
    "pathlib.Path(p + '.tmp').write_text(str(os.getpid()) + chr(10)); "
    "os.replace(p + '.tmp', p)"
)


def _whole_pid(pid_file: str) -> int | None:
    """The pid in ``pid_file``, or ``None`` while it is absent or not whole yet."""

    try:
        with open(pid_file, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return None
    return int(text) if re.fullmatch(r"[0-9]+\n", text) else None


def test_the_abort_cases_pid_reaches_the_test_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves of the abort case's pid handover, each on its own (#406 review
    round 2). With both in place each hides the other's regression: the case
    itself stays green with a reader that takes ``'4'`` or a child back to a plain
    ``open().write`` — so the reader's verdicts and the writer's one atomic
    ``os.replace`` are pinned here, in-process, with no race and on every platform.
    """

    pid_file = str(tmp_path / "child.pid")
    assert _whole_pid(pid_file) is None, "absent"
    for partial in ("", "4", "42", "\n", "x42\n"):
        Path(pid_file).write_text(partial, encoding="utf-8")
        assert _whole_pid(pid_file) is None, repr(partial)
    Path(pid_file).unlink()

    seen: list[tuple[bool, bool, str]] = []
    real_replace = os.replace

    def _spy(src: str, dst: str) -> None:
        seen.append(
            (
                Path(src).parent == Path(dst).parent,
                Path(dst).exists(),
                Path(src).read_text(encoding="utf-8"),
            )
        )
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _spy)
    monkeypatch.setattr(sys, "argv", ["-c", pid_file])
    exec(_ANNOUNCE_OWN_PID, {"os": os, "pathlib": pathlib, "sys": sys})
    monkeypatch.undo()

    assert seen == [(True, False, f"{os.getpid()}\n")], seen
    assert _whole_pid(pid_file) == os.getpid()
    assert [p.name for p in tmp_path.iterdir()] == ["child.pid"], "a temp file was left"


#: A command that says it is running and then outlives the case using it. ``echo``
#: and ``sleep`` are spelled the same in bash and in pwsh (the resolved shell on
#: the windows leg, where they are aliases of Write-Output and Start-Sleep), so
#: one string serves both. The ``started`` line is the EVENT the abort and cancel
#: cases wait for (#330): they used to ``sleep(0.1)`` and bet that the shell had
#: spawned by then, which a loaded windows runner does not honour — and a signal
#: or cancel that lands before the spawn tests a different path than the case
#: names, silently. Defined after its first user on purpose: moving it above
#: would shift the lines ``test_bash_tool_containment.py`` cites in the
#: process-group case's docstring.
_STARTED_THEN_SLEEPS = "echo started; sleep 30"

#: Why an aborted case's bound is a VERDICT and not a hang guard, for its
#: failure message (#471, #449's rule). ``exit_code is None`` comes from
#: ``_signal_aborted`` and holds when the kill reached nothing; the command then
#: runs out its own 30 s sleep and the call returns ~30 s after the abort, twice
#: :data:`~tests.rpc._bash_phases.ABORT_ENDS_CALL_SECONDS`, while a working abort
#: returned in at most 0.547 s on windows-latest (#449, 900 calls). The bound
#: checks the RETURN, not that the command ended: see the module docstring.
_WHY_THE_ABORT_HAS_A_VERDICT = (
    "an abort that kills nothing returns ~30 s after it, when the command's own sleep ends"
)


async def _await_started(
    chunks: list[bytes], phases: BashPhases, task: asyncio.Task[ExecExitResult]
) -> None:
    """Block until the command's ``started`` line has reached ``on_data``.

    A HANG GUARD with the call's phases (#471). It holds the shell's whole
    start-up, which #449 measured at up to 5.97 s for a bare pwsh on
    windows-latest, under what was a 10 s bound that could only say ``output so
    far: b''``. A start slower than 2 s warns with the timeline: it is spent
    before the abort's or the cancel's own bound starts, and would otherwise
    say nothing. ``task`` is the call: if it returns before the line arrives,
    the wait fails at once and says how it returned.
    """

    await phases.shell_started(
        "the command's 'started' line to reach on_data",
        until=lambda: b"started" in b"".join(chunks),
        detail=lambda: f"output so far: {b''.join(chunks)!r}",
        call=task,
    )


async def _await_the_cancelled_call(
    task: asyncio.Task[ExecExitResult], phases: BashPhases
) -> None:
    """Wait for an ``exec`` whose task was cancelled to come back, any way it does.

    A HANG GUARD (#471), not a verdict: the cases assert the
    ``CancelledError`` itself, and the time cannot decide it. The drain after
    the cancel leg's kill ends on 0.1 s of silence and is capped at
    ``KILL_DRAIN_SECONDS`` (1.0 s), so a cancel whose kill reached nothing
    still comes back with its ``CancelledError``: 0.11 s on darwin with that
    kill deleted, the command left running and its 30 s spent in the loop's
    teardown instead. ``test_bash_tool_containment.py``'s cancel cases are the
    ones that fail then. A cancellation that is swallowed comes back with a
    result, or not before the guard. Through ``asyncio.wait``, which never
    raises the task's outcome: the case reads it afterwards, and a slow return
    still warns with the timeline (``guard`` reports only one that returns).
    """

    await phases.guard(
        asyncio.wait([task]),
        "the cancelled exec to come back",
        bound=HANG_GUARD_SECONDS,
    )


# ---------------------------------------------------------------------------
# bash exec — CancelledError path
# ---------------------------------------------------------------------------


async def test_bash_exec_cancel_propagates_cancelled_error(
    tmp_path: Path, phases: BashPhases
) -> None:
    """Cancelling the exec task must NOT swallow CancelledError."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    phases.stamp("call")
    task = asyncio.create_task(
        ops.exec(_STARTED_THEN_SLEEPS, str(tmp_path), on_data=chunks.append)
    )

    await _await_started(chunks, phases, task)
    phases.stamp("cancel")
    task.cancel()

    await _await_the_cancelled_call(task, phases)
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_bash_exec_cancel_returns_none_exit_code(tmp_path: Path, phases: BashPhases) -> None:
    """After cancel the task result (if collected) should have exit_code=None."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    phases.stamp("call")
    task = asyncio.create_task(
        ops.exec(_STARTED_THEN_SLEEPS, str(tmp_path), on_data=chunks.append)
    )

    await _await_started(chunks, phases, task)
    phases.stamp("cancel")
    task.cancel()

    await _await_the_cancelled_call(task, phases)
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_bash_exec_normal_path_unaffected(tmp_path: Path, phases: BashPhases) -> None:
    """The normal exec path (no signal, no cancel) must be byte-identical to before."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    # A HANG GUARD (#471), not a budget: this case asserts the output and the
    # exit code, nothing about time. Its 5 s failed on windows-latest inside
    # proc.wait (job 115589770821); 30 s is 5x the worst pwsh start #449
    # measured there (5.97 s) and 11x its slowest product call (2.70 s).
    result = await phases.guard(
        ops.exec("echo aelix-abort-test", str(tmp_path), on_data=chunks.append),
        "exec 'echo aelix-abort-test'",
        bound=HANG_GUARD_SECONDS,
    )
    output = b"".join(chunks).decode()
    assert result.exit_code == 0
    assert "aelix-abort-test" in output


async def test_bash_exec_no_signal_no_watcher(tmp_path: Path, phases: BashPhases) -> None:
    """Passing signal=None must not start a watcher — normal path unchanged."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    # A HANG GUARD (#471), sized as the case above: its 5 s failed the same way
    # (run 37942222504, windows py3.11).
    result = await phases.guard(
        ops.exec("echo ok", str(tmp_path), on_data=chunks.append, signal=None),
        "exec 'echo ok' with signal=None",
        bound=HANG_GUARD_SECONDS,
    )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# bash exec — a kill against a process that is already gone must be contained
# ---------------------------------------------------------------------------


async def test_bash_exec_abort_with_process_already_gone_still_returns(
    monkeypatch,
    tmp_path: Path,
    phases: BashPhases,
) -> None:
    """If the kill lands on a process that already exited, exec() still returns.

    Written to cover the ``suppress(OSError)`` around every rung of the ladder
    (``ProcessTree.hard_kill``, the ``proc.kill()`` belt, the bounded reap) —
    ``except (ProcessLookupError, PermissionError): return`` inside
    ``_kill_group`` until #222.  It runs a short-lived command (``true``) and
    fires the signal 0.2 s later, meaning the kill to find nothing to reach.
    exec() must return with exit_code 0 or None and must NOT raise.

    WHERE IT WAS MEASURED, IT NEVER MEETS A GONE COMMAND (#471 review round 2;
    the case was the same before #471). On darwin ``exec`` has already
    returned when the 0.2 s abort fires, so the watcher is disarmed and no
    kill runs at all (20 replays of 20, exit_code 0). On windows-latest the
    abort lands before the shell has produced any output (480 calls of 480,
    run 38502661220), so the kill ends a shell that is still starting; and
    ``true`` is not a pwsh command, so a pwsh that got to run it would exit 1,
    which the assert below does not accept. What the ladder does with a
    process that has already exited is not exercised here; a follow-up
    carries it.
    """
    import aelix_coding_agent.tools.bash as _bash_mod

    ops = _bash_mod.create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    # Run a command that exits quickly, then fire the signal. Meant to make the
    # ladder meet a process that is gone; the docstring says where the abort
    # really lands.
    phases.stamp("call")
    task = asyncio.create_task(
        ops.exec("true", str(tmp_path), on_data=chunks.append, signal=sig)
    )
    # Let the process finish naturally first. A WINDOW, NOT A VERDICT (#330
    # classified it): either outcome is asserted below — ``0`` if the process
    # was already gone, ``None`` if the abort won — so load can only move which
    # path the kill takes, never turn the case red. ``exec`` exposes no exit
    # event to wait for before the abort short of its own return.
    await asyncio.sleep(0.2)
    phases.stamp("abort")
    sig.abort()

    # exec() must return (not hang).  exit_code may be 0 (process already done)
    # or None (signal path wins); either way no exception must escape. So a
    # HANG GUARD (#471), not a verdict: the command ends by itself whatever the
    # kill does. On windows-latest (run 38502661220, both versions of this
    # file) abort to return took a median 0.10 s; at most 2.67 s in the
    # 529b997e file, 2.59 s of it in the kill ladder, which is half of what was
    # a 5 s bound, and at most 0.57 s in this one.
    result = await phases.guard(
        task,
        "exec after an abort 0.2 s into 'true'",
        bound=HANG_GUARD_SECONDS,
    )
    assert result.exit_code in (0, None)


async def test_bash_exec_cancel_watcher_teardown_catches_exception(
    monkeypatch,
    tmp_path: Path,
    phases: BashPhases,
) -> None:
    """The watcher teardown contains the watcher's own outcome and masks nothing.

    Unchanged since it was written, but the MECHANISM it names changed with
    #234 and the case has to say so. It used to be a broad
    ``except (asyncio.CancelledError, Exception)`` around ``await
    watcher_task`` — which is exactly what could not tell the watcher's own
    cancellation from the CALLER's, and threw the caller's away
    (``test_a_turn_cancel_landing_in_the_watcher_teardown_is_not_swallowed``).
    It is ``await asyncio.wait([watcher_task])`` now: how the watcher finished
    — cancelled or raised — is absorbed as membership of the ``done`` set
    rather than by an ``except``, and an exception it left behind is retrieved
    explicitly in a ``finally``
    (``test_a_signal_that_blows_up_while_being_cancelled_leaves_nothing_for_the_gc``).

    Here the cancel lands in ``_wait`` rather than in the teardown, so the
    ``except asyncio.CancelledError`` leg above IS on the stack: what this case
    pins is that disarming the watcher on the way out neither masks that
    cancellation nor replaces it with the watcher's.

    Note: we cannot inject a RuntimeError into the ladder and simultaneously expect
    exec() to return (the unreaped process would hold the drain open).  Instead
    we validate the containment contract by checking CancelledError propagates when
    the watcher encounters ProcessLookupError (process already dead before kill).
    """
    import aelix_coding_agent.tools.bash as _bash_mod

    ops = _bash_mod.create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    # Register a signal watcher so the watcher_task code path is exercised.
    phases.stamp("call")
    task = asyncio.create_task(
        ops.exec(_STARTED_THEN_SLEEPS, str(tmp_path), on_data=chunks.append, signal=sig)
    )
    await _await_started(chunks, phases, task)

    # Cancel the outer task (Esc path) — the finally block cancels the watcher
    # task; any exception in the watcher teardown must be contained.
    phases.stamp("cancel")
    task.cancel()

    await _await_the_cancelled_call(task, phases)
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# bash exec — a cancellation that LANDS IN the watcher teardown (#234)
# ---------------------------------------------------------------------------

#: A five-second sleeper for the resolved shell, per platform: ``sleep`` is not
#: a pwsh command and pwsh needs the call operator to run a quoted path, which
#: is how ``test_bash_tool.py`` builds its own timeout child.
_INTERPRETER = f'& "{sys.executable}"' if sys.platform == "win32" else shlex.quote(sys.executable)
_SLEEPER = f'{_INTERPRETER} -c "import time; time.sleep(5)" "aelix234"'


class _CancelsTheTurnFromInsideTheWatcher:
    """The whole signal surface ``exec`` needs, plus a hook in the one window.

    ``exec`` starts the watcher on ``hasattr(signal, "wait")`` alone, so a
    duck-typed object is the fake: ``wait()`` here blocks on an event nobody
    ever sets, which means the ONLY thing that ever wakes it is the teardown's
    own ``watcher_task.cancel()``. At that instant the task running ``exec`` is
    suspended on the teardown's await — so cancelling it from here lands the
    caller's cancellation in exactly the window #234 exists for, every time,
    where the #222 review's probe reached it by random timing (~2 % of the
    cancels it actually delivered to a still-running ``exec``).

    ``deliver`` picks which delivery path is exercised, and WHICH ONE a hop
    reaches is decided by what ``exec`` is suspended on. The teardown awaits
    ``asyncio.wait``'s internal waiter, NOT the watcher task, and that waiter is
    resolved only by the ``_on_completion`` callback the ``wait`` registered on
    the watcher — which is scheduled AFTER anything this hook queues from inside
    its own ``except``. So a single ``loop.call_soon`` is not a second path here:

    * ``"inline"`` and a single ``call_soon`` both cancel while the waiter is
      still pending, so ``Task.cancel`` takes the ``_fut_waiter.cancel()`` →
      ``True`` branch — measured identical, 30/30 each on darwin/py3.12.13.
    * ``"after_completion"`` hops twice, landing after ``_on_completion`` has
      resolved the waiter: there the call returns ``False`` and the loop sets
      ``_must_cancel`` instead (30/30).

    ``main`` loses both. On ``main``'s ``await watcher_task`` a single
    ``call_soon`` DID reach ``_must_cancel``, because the awaited object was the
    watcher task itself; moving to ``asyncio.wait`` is what collapsed the two
    paths, so this docstring cannot be inherited from the design.
    """

    def __init__(self, *, deliver: str = "inline") -> None:
        self._event = asyncio.Event()
        self._deliver = deliver
        self.exec_task: asyncio.Task[ExecExitResult] | None = None
        self.cancelled_the_turn = False
        self.delivered_via: str | None = None

    def _cancel_and_record(self) -> None:
        assert self.exec_task is not None
        self.exec_task.cancel()
        # Pinned so the arm cannot silently degrade into a copy of the inline
        # one, which is exactly what a single ``call_soon`` was.
        self.delivered_via = (
            "_must_cancel" if self.exec_task._must_cancel else "_fut_waiter.cancel()"
        )

    async def wait(self) -> None:
        try:
            await self._event.wait()
        except asyncio.CancelledError:  # the teardown's own ``watcher_task.cancel()``
            assert self.exec_task is not None, "the case never handed over the exec task"
            self.cancelled_the_turn = True
            loop = asyncio.get_running_loop()
            if self._deliver == "after_completion":
                loop.call_soon(lambda: loop.call_soon(self._cancel_and_record))
            else:
                self._cancel_and_record()
            raise


@pytest.mark.parametrize(
    (
        "arm",
        "command",
        "timeout",
        "what_main_returned",
        "deliver",
        "expect_delivered_via",
        "expect_in_output",
    ),
    [
        pytest.param(
            "normal/inline",
            "echo ok",
            None,
            "exit_code=0, timed_out=False",
            "inline",
            "_fut_waiter.cancel()",
            b"ok",
            id="normal-leg-inline-cancel",
        ),
        pytest.param(
            "timeout/inline",
            _SLEEPER,
            0.3,
            "exit_code=None, timed_out=True",
            "inline",
            "_fut_waiter.cancel()",
            None,
            id="timeout-leg-inline-cancel",
        ),
        pytest.param(
            "normal/after_completion",
            "echo ok",
            None,
            "exit_code=0, timed_out=False",
            "after_completion",
            "_must_cancel",
            b"ok",
            id="normal-leg-after-completion-cancel",
        ),
    ],
)
async def test_a_turn_cancel_landing_in_the_watcher_teardown_is_not_swallowed(
    tmp_path: Path,
    arm: str,
    command: str,
    timeout: float | None,
    what_main_returned: str,
    deliver: str,
    expect_delivered_via: str,
    expect_in_output: bytes | None,
) -> None:
    """A cancellation delivered inside the watcher teardown must come back out.

    ``main`` disarms the watcher with ``watcher_task.cancel()`` and then awaits
    it inside ``suppress(CancelledError, Exception)``. TWO cancellations can
    arrive at that ``await`` and it cannot tell them apart, so the caller's is
    swallowed and ``exec`` returns an ordinary result. Both legs that reach the
    teardown with a live watcher are parametrised, because the fix does not
    treat them alike:

    * NORMAL — the command exited on its own; ``main`` returned
      ``exit_code=0, timed_out=False``.
    * TIMEOUT — ``_wait`` caught ``TimeoutExpired``, already ended the tree and
      stamped the kill, and returned ``None``; ``main`` returned
      ``exit_code=None, timed_out=True``. After the fix the cancellation wins
      over that label, which is a deliberate second observable change
      (ADR-0238, CHANGELOG) and not an accident of this case.

    The third arm is the same window reached on the loop turn AFTER
    ``asyncio.wait``'s ``_on_completion`` has resolved the waiter, which is where
    ``Task.cancel`` sets ``_must_cancel`` instead of cancelling a live future.
    Each arm asserts the branch it took, because a single ``call_soon`` lands on
    the same branch as the inline cancel and the arm would otherwise be a third
    copy of the first — see the hook's docstring.
    """

    ops = create_local_bash_operations()
    sig = _CancelsTheTurnFromInsideTheWatcher(deliver=deliver)
    chunks: list[bytes] = []
    task = asyncio.ensure_future(
        ops.exec(command, str(tmp_path), on_data=chunks.append, signal=sig, timeout=timeout)
    )
    sig.exec_task = task
    started = time.monotonic()

    # Never left to GitHub's 360-minute default: this file has no
    # ``pytest-timeout`` and the jobs have no ``timeout-minutes``.
    _done, pending = await asyncio.wait([task], timeout=60.0)
    elapsed = time.monotonic() - started
    if pending:
        task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait([task], timeout=5.0)
        pytest.fail(f"[{arm}] exec did not return within 60s")

    assert sig.cancelled_the_turn, (
        f"[{arm}] the teardown never cancelled the watcher — the case measured nothing"
    )
    assert sig.delivered_via == expect_delivered_via, (
        f"[{arm}] the cancel took the {sig.delivered_via} branch, not "
        f"{expect_delivered_via} — this arm has degraded into a copy of another"
    )
    assert task.cancelled(), (
        f"[{arm}] the watcher teardown swallowed the caller's cancellation: exec returned "
        f"{task.result()!r} (on main: {what_main_returned})"
    )
    if expect_in_output is not None:
        # ``in``, never ``== b"ok\n"``: pwsh writes CRLF on the windows leg.
        assert expect_in_output in b"".join(chunks)
    warnings.warn(
        f"watcher-teardown cancel [{arm}] returned in {elapsed:.3f}s on {sys.platform}",
        stacklevel=1,
    )


class _BlowsUpWhileBeingCancelled:
    """A signal whose ``wait()`` fails DURING its own cancellation.

    The ordering that matters for the GC report: ``Task.cancel()`` clears
    CPython's ``_log_traceback`` only for a task that has ALREADY failed, and
    this one fails AFTER that ``cancel()``, where ``Future.set_exception`` sets
    the flag back. ``main``'s ``await watcher_task`` retrieved the exception
    into its ``suppress``; a bare ``asyncio.wait`` would not, and the loop would
    report ``Task exception was never retrieved`` at GC.

    With ``exec_task`` set it also cancels the caller, so the same object
    produces the arm where a retrieval written AFTER the wait is skipped.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.exec_task: asyncio.Task[ExecExitResult] | None = None
        self.cancelled_the_turn = False
        self.watcher_task: asyncio.Task[None] | None = None

    async def wait(self) -> None:
        self.watcher_task = asyncio.current_task()
        try:
            await self._event.wait()
        except asyncio.CancelledError as cancelled:
            if self.exec_task is not None:
                self.cancelled_the_turn = True
                self.exec_task.cancel()
            raise RuntimeError("the watcher failed while it was being cancelled") from cancelled


class _BlowsUpBeforeTheTeardown:
    """A signal whose ``wait()`` fails BEFORE the teardown ever reaches it."""

    async def wait(self) -> None:
        raise RuntimeError("the watcher failed before the teardown reached it")


async def _fails_and_is_never_retrieved() -> None:
    """The blindness control's own task body."""

    raise RuntimeError("the blindness control's failure, deliberately unretrieved")


async def _settle_the_gc() -> None:
    """Collect, then give the loop a turn, twice.

    The report is emitted from ``Task.__del__`` through
    ``loop.call_exception_handler``, so both halves are needed and neither is
    enough on its own.
    """

    for _ in range(2):
        gc.collect()
        await asyncio.sleep(0)


def _never_retrieved(reported: list[dict[str, Any]]) -> list[str]:
    return [
        str(context.get("message", ""))
        for context in reported
        if "never retrieved" in str(context.get("message", ""))
    ]


async def test_a_signal_that_blows_up_while_being_cancelled_leaves_nothing_for_the_gc(
    tmp_path: Path,
) -> None:
    """The teardown must still RETRIEVE the watcher's exception, in a ``finally``.

    Three arms and a control, in this order, against a loop exception handler:

    1. A watcher that fails during its own cancellation, no caller cancel. A
       bare ``asyncio.wait`` reports here where ``main``'s ``await`` did not —
       this arm is why the retrieval exists at all.
    2. The same watcher plus the caller's cancellation, delivered from inside
       it. A retrieval written AFTER the wait never runs, because the
       cancellation propagates out of the wait — this arm is why the retrieval
       is in a ``finally``, and it is a second repro of #234's own window
       (``main`` returns a result here instead of cancelling).
    3. A watcher that failed BEFORE the teardown, which is the shape the
       pre-#234 reasoning was derived from. It is green under every shape,
       because there ``watcher_task.cancel()`` really does disarm the report —
       it is here to document that it discriminates NOTHING, not as evidence.

    THE BLINDNESS CONTROL RUNS LAST, and the ordering is load-bearing: the
    handler buffer is shared, so a control run before the ``exec`` calls dirties
    it and the three green arms then fail on ``main`` and on the fix alike.
    """

    ops = create_local_bash_operations()
    loop = asyncio.get_running_loop()
    reported: list[dict[str, Any]] = []
    saved = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    try:
        # ARM 1 — the watcher fails during its own cancellation; no caller cancel.
        chunks: list[bytes] = []
        first = _BlowsUpWhileBeingCancelled()
        result = await asyncio.wait_for(
            ops.exec("echo ok", str(tmp_path), on_data=chunks.append, signal=first), 60.0
        )
        assert result.exit_code == 0
        await _settle_the_gc()
        assert _never_retrieved(reported) == [], (
            "arm 1: the teardown left the watcher's exception for the GC to report"
        )

        # ARM 2 — the same, with the caller's cancellation landing in the wait.
        cancelled_chunks: list[bytes] = []
        second = _BlowsUpWhileBeingCancelled()
        task = asyncio.ensure_future(
            ops.exec("echo ok", str(tmp_path), on_data=cancelled_chunks.append, signal=second)
        )
        second.exec_task = task
        _done, pending = await asyncio.wait([task], timeout=60.0)
        if pending:
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait([task], timeout=5.0)
            pytest.fail("arm 2: exec did not return within 60s")
        assert second.cancelled_the_turn, (
            "arm 2: the teardown never cancelled the watcher — the arm measured nothing"
        )
        assert task.cancelled(), (
            f"arm 2: the teardown swallowed the caller's cancellation: exec returned "
            f"{task.result()!r}"
        )
        # Everything that could keep the watcher's task reachable, dropped:
        # the exec task holds ``exec``'s frame through its ``_cancelled_exc``
        # traceback, and the hook holds the exec task. With either still bound
        # the collection below reaches nothing and this arm asserts silence it
        # did not measure.
        second.exec_task = None
        ref = weakref.ref(second.watcher_task)
        second.watcher_task = None
        del task, _done, pending
        await _settle_the_gc()
        assert ref() is None, (
            "arm 2: the watcher task was still reachable — this arm's silence "
            "measured nothing"
        )
        assert _never_retrieved(reported) == [], (
            "arm 2: a retrieval that is not in a ``finally`` is skipped by the cancellation"
        )

        # ARM 3 — the watcher had already failed when the teardown reached it.
        early_chunks: list[bytes] = []
        early = await asyncio.wait_for(
            ops.exec(
                "echo ok",
                str(tmp_path),
                on_data=early_chunks.append,
                signal=_BlowsUpBeforeTheTeardown(),
            ),
            60.0,
        )
        assert early.exit_code == 0
        await _settle_the_gc()
        assert _never_retrieved(reported) == [], (
            "arm 3: the already-failed watcher was left for the GC to report"
        )

        # THE BLINDNESS CONTROL, LAST: an untouched failed task in this same
        # case must reach the handler, or the three asserts above prove nothing
        # about the handler and would stay green with it uninstalled.
        control = asyncio.ensure_future(_fails_and_is_never_retrieved())
        await asyncio.wait([control], timeout=5.0)
        assert control.done()
        del control
        await _settle_the_gc()
        assert _never_retrieved(reported), (
            "the control's failed task was NOT reported — this case cannot see a GC report "
            "at all, so its three green arms measured nothing"
        )
    finally:
        loop.set_exception_handler(saved)


# ---------------------------------------------------------------------------
# The start wait reports the case's own event (#471)
# ---------------------------------------------------------------------------


async def test_a_slow_start_is_reported_up_to_the_cases_own_event() -> None:
    """A start wait whose event no stamp marks still names the phase that took the time.

    :func:`_await_started` and the process-group case wait for their own sign
    that the command runs, a line on ``on_data`` or a pid file, through
    ``BashPhases.shell_started(until=...)``. No stamp of the call marks a pid
    file, so the wait stamps ``running`` when the sign holds; without it a
    5.5 s start was reported with ``spawn -> spawned (0.001s)`` as its slowest
    phase. In-process: the sign is a clock, and no shell runs.
    """

    phases = BashPhases()
    phases.stamp("call")
    ready = time.monotonic() + 0.4

    with pytest.warns(UserWarning, match=r"^#449 a sign 0\.4 s away") as caught:
        await phases.shell_started(
            "a sign 0.4 s away", until=lambda: time.monotonic() >= ready, slow=0.2
        )

    (message,) = [str(w.message) for w in caught if str(w.message).startswith("#449")]
    assert "slowest phase call -> running (" in message, message
    assert "the command starting, up to the case's own sign that it runs" in message, message
    # 0.4 s away; 0.35 leaves the poll and a 15.6 ms clock a margin.
    assert phases.stamps["running"] - phases.stamps["call"] >= 350_000_000, message


async def test_an_expired_start_wait_names_the_phase_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired start wait's timeline ends at ``(guard expired)``, as ``guard``'s does.

    So the slowest phase it names is the one still running when the bound ran
    out, not the largest gap before it, and the case's own detail (the output
    so far) comes ahead of it. The bound is shortened here; the cases use the
    hang guard.
    """

    monkeypatch.setattr(_bash_phases, "HANG_GUARD_SECONDS", 0.2)
    phases = BashPhases()
    phases.stamp("call")

    with pytest.raises(AssertionError) as failed:
        await phases.shell_started(
            "a sign that never comes",
            until=lambda: False,
            detail=lambda: "output so far: b''",
        )

    message = str(failed.value)
    assert "(bound 0.2s" in message and "for: a sign that never comes" in message, message
    assert "\noutput so far: b''\nslowest phase call -> (guard expired) (" in message, message


# ---------------------------------------------------------------------------
# The start wait's end, its poll, and guard()'s expiry (#471 review round 2)
# ---------------------------------------------------------------------------


async def test_a_start_wait_whose_call_returned_fails_at_once_naming_it(
    tmp_path: Path, phases: BashPhases
) -> None:
    """A start wait stops as soon as the call it waits on has returned, and says so.

    The process-group case's own "spawn failed?" shape: the interpreter does
    not exist, so the pid file never appears and ``exec`` returns at once (127
    from bash, an error exit from pwsh). Before review round 2 the wait ran out
    the whole 30 s guard after that and then named ``drain entered -> (guard
    expired)`` as "the phase in flight" (darwin: ``exec`` had returned 127 in
    0.010 s). Now it fails when it sees the call done: no ``(guard expired)``,
    a ``returned`` stamp, how the call ended and the shell's output. Asserted
    on the message alone, so a slow runner cannot decide it: a wait that
    ignored the call would fail only at the guard, and with the other text.
    """

    missing = tmp_path / "no-such-interpreter"
    interpreter = f'& "{missing}"' if sys.platform == "win32" else shlex.quote(str(missing))
    pid_file = str(tmp_path / "child.pid")
    chunks: list[bytes] = []
    phases.stamp("call")
    task = asyncio.create_task(
        create_local_bash_operations().exec(
            f'{interpreter} -c "pass" "{pid_file}"', str(tmp_path), on_data=chunks.append
        )
    )

    with pytest.raises(AssertionError) as failed:
        await phases.shell_started(
            "a pid file that a missing interpreter never writes",
            until=lambda: _whole_pid(pid_file) is not None,
            detail=lambda: f"output so far: {b''.join(chunks)!r}",
            interval=0.05,
            call=task,
        )

    message = str(failed.value)
    assert task.done() and not task.cancelled(), message
    assert message.startswith(
        "a pid file that a missing interpreter never writes: the call returned before it "
        f"held (returned {task.result()!r}), "
    ), message
    assert "(guard expired)" not in message, message
    # pwsh quotes the missing term in single quotes, so its bytes repr as b"..."
    assert re.search(r"\noutput so far: b['\"]", message), message
    assert "returned" in phases.stamps, message
    assert message.endswith(" returned"), message


async def test_a_start_wait_stamps_returned_when_the_call_finished() -> None:
    """The ``returned`` stamp is the call's end, not the poll that noticed it.

    Stamped by the poll, it would add up to ``interval`` to the gap before it,
    which is glossed as the post-exit drain. Ordering, not time: the case's
    sign records the first poll that runs after the call is done, on the
    stamps' own clock (``_bash_phases._now_ns``: GetSystemTimePreciseAsFileTime
    on win32, ``time.time_ns`` elsewhere), and the stamp must come before
    that poll. In-process: the
    call is a future that a timer resolves 0.05 s into a 0.3 s poll.
    """

    loop = asyncio.get_running_loop()
    call: asyncio.Future[str] = loop.create_future()
    loop.call_later(0.05, call.set_result, "finished")
    first_poll_after: list[int] = []

    def never_holds() -> bool:
        if call.done() and not first_poll_after:
            first_poll_after.append(_bash_phases._now_ns())
        return False

    phases = BashPhases()
    phases.stamp("call")
    with pytest.raises(AssertionError, match="the call returned before it held"):
        await phases.shell_started(
            "a sign that never comes", until=never_holds, interval=0.3, call=call
        )

    (polled_ns,) = first_poll_after
    assert phases.stamps["returned"] < polled_ns, phases.stamps


async def test_a_start_wait_polls_at_the_interval_it_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``shell_started(interval=...)`` is the poll the wait sleeps between its asks.

    The process-group case passes 0.05 s because opening its pid file every
    0.01 s hit a ``PermissionError`` on windows-latest (1 of 258 calls, run
    38502661220). Read from the sleeps the wait arms, not from a clock: in
    review round 1 a helper that dropped the argument and polled at 0.01 s
    passed every other case. The sleeps are recorded through a view of
    :mod:`asyncio` on :mod:`tests.event_waits` alone, as ``BashPhases.install``
    views :mod:`subprocess` (``record_armed_waits`` cannot record that module:
    its recorder calls ``asyncio.sleep`` through the same name).
    """

    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(delay: float, result: Any = None) -> Any:
        slept.append(delay)
        return await real_sleep(delay, result)

    view = types.SimpleNamespace(**vars(asyncio))
    view.sleep = recording_sleep
    monkeypatch.setattr(event_waits, "asyncio", view)
    asked: list[None] = []

    def third_ask() -> bool:
        asked.append(None)
        return len(asked) == 3

    await BashPhases().shell_started("a sign on the third ask", until=third_ask, interval=0.05)

    assert slept == [0.05, 0.05], slept


async def test_a_late_delivery_after_the_first_output_is_glossed_as_this_process() -> None:
    """The gap from the shell's first output to the case's sign is this process's.

    :func:`_await_started`'s sign is the ``started`` line, which is the shell's
    first output. The reader stamps that on its own thread; what follows is
    the loop delivering it to ``on_data`` and the next poll. Glossed as "the
    command starting", a loop held 3 s after the first byte read as a 3.009 s
    command start (review round 2, darwin). In-process: the first output is
    stamped by hand and the sign is a clock.
    """

    phases = BashPhases()
    phases.stamp("call")
    phases.stamp("first output")
    ready = time.monotonic() + 0.4

    with pytest.warns(UserWarning, match=r"^#449 a sign 0\.4 s after the first output") as caught:
        await phases.shell_started(
            "a sign 0.4 s after the first output",
            until=lambda: time.monotonic() >= ready,
            slow=0.2,
        )

    (message,) = [str(w.message) for w in caught if str(w.message).startswith("#449")]
    assert "slowest phase first output -> running (" in message, message
    assert (
        "this process, after the shell's first output: the loop delivering it to on_data"
        in message
    ), message
    assert "the command starting" not in message, message


async def test_an_expiry_that_fires_early_is_stamped_before_the_kill_it_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``guard``'s ``(guard expired)`` is stamped when the bound fires, before its kill.

    The bound fires on the loop's clock, and asyncio runs a timer up to the
    loop's clock resolution early (15.6 ms on windows py3.11 and py3.12). The
    stamp used to be the start plus the bound on the wall clock, so the kill
    that the expiry's cancellation runs could be stamped FIRST and the report
    named ``X -> kill started``: on windows-latest 4 of the 24 expired-guard
    reports of run 38502661220 and 5 of the 54 of run 38508575549. Injected
    here without a race: once the call starts, the loop's clock runs 0.05 s
    ahead of the wall clock, so the 0.5 s bound fires 0.05 s early, and the
    call stamps ``kill started`` first thing on its cancellation, as
    ``exec``'s cancel leg does. A task that yields on every loop turn keeps
    the loop out of its selector, so each timer runs on the first turn after
    its deadline: left to sleep, the loop on darwin woke late enough that an
    expiry timer due 1 ms AFTER ``within``'s still ran first 5 times in 6.
    """

    loop = asyncio.get_running_loop()
    real_time = loop.time
    ahead = [0.0]
    phases = BashPhases()
    phases.stamp("call")

    async def never_returns() -> None:
        ahead[0] = 0.05
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            phases.stamp("kill started")
            raise

    spinning = True

    async def spin() -> None:
        while spinning:
            await asyncio.sleep(0)

    monkeypatch.setattr(loop, "time", lambda: real_time() + ahead[0])
    spinner = asyncio.ensure_future(spin())
    try:
        with pytest.raises(AssertionError) as failed:
            await phases.guard(never_returns(), "a call that never returns", bound=0.5)
    finally:
        spinning = False
        await spinner
        monkeypatch.undo()

    message = str(failed.value)
    assert "slowest phase call -> (guard expired) (" in message, message
    timeline = message.split("timeline: ", 1)[1]
    assert timeline.index("(guard expired)") < timeline.index("kill started"), message


def test_an_expiry_tied_with_a_stamp_sorts_before_it() -> None:
    """On one clock tick, ``(guard expired)`` sorts before the stamp it ties with.

    What the expiry starts (its cancellation's kill) is stamped after it,
    never before, so a tie is read that way; sorted by time alone, the stamp
    taken first would win the tie and the report would name ``X -> kill
    started``. In-process, on stamps set by hand.
    """

    phases = BashPhases()
    phases.stamps.update({"call": 1_000, "kill started": 2_000})

    timeline, slowest = phases.report(now=2_000)

    assert slowest is not None and slowest.startswith("call -> (guard expired) ("), slowest
    assert timeline.endswith("(guard expired) | +0.000s kill started"), timeline
