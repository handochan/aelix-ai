"""#698 (ADR-0290) — Esc on ``/agents run`` with a stale cancel count on the input loop's task.

An extension's ``input`` or ``before_agent_start`` hook, or a command, runs on that task, the one
the whole session runs on. A ``TaskGroup`` it caught a child's failure from leaves the count up on
3.11 and 3.12, for the rest of the session, and ``_run_cancellable`` read that as a teardown: Esc
ended the TUI with a traceback. It reads a rise across its wait now. A real teardown still ends it.

Through the real ``run_tui`` and a real runtime with a stub child, as the #604 tests next door.

No ``__init__.py`` in ``tests/tui`` — local convention.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.prompt_file import _pid_is_live

from tests.harness.test_stale_cancel_count_698 import _simulated, _task_group
from tests.tui.test_interrupt_reaches_long_work_604 import (
    _DelegatingHarness,
    _OneProfileRuntime,
    _real_runtime,
    _the_live_child,
)
from tests.tui.test_run_tui_smoke import (
    _harness_chrome,
    _launch,
    _quit_within,
    _spy_commits,
    _wait,
)

_MAKERS = {"simulated": _simulated, "task_group": _task_group}


class _LeavesItOnTheFirstPrompt(_DelegatingHarness):
    """A first prompt that leaves the stale count, as a hook on it would, and returns."""

    def __init__(self, subagents: _OneProfileRuntime, how: str) -> None:
        super().__init__(subagents)
        self.how = how
        self.first_done = False

    async def prompt(
        self, text: str, *, source: str = "interactive", images: Any = None
    ) -> list[Any]:
        self.prompts.append((text, source))
        if not self.first_done:
            await _MAKERS[self.how]()
            self.first_done = True
        return []


def _stale_on(task: asyncio.Task[Any], how: str) -> None:
    expected = 0 if how == "task_group" and sys.version_info >= (3, 13) else 1
    assert task.cancelling() == expected, "the stale count was never made"


@pytest.mark.parametrize("how", list(_MAKERS))
async def test_esc_on_a_running_agents_run_with_a_stale_count_stops_the_command_alone(
    tmp_path: Path, how: str
) -> None:
    impl = _real_runtime(tmp_path)
    seam = _OneProfileRuntime(impl)
    harness = _LeavesItOnTheFirstPrompt(seam, how)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome)
        try:
            await _wait(lambda: chrome.app.is_running)
            pipe.send_text("first\n")
            await _wait(lambda: harness.first_done, what="the first prompt")
            _stale_on(task, how)

            pipe.send_text("/agents run scout do the long thing\n")
            pid = await _the_live_child(impl)
            await _wait(lambda: chrome.running, what="the command to mark the TUI running")
            pipe.send_text("\x1b")

            await _wait(lambda: not _pid_is_live(pid), what=f"child {pid} to be reaped")
            await _wait(
                lambda: any("stopped" in c for c in commits),
                what="the stopped line",
                detail=lambda: f"commits: {commits!r} task: {task!r}",
            )
            await _wait(lambda: not chrome.running, what="running to come back down")
            assert not task.done(), "Esc ended the TUI"

            pipe.send_text("still here\n")
            await _wait(lambda: ("still here", "interactive") in harness.prompts)
            pipe.send_text("/quit\n")
            code = await _quit_within(task)
        finally:
            harness._released.set()
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            await impl.stop_all()
    assert code == 0


@pytest.mark.parametrize("how", list(_MAKERS))
async def test_a_teardown_cancel_that_meets_an_interrupt_with_a_stale_count_still_cancels_the_loop(
    tmp_path: Path, how: str
) -> None:
    impl = _real_runtime(tmp_path)
    harness = _LeavesItOnTheFirstPrompt(_OneProfileRuntime(impl), how)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        try:
            await _wait(lambda: chrome.app.is_running)
            pipe.send_text("first\n")
            await _wait(lambda: harness.first_done, what="the first prompt")
            _stale_on(task, how)

            pipe.send_text("/agents run scout do the long thing\n")
            pid = await _the_live_child(impl)
            await _wait(lambda: chrome.running, what="the command to mark the TUI running")
            interrupt = chrome.on_interrupt
            assert interrupt is not None

            interrupt()
            task.cancel()

            # An anti-hang bound, not a timing claim (the #604 test next door).
            done, _pending = await asyncio.wait({task}, timeout=30)
            assert task in done, "the input loop swallowed the teardown's cancel"
            assert task.cancelled()
            await _wait(lambda: not _pid_is_live(pid), what=f"child {pid} to be reaped")
        finally:
            harness._released.set()
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            await impl.stop_all()
