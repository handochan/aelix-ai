"""Issue #321 — a cancelled ``prompt()`` gives the phase back.

``prompt()`` claims the phase synchronously, before its first await, so that a
concurrent caller meets the busy guard at once. Everything up to the call to
``_run`` — the ``input`` hook, the drain's ``queue_update`` emit and
``before_agent_start`` — runs under that claim, and so does ``_run``'s own
``await self._session.build_context()``, which precedes the ``try`` whose
``finally`` normally gives the phase back. The only thing that released the
claim on that stretch was ``prompt()``'s ``except Exception``, and a
``CancelledError`` has been a ``BaseException`` since Python 3.8. So an
embedder that cancelled the ``prompt()`` task there — ``task.cancel()``, an
``asyncio.wait_for`` / ``asyncio.timeout`` expiring, a ``TaskGroup`` sibling
failing — left ``_phase == "turn"`` for good: every later ``prompt()`` raised
``busy``, ``wait_for_idle()`` never returned and ``dispose()`` parked on it.
``abort()`` cannot reach that window (it cancels ``_current_turn_task``, which
does not exist yet), so the harness had to recover by itself or not at all.

Measured on 02f98560 with ``.omc/specs/321-cancel-probe.py`` ARMs 1-6: phase
``'turn'``, idle event clear, next ``prompt()`` → ``AgentHarnessError('busy')``
at every one of the four points; a parked ``wait_for_idle()`` and ``dispose()``
both still waiting after 2 s.

#321's reset was not unconditional: while ``_run``'s ``finally`` set the phase
idle before ``prompt()``'s retry backoff and threshold compaction check, a
SECOND ``prompt()`` could get in there, and a reset keyed on ``phase ==
"turn"`` alone opened the guard under that other call's turn (probe ARM 7). So
#321 gave each call an owner claim and reset only on its own. #334 closed the
window itself — the claim is now held through the whole ``prompt()`` and
released once, unconditionally, in its ``finally`` — so the two-prompt tests
that lived in the second half of this file cannot be built any more; the note
where they were says where each guarantee lives now (ADR-0023, #334 amendment
2026-09-25). What stays here is #321's own point: every exit that is not a
return, at every await before the first run's ``try``, gives the phase back.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
)
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.types import CompactionStartEvent
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

QUEUED = "QUEUED-BY-NEXT-TURN"
# Generous: a bounded wait only runs out on the defect, never on a slow box.
WAIT = 10.0


def _texts(messages: Any) -> list[str]:
    return [
        c.text
        for m in messages
        for c in getattr(m, "content", [])
        if hasattr(c, "text")
    ]


def _ok_stream(seen: list[list[str]]) -> Any:
    """A provider that answers, recording what it was handed."""

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(_texts(context.messages))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    return fn


def _blocker() -> tuple[asyncio.Event, Any]:
    """A handler that parks for ever, and the event it sets on entry.

    The cancel is delivered only once the harness is PROVABLY parked on it —
    never after a sleep — so the point being measured is the point reached.
    """

    entered = asyncio.Event()

    async def block(*_args: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    return entered, block


async def _cancel_parked(task: asyncio.Task[Any], entered: asyncio.Event) -> None:
    """Cancel ``task`` where it is parked, and require the cancel to surface.

    A cancellation must stay a cancellation: the reset re-raises, it does not
    swallow. Every test in this file goes through here, so a reset that ate
    the ``CancelledError`` would redden all of them.
    """

    await asyncio.wait_for(entered.wait(), WAIT)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, WAIT)


def _assert_idle(h: AgentHarness) -> None:
    assert h.phase == "idle", f"the phase was left at {h.phase!r}"
    assert h._idle_event.is_set(), "wait_for_idle() would never return"


# === The four points on the way to a turn =================================


@pytest.mark.parametrize("hook", ["input", "queue_update", "before_agent_start"])
async def test_a_cancel_before_run_gives_the_phase_back(hook: str) -> None:
    """The issue's reproduction, at each await between the claim and ``_run``.

    ``queue_update`` only fires when the drain took something, so that arm
    queues a message first — which is exactly #311's window.
    """

    seen: list[list[str]] = []
    h = AgentHarness(AgentHarnessOptions(stream_fn=_ok_stream(seen)))
    if hook == "queue_update":
        await h.next_turn(QUEUED)
    entered, block = _blocker()
    unsubscribe = h.hooks.on(hook, block)  # type: ignore[call-overload]

    await _cancel_parked(asyncio.ensure_future(h.prompt("cancelled")), entered)

    _assert_idle(h)
    assert seen == [], "the cancelled prompt must not have reached the provider"
    unsubscribe()
    await asyncio.wait_for(h.prompt("later"), WAIT)
    assert seen[-1][-1] == "later"


@pytest.mark.parametrize("fault", ["cancel", "raise"])
async def test_a_cancel_in_build_context_gives_the_phase_back(fault: str) -> None:
    """The fourth point is inside ``_run`` but before its ``try``.

    ``_run`` awaits ``session.build_context()`` before entering the ``try``
    whose ``finally`` runs its close-out, so a cancel there unwinds straight
    into ``prompt()``'s release. Probe ARM 4. The ``raise`` arm is #321's ARM
    21 with the second prompt taken out — the shape #334 leaves buildable.
    """

    seen: list[list[str]] = []
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=_ok_stream(seen)))
    real_build_context = session.build_context
    entered = asyncio.Event()
    park = {"on": True}

    async def build_context() -> Any:
        if park["on"]:
            entered.set()
            if fault == "raise":
                raise RuntimeError("session storage unavailable")
            await asyncio.Event().wait()
        return await real_build_context()

    session.build_context = build_context  # type: ignore[method-assign]

    if fault == "cancel":
        await _cancel_parked(asyncio.ensure_future(h.prompt("cancelled")), entered)
    else:
        with pytest.raises(RuntimeError, match="session storage unavailable"):
            await asyncio.wait_for(h.prompt("raised"), WAIT)

    _assert_idle(h)
    park["on"] = False
    await asyncio.wait_for(h.prompt("later"), WAIT)
    assert seen[-1][-1] == "later"


# === Who was waiting on the phase =========================================


async def test_a_wait_for_idle_parked_before_the_cancel_is_woken_by_it() -> None:
    """Not merely a NEW waiter finding the event set: one already parked on it
    must wake. On the base it was still parked 2 s later (probe ARM 5)."""

    h = AgentHarness(AgentHarnessOptions(stream_fn=_ok_stream([])))
    parked = asyncio.Event()

    class _Parking(asyncio.Event):
        """Signals once a waiter has found the event clear. Nothing yields
        between that and the waiter's own ``await``, so once ``parked`` is seen
        the waiter's future is on the event — where ``not waiter.done()`` alone
        is already true before the waiter has started."""

        async def wait(self) -> Literal[True]:
            if not self.is_set():
                parked.set()
            return await super().wait()

    h._idle_event = _Parking()
    h._idle_event.set()
    entered, block = _blocker()
    h.hooks.on("before_agent_start", block)  # type: ignore[call-overload]
    task = asyncio.ensure_future(h.prompt("cancelled"))
    await asyncio.wait_for(entered.wait(), WAIT)
    waiter = asyncio.ensure_future(h.wait_for_idle())
    await asyncio.wait_for(parked.wait(), WAIT)
    assert not waiter.done(), "the waiter must be parked before the cancel"

    await _cancel_parked(task, entered)

    await asyncio.wait_for(waiter, WAIT)


async def test_dispose_returns_after_the_cancel() -> None:
    """``dispose()`` calls ``abort()`` and then ``wait_for_idle()`` whenever
    the phase is not idle, and ``abort()`` has no turn task to cancel in this
    window — so on the base teardown itself hung (probe ARM 6)."""

    h = AgentHarness(AgentHarnessOptions(stream_fn=_ok_stream([])))
    entered, block = _blocker()
    h.hooks.on("before_agent_start", block)  # type: ignore[call-overload]

    await _cancel_parked(asyncio.ensure_future(h.prompt("cancelled")), entered)

    await asyncio.wait_for(h.dispose(), WAIT)


async def test_the_restore_runs_first_and_once_and_the_restored_message_ships() -> None:
    """#311's clause and the release, both running on the same cancel.

    ``prompt()`` nests them: the inner ``except BaseException`` puts the
    drained ``next_turn`` messages back, re-raises, and only then does the
    outer ``finally`` give the phase back (#321's ``except`` clause until
    #334 made it the one release). The phase goes back through
    ``_idle_event.set()``, so the snapshot taken there shows what a woken
    waiter will find — the message already back, once. Neither awaits here —
    the release's flush returns at once with nothing pending — so no other
    task can see the state in between.

    #311 made the message survive the cancel but could not make it
    deliverable: the phase stayed at "turn" and the next ``prompt()`` was
    refused (its ARM 7). This is the half it stopped short of.
    """

    seen: list[list[str]] = []
    h = AgentHarness(AgentHarnessOptions(stream_fn=_ok_stream(seen)))
    snapshots: list[tuple[str, list[str]]] = []

    class _Recording(asyncio.Event):
        def set(self) -> None:
            snapshots.append((h.phase, _texts(h._next_turn_queue)))
            super().set()

    h._idle_event = _Recording()
    h._idle_event.set()
    snapshots.clear()

    await h.next_turn(QUEUED)
    entered, block = _blocker()
    unsubscribe = h.hooks.on("queue_update", block)  # type: ignore[call-overload]

    await _cancel_parked(asyncio.ensure_future(h.prompt("cancelled")), entered)

    assert snapshots == [("idle", [QUEUED])]
    assert _texts(h._next_turn_queue) == [QUEUED]
    unsubscribe()
    await asyncio.wait_for(h.prompt("later"), WAIT)
    assert seen == [[QUEUED, "later"]]
    assert h._next_turn_queue == []


# === Whose phase it is — closed by #334 ====================================
#
# This section held #321's two-prompt tests (its probe ARMs 7, 12, 13, 15-17,
# 21, 22 and 24): a second ``prompt()`` got in during the first one's tail —
# the retry backoff, the overflow read, the threshold check — because
# ``_run``'s ``finally`` had already set the phase idle, and the tests pinned
# whose phase a cancel or a raise then gave back. #334 holds the claim through
# the whole ``prompt()``, so a second prompt is refused ``busy`` at every one of
# those points and none of those shapes can be built any more (each test's
# setup now fails at its own precondition, not at its guarantee). Where each
# guarantee lives now, in ``test_harness_prompt_holds_the_turn_through_its_tail.py``:
#
# - ARM 7 (cancel in the backoff), 12 (raise in the threshold check), 15/16
#   (#2's own tail), 22 (a third prompt), 24 (``InputHandled`` under a re-run):
#   ``test_a_second_prompt_is_refused_at_every_point_of_the_first_ones_tail``,
#   ``test_cancelling_the_first_prompt_in_its_backoff_gives_the_phase_back``,
#   ``test_the_threshold_compaction_runs_inside_the_claim``; ``InputHandled``
#   alone still idles (``tests/test_input_emit.py``).
# - ARMs 13 and 17 (a re-run that fails to start):
#   ``test_a_rerun_that_fails_to_start_gives_the_phase_back[retry|overflow,
#   raise|cancel]``.
# - ARM 21 (a first run that fails to start): the ``build_context`` test above,
#   both faults.
#
# ADR-0023, #334 amendment 2026-09-25.


# === The sweep: the same defect in compact() ==============================


def _park_on_compaction_start(h: AgentHarness) -> asyncio.Event:
    """An async subscriber that parks on ``compaction_start``.

    Subscribers may be coroutines (``_emit_to_subscribers`` awaits them), so
    an embedder's listener can hold that emit open — every shipped subscriber
    in the tree is synchronous, which is why only an embedder reaches this.
    """

    entered = asyncio.Event()

    async def listener(event: object) -> None:
        if isinstance(event, CompactionStartEvent):
            entered.set()
            await asyncio.Event().wait()

    h.subscribe(listener)
    return entered


async def test_a_cancel_in_compaction_start_gives_the_phase_back() -> None:
    """``compact()`` flipped to "compaction" and awaited the start emit BEFORE
    entering the ``try`` whose ``finally`` gives the phase back, so a cancel
    there left it at "compaction" for good (probe ARM 8). pi has no such
    window — its ``_emit`` is synchronous (``agent-session.ts``).
    """

    seen: list[list[str]] = []
    h = AgentHarness(
        AgentHarnessOptions(
            session=Session(MemorySessionStorage()), stream_fn=_ok_stream(seen)
        )
    )
    entered = _park_on_compaction_start(h)

    await _cancel_parked(asyncio.ensure_future(h.compact()), entered)

    _assert_idle(h)
    h._listeners.clear()
    await asyncio.wait_for(h.prompt("later"), WAIT)
    assert seen[-1][-1] == "later"


async def test_a_prompt_cancelled_in_its_threshold_compaction_leaves_the_harness_idle() -> None:
    """The same window reached through ``prompt()``: the threshold
    auto-compaction runs ``compact()`` inside the prompt, so cancelling the
    PROMPT there left the phase at "compaction" (probe ARM 9). Since #334 that
    compaction nests under the prompt's claim, and the prompt's release gives
    the phase back whatever the compaction was doing when the cancel landed.
    """

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="big")],
                stop_reason="end_turn",
                usage={"total_tokens": 5_000},
            )
        )

    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=fn)
    )
    h._state.model = SimpleNamespace(context_window=20_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    entered = _park_on_compaction_start(h)

    await _cancel_parked(asyncio.ensure_future(h.prompt("live")), entered)

    _assert_idle(h)
