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
from collections.abc import Callable, Collection
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl,
    UIContent,
    UIControl,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import PasswordProcessor, Processor
from prompt_toolkit.utils import get_cwidth

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
from aelix_coding_agent.extensions.widget_protocols import Component, Theme
from aelix_coding_agent.tui import themes as theme_registry
from aelix_coding_agent.tui.overlay import show_modal

if TYPE_CHECKING:
    from prompt_toolkit.formatted_text import StyleAndTextTuples

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

# GitHub #48 ask 1 — the panel RULE, which used to be :data:`_PICK_DIM`. SGR 2 is
# the faintest attribute a terminal has, and it was carrying the one element
# whose entire job is to say where the panel starts and stops; the owner's report
# was that an opened panel does not separate from the transcript around it.
#
# Cyan and not bold cyan: :data:`_PICK_SEL` is ``\x1b[1;36m`` and belongs to the
# HIGHLIGHTED ROW, which has to stay the single brightest thing on screen. A rule
# in the same style gives the eye two winners. Plain ANSI 36 is 4-bit, so it
# survives a terminal with no truecolor and follows the user's own palette.
#
# The glyph is a constant because the choice between ``─`` and a heavier ``━`` is
# taste, not correctness — the rest of this codebase (the banner box,
# ``model_detail_lines``) draws ``─``, so that is the default.
_PICK_RULE = "\x1b[36m"  # cyan — the top/bottom rule
_PICK_RULE_CHAR = "─"

#: C0, DEL and C1 minus the newline, mapped to a space, for the picker TITLE.
#: C1 is in the set because ``\x9b`` is a one-byte CSI; the newline is spared
#: because a multi-row title is a supported shape (spawn consent passes nine).
_TITLE_CONTROL_MAP = {
    codepoint: " "
    for codepoint in (*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0))
    if codepoint != 0x0A
}


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
    """Frame a picker body: title on a coloured rule, body, rule, dim hint.

    ``body`` rows are already styled by the caller (the current row colored, the
    counter/detail dimmed). ``content_width`` is the widest PLAIN content line so
    the rules span the panel. Returns :class:`ANSI` so the escapes render.

    GitHub #48 ask 1. ADR-0163 shipped this as a bold title on its own line above
    a DIM divider, and the title then reads as ordinary transcript text: the only
    thing marking the top of the panel was the faintest attribute the terminal
    has. A single-row title now rides the top rule — ``── Settings ─────`` — so
    the boundary and the label are one object rather than two weak ones.

    THAT ALSO REMOVES A ROW, WHICH IS THE SAFE DIRECTION. The modal does not
    scroll, it BOTTOM-TRUNCATES: ``build()`` returns one ``Window`` with
    ``wrap_lines`` False and no ``get_cursor_position``, so prompt-toolkit has
    nothing to scroll to, and ``_CappedContainer`` clamps the height
    (``tui/overlay.py:221-231``). ADR-0199 measured that at eight members the
    hint, the closing rule, the counter and the last option — which
    ``consent.build_options`` guarantees is ``Cancel`` — are simply not drawn.

    #399: this is the frame of aelix's OWN pickers (``select(..., own=True)``),
    ``tabbed`` and ``multiselect``, unchanged. An extension's or the model's
    ``select`` (spawn consent among them) no longer comes here: it wraps and,
    when it must, scrolls (:class:`_TitleHeldControl`, :func:`_picker_head`).

    A MULTI-ROW TITLE KEEPS THE OLD SHAPE, and that is load-bearing rather than
    tidy. The spawn-consent dialog passes a NINE-row title
    (``tests/agents_ext/test_spawn_consent.py:1351`` pins ``title.count("\\n")
    == 8``) and ``aelix_agents/consent.py`` writes its height budget down as
    ``title_rows + option_rows + 4``, gated by
    ``tests/agents_ext/test_batch_consent.py:344``. Nine rows cannot ride a rule,
    and quietly changing that arithmetic is how ``Cancel`` goes off screen.
    MEASURED against the shipped helper: single-row title 8 rows → 7, nine-row
    title 16 rows → 16, and the top rule is exactly as wide as the bottom one at
    every content width, Hangul titles included.
    """

    # THE TITLE IS SANITISED; the BODY is not, and the asymmetry is NARROWER than
    # it looks. Body rows do arrive already styled by this module — the cursor row
    # is ``_PICK_SEL``, the counter is dim — but ``select`` also interpolates the
    # caller's OPTION strings into them, so "the body is our own styling" is true
    # of the wrapper and not of what it wraps. That gap is pre-existing (it is
    # unchanged from ``main``) and is not closed here; it is named so the next
    # reader does not take the asymmetry for a proof of safety.
    #
    # WHAT IT ACTUALLY BUYS, measured rather than assumed — this comment has
    # carried a wrong version of it twice. The map deletes the CONTROL BYTES, so
    # no escape SEQUENCE is ever assembled from a caller's title; what remains is
    # the payload's printable tail, and it lands as ordinary text (``[31m``,
    # ``]0;PWNED``). Visible junk in a label is a caller getting what it asked
    # for. A live escape in a frame this module claims to own is not.
    #
    # WHAT IT DOES NOT BUY, and two earlier drafts said it did:
    #
    # * The window title. No OSC reaches a terminal from here with or without the
    #   map — driven through the real frame onto a pyte screen, ``screen.title``
    #   stays ``''``, against a control that reads ``'REAL'`` from a genuine OSC
    #   fed to the same emulator.
    # * The rules agreeing. They already agree: both are drawn from the one
    #   ``width`` number below, so they are equal in every escape class and in both
    #   title placements — measured +0 for plain, SGR, OSC, CSI-non-``m``, one-byte
    #   C1 and DCS. The "32 cells against 40" an earlier draft reported does not
    #   reproduce in either direction.
    #
    # The width IS mis-measured, and that is a separate, smaller thing:
    # ``_visible_len`` strips SGR only, so an escaped title under-counts and the
    # frame sizes itself from the wrong number. Both rules then share that wrong
    # number, which is why the frame stays square while the title can still
    # overflow its own line — the degradation the ``fits`` check below documents.
    #
    # Newlines survive: the spawn-consent dialog's title is nine rows.
    title = title.translate(_TITLE_CONTROL_MAP)
    width = max(_PICK_MIN_WIDTH, min(content_width, _PICK_MAX_WIDTH))
    rule = f"{_PICK_RULE}{_PICK_RULE_CHAR * width}{_PICK_RST}"
    # A title only rides the rule if it LEAVES one. ``width`` is clamped to
    # :data:`_PICK_MAX_WIDTH` while the title is not bounded at all —
    # ``ExtensionUIContext.select`` takes whatever an extension passes — so a
    # long enough label would make the TOP rule overrun the bottom one by its own
    # excess. On its own line it overflows exactly as it does today, which is a
    # clipped title; in the rule it would be a visibly broken frame.
    fits = title and "\n" not in title and _visible_len(title) + 6 <= width
    if fits:
        # ``── title ────``: two lead cells, a space either side of the label,
        # and the rest of the rule. Measured in CELLS — a Hangul title is twice
        # its length in columns, and a top rule that disagreed with the bottom
        # one by half its label would be worse than the dim rule it replaces.
        # ``max(0, …)`` is not catching a live case: the ``+ 6`` above already
        # guarantees at least two trailing cells. It is here so that a future
        # edit to that condition degrades to a short rule rather than to a rule
        # with no tail — ``"─" * -164`` is silently ``""``, not an error.
        tail = _PICK_RULE_CHAR * max(0, width - 4 - _visible_len(title))
        head = [
            f"{_PICK_RULE}{_PICK_RULE_CHAR * 2} {_PICK_RST}"
            f"{_PICK_BOLD}{title}{_PICK_RST}"
            f"{_PICK_RULE} {tail}{_PICK_RST}"
        ]
    else:
        head = [f"{_PICK_BOLD}{title}{_PICK_RST}", rule]
    lines = [
        *head,
        *body,
        rule,
        f"{_PICK_DIM}{hint}{_PICK_RST}",
    ]
    return ANSI("\n".join(lines))


