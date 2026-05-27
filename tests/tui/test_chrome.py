"""Sprint 6h₁₀b (ADR-0105) — AelixChrome headless tests.

Drives the real long-running ``Application`` headlessly via ``create_pipe_input``
+ ``DummyOutput`` under ``create_app_session`` (the architecture validated by the
6h₁₀b spike). No real terminal, no fixed sleeps beyond a single event-loop yield
to let the Application process fed input.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from aelix_coding_agent.tui.chrome import AelixChrome
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout import Float, Window
from prompt_toolkit.output import DummyOutput
from rich.console import Console
from rich.text import Text


class FakeClock:
    def __init__(self, start: float = 0.0, auto: float = 0.0) -> None:
        self.t = start
        self.auto = auto

    def __call__(self) -> float:
        value = self.t
        self.t += self.auto
        return value

    def advance(self, dt: float) -> None:
        self.t += dt


@asynccontextmanager
async def _chrome(*, run_app: bool) -> AsyncGenerator[tuple[AelixChrome, PipeInput, io.StringIO]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        task = asyncio.create_task(chrome.run()) if run_app else None
        try:
            yield chrome, pipe, buf
        finally:
            if task is not None:
                chrome.exit()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, timeout=3)


# === pure state / renderers (app not running) ===========================


async def test_set_status_renders() -> None:
    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        chrome.set_status("git", "main")
        chrome.set_status("model", "opus")
        rendered = chrome._render_status()
        assert "main" in rendered and "opus" in rendered
        chrome.set_status("git", None)
        assert "main" not in chrome._render_status()


async def test_working_render_shows_message() -> None:
    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        assert chrome._render_working() == ""  # hidden by default
        chrome.set_working_visible(True)
        chrome.set_working_message("thinking")
        assert "thinking" in chrome._render_working()


async def test_spinner_advances_by_time_not_render_count() -> None:
    clock = FakeClock(auto=0.0)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome(
            console=Console(file=io.StringIO(), force_terminal=True, width=80), time_fn=clock
        )
        chrome.set_working_visible(True)
        first = chrome._render_working()
        assert chrome._render_working() == first  # within interval → no advance
        clock.advance(0.2)  # past the 0.08s interval
        assert chrome._render_working() != first  # advanced by elapsed time


async def test_float_add_remove_lifecycle() -> None:
    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        f = Float(content=Window())
        chrome.add_float(f)
        assert f in chrome._floats
        chrome.remove_float(f)
        assert f not in chrome._floats
        chrome.remove_float(f)  # idempotent — must not raise


async def test_request_eof_unblocks_get_input() -> None:
    async with _chrome(run_app=True) as (chrome, _pipe, _buf):
        fut = asyncio.ensure_future(chrome.get_input())
        await asyncio.sleep(0.02)
        chrome.request_eof()
        with pytest.raises(EOFError):
            await asyncio.wait_for(fut, timeout=5)


async def test_editor_remote_control() -> None:
    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        chrome.set_editor_text("foo")
        assert chrome.get_editor_text() == "foo"
        chrome.paste_to_editor("bar")
        assert chrome.get_editor_text() == "foobar"


async def test_widget_slots() -> None:
    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        chrome.set_widget("w1", ["line-a", "line-b"], above=True)
        assert "line-a" in str(chrome._render_widgets_above())
        chrome.set_widget("w1", None, above=True)
        assert "line-a" not in str(chrome._render_widgets_above())


# === app-driven (input queue / print_above) ============================


async def test_get_input_returns_submitted_line() -> None:
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        fut = asyncio.ensure_future(chrome.get_input())
        await asyncio.sleep(0.05)  # let the Application process the fed line
        pipe.send_text("hello world\n")
        result = await asyncio.wait_for(fut, timeout=5)
        assert result == "hello world"


async def test_input_buffered_before_get_input() -> None:
    # Queue-based: a line submitted before get_input is not lost.
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        await asyncio.sleep(0.02)
        pipe.send_text("early\n")
        await asyncio.sleep(0.05)
        result = await asyncio.wait_for(chrome.get_input(), timeout=5)
        assert result == "early"


async def test_ctrl_d_raises_eof() -> None:
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        fut = asyncio.ensure_future(chrome.get_input())
        await asyncio.sleep(0.05)
        pipe.send_text("\x04")  # Ctrl+D on an empty buffer
        with pytest.raises(EOFError):
            await asyncio.wait_for(fut, timeout=5)


async def test_print_above_writes_to_scrollback() -> None:
    async with _chrome(run_app=True) as (chrome, _pipe, buf):
        await asyncio.sleep(0.02)
        await asyncio.wait_for(chrome.print_above(Text("STREAMED-OUTPUT")), timeout=5)
        assert "STREAMED-OUTPUT" in buf.getvalue()


async def test_running_gates_enter_submission() -> None:
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        chrome.set_running(True)  # turn in progress → enter must not submit
        fut = asyncio.ensure_future(chrome.get_input())
        await asyncio.sleep(0.05)
        pipe.send_text("ignored\n")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(fut, timeout=0.4)
        fut.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fut


# === Sprint 6h₁₂b — esc-to-interrupt + working hint =====================


def _escape_binding(chrome: AelixChrome):
    """Return the running-gated ``escape`` Binding from the chrome key bindings."""
    kb = chrome.app.key_bindings
    assert kb is not None
    for binding in kb.bindings:
        keys = tuple(getattr(k, "value", str(k)) for k in binding.keys)
        if keys == ("escape",):
            return binding
    raise AssertionError("escape binding not found")


async def test_escape_interrupts_only_when_running() -> None:
    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        calls: list[int] = []
        chrome.on_interrupt = lambda: calls.append(1)
        binding = _escape_binding(chrome)

        # idle → the running-gate filter is False, so Esc is inert.
        chrome.set_running(False)
        assert not binding.filter()
        # running → filter True; invoking the handler fires on_interrupt.
        chrome.set_running(True)
        assert binding.filter()
        binding.handler(None)  # type: ignore[arg-type]  # handler ignores the event
        assert calls == [1]


async def test_working_line_shows_esc_hint_when_running() -> None:
    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        chrome.set_working_visible(True)
        chrome.set_working_message("thinking")
        assert "esc to interrupt" not in chrome._render_working()  # idle: no hint
        chrome.set_running(True)
        rendered = chrome._render_working()
        assert "thinking" in rendered
        assert "esc to interrupt" in rendered
