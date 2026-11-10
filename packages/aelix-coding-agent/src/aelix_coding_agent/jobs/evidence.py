"""What a job's processes are, whether they are gone, and how a stop proves it (#722, ADR-0293).

Two uses, one rule — **evidence before conclusions, and never a kill on a
record alone**:

* **The barrier** (:func:`stop_legs`, :func:`confirm_gone`). A job reaches a
  terminal state only after the processes it registered are confirmed gone:
  each root reaped, each process group empty, each walked descendant gone,
  each service-owned tree's collected members waited on
  (``ProcessTree.collect`` / ``stop_and_collect`` / ``confirm``). **Evidence is
  collected before the first signal of every path** — the soft leg, the hard
  leg, the kill of a process registered after the stop latch, and the
  normal-exit stop of what a root left behind (Codex #3, #4): on win32 the job's
  members are opened before ``CTRL_BREAK`` or ``taskkill`` can empty the
  listing; on POSIX the root's descendants are walked, with their start times
  and the groups they lead, while the root still lives, so one that left its
  group is still named when the root has died. A pid the job registered without
  a handle or a group, and a pid walked before an earlier signal, is signalled
  only while its start time still matches the one recorded; a group the
  earlier collection recorded is killed whole (ADR-0293 §5).
* **A restart** (:func:`classify_restart`). Report-only: a record in a session
  file is a hint, not authority, so nothing found at start-up is ever signalled
  (owner decision 4, 2026-11-07). A recorded process that is alive with the
  same start time is ``unresolved`` — also when its command line no longer
  shows the job's markers, which an ``exec`` changes and which proves nothing
  gone (Codex #20); only when every recorded process is confirmed gone (no such
  pid, or the number now belongs to a process that started at another time) is
  the job ``lost``; anything that cannot be read is ``unresolved``. A job that
  registered no process is judged by the aelix process that ran it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.utils import _process_walk
from aelix_ai.utils._process_identity import process_argv, process_started, same_process
from aelix_ai.utils._process_tree import Confirmed, ProcessTree, StopReceipt, Unconfirmed

#: SIGTERM (or the tree's soft kill) to SIGKILL, inside a stop's barrier.
STOP_GRACE = 2.0


def this_host() -> str:
    try:
        return socket.gethostname()
    except OSError:  # pragma: no cover — defensive
        return ""


def supervisor_evidence() -> dict[str, Any]:
    """This aelix process, as a ``start`` fact records it."""

    pid = os.getpid()
    return {"pid": pid, "started": process_started(pid), "host": this_host()}


@dataclass
class ProcessRecord:
    """One process a job registered: what the service may wait on and signal."""

    pid: int
    started: float | None
    pgid: int | None = None
    proc: Any = None  # asyncio.subprocess.Process
    tree: ProcessTree | None = None
    receipt: StopReceipt | None = None
    #: POSIX, a record with no tree: descendants walked before a signal, with
    #: their start times, and the groups they and the root lead.
    walked: dict[int, float | None] = field(default_factory=dict)
    groups: list[int] = field(default_factory=list)

    def evidence(self) -> dict[str, Any]:
        data: dict[str, Any] = {"pid": self.pid, "started": self.started, "host": this_host()}
        if self.pgid is not None:
            data["pgid"] = self.pgid
        if self.tree is not None and self.tree.job is not None:
            data["win_job"] = True
        return data

    def root_gone(self) -> bool | None:
        """``True`` gone, ``False`` alive, ``None`` cannot tell."""

        if self.proc is not None:
            return getattr(self.proc, "returncode", None) is not None
        same = same_process(self.pid, self.started)
        return None if same is None else not same

    def identity_holds(self) -> bool:
        """Whether signalling ``pid`` alone is safe: a live process with the recorded start time."""

        if self.proc is not None and getattr(self.proc, "returncode", None) is None:
            return True
        return same_process(self.pid, self.started) is True


def _group_alive(pgid: int) -> bool:
    if sys.platform == "win32":
        return False
    try:
        os.killpg(pgid, 0)  # pyright: ignore[reportAttributeAccessIssue]
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _walk(pid: int) -> list[int]:
    return _process_walk.descendant_pids(pid)


def _leaders(pids: list[int]) -> list[int]:
    return _process_walk.group_leaders(pids)


def collect(record: ProcessRecord, *, policy: str = "stop") -> None:
    """Evidence for ``record`` before anything is signalled; merged into what was collected before.

    Never raises and never signals. A tree collects through
    ``ProcessTree.collect``; a POSIX record with no tree walks its root's
    descendants while the root's identity still holds. On win32 a record with
    no tree has no evidence beyond its handle.
    """

    if record.tree is not None and not record.tree.closed:
        record.receipt = record.tree.collect(
            policy=policy,  # type: ignore[arg-type]
            walk=_walk,
            leaders=_leaders,
            started=process_started,
            prior=record.receipt,
        )
        return
    if sys.platform == "win32" or not record.identity_holds():
        return
    try:
        fresh = [p for p in _walk(record.pid) if p != record.pid and p not in record.walked]
    except Exception:  # noqa: BLE001 — a walk that fails leaves the evidence as it was
        fresh = []
    for pid in fresh:
        record.walked[pid] = process_started(pid)
    if record.pgid is not None and record.pgid not in record.groups:
        record.groups.append(record.pgid)
    for group in _leaders([record.pid, *fresh]):
        if group not in record.groups:
            record.groups.append(group)


def _moved(pid: int, stamp: float | None) -> bool:
    """Whether ``pid`` now names a process that started at another time than ``stamp`` — a reused number.

    ``False`` when it is the same process, when it is gone, or when either time
    is unknown: only a positive reading of ANOTHER start time is a reuse. The
    kernel reuses a group leader's number only once its group is empty, so a
    group whose leader's number moved is no longer the job's (#611 S1c).
    """

    if stamp is None:
        return False
    now = process_started(pid)
    return now is not None and now != stamp


def _signal_record(record: ProcessRecord, *, hard: bool) -> None:
    """One kill leg for one record, its evidence collected first. Never raises; never signals an unverified bare pid.

    Never a group whose recorded leader's number now belongs to another
    process (:func:`_moved`; #611 S1c): the root's own group, a walked
    leader's, or the tree's.
    """

    if record.tree is not None and not record.tree.closed:
        if hard:
            record.receipt = record.tree.stop_and_collect(
                policy="stop",
                walk=_walk,
                leaders=_leaders,
                started=process_started,
                prior=record.receipt,
                same=same_process,
                root_started=record.started,
            )
        else:
            collect(record)
            if not _moved(record.pid, record.started):
                record.tree.soft_kill(whole_group=True)
        return
    if sys.platform == "win32":
        if record.proc is not None and getattr(record.proc, "returncode", None) is None:
            with contextlib.suppress(OSError, ProcessLookupError):
                record.proc.kill()
        return
    collect(record)
    sig = signal.SIGKILL if hard else signal.SIGTERM  # pyright: ignore[reportAttributeAccessIssue]
    if hard:
        for pid, stamp in record.walked.items():
            if same_process(pid, stamp) is True:
                with contextlib.suppress(OSError):
                    os.kill(pid, sig)
        for group in record.groups:
            if group != record.pgid and not _moved(group, _leader_stamp(record, group)):
                with contextlib.suppress(OSError):
                    os.killpg(group, sig)  # pyright: ignore[reportAttributeAccessIssue]
    if record.pgid is not None:
        if not _moved(record.pgid, _leader_stamp(record, record.pgid)):
            with contextlib.suppress(OSError):
                os.killpg(record.pgid, sig)  # pyright: ignore[reportAttributeAccessIssue]
        return
    if record.identity_holds():
        with contextlib.suppress(OSError):
            os.kill(record.pid, sig)


def _leader_stamp(record: ProcessRecord, group: int) -> float | None:
    """The start time recorded for ``group``'s leader: the root's, or a walked pid's; else unknown."""

    if group == record.pid:
        return record.started
    return record.walked.get(group)


def kill_now(record: ProcessRecord) -> None:
    """The hard leg at once, evidence first: a registration that arrives after the stop latch."""

    _signal_record(record, hard=True)


async def stop_legs(
    records: list[ProcessRecord],
    *,
    grace: float = STOP_GRACE,
    clock: Callable[[], float] = time.monotonic,
    poll: float = 0.05,
) -> None:
    """Evidence, then soft, then — for whatever is left after ``grace`` — the hard leg.

    Each record's evidence is collected before its soft signal, and again (merged)
    before the hard one; the receipt stays on the record for :func:`confirm_gone`.
    """

    live = [r for r in records if r.root_gone() is not True]
    for record in live:
        _signal_record(record, hard=False)
    end = clock() + grace
    while clock() < end and any(r.root_gone() is not True for r in live):
        await asyncio.sleep(poll)
    for record in records:
        if (
            (record.tree is not None and not record.tree.closed)
            or record.root_gone() is not True
            or record.pgid is not None
            or record.walked
        ):
            _signal_record(record, hard=True)


def stop_descendants(records: list[ProcessRecord]) -> None:
    """The normal-exit policy: the roots are gone; stop what is left in their trees and groups."""

    for record in records:
        if record.tree is not None and not record.tree.closed:
            record.receipt = record.tree.stop_and_collect(
                policy="stop_descendants",
                walk=_walk,
                leaders=_leaders,
                started=process_started,
                prior=record.receipt,
                same=same_process,
                root_started=record.started,
            )
        elif (
            record.pgid is not None
            and _group_alive(record.pgid)
            and not _moved(record.pgid, _leader_stamp(record, record.pgid))
        ):
            with contextlib.suppress(OSError):
                os.killpg(record.pgid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]


async def confirm_gone(
    records: list[ProcessRecord],
    deadline: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    poll: float = 0.05,
    check_groups: bool = True,
) -> tuple[bool, str | None]:
    """Whether every record's root is reaped, its groups empty and its walked descendants gone, by ``deadline``."""

    for record in records:
        while record.root_gone() is not True:
            if record.proc is not None:
                left = deadline - clock()
                if left <= 0:
                    return False, f"pid {record.pid} not confirmed stopped"
                with contextlib.suppress(asyncio.TimeoutError, Exception):
                    await asyncio.wait_for(record.proc.wait(), timeout=min(left, 1.0))
                continue
            if clock() >= deadline:
                return False, f"pid {record.pid} not confirmed stopped"
            await asyncio.sleep(poll)
        if record.receipt is not None and record.tree is not None:
            verdict: Confirmed | Unconfirmed = await record.tree.confirm(
                record.receipt, deadline, clock=clock, poll=poll, same=same_process
            )
            if isinstance(verdict, Unconfirmed):
                return False, verdict.reason
            continue
        if check_groups:
            groups: list[int] = [record.pgid] if record.pgid is not None else []
            groups.extend(g for g in record.groups if g not in groups)
            for group in groups:
                while _group_alive(group):
                    if clock() >= deadline:
                        return False, f"process group {group} not confirmed empty"
                    await asyncio.sleep(poll)
        for pid, stamp in record.walked.items():
            while same_process(pid, stamp) is not False:
                if clock() >= deadline:
                    return False, f"pid {pid} not confirmed stopped"
                await asyncio.sleep(poll)
    return True, None


def classify_restart(
    processes: Sequence[Mapping[str, Any]],
    supervisor: Mapping[str, Any] | None,
    *,
    argv_contains: tuple[str, ...] = (),
    host: str | None = None,
    same: Callable[[int, float | None], bool | None] = same_process,
    argv: Callable[[int], list[str] | None] = process_argv,
) -> tuple[str, str]:
    """``("lost", why)`` or ``("unresolved", why)`` for a job a restart found unfinished. Signals nothing."""

    here = this_host() if host is None else host
    evidence = [p.get("evidence") for p in processes if isinstance(p.get("evidence"), Mapping)]
    judged_by_supervisor = False
    if not evidence:
        if not isinstance(supervisor, Mapping):
            return "unresolved", "no process was recorded for it"
        evidence = [supervisor]
        judged_by_supervisor = True
    for item in evidence:
        assert isinstance(item, Mapping)
        if item.get("host") != here:
            return "unresolved", "it ran on another host"
        pid = item.get("pid")
        started = item.get("started")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return "unresolved", "its process record is unreadable"
        if started is not None and not isinstance(started, int | float):
            return "unresolved", "its process record is unreadable"
        verdict = same(pid, started)
        if verdict is None:
            return "unresolved", f"pid {pid} not confirmed stopped"
        if verdict is True:
            if argv_contains and not judged_by_supervisor:
                seen = argv(pid)
                if seen is not None and not all(
                    any(a in part for part in seen) for a in argv_contains
                ):
                    # The recorded process is alive; its command line changed
                    # (an exec) — that is no evidence it is gone (Codex #20).
                    return (
                        "unresolved",
                        f"pid {pid} is still running, and its command line no longer shows this job",
                    )
            if judged_by_supervisor:
                return "unresolved", f"the aelix process that ran it (pid {pid}) is still running"
            return "unresolved", f"pid {pid} is still running"
    return "lost", "its processes are gone and no result was recorded"


__all__ = [
    "STOP_GRACE",
    "ProcessRecord",
    "classify_restart",
    "collect",
    "confirm_gone",
    "kill_now",
    "stop_descendants",
    "stop_legs",
    "supervisor_evidence",
    "this_host",
]
