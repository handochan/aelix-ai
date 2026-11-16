"""#775 (#611 S3a) — a moved run's job task cancelled before its first step is rescued (Codex M2, r4 errata E2).

The commit creates the job's task and returns the detach text in one step; the
task has not run yet. If it is cancelled now — a service stop, ``shutdown``'s
re-cancel, a loop teardown — the moved coroutine would never be resumed: its
child, its row, its cleanup and its records would be left. The task's
done-callback rescues it: a new task runs the job service's own ``_drive`` with
the runner's first move a cancel thrown into the moved run, so the run takes
ITS OWN cancel path (``_stream_and_reap``'s eager abort, ``_run``'s settle and
final handed to the job, the row popped, the cleanup duty ended), and
``_finalize`` runs the barrier, writes the outcome ``stopped`` and frees the slot.

Real ``PrintChannel`` child, real job service; the cancel is delivered in the
commit's own step (the service's ``adopt`` is wrapped to cancel the task it is
handed). Fails on ``c91cdc7b``: nothing moves a run.
"""

from __future__ import annotations

import asyncio
import gc
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import detach as dt
from aelix_agents.child_session import CHILD_SESSION_TYPE
from aelix_agents.jobs_client import KIND, AdoptedRunner
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE
from aelix_coding_agent.jobs.service import JobService

from tests.agents_ext._detach_rig import (  # noqa: F401 — reap_children is the fixture
    finish,
    parked_call,
    reap_children,
    row_of,
    script,
    settled,
    tui_rig,
    within,
)
from tests.agents_ext.test_agent_background_611 import jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_tool_and_security import _text

REAL_CHILD = pytest.mark.skipif(
    sys.platform == "win32",
    reason="as test_agent_background_611: the gated rig's real child is proven on win32 by #611 S3c",
)
pytestmark = pytest.mark.usefixtures("jobs_on", "reap_children")


@REAL_CHILD
async def test_a_job_task_cancelled_before_its_first_step_ends_stopped_through_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("never said", gate=gate, partial="halfway"))
    turn = await parked_call(rig)
    row = row_of(rig)
    boxes: list[Any] = []
    drives: list[str] = []
    real_adopt, real_drive = JobService.adopt, JobService._drive

    def adopt(self: JobService, job: Any, task: asyncio.Task[Any]) -> None:
        real_adopt(self, job, task)
        boxes.append(task)
        task.cancel()  # in the commit's own step: the task never runs

    async def drive(self: JobService, job: Any) -> None:
        drives.append(job.job_id)
        await real_drive(self, job)

    policies: list[str] = []
    real_cleanup = AdoptedRunner.helper_cleanup

    async def helper_cleanup(self: AdoptedRunner, receipt: Any) -> Any:
        policies.append(receipt["policy"])
        return await real_cleanup(self, receipt)

    monkeypatch.setattr(JobService, "adopt", adopt)
    monkeypatch.setattr(JobService, "_drive", drive)
    monkeypatch.setattr(AdoptedRunner, "helper_cleanup", helper_cleanup)
    assert rig.jobs_ui.request_detach()
    assert "Moved to the background" in _text(await within(turn))
    snap = await settled(rig)
    (cancelled,) = boxes
    assert cancelled.cancelled(), "the job's first task never ran"
    assert drives == [snap.job_id], "the rescue went through the service's _drive"
    assert snap.state == "stopped"
    assert policies == ["stop"], "a rescued job is latched stopped: its barrier takes the stop policy"
    assert rig.service.admission.count(KIND) == 0, "_finalize freed the slot"
    runtime = rig.ext.runtime
    assert runtime is not None and row.id not in runtime._children, "the row was popped"
    assert row.proc.returncode is not None, "the child was reaped"
    assert row.cleanup is not None and row.cleanup.ended.is_set(), "the cleanup duty ended"
    await runtime.drain_cleanups()
    assert row.cleanup.task is not None and row.cleanup.task.done()
    entries = await rig.session.get_entries()
    tagged = [
        (e.custom_type, e.data.get("phase") or e.data.get("fact"))
        for e in entries
        if e.type == "custom" and e.data.get("job_id") == snap.job_id
    ]
    assert (CHILD_SESSION_TYPE, "settle") in tagged, "the cancel path's settle went to the job"
    assert tagged[-1] == (JOB_FACT_TYPE, "outcome")
    outcome = next(e.data for e in entries if e.type == "custom" and e.data.get("fact") == "outcome")
    assert outcome["status"] == "stopped" and outcome["client_records"] == "ok"
    assert len(rig.harness.undelivered_notices()) == 1
    await finish(rig)


