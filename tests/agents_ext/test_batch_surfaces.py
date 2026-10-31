"""The three S10 surfaces, the H10 throttle, and the batch group — ADR-0199,
as ADR-0278 (#132) left them.

#132 REPLACED THE PANEL'S LAYOUT AND RETIRED THE AGGREGATE ROW, and the tests
below that pinned either were updated in place rather than deleted: each one
that still guards a property (a bound, a count, a dedup, an ownership check)
now guards it on the new panel, and each one whose rule was superseded says so
in its docstring and pins the rule that replaced it. ``format_aggregate_status``
(surface 1), the card (surface 2) and the throttle are unchanged and so are
their tests. The new panel's own tests are ``test_delegation_panel_132.py``.

Pure unit level. Every layout here is a function of a hand-built list of
snapshots, and the throttle runs on an injected clock rather than a ``sleep``,
so this file has no processes, no event loop timing and nothing to flake on.

WHAT THIS FILE CANNOT SEE, AND WHO SEES IT. A bridge whose adoption never
matched would write four ``status_key(id)`` rows, ``chrome._render_status`` would
``"  ".join`` them into a height-1 row (``tui/chrome.py:1216-1227``), and every
assertion below over synthetic snapshots would still pass. That is why
:func:`test_a_four_member_group_writes_exactly_one_status_key` drives the real
bridge through the real ``begin_group`` / ``adopt`` / ``__call__`` sequence, and
why WP-6 additionally drives a real batch end to end.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_agents import panel as panel_module
from aelix_agents.panel import (
    _ELLIPSIS,
    _MAX_INPUT_CHARS,
    AGGREGATE_MAX_CHARS,
    PANEL_MAX_ROWS,
    PANEL_MIN_CHILDREN,
    PANEL_ROW_MAX_CHARS,
    PANEL_WIDGET_KEY,
    PARTIAL_MIN_INTERVAL_MS,
    PartialThrottle,
    _flatten,
    format_aggregate_status,
    format_card,
    format_panel,
    panel_height,
)
from aelix_agents.progress import (
    SubagentProgressBridge,
    format_status_row,
    group_status_key,
    status_key,
)
from aelix_coding_agent.extensions.headless_ui import HEADLESS_UI_CONTEXT
from aelix_coding_agent.subagent_contract import (
    SUBAGENT_END,
    SUBAGENT_START,
    SubagentProgress,
)
from rich.cells import cell_len

from tests.agents_ext.test_events_and_statusline import _Api, _progress, _Ui

_GROUP = "tc-batch"


def _member(index: int, **kwargs: Any) -> SubagentProgress:
    """One member snapshot, id derived from its submitted index."""

    return _progress(id=f"sub-{index}", **kwargs)


def _reading(index: int, **kwargs: Any) -> SubagentProgress:
    """A member mid-tool: the shape that must NOT force a throttle flush.

    Same ``state`` and same ``current_tool`` as the previous frame is exactly the
    "the child is still working" case the 500 ms interval exists to thin out.
    """

    return _member(index, state="running", current_tool="read", **kwargs)


class _Clock:
    """Monotonic seconds under test control — the throttle's only clock."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance_ms(self, milliseconds: float) -> None:
        self.seconds += milliseconds / 1000


# === surface 1: the aggregate statusline row ==================================


def test_the_aggregate_row_is_s10s_worked_example() -> None:
    """The exact line decision S10 specifies, byte for byte.

    Four members, one of them still parked on the batch semaphore and therefore
    ``None`` — it has no spawn id at all, which is precisely why the group is
    opened with a COUNT (§3.6).
    """

    snapshots = [
        _member(0, state="running", elapsed_ms=33_000, tokens=12_300, cost=0.005),
        _member(1, state="running", elapsed_ms=31_000, tokens=9_000, cost=0.0031),
        _member(2, state="done", elapsed_ms=12_000, tokens=4_000, cost=0.0012),
        None,
    ]

    assert format_aggregate_status(snapshots) == (
        "agent scout ×4 · 2 running · 1 done · 1 queued · 33s · 12.3k tok · $0.0093"
    )


def test_the_aggregate_row_fits_a_shared_height_one_status_bar() -> None:
    """The reason surface 1 exists at all.

    Eight members, a long profile name and every state class non-zero — the
    widest line this formatter can be asked for. ``_render_status`` gives it a
    FIXED height-1 row shared with every other segment, so a row that wraps is a
    row that disappears.
    """

    snapshots: list[SubagentProgress | None] = [
        _member(0, profile="aelix-code-reviewer", state="running", elapsed_ms=999_000),
        _member(1, profile="aelix-code-reviewer", state="starting"),
        _member(2, profile="aelix-code-reviewer", state="running"),
        _member(3, profile="aelix-code-reviewer", state="running"),
        _member(4, profile="aelix-code-reviewer", state="done", tokens=987_654),
        _member(5, profile="aelix-code-reviewer", state="error"),
        _member(6, profile="aelix-code-reviewer", state="stopped", cost=99.9999),
        None,
    ]

    text = format_aggregate_status(snapshots)
    assert "\n" not in text
    assert len(text) <= AGGREGATE_MAX_CHARS < 80


def test_every_member_is_counted_exactly_once() -> None:
    """``starting`` folds into ``running`` and ``error`` reads ``failed`` — but
    the counts still sum to N, or the row is lying about the batch size."""

    snapshots: list[SubagentProgress | None] = [
        _member(0, state="starting"),
        _member(1, state="running"),
        _member(2, state="done"),
        _member(3, state="error"),
        _member(4, state="stopped"),
        None,
    ]

    text = format_aggregate_status(snapshots)
    assert "×6" in text
    assert "2 running" in text  # starting + running
    assert "1 done" in text
    assert "1 failed" in text  # ``error`` in the batch header's vocabulary
    assert "1 stopped" in text
    assert "1 queued" in text


def test_the_aggregate_token_count_is_the_max_and_the_cost_is_the_sum() -> None:
    """``SubagentUsage.tokens`` is a context LEVEL, "last message wins"
    (``subagent_contract.py:98-99``), so summing it across members would report
    a number several times the real one — the mistake ``stream.py:269-272``
    already warns about, and the rule ``aggregate.roll_up_usage`` follows. Cost
    IS a flow and IS summed."""

    snapshots = [
        _member(0, tokens=8_000, cost=0.01),
        _member(1, tokens=3_000, cost=0.02),
    ]

    text = format_aggregate_status(snapshots)
    assert "8.0k tok" in text
    assert "$0.0300" in text


def test_an_aggregate_with_nothing_published_is_empty_not_blank() -> None:
    """``""`` means "write no row". An empty segment would still cost the
    two-space join at ``chrome.py:1227``, and a row that cannot name the agent
    is noise on a row shared with everything else."""

    assert format_aggregate_status([None, None]) == ""
    assert format_aggregate_status([]) == ""


# === surface 2: the tool card =================================================


def test_a_single_child_card_is_byte_identical_to_p2() -> None:
    """The shipped single-delegation transcript is not something P3 may change.

    The expected strings are spelled out rather than imported from
    ``extension._partial_text`` on purpose: WP-6 DELETES that helper, so an
    import would make this test pass by tautology after the deletion.
    """

    assert format_card([_reading(0, elapsed_ms=4_200)]) == (
        "agent scout [running] · read · 4s"
    )
    assert format_card([_member(0, state="running", elapsed_ms=4_200)]) == (
        "agent scout [running] · 4s"
    )


def test_the_card_carries_one_line_per_child_in_submitted_order() -> None:
    """N lines, indexed ``[k/N]`` — the order the model wrote the tasks in and
    the order ``aggregate`` renders the results in (§3.4: never completion
    order). Today's ``_partial_text`` renders ONE child, so with N children the
    last writer wins."""

    lines = format_card(
        [
            _member(0, state="running", current_tool="read", elapsed_ms=1_000),
            _member(1, state="done", elapsed_ms=2_000),
            _member(2, state="running", current_tool="bash", elapsed_ms=3_000),
        ]
    ).split("\n")

    assert lines == [
        "[1/3] agent scout [running] · read · 1s",
        "[2/3] agent scout [done] · 2s",
        "[3/3] agent scout [running] · bash · 3s",
    ]


def test_a_queued_member_is_named_on_the_card_not_omitted() -> None:
    """A card that silently shows 2 of 4 rows reads as "two tasks were dropped",
    and the model reads this card too."""

    lines = format_card([_member(0, state="running"), None, None, _member(3)]).split(
        "\n"
    )
    assert len(lines) == 4
    assert lines[1] == "[2/4] agent scout [queued]"
    assert lines[2] == "[3/4] agent scout [queued]"


# === surface 3: the widget panel ==============================================


def test_one_child_gets_the_panel_too_and_no_child_gets_none() -> None:
    """S10 kept a single delegation off the panel ("P2 had no panel"). ADR-0278
    D1 reversed that — measured on ``9d1517a8``, one child's per-child status
    row was never on screen at 80 or 120 columns under a yolo parent — so one
    child gets the five-row block, and only an empty group draws nothing."""

    assert PANEL_MIN_CHILDREN == 1
    assert len(format_panel([_member(0)])) == 5
    assert format_panel([]) == []


