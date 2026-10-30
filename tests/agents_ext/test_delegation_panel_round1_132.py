"""#132 review round 1 — the delegation panel's defects found by the verify
pass, the Codex cross-review and the live lane, each pinned by a test that
failed on ``786b95e5`` (ADR-0278; the findings and their probes are listed in
``.omc/specs/132-progress-2026-10-30.md``, "Review round 1").

F1  an open call stays in ``activity`` however many calls finish after it;
F2  a whitespace-only reply keeps the last reply, and a work-bound cut says so;
F3  no row is wider than the width by prompt_toolkit's measure either;
F4  a lone surrogate becomes U+FFFD, and ``linger`` reports a refused frame;
F5  a lingering Esc frame refits when the terminal's width changes;
F6  the one-row layout names a disagreeing member's model, sanitised;
F7  the elapsed time outlives the model and the profile in both headers;
F8  the Esc frame offers a resume only when there is a session to resume;
F9  two cells between a label and its numbers, and labels that keep their cut;
F10 an ended group's member writes nothing, and a headless bridge formats
    nothing.

The glass half of F3 (the real chrome, read off a pyte screen) is
``tests/tui/test_delegation_panel_width_132.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from typing import Any

import pytest
from aelix_agents import panel as panel_module
from aelix_agents import progress as progress_module
from aelix_agents import stream as stream_module
from aelix_agents.panel import PANEL_WIDGET_KEY, _flatten, format_panel, panel_height
from aelix_agents.progress import TICK_INTERVAL_S, SubagentProgressBridge, format_status_row
from aelix_agents.stream import (
    ACTIVITY_MAX,
    _one_line,
    _StreamState,
    activity_of,
    reduce_event,
)
from aelix_coding_agent.extensions.headless_ui import HEADLESS_UI_CONTEXT
from aelix_coding_agent.subagent_contract import SubagentProgress
from prompt_toolkit.utils import get_cwidth
from rich.cells import cell_len

from tests.agents_ext.test_delegation_panel_132 import (
    _TASKS,
    _bridge,
    _call,
    _end,
    _panels,
    _screen,
    _snap,
    _start,
    _stopped,
    _visible,
)
from tests.agents_ext.test_events_and_statusline import _Api, _Ui

# === F1: an open call is never evicted by the calls that finish after it =====


def _five_parallel_calls(state: _StreamState) -> None:
    """The cross-review's shape: a slow ``grep`` and four ``read`` calls asked
    for in one message; the reads finish while the grep is still running."""

    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, {"type": "turn_start"})
    _start(state, "0", "grep", path="large-tree", pattern="needle")
    for k in range(1, 5):
        _start(state, str(k), "read", path=f"file{k}.txt")
    for k in range(1, 5):
        _end(state, str(k))


def test_an_open_call_stays_in_activity_however_many_calls_finish_after_it() -> None:
    state = _StreamState()
    _five_parallel_calls(state)
    shown = activity_of(state, live=True)
    # At most four, in start order, and the grep that is still running first.
    assert [(a.name, a.args, a.status) for a in shown] == [
        ("grep", "large-tree", "running"),
        ("read", "file2.txt", "ok"),
        ("read", "file3.txt", "ok"),
        ("read", "file4.txt", "ok"),
    ]
    # Once it ends the window is the LATEST four by start order again: the
    # grep started first, so it is the one that goes.
    _end(state, "0")
    assert len(state.recent) == ACTIVITY_MAX
    assert [(a.args, a.status) for a in activity_of(state, live=True)] == [
        ("file1.txt", "ok"),
        ("file2.txt", "ok"),
        ("file3.txt", "ok"),
        ("file4.txt", "ok"),
    ]
    _start(state, "5", "read", path="file5.txt")
    assert [a.args for a in activity_of(state, live=True)] == [
        "file2.txt",
        "file3.txt",
        "file4.txt",
        "file5.txt",
    ]


def test_the_window_keeps_the_latest_finished_calls_whatever_order_they_end_in() -> None:
    """The reads end in reverse order while the grep runs; the window keeps
    the four reads (not whichever ended first), and the latest of them by
    start order once the grep is done — the live run showed ``a, b, c`` with
    ``d`` dropped when every finished call counted against the four."""

    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, {"type": "turn_start"})
    _start(state, "0", "grep", path="large-tree")
    for k in range(1, 5):
        _start(state, str(k), "read", path=f"file{k}.txt")
    for k in range(4, 0, -1):
        _end(state, str(k))
    assert [a.args for a in activity_of(state, live=True)] == [
        "large-tree", "file2.txt", "file3.txt", "file4.txt",
    ]
    _end(state, "0")
    assert [a.args for a in activity_of(state, live=True)] == [
        "file1.txt", "file2.txt", "file3.txt", "file4.txt",
    ]


def test_an_open_call_leaves_the_window_when_its_turn_or_run_ends() -> None:
    """Pinned only while it could still be running: a new turn or the end of
    the run means its end event will never come, and it may go."""

    state = _StreamState()
    _five_parallel_calls(state)
    reduce_event(state, {"type": "turn_start"})
    assert len(state.recent) == ACTIVITY_MAX
    assert all(a.status != "running" for a in activity_of(state, live=True))


def test_the_window_stays_bounded_however_many_calls_are_open() -> None:
    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, {"type": "turn_start"})
    for k in range(500):
        _start(state, f"c{k}", "read", path=f"f{k}")
    assert len(state.recent) == stream_module._RECENT_MAX
    shown = activity_of(state, live=True)
    # Four, all running, the oldest open ones — a running call is never pushed
    # out by one that started after it.
    assert [a.args for a in shown] == ["f0", "f1", "f2", "f3"]
    assert {a.status for a in shown} == {"running"}


def test_the_panel_shows_the_open_call_in_both_layouts() -> None:
    state = _StreamState()
    _five_parallel_calls(state)
    snap = _snap(elapsed_ms=4_000, activity=activity_of(state, live=True))
    one = _screen(format_panel([snap], width=80))
    assert " · running" in one[0], one[0]
    assert one[1:4] == ["│   ▶ grep large-tree", "│   ✓ read file3.txt", "│   ✓ read file4.txt"]
    two = _screen(format_panel([snap, _snap(1)], tasks=("a", "b"), width=80))
    assert two[2] == "│      ✓✓✓ ▶ grep large-tree", two[2]


async def test_a_real_harness_s_parallel_calls_keep_the_running_one() -> None:
    """The cross-review's reproduction (``parallel_tools_probe.py``) as a test:
    a real ``AgentHarness`` executes a gated ``grep`` and four ``read`` calls in
    parallel; with the reads finished and the grep still gated, the panel says
    ``running`` and shows the grep, where it said ``thinking`` on ``786b95e5``."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.types import AgentTool
    from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
    from aelix_ai.models import Model
    from aelix_ai.tools import ToolResult

    from tests.agents_ext.test_stopped_child_reports_604 import _scripted

    state = _StreamState()
    release = asyncio.Event()
    reads_done = asyncio.Event()
    ended: list[str] = []

    async def grep(args: Any, ctx: Any) -> ToolResult:
        await release.wait()
        return ToolResult(content=[TextContent(text="match")])

    async def read(args: Any, ctx: Any) -> ToolResult:
        return ToolResult(content=[TextContent(text="text")])

    def tool(name: str, execute: Any) -> AgentTool:
        return AgentTool(
            name=name,
            description=name,
            parameters={"type": "object"},
            execution_mode="parallel",
            execute=execute,
        )

    first = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id=str(k),
                tool_name="grep" if k == 0 else "read",
                input={"path": "large-tree" if k == 0 else f"file{k}.txt"},
            )
            for k in range(5)
        ],
        stop_reason="tool_use",
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            tools=[tool("grep", grep), tool("read", read)],
            stream_fn=_scripted(first, []),
        )
    )

    def on_event(event: Any) -> None:
        data = dataclasses.asdict(event)
        reduce_event(state, data)
        if data.get("type") == "tool_execution_end" and data.get("tool_call_id") != "0":
            ended.append(data["tool_call_id"])
            if len(ended) == 4:
                reads_done.set()

    harness.subscribe(on_event)
    turn = asyncio.create_task(harness.prompt("run tools"))
    try:
        await asyncio.wait_for(reads_done.wait(), 5)
        snap = _snap(elapsed_ms=1_000, activity=activity_of(state, live=True))
        rows = _screen(format_panel([snap], width=80))
        assert not turn.done()
        assert " · running" in rows[0], rows
        assert any(row.startswith("│   ▶ grep large-tree") for row in rows), rows
    finally:
        release.set()
        await asyncio.wait_for(turn, 5)
        await harness.dispose()


