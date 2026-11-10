"""A delegated agent's launch ledger: what its ``bash`` started stops with it (#719, ADR-0296).

THE DECISION (owner, 2026-11-07, the #719 decision comment): helpers that a
delegated child's ``bash`` detached with ``cmd &``, ``(cmd &)`` or
``nohup cmd &`` stop when that child ends or is stopped; a top-level session's
stay released (#232). The child's ``bash`` records the process group in a launch
ledger beside the child session, right before and right after the spawn; the
parent checks the ledger plus the delegation's env marker after the child ends
or stops; on Linux the delegated child becomes a subreaper. Shipped ungated for
foreground delegations (owner OQ-A, 2026-11-10).

WHO ARMS IT. Only the delegation parent (``aelix_agents.print_channel``) puts
:data:`LEDGER_ENV` into a child's environment, and only a process that finds it
there at startup is ARMED (:func:`arm_from_environment`, called by
``cli/entry.py``). An unarmed process — the top-level aelix, its REPL ``!``, RPC
``exec`` — takes none of the paths below: :func:`writer` is ``None``, and
``tools/bash.py`` spawns and passes the environment byte for byte as before.

THE RECORDS. One JSON object per line, appended (``O_APPEND``), each written
whole or the writer stops writing (:class:`LaunchWriter`)::

    {"v":1,"t":"open","run":"<run key>","parent":<pid>}              parent, before the spawn
    {"v":1,"t":"armed","pid":<pid>,"subreaper":true|false|null}       agent, at startup
    {"v":1,"t":"intent","n":<n>}                                      agent, before Popen
    {"v":1,"t":"launch","n":<n>,"pid":P,"pgid":G,"started":<s|null>}  agent, right after Popen
    {"v":1,"t":"failed","n":<n>}                                      agent, the spawn raised
    {"v":1,"t":"retired","n":<n>}                                     agent, group seen empty
    {"v":1,"t":"exit","cleanup":"confirmed"|"unverified","reason":…,"killed":k}
    {"v":1,"t":"swept","confirmed":false,"reason":…}                  parent, when it keeps the file
    {"v":1,"t":"damaged"}                                             agent, once, after a failed write
    {"v":1,"t":"compacted"}                                           agent, heading a compacted file

The marker's value is never written: the parent keeps it in memory and the
child has it in its environment. The file stays small: the writer compacts it
by an atomic replace once 512 launches have closed, keeping the live ones
(:class:`LaunchWriter`), and the parent reads at most 2 MiB of it (:func:`read`).

AUTHORITY BEFORE EVERY SIGNAL (r3 §3.5). A ledger group is signalled only when it
is not retired, still has a member, and its leader — the shell ``bash`` started,
which leads its own session — either still has the start time the ``launch``
line recorded, or is gone (or a zombie). A live leader whose recorded start time
is unknown or different is never signalled (``leader identity unknown`` /
``leader number moved``). A gone leader's non-empty group is killed (owner
OQ-D, 2026-11-10): the residual is a group that emptied after its last retire
check, whose number went to a new process that made itself a group leader and
started members. A marker hit is a process of this user whose environment holds
this run's exact token, read between two equal readings of its identity,
started no earlier than the agent, and is never the scanner, its parent or an
ancestor, by number (:func:`vetoed`; Codex N1, and Codex 1 and 2 of #719's fix
round 1); it is verified before its descendants are walked, and it and they
are signalled through S2a's identity-checked legs
(:class:`~aelix_ai.utils._process_signal.HeldWalk`), the groups they lead first.
A scan that could not list this user's processes is a reason, never "nothing
found".

THE AGENT'S SIDE (r3 §3.3). On Linux an armed agent is a child subreaper
(``PR_SET_CHILD_SUBREAPER``), so an orphan of its ``bash`` — a double fork, a
``setsid``, one that cleared its environment — is reparented to it rather than
to init. It reaps nothing it did not spawn (the lead's call: no zombie reaping
during the run), so an adopted helper that exits stays a zombie under it until
it exits. A retire tick (the lead's call: every :data:`RETIRE_SECONDS`) and every
``bash`` call's end mark the groups that answer ``ESRCH``. At its end
(:func:`exit_cleanup`, from ``modes/print_mode.py``'s dispose) it walks its own
descendants and kills them, groups first, at most :data:`EXIT_PASSES` times
(the lead's call) until a pass finds nothing, then the ledger groups and the
marker hits, then confirms within :data:`CONFIRM_SECONDS` (a zombie is gone)
and writes ``exit`` — also on an exit that never reaches that dispose (an
``atexit`` hook arming registers). A group whose ``launch`` line could not be
written is stopped when its call returns rather than released. An agent that
dies without its cleanup (a crash, ``os._exit``, a SIGKILL that was not aelix's
own stop) writes no ``exit``, and the parent's answer is ``unverified``.

NOT ON WINDOWS. The delegation's Job (``kill_on_close=True``) holds every
descendant there; no ledger is created on win32 (r2 §3.6).
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import ctypes
import json
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.utils import _process_env, _process_walk
from aelix_ai.utils._process_identity import (
    pid_is_live,
    pid_is_zombie,
    process_identity,
    process_started,
    same_process,
)
from aelix_ai.utils._process_signal import HeldWalk
from aelix_ai.utils._process_walk import WalkEntry

LEDGER_ENV = "AELIX_LAUNCH_LEDGER"
"""The ledger's path, in an armed agent's environment. Internal: an armed
agent's ``bash`` commands never see it."""
MARKER_ENV = "AELIX_DELEGATION_MARKER"
"""``<token>[,<token>…]``: each delegation appends its run's token, never
replaces one, and nothing strips it, so a helper that keeps its environment is
found by it."""
PROTOCOL_ENV = "AELIX_JOBS_PROTOCOL"
"""Stripped from an armed agent's ``bash`` commands with :data:`LEDGER_ENV`
(r2 §3.2; Codex #11: only when armed)."""
LEDGER_SUFFIX = ".launch-ledger"
FALLBACK_NAME = "launch-ledger"
"""The name inside the per-spawn prompt directory when the run has no child
session file; the run's cleanup reads it before it removes that directory."""
VERSION = 1
RETIRE_SECONDS = 2.0
"""The retire tick (the lead's call)."""
EXIT_PASSES = 3
"""Walk-then-kill passes of the agent's exit cleanup (the lead's call)."""
CONFIRM_SECONDS = 2.0
"""How long a sweep waits for what it signalled to be gone."""
TARGETS_SHOWN = 16
"""Targets an ``unverified`` answer names before it gives a count."""
COMPACT_AFTER = 512
"""Launches retired or failed since the last compaction before the writer rewrites the file."""
ACTIVE_BOUND = 1024
"""Live launch groups the writer leaves running past their call; a later one is
stopped when its call returns, like one whose ``launch`` line was lost."""
READ_BOUND = 2 << 20
"""Bytes a parent reads of a ledger; the writer's compaction keeps it far below."""
LINE_BOUND = 4096
"""Bytes of one record; a longer line is a damaged ledger."""
COMPACT_SUFFIX = ".compact"
"""The temporary file a compaction writes beside the ledger before its replace."""

