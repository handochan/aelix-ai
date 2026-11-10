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

#611 S2a adds three reads the stop legs need and one way to send: whether a
pid is a zombie (:func:`pid_is_zombie`, which the barrier's confirm step alone
counts as gone), a pid's parent and start time from ONE kernel read
(:func:`process_identity`, the walk's entries), whether a recorded group
leader still gives authority over its group (:func:`leader_authorizes`), and on
Linux a pidfd acquired for a pid only when it still has the stamp a walk
recorded (:func:`acquire_pidfd`). A signal sent through that pidfd reaches that
process or nothing; no other function here signals.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

_PROC = Path("/proc")
_LIBPROC_PATH = "/usr/lib/libproc.dylib"
_PROC_PIDTBSDINFO = 3

#: ``sysctl(CTL_KERN, KERN_PROC, KERN_PROC_PID, pid)``: macOS's ``kinfo_proc``
#: is the one read that names a zombie there — ``proc_pidinfo`` answers ``ESRCH``
#: for one, and ``kill(pid, 0)`` succeeds (measured, #611 S2a).
_CTL_KERN, _KERN_PROC, _KERN_PROC_PID = 1, 14, 1
_KINFO_PROC_SIZE = 648
#: ``kp_proc.p_stat``: after ``p_un`` (16), ``p_vmspace`` (8), ``p_sigacts`` (8)
#: and ``p_flag`` (4). ``SZOMB`` is 5 (``<sys/proc.h>``).
_P_STAT_OFFSET = 36
_SZOMB = 5

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


def pid_is_zombie(pid: int) -> bool | None:
    """Whether ``pid`` has exited and waits to be reaped. ``None`` when it cannot tell.

    Linux: ``/proc/<pid>/stat``'s state field is ``Z``. macOS: ``kinfo_proc``'s
    ``p_stat`` is ``SZOMB``. A pid that is gone is not a zombie (``False``).
    Read only by the barrier's confirm step (#611 S2a, r2 §4.3): a zombie holds
    its number and its group until its parent reaps it, so ``kill(pid, 0)`` and
    ``killpg(g, 0)`` answer as if it ran, but it runs nothing.
    """

    if pid <= 0:
        return None
    if _PROC.is_dir():
        fields = _linux_stat(pid)
        if fields is None:
            return False if pid_is_live(pid) is False else None
        return fields[0] == "Z"
    if sys.platform == "darwin":
        return _darwin_zombie(pid)
    return None


def process_identity(pid: int) -> tuple[int, float | None] | None:
    """``pid``'s parent and start time from ONE kernel read, or ``None``.

    Linux: one ``/proc/<pid>/stat`` read (fields 4 and 22). macOS: one
    ``proc_pidinfo(PROC_PIDTBSDINFO)`` (``pbi_ppid`` and the start time, the
    same value :func:`process_started` answers). The walk keeps a child only if
    the parent read here is the one it is walking (#611 S2a, Codex N3).
    """

    if pid <= 0:
        return None
    if _PROC.is_dir():
        fields = _linux_stat(pid)
        if fields is None or len(fields) < 20:
            return None
        try:
            return int(fields[1]), float(int(fields[19]))
        except ValueError:
            return None
    if sys.platform == "darwin":
        info = _darwin_bsdinfo(pid)
        if info is None:
            return None
        return int(info.pbi_ppid), _darwin_stamp(info)
    return None


def leader_authorizes(
    pid: int, stamp: float | None, *, started: Callable[[int], float | None] | None = None
) -> bool:
    """Whether a group recorded with leader ``pid`` (start time ``stamp``) may be signalled.

    A start time read now decides first: the recorded one again is authority,
    another one is a reused number and never is (a positive reading of another
    process outranks a separate liveness read). With either unknown, a leader
    that is gone, or a zombie, is "gone", never "moved": the kernel keeps a
    group's number while any member lives, so the group is still the one
    recorded, and it is signalled (the owner's OQ-D answer, 2026-11-10, with its
    residual stated in ADR-0293's 2026-11-10 note). A live leader whose
    recorded start time is null, or whose current one cannot be read, is never
    authority (#611 S2a, PE3). ``started`` is the start-time reader a caller
    injected (``ProcessTree.stop_and_collect``'s).
    """

    now = (started if started is not None else process_started)(pid)
    if now is not None and stamp is not None:
        return now == stamp
    return pid_is_zombie(pid) is True or pid_is_live(pid) is False


PidfdAnswer = Literal["held", "stranger", "fallback"]


def acquire_pidfd(pid: int, stamp: float | None) -> tuple[PidfdAnswer, int | None]:
    """Linux: a pidfd for ``pid`` when it is still the process that started at ``stamp``.

    ``("held", fd)``: ``pidfd_open``, then the start time read, then the pidfd
    seen live — so the fd named that number for the whole read, and a matching
    stamp makes it that process (r3 §4.2). ``("stranger", None)``: gone, or
    another process, or a ``stamp`` never recorded; nothing may be sent to the
    number. ``("fallback", None)``: no pidfds here (macOS, Windows, an old
    kernel, ``EMFILE``); the caller compares the stamp right before each signal.
    The caller closes a held fd.
    """

    if stamp is None:
        return "stranger", None
    opener = getattr(os, "pidfd_open", None)
    sender = getattr(signal, "pidfd_send_signal", None)
    if opener is None or sender is None:
        return "fallback", None
    try:
        fd = opener(pid)
    except ProcessLookupError:
        return "stranger", None
    except OSError:
        return "fallback", None
    try:
        now = process_started(pid)
        sender(fd, 0)
    except OSError:
        now = None
    if now is None or now != stamp:
        os.close(fd)
        return "stranger", None
    return "held", fd


def pidfd_live(fd: int) -> bool:
    """Whether the process a held pidfd names has not been reaped (signal 0)."""

    sender = getattr(signal, "pidfd_send_signal", None)
    if sender is None:  # pragma: no cover — a held fd implies the call exists
        return False
    try:
        sender(fd, 0)
    except OSError:
        return False
    return True


def pidfd_signal(fd: int, sig: int) -> None:
    """Send ``sig`` through a held pidfd: that process, or nothing. Never raises."""

    sender = getattr(signal, "pidfd_send_signal", None)
    if sender is None:  # pragma: no cover — a held fd implies the call exists
        return
    with contextlib.suppress(OSError):
        sender(fd, sig)


def _linux_stat(pid: int) -> list[str] | None:
    """``/proc/<pid>/stat`` from field 3 (state) on, split; ``None`` when unreadable."""

    try:
        stat = (_PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    _, _, rest = stat.rpartition(")")
    fields = rest.split()
    return fields or None


@functools.cache
def _libproc() -> ctypes.CDLL | None:
    """libproc, loaded once: a stop leg reads one ``proc_pidinfo`` per walked process."""

    try:
        return ctypes.CDLL(_LIBPROC_PATH, use_errno=True)
    except OSError:
        return None


@functools.cache
def _libc() -> ctypes.CDLL | None:
    try:
        return ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None


def _darwin_bsdinfo(pid: int) -> _ProcBsdInfo | None:
    lib = _libproc()
    if lib is None:
        return None
    try:
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
    return info


def _darwin_stamp(info: _ProcBsdInfo) -> float:
    return float(info.pbi_start_tvsec) + float(info.pbi_start_tvusec) / 1_000_000


def _darwin_zombie(pid: int) -> bool | None:
    libc = _libc()
    if libc is None:
        return None
    try:
        mib = (ctypes.c_int * 4)(_CTL_KERN, _KERN_PROC, _KERN_PROC_PID, pid)
        buffer = ctypes.create_string_buffer(_KINFO_PROC_SIZE)
        size = ctypes.c_size_t(_KINFO_PROC_SIZE)
        failed = libc.sysctl(mib, 4, buffer, ctypes.byref(size), None, ctypes.c_size_t(0))
    except (OSError, AttributeError):
        return None
    if failed:
        return None
    if size.value == 0:
        return False  # no such process
    if size.value != _KINFO_PROC_SIZE:
        return None
    return buffer.raw[_P_STAT_OFFSET] == _SZOMB


def _linux_started(pid: int) -> float | None:
    fields = _linux_stat(pid)
    # ``fields`` starts at field 3 (state); field 22 is index 19 here.
    if fields is None or len(fields) < 20:
        return None
    try:
        return float(int(fields[19]))
    except ValueError:
        return None


def _darwin_started(pid: int) -> float | None:
    info = _darwin_bsdinfo(pid)
    return None if info is None else _darwin_stamp(info)


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


__all__ = [
    "PidfdAnswer",
    "acquire_pidfd",
    "leader_authorizes",
    "pid_is_live",
    "pid_is_zombie",
    "pidfd_live",
    "pidfd_signal",
    "process_argv",
    "process_identity",
    "process_started",
    "same_process",
]
