"""#612 (ADR-0307, contract r5 §4): the child-event hub, its producer and the host controls.

``runtime._publish`` — the sole producer of a child's progress — feeds the
build's :class:`~aelix_agents.child_events.ChildEventHub` with each snapshot and
the reducer's tool transitions; the hub emits ``aelix_child`` frames on a change
(``state`` first, every ``tool_start`` with exactly one ``tool_end``, ``text``,
``stuck``, ``wrap_up``, the terminal ``state`` with its ``end``), keeps a ring per
child and answers ``children/events``' cursor table; every projected string is
capped. :class:`~aelix_agents.host_controls.HostControls` reaches #123's doors on
real rows.

Fails on ``567753c0``: ``aelix_agents.child_events`` and
``aelix_agents.host_controls`` do not exist, ``_SubagentRuntimeImpl`` takes no
``events``, ``_StreamState`` has no ``transitions`` and ``RunningChild`` no
``parent_tool_call_id``, ``envelope``, ``cancelled``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.child_events import (
    ENDED_KEPT,
    OPEN_CALLS_MAX,
    RING_MAX,
    ChildEventHub,
    cap,
    wire_call_id,
)
from aelix_agents.command_wire import CommandWire, new_wrap_up
from aelix_agents.detach import AdoptedLink
from aelix_agents.envelope import REASON_OWN_ABORT, WRAP_UP_REASON, child_end
from aelix_agents.host_controls import SESSION_CHANGED, HostControls
from aelix_agents.print_channel import RunningChild
from aelix_agents.runtime import (
    RESUME_HINT,
    WRAP_UP_RESUME_HINT,
    SpawnRecordMeta,
    SubagentHost,
    _record_wrap_up,
    _SubagentRuntimeImpl,
)
from aelix_agents.stream import Fingerprint, reduce_event
from aelix_coding_agent.subagent_contract import (
    CHILD_EVENTS,
    SubagentControls,
    SubagentResult,
)

from tests.agents_ext._detach_rig import reap_children  # noqa: F401 — the fixture
from tests.agents_ext.test_child_session_records import _Channel, _grant, _parent, _resolved
from tests.event_waits import wait_until

# === a runtime with a hub, rows fed by hand ===========================================


def _runtime(
    *, channel: Any = None, cwd: str = "/nowhere"
) -> tuple[_SubagentRuntimeImpl, ChildEventHub, list[tuple[str, dict[str, Any]]]]:
    hub = ChildEventHub(epoch="e-1")
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: cwd), channel=channel or _Channel(), events=hub)
    frames: list[tuple[str, dict[str, Any]]] = []
    hub.subscribe(lambda event, data: frames.append((event, data)))
    return runtime, hub, frames


def _child(child_id: str = "sub-1", **kwargs: Any) -> RunningChild:
    return RunningChild(id=child_id, profile="scout", parent_tool_call_id="tc-1", **kwargs)


def _feed(runtime: _SubagentRuntimeImpl, child: RunningChild, *events: dict[str, Any]) -> None:
    """Each event reduced and then published, as the stdout pump does per line."""

    for event in events:
        reduce_event(child.stream, event)
        runtime._publish(child, child.stream, None)


def _start(call_id: Any, name: str = "read") -> dict[str, Any]:
    return {"type": "tool_execution_start", "tool_name": name, "tool_call_id": call_id, "args": {"path": "a.py"}}


def _end(call_id: str, *, error: bool = False) -> dict[str, Any]:
    return {"type": "tool_execution_end", "tool_call_id": call_id, "is_error": error, "result": {}}


def _say(text: str) -> dict[str, Any]:
    return {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


def _tools(frames: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, str, str]]:
    return [
        (event, data["callId"], data.get("status", data.get("name", "")))
        for event, data in frames
        if event in ("tool_start", "tool_end")
    ]


_AGENT_START = {"type": "agent_start"}
_TURN_START = {"type": "turn_start"}
_AGENT_END = {"type": "agent_end"}


# === frames ===========================================================================


def test_the_first_snapshot_is_a_state_frame_with_the_identity_and_every_frame_has_id_seq_epoch() -> None:
    runtime, hub, frames = _runtime()
    child = _child()
    runtime._publish(child, child.stream, None)
    assert frames == [
        (
            "state",
            {
                "id": "sub-1",
                "seq": 1,
                "epoch": "e-1",
                "state": "starting",
                "profile": "scout",
                "sessionId": None,
                "jobId": None,
                "parentToolCallId": "tc-1",
                "background": False,
                "wrappingUp": False,
            },
        )
    ]
    runtime._publish(child, child.stream, None)
    assert len(frames) == 1, "a frame only on a change"
    child.state = "running"
    runtime._publish(child, child.stream, None)
    assert [(event, data["seq"], data["state"]) for event, data in frames[1:]] == [("state", 2, "running")]
    assert set(CHILD_EVENTS) == {"state", "tool_start", "tool_end", "text", "stuck", "wrap_up", "approval"}


def test_five_open_calls_then_agent_end_are_five_starts_and_five_no_result_ends_in_start_order() -> None:
    """The reducer's ``recent`` keeps four of five open calls at ``agent_end`` (Codex 9); the hub closes all five."""

    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    _feed(runtime, child, _AGENT_START, _TURN_START, *(_start(f"c{n}") for n in range(5)), _AGENT_END)
    assert _tools(frames) == [("tool_start", f"c{n}", "read") for n in range(5)] + [
        ("tool_end", f"c{n}", "no_result") for n in range(5)
    ]
    assert len(child.stream.recent) == 4, "the display window trimmed one; the hub did not"


def test_a_turn_boundary_closes_the_ended_turns_open_calls_before_the_next_turns_frames() -> None:
    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    _feed(runtime, child, _AGENT_START, _TURN_START, _start("a"), _start("b"), _end("a", error=True), _TURN_START, _start("c"), _end("c"))
    assert _tools(frames) == [
        ("tool_start", "a", "read"),
        ("tool_start", "b", "read"),
        ("tool_end", "a", "failed"),
        ("tool_end", "b", "no_result"),
        ("tool_start", "c", "read"),
        ("tool_end", "c", "ok"),
    ]


