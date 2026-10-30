"""#132 — the delegation panel: layouts, the reducer behind them, and the bridge
that draws them (ADR-0278 D3-D7).

Three layers, each pinned on its own:

* the PURE layouts (``panel.format_panel``, ``panel.panel_height``) against
  hand-built snapshots — the mockups of D4 and D7 row for row, then the bounds
  every frame must keep at every width: no row wider than the width it was
  given, never a row more or fewer than the height fixed when the call began,
  no escape the panel did not write itself, whatever a child or the parent
  model put in a field;
* the REDUCER (``stream``): the bounded ``recent`` window that keeps moving past
  the trail's 200 calls, the turn counter that tells an open call from one whose
  end will never come, and ``last_text``;
* the BRIDGE (``progress.SubagentProgressBridge``) against a fake loop and a
  fake clock: nothing is drawn from the publish path, at most one redraw per
  250 ms, a 1 s tick while a member is live, every handle cancelled when the
  group ends, and the Esc frame that stays until it is dismissed.

The wiring through the real extension is ``test_delegation_panel_wiring_132.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import chain as chain_module
from aelix_agents import panel as panel_module
from aelix_agents.panel import (
    PANEL_MAX_ROWS,
    PANEL_MIN_CHILDREN,
    PANEL_SINGLE_ROWS,
    PANEL_WIDGET_KEY,
    format_panel,
    panel_height,
)
from aelix_agents.progress import (
    RENDER_MIN_INTERVAL_S,
    TICK_INTERVAL_S,
    SubagentProgressBridge,
    format_status_row,
    group_status_key,
    status_key,
)
from aelix_agents.stream import (
    ACTIVITY_MAX,
    LAST_TEXT_MAX_CHARS,
    MAX_TRAIL_ENTRIES,
    _StreamState,
    activity_of,
    reduce_event,
)
from aelix_ai.utils.terminal_text import contains_steering_chars
from aelix_coding_agent.extensions.headless_ui import HEADLESS_UI_CONTEXT
from aelix_coding_agent.subagent_contract import (
    CONTRACT_VERSION,
    SubagentProgress,
    SubagentResult,
    SubagentToolActivity,
    SubagentUsage,
)
from rich.cells import cell_len

from tests.agents_ext.test_events_and_statusline import _Api, _Ui

_SGR = re.compile(r"\x1b\[[0-9;]*m")
_OUR_SGR = {
    "\x1b[0m",
    "\x1b[2m",
    "\x1b[2;3m",
    "\x1b[31m",
    "\x1b[32m",
    "\x1b[33m",
    "\x1b[36m",
}


def _visible(line: str) -> str:
    return _SGR.sub("", line)


def _screen(lines: list[str]) -> list[str]:
    return [_visible(line).rstrip() for line in lines]


def _flush(left: str, right: str, width: int = 80) -> str:
    """``left`` and ``right`` flush against the two edges of ``width`` cells —
    the expected rows are written this way so a hand-counted run of spaces
    cannot be what a test pins."""

    return left + " " * (width - cell_len(left) - cell_len(right)) + right


def _call(name: str, args: str = "", status: str = "ok") -> SubagentToolActivity:
    return SubagentToolActivity(name=name, args=args, status=status)  # type: ignore[arg-type]


def _snap(index: int = 0, **kwargs: Any) -> SubagentProgress:
    base: dict[str, Any] = {
        "id": f"sub-{index}",
        "profile": "worker",
        "state": "running",
        "model": "s-1",
    }
    base.update(kwargs)
    return SubagentProgress(**base)


_TASKS = (
    "Read notes.txt and wait six seconds, then report.",
    "Wait ten seconds, echo bee, then report.",
    "Write c-out.txt, wait fourteen seconds, then report.",
)


def _three() -> list[SubagentProgress | None]:
    """D4's N >= 2 mockup, as snapshots."""

    return [
        _snap(
            0,
            elapsed_ms=6_000,
            tokens=400,
            activity=(_call("read", "notes.txt"), _call("bash", "sleep 6", "running")),
        ),
        _snap(
            1,
            elapsed_ms=8_000,
            tokens=400,
            activity=(_call("bash", "sleep 10"), _call("bash", "echo bee")),
            last_text="Waiting ten seconds before echoing bee.",
        ),
        _snap(
            2,
            state="done",
            elapsed_ms=14_000,
            tokens=400,
            activity=(
                _call("write", "c-out.txt"),
                _call("bash", "sleep 14"),
                _call("bash", "echo done"),
            ),
            last_text="Wrote c-out.txt and waited fourteen seconds.",
        ),
    ]


def _one() -> SubagentProgress:
    """D4's N = 1 mockup, as a snapshot."""

    return _snap(
        elapsed_ms=65_000,
        tokens=1_500,
        activity=(
            _call("read", "notes.txt"),
            _call("bash", "pytest -q tests/test_retry.py", "failed"),
            _call("bash", "sleep 12", "running"),
        ),
        last_text="Read the notes; one test fails, re-running the slow step.",
    )


# === the contract (D3) ========================================================


def test_the_new_progress_fields_are_additive_and_defaulted() -> None:
    """A snapshot built the old way still builds, and the contract version did
    not move — the precedent ``model``/``provider`` set."""

    old = SubagentProgress(id="sub-1", profile="scout", state="running")
    assert old.activity == ()
    assert old.last_text is None
    assert SubagentToolActivity(name="read").status == "running"
    assert CONTRACT_VERSION == 1


