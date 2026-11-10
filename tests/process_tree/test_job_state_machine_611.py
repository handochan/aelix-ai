"""One state per Windows Job decides every kill, preserve and close (#611 S2b, #759).

Driven through an injected api on every host (the repo's inject-don't-skip
convention for the win32 arm); the real Job runs on windows-latest in
``test_job_object_windows_611.py``.

* G: the call sequences every caller makes today take the same api calls.
* A/N: ``preserve_descendants`` and the suspended attach did not exist on
  ``3cab5d16`` (an ``AttributeError`` or ``TypeError`` there); each case asserts
  behaviour and is pinned by a named mutant in the lane's report.
* N: a retry after ``Unconfirmed`` reused a member handle ``_confirm_win32`` had
  already closed (Codex finding #8 on the #611 S2 contract, reproduced by its
  injected probe): on ``3cab5d16`` this file's fake records ``terminate_process``
  on a closed handle that the system had handed to another process.
"""

from __future__ import annotations

import asyncio
import ctypes
import threading
from typing import Any

import pytest
from aelix_ai.utils import _process_tree
from aelix_ai.utils._process_tree import Confirmed, ProcessTree, Unconfirmed

KILL_ON_CLOSE = 0x2000
BREAKAWAY_OK = 0x0800
OTHER_LIMIT = 0x0008


class FakeKernel:
    """A job (7) with a root (10) and a helper (11). A handle is a fresh integer per open.

    ``open`` is the set of handles the fake considers open; a closed handle the
    "system" may hand to an unrelated process (``recycled``), and any call
    through one is a ``misuse``.
    """

    def __init__(self, *, members: tuple[int, ...] = (10, 11)) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.members = set(members)
        self.alive = set(members)
        self.next_handle = 100
        self.owner: dict[int, int] = {}
        self.open: set[int] = set()
        self.recycled: dict[int, str] = {}
        self.misuse: list[tuple[str, int, str]] = []
        self.flags = KILL_ON_CLOSE | OTHER_LIMIT
        self.set_flags: list[int] = []
        self.fail: set[str] = set()
        self.resumable = True
        self.dies_on_terminate = True

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise OSError(f"{name} failed")

    def _use(self, name: str, handle: int) -> int | None:
        if handle not in self.open:
            self.misuse.append((name, handle, self.recycled.get(handle, "closed")))
            return None
        return self.owner[handle]

    # _Win32Api
    def create_job(self, *, kill_on_close: bool) -> int:
        self.calls.append(("create_job", kill_on_close))
        self._maybe_fail("create_job")
        return 7

    def assign(self, job: int, pid: int, handle: int | None) -> None:
        self.calls.append(("assign", pid))
        self._maybe_fail("assign")

    def terminate_job(self, job: int) -> None:
        self.calls.append(("terminate_job", job))
        self.alive.clear()

    def close_handle(self, job: int) -> None:
        self.calls.append(("close_job", job))

    def ctrl_break(self, pid: int) -> None:
        self.calls.append(("ctrl_break", pid))

    def taskkill(self, pid: int) -> None:
        self.calls.append(("taskkill", pid))

    # _Win32EvidenceApi
    def job_member_pids(self, job: int) -> list[int]:
        self.calls.append(("list", job))
        return sorted(self.members & self.alive)

    def active_count(self, job: int) -> int:
        return len(self.alive)

    def open_member(self, pid: int) -> int:
        self.calls.append(("open", pid))
        self._maybe_fail("open_member")
        self.next_handle += 1
        self.owner[self.next_handle] = pid
        self.open.add(self.next_handle)
        return self.next_handle

    def in_job(self, handle: int, job: int) -> bool:
        pid = self._use("in_job", handle)
        return pid in self.members

    def terminate_process(self, handle: int) -> None:
        pid = self._use("terminate_process", handle)
        self.calls.append(("terminate_process", pid))
        self._maybe_fail("terminate_process")
        if pid is not None and self.dies_on_terminate:
            self.alive.discard(pid)

    def wait_handle(self, handle: int, timeout_ms: int) -> bool:
        pid = self._use("wait_handle", handle)
        self.calls.append(("wait", pid))
        self._maybe_fail("wait_handle")
        return pid not in self.alive

    def close_process(self, handle: int) -> None:
        self.calls.append(("close_process", self.owner.get(handle)))
        self.open.discard(handle)

    # _Win32JobControlApi
    def limit_flags(self, job: int) -> int:
        self.calls.append(("limit_flags", job))
        return self.flags

    def set_limit_flags(self, job: int, flags: int) -> None:
        self.calls.append(("set_limit_flags", flags))
        self._maybe_fail("set_limit_flags")
        self.set_flags.append(flags)
        self.flags = flags

    def can_resume(self) -> bool:
        self.calls.append(("can_resume", None))
        return self.resumable

    def resume(self, pid: int, handle: int | None) -> None:
        self.calls.append(("resume", pid))
        self._maybe_fail("resume")

    def duplicate(self, handle: int) -> int:
        self.calls.append(("duplicate", self.owner.get(handle)))
        self._maybe_fail("duplicate")
        pid = self._use("duplicate", handle)
        self.next_handle += 1
        self.owner[self.next_handle] = pid if pid is not None else -1
        self.open.add(self.next_handle)
        return self.next_handle

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.01
        return self.now


