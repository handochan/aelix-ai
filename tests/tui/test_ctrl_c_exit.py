"""#66 item 2 — idle double-Ctrl+C-to-exit key binding (fake-clock).

Drives the ``c-c`` binding handler directly (the same pattern as
``test_chrome_keybindings``) with an injected monotonic clock so the 2s arm
window is exercised deterministically. Asserts:
  · idle empty buffer → first press arms + shows the transient status hint, no
    EOF; a second press within the window requests EOF (the Ctrl+D teardown);
  · a second press AFTER the window re-arms instead of exiting;
  · a running-turn Ctrl+C still aborts (never arms);
  · a non-empty buffer Ctrl+C clears the line without arming.
"""

from __future__ import annotations

import io

from aelix_coding_agent.tui.chrome import (
    _CTRL_C_EXIT_HINT,
    _CTRL_C_EXIT_WINDOW,
    _EOF,
    AelixChrome,
)
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


class _Clock:
    """A mutable monotonic clock injected via ``time_fn``."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


def _make_chrome(clock: _Clock) -> AelixChrome:
    console = Console(file=io.StringIO(), force_terminal=True, width=80)
    return AelixChrome(console=console, time_fn=clock)


def _cc_handler(chrome: AelixChrome):
    kb = chrome.app.key_bindings
    assert kb is not None
    for binding in kb.bindings:
        keys = tuple(getattr(k, "value", str(k)) for k in binding.keys)
        if keys == ("c-c",):
            return binding.handler
    raise AssertionError("no c-c binding found")


def _eof_pending(chrome: AelixChrome) -> bool:
    """True iff the (only) queued item is the EOF sentinel; drains it."""

    if chrome._input_queue.empty():
        return False
    return chrome._input_queue.get_nowait() is _EOF


async def test_idle_empty_ctrl_c_arms_and_shows_hint() -> None:
    clock = _Clock()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome(clock)
        handler = _cc_handler(chrome)

        assert chrome._last_ctrl_c is None
        assert _CTRL_C_EXIT_HINT not in chrome._render_status()

        handler(None)  # first idle empty-buffer press → arm

        assert chrome._last_ctrl_c == clock.t
        assert _CTRL_C_EXIT_HINT in chrome._render_status()
        assert not _eof_pending(chrome)  # nothing exits on the first press


async def test_second_ctrl_c_within_window_requests_eof() -> None:
    clock = _Clock()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome(clock)
        handler = _cc_handler(chrome)

        handler(None)  # arm
        clock.t += _CTRL_C_EXIT_WINDOW - 0.5  # still inside the window
        handler(None)  # second press → EOF (reuses the Ctrl+D teardown)

        assert _eof_pending(chrome)
        assert chrome._last_ctrl_c is None  # disarmed after firing


async def test_second_ctrl_c_after_window_rearms_not_exits() -> None:
    clock = _Clock()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome(clock)
        handler = _cc_handler(chrome)

        handler(None)  # arm at t0
        clock.t += _CTRL_C_EXIT_WINDOW + 0.5  # window elapsed
        # The stale arm no longer shows a hint (self-clears at render time).
        assert _CTRL_C_EXIT_HINT not in chrome._render_status()

        handler(None)  # a late press RE-ARMS instead of exiting

        assert not _eof_pending(chrome)
        assert chrome._last_ctrl_c == clock.t
        assert _CTRL_C_EXIT_HINT in chrome._render_status()


async def test_hint_self_clears_after_window() -> None:
    clock = _Clock()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome(clock)
        handler = _cc_handler(chrome)

        handler(None)
        assert _CTRL_C_EXIT_HINT in chrome._render_status()
        clock.t += _CTRL_C_EXIT_WINDOW + 0.01
        assert _CTRL_C_EXIT_HINT not in chrome._render_status()


async def test_non_empty_buffer_ctrl_c_clears_without_arming() -> None:
    clock = _Clock()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome(clock)
        handler = _cc_handler(chrome)
        chrome.buffer.insert_text("draft message")

        handler(None)  # idle, NON-empty → clear line, do NOT arm

        assert chrome.buffer.text == ""
        assert chrome._last_ctrl_c is None
        assert _CTRL_C_EXIT_HINT not in chrome._render_status()
        assert not _eof_pending(chrome)


async def test_running_turn_ctrl_c_aborts_without_arming() -> None:
    clock = _Clock()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome(clock)
        aborts: list[int] = []
        chrome.on_interrupt = lambda: aborts.append(1)
        chrome.set_running(True)
        handler = _cc_handler(chrome)

        handler(None)  # running → abort only

        assert aborts == [1]
        assert chrome._last_ctrl_c is None  # never arms mid-turn
        assert not _eof_pending(chrome)


async def test_turn_start_disarms_pending_exit_window() -> None:
    # Arm an idle exit window, then start a turn: a fast turn must not let the
    # stale first press count as the "second press" once idle again.
    clock = _Clock()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome(clock)
        handler = _cc_handler(chrome)

        handler(None)  # arm at t0
        assert chrome._last_ctrl_c == clock.t
        chrome.set_running(True)  # a turn starts → disarm
        assert chrome._last_ctrl_c is None
        chrome.set_running(False)  # turn ends; back to idle

        handler(None)  # a fresh press must ARM (not exit)
        assert not _eof_pending(chrome)
        assert chrome._last_ctrl_c == clock.t


async def test_running_turn_ctrl_c_twice_never_exits() -> None:
    clock = _Clock()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome(clock)
        aborts: list[int] = []
        chrome.on_interrupt = lambda: aborts.append(1)
        chrome.set_running(True)
        handler = _cc_handler(chrome)

        handler(None)
        handler(None)  # two presses while running → two aborts, never EOF

        assert aborts == [1, 1]
        assert not _eof_pending(chrome)
