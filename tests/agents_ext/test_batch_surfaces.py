"""The three S10 surfaces, the H10 throttle, and the batch group — ADR-0199.

Pure unit level. Every layout here is a function of a hand-built list of
snapshots, and the throttle runs on an injected clock rather than a ``sleep``,
so this file has no processes, no event loop timing and nothing to flake on.

WHAT THIS FILE CANNOT SEE, AND WHO SEES IT. A bridge whose adoption never
matched would write four ``status_key(id)`` rows, ``chrome._render_status`` would
``"  ".join`` them into a height-1 row (``tui/chrome.py:1036-1047``), and every
assertion below over synthetic snapshots would still pass. That is why
:func:`test_a_four_member_group_writes_exactly_one_status_key` drives the real
bridge through the real ``begin_group`` / ``adopt`` / ``__call__`` sequence, and
why WP-6 additionally drives a real batch end to end.
"""

from __future__ import annotations

from typing import Any

from aelix_agents.panel import (
    AGGREGATE_MAX_CHARS,
    PANEL_MAX_ROWS,
    PANEL_MIN_CHILDREN,
    PANEL_ROW_MAX_CHARS,
    PANEL_WIDGET_KEY,
    PARTIAL_MIN_INTERVAL_MS,
    PartialThrottle,
    _panel_row,
    format_aggregate_status,
    format_card,
    format_panel,
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
    (``subagent_contract.py:95-96``), so summing it across members would report
    a number several times the real one — the mistake ``stream.py:207-210``
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
    two-space join at ``chrome.py:1047``, and a row that cannot name the agent
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


def test_no_panel_below_two_children() -> None:
    """S10: a single delegation keeps P2's behaviour EXACTLY, and P2 had no
    panel."""

    assert PANEL_MIN_CHILDREN == 2
    assert format_panel([_member(0)]) == []
    assert format_panel([]) == []


def test_the_panel_is_the_aggregate_header_plus_one_row_per_child() -> None:
    """The header IS :func:`format_aggregate_status`, not a second spelling of
    the same facts — the panel and the statusline can then never disagree."""

    snapshots: list[SubagentProgress | None] = [
        _reading(0, elapsed_ms=1_000, tokens=1_500),
        _member(1, state="done", elapsed_ms=2_000, cost=0.0031),
        None,
    ]

    lines = format_panel(snapshots)
    assert lines[0] == format_aggregate_status(snapshots)
    assert lines[1:] == [
        "[1/3] running · read · 1s · 1.5k tok",
        "[2/3] done · 2s · $0.0031",
        "[3/3] queued",
    ]


def test_the_panel_rows_lead_with_the_childs_model_when_present() -> None:
    """S10 Option A: the model leads each per-child row, so the ``· state`` that
    follows lines up across a one-profile batch (all members share one model —
    the "light alignment"). A queued member has no snapshot and so no model."""

    snapshots: list[SubagentProgress | None] = [
        _reading(0, model="claude-opus-4-8", elapsed_ms=1_000, tokens=1_500),
        _member(
            1, model="claude-opus-4-8", state="done", elapsed_ms=2_000, cost=0.0031
        ),
        None,
    ]

    lines = format_panel(snapshots)
    assert lines[1:] == [
        "[1/3] claude-opus-4-8 · running · read · 1s · 1.5k tok",
        "[2/3] claude-opus-4-8 · done · 2s · $0.0031",
        "[3/3] queued",
    ]


def test_a_model_less_panel_row_omits_the_model_term() -> None:
    """``model`` is ``None`` until the child's first ``message_end`` resolves the
    run model. The term is OMITTED then — never ``None``, never a dangling
    separator — so the row is byte-identical to what P3 shipped before the model
    was carried."""

    row = _panel_row(
        _member(0, state="running", current_tool="read", elapsed_ms=1_000)
    )
    assert row == "running · read · 1s"
    assert "None" not in row


def test_a_hostile_model_cannot_break_a_panel_row() -> None:
    """``model`` is child-authored, exactly like ``current_tool``, so it goes
    through the same :func:`_flatten`: no newline to add a chrome row, no ESC to
    smuggle an SGR, and bounded width — measured on the row builder AND through
    :func:`format_panel`, the two layers finding F2 pins separately."""

    hostile = "gpt\n" + "\n".join(f"\x1b[31m ROW {i}" for i in range(40)) + "x" * 5000
    row = _panel_row(_member(0, state="running", model=hostile))
    assert "\n" not in row
    assert "\x1b" not in row
    assert "\x9b" not in row  # C1: the one-byte CSI

    lines = format_panel(
        [_member(0, state="running", model=hostile), _member(1, state="running")]
    )
    assert not any("\n" in line for line in lines)
    for line in lines:
        assert len(line) <= max(PANEL_ROW_MAX_CHARS, AGGREGATE_MAX_CHARS), line


# === the H10 throttle =========================================================


def test_the_throttle_drops_mid_stream_frames_but_never_a_state_change() -> None:
    """H10: the kernel appends an ``asyncio.Task`` per ``on_partial`` into a list
    it never prunes (``loop.py:581``, ``:608``) and ``loop.py`` may not be
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
    statusline half already does (``progress.py:203-205``)."""

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
    api = _Api(ui)
    return SubagentProgressBridge(api), api


def test_a_four_member_group_writes_exactly_one_status_key() -> None:
    """R11, arriving through the seam surface 1 exists to prevent.

    A bridge whose adoption never matched would write four ``status_key(id)``
    rows and ``_render_status`` would join them into a height-1 row; every
    synthetic-snapshot assertion above would still pass. So: exactly ONE key,
    and it is the group's.
    """

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=4)
    for index in range(4):
        bridge.adopt(f"sub-{index}", _GROUP, index=index)
        bridge(_reading(index, elapsed_ms=1_000))

    assert {key for key, _ in ui.writes} == {group_status_key(_GROUP)}
    # The panel is re-rendered as each member appears — one KEY, N contents.
    assert {key for key, _ in ui.widgets} == {PANEL_WIDGET_KEY}
    assert ui.widgets[-1][1] == format_panel(
        [_reading(index, elapsed_ms=1_000) for index in range(4)]
    )


