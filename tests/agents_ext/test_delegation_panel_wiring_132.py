"""#132 — the delegation panel through the real extension (ADR-0278 D1, D2, D7).

``test_delegation_panel_132.py`` pins the layouts and the bridge on their own.
What only this layer can get wrong is the wiring: whether ``_execute`` opens a
group for EVERY call — one child and a resume included — whether a grouped
member stays off the statusline, whether a cancel leaves the children's record
on screen instead of blanking it, and whether the next prompt, a session
teardown and a teardown that races the cancel each take it down.

The bridge's redraws are made immediate here (its scheduler answers "no loop"),
because what is under test is WHICH frames the wiring causes, not when the loop
gets to them; the throttle and the tick are the other file's subject.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    BeforeAgentStartHookEvent,
    SessionShutdownHookEvent,
    ToolCallHookEvent,
)
from aelix_agents.panel import PANEL_WIDGET_KEY
from aelix_agents.progress import SubagentProgressBridge, group_status_key
from aelix_agents.stream import reduce_event
from aelix_agents.tool import AGENT_TOOL_NAME
from aelix_ai.models import Model
from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.subagent_contract import SubagentResult

from tests.agents_ext.test_agent_resume_tool_610 import _delegated, _resume
from tests.agents_ext.test_child_session_records import _parent
from tests.agents_ext.test_stopped_child_reports_604 import (
    _end,
    _message,
    _Parking,
    _scripted,
    _start,
    _tool_call,
)
from tests.agents_ext.test_tool_and_security import (
    _Bench,
    _bench,
    _call,
    _RecordingChannel,
    _text,
    _write_profile,
)
from tests.event_waits import wait_until

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _shown(lines: list[str] | None) -> list[str]:
    return [_SGR.sub("", line).rstrip() for line in lines or []]


def _watch(bench: _Bench) -> list[tuple[str, list[str] | None]]:
    """Record every panel write, and make the bridge draw at once."""

    widgets: list[tuple[str, list[str] | None]] = []

    def _set_widget(key: str, content: list[str] | None, options: Any = None) -> None:
        widgets.append((key, content))

    bench.ui.set_widget = _set_widget  # type: ignore[attr-defined]
    bridge = bench.ext._progress
    assert bridge is not None
    bridge._call_later = lambda delay, callback: None  # noqa: SLF001 — draw at once
    return widgets


def _frames(widgets: list[tuple[str, list[str] | None]]) -> list[list[str]]:
    return [_shown(lines) for key, lines in widgets if key == PANEL_WIDGET_KEY and lines]


class _Working(_RecordingChannel):
    """A child that reads, runs a command, answers — published line by line, the
    way ``PrintChannel._pump_stdout`` drives ``on_stream``."""

    async def run(self, plan: Any, *, child: Any = None, on_stream: Any = None) -> SubagentResult:
        child.state = "running"
        for event in (
            {"type": "agent_start"},
            {"type": "turn_start"},
            _start("r1", "read", path="notes.txt"),
            _end("r1"),
            _start("b1", "bash", command="echo hi"),
            _end("b1"),
            _message("All done."),
        ):
            reduce_event(child.stream, event)
            on_stream(child.stream)
        return await super().run(plan, child=child, on_stream=on_stream)


async def test_a_single_delegation_gets_the_panel_and_no_statusline_row(tmp_path: Path) -> None:
    """D1 + D2 at N = 1. Until #132 a call of one opened no group and wrote a
    per-child statusline row; now it is drawn as the one-child block, and the
    statusline is left to the disclosure row alone (none here: not yolo)."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True, channel=_Working())
    widgets = _watch(bench)

    result = await _call(bench, {"profile": "scout", "task": "go"}, tool_call_id="tc-solo")

    assert result.is_error is False, _text(result)
    assert bench.ui.writes == {}, "a grouped child wrote a statusline row"
    frames = _frames(widgets)
    assert frames, "the panel was never drawn"
    assert all(len(frame) == 5 for frame in frames), frames
    assert frames[0][0].startswith("│ ● agent scout · starting"), frames[0]
    assert any(frame[1] == "│   ▶ read notes.txt" for frame in frames), frames
    assert frames[-1][0].startswith("│ ✓ agent scout · done"), frames[-1]
    assert frames[-1][1:] == ["│   ✓ read notes.txt", "│   ✓ $ echo hi", "│", "│   “All done.”"]
    # A normal finish clears it: the result card in the transcript is the record.
    assert widgets[-1] == (PANEL_WIDGET_KEY, None)
    assert bench.ext._progress._groups == {}  # type: ignore[union-attr]


