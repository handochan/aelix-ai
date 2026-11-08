"""The runner contract the service enforces (#722, Codex r3 #13, #14).

Fails on main ``a385c64a``: no job service. Naive: an open ``register_process``
lets a runner add a process after the barrier passed (nothing would ever stop
it), a registration after the stop latch lives until the next poll, and a
failed launch turns into a ``failed`` notice the model then reads twice (once
as the tool's error, once as a notice).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs.contract import ClientOutcome
from aelix_coding_agent.jobs.records import fold
from aelix_coding_agent.jobs.service import JobLaunchFailed, JobStartRefused

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, reopen, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")


async def _bound(tmp_path: Path) -> tuple[Any, Any, Any]:
    service = fast_service()
    _sleeper.register(service)
    session = await make_session(tmp_path)
    harness = make_harness(session)
    await service.bind(harness)
    return service, harness, session


async def test_registration_sealed_after_run(tmp_path: Path) -> None:
    service, _harness, _session = await _bound(tmp_path)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.05})
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    ctx = _sleeper.Sleeper.instances[-1].ctx
    with pytest.raises(RuntimeError, match="sealed"):
        ctx.register_process(12345)
    with pytest.raises(RuntimeError, match="sealed"):
        ctx.register_directory(str(tmp_path))
    await service.shutdown()


async def test_registration_after_stop_latch_is_killed(tmp_path: Path) -> None:
    service, _harness, _session = await _bound(tmp_path)
    job_id = await service.start(
        _sleeper.KIND, title="nap", params={"seconds": 30, "ignore_stop": True}
    )
    runner = _sleeper.Sleeper.instances[-1]
    service.request_stop(job_id)
    late = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)", **_sleeper._spawn_kwargs()
    )
    runner.ctx.register_process(late, pgid=late.pid if sys.platform != "win32" else None)
    await asyncio.wait_for(late.wait(), 5)
    assert late.returncode is not None
    runner.stopped.set()
    await wait_for(lambda: service.snapshot(job_id).state == "stopped")  # type: ignore[union-attr]
    await service.shutdown()


async def test_launch_failure_is_the_tool_result(tmp_path: Path) -> None:
    service, harness, session = await _bound(tmp_path)
    with pytest.raises(JobLaunchFailed, match="no such program"):
        await service.start(_sleeper.KIND, title="nap", params={"launch": "fail"})
    assert harness.pending_notices == 0
    assert service.admission.count(_sleeper.KIND) == 0
    jobs = fold(await session.get_entries())
    (job,) = jobs.values()
    assert job.outcome is not None and job.outcome["status"] == "failed"
    assert "notice" not in job.outcome
    await service.shutdown()


async def test_a_run_that_returns_before_reporting_its_launch_is_a_failed_launch(
    tmp_path: Path,
) -> None:
    service, harness, _session = await _bound(tmp_path)

    class Quiet(_sleeper.Sleeper):
        async def run(self, ctx: Any) -> ClientOutcome:
            return ClientOutcome("done", "never launched")

    service.register_kind(_sleeper.SleeperAdapter(), Quiet)
    with pytest.raises(JobLaunchFailed, match="before it reported its launch"):
        await service.start(_sleeper.KIND, title="nap")
    assert harness.pending_notices == 0
    await service.shutdown()


async def test_an_unknown_kind_or_a_start_fact_that_cannot_be_written_refuses_before_anything_runs(
    tmp_path: Path,
) -> None:
    service, _harness, session = await _bound(tmp_path)
    with pytest.raises(JobStartRefused, match="no job kind"):
        await service.start("nope", title="x")

    async def refuse(*_a: Any, **_k: Any) -> str:
        raise OSError("disk full")

    session.place_custom_entry = refuse  # type: ignore[method-assign]
    with pytest.raises(JobStartRefused, match="could not be recorded"):
        await service.start(_sleeper.KIND, title="nap")
    assert _sleeper.Sleeper.instances == [], "no runner was built"
    assert service.admission.count(_sleeper.KIND) == 0
    await service.shutdown()


async def test_the_start_fact_is_confirmed_before_the_launch(tmp_path: Path) -> None:
    service, _harness, _session = await _bound(tmp_path)
    seen: list[int] = []

    class Peek(_sleeper.Sleeper):
        async def run(self, ctx: Any) -> ClientOutcome:
            reopened = await reopen(tmp_path)
            seen.append(len(fold(await reopened.get_entries())))
            return await super().run(ctx)

    service.register_kind(_sleeper.SleeperAdapter(), Peek)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    assert seen == [1]
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    await service.shutdown()
