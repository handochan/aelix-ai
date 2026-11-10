"""POSIX: a service stop walks the root's descendants before it signals (#722, Codex #4).

Real processes. The root starts a helper in a session (and group) of its own,
then sleeps. On ``a80e81fa`` the service never supplied the walk: the stop
reported ``stopped``, the root was reaped and the slot released, and the helper
ran on (``walked_pids=[]``, ``helper_alive=true``). Now the soft leg walks the
root's descendants (with their start times, and the groups they lead) while the
root lives; the hard leg kills what is still the walked process; the barrier
waits for every walked pid and group to be gone.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.utils._process_identity import pid_is_live, process_started
from aelix_ai.utils._process_tree import ProcessTree, StopReceipt
from aelix_coding_agent.jobs import evidence
from aelix_coding_agent.jobs.contract import ClientOutcome, HelperCleanup

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session

pytestmark = [
    pytest.mark.usefixtures("jobs_active"),
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups and sessions"),
]

_ROOT = (
    "import subprocess, sys, time\n"
    "from pathlib import Path\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
    " start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,"
    " stderr=subprocess.DEVNULL)\n"
    "Path(sys.argv[1]).write_text(str(p.pid))\n"
    "time.sleep(60)\n"
)


class _Runner:
    def __init__(self, params: Any) -> None:
        self.marker = Path(params["marker"])
        self.use_tree = bool(params.get("tree", True))
        self.stopped = asyncio.Event()
        self.proc: Any = None

    async def run(self, ctx: Any) -> ClientOutcome:
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", _ROOT, str(self.marker), start_new_session=True
        )
        tree = ProcessTree.attach(self.proc.pid) if self.use_tree else None
        ctx.register_process(self.proc, pgid=self.proc.pid, tree=tree)
        while not self.marker.exists():
            await asyncio.sleep(0.005)
        ctx.launched()
        await self.stopped.wait()
        return ClientOutcome("stopped", "stopped root")

    def request_stop(self, _reason: str) -> None:
        self.stopped.set()

    async def settle(self) -> None:
        await self.proc.wait()

    async def helper_cleanup(self, _receipt: Any) -> HelperCleanup:
        return HelperCleanup("none")

    def records(self, _outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        return []


@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare-group"])
async def test_a_descendant_in_its_own_group_is_walked_and_stopped(tmp_path: Path, tree: bool) -> None:
    marker = tmp_path / "helper.pid"
    service = fast_service(cleanup_bound=3.0, stop_grace=0.1)
    service.register_kind(_sleeper.SleeperAdapter(), _Runner)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(
        _sleeper.KIND, title="root with a helper", params={"marker": str(marker), "tree": tree}
    )
    helper = int(marker.read_text())
    try:
        snap = await service.stop(job_id)
        record = service._jobs[job_id].processes[0]
        walked = record.receipt.pids if tree else tuple(record.walked)  # type: ignore[union-attr]
        assert helper in walked, "named before the root died"
        assert snap is not None and snap.state == "stopped"
        assert pid_is_live(helper) is False, "the helper is gone before the job is stopped"
        assert service.admission.count(_sleeper.KIND) == 0
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(helper, signal.SIGKILL)
        await service.shutdown()


def test_a_live_process_whose_command_line_changed_is_unresolved_not_lost() -> None:
    """Codex #20: the recorded pid is alive with its recorded start time; its argv
    no longer shows the job's marker (an ``exec``). That proves nothing gone.

    Fails on ``a80e81fa``: the mismatch fell through to ``lost``, "its processes
    are gone and no result was recorded".
    """

    state, why = evidence.classify_restart(
        [{"evidence": {"pid": 10, "started": 1.0, "host": "here"}}],
        None,
        host="here",
        same=lambda _p, _t: True,
        argv=lambda _p: ["changed-argv"],
        argv_contains=("original-argv",),
    )
    assert state == "unresolved" and "still running" in why
    gone, _ = evidence.classify_restart(
        [{"evidence": {"pid": 10, "started": 1.0, "host": "here"}}],
        None,
        host="here",
        same=lambda _p, _t: False,
        argv=lambda _p: None,
        argv_contains=("original-argv",),
    )
    assert gone == "lost"


# === #722 fix round 2: the pid-identity guard at the hard leg (verify SF2) ====
#
# ADR-0293 §5: "SIGKILL deepest first — a pid walked by an earlier collection
# only while it is still the process walked". Real processes stand in for a
# number reused after the walk: ``other`` is alive, and the stamp recorded for
# its pid is not its start time, so it is not the process that was walked. The
# root leads its own group, as a job's root does.


def _sleeper_proc(*, own_group: bool) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=own_group,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _started(proc: subprocess.Popen[bytes]) -> float:
    end = time.monotonic() + 5
    while time.monotonic() < end:
        stamp = process_started(proc.pid)
        if stamp is not None:
            return stamp
        time.sleep(0.01)
    raise AssertionError(f"no start time for {proc.pid}")


@pytest.fixture
def procs() -> Iterator[list[subprocess.Popen[bytes]]]:
    made: list[subprocess.Popen[bytes]] = []
    yield made
    for proc in made:
        with contextlib.suppress(OSError):
            proc.kill()
        proc.wait(5)


def _record(root: subprocess.Popen[bytes], other: int, stamp: float, *, tree: bool) -> evidence.ProcessRecord:
    """A job's root record whose earlier collection walked ``other`` with ``stamp``."""

    record = evidence.ProcessRecord(pid=root.pid, started=_started(root), pgid=root.pid)
    if tree:
        record.tree = ProcessTree.attach(root.pid)
        record.receipt = StopReceipt(
            "stop", root.pid, pids=(other,), started=((other, stamp),), groups=(root.pid,)
        )
    else:
        record.walked[other] = stamp
        record.groups.append(root.pid)
    return record


