"""Issue #321 — does a cancellation leave the harness busy for good?

Run from inside a tree with that tree's interpreter, so the ``core.py`` measured
is the one printed on the first line:

    cd <tree> && uv run --no-sync python <this file> [ARM ...]

Every wait is bounded, so a wedged harness prints ``TIMEOUT`` instead of
hanging the probe. The cancel is always delivered the way an embedder delivers
it — ``task.cancel()`` on the task running the harness coroutine — at a point the
probe has proved the coroutine is parked on (an ``asyncio.Event`` the blocking
handler sets on entry), never after a sleep.

ARMs
  1  cancel parked in an ``input`` hook
  2  cancel parked in the ``queue_update`` drain emit (#311's window) — and can
     the restored message then ship?
  3  cancel parked in a ``before_agent_start`` handler
  4  cancel parked in ``session.build_context()`` — inside ``_run`` but before
     its ``try``, so ``_run``'s own ``finally`` does not cover it
  4q the same with a ``next_turn`` message queued first: the drain has already
     handed it to ``_run``, past the region #311's clause guards (#320's window)
  5  a ``wait_for_idle()`` parked BEFORE the cancel: does it wake?
  6  ``dispose()`` after the cancel: does it return?
  7  THE TRAP. prompt #1 is in its retry backoff (``_run``'s ``finally`` already
     set the phase idle), prompt #2 passes the busy guard and is mid-turn, then
     prompt #1 is cancelled. Whose phase is it afterwards?
  8  sweep: ``compact()`` cancelled in its ``compaction_start`` subscriber emit
  9  sweep: ``prompt()`` cancelled in the threshold auto-compaction's
     ``compaction_start`` emit (the same sibling, reached through prompt)
  10 sweep: ``navigate_tree()`` cancelled in its ``session_before_tree`` hook
  11 side observation: what a cancel in the retry backoff leaves in the retry
     bookkeeping (``_retry_attempt``, ``auto_retry_end``)
  12 THE TRAP, exception arm: prompt #1 is past ``_run`` in its threshold
     auto-compaction check, prompt #2 slips in and is mid-turn, then #1's
     ``compact()`` raises ``busy`` — whose phase does #1's reset clear?
  13c/13r  the re-run's own turn: #2 slips into #1's backoff and FINISHES, then
     #1's own re-run sets "turn" and is cancelled (13c) or raises (13r) in
     ``build_context()``. Nobody else holds the phase now, so it must still be
     given back — by the claim #1's re-run takes (the first version of this
     change reached the same end through an ``or claim is None`` arm, now
     gone).
  14 the ARM 3 cancel delivered by ``asyncio.wait_for`` / ``asyncio.timeout``
     running out, or by a failing ``TaskGroup`` sibling, rather than by
     ``task.cancel()`` — the embedder patterns the CHANGELOG names. The hook never
     returns, so the timeout always fires; 0.2 s only bounds the probe.
  15 the claim vs a re-run, cancel arm, with the REAL backoff (base delay cut to
     1.5 s): #1 and #2 both fail retryably, #1's backoff (attempt 1) ends before
     #2's (attempt 2, the counter is shared), #1's re-run parks mid-turn, #2 is
     cancelled in its own backoff. The turn standing is #1's re-run's.
  16 the claim vs a re-run, raise arm (ARM 12 turned around): #2's threshold
     ``compact()`` raises ``busy`` while #1's re-run is mid-turn.
  17r/17c  #1's re-run raises / is cancelled in ``build_context()`` while #2
     waits in its own backoff; #2 then gives its retry up and returns normally.
  18 OVERLAP: #1's backoff ends while #2 is MID-TURN, so #1's re-run and #2's
     turn run at once (the idle tail's own defect); #1's re-run is cancelled in
     ``build_context()`` with #2's turn still live. 18r: the same, raising.
     18h / 18h-input: the same cancel with #2 still parked in its
     ``before_agent_start`` / ``input`` hook, not yet at the provider.
  19 OVERLAP, no fault at all: the same two turns; #2's ends first.
  20 OVERLAP, the other way round: #2 is parked in ``before_agent_start`` when
     #1's re-run goes mid-turn, and #2 is cancelled there.
  21r/21c  the FIRST run's flip (cross-review round): #2 gets in during #1's
     backoff and parks in ``before_agent_start``; #1's re-run runs and #1
     returns meanwhile; then #2's first ``build_context()`` raises (21r) or is
     cancelled (21c). Nothing else is in flight. 21r-input: #2 parked in its
     ``input`` hook instead; 21r-real / 21c-real: the real timed backoff (base
     delay cut to 300 ms) instead of the gate.
  22 the first run's flip, the other way: as 21, but once #1 has returned a
     third ``prompt()`` gets in and parks in its ``input`` hook; #2's first run
     goes mid-turn; #3 is cancelled. The turn standing is #2's.
  23c/23r  OVERLAP, the first run flipping last: #2 (parked in
     ``before_agent_start``) is released while #1's re-run is mid-turn, and #2's
     first ``build_context()`` is cancelled (23c) or raises (23r).
  24 the ``InputHandled`` return: #2 is in its ``input`` hook when #1's re-run
     goes mid-turn; the hook then answers ``InputHandled`` and #2 returns ``[]``.

  ARMs 15-24 use a gated backoff (a replaced ``_handle_retryable_error``) except
  15, 21r-real and 21c-real, so their order is a sequence, not a race; each
  third (or fourth) ``prompt()`` that is let in is cancelled again once it has
  provably reached the provider, or runs to completion when the provider answers
  it at once. ARMs 21-24 are the independent cross-review's probe, ported (its
  X1r/X1c = 21r/21c, X2 = 22, X3c/X3r = 23c/23r, X4 = 24); 18r, 18h and
  18h-input were added in the same round.

Measured 2026-09-24 in the cross-review round on five trees, each run whole with
the loaded ``core.py`` printed on the first line: BASE, ``a9805d03`` — whose
``packages/`` is the same git tree as ``02f98560``'s, the base every other
record of this change names — run from a throwaway worktree of it; WIDEN, BASE
with ``except Exception`` -> ``except BaseException`` and nothing else; V1, the
first version of this change — the claim taken at ``prompt()``'s entry only,
released in a ``finally``, and an ``or claim is None`` arm; V2, the second
version — the claim taken at entry and right before each re-run, but not at the
first run's flip — run from a throwaway worktree of it; and NOW, the claim taken
in ``_run``'s flip itself. WIDEN and V1 were scratch copies of aelix-agent-core's
``src`` with that ``core.py`` first on ``PYTHONPATH``. V1 and V2 were both
reviewed and never merged. Every arm the earlier rounds recorded (1-20, without
18r and 18h) gave on BASE, WIDEN, V1 and V2 what they recorded for that tree (V2
was then called FIX, V1 LANE)::

    ARM 1-4   BASE  phase='turn' idle_event=False, next prompt(): busy
              WIDEN, V1, V2, NOW  phase='idle', next prompt(): ACCEPTED
    ARM 2     WIDEN, V1, V2, NOW  stream_fn saw [['QUEUED-BY-NEXT-TURN', 'later']], queue after []
    ARM 4q    BASE  phase='turn', queue [], next prompt(): busy
              WIDEN, V1, V2, NOW  phase='idle', queue [], stream_fn saw [['later']] — the
              drained message is lost, as #320 reports; the phase coming back is all this fixes
    ARM 5/6   BASE  waiter / dispose(): TIMEOUT after 2.0s    WIDEN, V1, V2, NOW  woke / returned
    ARM 7     BASE, V1, V2, NOW  #1 cancelled, #2 mid-turn: phase='turn', #3 busy
              WIDEN  phase='idle' idle_event=True, #3 ACCEPTED
    ARM 8/9   BASE, WIDEN  phase='compaction'                 V1, V2, NOW  phase='idle'
    ARM 10    all five  phase='idle' (navigate_tree is clean)
    ARM 11    all five  _retry_attempt=1, events ['AutoRetryStartEvent']
    ARM 12    BASE, WIDEN  #1 raised busy, phase='idle' under #2's turn, #3 ACCEPTED
              V1, V2, NOW  phase='turn', #3 busy
    ARM 13c   BASE  phase='turn', #3 busy        WIDEN, V1, V2, NOW  idle, #3 ACCEPTED
    ARM 13r   all five  idle, #3 ACCEPTED
    ARM 14    BASE  TimeoutError / the sibling's RuntimeError, phase='turn', busy — all three
              WIDEN, V1, V2, NOW  idle, next prompt(): ACCEPTED — all three
    ARM 15    BASE, V2, NOW  #2 cancelled: phase='turn', #3 busy
              WIDEN, V1  phase='idle' idle_event=True, #3 ACCEPTED (reached the provider)
    ARM 16    BASE, WIDEN, V1  #2 raised busy: phase='idle', #3 ACCEPTED    V2, NOW  phase='turn', #3 busy
    ARM 17r   BASE, WIDEN, V2, NOW  idle after #1 and after #2, wait_for_idle() returned, next ACCEPTED
              V1  phase='turn' after both, wait_for_idle() TIMEOUT after 2.0s, next busy
    ARM 17c   BASE, V1  phase='turn' after both, wait_for_idle() TIMEOUT after 2.0s, next busy
              WIDEN, V2, NOW  idle, returned, ACCEPTED
    ARM 18    BASE, V1  #1 cancelled under #2's live turn: phase='turn', #3 busy
              WIDEN, V2, NOW  phase='idle' idle_event=True, #3 ACCEPTED
    ARM 18r   BASE, WIDEN, V2, NOW  #1 raised under #2's live turn: phase='idle', #3 ACCEPTED
              V1  phase='turn', #3 busy
    ARM 18h   BASE, V1  #1 cancelled, #2 still in its hook: phase='turn', #3 busy
    (-input)  WIDEN, V2, NOW  phase='idle' idle_event=True, #3 ACCEPTED — both hooks
    ARM 19    all five  #2 returned, #1's re-run live: phase='idle' idle_event=True, #3 ACCEPTED
    ARM 20    BASE, V2, NOW  #2 cancelled under #1's live re-run: phase='turn', #3 busy
              WIDEN, V1  phase='idle' idle_event=True, #3 ACCEPTED
    ARM 21r   BASE, WIDEN, V1, NOW  after #2: idle, wait_for_idle() returned, next ACCEPTED,
    (-input,    dispose() returned — all three
     -real)   V2  after #2: phase='turn' idle_event=False, wait_for_idle() TIMEOUT after 2.0s,
                next busy, dispose() TIMEOUT after 2.0s — all three
    ARM 21c   BASE, V2  after #2: phase='turn', wait_for_idle() and dispose() TIMEOUT, next busy
    (-real)   WIDEN, V1, NOW  idle, returned, ACCEPTED, returned — both
    ARM 22    BASE, NOW  #3 cancelled, #2's first run mid-turn: phase='turn', #4 busy
              WIDEN, V1, V2  phase='idle' idle_event=True, #4 ACCEPTED (ran to completion)
    ARM 23c   BASE, V2  #2 cancelled, #1's re-run mid-turn: phase='turn', #3 busy
              WIDEN, V1, NOW  phase='idle' idle_event=True, #3 ACCEPTED (reached the provider)
    ARM 23r   BASE, WIDEN, V1, NOW  #2 raised, #1's re-run mid-turn: phase='idle', #3 ACCEPTED
              V2  phase='turn', #3 busy
    ARM 24    BASE, WIDEN, V1, V2  #2 returned [], #1's re-run mid-turn: phase='idle', #3 ACCEPTED
              NOW  phase='turn' idle_event=False, #3 busy

ARMs 18-20 and 23 are the idle tail's two-turns-at-once state, where one claim
cannot name both turns: it names the call whose flip came last. Against BASE,
NOW loses 18 (with 18h and 18h-input) and 23c — the two regressions of this
change, both a cancel landing on the call that flipped last, before its ``try``,
while the other call's turn is live — and keeps 20 and 22 (24 it does better
than BASE). V2 lost 18, 21r and 22 and kept 23c; V1 lost 15, 17r, 20, 22 and 23c
and kept 18. In 19 every tree opens the guard under a live turn with no fault at
all. A raise in 18's and 23's places resets on BASE too (18r, 23r). The same
probe with the first version's release ``finally`` and ``or claim is None`` arm
put back into NOW (sabotage arm I) prints the same outcome in every arm — only
ARM 13's diagnostic ``claim=`` line differs — and put back into V2 it changes
the outcome of exactly the five ARM 21 arms, all to idle (ARM 13's ``claim=``
line differs there too).
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from types import SimpleNamespace
from typing import Any

import aelix_agent_core.harness.core as core_mod
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
)

T = 2.0  # every bounded wait


def texts(messages: Any) -> list[str]:
    return [
        c.text
        for m in messages
        for c in getattr(m, "content", [])
        if hasattr(c, "text")
    ]


def ok_stream(seen: list[list[str]]) -> Any:
    async def fn(model: Any, context: Any, options: Any = None) -> Any:
        seen.append(texts(context.messages))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    return fn


def blocker() -> tuple[asyncio.Event, Any]:
    entered = asyncio.Event()

    async def block(*_args: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    return entered, block


async def cancel_parked(task: asyncio.Task[Any], entered: asyncio.Event) -> str:
    await asyncio.wait_for(entered.wait(), T)
    task.cancel()
    try:
        await asyncio.wait_for(task, T)
    except asyncio.CancelledError:
        return "CancelledError"
    except TimeoutError:
        return "TIMEOUT (task did not unwind)"
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return "returned normally"


async def next_prompt(h: AgentHarness, text: str = "later") -> str:
    try:
        await asyncio.wait_for(h.prompt(text), T)
    except TimeoutError:
        return "TIMEOUT"
    except AgentHarnessError as exc:
        return f"AgentHarnessError({exc.code!r}) {exc}"
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return "ACCEPTED"


def state(h: AgentHarness) -> str:
    return f"phase={h._phase!r} idle_event={h._idle_event.is_set()}"


async def _pre_run_arm(label: str, hook: str | None, *, queued: bool) -> None:
    print(f"\nARM {label}")
    seen: list[list[str]] = []
    h = AgentHarness(AgentHarnessOptions(stream_fn=ok_stream(seen)))
    if queued:
        await h.next_turn("QUEUED-BY-NEXT-TURN")
    entered, block = blocker()
    unsub = h.hooks.on(hook, block)  # type: ignore[arg-type]
    task = asyncio.ensure_future(h.prompt("live"))
    print("  prompt() ended       :", await cancel_parked(task, entered))
    print("  after the cancel     :", state(h))
    if queued:
        print("  next_turn queue      :", texts(h._next_turn_queue))
    unsub()
    print("  next prompt()        :", await next_prompt(h))
    print("  stream_fn saw        :", seen)
    if queued:
        print("  next_turn queue after:", texts(h._next_turn_queue))


async def arm1() -> None:
    await _pre_run_arm("1  cancel in an input hook", "input", queued=False)


async def arm2() -> None:
    await _pre_run_arm("2  cancel in the queue_update drain emit", "queue_update", queued=True)


async def arm3() -> None:
    await _pre_run_arm("3  cancel in before_agent_start", "before_agent_start", queued=False)


async def arm4() -> None:
    print("\nARM 4  cancel in session.build_context() (inside _run, before its try)")
    seen: list[list[str]] = []
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=ok_stream(seen)))
    real = session.build_context
    entered = asyncio.Event()
    gate = {"block": True}

    async def slow_build_context() -> Any:
        if gate["block"]:
            entered.set()
            await asyncio.Event().wait()
        return await real()

    session.build_context = slow_build_context  # type: ignore[method-assign]
    task = asyncio.ensure_future(h.prompt("live"))
    print("  prompt() ended   :", await cancel_parked(task, entered))
    print("  after the cancel :", state(h))
    gate["block"] = False
    print("  next prompt()    :", await next_prompt(h))
    print("  stream_fn saw    :", seen)


async def arm4q() -> None:
    print(
        "\nARM 4q the same cancel with a next_turn message queued — the drain has handed"
        " it to _run, past #311's region (#320's window)"
    )
    seen: list[list[str]] = []
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=ok_stream(seen)))
    await h.next_turn("QUEUED-BY-NEXT-TURN")
    real = session.build_context
    entered = asyncio.Event()
    gate = {"block": True}

    async def slow_build_context() -> Any:
        if gate["block"]:
            entered.set()
            await asyncio.Event().wait()
        return await real()

    session.build_context = slow_build_context  # type: ignore[method-assign]
    task = asyncio.ensure_future(h.prompt("live"))
    print("  prompt() ended   :", await cancel_parked(task, entered))
    print("  after the cancel :", state(h), f"next_turn queue={texts(h._next_turn_queue)}")
    gate["block"] = False
    print("  next prompt()    :", await next_prompt(h))
    print("  stream_fn saw    :", seen)


async def arm5() -> None:
    print("\nARM 5  a wait_for_idle() parked before the cancel")
    h = AgentHarness(AgentHarnessOptions(stream_fn=ok_stream([])))
    entered, block = blocker()
    h.hooks.on("before_agent_start", block)  # type: ignore[arg-type]
    task = asyncio.ensure_future(h.prompt("live"))
    await asyncio.wait_for(entered.wait(), T)
    waiter = asyncio.ensure_future(h.wait_for_idle())
    await asyncio.sleep(0)
    print("  waiter done before the cancel:", waiter.done())
    await cancel_parked(task, entered)
    try:
        await asyncio.wait_for(asyncio.shield(waiter), T)
        print("  waiter after the cancel      : woke")
    except TimeoutError:
        print(f"  waiter after the cancel      : TIMEOUT after {T}s (never woke)")
        waiter.cancel()


async def arm6() -> None:
    print("\nARM 6  dispose() after the cancel")
    h = AgentHarness(AgentHarnessOptions(stream_fn=ok_stream([])))
    entered, block = blocker()
    h.hooks.on("before_agent_start", block)  # type: ignore[arg-type]
    task = asyncio.ensure_future(h.prompt("live"))
    await cancel_parked(task, entered)
    try:
        await asyncio.wait_for(h.dispose(), T)
        print("  dispose():", "returned")
    except TimeoutError:
        print(f"  dispose(): TIMEOUT after {T}s (parked in wait_for_idle)")


async def arm7() -> None:
    print("\nARM 7  THE TRAP — prompt #2 owns the turn when prompt #1 is cancelled")
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 60_000  # type: ignore[attr-defined]
    calls = {"n": 0}
    p2_mid_turn = asyncio.Event()
    release_p2 = asyncio.Event()

    async def fn(model: Any, context: Any, options: Any = None) -> Any:
        calls["n"] += 1
        n = calls["n"]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if n == 1:  # prompt #1: fail retryably
            failed = AssistantMessage(
                content=[], stop_reason="error", error_message="rate limit exceeded"
            )
            yield AssistantErrorEvent(
                reason="error", error=failed, error_message="rate limit exceeded"
            )
            return
        if n == 2:  # prompt #2: park mid-turn
            p2_mid_turn.set()
            await release_p2.wait()
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

    def watch(event: Any) -> None:
        if type(event).__name__ == "AutoRetryStartEvent":
            in_backoff.set()

    h.subscribe(watch)
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(in_backoff.wait(), T)
    print("  #1 in its backoff          :", state(h))
    idle_now = asyncio.ensure_future(h.wait_for_idle())
    await asyncio.sleep(0)
    print("  wait_for_idle() during it  :", "returned" if idle_now.done() else "parked")
    p2 = asyncio.ensure_future(h.prompt("second"))
    try:
        await asyncio.wait_for(p2_mid_turn.wait(), T)
        print("  #2 prompt()                : ACCEPTED, parked mid-turn")
    except TimeoutError:
        print("  #2 prompt()                : did not reach the provider", p2.done() and p2.exception())
    print("  before #1 is cancelled     :", state(h))
    p1.cancel()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p1, T)
    print("  #1 cancelled, #2 mid-turn  :", state(h))
    print("  #3 prompt() now            :", await next_prompt(h, "third"))
    release_p2.set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p2, T)
    print("  after #2 finished          :", state(h))
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 2000  # type: ignore[attr-defined]


async def _compaction_listener(h: AgentHarness) -> asyncio.Event:
    entered = asyncio.Event()

    async def listener(event: Any) -> None:
        if type(event).__name__ == "CompactionStartEvent":
            entered.set()
            await asyncio.Event().wait()

    h.subscribe(listener)
    return entered


async def arm8() -> None:
    print("\nARM 8  compact() cancelled in its compaction_start emit")
    h = AgentHarness(
        AgentHarnessOptions(
            session=Session(MemorySessionStorage()), stream_fn=ok_stream([])
        )
    )
    entered = await _compaction_listener(h)
    task = asyncio.ensure_future(h.compact())
    print("  compact() ended  :", await cancel_parked(task, entered))
    print("  after the cancel :", state(h))
    h._listeners.clear()
    print("  next prompt()    :", await next_prompt(h))


async def arm9() -> None:
    print("\nARM 9  prompt() cancelled in the threshold auto-compaction's compaction_start emit")

    async def fn(model: Any, context: Any, options: Any = None) -> Any:
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
    entered = await _compaction_listener(h)
    task = asyncio.ensure_future(h.prompt("live"))
    print("  prompt() ended   :", await cancel_parked(task, entered))
    print("  after the cancel :", state(h))


async def arm10() -> None:
    print("\nARM 10 navigate_tree() cancelled in its session_before_tree hook")
    session = Session(MemorySessionStorage())
    user_id = await session.append_message(UserMessage(content=[TextContent(text="hello")]))
    await session.append_message(AssistantMessage(content=[TextContent(text="hi back")]))
    h = AgentHarness(AgentHarnessOptions(session=session))
    entered, block = blocker()
    h.hooks.on("session_before_tree", block)  # type: ignore[arg-type]
    task = asyncio.ensure_future(h.navigate_tree(user_id))
    print("  navigate_tree() ended:", await cancel_parked(task, entered))
    print("  after the cancel     :", state(h))


async def arm11() -> None:
    print("\nARM 11 side observation — a cancel in the retry backoff, no second prompt")
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 60_000  # type: ignore[attr-defined]

    async def fn(model: Any, context: Any, options: Any = None) -> Any:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        failed = AssistantMessage(
            content=[], stop_reason="error", error_message="rate limit exceeded"
        )
        yield AssistantErrorEvent(
            reason="error", error=failed, error_message="rate limit exceeded"
        )

    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=fn)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    events: list[str] = []
    in_backoff = asyncio.Event()

    def watch(event: Any) -> None:
        name = type(event).__name__
        if name.startswith("AutoRetry"):
            events.append(name)
        if name == "AutoRetryStartEvent":
            in_backoff.set()

    h.subscribe(watch)
    task = asyncio.ensure_future(h.prompt("first"))
    print("  prompt() ended :", await cancel_parked(task, in_backoff))
    print("  after          :", state(h), f"_retry_attempt={h._retry_attempt}")
    print("  retry events   :", events)
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 2000  # type: ignore[attr-defined]


async def arm12() -> None:
    print("\nARM 12 THE TRAP, exception arm — prompt #1 RAISES while prompt #2 owns the turn")
    gap_entered = asyncio.Event()
    release_gap = asyncio.Event()
    p2_mid_turn = asyncio.Event()
    release_p2 = asyncio.Event()
    calls = {"stream": 0, "branch": 0}

    async def fn(model: Any, context: Any, options: Any = None) -> Any:
        calls["stream"] += 1
        n = calls["stream"]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if n == 2:  # prompt #2: park mid-turn
            p2_mid_turn.set()
            await release_p2.wait()
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=f"answer {n}")],
                stop_reason="end_turn",
                usage={"total_tokens": 5_000} if n == 1 else None,
            )
        )

    session = Session(MemorySessionStorage())
    real_get_branch = session.get_branch
    holder: dict[str, AgentHarness] = {}

    async def get_branch(*args: Any, **kwargs: Any) -> Any:
        # ``build_context()`` reads the branch too, from inside ``_run`` with
        # the phase at "turn"; the call to park is prompt #1's threshold check,
        # the first one made AFTER ``_run``'s ``finally`` set the phase idle.
        if holder["h"]._phase == "idle" and not gap_entered.is_set():
            calls["branch"] += 1
            gap_entered.set()
            await release_gap.wait()
        return await real_get_branch(*args, **kwargs)

    session.get_branch = get_branch  # type: ignore[method-assign]
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=fn))
    holder["h"] = h
    h._state.model = SimpleNamespace(context_window=20_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(gap_entered.wait(), T)
    print("  #1 past _run, in its tail  :", state(h))
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(p2_mid_turn.wait(), T)
    print("  #2 prompt()                : ACCEPTED, parked mid-turn")
    release_gap.set()
    try:
        await asyncio.wait_for(p1, T)
        print("  #1 ended                   : returned")
    except BaseException as exc:  # noqa: BLE001
        print("  #1 ended                   :", f"{type(exc).__name__}: {exc}")
    print("  #1 raised, #2 mid-turn     :", state(h))
    print("  #3 prompt() now            :", await next_prompt(h, "third"))
    release_p2.set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p2, T)
    print("  after #2 finished          :", state(h))


async def _arm13(fault: str) -> None:
    print(
        f"\nARM 13{'c' if fault == 'cancel' else 'r'} the re-run's own turn — "
        f"#2 ran and finished inside #1's backoff, then #1's re-run {fault}s in "
        "build_context()"
    )
    calls = {"stream": 0}

    async def fn(model: Any, context: Any, options: Any = None) -> Any:
        calls["stream"] += 1
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if calls["stream"] == 1:  # prompt #1: fail retryably
            failed = AssistantMessage(
                content=[], stop_reason="error", error_message="rate limit exceeded"
            )
            yield AssistantErrorEvent(
                reason="error", error=failed, error_message="rate limit exceeded"
            )
            return
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    session = Session(MemorySessionStorage())
    real_build_context = session.build_context
    park = {"on": False}
    parked = asyncio.Event()

    async def build_context() -> Any:
        if park["on"]:
            park["on"] = False
            parked.set()
            if fault == "raise":
                raise RuntimeError("session storage unavailable")
            await asyncio.Event().wait()
        return await real_build_context()

    session.build_context = build_context  # type: ignore[method-assign]
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=fn))
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    # The backoff, made deterministic: park until released instead of sleeping.
    in_backoff = asyncio.Event()
    release_backoff = asyncio.Event()

    async def backoff(message: Any) -> bool:
        if h._state.messages and isinstance(h._state.messages[-1], AssistantMessage):
            h._state.messages.pop()
        in_backoff.set()
        await release_backoff.wait()
        return True

    h._handle_retryable_error = backoff  # type: ignore[method-assign]
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(in_backoff.wait(), T)
    print("  #1 in its backoff        :", state(h))
    print("  #2 prompt(), start to end:", await next_prompt(h, "second"))
    claim = getattr(h, "_turn_owner", "n/a")
    claim_text = "n/a" if claim == "n/a" else "None" if claim is None else "left by #2"
    print("  after #2 returned        :", state(h), f"claim={claim_text}")
    park["on"] = True
    release_backoff.set()
    await asyncio.wait_for(parked.wait(), T)
    if fault == "cancel":
        p1.cancel()
    try:
        await asyncio.wait_for(p1, T)
        print("  #1 ended                 : returned")
    except BaseException as exc:  # noqa: BLE001
        print("  #1 ended                 :", f"{type(exc).__name__}: {exc}")
    print("  after                    :", state(h))
    print("  #3 prompt()              :", await next_prompt(h, "third"))


async def arm13c() -> None:
    await _arm13("cancel")


async def arm13r() -> None:
    await _arm13("raise")


async def arm14() -> None:
    print(
        "\nARM 14 the same cancel, delivered by a timeout or a failing TaskGroup sibling"
        " instead of task.cancel()"
    )
    for label in ("asyncio.wait_for", "asyncio.timeout", "TaskGroup sibling"):
        h = AgentHarness(AgentHarnessOptions(stream_fn=ok_stream([])))
        entered, block = blocker()
        unsub = h.hooks.on("before_agent_start", block)  # type: ignore[arg-type]

        async def sibling(entered: asyncio.Event = entered) -> None:
            await entered.wait()
            raise RuntimeError("sibling failed")

        outcome = "returned"
        if label == "TaskGroup sibling":
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(h.prompt("live"))
                    tg.create_task(sibling())
            except* RuntimeError:
                outcome = "sibling's RuntimeError (the group cancelled prompt())"
        else:
            try:
                if label == "asyncio.wait_for":
                    await asyncio.wait_for(h.prompt("live"), 0.2)
                else:
                    async with asyncio.timeout(0.2):
                        await h.prompt("live")
            except TimeoutError:
                outcome = "TimeoutError"
        unsub()
        print(f"  {label:<17}: prompt() -> {outcome}; hook entered={entered.is_set()};"
              f" after: {state(h)}; next prompt(): {await next_prompt(h)}")


# --- ARMs 15-20: the claim vs a re-run (fix stage) ---------------------------
#
# The claim used to be taken at ``prompt()``'s entry only, while a re-run
# ``_run([])`` sets "turn" again. These arms put a second prompt into the first
# one's tail and look at whose "turn" is given back. 15 uses the real backoff;
# the rest replace it with a gate so the order is a sequence, not a race.


class Provider:
    """Scripted per call: "retry" fails retryably, "big" answers over the 20k
    model's compaction threshold, "park" answers once ``release[n]`` is set;
    unscripted calls behave as ``default``."""

    def __init__(self, script: dict[int, str], default: str = "ok") -> None:
        self.script = script
        self.default = default
        self.calls = 0
        self.parked: dict[int, asyncio.Event] = {}
        self.release: dict[int, asyncio.Event] = {}

    def event(self, table: dict[int, asyncio.Event], n: int) -> asyncio.Event:
        return table.setdefault(n, asyncio.Event())

    async def __call__(self, model: Any, context: Any, options: Any = None) -> Any:
        self.calls += 1
        n = self.calls
        kind = self.script.get(n, self.default)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if kind == "retry":
            failed = AssistantMessage(
                content=[], stop_reason="error", error_message="rate limit exceeded"
            )
            yield AssistantErrorEvent(
                reason="error", error=failed, error_message="rate limit exceeded"
            )
            return
        if kind == "park":
            self.event(self.parked, n).set()
            await self.event(self.release, n).wait()
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=f"answer {n}")],
                stop_reason="end_turn",
                usage={"total_tokens": 5_000} if kind == "big" else None,
            )
        )


class Backoff:
    """``_handle_retryable_error`` as a gate: the i-th call pops the error
    assistant, sets ``entered[i]``, parks until ``release[i]`` and returns
    ``verdicts[i]`` (``False`` = the retry given up, as ``abort_retry()``)."""

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
        if self.h._state.messages and isinstance(self.h._state.messages[-1], AssistantMessage):
            self.h._state.messages.pop()
        self.entered[i].set()
        await self.release[i].wait()
        return self.verdicts[i]


def fault_next_build_context(session: Session, fault: str) -> tuple[Any, asyncio.Event]:
    real = session.build_context
    armed = {"on": False}
    reached = asyncio.Event()

    async def build_context() -> Any:
        if armed["on"]:
            armed["on"] = False
            reached.set()
            if fault == "raise":
                raise RuntimeError("session storage unavailable")
            await asyncio.Event().wait()
        return await real()

    session.build_context = build_context  # type: ignore[method-assign]
    return (lambda: armed.__setitem__("on", True)), reached


def owner_of(h: AgentHarness, names: dict[int, str]) -> str:
    if not hasattr(h, "_turn_owner"):
        return "n/a (no claim on this tree)"
    return names.get(id(h._turn_owner), "None" if h._turn_owner is None else "?")


async def third_prompt(h: AgentHarness, provider: Provider, text: str = "third") -> str:
    """A third (or fourth) ``prompt()``: refused, or let in — then cancelled once
    it has provably reached the provider, so it does not linger."""

    before = provider.calls
    t = asyncio.ensure_future(h.prompt(text))
    done, _ = await asyncio.wait({t}, timeout=0.5)
    if t in done:
        exc = t.exception()
        if exc is None:
            return "ACCEPTED (ran to completion)"
        if isinstance(exc, AgentHarnessError):
            return f"busy: AgentHarnessError({exc.code!r})"
        return f"ended: {exc!r}"
    reached = provider.calls > before
    t.cancel()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(t, T)
    return "ACCEPTED (reached the provider)" if reached else "pending"


async def arm15() -> None:
    print(
        "\nARM 15 the claim vs a re-run, cancel arm, REAL backoff — #2 waits in its own"
        " backoff while #1's re-run is mid-turn, then #2 is cancelled"
    )
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 1500  # type: ignore[attr-defined]
    provider = Provider({1: "retry", 2: "retry", 3: "park"}, default="park")
    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=provider)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    starts: list[asyncio.Event] = [asyncio.Event(), asyncio.Event()]

    def watch(event: Any) -> None:
        if type(event).__name__ == "AutoRetryStartEvent":
            for e in starts:
                if not e.is_set():
                    e.set()
                    break

    h.subscribe(watch)
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(starts[0].wait(), T)
    names = {id(getattr(h, "_turn_owner", None)): "#1"}
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(starts[1].wait(), T)
    names[id(getattr(h, "_turn_owner", None))] = "#2"
    print("  #1 and #2 both in a real backoff:", state(h), f"claim={owner_of(h, names)}")
    await asyncio.wait_for(provider.event(provider.parked, 3).wait(), 3 * T)
    print("  #1's re-run mid-turn           :", state(h), f"claim={owner_of(h, names)}",
          f"(#2 still in its backoff: {not p2.done()})")
    p2.cancel()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p2, T)
    print("  #2 cancelled                   :", state(h), f"claim={owner_of(h, names)}")
    print("  #3 prompt() now                :", await third_prompt(h, provider))
    provider.event(provider.release, 3).set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p1, T)
    print("  after #1 finished              :", state(h))
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 2000  # type: ignore[attr-defined]


async def arm16() -> None:
    print(
        "\nARM 16 the claim vs a re-run, raise arm — #2's threshold compact() raises"
        " while #1's re-run is mid-turn (ARM 12 turned around)"
    )
    provider = Provider({1: "retry", 2: "big", 3: "park"}, default="park")
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.model = SimpleNamespace(context_window=20_000)  # type: ignore[assignment]
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = True
    backoff = Backoff(h, [True])
    real_get_branch = session.get_branch
    gap_entered = asyncio.Event()
    release_gap = asyncio.Event()

    async def get_branch(*args: Any, **kwargs: Any) -> Any:
        if h._phase == "idle" and not gap_entered.is_set():
            gap_entered.set()
            await release_gap.wait()
        return await real_get_branch(*args, **kwargs)

    session.get_branch = get_branch  # type: ignore[method-assign]
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), T)
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(gap_entered.wait(), T)
    print("  #2 reading the branch in its tail:", state(h))
    backoff.release[0].set()
    await asyncio.wait_for(provider.event(provider.parked, 3).wait(), T)
    print("  #1's re-run mid-turn             :", state(h))
    release_gap.set()
    try:
        await asyncio.wait_for(p2, T)
        print("  #2 ended                         : returned")
    except BaseException as exc:  # noqa: BLE001
        print("  #2 ended                         :", f"{type(exc).__name__}: {exc}")
    print("  while #1's re-run is mid-turn    :", state(h))
    print("  #3 prompt() now                  :", await third_prompt(h, provider))
    provider.event(provider.release, 3).set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p1, T)
    print("  after #1 finished                :", state(h))


async def _arm17(fault: str) -> None:
    print(
        f"\nARM 17{'r' if fault == 'raise' else 'c'} #1's re-run "
        f"{'raises' if fault == 'raise' else 'is cancelled'} in build_context() while #2"
        " waits in its own backoff; #2 then gives its retry up and returns normally"
    )
    provider = Provider({1: "retry", 2: "retry"})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = Backoff(h, [True, False])
    arm, reached = fault_next_build_context(session, fault)
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), T)
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(backoff.entered[1].wait(), T)
    arm()
    backoff.release[0].set()
    await asyncio.wait_for(reached.wait(), T)
    if fault == "cancel":
        p1.cancel()
    try:
        await asyncio.wait_for(p1, T)
        print("  #1 ended                :", "returned")
    except BaseException as exc:  # noqa: BLE001
        print("  #1 ended                :", f"{type(exc).__name__}: {exc}")
    print("  after #1 ended          :", state(h))
    backoff.release[1].set()
    try:
        await asyncio.wait_for(p2, T)
        print("  #2 ended                :", "returned normally")
    except BaseException as exc:  # noqa: BLE001
        print("  #2 ended                :", f"{type(exc).__name__}: {exc}")
    print("  after #2 returned       :", state(h))
    try:
        await asyncio.wait_for(h.wait_for_idle(), T)
        print("  wait_for_idle()         : returned")
    except TimeoutError:
        print(f"  wait_for_idle()         : TIMEOUT after {T}s")
    print("  next prompt()           :", await next_prompt(h, "third"))


async def arm17r() -> None:
    await _arm17("raise")


async def arm17c() -> None:
    await _arm17("cancel")


async def _arm18(fault: str) -> None:
    print(
        f"\nARM 18{'' if fault == 'cancel' else 'r'} OVERLAP — #1's backoff ends while #2 is"
        f" mid-turn; #1's re-run {'is cancelled' if fault == 'cancel' else 'raises'} in"
        " build_context() with #2's turn still live"
    )
    provider = Provider({1: "retry", 2: "park"}, default="park")
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = Backoff(h, [True])
    arm, reached = fault_next_build_context(session, fault)
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), T)
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(provider.event(provider.parked, 2).wait(), T)
    arm()
    backoff.release[0].set()
    await asyncio.wait_for(reached.wait(), T)
    if fault == "cancel":
        print("  #2 mid-turn, #1's re-run in build_context:", state(h))
        p1.cancel()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p1, T)
    print(f"  #1 {'cancelled' if fault == 'cancel' else 'raised'}, #2 still mid-turn"
          f"{'          ' if fault == 'cancel' else '             '}:", state(h))
    print("  #3 prompt() now                          :", await third_prompt(h, provider))
    provider.event(provider.release, 2).set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p2, T)
    print("  after #2 finished                        :", state(h))


async def arm18() -> None:
    await _arm18("cancel")


async def _arm18h(hook: str) -> None:
    print(
        f"\nARM 18h{'-input' if hook == 'input' else ''} OVERLAP — ARM 18 with #2 still in its"
        f" {hook} hook, not yet at the provider, when #1's re-run is cancelled in"
        " build_context()"
    )
    provider = Provider({1: "retry"}, default="park")
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = Backoff(h, [True])
    arm, reached = fault_next_build_context(session, "cancel")
    in_hook, release_hook = park_hook_of(h, hook, "second")
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), T)
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(in_hook.wait(), T)
    arm()
    backoff.release[0].set()
    await asyncio.wait_for(reached.wait(), T)
    print("  #2 in its hook, #1's re-run in build_context:", state(h))
    p1.cancel()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p1, T)
    print("  #1 cancelled, #2 still in its hook          :", state(h))
    print("  #3 prompt() now                             :", await third_prompt(h, provider))
    release_hook.set()
    n = provider.calls + 1  # #2's call: a third prompt let in above took one
    await asyncio.wait_for(provider.event(provider.parked, n).wait(), T)
    provider.event(provider.release, n).set()
    try:
        await asyncio.wait_for(p2, T)
        print("  #2 ended                                    : returned")
    except BaseException as exc:  # noqa: BLE001
        print("  #2 ended                                    :", f"{type(exc).__name__}: {exc}")
    print("  after #2 finished                           :", state(h))


async def arm18h() -> None:
    await _arm18h("before_agent_start")


async def arm18h_input() -> None:
    await _arm18h("input")


async def arm18r() -> None:
    await _arm18("raise")


async def arm19() -> None:
    print(
        "\nARM 19 OVERLAP, no fault — #1's re-run starts while #2 is mid-turn; #2's"
        " turn ends first, #1's re-run still live"
    )
    provider = Provider({1: "retry", 2: "park", 3: "park"}, default="park")
    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=provider)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = Backoff(h, [True])
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), T)
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(provider.event(provider.parked, 2).wait(), T)
    backoff.release[0].set()
    await asyncio.wait_for(provider.event(provider.parked, 3).wait(), T)
    print("  both turns mid-stream            :", state(h))
    provider.event(provider.release, 2).set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p2, T)
    print("  #2 returned, #1's re-run live    :", state(h))
    print("  #3 prompt() now                  :", await third_prompt(h, provider))
    provider.event(provider.release, 3).set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p1, T)
    print("  after #1 finished                :", state(h))


async def arm20() -> None:
    print(
        "\nARM 20 OVERLAP, the other way — #2 is parked in before_agent_start when"
        " #1's re-run goes mid-turn; #2 is cancelled there"
    )
    provider = Provider({1: "retry", 2: "park"}, default="park")
    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=provider)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = Backoff(h, [True])
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), T)
    entered, block = blocker()
    unsub = h.hooks.on("before_agent_start", block)  # type: ignore[arg-type]
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(entered.wait(), T)
    backoff.release[0].set()
    await asyncio.wait_for(provider.event(provider.parked, 2).wait(), T)
    print("  #2 in its hook, #1's re-run mid-turn:", state(h))
    p2.cancel()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p2, T)
    unsub()
    print("  #2 cancelled                        :", state(h))
    print("  #3 prompt() now                     :", await third_prompt(h, provider))
    provider.event(provider.release, 2).set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p1, T)
    print("  after #1 finished                   :", state(h))


# --- ARMs 21-24: the first run's flip (cross-review round) -------------------
#
# The second version of this change took the claim at ``prompt()``'s entry and
# right before each re-run ``_run([])``, but not at the first ``_run``'s flip,
# which comes three awaits after the entry (the ``input`` hook, the drain's
# ``queue_update``, ``before_agent_start``) — and another call's re-run can take
# the claim in between. Found by an independent cross-review; these arms are its
# probe's, ported here (its X1r/X1c = 21r/21c, X2 = 22, X3c/X3r = 23c/23r, X4 =
# 24).


def park_hook_of(
    h: AgentHarness, hook: str, text: str, result: Any = None
) -> tuple[asyncio.Event, asyncio.Event]:
    """Park ``hook`` for the prompt of ``text`` only (``before_agent_start``
    carries it as ``prompt``, ``input`` as ``text``); released, answer
    ``result``."""

    field = "prompt" if hook == "before_agent_start" else "text"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(event: Any, *_args: Any) -> Any:
        if getattr(event, field, None) != text:
            return None
        entered.set()
        await release.wait()
        return result

    h.hooks.on(hook, handler)  # type: ignore[arg-type]
    return entered, release


async def _arm21(fault: str, where: str = "before_agent_start", real: bool = False) -> None:
    label = f"21{'r' if fault == 'raise' else 'c'}{'-input' if where == 'input' else ''}"
    label += "-real" if real else ""
    print(
        f"\nARM {label} the FIRST run's flip — #2 parked in {where} while #1's re-run"
        " runs and returns; then #2's first build_context()"
        f" {'raises' if fault == 'raise' else 'is cancelled'}"
        + (" (REAL backoff, base delay 300 ms)" if real else "")
    )
    provider = Provider({1: "retry"})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    arm, reached = fault_next_build_context(session, fault)
    in_hook, release_hook = park_hook_of(h, where, "second")
    release_backoff: asyncio.Event | None = None
    if real:
        core_mod._AUTO_RETRY_BASE_DELAY_MS = 300  # type: ignore[attr-defined]
        in_backoff = asyncio.Event()

        def watch(event: Any) -> None:
            if type(event).__name__ == "AutoRetryStartEvent":
                in_backoff.set()

        h.subscribe(watch)
    else:
        backoff = Backoff(h, [True])
        in_backoff, release_backoff = backoff.entered[0], backoff.release[0]
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(in_backoff.wait(), T)
    print("  #1 in its backoff     :", state(h))
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(in_hook.wait(), T)
    print("  #2 parked in its hook :", state(h))
    if release_backoff is not None:
        release_backoff.set()
    try:
        await asyncio.wait_for(p1, T)
        print("  #1 returned           :", state(h), "(#2 still in its hook)")
    except BaseException as exc:  # noqa: BLE001
        print("  #1 ended              :", f"{type(exc).__name__}: {exc}")
    arm()
    release_hook.set()
    if fault == "cancel":
        await asyncio.wait_for(reached.wait(), T)
        print("  #2 in build_context   :", state(h))
        p2.cancel()
    try:
        await asyncio.wait_for(p2, T)
        print("  #2 ended              : returned")
    except BaseException as exc:  # noqa: BLE001
        print("  #2 ended              :", f"{type(exc).__name__}: {exc}")
    print("  after #2              :", state(h))
    try:
        await asyncio.wait_for(h.wait_for_idle(), T)
        print("  wait_for_idle()       : returned")
    except TimeoutError:
        print(f"  wait_for_idle()       : TIMEOUT after {T}s")
    print("  next prompt()         :", await next_prompt(h, "third"))
    try:
        await asyncio.wait_for(h.dispose(), T)
        print("  dispose()             : returned")
    except TimeoutError:
        print(f"  dispose()             : TIMEOUT after {T}s")
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 2000  # type: ignore[attr-defined]


async def arm21r() -> None:
    await _arm21("raise")


async def arm21c() -> None:
    await _arm21("cancel")


async def arm21r_input() -> None:
    await _arm21("raise", where="input")


async def arm21r_real() -> None:
    await _arm21("raise", real=True)


async def arm21c_real() -> None:
    await _arm21("cancel", real=True)


async def arm22() -> None:
    print(
        "\nARM 22 the first run's flip, the other way — #1's re-run ran and returned"
        " while #2 waited in before_agent_start; #3 gets in and parks in its input hook;"
        " #2's first run goes mid-turn; #3 is cancelled"
    )
    provider = Provider({1: "retry", 3: "park"})  # #1's re-run (call 2) answers at once
    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=provider)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = Backoff(h, [True])
    e2, r2 = park_hook_of(h, "before_agent_start", "second")
    e3, _ = park_hook_of(h, "input", "third")
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), T)
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(e2.wait(), T)
    backoff.release[0].set()
    await asyncio.wait_for(p1, T)
    print("  #1 returned, #2 in its hook :", state(h))
    p3 = asyncio.ensure_future(h.prompt("third"))
    await asyncio.wait_for(e3.wait(), T)
    r2.set()
    await asyncio.wait_for(provider.event(provider.parked, 3).wait(), T)
    print("  #2's first run mid-turn     :", state(h))
    p3.cancel()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p3, T)
    print("  #3 cancelled, #2 mid-turn   :", state(h))
    print("  #4 prompt() now             :", await third_prompt(h, provider, "fourth"))
    provider.event(provider.release, 3).set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p2, T)
    print("  after #2 finished           :", state(h))


async def _arm23(fault: str) -> None:
    print(
        f"\nARM 23{'c' if fault == 'cancel' else 'r'} OVERLAP, the first run last — #2"
        " (parked in before_agent_start) is released while #1's re-run is mid-turn,"
        f" and #2's first build_context() {'is cancelled' if fault == 'cancel' else 'raises'}"
    )
    provider = Provider({1: "retry", 2: "park"}, default="park")
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = Backoff(h, [True])
    arm, reached = fault_next_build_context(session, fault)
    e2, r2 = park_hook_of(h, "before_agent_start", "second")
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), T)
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(e2.wait(), T)
    backoff.release[0].set()
    await asyncio.wait_for(provider.event(provider.parked, 2).wait(), T)
    print("  #1's re-run mid-turn, #2 in its hook:", state(h))
    arm()
    r2.set()
    await asyncio.wait_for(reached.wait(), T)
    if fault == "cancel":
        p2.cancel()
    try:
        await asyncio.wait_for(p2, T)
        print("  #2 ended                            : returned")
    except BaseException as exc:  # noqa: BLE001
        print("  #2 ended                            :", f"{type(exc).__name__}: {exc}")
    print("  #1's re-run still mid-turn          :", state(h))
    print("  #3 prompt() now                     :", await third_prompt(h, provider))
    provider.event(provider.release, 2).set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p1, T)
    print("  after #1 finished                   :", state(h))


async def arm23c() -> None:
    await _arm23("cancel")


async def arm23r() -> None:
    await _arm23("raise")


async def arm24() -> None:
    from aelix_agent_core.harness.hooks import InputHandled

    print(
        "\nARM 24 the InputHandled return — #2 is in its input hook when #1's re-run"
        " goes mid-turn; the hook then answers InputHandled"
    )
    provider = Provider({1: "retry", 2: "park"}, default="park")
    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=provider)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    backoff = Backoff(h, [True])
    e2, r2 = park_hook_of(h, "input", "second", result=InputHandled())
    p1 = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), T)
    p2 = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(e2.wait(), T)
    backoff.release[0].set()
    await asyncio.wait_for(provider.event(provider.parked, 2).wait(), T)
    print("  #1's re-run mid-turn, #2 in its hook:", state(h))
    r2.set()
    print("  #2 returned                         :", await asyncio.wait_for(p2, T))
    print("  #1's re-run still mid-turn          :", state(h))
    print("  #3 prompt() now                     :", await third_prompt(h, provider))
    provider.event(provider.release, 2).set()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(p1, T)
    print("  after #1 finished                   :", state(h))


ARMS = {
    "1": arm1, "2": arm2, "3": arm3, "4": arm4, "4q": arm4q, "5": arm5, "6": arm6,
    "7": arm7, "8": arm8, "9": arm9, "10": arm10, "11": arm11, "12": arm12,
    "13c": arm13c, "13r": arm13r, "14": arm14,
    "15": arm15, "16": arm16, "17r": arm17r, "17c": arm17c,
    "18": arm18, "18r": arm18r, "18h": arm18h, "18h-input": arm18h_input,
    "19": arm19, "20": arm20,
    "21r": arm21r, "21c": arm21c, "21r-input": arm21r_input,
    "21r-real": arm21r_real, "21c-real": arm21c_real,
    "22": arm22, "23c": arm23c, "23r": arm23r, "24": arm24,
}


async def main(selected: list[str]) -> None:
    print("core.py:", core_mod.__file__)
    for key in selected or list(ARMS):
        await ARMS[key]()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
