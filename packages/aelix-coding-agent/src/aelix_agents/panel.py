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

from rich.cells import cell_len, set_cell_size

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
kernel run the whole tool batch sequentially (``loop.py:699-709``), so two
``agent`` calls never have panels open at the same time. ``progress.py`` still
tracks which group last wrote the slot, so an end_group for a group that does
not own it cannot blank someone else's panel."""

AGGREGATE_MAX_CHARS = 78
"""Hard ceiling on surface 1, in characters.

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
"""Hard ceiling on ONE panel row, in characters (finding F2, HIGH).

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
    """

    flat = " ".join(value.split()).translate(_CONTROL_KILL)
    if cell_len(flat) <= limit:
        return flat
    return set_cell_size(flat, max(0, limit - 1)) + _ELLIPSIS

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

    Mirrors ``progress._format_tokens`` (``progress.py:157-160``) rather than
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

    WIDTH. The head and the state counts are the substance and are never
    dropped; the trailing numbers are appended only while they fit inside
    :data:`AGGREGATE_MAX_CHARS`, right to left. The final hard truncation is
    reachable only by a pathological count set (five non-zero classes at a wide
    profile name) and exists so this function can promise a bound rather than
    hope for one.

    ``extra_head`` appends one term to the head, and ONLY :func:`format_panel`
    passes it — the panel states the batch model there because it took that
    column away from the rows. The default is empty, so the STATUSLINE row this
    same function produces is byte-identical to what it always was: there are
    four characters of headroom under :data:`AGGREGATE_MAX_CHARS` at S10's own
    worked example and a model term does not fit in them. Being a parameter
    rather than a second function is what keeps the two surfaces unable to state
    a different fact.
    """

    total = len(snapshots)
    live = [snapshot for snapshot in snapshots if snapshot is not None]
    if total == 0 or not live:
        return ""

    head = f"agent {_short_profile(live[0].profile)} ×{total}"
    if extra_head:
        head += f" · {_flatten(extra_head, limit=_MAX_PROFILE_CHARS * 2)}"
    text = " · ".join([head, *_state_counts(snapshots)])

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
        return set_cell_size(text, cap - 1) + _ELLIPSIS
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
"""Never squeeze the job label below this, even if the numbers are wide. A label
cut to two characters is a column that costs width and says nothing."""


def _panel_row(
    snapshot: SubagentProgress | None,
    *,
    task: str = "",
    hide_model: bool = False,
) -> str:
    if snapshot is None:
        return f"{_flatten(task, limit=PANEL_ROW_MAX_CHARS)} · queued" if task else "queued"
    # ``state`` is a product-core literal (``subagent_contract.py:66``) and the
    # numbers are numbers; ``current_tool``, ``model`` AND ``task`` are strings
    # this module does not author — the first two are the CHILD's own bytes, read
    # off its stdout, and the third is the PARENT model's tool-call argument — so
    # all three go through :func:`_flatten`.
    #
    # ``task`` takes the place ``model`` used to lead from, and the reason is the
    # reason the old comment gave for putting ``model`` there: "it is the same
    # for every member of a one-profile batch, so the column is naturally
    # uniform-width". A column identical on every row identifies nothing, which
    # is precisely what the owner asked the panel to show. The model is not lost
    # — :func:`_batch_model` states it once in the header — and when the members
    # DISAGREE ``hide_model`` is False and the old per-row term comes back.
    parts: list[str] = []
    if task:
        parts.append(_flatten(task, limit=PANEL_ROW_MAX_CHARS))
    if snapshot.model and not hide_model:
        parts.append(_flatten(snapshot.model, limit=PANEL_ROW_MAX_CHARS))
    parts.append(snapshot.state)
    if snapshot.current_tool:
        parts.append(_flatten(snapshot.current_tool, limit=PANEL_ROW_MAX_CHARS))
    parts.append(f"{snapshot.elapsed_ms / 1000:.0f}s")
    if snapshot.tokens:
        parts.append(_format_tokens(snapshot.tokens))
    if snapshot.cost:
        parts.append(f"${snapshot.cost:.4f}")
    return " · ".join(parts)


