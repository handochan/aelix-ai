"""#775 (#611 S3a) — the commit's order and its refusals (ADR-0298 §3; Codex r3 M3, M4, M5, N3).

The commit validates, creates the job's task — gated, never started eagerly —
FIRST, because that is the one failure that cannot be undone, publishes with an
undo for each step, takes the call's slot LAST, and only then opens the gate.
Every refusal leaves the run where it was: it goes on in the turn, ends with
its own result, and gives its slot back in the step it returns in.

The abort cases are measured on the SEQUENTIAL path — ``agent`` runs inline in
the turn task (``execution_mode="sequential"``, guarded below) — with the
TUI's real guard (``JobsUi.detach_blocked``) over the rig's real harness.

Fails on ``c91cdc7b``: there is no commit (``aelix_agents.detach``).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import detach as dt
from aelix_agents.jobs_client import KIND, DelegationJobs
from aelix_agents.tool import create_agent_tool
from aelix_coding_agent.jobs.service import JobService

from tests.agents_ext._detach_rig import (  # noqa: F401 — reap_children is the fixture
    finish,
    parked_call,
    ready,
    reap_children,
    row_of,
    script,
    tui_rig,
    within,
)
from tests.agents_ext.test_agent_background_611 import jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_stopped_child_reports_604 import _QueueSpy
from tests.agents_ext.test_tool_and_security import _text

REAL_CHILD = pytest.mark.skipif(
    sys.platform == "win32",
    reason="as test_agent_background_611: the gated rig's real child is proven on win32 by #611 S3c",
)
pytestmark = pytest.mark.usefixtures("jobs_on", "reap_children")
MOVED = "Moved to the background at the user's request"


async def _stays(rig: Any, turn: asyncio.Future[Any], gate: Path) -> str:
    """The refused run goes on in the turn: its own result, its slot back when it returns."""

    assert rig.jobs() == []
    assert rig.service.admission.count(KIND) == 1, "the call still holds its one slot"
    gate.write_text("go")
    text = _text(await within(turn))
    assert MOVED not in text and "the answer" in text
    assert rig.service.admission.count(KIND) == 0
    assert rig.ext._adopted_routes == {}
    return text


async def _rig_and_call(tmp_path: Path) -> tuple[Any, asyncio.Future[Any], Path]:
    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("the answer", gate=gate, partial="halfway"))
    turn = await parked_call(rig)
    return rig, turn, gate


@REAL_CHILD
async def test_a_task_factory_refuses(tmp_path: Path) -> None:
    rig, turn, gate = await _rig_and_call(tmp_path)
    loop = asyncio.get_running_loop()
    loop.set_task_factory(lambda lp, coro, **kw: asyncio.Task(coro, loop=lp, **kw))
    try:
        assert rig.jobs_ui.request_detach()
        await asyncio.sleep(0.05)
    finally:
        loop.set_task_factory(None)
    assert rig.notes == ["Ctrl+B: could not move it to the background now (a task factory is installed)."]
    await _stays(rig, turn, gate)
    await finish(rig)


@REAL_CHILD
async def test_a_job_task_that_cannot_be_created_spends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, turn, gate = await _rig_and_call(tmp_path)
    loop = asyncio.get_running_loop()
    real = loop.create_task

    def create_task(coro: Any, **kwargs: Any) -> Any:
        if getattr(coro, "__qualname__", "") == "_entry":
            raise RuntimeError("no task for you")
        return real(coro, **kwargs)

    monkeypatch.setattr(loop, "create_task", create_task)
    assert rig.jobs_ui.request_detach()
    await asyncio.sleep(0.05)
    monkeypatch.undo()
    assert rig.notes == [
        "Ctrl+B: could not move it to the background now (the job's task could not be created: RuntimeError)."
    ]
    assert rig.service.snapshots() == [] and rig.ext._delegation_jobs.info == {}
    assert row_of(rig).adopted is None
    await _stays(rig, turn, gate)
    await finish(rig)


@REAL_CHILD
async def test_a_publish_step_that_fails_is_undone_and_the_gate_stays_shut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, turn, gate = await _rig_and_call(tmp_path)
    tasks: list[asyncio.Task[Any]] = []
    real_adopt = JobService.adopt

    def adopt(self: JobService, job: Any, task: Any) -> None:
        tasks.append(task)
        real_adopt(self, job, task)

    def broken(self: DelegationJobs, entry: Any) -> None:
        raise RuntimeError("the client's map is broken")

    monkeypatch.setattr(JobService, "adopt", adopt)
    monkeypatch.setattr(DelegationJobs, "adopt_entry", broken)
    row = row_of(rig)
    assert rig.jobs_ui.request_detach()
    await asyncio.sleep(0.05)
    assert rig.notes == ["Ctrl+B: could not move it to the background now (publishing it failed: RuntimeError)."]
    (task,) = tasks
    assert task.done() and not task.cancelled() and task.result() is None, "the gate stayed shut"
    assert rig.service.snapshots() == [], "the job was taken back out"
    assert row.adopted is None and rig.ext._adopted_routes == {}
    await _stays(rig, turn, gate)
    await finish(rig)


@REAL_CHILD
async def test_the_task_comes_first_and_the_slot_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, turn, gate = await _rig_and_call(tmp_path)
    order: list[str] = []
    loop = asyncio.get_running_loop()
    real_create = loop.create_task

    def create_task(coro: Any, **kwargs: Any) -> Any:
        if getattr(coro, "__qualname__", "") == "_entry":
            order.append("create_task")
        return real_create(coro, **kwargs)

    def spy(name: str, real: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            order.append(name)
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(loop, "create_task", create_task)
    monkeypatch.setattr(JobService, "adopt", spy("adopt", JobService.adopt))
    monkeypatch.setattr(DelegationJobs, "adopt_entry", spy("client", DelegationJobs.adopt_entry))
    monkeypatch.setattr(JobService, "give_slot", spy("slot", JobService.give_slot))
    before = rig.service.admission.count(KIND)
    assert rig.jobs_ui.request_detach()
    text = _text(await within(turn))
    monkeypatch.undo()
    assert MOVED in text
    assert order == ["create_task", "adopt", "client", "slot"]
    assert rig.service.admission.count(KIND) == before == 1, "the slot moved, the count did not"
    gate.write_text("go")
    await finish(rig)


# === the abort state (Codex M5), on the sequential path ==============================


def test_agent_stays_a_sequential_tool() -> None:
    """Guard: ``agent`` runs inline in the turn task, so a turn cancel reaches the delegate's await."""

    tool = create_agent_tool(description="d", execute=None)  # type: ignore[arg-type]
    assert tool.execution_mode == "sequential"