def test_the_panel_header_states_the_counts_the_aggregate_states() -> None:
    """The header used to BE :func:`format_aggregate_status`. Since ADR-0278 it is
    the panel's own (``3 agents · scout · …``) and the aggregate row is no longer
    drawn (D2) — but the counts are the same function's (``_state_counts``), so
    the two cannot state a different fact about the batch."""

    snapshots: list[SubagentProgress | None] = [
        _reading(0, elapsed_ms=1_000, tokens=1_500),
        _member(1, state="done", elapsed_ms=2_000, cost=0.0031),
        None,
    ]

    header = _visible(format_panel(snapshots)[0])
    counts = "1 running · 1 done · 1 queued"
    assert counts in format_aggregate_status(snapshots)
    assert counts in header, header
    assert header.startswith("│ 3 agents · scout"), header


def test_an_agreeing_batch_names_its_model_once_in_the_header() -> None:
    """S10 Option A led every row with the model; the rows now carry the task and
    the header carries the batch's one model (ADR-0278 D4). A queued member has
    no snapshot and so no model either way."""

    snapshots: list[SubagentProgress | None] = [
        _reading(0, model="claude-opus-4-8", elapsed_ms=1_000, tokens=1_500),
        _member(1, model="claude-opus-4-8", state="done", elapsed_ms=2_000),
        None,
    ]

    lines = _panel(snapshots, tasks=("a", "b", "c"))
    assert " · claude-opus-4-8 · " in lines[0], lines[0]
    assert not any("claude-opus-4-8" in row for row in lines[1:]), lines


def test_a_model_less_panel_omits_the_model_term() -> None:
    """``model`` is ``None`` until the child's first ``message_end`` resolves the
    run model — and for every snapshot a runtime with no spawn model publishes.
    The term is OMITTED then: never ``None``, never a dangling separator."""

    for snapshots in (
        [_member(0, state="running", current_tool="read", elapsed_ms=1_000)],
        [_member(0, state="running"), _member(1, state="running")],
    ):
        for line in _panel(snapshots, tasks=("a", "b")):
            assert "None" not in line, line
            assert "·  ·" not in line and " ·  " not in line, line


def test_a_hostile_model_cannot_break_a_panel_row() -> None:
    """``model`` is child-authored, exactly like ``current_tool``, so it goes
    through the same :func:`_flatten`: no newline to add a chrome row, no ESC to
    smuggle an SGR, and bounded width — in the one-child header and in a batch
    whose members disagree (where every row carries its own)."""

    hostile = "gpt\n" + "\n".join(f"\x1b[31m ROW {i}" for i in range(40)) + "x" * 5000
    for snapshots in (
        [_member(0, state="running", model=hostile)],
        [_member(0, state="running", model=hostile), _member(1, state="running", model="b")],
    ):
        lines = format_panel(snapshots, tasks=("a", "b"))
        assert not any("\n" in line for line in lines)
        for line in lines:
            plain = _visible(line)
            assert "\x1b" not in plain and "\x9b" not in plain, plain
            assert cell_len(plain) <= 80, line


# === the H10 throttle =========================================================


def test_the_throttle_drops_mid_stream_frames_but_never_a_state_change() -> None:
    """H10: the kernel appends an ``asyncio.Task`` per ``on_partial`` into a list
    it never prunes (``loop.py:603``, ``:636``) and ``loop.py`` may not be
    edited, so dropping frames here is the only legal mitigation. What may never
    be dropped is the frame that carries a state change — the last thing the
    user sees must not be stale."""

    clock = _Clock()
    throttle = PartialThrottle(2, now=clock)

    assert throttle.record(0, _reading(0)) is not None
    assert throttle.record(1, _reading(1)) is not None

    clock.advance_ms(PARTIAL_MIN_INTERVAL_MS / 5)
    assert throttle.record(0, _reading(0, elapsed_ms=100)) is None
    assert throttle.record(0, _reading(0, elapsed_ms=200)) is None

    # A state change flushes REGARDLESS of the interval.
    final = throttle.record(0, _member(0, state="done", elapsed_ms=300))
    assert final is not None
    assert "[done]" in final


def test_a_current_tool_change_flushes_inside_the_interval() -> None:
    """``stream.py`` only touches ``current_tool`` at tool boundaries, so a tool
    transition is the one high-information frame a batch produces."""

    clock = _Clock()
    throttle = PartialThrottle(1, now=clock)
    throttle.record(0, _reading(0))

    clock.advance_ms(1)
    flushed = throttle.record(0, _member(0, state="running", current_tool="bash"))
    assert flushed is not None
    assert "bash" in flushed


def test_the_interval_alone_flushes_once_it_elapses() -> None:
    clock = _Clock()
    throttle = PartialThrottle(1, now=clock)
    throttle.record(0, _reading(0))

    clock.advance_ms(PARTIAL_MIN_INTERVAL_MS - 1)
    assert throttle.record(0, _reading(0, elapsed_ms=1_000)) is None
    clock.advance_ms(2)
    assert throttle.record(0, _reading(0, elapsed_ms=2_000)) is not None


def test_the_throttle_records_every_snapshot_even_when_it_drops_the_emit() -> None:
    """IT THROTTLES THE EMIT, NEVER THE INGEST (reviewer finding 19).

    Gating the ingest instead would freeze a child's row for the whole time it
    waits on the provider — 30-60 s with no ``current_tool`` change — while its
    siblings updated around it. Three frames are dropped here; the fourth emit
    must carry the newest state of the children whose frames were dropped, not
    the state they had when they were last emitted.
    """

    clock = _Clock()
    throttle = PartialThrottle(4, now=clock)
    for index in range(4):
        throttle.record(index, _reading(index))

    clock.advance_ms(PARTIAL_MIN_INTERVAL_MS / 10)
    assert throttle.record(2, _reading(2, elapsed_ms=5_000)) is None
    assert throttle.record(2, _reading(2, elapsed_ms=6_000)) is None
    assert throttle.record(3, _reading(3, elapsed_ms=7_000)) is None

    # The table saw all three, even though the screen saw none of them.
    assert throttle.snapshots[2].elapsed_ms == 6_000  # type: ignore[union-attr]
    assert throttle.snapshots[3].elapsed_ms == 7_000  # type: ignore[union-attr]

    clock.advance_ms(PARTIAL_MIN_INTERVAL_MS)
    emitted = throttle.record(0, _reading(0, elapsed_ms=8_000))
    assert emitted is not None
    assert "[3/4] agent scout [running] · read · 6s" in emitted
    assert "[4/4] agent scout [running] · read · 7s" in emitted


def test_an_unchanged_card_is_never_re_emitted() -> None:
    """A frame that repaints the same bytes is a kernel ``Task`` bought for
    nothing, and it cannot lose information by construction — the same dedup the
    statusline half already does (``progress.py:858-860``)."""

    clock = _Clock()
    throttle = PartialThrottle(1, now=clock)
    assert throttle.record(0, _reading(0)) is not None

    clock.advance_ms(PARTIAL_MIN_INTERVAL_MS * 10)
    assert throttle.record(0, _reading(0)) is None


def test_a_member_index_beyond_expected_grows_the_table() -> None:
    """The executor and the table disagreeing about the member count can only be
    a bug — but the recovery that loses a child's only surface is the worse
    one."""

    throttle = PartialThrottle(1, now=_Clock())
    throttle.record(0, _member(0, state="running"))
    text = throttle.record(2, _member(2, state="running"))
    assert text is not None
    assert text.startswith("[1/3] ")
    assert throttle.snapshots[1] is None


# === the bridge group =========================================================


def _bridge(ui: Any) -> tuple[SubagentProgressBridge, Any]:
    """A bridge with no loop (it draws on the publish) and a STOPPED clock, so
    every idle time is zero and a frame is a pure function of its snapshots."""

    api = _Api(ui)
    return SubagentProgressBridge(api, clock=lambda: 0.0), api


def _drawn(
    snapshots: list[SubagentProgress | None], *, tasks: tuple[str, ...] = (), **kw: Any
) -> list[str]:
    """What the bridge draws for ``snapshots`` on a UI with no chrome."""

    return format_panel(
        snapshots,
        tasks=tasks,
        width=80,
        height=panel_height(len(snapshots)),
        idle_ms=[0 if s is not None else None for s in snapshots],
        profile=kw.pop("profile", ""),
        **kw,
    )


def test_a_four_member_group_writes_no_status_key_and_one_panel() -> None:
    """R11, arriving through the seam the panel exists to prevent — now with no
    statusline row at all (ADR-0278 D2).

    A bridge whose adoption never matched would write four ``status_key(id)``
    rows; one whose grouping wrote the retired aggregate would write one. The
    statusline is left to the disclosure row, and the panel is ONE widget key.
    """

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=4)
    for index in range(4):
        bridge.adopt(f"sub-{index}", _GROUP, index=index)
        bridge(_reading(index, elapsed_ms=1_000))

    assert ui.writes == []
    assert {key for key, _ in ui.widgets} == {PANEL_WIDGET_KEY}
    assert ui.widgets[-1][1] == _drawn([_reading(index, elapsed_ms=1_000) for index in range(4)])


