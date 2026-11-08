"""Read-only: a process's live descendants and the groups they lead (#722, ADR-0293).

Moved down from ``aelix_agents/reaper.py`` (#691, ADR-0291), which re-exports
:func:`descendant_pids` and :func:`group_leaders` and keeps its own module
names as the seams its tests patch. The job service's stop evidence (#611 S1b)
reads the same walk: it is collected while a job's root still lives and before
anything is signalled, so a descendant that leaves the root's group is still
named when its root dies (Codex #4).

Nothing here signals. Linux reads ``/proc/<pid>/status``'s ``PPid:`` line (not
``stat``, whose name field may hold spaces and parentheses); macOS asks the
kernel for each process's children through libproc's ``proc_listpids``
(``PROC_PPID_ONLY``); Windows has neither, and the job object is its tree.
Neither walk is one snapshot: a process forked between two reads can be missed,
the race ADR-0197 accepts for the walk.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

PROC = Path("/proc")

LIBPROC_PATH = "/usr/lib/libproc.dylib"
"""Where macOS exports ``proc_listpids``; ``dlopen`` resolves it from the dyld
shared cache (there is no such file on disk since macOS 11)."""

PROC_PPID_ONLY = 6
"""``proc_listpids``'s filter from ``<sys/proc_info.h>``: the processes whose
parent is the ``typeinfo`` argument."""

FIRST_CAPACITY = 64
"""Pids :func:`darwin_children` asks for first; a full answer is asked again at
twice the size, since the kernel drops what does not fit without saying so."""

MAX_CAPACITY = 1 << 17
"""Where that doubling stops: above any ``kern.maxproc``."""


def descendant_pids(
    pid: int,
    *,
    proc: Path = PROC,
    is_darwin: Callable[[], bool] | None = None,
    libproc: Callable[[], Any] | None = None,
) -> list[int]:
    """Every live descendant of ``pid``, DEEPEST FIRST; ``[]`` on Windows and on any read error.

    Deepest first so a grandchild is signalled before its parent, which could
    otherwise leave it reparented and outside a later walk. ``/proc`` is tried
    first, so a Linux host walks it whatever platform a test injects.
    """

    if proc.is_dir():
        children = proc_children(proc)
        return deepest_first(pid, lambda parent: children.get(parent, ()))
    darwin = is_darwin() if is_darwin is not None else sys.platform == "darwin"
    if darwin:
        lib = libproc() if libproc is not None else load_libproc()
        if lib is None:
            return []
        return deepest_first(pid, lambda parent: darwin_children(lib, parent))
    return []


def deepest_first(pid: int, children_of: Callable[[int], Iterable[int]]) -> list[int]:
    """Breadth-first from ``pid``, then reversed: deepest generation first.

    ``seen`` guards against the cycle a pid-reuse race can make in the reads.
    """

    ordered: list[int] = []
    seen: set[int] = {pid}
    frontier = list(children_of(pid))
    while frontier:
        nxt: list[int] = []
        for candidate in frontier:
            if candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)
            nxt.extend(children_of(candidate))
        frontier = nxt
    ordered.reverse()
    return ordered


@functools.cache
def load_libproc() -> ctypes.CDLL | None:
    """libproc with ``proc_listpids`` typed, or ``None`` when it cannot be loaded."""

    try:
        lib = ctypes.CDLL(LIBPROC_PATH, use_errno=True)
        listpids = lib.proc_listpids
    except (OSError, AttributeError):
        return None
    listpids.argtypes = (ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int)
    listpids.restype = ctypes.c_int
    return lib


def darwin_children(libproc: Any, pid: int) -> list[int]:
    """``pid``'s children, from ``proc_listpids(PROC_PPID_ONLY, pid, …)``.

    The call answers in BYTES and ``0`` on failure, an unknown pid included. A
    zombie child is listed too; signalling one is harmless.
    """

    width = ctypes.sizeof(ctypes.c_int)
    capacity = FIRST_CAPACITY
    while True:
        buffer = (ctypes.c_int * capacity)()
        filled = libproc.proc_listpids(PROC_PPID_ONLY, pid, buffer, capacity * width)
        if filled <= 0:
            return []
        count = min(filled // width, capacity)
        if count < capacity or capacity >= MAX_CAPACITY:
            return [child for child in buffer[:count] if child > 0]
        capacity *= 2


def proc_children(proc: Path = PROC) -> dict[int, list[int]]:
    """The ``/proc`` ``PPid`` index: parent pid → its children's pids."""

    parents: dict[int, int] = {}
    try:
        entries = list(proc.iterdir())
    except OSError:
        return {}
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            # The process exited between the listing and the read — normal.
            continue
        for line in status.splitlines():
            if line.startswith("PPid:"):
                with contextlib.suppress(ValueError):
                    parents[int(name)] = int(line.split()[1])
                break

    children: dict[int, list[int]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    return children


def group_leaders(pids: Iterable[int], *, platform: str | None = None) -> list[int]:
    """Those of ``pids`` that lead their own process group, read NOW; ``[]`` on Windows.

    Taken right after the walk, before any signal, while every pid in it was
    just seen alive (ADR-0291): a group named here holds what the walk cannot —
    a ``(cmd &)`` whose parent is gone. ``getpgid`` failing is "not a leader".
    """

    if (platform if platform is not None else sys.platform) == "win32":
        return []
    leaders: list[int] = []
    for pid in pids:
        with contextlib.suppress(OSError):
            if os.getpgid(pid) == pid:  # pyright: ignore[reportAttributeAccessIssue]
                leaders.append(pid)
    return leaders


__all__ = [
    "FIRST_CAPACITY",
    "LIBPROC_PATH",
    "MAX_CAPACITY",
    "PROC",
    "PROC_PPID_ONLY",
    "darwin_children",
    "deepest_first",
    "descendant_pids",
    "group_leaders",
    "load_libproc",
    "proc_children",
]
