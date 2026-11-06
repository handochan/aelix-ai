"""The shared on/off panel for one resource kind (#511, ADR-0279 D11; layout A).

Three parts:

* :class:`SelectionModel` — pure state: rows, cursor, search, the "Save to"
  scope, unsaved edits. :meth:`SelectionModel.toggle` returns the refusal for
  a locked row instead of changing it.
* :func:`render_panel` / :class:`SelectionList` — the model drawn as rows no
  wider than the width read for THIS frame (``terminal_columns`` under the
  ADR-0219 ceiling, not ``_PICK_MAX_WIDTH``), and exactly ``height`` rows, so
  the panel does not grow or shrink while it is open (ADR-0194).
* :class:`SelectionPanelBody` — the panel without its host: the keys, the
  rendering and the save/close lifecycle (#510, ADR-0283 §6). Enter saves
  through :class:`ResourceSelection` and applies (a kind whose effect is
  ``reload`` saves only); Esc discards.
* :func:`run_selection_panel` — the standalone modal host over ``show_modal``:
  it maps keys to the body and closes in ONE frame — the summary is printed and
  the panel unmounted inside the same ``print_above_many`` suspend. #25 embeds
  the body in ``tabbed`` instead.

``/skill`` (#511) and ``/tool`` (#510) use it; ``/extension``'s Installed tab
(#25) passes its own :class:`ResourceKind`.

Every string that comes from a file — a name, a description, a path — is
flattened before it is measured: control characters are shown by name
(``approval_dialog._shown``, #179), never written to the terminal.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from rich.text import Text

from aelix_coding_agent.core.resource_filter import Scope
from aelix_coding_agent.core.resource_selection import ApplyResult, SaveResult
from aelix_coding_agent.tui.width import cluster_cells, graphemes, terminal_columns

if TYPE_CHECKING:
    from aelix_coding_agent.core.resource_selection import (
        ItemState,
        ResourceKind,
        ResourceSelection,
        Snapshot,
    )

_SEL = "\x1b[1;36m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_CYAN = "\x1b[36m"
_YELLOW = "\x1b[33m"
_RED = "\x1b[31m"
_RST = "\x1b[0m"
_RULE = "─"

_NAME_MIN = 12
_NAME_MAX = 24
_NARROW = 60  # below this the description column is dropped
_SCOPE_WORD = {"global": "Global", "project": "This project"}


# --- text -------------------------------------------------------------------


def flat(text: str) -> str:
    """One row of file-sourced *text*: steering characters named, newlines and tabs too."""

    from aelix_coding_agent.tui.approval_dialog import _shown  # noqa: PLC0415

    return _shown(text, one_row=True)[0]


def cells(text: str) -> int:
    if text.isascii():
        return len(text)
    clusters = graphemes(text)
    return sum(cluster_cells(c) for c in clusters)


def fit(text: str, width: int) -> str:
    """*text* cut to at most *width* cells, ending in ``…`` when cut (CJK-aware)."""

    if width <= 0:
        return ""
    if cells(text) <= width:
        return text
    out: list[str] = []
    used = 0
    for cluster in graphemes(text):
        w = cluster_cells(cluster)
        if used + w > width - 1:
            break
        out.append(cluster)
        used += w
    return "".join(out) + "…"


def pad(text: str, width: int, *, right: bool = False) -> str:
    """*text* fitted to *width* and padded with spaces to exactly that many cells."""

    shown = fit(text, width)
    gap = " " * max(0, width - cells(shown))
    return gap + shown if right else shown + gap


def _chunks(word: str, width: int) -> list[str]:
    """*word* split into pieces of at most *width* cells, at cluster boundaries."""

    out: list[str] = []
    piece, used = "", 0
    for cluster in graphemes(word):
        w = cluster_cells(cluster)
        if used + w > width and piece:
            out.append(piece)
            piece, used = "", 0
        piece += cluster
        used += w
    if piece:
        out.append(piece)
    return out


def _pieces(word: str, width: int) -> list[str]:
    """A word wider than *width*, broken after a path separator where one fits (#510 V12).

    A settings path in a save summary was cut mid-name at 50 columns
    (``…/agent/setti`` / ``ngs.json``); a piece with no separator to break
    at falls back to :func:`_chunks`.
    """

    out: list[str] = []
    rest = word
    while cells(rest) > width:
        cut = max(
            (i + 1 for i, ch in enumerate(rest) if ch in "/\\" and cells(rest[: i + 1]) <= width),
            default=0,
        )
        if cut <= 1 or cut == len(rest):
            # Nothing to break at but a leading separator: a lone "/" row helps no one.
            return [*out, *_chunks(rest, width)]
        out.append(rest[:cut])
        rest = rest[cut:]
    return [*out, rest]


def wrap(text: str, width: int, rows: int) -> list[str]:
    """*text* wrapped by word to *width* cells, at most *rows* rows, the last cut with ``…``."""

    if width <= 0 or rows <= 0:
        return []
    lines: list[str] = []
    line = ""
    for word in text.split():
        for piece in _pieces(word, width) if cells(word) > width else [word]:
            candidate = f"{line} {piece}" if line else piece
            if cells(candidate) <= width:
                line = candidate
                continue
            lines.append(line)
            line = piece
    if line:
        lines.append(line)
    if len(lines) > rows:
        lines = lines[:rows]
        lines[-1] = (
            fit(lines[-1] + " …", width)
            if cells(lines[-1]) + 2 <= width
            else fit(lines[-1] + "…" * 2, width)
        )
    return lines


# --- model ------------------------------------------------------------------


@dataclass
class SelectionModel:
    """Everything the panel shows, with no terminal attached."""

    snapshot: Snapshot
    title: str
    noun: tuple[str, str] = ("item", "items")
    cursor: int = 0
    scroll: int = 0
    search: str = ""
    target: Scope = "global"
    edits: dict[str, bool] = field(default_factory=dict)
    """Row key -> the value Enter saves. All of them go to :attr:`target` (one scope per save)."""
    message: tuple[str, str] | None = None
    """``(style, text)`` for the state line: ``error`` red, ``warn`` yellow, ``info`` dim."""
    confirm_create: bool = False
    busy: bool = False
    closing: bool = False
    effect: str = "next_request"
    """The kind's :data:`~.resource_selection.Effect`: ``reload`` words a pending
    row "/reload applies it", and Enter saves without applying (#25)."""
    tabbed: bool = False
    """Embedded in ``tabbed`` (#25): the key hint names Tab."""

    # --- reading -----------------------------------------------------------

    @property
    def states(self) -> tuple[ItemState, ...]:
        return self.snapshot.states

    @property
    def dirty(self) -> bool:
        return bool(self.edits)

    def visible(self) -> list[ItemState]:
        needle = self.search.lower()
        if not needle:
            return list(self.states)
        return [
            s
            for s in self.states
            if needle in s.item.label.lower() or needle in s.item.description.lower()
        ]

    def current(self) -> ItemState | None:
        rows = self.visible()
        if not rows:
            return None
        self.cursor = max(0, min(self.cursor, len(rows) - 1))
        return rows[self.cursor]

    def shown(self, state: ItemState) -> bool:
        """The box: a lock's value, else the unsaved edit, else the saved value."""

        if state.lock is not None:
            return state.lock.value
        edit = self.edits.get(state.item.key)
        return edit if edit is not None else state.saved

    def status(self, state: ItemState) -> tuple[str, str]:
        """``(text, style)`` for the right-hand column."""

        lock = state.lock
        if lock is not None:
            if state.problem is not None:
                # #683 — a locked row can fail to load too (an ``-e`` extension
                # whose setup raised): the lock's tag hid it.
                return "failed", _RED
            if lock.tag:
                return lock.tag, _DIM
            shadowed = lock.kind == "shadowed" or lock.reason.startswith("shadowed")
            return ("shadowed" if shadowed else "locked"), _DIM
        if state.item.key in self.edits:
            # Before "failed" (#25): turning a failed extension off is an edit.
            return "* unsaved", _CYAN
        if state.problem is not None:
            return "failed", _RED
        if state.pending:
            after = "/reload" if self.effect == "reload" else "pending"
            return f"{'on' if state.saved else 'off'} · {after}", _YELLOW
        if state.decided_by == "project":
            return "project", _DIM
        if not state.saved:
            return "off", _DIM
        if state.live is False:
            return "off now", _DIM
        return "", ""

    def counts(self) -> tuple[int, int, int]:
        """``(items, off, locked)`` — placeholder and info rows are not items (#510)."""

        items = [s for s in self.states if s.item.role == "item"]
        off = sum(1 for s in items if s.lock is None and not self.shown(s))
        locked = sum(1 for s in items if s.lock is not None)
        return len(items), off, locked

    def scope_writable(self, scope: Scope) -> tuple[bool, str | None]:
        status = self.snapshot.scopes.get(scope)
        if status is None:
            return False, "not available"
        return status.writable, status.reason

    # --- editing -----------------------------------------------------------

    def _moved(self) -> None:
        """A refusal is about the row it was said for; moving away clears it (V2).

        The create-the-project-file question stays until it is answered.
        """

        if not self.confirm_create:
            self.message = None

    def move(self, delta: int) -> None:
        rows = self.visible()
        if rows:
            self.cursor = (self.cursor + delta) % len(rows)
        self._moved()

    def jump(self, delta: int) -> None:
        rows = self.visible()
        if rows:
            self.cursor = max(0, min(len(rows) - 1, self.cursor + delta))
        self._moved()

    def home(self) -> None:
        self.cursor = 0
        self._moved()

    def end(self) -> None:
        self.cursor = max(0, len(self.visible()) - 1)
        self._moved()

    def toggle(self) -> str | None:
        """Flip the cursor row. Returns why not, for a locked row (and changes nothing)."""

        state = self.current()
        if state is None:
            return None
        self.confirm_create = False
        if state.item.role == "info":
            refusal = (
                (state.lock.refusal or state.lock.reason)
                if state.lock is not None
                else f"{state.item.label} is status only"
            )
            self.message = ("warn", refusal)
            return refusal
        if state.lock is not None:
            if state.lock.kind == "unavailable":
                name = f"{state.item.label} is unavailable"
            elif state.item.identities:
                name = f"{state.item.label} is locked"
            else:
                name = "Locked"
            refusal = f"{name}: {state.lock.reason}"
            self.message = ("warn", refusal)
            return refusal
        want = not self.shown(state)
        if want == state.saved:
            self.edits.pop(state.item.key, None)
        else:
            self.edits[state.item.key] = want
        self.message = None
        return None

    def set_target(self, scope: Scope) -> str | None:
        writable, reason = self.scope_writable(scope)
        self.confirm_create = False
        if not writable:
            refusal = f"Cannot save to {_SCOPE_WORD[scope]}: {reason}"
            self.message = ("warn", refusal)
            return refusal
        # Unsaved edits follow the control: ONE file is written per Enter (C20).
        self.target = scope
        self.message = None
        return None

    def type(self, char: str) -> None:
        self.search += char
        self.cursor = 0
        self.scroll = 0
        self._moved()

    def backspace(self) -> None:
        if self.search:
            self.search = self.search[:-1]
            self.cursor = 0
            self.scroll = 0
            self._moved()


# --- rendering --------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    description_rows: int
    banner_rows: int

    @property
    def fixed_rows(self) -> int:
        # title, search, banners, bottom rule, detail name, description, state, hint
        return 2 + self.banner_rows + 1 + 1 + self.description_rows + 1 + 1

    @property
    def viewport(self) -> int:
        return max(1, self.height - self.fixed_rows)


def _banners(model: SelectionModel) -> list[tuple[str, str]]:
    rows = [(n, _DIM) for n in model.snapshot.notices]
    problems = list(model.snapshot.problems)
    if model.snapshot.unreadable:
        rows.append(
            (f"⚠ {model.snapshot.unreadable[0]} — nothing is applied until it can be read", _RED)
        )
    elif problems:
        more = f" (+{len(problems) - 1} more; see the launch warnings)" if len(problems) > 1 else ""
        rows.append((f"⚠ {problems[0]}{more}", _YELLOW))
    return rows


#: The most rows one banner wraps to (#25 verify V10: a reload failure's reason
#: was cut at 50 and 80 columns); a longer one ends in ``…``.
BANNER_ROWS = 4


def _banner_lines(model: SelectionModel, width: int) -> list[tuple[str, str]]:
    """Each banner wrapped to *width* (less the one-cell margin), with its style."""

    out: list[tuple[str, str]] = []
    for text, style in _banners(model):
        for line in wrap(flat(text), max(1, width - 1), BANNER_ROWS) or [""]:
            out.append((line, style))
    return out


def plan_layout(model: SelectionModel, width: int, rows_available: int) -> Layout:
    """The panel's size when it opens: ``min(rows available, content)`` (ADR-0279 D11)."""

    description_rows = 2 if width >= 70 else 3
    banner_rows = len(_banner_lines(model, width))
    groups = {s.item.group for s in model.states}
    list_rows = len(model.states) + len(groups)
    # Fixed while the panel is open (ADR-0194): searching or a refresh never
    # resizes it, so the content counted here is the whole list.
    content = 2 + banner_rows + max(1, list_rows) + 1 + 1 + description_rows + 1 + 1
    return Layout(width, max(1, min(rows_available, content)), description_rows, banner_rows)


def _display(model: SelectionModel) -> tuple[list[tuple[str, Any]], dict[int, int]]:
    """Rows to draw (``("h", label)`` / ``("i", visible index)``) and visible index → row."""

    rows: list[tuple[str, Any]] = []
    where: dict[int, int] = {}
    group = None
    for index, state in enumerate(model.visible()):
        if state.item.group != group:
            group = state.item.group
            rows.append(("h", state.item.group_label))
        where[index] = len(rows)
        rows.append(("i", index))
    return rows, where


def _window(model: SelectionModel, viewport: int) -> list[tuple[str, Any]]:
    """The display rows in view: never a group header with none of its rows under it.

    At the TOP (#509) a header stays pinned over its group, and a one-row group
    in view only as that row starts at the next header instead. At the BOTTOM
    (#510; #509 verify R5) a header on the last row whose rows are all below
    the window is drawn as a blank row — never alone, and the list does not
    scroll for it.
    """

    display, where = _display(model)
    if not display:
        return []
    pos = where.get(model.cursor, 0)
    scroll = model.scroll
    if pos < scroll:
        scroll = pos
    if pos >= scroll + viewport:
        scroll = pos - viewport + 1
    scroll = max(0, min(scroll, max(0, len(display) - viewport)))
    if viewport < 2:
        # One row: the row Space would toggle, without its header (Codex X8).
        model.scroll = pos
        return [("i", model.cursor)]
    if scroll > 0 and display[scroll][0] == "i" and pos == scroll:
        scroll -= 1

    def _at(start: int) -> tuple[list[tuple[str, Any]], int]:
        rows = list(display[start : start + viewport])
        if start > 0 and rows and rows[0][0] == "i":
            if len(rows) > 1 and rows[1][0] == "i":
                # The top row's group header stays pinned above the rest of its group.
                index = rows[0][1]
                rows[0] = ("h", model.visible()[index].item.group_label)
            else:
                # The top row is the last of its group in view, so a header
                # pinned there would stand over the next group's with no row of
                # its own (#509 verify V6: every untrusted ``.agents`` folder is a
                # one-row group). Start at that next header instead; the cursor
                # is below it, and at the end of the list one row is left blank.
                start += 1
                rows = list(display[start : start + viewport])
        return rows, start

    rows, scroll = _at(scroll)
    if rows and rows[-1][0] == "h" and scroll + len(rows) < len(display):
        # The bottom header's rows are all below the window. Drawn blank, not
        # scrolled into view: a list that moves while the cursor is mid-window
        # (measured at 80x24: ↓ from the first row scrolled it) loses the row
        # the user just left. The header shows once the cursor reaches it.
        rows[-1] = ("blank", None)
    model.scroll = scroll
    return rows


def _columns(model: SelectionModel, width: int) -> tuple[int, int, int]:
    """``(name, description, status)`` widths; description 0 when the panel is narrow."""

    names = [cells(flat(s.item.label)) for s in model.states] or [_NAME_MIN]
    status_w = max([cells(model.status(s)[0]) for s in model.states] + [9 if model.states else 0])
    fixed = 3 + 4  # " ▸ " + "[✓] "
    if width < _NARROW:
        # The status column gives way to keep four cells of name, so that
        # ``fixed + name + 1 + status`` never passes the width (V5: 20–21 did).
        status_w = max(0, min(status_w, width - fixed - 1 - 4))
        name_w = max(1, width - fixed - 1 - status_w)
        return name_w, 0, status_w
    name_w = max(_NAME_MIN, min(_NAME_MAX, max(names)))
    desc_w = width - fixed - name_w - 2 - 1 - status_w
    if desc_w < 12:
        return max(1, width - fixed - 1 - status_w), 0, status_w
    return name_w, desc_w, status_w


def _title_row(model: SelectionModel, width: int) -> str:
    """``── Skills ──────── Save to [Global]  This project ──`` (narrow: ``[Global] Project``)."""

    narrow = width < _NARROW
    words = {"global": "Global", "project": "Project" if narrow else "This project"}
    plain: list[str] = []
    styled: list[str] = []
    for scope in ("global", "project"):
        word = words[scope]
        if model.target == scope:
            plain.append(f"[{word}]")
            styled.append(f"{_BOLD}[{word}]{_RST}")
        elif not model.scope_writable(scope)[0]:
            plain.append(word)
            styled.append(f"{_DIM}{word}{_RST}")
        else:
            plain.append(word)
            styled.append(word)
    sep = " " if narrow else "  "
    label = "" if narrow else "Save to "
    right_plain = f" {label}{sep.join(plain)} {_RULE * 2}"
    right = f" {label}{sep.join(styled)}{_CYAN} {_RULE * 2}{_RST}"
    title = fit(flat(model.title), max(1, width - 6))
    left_plain = f"{_RULE * 2} {title} "
    left = f"{_CYAN}{_RULE * 2} {_RST}{_BOLD}{title}{_RST}"
    fill = width - cells(left_plain) - cells(right_plain)
    if fill < 1:
        # The scope control does not fit: the rule and the title alone.
        return f"{left}{_CYAN} {_RULE * max(0, width - cells(left_plain))}{_RST}"
    return f"{left}{_CYAN} {_RULE * fill}{_RST}{right}"


def _search_row(model: SelectionModel, width: int, viewport: int) -> str:
    """`` Search: query█                       (3/112) · 7 skills · 2 off · 1 locked``."""

    total, off, locked = model.counts()
    visible = model.visible()
    display, _where = _display(model)
    position = f"({model.cursor + 1}/{len(visible)})" if len(display) > viewport and visible else ""
    noun = model.noun[0] if total == 1 else model.noun[1]
    # A placeholder row is not a match (#510); a read-only status row the
    # search shows is (#25 verify V12: "0 matches" over a listed MCP server).
    matched = sum(1 for s in visible if s.item.role != "placeholder")
    # Most important first; whole parts are dropped from the end, never cut.
    parts = [
        p
        for p in (
            position,
            f"{matched} match{'' if matched == 1 else 'es'}" if model.search else "",
            f"{total} {noun}",
            f"{off} off" if off else "",
            f"{locked} locked" if locked else "",
        )
        if p
    ]
    label = " Search: "
    query = flat(model.search)
    # The query keeps at least 8 cells; the counts give way first.
    budget = max(0, width - cells(label) - 1 - 8 - 2)
    while len(parts) > 1 and cells(" · ".join(parts)) > budget:
        parts.pop()
    right = fit(" · ".join(parts), budget)
    room = max(1, width - cells(label) - 1 - (cells(right) + 2 if right else 0))
    if cells(query) > room:
        # Show the END of a long query: that is where the user is typing.
        kept: list[str] = []
        used = 1
        for cluster in reversed(graphemes(query)):
            w = cluster_cells(cluster)
            if used + w > room:
                break
            kept.insert(0, cluster)
            used += w
        query = "…" + "".join(kept)
    gap = width - cells(label) - cells(query) - 1 - cells(right)
    return (
        f"{_DIM}{label}{_RST}{_SEL}{query}{_RST}{_DIM}█{_RST}{' ' * max(0, gap)}{_DIM}{right}{_RST}"
    )


def _item_row(
    model: SelectionModel, state: ItemState, is_cursor: bool, cols: tuple[int, int, int]
) -> str:
    name_w, desc_w, status_w = cols
    on = model.shown(state)
    if state.item.role == "info":
        # A dot, not a box: status only (#25's MCP servers).
        box = " ● " if on else " ○ "
    elif state.lock is not None or state.item.role == "placeholder":
        # Round brackets: this row cannot be toggled.
        box = "(✓)" if on and state.item.role == "item" else "( )"
    else:
        box = "[✓]" if on else "[ ]"
    marker = " ▸ " if is_cursor else "   "
    name = pad(flat(state.item.label), name_w)
    desc = ""
    if desc_w:
        desc = "  " + pad(flat(state.item.description), desc_w)
    status_text, status_style = model.status(state)
    status = " " + pad(status_text, status_w, right=True)
    body = f"{marker}{box} {name}{desc}"
    if is_cursor:
        return f"{_SEL}{body}{status}{_RST}"
    base = _DIM if (state.lock is not None or not on) else ""
    styled_status = f"{status_style}{status}{_RST}" if status_style else status
    return f"{base}{body}{_RST}{styled_status}"


def _detail_rows(model: SelectionModel, layout: Layout) -> list[str]:
    width = layout.width
    state = model.current()
    rows: list[str] = []
    if state is None:
        rows.append(f"{_DIM} {fit('No match. Backspace clears the search.', width - 1)}{_RST}")
        rows.extend([""] * layout.description_rows)
    else:
        item = state.item
        where = flat(item.location or item.path or "")
        head = f" {flat(item.label)}" + (f" · {where}" if where else "")
        rows.append(f"{_BOLD}{fit(head, width)}{_RST}")
        refused = _refusal_rows(model, width, layout.description_rows + 1)
        if refused:
            return rows + refused
        described = wrap(flat(item.description), width - 1, layout.description_rows)
        rows.extend(f" {line}" for line in described)
        rows.extend([""] * (layout.description_rows - len(described)))
    rows.append(_state_line(model, state, width))
    return rows


def _target_words(text: str, width: int) -> str:
    """*text* with the title row's word for this project's file at *width* (#510 r2)."""

    return text.replace(_SCOPE_WORD["project"], "Project") if width < _NARROW else text


def _refusal_rows(model: SelectionModel, width: int, rows: int) -> list[str]:
    """A refused save's reason over the description rows and the state line (#510 r2).

    Only when one row would cut it: the reason says what to do first and
    then why, and the why was never on screen (verify r1 R1). Exactly *rows*
    rows, so the panel's height does not change.
    """

    if model.message is None or model.message[0] != "error":
        return []
    text = flat(_target_words(model.message[1], width))
    if cells(text) <= width - 1:
        return []
    lines = wrap(text, width - 1, rows)
    return [f"{_RED} {line}{_RST}" for line in lines] + [""] * (rows - len(lines))


def _word(value: bool) -> str:
    return "on" if value else "off"


def _state_line(model: SelectionModel, state: ItemState | None, width: int) -> str:
    # Every piece here can carry a file's text — a lock reason names a path, a
    # save error quotes one — so the WHOLE line is flattened before it is
    # measured (Codex X7), as names and descriptions are.
    if model.message is not None:
        style, text = model.message
        colour = {"error": _RED, "warn": _YELLOW}.get(style, _DIM)
        return f"{colour} {fit(flat(_target_words(text, width)), width - 1)}{_RST}"
    if state is None:
        return ""
    lock = state.lock
    if lock is not None:
        kind = lock.kind
        if kind == "shadowed" or (not kind and not state.item.invocation and state.item.identities):
            # A shadowed copy: the saved value is its winner's, not its own
            # (V7). An unset kind keeps #511's inference for a skill row.
            text = f"Not loaded: {lock.reason}"
        elif kind == "unavailable":
            text = f"Unavailable: {lock.reason}"
        elif kind == "status":
            text = lock.reason
        else:
            new_kind = kind in ("cli", "profile", "always_on")
            word = f" {_word(lock.value)}" if new_kind else ""
            text = f"Locked{word}: {lock.reason}"
            # Both values when they disagree (ADR-0279 D13) — for the run locks
            # only "saved off, locked on", the one that changes after the run:
            # every saved-on row under ``--no-tools`` said "Saved on" (#510 grid).
            disagree = state.saved != lock.value and (not new_kind or not state.saved)
            if state.item.identities and state.item.role == "item" and disagree:
                decided = " (this project's file)" if state.decided_by == "project" else ""
                text = f"Saved {_word(state.saved)}{decided} · {text}"
            if lock.value and state.live is False:
                # A run lock lets an extension narrow, never widen (ADR-0283).
                text += " · off now (turned off this run)"
        if state.problem is not None:
            # #683 — the load error, after what locks the row (an ``-e`` row
            # said only "Locked on: loaded by -e for this run").
            text += f" · failed to load: {state.problem}"
            return f"{_RED} {fit(flat(text), width - 1)}{_RST}"
        return f"{_DIM} {fit(flat(text), width - 1)}{_RST}"
    if state.problem is not None and state.item.key not in model.edits:
        text = f"Saved {_word(state.saved)} · failed to load: {state.problem}"
        return f"{_RED} {fit(flat(text), width - 1)}{_RST}"
    decided = " (this project's file)" if state.decided_by == "project" else ""
    now = state.live if state.live is not None else state.applied
    text = f"Saved {_word(state.saved)}{decided} · now {_word(now)}"
    if state.live is False and state.applied:
        text += " (turned off this run)"
    edit = model.edits.get(state.item.key)
    if edit is not None:
        # The title row's words (#510 V12): "This project" did not fit at 50 columns.
        word = "Project" if width < _NARROW and model.target == "project" else None
        text += f" · {_word(edit)} after Enter ({word or _SCOPE_WORD[model.target]})"
        if model.target == "global" and state.decided_by == "project":
            text += " — this project's file still decides here"
    elif state.pending:
        text += (
            " · /reload applies it"
            if model.effect == "reload"
            else " · Enter applies the saved value"
        )
    return f"{_DIM} {fit(flat(text), width - 1)}{_RST}"


def _hint(model: SelectionModel, width: int) -> str:
    if model.confirm_create:
        text = "Enter create and save · Esc back · next launch may ask; -p may need --approve"
    elif model.tabbed and width < _NARROW:
        text = "Tab ↑↓ Space ←→scope Enter save Esc"
    elif model.tabbed:
        text = "Tab tabs · ↑↓ · Space toggle · ←→ save to · Enter save · Esc cancel · search"
    elif width < _NARROW:
        text = "↑↓ Space ←→scope Enter save Esc cancel"
    else:
        text = "↑↓ move · Space toggle · ←→ save to · Enter save · Esc cancel · type to search"
    return f"{_DIM} {fit(text, width - 1)}{_RST}"


def tab_title_row(
    title: str,
    names: list[str],
    active: int,
    width: int,
    model: SelectionModel | None = None,
) -> str:
    """``── Extensions  [Installed] Discover Sources ── Save to [Global]  This project ──``.

    #25 (layout A): ``tabbed``'s tab row merged into the panel's title row, so
    the list keeps the row. Without *model* (a read-only tab) only the tabs.
    What does not fit goes in order: the "Save to" label and the title, then
    "This project" shortens to "Project"; the tabs themselves always stay.
    """

    tabs_plain: list[str] = []
    tabs_styled: list[str] = []
    for index, name in enumerate(names):
        word = flat(name)
        if index == active:
            tabs_plain.append(f"[{word}]")
            tabs_styled.append(f"{_SEL}[{word}]{_RST}")
        else:
            tabs_plain.append(word)
            tabs_styled.append(word)
    tabs_p = " ".join(tabs_plain)
    tabs_s = " ".join(tabs_styled)

    def _right(label: str, sep: str, project: str, tail: bool) -> tuple[str, str]:
        if model is None:
            return "", ""
        plain: list[str] = []
        styled: list[str] = []
        for scope in ("global", "project"):
            word = "Global" if scope == "global" else project
            if model.target == scope:
                plain.append(f"[{word}]")
                styled.append(f"{_BOLD}[{word}]{_RST}")
            elif not model.scope_writable(scope)[0]:
                plain.append(word)
                styled.append(f"{_DIM}{word}{_RST}")
            else:
                plain.append(word)
                styled.append(word)
        end = f" {_RULE * 2}" if tail else ""
        return (
            f" {label}{sep.join(plain)}{end}",
            f" {label}{sep.join(styled)}{_CYAN}{end}{_RST}",
        )

    shapes = (
        (True, "Save to ", "  ", "This project", True),
        (False, "Save to ", "  ", "This project", True),
        (False, "", " ", "Project", True),
        (False, "", " ", "Project", False),
    )
    for show_title, label, sep, project, tail in shapes[2 if width < _NARROW else 0 :]:
        lead = _RULE * (2 if show_title else 1)
        head_plain = f"{lead} " + (f"{flat(title)}  " if show_title else "") + tabs_p + " "
        head = (
            f"{_CYAN}{lead} {_RST}"
            + (f"{_BOLD}{flat(title)}{_RST}  " if show_title else "")
            + tabs_s
            + " "
        )
        right_plain, right = _right(label, sep, project, tail)
        fill = width - cells(head_plain) - cells(right_plain)
        if fill >= 1:
            return f"{head}{_CYAN}{_RULE * fill}{_RST}{right}"
    # Not even the tabs and the scope fit: the tabs alone, cut.
    return fit_ansi(f"{_CYAN}{_RULE} {_RST}{tabs_s}", width)


def fit_ansi(line: str, width: int) -> str:
    """*line* (owned SGR only) cut to *width* cells with ``…``, its escapes kept (#25)."""

    import re  # noqa: PLC0415

    pieces = re.split(r"(\x1b\[[0-9;]*m)", line)
    if sum(cells(p) for p in pieces if not p.startswith("\x1b[")) <= width:
        return line
    out: list[str] = []
    used = 0
    for piece in pieces:
        if piece.startswith("\x1b["):
            out.append(piece)
            continue
        for cluster in graphemes(piece):
            w = cluster_cells(cluster)
            if used + w > width - 1:
                return "".join(out) + "…" + _RST
            out.append(cluster)
            used += w
    return "".join(out)


TitleRow = Callable[[SelectionModel, int], str]
"""Draws the title row at a width — the standalone ``── Title ── Save to …``, or a
host's own (#25 merges ``tabbed``'s tab row into it, layout A)."""


def render_panel(
    model: SelectionModel, layout: Layout, *, title_row: TitleRow | None = None
) -> list[str]:
    """Exactly ``layout.height`` ANSI rows, none wider than ``layout.width`` cells."""

    width = layout.width
    viewport = layout.viewport
    draw_title = title_row if title_row is not None else _title_row
    if layout.height < layout.fixed_rows + 1:
        # Not even one list row fits: say so rather than draw a panel with no list.
        small = [
            draw_title(model, width),
            f"{_YELLOW} {fit('The terminal is too small for this panel; Esc closes it.', width - 1)}{_RST}",
        ]
        return (small + [""] * layout.height)[: layout.height]
    rows = [draw_title(model, width), _search_row(model, width, viewport)]
    # Exactly the planned banner rows, wrapped (verify V10): the height is fixed
    # while the panel is open, whatever the banners say now.
    banners = _banner_lines(model, width)[: layout.banner_rows]
    banners += [("", _DIM)] * (layout.banner_rows - len(banners))
    for text, style in banners:
        rows.append(f"{style} {fit(text, width - 1)}{_RST}")
    cols = _columns(model, width)
    visible = model.visible()
    window = _window(model, viewport)
    body: list[str] = []
    for kind, value in window:
        if kind == "h":
            label = flat(value)
            word, _sep, rest = label.partition("  ")
            text = fit(f" {word}" + (f"  {rest}" if rest else ""), width)
            head = fit(f" {word}", width)
            body.append(f"{_BOLD}{head}{_RST}{_DIM}{text[len(head) :]}{_RST}")
        elif kind == "blank":
            body.append("")
        else:
            body.append(_item_row(model, visible[value], value == model.cursor, cols))
    if not visible:
        body.append(f"{_DIM}   (no matches){_RST}")
    body = body[:viewport]
    body.extend([""] * (viewport - len(body)))
    rows.extend(body)
    rows.append(f"{_CYAN}{_RULE * width}{_RST}")
    rows.extend(_detail_rows(model, layout))
    rows.append(_hint(model, width))
    # Fixed height: the panel never grows or shrinks while it is open.
    rows = rows[: layout.height]
    rows.extend([""] * (layout.height - len(rows)))
    return rows


class SelectionList(FormattedTextControl):
    """The model as a prompt-toolkit control, re-rendered at the live width each frame."""

    def __init__(
        self,
        model: SelectionModel,
        layout: Layout,
        width: Callable[[], int],
        key_bindings: KeyBindings,
        cap: Callable[[], int] | None = None,
        title_row: TitleRow | None = None,
    ) -> None:
        self.model = model
        self.layout_at_open = layout
        self._width = width
        self._cap = cap
        self._title_row = title_row
        super().__init__(self._render, focusable=True, key_bindings=key_bindings)

    def current_layout(self) -> Layout:
        """The width read now; the height fixed at open — unless the terminal SHRANK under it.

        Resize-down (C22): the modal is capped at the rows left above the input
        and footer, and a panel taller than that would lose its bottom — the
        hint and the state line — to the cap. So the height follows the cap
        down (never up), and the list window gives up the rows.
        """

        base = self.layout_at_open
        height = base.height
        if self._cap is not None:
            with contextlib.suppress(Exception):
                height = max(1, min(height, int(self._cap())))
        return Layout(max(20, self._width()), height, base.description_rows, base.banner_rows)

    def _render(self) -> ANSI:
        return ANSI(
            "\n".join(render_panel(self.model, self.current_layout(), title_row=self._title_row))
        )


# --- summary ----------------------------------------------------------------


def summary(
    title: str,
    save: SaveResult | None,
    apply: ApplyResult | None,
    *,
    effect_note: str,
    width: int = 80,
    always_note: bool = False,
    held: Sequence[tuple[str, bool, str]] = (),
) -> Text:
    """The transcript lines Enter leaves behind (ADR-0279 D11), wrapped to *width* with their indent.

    *always_note* (a kind whose effect is ``reload``, #25): Enter saved and
    applied nothing, so the guidance is said whenever something was saved.

    #704 — a save that was applied always says what it did for the model. The
    note when the session's list moved; else, for *held* (``(label, saved
    value, why)`` of a change the session still does not use, read after the
    apply), that it stays as it was and why; else that the session already had
    it. Before, a save the apply found nothing to do for left the file line
    alone, and "saved" read as "applied" or as nothing at all.
    """

    out = Text()

    def line(text: str, style: str = "", indent: int = 2, first: str | None = None) -> None:
        lead = first if first is not None else " " * indent
        rows = wrap(flat(text), max(10, width - cells(lead)), 1_000)
        for i, row in enumerate(rows or [""]):
            if out:
                out.append("\n")
            out.append((lead if i == 0 else " " * cells(lead)) + row, style=style)

    if save is not None and save.written:
        written = ", ".join(_home_short(path) for path in save.written)
        line(f"{title} saved to {written}", "green", first="✓ ")
    elif save is not None:
        line(f"{title}: the settings files already said this", "green", first="✓ ")
    else:
        line(f"{title}: nothing to save", "green", first="✓ ")
    changed = list(save.changed) if save is not None else []
    for label, value in changed:
        out.append(f"\n    {'on ' if value else 'off'}  {fit(flat(label), max(1, width - 9))}")
    for mismatch in save.mismatches if save is not None else ():
        line(mismatch, "yellow")
    if apply is not None:
        own = {label for label, _v in changed}
        pieces = [f"{n} on" for n in apply.turned_on if n not in own]
        pieces += [f"{n} off" for n in apply.turned_off if n not in own]
        if pieces:
            line(f"Also applied from settings: {', '.join(pieces)} (saved elsewhere).")
        if apply.deferred:
            line("A turn is running; this applies when it ends.", "yellow")
        if apply.warning:
            line(f"{apply.warning}.", "yellow")
        moved = apply.applied and bool(apply.turned_on or apply.turned_off)
        if effect_note and (apply.deferred or moved):
            line(effect_note, "dim")
        if apply.applied and not apply.deferred:
            for label, value, why in held:
                reason = f": {why}" if why else ""
                line(f"{label} stays {'off' if value else 'on'} in this session{reason}.", "yellow")
            kept = {h[0] for h in held}
            settled = [(label, value) for label, value in changed if label not in kept]
            if settled and not moved and not apply.warning:
                if len(settled) == 1:
                    label, value = settled[0]
                    what = f"{label} {'on' if value else 'off'}"
                else:
                    what = "these"
                line(f"Nothing changes for the model: this session already had {what}.", "dim")
    elif always_note and effect_note and save is not None and save.changed:
        line(effect_note, "dim")
    return out


def _home_short(path: str) -> str:
    """*path* with ``~`` for the home directory, as the settings files are named elsewhere."""

    home = os.path.expanduser("~")
    if not home or home == "~":
        return path
    # Resolved too (verify r1 R4): the project file's path is resolved, and a
    # HOME under a symlink (macOS /tmp is /private/tmp) did not match it.
    for base in dict.fromkeys((home, os.path.realpath(home))):
        if path == base or path.startswith(base + os.sep):
            return "~" + path[len(base) :]
    return path


def deferred_note(title: str, result: ApplyResult) -> Text:
    """The line a deferred apply leaves when the turn it waited for has ended."""

    # The warning quotes an exception (a path, a file's bytes): flattened (X7).
    if not result.applied:
        return Text(flat(f"⚠ {title}: {result.warning or 'not applied'}."), style="yellow")
    pieces = [f"{n} on" for n in result.turned_on] + [f"{n} off" for n in result.turned_off]
    text = f"✓ {title} applied after the turn" + (f": {', '.join(pieces)}" if pieces else "")
    if result.warning:
        text += f" ({result.warning})"
    return Text(flat(text), style="green")


PanelOutcome = Literal["handled", "save", "close", "ignored"]

_EDIT_KEYS = frozenset(
    {"up", "down", "pageup", "pagedown", "home", "end", "space", "left", "right", "backspace"}
)


class SelectionPanelBody:
    """The panel without its host (#510, ADR-0283 §6): keys, rendering, save and close.

    A host owns the screen: it maps a key to :meth:`handle`, runs :meth:`save`
    when that says ``save``, and closes with the lines :meth:`save` or
    :meth:`discard_lines` return. :func:`run_selection_panel` is the
    standalone host; #25 embeds a body in ``tabbed``'s Installed tab, whose
    title row (tabs and "Save to" in one row, layout A) it passes as
    *title_row*. The panel's height is the host's: fixed when it opens.
    """

    def __init__(
        self,
        selection: ResourceSelection,
        kind: ResourceKind,
        *,
        title: str,
        noun: tuple[str, str] = ("item", "items"),
        effect_note: str | Callable[[], str] = "",
        width: Callable[[], int] = lambda: 80,
        commit: Callable[[object], None] = lambda _line: None,
        title_row: TitleRow | None = None,
        invalidate: Callable[[], None] = lambda: None,
    ) -> None:
        self.selection = selection
        self._invalidate = invalidate
        self.kind = kind
        self.title = title
        self.effect_note = effect_note
        self._width = width
        self._commit = commit
        self.title_row = title_row
        self.model = SelectionModel(selection.snapshot(kind), title, noun)
        self.model.effect = str(getattr(kind, "effect", "next_request"))
        if not self.model.scope_writable("global")[0] and self.model.scope_writable("project")[0]:
            self.model.target = "project"
        self.layout: Layout | None = None
        """The layout fixed at open; :meth:`plan` sets it."""

    # --- state -------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        return self.model.dirty

    @property
    def busy(self) -> bool:
        return self.model.busy or self.model.closing

    def plan(self, width: int, rows_available: int) -> Layout:
        """Fix the panel's height for as long as it is open (ADR-0194, ADR-0279 D11)."""

        self.layout = plan_layout(self.model, width, rows_available)
        return self.layout

    def render(self, width: int, height: int) -> list[str]:
        base = self.layout or plan_layout(self.model, width, height)
        layout = Layout(max(20, width), height, base.description_rows, base.banner_rows)
        return render_panel(self.model, layout, title_row=self.title_row)

    # --- keys --------------------------------------------------------------

    def handle(self, key: str, data: str = "", *, too_small: bool = False) -> PanelOutcome:
        """One key. ``save``: the host runs :meth:`save`; ``close``: it closes with
        :meth:`discard_lines`. Synchronous, so a buffered key after Enter sees
        the claim (Codex X5)."""

        model = self.model
        if model.busy or model.closing:
            return "ignored"
        viewport = self.layout.viewport if self.layout is not None else 1
        if key in ("escape", "c-c"):
            if model.confirm_create:
                model.confirm_create = False
                model.message = None
                return "handled"
            model.closing = True
            return "close"
        if too_small:
            # No list row is on screen: nothing may change unseen (X8).
            return "ignored"
        if key in ("enter", "c-j"):
            # Claimed HERE, before the save task runs (Codex X5).
            model.busy = True
            return "save"
        actions: dict[str, Callable[[], object]] = {
            "up": lambda: model.move(-1),
            "down": lambda: model.move(1),
            "pageup": lambda: model.jump(-viewport),
            "pagedown": lambda: model.jump(viewport),
            "home": model.home,
            "end": model.end,
            "space": model.toggle,
            "left": lambda: model.set_target("global"),
            "right": lambda: model.set_target("project"),
            "backspace": model.backspace,
        }
        action = actions.get(key)
        if action is not None:
            action()
            return "handled"
        if len(data) == 1 and data.isprintable() and data != " ":
            model.type(data)
            return "handled"
        return "ignored"

    # --- lifecycle ---------------------------------------------------------

    def discard_lines(self) -> list[object]:
        count = len(self.model.edits)
        self.model.closing = True
        if count:
            return [
                Text(
                    f"{self.title}: {count} unsaved change{'s' if count != 1 else ''} discarded.",
                    style="dim",
                )
            ]
        return [Text(f"{self.title}: closed, nothing changed.", style="dim")]

    async def save(self) -> list[object] | None:
        """Save, apply (unless the effect is ``reload``) and return the summary.

        ``None`` keeps the panel open: the create-the-project-file question,
        or a save that failed — its message is on the state line and the edits
        are kept.
        """

        model = self.model
        selection, kind, title = self.selection, self.kind, self.title
        if model.closing:
            return None
        if (
            model.edits
            and not model.confirm_create
            and selection.needs_new_project_file(model.target, model.edits)
        ):
            model.busy = False
            model.confirm_create = True
            # C22: the file makes trust APPLY here; whether it asks depends on
            # a saved decision, so the line says "may", and the guide says why.
            # The consequence is split over the state line and the hint so
            # both fit 80 columns (the hint gives way first when narrower).
            model.message = (
                "warn",
                "This creates .aelix/settings.json: the folder falls under project trust.",
            )
            self._invalidate()
            return None
        model.message = ("info", "Saving…")
        self._invalidate()
        save: SaveResult | None = None
        if model.edits:
            try:
                save = await selection.save(kind, model.target, model.edits)
            except Exception as exc:  # noqa: BLE001 — never leave the panel stuck busy
                save = SaveResult(False, f"{exc}")
            if not save.ok:
                model.busy = False
                model.confirm_create = False
                model.message = ("error", f"✖ Not saved: {save.error}")
                self._invalidate()
                return None
        applied: ApplyResult | None = None
        reload_bound = model.effect == "reload"
        if not reload_bound:
            try:

                def _later(result: ApplyResult) -> None:
                    # V10: the files said what was applied when the turn ended —
                    # a bare "applied after the turn" would report nothing.
                    if result.applied and not (
                        result.turned_on or result.turned_off or result.warning
                    ):
                        return
                    self._commit(deferred_note(title, result))

                applied = await selection.apply(kind, on_deferred=_later)
            except Exception as exc:  # noqa: BLE001 — saved, not applied: say so
                applied = None
                self._commit(
                    Text(flat(f"⚠ {title}: saved, but not applied ({exc})."), style="yellow")
                )
        model.closing = True
        return [
            summary(
                title,
                save,
                applied,
                effect_note=self._effect_note(),
                width=self._width(),
                always_note=reload_bound,
                held=self._held(save, applied),
            )
        ]

    def _effect_note(self) -> str:
        """The note, read when the save is said: #704 — what the model gets can
        depend on the session as it is then (a skill list the prompt withholds)."""

        note = self.effect_note
        if not callable(note):
            return note
        try:
            return note()
        except Exception:  # noqa: BLE001 — the summary is said without it
            return ""

    def _held(
        self, save: SaveResult | None, applied: ApplyResult | None
    ) -> tuple[tuple[str, bool, str], ...]:
        """#704 — the saved changes this session still does not use, read after the apply.

        A fresh read: what the model gets now is the live state (``live``, a
        lock's value), not what the apply returned. Empty when nothing was
        applied here — deferred, refused or failed — or the read fails.
        """

        if save is None or not save.changed or applied is None:
            return ()
        if not applied.applied or applied.deferred:
            return ()
        try:
            states = self.selection.snapshot(self.kind).states
        except Exception:  # noqa: BLE001 — the summary is said without it
            return ()
        by_label: dict[str, ItemState] = {}
        for state in states:
            by_label.setdefault(state.item.label, state)
        out: list[tuple[str, bool, str]] = []
        for label, value in save.changed:
            state = by_label.get(label)
            if state is None:
                continue
            now = state.live if state.live is not None else state.in_force
            if now == value:
                continue
            if state.lock is not None:
                why = state.lock.reason
            elif state.live is False and state.applied:
                why = "it was turned off for this run"
            else:
                why = ""
            out.append((label, value, why))
        return tuple(out)


async def run_selection_panel(
    ctx: Any,
    selection: ResourceSelection,
    kind: ResourceKind,
    *,
    title: str,
    noun: tuple[str, str] = ("item", "items"),
    effect_note: str | Callable[[], str] = "",
    render_width_cap: Callable[[], int | None] = lambda: None,
) -> None:
    """Open the panel for *kind*; return when it has closed (ADR-0279 D11)."""

    from aelix_coding_agent.tui.overlay import _modal_cap, show_modal  # noqa: PLC0415

    chrome = ctx.chrome

    def _width() -> int:
        return terminal_columns(chrome, max_width=render_width_cap())

    def _rows() -> int:
        try:
            return int(chrome.app.output.get_size().rows)
        except Exception:  # noqa: BLE001 — no terminal (tests) → a 24-row screen
            return 24

    def _invalidate() -> None:
        with contextlib.suppress(Exception):
            chrome.invalidate()

    body = SelectionPanelBody(
        selection,
        kind,
        title=title,
        noun=noun,
        effect_note=effect_note,
        width=_width,
        commit=ctx.commit,
        invalidate=_invalidate,
    )
    model = body.model
    try:
        cap = _modal_cap(chrome, None)
    except Exception:  # noqa: BLE001
        cap = _rows()
    layout = body.plan(_width(), min(_rows() - 7, cap))
    tasks: set[asyncio.Task[Any]] = set()

    def build(result: asyncio.Future[Any]) -> Window:
        kb = KeyBindings()

        async def _close(lines: list[object]) -> None:
            def _unmount() -> None:
                # #701 (verify V3) — this panel leaves, by name: a turn's dialog
                # mounted over it while the save ran stays on screen and keeps
                # the keys, instead of being taken down with its answer pending.
                chrome.unmount_modal(window)
                if not chrome.is_modal_open():
                    with contextlib.suppress(Exception):
                        chrome.focus_input()
                if not result.done():
                    result.set_result(None)

            printer = getattr(chrome, "print_above_many", None)
            live = getattr(chrome, "stream_live", None)
            if printer is None or (callable(live) and live()):
                # #701 — opened while the model answers: printed straight above
                # the chrome, the summary would land above the answer still
                # being written; the output queue puts it under it.
                for line in lines:
                    ctx.commit(line)
                _unmount()
                return
            try:
                # ONE frame: the summary lands in scrollback and the panel leaves
                # the screen inside the same suspend (ADR-0194).
                await printer(lines, apply_before_redraw=_unmount)
            except Exception:  # noqa: BLE001 — never strand an open panel
                _unmount()

        def _spawn(coro: Any) -> None:
            task = asyncio.get_running_loop().create_task(coro)
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        async def _save_then_close() -> None:
            lines = await body.save()
            if lines is not None:
                await _close(lines)

        def _too_small() -> bool:
            current = control.current_layout()
            return current.height < current.fixed_rows + 1

        def _dispatch(key: str) -> Callable[[Any], None]:
            def _run(event: Any) -> None:
                data = getattr(event, "data", None) or ""
                outcome = body.handle(key, data, too_small=_too_small())
                if outcome == "save":
                    _spawn(_save_then_close())
                elif outcome == "close":
                    _spawn(_close(body.discard_lines()))
                if outcome != "ignored":
                    _invalidate()

            return _run

        for key in (*sorted(_EDIT_KEYS), "enter", "c-j", "escape", "c-c"):
            kb.add(key)(_dispatch(key))
        kb.add("<any>")(_dispatch("<any>"))

        def _live_cap() -> int:
            return min(_rows() - 7, _modal_cap(chrome, None))

        control = SelectionList(model, layout, _width, kb, cap=_live_cap)
        window = Window(
            control,
            height=lambda: Dimension.exact(control.current_layout().height),
            dont_extend_height=True,
        )
        return window

    await show_modal(chrome, build)
    for task in list(tasks):
        with contextlib.suppress(Exception):
            await task


__all__ = [
    "Layout",
    "PanelOutcome",
    "fit_ansi",
    "tab_title_row",
    "SelectionList",
    "SelectionModel",
    "SelectionPanelBody",
    "TitleRow",
    "deferred_note",
    "fit",
    "flat",
    "plan_layout",
    "render_panel",
    "run_selection_panel",
    "summary",
]
