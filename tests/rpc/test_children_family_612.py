"""#612 (ADR-0307, contract r5 §3, §4.5): the RPC ``children/`` family, its fences and the wake.

``children/list`` and ``children/events`` are lock-free reads of the build's
hub; ``children/steer``, ``children/wrap_up`` and ``children/stop`` are admitted
before the mutation lock (``CONCURRENT_METHODS``), refused while a session change
runs, bound to the generation that admitted them, settled — answered
``unknown`` — when a change begins, and admitted again on the same generation
after a change that never replaced the harness; the old generation's controls
are retired at the rebind. ``commands/cancel {}`` does not reach them, shutdown
and a cancelled ``run_rpc_mode`` do. ``CommandWire.request`` drops its
correlation entry when cancelled. A notice raises ``aelix_notice`` (primed at
subscription), and ``children/notices`` runs one notice turn and says what is
left.

Fails on ``567753c0``: every ``children/*`` method answers "Unsupported runtime
control method", ``RpcControlPlane`` takes no ``jobs``, no ``aelix_notice`` is
emitted, and a cancelled ``CommandWire.request`` leaves its entry behind.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import SessionBeforeSwitchResult
from aelix_agent_core.harness.inbox import Notice
from aelix_agents.child_events import ChildEventHub
from aelix_agents.command_wire import CommandWire
from aelix_agents.host_controls import HostControls
from aelix_agents.print_channel import RunningChild
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.extensions.api import EventBus, Extension
from aelix_coding_agent.rpc.control import (
    CANCELLABLE_METHODS,
    CONCURRENT_METHODS,
    MUTATING_METHODS,
    READ_ONLY_METHODS,
    TURN_SAFE_METHODS,
    RpcControlPlane,
)
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

from tests.jobs.conftest import fast_service, make_harness, make_session
from tests.rpc.test_runtime_control_lifecycle_594 import Rig, Wire, _control

# === fakes ===========================================================================


class FakeControls:
    """A ``SubagentControls`` whose doors can park on a gate and record what they were asked."""

    def __init__(self) -> None:
        self.epoch = uuid.uuid4().hex
        self.closed = False
        self.calls: list[tuple[str, ...]] = []
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()
        self.subscribers: list[Any] = []
        self.rows = [{"id": "sub-1", "state": "running"}]

    def list(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    def events(self, id: str, after_seq: int | None) -> dict[str, Any]:  # noqa: A002
        if id != "sub-1":
            raise ValueError(f"no agent {id!r} in this session")
        return {"epoch": self.epoch, "events": [], "nextSeq": 1, "lowSeq": 1, "gap": False, "after": after_seq}

    async def _door(self, *call: str) -> None:
        if self.closed:
            raise ValueError("the session changed")
        self.calls.append(call)
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()

    async def steer(self, id: str, text: str, mode: str) -> dict[str, Any]:  # noqa: A002
        await self._door("steer", id, text, mode)
        if id == "sub-ended":
            return {"outcome": "refused", "phase": None, "verb": mode, "ended": False, "reason": "use resume"}
        return {"outcome": "queued", "phase": "streaming", "verb": mode, "ended": False, "reason": None}

    async def wrap_up(self, id: str) -> dict[str, Any]:  # noqa: A002
        await self._door("wrap_up", id)
        return {"outcome": "queued", "phase": "idle", "verb": "wrap_up", "ended": False, "reason": None}

    async def stop(self, id: str) -> dict[str, Any]:  # noqa: A002
        await self._door("stop", id)
        return {"state": "stopped"}

    def subscribe(self, callback: Any) -> Any:
        self.subscribers.append(callback)
        return lambda: self.subscribers.remove(callback)

    def close(self) -> None:
        self.closed = True

    def publish(self, event: str, data: dict[str, Any]) -> None:
        for callback in list(self.subscribers):
            callback(event, data)


class FakeSubagents:
    """The least a runtime needs to bind the seam, carrying ``controls``."""

    contract_version = 1

    def __init__(self, controls: Any) -> None:
        self.controls = controls

    def resolve_profile(self, name_or_path: str, *, allow_project: bool = False) -> Any:
        raise ValueError(name_or_path)

    async def spawn(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("not spawned here")

    def list(self) -> list[Any]:
        return []

    def status(self, id: str) -> Any:  # noqa: A002
        raise KeyError(id)

    async def stop(self, id: str) -> None:  # noqa: A002
        return None

    async def stop_all(self) -> None:
        return None


class ChildrenRig(Rig):
    """``Rig`` whose every build binds a fresh :class:`FakeControls` (one generation each)."""

    def __init__(self, tmp: Path, extensions: Any) -> None:
        super().__init__(tmp, extensions)
        self.built: list[FakeControls] = []

    def harness(self, session: Any) -> Any:
        harness = super().harness(session)
        controls = FakeControls()
        self.built.append(controls)
        harness.runtime.bind_subagents(FakeSubagents(controls), replace=True)
        return harness


async def _plane(tmp_path: Path, extensions: Any = list) -> tuple[ChildrenRig, RpcControlPlane, list[dict[str, Any]]]:
    rig = await ChildrenRig.create(tmp_path, extensions)
    frames: list[dict[str, Any]] = []
    plane = RpcControlPlane(rig.runtime, emit=frames.append)

    async def rebind(harness: Any, reason: str = "resume") -> None:
        plane.bind_progress(harness)

    rig.runtime.set_rebind_session(rebind)
    return rig, plane, frames


# === admission and routing ============================================================


def test_the_children_methods_sit_in_their_admission_sets() -> None:
    assert {"children/list", "children/events"} <= READ_ONLY_METHODS
    assert {"children/steer", "children/wrap_up", "children/stop"} == CONCURRENT_METHODS
    assert "children/notices" in MUTATING_METHODS and "children/notices" in CANCELLABLE_METHODS
    assert not CONCURRENT_METHODS & (READ_ONLY_METHODS | MUTATING_METHODS | TURN_SAFE_METHODS)


async def test_list_events_and_the_doors_reach_the_controls(tmp_path: Path) -> None:
    rig, plane, _frames = await _plane(tmp_path)
    controls = rig.built[-1]
    listed = await plane.handle("children/list", {})
    assert listed == {"epoch": controls.epoch, "children": [{"id": "sub-1", "state": "running"}]}
    assert (await plane.handle("children/events", {"id": "sub-1", "afterSeq": 0}))["after"] == 0
    assert (await plane.handle("children/events", {"id": "sub-1"}))["after"] is None
    with pytest.raises(ValueError, match="no agent"):
        await plane.handle("children/events", {"id": "sub-x"})
    with pytest.raises(ValueError, match="afterSeq must be an integer"):
        await plane.handle("children/events", {"id": "sub-1", "afterSeq": True})
    steered = await plane.handle("children/steer", {"id": "sub-1", "text": "note"})
    assert steered == {"outcome": "queued", "phase": "streaming", "verb": "steer", "ended": False, "reason": None}
    assert (await plane.handle("children/steer", {"id": "sub-1", "text": "n", "mode": "follow_up"}))["verb"] == "follow_up"
    assert (await plane.handle("children/wrap_up", {"id": "sub-1"}))["verb"] == "wrap_up"
    assert await plane.handle("children/stop", {"id": "sub-1"}) == {"state": "stopped"}
    assert controls.calls == [
        ("steer", "sub-1", "note", "steer"),
        ("steer", "sub-1", "n", "follow_up"),
        ("wrap_up", "sub-1"),
        ("stop", "sub-1"),
    ]
    with pytest.raises(ValueError, match="use resume"):
        await plane.handle("children/steer", {"id": "sub-ended", "text": "late"})


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"text": "x"}, "id must be"),
        ({"id": "sub-1"}, "text must be a non-empty string"),
        ({"id": "sub-1", "text": "   "}, "text must be a non-empty string"),
        ({"id": "sub-1", "text": "x" * 100_001}, "100000"),
        ({"id": "sub-1", "text": "a\x00b"}, "NUL"),
        ({"id": "sub-1", "text": "x", "mode": "prompt"}, "mode must be steer or follow_up"),
    ],
)
async def test_steer_refuses_what_its_inbox_cannot_take(tmp_path: Path, params: dict[str, Any], message: str) -> None:
    rig, plane, _frames = await _plane(tmp_path)
    with pytest.raises(ValueError, match=message):
        await plane.handle("children/steer", params)
    assert rig.built[-1].calls == []
    assert (await plane.handle("children/steer", {"id": "sub-1", "text": "x" * 100_000}))["outcome"] == "queued"


async def test_a_host_with_no_delegated_agents_refuses_and_lists_none_of_them(tmp_path: Path) -> None:
    rig = await Rig.create(tmp_path)
    plane = RpcControlPlane(rig.runtime)
    for method in ("children/list", "children/events", "children/steer", "children/wrap_up", "children/stop"):
        with pytest.raises(ValueError, match="no delegated agents"):
            await plane.handle(method, {"id": "sub-1", "text": "x"})
    with pytest.raises(ValueError, match="no job service"):
        await plane.handle("children/notices", {})
    assert not {m for m in (await plane.handle("capabilities", {}))["methods"] if m.startswith("children/")}


async def test_capabilities_list_each_half_on_its_own_service(tmp_path: Path) -> None:
    _rig, plane, _frames = await _plane(tmp_path)
    five = ["children/list", "children/events", "children/steer", "children/wrap_up", "children/stop"]
    methods = (await plane.handle("capabilities", {}))["methods"]
    assert all(m in methods for m in five) and "children/notices" not in methods
    service = fast_service()
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    jobs_only = RpcControlPlane(SimpleNamespace(harness=harness), jobs=service)
    listed = (await jobs_only.handle("capabilities", {}))["methods"]
    assert [m for m in listed if m.startswith("children/")] == ["children/notices"]


async def test_the_aelix_subagent_channels_stay_three_with_controls_and_jobs(tmp_path: Path) -> None:
    bus = EventBus()
    runtime = SimpleNamespace(event_bus=bus, subagents=FakeSubagents(FakeControls()))
    harness = SimpleNamespace(runtime=runtime, notices_enabled=True, pending_notices=0, add_notice_listener=lambda cb: lambda: None)
    RpcControlPlane(SimpleNamespace(harness=harness), jobs=object())
    assert sorted(bus._handlers) == ["subagent_end", "subagent_start", "subagent_tool"]


# === the fences =======================================================================


async def test_an_intervention_is_answered_while_a_mutation_holds_the_lock(tmp_path: Path) -> None:
    """As ``agents/run`` holds the lock for its child (contract r5 §3.4)."""

    _rig, plane, _frames = await _plane(tmp_path)
    async with plane.lock:
        assert (await plane.handle("children/steer", {"id": "sub-1", "text": "x"}))["outcome"] == "queued"
        assert await plane.handle("children/stop", {"id": "sub-1"}) == {"state": "stopped"}
        assert (await plane.handle("children/list", {}))["children"]


async def test_interventions_are_refused_while_a_reload_runs_and_settled_when_it_begins(tmp_path: Path) -> None:
    rig, plane, _frames = await _plane(tmp_path)
    first = rig.built[-1]
    first.gate = asyncio.Event()
    steering = asyncio.create_task(plane.handle("children/steer", {"id": "sub-1", "text": "x"}))
    await first.entered.wait()
    hold = rig.hold = asyncio.Event()
    reload = asyncio.create_task(plane.handle("resources/reload", {}))
    answer = await asyncio.wait_for(steering, 5)
    assert answer == {
        "outcome": "unknown",
        "phase": None,
        "verb": "steer",
        "ended": False,
        "reason": "a session change began before the agent answered",
    }
    await rig.build_entered.wait()
    with pytest.raises(ValueError, match="session change"):
        await asyncio.wait_for(plane.handle("children/wrap_up", {"id": "sub-1"}), 5)
    assert not first.closed, "fencing is not retirement"
    generation = plane.generation
    hold.set()
    await asyncio.wait_for(reload, 5)
    assert first.closed and plane.generation == generation + 1
    assert plane.controls is rig.built[-1] and plane.controls is not first
    assert (await plane.handle("children/steer", {"id": "sub-1", "text": "y"}))["outcome"] == "queued"
    assert rig.built[-1].calls == [("steer", "sub-1", "y", "steer")]


async def test_a_stop_a_session_change_interrupts_answers_the_state_the_hub_holds(tmp_path: Path) -> None:
    rig, plane, _frames = await _plane(tmp_path)
    controls = rig.built[-1]
    controls.gate = asyncio.Event()

    async def stop() -> tuple[dict[str, Any], int]:
        answer = await plane.handle("children/stop", {"id": "sub-1"})
        task = asyncio.current_task()
        return answer, task.cancelling() if task is not None else -1

    stopping = asyncio.create_task(stop())
    await controls.entered.wait()
    await plane.settle_interventions()
    assert await asyncio.wait_for(stopping, 5) == ({"state": "running"}, 0)
    controls.entered.clear()
    controls.rows = []
    gone = asyncio.create_task(plane.handle("children/stop", {"id": "sub-1"}))
    await controls.entered.wait()
    await plane.settle_interventions()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(gone, 5)


async def test_the_old_generations_frames_reach_the_host_until_the_rebind_and_none_after(tmp_path: Path) -> None:
    rig, plane, frames = await _plane(tmp_path)
    first = rig.built[-1]
    first.publish("state", {"id": "sub-1", "seq": 1})
    hold = rig.hold = asyncio.Event()
    reload = asyncio.create_task(plane.handle("resources/reload", {}))
    await rig.build_entered.wait()
    first.publish("state", {"id": "sub-1", "seq": 2, "note": "a teardown frame"})
    hold.set()
    await asyncio.wait_for(reload, 5)
    first.publish("state", {"id": "sub-1", "seq": 3})
    rig.built[-1].publish("text", {"id": "sub-2", "seq": 1})
    children = [(f["event"], f["data"]["seq"]) for f in frames if f["type"] == "aelix_child"]
    assert children == [("state", 1), ("state", 2), ("text", 1)]


def _veto(extension: Extension) -> Extension:
    extension.handlers["session_before_switch"] = [lambda event, ctx: SessionBeforeSwitchResult(cancel=True)]
    return extension


def _held_switch(gate: asyncio.Event, entered: asyncio.Event) -> Extension:
    async def hold(event: Any, ctx: Any) -> None:
        entered.set()
        await gate.wait()

    extension = Extension(name="held")
    extension.handlers["session_before_switch"] = [hold]
    return extension


async def test_a_held_classic_swap_refuses_interventions_and_a_vetoed_one_admits_again_on_the_same_generation(
    tmp_path: Path,
) -> None:
    gate, entered = asyncio.Event(), asyncio.Event()
    veto = {"on": False}

    def extensions() -> list[Extension]:
        if veto["on"]:
            return [_veto(Extension(name="veto"))]
        return [_held_switch(gate, entered)]

    rig = await ChildrenRig.create(tmp_path, extensions)
    controls = rig.built[-1]
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(rig.runtime.harness, runtime_host=rig.runtime, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False)
    )
    controls.gate = asyncio.Event()
    wire.send(_control("s1", "children/steer", id="sub-1", text="before"))
    await controls.entered.wait()
    wire.send({"type": "new_session", "id": "n1"})
    settled = await asyncio.wait_for(wire.response("s1"), 5)
    assert settled["data"]["outcome"] == "unknown", settled
    await entered.wait()
    wire.send(_control("s2", "children/steer", id="sub-1", text="during"))
    refused = await asyncio.wait_for(wire.response("s2"), 5)
    assert refused["success"] is False and "session change" in refused["error"]
    gate.set()
    await asyncio.wait_for(wire.response("n1"), 5)
    wire.send(eof=True)
    await asyncio.wait_for(run, 10)

    veto["on"] = True
    rig2 = await ChildrenRig.create(tmp_path / "v", extensions)
    controls2 = rig2.built[-1]
    wire2 = Wire()
    run2 = asyncio.create_task(
        run_rpc_mode(rig2.runtime.harness, runtime_host=rig2.runtime, stdin=wire2.reader, stdout_write=wire2.write, install_signal_handlers=False)
    )
    wire2.send({"type": "new_session", "id": "n2"})
    vetoed = await asyncio.wait_for(wire2.response("n2"), 5)
    assert vetoed["success"] is True and vetoed["data"]["cancelled"] is True
    wire2.send(_control("s3", "children/steer", id="sub-1", text="after the veto"))
    again = await asyncio.wait_for(wire2.response("s3"), 5)
    assert again["data"]["outcome"] == "queued" and not controls2.closed
    assert controls2.calls == [("steer", "sub-1", "after the veto", "steer")]
    wire2.send(eof=True)
    await asyncio.wait_for(run2, 10)


async def test_a_deferred_cancel_landing_in_the_settle_still_lets_the_guarded_reload_finish(tmp_path: Path) -> None:
    """#612 fix round 1, Codex C-M2 (Codex r1a's ``deferred`` probe).

    A steer is in flight; ``commands/run`` runs ``/replace``, whose ``ctx.reload()``
    is a guarded transition that first settles the steer; ``commands/cancel {}``
    lands while that settle waits and is answered ``deferred``. The reload goes
    on, and the cancel lands as it returns. On ``111e02d7`` the cancel landed in
    the settle, outside the shielded step: the factory was never entered and the
    harness was not replaced.
    """

    from tests.rpc.test_runtime_control_lifecycle_594 import _commands

    async def replace(_args: str, ctx: Any) -> None:
        await ctx.reload()

    rig = await ChildrenRig.create(tmp_path, lambda: [_commands(replace=replace)])
    controls = rig.built[-1]
    controls.gate = asyncio.Event()
    before = rig.runtime.harness
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(rig.runtime.harness, runtime_host=rig.runtime, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False)
    )
    try:
        wire.send(_control("steer", "children/steer", id="sub-1", text="look"))
        await asyncio.wait_for(controls.entered.wait(), 5)
        wire.send(_control("reload", "commands/run", text="/replace"), _control("cancel", "commands/cancel"))
        verdict = await asyncio.wait_for(wire.response("cancel"), 5)
        assert verdict["data"] == {"cancelled": True, "method": "commands/run", "deferred": True}
        command = await asyncio.wait_for(wire.response("reload"), 5)
        steer = await asyncio.wait_for(wire.response("steer"), 5)
        assert steer["data"]["outcome"] == "unknown"
        assert command["success"] is False and "cancelled" in command["error"].lower()
        assert rig.builds == 1, "the deferred cancel dropped the reload"
        assert rig.runtime.harness is not before
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)


async def test_input_eof_still_sends_the_terminal_frames_the_disposal_publishes_then_retires(tmp_path: Path) -> None:
    """#612 fix round 1, Codex C-M4 (Codex r1a's ``eof`` probe).

    The host half-closes its input and keeps reading. The disposal's
    ``session_shutdown`` stops the children, and their terminal ``aelix_child``
    frames reach the wire; then the subscription ends and the controls are
    retired. On ``111e02d7`` the subscription ended before the disposal: the wire
    held only the earlier ``running`` frame and the controls stayed open.
    """

    rig_box: dict[str, ChildrenRig] = {}

    def extensions() -> list[Extension]:
        extension = Extension(name="shutdown")

        async def teardown(_event: Any, _ctx: Any) -> None:
            rig_box["rig"].built[0].publish("state", {"id": "sub-1", "state": "stopped", "end": "stopped"})

        extension.handlers.setdefault("session_shutdown", []).append(teardown)
        return [extension]

    rig = await ChildrenRig.create(tmp_path, extensions)
    rig_box["rig"] = rig
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(rig.runtime.harness, runtime_host=rig.runtime, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False)
    )
    wire.send(_control("ready", "children/list"))
    await asyncio.wait_for(wire.response("ready"), 5)
    rig.built[0].publish("state", {"id": "sub-1", "state": "running"})
    wire.send(eof=True)
    await asyncio.wait_for(run, 5)
    frames = [f["data"] for f in wire.records if f["type"] == "aelix_child"]
    assert frames == [{"id": "sub-1", "state": "running"}, {"id": "sub-1", "state": "stopped", "end": "stopped"}]
    assert rig.built[0].closed and rig.built[0].subscribers == []


@pytest.mark.parametrize("command_type", [[], {}, 7, None], ids=["list", "map", "int", "null"])
async def test_a_classic_command_whose_type_is_not_a_string_is_answered_parse(tmp_path: Path, command_type: Any) -> None:
    """#612 fix round 1, Codex C-M5: the swap settle reads the raw ``type``; an unhashable one raised.

    On ``111e02d7`` ``{"type": [], …}`` and ``{"type": {}, …}`` raised ``TypeError``
    in ``settle_classic`` and got no response (the base, ``567753c0``, answered
    ``parse``); ``7`` and ``null`` are the hashable guards.
    """

    rig = await Rig.create(tmp_path)
    loop = asyncio.get_running_loop()
    errors: list[object] = []
    loop.set_exception_handler(lambda _loop, context: errors.append(context.get("exception")))
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(rig.runtime.harness, runtime_host=rig.runtime, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False)
    )
    try:
        wire.send({"type": command_type, "id": "bad"}, {"type": "get_state", "id": "alive"})
        await asyncio.wait_for(wire.response("alive"), 5)
        reply = await asyncio.wait_for(wire.response("bad"), 5)
        assert reply["command"] == "parse" and reply["success"] is False, reply
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)
        loop.set_exception_handler(None)
    assert errors == []


async def test_commands_cancel_without_an_id_does_not_reach_an_intervention(tmp_path: Path) -> None:
    rig, plane, _frames = await _plane(tmp_path)
    controls = rig.built[-1]
    controls.gate = asyncio.Event()
    steering = asyncio.create_task(plane.handle("children/steer", {"id": "sub-1", "text": "x"}))
    await controls.entered.wait()
    assert plane.cancel({}) == {"cancelled": False}
    await asyncio.sleep(0)
    assert not steering.done()
    controls.gate.set()
    assert (await asyncio.wait_for(steering, 5))["outcome"] == "queued"


async def test_shutdown_cancels_an_intervention_which_answers_unknown(tmp_path: Path) -> None:
    rig, plane, _frames = await _plane(tmp_path)
    controls = rig.built[-1]
    controls.gate = asyncio.Event()
    steering = asyncio.create_task(plane.handle("children/wrap_up", {"id": "sub-1"}))
    await controls.entered.wait()
    plane.begin_shutdown()
    answer = await asyncio.wait_for(steering, 5)
    assert (answer["outcome"], answer["reason"]) == ("unknown", "the RPC host began shutting down before the agent answered")
    with pytest.raises(ValueError, match="shutting down"):
        await asyncio.wait_for(plane.handle("children/steer", {"id": "sub-1", "text": "x"}), 5)


async def test_a_cancelled_run_rpc_mode_cancels_the_interventions_in_flight(tmp_path: Path) -> None:
    """Codex r4-2 9: a cancelled ``run_rpc_mode`` skips its main loop's ``begin_shutdown``."""

    rig = await ChildrenRig.create(tmp_path, list)
    controls = rig.built[-1]
    controls.gate = asyncio.Event()
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(rig.runtime.harness, runtime_host=rig.runtime, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False)
    )
    wire.send(_control("s1", "children/steer", id="sub-1", text="x"))
    await controls.entered.wait()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    answer = await asyncio.wait_for(wire.response("s1"), 5)
    assert (answer["data"]["outcome"], answer["data"]["reason"]) == (
        "unknown",
        "the RPC host began shutting down before the agent answered",
    )