def test_the_bridge_measures_the_terminal_rather_than_assuming_the_constant() -> None:
    """The WIRING, which nothing else here can see.

    ``format_panel`` honours the ``width`` it is given, and every test of that is
    a test of the formatter. Whether the one production caller actually MEASURES a
    terminal and passes it is a separate fact, and dropping the argument at the
    call site left the formatter tests green — a sabotage run caught exactly that.
    This drives the real bridge through the real ``begin_group`` / ``adopt`` /
    ``__call__`` sequence with a UI whose chrome reports 40 columns, and reads what
    reached ``set_widget``.

    The bound is the TERMINAL's: at 40 columns a wider row is the defect. Since
    ADR-0278 D4 the positive control is the other end too — at 200 columns the
    panel is the transcript's 120, not the 78-cell ribbon it used to be.
    """

    class _Sized:
        """A chrome whose output reports a fixed size, like ``tui/width`` reads."""

        def __init__(self, columns: int) -> None:
            size = type("Size", (), {"columns": columns, "rows": 24})()
            output = type("Out", (), {"get_size": lambda _self: size})()
            self.app = type("App", (), {"output": output})()

    ui = _Ui()
    ui.chrome = _Sized(40)  # type: ignore[attr-defined]
    bridge, _ = _bridge(ui)
    jobs = tuple(f"port the exponential retry backoff to {name}" for name in
                 ("google", "openai", "azure", "vertex"))
    bridge.begin_group(_GROUP, expected=4, tasks=jobs)
    for index in range(4):
        bridge.adopt(f"sub-{index}", _GROUP, index=index)
        bridge(_reading(index, model="github-copilot/gpt-5.6-codex", elapsed_ms=1_000))

    written = ui.widgets[-1][1]
    assert written is not None
    for row in written:
        assert cell_len(_visible(row)) <= 40, (cell_len(_visible(row)), row)
    ui_wide = _Ui()
    ui_wide.chrome = _Sized(200)  # type: ignore[attr-defined]
    wide, _ = _bridge(ui_wide)
    wide.begin_group(_GROUP, expected=4, tasks=jobs)
    for index in range(4):
        wide.adopt(f"sub-{index}", _GROUP, index=index)
        wide(_reading(index, model="github-copilot/gpt-5.6-codex", elapsed_ms=1_000))
    widest = max(cell_len(_visible(row)) for row in ui_wide.widgets[-1][1] or [])
    assert widest == 120, widest


# === GitHub #48 ask 3 — the panel says what each member is DOING =============


_JOBS = (
    "audit width.py for baked widths",
    "port the picker frame to a terminal-derived width",
    "re-run the pyte harness at 40 and 200 columns",
    "write the ADR",
)