def _tree(api: FakeKernel, *, kill_on_close: bool = True) -> ProcessTree:
    return ProcessTree.attach(10, kill_on_close=kill_on_close, platform="win32", api=api)


# === G: today's call sequences ================================================


def test_open_then_close_is_todays_release() -> None:
    """Foreground ``bash``: attach at ``kill_on_close=False``, then close — a release, nothing killed."""

    api = FakeKernel()
    tree = _tree(api, kill_on_close=False)
    tree.close()
    assert api.calls == [("create_job", False), ("assign", 10), ("close_job", 7)]
    assert tree.closed


def test_hard_kill_from_open_is_todays_order() -> None:
    """``hard_kill``: ``taskkill /T`` while the root lives, then ``TerminateJobObject``; again is again."""

    api = FakeKernel()
    tree = _tree(api)
    tree.hard_kill()
    tree.hard_kill()
    tree.close()
    assert api.names()[2:] == ["taskkill", "terminate_job", "taskkill", "terminate_job", "close_job"]


async def test_a_service_stop_is_todays_sequence() -> None:
    """``stop_and_collect`` then ``confirm``: list, open members, kill, wait, last the job, then close them."""

    api = FakeKernel()
    tree = _tree(api)
    receipt = tree.stop_and_collect(policy="stop")
    verdict = await tree.confirm(receipt, deadline=1.0, clock=_Clock(), poll=0)
    assert isinstance(verdict, Confirmed)
    assert api.names()[2:] == [
        "list", "open", "open", "taskkill", "terminate_process", "terminate_process",
        "wait", "wait", "list", "terminate_job", "close_process", "close_process",
    ]  # fmt: skip
    assert api.misuse == []


# === A/N: preserve ============================================================


def test_preserve_first_then_every_kill_entry_is_a_no_op() -> None:
    """``PRESERVED``: no console event, no ``taskkill``, no terminate, no job kill; close only releases."""

    api = FakeKernel()
    tree = _tree(api)
    assert tree.preserve_descendants() is True
    before = len(api.calls)
    assert tree.soft_kill() is False
    tree.hard_kill()
    tree.stop_and_collect(policy="stop")
    tree.close()
    after = api.names()[before:]
    assert not {"ctrl_break", "taskkill", "terminate_process", "terminate_job"} & set(after), after
    assert after[-1] == "close_job"
    assert tree.state == "closed"


def test_hard_kill_after_preserve_runs_no_taskkill() -> None:
    api = FakeKernel()
    tree = _tree(api)
    tree.preserve_descendants()
    tree.hard_kill()
    assert "taskkill" not in api.names()
    assert tree.state == "preserved"