# === the layouts: D4's mockups ================================================


def test_one_child_is_the_five_row_block_of_the_mockup() -> None:
    lines = format_panel([_one()], width=80, idle_ms=[7_000])
    assert _screen(lines) == [
        _flush("│ ● agent worker · s-1 · running", "idle 7s · 1m12s · 1.5k tok"),
                "│   ✓ read notes.txt",
                "│   ✗ $ pytest -q tests/test_retry.py",
                "│   ▶ $ sleep 12 · idle 7s",
                "│   “Read the notes; one test fails, re-running the slow step.”",
    ]


def test_one_child_fills_its_call_rows_top_down_and_keeps_its_height() -> None:
    """Rows not yet filled are the gutter alone — the block is five rows before
    the first call as after the last (D5)."""

    starting = _snap(state="starting", elapsed_ms=0)
    first = _snap(elapsed_ms=900, activity=(_call("read", "notes.txt", "running"),))
    assert _screen(format_panel([starting], width=80)) == [
        _flush("│ ● agent worker · s-1 · starting", "0s"),
        "│",
        "│",
        "│",
                "│   waiting for the first reply",
    ]
    assert _screen(format_panel([first], width=80)) == [
        _flush("│ ● agent worker · s-1 · running", "0s"),
                "│   ▶ read notes.txt",
        "│",
        "│",
                "│   waiting for the first reply",
    ]


def test_a_resume_says_so_and_a_thinking_child_says_thinking() -> None:
    thinking = _snap(
        elapsed_ms=3_000, activity=(_call("read", "a.md"),), last_text="Looking at a.md."
    )
    header = _screen(format_panel([thinking], width=80, resumed=True))[0]
    assert header.startswith("│ ● agent worker (resumed) · s-1 · thinking"), header


def test_two_to_four_children_are_a_header_and_two_rows_each() -> None:
    lines = format_panel(
        _three(), tasks=_TASKS, width=80, idle_ms=[4_000, 6_000, 0], profile="worker"
    )
    assert _screen(lines) == [
        _flush("│ 3 agents · worker · s-1 · 2 running · 1 done", "14s · 0.4k tok"),
        _flush("│ ● 1  Read notes.txt and wait six seconds, then report.", "10s · 0.4k"),
                "│      ✓ ▶ $ sleep 6",
        _flush("│ ● 2  Wait ten seconds, echo bee, then report.", "14s · 0.4k"),
                "│      ✓✓ thinking · idle 6s · “Waiting ten seconds before echoing bee.”",
        _flush("│ ✓ 3  Write c-out.txt, wait fourteen seconds, then report.", "14s · 0.4k"),
                "│      ✓✓✓ “Wrote c-out.txt and waited fourteen seconds.”",
    ]


def test_a_chain_names_itself_its_placeholder_and_the_step_it_waits_for() -> None:
    tasks = (
        "Wait six seconds and hand off.",
        "Using this hand-off: {previous} -- and say {{previous}} literally.",
    )
    lines = _screen(
        format_panel(
            [_snap(0, activity=(_call("bash", "sleep 6", "running"),)), None],
            tasks=tasks,
            width=100,
            mode="chain",
            profile="worker",
        )
    )
    assert lines[0].startswith("│ chain of 2 · worker · s-1 · 1 running · 1 queued"), lines[0]
    assert lines[3] == (
        "│ ○ 2  Using this hand-off: ‹previous› -- and say {previous} literally."
    )
    assert lines[4] == "│      waiting for step 1"


def test_the_placeholder_spellings_are_the_chain_modules() -> None:
    """Restated in ``panel`` so it imports nothing from ``aelix_agents``; if
    ``chain`` ever respells the token, this goes red rather than the label."""

    assert panel_module._PREVIOUS_TOKEN == chain_module.PREVIOUS_TOKEN
    assert panel_module._PREVIOUS_ESCAPE == chain_module.PREVIOUS_ESCAPE


def test_a_queued_parallel_member_is_named_and_waits_to_start() -> None:
    lines = _screen(
        format_panel([_snap(0), None], tasks=("alpha", "bravo"), width=80, profile="worker")
    )
    assert lines[3] == "│ ○ 2  bravo"
    assert lines[4] == "│      waiting to start"
    assert "1 queued" in lines[0]


def test_a_call_whose_end_never_came_reads_no_result() -> None:
    cut = _snap(
        state="stopped",
        elapsed_ms=4_000,
        activity=(_call("write", "notes.md"), _call("bash", "sleep 97", "no_result")),
    )
    lines = _screen(format_panel([cut], width=80))
    assert lines[2] == "│   ■ $ sleep 97 · no result"
    both = _screen(format_panel([cut, _snap(1)], tasks=("a", "b"), width=80))
    assert both[2] == "│      ✓ stopped · ■ $ sleep 97 · no result"


def test_members_that_disagree_on_the_model_each_name_theirs() -> None:
    mixed = [_snap(0, model="gpt-5", elapsed_ms=1_000), _snap(1, model="claude-opus-5")]
    lines = _screen(format_panel(mixed, tasks=("alpha", "bravo"), width=80))
    assert "gpt-5" not in lines[0] and "claude-opus-5" not in lines[0], lines[0]
    assert lines[1].endswith("gpt-5 · 1s"), lines[1]
    assert lines[3].endswith("claude-opus-5 · 0s"), lines[3]
    agreeing = _screen(format_panel([_snap(0), _snap(1)], tasks=("a", "b"), width=80))
    assert " · s-1 · " in agreeing[0]
    assert not any("s-1" in row for row in agreeing[1:])


