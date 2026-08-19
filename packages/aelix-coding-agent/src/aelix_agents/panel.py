"""The three delegation surfaces of decision S10, plus the ``on_partial``
throttle (ADR-0199 §S10, hazard H10).

PURE. No ``asyncio``, no UI handle, no process, and the only clock is the one
:class:`PartialThrottle` takes as an argument — so every layout below is
pinnable against a hand-built list of snapshots, and the throttle is pinnable
against a fake clock rather than a ``sleep``.

THE UNIT IS A LIST OF PER-CHILD SNAPSHOTS, INDEXED BY SUBMITTED POSITION.
``snapshots[k] is None`` means member ``k`` has published nothing yet — it is
parked on the batch semaphore (``batch.MAX_CONCURRENCY``) and has no spawn id at
all, because ``spawn_id = _new_id()`` is minted inside ``runtime._run``
(``runtime.py:799``). That is precisely why the UI group is opened with a COUNT
(``progress.SubagentProgressBridge.begin_group(key, expected=…)``) rather than
with ids: without the count there is nothing to render the ``queued`` term from.

WHY THREE SURFACES AND NOT ONE (S10):

1. :func:`format_aggregate_status` — ONE line for the whole batch.
   ``chrome._render_status`` ``"  ".join``s every registered segment into a
   FIXED height-1 row (``tui/chrome.py:1097-1108``, rendered at
   ``chrome.py:699``). Four 45-char per-child segments are ~186 characters and
   an 80-column terminal shows 1.7 of them, so per-child statusline rows are not
   a design that survives fan-out.
2. :func:`format_card` — N lines, one per child, for ``ctx.on_partial``. This is
   the PERMANENT record: it stays in the transcript after the turn ends. At
   ``N == 1`` its output is byte-identical to P2's ``_partial_text``.
3. :func:`format_panel` — the ``set_widget`` panel, and ONLY at ``N >= 2``
   (:data:`PANEL_MIN_CHILDREN`). Widgets render in their own
   ``Window(…, dont_extend_height=True)`` (``chrome.py:693``/``:698``) so they
   may be multi-row, but a single delegation must keep P2's behaviour exactly —
   and P2 had no panel.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.cells import cell_len

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from aelix_coding_agent.subagent_contract import SubagentProgress

PARTIAL_MIN_INTERVAL_MS = 500
"""Minimum wall time between two ``ctx.on_partial`` emissions FOR ONE CALL.

The kernel appends an ``asyncio.Task`` per ``on_partial`` into a list it never
prunes (``harness/loop.py:581`` declares ``update_events``, ``:608`` appends,
and it is drained only when the turn settles), and ``loop.py`` is KERNEL — it
may not be edited — so throttling here is the ONLY legal mitigation for H10.

500 ms is 5 repaints at the TUI's ``refresh_interval=0.1`` (``chrome.py:723``),
so every emission is guaranteed visible while 5 Hz is already past the rate a
human reads a changing row. It bounds a 10-minute child at ~1 200 interval
flushes PLUS the forced flushes on every ``current_tool`` transition (two per
child tool call) — call it ~2 000 per child, so the worst legal fan-out
(8 children × 10 min) holds on the order of 16 000 completed kernel Tasks
instead of an unbounded number. Today the runtime publishes after EVERY reduced
stdout line (``runtime.py:804-805``), which for a chatty child is hundreds per
turn."""

PANEL_MIN_CHILDREN = 2
"""Below this, no widget panel at all — S10's "a single delegation keeps today's
behaviour exactly"."""

