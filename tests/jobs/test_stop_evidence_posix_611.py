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
        # Not running: since #611 S2a the barrier counts a zombie as gone, and the
        # member's reaper (init, or docker's ``--init``) may not have reaped it yet.
        assert not _running(member), "a member of the group outlived the barrier"
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


# === #611 S2a (#758): identity before every signal, and the confirm step's rules ===
#
# The service legs are behind ``jobs.activation.ACTIVE``; these drive them
# directly. A reused number is simulated as above: a recorded start time 1000 s
# off the real one, or a start-time reader that answers so.


def _leader_with_member(tmp_path: Path, procs: list[subprocess.Popen[bytes]]) -> tuple[subprocess.Popen[bytes], int]:
    """A process leading its own group, with a member (its child) in that group."""

    marker = tmp_path / "member.pid"
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
    assert os.getpgid(member) == leader.pid
    return leader, member


def _running(pid: int) -> bool:
    """Alive and not a zombie, from ONE ``ps`` read.

    Not ``pid_is_live`` then ``ps``: a zombie its reaper (init, docker's
    ``--init``) takes between the two reads looked alive to the first and
    absent to the second, and read as running (#758 fix round 1, verify F1).
    No ``ps`` row is gone.
    """

    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_a_null_stamp_live_leader_is_never_signalled(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]], tree: bool
) -> None:
    """PE3: a walked pid leads a group, lives, and its start time was never read.

    Neither it nor its group is signalled: nothing says the number is still the
    process walked. Fails on ``3cab5d16``: ``_moved(g, None)`` (and the tree's
    ``moved``) answered False, so the group was killed whole.
    """

    root = _sleeper_proc(own_group=True)
    procs.append(root)
    leader, member = _leader_with_member(tmp_path, procs)
    record = evidence.ProcessRecord(pid=root.pid, started=_started(root), pgid=root.pid)
    if tree:
        record.tree = ProcessTree.attach(root.pid)
        record.receipt = StopReceipt(
            "stop", root.pid, pids=(leader.pid,), started=((leader.pid, None),), groups=(root.pid, leader.pid)
        )
    else:
        record.walked[leader.pid] = None
        record.groups.extend([root.pid, leader.pid])
    try:
        evidence.kill_now(record)
        root.wait(5)
        time.sleep(0.2)
        assert _running(member), "the group of a leader of unknown identity was signalled"
        assert _running(leader.pid), "a leader of unknown identity was signalled"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(member, signal.SIGKILL)


def test_a_fresh_pid_whose_stamp_changed_gets_no_kill(procs: list[subprocess.Popen[bytes]]) -> None:
    """PE2 (Codex #4's probe shape): a pid THIS collection walked is checked too.

    Its start time is read after the walk; the number then reads as another
    process. Fails on ``3cab5d16``: a freshly walked pid was SIGKILLed with no
    call to ``same`` at all.
    """

    root = _sleeper_proc(own_group=True)
    procs.append(root)
    other = _sleeper_proc(own_group=False)
    procs.append(other)
    real = _started(other)
    tree = ProcessTree.attach(root.pid)

    tree.stop_and_collect(
        walk=lambda _pid: [other.pid],
        leaders=lambda _pids: [],
        started=lambda pid: real - 1000.0 if pid == other.pid else process_started(pid),
        same=evidence.same_process,
        root_started=_started(root),
    )

    root.wait(5)
    time.sleep(0.2)
    assert other.poll() is None, "a freshly walked number that reads as another process was killed"


