"""#596 (ADR-0287) — a ``!`` command Entered while a turn runs runs at once.

Before #596 the chrome steered the line to the model as text. pi checks for
``!`` before its streaming-steer branch and runs it; Aelix now does too, and
holds what the model gets of a ``!`` command (``record_user_bash``) until
nothing runs, as pi holds ``_pendingBashMessages`` until its run ends: written
mid-turn, the record could land between a tool call and its result.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_coding_agent.cli.repl import UserBashRun
from aelix_coding_agent.tui import shell as tui_shell
from aelix_coding_agent.tui.shell import _HeldShellRecords
from test_run_tui_smoke import (
    FakeHarness,
    _harness_chrome,
    _launch,
    _quit_within,
    _spy_commits,
    _wait,
)


def _run(command: str, *, transient: bool = False) -> UserBashRun:
    return UserBashRun(command=command, output="out\n", exit_code=0, exclude_from_context=transient)


# === the holder ================================================================


async def test_a_record_waits_while_anything_runs_and_lands_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[str] = []

    async def _record(harness: object, run: UserBashRun) -> None:
        recorded.append(run.command)

    monkeypatch.setattr(tui_shell, "record_user_bash", _record)
    running = {"on": True}
    held = _HeldShellRecords(running=lambda: running["on"])
    harness = SimpleNamespace(session=None, is_idle=True)

    await held.record_or_hold(harness, _run("first"))
    await held.record_or_hold(harness, _run("second"))
    await held.settle()
    assert recorded == []  # the turn is still running

    running["on"] = False
    harness.is_idle = False  # an extension's turn, or a compaction
    await held.settle()
    assert recorded == []

    harness.is_idle = True
    await held.settle()
    assert recorded == ["first", "second"]

    await held.record_or_hold(harness, _run("idle"))
    assert recorded == ["first", "second", "idle"]  # nothing runs: at once


async def test_a_double_bang_is_never_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[str] = []

    async def _record(harness: object, run: UserBashRun) -> None:
        recorded.append(run.command)

    monkeypatch.setattr(tui_shell, "record_user_bash", _record)
    held = _HeldShellRecords(running=lambda: False)
    await held.record_or_hold(SimpleNamespace(session=None), _run("secret", transient=True))
    await held.settle()
    assert recorded == []


async def test_a_record_for_a_session_left_meanwhile_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[str] = []

    async def _record(harness: object, run: UserBashRun) -> None:
        recorded.append(run.command)

    monkeypatch.setattr(tui_shell, "record_user_bash", _record)
    running = {"on": True}
    held = _HeldShellRecords(running=lambda: running["on"])
    harness = SimpleNamespace(session=object())
    await held.record_or_hold(harness, _run("old"))
    harness.session = object()  # /new or /resume while it was held
    running["on"] = False
    await held.settle()
    assert recorded == []


async def test_a_failing_record_does_not_reach_the_input_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _record(harness: object, run: UserBashRun) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(tui_shell, "record_user_bash", _record)
    held = _HeldShellRecords(running=lambda: False)
    await held.record_or_hold(SimpleNamespace(session=None), _run("x"))  # does not raise
    await held.settle()


async def test_settle_waits_for_a_record_being_written(monkeypatch: pytest.MonkeyPatch) -> None:
    # The input loop's ``settle`` right before ``set_running(True)``: a record
    # already being written finishes before the turn is marked running.
    gate = asyncio.Event()
    order: list[str] = []

    async def _slow(harness: object, run: UserBashRun) -> None:
        order.append("record start")
        await gate.wait()
        order.append("record end")

    monkeypatch.setattr(tui_shell, "record_user_bash", _slow)
    held = _HeldShellRecords(running=lambda: False)
    writer = asyncio.create_task(held.record_or_hold(SimpleNamespace(session=None), _run("x")))
    await _wait(lambda: order == ["record start"], what="the record to start")

    async def _turn() -> None:
        await held.settle()
        order.append("turn starts")

    turn = asyncio.create_task(_turn())
    await asyncio.sleep(0)
    assert order == ["record start"]
    gate.set()
    await asyncio.gather(writer, turn)
    assert order == ["record start", "record end", "turn starts"]


# === run_tui end to end ========================================================


class _HoldingHarness(FakeHarness):
    """A turn that runs until the test lets it end; steer is recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.steers: list[str] = []

    async def prompt(self, text: str, *, source: str = "interactive", images: Any = None):
        self.prompts.append((text, source))
        await self.release.wait()
        return []

    async def steer(self, text: str) -> None:
        self.steers.append(text)


async def test_bang_mid_turn_runs_now_and_reaches_the_model_after_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _HoldingHarness()
    ran: list[tuple[str, bool, bool]] = []
    recorded: list[tuple[str, bool]] = []
    chrome_ref: dict[str, Any] = {}
    gate = asyncio.Event()

    async def _fake_run(h: object, command: str, *, exclude_from_context: bool, cwd: str) -> UserBashRun:
        ran.append((command, exclude_from_context, chrome_ref["chrome"].running))
        if command == "echo MIDTURN":
            await gate.wait()  # still running: the status row says so
        return UserBashRun(command, f"{command} output\n", 0, exclude_from_context)

    async def _fake_record(h: object, run: UserBashRun) -> None:
        recorded.append((run.command, chrome_ref["chrome"].running))

    monkeypatch.setattr(tui_shell, "run_user_bash", _fake_run)
    monkeypatch.setattr(tui_shell, "record_user_bash", _fake_record)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("write a long answer\n")
        await _wait(lambda: chrome.running and bool(harness.prompts), what="the turn to start")

        pipe.send_text("!echo MIDTURN\n")
        await _wait(lambda: len(ran) == 1, what="the ! command to run during the turn")
        assert any("echo MIDTURN  (running…)" in v for v in chrome._status.values())
        gate.set()
        # The command line and its output land as ONE commit, so an answer
        # streaming meanwhile cannot come between them.
        await _wait(
            lambda: any("! echo MIDTURN" in c and "echo MIDTURN output" in c for c in commits),
            what="the command and its output committed together",
        )
        assert not any("running…" in v for v in chrome._status.values())
        pipe.send_text("!!echo QUIET\n")
        await _wait(lambda: len(ran) == 2, what="the !! command to run during the turn")
        await asyncio.sleep(0.1)
        assert ran == [("echo MIDTURN", False, True), ("echo QUIET", True, True)]
        assert harness.steers == []  # not steered to the model as text
        assert recorded == []  # held while the turn runs

        harness.release.set()
        await _wait(lambda: recorded == [("echo MIDTURN", False)], what="the record after the turn")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert harness.prompts == [("write a long answer", "interactive")]
    assert recorded == [("echo MIDTURN", False)]  # the !! one never