PANEL_WIDGET_KEY = "aelix-agents:batch"
"""The ``set_widget`` slot the batch panel owns.

ONE key, not one per batch, and that is safe rather than lucky: ``agent``
declares ``execution_mode="sequential"`` (``tool.py:603``), which makes the
kernel run the whole tool batch sequentially (``loop.py:706-716``), so two
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
row (``chrome.py:1097-1108`` joins them with two spaces), so the last two
columns are the join, not content. It is not lower than that because S10's own
worked example —
``agent scout ×4 · 2 running · 1 done · 1 queued · 33s · 12.3k tok · $0.0093``
— is 74 characters and must render in full; a ceiling that silently amputated
the spec's example would be a ceiling chosen against the design. The comparison
that matters is with what surface 1 replaces: four per-child rows
(``format_status_row``) joined into the same height-1 row are ~186 characters,
of which an 80-column terminal shows 1.7."""

_MAX_PROFILE_CHARS = 16
"""Profile names come from filenames and are unbounded. Truncated for surface 1
ONLY — the tool card (surface 2) prints the name in full, because that is the
permanent transcript record and byte-identity with P2 is a requirement there."""

PANEL_ROW_MAX_CHARS = 78
"""Hard ceiling on ONE panel row, in terminal CELLS (finding F2, HIGH).

Same note as :data:`AGGREGATE_MAX_CHARS`: the NAME says chars, the unit is cells.

The same number and the same reason as :data:`AGGREGATE_MAX_CHARS`: 80 columns
less the two the surrounding join spends. The panel needed its own name rather
than sharing the constant because the two are bounded for different renderers —
if the widget window ever gains a border, this one moves and the statusline one
does not."""

PANEL_MAX_ROWS = 9
"""Hard ceiling on the panel's HEIGHT, in rows (finding F2, HIGH).

One header plus ``tool.MAX_PARALLEL_TASKS`` (= 8) member rows, which is every
legal batch — the parser refuses a ninth task outright (``tool.py:392-398``), so
this never fires on input a model can actually get past the door. Spelled here
rather than imported so this module keeps its "no aelix_agents imports" shape,
and checked anyway because the widget is the one surface with NO downstream
bound: ``chrome._render_widget_lines`` joins the list with ``\\n`` and ANSI-parses
it into a ``Window(…, dont_extend_height=True)`` (``chrome.py:1167``/``:693``),
which is multi-row and uncapped, so an over-long list pushes the transcript and
the input line off screen. Contrast ``chrome._render_status``, which strips
newlines because "this is a fixed height=1 chrome row" (``chrome.py:1097-1099``) —
the statusline was defended at the far end and the widget is not."""

_ELLIPSIS = "…"

_ZERO_WIDTH_SLACK = 4
"""Codepoints per cell :func:`_flatten` carries before it stops measuring and
slices. Only zero-width input can reach it — one visible codepoint is at least
one cell — so it bounds the pathological case without taking the ellipsis away
from the ordinary one."""

_MAX_INPUT_CHARS = 8192
"""Ceiling on what :func:`_flatten` will even LOOK at, before the two width caps.

The whitespace collapse and the ``translate`` are both linear in the input, and
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
what make the OUTPUT safe."""

_CONTROL_KILL = dict.fromkeys(
    [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)]
)
"""``str.translate`` table deleting C0, DEL and C1 — the same set
``consent._CONTROL_CHARS`` refuses, restated rather than imported because
``consent`` is the module that prompts and this one may not depend on it.

C1 is in the set because ``\\x9b`` IS a CSI, i.e. the one-byte spelling of
``\\x1b[``."""


