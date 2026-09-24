"""Issue #311 — a hook that throws during the drain must not eat the queue.

``prompt()`` detaches ``_next_turn_queue`` into a local (``core.py:1304-1305``)
and only hands it to ``_run`` some twenty lines later. Two awaits sit in that
gap — ``_emit_queue_update`` and ``_emit_before_agent_start`` — and both turn a
handler exception into a raised ``AgentHarnessError``, while a handler's
``error_mode`` defaults to ``"throw"`` (``hooks.py:2493-2500``). Before this suite
existed, either raise unwound the frame with the user's queued text in a dead
local: on no queue, in no turn, in no exception.

Measured with ``.omc/specs/311-next-turn-drain.py`` on fad2e28 — the message
queued, the observer raised, ``queue after: []``, ``reached stream_fn: []``,
``in state.messages: []``.

The fault is injected at the hook seam, which is where a real one arrives: any
extension observing ``queue_update`` to repaint a queue indicator, or any
``before_agent_start`` handler, is one unguarded attribute access away from
being this failure.

Two of these tests are not about the loss at all.
``test_a_turn_that_fails_after_the_drain_does_not_requeue`` pins the guard's
far edge — once ``agent_loop`` has been handed the prompts the messages have
reached the provider, so restoring them would deliver the same sentence twice
— and ``test_both_push_sites_reach_the_guarded_queue`` covers the other side:
every other test here fills the queue through ``next_turn()``, so a push site
that stopped pushing would leave this file green.

What this file does NOT cover, because the fix does not reach it: ``_run``
awaits ``self._session.build_context()`` (``core.py:4544``) before
``agent_loop`` is handed the list, and a session raising there loses the
drained messages the same three ways. That window is measured in
``.omc/specs/311-next-turn-drain.py`` ARM 6 and reported as its own issue;
ADR-0246 §"Where the guard ends" says why widening this guard is not its
remedy (it reddens the far-edge test above).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
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

QUEUED = "QUEUED-BY-NEXT-TURN"
LATE = "ARRIVED-DURING-AWAIT"


def _texts(messages: Any) -> list[str]:
    """Every text block in ``messages``, in order."""

    return [
        c.text
        for m in messages
        for c in getattr(m, "content", [])
        if hasattr(c, "text")
    ]


def _harness(*, fail_stream: bool = False) -> tuple[AgentHarness, list[list[Any]]]:
    """A harness plus the list of message-lists its ``stream_fn`` was handed.

    Recording at ``stream_fn`` rather than at ``prompt``'s arguments is
    deliberate: what these tests care about is DELIVERY to the provider, not
    forwarding. ``tests/harness/test_harness_prompt_images.py`` records the
    reason that distinction is load-bearing in this file's neighbourhood.
    """

    seen: list[list[Any]] = []

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(list(context.messages))
        if fail_stream:
            raise RuntimeError("provider blew up")
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    return AgentHarness(AgentHarnessOptions(stream_fn=fn)), seen


def _throwing(message: str) -> Any:
    def handler(event: Any, _ctx: Any) -> None:
        raise RuntimeError(message)

    return handler


# === The loss itself ==================================================


async def test_a_throwing_queue_update_observer_leaves_the_next_turn_queue_intact() -> None:
    h, seen = _harness()
    # Queue while idle. ``next_turn()`` emits ``queue_update`` itself, so the
    # faulty handler is registered afterwards — an extension that loads late,
    # or an observer that starts failing between two turns.
    await h.next_turn(QUEUED)
    h.hooks.on("queue_update", _throwing("observer blew up"))  # type: ignore[arg-type]

    with pytest.raises(AgentHarnessError):
        await h.prompt("live user text")

    assert _texts(h._next_turn_queue) == [QUEUED]
    # And it really did not reach the turn, so the queue is the ONLY place it
    # can be — an assertion that the queue is non-empty would also pass if the
    # message had been delivered and restored.
    assert seen == []
    assert _texts(h.state.messages) == []


async def test_a_throwing_before_agent_start_handler_leaves_the_next_turn_queue_intact() -> None:
    """The window is wider than the issue said.

    #311 named ``_emit_queue_update``. ``_emit_before_agent_start``
    (``core.py:1342``) is in the same window and wraps handler exceptions the
    same way, so a remedy scoped to the emit alone would have left half the
    gap open. This is why the guard wraps the region rather than the call.
    """

    h, seen = _harness()
    await h.next_turn(QUEUED)
    h.hooks.on("before_agent_start", _throwing("handler blew up"))  # type: ignore[arg-type]

    with pytest.raises(AgentHarnessError):
        await h.prompt("live user text")

    assert _texts(h._next_turn_queue) == [QUEUED]
    assert seen == []


# === Where the restored messages go back ==============================


async def test_the_restored_messages_go_back_in_front_of_what_arrived_mid_await() -> None:
    """FIFO across the raise: the drained messages are OLDER than the queue.

    ``next_turn()`` is legal from inside the very handlers these awaits run,
    so at the moment of the raise the queue can already hold a strictly newer
    message. Appending the restored ones would hand the model the user's two
    sentences in the wrong order — the same reason #301 prepends its
    un-attempted tail (ADR-0245 §1).
    """

    h, _seen = _harness()
    await h.next_turn(QUEUED)

    async def enqueue_then_boom(event: Any, _ctx: Any) -> None:
        await h.next_turn(LATE)
        raise RuntimeError("handler blew up after enqueueing")

    h.hooks.on("before_agent_start", enqueue_then_boom)  # type: ignore[arg-type]

    with pytest.raises(AgentHarnessError):
        await h.prompt("live user text")

    assert _texts(h._next_turn_queue) == [QUEUED, LATE]


async def test_the_next_prompt_delivers_what_the_failed_one_drained() -> None:
    """The point of restoring: the message must reach a provider eventually."""

    h, seen = _harness()
    await h.next_turn(QUEUED)

    calls = {"n": 0}

    def boom_once(event: Any, _ctx: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("observer blew up")

    h.hooks.on("queue_update", boom_once)  # type: ignore[arg-type]

    with pytest.raises(AgentHarnessError):
        await h.prompt("first prompt")
    await h.prompt("second prompt")

    delivered = [t for ctx in seen for t in _texts(ctx)]
    assert delivered == [QUEUED, "second prompt"]


async def test_a_cancelled_drain_hands_the_messages_back() -> None:
    """A cancel landing on the emit is the same loss in ``CancelledError``.

    ``abort()`` clears the steer and follow_up queues and deliberately leaves
    ``_next_turn_queue`` alone (``core.py:1584-1585``), so a cancellation that
    ate the drained messages would contradict that design. The guard catches
    ``BaseException`` for this arm.

    The cancel is NOT delivered by ``abort()``, which cancels only
    ``_current_turn_task`` — assigned inside ``_run``, after this window
    (measured: ``.omc/specs/311-next-turn-drain.py`` ARM 7b). It is an
    embedder cancelling its own ``prompt()`` task, as this test does. What the
    assertion below deliberately stops short of — that the restored message
    can then be SENT — was blocked by a separate defect until #321: the
    cancel also left ``_phase`` at ``"turn"``, so every later ``prompt()`` was
    refused. That half is asserted next door, in
    ``tests/test_harness_cancel_gives_the_phase_back.py``
    (``test_the_restore_runs_first_and_once_and_the_restored_message_ships``).
    """

    h, _seen = _harness()
    await h.next_turn(QUEUED)

    entered = asyncio.Event()

    async def block(event: Any, _ctx: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    h.hooks.on("queue_update", block)  # type: ignore[arg-type]

    task = asyncio.ensure_future(h.prompt("live user text"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _texts(h._next_turn_queue) == [QUEUED]


# === The guard's two edges ============================================


async def test_a_successful_prompt_still_empties_the_queue() -> None:
    """The restore must not fire on the happy path.

    A guard that put the messages back unconditionally would re-deliver every
    queued message on every subsequent turn, forever.
    """

    h, seen = _harness()
    await h.next_turn(QUEUED)
    await h.prompt("live user text")

    assert h._next_turn_queue == []
    assert [t for ctx in seen for t in _texts(ctx)] == [QUEUED, "live user text"]

    await h.prompt("second prompt")
    assert _texts(seen[-1]).count(QUEUED) == 1


async def test_a_turn_that_fails_after_the_drain_does_not_requeue() -> None:
    """The far edge: ``_run`` is outside the guard, and must stay outside.

    What this measures is the provider failing: the first thing asserted is
    that ``stream_fn`` was handed the drained message, so this turn is one
    that happened and failed, not a message that never left, and re-queueing
    it would send the user's sentence to the model twice. Widening the guard
    over ``await self._run(...)`` is exactly what reddens this test
    (``.omc/specs/311-sabotage.py`` ARM D).

    It does NOT license the wider claim this docstring used to make — that
    the messages are safe once ``_run`` has them. They are not: ``_run``
    awaits ``self._session.build_context()`` before ``agent_loop`` receives
    the list, and a session raising there loses them (ARM 6 of the drain
    probe). Neither that window nor the user messages a failed turn drops
    from live state is this issue's; both are filed, and ADR-0246 §"Where the
    guard ends" is the record.
    """

    h, seen = _harness(fail_stream=True)
    await h.next_turn(QUEUED)

    with pytest.raises(RuntimeError):
        await h.prompt("live user text")

    assert _texts(seen[0]) == [QUEUED, "live user text"]
    assert h._next_turn_queue == []


# === Who fills this queue =============================================


async def test_both_push_sites_reach_the_guarded_queue() -> None:
    """There are exactly two pushes onto ``_next_turn_queue``.

    ``next_turn()`` (``core.py:3116``) and ``_action_send_message()``
    (``core.py:4139``), the latter reached by ``ExtensionAPI.send_message`` /
    ``send_user_message`` and by the ``ReplacedSessionContext`` handle. Every
    other test in this file uses the first, so without this one a change that
    stopped the extension route from queueing would not redden anything here.
    """

    h, _seen = _harness()
    ctx = h.create_replaced_session_context()
    await ctx.send_message(UserMessage(content=[TextContent(text="via-send-message")]))
    await ctx.send_user_message("via-send-user-message")
    await h.next_turn(QUEUED)

    assert _texts(h._next_turn_queue) == [
        "via-send-message",
        "via-send-user-message",
        QUEUED,
    ]

    h.hooks.on("queue_update", _throwing("observer blew up"))  # type: ignore[arg-type]
    with pytest.raises(AgentHarnessError):
        await h.prompt("live user text")

    assert _texts(h._next_turn_queue) == [
        "via-send-message",
        "via-send-user-message",
        QUEUED,
    ]
