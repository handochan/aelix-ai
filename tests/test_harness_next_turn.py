"""Tests for ``next_turn()`` and ``append_message()`` (Pi parity, spec §B).

Pi: ``agent-harness.ts:572-582`` (``nextTurn`` + ``appendMessage``) and
``agent-harness.ts:466-472`` (``executeTurn`` drain).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
)
from aelix_agent_core.harness.hooks import (
    QueueUpdateHookEvent,
    SettledHookEvent,
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


def _make_capturing_stream(captured: list[Context]) -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        captured.append(context)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")],
                stop_reason="end_turn",
            )
        )

    return fn


# === B.1 next_turn ====================================================


async def test_next_turn_idle_enqueue_and_emit() -> None:
    h = AgentHarness(AgentHarnessOptions())
    seen: list[QueueUpdateHookEvent] = []
    h.hooks.on("queue_update", lambda e, _c: seen.append(e))  # type: ignore[arg-type]

    await h.next_turn("hello")

    assert len(h._next_turn_queue) == 1
    msg = h._next_turn_queue[0]
    assert isinstance(msg, UserMessage)
    assert any(isinstance(c, TextContent) and c.text == "hello" for c in msg.content)

    assert len(seen) == 1
    assert len(seen[0].next_turn) == 1
    assert seen[0].steer == []
    assert seen[0].follow_up == []


async def test_next_turn_drained_at_next_prompt() -> None:
    captured: list[Context] = []
    h = AgentHarness(AgentHarnessOptions(stream_fn=_make_capturing_stream(captured)))

    await h.next_turn("queued-1")
    await h.prompt("real")

    # After prompt(), the queue is drained.
    assert h._next_turn_queue == []
    # The captured context's messages should contain BOTH the queued message
    # AND the prompt user message (prompts list is prepended to state.messages
    # at agent_loop ingress). ``captured[0]`` is recorded by the stream_fn to
    # prove the loop received the call; assertions below check state.messages
    # after the turn settles since that is the persisted result.
    assert captured  # stream_fn was reached
    assert any(
        isinstance(m, UserMessage)
        and any(isinstance(c, TextContent) and c.text == "queued-1" for c in m.content)
        for m in h.state.messages
    )
    assert any(
        isinstance(m, UserMessage)
        and any(isinstance(c, TextContent) and c.text == "real" for c in m.content)
        for m in h.state.messages
    )


async def test_next_turn_drain_emits_queue_update_at_next_prompt() -> None:
    """F-3b-3 (W5 should-fix): Pi ``executeTurn`` L487 emits ``queue_update``
    when the next_turn queue is drained at the start of the next turn.

    Two emits are expected end-to-end: one at ``next_turn()`` enqueue time
    (queue showing the message), and a second one when ``prompt()`` drains
    the queue (queue showing empty next_turn). Aelix Sprint 3b W6 ships the
    drain emit so observers see the empty snapshot consistent with Pi.
    """

    captured: list[Context] = []
    h = AgentHarness(AgentHarnessOptions(stream_fn=_make_capturing_stream(captured)))
    seen: list[QueueUpdateHookEvent] = []
    h.hooks.on("queue_update", lambda e, _c: seen.append(e))  # type: ignore[arg-type]

    await h.next_turn("queued-1")
    await h.prompt("real")

    # Two emits total: 1 at enqueue (next_turn=[queued]), 1 at drain (empty).
    assert len(seen) == 2, f"expected 2 queue_update emits, got {len(seen)}"
    assert len(seen[0].next_turn) == 1
    assert seen[1].next_turn == []


async def test_settled_next_turn_count_populated() -> None:
    captured: list[Context] = []
    h = AgentHarness(AgentHarnessOptions(stream_fn=_make_capturing_stream(captured)))
    seen: list[SettledHookEvent] = []
    h.hooks.on("settled", lambda e, _c: seen.append(e))  # type: ignore[arg-type]

    # Enqueue a follow-up next_turn message DURING this turn so by the time
    # ``settled`` fires the queue still has the pending entry.
    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.next_turn("future")
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hi")

    assert len(seen) == 1
    assert seen[0].next_turn_count == 1


# === B.2 append_message ===============================================


async def test_append_message_idle_direct_append() -> None:
    h = AgentHarness(AgentHarnessOptions())
    msg = UserMessage(content=[TextContent(text="from-test")])

    await h.append_message(msg)

    assert h.state.messages == [msg]
    # No pending writes accumulated for idle path.
    assert h._pending_session_writes == []


async def test_append_message_turn_pending_write() -> None:
    captured: list[Context] = []
    h = AgentHarness(AgentHarnessOptions(stream_fn=_make_capturing_stream(captured)))
    pending_during_turn: list[Any] = []

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.append_message(UserMessage(content=[TextContent(text="mid-turn")]))
        pending_during_turn.extend(h._pending_session_writes)
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hi")

    # During the turn a PendingMessageWrite existed; turn_end flushed it.
    assert len(pending_during_turn) == 1
    # After turn_end flush, the appended message is part of state.messages.
    assert any(
        isinstance(m, UserMessage)
        and any(isinstance(c, TextContent) and c.text == "mid-turn" for c in m.content)
        for m in h.state.messages
    )


async def test_append_message_emits_no_event() -> None:
    h = AgentHarness(AgentHarnessOptions())
    seen: list[Any] = []
    for name in ("queue_update", "model_select", "thinking_level_select", "resources_update"):
        h.hooks.on(name, lambda e, _c, _s=seen: _s.append(e))  # type: ignore[arg-type, call-overload]

    await h.append_message(UserMessage(content=[TextContent(text="hi")]))
    assert seen == []