async def test_confirm_rescan_terminate_is_gated() -> None:
    """A confirm on a preserved tree terminates no member it finds and does not kill the job."""

    api = FakeKernel()
    tree = _tree(api)
    receipt = tree.collect()
    tree.preserve_descendants()
    api.members.add(12)
    api.alive.add(12)
    api.alive.difference_update({10, 11})
    await tree.confirm(receipt, deadline=1.0, clock=_Clock(), poll=0)
    assert "terminate_process" not in api.names()
    api.alive.discard(12)
    assert isinstance(await tree.confirm(receipt, deadline=1.0, clock=_Clock(), poll=0), Confirmed)
    assert "terminate_job" not in api.names(), "a preserved job is not killed at the end of a confirm"


def test_preserve_clears_only_kill_on_close_never_breakaway() -> None:
    """The flags the job had, minus ``KILL_ON_JOB_CLOSE``; ``BREAKAWAY_OK`` is never set."""

    api = FakeKernel()
    tree = _tree(api)
    assert tree.preserve_descendants() is True
    assert api.set_flags == [OTHER_LIMIT]
    assert all(not flags & BREAKAWAY_OK for flags in api.set_flags)


def test_a_failed_preserve_stays_open_and_reports_false() -> None:
    """``SetInformationJobObject`` refused: ``False``, still ``OPEN``, and a kill still kills."""

    api = FakeKernel()
    api.fail.add("set_limit_flags")
    tree = _tree(api)
    assert tree.preserve_descendants() is False
    assert tree.state == "open"
    tree.hard_kill()
    assert api.names()[-2:] == ["taskkill", "terminate_job"]


def test_a_preserve_after_a_kill_entry_fails_and_the_stop_wins() -> None:
    """``TERMINATING`` + preserve is ``False``: a stop under way is not undone by a late preserve."""

    api = FakeKernel()
    tree = _tree(api)
    tree.soft_kill()
    assert tree.preserve_descendants() is False
    assert "set_limit_flags" not in api.names()
    assert tree.state == "terminating"


def test_posix_and_jobless_trees_preserve_at_once() -> None:
    """``close`` never kills there, so preserving changes no flag and holds."""

    api = FakeKernel()
    api.fail.add("create_job")
    fallback = _tree(api)
    assert fallback.job is None and fallback.preserve_descendants() is True
    assert "set_limit_flags" not in api.names()


# === N: member handles live as long as the cleanup duty (PE5) =================


async def test_a_retry_after_unconfirmed_never_uses_a_closed_handle() -> None:
    """Codex #8: confirm times out with the helper alive; the liveness retry stops again.

    On ``3cab5d16`` ``_confirm_win32`` closed both handles on its way out, the
    receipt kept their integers, and the retry's ``_kill_win32`` called
    ``TerminateProcess`` on them — here one of them now names another process.
    Now the tree keeps them open until a confirm succeeds or it is closed.
    """

    api = FakeKernel()
    api.dies_on_terminate = False
    tree = _tree(api)
    receipt = tree.stop_and_collect(policy="stop")
    verdict = await tree.confirm(receipt, deadline=0.05, clock=_Clock(), poll=0)
    assert isinstance(verdict, Unconfirmed)
    assert len(api.open) == 2, "an unconfirmed stop keeps the member handles it still needs"
    for handle in list(api.owner):
        if handle not in api.open:
            api.recycled[handle] = "an unrelated process"
    retry = tree.stop_and_collect(policy="stop", prior=receipt)
    assert api.misuse == [], api.misuse
    assert [c for c in api.calls if c[0] == "terminate_process"][-2:] == [
        ("terminate_process", 10),
        ("terminate_process", 11),
    ]
    api.dies_on_terminate = True
    api.alive.clear()
    assert isinstance(await tree.confirm(retry, deadline=1.0, clock=_Clock(), poll=0), Confirmed)
    assert api.open == set(), "a confirmed stop releases every member handle"