LEADER_UNKNOWN = "leader identity unknown"
LEADER_MOVED = "leader number moved"
IN_FLIGHT = "a launch was in flight"
DAMAGED = "the launch ledger is damaged"
NOT_ARMED = "the agent did not arm its launch ledger"
NO_EXIT_CLEANUP = "the agent ended without its own cleanup"
NOT_SUBREAPER = "the agent was not a subreaper"
NOT_CREATED = "the launch ledger could not be created"
SCAN_FAILED = "this user's processes could not be listed"

_PR_SET_CHILD_SUBREAPER = 36
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


# ── the file ──────────────────────────────────────────────────────


def _line(record: Mapping[str, Any]) -> bytes:
    return (json.dumps({"v": VERSION, **record}, separators=(",", ":")) + "\n").encode()


def _write_all(fd: int, data: bytes) -> None:
    """``data`` whole, looping over short writes; raises ``OSError``."""

    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def create(path: str, *, run: str, parent: int | None = None) -> bool:
    """The parent's ``open`` line in a new file (``O_EXCL``, 0600). ``False`` when it cannot."""

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        return False
    try:
        _write_all(fd, _line({"t": "open", "run": run, "parent": parent or os.getpid()}))
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


def append(path: str, record: Mapping[str, Any]) -> bool:
    """One more line (the parent's ``swept``). ``False`` when it could not be written."""

    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | _O_NOFOLLOW | _O_CLOEXEC)
    except OSError:
        return False
    try:
        _write_all(fd, _line(record))
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