# === F2: the last reply ======================================================


def _reply(state: _StreamState, *blocks: dict[str, Any]) -> None:
    reduce_event(
        state,
        {"type": "message_end", "message": {"role": "assistant", "content": list(blocks)}},
    )


def test_a_whitespace_only_reply_keeps_the_last_reply() -> None:
    """``_extract_text`` keeps a ``"\\n\\n"`` text block before a tool call; it
    set ``last_text`` back to ``None`` and a child that had replied read
    "waiting for the first reply" again."""

    state = _StreamState()
    _reply(state, {"type": "text", "text": "Read the notes; one test fails."})
    _reply(state, {"type": "text", "text": "\n\n"}, {"type": "toolCall", "id": "c1"})
    assert state.last_text == "Read the notes; one test fails."


@pytest.mark.parametrize("filler", [" ", "\n", "\t "])
def test_a_reply_cut_by_the_work_bound_says_so(filler: str) -> None:
    """``"A" + " " * 5000 + "B"`` came back as ``"A"`` with no mark."""

    assert _one_line("A" + filler * 5000 + "B") == "A…"
    assert _one_line("one\n two") == "one two"
    assert _one_line("A" * 600).endswith("…") and len(_one_line("A" * 600)) == 512


# === F3: both width measures =================================================

_WIDE = (
    "🇦" * 60 + "TAIL",  # regional indicators: rich 1 each, prompt_toolkit 2
    "🏽" * 60 + "TAIL",  # skin-tone modifiers: rich 0, prompt_toolkit 2
    "👩\u200d💻" * 40,  # ZWJ: rich 2 per pair, prompt_toolkit 4
    "👨\u200d👩\u200d👧 family 🇰🇷 flag 🇦 lone ❤\ufe0f heart " * 6,
    "❤\ufe0f" * 60,  # VS16: rich 2, prompt_toolkit 1
    "한" * 60,
)