def test_close_releases_the_member_handles_a_stop_still_holds() -> None:
    api = FakeKernel()
    tree = _tree(api)
    tree.stop_and_collect(policy="stop")
    assert len(api.open) == 2
    tree.close()
    assert api.open == set()


def test_a_member_no_longer_held_is_reopened_and_asked_again() -> None:
    """An earlier receipt's member whose handle this tree no longer holds is opened again, and checked."""

    api = FakeKernel()
    tree = _tree(api)
    first = tree.collect()
    _process_tree._close_held(api, tree._held)
    later = tree.collect(prior=first)
    assert [pid for name, pid in api.calls if name == "open"] == [10, 11, 10, 11]
    assert {pid for pid, _h in later.handles} == {10, 11}
    assert all(handle in api.open for _pid, handle in later.handles)


# === A/N: the suspended spawn =================================================


def _suspended(api: FakeKernel, *, latched: bool = False) -> ProcessTree:
    return ProcessTree.attach(
        10, kill_on_close=True, platform="win32", api=api, suspended=True, stop_latched=lambda: latched
    )


def test_a_suspended_spawn_is_assigned_then_resumed() -> None:
    api = FakeKernel()
    tree = _suspended(api)
    assert api.names() == ["create_job", "assign", "resume"]
    assert tree.contained and tree.state == "open"


def test_a_latched_stop_never_resumes() -> None:
    """The stop latch is read before the resume: set, the process is ended, never run."""

    api = FakeKernel()
    with pytest.raises(_process_tree.SuspendedLaunchError) as caught:
        _suspended(api, latched=True)
    assert "resume" not in api.names()
    assert api.names()[-5:] == ["open", "terminate_process", "wait", "close_process", "close_job"]
    assert caught.value.tree is None


@pytest.mark.parametrize("step", ["create_job", "assign"])
def test_assign_failure_terminates_never_resumes_uncontained(step: str) -> None:
    api = FakeKernel()
    api.fail.add(step)
    with pytest.raises(_process_tree.SuspendedLaunchError) as caught:
        _suspended(api)
    assert "resume" not in api.names()
    assert ("terminate_process", 10) in api.calls and ("wait", 10) in api.calls
    assert caught.value.tree is None
    if step == "assign":
        assert ("close_job", 7) in api.calls, "the job that held nothing is released"


def test_resume_failure_terminates_and_waits() -> None:
    api = FakeKernel()
    api.fail.add("resume")
    with pytest.raises(_process_tree.SuspendedLaunchError):
        _suspended(api)
    assert api.names()[-5:] == ["open", "terminate_process", "wait", "close_process", "close_job"]


@pytest.mark.parametrize("step", ["terminate_process", "wait_handle", "open_member"])
def test_a_failed_terminate_keeps_ownership(step: str) -> None:
    """Ending the process failed: the error carries the tree, which still holds it."""

    api = FakeKernel()
    api.fail.update({"resume", step})
    with pytest.raises(_process_tree.SuspendedLaunchError) as caught:
        _suspended(api)
    tree = caught.value.tree
    assert tree is not None and tree.state == "terminating" and not tree.closed
    if step != "open_member":
        assert tree._held == {10: max(api.owner)} and max(api.owner) in api.open
    assert "close_job" not in api.names(), "the job, and its kill-on-close, stay with the owner"
    tree.close()


def test_missing_ntresumeprocess_spawns_unsuspended() -> None:
    """Checked before anything is created: no ``CREATE_SUSPENDED`` and a plain attach."""

    api = FakeKernel()
    api.resumable = False
    assert ProcessTree.suspend_flags(platform="win32", api=api) == 0
    api.resumable = True
    assert ProcessTree.suspend_flags(platform="win32", api=api) == _process_tree.CREATE_SUSPENDED
    assert ProcessTree.suspend_flags(platform="linux") == 0
    assert api.names() == ["can_resume", "can_resume"]


