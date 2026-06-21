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


async def test_clear_is_headless_safe() -> None:
    # Sprint 6h₁₂d — /clear path. Under DummyOutput clear() must not raise
    # whether the app is running or not (best-effort + suppressed).
    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        chrome.clear()  # app not running → no-op, must not raise
    async with _chrome(run_app=True) as (chrome, _pipe, _buf):
        await asyncio.sleep(0.02)
        chrome.clear()  # app running → writes clear seq through DummyOutput


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


async def test_submit_line_and_running_property() -> None:
    """ADR-0119 follow-up: submit_line injects a normal-submit line (used to
    re-route an idle late-steer); `running` mirrors set_running."""
    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        assert chrome.running is False
        chrome.set_running(True)
        assert chrome.running is True
        chrome.set_running(False)
        chrome.submit_line("rerouted")
        assert await asyncio.wait_for(chrome.get_input(), timeout=5) == "rerouted"


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
        out = buf.getvalue()
        assert "STREAMED-OUTPUT" in out
        # Sprint 6h₂₅ (ADR-0153, WP-9): the single-renderable path shares the CSI
        # 2026 begin/end bracket with print_above_many — assert begin<body<end so
        # both near-identical code paths have symmetric coverage.
        begin = out.find("\x1b[?2026h")
        end = out.find("\x1b[?2026l")
        body = out.find("STREAMED-OUTPUT")
        assert begin >= 0 and end >= 0, "CSI 2026 begin/end not emitted"
        assert begin < body < end, f"sync bracket does not wrap the write: {out!r}"


async def test_print_above_many_writes_each_in_order() -> None:
    # Sprint 6h₂₄ — flicker fix. print_above_many batches multiple renderables
    # into ONE in_terminal suspend; each renderable still appears in scrollback
    # in submission order.
    async with _chrome(run_app=True) as (chrome, _pipe, buf):
        await asyncio.sleep(0.02)
        await asyncio.wait_for(
            chrome.print_above_many(
                [Text("FIRST-LINE"), Text("SECOND-LINE"), Text("THIRD-LINE")]
            ),
            timeout=5,
        )
        out = buf.getvalue()
        i1, i2, i3 = out.find("FIRST-LINE"), out.find("SECOND-LINE"), out.find("THIRD-LINE")
        assert i1 >= 0 < i2 < i3, f"order broken: {i1},{i2},{i3} in {out!r}"


async def test_print_above_many_empty_is_noop() -> None:
    # Sprint 6h₂₄: an empty batch must NOT suspend the renderer (no in_terminal,
    # no invalidate). Smoke test: completes instantly without raising.
    async with _chrome(run_app=True) as (chrome, _pipe, _buf):
        await asyncio.sleep(0.02)
        await asyncio.wait_for(chrome.print_above_many([]), timeout=1)


async def test_running_enter_steers_not_submits() -> None:
    # Sprint 6h₁₂e — Enter is no longer gated on ``not _running``. Mid-turn Enter
    # now STEERS (fires on_steer) instead of feeding the input queue, so the
    # serialized _input_loop is bypassed (pi interactive-mode parity). The
    # queue must NOT receive the line.
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        steered: list[str] = []
        chrome.on_steer = lambda t: steered.append(t)
        chrome.set_running(True)  # turn in progress → enter steers
        fut = asyncio.ensure_future(chrome.get_input())
        await asyncio.sleep(0.05)
        pipe.send_text("steer this\n")
        # The queue stays empty (steered instead) — get_input never resolves.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(fut, timeout=0.4)
        fut.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fut
        assert steered == ["steer this"]


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
    # Sprint 6h₂₅ (ADR-0153, WP-3): the affordance text is now "esc to cancel"
    # (was "esc to interrupt") to match the user mockup.
    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        chrome.set_working_visible(True)
        chrome.set_working_message("thinking")
        assert "esc to cancel" not in chrome._render_working()  # idle: no hint
        chrome.set_running(True)
        rendered = chrome._render_working()
        assert "thinking" in rendered
        assert "esc to cancel" in rendered


async def test_working_line_shows_elapsed_seconds_while_running() -> None:
    # Sprint 6h₂₅ (ADR-0153, WP-3): a turn-elapsed counter ({n}s) ticks in the
    # working line while running, stamped on set_running(True) and cleared on
    # set_running(False). The counter advances with the injected clock.
    clock = FakeClock()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome(
            console=Console(file=io.StringIO(), force_terminal=True, width=80), time_fn=clock
        )
        chrome.set_working_visible(True)
        chrome.set_working_message("thinking")
        chrome.set_running(True)
        clock.advance(4.0)
        rendered = chrome._render_working()
        assert "4s" in rendered
        assert "esc to cancel" in rendered
        chrome.set_running(False)
        assert chrome._run_started is None  # falling edge clears the stamp


