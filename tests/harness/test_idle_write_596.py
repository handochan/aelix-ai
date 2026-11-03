"""#596 (ADR-0287) — ``AgentHarness.idle_write``, an outside writer's turn at the session.

The TUI records a ``!`` command between turns: an append to the session, then
to the live message list, with awaits in between. A turn an extension started
meanwhile (``send_message(..., trigger_turn=True)``) read the same leaf and
forked the session away from the record (Codex 596-02). Held, ``idle_write`` is
the lock every entry that drains the write queue waits on, so the turn starts
after the write instead of refusing or racing it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


async def _harness(tmp_path: Path, seen: list[Context]) -> tuple[AgentHarness, Session]:
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(tmp_path / "s.jsonl"), cwd=str(tmp_path), session_id="iw"
    )
    session = Session(storage)

    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(context)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="mock", provider="mock"), session=session, stream_fn=fn)
    )
    return harness, session


async def test_a_prompt_started_inside_waits_for_it_and_chains_after_its_entry(
    tmp_path: Path,
) -> None:
    seen: list[Context] = []
    harness, session = await _harness(tmp_path, seen)
    turn: asyncio.Task[object] | None = None
    async with harness.idle_write():
        entry_id = await session.append_custom_message_entry("note", "written outside", True)
        turn = asyncio.create_task(harness.prompt("hi", source="extension"))
        await asyncio.sleep(0.05)
        assert not seen  # it claimed the turn and waits at its drain
        assert not harness.is_idle
    assert turn is not None
    await asyncio.wait_for(turn, 5)
    entries = await session.get_entries()
    user = next(e for e in entries if getattr(getattr(e, "message", None), "role", None) == "user")
    assert user.parent_id == entry_id
    await harness.dispose()


async def test_it_is_refused_while_a_turn_runs(tmp_path: Path) -> None:
    seen: list[Context] = []
    harness, _session = await _harness(tmp_path, seen)
    gate = asyncio.Event()

    async def held(*_a: object, **_k: object) -> None:
        await gate.wait()

    harness.hooks.on("before_agent_start", held)
    turn = asyncio.create_task(harness.prompt("hi"))
    await asyncio.sleep(0.05)
    assert not harness.is_idle
    with pytest.raises(AgentHarnessError) as info:
        async with harness.idle_write():
            pass
    assert info.value.code == "busy"
    gate.set()
    await asyncio.wait_for(turn, 5)
    await harness.dispose()


async def test_a_compaction_asked_inside_waits_for_it(tmp_path: Path) -> None:
    seen: list[Context] = []
    harness, _session = await _harness(tmp_path, seen)
    order: list[str] = []

    async def compact() -> None:
        try:
            await harness.compact()
        except AgentHarnessError as exc:  # nothing to compact: it got past the drain
            order.append(f"compact: {exc.code}")

    async with harness.idle_write():
        task = asyncio.create_task(compact())
        await asyncio.sleep(0.05)
        order.append("write done")
    await asyncio.wait_for(task, 5)
    assert order[0] == "write done"
    await harness.dispose()