def _picker_head(title_rows: list[str], content_width: int, screen_width: int) -> tuple[list[str], str]:
    """The rows above the body of an extension's or the model's ``select`` (#399), and its rule.

    :func:`_picker_frame`'s head for ``title_rows`` that :func:`_title_rows`
    already named and wrapped to ``screen_width``: one row when a single-row
    title rides the top rule (``── title ────``), else the title rows and the
    rule under them. The rule is no wider than the screen. A title rides the
    rule only when the whole rule row fits the screen, so a title drawn there
    is on screen whole. aelix's own pickers never come here: they keep
    :func:`_picker_frame` byte for byte.
    """

    from aelix_coding_agent.tui.width import cells_by_cluster  # noqa: PLC0415

    width = min(max(_PICK_MIN_WIDTH, min(content_width, _PICK_MAX_WIDTH)), max(1, screen_width))
    rule = f"{_PICK_RULE}{_PICK_RULE_CHAR * width}{_PICK_RST}"
    title = title_rows[0] if len(title_rows) == 1 else ""
    if title and cells_by_cluster(_ANSI_RE.sub("", title)) + 6 <= width:
        tail = _PICK_RULE_CHAR * max(0, width - 4 - _visible_len(title))
        return [
            f"{_PICK_RULE}{_PICK_RULE_CHAR * 2} {_PICK_RST}"
            f"{_PICK_BOLD}{title}{_PICK_RST}"
            f"{_PICK_RULE} {tail}{_PICK_RST}"
        ], rule
    return [*(f"{_PICK_BOLD}{row}{_PICK_RST}" for row in title_rows), rule], rule


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(text: str) -> int:
    """Display COLUMNS of ``text`` ignoring SGR (color/dim) escape sequences.

    ``_picker_frame`` sizes its dividers from the widest PLAIN content line, so a
    body line carrying its own ANSI (e.g. a dim footer note from the stats /
    extension tabs) must be measured by its VISIBLE length — counting the escape
    bytes would over-pad the frame.

    Width is measured in terminal cells, not codepoints: East-Asian wide
    characters occupy two columns each, so counting ``len()`` made every
    Hangul/CJK row under-span its frame by roughly half. ``get_cwidth`` is
    the same wcwidth-backed measure ``chrome.py`` already uses.
    """

    return get_cwidth(_ANSI_RE.sub("", text))