def test_five_or_more_children_are_one_row_each() -> None:
    snapshots = [*_three(), None, None]
    tasks = (*_TASKS, "d", "e")
    lines = format_panel(snapshots, tasks=tasks, width=80, height=panel_height(5))
    assert len(lines) == 6
    shown = _screen(lines)
    assert shown[1].startswith("│ ● 1 Read notes.txt"), shown[1]
    assert "▶ $ sleep 6" in shown[1]
    assert shown[1].endswith(" 6s")
    assert shown[4].startswith("│ ○ 4 d ") and shown[4].endswith("waiting to start"), shown[4]
    # One label column for every row, so the marks and states line up.
    assert shown[1].index("✓ ▶") == shown[4].index("waiting") == shown[5].index("waiting")


def test_a_cut_label_keeps_the_end_that_tells_tasks_apart() -> None:
    """A fan-out over one verb differs at its END; a label cut in the middle
    keeps it (from 24 cells — below that both ends say too little)."""

    fit = panel_module._fit_label
    jobs = [f"port the retry backoff to {name}" for name in ("google", "openai", "azure")]
    for limit in (24, 28, 31):
        shown = [fit(job, limit) for job in jobs]
        assert all(cell_len(label) <= limit for label in shown), shown
        for name, label in zip(("google", "openai", "azure"), shown, strict=True):
            assert label.endswith(name) and label.startswith("port"), (limit, label)
    assert fit("port the retry backoff to google", 20) == "port the retry back…"
    assert fit("short", 20) == "short"


def test_labels_do_not_move_when_one_member_s_number_gains_a_digit() -> None:
    """The labels share ONE numbers column, sized from every member's numbers:
    a member going from 9 s to 10 s does not re-cut its own label while the
    others stand still (the jitter ADR-0227 measured on the old label column)."""

    def labels(first_ms: int) -> list[str]:
        snapshots = [
            _snap(0, elapsed_ms=first_ms, tokens=400),
            _snap(1, elapsed_ms=14_000, tokens=400),
            _snap(2, elapsed_ms=3_000, tokens=400),
        ]
        rows = _screen(format_panel(snapshots, tasks=_TASKS, width=60))
        # ``<label>  <elapsed> · <tokens>``: drop the numbers, keep the label.
        return [row.split(" · ")[0].rsplit(" ", 1)[0].rstrip() for row in rows[1::2]]

    before, after = labels(9_000), labels(10_000)
    assert any("…" in label for label in before), before  # the cut is under test
    assert before == after


def test_the_compact_label_column_ignores_what_members_are_doing() -> None:
    """Sized from the labels and the width only, so a member starting a long
    call does not drag every label with it."""

    tasks = (*_TASKS, "d", "e")
    idle = [_snap(k, state="starting") for k in range(5)]
    busy = [
        _snap(k, activity=(_call("bash", "x" * 80, "running"),), last_text="y" * 80)
        for k in range(5)
    ]
    for width in (40, 60, 80):
        a = _screen(format_panel(idle, tasks=tasks, width=width, height=6))
        b = _screen(format_panel(busy, tasks=tasks, width=width, height=6))
        for row_a, row_b in zip(a[1:], b[1:], strict=True):
            assert row_a[:6] == row_b[:6]
            label_a = row_a[6:].split("  ")[0]
            label_b = row_b[6:].split("  ")[0]
            assert label_a == label_b, (width, row_a, row_b)


def test_a_chain_s_header_adds_its_steps_ages() -> None:
    """A chain's steps run one after another: the header's time is their sum,
    not the oldest step's age (the max read 9 s on a call 15 s old, live)."""

    steps = [_snap(0, state="done", elapsed_ms=9_000), _snap(1, elapsed_ms=5_000)]
    chain = _screen(format_panel(steps, tasks=("a", "b"), width=80, mode="chain"))
    parallel = _screen(format_panel(steps, tasks=("a", "b"), width=80))
    assert chain[0].endswith(" 14s"), chain[0]
    assert parallel[0].endswith(" 9s"), parallel[0]


def test_the_height_is_decided_from_the_member_count_and_the_terminal() -> None:
    """D4: one child five rows; two to four, two rows each while that fits a
    third of the terminal (never less than five); one row each otherwise and
    from five on; nine at most."""

    assert PANEL_MIN_CHILDREN == 1
    assert [panel_height(n, terminal_rows=30) for n in range(1, 9)] == [5, 5, 7, 9, 6, 7, 8, 9]
    # A 24-row terminal's budget is eight: four children no longer fit two rows.
    assert [panel_height(n, terminal_rows=24) for n in range(1, 5)] == [5, 5, 7, 5]
    # Short or unknown terminals fall back to the default's budget, never below five.
    assert panel_height(2, terminal_rows=6) == 5
    assert panel_height(3, terminal_rows=None) == panel_height(3, terminal_rows=24)
    assert panel_height(40, terminal_rows=200) == PANEL_MAX_ROWS


def test_a_compact_group_draws_one_row_per_child_even_below_five() -> None:
    lines = format_panel(_three(), tasks=_TASKS, width=80, height=4)
    assert len(lines) == 4
    assert [row.split()[1] for row in _screen(lines)[1:]] == ["●", "●", "✓"]


