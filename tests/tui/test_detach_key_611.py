"""#775 (#611 S3a) — the Ctrl+B key (ADR-0298; owner OQ-S3-3, 2026-11-12: "Ctrl+B만 + 가이드에 '두 번' (권장)").

Ctrl+B is bound only while background jobs are on (``run_tui`` builds the
chrome with ``detach_key`` when it is handed the job service). It asks the host
only when the input has focus, no modal and no completion menu is open, no
interrupt is pending and EXACTLY ONE running call is ready (opencode's gate,
Qwen's "exactly one"). Otherwise prompt_toolkit's emacs ``backward-char`` takes
the key, as it always did. An extension's ``ctrl+b`` is skipped while it is
bound, as any built-in collision is (#20's "built-ins win").

The keys go through a running chrome's real key processor (pipe input), so the
emacs fall-through is the real one. The modal case runs 25 times in one
process (the TUI key race, memory 2026-11-05).

Fails on ``c91cdc7b``: ``AelixChrome`` takes no ``detach_key``.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from _polling import quit_within, wait_until  # sibling helper (pytest prepend import mode)
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.tui import shell as tui_shell
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.commands import _DETACH_HOTKEY, _hotkeys_handler
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from tests.tui.test_run_tui_smoke import FakeHarness, FakeRuntime

_CTRL_B = "\x02"


def _bound(chrome: AelixChrome, key: str) -> list[Any]:
    kb = chrome.app.key_bindings
    assert kb is not None
    return [b for b in kb.bindings if tuple(getattr(k, "value", str(k)) for k in b.keys) == (key,)]


class _Host:
    """``JobsUi``'s two answers, counted."""

    def __init__(self, offers: int = 1) -> None:
        self.offers = offers
        self.requests = 0
        self.taken = True

    def ready(self) -> bool:
        return self.offers == 1

    def request(self) -> bool:
        self.requests += 1
        return self.taken


@contextlib.asynccontextmanager
async def _running(*, detach_key: bool = True, shortcuts: dict[str, Any] | None = None) -> AsyncIterator[Any]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome(
            console=Console(file=io.StringIO(), force_terminal=True, width=80),
            pt_input=pipe,
            pt_output=DummyOutput(),
            detach_key=detach_key,
            extension_shortcuts=(lambda: shortcuts) if shortcuts is not None else None,
        )
        task = asyncio.ensure_future(chrome.run())
        try:
            await wait_until(lambda: chrome.app.is_running, what="the chrome to run")
            chrome.set_running(True)
            chrome.buffer.text = "abcd"
            chrome.buffer.cursor_position = 4
            yield chrome, pipe
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=3)


def _wire(chrome: AelixChrome, host: _Host) -> None:
    chrome.detach_ready = host.ready
    chrome.on_detach = host.request


async def _press(chrome: AelixChrome, pipe: Any, keys: str = _CTRL_B) -> None:
    pipe.send_text(keys)
    for _ in range(10):
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)


def test_c_b_is_bound_only_with_jobs_on() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        assert len(_bound(AelixChrome(detach_key=True), "c-b")) == 1
        assert _bound(AelixChrome(), "c-b") == []


async def test_the_real_run_tui_binds_it_and_wires_the_jobs_ui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run_tui`` itself builds the chrome with the key when it is handed the job service."""

    from aelix_coding_agent.jobs.service import JobService

    built: list[AelixChrome] = []
    real = tui_shell.AelixChrome

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):

        def chrome(**kwargs: Any) -> AelixChrome:
            kwargs.pop("history_path", None)
            made = real(**kwargs, pt_input=pipe, pt_output=DummyOutput())
            built.append(made)
            return made

        monkeypatch.setattr(tui_shell, "AelixChrome", chrome)
        monkeypatch.setattr(activation, "ACTIVE", True)
        service = JobService()
        harness = FakeHarness()
        harness.pending_notices = 0  # type: ignore[attr-defined]
        task = asyncio.ensure_future(
            tui_shell.run_tui(
                FakeRuntime(harness),  # type: ignore[arg-type]
                cwd=str(tmp_path),
                install_signal_handlers=False,
                jobs=service,
            )
        )
        await wait_until(lambda: bool(built) and built[0].app.is_running, what="run_tui's chrome")
        (made,) = built
        assert len(_bound(made, "c-b")) == 1
        assert made.detach_ready is not None and made.on_detach is not None
        assert service.detach.guard is not None, "the TUI is the host every commit asks"
        # Esc/Ctrl+C's interrupt sets the latch before it schedules the abort.
        made.set_running(True)
        assert made.on_interrupt is not None and not made.interrupt_pending
        made.on_interrupt()
        assert made.interrupt_pending
        assert service.detach.blocked() == "an interrupt is pending"
        made.set_running(False)
        pipe.send_text("/quit\n")
        await quit_within(task)
    assert service.detach.guard is None, "closed with the TUI"


@pytest.mark.skipif(sys.platform == "win32", reason="run_tui installs no SIGTERM handler on win32")
async def test_a_sigterm_sets_the_latch_before_it_aborts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify F10 M115: ``run_tui``'s SIGTERM/SIGHUP handler is one of the three places that latch."""

    import signal

    from aelix_coding_agent.jobs.service import JobService

    built: list[AelixChrome] = []
    handlers: dict[int, Any] = {}
    real = tui_shell.AelixChrome
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, cb, *a: handlers.__setitem__(sig, cb))
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: handlers.pop(sig, None) is not None)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):

        def chrome(**kwargs: Any) -> AelixChrome:
            kwargs.pop("history_path", None)
            made = real(**kwargs, pt_input=pipe, pt_output=DummyOutput())
            built.append(made)
            return made

        monkeypatch.setattr(tui_shell, "AelixChrome", chrome)
        monkeypatch.setattr(activation, "ACTIVE", True)
        harness = FakeHarness()
        harness.pending_notices = 0  # type: ignore[attr-defined]
        task = asyncio.ensure_future(
            tui_shell.run_tui(
                FakeRuntime(harness),  # type: ignore[arg-type]
                cwd=str(tmp_path),
                install_signal_handlers=True,
                jobs=JobService(),
            )
        )
        await wait_until(lambda: bool(built) and built[0].app.is_running, what="run_tui's chrome")
        (made,) = built
        latched: list[bool] = []
        real_note = made.note_interrupt
        made.note_interrupt = lambda: (latched.append(True), real_note())[1]  # type: ignore[method-assign]
        handlers[signal.SIGTERM]()
        assert latched == [True]
        await quit_within(task)