@dataclass(frozen=True)
class Launch:
    """One spawn the ledger names: its shell and the group that shell leads."""

    n: int
    pid: int
    pgid: int
    started: float | None


@dataclass
class Reading:
    """What a ledger says. Every line that parses is acted on; one that does not is ``damaged``.

    Only what a sweep needs is kept: a ``retired`` launch and a ``failed``
    intent are dropped as they are read (``retired_count`` counts them), so the
    indexes hold the live launches and the intents still in flight.
    """

    opened: bool = False
    armed: dict[str, Any] | None = None
    intents: set[int] = field(default_factory=set)
    launches: dict[int, Launch] = field(default_factory=dict)
    failed: set[int] = field(default_factory=set)
    retired: set[int] = field(default_factory=set)
    retired_count: int = 0
    exit: dict[str, Any] | None = None
    damaged: bool = False

    @property
    def in_flight(self) -> set[int]:
        """An ``intent`` with neither a ``launch`` nor a ``failed``: its group is unknown."""

        return self.intents - set(self.launches) - self.failed


def read(path: str) -> Reading | None:
    """The ledger at ``path``, streamed and bounded, or ``None`` when there is no such file.

    At most :data:`READ_BOUND` bytes and :data:`LINE_BOUND` per line are read
    (the writer compacts long before either, :class:`LaunchWriter`); a ledger
    past either is ``damaged`` and what was read is still acted on. Blocking:
    the parent calls it on a worker thread.
    """

    try:
        handle = open(path, "rb")  # noqa: SIM115 — closed below, read line by line
    except OSError:
        return None
    reading = Reading()
    total = 0
    with handle:
        while True:
            try:
                line = handle.readline(LINE_BOUND + 1)
            except OSError:
                reading.damaged = True
                break
            if not line:
                break
            total += len(line)
            if total > READ_BOUND or not line.endswith(b"\n"):
                # Past the bound, a line longer than any record, or a last
                # line with no newline: a write cut short.
                reading.damaged = True
                break
            if not _fold(reading, line[:-1]):
                reading.damaged = True
    return reading


def _fold(reading: Reading, line: bytes) -> bool:
    try:
        record = json.loads(line)
    except ValueError:
        return False
    if not isinstance(record, dict) or record.get("v") != VERSION:
        return False
    kind = record.get("t")
    n = record.get("n")
    if kind == "open":
        reading.opened = True
    elif kind == "armed":
        reading.armed = record
    elif kind == "exit":
        reading.exit = record
    elif kind in ("swept", "compacted"):
        pass
    elif kind == "damaged":
        reading.damaged = True
    elif not isinstance(n, int):
        return False
    elif kind == "intent":
        reading.intents.add(n)
    elif kind == "failed":
        reading.intents.discard(n)
    elif kind == "retired":
        reading.intents.discard(n)
        reading.launches.pop(n, None)
        reading.retired_count += 1
    elif kind == "launch":
        return _fold_launch(reading, n, record)
    else:
        return False
    return True


def _fold_launch(reading: Reading, n: int, record: Mapping[str, Any]) -> bool:
    pid, pgid, started = record.get("pid"), record.get("pgid"), record.get("started")
    if not isinstance(pid, int) or not isinstance(pgid, int) or pid <= 0 or pgid <= 0:
        return False
    stamp = float(started) if isinstance(started, (int, float)) else None
    reading.launches[n] = Launch(n, pid, pgid, stamp)
    return True


# ── authority and signals ────────────────────────────────────────