# === a real wire behind the real controls =============================================


class _SilentStdin:
    """A child's stdin that takes every line and never answers."""

    transport = None

    def __init__(self) -> None:
        self.lines: list[bytes] = []
        self.drained = 0

    def write(self, data: bytes) -> None:
        self.lines.append(data)

    async def drain(self) -> None:
        self.drained += 1


async def _in_its_ack_wait(stdin: _SilentStdin) -> None:
    """Until the request's write drained and it waits for the answer.

    Not one loop turn earlier: on Python 3.11 ``asyncio.wait_for`` swallows a
    cancel that lands in the turn its inner await completes (CPython gh-86296,
    fixed in 3.12), so a cancel aimed at the drain's last turn is not seen there.
    """

    for _ in range(50):
        if stdin.drained:
            break
        await asyncio.sleep(0)
    for _ in range(3):
        await asyncio.sleep(0)

    def close(self) -> None:
        return None


def _live_row(hub: ChildEventHub) -> tuple[_SubagentRuntimeImpl, RunningChild]:
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: "/nowhere"), channel=object(), events=hub)  # type: ignore[arg-type]
    child = RunningChild(id="sub-live", profile="scout", state="running")
    child.proc = object()
    child.wire = CommandWire("sub-live")
    child.wire.attach(_SilentStdin())
    runtime._children[child.id] = child
    runtime._publish(child, child.stream, None)
    return runtime, child