def test_a_group_of_one_keeps_p2s_per_child_row_and_no_panel() -> None:
    """S10: at N == 1 every surface is byte-identical to P2. The group is still
    opened and closed so the extension's ``try``/``finally`` is symmetric."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=1)
    bridge.adopt("sub-0", _GROUP, index=0)
    snapshot = _reading(0, elapsed_ms=1_000)
    bridge(snapshot)

    assert ui.writes == [(status_key("sub-0"), format_status_row(snapshot))]
    assert ui.widgets == []


def test_an_unadopted_child_keeps_its_own_row_while_a_group_is_open() -> None:
    """§3.6's second residual: the bridge learns membership only from ``adopt``,
    so a concurrent ``/agents run`` child correctly falls back to its own
    per-child row rather than being counted into someone else's batch."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_reading(0))
    bridge(_progress(id="sub-loner", current_tool="grep"))

    assert {key for key, _ in ui.writes} == {
        group_status_key(_GROUP),
        status_key("sub-loner"),
    }


def test_a_terminal_member_stays_on_the_aggregate_as_done() -> None:
    """The un-grouped path CLEARS the row on a terminal state. A grouped member
    must not: "1 done · 1 running" is the fact the aggregate exists to carry, and
    the row belongs to the group, not to the child."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    for index in range(2):
        bridge.adopt(f"sub-{index}", _GROUP, index=index)
        bridge(_reading(index))
    bridge(_member(0, state="done", elapsed_ms=4_000))

    key, text = ui.writes[-1]
    assert key == group_status_key(_GROUP)
    assert text is not None
    assert "1 done" in text
    assert "1 running" in text


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


def test_end_group_clears_both_the_aggregate_row_and_the_panel() -> None:
    """A statusline segment — or a panel — outliving the delegation that owns it
    is a lie the user cannot dismiss."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    for index in range(2):
        bridge.adopt(f"sub-{index}", _GROUP, index=index)
        bridge(_member(index, state="running"))

    bridge.end_group(_GROUP)
    assert ui.writes[-1] == (group_status_key(_GROUP), None)
    assert ui.widgets[-1] == (PANEL_WIDGET_KEY, None)