def test_idle_shows_from_five_seconds_and_moves_the_clock_of_a_live_child() -> None:
    quiet = _snap(elapsed_ms=10_000, activity=(_call("bash", "sleep 30", "running"),))
    at_4 = _screen(format_panel([quiet], width=80, idle_ms=[4_000]))
    at_9 = _screen(format_panel([quiet], width=80, idle_ms=[9_000]))
    assert "idle" not in at_4[0] and at_4[0].endswith("14s"), at_4[0]
    assert at_9[0].endswith("idle 9s · 19s"), at_9[0]
    assert at_9[1] == "│   ▶ $ sleep 30 · idle 9s"
    # A finished child's clock stands still: it is not idle, it is done.
    done = _snap(state="done", elapsed_ms=10_000)
    assert _screen(format_panel([done], width=80, idle_ms=[9_000]))[0].endswith("10s")


def test_time_and_tokens_are_spelled_the_way_d4_spells_them() -> None:
    elapsed = panel_module._format_elapsed
    tokens = panel_module._compact_tokens
    assert [elapsed(ms) for ms in (0, 9_000, 72_000, 3_720_000)] == ["0s", "9s", "1m12s", "1h02m"]
    assert [tokens(t) for t in (15, 400, 1_500, 12_300, 2_000_000)] == [
        "15",
        "0.4k",
        "1.5k",
        "12.3k",
        "2.0M",
    ]


# === the Esc frame (D7) =======================================================


def _stopped(index: int = 0, **kwargs: Any) -> SubagentResult:
    base: dict[str, Any] = {
        "id": f"sub-{index}",
        "profile": "worker",
        "ok": False,
        "status": "aborted",
        "summary": "",
    }
    base.update(kwargs)
    return SubagentResult(**base)


def test_the_esc_frame_for_one_child_is_the_mockup_and_keeps_five_rows() -> None:
    live = _snap(
        elapsed_ms=3_600,
        activity=(_call("write", "notes.md"), _call("bash", "sleep 97", "running")),
    )
    lines = format_panel(
        [live],
        width=80,
        cancelled=True,
        results={
            0: _stopped(
                changed_files=("notes.md",), usage=SubagentUsage(turns=2), session_id="s-77"
            )
        },
    )
    assert _screen(lines) == [
        _flush("│ ■ agent worker · stopped — the turn was cancelled", "2 turns · 3s"),
                "│   ✓ write notes.md",
                "│   ■ $ sleep 97 · no result",
                "│   changed: notes.md",
                "│   Ask to continue it and the model can resume it.",
    ]


def test_the_esc_frame_for_a_batch_names_each_member_and_who_never_started() -> None:
    snapshots: list[SubagentProgress | None] = [
        _snap(0, elapsed_ms=5_000, activity=(_call("read", "a"), _call("bash", "sleep 9", "running"))),
        _snap(1, state="done", elapsed_ms=2_000, last_text="B finished."),
        None,
    ]
    lines = format_panel(
        snapshots,
        tasks=("task a", "task b", "task c"),
        width=80,
        cancelled=True,
        results={0: _stopped(changed_files=("a.md", "b.md"), session_id="s-77")},
    )
    assert len(lines) == panel_height(3)
    assert _screen(lines) == [
        "│ 3 agents · stopped — the turn was cancelled · ask to continue them",
        _flush("│ ■ 1  task a", "5s"),
                "│      ✓ ■ $ sleep 9 · no result · changed: a.md, b.md",
        _flush("│ ✓ 2  task b", "2s"),
                "│      “B finished.”",
        "│ ○ 3  task c",
                "│      not started",
    ]


# === the bounds every frame keeps =============================================

_HOSTILE = (
    "보안\n" + "\n".join(f"\x1b[31mROW {i}" for i in range(20)) + "한" * 300,
    "read\x1b]52;c;SGVsbG8=\x07\x01\x1b[2J\x02",
    "evil‮txt.exe‮ ⁦x⁩",
    "a" + "́" * 2_000,
    "‍‍" + "👩‍💻" * 60,
    "x" * 9_000,
    "\r\n\t  \x85 spaced  　 out",
    "\x9b2J\x9d0;title\x9c",
    "",
)


def _hostile_snapshot(index: int, text: str, state: str = "running") -> SubagentProgress:
    return SubagentProgress(
        id=f"sub-{index}",
        profile=text or "worker",
        state=state,  # type: ignore[arg-type]
        model=text,
        current_tool=text,
        elapsed_ms=999_999_000,
        tokens=987_654_321,
        cost=99.9999,
        activity=(
            _call(text, text, "ok"),
            _call("bash", text, "failed"),
            _call(text, "", "no_result"),
            _call(text, text, "running"),
        ),
        last_text=text,
    )


def _assert_bounded(lines: list[str], *, width: int, height: int, why: object) -> None:
    assert len(lines) == height, (why, len(lines))
    for line in lines:
        assert "\n" not in line and "\r" not in line, (why, line)
        escapes = set(re.findall(r"\x1b\[[0-9;]*[A-Za-z]", line))
        assert escapes <= _OUR_SGR, (why, escapes)
        plain = _visible(line)
        assert "\x1b" not in plain, (why, plain)
        assert not contains_steering_chars(plain), (why, repr(plain))
        assert cell_len(plain) <= width, (why, cell_len(plain), plain)
        # And in CODE POINTS: a zero-width flood measures nothing in cells, so
        # the cell bound alone would let 2 048 combining marks through a label.
        # At most three child fields share a row, each at most four code points
        # per cell (``panel._ZERO_WIDTH_SLACK``), plus the panel's own words.
        assert len(plain) <= 12 * width + 64, (why, len(plain))