def _flatten(value: str, *, limit: int) -> str:
    """One row of untrusted text: no newlines, no escapes, bounded width. (F2)

    EVERY string this module renders into the widget is child-authored.
    ``SubagentProgress.current_tool`` is set from the CHILD process's own stdout
    JSON — any non-empty ``str`` in ``tool_execution_start.tool_name``
    (``stream.py:671-673``) — and the kernel emits that event with the raw
    model-supplied name BEFORE ``_prepare_tool_call`` looks the tool up
    (``loop.py:734-744``), so it is not constrained to a real tool name. A child
    is exactly the process this phase's threat model assumes has read attacker
    controlled content (``consent._may_widen`` constraint 6). Demonstrated: 40
    newlines in ``current_tool`` turned a 3-entry panel into 43 screen rows with
    raw ESC intact.

    ``profile`` gets the same treatment: it comes from a filename, and a filename
    may contain ``\\n`` too.

    The order matches ``consent._sanitize_field``: collapse whitespace (which is
    what bounds the ROW COUNT), then delete controls (which is what bounds what
    the terminal will obey), then bound the width — measured on the flattened
    string, so the limit counts what is drawn.

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
    translate are linear in the input, and this runs on every publish for every
    member of a batch.
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
    flat = " ".join(value.split()).translate(_CONTROL_KILL)
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
    # :data:`PANEL_ROW_MAX_CHARS` is the same overhang the closing clamp in
    # :func:`_composed_row` exists to catch, and this is the half that promises it.
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
    Nothing else in this module noticed, because the closing clamp in
    :func:`_composed_row` calls the same function.

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
    """

    if cells <= 0:
        return ""
    if cell_len(text) <= cells:
        return text
    out: list[str] = []
    used = 0
    for char in text:
        width = cell_len(char)
        if used + width > cells:
            break
        out.append(char)
        used += width
    while out and cell_len("".join(out)) > cells:
        out.pop()
    return "".join(out)


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
    """Compact token count for surfaces 1 and 3.

    Mirrors ``progress._format_tokens`` (``progress.py:158-161``) rather than
    importing it — the same call ``aggregate._format_count`` makes
    (``aggregate.py:145-155``) and for the same reason: these are three
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

    :func:`format_aggregate_status` is ALSO the panel's header line, and the
    panel is not newline-stripped downstream the way the statusline is
    (``chrome.py:1097-1099``). A profile name of 16 characters containing newlines
    would pass the length cap and still add rows to the widget, so the same
    helper that bounds every panel row bounds this too.
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
    :func:`_model_term`, which sizes the model against them. It was briefly called
    from :func:`format_panel` as well, to decide whether the rows should suppress
    the model; that question turned out not to need an answer — see the comment
    there — but this stayed split, because "the head and the counts" is one idea
    and the width rule reads better naming it.
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

12 is the same number :data:`_NUMBERS_MIN_CELLS` uses and for the same reason: it
is the width at which a truncated string still identifies what it came from.
MEASURED, ``_flatten`` at limit 12 gives ``github-copi…`` and ``anthropic/c…`` —
an earlier draft of this line offered ``github-copil…``, which is 13 cells and is
therefore a string this constant can never produce.

THE FALLBACK BELOW IT IS THE ROWS, AND HOW GOOD IT IS DEPENDS ON THE LABELS.
A draft of this line called it a "real fallback" without qualification and a later
one denied it existed; both were reading one band as the whole picture. MEASURED
against a 28-cell provider-scoped id: at five count classes on an 80-column screen
the rows carry it nearly whole (``running · github-copilot/gpt-5.…``), while at
three classes on a 50-column one with 32-cell labels they manage ``running · g…``
and the tool name is what paid for it. The header's remaining room beats a row's
from 54 columns up, and below 52 a row has two cells.

Dropping the term here therefore hands the question to :func:`format_panel`, not
to nobody — see the comment there for why the rows take it."""


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
    segment still costs the two-space join at ``chrome.py:1108``.

    NUMBERS. ``elapsed`` is the MAX over live members — they start together, so
    the oldest one is the batch's age; the executor's own measured wall clock is
    the number that reaches the final ``ToolResult`` (§3.4) and it does not
    exist yet while the batch runs. ``tokens`` is also a MAX and deliberately
    NOT a sum: ``SubagentUsage.tokens`` is documented as a context LEVEL, "last
    message wins" (``subagent_contract.py:95-96``, and ``stream.py:214-217``
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

    WHEN IT IS OMITTED THE PANEL'S ROWS TAKE IT BACK, at whatever width the label
    column leaves them — see :func:`format_panel`. Two drafts of this paragraph
    described that as free and then as impossible; it is neither, and the numbers
    are next to the decision rather than here, because this function is also the
    statusline and the statusline has no rows.

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

    ``extra_head`` appends one term to the head, and ONLY :func:`format_panel`
    passes it — the panel states the batch model there because it took that
    column away from the rows. The default is empty, so the STATUSLINE row this
    same function produces is byte-identical to what it always was: there are
    four characters of headroom under :data:`AGGREGATE_MAX_CHARS` at S10's own
    worked example and a model term does not fit in them. Being a parameter
    rather than a second function is what keeps the two surfaces unable to state
    a different fact, and the statusline — which has no rows and where the counts
    are the whole substance — passes no ``extra_head`` and is untouched by this.
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
    """P2's ``extension._partial_text``, VERBATIM.

    The single-child tool card must be byte-identical to what P2 wrote, which is
    what keeps the shipped single-delegation transcript unchanged under S2. Any
    edit here is a visible change to a surface no P3 decision authorised.
    """

    tool = f" · {progress.current_tool}" if progress.current_tool else ""
    return (
        f"agent {progress.profile} [{progress.state}]{tool} · "
        f"{progress.elapsed_ms / 1000:.0f}s"
    )