@REAL_CHILD
async def test_a_non_cancelling_abort_refuses_the_commit(tmp_path: Path) -> None:
    """An extension's ``ctx.abort()`` sets the harness's latch and cancels nothing (R20)."""

    rig, turn, gate = await _rig_and_call(tmp_path)
    assert rig.jobs_ui.request_detach()
    rig.harness._abort_requested = True  # after the key, before the run woke
    await asyncio.sleep(0.05)
    assert rig.notes == ["Ctrl+B: could not move it to the background now (an abort is pending)."]
    rig.harness._abort_requested = False
    await _stays(rig, turn, gate)
    await finish(rig)


@REAL_CHILD
@pytest.mark.parametrize("first", ["ctrl_b", "esc"])
async def test_esc_and_ctrl_b_in_one_loop_turn_esc_wins(tmp_path: Path, first: str) -> None:
    """Both orders, in one loop turn, the abort through the real ``AgentHarness.abort`` (R14).

    Ctrl+B first: the key wakes the run, but the abort's cancel lands on the turn
    task before it steps and is thrown into the delegate's await — today's Esc.
    Esc first: its latch is set synchronously (``note_interrupt``), so the key is
    not taken at all.
    """

    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, script=script("the answer", gate=gate, partial="halfway"))
    queued: list[Any] = []
    rig.ext._api = _QueueSpy(rig.ext._api, queued)
    turn = await parked_call(rig)
    rig.harness._current_turn_task = turn  # the call runs in the turn task (sequential)

    def esc() -> None:
        rig.chrome.note_interrupt()
        asyncio.ensure_future(rig.harness.abort(withdraw_unanswered=True))

    if first == "ctrl_b":
        assert rig.jobs_ui.request_detach()
        esc()
    else:
        esc()
        assert not rig.jobs_ui.request_detach()
    with pytest.raises(asyncio.CancelledError):
        await within(turn)
    # Today's Esc: cancelled, with one D14 report — the call's result and an
    # owed record since #800 (ADR-0307 D1-D2), nothing on the next-turn queue.
    # ``getattr``: on the base, which has no owed table, this fails on the
    # assertion (the report sits on the queue), not on an ``AttributeError``.
    owed = getattr(rig.ext, "_owed", {})
    assert rig.jobs() == [] and len(owed) == 1 and queued == [], "today's Esc"
    assert rig.service.admission.count(KIND) == 0
    gate.write_text("go")
    await finish(rig)


@REAL_CHILD
async def test_an_interrupt_pending_at_the_commit_refuses(tmp_path: Path) -> None:
    rig, turn, gate = await _rig_and_call(tmp_path)
    assert rig.jobs_ui.request_detach()
    rig.chrome.interrupt_pending = True  # an Esc whose abort has not reached ``abort()`` yet
    await asyncio.sleep(0.05)
    assert rig.notes == ["Ctrl+B: could not move it to the background now (an interrupt is pending)."]
    rig.chrome.interrupt_pending = False
    await _stays(rig, turn, gate)
    await finish(rig)


@REAL_CHILD
async def test_one_attempt_per_call(tmp_path: Path) -> None:
    rig, turn, gate = await _rig_and_call(tmp_path)
    call = row_of(rig).detach
    assert call.state == "parked"
    rig.chrome.interrupt_pending = True
    assert not rig.jobs_ui.request_detach(), "the key's own check refuses first"
    rig.chrome.interrupt_pending = False
    rig.harness._abort_requested = True
    assert rig.service.detach.request(), "the offer itself takes it; the commit refuses"
    await asyncio.sleep(0.05)
    rig.harness._abort_requested = False
    assert call.state == "refused" and call.refusal == "an abort is pending"
    assert ready(rig) == 0 and not rig.service.detach.request() and not rig.jobs_ui.request_detach()
    await _stays(rig, turn, gate)
    await finish(rig)


def test_commit_outside_claimed_is_refused_without_side_effects() -> None:
    calls: list[Any] = []
    call = dt.CallDetach(adopt=lambda c, inner: calls.append(inner), offer=lambda c: lambda: None)
    for state in ("idle", "parked", "left", "refused", "adopted"):
        call.state = state
        assert call.commit(object()) is None
    assert calls == []
