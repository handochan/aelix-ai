"""#775 (#611 S3a) — Ctrl+B moves a running foreground delegation into a background job (ADR-0298).

AT WHAT LEVEL. ``test_agent_background_611``'s rig — the REAL ``AgentsExtension``
hook and ``execute()``, the REAL job service bound to a harness with the kernel
inbox on, a REAL ``PrintChannel`` whose child is a stdlib stub writing the
print-mode stream — told it is on the TUI, with the TUI's real ``JobsUi`` as the
host (``tests/agents_ext/_detach_rig.py``). The key is ``JobsUi.request_detach``.

Fails on ``c91cdc7b``: nothing offers a running call (``JobService.detach``),
and ``AgentsExtension`` has no delegate.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agents import detach as dt
from aelix_agents.child_session import CHILD_SESSION_TYPE, RESUME_RUNNING, USAGE_TYPE
from aelix_agents.jobs_client import (
    KIND,
    AdoptedRunner,
    AgentJob,
    DelegationJobs,
    DelegationKindAdapter,
    _job_named_by,
    adopted_client_start,
)
from aelix_agents.print_channel import CleanupDuty, HelperAnswer, _woke_for_detach
from aelix_coding_agent.jobs.contract import ClientOutcome
from aelix_coding_agent.jobs.notice import final_notice_id
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE

from tests.agents_ext._detach_rig import (  # noqa: F401 — reap_children is the fixture
    call,
    finish,
    parked_call,
    ready,
    reap_children,
    row_of,
    script,
    settled,
    tui_rig,
    within,
)
from tests.agents_ext.test_agent_background_611 import (  # noqa: F401 — the fixture
    _spawned_id,
    jobs_on,
)
from tests.agents_ext.test_stopped_child_reports_604 import _QueueSpy
from tests.agents_ext.test_tool_and_security import _text
from tests.jobs.conftest import wait_for

REAL_CHILD = pytest.mark.skipif(
    sys.platform == "win32",
    reason="as test_agent_background_611: the gated rig's real child is proven on win32 by #611 S3c",
)
pytestmark = pytest.mark.usefixtures("jobs_on", "reap_children")
MOVED = "Moved to the background at the user's request"


def _agent_id(text: str) -> str:
    return text.split("(id ", 1)[1].split(";", 1)[0]


def _resumable(gate: Path) -> Any:
    """A stub child that writes the user line a real child's session holds, so it can be resumed."""

    from aelix_agents.print_channel import PrintChannel

    from tests.agents_ext.test_print_channel_spawn import _stub

    script = _stub(
        "path = sys.argv[1]\n"
        "with open(path, 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'type': 'message', 'id': 'u1', 'parentId': None,"
        " 'timestamp': '2026-11-09T00:00:00Z', 'message': {'role': 'user',"
        " 'content': [{'type': 'text', 'text': 'Task'}]}}) + '\\n')\n"
        "start()\n"
        "say('halfway')\n"
        "end = time.time() + 30\n"
        f"while not os.path.exists({str(gate)!r}) and time.time() < end:\n"
        "    time.sleep(0.02)\n"
        "say('the moved answer')\n"
        "done()\n"
        'if os.environ.get("AELIX_JOBS_PROTOCOL") == "1":\n'
        '    emit({"type": "aelix_print_done"})\n'
    )
    return PrintChannel(
        grace=2.0,
        argv_builder=lambda *_a, **k: [sys.executable, "-c", script, k.get("session_path") or ""],
    )


async def _moved(tmp_path: Path, **kwargs: Any) -> tuple[Any, Path, str, Any]:
    gate = tmp_path / "gate"
    if "channel" not in kwargs:
        kwargs["script"] = script("the moved answer", gate=gate, partial="halfway")
    rig = await tui_rig(tmp_path, **kwargs)
    queued: list[Any] = []
    rig.ext._api = _QueueSpy(rig.ext._api, queued)
    rig.queued = queued
    turn = await parked_call(rig)
    row = row_of(rig)
    await wait_for(lambda: row.stream.last_text == "halfway")
    assert rig.jobs_ui.request_detach()
    text = _text(await within(turn))
    assert MOVED in text, text
    return rig, gate, text, row


