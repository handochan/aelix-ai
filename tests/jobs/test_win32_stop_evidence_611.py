"""The win32 stop collects member handles before any job-wide kill (#722, Codex r5 #1, #2, r3 #1).

Driven through an injected api, Codex's countermodel: the job lists the root and
a helper before the kill and NOTHING after it, while the helper's handle stays
unsignalled. Fails on main ``a385c64a``: ``ProcessTree`` has no
``stop_and_collect``. Naive (round 5's order — ``hard_kill``, then scan the
job): the scan after the kill finds an empty list and reads it as "all
stopped" while the helper runs.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_ai.utils._process_tree import Confirmed, ProcessTree, Unconfirmed


class FakeJob:
    """A job with a root (pid 10) and a helper (pid 11); handles are 100 + pid."""

    def __init__(self, *, helper_dies: bool = True, list_after_kill: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.members = {10, 11}
        self.alive = {10, 11}
        self.killed = False
        self.helper_dies = helper_dies
        self.list_after_kill = list_after_kill
        self.closed: set[int] = set()

    # _Win32Api
    def create_job(self, *, kill_on_close: bool) -> int:
        return 7

    def assign(self, job: int, pid: int, handle: int | None) -> None:
        self.calls.append(("assign", pid))

    def terminate_job(self, job: int) -> None:
        self.calls.append(("terminate_job", job))
        self.alive.clear()

    def close_handle(self, job: int) -> None:
        self.calls.append(("close_job", job))

    def ctrl_break(self, pid: int) -> None:
        self.calls.append(("ctrl_break", pid))

    def taskkill(self, pid: int) -> None:
        self.calls.append(("taskkill", pid))
        self.killed = True
        self.alive.discard(10)

    # _Win32EvidenceApi
    def job_member_pids(self, job: int) -> list[int]:
        self.calls.append(("list", job))
        if self.killed and not self.list_after_kill:
            return []  # Codex's countermodel: an empty list after the kill
        return sorted(self.members)

    def active_count(self, job: int) -> int:
        return len(self.alive)

    def open_member(self, pid: int) -> int:
        self.calls.append(("open", pid))
        return 100 + pid

    def in_job(self, handle: int, job: int) -> bool:
        return True

    def terminate_process(self, handle: int) -> None:
        self.calls.append(("terminate_process", handle - 100))
        if handle - 100 != 11 or self.helper_dies:
            self.alive.discard(handle - 100)

    def wait_handle(self, handle: int, timeout_ms: int) -> bool:
        return (handle - 100) not in self.alive

    def close_process(self, handle: int) -> None:
        self.closed.add(handle)


def _tree(api: FakeJob) -> ProcessTree:
    return ProcessTree.attach(10, kill_on_close=True, platform="win32", api=api)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.01
        return self.now


async def test_member_handles_are_collected_before_any_job_wide_kill() -> None:
    api = FakeJob()
    tree = _tree(api)
    receipt = tree.stop_and_collect(policy="stop")
    names = [name for name, _ in api.calls if name != "assign"]
    first_kill = min(names.index(n) for n in ("taskkill", "terminate_process"))
    assert names[:first_kill] == ["list", "open", "open"], names
    assert "terminate_job" not in names, "TerminateJobObject comes last, in confirm"
    assert dict(receipt.handles) == {10: 110, 11: 111}

    verdict = await tree.confirm(receipt, deadline=1.0, clock=_Clock(), poll=0)
    assert isinstance(verdict, Confirmed)
    tail = [name for name, _ in api.calls][-1]
    assert tail == "terminate_job"
    assert api.closed == {110, 111}


async def test_empty_member_list_after_kill_is_not_confirmation() -> None:
    api = FakeJob(helper_dies=False)
    tree = _tree(api)
    receipt = tree.stop_and_collect(policy="stop")
    verdict = await tree.confirm(receipt, deadline=0.5, clock=_Clock(), poll=0)
    assert isinstance(verdict, Unconfirmed)
    assert "11" in verdict.reason
    assert ("terminate_job", 7) not in api.calls


async def test_normal_exit_stops_and_waits_remaining_members_before_the_barrier_passes() -> None:
    """``stop_descendants``: the root has exited; the helper still in the job is stopped and waited."""

    api = FakeJob()
    api.alive.discard(10)
    api.members = {11}
    tree = _tree(api)
    receipt = tree.stop_and_collect(policy="stop_descendants")
    assert ("taskkill", 10) not in api.calls, "no tree kill once the root is gone"
    assert dict(receipt.handles) == {11: 111}
    assert ("terminate_process", 11) in api.calls
    verdict = await tree.confirm(receipt, deadline=1.0, clock=_Clock(), poll=0)
    assert isinstance(verdict, Confirmed)
    assert 11 not in api.alive


async def test_a_member_that_joins_after_the_kill_is_collected_by_the_rescan() -> None:
    api = FakeJob(list_after_kill=True)
    tree = _tree(api)
    receipt = tree.stop_and_collect(policy="stop")
    api.members.add(12)
    api.alive.add(12)
    verdict = await tree.confirm(receipt, deadline=1.0, clock=_Clock(), poll=0)
    assert isinstance(verdict, Confirmed)
    assert ("terminate_process", 12) in api.calls


async def test_a_failed_member_query_is_never_confirmation() -> None:
    api = FakeJob()

    def broken(job: int) -> list[int]:
        raise OSError("access denied")

    api.job_member_pids = broken  # type: ignore[method-assign]
    tree = _tree(api)
    receipt = tree.stop_and_collect(policy="stop")
    verdict = await tree.confirm(receipt, deadline=1.0, clock=_Clock(), poll=0)
    assert isinstance(verdict, Unconfirmed) and "member query failed" in verdict.reason


def test_service_owned_tree_is_not_closed_before_the_barrier() -> None:
    """The stop collects and kills; it never closes the job handle (a close with
    kill-on-close is a kill that leaves no evidence)."""

    api = FakeJob()
    tree = _tree(api)
    assert tree.active_processes() == 2, "the monitoring count, read from the job"
    tree.stop_and_collect(policy="stop")
    assert not tree.closed
    assert ("close_job", 7) not in api.calls
    assert tree.job == 7


# === #722 fix round 1 — every service path collects before it signals (Codex #3) ===


class _JobKillsRoot(FakeJob):
    """``TerminateJobObject`` ends the root and empties the listing; the helper's
    own handle is never signalled by it (Codex's countermodel)."""

    def terminate_job(self, job: int) -> None:
        self.calls.append(("terminate_job", job))
        self.alive.discard(10)
        self.killed = True


def _first(calls: list[tuple[str, Any]], name: str) -> int:
    return next(i for i, call in enumerate(calls) if call[0] == name)


async def test_a_registration_after_the_stop_latch_collects_before_it_kills() -> None:
    """The eager kill of a process registered after the stop latch.

    Fails on ``a80e81fa``: ``kill_now`` was ``hard_kill`` — ``taskkill`` and
    ``TerminateJobObject`` first, the listing (now empty) after — so the receipt
    held no handle and ``confirm`` said Confirmed with the helper alive.
    """

    from aelix_coding_agent.jobs import evidence

    api = _JobKillsRoot(helper_dies=False)
    record = evidence.ProcessRecord(10, 1.0, tree=_tree(api))
    evidence.kill_now(record)
    assert _first(api.calls, "list") < _first(api.calls, "taskkill")
    assert "terminate_job" not in [c[0] for c in api.calls], "only after every member was waited"
    assert record.receipt is not None and dict(record.receipt.handles) == {10: 110, 11: 111}
    verdict = await record.tree.confirm(record.receipt, 1.0, clock=_Clock(), poll=0)  # type: ignore[union-attr]
    assert isinstance(verdict, Unconfirmed), "the helper's handle never signalled"


async def test_a_soft_stop_collects_before_ctrl_break(monkeypatch: pytest.MonkeyPatch) -> None:
    """The soft leg of a service stop.

    Fails on ``a80e81fa``: ``CTRL_BREAK`` went first; the root exited, the
    listing was empty by the time the hard leg collected, and ``confirm`` said
    Confirmed with the helper alive.
    """

    from aelix_coding_agent.jobs import evidence

    # The root is the job's own process, so its number reads the start time the
    # record holds. Read from the host instead, a real pid 10 (a kernel thread on
    # Linux CI) is a reused number and the soft leg skips it (#611 S1c's guard).
    monkeypatch.setattr(evidence, "process_started", lambda pid: 1.0 if pid == 10 else None)
    api = _JobKillsRoot(helper_dies=False)

    class Root:
        returncode: int | None = None

    root = Root()

    def graceful_exit(pid: int) -> None:
        api.calls.append(("ctrl_break", pid))
        api.killed = True
        api.alive.discard(10)
        root.returncode = 0

    api.ctrl_break = graceful_exit  # type: ignore[method-assign]
    record = evidence.ProcessRecord(10, 1.0, proc=root, tree=_tree(api))
    await evidence.stop_legs([record], grace=0)
    assert _first(api.calls, "list") < _first(api.calls, "ctrl_break")
    assert record.receipt is not None and 11 in dict(record.receipt.handles)
    verdict = await record.tree.confirm(record.receipt, 1.0, clock=_Clock(), poll=0)  # type: ignore[union-attr]
    assert isinstance(verdict, Unconfirmed)

    api2 = _JobKillsRoot(helper_dies=True)
    root2 = Root()

    def exits(pid: int) -> None:
        api2.calls.append(("ctrl_break", pid))
        api2.killed = True
        api2.alive.discard(10)
        root2.returncode = 0

    api2.ctrl_break = exits  # type: ignore[method-assign]
    record2 = evidence.ProcessRecord(10, 1.0, proc=root2, tree=_tree(api2))
    await evidence.stop_legs([record2], grace=0)
    verdict2 = await record2.tree.confirm(record2.receipt, 1.0, clock=_Clock(), poll=0)  # type: ignore[union-attr, arg-type]
    assert isinstance(verdict2, Confirmed), "a helper that does die is confirmed"


def test_a_later_collection_keeps_the_handles_it_holds_and_opens_only_newcomers() -> None:
    """``collect(prior=…)`` on win32: a member already held is not opened again
    (verify R09: a second handle per member, never closed, and the receipt naming
    each member twice); a member that joined since is opened and added."""

    api = FakeJob()
    tree = _tree(api)
    first = tree.collect()
    api.members.add(12)
    api.alive.add(12)
    later = tree.collect(prior=first)
    assert [pid for name, pid in api.calls if name == "open"] == [10, 11, 12]
    assert later.handles == ((10, 110), (11, 111), (12, 112))