def test_end_group_cannot_blank_a_panel_it_does_not_own() -> None:
    """One widget key is safe rather than lucky — ``agent`` declares
    ``execution_mode="sequential"`` (``tool.py:281-287``) so two calls never have
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


def test_ending_a_group_of_one_is_byte_identical_to_opening_no_group() -> None:
    """S10's floor, measured rather than asserted in prose.

    ``_clear_row`` issues its ``ui.set_status(key, None)`` UNCONDITIONALLY — the
    ``_rows`` pop is a dedup-cache eviction, not a guard — so ``end_group`` used
    to emit a clear for the aggregate key of a group that never wrote a row. At
    ``expected=1`` the group is inactive and renders nothing, which made that
    stray write the ONLY difference between "one delegation inside a group" and
    "one delegation, P2". The comparison is against a second bridge driven with
    no group at all, because "byte-identical to P2" is the actual claim and a
    hand-written expected list would only restate this bridge's opinion of it.
    """

    snapshots = [
        _reading(0, elapsed_ms=1_000),
        _member(0, state="done", elapsed_ms=2_000),
    ]

    grouped_ui = _Ui()
    grouped, _ = _bridge(grouped_ui)
    grouped.begin_group(_GROUP, expected=1)
    grouped.adopt("sub-0", _GROUP, index=0)
    for snapshot in snapshots:
        grouped(snapshot)
    grouped.end_group(_GROUP)

    p2_ui = _Ui()
    p2, _ = _bridge(p2_ui)
    for snapshot in snapshots:
        p2(snapshot)

    assert grouped_ui.writes == p2_ui.writes
    assert grouped_ui.widgets == p2_ui.widgets == []
    # And, explicitly: no key of the group's shape was ever touched.
    assert group_status_key(_GROUP) not in {key for key, _ in grouped_ui.writes}


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


def test_end_group_still_clears_an_aggregate_row_it_did_write() -> None:
    """The guard must be "was it written", not "is the group active".

    The whole point of the aggregate row is that it is cleared when the batch
    ends; a guard that over-fired here would trade a cosmetic stray write for an
    orphaned segment the user cannot dismiss. Driven one member at a time so the
    row exists but the group is still mid-flight when it is closed — the
    cancelled-turn shape, where ``end_group`` runs from the extension's
    ``finally`` rather than after a clean batch.
    """

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=4)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_reading(0, elapsed_ms=1_000))
    assert [key for key, _ in ui.writes] == [group_status_key(_GROUP)]

    bridge.end_group(_GROUP)
    assert ui.writes[-1] == (group_status_key(_GROUP), None)
    assert ui.widgets[-1] == (PANEL_WIDGET_KEY, None)


def test_reopening_a_leaked_group_key_still_clears_the_stale_row() -> None:
    """``begin_group`` on a live key ends the old group first, and that path
    goes through the same guard. A stale aggregate row surviving the re-open
    would be a lie about the PREVIOUS batch shown over the new one."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_reading(0, elapsed_ms=1_000))
    written = len(ui.writes)

    bridge.begin_group(_GROUP, expected=2)
    assert ui.writes[written:] == [(group_status_key(_GROUP), None)]


def test_clear_ends_every_open_group() -> None:
    """The ``session_shutdown`` / teardown half, now including the panel."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_member(0, state="running"))
    ui.writes.clear()
    ui.widgets.clear()

    bridge.clear()
    assert ui.writes == [(group_status_key(_GROUP), None)]
    assert ui.widgets == [(PANEL_WIDGET_KEY, None)]


def test_an_unchanged_group_row_is_not_rewritten() -> None:
    """Hundreds of publishes per turn must not become hundreds of UI writes —
    and ``chrome.set_widget`` calls ``invalidate()`` unconditionally
    (``chrome.py:1373-1380``), so an unchanged panel would be a full repaint per
    reduced stdout line."""

    ui = _Ui()
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    for _ in range(5):
        bridge(_reading(0, elapsed_ms=1_000))

    assert len(ui.writes) == 1
    assert len(ui.widgets) == 1


def test_headless_never_raises_on_a_group() -> None:
    """In print / json / rpc mode EVERY ``ui.*`` method raises, ``set_widget``
    included (``headless_ui.py:126-144``). The ``_ui()`` identity guard is what
    keeps a delegation from dying of a panel update."""

    bridge, _ = _bridge(HEADLESS_UI_CONTEXT)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_member(0, state="running"))
    bridge(_member(0, state="done"))
    bridge.end_group(_GROUP)
    bridge.clear()


def test_a_raising_set_widget_still_leaves_the_aggregate_row_on_screen() -> None:
    """The binding can flip between the guard and the call. When it does, the
    panel is what is lost — not the statusline row, and never the delegation."""

    ui = _Ui()

    def _boom(key: str, content: list[str] | None, options: object = None) -> None:
        raise NotImplementedError("ExtensionUIContext.set_widget is not bound")

    ui.set_widget = _boom  # type: ignore[method-assign, assignment]
    bridge, _ = _bridge(ui)
    bridge.begin_group(_GROUP, expected=2)
    bridge.adopt("sub-0", _GROUP, index=0)
    bridge(_member(0, state="running"))

    assert [key for key, _ in ui.writes] == [group_status_key(_GROUP)]


# --- F2: the widget panel renders CHILD-AUTHORED text -------------------------
#
# THE HIGH FINDING, AND THE SURFACE IS NEW IN P3 (P2 shipped no widget at all).
# ``SubagentProgress.current_tool`` is set from the CHILD process's own stdout
# JSON — any non-empty ``str`` in ``tool_execution_start.tool_name``
# (``stream.py:462-464``) — and the kernel emits that event with the raw
# model-supplied name BEFORE ``_prepare_tool_call`` looks the tool up
# (``loop.py:717-723``), so it is not constrained to a real tool name. A child is
# exactly the process this phase's threat model assumes has read attacker
# controlled content (``consent._may_widen`` constraint 6).
#
# The far end applies no bound of its own: ``chrome._render_widget_lines``
# returns ``ANSI("\n".join(lines))`` (``chrome.py:1106``) into a
# ``Window(…, dont_extend_height=True)`` (``chrome.py:655``) — multi-row,
# ANSI-parsed, no height cap. Contrast the SIBLING surface, which is defended at
# the far end: ``chrome._render_status`` strips newlines because "this is a fixed
# height=1 chrome row" (``chrome.py:1036``).
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

    So "returned 3 entries" is not the property that matters; "the rendered
    string is 3 rows" is. Asserted on the join, which is what ``chrome.py:1106``
    actually does.
    """

    lines = format_panel([_hostile(0), _member(1, state="running")])

    assert len(lines) == 3  # header + two members
    assert "\n".join(lines).count("\n") == 2
    assert not any("\n" in line for line in lines)