def _group_empty(pgid: int) -> bool:
    """``killpg(g, 0)`` answers ``ESRCH``: no member is left (r3 §3.2's retire rule)."""

    try:
        os.killpg(pgid, 0)  # pyright: ignore[reportAttributeAccessIssue]
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def leader_verdict(launch: Launch) -> str | None:
    """``None`` when ``launch``'s group may be signalled, else why not (r3 §3.5).

    The group's leader is the shell (``pgid == pid``, ``start_new_session``).
    Alive with the recorded start time: authority. Gone, or a zombie: authority
    (owner OQ-D). Alive with a start time that differs, or that is not known on
    either side: never.
    """

    now = process_started(launch.pgid)
    if now is not None and launch.started is not None:
        return None if now == launch.started else LEADER_MOVED
    if pid_is_zombie(launch.pgid) is True or pid_is_live(launch.pgid) is False:
        return None
    return LEADER_UNKNOWN


@dataclass
class Sweep:
    """What one sweep signalled, and what it could not."""

    groups: list[int] = field(default_factory=list)
    pids: list[WalkEntry] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def merge(self, other: Sweep) -> None:
        self.groups.extend(g for g in other.groups if g not in self.groups)
        self.pids.extend(other.pids)
        self.reasons.extend(other.reasons)


