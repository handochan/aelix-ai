"""The barrier: no terminal state before the job's processes are confirmed gone (#722, Codex r1 #1).

On main ``a385c64a`` there is no job service (``ImportError``). The naive
service records ``stopped`` as soon as the runner returns from a stop request;
here the runner returns at once while its child still sleeps, and the state may
turn terminal only after that child is reaped.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from aelix_coding_agent.jobs.contract import TERMINAL_STATES

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")


async def test_no_terminal_before_root_exit_after_cancel(tmp_path: Path) -> None:
    service = fast_service()
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    runner = _sleeper.Sleeper.instances[-1]
    assert runner.proc is not None and runner.proc.returncode is None

    states: list[str] = []
    service.add_listener(lambda: states.append(service.snapshot(job_id).state))  # type: ignore[union-attr]
    snap = await service.stop(job_id)

    assert snap is not None and snap.state == "stopped"
    assert runner.proc.returncode is not None, "reaped before the terminal state"
    first_terminal = next(i for i, s in enumerate(states) if s in TERMINAL_STATES)
    assert all(s not in TERMINAL_STATES for s in states[:first_terminal])
    if sys.platform != "win32":
        with pytest.raises(ProcessLookupError):
            os.killpg(runner.proc.pid, 0)
    await service.shutdown()


async def test_a_job_that_finishes_on_its_own_is_done_with_its_output(tmp_path: Path) -> None:
    service = fast_service()
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.05})
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    snap = service.snapshot(job_id)
    assert snap is not None and "slept 0.05" in snap.payload["output"]
    assert harness.pending_notices == 1
    await service.shutdown()


async def test_the_stop_reason_is_sent_once_and_the_slot_is_held_until_the_barrier(
    tmp_path: Path,
) -> None:
    service = fast_service()
    _sleeper.register(service, bound=1)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    assert service.admission.count(_sleeper.KIND) == 1
    service.request_stop(job_id)
    service.request_stop(job_id)
    runner = _sleeper.Sleeper.instances[-1]
    assert runner.calls.count("request_stop") == 1
    assert service.admission.count(_sleeper.KIND) == 1, "held until cleanup is confirmed"
    await wait_for(lambda: service.snapshot(job_id).state == "stopped")  # type: ignore[union-attr]
    assert service.admission.count(_sleeper.KIND) == 0
    await asyncio.sleep(0)
    await service.shutdown()