async def test_a_settled_steer_leaves_no_correlation_entry_on_the_wire(tmp_path: Path) -> None:
    """Codex measured ``pending=1`` left behind at the base (contract r5 §3.4)."""

    hub = ChildEventHub()
    _runtime, child = _live_row(hub)
    rig = await ChildrenRig.create(tmp_path, list)
    plane = RpcControlPlane(rig.runtime)
    plane.controls = HostControls(hub)
    steering = asyncio.create_task(plane.handle("children/steer", {"id": "sub-live", "text": "x"}))
    await _in_its_ack_wait(child.wire._writer)
    assert child.wire.pending == 1 and len(child.wire._writer.lines) == 1
    await plane.settle_interventions()
    assert (await asyncio.wait_for(steering, 5))["outcome"] == "unknown"
    assert child.wire.pending == 0
    late = '{"type": "response", "id": "sub-live-1", "success": true, "data": {}}'
    assert child.wire.take(late) is True, "a late answer is an unknown id, taken and dropped"


async def test_a_cancelled_request_drops_its_entry_on_the_wire_itself() -> None:
    wire = CommandWire("w")
    stdin = _SilentStdin()
    wire.attach(stdin)
    waiting = asyncio.ensure_future(wire.request("steer", {"message": "a"}))
    await _in_its_ack_wait(stdin)
    assert wire.pending == 1
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert wire.pending == 0


