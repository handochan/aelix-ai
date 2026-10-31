"""The delegation panel, the ``on_partial`` card, and the throttle behind the
card (ADR-0278 for the panel; ADR-0199 §S10/§(l) for the card and the aggregate).

PURE. No ``asyncio``, no UI handle, no process and no clock: :class:`PartialThrottle`
takes its clock as an argument, and :func:`format_panel` takes the idle times the
bridge measured — so every layout below is pinnable against a hand-built list of
snapshots, and the throttle against a fake clock rather than a ``sleep``.

THE UNIT IS A LIST OF PER-CHILD SNAPSHOTS, INDEXED BY SUBMITTED POSITION.
``snapshots[k] is None`` means member ``k`` has published nothing yet — it is
parked on the batch semaphore (``batch.MAX_CONCURRENCY``), or it is a chain step
waiting for the one before it, and has no spawn id at all, because
``spawn_id = _new_id()`` is minted inside ``runtime._run``
(``runtime.py:1410``). That is precisely why the UI group is opened with a COUNT
(``progress.SubagentProgressBridge.begin_group(key, expected=…)``) rather than
with ids: without the count there is nothing to render the ``queued`` term from.

WHAT IS DRAWN, AND WHERE (#132 replaced S10's "three surfaces" account):

1. :func:`format_panel` — the ``set_widget`` panel above the input, for EVERY
   delegation the model's ``agent`` tool makes, one child included (ADR-0278
   D1): a five-row block for one child, a header and two rows per child for two
   to four, one row per child beyond that or on a short terminal (D4). Its
   height is fixed for the life of the call (D5), the bridge redraws it on a
   throttle and a 1 s tick (D6), and an Esc leaves its last frame on screen
   until the next prompt (D7).
2. :func:`format_card` — the ``ctx.on_partial`` card. It reaches the MODEL and
   the event stream, not the screen: ``tui/render.py`` no-ops
   ``tool_execution_update`` (ADR-0199 §(l)'s correction). Unchanged by #132,
   including its byte identity with P2 for a clean snapshot.
3. :func:`format_aggregate_status` — S10's one-line batch statusline row. Since
   ADR-0278 D2 the bridge writes no such row (the panel's header says the same
   things, and the baseline showed the row pushed off-screen by the disclosure
   row anyway); the function is public and kept as it was.
"""

from __future__ import annotations

import math
import re
import time
from bisect import bisect_right
from collections import Counter
from functools import lru_cache
from itertools import accumulate
from typing import TYPE_CHECKING, NamedTuple

from aelix_ai.utils.terminal_text import safe_for_terminal
from rich.cells import cell_len
from wcwidth import wcwidth as _wcwidth

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from aelix_coding_agent.subagent_contract import SubagentProgress

PARTIAL_MIN_INTERVAL_MS = 500
"""Minimum wall time between two ``ctx.on_partial`` emissions FOR ONE CALL.

The kernel appends an ``asyncio.Task`` per ``on_partial`` into a list it never
prunes (``harness/loop.py:581`` declares ``update_events``, ``:608`` appends,
and it is drained only when the turn settles), and ``loop.py`` is KERNEL — it
may not be edited — so throttling here is the ONLY legal mitigation for H10.

500 ms is 5 repaints at the TUI's ``refresh_interval=0.1`` (``chrome.py:841``),
so every emission is guaranteed visible while 5 Hz is already past the rate a
human reads a changing row. It bounds a 10-minute child at ~1 200 interval
flushes PLUS the forced flushes on every ``current_tool`` transition (two per
child tool call) — call it ~2 000 per child, so the worst legal fan-out
(8 children × 10 min) holds on the order of 16 000 completed kernel Tasks
instead of an unbounded number. Today the runtime publishes after EVERY reduced
stdout line (``runtime.py:1423-1424``), which for a chatty child is hundreds per
turn."""

PANEL_MIN_CHILDREN = 1
"""Below this, no widget panel at all — so only an empty group draws nothing.

It was 2 (ADR-0199 S10: "a single delegation keeps P2's behaviour exactly", and
P2 had no panel). ADR-0278 D1 (owner decision, 2026-10-28) gives one child the
same panel as many: measured on ``9d1517a8``, a single delegation's per-child
statusline row was never visible at 80 or 120 columns under a ``yolo`` parent,
because the disclosure row takes the line, so one child showed no progress at
all."""

PANEL_WIDGET_KEY = "aelix-agents:batch"
"""The ``set_widget`` slot the batch panel owns.

ONE key, not one per batch, and that is safe rather than lucky: ``agent``
declares ``execution_mode="sequential"`` (``tool.py:894``), which makes the
kernel run the whole tool batch sequentially (``loop.py:711-721``), so two
``agent`` calls never have panels open at the same time. ``progress.py`` still
tracks which group last wrote the slot, so an end_group for a group that does
not own it cannot blank someone else's panel."""

AGGREGATE_MAX_CHARS = 78
"""Hard ceiling on surface 1, in terminal CELLS.

The name says CHARS and the unit is cells; the name is kept because it is public
and the rename is not worth a breaking change, but the unit moved when the panel
stopped counting codepoints — a Hangul syllable is one character and two cells,
and it was the character count that let a row through at twice its promised
width.

78 and not 80: the aggregate is one segment among several on a shared height-1
row (``chrome.py:1216-1227`` joins them with two spaces), so the last two
columns are the join, not content. It is not lower than that because S10's own
worked example —
``agent scout ×4 · 2 running · 1 done · 1 queued · 33s · 12.3k tok · $0.0093``
— is 74 characters and must render in full; a ceiling that silently amputated
the spec's example would be a ceiling chosen against the design. The comparison
that matters is with what surface 1 replaces: four per-child rows
(``format_status_row``) joined into the same height-1 row are ~186 characters,
of which an 80-column terminal shows 1.7."""

_MAX_PROFILE_CHARS = 16
"""A profile name from aelix's own runtime is the profile's validated ``name:``
field (``agents/profile.py``: ``^[a-z0-9-]+$``, at most 64 characters, so at
most 64 cells); one handed over by another ``SubagentRuntime`` is not bounded at
all. Truncated to this for surface 1 ONLY. The tool card (surface 2) prints the name at the panel's own
per-field bound, :data:`PANEL_ROW_MAX_CHARS`, which is every real profile name in
full — the byte-identity it keeps with P2 is stated, and bounded, at
:func:`_child_line` (#178)."""

PANEL_ROW_MAX_CHARS = 78
"""The per-FIELD bound, in terminal CELLS, that the card (:func:`_child_line`)
and the per-child status row (``progress.format_status_row``) flatten every
child-authored term to (finding F2, HIGH; #178).

Same note as :data:`AGGREGATE_MAX_CHARS`: the NAME says chars, the unit is cells.

It used to be the panel's whole-row ceiling too — a 78-cell ribbon at every
terminal width. Since ADR-0278 D4 a panel row is bounded by the LIVE terminal
width the bridge passes (``tui.width.terminal_columns``, capped at the
transcript's own 120), which is the bound that matters: the widget window clips
whatever is wider than the terminal. The name is kept because it is public."""

PANEL_MAX_ROWS = 9
"""Hard ceiling on the panel's HEIGHT, in rows (finding F2, HIGH).

One header plus ``tool.MAX_PARALLEL_TASKS`` (= 8) one-row members, which is every
legal batch — the parser refuses a ninth task outright (``tool.py:676-682``), so
this never fires on input a model can actually get past the door. Two rows per
member (ADR-0278 D4) fit under it up to four members; past that the panel draws
one row each (:func:`panel_height`). Spelled here
rather than imported so this module keeps its "no aelix_agents imports" shape,
and checked anyway because the widget is the one surface with NO downstream
bound: ``chrome._render_widget_lines`` joins the list with ``\\n`` and ANSI-parses
it into a ``Window(…, dont_extend_height=True)`` (``chrome.py:1286``/``:800-802``),
which is multi-row and uncapped, so an over-long list pushes the transcript and
the input line off screen. Contrast ``chrome._render_status``, which strips
newlines because "this is a fixed height=1 chrome row" (``chrome.py:1216-1218``) —
the statusline was defended at the far end and the widget is not."""

_ELLIPSIS = "…"

_ZERO_WIDTH_SLACK = 4
"""Codepoints per cell :func:`_flatten` carries before it stops measuring and
slices. Only zero-width input can reach it — one visible codepoint is at least
one cell — so it bounds the pathological case without taking the ellipsis away
from the ordinary one."""

_MAX_INPUT_CHARS = 8192
"""Ceiling on what :func:`_flatten` will even LOOK at, before the two width caps.

The whitespace collapse and the control strip are both linear in the input, and
``_flatten`` runs on every publish for every member of a batch — the runtime
publishes after every reduced stdout line. A child that sends a megabyte in
``current_tool`` should not buy a megabyte of work per frame.

WHERE THE COST ACTUALLY IS, re-measured because an earlier draft of this
paragraph put it in the wrong step. At 200 000 characters the collapse is
0.2-1.9 ms across every input shape tried; the expensive steps are the two after
it, and only for non-ASCII, where ``translate`` takes the general per-character
path: 76 ms for ``translate`` and 144 ms for ``cell_len`` on 200 000 combining
acutes, against 0.3 ms and 3.8 ms for the same length in ASCII. So this bound is
worth having, and it is the flood of zero-width marks it is worth having against.

Generous rather than tight (a hundred times the widest row this module draws) so
it can only ever bite input that was already going to be truncated to nothing
visible. It is a work bound, not a width bound: the two width caps below are
what make the OUTPUT safe.

The ``translate`` figures above predate #178, which swapped the restated C0/DEL/C1
table for ``aelix_ai.utils.terminal_text.safe_for_terminal``. MEASURED on that
swap: 8 192 combining acutes cost 270 us through the old ``translate`` and 276 us
through the shared helper, and a short tool name 0.3 us against 2 us — the bound
is what keeps either one cheap."""


_SURROGATE = re.compile(r"[\ud800-\udfff]")
"""A lone UTF-16 surrogate. A Python ``str`` can hold one — JSON's
``"\\ud800"`` decodes to it — and no UTF-8 encoder will write it."""


def _ptk_cells(text: str) -> int:
    """``text``'s width as prompt_toolkit lays it out: ``get_cwidth`` summed per
    character, which is ``max(0, wcwidth(char))`` per character
    (``prompt_toolkit.utils._CharSizesCache``) — computed from ``wcwidth``
    itself, the function prompt_toolkit calls, so this pure module does not
    import a UI toolkit; a test pins that the two agree.

    A long string is summed per DISTINCT character (#132 review round 2):
    the sum is the same, and counting is done in C, so a 2 048-character
    field of a few repeated emoji costs a few ``wcwidth`` calls, not 2 048."""

    if len(text) <= _COUNT_FROM:
        return sum(max(0, _wcwidth(char)) for char in text)
    return sum(count * max(0, _wcwidth(char)) for char, count in Counter(text).items())


_COUNT_FROM = 64
"""From this length :func:`_ptk_cells` counts distinct characters first."""


@lru_cache(maxsize=4096)
def _char_cells(char: str) -> int:
    """One character's width by :func:`_cells` — rich's ``cell_len`` and
    prompt_toolkit's ``get_cwidth``, the larger — cached, because the cut
    below reads it for every character it keeps (#132 review round 2). A test
    pins that it equals :func:`_cells` of the one-character string."""

    return max(cell_len(char), max(0, _wcwidth(char)))


def _cells(text: str) -> int:
    """The width a panel row is bounded by: the LARGER of rich's measure and
    prompt_toolkit's (#132 review round 1).

    The panel was measured with ``rich.cells.cell_len`` alone, and the widget is
    drawn by prompt_toolkit (``chrome._render_widget_lines`` → ``ANSI`` → a
    ``Window``), which places each character at the column ``get_cwidth``
    summed to and drops whatever lands past the window's width. The two
    disagree on emoji: MEASURED at 80 columns, a reply of 60 regional
    indicators was 66 cells to rich and 126 to prompt_toolkit, and the real
    chrome cut the row with no ``…`` — the reply's tail and closing quote
    simply gone; skin-tone modifiers and ZWJ families do the same. The other
    way round, ``❤️`` (U+2764 U+FE0F) is one cell to prompt_toolkit and two to
    rich and to most terminals. A row no wider than the width by BOTH is never
    clipped by the window and never wider than a grapheme-aware terminal draws
    it. Printable ASCII is one cell to both, so it costs nothing there.
    """

    width = cell_len(text)
    if text.isascii():
        return width
    return max(width, _ptk_cells(text))