@pytest.mark.parametrize("stamp", ["recorded", "null"])
@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_a_dead_leader_group_with_no_verifiable_member_is_killed(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]], tree: bool, stamp: str
) -> None:
    """The owner's OQ-D answer (2026-11-10): a walked leader that is GONE gives authority.

    The leader exited and was reaped; its member, which nothing walked, is left
    in its group. The group is killed, recorded stamp or none: the kernel keeps
    a group's number while a member lives, so it is still the group recorded.
    The residual (ADR-0291's note): the group empties and its number goes to a
    new leader between the read and the ``killpg``. Green on ``3cab5d16`` too,
    where gone was not moved; it pins the rule against reading gone as unknown.
    """

    marker, go = tmp_path / "member.pid", tmp_path / "go"
    root = _sleeper_proc(own_group=True)
    procs.append(root)
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
        assert time.monotonic() < end
        time.sleep(0.01)
    member = int(marker.read_text())
    recorded = _started(leader) if stamp == "recorded" else None
    go.write_text("")
    leader.wait(5)
    record = evidence.ProcessRecord(pid=root.pid, started=_started(root), pgid=root.pid)
    if tree:
        record.tree = ProcessTree.attach(root.pid)
        record.receipt = StopReceipt(
            "stop", root.pid, pids=(leader.pid,), started=((leader.pid, recorded),), groups=(root.pid, leader.pid)
        )
    else:
        record.walked[leader.pid] = recorded
        record.groups.extend([root.pid, leader.pid])
    try:
        evidence.kill_now(record)
        end = time.monotonic() + 5
        while pid_is_live(member) is not False:
            assert time.monotonic() < end, "a dead leader's group with a member left was not killed"
            time.sleep(0.02)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(member, signal.SIGKILL)


@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_a_zombie_leader_is_gone_not_moved(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]], tree: bool
) -> None:
    """A walked leader that exited and is not yet reaped, with no start time recorded.

    A zombie holds its number and its group until reaped, so it is gone, never
    another process: its group (with a live member) is killed. Green on
    ``3cab5d16``; it pins the zombie half of ``leader_authorizes``, without which
    a null-stamp zombie would read as a live leader of unknown identity.
    """

    marker, go = tmp_path / "member.pid", tmp_path / "go"
    root = _sleeper_proc(own_group=True)
    procs.append(root)
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
        assert time.monotonic() < end
        time.sleep(0.01)
    member = int(marker.read_text())
    go.write_text("")
    end = time.monotonic() + 5
    while not _zombie(leader.pid):
        assert time.monotonic() < end, "the leader never exited"
        time.sleep(0.01)
    record = evidence.ProcessRecord(pid=root.pid, started=_started(root), pgid=root.pid)
    if tree:
        record.tree = ProcessTree.attach(root.pid)
        record.receipt = StopReceipt(
            "stop", root.pid, pids=(leader.pid,), started=((leader.pid, None),), groups=(root.pid, leader.pid)
        )
    else:
        record.walked[leader.pid] = None
        record.groups.extend([root.pid, leader.pid])
    try:
        evidence.kill_now(record)
        end = time.monotonic() + 5
        while pid_is_live(member) is not False:
            assert time.monotonic() < end, "a zombie leader's group was not killed"
            time.sleep(0.02)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(member, signal.SIGKILL)


def _gone_root(procs: list[subprocess.Popen[bytes]], *, tree: bool) -> evidence.ProcessRecord:
    """A record whose root ran, was stopped and was reaped: the barrier's own step is done."""

    root = _sleeper_proc(own_group=True)
    procs.append(root)
    record = evidence.ProcessRecord(pid=root.pid, started=_started(root))
    if tree:
        record.tree = ProcessTree.attach(root.pid)
    root.kill()
    root.wait(5)
    return record