def _visible(line: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", line)


def _panel(snapshots: list[SubagentProgress | None], **kw: Any) -> list[str]:
    return [_visible(line) for line in format_panel(snapshots, **kw)]


def _four(**kw: Any) -> list[SubagentProgress | None]:
    return [
        _reading(0, model="gpt-5-codex", elapsed_ms=12_000, tokens=1_500, **kw),
        _reading(1, model="gpt-5-codex", elapsed_ms=3_000, tokens=400, **kw),
        _member(1, model="gpt-5-codex", state="done", elapsed_ms=8_000),
        None,
    ]


def test_the_panel_names_the_job_each_member_is_running() -> None:
    """The owner's ask, and the reason it needed plumbing rather than a layout.

    One ``agent()`` call is one profile and one model across N tasks (S3), so
    every per-member field the panel had was IDENTICAL on every row — including
    ``model``, whose own comment gave "the column is naturally uniform-width" as
    the reason to put it first. The submitted task was the only differing fact
    and it stopped at ``extension.py``.

    A label that does not fit is cut in the MIDDLE since ADR-0278 D4, so what a
    row must show is the job's first words and its last one, not a prefix.
    """

    rows = _panel(_four(), tasks=_JOBS)[1:]
    for index, job in enumerate(_JOBS):
        words = job.split()
        assert " ".join(words[:2]) in rows[index], (index, rows[index])
        assert words[-1] in rows[index], (index, rows[index])


def test_the_job_label_is_index_aligned_with_the_member() -> None:
    """``tasks[k]`` belongs to row k, and off-by-one here is invisible by
    inspection: every label is plausible on every row.

    The member table is indexed by SUBMITTED position and so is ``tasks``; that
    is the whole reason a bare tuple is sufficient (``progress._Group``).
    """

    rows = _panel(_four(), tasks=("alpha", "bravo", "charlie", "delta"))[1:]
    # ``│ `` is the panel's own gutter (ADR-0278 D4); ``_visible`` strips the
    # SGR around it, not the glyph. Four members on the default 24-row budget
    # are one row each.
    assert rows[0].startswith("│ ● 1 alpha")
    assert rows[1].startswith("│ ● 2 bravo")
    assert rows[2].startswith("│ ✓ 3 charlie")
    assert rows[3].startswith("│ ○ 4 delta")


def test_the_model_moves_to_the_header_when_every_member_agrees() -> None:
    """It took the rows' width, so it is stated once instead of N times."""

    lines = _panel(_four(), tasks=_JOBS)
    assert "gpt-5-codex" in lines[0]
    for row in lines[1:]:
        assert "gpt-5-codex" not in row, row


def test_the_model_stays_on_the_rows_when_members_disagree() -> None:
    """``model`` is read off each CHILD's own ``message_end``, so two children of
    one profile CAN report different strings — a provider fallback, a mid-batch
    alias resolution. A header claiming one model for a batch running two is
    worse than a repeated column, so the rows take it back.

    AND WHEN THE ROW CANNOT HOLD BOTH, THE STATE WINS. It used to be the order of
    two words in a numbers column; since ADR-0278 D4 the state is the row's
    leading GLYPH, which nothing cuts, and the model is a right-hand term that
    goes before the elapsed time does.
    """

    mixed: list[SubagentProgress | None] = [
        _reading(0, model="gpt-5-codex"),
        _reading(1, model="claude-opus-5"),
        None,
    ]

    roomy = _panel(mixed, tasks=("alpha", "beta", "gamma"))
    assert "gpt-5-codex" not in roomy[0] and "claude-opus-5" not in roomy[0]
    assert "gpt-5-codex" in roomy[1]
    assert "claude-opus-5" in roomy[3]

    for width in (30, 40, 60):
        tight = _panel(mixed, tasks=_JOBS[:3], width=width)
        assert tight[1].startswith("│ ● 1") and tight[3].startswith("│ ● 2"), tight
        assert tight[5].startswith("│ ○ 3"), tight


def test_the_shared_statusline_row_is_untouched_by_all_of_this() -> None:
    """The model cannot go into ``format_aggregate_status``'s default output.

    That string is ALSO the height-1 row shared with every other statusline
    segment, bounded at :data:`AGGREGATE_MAX_CHARS`, and S10's worked example is
    already 74 of those 78 characters. So the panel passes ``extra_head`` and the
    statusline keeps exactly what it had — which is what "the panel and the
    statusline can never disagree" was protecting: they still cannot state a
    DIFFERENT fact, the panel simply states one more.
    """

    snapshots = _four()
    assert format_aggregate_status(snapshots) == (
        "agent scout ×4 · 2 running · 1 done · 1 queued · 12s · 1.5k tok"
    )
    assert "gpt-5-codex" not in format_aggregate_status(snapshots)


def test_a_panel_without_tasks_still_draws_a_row_per_member() -> None:
    """Every production caller passes the tasks now — the extension opens a group
    for every call with its tasks (ADR-0278 D1). A caller that passes none gets
    the same shape with no label: the old no-tasks byte identity belonged to a
    layout that is gone."""

    snapshots: list[SubagentProgress | None] = [
        _reading(0, model="claude-opus-4-8", elapsed_ms=1_000, tokens=1_500),
        _member(1, model="claude-opus-4-8", state="done", elapsed_ms=2_000, cost=0.0031),
        None,
    ]
    rows = _panel(snapshots)
    assert len(rows) == 7
    assert [row[:5] for row in rows[1::2]] == ["│ ● 1", "│ ✓ 2", "│ ○ 3"]


def test_job_labels_do_not_change_the_row_count() -> None:
    """Row COUNT is the measured flicker path (``chrome.py:781-793`` records an
    attempt reverted for making it worse), and the panel shares its window with
    the live thinking stream. The label goes INSIDE the existing row, and the
    count is the one fixed when the group opened (ADR-0278 D5)."""

    for tasks in ((), _JOBS):
        assert len(format_panel(_four(), tasks=tasks)) == panel_height(4)


def test_a_hostile_job_label_cannot_break_the_panel() -> None:
    """The task text is the PARENT model's tool-call argument, not this module's.

    A prompt-injected model authors it, so it is untrusted in the same way
    ``current_tool`` is — newlines that would add chrome rows, ESC that would
    smuggle an SGR, and width measured in cells rather than codepoints.
    """

    evil = "보안\n" + "\n".join(f"\x1b[31mROW {i}" for i in range(40)) + "한" * 500
    lines = format_panel(_four(), tasks=(evil, *_JOBS[1:]))

    assert len(lines) == panel_height(4)
    joined = "".join(lines)
    assert "\n" not in joined
    assert "\x1b[31mROW" not in joined
    assert "\x9b" not in joined
    for line in lines:
        assert cell_len(_visible(line)) <= 80, cell_len(_visible(line))


def test_the_gutter_is_an_escape_the_chrome_will_parse() -> None:
    """``chrome._render_widget_lines`` ANSI-parses the joined lines, so raw SGR is
    this module's own vocabulary here — but only if it SURVIVES ``_flatten``,
    which deletes C0 and would strip an ESC handed to it. Applied after, never
    before. The gutter is the transcript card's ``│ `` since ADR-0278 D4."""

    lines = format_panel(_four(), tasks=_JOBS)
    for line in lines:
        assert line.startswith("\x1b[2m│ \x1b[0m"), line


def test_a_four_member_group_carries_its_tasks_through_the_real_bridge() -> None:
    """The seam, not the layout: ``begin_group`` stores them and the bridge's
    redraw passes them on. Every assertion above builds the panel directly and
    would pass with the bridge dropping ``tasks`` on the floor."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=4, tasks=_JOBS)
    for index in range(4):
        bridge.adopt(f"sub-{index}", _GROUP, index=index)
        bridge(_reading(index, elapsed_ms=1_000))

    written = ui.widgets[-1][1]
    assert written == _drawn([_reading(index, elapsed_ms=1_000) for index in range(4)], tasks=_JOBS)
    for index, job in enumerate(_JOBS):
        assert job[:12] in _visible(written[index + 1])


# === review findings: bounds that were not bounds, and a row that was all 78 ===


def test_a_zero_width_flood_is_still_bounded() -> None:
    """A cell count is NOT a bound, and the cells-only version had none.

    ``cell_len`` returns 0 for a combining mark, so a string made of them has no
    width and a cell cap never fires. MEASURED on the cells-only version: 200 000
    combining acutes in, 200 000 out, against a limit of 78 — a hard character cap
    replaced by no cap at all, on a row that reaches a widget window with no bound
    of its own. Both units, codepoints first.
    """

    flood = "\u0301" * 200_000
    out = _flatten(flood, limit=PANEL_ROW_MAX_CHARS)
    # BOTH units. The codepoint cap is slack — the ellipsis has to survive for
    # ordinary input — so the contract is "bounded", not "bounded at the cell
    # budget": 200 000 in, a few hundred out, and never more than the budget in
    # CELLS.
    #
    # LITERALS, NOT ``PANEL_ROW_MAX_CHARS * _ZERO_WIDTH_SLACK``. Writing the bound
    # in terms of the constant that sets it made it unfalsifiable: with
    # ``_ZERO_WIDTH_SLACK`` raised to 100 000 this file still passed while
    # ``_flatten`` handed back 8 192 codepoints — ``_MAX_INPUT_CHARS`` caps the
    # input first here, so the slack never reaches the full 200 000, and the
    # assertion still grew with the thing it was meant to constrain. (The same
    # sabotage against ``session_labels.truncate_cells``, which has no input cap,
    # does hand back all 200 000 — see ``tests/cli/test_session_labels.py``.) The
    # cell half cannot carry the test alone either, since a combining mark
    # measures zero and ``0 <= 78`` holds for any output whatsoever.
    assert len(out) <= 313, len(out)  # 78 cells x 4 codepoints, ellipsis included
    assert cell_len(out) <= PANEL_ROW_MAX_CHARS

    rows = format_panel(
        [
            _member(k, state="running", current_tool=flood, last_text=flood, model=flood)
            for k in range(8)
        ],
        tasks=tuple(flood for _ in range(8)),
    )
    assert len(rows) <= PANEL_MAX_ROWS
    for row in rows:
        plain = _visible(row)
        # At most three flattened child fields share a row (the label, the call,
        # the reply), each at most four codepoints per cell of an 80-cell row.
        assert len(plain) <= 3 * 4 * 80 + 80, len(plain)
        assert cell_len(plain) <= 80


def test_the_ellipsis_is_inside_the_budget_not_added_to_it() -> None:
    """The codepoint backstop marks its cut, and the mark has to fit the budget.

    Returning ``flat + _ELLIPSIS`` measured against ``limit`` rather than one less
    was a cell over whenever the codepoint slice happened to land exactly on the
    budget — reachable at four codepoints per cell, i.e. a base character carrying
    three combining marks. One cell past :data:`PANEL_ROW_MAX_CHARS` is a row
    hanging outside the frame that sized itself to that number.

    A SWEEP, because the overhang only appears where the slice and the budget
    coincide: a single hand-picked pair is as likely to miss it as to find it.
    """

    for limit in range(1, 90):
        for n in range(1, 90):
            text = ("a" + "́" * 3) * n + "a"
            assert cell_len(_flatten(text, limit=limit)) <= limit, (limit, n)

    # Control: the cheap way to satisfy the sweep is to stop marking the cut, and
    # ordinary over-long input must still say that it was cut.
    marked = _flatten("x" * 100, limit=20)
    assert marked.endswith(_ELLIPSIS), marked
    assert cell_len(marked) == 20, marked


def test_the_cell_bound_holds_for_the_shapes_the_first_sweep_did_not_cover() -> None:
    """POSITION and GRAPHEME, the two axes the sweep above misses.

    That sweep puts its zero-width characters AFTER a visible glyph, and every
    input it builds is one codepoint per column plus combining marks. Two shapes
    it therefore cannot reach both broke the bound, and an external review found
    them:

    * A LEADING U+200D ZERO WIDTH JOINER. ``rich.cells.set_cell_size`` is not
      exact for a string that begins with one — MEASURED,
      ``set_cell_size("\\u200d" * 2 + "a" * 79, 39)`` returns 40 cells — so
      ``_flatten`` returned ``limit + 1`` while believing the library had bounded
      it. 176 (limit, input) pairs violated the bound. ZWJ SPECIFICALLY: U+200B,
      U+0301, U+FE0F, U+200E and U+00AD are all exact over the same sweep, which
      is why the loop below still walks every zero-width character it can find
      rather than only the one that broke.
    * GRAPHEME CLUSTERS. ``cell_len`` measures clusters, so it is NOT additive
      over characters: ``❤️`` is 1 by the per-character sum and 2 as a string.
      An accumulate loop that trusts the sum therefore under-counts, and the first
      fix for the point above returned 9 cells against a budget of 5 for a row of
      hearts. The measure of the WHOLE result is the only thing that settles it.

    Both axes are swept here, and the ordinary case is asserted alongside so a
    "fix" that simply truncates harder cannot pass.
    """

    zero_width = ("‍", "​", "️", "́", "͏")
    clusters = ("❤️", "👩‍💻", "🇺🇸", "👨‍👩‍👧‍👦", "🏳️‍🌈", "é")

    for limit in range(1, 90):
        for mark in zero_width:
            for count in (1, 2, 3, 5, 9, 17):
                for tail in (0, 1, 50, 200):
                    for text in (
                        mark * count + "a" * tail,  # LEADING — the failing shape
                        "a" * tail + mark * count,
                        "".join("a" + mark * count for _ in range(max(1, tail // 4))),
                    ):
                        got = cell_len(_flatten(text, limit=limit))
                        assert got <= limit, (limit, repr(mark), count, tail, got)
        for cluster in clusters:
            for count in (1, 3, 10, 40):
                for text in (cluster * count, "a" * 20 + cluster * count, ("a" + cluster) * count):
                    got = cell_len(_flatten(text, limit=limit))
                    assert got <= limit, (limit, cluster, count, got)

    # Control: ordinary input is untouched and an ordinary cut is still marked.
    assert _flatten("read_file", limit=78) == "read_file"
    assert _flatten("x" * 100, limit=20).endswith(_ELLIPSIS)


def test_flatten_does_not_do_unbounded_work_per_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control strip is linear in the INPUT, and this runs on every publish for
    every member. A megabyte of ``current_tool`` must not buy a megabyte of work.

    COUNTED, NOT TIMED, and on the shape that is actually expensive. The first
    version wrapped a wall clock around ``"x" * 4_000_000`` and asserted under
    200 ms: the bound could be DELETED outright and it still passed in 12 ms,
    because a whitespace-free ASCII run is the cheapest input there is — one
    ``split`` token, then an ASCII ``translate`` fast path that consults the table
    once per DISTINCT byte. Non-ASCII takes the general path, where every
    character is a real lookup; that is the input this constant exists for.

    The observation is how many characters reach the control strip, recorded by
    wrapping it. Asserting EQUALITY against the literal bound makes the test its
    own positive control: a broken instrument reads 0 and fails as loudly as an
    absent bound reads 400 000.

    #178 moved the strip from a module-local ``translate`` table onto
    ``terminal_text.safe_for_terminal``; the counting moved with it, because
    the old counter wrapped a table that no longer exists and would read 0.
    """

    seen: list[int] = []
    real = panel_module.safe_for_terminal

    def _counting(text: str, **kwargs: Any) -> str:
        seen.append(len(text))
        return real(text, **kwargs)

    monkeypatch.setattr(panel_module, "safe_for_terminal", _counting)
    out = _flatten("\u0301" * 400_000, limit=PANEL_ROW_MAX_CHARS)

    assert seen == [8192], (
        f"{seen} characters reached the control strip; the work bound is 8192"
    )
    assert cell_len(out) <= PANEL_ROW_MAX_CHARS


def test_no_row_is_wider_than_the_terminal_it_is_drawn_into() -> None:
    """The widget window CLIPS rather than wraps, so a row wider than the terminal
    loses its right-hand end.

    MEASURED on the first job-label version: rows padded to the 78-cell ceiling
    lost the word ``queued`` at 72 columns. ADR-0278 D4 measures the row against
    the LIVE terminal instead, so right-aligning the numbers is safe: a row ends
    at the terminal's edge, never past it, and the queued member's words survive
    at every width ``tui/width.py`` supports (it has no floor).
    """

    for width in (30, 40, 60, 72, 80):
        rows = _panel(_four(), tasks=("a", "bb", "ccc", "write the ADR"), width=width)
        assert all(cell_len(row) <= width for row in rows), (width, rows)
        label = "write the ADR" if width >= 40 else "write"
        assert rows[-1].startswith(f"│ ○ 4 {label}"), (width, rows[-1])
        assert "waiting" in rows[-1], (width, rows[-1])


def test_labels_that_share_a_prefix_stay_distinct_idle_or_busy() -> None:
    """Four tasks over one verb must stay four rows a user can tell apart.

    The old label COLUMN was sized from the numbers until a review measured it
    collapsing four such labels to one common prefix once the children got busy.
    ADR-0278 D4 has no shared column — each label is cut only by its own row's
    numbers — and the property that mattered is pinned on the new layout: at 80
    columns, idle or busy, two rows each or one, the four labels differ.
    """

    jobs = (
        "port the retry backoff to google",
        "port the retry backoff to openai",
        "port the retry backoff to azure",
        "port the retry backoff to vertex",
    )
    idle = [_member(k, state="starting") for k in range(4)]
    busy = [
        _member(k, state="running", current_tool="read", elapsed_ms=999_000,
                tokens=987_654, cost=99.9999)
        for k in range(4)
    ]
    for snapshots in (idle, busy):
        for height in (5, 9):
            rows = _panel(snapshots, tasks=jobs, height=height)
            labelled = rows[1::2] if height == 9 else rows[1:]
            tails = {name for name in ("google", "openai", "azure", "vertex")
                     for row in labelled if name in row}
            assert len(tails) == 4, (height, labelled)


def _numbers_half(row: str) -> str:
    """A panel row without its gutter, index prefix and job label.

    The label is the SUBMITTED TASK — the parent model's own words — so a batch
    whose task says "migrate the github-copilot/gpt-5.6-codex path" puts the model
    id into the row without the row having rendered a model at all. Searching the
    whole row for a model therefore reports one that ``_panel_row`` correctly
    suppressed. The column is padded with at least two spaces (``_composed_row``),
    which is the seam.
    """

    body = row.split("] ", 1)[-1]
    parts = body.split("  ")
    return parts[-1].strip() if len(parts) > 1 else body.strip()


def _named_model(text: str, model: str) -> str:
    """The longest prefix of ``model`` that ``text`` actually shows.

    The panel may state the model whole or truncated, so "is the model here"
    cannot be a substring test on the whole id. Derived from the id itself rather
    than from the module's own truncator, which would make the check agree with
    the code by construction.

    THE PREFIX HAS TO IDENTIFY THE MODEL. The first version accepted any prefix
    at all, and ``c`` is a prefix of ``claude-opus-4-8`` that appears in every row
    of English prose — the check reported the model on rows that never mentioned
    it. The floor is the whole id or 8 cells, whichever is shorter: 8 separates
    ``claude-o…`` from ``openai/g…`` and from ``gpt-5``.
    """

    floor = min(cell_len(model), 8)
    for size in range(len(model), 0, -1):
        prefix = model[:size]
        if cell_len(prefix) >= floor and prefix in text:
            return prefix
    return ""


_MODEL_IDS = (
    "gpt-5",
    "claude-opus-4-8",
    "openai/gpt-5.6-luna-preview-2026",
    "github-copilot-enterprise/gpt-5.6-luna-preview-2026-08",
)
"""5, 15, 32 and 54 cells — a bare id, a vendor id, a provider-scoped one and one
past the ceiling.

THE FOURTH IS WHY THIS IS A TUPLE AND NOT THREE. 32 is exactly
``_MAX_PROFILE_CHARS * 2``, the widest term the head will ever hold, so a sweep
that stopped there never asked what happens ABOVE the bound — the docstring said
"every id length" while its longest fixture sat on the boundary and one character
further would have falsified it."""


def test_the_counts_survive_the_model_but_not_a_head_wider_than_the_cap() -> None:
    """Where the bound runs out, pinned — because the docstring says where.

    Sizing the model against the counts stops the MODEL displacing one. It cannot
    stop the head and the counts THEMSELVES exceeding the cap, and the sentence
    written for the fix first claimed otherwise, which is the same overclaim the
    fix was correcting. MEASURED at cap 76 with all five classes non-zero: a
    5-cell profile survives to 495 members and is cut at 4 995; a 16-cell profile
    name is over at 5.

    The 16-cell arm is the cheap one and is what this pins. Both shapes need a
    batch S3 does not produce — one ``agent()`` call is a handful of tasks — so
    this is a documented limit, not a defect: the test exists so the limit cannot
    move without somebody noticing.
    """

    def batch(profile: str) -> list[SubagentProgress | None]:
        states = ("running", "done", "error", "stopped")
        return [
            _member(index, profile=profile, state=state, model="gpt-5")
            for index, state in enumerate(states)
        ] + [None]

    every_count = ("1 running", "1 done", "1 failed", "1 stopped", "1 queued")

    # BESIDE A MODEL THAT IS ACTUALLY THERE. A first version passed a 54-cell id,
    # which ``_model_term`` drops at five classes — so the row it asserted on was
    # byte-identical to the same row with ``extra_head=""`` and the counts were
    # surviving nothing. Five classes leave four cells, so a four-cell id is what
    # exercises the rule this test is named for.
    beside = format_aggregate_status(batch("scout"), extra_head="gpt4", cap=76)
    assert "gpt4" in beside, beside
    for count in every_count:
        assert count in beside, (count, beside)

    # And when the id does NOT fit, the model is what goes, not a count.
    narrow = format_aggregate_status(batch("scout"), extra_head=_MODEL_IDS[3], cap=76)
    for count in every_count:
        assert count in narrow, (count, narrow)
    assert not _named_model(narrow, _MODEL_IDS[3]), narrow

    # ``가`` is two cells, so eight of them is the 16-cell ceiling _short_profile
    # allows. Nothing is left for the counts and the closing truncation reaches
    # them; this arm is the limit, asserted so it stays a known one. It gates the
    # closing truncation rather than the model rule — the model is dropped here
    # too, and saying so is the difference between a limit and a coincidence.
    wide = format_aggregate_status(batch("가" * 8), extra_head=_MODEL_IDS[3], cap=76)
    assert cell_len(wide) <= 76, cell_len(wide)
    assert "1 queued" not in wide, wide


def test_a_model_that_does_not_fit_whole_is_TRUNCATED_not_dropped() -> None:
    """The central rule of the AGGREGATE's model term, and it had exactly one gate.

    Reverting :func:`_model_term` to the rule it replaced — show the whole term or
    nothing — left every unit test in this file GREEN; only the pyte-glass test
    noticed. The rule that matters is the middle branch: between "it fits" and
    "there is no room worth spending", the model is CUT to what is left.

    Asserted on ``format_aggregate_status(extra_head=…)`` itself since ADR-0278:
    the panel builds its own header now (which names a model whole or not at
    all, see the next test), and this function is the one that keeps the rule.
    """

    model = "github-copilot/gpt-5.6-codex"  # 28 cells
    header = format_aggregate_status(_agreeing(model), extra_head=model, cap=76)

    shown = _named_model(header, model)
    assert shown, header
    assert model not in header, header
    assert cell_len(shown) >= 12 - cell_len(_ELLIPSIS), (shown, header)
    assert _ELLIPSIS in header, header
    for count in ("1 done", "1 failed", "1 stopped", "1 queued"):
        assert count in header, (count, header)

    short = format_aggregate_status(_agreeing("gpt-5"), extra_head="gpt-5", cap=76)
    assert "gpt-5" in short and "gpt-5…" not in short, short


def test_when_the_header_cannot_afford_the_model_no_row_repeats_it() -> None:
    """The panel header names an agreeing batch's model when it fits beside the
    counts and the elapsed time, and drops it otherwise (ADR-0278 D4).

    SUPERSEDED, and said so rather than deleted: the old panel handed a dropped
    model to the ROWS (an owner choice on the job-label layout, where it read
    ``running · github-copilot/gpt-5.…``). D4's rows carry a model only when the
    members DISAGREE; an agreeing batch's model is one fact about the whole
    call, so when the counts take its room it is not named at that width. The
    counts are what the space goes to, and that is the point: ``1 queued`` is a
    fact the panel cannot state any other way.
    """

    model = "github-copilot/gpt-5.6-codex"
    wide: list[SubagentProgress | None] = [
        _member(index, model=model, state="running", current_tool="read_file")
        for index in range(5)
    ]
    wide += [
        _member(5, model=model, state="done", current_tool="read_file"),
        _member(6, model=model, state="error", current_tool="read_file"),
        _member(7, model=model, state="stopped", current_tool="read_file"),
        None,
    ]
    jobs = tuple(f"job number {index}" for index in range(9))
    lines = _panel(wide, tasks=jobs, width=80, height=PANEL_MAX_ROWS + 1)
    header, rows = lines[0], lines[1:]

    assert "1 queued" in header, header
    assert not _named_model(header, model), header
    assert "job number 0" in rows[0], rows[0]
    assert not any(_named_model(row, model) for row in rows), rows
    # Positive control: where there is room, the header names it whole.
    roomy = _panel(wide, tasks=jobs, width=160, height=PANEL_MAX_ROWS + 1)
    assert model in roomy[0], roomy[0]


def test_a_disagreeing_batch_still_states_every_member_s_state() -> None:
    """Every row leads with its state's GLYPH (ADR-0278 D4), so no model, label
    or number can push the state off the row — the failure this test was written
    for, when a 28-cell provider-scoped id ate a numbers column and 1 of 4 rows
    showed a state word."""

    mixed: list[SubagentProgress | None] = [
        _member(0, model="github-copilot/gpt-5.6-codex", state="starting"),
        _member(1, model="gpt-5.6-codex", state="running", current_tool="grep"),
        _member(2, model="gpt-5.6-codex", state="done"),
        None,
    ]
    for width in (40, 80, 120, 200):
        lines = [_visible(line) for line in format_panel(mixed, tasks=_JOBS, width=width)]
        rows = lines[1:]
        assert len(rows) == 4, (width, lines)
        for row, glyph in zip(rows, ("●", "●", "✓", "○"), strict=True):
            assert row.startswith(f"│ {glyph} "), (width, glyph, row)
        assert not _named_model(lines[0], "gpt-5.6-codex"), lines[0]


def test_the_panel_keeps_every_state_count_at_every_id_length() -> None:
    """The counts are the substance and the model is what gets cut, not them.

    Three rules were tried at this seam on the aggregate-based header; ADR-0278's
    header keeps the result: the model is admitted only beside the counts and
    the elapsed time, at most 32 cells, and the counts are never cut to make
    room for numbers. So the gate is the counts, at every id length, spelled in
    full, and — when the model is shown — after the profile and before them.
    """

    for model in _MODEL_IDS:
        snapshots: list[SubagentProgress | None] = [
            _member(0, model=model, state="error"),
            _member(1, model=model, state="stopped"),
            _member(2, model=model, state="done"),
            None,
        ]
        header = _visible(format_panel(snapshots, tasks=_JOBS)[0])
        assert cell_len(header) <= 80, (model, cell_len(header))
        for count in ("1 done", "1 failed", "1 stopped", "1 queued"):
            assert count in header, (model, count, header)
        shown = _named_model(header, model)
        if shown:
            assert header.index("scout") < header.index(shown) < header.index("1 done"), header

    wide: list[SubagentProgress | None] = [
        _member(index, model="gpt-5", state="running") for index in range(5)
    ]
    wide += [
        _member(5, model="gpt-5", state="done"),
        _member(6, model="gpt-5", state="error"),
        _member(7, model="gpt-5", state="stopped"),
        None,
    ]
    lines = [_visible(line) for line in format_panel(wide, tasks=_JOBS)]
    assert "1 queued" in lines[0], lines[0]


# === second review round: the fixes' own holes ================================


def _agreeing(model: str) -> list[SubagentProgress | None]:
    """Three published members on ONE model, plus one still queued."""

    return [
        _member(0, model=model, state="error"),
        _member(1, model=model, state="stopped"),
        _member(2, model=model, state="done"),
        None,
    ]


def test_never_more_than_one_surface_names_an_agreeing_model() -> None:
    """NEVER BOTH is the invariant, at every width production can pass
    (``tui.width.terminal_columns``): an agreeing batch's model is in the header
    or nowhere, never repeated down the rows (ADR-0278 D4)."""

    for model in _MODEL_IDS:
        for width in (30, 40, 50, 60, 70, 78, 120):
            lines = _panel(_agreeing(model), tasks=_JOBS, width=width)
            in_rows = [r for r in lines[1:] if _named_model(r, model)]
            assert not in_rows, (model, width, lines)

    # Positive control: at the widest width the header names it — whole, or at
    # the header's own 32-cell field bound for an id past it.
    for model in _MODEL_IDS:
        header = _panel(_agreeing(model), tasks=_JOBS, width=120)[0]
        shown = _named_model(header, model)
        assert shown, (model, header)
        assert cell_len(shown) >= min(cell_len(model), 32 - cell_len(_ELLIPSIS)), (model, shown)


def test_the_model_s_cost_at_narrow_widths_is_stated_not_hidden() -> None:
    """What dropping the model costs, in the same test as the rule.

    The old panel's rows took a dropped model back (owner choice, ADR-0227);
    ADR-0278 D4 supersedes that layout. At 50 columns a three-class batch with a
    28-cell id keeps its counts and its elapsed time and loses the model; at
    120 the header names it.
    """

    model = "github-copilot/gpt-5.6-codex"
    snaps: list[SubagentProgress | None] = [
        _member(0, model=model, state="running", current_tool="read_file"),
        _member(1, model=model, state="running", current_tool="read_file"),
        _member(2, model=model, state="done", current_tool="read_file"),
        None,
    ]
    narrow = _panel(snaps, tasks=_JOBS, width=50)
    assert not _named_model(narrow[0], model), narrow[0]
    assert "2 running · 1 done · 1 queued" in narrow[0], narrow[0]
    assert model in _panel(snaps, tasks=_JOBS, width=120)[0]


def test_leading_whitespace_cannot_delete_a_field() -> None:
    """``_MAX_INPUT_CHARS`` is a WORK bound, not a width bound.

    Slicing before the whitespace collapse let leading blanks consume the whole
    window: MEASURED, 9 000 spaces followed by ``read_file`` rendered as the empty
    string — the field deleted rather than bounded.
    """

    assert _flatten(" " * 9_000 + "read_file", limit=PANEL_ROW_MAX_CHARS) == "read_file"
    assert _flatten("\t" * 9_000 + "grep", limit=PANEL_ROW_MAX_CHARS) == "grep"


def test_the_input_bound_still_bounds_the_work() -> None:
    """The fix above must not have removed the bound it was loosening.

    MEASURED BY WHAT THE WORK ALLOCATES, NOT BY HOW LONG IT TAKES (#330). This
    was ``perf_counter() < 200 ms`` over 4 000 000 characters, a clock verdict a
    loaded windows runner could trip — and one that could not see the regression
    it named: measured on darwin, with the bound lifted (``_MAX_INPUT_CHARS`` set
    past the input) the same call takes 4 ms. What the bound actually bounds is
    how much text the steps after it are handed, and ``translate`` builds a new
    string of exactly that size — so the call's peak allocation is the work, read
    off an allocator that no scheduler can skew: 0.10 MB bounded, 4.0 MB with
    the bound lifted (darwin, py3.12). The input is built before tracing starts,
    so only the call's own allocations count.
    """

    import tracemalloc

    text = "x" * 4_000_000
    started_here = not tracemalloc.is_tracing()
    if started_here:
        tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        _flatten(text, limit=PANEL_ROW_MAX_CHARS)
        peak = tracemalloc.get_traced_memory()[1] - before
    finally:
        if started_here:
            tracemalloc.stop()
    assert peak < len(text) // 4, (
        f"_flatten allocated {peak / 1e6:.2f} MB at its peak for a "
        f"{len(text) / 1e6:.0f} MB input: the work is scaling with the input, not "
        f"with _MAX_INPUT_CHARS ({_MAX_INPUT_CHARS})"
    )