def test_an_end_without_an_emitted_start_is_dropped_and_a_start_without_an_id_is_not_emitted() -> None:
    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    _feed(runtime, child, _AGENT_START, _TURN_START, _start(7), _end("never-started"), _start("x"), _end("x"))
    assert _tools(frames) == [("tool_start", "x", "read"), ("tool_end", "x", "ok")]


def test_a_start_past_the_open_bound_emits_neither_a_start_nor_an_end() -> None:
    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    over = f"c{OPEN_CALLS_MAX}"
    _feed(runtime, child, _AGENT_START, _TURN_START, *(_start(f"c{n}") for n in range(OPEN_CALLS_MAX + 1)))
    _feed(runtime, child, _end(over), _AGENT_END)
    tools = _tools(frames)
    assert sum(1 for event, _id, _s in tools if event == "tool_start") == OPEN_CALLS_MAX
    assert sum(1 for event, _id, _s in tools if event == "tool_end") == OPEN_CALLS_MAX
    assert all(call_id != over for _event, call_id, _s in tools)


def test_every_emitted_start_has_exactly_one_end_across_a_retry_and_the_rows_end() -> None:
    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    _feed(runtime, child, _AGENT_START, _TURN_START, _start("a"), _AGENT_END, _AGENT_START, _TURN_START, _start("b"))
    child.state = "done"
    runtime._publish(child, child.stream, None, final=True)
    starts = [c for e, c, _s in _tools(frames) if e == "tool_start"]
    ends = [c for e, c, _s in _tools(frames) if e == "tool_end"]
    assert sorted(starts) == sorted(ends) == ["a", "b"]
    assert frames[-1][0] == "state" and frames[-1][1]["state"] == "done", "the terminal frame is the row's last"


def test_text_stuck_and_wrap_up_frames_each_on_a_change() -> None:
    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    _feed(runtime, child, _AGENT_START, _TURN_START, _say("halfway"), _say("halfway"))
    assert [(e, d["text"]) for e, d in frames if e == "text"] == [("text", "halfway")]
    for _ in range(4):
        child.stream.fingerprints.append(Fingerprint(name="read", args="h", result="r", is_error=False))
    runtime._publish(child, child.stream, None)
    assert [d["stuck"] for e, d in frames if e == "stuck"] == ["repeat"]
    child.stream.fingerprints.clear()
    runtime._publish(child, child.stream, None)
    assert [d["stuck"] for e, d in frames if e == "stuck"] == ["repeat", None], "cleared, as a change"
    _record_wrap_up(runtime, child, new_wrap_up("user"))
    _record_wrap_up(runtime, child, None)
    assert [d["requested"] for e, d in frames if e == "wrap_up"] == [True, False]


class _ClosingStdin:
    """A stdin whose pipe is closing before the first byte: the wire answers ``not_sent``."""

    class transport:  # noqa: N801 — the attribute ``CommandWire`` reads
        @staticmethod
        def is_closing() -> bool:
            return True


async def test_a_wrap_up_that_was_not_sent_is_taken_back_on_the_wire_too() -> None:
    """``runtime.wrap_up``'s own ``not_sent`` path (``runtime.py`` ``_record_wrap_up(None)``; Codex 10)."""

    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    child.proc = object()
    child.wire = CommandWire("r")
    child.wire.attach(_ClosingStdin())
    runtime._children[child.id] = child
    delivery = await runtime.wrap_up(child.id)
    assert delivery.outcome == "not_sent"
    assert [d["requested"] for e, d in frames if e == "wrap_up"] == [True, False]


# === the terminal frame ===============================================================


@pytest.mark.parametrize(
    ("result", "stopped", "cancelled", "consumed", "end"),
    [
        (SubagentResult(id="x", profile="p", ok=True, status="ok", summary="s"), True, True, True, "completed"),
        (SubagentResult(id="x", profile="p", ok=False, status="aborted", summary="s"), True, True, True, "stopped"),
        (SubagentResult(id="x", profile="p", ok=False, status="aborted", summary="s"), False, True, True, "cancelled"),
        (SubagentResult(id="x", profile="p", ok=False, status="aborted", summary="s"), False, False, True, "wrapped_up"),
        (SubagentResult(id="x", profile="p", ok=False, status="aborted", summary="s"), False, False, False, "failed"),
        (SubagentResult(id="x", profile="p", ok=False, status="aborted", summary="s", stop_reason="aborted"), False, False, True, "failed"),
        (SubagentResult(id="x", profile="p", ok=False, status="error", summary="s"), True, True, True, "failed"),
        (SubagentResult(id="x", profile="p", ok=False, status="declined", summary="s"), False, False, False, "failed"),
        (None, True, True, False, "cancelled"),
        (None, True, False, False, "stopped"),
        (None, False, False, False, "failed"),
    ],
)
def test_child_end_keeps_the_wrap_up_verdicts_precedence(
    result: SubagentResult | None, stopped: bool, cancelled: bool, consumed: bool, end: str
) -> None:
    assert child_end(result, stopped=stopped, cancelled=cancelled, wrap_up_consumed=consumed) == end