def _assert_fits_both(rows: list[str], width: int, why: object) -> None:
    for row in rows:
        plain = _visible(row)
        assert cell_len(plain) <= width, (why, "rich", cell_len(plain), plain)
        assert get_cwidth(plain) <= width, (why, "prompt_toolkit", get_cwidth(plain), plain)


def test_the_prompt_toolkit_measure_is_get_cwidth() -> None:
    """``panel`` measures prompt_toolkit's way without importing it."""

    for text in (*_WIDE, "x\ud800y", "e\u0301" * 9, "plain ascii", ""):
        assert panel_module._ptk_cells(text) == get_cwidth(text), repr(text[:12])


@pytest.mark.parametrize("width", [20, 31, 40, 59, 60, 80, 81, 120])
def test_no_row_is_wider_than_the_width_by_either_measure(width: int) -> None:
    for text in _WIDE:
        snaps = [
            SubagentProgress(
                id=f"s{k}",
                profile=text,
                state="running",
                model=text + str(k),
                elapsed_ms=9_000,
                tokens=1_500,
                activity=(_call("bash", text), _call(text, text, "running")),
                last_text=text,
            )
            for k in range(8)
        ]
        for members in (1, 2, 3, 5, 8):
            for cancelled in (False, True):
                rows = format_panel(
                    snaps[:members],
                    tasks=(text,) * members,
                    width=width,
                    height=panel_height(members, terminal_rows=30),
                    profile=text,
                    idle_ms=[7_000] * members,
                    cancelled=cancelled,
                    results={0: _stopped(changed_files=(text,), session_id="s")},
                )
                _assert_fits_both(rows, width, (width, members, cancelled, text[:6]))