def test_a_group_of_one_draws_the_panel_and_writes_no_per_child_row() -> None:
    """ADR-0278 D1 + D2, superseding S10's "at N == 1 every surface is
    byte-identical to P2": a single delegation's group is ACTIVE, it draws the
    five-row block, and its member writes no statusline row."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=1)
    bridge.adopt("sub-0", _GROUP, index=0)
    snapshot = _reading(0, elapsed_ms=1_000)
    bridge(snapshot)

    assert ui.writes == []
    assert ui.widgets == [(PANEL_WIDGET_KEY, _drawn([snapshot]))]


def test_an_unadopted_child_keeps_its_own_row_while_a_group_is_open() -> None:
    """§3.6's second residual: the bridge learns membership only from ``adopt``,
    so a concurrent ``/agents run`` child correctly falls back to its own
    per-child row — byte-identical — rather than being counted into someone
    else's call. The group's own member writes none (ADR-0278 D2)."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_reading(0))
    loner = _progress(id="sub-loner", current_tool="grep")
    bridge(loner)

    assert ui.writes == [(status_key("sub-loner"), format_status_row(loner))]


def test_a_terminal_member_stays_on_the_panel_as_done() -> None:
    """The un-grouped path CLEARS the row on a terminal state. A grouped member
    must not leave the panel: "1 done · 1 running" is the fact the header
    exists to carry, and the row belongs to the group, not to the child."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    for index in range(2):
        bridge.adopt(f"sub-{index}", _GROUP, index=index)
        bridge(_reading(index))
    bridge(_member(0, state="done", elapsed_ms=4_000))

    key, lines = ui.widgets[-1]
    assert key == PANEL_WIDGET_KEY
    assert lines is not None
    header = _visible(lines[0])
    assert "1 running · 1 done" in header, header
    assert ui.writes == []


def test_the_bus_half_is_untouched_by_grouping() -> None:
    """``SubagentProgress`` gains no ``batch_id`` in P3 (§3.6 — the product-core
    delta is zero), so a subscriber still sees one START and one END per member,
    on the contract channels, grouped or not."""

    ui = _Ui()
    bridge, api = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    for index in range(2):
        bridge.adopt(f"sub-{index}", _GROUP, index=index)
        bridge(_member(index, state="running"))
        bridge(_member(index, state="done"))

    channels = [channel for channel, _ in api.events.emitted]
    assert channels.count(SUBAGENT_START) == 2
    assert channels.count(SUBAGENT_END) == 2


def test_end_group_blanks_the_panel_and_writes_no_row() -> None:
    """A panel outliving the delegation that owns it is a lie the user cannot
    dismiss. There is no aggregate row to clear any more (ADR-0278 D2)."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    for index in range(2):
        bridge.adopt(f"sub-{index}", _GROUP, index=index)
        bridge(_member(index, state="running"))

    bridge.end_group(_GROUP)
    assert ui.writes == []
    assert ui.widgets[-1] == (PANEL_WIDGET_KEY, None)