class _AnsweringStdin(_SilentStdin):
    """A child's stdin that answers each line ``queued`` on the next loop turn, through the wire."""

    def __init__(self, wire: CommandWire) -> None:
        super().__init__()
        self.wire = wire

    def write(self, data: bytes) -> None:
        import json

        super().write(data)
        request = json.loads(data)
        answer = json.dumps({"type": "response", "id": request["id"], "success": True, "data": {"phase": "streaming"}})
        asyncio.get_running_loop().call_soon(self.wire.take, answer)


async def test_a_model_send_and_a_host_steer_racing_on_one_row_are_both_answered(tmp_path: Path) -> None:
    hub = ChildEventHub()
    runtime, child = _live_row(hub)
    child.wire = CommandWire("sub-live")
    child.wire.attach(_AnsweringStdin(child.wire))
    rig = await ChildrenRig.create(tmp_path, list)
    plane = RpcControlPlane(rig.runtime)
    plane.controls = HostControls(hub)
    model, host = await asyncio.gather(
        runtime.steer(child.id, "from the model", origin="model"),
        plane.handle("children/steer", {"id": "sub-live", "text": "from the host"}),
    )
    assert (model.outcome, host["outcome"]) == ("queued", "queued")
    assert child.wire.pending == 0 and len(child.wire._writer.lines) == 2