def test_a_reply_cut_for_width_keeps_its_ellipsis_and_closing_quote() -> None:
    """The row is cut where prompt_toolkit will draw it, inside the quotes —
    not past the window's edge, where the chrome dropped the tail with no
    ``…``."""

    snap = _snap(last_text="🇦" * 60 + "TAIL")
    reply = _visible(format_panel([snap], width=80)[4]).rstrip()
    assert reply.endswith("…”"), reply
    assert get_cwidth(reply) <= 80


def test_the_statusline_row_is_never_made_wider() -> None:
    """``_cut_to_cells`` is shared with the statusline row; measuring with the
    larger of the two can only cut a field shorter, and a clean ASCII row is
    byte-identical."""

    clean = SubagentProgress(
        id="s", profile="scout", state="running", model="gpt-5", current_tool="read",
        elapsed_ms=3_000, tokens=1_500,
    )
    assert format_status_row(clean) == "agent scout · gpt-5 · read · 3s · 1.5k tok"
    for text in _WIDE:
        row = format_status_row(
            SubagentProgress(id="s", profile=text, state="running", current_tool=text)
        )
        for field in row.split(" · ")[:2]:
            assert cell_len(field) <= panel_module.PANEL_ROW_MAX_CHARS + len("agent "), field


# === F4: surrogates, and a frame the UI refused ==============================


def test_a_lone_surrogate_becomes_a_replacement_character() -> None:
    assert _flatten("X\ud800Y", limit=78) == "X\ufffdY"
    snap = _snap(
        model="m\udfff",
        activity=(_call("read", "X\ud800Y", "running"),),
        last_text="reply \ud83d alone",
    )
    for rows in (
        format_panel([snap], width=80),
        format_panel([snap, _snap(1)], tasks=("t\ud800", "u"), width=80),
        format_panel([snap], width=80, cancelled=True, results={0: _stopped()}),
    ):
        for row in rows:
            row.encode("utf-8")  # raised UnicodeEncodeError on 786b95e5
    format_status_row(snap).encode("utf-8")


def test_linger_reports_a_frame_the_ui_refused_and_blanks_the_stale_one() -> None:
    """``linger`` said True because the group owned the PREVIOUS widget, while
    the Esc frame itself had failed to write."""

    class _Refusing(_Ui):
        def set_widget(self, key: str, content: Any, options: object = None) -> None:
            if content and any("the turn was cancelled" in line for line in content):
                raise UnicodeEncodeError("utf-8", "x", 0, 1, "refused")
            super().set_widget(key, content, options)

    bridge, _, loop = _bridge()
    ui = _Refusing()
    ui.chrome = _bridge()[1].chrome  # type: ignore[attr-defined]
    bridge._api.runtime.ui = ui
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(activity=(_call("read", "a.txt", "running"),)))
    loop.advance(0)
    assert "running" in _visible(_panels(ui)[-1][0])
    assert bridge.linger("tc", {}) is False
    assert ui.widgets[-1] == (PANEL_WIDGET_KEY, None), "the running frame was left up"
    assert bridge._groups == {}
    assert loop.pending() == []


