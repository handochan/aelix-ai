"""#123 D1 — a running job tells its session, once, that it may need it (``JobContext.attention``).

The door is kind-agnostic (ADR-0293's 2026-11-18 note): a kind hands the
service a body and a short reason; the service frames ONE notice per job, id
``<job>:attention``, ``details.status == "running"``, and keeps it apart from
the job's final notice. It never changes the job's ``notice``,
``notice_state``, ``owed`` or ``placed`` (I1), so nothing that counts results
counts it (I2); the TUI's wake does (I3); a rebind posts it again only while
the job runs (I4); a second call posts nothing (I5); a job let go with it
still waiting owes nothing for it (I6).

Service level with a runner that starts no process: every platform. The
delegation kind's side is ``tests/agents_ext/test_stuck_notice_123.py``.

Fails on ``88e04d80``: the job's context has no ``attention``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs import service as service_module
from aelix_coding_agent.jobs.contract import ClientOutcome, HelperCleanup, NoticeBody
from aelix_coding_agent.jobs.indicator import indicator_text
from aelix_coding_agent.jobs.notice import NOTICE_MAX_BYTES
from aelix_coding_agent.jobs.records import NOTICE_CUSTOM_TYPE
from aelix_coding_agent.jobs.service import JobService
from aelix_coding_agent.modes import print_mode
from aelix_coding_agent.tui.jobs_ui import JobsUi

from tests.jobs._sleeper import KIND, SleeperAdapter
from tests.jobs.conftest import RecordingModel, fast_service, make_harness, make_session, wait_for
from tests.tui.test_notice_never_touches_dialogs_611 import _Chrome, _Host

REASON = "looks stuck (repeat)"


class _Held:
    """A runner that starts no process: launched at once, running until ``release`` or a stop.

    ``hold_settle`` keeps its barrier open after ``run`` returned (the job is
    still ``running`` there, its context sealed); ``early`` asks the door
    before it reports its launch (the job is ``starting``).
    """

    instances: list[_Held] = []

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.release = asyncio.Event()
        self.settle_gate = asyncio.Event()
        if not params.get("hold_settle"):
            self.settle_gate.set()
        self.early = bool(params.get("early"))
        self.early_answer: bool | None = None
        self.ctx: Any = None
        self.stopped = False
        _Held.instances.append(self)

    async def run(self, ctx: Any) -> ClientOutcome:
        self.ctx = ctx
        if self.early:
            self.early_answer = ctx.attention(NoticeBody("too early"), brief="early")
        ctx.launched()
        ctx.progress({"phase": "held"})
        await self.release.wait()
        status = "stopped" if self.stopped else "done"
        return ClientOutcome(status, f"held {status}", {"output": "the held answer"})

    def request_stop(self, reason: str) -> None:
        self.stopped = True
        self.release.set()

    async def settle(self) -> None:
        await self.settle_gate.wait()

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        return HelperCleanup("none")

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        return []


@pytest.fixture(autouse=True)
def _active(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(activation, "ACTIVE", True)
    _Held.instances.clear()
    yield
    activation.install_service(None)


async def _running(
    tmp_path: Path,
    *,
    notify: bool | None = None,
    adapter_notify: bool = True,
    interval: float = 0.05,
    **params: Any,
) -> tuple[JobService, Any, RecordingModel, str, _Held]:
    model = RecordingModel()
    harness = make_harness(await make_session(tmp_path), model)
    service = fast_service(interval=interval)
    service.register_kind(SleeperAdapter(notify=adapter_notify), _Held)
    await service.bind(harness)
    job_id = await service.start(KIND, title="held", params=params, notify=notify)
    return service, harness, model, job_id, _Held.instances[-1]


def _body(text: str = "last call: grep x") -> NoticeBody:
    return NoticeBody(text, next_action="aelix marked it stuck. It was not stopped.")


def _ids(harness: Any) -> list[str]:
    return [n.notice_id for n in harness.undelivered_notices()]


async def _end(service: JobService, runner: _Held, job_id: str) -> None:
    runner.release.set()
    await wait_for(lambda: service.snapshot(job_id).notice_state != "none")


# === 1. the door ====================================================================


async def test_a_running_job_posts_one_attention_notice_and_a_second_call_posts_nothing(
    tmp_path: Path,
) -> None:
    # No liveness tick in the test's time: only the door can wake the waiter below.
    service, harness, _model, job_id, runner = await _running(tmp_path, interval=60.0)
    changed = asyncio.ensure_future(service.changed())
    await asyncio.sleep(0)
    assert runner.ctx.attention(_body(), brief=REASON) is True
    await asyncio.wait({changed}, timeout=1)
    assert changed.done(), "the service's waiters hear of it (agent(action='wait'), the footer)"
    (notice,) = harness.undelivered_notices()
    assert notice.notice_id == f"{job_id}:attention"
    assert notice.custom_type == NOTICE_CUSTOM_TYPE
    assert dict(notice.details) == {"v": 1, "job_id": job_id, "status": "running"}
    head = notice.body.splitlines()[0]
    assert head.startswith(f'[aelix system notice — not user input] Background sleeper "held" (id {job_id}) ')
    assert "is still running after" in head, head
    assert notice.brief == f'sleeper "held" still running: {REASON}'
    fence_end = notice.body.index("</background-job-result>")
    assert notice.body.index("last call: grep x") < fence_end < notice.body.index("It was not stopped.")
    assert 'job(action="result"' not in notice.body
    assert notice.finalize is None
    # I5: once per job, whatever became of the first.
    assert runner.ctx.attention(_body("again"), brief=REASON) is False
    assert _ids(harness) == [f"{job_id}:attention"]
    await harness.prompt_notices()
    assert runner.ctx.attention(_body("after it was placed"), brief=REASON) is False
    assert harness.undelivered_notices() == []
    await service.shutdown()


async def test_an_id_the_current_harness_already_knows_leaves_no_pending_record(tmp_path: Path) -> None:
    """Fix round 2, C-N (Codex's ``rejected_id``): the inbox refuses an id it
    already knows. The door used to record ``pending`` and answer True, so the
    job read as one whose attention notice waits and the TUI's wake counted a
    notice no inbox held."""

    from aelix_agent_core.harness.inbox import Notice

    service, harness, _model, job_id, runner = await _running(tmp_path)
    harness.post_notice(Notice(f"{job_id}:attention", "foreign", "already known"))
    await harness.prompt_notices()
    assert runner.ctx.attention(_body(), brief=REASON) is False
    assert service._jobs[job_id].attention.state == "dropped"
    assert service.snapshot(job_id).attention_waiting is False
    assert service.has_live_pending() is False
    assert runner.ctx.attention(_body(), brief=REASON) is False, "still once per job"
    await service.shutdown()


async def test_a_job_of_another_session_keeps_its_record_for_its_own(tmp_path: Path) -> None:
    """C-N's control: a job whose session is not the current one is not refused
    by any inbox; its record waits for that session's bind, and the door says True."""

    service, harness, _model, job_id, runner = await _running(tmp_path)
    other = make_harness(await make_session(tmp_path, "other.jsonl", "other"))
    await service.bind(other)
    assert runner.ctx.attention(_body(), brief=REASON) is True
    assert service._jobs[job_id].attention.state == "pending"
    assert other.undelivered_notices() == []
    await service.bind(harness)
    assert _ids(harness) == [f"{job_id}:attention"]
    await service.shutdown()


@pytest.mark.parametrize("gone", ["no harness", "disposed"])
async def test_with_no_live_harness_the_record_waits_for_the_next_bind(tmp_path: Path, gone: str) -> None:
    """C-N's other control: between a harness and the next (none bound, or the
    bound one disposed) nothing refused the id. The record stays ``pending``,
    the door says True, and the next bind posts it."""

    service, harness, _model, job_id, runner = await _running(tmp_path)
    service._harness = None if gone == "no harness" else SimpleNamespace(disposed=True)
    assert runner.ctx.attention(_body(), brief=REASON) is True
    assert service._jobs[job_id].attention.state == "pending"
    again = make_harness(harness.session)
    await service.bind(again)
    assert _ids(again) == [f"{job_id}:attention"]
    await service.shutdown()


async def test_a_body_too_long_is_cut_without_a_result_hint(tmp_path: Path) -> None:
    service, harness, _model, job_id, runner = await _running(tmp_path)
    assert runner.ctx.attention(NoticeBody("z" * 40_000, next_action="next"), brief=REASON)
    (notice,) = harness.undelivered_notices()
    assert len(notice.body.encode("utf-8")) <= NOTICE_MAX_BYTES
    assert "(cut here;" in notice.body and 'job(action="result"' not in notice.body
    await service.shutdown()


# === 2. accounting (I1, I2) ============================================================


async def test_an_attention_notice_placed_while_the_job_runs_is_not_its_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routed through the job's own notice record, its placement would mark the
    running job ``delivered`` (``_job_notice_placed``: nothing owed, no final
    notice yet), and in ``_complete``'s window between ``job.notice = …`` and the
    outcome fact a ``-p`` drain would read the job settled before its result was
    posted. Pinned here: from the terminal state until the final notice is
    posted, ``unsettled()`` stays true."""

    service, harness, model, job_id, runner = await _running(tmp_path)
    assert runner.ctx.attention(_body(), brief=REASON)
    snap = service.snapshot(job_id)
    assert snap.attention_waiting is True
    assert (snap.notice_state, snap.result_waiting, service.unsettled()) == ("none", False, True)
    await harness.prompt_notices()
    assert any("is still running after" in t for t in model.contexts[-1])
    snap = service.snapshot(job_id)
    assert (snap.notice_state, snap.result_waiting, snap.attention_waiting) == ("none", False, False)
    assert service.unsettled() is True
    job = service._jobs[job_id]
    assert (job.notice, job.notice_state, job.owed, job.placed) == (None, "none", set(), set())

    outcome_written = asyncio.Event()
    real_append = service_module.append_fact

    async def held_outcome(session: Any, fact: dict[str, Any]) -> Any:
        if fact.get("fact") == "outcome":
            await outcome_written.wait()
        return await real_append(session, fact)

    monkeypatch.setattr(service_module, "append_fact", held_outcome)
    runner.release.set()
    await wait_for(lambda: job.state == "done" and job.notice is not None)
    # The window: terminal, its final notice framed, the outcome fact not yet written.
    assert service.snapshot(job_id).notice_state == "none"
    assert service.unsettled() is True, "a -p drain must not leave before the result is posted"
    outcome_written.set()
    await wait_for(lambda: service.snapshot(job_id).notice_state == "pending")
    assert service.unsettled() is False
    assert _ids(harness) == [f"{job_id}:final"]
    await harness.prompt_notices()
    assert service.snapshot(job_id).notice_state == "delivered"
    await service.shutdown()


# === 3. the gates ======================================================================


async def test_no_attention_for_a_job_started_without_notify(tmp_path: Path) -> None:
    service, harness, _model, _job_id, runner = await _running(tmp_path, notify=False)
    assert runner.ctx.attention(_body(), brief=REASON) is False
    assert harness.undelivered_notices() == []
    await service.shutdown()


async def test_no_attention_for_a_kind_that_does_not_notify(tmp_path: Path) -> None:
    service, harness, _model, _job_id, runner = await _running(tmp_path, adapter_notify=False)
    assert runner.ctx.attention(_body(), brief=REASON) is False
    assert harness.undelivered_notices() == []
    await service.shutdown()


async def test_no_attention_once_a_stop_is_latched_even_while_the_state_still_reads_running(
    tmp_path: Path,
) -> None:
    service, harness, _model, job_id, runner = await _running(tmp_path)
    job = service._jobs[job_id]
    # The liveness loop's cancel latches the stop and leaves the state as it was.
    service._cancel_runner(job, "its runner did not stop when asked", "stopped")
    assert (job.state, job.stop_latched) == ("running", True)
    assert runner.ctx.attention(_body(), brief=REASON) is False
    assert harness.undelivered_notices() == []
    await wait_for(lambda: service.snapshot(job_id).state == "stopped")
    await service.shutdown()


async def test_no_attention_while_stopping(tmp_path: Path) -> None:
    service, harness, _model, job_id, runner = await _running(tmp_path, hold_settle=True)
    service.request_stop(job_id)
    assert service.snapshot(job_id).state == "stopping"
    assert runner.ctx.attention(_body(), brief=REASON) is False
    runner.settle_gate.set()
    await wait_for(lambda: service.snapshot(job_id).state == "stopped")
    assert _ids(harness) == [f"{job_id}:final"]
    await service.shutdown()


async def test_no_attention_before_the_launch_is_reported(tmp_path: Path) -> None:
    service, harness, _model, _job_id, runner = await _running(tmp_path, early=True)
    assert runner.early_answer is False, "a starting job raises nothing"
    assert harness.undelivered_notices() == []
    assert runner.ctx.attention(_body(), brief=REASON) is True, "once running, it may"
    await service.shutdown()


async def test_no_attention_once_the_run_returned_though_the_barrier_still_reads_running(
    tmp_path: Path,
) -> None:
    service, harness, _model, job_id, runner = await _running(tmp_path, hold_settle=True)
    runner.release.set()
    job = service._jobs[job_id]
    await wait_for(lambda: job.run_returned)
    assert job.state == "running", "in its barrier"
    assert runner.ctx.attention(_body(), brief=REASON) is False
    runner.settle_gate.set()
    await wait_for(lambda: service.snapshot(job_id).state == "done")
    assert runner.ctx.attention(_body(), brief=REASON) is False, "ended"
    assert _ids(harness) == [f"{job_id}:final"]
    await service.shutdown()


# === 5. the wake (I3) and what counts results (I2) =========================================


async def test_the_wake_counts_it_while_it_waits_and_no_result_count_moves(tmp_path: Path) -> None:
    service, harness, _model, job_id, runner = await _running(tmp_path)
    before = (indicator_text(service.snapshots(), extra_waiting=service.extension_waiting()), service.extension_waiting())
    assert service.has_live_pending() is False
    lines: list[str] = []
    chrome = _Chrome()
    ui = JobsUi(service, chrome=chrome, runtime_host=_Host(harness), commit=lines.append)
    ui.parked = True
    assert runner.ctx.attention(_body(), brief=REASON)
    assert service.has_live_pending() is True
    assert len(chrome.queue) == 1, "an idle, parked TUI is woken for it (G7)"
    after = (indicator_text(service.snapshots(), extra_waiting=service.extension_waiting()), service.extension_waiting())
    assert after == before == ("⚙ 1 running", 0)
    assert ui.take()
    await harness.prompt_notices()
    assert service.has_live_pending() is False
    # 9. the transcript line says what it is: a notice, not a result.
    assert lines == [f'◆ background notice: sleeper "held" still running: {REASON}']
    await _end(service, runner, job_id)
    await harness.prompt_notices()
    assert lines[1].startswith('◆ background result: sleeper "held" done'), lines
    ui.close()
    await service.shutdown()


async def test_jobs_send_counts_an_attention_notice_apart_from_the_results(tmp_path: Path) -> None:
    """Fix round 2, L2: with a result and an attention notice waiting, the
    TUI's ``/jobs send`` said "Sending 2 waiting results"."""

    from aelix_coding_agent.jobs.commands import JobsCommand

    service, harness, _model, job_id, runner = await _running(tmp_path)
    ended_id = await service.start(KIND, title="second")
    _Held.instances[-1].release.set()
    await wait_for(lambda: service.snapshot(ended_id).notice_state == "pending")
    assert runner.ctx.attention(_body(), brief=REASON)
    ui = JobsUi(service, chrome=_Chrome(), runtime_host=_Host(harness))
    service.send_hook = ui.send_now
    assert harness.pending_notices == 2
    assert await JobsCommand(service)("send") == "Sending 1 waiting result and 1 notice to the model."
    ui.close()
    await service.shutdown()


async def test_jobs_send_does_not_count_an_attention_notice_the_harness_holds(tmp_path: Path) -> None:
    """L2: a held notice (a placement conflict) is not pending, so ``/jobs send``
    does not count it among what it sends."""

    from aelix_agent_core.harness.inbox import notice_entry_id
    from aelix_coding_agent.jobs.commands import JobsCommand

    service, harness, _model, job_id, runner = await _running(tmp_path)
    await harness.session.place_custom_entry(notice_entry_id(f"{job_id}:attention"), "foreign", {"taken": True})
    assert runner.ctx.attention(_body(), brief=REASON)
    await harness.prompt_notices()
    assert f"{job_id}:attention" in harness.held_notices(), "the premise: held"
    ended_id = await service.start(KIND, title="second")
    _Held.instances[-1].release.set()
    await wait_for(lambda: service.snapshot(ended_id).notice_state == "pending")
    ui = JobsUi(service, chrome=_Chrome(), runtime_host=_Host(harness))
    service.send_hook = ui.send_now
    assert harness.pending_notices == 1
    assert await JobsCommand(service)("send") == "Sending 1 waiting result to the model."
    ui.close()
    await service.shutdown()


# === 6. rebind (I4) ========================================================================


async def test_a_rebind_posts_it_again_while_the_job_runs(tmp_path: Path) -> None:
    service, harness, _model, job_id, runner = await _running(tmp_path)
    assert runner.ctx.attention(_body(), brief=REASON)
    again = make_harness(harness.session)
    await service.bind(again)
    assert _ids(again) == [f"{job_id}:attention"]
    assert service.has_live_pending() is True
    await again.prompt_notices()
    assert service.has_live_pending() is False
    assert service.snapshot(job_id).notice_state == "none"
    await service.shutdown()


async def test_a_rebind_after_it_was_placed_posts_nothing(tmp_path: Path) -> None:
    """Fix round 2, C-M 1 (guard): only a ``pending`` record is posted again."""

    service, harness, _model, job_id, runner = await _running(tmp_path)
    assert runner.ctx.attention(_body(), brief=REASON)
    await harness.prompt_notices()
    assert service._jobs[job_id].attention.state == "delivered"
    again = make_harness(harness.session)
    await service.bind(again)
    assert again.undelivered_notices() == [], "placed once: not posted again"
    assert service._jobs[job_id].attention.state == "delivered"
    await service.shutdown()


@pytest.mark.parametrize("why", ["run returned", "stop latched"])
async def test_a_rebind_while_the_job_still_reads_running_but_may_not_attend_drops_it(
    tmp_path: Path, why: str
) -> None:
    """Fix round 2, C-M 2 (guard): the state alone does not say it. A job whose
    run returned (its context sealed, its barrier open) or whose stop the
    liveness loop latched still reads ``running``; a rebind then drops its
    pending attention notice instead of posting it again."""

    service, harness, _model, job_id, runner = await _running(tmp_path, hold_settle=True)
    job = service._jobs[job_id]
    assert runner.ctx.attention(_body(), brief=REASON)
    if why == "run returned":
        runner.release.set()
        await wait_for(lambda: job.run_returned)
    else:
        service._cancel_runner(job, "its runner did not stop when asked", "stopped")
        assert job.stop_latched
    assert job.state == "running", "the premise: it still reads running"
    again = make_harness(harness.session)
    await service.bind(again)
    assert f"{job_id}:attention" not in _ids(again)
    assert job.attention.state == "dropped"
    runner.settle_gate.set()
    await wait_for(lambda: service.snapshot(job_id).state in ("done", "stopped"))
    await service.shutdown()


async def test_a_rebind_after_the_job_ended_drops_it(tmp_path: Path) -> None:
    service, harness, _model, job_id, runner = await _running(tmp_path)
    assert runner.ctx.attention(_body(), brief=REASON)
    await _end(service, runner, job_id)
    assert _ids(harness) == [f"{job_id}:attention", f"{job_id}:final"]
    again = make_harness(harness.session)
    await service.bind(again)
    assert _ids(again) == [f"{job_id}:final"], "only its result is posted again"
    await again.prompt_notices()
    assert service.has_live_pending() is False, "the dropped record is not counted"
    assert service.snapshot(job_id).notice_state == "delivered"
    await service.shutdown()


# === I6: nothing counts a waiting attention notice once the job is over ======================


async def test_a_job_whose_attention_notice_still_waits_owes_nothing_for_it(tmp_path: Path) -> None:
    service, harness, _model, job_id, runner = await _running(tmp_path)
    assert runner.ctx.attention(_body(), brief=REASON)
    job = service._jobs[job_id]
    await _end(service, runner, job_id)
    final = next(n for n in harness.undelivered_notices() if n.notice_id.endswith(":final"))
    # The final notice placed and the attention one still waiting (as a held one
    # would): the job is delivered and owes nothing, so #769 may let it go.
    await wait_for(lambda: job.task is not None and job.task.done())
    service._job_notice_placed(job, final)
    assert job.owed == set() and job.notice_state == "delivered"
    assert service_module._owes_nothing(job) is True
    assert f"{job_id}:attention" in _ids(harness)
    # An ended job's snapshot reads as one with no attention notice: the same as
    # its records read back, which #769 compares before it lets the job go.
    assert service.snapshot(job_id).attention_waiting is False
    await service.shutdown()


# === 7. the -p drain and 8. its interrupt flush ===============================================


async def _print_runtime(tmp_path: Path, model: RecordingModel) -> tuple[Any, JobService]:
    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.runtime import AgentSessionRuntime
    from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
    from aelix_ai.streaming import Model

    session = await make_session(tmp_path)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"), stream_fn=model, session=session, notice_inbox=True
        )
    )

    async def factory(_s: Any) -> AgentHarness:
        return harness

    runtime = AgentSessionRuntime(harness, factory, repo=JsonlSessionRepo(fs=LocalFileSystem()), fs=LocalFileSystem())
    service = fast_service()
    service.register_kind(SleeperAdapter(), _Held)
    await service.bind(harness)
    return runtime, service