async def test_one_ready_offer_takes_the_key_and_the_cursor_stays() -> None:
    async with _running() as (chrome, pipe):
        host = _Host()
        _wire(chrome, host)
        await _press(chrome, pipe)
        assert host.requests == 1 and chrome.buffer.cursor_position == 4


@pytest.mark.parametrize("why", ["none", "two", "no host", "interrupt pending", "not taken"])
async def test_otherwise_ctrl_b_moves_the_cursor_left(why: str) -> None:
    async with _running() as (chrome, pipe):
        host = _Host(offers={"none": 0, "two": 2}.get(why, 1))
        if why != "no host":
            _wire(chrome, host)
        if why == "interrupt pending":
            chrome.note_interrupt()
        if why == "not taken":
            host.taken = False  # the offer went between the filter and the handler
        await _press(chrome, pipe)
        assert chrome.buffer.cursor_position == 3, "emacs backward-char"
        assert host.requests == (1 if why == "not taken" else 0)


async def test_without_input_focus_ctrl_b_asks_nothing() -> None:
    """Verify F10 M100: the filter's focus check, on its own (no modal is open)."""

    async with _running() as (chrome, pipe):
        host = _Host()
        _wire(chrome, host)
        real_window = chrome._input_window
        chrome._input_window = Window(FormattedTextControl("x", focusable=True))  # not the focused one
        try:
            await _press(chrome, pipe)
        finally:
            chrome._input_window = real_window
        assert host.requests == 0
        assert chrome.buffer.cursor_position == 3, "emacs backward-char took it (verify round 2 N3)"


async def test_an_open_completion_menu_keeps_ctrl_b_the_cursor_key() -> None:
    async with _running() as (chrome, pipe):
        host = _Host()
        _wire(chrome, host)
        chrome.buffer.completer = WordCompleter(["/help", "/hotkeys"])
        chrome.buffer.text = "/"
        chrome.buffer.cursor_position = 1
        chrome.buffer.start_completion(select_first=False)
        await wait_until(lambda: chrome.buffer.complete_state is not None, what="the completion menu")
        await _press(chrome, pipe)
        assert host.requests == 0 and chrome.buffer.cursor_position == 0


async def test_under_a_modal_ctrl_b_does_nothing_25_times_in_one_process() -> None:
    async with _running() as (chrome, pipe):
        host = _Host()
        _wire(chrome, host)
        for _ in range(25):
            dialog = Window(FormattedTextControl("a dialog", focusable=True), height=1)
            chrome.mount_modal(dialog, focus=dialog)  # as ``show_modal`` mounts and focuses one
            chrome.focus(dialog)
            await _press(chrome, pipe)
            assert host.requests == 0
            chrome.unmount_modal()
            chrome.focus_input()
            await asyncio.sleep(0)
        assert chrome.buffer.cursor_position == 4, "the hidden input is not touched"
        # A modal that leaves the focus where it was (a ``custom()`` overlay binds
        # no key): the key still asks nothing.
        chrome.mount_modal(Window(height=1))
        await _press(chrome, pipe)
        assert host.requests == 0
        chrome.unmount_modal()


async def test_the_interrupt_latch_clears_when_the_turn_ends() -> None:
    async with _running() as (chrome, _pipe):
        chrome.note_interrupt()
        chrome.set_running(True)  # a command run mid-turn puts ``running`` back
        assert chrome.interrupt_pending
        chrome.set_running(False)
        assert not chrome.interrupt_pending


async def test_without_jobs_ctrl_b_is_backward_char_as_before() -> None:
    async with _running(detach_key=False) as (chrome, pipe):
        await _press(chrome, pipe)
        assert chrome.buffer.cursor_position == 3


def test_an_extensions_ctrl_b_is_skipped_while_the_key_is_bound(caplog: pytest.LogCaptureFixture) -> None:
    shortcuts = {"ctrl+b": object()}
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        caplog.clear()
        AelixChrome(detach_key=True, extension_shortcuts=lambda: shortcuts)
        assert "extension shortcut 'ctrl+b' skipped: key already bound (built-ins win)" in caplog.text
        caplog.clear()
        assert len(_bound(AelixChrome(extension_shortcuts=lambda: shortcuts), "c-b")) == 1
        assert "skipped" not in caplog.text


@pytest.mark.parametrize("on", [True, False])
async def test_hotkeys_lists_ctrl_b_only_with_jobs_on(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(activation, "ACTIVE", on)
    committed: list[Any] = []

    class _Ctx:
        def commit(self, renderable: Any) -> None:
            committed.append(renderable)

    await _hotkeys_handler(_Ctx(), "")  # type: ignore[arg-type]
    out = io.StringIO()
    Console(file=out, width=200).print(committed[0])
    assert (_DETACH_HOTKEY[0] in out.getvalue()) is on
    assert ("twice in tmux" in out.getvalue()) is on
