"""#821 — a run moved with Ctrl+B counts its time from its start, not from the move.

``JobService.prepare_adopted`` started the job's clock (and its ``created``) at
the move, so the time the run spent in the foreground was missing from every
elapsed time the job reported: a run moved while marked stuck got its attention
notice as "is still running after 0s", and its finished notice, its
``agent(action="status")`` "for …" and ``/jobs`` counted from the move. The
commit now hands the job the run's foreground time (``ran_for``, from the row's
``started_at``, the stamp its result's ``elapsed_ms`` counts from); the job's
last activity stays the move.

The foreground time is made 5 s here by moving the row's ``started_at`` back
before Ctrl+B, rather than waiting. Fails on ``1c2a4a7c``: the attention header
says "after 0s", the status row "for 0s", the snapshot's ``elapsed`` is under a
second (and the service row raises ``TypeError`` on ``ran_for``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs.commands import detail
from aelix_coding_agent.jobs.contract import NoticeBody

from tests.agents_ext._detach_rig import (  # noqa: F401 — reap_children is the fixture
    call,
    finish,
    parked_call,
    reap_children,
    row_of,
    settled,
    tui_rig,
    within,
)
from tests.agents_ext.test_agent_background_611 import jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_stuck_notice_123 import _STUCK_THEN_SILENT
from tests.agents_ext.test_tool_and_security import _text
from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for
from tests.jobs.test_adopt_611 import _child, _record, _service, _Waiter

FOREGROUND = 5.0


def _seconds(pattern: str, text: str) -> int:
    found = re.search(pattern, text)
    assert found, (pattern, text)
    return int(found.group(1))


@pytest.mark.skipif(sys.platform == "win32", reason="a stub child's pipes, as test_stuck_notice_123")
@pytest.mark.usefixtures("jobs_on", "reap_children")
async def test_a_run_moved_while_stuck_counts_its_foreground_time_everywhere(tmp_path: Path) -> None:
    gate = tmp_path / "exit"
    rig = await tui_rig(tmp_path, script=_STUCK_THEN_SILENT.replace("GATE", repr(str(gate))))
    try:
        turn = await parked_call(rig)
        row = row_of(rig)
        await wait_for(lambda: row.stream.last_text == "halfway")
        row.started_at -= FOREGROUND  # as if it had run 5 s in the foreground
        # ... and the wall clock read an hour earlier at its start (it was set
        # forward since): the job's start is what it read then (fix round 1, L11).
        row.started_wall = time.time() - 3600 - FOREGROUND
        assert rig.jobs_ui.request_detach()
        await within(turn)
        (snap,) = rig.jobs()
        attention = f"{snap.job_id}:attention"
        await wait_for(lambda: attention in [n.notice_id for n in rig.harness.undelivered_notices()])
        (notice,) = [n for n in rig.harness.undelivered_notices() if n.notice_id == attention]
        head = notice.body.splitlines()[0]
        assert _seconds(r"is still running after (\d+)s\.", head) >= FOREGROUND, head
        running = rig.service.snapshot(snap.job_id)
        assert running.elapsed is not None and running.elapsed >= FOREGROUND
        assert running.last_activity_ago is not None and running.last_activity_ago < FOREGROUND, "active at the move"
        status = _text(await within(asyncio.ensure_future(call(rig, {"action": "status", "id": snap.job_id}))))
        assert _seconds(r"\bfor (\d+)s\b", status) >= FOREGROUND, status
        started = next(line for line in detail(rig.service, running, 120) if line.lstrip().startswith("started"))
        assert started.split("started", 1)[1].strip() == time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(row.started_wall)
        ), started
    finally:
        gate.write_text("go")
    ended = await settled(rig)
    final = next(n for n in rig.harness.undelivered_notices() if n.notice_id == f"{ended.job_id}:final")
    assert _seconds(r" after (\d+)s\.", final.body.splitlines()[0]) >= FOREGROUND, final.body.splitlines()[0]
    assert ended.elapsed is not None and ended.elapsed >= FOREGROUND
    await finish(rig)


async def _frozen_service(tmp_path: Path, now: list[float], wall: list[float]) -> tuple[Any, Any]:
    """``_service``'s, on a monotonic clock and a wall clock that move only when told
    (fix round 1, V-F9: the elapsed times below no longer race the child's startup)."""

    service = fast_service(clock=lambda: now[0], wall=lambda: wall[0])
    adapter = _sleeper.SleeperAdapter()
    adapter.capabilities = dataclasses.replace(adapter.capabilities, detachable=True)
    service.register_kind(adapter, _sleeper.Sleeper)
    session = await make_session(tmp_path)
    await service.bind(make_harness(session))
    return service, session


@pytest.mark.usefixtures("jobs_on")
async def test_an_adopted_job_starts_its_clock_ran_for_seconds_before_the_move(tmp_path: Path) -> None:
    now, wall = [1000.0], [5000.0]
    service, session = await _frozen_service(tmp_path, now, wall)
    adapter = service.registry.get(_sleeper.KIND).adapter
    adapter.capabilities = dataclasses.replace(adapter.capabilities, notify=True)
    proc = await _child(0.3)
    reservation = service.reserve(_sleeper.KIND, 1)
    job = service.prepare_adopted(
        _sleeper.KIND,
        title="moved",
        runner=_Waiter(proc),
        session=session,
        client_start={"v": 1, "adopted": True, "key": "k"},
        process=_record(proc),
        ran_for=2.0,
    )
    service.adopt(job, asyncio.get_running_loop().create_task(service.run_adopted(job)))
    service.give_slot(job, reservation)
    service.announce_adopted(job)
    assert job.created == 4998.0, "no wall start given: counted back from the move"
    snap = service.snapshot(job.job_id)
    assert snap.elapsed == 2.0
    assert snap.last_activity_ago == 0.0, "its last activity is the move"
    assert job.ctx.attention(NoticeBody("looping"), brief="looks stuck")
    harness = service._harness  # noqa: SLF001 — the harness the service posts to
    (attention,) = [n for n in harness.undelivered_notices() if n.notice_id.endswith(":attention")]
    assert attention.body.splitlines()[0].endswith("is still running after 2s."), attention.body.splitlines()[0]
    await wait_for(lambda: service.snapshot(job.job_id).notice_state == "pending")
    (final,) = [n for n in harness.undelivered_notices() if n.notice_id.endswith(":final")]
    assert final.body.splitlines()[0].endswith("finished after 2s."), final.body.splitlines()[0]
    await service.shutdown()


@pytest.mark.usefixtures("jobs_on")
async def test_a_moved_jobs_start_is_the_wall_clock_its_call_started_at(tmp_path: Path) -> None:
    """Codex review of #821, L11: ``created`` was the move's wall time less ``ran_for``,
    so a wall clock set back 60 s during a 10 s run put the start 60 s early.
    Fails on ``1099de2b``: ``prepare_adopted`` takes no ``started_wall``."""

    now, wall = [1000.0], [940.0]  # the wall clock was set back 60 s during the run
    service, session = await _frozen_service(tmp_path, now, wall)
    proc = await _child(0.1)
    try:
        job = service.prepare_adopted(
            _sleeper.KIND,
            title="moved",
            runner=_Waiter(proc),
            session=session,
            client_start={"v": 1, "adopted": True, "key": "k"},
            process=_record(proc),
            ran_for=10.0,
            started_wall=1000.0,
        )
        assert job.created == 1000.0, "the start as the wall clock read it then"
        assert now[0] - job.started_at == 10.0, "the elapsed time is the monotonic clock's"
    finally:
        await proc.wait()
        await service.shutdown()


@pytest.mark.usefixtures("jobs_on")
async def test_a_negative_foreground_time_counts_as_none(tmp_path: Path) -> None:
    """A run never started after its move: ``ran_for`` below zero starts the clock at the move."""

    service, session = await _service(tmp_path)
    proc = await _child(0.2)
    job = service.prepare_adopted(
        _sleeper.KIND,
        title="moved",
        runner=_Waiter(proc),
        session=session,
        client_start={"v": 1, "adopted": True, "key": "k"},
        process=_record(proc),
        ran_for=-5.0,
    )
    service.adopt(job, asyncio.get_running_loop().create_task(service.run_adopted(job)))
    service.give_slot(job, service.reserve(_sleeper.KIND, 1))
    snap = service.snapshot(job.job_id)
    assert snap.elapsed is not None and 0.0 <= snap.elapsed < 1.0
    assert job.created <= time.time()
    await service.shutdown()


@pytest.mark.usefixtures("jobs_on")
async def test_a_restart_shows_the_run_time_the_outcome_recorded(tmp_path: Path) -> None:
    """Codex review of #821, M5 (on ``1c2a4a7c`` too): a restart's snapshot of an
    ended job had ``elapsed`` ``None`` though its outcome fact holds ``elapsed_ms``,
    so ``/jobs <id>`` read "elapsed —". Fails on ``1099de2b``: ``None``."""

    now, wall = [1000.0], [5000.0]
    service, session = await _frozen_service(tmp_path, now, wall)
    proc = await _child(0.1)
    job = service.prepare_adopted(
        _sleeper.KIND,
        title="moved",
        runner=_Waiter(proc),
        session=session,
        client_start={"v": 1, "adopted": True, "key": "k"},
        process=_record(proc),
        ran_for=10.0,
    )
    service.adopt(job, asyncio.get_running_loop().create_task(service.run_adopted(job)))
    service.give_slot(job, service.reserve(_sleeper.KIND, 1))
    await wait_for(lambda: service.snapshot(job.job_id).state == "done")  # type: ignore[union-attr]
    await service.shutdown()
    reopened = fast_service()
    reopened.register_kind(_sleeper.SleeperAdapter(), _sleeper.Sleeper)
    await reopened.bind(make_harness(session))
    try:
        snap = reopened.snapshot(job.job_id)
        assert snap is not None and snap.state == "done" and snap.elapsed == 10.0, snap
        assert [line.split() for line in detail(reopened, snap, 120) if "elapsed" in line] == [["elapsed", "10s"]]
    finally:
        await reopened.shutdown()


def test_an_outcome_with_no_measured_run_stays_unknown() -> None:
    from aelix_coding_agent.jobs.service import _recorded_elapsed

    assert [_recorded_elapsed({"elapsed_ms": v}) for v in (1500, 0, -3, None, True, "9", 2.5)] == [
        1.5,
        None,
        None,
        None,
        None,
        None,
        0.0025,
    ]
    assert _recorded_elapsed({}) is None