def _queued_line(profile: str) -> str:
    """A member that is parked on the semaphore and has no snapshot at all.

    It is named rather than omitted: a card that silently shows 3 of 8 rows
    reads as "5 tasks were dropped", and the model reads this card too.
    """

    return f"agent {profile} [queued]" if profile else "[queued]"


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

    At ``N == 1`` the output is P2's, byte for byte, with NO index prefix: the
    single-delegation transcript is not something this phase is allowed to
    change. At ``N >= 2`` every line carries ``[k/N]`` in SUBMITTED order —
    the order the model wrote the tasks in and the order ``aggregate`` renders
    the results in (§3.4: "never completion order").
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
    rows carry their own model again, because a header claiming one model for a
    batch running two is worse than a repeated column.
    """

    models = {s.model for s in snapshots if s is not None and s.model}
    return next(iter(models)) if len(models) == 1 else ""


_PANEL_DIM = "\x1b[2m"
_PANEL_RST = "\x1b[0m"
_GUTTER = "▌ "
"""The panel's left edge, dim. Two visible cells, budgeted for below.

``chrome._render_widget_lines`` ANSI-parses the joined lines
(``chrome.py:1163-1167``), so raw SGR is the module's own vocabulary here — the
same raw-escape convention ``tui/context.py`` uses — and no import is needed,
which keeps this module's declared purity.

APPLIED AFTER :func:`_flatten`, NEVER BEFORE. ``_flatten`` deletes C0, which
includes ESC, so an escape handed to it is silently stripped; and it collapses
whitespace, which would eat the column padding. Every untrusted PART is
flattened on its own and the row is assembled from the results, so the width
bound is arithmetic rather than a final pass — with a cell-accurate clamp at the
end as the backstop, because arithmetic is a thing one gets wrong."""

_TASK_MIN_CELLS = 8
"""Never squeeze the job label below this. A label cut to two characters is a
column that costs width and says nothing."""

_NUMBERS_MIN_CELLS = 12
"""What the job-label column must leave for the numbers half of a row.

The label column is a FIXED cost paid BEFORE the numbers, so it is the thing that
decides whether the state is visible at all. On the ``tasks`` path the numbers
begin with the state word — ``_panel_row`` is called with ``state_first`` there,
which is what makes the state the last thing this reserve can lose. MEASURED at
limit 12, ``starting · 33s`` renders as ``starting · …``: the widest state word
is 8 cells and 12 holds it, the separator and the ellipsis. NOTHING of the next
term survives, and an earlier draft of this paragraph claimed it did.

