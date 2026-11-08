"""Every adapter and runner operation that can fail, and what the service does then (#722, Codex r5 #10, r3 #13).

ADR-0293's table, one case per operation. Fails on main ``a385c64a``: no job
service. Naive: a ``settle`` that raises reads as ``failed`` (or ``stopped``),
which tells the model a job ended whose processes nobody confirmed; a raising
``render_notice`` loses the notice; a raising ``records`` loses the outcome.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs.records import append_fact, fold, make_fact

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, reopen, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")

_RUNNER_OPS = ("run", "request_stop", "settle", "helper_cleanup", "records")
_ADAPTER_OPS = (
    "render_notice",
    "finalize_at_placement",
    "render_rows",
    "output",
    "recover",
    "relation",
)


async def _run_job(
    tmp_path: Path,
    *,
    runner_fail: frozenset[str] = frozenset(),
    adapter_fail: frozenset[str] = frozenset(),
    stop: bool = False,
) -> tuple[Any, Any, str, Any]:
    service = fast_service(cleanup_bound=1.0)
    _sleeper.register(service, fail=adapter_fail)
    session = await make_session(tmp_path)
    harness = make_harness(session)
    await service.bind(harness)
    job_id = (
        await service.start(
            _sleeper.KIND,
            title="nap",
            params={"seconds": 30 if stop else 0.05, "fail": sorted(runner_fail)},
        )
        if "run" not in runner_fail
        else await _start_expect_failure(service)
    )
    if stop:
        await service.stop(job_id)
    await wait_for(
        lambda: service.snapshot(job_id).state not in ("starting", "running", "stopping")
    )  # type: ignore[union-attr]
    return service, harness, job_id, session


async def _start_expect_failure(service: Any) -> str:
    try:
        return await service.start(_sleeper.KIND, title="nap", params={"fail": ["run"]})
    except Exception:  # noqa: BLE001 — a run that raises before its launch is a failed launch
        (job_id,) = service._jobs
        return str(job_id)


@pytest.mark.parametrize("op", [*_RUNNER_OPS, *_ADAPTER_OPS, "decode"])
async def test_failure_transitions(op: str, tmp_path: Path) -> None:
    if op == "run":
        service, harness, job_id, session = await _run_job(tmp_path, runner_fail=frozenset({"run"}))
        snap = service.snapshot(job_id)
        assert snap.state == "failed" and "client error: RuntimeError" in snap.brief
        assert harness.pending_notices == 0, "raised before its launch: the start's own error"
    elif op == "request_stop":
        service, harness, job_id, session = await _run_job(
            tmp_path, runner_fail=frozenset({"request_stop"}), stop=True
        )
        runner = _sleeper.Sleeper.instances[-1]
        assert runner.proc is not None and runner.proc.returncode is not None, (
            "the service's own legs stopped it"
        )
        assert service.snapshot(job_id).state in ("done", "stopped", "failed")
    elif op == "settle":
        service, harness, job_id, session = await _run_job(
            tmp_path, runner_fail=frozenset({"settle"})
        )
        snap = service.snapshot(job_id)
        assert snap.state == "unresolved" and snap.unresolved_reason == "settle_error"
        assert service.admission.count(_sleeper.KIND) == 1, "the slot is kept"
        await service.tick()
        assert service.snapshot(job_id).state == "unresolved", "never failed, stopped or lost"
    elif op == "helper_cleanup":
        # Contract §2.5 (Codex #7): a FAILED cleanup is not a finished job — it
        # stays unresolved(cleanup_error) and keeps its slot; the duty is retried.
        service, harness, job_id, session = await _run_job(
            tmp_path, runner_fail=frozenset({"helper_cleanup"})
        )
        job = fold(await session.get_entries())[job_id]
        assert job.cleanup is not None and job.cleanup["helpers"] == "unverified"
        assert job.outcome is None, "no outcome while the cleanup duty is open"
        snap = service.snapshot(job_id)
        assert snap is not None and (snap.state, snap.unresolved_reason) == ("unresolved", "cleanup_error")
        assert service.admission.count(_sleeper.KIND) == 1, "the slot is kept"
        (notice,) = harness.undelivered_notices()
        assert "not confirmed stopped" in notice.body
    elif op == "records":
        service, harness, job_id, session = await _run_job(
            tmp_path, runner_fail=frozenset({"records"})
        )
        job = fold(await session.get_entries())[job_id]
        assert job.outcome is not None and job.outcome["client_records"] == "missing"
        assert harness.pending_notices == 1
    elif op == "render_notice":
        service, harness, job_id, session = await _run_job(
            tmp_path, adapter_fail=frozenset({"render_notice"})
        )
        (notice,) = harness.undelivered_notices()
        assert "its client could not render a result" in notice.body
    elif op == "finalize_at_placement":
        service, harness, job_id, session = await _run_job(
            tmp_path, adapter_fail=frozenset({"finalize_at_placement"})
        )
        await harness.prompt_notices()
        entry = next(
            e for e in await session.get_entries() if getattr(e, "type", "") == "custom_message"
        )
        assert "placed on a branch" not in entry.content, "no extra line"
    elif op == "render_rows":
        service, harness, job_id, session = await _run_job(
            tmp_path, adapter_fail=frozenset({"render_rows"})
        )
        snap = service.snapshot(job_id)
        assert service.render_rows(snap, 80) == ["sleeper · nap"]
    elif op == "output":
        service, harness, job_id, session = await _run_job(
            tmp_path, adapter_fail=frozenset({"output"})
        )
        assert service.output(job_id) == "could not read the result: RuntimeError"
    elif op in ("recover", "relation"):
        session = await make_session(tmp_path)
        await append_fact(
            session,
            make_fact(
                "start",
                "j",
                kind=_sleeper.KIND,
                kind_version=1,
                owner="s",
                mode="background",
                notify=True,
                title="nap",
                created=1.0,
                settings_key="sleeper",
            ),
        )
        service = fast_service()
        _sleeper.register(service, fail=frozenset({op}))
        harness = make_harness(await reopen(tmp_path))
        await service.bind(harness)
        assert service.snapshot("j").unresolved_reason == f"{op}_error"  # type: ignore[union-attr]
        # Codex #11: the model is told the job is unresolved (a restored notice,
        # which starts no TUI turn); nothing the kind would render is made up.
        (notice,) = harness.undelivered_notices()
        assert notice.notice_id == "j:unresolved" and f"({op}_error)" in notice.body
        assert service.admission.count(_sleeper.KIND) == 1, "its slot is held"
        return
    elif op == "decode":
        # ``decode`` reads a client's stored outcome payload; the service reads the
        # stored body instead, so a broken decode never costs a notice.
        service, harness, job_id, session = await _run_job(
            tmp_path, adapter_fail=frozenset({"decode"})
        )
        await service.shutdown()
        again = fast_service()
        _sleeper.register(again, fail=frozenset({"decode"}))
        h2 = make_harness(await reopen(tmp_path))
        await again.bind(h2)
        assert h2.pending_notices == 1
        return
    await service.shutdown()


async def test_cancellation_is_reraised_from_every_operation(tmp_path: Path) -> None:
    import asyncio

    service = fast_service()
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(
        _sleeper.KIND, title="nap", params={"seconds": 30, "hang_settle": True}
    )
    job = service._jobs[job_id]
    service.request_stop(job_id)
    await asyncio.sleep(0.4)
    assert job.task is not None
    job.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job.task
