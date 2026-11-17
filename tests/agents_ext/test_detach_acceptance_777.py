"""#777 (#611 S3c) PR c2: a job Ctrl+B moved, stopped in the step of its move, and past ``/reload``'s bound.

In-process, through ``_detach_rig``: the real extension, job service and harness,
the real ``JobsUi.request_detach`` (what Ctrl+B calls) and a real
``PrintChannel`` child, a stub that waits for its gate.

* **A stop in the step of the move.** ``request_stop`` is asked inside the
  commit's last step (``JobService.give_slot``), before the job's task has run.
  The task is not cancelled — it runs, ``_drive`` takes the run, and the stop
  takes the run's own path: its child killed and reaped, its row popped, its
  cleanup ended, its settle handed to the job, and ``_finalize`` writes
  ``stopped`` and frees the slot. This is S3a verify round 1's probe
  (``request_stop_same_step``), kept as a test; the rescue proper — the task
  cancelled before its first step — is
  ``test_detach_rescue_611.py::test_a_job_task_cancelled_before_its_first_step_ends_stopped_through_drive``.
* **A moved run that outlasts ``/reload``'s bound.** The reload's stop kills its
  child, and the run's end is held past the bound: its cleanup task's release
  of the child's pipes waits for a release, and the run joins that task last.
  ``/reload`` returns at the bound with the job ``unresolved(reload_timeout)``,
  its slot held and its row still there. The extension's runtime is then
  invalidated, as the reload's rebuild does; released, the run ends, and the
  job ends ``stopped`` with its records in the session it was moved in and its
  slot back. The service's bound is 0.5 s here and its stop grace and exit
  grace 30 s, so the run's own late end ends it; with the product's 10 s, 2 s
  and 45 s the stop's escalation would cancel such a run at about 4 s, before
  the bound — not measured here.

Both fail on ``ab75d07f``, the commit before S3a (#775): there is no
``tests/agents_ext/_detach_rig.py`` and nothing moves a run.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import print_channel
from aelix_agents.child_session import CHILD_SESSION_TYPE
from aelix_agents.jobs_client import KIND
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
    reason="as test_detach_rescue_611: the moved run's real child is measured on win32 by L2a",
)
pytestmark = [REAL_CHILD, pytest.mark.usefixtures("jobs_on", "reap_children")]


async def _job_records(rig: Any, job_id: str) -> list[tuple[str, Any]]:
    entries = await rig.session.get_entries()
    return [
        (e.custom_type, e.data.get("phase") or e.data.get("fact"))
        for e in entries
        if e.type == "custom" and e.data.get("job_id") == job_id
    ]


def _outcome(entries: list[Any]) -> dict[str, Any]:
    return next(e.data for e in entries if e.type == "custom" and e.data.get("fact") == "outcome")


async def test_a_stop_in_the_step_of_the_move_runs_through_drive_and_ends_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("never said", gate=gate, partial="halfway"))
    turn = await parked_call(rig)
    row = row_of(rig)
    first_tasks: list[asyncio.Task[Any]] = []
    drives: list[str] = []
    stopped: list[bool] = []
    real_adopt, real_give, real_drive = JobService.adopt, JobService.give_slot, JobService._drive

    def adopt(self: JobService, job: Any, task: asyncio.Task[Any]) -> None:
        first_tasks.append(task)
        real_adopt(self, job, task)

    def give_slot(self: JobService, job: Any, reservation: Any) -> None:
        real_give(self, job, reservation)
        stopped.append(
            self.request_stop(job.job_id)
        )  # the commit's last step, its task not yet run

    async def drive(self: JobService, job: Any) -> None:
        drives.append(job.job_id)
        await real_drive(self, job)

    monkeypatch.setattr(JobService, "adopt", adopt)
    monkeypatch.setattr(JobService, "give_slot", give_slot)
    monkeypatch.setattr(JobService, "_drive", drive)
    assert rig.jobs_ui.request_detach()
    assert "Moved to the background" in _text(await within(turn))
    snap = await settled(rig)
    assert stopped == [True], "the stop was asked in the commit's step"
    (first,) = first_tasks
    assert not first.cancelled(), "the stop did not cancel the task that had not run: no rescue"
    assert drives == [snap.job_id], "the job's own task drove the run through _drive"
    assert snap.state == "stopped"
    job = rig.service._jobs[snap.job_id]
    assert job.stop_requested_by_service is None and "did not stop when asked" not in snap.brief, (
        "the stop reached the moved run: the service's escalation never had to cancel it"
    )
    assert rig.service.admission.count(KIND) == 0, "_finalize freed the slot"
    runtime = rig.ext.runtime
    assert runtime is not None and row.id not in runtime._children, "the row was popped"
    assert row.proc.returncode is not None, "the child was reaped"
    assert row.cleanup is not None and row.cleanup.ended.is_set(), "the cleanup duty ended"
    tagged = await _job_records(rig, snap.job_id)
    assert (CHILD_SESSION_TYPE, "settle") in tagged, "the run's settle went to the job"
    assert tagged[-1] == (JOB_FACT_TYPE, "outcome")
    outcome = _outcome(await rig.session.get_entries())
    assert outcome["status"] == "stopped" and outcome["client_records"] == "ok"
    assert len(rig.harness.undelivered_notices()) == 1
    await finish(rig)


async def test_a_moved_run_that_outlasts_reloads_bound_is_unresolved_then_ends_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("never said", gate=gate, partial="halfway"))
    # The bound short, and the service's own escalation and liveness cancel later than the
    # release, so what ends the run is its own late end (the product: 10 s, 2 s, 45 s).
    rig.service._cleanup_bound, rig.service._stop_grace, rig.service._exit_grace = 0.5, 30.0, 30.0
    release = asyncio.Event()
    held: list[int | None] = []
    real_release_pipes = print_channel._release_pipes

    async def slow_pipes(proc: Any) -> None:
        held.append(proc.returncode)  # its child is reaped; its pipes close past the bound
        await release.wait()
        await real_release_pipes(proc)

    # A step of the run's cleanup task, which the run joins last: no frame of the chain
    # the commit audits, so the move is admitted as without it.
    monkeypatch.setattr(print_channel, "_release_pipes", slow_pipes)
    turn = await parked_call(rig)
    row = row_of(rig)
    assert rig.jobs_ui.request_detach()
    assert "Moved to the background" in _text(await within(turn))
    (snap,) = rig.jobs()
    job_id = snap.job_id

    try:
        loop = asyncio.get_running_loop()
        begun = loop.time()
        await rig.service.owner_ended(rig.service.owner, "reload")
        took = loop.time() - begun
        assert 0.45 <= took < 2.5, f"/reload returned after {took:.2f} s, not at the bound"
        snap = rig.service.snapshot(job_id)
        assert snap is not None and (snap.state, snap.unresolved_reason) == (
            "unresolved",
            "reload_timeout",
        )
        assert len(held) == 1 and held[0] is not None, (
            "the reload's stop killed and reaped the child"
        )
        assert rig.service.admission.count(KIND) == 1, "its slot is held"
        runtime = rig.ext.runtime
        assert runtime is not None and row.id in runtime._children, (
            "the run has not ended: its row is there"
        )
        rig.runtime.invalidate()  # as the reload's rebuild does to the extension that moved it
    finally:
        release.set()  # whatever happened above: the run is let go
    snap = await settled(rig)
    assert snap.state == "stopped", snap
    assert rig.service.admission.count(KIND) == 0, "the slot came back"
    assert row.id not in runtime._children, "the row was popped"
    assert row.cleanup is not None and row.cleanup.ended.is_set(), "the cleanup duty ended"
    tagged = await _job_records(rig, job_id)
    assert (CHILD_SESSION_TYPE, "settle") in tagged and tagged[-1] == (JOB_FACT_TYPE, "outcome"), (
        tagged
    )
    assert _outcome(await rig.session.get_entries())["status"] == "stopped"
    gate.write_text("go")
    await finish(rig)