class _AnswersSteersOnly(_SilentStdin):
    """A child's stdin that answers each ``steer`` line ``queued`` and leaves every ``approval`` line unanswered."""

    def __init__(self, wire: CommandWire) -> None:
        super().__init__()
        self.wire = wire

    def write(self, data: bytes) -> None:
        import json

        super().write(data)
        request = json.loads(data)
        if request["type"] == "steer":
            answer = json.dumps({"type": "response", "id": request["id"], "success": True, "data": {"phase": "idle"}})
            asyncio.get_running_loop().call_soon(self.wire.take, answer)

    def kinds(self) -> list[str]:
        import json

        return [json.loads(line)["type"] for line in self.lines]


def _asking_row(hub: ChildEventHub) -> tuple[_SubagentRuntimeImpl, RunningChild, _AnswersSteersOnly]:
    """A live row with one approval request open that nobody answers."""

    import json

    from aelix_agents.child_approvals import ChildApprovals

    runtime, child = _live_row(hub)
    stdin = _AnswersSteersOnly(child.wire)
    child.wire = CommandWire("sub-live")
    child.wire.attach(stdin)
    stdin.wire = child.wire

    async def no_answer(_request: Any) -> tuple[str, str | None]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    child.approvals = ChildApprovals(child, decide=no_answer, hub=hub)
    child.approvals.offer(json.dumps({"type": "approval_request", "id": "ap-1", "toolName": "bash",
                                      "toolCallId": "tool-1", "kind": "bash", "args": {"command": "true"}}))
    return runtime, child, stdin