def sweep_groups(launches: Iterable[Launch], retired: Iterable[int]) -> Sweep:
    """SIGKILL each launched group with authority; never a retired one. Never raises."""

    done = Sweep()
    skip = set(retired)
    for launch in launches:
        if launch.n in skip or launch.pgid in done.groups or _group_empty(launch.pgid):
            continue
        why = leader_verdict(launch)
        if why is not None:
            done.reasons.append(f"process group {launch.pgid}: {why}")
            continue
        with contextlib.suppress(OSError):
            os.killpg(launch.pgid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
        done.groups.append(launch.pgid)
    return done


def ancestry(pid: int | None = None) -> set[tuple[int, float | None]]:
    """``pid`` (default: this process) and every ancestor, as ``(pid, start time)``."""

    found: set[tuple[int, float | None]] = set()
    current = os.getpid() if pid is None else pid
    for _ in range(256):
        identity = process_identity(current)
        if identity is None:
            found.add((current, process_started(current)))
            break
        found.add((current, identity[1]))
        if identity[0] <= 1 or identity[0] == current:
            break
        current = identity[0]
    return found


def run_token(marker: str | None) -> str | None:
    """This run's token: the LAST one in a marker, which each delegation appends to."""

    if not marker:
        return None
    tokens = [token for token in marker.split(",") if token]
    return tokens[-1] if tokens else None


def vetoed(exclude: Iterable[tuple[int, float | None]] = ()) -> set[int]:
    """The pids no sweep may signal: this process, its parent, and every pid in ``exclude``.

    By NUMBER, whatever start time was or was not read for it (Codex 1 on
    #719: an ancestry read that failed recorded ``(pid, None)``, and an
    exact ``(pid, stamp)`` match then let the scanner pass as a hit). Read
    again right before each signal (:func:`kill_hits`).
    """

    return {pid for pid, _ in exclude} | {os.getpid(), os.getppid()}


def _held_identity(pid: int) -> tuple[int, float] | None:
    identity = process_identity(pid)
    if identity is None or identity[1] is None:
        return None
    return identity[0], identity[1]


def marker_hits(
    token: str,
    *,
    since: float | None,
    exclude: set[tuple[int, float | None]],
) -> list[WalkEntry] | None:
    """This user's processes whose environment holds ``token``, started no earlier than ``since``.

    ``None`` when this user's processes could not be listed — a scan that did
    not run, which no caller may read as "nothing found" (Codex 7). Never a
    vetoed pid (:func:`vetoed`). The start time is read BEFORE the environment
    and again AFTER it, and a pid whose two readings differ, or either is
    unknown, is not a hit: so the token read belongs to the process that is
    signalled, not to one that took the number in between (Codex 2). A parent
    that changed between the readings (a reparent) does not matter. A process whose environment cannot be read is not a hit
    (a macOS platform binary's reads empty, M4).
    """

    pids = _process_env.own_pids()
    if pids is None:
        return None
    skip = vetoed(exclude)
    hits: list[WalkEntry] = []
    for pid in pids:
        if pid in skip:
            continue
        before = _held_identity(pid)
        if before is None:
            continue
        value = _process_env.environ_value(pid, MARKER_ENV)
        if not value or token not in value.split(","):
            continue
        after = _held_identity(pid)
        if after is None or after[1] != before[1] or (since is not None and before[1] < since):
            continue
        hits.append(WalkEntry(pid, before[1], before[0]))
    return hits


def kill_hits(
    hits: list[WalkEntry] | None, *, exclude: Iterable[tuple[int, float | None]] = ()
) -> Sweep:
    """SIGKILL the hits and their descendants, the groups they lead first. Never raises.

    Each hit is verified — and on Linux held by a pidfd — BEFORE its
    descendants are walked; a hit that no longer verifies contributes nothing,
    its number's new children included (Codex 2). What is walked from the
    verified hits is signalled first, then the hits, each through S2a's legs
    (groups before their leaders, deepest first), and a vetoed pid
    (:func:`vetoed`, read again for each set) never is.
    """

    done = Sweep()
    candidates = [hit for hit in hits or () if hit.pid not in vetoed(exclude)]
    if not candidates:
        return done
    roots = HeldWalk(candidates, _process_walk.group_leaders([hit.pid for hit in candidates]))
    try:
        verified = [hit for hit in roots.entries if roots.verifies(hit)]
        mine = {hit.pid for hit in verified}
        walked: dict[int, WalkEntry] = {}
        for hit in verified:
            for entry in _process_walk.descendant_identities(hit.pid):
                if entry.pid not in mine:
                    walked.setdefault(entry.pid, entry)
        veto = vetoed(exclude)
        below = [entry for entry in walked.values() if entry.pid not in veto]
        held = HeldWalk(below, _process_walk.group_leaders([e.pid for e in below]))
        try:
            done.groups.extend(e.pid for e in below if e.pid in held.groups and held.verifies(e))
            held.kill(signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
        finally:
            held.close()
        done.groups.extend(h.pid for h in verified if h.pid in roots.groups and roots.verifies(h))
        roots.kill(signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
    finally:
        roots.close()
    done.pids.extend([*below, *verified])
    return done


def _group_settled(pgid: int) -> bool:
    return _group_empty(pgid) or _process_walk.group_only_zombies(pgid)


def _pid_settled(entry: WalkEntry) -> bool:
    return same_process(entry.pid, entry.stamp) is False or pid_is_zombie(entry.pid) is True


async def confirm_stopped(
    done: Sweep,
    *,
    bound: float = CONFIRM_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    poll: float = 0.05,
) -> str | None:
    """``None`` once every group ``done`` signalled is empty (or only zombies) and every pid gone; else why not."""

    deadline = clock() + bound
    for group in done.groups:
        while not _group_settled(group):
            if clock() >= deadline:
                return f"process group {group} not confirmed empty"
            await asyncio.sleep(poll)
    for entry in done.pids:
        while not _pid_settled(entry):
            if clock() >= deadline:
                return f"pid {entry.pid} not confirmed stopped"
            await asyncio.sleep(poll)
    return None


def summarize(reasons: list[str]) -> str:
    """At most :data:`TARGETS_SHOWN` reasons, then a count."""

    unique = list(dict.fromkeys(reasons))
    shown = "; ".join(unique[:TARGETS_SHOWN])
    if len(unique) > TARGETS_SHOWN:
        shown += f"; and {len(unique) - TARGETS_SHOWN} more"
    return shown


# ── the agent's writer ───────────────────────────────────────────


def _set_subreaper() -> bool | None:
    """Linux: ``prctl(PR_SET_CHILD_SUBREAPER, 1)``. ``None`` elsewhere."""

    if sys.platform != "linux":
        return None
    try:
        prctl = ctypes.CDLL(None, use_errno=True).prctl
        return int(prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)) == 0
    except (OSError, AttributeError):
        return False


class LaunchWriter:
    """An armed agent's writer: one per process, every record under one lock.

    A record is one ``os.write`` loop on an ``O_APPEND`` fd. After any
    ``OSError`` the writer is ``damaged``: it tries once to append a
    ``damaged`` line and then writes nothing more — a later record after a cut
    one would fuse with it.

    A GROUP WHOSE ``launch`` LINE WAS NOT PUBLISHED IS NOT RELEASED (the lead's
    call, #719 fix round 1, Codex 4). Its launch is kept in :attr:`held`, and
    when its call returns (:func:`after_call`) its group is stopped through the
    ledger's authority rule (:func:`sweep_groups`, the shell being gone): a
    helper aelix could not record does not outlive the call that started it,
    since only this process knew of it and a stop kills this process first. So
    is a launch past :data:`ACTIVE_BOUND` live groups.

    BOUNDED (Codex 8): once :data:`COMPACT_AFTER` launches retired or failed
    since the last compaction, the file is rewritten — its ``open`` and
    ``armed`` lines, a ``compacted`` line, the intents still in flight and the
    live launches — by an atomic replace, and the retired history leaves memory
    too. :attr:`launches` holds the live launches only.
    """

    def __init__(self, fd: int, path: str) -> None:
        self.path = path
        self._fd: int | None = fd
        self._lock = threading.Lock()
        self._seq = 0
        self.damaged = False
        self.launches: dict[int, Launch] = {}
        self.retired: set[int] = set()
        self.pending: set[int] = set()
        self.held: dict[int, Launch] = {}
        self.armed_record: dict[str, Any] | None = None
        self.closed_since = 0
        self.subreaper: bool | None = None
        self.exited = False
        self.finished = False
        self.owner_pid = os.getpid()
        self._tick: asyncio.Task[None] | None = None

    def write(self, record: Mapping[str, Any]) -> bool:
        with self._lock:
            fd = self._fd
            if fd is None or self.damaged:
                return False
            try:
                _write_all(fd, _line(record))
            except OSError:
                self.damaged = True
                # One best-effort mark, on a line of its own after whatever
                # part of the record landed, so the parent reads the ledger
                # as damaged even when the agent's stop leaves no ``exit``.
                with contextlib.suppress(OSError):
                    _write_all(fd, b"\n" + _line({"t": "damaged"}))
                return False
            return True

    def intent(self) -> int:
        """The next sequence number, its ``intent`` written in full before the spawn."""

        with self._lock:
            self._seq += 1
            n = self._seq
            self.pending.add(n)
        self.write({"t": "intent", "n": n})
        return n

    def launched(self, n: int, pid: int) -> None:
        """Right after a successful spawn. Never raises; the launch is kept even if its line is not."""

        try:
            pgid = os.getpgid(pid)  # pyright: ignore[reportAttributeAccessIssue]
        except OSError:
            pgid = pid
        started = process_started(pid)
        launch = Launch(n, pid, pgid, started)
        with self._lock:
            self.pending.discard(n)
            over = len(self.launches) >= ACTIVE_BOUND
            self.launches[n] = launch
        published = self.write({"t": "launch", "n": n, "pid": pid, "pgid": pgid, "started": started})
        if over or not published:
            with self._lock:
                self.held[n] = launch
        self._start_tick()

    def failed(self, n: int) -> None:
        with self._lock:
            self.pending.discard(n)
            self.closed_since += 1
        self.write({"t": "failed", "n": n})

    def retire(self) -> None:
        """Mark each live group that answers ``ESRCH``; compact past the bound. Never raises; reaps nothing."""

        with self._lock:
            live = list(self.launches.values())
        for launch in live:
            if _group_empty(launch.pgid):
                with self._lock:
                    self.launches.pop(launch.n, None)
                    self.held.pop(launch.n, None)
                    self.retired.add(launch.n)
                    self.closed_since += 1
                self.write({"t": "retired", "n": launch.n})
        if self.closed_since >= COMPACT_AFTER:
            self.compact()

    def stop_held(self) -> None:
        """Stop each held group whose shell has exited (:attr:`held`). Never raises."""

        with self._lock:
            held = list(self.held.values())
        for launch in held:
            if pid_is_live(launch.pid) is True and pid_is_zombie(launch.pid) is not True:
                continue  # its own call is still running; that call's end stops it
            sweep_groups([launch], ())
            with self._lock:
                self.held.pop(launch.n, None)

    def compact(self) -> None:
        """Rewrite the ledger as its live state, by an atomic replace. Never raises."""

        with self._lock:
            fd = self._fd
            if fd is None or self.damaged:
                return
            body = [_line(self.armed_record)] if self.armed_record is not None else []
            body.append(_line({"t": "compacted"}))
            body.extend(_line({"t": "intent", "n": n}) for n in sorted(self.pending))
            for launch in self.launches.values():
                body.append(_line({"t": "intent", "n": launch.n}))
                body.append(
                    _line(
                        {
                            "t": "launch",
                            "n": launch.n,
                            "pid": launch.pid,
                            "pgid": launch.pgid,
                            "started": launch.started,
                        }
                    )
                )
            try:
                fresh = _replace(self.path, body)
            except OSError:
                self.damaged = True
                append(self.path, {"t": "damaged"})
                return
            self._fd = fresh
            self.retired.clear()
            self.closed_since = 0
        with contextlib.suppress(OSError):
            os.close(fd)

    def _start_tick(self) -> None:
        if self._tick is not None or self.exited:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._tick = loop.create_task(self._ticking())

    async def _ticking(self) -> None:
        while True:
            await asyncio.sleep(RETIRE_SECONDS)
            self.retire()

    def stop_tick(self) -> None:
        tick, self._tick = self._tick, None
        if tick is not None:
            tick.cancel()

    def close(self) -> None:
        with self._lock:
            fd, self._fd = self._fd, None
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)


def _replace(path: str, body: list[bytes]) -> int:
    """``path`` rewritten as its first line followed by ``body``, atomically; an append fd to it."""

    with open(path, "rb") as handle:
        first = handle.readline(LINE_BOUND + 1)
    if not first.endswith(b"\n"):
        raise OSError("the ledger has no whole first line")
    tmp = path + COMPACT_SUFFIX
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_NOFOLLOW | _O_CLOEXEC, 0o600)
    try:
        _write_all(fd, first + b"".join(body))
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return os.open(path, os.O_WRONLY | os.O_APPEND | _O_NOFOLLOW | _O_CLOEXEC)