@REAL_CHILD
async def test_ctrl_b_in_the_live_wait_moves_the_run_into_a_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_coding_agent.jobs.service import JobService

    rescues: list[str] = []
    monkeypatch.setattr(JobService, "rescue_adopted", lambda self, job, why: rescues.append(job.job_id))
    rig, gate, text, row = await _moved(tmp_path)
    (snap,) = rig.jobs()
    assert snap.state == "running" and snap.job_id in text
    jobs = rig.ext._delegation_jobs
    assert jobs.job_id_for(row.session_id) == jobs.job_id_for(row.id) == snap.job_id, "named at the move"
    assert f'agent(action="status", id="{row.session_id}")' in text
    assert rig.queued == [], "no D14 report: the call returned, it was not cancelled"
    runtime = rig.ext.runtime
    assert runtime is not None and runtime._children == {row.id: row}, "the row lives until _run ends"
    assert rig.service.admission.count(KIND) == 1, "the slot moved into the job"
    gate.write_text("go")
    snap = await settled(rig)
    assert snap.state == "done" and snap.brief == "the moved answer"
    assert runtime._children == {}, "_run ended in the job's task and popped it"
    await asyncio.sleep(0.05)
    assert rescues == [], "the runner drove the run: its first task's rescue stood down"
    (notice,) = rig.harness.undelivered_notices()
    assert notice.notice_id == final_notice_id(snap.job_id) and "the moved answer" in notice.body
    assert rig.service.admission.count(KIND) == 0
    entries = await rig.session.get_entries()
    shape = [
        (e.custom_type, e.data.get("fact") or e.data.get("phase") or e.data.get("state"), e.data.get("job_id"))
        for e in entries
        if e.type == "custom" and e.custom_type in (JOB_FACT_TYPE, CHILD_SESSION_TYPE, USAGE_TYPE)
    ]
    job = snap.job_id
    # The run's opening records are the call's (it started in the foreground);
    # its closing records went to the job, confirmed and carrying its id, before
    # the job's cleanup and outcome (ADR-0293 §7).
    assert shape == [
        (CHILD_SESSION_TYPE, "start", None),
        (USAGE_TYPE, "pending", None),
        (JOB_FACT_TYPE, "start", job),
        (JOB_FACT_TYPE, "process", job),
        (CHILD_SESSION_TYPE, "settle", job),
        (USAGE_TYPE, "final", job),
        (JOB_FACT_TYPE, "cleanup", job),
        (JOB_FACT_TYPE, "outcome", job),
    ]
    start = next(e.data for e in entries if e.type == "custom" and e.data.get("fact") == "start")
    assert isinstance(row.session_rel, str) and row.session_rel.endswith(".jsonl")
    assert start["client"] == {
        "v": 1,
        "adopted": True,
        "key": row.id,
        "child": {"session_id": row.session_id, "rel": row.session_rel},
        "profile": "scout",
        "task": "count files",
        "tool_call_id": start["client"]["tool_call_id"],
        "posture": "plan",
    }
    process = next(e.data for e in entries if e.type == "custom" and e.data.get("fact") == "process")
    assert process["evidence"]["pid"] == row.proc.pid
    assert process["evidence"]["started"] == row.launch_started, "the stamp taken at the spawn"
    outcome = next(e.data for e in entries if e.type == "custom" and e.data.get("fact") == "outcome")
    assert outcome["client_records"] == "ok"
    await finish(rig)


@REAL_CHILD
async def test_progress_after_the_move_goes_to_the_job_not_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, gate, _text_, row = await _moved(tmp_path)
    bridge = rig.ext._progress
    seen: list[str] = []
    monkeypatch.setattr(type(bridge), "__call__", lambda self, progress: seen.append(progress.id))
    # The call's own tap (its panel group, its card) gets nothing after the move.
    monkeypatch.setattr(type(bridge), "adopt", lambda self, spawn_id, key, index=0: seen.append(spawn_id))
    assert row.id in rig.ext._adopted_routes
    gate.write_text("go")
    snap = await settled(rig)
    assert row.id not in seen, "the statusline bridge never sees the moved run again"
    assert snap.progress is not None and snap.progress.get("state") in ("done", "error")
    assert rig.ext._adopted_routes == {}, "the route ends with the run"
    await finish(rig)


@REAL_CHILD
async def test_a_moved_agent_is_running_to_resume_and_resumes_once_done(tmp_path: Path) -> None:
    rig, gate, text, _row = await _moved(tmp_path, channel=_resumable(tmp_path / "gate"))
    resume = {"action": "resume", "profile": "scout", "id": _agent_id(text), "task": "again"}
    refused = await call(rig, resume)
    assert refused.is_error and _text(refused) == RESUME_RUNNING
    gate.write_text("go")
    await settled(rig)
    gate.unlink()
    rig.ext.detach_host = lambda: False  # this resume runs to its end in the foreground
    running = asyncio.ensure_future(rig.call(resume))
    await wait_for(lambda: rig.ext.runtime is not None and len(rig.ext.runtime._children) == 1)
    gate.write_text("go")
    resumed = await within(running)
    assert not resumed.is_error, _text(resumed)
    assert "the moved answer" in _text(resumed)
    await finish(rig)