def test_a_suspended_attach_is_win32_only() -> None:
    with pytest.raises(ValueError, match="win32 only"):
        ProcessTree.attach(10, platform="linux", suspended=True)


# === #759 fix round 1 =========================================================


class _RacingKernel(FakeKernel):
    """``TerminateProcess`` that blocks until released, so a ``close()`` can race it."""

    def __init__(self) -> None:
        super().__init__(members=(10,))
        self.entered = threading.Event()
        self.proceed = threading.Event()

    def terminate_process(self, handle: int) -> None:
        self.entered.set()
        assert self.proceed.wait(5)
        super().terminate_process(handle)


def test_a_close_waits_for_a_terminate_already_under_way() -> None:
    """Codex #5: a kill has taken a member handle; another thread closes the tree.

    The close waits until the kill's kernel call returns, so the handle is not
    closed — and handed by the system to another process — in between. Fails on
    ``f3e9b814``: the close went through at once and ``TerminateProcess`` then
    used a closed handle, recorded as recycled.
    """

    api = _RacingKernel()
    tree = ProcessTree.attach(10, platform="win32", api=api, kill_on_close=False)
    receipt = tree.collect()
    killer = threading.Thread(target=lambda: tree.stop_and_collect(prior=receipt))
    killer.start()
    assert api.entered.wait(5)
    closer = threading.Thread(target=tree.close)
    closer.start()
    closer.join(0.2)
    closing_waited = closer.is_alive()
    for handle in api.owner:
        if handle not in api.open:
            api.recycled[handle] = "an unrelated process"
    api.proceed.set()
    killer.join(5)
    closer.join(5)
    assert api.misuse == [], api.misuse
    assert closing_waited, "close() did not wait for the kill under way"
    assert tree.state == "closed" and api.open == set()


async def test_a_close_during_confirm_is_an_unconfirmed_answer() -> None:
    """Verify F3: a tree closed while its confirm waits ends that confirm ``Unconfirmed``.

    Fails on ``f3e9b814``: ``KeyError`` out of the confirm, its ``_held`` emptied
    by the close.
    """

    api = FakeKernel(members=(10,))
    api.dies_on_terminate = False
    tree = _tree(api)
    receipt = tree.stop_and_collect()
    task = asyncio.ensure_future(tree.confirm(receipt, deadline=100.0, clock=_Clock(), poll=0))
    await asyncio.sleep(0)
    tree.close()
    verdict = await asyncio.wait_for(task, 5)
    assert isinstance(verdict, Unconfirmed) and "closed" in verdict.reason, verdict
    assert api.misuse == []


class _HidingKernel(FakeKernel):
    def __init__(self) -> None:
        super().__init__(members=(10,))
        self.hide = False

    def job_member_pids(self, job: int) -> list[int]:
        return [] if self.hide else super().job_member_pids(job)

    def terminate_job(self, job: int) -> None:
        self.calls.append(("terminate_job", job))


async def test_a_member_found_late_stays_in_the_retry_evidence() -> None:
    """Codex #6: confirm finds member 12 late, holds and terminates it, and ends
    ``Unconfirmed``; the retry's listing no longer shows 12 while its handle is
    not signalled yet. The retry still waits on it.

    Fails on ``f3e9b814``: the retry's receipt held only 10 and it answered
    ``Confirmed()`` with 12 running.
    """

    api = _HidingKernel()
    tree = _tree(api)
    first = tree.stop_and_collect()
    api.members.add(12)
    api.alive.add(12)
    api.dies_on_terminate = False
    assert isinstance(await tree.confirm(first, deadline=0.05, clock=_Clock(), poll=0), Unconfirmed)
    api.hide = True
    # The first receipt again: the confirm waits on every member the tree holds, 12 included.
    again = await tree.confirm(first, deadline=0.05, clock=_Clock(), poll=0)
    assert isinstance(again, Unconfirmed) and "12" in again.reason, again
    retry = tree.stop_and_collect(prior=first)
    assert 12 in {pid for pid, _h in retry.handles}
    verdict = await tree.confirm(retry, deadline=0.05, clock=_Clock(), poll=0)
    assert isinstance(verdict, Unconfirmed) and "12" in verdict.reason, verdict
    api.alive.discard(12)
    api.alive.discard(10)
    assert isinstance(await tree.confirm(retry, deadline=1.0, clock=_Clock(), poll=0), Confirmed)


