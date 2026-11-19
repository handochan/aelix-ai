"""#827 — ``job(action="status"|"result")`` takes a background agent's own id.

A background agent has two ids, its agent id and its job id, and the answer that
starts it names both ("(id <agent id>; its job is <job id>)"); ``agent()`` takes
either. The ``job`` tools found a job by its job id or a prefix only, so an agent
id read "no job … in this session" — the #123 live parent called it three times
before it used the job id. ``commands.find`` now asks each kind that is a
``JobIdAdapter`` (the agent kind: ``DelegationKindAdapter.job_for``) once neither an
exact job id nor a unique prefix of one matched (fix round 1, Codex H2), so ``job``,
``job_stop`` and ``/jobs`` take the agent id too, after a restart for a run that has
ended (Codex M4; a spawned run read back unresolved is named by its job id only,
V2-F2); an id nothing names says, in the ``job`` and ``job_stop`` tools, that
``job(action="list")`` lists the ids.

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


async def _read_back(tmp_path: Path, *, alive: bool) -> Any:
    """A spawned run's records with no settle record, and a restart that reads them back:
    ``recovered`` when its recorded process is gone, ``unresolved`` when it is alive."""

    import os

    from aelix_ai.utils._process_identity import process_started

    from tests.agents_ext.test_child_recovery_611 import (
        _gone_process_fact,
        _process_fact,
        _restart,
        _spawned,
        _start_fact,
    )

    session, receipt = await _spawned(tmp_path, "CHILD SAID THIS")
    await _start_fact(session)
    if alive:
        await _process_fact(session, os.getpid(), process_started(os.getpid()))
    else:
        await _gone_process_fact(session)
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


async def test_a_spawned_run_read_back_unresolved_is_named_by_its_job_id_only(tmp_path: Path) -> None:
    """V2-F2: a spawned run the restart reads back as ``unresolved`` (its recorded
    process alive, no settle record) has no ids in its payload, so the ``job`` tools
    answer "no job …" to its agent id and its run key, while ``agent()`` takes both.
    This pins what the docs now say (the claim was "after a restart" with no
    condition); it passes on ``a3c8b9a6``, which behaves the same."""

    from tests.agents_ext.test_child_recovery_611 import _JOB

    session, receipt, service, jobs = await _read_back(tmp_path, alive=True)
    try:
        snap = service.snapshot(_JOB)
        assert snap is not None and (snap.state, dict(snap.payload)) == ("unresolved", {}), snap
        assert receipt.child is not None
        job = create_job_tool(service)
        assert (await _job(job, action="status", id=_JOB))[0] is False
        for ident in (receipt.child.session_id, receipt.key):
            error, said = await _job(job, action="status", id=ident)
            assert error is True and said.startswith(f"no job {ident} in this session;"), said
            assert await JobsCommand(service)(ident) == f"no job {ident} in this session"
            assert await jobs.resolve(service, ident, session=session) == _JOB, "agent() takes it"
    finally:
        await service.shutdown()


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
    # The rig's 0.3 s exit grace failed the moved run under load (verify r3 R3-F1:
    # 4 of 73 at load 25-42); the product gives 45 s, test_detach_acceptance_777 30 s.
    rig.service._exit_grace = 30.0
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