@REAL_CHILD
async def test_a_running_spawned_agent_is_claimed_against_resume(tmp_path: Path) -> None:
    """R15: a ``spawn`` job runs on a runtime of its own, so the claim is the client's, not a row's."""

    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, channel=_resumable(gate))
    agent_id = _spawned_id(await call(rig, {"action": "spawn", "profile": "scout", "task": "t"}))
    await wait_for(lambda: (rig.jobs()[0].progress or {}).get("last_text") == "halfway")
    jobs = rig.ext._delegation_jobs
    assert jobs is not None and jobs.live_sessions(rig.ext._jobs_facade) == {agent_id}
    refused = await call(rig, {"action": "resume", "profile": "scout", "id": agent_id, "task": "again"})
    assert refused.is_error and _text(refused) == RESUME_RUNNING
    gate.write_text("go")
    await settled(rig)
    assert jobs.live_sessions(rig.ext._jobs_facade) == set()
    await finish(rig)


@REAL_CHILD
async def test_a_moved_run_stopped_by_the_user_ends_stopped(tmp_path: Path) -> None:
    """``/jobs stop`` (``JobService.stop``) on a moved run: today's stop path, through the job."""

    rig, gate, _text_, row = await _moved(tmp_path)
    (snap,) = rig.jobs()
    assert rig.service.request_stop(snap.job_id)
    assert row.stopped, "the row reads stopped in the step the stop was asked"
    snap = await settled(rig)
    assert snap.state == "stopped"
    assert row.proc.returncode is not None and rig.service.admission.count(KIND) == 0
    (notice,) = rig.harness.undelivered_notices()
    assert notice.notice_id == final_notice_id(snap.job_id)
    gate.write_text("go")
    await finish(rig)


@REAL_CHILD
async def test_host_stop_after_adoption_before_runner_start_keeps_the_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#855 report verification R1: stop the published job before its task enters."""
    from aelix_coding_agent.jobs.service import JobService

    entered, release = asyncio.Event(), asyncio.Event()
    original = JobService.run_adopted

    async def gated(service: JobService, job: Any) -> None:
        entered.set()
        await release.wait()
        await original(service, job)

    monkeypatch.setattr(JobService, "run_adopted", gated)
    rig, _, _, row = await _moved(tmp_path)
    controls, ends = rig.ext.runtime.controls, []

    def observe(event: str, data: dict[str, Any]) -> None:
        if event == "state" and data.get("end"):
            ends.append(data["reason"])

    off = controls.subscribe(observe)
    try:
        (job,) = rig.jobs()
        assert any(child.get("jobId") == job.job_id for child in controls.list())
        await asyncio.wait_for(entered.wait(), 10)
        assert await controls.stop(row.id) == {"state": "running"}
        release.set()
        snap = await settled(rig)
        assert snap.state == "stopped"
        result = _text(await call(rig, {"action": "result", "id": job.job_id}))
        host = "stopped by the RPC host (children/stop)"
        assert (ends, host in result) == ([host], True), (ends, result)
    finally:
        release.set()
        off()
        await finish(rig)


@REAL_CHILD
async def test_a_running_resume_moves_too(tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, channel=_resumable(gate))
    rig.ext.detach_host = lambda: False
    gate.write_text("go")
    first = _text(await call(rig, {"profile": "scout", "task": "first"}))
    child_id = first.split("Session: ", 1)[1].split()[0]
    gate.unlink()
    rig.ext.detach_host = lambda: True
    turn = await parked_call(rig, {"action": "resume", "profile": "scout", "id": child_id, "task": "again"})
    row = row_of(rig)
    await wait_for(lambda: row.stream.last_text == "halfway")
    assert row.session_id == child_id
    assert rig.jobs_ui.request_detach()
    text = _text(await within(turn))
    assert 'Moved to the background at the user\'s request: the resumed agent "scout"' in text, text
    gate.write_text("go")
    snap = await settled(rig)
    assert snap.state == "done" and len(rig.harness.undelivered_notices()) == 1
    await finish(rig)