_WRITER: LaunchWriter | None = None
_EXIT: asyncio.Future[None] | None = None
_AT_EXIT_REGISTERED = False


def writer() -> LaunchWriter | None:
    """This process's writer, or ``None`` when it is not armed."""

    return _WRITER


def arm_from_environment(environ: dict[str, str] | None = None) -> LaunchWriter | None:
    """Arm this process when its environment names a ledger. Idempotent; never raises.

    Takes :data:`LEDGER_ENV` OUT of the environment, so nothing this process
    starts inherits it and arms itself against the same file. On Linux the
    process becomes a child subreaper first, and the ``armed`` line says
    whether that worked. It also registers :func:`_at_interpreter_exit`, so an
    exit that never reaches print mode's dispose — one of ``cli/entry.py``'s
    early returns — still runs the exit cleanup (#719 fix round 1).
    """

    global _WRITER, _AT_EXIT_REGISTERED  # noqa: PLW0603 — one writer per process, by design
    if _WRITER is not None:
        return _WRITER
    env = os.environ if environ is None else environ
    path = env.pop(LEDGER_ENV, None)
    if not path or sys.platform == "win32":
        return None
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | _O_NOFOLLOW | _O_CLOEXEC)
    except OSError:
        return None
    armed = LaunchWriter(fd, path)
    armed.subreaper = _set_subreaper()
    armed.armed_record = {"t": "armed", "pid": os.getpid(), "subreaper": armed.subreaper}
    armed.write(armed.armed_record)
    _WRITER = armed
    if not _AT_EXIT_REGISTERED:
        atexit.register(_at_interpreter_exit)
        _AT_EXIT_REGISTERED = True
    return armed