def _title_rows(title: str, width: int) -> list[str]:
    """An extension's or the model's *title* as rows no wider than *width* (#399).

    Two things happen to every line of it, and both are the approval prompt's
    own functions (``tui/approval_dialog.py``), reused rather than copied:

    * each character :func:`~aelix_ai.utils.terminal_text.safe_for_terminal`
      would remove is drawn as its NAME (``^[``, ``^M``, ``^I``, ``<U+202E>``)
      in reverse video (:func:`~aelix_coding_agent.tui.approval_dialog._shown`
      with ``one_row``, then ``_ansi_named``). :data:`_TITLE_CONTROL_MAP`
      blanked them, so a CR in a command read as a space and ``ESC [8m`` as
      ``[8m``: what was shown was not what was asked about. A newline still
      starts a row (the spawn-consent title is nine of them);
    * a line wider than *width* is cut into rows that fit, breaking after a
      space where it can and never inside a grapheme cluster narrower than
      the row (:func:`~aelix_coding_agent.tui.approval_dialog._cut`, whose
      known limit is a cluster wider than a whole row; each cluster
      measured with ``cluster_cells`` so no row is wider on screen than
      *width*, and a combining mark stays on its letter's row: review round 3). pi's ``ExtensionSelectorComponent`` draws the title as a
      wrapping ``Text`` (``extension-selector.ts`` line 48); aelix cut it at
      the screen's edge with no marker, so pi's ``permission-gate.ts`` example,
      ported, ran a command whose tail was never on screen.

    A line that fits and names nothing is returned as it was, so every title
    aelix's own pickers pass renders byte for byte as before. No character is
    dropped. The rows are ANSI text holding only SGR 7 and 27.
    """

    from aelix_coding_agent.tui.approval_dialog import _ansi_named, _cut, _shown  # noqa: PLC0415
    from aelix_coding_agent.tui.width import cells_by_cluster  # noqa: PLC0415

    limit = max(1, width)
    rows: list[str] = []
    for line in title.split("\n"):
        text, spans = _shown(line, one_row=True)
        if cells_by_cluster(text) <= limit:
            rows.append(_ansi_named((text, spans)))
            continue
        named = {i for a, b in spans for i in range(a, b)}
        chars: list[tuple[str, str]] = [
            ("name" if i in named else "", ch) for i, ch in enumerate(text)
        ]
        for row in _cut(cast("StyleAndTextTuples", chars), limit):
            row_text = "".join(fragment[1] for fragment in row)
            row_spans: list[tuple[int, int]] = []
            for i, fragment in enumerate(row):
                if fragment[0] != "name":
                    continue
                if row_spans and row_spans[-1][1] == i:
                    row_spans[-1] = (row_spans[-1][0], i + 1)
                else:
                    row_spans.append((i, i + 1))
            rows.append(_ansi_named((row_text, row_spans)))
    return rows


