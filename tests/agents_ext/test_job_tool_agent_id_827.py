"""#827 — ``job(action="status"|"result")`` takes a background agent's own id.

A background agent has two ids, its agent id and its job id, and the answer that
starts it names both ("(id <agent id>; its job is <job id>)"); ``agent()`` takes
either. The ``job`` tools found a job by its job id or a prefix only, so an agent
id read "no job … in this session" — the #123 live parent called it three times
before it used the job id. ``commands.find`` now asks each kind that is a
``JobIdAdapter`` (the agent kind: ``DelegationKindAdapter.job_for``) once neither an
exact job id nor a unique prefix of one matched (fix round 1, Codex H2), so ``job``,
``job_stop`` and ``/jobs`` take the agent id too, after a restart for a run that has
ended (Codex M4) and, since #847, for a spawned run read back unresolved or lost, at
that restart and every later one (the ids its run's own start record holds; V2-F2
had pinned "its job id only"); an id
nothing names says, in the ``job`` and ``job_stop`` tools, that ``job(action="list")``
lists the ids.

The resolution, not a pointer, is the implementer's call: the agent kind already
holds the map (``DelegationJobs.job_id_for``) and the core stays kind-agnostic
(``test_core_is_kind_agnostic_611`` scans the new code). Fails on ``1c2a4a7c``:
``job(status, id=<agent id>)`` is an error, "no job <agent id> in this session".
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs.commands import JobsCommand, find
from aelix_coding_agent.jobs.contract import JobIdAdapter
from aelix_coding_agent.jobs.tool import create_job_stop_tool, create_job_tool

from tests.agents_ext._detach_rig import (  # noqa: F401 — reap_children is the fixture
    finish,
    reap_children,
    settled,
)
from tests.agents_ext.test_agent_background_611 import jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_detach_611 import _agent_id, _moved
from tests.agents_ext.test_tool_and_security import _text
from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session

pytestmark = [
    pytest.mark.usefixtures("jobs_on", "reap_children"),
    pytest.mark.skipif(sys.platform == "win32", reason="a stub child's pipes, as test_detach_611"),
]


async def _job(tool: Any, **args: Any) -> tuple[bool, str]:
    result = await tool.execute(args)
    return result.is_error, _text(result)


async def test_the_job_tools_and_jobs_take_a_moved_agents_id(tmp_path: Path) -> None:
    rig, gate, text, _row = await _moved(tmp_path)
    agent_id = _agent_id(text)
    (snap,) = rig.jobs()
    assert agent_id != snap.job_id and f"its job is {snap.job_id}" in text, "the premise: two ids"
    job = create_job_tool(rig.service)
    by_agent = await _job(job, action="status", id=agent_id)
    assert by_agent == await _job(job, action="status", id=snap.job_id)
    assert by_agent[0] is False and f"id        {snap.job_id}" in by_agent[1], by_agent
    stop = await _job(create_job_stop_tool(rig.service), id=agent_id)
    assert stop == (True, 'use agent(action="status"|"wait"|"stop"|"result", id=…)'), "found, then the kind's hint"
    assert (await JobsCommand(rig.service)(agent_id)).splitlines()[1].strip() == f"id        {snap.job_id}"
    gate.write_text("go")
    await settled(rig)
    result = await _job(job, action="result", id=agent_id)
    assert result == await _job(job, action="result", id=snap.job_id)
    # What the kind pages (the answer, or where to read it) is the kind's; the two ids read the same.
    assert result[0] is False and result[1], result
    await finish(rig)


async def test_an_id_nothing_names_points_at_the_list(tmp_path: Path) -> None:
    rig, gate, _text_, _row = await _moved(tmp_path)
    missing = "d2745a72-c224-4e87-820a-d2ece633bae1"
    for tool, args in (
        (create_job_tool(rig.service), {"action": "status", "id": missing}),
        (create_job_tool(rig.service), {"action": "result", "id": missing}),
        (create_job_stop_tool(rig.service), {"id": missing}),
    ):
        assert await _job(tool, **args) == (
            True,
            f'no job {missing} in this session; job(action="list") lists this session\'s jobs and their ids',
        )
    gate.write_text("go")
    await settled(rig)
    await finish(rig)


class _Named(_sleeper.SleeperAdapter):
    """A kind that names its jobs by ids of its own, through ``JobIdAdapter``."""

    def __init__(self, names: dict[str, str], *, name: str = _sleeper.KIND, raises: bool = False) -> None:
        super().__init__()
        self.name = name
        self.names = names
        self.raises = raises
        self.asked: list[list[str]] = []

    def job_for(self, ident: str, jobs: Any = None) -> str | None:
        # ``jobs`` optional so this fake answers ``1099de2b``'s one-argument call too.
        self.asked.append([snap.job_id for snap in jobs or ()])
        if self.raises:
            raise RuntimeError("boom")
        return self.names.get(ident)


async def _start(service: Any, kind: str, job_id: str) -> str:
    """A sleeper job of ``kind`` whose job id is ``job_id`` (the service draws ids from ``uuid4``)."""

    from types import SimpleNamespace
    from unittest.mock import patch

    from aelix_coding_agent.jobs import service as service_module

    with patch.object(service_module, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=job_id))):
        return await service.start(kind, title=kind, params={"seconds": 30})


async def test_the_service_asks_the_kind_and_keeps_to_the_current_session(tmp_path: Path) -> None:
    service = fast_service()
    names: dict[str, str] = {}
    adapter = _Named(names)
    assert isinstance(adapter, JobIdAdapter)
    service.register_kind(adapter, _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(_sleeper.KIND, title="named", params={"seconds": 5})
    names["mine"] = job_id
    names["elsewhere"] = "0123456789abcdef"  # no job of this session
    assert service.jobs_named("mine") == [job_id]
    assert adapter.asked[-1] == [job_id], "the kind is handed its own jobs of this session"
    assert service.jobs_named("elsewhere") == []
    assert service.jobs_named("unknown") == []
    adapter.raises = True
    assert service.jobs_named("mine") == [], "a kind that raises names nothing"
    await service.stop(job_id)
    await service.shutdown()
    await _sleeper.reap_all()


# === fix round 1 (Codex review of #827: H2, M4) ======================================


async def test_a_job_id_prefix_names_its_job_before_any_kinds_alias(tmp_path: Path) -> None:
    """H2: a kind's alias that is also a prefix of another job's id redirected
    ``job_stop`` to the aliased job. Fails on ``1099de2b``: ``def456…`` is stopped."""

    service = fast_service()
    names: dict[str, str] = {}
    service.register_kind(_Named(names, name="named"), _sleeper.Sleeper)
    service.register_kind(_sleeper.SleeperAdapter(), _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    try:
        first = await _start(service, _sleeper.KIND, "abc1230000000000")
        second = await _start(service, "named", "def4560000000000")
        names["abc123"] = second
        assert find(service, "abc123").job_id == first  # type: ignore[union-attr]
        stopped = await _job(create_job_stop_tool(service), id="abc123…")
        assert stopped[0] is False and stopped[1].startswith(f"{first}: stopped"), stopped
        assert service.snapshot(second).state == "running"  # type: ignore[union-attr]
    finally:
        await service.shutdown()
        await _sleeper.reap_all()


async def test_a_kinds_answer_counts_only_for_its_own_jobs(tmp_path: Path) -> None:
    """H2: an adapter named a job of another kind. Fails on ``1099de2b``: the
    sleeper job is found by the ``named`` kind's alias."""

    service = fast_service()
    names: dict[str, str] = {}
    service.register_kind(_Named(names, name="named"), _sleeper.Sleeper)
    service.register_kind(_sleeper.SleeperAdapter(), _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    try:
        other = await _start(service, _sleeper.KIND, "abc1230000000000")
        await _start(service, "named", "def4560000000000")
        names["wrong-kind"] = other
        assert find(service, "wrong-kind") == "no job wrong-kind in this session"
    finally:
        await service.shutdown()
        await _sleeper.reap_all()


async def test_an_id_two_kinds_answer_differently_is_refused(tmp_path: Path) -> None:
    """H2: an ambiguous alias names no job. Fails on ``1099de2b``: the first kind's answer wins."""

    service = fast_service()
    one: dict[str, str] = {}
    two: dict[str, str] = {}
    service.register_kind(_Named(one, name="named-a"), _sleeper.Sleeper)
    service.register_kind(_Named(two, name="named-b"), _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    try:
        one["shared"] = await _start(service, "named-a", "aaaa000000000000")
        two["shared"] = await _start(service, "named-b", "bbbb000000000000")
        assert find(service, "shared") == "shared names 2 jobs of different kinds; give a job id"
        two["shared"] = one["shared"]  # not its own: dropped, so one answer is left
        assert find(service, "shared").job_id == one["shared"]  # type: ignore[union-attr]
    finally:
        await service.shutdown()
        await _sleeper.reap_all()


async def test_an_ended_agents_id_still_names_its_job_after_a_restart(tmp_path: Path) -> None:
    """M4: the agent kind read only this process's map, so after a reopen ``job``
    answered "no job <agent id>" though ``agent()`` resolved it. Fails on
    ``1099de2b``: the restarted service's ``job(status, id=<agent id>)`` is an error."""

    from tests.agents_ext.test_child_recovery_611 import _restart
    from tests.agents_ext.test_detach_611 import _resumable

    rig, gate, text, _row = await _moved(tmp_path, channel=_resumable(tmp_path / "gate"))
    try:
        agent_id = _agent_id(text)
        gate.write_text("go")
        snap = await settled(rig)
        new, new_jobs, _harness = await _restart(rig.session)
        try:
            assert await new_jobs.resolve(new, agent_id, session=rig.session) == snap.job_id, "agent() takes it"
            by_agent = await _job(create_job_tool(new), action="status", id=agent_id)
            assert by_agent == await _job(create_job_tool(new), action="status", id=snap.job_id)
            assert by_agent[0] is False and f"id        {snap.job_id}" in by_agent[1], by_agent
        finally:
            await new.shutdown()
    finally:
        await finish(rig)


async def test_a_spawned_agents_id_names_its_job_after_a_restart(tmp_path: Path) -> None:
    """M4 for a ``spawn``: its job has no moved run's linkage; the ids a restart reads
    back from the run's own records into the job's payload name it. Fails on
    ``1099de2b``: "no job <agent id>"."""

    from tests.agents_ext._detach_rig import call, script, tui_rig
    from tests.agents_ext.test_child_recovery_611 import _restart
    from tests.jobs.conftest import wait_for

    rig = await tui_rig(tmp_path, script=script("the spawned answer"))
    try:
        text = _text(await call(rig, {"action": "spawn", "profile": "scout", "task": "count files"}))
        agent_id = _agent_id(text)
        (snap,) = rig.jobs()
        await wait_for(lambda: rig.service.snapshot(snap.job_id).state == "done", timeout=30)  # type: ignore[union-attr]
        new, _jobs, _harness = await _restart(rig.session)
        try:
            restored = new.snapshot(snap.job_id)
            assert restored is not None and restored.payload.get("agent_id") == agent_id, restored
            by_agent = await _job(create_job_tool(new), action="status", id=agent_id)
            assert by_agent == await _job(create_job_tool(new), action="status", id=snap.job_id)
            assert by_agent[0] is False and f"id        {snap.job_id}" in by_agent[1], by_agent
        finally:
            await new.shutdown()
    finally:
        await finish(rig)


def _agent_snap(job_id: str, *, payload: dict[str, Any] | None = None, client_start: dict[str, Any] | None = None) -> Any:
    from aelix_coding_agent.jobs.contract import JobSnapshot

    return JobSnapshot(
        job_id=job_id,
        kind="agent",
        label="agent",
        title="scout: t",
        state="unresolved",
        owner="s",
        created=0.0,
        elapsed=None,
        last_activity_ago=None,
        quiet=False,
        result_waiting=False,
        notice_state="none",
        brief="",
        payload=payload or {},
        client_start=client_start or {},
    )


def test_the_agent_kind_answers_only_among_the_jobs_it_is_handed() -> None:
    """``job_for`` answers among ``jobs``: this process's map naming a job that is not
    one of them (another session's) does not win over the job whose own data names it."""

    from aelix_agents.jobs_client import AgentJob, DelegationJobs, DelegationKindAdapter

    jobs = DelegationJobs()
    jobs.adopt_entry(AgentJob(job_id="aaaa000000000000", profile="scout", agent_id="agent-1"))
    adapter = DelegationKindAdapter(jobs, session=lambda: None)
    mine = _agent_snap("bbbb000000000000", payload={"agent_id": "agent-1"})
    assert adapter.job_for("agent-1", [mine]) == "bbbb000000000000"
    assert adapter.job_for("agent-1", [_agent_snap("aaaa000000000000"), mine]) == "aaaa000000000000"


def test_a_moved_run_with_no_payload_yet_is_named_by_its_linkage() -> None:
    """A restart's moved run that is not ended has no read-back payload; its job's
    ``start`` fact links its key and child session (#775), which name it."""

    from aelix_agents.jobs_client import ADOPTED_LINK_VERSION, DelegationJobs, DelegationKindAdapter

    linkage = {"v": ADOPTED_LINK_VERSION, "adopted": True, "key": "sub-k", "child": {"session_id": "sess-1"}}
    snap = _agent_snap("cccc000000000000", client_start=linkage)
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: None)
    assert adapter.job_for("sess-1", [snap]) == "cccc000000000000"
    assert adapter.job_for("sub-k", [snap]) == "cccc000000000000"
    assert adapter.job_for("other", [snap]) is None


# === fix round 2 (lane b7f verify round 2: V2-F2, V2-F3) =============================


async def test_a_prefix_two_job_ids_share_names_neither_and_no_kinds_alias_steps_in(tmp_path: Path) -> None:
    """V2-F3 B1: a prefix two job ids share is answered "names 2 jobs; give more of
    the id", not handed to the kinds, even when a kind's alias is that prefix.
    Passes on ``a3c8b9a6``; it kills the mutant that skips the answer
    (``if matches:`` made false), under which the alias named the second job."""

    service = fast_service()
    names: dict[str, str] = {}
    service.register_kind(_Named(names, name="named"), _sleeper.Sleeper)
    service.register_kind(_sleeper.SleeperAdapter(), _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    try:
        await _start(service, _sleeper.KIND, "abc1230000000000")
        second = await _start(service, "named", "abc4560000000000")
        names["abc"] = second
        assert find(service, "abc") == "abc names 2 jobs; give more of the id"
        assert await JobsCommand(service)("abc") == "abc names 2 jobs; give more of the id"
        assert find(service, "abc4").job_id == second  # type: ignore[union-attr]
    finally:
        await service.shutdown()
        await _sleeper.reap_all()


async def _read_back(
    tmp_path: Path,
    *,
    alive: bool,
    answers: tuple[str, ...] = ("CHILD SAID THIS",),
    held: list[Any] | None = None,
) -> Any:
    """A spawned run's records with no settle record, and a restart that reads them back:
    ``recovered`` when its recorded process is gone, ``unresolved`` when it is alive.

    ``answers`` are what its child's file holds (none: a gone run reads back ``lost``);
    with ``held``, the child's file's writer lock is taken before the restart, as a
    live child holds it, and appended there for the test to release (#847)."""

    import os

    from aelix_agent_core.session.session_lock import SessionWriterLock
    from aelix_ai.utils._process_identity import process_started

    from tests.agents_ext.test_child_recovery_611 import (
        _gone_process_fact,
        _process_fact,
        _restart,
        _spawned,
        _start_fact,
    )

    session, receipt = await _spawned(tmp_path, *answers)
    await _start_fact(session)
    if alive:
        await _process_fact(session, os.getpid(), process_started(os.getpid()))
    else:
        await _gone_process_fact(session)
    if held is not None:
        assert receipt.child is not None
        lock = SessionWriterLock(receipt.child.path, timeout=0)
        assert lock.try_acquire()
        held.append(lock)
    service, jobs, _harness = await _restart(session)
    return session, receipt, service, jobs


async def test_a_recovered_runs_agent_id_and_run_key_name_its_job_after_a_restart(tmp_path: Path) -> None:
    """V2-F3 B14: a run the restart reads back as ``recovered`` (no settle record, its
    process gone) is named by its child session id and by its run key, from the ids
    read back into its payload. Passes on ``a3c8b9a6``; it kills the mutant that
    drops ``payload["key"]`` from ``_names_of`` (the run key read "no job …")."""

    from tests.agents_ext.test_child_recovery_611 import _JOB

    _session, receipt, service, _jobs = await _read_back(tmp_path, alive=False)
    try:
        snap = service.snapshot(_JOB)
        assert snap is not None and snap.state == "recovered", snap
        assert receipt.child is not None
        job = create_job_tool(service)
        by_job_id = await _job(job, action="status", id=_JOB)
        assert by_job_id[0] is False, by_job_id
        for ident in (receipt.child.session_id, receipt.key):
            assert await _job(job, action="status", id=ident) == by_job_id, ident
    finally:
        await service.shutdown()


# === #847: a spawned run read back unfinished is named by its agent id and run key ======


@pytest.mark.parametrize(
    ("alive", "answers", "lock", "state"),
    [
        pytest.param(True, ("CHILD SAID THIS",), False, "unresolved", id="alive-answer-read"),
        pytest.param(True, ("CHILD SAID THIS",), True, "unresolved", id="alive-file-locked"),
        pytest.param(False, (), False, "lost", id="gone-no-answer"),
    ],
)
async def test_a_spawned_run_read_back_unfinished_is_named_by_its_agent_id_and_run_key(
    tmp_path: Path, alive: bool, answers: tuple[str, ...], lock: bool, state: str
) -> None:
    """#847 (was V2-F2's pin): a spawned run the restart reads back with no settle
    record — ``unresolved`` while its recorded process is alive, whether or not its
    child's file is locked, and ``lost`` when it is gone and left no answer — keeps an
    empty payload, and the ``job`` tools, ``job_stop`` and ``/jobs`` find it by its
    agent id and by its run key, as ``agent()`` does. The agent kind names it by the
    ids its run's own start record (with the job id) holds, which ``recover`` reads.
    Fails on ``0436193c``: "no job <agent id> in this session" for all three."""

    from tests.agents_ext.test_child_recovery_611 import _JOB

    held: list[Any] = []
    session, receipt, service, jobs = await _read_back(
        tmp_path, alive=alive, answers=answers, held=held if lock else None
    )
    try:
        snap = service.snapshot(_JOB)
        assert snap is not None and (snap.state, dict(snap.payload)) == (state, {}), snap
        assert receipt.child is not None
        job = create_job_tool(service)
        by_job_id = await _job(job, action="status", id=_JOB)
        assert by_job_id[0] is False and f"id        {_JOB}" in by_job_id[1], by_job_id
        detail = await JobsCommand(service)(_JOB)
        for ident in (receipt.child.session_id, receipt.key):
            assert await _job(job, action="status", id=ident) == by_job_id, ident
            assert await JobsCommand(service)(ident) == detail, ident
            stop = await _job(create_job_stop_tool(service), id=ident)
            assert stop == await _job(create_job_stop_tool(service), id=_JOB), ident
            assert not stop[1].startswith("no job"), stop
            assert await jobs.resolve(service, ident, session=session) == _JOB, "agent() takes it"
    finally:
        for taken in held:
            taken.release()
        await service.shutdown()


async def test_a_job_whose_records_name_no_agent_reads_back_by_its_job_id_only(tmp_path: Path) -> None:
    """#847: a job whose records hold no ids of its run — its run's start record not
    written (a crash between the job's start and its run's), or one with no key and no
    child — reads back as before: ``unresolved`` from the OS evidence, never the
    kind's ``recover_error``, and named by its job id only. It kills the mutant that
    reads the names off the record itself (``dict(start)``, which raises when there is
    none) instead of through ``_payload_of``."""

    import os

    from aelix_agents.child_session import CHILD_SESSION_TYPE, append_records
    from aelix_ai.utils._process_identity import process_started

    from tests.agents_ext.test_child_recovery_611 import _JOB, _process_fact, _restart, _start_fact
    from tests.agents_ext.test_child_session_records import _parent

    for shape, records in (
        ("no run record", []),
        ("a start with no ids", [(CHILD_SESSION_TYPE, {"v": 1, "phase": "start", "job_id": _JOB})]),
    ):
        session, _repo, _path = await _parent(tmp_path / shape.replace(" ", "-"))
        await _start_fact(session)
        if records:
            await append_records(session, records)
        await _process_fact(session, os.getpid(), process_started(os.getpid()))
        service, _jobs, _harness = await _restart(session)
        try:
            snap = service.snapshot(_JOB)
            assert snap is not None and snap.state == "unresolved", (shape, snap)
            # OS evidence's reason (Linux adds that the argv no longer shows the job), not ``recover_error``.
            assert str(snap.unresolved_reason).startswith(f"pid {os.getpid()} is still running"), (shape, snap)
            job = create_job_tool(service)
            assert (await _job(job, action="status", id=_JOB))[0] is False, shape
            error, said = await _job(job, action="status", id="sub-aaaaaaaaaaaa")
            assert error is True and said.startswith("no job sub-aaaaaaaaaaaa in this session;"), (shape, said)
        finally:
            await service.shutdown()


async def test_a_settled_run_whose_restored_outcome_is_not_written_is_named_by_its_agent_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#847: the ids are kept whatever the verdict. A run with its parent's settle is
    read back as ended, but when that outcome's write fails the job stays
    ``unresolved(record_failed)`` with an empty payload; its agent id and run key still
    name it. It kills the mutant that keeps the ids only after a settle was ruled out."""

    import os

    from aelix_ai.utils._process_identity import process_started
    from aelix_coding_agent.jobs import service as service_module
    from aelix_coding_agent.jobs.records import RecordWriteFailed

    from tests.agents_ext.test_child_recovery_611 import (
        _JOB,
        _process_fact,
        _restart,
        _settle,
        _spawned,
        _start_fact,
    )

    session, receipt = await _spawned(tmp_path, "CHILD SAID THIS")
    await _start_fact(session)
    await _process_fact(session, os.getpid(), process_started(os.getpid()))
    await _settle(session, receipt, "ok")
    real = service_module.append_fact

    async def refuse_outcomes(target: Any, fact: dict[str, Any]) -> Any:
        if fact.get("fact") == "outcome":
            raise RecordWriteFailed("the outcome could not be placed")
        return await real(target, fact)

    monkeypatch.setattr(service_module, "append_fact", refuse_outcomes)
    assert receipt.child is not None
    service, _jobs, _harness = await _restart(session)
    try:
        snap = service.snapshot(_JOB)
        assert snap is not None and (snap.state, snap.unresolved_reason, dict(snap.payload)) == (
            "unresolved",
            "record_failed",
            {},
        ), snap
        job = create_job_tool(service)
        by_job_id = await _job(job, action="status", id=_JOB)
        for ident in (receipt.child.session_id, receipt.key):
            assert await _job(job, action="status", id=ident) == by_job_id, ident
    finally:
        await service.shutdown()


async def test_a_linkage_of_an_unknown_version_names_its_job_by_its_job_id_only(tmp_path: Path) -> None:
    """#847: a job whose ``start`` fact links a moved run by a linkage version this aelix
    cannot read is ``unresolved(adapter_decode_error)`` (#775), and the records it
    links are not trusted to be its run's: neither the linkage's ids nor those of a
    start record that carries its job id name it. Nor at a later restart that hands
    ``decode`` an outcome of that job (one an aelix that reads the linkage would
    record): ``decode`` refuses the linkage before it keeps any id (#847 fix round 1).
    It kills the mutants that drop the ``_linkage_unknown`` guard from ``recover`` and
    that keep ``decode``'s ids before its linkage check."""

    import os

    from aelix_agents.jobs_client import KIND
    from aelix_ai.utils._process_identity import process_started
    from aelix_coding_agent.jobs.records import append_fact, make_fact

    from tests.agents_ext.test_child_recovery_611 import _JOB, _process_fact, _restart, _spawned

    session, receipt = await _spawned(tmp_path, "CHILD SAID THIS")
    meta = await session.get_metadata()
    linkage = {"v": 99, "adopted": True, "key": "sub-linked", "child": {"session_id": "linked-session"}}
    fields = {"kind": KIND, "kind_version": 1, "owner": meta.id, "mode": "background", "notify": True}
    fields.update(title="scout: count files", created=0.0, settings_key="agents", client=linkage)
    await append_fact(session, make_fact("start", _JOB, **fields))
    await _process_fact(session, os.getpid(), process_started(os.getpid()))
    assert receipt.child is not None
    service, _jobs, _harness = await _restart(session)
    try:
        snap = service.snapshot(_JOB)
        assert snap is not None and (snap.state, snap.unresolved_reason) == ("unresolved", "adapter_decode_error"), snap
        job = create_job_tool(service)
        assert (await _job(job, action="status", id=_JOB))[0] is False
        for ident in (receipt.child.session_id, receipt.key, "sub-linked", "linked-session"):
            error, said = await _job(job, action="status", id=ident)
            assert error is True and said.startswith(f"no job {ident} in this session;"), (ident, said)
    finally:
        await service.shutdown()
    outcome = make_fact("outcome", _JOB, status="lost", brief="gone", elapsed_ms=0, client_records="ok")
    await append_fact(session, outcome)
    service, _jobs, _harness = await _restart(session)
    try:
        snap = service.snapshot(_JOB)
        assert snap is not None and (snap.state, dict(snap.payload)) == ("lost", {}), snap
        job = create_job_tool(service)
        assert (await _job(job, action="status", id=_JOB))[0] is False
        for ident in (receipt.child.session_id, receipt.key, "sub-linked", "linked-session"):
            error, said = await _job(job, action="status", id=ident)
            assert error is True and said.startswith(f"no job {ident} in this session;"), (ident, said)
    finally:
        await service.shutdown()


async def test_an_id_a_restored_agent_and_another_kind_both_answer_is_refused(tmp_path: Path) -> None:
    """#847: after a restart, an agent id the agent kind now answers with its restored
    job, and another kind answers with a job of its own, names no job: it is refused as
    two kinds' answers are (``JobService.jobs_named``); each job id still names its
    job. Fails on ``0436193c``: the agent kind answered nothing, so the other kind's
    job was found."""

    import os

    from aelix_agents.jobs_client import DelegationJobs, DelegationKindAdapter
    from aelix_ai.utils._process_identity import process_started

    from tests.agents_ext.test_child_recovery_611 import _JOB, _process_fact, _spawned, _start_fact

    session, receipt = await _spawned(tmp_path, "CHILD SAID THIS")
    await _start_fact(session)
    await _process_fact(session, os.getpid(), process_started(os.getpid()))
    assert receipt.child is not None
    agent_id = receipt.child.session_id
    service = fast_service()
    service.register_kind(
        DelegationKindAdapter(DelegationJobs(), session=lambda: session), lambda _p: None  # type: ignore[arg-type,return-value]
    )
    names: dict[str, str] = {}
    service.register_kind(_Named(names, name="named"), _sleeper.Sleeper)
    await service.bind(make_harness(session))
    try:
        assert service.snapshot(_JOB).state == "unresolved"  # type: ignore[union-attr]
        other = await _start(service, "named", "eeee000000000000")
        names[agent_id] = other
        refused = f"{agent_id} names 2 jobs of different kinds; give a job id"
        assert find(service, agent_id) == refused
        assert await _job(create_job_tool(service), action="status", id=agent_id) == (True, refused)
        assert await JobsCommand(service)(agent_id) == refused
        assert find(service, _JOB).job_id == _JOB  # type: ignore[union-attr]
        assert find(service, other).job_id == other  # type: ignore[union-attr]
        del names[agent_id]
        assert find(service, agent_id).job_id == _JOB, "the agent kind's answer alone"  # type: ignore[union-attr]
    finally:
        await service.shutdown()
        await _sleeper.reap_all()


async def test_a_live_spawned_childs_agent_id_names_its_job_in_the_next_service(tmp_path: Path) -> None:
    """#847, a real process: a background agent spawned through the real extension, job
    service and print channel is still running when its service is dropped without a
    shutdown (as a crash leaves it); a second service bound to the same session reads
    the job back ``unresolved`` and takes the agent id in ``job``, ``job_stop`` and
    ``/jobs``. Fails on ``0436193c``: "no job <agent id> in this session"."""

    import asyncio

    from aelix_coding_agent.jobs.records import JOB_FACT_TYPE

    from tests.agents_ext._detach_rig import call, script, tui_rig
    from tests.agents_ext.test_child_recovery_611 import _restart

    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("the spawned answer", gate=gate))
    try:
        text = _text(await call(rig, {"action": "spawn", "profile": "scout", "task": "count files"}))
        agent_id = _agent_id(text)
        (snap,) = rig.jobs()
        for _ in range(200):
            # The child's process fact: what the next service reads it alive by.
            entries = [e for e in await rig.session.get_entries() if e.type == "custom"]
            if any(e.custom_type == JOB_FACT_TYPE and e.data.get("fact") == "process" for e in entries):
                break
            await asyncio.sleep(0.025)
        assert rig.service.snapshot(snap.job_id).state == "running", "the premise: the child runs"  # type: ignore[union-attr]
        new, _jobs, _harness = await _restart(rig.session)
        try:
            restored = new.snapshot(snap.job_id)
            assert restored is not None and restored.state == "unresolved", restored
            job = create_job_tool(new)
            by_agent = await _job(job, action="status", id=agent_id)
            assert by_agent == await _job(job, action="status", id=snap.job_id)
            assert by_agent[0] is False and f"id        {snap.job_id}" in by_agent[1], by_agent
            stop = await _job(create_job_stop_tool(new), id=agent_id)
            assert stop == await _job(create_job_stop_tool(new), id=snap.job_id), stop
            assert (await JobsCommand(new)(agent_id)).splitlines()[1].strip() == f"id        {snap.job_id}"
        finally:
            await new.shutdown()
    finally:
        gate.write_text("go")
        await finish(rig)


async def test_after_a_restart_an_agent_with_two_runs_names_its_newer_run(tmp_path: Path) -> None:
    """V2-F3 B4: a resumed agent has two runs, two jobs and one agent id. In this
    process the id names the newer run (its last spawn or move), and after a restart
    the kind is handed its jobs newest first and names the newer run too. Passes on
    ``a3c8b9a6``; it kills the mutant that sorts oldest first (``reverse=False`` in
    ``JobService.jobs_named``), under which the restart named the older run."""

    from tests.agents_ext._detach_rig import parked_call, row_of, tui_rig, within
    from tests.agents_ext.test_child_recovery_611 import _restart
    from tests.agents_ext.test_detach_611 import _resumable
    from tests.jobs.conftest import wait_for

    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, channel=_resumable(gate))
    try:
        turn = await parked_call(rig)
        row = row_of(rig)
        await wait_for(lambda: row.stream.last_text == "halfway")
        assert rig.jobs_ui.request_detach()
        await within(turn)
        gate.write_text("go")
        first = await settled(rig)
        gate.unlink()
        turn = await parked_call(rig, {"action": "resume", "profile": "scout", "id": row.session_id, "task": "again"})
        row2 = row_of(rig)
        await wait_for(lambda: row2.stream.last_text == "halfway")
        assert rig.jobs_ui.request_detach()
        await within(turn)
        gate.write_text("go")
        await wait_for(lambda: len(rig.jobs()) == 2 and all(s.state == "done" for s in rig.jobs()), timeout=30)
        (second,) = [s for s in rig.jobs() if s.job_id != first.job_id]
        assert row2.session_id == row.session_id and second.created > first.created, "the premise"
        by_agent = await _job(create_job_tool(rig.service), action="status", id=row.session_id)
        assert f"id        {second.job_id}" in by_agent[1], by_agent
        new, _jobs, _harness = await _restart(rig.session)
        try:
            by_agent = await _job(create_job_tool(new), action="status", id=row.session_id)
            assert by_agent[0] is False and f"id        {second.job_id}" in by_agent[1], by_agent
        finally:
            await new.shutdown()
    finally:
        gate.write_text("go")
        await finish(rig)


# === #847 fix round 1: every later restart, malformed start records, two runs ==========


async def _named_alike(service: Any, jobs: Any, session: Any, job_id: str, idents: tuple[str, ...]) -> None:
    """Each of ``idents`` reads as ``job_id`` does in ``job`` status and result, ``job_stop``
    and ``/jobs``, and ``agent()`` takes it."""

    job, stop = create_job_tool(service), create_job_stop_tool(service)
    status = await _job(job, action="status", id=job_id)
    assert status[0] is False and f"id        {job_id}" in status[1], status
    result = await _job(job, action="result", id=job_id)
    stopped = await _job(stop, id=job_id)
    detail = await JobsCommand(service)(job_id)
    for ident in idents:
        assert await _job(job, action="status", id=ident) == status, ident
        assert await _job(job, action="result", id=ident) == result, ident
        assert await _job(stop, id=ident) == stopped, ident
        assert await JobsCommand(service)(ident) == detail, ident
        assert await jobs.resolve(service, ident, session=session) == job_id, ("agent() takes it", ident)


async def test_a_lost_runs_agent_id_names_its_job_at_every_later_restart(tmp_path: Path) -> None:
    """#847 fix round 1, F1: a spawned run the restart reads back ``lost`` (its process
    gone, no answer in its child's file) is recorded with an ``outcome: lost`` fact and
    an empty payload. The restarts after that ``decode`` that fact instead of asking
    ``recover``, and the agent kind still names the job by the agent id and run key
    its run's start record holds; nothing records the outcome again. Fails on
    ``0436193c`` at the first restart, and at the second when ``decode`` keeps no ids
    (the mutant that drops its ``keep_read_back``): ``job(status, id=<agent id>)``
    reads "no job <agent id> in this session"."""

    from tests.agents_ext.test_child_recovery_611 import _JOB, _outcomes, _restart

    session, receipt, service, jobs = await _read_back(tmp_path, alive=False, answers=())
    assert receipt.child is not None
    idents = (receipt.child.session_id, receipt.key)
    try:
        assert service.snapshot(_JOB).state == "lost"  # type: ignore[union-attr]
        await _named_alike(service, jobs, session, _JOB, idents)
    finally:
        await service.shutdown()
    assert _outcomes(list(await session.get_entries())) == ["lost"], "the first restart recorded it"
    for restart in ("second", "third"):
        service, jobs, _harness = await _restart(session)
        try:
            snap = service.snapshot(_JOB)
            assert snap is not None and (snap.state, dict(snap.payload)) == ("lost", {}), (restart, snap)
            await _named_alike(service, jobs, session, _JOB, idents)
        finally:
            await service.shutdown()
    assert _outcomes(list(await session.get_entries())) == ["lost"], "no later restart records it again"


_CRASHED_PARENT = """
import asyncio, json, os, sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from aelix_coding_agent.jobs import activation

activation.ACTIVE = True
from aelix_agents.child_session import CHILD_SESSION_TYPE
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE
import tests.agents_ext._detach_rig as rig_module

rig_module.GATE_SECONDS = 120
from tests.agents_ext._detach_rig import call, script, tui_rig
from tests.agents_ext.test_tool_and_security import _text


async def main(tmp):
    rig = await tui_rig(tmp, script=script("the spawned answer", gate=tmp / "gate"))
    text = _text(await call(rig, {"action": "spawn", "profile": "scout", "task": "count files"}))
    (snap,) = rig.jobs()
    loop = asyncio.get_running_loop()
    end, pids, keys = loop.time() + 60, [], []
    while loop.time() < end:
        entries = [e for e in await rig.session.get_entries() if e.type == "custom"]
        pids = [
            [e.data["evidence"]["pid"], e.data["evidence"]["started"]]
            for e in entries
            if e.custom_type == JOB_FACT_TYPE and e.data.get("fact") == "process"
        ]
        keys = [
            e.data.get("key")
            for e in entries
            if e.custom_type == CHILD_SESSION_TYPE and e.data.get("phase") == "start"
        ]
        progress = rig.service.snapshot(snap.job_id).progress or {}
        if pids and progress.get("tool") == "read":
            break  # the child is past its opening lines, at its gate
        await asyncio.sleep(0.025)
    else:
        raise SystemExit(f"the child never reached its gate: {pids} {progress}")
    print(json.dumps({"text": text, "job_id": snap.job_id, "keys": keys, "pids": pids}), flush=True)
    os._exit(0)  # a crash: no shutdown, no settle record, no outcome


asyncio.run(main(Path(sys.argv[1])))
"""


async def test_a_crashed_parents_agent_is_named_by_its_agent_id_after_it_is_read_back_lost(tmp_path: Path) -> None:
    """#847 fix round 1, F1, the verify round's reproducer: a parent process spawns a
    background agent through the real extension, job service and print channel and
    ends with ``os._exit`` while its child runs, as a crash leaves it. The next
    service reads the job back ``unresolved`` while the child lives (macOS: the stub
    reads no inbox) and ``lost`` once it is killed, or ``lost`` at once where the
    child died with its parent (Linux ``pdeathsig``); the service after that opens the
    session from disk and decodes the ``lost`` outcome. In both, the ``job`` tools,
    ``job_stop``, ``/jobs`` and ``agent()`` take the agent id and the run key. Fails
    on ``0436193c`` at the first service, and at the last when ``decode`` keeps no ids:
    "no job <agent id> in this session"."""

    import asyncio
    import contextlib
    import json
    import os
    import signal

    from aelix_agent_core.session import LocalFileSystem, Session
    from aelix_agent_core.session.jsonl_storage import JsonlSessionStorage
    from aelix_ai.utils._process_identity import process_started

    from tests.agents_ext.test_child_recovery_611 import _outcomes, _restart
    from tests.env_sandbox import child_env
    from tests.jobs.conftest import wait_for

    root = Path(__file__).resolve().parents[2]
    home = tmp_path / "home"
    home.mkdir()
    env = child_env(
        home,
        AELIX_CODING_AGENT_DIR=str(home / "agent"),
        AELIX_CODING_AGENT_SESSION_DIR=str(home / "sessions"),
        PI_OFFLINE="1",
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CRASHED_PARENT,
        str(tmp_path),
        str(root),
        cwd=str(root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    said = out.decode("utf-8", errors="replace").strip().splitlines()
    assert proc.returncode == 0 and said, err.decode("utf-8", errors="replace")
    crashed = json.loads(said[-1])
    ((child_pid, stamp),) = crashed["pids"]

    def kill_the_child() -> None:
        # Only the process the crashed parent started: a pid that is gone may be reused.
        if stamp is not None and process_started(child_pid) == stamp:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
    (key,) = crashed["keys"]
    job_id, idents = crashed["job_id"], (_agent_id(crashed["text"]), key)
    (path,) = (tmp_path / "s").glob("*/p.jsonl")

    async def reopened() -> Any:
        return Session(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))

    try:
        session = await reopened()
        service, jobs, _harness = await _restart(session)
        try:
            first = service.snapshot(job_id)
            assert first is not None and first.state in ("unresolved", "lost"), first
            await _named_alike(service, jobs, session, job_id, idents)
            if first.state == "unresolved":
                kill_the_child()
                await wait_for(lambda: service.snapshot(job_id).state == "lost", timeout=30)  # type: ignore[union-attr]
                await _named_alike(service, jobs, session, job_id, idents)
        finally:
            await service.shutdown()
        session = await reopened()
        assert _outcomes(list(await session.get_entries())) == ["lost"], "the restart recorded it"
        service, jobs, _harness = await _restart(session)
        try:
            snap = service.snapshot(job_id)
            assert snap is not None and (snap.state, dict(snap.payload)) == ("lost", {}), snap
            await _named_alike(service, jobs, session, job_id, idents)
        finally:
            await service.shutdown()
    finally:
        kill_the_child()


@pytest.mark.parametrize("settled", [False, True], ids=["no-settle", "settled"])
@pytest.mark.parametrize(
    "ids",
    [
        pytest.param({"key": ["sub-x"], "child": {"session_id": "s-1"}}, id="list-key"),
        pytest.param({"key": "sub-x", "child": {"session_id": {"a": 1}}}, id="dict-session-id"),
    ],
)
async def test_a_start_record_whose_ids_are_not_strings_reads_back_as_before(
    tmp_path: Path, ids: dict[str, Any], settled: bool
) -> None:
    """#847 fix round 1, F2 (the verify round's probe): a run's start record whose run
    key or child session id is a list or a dict, which only a hand-edited or corrupt
    file holds, reads back as it did before #847: ``done`` from its parent's settle,
    else ``unresolved`` from the OS evidence, and its job id names it. Passes on
    ``0436193c``. Fails when ``keep_read_back`` keeps values that are not strings (as
    #847 was first built): a frozenset of the list or dict raised in ``recover``, and
    the job read ``unresolved(recover_error)``."""

    import os

    from aelix_agents.child_session import CHILD_SESSION_TYPE, append_records
    from aelix_ai.utils._process_identity import process_started

    from tests.agents_ext.test_child_recovery_611 import _JOB, _process_fact, _restart, _start_fact
    from tests.agents_ext.test_child_session_records import _parent

    session, _repo, _path = await _parent(tmp_path)
    await _start_fact(session)
    records = [(CHILD_SESSION_TYPE, {"v": 1, "phase": "start", "job_id": _JOB, **ids})]
    if settled:
        records.append((CHILD_SESSION_TYPE, {"v": 1, "phase": "settle", "job_id": _JOB, "status": "ok", **ids}))
    await append_records(session, records)
    await _process_fact(session, os.getpid(), process_started(os.getpid()))
    service, _jobs, _harness = await _restart(session)
    try:
        snap = service.snapshot(_JOB)
        assert snap is not None, snap
        if settled:
            assert (snap.state, snap.unresolved_reason) == ("done", None), snap
        else:
            assert snap.state == "unresolved", snap
            # OS evidence's reason (Linux adds that the argv no longer shows the job), not ``recover_error``.
            assert str(snap.unresolved_reason).startswith(f"pid {os.getpid()} is still running"), snap
        assert (await _job(create_job_tool(service), action="status", id=_JOB))[0] is False
    finally:
        await service.shutdown()


async def test_after_a_restart_an_agent_whose_older_run_settled_names_its_newer_unfinished_run(
    tmp_path: Path,
) -> None:
    """#847 fix round 1, F3 (the verify round's probe): a resumed agent has two runs,
    two jobs and one agent id. Its older run ended with its parent's settle, so a
    restart reads its ids into that job's payload; its newer run was still running
    when the parent crashed, so its job has an empty payload and only its start
    record names it. The ``job`` tools, ``/jobs`` and ``agent()`` all take the newer
    job, newest first. Fails on ``0436193c`` (``job`` named the older job while
    ``agent()`` took the newer); it kills the mutant that reads every job's payload
    before any job's start record (verify round 1, M8)."""

    import asyncio
    import os

    from aelix_agents.child_session import append_records
    from aelix_agents.jobs_client import KIND
    from aelix_agents.stream import _StreamState
    from aelix_ai.utils._process_identity import process_started
    from aelix_coding_agent.jobs import evidence as ev
    from aelix_coding_agent.jobs.records import append_fact, make_fact
    from aelix_coding_agent.subagent_contract import SubagentResult

    from tests.agents_ext.test_child_recovery_611 import _child_says, _receipt, _restart
    from tests.agents_ext.test_child_session_records import _parent

    older, newer = "aaaa000000000001", "bbbb000000000002"
    session, _repo, _path = await _parent(tmp_path)
    meta = await session.get_metadata()

    async def start(job_id: str, created: float) -> None:
        fields = {"kind": KIND, "kind_version": 1, "owner": meta.id, "mode": "background", "notify": True}
        fields.update(title=f"scout {job_id[:4]}", created=created, settings_key="agents")
        await append_fact(session, make_fact("start", job_id, **fields))

    async def process(job_id: str, pid: int, stamp: float | None) -> None:
        evidence = {"pid": pid, "started": stamp, "host": ev.this_host()}
        await append_fact(session, make_fact("process", job_id, seq=1, evidence=evidence))

    first = _receipt("sub-aaaaaaaaaaaa", job_id=older)
    await start(older, 1.0)
    await first.open(session, cwd=str(tmp_path / "p"))
    assert first.child is not None
    _child_says(first.child.path, "run A's answer")
    result = SubagentResult(id=first.key, profile="scout", ok=True, status="ok", summary="x")
    settle = first.outcome_records(result, state=_StreamState())
    await append_records(session, [(t, {**d, "job_id": older}) for t, d in settle])
    gone = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    stamp = process_started(gone.pid)
    await gone.wait()
    await process(older, gone.pid, stamp)
    await start(newer, 2.0)
    second = _receipt("sub-bbbbbbbbbbbb", resumes=first.key, job_id=newer)
    await second.attach(session, child=first.child)
    await process(newer, os.getpid(), process_started(os.getpid()))
    agent_id = first.child.session_id
    service, jobs, _harness = await _restart(session)
    try:
        a, b = service.snapshot(older), service.snapshot(newer)
        assert a is not None and a.state == "done" and a.payload.get("session_id") == agent_id, a
        assert b is not None and b.state == "unresolved" and dict(b.payload) == {}, b
        assert await jobs.resolve(service, agent_id, session=session) == newer, "agent() takes the newer run"
        status = await _job(create_job_tool(service), action="status", id=agent_id)
        assert status == await _job(create_job_tool(service), action="status", id=newer), status
        assert (await JobsCommand(service)(agent_id)).splitlines()[1].strip() == f"id        {newer}"
        assert find(service, agent_id).job_id == newer  # type: ignore[union-attr]
    finally:
        await service.shutdown()