# === Sprint 6h₂₅ (ADR-0153, WP-3) — input prefix + placeholder =========


async def test_input_prefix_and_placeholder_wired() -> None:
    # The input BufferControl carries a BeforeInput ``❯ `` prefix processor and a
    # placeholder processor (headless-safe pure transformations).
    from aelix_coding_agent.tui.chrome import _INPUT_PLACEHOLDER, _INPUT_PREFIX
    from prompt_toolkit.layout.processors import BeforeInput

    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        control = chrome._input_window.content  # type: ignore[union-attr]
        procs = control.input_processors
        before = [p for p in procs if isinstance(p, BeforeInput)]
        assert before, "input prefix (BeforeInput) not wired"
        # BeforeInput stores its text as formatted text; the prefix glyph is present.
        assert _INPUT_PREFIX.strip() in str(before[0].text)
        placeholders = [p for p in procs if type(p).__name__ == "_PlaceholderProcessor"]
        assert placeholders, "placeholder processor not wired"
        assert placeholders[0]._text == _INPUT_PLACEHOLDER


async def test_placeholder_shows_when_empty_hides_when_typed() -> None:
    from aelix_coding_agent.tui.chrome import _INPUT_PLACEHOLDER
    from prompt_toolkit.document import Document
    from prompt_toolkit.layout.processors import TransformationInput

    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        control = chrome._input_window.content  # type: ignore[union-attr]
        ph = next(p for p in control.input_processors if type(p).__name__ == "_PlaceholderProcessor")
        # Empty buffer on line 0 → placeholder fragments rendered (dim). The
        # placeholder processor runs AFTER BeforeInput, so its incoming fragments
        # carry the ``❯ `` prefix — feed those and assert the prefix SURVIVES
        # alongside the placeholder (locks in the append-not-replace fix).
        prefix_frags = [("class:aelix.prompt bold fg:cyan", "❯ ")]
        empty = TransformationInput(control, Document(""), 0, lambda i: i, prefix_frags, 80, 1)
        empty_frags = ph.apply_transformation(empty).fragments
        assert any(_INPUT_PLACEHOLDER in text for _style, text in empty_frags)
        assert any("❯" in text for _style, text in empty_frags), "prefix swallowed"
        # Non-empty buffer → placeholder gone; original fragments pass through.
        full = TransformationInput(control, Document("hi"), 0, lambda i: i, [("", "hi")], 80, 1)
        full_frags = ph.apply_transformation(full).fragments
        assert not any(_INPUT_PLACEHOLDER in text for _style, text in full_frags)
        assert full_frags == [("", "hi")]


async def test_print_above_emits_csi_2026_synchronized_update() -> None:
    # Sprint 6h₂₅ (ADR-0153, WP-9): print_above_many brackets the scrollback write
    # with CSI 2026 Begin (?2026h) / End (?2026l) so a supporting terminal paints
    # it atomically. The sequences go through the Rich console file.
    async with _chrome(run_app=True) as (chrome, _pipe, buf):
        await asyncio.sleep(0.02)
        await asyncio.wait_for(
            chrome.print_above_many([Text("ONLY-LINE")]), timeout=5
        )
        out = buf.getvalue()
        begin = out.find("\x1b[?2026h")
        end = out.find("\x1b[?2026l")
        body = out.find("ONLY-LINE")
        assert begin >= 0 and end >= 0, "CSI 2026 begin/end not emitted"
        assert begin < body < end, f"sync bracket does not wrap the write: {out!r}"


# === Sprint 6h₁₂e — steer / follow-up (queue-while-running) =============


async def test_running_enter_steers_idle_enter_submits() -> None:
    # Mid-turn Enter fires on_steer (NOT the queue); idle Enter still feeds the
    # queue (the unchanged submit path).
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        steered: list[str] = []
        chrome.on_steer = lambda t: steered.append(t)
        # running → steer
        chrome.set_running(True)
        await asyncio.sleep(0.02)
        pipe.send_text("steer me\n")
        await asyncio.sleep(0.05)
        assert steered == ["steer me"]
        # idle → submit to the queue (steer untouched)
        chrome.set_running(False)
        await asyncio.sleep(0.02)
        pipe.send_text("normal line\n")
        result = await asyncio.wait_for(chrome.get_input(), timeout=5)
        assert result == "normal line"
        assert steered == ["steer me"]


async def test_running_alt_enter_follows_up() -> None:
    # Alt+Enter (escape, enter) mid-turn fires on_follow_up, not on_steer / queue.
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        steered: list[str] = []
        followed: list[str] = []
        chrome.on_steer = lambda t: steered.append(t)
        chrome.on_follow_up = lambda t: followed.append(t)
        chrome.set_running(True)
        await asyncio.sleep(0.02)
        pipe.send_text("queue this\x1b\r")  # Alt+Enter = ESC then CR
        await asyncio.sleep(0.05)
        assert followed == ["queue this"]
        assert steered == []


