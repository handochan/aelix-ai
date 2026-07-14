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
import inspect
import logging
import re
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.application.run_in_terminal import in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, has_completions, is_done, renderer_height_is_known
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import KEY_ALIASES, Keys
from prompt_toolkit.layout import (
    ConditionalContainer,
    DynamicContainer,
    Float,
    FloatContainer,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.containers import AnyContainer, ScrollOffsets
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl, UIContent
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.menus import CompletionsMenuControl, _get_menu_item_fragments
from prompt_toolkit.layout.processors import (
    BeforeInput,
    Processor,
    Transformation,
    TransformationInput,
)
from prompt_toolkit.utils import get_cwidth
from rich.console import Console

if TYPE_CHECKING:
    from prompt_toolkit.completion import Completer
    from prompt_toolkit.formatted_text import StyleAndTextTuples
    from prompt_toolkit.input.base import Input
    from prompt_toolkit.layout.controls import GetLinePrefixCallable
    from prompt_toolkit.output.base import Output

logger = logging.getLogger(__name__)


def _translate_key_spec(spec: str) -> tuple[str, ...] | None:
    """Issue #20 — human key spec → prompt-toolkit key sequence.

    Extensions register human-readable specs (``"ctrl+t"``); prompt-toolkit
    wants its native names (``"c-t"``). Supported: ``ctrl+<key>`` →
    ``c-<key>``; ``shift+<key>`` → ``s-<key>``; ``alt+<key>``/``meta+<key>``
    → the ``("escape", <key>)`` two-key sequence; a spec WITHOUT ``+`` passes
    through as a native prompt-toolkit name (``"c-t"``, ``"f5"``,
    ``"escape"``). Anything else → ``None`` (caller skips + warns). Final
    validation stays with ``KeyBindings.add`` — it raises ``ValueError`` on
    names this translator can't vet (also handled by the caller).
    """

    s = spec.strip()
    if not s:
        return None
    if "+" not in s:
        if len(s) == 1:
            # A bare printable character ('y') would bind GLOBALLY and hijack
            # normal typing of that letter (adversarial-review HIGH) — reject;
            # every legitimate prompt-toolkit name is multi-char ('c-y', 'f5',
            # 'escape', 'tab').
            return None
        return (s,)
    parts = [p.strip().lower() for p in s.split("+")]
    if len(parts) != 2 or not all(parts):
        return None
    modifier, key = parts
    if modifier == "ctrl":
        return (f"c-{key}",)
    if modifier == "shift":
        return (f"s-{key}",)
    if modifier in ("alt", "meta"):
        return ("escape", key)
    return None

_DEFAULT_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

# Sprint 6h₃₂ — the working spinner glyph is wrapped in a vibrant 24-bit green so
# it reads as "alive / in progress" against the dim ``({elapsed}s · esc …)``
# suffix and the default-weight message. ``_render_working`` returns a raw ANSI
# string (consumed via ``_ansi_row`` → ``ANSI(...)``), so the SGR escapes render;
# they degrade to no-op on terminals that strip truecolor. Only the glyph is
# coloured — the message keeps the terminal's default foreground.
_SPINNER_COLOR = "\x1b[38;2;87;224;120m"
_SPINNER_RESET = "\x1b[0m"

# Sprint 6h₂₆ (ADR-0156) — the selected-row pointer drawn in front of the
# highlighted completion (claude/qwen-additive polish over pi's plain menu).
_MENU_MARKER = "→"
# The counter row is styled with the menu's already-defined meta class so it
# tracks the active theme without adding a new style key.
_MENU_COUNTER_STYLE = "class:completion-menu.meta.completion"


class _Eof:
    """Sentinel pushed onto the input queue by Ctrl+D."""


_EOF = _Eof()

# Sprint 6h₂₅ (ADR-0153, WP-3) — live input affordances.
_INPUT_PREFIX = "❯ "
_INPUT_PLACEHOLDER = "Type your message or @path/to/file"
# #66 item 6b — the neutral (DEFAULT-mode) style for the ``❯ `` input prefix.
# Non-default permission modes recolor it via ``prompt_style_provider`` (single
# source: ``permission_mode.MODE_META[...].badge_style``); DEFAULT keeps this.
_DEFAULT_PROMPT_STYLE = "class:aelix.prompt bold fg:cyan"

# #66 item 2 — idle Ctrl+C exit affordance. An empty-buffer Ctrl+C arms a short
# window; a second Ctrl+C within it requests EOF (the exact Ctrl+D teardown). A
# running-turn Ctrl+C stays abort-only, and a non-empty buffer just clears the
# line (neither arms the timer).
_CTRL_C_EXIT_WINDOW = 2.0
_CTRL_C_EXIT_HINT = "Press Ctrl+C again to exit"

# Issue #81 — large bracketed-paste collapse (Claude-Code-inspired, Aelix-native).
# A paste with at least ``_PASTE_COLLAPSE_MIN_LINES`` lines OR ``_PASTE_COLLAPSE_MIN_CHARS``
# characters is replaced in the *editor* by a ``[Pasted text #N +M lines]``
# placeholder; the original is held in a per-session registry and re-expanded at
# submit so the model always receives the full text (only the input box is
# compressed — the transcript echo and the model prompt both get the original).
# A second, immediately-consecutive paste of the IDENTICAL content inserts the
# raw text instead of collapsing again ("paste again to reveal", per the issue).
# The registry is bounded so a long editing session can't grow it without limit.
_PASTE_COLLAPSE_MIN_LINES = 6
_PASTE_COLLAPSE_MIN_CHARS = 1000
_PASTE_REGISTRY_MAX = 100


def _paste_line_count(data: str) -> int:
    """Number of lines in ``data`` (``splitlines`` drops one trailing newline)."""

    return len(data.splitlines()) or (1 if data else 0)


def _should_collapse_paste(data: str) -> bool:
    """True when a bracketed paste is large enough to collapse to a placeholder.

    Collapses on line count OR raw length so both a many-line paste and a single
    very long line (e.g. minified JSON) are compressed in the editor."""

    if not data:
        return False
    return (
        _paste_line_count(data) >= _PASTE_COLLAPSE_MIN_LINES
        or len(data) >= _PASTE_COLLAPSE_MIN_CHARS
    )


def _paste_placeholder(counter: int, data: str) -> str:
    """The ``[Pasted text #N +M lines]`` token the editor shows for a collapse."""

    return f"[Pasted text #{counter} +{_paste_line_count(data)} lines]"


class _PlaceholderProcessor(Processor):
    """Render a dim placeholder on the FIRST input line while the buffer is empty.

    Sprint 6h₂₅ (ADR-0153, WP-3). prompt-toolkit 3.0.52 has no first-class
    placeholder for a bare :class:`BufferControl`, so this small processor
    appends the placeholder fragments only when ``document.text == ""`` (it
    disappears on the first keystroke). Operates on ``lineno == 0`` only and is
    a pure transformation, so it is headless-safe (no I/O, no app state).
    """

    def __init__(self, text: str, style: str = "class:aelix.placeholder fg:gray") -> None:
        self._text = text
        self._style = style

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        if ti.lineno == 0 and ti.document.text == "":
            # APPEND, don't replace: this processor is chained AFTER BeforeInput,
            # which has already prepended the ``❯ `` prefix fragments on line 0.
            # Replacing them would swallow the prefix on the idle/empty buffer —
            # the state the user stares at most — so it must reappear only after
            # a keystroke. Concatenating keeps the prefix and the placeholder
            # together: ``❯ Type your message or @path/to/file``.
            return Transformation(ti.fragments + [(self._style, self._text)])
        return Transformation(ti.fragments)


class _ModePrompt(BeforeInput):
    """A ``BeforeInput`` ``❯ `` prefix whose STYLE follows the permission mode.

    #66 item 6b. ``BeforeInput`` re-reads ``self.style`` on every
    ``apply_transformation`` (see prompt-toolkit ``processors.py``), so exposing
    ``style`` as a property that resolves the chrome's live
    ``prompt_style_provider`` makes the ❯ recolor per mode with no rebuild. The
    ``text`` stays the plain ``❯ `` string (so the placeholder processor + the
    prefix-wiring tests see it unchanged); only the color is dynamic. DEFAULT
    mode / no provider → the neutral :data:`_DEFAULT_PROMPT_STYLE` (bold cyan).
    """

    def __init__(self, chrome: AelixChrome, text: str) -> None:
        self._chrome = chrome
        super().__init__(text)  # sets self.text=text, self.style="" (ignored)

    @property
    def style(self) -> str:  # type: ignore[override]
        provider = self._chrome.prompt_style_provider
        if provider is not None:
            try:
                return provider()
            except Exception:  # noqa: BLE001 — a faulty provider must not break input
                pass
        return _DEFAULT_PROMPT_STYLE

    @style.setter
    def style(self, value: str) -> None:
        # ``BeforeInput.__init__`` assigns ``self.style = ""``; swallowed — the
        # property computes the live per-mode style on every render.
        pass


class _MarkedCompletionsMenuControl(CompletionsMenuControl):
    """``CompletionsMenuControl`` + a selected-row marker and a match counter.

    Sprint 6h₂₆ (ADR-0156). Two additive affordances over the stock control:

    * the current (highlighted) row is prefixed with :data:`_MENU_MARKER` in
      place of the single leading space the stock control emits, and
    * a synthetic trailing row renders ``(current/total)`` so the user can see
      their position in a long completion list.

    Implementation is deliberately surgical: it overrides ONLY
    :meth:`create_content` and :meth:`preferred_height`, reusing the base
    control's width / meta-column / show-meta helpers so the per-command
    description column (``display_meta``) keeps rendering unchanged. The marker
    swap relies on the documented shape of
    :func:`~prompt_toolkit.layout.menus._get_menu_item_fragments`: with
    ``space_after=True`` its first fragment is always ``("", " ")`` (a single
    leading space), so the current row swaps that fragment's text for the
    marker glyph and leaves every non-current row untouched.

    Pure :class:`~prompt_toolkit.layout.controls.UIControl`: no I/O, so it
    renders headlessly under ``DummyOutput`` (verified in the menu-control
    render tests).
    """

    def create_content(self, width: int, height: int) -> UIContent:
        complete_state = get_app().current_buffer.complete_state
        if not complete_state:
            return UIContent()

        completions = complete_state.completions
        index = complete_state.complete_index  # may be None when nothing is selected
        menu_width = self._get_menu_width(width, complete_state)
        menu_meta_width = self._get_menu_meta_width(width - menu_width, complete_state)
        show_meta = self._show_meta(complete_state)
        # 1-based display; None index (no selection) reads as the first row.
        counter = f"({(index or 0) + 1}/{len(completions)})"

        def get_line(i: int) -> StyleAndTextTuples:
            if i == len(completions):
                # Synthetic counter row pinned to the bottom of the content.
                pad = " " * max(0, (menu_width + menu_meta_width) - get_cwidth(counter) - 1)
                return to_formatted_text([(_MENU_COUNTER_STYLE, " " + counter + pad)])
            c = completions[i]
            is_current = i == index
            frags = list(
                _get_menu_item_fragments(c, is_current, menu_width, space_after=True)
            )
            # Swap the stock leading space for the marker on the current row only.
            if is_current and frags and frags[0][1] == " ":
                frags[0] = (frags[0][0], _MENU_MARKER)
            if show_meta:
                frags += self._get_menu_item_meta_fragments(c, is_current, menu_meta_width)
            return frags

        return UIContent(
            get_line=get_line,
            cursor_position=Point(x=0, y=index or 0),
            line_count=len(completions) + 1,  # +1 for the synthetic counter row
        )

    def preferred_height(
        self,
        width: int,
        max_available_height: int,
        wrap_lines: bool,
        get_line_prefix: GetLinePrefixCallable | None,
    ) -> int | None:
        complete_state = get_app().current_buffer.complete_state
        # +1 = counter row; 0 when there is nothing to show (inert / headless).
        return (len(complete_state.completions) + 1) if complete_state else 0


class _MarkedCompletionsMenu(ConditionalContainer):
    """``CompletionsMenu`` shape mounting :class:`_MarkedCompletionsMenuControl`.

    Sprint 6h₂₆ (ADR-0156). Mirrors
    :class:`~prompt_toolkit.layout.menus.CompletionsMenu` exactly (same window
    sizing, scroll offsets, scrollbar margin, z-index, and
    ``has_completions & ~is_done`` visibility filter) but swaps in the marked
    control. The synthetic counter row consumes one menu row, so at
    ``max_height`` the window scrolls (``scroll_offset`` keeps the selected row
    visible) while the counter pins to the content bottom. With no completions
    the :class:`~prompt_toolkit.layout.ConditionalContainer` filter renders
    nothing, so the menu stays inert exactly like the stock one.
    """

    def __init__(self, max_height: int = 8, scroll_offset: int = 1) -> None:
        super().__init__(
            content=Window(
                content=_MarkedCompletionsMenuControl(),
                width=Dimension(min=8),
                height=Dimension(min=1, max=max_height),
                scroll_offsets=ScrollOffsets(top=scroll_offset, bottom=scroll_offset),
                right_margins=[ScrollbarMargin(display_arrows=False)],
                dont_extend_width=True,
                style="class:completion-menu",
                z_index=10**8,
            ),
            filter=has_completions & ~is_done,
        )


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
        extension_shortcuts: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._console = console if console is not None else Console()
        self._prompt = prompt
        self._time = time_fn  # injectable clock (spinner cadence; tests)
        # CSI 2026 bracket depth (ADR-0194) — merges overlapping brackets from
        # concurrent print_above callers; see _sync_update.
        self._sync_depth = 0
        # Issue #20 — LIVE provider of extension shortcuts ({key_spec:
        # ExtensionShortcut}). Key SPECS are enumerated once at KeyBindings
        # build time (prompt-toolkit bindings are pre-registered), but the
        # HANDLER is re-looked-up through this provider on every fire, so a
        # #24 reload's handler swaps take effect immediately. Known W1
        # limitation: a shortcut key ADDED by a reloaded extension binds on
        # the next TUI start (the KeyBindings table is built once).
        self._extension_shortcuts_provider = extension_shortcuts
        # Live async shortcut-handler tasks — held so the fire-and-forget
        # coroutines are drained via done-callback (no "Task exception was
        # never retrieved" GC noise); see _fire_extension_shortcut.
        self._shortcut_tasks: set[asyncio.Task[Any]] = set()
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
        # shift+tab cycles the permission posture (WP-0, ADR-0157). Fires a
        # host-wired callback (``run_tui._cycle_permission``) that advances
        # ``PermissionPosture.cycle()`` + repaints the footer badge. Safe idle or
        # mid-turn (it only flips a field + repaints; the gate reads the posture
        # on the next tool_call under its lock). None in headless tests.
        self.on_permission_cycle: Callable[[], None] | None = None
        # #66 item 6b — a host-wired provider returning the prompt-toolkit style
        # for the ``❯ `` input prefix, following the permission mode (single
        # source: ``MODE_META[...].badge_style``). ``None`` (headless / no
        # posture) → the neutral bold-cyan default (:data:`_DEFAULT_PROMPT_STYLE`).
        self.prompt_style_provider: Callable[[], str] | None = None

        # === state the live regions read ===
        self._status: dict[str, str] = {}
        # #66 item 2 — monotonic timestamp of the last idle empty-buffer Ctrl+C
        # (via ``self._time``); a second press within ``_CTRL_C_EXIT_WINDOW``
        # requests EOF. ``None`` = not armed. Reset on line-clear / exit / expiry.
        self._last_ctrl_c: float | None = None
        self._footer_line: str = ""
        self._header_line: str = ""
        self._breadcrumb_line: str = ""
        self._working_message: str | None = None
        self._working_visible: bool = False
        self._running: bool = False
        # Sprint 6h₂₅ (ADR-0153, WP-3) — turn-elapsed clock. ``set_running(True)``
        # stamps a monotonic start; ``_render_working`` renders ``({elapsed}s)``
        # while running, ticking with the existing ``refresh_interval`` repaints
        # (no extra loop). Reset to None on ``set_running(False)``.
        self._run_started: float | None = None
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
        # Issue #81 — pasted-text collapse. ``_paste_registry`` maps a live
        # ``[Pasted text #N +M lines]`` placeholder → its original text; the
        # placeholder is what the editor shows, and ``_expand_pastes`` substitutes
        # the original back in at submit so the model gets the full content.
        # ``_paste_counter`` is monotonic across the session (the #N never resets,
        # like Claude Code). ``_last_pasted_raw`` remembers the most recent
        # collapsed paste so an immediately-repeated identical paste reveals the
        # raw text instead of collapsing a second time.
        self._paste_registry: dict[str, str] = {}
        self._paste_counter: int = 0
        self._last_pasted_raw: str | None = None
        # The placeholder inserted for ``_last_pasted_raw`` — so an immediately
        # repeated identical paste REPLACES that placeholder with the raw text
        # (a true "reveal") instead of appending a second, still-expandable copy.
        self._last_placeholder: str | None = None

        # Modal overlays (dialogs / custom components) live as Floats over the
        # main content; the overlay manager appends/removes from this list. The
        # completions dropdown is a permanent leading Float anchored to the cursor
        # — inert until ``set_command_completer`` installs a completer (no
        # completions → nothing renders); kept first so transient overlays
        # (``add_float``) draw above it.
        self._completions_float = Float(
            xcursor=True,
            ycursor=True,
            # Sprint 6h₂₆ (ADR-0156): the marked menu adds a selected-row marker
            # and a (current/total) match counter; otherwise identical to the
            # stock CompletionsMenu(max_height=8, scroll_offset=1).
            content=_MarkedCompletionsMenu(max_height=8, scroll_offset=1),
        )
        self._floats: list[Float] = [self._completions_float]
        # Sprint 6h₂₈ (ADR-0159) — the in-flow modal slot. A captured modal
        # (``/model`` picker, ``/settings``, the WP-0 approval dialog, …) is
        # mounted HERE rather than as a centered ``Float``: a Float never
        # contributes to the non-fullscreen app's rendered height
        # (``FloatContainer.preferred_height`` delegates to the body only), so a
        # modal taller than the few rows the inline app owns at the terminal
        # bottom overflowed below the edge and clipped (the user-reported "Yes/No
        # is cut off"). Mounting the modal as a real HSplit child ABOVE the input
        # makes the body's preferred height grow to include it, so the renderer
        # allocates the taller region (capped at terminal rows) and the terminal
        # scrolls prior scrollback UP — the whole modal renders, never clipped.
        # ``None`` → the placeholder (0 rows) renders, so the slot is invisible
        # when idle. Set BEFORE the app is built (``_render_modal_slot`` reads it).
        self._modal: AnyContainer | None = None
        # A zero-row idle placeholder — measured to contribute 0 rows, so the
        # slot adds no gap to the chrome when no modal is open.
        self._modal_placeholder: Window = Window(height=0)
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

        # WP-8 (Feature 5) — the footer is the ONE chrome row that may grow to
        # multiple lines (the mockup-A multi-line statusline). Unlike the fixed
        # ``height1`` helper above, it carries a ``min=1`` Dimension with NO fixed
        # ``max`` so ``self._footer_line`` may hold ``\n``-separated rows. Header /
        # breadcrumb / status stay single-line (they still use ``_ansi_row`` and
        # their setters strip ``\n``). ``dont_extend_height`` keeps it as tall as
        # its content (1 row in the default single-line mode).
        def _footer_row() -> ConditionalContainer:
            return ConditionalContainer(
                Window(
                    FormattedTextControl(lambda: ANSI(self._footer_line)),
                    height=Dimension(min=1),
                    dont_extend_height=True,
                    style="class:aelix.chrome",
                ),
                filter=renderer_height_is_known,
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
        # ``preferred=1``: prompt-toolkit's Dimension defaults ``preferred`` to
        # ``max`` when omitted, which made HSplit allocate 10 rows for the editor
        # at all times — a ~12-row blank gap (10 editor + status + footer) below
        # the chat output, even when the editor held zero lines. Pinning
        # ``preferred=1`` lets the editor START at 1 row and grow only when
        # content needs more space, matching pi / Claude Code's compact-when-idle
        # editor footprint.
        # Sprint 6h₂₅ (ADR-0153, WP-3) — a live ``❯ `` input prefix (bold cyan)
        # plus a dim empty-buffer placeholder. Both are pure ``BufferControl``
        # input processors (no I/O), so they render headlessly under DummyOutput.
        # BeforeInput draws the prefix BEFORE the placeholder/text on line 0.
        input_window = Window(
            BufferControl(
                self.buffer,
                input_processors=[
                    # #66 item 6b — the ❯ prefix color follows the permission mode
                    # (``_ModePrompt`` reads ``self.prompt_style_provider`` live).
                    _ModePrompt(self, _INPUT_PREFIX),
                    _PlaceholderProcessor(_INPUT_PLACEHOLDER),
                ],
            ),
            wrap_lines=True,
            height=Dimension(min=1, max=10, preferred=1),
        )
        self._input_window = input_window
        # Sprint 6h₂₈ (ADR-0159) — the in-flow modal slot. A DynamicContainer
        # re-resolves its child every render via ``_render_modal_slot`` (a plain
        # ConditionalContainer can't SWAP which container it shows — it only
        # toggles a FIXED child's visibility — so DynamicContainer is required to
        # mount/unmount the active modal). Placed ABOVE ``input_window`` so the
        # modal renders in the same in-flow zone as widgets_above / the stream
        # tail / the autocomplete menu — "below the chat, above the prompt".
        modal_slot = DynamicContainer(self._render_modal_slot)
        # #66 item 3 (corrected) — while an in-flow modal (picker / settings) is
        # open the user types INTO the modal, so the chat input row ("❯ Type your
        # message…") is unused and confusing. HIDE ONLY the input row; the chat
        # scrollback keeps its natural height, exactly as before (this is NOT the
        # reverted full-screen picker behaviour). ``self._input_window`` still points at
        # the RAW Window (set above) so focus() / focus_input() target the live
        # control and ``focused_element`` below stays valid; the
        # ConditionalContainer only toggles VISIBILITY. Non-capturing floats
        # (autocomplete / toasts) use add_float — is_modal_open() stays False for
        # them — so the input stays visible while a completion menu is open.
        input_conditional = ConditionalContainer(
            input_window, filter=Condition(lambda: not self.is_modal_open())
        )
        # #66 item 5 — the "Working…" row sits ABOVE the input (was below).
        # gate_visible keeps it at 0 rows when idle (no blank gap), and it
        # renders below any open modal_slot (no collision).
        working_row = _ansi_row(self._render_working, gate_visible=True)
        # A 1-row breathing gap between the "Working…" row and the input. Gated on
        # the SAME working-visible condition as the working row so the gap only
        # appears while a turn runs (no permanent blank line when idle) — AND on
        # ``not is_modal_open()`` so it collapses with the input row it separates
        # (a modal opening mid-turn hides the input, so the gap has nothing to
        # separate and would otherwise leave a stray blank row above the modal).
        working_spacer = ConditionalContainer(
            Window(height=1),
            filter=renderer_height_is_known
            & Condition(
                lambda: (self._working_visible or self._running)
                and not self.is_modal_open()
            ),
        )
        body = HSplit(
            [
                header,
                breadcrumb,
                Window(FormattedTextControl(self._render_widgets_above), dont_extend_height=True),
                modal_slot,
                working_row,
                working_spacer,
                input_conditional,
                Window(FormattedTextControl(self._render_widgets_below), dont_extend_height=True),
                _ansi_row(self._render_status),
                _footer_row(),
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
            # Sprint 6h₂₄ v2 — flicker fix. The chrome's redraw cadence is what
            # the user perceives as "the bottom UI flickers": every
            # ``refresh_interval`` tick re-evaluates the spinner + status + tail
            # widget, and ``min_redraw_interval`` is the floor for how often
            # those re-evaluations flush to the terminal. At 50 ms (20 FPS) both
            # the working spinner and the tail-widget rerenders thrash visibly
            # during a token stream. The actual perceptual smoothness of a
            # spinner sits around 10 Hz (the human flicker threshold for small
            # glyph changes is ~16 Hz), so 100 ms keeps motion live while
            # halving the terminal write rate. ``min_redraw_interval`` lifted to
            # 80 ms (12.5 FPS) so back-to-back ``invalidate`` calls (e.g. tail
            # + status + footer in the same loop tick) coalesce into one frame.
            refresh_interval=0.1,
            min_redraw_interval=0.08,
            input=pt_input,
            output=pt_output,
        )
        # Alt+Enter (ADR-0119 follow-up) is the 2-key sequence ("escape",
        # "enter"), which makes Esc a PREFIX key — so a standalone Esc (the
        # "esc to cancel" affordance while a turn runs) would otherwise wait
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
            # Issue #81 — expand any collapsed-paste placeholders back to their
            # original text so the model receives the full content (the editor
            # only ever displayed the compact ``[Pasted text …]`` placeholder).
            # History stores the EXPANDED text so an Up-arrow recall is
            # self-contained even after the registry is cleared on submit.
            submit_text = self._expand_pastes(text)
            # Mid-turn Enter steers (injects into the running turn) instead of
            # feeding the serialized input queue — bypasses _input_loop, which is
            # blocked awaiting harness.prompt(). Idle / no-callback → normal submit.
            if self._running and submit_text.strip() and self.on_steer is not None:
                self.buffer.reset()
                with contextlib.suppress(Exception):
                    self.buffer.history.append_string(submit_text)
                self._reset_paste_state()
                self.on_steer(submit_text)
                return
            self.buffer.reset()
            # The queue accept path bypasses prompt-toolkit's normal handler, so
            # record history explicitly.
            if submit_text.strip():
                with contextlib.suppress(Exception):
                    self.buffer.history.append_string(submit_text)
            self._reset_paste_state()
            self._input_queue.put_nowait(submit_text)

        @kb.add("escape", "enter")
        def _follow_up(event: object) -> None:
            # Alt+Enter is the follow-up affordance: while a turn is running it
            # queues the line for AFTER the turn (harness.follow_up). It is only
            # meaningful mid-turn — idle Alt+Enter is a no-op (does NOT submit; the
            # input editor is single-line so there is no newline-insert to shadow).
            # Issue #81 — expand collapsed-paste placeholders (see _accept).
            submit_text = self._expand_pastes(self.buffer.text)
            if self._running and submit_text.strip() and self.on_follow_up is not None:
                self.buffer.reset()
                with contextlib.suppress(Exception):
                    self.buffer.history.append_string(submit_text)
                self._reset_paste_state()
                self.on_follow_up(submit_text)
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

        @kb.add(Keys.BracketedPaste)  # Issue #81: collapse large text pastes.
        def _bracketed_paste(event: Any) -> None:
            # Overrides prompt-toolkit's default bracketed-paste handler (which
            # inserts the raw data). Normalize line endings the same way the
            # default does (iTerm2 & friends send \r\n / \r in a paste).
            data = event.data.replace("\r\n", "\n").replace("\r", "\n")
            buf = event.current_buffer
            # Small pastes insert as-is (no placeholder — the collapse is only a
            # win for large content that would otherwise flood the editor).
            if not _should_collapse_paste(data):
                buf.insert_text(data)
                self._last_pasted_raw = data
                self._last_placeholder = None
                return
            # "Paste again to reveal": an immediately-repeated identical paste
            # REPLACES the placeholder the first paste inserted with the raw text
            # and pops its registry entry, then disarms. Replacing (not appending)
            # is critical: leaving the placeholder in the buffer would let
            # _expand_pastes re-expand it at submit and send the content TWICE
            # (Issue #81 review, HIGH). Falls back to a plain insert if the
            # placeholder is no longer in the buffer (the user edited it away).
            if data == self._last_pasted_raw:
                placeholder = self._last_placeholder
                if placeholder is not None and placeholder in buf.text:
                    idx = buf.text.index(placeholder)
                    buf.text = (
                        buf.text[:idx] + data + buf.text[idx + len(placeholder) :]
                    )
                    buf.cursor_position = idx + len(data)
                    self._paste_registry.pop(placeholder, None)
                else:
                    buf.insert_text(data)
                self._last_pasted_raw = None
                self._last_placeholder = None
                return
            placeholder = self._register_paste(data)
            buf.insert_text(placeholder)
            self._last_pasted_raw = data
            self._last_placeholder = placeholder

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
            # #66 item 2 — Ctrl+C behaviour by state:
            #  · running turn → abort (unchanged); never arms the exit timer.
            #  · idle, NON-empty buffer → clear the line (conventional REPL);
            #    never arms the exit timer.
            #  · idle, EMPTY buffer → first press arms a short window + shows a
            #    transient status hint; a second press within the window requests
            #    EOF (the exact Ctrl+D teardown), never exit() directly.
            if self._running:
                if self.on_interrupt is not None:
                    self.on_interrupt()
                return
            if self.buffer.text:
                self.buffer.reset()
                self._reset_paste_state()  # Issue #81 — drop dangling paste entries.
                self._last_ctrl_c = None
                return
            now = self._time()
            if (
                self._last_ctrl_c is not None
                and now - self._last_ctrl_c <= _CTRL_C_EXIT_WINDOW
            ):
                self._last_ctrl_c = None
                self.request_eof()
                return
            # First press (or a press after the window elapsed) → (re-)arm.
            self._last_ctrl_c = now
            self.invalidate()

        @kb.add("escape", filter=Condition(lambda: self._running))
        def _escape_interrupt(event: object) -> None:
            # Esc interrupts an in-progress turn (same as Ctrl-C while running);
            # running-gated so Esc stays inert when idle (no interference with
            # editing / the completion menu, which has no focus during a turn).
            if self.on_interrupt is not None:
                self.on_interrupt()

        # shift+tab cycles the permission posture (WP-0, ADR-0157). prompt-toolkit
        # maps the shift+tab / backtab escape sequence (CSI Z) to the single key
        # name ``s-tab`` (the literal ``"backtab"`` is NOT a valid prompt-toolkit
        # key name and raises at binding time). ``s-tab`` is FREE (Tab itself is
        # c-i, bound above for completion). Safe idle + running (the handler just
        # flips a field + repaints; the gate reads the posture on the next
        # tool_call under its lock).
        #
        # FILTER (nit WP-0): gate on the input window holding focus. A modal Float
        # (the approval dialog / /model picker / /settings) focuses its own Window
        # but does NOT consume ``s-tab``, so prompt-toolkit would otherwise fall
        # through to this GLOBAL binding and silently cycle the posture behind the
        # open modal (a confusing UX wart — and posture changes mid-prompt). The
        # focus check keeps shift+tab inert whenever a modal owns focus.
        @kb.add(
            "s-tab",
            filter=Condition(lambda: self._input_has_focus() and not self.is_modal_open()),
        )
        def _cycle_permission(event: object) -> None:
            if self.on_permission_cycle is not None:
                self.on_permission_cycle()
            # #66 item 6b — repaint so the ❯ prompt color tracks the new mode
            # (the footer badge is repainted by the host's cycle callback).
            self.invalidate()

        # Issue #20 — extension shortcuts LAST, so built-ins can never be
        # shadowed (already-bound key sequences are skipped with a warning).
        self._register_extension_shortcuts(kb)

        return kb

    def _register_extension_shortcuts(self, kb: KeyBindings) -> None:
        """Issue #20 — bind extension-registered shortcuts into the app.

        Key specs come from the live provider ONCE at build time; each
        binding's handler re-resolves the shortcut through the provider at
        FIRE time (reload-safe for handler swaps — the same live-read idiom
        as CommandDispatchService). Rules: built-ins win (an already-bound
        sequence is skipped), an untranslatable/invalid key spec is skipped,
        and every skip logs a warning naming the spec. Handlers are invoked
        with NO arguments; an awaitable result is scheduled fire-and-forget
        on the prompt-toolkit event loop.
        """

        provider = self._extension_shortcuts_provider
        if provider is None:
            return
        try:
            shortcuts = provider() or {}
        except Exception:  # noqa: BLE001 — a faulty provider must not break the TUI
            logger.warning("extension shortcut provider failed", exc_info=True)
            return
        # NOTE: binding keys are ``Keys`` enum members whose ``str()`` is
        # ``'Keys.ControlT'`` — the canonical name lives in ``.value``
        # (``'c-t'``); plain characters have no ``.value``. BOTH sides of the
        # collision check are additionally canonicalized through
        # ``KEY_ALIASES`` (``'enter'``≡``'c-m'``, ``'tab'``≡``'c-i'``) —
        # without it an extension spec ``'enter'`` sailed past the guard and
        # SHADOWED the core Enter submit (adversarial-review HIGH,
        # live-reproduced).
        def _canonical(seq: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(KEY_ALIASES.get(k, k) for k in seq)

        bound = {
            _canonical(tuple(getattr(k, "value", str(k)) for k in b.keys))
            for b in kb.bindings
        }
        # Same gate as the built-in ``s-tab`` binding: extension shortcuts
        # stay inert while a modal (approval dialog / picker) owns focus —
        # arbitrary extension code must not fire over a permission prompt
        # (adversarial-review MEDIUM).
        ext_filter = Condition(
            lambda: self._input_has_focus() and not self.is_modal_open()
        )
        for spec in shortcuts:
            seq = _translate_key_spec(spec)
            if seq is None:
                logger.warning(
                    "extension shortcut %r skipped: unsupported key spec", spec
                )
                continue
            if _canonical(seq) in bound:
                logger.warning(
                    "extension shortcut %r skipped: key already bound "
                    "(built-ins win)",
                    spec,
                )
                continue

            def _make_handler(spec_: str) -> Callable[[object], None]:
                def _fire(event: object) -> None:
                    self._fire_extension_shortcut(spec_)

                return _fire

            try:
                kb.add(*seq, filter=ext_filter)(_make_handler(spec))
            except ValueError:
                logger.warning(
                    "extension shortcut %r skipped: prompt-toolkit rejected "
                    "key %r",
                    spec,
                    seq,
                )
                continue
            bound.add(_canonical(seq))

    def _fire_extension_shortcut(self, key_spec: str) -> None:
        """Resolve the shortcut LIVE and run its handler (never raises)."""

        provider = self._extension_shortcuts_provider
        if provider is None:
            return
        try:
            shortcut = (provider() or {}).get(key_spec)
            handler = getattr(shortcut, "handler", None)
            if not callable(handler):
                return  # removed by a reload — inert until restart rebinds
            result = handler()
            if inspect.isawaitable(result):
                # Hold a reference + drain via done-callback: a bare
                # ensure_future whose coroutine raises would surface as
                # "Task exception was never retrieved" GC noise
                # (adversarial-review MEDIUM).
                task = asyncio.ensure_future(result)
                self._shortcut_tasks.add(task)

                def _done(t: asyncio.Task[Any], spec_: str = key_spec) -> None:
                    self._shortcut_tasks.discard(t)
                    if not t.cancelled() and t.exception() is not None:
                        logger.warning(
                            "extension shortcut %r async handler failed",
                            spec_,
                            exc_info=t.exception(),
                        )

                task.add_done_callback(_done)
        except Exception:  # noqa: BLE001 — a faulty extension must not crash the TUI
            logger.warning(
                "extension shortcut %r handler failed", key_spec, exc_info=True
            )

    # === region renderers (ANSI strings) ===================================

    def _render_status(self) -> str:
        # Strip newlines: this is a fixed height=1 chrome row.
        parts = [v.replace("\n", " ") for v in self._status.values()]
        # #66 item 2 — the transient "Press Ctrl+C again to exit" hint. Rendered
        # (not stored in ``_status``) so it self-clears once the arm window
        # elapses — the app's ``refresh_interval`` repaints drop it with no timer.
        hint = self._ctrl_c_exit_hint()
        if hint:
            parts.append(hint)
        if not parts:
            return ""
        return "  ".join(parts)

    def _ctrl_c_exit_hint(self) -> str:
        """The idle-exit hint while a Ctrl+C exit window is armed, else ``""``.

        Pure read (no state mutation): the arm timestamp lives in
        ``_last_ctrl_c`` and is (re-)set / cleared by the ``c-c`` binding. Once
        ``_CTRL_C_EXIT_WINDOW`` has elapsed the hint simply stops rendering; the
        next idle empty-buffer Ctrl+C re-arms with a fresh timestamp.
        """

        if self._last_ctrl_c is None:
            return ""
        if self._time() - self._last_ctrl_c > _CTRL_C_EXIT_WINDOW:
            return ""
        return _CTRL_C_EXIT_HINT

    def _render_working(self) -> str:
        if not (self._working_visible or self._running):
            return ""
        frame = ""
        if self._spinner_frames:
            now = self._time()
            if now - self._spinner_last >= self._spinner_interval:
                self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)
                self._spinner_last = now
            frame = f"{_SPINNER_COLOR}{self._spinner_frames[self._spinner_index]}{_SPINNER_RESET}"
        message = (self._working_message or "Working…").replace("\n", " ")
        line = f"{frame} {message}".strip()
        # Sprint 6h₂₅ (ADR-0153, WP-3) — while a turn runs, append a dim suffix
        # group: elapsed seconds + the cancel affordance. The elapsed counter
        # ticks with the existing ``refresh_interval`` repaints (no extra loop).
        # NOTE: a ``↑ N tokens`` clause is intentionally OMITTED — the streaming
        # event path carries text deltas (strings), not an incremental
        # output-token / usage signal, so no real source exists to count from
        # without fabricating a number (deferred; see ADR-0153 WP-3).
        if self._running:
            parts: list[str] = []
            # set_running sets _running and _run_started atomically, so this is a
            # belt-and-suspenders guard against a hypothetical out-of-band
            # _running mutation — _run_started is normally non-None when running.
            if self._run_started is not None:
                elapsed = max(0, int(self._time() - self._run_started))
                parts.append(f"{elapsed}s")
            parts.append("esc to cancel")
            line += " \x1b[2m(" + " · ".join(parts) + ")\x1b[0m"
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

    def _sync_update(self, on: bool) -> None:
        """Emit the CSI 2026 Begin/End Synchronized Update sequence (WP-9).

        Sprint 6h₂₅ (ADR-0153). ``\\x1b[?2026h`` tells a supporting terminal to
        buffer screen updates until ``\\x1b[?2026l`` (it then paints the whole
        scrollback write + chrome repaint in ONE frame, removing tearing).
        Written through the SAME stream Rich uses (``self._console.file``) so it
        brackets the Rich ``print`` atomically. Best-effort + exception-
        suppressed: terminals without support ignore the unknown private CSI, and
        a non-writable file (DummyOutput path in tests) is a harmless no-op.

        Depth-counted (flicker fix round 3, ADR-0194): the bracket now opens
        BEFORE ``in_terminal()`` — i.e. before joining prompt_toolkit's
        run-in-terminal serialization chain — so two concurrent callers (the
        output pump + a descriptor's fire-and-forget ``print_above``,
        descriptors.py) can overlap brackets. DEC 2026 is a boolean MODE, not
        a nesting counter: the first caller's ``l`` would strip the waiter's
        bracket and its suspend would paint unsynchronized. Counting depth
        merges overlapping brackets into one (``h`` only on 0→1, ``l`` only on
        1→0); terminals auto-release a stale bracket on a short timeout, so
        the merged span stays bounded. The counter mutates OUTSIDE the
        suppress so a failed write can never unbalance it.
        """

        if on:
            self._sync_depth += 1
            if self._sync_depth != 1:
                return
        else:
            if self._sync_depth == 0:
                return  # unbalanced close: never emit a stray l
            self._sync_depth -= 1
            if self._sync_depth != 0:
                return
        with contextlib.suppress(Exception):
            self._console.file.write("\x1b[?2026h" if on else "\x1b[?2026l")
            self._console.file.flush()

    async def print_above(self, renderable: object) -> None:
        """Render ``renderable`` to scrollback ABOVE the pinned chrome."""

        # CSI 2026 synchronized output (WP-9, re-scoped — flicker fix round 3):
        # the bracket must wrap the WHOLE suspend cycle, not just the Rich
        # write. ``in_terminal`` erases the chrome on ENTRY and repaints it on
        # EXIT (prompt_toolkit run_in_terminal.py — ``renderer.erase()`` in
        # ``__aenter__``, ``app._redraw()`` in the ``__aexit__`` finally), so a
        # bracket INSIDE the block left both frames unsynchronized: supporting
        # terminals painted "chrome gone" and "chrome back" as separate frames
        # and the chrome visibly blinked once per commit batch. Outside the
        # block, erase → scrollback write → chrome repaint collapse into ONE
        # painted frame. Safe: the exit redraw is synchronous
        # (``Application._redraw`` calls ``renderer.render`` directly, so its
        # bytes are flushed before ``__aexit__`` returns), and terminals
        # auto-release a stale 2026 bracket on a short timeout, so an
        # exception inside cannot wedge the screen (belt: the finally below).
        self._sync_update(True)
        try:
            async with in_terminal():
                self._console.print(renderable)
        finally:
            self._sync_update(False)
        self.app.invalidate()

    async def print_above_many(
        self,
        renderables: Sequence[object],
        *,
        apply_before_redraw: Callable[[], None] | None = None,
    ) -> None:
        """Batch-render ``renderables`` to scrollback in ONE ``in_terminal`` block.

        Sprint 6h₂₄ — flicker fix. Each ``in_terminal()`` suspends the renderer,
        emits to scrollback, then re-paints the chrome below; doing this once per
        committed line during a fast token stream is what the user perceives as
        flicker. Grouping consecutive renderables under a single suspend cuts the
        number of full-chrome repaints to one per batch (down from one per
        committed line), without changing visible ordering.

        Empty ``renderables`` skips the suspend entirely (``apply_before_redraw``
        still runs, synchronously, if given — it must not be silently dropped).

        Flicker fix round 3 (supersedes the 6h₂₅ INSIDE-the-block placement):
        the CSI 2026 bracket now wraps the WHOLE suspend — ``in_terminal``
        erases the chrome on entry and repaints it on exit, both previously
        OUTSIDE the bracket, so the chrome still blinked per batch on
        supporting terminals. See :meth:`print_above` for the full rationale.

        ``apply_before_redraw`` runs INSIDE the suspend, after the scrollback
        writes and before the exit repaint — chrome state it mutates (e.g. the
        ``__stream__`` tail widget) is painted in the SAME frame as the batch.
        This is the atomic stable-line/final handoff: committed text lands in
        scrollback and leaves the live window in one paint, never two.
        """

        if not renderables:
            if apply_before_redraw is not None:
                apply_before_redraw()
            return
        self._sync_update(True)
        try:
            async with in_terminal():
                for renderable in renderables:
                    self._console.print(renderable)
                if apply_before_redraw is not None:
                    apply_before_redraw()
        finally:
            self._sync_update(False)
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

    def get_working_message(self) -> str | None:
        """The current working-row message (``None`` = the default ``Working…``)."""
        return self._working_message

    def get_working_visible(self) -> bool:
        """Whether the working row is force-visible independent of ``set_running``."""
        return self._working_visible

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
        # Sprint 6h₂₅ (ADR-0153, WP-3) — stamp the turn-start on rising edge so
        # _render_working can show elapsed seconds; clear on falling edge.
        self._run_started = self._time() if running else None
        # #66 item 2 — a turn starting disarms any pending idle Ctrl+C-exit window
        # so a fast turn can't let a stale first-press count as the "second press".
        if running:
            self._last_ctrl_c = None
        self.invalidate()

    def set_footer_line(self, text: str) -> None:
        # WP-8 (Feature 5) — the footer row is multi-line-capable, so it KEEPS
        # ``\n`` (the footer Window's Dimension is min=1 with no fixed max). The
        # caller (``_refresh_footer``) decides single- vs multi-line; in
        # single-line mode it passes a ``\n``-free string, so this is a no-op
        # change there. Header / breadcrumb / status still strip ``\n`` (below).
        self._footer_line = text
        self.invalidate()

    def set_footer_block(self, lines: list[str]) -> None:
        """Set the footer to a list of rows (joined by ``\\n``); multi-line-capable.

        WP-8 (Feature 5). A convenience over :meth:`set_footer_line` for the
        grouped multi-line statusline composer. Empty list clears the footer.
        """

        self._footer_line = "\n".join(lines)
        self.invalidate()

    def footer_line_count(self) -> int:
        """The number of rows the footer currently occupies (≥1).

        WP-8 (Feature 5) — :func:`overlay._reserve_rows` reads this so a taller
        multi-line footer grows the modal reserve and a near-cap modal never
        clips. An empty footer still reserves 1 row (its ConditionalContainer is
        gated on ``renderer_height_is_known``, not on content, and reports its
        ``min=1`` Dimension).
        """

        if not self._footer_line:
            return 1
        return self._footer_line.count("\n") + 1

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
        # Issue #81 — expand collapsed-paste placeholders so consumers that
        # snapshot the editor (Ctrl+G external editor, Alt+Up dequeue) see and
        # can edit the REAL content, never an opaque ``[Pasted text …]`` token
        # (editing/deleting which would otherwise silently drop the paste). The
        # registry is left intact; it is reconciled at submit / on set_editor_text.
        return self._expand_pastes(self.buffer.text)

    def set_editor_text(self, text: str) -> None:
        # Replaces the whole buffer (external-editor result, dequeue restore),
        # which already carries expanded text (see get_editor_text), so any
        # previously-registered placeholders are gone from the buffer — drop the
        # now-orphaned registry so a stale entry can't re-expand at submit.
        self._reset_paste_state()
        self.buffer.text = text
        self.buffer.cursor_position = len(text)  # cursor to end, so paste appends

    def paste_to_editor(self, text: str) -> None:
        self.buffer.insert_text(text)

    # === pasted-text collapse (Issue #81) ==================================

    def _register_paste(self, data: str) -> str:
        """Store a collapsed paste under a fresh ``[Pasted text #N +M lines]``
        placeholder and return it. The registry is bounded (:data:`_PASTE_REGISTRY_MAX`)
        so a long editing session without a submit can't grow it without limit —
        but eviction only drops entries whose placeholder is NO LONGER in the live
        buffer, so a still-visible token can never be stranded (which would leak
        the literal placeholder to the model and lose its content — Issue #81
        review). If every entry is still live the bound is briefly exceeded rather
        than corrupt the buffer."""

        self._paste_counter += 1
        placeholder = _paste_placeholder(self._paste_counter, data)
        self._paste_registry[placeholder] = data
        if len(self._paste_registry) > _PASTE_REGISTRY_MAX:
            live = self.buffer.text
            for key in list(self._paste_registry):
                if len(self._paste_registry) <= _PASTE_REGISTRY_MAX:
                    break
                if key != placeholder and key not in live:
                    del self._paste_registry[key]
        return placeholder

    def _expand_pastes(self, text: str) -> str:
        """Substitute every registered paste placeholder in ``text`` with its
        original content so the model receives the full text (the editor only
        ever showed the compact placeholder). Returns ``text`` unchanged when
        nothing is registered.

        A single left-to-right regex pass (alternation of the registered
        placeholders, longest first so a shorter ``#N`` id can't shadow a longer
        one) maps each match through the registry — so original text spliced in
        by an earlier substitution is never re-scanned and cannot be
        double-expanded even if a pasted blob happens to contain another live
        placeholder token."""

        if not self._paste_registry:
            return text
        placeholders = sorted(self._paste_registry, key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(p) for p in placeholders))
        return pattern.sub(lambda m: self._paste_registry[m.group(0)], text)

    def _reset_paste_state(self) -> None:
        """Drop the paste registry + re-paste memory. Called when a line is
        consumed (submit) or discarded (Ctrl+C clear). ``_paste_counter`` is
        deliberately NOT reset — ``#N`` stays monotonic across the session (like
        Claude Code) so a placeholder never collides with an earlier one."""

        self._paste_registry.clear()
        self._last_pasted_raw = None
        self._last_placeholder = None

    # === overlay support ===================================================

    def _render_modal_slot(self) -> AnyContainer:
        """The container the in-flow modal slot draws (Sprint 6h₂₈, ADR-0159).

        Returns the active modal when one is mounted, else the zero-row
        placeholder (so the slot is invisible + contributes 0 rows when idle).
        Called every render by the :class:`DynamicContainer` in the body HSplit.
        """

        return self._modal if self._modal is not None else self._modal_placeholder

    def mount_modal(self, content: AnyContainer) -> None:
        """Mount ``content`` in the in-flow modal slot (Sprint 6h₂₈, ADR-0159).

        REPLACES the role :meth:`add_float` played for ``show_modal``: the slot
        is a real HSplit child, so the body's preferred height grows to include
        the modal and the non-fullscreen renderer allocates the taller region
        (capped at terminal rows) — the modal renders fully, never clipped below
        the terminal edge. Kept separate from :meth:`add_float` so the
        completions menu + descriptor toasts (which never clip) stay as Floats.
        """

        self._modal = content
        self.invalidate()

    def unmount_modal(self) -> None:
        """Remove the in-flow modal so the slot collapses to 0 rows (ADR-0159)."""

        self._modal = None
        self.invalidate()

    def is_modal_open(self) -> bool:
        """Whether an in-flow modal is currently mounted (ADR-0159)."""

        return self._modal is not None

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

    def _input_has_focus(self) -> bool:
        """Whether the editor input window currently holds layout focus.

        Used to gate the global ``s-tab`` permission-cycle binding so it stays
        inert while a modal Float (approval dialog / picker / settings) owns
        focus (nit WP-0). Fail-safe: any error → ``False`` (binding inert).
        """

        if self._input_window is None:
            return False
        try:
            return self.app.layout.has_focus(self._input_window)
        except Exception:  # noqa: BLE001 — pre-run / torn-down layout → inert
            return False

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
