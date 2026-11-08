"""Read-only process identity: is a pid alive, when did it start, what is its argv (#722).

A pid alone names whatever process holds the number now; a pid plus its start
time names one process. The job service records both when a job registers a
process and compares them when a restart finds the job unfinished (ADR-0293
§restart): a live pid whose start time matches is that job's process, one whose
start time differs is a stranger that reused the number, and one whose start
time cannot be read is ambiguous. Nothing here signals anything.

* Linux: ``/proc/<pid>/stat`` field 22 (clock ticks since boot), read after the
  last ``)`` because the command name in field 2 may hold spaces and
  parentheses; argv from ``/proc/<pid>/cmdline``.
* macOS: ``proc_pidinfo(PROC_PIDTBSDINFO)``, whose ``pbi_start_tvsec`` and
  ``pbi_start_tvusec`` are the start time; argv is not read (``KERN_PROCARGS2``
  of a platform binary is refused even to its owner — measured in #611 round 3),
  so a match there is by start time alone.
* Windows: ``GetProcessTimes`` on a ``PROCESS_QUERY_LIMITED_INFORMATION``
  handle; argv is not read. Liveness by waiting on the process object, never by
  ``os.kill(pid, 0)``, which on Windows delivers a console Ctrl+C (ADR-0238).

Every function answers ``None`` when it cannot tell; none raises.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
from pathlib import Path

_PROC = Path("/proc")
_LIBPROC_PATH = "/usr/lib/libproc.dylib"
_PROC_PIDTBSDINFO = 3

_SYNCHRONIZE = 0x0010_0000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0
_ERROR_INVALID_PARAMETER = 87


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [  # noqa: RUF012 — the ctypes layout of ``struct proc_bsdinfo``
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def pid_is_live(pid: int) -> bool | None:
    """``True`` alive, ``False`` gone, ``None`` unknown. Never signals on Windows."""

    if pid <= 0:
        return None
    if sys.platform == "win32":
        return _win32_live(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def process_started(pid: int) -> float | None:
    """An opaque start-time value for ``pid``: equal for one process, different after reuse."""

    if pid <= 0:
        return None
    if _PROC.is_dir():
        return _linux_started(pid)
    if sys.platform == "darwin":
        return _darwin_started(pid)
    if sys.platform == "win32":
        return _win32_started(pid)
    return None


def process_argv(pid: int) -> list[str] | None:
    """``pid``'s argv where it is readable (Linux), else ``None``."""

    if pid <= 0 or not _PROC.is_dir():
        return None
    try:
        raw = (_PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return [part.decode("utf-8", errors="replace") for part in raw.rstrip(b"\0").split(b"\0")]


def same_process(pid: int, started: float | None) -> bool | None:
    """Whether ``pid`` is still the process that started at ``started``.

    ``True``: alive with the same start time. ``False``: gone, or the number now
    belongs to a process that started at another time. ``None``: cannot tell.
    """

    live = pid_is_live(pid)
    if live is False:
        return False
    if live is None or started is None:
        return None
    now = process_started(pid)
    if now is None:
        return None
    return now == started


def _linux_started(pid: int) -> float | None:
    try:
        stat = (_PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    _, _, rest = stat.rpartition(")")
    fields = rest.split()
    # ``rest`` starts at field 3 (state); field 22 is index 19 here.
    if len(fields) < 20:
        return None
    try:
        return float(int(fields[19]))
    except ValueError:
        return None


def _darwin_started(pid: int) -> float | None:
    try:
        lib = ctypes.CDLL(_LIBPROC_PATH, use_errno=True)
        info = _ProcBsdInfo()
        size = lib.proc_pidinfo(
            ctypes.c_int(pid),
            _PROC_PIDTBSDINFO,
            ctypes.c_uint64(0),
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
    except (OSError, AttributeError):
        return None
    if size != ctypes.sizeof(info):
        return None
    return float(info.pbi_start_tvsec) + float(info.pbi_start_tvusec) / 1_000_000


def _kernel32() -> object | None:
    with contextlib.suppress(Exception):
        return ctypes.WinDLL("kernel32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]
    return None


def _win32_live(pid: int) -> bool | None:
    kernel32 = _kernel32()
    if kernel32 is None:
        return None
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)  # type: ignore[attr-defined]
    if not handle:
        last = ctypes.get_last_error()  # pyright: ignore[reportAttributeAccessIssue]
        return last != _ERROR_INVALID_PARAMETER
    try:
        return kernel32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0  # type: ignore[attr-defined]
    finally:
        kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


def _win32_started(pid: int) -> float | None:
    kernel32 = _kernel32()
    if kernel32 is None:
        return None
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)  # type: ignore[attr-defined]
    if not handle:
        return None
    try:
        created = ctypes.c_uint64()
        exited = ctypes.c_uint64()
        kernel = ctypes.c_uint64()
        user = ctypes.c_uint64()
        ok = kernel32.GetProcessTimes(  # type: ignore[attr-defined]
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        return float(created.value) if ok else None
    finally:
        kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


__all__ = ["pid_is_live", "process_argv", "process_started", "same_process"]