async def test_idle_alt_enter_is_noop() -> None:
    # Idle Alt+Enter does NOT submit (no-op) — it is only the follow-up
    # affordance while a turn is running.
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        followed: list[str] = []
        chrome.on_follow_up = lambda t: followed.append(t)
        chrome.set_running(False)
        fut = asyncio.ensure_future(chrome.get_input())
        await asyncio.sleep(0.02)
        pipe.send_text("nothing\x1b\r")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(fut, timeout=0.4)
        fut.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fut
        assert followed == []


# === Sprint 6h₁₄a (ADR-0121) — multiline input ============================


async def test_backslash_continuation_inserts_newline_not_submit() -> None:
    # A draft ending in a single trailing backslash + Enter inserts a newline
    # (manual multi-line entry) instead of submitting.
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        pipe.send_text("abc\\\n")  # types `abc\`, then Enter (\n = c-j)
        await asyncio.sleep(0.05)
        assert chrome.buffer.text == "abc\n"  # backslash consumed, newline added
        assert chrome._input_queue.empty()  # NOT submitted


async def test_double_backslash_submits_literal() -> None:
    # An even run of trailing backslashes is NOT a continuation — `\\` is a
    # literal backslash and the line submits.
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        fut = asyncio.ensure_future(chrome.get_input())
        await asyncio.sleep(0.05)
        pipe.send_text("x" + "\\\\" + "\n")  # types `x\\`, then Enter
        result = await asyncio.wait_for(fut, timeout=5)
        assert result == "x" + "\\" * 2  # both backslashes preserved, submitted


# === Sprint 6h₁₅ (ADR-0123) — Ctrl+T toggle + Alt+Up dequeue ===============


async def test_ctrl_t_fires_thinking_toggle() -> None:
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        toggled: list[int] = []
        chrome.on_thinking_toggle = lambda: toggled.append(1)
        pipe.send_text("\x14")  # Ctrl+T
        await asyncio.sleep(0.05)
        assert toggled == [1]


async def test_alt_up_fires_dequeue() -> None:
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        dequeued: list[int] = []
        chrome.on_dequeue = lambda: dequeued.append(1)
        pipe.send_text("\x1b\x1b[A")  # Alt+Up = Esc then Up-arrow
        await asyncio.sleep(0.05)
        assert dequeued == [1]


# === Sprint 6h₁₆ (ADR-0124) — OSC 52 clipboard ============================


async def test_ctrl_v_fires_image_paste() -> None:
    # Sprint 6h₁₉ (ADR-0127): Ctrl+V fires the host-wired image-paste callback.
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        pasted: list[int] = []
        chrome.on_image_paste = lambda: pasted.append(1)
        pipe.send_text("\x16")  # Ctrl+V
        await asyncio.sleep(0.05)
        assert pasted == [1]


async def test_ctrl_g_fires_external_editor() -> None:
    # Sprint 6h₂₃ (ADR-0131): Ctrl+G fires the host-wired external-editor
    # callback (pi parity). \x07 is the ASCII BEL = Ctrl+G.
    async with _chrome(run_app=True) as (chrome, pipe, _buf):
        opened: list[int] = []
        chrome.on_external_editor = lambda: opened.append(1)
        pipe.send_text("\x07")
        await asyncio.sleep(0.05)
        assert opened == [1]


async def test_copy_to_clipboard_writes_osc52() -> None:
    import base64

    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        captured: list[str] = []
        chrome.app.output.write_raw = lambda s: captured.append(s)  # type: ignore[method-assign]
        chrome.app.output.flush = lambda: None  # type: ignore[method-assign]
        ok = chrome.copy_to_clipboard("hi there")
        assert ok is True
        expected = base64.b64encode(b"hi there").decode("ascii")
        assert captured == [f"\x1b]52;c;{expected}\x07"]


# === Sprint 6h₂₆ (ADR-0156) — marked completions menu mounted ===========


async def test_completions_float_uses_marked_menu() -> None:
    # The completions dropdown Float mounts the marker+counter menu container
    # (not the stock CompletionsMenu) — guards against an import / construction
    # regression at chrome __init__ time.
    from aelix_coding_agent.tui.chrome import (
        _MarkedCompletionsMenu,
        _MarkedCompletionsMenuControl,
    )

    async with _chrome(run_app=False) as (chrome, _pipe, _buf):
        menu = chrome._completions_float.content
        assert isinstance(menu, _MarkedCompletionsMenu)
        # The window's control is the marked control.
        assert isinstance(menu.content.content, _MarkedCompletionsMenuControl)