@REAL_CHILD
async def test_a_start_step_that_fails_before_the_runner_drives_is_rescued_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r1 2, verify F3. The job's first task raises in ``run_adopted`` before ``_drive``;
    the rescue runs ``_drive`` anyway (its start steps best effort), and the job ends ``stopped``.

    On ``fb5e7654`` ``_entry`` marked the run entered before ``run_adopted``, so nothing rescued
    it: the job stayed ``running`` with its child, row and slot, and this test's wait failed.
    """

    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("never said", gate=gate, partial="halfway"))
    turn = await parked_call(rig)
    row = row_of(rig)

    def broken(self: JobService) -> None:
        raise RuntimeError("liveness")

    monkeypatch.setattr(JobService, "_ensure_liveness", broken)  # in the first task and the rescue's
    assert rig.jobs_ui.request_detach()
    assert "Moved to the background" in _text(await within(turn))
    snap = await settled(rig)
    assert snap.state == "stopped"
    assert rig.service.admission.count(KIND) == 0, "_finalize freed the slot"
    runtime = rig.ext.runtime
    assert runtime is not None and row.id not in runtime._children
    assert row.proc.returncode is not None and row.cleanup is not None and row.cleanup.ended.is_set()
    entries = await rig.session.get_entries()
    facts = [e.data["fact"] for e in entries if e.type == "custom" and e.custom_type == JOB_FACT_TYPE]
    assert facts.count("start") == 1, "the facts task the first task made is not made twice"
    monkeypatch.undo()
    gate.write_text("go")
    await finish(rig)


async def test_the_rescue_stands_down_once_the_runner_drives(caplog: pytest.LogCaptureFixture) -> None:
    """``Box.entered`` is the runner's to set (in the step it drives the run), never ``_entry``'s."""

    loop = asyncio.get_running_loop()
    rescued: list[bool] = []
    box = dt.Box(inner=None, published=True)

    async def start() -> None:
        raise RuntimeError("a start step")

    task = loop.create_task(dt._entry(box, start))
    task.add_done_callback(lambda t: dt._rescue(t, box=box, rescue=lambda: rescued.append(True)))
    await asyncio.wait({task})
    await asyncio.sleep(0)
    assert rescued == [True] and box.entered is False
    del task  # the rescue answered for its exception: none is reported as never retrieved
    gc.collect()
    assert "never retrieved" not in caplog.text
    entered = dt.Box(inner=None, published=True, entered=True)
    done = loop.create_task(asyncio.sleep(0))
    await done
    dt._rescue(done, box=entered, rescue=lambda: rescued.append(False))
    assert rescued == [True]


@REAL_CHILD
async def test_a_facts_task_that_fails_once_is_made_again_by_the_rescue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify round 2 F3 (S06, S07) and N5: the first task cannot create its facts task; the
    rescue makes it and starts the liveness loop, the job's facts are whole, and the first
    failure is logged once though the retry succeeded."""

    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("never said", gate=gate, partial="halfway"))
    turn = await parked_call(rig)
    loop = asyncio.get_running_loop()
    real_create = loop.create_task
    failed: list[bool] = []
    liveness: list[bool] = []
    real_liveness = JobService._ensure_liveness

    def create_task(coro: Any, **kwargs: Any) -> Any:
        if getattr(coro, "__qualname__", "") == "JobService._adopted_facts" and not failed:
            failed.append(True)
            coro.close()
            raise RuntimeError("the facts task could not be created once")
        return real_create(coro, **kwargs)

    def ensure_liveness(self: JobService) -> None:
        liveness.append(True)
        real_liveness(self)

    monkeypatch.setattr(loop, "create_task", create_task)
    monkeypatch.setattr(JobService, "_ensure_liveness", ensure_liveness)
    assert rig.jobs_ui.request_detach()
    assert "Moved to the background" in _text(await within(turn))
    snap = await settled(rig)
    monkeypatch.undo()
    assert failed == [True] and snap.state == "stopped"
    assert liveness, "the rescue started the liveness loop the first task never reached"
    entries = await rig.session.get_entries()
    facts = [e.data["fact"] for e in entries if e.type == "custom" and e.custom_type == JOB_FACT_TYPE]
    assert facts == ["start", "process", "cleanup", "outcome"]
    assert caplog.text.count("ended before its runner took the run") == 1
    assert "the facts task could not be created once" in caplog.text
    gate.write_text("go")
    await finish(rig)

