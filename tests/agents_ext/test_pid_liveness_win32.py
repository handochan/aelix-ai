"""``_pid_is_live`` must not use ``os.kill(pid, 0)`` on windows (#103 leg).

Signal 0 is ``CTRL_C_EVENT``. CPython's ``os_kill_impl`` routes it to
``GenerateConsoleCtrlEvent`` instead of to any liveness check, so on windows the
POSIX idiom is not a probe — it delivers a console Ctrl+C to every process
sharing the console. Measured on a ``windows-latest`` runner:

    os.kill(child.pid, 0)   -> returned normally
    child, an idle time.sleep(30), one second later -> DEAD

``sweep_stale_prompt_dirs`` calls this on every delegation-parent startup
against pids read off temp-directory names. Those pids are stale by definition
and windows recycles them, so the old form could have interrupted an unrelated
process that inherited the number.

No ``skipif``: the branch is a ``sys.platform`` read and the win32 arm is
``ctypes`` against a ``WinDLL`` that does not exist here, so both are driven by
injection — the doctrine in ``tests/cli/test_stdio_encoding_win32.py``.
"""

from __future__ import annotations

import os
import sys

import pytest
from aelix_agents import prompt_file as pf


def test_windows_never_reaches_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: signal 0 must not be sent on the platform that arms it."""

    monkeypatch.setattr(pf.sys, "platform", "win32", raising=True)

    def _explode(*_a: object, **_k: object) -> None:
        raise AssertionError("os.kill reached on windows — this is CTRL_C_EVENT")

    monkeypatch.setattr(pf.os, "kill", _explode)
    monkeypatch.setattr(pf, "_pid_is_live_win32", lambda _pid: True)

    assert pf._pid_is_live(4321) is True


@pytest.mark.parametrize(
    ("handle", "wait_rc", "last_error", "expected", "why"),
    [
        (0, None, 87, False, "OpenProcess ERROR_INVALID_PARAMETER — no such pid"),
        (0, None, 5, True, "ACCESS_DENIED — exists, not ours; errs toward LIVE"),
        (123, 0x0, None, False, "WAIT_OBJECT_0 — the process object is signalled"),
        (123, 0x102, None, True, "WAIT_TIMEOUT — still running"),
    ],
)
def test_the_win32_arm_reads_the_process_object(
    handle: int,
    wait_rc: int | None,
    last_error: int | None,
    expected: bool,
    why: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``WaitForSingleObject`` semantics, and the errs-toward-LIVE contract.

    ``ctypes.WinDLL`` does not exist off windows, so the kernel32 handle is
    injected. That is the only way this arm is testable at all on the runners
    we have, and an untested arm here is how the original defect shipped.
    """

    closed: list[int] = []

    class _Kernel32:
        def OpenProcess(self, _access: int, _inherit: bool, _pid: int) -> int:  # noqa: N802
            return handle

        def WaitForSingleObject(self, _h: int, _ms: int) -> int:  # noqa: N802
            assert wait_rc is not None
            return wait_rc

        def CloseHandle(self, h: int) -> None:  # noqa: N802
            closed.append(h)

    fake_ctypes = type(
        "_C",
        (),
        {
            "WinDLL": staticmethod(lambda *_a, **_k: _Kernel32()),
            "get_last_error": staticmethod(lambda: last_error),
        },
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)  # type: ignore[arg-type]

    assert pf._pid_is_live_win32(4321) is expected, why
    # A handle that was opened must be closed; a failed open must not be.
    assert closed == ([handle] if handle else [])


def test_posix_still_uses_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """The windows branch must not become an unconditional rewrite."""

    monkeypatch.setattr(pf.sys, "platform", "linux", raising=True)
    seen: list[tuple[int, int]] = []

    def _spy(pid: int, sig: int) -> None:
        seen.append((pid, sig))

    monkeypatch.setattr(pf.os, "kill", _spy)

    assert pf._pid_is_live(4321) is True
    assert seen == [(4321, 0)]


def test_a_real_live_pid_reads_live() -> None:
    """One case against the real OS, so the fakes above cannot all be wrong."""

    assert pf._pid_is_live(os.getpid()) is True