def _flatten(value: str, *, limit: int) -> str:
    """One row of untrusted text: no newlines, no escapes, bounded width. (F2)

    EVERY string this module renders into the widget is child-authored.
    ``SubagentProgress.current_tool`` is set from the CHILD process's own stdout
    JSON — any non-empty ``str`` in ``tool_execution_start.tool_name``
    (``stream.py:1175-1177``) — and the kernel emits that event with the raw
    model-supplied name BEFORE ``_prepare_tool_call`` looks the tool up
    (``loop.py:739-749``), so it is not constrained to a real tool name. A child
    is exactly the process this phase's threat model assumes has read attacker
    controlled content (``consent._may_widen`` constraint 6). Demonstrated: 40
    newlines in ``current_tool`` turned a 3-entry panel into 43 screen rows with
    raw ESC intact.

    ``profile`` gets the same treatment although aelix's own runtime only ever
    publishes a profile's validated ``name:`` field (``agents/profile.py``:
    ``^[a-z0-9-]+$``, at most 64 characters): ``SubagentProgress`` is a public
    contract, and a profile handed over by another ``SubagentRuntime`` is as
    untrusted as a tool name.

    The order follows ``consent._sanitize_field`` with one step added: collapse
    whitespace (which is what bounds the ROW COUNT), then delete controls (which
    is what bounds what the terminal will obey), then collapse once more —
    deleting a control that sat beside a space, or at either end, leaves a
    double, leading or trailing space (#178 review round 3) — then bound the
    width, measured on the flattened string, so the limit counts what is drawn.

    THE CONTROL STRIP IS ``safe_for_terminal``'s, NOT A RESTATED TABLE (#178).
    This module used to carry its own C0/DEL/C1 ``translate`` table, a copy of
    ``consent._CONTROL_CHARS``. A copy is a second answer to "what may reach a
    terminal", and the two had already diverged: the shared helper also deletes
    the BiDi overrides and isolates (U+202A-U+202E, U+2066-U+2069), ZWSP and a
    stray BOM, and this table did not — MEASURED, U+202E survived into a panel
    row on ``8f7d98aa``. It deletes rather than spaces, like the table it
    replaced, so ``\\x1b[31m`` still collapses to the inert literal ``[31m``;
    the whitespace collapse before it has already turned every newline, CR, tab,
    NEL and U+2028/U+2029 into one space. ZWJ is deliberately kept by the helper
    (it composes ``👩‍💻``), which is why :func:`_cut_to_cells` must still be exact
    over it.

    EVERY CHILD-AUTHORED TERM EVERY ``aelix_agents`` SURFACE PRINTS GOES THROUGH
    HERE — the widget rows and header, the statusline aggregate, the per-child
    status row (``progress.format_status_row``), the tool card
    (:func:`format_card`) and the result footer (``tool._usage_field``). Until
    #178 the per-child status row and the tool card interpolated ``profile``,
    ``state`` and ``current_tool`` raw; the status row is ANSI-parsed by the
    chrome, so a child's ``\\x01…\\x02`` reached the terminal as a
    ``ZeroWidthEscape`` — an OSC 52 clipboard write, measured.

    ``limit`` IS IN CELLS, AND THAT IS THE WHOLE OF THIS FUNCTION'S SECOND
    FINDING. It counted codepoints, which is not what a terminal draws: a Hangul
    syllable or an emoji occupies two columns and one character, so the two
    diverge by a factor of two on exactly the input a hostile child would pick.
    MEASURED before the fix, with ``current_tool`` set to ``"한" * 60`` on eight
    members: every row came out at 78 characters — inside the ceiling, so nothing
    was dropped — and 125 CELLS, wrapping at 80 columns into 17 screen rows
    against a :data:`PANEL_MAX_ROWS` of 9. That ceiling is not layout: the
    docstring beside it explains it exists because the widget is the one surface
    with no downstream bound, so an over-long list pushes the transcript and the
    input line off screen. A 2x breach of it is reachable from child stdout.

    ``rich.cells`` rather than a private measure: ``tui/render.py`` draws with
    ``cell_len``/``set_cell_size`` and ``tool.py`` — which imports THIS
    function — already fixed its own half of the same finding (M1) against those
    primitives. Sharing them is what makes the budget and the draw agree.

    BOTH UNITS, BECAUSE NEITHER IS A BOUND ON ITS OWN. Counting codepoints let a
    Hangul row through at twice its promised width, which is the finding above.
    Counting CELLS alone is worse: ``cell_len`` returns 0 for a combining mark,
    so a string of them has no width at all and a cell cap never fires. MEASURED
    on the cells-only version: 200 000 combining acutes went in and 200 000 came
    out, cell width 0, against a limit of 78 — a hard character cap replaced by
    no cap. That row reaches ``chrome._render_widget_lines``, which has no bound
    of its own, and the repaint it causes is measured in tens of seconds.

    So the codepoint cap comes FIRST and the cell cap narrows what survives it.
    The input is bounded before any of that work: the whitespace collapse and the
    control strip are linear in the input, and this runs on every publish for every
    member of a batch.

    A LONE SURROGATE BECOMES U+FFFD (#132 review round 1). It is not a steering
    character, so the strip kept it, and it cannot be encoded: MEASURED with the
    real ``RpcCommandUI``, a call argument ``"X\\ud800Y"`` made every later
    ``setWidget`` fail to encode, the write was suppressed, and the RPC client
    kept a frame that said ``running`` after the Esc. A terminal's output
    replaces it with ``?``; the replacement here is the one every surface gets.
    """

    # STRIP BEFORE SLICING. The slice is a work bound, not a width bound, and
    # running it before the whitespace collapse let leading blanks consume the
    # whole window: MEASURED, 9 000 spaces followed by ``read_file`` rendered as
    # the empty string — the field deleted rather than bounded. ``strip`` scans
    # from the ends only, so it is cheaper than the ``split`` that follows it and
    # cheaper than what this function did before the bound existed.
    value = value.strip()
    if len(value) > _MAX_INPUT_CHARS:
        value = value[:_MAX_INPUT_CHARS]
    # COLLAPSE, DELETE, COLLAPSE AGAIN. The first collapse turns every newline,
    # CR, tab and Unicode space into one U+0020 before the strip could delete a
    # newline outright and glue two words together. The second is there because
    # the strip runs after the first: MEASURED on ``bf37350f``, ``"a \x1b b"``
    # gave ``"a  b"``, ``"\x1b read"`` gave ``" read"`` and ``"\x01 \x02"`` gave
    # ``" "`` — a tool term made of controls and one space survived as a blank.
    # A clean field holds no steering character and no whitespace but single
    # inner spaces, so neither collapse changes it (#178 review round 3).
    flat = " ".join(safe_for_terminal(" ".join(value.split())).split())
    if not flat.isascii():
        flat = _SURROGATE.sub("\ufffd", flat)
    # The zero-width backstop, SLACK rather than equal to ``limit``: a slice at
    # exactly the budget would leave the cell cap below with nothing to do and
    # would return an over-long row unmarked. Only zero-width input can reach it,
    # since one visible codepoint is at least one cell. Slicing by codepoint never
    # splits a codepoint; it can orphan a combining mark from its base, which is
    # cosmetic, and this arm is only reached by input that was never legitimate.
    clipped = len(flat) > limit * _ZERO_WIDTH_SLACK
    if clipped:
        flat = flat[: limit * _ZERO_WIDTH_SLACK]
    # THE ELLIPSIS IS INSIDE ``limit`` ON BOTH ARMS. Returning ``flat + _ELLIPSIS``
    # against ``limit`` rather than one less was a cell over whenever the codepoint
    # slice landed exactly on the budget — reachable at four codepoints per cell,
    # a base character carrying three combining marks. A row one cell past
    # :data:`PANEL_ROW_MAX_CHARS` is the overhang a closing clamp exists to catch
    # (``_Line.render`` in the panel), and this is the half that promises it.
    if cell_len(flat) <= limit - (cell_len(_ELLIPSIS) if clipped else 0):
        return flat + _ELLIPSIS if clipped else flat
    return _cut_to_cells(flat, max(0, limit - 1)) + _ELLIPSIS

def _cut_to_cells(text: str, cells: int) -> str:
    """Truncate to at most ``cells`` columns, EXACTLY, by accumulation.

    ``rich.cells.set_cell_size`` is not exact for a string that begins with
    U+200D ZERO WIDTH JOINER. MEASURED: ``set_cell_size("\\u200d" * 2 + "a" * 79,
    39)`` returns a string measuring 40 cells, not 39 — so ``_flatten`` handed
    back ``limit + 1`` for that input while believing the library had bounded it.
    An odd number of leading joiners undershoots by one; an even number of two or
    more overshoots by one.

    ZWJ SPECIFICALLY, not zero-width generally, and the distinction is worth the
    word: swept over the same shapes, U+200B, U+0301, U+FE0F, U+200E and U+00AD
    are all exact. ZWJ is the one that makes rich's grapheme spans disagree with
    its own measure of the whole. A reader hardening "zero-width" input would be
    guarding five characters that were never broken, and might well conclude the
    guard was unnecessary when three of them tested clean.
    Nothing else in this module noticed, because the panel's closing clamp
    (``_Line.render``) calls the same function.

    THE SHAPE THAT BROKE IT IS NOT THE SHAPE THE FIRST SWEEP TESTED. That sweep
    used a base character carrying three combining marks — zero-width AFTER a
    visible glyph — and found no violation. The failing input is zero-width
    BEFORE any glyph, which is the same class and a different position. Counting
    the result here rather than trusting a helper is what makes the bound hold for
    both, and for whatever the next shape turns out to be.

    AND ``cell_len`` IS NOT ADDITIVE, which the accumulate loop alone gets wrong in
    the other direction. It measures grapheme clusters, so the sum over characters
    and the measure of the whole disagree — MEASURED: ``❤️`` (U+2764 U+FE0F) is 1
    by the sum and 2 as a string, and ``👩‍💻`` is 4 by the sum and 2 as a string.
    Accumulating with the per-character number therefore UNDER-counts a
    variation-selector sequence, and a first version of this function returned 9
    cells against a budget of 5 for a row of hearts. So the loop places the cut and
    the whole-string measure then confirms it, shrinking while it must. The shrink
    is bounded by the budget — the prefix summed to at most ``cells``, so at most
    ``cells`` characters were under-counted — and it terminates because the empty
    string measures zero.

    ``cli/session_labels.truncate_cells`` accumulates without this second step and
    is nonetheless correct, because it measures with ``prompt_toolkit``'s
    ``get_cwidth``, which IS additive over these sequences (measured: per-character
    sum equals the whole for every shape above). Two renderers, two width
    libraries; the loop that is right for one is not right for the other.

    THE MEASURE IS :func:`_cells`, BOTH LIBRARIES' LARGER (#132 review round 1),
    because the panel is laid out by one and drawn by the other. Each
    character is accumulated at its larger width and the whole is confirmed
    the same way, so the result is no wider than ``cells`` by either. A caller
    that bounds with rich alone (the statusline row) only ever gets a shorter
    cut than before, never a wider one.

    THE SHRINK BISECTS (#132 review round 2). It used to drop one character
    and measure the whole prefix again, which is quadratic exactly when the
    per-character sum under-counts by a lot: MEASURED, eight members with
    64 KiB tasks of ``❤️`` took 430 ms a frame, and the 1 s tick drew one
    every second. :func:`_fitting_prefix` finds the same cut in a logarithmic
    number of measures; ordinary and CJK text never reach it, because their
    per-character sum is the whole string's width. The accumulation itself is
    the same sum with the first overflow found by ``bisect`` over running
    totals (widths are never negative, so the totals never fall).
    """

    if cells <= 0:
        return ""
    if _cells(text) <= cells:
        return text
    end = bisect_right(list(accumulate(map(_char_cells, text))), cells)
    return text[: _fitting_prefix(text, end, cells)]


def _fitting_prefix(text: str, end: int, cells: int) -> int:
    """The longest ``k <= end`` whose ``text[:k]`` is at most ``cells`` wide
    by :func:`_cells`, by bisection (#132 review round 2).

    ``end`` is where the per-character sum placed the cut, so ``text[:end]``
    usually fits and costs one measure. When a grapheme cluster made the whole
    wider than the sum, the prefix is bisected: the empty prefix fits, and
    each measure halves the range. A longer prefix is never narrower — rich's
    measure only adds as it reads (a ZWJ skips the next character, a VS16
    widens the one before) and prompt_toolkit's is a sum — so this is the cut
    the one-character-at-a-time loop it replaced found: MEASURED, identical on
    320 000 random cuts of ASCII, CJK, accented and emoji text (ZWJ, VS16,
    VS15, regional indicators, skin tones, combining marks). Either way the
    result is checked against ``cells``, not assumed to fit.
    """

    if _cells(text[:end]) <= cells:
        return end
    fits, over = 0, end
    while over - fits > 1:
        mid = (fits + over) // 2
        if _cells(text[:mid]) <= cells:
            fits = mid
        else:
            over = mid
    return fits