@pytest.mark.parametrize(
    ("stop_reason", "exit_code", "status", "reason"),
    [("aborted", 0, "aborted", REASON_OWN_ABORT), ("error", 1, "error", "exited with code 1")],
)
def test_a_consumed_wrap_up_never_masks_the_childs_own_failure(
    stop_reason: str, exit_code: int, status: str, reason: str
) -> None:
    """#612 fix round 1, Codex C-M8 (Codex r1a's ``ends`` probe, the production envelope builder).

    The wrap-up is weighed after the run's own verdict, and ``wrapped_up`` comes
    only from turning a run that would be ``ok``. On ``111e02d7`` both runs became
    ``aborted`` "wrapped up at the user's request" and ended ``wrapped_up``.
    """

    from aelix_agents.envelope import build_result
    from aelix_agents.stream import _StreamState

    state = _StreamState(saw_agent_end=True, stop_reason=stop_reason)
    facts: dict[str, Any] = dict(id="sub-a", profile="scout", state=state, outcome="ok", exit_code=exit_code)
    plain = build_result(**facts)
    wrapped = build_result(**facts, wrap_up=new_wrap_up("user"), wrap_up_consumed=True)
    assert (plain.status, plain.reason) == (status, reason)
    assert (wrapped.ok, wrapped.status, wrapped.reason, wrapped.note) == (False, status, reason, None)
    assert child_end(wrapped, stopped=False, cancelled=False, wrap_up_consumed=True) == "failed"


def test_a_lingered_run_turned_by_a_wrap_up_carries_no_lingering_note() -> None:
    """Fix round 1, Codex C-M8: the lingering note is a fact about a SUCCESSFUL run; a wrapped-up one is not."""

    from aelix_agents.envelope import LINGERED_NOTE, build_result
    from aelix_agents.stream import _StreamState, reduce_event

    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, _say("all done"))
    reduce_event(state, {"type": "agent_end"})
    facts: dict[str, Any] = dict(id="sub-a", profile="scout", state=state, outcome="ok", exit_code=-9, lingered=True)
    assert build_result(**facts).note == LINGERED_NOTE
    wrapped = build_result(**facts, wrap_up=new_wrap_up("user"), wrap_up_consumed=True)
    assert (wrapped.status, wrapped.note) == ("aborted", None)


def test_a_consumed_wrap_up_on_a_clean_run_still_ends_wrapped_up() -> None:
    """The guard beside it: a run whose own verdict is ``ok`` is the one the wrap-up turns."""

    from aelix_agents.envelope import build_result
    from aelix_agents.stream import _StreamState

    state = _StreamState(saw_agent_end=True, stop_reason="end_turn")
    result = build_result(
        id="sub-a", profile="scout", state=state, outcome="ok", exit_code=0,
        wrap_up=new_wrap_up("model"), wrap_up_consumed=True,
    )
    assert (result.ok, result.status, result.reason) == (False, "aborted", WRAP_UP_REASON.format(origin="model"))
    assert child_end(result, stopped=False, cancelled=False, wrap_up_consumed=True) == "wrapped_up"


class _EndChannel:
    """A channel whose run ends as ``kind`` says: the envelope (or none) each end case has."""

    resumes_agents = True

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, plan: Any, *, child: RunningChild, on_stream: Any = None) -> SubagentResult:
        self.arrived.set()
        if self.kind == "raise":
            raise RuntimeError("the channel broke")
        if self.kind in ("park", "stop"):
            await self.release.wait()
        status, reason = "ok", None
        if self.kind == "stop":
            child.state = "stopped"
            status, reason = "aborted", "stopped by the session (stop, /new, /resume, /fork or quit)"
        elif self.kind == "own_abort":
            child.state = "error"
            status, reason = "aborted", REASON_OWN_ABORT
        elif self.kind == "wrapped":
            wrap = new_wrap_up("user")
            child.wrap_up, child.stream.wrap_up_head, child.stream.wrap_up_read = wrap, wrap.head, True
            child.state = "done"
            status, reason = "aborted", WRAP_UP_REASON.format(origin="user")
        else:
            child.state = "done"
        child.proc = object()
        return SubagentResult(id=plan.id, profile=plan.resolved.name, ok=status == "ok", status=status, summary="s", reason=reason)


async def _run_to_end(kind: str) -> list[tuple[str, dict[str, Any]]]:
    channel = _EndChannel(kind)
    runtime, _hub, frames = _runtime(channel=channel)
    task = asyncio.ensure_future(runtime.spawn_granted(_grant(), _resolved(), "go", record=SpawnRecordMeta(tool_call_id="tc-9", mode="single")))
    await asyncio.wait_for(channel.arrived.wait(), 10)
    if kind == "stop":
        (row,) = runtime._children.values()
        stopping = asyncio.ensure_future(runtime.stop(row.id))
        await asyncio.sleep(0)
        channel.release.set()
        await asyncio.wait_for(stopping, 10)
    if kind == "park":
        task.cancel()
    with pytest.raises((asyncio.CancelledError, RuntimeError)) if kind in ("park", "raise") else _nothing():
        await asyncio.wait_for(task, 10)
    return frames


class _nothing:  # noqa: N801 — a context that expects no exception
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: Any) -> bool:
        return False


@pytest.mark.parametrize(
    ("kind", "end", "outcome", "state"),
    [
        ("ok", "completed", "ok", "done"),
        ("wrapped", "wrapped_up", "aborted", "done"),
        ("stop", "stopped", "aborted", "stopped"),
        ("park", "cancelled", None, "error"),
        ("own_abort", "failed", "aborted", "error"),
        ("raise", "failed", None, "error"),
    ],
)
async def test_the_terminal_frame_carries_the_end_for_each_way_a_run_ends(
    kind: str, end: str, outcome: str | None, state: str
) -> None:
    frames = await _run_to_end(kind)
    terminal = [data for event, data in frames if event == "state" and "end" in data]
    assert len(terminal) == 1, "exactly one terminal frame"
    assert frames[-1][1] is terminal[0], "and it is the row's last"
    assert (terminal[0]["end"], terminal[0]["outcome"], terminal[0]["state"]) == (end, outcome, state)
    assert terminal[0]["parentToolCallId"] == "tc-9"
    if kind == "stop":
        assert all(d["state"] != "stopped" for e, d in frames[:-1] if e == "state"), "held back until the end"


