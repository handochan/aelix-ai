"""Sprint 6h₁₀b (ADR-0105) — Float-based overlays + modal dialog runner.

Maps Pi-parity ``OverlayOptions`` (9 anchors + sizing) onto prompt-toolkit
:class:`~prompt_toolkit.layout.Float` placement, implements
:class:`~aelix_coding_agent.extensions.widget_protocols.OverlayHandle` over the
Float lifecycle, and provides :func:`show_modal` — the shared primitive the
``ExtensionUIContext`` dialogs (``select``/``confirm``/``input``/``editor``/
``custom``) build on: add a focusable Float over the chrome, await a result
Future resolved by the dialog's key bindings, then remove the Float and restore
focus to the editor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Float
from prompt_toolkit.layout.containers import AnyContainer

from aelix_coding_agent.extensions.widget_protocols import OverlayOptions

if TYPE_CHECKING:
    from aelix_coding_agent.tui.chrome import AelixChrome

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


async def show_modal(
    chrome: AelixChrome,
    build_content: Callable[[asyncio.Future[Any]], AnyContainer],
    *,
    options: OverlayOptions | None = None,
    on_handle: Callable[[AelixOverlayHandle], None] | None = None,
    result: asyncio.Future[Any] | None = None,
) -> Any:
    """Show a focusable modal Float; await + return the result it resolves.

    ``build_content`` receives the result Future and returns the (focusable)
    container — its key bindings resolve the Future. Pass ``result`` to await a
    caller-owned Future (e.g. ``custom()`` resolves it via a ``done`` callback).
    The Float is removed and editor focus restored on completion (incl. cancel).
    """

    if result is None:
        result = asyncio.get_running_loop().create_future()
    content = build_content(result)
    hidden: dict[str, bool] = {"v": False}
    wrapped = ConditionalContainer(content, filter=Condition(lambda: not hidden["v"]))
    float_ = make_float(wrapped, options)
    if on_handle is not None:
        on_handle(AelixOverlayHandle(chrome, content, hidden))

    chrome.add_float(float_)
    if not (options is not None and options.non_capturing):
        chrome.focus(content)  # non_capturing overlays do not steal focus
    try:
        return await result
    finally:
        chrome.remove_float(float_)
        chrome.focus_input()


__all__ = ["AelixOverlayHandle", "make_float", "show_modal"]
