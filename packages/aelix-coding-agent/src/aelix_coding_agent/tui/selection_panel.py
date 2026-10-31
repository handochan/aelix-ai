"""The shared on/off panel for one resource kind (#511, ADR-0279 D11; layout A).

Three parts:

* :class:`SelectionModel` — pure state: rows, cursor, search, the "Save to"
  scope, unsaved edits. :meth:`SelectionModel.toggle` returns the refusal for
  a locked row instead of changing it.
* :func:`render_panel` / :class:`SelectionList` — the model drawn as rows no
  wider than the width read for THIS frame (``terminal_columns`` under the
  ADR-0219 ceiling, not ``_PICK_MAX_WIDTH``), and exactly ``height`` rows, so
  the panel does not grow or shrink while it is open (ADR-0194).
* :func:`run_selection_panel` — the modal host over ``show_modal``. Enter
  saves through :class:`ResourceSelection`, applies, and closes in ONE frame:
  the summary is printed and the panel unmounted inside the same
  ``print_above_many`` suspend. Esc discards and closes the same way.

``/skill`` uses it now; ``/tool`` (#510) and ``/extension``'s Installed tab
(#25) pass their own :class:`ResourceKind`.

Every string that comes from a file — a name, a description, a path — is
flattened before it is measured: control characters are shown by name
(``approval_dialog._shown``, #179), never written to the terminal.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from rich.text import Text

from aelix_coding_agent.core.resource_filter import Scope
from aelix_coding_agent.core.resource_selection import SaveResult
from aelix_coding_agent.tui.width import cluster_cells, graphemes, terminal_columns

if TYPE_CHECKING:
    from aelix_coding_agent.core.resource_selection import (
        ApplyResult,
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


def wrap(text: str, width: int, rows: int) -> list[str]:
    """*text* wrapped by word to *width* cells, at most *rows* rows, the last cut with ``…``."""

    if width <= 0 or rows <= 0:
        return []
    lines: list[str] = []
    line = ""
    for word in text.split():
        for piece in _chunks(word, width) if cells(word) > width else [word]:
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

        if state.lock is not None:
            word = "shadowed" if state.lock.reason.startswith("shadowed") else "locked"
            return word, _DIM
        if state.item.key in self.edits:
            return "* unsaved", _CYAN
        if state.pending:
            return f"{'on' if state.saved else 'off'} · pending", _YELLOW
        if state.decided_by == "project":
            return "project", _DIM
        if not state.saved:
            return "off", _DIM
        return "", ""

    def counts(self) -> tuple[int, int, int]:
        total = len(self.states)
        off = sum(1 for s in self.states if s.lock is None and not self.shown(s))
        locked = sum(1 for s in self.states if s.lock is not None)
        return total, off, locked

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
        if state.lock is not None:
            name = f"{state.item.label} is locked" if state.item.identities else "Locked"
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


def plan_layout(model: SelectionModel, width: int, rows_available: int) -> Layout:
    """The panel's size when it opens: ``min(rows available, content)`` (ADR-0279 D11)."""

    description_rows = 2 if width >= 70 else 3
    banner_rows = len(_banners(model))
    groups = {s.item.group for s in model.states}
    list_rows = len(model.states) + len(groups)
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
    model.scroll = scroll
    rows = list(display[scroll : scroll + viewport])
    if scroll > 0 and rows and rows[0][0] == "i":
        # The cursor's group header stays pinned above the window.
        index = rows[0][1]
        rows[0] = ("h", model.visible()[index].item.group_label)
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
    # Most important first; whole parts are dropped from the end, never cut.
    parts = [
        p
        for p in (
            position,
            f"{len(visible)} match{'' if len(visible) == 1 else 'es'}" if model.search else "",
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
    # Round brackets: this row cannot be toggled.
    box = ("(✓)" if on else "( )") if state.lock is not None else ("[✓]" if on else "[ ]")
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
        described = wrap(flat(item.description), width - 1, layout.description_rows)
        rows.extend(f" {line}" for line in described)
        rows.extend([""] * (layout.description_rows - len(described)))
    rows.append(_state_line(model, state, width))
    return rows


def _word(value: bool) -> str:
    return "on" if value else "off"


def _state_line(model: SelectionModel, state: ItemState | None, width: int) -> str:
    # Every piece here can carry a file's text — a lock reason names a path, a
    # save error quotes one — so the WHOLE line is flattened before it is
    # measured (Codex X7), as names and descriptions are.
    if model.message is not None:
        style, text = model.message
        colour = {"error": _RED, "warn": _YELLOW}.get(style, _DIM)
        return f"{colour} {fit(flat(text), width - 1)}{_RST}"
    if state is None:
        return ""
    if state.lock is not None:
        if not state.item.invocation and state.item.identities:
            # A shadowed copy: the saved value is its winner's, not its own (V7).
            text = f"Not loaded: {state.lock.reason}"
        else:
            text = f"Locked: {state.lock.reason}"
            if state.item.identities and state.saved != state.lock.value:
                text = f"Saved {_word(state.saved)} · {text}"
        return f"{_DIM} {fit(flat(text), width - 1)}{_RST}"
    decided = " (this project's file)" if state.decided_by == "project" else ""
    text = f"Saved {_word(state.saved)}{decided} · now {_word(state.applied)}"
    edit = model.edits.get(state.item.key)
    if edit is not None:
        text += f" · {_word(edit)} after Enter ({_SCOPE_WORD[model.target]})"
        if model.target == "global" and state.decided_by == "project":
            text += " — this project's file still decides here"
    elif state.pending:
        text += " · Enter applies the saved value"
    return f"{_DIM} {fit(flat(text), width - 1)}{_RST}"


def _hint(model: SelectionModel, width: int) -> str:
    if model.confirm_create:
        text = "Enter create and save · Esc back · next launch may ask; -p may need --approve"
    elif width < _NARROW:
        text = "↑↓ Space ←→scope Enter save Esc cancel"
    else:
        text = "↑↓ move · Space toggle · ←→ save to · Enter save · Esc cancel · type to search"
    return f"{_DIM} {fit(text, width - 1)}{_RST}"


def render_panel(model: SelectionModel, layout: Layout) -> list[str]:
    """Exactly ``layout.height`` ANSI rows, none wider than ``layout.width`` cells."""

    width = layout.width
    viewport = layout.viewport
    if layout.height < layout.fixed_rows + 1:
        # Not even one list row fits: say so rather than draw a panel with no list.
        small = [
            _title_row(model, width),
            f"{_YELLOW} {fit('The terminal is too small for this panel; Esc closes it.', width - 1)}{_RST}",
        ]
        return (small + [""] * layout.height)[: layout.height]
    rows = [_title_row(model, width), _search_row(model, width, viewport)]
    for text, style in _banners(model):
        rows.append(f"{style} {fit(flat(text), width - 1)}{_RST}")
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
    ) -> None:
        self.model = model
        self.layout_at_open = layout
        self._width = width
        self._cap = cap
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
        return ANSI("\n".join(render_panel(self.model, self.current_layout())))


# --- summary ----------------------------------------------------------------


def summary(
    title: str,
    save: SaveResult | None,
    apply: ApplyResult | None,
    *,
    effect_note: str,
    width: int = 80,
) -> Text:
    """The transcript lines Enter leaves behind (ADR-0279 D11), wrapped to *width* with their indent."""

    out = Text()

    def line(text: str, style: str = "", indent: int = 2, first: str | None = None) -> None:
        lead = first if first is not None else " " * indent
        rows = wrap(flat(text), max(10, width - cells(lead)), 1_000)
        for i, row in enumerate(rows or [""]):
            if out:
                out.append("\n")
            out.append((lead if i == 0 else " " * cells(lead)) + row, style=style)

    if save is not None and save.written:
        line(f"{title} saved to {', '.join(save.written)}", "green", first="✓ ")
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
        if effect_note and (
            apply.deferred or (apply.applied and (apply.turned_on or apply.turned_off))
        ):
            line(effect_note, "dim")
    return out


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


async def run_selection_panel(
    ctx: Any,
    selection: ResourceSelection,
    kind: ResourceKind,
    *,
    title: str,
    noun: tuple[str, str] = ("item", "items"),
    effect_note: str = "",
    render_width_cap: Callable[[], int | None] = lambda: None,
) -> None:
    """Open the panel for *kind*; return when it has closed (ADR-0279 D11)."""

    from aelix_coding_agent.tui.overlay import _modal_cap, show_modal  # noqa: PLC0415

    chrome = ctx.chrome
    snapshot = selection.snapshot(kind)
    model = SelectionModel(snapshot, title, noun)
    if not model.scope_writable("global")[0] and model.scope_writable("project")[0]:
        model.target = "project"

    def _width() -> int:
        return terminal_columns(chrome, max_width=render_width_cap())

    def _rows() -> int:
        try:
            return int(chrome.app.output.get_size().rows)
        except Exception:  # noqa: BLE001 — no terminal (tests) → a 24-row screen
            return 24

    try:
        cap = _modal_cap(chrome, None)
    except Exception:  # noqa: BLE001
        cap = _rows()
    layout = plan_layout(model, _width(), min(_rows() - 7, cap))
    tasks: set[asyncio.Task[Any]] = set()

    def _invalidate() -> None:
        with contextlib.suppress(Exception):
            chrome.invalidate()

    def build(result: asyncio.Future[Any]) -> Window:
        kb = KeyBindings()

        async def _close(lines: list[object]) -> None:
            def _unmount() -> None:
                chrome.unmount_modal()
                with contextlib.suppress(Exception):
                    chrome.focus_input()
                if not result.done():
                    result.set_result(None)

            printer = getattr(chrome, "print_above_many", None)
            if printer is None:
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

        async def _enter() -> None:
            # ``model.busy`` was claimed by the key handler, synchronously (X5).
            if model.closing:
                return
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
                _invalidate()
                return
            model.message = ("info", "Saving…")
            _invalidate()
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
                    _invalidate()
                    return
            applied: ApplyResult | None
            try:

                def _later(result: ApplyResult) -> None:
                    ctx.commit(deferred_note(title, result))

                applied = await selection.apply(kind, on_deferred=_later)
            except Exception as exc:  # noqa: BLE001 — saved, not applied: say so
                applied = None
                ctx.commit(
                    Text(flat(f"⚠ {title}: saved, but not applied ({exc})."), style="yellow")
                )
            model.closing = True
            await _close([summary(title, save, applied, effect_note=effect_note, width=_width())])

        def _too_small() -> bool:
            current = control.current_layout()
            return current.height < current.fixed_rows + 1

        def _guard(handler: Callable[[], object], *, edits: bool = True) -> Callable[[Any], None]:
            def _run(_event: Any) -> None:
                if model.busy or model.closing:
                    return
                if edits and _too_small():
                    # No list row is on screen: nothing may change unseen (X8).
                    return
                handler()
                _invalidate()

            return _run

        kb.add("up")(_guard(lambda: model.move(-1)))
        kb.add("down")(_guard(lambda: model.move(1)))
        kb.add("pageup")(_guard(lambda: model.jump(-layout.viewport)))
        kb.add("pagedown")(_guard(lambda: model.jump(layout.viewport)))
        kb.add("home")(_guard(model.home))
        kb.add("end")(_guard(model.end))
        kb.add("space")(_guard(lambda: model.toggle()))
        kb.add("left")(_guard(lambda: model.set_target("global")))
        kb.add("right")(_guard(lambda: model.set_target("project")))
        kb.add("backspace")(_guard(model.backspace))

        def _on_enter() -> None:
            # Claim the save HERE, before the task runs (Codex X5): a buffered
            # Esc or a second Enter that arrives before it starts is refused by
            # ``_guard`` instead of racing it.
            model.busy = True
            _spawn(_enter())

        kb.add("enter")(_guard(_on_enter))
        kb.add("c-j")(_guard(_on_enter))

        def _on_escape() -> None:
            if model.confirm_create:
                model.confirm_create = False
                model.message = None
                return
            model.closing = True
            count = len(model.edits)
            note = (
                Text(
                    f"{title}: {count} unsaved change{'s' if count != 1 else ''} discarded.",
                    style="dim",
                )
                if count
                else Text(f"{title}: closed, nothing changed.", style="dim")
            )
            _spawn(_close([note]))

        kb.add("escape")(_guard(_on_escape, edits=False))
        kb.add("c-c")(_guard(_on_escape, edits=False))

        @kb.add("<any>")
        def _char(event: Any) -> None:
            if model.busy or model.closing or _too_small():
                return  # too small: the search would change a list nobody sees
            data = getattr(event, "data", None) or ""
            if len(data) == 1 and data.isprintable() and data != " ":
                model.type(data)
                _invalidate()

        def _live_cap() -> int:
            return min(_rows() - 7, _modal_cap(chrome, None))

        control = SelectionList(model, layout, _width, kb, cap=_live_cap)
        return Window(
            control,
            height=lambda: Dimension.exact(control.current_layout().height),
            dont_extend_height=True,
        )

    await show_modal(chrome, build)
    for task in list(tasks):
        with contextlib.suppress(Exception):
            await task


__all__ = [
    "Layout",
    "SelectionList",
    "SelectionModel",
    "deferred_note",
    "fit",
    "flat",
    "plan_layout",
    "render_panel",
    "run_selection_panel",
    "summary",
]
