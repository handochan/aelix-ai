"""#689 — a cancel that lands as the update check finishes ends ``run_tui``'s update notice.

``_commit_update_notice`` waited with ``asyncio.wait_for(asyncio.shield(check), 4.0)``.
On Python 3.11, ``wait_for`` returns the result instead of raising when the cancel
lands in the loop turn after the awaited future is done, so the cancel was swallowed
and ``run_tui`` went on running: measured on base ``80ff2cc2`` with the forced window
below, 3.11.15 left the notice uncancelled and ``run_tui`` still running 5 s later
(3.12 and 3.14 ended). It is the candidate cause of the 240 s hangs in #689's CI dumps,
reproduced only with the cancel forced into that loop turn.

The window is forced the same way in each test: a done callback that cancels the
waiter is added to the check after ``_commit_update_notice`` started waiting on it, so
it runs in the loop turn the check finishes in, after the callback the wait added.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest
from aelix_coding_agent.tui import shell as _shell

from tests.tui.test_run_tui_smoke import _harness_chrome, _launch, _quit_within, _wait


def _gated_check(loop: asyncio.AbstractEventLoop) -> tuple[asyncio.Future[None], asyncio.Task[Any]]:
    gate: asyncio.Future[None] = loop.create_future()

    async def check() -> None:
        await gate

    return gate, asyncio.ensure_future(check())


async def test_a_cancel_landing_as_the_check_finishes_cancels_the_notice() -> None:
    gate, update = _gated_check(asyncio.get_running_loop())
    commits: list[object] = []
    notice = asyncio.ensure_future(_shell._commit_update_notice(update, commits.append))
    await asyncio.sleep(0)  # the notice now waits on the check
    update.add_done_callback(lambda _t: notice.cancel())
    gate.set_result(None)
    await asyncio.wait({notice}, timeout=5.0)
    assert notice.cancelled(), (
        f"py{sys.version_info[:2]}: the cancel was swallowed and the notice returned"
    )
    assert commits == []


async def test_a_cancel_while_the_check_runs_cancels_the_notice() -> None:
    gate, update = _gated_check(asyncio.get_running_loop())
    notice = asyncio.ensure_future(_shell._commit_update_notice(update, [].append))
    await asyncio.sleep(0)
    notice.cancel()
    await asyncio.wait({notice}, timeout=5.0)
    assert notice.cancelled()
    # The check is left running, as the shield left it: its answer is cached for
    # the next launch.
    assert not update.done()
    gate.set_result(None)
    await update


async def test_a_check_that_outlasts_the_hold_is_left_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_coding_agent.update_check as uc

    monkeypatch.setattr(uc, "FETCH_TIMEOUT_S", 0.05)
    gate, update = _gated_check(asyncio.get_running_loop())
    commits: list[object] = []
    await asyncio.wait_for(_shell._commit_update_notice(update, commits.append), 5.0)
    assert commits == []
    assert not update.done(), "the hold ended by cancelling the check"
    gate.set_result(None)
    await update


async def test_the_wait_answers_none_for_a_check_still_running() -> None:
    gate, update = _gated_check(asyncio.get_running_loop())
    assert await _shell._update_check_answer(update, 0.01) is None
    assert not update.done()
    gate.set_result(None)
    await update


async def test_a_check_that_ends_cancelled_says_nothing() -> None:
    """The check's own cancel is no answer, like every other way it can fail: the
    notice says nothing and returns. ``wait_for(shield(...))`` passed it on as the
    caller's own cancel, which would have ended ``run_tui``; nothing cancels the
    check today."""

    _gate, update = _gated_check(asyncio.get_running_loop())
    commits: list[object] = []
    notice = asyncio.ensure_future(_shell._commit_update_notice(update, commits.append))
    await asyncio.sleep(0)
    update.cancel()
    await asyncio.wait({notice}, timeout=5.0)
    assert notice.done()
    assert not notice.cancelled(), "the check's cancel ended the notice as a cancel"
    assert notice.exception() is None
    assert commits == []


async def test_run_tui_ends_on_a_cancel_landing_as_the_check_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    holder: dict[str, Any] = {}

    def start(_settings: object) -> asyncio.Task[Any]:
        holder["gate"], holder["update"] = _gated_check(loop)
        return holder["update"]

    entered: list[bool] = []
    real_notice = _shell._commit_update_notice

    async def notice(task: Any, commit: Any) -> None:
        entered.append(True)
        await real_notice(task, commit)

    monkeypatch.setattr(_shell, "_start_update_check", start)
    monkeypatch.setattr(_shell, "_commit_update_notice", notice)

    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running and bool(entered))
        await asyncio.sleep(0)
        holder["update"].add_done_callback(lambda _t: task.cancel())
        holder["gate"].set_result(None)
        done, _ = await asyncio.wait({task}, timeout=5.0)
        if not done:
            pipe.send_text("/quit\n")
            await _quit_within(task)
    assert done, f"py{sys.version_info[:2]}: run_tui swallowed the cancel and kept running"
    assert task.cancelled()