def test_a_child_cannot_emit_escape_sequences_into_the_parents_chrome() -> None:
    """No ESC means no SGR — including ``\\x1b[8m``, which hides what follows.

    That is the same primitive the F1 consent forgery used, and here it would be
    aimed at the parent's own chrome rather than at a modal.
    """

    rendered = "\n".join(format_panel([_hostile(0), _hostile(1)]))

    assert "\x1b" not in rendered
    assert "\x9b" not in rendered  # C1: the one-byte CSI
    assert "\r" not in rendered  # overwriting a row in place


def test_every_panel_row_is_bounded_the_way_the_aggregate_already_was() -> None:
    """``format_aggregate_status`` capped at :data:`AGGREGATE_MAX_CHARS`; the
    panel capped nothing. Same input, same class of bound, measured on both."""

    huge = _member(0, state="running", current_tool="x" * 5000)

    assert len(format_aggregate_status([huge, huge])) <= AGGREGATE_MAX_CHARS
    for row in format_panel([huge, huge]):
        assert len(row) <= max(PANEL_ROW_MAX_CHARS, AGGREGATE_MAX_CHARS), row


def test_a_hostile_profile_name_cannot_grow_the_panel_header() -> None:
    """The header is :func:`format_aggregate_status`, and the panel does NOT get
    the newline stripping ``chrome._render_status`` gives the statusline.

    A profile name comes from a filename, so it may contain ``\\n`` — and a
    16-character one passes the length cap while still adding rows.
    """

    evil = _member(0, state="running", profile="a\nb\nc\nd\x1b[8m")
    lines = format_panel([evil, _member(1, state="running")])

    assert len(lines) == 3
    assert "\n" not in "".join(lines)
    assert "\x1b" not in "".join(lines)


def test_the_panel_is_bounded_in_HEIGHT_and_says_what_it_dropped() -> None:
    """The second dimension. ``tool.py:341-344`` refuses a ninth task, so this
    can only fire on a bug — but the window it feeds has no height cap at all,
    and a silently SHORTER panel reads as "those children were dropped"."""

    lines = format_panel([_member(k, state="running") for k in range(40)])

    assert len(lines) == PANEL_MAX_ROWS
    assert lines[-1].startswith("…")
    assert "more" in lines[-1]


def test_a_legal_eight_member_batch_is_never_truncated() -> None:
    """The height cap must not fire on anything a model can actually get past
    the door — ``MAX_PARALLEL_TASKS`` is 8, and 1 header + 8 rows is the cap."""

    lines = format_panel([_member(k, state="running") for k in range(8)])

    assert len(lines) == 9
    assert not lines[-1].startswith("…")
    for index in range(8):
        assert any(line.startswith(f"[{index + 1}/8] ") for line in lines)


def test_the_row_builder_is_safe_on_its_own_not_only_via_format_panel() -> None:
    """BOTH layers, pinned separately — because one masks the other.

    :func:`format_panel` flattens the composed row, so removing the bound inside
    ``_panel_row`` changes nothing that :func:`format_panel` can observe: the
    mutation survives every test above. It is still worth having and still worth
    pinning. ``_panel_row`` is the function that touches the child's bytes
    (``panel.py:281``, the site the finding names), and the next consumer of a
    per-child row — a card variant, a future surface — will call it rather than
    re-deriving it, and must inherit the bound rather than have to remember it.
    """

    row = _panel_row(_hostile(0))

    assert "\n" not in row
    assert "\x1b" not in row
    # The tool name alone is bounded, before anything downstream composes it
    # with the elapsed / token / cost suffixes.
    assert len(_panel_row(_member(0, current_tool="x" * 5000))) <= (
        PANEL_ROW_MAX_CHARS + 40
    )
