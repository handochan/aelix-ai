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

#611 S2a: :func:`descendant_identities` is the same walk with each process's
start time, read in the SAME kernel read as its parent (``/proc/<pid>/stat`` on
Linux, ``proc_pidinfo(PROC_PIDTBSDINFO)`` on macOS), and a child kept only
while that parent is the one being walked. A stop leg then signals a walked
process only while it still has that start time (``_process_signal``), so a
number handed on between the walk and the kill is not hit. What it cannot see
is a parent whose number was reused between its own read and its children's
(ADR-0291's note, R3). :func:`group_members` lists a group's processes for the
barrier's zombie rule.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NamedTuple

from aelix_ai.utils import _process_identity

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

PROC_PGRP_ONLY = 2
"""``proc_listpids``'s filter for the processes of group ``typeinfo``. A zombie
is listed too (measured on macOS, #611 S2a)."""


class WalkEntry(NamedTuple):
    """One walked process: its pid, its start time and its parent, read together.

    ``stamp`` is ``None`` when the start time could not be read; such an entry
    is never signalled by pid (#611 S2a).
    """

    pid: int
    stamp: float | None
    ppid: int | None


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


def descendant_identities(
    pid: int,
    *,
    proc: Path = PROC,
    is_darwin: Callable[[], bool] | None = None,
    libproc: Callable[[], Any] | None = None,
    identity: Callable[[int], tuple[int, float | None] | None] | None = None,
) -> list[WalkEntry]:
    """:func:`descendant_pids` with each process's start time, DEEPEST FIRST.

    Linux: one ``/proc/<pid>/stat`` read per process gives its parent and its
    start time, and the parent index is built from those reads. macOS: the
    kernel's children of each walked parent, each kept only if one
    ``proc_pidinfo`` read still names that parent (a zombie, whose info the
    kernel no longer answers, is dropped: it runs nothing). ``[]`` on Windows
    and on any read error.
    """

    entries: dict[int, WalkEntry] = {}
    if proc.is_dir():
        index = proc_stat_children(proc)

        def children_of(parent: int) -> list[int]:
            kids = index.get(parent, [])
            for entry in kids:
                entries.setdefault(entry.pid, entry)
            return [entry.pid for entry in kids]

    else:
        darwin = is_darwin() if is_darwin is not None else sys.platform == "darwin"
        lib = (libproc() if libproc is not None else load_libproc()) if darwin else None
        if lib is None:
            return []
        read = identity if identity is not None else _process_identity.process_identity

        def children_of(parent: int) -> list[int]:
            kept: list[int] = []
            for child in darwin_children(lib, parent):
                ident = read(child)
                if ident is None or ident[0] != parent:
                    continue
                entries.setdefault(child, WalkEntry(child, ident[1], parent))
                kept.append(child)
            return kept

    return [entries[p] for p in deepest_first(pid, children_of)]


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


def proc_stat_children(proc: Path = PROC) -> dict[int, list[WalkEntry]]:
    """The ``/proc`` parent index from ``stat``: parent pid → its children, stamped.

    One read per process gives the parent and the start time together, so an
    entry's stamp is the start time of the process that had that parent.
    ``stat``'s name field may hold spaces and parentheses; the fields are read
    after its LAST ``)``.
    """

    children: dict[int, list[WalkEntry]] = {}
    try:
        names = [entry.name for entry in proc.iterdir()]
    except OSError:
        return {}
    for name in names:
        if not name.isdigit():
            continue
        try:
            stat = (proc / name / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # exited between the listing and the read — normal
        fields = stat.rpartition(")")[2].split()
        if len(fields) < 20:
            continue
        try:
            parent, stamp = int(fields[1]), float(int(fields[19]))
        except ValueError:
            continue
        children.setdefault(parent, []).append(WalkEntry(int(name), stamp, parent))
    return children


def group_members(pgid: int, *, proc: Path = PROC) -> list[int] | None:
    """The pids in process group ``pgid``, zombies included; ``None`` when they cannot be listed.

    Linux: ``/proc/<pid>/stat`` field 5. macOS: ``proc_listpids(PROC_PGRP_ONLY)``.
    The listing is all or nothing (#758 fix round 1, Codex #7): a process whose
    ``stat`` exists but cannot be read, or reads short, might be a live member,
    so the answer is ``None`` (unknown), never a list without it. A process that
    exited between the listing and the read is simply not a member.
    """

    if proc.is_dir():
        members: list[int] = []
        try:
            names = [entry.name for entry in proc.iterdir()]
        except OSError:
            return None
        for name in names:
            if not name.isdigit():
                continue
            try:
                stat = (proc / name / "stat").read_text(encoding="utf-8", errors="replace")
            except (FileNotFoundError, ProcessLookupError):
                continue  # exited between the listing and the read
            except OSError:
                return None  # unreadable: it may be a live member
            fields = stat.rpartition(")")[2].split()
            if len(fields) < 3:
                return None
            if fields[2] == str(pgid):
                members.append(int(name))
        return members
    if sys.platform != "darwin":
        return None
    lib = load_libproc()
    if lib is None:
        return None
    width = ctypes.sizeof(ctypes.c_int)
    capacity = FIRST_CAPACITY
    while True:
        buffer = (ctypes.c_int * capacity)()
        filled = lib.proc_listpids(PROC_PGRP_ONLY, pgid, buffer, capacity * width)
        if filled < 0:
            return None
        count = min(filled // width, capacity)
        if count < capacity or capacity >= MAX_CAPACITY:
            return [member for member in buffer[:count] if member > 0]
        capacity *= 2


def group_only_zombies(pgid: int) -> bool:
    """Whether every process left in group ``pgid`` is a zombie (and at least one is listed).

    The barrier's confirm step counts such a group as empty (#611 S2a, r2 §4.3):
    on macOS ``killpg(g, 0)`` answers ``EPERM`` for it and on Linux success,
    both of which read as "a member lives". A listing that is unknown or empty,
    or a member whose state cannot be read, is not "only zombies".
    """

    members = group_members(pgid)
    if not members:
        return False
    return all(_process_identity.pid_is_zombie(member) is True for member in members)


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
    "PROC_PGRP_ONLY",
    "PROC_PPID_ONLY",
    "WalkEntry",
    "darwin_children",
    "deepest_first",
    "descendant_identities",
    "descendant_pids",
    "group_leaders",
    "group_members",
    "group_only_zombies",
    "load_libproc",
    "proc_children",
    "proc_stat_children",
]
