"""The TUI waker: a live result wakes an idle parent at its park, through every gate (#722, Codex r1 #7).

Fails on main ``a385c64a``: no waker. Naive: waking only on ``NoticeQueued``
misses a notice that arrived while the turn's ``agent_end`` or a compaction
still ran (the harness was busy then, and nothing re-checks at the park), so
the result sits until the user types (openai/codex#47762's shape).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.tui.jobs_ui import JobsUi

from tests.jobs import _sleeper
from tests.jobs.conftest import (
    RecordingModel,
    _reap_sleepers,  # noqa: F401 — autouse: reap the children
    fast_service,
    make_harness,
    make_session,
    wait_for,
)
from tests.tui.test_notice_never_touches_dialogs_611 import _Chrome, _Host, _notice


@pytest.fixture(autouse=True)
def _active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(activation, "ACTIVE", True)


async def test_notice_arriving_during_agent_end_or_compaction_wakes_at_park(tmp_path: Path) -> None:
    model = RecordingModel()
    harness = make_harness(await make_session(tmp_path), model)
    service = fast_service()
    _sleeper.register(service)
    await service.bind(harness)
    chrome = _Chrome()
    ui = JobsUi(service, chrome=chrome, runtime_host=_Host(harness))

    posted: list[Any] = []

    async def late(_event: Any, _ctx: Any) -> None:
        # the turn's last boundary is behind it; the harness is still busy
        if not posted:
            job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
            await wait_for(lambda: harness.pending_notices == 1)
            posted.append(job_id)

    harness.hooks.on("agent_end", late)
    ui.parked = False  # the input loop is inside the typed turn
    await harness.prompt("hi")
    assert harness.pending_notices == 1 and chrome.queue == [], "busy and not parked: no wake yet"
    ui.parked = True  # the loop comes back to get_input
    assert ui.maybe_wake() and len(chrome.queue) == 1
    assert ui.take()
    await harness.prompt_notices()
    assert any("sleeper said" in t for t in model.contexts[-1])
    ui.close()
    await service.shutdown()


async def test_an_idle_parked_tui_is_woken_by_a_live_result(tmp_path: Path) -> None:
    harness = make_harness(await make_session(tmp_path))
    service = fast_service()
    _sleeper.register(service)
    await service.bind(harness)
    chrome = _Chrome()
    ui = JobsUi(service, chrome=chrome, runtime_host=_Host(harness))
    ui.parked = True
    await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.05})
    await wait_for(lambda: len(chrome.queue) == 1)
    assert ui.take()
    ui.close()
    await service.shutdown()


async def test_held_stopped_or_unwinding_gates(tmp_path: Path) -> None:
    harness = make_harness(await make_session(tmp_path))
    chrome = _Chrome()
    host = _Host(harness)
    ui = JobsUi(fast_service(), chrome=chrome, runtime_host=host)
    ui.parked = True
    ui.force = True
    harness.hold_turns("route undecided")
    harness.post_notice(_notice())
    assert ui.gates() == "G3: held or disposed" and chrome.queue == []
    harness.hold_turns(None)  # TurnsReleased re-checks
    assert len(chrome.queue) == 1
    ui.take()
    host.stopped = "stopped"  # type: ignore[assignment]
    assert ui.gates() == "G3: stopped or reloading"
    ui.close()


async def test_a_turn_that_places_nothing_does_not_spin(tmp_path: Path) -> None:
    harness = make_harness(await make_session(tmp_path))
    chrome = _Chrome()
    ui = JobsUi(fast_service(), chrome=chrome, runtime_host=_Host(harness))
    ui.parked = True
    ui.force = True
    harness.hold_turns("x")
    harness.post_notice(_notice())
    ui.force = False
    result = await harness.prompt_notices()  # refused: held
    ui.after_turn(result)
    harness.hold_turns(None)
    assert ui.stalled is False, "released turns clear the stall"
    ui.after_turn(type("R", (), {"started": True, "placed": 0})())
    assert ui.stalled and ui.gates() is not None
    await asyncio.sleep(0)
    ui.close()


# === #722 fix round 1 =========================================================


def _inbox_rig() -> Any:
    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions

    from tests.runtime.test_failed_rebuild_663 import MODEL, _Rig

    class _InboxRig(_Rig):
        ui: Any = None

        def __init__(self, tmp: Path) -> None:
            super().__init__(tmp)
            self.during: list[str | None] = []

        def harness(self, session: Any) -> AgentHarness:
            return AgentHarness(
                AgentHarnessOptions(
                    model=MODEL,
                    stream_fn=self._stream,
                    session=session,
                    cwd=str(self.tmp),
                    settings_manager=self.settings,
                    notice_inbox=True,
                )
            )

        async def factory(self, session: Any, *, reload_seed: Any = None) -> AgentHarness:
            if self.ui is not None:
                self.during.append(self.ui.gates(at_dequeue=True))
            return await super().factory(session, reload_seed=reload_seed)

    return _InboxRig


async def test_the_waker_wakes_after_a_reload_and_after_a_failed_one(tmp_path: Path) -> None:
    """Verify B1: after any ``/reload`` the waker never woke again — G3 read the
    latest reload's ``reload_phase``, which stays ``done`` (or the failed step).
    G3 now holds only while a reload or session change runs (``in_transition``).

    Fails on ``a80e81fa``: ``gates()`` answered "G3: stopped or reloading" after
    the reload and no wake was queued.
    """

    from aelix_agent_core.runtime import RebuildFailedError

    from tests.runtime.test_failed_rebuild_663 import _once

    rig = await _inbox_rig().create(tmp_path)
    service = fast_service()
    _sleeper.register(service)
    await service.bind(rig.runtime.harness)
    chrome = _Chrome()
    ui = JobsUi(service, chrome=chrome, runtime_host=rig.runtime)
    rig.ui = ui

    await rig.runtime.reload()
    assert rig.runtime.reload_phase == "done" and not rig.runtime.in_transition
    assert rig.during == ["G3: stopped or reloading"], "held while the reload ran"
    await service.bind(rig.runtime.harness)
    ui.parked = True
    await service.start(_sleeper.KIND, title="after a reload", params={"seconds": 0.05})
    await wait_for(lambda: len(chrome.queue) == 1)
    assert ui.take()
    await rig.runtime.harness.prompt_notices()

    rig.fail = _once(RuntimeError("the build broke"))
    with pytest.raises(RebuildFailedError):
        await rig.runtime.reload()
    assert rig.runtime.reload_phase == "build" and rig.runtime.stopped is None
    await service.bind(rig.runtime.harness)
    chrome.queue.clear()
    ui.parked = True
    await service.start(_sleeper.KIND, title="after a failed reload", params={"seconds": 0.05})
    await wait_for(lambda: len(chrome.queue) == 1)
    assert ui.take()
    ui.close()
    await service.shutdown()


async def test_an_extension_notice_wakes_counts_and_is_sent(tmp_path: Path) -> None:
    """Verify S3: a notice an extension posts is live — it wakes an idle TUI, the
    footer counts it, and ``/jobs send`` sends it.

    Fails on ``a80e81fa``: G7 read only job snapshots ("only restored results"),
    the footer showed nothing, and ``/jobs send`` said "No results are waiting."
    """

    from aelix_coding_agent.jobs.commands import JobsCommand

    harness = make_harness(await make_session(tmp_path))
    service = fast_service()
    await service.bind(harness)
    chrome = _Chrome()
    ui = JobsUi(service, chrome=chrome, runtime_host=_Host(harness))
    ui.parked = True
    assert service.post_extension_notice("poke-ext", "poke-1", "poked")
    assert not service.post_extension_notice("poke-ext", "poke-1", "poked"), "once per id"
    assert len(chrome.queue) == 1, "woken"
    assert ui.footer(120) == "⚙ 1 result waiting"
    assert service.has_live_pending()
    ui.take()
    service.send_hook = None
    said = await JobsCommand(service)("send")
    assert said == "Sent 1 waiting result to the model."
    assert service.extension_waiting() == 0 and ui.footer(120) is None
    ui.close()


async def test_an_esc_still_unwinding_holds_the_wake(tmp_path: Path) -> None:
    """Verify V09: G5. On the real harness an unwinding Esc is also a turn still
    running, so G1 holds it first; the stand-in below is idle with its withdrawal
    still pending, which only G5 refuses."""

    model = RecordingModel()
    harness = make_harness(await make_session(tmp_path), model)
    chrome = _Chrome()
    ui = JobsUi(fast_service(), chrome=chrome, runtime_host=_Host(harness))
    ui.parked = True
    ui.force = True
    harness.post_notice(_notice())

    class _Unwinding:
        is_idle = True
        turns_held = None
        disposed = False
        withdraw_pending = True
        pending_notices = 1

        def add_notice_listener(self, _listener: Any) -> Any:
            return lambda: None

    host = _Host(harness)
    host.harness = _Unwinding()  # type: ignore[assignment]
    ui2 = JobsUi(fast_service(), chrome=_Chrome(), runtime_host=host)
    ui2.parked = True
    ui2.force = True
    assert ui2.gates() == "G5: an Esc is unwinding"
    ui.close()
    ui2.close()
