"""#347 over the RPC wire — ``compact`` and ``abort`` run at once (#684).

What a host sees: the ``compact`` response is an error saying it was cancelled, not a
crash and not a summary; ``compaction_end`` says aborted; the file holds no entry; and
the session goes on. ``abort`` is answered first, since ``compact`` waits for what it stops.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import ReloadSeed, create_agent_session_runtime
from aelix_agent_core.session import (
    CompactionPreparation,
    CompactResult,
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

from tests.rpc.test_queued_lines_rpc_681 import _prompt, _rpc
from tests.rpc.test_runtime_control_lifecycle_594 import Wire

MODEL = Model(id="fixture", provider="fixture", context_window=200_000, max_tokens=4096)


class _Rig:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.entered = asyncio.Event()
        self.cancelled = 0
        self.hold = True
        self.runtime: Any = None
        self.session: Session

    async def _stream(self, model: Model, context: Context, options: SimpleStreamOptions) -> Any:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="OK")], stop_reason="end_turn")
        )

    async def _summarise(
        self, _model: Any, prep: CompactionPreparation, _ci: Any
    ) -> CompactResult:
        self.entered.set()
        if self.hold:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )

    def build(self, session: Session) -> AgentHarness:
        return AgentHarness(
            AgentHarnessOptions(
                model=MODEL,
                stream_fn=self._stream,
                session=session,
                cwd=str(self.tmp),
                _summarizer_override=self._summarise,
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
        for _ in range(6):
            await rig.session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
        rig.runtime = await create_agent_session_runtime(
            rig.build(rig.session), rig.factory, repo=repo, fs=fs
        )
        return rig

    async def compactions(self) -> int:
        return len([e for e in await self.session.get_entries() if e.type == "compaction"])


def _ends(wire: Any) -> list[tuple[Any, ...]]:
    return [
        (r["reason"], r["aborted"], r.get("error_message"))
        for r in wire.records
        if r.get("type") == "compaction_end"
    ]


async def test_an_abort_during_compact_answers_it_with_an_error_saying_it_was_cancelled(
    tmp_path: Path,
) -> None:
    rig = await _Rig.create(tmp_path)
    async with _rpc(rig) as wire:  # type: ignore[arg-type]
        wire.send({"type": "compact", "id": "c"})
        await asyncio.wait_for(rig.entered.wait(), 5)
        wire.send({"type": "abort", "id": "a"})

        answered = await wire.response("c")
        assert answered["success"] is False
        assert answered["error"] == "Compaction cancelled"
        assert (await wire.response("a"))["success"] is True
        await wire.record(lambda r: r.get("type") == "compaction_end")

        assert rig.cancelled == 1
        assert _ends(wire) == [("manual", True, None)]
        assert await rig.compactions() == 0

        # The session goes on, and the abort is not held against what comes next.
        await _prompt(wire, "p", "next")
        rig.hold = False
        wire.send({"type": "compact", "id": "c2"})
        assert (await wire.response("c2"))["success"] is True
        assert await rig.compactions() == 1


async def test_compact_and_abort_sent_together_in_that_order_cancel_it(tmp_path: Path) -> None:
    """``compact`` has taken its first step when ``abort`` lands, and no request goes out."""

    rig = await _Rig.create(tmp_path)
    async with _rpc(rig) as wire:  # type: ignore[arg-type]
        wire.send({"type": "compact", "id": "c"}, {"type": "abort", "id": "a"})
        answered = await wire.response("c")
        assert (answered["success"], answered["error"]) == (False, "Compaction cancelled")
        assert (await wire.response("a"))["success"] is True
        assert await rig.compactions() == 0
        assert not rig.entered.is_set()


async def test_abort_and_compact_sent_together_in_that_order_compact(tmp_path: Path) -> None:
    """The abort landed while idle, so it is no abort of the compaction after it."""

    rig = await _Rig.create(tmp_path)
    rig.hold = False
    async with _rpc(rig) as wire:  # type: ignore[arg-type]
        wire.send({"type": "abort", "id": "a"}, {"type": "compact", "id": "c"})
        assert (await wire.response("a"))["success"] is True
        assert (await wire.response("c"))["success"] is True
        assert await rig.compactions() == 1


# === The way out: a stdin EOF, as a signal, waits on the command tasks ======


@pytest.mark.parametrize("parked_abort_handler", [False, True], ids=["plain", "parked-handler"])
async def test_an_eof_during_compact_stops_it_and_the_run_ends(
    tmp_path: Path, parked_abort_handler: bool
) -> None:
    """Nobody releases the summary: the shutdown's wait for ``compact`` waited on it."""

    rig = await _Rig.create(tmp_path)
    if parked_abort_handler:

        async def park(_event: Any, _ctx: Any) -> None:
            await asyncio.Event().wait()

        rig.runtime.harness.hooks.on("abort", park)
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
        wire.send({"type": "compact", "id": "c"})
        await asyncio.wait_for(rig.entered.wait(), 5)
        wire.send(eof=True)
        await asyncio.wait_for(run, 5)
    finally:
        run.cancel()
        with contextlib.suppress(BaseException):
            await run
    answered = await wire.response("c")
    assert (answered["success"], answered["error"]) == (False, "Compaction cancelled")
    assert rig.cancelled == 1
    assert await rig.compactions() == 0