@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_confirm_counts_a_zombie_only_group_as_empty(
    procs: list[subprocess.Popen[bytes]], tree: bool
) -> None:
    """A recorded group whose one member exited and waits to be reaped is empty to the barrier.

    ``killpg(g, 0)`` still answers for it (success on Linux, ``EPERM`` on macOS,
    measured), but it runs nothing. Fails on ``3cab5d16``: the barrier waited
    on it to the deadline, "process group … not confirmed empty".
    """

    record = _gone_root(procs, tree=tree)
    zombie = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    procs.append(zombie)
    end = time.monotonic() + 5
    while not _zombie(zombie.pid):
        assert time.monotonic() < end
        time.sleep(0.01)
    if tree:
        record.receipt = StopReceipt("stop", record.pid, groups=(zombie.pid,))
    else:
        record.groups.append(zombie.pid)

    confirmed, why = asyncio.run(evidence.confirm_gone([record], time.monotonic() + 1.0))

    assert confirmed, why


_WAITS_FOR_GO = "import os, sys, time\nwhile not os.path.exists(sys.argv[1]):\n    time.sleep(0.01)\n"


@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_confirm_counts_a_zombie_walked_pid_as_gone(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]], tree: bool
) -> None:
    """A walked pid that exited and is not reaped yet is gone to the barrier.

    Its number is still its own (Linux reads the same start time, macOS reads
    none), so ``same_process`` alone never said "gone". Fails on ``3cab5d16``:
    "pid … not confirmed stopped" at the deadline.
    """

    record = _gone_root(procs, tree=tree)
    go = tmp_path / "go"
    walked = subprocess.Popen([sys.executable, "-c", _WAITS_FOR_GO, str(go)])
    procs.append(walked)
    stamp = _started(walked)
    go.write_text("")
    end = time.monotonic() + 5
    while not _zombie(walked.pid):
        assert time.monotonic() < end
        time.sleep(0.01)
    if tree:
        record.receipt = StopReceipt("stop", record.pid, pids=(walked.pid,), started=((walked.pid, stamp),))
    else:
        record.walked[walked.pid] = stamp

    confirmed, why = asyncio.run(evidence.confirm_gone([record], time.monotonic() + 1.0))

    assert confirmed, why


_SUBREAPER = """
import asyncio, ctypes, os, subprocess, sys, time
sys.path[:0] = {path!r}
from aelix_ai.utils._process_identity import process_started
from aelix_coding_agent.jobs import evidence

PR_SET_CHILD_SUBREAPER = 36
ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
go, out = sys.argv[1], sys.argv[2]
middle = subprocess.Popen([sys.executable, "-c",
    "import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]);"
    " print(p.pid, flush=True)", {waits!r}, go], stdout=subprocess.PIPE)
adopted = int(middle.stdout.readline())
middle.wait()
stamp = process_started(adopted)
root = subprocess.Popen([sys.executable, "-c", "pass"])
root.wait()
record = evidence.ProcessRecord(pid=root.pid, started=None)
record.walked[adopted] = stamp
open(go, "w").close()
time.sleep(0.5)
state = open("/proc/%d/stat" % adopted).read().rpartition(")")[2].split()[0]
ok, why = asyncio.run(evidence.confirm_gone([record], time.monotonic() + 1.0))
print(state, ok, why, flush=True)
os.waitpid(adopted, 0)
"""


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_CHILD_SUBREAPER is Linux")
def test_confirm_counts_an_adopted_zombie_as_gone_under_a_subreaper(tmp_path: Path) -> None:
    """The shape S2c's armed agent has: a subreaper whose adopted descendant exited.

    The subreaper does not reap it (r3, the lead's decision 2), so it stays a
    zombie under it, and the agent's own barrier must not wait on it. Run in a
    child process so the subreaper bit stays out of the test process. Fails on
    ``3cab5d16``: ``Z False pid … not confirmed stopped``.
    """

    script = _SUBREAPER.format(path=sys.path, waits=_WAITS_FOR_GO)
    done = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "go"), str(tmp_path / "out")],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split()[:2] == ["Z", "True"], done.stdout