def _composed_row(index: int, total: int, task: str, numbers: str) -> str:
    """``▌ [k/N] <job label>      <state · tool · numbers>`` in one bounded row.

    The job label is left-aligned from a FIXED column (the index prefix never
    changes width within a batch) and the numbers are flush RIGHT against the row
    budget, so only the whitespace between them moves as a child progresses.
    Aligning the numbers to the widest row instead would make every OTHER row's
    label jump whenever one member's elapsed time gained a digit.

    The width bound is arithmetic — each untrusted part is :func:`_flatten`ed to
    its own share, then the row is assembled from the results, because a final
    flatten would collapse the padding this layout is made of. The closing clamp
    is the backstop for that arithmetic; it uses ``set_cell_size`` rather than a
    slice so it, too, counts what the terminal draws.
    """

    prefix = f"[{index + 1}/{total}] "
    room = PANEL_ROW_MAX_CHARS - cell_len(_GUTTER) - cell_len(prefix)
    numbers = _flatten(numbers, limit=max(0, room - _TASK_MIN_CELLS - 2))
    label = _flatten(task, limit=max(_TASK_MIN_CELLS, room - cell_len(numbers) - 2))
    gap = max(1, room - cell_len(label) - cell_len(numbers))
    body = f"{prefix}{label}{' ' * gap}{_PANEL_DIM}{numbers}{_PANEL_RST}"
    visible = cell_len(prefix) + cell_len(label) + gap + cell_len(numbers)
    if visible > PANEL_ROW_MAX_CHARS - cell_len(_GUTTER):
        body = set_cell_size(
            f"{prefix}{label}{' ' * gap}{numbers}",
            PANEL_ROW_MAX_CHARS - cell_len(_GUTTER),
        )
    return f"{_PANEL_DIM}{_GUTTER}{_PANEL_RST}{body}"


def format_panel(
    snapshots: Sequence[SubagentProgress | None],
    *,
    tasks: Sequence[str] = (),
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
    Every row is :func:`_flatten`ed to :data:`PANEL_ROW_MAX_CHARS` and the list
    is bounded to :data:`PANEL_MAX_ROWS`, because the far end applies neither:
    ``chrome._render_widget_lines`` ANSI-parses ``"\\n".join(lines)`` into a
    window with no height cap. The width bound is applied to the WHOLE row, after
    the ``[k/N] `` prefix, so the number this function promises is the number of
    columns it uses.
    """

    total = len(snapshots)
    if total < PANEL_MIN_CHILDREN:
        return []
    model = _batch_model(snapshots) if tasks else ""
    # The gutter costs two cells the aggregate does not know about, so the panel
    # header is budgeted two narrower. Passed as a CAP rather than clamped after,
    # so the existing "drop a whole trailing number" logic does the shortening
    # instead of a cut landing mid-``$0.00…``.
    header = format_aggregate_status(
        snapshots,
        extra_head=model,
        cap=AGGREGATE_MAX_CHARS - (cell_len(_GUTTER) if tasks else 0),
    )
    lines: list[str] = []
    if header:
        lines.append(f"{_PANEL_DIM}{_GUTTER}{_PANEL_RST}{header}" if tasks else header)
    for index, snapshot in enumerate(snapshots):
        task = tasks[index] if index < len(tasks) else ""
        numbers = _panel_row(snapshot, hide_model=bool(model))
        if not task:
            # Byte-for-byte what this function rendered before ``tasks`` existed.
            lines.append(
                _flatten(f"[{index + 1}/{total}] {numbers}", limit=PANEL_ROW_MAX_CHARS)
            )
            continue
        lines.append(_composed_row(index, total, task, numbers))

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
    That final dedup mirrors the statusline half (``progress.py:466-468``): a
    frame that would repaint the same bytes is a kernel ``Task`` bought for
    nothing (H10), and it cannot lose information by construction.
    """

    __slots__ = ("_interval_ms", "_last_emit_s", "_last_text", "_now", "_snapshots")

    def __init__(
        self,
        expected: int,
        *,
        now: Callable[[], float] = time.monotonic,
        interval_ms: int = PARTIAL_MIN_INTERVAL_MS,
    ) -> None:
        """:param expected: how many members the batch submitted — the length of
        the snapshot table, so members still parked on the semaphore render as
        ``queued`` from the first frame instead of appearing one by one.
        :param now: monotonic seconds. Injectable so the tests pin the interval
        deterministically rather than sleeping.
        """

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
        change. For a caller that needs a final frame after the batch returns."""

        return format_card(self._snapshots)

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

        text = format_card(self._snapshots)
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
