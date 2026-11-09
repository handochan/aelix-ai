"""#611 S1c — a background delegation's row is the JOB's: its tree, its kills and its prompt directory (ADR-0295).

Contract §2.3 ("Eager paths") and §3 rows 7 and 8, for the delegation client:

* the prompt directory and the process tree OUTLIVE ``PrintChannel.run`` and
  the session's ``stop_all``; the service removes the directory only after its
  barrier and the runner's cleanup duty, and the runner closes the tree only
  there (row 7's delegation half; the generic half is
  ``tests/jobs/test_cleanup_obligation_611.py``);
* every kill the channel or the reaper would make for such a row goes through
  the job's evidence legs instead — ``_eager_abort``, ``abort_child``,
  ``reap`` — so nothing signals before the evidence the barrier confirms
  against is collected (ADR-0293 §5);
* on POSIX the cleanup duty answers ``unverified`` until S2 (#719), and the
  notice says so (row 8, S1 part);
* a finished agent's process group is stopped and confirmed inside the barrier
  (``normal_exit="stop_descendants"``; contract row 2 for this kind).

Legacy rows — every foreground delegation — keep every one of those paths:
the existing ``test_print_channel_spawn`` and ``test_delegated_child_orphans_691``
suites pin them and run unchanged.

Fails on ``6caaae09``: ``RunningChild`` has no owner and ``reap`` takes none.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import signal
import sys
from pathlib import Path
from typing import Any

import aelix_agents.print_channel as pc
import aelix_agents.reaper as reaper
import pytest
from aelix_agents.jobs_client import KIND, DelegationRunner
from aelix_agents.print_channel import PrintChannel, RunningChild, abort_child
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE

from tests.agents_ext.test_agent_background_611 import (
    _rig,
    _script,
    _settled,
    _spawned_id,
)
from tests.agents_ext.test_print_channel_spawn import _stub
from tests.jobs.conftest import wait_for

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


@pytest.fixture
def jobs_on(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(activation, "ACTIVE", True)
    yield
    activation.install_service(None)


class _Owner:
    """A :class:`~aelix_agents.print_channel.CleanupOwner` that records, and kills a real child."""

    def __init__(self, proc: Any = None) -> None:
        self.proc = proc
        self.now = 0
        self.soft: list[float] = []

    def child_env(self) -> dict[str, str]:
        return {}

    def on_directory(self, path: str) -> None:
        pass

    def on_process(self, row: Any, proc: Any, *, session_path: str | None = None) -> None:
        self.proc = proc

    def stop_now(self) -> None:
        self.now += 1
        if self.proc is not None and self.proc.returncode is None:
            os.killpg(self.proc.pid, signal.SIGKILL)

    async def stop(self, grace: float) -> None:
        self.soft.append(grace)
        if self.proc is not None and self.proc.returncode is None:
            os.killpg(self.proc.pid, signal.SIGTERM)


async def _sleeper() -> Any:
    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)", start_new_session=True
    )


# === row 7, the delegation half ==========================================================


@POSIX
async def test_service_owned_directory_survives_channel_and_stop_all_until_helper_cleanup(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r5 probe 3's shape on a real background child: the directory and the tree wait for the barrier."""

    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    seen: dict[str, Any] = {}
    trees: list[Any] = []
    original = DelegationRunner.helper_cleanup

    async def watching(self: DelegationRunner, receipt: Any) -> Any:
        own = self._own
        assert own is not None
        directory = rig.service._jobs[receipt["job_id"]].directories[0]
        seen["dir_at_cleanup"] = os.path.isdir(directory)
        seen["tree_closed_at_cleanup"] = own.tree is not None and own.tree.closed
        seen["run_returned"] = self.result is not None
        seen["directory"] = directory
        trees.append(own.tree)
        return await original(self, receipt)

    monkeypatch.setattr(DelegationRunner, "helper_cleanup", watching)
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    (snap,) = rig.jobs()
    directory = rig.service._jobs[snap.job_id].directories[0]
    assert os.path.isdir(directory)
    # The session's own stop of ITS delegations never reaches the job's row.
    runtime = rig.ext.runtime
    assert runtime is not None
    await runtime.stop_all()
    assert os.path.isdir(directory)
    assert rig.jobs()[0].state == "running"
    gate.write_text("")
    snap = await _settled(rig)
    assert snap.state == "done"
    assert seen == {
        "dir_at_cleanup": True,
        "tree_closed_at_cleanup": False,
        "run_returned": True,
        "directory": directory,
    }
    assert not os.path.exists(directory), "removed once the barrier and the cleanup duty were done"
    (tree,) = trees
    assert tree is not None and tree.closed, "closed by the cleanup duty, after the barrier"
    await rig.service.shutdown()


