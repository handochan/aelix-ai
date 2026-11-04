"""#663 (ADR-0289) — after a failed ``resources/reload`` the gates are still there.

Measured before the fix (``.omc/specs/25-followups-evidence/i2a/rpc``): a
``resources/reload`` that raised after the teardown answered an error and kept
taking prompts on the torn-down harness, whose ``tool_call`` handlers were gone —
the model's ``rm -rf … && echo GUARD > guard.txt`` ran and wrote ``guard.txt``.

Here the harness carries the real Guardrail and Permission extensions (in the
order ``cli/entry.py`` prepends them) and the real built-in ``bash`` tool; the
model is scripted. The RPC host is ``run_rpc_mode`` over a JSONL wire.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import STOPPED_MESSAGE, ReloadSeed, create_agent_session_runtime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.builtin.guardrail import GuardrailExtension
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.extensions.loader import load_extensions
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode
from aelix_coding_agent.tools import create_all_tools

from tests.rpc.test_runtime_control_lifecycle_594 import Wire, _control

MODEL = Model(id="fixture", provider="fixture")


class _GateRig:
    def __init__(self, tmp: Path) -> None:
        self.work = tmp / "work"
        self.work.mkdir()
        self.tmp = tmp
        self.requests = 0
        self.fail: Callable[[ReloadSeed | None], bool] = lambda _seed: False
        self.runtime: Any = None

    async def _stream(
        self, model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        self.requests += 1
        last = context.messages[-1]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if getattr(last, "role", None) == "user":
            tag = "".join(getattr(c, "text", "") for c in last.content).split()[-1]
            command = f"rm -rf ./no-such-dir-663 && echo GUARD > {tag}.txt"
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[
                        ToolCallContent(
                            tool_call_id=f"call_{self.requests}",
                            tool_name="bash",
                            input={"command": command},
                        )
                    ],
                    stop_reason="tool_use",
                )
            )
            return
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        )

    async def build(self, session: Session) -> AgentHarness:
        async def rebuild(args: str, ctx: Any) -> str:
            await ctx.reload()
            return "rebuilt"

        def commands(aelix: Any) -> None:
            aelix.register_command("rebuild", handler=rebuild, description="rebuild")

        # The order ``cli/entry.py`` prepends them in: Guardrail first.
        loaded = await load_extensions([GuardrailExtension(), PermissionExtension(), commands])
        return AgentHarness(
            AgentHarnessOptions(
                model=MODEL,
                stream_fn=self._stream,
                session=session,
                cwd=str(self.work),
                tools=[create_all_tools(str(self.work))["bash"]],
                extensions=loaded.extensions,
                runtime=loaded.runtime,
            )
        )

    async def factory(self, session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        if self.fail(reload_seed):
            raise RuntimeError("build boom")
        return await self.build(session)

    @classmethod
    async def create(cls, tmp: Path) -> _GateRig:
        rig = cls(tmp)
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp / "sessions"))
        session = await repo.create(JsonlSessionCreateOptions(cwd=str(rig.work)))
        await session.append_message(UserMessage(content=[TextContent(text="hello")]))
        rig.runtime = await create_agent_session_runtime(
            await rig.build(session), rig.factory, repo=repo, fs=fs
        )
        return rig


def _once(seed: ReloadSeed | None) -> bool:
    return not (seed is not None and seed.restore)


def _always(_seed: ReloadSeed | None) -> bool:
    return True


def _never(_seed: ReloadSeed | None) -> bool:
    return False


@contextlib.asynccontextmanager
async def _rpc(rig: _GateRig) -> AsyncIterator[Wire]:
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
    try:
        yield wire
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(run, 30)
        with contextlib.suppress(Exception):
            await rig.runtime.harness.dispose()


def _ends(wire: Wire) -> int:
    return sum(1 for r in wire.records if r.get("type") == "agent_end")


async def _prompt(wire: Wire, request_id: str, text: str) -> dict[str, Any]:
    ends = _ends(wire)
    wire.send({"type": "prompt", "id": request_id, "message": text})
    response = await wire.response(request_id)
    if response.get("success"):
        await wire.record(lambda _r: _ends(wire) > ends)
    return response


def _tool_results(wire: Wire) -> list[str]:
    return [
        str(r.get("result"))
        for r in wire.records
        if r.get("type") == "tool_execution_end" and r.get("tool_name") == "bash"
    ]


async def test_rpc_reload_failure_keeps_guardrail_then_stops_then_recovers(tmp_path: Path) -> None:
    rig = await _GateRig.create(tmp_path)
    async with _rpc(rig) as wire:
        # Control: before any reload, the guardrail refuses.
        assert (await _prompt(wire, "p0", "please before"))["success"] is True
        assert not (rig.work / "before.txt").exists()

        # 1. The rebuild fails, the build on the previous settings works: kept.
        rig.fail = _once
        wire.send(_control("r1", "resources/reload"))
        r1 = await wire.response("r1")
        assert r1["success"] is False
        assert "RuntimeError: build boom — kept the previous settings" in r1["error"]
        assert (await _prompt(wire, "p1", "please kept"))["success"] is True
        assert not (rig.work / "kept.txt").exists()
        assert "[guardrail]" in _tool_results(wire)[-1]

        # 2. Both builds fail: stopped. The prompt is refused before any request.
        rig.fail = _always
        wire.send(_control("r2", "resources/reload"))
        r2 = await wire.response("r2")
        assert r2["success"] is False and "aelix is stopped until /reload succeeds" in r2["error"]
        requests = rig.requests
        p2 = await _prompt(wire, "p2", "please stopped")
        assert p2["success"] is False and STOPPED_MESSAGE in p2["error"]
        assert rig.requests == requests
        assert not (rig.work / "stopped.txt").exists()

        # 3. The cause is fixed: the next reload succeeds and the gate is back.
        rig.fail = _never
        wire.send(_control("r3", "resources/reload"))
        assert (await wire.response("r3"))["success"] is True
        assert (await _prompt(wire, "p3", "please recovered"))["success"] is True
        assert not (rig.work / "recovered.txt").exists()
        assert "[guardrail]" in _tool_results(wire)[-1]


async def test_an_extensions_ctx_reload_says_what_runs_after_it_failed(tmp_path: Path) -> None:
    rig = await _GateRig.create(tmp_path)
    async with _rpc(rig) as wire:
        rig.fail = _once
        wire.send(_control("c1", "commands/run", text="/rebuild"))
        c1 = await wire.response("c1")
        errors = [m["text"] for m in c1["data"]["messages"] if m["type"] == "error"]
        assert any("kept the previous settings" in e for e in errors), c1
        assert rig.runtime.stopped is None
        assert (await _prompt(wire, "p1", "please ext")).get("success") is True
        assert not (rig.work / "ext.txt").exists()

        rig.fail = _always
        wire.send(_control("c2", "commands/run", text="/rebuild"))
        c2 = await wire.response("c2")
        errors = [m["text"] for m in c2["data"]["messages"] if m["type"] == "error"]
        assert any("aelix is stopped until /reload succeeds" in e for e in errors), c2
        assert rig.runtime.stopped == STOPPED_MESSAGE


async def test_nothing_runs_over_rpc_while_stopped(tmp_path: Path) -> None:
    """Codex R03, verify V1 and V7: bash, steer, follow-up and agents/run say the stop.

    Before: RPC ``bash`` ran the client's command on the stopped runtime (ADR-0289
    named it an exception), ``steer`` and ``follow_up`` answered ``success: true``
    and the messages were lost, and ``agents/run`` said "Subagents are disabled".
    """

    rig = await _GateRig.create(tmp_path)
    async with _rpc(rig) as wire:
        rig.fail = _always
        wire.send(_control("r1", "resources/reload"))
        assert "aelix is stopped" in (await wire.response("r1"))["error"]

        wire.send({"type": "bash", "id": "b1", "command": "echo RPC > bypass.txt"})
        b1 = await wire.response("b1")
        assert b1["success"] is False and b1["error"] == STOPPED_MESSAGE
        assert not (rig.work / "bypass.txt").exists()

        for kind in ("steer", "follow_up"):
            wire.send({"type": kind, "id": kind, "message": "later"})
            answer = await wire.response(kind)
            assert answer["success"] is False and STOPPED_MESSAGE in answer["error"], answer
        wire.send({"type": "get_state", "id": "s1"})
        assert (await wire.response("s1"))["data"]["pendingMessageCount"] == 0

        wire.send(_control("a1", "agents/run", name="helper", task="do it"))
        a1 = await wire.response("a1")
        assert a1["success"] is False and a1["error"] == STOPPED_MESSAGE, a1

        # Recovery: a reload that works runs the client's bash again.
        rig.fail = _never
        wire.send(_control("r2", "resources/reload"))
        assert (await wire.response("r2"))["success"] is True
        wire.send({"type": "bash", "id": "b2", "command": "echo RPC > after.txt"})
        assert (await wire.response("b2"))["success"] is True
        assert (rig.work / "after.txt").exists()


async def test_a_prompt_during_a_classic_swap_is_refused(tmp_path: Path) -> None:
    """Verify V2: a classic ``new_session`` never took the control lock, so a
    ``prompt`` sent while it rebuilt ran on the harness it had torn down."""

    rig = await _GateRig.create(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    real = rig.factory

    async def slow(session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        entered.set()
        await release.wait()
        return await real(session, reload_seed=reload_seed)

    rig.runtime._create_harness = slow
    async with _rpc(rig) as wire:
        wire.send({"type": "new_session", "id": "n1"})
        await entered.wait()
        requests = rig.requests
        wire.send({"type": "prompt", "id": "p1", "message": "please window"})
        p1 = await wire.response("p1")
        assert p1["success"] is False
        assert "unavailable while a session lifecycle transition runs" in p1["error"]
        release.set()
        assert (await wire.response("n1"))["success"] is True
        assert rig.requests == requests and not (rig.work / "window.txt").exists()
        # Once the swap has answered, prompts run on the new harness, gated.
        assert (await _prompt(wire, "p2", "please after")).get("success") is True
        assert not (rig.work / "after.txt").exists()
