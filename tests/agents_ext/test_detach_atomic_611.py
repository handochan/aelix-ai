"""#775 fix round 1 — the commit is exception-atomic: the run is the job's or the turn's, never both.

Codex round 1 #1 and #3, verify F1, F4, F5 and F6, each reproduced first on
``fb5e7654``:

* the call's answer (``Detached`` and its text) is built before the job's task
  exists, so a failure there is a refusal with nothing spent; on ``fb5e7654``
  it was built after the gate opened and the slot moved, and its failure made a
  refusal while the job also drove the run (1a);
* a publish step's undo is registered before the step runs, so a step that
  changed something and then raised is undone (1b);
* ``Reservation.split`` builds the job's reservation before it spends the
  call's, so a failure there leaks no slot (1c);
* publication is silent and the job's listeners hear of it once the commit is
  whole (3);
* a slot spent before the commit is refused by the commit's own check, before
  the job's task exists (verify F10, M056).

Real extension, job service, harness and print channel child (``_detach_rig``).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import detach as dt
from aelix_agents.jobs_client import KIND, DelegationJobs
from aelix_coding_agent.jobs import admission as adm
from aelix_coding_agent.jobs.service import JobService

from tests.agents_ext._detach_rig import (  # noqa: F401 — reap_children is the fixture
    finish,
    parked_call,
    reap_children,
    row_of,
    script,
    tui_rig,
    within,
)
from tests.agents_ext.test_agent_background_611 import jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_tool_and_security import _text

REAL_CHILD = pytest.mark.skipif(
    sys.platform == "win32",
    reason="as test_agent_background_611: the gated rig's real child is proven on win32 by #611 S3c",
)
pytestmark = [REAL_CHILD, pytest.mark.usefixtures("jobs_on", "reap_children")]
MOVED = "Moved to the background at the user's request"
NOTE = "Ctrl+B: could not move it to the background now ({})."


async def _parked(tmp_path: Path) -> tuple[Any, asyncio.Future[Any], Path, Any]:
    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("the answer", gate=gate, partial="halfway"))
    turn = await parked_call(rig)
    return rig, turn, gate, row_of(rig)


async def _stayed(rig: Any, turn: asyncio.Future[Any], gate: Path, row: Any) -> None:
    """A refusal left nothing behind: no job, no client entry, no route, the run ends in the turn."""

    assert rig.service.snapshots() == [] and rig.ext._delegation_jobs.info == {}
    assert row.adopted is None and rig.ext._adopted_routes == {} and row.detach.state == "refused"
    gate.write_text("go")
    text = _text(await within(turn))
    assert MOVED not in text and "the answer" in text
    assert rig.service.admission.count(KIND) == 0, "the call's slot is back at its return"
    await finish(rig)


async def test_the_answer_is_built_before_the_gate_so_its_failure_is_a_clean_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 1a, verify F1. On ``fb5e7654``: a refusal line AND a running job that also drove the run."""

    rig, turn, gate, row = await _parked(tmp_path)

    def broken(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("text")

    monkeypatch.setattr(dt, "detach_text", broken)
    assert rig.jobs_ui.request_detach()
    await asyncio.sleep(0.1)
    assert rig.notes == [NOTE.format("RuntimeError: text")]
    await _stayed(rig, turn, gate, row)


async def test_a_step_that_changed_something_and_then_raised_is_undone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 1b, verify F4 (M066/M083). On ``fb5e7654`` the client's entry for the job stays: a ghost."""

    rig, turn, gate, row = await _parked(tmp_path)

    def broken(self: DelegationJobs, entry: Any) -> None:
        raise RuntimeError("name")

    monkeypatch.setattr(DelegationJobs, "name", broken)  # adopt_entry inserts, then names
    assert rig.jobs_ui.request_detach()
    await asyncio.sleep(0.1)
    monkeypatch.undo()
    assert rig.notes == [NOTE.format("publishing it failed: RuntimeError")]
    await _stayed(rig, turn, gate, row)


async def test_a_split_that_cannot_build_the_jobs_reservation_spends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 1c, verify F5. On ``fb5e7654`` the call's reservation was spent first: one slot leaked."""

    rig, turn, gate, row = await _parked(tmp_path)

    class Unbuildable(adm.Reservation):
        def __init__(self, owner: Any, key: str, slots: int) -> None:
            raise RuntimeError("allocating the job's reservation failed")

    monkeypatch.setattr(adm, "Reservation", Unbuildable)
    assert rig.jobs_ui.request_detach()
    await asyncio.sleep(0.1)
    monkeypatch.undo()
    assert rig.notes == [NOTE.format("publishing it failed: RuntimeError")]
    await _stayed(rig, turn, gate, row)


def test_split_builds_the_destination_before_it_spends_the_source() -> None:
    admission = adm.Admission()
    source = admission.reserve("k", 4)
    built: list[int] = []

    class Watching(adm.Reservation):
        def __init__(self, owner: Any, key: str, slots: int) -> None:
            built.append(source.slots)  # the source as it is when the destination is made
            super().__init__(owner, key, slots)

    real = adm.Reservation
    adm.Reservation = Watching  # type: ignore[misc]
    try:
        moved = source.split(1)
    finally:
        adm.Reservation = real  # type: ignore[misc]
    assert built == [1] and moved.slots == 1 and source.released
    assert admission.count("k") == 1


async def test_listeners_hear_of_the_job_only_once_the_commit_is_whole(tmp_path: Path) -> None:
    """Codex 3, verify F6. On ``fb5e7654`` a listener ran inside ``JobService.adopt``: before the
    client's entry, the progress route and the slot existed, and anything it changed was not re-checked."""

    rig, turn, gate, row = await _parked(tmp_path)
    seen: list[dict[str, Any]] = []

    def listener() -> None:
        jobs = list(rig.service._jobs.values())
        if jobs and not seen:
            seen.append(
                {
                    "slot": jobs[0].reservation.slots,
                    "client_entry": jobs[0].job_id in rig.ext._delegation_jobs.info,
                    "route": row.id in rig.ext._adopted_routes,
                    "row_adopted": row.adopted is not None,
                }
            )
            rig.chrome.interrupt_pending = True  # what a listener could do mid-commit

    rig.service.add_listener(listener)
    assert rig.jobs_ui.request_detach()
    assert MOVED in _text(await within(turn))
    assert seen == [{"slot": 1, "client_entry": True, "route": True, "row_adopted": True}]
    rig.chrome.interrupt_pending = False
    gate.write_text("go")
    await finish(rig)


async def test_after_the_gate_opens_nothing_raises_into_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listener notice that fails after the move does not turn it into a refusal (guard)."""

    rig, turn, gate, _row = await _parked(tmp_path)

    def broken(self: JobService, job: Any) -> None:
        raise RuntimeError("announce")

    monkeypatch.setattr(JobService, "announce_adopted", broken, raising=False)
    assert rig.jobs_ui.request_detach()
    assert MOVED in _text(await within(turn))
    assert rig.notes == [] and [s.state for s in rig.jobs()] == ["running"]
    gate.write_text("go")
    await finish(rig)


async def test_a_slot_spent_before_the_commit_is_refused_before_the_jobs_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify F10 M056: the commit's own slot check, ahead of the task (``split`` would refuse later)."""

    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("the answer", gate=gate, partial="halfway"))
    taken: list[Any] = []
    facade = rig.ext._jobs_facade
    real_reserve = facade.reserve

    def reserve(kind: str, wanted: int = 1) -> Any:
        taken.append(real_reserve(kind, wanted))
        return taken[-1]

    facade.reserve = reserve
    turn = await parked_call(rig)
    row = row_of(rig)
    entries: list[str] = []
    loop = asyncio.get_running_loop()
    real_create = loop.create_task

    def create_task(coro: Any, **kwargs: Any) -> Any:
        entries.append(getattr(coro, "__qualname__", ""))
        return real_create(coro, **kwargs)

    (reservation,) = taken
    reservation.release()  # spent before the key
    monkeypatch.setattr(loop, "create_task", create_task)
    assert rig.jobs_ui.request_detach()
    await asyncio.sleep(0.1)
    monkeypatch.undo()
    assert rig.notes == [NOTE.format("the call's slot is spent")]
    assert "_entry" not in entries
    await _stayed(rig, turn, gate, row)
