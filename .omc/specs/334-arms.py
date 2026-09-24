"""#334 design probe — the #321 ARMs' shapes, run up to the point where the
second ``prompt()`` must enter, plus the single-prompt halves of their faults.

    cd <tree> && [PYTHONPATH=<scratch src>] uv run --no-sync python .omc/specs/334-arms.py [ARM ...]

Every wait bounded; every park an event the harness side sets. The gated
backoff (``Gate``) replaces ``_handle_retryable_error`` the way the #321 tests'
``_Backoff`` does, so a re-run follows in sequence, not in a race.

ARMs
  A7    #1 in its REAL backoff: phase / wait_for_idle; #2 prompt(); then #1 is
        cancelled: phase, next prompt()
  A12   #1 in its threshold-compaction check (the tail's first branch read,
        parked): #2 prompt(); then #1 finishes: did its compact() raise?
  A13o  #1 in its overflow recovery's boundary read: #2 prompt(); #1 finishes
  A18   #1 in its gated backoff; #2 prompt() (18/19/20/23/24 all need #2 to get
        in here); #1's re-run then runs: what did the provider see?
  A15   #1 in its gated backoff; #2 prompt() (15/16/17/21/22 need #2 here too)
  R13c/R13r  single prompt: #1's RETRY re-run is cancelled / raises in
        build_context(): phase, wait_for_idle(), next prompt(), dispose()
  O13c/O13r  the same at the OVERFLOW re-run
  F21r  single prompt: #1's FIRST build_context() raises (ARM 21's fault with
        nothing else in flight; ARM 4 is the cancel)
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from typing import Any

import aelix_agent_core.harness.core as core_mod
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
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

W = 5.0
_OVERFLOW = "prompt is too long: 213462 tokens > 200000 maximum"


def _texts(ms: Any) -> list[str]:
    return [c.text for m in ms for c in getattr(m, "content", []) if hasattr(c, "text")]


def st(h: AgentHarness) -> str:
    return f"phase={h.phase!r} idle_event={h._idle_event.is_set()}"


class Provider:
    def __init__(self, script: dict[int, str]) -> None:
        self.script, self.calls, self.seen = script, 0, []
        self.parked = {n: asyncio.Event() for n in range(1, 10)}
        self.release = {n: asyncio.Event() for n in range(1, 10)}

    async def __call__(self, model: Model, context: Context, options: SimpleStreamOptions) -> AsyncIterator[AssistantMessageEvent]:
        self.calls += 1
        n = self.calls
        self.seen.append([t for t in _texts(context.messages) if not t.startswith("x" * 10)])
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
        yield AssistantEndEvent(message=AssistantMessage(
            content=[TextContent(text=f"answer {n}")], stop_reason="end_turn",
            usage={"total_tokens": 5_000} if kind == "big" else None))


class Gate:
    def __init__(self, h: AgentHarness, verdicts: list[bool]) -> None:
        self.h, self.verdicts, self.n = h, verdicts, 0
        self.entered = [asyncio.Event() for _ in verdicts]
        self.release = [asyncio.Event() for _ in verdicts]
        h._handle_retryable_error = self  # type: ignore[method-assign]

    async def __call__(self, message: Any) -> bool:
        i = self.n
        self.n += 1
        ms = self.h._state.messages
        if ms and isinstance(ms[-1], AssistantMessage):
            ms.pop()
        self.entered[i].set()
        await self.release[i].wait()
        return self.verdicts[i]


def _override() -> Any:
    async def fn(_m: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(summary="S", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=1, details={})
    return fn


async def seeded() -> Session:
    s = Session(MemorySessionStorage())
    for _ in range(6):
        await s.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    return s


async def try_prompt(h: AgentHarness, text: str, provider: Provider) -> tuple[str, asyncio.Future[Any] | None]:
    fut = asyncio.ensure_future(h.prompt(text))
    done, _ = await asyncio.wait({fut}, timeout=0.2)
    if fut in done and fut.exception() is not None:
        e = fut.exception()
        code = getattr(e, "code", "")
        return f"refused: {type(e).__name__}({code!r})", None
    return f"ACCEPTED (provider calls now {provider.calls})", fut


async def waiter_state(h: AgentHarness) -> str:
    w = asyncio.ensure_future(h.wait_for_idle())
    done, _ = await asyncio.wait({w}, timeout=0.2)
    if w in done:
        return "wait_for_idle() returned"
    w.cancel()
    return "wait_for_idle() parked"


async def drain(*futs: asyncio.Future[Any] | None) -> list[str]:
    out = []
    for f in futs:
        if f is None:
            continue
        try:
            await asyncio.wait_for(f, W)
            out.append("returned")
        except BaseException as e:  # noqa: BLE001
            out.append(f"{type(e).__name__}: {e}")
    return out


async def A7() -> None:
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 60_000  # type: ignore[attr-defined]
    p = Provider({1: "retry", 2: "park"})
    h = AgentHarness(AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=p))
    h._state.auto_retry_enabled, h._state.auto_compaction_enabled = True, False
    started = asyncio.Event()
    h.subscribe(lambda e: started.set() if type(e).__name__ == "AutoRetryStartEvent" else None)
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(started.wait(), W)
    print("\nA7  #1 in its REAL backoff")
    print("  #1 in its backoff  :", st(h), "|", await waiter_state(h))
    res, second = await try_prompt(h, "second", p)
    print("  #2 prompt()        :", res)
    first.cancel()
    print("  #1 cancelled       :", (await drain(first))[0], "|", st(h))
    if second is not None:
        p.release[2].set()
        await drain(second)
    res3, third = await try_prompt(h, "third", p)
    print("  next prompt()      :", res3)
    for n in range(1, 5):
        p.release[n].set()
    await drain(third)
    print("  after              :", st(h))


async def A12() -> None:
    p = Provider({1: "big", 2: "park"})
    s = await seeded()
    h = AgentHarness(AgentHarnessOptions(session=s, stream_fn=p, _summarizer_override=_override()))
    h._state.model = Model(id="m20k", context_window=20_000)
    h._state.auto_compaction_enabled = True
    settled = {"on": False}

    async def on_settled(*_a: Any) -> None:
        settled["on"] = True

    h.hooks.on("settled", on_settled)  # type: ignore[call-overload]
    gap, release = asyncio.Event(), asyncio.Event()
    real = s.get_branch

    async def get_branch(*a: Any, **k: Any) -> Any:
        if settled["on"] and not gap.is_set():
            gap.set()
            await release.wait()
        return await real(*a, **k)

    s.get_branch = get_branch  # type: ignore[method-assign]
    compactions: list[str] = []
    h.hooks.on("session_compact", lambda ev, *_: compactions.append(ev.reason))  # type: ignore[call-overload]
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(gap.wait(), W)
    print("\nA12 #1 in its threshold-compaction check (the tail's branch read)")
    print("  #1 in the check    :", st(h), "|", await waiter_state(h))
    res, second = await try_prompt(h, "second", p)
    print("  #2 prompt()        :", res)
    if second is not None:
        await asyncio.wait_for(p.parked[2].wait(), W)
    release.set()
    print("  #1                 :", (await drain(first))[0], "| compactions:", compactions)
    if second is not None:
        p.release[2].set()
        print("  #2                 :", (await drain(second))[0])
    print("  after              :", st(h))


async def A13o() -> None:
    p = Provider({1: "overflow"})
    s = await seeded()
    h = AgentHarness(AgentHarnessOptions(session=s, stream_fn=p, _summarizer_override=_override()))
    h._state.model = Model(id="m200k", context_window=200_000)
    h._state.auto_compaction_enabled, h._state.auto_retry_enabled = True, False
    settled = {"on": False}

    async def on_settled(*_a: Any) -> None:
        settled["on"] = True

    h.hooks.on("settled", on_settled)  # type: ignore[call-overload]
    gap, release = asyncio.Event(), asyncio.Event()
    real = s.get_branch

    async def get_branch(*a: Any, **k: Any) -> Any:
        if settled["on"] and not gap.is_set():
            gap.set()
            await release.wait()
        return await real(*a, **k)

    s.get_branch = get_branch  # type: ignore[method-assign]
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(gap.wait(), W)
    print("\nA13o #1 in its overflow recovery's boundary read")
    print("  #1 in the read     :", st(h), "|", await waiter_state(h))
    res, second = await try_prompt(h, "second", p)
    print("  #2 prompt()        :", res)
    release.set()
    print("  #1                 :", (await drain(first))[0])
    await drain(second)
    print("  provider saw       :", p.seen)
    print("  after              :", st(h))


async def A18(label: str = "A18") -> None:
    p = Provider({1: "retry"})
    h = AgentHarness(AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=p))
    h._state.auto_retry_enabled, h._state.auto_compaction_enabled = True, False
    g = Gate(h, [True])
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(g.entered[0].wait(), W)
    print(f"\n{label} #1 in its gated backoff (where 15-24 need #2 to enter)")
    print("  #1 in its backoff  :", st(h), "|", await waiter_state(h))
    res, second = await try_prompt(h, "second", p)
    print("  #2 prompt()        :", res)
    g.release[0].set()
    print("  #1                 :", (await drain(first))[0])
    await drain(second)
    print("  provider saw       :", p.seen, "(call 2 = #1's re-run)")
    print("  after              :", st(h))


def _fault(s: Session, fault: str) -> tuple[Any, asyncio.Event]:
    real = s.build_context
    armed = {"on": False}
    reached = asyncio.Event()

    async def bc() -> Any:
        if armed["on"]:
            armed["on"] = False
            reached.set()
            if fault == "raise":
                raise RuntimeError("session storage unavailable")
            await asyncio.Event().wait()
        return await real()

    s.build_context = bc  # type: ignore[method-assign]
    return (lambda: armed.__setitem__("on", True)), reached


async def single(site: str, fault: str) -> None:
    s = await seeded() if site == "overflow" else Session(MemorySessionStorage())
    p = Provider({1: site})
    h = AgentHarness(AgentHarnessOptions(session=s, stream_fn=p, _summarizer_override=_override()))
    arm, reached = _fault(s, fault)
    if site == "retry":
        h._state.auto_retry_enabled, h._state.auto_compaction_enabled = True, False
        g = Gate(h, [True])
        first = asyncio.ensure_future(h.prompt("first"))
        await asyncio.wait_for(g.entered[0].wait(), W)
        arm()
        g.release[0].set()
    else:
        h._state.model = Model(id="m200k", context_window=200_000)
        h._state.auto_retry_enabled, h._state.auto_compaction_enabled = False, True
        in_hook, go = asyncio.Event(), asyncio.Event()

        async def bc_hook(*_a: Any) -> None:
            in_hook.set()
            await go.wait()

        h.hooks.on("session_before_compact", bc_hook)  # type: ignore[call-overload]
        first = asyncio.ensure_future(h.prompt("first"))
        await asyncio.wait_for(in_hook.wait(), W)
        arm()
        go.set()
    name = ("R" if site == "retry" else "O") + "13" + fault[0]
    print(f"\n{name} single prompt: the {site.upper()} re-run {'is cancelled' if fault == 'cancel' else 'raises'} in build_context()")
    await asyncio.wait_for(reached.wait(), W)
    if fault == "cancel":
        first.cancel()
    print("  #1                 :", (await drain(first))[0])
    print("  after              :", st(h), "|", await waiter_state(h))
    res, nxt = await try_prompt(h, "next", p)
    print("  next prompt()      :", res.split(" (")[0])
    await drain(nxt)
    try:
        await asyncio.wait_for(h.dispose(), W)
        print("  dispose()          : returned")
    except TimeoutError:
        print("  dispose()          : TIMEOUT")


async def F21r() -> None:
    s = Session(MemorySessionStorage())
    p = Provider({})
    h = AgentHarness(AgentHarnessOptions(session=s, stream_fn=p))
    arm, _ = _fault(s, "raise")
    arm()
    print("\nF21r single prompt: the FIRST build_context() raises")
    print("  #1                 :", (await drain(asyncio.ensure_future(h.prompt("first"))))[0])
    print("  after              :", st(h), "|", await waiter_state(h))
    res, nxt = await try_prompt(h, "next", p)
    print("  next prompt()      :", res.split(" (")[0])
    await drain(nxt)


ARMS: dict[str, Any] = {
    "A7": A7, "A12": A12, "A13o": A13o, "A18": A18, "A15": lambda: A18("A15"),
    "R13c": lambda: single("retry", "cancel"), "R13r": lambda: single("retry", "raise"),
    "O13c": lambda: single("overflow", "cancel"), "O13r": lambda: single("overflow", "raise"),
    "F21r": F21r,
}


async def main(argv: list[str]) -> None:
    print("core.py:", core_mod.__file__)
    for name in argv or list(ARMS):
        await ARMS[name]()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