class _TitleHeldControl(UIControl):
    """An extension's or the model's ``select`` / ``confirm`` (#399): the dialog
    whose title the approving keys wait for.

    aelix's own pickers (``own=True``) never use this: they keep main's plain
    ``Window(FormattedTextControl(...))``, byte for byte, at every size.

    ``frame(width)`` returns the title's rows (named and wrapped by
    :func:`_title_rows`), then two lists of the rows that can go under it,
    each ordered from the fullest to the smallest: the rows used when the
    title is drawn whole, and the rows used when it scrolls. For ``select``
    both are its rule, option rows, counter, detail, rule and hint, and the
    smaller ones scroll the OPTION rows in fewer rows (the highlighted one
    always among them, with the counter while there is room for it).

    * A title drawn whole is never held. It is drawn whole whenever it fits
      the modal with at least the FIRST row of the highlighted option under
      it (the option area shrinks first: the hint, the closing rule, the
      detail, the rule under the title, then fewer option rows, the counter,
      and last the rows of a multi-line highlighted label past the first,
      cut from the bottom as main cuts them: review round 4).
    * Only a title taller than that scrolls, through the approval prompt's
      :class:`~aelix_coding_agent.tui.approval_dialog._BodyViewport` (same
      footer, PgUp/PgDn a page, Ctrl+Up/Down a row), above its footer and the
      fullest rows that still leave it four rows (else the smallest: the
      highlighted option alone, a multi-line label cut to the rows that
      leave the title four, and never to fewer than its first). Every
      approving key waits until every title row has been drawn by a real
      paint.
    * When not even one title row, the footer and the highlighted option's
      first row fit, the dialog says the terminal is too small, and only Esc
      and Ctrl+C (and ``n`` in ``confirm``, and a ``cancel_options`` row)
      answer. Each paint lays the dialog out afresh, so when the terminal
      grows the title is drawn whole (and answers) or scrolls (and answers
      once all of it is drawn). Review rounds 3 and 5 pin all three resets of
      ``_too_small``: drawn whole, scrolled, and over a tall option's first row.

    :meth:`holds` is what Enter, Space and Ctrl+J (``select``) and ``y``
    (``confirm``) ask before they answer; an Enter typed before the first
    paint is held too (nothing has been drawn), and dropped. ``select`` lets
    a caller name its own cancel row (``cancel_options``), which answers while
    the title is held.
    """

    def __init__(
        self,
        frame: Callable[
            [int],
            tuple[
                list[StyleAndTextTuples],
                list[list[StyleAndTextTuples]],
                list[list[StyleAndTextTuples]],
            ],
        ],
        key_bindings: KeyBindings,
        *,
        held: str,
    ) -> None:
        from aelix_coding_agent.tui.approval_dialog import _BodyViewport  # noqa: PLC0415

        self.key_bindings = key_bindings
        self._frame = frame
        self._held = held
        self._width: int | None = None
        self._too_small = False
        self._title: list[StyleAndTextTuples] = []
        self.title = _BodyViewport(lambda: self._title, lambda: self._width, held=held)

    def holds(self) -> bool:
        """Must an approving key wait? (See the class docstring.)"""

        return self._too_small or not self.title.all_shown()

    def is_focusable(self) -> bool:
        return True

    def get_key_bindings(self) -> KeyBindings:
        return self.key_bindings

    def preferred_height(
        self,
        width: int,
        max_available_height: int,
        wrap_lines: bool,
        get_line_prefix: Any,
    ) -> int | None:
        title, whole, _scrolled = self._frame(width)
        return len(title) + len(whole[0])

    def create_content(self, width: int, height: int) -> UIContent:
        title, whole, scrolled = self._frame(width)
        self._width = width
        self._title = title
        drawn = self._layout(title, whole, scrolled, width, height)
        return UIContent(get_line=lambda i: drawn[i], line_count=len(drawn))

    def _layout(
        self,
        title: list[StyleAndTextTuples],
        whole: list[list[StyleAndTextTuples]],
        scrolled: list[list[StyleAndTextTuples]],
        width: int,
        height: int,
    ) -> list[StyleAndTextTuples]:
        for under in whole:
            if len(title) + len(under) <= height:
                # The title fits with (at least) the highlighted option: drawn
                # whole, so seen, so never held.
                self._too_small = False
                self.title._draw(None)
                return [*title, *under]
        if whole[-1] and len(title) + 1 <= height:
            # Review round 4 (Codex round 3, cat 3): the smallest set is the
            # highlighted option alone, and a label of several lines is cut
            # from the bottom as main cuts it, so only its FIRST row must fit.
            # ``Deploy?`` over an eight-line option in an eight-row modal said
            # "too small" and held Enter, where main drew it and Enter selected.
            self._too_small = False
            self.title._draw(None)
            return [*title, *whole[-1]][:height]
        under = next(
            (rows for rows in scrolled if height - 1 - len(rows) >= _TITLE_PAGE_ROWS),
            None,
        )
        if under is None:
            # The same cut under a scrolling title: the highlighted option's
            # first rows, leaving the title a page where there is room for one.
            under = scrolled[-1][: max(1, height - 1 - _TITLE_PAGE_ROWS)]
        room = height - 1 - len(under)
        if room < 1:
            # Not even one title row, the footer and the highlighted option:
            # nothing is answerable but a refusal.
            self._too_small = True
            return [[("bold", _cut_cells(_TOO_SMALL.format(held=self._held), width))]]
        self._too_small = False
        out = list(self.title._draw(room))
        out.append([("bold", _cut_cells(self.title.footer_text(width), width))])
        out.extend(under)
        return out


#: The fewest title rows a scrolling title is given while there is a fuller set
#: of rows to cut under it: a page of PgDn is then at least three rows.
_TITLE_PAGE_ROWS = 4

_TOO_SMALL = "The terminal is too small for this question. {held} is held; Esc cancels."


