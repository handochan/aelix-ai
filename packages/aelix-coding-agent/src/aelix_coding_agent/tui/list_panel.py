"""A list panel that redraws itself: rows held by key, one selection, ``k/N`` scrolling,
an optional detail area and a key row, in an in-flow modal (#776, ADR-0299; ADR-0159).

GENERIC. Nothing here names what the rows are. A host hands :class:`PanelRow`\\ s —
a stable ``key``, a one-cell ``mark``, its ``columns`` and its ``detail`` lines —
lays the columns out with :class:`Column`\\ s, and binds its own keys; the widget
owns the selection, the scrolling, the fixed height, the drawing and the redraw.
``/jobs`` (``tui/jobs_panel.py``) is its first host; it is meant to be reused as
it is.

* :class:`ListPanelModel` — pure state. The selection is held by KEY: rows may
  re-sort or vanish between redraws, and a selected row that vanished hands the
  selection to the row now at its place (:meth:`ListPanelModel.update` says so).
* :func:`render_list_panel` — exactly :attr:`PanelLayout.height` rows, none wider
  than the width read for this frame, measured in terminal cells.
* :class:`ListPanel` — the modal host over ``show_modal``: ``↑``/``↓`` move, the
  close keys close (Esc, ``q`` and Ctrl+C, as every modal's Ctrl+C closes it), a
  host's ``enter`` is Ctrl+J too, every other bound key goes to the host's handler,
  and the rows are read again every ``interval`` seconds (and on
  :meth:`ListPanel.refresh`). It closes by itself when the application ends — a
  SIGTERM's exit, a crash — and when a frame cannot be drawn, so nothing waits on
  a panel no key can reach (#776 fix round 1).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension

from aelix_coding_agent.tui.selection_panel import cells, fit, flat, pad
from aelix_coding_agent.tui.width import terminal_columns

if TYPE_CHECKING:
    from aelix_coding_agent.tui.chrome import AelixChrome

_log = logging.getLogger(__name__)

_SEL = "\x1b[1;36m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_CYAN = "\x1b[36m"
_RST = "\x1b[0m"
_RULE = "─"
_GAP = "  "
_LEAD = 4
"""The cells before a row's columns: the pointer, a space, the mark, a space."""


@dataclass(frozen=True)
class Column:
    """``width`` cells, or ``None`` for the column that takes what is left (at least
    ``min`` cells). Columns with a ``drop`` above 0 go, the highest first, before the
    flexible one would get fewer than ``min``."""

    width: int | None = None
    min: int = 12
    drop: int = 0


@dataclass(frozen=True)
class PanelRow:
    """One row: ``key`` is its identity across redraws."""

    key: str
    columns: tuple[str, ...]
    mark: str = " "
    detail: tuple[str, ...] = ()


@dataclass(frozen=True)
class PanelLayout:
    """``rows`` list rows and ``detail`` detail rows (0: no detail area), fixed while open."""

    rows: int
    detail: int = 0

    @property
    def height(self) -> int:
        """The title row, the list, a rule and the detail when there is one, the key row."""

        return 1 + self.rows + (1 + self.detail if self.detail else 0) + 1


@dataclass
class ListPanelModel:
    layout: PanelLayout
    rows: list[PanelRow] = field(default_factory=list)
    selected: str | None = None
    top: int = 0

    def index(self) -> int:
        keys = [row.key for row in self.rows]
        return keys.index(self.selected) if self.selected in keys else 0

    def current(self) -> PanelRow | None:
        return self.rows[self.index()] if self.rows else None

    def update(self, rows: Sequence[PanelRow]) -> bool:
        """Take the rows read now; whether the selected row is gone (the selection then
        moved to the row now at its place, or to none)."""

        at = self.index()
        had = self.selected
        self.rows = list(rows)
        keys = [row.key for row in self.rows]
        if had in keys:
            self._follow()
            return False
        self.selected = keys[min(at, len(keys) - 1)] if keys else None
        self._follow()
        return had is not None

    def move(self, delta: int) -> bool:
        """Move the selection by ``delta`` rows, held at the ends; whether it moved."""

        if not self.rows:
            return False
        at = max(0, min(len(self.rows) - 1, self.index() + delta))
        moved = self.rows[at].key != self.selected
        self.selected = self.rows[at].key
        self._follow()
        return moved

    def _follow(self) -> None:
        """Scroll so the selection is in view, never past the last row."""

        at, size = self.index(), self.layout.rows
        self.top = min(max(self.top, at - size + 1), at)
        self.top = max(0, min(self.top, len(self.rows) - size))

    def position(self) -> str | None:
        """``k/N`` when there are more rows than fit, else ``None``."""

        if len(self.rows) <= self.layout.rows:
            return None
        return f"{self.index() + 1}/{len(self.rows)}"


