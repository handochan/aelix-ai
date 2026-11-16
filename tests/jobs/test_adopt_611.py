"""#775 (#611 S3a) — ``JobService.prepare_adopted`` / ``adopt``: a running process becomes a job (ADR-0298).

Kind-agnostic, with a synthetic detachable kind (``test:sleeper`` with
``detachable=True``) and a runner that only waits for a child the test started
itself — what a client's commit hands the service once its own run moved:

* ``adopt`` is SYNCHRONOUS: a plain function prepares, adopts and hands the
  slot over, inside the loop, with no ``await`` — the commit runs in one step;
* the process's start time is the one handed over, never sampled again;
* every refusal happens before anything changed: no job, no slot, no task;
* the job's own task writes its ``start`` (with the client's linkage) and
  ``process`` facts, then runs and ends as any job does, its slot freed.

Fails on ``c91cdc7b``: the service has no ``prepare_adopted``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs import evidence as ev
from aelix_coding_agent.jobs import service as service_module
from aelix_coding_agent.jobs.contract import ClientOutcome, HelperCleanup
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE
from aelix_coding_agent.jobs.service import JobService, JobStartRefused

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")


class _Waiter:
    """A runner that took over a child it did not start: it waits for it, as a moved run does."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc

    async def run(self, ctx: Any) -> ClientOutcome:
        await self.proc.wait()
        return ClientOutcome("done", "the child ended")

    def request_stop(self, reason: str) -> None:
        self.proc.kill()

    async def settle(self) -> None:
        await self.proc.wait()

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        return HelperCleanup("confirmed")

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        return [("test.adopted", {"status": outcome.status})]


async def _service(tmp_path: Path, *, detachable: bool = True) -> tuple[JobService, Any]:
    service = fast_service()
    adapter = _sleeper.SleeperAdapter()
    adapter.capabilities = dataclasses.replace(adapter.capabilities, detachable=detachable)
    service.register_kind(adapter, _sleeper.Sleeper)
    session = await make_session(tmp_path)
    await service.bind(make_harness(session))
    return service, session


async def _child(seconds: float = 0.2) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", f"import time; time.sleep({seconds})", **_sleeper._spawn_kwargs()
    )


def _record(proc: asyncio.subprocess.Process, started: float = 1234.5) -> ev.ProcessRecord:
    return ev.ProcessRecord(pid=proc.pid, started=started, pgid=None, proc=proc)


async def test_adopt_is_synchronous_and_the_job_runs_as_any_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, session = await _service(tmp_path)
    proc = await _child()
    sampled: list[int] = []
    monkeypatch.setattr(service_module, "process_started", lambda pid: sampled.append(pid))
    reservation = service.reserve(_sleeper.KIND, 1)
    assert service.admission.count(_sleeper.KIND) == 1

    def commit() -> Any:  # a plain function: the client's commit has no await
        job = service.prepare_adopted(
            _sleeper.KIND,
            title="moved",
            runner=_Waiter(proc),
            session=session,
            client_start={"v": 1, "adopted": True, "key": "k"},
            process=_record(proc),
        )
        task = asyncio.get_running_loop().create_task(service.run_adopted(job))
        service.adopt(job, task)
        service.give_slot(job, reservation)
        return job

    job = commit()
    assert reservation.released and service.admission.count(_sleeper.KIND) == 1, "moved, not taken again"
    assert service.snapshot(job.job_id) is not None and service.snapshot(job.job_id).state == "running"
    await wait_for(lambda: service.snapshot(job.job_id).state == "done")
    assert service.admission.count(_sleeper.KIND) == 0, "freed at its confirmed cleanup"
    assert sampled == [], "the start time handed over is used, never sampled again"
    entries = await session.get_entries()
    facts = [e.data for e in entries if e.type == "custom" and e.custom_type == JOB_FACT_TYPE]
    assert [f["fact"] for f in facts] == ["start", "process", "cleanup", "outcome"]
    assert facts[0]["client"] == {"v": 1, "adopted": True, "key": "k"} and facts[0]["mode"] == "background"
    assert facts[1]["evidence"]["pid"] == proc.pid and facts[1]["evidence"]["started"] == 1234.5
    await service.shutdown()


@pytest.mark.parametrize("why", ["closed", "unknown kind", "not detachable", "another session"])
async def test_a_refusal_changes_nothing(tmp_path: Path, why: str) -> None:
    service, session = await _service(tmp_path, detachable=why != "not detachable")
    proc = await _child(0.05)
    kind = "nope" if why == "unknown kind" else _sleeper.KIND
    if why == "closed":
        service._closed = True
    if why == "another session":
        session = await make_session(tmp_path, name="other.jsonl", session_id="other")
    reservation = service.reserve(_sleeper.KIND, 1)
    started_before = service._started
    with pytest.raises(JobStartRefused):
        service.prepare_adopted(
            kind,
            title="t",
            runner=_Waiter(proc),
            session=session,
            client_start={},
            process=_record(proc),
        )
    assert service.snapshots() == [] and service._jobs == {} and service._started == started_before
    assert not reservation.released and service.admission.count(_sleeper.KIND) == 1
    reservation.release()
    await proc.wait()
    service._closed = False
    await service.shutdown()


async def test_unadopt_takes_a_published_job_back(tmp_path: Path) -> None:
    service, session = await _service(tmp_path)
    proc = await _child(0.05)
    job = service.prepare_adopted(
        _sleeper.KIND, title="t", runner=_Waiter(proc), session=session, client_start={}, process=_record(proc)
    )
    task = asyncio.get_running_loop().create_task(asyncio.sleep(0))
    started = service._started
    heard: list[bool] = []
    service.add_listener(lambda: heard.append(True))
    service.adopt(job, task)
    assert service.snapshot(job.job_id) is not None
    service.unadopt(job)
    assert service.snapshot(job.job_id) is None and service.snapshots() == []
    assert service._started == started, "its place in start order is given back (verify N5)"
    assert heard == [], "adopt and unadopt are silent: listeners hear only a whole commit"
    await task
    await proc.wait()
    await service.shutdown()


def test_the_offers_need_a_host_and_exactly_one_ready_offer() -> None:
    from aelix_coding_agent.jobs.detach import NO_HOST, DetachOffers

    class _Offer:
        def __init__(self, ready: bool) -> None:
            self.is_ready, self.asked = ready, 0

        def ready(self) -> bool:
            return self.is_ready

        def request(self) -> bool:
            self.asked += 1
            return True

    offers = DetachOffers()
    assert offers.blocked() == NO_HOST, "no host, no move"
    offers.guard = lambda: None
    assert offers.blocked() is None

    def broken() -> str | None:
        raise RuntimeError("x")

    offers.guard = broken
    assert offers.blocked() == "the host could not say whether it is safe"
    one, two = _Offer(True), _Offer(True)
    take_one = offers.offer(one)
    take_two = offers.offer(two)
    assert not offers.request() and one.asked == two.asked == 0
    take_two()
    take_two()  # idempotent
    assert offers.request() and one.asked == 1
    one.is_ready = False
    assert not offers.request()
    take_one()
    assert offers.ready() == []
    guard = offers.guard
    offers.unhost(lambda: None)
    assert offers.guard is guard, "another host's leaving does not unhost this one"
    offers.unhost(guard)
    assert offers.guard is None and offers.note is None