async def test_a_silent_childs_receipt_gives_a_state_frame_with_its_session_before_any_output(tmp_path: Path) -> None:
    session, _repo, _path = await _parent(tmp_path)
    channel = _Channel(park=True)
    hub = ChildEventHub()
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path / "p"), session=lambda: session), channel=channel, events=hub
    )
    frames: list[tuple[str, dict[str, Any]]] = []
    hub.subscribe(lambda event, data: frames.append((event, data)))
    task = asyncio.ensure_future(runtime.spawn_granted(_grant(), _resolved(), "go"))
    try:
        await asyncio.wait_for(channel.arrived.wait(), 10)
        (row,) = runtime._children.values()
        assert row.session_id is not None
        assert [(e, d["sessionId"]) for e, d in frames] == [("state", None), ("state", row.session_id)]
    finally:
        channel.release.set()
        await asyncio.wait_for(task, 10)


def test_a_ctrl_b_move_gives_a_state_frame_with_the_job_at_once() -> None:
    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    runtime._publish(child, child.stream, None)
    child.adopted = AdoptedLink(job_id="j-42")
    runtime._publish_identity(child)
    assert [(e, d["jobId"], d["background"]) for e, d in frames] == [("state", None, False), ("state", "j-42", True)]


@pytest.mark.skipif(sys.platform == "win32", reason="as test_detach_611: the gated rig's real child")
@pytest.mark.usefixtures("reap_children")
async def test_a_real_ctrl_b_move_gives_the_state_frame_with_the_job_before_the_child_speaks_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commit's ``announce`` publishes the row's identity once ``row.adopted`` names the job.

    The child is parked on its gate after ``halfway``, so nothing it prints can
    carry the job id: the frame comes from the move itself (contract r5 §4.1).
    """

    from aelix_coding_agent.jobs import activation

    from tests.agents_ext._detach_rig import finish, parked_call, row_of, script, tui_rig, within
    from tests.agents_ext.test_tool_and_security import _text
    from tests.jobs.conftest import wait_for

    monkeypatch.setattr(activation, "ACTIVE", True)
    gate = tmp_path / "gate"
    try:
        rig = await tui_rig(tmp_path, script=script("the moved answer", gate=gate, partial="halfway"))
        frames: list[tuple[str, dict[str, Any]]] = []
        rig.ext.runtime.events.subscribe(lambda event, data: frames.append((event, data)))
        turn = await parked_call(rig)
        row = row_of(rig)
        await wait_for(lambda: row.stream.last_text == "halfway")
        seen = len(frames)
        assert rig.jobs_ui.request_detach()
        assert "Moved to the background" in _text(await within(turn))
        (snap,) = rig.jobs()
        moved = frames[seen:]
        assert [(e, d["jobId"], d["background"]) for e, d in moved] == [("state", snap.job_id, True)]
        gate.touch()
        await finish(rig)
    finally:
        gate.touch()
        activation.install_service(None)


# === bounds and ids ===================================================================


def test_a_3_mib_tool_name_is_capped_and_the_row_stays_under_12_kib() -> None:
    runtime, hub, frames = _runtime()
    child = _child(state="running")
    huge = "x" * (3 * 1024 * 1024)
    _feed(runtime, child, _AGENT_START, _TURN_START, {"type": "tool_execution_start", "tool_name": huge, "tool_call_id": "c1", "args": {}})
    (row,) = hub.rows()
    assert row["currentTool"] is not None and len(row["currentTool"]) <= 120
    assert len(json.dumps(row, ensure_ascii=False).encode("utf-8")) <= 12 * 1024
    assert all(len(json.dumps(d, ensure_ascii=False).encode("utf-8")) <= 4 * 1024 for _e, d in frames)


def test_a_literal_h_id_and_a_long_id_map_to_different_wire_ids() -> None:
    long_id = "a" * 129
    hashed = wire_call_id(long_id)
    assert hashed == "h:" + hashlib.sha256(long_id.encode()).hexdigest()[:32]
    assert wire_call_id(hashed) != hashed, "a literal id in the reserved namespace is hashed too"
    assert wire_call_id("a" * 128) == "a" * 128
    assert wire_call_id("call_1") == "call_1"
    assert wire_call_id("\ud800").startswith("h:"), "an id UTF-8 cannot carry"


_WORST_RAW_IDS = {
    "four-byte": "\U0001f600" * 32,  # 128 UTF-8 bytes, kept raw
    "quotes": '"' * 64,  # 128 bytes once escaped, kept raw
    "controls": "\0" * 128,  # 768 bytes once escaped: hashed since fix round 1
    "long": "x" * (512 * 1024),
}


def _not_json(constant: str) -> Any:
    raise AssertionError(f"{constant} is not JSON; strict readers reject the line")


def _worst_case(raw_id: str) -> tuple[dict[str, int], int]:
    """Every projected string at its cap from four-byte characters, ``raw_id`` as both call ids,
    an approval open, usage values a child could report, the terminal frame (contract r5 §4.4).

    Sizes are of the COMPLETE serialized JSONL frame and row as the plane writes them.
    """

    from aelix_coding_agent.rpc._jsonl import serialize_json_line
    from aelix_coding_agent.subagent_contract import SubagentProgress, SubagentToolActivity

    wide = "\U0001f600" * 4096
    hub = ChildEventHub(epoch="e" * 32)
    frames: list[tuple[str, dict[str, Any]]] = []
    hub.subscribe(lambda event, data: frames.append((event, data)))
    child = RunningChild(id="sub-" + "f" * 12, profile=wide, parent_tool_call_id=raw_id, session_id="s" * 36, state="running")
    child.adopted = AdoptedLink(job_id="j" * 16)
    progress = SubagentProgress(
        id=child.id,
        profile=wide,
        state="running",
        current_tool=wide,
        model=wide,
        provider=wide,
        activity=tuple(SubagentToolActivity(name=wide, args=wide, status="running") for _ in range(4)),
        last_text=wide,
        stuck=wide,
        wrapping_up=True,
        elapsed_ms=10**4000,
        tokens=10**4000,
        cost=float("inf"),
    )
    hub.publish(None, child, progress, [("start", raw_id, wide, wide, 0)])
    hub.approval(child.id, raw_id, wide, "asked")
    row_line = serialize_json_line(hub.rows()[0])
    json.loads(row_line, parse_constant=_not_json)
    row_size = len(row_line.encode("utf-8"))
    hub.publish(None, child, progress, (), terminal={"end": "failed", "outcome": "error", "reason": wide, "note": wide})
    sizes: dict[str, int] = {}
    for event, data in frames:
        line = serialize_json_line({"type": "aelix_child", "event": event, "data": data}).encode("utf-8")
        json.loads(line, parse_constant=_not_json)
        sizes[event] = max(sizes.get(event, 0), len(line))
    return sizes, row_size


@pytest.mark.parametrize("raw_id", sorted(_WORST_RAW_IDS))
def test_the_largest_frame_and_row_stay_inside_the_bounds_as_the_wire_writes_them(raw_id: str) -> None:
    """Fix round 1, Codex C-M6: the bounds hold for the complete JSONL line, ids escaped.

    On ``111e02d7`` the ``controls`` id kept raw made the terminal frame 5,719 B
    (over 5 KiB) and a 4,001-digit token count made the row 14,427 B (over 12 KiB);
    the costs' ``inf`` made the row's line not JSON. ADR-0307 states the maxima
    these cases measure.
    """

    sizes, row_size = _worst_case(_WORST_RAW_IDS[raw_id])
    assert row_size <= 12 * 1024, row_size
    assert max(sizes.values()) <= 5 * 1024, sizes


def test_a_call_id_is_bounded_as_the_wire_writes_it() -> None:
    """Fix round 1, Codex C-M6: ``wire_call_id`` budgets the JSON-escaped bytes, not the raw ones."""

    for raw in ("\0" * 128, '"' * 65, "\\" * 65, "\x01"):
        wired = wire_call_id(raw)
        assert len(json.dumps(wired, ensure_ascii=False).encode("utf-8")) - 2 <= 128
    assert wire_call_id("\0" * 128).startswith("h:")
    assert wire_call_id('"' * 64) == '"' * 64
    assert wire_call_id('"' * 65).startswith("h:")
    assert wire_call_id("\U0001f600" * 32) == "\U0001f600" * 32
    assert wire_call_id("\U0001f600" * 33).startswith("h:")


def test_usage_values_are_bounded_and_strict_json() -> None:
    """Fix round 1, Codex C-M6: two ``1e308`` costs and a 4,001-digit token count, as a child reports them."""

    runtime, hub, _frames = _runtime()
    child = _child(state="running")
    for _ in range(2):
        event = _say("cost")
        event["message"]["usage"] = {"cost": {"total": 1e308}}
        _feed(runtime, child, event)
    (row,) = hub.rows()
    json.dumps(row, allow_nan=False)
    assert row["cost"] == sys.float_info.max
    event = _say("tokens")
    event["message"]["usage"] = {"totalTokens": 10**4000}
    _feed(runtime, child, event)
    (row,) = hub.rows()
    assert row["tokens"] == 2**53 - 1


@pytest.mark.parametrize(
    ("tokens", "cost", "want_tokens", "want_cost"),
    [
        (float("inf"), float("nan"), 2**53 - 1, 0.0),
        (float("-inf"), -1.5, 0, 0.0),
        (float("nan"), 10**400, 0, sys.float_info.max),
        (12.9, 0.25, 12, 0.25),
        (True, "1", 0, 0.0),
        # Fix round 2, F3 (the verify's mutant E7, ``_count`` without its clamp at 0): a
        # negative finite count reads 0, never a negative ``tokens``.
        (-5, 0.0, 0, 0.0),
        (-12.9, 0.0, 0, 0.0),
    ],
)
def test_a_rows_usage_is_normalised_at_projection(tokens: Any, cost: Any, want_tokens: int, want_cost: float) -> None:
    """Fix round 1, Codex C-M6: whatever a progress snapshot carries, the row's numbers are JSON and bounded."""

    from aelix_coding_agent.subagent_contract import SubagentProgress

    hub = ChildEventHub()
    child = _child(state="running")
    hub.publish(None, child, SubagentProgress(id=child.id, profile="scout", state="running", tokens=tokens, cost=cost))
    (row,) = hub.rows()
    json.dumps(row, allow_nan=False)
    assert (row["tokens"], row["cost"]) == (want_tokens, want_cost)


