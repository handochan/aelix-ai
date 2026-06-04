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
import base64
import contextlib
import time
from collections.abc import Callable, Sequence
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
        # Steer / follow-up callbacks (Sprint 6h₁₂e — queue-while-running). Fired
        # from the Enter / Alt+Enter bindings ONLY while a turn is running, so the
        # serialized _input_loop is bypassed (the host wires these to harness
        # steer()/follow_up() concurrently — mirror of the on_interrupt pattern).
        self.on_steer: Callable[[str], None] | None = None
        self.on_follow_up: Callable[[str], None] | None = None
        # Ctrl+T toggles thinking-block visibility; Alt+Up restores queued
        # steer/follow-up messages back into the editor (Sprint 6h₁₅, ADR-0123).
        self.on_thinking_toggle: Callable[[], None] | None = None
        self.on_dequeue: Callable[[], None] | None = None
        # Ctrl+V paste-image (pi parity ``interactive-mode.ts:2430-2450``,
        # Sprint 6h₁₉, ADR-0127). Fires a host-wired callback that reads the
        # clipboard image, writes it to a temp file, and inserts the path at
        # the cursor. None in headless tests / when no host is attached.
        self.on_image_paste: Callable[[], None] | None = None
        # Ctrl+G external-editor (pi parity, Sprint 6h₂₃, ADR-0131). Fires a
        # host-wired callback that snapshots the current editor text into a
        # temp file, suspends prompt-toolkit via ``in_terminal``, spawns
        # ``$EDITOR`` (or ``vi`` fallback), then replaces the editor text with
        # the edited result. None in headless tests / when no host is attached.
        self.on_external_editor: Callable[[], None] | None = None

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
        # multiline=True so the editor holds newlines: a bracketed multi-line
        # paste keeps its line breaks (was mangled under multiline=False), and
        # Ctrl+J inserts an explicit newline (see _build_key_bindings). Enter
        # still SUBMITS — our app-level "enter" binding overrides the default
        # multiline "insert newline" behaviour. (ADR-0121 — pi uses Shift+Enter
        # for newline, but prompt-toolkit 3.0.52 maps the Shift+Enter CSI-u
        # sequence to plain c-m, indistinguishable from Enter, so Ctrl+J is the
        # achievable explicit-newline key.)
        self.buffer = Buffer(name="input", multiline=True, history=history)
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
        # height grows with multi-line content up to 10 rows, then the buffer
        # scrolls internally (keeps the chrome from pushing scrollback off-screen).
        input_window = Window(
            BufferControl(self.buffer), wrap_lines=True, height=Dimension(min=1, max=10)
        )
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
        app = Application(
            layout=layout,
            key_bindings=self._build_key_bindings(),
            full_screen=False,
            refresh_interval=0.05,
            min_redraw_interval=0.05,
            input=pt_input,
            output=pt_output,
        )
        # Alt+Enter (ADR-0119 follow-up) is the 2-key sequence ("escape",
        # "enter"), which makes Esc a PREFIX key — so a standalone Esc (the
        # "esc to interrupt" affordance while a turn runs) would otherwise wait
        # ~0.5s (``ttimeoutlen`` default) before flushing. ``ttimeoutlen`` is an
        # instance attribute (NOT a constructor kwarg in this prompt-toolkit
        # version); shrink it so single-Esc stays snappy while Alt+Enter still
        # registers (its esc+enter bytes arrive together within the window).
        app.ttimeoutlen = 0.05
        return app

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        # Sprint 6h₁₂e — Enter is NO LONGER gated on ``not self._running``: the
        # input editor stays live during a turn so Enter mid-turn steers (pi
        # interactive-mode parity). The idle path is unchanged (feeds the queue).
        #
        # Both ``enter`` (c-m / CR) and ``c-j`` (LF) submit: with ``multiline=True``
        # the prompt-toolkit defaults would treat c-j (and c-m) as "insert newline",
        # but in this terminal/parser both CR and LF are how "Enter" is delivered
        # (the pipe-input tests feed ``\n`` = c-j to submit), so we bind BOTH to the
        # accept handler to keep submit working. Manual newline entry is via
        # backslash-continuation (a line ending in a single ``\`` — see below); pi
        # uses Shift+Enter, but prompt-toolkit 3.0.52 collapses the Shift+Enter
        # CSI-u sequence to c-m (== Enter), so a distinct newline key isn't
        # available (ADR-0121).
        @kb.add("enter")
        @kb.add("c-j")
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
            # Backslash-continuation: a draft ending in an ODD number of trailing
            # backslashes inserts a newline instead of submitting (manual multi-line
            # entry — same idiom Claude Code uses; terminal-independent). One
            # trailing ``\`` is consumed; ``\\`` is a literal backslash + submit.
            trailing = len(text) - len(text.rstrip("\\"))
            if trailing % 2 == 1 and self.buffer.cursor_position == len(text):
                self.buffer.delete_before_cursor(1)
                self.buffer.insert_text("\n")
                return
            # Mid-turn Enter steers (injects into the running turn) instead of
            # feeding the serialized input queue — bypasses _input_loop, which is
            # blocked awaiting harness.prompt(). Idle / no-callback → normal submit.
            if self._running and text.strip() and self.on_steer is not None:
                self.buffer.reset()
                with contextlib.suppress(Exception):
                    self.buffer.history.append_string(text)
                self.on_steer(text)
                return
            self.buffer.reset()
            # The queue accept path bypasses prompt-toolkit's normal handler, so
            # record history explicitly.
            if text.strip():
                with contextlib.suppress(Exception):
                    self.buffer.history.append_string(text)
            self._input_queue.put_nowait(text)

        @kb.add("escape", "enter")
        def _follow_up(event: object) -> None:
            # Alt+Enter is the follow-up affordance: while a turn is running it
            # queues the line for AFTER the turn (harness.follow_up). It is only
            # meaningful mid-turn — idle Alt+Enter is a no-op (does NOT submit; the
            # input editor is single-line so there is no newline-insert to shadow).
            text = self.buffer.text
            if self._running and text.strip() and self.on_follow_up is not None:
                self.buffer.reset()
                with contextlib.suppress(Exception):
                    self.buffer.history.append_string(text)
                self.on_follow_up(text)
                return

        @kb.add("c-t")  # Toggle thinking-block visibility (Sprint 6h₁₅).
        def _toggle_thinking(event: object) -> None:
            if self.on_thinking_toggle is not None:
                self.on_thinking_toggle()

        @kb.add("escape", "up")  # Alt+Up: restore queued messages to the editor.
        def _dequeue(event: object) -> None:
            if self.on_dequeue is not None:
                self.on_dequeue()

        @kb.add("c-v")  # Ctrl+V: paste clipboard image (pi parity).
        def _paste_image(event: object) -> None:
            if self.on_image_paste is not None:
                self.on_image_paste()

        @kb.add("c-g")  # Ctrl+G: open the current input in $EDITOR (pi parity).
        def _external_editor(event: object) -> None:
            if self.on_external_editor is not None:
                self.on_external_editor()

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

    async def print_above_many(self, renderables: Sequence[object]) -> None:
        """Batch-render ``renderables`` to scrollback in ONE ``in_terminal`` block.

        Sprint 6h₂₄ — flicker fix. Each ``in_terminal()`` suspends the renderer,
        emits to scrollback, then re-paints the chrome below; doing this once per
        committed line during a fast token stream is what the user perceives as
        flicker. Grouping consecutive renderables under a single suspend cuts the
        number of full-chrome repaints to one per batch (down from one per
        committed line), without changing visible ordering.

        Empty ``renderables`` is a no-op (no suspend, no invalidate).
        """

        if not renderables:
            return
        async with in_terminal():
            for renderable in renderables:
                self._console.print(renderable)
        self.app.invalidate()

    def clear(self) -> None:
        """Clear the terminal scrollback without killing the live chrome.

        Sprint 6h₁₂d (`/clear`): writes the clear-scrollback + clear-screen +
        home sequence (``\\x1b[3J\\x1b[2J\\x1b[H``) through the app output, then
        invalidates so the chrome repaints below. Best-effort + headless-safe:
        :class:`~prompt_toolkit.output.DummyOutput` swallows the writes and the
        whole body is exception-suppressed, so it never raises in tests.
        """

        with contextlib.suppress(Exception):
            output = self.app.output
            output.write_raw("\x1b[3J\x1b[2J\x1b[H")
            output.flush()
        self.invalidate()

    def copy_to_clipboard(self, text: str) -> bool:
        """Copy ``text`` to the system clipboard via the OSC 52 escape sequence.

        OSC 52 is terminal-native (no dependency, works over SSH) — the terminal
        emulator puts the base64 payload on its clipboard. Best-effort +
        headless-safe (``DummyOutput`` swallows; the whole body is
        exception-suppressed). Returns ``True`` if the write was attempted.
        """

        try:
            payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
            output = self.app.output
            output.write_raw(f"\x1b]52;c;{payload}\x07")
            output.flush()
            return True
        except Exception:  # noqa: BLE001 — clipboard is best-effort, never raises
            return False

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

    @property
    def running(self) -> bool:
        """True while a turn is in progress (mirrors :meth:`set_running`)."""
        return self._running

    def submit_line(self, text: str) -> None:
        """Inject ``text`` into the input queue as if the user submitted it.

        ADR-0119 follow-up: lets the steer host re-route a message that landed
        AFTER the turn ended (the late-steer race) through the normal turn path,
        instead of leaving it orphaned in the steering queue (echoed + counted
        but inert until the next prompt).
        """

        self._input_queue.put_nowait(text)

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
            from aelix_coding_agent.tui.completion import wants_completion

            self.buffer.completer = completer
            # Auto-complete only in a completable context — a ``/`` slash command
            # or an ``@file`` mention token — so ordinary prose types
            # uninterrupted; the menu (and Tab/c-space) cover everything else.
            self.buffer.complete_while_typing = Condition(
                lambda: wants_completion(self.buffer.document.text_before_cursor)
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