def plan_columns(columns: Sequence[Column], width: int) -> list[int | None]:
    """Each column's cells in a row ``width`` cells wide; ``None`` for one dropped."""

    shown: list[int | None] = [c.width if c.width is not None else 0 for c in columns]
    flex = next((i for i, c in enumerate(columns) if c.width is None), None)
    by_drop = sorted((i for i, c in enumerate(columns) if c.drop > 0), key=lambda i: -columns[i].drop)

    def left() -> int:
        kept = [w for w in shown if w is not None]
        return width - _LEAD - sum(kept) - len(_GAP) * (len(kept) - 1)

    for i in by_drop:
        if flex is None or left() >= columns[flex].min:
            break
        shown[i] = None
    if flex is not None:
        shown[flex] = max(1, left())
    return shown


def _item_row(row: PanelRow, selected: bool, widths: Sequence[int | None], width: int) -> str:
    parts = [
        pad(flat(text), w) if i < len(widths) - 1 else fit(flat(text), w)
        for i, (text, w) in enumerate(zip(row.columns, widths, strict=False))
        if w is not None
    ]
    body = fit(_GAP.join(parts).rstrip(), max(1, width - _LEAD))
    pointer = "▸" if selected else " "
    if selected:
        return f"{_SEL}{pointer} {row.mark} {body}{_RST}"
    return f"{pointer} {row.mark} {body}"


def _title_row(title: str, right: str, width: int) -> str:
    """``── Title ──────────── right ──``; the right side goes first when it does not fit."""

    title = fit(flat(title), max(1, width - 6))
    left_plain = f"{_RULE * 2} {title} "
    right_plain = f" {right} {_RULE * 2}" if right else _RULE * 2
    head = f"{_CYAN}{_RULE * 2} {_RST}{_BOLD}{title}{_RST}{_CYAN} "
    fill = width - cells(left_plain) - cells(right_plain)
    if fill < 1:
        return f"{head}{_RULE * max(0, width - cells(left_plain))}{_RST}"
    return f"{head}{_RULE * fill}{right_plain}{_RST}"


def render_list_panel(
    model: ListPanelModel,
    *,
    columns: Sequence[Column],
    title: str,
    right: str,
    hint: str,
    width: int,
) -> list[str]:
    """Exactly ``model.layout.height`` rows, none wider than ``width`` cells."""

    layout = model.layout
    corner = " · ".join(part for part in (right, model.position()) if part)
    rows = [_title_row(title, corner, width)]
    widths = plan_columns(columns, width)
    shown = model.rows[model.top : model.top + layout.rows]
    selected = model.current()
    for row in shown:
        rows.append(_item_row(row, row is selected, widths, width))
    rows.extend([""] * (layout.rows - len(shown)))
    if layout.detail:
        rows.append(f"{_CYAN}{_RULE * width}{_RST}")
        detail = list(selected.detail[: layout.detail]) if selected is not None else []
        rows.extend(f"{_DIM}  {fit(flat(line), max(1, width - 2))}{_RST}" for line in detail)
        rows.extend([""] * (layout.detail - len(detail)))
    rows.append(f"{_DIM}{fit(flat(hint), width)}{_RST}")
    return rows


Handler = Callable[["PanelRow | None"], None]
"""A host's key: called with the selected row (``None`` when there is none)."""