_STATE_LABELS: dict[str, str] = {
    "starting": "running",
    "running": "running",
    "done": "done",
    "error": "failed",
    "stopped": "stopped",
}
"""``SubagentState`` (``subagent_contract.py:66``) → the aggregate's vocabulary.

``starting`` folds into ``running`` because the distinction is sub-second and
the row has no width to spend on it. ``error`` renders as ``failed`` to match
the batch header ``aggregate.render_batch_result`` writes (§3.4: "3 ok · 1
failed") — one batch must not be described in two vocabularies. An unknown
state (a product-core literal added later) also reads ``running``: whatever it
is, it is not one of the three terminal ones."""

_COUNT_ORDER = ("running", "done", "failed", "stopped", "queued")
"""Fixed render order, so the row does not reshuffle under the user's eye as
members finish."""


def _format_tokens(tokens: int) -> str:
    """Compact token count for the aggregate row (the panel has its own,
    :func:`_compact_tokens`, whose cells are fewer).

    Mirrors ``progress._format_tokens`` (``progress.py:242-245``) rather than
    importing it — the same call ``aggregate._format_count`` makes
    (``aggregate.py:207-217``) and for the same reason: these are three
    renderers with three different unit conventions, and a shared helper would
    only move the divergence. The threshold and the single decimal place are
    kept identical so a per-child row and an aggregate row never disagree about
    how 1 500 is spelled.
    """

    if tokens < 1000:
        return f"{tokens} tok"
    return f"{tokens / 1000:.1f}k tok"


def _short_profile(name: str) -> str:
    """Bound the profile name for surface 1 — and flatten it (F2).

    :func:`format_aggregate_status` was ALSO the panel's header line until
    ADR-0278, and the panel is not newline-stripped downstream the way the
    statusline is (``chrome.py:1216-1218``). A profile name of 16 characters
    containing newlines would pass the length cap and still add rows to the
    widget, so the same helper that bounds every panel row bounds this too.
    """

    return _flatten(name, limit=_MAX_PROFILE_CHARS)


def _state_counts(snapshots: Sequence[SubagentProgress | None]) -> list[str]:
    """``["2 running", "1 done", "1 queued"]`` — the non-zero terms only."""

    # ``dict.fromkeys`` over a literal tuple infers Literal keys, but the
    # lookup below is ``_STATE_LABELS.get(state, "running")`` — a plain str.
    # The values ARE always one of _COUNT_ORDER; the annotation just says so.
    counts: dict[str, int] = dict.fromkeys(_COUNT_ORDER, 0)
    for snapshot in snapshots:
        if snapshot is None:
            counts["queued"] += 1
            continue
        counts[_STATE_LABELS.get(snapshot.state, "running")] += 1
    return [f"{count} {label}" for label, count in counts.items() if count]


def _head_and_counts(
    snapshots: Sequence[SubagentProgress | None],
) -> tuple[str, list[str]]:
    """``("agent scout ×4", ["2 running", "1 done", "1 queued"])``.

    ``("", [])`` when there is nothing to say — no members, or not one of them has
    published, so the profile name is not knowable.

    Split out so :func:`format_aggregate_status` can hand the same two strings to
    :func:`_model_term`, which sizes the model against them.
    """

    total = len(snapshots)
    live = [snapshot for snapshot in snapshots if snapshot is not None]
    if total == 0 or not live:
        return "", []
    return f"agent {_short_profile(live[0].profile)} ×{total}", _state_counts(snapshots)


_MODEL_MIN_CELLS = 12
"""Narrowest CUT the batch model is worth showing in the aggregate head.

IT GOVERNS TRUNCATION, NOT PRESENCE, and the first draft of this line said "below
this the model is not named at all" — which is false for every id short enough to
fit whole. ``gpt-5`` is five cells and is named in five;
:func:`_model_term` reaches this floor only once it has to CUT.

12 is the width at which a truncated string still identifies what it came from
(the panel's :data:`_NUMBERS_FLOOR` is the same number for the same reason).
MEASURED, ``_flatten`` at limit 12 gives ``github-copi…`` and ``anthropic/c…`` —
an earlier draft of this line offered ``github-copil…``, which is 13 cells and is
therefore a string this constant can never produce.

Below it the term is dropped. Until ADR-0278 the panel's rows took it back; the
panel now builds its own header (:func:`_batch_header`), so on the statusline row
— the one caller left, and only with ``extra_head`` — a dropped model is
simply not named."""


def _model_term(
    head: str, counts: Sequence[str], model: str, cap: int
) -> str:
    """What the head can afford to say about the batch's model, or ``""``.

    THE COUNTS ARE PAID FIRST. They are the substance of this row — a batch
    statusline that cannot say how many members are queued has stopped doing its
    job — and they are bounded and few, while the model is one unbounded
    provider-scoped string. So the model gets the remainder, flattened into it,
    and is dropped when the remainder is not worth spending.

    SPENDING THE REMAINDER IS FREE. It is by definition what the head and the
    counts left, so the only thing a term here displaces is the trailing numbers,
    which are appended afterwards and only while they fit. That is why the floor
    is about legibility alone (:data:`_MODEL_MIN_CELLS`) and not about what the
    term costs.

    Also bounded by ``_MAX_PROFILE_CHARS * 2`` regardless of how much room a tiny
    batch leaves, so the head cannot grow without limit on a wide terminal.

    Returning ``""`` for an empty ``model`` is the statusline's path: that surface
    passes no model and must be byte-identical to what it always rendered.
    """

    if not model:
        return ""
    flat = _flatten(model, limit=_MAX_PROFILE_CHARS * 2)
    room = cap - cell_len(" · ".join([head, *counts])) - cell_len(" · ")
    if cell_len(flat) <= room:
        # It fits whole. The floor below is about what a CUT one is worth, so it
        # must not reject a short id that needs no cutting.
        return flat
    if room < _MODEL_MIN_CELLS:
        return ""
    return _flatten(flat, limit=room)


def format_aggregate_status(
    snapshots: Sequence[SubagentProgress | None],
    *,
    extra_head: str = "",
    cap: int = AGGREGATE_MAX_CHARS,
) -> str:
    """S10 surface 1 — ONE statusline row for the whole batch.

    ``agent scout ×4 · 2 running · 1 done · 1 queued · 33s · 12.3k tok · $0.0093``

    Returns ``""`` when there is nothing to say (no members, or not one of them
    has published yet — the profile name is only knowable from a snapshot, and a
    row that cannot name the agent is noise on a shared row). The caller must
    treat ``""`` as "write no row", NOT as "write an empty row": an empty
    segment still costs the two-space join at ``chrome.py:1227``.

    NUMBERS. ``elapsed`` is the MAX over live members — they start together, so
    the oldest one is the batch's age; the executor's own measured wall clock is
    the number that reaches the final ``ToolResult`` (§3.4) and it does not
    exist yet while the batch runs. ``tokens`` is also a MAX and deliberately
    NOT a sum: ``SubagentUsage.tokens`` is documented as a context LEVEL, "last
    message wins" (``subagent_contract.py:98-99``, and ``stream.py:269-272``
    warns about exactly this), so summing it would report a number several times
    the real one — the same rule ``aggregate.roll_up_usage`` follows. ``cost``
    IS a flow and IS summed.

    WIDTH. The head and the state counts are the substance; the trailing numbers
    are appended only while they fit inside :data:`AGGREGATE_MAX_CHARS`, right to
    left, and the closing truncation is the backstop for the arithmetic.

    THE COUNTS ARE WHAT ``extra_head`` IS SIZED AGAINST, and that is the third
    correction this pair has needed. Two earlier rules both failed, in opposite
    directions:

    * conditional on the WHOLE term fitting — with one queued member the
      head, model and counts join at 77 cells against a cap of 76 — the head and
      the counts are 46 of those — so a model id
      that would have fitted truncated was dropped outright and every row carried
      it again;
    * appended UNCONDITIONALLY — position was supposed to protect the counts,
      but the closing truncation reaches them anyway. MEASURED at cap 76 with
      ``github-copilot/gpt-5.6-codex`` (28 cells) and three count classes:
      ``… · 2 running · 1 done · 1 queu…`` — a count cut mid-word, and the
      elapsed, the tokens and the cost gone with it. At nine members with five
      count classes the truncation ate ``1 queued`` entirely while
      :data:`PANEL_MAX_ROWS` dropped the queued member's own row, so the word
      appeared NOWHERE in the panel — the exact misreading :func:`_queued_line`
      exists to prevent. The docstring at the time claimed the counts were "never
      dropped" and that the hard truncation was "reachable only by a pathological
      count set"; both were false in an ordinary batch.

    :func:`_model_term` is the rule now: the model gets the cells the head and the
    counts leave, truncated to fit, and is omitted entirely below
    :data:`_MODEL_MIN_CELLS`. The term it is shown as is then a truncation the
    reader can SEE rather than a silent one.

    THE COUNTS ARE STILL NOT UNCONDITIONAL, and saying they were is the mistake
    this paragraph replaced. What changed is that the MODEL can no longer displace
    one. The closing truncation still reaches them when the head and the counts
    ALONE exceed the cap, because at that point there is nothing left to give.

    WHAT DECIDES THAT IS THE SPINE — the head joined to the five counts — and not a
    member count, which is what two earlier drafts of this paragraph both got
    wrong. The counts are spelled in decimal, so their width depends on how the
    members SPLIT across the classes and not on how many there are: MEASURED at
    cap 76 with a 5-cell profile and all five classes non-zero, some splits of 170
    members are already over while some splits of 600 still fit, so "survives to
    495" and "first cut at 484" are both samples of a scatter reported as a
    ceiling. The one clean statement is at the other end: a 16-cell profile name
    is over at FIVE members (spine 80), because the name alone spends what the
    counts need. Neither shape reaches a real batch — one ``agent()`` call is a
    handful of tasks under one profile — but the bound is arithmetic and this
    docstring should say what governs it rather than quote a number that does
    not.

    ``extra_head`` appends one term to the head. The old panel passed it to name
    the batch model in its header; since ADR-0278 the panel builds its own header
    (:func:`_batch_header`) and NO product caller passes it. The default is
    empty, so the statusline row this function produces is byte-identical to
    what it always was: there are four characters of headroom under
    :data:`AGGREGATE_MAX_CHARS` at S10's own worked example and a model term does
    not fit in them.

    NO PRODUCT SURFACE DRAWS THIS ROW ANY MORE (ADR-0278 D2): the bridge writes no
    aggregate row, because the panel header carries the same counts and the row
    was pushed off-screen by the disclosure row (measured on ``9d1517a8``). The
    function stays public and unchanged for any caller that wants the one-line
    form.
    """

    head, counts = _head_and_counts(snapshots)
    if not head:
        return ""
    live = [snapshot for snapshot in snapshots if snapshot is not None]

    term = _model_term(head, counts, extra_head, cap)
    if term:
        # BEFORE THE COUNTS, so the reader meets the batch's identity first; the
        # width it is allowed to take was already decided against them.
        head = f"{head} · {term}"
    text = " · ".join([head, *counts])

    tail = [f"{max(item.elapsed_ms for item in live) / 1000:.0f}s"]
    tokens = max(item.tokens for item in live)
    if tokens:
        tail.append(_format_tokens(tokens))
    cost = sum(item.cost for item in live)
    if cost:
        tail.append(f"${cost:.4f}")
    for segment in tail:
        candidate = f"{text} · {segment}"
        # CELLS, like :func:`_flatten`. ``_short_profile`` bounds the profile to
        # 16 CELLS, so a Hangul name is eight characters wide and sixteen columns
        # wide; measuring this join with ``len`` undercounts by that difference
        # and admits a row past the ceiling onto a shared height-1 statusline.
        # ASCII is unaffected — ``cell_len`` and ``len`` agree there — so this
        # changes nothing for a batch whose profile is spelled in ASCII.
        if cell_len(candidate) > cap:
            # Left-to-right, so the drop is always a suffix: losing the cost
            # while keeping the elapsed reads as terseness; the reverse reads as
            # a bug.
            break
        text = candidate

    if cell_len(text) > cap:
        return _cut_to_cells(text, cap - 1) + _ELLIPSIS
    return text


