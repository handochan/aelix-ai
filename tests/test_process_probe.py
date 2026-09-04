"""Unit tests for :mod:`tests.process_probe` — the #203 liveness probe.

These run on every platform and are the reason the two kill tests can be
trusted off Linux: they pin that the probe says ALIVE for a process that is
demonstrably running and GONE only after it has actually been killed and
reaped.  The old ``/proc``-absence check would have failed the first of these
assertions on macOS and Windows, which is precisely the bug.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys

import pytest

from tests.process_probe import (
    STATE_ALIVE,
    STATE_GONE,
    STATE_ZOMBIE,
    await_dead_or_zombie,
    is_dead_or_zombie,
    probe_state,
)


def test_probe_reports_self_alive() -> None:
    """The running interpreter is alive — true on every platform, no spawn."""

    assert probe_state(os.getpid()) == STATE_ALIVE
    assert is_dead_or_zombie(os.getpid()) is False


async def test_probe_tracks_a_real_child_from_alive_to_dead() -> None:
    """Spawn a sleeper, probe ALIVE, kill + reap it, probe dead."""

    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # A process we are holding open must never read as dead. This is the
        # assertion the old /proc-absence poll got wrong off Linux.
        assert probe_state(proc.pid) == STATE_ALIVE
        assert is_dead_or_zombie(proc.pid) is False

        proc.kill()
        # Reap it, so the pid leaves the table rather than lingering as a
        # zombie that only Linux/macOS could name.
        proc.wait(timeout=10)

        state = await await_dead_or_zombie(proc.pid, timeout=5.0)
        assert state in (STATE_GONE, STATE_ZOMBIE), (
            f"probe still reports '{state}' for reaped pid {proc.pid}"
        )
        assert is_dead_or_zombie(proc.pid) is True
    finally:
        if proc.poll() is None:  # pragma: no cover — only on an assert above
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(subprocess.SubprocessError):
                proc.wait(timeout=10)


async def test_await_dead_or_zombie_returns_alive_on_timeout() -> None:
    """A live child makes the waiter time out and report ALIVE, not "gone".

    This is the non-vacuity property the two kill tests depend on: when the
    kill does not happen, the helper must hand the caller a state that fails
    the assertion.
    """

    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        state = await await_dead_or_zombie(proc.pid, timeout=0.2)
        assert state == STATE_ALIVE
    finally:
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.SubprocessError):
            proc.wait(timeout=10)


def test_a_non_pid_is_a_caller_bug_not_a_dead_process() -> None:
    """``pid <= 0`` must raise: reading it as GONE would be this module's own
    vacuity."""

    with pytest.raises(ValueError, match="not a process id"):
        probe_state(0)
    with pytest.raises(ValueError, match="not a process id"):
        probe_state(-1)


class _FakeKernel32:
    """The three calls :func:`_probe_win32` makes, scripted.

    ``open_handle`` is what OpenProcess returns (0 = failure), ``last_error``
    what GetLastError then says, ``exit_code`` what GetExitCodeProcess writes
    (``None`` = the call fails).
    """

    def __init__(self, *, open_handle: int, last_error: int = 0, exit_code: int | None = None):
        self.open_handle = open_handle
        self.last_error = last_error
        self.exit_code = exit_code
        self.closed: list[int] = []
        self.opened_with: tuple[int, bool, int] | None = None

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:  # noqa: N802
        self.opened_with = (access, inherit, pid)
        return self.open_handle

    def GetExitCodeProcess(self, handle: int, out) -> int:  # noqa: N802
        if self.exit_code is None:
            return 0
        out._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle: int) -> int:  # noqa: N802
        self.closed.append(handle)
        return 1


def _win32(fake: _FakeKernel32, pid: int = 4242) -> str:
    from tests.process_probe import _probe_win32

    return _probe_win32(pid, kernel32=fake, get_last_error=lambda: fake.last_error)


def test_win32_no_such_pid_is_gone_but_access_denied_is_alive() -> None:
    """Injected on every platform: only ERROR_INVALID_PARAMETER (87) is proof
    of death; ACCESS_DENIED (5) means a process is there and not ours."""

    assert _win32(_FakeKernel32(open_handle=0, last_error=87)) == STATE_GONE
    assert _win32(_FakeKernel32(open_handle=0, last_error=5)) == STATE_ALIVE


def test_win32_still_active_is_alive_and_any_exit_code_is_gone() -> None:
    running = _FakeKernel32(open_handle=0x1234, exit_code=259)
    assert _win32(running) == STATE_ALIVE
    exited = _FakeKernel32(open_handle=0x1234, exit_code=0)
    assert _win32(exited) == STATE_GONE
    failed_query = _FakeKernel32(open_handle=0x1234, exit_code=None)
    assert _win32(failed_query) == STATE_ALIVE  # "cannot tell" is never "dead"
    # The handle is closed on every path, and opened with the LIMITED mask.
    for fake in (running, exited, failed_query):
        assert fake.closed == [0x1234]
        assert fake.opened_with == (0x1000, False, 4242)
