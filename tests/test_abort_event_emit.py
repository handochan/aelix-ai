"""Tests for ``AbortHookEvent`` emit site (Sprint 3d P-10 closure).

Pi parity invariant: ``AgentHarness.abort()`` emits a dedicated ``abort``
own-event carrying snapshots of the messages that were cleared from the
steer and follow_up queues. This was the last Phase 2.1 emit-site gap
identified by the closure pin (ADR-0039 §P-10).

Pi source: ``agent-harness.ts`` ``abort()`` method (SHA 734e08e) — Pi
captures ``clearedSteer`` / ``clearedFollowUp`` arrays BEFORE clearing the
queues, then emits the ``abort`` own-event.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    AbortHookEvent,
    QueueUpdateHookEvent,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


def _text_msg(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        stop_reason="end_turn",
    )


async def test_abort_emits_abort_hook_event_with_cleared_snapshots() -> None:
    """abort() must emit AbortHookEvent with the pre-clear snapshots."""

    gate = asyncio.Event()

    async def slow_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("done"))

    h = AgentHarness(AgentHarnessOptions(stream_fn=slow_stream))
    seen_abort: list[AbortHookEvent] = []
    h.hooks.on("abort", lambda e, _c: seen_abort.append(e))  # type: ignore[arg-type]

    task = asyncio.create_task(h.prompt("go"))
    await asyncio.sleep(0)

    await h.steer("s1")
    await h.steer("s2")
    await h.follow_up("f1")

    await h.abort()

    gate.set()
    await task

    # Exactly one abort event was emitted with the pre-clear snapshots.
    assert len(seen_abort) == 1
    event = seen_abort[0]
    assert isinstance(event, AbortHookEvent)
    assert len(event.cleared_steer) == 2
    assert len(event.cleared_follow_up) == 1
    # Snapshot order is insertion order (Pi parity).
    steer_texts = [
        c.text
        for m in event.cleared_steer
        if isinstance(m, UserMessage)
        for c in m.content
        if isinstance(c, TextContent)
    ]
    follow_up_texts = [
        c.text
        for m in event.cleared_follow_up
        if isinstance(m, UserMessage)
        for c in m.content
        if isinstance(c, TextContent)
    ]
    assert steer_texts == ["s1", "s2"]
    assert follow_up_texts == ["f1"]


async def test_abort_with_empty_queues_emits_event_with_empty_snapshots() -> None:
    """abort() always emits AbortHookEvent — empty queues yield empty lists."""

    async def quick_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("done"))

    h = AgentHarness(AgentHarnessOptions(stream_fn=quick_stream))
    seen_abort: list[AbortHookEvent] = []
    h.hooks.on("abort", lambda e, _c: seen_abort.append(e))  # type: ignore[arg-type]

    # No steer/follow_up enqueued; queues are empty when abort fires.
    await h.abort()

    assert len(seen_abort) == 1
    assert seen_abort[0].cleared_steer == []
    assert seen_abort[0].cleared_follow_up == []


async def test_abort_emits_abort_event_before_queue_update() -> None:
    """abort() emits AbortHookEvent first (with snapshots), then queue_update.

    Order matters: ``abort`` carries the pre-clear payload, ``queue_update``
    carries the post-clear state. Observers reasoning about queue transitions
    rely on this ordering.
    """

    order: list[str] = []

    async def quick_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("done"))

    h = AgentHarness(AgentHarnessOptions(stream_fn=quick_stream))

    def abort_handler(event: AbortHookEvent, _ctx: Any) -> None:
        order.append("abort")

    def queue_handler(event: QueueUpdateHookEvent, _ctx: Any) -> None:
        order.append("queue_update")

    h.hooks.on("abort", abort_handler)  # type: ignore[arg-type]
    h.hooks.on("queue_update", queue_handler)  # type: ignore[arg-type]

    await h.steer("s1")
    # ``steer`` itself emits queue_update; clear the order list so we only
    # observe the abort-driven sequence.
    order.clear()

    await h.abort()

    assert order == ["abort", "queue_update"]