def _child_line(progress: SubagentProgress) -> str:
    """P2's ``extension._partial_text`` for every CLEAN snapshot — and only those.

    THE CONTRACT WAS RE-DECIDED, NOT QUIETLY BROKEN (#178, ADR-0199 §(l)
    amendment 2026-10-08). This used to say "VERBATIM": the single-child card
    had to be byte-identical to what P2 wrote, which kept the shipped
    single-delegation transcript unchanged under S2. Verbatim was also how a
    child put raw bytes into it. ``current_tool`` is the child's own stdout
    (``stream.py`` keeps any non-empty ``tool_execution_start.tool_name``), and
    MEASURED on ``8f7d98aa``: ``profile="scout\\x1b[2J"`` with
    ``current_tool="read\\r\\x1b[31mFAKE"`` came out of :func:`format_card` with
    the ESC and the CR intact, while the widget beside it printed the same two
    fields flattened. One child, two surfaces, two answers.

    So every term this line interpolates goes through :func:`_flatten` at
    :data:`PANEL_ROW_MAX_CHARS` — the widget's own sanitiser and its own per-field
    bound (``_panel_row``), one function and no copy — and the contract is
    now two halves, both pinned by rows in ``tests/agents_ext``:

    * IDENTITY for a clean field: no steering character
      (``aelix_ai.utils.terminal_text``: C0, DEL, C1, the BiDi overrides and
      isolates, U+2028/U+2029, ZWSP, BOM); no whitespace except single ASCII
      spaces (U+0020) between words — so no leading, trailing or repeated
      whitespace, and no tab, no no-break space (U+00A0, U+202F), no em or other
      typographic space (U+2000-U+200A) and no ideographic space (U+3000); no
      lone surrogate (which no UTF-8 output can write; #132 review round 1);
      and it fits — at most 78 cells AND at most 312 code points, the zero-width
      backstop (:data:`_ZERO_WIDTH_SLACK` code points per cell), which only a
      run of combining or other zero-width marks reaches. Every built-in tool
      name, every profile name aelix's runtime publishes (the validated
      ``name:`` field, ``^[a-z0-9-]+$``, at most 64) and the five
      ``SubagentState`` literals are clean, so the shipped card is unchanged
      byte for byte.
    * SANITISED otherwise, and the output differs from P2's exactly where the
      input carried one of those. What happens to each: a steering character is
      DELETED, and only that character — an escape sequence is defanged, not
      removed: its ESC (or one-byte C1 introducer) goes and the rest of it stays
      as inert text, so ``\\x1b[31m`` prints ``[31m``. Every run of whitespace,
      in any of ``str.split``'s spellings (a newline, CR, tab, NEL and every
      Unicode space above), becomes ONE U+0020, and leading and trailing
      whitespace goes — also where deleting a steering character left them, so
      ``"a \\x1b b"`` prints ``"a b"`` — and a tool name ``"read\\u00a0file"``
      prints ``"read file"``. A lone surrogate becomes U+FFFD. A field wider
      than 78 cells or longer than 312 code points is cut and ends in ``…``.

    The second and third clauses of "clean" are the widget's, kept rather than
    special-cased: a field that needs them was never a name, and an unbounded
    one is a megabyte in the permanent record per frame.

    ``current_tool`` is tested AFTER flattening, so a tool name made only of
    controls and whitespace drops the term instead of leaving an empty ``·  ·``.

    A non-``str`` field is ``str()``-ed first, which is what P2's f-string did
    with it: ``profile=None`` still prints ``agent None``. The contract types all
    three as ``str``, but ``SubagentProgress`` is a public contract another
    runtime may fill, and raising here would cost the card, not just the term.
    """

    tool_name = (
        _flatten(str(progress.current_tool), limit=PANEL_ROW_MAX_CHARS)
        if progress.current_tool
        else ""
    )
    tool = f" · {tool_name}" if tool_name else ""
    profile = _flatten(str(progress.profile), limit=PANEL_ROW_MAX_CHARS)
    state = _flatten(str(progress.state), limit=PANEL_ROW_MAX_CHARS)
    return f"agent {profile} [{state}]{tool} · {progress.elapsed_ms / 1000:.0f}s"


def _queued_line(profile: str) -> str:
    """A member that is parked on the semaphore and has no snapshot at all.

    It is named rather than omitted: a card that silently shows 3 of 8 rows
    reads as "5 tasks were dropped", and the model reads this card too.

    ``profile`` is a published member's, so it is flattened here exactly as
    :func:`_child_line` flattens it, ``str()`` first — a queued row is the same
    card (#178).
    """

    name = _flatten(str(profile), limit=PANEL_ROW_MAX_CHARS) if profile else ""
    return f"agent {name} [queued]" if name else "[queued]"


def _batch_profile(snapshots: Sequence[SubagentProgress | None]) -> str:
    """The one profile the whole batch runs under (S3 — one profile × N tasks).

    Taken from the first member that has published, because a queued member
    cannot report its own name and there is only ever one name to report.
    """

    return next(
        (snapshot.profile for snapshot in snapshots if snapshot is not None), ""
    )


def format_card(snapshots: Sequence[SubagentProgress | None]) -> str:
    """S10 surface 2 — the ``ctx.on_partial`` tool card, one line per child.

    At ``N == 1`` the output is P2's, byte for byte, with NO index prefix, for
    every clean snapshot: the single-delegation transcript is not something this
    phase is allowed to change. A snapshot that is not clean — a steering
    character, whitespace other than single ASCII spaces, or a field over 78
    cells or 312 code points — is sanitised instead; :func:`_child_line` states both halves and
    what "clean" means (#178). At ``N >= 2`` every line carries ``[k/N]`` in
    SUBMITTED order — the order the model wrote the tasks in and the order
    ``aggregate`` renders the results in (§3.4: "never completion order").
    """

    total = len(snapshots)
    if total == 0:
        return ""
    if total == 1:
        return "" if snapshots[0] is None else _child_line(snapshots[0])

    profile = _batch_profile(snapshots)
    return "\n".join(
        f"[{index + 1}/{total}] "
        + (_child_line(snapshot) if snapshot is not None else _queued_line(profile))
        for index, snapshot in enumerate(snapshots)
    )


def _batch_model(snapshots: Sequence[SubagentProgress | None]) -> str:
    """The one model the whole batch runs under, or ``""`` if they disagree.

    Mirrors :func:`_batch_profile`. One ``agent()`` call is one profile and one
    model across N tasks (S3), so in practice this is every published member's
    ``model`` — but ``model`` is read off each CHILD's own ``message_end``, so
    two children of one profile CAN report different strings (a provider
    fallback, a mid-batch alias resolution). Disagreement returns ``""`` and the
    panel's rows carry their own model instead (:func:`_batch_block`), because a
    header claiming one model for a batch running two is worse than a repeated
    column.
    """

    models = {s.model for s in snapshots if s is not None and s.model}
    return next(iter(models)) if len(models) == 1 else ""


# ── the delegation panel (#132, ADR-0278) ─────────────────────────────────

PANEL_SINGLE_ROWS = 5
"""The height of the one-child block (ADR-0278 D4): its header, its last three
calls and its last reply. A resume is one child and gets the same block."""

_COMPACT_FROM = 5
"""From this many members the panel draws one row per member (D4). Two rows
each would be 11 rows at five, past :data:`PANEL_MAX_ROWS`."""

_ROWS_FALLBACK = 24
"""Terminal rows assumed when the bridge could not measure them — the common
default, so an unknown terminal gets the layout a 24-row one gets."""

_DEFAULT_WIDTH = 80
"""Columns assumed when a caller passes none. The bridge always passes the live
width (``tui.width.terminal_columns``), so this is for direct callers only."""