A RESERVE RATHER THAN A CEILING, and that replaces a constant cap of 24 cells
that was measured on the wrong axis. The cap was chosen at a 40-column terminal,
where it let 2 of 4 rows keep a full state word against 0 of 4 at 28 and above.
What was never measured is what it cost everywhere else: a batch fanned out over
one verb — ``port the retry backoff to google`` / ``… to openai`` / ``… to
azure`` / ``… to vertex``, 32/32/31/32 cells — truncates to a common 24-cell
prefix, so all four rows render the SAME string and the panel stops answering the
question it was added to answer. MEASURED on that batch: 1 distinct label at the
cap, 4 without it.

WHAT REMOVING THE CAP COSTS, stated rather than claimed away. It is not free
space that is recovered — MEASURED on that same batch at 80 columns, the capped
build's rows were 78/78/78/40 cells, not rows with room to spare. The cells the
elision saved were spent on the numbers, so removing it spends them back: with a
49-cell label the numbers half is 19 cells and a row reads
``running · read · 1…`` where the capped build read
``running · read · 12s · 1.5k tok · $0.0600``. Per-member cost and the tail of
the elapsed are the price of four labels a user can tell apart, which is the
trade this phase was asked to make; the batch's cost is still in the header
whenever the aggregate's own budget reaches it.

The batch model moved off the rows only WHEN THE MEMBERS AGREE — ``_batch_model``
returns ``""`` when two children report different strings, and then the rows
carry it again (``_panel_row`` says so in its own comment). It is worth
31 cells there, not the 28 an earlier draft of this paragraph claimed.