@pytest.mark.parametrize("width", [1, 2, 3, 10, 30, 31, 40, 59, 60, 80, 81, 120, 200])
def test_no_frame_is_wider_or_taller_than_it_was_given(width: int) -> None:
    for text in _HOSTILE:
        for members in range(1, 9):
            snapshots: list[SubagentProgress | None] = [
                _hostile_snapshot(k, text, state=("running", "done", "error", "stopped")[k % 4])
                for k in range(members)
            ]
            if members > 2:
                snapshots[-1] = None
            height = panel_height(members, terminal_rows=30)
            for mode in ("parallel", "chain"):
                for cancelled in (False, True):
                    lines = format_panel(
                        snapshots,
                        tasks=tuple(text + " {previous}" for _ in range(members)),
                        width=width,
                        height=height,
                        mode=mode,
                        profile=text,
                        idle_ms=[9_000] * members,
                        cancelled=cancelled,
                        results={0: _stopped(changed_files=(text, text))},
                    )
                    _assert_bounded(
                        lines,
                        width=width,
                        height=height,
                        why=(width, members, mode, cancelled, text[:12]),
                    )


def test_a_child_cannot_add_a_row_or_an_escape_through_any_field() -> None:
    """The F2 finding (``test_batch_surfaces``) restated for every field the new
    layout reads: forty newlines and an SGR in the reply, the call, the model
    and the label still make a five-row block and a seven-row batch."""

    hostile = _HOSTILE[0]
    one = format_panel([_hostile_snapshot(0, hostile)], width=80)
    assert len(one) == PANEL_SINGLE_ROWS
    # Defanged, not removed: the ESC goes and ``[31mROW`` stays as inert text —
    # the panel's own red (``\x1b[31m✗``) is the only SGR 31 left.
    assert "\x1b[31mROW" not in "".join(one)
    assert "[31mROW" in "".join(one)
    three = format_panel(
        [_hostile_snapshot(k, hostile) for k in range(3)], tasks=(hostile,) * 3, width=80
    )
    assert len(three) == 7
    assert "\x1b[31mROW" not in "".join(three)


def test_cjk_labels_are_measured_in_cells() -> None:
    labels = ("한국어 작업 " * 20, "日本語のタスク" * 20, "中文任务" * 30)
    for width in (30, 50, 80, 120):
        lines = format_panel(
            [_snap(k, last_text="답변 " * 100) for k in range(3)], tasks=labels, width=width
        )
        for line in lines:
            assert cell_len(_visible(line)) <= width, (width, line)
        assert any("…" in _visible(line) for line in lines)


def test_the_panel_writes_no_escape_but_its_own_sgr() -> None:
    lines = format_panel(_three(), tasks=_TASKS, width=80, idle_ms=[9_000, 9_000, 0])
    escapes = {seq for line in lines for seq in re.findall(r"\x1b\[[0-9;]*[A-Za-z]", line)}
    assert escapes <= _OUR_SGR, escapes
    assert {"\x1b[36m", "\x1b[32m", "\x1b[2;3m"} <= escapes  # glyphs and the reply are styled


def test_a_foreign_runtime_s_odd_values_render_rather_than_raise() -> None:
    """``SubagentProgress`` is a public contract another runtime may fill; the
    panel reads it defensively and a render never raises over a ``None``."""

    odd = SubagentProgress(
        id="sub-0",
        profile=None,  # type: ignore[arg-type]
        state="paused",  # type: ignore[arg-type]
        model=None,
        elapsed_ms=float("nan"),  # type: ignore[arg-type]
        tokens=None,  # type: ignore[arg-type]
        cost=float("inf"),
        activity=None,  # type: ignore[arg-type]
        last_text=12,  # type: ignore[arg-type]
    )
    assert len(format_panel([odd], width=80)) == PANEL_SINGLE_ROWS
    assert len(format_panel([odd, odd], tasks=(None, 3), width=80)) == 5  # type: ignore[arg-type]


def test_a_frame_is_exactly_the_height_it_was_given() -> None:
    """Shorter content is padded with gutter rows and longer content is cut with
    a ``… N more`` row: the list length is the screen height, and it is the
    one fixed when the group opened (D5), whatever the layout produced."""

    two = format_panel([_snap(0), _snap(1)], tasks=("a", "b"), width=80, height=9)
    assert len(two) == 9
    assert _screen(two)[5:] == ["│"] * 4
    many = _screen(format_panel([_snap(k) for k in range(12)], width=80, height=PANEL_MAX_ROWS))
    assert len(many) == PANEL_MAX_ROWS
    assert many[-1] == "│ … 5 more"  # header + 7 members shown, 5 named


def test_an_empty_group_draws_nothing() -> None:
    assert format_panel([], width=80) == []


# === the reducer: ``recent``, the turn counter, ``last_text`` (D3) ============


def _start(state: _StreamState, call_id: str, name: str = "bash", **args: Any) -> None:
    reduce_event(
        state,
        {"type": "tool_execution_start", "tool_call_id": call_id, "tool_name": name, "args": args},
    )


def _end(state: _StreamState, call_id: str, *, error: bool = False) -> None:
    reduce_event(state, {"type": "tool_execution_end", "tool_call_id": call_id, "is_error": error})


