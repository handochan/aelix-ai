"""Read-only: this user's processes and one variable of a process's environment (#719, ADR-0296).

A delegated agent's run carries a token in ``AELIX_DELEGATION_MARKER``, and a
helper its ``bash`` started keeps it unless the helper clears its environment.
The run's cleanup finds such a helper by that token after it left the agent's
process group and its parent chain (``aelix_coding_agent.tools.launch_ledger``).
This module answers the two reads that takes, and signals nothing.

* :func:`own_pids` — the pids of this user's processes. Linux: the ``/proc``
  entries whose owner is our uid. macOS: ``sysctl(CTL_KERN, KERN_PROC,
  KERN_PROC_UID, uid)`` (M6 on the #611 S2 contract: 785 pids listed in 7.1 ms
  on macOS 26.6.2).
* :func:`environ_value` — one variable of ``pid``'s environment as the process
  was started. Linux: ``/proc/<pid>/environ``. macOS: ``KERN_PROCARGS2``, whose
  buffer holds ``argc``, the executable path, argv and then the environment. A
  platform binary (``/bin/sleep``, ``/bin/bash``) answered argv with an EMPTY
  environment on macOS 26.6.2 (M4), so a helper that is one is not found by its
  token there — ADR-0296 states it.

Both answer ``None`` when they cannot read; neither raises.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import os
import sys
from pathlib import Path

_PROC = Path("/proc")

#: ``sysctl`` names: ``CTL_KERN``, ``KERN_ARGMAX``, ``KERN_PROC``,
#: ``KERN_PROC_UID`` and ``KERN_PROCARGS2`` (``<sys/sysctl.h>``).
_CTL_KERN, _KERN_ARGMAX, _KERN_PROC, _KERN_PROC_UID, _KERN_PROCARGS2 = 1, 8, 14, 5, 49
#: ``sizeof(struct kinfo_proc)`` and the offset of ``kp_proc.p_pid`` in it on
#: 64-bit macOS (the M6 probe, ``.omc/probes/611-s2-design/uid_scan_probe.py``).
_KINFO_PROC_SIZE = 648
_P_PID_OFFSET = 40


def own_pids() -> list[int] | None:
    """The pids of this user's processes, or ``None`` when they cannot be listed."""

    if _PROC.is_dir():
        return _linux_own_pids()
    if sys.platform == "darwin":
        return _darwin_own_pids()
    return None


def environ_value(pid: int, name: str) -> str | None:
    """``name`` in ``pid``'s environment, ``""`` when absent, ``None`` when unreadable."""

    if pid <= 0:
        return None
    if _PROC.is_dir():
        try:
            raw = (_PROC / str(pid) / "environ").read_bytes()
        except OSError:
            return None
        entries = raw.split(b"\0")
    elif sys.platform == "darwin":
        found = _darwin_environ(pid)
        if found is None:
            return None
        entries = found
    else:
        return None
    prefix = name.encode() + b"="
    for entry in entries:
        if entry.startswith(prefix):
            return entry[len(prefix) :].decode("utf-8", errors="replace")
    return ""


def _linux_own_pids() -> list[int] | None:
    uid = os.getuid()  # pyright: ignore[reportAttributeAccessIssue]
    try:
        names = [entry.name for entry in _PROC.iterdir()]
    except OSError:
        return None
    pids: list[int] = []
    for name in names:
        if not name.isdigit():
            continue
        with contextlib.suppress(OSError):
            if (_PROC / name).stat().st_uid == uid:
                pids.append(int(name))
    return pids


@functools.cache
def _libc() -> ctypes.CDLL | None:
    try:
        return ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None


def _darwin_own_pids() -> list[int] | None:
    libc = _libc()
    if libc is None:
        return None
    uid = os.getuid()  # pyright: ignore[reportAttributeAccessIssue]
    mib = (ctypes.c_int * 4)(_CTL_KERN, _KERN_PROC, _KERN_PROC_UID, uid)
    try:
        size = ctypes.c_size_t(0)
        if libc.sysctl(mib, 4, None, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
        # Room for processes started between the two calls.
        buffer = ctypes.create_string_buffer(size.value + 64 * _KINFO_PROC_SIZE)
        size = ctypes.c_size_t(len(buffer))
        if libc.sysctl(mib, 4, buffer, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
    except (OSError, AttributeError):
        return None
    raw = buffer.raw
    count = size.value // _KINFO_PROC_SIZE
    pids: list[int] = []
    for index in range(count):
        at = index * _KINFO_PROC_SIZE + _P_PID_OFFSET
        pid = int.from_bytes(raw[at : at + 4], "little", signed=True)
        if pid > 0:
            pids.append(pid)
    return pids


@functools.cache
def _darwin_argmax() -> int:
    libc = _libc()
    value = ctypes.c_int(0)
    if libc is None:
        return 0
    size = ctypes.c_size_t(ctypes.sizeof(value))
    mib = (ctypes.c_int * 2)(_CTL_KERN, _KERN_ARGMAX)
    try:
        if libc.sysctl(mib, 2, ctypes.byref(value), ctypes.byref(size), None, ctypes.c_size_t(0)):
            return 0
    except (OSError, AttributeError):
        return 0
    return int(value.value)


def _darwin_environ(pid: int) -> list[bytes] | None:
    """``pid``'s environment from ``KERN_PROCARGS2``, or ``None`` when the kernel refuses it."""

    libc = _libc()
    argmax = _darwin_argmax()
    if libc is None or argmax <= 0:
        return None
    mib = (ctypes.c_int * 3)(_CTL_KERN, _KERN_PROCARGS2, pid)
    buffer = ctypes.create_string_buffer(argmax)
    size = ctypes.c_size_t(argmax)
    try:
        if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
    except (OSError, AttributeError):
        return None
    raw = buffer.raw[: size.value]
    if len(raw) < 4:
        return None
    argc = int.from_bytes(raw[:4], "little")
    rest = raw[4:]
    # The executable path, then NUL padding, then argv and the environment.
    at = rest.find(b"\0")
    if at < 0:
        return None
    while at < len(rest) and rest[at] == 0:
        at += 1
    strings = rest[at:].split(b"\0")
    environ: list[bytes] = []
    for entry in strings[argc:]:
        if not entry:
            break
        environ.append(entry)
    return environ


__all__ = ["environ_value", "own_pids"]