@pytest.mark.parametrize("whose", ["walked-leader", "root"])
@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_confirm_does_not_wait_on_a_recorded_group_whose_leader_moved(
    procs: list[subprocess.Popen[bytes]], tree: bool, whose: str
) -> None:
    """#731 item 1: a recorded group whose leader's number now reads as another process.

    The legs leave it alone, so the barrier does not wait for it to empty: it is
    not the job's. Fails on ``3cab5d16``: the barrier waited on the other
    process's live group to the deadline, and the job would read ``unresolved``.
    """

    newcomer = _sleeper_proc(own_group=True)
    procs.append(newcomer)
    moved = _started(newcomer) - 1000.0
    if whose == "root":
        record = evidence.ProcessRecord(pid=newcomer.pid, started=moved, pgid=newcomer.pid)
        if tree:
            record.tree = ProcessTree.attach(newcomer.pid)
            record.receipt = StopReceipt("stop", newcomer.pid, groups=(newcomer.pid,))
    else:
        record = _gone_root(procs, tree=tree)
        if tree:
            record.receipt = StopReceipt(
                "stop", record.pid, pids=(newcomer.pid,), started=((newcomer.pid, moved),), groups=(newcomer.pid,)
            )
        else:
            record.walked[newcomer.pid] = moved
            record.groups.append(newcomer.pid)

    confirmed, why = asyncio.run(evidence.confirm_gone([record], time.monotonic() + 1.0))

    assert confirmed, why
    assert newcomer.poll() is None


_PARENT_OF_ONE = (
    "import subprocess, sys, time\n"
    "from pathlib import Path\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
    " start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,"
    " stderr=subprocess.DEVNULL)\n"
    "Path(sys.argv[1]).write_text(str(p.pid))\n"
    "time.sleep(60)\n"
)


class _Unreaped:
    returncode = None

    def __init__(self, pid: int) -> None:
        self.pid = pid