async def test_a_batch_gets_one_panel_and_no_statusline_row_at_all(tmp_path: Path) -> None:
    """D2 at N >= 2: no per-child row and no aggregate row — the panel's header
    says what the aggregate said."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True, channel=_Working())
    widgets = _watch(bench)

    await _call(
        bench,
        {"profile": "scout", "mode": "parallel", "tasks": ["alpha", "bravo", "charlie"]},
        tool_call_id="tc-batch",
    )

    assert bench.ui.writes == {}
    assert group_status_key("tc-batch") not in bench.ui.writes
    frames = _frames(widgets)
    assert {len(frame) for frame in frames} == {7}
    assert frames[-1][0].startswith("│ 3 agents · scout"), frames[-1]
    assert [row.split()[3] for row in frames[-1][1::2]] == ["alpha", "bravo", "charlie"]
    assert widgets[-1] == (PANEL_WIDGET_KEY, None)


async def test_a_resume_gets_the_one_child_panel_and_says_it_is_a_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = await _delegated(tmp_path, has_ui=True)
    widgets = _watch(scene.bench)
    opened: list[dict[str, Any]] = []
    begin = SubagentProgressBridge.begin_group

    def _recording(self: SubagentProgressBridge, key: str, **kwargs: Any) -> None:
        opened.append(kwargs)
        begin(self, key, **kwargs)

    monkeypatch.setattr(SubagentProgressBridge, "begin_group", _recording)

    result = await _resume(scene, "now write the summary")

    assert result.is_error is False, _text(result)
    assert [(o["expected"], o["resumed"], o["profile"]) for o in opened] == [(1, True, "scout")]
    assert opened[0]["tasks"] == ("now write the summary",)
    frames = _frames(widgets)
    assert frames and frames[0][0].startswith("│ ● agent scout (resumed) · starting"), frames
    assert all(len(frame) == 5 for frame in frames), frames


# === Esc: the record stays until the next prompt (D7) =========================


async def _cancelled(
    tmp_path: Path, args: dict[str, Any], *, parked: int
) -> tuple[AgentHarness, _Bench, list[tuple[str, list[str] | None]], list[list[Any]]]:
    """A real harness whose model delegates; Esc while the children run.

    ``_Parking`` writes ``<task>.md``, says it is halfway and parks inside an
    ``edit`` — the shape ADR-0276's L2 measured with a real child.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    channel = _Parking()
    bench = _bench(tmp_path, has_ui=True, channel=channel)  # type: ignore[arg-type]
    widgets = _watch(bench)
    session, _repo, _path = await _parent(tmp_path)
    bench.ext.session = lambda: session
    seen: list[list[Any]] = []
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            extensions=[bench.extension],
            runtime=bench.runtime,
            stream_fn=_scripted(_tool_call(args), seen),
        )
    )
    turn = asyncio.ensure_future(harness.prompt("delegate it"))
    await wait_until(
        lambda: channel.parked >= parked or turn.done(),
        what="the delegation to be running",
        detail=lambda: f"parked={channel.parked} turn_done={turn.done()}",
    )
    assert not turn.done(), "the turn ended before it could be cancelled"
    await harness.abort()
    await asyncio.wait_for(turn, 30)
    return harness, bench, widgets, seen


