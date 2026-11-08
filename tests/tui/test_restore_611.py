"""A restored session's waiting results: one line, no turn, ``/jobs send`` delivers (#722, owner OQ7).

Owner, 2026-11-07 (OQ7 "권고대로"): the TUI does not start a turn for restored
notices; a line says they are waiting, and ``/jobs send`` sends them now.
Fails on main ``a385c64a``: nothing restores. Naive: posting a restored notice
like a live one wakes the idle TUI into a turn the user never asked for
(QwenLM/qwen-code#14273 is the opposite failure: nothing at all).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.commands import JobsCommand
from aelix_coding_agent.tui.jobs_ui import JobsUi, restored_line

from tests.jobs import _sleeper
from tests.jobs.conftest import (
    RecordingModel,
    _reap_sleepers,  # noqa: F401 — autouse: reap the children
    fast_service,
    make_harness,
    make_session,
    reopen,
)
from tests.tui.test_notice_never_touches_dialogs_611 import _Chrome, _Host


@pytest.fixture(autouse=True)
def _active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(activation, "ACTIVE", True)


async def _finished_job_session(tmp_path: Path) -> None:
    harness = make_harness(await make_session(tmp_path))
    service = fast_service()
    _sleeper.register(service)
    await service.bind(harness)
    await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    while harness.pending_notices == 0:
        await asyncio.sleep(0.02)
    await service.shutdown()  # the process ends before the notice is placed


async def test_restored_notices_start_no_turn_and_show_the_line(tmp_path: Path) -> None:
    await _finished_job_session(tmp_path)
    model = RecordingModel()
    harness = make_harness(await reopen(tmp_path), model)
    service = fast_service()
    _sleeper.register(service)
    await service.bind(harness, reason="resume")
    chrome = _Chrome()
    lines: list[str] = []
    ui = JobsUi(service, chrome=chrome, runtime_host=_Host(harness), commit=lines.append)
    ui.parked = True
    ui.on_service_change()
    assert lines == [], "not before the banner and the replayed transcript"
    ui.ready = True
    ui.on_service_change()
    assert lines == [restored_line(1)]
    assert lines[0] == (
        "◆ 1 background result is waiting. It goes to the model with your next message, "
        "or run /jobs send to send it now."
    ), "singular (verify N3)"
    assert restored_line(2).startswith("◆ 2 background results are waiting. They go")
    assert chrome.queue == [] and ui.gates() == "G7: only restored results (they wait for /jobs send)"
    ui.on_service_change()
    assert lines == [restored_line(1)], "once"
    assert model.contexts == []

    # They go with the next message.
    await harness.prompt("next")
    assert any("sleeper said" in t for t in model.contexts[0])
    ui.close()


async def test_jobs_send_delivers_now(tmp_path: Path) -> None:
    await _finished_job_session(tmp_path)
    model = RecordingModel()
    harness = make_harness(await reopen(tmp_path), model)
    service = fast_service()
    _sleeper.register(service)
    await service.bind(harness, reason="resume")
    chrome = _Chrome()
    ui = JobsUi(service, chrome=chrome, runtime_host=_Host(harness))
    service.send_hook = ui.send_now
    said = await JobsCommand(service)("send")
    assert said == "Sending 1 waiting result to the model."
    assert len(chrome.queue) == 1
    ui.parked = True
    assert ui.take(), "forced: restored results go when asked"
    result = await harness.prompt_notices()
    assert result.started and any("sleeper said" in t for t in model.contexts[0])
    assert await JobsCommand(service)("send") == "No results are waiting."
    ui.close()


def test_restored_line_plural() -> None:
    assert restored_line(2).startswith("◆ 2 background results are waiting.")