def test_an_rpc_client_gets_the_esc_frame_after_a_surrogate() -> None:
    """The cross-review's reproduction with the real ``RpcCommandUI`` and the
    RPC JSONL encoder."""

    from aelix_coding_agent.rpc._jsonl import serialize_json_line
    from aelix_coding_agent.rpc.control_ui import RpcCommandUI

    wire: list[dict[str, Any]] = []

    def emit(event: dict[str, Any]) -> None:
        wire.append(json.loads(serialize_json_line(event).encode("utf-8")))

    ui = RpcCommandUI(emit)
    ui.enabled = True
    bridge = SubagentProgressBridge(_Api(ui), call_later=lambda *a: None)
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(activity=(_call("read", "normal.txt", "running"),)))
    bridge(_snap(activity=(_call("read", "X\ud800Y", "running"),)))
    assert bridge.linger("tc", {}) is True
    last = [_visible(line) for line in wire[-1]["lines"]]
    assert "stopped — the turn was cancelled" in last[0], last
    assert "read X\ufffdY · no result" in last[1], last


# === F5: the Esc frame refits ================================================


def test_a_lingering_frame_refits_when_the_terminal_is_resized() -> None:
    bridge, ui, loop = _bridge(columns=80)
    bridge.begin_group("tc", expected=3, tasks=_TASKS, profile="worker")
    for k in range(3):
        bridge.adopt(f"sub-{k}", "tc", index=k)
        bridge(_snap(k, elapsed_ms=4_000, activity=(_call("bash", "sleep 9", "running"),)))
    loop.advance(0)
    assert bridge.linger("tc", {}) is True
    before = _panels(ui)[-1]
    assert max(cell_len(_visible(row)) for row in before) == 80
    writes = len(ui.widgets)
    loop.advance(3 * TICK_INTERVAL_S)
    assert len(ui.widgets) == writes, "an unchanged frame was written again"
    ui.chrome.columns = 40  # type: ignore[attr-defined]
    loop.advance(TICK_INTERVAL_S)
    after = _panels(ui)[-1]
    assert len(ui.widgets) == writes + 1
    assert len(after) == len(before), "the height moved"
    assert all(cell_len(_visible(row)) <= 40 for row in after), _screen(after)
    assert "stopped" in _visible(after[0])
    loop.advance(3 * TICK_INTERVAL_S)
    assert len(ui.widgets) == writes + 1
    bridge.dismiss()
    assert loop.pending() == []


@pytest.mark.parametrize("ending", ["end_group", "clear", "dismiss"])
def test_the_watch_ends_with_the_frame(ending: str) -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap())
    loop.advance(0)
    bridge.linger("tc", {})
    assert len(loop.pending()) == 1
    if ending == "end_group":
        bridge.end_group("tc")
    else:
        getattr(bridge, ending)()
    assert loop.pending() == []
    writes = len(ui.widgets)
    ui.chrome.columns = 40  # type: ignore[attr-defined]
    loop.advance(5)
    assert len(ui.widgets) == writes


# === F6: the one-row layout names a disagreeing model ========================


def test_the_one_row_layout_names_each_member_s_model_when_they_disagree() -> None:
    snaps = [_snap(k, elapsed_ms=1_000, model="model-A" if k < 4 else "model-B") for k in range(5)]
    rows = _screen(format_panel(snaps, tasks=[f"job {k}" for k in range(5)], width=120))
    assert len(rows) == 6
    for k, row in enumerate(rows[1:]):
        assert row.endswith(("model-A" if k < 4 else "model-B") + " · 1s"), row
    # A short terminal compacts two members as well, and still names them.
    two = _screen(
        format_panel(snaps[3:], tasks=("job 3", "job 4"), width=80, height=3)
    )
    assert two[1].endswith("model-A · 1s") and two[2].endswith("model-B · 1s"), two
    # When the rows are short of room the model goes first and the time stays.
    narrow = _screen(format_panel(snaps, tasks=[f"job {k}" for k in range(5)], width=30))
    for row in narrow[1:]:
        assert "model-" not in row and row.endswith(" 1s"), row
    # Agreeing members name it nowhere in a row.
    same = _screen(format_panel([_snap(k) for k in range(5)], tasks=["j"] * 5, width=120))
    assert not any("s-1" in row for row in same[1:])