def test_recent_keeps_the_last_calls_and_shares_the_trail_s_outcome() -> None:
    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, {"type": "turn_start"})
    for k in range(6):
        _start(state, f"c{k}", command=f"echo {k}")
        _end(state, f"c{k}", error=k == 4)
    assert len(state.recent) == ACTIVITY_MAX
    assert [call.args for call in state.recent] == ["echo 2", "echo 3", "echo 4", "echo 5"]
    assert state.recent[-1] is state.tool_trail[-1]
    assert [a.status for a in activity_of(state, live=True)] == ["ok", "ok", "failed", "ok"]


def test_recent_keeps_moving_after_the_trail_stops() -> None:
    """The trail stops recording at ``MAX_TRAIL_ENTRIES``; a panel reading it
    would freeze at call 200. ``recent`` is fed by EVERY start, and an end past
    the trail's cap still closes the call in ``recent``."""

    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, {"type": "turn_start"})
    for k in range(MAX_TRAIL_ENTRIES + 7):
        _start(state, f"c{k}", command=f"step {k}")
        if k < MAX_TRAIL_ENTRIES + 6:
            _end(state, f"c{k}", error=k == MAX_TRAIL_ENTRIES + 5)
    assert len(state.tool_trail) == MAX_TRAIL_ENTRIES
    assert state.trail_overflow == 7
    shown = activity_of(state, live=True)
    assert [a.args for a in shown] == [f"step {k}" for k in range(203, 207)]
    assert [a.status for a in shown] == ["ok", "ok", "failed", "running"]


def test_an_open_call_is_running_only_in_the_current_turn_of_a_live_open_run() -> None:
    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, {"type": "turn_start"})
    _start(state, "c1", command="sleep 9")
    assert [a.status for a in activity_of(state, live=True)] == ["running"]
    # The child is no longer live (stopped, killed): its end will never come.
    assert [a.status for a in activity_of(state, live=False)] == ["no_result"]
    # A new turn began without the end event.
    reduce_event(state, {"type": "turn_start"})
    assert [a.status for a in activity_of(state, live=True)] == ["no_result"]
    _start(state, "c2", command="sleep 1")
    assert [a.status for a in activity_of(state, live=True)] == ["no_result", "running"]
    # The run ended.
    reduce_event(state, {"type": "agent_end"})
    assert [a.status for a in activity_of(state, live=True)] == ["no_result", "no_result"]


def test_last_text_is_the_latest_reply_on_one_bounded_line() -> None:
    state = _StreamState()
    assert state.last_text is None

    def reply(text: str) -> None:
        reduce_event(
            state,
            {
                "type": "message_end",
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            },
        )

    reply("\n\n  First  line\n\tsecond　line  ")
    assert state.last_text == "First line second line"
    reduce_event(
        state,
        {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "toolCall"}]}},
    )
    assert state.last_text == "First line second line", "a message with no text keeps the last"
    reply("word " * 2_000)
    assert state.last_text is not None
    assert len(state.last_text) == LAST_TEXT_MAX_CHARS
    assert state.last_text.endswith("…")


async def test_the_runtime_publishes_activity_and_the_last_reply(tmp_path: Path) -> None:
    """``runtime._publish`` is the sole producer: it projects ``recent`` with the
    one fact the stream lacks — is the child live — so its last snapshot, taken
    after the child ended, carries no ``running`` call."""

    from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl

    from tests.agents_ext.test_print_channel_spawn import _profile, _resolved

    class _Channel:
        async def run(self, plan: Any, *, child: Any = None, on_stream: Any = None) -> Any:
            state: _StreamState = child.stream
            child.state = "running"
            for event in (
                {"type": "agent_start"},
                {"type": "turn_start"},
                {"type": "tool_execution_start", "tool_call_id": "a",
                 "tool_name": "read", "args": {"path": "notes.txt"}},
                {"type": "tool_execution_end", "tool_call_id": "a", "is_error": False},
                {"type": "tool_execution_start", "tool_call_id": "b",
                 "tool_name": "bash", "args": {"command": "sleep 9"}},
                {"type": "message_end", "message": {"role": "assistant",
                 "content": [{"type": "text", "text": "Read it.\nWaiting."}]}},
            ):
                reduce_event(state, event)
                on_stream(state)
            child.state = "stopped"
            return SubagentResult(
                id=plan.id, profile=plan.resolved.name, ok=False, status="aborted", summary=""
            )

    seen: list[SubagentProgress] = []
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: str(tmp_path)), channel=_Channel())
    await runtime.spawn(_resolved(_profile()), "go", on_event=seen.append)

    live = [p for p in seen if p.state == "running"]
    assert [(a.name, a.args, a.status) for a in live[-1].activity] == [
        ("read", "notes.txt", "ok"),
        ("bash", "sleep 9", "running"),
    ]
    assert live[-1].last_text == "Read it. Waiting."
    assert seen[-1].state == "stopped"
    assert [a.status for a in seen[-1].activity] == ["ok", "no_result"]


# === the bridge: never from the publish path, throttled, ticking (D6) ========


class _Handle:
    def __init__(self) -> None:
        self.cancelled = False
        self.ran = False

    def cancel(self) -> None:
        self.cancelled = True


