"""#594 — runtime-control lifecycle: cancellation, EOF, classic/control exclusion, branching.

Every case drives real :class:`AgentSessionRuntime` / :class:`AgentHarness` objects,
either through :class:`RpcControlPlane` or through ``run_rpc_mode``'s JSONL dispatch.
Ordering comes from explicit events and from asyncio's FIFO task scheduling, never
from sleeps.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import SessionBeforeSwitchResult
from aelix_agent_core.runtime import create_agent_session_runtime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
)
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.extensions.api import Extension, RegisteredCommand
from aelix_coding_agent.rpc.control import RpcControlPlane
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

MODEL = Model(id="fixture", provider="fixture")


async def _stream(
    model: Model, context: Context, options: SimpleStreamOptions
) -> AsyncIterator[AssistantMessageEvent]:
    yield AssistantStartEvent(partial=AssistantMessage(content=[]))
    yield AssistantEndEvent(
        message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
    )


def _tool(name: str) -> AgentTool:
    return AgentTool(name=name, description=name, parameters={"type": "object", "properties": {}})


class Rig:
    """A persisted session behind a real runtime whose factory can be held mid-build."""

    def __init__(self, tmp: Path, extensions: Callable[[], list[Extension]]) -> None:
        self.tmp = tmp
        self.extensions = extensions
        self.hold: asyncio.Event | None = None
        self.build_entered = asyncio.Event()
        self.builds = 0
        self.runtime: Any = None
        self.other_file: str | None = None
        self.fork_entry: str | None = None

    async def factory(self, session: Any, *, reload_seed: object = None) -> AgentHarness:
        self.builds += 1
        if self.hold is not None:
            hold, self.hold = self.hold, None
            self.build_entered.set()
            await hold.wait()
        return self.harness(session)

    def harness(self, session: Any) -> AgentHarness:
        return AgentHarness(
            AgentHarnessOptions(
                model=MODEL,
                stream_fn=_stream,
                session=session,
                cwd=str(self.tmp),
                tools=[_tool("alpha"), _tool("beta")],
                extensions=self.extensions(),
            )
        )

    @classmethod
    async def create(cls, tmp: Path, extensions: Callable[[], list[Extension]] = list) -> Rig:
        rig = cls(tmp, extensions)
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp / "sessions"))
        # A second persisted session, so ``switch_session`` has somewhere to go.
        other = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        await other.append_message(UserMessage(content=[TextContent(text="elsewhere")]))
        rig.other_file = other.session_file
        session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        await session.append_message(UserMessage(content=[TextContent(text="hello")]))
        rig.runtime = await create_agent_session_runtime(
            rig.harness(session), rig.factory, repo=repo, fs=fs
        )
        return rig


class Wire:
    """A JSONL client for ``run_rpc_mode``: feed lines, await correlated responses."""

    def __init__(self) -> None:
        self.reader = asyncio.StreamReader()
        self.records: list[dict[str, Any]] = []
        self._changed = asyncio.Event()

    def write(self, data: bytes) -> None:
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                self.records.append(json.loads(line))
        self._changed.set()

    def send(self, *payloads: dict[str, Any], eof: bool = False) -> None:
        self.reader.feed_data(b"".join((json.dumps(p) + "\n").encode() for p in payloads))
        if eof:
            self.reader.feed_eof()

    async def response(self, request_id: str) -> dict[str, Any]:
        while True:
            self._changed.clear()
            for record in self.records:
                if record.get("type") == "response" and record.get("id") == request_id:
                    return record
            await self._changed.wait()

    async def record(self, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        while True:
            self._changed.clear()
            for record in self.records:
                if predicate(record):
                    return record
            await self._changed.wait()


def _commands(**handlers: Any) -> Extension:
    extension = Extension(name="fixture")
    for name, handler in handlers.items():
        extension.commands[name] = RegisteredCommand(
            name=name, handler=handler, description=name, source="fixture"
        )
    return extension


def _control(request_id: str, method: str, **params: Any) -> dict[str, Any]:
    return {"type": "aelix_control", "id": request_id, "method": method, "params": params}


# === F1: a lifecycle transition is never cancelled half-way =====================


async def test_cancel_does_not_interrupt_a_reload(tmp_path: Path) -> None:
    rig = await Rig.create(tmp_path)
    before = rig.runtime.harness
    plane = RpcControlPlane(rig.runtime)
    hold = rig.hold = asyncio.Event()
    reload = asyncio.create_task(plane.handle("resources/reload", {}))
    await rig.build_entered.wait()  # the old harness is disposed; the new one is not built yet

    verdict = await plane.handle("commands/cancel", {})
    hold.set()
    outcome = await asyncio.gather(reload, return_exceptions=True)

    assert verdict["cancelled"] is False
    assert verdict.get("reason") == "not_cancellable"
    assert outcome == [{"cancelled": False}]
    assert rig.runtime.harness is not before
    assert not rig.runtime.harness.runtime.is_stale
    await rig.runtime.harness.dispose()


async def test_cancel_by_request_id_targets_a_queued_mutation(tmp_path: Path) -> None:
    entered, release, late = asyncio.Event(), asyncio.Event(), []

    async def hold(args: str, ctx: Any) -> str:
        entered.set()
        await release.wait()
        return "held"

    harness = AgentHarness(
        AgentHarnessOptions(
            model=MODEL, extensions=[_commands(hold=hold, late=lambda a, c: late.append(a))]
        )
    )
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            harness, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False
        )
    )
    try:
        wire.send(_control("h", "commands/run", text="/hold"))
        await entered.wait()
        # Same chunk: "l" queues behind the lock before the cancel is dispatched.
        wire.send(
            _control("l", "commands/run", text="/late"),
            _control("c", "commands/cancel", requestId="l"),
        )
        cancel = await wire.response("c")
        assert cancel["success"] is True
        assert cancel["data"] == {"cancelled": True, "method": "commands/run"}
        queued = await wire.response("l")
        assert queued["success"] is False
        release.set()
        held = await wire.response("h")
        assert held["success"] is True, held
        assert late == []
    finally:
        release.set()
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)
        await harness.dispose()


# === F1 (review r1): an extension command's own session replacement ============
#
# ``ctx.new_session`` / ``fork`` / ``switch_session`` / ``reload`` dispose the old
# harness before the factory builds the new one, exactly like a direct reload. The
# enclosing ``commands/run`` is cancellable, but that window must not be.


async def _leaf(rig: Rig) -> str:
    return await rig.runtime.session.get_leaf_id()


_REPLACEMENTS: dict[str, Callable[[Any, Rig], Any]] = {
    "new_session": lambda ctx, rig: ctx.new_session(),
    "fork": lambda ctx, rig: ctx.fork(rig.fork_entry),
    "switch_session": lambda ctx, rig: ctx.switch_session(rig.other_file),
    "reload": lambda ctx, rig: ctx.reload(),
}


@pytest.mark.parametrize("operation", sorted(_REPLACEMENTS))
async def test_cancel_waits_out_a_commands_session_replacement(
    tmp_path: Path, operation: str
) -> None:
    after: list[str] = []
    rig_box: dict[str, Rig] = {}

    async def replace(args: str, ctx: Any) -> None:
        await _REPLACEMENTS[operation](ctx, rig_box["rig"])
        after.append("handler continued")

    rig = await Rig.create(tmp_path, lambda: [_commands(replace=replace)])
    rig_box["rig"] = rig
    rig.fork_entry = await _leaf(rig)  # the user message, as fork's "before" requires
    before = rig.runtime.harness
    plane = RpcControlPlane(rig.runtime)
    hold = rig.hold = asyncio.Event()
    command = asyncio.create_task(plane.handle("commands/run", {"text": "/replace"}))
    await rig.build_entered.wait()  # old harness disposed, new one not built yet

    verdict = await plane.handle("commands/cancel", {})
    hold.set()
    outcome = await asyncio.gather(command, return_exceptions=True)

    # The cancel is honoured, but only once the replacement has finished.
    assert verdict == {"cancelled": True, "method": "commands/run", "deferred": True}
    assert [type(value).__name__ for value in outcome] == ["CancelledError"]
    assert after == []
    assert rig.runtime.harness is not before
    assert not rig.runtime.harness.runtime.is_stale
    await rig.runtime.harness.dispose()


async def test_a_command_stays_cancellable_after_its_replacement(tmp_path: Path) -> None:
    replaced = asyncio.Event()

    async def replace_then_wait(args: str, ctx: Any) -> None:
        await ctx.new_session()
        replaced.set()
        await asyncio.Event().wait()

    rig = await Rig.create(tmp_path, lambda: [_commands(replace=replace_then_wait)])
    before = rig.runtime.harness
    plane = RpcControlPlane(rig.runtime)
    command = asyncio.create_task(plane.handle("commands/run", {"text": "/replace"}))
    await replaced.wait()

    verdict = await plane.handle("commands/cancel", {})
    outcome = await asyncio.gather(command, return_exceptions=True)

    assert verdict == {"cancelled": True, "method": "commands/run"}
    assert [type(value).__name__ for value in outcome] == ["CancelledError"]
    assert rig.runtime.harness is not before
    assert not rig.runtime.harness.runtime.is_stale
    await rig.runtime.harness.dispose()


async def test_eof_waits_out_a_commands_session_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutting_down = asyncio.Event()
    original = RpcControlPlane.begin_shutdown

    def observed(self: RpcControlPlane) -> None:
        original(self)
        shutting_down.set()

    monkeypatch.setattr(RpcControlPlane, "begin_shutdown", observed)

    async def replace(args: str, ctx: Any) -> None:
        await ctx.new_session()

    rig = await Rig.create(tmp_path, lambda: [_commands(replace=replace)])
    before = rig.runtime.harness
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            before,
            runtime_host=rig.runtime,
            stdin=wire.reader,
            stdout_write=wire.write,
            install_signal_handlers=False,
        )
    )
    hold = rig.hold = asyncio.Event()
    wire.send(_control("c", "commands/run", text="/replace"))
    await rig.build_entered.wait()
    wire.send(eof=True)
    await shutting_down.wait()  # shutdown began while the replacement was mid-build
    hold.set()
    await asyncio.wait_for(run, 10)
    # The replacement completed (the cut one left the disposed harness live).
    assert rig.runtime.harness is not before
    assert rig.builds == 1  # the factory's one build ran to the end


async def test_classic_mutators_wait_out_a_commands_session_replacement(tmp_path: Path) -> None:
    async def replace(args: str, ctx: Any) -> None:
        await ctx.new_session()

    rig = await Rig.create(tmp_path, lambda: [_commands(replace=replace)])
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            rig.runtime.harness,
            runtime_host=rig.runtime,
            stdin=wire.reader,
            stdout_write=wire.write,
            install_signal_handlers=False,
        )
    )
    hold = rig.hold = asyncio.Event()
    try:
        wire.send(_control("c", "commands/run", text="/replace"))
        await rig.build_entered.wait()
        wire.send(
            {"type": "set_session_name", "id": "n", "name": "renamed"},
            {"type": "get_state", "id": "s"},
        )
        renamed = await wire.response("n")
        assert renamed["success"] is False
        assert "commands/run" in renamed["error"]
        assert (await wire.response("s"))["success"] is True
        hold.set()
        assert (await wire.response("c"))["success"] is True
    finally:
        hold.set()
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)
        await rig.runtime.harness.dispose()


async def test_a_commands_replacement_waits_for_a_classic_command_to_answer(
    tmp_path: Path,
) -> None:
    started, proceed = asyncio.Event(), asyncio.Event()

    async def replace(args: str, ctx: Any) -> None:
        started.set()
        await proceed.wait()
        await ctx.new_session()

    rig = await Rig.create(tmp_path, lambda: [_commands(replace=replace)])
    before = rig.runtime.harness
    plane = RpcControlPlane(rig.runtime)
    command = asyncio.create_task(plane.handle("commands/run", {"text": "/replace"}))
    await started.wait()
    # A classic mutator that is not a transition is admitted beside an ordinary
    # command, and is still answering when the command asks for a replacement.
    refusal, counted = plane.classic_gate("set_session_name")
    assert refusal is None and counted
    proceed.set()
    result = await command
    plane.classic_done()

    assert result["outcome"] == "error"
    assert "classic RPC command" in result["messages"][0]["text"]
    assert rig.runtime.harness is before and rig.builds == 0
    await before.dispose()


# === F4: EOF cancels queued mutations instead of running them ===================


async def test_eof_cancels_queued_mutations_instead_of_running_them(tmp_path: Path) -> None:
    entered, late = asyncio.Event(), []

    async def hold(args: str, ctx: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    harness = AgentHarness(
        AgentHarnessOptions(
            model=MODEL, extensions=[_commands(hold=hold, late=lambda a, c: late.append(a))]
        )
    )
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            harness, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False
        )
    )
    try:
        wire.send(_control("h", "commands/run", text="/hold"))
        await entered.wait()
        wire.send(_control("l", "commands/run", text="/late"), eof=True)
        await asyncio.wait_for(run, 10)
        assert late == [], "a mutation queued at EOF must not run after the client left"
        assert (await wire.response("h"))["success"] is False
        assert (await wire.response("l"))["success"] is False
    finally:
        await harness.dispose()


# === F5: classic RPC and control mutations exclude each other ==================


async def test_classic_session_replacement_waits_out_a_control_mutation(tmp_path: Path) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(args: str, ctx: Any) -> None:
        entered.set()
        await release.wait()

    rig = await Rig.create(tmp_path, lambda: [_commands(hold=hold)])
    live = rig.runtime.harness
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            live,
            runtime_host=rig.runtime,
            stdin=wire.reader,
            stdout_write=wire.write,
            install_signal_handlers=False,
        )
    )
    try:
        wire.send(_control("h", "commands/run", text="/hold"))
        await entered.wait()
        wire.send({"type": "new_session", "id": "n"})
        refused = await wire.response("n")
        assert refused["success"] is False
        assert "control" in refused["error"].lower()
        assert rig.runtime.harness is live
        release.set()
        assert (await wire.response("h"))["success"] is True
    finally:
        release.set()
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)
        await rig.runtime.harness.dispose()


async def test_a_reload_admits_only_classic_reads_and_aborts(tmp_path: Path) -> None:
    rig = await Rig.create(tmp_path)
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            rig.runtime.harness,
            runtime_host=rig.runtime,
            stdin=wire.reader,
            stdout_write=wire.write,
            install_signal_handlers=False,
        )
    )
    hold = rig.hold = asyncio.Event()
    try:
        wire.send(_control("r", "resources/reload"))
        await rig.build_entered.wait()
        wire.send(
            {"type": "set_session_name", "id": "n", "name": "renamed"},
            {"type": "get_state", "id": "s"},
        )
        renamed = await wire.response("n")
        assert renamed["success"] is False
        assert "resources/reload" in renamed["error"]
        assert (await wire.response("s"))["success"] is True
        hold.set()
        assert (await wire.response("r"))["success"] is True
    finally:
        hold.set()
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)
        await rig.runtime.harness.dispose()


async def test_eof_lets_a_running_reload_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutting_down = asyncio.Event()
    original = RpcControlPlane.begin_shutdown

    def observed(self: RpcControlPlane) -> None:
        original(self)
        shutting_down.set()

    monkeypatch.setattr(RpcControlPlane, "begin_shutdown", observed)
    rig = await Rig.create(tmp_path)
    before = rig.runtime.harness
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            before,
            runtime_host=rig.runtime,
            stdin=wire.reader,
            stdout_write=wire.write,
            install_signal_handlers=False,
        )
    )
    hold = rig.hold = asyncio.Event()
    wire.send(_control("r", "resources/reload"))
    await rig.build_entered.wait()
    wire.send(eof=True)
    await shutting_down.wait()  # shutdown began while the reload was mid-build
    hold.set()
    await asyncio.wait_for(run, 10)
    # Completed, not cancelled (the old EOF path answered "Command cancelled" and
    # left the disposed harness live). run_rpc_mode disposes the runtime itself.
    reloaded = await wire.response("r")
    assert reloaded["success"] is True and reloaded["data"] == {"cancelled": False}
    assert rig.runtime.harness is not before


async def test_control_mutation_refuses_a_classic_prompt_still_starting(tmp_path: Path) -> None:
    posture = PermissionPosture()
    harness = AgentHarness(AgentHarnessOptions(model=MODEL, stream_fn=_stream))
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            harness,
            stdin=wire.reader,
            stdout_write=wire.write,
            install_signal_handlers=False,
            permission_posture=posture,
        )
    )
    try:
        # One chunk: the prompt task is created before the control mutation runs, but
        # its turn has not flipped the phase yet.
        wire.send(
            {"type": "prompt", "id": "p", "message": "hi"},
            _control("m", "permissions/set", mode="plan"),
        )
        mutation = await wire.response("m")
        assert mutation["success"] is False, mutation
        assert posture.get() is PermissionMode.DEFAULT
        assert (await wire.response("p"))["success"] is True
        await wire.record(lambda r: r.get("type") == "agent_end")
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)
        await harness.dispose()


# === F6: branching returns to the original session, or says it did not ========


async def test_branch_restores_the_original_session_and_tool_selection(tmp_path: Path) -> None:
    rig = await Rig.create(tmp_path)
    plane = RpcControlPlane(rig.runtime)
    original = rig.runtime.harness.session_file
    await plane.handle("tools/set_active", {"names": ["alpha"]})

    result = await plane.handle("sessions/branch", {"kind": "clone"})

    assert result["cancelled"] is False
    assert result.get("restored") is True
    assert result["sessionFile"] != original
    assert Path(result["sessionFile"]).is_file()
    assert rig.runtime.harness.session_file == original
    assert rig.runtime.harness.state.active_tool_names == ["alpha"]
    await rig.runtime.harness.dispose()


async def test_branch_reports_a_vetoed_return(tmp_path: Path) -> None:
    def veto() -> list[Extension]:
        extension = Extension(name="veto")
        extension.handlers["session_before_switch"] = [
            lambda event, ctx: SessionBeforeSwitchResult(cancel=True)
        ]
        return [extension]

    rig = await Rig.create(tmp_path, veto)
    plane = RpcControlPlane(rig.runtime)
    original = rig.runtime.harness.session_file
    try:
        await plane.handle("sessions/branch", {"kind": "clone"})
    except ValueError as error:
        message = str(error)
    else:  # pragma: no cover - the pre-fix behaviour this case exists for
        raise AssertionError("a vetoed return was reported as success")
    live = rig.runtime.harness.session_file
    assert live != original
    assert live is not None and live in message
    await rig.runtime.harness.dispose()