# === refused phases: the key is not taken, or the commit refuses ==========================


@REAL_CHILD
async def test_no_offer_while_the_spawn_is_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aelix_agents.print_channel as pc

    rig = await tui_rig(tmp_path)
    release = asyncio.Event()
    real = pc.asyncio.create_subprocess_exec

    async def held(*args: Any, **kwargs: Any) -> Any:
        await release.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(pc.asyncio, "create_subprocess_exec", held)
    turn = asyncio.ensure_future(rig.call({"profile": "scout", "task": "t"}))
    await wait_for(lambda: rig.ext.runtime is not None and len(rig.ext.runtime._children) == 1)
    assert ready(rig) == 0 and not rig.jobs_ui.request_detach()
    release.set()
    assert MOVED not in _text(await within(turn))
    await finish(rig)


@REAL_CHILD
async def test_a_stopped_row_or_a_transition_offers_nothing(tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("x", gate=gate, partial="halfway"))
    turn = await parked_call(rig)
    row = row_of(rig)
    row.stopped = True
    assert ready(rig) == 0 and not rig.jobs_ui.request_detach()
    row.stopped = False
    rig.host.in_transition = True
    assert not rig.jobs_ui.request_detach(), "a reload or a session change is running"
    rig.host.in_transition = False
    rig.chrome.running = False
    assert not rig.jobs_ui.request_detach(), "no turn runs"
    rig.chrome.running = True
    gate.write_text("go")
    assert MOVED not in _text(await within(turn))
    assert rig.service.detach._offers == [], "the live wait is over: the offer went with it"
    await finish(rig)


@REAL_CHILD
async def test_two_offers_are_not_one(tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("x", gate=gate, partial="halfway"))
    turn = await parked_call(rig)
    other = dt.CallDetach(adopt=lambda c, i: None, offer=lambda c: lambda: None)
    other.state, other.row, other._future = "parked", row_of(rig), asyncio.get_running_loop().create_future()
    withdraw = rig.service.detach.offer(other)
    assert ready(rig) == 2 and not rig.jobs_ui.detach_ready() and not rig.jobs_ui.request_detach()
    assert not rig.service.detach.request(), "the offers' own check: exactly one"
    withdraw()
    gate.write_text("go")
    assert MOVED not in _text(await within(turn))
    await finish(rig)


async def test_a_wake_that_raced_the_end_keeps_the_result() -> None:
    """Model H: Ctrl+B and the child's end in one loop turn — the run returns its result."""

    loop = asyncio.get_running_loop()
    asked, pumps, exited = loop.create_future(), loop.create_future(), loop.create_future()
    claimed: list[bool] = []
    row = SimpleNamespace(detach=SimpleNamespace(claim=lambda: claimed.append(True)))
    asked.set_result(True)
    exited.set_result(0)
    assert _woke_for_detach(row, asked, pumps, exited) is False  # type: ignore[arg-type]
    assert _woke_for_detach(row, None, pumps, loop.create_future()) is False  # type: ignore[arg-type]
    eof = loop.create_future()
    eof.set_result(None)  # both pipes at EOF, the exit not seen yet: the post-EOF bound (r3 §2.3)
    assert _woke_for_detach(row, asked, eof, loop.create_future()) is False  # type: ignore[arg-type]
    assert claimed == []
    assert _woke_for_detach(row, asked, pumps, loop.create_future()) is True  # type: ignore[arg-type]
    assert claimed == [True]


async def test_ready_needs_a_live_unstopped_child() -> None:
    call = dt.CallDetach(adopt=lambda c, i: None, offer=lambda c: lambda: None)
    proc = SimpleNamespace(returncode=None)
    row = SimpleNamespace(id="r", proc=proc, stopped=False, eager_kill=False)
    assert call.park(row) is not None and call.ready()  # type: ignore[arg-type]
    proc.returncode = 0
    assert not call.ready()
    proc.returncode, row.eager_kill = None, True
    assert not call.ready()
    row.eager_kill = False
    assert call.request() and not call.ready() and not call.request(), "one request"
    call.claim()
    assert call.state == "claimed" and call.park(row) is None  # type: ignore[arg-type]
    fresh = dt.CallDetach(adopt=lambda c, i: None, offer=lambda c: lambda: None)
    fresh.park(row)  # type: ignore[arg-type]
    fresh.leave()
    assert fresh.state == "left" and not fresh.ready()


# === the moved run's records and cleanup answer (N5, N6) ===================================


