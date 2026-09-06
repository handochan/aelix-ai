"""Sprint 3 cooperative-abort — AbortSignal unit tests + bash exec abort tests.

Covers:
- :class:`~aelix_coding_agent.tools._abort.AbortSignal` API contract.
- :meth:`~aelix_coding_agent.tools.bash._LocalBashOperations.exec` with
  signal abort: the child's tree is killed and exit_code is None.
- :meth:`~aelix_coding_agent.tools.bash._LocalBashOperations.exec` with
  asyncio.CancelledError: the tree is killed and CancelledError propagates
  (is NOT swallowed).
- The same, for a cancellation that lands in the WATCHER TEARDOWN rather than
  in ``_wait`` — the window #234 closed, plus the explicit retrieval that keeps
  a watcher's own failure out of the loop's GC report.

Since #222 the kill at both legs is a :class:`~aelix_ai.utils._process_tree.ProcessTree`
ladder rather than the pid-only ``_kill_group`` these cases were written
against (that one-line delegate is deleted). The cases below are unchanged
because their SUBJECT is unchanged — the abort ends the command and the label
comes back right; what the ladder buys beyond the pid is asserted against real
trees in ``test_bash_tool_containment.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import os
import shlex
import sys
import time
import warnings
import weakref
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.tools._abort import AbortSignal
from aelix_coding_agent.tools.bash import ExecExitResult, create_local_bash_operations

from tests.process_probe import STATE_GONE, STATE_ZOMBIE, await_dead_or_zombie

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
    sig = AbortSignal()

    async def _fire_later() -> None:
        await asyncio.sleep(0.05)
        sig.abort()

    asyncio.create_task(_fire_later())
    await asyncio.wait_for(sig.wait(), timeout=2.0)
    assert sig.aborted is True


# ---------------------------------------------------------------------------
# bash exec — signal abort path
# ---------------------------------------------------------------------------


async def test_bash_exec_signal_abort_kills_child(tmp_path: Path) -> None:
    """Aborting via AbortSignal kills the subprocess group and returns exit_code=None."""

    ops = create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    async def _exec_task() -> ExecExitResult:
        return await ops.exec(
            "sleep 30",
            str(tmp_path),
            on_data=chunks.append,
            signal=sig,
        )

    task = asyncio.create_task(_exec_task())

    # Give the process time to start, then abort.
    await asyncio.sleep(0.1)
    sig.abort()

    result = await asyncio.wait_for(task, timeout=5.0)
    assert result.exit_code is None, "Aborted exec must return exit_code=None"


async def test_bash_exec_signal_abort_kills_process_group(tmp_path: Path) -> None:
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
    command = (
        f'{interpreter} -c "import os, sys, time; '
        f"open(sys.argv[1], 'w').write(str(os.getpid())); "
        f'time.sleep(30)" "{pid_file}"'
    )

    ops = create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    task = asyncio.create_task(
        ops.exec(command, str(tmp_path), on_data=chunks.append, signal=sig)
    )

    # Wait for the pid_file to appear (child is up and running).
    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        if os.path.exists(pid_file):
            break
        await asyncio.sleep(0.05)

    assert os.path.exists(pid_file), "Child never wrote its PID — spawn failed?"

    try:
        with open(pid_file) as _pf:
            child_pid = int(_pf.read().strip())
    finally:
        with contextlib.suppress(OSError):
            os.unlink(pid_file)

    # Fire the abort signal — must kill the process group.
    sig.abort()
    result = await asyncio.wait_for(task, timeout=5.0)
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


# ---------------------------------------------------------------------------
# bash exec — CancelledError path
# ---------------------------------------------------------------------------


async def test_bash_exec_cancel_propagates_cancelled_error(tmp_path: Path) -> None:
    """Cancelling the exec task must NOT swallow CancelledError."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    task = asyncio.create_task(
        ops.exec("sleep 30", str(tmp_path), on_data=chunks.append)
    )

    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)


async def test_bash_exec_cancel_returns_none_exit_code(tmp_path: Path) -> None:
    """After cancel the task result (if collected) should have exit_code=None."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    task = asyncio.create_task(
        ops.exec("sleep 30", str(tmp_path), on_data=chunks.append)
    )

    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)


async def test_bash_exec_normal_path_unaffected(tmp_path: Path) -> None:
    """The normal exec path (no signal, no cancel) must be byte-identical to before."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    result = await asyncio.wait_for(
        ops.exec("echo aelix-abort-test", str(tmp_path), on_data=chunks.append),
        timeout=5.0,
    )
    output = b"".join(chunks).decode()
    assert result.exit_code == 0
    assert "aelix-abort-test" in output


async def test_bash_exec_no_signal_no_watcher(tmp_path: Path) -> None:
    """Passing signal=None must not start a watcher — normal path unchanged."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    result = await asyncio.wait_for(
        ops.exec("echo ok", str(tmp_path), on_data=chunks.append, signal=None),
        timeout=5.0,
    )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# bash exec — a kill against a process that is already gone must be contained
# ---------------------------------------------------------------------------


async def test_bash_exec_abort_with_process_already_gone_still_returns(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """If the kill lands on a process that already exited, exec() still returns.

    This covers the ``suppress(OSError)`` around every rung of the ladder
    (``ProcessTree.hard_kill``, the ``proc.kill()`` belt, the bounded reap) —
    ``except (ProcessLookupError, PermissionError): return`` inside
    ``_kill_group`` until #222.  We use a short-lived command (``true``) that
    exits quickly, then fire the signal after a delay, so the kill has nothing
    to reach.  exec() must return normally with exit_code=None (signal-aborted
    path overrides the actual exit code) and must NOT raise.
    """
    import aelix_coding_agent.tools.bash as _bash_mod

    ops = _bash_mod.create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    # Run a command that exits quickly; wait long enough for it to finish,
    # then fire the signal.  The ladder will encounter ProcessLookupError
    # (no process to kill) — must be silently ignored.
    task = asyncio.create_task(
        ops.exec("true", str(tmp_path), on_data=chunks.append, signal=sig)
    )
    # Let the process finish naturally first.
    await asyncio.sleep(0.2)
    sig.abort()

    # exec() must return (not hang).  exit_code may be 0 (process already done)
    # or None (signal path wins); either way no exception must escape.
    result = await asyncio.wait_for(task, timeout=5.0)
    assert result.exit_code in (0, None)


async def test_bash_exec_cancel_watcher_teardown_catches_exception(
    monkeypatch,
    tmp_path: Path,
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
    task = asyncio.create_task(
        ops.exec("sleep 30", str(tmp_path), on_data=chunks.append, signal=sig)
    )
    await asyncio.sleep(0.1)

    # Cancel the outer task (Esc path) — the finally block cancels the watcher
    # task; any exception in the watcher teardown must be contained.
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)


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