@pytest.mark.parametrize("members", [2, 5])
def test_a_per_row_model_is_sanitised_in_both_layouts(members: int) -> None:
    hostile = "m\x1b]52;c;QQ==\x07\x01\x1b[2J\x02"
    snaps = [_snap(0, model=hostile)] + [_snap(k, model="other") for k in range(1, members)]
    rows = format_panel(snaps, tasks=["t"] * members, width=120)
    joined = "".join(rows)
    assert "\x1b]52" not in joined and "\x07" not in joined and "\x01" not in joined
    assert "]52;c;QQ==" in _visible(rows[1]), _visible(rows[1])  # defanged, named


# === F7: the elapsed time outlives the model and the profile ================


def _header(width: int, **kwargs: Any) -> str:
    snap = _snap(
        model="anthropic/claude-haiku-4.5",
        elapsed_ms=72_000,
        tokens=1_500,
        cost=0.0093,
        activity=(_call("bash", "sleep 12", "running"),),
        **kwargs,
    )
    return _screen(format_panel([snap], width=width, idle_ms=[7_000]))[0]


def test_the_one_child_header_drops_cost_idle_tokens_model_profile_then_time() -> None:
    gone: list[str] = []
    terms = {
        "cost": "$0.0093",
        "idle": "idle 7s",
        "tokens": "1.5k tok",
        "model": "anthropic/claude-haiku-4.5",
        "profile": "agent worker",
        "elapsed": "1m19s",
    }
    for width in range(140, 14, -1):
        header = _header(width)
        for name, text in terms.items():
            if name not in gone and text not in header:
                gone.append(name)
    assert gone == ["cost", "idle", "tokens", "model", "profile", "elapsed"], gone
    # 56-61 columns, where the model was kept and the time dropped. Since
    # review round 2 the tokens that fit after the model went are shown too
    # (the greedy fill, ``test_delegation_panel_round2_132.py``).
    for width in range(56, 62):
        header = _header(width)
        assert "1m19s · 1.5k tok" in header and "anthropic" not in header, (width, header)


def test_the_batch_header_drops_cost_tokens_model_profile_then_time() -> None:
    snaps = [
        _snap(k, model="anthropic/claude-haiku-4.5", elapsed_ms=10_000, tokens=1_500, cost=0.01,
              state=("running", "done", "done")[k])
        for k in range(3)
    ]
    gone: list[str] = []
    terms = {
        "cost": "$0.0300",
        "tokens": "1.5k tok",
        "model": "anthropic/claude-haiku-4.5",
        "profile": "worker",
        "elapsed": "10s",
    }
    for width in range(140, 30, -1):
        header = _screen(format_panel(snaps, tasks=_TASKS, width=width, profile="worker"))[0]
        assert "1 running · 2 done" in header, (width, header)  # the counts are never cut
        for name, text in terms.items():
            if name not in gone and text not in header:
                gone.append(name)
    assert gone == ["cost", "tokens", "model", "profile", "elapsed"], gone
    # 40 columns, where the profile was kept and the time dropped.
    header = _screen(format_panel(snaps, tasks=_TASKS, width=40, profile="worker"))[0]
    assert header.endswith(" 10s") and "worker" not in header, header


def test_a_number_is_judged_at_its_reserve_and_the_model_stays_put() -> None:
    """Every header term is judged with the clock at its reserve. Round 1
    judged a number at its width now, so on this 60-column header ``15 tok``
    showed at 10 s and went at ``1m00s`` — a term coming and going with the
    clock, which review round 2 ruled out (ADR-0278 D4): the tokens are shown
    at both times or at neither, and the model stays."""

    def header(elapsed_ms: int) -> str:
        snaps = [
            _snap(k, state=("running", "done", "running")[k], elapsed_ms=elapsed_ms, tokens=15)
            for k in range(3)
        ]
        return _screen(format_panel(snaps, tasks=_TASKS, width=60, profile="worker"))[0]

    early, late = header(10_000), header(60_000)
    assert early.endswith(" 10s") and late.endswith(" 1m00s"), (early, late)
    assert "tok" not in early and "tok" not in late, (early, late)
    assert " · worker · s-1 · " in early and " · worker · s-1 · " in late