def _at_interpreter_exit() -> None:
    """The exit cleanup for an armed process whose run never disposed (every early return).

    Only in the process that armed: a ``fork`` of it inherits the hook and the
    writer, and its ``sys.exit`` must not sweep the agent's live groups or
    write the agent's ``exit`` line (#719 finish, verify G1).
    """

    current = _WRITER
    if current is None or current.finished or os.getpid() != current.owner_pid:
        return
    with contextlib.suppress(BaseException):
        asyncio.run(_exit_cleanup(current))


def disarm() -> None:
    """Forget this process's writer (tests)."""

    global _WRITER, _EXIT  # noqa: PLW0603
    current, _WRITER, _EXIT = _WRITER, None, None
    if current is not None:
        current.stop_tick()
        current.close()


def spawn_env(env: dict[str, str]) -> dict[str, str]:
    """An armed agent's ``bash`` command environment: no ledger, no jobs protocol (r2 §3.2).

    ``AELIX_DELEGATION_MARKER`` passes through.
    """

    return {key: value for key, value in env.items() if key not in (LEDGER_ENV, PROTOCOL_ENV)}


def after_call() -> None:
    """A ``bash`` call ended: retire what emptied, stop what was held. A no-op when unarmed."""

    current = _WRITER
    if current is not None:
        current.retire()
        current.stop_held()