def _cut_cells(text: str, width: int) -> str:
    """*text* cut to *width* cells, ending in ``…`` when cut."""

    if get_cwidth(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        w = get_cwidth(ch)
        if used + w > max(0, width - 1):
            break
        out += ch
        used += w
    return out + "…" if width >= 1 else ""


def _row(text: str) -> StyleAndTextTuples:
    """One title row of ANSI text as prompt-toolkit fragments.

    A row that ends in a zero-width character gets a space after it: review
    round 4 (verify round 3) measured prompt-toolkit writing such a mark into
    the NEXT cell as well as merging it into its base's, so a wrapped row
    ending in ``e`` + U+0301 drew the accent twice. The space takes that cell
    (``cells_by_cluster`` keeps it free, ``ends_row``).
    """

    row = to_formatted_text(ANSI(text))
    return [*row, ("", " ")] if row and row[-1][1] and not get_cwidth(row[-1][1][-1]) else row


def _rows(lines: list[str]) -> list[StyleAndTextTuples]:
    """The rows under a ``select`` title as screen rows, parsed as main parses them.

    :func:`_picker_frame` joins its rows with newlines into ONE ANSI text and a
    ``FormattedTextControl`` splits that into screen rows, so on main an option
    label with a newline in it is two rows and a style a label leaves open runs
    on into the rows under it. #399 review round 3 (Codex): parsed a row at a
    time, the newline reached the screen as a character and was drawn as
    ``^J``. Joined, parsed once and split the same way, the option rows are
    main's (#179 owns them).
    """

    from prompt_toolkit.formatted_text.utils import split_lines  # noqa: PLC0415

    if not lines:
        return []
    return list(split_lines(to_formatted_text(ANSI("\n".join(lines)))))


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
        thinking_provider: Callable[[], str | None] | None = None,
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
        # Live reasoning-effort provider for the 🧠 thinking-level footer segment
        # (default-ON since #248; /statusline can still uncheck it). Yields the
        # level as the shell composed it — plus the tier the model receives when
        # they differ (#251) — live, so it tracks /thinking, /settings and RPC.
        self._thinking_provider = thinking_provider
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
        self._usage_cost_known: bool = True
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
        # default-enabled set; a wired store reads the user's enabled-id set
        # (load() degrades to defaults).
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
        own: bool = False,
        cancel_options: Collection[str] = (),
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

        #399 — whose question this is decides how it is drawn:

        * ``own=True`` marks one of aelix's own pickers (``/model``,
          ``/settings``, ``/resume``, ``/trust``, the theme picker,
          ``/thinking``, ``/login``, ``/logout``, the session-in-use prompt,
          and, through ``runtime.ui.select`` and ``select_declared``,
          ``/extension new``'s placement question and ``/agents run``'s
          project-agent confirm).
          aelix wrote the question, and the picker is main's, byte for byte:
          the same frame (:func:`_picker_frame`), the same window, the same keys,
          a key typed before the first paint taken, at every terminal size.
        * The default is an extension's or the model's question: this is the
          method an extension's ``ctx.ui.select`` reaches (the bound UI is this
          object), and so do the permission gate's fallback and the
          spawn-consent dialog. Its title wraps like pi's and names its control
          characters (:func:`_title_rows`). A title that fits the modal with its
          highlighted option is drawn whole and never held; the option rows
          scroll in what room is left. Only a title taller than that scrolls,
          and then EVERY option waits until all of it has been drawn
          (:class:`_TitleHeldControl`), except a row named in
          ``cancel_options`` (the dialog's own Cancel, which aelix passes for
          spawn consent and the permission fallback's No rows). An Enter typed
          before the first paint is dropped. Esc and Ctrl+C always answer.

        ``own`` and ``cancel_options`` are deliberately NOT on the protocol.

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
                    max(_visible_len(title), 40),
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
                [_visible_len(title), _visible_len(counter), _visible_len(hint)]
                + [_visible_len(items[i][1]) + 2 for i in range(start, end)]
                + [_visible_len(d) for d in detail_lines]
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

        def option_rows(items: list[tuple[int, str]], idx: int, room: int | None) -> list[str]:
            # #399: the option rows in at most ``room`` rows (``None``: as many
            # as ``render`` shows), the highlighted one always among them, with
            # ⋮ markers while they fit. Each row is drawn exactly as ``render``
            # draws it (#179 owns option rows), so a label with a newline in it
            # takes as many screen rows as it has lines (review round 3).
            n = len(items)
            for shown in range(min(viewport, n), 0, -1):
                start = max(0, min(idx - shown // 2, n - shown))
                end = min(n, start + shown)
                above, below = start > 0, end < n
                lines = sum(1 + items[i][1].count("\n") for i in range(start, end))
                if room is not None and lines + above + below > room:
                    continue
                rows = [f"{_PICK_DIM}  ⋮{_PICK_RST}"] if above else []
                for i in range(start, end):
                    text = items[i][1]
                    rows.append(f"{_PICK_SEL}▸ {text}{_PICK_RST}" if i == idx else f"  {text}")
                if below:
                    rows.append(f"{_PICK_DIM}  ⋮{_PICK_RST}")
                return rows
            return [f"{_PICK_SEL}▸ {items[idx][1]}{_PICK_RST}"]

        def parts(screen_width: int) -> tuple[list[str], list[list[str]]]:
            # #399, an extension's or the model's question: the title rows, and
            # the rows that can go under them from the fullest to the smallest.
            title_rows = _title_rows(title, screen_width)
            items = filtered()
            if not items:
                head, rule = _picker_head(title_rows, max(_visible_len(title), 40), screen_width)
                body = [f"{_PICK_DIM}(no matches){_PICK_RST}", _filter_line(state["filter"])]
                hint = f"{_PICK_DIM}Backspace to clear · Esc to cancel{_PICK_RST}"
                lead = [rule] if len(head) > 1 else []
                return head[: len(head) - len(lead)], [
                    [*lead, *body, rule, hint],
                    [*lead, *body],
                    body,
                    body[:1],
                ]
            idx = max(0, min(state["idx"], len(items) - 1))
            state["idx"] = idx
            detail_lines: list[str] = []
            if detail is not None:
                with contextlib.suppress(Exception):
                    detail_lines = list(detail(items[idx][0]))
            counter = f"({idx + 1}/{len(items)})"
            if state["filter"]:
                counter += _filter_counter_suffix(state["filter"])
            hint = "↑/↓ move · type to filter · Enter select · Esc cancel"
            full = option_rows(items, idx, None)
            width = max(
                [_visible_len(title), _visible_len(counter), _visible_len(hint)]
                + [_visible_len(row) for row in full]
                + [_visible_len(d) for d in detail_lines]
            )
            head, rule = _picker_head(title_rows, width, screen_width)
            lead = [rule] if len(head) > 1 else []
            count = f"{_PICK_DIM}  {counter}{_PICK_RST}"
            details = [f"{_PICK_DIM}{d}{_PICK_RST}" for d in detail_lines]
            unders = [
                [*lead, *full, count, *details, rule, f"{_PICK_DIM}{hint}{_PICK_RST}"],
                [*lead, *full, count, *details, rule],
                [*lead, *full, count, *details],
                [*lead, *full, count],
                [*full, count],
            ]
            # Rooms in screen rows: a label with a newline is a row per line.
            full_rows = sum(1 + row.count("\n") for row in full)
            unders += [[*option_rows(items, idx, room), count] for room in range(full_rows - 1, 0, -1)]
            unders.append(option_rows(items, idx, 1))
            return head[: len(head) - len(lead)], unders

        # The rows of one paint, parsed once: prompt-toolkit asks for the height
        # and then for the content, and ``detail`` ran once a paint before, as
        # ``FormattedTextControl`` caches by ``render_counter``.
        drawn: dict[str, Any] = {"key": None, "frame": None}

        def frame(
            screen_width: int,
        ) -> tuple[list[StyleAndTextTuples], list[list[StyleAndTextTuples]], list[list[StyleAndTextTuples]]]:
            key = (get_app().render_counter, screen_width, state["idx"], state["filter"])
            if drawn["key"] != key:
                title_rows, unders = parts(screen_width)
                rows = [_rows(under) for under in unders]
                drawn["frame"] = ([_row(r) for r in title_rows], rows, rows)
                drawn["key"] = key
            return cast(
                "tuple[list[StyleAndTextTuples], list[list[StyleAndTextTuples]], list[list[StyleAndTextTuples]]]",
                drawn["frame"],
            )

        refusals = frozenset(cancel_options)

        def build(result: asyncio.Future[Any]) -> Window:
            kb = KeyBindings()
            control = None if own else _TitleHeldControl(frame, kb, held="Enter")

            def _confirm(_e: object) -> None:
                items = filtered()
                if not items:
                    return
                idx = max(0, min(state["idx"], len(items) - 1))
                if control is not None and control.holds() and items[idx][1] not in refusals:
                    # #399: no option is taken while part of an extension's or
                    # the model's question has not been on screen (pi's
                    # permission-gate example puts the whole command there).
                    # Every option but the dialog's own cancel row. Esc and
                    # Ctrl+C always answer.
                    self.chrome.invalidate()
                    return
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
            if control is not None:
                # #399: a title taller than its room scrolls a page (one
                # screenful less one row) or a row, as the approval prompt's
                # body does.
                title_view = control.title
                kb.add("pageup")(lambda _e: title_view.page(-1, self.chrome))
                kb.add("pagedown")(lambda _e: title_view.page(1, self.chrome))
                kb.add("c-up")(lambda _e: title_view.scroll(-1, self.chrome))
                kb.add("c-down")(lambda _e: title_view.scroll(1, self.chrome))

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

            if control is None:
                return Window(
                    FormattedTextControl(render, focusable=True, key_bindings=kb),
                    dont_extend_height=True,
                )
            return Window(control, dont_extend_height=True)

        return await show_modal(self.chrome, build)

    async def tabbed(
        self,
        title: str,
        tabs: list[tuple[str, Callable[[], list[str]]]],
        *,
        initial: int = 0,
        filter_tabs: set[int] | None = None,
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
            plain_header_len = _visible_len("  ".join(name for name, _ in tabs))
            width = max(
                [_visible_len(title), plain_header_len, _visible_len(hint)]
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

            return Window(
                FormattedTextControl(render, focusable=True, key_bindings=kb),
                dont_extend_height=True,
            )

        await show_modal(self.chrome, build)
        return None

    async def multiselect(
        self,
        title: str,
        options: list[tuple[str, str, str]],
        *,
        selected: set[str],
        extra_toggles: list[tuple[str, str]] | list[tuple[str, str, bool]] | None = None,
        preview: Callable[[set[str], dict[str, bool]], list[str]] | None = None,
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
                    max(_visible_len(title), 40),
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
                [_visible_len(title), _visible_len(counter), _visible_len(hint)]
                + [_visible_len(r) + 2 for r in plain_rows]
                + [_visible_len(p) for p in preview_lines]
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

            return Window(
                FormattedTextControl(render, focusable=True, key_bindings=kb),
                dont_extend_height=True,
            )

        return await show_modal(self.chrome, build)

    async def confirm(
        self,
        title: str,
        message: str,
        opts: ExtensionUIDialogOptions | None = None,
        *,
        own: bool = False,
    ) -> bool:
        """A yes/no question: ``y`` answers yes; ``n``, Esc and Ctrl+C answer no.

        #399, as :meth:`select`: ``own=True`` (aelix's own question, ``/login``
        and ``/logout``) is main's dialog byte for byte, a ``y`` typed before
        the first paint taken. Without it (an extension's ``ctx.ui.confirm``, a
        descriptor's confirm text) the title and the message wrap and name
        their control characters (:func:`_title_rows`); main drew them as one
        row per line cut at the screen's edge. When they are taller than the
        modal they scroll (PgUp/PgDn) and ``y`` waits until every row has been
        drawn (:class:`_TitleHeldControl`); a ``y`` typed before the first paint
        is dropped.
        """

        drawn: dict[str, Any] = {"key": None, "frame": None}
        answer: list[StyleAndTextTuples] = [[("bold", "[y/n]")]]

        def frame(
            screen_width: int,
        ) -> tuple[list[StyleAndTextTuples], list[list[StyleAndTextTuples]], list[list[StyleAndTextTuples]]]:
            key = (get_app().render_counter, screen_width)
            if drawn["key"] != key:
                rows = [_row(r) for r in _title_rows(f"{title}\n{message} [y/n]", screen_width)]
                # Drawn whole, the ``[y/n]`` is on its last row. Scrolled, it
                # gets a row of its own under the footer.
                drawn["frame"] = (rows, [[]], [answer])
                drawn["key"] = key
            return cast(
                "tuple[list[StyleAndTextTuples], list[list[StyleAndTextTuples]], list[list[StyleAndTextTuples]]]",
                drawn["frame"],
            )

        def build(result: asyncio.Future[Any]) -> Window:
            kb = KeyBindings()
            control = None if own else _TitleHeldControl(frame, kb, held="y")

            def _yes(_e: object) -> None:
                # #399: like ``select``, a yes is not taken while part of an
                # extension's question has not been on screen; n, Esc and
                # Ctrl+C always answer.
                if control is not None and control.holds():
                    self.chrome.invalidate()
                    return
                _resolve(result, True)

            for key in ("y", "Y"):
                kb.add(key)(_yes)
            if control is not None:
                title_view = control.title
                kb.add("pageup")(lambda _e: title_view.page(-1, self.chrome))
                kb.add("pagedown")(lambda _e: title_view.page(1, self.chrome))
                kb.add("c-up")(lambda _e: title_view.scroll(-1, self.chrome))
                kb.add("c-down")(lambda _e: title_view.scroll(1, self.chrome))
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
            if control is None:
                return Window(
                    FormattedTextControl(
                        f"{title}\n{message} [y/n]", focusable=True, key_bindings=kb
                    ),
                    dont_extend_height=True,
                )
            return Window(control, dont_extend_height=True)

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

    # WP-8 (Feature 5) — the grouped row layout: each row lists the segment ids
    # it carries, in render order. Empty rows (no enabled, non-empty segment) are
    # omitted. ``permission-mode`` stays the LEADING segment of its row
    # (ADR-0159). Extension statuses join the LAST row.
    #
    # TWO rows, not the three WP-8 shipped. ``current-dir`` had a row to itself
    # and ``permission-mode`` another, so the common case — one directory, the
    # default posture — spent two chrome rows on about thirty cells:
    #
    #     ✱ gpt-5.6-luna  ·  🧠 high  ·  ⎇ main  ·  ◔ 24% · 96.4K/400K
    #     📂 /workspaces/aelix-ai
    #     ● default
    #
    # The chrome is subtracted from the scrollback the user is actually reading,
    # so a row costs more than it looks. Merged with ``permission-mode`` FIRST:
    # ADR-0159 requires the badge to lead its row, and it is the security-visible
    # segment, so it keeps the position the eye lands on. The pairing is also the
    # honest one — both answer "where am I and what am I allowed to do", while
    # row 1 answers "what am I talking to".
    #
    # ``current-dir`` GOES LAST, and that ordering is the whole of a defect this
    # merge introduced and then had to fix. The chrome row is height-1 and is
    # CLIPPED at the terminal, which is the very thing multi-line mode exists to
    # stop — and ``current-dir`` is the one segment with no bound, so it decides
    # whether the row overflows.
    #
    # MEASURED through the real chrome onto a pyte screen at 80 columns, with
    # ``cwd=/workspaces/aelix-ai/packages/aelix-coding-agent/src/aelix_coding_agent``
    # and the four segments below enabled. The cwd is named because the number
    # depends on it, and an earlier draft of this comment reported 95 cells for an
    # unnamed one:
    #
    #     path in the middle  composed 106 cells → 79 on glass, LOST ``⏵⏵ all``
    #                         and ``⋯ 3 queued`` — the only LIVE and TRANSIENT
    #                         signals on the whole footer
    #     path last (this)    composed 106 cells → 79 on glass, LOST only the
    #                         path's tail
    #
    # With one extension status published the composed row is 129 cells in both
    # orderings, and the ordering decides whether the extension's own status
    # survives too: it does here, and does not with the path in front of it.
    #
    # So the overflow eats the path — the segment a user can already see in their
    # own shell prompt, and the one whose tail is the most guessable. That is a
    # TRADE for the row this merge saves, not a free win: on ``main``, where
    # ``current-dir`` had a row to itself, this path rendered whole.
    _MULTILINE_ROWS: tuple[tuple[str, ...], ...] = (
        ("model", "thinking-level", "git-branch", "context-remaining",
         "input-tokens", "output-tokens", "cost"),
        ("permission-mode", "steering", "pending-queued", "current-dir"),
    )

    _UNBOUNDED_TAIL_SEGMENTS = frozenset({"current-dir"})
    """Segments the ordering above puts last, and that anything merged into a row
    LATER has to be inserted in front of.

    The tuple order alone is not the rendered order: extension statuses are not
    registry segments and join the last row after it is composed, so appending
    them to the joined string put the path back in the middle and the clip ate the
    extension's own status instead. Naming the segment here is what lets
    ``_refresh_footer`` keep the promise the tuple makes."""

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
        # order is canonical — a persisted enabled-set gates MEMBERSHIP, never
        # order (#248). The ADR-0159 invariants (permission badge
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
            # omitted; extension statuses join the last rendered row.
            #
            # BEFORE THE UNBOUNDED SEGMENT, NOT AFTER IT. Joining the row first and
            # appending the extension tail to the string put ``current-dir`` back
            # in the MIDDLE, which is the one position ``_MULTILINE_ROWS`` orders
            # it out of: MEASURED at 80 columns with one extension status and a
            # realistically nested cwd, the merged row is 129 cells and what the
            # height-1 clip took was the extension's status, entirely. Composing
            # the row as cells and inserting at the path keeps the promise the
            # ordering makes — the path is the segment that overflows.
            composed: list[list[str]] = []
            composed_ids: list[list[str]] = []
            for row_ids in self._MULTILINE_ROWS:
                present = [sid for sid in row_ids if sid in values]
                if present:
                    composed.append([values[sid] for sid in present])
                    composed_ids.append(present)
            if ext_statuses:
                if composed:
                    cells, ids = composed[-1], composed_ids[-1]
                    at = next(
                        (
                            index
                            for index, sid in enumerate(ids)
                            if sid in self._UNBOUNDED_TAIL_SEGMENTS
                        ),
                        len(cells),
                    )
                    cells[at:at] = ext_statuses
                else:
                    composed.append(list(ext_statuses))
            self.chrome.set_footer_block(["  ·  ".join(cells) for cells in composed])
            return

        # Single-line (default): the canonical registry order joined by ``  ·  ``.
        parts = [values[segment.id] for segment in self._segments if segment.id in values]
        parts.extend(ext_statuses)
        self.chrome.set_footer_line("  ·  ".join(parts))

    def set_context_label(self, label: str | None) -> None:
        """Update the live context-window usage segment + repaint the footer.

        Called by ``run_tui`` with a formatted label (or ``None`` when usage is
        unavailable — e.g. model registry not wired). Six triggers, of which two
        can paint synchronously and the rest arrive off an async stats read: on
        ``message_end`` from the finished assistant message's own usage (#249 —
        the mid-turn SOURCE of the figure), on ``compaction_end``, on
        ``turn_end`` *when no such live figure is held*, on ``settled``, on
        ``model_select`` (synchronous too while a live figure is held — only the
        denominator moves, so there is nothing to read), and on a session
        rebind.

        An UNCHANGED label returns WITHOUT repainting. The triggers overlap by
        design and now three ways — ``turn_end`` covers the abort/error turn
        paths, ``settled`` the success path, and a ``model_select`` that does not
        change the context window repaints the same string — so the same value
        commonly arrives more than once for one turn, and this keeps that from
        costing extra invalidates on a paint path with a flicker-regression
        history.
        """
        if label == self._context_label:
            return
        self._context_label = label
        self._refresh_footer()

    def set_usage_stats(
        self,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        cost_known: bool = True,
    ) -> None:
        """Cache the session usage scalars for the optional token/cost footer
        segments + repaint the footer (WP-2, ADR-0160).

        Same cadence, and the same unchanged-value short-circuit, as
        :meth:`set_context_label`. The token/cost segments are default-OFF, so
        this is inert until the user enables them via ``/statusline``.
        """

        if (
            input_tokens == self._usage_input_tokens
            and output_tokens == self._usage_output_tokens
            and cost == self._usage_cost
            and cost_known == self._usage_cost_known
        ):
            return
        self._usage_input_tokens = input_tokens
        self._usage_output_tokens = output_tokens
        self._usage_cost = cost
        self._usage_cost_known = cost_known
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