def test_the_header_s_choices_do_not_flicker_as_the_clock_gains_a_digit() -> None:
    """Judged with the elapsed time at its reserve: what the header shows
    beside the time does not change at 9 s -> 10 s or 59 s -> 1m00s."""

    for width in range(40, 90):
        for before, after in ((9_000, 10_000), (59_000, 60_000)):
            a = _screen(format_panel([_snap(model="m" * 20, elapsed_ms=before)], width=width))[0]
            b = _screen(format_panel([_snap(model="m" * 20, elapsed_ms=after)], width=width))[0]
            assert ("m" * 20 in a) == ("m" * 20 in b), (width, a, b)


# === F8: the resume hint =====================================================


def test_the_esc_frame_offers_a_resume_only_when_there_is_a_session() -> None:
    live = _snap(elapsed_ms=3_000, activity=(_call("bash", "sleep 9", "running"),))
    with_session = _screen(
        format_panel([live], width=80, cancelled=True, results={0: _stopped(session_id="s-1")})
    )
    without = _screen(
        format_panel([live], width=80, cancelled=True, results={0: _stopped(session_id=None)})
    )
    assert with_session[4] == "│   Ask to continue it and the model can resume it."
    assert without[4] == "│   It has no session, so it cannot be resumed."
    assert len(with_session) == len(without) == 5

    pair = [live, _snap(1, state="done")]
    none = _screen(format_panel(pair, tasks=("a", "b"), width=80, cancelled=True,
                                results={0: _stopped(), 1: _stopped(1)}))
    some = _screen(format_panel(pair, tasks=("a", "b"), width=80, cancelled=True,
                                results={0: _stopped(), 1: _stopped(1, session_id="s-2")}))
    assert none[0] == "│ 2 agents · stopped — the turn was cancelled · no session to resume"
    assert some[0] == "│ 2 agents · stopped — the turn was cancelled · ask to continue them"
    assert len(none) == len(some)


# === F9: the gap, and labels that keep their cut =============================


_NUMBERS = re.compile(r"(?:idle \d+s · )?\d+(?:m\d\ds)?s(?: · [\d.]+k?(?: tok)?)?$")


def _gap(row: str) -> int | None:
    """Spaces between a row's text and its right-hand numbers, or ``None`` for
    a row that has no numbers."""

    match = _NUMBERS.search(row.rstrip())
    if match is None:
        return None
    before = row.rstrip()[: match.start()]
    return len(before) - len(before.rstrip(" "))


@pytest.mark.parametrize("width", [40, 50, 60, 80, 100])
def test_a_label_and_its_numbers_are_two_cells_apart_in_every_layout(width: int) -> None:
    long = (
        "Write 😀 emoji notes, wait twelve seconds, 中文 then report back",
        "Read notes.txt and wait six seconds, then report back to me",
        "Wait ten seconds, echo bee, then report back to the parent",
    )
    snaps = [_snap(k, elapsed_ms=11_000, tokens=15, model="s-1") for k in range(5)]
    checked = 0
    for members in (1, 3, 5):
        rows = _screen(
            format_panel(snaps[:members], tasks=(long * 2)[:members], width=width,
                         height=panel_height(members, terminal_rows=30), profile="worker")
        )
        for row in rows:
            gap = _gap(row)
            if gap is not None:
                checked += 1
                assert gap >= 2, (width, members, row)
    assert checked >= 6