# === row 8, S1 part ===============================================================


@POSIX
async def test_s1_reports_helpers_unverified(tmp_path: Path, jobs_on: Any) -> None:
    """Until S2 (#719) POSIX cannot confirm a helper that left the agent's group: it says so."""

    rig = await _rig(tmp_path)
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    snap = await _settled(rig)
    assert snap.state == "done"
    facts = [
        e.data for e in await rig.session.get_entries() if e.type == "custom" and e.custom_type == JOB_FACT_TYPE
    ]
    (cleanup,) = [f for f in facts if f["fact"] == "cleanup"]
    assert cleanup["helpers"] == "unverified" and "#719" in cleanup["reason"]
    (notice,) = rig.harness.undelivered_notices()
    assert "not confirmed stopped" in notice.body
    await rig.service.shutdown()


# === contract row 2, for this kind =================================================


@POSIX
async def test_a_finished_agents_group_is_stopped_before_its_job_ends(tmp_path: Path, jobs_on: Any) -> None:
    """``stop_descendants``: what the child left in its own group is killed and confirmed in the barrier."""

    pidfile = tmp_path / "helper.pid"
    script = _stub(
        "start()\n"
        "helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"announce({str(pidfile)!r}, helper.pid)\n"
        "say('left a helper')\n"
        "done()\n"
        'emit({"type": "aelix_print_done"})\n'
    )
    rig = await _rig(tmp_path, script=script)
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    snap = await _settled(rig)
    assert snap.state == "done"
    helper = int(pidfile.read_text())
    try:
        os.kill(helper, 0)
    except ProcessLookupError:
        pass
    else:  # pragma: no cover — the failure branch
        os.kill(helper, signal.SIGKILL)
        pytest.fail("the agent's helper in its own group outlived the job's barrier")
    await rig.service.shutdown()


# === the eager paths go through the owner =================================================


@POSIX
async def test_eager_abort_of_an_owned_row_goes_through_its_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_eager_abort`` (Esc Esc, a cancelled run) never kills an owned row from outside the job."""

    called: list[str] = []
    monkeypatch.setattr(pc, "kill_tree", lambda *a, **k: called.append("kill_tree"))
    monkeypatch.setattr(pc, "kill_group_if_live", lambda *a, **k: called.append("kill_group_if_live"))
    proc = await _sleeper()
    owner = _Owner(proc)
    row = RunningChild(id="sub-x", profile="p", owner=owner)
    PrintChannel._eager_abort(proc, row)
    assert owner.now == 1 and called == []
    assert row.state == "stopped" and row.eager_kill
    assert await asyncio.wait_for(proc.wait(), 10) == -signal.SIGKILL
    # A legacy row still takes the legacy legs.
    legacy = await _sleeper()
    PrintChannel._eager_abort(legacy, RunningChild(id="sub-y", profile="p"))
    assert called == ["kill_tree", "kill_group_if_live"]
    os.killpg(legacy.pid, signal.SIGKILL)
    await legacy.wait()


@POSIX
async def test_reap_of_an_owned_row_signals_nothing_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """``reap(owner=…)``: the owner's legs, this one's wait — eager or not."""

    called: list[str] = []
    monkeypatch.setattr(reaper, "_signal_child", lambda *a, **k: called.append("signal"))
    monkeypatch.setattr(reaper, "kill_tree", lambda *a, **k: called.append("kill_tree"))
    eager = await _sleeper()
    owner = _Owner(eager)
    assert await reaper.reap(eager, eager_kill=True, owner=owner) == -signal.SIGKILL
    assert owner.now == 1 and owner.soft == []
    soft = await _sleeper()
    owner = _Owner(soft)
    assert await reaper.reap(soft, grace=3.0, owner=owner) == -signal.SIGTERM
    assert owner.soft == [3.0] and owner.now == 0
    assert called == []


@POSIX
async def test_reap_of_an_owned_row_escalates_through_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second request mid-grace (``escalate``) is the owner's hard leg at once."""

    proc = await _sleeper()

    class Stubborn(_Owner):
        async def stop(self, grace: float) -> None:  # ignores the soft leg
            self.soft.append(grace)
            await asyncio.sleep(grace)

    owner = Stubborn(proc)
    escalate = asyncio.Event()
    task = asyncio.ensure_future(reaper.reap(proc, grace=30.0, owner=owner, escalate=escalate))
    await wait_for(lambda: owner.soft == [30.0])
    escalate.set()
    assert await asyncio.wait_for(task, 10) == -signal.SIGKILL
    assert owner.now == 1