def test_the_open_calls_are_keyed_by_the_bounded_wire_id() -> None:
    """Fix round 1, Codex C-M7: 96 half-MiB ids kept 50,335,862 B alive in the open-call keys at ``111e02d7``."""

    runtime, hub, frames = _runtime()
    child = _child(state="running")
    raw = [f"{n}:" + "x" * (512 * 1024) for n in range(96)]
    for call_id in raw:
        _feed(runtime, child, _start(call_id))
    keys = list(hub._rows[child.id].open_calls)
    assert len(keys) == 96 and max(len(k) for k in keys) == 34
    _feed(runtime, child, _end(raw[7]))
    starts = {d["callId"] for e, d in frames if e == "tool_start"}
    (ended,) = [d["callId"] for e, d in frames if e == "tool_end"]
    assert ended == wire_call_id(raw[7]) and ended in starts
    assert len(hub._rows[child.id].open_calls) == 95


def test_cap_strips_control_characters_and_lone_surrogates_to_one_line() -> None:
    assert cap("a\nb\tc\x00d\ud800", 120) == "a b c d\ufffd"
    assert cap("y" * 200, 120) == "y" * 119 + "…"
    assert cap(None, 120) is None


# === replay ===========================================================================


def _row_with(hub: ChildEventHub, child_id: str, texts: int) -> RunningChild:
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: "/nowhere"), channel=_Channel(), events=hub)
    child = _child(child_id, state="running")
    runtime._publish(child, child.stream, None)
    for n in range(texts):
        _feed(runtime, child, _say(f"t{n}"))
    return child