@pytest.mark.parametrize("own_group", [False, True], ids=["in-our-group", "leads-its-group"])
@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_a_pid_walked_earlier_that_now_names_another_process_gets_no_signal(
    procs: list[subprocess.Popen[bytes]], tree: bool, own_group: bool
) -> None:
    """The hard leg kills the root and its group; ``other`` gets nothing.

    Killing a prior-walked pid without the identity check (verify R08, and R32
    for a record with no tree) kills ``other``. So did the group read on
    ``5ae22118`` for a tree when ``other`` leads a group of its own: the hard
    leg read the leaders of the earlier walk's pids again, found that number
    now leading one, and killed that group whole.
    """

    root = _sleeper_proc(own_group=True)
    procs.append(root)
    other = _sleeper_proc(own_group=own_group)
    procs.append(other)
    record = _record(root, other.pid, _started(other) - 1000.0, tree=tree)

    evidence.kill_now(record)

    root.wait(5)
    time.sleep(0.2)
    assert other.poll() is None, "a process the job never walked was signalled"
    if tree:
        assert record.receipt is not None
        assert other.pid not in record.receipt.groups
        assert dict(record.receipt.started)[other.pid] == _started(other) - 1000.0


@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_a_pid_walked_earlier_that_is_still_that_process_is_killed_on_its_own(
    procs: list[subprocess.Popen[bytes]], tree: bool
) -> None:
    """The per-pid leg alone: ``other`` is still the process walked, in no group
    the job recorded and no longer under the root, so nothing but its own SIGKILL
    reaches it.

    Masked by the group kills in every other test: never killing a prior pid
    (verify R07), or the hard leg passing no identity check, which leaves every
    prior pid unproven (R31), lets ``other`` live.
    """

    root = _sleeper_proc(own_group=True)
    procs.append(root)
    other = _sleeper_proc(own_group=False)
    procs.append(other)
    record = _record(root, other.pid, _started(other), tree=tree)

    evidence.kill_now(record)

    root.wait(5)
    assert other.wait(5) == -signal.SIGKILL


def test_a_later_collection_keeps_the_stamp_of_the_process_it_walked(
    procs: list[subprocess.Popen[bytes]],
) -> None:
    """``prior``: a pid an earlier collection walked keeps that walk's start time,
    also when a later walk names the number again with another one. Re-stamping
    it (verify R10) would make a reused number pass the identity check."""

    root = _sleeper_proc(own_group=True)
    procs.append(root)
    tree = ProcessTree.attach(root.pid)
    prior = StopReceipt("stop", root.pid, pids=(4242,), started=((4242, 1.0),), groups=(root.pid,))
    receipt = tree.collect(
        walk=lambda _pid: [4243, 4242],
        leaders=lambda _pids: [],
        started=lambda pid: {4242: 2.0, 4243: 3.0}[pid],
        prior=prior,
    )
    assert receipt.pids == (4243, 4242)
    assert receipt.started == ((4243, 3.0), (4242, 1.0))