class _MembershipFails(FakeKernel):
    def in_job(self, handle: int, job: int) -> bool:
        if self.owner.get(handle) == 12:
            raise OSError("IsProcessInJob failed")
        return super().in_job(handle, job)


async def test_a_rescan_whose_membership_check_fails_closes_its_handle_and_is_unconfirmed() -> None:
    """Codex #10: ``IsProcessInJob`` fails for a member the rescan opened.

    The handle is closed and the answer is ``Unconfirmed``. Fails on
    ``f3e9b814``: the ``OSError`` escaped the confirm and handle 12 stayed open,
    beyond ``close()`` and the finalizer.
    """

    api = _MembershipFails(members=(10,))
    tree = _tree(api)
    receipt = tree.stop_and_collect()
    api.members.add(12)
    api.alive.add(12)
    verdict = await tree.confirm(receipt, deadline=1.0, clock=_Clock(), poll=0)
    assert isinstance(verdict, Unconfirmed) and "12" in verdict.reason, verdict
    tree.close()
    assert api.open == set()


class _Stranger(FakeKernel):
    """Lists 12, whose handle answers ``IsProcessInJob`` false: not a member."""

    def in_job(self, handle: int, job: int) -> bool:
        return self.owner.get(handle) != 12 and super().in_job(handle, job)


@pytest.mark.parametrize("when", ["collect", "rescan"])
async def test_a_listed_process_that_is_not_a_member_is_closed_never_held_or_terminated(when: str) -> None:
    """J13, J26: a handle that ``IsProcessInJob`` denies is closed at once; nothing is sent through it."""

    api = _Stranger(members=(10,))
    tree = _tree(api)
    if when == "collect":
        api.members.add(12)
        api.alive.add(12)
        receipt = tree.stop_and_collect()
    else:
        receipt = tree.stop_and_collect()
        api.members.add(12)
        api.alive.add(12)
        await tree.confirm(receipt, deadline=1.0, clock=_Clock(), poll=0)
    assert 12 not in tree._held
    assert ("terminate_process", 12) not in api.calls
    assert ("close_process", 12) in api.calls
    tree.close()


def test_an_earlier_member_that_is_gone_adds_no_error() -> None:
    """J27: a member an earlier receipt named that has exited and cannot be opened is no error."""

    api = FakeKernel()
    tree = _tree(api)
    first = tree.collect()
    _process_tree._close_held(api, tree._held)
    api.members.discard(11)
    api.alive.discard(11)
    api.fail.add("open_member")
    api.members.discard(10)
    later = tree.collect(prior=first)
    assert later.errors == ()


class _StructKernel:
    """``QueryInformationJobObject``/``SetInformationJobObject`` over one real ctypes struct."""

    def __init__(self) -> None:
        self.received: dict[str, int] = {}

    def QueryInformationJobObject(self, job: int, kind: int, ptr: Any, size: int, unused: Any) -> int:  # noqa: N802
        data = ctypes.cast(ptr, ctypes.POINTER(_process_tree._JOBOBJECT_EXTENDED_LIMIT_INFORMATION)).contents
        data.BasicLimitInformation.LimitFlags = KILL_ON_CLOSE | 0x100
        data.ProcessMemoryLimit = 64 * 1024 * 1024
        return 1

    def SetInformationJobObject(self, job: int, kind: int, ptr: Any, size: int) -> int:  # noqa: N802
        data = ctypes.cast(ptr, ctypes.POINTER(_process_tree._JOBOBJECT_EXTENDED_LIMIT_INFORMATION)).contents
        self.received = {
            "flags": data.BasicLimitInformation.LimitFlags,
            "ProcessMemoryLimit": data.ProcessMemoryLimit,
        }
        return 1