def _runner() -> AdoptedRunner:
    return AdoptedRunner(
        box=SimpleNamespace(inner=None),
        row=SimpleNamespace(id="r", stopped=False, cleanup=None),  # type: ignore[arg-type]
        runtime=None,  # type: ignore[arg-type]
        link=dt.AdoptedLink(job_id="j"),
        entry=AgentJob(job_id="j", profile="scout", key="r"),
        profile="scout",
        session=None,
        on_end=lambda: None,
    )


async def test_adopted_runner_accepts_the_existing_context_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _runner()

    async def drive(*_args: Any, **_kwargs: Any) -> ClientOutcome:
        raise RuntimeError("entered the adopted run")

    monkeypatch.setattr(dt, "drive", drive)
    with pytest.raises(RuntimeError, match="entered the adopted run"):
        await runner.run(ctx=SimpleNamespace())


def test_records_raise_when_the_run_handed_none_over() -> None:
    runner = _runner()
    with pytest.raises(RuntimeError, match="no closing records"):
        runner.records(ClientOutcome("done"))
    runner._link.hand([("aelix.child_session", {"phase": "settle"})])
    assert runner.records(ClientOutcome("done")) == [("aelix.child_session", {"phase": "settle"})]


@pytest.mark.skipif(sys.platform == "win32", reason="win32 answers unverified: its Job's members end later")
async def test_the_cleanup_answer_the_run_kept_is_the_jobs() -> None:
    def duty(answer: Any = None, error: BaseException | None = None) -> CleanupDuty:
        kept = CleanupDuty(key="r", answer=answer, error=error)
        kept.task = asyncio.ensure_future(asyncio.sleep(0))
        return kept

    confirmed = {"confirmed": True}
    runner = _runner()
    assert (await runner.helper_cleanup({"confirmed": False})).state == "unverified"
    assert (await runner.helper_cleanup(confirmed)).state == "unverified", "no duty: untracked"
    runner._row.cleanup = duty(None)  # a run with no launch ledger: its sweep never ran
    untracked = await runner.helper_cleanup(confirmed)
    assert (untracked.state, untracked.reason) == ("unverified", "the agent's run had no launch ledger to sweep")
    runner._row.cleanup = duty(HelperAnswer(confirmed=True))
    assert (await runner.helper_cleanup(confirmed)).state == "confirmed"
    runner._row.cleanup = duty(HelperAnswer(confirmed=False, reason="2 left"))
    answer = await runner.helper_cleanup(confirmed)
    assert (answer.state, answer.reason) == ("unverified", "2 left")
    runner._row.cleanup = duty(error=OSError("boom"))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await runner.helper_cleanup(confirmed)
    pending = CleanupDuty(key="r")
    pending.task = asyncio.ensure_future(asyncio.Event().wait())
    runner._row.cleanup = pending
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runner.helper_cleanup(confirmed), 0.05)
    assert not pending.task.done(), "the job's bound never cancels the run's own cleanup"
    pending.task.cancel()


# === linkage v1 and its readers, before and after a restart (N7) ========================


def _linkage() -> dict[str, Any]:
    row = SimpleNamespace(id="sub-k", session_id="child-1", session_rel="kids/child-1.jsonl")
    return adopted_client_start(row, profile="scout", task="t", tool_call_id="tc", posture="plan")  # type: ignore[arg-type]


def test_decode_after_a_restart_reads_the_moved_runs_identity_from_its_linkage() -> None:
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: None)
    decoded = adapter.decode(
        {"fact": {"status": "recovered", "brief": "b"}, "records": [], "client_start": _linkage()}
    )
    assert decoded.payload["key"] == "sub-k" and decoded.payload["session_id"] == "child-1"
    assert decoded.payload["rel"] == "kids/child-1.jsonl" and decoded.payload["profile"] == "scout"


async def test_recover_before_the_settle_reads_the_child_by_the_linkage(monkeypatch: pytest.MonkeyPatch) -> None:
    import aelix_agents.jobs_client as jc

    asked: list[str] = []

    async def answer(session: Any, key: str) -> Any:
        asked.append(key)
        return jc.CANNOT_BIND_RUN

    monkeypatch.setattr(jc, "recover_run_answer", answer)
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: object())
    recovered = await adapter.recover({"client": _linkage()}, [])
    assert asked == ["sub-k"] and recovered is not None and recovered.status == "unresolved"
    unknown = {**_linkage(), "v": 2}
    recovered = await adapter.recover({"client": unknown}, [])
    assert recovered is not None and (recovered.status, recovered.reason) == ("unresolved", "adapter_decode_error")
    assert await adapter.recover({}, []) is None, "a spawn job with no start record: OS evidence"