def test_children_events_follows_the_cursor_table() -> None:
    hub = ChildEventHub(epoch="e")
    _row_with(hub, "sub-1", 3)  # seq 1 state, 2..4 text
    full = hub.events("sub-1", None)
    assert [e["data"]["seq"] for e in full["events"]] == [1, 2, 3, 4]
    assert (full["nextSeq"], full["lowSeq"], full["gap"], full["epoch"]) == (5, 1, False, "e")
    assert [e["data"]["seq"] for e in hub.events("sub-1", 2)["events"]] == [3, 4]
    assert hub.events("sub-1", 4)["events"] == [] and hub.events("sub-1", 0)["gap"] is False
    with pytest.raises(ValueError, match="ahead of this agent's events \\(next is 5\\)"):
        hub.events("sub-1", 5)
    with pytest.raises(ValueError, match="negative"):
        hub.events("sub-1", -1)
    with pytest.raises(ValueError, match="no agent 'sub-x' in this session"):
        hub.events("sub-x", None)


def test_a_cursor_older_than_the_ring_answers_every_kept_event_with_a_gap() -> None:
    hub = ChildEventHub()
    _row_with(hub, "sub-1", RING_MAX + 10)
    absent = hub.events("sub-1", None)
    assert len(absent["events"]) == RING_MAX and absent["lowSeq"] == 12 and absent["gap"] is True
    stale = hub.events("sub-1", 3)
    assert stale["gap"] is True and len(stale["events"]) == RING_MAX
    edge = hub.events("sub-1", 11)
    assert edge["gap"] is False and len(edge["events"]) == RING_MAX


def test_ended_rows_keep_their_ring_until_sixty_four_then_leave_a_tombstone() -> None:
    hub = ChildEventHub()
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: "/nowhere"), channel=_Channel(), events=hub)
    for n in range(ENDED_KEPT + 1):
        child = _child(f"sub-{n}", state="done")
        runtime._publish(child, child.stream, None, final=True)
    assert len(hub.rows()) == ENDED_KEPT
    assert hub.state_of("sub-0") == "done"
    with pytest.raises(ValueError, match="no longer kept \\(it ended failed\\)"):
        hub.events("sub-0", None)
    assert hub.events("sub-1", None)["events"][-1]["data"]["end"] == "failed"
    runtime._publish(_child("sub-0"), _child("sub-0").stream, None)
    assert "sub-0" not in {row["id"] for row in hub.rows()}, "a tombstoned id is never a row again"


# === the host controls on real rows ===================================================


def test_host_controls_is_the_contracts_protocol() -> None:
    assert isinstance(HostControls(ChildEventHub()), SubagentControls)


async def test_host_controls_refuse_by_state_and_reach_the_runtime_doors(tmp_path: Path) -> None:
    channel = _Channel(park=True)
    runtime, hub, _frames = _runtime(channel=channel)
    controls = HostControls(hub)
    task = asyncio.ensure_future(runtime.spawn_granted(_grant(), _resolved(), "go"))
    await asyncio.wait_for(channel.arrived.wait(), 10)
    (row,) = runtime._children.values()
    not_started = await controls.steer(row.id, "hi", "steer")
    assert (not_started["outcome"], not_started["reason"]) == ("refused", "the agent has not started yet; retry")
    assert (await controls.wrap_up(row.id))["reason"] == "the agent has not started yet; retry"
    row.wire, row.proc = CommandWire("r"), object()
    row.wire.broken = True
    row.wire.attach(object())
    assert (await controls.steer(row.id, "hi", "follow_up"))["reason"] == "the agent's inbox is broken; stop or resume it"
    with pytest.raises(ValueError, match="no agent 'sub-nope' in this session"):
        await controls.steer("sub-nope", "hi", "steer")
    stopped = asyncio.ensure_future(controls.stop(row.id))
    await wait_until(lambda: row.stopped, what="the stop door to mark the row")
    channel.release.set()
    assert (await asyncio.wait_for(stopped, 10))["state"] in ("starting", "stopped")
    await asyncio.wait_for(task, 10)
    assert hub.state_of(row.id) == "stopped"
    ended = await controls.steer(row.id, "late", "steer")
    assert (ended["outcome"], ended["reason"]) == ("refused", RESUME_HINT)
    assert (await controls.wrap_up(row.id))["reason"] == WRAP_UP_RESUME_HINT
    assert await controls.stop(row.id) == {"state": "stopped"}
    controls.close()
    for door in (controls.steer(row.id, "x", "steer"), controls.wrap_up(row.id), controls.stop(row.id)):
        with pytest.raises(ValueError, match=SESSION_CHANGED):
            await door
    assert controls.list()[0]["id"] == row.id, "reads go on after close"


@pytest.mark.skipif(sys.platform == "win32", reason="the stub child's pipes are POSIX here")
async def test_host_controls_steer_and_wrap_up_a_live_child(tmp_path: Path) -> None:
    from tests.agents_ext.test_intervention_doors_123 import _INBOX_STUB, _channel, _halfway

    (tmp_path / "p").mkdir()
    runtime, hub, frames = _runtime(channel=_channel(script=_INBOX_STUB), cwd=str(tmp_path / "p"))
    controls = HostControls(hub)
    task = asyncio.ensure_future(runtime.spawn_granted(_grant(), _resolved(), "go", record=SpawnRecordMeta(tool_call_id="tc-1", mode="single")))
    row = await _halfway(runtime, task)
    steered = await controls.steer(row.id, "NOTE", "steer")
    assert steered == {"outcome": "queued", "phase": "streaming", "verb": "steer", "ended": False, "reason": None}
    second = await controls.wrap_up(row.id)
    assert (second["outcome"], second["verb"]) == ("queued", "wrap_up")
    again = await controls.wrap_up(row.id)
    assert (again["outcome"], again["reason"]) == ("refused", "a wrap-up was already requested for this agent")
    result = await asyncio.wait_for(task, 30)
    assert result.status == "aborted"
    terminal = [d for e, d in frames if e == "state" and "end" in d]
    assert [(d["end"], d["outcome"], d["reason"]) for d in terminal] == [
        ("wrapped_up", "aborted", "wrapped up at the user's request")
    ], "a host's wrap-up is the user's"
    assert [d["requested"] for e, d in frames if e == "wrap_up"] == [True]
    assert [e for e, _d in frames if e.startswith("tool_")] == ["tool_start", "tool_end"]


