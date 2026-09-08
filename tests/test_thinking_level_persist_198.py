"""Issue #198, half 1 — an idle ``/thinking`` change must be written down.

Measured on ``main`` (0985fcf) before the fix: with the harness at phase
``idle``, ``await harness.set_thinking_level("high")`` left
``state.thinking_level == "high"`` but the session's entry types were ``[]`` and
``build_context().thinking_level`` was ``"off"`` — ``set_thinking_level`` queued a
``PendingThinkingLevelChangeWrite`` only while ``_phase == "turn"``, and the flush
dispatcher is the sole caller of ``Session.append_thinking_level_change``. Every
caller that a user actually reaches (the ``/thinking`` picker, the ``/settings``
row, the startup seed, ``/agents use``) runs idle, so the session file carried no
record of the level and both resume seams had nothing to restore.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _harness(session: Session) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
        )
    )


async def _entry_types(session: Session) -> list[str]:
    return [e.type for e in await session.get_entries()]


async def test_idle_set_thinking_level_appends_entry() -> None:
    """THE REGRESSION: an idle set writes a ``thinking_level_change`` entry.

    Without the append this reads ``[]`` / ``"off"`` — which is what ``main``
    measured — and every restore below is a no-op for the picker path.
    """

    session = Session(MemorySessionStorage())
    harness = _harness(session)

    await harness.set_thinking_level("high")

    assert await _entry_types(session) == ["thinking_level_change"]
    assert harness.state.thinking_level == "high"
    ctx = await session.build_context()
    assert ctx.thinking_level == "high"


async def test_repeat_set_thinking_level_writes_once() -> None:
    """The change-guard (pi ``isChanging``): re-selecting the live level writes
    nothing, so the JSONL does not grow an entry per picker re-select."""

    session = Session(MemorySessionStorage())
    harness = _harness(session)

    await harness.set_thinking_level("high")
    await harness.set_thinking_level("high")
    await harness.set_thinking_level("high")

    assert await _entry_types(session) == ["thinking_level_change"]

    # A real change still writes.
    await harness.set_thinking_level("low")
    assert await _entry_types(session) == [
        "thinking_level_change",
        "thinking_level_change",
    ]
    assert (await session.build_context()).thinking_level == "low"


async def test_set_thinking_level_during_turn_still_defers() -> None:
    """In-turn behaviour is untouched: the write stays deferred to the pending
    queue so the control entry lands AFTER the turn's messages, not ahead of
    them (``tests/test_harness_setters.py`` pins the push itself)."""

    session = Session(MemorySessionStorage())
    harness = _harness(session)
    during: list[list[str]] = []

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await harness.set_thinking_level("medium")
        during.append(await _entry_types(session))
        return None

    harness.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await harness.prompt("hi")

    # Nothing written at the moment of the in-turn set.
    assert during == [[]]
    assert harness.state.thinking_level == "medium"
    # ...and the flush put it after the turn's messages.
    types = await _entry_types(session)
    assert types[-1] == "thinking_level_change"
    assert types.count("thinking_level_change") == 1


async def test_raising_hook_leaves_no_entry() -> None:
    """Ordering (§A.1): the append runs AFTER the ``thinking_level_select``
    emit, so an extension that refuses the level leaves no record of it.

    ``/agents use`` rolls a rejected level back by writing ``AgentState``
    directly (``agents/service.py``), which cannot undo a session append — so
    the ordering is the only thing keeping a refused level out of the file.
    """

    session = Session(MemorySessionStorage())
    harness = _harness(session)

    def boom(event: Any, _ctx: Any) -> Any:
        raise RuntimeError("nope")

    harness.hooks.on("thinking_level_select", boom)  # type: ignore[arg-type]

    with pytest.raises(AgentHarnessError) as exc:
        await harness.set_thinking_level("high")

    assert exc.value.code == "hook"
    assert await _entry_types(session) == []


async def test_extension_set_thinking_level_retrieves_a_failed_append(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``ExtensionAPI.setThinkingLevel`` fires the setter as a pinned
    fire-and-forget task. Now that the idle path does file I/O, a session whose
    append fails (read-only sessions directory, full disk) raises inside a task
    nobody awaits — and an exception that is never *retrieved* is reported by
    asyncio at GC time as ``Task exception was never retrieved``, in a traceback
    with no caller. ``_pin_task``'s done callback retrieves it and logs instead.
    """

    session = Session(MemorySessionStorage())

    async def _boom(thinking_level: str) -> str:
        raise OSError("read-only session directory")

    session.append_thinking_level_change = _boom  # type: ignore[method-assign]
    harness = _harness(session)

    with caplog.at_level(logging.DEBUG, logger="aelix_agent_core.harness.core"):
        harness._action_set_thinking_level("high")
        # One yield per await in the setter's path, plus the done callback,
        # which asyncio runs via ``call_soon``.
        for _ in range(6):
            await asyncio.sleep(0)

    assert not harness._pending_tasks
    assert any(
        "pinned extension-action task raised" in r.message for r in caplog.records
    )
