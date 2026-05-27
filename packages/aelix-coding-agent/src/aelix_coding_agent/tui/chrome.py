"""Sprint 6h₁₀b (ADR-0105) — AelixChrome: the persistent live bottom region.

A single long-running ``Application(full_screen=False)`` that runs for the whole
session. Unlike Sprint 6h₁₀a's per-turn ``PromptSession.prompt_async`` (which
exits on every Enter and therefore cannot keep a chrome live during a turn), this
Application never exits between turns, so the status line / footer / working
spinner / input editor stay pinned at the terminal's bottom edge while assistant
+ tool output scroll into native scrollback ABOVE them.

Architecture (source-verified against prompt-toolkit 3.0.52; ADR-0105 §arch):
- ``full_screen=False`` → inline, no alternate screen; chat history scrolls into
  the terminal's scrollback (the Pi / Claude Code UX).
- :func:`~prompt_toolkit.application.run_in_terminal.in_terminal` → the canonical
  "print above a running app" protocol; suspends the renderer, lets Rich write
  into scrollback, repaints the chrome below. :meth:`print_above` uses it.
- ``refresh_interval`` → drives the working spinner at ~20 FPS with no busy loop.
- ``renderer_height_is_known`` filter → gates chrome rows until the first CPR
  arrives (no first-render flicker; same guard ``PromptSession.bottom_toolbar``
  uses).

Rich remains the *rendering engine* (status/footer segments rendered to ANSI and
wrapped in :class:`~prompt_toolkit.formatted_text.ANSI`); prompt-toolkit owns the
*terminal layout + ownership*. Rich ``Live`` is NOT used here (it would contend
with the prompt-toolkit renderer).

Test-safety: inject ``pt_input`` (a ``create_pipe_input`` pipe) + ``pt_output``
(``DummyOutput``) and drive headlessly under ``create_app_session`` — validated
to not block on CPR (the 6h₁₀b architecture spike).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from prompt_toolkit.application import Application
from prompt_toolkit.application.run_in_terminal import in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, renderer_height_is_known
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.containers import AnyContainer
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from rich.console import Console

if TYPE_CHECKING:
    from prompt_toolkit.completion import Completer
    from prompt_toolkit.input.base import Input
    from prompt_toolkit.output.base import Output

_DEFAULT_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class _Eof:
    """Sentinel pushed onto the input queue by Ctrl+D."""


_EOF = _Eof()


class AelixChrome:
    """The persistent prompt-toolkit Application + live bottom regions.

    The host (``run_tui``) wires :attr:`on_interrupt` (Ctrl+C during a turn) and
    toggles :meth:`set_running` around each turn. Extensions reach the setters
    through :class:`~aelix_coding_agent.tui.context.AelixTUIContext`.
    """

    def __init__(
        self,
        *,
        console: Console | None = None,
        history_path: str | None = None,
        pt_input: Input | None = None,
        pt_output: Output | None = None,
        prompt: str = "» ",
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._console = console if console is not None else Console()
        self._prompt = prompt
        self._time = time_fn  # injectable clock (spinner cadence; tests)
        self.on_interrupt: Callable[[], None] | None = None

        # === state the live regions read ===
        self._status: dict[str, str] = {}
        self._footer_line: str = ""
        self._header_line: str = ""
        self._breadcrumb_line: str = ""
        self._working_message: str | None = None
        self._working_visible: bool = False
        self._running: bool = False
        self._spinner_frames: tuple[str, ...] = _DEFAULT_SPINNER
        self._spinner_index: int = 0
        # Spinner advances by wall-clock time (not render cadence) so its speed
        # is stable regardless of repaint frequency (ADR-0105 review).
        self._spinner_interval: float = 0.08
        self._spinner_last: float = 0.0
        self._widgets_above: dict[str, list[str]] = {}
        self._widgets_below: dict[str, list[str]] = {}

        history: History = FileHistory(history_path) if history_path else InMemoryHistory()
        self.buffer = Buffer(name="input", multiline=False, history=history)
        # Submitted lines flow through a queue (loop-agnostic at construction;
        # robust to input arriving before/after a get_input() call).
        self._input_queue: asyncio.Queue[str | _Eof] = asyncio.Queue()

        # Modal overlays (dialogs / custom components) live as Floats over the
        # main content; the overlay manager appends/removes from this list. The
        # completions dropdown is a permanent leading Float anchored to the cursor
        # — inert until ``set_command_completer`` installs a completer (no
        # completions → nothing renders); kept first so transient overlays
        # (``add_float``) draw above it.
        self._completions_float = Float(
            xcursor=True,
            ycursor=True,
            content=CompletionsMenu(max_height=8, scroll_offset=1),
        )
        self._floats: list[Float] = [self._completions_float]
        self._input_window: Window | None = None
        self.app: Application[None] = self._build_app(pt_input, pt_output)

    # === layout ============================================================

    def _build_app(self, pt_input: Input | None, pt_output: Output | None) -> Application[None]:
        height1 = Dimension(min=1, max=1)

        def _ansi_row(
            getter: Callable[[], str], *, gate_visible: bool = False
        ) -> ConditionalContainer:
            # A 1-row chrome line. Windows take no ``filter``; conditional
            # visibility goes on a ConditionalContainer wrapper.
            cond = renderer_height_is_known
            if gate_visible:
                cond = cond & Condition(lambda: self._working_visible or self._running)
            return ConditionalContainer(
                Window(
                    FormattedTextControl(lambda: ANSI(getter())),
                    height=height1,
                    dont_extend_height=True,
                    style="class:aelix.chrome",
                ),
                filter=cond,
            )

        header = ConditionalContainer(
            Window(
                FormattedTextControl(lambda: ANSI(self._header_line)),
                height=height1,
                dont_extend_height=True,
            ),
            filter=renderer_height_is_known & Condition(lambda: bool(self._header_line)),
        )
        # Dedicated breadcrumb row (6h₁₀e §D): descriptor breadcrumb chains land
        # here (``set_breadcrumb_line``) so they no longer collide with the
        # ``set_header`` factory line. Same CPR + non-empty gate as the header.
        breadcrumb = ConditionalContainer(
            Window(
                FormattedTextControl(lambda: ANSI(self._breadcrumb_line)),
                height=height1,
                dont_extend_height=True,
            ),
            filter=renderer_height_is_known & Condition(lambda: bool(self._breadcrumb_line)),
        )
        input_window = Window(BufferControl(self.buffer), wrap_lines=True, height=Dimension(min=1))
        self._input_window = input_window
        body = HSplit(
            [
                header,
                breadcrumb,
                Window(FormattedTextControl(self._render_widgets_above), dont_extend_height=True),
                input_window,
                Window(FormattedTextControl(self._render_widgets_below), dont_extend_height=True),
                _ansi_row(self._render_working, gate_visible=True),
                _ansi_row(self._render_status),
                _ansi_row(lambda: self._footer_line),
            ]
        )
        layout = Layout(
            FloatContainer(content=body, floats=self._floats),
            focused_element=input_window,
        )
        return Application(
            layout=layout,
            key_bindings=self._build_key_bindings(),
            full_screen=False,
            refresh_interval=0.05,
            min_redraw_interval=0.05,
            input=pt_input,
            output=pt_output,
        )

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter", filter=Condition(lambda: not self._running))
        def _accept(event: object) -> None:
            # When the completions menu has a highlighted entry, Enter confirms it
            # (applies + closes the menu) rather than submitting the line — the
            # conventional autocomplete contract. Only submit when no completion
            # is currently selected.
            complete_state = self.buffer.complete_state
            if complete_state is not None and complete_state.current_completion is not None:
                self.buffer.apply_completion(complete_state.current_completion)
                return
            text = self.buffer.text
            self.buffer.reset()
            # The queue accept path bypasses prompt-toolkit's normal handler, so
            # record history explicitly.
            if text.strip():
                with contextlib.suppress(Exception):
                    self.buffer.history.append_string(text)
            self._input_queue.put_nowait(text)

        @kb.add("c-i")  # Tab: start completion, then cycle through entries.
        def _complete(event: object) -> None:
            buf = self.buffer
            if buf.complete_state is not None:
                buf.complete_next()
            else:
                buf.start_completion(select_first=False)

        @kb.add("c-space")  # Explicitly open the completions menu.
        def _start_complete(event: object) -> None:
            self.buffer.start_completion(select_first=False)

        @kb.add("c-d", filter=Condition(lambda: not self._running and not self.buffer.text))
        def _eof(event: object) -> None:
            self._input_queue.put_nowait(_EOF)

        @kb.add("c-c")
        def _interrupt(event: object) -> None:
            if self._running:
                if self.on_interrupt is not None:
                    self.on_interrupt()
            else:
                self.buffer.reset()  # clear the current line (conventional REPL)

        @kb.add("escape", filter=Condition(lambda: self._running))
        def _escape_interrupt(event: object) -> None:
            # Esc interrupts an in-progress turn (same as Ctrl-C while running);
            # running-gated so Esc stays inert when idle (no interference with
            # editing / the completion menu, which has no focus during a turn).
            if self.on_interrupt is not None:
                self.on_interrupt()

        return kb

    # === region renderers (ANSI strings) ===================================

    def _render_status(self) -> str:
        if not self._status:
            return ""
        # Strip newlines: this is a fixed height=1 chrome row.
        return "  ".join(v.replace("\n", " ") for v in self._status.values())

    def _render_working(self) -> str:
        if not (self._working_visible or self._running):
            return ""
        frame = ""
        if self._spinner_frames:
            now = self._time()
            if now - self._spinner_last >= self._spinner_interval:
                self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)
                self._spinner_last = now
            frame = self._spinner_frames[self._spinner_index]
        message = (self._working_message or "Working…").replace("\n", " ")
        line = f"{frame} {message}".strip()
        # Surface the esc-to-interrupt affordance while a turn is running
        # (Sprint 6h₁₂b); dim so it reads as a hint, not part of the message.
        if self._running:
            line += " \x1b[2m· esc to interrupt\x1b[0m"
        return line

    def _render_widgets_above(self) -> ANSI:
        return self._render_widget_lines(self._widgets_above)

    def _render_widgets_below(self) -> ANSI:
        return self._render_widget_lines(self._widgets_below)

    @staticmethod
    def _render_widget_lines(slots: dict[str, list[str]]) -> ANSI:
        lines: list[str] = []
        for slot in slots.values():
            lines.extend(slot)
        return ANSI("\n".join(lines))

    # === public output / input =============================================

    async def print_above(self, renderable: object) -> None:
        """Render ``renderable`` to scrollback ABOVE the pinned chrome."""

        async with in_terminal():
            self._console.print(renderable)
        self.app.invalidate()

    async def get_input(self) -> str:
        """Await one submitted input line. Raises ``EOFError`` on Ctrl+D."""

        item = await self._input_queue.get()
        if isinstance(item, _Eof):
            raise EOFError
        return item

    def request_eof(self) -> None:
        """Push an EOF sentinel so a pending get_input raises EOFError.

        Used by the SIGTERM/SIGHUP handler to unblock the input loop and let
        run_tui's finally perform a full, ordered teardown.
        """

        self._input_queue.put_nowait(_EOF)

    # === state setters (driven by AelixTUIContext) =========================

    def set_status(self, key: str, text: str | None) -> None:
        if text is None:
            self._status.pop(key, None)
        else:
            self._status[key] = text
        self.invalidate()

    def set_working_message(self, message: str | None) -> None:
        self._working_message = message
        self.invalidate()

    def set_working_visible(self, visible: bool) -> None:
        self._working_visible = visible
        self.invalidate()

    def set_working_indicator(
        self, frames: list[str] | None, interval_ms: int | None = None
    ) -> None:
        # ``frames`` None → restore default; [] → hide the glyph (static message).
        self._spinner_frames = tuple(frames) if frames is not None else _DEFAULT_SPINNER
        self._spinner_index = 0
        if interval_ms is not None and interval_ms > 0:
            self._spinner_interval = interval_ms / 1000.0
        self.invalidate()

    def set_running(self, running: bool) -> None:
        self._running = running
        self.invalidate()

    def set_footer_line(self, text: str) -> None:
        self._footer_line = text.replace("\n", " ")  # fixed height=1 row
        self.invalidate()

    def set_header_line(self, text: str) -> None:
        self._header_line = text.replace("\n", " ")  # fixed height=1 row
        self.invalidate()

    def set_breadcrumb_line(self, text: str) -> None:
        self._breadcrumb_line = text.replace("\n", " ")  # fixed height=1 row
        self.invalidate()

    def set_widget(self, key: str, lines: list[str] | None, *, above: bool = True) -> None:
        slots = self._widgets_above if above else self._widgets_below
        if lines is None:
            slots.pop(key, None)
        else:
            slots[key] = list(lines)
        self.invalidate()

    def set_title(self, title: str) -> None:
        # Best-effort; some outputs (DummyOutput) do not support titles.
        with contextlib.suppress(Exception):
            self.app.output.set_title(title)

    # === completion seam ===================================================

    def set_command_completer(self, completer: Completer | None) -> None:
        """Install (or clear) the input-buffer completer.

        Idempotent + None-safe. When a completer is set, completions pop live
        while typing a slash command (``/…``) so the descriptor command-route
        dropdown surfaces without needing Tab; Tab / Ctrl-Space still work and
        the menu is inert for non-slash input. Clearing with ``None`` disables
        completion entirely.
        """
        if completer is None:
            from prompt_toolkit.completion import DummyCompleter

            self.buffer.completer = DummyCompleter()
            self.buffer.complete_while_typing = Condition(lambda: False)
        else:
            self.buffer.completer = completer
            # Auto-complete only for slash commands so ordinary prompts type
            # uninterrupted; the menu (and Tab/c-space) cover everything else.
            self.buffer.complete_while_typing = Condition(
                lambda: self.buffer.text.startswith("/")
            )

    # === editor remote-control seam ========================================

    def get_editor_text(self) -> str:
        return self.buffer.text

    def set_editor_text(self, text: str) -> None:
        self.buffer.text = text
        self.buffer.cursor_position = len(text)  # cursor to end, so paste appends

    def paste_to_editor(self, text: str) -> None:
        self.buffer.insert_text(text)

    # === overlay support ===================================================

    def add_float(self, float_: Float) -> None:
        self._floats.append(float_)
        self.invalidate()

    def remove_float(self, float_: Float) -> None:
        with contextlib.suppress(ValueError):
            self._floats.remove(float_)
        self.invalidate()

    def focus(self, target: AnyContainer) -> None:
        with contextlib.suppress(Exception):
            self.app.layout.focus(target)

    def focus_input(self) -> None:
        if self._input_window is not None:
            self.focus(self._input_window)

    # === lifecycle =========================================================

    def invalidate(self) -> None:
        # No-op before the app runs / after it exits.
        with contextlib.suppress(Exception):
            self.app.invalidate()

    async def run(self) -> None:
        await self.app.run_async()

    def exit(self) -> None:
        # ``app.exit()`` raises if the app is not running — harmless at teardown.
        with contextlib.suppress(Exception):
            self.app.exit()


__all__ = ["AelixChrome"]