def test_a_later_collection_reads_groups_only_for_earlier_pids_still_the_process_walked(
    procs: list[subprocess.Popen[bytes]],
) -> None:
    """Of an earlier walk's pids, the group read covers only one whose start time
    is the one recorded: not a number reused (another start time), not one whose
    start time cannot be read now or could not be then."""

    root = _sleeper_proc(own_group=True)
    procs.append(root)
    tree = ProcessTree.attach(root.pid)
    now = {4242: 1.0, 4243: 2.0, 4244: None, 4245: None, 4246: 5.0}
    prior = StopReceipt(
        "stop",
        root.pid,
        pids=(4242, 4243, 4244, 4245),
        started=((4242, 1.0), (4243, 1.0), (4244, None), (4245, 1.0)),
        groups=(root.pid,),
    )
    asked: list[list[int]] = []

    def leaders(pids: list[int]) -> list[int]:
        asked.append(list(pids))
        return []

    tree.collect(walk=lambda _pid: [4246], leaders=leaders, started=now.__getitem__, prior=prior)
    assert asked == [[root.pid, 4246, 4242]]


_LEADER = (
    "import subprocess, sys, time\n"
    "from pathlib import Path\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
    " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "Path(sys.argv[1]).write_text(str(p.pid))\n"
    "time.sleep(60)\n"
)