@POSIX
async def test_abort_child_of_an_owned_row_goes_through_its_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(reaper, "kill_tree", lambda *a, **k: called.append("kill_tree"))
    proc = await _sleeper()
    owner = _Owner(proc)
    row = RunningChild(id="sub-z", profile="p", owner=owner, proc=proc)
    await abort_child(row, grace=0.5)
    assert row.stopped and owner.now == 1 and called == []
    assert proc.returncode == -signal.SIGKILL


# === the service's own stop reaches the child ===============================================


@POSIX
async def test_a_shutdown_stops_a_running_agent_through_the_barrier(tmp_path: Path, jobs_on: Any) -> None:
    """Quitting: the job is stopped, its process confirmed gone, its partial result recorded."""

    rig = await _rig(tmp_path, script=_script(gate=tmp_path / "never", partial="started on it"))
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    await wait_for(lambda: (rig.jobs()[0].progress or {}).get("last_text") == "started on it")
    facts = [e.data for e in await rig.session.get_entries() if e.type == "custom" and e.custom_type == JOB_FACT_TYPE]
    (process,) = [f for f in facts if f["fact"] == "process"]
    pid = process["evidence"]["pid"]
    await rig.service.shutdown()
    (snap,) = [s for s in rig.service.snapshots() if s.kind == KIND]
    assert snap.state == "stopped"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    outcome = [
        e.data
        for e in await rig.session.get_entries()
        if e.type == "custom" and e.custom_type == JOB_FACT_TYPE and e.data["fact"] == "outcome"
    ]
    assert outcome and outcome[0]["status"] == "stopped"
    assert "started on it" in outcome[0]["notice"]["body"]



# === a barrier that did not confirm keeps its tree (Codex #2) ======================