async def test_a_wrap_up_a_vetoed_swap_cancels_before_its_steer_is_refused_unsent_and_can_be_asked_again(
    tmp_path: Path,
) -> None:
    """#612 fix round 1, C-M3 + V-F1 (Codex r1a's ``wrap`` probe, a vetoed ``new_session``).

    ``children/wrap_up`` on a row with an open approval request writes the
    request's ``cancel`` and waits for its acknowledgement; the swap's settle
    cancels it there, before the final steer. Answered as not sent, with the
    request closed and the wrap-up taken back, so a retry after the veto writes
    the final steer. On ``111e02d7`` the answer is ``unknown``, the request
    stays open and the retry is refused "a wrap-up was already requested".
    """

    hub = ChildEventHub()
    child_runtime, child, stdin = _asking_row(hub)
    rig = await Rig.create(tmp_path, lambda: [_veto(Extension(name="veto"))])
    rig.runtime.harness.runtime.bind_subagents(child_runtime, replace=True)
    child_runtime.controls = HostControls(hub)  # type: ignore[attr-defined]
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(rig.runtime.harness, runtime_host=rig.runtime, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False)
    )
    try:
        wire.send(_control("wrap", "children/wrap_up", id=child.id))
        for _ in range(200):
            if stdin.lines:
                break
            await asyncio.sleep(0)
        assert stdin.kinds() == ["approval"]
        wire.send({"type": "new_session", "id": "veto"})
        reply = await asyncio.wait_for(wire.response("wrap"), 5)
        assert reply["success"] is False, reply
        assert reply["error"] == "a session change began before this request was sent"
        swap = await asyncio.wait_for(wire.response("veto"), 5)
        assert swap["data"]["cancelled"] is True
        assert child.approvals.open_request is None and child.wrap_up is None
        steps = [e["data"].get("step") for e in hub.events(child.id, None)["events"] if e["event"] == "approval"]
        assert steps == ["asked", "denied"]
        wire.send(_control("retry", "children/wrap_up", id=child.id))
        retry = await asyncio.wait_for(wire.response("retry"), 5)
        assert retry["success"] is True and retry["data"]["outcome"] == "queued", retry
        assert stdin.kinds() == ["approval", "steer"]
    finally:
        child.approvals.close()
        child.wire.close()
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)


async def test_shutdown_while_a_wrap_up_waits_on_its_approval_cancel_answers_not_sent(tmp_path: Path) -> None:
    """#612 fix round 1, V-F1: the shutdown cause, in the plane's own words."""

    hub = ChildEventHub()
    _runtime, child, stdin = _asking_row(hub)
    rig = await ChildrenRig.create(tmp_path, list)
    plane = RpcControlPlane(rig.runtime)
    plane.controls = HostControls(hub)
    wrapping = asyncio.create_task(plane.handle("children/wrap_up", {"id": child.id}))
    for _ in range(200):
        if stdin.lines:
            break
        await asyncio.sleep(0)
    assert stdin.kinds() == ["approval"]
    plane.begin_shutdown()
    with pytest.raises(ValueError, match="^the RPC host began shutting down before this request was sent$"):
        await asyncio.wait_for(wrapping, 5)
    assert child.wrap_up is None and child.approvals.open_request is None
    child.approvals.close()
    child.wire.close()