def test_end_group_cannot_blank_a_panel_it_does_not_own() -> None:
    """One widget key is safe rather than lucky — ``agent`` declares
    ``execution_mode="sequential"`` (``tool.py:894``) so two calls never have
    panels open at once. The ownership check is what keeps that a belt rather
    than a bet."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group("first", expected=2)
    bridge.begin_group("second", expected=2)
    bridge.adopt("sub-0", "second", index=0)
    bridge(_member(0, state="running"))
    ui.widgets.clear()

    bridge.end_group("first")
    assert ui.widgets == []


def test_ending_a_group_of_one_blanks_its_panel_and_never_touches_the_statusline() -> None:
    """S10's floor was that a group of one wrote exactly what no group wrote.
    ADR-0278 D1 replaced the floor with the panel; what this keeps is the part
    that still matters — the statusline is not touched at all, not even with a
    clear for a key it never held, and the panel goes when the call ends."""

    snapshots = [
        _reading(0, elapsed_ms=1_000),
        _member(0, state="done", elapsed_ms=2_000),
    ]

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=1)
    bridge.adopt("sub-0", _GROUP, index=0)
    for snapshot in snapshots:
        bridge(snapshot)
    bridge.end_group(_GROUP)

    assert ui.writes == []
    assert [content is None for _, content in ui.widgets] == [False, False, True]
    assert group_status_key(_GROUP) not in {key for key, _ in ui.writes}


def test_end_group_writes_nothing_for_an_active_group_that_never_rendered() -> None:
    """The failure-path twin: a batch that dies before its first snapshot.

    A consent refusal or a turn cancelled between ``begin_group`` and the first
    child leaves an ACTIVE group whose aggregate row was never written. Clearing
    a key the UI has never seen is a write with nothing behind it — and on a
    real ``chrome.set_status`` it is a repaint for a segment that does not
    exist.
    """

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=4)

    bridge.end_group(_GROUP)
    assert ui.writes == []
    assert ui.widgets == []


def test_end_group_mid_flight_still_blanks_the_panel_it_drew() -> None:
    """The guard is "did this group draw", not "is the group active" — a group
    closed mid-flight (the extension's ``finally`` after a failure) must not
    leave its panel behind."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=4)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_reading(0, elapsed_ms=1_000))
    assert [key for key, _ in ui.widgets] == [PANEL_WIDGET_KEY]

    bridge.end_group(_GROUP)
    assert ui.widgets[-1] == (PANEL_WIDGET_KEY, None)
    assert ui.writes == []


def test_reopening_a_leaked_group_key_still_blanks_the_stale_panel() -> None:
    """``begin_group`` on a live key ends the old group first. A stale panel
    surviving the re-open would be a lie about the PREVIOUS call shown over the
    new one."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_reading(0, elapsed_ms=1_000))
    written = len(ui.widgets)

    bridge.begin_group(_GROUP, expected=2)
    assert ui.widgets[written:] == [(PANEL_WIDGET_KEY, None)]


def test_clear_ends_every_open_group() -> None:
    """The ``session_shutdown`` / teardown half, including the panel."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_member(0, state="running"))
    ui.writes.clear()
    ui.widgets.clear()

    bridge.clear()
    assert ui.writes == []
    assert ui.widgets == [(PANEL_WIDGET_KEY, None)]


def test_an_unchanged_panel_is_not_rewritten() -> None:
    """Hundreds of publishes per turn must not become hundreds of UI writes —
    and ``chrome.set_widget`` calls ``invalidate()`` unconditionally
    (``chrome.py:1597-1603``), so an unchanged panel would be a full repaint per
    redraw."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    for _ in range(5):
        bridge(_reading(0, elapsed_ms=1_000))

    assert ui.writes == []
    assert len(ui.widgets) == 1


def test_headless_never_raises_on_a_group() -> None:
    """In print / json / rpc mode EVERY ``ui.*`` method raises, ``set_widget``
    included (``headless_ui.py:125-145``). The ``_ui()`` identity guard is what
    keeps a delegation from dying of a panel update."""

    bridge, _ = _bridge(HEADLESS_UI_CONTEXT)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_member(0, state="running"))
    bridge(_member(0, state="done"))
    bridge.end_group(_GROUP)
    bridge.clear()


def test_a_raising_set_widget_costs_the_panel_and_never_the_delegation() -> None:
    """The binding can flip between the guard and the call. When it does, the
    panel is what is lost — never the delegation, and no statusline row appears
    in its place (ADR-0278 D2)."""

    ui = _Ui()

    def _boom(key: str, content: list[str] | None, options: object = None) -> None:
        raise NotImplementedError("ExtensionUIContext.set_widget is not bound")

    ui.set_widget = _boom  # type: ignore[method-assign, assignment]
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_member(0, state="running"))
    bridge.end_group(_GROUP)

    assert ui.writes == []


# --- F2: the widget panel renders CHILD-AUTHORED text -------------------------
#
# THE HIGH FINDING, AND THE SURFACE IS NEW IN P3 (P2 shipped no widget at all).
# ``SubagentProgress.current_tool`` is set from the CHILD process's own stdout
# JSON — any non-empty ``str`` in ``tool_execution_start.tool_name``
# (``stream.py:1175-1177``) — and the kernel emits that event with the raw
# model-supplied name BEFORE ``_prepare_tool_call`` looks the tool up
# (``loop.py:739-749``), so it is not constrained to a real tool name. A child is
# exactly the process this phase's threat model assumes has read attacker
# controlled content (``consent._may_widen`` constraint 6).
#
# The far end applies no bound of its own: ``chrome._render_widget_lines``
# returns ``ANSI("\n".join(lines))`` (``chrome.py:1286``) into a
# ``Window(…, dont_extend_height=True)`` (``chrome.py:800-802``) — multi-row,
# ANSI-parsed, no height cap. Contrast the SIBLING surface, which is defended at
# the far end: ``chrome._render_status`` strips newlines because "this is a fixed
# height=1 chrome row" (``chrome.py:1216-1218``).
#
# Measured before the fix: 40 newlines in ``current_tool`` turned a 3-entry panel
# into 43 screen rows with raw ESC intact, pushing the transcript and the input
# line off screen; and a 5 000-character tool name produced a 5 021-character row
# while ``format_aggregate_status`` capped the same input at 31.

_HOSTILE_TOOL = "read\n" + "\n".join(f"\x1b[31m FAKE CHROME ROW {i}" for i in range(40))


def _hostile(index: int, **kwargs: Any) -> SubagentProgress:
    return _member(index, state="running", current_tool=_HOSTILE_TOOL, **kwargs)


def test_a_child_cannot_add_rows_to_the_panel() -> None:
    """The list length IS the screen height — ``chrome`` joins it with ``\\n``.

    So "returned N entries" is not the property that matters; "the rendered
    string is N rows" is. Asserted on the join, which is what ``chrome.py:1286``
    actually does — at the height fixed for two members (ADR-0278 D5).
    """

    lines = format_panel([_hostile(0), _member(1, state="running")])

    assert len(lines) == panel_height(2)
    assert "\n".join(lines).count("\n") == panel_height(2) - 1
    assert not any("\n" in line for line in lines)


def test_a_child_cannot_emit_escape_sequences_into_the_parents_chrome() -> None:
    """No ESC from the child means no SGR from the child — including ``\\x1b[8m``,
    which hides what follows. The panel writes its own SGR (ADR-0278 D4), so the
    assertion is on what is left once that vocabulary is taken out.
    """

    rendered = "\n".join(format_panel([_hostile(0), _hostile(1)]))
    plain = _visible(rendered)

    assert "\x1b" not in plain
    assert "\x9b" not in plain  # C1: the one-byte CSI
    assert "\r" not in plain  # overwriting a row in place


def test_every_panel_row_is_bounded_the_way_the_aggregate_already_was() -> None:
    """``format_aggregate_status`` capped at :data:`AGGREGATE_MAX_CHARS`; the
    panel's rows are capped at the width they are drawn into. Same input, same
    class of bound, measured on both — in CELLS, on what is visible."""

    huge = _member(0, state="running", current_tool="x" * 5000, last_text="y" * 5000)

    assert cell_len(format_aggregate_status([huge, huge])) <= AGGREGATE_MAX_CHARS
    for row in format_panel([huge, huge]):
        assert cell_len(_visible(row)) <= 80, row


def test_a_wide_character_row_is_bounded_in_CELLS_not_characters() -> None:
    """A child picks its own alphabet, and a Hangul syllable or an emoji is two
    columns and one character. SWEPT rather than sampled: a single sample once
    stayed green under the sabotage because the outer cut repaired it."""

    for alphabet in ("한", "🔥"):
        for n in range(2, 60):
            text = alphabet * n
            rows = format_panel(
                [_member(k, state="running", last_text=text, current_tool=text)
                 for k in range(8)],
                tasks=tuple(text for _ in range(8)),
            )
            for row in rows:
                assert cell_len(_visible(row)) <= 80, (alphabet, n, row)


def test_a_wide_character_panel_cannot_breach_the_height_ceiling() -> None:
    """The consequence rather than the measure: a row wider than the terminal
    wraps into two screen rows. MEASURED before the cell fix: nine entries became
    SEVENTEEN screen rows at 80 columns."""

    for alphabet in ("한", "🔥"):
        for n in (19, 28, 37, 46):
            text = alphabet * n
            lines = format_panel(
                [_member(k, state="running", last_text=text) for k in range(8)],
                tasks=tuple(text for _ in range(8)),
            )
            assert len(lines) == PANEL_MAX_ROWS
            screen_rows = sum(max(1, -(-cell_len(_visible(line)) // 80)) for line in lines)
            assert screen_rows == PANEL_MAX_ROWS, (alphabet, n, screen_rows)


def _wide_profile_batch(syllables: int) -> list[SubagentProgress | None]:
    """The widest aggregate this formatter can be asked for, in Hangul.

    Every state class non-zero and every trailing number at full width — the same
    shape ``test_the_aggregate_row_fits_a_shared_height_one_status_bar`` uses to
    pin the ASCII case, with the profile name swapped for one whose characters
    are two columns wide.
    """

    states = ["running", "starting", "running", "running", "done", "error", "stopped"]
    return [
        _member(
            index,
            profile="한" * syllables,
            state=state,
            elapsed_ms=999_000 if index == 0 else 1_000,
            tokens=987_654 if index == 4 else 0,
            cost=99.9999 if index == 6 else 0,
        )
        for index, state in enumerate(states)
    ] + [None]


def test_the_aggregate_row_is_bounded_in_cells_for_a_wide_profile_name() -> None:
    """The same unit error on the SHARED height-1 statusline row.

    ``_short_profile`` bounds the name to 16 cells, so a Hangul profile is eight
    characters and sixteen columns; the final cut measured characters and so let
    a row onto that shared row two columns past the ceiling. MEASURED against the
    sabotaged build at eight syllables: 80 cells.
    """

    for syllables in range(1, 24):
        text = format_aggregate_status(_wide_profile_batch(syllables))
        assert cell_len(text) <= AGGREGATE_MAX_CHARS, (syllables, cell_len(text), text)


def test_the_aggregate_drops_whole_segments_rather_than_cutting_a_number() -> None:
    """The join that appends the trailing numbers is measured in cells too, and
    this is what that half buys — it is invisible to a width assertion.

    ``format_aggregate_status`` appends ``elapsed``, ``tokens`` and ``cost``
    left to right "so the drop is always a suffix: losing the cost while keeping
    the elapsed reads as terseness; the reverse reads as a bug". A codepoint
    measure admits a segment the cell measure would refuse, and the final cut
    then slices it mid-number. MEASURED at four syllables: the fixed build ends
    ``· 1 queued`` at 72 cells; the sabotaged one ends ``· 99…``.
    """

    text = format_aggregate_status(_wide_profile_batch(4))
    assert not text.endswith(_ELLIPSIS), text
    assert text.endswith("1 queued"), text


def test_an_ascii_batch_measures_the_same_in_cells_and_characters() -> None:
    """``cell_len`` and ``len`` agree on ASCII, so the cell measure costs an
    ordinary batch nothing: every visible row is as many cells as characters,
    the panel's own one-cell glyphs included."""

    snapshots: list[SubagentProgress | None] = [
        _reading(0, elapsed_ms=1_000, tokens=1_500),
        _member(1, state="done", elapsed_ms=2_000, cost=0.0031),
        None,
    ]
    for row in _panel(snapshots, tasks=("alpha", "bravo", "charlie")):
        assert cell_len(row) == len(row), row


def test_a_hostile_profile_name_cannot_grow_the_panel_header() -> None:
    """The panel does NOT get the newline stripping ``chrome._render_status``
    gives the statusline. A profile name comes from a filename, so it may contain
    ``\\n`` — and a 16-character one passes a length cap while still adding rows.
    """

    evil = _member(0, state="running", profile="a\nb\nc\nd\x1b[8m")
    for snapshots in ([evil], [evil, _member(1, state="running")]):
        lines = format_panel(snapshots, profile="a\nb\x1b[8m")
        assert len(lines) == 5
        assert "\n" not in "".join(lines)
        assert "\x1b" not in _visible("".join(lines))


def test_the_panel_is_bounded_in_HEIGHT_and_says_what_it_dropped() -> None:
    """The second dimension. ``tool.py:676-682`` refuses a ninth task, so this
    can only fire on a bug — but the window it feeds has no height cap at all,
    and a silently SHORTER panel reads as "those children were dropped"."""

    lines = format_panel([_member(k, state="running") for k in range(40)])

    assert len(lines) == PANEL_MAX_ROWS
    last = _visible(lines[-1])
    assert last.startswith("│ …"), last
    assert "more" in last


def test_a_legal_eight_member_batch_is_never_truncated() -> None:
    """The height cap must not fire on anything a model can actually get past
    the door — ``MAX_PARALLEL_TASKS`` is 8, and 1 header + 8 one-row members is
    the cap (ADR-0278 D4's compact layout)."""

    lines = [_visible(line) for line in format_panel([_member(k, state="running") for k in range(8)])]

    assert len(lines) == 9
    assert "more" not in lines[-1]
    for index in range(8):
        assert any(line.startswith(f"│ ● {index + 1} ") for line in lines), lines


def test_the_call_renderer_is_safe_on_its_own_not_only_via_format_panel() -> None:
    """BOTH layers, pinned separately — because one masks the other.

    :func:`format_panel` cuts every row as a whole (``_Line.render``), so
    removing the bound inside the function that touches a child's call bytes
    changes nothing it can observe. That function is ``panel._call_text`` since
    ADR-0278 (it replaced ``_panel_row``, which an older citation here named),
    and the next consumer of a rendered call will call it rather than re-derive
    it — so it must carry the bound itself.
    """

    from aelix_coding_agent.subagent_contract import SubagentToolActivity

    for limit in (1, 12, 40, 80):
        text = panel_module._call_text(
            SubagentToolActivity(name=_HOSTILE_TOOL, args=_HOSTILE_TOOL), limit
        )
        assert "\n" not in text and "\x1b" not in text, text
        assert cell_len(text) <= limit, (limit, text)
    assert panel_module._call_text(SubagentToolActivity(name="bash", args="ls -la"), 80) == "$ ls -la"
