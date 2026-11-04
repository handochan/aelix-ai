"""#681 over the RPC wire — a ``follow_up`` queued while idle survives ``resources/reload``
and comes back on ``input_withdrawn`` after ``new_session``.

Measured before the fix (``.omc/specs/663-followups-evidence/out/p4_rpc_idle_queue-*``):
``follow_up`` answered ``success: true``, then ``pendingMessageCount`` went from 1 to 0
across the reload or the swap, and no event said where the line went; after the
reload it never reached the model.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import ReloadSeed, create_agent_session_runtime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

from tests.rpc.test_runtime_control_lifecycle_594 import Wire, _control

MODEL = Model(id="fixture", provider="fixture")


class _Rig:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.requests: list[list[str]] = []
        self.runtime: Any = None

    async def _stream(
        self, model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        self.requests.append(
            [
                "".join(getattr(c, "text", "") for c in m.content)
                for m in context.messages
                if getattr(m, "role", None) == "user"
            ]
        )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    def build(self, session: Session) -> AgentHarness:
        return AgentHarness(
            AgentHarnessOptions(
                model=MODEL, stream_fn=self._stream, session=session, cwd=str(self.tmp)
            )
        )

    async def factory(
        self, session: Session, *, reload_seed: ReloadSeed | None = None
    ) -> AgentHarness:
        return self.build(session)

    @classmethod
    async def create(cls, tmp: Path) -> _Rig:
        rig = cls(tmp)
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp / "sessions"))
        session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        rig.runtime = await create_agent_session_runtime(
            rig.build(session), rig.factory, repo=repo, fs=fs
        )
        return rig


@contextlib.asynccontextmanager
async def _rpc(rig: _Rig) -> AsyncIterator[Wire]:
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


async def _prompt(wire: Wire, request_id: str, text: str) -> None:
    ends = _ends(wire)
    wire.send({"type": "prompt", "id": request_id, "message": text})
    assert (await wire.response(request_id))["success"] is True
    await wire.record(lambda _r: _ends(wire) > ends)


async def _pending(wire: Wire, request_id: str) -> int:
    wire.send({"type": "get_state", "id": request_id})
    return (await wire.response(request_id))["data"]["pendingMessageCount"]


def _withdrawn(wire: Wire) -> list[tuple[list[str], bool]]:
    return [
        ([m["content"][0]["text"] for m in r["messages"]], r["prompt_withdrawn"])
        for r in wire.records
        if r.get("type") == "input_withdrawn"
    ]


async def test_a_line_queued_while_idle_survives_resources_reload(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    async with _rpc(rig) as wire:
        await _prompt(wire, "p1", "hello")
        wire.send({"type": "follow_up", "id": "f", "message": "QUEUED-IDLE"})
        assert (await wire.response("f"))["success"] is True
        assert await _pending(wire, "s0") == 1

        wire.send(_control("r", "resources/reload"))
        assert (await wire.response("r"))["success"] is True
        assert await _pending(wire, "s1") == 1

        await _prompt(wire, "p2", "next")
        assert await _pending(wire, "s2") == 0
        assert "QUEUED-IDLE" in rig.requests[-1]
        assert _withdrawn(wire) == []


async def test_new_session_gives_a_queued_line_back(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    async with _rpc(rig) as wire:
        await _prompt(wire, "p1", "hello")
        wire.send({"type": "follow_up", "id": "f", "message": "QUEUED-IDLE"})
        assert (await wire.response("f"))["success"] is True

        wire.send({"type": "new_session", "id": "n"})
        assert (await wire.response("n"))["success"] is True
        assert _withdrawn(wire) == [(["QUEUED-IDLE"], False)]
        assert await _pending(wire, "s1") == 0

        await _prompt(wire, "p2", "next")
        assert rig.requests[-1] == ["next"]


async def test_a_line_queued_during_the_teardown_reaches_the_host(tmp_path: Path) -> None:
    """Codex review r1 — a ``session_shutdown`` handler's ``follow_up`` comes back after the teardown.

    The RPC lifecycle gate refuses a host's ``steer`` in that window; an
    extension's call goes straight to the harness. The event comes from the
    harness being replaced, which the wire still listens to until the rebind.
    """

    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness

    async def _shutdown(_event: Any, _ctx: Any) -> None:
        await old.follow_up("SHUTDOWN-LINE")

    old.hooks.on("session_shutdown", _shutdown)
    async with _rpc(rig) as wire:
        wire.send({"type": "new_session", "id": "n"})
        assert (await wire.response("n"))["success"] is True
        assert _withdrawn(wire) == [(["SHUTDOWN-LINE"], False)]
        assert await _pending(wire, "s1") == 0