@pytest.mark.parametrize("width", [60, 80])
def test_labels_keep_their_cut_through_the_numbers_gaining_digits(width: int) -> None:
    """9s -> 10s, 59s -> 1m00s and 99 -> 0.1k used to re-cut every label of the
    two-row layout at once (live: ``with the bash…files you saw.`` -> ``with
    the…you saw.``). Since review round 2 a label that fits beside the numbers
    as they are is shown whole until they grow into it, then cut once: each
    label is whole, or one cut, and never whole again after it was cut."""

    tasks = (
        "[TAG:A] Read notes.txt and wait six seconds, then report.",
        "[TAG:B] Wait ten seconds, echo bee, then report.",
        "[TAG:C] Write c-out.txt, wait fourteen seconds, then report.",
    )

    def labels(elapsed_ms: int, tokens: int) -> list[str]:
        snaps = [_snap(k, elapsed_ms=elapsed_ms, tokens=tokens) for k in range(3)]
        rows = _screen(format_panel(snaps, tasks=tasks, width=width, profile="worker"))
        return [re.sub(r" {2,}\S+( · \S+)*$", "", row) for row in rows[1::2]]

    frames = [labels(ms, tok) for ms, tok in
              ((9_000, 15), (10_000, 15), (59_000, 99), (60_000, 100), (61_000, 9_900))]
    for k, task in enumerate(tasks):
        seen = [frame[k] for frame in frames]
        whole = f"│ ● {k + 1}  {task}"
        cuts = [label for label in seen if label != whole]
        assert len(set(cuts)) <= 1, seen
        if cuts:
            assert seen.index(cuts[0]) == len(seen) - len(cuts), seen  # never whole again


# === F10: an ended group's member, and a headless bridge =====================


def test_a_member_of_an_ended_group_writes_nothing() -> None:
    bridge, ui, loop = _bridge()
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    bridge(_snap(0))
    loop.advance(0)
    bridge.linger("tc", {})
    bridge.dismiss()
    widgets = len(ui.widgets)
    bridge(_snap(0, elapsed_ms=9_500))  # still on its way out
    bridge(_snap(0, state="stopped"))
    assert ui.writes == [], "a statusline row for a call that was over"
    assert len(ui.widgets) == widgets
    # An ungrouped child (``/agents run``) still writes its row.
    bridge(SubagentProgress(id="sub-loner", profile="scout", state="running"))
    assert [key for key, _ in ui.writes] == ["subagent:sub-loner"]


def test_the_ended_members_it_remembers_are_bounded() -> None:
    bridge, _, _ = _bridge()
    for n in range(40):
        bridge.begin_group(f"tc-{n}", expected=8, tasks=("t",) * 8, profile="worker")
        for k in range(8):
            bridge.adopt(f"sub-{n}-{k}", f"tc-{n}", index=k)
        bridge.end_group(f"tc-{n}")
    assert len(bridge._retired) == progress_module._RETIRED_MAX
    assert "sub-39-7" in bridge._retired and "sub-0-0" not in bridge._retired


def test_a_headless_bridge_formats_nothing_until_a_ui_binds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    real = progress_module.format_panel

    def counting(*args: Any, **kwargs: Any) -> list[str]:
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(progress_module, "format_panel", counting)
    bridge, ui, loop = _bridge()
    bridge._api.runtime.ui = HEADLESS_UI_CONTEXT
    bridge.begin_group("tc", expected=1, profile="worker")
    bridge.adopt("sub-0", "tc", index=0)
    for step in range(20):
        bridge(_snap(elapsed_ms=step * 100))
        loop.advance(0.3)
    loop.advance(5)
    assert calls == 0
    assert loop.pending() == []
    # A UI that binds later is drawn on the next snapshot, and ticks.
    bridge._api.runtime.ui = ui
    bridge(_snap(elapsed_ms=9_000))
    loop.advance(0)
    assert calls == 1 and len(_panels(ui)) == 1
    assert len(loop.pending()) == 1
