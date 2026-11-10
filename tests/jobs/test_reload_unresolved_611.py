"""Owner end and ``/reload`` stop the session's jobs, bounded; what is not confirmed stays the service's (#722).

Owner decision 4 (2026-11-07): ``/reload`` stops background jobs. Codex r5 #10:
a job whose ``settle`` hangs must not block the reload and must not be
reported ended. Codex r1 #10: a new runtime per build must not orphan the jobs.
Fails on main ``a385c64a``: no job service. Naive: the reload awaits every
barrier unbounded (a hanging settle hangs ``/reload``), or drops the job, or
calls the invalidated extension API from the old runner.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs.contract import ClientOutcome
from aelix_coding_agent.jobs.records import fold

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for, wait_settled

pytestmark = pytest.mark.usefixtures("jobs_active")


async def test_reload_with_hanging_settle_leaves_unresolved_owned_by_service(
    tmp_path: Path,
) -> None:
    service = fast_service(cleanup_bound=0.5)
    _sleeper.register(service)
    session = await make_session(tmp_path)
    await service.bind(make_harness(session))
    job_id = await service.start(
        _sleeper.KIND, title="nap", params={"seconds": 30, "hang_settle": True}
    )

    loop = asyncio.get_running_loop()
    begun = loop.time()
    await service.owner_ended(service.owner, "reload")
    assert loop.time() - begun < 3.0, "bounded by CLEANUP_BOUND"

    snap = service.snapshot(job_id)
    assert snap is not None and snap.state == "unresolved"
    assert service.admission.count(_sleeper.KIND) == 1, "its slot is held"
    job = service._jobs[job_id]
    assert job.task is not None and not job.task.done(), "the finalizer is still the service's"

    # The rebuilt harness of the same session is bound; the job is still listed.
    await service.bind(make_harness(session), reason="reload")
    assert [s.job_id for s in service.snapshots()] == [job_id]
    job.task.cancel()


async def test_old_runner_never_calls_an_invalidated_api(tmp_path: Path) -> None:
    """A runner holds its context only: after the reload invalidates the extension API, it finishes."""

    service = fast_service()
    session = await make_session(tmp_path)
    await service.bind(make_harness(session))

    class StaleApi:
        valid = True

        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"the runner touched the extension API ({name}) after it started")

    api = StaleApi()

    class Holder(_sleeper.Sleeper):
        def __init__(self, params: Any) -> None:
            super().__init__(params)
            self.api = api  # what a careless runner would keep

        async def run(self, ctx: Any) -> ClientOutcome:
            return await super().run(ctx)

    service.register_kind(_sleeper.SleeperAdapter(), Holder)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.2})
    await service.owner_ended(service.owner, "reload")
    await wait_for(lambda: service.snapshot(job_id).state in ("stopped", "done"))  # type: ignore[union-attr]


async def test_service_survives_new_runtime_per_build(tmp_path: Path) -> None:
    """One service across builds: a job started under build 1 delivers to build 2's harness."""

    service = fast_service()
    _sleeper.register(service)
    session = await make_session(tmp_path)
    first = make_harness(session)
    await service.bind(first)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.3})
    await first.dispose()
    second = make_harness(session)
    await service.bind(second, reason="reload")
    _sleeper.register(service)  # the rebuilt extensions register the kind again
    await wait_for(lambda: second.pending_notices == 1)
    assert first.pending_notices == 0
    assert service.snapshot(job_id).state == "done"  # type: ignore[union-attr]
    await service.shutdown()


async def test_a_session_swap_stops_the_old_sessions_jobs_and_keeps_their_notices_there(
    tmp_path: Path,
) -> None:
    service = fast_service()
    _sleeper.register(service)
    old = await make_session(tmp_path, "old.jsonl", session_id="old")
    await service.bind(make_harness(old))
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    await service.owner_ended("old", "owner_end")
    assert service.snapshot(job_id).state == "stopped"  # type: ignore[union-attr]
    new = make_harness(await make_session(tmp_path, "new.jsonl", session_id="new"))
    await service.bind(new, reason="new")
    assert new.pending_notices == 0, "G7: a notice belongs to its own session"
    assert service.snapshots() == []
    await service.shutdown()


async def test_a_job_stopped_and_then_confirmed_after_the_bound_records_to_its_session(
    tmp_path: Path,
) -> None:
    service = fast_service(cleanup_bound=0.3, stop_grace=0.05)
    _sleeper.register(service)
    session = await make_session(tmp_path)
    harness = make_harness(session)
    await service.bind(harness)
    job_id = await service.start(
        _sleeper.KIND, title="nap", params={"seconds": 30, "ignore_stop": True}
    )
    await service.owner_ended(service.owner, "reload")
    # Its runner ignored the stop; the service's own legs ended its process
    # anyway (Codex #6), so the job ends once its barrier passes, its records
    # in the session it was started in.
    await wait_for(lambda: service.snapshot(job_id).state in ("done", "stopped"))  # type: ignore[union-attr]
    runner = _sleeper.Sleeper.instances[-1]
    assert runner.proc is not None and runner.proc.returncode is not None
    await wait_for(lambda: harness.pending_notices == 1)
    assert job_id in fold(await session.get_entries())
    await service.shutdown()