@POSIX
async def test_an_unconfirmed_barrier_leaves_the_tree_open_and_the_recheck_closes_it(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tree, its directory and its slot stay with the job until the barrier confirms.

    Fails on ``b387b4b5``: ``state=unresolved tree_closed=True``.
    """

    from aelix_ai.utils._process_tree import Unconfirmed

    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    rig.service._interval = 1000
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    job = next(iter(rig.service._jobs.values()))
    own = job.runner._own
    tree = own.tree
    assert tree is not None and not tree.closed

    async def no_confirmation(*_a: Any, **_k: Any) -> Any:
        return Unconfirmed("injected confirmation failure")

    monkeypatch.setattr(tree, "confirm", no_confirmation)
    gate.write_text("")
    await wait_for(lambda: job.state == "unresolved")
    assert not tree.closed, "an unconfirmed barrier keeps the legs it may still need"
    assert Path(job.directories[0]).exists()
    assert rig.service.admission.count(KIND) == 1
    monkeypatch.undo()
    await rig.service._recheck_unresolved(job, force=True)
    assert job.state == "done" and tree.closed
    assert not Path(job.directories[0]).exists()
    assert rig.service.admission.count(KIND) == 0
    await rig.service.shutdown()


async def test_win32_a_confirmed_cleanup_after_a_failed_one_reads_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On the injected win32 seam: an unconfirmed barrier keeps the Job handle open,
    and the cleanup the recheck asks once it confirms reads ``confirmed``.

    Fails on ``b387b4b5``: ``tree_closed=True job_handle=None`` after the first
    answer, and ``helpers=unverified`` after the second.
    """

    from types import SimpleNamespace

    import aelix_agents.jobs_client as jc
    from aelix_coding_agent.jobs import evidence as ev

    from tests.jobs.test_win32_stop_evidence_611 import FakeJob, _Clock, _tree

    monkeypatch.setattr(jc, "sys", SimpleNamespace(platform="win32"))
    api = FakeJob(helper_dies=False)
    tree = _tree(api)
    record = ev.ProcessRecord(10, 1.0, tree=tree)
    record.receipt = tree.stop_and_collect(policy="stop")
    runner = object.__new__(DelegationRunner)
    runner._own = SimpleNamespace(tree=tree, record=record, had_job=False)
    first = await runner.helper_cleanup({"confirmed": False})
    assert first.state == "unverified"
    assert not tree.closed and tree.job is not None
    assert ("close_job", 7) not in api.calls
    api.alive.clear()  # the recheck's legs: the helper is gone now
    verdict = await tree.confirm(record.receipt, 1.0, clock=_Clock(), poll=0)
    assert type(verdict).__name__ == "Confirmed"
    second = await runner.helper_cleanup({"confirmed": True})
    assert second.state == "confirmed"
    assert tree.closed and ("close_job", 7) in api.calls


@POSIX
async def test_stop_all_of_the_runners_own_runtime_leaves_the_directory_to_the_job(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex #13: ``stop_all`` on the runtime that HOLDS the owned row stops the child,
    and the prompt directory waits for the job's cleanup duty.

    Fails on ``b387b4b5``: ``directory_exists=False job_state=running`` while
    the cleanup duty was still open.
    """

    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    entered, allowed = asyncio.Event(), asyncio.Event()
    seen: dict[str, Any] = {}
    original = DelegationRunner.helper_cleanup

    async def held(self: DelegationRunner, receipt: Any) -> Any:
        seen["dir_at_cleanup"] = os.path.isdir(rig.service._jobs[receipt["job_id"]].directories[0])
        entered.set()
        await allowed.wait()
        return await original(self, receipt)

    monkeypatch.setattr(DelegationRunner, "helper_cleanup", held)
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    job = next(iter(rig.service._jobs.values()))
    directory = job.directories[0]
    runner = job.runner
    assert any(row.owner is not None for row in runner._runtime._children.values())
    await runner._runtime.stop_all()
    await asyncio.wait_for(entered.wait(), 10)
    assert os.path.isdir(directory), "the job's, until its cleanup duty is done"
    allowed.set()
    snap = await _settled(rig)
    assert snap.state == "stopped"
    assert seen == {"dir_at_cleanup": True}
    assert not os.path.exists(directory)
    await rig.service.shutdown()


# === verify's surviving mutants V01, V02, V06 =========================================


class _Ctx:
    """The job context an owner registers with, recording what it was handed."""

    job_id = "j1"

    def __init__(self) -> None:
        self.registered: list[dict[str, Any]] = []
        self.records: list[Any] = []

    def protocol_env(self) -> dict[str, str]:
        return {}

    def env_marker(self) -> dict[str, str]:
        return {}

    def register_directory(self, path: str) -> None:
        pass

    def register_process(self, proc: Any, *, pgid: int | None = None, tree: Any = None) -> Any:
        from aelix_ai.utils._process_identity import process_started
        from aelix_coding_agent.jobs import evidence as ev

        self.registered.append({"pid": proc.pid, "pgid": pgid, "tree": tree})
        record = ev.ProcessRecord(
            pid=proc.pid, started=process_started(proc.pid), pgid=pgid, proc=proc, tree=tree
        )
        self.records.append(record)
        return record

    def launched(self) -> None:
        pass


class _Row:
    tree = None
    id = "sub-x"
    session_id = None


@POSIX
async def test_a_child_whose_tree_did_not_attach_is_registered_by_its_own_group() -> None:
    """Verify V06: with no tree, the child's own process group (it leads one) is the job's evidence."""

    from aelix_agents.jobs_client import _Ownership

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "pass", start_new_session=True
    )
    ctx = _Ctx()
    own = _Ownership(ctx)
    own.on_process(_Row(), proc)  # type: ignore[arg-type]
    await proc.wait()
    assert ctx.registered == [{"pid": proc.pid, "pgid": proc.pid, "tree": None}]


@POSIX
async def test_an_owned_rows_post_exit_drain_ends_its_pipe_holder_through_the_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify V01, V02: the child exited, a helper in its group holds its stdout — the
    owner's hard leg (evidence first) ends it, then the squatters by pid with NO
    tree, so no tree-wide kill empties what the barrier still has to read."""

    from aelix_agents.jobs_client import _Ownership

    monkeypatch.setattr(pc, "POST_EXIT_DRAIN_SECONDS", 0.3)
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        "sleep 30 & echo $!; exit 0",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdout is not None
    helper = int((await proc.stdout.readline()).strip())
    stdout = proc.stdout

    async def pump() -> None:
        await stdout.read()

    pumps = asyncio.ensure_future(pump())
    # Not ``proc.wait()``: on py<=3.12 it returns only once every pipe closed,
    # which the helper holding stdout prevents. ``returncode`` is set at exit.
    await wait_for(lambda: proc.returncode is not None)
    own = _Ownership(_Ctx())
    own.on_process(_Row(), proc)  # type: ignore[arg-type]
    order: list[str] = []
    real_stop_now, real_kill_tree = own.stop_now, pc.kill_tree

    def stop_now() -> None:
        order.append("owner")
        real_stop_now()

    def kill_tree(*args: Any, **kwargs: Any) -> Any:
        order.append(f"kill_tree tree={kwargs.get('tree')!r}")
        return real_kill_tree(*args, **kwargs)

    monkeypatch.setattr(own, "stop_now", stop_now)
    monkeypatch.setattr(pc, "kill_tree", kill_tree)
    # The row's tree, as the channel passes it; an owned row's squatter kill must not use it.
    row_tree = object()
    try:
        await PrintChannel._drain_after_exit(proc, pumps, row_tree, owner=own)  # type: ignore[arg-type]
        await asyncio.sleep(0.2)
        assert order == ["owner", "kill_tree tree=None"]
        with pytest.raises(ProcessLookupError):
            os.kill(helper, 0)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(helper, signal.SIGKILL)
        pumps.cancel()


# === item 18: every signal to an owned row, on stop and on /reload ========================


@POSIX
@pytest.mark.parametrize("how", ["stop", "reload"])
async def test_no_signal_reaches_a_reused_pid_or_a_group_another_process_leads(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """Every ``kill``/``killpg`` sent for an owned row is recorded. The child ignores
    SIGTERM, so the runner's soft and hard legs and the service's escalation all
    run. From the moment the child is reaped its number is made to read as
    ANOTHER process's — a reused pid, leading its own group (the residual
    ADR-0238 names) — and no leg may signal that pid or ``killpg`` that group.

    Fails on ``b387b4b5``: the finalizer's hard leg, after the runner's,
    ``killpg``-ed the child's group number once its leader was another process.
    """

    from aelix_ai.utils import _process_identity as identity
    from aelix_coding_agent.jobs import evidence as ev

    script = _stub(
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "start()\n"
        "say('working')\n"
        "for _ in range(1200):\n"
        "    time.sleep(0.05)\n"
    )
    rig = await _rig(tmp_path, script=script)
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    job = next(iter(rig.service._jobs.values()))
    job.runner._launch = dataclasses.replace(job.runner._launch, stop_grace=0.3)
    record = job.runner._own.record
    child, proc = record.pid, record.proc
    log: list[tuple[str, int, int, bool]] = []

    def reused(pid: int) -> bool:
        return pid == child and proc.returncode is not None

    real_started, real_same = ev.process_started, ev.same_process

    def started(pid: int) -> float | None:
        return -1.0 if reused(pid) else real_started(pid)

    def same(pid: int, stamp: float | None) -> bool | None:
        return False if reused(pid) else real_same(pid, stamp)

    real_kill, real_killpg = os.kill, os.killpg

    def kill(pid: int, sig: int) -> None:
        if sig:
            log.append(("kill", pid, sig, reused(pid)))
            if reused(pid):
                return  # a simulated other process: never signal it for real
        real_kill(pid, sig)

    def killpg(pgid: int, sig: int) -> None:
        if sig:
            log.append(("killpg", pgid, sig, reused(pgid)))
            if reused(pgid):
                return
        real_killpg(pgid, sig)

    monkeypatch.setattr(ev, "process_started", started)
    monkeypatch.setattr(ev, "same_process", same)
    monkeypatch.setattr(identity, "process_started", started)
    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(os, "killpg", killpg)
    try:
        if how == "stop":
            assert not (await rig.call({"action": "stop", "id": agent_id})).is_error
        else:
            await rig.service.owner_ended(job.owner, "reload")
        await wait_for(lambda: job.state in ("stopped", "unresolved"), timeout=15)
    finally:
        monkeypatch.undo()
    assert job.state == "stopped", job.unresolved_reason
    to_child = [entry for entry in log if entry[1] == child]
    assert any(not after for *_e, after in to_child), "the legs did signal it while it lived"
    late = [entry for entry in to_child if entry[3]]
    assert late == [], f"signalled after its number became another process's: {late}"
    await rig.service.shutdown()