async def exit_cleanup() -> None:
    """The armed agent's end: its descendants, its ledger groups and its marker hits (r3 §3.3).

    Once per process; a second caller (the signal path and the ``finally`` both
    dispose) waits for the first. A no-op when unarmed.
    """

    global _EXIT  # noqa: PLW0603
    current = _WRITER
    if current is None or os.getpid() != current.owner_pid:
        return
    if _EXIT is None:
        _EXIT = asyncio.ensure_future(_exit_cleanup(current))
    with contextlib.suppress(Exception):
        await asyncio.shield(_EXIT)


def _kill_own_descendants(me: int) -> Sweep:
    """At most :data:`EXIT_PASSES` walk-then-kill passes, until one finds nothing alive."""

    done = Sweep()
    for _ in range(EXIT_PASSES):
        entries = [
            entry
            for entry in _process_walk.descendant_identities(me)
            if pid_is_zombie(entry.pid) is not True
        ]
        if not entries:
            break
        held = HeldWalk(entries, _process_walk.group_leaders([e.pid for e in entries]))
        try:
            held.kill(signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
        finally:
            held.close()
        done.pids.extend(entries)
    return done


def _scan(me: int) -> Sweep:
    """This run's marker hits, never this process or an ancestor; a scan that could not run says so."""

    token = run_token(os.environ.get(MARKER_ENV))
    if token is None:
        return Sweep()
    exclude = ancestry(me)
    hits = marker_hits(token, since=process_started(me), exclude=exclude)
    if hits is None:
        return Sweep(reasons=[SCAN_FAILED])
    return kill_hits(hits, exclude=exclude)


async def _exit_cleanup(current: LaunchWriter) -> None:
    current.stop_tick()
    current.exited = True
    me = os.getpid()
    done = _kill_own_descendants(me)
    current.retire()
    with current._lock:
        launches = list(current.launches.values())
    done.merge(sweep_groups(launches, current.retired))
    done.merge(_scan(me))
    why = await confirm_stopped(done)
    reasons = list(done.reasons) + ([why] if why is not None else [])
    current.write(
        {
            "t": "exit",
            "cleanup": "unverified" if reasons else "confirmed",
            "reason": summarize(reasons) if reasons else None,
            "killed": len(done.pids) + len(done.groups),
        }
    )
    current.finished = True
    current.close()


__all__ = [
    "CONFIRM_SECONDS",
    "DAMAGED",
    "EXIT_PASSES",
    "FALLBACK_NAME",
    "IN_FLIGHT",
    "LEADER_MOVED",
    "LEADER_UNKNOWN",
    "LEDGER_ENV",
    "LEDGER_SUFFIX",
    "MARKER_ENV",
    "NOT_ARMED",
    "NOT_CREATED",
    "NOT_SUBREAPER",
    "NO_EXIT_CLEANUP",
    "PROTOCOL_ENV",
    "RETIRE_SECONDS",
    "SCAN_FAILED",
    "Launch",
    "LaunchWriter",
    "Reading",
    "Sweep",
    "after_call",
    "ancestry",
    "append",
    "arm_from_environment",
    "confirm_stopped",
    "create",
    "disarm",
    "exit_cleanup",
    "kill_hits",
    "leader_verdict",
    "marker_hits",
    "read",
    "run_token",
    "spawn_env",
    "vetoed",
    "summarize",
    "sweep_groups",
    "writer",
]