def test_preserve_keeps_every_other_limit_value() -> None:
    """Codex #8 / verify F7: the kernel api clears the flag and keeps the values.

    The job has ``JOB_OBJECT_LIMIT_PROCESS_MEMORY`` (0x100) at 64 MiB. Fails on
    ``f3e9b814``: the struct written back was a zeroed one, so
    ``ProcessMemoryLimit`` became 0 under a flag that still enforces it.
    """

    api = object.__new__(_process_tree._KernelApi)
    kernel = _StructKernel()
    api._kernel32 = kernel
    api._last_error = lambda: 0
    api.set_limit_flags(7, api.limit_flags(7) & ~KILL_ON_CLOSE)
    assert kernel.received == {"flags": 0x100, "ProcessMemoryLimit": 64 * 1024 * 1024}


def test_a_suspended_launch_ends_the_process_through_the_handle_it_was_given() -> None:
    """Codex #9: the job cannot be created and reopening the process is refused,
    but the launch was handed a handle: its own copy ends and waits on it.

    Fails on ``f3e9b814``: the abort ignored the handle, could not open one, and
    left the process running (a tree with nothing held).
    """

    api = FakeKernel(members=(10,))
    api.fail.update({"create_job", "open_member"})
    api.open.add(501)
    api.owner[501] = 10
    with pytest.raises(_process_tree.SuspendedLaunchError) as caught:
        ProcessTree.attach(10, platform="win32", api=api, handle=501, suspended=True)
    assert caught.value.tree is None
    assert 10 not in api.alive
    assert 501 in api.open, "the caller's handle stays the caller's"
    assert api.open == {501}, "the tree's own copy is released"


def test_a_suspended_launch_whose_end_times_out_keeps_the_tree() -> None:
    """J17: ``TerminateProcess`` returns but the wait for the process to end times out.

    The tree, still holding the process, rides on the error, ``TERMINATING``.
    """

    api = FakeKernel(members=(10,))
    api.fail.add("resume")
    api.dies_on_terminate = False
    with pytest.raises(_process_tree.SuspendedLaunchError) as caught:
        ProcessTree.attach(10, platform="win32", api=api, suspended=True, stop_latched=lambda: False)
    tree = caught.value.tree
    assert tree is not None and tree.state == "terminating" and "did not end" in str(caught.value)
    assert tree._held and all(handle in api.open for handle in tree._held.values())
    tree.close()


@pytest.mark.parametrize("why", ["latch", "resume"])
def test_a_latched_or_unresumed_launch_ends_the_process_through_its_own_copy_of_the_handle(why: str) -> None:
    """The job and the assignment succeed; then the stop latch is set, or the resume
    fails, on a process that refuses ``OpenProcess``; the launch was given handle 501.
    The process is ended through the tree's duplicate, which is then closed, and the
    caller's 501 stays open (verify-r2 N1). Fails when the duplicate is not held after
    a successful assignment (NT16): the abort cannot open the process and leaves it
    running, with the tree on the error."""

    api = FakeKernel(members=(10,))
    api.fail.add("open_member")
    if why == "resume":
        api.fail.add("resume")
    api.open.add(501)
    api.owner[501] = 10
    with pytest.raises(_process_tree.SuspendedLaunchError) as caught:
        ProcessTree.attach(
            10, platform="win32", api=api, handle=501, suspended=True, stop_latched=lambda: why == "latch"
        )
    assert caught.value.tree is None, caught.value
    assert 10 not in api.alive
    assert api.open == {501}, "the duplicate is closed and the caller's handle stays open"
    assert "resume" not in api.names() or why == "resume"

