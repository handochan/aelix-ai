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

The reset is not unconditional, and the second half of this file is why.
``_run``'s ``finally`` sets the phase idle before ``prompt()`` runs its retry
backoff and its threshold compaction check, so a SECOND ``prompt()`` can pass
the guard and be mid-turn by the time the first one is cancelled or raises. A
reset keyed on ``phase == "turn"`` alone then opens the guard under that other
turn — measured with exactly that one-word widening (probe ARM 7 on a patched
tree: phase ``'idle'`` while #2 is mid-turn, a third ``prompt()`` ACCEPTED).
The base has the same hole on its exception path already (ARM 12). So each
``prompt()`` takes a claim with every flip to "turn" it makes — at its entry,
and at ``_run``'s own flip, which its first run and each re-run reach with the
call's ``owner`` — and gives the phase back only while the claim is still its
own. Taking it at entry alone was not enough: a re-run sets "turn" again, and a
claim left naming the other call let that call's cancel or raise open the guard
under the re-run, and a failing re-run wedge the harness (probe ARMs 15-17) —
measured on the first version of this change, which took the claim at entry
only, released it in a ``finally`` and also reset on ``claim is None``. Taking
it at entry and before each re-run was not enough either: the FIRST run's flip
comes three awaits after the entry, another call's re-run can take the claim in
between, and then that first run failing before its ``try`` wedged the harness,
a raise included (probe ARMs 21r/21c) — the second version of this change,
found by an independent cross-review. Both versions were reviewed and never
merged; "the first version" and "the second version" below mean them.

The window in which a second ``prompt()`` can get in at all is its own defect
and is NOT closed here; see the ADR-0023 amendment of 2026-09-24.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from aelix_agent_core.harness import core as core_mod
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.harness.hooks import InputHandled
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.types import AutoRetryStartEvent, CompactionStartEvent
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
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


async def test_a_cancel_in_build_context_gives_the_phase_back() -> None:
    """The fourth point is inside ``_run`` but before its ``try``.

    ``_run`` flips the phase and then awaits ``session.build_context()``
    before entering the ``try`` whose ``finally`` would give it back, so a
    cancel there unwinds straight into ``prompt()``'s clause. Probe ARM 4.
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
            await asyncio.Event().wait()
        return await real_build_context()

    session.build_context = build_context  # type: ignore[method-assign]

    await _cancel_parked(asyncio.ensure_future(h.prompt("cancelled")), entered)

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
    """#311's clause and this one, both running on the same cancel.

    ``prompt()`` nests them: the inner ``except BaseException`` puts the
    drained ``next_turn`` messages back, re-raises, and only then does the
    outer clause give the phase back. The phase goes back through
    ``_idle_event.set()``, so the snapshot taken there shows what a woken
    waiter will find — the message already back, once. Neither clause awaits,
    so no other task can see the state in between.

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


# === Whose phase it is ====================================================


async def test_a_cancelled_prompt_leaves_a_turn_another_prompt_owns_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE TRAP. Green on the base; red on the obvious fix.

    ``_run``'s ``finally`` has already set the phase idle when ``prompt()``
    sleeps in its retry backoff, so a second ``prompt()`` passes the busy
    guard and owns a turn of its own. Cancel the first one now and a reset
    keyed on ``phase == "turn"`` resets the SECOND call's phase — measured
    with ``except Exception`` widened to ``except BaseException`` and nothing
    else: phase ``'idle'`` under #2's live turn and a third ``prompt()``
    ACCEPTED on top of it (probe ARM 7). The base refused the third, because
    it never reset on a cancel at all; this must keep doing that.
    """

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 60_000)
    calls = {"n": 0}
    second_mid_turn = asyncio.Event()
    release_second = asyncio.Event()

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        calls["n"] += 1
        n = calls["n"]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if n == 1:
            failed = AssistantMessage(
                content=[], stop_reason="error", error_message="rate limit exceeded"
            )
            yield AssistantErrorEvent(
                reason="error", error=failed, error_message="rate limit exceeded"
            )
            return
        if n == 2:
            second_mid_turn.set()
            await release_second.wait()
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=f"answer {n}")], stop_reason="end_turn"
            )
        )

    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=fn)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    in_backoff = asyncio.Event()

    def watch(event: object) -> None:
        if isinstance(event, AutoRetryStartEvent):
            in_backoff.set()

    h.subscribe(watch)
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(in_backoff.wait(), WAIT)
    assert h.phase == "idle", "precondition: the backoff runs with the phase idle"
    second = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(second_mid_turn.wait(), WAIT)

    await _cancel_parked(first, in_backoff)

    assert h.phase == "turn", "the cancel reset a turn the second prompt owns"
    with pytest.raises(AgentHarnessError) as refused:
        await asyncio.wait_for(h.prompt("third"), WAIT)
    assert refused.value.code == "busy"
    release_second.set()
    await asyncio.wait_for(second, WAIT)
    _assert_idle(h)


async def test_a_raising_prompt_leaves_a_turn_another_prompt_owns_alone() -> None:
    """The trap's exception arm, which the BASE already fell into.

    The window is the whole tail after ``_run``, not only the backoff: here
    the second ``prompt()`` gets in while the first is reading the branch for
    its threshold compaction check. The first one's ``compact()`` then finds
    the harness busy and raises, and the base's ``except Exception`` reset
    the phase from under the second call's live turn — a third ``prompt()``
    ACCEPTED (probe ARM 12, on 02f98560 itself).
    """

    gap_entered = asyncio.Event()
    release_gap = asyncio.Event()
    second_mid_turn = asyncio.Event()
    release_second = asyncio.Event()
    calls = {"n": 0}

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        calls["n"] += 1
        n = calls["n"]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if n == 2:
            second_mid_turn.set()
            await release_second.wait()
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=f"answer {n}")],
                stop_reason="end_turn",
                # Over the threshold for the model below, so the first call's
                # tail runs the compaction check.
                usage={"total_tokens": 5_000} if n == 1 else None,
            )
        )

    session = Session(MemorySessionStorage())
    real_get_branch = session.get_branch
    holder: dict[str, AgentHarness] = {}

    async def get_branch(*args: Any, **kwargs: Any) -> Any:
        # ``build_context()`` reads the branch too, from inside ``_run`` with
        # the phase at "turn"; the read to park is the first one after
        # ``_run``'s ``finally`` has set it idle — the threshold check.
        if holder["h"].phase == "idle" and not gap_entered.is_set():
            gap_entered.set()
            await release_gap.wait()
        return await real_get_branch(*args, **kwargs)

    session.get_branch = get_branch  # type: ignore[method-assign]
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=fn))
    holder["h"] = h
    h._state.model = SimpleNamespace(context_window=20_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True

    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(gap_entered.wait(), WAIT)
    second = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(second_mid_turn.wait(), WAIT)
    release_gap.set()
    with pytest.raises(AgentHarnessError) as raised:
        await asyncio.wait_for(first, WAIT)
    assert raised.value.code == "busy"

    assert h.phase == "turn", "the raise reset a turn the second prompt owns"
    with pytest.raises(AgentHarnessError) as refused:
        await asyncio.wait_for(h.prompt("third"), WAIT)
    assert refused.value.code == "busy"
    release_second.set()
    await asyncio.wait_for(second, WAIT)
    _assert_idle(h)


_OVERFLOW = "prompt is too long: 213462 tokens > 200000 maximum"


def _model_20k() -> Any:
    # Small enough that a 5k-token answer trips the threshold compaction check.
    return SimpleNamespace(context_window=20_000)


class _Provider:
    """A provider scripted per call: ``"retry"`` fails retryably, ``"overflow"``
    fails with a context overflow, ``"big"`` answers over the 20k model's
    compaction threshold, ``"park"`` answers only once ``release[n]`` is set,
    and a call with no entry answers at once."""

    def __init__(self, script: dict[int, str]) -> None:
        self.script = script
        self.calls = 0
        self.parked = {n: asyncio.Event() for n in script}
        self.release = {n: asyncio.Event() for n in script}

    async def __call__(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        self.calls += 1
        n = self.calls
        kind = self.script.get(n, "ok")
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if kind in ("retry", "overflow"):
            text = "rate limit exceeded" if kind == "retry" else _OVERFLOW
            failed = AssistantMessage(content=[], stop_reason="error", error_message=text)
            yield AssistantErrorEvent(reason="error", error=failed, error_message=text)
            return
        if kind == "park":
            self.parked[n].set()
            await self.release[n].wait()
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=f"answer {n}")],
                stop_reason="end_turn",
                usage={"total_tokens": 5_000} if kind == "big" else None,
            )
        )


class _Backoff:
    """The retry backoff, made a sequence instead of a race.

    The real one is a timed sleep. This one installs itself as
    ``_handle_retryable_error``; its i-th call pops the error assistant as the
    real one does, sets ``entered[i]``, parks until ``release[i]`` and then
    retries (``True``) or gives the retry up (``False`` — what
    ``abort_retry()`` or an exhausted budget returns).
    """

    def __init__(self, h: AgentHarness, verdicts: list[bool]) -> None:
        self.h = h
        self.verdicts = verdicts
        self.entered = [asyncio.Event() for _ in verdicts]
        self.release = [asyncio.Event() for _ in verdicts]
        self.n = 0
        h._handle_retryable_error = self  # type: ignore[method-assign]

    async def __call__(self, message: Any) -> bool:
        i = self.n
        self.n += 1
        messages = self.h._state.messages
        if messages and isinstance(messages[-1], AssistantMessage):
            messages.pop()
        self.entered[i].set()
        await self.release[i].wait()
        return self.verdicts[i]


def _fault_the_next_build_context(
    session: Session, fault: str
) -> tuple[Callable[[], None], asyncio.Event]:
    """Arm the NEXT ``build_context()`` — a re-run's — to raise or to park.

    It runs inside ``_run`` after the phase flip and before ``_run``'s ``try``,
    so a raise or a cancel there unwinds straight into ``prompt()``'s clause.
    """

    real_build_context = session.build_context
    armed = {"on": False}
    reached = asyncio.Event()

    async def build_context() -> Any:
        if armed["on"]:
            armed["on"] = False
            reached.set()
            if fault == "raise":
                raise RuntimeError("session storage unavailable")
            await asyncio.Event().wait()
        return await real_build_context()

    session.build_context = build_context  # type: ignore[method-assign]

    def arm() -> None:
        armed["on"] = True

    return arm, reached


def _park_the_first_idle_branch_read(
    session: Session, h: AgentHarness
) -> tuple[asyncio.Event, asyncio.Event]:
    """Park the first ``get_branch()`` made with the phase idle — a tail's read.

    ``build_context()`` reads the branch too, from inside ``_run`` with the
    phase at "turn"; the reads made with it idle are a tail's: overflow
    recovery's staleness check and the threshold compaction check.
    """

    real_get_branch = session.get_branch
    entered = asyncio.Event()
    release = asyncio.Event()

    async def get_branch(*args: Any, **kwargs: Any) -> Any:
        if h.phase == "idle" and not entered.is_set():
            entered.set()
            await release.wait()
        return await real_get_branch(*args, **kwargs)

    session.get_branch = get_branch  # type: ignore[method-assign]
    return entered, release


async def test_a_second_prompt_cancelled_in_its_tail_leaves_the_first_ones_rerun_alone() -> None:
    """The claim follows the phase into a re-run — the cancel arm.

    #2 gets in during #1's backoff, fails retryably too and waits in a backoff
    of its own; #1's backoff ends first and its re-run sets "turn" again. That
    turn is the re-run's — #2's ended when its ``_run`` returned. Cancel #2 now:
    with the claim taken only at ``prompt()``'s entry it still named #2, so #2
    gave back #1's live re-run and a third ``prompt()`` reached the provider on
    top of it (the first version; probe ARM 15 with the real backoff
    sleeping). 02f98560 never reset on a cancel at all, so this is green
    there.
    """

    provider = _Provider({1: "retry", 2: "retry", 3: "park"})
    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=provider)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = _Backoff(h, [True, True])

    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), WAIT)
    second = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(backoff.entered[1].wait(), WAIT)
    backoff.release[0].set()
    await asyncio.wait_for(provider.parked[3].wait(), WAIT)

    await _cancel_parked(second, backoff.entered[1])

    assert h.phase == "turn", "the cancel gave back the first prompt's live re-run"
    with pytest.raises(AgentHarnessError) as refused:
        await asyncio.wait_for(h.prompt("third"), WAIT)
    assert refused.value.code == "busy"
    provider.release[3].set()
    await asyncio.wait_for(first, WAIT)
    _assert_idle(h)


async def test_a_second_prompt_raising_in_its_tail_leaves_the_first_ones_rerun_alone() -> None:
    """The claim follows the phase into a re-run — the raise arm, and ARM 12
    turned around.

    #2 gets in during #1's backoff, answers over the threshold and reads the
    branch for its compaction check while #1's re-run starts and goes
    mid-turn; #2's ``compact()`` then finds the phase at "turn" and raises
    ``busy``. The turn is #1's re-run's. 02f98560's ``except Exception`` reset
    it on any raise and the first version's claim still named #2, so both let
    a third ``prompt()`` in on top of it (probe ARM 16).
    """

    provider = _Provider({1: "retry", 2: "big", 3: "park"})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.model = _model_20k()  # type: ignore[assignment]
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = True
    backoff = _Backoff(h, [True])
    gap_entered, release_gap = _park_the_first_idle_branch_read(session, h)

    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), WAIT)
    second = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(gap_entered.wait(), WAIT)
    backoff.release[0].set()
    await asyncio.wait_for(provider.parked[3].wait(), WAIT)
    release_gap.set()
    with pytest.raises(AgentHarnessError) as raised:
        await asyncio.wait_for(second, WAIT)
    assert raised.value.code == "busy"

    assert h.phase == "turn", "the raise gave back the first prompt's live re-run"
    with pytest.raises(AgentHarnessError) as refused:
        await asyncio.wait_for(h.prompt("third"), WAIT)
    assert refused.value.code == "busy"
    provider.release[3].set()
    await asyncio.wait_for(first, WAIT)
    _assert_idle(h)


@pytest.mark.parametrize("fault", ["raise", "cancel"])
async def test_a_rerun_that_fails_to_start_gives_the_phase_back_while_a_second_prompt_waits(
    fault: str,
) -> None:
    """The other edge of the same claim: the re-run's turn is the re-run's.

    #2 gets in during #1's backoff and is still waiting in its own backoff when
    #1's re-run sets "turn" and fails in ``build_context()``. No other turn is
    in flight, so #1 must give the phase back. With the claim left naming #2,
    #1 did not, #2 then gave its retry up and returned normally — and the
    harness stayed at "turn" for good, ``wait_for_idle()`` parked and every
    ``prompt()`` refused (the first version, probe ARMs 17r/17c). 02f98560's
    ``except Exception`` did reset the raise; it wedged the cancel as it
    wedged every cancel.
    """

    provider = _Provider({1: "retry", 2: "retry"})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = _Backoff(h, [True, False])
    arm, reached = _fault_the_next_build_context(session, fault)

    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), WAIT)
    second = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(backoff.entered[1].wait(), WAIT)
    arm()
    backoff.release[0].set()
    if fault == "cancel":
        await _cancel_parked(first, reached)
    else:
        with pytest.raises(RuntimeError, match="session storage unavailable"):
            await asyncio.wait_for(first, WAIT)

    _assert_idle(h)
    backoff.release[1].set()
    await asyncio.wait_for(second, WAIT)
    _assert_idle(h)
    await asyncio.wait_for(h.prompt("third"), WAIT)


@pytest.mark.parametrize("fault", ["cancel", "raise"])
@pytest.mark.parametrize("site", ["retry", "overflow"])
async def test_a_rerun_gives_the_phase_back_after_a_second_prompt_ran_in_the_gap(
    site: str, fault: str
) -> None:
    """Both re-run sites take the claim, not only the retry loop's.

    A second ``prompt()`` runs start to finish inside the first one's tail —
    its retry backoff, or the branch read of its overflow recovery — and takes
    the claim with it. The first one's re-run then sets "turn" itself and is
    cancelled (or raises) in ``build_context()``. Nobody else holds the phase,
    so it is the re-run's to give back: a claim still naming the finished
    second call would wedge the cancel for good and turn the raise, which
    02f98560's ``except Exception`` did reset, into a regression (probe ARMs
    13c / 13r, the retry site). The overflow site's re-run is a ``_run`` call
    of its own, passing ``owner`` itself, so it is pinned by its own arm.
    """

    provider = _Provider({1: site})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.model = _model_20k()  # type: ignore[assignment]
    arm, reached = _fault_the_next_build_context(session, fault)
    if site == "retry":
        h._state.auto_retry_enabled = True
        h._state.auto_compaction_enabled = False
        backoff = _Backoff(h, [True])
        gap_entered, release_gap = backoff.entered[0], backoff.release[0]
    else:
        h._state.auto_retry_enabled = False
        h._state.auto_compaction_enabled = True
        gap_entered, release_gap = _park_the_first_idle_branch_read(session, h)

        async def compact(*_args: Any, **_kwargs: Any) -> None:
            return None

        h.compact = compact  # type: ignore[method-assign]

    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(gap_entered.wait(), WAIT)
    await asyncio.wait_for(h.prompt("second"), WAIT)
    arm()
    release_gap.set()
    if fault == "cancel":
        await _cancel_parked(first, reached)
    else:
        with pytest.raises(RuntimeError, match="session storage unavailable"):
            await asyncio.wait_for(first, WAIT)

    _assert_idle(h)
    await asyncio.wait_for(h.prompt("third"), WAIT)


def _park_the_hook_of(
    h: AgentHarness, hook: str, text: str, result: Any = None
) -> tuple[asyncio.Event, asyncio.Event]:
    """Park ``hook`` for the ``prompt()`` of ``text`` only; every other prompt
    passes straight through. Released, the handler answers ``result`` — ``None``
    is no opinion. ``before_agent_start`` carries the text as ``prompt``,
    ``input`` as ``text``."""

    field = "prompt" if hook == "before_agent_start" else "text"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(event: Any, *_args: Any) -> Any:
        if getattr(event, field, None) != text:
            return None
        entered.set()
        await release.wait()
        return result

    h.hooks.on(hook, handler)  # type: ignore[call-overload]
    return entered, release


@pytest.mark.parametrize("fault", ["raise", "cancel"])
async def test_a_first_run_that_fails_to_start_gives_the_phase_back_after_a_rerun_ran_in_its_hooks(
    fault: str,
) -> None:
    """The claim goes with the FIRST run's flip too, not only a re-run's.

    #2 gets in during #1's backoff and parks in ``before_agent_start``; #1's
    re-run runs start to finish meanwhile and #1 returns, its claim still
    standing. Then #2's first ``_run`` sets "turn" and fails in
    ``build_context()`` with no other turn in flight — the phase is #2's to give
    back. The second version of this change took the claim at ``prompt()``'s
    entry and before each re-run, three awaits away from the first run's own
    flip, so the claim still named #1 and #2 gave nothing back: "turn" for
    good, ``wait_for_idle()`` and ``dispose()`` parked, every ``prompt()``
    refused — a raise included, which 02f98560's ``except Exception`` did
    reset (probe ARMs 21r / 21c; found by an independent cross-review).
    02f98560 wedged the cancel as it wedged every cancel.
    """

    provider = _Provider({1: "retry"})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = _Backoff(h, [True])
    arm, reached = _fault_the_next_build_context(session, fault)
    in_hook, release_hook = _park_the_hook_of(h, "before_agent_start", "second")

    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), WAIT)
    second = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(in_hook.wait(), WAIT)
    backoff.release[0].set()
    await asyncio.wait_for(first, WAIT)
    assert h.phase == "idle", "precondition: #1's re-run ended while #2 was in its hook"
    arm()
    release_hook.set()
    if fault == "cancel":
        await _cancel_parked(second, reached)
    else:
        with pytest.raises(RuntimeError, match="session storage unavailable"):
            await asyncio.wait_for(second, WAIT)

    _assert_idle(h)
    await asyncio.wait_for(h.prompt("third"), WAIT)


async def test_a_cancel_in_a_third_prompts_hook_leaves_the_second_ones_first_run_alone() -> None:
    """The first run's claim, the other way round.

    #1's re-run runs start to finish while #2 waits in ``before_agent_start``,
    so the phase is idle and a third ``prompt()`` gets in and parks in its
    ``input`` hook, its entry taking the claim. #2's first ``_run`` then sets
    "turn" and goes mid-turn: the turn is #2's. Cancel #3 now: in the second
    version #2's first run had not taken the claim, which still named #3, so
    #3 gave back #2's live turn and a fourth ``prompt()`` got in on top of it
    (probe ARM 22). 02f98560 never reset on a cancel, so this is green there.
    """

    provider = _Provider({1: "retry", 3: "park"})
    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=provider)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = _Backoff(h, [True])
    second_in_hook, release_second = _park_the_hook_of(h, "before_agent_start", "second")
    third_in_hook, _ = _park_the_hook_of(h, "input", "third")

    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), WAIT)
    second = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(second_in_hook.wait(), WAIT)
    backoff.release[0].set()
    await asyncio.wait_for(first, WAIT)
    third = asyncio.ensure_future(h.prompt("third"))
    await asyncio.wait_for(third_in_hook.wait(), WAIT)
    release_second.set()
    await asyncio.wait_for(provider.parked[3].wait(), WAIT)

    await _cancel_parked(third, third_in_hook)

    assert h.phase == "turn", "the cancel gave back the second prompt's live turn"
    with pytest.raises(AgentHarnessError) as refused:
        await asyncio.wait_for(h.prompt("fourth"), WAIT)
    assert refused.value.code == "busy"
    provider.release[3].set()
    await asyncio.wait_for(second, WAIT)
    _assert_idle(h)


async def test_an_input_hook_that_handles_its_prompt_leaves_a_rerun_it_does_not_own_alone() -> None:
    """The ``InputHandled`` return gives the phase back by the same rule.

    #2 gets in during #1's backoff and is still in its ``input`` hook when
    #1's re-run sets "turn" and goes mid-turn; the hook then answers
    ``InputHandled`` and #2 returns ``[]``. The turn is #1's re-run's. That
    return reset the phase with no owner check — on 02f98560 and in both
    earlier versions of this change — and a third ``prompt()`` got in on top of
    the re-run (probe ARM 24, found by the independent cross-review). An
    ``InputHandled`` with nothing else in flight still gives the phase back:
    ``tests/test_input_emit.py`` asserts it.
    """

    provider = _Provider({1: "retry", 2: "park"})
    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=provider)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = _Backoff(h, [True])
    in_hook, release_hook = _park_the_hook_of(h, "input", "second", result=InputHandled())

    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), WAIT)
    second = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(in_hook.wait(), WAIT)
    backoff.release[0].set()
    await asyncio.wait_for(provider.parked[2].wait(), WAIT)
    release_hook.set()
    assert await asyncio.wait_for(second, WAIT) == []

    assert h.phase == "turn", "the handled prompt gave back the first prompt's live re-run"
    with pytest.raises(AgentHarnessError) as refused:
        await asyncio.wait_for(h.prompt("third"), WAIT)
    assert refused.value.code == "busy"
    provider.release[2].set()
    await asyncio.wait_for(first, WAIT)
    _assert_idle(h)


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
    PROMPT there left the phase at "compaction" — which ``prompt()``'s own
    clause does not own and must not touch (probe ARM 9).
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