@pytest.mark.parametrize("legs", ["kill_now", "stop_descendants", "stop_legs"])
def test_the_tree_path_checks_the_roots_identity_before_walking(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]], legs: str
) -> None:
    """#731 item 2: the root's number reads as another process, which has a child.

    The tree path must not walk that number: what it would name is the other
    process's children. Fails on ``3cab5d16``: the tree path walked it and
    SIGKILLed the child (a freshly walked pid, killed unchecked).
    """

    marker = tmp_path / "child.pid"
    newcomer = subprocess.Popen(
        [sys.executable, "-c", _PARENT_OF_ONE, str(marker)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(newcomer)
    end = time.monotonic() + 5
    while not marker.exists() or not marker.read_text():
        assert time.monotonic() < end
        time.sleep(0.01)
    child = int(marker.read_text())
    record = evidence.ProcessRecord(
        pid=newcomer.pid, started=_started(newcomer) - 1000.0, pgid=newcomer.pid
    )
    record.tree = ProcessTree.attach(newcomer.pid)
    try:
        if legs == "kill_now":
            evidence.kill_now(record)
        elif legs == "stop_legs":
            # The soft leg collects too: a walk there would put the child in the
            # receipt, and the hard leg would then kill it as a pid walked earlier.
            # It runs for a root not yet seen gone — here the asyncio watcher's
            # lag: a ``returncode`` still ``None`` though the number moved on.
            record.proc = _Unreaped(newcomer.pid)
            asyncio.run(evidence.stop_legs([record], grace=0.1))
        else:
            evidence.stop_descendants([record])
        time.sleep(0.2)
        assert _running(child), "a child of the process that took the root's number was killed"
        assert newcomer.poll() is None
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, signal.SIGKILL)


def test_a_fresh_pid_whose_start_time_cannot_be_read_still_has_its_group_read(
    procs: list[subprocess.Popen[bytes]],
) -> None:
    """#731's O4: the group read covers every pid THIS walk found, readable start time or not.

    O4 (#722's last verify) read the groups only of pids whose start time
    matched, fresh ones included; it differs from the code only for a fresh pid
    whose start time cannot be read, and then drops that pid's group from the
    evidence. Green on ``3cab5d16``; red under O4.
    """

    root = _sleeper_proc(own_group=True)
    procs.append(root)
    tree = ProcessTree.attach(root.pid)
    asked: list[list[int]] = []

    def leaders(pids: list[int]) -> list[int]:
        asked.append(list(pids))
        return []

    tree.collect(walk=lambda _pid: [4246], leaders=leaders, started=lambda _pid: None)
    assert asked == [[root.pid, 4246]]


# === #758 fix round 1 =========================================================


class _Sends:
    """``os.kill``/``os.killpg`` recorded and never sent: the pids below are fake."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sent: list[tuple[str, int]] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: self.sent.append(("kill", pid)) if sig else None)
        monkeypatch.setattr(os, "killpg", lambda pid, sig: self.sent.append(("killpg", pid)) if sig else None)


@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_a_child_replaced_between_the_walk_and_its_stamp_gets_no_signal(
    monkeypatch: pytest.MonkeyPatch, tree: bool
) -> None:
    """Codex #2: the walk names owned child 20; by the time its start time is read
    the number holds a stranger. The identity recorded must be the one the walk
    read with the parent, so the stranger is never signalled.

    Fails on ``f3e9b814``: a pid-only walk, then ``process_started(20)`` read the
    stranger's time, and ``same_process`` then passed it: ``kill(20)``.
    """

    from aelix_ai.utils import _process_walk

    owned, stranger = 5.0, 99.0

    def now(pid: int) -> float:
        return 1.0 if pid == 10 else stranger

    monkeypatch.setattr(_process_walk, "descendant_pids", lambda _pid: [20])
    monkeypatch.setattr(
        _process_walk, "descendant_identities", lambda _pid: [_process_walk.WalkEntry(20, owned, 10)]
    )
    monkeypatch.setattr(evidence, "process_started", now)
    monkeypatch.setattr(evidence, "same_process", lambda pid, stamp: now(pid) == stamp)
    monkeypatch.setattr(evidence, "_leaders", lambda _pids: [])
    sends = _Sends(monkeypatch)
    record = evidence.ProcessRecord(10, 1.0, tree=ProcessTree(10, contained=False) if tree else None)

    evidence.kill_now(record)

    assert ("kill", 20) not in sends.sent, sends.sent
    walked = dict(record.receipt.started) if tree else record.walked  # type: ignore[union-attr]
    assert walked == {20: owned}


def _root_and_member(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]]
) -> tuple[subprocess.Popen[bytes], int]:
    return _leader_with_member(tmp_path, procs)


def _leg(record: evidence.ProcessRecord, leg: str) -> None:
    if leg == "hard":
        evidence.kill_now(record)
    elif leg == "soft":
        evidence._signal_record(record, hard=False)
    else:
        evidence.stop_descendants([record])


_LEGS = pytest.mark.parametrize("leg", ["hard", "soft", "stop_descendants"])


@_LEGS
@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_a_root_number_naming_a_live_leader_of_unknown_start_time_is_not_group_killed(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]], tree: bool, leg: str
) -> None:
    """PE3 for the root's own group (Codex #4; verify F2, shape A).

    The record has no start time for its root, and the root's number now names
    a live leader ``S`` with a member. Nothing proves ``S`` is the job's: no leg
    signals its group or ``S``. Fails on ``f3e9b814`` on every leg: the root
    branches still asked ``_moved(S, None)``, which is False, and killed.
    """

    s, member = _root_and_member(tmp_path, procs)
    record = evidence.ProcessRecord(pid=s.pid, started=None, pgid=s.pid)
    if tree:
        record.tree = ProcessTree.attach(s.pid)
    try:
        _leg(record, leg)
        time.sleep(0.3)
        assert _running(member), "a group nothing proved the job's was signalled"
        assert s.poll() is None
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(member, signal.SIGKILL)


@_LEGS
def test_a_registered_group_whose_live_leader_has_no_start_time_is_not_killed(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]], leg: str
) -> None:
    """Verify F2, shape B: the root is the job's own and lives; the group the
    client registered is led by ``S``, whose start time was never recorded.
    ``S``'s group gets nothing. Fails on ``f3e9b814``: ``killpg(S)``.
    """

    s, member = _root_and_member(tmp_path, procs)
    root = _sleeper_proc(own_group=True)
    procs.append(root)
    record = evidence.ProcessRecord(pid=root.pid, started=_started(root), pgid=s.pid)
    try:
        _leg(record, leg)
        time.sleep(0.3)
        assert _running(member), "a registered group of unknown leader was signalled"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(member, signal.SIGKILL)


@_LEGS
def test_the_jobs_own_unreaped_root_still_gives_authority_over_its_group(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]], leg: str
) -> None:
    """The guard on the other side: no start time recorded, but the root is the
    service's own ``Process`` and not reaped — its group is still killed (or sent
    SIGTERM, on the soft leg). Green on ``f3e9b814`` and after."""

    s, member = _root_and_member(tmp_path, procs)
    record = evidence.ProcessRecord(pid=s.pid, started=None, pgid=s.pid, proc=s)
    try:
        _leg(record, leg)
        end = time.monotonic() + 5
        while _running(member):
            assert time.monotonic() < end, "the job's own root's group was not signalled"
            time.sleep(0.02)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(member, signal.SIGKILL)


#: Leads its group and leaves a member in it with PPID 1 (a double fork), so no walk
#: names the member and only the root's group reaches it.
_ORPHAN_LEADER = (
    "import os, sys, time\n"
    "r, w = os.pipe()\n"
    "mid = os.fork()\n"
    "if mid == 0:\n"
    "    m = os.fork()\n"
    "    if m == 0:\n"
    "        time.sleep(60)\n"
    "        os._exit(0)\n"
    "    os.write(w, str(m).encode())\n"
    "    os._exit(0)\n"
    "os.close(w)\n"
    "m = os.read(r, 100).decode()\n"
    "os.waitpid(mid, 0)\n"
    "open(sys.argv[1] + '.tmp', 'w').write(m)\n"
    "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
    "time.sleep(60)\n"
)


@_LEGS
def test_the_jobs_own_unreaped_root_gives_authority_on_the_tree_path_too(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]], leg: str
) -> None:
    """The guard above, with a tree (verify-r2 N1): no start time recorded, the root
    is the service's own unreaped ``Process``, and ``ProcessTree`` gets that as
    ``root_live``. Its group — which holds a member no walk names (PPID 1) — is
    still killed (or sent SIGTERM, on the soft leg). Fails on the hard leg and
    ``stop_descendants`` with ``_root_live`` answering False (NE5) or
    ``_root_authorized`` ignoring ``root_live`` (NT2)."""

    marker = tmp_path / "orphan.pid"
    s = subprocess.Popen(
        [sys.executable, "-c", _ORPHAN_LEADER, str(marker)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(s)
    end = time.monotonic() + 5
    while not marker.exists() or not marker.read_text():
        assert time.monotonic() < end, "the root never left its member behind"
        time.sleep(0.01)
    member = int(marker.read_text())
    assert os.getpgid(member) == s.pid
    record = evidence.ProcessRecord(
        pid=s.pid, started=None, pgid=s.pid, proc=s, tree=ProcessTree.attach(s.pid)
    )
    try:
        _leg(record, leg)
        end = time.monotonic() + 5
        while _running(member):
            assert time.monotonic() < end, "the job's own root's group was not signalled"
            time.sleep(0.02)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(member, signal.SIGKILL)


def test_an_unreadable_member_makes_the_listing_unknown(tmp_path: Path) -> None:
    """Codex #7: a group's listing skips nothing it cannot read.

    ``/proc`` holds a zombie ``20`` and a process ``30`` whose ``stat`` cannot be
    read; ``30`` may be a live member, so the listing is unknown (``None``) and
    the group is not "only zombies". Fails on ``f3e9b814``, which dropped ``30``
    and answered ``[20]``.
    """

    from aelix_ai.utils import _process_walk

    proc = tmp_path / "proc"
    (proc / "20").mkdir(parents=True)
    (proc / "20" / "stat").write_text("20 (z) Z 1 10 " + "0 " * 16 + "123\n", encoding="utf-8")
    (proc / "30" / "stat").mkdir(parents=True)  # read_text raises IsADirectoryError
    (proc / "40").mkdir()  # exited before its stat was read: not a member

    assert _process_walk.group_members(10, proc=proc) is None
    short = tmp_path / "short"
    (short / "50").mkdir(parents=True)
    (short / "50" / "stat").write_text("50 (x) R\n", encoding="utf-8")  # too short to name a group
    assert _process_walk.group_members(10, proc=short) is None


def test_a_root_of_unknown_start_time_that_is_not_the_jobs_process_is_not_walked(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    """E2: no start time recorded and no ``Process`` of the job's: the root's number
    is not proven its own, so it is not walked and its child is not signalled.
    Green on ``f3e9b814``; it pins ``_root_walkable``'s fallback to ``identity_holds``."""

    marker = tmp_path / "child.pid"
    newcomer = subprocess.Popen(
        [sys.executable, "-c", _PARENT_OF_ONE, str(marker)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(newcomer)
    end = time.monotonic() + 5
    while not marker.exists() or not marker.read_text():
        assert time.monotonic() < end
        time.sleep(0.01)
    child = int(marker.read_text())
    record = evidence.ProcessRecord(pid=newcomer.pid, started=None, pgid=newcomer.pid)
    record.tree = ProcessTree.attach(newcomer.pid)
    try:
        evidence.kill_now(record)
        time.sleep(0.2)
        assert _running(child)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, signal.SIGKILL)


def test_only_zombies_needs_a_whole_listing_of_zombies(monkeypatch: pytest.MonkeyPatch) -> None:
    """W4, W5: an empty or unknown listing, or one live member beside a zombie, is not "only zombies"."""

    from aelix_ai.utils import _process_identity, _process_walk

    monkeypatch.setattr(_process_identity, "pid_is_zombie", lambda pid: pid == 20)
    for listed, answer in (([20], True), ([20, 30], False), ([], False), (None, False)):
        monkeypatch.setattr(_process_walk, "group_members", lambda _g, listed=listed: listed)
        assert _process_walk.group_only_zombies(10) is answer, listed


@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
async def test_a_leader_whose_number_moves_during_the_wait_is_not_waited_on(
    monkeypatch: pytest.MonkeyPatch, tree: bool
) -> None:
    """Codex #11: the recorded leader is itself at the first poll, and its number
    reads as another process from the second. The barrier stops waiting for it
    within this confirm. Fails on ``f3e9b814``, which read the leader once,
    before the wait, and waited on the other process's group to the deadline."""

    from aelix_ai.utils import _process_identity, _process_tree, _process_walk

    state = {"stamp": 1.0}

    def alive(_group: int) -> bool:
        state["stamp"] = 2.0
        return True

    monkeypatch.setattr(_process_identity, "process_started", lambda _pid: state["stamp"])
    monkeypatch.setattr(evidence, "process_started", lambda _pid: state["stamp"])
    monkeypatch.setattr(_process_tree, "_group_alive", alive)
    monkeypatch.setattr(evidence, "_group_alive", alive)
    monkeypatch.setattr(_process_walk, "group_only_zombies", lambda _g: False)
    deadline = time.monotonic() + 0.5
    if tree:
        verdict = await ProcessTree(10, contained=True, pgid=10).confirm(
            StopReceipt("stop", 10, groups=(10,)), deadline, poll=0.01, root_started=1.0
        )
        assert type(verdict).__name__ == "Confirmed", verdict
    else:
        why = await evidence._confirm_rest(
            evidence.ProcessRecord(10, 1.0, pgid=10), deadline, clock=time.monotonic, poll=0.01, check_groups=True
        )
        assert why is None, why


@pytest.mark.parametrize("tree", [True, False], ids=["tree", "bare"])
def test_a_live_leader_whose_start_time_was_never_read_is_still_waited_on(
    procs: list[subprocess.Popen[bytes]], tree: bool
) -> None:
    """T10: "moved" needs a positive reading of another start time. A live
    walked leader with no recorded one is waited on, and the barrier ends
    unconfirmed at its deadline, naming the group. Green on ``f3e9b814``."""

    other = _sleeper_proc(own_group=True)
    procs.append(other)
    root = _sleeper_proc(own_group=True)
    procs.append(root)
    root.kill()
    root.wait(5)
    record = evidence.ProcessRecord(pid=root.pid, started=None)
    # The group alone: a walked pid of unknown start time would keep the
    # barrier waiting by itself, and hide whether the group is waited on.
    if tree:
        record.tree = ProcessTree(root.pid, contained=False)
        record.receipt = StopReceipt("stop", root.pid, groups=(other.pid,))
    else:
        record.groups.append(other.pid)

    confirmed, why = asyncio.run(evidence.confirm_gone([record], time.monotonic() + 0.3))

    assert not confirmed and why is not None and str(other.pid) in why, why


async def test_the_tree_confirm_compares_start_times_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ProcessTree.confirm`` without ``same``: a walked number that now reads as
    another process is gone, not waited on. Fails on ``f3e9b814``, which asked
    ``kill(pid, 0)`` there and waited on the reused, live number (a Codex probe)."""

    from aelix_ai.utils import _process_identity, _process_tree

    monkeypatch.setattr(_process_tree, "_group_alive", lambda _g: True)
    monkeypatch.setattr(_process_identity, "pid_is_live", lambda _pid: True)
    monkeypatch.setattr(_process_identity, "pid_is_zombie", lambda _pid: False)
    monkeypatch.setattr(_process_identity, "process_started", lambda _pid: 2.0)
    receipt = StopReceipt("stop", 10, pids=(20,), started=((20, 1.0),))

    verdict = await ProcessTree(10, contained=False).confirm(receipt, time.monotonic() + 0.2, poll=0.01)

    assert type(verdict).__name__ == "Confirmed", verdict


def test_a_bare_root_whose_number_moved_is_neither_walked_nor_signalled_while_its_returncode_lags(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    """The record with no tree: its root's number reads as another process, which
    has a child, while the job's ``Process`` still reads ``returncode is None``
    (the asyncio watcher's lag). The soft leg must not walk that number and the
    hard leg must signal neither the other process nor its child. Fails on
    ``3cab5d16`` (the bare walk trusted ``returncode``); pins ``_root_walkable``
    on the bare path and the root branch's ``return`` (mutants E11, N7)."""

    marker = tmp_path / "child.pid"
    newcomer = subprocess.Popen(
        [sys.executable, "-c", _PARENT_OF_ONE, str(marker)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(newcomer)
    end = time.monotonic() + 5
    while not marker.exists() or not marker.read_text():
        assert time.monotonic() < end
        time.sleep(0.01)
    child = int(marker.read_text())
    record = evidence.ProcessRecord(
        pid=newcomer.pid, started=_started(newcomer) - 1000.0, pgid=newcomer.pid, proc=_Unreaped(newcomer.pid)
    )
    try:
        asyncio.run(evidence.stop_legs([record], grace=0.1))
        time.sleep(0.2)
        assert _running(child), "a child of the process that took the root's number was signalled"
        assert newcomer.poll() is None, "the process that took the root's number was signalled"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, signal.SIGKILL)

