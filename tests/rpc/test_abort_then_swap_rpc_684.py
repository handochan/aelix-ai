"""#684 over the RPC wire — ``abort`` and ``new_session`` (or ``clone``) sent together.

Measured before the fix (``.omc/specs/663-followups-evidence/out/p7_rpc_abort_then_swap-*``):
both answered ``success: true``, no ``input_withdrawn`` came, and the old session's
path ended with the cancelled prompt — the swap's teardown aborted the turn again
with the default and overwrote the Esc. Sent after the ``abort`` response, or with
no swap at all, the prompt came back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
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
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

from tests.rpc.test_queued_lines_rpc_681 import _rpc

MODEL = Model(id="fixture", provider="fixture")


class _Rig:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.calls = 0
        self.reached = asyncio.Event()
        self.runtime: Any = None
        self.session: Session

    async def _stream(self, model: Model, context: Context, options: SimpleStreamOptions) -> Any:
        self.calls += 1
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if self.calls > 1:
            # The second prompt is held until it is cancelled.
            self.reached.set()
            await asyncio.Event().wait()
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="OK1")], stop_reason="end_turn")
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
        rig.session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        rig.runtime = await create_agent_session_runtime(
            rig.build(rig.session), rig.factory, repo=repo, fs=fs
        )
        return rig


async def _texts(session: Session) -> list[str]:
    out = []
    for entry in await session.get_branch():
        if entry.type == "message":
            message: Any = entry.message  # type: ignore[attr-defined]
            out.append("".join(getattr(c, "text", "") for c in message.content) or message.role)
    return out


@pytest.mark.parametrize("second", ["none", "new_session", "clone"])
async def test_abort_and_new_session_sent_together_give_the_prompt_back(
    tmp_path: Path, second: str
) -> None:
    rig = await _Rig.create(tmp_path)
    async with _rpc(rig) as wire:  # type: ignore[arg-type]
        ends = sum(1 for r in wire.records if r.get("type") == "agent_end")
        wire.send({"type": "prompt", "id": "p1", "message": "hello"})
        assert (await wire.response("p1"))["success"] is True
        await wire.record(
            lambda _r: sum(1 for r in wire.records if r.get("type") == "agent_end") > ends
        )
        wire.send({"type": "prompt", "id": "p2", "message": "CANCEL-ME"})
        assert (await wire.response("p2"))["success"] is True
        await asyncio.wait_for(rig.reached.wait(), 5)

        payloads: list[dict[str, Any]] = [{"type": "abort", "id": "a"}]
        if second != "none":
            payloads.append({"type": second, "id": "n"})
        wire.send(*payloads)  # one write: the two lines arrive together
        assert (await wire.response("a"))["success"] is True
        if second != "none":
            assert (await wire.response("n"))["success"] is True
        else:
            await wire.record(lambda r: r.get("type") == "input_withdrawn")

        withdrawn = [r for r in wire.records if r.get("type") == "input_withdrawn"]
        assert [
            ([m["content"][0]["text"] for m in r["messages"]], r["prompt_withdrawn"])
            for r in withdrawn
        ] == [(["CANCEL-ME"], True)]
        assert await _texts(rig.session) == ["hello", "OK1"]
        if second == "clone":
            # The clone reads the leaf after the Esc put it back (``_handle_clone``).
            assert await _texts(rig.runtime.session) == ["hello", "OK1"]


@pytest.mark.parametrize("second", ["new_session", "clone"])
async def test_an_abort_while_before_agent_start_runs_gives_the_prompt_back(
    tmp_path: Path, second: str
) -> None:
    """Codex review r1: the prompt is accepted and its ``before_agent_start`` handler still runs.

    The abort found no turn to cancel and ``_run`` cleared it: the prompt went to
    the model, the swap waited for it, no ``input_withdrawn`` came and the clone
    copied the prompt with its answer.
    """

    rig = await _Rig.create(tmp_path)
    async with _rpc(rig) as wire:  # type: ignore[arg-type]
        ends = sum(1 for r in wire.records if r.get("type") == "agent_end")
        wire.send({"type": "prompt", "id": "p1", "message": "hello"})
        assert (await wire.response("p1"))["success"] is True
        await wire.record(
            lambda _r: sum(1 for r in wire.records if r.get("type") == "agent_end") > ends
        )
        entered, release = asyncio.Event(), asyncio.Event()

        async def _held(_event: Any, _ctx: Any) -> None:
            entered.set()
            await release.wait()

        rig.runtime.harness.hooks.on("before_agent_start", _held)
        wire.send({"type": "prompt", "id": "p2", "message": "CANCEL-ME"})
        assert (await wire.response("p2"))["success"] is True
        await asyncio.wait_for(entered.wait(), 5)

        wire.send({"type": "abort", "id": "a"}, {"type": second, "id": "n"})
        assert (await wire.response("a"))["success"] is True
        release.set()
        assert (await asyncio.wait_for(wire.response("n"), 5))["success"] is True

        withdrawn = [r for r in wire.records if r.get("type") == "input_withdrawn"]
        assert [
            ([m["content"][0]["text"] for m in r["messages"]], r["prompt_withdrawn"])
            for r in withdrawn
        ] == [(["CANCEL-ME"], True)]
        assert rig.calls == 1, "the cancelled prompt reached the model"
        assert await _texts(rig.session) == ["hello", "OK1"]
        if second == "clone":
            assert await _texts(rig.runtime.session) == ["hello", "OK1"]
