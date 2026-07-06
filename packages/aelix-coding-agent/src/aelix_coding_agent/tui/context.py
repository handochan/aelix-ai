"""Sprint 6h₁₀b (ADR-0105) — AelixTUIContext: concrete ExtensionUIContext.

The prompt-toolkit + Rich implementation of the Pi-parity 27-method
``ExtensionUIContext`` surface. Backing:
- **dialogs / custom** → :func:`~aelix_coding_agent.tui.overlay.show_modal`
- **status / working / footer / header / widgets / title / editor** → :class:`AelixChrome`
- **theme** → the :mod:`~aelix_coding_agent.tui.themes` registry (built-ins only this sprint)

``run_tui`` binds an instance via ``harness.runtime.bind_ui(ctx)`` so loaded
(Tier-1 in-process) extensions can drive the UI. Manifest-contributed themes,
the Tier-2 descriptor renderer, and per-extension ``ui_tui_trusted`` gating are
deferred (ADR-0105).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import PasswordProcessor, Processor

from aelix_coding_agent.extensions.ext_ui import (
    CustomComponentFactory,
    CustomOptions,
    EditorFactory,
    ExtensionUIDialogOptions,
    ExtensionWidgetOptions,
    FooterFactory,
    HeaderFactory,
    NotificationKind,
    SetThemeResult,
    TerminalInputHandler,
    ThemeInfo,
    WidgetFactory,
    WorkingIndicatorOptions,
)
from aelix_coding_agent.extensions.widget_protocols import (
    Component,
    OverlayOptions,
    Theme,
)
from aelix_coding_agent.tui import themes as theme_registry
from aelix_coding_agent.tui.overlay import show_modal

if TYPE_CHECKING:
    from aelix_coding_agent.extensions.ext_ui import AutocompleteProviderFactory
    from aelix_coding_agent.tui.chrome import AelixChrome
    from aelix_coding_agent.tui.footer_data import AelixFooterData
    from aelix_coding_agent.tui.footer_segments import FooterSegment
    from aelix_coding_agent.tui.statusline_store import StatuslineStore

_RENDER_WIDTH = 80  # best-effort width for factory-rendered widget lines

# Sprint 6h₂₈ (ADR-0159) — the steering ⏵⏵ segment is HIDDEN at this value (the
# user does not want "one-at-a-time" shown by default); it surfaces only when the
# user switches steering to "all".
_DEFAULT_STEERING_MODE = "one-at-a-time"

# Sprint 6h₃₀ (ADR-0163) — picker visual polish for select()/multiselect(): a
# framed panel (bold title + top/bottom dividers), a COLOR-highlighted current
# row, and DIMMED counter / detail / hint so the eye lands on the selection.
# Raw ANSI (theme-agnostic — independent of the app style map; mirrors the
# approval-dialog precedent of Rich→ANSI). Returned via ``ANSI(...)`` so
# prompt-toolkit interprets the escapes instead of printing them literally.
_PICK_SEL = "\x1b[1;36m"  # bold cyan — the highlighted row
_PICK_DIM = "\x1b[2m"  # dim — dividers, counter, detail/help, hint
_PICK_BOLD = "\x1b[1m"  # bold — the title
_PICK_RST = "\x1b[0m"
# GitHub #66 item 4 — the typed filter VALUE renders bold cyan (echoing the ❯ /
# highlighted-row color) while the "Filter:" label + surrounding chrome stay dim,
# so the eye lands on what the user is typing. Same raw-ANSI / theme-agnostic
# convention as the other picker constants.
_PICK_FILTER = "\x1b[1;36m"  # bold cyan — the live filter value
_PICK_MIN_WIDTH = 28
_PICK_MAX_WIDTH = 78


def _filter_line(value: str, placeholder: str | None = None) -> str:
    """A standalone filter affordance: dim ``Filter:`` label + bright typed VALUE.

    GitHub #66 item 4 (owner decision): brighten ONLY the typed value (bold cyan),
    keeping the ``Filter:`` label + chrome dim. When ``value`` is empty and a
    ``placeholder`` is given (e.g. ``(type to filter)``) the whole line renders dim
    — a hint is not typed input. Used by the no-match views + the tabbed filter
    row; the counter suffix (already wrapped in a dim line) brightens the value
    inline via :data:`_PICK_FILTER`.
    """

    if not value:
        shown = placeholder if placeholder is not None else ""
        return f"{_PICK_DIM}Filter: {shown}{_PICK_RST}"
    return f"{_PICK_DIM}Filter: {_PICK_RST}{_PICK_FILTER}{value}{_PICK_RST}"


def _filter_counter_suffix(value: str) -> str:
    """The ``  ·  Filter: <value>`` suffix appended to a picker's counter line.

    GitHub #66 item 4: the "  ·  Filter:" label inherits the DIM of the counter
    line it is embedded in (the caller wraps the whole counter in :data:`_PICK_DIM`)
    while the typed VALUE renders bold cyan via :data:`_PICK_FILTER`. The trailing
    reset closes the bright run; the outer line's own reset closes the dim run.
    """

    return f"  ·  Filter: {_PICK_RST}{_PICK_FILTER}{value}{_PICK_RST}"


def _picker_frame(title: str, body: list[str], hint: str, content_width: int) -> ANSI:
    """Frame a picker body: bold title + top/bottom dividers + dim hint (ADR-0163).

    ``body`` rows are already styled by the caller (the current row colored, the
    counter/detail dimmed). ``content_width`` is the widest PLAIN content line so
    the dividers span the panel. Returns :class:`ANSI` so the escapes render.
    """

    width = max(_PICK_MIN_WIDTH, min(content_width, _PICK_MAX_WIDTH))
    divider = f"{_PICK_DIM}{'─' * width}{_PICK_RST}"
    lines = [
        f"{_PICK_BOLD}{title}{_PICK_RST}",
        divider,
        *body,
        divider,
        f"{_PICK_DIM}{hint}{_PICK_RST}",
    ]
    return ANSI("\n".join(lines))


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(text: str) -> int:
    """Display width of ``text`` ignoring SGR (color/dim) escape sequences.

    ``_picker_frame`` sizes its dividers from the widest PLAIN content line, so a
    body line carrying its own ANSI (e.g. a dim footer note from the stats /
    extension tabs) must be measured by its VISIBLE length — counting the escape
    bytes would over-pad the frame.
    """

    return len(_ANSI_RE.sub("", text))


def _resolve(future: asyncio.Future[Any], value: Any) -> None:
    if not future.done():
        future.set_result(value)


class AelixKeybindings:
    """Minimal ``KeybindingsManager`` — a dict of action → key."""

    def __init__(self) -> None:
        self._bindings: dict[str, str] = {}

    def get_binding(self, action: str) -> str | None:
        return self._bindings.get(action)

    def set_binding(self, action: str, key: str) -> None:
        self._bindings[action] = key


class AelixTUI:
    """Minimal ``TUI`` façade passed to extension factories."""

    def __init__(self, ctx: AelixTUIContext) -> None:
        self._ctx = ctx

    def request_render(self) -> None:
        self._ctx.chrome.invalidate()

    def add_input_listener(self, handler: Callable[[str], None]) -> Callable[[], None]:
        return self._ctx.on_terminal_input(lambda data: _wrap_listener(handler, data))


def _wrap_listener(handler: Callable[[str], None], data: str) -> None:
    handler(data)


class AelixTUIContext:
    """Concrete :class:`~aelix_coding_agent.extensions.ext_ui.ExtensionUIContext`."""

    def __init__(
        self,
        chrome: AelixChrome,
        footer: AelixFooterData,
        *,
        model_provider: Callable[[], str | None] | None = None,
        mode_provider: Callable[[], str | None] | None = None,
        pending_provider: Callable[[], int] | None = None,
        permission_badge_provider: Callable[[], str | None] | None = None,
        cwd: str | None = None,
        mode: str = _DEFAULT_STEERING_MODE,
        statusline_store: StatuslineStore | None = None,
    ) -> None:
        self.chrome = chrome
        self._footer = footer
        self._model_provider = model_provider
        # Permission posture badge (WP-0, ADR-0157). Reads the LIVE posture mode
        # → its distinct footer glyph (✎/⏸/⚠/🤖); returns None on DEFAULT so the
        # segment is omitted. Kept SEPARATE from the ⏵⏵ steering segment so the
        # two never collide. None in headless tests (no posture wired).
        self._permission_badge_provider = permission_badge_provider
        # Live count of steer/follow-up messages queued during a turn (Sprint
        # 6h₁₂e); reads harness.pending_message_count so the footer shows a
        # "⋯ N queued" segment that drains as messages are consumed.
        self._pending_provider = pending_provider
        # ``mode_provider`` reads the LIVE steering mode from the harness so the
        # footer reflects reality instead of a stale local string; ``_mode`` is
        # the fallback when no provider is wired (headless/tests).
        self._mode_provider = mode_provider
        self._cwd = cwd
        self._mode = mode
        # Live context-window usage label (e.g. "◔ 42% · 84k/200k"), refreshed
        # async on turn_end by run_tui; None until the first turn completes.
        self._context_label: str | None = None
        # WP-2 (ADR-0160) — cached usage scalars for the OPTIONAL token/cost footer
        # segments. The footer refreshes on invalidate (NOT in an await context) and
        # get_session_stats is async, so run_tui pushes these via set_usage_stats on
        # turn_end (same cadence as the context-window label). Stale-between-turns is
        # acceptable (matches the context% segment).
        self._usage_input_tokens: int = 0
        self._usage_output_tokens: int = 0
        self._usage_cost: float = 0.0
        self._theme: Theme = theme_registry.DEFAULT_THEME
        self._tools_expanded = False
        self._hidden_thinking_label: str | None = None
        self._terminal_handlers: list[TerminalInputHandler] = []
        self._autocomplete: list[AutocompleteProviderFactory] = []
        self._editor_factory: EditorFactory | None = None
        self._footer_factory: FooterFactory | None = None
        self._notify_seq = 0
        self._tui = AelixTUI(self)
        self._kb = AelixKeybindings()
        # WP-2 (ADR-0160) — the named footer-segment registry. Built once here
        # (after the providers are assigned) so each segment's produce() closure
        # reads the LIVE context state. The ADR-0159 rules (permission badge
        # leading + omit-when-no-provider; steering hidden at default) live INSIDE
        # the producers, independent of the enabled-set.
        from aelix_coding_agent.tui.footer_segments import build_footer_registry

        self._segments: list[FooterSegment] = build_footer_registry(self)
        # The statusline store gates which segments render. None → the registry
        # default-enabled set (byte-identical to the pre-ADR-0160 footer); a wired
        # store reads the user's enabled-id set (load() degrades to defaults).
        self._statusline_store = statusline_store
        self._refresh_footer()

    # === Dialogs (5) =======================================================

    async def select(
        self,
        title: str,
        options: list[str],
        opts: ExtensionUIDialogOptions | None = None,
        detail: Callable[[int], list[str]] | None = None,
        initial_index: int = 0,
        *,
        fill_screen: bool = False,
    ) -> str | None:
        """Pi-parity arrow-key select with type-to-filter (Sprint 6h₂₄).

        Pi UX (``interactive-mode.ts`` settings/model pickers): ``→`` marker
        on the current row, ↑/↓ to move (wraps), Enter/Space to confirm,
        Esc to cancel, printable chars filter the list incrementally. The
        prior impl exposed only digit shortcuts 1-9 — broken UX for menus
        with more than 9 items (e.g. /model) and surprising for anyone used
        to pi. Both the digit-only shortcut and the 9-item cap are gone.

        ``detail`` (Sprint 6h₂₆, ADR-0154) is an optional per-highlight footer:
        a callback given the ORIGINAL option index of the highlighted row,
        returning extra lines rendered below the list (e.g. ``/model``'s
        modality / context-window / base-url panel). Default ``None`` preserves
        the prior behavior for every existing caller (/settings, /resume, the
        permission prompt). It is purely cosmetic and guarded — a raising
        ``detail`` never breaks the modal.

        NOTE: ``detail`` is an ``AelixTUIContext``-only extension and is
        deliberately NOT part of the ``ExtensionUIContext`` protocol (extensions
        calling ``ctx.ui.select`` have no need for it). Callers that pass it must
        be typed against the concrete ``AelixTUIContext``, not the protocol.

        ``fill_screen`` (GitHub #66 item 3) opts a big-list picker into FILLING the
        terminal-bounded modal region (short content top-aligns, blank space below)
        instead of leaving chat visible beneath a short list. Default ``False``
        keeps the natural (grow-with-content) height. Also ``AelixTUIContext``-only.

        Empty ``options`` resolves to ``None`` immediately (no dialog).
        """

        if not options:
            return None

        # Per-call mutable state. ``idx`` is into the FILTERED view, not
        # ``options`` — the filter changes the visible set so the cursor is
        # naturally relative to what's on screen. ``initial_index`` (clamped)
        # restores the cursor when a caller re-opens the picker (Sprint 6h₃₀,
        # ADR-0163: /settings keeps your row after returning from a sub-picker
        # like the /model selector, instead of snapping back to the top). The
        # filter starts empty, so the filtered view == ``options`` at open and
        # the index maps 1:1.
        state: dict[str, Any] = {
            "idx": max(0, min(initial_index, len(options) - 1)),
            "filter": "",
        }
        viewport = 8  # max rows of options shown at once; ⋮ markers for scroll

        def filtered() -> list[tuple[int, str]]:
            """``(orig_index, text)`` rows matching the current filter (case-insensitive)."""
            needle = state["filter"].lower()
            if not needle:
                return list(enumerate(options))
            return [(i, o) for i, o in enumerate(options) if needle in o.lower()]

        def render() -> ANSI:
            # Sprint 6h₃₀ (ADR-0163) — framed + colored. Current row is bold cyan
            # with a ▸ marker; counter / detail / hint are dim; a top+bottom
            # divider frames the panel. See _picker_frame.
            items = filtered()
            if not items:
                return _picker_frame(
                    title,
                    [
                        f"{_PICK_DIM}(no matches){_PICK_RST}",
                        _filter_line(state["filter"]),
                    ],
                    "Backspace to clear · Esc to cancel",
                    max(len(title), 40),
                )
            idx = max(0, min(state["idx"], len(items) - 1))
            state["idx"] = idx
            # Scroll window so the cursor stays visible. W-review 6h₂₄ LOW-1:
            # centers cursor when interior; clamps to top/bottom near edges.
            start = max(0, min(idx - viewport // 2, len(items) - viewport))
            end = min(len(items), start + viewport)
            detail_lines: list[str] = []
            if detail is not None:
                # Per-highlight detail panel (Sprint 6h₂₆, ADR-0154). The callback
                # gets the ORIGINAL option index (``items[idx][0]`` — the index
                # into ``options``, not the filtered view). Guarded: a raising
                # callback must never break the modal.
                with contextlib.suppress(Exception):
                    detail_lines = list(detail(items[idx][0]))
            counter = f"({idx + 1}/{len(items)})"
            if state["filter"]:
                # #66 item 4: brighten the typed VALUE (bold cyan); the "  ·  Filter:"
                # label + counter stay dim (inherited from the dim-wrapped line).
                counter += _filter_counter_suffix(state["filter"])
            hint = "↑/↓ move · type to filter · Enter select · Esc cancel"
            width = max(
                [len(title), _visible_len(counter), len(hint)]
                + [len(items[i][1]) + 2 for i in range(start, end)]
                + [len(d) for d in detail_lines]
            )
            body: list[str] = []
            if start > 0:
                body.append(f"{_PICK_DIM}  ⋮{_PICK_RST}")
            for i in range(start, end):
                text = items[i][1]
                if i == idx:
                    body.append(f"{_PICK_SEL}▸ {text}{_PICK_RST}")
                else:
                    body.append(f"  {text}")
            if end < len(items):
                body.append(f"{_PICK_DIM}  ⋮{_PICK_RST}")
            body.append(f"{_PICK_DIM}  {counter}{_PICK_RST}")
            for d in detail_lines:
                body.append(f"{_PICK_DIM}{d}{_PICK_RST}")
            return _picker_frame(title, body, hint, width)

        def build(result: asyncio.Future[Any]) -> Window:
            kb = KeyBindings()

            def _confirm(_e: object) -> None:
                items = filtered()
                if not items:
                    return
                idx = max(0, min(state["idx"], len(items) - 1))
                _resolve(result, items[idx][1])

            @kb.add("up")
            def _up(_e: object) -> None:
                items = filtered()
                if not items:
                    return
                state["idx"] = (state["idx"] - 1) % len(items)
                self.chrome.invalidate()

            @kb.add("down")
            def _down(_e: object) -> None:
                items = filtered()
                if not items:
                    return
                state["idx"] = (state["idx"] + 1) % len(items)
                self.chrome.invalidate()

            kb.add("enter")(_confirm)
            kb.add("c-j")(_confirm)
            kb.add("space")(_confirm)
            kb.add("escape")(lambda _e: _resolve(result, None))
            kb.add("c-c")(lambda _e: _resolve(result, None))

            @kb.add("backspace")
            def _backspace(_e: object) -> None:
                if state["filter"]:
                    state["filter"] = state["filter"][:-1]
                    state["idx"] = 0
                    self.chrome.invalidate()

            # Type-to-filter: catch every other key and append printable
            # single-char data to the filter. ``<any>`` runs ONLY when no
            # earlier (more-specific) binding matched, so arrow keys / Enter /
            # Space / Esc / Backspace are not affected.
            @kb.add("<any>")
            def _filter_char(event: Any) -> None:
                data = getattr(event, "data", None) or ""
                if len(data) == 1 and data.isprintable():
                    state["filter"] += data
                    state["idx"] = 0
                    self.chrome.invalidate()

            # #66 item 3: in fill mode drop dont_extend_height so the Window fills
            # the capped region (top-aligned) instead of hugging its content.
            return Window(
                FormattedTextControl(render, focusable=True, key_bindings=kb),
                dont_extend_height=not fill_screen,
            )

        # NB: name this ``modal_options`` — NOT ``options`` — so it does not rebind
        # the ``options`` LIST parameter that the render/filtered closures capture.
        modal_options = OverlayOptions(fill_screen=True) if fill_screen else None
        return await show_modal(self.chrome, build, options=modal_options)

    async def tabbed(
        self,
        title: str,
        tabs: list[tuple[str, Callable[[], list[str]]]],
        *,
        initial: int = 0,
        filter_tabs: set[int] | None = None,
        fill_screen: bool = False,
    ) -> None:
        """A framed tabbed viewer (WP-8, the /stats + /extension shell).

        Built like :meth:`select` (``show_modal`` + a local ``KeyBindings``), NOT
        ``custom()`` — ``custom``'s Window has no key bindings and cannot handle
        Tab/arrows. Renders a tab header row (the active tab ``_PICK_SEL``), the
        active tab's rendered lines, and a dim hint. Keys: ``Tab`` / ``→`` next
        tab, ``Shift-Tab`` / ``←`` prev tab (both wrap); ``Esc`` / ``Ctrl-C``
        close (and ``q`` on a NON-filterable tab). Each ``render()`` is guarded —
        a raising tab shows an error line instead of breaking the modal. Empty
        ``tabs`` returns immediately.

        :param tabs: ``(tab_name, render)`` pairs; ``render`` is a no-arg callable
            returning the active tab's body lines.
        :param initial: the tab index shown first (clamped into range).
        :param filter_tabs: (Issue #65, ADR-0188) the OPTIONAL set of tab indices
            that accept an in-tab type-to-filter. On a filterable tab printable
            single-char keys append to a live filter string (``state["filter"]``),
            Backspace pops it, and the tab's body lines are filtered
            case-insensitively (substring, on the VISIBLE text) before render; a
            dim ``Filter: <x>`` affordance + a "type to filter" hint show. CRITICAL
            behavior branches (read live per keypress on ``state["idx"]``): on a
            filterable tab ``q`` YIELDS to the filter (you can type 'q'), so
            closing is Esc / Ctrl-C only; on a NON-filterable tab behavior is
            byte-identical to before (``q`` closes, every other key is a no-op).
            The filter RESETS to empty whenever the active tab changes.
            :data:`None` (the default) keeps every tab read-only.
        :param fill_screen: (GitHub #66 item 3) when True the viewer FILLS the
            terminal-bounded modal region (content top-aligned, blank space below)
            instead of hugging its content. Default False keeps natural height.
        """

        if not tabs:
            return None

        state: dict[str, Any] = {"idx": max(0, min(initial, len(tabs) - 1)), "filter": ""}

        def _is_filterable(idx: int) -> bool:
            return filter_tabs is not None and idx in filter_tabs

        def render() -> ANSI:
            idx = max(0, min(state["idx"], len(tabs) - 1))
            state["idx"] = idx
            filterable = _is_filterable(idx)
            # Header row: each tab name, the active one bold-cyan, the rest plain.
            header_cells: list[str] = []
            for i, (name, _render) in enumerate(tabs):
                if i == idx:
                    header_cells.append(f"{_PICK_SEL}{name}{_PICK_RST}")
                else:
                    header_cells.append(name)
            header = "  ".join(header_cells)
            # Active tab body — guarded so a raising formatter never breaks the
            # modal (spec: "a raising tab shows an error line").
            try:
                body_lines = list(tabs[idx][1]())
            except Exception as exc:  # noqa: BLE001 — degrade, never crash
                body_lines = [f"{_PICK_DIM}(this tab failed to render: {exc}){_PICK_RST}"]
            extra_lines: list[str] = []
            if filterable:
                # Filter on the VISIBLE text (strip a line's own ANSI first) so a
                # dim/styled body line still matches by its plain content.
                needle = state["filter"].lower()
                if needle:
                    body_lines = [
                        line for line in body_lines if needle in _ANSI_RE.sub("", line).lower()
                    ]
                    if not body_lines:
                        body_lines = [f"{_PICK_DIM}(no matches){_PICK_RST}"]
                # #66 item 4: dim "Filter:" label, bright-cyan typed value; the
                # "(type to filter)" placeholder stays dim (it isn't typed input).
                extra_lines = [_filter_line(state["filter"], "(type to filter)")]
                hint = "Tab/←→ switch · type to filter · Esc close"
            else:
                hint = "Tab/←→ switch · Esc close"
            # Plain (un-styled) widths for the divider span: measure the raw tab
            # names for the header, and the VISIBLE length of each body line
            # (a tab body may carry its own ANSI — counting the escape bytes
            # would over-pad the frame).
            plain_header_len = len("  ".join(name for name, _ in tabs))
            width = max(
                [len(title), plain_header_len, len(hint)]
                + [_visible_len(line) for line in (*extra_lines, *body_lines)]
            )
            body = [header, *extra_lines, *body_lines]
            return _picker_frame(title, body, hint, width)

        def build(result: asyncio.Future[Any]) -> Window:
            kb = KeyBindings()

            def _next(_e: object) -> None:
                state["idx"] = (state["idx"] + 1) % len(tabs)
                state["filter"] = ""  # a fresh tab starts unfiltered
                self.chrome.invalidate()

            def _prev(_e: object) -> None:
                state["idx"] = (state["idx"] - 1) % len(tabs)
                state["filter"] = ""  # a fresh tab starts unfiltered
                self.chrome.invalidate()

            kb.add("tab")(_next)
            kb.add("right")(_next)
            kb.add("s-tab")(_prev)
            kb.add("left")(_prev)
            kb.add("escape")(lambda _e: _resolve(result, None))
            kb.add("c-c")(lambda _e: _resolve(result, None))

            @kb.add("q")
            def _q(_e: object) -> None:
                # On a filterable tab 'q' is a printable char that must reach the
                # filter (closing is Esc / Ctrl-C there); on a read-only tab it
                # keeps its historical close binding. Branch live on the tab idx.
                if _is_filterable(state["idx"]):
                    state["filter"] += "q"
                    self.chrome.invalidate()
                else:
                    _resolve(result, None)

            # Consume Enter (CR + LF) so it can't leak past the focused viewer to
            # the chrome's global accept (submit / steer) — Enter dismisses this
            # viewer (matches Esc). Same leak-guard the other modals apply
            # (ADR-0121 W-review M1/M2: select/confirm/input/editor bind enter +
            # c-j at control level).
            kb.add("enter")(lambda _e: _resolve(result, None))
            kb.add("c-j")(lambda _e: _resolve(result, None))

            @kb.add("backspace")
            def _backspace(_e: object) -> None:
                # Filter edit on a filterable tab; a no-op elsewhere (matches the
                # prior <any> catch-all, so read-only tabs are unchanged).
                if _is_filterable(state["idx"]) and state["filter"]:
                    state["filter"] = state["filter"][:-1]
                    self.chrome.invalidate()

            # Catch every other (unbound) key. On a filterable tab a printable
            # single char appends to the filter (mirrors select()'s <any>); on a
            # read-only tab it stays a no-op so a stray keystroke never bubbles
            # past the focused viewer into the hidden input buffer. ``<any>`` runs
            # only when no more-specific binding matched, so the tab-switch / close
            # keys above are unaffected.
            @kb.add("<any>")
            def _any(event: Any) -> None:
                if not _is_filterable(state["idx"]):
                    return
                # Accept the printable chars of ``data`` — a single keystroke OR a
                # bracketed paste / IME commit (multi-char) — dropping any control
                # chars so an escape sequence never lands in the filter.
                data = getattr(event, "data", None) or ""
                printable = "".join(ch for ch in data if ch.isprintable())
                if printable:
                    state["filter"] += printable
                    self.chrome.invalidate()

            # #66 item 3: fill mode drops dont_extend_height so the viewer fills
            # the capped region (top-aligned) rather than hugging its content.
            return Window(
                FormattedTextControl(render, focusable=True, key_bindings=kb),
                dont_extend_height=not fill_screen,
            )

        modal_options = OverlayOptions(fill_screen=True) if fill_screen else None
        await show_modal(self.chrome, build, options=modal_options)
        return None

    async def multiselect(
        self,
        title: str,
        options: list[tuple[str, str, str]],
        *,
        selected: set[str],
        extra_toggles: list[tuple[str, str]] | list[tuple[str, str, bool]] | None = None,
        preview: Callable[[set[str], dict[str, bool]], list[str]] | None = None,
        fill_screen: bool = False,
    ) -> tuple[set[str], dict[str, bool]] | None:
        """WP-2 (ADR-0160) — a multi-checkbox picker (sibling to :meth:`select`).

        Reuses the ``select`` scaffolding (``show_modal`` + arrow-nav +
        type-to-filter + viewport + ``<any>`` catch-all + Esc/c-c cancel); the
        only additions are Space=toggle ✓/☐ on the highlight and a live preview
        line. The shared dependency of ``/scoped-models`` (ImplConsumers) and
        ``/statusline``.

        :param options: ``(id, label, description)`` rows — ``id`` is the stable
            key toggled in the returned set; ``description`` is shown under the
            list for the highlighted row (cosmetic, guarded).
        :param selected: the initial checked id set (copied; not mutated).
        :param extra_toggles: optional boolean toggles rendered below the option
            list (e.g. "Use theme colors"). Each is a ``(key, label)`` pair OR a
            ``(key, label, initial)`` triple — when the third element is given it
            seeds the toggle's initial checked state from a persisted value (so a
            stored ``True`` is preserved on confirm instead of silently reset to
            ``False``). Returned as the second tuple element.
        :param preview: optional ``(selected, toggles) -> lines`` callback for a
            live preview block (e.g. a sample footer). Guarded — a raising
            preview never breaks the modal.
        :param fill_screen: (GitHub #66 item 3) when True the picker FILLS the
            terminal-bounded modal region (content top-aligned, blank space below)
            instead of hugging its content. Default False keeps natural height.
        :returns: ``(selected_ids, toggle_states)`` on Enter, or ``None`` on
            Esc / Ctrl+C (caller treats None as "no change").

        .. note::
            The type-to-filter is SINGLE-TOKEN: Space toggles the highlighted row
            (it is not appended to the filter), so a multi-word label like
            ``Permission mode`` can only be matched by a single token substring
            (``permis`` or ``mode``), not the full phrase. This matches the
            single-choice :meth:`select` behavior (pi parity); it is intentional.
        """

        if not options and not extra_toggles:
            return None

        chosen: set[str] = set(selected)
        # Seed each toggle from its optional initial state (the 3rd tuple element,
        # when present); a 2-tuple defaults to ``False``. This preserves a stored
        # ``True`` (e.g. a persisted ``multiline``) across confirm instead of the
        # picker silently resetting it.
        toggles: dict[str, bool] = {
            t[0]: bool(t[2]) if len(t) > 2 else False for t in (extra_toggles or [])
        }
        # ``cursor`` indexes a UNIFIED list: the filtered option rows first, then
        # the extra toggle rows. Space toggles whichever the cursor is on.
        state: dict[str, Any] = {"cursor": 0, "filter": ""}
        viewport = 10

        def filtered_options() -> list[tuple[int, tuple[str, str, str]]]:
            needle = state["filter"].lower()
            if not needle:
                return list(enumerate(options))
            return [
                (i, o)
                for i, o in enumerate(options)
                if needle in o[1].lower() or needle in o[0].lower()
            ]

        def total_rows() -> int:
            return len(filtered_options()) + len(extra_toggles or [])

        def render() -> ANSI:
            # Sprint 6h₃₀ (ADR-0163) — framed + colored, matching select(): the
            # cursor row is bold cyan with a ▸ marker; checkboxes stay ✓/☐;
            # counter / preview / hint are dim; a top+bottom divider frames it.
            opts = filtered_options()
            n_opts = len(opts)
            n_total = total_rows()
            if n_total == 0:
                return _picker_frame(
                    title,
                    [
                        f"{_PICK_DIM}(no matches){_PICK_RST}",
                        _filter_line(state["filter"]),
                    ],
                    "Backspace to clear · Esc to cancel",
                    max(len(title), 40),
                )
            cursor = max(0, min(state["cursor"], n_total - 1))
            state["cursor"] = cursor
            # Scroll window across the OPTION rows only (toggles always trail).
            start = max(0, min(cursor - viewport // 2, max(0, n_opts - viewport)))
            end = min(n_opts, start + viewport)
            plain_rows: list[str] = []  # for width sizing
            body: list[str] = []

            def _row(is_cursor: bool, plain: str) -> str:
                plain_rows.append(plain)
                if is_cursor:
                    return f"{_PICK_SEL}▸ {plain}{_PICK_RST}"
                return f"  {plain}"

            if start > 0:
                body.append(f"{_PICK_DIM}  ⋮{_PICK_RST}")
            for vi in range(start, end):
                _orig_idx, (oid, label, _desc) = opts[vi]
                box = "✓" if oid in chosen else "☐"
                body.append(_row(vi == cursor, f"[{box}] {label}"))
            if end < n_opts:
                body.append(f"{_PICK_DIM}  ⋮{_PICK_RST}")
            # Extra toggle rows (rendered after the option list). Each entry is a
            # ``(key, label)`` pair or a ``(key, label, initial)`` triple.
            for ti, toggle in enumerate(extra_toggles or []):
                key, label = toggle[0], toggle[1]
                box = "✓" if toggles.get(key) else "☐"
                body.append(_row(n_opts + ti == cursor, f"[{box}] {label}"))
            counter = f"({min(cursor + 1, n_total)}/{n_total})"
            if state["filter"]:
                # #66 item 4: brighten the typed VALUE; label/counter stay dim.
                counter += _filter_counter_suffix(state["filter"])
            body.append(f"{_PICK_DIM}  {counter}{_PICK_RST}")
            preview_lines: list[str] = []
            if preview is not None:
                with contextlib.suppress(Exception):
                    pl = preview(set(chosen), dict(toggles))
                    if pl:
                        preview_lines = list(pl)
            for p in preview_lines:
                body.append(f"{_PICK_DIM}{p}{_PICK_RST}")
            hint = "↑/↓ move · Space toggle · Enter confirm · Esc cancel"
            width = max(
                [len(title), _visible_len(counter), len(hint)]
                + [len(r) + 2 for r in plain_rows]
                + [len(p) for p in preview_lines]
            )
            return _picker_frame(title, body, hint, width)

        def build(result: asyncio.Future[Any]) -> Window:
            kb = KeyBindings()

            @kb.add("up")
            def _up(_e: object) -> None:
                n = total_rows()
                if n:
                    state["cursor"] = (state["cursor"] - 1) % n
                    self.chrome.invalidate()

            @kb.add("down")
            def _down(_e: object) -> None:
                n = total_rows()
                if n:
                    state["cursor"] = (state["cursor"] + 1) % n
                    self.chrome.invalidate()

            def _toggle(_e: object) -> None:
                opts = filtered_options()
                n_opts = len(opts)
                cursor = state["cursor"]
                if cursor < n_opts:
                    oid = opts[cursor][1][0]
                    if oid in chosen:
                        chosen.discard(oid)
                    else:
                        chosen.add(oid)
                elif extra_toggles:
                    ti = cursor - n_opts
                    if 0 <= ti < len(extra_toggles):
                        key = extra_toggles[ti][0]
                        toggles[key] = not toggles.get(key)
                self.chrome.invalidate()

            kb.add("space")(_toggle)

            # Enter / c-j CONFIRM (return the full selection). Bound LOCALLY so
            # they never leak to the chrome's global accept (ADR-0121 pattern).
            kb.add("enter")(lambda _e: _resolve(result, (set(chosen), dict(toggles))))
            kb.add("c-j")(lambda _e: _resolve(result, (set(chosen), dict(toggles))))
            kb.add("escape")(lambda _e: _resolve(result, None))
            kb.add("c-c")(lambda _e: _resolve(result, None))

            @kb.add("backspace")
            def _backspace(_e: object) -> None:
                if state["filter"]:
                    state["filter"] = state["filter"][:-1]
                    state["cursor"] = 0
                    self.chrome.invalidate()

            @kb.add("<any>")
            def _filter_char(event: Any) -> None:
                data = getattr(event, "data", None) or ""
                if len(data) == 1 and data.isprintable():
                    state["filter"] += data
                    state["cursor"] = 0
                    self.chrome.invalidate()

            # #66 item 3: fill mode drops dont_extend_height so the picker fills
            # the capped region (top-aligned) rather than hugging its content.
            return Window(
                FormattedTextControl(render, focusable=True, key_bindings=kb),
                dont_extend_height=not fill_screen,
            )

        # NB: ``modal_options`` — NOT ``options`` — to avoid rebinding the
        # ``options`` LIST parameter captured by the render/filtered closures.
        modal_options = OverlayOptions(fill_screen=True) if fill_screen else None
        return await show_modal(self.chrome, build, options=modal_options)

    async def confirm(
        self, title: str, message: str, opts: ExtensionUIDialogOptions | None = None
    ) -> bool:
        def build(result: asyncio.Future[Any]) -> Window:
            kb = KeyBindings()
            for key in ("y", "Y"):
                kb.add(key)(lambda _e: _resolve(result, True))
            for key in ("n", "N", "escape"):
                kb.add(key)(lambda _e: _resolve(result, False))
            # W-review 6h₂₄ LOW-4: Ctrl+C cancels (matches ``select`` + ``editor``).
            # Without this, c-c leaks to the chrome global "clear buffer" while
            # a modal is focused — inconsistent UX across the dialog set.
            kb.add("c-c")(lambda _e: _resolve(result, False))
            # Consume Enter (CR + LF) so it can't leak to the chrome's global
            # accept (ADR-0121 W-review M1). Enter is a deliberate no-op rather
            # than defaulting to "yes" — a confirm must be answered explicitly so
            # a stray Enter never auto-approves a destructive action.
            kb.add("enter")(lambda _e: None)
            kb.add("c-j")(lambda _e: None)
            return Window(
                FormattedTextControl(
                    f"{title}\n{message} [y/n]", focusable=True, key_bindings=kb
                ),
                dont_extend_height=True,
            )

        return bool(await show_modal(self.chrome, build))

    async def input(
        self,
        title: str,
        placeholder: str | None = None,
        opts: ExtensionUIDialogOptions | None = None,
        *,
        password: bool = False,
    ) -> str | None:
        buffer = Buffer(multiline=False)
        # WP-8 (Feature 1) — when ``password`` is set, mask the typed characters
        # so a secret (API key / OAuth code) is NOT echoed to the screen or left
        # in the terminal scrollback. The buffer still holds the real text, only
        # the rendering is replaced (PasswordProcessor draws ``*`` per char).
        processors: list[Processor] | None = (
            [PasswordProcessor()] if password else None
        )

        def build(result: asyncio.Future[Any]) -> HSplit:
            kb = KeyBindings()
            # Bind BOTH c-m (enter) and c-j: with the chrome's main input now
            # treating LF (c-j) as a submit key (ADR-0121 multiline), an unbound
            # c-j here would bubble past the focused modal to that global handler
            # and never resolve the dialog. Binding it at control level keeps
            # "Enter" (CR or LF) resolving the modal regardless.
            kb.add("enter")(lambda _e: _resolve(result, buffer.text))
            kb.add("c-j")(lambda _e: _resolve(result, buffer.text))
            kb.add("escape")(lambda _e: _resolve(result, None))
            # W-review 6h₂₄ LOW-4: c-c cancels (matches confirm/select/editor).
            kb.add("c-c")(lambda _e: _resolve(result, None))
            return HSplit(
                [
                    Window(FormattedTextControl(title), dont_extend_height=True),
                    Window(
                        BufferControl(
                            buffer,
                            key_bindings=kb,
                            input_processors=processors,
                        ),
                        height=Dimension(min=1),
                    ),
                ]
            )

        return await show_modal(self.chrome, build)

    def notify(self, message: str, kind: NotificationKind = "info") -> None:
        # Generation token: a stale timer must not clear a newer notification.
        self._notify_seq += 1
        token = self._notify_seq
        self.chrome.set_status("__notify__", message)
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().call_later(3.0, lambda: self._clear_notify(token))

    def _clear_notify(self, token: int) -> None:
        if token == self._notify_seq:
            self.chrome.set_status("__notify__", None)

    async def editor(self, title: str, prefill: str | None = None) -> str | None:
        buffer = Buffer(multiline=True)
        if prefill:
            buffer.text = prefill
            buffer.cursor_position = len(prefill)

        def build(result: asyncio.Future[Any]) -> HSplit:
            kb = KeyBindings()
            # Esc cancels (consistent with the other dialogs); Ctrl+S saves.
            kb.add("c-s")(lambda _e: _resolve(result, buffer.text))
            kb.add("escape")(lambda _e: _resolve(result, None))
            kb.add("c-c")(lambda _e: _resolve(result, None))
            # Enter (CR + LF) inserts a newline — this is a MULTILINE editor, so
            # Enter must edit, not leak to the chrome's global accept (ADR-0121
            # W-review M2: the editor modal previously lost the newline). Save is
            # Ctrl+S, cancel is Esc.
            kb.add("enter")(lambda _e: buffer.insert_text("\n"))
            kb.add("c-j")(lambda _e: buffer.insert_text("\n"))
            return HSplit(
                [
                    Window(
                        FormattedTextControl(f"{title} (Ctrl+S to save, Esc to cancel)"),
                        dont_extend_height=True,
                    ),
                    Window(BufferControl(buffer, key_bindings=kb), height=Dimension(min=3)),
                ]
            )

        return await show_modal(self.chrome, build)

    # === Raw input (1) =====================================================

    def on_terminal_input(self, handler: TerminalInputHandler) -> Callable[[], None]:
        # NOTE (ADR-0105 deferred): handlers are registered + unsubscribable, but
        # raw-input *dispatch* (feeding keys to handlers + honoring
        # TerminalInputResult.consume/data) is not yet wired into the chrome key
        # processor. Registration is functional; dispatch lands in a later sprint.
        self._terminal_handlers.append(handler)

        def _unsub() -> None:
            with contextlib.suppress(ValueError):
                self._terminal_handlers.remove(handler)

        return _unsub

    # === Status / working (5) ==============================================

    def set_status(self, key: str, text: str | None) -> None:
        self.chrome.set_status(key, text)

    def set_working_message(self, message: str | None = None) -> None:
        self.chrome.set_working_message(message)

    def set_working_visible(self, visible: bool) -> None:
        self.chrome.set_working_visible(visible)

    def set_working_indicator(self, options: WorkingIndicatorOptions | None = None) -> None:
        frames = options.frames if options is not None else None
        interval = options.interval_ms if options is not None else None
        self.chrome.set_working_indicator(frames, interval)

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        self._hidden_thinking_label = label

    # === Layout (5) ========================================================

    def set_widget(
        self,
        key: str,
        content: list[str] | WidgetFactory | None,
        options: ExtensionWidgetOptions | None = None,
    ) -> None:
        above = (options.placement if options else "above_editor") == "above_editor"
        if content is None:
            self.chrome.set_widget(key, None, above=above)
            return
        if callable(content):
            component = content(self._tui, self._theme)
            lines = component.render(_RENDER_WIDTH)
        else:
            lines = list(content)
        self.chrome.set_widget(key, lines, above=above)

    def set_footer(self, factory: FooterFactory | None) -> None:
        self._footer_factory = factory
        self._refresh_footer()

    def set_header(self, factory: HeaderFactory | None) -> None:
        if factory is None:
            self.chrome.set_header_line("")
            return
        component = factory(self._tui, self._theme)
        self.chrome.set_header_line("\n".join(component.render(_RENDER_WIDTH)))

    def set_title(self, title: str) -> None:
        self.chrome.set_title(title)

    # === Custom overlays (1) ===============================================

    async def custom(
        self, factory: CustomComponentFactory, options: CustomOptions | None = None
    ) -> object:
        result: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        def _done(value: object) -> None:
            _resolve(result, value)

        component = factory(self._tui, self._theme, self._kb, _done)
        if inspect.isawaitable(component):
            component = await component
        comp: Component = component  # type: ignore[assignment]

        def build(_result: asyncio.Future[Any]) -> Window:
            return Window(
                FormattedTextControl(
                    lambda: ANSI("\n".join(comp.render(_RENDER_WIDTH))), focusable=True
                ),
                dont_extend_height=True,
            )

        opts = options or CustomOptions()
        ov = opts.overlay_options
        overlay_options = ov() if callable(ov) else ov  # responsive form → evaluate
        return await show_modal(
            self.chrome, build, options=overlay_options, on_handle=opts.on_handle, result=result
        )

    # === Editor remote control (5) =========================================

    def paste_to_editor(self, text: str) -> None:
        self.chrome.paste_to_editor(text)

    def set_editor_text(self, text: str) -> None:
        self.chrome.set_editor_text(text)

    def get_editor_text(self) -> str:
        return self.chrome.get_editor_text()

    def set_editor_component(self, factory: EditorFactory | None) -> None:
        self._editor_factory = factory

    def get_editor_component(self) -> EditorFactory | None:
        return self._editor_factory

    # === Autocomplete (1) ==================================================

    def add_autocomplete_provider(self, factory: AutocompleteProviderFactory) -> None:
        self._autocomplete.append(factory)

    # === Theme (5 + property) ==============================================

    @property
    def theme(self) -> Theme:
        return self._theme

    def get_all_themes(self) -> list[ThemeInfo]:
        return theme_registry.list_theme_infos()

    def get_theme(self, name: str) -> Theme | None:
        return theme_registry.get_theme(name)

    def set_theme(self, theme: str | Theme) -> SetThemeResult:
        if isinstance(theme, str):
            resolved = theme_registry.get_theme(theme)
            if resolved is None:
                return SetThemeResult(success=False, error=f"unknown theme: {theme}")
            self._theme = resolved
        else:
            self._theme = theme
        self.chrome.invalidate()
        return SetThemeResult(success=True)

    def get_tools_expanded(self) -> bool:
        return self._tools_expanded

    def set_tools_expanded(self, expanded: bool) -> None:
        self._tools_expanded = expanded

    # === internal ==========================================================

    def _enabled_segment_ids(self) -> set[str] | None:
        """The set of enabled footer-segment ids (WP-2, ADR-0160).

        ``None`` → use each segment's ``default_enabled`` flag (no store wired /
        fresh install). A wired store reads the user's enabled-id set;
        ``StatuslineStore.load`` degrades to the registry defaults on a missing/
        corrupt file, so this never raises.
        """

        if self._statusline_store is None:
            return None
        with contextlib.suppress(Exception):
            return set(self._statusline_store.load().enabled)
        return None

    def _statusline_multiline(self) -> bool:
        """Whether the footer renders as the WP-8 grouped multi-line block.

        Reads the persisted ``StatuslineConfig.multiline`` flag; ``False`` (the
        default + no-store + corrupt-store path) keeps the single-line footer.
        Never raises — ``load()`` degrades to defaults.
        """

        if self._statusline_store is None:
            return False
        with contextlib.suppress(Exception):
            return bool(self._statusline_store.load().multiline)
        return False

    def _enabled_segment_values(self) -> dict[str, str]:
        """``{segment_id: rendered_value}`` for every enabled, non-empty segment.

        Shared by the single- and multi-line footer composers so both read the
        SAME segment registry (no value re-derivation). The ADR-0159 in-producer
        invariants (badge leading + omit-when-no-provider; steering hidden at
        default) are enforced inside each ``produce``.
        """

        enabled = self._enabled_segment_ids()
        values: dict[str, str] = {}
        for segment in self._segments:
            is_on = (
                segment.default_enabled if enabled is None else segment.id in enabled
            )
            if not is_on:
                continue
            value = segment.produce()
            if value:
                values[segment.id] = value
        return values

    # WP-8 (Feature 5) — the mockup-A grouped row layout: each row lists the
    # segment ids it carries, in render order. Empty rows (no enabled, non-empty
    # segment) are omitted. ``permission-mode`` stays the LEADING segment of its
    # row (ADR-0159). Extension statuses join the LAST row.
    _MULTILINE_ROWS: tuple[tuple[str, ...], ...] = (
        ("model", "git-branch", "context-remaining", "input-tokens", "output-tokens", "cost"),
        ("current-dir",),
        ("permission-mode", "steering", "pending-queued"),
    )

    def _refresh_footer(self) -> None:
        if self._footer_factory is not None:
            component = self._footer_factory(self._tui, self._theme, self._footer)
            rendered = component.render(_RENDER_WIDTH)
            # WP-8 (Feature 5): ``set_footer_line`` now PRESERVES ``\n`` for the
            # multi-line statusline. A factory footer must still honor the
            # default-OFF opt-in — join its lines by ``\n`` only when multi-line
            # mode is on; otherwise collapse to a single row (the pre-WP-8
            # behavior, when ``set_footer_line`` stripped newlines) so an
            # extension footer can't silently grow the chrome unguarded.
            sep = "\n" if self._statusline_multiline() else "  ·  "
            self.chrome.set_footer_line(sep.join(rendered))
            return
        # WP-2 (ADR-0160) — compose from the named segment registry. The registry
        # order is canonical (matches the pre-ADR-0160 hard-coded footer); an
        # enabled-set gates membership. The ADR-0159 invariants (permission badge
        # leading + omit-when-no-provider; steering hidden at default) live INSIDE
        # the producers, so an adversarial/empty enabled-set can only hide a
        # segment the user explicitly unchecked — it can never surface a stray
        # badge or vanish the leading position of the security-visible one.
        values = self._enabled_segment_values()
        # Extension statuses are NOT registry segments (an extension owns its own
        # slot, never user-toggleable) — gathered once for both layouts.
        ext_statuses = [v for v in self._footer.get_extension_statuses().values() if v]

        if self._statusline_multiline():
            # WP-8 (Feature 5) — grouped multi-line block (mockup A). Each row
            # joins its enabled+non-empty segments by ``  ·  ``; empty rows are
            # omitted; extension statuses join the last rendered row's tail.
            rows: list[str] = []
            for row_ids in self._MULTILINE_ROWS:
                cells = [values[sid] for sid in row_ids if sid in values]
                if cells:
                    rows.append("  ·  ".join(cells))
            if ext_statuses:
                ext_row = "  ·  ".join(ext_statuses)
                if rows:
                    rows[-1] = f"{rows[-1]}  ·  {ext_row}"
                else:
                    rows.append(ext_row)
            self.chrome.set_footer_block(rows)
            return

        # Single-line (default): the canonical registry order joined by ``  ·  ``.
        parts = [values[segment.id] for segment in self._segments if segment.id in values]
        parts.extend(ext_statuses)
        self.chrome.set_footer_line("  ·  ".join(parts))

    def set_context_label(self, label: str | None) -> None:
        """Update the live context-window usage segment + repaint the footer.

        Called by ``run_tui`` after ``turn_end`` with a formatted label (or
        ``None`` when usage is unavailable — e.g. model registry not wired).
        """
        self._context_label = label
        self._refresh_footer()

    def set_usage_stats(
        self, input_tokens: int, output_tokens: int, cost: float
    ) -> None:
        """Cache the session usage scalars for the optional token/cost footer
        segments + repaint the footer (WP-2, ADR-0160).

        Called by ``run_tui`` after ``turn_end`` (same cadence as
        :meth:`set_context_label`). The token/cost segments are default-OFF, so
        this is inert until the user enables them via ``/statusline``.
        """

        self._usage_input_tokens = input_tokens
        self._usage_output_tokens = output_tokens
        self._usage_cost = cost
        self._refresh_footer()

    @staticmethod
    def _abbrev_cwd(cwd: str) -> str:
        """Home-abbreviate a path (``/home/x/p`` → ``~/p``); identity otherwise."""
        home = str(Path.home())
        if cwd == home:
            return "~"
        prefix = home.rstrip("/") + "/"
        if cwd.startswith(prefix):
            return "~/" + cwd[len(prefix):]
        return cwd


__all__ = ["AelixKeybindings", "AelixTUI", "AelixTUIContext"]
