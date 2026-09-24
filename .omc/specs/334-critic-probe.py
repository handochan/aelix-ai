"""#334 critic probe — gaps the critic lane found in the #334 design (K1-K5).

Written by the critic lane against the design's prototype; committed by the
implementation lane, which fixed each one (ADR-0023's #334 amendment has the
before/after). Run from a tree, optionally with a scratch copy first on
PYTHONPATH:

    cd <tree> && [PYTHONPATH=<scratch>/src] uv run --no-sync python .omc/specs/334-critic-probe.py [ARM ...]

The first printed line is the core.py that was loaded.

ARMs
  K1  the OVERFLOW re-run is in flight (provider call 2 parked): phase, the idle
      event, a wait_for_idle() waiter parked BEFORE the prompt's first run, a
      second prompt(). Run against the prototype and against sab_b (nested
      compact()'s finally also sets the idle event) to see whether the design's
      test_the_overflow_rerun_runs_inside_the_claim (phase == "turn", prompt()
      busy, idle after) can tell them apart.
  K2  writes made WHILE the prompt's release flush is awaiting a session write
      (set_model, append_message): are they recorded by the time prompt()
      returns, or stranded on the pending queue of an idle harness?
  K3  dispose() of a turn in flight whose context is over the threshold: the
      aborted run's tail still runs a threshold compaction (pi skips it:
      _handlePostAgentRun returns on _agentRunAbortRequested, _checkCompaction
      skips stopReason "aborted"). Does dispose() return before or after that
      compaction's summariser call?
  K4  abort() while the prompt is parked in its tail's threshold branch read
      (after the last _run, before compact()): is the compaction still run?
      (and does a parked dispose() wait for it)
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import aelix_agent_core.harness.core as core_mod
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.session import (
    CompactionPreparation,
    CompactResult,
    MemorySessionStorage,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

WAIT = 5.0
_OVERFLOW = "prompt is too long: 213462 tokens > 200000 maximum"


def _texts(messages: Any) -> list[str]:
    return [c.text for m in messages for c in getattr(m, "content", []) if hasattr(c, "text")]


def _st(h: AgentHarness) -> str:
    return f"phase={h.phase!r} idle_event={h._idle_event.is_set()}"


class Provider:
    def __init__(self, script: dict[int, str]) -> None:
        self.script = script
        self.calls = 0
        self.seen: list[list[str]] = []
        self.parked = {n: asyncio.Event() for n in range(1, 10)}
        self.release = {n: asyncio.Event() for n in range(1, 10)}

    async def __call__(
        self, model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        self.calls += 1
        n = self.calls
        self.seen.append(_texts(context.messages))
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


class Summariser:
    def __init__(self, park: bool = False) -> None:
        self.calls = 0
        self.park = park
        self.parked = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, _model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        self.calls += 1
        if self.park:
            self.parked.set()
            await self.release.wait()
        return CompactResult(
            summary="S", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=1, details={}
        )


async def _seeded_session() -> Session:
    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    return session


async def _wait_for(ev: asyncio.Event, timeout: float = 0.5) -> bool:
    try:
        await asyncio.wait_for(ev.wait(), timeout)
        return True
    except TimeoutError:
        return False


async def _spin(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def _try_prompt(h: AgentHarness) -> str:
    try:
        await asyncio.wait_for(h.prompt("intruder"), 0.2)
        return "ACCEPTED (ran to completion)"
    except AgentHarnessError as exc:
        return f"refused: {exc.code}"
    except TimeoutError:
        return "ACCEPTED (still running)"


async def armK1() -> None:
    print("\nK1  overflow re-run in flight: what does a pre-parked wait_for_idle() see?")
    provider = Provider({1: "overflow", 2: "park"})
    session = await _seeded_session()
    h = AgentHarness(
        AgentHarnessOptions(session=session, stream_fn=provider, _summarizer_override=Summariser())
    )
    h._state.model = SimpleNamespace(context_window=200_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    h._state.auto_retry_enabled = False
    first = asyncio.ensure_future(h.prompt("first"))
    waiter = asyncio.ensure_future(h.wait_for_idle())
    await _spin(2)
    print("  after #1's entry          :", _st(h), "waiter done:", waiter.done())
    ok = await _wait_for(provider.parked[2], WAIT)
    print("  overflow re-run reached   :", ok, f"(provider calls={provider.calls})")
    await _spin()
    print("  during the re-run         :", _st(h), f"is_idle={h.is_idle}",
          "| pre-parked wait_for_idle():", "RETURNED" if waiter.done() else "parked")
    print("  a prompt() now            :", await _try_prompt(h))
    fresh = asyncio.ensure_future(h.wait_for_idle())
    await _spin()
    print("  a wait_for_idle() started now:", "RETURNED" if fresh.done() else "parked")
    provider.release[2].set()
    try:
        await asyncio.wait_for(first, WAIT)
        print("  #1                        : returned;", _st(h))
    except BaseException as exc:  # noqa: BLE001
        print("  #1                        :", f"{type(exc).__name__}: {exc}")


async def armK2() -> None:
    print("\nK2  writes made while the release flush awaits a session write")
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 60_000  # type: ignore[attr-defined]
    provider = Provider({1: "retry"})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    started = asyncio.Event()
    h.subscribe(lambda e: started.set() if type(e).__name__ == "AutoRetryStartEvent" else None)
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(started.wait(), WAIT)
    await _spin()
    print("  in #1's backoff           :", _st(h))
    await h.set_thinking_level("high")
    print("  set_thinking_level('high'): pending", [type(p).__name__ for p in h._pending_session_writes])
    if not h._pending_session_writes:
        print("  (appended directly: this tree has no release flush to park — arm not constructible)")
        h.abort_retry()
        await asyncio.wait_for(first, WAIT)
        return
    parked = asyncio.Event()
    release = asyncio.Event()
    real = session.append_thinking_level_change

    async def slow(level: str) -> Any:
        parked.set()
        await release.wait()
        return await real(level)

    session.append_thinking_level_change = slow  # type: ignore[method-assign]
    h.abort_retry()  # the retry is given up: no re-run, so only the release flush writes it
    ok = await _wait_for(parked, WAIT)
    print("  release flush parked      :", ok, "|", _st(h))
    await h.set_model(Model(id="late-model", context_window=100_000))
    await h.append_message(UserMessage(content=[TextContent(text="late-append")]))
    print("  set_model + append_message during the flush: pending",
          [type(p).__name__ for p in h._pending_session_writes])
    release.set()
    await asyncio.wait_for(first, WAIT)
    entries = await session.get_branch()
    print("  #1 returned               :", _st(h))
    print("  session entry types       :", [getattr(e, "type", "?") for e in entries])
    print("  STILL PENDING on an idle harness:", [type(p).__name__ for p in h._pending_session_writes])


async def _k3_setup(park_summariser: bool) -> tuple[AgentHarness, Provider, Summariser]:
    provider = Provider({1: "park"})
    session = await _seeded_session()
    summariser = Summariser(park=park_summariser)
    h = AgentHarness(
        AgentHarnessOptions(session=session, stream_fn=provider, _summarizer_override=summariser)
    )
    h._state.model = Model(id="m20k", context_window=20_000)
    h._state.auto_compaction_enabled = True
    h._state.auto_retry_enabled = False
    # A previous, successful answer whose usage is already over 20k - reserve: the
    # aborted run's estimate walks back to it (pi's estimateContextTokens).
    h._state.messages.extend([
        UserMessage(content=[TextContent(text="earlier")]),
        AssistantMessage(content=[TextContent(text="earlier answer")], stop_reason="end_turn",
                         usage={"total_tokens": 5_000}),
    ])
    return h, provider, summariser


async def armK3() -> None:
    print("\nK3  dispose() of a turn in flight, context over the threshold")
    h, provider, summariser = await _k3_setup(park_summariser=True)
    compactions: list[str] = []
    h.hooks.on("session_compact", lambda ev, *_: compactions.append(ev.reason))  # type: ignore[call-overload]
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(provider.parked[1].wait(), WAIT)
    print("  turn in flight            :", _st(h))
    disposer = asyncio.ensure_future(h.dispose())
    summ = await _wait_for(summariser.parked, 1.0)
    await _spin()
    print("  aborted run's tail called the summariser:", summ,
          "| dispose() done at that moment:", disposer.done(), "|", _st(h))
    summariser.release.set()
    for name, fut in (("dispose()", disposer), ("#1", first)):
        try:
            await asyncio.wait_for(fut, WAIT)
            print(f"  {name:<26}: returned")
        except BaseException as exc:  # noqa: BLE001
            print(f"  {name:<26}: {type(exc).__name__}: {exc}")
    print("  summariser calls          :", summariser.calls, "| session_compact reasons seen:", compactions,
          "| stop_reasons in state:", [getattr(m, "stop_reason", None) for m in h._state.messages if isinstance(m, AssistantMessage)])


async def armK4() -> None:
    print("\nK4  abort() while #1 is parked in its threshold branch read (after the last _run)")
    provider = Provider({1: "big"})
    session = await _seeded_session()
    summariser = Summariser()
    h = AgentHarness(
        AgentHarnessOptions(session=session, stream_fn=provider, _summarizer_override=summariser)
    )
    h._state.model = Model(id="m20k", context_window=20_000)
    h._state.auto_compaction_enabled = True
    settled = asyncio.Event()

    async def on_settled(*_a: Any) -> None:
        settled.set()

    h.hooks.on("settled", on_settled)  # type: ignore[call-overload]
    gap = asyncio.Event()
    release_gap = asyncio.Event()
    real_get_branch = session.get_branch

    async def get_branch(*a: Any, **k: Any) -> Any:
        if settled.is_set() and not gap.is_set():
            gap.set()
            await release_gap.wait()
        return await real_get_branch(*a, **k)

    session.get_branch = get_branch  # type: ignore[method-assign]
    first = asyncio.ensure_future(h.prompt("first"))
    ok = await _wait_for(gap, WAIT)
    print("  parked in the branch read :", ok, "|", _st(h))
    await h.abort()
    print("  abort() returned          : abort_requested =", h._abort_requested)
    disposer = asyncio.ensure_future(h.dispose())
    await _spin()
    print("  a dispose() started now   : done =", disposer.done())
    release_gap.set()
    await asyncio.wait_for(first, WAIT)
    await asyncio.wait_for(disposer, WAIT)
    print("  #1 returned               :", _st(h), "| summariser calls (compaction after abort):", summariser.calls)


async def armK5() -> None:
    print("\nK5  dispose() while an ASYNC auto_retry_start subscriber runs (before _retry_abort_event exists)")
    import time
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 1_000  # type: ignore[attr-defined]
    provider = Provider({1: "retry"})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    in_sub = asyncio.Event()
    release_sub = asyncio.Event()

    async def sub(event: object) -> None:
        if type(event).__name__ == "AutoRetryStartEvent" and not in_sub.is_set():
            in_sub.set()
            await release_sub.wait()

    h.subscribe(sub)
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(in_sub.wait(), WAIT)
    print("  in the auto_retry_start subscriber:", _st(h), "| _retry_abort_event:", h._retry_abort_event)
    disposer = asyncio.ensure_future(h.dispose())
    await _spin()
    print("  dispose() started; done =", disposer.done(), "| abort_requested =", h._abort_requested)
    t0 = time.monotonic()
    release_sub.set()
    try:
        await asyncio.wait_for(disposer, WAIT)
        print(f"  dispose() returned {time.monotonic() - t0:.2f} s after the subscriber released")
    except TimeoutError:
        print("  dispose() TIMEOUT")
    try:
        await asyncio.wait_for(first, WAIT)
    except BaseException as exc:  # noqa: BLE001
        print("  #1:", type(exc).__name__, exc)
    print("  provider calls:", provider.calls, "(2 = the re-run went out after dispose()'s abort)",
          "| #1 done:", first.done(), "|", _st(h))


async def armK1t() -> None:
    print("\nK1t threshold compaction of a successful turn: a wait_for_idle() parked before the prompt, seen from session_compact")
    provider = Provider({1: "big"})
    session = await _seeded_session()
    h = AgentHarness(
        AgentHarnessOptions(session=session, stream_fn=provider, _summarizer_override=Summariser())
    )
    h._state.model = Model(id="m20k", context_window=20_000)
    h._state.auto_compaction_enabled = True
    seen: list[str] = []
    first_holder: list[Any] = []

    async def on_compact(ev: Any, *_a: Any) -> None:
        await _spin()
        seen.append(f"reason={ev.reason} {_st(h)} waiter={'RETURNED' if first_holder[0].done() else 'parked'}")

    h.hooks.on("session_compact", on_compact)  # type: ignore[call-overload]
    first = asyncio.ensure_future(h.prompt("first"))
    waiter = asyncio.ensure_future(h.wait_for_idle())
    first_holder.append(waiter)
    await asyncio.wait_for(first, WAIT)
    print("  in session_compact        :", seen)


ARMS = {"K1t": armK1t, "K1": armK1, "K2": armK2, "K3": armK3, "K4": armK4, "K5": armK5}


async def main(argv: list[str]) -> None:
    print("core.py:", core_mod.__file__)
    for name in argv or list(ARMS):
        await ARMS[name]()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
