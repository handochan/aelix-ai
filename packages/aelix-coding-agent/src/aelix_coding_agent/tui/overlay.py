"""Sprint 6h₁₀b (ADR-0105) — overlays + modal dialog runner.

Sprint 6h₂₈ (ADR-0159) moved the CAPTURING modal :func:`show_modal` from a
centered :class:`~prompt_toolkit.layout.Float` to an IN-FLOW slot mounted in the
chrome HSplit above the input. A Float never contributes to the non-fullscreen
app's rendered height, so a modal taller than the few rows the inline chrome
owns at the terminal bottom overflowed below the terminal edge and clipped (the
user-reported "/model · /settings · approval Yes/No is cut off"). The in-flow
slot is a real HSplit child, so the body's preferred height grows to include the
modal, the renderer allocates the taller region (capped at terminal rows), and
the terminal scrolls prior output UP — the whole modal renders, never clipped.

:func:`make_float` is RETAINED (descriptor toasts + the completions menu keep
using cursor-anchored / short Floats, which never clip); ``show_modal`` simply
stops calling it. ``OverlayOptions.anchor`` / ``offset_x`` / ``offset_y`` become
no-ops for an in-flow modal (it always renders above the input); ``width`` /
``max_height`` from the options ARE honored on the wrapping container.

Implements :class:`~aelix_coding_agent.extensions.widget_protocols.OverlayHandle`
over the modal lifecycle, and provides :func:`show_modal` — the shared primitive
the ``ExtensionUIContext`` dialogs (``select``/``confirm``/``input``/``editor``/
``custom``) AND the WP-0 approval dialog build on: mount a focusable modal in the
slot, await a result Future resolved by the dialog's key bindings, then unmount
the modal and restore focus to the editor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Float
from prompt_toolkit.layout.containers import AnyContainer, Container, to_container
from prompt_toolkit.layout.dimension import Dimension

from aelix_coding_agent.extensions.widget_protocols import OverlayOptions

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prompt_toolkit.key_binding import KeyBindingsBase
    from prompt_toolkit.layout.dimension import Dimension as DimensionT
    from prompt_toolkit.layout.mouse_handlers import MouseHandlers
    from prompt_toolkit.layout.screen import Screen, WritePosition

    from aelix_coding_agent.tui.chrome import AelixChrome

# Rows reserved BELOW the modal for the input editor + status + footer so a
# pathologically tall modal never pushes the prompt off the terminal. The cap is
# computed lazily per-render (callable Dimension) so it tracks terminal resize.
# This is a FLOOR: when the live input window preferred height is reachable the
# reserve grows to ``input + status + footer`` so a multi-line input buffer (the
# editor Dimension is min=1,max=10) can't push the footer off the terminal under
# a near-cap modal (review LOW). Assumes a ~1-row idle input otherwise.
_MODAL_RESERVE_ROWS = 5
# Status (1) + footer (1) rows that always sit below the modal+input.
_MODAL_STATUS_FOOTER_ROWS = 2
# Floor when the terminal size is unavailable / tiny (keeps a usable modal).
_MODAL_MIN_HEIGHT = 3
# Fallback cap when the output size can't be read (never reached under a real
# Output or DummyOutput, which both report a size; defensive only).
_MODAL_FALLBACK_CAP = 20

# Anchor → (top, bottom, left, right) flags. ``True`` means "pin this edge".
_ANCHOR_EDGES: dict[str, tuple[bool, bool, bool, bool]] = {
    "center": (False, False, False, False),
    "top-left": (True, False, True, False),
    "top-right": (True, False, False, True),
    "bottom-left": (False, True, True, False),
    "bottom-right": (False, True, False, True),
    "top-center": (True, False, False, False),
    "bottom-center": (False, True, False, False),
    "left-center": (False, False, True, False),
    "right-center": (False, False, False, True),
}


def make_float(content: AnyContainer, options: OverlayOptions | None = None) -> Float:
    """Build a :class:`Float` placing ``content`` per ``options`` (default: centered)."""

    opts = options or OverlayOptions()
    top_e, bottom_e, left_e, right_e = _ANCHOR_EDGES.get(opts.anchor, (False, False, False, False))
    kwargs: dict[str, Any] = {}
    if top_e:
        kwargs["top"] = max(0, opts.offset_y)
    if bottom_e:
        kwargs["bottom"] = max(0, opts.offset_y)
    if left_e:
        kwargs["left"] = max(0, opts.offset_x)
    if right_e:
        kwargs["right"] = max(0, opts.offset_x)
    if isinstance(opts.width, int):
        kwargs["width"] = opts.width
    return Float(content=content, **kwargs)


class AelixOverlayHandle:
    """Concrete ``OverlayHandle`` over a chrome Float (hide/show/focus).

    Shares the ``hidden`` flag dict with the Float's ``ConditionalContainer``
    filter so :meth:`set_hidden` toggles visibility without rebuilding the Float.
    """

    def __init__(
        self, chrome: AelixChrome, content: AnyContainer, hidden: dict[str, bool]
    ) -> None:
        self._chrome = chrome
        self._content = content
        self._hidden = hidden

    def hide(self) -> None:
        self.set_hidden(True)

    def set_hidden(self, hidden: bool) -> None:
        self._hidden["v"] = hidden
        self._chrome.invalidate()

    def is_hidden(self) -> bool:
        return self._hidden["v"]

    def focus(self) -> None:
        self._chrome.focus(self._content)

    def unfocus(self) -> None:
        self._chrome.focus_input()

    def is_focused(self) -> bool:
        try:
            return self._chrome.app.layout.has_focus(self._content)
        except Exception:  # noqa: BLE001 — focus query before app runs
            return False


def _modal_cap(chrome: AelixChrome, options: OverlayOptions | None) -> int:
    """The max rows a modal may occupy = terminal rows − reserve (input/footer).

    Re-read per call from the live output size so it tracks terminal resize while
    the modal is open (``_CappedContainer.preferred_height`` calls this every
    render). ``OverlayOptions.max_height`` (when an int) tightens the cap further;
    it never loosens it past the terminal bound.
    """

    try:
        rows = chrome.app.output.get_size().rows
    except Exception:  # noqa: BLE001 — size unavailable (pre-run) → fallback
        rows = _MODAL_FALLBACK_CAP + _MODAL_RESERVE_ROWS
    cap = max(_MODAL_MIN_HEIGHT, rows - _reserve_rows(chrome))
    if options is not None and isinstance(options.max_height, int):
        cap = min(cap, max(_MODAL_MIN_HEIGHT, options.max_height))
    return cap


# The chrome input editor Dimension is min=1,max=10 (chrome.py); a multi-line
# buffer grows the editor up to this many rows before it scrolls internally.
_INPUT_MAX_ROWS = 10


def _reserve_rows(chrome: AelixChrome) -> int:
    """Rows to reserve below the modal for the input editor + status + footer.

    Derives the editor's LIVE row count from its buffer line count (the editor
    grows to ``_INPUT_MAX_ROWS`` under a multi-line paste, then scrolls), so a
    near-cap modal plus a tall input can't push the footer off the terminal. The
    editor's explicit ``Dimension(min=1,max=10,preferred=1)`` makes its
    ``preferred_height`` always report 1 regardless of content, so the live count
    must come from the buffer, not the window. Falls back to the
    :data:`_MODAL_RESERVE_ROWS` floor when the buffer is unavailable. (Review LOW.)
    """

    # WP-8 (Feature 5) — the footer may now be multi-line (the mockup-A grouped
    # statusline). The fixed ``_MODAL_STATUS_FOOTER_ROWS`` (status 1 + footer 1)
    # assumed a 1-row footer; read the LIVE footer row count so the reserve grows
    # by the extra footer rows and a near-cap modal never clips. Guarded: an
    # absent/raising accessor floors back to the single-row assumption.
    footer_rows = 1
    counter: Callable[[], int] | None = getattr(chrome, "footer_line_count", None)
    if callable(counter):
        try:
            footer_rows = max(1, int(counter()))
        except Exception:  # noqa: BLE001 — torn-down chrome → 1-row floor
            footer_rows = 1
    # status (1) + the live footer row count.
    status_footer_rows = max(_MODAL_STATUS_FOOTER_ROWS, 1 + footer_rows)

    buffer = getattr(chrome, "buffer", None)
    if buffer is None:
        return max(_MODAL_RESERVE_ROWS, status_footer_rows)
    try:
        line_count = buffer.text.count("\n") + 1
    except Exception:  # noqa: BLE001 — torn-down buffer → floor
        return max(_MODAL_RESERVE_ROWS, status_footer_rows)
    input_rows = max(1, min(line_count, _INPUT_MAX_ROWS))
    return max(_MODAL_RESERVE_ROWS, input_rows + status_footer_rows)


class _CappedContainer(Container):
    """Wrap a child container, clamping its ``preferred_height`` to a live cap.

    Sprint 6h₂₈ (ADR-0159). An explicit ``height=Dimension(max=…)`` on an
    ``HSplit`` forces the HSplit's *preferred* to the Dimension's ``preferred``
    (0/1 — overriding the content-derived height), so a capped HSplit reported 0
    rows and never grew the body. This thin delegating container instead lets the
    child compute its natural ``preferred_height`` and only CLAMPS the result to
    ``cap()`` — so the modal grows with its content up to the terminal bound, then
    the child's own cursor-driven viewport scrolls internally (``select`` /
    approval already self-viewport). The cap is a callable so it tracks resize.
    Everything else delegates straight to the child.

    ``fill`` (GitHub #66 item 3) opts a picker into FILLING the capped region:
    ``preferred_height`` returns the full cap instead of the content-derived
    height, so a SHORT list/tabbed picker expands to the terminal-bounded cap
    (its child Window drops ``dont_extend_height``, so it fills the allocated
    rows and top-aligns its content — blank space below). Default ``False``
    keeps the grow-with-content behavior for natural-height modals.
    """

    def __init__(
        self, child: AnyContainer, cap: Callable[[], int], *, fill: bool = False
    ) -> None:
        self._child: Container = to_container(child)
        self._cap = cap
        self._fill = fill

    def reset(self) -> None:
        self._child.reset()

    def preferred_width(self, max_available_width: int) -> DimensionT:
        return self._child.preferred_width(max_available_width)

    def preferred_height(
        self, width: int, max_available_height: int
    ) -> DimensionT:
        cap = self._cap()
        inner = self._child.preferred_height(width, min(max_available_height, cap))
        clamped_max = min(inner.max, cap) if inner.max is not None else cap
        # Fill mode (GitHub #66 item 3): report the full cap as ``preferred`` so
        # the in-flow slot allocates the whole capped region and the child Window
        # (its ``dont_extend_height`` dropped) fills it — short content top-aligns
        # with blank space below. ``min`` stays the child's clamped min so the
        # HSplit can still shrink the slot under terminal pressure (the invariant
        # that input/status/footer are never pushed off-screen is preserved).
        preferred = clamped_max if self._fill else min(inner.preferred, clamped_max)
        return Dimension(
            min=min(inner.min, clamped_max), max=clamped_max, preferred=preferred
        )

    def write_to_screen(
        self,
        screen: Screen,
        mouse_handlers: MouseHandlers,
        write_position: WritePosition,
        parent_style: str,
        erase_bg: bool,
        z_index: int | None,
    ) -> None:
        self._child.write_to_screen(
            screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
        )

    def is_modal(self) -> bool:
        return self._child.is_modal()

    def get_key_bindings(self) -> KeyBindingsBase | None:
        return self._child.get_key_bindings()

    def get_children(self) -> Sequence[Container]:
        return [self._child]


def _wrap_modal(
    content: AnyContainer, hidden: dict[str, bool], chrome: AelixChrome,
    options: OverlayOptions | None,
) -> AnyContainer:
    """Wrap ``content`` in the hide-toggle + terminal-bounded height cap.

    The :class:`ConditionalContainer` preserves :class:`AelixOverlayHandle`
    ``hide()`` / ``set_hidden()`` semantics (same shared ``hidden`` dict, now
    in-flow rather than in a Float). :class:`_CappedContainer` bounds the modal so
    a pathologically tall one can never push the prompt off the terminal —
    internal scroll handles the remainder for dialogs that report a cursor
    (``select`` / approval already self-viewport).
    """

    inner = ConditionalContainer(content, filter=Condition(lambda: not hidden["v"]))
    fill = bool(options is not None and getattr(options, "fill_screen", False))
    return _CappedContainer(inner, lambda: _modal_cap(chrome, options), fill=fill)


async def show_modal(
    chrome: AelixChrome,
    build_content: Callable[[asyncio.Future[Any]], AnyContainer],
    *,
    options: OverlayOptions | None = None,
    on_handle: Callable[[AelixOverlayHandle], None] | None = None,
    result: asyncio.Future[Any] | None = None,
) -> Any:
    """Mount a focusable in-flow modal; await + return the result it resolves.

    ``build_content`` receives the result Future and returns the (focusable)
    container — its key bindings resolve the Future. Pass ``result`` to await a
    caller-owned Future (e.g. ``custom()`` resolves it via a ``done`` callback).
    A CAPTURING modal is mounted in the chrome's single in-flow slot ABOVE the
    input (so the chrome grows upward and the whole modal stays visible, never
    clipped — ADR-0159), height-capped to the terminal, and steals focus; on
    completion (incl. cancel) it is unmounted and editor focus restored. A
    NON-CAPTURING overlay (``OverlayOptions.non_capturing``) instead takes the
    legacy Float path: it floats over the body, does NOT displace the input or
    steal focus, and coexists with a concurrently-open capturing modal (review
    LOW) — ``anchor`` / ``offset`` are honored for it.

    The public signature is unchanged from the Float era, so every caller
    (select/confirm/input/editor/custom + the approval dialog + descriptor
    management-modal) benefits with zero call-site edits.
    """

    if result is None:
        result = asyncio.get_running_loop().create_future()
    content = build_content(result)
    hidden: dict[str, bool] = {"v": False}
    non_capturing = options is not None and options.non_capturing

    # Non-capturing overlays (e.g. descriptor toasts) keep the Float path: a
    # Float coexists with — and never displaces — a concurrently-mounted
    # capturing modal (the single in-flow slot holds exactly one capturing modal;
    # routing a non-capturing overlay through it would clobber an open picker's
    # slot, and pushing the input down for a toast is the wrong UX). Floats are
    # short for these overlays, so the clip that motivated the in-flow slot does
    # not apply. (ADR-0159, review LOW.)
    if non_capturing:
        inner = ConditionalContainer(content, filter=Condition(lambda: not hidden["v"]))
        float_ = make_float(inner, options)
        if on_handle is not None:
            on_handle(AelixOverlayHandle(chrome, content, hidden))
        chrome.add_float(float_)
        try:
            return await result
        finally:
            chrome.remove_float(float_)
        # NOTE: a non-capturing overlay deliberately does NOT steal focus or
        # restore it, so the input editor keeps focus the whole time.

    slot_content = _wrap_modal(content, hidden, chrome, options)
    if on_handle is not None:
        on_handle(AelixOverlayHandle(chrome, content, hidden))

    chrome.mount_modal(slot_content)
    chrome.focus(content)
    try:
        return await result
    finally:
        chrome.unmount_modal()
        chrome.focus_input()


__all__ = ["AelixOverlayHandle", "make_float", "show_modal"]