# === aelix_notice and children/notices ================================================


def _notice(n: int, body: str = "a job ended") -> Notice:
    return Notice(notice_id=f"n-{n}", custom_type="aelix.test", body=body)


async def _jobs_plane(tmp_path: Path) -> tuple[Any, Any, RpcControlPlane, list[dict[str, Any]]]:
    service = fast_service()
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    harness.post_notice(_notice(0))  # restored at startup: before the plane subscribes
    frames: list[dict[str, Any]] = []
    plane = RpcControlPlane(SimpleNamespace(harness=harness), jobs=service, emit=frames.append)
    return service, harness, plane, frames


def _notices(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in frames if f["type"] == "aelix_notice"]


async def test_a_pending_notice_primes_one_aelix_notice_and_each_later_one_raises_one(tmp_path: Path) -> None:
    _service, harness, plane, frames = await _jobs_plane(tmp_path)
    assert _notices(frames) == [{"type": "aelix_notice", "event": "queued", "data": {}}]
    harness.post_notice(_notice(1))
    assert len(_notices(frames)) == 2
    plane.unbind_progress()
    harness.post_notice(_notice(2))
    assert len(_notices(frames)) == 2, "unsubscribed with the binding"


async def test_children_notices_runs_one_turn_and_says_what_is_left(tmp_path: Path) -> None:
    _service, harness, plane, _frames = await _jobs_plane(tmp_path)
    answer = await plane.handle("children/notices", {})
    assert answer == {"started": True, "placed": 1, "placedInTurn": 1, "reason": None, "remaining": 0}
    assert await plane.handle("children/notices", {}) == {
        "started": False, "placed": 0, "placedInTurn": 0, "reason": "nothing", "remaining": 0
    }
    budget = 64 * 1024
    harness.post_notice(_notice(1, "x" * (budget - 100)))
    harness.post_notice(_notice(2, "y" * (budget - 100)))
    first = await plane.handle("children/notices", {})
    assert (first["started"], first["placed"]) == (True, 1), "one turn's budget before the run"
    assert first["remaining"] == harness.pending_notices
    # Fix round 1, Codex C-M9 (narrowed, the lead's call): a boundary inside the
    # turn claimed the second budget, so this one call placed both notices while
    # ``placed`` says 1 — what ADR-0307 D4 and the guide now say. #857:
    # ``placedInTurn`` counts both.
    assert first["remaining"] == 0
    assert first["placedInTurn"] == 2
    placed_ids = [
        e.details.get("notice_id") for e in await harness.session.get_branch() if e.type == "custom_message"
    ]
    assert placed_ids[-2:] == ["n-1", "n-2"], placed_ids

    while (await plane.handle("children/notices", {}))["started"]:
        pass

    # What the inbox holds once the turn is over is what ``remaining`` says.
    posted: list[str] = []

    def late(event: Any) -> None:
        if getattr(event, "type", None) == "agent_end" and not posted:
            posted.append("n-4")
            harness.post_notice(_notice(4, "posted as the turn ended"))

    harness.post_notice(_notice(3))
    unsubscribe = harness.subscribe(late)
    left = await plane.handle("children/notices", {})
    unsubscribe()
    assert (left["placed"], left["remaining"]) == (1, 1), left
    assert harness.pending_notices == 1
    again = await plane.handle("children/notices", {})
    assert (again["started"], again["placed"], again["remaining"]) == (True, 1, 0)


class _GatedModel:
    """A stream that waits for ``gate`` before it answers (a notice turn that can be cancelled)."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def __call__(self, _model: Any, _context: Any, _options: Any = None) -> Any:
        from aelix_ai.messages import AssistantMessage, TextContent
        from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent

        self.entered.set()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        await self.gate.wait()
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn"))


async def test_children_notices_is_cancelled_by_commands_cancel_and_the_harness_settles_idle(tmp_path: Path) -> None:
    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions

    model = _GatedModel()
    harness = AgentHarness(AgentHarnessOptions(stream_fn=model, session=await make_session(tmp_path), notice_inbox=True))
    service = fast_service()
    await service.bind(harness)
    harness.post_notice(_notice(1))
    plane = RpcControlPlane(SimpleNamespace(harness=harness), jobs=service)
    turn = asyncio.create_task(plane.handle("children/notices", {}, "w1"))
    await model.entered.wait()
    assert plane.cancel({}) == {"cancelled": True, "method": "children/notices"}
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(turn, 5)
    assert harness.phase == "idle"


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="the stub child's pipes are POSIX here")
async def test_a_spawned_child_emits_aelix_child_frames_and_no_aelix_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Background rows never reach the bus (contract r5 §2): the hub is how a host sees them."""

    from aelix_coding_agent.jobs import activation

    from tests.agents_ext.test_agent_background_611 import _rig, _spawned_id
    from tests.jobs.conftest import wait_for

    monkeypatch.setattr(activation, "ACTIVE", True)
    try:
        rig = await _rig(tmp_path)
        frames: list[dict[str, Any]] = []
        plane = RpcControlPlane(SimpleNamespace(harness=SimpleNamespace(runtime=rig.runtime)), emit=frames.append)
        assert isinstance(plane.controls, HostControls)
        _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "count"}))
        await wait_for(lambda: any(f["type"] == "aelix_child" and "end" in f["data"] for f in frames))
        kinds = {f["type"] for f in frames}
        assert "aelix_subagent" not in kinds
        states = [f["data"] for f in frames if f["type"] == "aelix_child" and f["event"] == "state"]
        assert states[0]["background"] is True and states[-1]["end"] == "completed"
        events = [f["event"] for f in frames if f["type"] == "aelix_child"]
        assert "tool_start" not in events, "the stub's one call carries no id: a start the hub cannot track"
        assert events.count("text") == 1
        await rig.service.shutdown()
    finally:
        activation.install_service(None)