async def test_recover_after_the_settle_is_done() -> None:
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: object())
    settle = {"custom_type": CHILD_SESSION_TYPE, "phase": "settle", "status": "ok", "key": "sub-k", "child": {}}
    recovered = await adapter.recover({"client": _linkage()}, [settle])
    assert recovered is not None and recovered.status == "done"


def test_resolve_finds_a_moved_job_by_its_start_fact() -> None:
    fact = SimpleNamespace(custom_type=JOB_FACT_TYPE, data={"fact": "start", "job_id": "job-1", "client": _linkage()})
    assert _job_named_by(fact) == ("job-1", {"sub-k", "child-1"})
    plain_start = SimpleNamespace(custom_type=JOB_FACT_TYPE, data={"fact": "start", "job_id": "job-2"})
    assert _job_named_by(plain_start) is None
    settle = SimpleNamespace(
        custom_type=CHILD_SESSION_TYPE, data={"job_id": "job-3", "key": "k", "child": {"session_id": "s"}}
    )
    assert _job_named_by(settle) == ("job-3", {"k", "s"})
    other = SimpleNamespace(custom_type="something.else", data={"job_id": "job-4", "key": "k"})
    assert _job_named_by(other) is None


@REAL_CHILD
async def test_a_run_whose_start_time_was_not_read_is_refused_at_the_commit(tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("x", gate=gate, partial="halfway"))
    turn = await parked_call(rig)
    row = row_of(rig)
    row.launch_started = None
    assert rig.jobs_ui.request_detach()
    await asyncio.sleep(0.05)
    assert row.detach.refusal == "the agent's start time was not recorded at its spawn"
    assert rig.jobs() == []
    gate.write_text("go")
    assert MOVED not in _text(await within(turn))
    await finish(rig)


@REAL_CHILD
async def test_a_subclassed_channel_is_refused_at_the_chain_walk(tmp_path: Path) -> None:
    """A frame the audit never admitted — here a subclass's ``run`` — refuses the move."""

    from aelix_agents.print_channel import PrintChannel

    class Wrapped(PrintChannel):
        async def run(self, plan: Any, **kwargs: Any) -> Any:
            return await super().run(plan, **kwargs)

    gate = tmp_path / "gate"
    source = script("x", gate=gate, partial="halfway")
    channel = Wrapped(grace=2.0, argv_builder=lambda *_a, **_k: [sys.executable, "-c", source])
    rig = await tui_rig(tmp_path, channel=channel)
    turn = await parked_call(rig)
    row = row_of(rig)
    assert rig.jobs_ui.request_detach()
    await asyncio.sleep(0.05)
    assert row.detach.refusal == "a frame across the move is not in the audited set"
    gate.write_text("go")
    assert MOVED not in _text(await within(turn)) and rig.jobs() == []
    await finish(rig)


def test_the_row_and_slot_checks() -> None:
    from aelix_agents.extension import _row_refusal, _slot_refusal

    def row(**kw: Any) -> Any:
        base = {"proc": SimpleNamespace(returncode=None), "launch_started": 1.0, "stopped": False, "eager_kill": False}
        return SimpleNamespace(**{**base, **kw})

    assert _row_refusal(row()) is None
    assert _row_refusal(row(proc=None)) == "the agent has already ended"
    assert _row_refusal(row(proc=SimpleNamespace(returncode=0))) == "the agent has already ended"
    assert _row_refusal(row(launch_started=None)) == "the agent's start time was not recorded at its spawn"
    assert _row_refusal(row(stopped=True)) == "the agent is being stopped"
    assert _row_refusal(row(eager_kill=True)) == "the agent is being stopped"
    spent = "the call's slot is spent"
    assert _slot_refusal(None) == spent
    assert _slot_refusal(SimpleNamespace(released=True, slots=1)) == spent
    assert _slot_refusal(SimpleNamespace(released=False, slots=0)) == spent
    assert _slot_refusal(SimpleNamespace(released=False, slots=1)) is None