async def test_esc_leaves_the_child_s_record_on_screen_until_the_next_prompt(
    tmp_path: Path,
) -> None:
    harness, bench, widgets, _seen = await _cancelled(
        tmp_path, {"profile": "scout", "task": "look"}, parked=1
    )

    key, last = widgets[-1]
    assert key == PANEL_WIDGET_KEY and last is not None, "Esc blanked the panel"
    frame = _shown(last)
    assert len(frame) == 5
    assert frame[0].startswith("│ ■ agent scout · stopped — the turn was cancelled"), frame
    assert frame[1] == "│   ✓ write look.md"
    assert frame[2] == "│   ■ edit shared.py · no result"
    # From the SAME envelopes the model's report was built from (D14).
    assert frame[3] == "│   changed: look.md"
    assert frame[4] == "│   Ask to continue it and the model can resume it."
    assert len(harness._next_turn_queue) == 1, "the model's report was not queued"

    await harness.prompt("what happened?")
    assert widgets[-1] == (PANEL_WIDGET_KEY, None), "the next prompt left the frame up"
    assert bench.ext._progress._groups == {}  # type: ignore[union-attr]


async def test_esc_during_a_batch_keeps_every_member_s_row(tmp_path: Path) -> None:
    _harness, _bench_, widgets, _seen = await _cancelled(
        tmp_path,
        {"profile": "scout", "mode": "parallel", "tasks": ["finish", "one", "two"]},
        parked=2,
    )

    frame = _shown(widgets[-1][1])
    assert len(frame) == 7, frame
    assert frame[0] == "│ 3 agents · stopped — the turn was cancelled · ask to continue them"
    assert frame[1].startswith("│ ✓ 1  finish"), frame[1]
    assert frame[3].startswith("│ ■ 2  one"), frame[3]
    assert frame[4] == "│      ✓ ■ edit shared.py · no result · changed: one.md"
    assert frame[5].startswith("│ ■ 3  two"), frame[5]


async def test_a_session_teardown_takes_the_frame_down(tmp_path: Path) -> None:
    _harness, bench, widgets, _seen = await _cancelled(
        tmp_path, {"profile": "scout", "task": "look"}, parked=1
    )
    assert widgets[-1][1] is not None

    handler = bench.extension.handlers["session_shutdown"][0]
    await handler(SessionShutdownHookEvent(reason="new"), bench.ctx)

    assert widgets[-1] == (PANEL_WIDGET_KEY, None)
    assert bench.ext._progress._groups == {}  # type: ignore[union-attr]


async def test_the_next_prompt_s_hook_is_what_dismisses_it(tmp_path: Path) -> None:
    """The hook itself, without a harness: ``before_agent_start`` is the
    dismissal, so a slash command that runs no prompt leaves the frame up."""

    _harness, bench, widgets, _seen = await _cancelled(
        tmp_path, {"profile": "scout", "task": "look"}, parked=1
    )
    handler = bench.extension.handlers["before_agent_start"][0]
    await handler(BeforeAgentStartHookEvent(prompt="next"), bench.ctx)
    assert widgets[-1] == (PANEL_WIDGET_KEY, None)


async def test_a_teardown_that_beat_the_cancel_leaves_no_frame(tmp_path: Path) -> None:
    """``session_shutdown`` runs before the harness aborts the turn
    (``_teardown_current``: emit, invalidate, dispose). The teardown's ``clear``
    has already taken the group when the cancel reaches ``_execute``, so the
    ``linger`` there finds nothing and draws nothing on the next session's
    screen."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    channel = _Parking()
    bench = _bench(tmp_path, has_ui=True, channel=channel)  # type: ignore[arg-type]
    widgets = _watch(bench)
    args = {"profile": "scout", "task": "look"}
    blocked = await bench.hook(
        ToolCallHookEvent(tool_call_id="tc-race", tool_name=AGENT_TOOL_NAME, args=dict(args))
    )
    assert blocked is None or not blocked.block
    execute = bench.tool.execute
    assert execute is not None
    task = asyncio.ensure_future(execute(dict(args), ToolExecutionContext(tool_call_id="tc-race")))
    await wait_until(lambda: channel.parked >= 1, what="the child to be running")
    assert _frames(widgets), "the panel was never drawn"

    bench.ext._progress.clear()  # type: ignore[union-attr] — what session_shutdown does
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert widgets[-1] == (PANEL_WIDGET_KEY, None), _shown(widgets[-1][1])
    assert bench.ext._progress._groups == {}  # type: ignore[union-attr]