async def test_a_spawned_job_feeds_the_builds_hub_and_its_stop_goes_through_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.jobs import activation

    from tests.agents_ext.test_agent_background_611 import _rig, _script, _spawned_id

    monkeypatch.setattr(activation, "ACTIVE", True)
    gate = tmp_path / "gate"
    try:
        rig = await _rig(tmp_path, script=_script(gate=gate, partial="working"))
        runtime = rig.ext.runtime
        assert runtime is not None and isinstance(runtime.controls, HostControls)
        hub = runtime.events
        frames: list[tuple[str, dict[str, Any]]] = []
        hub.subscribe(lambda event, data: frames.append((event, data)))
        _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "count"}, tool_call_id="tc-spawn"))
        await wait_until(lambda: any(e == "text" for e, _d in frames), what="the job's row to speak")
        (row,) = runtime.controls.list()
        assert row["background"] is True and row["jobId"] == rig.jobs()[0].job_id
        assert row["parentToolCallId"] == "tc-spawn"
        answer = await runtime.controls.stop(row["id"])
        assert answer["state"] == "running", "the job's stop returns before its legs"
        assert rig.service.snapshot(row["jobId"]).state == "stopping", "asked of the job service"
        snap = await rig.service.stop(row["jobId"], wait=10.0)
        assert snap is not None and snap.state in ("stopped", "done", "failed")
        await wait_until(lambda: any("end" in d for e, d in frames if e == "state"), what="the terminal frame")
        assert frames[-1][1]["end"] == "stopped"
        await rig.service.shutdown()
    finally:
        gate.touch()
        activation.install_service(None)


# === the edges a mutant found ==========================================================


def test_a_row_publishes_nothing_after_its_terminal_frame_and_a_first_final_publish_is_one_frame() -> None:
    runtime, _hub, frames = _runtime()
    child = _child(state="done")
    runtime._publish(child, child.stream, None, final=True)
    assert [(e, d["state"], d["end"]) for e, d in frames] == [("state", "done", "failed")]
    child.state = "running"
    _feed(runtime, child, _say("after the end"))
    assert len(frames) == 1


def test_a_turn_start_alone_closes_the_ended_turns_calls_before_the_next_turns_text() -> None:
    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    _feed(runtime, child, _AGENT_START, _TURN_START, _start("a"), _TURN_START, _say("thinking"))
    assert [(e, d.get("status") or d.get("text") or d.get("name")) for e, d in frames[1:]] == [
        ("tool_start", "read"),
        ("tool_end", "no_result"),
        ("text", "thinking"),
    ]


def test_a_call_of_a_retried_run_stays_open_until_its_own_end() -> None:
    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    _feed(runtime, child, _AGENT_START, _TURN_START, _start("a"), _AGENT_END, _AGENT_START, _TURN_START, _start("b"), _end("b"))
    assert _tools(frames) == [
        ("tool_start", "a", "read"),
        ("tool_end", "a", "no_result"),
        ("tool_start", "b", "read"),
        ("tool_end", "b", "ok"),
    ]


def test_a_rows_seq_and_low_seq_are_its_last_and_oldest_kept_frames() -> None:
    hub = ChildEventHub()
    _row_with(hub, "sub-1", 2)
    (row,) = hub.rows()
    assert (row["seq"], row["lowSeq"], row["end"], row["lastText"]) == (3, 1, None, "t1")


def test_the_tombstones_are_bounded_too(monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_agents import child_events

    monkeypatch.setattr(child_events, "ENDED_KEPT", 1)
    monkeypatch.setattr(child_events, "TOMBSTONES_KEPT", 1)
    hub = ChildEventHub()
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: "/nowhere"), channel=_Channel(), events=hub)
    for n in range(3):
        child = _child(f"sub-{n}", state="stopped")
        runtime._publish(child, child.stream, None, final=True)
    assert [row["id"] for row in hub.rows()] == ["sub-2"]
    assert hub.state_of("sub-1") == "stopped"
    with pytest.raises(ValueError, match="no agent 'sub-0' in this session"):
        hub.state_of("sub-0")


def test_an_approval_step_is_a_frame_and_an_open_request_is_on_the_row() -> None:
    runtime, hub, frames = _runtime()
    child = _child(state="running")
    runtime._publish(child, child.stream, None)
    hub.approval(child.id, "call_9", "bash", "asked")
    assert hub.rows()[0]["approval"] == {"callId": "call_9", "toolName": "bash"}
    hub.approval(child.id, "call_9", "bash", "allowed")
    assert hub.rows()[0]["approval"] is None
    assert [(d["callId"], d["toolName"], d["step"]) for e, d in frames if e == "approval"] == [
        ("call_9", "bash", "asked"),
        ("call_9", "bash", "allowed"),
    ]
    hub.approval("sub-unknown", "c", "bash", "asked")
    assert len(frames) == 3


async def test_a_cancel_of_a_child_with_a_process_carries_the_cancels_envelope() -> None:
    """``_leave_cancelled_envelope`` builds one for a row with a process (ADR-0276 D14)."""

    channel = _EndChannel("park")
    runtime, _hub, frames = _runtime(channel=channel)
    from aelix_agents.runtime import CallEnvelopes

    task = asyncio.ensure_future(
        runtime.spawn_granted(
            _grant(), _resolved(), "go", record=SpawnRecordMeta(tool_call_id="tc-9", mode="single", index=0, envelopes=CallEnvelopes())
        )
    )
    await asyncio.wait_for(channel.arrived.wait(), 10)
    (row,) = runtime._children.values()
    row.proc = object()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (terminal,) = [d for e, d in frames if e == "state" and "end" in d]
    assert (terminal["end"], terminal["outcome"], terminal["reason"]) == ("cancelled", "aborted", "the turn was cancelled")