def test_an_earlier_walks_pid_still_that_process_has_the_group_it_leads_read_again(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    """The other side of the group read: a pid walked earlier that is still the
    process walked, and now leads a group with a member the walk cannot reach,
    has that group named at the hard leg and killed whole."""

    marker = tmp_path / "member.pid"
    root = _sleeper_proc(own_group=True)
    procs.append(root)
    leader = subprocess.Popen(
        [sys.executable, "-c", _LEADER, str(marker)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(leader)
    end = time.monotonic() + 5
    while not marker.exists() or not marker.read_text():
        assert time.monotonic() < end, "the leader never started its member"
        time.sleep(0.01)
    member = int(marker.read_text())
    assert os.getpgid(member) == leader.pid, "the member is in the leader's group"
    record = _record(root, leader.pid, _started(leader), tree=True)
    try:
        evidence.kill_now(record)
        assert record.receipt is not None and leader.pid in record.receipt.groups
        end = time.monotonic() + 5
        while pid_is_live(member) is not False:
            assert time.monotonic() < end, "the member of the leader's group survived"
            time.sleep(0.02)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(member, signal.SIGKILL)


@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_a_group_whose_recorded_leader_number_now_leads_another_process_gets_no_signal(
    procs: list[subprocess.Popen[bytes]], tree: bool
) -> None:
    """#611 S1c (item 18): the root's number reads as a process that started at another
    time — reused, and leading a group of its own — so neither its group nor the
    number itself is signalled, by the soft leg or the hard one.

    Fails on ``b387b4b5``: the hard leg ``killpg``-ed the recorded group number
    whatever led it now (the residual ADR-0238 accepts for trees).
    """

    newcomer = _sleeper_proc(own_group=True)
    procs.append(newcomer)
    record = evidence.ProcessRecord(
        pid=newcomer.pid, started=_started(newcomer) - 1000.0, pgid=newcomer.pid
    )
    if tree:
        record.tree = ProcessTree.attach(newcomer.pid)
        record.receipt = StopReceipt("stop", newcomer.pid, groups=(newcomer.pid,))
    else:
        record.groups.append(newcomer.pid)

    asyncio.run(evidence.stop_legs([record], grace=0.1))
    evidence.stop_descendants([record])
    # The soft leg on its own: ``stop_legs`` sends it only to a root not yet
    # gone, which a moved number never is, so it is driven directly.
    evidence._signal_record(record, hard=False)

    time.sleep(0.2)
    assert newcomer.poll() is None, "another process's group was signalled"


# === #723 fix round 2: gone is not moved (verify-r2 SF1) ======================
#
# The leader check above must not cost the ordinary case its kill: a job's root
# that exited on its own while a member of its group lives on. Its number is
# gone, not another process's, so the group still gets its ``killpg``. A zombie
# leader is read with no start time on macOS and with the recorded one on
# Linux; either way it is not a reuse.

_EXITING_LEADER = (
    "import os, subprocess, sys, time\n"
    "from pathlib import Path\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
    " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "Path(sys.argv[1]).write_text(str(p.pid))\n"
    "while not os.path.exists(sys.argv[2]):\n"
    "    time.sleep(0.01)\n"
)


def _zombie(pid: int) -> bool:
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return state.stdout.strip().startswith("Z")


@pytest.mark.parametrize("legs", ["stop_descendants", "stop_legs"])
@pytest.mark.parametrize("leader_end", ["reaped", "zombie"])
@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_a_group_whose_recorded_leader_is_gone_is_still_killed(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]], tree: bool, leader_end: str, legs: str
) -> None:
    """The root leads its group and exits on its own, leaving a member in it;
    the legs then run. The group gets its ``killpg``, and the barrier confirms
    it empty.

    Kills verify-r2's S04c, which reads "a start time recorded, none readable
    now" as a moved number in the record-with-no-tree legs: with it the member
    outlives the legs in the ``bare`` cases (the ``bare-zombie`` ones on macOS
    only, where a zombie has no start time). The ``tree`` cases are guards of
    ``ProcessTree``'s own reading of the root.
    """

    marker, go = tmp_path / "member.pid", tmp_path / "go"
    leader = subprocess.Popen(
        [sys.executable, "-c", _EXITING_LEADER, str(marker), str(go)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(leader)
    end = time.monotonic() + 5
    while not marker.exists() or not marker.read_text():
        assert time.monotonic() < end, "the leader never started its member"
        time.sleep(0.01)
    member = int(marker.read_text())
    try:
        assert os.getpgid(member) == leader.pid, "the member is in the leader's group"
        record = evidence.ProcessRecord(pid=leader.pid, started=_started(leader), pgid=leader.pid)
        if tree:
            record.tree = ProcessTree.attach(leader.pid)
        else:
            record.groups.append(leader.pid)
        go.write_text("")
        if leader_end == "reaped":
            leader.wait(5)
        else:
            end = time.monotonic() + 5
            while not _zombie(leader.pid):
                assert time.monotonic() < end, "the leader never exited"
                time.sleep(0.01)
        assert pid_is_live(member) is True

        if legs == "stop_descendants":
            evidence.stop_descendants([record])
        else:
            asyncio.run(evidence.stop_legs([record], grace=0.1))
        leader.wait(5)  # the zombie's reap is its parent's: the service's ``proc.wait()``

        confirmed, why = asyncio.run(evidence.confirm_gone([record], time.monotonic() + 5))
        assert confirmed, why
        assert pid_is_live(member) is False, "a member of the group outlived the barrier"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(member, signal.SIGKILL)


@pytest.mark.parametrize("moved", [True, False], ids=["moved", "same"])
def test_a_root_that_leads_no_group_is_killed_only_while_its_number_is_its_own(
    procs: list[subprocess.Popen[bytes]], moved: bool
) -> None:
    """A tree whose root leads no group (it stayed in ours): its hard leg has no
    group to kill, so the root gets a SIGKILL of its own — but not once its
    number reads as a process that started at another time.

    ``moved`` kills verify-r2's S02, which drops that check from
    ``ProcessTree._kill_posix``'s root kill: the newcomer is killed. ``same``
    is a guard: the root that is still the one recorded is killed.
    """

    root = _sleeper_proc(own_group=False)
    procs.append(root)
    stamp = _started(root)
    record = evidence.ProcessRecord(pid=root.pid, started=stamp - 1000.0 if moved else stamp)
    record.tree = ProcessTree.attach(root.pid)
    assert not record.tree.contained, "the root stayed in this process's group"

    evidence.kill_now(record)

    assert record.receipt is not None and record.receipt.groups == ()
    if moved:
        time.sleep(0.2)
        assert root.poll() is None, "a process that only took the root's number was killed"
    else:
        assert root.wait(5) == -signal.SIGKILL