@pytest.mark.skipif(sys.platform == "win32", reason="the sweep is POSIX's (win32 has no launch ledger)")
async def test_the_cleanup_task_keeps_its_sweeps_answer_and_its_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aelix_agents.print_channel as pc

    async def ended(proc_answer: Any) -> CleanupDuty:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        await proc.wait()
        row = pc.RunningChild(id="r", profile="p", proc=proc)
        row.tracking = pc.HelperTracking(token="t", ledger=None, beside_session=False)
        duty = CleanupDuty(key="r")
        duty.ended.set()

        async def sweep(tracking: Any, *, parent_stopped: bool) -> Any:
            if isinstance(proc_answer, BaseException):
                raise proc_answer
            return proc_answer

        monkeypatch.setattr(pc, "sweep_helpers", sweep)
        await pc._clean_up(row, duty, 0.5)
        return duty

    kept = await ended(HelperAnswer(confirmed=False, reason="2 left"))
    assert kept.answer == HelperAnswer(confirmed=False, reason="2 left") and kept.error is None
    failed = await ended(OSError("no scan"))
    assert failed.answer is None and isinstance(failed.error, OSError)


async def test_an_unknown_linkage_version_is_unresolved_with_or_without_a_settle() -> None:
    """Codex r1 6, verify F8. On ``fb5e7654`` a v2 linkage with a settle recovered ``done``, and
    ``decode`` read it. Both now refuse it: ``recover`` says ``adapter_decode_error`` and ``decode``
    raises, so the service keeps the job's stored brief and body (ADR-0293's decode failure)."""

    unknown = {**_linkage(), "v": 2}
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: object())
    settle = {"custom_type": CHILD_SESSION_TYPE, "phase": "settle", "status": "ok", "key": "sub-k", "child": {}}
    for records in ([], [settle]):
        recovered = await adapter.recover({"client": unknown}, records)
        assert recovered is not None and (recovered.status, recovered.reason) == (
            "unresolved",
            "adapter_decode_error",
        )
    for status in ("done", "recovered"):
        with pytest.raises(ValueError, match="adapter_decode_error"):
            adapter.decode({"fact": {"status": status}, "records": [settle], "client_start": unknown})
    assert adapter.decode({"fact": {"status": "done"}, "records": [settle], "client_start": _linkage()}).status == "done"


async def test_a_restart_decodes_a_recovered_moved_run_from_its_linkage(tmp_path: Path) -> None:
    """Verify F10 M092: the service hands ``decode`` the start fact's ``client``; a moved run's
    recovered outcome reads its child session and key from it, as no start record of the job
    names them. (``_read_back``'s copy, M093, is for jobs this process ran, which never end
    ``recovered``: equivalent.)"""

    from aelix_coding_agent.jobs.records import append_fact, make_fact

    from tests.agents_ext.test_child_recovery_611 import _restart
    from tests.agents_ext.test_child_session_records import _parent

    session, _repo, _path = await _parent(tmp_path)
    meta = await session.get_metadata()
    common = {"kind": KIND, "kind_version": 1, "owner": meta.id, "mode": "background", "notify": True}
    await append_fact(
        session,
        make_fact("start", "job-m", title="scout: t", created=0.0, settings_key="agents", client=_linkage(), **common),
    )
    await append_fact(
        session,
        make_fact("outcome", "job-m", status="recovered", brief="b", elapsed_ms=0, client_records="ok"),
    )
    service, _jobs, _harness = await _restart(session)
    snap = service.snapshot("job-m")
    assert snap is not None and snap.payload.get("session_id") == "child-1" and snap.payload.get("key") == "sub-k"
    await service.shutdown()


@REAL_CHILD
async def test_an_agent_moved_twice_resolves_to_its_latest_job_after_a_restart(tmp_path: Path) -> None:
    """Codex r1 4, verify F2. A run moved, finished, then resumed and moved again has two jobs.
    On ``fb5e7654`` a reader with no in-memory map (a restart) took the oldest record and named the
    first, finished job; through a fresh service's reconciliation too."""

    from tests.agents_ext.test_child_recovery_611 import _restart

    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, channel=_resumable(gate))
    turn = await parked_call(rig)
    row = row_of(rig)
    await wait_for(lambda: row.stream.last_text == "halfway")
    assert rig.jobs_ui.request_detach()
    assert MOVED in _text(await within(turn))
    child_id = row.session_id
    gate.write_text("go")
    first = await settled(rig)
    gate.unlink()
    turn = await parked_call(rig, {"action": "resume", "profile": "scout", "id": child_id, "task": "again"})
    second_row = row_of(rig)
    await wait_for(lambda: second_row.stream.last_text == "halfway")
    assert rig.jobs_ui.request_detach()
    second_id = _text(await within(turn)).split("its job is ", 1)[1].split(")", 1)[0]
    assert second_id != first.job_id
    assert await rig.ext._delegation_jobs.resolve(rig.service, child_id, session=rig.session) == second_id
    assert await DelegationJobs().resolve(rig.service, child_id, session=rig.session) == second_id
    restarted, _jobs, _harness = await _restart(rig.session)
    try:
        assert await DelegationJobs().resolve(restarted, child_id, session=rig.session) == second_id
        assert await DelegationJobs().resolve(restarted, first.job_id, session=rig.session) == first.job_id
    finally:
        await restarted.shutdown()
    gate.write_text("go")
    await finish(rig)