_DIM = "\x1b[2m"
_RST = "\x1b[0m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_REPLY = "\x1b[2;3m"
"""Dim italic, for the child's own words. Every SGR this module writes is one of
these seven — ``chrome._render_widget_lines`` ANSI-parses the joined rows, so raw
SGR is the module's vocabulary, and a test pins the set."""

_GUTTER = "│ "
"""The panel's left edge: the same ``│ `` gutter a transcript tool card's body
is drawn with (``tui/render.py``), so the panel reads as the live body of the
``● agent(…)`` header printed above it (D4). Dim; two cells, budgeted for."""

_IDLE_SHOWN_MS = 5_000
"""``idle Ns`` appears once a live child has written nothing for this long (D4)."""

_CALLS_SHOWN = 3
"""Calls the one-child block lists, and outcome marks a two-row member shows
before the call open now (D4). ``stream.ACTIVITY_MAX`` is one more than this."""

_HEAD_FIELD_CELLS = 32
"""Bound on a profile or a model term in a header or a row — child-authored, so
bounded before layout, and wide enough for any real id the row has room for."""

_NUMBERS_FLOOR = 12
"""What a row's left side may be cut to before its right-hand numbers start
being dropped. A label cut shorter than this no longer says which task it is."""

_PREVIOUS_TOKEN = "{previous}"
_PREVIOUS_ESCAPE = "{{previous}}"
_PREVIOUS_SHOWN = "‹previous›"
"""A chain step's placeholder as a label shows it (D4). The two spellings are
``chain.PREVIOUS_TOKEN`` and ``chain.PREVIOUS_ESCAPE``, restated so this module
imports nothing from ``aelix_agents``; a test pins that they agree."""

_STATE_GLYPHS: dict[str, tuple[str, str]] = {
    "queued": ("○", _DIM),
    "starting": ("●", _CYAN),
    "running": ("●", _CYAN),
    "thinking": ("●", _CYAN),
    "done": ("✓", _GREEN),
    "failed": ("✗", _RED),
    "stopped": ("■", _YELLOW),
}
"""Phase → (glyph, style) for a member's row and the one-child header (D4)."""

_CALL_MARKS: dict[str, tuple[str, str]] = {
    "running": ("▶", _CYAN),
    "ok": ("✓", _GREEN),
    "failed": ("✗", _RED),
    "no_result": ("■", _YELLOW),
}
"""``SubagentToolStatus`` → (mark, style). An unknown status reads ``no_result``:
whatever it is, the panel cannot claim the call finished."""


class _Line:
    """One panel row under construction: text parts and the SGR each wears.

    THE WIDTH BOUND IS APPLIED TO THE VISIBLE TEXT AS A WHOLE (:meth:`render`),
    measured once with :func:`_cells` — rich's measure and prompt_toolkit's,
    whichever is larger — and cut with :func:`_cut_to_cells`, so a grapheme
    cluster spanning two parts is counted the way the terminal draws it and no
    character lands past the column prompt_toolkit's window clips at; the
    styles are then laid back over what survived. Every part is either text
    this module authored or a child string already :func:`_flatten`-ed, so an
    ESC can only come from the style column.
    """

    __slots__ = ("_parts",)

    def __init__(self) -> None:
        self._parts: list[tuple[str, str]] = []

    def add(self, text: str, style: str = "") -> _Line:
        if text:
            self._parts.append((text, style))
        return self

    @property
    def visible(self) -> str:
        return "".join(text for text, _ in self._parts)

    def cells(self) -> int:
        return _cells(self.visible)

    def render(self, width: int) -> tuple[str, int]:
        """``(styled text, cells)`` — never wider than ``width`` cells, and
        ending in ``…`` when it had to be cut."""

        if width <= 0:
            return "", 0
        visible = self.visible
        keep, cut = len(visible), False
        if _cells(visible) > width:
            prefix = _cut_to_cells(visible, width - 1)
            while prefix and _cells(prefix + _ELLIPSIS) > width:
                prefix = prefix[:-1]
            keep, cut = len(prefix), True
        out: list[str] = []
        pos = 0
        for text, style in self._parts:
            if pos >= keep:
                break
            piece = text[: keep - pos]
            pos += len(piece)
            out.append(f"{style}{piece}{_RST}" if style else piece)
        if cut:
            out.append(_ELLIPSIS)
        shown = visible[:keep] + (_ELLIPSIS if cut else "")
        return "".join(out), _cells(shown)


def _gutter_only(width: int) -> str:
    """A row with nothing in it yet: the gutter alone, so the panel keeps its
    shape (and its height, D5) while a child has not filled the row."""

    return f"{_DIM}{_cut_to_cells(_GUTTER.rstrip(), max(width, 0))}{_RST}" if width > 0 else ""


_GAP = 2
"""The fewest cells between a row's left side and its right-hand numbers (#132
review round 1): with one, a full row read as one run of text —
``…conds, then report. 3s · 15`` at 60 columns."""


def _row(left: _Line, right: _Line | None, width: int, *, floor: int = 0) -> str:
    """Gutter, ``left``, and ``right`` flush against the right edge — in at most
    ``width`` cells, with at least :data:`_GAP` between the two.

    When both do not fit, ``left`` is cut (never below ``floor`` cells, or its
    own width if smaller) and ``right`` keeps its place; below that, ``right``
    goes. Callers drop right-hand terms first (:func:`_fit_header`,
    :func:`_numbers_column`), so this only decides between "cut the left" and
    "no numbers at all".
    """

    gutter_cells = _cells(_GUTTER)
    if width <= gutter_cells:
        return _gutter_only(width)
    gutter = f"{_DIM}{_GUTTER}{_RST}"
    avail = width - gutter_cells
    right_cells = right.cells() if right is not None else 0
    if right is None or right_cells == 0 or right_cells + _GAP > avail:
        text, _ = left.render(avail)
        return gutter + text
    room = avail - right_cells - _GAP
    left_cells = left.cells()
    if room < min(left_cells, floor):
        text, _ = left.render(avail)
        return gutter + text
    text, used = left.render(min(left_cells, room))
    numbers, _ = right.render(right_cells)
    return gutter + text + " " * (avail - used - right_cells) + numbers


class _Term(NamedTuple):
    """One right-hand number: its text, its RANK (the lowest is dropped first
    when a row is short of room) and the cells it RESERVES (#132 review
    round 1).

    The reserve is what keeps a cut still. A row's left side is cut to what its
    numbers leave, so a number that gains a digit — ``9s`` → ``10s`` →
    ``1m00s``, ``15`` → ``0.1k`` → ``12.3k`` — used to re-cut every label of a
    batch at once (live: ``with the bash…files you saw.`` → ``with the…you
    saw.``). Sized for :data:`_NUMBER_RESERVE` cells while there is room, the
    cut holds through those transitions; past it (``10m00s``, ``123.4k``) it
    moves, and moves back once the number is inside the reserve again
    (``1h00m``, ``1.0M``)."""

    text: str
    rank: int
    reserve: int = 0


_NUMBER_RESERVE = 5
"""Cells an elapsed time or a token count is laid out for: ``1m00s`` and
``12.3k``, so ``9s``, ``59s``, ``99`` and ``9.9k`` all grow into the room they
already have."""

_IDLE_RESERVE = len("idle ") + _NUMBER_RESERVE
"""A header's ``idle 1m00s``: the idle time is a clock too (#132 review round 2)."""

_TOKENS_RESERVE = _NUMBER_RESERVE + len(" tok")
"""A header's ``12.3k tok`` (#132 review round 2)."""

_ELAPSED_RANK = 6
"""The elapsed time's rank in every header: above every other number and above
the model and the profile, because it is the liveness signal the 1 s tick
exists to move (#132 review round 1)."""


def _terms_cells(terms: Sequence[_Term], *, reserved: bool) -> int:
    """Width of ``terms`` joined with `` · `` — at their reserves, or as they are."""

    if not terms:
        return 0
    widths = [
        max(_cells(term.text), term.reserve) if reserved else _cells(term.text) for term in terms
    ]
    return sum(widths) + 3 * (len(terms) - 1)


def _terms_line(terms: Sequence[_Term]) -> _Line | None:
    return _Line().add(" · ".join(term.text for term in terms), _DIM) if terms else None


def _fit_header(
    build: Callable[[frozenset[str]], _Line],
    parts: Mapping[str, int],
    terms: Sequence[_Term],
    avail: int,
    *,
    slack: int = 0,
) -> tuple[_Line, _Line | None]:
    """A header's left side and its numbers, in at most ``avail`` cells.

    ``parts`` are the left side's optional terms (``model``, ``profile``, the
    Esc frame's fixed phrases) by rank; ``build`` draws the left side with the
    ones kept. The items are taken HIGHEST RANK FIRST, a number or a part
    alike, and each is kept when it fits beside the ones already kept — so the
    elapsed time (:data:`_ELAPSED_RANK`) is decided first, then the profile,
    the model, the tokens, the idle time and the cost (#132 review round 2:
    the order used to be strict, so a header too narrow for a long model id
    dropped the tokens and the idle time too, and MEASURED live at 60 columns
    read ``● agent worker · running`` and 32 blank cells before ``6s``).
    A term that does not fit no longer takes the lower-ranked ones with it —
    except the elapsed time: a header that cannot show it shows nothing else
    optional, so it is still the last to go.

    EVERY ITEM IS JUDGED AT A WIDTH THAT DOES NOT MOVE with the clock or the
    state word: each number at its reserve (:class:`_Term` — ``1m00s``,
    ``idle 1m00s``, ``12.3k tok``) and the left side with ``slack`` more
    cells (the widest state word). So no term comes or goes at 9 s → 10 s →
    1m00s or as a child moves between ``running`` and ``thinking``; the row is
    then drawn at the numbers' actual widths, which a reserve never
    undercounts. A number with a reserve and no text yet — the idle time
    before five quiet seconds, the tokens before the first reply — holds its
    place all the same and is drawn when it has something to say, so the idle
    time appearing does not push the cost out (or let it back in) every time
    a child goes quiet. What ``build`` always draws — the counts of a batch —
    is never dropped here, and is cut by :func:`_row` only when nothing else
    is left to give.
    """

    items = sorted(
        [(rank, name, None) for name, rank in parts.items()]
        + [(term.rank, "", term) for term in terms if term.text or term.reserve],
        key=lambda item: -item[0],
    )
    kept_parts: frozenset[str] = frozenset()
    kept: list[_Term] = []
    for rank, name, term in items:
        trial_parts = kept_parts | {name} if name else kept_parts
        trial = [*kept, term] if term is not None else kept
        right = _GAP + _terms_cells(trial, reserved=True) if trial else 0
        if build(trial_parts).cells() + slack + right <= avail:
            kept_parts, kept = trial_parts, trial
        elif rank >= _ELAPSED_RANK:
            break
    order = {id(term): index for index, term in enumerate(terms)}
    kept.sort(key=lambda term: order[id(term)])
    return build(kept_parts), _terms_line([term for term in kept if term.text])


def _number(value: object) -> int:
    """A child-reported count as a non-negative ``int``; anything else is 0.
    Another ``SubagentRuntime`` may fill these fields, and a render must not
    raise over a ``None`` or a NaN."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    try:
        return max(0, int(value))
    except (OverflowError, ValueError):
        return 0


def _format_elapsed(ms: int) -> str:
    """``9s``, ``1m12s``, ``1h02m`` (D4)."""

    seconds = _number(ms) // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _compact_tokens(tokens: int) -> str:
    """``15``, ``0.4k``, ``1.5k``, ``1.2M`` — a context LEVEL, never a sum
    (``SubagentUsage.tokens``)."""

    tokens = _number(tokens)
    if tokens < 100:
        return str(tokens)
    if tokens < 999_950:
        return f"{tokens / 1000:.1f}k"
    return f"{tokens / 1_000_000:.1f}M"


def _cost(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    try:
        cost = float(value)
    except (OverflowError, ValueError):
        return 0.0
    return cost if math.isfinite(cost) else 0.0


def _bounded(text: str, limit: int) -> str:
    """:func:`_flatten`, then no wider than ``limit`` by :func:`_cells` too.

    ``_flatten`` decides "fits" with rich's measure, which is its contract with
    the card and the statusline row; a panel field must fit prompt_toolkit's as
    well (#132 review round 1), or the row's own cut takes the field's closing
    quote and whatever follows it."""

    flat = _flatten(text, limit=limit)
    # ``_flatten`` already bounded it by rich's measure; only prompt_toolkit's
    # can still be over.
    if not flat.isascii() and _ptk_cells(flat) > limit:
        return _cut_to_cells(flat, max(0, limit - 1)) + _ELLIPSIS
    return flat


def _flat(value: object, limit: int) -> str:
    """A child-authored field, ``str()``-ed, flattened, at most ``limit`` cells."""

    if value is None or limit <= 0:
        return ""
    return _bounded(str(value), limit)


def _activity(snapshot: SubagentProgress, *, cancelled: bool) -> list[object]:
    """The snapshot's recent calls, oldest first, at most ``ACTIVITY_MAX``.

    Read defensively — ``activity`` is a public field another runtime may fill
    with anything — and, in the Esc frame, every call still ``running`` is shown
    as the stop left it: ``no_result`` (D7).
    """

    raw: object = getattr(snapshot, "activity", ())
    items: list[object] = (
        _keep(list(raw), _CALLS_SHOWN + 1, "running") if isinstance(raw, tuple | list) else []
    )
    if not cancelled:
        return items
    return [_CutCall(item) if _status(item) == "running" else item for item in items]


def _keep(items: Sequence[object], count: int, want: str) -> list[object]:
    """The latest ``count`` of ``items``, in order — but the latest one whose
    status is ``want`` stays among them however many came after it (#132
    review round 1).

    A call still running, or the call a stop cut, is what a member is doing;
    four later calls that finished do not make it something it did. MEASURED:
    with a slow ``grep`` open and four ``read`` calls finished after it, the
    one-child block listed the three reads and no ``▶``."""

    if count <= 0:
        return []
    latest = list(items[-count:])
    if not want or len(items) <= count:
        return latest
    held = next((item for item in reversed(items) if _status(item) == want), None)
    if held is None or any(item is held for item in latest):
        return latest
    return [held, *items[len(items) - count + 1 :]]


class _CutCall:
    """A call the cancel cut: same name and arguments, status ``no_result``."""

    __slots__ = ("args", "name", "status")

    def __init__(self, item: object) -> None:
        self.name = getattr(item, "name", "")
        self.args = getattr(item, "args", "")
        self.status = "no_result"


def _status(item: object) -> str:
    status = getattr(item, "status", "no_result")
    return status if status in _CALL_MARKS else "no_result"


def _call_text(item: object, limit: int) -> str:
    """One call as the panel shows it (D4): ``bash`` as ``$ <command>``, any
    other tool as ``<name> <primary argument>`` — the argument is the trail's
    rendering (``stream._render_args``). Both halves are child-authored and are
    flattened on their own, then the join is bounded to ``limit`` cells."""

    if limit <= 0:
        return ""
    name = _flat(getattr(item, "name", ""), _MAX_INPUT_CHARS)
    args = _flat(getattr(item, "args", ""), _MAX_INPUT_CHARS)
    if name == "bash":
        text = f"$ {args}" if args else "bash"
    else:
        text = f"{name} {args}" if args else name or "?"
    return _bounded(text, limit)


def _quoted(text: object, room: int) -> str:
    """The child's last reply as ``“…”`` in at most ``room`` cells, or ``""``
    when there is no room worth quoting into. The cut goes INSIDE the quotes."""

    if not text or room < 5:
        return ""
    inner = _flat(text, room - 2)
    return f"“{inner}”" if inner else ""


def _phase(
    snapshot: SubagentProgress | None,
    items: Sequence[object],
    *,
    cancelled: bool,
    result: object | None = None,
) -> str:
    """What a member is doing, in the panel's words (D4): ``queued``,
    ``starting``, ``running`` (a call is open), ``thinking`` (live, no call
    open), ``done``, ``failed`` or ``stopped``.

    In the Esc frame a member that was still live reads ``stopped`` — that is
    what the cancel did to it — and so does one whose envelope says the cancel
    ended it (``status == "aborted"``), whatever its last snapshot said: a
    channel that raises the cancel without marking its row leaves ``_run`` to
    promote the row to ``error`` on the way out, which is a failure nobody had.
    """

    if snapshot is None:
        return "queued"
    if cancelled and getattr(result, "status", None) == "aborted":
        return "stopped"
    state = snapshot.state
    if state == "done":
        return "done"
    if state == "error":
        return "failed"
    if state == "stopped" or cancelled:
        return "stopped"
    if state == "starting":
        return "starting"
    if any(_status(item) == "running" for item in items):
        return "running"
    return "thinking"


def _live(phase: str) -> bool:
    return phase in ("starting", "running", "thinking")


def _elapsed_now(snapshot: SubagentProgress, idle_ms: int | None, phase: str) -> int:
    """The child's age NOW: its last snapshot's ``elapsed_ms`` plus the time
    since that snapshot arrived, while it is live. That sum is what lets the 1 s
    tick (D6) advance a silent child's clock."""

    elapsed = _number(snapshot.elapsed_ms)
    if _live(phase) and idle_ms is not None:
        elapsed += _number(idle_ms)
    return elapsed


def _idle_term(idle_ms: int | None, phase: str) -> str:
    if not _live(phase) or idle_ms is None or _number(idle_ms) < _IDLE_SHOWN_MS:
        return ""
    return f"idle {_format_elapsed(idle_ms)}"


def _label(task: object, mode: str) -> str:
    """A member's submitted task as its label. In a chain, ``{previous}`` reads
    ``‹previous›`` (it is a placeholder, not text the child saw) and the escape
    ``{{previous}}`` reads as the literal it stands for. Work-bounded before the
    substitution, as :func:`_flatten` bounds its own."""

    text = str(task).strip()[:_MAX_INPUT_CHARS]
    if mode != "chain":
        return text
    return _PREVIOUS_TOKEN.join(
        part.replace(_PREVIOUS_TOKEN, _PREVIOUS_SHOWN) for part in text.split(_PREVIOUS_ESCAPE)
    )


_LABEL_WORK_CELLS = 512
"""How much of a task a label is cut FROM. A task can be 64 KiB; past this the
label is cut at its end like any other field."""


@lru_cache(maxsize=16)
def _flat_label(task: str, mode: str) -> str:
    """A task as its label, flattened and bounded to :data:`_LABEL_WORK_CELLS`.

    CACHED (#132 review round 2) because a group's tasks never change while it
    draws, and this is most of a frame's work on a long one: MEASURED, 1.25 ms
    per 64 KiB task of ``❤️`` — 10 ms of a 13 ms eight-member frame, paid
    again on every redraw and every 1 s tick. Sixteen entries hold two full
    batches; a task is at most ``chain.MAX_TASK_BYTES`` (64 KiB of UTF-8, at
    most 256 KiB as a ``str``), so the cache holds a few megabytes at worst."""

    return _bounded(_label(task, mode), _LABEL_WORK_CELLS)

_WORD_SNAP = 4
"""A middle cut's two ends move to the nearest word boundary when it is at most
this many characters away, so neither end is a broken word if it can help."""

_MIDDLE_CUT_FROM = 24
"""Below this many cells a label is cut at its end: there is no room for two
ends that each still say something (measured on the baseline's own tasks, a
16-cell middle cut read ``[TAG:A] Read…report.``)."""


def _tail_cells(text: str, cells: int) -> str:
    """The last ``cells`` columns of ``text``, EXACTLY — :func:`_cut_to_cells`
    from the other end, with the same measure (:func:`_cells`) and the same
    whole-string check, because ``cell_len`` is not additive over grapheme
    clusters.

    NOT BISECTED, unlike :func:`_cut_to_cells` (#132 review round 2): a
    suffix's width is not monotonic in where it starts — one that begins at a
    ZWJ or a VS16 can measure narrower than the one a character later
    (MEASURED, 12 of 200 000 random emoji strings cut differently) — so a
    bisection would change the cut. Nor does it need one: its only caller is
    :func:`_fit_label`'s middle cut, which is reached only by a label of at
    most four code points per cell of a row, so this loop is bounded by the
    row's width."""

    if cells <= 0:
        return ""
    if _cells(text) <= cells:
        return text
    out: list[str] = []
    used = 0
    for char in reversed(text):
        width = _cells(char)
        if used + width > cells:
            break
        out.append(char)
        used += width
    tail = "".join(reversed(out))
    while tail and _cells(tail) > cells:
        tail = tail[1:]
    return tail


def _fit_label(task: str, limit: int) -> str:
    """A task label in at most ``limit`` cells, cut in the MIDDLE when it must
    be cut and there is room for both ends.

    A fan-out's tasks share their start and differ at their end — ``port the
    retry backoff to google`` / ``… to openai`` — and a label cut at its end
    turns four rows into one string four times, which is the failure a review
    measured on this panel's predecessor (ADR-0227: "1 distinct label"). So the
    head keeps three fifths and the tail the rest, each snapped to a word
    boundary when one is within :data:`_WORD_SNAP`. Flattened first (the task is the PARENT model's
    text and untrusted), and bounded by the whole-string measure at the end.
    """

    return _fit_flat(_bounded(task, _LABEL_WORK_CELLS), limit)


def _whole(flat: str, limit: int) -> bool:
    """Does an already-flattened label fit ``limit`` cells uncut?

    BOTH UNITS, as :func:`_flatten` keeps them: a run of zero-width marks
    measures nothing, so a cell check alone would pass 2 048 code points of
    them through as "fits"."""

    return limit > 0 and _cells(flat) <= limit and len(flat) <= limit * _ZERO_WIDTH_SLACK


def _fit_flat(flat: str, limit: int) -> str:
    """:func:`_fit_label` for a label the caller already flattened and bounded
    to :data:`_LABEL_WORK_CELLS` — as :func:`_batch_block` does once per frame —
    so the row does not flatten it again (#132 review round 2). ``_bounded``
    is idempotent, so the cut is the same."""

    if limit <= 0:
        return ""
    budget = limit * _ZERO_WIDTH_SLACK
    if _whole(flat, limit):
        return flat
    if limit < _MIDDLE_CUT_FROM or flat.endswith(_ELLIPSIS) or len(flat) > budget:
        return _bounded(flat, limit)
    head = _cut_to_cells(flat, (limit - 1) * 3 // 5)
    space = head.rfind(" ")
    if space > 0 and len(head) - space <= _WORD_SNAP:
        head = head[:space]
    head = head.rstrip()
    tail = _tail_cells(flat, limit - 1 - _cells(head))
    space = tail.find(" ")
    if 0 <= space < _WORD_SNAP:
        tail = tail[space + 1 :]
    out = f"{head}{_ELLIPSIS}{tail.lstrip()}"
    return out if _cells(out) <= limit and len(out) <= budget else _bounded(flat, limit)


def _changed(result: object, room: int) -> str:
    """``a.md, b.py +3 more`` — the files a stopped member's writer tools
    changed (``SubagentResult.changed_files``), child-authored and bounded."""

    files = getattr(result, "changed_files", ())
    if not isinstance(files, tuple | list) or not files or room <= 0:
        return ""
    overflow = _number(getattr(result, "files_overflow", 0))
    more = f" +{overflow} more" if overflow else ""
    shown = ", ".join(_flat(path, _MAX_INPUT_CHARS) for path in files[:16])
    if len(files) > 16:
        more = f" +{len(files) - 16 + overflow} more"
    return _bounded(shown, max(1, room - _cells(more))) + more


def _marks(line: _Line, items: Sequence[object]) -> None:
    for item in items[-_CALLS_SHOWN:]:
        mark, style = _CALL_MARKS[_status(item)]
        line.add(mark, style)


def _split_current(items: Sequence[object], phase: str) -> tuple[list[object], object | None]:
    """``(marks, current)``: the call open now — or, for a stopped member, the
    call the stop cut — and the calls around it, in order, as outcome marks."""

    want = "running" if phase == "running" else "no_result" if phase == "stopped" else ""
    if not want:
        return list(items), None
    for index in range(len(items) - 1, -1, -1):
        if _status(items[index]) == want:
            return [item for k, item in enumerate(items) if k != index], items[index]
    return list(items), None


def _now(
    line: _Line,
    room: int,
    *,
    phase: str,
    current: object | None,
    snapshot: SubagentProgress | None,
    idle_ms: int | None,
    waiting: str,
    cancelled: bool,
    result: object | None,
) -> None:
    """What a member is doing NOW, appended to ``line`` in about ``room`` cells
    (row 2 of a two-row member, the tail of a compact one; D4/D7)."""

    used = line.cells()

    def left() -> int:
        return room - (line.cells() - used)

    idle = _idle_term(idle_ms, phase)
    reply = getattr(snapshot, "last_text", None) if snapshot is not None else None
    if phase == "queued":
        line.add("not started" if cancelled else waiting, _DIM)
    elif phase == "starting":
        line.add("starting", _DIM)
    elif phase == "running" and current is not None:
        suffix = f" · {idle}" if idle else ""
        if left() - 2 - _cells(suffix) < _NUMBERS_FLOOR:
            suffix = ""
        line.add("▶ ", _CYAN).add(_call_text(current, left() - 2 - _cells(suffix)))
        line.add(suffix, _DIM)
    elif phase == "thinking":
        line.add("thinking", _DIM)
        if idle:
            line.add(f" · {idle}", _DIM)
        quoted = _quoted(reply, left() - 3)
        if quoted:
            line.add(" · ", _DIM).add(quoted, _REPLY)
    elif phase == "done":
        quoted = _quoted(reply, left())
        if quoted:
            line.add(quoted, _REPLY)
        else:
            line.add("done", _DIM)
    elif phase == "failed":
        line.add("failed", _RED)
        quoted = _quoted(reply, left() - 3)
        if quoted:
            line.add(" · ", _DIM).add(quoted, _REPLY)
    else:  # stopped
        if not cancelled:
            line.add("stopped", _YELLOW)
        if current is not None:
            if not cancelled:
                line.add(" · ", _DIM)
            # Whole or not at all, as the one-child block does it (#132 review
            # round 2): the call keeps :data:`_NUMBERS_FLOOR` cells first. The
            # room is measured after the mark, as it always was. (``call``,
            # not ``room``: ``left()`` reads ``room``.)
            tail = " · no result"
            line.add("■ ", _YELLOW)
            call = left() - 2 - _cells(tail)
            if call < _NUMBERS_FLOOR:
                tail, call = "", left() - 2
            line.add(_call_text(current, call)).add(tail, _DIM)
        elif cancelled:
            line.add("stopped", _YELLOW)
        files = _changed(result, left() - 12) if cancelled else ""
        if files:
            line.add(" · changed: ", _DIM).add(files)


# ── one child: the five-row block (D4, D7) ────────────────────────────────

_RESUME_HINT = "Ask to continue it and the model can resume it."
_NO_RESUME_HINT = "It has no session, so it cannot be resumed."
"""The Esc frame's last row (D7). The first only when the child HAS a session
to resume (#132 review round 1): a ``--no-session`` parent's children have
none (``SubagentResult.started``'s note), and ``agent(action="resume")`` has
nothing to continue, so the frame said something false there.

Shown whole or not at all (#132 review round 2): a fixed phrase cut mid-word
— ``Ask to continue it and the model c…`` — is a sentence nobody wrote."""

_CANCELLED = " — the turn was cancelled"
"""What the Esc frame's header says after ``stopped`` (D7). Like the resume
hint, it is a phrase this module wrote, so it is shown whole or not at all —
an optional part of the header (:func:`_fit_header`), never cut mid-word by
:func:`_row` (#132 review round 2; MEASURED live at 60 columns, the batch
header read ``… · stopped — the turn was cancelled · ask to cont…``)."""


def _resumable(result: object | None) -> bool:
    session = getattr(result, "session_id", None)
    return isinstance(session, str) and bool(session)


def _single_block(
    snapshot: SubagentProgress | None,
    *,
    width: int,
    profile: str,
    resumed: bool,
    idle_ms: int | None,
    cancelled: bool,
    result: object | None,
) -> list[str]:
    items = _activity(snapshot, cancelled=cancelled) if snapshot is not None else []
    phase = _phase(snapshot, items, cancelled=cancelled, result=result)
    avail = max(0, width - _cells(_GUTTER))
    glyph, glyph_style = _STATE_GLYPHS[phase]

    name = _flat(getattr(snapshot, "profile", None) or profile, _HEAD_FIELD_CELLS)
    model = "" if cancelled else _flat(getattr(snapshot, "model", None), _HEAD_FIELD_CELLS)

    def header(parts: frozenset[str]) -> _Line:
        head = _Line().add(glyph, glyph_style)
        head.add(f" agent {name}" if name and "profile" in parts else " agent")
        if resumed:
            head.add(" (resumed)", _DIM)
        if cancelled:
            word = "stopped" if phase == "stopped" else phase
            text = f"{word}{_CANCELLED}" if "cancelled" in parts else word
            head.add(" · ").add(text, _YELLOW if word == "stopped" else "")
            return head
        if model and "model" in parts:
            head.add(" · ").add(model)
        return head.add(" · ").add("starting" if phase == "queued" else phase, _DIM)

    terms: list[_Term] = []
    if cancelled:
        turns = _number(getattr(getattr(result, "usage", None), "turns", 0))
        if turns:
            terms.append(_Term(f"{turns} turn{'s' if turns != 1 else ''}", 1))
        if snapshot is not None:
            # No reserve: the Esc frame does not tick, so its numbers never
            # grow, and a reserve would only cost room (#132 review round 2:
            # reserved, ``2 turns`` went at 66 columns where it fits).
            terms.append(_Term(_format_elapsed(snapshot.elapsed_ms), _ELAPSED_RANK))
    elif snapshot is not None:
        tokens = _number(snapshot.tokens)
        cost = _cost(snapshot.cost)
        terms = [
            _Term(_idle_term(idle_ms, phase), 2, _IDLE_RESERVE),
            _Term(
                _format_elapsed(_elapsed_now(snapshot, idle_ms, phase)),
                _ELAPSED_RANK,
                _NUMBER_RESERVE,
            ),
            _Term(f"{_compact_tokens(tokens)} tok" if tokens else "", 3, _TOKENS_RESERVE),
            _Term(f"${cost:.4f}" if cost else "", 1),
        ]
    # PRIORITY (#132 review rounds 1 and 2): the elapsed time first — it is
    # what the 1 s tick moves, so a header without it says nothing about
    # whether the child is alive (MEASURED before round 1: at 56-61 columns
    # the header kept ``anthropic/claude-haiku-4.5`` and dropped the time) —
    # then the profile, the model, the tokens, the idle time and the cost,
    # each kept when it fits (:func:`_fit_header`). Judged with the WIDEST
    # state word (``thinking``/``starting``), so no term comes and goes as the
    # child moves between running and thinking. In the Esc frame the phrase
    # after ``stopped`` takes the model's place, and the turns the cost's.
    parts = {"cancelled": 4, "profile": 5} if cancelled else {"model": 4, "profile": 5}
    slack = max(len("thinking") - len(phase), 0) if not cancelled else 0
    head, numbers = _fit_header(header, parts, terms, avail, slack=slack)
    rows = [_row(head, numbers, width, floor=head.cells())]

    # The calls: three rows live, two in the Esc frame (whose fourth row says
    # what changed). Oldest at the top, filled top-down; a row with no call yet
    # is the gutter alone, so the block's height never moves (D5). The call
    # open now — or the one the stop cut — keeps its row however many calls
    # finished after it (:func:`_keep`).
    slots = 2 if cancelled else _CALLS_SHOWN
    want = "running" if phase == "running" else "no_result" if phase == "stopped" else ""
    shown = _keep(items, slots, want)
    for item in shown:
        status = _status(item)
        mark, style = _CALL_MARKS[status]
        line = _Line().add("  ").add(mark, style).add(" ")
        idle = _idle_term(idle_ms, phase) if status == "running" else ""
        tail = f" · {idle}" if idle else " · no result" if status == "no_result" else ""
        room = avail - line.cells() - _cells(tail)
        if tail and room < _NUMBERS_FLOOR:
            tail, room = "", avail - line.cells()
        line.add(_call_text(item, room)).add(tail, _DIM)
        rows.append(_row(line, None, width))
    rows.extend(_gutter_only(width) for _ in range(slots - len(shown)))

    reply = getattr(snapshot, "last_text", None) if snapshot is not None else None
    if cancelled:
        files = _changed(result, avail - 2 - _cells("changed: "))
        line = _Line().add("  ")
        if files:
            line.add("changed: ", _DIM).add(files)
        else:
            line.add(_quoted(reply, avail - 2), _REPLY)
        rows.append(_row(line, None, width) if line.cells() > 2 else _gutter_only(width))
        hint = _Line().add("  ").add(_RESUME_HINT if _resumable(result) else _NO_RESUME_HINT, _DIM)
        rows.append(_row(hint, None, width) if hint.cells() <= avail else _gutter_only(width))
        return rows

    quoted = _quoted(reply, avail - 2)
    line = _Line().add("  ")
    if quoted:
        line.add(quoted, _REPLY)
    elif _live(phase) or phase == "queued":
        line.add("waiting for the first reply", _DIM)
    else:
        line.add("no reply", _DIM)
    rows.append(_row(line, None, width))
    return rows


# ── two or more: a header, then two rows per child or one (D4, D7) ────────


def _tail(
    line: _Line,
    room: int,
    *,
    marks: Sequence[object],
    phase: str,
    current: object | None,
    snapshot: SubagentProgress | None,
    idle_ms: int | None,
    waiting: str,
    cancelled: bool,
    result: object | None,
    quote: bool = True,
) -> None:
    """A member's outcome marks, then what it is doing now, in about ``room``
    cells — row 2 of a two-row member, the tail of a compact one (D4)."""

    used = line.cells()
    _marks(line, marks)
    if line.cells() > used:
        line.add(" ")
    _now(
        line,
        room - (line.cells() - used),
        phase=phase,
        current=current,
        snapshot=snapshot if quote else None,
        idle_ms=idle_ms,
        waiting=waiting,
        cancelled=cancelled,
        result=result,
    )


def _compact_column(labels: Sequence[str], *, avail: int, digits: int) -> int:
    """The label column of the one-row layout, shared by every row so the marks
    line up (D4): the widest label, but no more than half of what the row has
    after its glyph, index and elapsed time — and never less than 8 cells.

    FROM THE LABELS AND THE WIDTH ONLY, never from what the members are doing:
    a column sized from the tails would move every time a member started a
    call, and drag every label with it (the same lesson ADR-0227 records for the
    old label column). Half keeps labels that differ only at their end apart,
    since :func:`_fit_label` cuts in the middle.
    """

    widest = max(
        (_cells(_fit_flat(label, max(1, avail))) for label in labels if label), default=0
    )
    if not widest:
        return 0
    room = avail - (2 + digits + 1) - 6  # the glyph and index; an elapsed time
    return max(0, min(widest, max(8, room // 2)))


def _numbers_column(
    rows: Sequence[Sequence[_Term]], *, avail: int, label_cells: int
) -> tuple[int, int, frozenset[int]]:
    """``(now, held, ranks)``: how wide the numbers every two-row member's first
    row shares are NOW, how wide a label that is cut must leave them, and the
    ranks of the terms they show.

    ``held`` is the widest member's numbers AT THEIR RESERVES (:class:`_Term`),
    so a cut label keeps its cut while any member's elapsed time or tokens gain
    a digit (#132 review round 1) — while that still leaves the labels their
    whole width (``label_cells``, the widest) or the :data:`_MIDDLE_CUT_FROM`
    cells a middle cut needs: on a narrow terminal, telling the tasks apart
    comes before keeping their cut still, and ``held`` is then ``now``. Below
    that the ranks narrow — a per-row model first, then the tokens, the
    elapsed time last — until the labels keep :data:`_NUMBERS_FLOOR`. One set
    of ranks for every row, so no member shows a term its neighbours do not.

    THE RESERVE NEVER CUTS A LABEL THAT FITS (#132 review round 2). A label is
    laid out against ``now`` when it fits there whole and against ``held``
    otherwise (:func:`_label_room`): MEASURED live at 80 columns, the reserve
    cut ``[TAG:C] Write c-out.txt, wait four…seconds, then report.`` with nine
    blank cells beside it, at ``6s · 15``. A label that is cut is cut against
    ``held`` whatever its neighbours do, so the clock alone never re-cuts it;
    a whole one is cut at most once by the clock, when the numbers grow into
    it. A label IS re-cut when the column changes width for another reason —
    a member's first token count, a count that falls, members coming to agree
    on a model, a number past its reserve (ADR-0278 Limits).
    """

    reserved_floor = max(_NUMBERS_FLOOR, min(label_cells, _MIDDLE_CUT_FROM))
    kept = {term.rank for row in rows for term in row if term.text}
    while True:
        shown = [[term for term in row if term.text and term.rank in kept] for row in rows]
        now = max((_terms_cells(row, reserved=False) for row in shown if row), default=0)
        held = max((_terms_cells(row, reserved=True) for row in shown if row), default=0)
        if now == 0:
            return 0, 0, frozenset(kept)
        if avail - held - _GAP >= reserved_floor:
            return now, held, frozenset(kept)
        if avail - now - _GAP >= _NUMBERS_FLOOR:
            return now, now, frozenset(kept)
        kept.discard(min(kept))


def _label_room(label: str, avail: int, *, now: int, held: int) -> int:
    """The cells a two-row member's label gets: what the numbers leave as they
    are NOW when it fits there whole, else what they leave at their reserves
    (:func:`_numbers_column`)."""

    room_now = avail - (now + _GAP if now else 0)
    if _whole(label, room_now):
        return room_now
    return avail - (held + _GAP if held else 0)


def _batch_header(
    snapshots: Sequence[SubagentProgress | None],
    *,
    width: int,
    mode: str,
    profile: str,
    shared_model: str,
    idle_ms: Sequence[int | None],
    phases: Sequence[str],
    cancelled: bool,
    resumable: bool,
) -> str:
    total = len(snapshots)
    avail = max(0, width - _cells(_GUTTER))
    title = f"chain of {total}" if mode == "chain" else f"{total} agents"
    if cancelled:
        # The hint only when there is a session to resume (#132 review round
        # 1). Both phrases whole or not at all, the hint first to go, and a
        # hint that fits without the other phrase is shown (#132 review round
        # 2, :data:`_CANCELLED`).
        hint = " · ask to continue them" if resumable else " · no session to resume"

        def stopped(parts: frozenset[str]) -> _Line:
            word = f"stopped{_CANCELLED}" if "cancelled" in parts else "stopped"
            head = _Line().add(title).add(" · ").add(word, _YELLOW)
            return head.add(hint, _DIM) if "hint" in parts else head

        head, _ = _fit_header(stopped, {"cancelled": 2, "hint": 1}, (), avail)
        return _row(head, None, width)
    name = _flat(profile, _HEAD_FIELD_CELLS)
    model = _flat(shared_model, _HEAD_FIELD_CELLS)
    counts = " · ".join(_state_counts(snapshots))

    def build(parts: frozenset[str]) -> _Line:
        head = _Line().add(title)
        if name and "profile" in parts:
            head.add(" · ").add(name)
        if model and "model" in parts:
            # An agreeing batch's model, whole or not at all (D4).
            head.add(" · ").add(model)
        if counts:
            head.add(" · ").add(counts, _DIM)
        return head

    published = [
        (s, idle_ms[k] if k < len(idle_ms) else None, phases[k])
        for k, s in enumerate(snapshots)
        if s is not None
    ]
    terms: list[_Term] = []
    if published:
        # A batch's members run together, so the oldest one's age is the
        # call's; a chain's run one after another, so their ages add up — the
        # max of a chain read 9 s on a call 15 s old (measured, live check).
        ages = [_elapsed_now(s, idle, phase) for s, idle, phase in published]
        elapsed = sum(ages) if mode == "chain" else max(ages)
        tokens = max(_number(s.tokens) for s, _, _ in published)
        cost = sum(_cost(s.cost) for s, _, _ in published)
        terms = [
            _Term(_format_elapsed(elapsed), _ELAPSED_RANK, _NUMBER_RESERVE),
            _Term(f"{_compact_tokens(tokens)} tok" if tokens else "", 3, _TOKENS_RESERVE),
            _Term(f"${cost:.4f}" if cost else "", 1),
        ]
    # PRIORITY (#132 review rounds 1 and 2): the elapsed time first, then the
    # profile, the model, the tokens and the cost, each kept when it fits, and
    # the counts always — MEASURED before round 1, at 40 columns the header
    # kept ``worker`` and dropped the time.
    head, numbers = _fit_header(build, {"model": 4, "profile": 5}, terms, avail)
    return _row(head, numbers, width, floor=head.cells())


def _batch_block(
    snapshots: Sequence[SubagentProgress | None],
    *,
    tasks: Sequence[str],
    width: int,
    compact: bool,
    mode: str,
    profile: str,
    idle_ms: Sequence[int | None],
    cancelled: bool,
    results: Mapping[int, object],
) -> list[str]:
    total = len(snapshots)
    avail = max(0, width - _cells(_GUTTER))
    shared_model = _batch_model(snapshots)
    # When the members DISAGREE every row names its own (a header claiming one
    # model for a batch running two is worse than a repeated column) — in BOTH
    # layouts (#132 review round 1: the one-row layout named no model at all).
    per_row_model = len({s.model for s in snapshots if s is not None and s.model}) > 1
    items = [
        _activity(s, cancelled=cancelled) if s is not None else [] for s in snapshots
    ]
    phases = [
        _phase(s, items[k], cancelled=cancelled, result=results.get(k))
        for k, s in enumerate(snapshots)
    ]
    rows = [
        _batch_header(
            snapshots,
            width=width,
            mode=mode,
            profile=profile or _batch_profile(snapshots),
            shared_model=shared_model,
            idle_ms=idle_ms,
            phases=phases,
            cancelled=cancelled,
            resumable=any(_resumable(result) for result in results.values()),
        )
    ]
    digits = len(str(total))
    # Flattened ONCE per frame (a task can be 64 KiB), and once per group in
    # practice (:func:`_flat_label`'s cache): the label column, the numbers
    # column and each row's cut all read these.
    labels = [_flat_label(str(tasks[k]), mode) if k < len(tasks) else "" for k in range(total)]
    all_terms: list[list[_Term]] = []
    for k, snapshot in enumerate(snapshots):
        terms: list[_Term] = []
        if snapshot is not None:
            idle = idle_ms[k] if k < len(idle_ms) else None
            if per_row_model:
                terms.append(_Term(_flat(snapshot.model, _HEAD_FIELD_CELLS), 1))
            terms.append(
                _Term(
                    _format_elapsed(_elapsed_now(snapshot, idle, phases[k])), 3, _NUMBER_RESERVE
                )
            )
            tokens = _number(snapshot.tokens)
            if tokens and not cancelled:
                terms.append(_Term(_compact_tokens(tokens), 2, _NUMBER_RESERVE))
        all_terms.append(terms)
    indent = 2 + digits + 2
    column = _compact_column(labels, avail=avail, digits=digits) if compact else 0
    # The one-row layout's right side: the elapsed time, and a disagreeing
    # member's model before it while EVERY row still leaves its tail
    # :data:`_NUMBERS_FLOOR` cells — the model is the first term to go. One
    # decision for all rows, from widths that do not change from frame to frame.
    prefix = 2 + digits + 1 + (column + 2 if column else 0)
    compact_model = compact and per_row_model and all(
        prefix + _NUMBERS_FLOOR + _GAP + _terms_cells([t for t in terms if t.rank != 2], reserved=True)
        <= avail
        for terms in all_terms
        if terms
    )
    # ONE numbers column for every member, at the numbers' reserves, so a label
    # that is cut is cut the same way on every frame — not each time its own
    # row's elapsed time gains a digit, nor each time any row's does — and a
    # label that fits beside the numbers as they are now is not cut for the
    # reserve (#132 review round 2, :func:`_label_room`).
    numbers_now, numbers_held, kept_ranks = (
        (0, 0, frozenset())
        if compact
        else _numbers_column(
            all_terms,
            avail=avail - indent,
            label_cells=max((_cells(label) for label in labels if label), default=0),
        )
    )
    for k, snapshot in enumerate(snapshots):
        phase = phases[k]
        idle = idle_ms[k] if k < len(idle_ms) else None
        glyph, glyph_style = _STATE_GLYPHS[phase]
        label = labels[k]
        waiting = f"waiting for step {k}" if mode == "chain" and k > 0 else "waiting to start"
        marks_items, current = _split_current(items[k], phase)
        terms = all_terms[k]

        if compact:
            # ``● 1 <label…>  <marks> <now…>   [<model> · ]<elapsed>``, the
            # labels in one column (``column``, sized for every row at once).
            line = _Line().add(glyph, glyph_style).add(f" {k + 1:>{digits}} ")
            right_terms = [
                term
                for term in terms
                if term.rank == 3 or (term.rank == 1 and compact_model and term.text)
            ]
            numbers = _terms_line(right_terms)
            right = _terms_cells(right_terms, reserved=True) + _GAP if right_terms else 0
            if column:
                shown = _fit_flat(label, column) if label else ""
                line.add(shown).add(" " * (column - _cells(shown) + 2))
            _tail(
                line,
                avail - right - line.cells(),
                marks=marks_items,
                phase=phase,
                current=current,
                snapshot=snapshot,
                idle_ms=idle,
                waiting=waiting,
                cancelled=cancelled,
                result=results.get(k),
            )
            rows.append(_row(line, numbers, width, floor=_NUMBERS_FLOOR))
            continue

        first = _Line().add(glyph, glyph_style).add(f" {k + 1:>{digits}}  ")
        numbers = _terms_line([term for term in terms if term.text and term.rank in kept_ranks])
        if label:
            room = _label_room(label, avail - indent, now=numbers_now, held=numbers_held)
            first.add(_fit_flat(label, max(1, room)))
        rows.append(_row(first, numbers, width, floor=indent + _NUMBERS_FLOOR))

        second = _Line().add(" " * indent)
        _tail(
            second,
            avail - second.cells(),
            marks=marks_items,
            phase=phase,
            current=current,
            snapshot=snapshot,
            idle_ms=idle,
            waiting=waiting,
            cancelled=cancelled,
            result=results.get(k),
        )
        rows.append(_row(second, None, width))
    return rows


def panel_height(members: int, *, terminal_rows: int | None = None) -> int:
    """The panel's row count for a group of ``members`` (D4, D5).

    Decided ONCE, when the group opens, and every frame of that group has
    exactly this many rows — the Esc frame included — because a height change
    is what moved the input and status rows (``chrome.py``'s note on the
    reverted widget gating). One child: :data:`PANEL_SINGLE_ROWS`. Two to four:
    a header and two rows each, while that fits a third of the terminal (never
    less than five rows); otherwise, and from five members on, one row each.
    :data:`PANEL_MAX_ROWS` is the hard ceiling on every shape.
    """

    if members <= 1:
        return PANEL_SINGLE_ROWS
    rows = terminal_rows if terminal_rows is not None and terminal_rows > 0 else _ROWS_FALLBACK
    budget = max(PANEL_SINGLE_ROWS, rows // 3)
    two_row = 1 + 2 * members
    if members < _COMPACT_FROM and two_row <= budget:
        return two_row
    return min(1 + members, PANEL_MAX_ROWS)


def format_panel(
    snapshots: Sequence[SubagentProgress | None],
    *,
    tasks: Sequence[str] = (),
    width: int = _DEFAULT_WIDTH,
    height: int | None = None,
    mode: str = "parallel",
    resumed: bool = False,
    profile: str = "",
    idle_ms: Sequence[int | None] = (),
    cancelled: bool = False,
    results: Mapping[int, object] | None = None,
) -> list[str]:
    """The delegation panel (ADR-0278): exactly ``height`` rows, none wider than
    ``width`` cells — or ``[]`` for a group with no members.

    ONE CHILD (a single delegation, a resume, a one-task batch) is a five-row
    block: a header with its state, model and numbers, its last three calls with
    their outcomes, and its last reply. TWO OR MORE are a header with the batch's
    counts and one or two rows per member: the submitted task and its numbers,
    then the outcome marks of its recent calls and what it is doing now. The
    layouts are D4's and the mockups are in ADR-0278.

    ``width`` is the live terminal's (``tui.width.terminal_columns``, re-read by
    the bridge on every frame); ``height`` was fixed when the group opened
    (:func:`panel_height`, D5) and defaults to it. ``idle_ms[k]`` is how long
    ago member ``k``'s last snapshot arrived — the bridge stamps arrivals,
    because the runtime publishes after every stdout line and "no snapshot for
    7 s" IS "the child wrote nothing for 7 s" (D3). ``cancelled`` draws the Esc
    frame (D7) with each member's envelope from ``results`` (by index).

    EVERY CHILD-AUTHORED STRING IS FLATTENED ON ITS OWN before layout — the
    profile, the model, each call's name and argument, the last reply, the
    changed files — and so is the task label, which the PARENT model wrote. A row
    is measured in terminal cells as a whole and cut with ``…`` (:class:`_Line`),
    and the list is cut or padded to ``height``, so neither bound depends on
    the arithmetic above being right.
    """

    total = len(snapshots)
    if total < PANEL_MIN_CHILDREN:
        return []
    width = max(1, _number(width))
    rows_wanted = max(1, height if height is not None else panel_height(total))
    found = results or {}
    if total == 1:
        rows = _single_block(
            snapshots[0],
            width=width,
            profile=profile,
            resumed=resumed,
            idle_ms=idle_ms[0] if idle_ms else None,
            cancelled=cancelled,
            result=found.get(0),
        )
    else:
        rows = _batch_block(
            snapshots,
            tasks=tasks,
            width=width,
            compact=rows_wanted < 1 + 2 * total,
            mode=mode,
            profile=profile,
            idle_ms=idle_ms,
            cancelled=cancelled,
            results=found,
        )
    if len(rows) > rows_wanted:
        # Only reachable from a caller with more members than ``tool.py``
        # admits, or a height smaller than the shape needs — a bug either way.
        # A silently SHORTER panel reads as "those children were dropped", so
        # the remainder is named (the misreading ``_queued_line`` prevents).
        if rows_wanted == 1:
            return rows[:1]
        hidden = len(rows) - rows_wanted + 1
        more = _row(_Line().add(f"… {hidden} more", _DIM), None, width)
        return [*rows[: rows_wanted - 1], more]
    return rows + [_gutter_only(width)] * (rows_wanted - len(rows))


class PartialThrottle:
    """Rate-limits the tool-card EMIT for one ``agent`` call. Never the INGEST.

    THE ORDER IS THE WHOLE POINT (reviewer finding 19). :meth:`record` writes the
    snapshot into the table FIRST and unconditionally, and only then decides
    whether to emit. Gating the ingest instead would freeze a child's row for the
    whole time it waits on the provider — 30-60 s with no ``current_tool``
    change, because ``stream.py`` only touches ``current_tool`` at tool
    boundaries — while its three siblings updated around it. The dropped frames
    are frames; the dropped facts would be facts.

    :meth:`record` RETURNS the text instead of calling ``ctx.on_partial``
    itself. That keeps this module pure (no ``ToolExecutionContext``, no
    ``contextlib.suppress`` policy that belongs to the caller) and lets the
    tests assert on exactly which frames survive rather than on a mock.

    A frame is emitted when ANY of these holds:

    * it is the first one for this call;
    * the member's ``state`` changed — which covers every terminal frame, so the
      last thing the user sees is never a stale one;
    * the member's ``current_tool`` changed;
    * :data:`PARTIAL_MIN_INTERVAL_MS` has elapsed since the last emission.

    …and never when the rendered text is identical to the last emitted text.
    That final dedup mirrors the statusline half (``progress.py:858-860``): a
    frame that would repaint the same bytes is a kernel ``Task`` bought for
    nothing (H10), and it cannot lose information by construction.
    """

    __slots__ = (
        "_header",
        "_interval_ms",
        "_last_emit_s",
        "_last_text",
        "_now",
        "_snapshots",
    )

    def __init__(
        self,
        expected: int,
        *,
        header: str = "",
        now: Callable[[], float] = time.monotonic,
        interval_ms: int = PARTIAL_MIN_INTERVAL_MS,
    ) -> None:
        """:param expected: how many members the batch submitted — the length of
        the snapshot table, so members still parked on the semaphore render as
        ``queued`` from the first frame instead of appearing one by one.
        :param header: a line to carry ABOVE the table on every frame. Empty for
        every delegation that shipped before #196, which is what keeps
        :func:`format_card`'s byte-identical ``N == 1`` guarantee intact for a
        clean snapshot (the only one it covers since #178 — see
        :func:`_child_line`) — the
        header is prepended HERE rather than inside ``format_card`` precisely so
        that function stays a pure function of the snapshots and its pin does
        not have to learn about consent.

        It is on every frame rather than emitted once because ``on_partial``
        REPLACES the card, it does not append: a disclosure emitted as its own
        partial would be overwritten by the first progress frame, ~200ms later,
        and the user would be told once for a fifth of a second.
        :param now: monotonic seconds. Injectable so the tests pin the interval
        deterministically rather than sleeping.
        """

        self._header = header
        self._snapshots: list[SubagentProgress | None] = [None] * max(expected, 0)
        self._now = now
        self._interval_ms = interval_ms
        self._last_emit_s: float | None = None
        self._last_text: str | None = None

    @property
    def snapshots(self) -> tuple[SubagentProgress | None, ...]:
        """The live table — every snapshot ever recorded, dropped frames
        included. Read by the tests and by anything that wants the current card
        without an emit decision."""

        return tuple(self._snapshots)

    def card(self) -> str:
        """The card as it stands right now, with no throttling and no state
        change. For a caller that needs a final frame after the batch returns —
        and for the FIRST frame, before any child has produced a snapshot, which
        is how a header reaches the user before the work starts rather than
        after it."""

        return self._with_header(format_card(self._snapshots))

    def _with_header(self, text: str) -> str:
        if not self._header:
            return text
        return f"{self._header}\n{text}" if text else self._header

    def record(self, index: int, progress: SubagentProgress) -> str | None:
        """Ingest one member snapshot; return the card to emit, or ``None``.

        ``index`` is the member's SUBMITTED position, bound into the executor's
        per-member ``on_event`` closure at member creation (§3.6) — never
        inferred from the spawn id, which does not exist until ``_run`` mints it
        (``runtime.py:1410``).
        """

        if index < 0:
            return None
        while index >= len(self._snapshots):
            # The executor and this table disagreeing about the member count can
            # only be a bug, but the recovery that loses a child's only surface
            # is the worse one — grow, and render what arrived.
            self._snapshots.append(None)

        previous = self._snapshots[index]
        self._snapshots[index] = progress

        forced = (
            previous is None
            or previous.state != progress.state
            or previous.current_tool != progress.current_tool
        )
        now_s = self._now()
        if (
            not forced
            and self._last_emit_s is not None
            and (now_s - self._last_emit_s) * 1000 < self._interval_ms
        ):
            return None

        text = self._with_header(format_card(self._snapshots))
        if text == self._last_text:
            return None
        self._last_text = text
        self._last_emit_s = now_s
        return text


__all__ = [
    "AGGREGATE_MAX_CHARS",
    "PANEL_MAX_ROWS",
    "PANEL_MIN_CHILDREN",
    "PANEL_ROW_MAX_CHARS",
    "PANEL_SINGLE_ROWS",
    "PANEL_WIDGET_KEY",
    "PARTIAL_MIN_INTERVAL_MS",
    "PartialThrottle",
    "format_aggregate_status",
    "format_card",
    "format_panel",
    "panel_height",
]