class _Loop:
    """A scheduler and a clock under test control — ``loop.call_later``'s shape."""

    def __init__(self) -> None:
        self.now = 100.0
        self._calls: list[tuple[float, int, Callable[[], None], _Handle]] = []

    def clock(self) -> float:
        return self.now

    def call_later(self, delay: float, callback: Callable[[], None]) -> _Handle:
        handle = _Handle()
        self._calls.append((self.now + delay, len(self._calls), callback, handle))
        return handle

    def pending(self) -> list[tuple[float, _Handle]]:
        return [(when, h) for when, _, _, h in self._calls if not h.cancelled and not h.ran]

    def advance(self, seconds: float) -> None:
        end = self.now + seconds
        while True:
            due = sorted(
                (c for c in self._calls if not c[3].cancelled and not c[3].ran and c[0] <= end),
                key=lambda c: (c[0], c[1]),
            )
            if not due:
                break
            when, _, callback, handle = due[0]
            self.now = max(self.now, when)
            handle.ran = True
            callback()
        self.now = end


class _Chrome:
    """A chrome whose output reports a size, as ``tui.width`` reads it."""

    def __init__(self, columns: int, rows: int) -> None:
        self.columns, self.rows = columns, rows
        chrome = self

        class _Out:
            def get_size(self) -> Any:
                return type("Size", (), {"columns": chrome.columns, "rows": chrome.rows})()

        self.app = type("App", (), {"output": _Out()})()


def _bridge(
    *, columns: int = 80, rows: int = 30, loop: _Loop | None = None
) -> tuple[SubagentProgressBridge, _Ui, _Loop]:
    loop = loop or _Loop()
    ui = _Ui()
    ui.chrome = _Chrome(columns, rows)  # type: ignore[attr-defined]
    bridge = SubagentProgressBridge(_Api(ui), clock=loop.clock, call_later=loop.call_later)
    return bridge, ui, loop


def _panels(ui: _Ui) -> list[list[str]]:
    return [lines for key, lines in ui.widgets if key == PANEL_WIDGET_KEY and lines]


def test_nothing_is_drawn_from_the_publish_path() -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=1, mode="single", profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(state="starting"))
    assert ui.widgets == [], "drawn inside the publish call"
    assert len(loop.pending()) == 1
    loop.advance(0)
    assert len(_panels(ui)) == 1


def test_a_chatty_group_is_redrawn_at_most_four_times_a_second() -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=3, tasks=_TASKS, profile="worker")
    for index in range(3):
        bridge.adopt(f"sub-{index}", "tc", index=index)
    for step in range(200):  # 200 snapshots over 2 s, 10 ms apart
        bridge(_snap(step % 3, elapsed_ms=step * 10, tokens=step))
        loop.advance(0.01)
    draws = len(_panels(ui))
    assert draws <= 2 / RENDER_MIN_INTERVAL_S + 1, draws
    assert draws >= 4, draws
    assert ui.writes == [], "a grouped member wrote a statusline row (D2)"


def test_the_last_snapshot_is_always_drawn_once_the_interval_passes() -> None:
    """The throttle drops frames, never facts: a snapshot absorbed by a pending
    redraw is read when that redraw runs."""

    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(elapsed_ms=1_000))
    loop.advance(0)
    bridge(_snap(elapsed_ms=2_000, activity=(_call("read", "late.txt", "running"),)))
    loop.advance(RENDER_MIN_INTERVAL_S / 2)
    assert "late.txt" not in "".join(_panels(ui)[-1])
    loop.advance(RENDER_MIN_INTERVAL_S)
    assert "late.txt" in "".join(_panels(ui)[-1])


def test_a_silent_live_child_s_clock_still_moves_once_a_second() -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(elapsed_ms=2_000, activity=(_call("bash", "sleep 12", "running"),)))
    loop.advance(0)
    headers = []
    for _ in range(7):
        loop.advance(TICK_INTERVAL_S)
        headers.append(_visible(_panels(ui)[-1][0]).rstrip())
    assert headers[0].endswith(" 3s"), headers
    assert headers[-1].endswith("idle 7s · 9s"), headers
    assert len(set(headers)) == 7, "the tick did not redraw"


def test_the_tick_stops_when_no_member_is_live() -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(elapsed_ms=2_000))
    loop.advance(0)
    bridge(_snap(state="done", elapsed_ms=3_000, last_text="done."))
    loop.advance(5 * TICK_INTERVAL_S)
    assert loop.pending() == []
    assert "✓ agent worker" in _visible(_panels(ui)[-1][0])


def test_end_group_cancels_every_handle_and_blanks_the_panel() -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=2, tasks=("a", "b"), profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(0))
    loop.advance(0)  # drawn, and a tick armed
    bridge(_snap(0, elapsed_ms=50))  # a redraw pending too
    assert len(loop.pending()) == 2
    bridge.end_group("tc")
    assert loop.pending() == []
    assert ui.widgets[-1] == (PANEL_WIDGET_KEY, None)
    count = len(ui.widgets)
    loop.advance(10)
    assert len(ui.widgets) == count, "something drew after the group ended"


def test_clear_cancels_every_handle_too() -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap())
    loop.advance(0)
    bridge.clear()
    assert loop.pending() == []
    assert ui.widgets[-1] == (PANEL_WIDGET_KEY, None)


def test_with_no_running_loop_the_bridge_draws_at_once() -> None:
    """The default scheduler outside a loop — a synchronous caller, a unit test
    — draws on the publish, as the bridge always did."""

    ui = _Ui()
    bridge = SubagentProgressBridge(_Api(ui))
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap())
    assert len(_panels(ui)) == 1