async def test_the_print_drain_runs_a_notice_turn_for_it_and_goes_on_waiting_for_the_job(
    tmp_path: Path,
) -> None:
    model = RecordingModel("fine")
    runtime, service = await _print_runtime(tmp_path, model)
    job_id = await service.start(KIND, title="held")
    runner = _Held.instances[-1]
    run = asyncio.ensure_future(
        print_mode.run_print_mode(runtime, mode="text", messages=[], initial_message="go", jobs=service)
    )
    await wait_for(lambda: len(model.contexts) == 1)
    assert runner.ctx.attention(_body(), brief=REASON)
    await wait_for(lambda: len(model.contexts) == 2)
    assert any("is still running after" in t for t in model.contexts[1])
    await asyncio.sleep(0.3)
    assert not run.done(), "the drain goes on waiting for the job (I2: it is still unsettled)"
    runner.release.set()
    code = await asyncio.wait_for(run, 10)
    assert code == 0
    assert len(model.contexts) == 3 and any("finished after" in t for t in model.contexts[2])
    assert service.snapshot(job_id).notice_state == "delivered"
    await service.shutdown()


@pytest.mark.parametrize("notify", [True, False], ids=["with-notice", "notify-off"])
async def test_the_print_drain_waits_while_complete_writes_before_the_final_notice_is_framed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notify: bool
) -> None:
    """Fix round 2, C-R1: ``_complete`` makes the job terminal, then writes its
    client records and its cleanup fact, and only then frames the final
    notice. In that window ``job.notice`` is still ``None``, so a drain that
    read ``unsettled()`` there left before the result was posted (the
    attention notice's turn can end inside it). Held here: the cleanup fact,
    after the terminal assignment. A job with no notice still settles once
    ``_complete`` is done."""

    model = RecordingModel("fine")
    runtime, service = await _print_runtime(tmp_path, model)
    job_id = await service.start(KIND, title="held", notify=notify)
    runner = _Held.instances[-1]
    job = service._jobs[job_id]
    held, release = asyncio.Event(), asyncio.Event()
    real_append = service_module.append_fact

    async def held_cleanup(session: Any, fact: dict[str, Any]) -> Any:
        if fact.get("fact") == "cleanup" and fact.get("job_id") == job_id:
            held.set()
            await release.wait()
        return await real_append(session, fact)

    monkeypatch.setattr(service_module, "append_fact", held_cleanup)
    runner.release.set()
    await asyncio.wait_for(held.wait(), 5)
    assert (job.state, job.notice, job.outbox) == ("done", None, None), "the window"
    drain = asyncio.ensure_future(
        print_mode._drain_background(runtime, service, print_mode._FirstFailure([]), stop=lambda: False)
    )
    await asyncio.wait({drain}, timeout=0.3)
    assert not drain.done(), "the drain left before the final notice was framed"
    assert service.unsettled() is True
    release.set()
    await asyncio.wait_for(drain, 10)
    assert service.unsettled() is False
    if notify:
        assert len(model.contexts) == 1 and any("finished after" in t for t in model.contexts[0])
        assert service.snapshot(job_id).notice_state == "delivered"
    else:
        assert model.contexts == [] and service.snapshot(job_id).notice_state == "none"
    await service.shutdown()


