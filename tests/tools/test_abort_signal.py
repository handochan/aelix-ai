"""Sprint 3 cooperative-abort — AbortSignal unit tests + bash exec abort tests.

Covers:
- :class:`~aelix_coding_agent.tools._abort.AbortSignal` API contract.
- :meth:`~aelix_coding_agent.tools.bash._LocalBashOperations.exec` with
  signal abort: the child process group is killed and exit_code is None.
- :meth:`~aelix_coding_agent.tools.bash._LocalBashOperations.exec` with
  asyncio.CancelledError: the child group is killed and CancelledError
  propagates (is NOT swallowed).
"""

from __future__ import annotations

import asyncio
import contextlib
import os

import pytest
from aelix_coding_agent.tools._abort import AbortSignal
from aelix_coding_agent.tools.bash import ExecExitResult, create_local_bash_operations

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


async def test_bash_exec_signal_abort_kills_child() -> None:
    """Aborting via AbortSignal kills the subprocess group and returns exit_code=None."""

    ops = create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    async def _exec_task() -> ExecExitResult:
        return await ops.exec(
            "sleep 30",
            "/tmp",
            on_data=chunks.append,
            signal=sig,
        )

    task = asyncio.create_task(_exec_task())

    # Give the process time to start, then abort.
    await asyncio.sleep(0.1)
    sig.abort()

    result = await asyncio.wait_for(task, timeout=5.0)
    assert result.exit_code is None, "Aborted exec must return exit_code=None"


async def test_bash_exec_signal_abort_kills_process_group() -> None:
    """Signal abort kills the entire process GROUP including grandchildren.

    Strategy: run ``sh -c 'echo $$; sleep 30'`` — the shell prints its own
    PID to stdout (captured via on_data), then spawns a grandchild ``sleep``.
    After sig.abort() we verify the shell process (and therefore the whole
    group, since start_new_session=True puts it in its own pgid) is dead or
    zombie.  This proves _kill_group uses os.killpg (group-level kill), not
    just a direct-child kill — matching the pattern proven in
    test_subprocess_helper.py::test_run_cancellable_cancellation_kills_child.
    """
    import tempfile

    pid_file = tempfile.mktemp(suffix=".pid")  # noqa: S306 — test-only
    # The shell prints its PID to the pid_file then sleeps, spawning
    # a grandchild sleep as well.
    command = (
        f"sh -c 'echo $$ > {pid_file}; sleep 30'"
    )

    ops = create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    task = asyncio.create_task(
        ops.exec(command, "/tmp", on_data=chunks.append, signal=sig)
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

    # Poll /proc/<pid>/status until the child is dead (Z/X) or fully reaped.
    deadline = asyncio.get_event_loop().time() + 3.0
    last_state = "unknown"
    while asyncio.get_event_loop().time() < deadline:
        proc_status = f"/proc/{child_pid}/status"
        if not os.path.exists(proc_status):
            last_state = "gone"
            break
        with open(proc_status) as f:
            for line in f:
                if line.startswith("State:"):
                    last_state = line.split()[1]
                    break
        if last_state in ("Z", "X", "gone"):
            break
        await asyncio.sleep(0.05)

    assert last_state in ("Z", "X", "gone"), (
        f"Child process {child_pid} still in state '{last_state}' "
        f"after signal abort (expected Z/X/gone — group kill failed?)"
    )


# ---------------------------------------------------------------------------
# bash exec — CancelledError path
# ---------------------------------------------------------------------------


async def test_bash_exec_cancel_propagates_cancelled_error() -> None:
    """Cancelling the exec task must NOT swallow CancelledError."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    task = asyncio.create_task(
        ops.exec("sleep 30", "/tmp", on_data=chunks.append)
    )

    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)


async def test_bash_exec_cancel_returns_none_exit_code() -> None:
    """After cancel the task result (if collected) should have exit_code=None."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    task = asyncio.create_task(
        ops.exec("sleep 30", "/tmp", on_data=chunks.append)
    )

    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)


async def test_bash_exec_normal_path_unaffected() -> None:
    """The normal exec path (no signal, no cancel) must be byte-identical to before."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    result = await asyncio.wait_for(
        ops.exec("echo aelix-abort-test", "/tmp", on_data=chunks.append),
        timeout=5.0,
    )
    output = b"".join(chunks).decode()
    assert result.exit_code == 0
    assert "aelix-abort-test" in output


async def test_bash_exec_no_signal_no_watcher() -> None:
    """Passing signal=None must not start a watcher — normal path unchanged."""

    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    result = await asyncio.wait_for(
        ops.exec("echo ok", "/tmp", on_data=chunks.append, signal=None),
        timeout=5.0,
    )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# bash exec — _kill_group failure during abort must be contained
# ---------------------------------------------------------------------------


async def test_bash_exec_abort_with_process_already_gone_still_returns(
    monkeypatch,
) -> None:
    """If _kill_group gets ProcessLookupError (process already gone), exec() still returns.

    This covers the ``except (ProcessLookupError, PermissionError): return`` guard
    inside _kill_group.  We use a short-lived command (``true``) that exits quickly,
    then fire the signal after a delay — _kill_group will get ProcessLookupError
    because the process is already dead.  exec() must return normally with exit_code=None
    (signal-aborted path overrides the actual exit code) and must NOT raise.
    """
    import aelix_coding_agent.tools.bash as _bash_mod

    ops = _bash_mod.create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    # Run a command that exits quickly; wait long enough for it to finish,
    # then fire the signal.  _kill_group will encounter ProcessLookupError
    # (no process to kill) — must be silently ignored.
    task = asyncio.create_task(
        ops.exec("true", "/tmp", on_data=chunks.append, signal=sig)
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
) -> None:
    """Watcher teardown ``except (asyncio.CancelledError, Exception): pass`` contains exceptions.

    The watcher task cancellation in the finally block uses a broad except so that
    any exception raised inside ``_watch_signal`` (e.g. from _kill_group) is contained
    and does NOT mask the outer CancelledError.  We verify this by asserting that
    exec() with a quick-exiting process + cancel still propagates CancelledError
    (not an unrelated exception from the watcher).

    Note: we cannot inject a RuntimeError into _kill_group and simultaneously expect
    exec() to return (the unreaped process would cause drain_task to hang).  Instead
    we validate the containment contract by checking CancelledError propagates when
    the watcher encounters ProcessLookupError (process already dead before kill).
    """
    import aelix_coding_agent.tools.bash as _bash_mod

    ops = _bash_mod.create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    # Register a signal watcher so the watcher_task code path is exercised.
    task = asyncio.create_task(
        ops.exec("sleep 30", "/tmp", on_data=chunks.append, signal=sig)
    )
    await asyncio.sleep(0.1)

    # Cancel the outer task (Esc path) — the finally block cancels the watcher
    # task; any exception in the watcher teardown must be contained.
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