class ListPanel:
    """The modal host. ``rows`` is read at open, every ``interval`` seconds and on :meth:`refresh`.

    ``on_move`` runs when the user moved the selection and ``on_lost`` when the
    selected row vanished from a read (both after the model changed); ``hint``
    is asked for the key row with the width of this frame. A host closes the
    panel with :meth:`close`, whose value :meth:`run` returns.
    """

    def __init__(
        self,
        chrome: AelixChrome,
        *,
        layout: PanelLayout,
        rows: Callable[[], Sequence[PanelRow]],
        columns: Sequence[Column] = (Column(),),
        title: Callable[[], str],
        right: Callable[[], str] = lambda: "",
        hint: Callable[[int], str] = lambda _width: "",
        keys: dict[str, Handler] | None = None,
        close_keys: Sequence[str] = ("escape", "q", "c-c"),
        on_move: Callable[[], None] | None = None,
        on_lost: Callable[[], None] | None = None,
        interval: float = 1.0,
    ) -> None:
        self.chrome = chrome
        self.model = ListPanelModel(layout)
        self.columns = tuple(columns)
        self._rows = rows
        self._title, self._right, self._hint = title, right, hint
        self._keys = dict(keys or {})
        self._close_keys = tuple(close_keys)
        self._on_move, self._on_lost = on_move, on_lost
        self.interval = interval
        self.window: Window | None = None
        self._result: asyncio.Future[Any] | None = None
        #: Takes back :meth:`_close_with_the_app`'s callback once the panel has closed.
        self._unwatch_app: Callable[[], None] = lambda: None

    # --- state -------------------------------------------------------------

    def refresh(self) -> None:
        """Read the rows again now; a redraw follows."""

        try:
            rows = list(self._rows())
        except Exception:  # noqa: BLE001 — a host that cannot answer keeps its last rows
            rows = self.model.rows
        if self.model.update(rows) and self._on_lost is not None:
            self._on_lost()
        self._invalidate()

    def on_top(self) -> bool:
        """Whether this panel is the modal on screen (open, and no dialog over it)."""

        return self.window is not None and self.chrome.modal_on_top(self.window)

    def close(self, value: Any = None) -> None:
        """Close the panel; :meth:`run` returns ``value``."""

        result = self._result
        if result is not None and not result.done():
            result.set_result(value)

    def _invalidate(self) -> None:
        with contextlib.suppress(Exception):
            self.chrome.invalidate()

    def lines(self, width: int | None = None) -> list[str]:
        """This frame's rows, at ``width`` cells (the terminal's by default)."""

        width = max(20, terminal_columns(self.chrome) if width is None else width)
        try:
            return render_list_panel(
                self.model,
                columns=self.columns,
                title=self._title(),
                right=self._right(),
                hint=self._hint(width),
                width=width,
            )
        except Exception:  # noqa: BLE001 — a host that cannot draw closes its panel
            _log.debug("the list panel could not draw; it closes", exc_info=True)
            self.close()
            return [""] * self.model.layout.height

    # --- keys --------------------------------------------------------------

    def press(self, key: str) -> None:
        """One key, as the bindings deliver it."""

        if key in ("up", "down"):
            if self.model.move(-1 if key == "up" else 1) and self._on_move is not None:
                self._on_move()
        elif key in self._close_keys:
            self.close()
        elif key in self._keys:
            self._keys[key](self.model.current())
        self._invalidate()

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()

        def bind(key: str) -> None:
            kb.add(key)(lambda _event: self.press(key))

        for key in dict.fromkeys(("up", "down", *self._close_keys, *self._keys)):
            bind(key)
        if "enter" in self._keys:
            kb.add("c-j")(lambda _event: self.press("enter"))  # as every modal's Enter
        # A key no binding names stops here; a key the application binds (Ctrl+B) is
        # the application's, whose own filters keep it inert under a focused modal.
        kb.add("<any>")(lambda _event: None)
        return kb

    # --- lifecycle -----------------------------------------------------------

    async def run(self) -> Any:
        """Open the panel; once it has closed, the value it was closed with."""

        from aelix_coding_agent.tui.overlay import show_modal  # noqa: PLC0415

        self.refresh()

        def build(result: asyncio.Future[Any]) -> Window:
            self._result = result
            self._close_with_the_app()
            control = FormattedTextControl(
                lambda: ANSI("\n".join(self.lines())), focusable=True, key_bindings=self._bindings()
            )
            self.window = Window(
                control, height=Dimension.exact(self.model.layout.height), dont_extend_height=True
            )
            return self.window

        ticker = asyncio.ensure_future(self._tick())
        try:
            return await show_modal(self.chrome, build)
        finally:
            ticker.cancel()
            await asyncio.wait([ticker])
            self._unwatch_app()
            self.window = None

    def _close_with_the_app(self) -> None:
        """Close when the application's run ends: no key reaches the panel after that, and
        whatever awaits it — the input loop at idle — would wait for ever (SIGTERM, #776)."""

        ended = getattr(getattr(self.chrome, "app", None), "future", None)
        if not isinstance(ended, asyncio.Future):
            return
        if ended.done():
            self.close()
            return

        def on_end(_ended: object) -> None:
            self.close()

        def unwatch() -> None:
            ended.remove_done_callback(on_end)

        ended.add_done_callback(on_end)
        self._unwatch_app = unwatch

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            self.refresh()


__all__ = [
    "Column",
    "Handler",
    "ListPanel",
    "ListPanelModel",
    "PanelLayout",
    "PanelRow",
    "plan_columns",
    "render_list_panel",
]
