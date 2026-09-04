"""Cross-platform "is this pid dead?" probe for the process-kill tests (#203).

WHAT WENT WRONG BEFORE.  Two tests that verify a killed child really died
polled ``/proc/<pid>/status`` and read the file's **absence** as proof of
death::

    if not os.path.exists(f"/proc/{pid}/status"):
        last_state = "gone"   # "fully reaped -- definitely dead"
        break

procfs does not exist on macOS and does not exist on Windows.  There the file
was never going to be there, so the loop took that branch on its first
iteration and both tests reported green while measuring nothing at all -- in
the process-tree-kill area, which is exactly where Windows has no process
groups (#105, #202).  The failure mode is the dangerous one: not a red test,
a *green* one.

THE RULE THIS MODULE ENCODES.  "I cannot tell" is never "it is dead".  Every
platform gets a real liveness question, and anything short of a definite
answer resolves to :data:`STATE_ALIVE`, which makes the caller keep polling
and ultimately fail on its own timeout.  A probe that errs toward ALIVE can
cost a slow test; a probe that errs toward GONE costs a test that never
fails, which is what #203 is.

No new dependency: ``psutil`` is not in ``pyproject.toml`` or ``uv.lock`` and
this module does not add it.

THE THREE ANSWERS.  :func:`probe_state` returns one of :data:`STATE_GONE`,
:data:`STATE_ZOMBIE`, :data:`STATE_ALIVE`.  ``GONE`` and ``ZOMBIE`` both mean
"dead" for a kill test -- a zombie holds nothing but an exit status; it takes
no signal and burns no CPU -- and :func:`is_dead_or_zombie` folds them
together.  They are kept apart because only one of them is decidable on Linux.

PLATFORM BRANCHES
-----------------

POSIX -- ``os.kill(pid, 0)`` is the primary question, and it is a real one
everywhere POSIX: signal 0 runs the permission and existence checks and
delivers nothing.  ``ProcessLookupError`` is the only proof of death.
``PermissionError`` means the pid exists and belongs to somebody else, so it
is ALIVE.  Note that ``os.kill(pid, 0)`` *succeeds* on a zombie, which is why
the original tests reached for procfs in the first place.

  * Linux: procfs stays the preferred path for the Z/X distinction, but it is
    consulted only when ``/proc`` is actually mounted (probed via
    ``/proc/self``).  When it is mounted and ``/proc/<pid>/status`` is missing,
    that *is* evidence -- the process was reaped between the ``os.kill`` and
    the open -- so GONE is returned.  When ``/proc`` is not mounted the module
    never looks at the per-pid path at all, so its absence can no longer be
    mistaken for death.
  * macOS/BSD: ``ps -o state= -p <pid>`` supplies the zombie distinction that
    procfs would have.  It is only consulted after ``os.kill`` already proved
    the pid exists, and it is only ever allowed to *upgrade* the answer to
    ZOMBIE -- an empty or failed ``ps`` returns ``None`` and the caller falls
    back to ALIVE, so the fallback can never manufacture a "gone".

win32 -- ``ctypes`` against ``kernel32``:

  * ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid)`` asks for a
    handle.  The *limited* mask is deliberate: it is granted for processes a
    plain user may not otherwise inspect, so a refusal really does mean
    "protected", not "missing".  A NULL handle with
    ``GetLastError() == ERROR_INVALID_PARAMETER`` is Windows' way of saying
    "no such pid" -> GONE.  Any other error (typically
    ``ERROR_ACCESS_DENIED``) means a process is there -> ALIVE.
  * ``GetExitCodeProcess(handle, &code)`` then reads the exit status.
    ``STILL_ACTIVE`` (259) means running -> ALIVE; anything else means the
    process has exited -> GONE.  Windows has no zombie state: an exited
    process with an open handle still answers this call, so GONE covers both
    of the POSIX cases.  The one blind spot is a process that genuinely exits
    with code 259, which reads as ALIVE -- erring toward ALIVE, per the rule
    above.

  ``os.kill(pid, 0)`` IS NOT A PROBE ON WINDOWS -- IT IS A KILL.  CPython's
  ``os_kill_impl`` special-cases only ``CTRL_C_EVENT`` and ``CTRL_BREAK_EVENT``
  (routed to ``GenerateConsoleCtrlEvent``); every other signal value, 0
  included, falls through to ``OpenProcess(PROCESS_ALL_ACCESS)`` +
  ``TerminateProcess(handle, sig)``.  So ``os.kill(pid, 0)`` on Windows
  terminates the target with exit code 0, or raises if it cannot.  Using it as
  a liveness check there would make the probe cause the death it is supposed
  to observe -- a second, worse way for these tests to pass without measuring.

  (``aelix_agents.prompt_file._pid_is_live_win32`` answers the same question
  in product code with ``SYNCHRONIZE`` + ``WaitForSingleObject``.  This module
  is test-only and deliberately does not import it: a test must not verify a
  kill through the same code it is trying to hold to account.)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any

__all__ = [
    "STATE_ALIVE",
    "STATE_GONE",
    "STATE_ZOMBIE",
    "await_dead_or_zombie",
    "is_dead_or_zombie",
    "probe_state",
]

#: The pid is not in the process table.
STATE_GONE = "gone"
#: The pid exists but holds nothing except an exit status (POSIX ``Z``/``X``).
STATE_ZOMBIE = "zombie"
#: The pid exists and is running -- or the platform could not prove otherwise.
STATE_ALIVE = "alive"

_DEAD_STATES = (STATE_GONE, STATE_ZOMBIE)

# ``OpenProcess`` access mask that a plain user is granted for processes it
# could not otherwise query, so that a refusal means "protected", not "gone".
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87


def probe_state(pid: int) -> str:
    """Return :data:`STATE_GONE`, :data:`STATE_ZOMBIE` or :data:`STATE_ALIVE`.

    Never answers GONE unless the platform actually said so. A pid that is
    not a pid (``<= 0``) is a caller bug and raises -- it must not read as
    "dead", which is exactly the shape of vacuity this module exists to end.
    See the module docstring for the per-platform reasoning.
    """

    if pid <= 0:
        raise ValueError(f"not a process id: {pid!r}")
    if sys.platform == "win32":
        return _probe_win32(pid)
    return _probe_posix(pid)


def is_dead_or_zombie(pid: int) -> bool:
    """``True`` when ``pid`` is gone or a zombie -- i.e. dead for kill tests."""

    return probe_state(pid) in _DEAD_STATES


async def await_dead_or_zombie(
    pid: int, *, timeout: float = 3.0, interval: float = 0.05
) -> str:
    """Poll ``pid`` until it is dead, returning the LAST OBSERVED state.

    The return value is the assertion subject: callers assert it is in
    (:data:`STATE_GONE`, :data:`STATE_ZOMBIE`) so the test fails -- on every
    platform -- when the child outlived the kill.  Signal delivery is
    asynchronous (the kernel schedules it), hence the poll rather than a
    single probe.
    """

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    state = probe_state(pid)
    while state not in _DEAD_STATES and loop.time() < deadline:
        await asyncio.sleep(interval)
        state = probe_state(pid)
    return state


# ---------------------------------------------------------------------------
# POSIX
# ---------------------------------------------------------------------------


def _probe_posix(pid: int) -> str:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # The only proof of death POSIX offers without a handle.
        return STATE_GONE
    except PermissionError:
        # Exists, owned by somebody else.
        return STATE_ALIVE
    except OSError:  # pragma: no cover -- defensive
        return STATE_ALIVE

    # The pid exists. Only the zombie question is left, and it is the one that
    # needs a per-platform source.
    for refine in (_procfs_state, _ps_state):
        refined = refine(pid)
        if refined is not None:
            return refined
    return STATE_ALIVE


def _procfs_state(pid: int) -> str | None:
    """Linux ``/proc/<pid>/status`` -- or ``None`` when procfs is not mounted.

    ``None`` is the whole point of this function: off Linux there is nothing
    here to read, and that says nothing about the process.
    """

    if not os.path.isdir("/proc/self"):
        return None
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("State:"):
                    code = line.split()[1]
                    return STATE_ZOMBIE if code in ("Z", "X") else STATE_ALIVE
    except FileNotFoundError:
        # procfs IS mounted and this pid has no entry: reaped in the window
        # between the os.kill above and this open. That is real evidence.
        return STATE_GONE
    except OSError:  # pragma: no cover -- defensive
        return None
    return None  # pragma: no cover -- status without a State: line


def _ps_state(pid: int) -> str | None:
    """macOS/BSD zombie detection via ``ps -o state=``.

    Only ever upgrades the answer to :data:`STATE_ZOMBIE`; it cannot report
    GONE.  ``os.kill`` above already established that the pid exists, and a
    ``ps`` that fails, is missing, or prints nothing must not be allowed to
    invent a death -- the caller's next poll iteration asks ``os.kill`` again.
    """

    ps_path = shutil.which("ps")
    if ps_path is None:  # pragma: no cover -- ps is present on macOS/BSD/Linux
        return None
    try:
        completed = subprocess.run(
            [ps_path, "-o", "state=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover -- defensive
        return None
    code = completed.stdout.strip()
    if not code:
        return None
    return STATE_ZOMBIE if code.startswith("Z") else STATE_ALIVE


# ---------------------------------------------------------------------------
# win32
# ---------------------------------------------------------------------------


def _load_kernel32() -> tuple[Any, Callable[[], int]]:
    """The real ``kernel32`` with explicit signatures, plus ``get_last_error``.

    Split out so :func:`_probe_win32` can be driven with a fake on every
    platform -- the decision logic is what the tests pin, not the DLL.
    """

    import ctypes

    # These suppressions are about the CHECKER, not about doubt at runtime:
    # ``WinDLL`` and ``get_last_error`` exist only on windows, and the type
    # gate analyses every file as Linux. The ``sys.platform`` guard in
    # :func:`probe_state` is what makes this branch unreachable elsewhere;
    # pyright cannot narrow into a separate function, so it has to be told.
    kernel32 = ctypes.WinDLL(  # pyright: ignore[reportAttributeAccessIssue]
        "kernel32", use_last_error=True
    )
    # Explicit signatures: a HANDLE is pointer-sized, and ctypes' default
    # ``c_int`` restype would truncate it on Win64.
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32, ctypes.get_last_error  # pyright: ignore[reportAttributeAccessIssue]


def _probe_win32(
    pid: int,
    *,
    kernel32: Any | None = None,
    get_last_error: Callable[[], int] | None = None,
) -> str:
    """Liveness via a process handle. Never via ``os.kill`` -- see module doc.

    ``kernel32``/``get_last_error`` are injection seams for the tests; the
    real DLL is loaded when they are omitted.
    """

    import ctypes

    if kernel32 is None or get_last_error is None:
        kernel32, get_last_error = _load_kernel32()

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        last_error = get_last_error()
        # Only "no such pid" is proof of death; ACCESS_DENIED and friends mean
        # a process is there and is not ours to inspect.
        return STATE_GONE if last_error == _ERROR_INVALID_PARAMETER else STATE_ALIVE
    try:
        exit_code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return STATE_ALIVE
        # No zombie state on Windows: an exited process with a live handle
        # still reports its code, so "not STILL_ACTIVE" covers Z and X both.
        return STATE_ALIVE if exit_code.value == _STILL_ACTIVE else STATE_GONE
    finally:
        kernel32.CloseHandle(handle)
