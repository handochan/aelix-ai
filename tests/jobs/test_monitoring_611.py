"""Monitoring from the main session: liveness, quiet, the indicator, ``/jobs`` and the job tool (#722).

Owner, 2026-11-07: "코어 메인세션에서 백그라운드 잡을 모니터링하는게 혹은 확인하는 게
더 중요". Fails on main ``a385c64a``: none of it exists. Naive: a job whose
process died while its runner hangs stays ``running`` forever (no liveness), or
a quiet job is stopped as if it had timed out (#604 decided there is no
timeout), or the model and the user see different tables.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs.commands import FOOTER, JobsCommand, list_rows
from aelix_coding_agent.jobs.contract import ClientOutcome, JobSnapshot
from aelix_coding_agent.jobs.indicator import indicator_text
from aelix_coding_agent.jobs.tool import create_job_stop_tool, create_job_tool

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_dead_root_with_stuck_runner_reaches_failed_or_unresolved(tmp_path: Path) -> None:
    class Stuck(_sleeper.Sleeper):
        async def run(self, ctx: Any) -> ClientOutcome:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "pass", **_sleeper._spawn_kwargs()
            )
            ctx.register_process(self.proc, pgid=self.proc.pid if sys.platform != "win32" else None)
            ctx.launched()
            await asyncio.Event().wait()  # never reports
            raise AssertionError("unreachable")

    service = fast_service(exit_grace=0.3, interval=0.05)
    service.register_kind(_sleeper.SleeperAdapter(), Stuck)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="stuck")
    await wait_for(lambda: service.snapshot(job_id).state in ("failed", "unresolved"), timeout=10)  # type: ignore[union-attr]
    snap = service.snapshot(job_id)
    assert snap is not None and snap.state == "failed"
    assert "its process ended but its runner did not report" in snap.brief
    await wait_for(lambda: harness.pending_notices == 1)
    await service.shutdown()


async def test_quiet_flag_never_kills_or_notifies(tmp_path: Path) -> None:
    clock = _Clock()
    service = fast_service(clock=clock, quiet_after=600.0, interval=3600)
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="long", params={"seconds": 30})
    clock.now += 601
    await service.tick()
    snap = service.snapshot(job_id)
    assert snap is not None and snap.quiet and snap.state == "running"
    runner = _sleeper.Sleeper.instances[-1]
    assert runner.proc is not None and runner.proc.returncode is None
    assert "request_stop" not in runner.calls
    assert harness.pending_notices == 0
    assert indicator_text(service.snapshots()) == "⚙ 1 running (quiet 10m)"
    runner.ctx.progress({"phase": "still here"})
    assert not service.snapshot(job_id).quiet  # type: ignore[union-attr]
    await service.stop(job_id)
    await service.shutdown()


def _snap(state: str, *, waiting: bool = False, quiet: bool = False, ago: float = 0.0) -> JobSnapshot:
    return JobSnapshot(
        job_id="x" * 16,
        kind="k",
        label="l",
        title="t",
        state=state,  # type: ignore[arg-type]
        owner="s",
        created=0.0,
        elapsed=1.0,
        last_activity_ago=ago,
        quiet=quiet,
        result_waiting=waiting,
        notice_state="pending" if waiting else "none",
    )


def test_indicator_text_and_narrow_widths() -> None:
    assert indicator_text([]) is None
    assert indicator_text([_snap("done")]) is None
    assert (
        indicator_text([_snap("running"), _snap("running"), _snap("done", waiting=True)])
        == "⚙ 2 running · 1 result waiting"
    )
    assert indicator_text([_snap("running", quiet=True, ago=12 * 60 + 5)]) == "⚙ 1 running (quiet 12m)"
    assert indicator_text([_snap("unresolved")]) == "⚙ 1 unresolved"
    assert indicator_text([_snap("done", waiting=True)] * 3) == "⚙ 3 results waiting"
    many = [_snap("running", quiet=True, ago=700), _snap("running"), _snap("done", waiting=True)]
    assert indicator_text(many, width=50) == "⚙ 2 running (quiet 11m) · 1 result waiting"
    assert indicator_text(many, width=30) == "⚙ 2 running · 1 result waiting"
    assert indicator_text(many, width=20) == "⚙ 2 · 1 waiting"


async def test_jobs_layout(tmp_path: Path) -> None:
    service = fast_service()
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    command = JobsCommand(service)
    assert await command("") == "No background jobs in this session."
    live = await service.start(_sleeper.KIND, title="worker: nap", params={"seconds": 30})
    done = await service.start(_sleeper.KIND, title="reviewer: nap", params={"seconds": 0.01})
    await wait_for(lambda: service.snapshot(done).state == "done")  # type: ignore[union-attr]
    text = await command("")
    lines = text.splitlines()
    assert lines[0].split() == ["ID", "KIND", "STATE", "AGE", "ACTIVITY", "TITLE"]
    assert live[:6] + "…" in lines[1] and "running" in lines[1] and "zz worker: nap" in lines[1]
    assert done[:6] + "…" in lines[2] and "done ◆" in lines[2] and "result waiting" in lines[2]
    assert lines[-1].strip() == FOOTER
    detail = await command(live[:8])
    assert "kind      test:sleeper" in detail and f"/jobs stop {live[:6]}…" in detail
    assert "No results" not in await command("send")
    await service.stop(live)
    await service.shutdown()


async def test_jobs_stop_and_result_any_kind(tmp_path: Path) -> None:
    service = fast_service()
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    command = JobsCommand(service)
    live = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    said = await command(f"stop {live[:5]}")
    assert said == f"Stopped {live[:6]}…: stopped"
    done = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    await wait_for(lambda: service.snapshot(done).state == "done")  # type: ignore[union-attr]
    assert "slept 0.01" in await command(f"result {done}")
    assert "no job" in await command("result zzzz")
    assert await command(f"stop {done}") == f"{done[:6]}… already ended: done"
    await service.shutdown()


async def test_job_tool_list_matches_jobs(tmp_path: Path) -> None:
    service = fast_service()
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    tool = create_job_tool(service)
    empty = await tool.execute({"action": "list"}, None)
    assert empty.content[0].text == "No background jobs in this session."
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    listed = await tool.execute({"action": "list"}, None)
    assert listed.content[0].text == "\n".join(list_rows(service, width=160))
    status = await tool.execute({"action": "status", "id": job_id}, None)
    assert "running" in status.content[0].text
    missing = await tool.execute({"action": "status"}, None)
    assert missing.is_error
    refused = await tool.execute({"action": "stop", "id": job_id}, None)
    assert refused.is_error and "job_stop" in refused.content[0].text, "job only reads"
    stopped = await create_job_stop_tool(service).execute({"id": job_id}, None)
    assert stopped.content[0].text == f"{job_id}: stopped"
    result = await tool.execute({"action": "result", "id": job_id}, None)
    assert not result.is_error
    await service.shutdown()


async def test_a_kind_that_routes_control_elsewhere_gets_its_hint(tmp_path: Path) -> None:
    from dataclasses import replace

    service = fast_service()
    adapter = _sleeper.SleeperAdapter()
    adapter.capabilities = replace(
        adapter.capabilities, job_tool_control=False, control_hint="use sleeper(action=stop)"
    )
    service.register_kind(adapter, _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    result = await create_job_stop_tool(service).execute({"id": job_id}, None)
    assert result.is_error and result.content[0].text == "use sleeper(action=stop)"
    await service.stop(job_id)
    await service.shutdown()


async def test_a_dead_root_gets_the_whole_exit_settle_grace(tmp_path: Path) -> None:
    """Verify V16: the runner of a job whose root is gone has ``EXIT_SETTLE_GRACE``
    (45 s, above delegation's 30 s post-EOF bound) to report before the service
    stops it; not a tick less."""

    from aelix_coding_agent.jobs.service import EXIT_SETTLE_GRACE

    assert EXIT_SETTLE_GRACE == 45.0
    clock = _Clock()

    class Stuck(_sleeper.Sleeper):
        async def run(self, ctx: Any) -> ClientOutcome:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "pass", **_sleeper._spawn_kwargs()
            )
            ctx.register_process(self.proc, pgid=self.proc.pid if sys.platform != "win32" else None)
            ctx.launched()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    service = fast_service(clock=clock, interval=3600, exit_grace=EXIT_SETTLE_GRACE)
    service.register_kind(_sleeper.SleeperAdapter(), Stuck)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(_sleeper.KIND, title="stuck")
    runner = Stuck.instances[-1]
    assert runner.proc is not None
    await runner.proc.wait()
    await service.tick()  # the root is seen gone now
    clock.now += EXIT_SETTLE_GRACE - 0.5
    await service.tick()
    job = service._jobs[job_id]
    assert job.task is not None and not job.task.done(), "still inside the grace"
    assert job.stop_requested_by_service is None
    clock.now += 1.0
    await service.tick()
    assert job.stop_requested_by_service is not None
    await wait_for(lambda: service.snapshot(job_id).state in ("failed", "unresolved"))  # type: ignore[union-attr]
    await service.shutdown()