async def test_a_reload_stops_a_runner_that_ignores_the_stop(tmp_path: Path) -> None:
    """Codex #6: ``request_stop`` returns, the runner ignores it and never returns.

    Fails on ``a80e81fa``: the reload's bound left it ``unresolved(reload_timeout)``
    with its root alive, and neither the liveness tick nor ``/jobs stop`` did
    anything while its task ran (``root_alive=true``, ``runner_done=false``).
    """

    import sys

    class Uncooperative(_sleeper.Sleeper):
        async def run(self, ctx: Any) -> ClientOutcome:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "import time; time.sleep(60)", **_sleeper._spawn_kwargs()
            )
            ctx.register_process(self.proc, pgid=self.proc.pid if sys.platform != "win32" else None)
            ctx.launched()
            await asyncio.Event().wait()  # ignores every stop
            raise AssertionError("unreachable")

        def request_stop(self, reason: str) -> None:
            self.calls.append("request_stop")

    service = fast_service(cleanup_bound=0.3, stop_grace=0.05)
    service.register_kind(_sleeper.SleeperAdapter(), Uncooperative)
    session = await make_session(tmp_path)
    harness = make_harness(session)
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="ignores stop")
    runner = Uncooperative.instances[-1]
    await service.owner_ended(service.owner, "reload")
    await wait_for(lambda: service.snapshot(job_id).state == "stopped")  # type: ignore[union-attr]
    assert runner.proc is not None and runner.proc.returncode is not None, "its root is gone"
    snap = service.snapshot(job_id)
    assert snap is not None and "did not stop when asked" in snap.brief
    assert service.admission.count(_sleeper.KIND) == 0
    assert "stopped" in fold(await session.get_entries())[job_id].outcome["status"]  # type: ignore[index]
    await service.shutdown()


async def test_a_failed_cleanup_is_retried_and_the_job_then_completes(tmp_path: Path) -> None:
    """Codex #7: ``helper_cleanup`` raised once. The job holds its slot as
    ``unresolved(cleanup_error)``; the retry succeeds and the job completes with
    what its run ended with.

    Fails on ``a80e81fa``: the raise became a returned "unverified", the job was
    ``done`` and its slot was released at once.
    """

    service = fast_service()
    _sleeper.register(service)
    session = await make_session(tmp_path)
    harness = make_harness(session)
    await service.bind(harness)
    job_id = await service.start(
        _sleeper.KIND, title="nap", params={"seconds": 0.01, "fail": ["helper_cleanup"]}
    )
    await wait_for(lambda: service.snapshot(job_id).state == "unresolved")  # type: ignore[union-attr]
    assert service.admission.count(_sleeper.KIND) == 1
    runner = _sleeper.Sleeper.instances[-1]
    runner.fail = frozenset()
    job = service._jobs[job_id]
    job.cleanup_due = 0.0
    await service.tick()
    snap = service.snapshot(job_id)
    assert snap is not None and snap.state == "done" and "slept" in snap.brief
    assert service.admission.count(_sleeper.KIND) == 0
    folded = fold(await session.get_entries())[job_id]
    assert folded.cleanup is not None and folded.cleanup["helpers"] == "none"
    assert folded.outcome is not None and folded.outcome["status"] == "done"
    await service.shutdown()


async def test_an_ignored_stop_is_ended_by_the_services_own_legs_before_any_cancel(tmp_path: Path) -> None:
    """ADR-0293 §3's row, in its order: after the stop grace the service's own
    kill legs, the grace again, and only then is ``run`` cancelled. A runner that
    ignores ``request_stop`` but returns once its process is gone therefore
    returns on its own: the legs ended the process inside the second grace.
    Without the escalation's own legs (verify R23) nothing ends the process until
    the cancel, and the job ends as the service's "did not stop when asked".
    """

    service = fast_service(stop_grace=0.5, cleanup_bound=5.0)
    _sleeper.register(service)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(
        _sleeper.KIND, title="nap", params={"seconds": 30, "ignore_stop": True}
    )
    since = time.monotonic()
    snap = await service.stop(job_id)
    runner = _sleeper.Sleeper.instances[-1]
    assert "request_stop" in runner.calls
    await wait_settled(service, (job_id,), since=since, state=("done", "stopped"))  # #751
    snap = service.snapshot(job_id)
    assert runner.proc is not None and runner.proc.returncode is not None
    assert snap is not None and snap.state in ("done", "stopped")
    assert service._jobs[job_id].stop_requested_by_service is None, "never cancelled"
    assert "did not stop when asked" not in snap.brief
    await service.shutdown()