def test_the_height_never_moves_through_a_whole_group_life() -> None:
    """D5, over everything that used to change the panel's height: the first
    snapshot, the first tool, the first reply, members finishing one by one, a
    resize, and the Esc frame."""

    for members, rows in ((1, 30), (2, 30), (3, 24), (4, 30), (4, 24), (6, 40)):
        bridge, ui, loop = _bridge(rows=rows)
        height = panel_height(members, terminal_rows=rows)
        bridge.begin_group("tc", expected=members, tasks=("job",) * members, profile="worker")
        for index in range(members):
            bridge.adopt(f"sub-{index}", "tc", index=index)
        frames: list[list[str]] = []

        def step(index: int, **kwargs: Any) -> None:
            bridge(_snap(index, **kwargs))  # noqa: B023 — the loop's own bridge
            loop.advance(RENDER_MIN_INTERVAL_S)  # noqa: B023
            frames.append(_panels(ui)[-1])  # noqa: B023

        step(0, state="starting")
        step(0, activity=(_call("read", "a", "running"),))
        step(0, activity=(_call("read", "a"),), last_text="Read a.")
        ui.chrome.columns = 50  # type: ignore[attr-defined]
        if members > 1:
            step(members - 1, activity=(_call("bash", "sleep 9", "running"),))
        step(0, state="done", last_text="Finished.")
        assert bridge.linger("tc", {}) is (members > 0)
        frames.append(_panels(ui)[-1])
        for frame in frames:
            assert len(frame) == height, (members, rows, len(frame), frame)
        assert all(cell_len(_visible(row)) <= 50 for row in frames[-1])


def test_a_grouped_member_writes_no_statusline_row_and_an_ungrouped_one_still_does() -> None:
    """D2 for the call's members; ``/agents run`` (never adopted) keeps its
    per-child row byte-identical."""

    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(0, current_tool="read"))
    loner = SubagentProgress(id="sub-loner", profile="scout", state="running", current_tool="grep")
    bridge(loner)
    loop.advance(1)
    assert ui.writes == [(status_key("sub-loner"), format_status_row(loner))]
    assert group_status_key("tc") not in {key for key, _ in ui.writes}


def test_the_esc_frame_stays_until_it_is_dismissed() -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=1, mode="single", profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(activity=(_call("bash", "sleep 9", "running"),)))
    loop.advance(0)
    assert bridge.linger("tc", {0: _stopped(changed_files=("x.md",))}) is True
    frame = _screen(_panels(ui)[-1])
    assert "stopped — the turn was cancelled" in frame[0]
    assert frame[3] == "│   changed: x.md"
    # A cancelled frame does not tick: the one handle left is the width watch
    # (review round 1), which redraws nothing while the width stands still.
    assert len(loop.pending()) == 1
    # A member still on its way out does not redraw the record of the cancel.
    count = len(ui.widgets)
    bridge(_snap(state="stopped", elapsed_ms=99_000))
    loop.advance(5)
    assert len(ui.widgets) == count
    # The next prompt takes it down, and the watch with it.
    bridge.dismiss()
    assert ui.widgets[-1] == (PANEL_WIDGET_KEY, None)
    assert bridge._groups == {}
    assert loop.pending() == []


def test_a_new_group_takes_down_a_lingering_frame() -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc-1", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc-1", index=0)
    bridge(_snap())
    loop.advance(0)
    bridge.linger("tc-1", {})
    bridge.begin_group("tc-2", expected=1, profile="worker")
    assert "tc-1" not in bridge._groups
    assert ui.widgets[-1] == (PANEL_WIDGET_KEY, None)


def test_nothing_lingers_for_a_call_with_no_published_member_or_no_group() -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=2, tasks=("a", "b"), profile="worker")
    assert bridge.linger("tc", {}) is False
    assert bridge._groups == {}
    assert ui.widgets == []
    # A session teardown cleared everything before the turn's cancel arrived.
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap())
    loop.advance(0)
    bridge.clear()
    count = len(ui.widgets)
    assert bridge.linger("tc", {}) is False
    assert len(ui.widgets) == count


def test_the_publish_path_and_a_redraw_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    import aelix_agents.progress as progress_module

    def _boom(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("layout bug")

    monkeypatch.setattr(progress_module, "format_panel", _boom)
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap())
    loop.advance(2)  # the scheduled redraw and the tick both hit the bug
    assert bridge.linger("tc", {}) is False
    assert ui.widgets == []


def test_a_headless_group_never_raises_and_never_lingers() -> None:
    bridge = SubagentProgressBridge(_Api(HEADLESS_UI_CONTEXT))
    bridge.begin_group("tc", expected=2, tasks=("a", "b"), profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(0))
    assert bridge.linger("tc", {}) is False
    bridge.dismiss()
    bridge.clear()


def test_the_width_is_the_live_terminal_s_and_capped_at_the_transcript_s() -> None:
    for columns, expected in ((40, 40), (100, 100), (200, 120)):
        bridge, ui, loop = _bridge(columns=columns)
        bridge.begin_group("tc", expected=1, profile="worker")
        bridge.adopt("sub-0", "tc", index=0)
        bridge(_snap(elapsed_ms=1_000))
        loop.advance(0)
        header = _visible(_panels(ui)[-1][0])
        assert cell_len(header) == expected, (columns, cell_len(header), header)
