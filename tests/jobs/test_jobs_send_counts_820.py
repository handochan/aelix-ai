"""#820 — ``/jobs send`` counts the pending results and attention notices from one set.

``JobsUi.send_now`` counted the waiting notices among the PENDING ones
(``harness.pending_notices``) and the attention notices among every UNDELIVERED
one, claimed and held included. While a boundary placed a claimed attention
notice and one result waited, it said "Sending 1 notice to the model.". Both
counts are now of the pending notices.

Pinned with the real ``JobsCommand`` and a real harness whose session write is
paused, so the claimed notice is the product's own (``prompt_notices`` claims it
and waits in ``place_custom_message``). Fails on ``1c2a4a7c``: the claimed
attention row says "Sending 1 notice", and the claimed result row raises nothing
but is pinned so the two mutants #820 names (the ``waiting > notices`` guard, the
plural of "notice") die.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs.commands import JobsCommand
from aelix_coding_agent.jobs.contract import NoticeBody
from aelix_coding_agent.tui.jobs_ui import JobsUi

from tests.jobs._sleeper import KIND
from tests.jobs.conftest import wait_for
from tests.jobs.test_attention_123 import _Held, _running
from tests.tui.test_notice_never_touches_dialogs_611 import _Chrome, _Host


async def _end_one(service: Any, title: str) -> str:
    job_id = await service.start(KIND, title=title)
    _Held.instances[-1].release.set()
    await wait_for(lambda: service.snapshot(job_id).notice_state == "pending")
    return job_id


async def _second_running(service: Any) -> _Held:
    await service.start(KIND, title="another")
    runner = _Held.instances[-1]
    await wait_for(lambda: runner.ctx is not None)
    return runner


def _pause_placement(harness: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    """Each notice placement waits at the session write until ``gate`` is set."""

    gate, entered = asyncio.Event(), asyncio.Event()
    real = harness.session.place_custom_message

    async def _paused(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await gate.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(harness.session, "place_custom_message", _paused)
    return gate, entered


async def _send(service: Any, harness: Any) -> tuple[str, JobsUi]:
    ui = JobsUi(service, chrome=_Chrome(), runtime_host=_Host(harness))
    service.send_hook = ui.send_now
    return await JobsCommand(service)("send"), ui


async def test_two_attention_notices_and_a_result_are_counted_in_the_plural(tmp_path: Path) -> None:
    service, harness, _model, _job_id, runner = await _running(tmp_path)
    other = await _second_running(service)
    await _end_one(service, "ended")
    assert runner.ctx.attention(NoticeBody("a"), brief="stuck")
    assert other.ctx.attention(NoticeBody("b"), brief="stuck")
    assert harness.pending_notices == 3
    said, ui = await _send(service, harness)
    assert said == "Sending 1 waiting result and 2 notices to the model."
    ui.close()
    for held in _Held.instances:
        held.release.set()
    await service.shutdown()


async def test_two_results_and_an_attention_notice_are_counted_in_the_plural(tmp_path: Path) -> None:
    service, harness, _model, _job_id, runner = await _running(tmp_path)
    await _end_one(service, "first")
    await _end_one(service, "second")
    assert runner.ctx.attention(NoticeBody("a"), brief="stuck")
    said, ui = await _send(service, harness)
    assert said == "Sending 2 waiting results and 1 notice to the model."
    ui.close()
    runner.release.set()
    await service.shutdown()


async def test_an_attention_notice_being_placed_is_not_counted_against_a_waiting_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, harness, _model, _job_id, runner = await _running(tmp_path)
    assert runner.ctx.attention(NoticeBody("a"), brief="stuck")
    gate, entered = _pause_placement(harness, monkeypatch)
    placing = asyncio.create_task(harness.prompt_notices())
    await entered.wait()
    assert harness.pending_notices == 0, "the premise: the attention notice is claimed"
    await _end_one(service, "ended")
    assert harness.pending_notices == 1
    said, ui = await _send(service, harness)
    assert said == "Sending 1 waiting result to the model."
    gate.set()
    await placing
    ui.close()
    runner.release.set()
    await service.shutdown()


async def test_a_result_being_placed_leaves_only_the_attention_notice_to_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, harness, _model, _job_id, runner = await _running(tmp_path)
    ended = await _end_one(service, "ended")
    gate, entered = _pause_placement(harness, monkeypatch)
    placing = asyncio.create_task(harness.prompt_notices())
    await entered.wait()
    assert harness.pending_notices == 0, "the premise: the result is claimed"
    assert service.snapshot(ended).result_waiting, "the premise: /jobs send still has a result to send"
    assert runner.ctx.attention(NoticeBody("a"), brief="stuck")
    said, ui = await _send(service, harness)
    assert said == "Sending 1 notice to the model."
    gate.set()
    await placing
    ui.close()
    runner.release.set()
    await service.shutdown()


async def test_jobs_send_with_only_an_attention_notice_pending_sends_it(tmp_path: Path) -> None:
    """Codex review of #820, L10: ``JobsCommand`` counted results before it asked the
    TUI's hook, so one pending attention notice and no result read "No results are
    waiting." and the wake stayed unforced. Fails on ``1099de2b``: that sentence."""

    service, harness, _model, _job_id, runner = await _running(tmp_path)
    ui = JobsUi(service, chrome=_Chrome(), runtime_host=_Host(harness))
    service.send_hook = ui.send_now
    try:
        assert runner.ctx.attention(NoticeBody("stuck"), brief="stuck")
        assert harness.pending_notices == 1
        assert await JobsCommand(service)("send") == "Sending 1 notice to the model."
        assert ui.force and ui.queued
    finally:
        ui.close()
        runner.release.set()
        await service.shutdown()