The 40-column terminal still loses the tail of the numbers, which is the
degradation ``_composed_row`` documents and left-packing exists to bound. The cap
did not fix that case either — it bought a partial word on half the rows and paid
for it at every width."""


def _panel_row(
    snapshot: SubagentProgress | None,
    *,
    hide_model: bool = False,
    state_first: bool = False,
) -> str:
    if snapshot is None:
        return "queued"
    # ``state`` is a product-core literal (``subagent_contract.py:66``) and the
    # numbers are numbers; ``current_tool``, ``model`` AND ``task`` are strings
    # this module does not author — the first two are the CHILD's own bytes, read
    # off its stdout, and the third is the PARENT model's tool-call argument — so
    # all three go through :func:`_flatten`.
    #
    # THIS FUNCTION NO LONGER RENDERS A WHOLE ROW when the panel has job labels:
    # :func:`format_panel` routes those through :func:`_composed_row`, which lays
    # the label out in its own column and uses what this returns as the numbers
    # half. The name is kept because the no-tasks path — every existing caller,
    # and N == 1 — is byte-identical to what it always produced.
    #
    # ``model`` is dropped when the batch agrees on one, because a column
    # identical on every row identifies nothing; :func:`_batch_model` states it
    # once in the header instead. When the members DISAGREE ``hide_model`` is
    # False and the per-row term comes back, since a header claiming one model
    # for a batch running two is worse than a repeated column.
    #
    # STATE FIRST ONLY WHEN THERE IS A LABEL COLUMN AHEAD OF IT. Main puts the
    # model first (S10 "Option A") and that costs nothing there, because the whole
    # 78 cells belong to this string. With a job-label column the numbers half gets
    # only what the label leaves, and a 28-cell provider-scoped id spends that
    # remainder before the state word is ever reached — MEASURED with members
    # DISAGREEING (so the model cannot leave the rows) and 35-cell labels, 2 of 4
    # rows showed their state at 78, 80, 100 AND 120 columns alike, flat, because
    # the budget is fixed and a wider terminal does not help. State-first is 4 of 4
    # at every one of them.
    #
    # Scoped to the tasks path so the no-tasks rows stay byte-identical to main,
    # which is a contract this module states twice and a test pins. Measured on
    # that path, model-first costs nothing: main and HEAD are the same row counts
    # at every width, because there is no column competing for the budget.
    parts: list[str] = []
    if state_first:
        parts.append(snapshot.state)
    if snapshot.model and not hide_model:
        parts.append(_flatten(snapshot.model, limit=PANEL_ROW_MAX_CHARS))
    if not state_first:
        parts.append(snapshot.state)
    if snapshot.current_tool:
        parts.append(_flatten(snapshot.current_tool, limit=PANEL_ROW_MAX_CHARS))
    parts.append(f"{snapshot.elapsed_ms / 1000:.0f}s")
    if snapshot.tokens:
        parts.append(_format_tokens(snapshot.tokens))
    if snapshot.cost:
        parts.append(f"${snapshot.cost:.4f}")
    return " · ".join(parts)


def _label_column(tasks: Sequence[str], room: int) -> int:
    """Cells the job-label column occupies, shared by every row of one panel.

    From the LABELS, never from the numbers, and that is the whole design. The
    labels are fixed for the life of a group — they are the submitted tasks — so a
    column sized from them is stable, while one sized from the numbers moves every
    time a member's elapsed time gains a digit and drags every other row's label
    with it.

    Sizing from the numbers also made the label budget shrink as a child got
    busier: four distinct tasks collapsed to a common prefix at exactly the moment
    work started and the user needed to tell the rows apart.

    The only ceiling is :data:`_NUMBERS_MIN_CELLS` — the room the numbers keep —
    because a ceiling that bites BELOW the labels a batch really submits causes
    that same collapse permanently instead of only while a child is busy.
    """

    widest = max((cell_len(_flatten(t, limit=room)) for t in tasks), default=0)
    reserved = max(_TASK_MIN_CELLS, room - _NUMBERS_MIN_CELLS - 2)
    return max(_TASK_MIN_CELLS, min(widest, reserved, room))


def _composed_row(
    index: int, total: int, task: str, numbers: str, column: int, budget: int
) -> str:
    """``▌ [k/N] <job label>   <state · tool · numbers>`` in one bounded row.

    LEFT-PACKED, NOT PADDED TO THE FULL BUDGET, and that is a correction. The
    first version right-aligned the numbers against
    :data:`PANEL_ROW_MAX_CHARS`, which made EVERY row exactly 78 cells — measured
    over 20 000 composed rows, the observed width set was literally ``[78]``. The
    widget window is ``Window(..., dont_extend_height=True)`` with ``wrap_lines``
    left False (``chrome.py:693``), so a row wider than the terminal is CLIPPED,
    not wrapped, and the clip takes the right-hand end. Measured through the real
    chrome at 72 columns, a queued member rendered as ``▌ [4/4] write the ADR``
    with the word ``queued`` gone entirely, while main's left-packed rows read
    ``[4/4] queued`` intact all the way down to 30. ``tui/width.py`` records this
    exact failure once already and says "never return more than the terminal
    has"; there is deliberately no minimum-width floor anywhere under ``tui/``,
    so a sub-78-column terminal is a supported configuration.

    So the label column is padded — that is what keeps the numbers aligned across
    rows — and the row then ENDS. A narrow terminal now loses the tail of the
    numbers, the same degradation main had, instead of losing all of them.

    The width bound is arithmetic: each untrusted part is :func:`_flatten`ed to
    its own share and the row is assembled from the results, because a final
    flatten would collapse the padding this layout is made of. The closing clamp
    is the backstop for that arithmetic, and it counts cells rather than slicing.
    """

    prefix = f"[{index + 1}/{total}] "
    room = max(0, budget - cell_len(_GUTTER) - cell_len(prefix))
    label = _flatten(task, limit=max(_TASK_MIN_CELLS, min(column, room)))
    numbers = _flatten(numbers, limit=max(0, room - column - 2))
    pad = " " * max(1, column - cell_len(label) + 2)
    body = f"{prefix}{label}{pad}{_PANEL_DIM}{numbers}{_PANEL_RST}"
    visible = cell_len(prefix) + cell_len(label) + len(pad) + cell_len(numbers)
    if visible > budget - cell_len(_GUTTER):
        body = _cut_to_cells(
            f"{prefix}{label}{pad}{numbers}", max(0, budget - cell_len(_GUTTER))
        )
    return f"{_PANEL_DIM}{_GUTTER}{_PANEL_RST}{body}"


def format_panel(
    snapshots: Sequence[SubagentProgress | None],
    *,
    tasks: Sequence[str] = (),
    width: int = PANEL_ROW_MAX_CHARS,
) -> list[str]:
    """S10 surface 3 — the widget panel, or ``[]`` below :data:`PANEL_MIN_CHILDREN`.

    The header is :func:`format_aggregate_status` PLUS the batch's one model, and
    the rows carry each member's ``tasks`` entry where the model used to be.
    Without that the panel answers "how many are running" and never "which one is
    doing what": the profile is one per call, the model is one per call, and the
    submitted task was the only differing field — and it was not plumbed here.

    THE MODEL MOVED TO THE HEADER RATHER THAN BEING DROPPED. It cannot go into
    :func:`format_aggregate_status` itself: that string is ALSO the shared
    height-1 statusline row, bounded by :data:`AGGREGATE_MAX_CHARS`, and S10's
    own worked example is already 74 of those 78 characters. So the panel builds
    its own header line from the same parts, one model term longer, and the
    statusline is untouched — the panel and the statusline still cannot state a
    DIFFERENT fact, which is what "never disagree" was protecting.

    ``tasks`` is index-aligned and optional: an empty one renders exactly what
    this function rendered before, which is what every existing caller and the
    N == 1 path get.

    The rows drop the profile name — it is in the header and it is the
    same for every member (S3) — and spend the width on per-child state instead.

    Returning ``[]`` is the caller's signal to write nothing; ``set_widget`` with
    an empty list would still allocate the window.

    BOUNDED IN BOTH DIMENSIONS, AND THAT IS SECURITY, NOT LAYOUT (F2, HIGH).
    Every row is :func:`_flatten`ed to ``width`` and the list is bounded to
    :data:`PANEL_MAX_ROWS`, because the far end applies neither:
    ``chrome._render_widget_lines`` ANSI-parses ``"\\n".join(lines)`` into a
    window with no height cap. The width bound is applied to the WHOLE row, after
    the ``[k/N] `` prefix, so the number this function promises is the number of
    columns it uses.

    ``width`` IS THE TERMINAL'S, CAPPED AT :data:`PANEL_ROW_MAX_CHARS`, and the
    caller supplies it — this module cannot see a terminal and does not import
    ``tui/width.py``. It only ever narrows: the panel stays a 78-cell ribbon on a
    200-column screen, deliberately, because a batch panel is a summary and not a
    table. What it fixes is the other end. The job-label column was sized against
    the fixed 78 while the window paints into whatever the terminal has, so at 30
    to 60 columns a long label took cells the terminal did not have and the state
    word went with them. MEASURED against ``main`` on a pyte screen, member rows
    still showing a state word, 35-cell labels: main 1/4 1/4 4/4 4/4 at 30/40/50/60
    columns against 0/4 0/4 2/4 4/4 here — a regression this branch introduced and
    that no constant can fix, because the right number is the terminal's.

    A RESIZE BETWEEN PUBLISHES LEAVES THE LAST WIDTH IN PLACE, and that is
    acceptable rather than ignored: the panel is only on screen while a batch is
    running, and the runtime republishes on every reduced stdout line from every
    member, so a stale width lives for one frame. Worse, it degrades to exactly
    what this parameter replaced — rows measured against 78 and clipped by the
    window — which is the behaviour shipped for the whole of ADR-0199.
    """

    total = len(snapshots)
    if total < PANEL_MIN_CHILDREN:
        return []
    budget = min(width, PANEL_ROW_MAX_CHARS)
    model = _batch_model(snapshots) if tasks else ""
    column = _label_column(
        tasks, max(0, budget - cell_len(_GUTTER) - len(f"[{total}/{total}] "))
    ) if tasks else 0
    # The gutter costs two cells the aggregate does not know about, so the panel
    # header is budgeted two narrower. Passed as a CAP rather than clamped after,
    # so the existing "drop a whole trailing number" logic does the shortening
    # instead of a cut landing mid-``$0.00…``.
    header_cap = min(budget, AGGREGATE_MAX_CHARS) - (cell_len(_GUTTER) if tasks else 0)
    header = format_aggregate_status(snapshots, extra_head=model, cap=header_cap)
    # THE ROWS HIDE THE MODEL ONLY IF THE HEADER COULD AFFORD IT, decided by the
    # SAME call the header made rather than by searching the finished string for
    # the model — a model shown truncated is present under one spelling and absent
    # under another, and a substring test reads that as "the header did not show
    # it" and lets both surfaces spend the width.
    #
    # SO WHEN THE HEADER DROPS IT, EVERY ROW CARRIES IT. That costs the tail of
    # the numbers, and how much it costs depends on the LABEL column rather than
    # on the terminal, which is why it is worth writing down:
    #
    #     80 cols, 5 count classes, 37-cell labels
    #         rows carry it   ``running · github-copilot/gpt-5.…``
    #         rows drop it    ``running · read_file · 33s · 12.…``
    #     62 cols, 3 count classes, 32-cell labels
    #         rows carry it   ``running · github-co…``
    #         rows drop it    ``running · read_file…``
    #     50 cols, same
    #         rows carry it   ``running · g…``
    #         rows drop it    ``running · r…``
    #
    # The header drops the model whenever the state counts leave it under
    # :data:`_MODEL_MIN_CELLS`, which happens at FIVE count classes on a 200-column
    # screen just as it does at three on a 50-column one — so this is not only a
    # narrow-terminal path. What the rows can then do with it is decided by the
    # LABEL COLUMN and not by the terminal: with 37-cell labels the model comes
    # back nearly whole at 80 columns, and with this file's 49-cell ones no width
    # below 76 lets a row show even eight cells of it. So "the rows carry it" is a
    # fallback that sometimes reads well and sometimes delivers nothing — never
    # WORSE than dropping it, which is the whole case for taking it.
    #
    # Both arms were rendered for the owner, who chose the model: it is a fact a
    # reader cannot get anywhere else on this surface, while the tool name churns
    # every few seconds and returns.
    #
    # The rows also carry a model when the members DISAGREE — then it is the only
    # thing telling two rows apart, and ``_batch_model`` returns ``""``, so
    # ``model`` is falsy, ``_model_term`` returns ``""`` and this is False.
    hide_model = bool(_model_term(*_head_and_counts(snapshots), model, header_cap))
    lines: list[str] = []
    if header:
        lines.append(f"{_PANEL_DIM}{_GUTTER}{_PANEL_RST}{header}" if tasks else header)
    for index, snapshot in enumerate(snapshots):
        task = tasks[index] if index < len(tasks) else ""
        numbers = _panel_row(snapshot, hide_model=hide_model, state_first=bool(task))
        if not task:
            # Byte-for-byte what this function rendered before ``tasks`` existed.
            lines.append(_flatten(f"[{index + 1}/{total}] {numbers}", limit=budget))
            continue
        lines.append(_composed_row(index, total, task, numbers, column, budget))

    if len(lines) > PANEL_MAX_ROWS:
        # Only reachable from a caller that submitted more members than
        # ``tool.py`` admits, i.e. a bug — but a silently SHORTER panel reads as
        # "those children were dropped", which is the same misreading
        # :func:`_queued_line` exists to prevent. Name the remainder instead.
        hidden = len(lines) - PANEL_MAX_ROWS + 1
        lines = [*lines[: PANEL_MAX_ROWS - 1], f"… {hidden} more"]
    return lines


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
    That final dedup mirrors the statusline half (``progress.py:496-498``): a
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
        :func:`format_card`'s byte-identical ``N == 1`` guarantee intact — the
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
        (``runtime.py:799``).
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
    "PANEL_WIDGET_KEY",
    "PARTIAL_MIN_INTERVAL_MS",
    "PartialThrottle",
    "format_aggregate_status",
    "format_card",
    "format_panel",
]