async def test_the_print_drain_waits_while_an_unresolved_job_s_notice_is_not_posted_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same window in ``_become_unresolved`` (fix round 2's sweep of C-R1):
    the state reads ``unresolved`` while its supervision fact is written and
    before its notice is posted. An unresolved job is not waited on, but a
    drain that left there never gave the model its notice."""

    model = RecordingModel("fine")
    runtime, service = await _print_runtime(tmp_path, model)
    job_id = await service.start(KIND, title="held")
    runner = _Held.instances[-1]
    job = service._jobs[job_id]

    async def failed_settle() -> None:
        raise RuntimeError("settle failed")

    runner.settle = failed_settle  # type: ignore[method-assign]
    held, release = asyncio.Event(), asyncio.Event()
    real_append = service_module.append_fact

    async def held_supervision(session: Any, fact: dict[str, Any]) -> Any:
        if fact.get("fact") == "supervision" and fact.get("job_id") == job_id:
            held.set()
            await release.wait()
        return await real_append(session, fact)

    monkeypatch.setattr(service_module, "append_fact", held_supervision)
    runner.release.set()
    await asyncio.wait_for(held.wait(), 5)
    assert (job.state, job.notice) == ("unresolved", None), "the window"
    drain = asyncio.ensure_future(
        print_mode._drain_background(runtime, service, print_mode._FirstFailure([]), stop=lambda: False)
    )
    await asyncio.wait({drain}, timeout=0.3)
    assert not drain.done(), "the drain left before the unresolved notice was posted"
    release.set()
    await asyncio.wait_for(drain, 10)
    assert len(model.contexts) == 1 and any("could not be confirmed stopped" in t for t in model.contexts[0])
    await service.shutdown()


async def test_a_job_that_becomes_unresolved_wakes_the_services_waiters_once_its_notice_is_posted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 2 (mutant S07): ``_become_unresolved``'s change notification
    moved into its ``finally``. No liveness tick in the test's time, and the
    waiter starts inside the window, so only that notification can wake it."""

    service, harness, _model, job_id, runner = await _running(tmp_path, interval=60.0)
    job = service._jobs[job_id]

    async def failed_settle() -> None:
        raise RuntimeError("settle failed")

    runner.settle = failed_settle  # type: ignore[method-assign]
    held, release = asyncio.Event(), asyncio.Event()
    real_append = service_module.append_fact

    async def held_supervision(session: Any, fact: dict[str, Any]) -> Any:
        if fact.get("fact") == "supervision" and fact.get("job_id") == job_id:
            held.set()
            await release.wait()
        return await real_append(session, fact)

    monkeypatch.setattr(service_module, "append_fact", held_supervision)
    runner.release.set()
    await asyncio.wait_for(held.wait(), 5)
    changed = asyncio.ensure_future(service.changed())
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait({changed}, timeout=1)
    assert changed.done(), "a waiter hears of the unresolved job and its notice"
    assert (job.state, _ids(harness)) == ("unresolved", [f"{job_id}:unresolved"])
    await service.shutdown()


@pytest.mark.parametrize("mode", ["text", "json"])
async def test_the_interrupt_flush_leaves_an_attention_notice_out(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    service, harness, _model, job_id, runner = await _running(tmp_path)
    assert runner.ctx.attention(_body(), brief=REASON)
    written: list[str] = []
    await print_mode._interrupt_flush(
        SimpleNamespace(harness=harness), service, mode, write_stdout=written.append
    )
    assert service.snapshot(job_id).state == "stopped"
    assert _ids(harness) == [f"{job_id}:attention", f"{job_id}:final"], "both still undelivered"
    err = capsys.readouterr().err
    if mode == "text":
        assert "interrupted — 1 background result was not given to the model" in err
        assert 'sleeper "held" stopped' in err and "still running" not in err
    else:
        events = [json.loads(line) for line in written]
        assert [e["notice_id"] for e in events] == [f"{job_id}:final"]
    await service.shutdown()