class _ParkedRuntime:
    """A row's runtime whose stop never returns: the answer comes at the bound."""

    def __init__(self) -> None:
        self.stopped: list[str] = []
        self.release = asyncio.Event()

    async def stop(self, child_id: str) -> None:
        self.stopped.append(child_id)
        await self.release.wait()


async def test_stop_answers_at_its_bound_while_the_door_goes_on_and_a_failed_job_stop_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_agents import host_controls

    monkeypatch.setattr(host_controls, "CHILD_STOP_ANSWER_BOUND", 0.05)
    hub = ChildEventHub()
    parked = _ParkedRuntime()
    child = _child(state="running")
    child.adopted = AdoptedLink(job_id="j-1")
    hub.publish(parked, child, None)
    asked: list[str] = []

    async def failing_job_stop(job_id: str) -> None:
        asked.append(job_id)
        raise RuntimeError("the job is gone")

    controls = HostControls(hub, stop_job=failing_job_stop)
    answer = await asyncio.wait_for(controls.stop(child.id), 5)
    assert answer == {"state": "running"}
    assert (asked, parked.stopped) == (["j-1"], ["sub-1"]), "the job's stop first, then the row's"
    assert len(controls._doors) == 1, "the door goes on past the answer"
    parked.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert controls._doors == set()


def test_a_terminal_state_before_the_final_publish_is_held_back() -> None:
    """A stop marks the row while its run unwinds; the host's one terminal frame carries the end."""

    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    runtime._publish(child, child.stream, None)
    child.state = "stopped"
    child.stopped = True
    _feed(runtime, child, _say("unwinding"))
    assert [(e, d.get("state")) for e, d in frames] == [("state", "running"), ("text", None)]
    runtime._publish(child, child.stream, None, final=True)
    assert [(e, d.get("state"), d.get("end")) for e, d in frames[2:]] == [("state", "stopped", "stopped")]


def test_transitions_drained_in_one_batch_keep_each_turns_closes_before_the_next_turns_starts() -> None:
    """A producer that publishes once for several lines (contract r5 §4.2: "before any frame of the next turn")."""

    runtime, _hub, frames = _runtime()
    child = _child(state="running")
    for event in (_AGENT_START, _TURN_START, _start("a"), _TURN_START, _start("b"), _end("b")):
        reduce_event(child.stream, event)
    runtime._publish(child, child.stream, None)
    assert _tools(frames) == [
        ("tool_start", "a", "read"),
        ("tool_end", "a", "no_result"),
        ("tool_start", "b", "read"),
        ("tool_end", "b", "ok"),
    ]


async def test_a_job_rows_stop_is_the_jobs_and_not_also_the_rows() -> None:
    """The service's stop records who asked and runs the job's legs; a second, direct kill would race them."""

    hub = ChildEventHub()
    parked = _ParkedRuntime()
    child = _child(state="running")
    child.adopted = AdoptedLink(job_id="j-2")
    hub.publish(parked, child, None)
    asked: list[str] = []

    async def job_stop(job_id: str) -> None:
        asked.append(job_id)

    assert await HostControls(hub, stop_job=job_stop).stop(child.id) == {"state": "running"}
    assert (asked, parked.stopped) == (["j-2"], [])
    foreground = _child("sub-fg", state="running")
    hub.publish(parked, foreground, None)
    parked.release.set()
    assert await HostControls(hub, stop_job=job_stop).stop("sub-fg") == {"state": "running"}
    assert (asked, parked.stopped) == (["j-2"], ["sub-fg"]), "a row with no job is stopped on its runtime"


def test_cap_marks_a_text_it_sliced_even_when_what_is_left_fits() -> None:
    """Fix round 1, verify V-F5 mutant E14: ``cap`` bounds its work by slicing first.

    A text longer than four times the limit is sliced before the strip; when what
    is left fits (blanks the strip removed), the result still ends in ``…``, so a
    host never reads a cut text as whole.
    """

    assert cap("a" + " " * 600 + "the tail it never saw", 120) == "a…"
    assert cap("a" + " " * 100, 120) == "a", "nothing sliced: nothing marked"


def test_a_live_rows_elapsed_ms_counts_from_its_start_between_snapshots() -> None:
    """Fix round 1, verify V-F5 mutant E23: ``children/list`` reads the clock for a live row.

    A row whose child printed nothing yet has ``elapsed_ms`` 0 from its snapshots;
    its ``elapsedMs`` is the time since it started, and an ended row's is its own.
    """

    import time

    hub = ChildEventHub()
    child = RunningChild(id="sub-1", profile="scout", state="running", started_at=time.monotonic() - 5)
    hub.publish(None, child, None)
    (row,) = hub.rows()
    assert row["elapsedMs"] >= 5000
    child.state = "done"
    hub.publish(None, child, None, terminal={"end": "completed", "outcome": "ok", "reason": None, "note": None})
    (ended,) = hub.rows()
    assert ended["elapsedMs"] == 0, "an ended row keeps its own figure"


async def test_a_hosts_stop_of_a_job_row_asks_the_job_service_as_the_user() -> None:
    """Fix round 1, verify V-F5 mutant X5: the job's stop is recorded as the user's, not waited for."""

    from aelix_agents.extension import _job_stopper

    asked: list[tuple[str, str, float]] = []

    class _Facade:
        async def stop(self, job_id: str, *, reason: str, wait: float) -> None:
            asked.append((job_id, reason, wait))

    stop = _job_stopper(_Facade())
    assert stop is not None and _job_stopper(None) is None
    await stop("j-1")
    assert asked == [("j-1", "user", 0.0)]