def test_the_hosts_guard_names_each_reason() -> None:
    """Verify F10 M109, M110: every reason ``JobsUi.detach_blocked`` refuses for, one at a time."""

    from aelix_coding_agent.jobs.service import JobService
    from aelix_coding_agent.tui.jobs_ui import JobsUi

    from tests.agents_ext._detach_rig import StandInChrome, StandInHost

    harness = SimpleNamespace(
        abort_pending=False, turns_held=None, disposed=False, pending_notices=0, add_notice_listener=None
    )
    chrome, host = StandInChrome(), StandInHost(harness)
    ui = JobsUi(JobService(), chrome=chrome, runtime_host=host)
    assert ui.detach_blocked() is None
    for target, attr, value, why in (
        (chrome, "interrupt_pending", True, "an interrupt is pending"),
        (harness, "abort_pending", True, "an abort is pending"),
        (host, "stopped", object(), "a session change or a reload is in progress"),
        (host, "in_transition", True, "a session change or a reload is in progress"),
        (harness, "turns_held", object(), "the session's turns are held"),
        (harness, "disposed", True, "the session's turns are held"),
    ):
        before = getattr(target, attr)
        setattr(target, attr, value)
        assert ui.detach_blocked() == why, attr
        setattr(target, attr, before)
    assert ui.detach_blocked() is None
    ui.close()



async def test_a_hosts_handle_whose_context_arrives_at_adopt_still_moves_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host facade's prepared handle has ``job_id`` at once and ``ctx`` from ``adopt`` on.

    Round 4 of the independent verify read ``job.ctx`` right after
    ``prepare_adopted`` to bind the runner's stop path early, and this handle
    raised there: the move failed in the foreground and no job existed. The
    base order — prepare, adopt, give_slot, announce_adopted, then the first
    read of ``ctx``, then run_adopted — is kept for every facade (#855).
    """

    from aelix_coding_agent.jobs.facade import JobsFacade

    trace: list[str] = []

    class Handle:
        def __init__(self, job: Any) -> None:
            self.job = job
            self.job_id = job.job_id
            self.published = False

        @property
        def ctx(self) -> Any:
            trace.append("ctx_after_publish" if self.published else "ctx_before_publish")
            if not self.published:
                raise RuntimeError("the host has not published this context")
            return self.job.ctx

    original_prepare = JobsFacade.prepare_adopted

    def prepare(self: Any, *args: Any, **kwargs: Any) -> Handle:
        trace.append("prepare")
        return Handle(original_prepare(self, *args, **kwargs))

    monkeypatch.setattr(JobsFacade, "prepare_adopted", prepare)

    def forward(name: str) -> Any:
        old = getattr(JobsFacade, name)

        def wrapped(self: Any, handle: Any, *args: Any, **kwargs: Any) -> Any:
            trace.append(name)
            result = old(self, handle.job, *args, **kwargs)
            if name == "adopt":
                handle.published = True
            return result

        return wrapped

    for name in ("adopt", "unadopt", "give_slot", "announce_adopted", "run_adopted", "rescue_adopted"):
        monkeypatch.setattr(JobsFacade, name, forward(name))
    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("finished", gate=gate, partial="halfway"))
    turn = await parked_call(rig)
    row = row_of(rig)
    await wait_for(lambda: row.stream.last_text == "halfway")
    try:
        rig.jobs_ui.request_detach()
        await wait_for(lambda: "prepare" in trace or turn.done())
        gate.write_text("go")
        result = _text(await within(turn))
        if rig.jobs():
            await settled(rig)
        assert MOVED in result and len(rig.jobs()) == 1, (result, trace)
        assert "ctx_before_publish" not in trace, trace
        assert trace[:4] == ["prepare", "adopt", "give_slot", "announce_adopted"], trace
    finally:
        gate.touch()
        await finish(rig)