# === the edges a mutant found ==========================================================


async def test_a_settle_returns_once_the_interventions_it_cancelled_have_answered(tmp_path: Path) -> None:
    rig, plane, _frames = await _plane(tmp_path)
    controls = rig.built[-1]
    controls.gate = asyncio.Event()

    async def steer() -> tuple[dict[str, Any], int]:
        answer = await plane.handle("children/steer", {"id": "sub-1", "text": "x"})
        task = asyncio.current_task()
        return answer, task.cancelling() if task is not None else -1

    steering = asyncio.create_task(steer())
    await controls.entered.wait()
    await plane.settle_interventions()
    assert steering.done(), "the change waits for the answers (at most 1 s)"
    answer, cancelling = steering.result()
    assert answer["outcome"] == "unknown"
    assert cancelling == 0, "the plane's own cancel is taken back off the task it answered"
    assert plane.interventions == []


async def test_a_cancel_that_is_not_the_planes_propagates(tmp_path: Path) -> None:
    rig, plane, _frames = await _plane(tmp_path)
    controls = rig.built[-1]
    controls.gate = asyncio.Event()
    steering = asyncio.create_task(plane.handle("children/steer", {"id": "sub-1", "text": "x"}))
    await controls.entered.wait()
    steering.cancel()
    with pytest.raises(asyncio.CancelledError):
        await steering
    assert plane.interventions == []


async def test_a_rebind_to_the_same_harness_keeps_its_controls(tmp_path: Path) -> None:
    rig, plane, frames = await _plane(tmp_path)
    controls = rig.built[-1]
    generation = plane.generation
    plane.bind_progress(plane.harness)
    assert not controls.closed and plane.controls is controls and plane.generation == generation + 1
    controls.publish("text", {"id": "sub-1", "seq": 1})
    assert len([f for f in frames if f["type"] == "aelix_child"]) == 1, "subscribed once, not twice"


async def test_a_job_service_beside_a_harness_without_a_notice_inbox_subscribes_nothing(tmp_path: Path) -> None:
    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions

    harness = AgentHarness(AgentHarnessOptions(session=await make_session(tmp_path)))
    assert harness.notices_enabled is False
    frames: list[dict[str, Any]] = []
    RpcControlPlane(SimpleNamespace(harness=harness), jobs=fast_service(), emit=frames.append)
    assert frames == []


async def test_only_a_queued_notice_raises_aelix_notice(tmp_path: Path) -> None:
    _service, _harness, plane, frames = await _jobs_plane(tmp_path)
    assert len(_notices(frames)) == 1
    assert (await plane.handle("children/notices", {}))["placed"] == 1
    assert len(_notices(frames)) == 1, "placed, released and idle changes raise nothing"


@pytest.mark.parametrize("turns", [1, 2, 3])
async def test_a_cancel_in_any_turn_of_the_write_raises_and_leaves_no_entry(turns: int) -> None:
    """Python 3.11's ``wait_for`` kept a cancel landing in the drain's last turn (gh-86296).

    Fails on ``567753c0``: on every interpreter the entry stays (``pending`` 1); on
    3.11 the first turn's cancel is swallowed and the request waits for its answer.
    """

    from aelix_agents import command_wire as cw

    wire = CommandWire("w")
    wire.attach(_SilentStdin())
    original, cw.ACK_SECONDS = cw.ACK_SECONDS, 2.0
    try:
        waiting = asyncio.ensure_future(wire.request("steer", {"message": "a"}))
        for _ in range(turns):
            await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiting, 5)
    finally:
        cw.ACK_SECONDS = original
    assert wire.pending == 0


async def test_a_cancel_while_the_write_drains_cancels_the_drain_too() -> None:
    """Fix round 1, verify V-F5 mutant W2: ``_drained`` cancels the drain with its caller.

    A pipe that never drains (a child that stopped reading) parks ``drain()``.
    Cancelled there, the request leaves no drain behind: with the
    ``drain.cancel()`` removed, the drain stayed pending after the caller's
    cancel, holding the writer until its own end.
    """

    class _StuckStdin(_SilentStdin):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def drain(self) -> None:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    wire = CommandWire("w")
    stdin = _StuckStdin()
    wire.attach(stdin)
    waiting = asyncio.ensure_future(wire.request("steer", {"message": "a"}))
    await asyncio.wait_for(stdin.entered.wait(), 5)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiting, 5)
    await asyncio.wait_for(stdin.cancelled.wait(), 1)
    assert wire.pending == 0
