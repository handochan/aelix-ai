"""#334 probe — what every entry point meets during prompt()'s tail.

Written by the #334 design lane (``/tmp/334-work/design/tail_probe.py``), committed
by the implementation lane. Its before/after values are in ADR-0023's #334
amendment. Siblings: ``334-arms.py`` (the #321 ARMs' shapes up to where a
second prompt must enter), ``334-critic-probe.py`` (K1-K5), ``334-live.py``,
``334-sabotage.py``.

Run from inside a tree with that tree's interpreter, so the ``core.py`` measured
is the one printed on the first line::

    cd <tree> && uv run --no-sync python .omc/specs/334-tail-probe.py [ARM ...]

Every wait is bounded; the state probed is reached by parking on an
``asyncio.Event`` the harness side sets (a subscriber, a hook, a session read),
never by sleeping. The backoff is the REAL ``_handle_retryable_error`` with the
base delay raised to 60 s, so "in the backoff" means parked in its
``asyncio.wait_for`` — each arm ends it with ``abort_retry()`` (unless the arm
is about what ends it).

ARMs
  T1   RPC ``prompt`` command during #1's backoff (``_handle_prompt``), and
       ``get_state``'s is_streaming / is_compacting at that moment
  T1s  the same prompt command with ``streamingBehavior: "steer"``
  T2   an extension's ``send_message(trigger_turn=True)`` during the backoff
  T3   ``dispose()`` during the backoff: does it wait for #1?
  T4   ``wait_for_idle()`` and ``is_idle`` / the extension ctx ``is_idle()``
       during the backoff
  T5   ``set_thinking_level`` during the backoff: where does the entry land?
  T6   ``navigate_tree`` during the backoff
  T7   a manual ``compact()`` (RPC ``compact``, extension ``ctx.compact``)
       during the backoff
  T8   the threshold-compaction check: the gap between ``_run``'s return and
       ``compact()``'s flip (the branch read), and inside the compaction
       (``session_before_compact``): phase, get_state, wait_for_idle
  T9   the overflow recovery: its boundary read (gap) and its compaction
  T10  ``abort()`` while the overflow compaction runs: does the re-run still
       go to the provider?
  T11  ``append_message`` during the backoff: state or pending queue?
"""

from __future__ import annotations

import asyncio
import contextlib
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
    """Scripted per call: "retry" fails retryably, "overflow" overflows, "big"
    answers over a 20k model's threshold, "park" waits for release; else ok."""

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


def _override() -> Any:
    async def fn(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(
            summary="S", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=1, details={}
        )

    return fn


async def _seeded_session() -> Session:
    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    return session


async def _in_backoff(script: dict[int, str], *, seed: bool = False) -> tuple[AgentHarness, Provider, asyncio.Future[Any], Session]:
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 60_000  # type: ignore[attr-defined]
    provider = Provider(script)
    session = await _seeded_session() if seed else Session(MemorySessionStorage())
    h = AgentHarness(
        AgentHarnessOptions(session=session, stream_fn=provider, _summarizer_override=_override())
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    started = asyncio.Event()

    def watch(event: object) -> None:
        if type(event).__name__ == "AutoRetryStartEvent":
            started.set()

    h.subscribe(watch)
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(started.wait(), WAIT)
    return h, provider, first, session


async def _finish(h: AgentHarness, first: asyncio.Future[Any]) -> str:
    h.abort_retry()
    try:
        await asyncio.wait_for(first, WAIT)
        return "returned"
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


async def armT1(steer: bool = False) -> None:
    from aelix_coding_agent.rpc.rpc_mode import _handle_get_state, _handle_prompt
    from aelix_coding_agent.rpc.rpc_types import RpcCommandGetState, RpcCommandPrompt

    h, provider, first, _ = await _in_backoff({1: "retry", 2: "park"})
    label = "T1s RPC prompt {streamingBehavior: steer}" if steer else "T1  RPC prompt command"
    print(f"\n{label} during #1's backoff")
    print("  #1 in its backoff       :", _st(h))
    state = await _handle_get_state(h, RpcCommandGetState(id="s"))
    data = state.data  # type: ignore[attr-defined]
    print(f"  get_state               : isStreaming={data.get('isStreaming')} isCompacting={data.get('isCompacting')}")
    cmd = RpcCommandPrompt(message="second", id="p2", streaming_behavior="steer" if steer else None)
    resp = await _handle_prompt(h, cmd)
    print("  prompt response         :", type(resp).__name__, getattr(resp, "error", "") or "")
    reached = await _wait_for(provider.parked[2])
    print("  #2 reached the provider :", reached, f"(provider calls={provider.calls})")
    print("  steering queue          :", _texts(h._steering_queue._messages))
    if reached:
        print("  while #2 is mid-turn    :", _st(h))
        provider.release[2].set()
    for t in list(h._pending_tasks):
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(t, WAIT)
    print("  #1 ended                :", await _finish(h, first))
    print("  provider saw            :", provider.seen)
    print("  after                   :", _st(h))


async def _wait_for(ev: asyncio.Event, timeout: float = 0.5) -> bool:
    try:
        await asyncio.wait_for(ev.wait(), timeout)
        return True
    except TimeoutError:
        return False


async def armT2() -> None:
    h, provider, first, _ = await _in_backoff({1: "retry", 2: "park"})
    print("\nT2  extension send_message(trigger_turn=True) during #1's backoff")
    print("  #1 in its backoff       :", _st(h))
    h._action_send_message(UserMessage(content=[TextContent(text="from-ext")]), trigger_turn=True)
    reached = await _wait_for(provider.parked[2])
    print("  a prompt reached the provider:", reached, f"(provider calls={provider.calls})")
    print("  next_turn queue         :", _texts(h._next_turn_queue))
    if reached:
        print("  while it is mid-turn    :", _st(h))
        provider.release[2].set()
    for t in list(h._pending_tasks):
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(t, WAIT)
    print("  #1 ended                :", await _finish(h, first))
    print("  provider saw            :", provider.seen)


async def armT3() -> None:
    h, provider, first, _ = await _in_backoff({1: "retry"})
    print("\nT3  dispose() during #1's backoff")
    print("  #1 in its backoff       :", _st(h))
    try:
        await asyncio.wait_for(h.dispose(), WAIT)
        print("  dispose()               : returned; #1 done:", first.done(), "; retry_aborted:", h._state.retry_aborted)
    except TimeoutError:
        print("  dispose()               : TIMEOUT")
    print("  #1 ended                :", await _finish(h, first))


async def armT4() -> None:
    h, provider, first, _ = await _in_backoff({1: "retry"})
    print("\nT4  wait_for_idle() / is_idle during #1's backoff")
    ctx = h._make_context()
    print("  #1 in its backoff       :", _st(h), f"is_idle={h.is_idle} ctx.is_idle()={ctx.is_idle()}")
    waiter = asyncio.ensure_future(h.wait_for_idle())
    done = await _wait_for_done(waiter)
    print("  wait_for_idle()         :", "returned while #1 is in its backoff" if done else "parked")
    print("  #1 ended                :", await _finish(h, first))
    if not done:
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(waiter, WAIT)
        print("  waiter after #1         :", "woke" if waiter.done() else "still parked")


async def _wait_for_done(fut: asyncio.Future[Any], timeout: float = 0.2) -> bool:
    done, _ = await asyncio.wait({fut}, timeout=timeout)
    return fut in done


async def armT5() -> None:
    h, provider, first, session = await _in_backoff({1: "retry"})
    print("\nT5  set_thinking_level('high') during #1's backoff")

    async def levels() -> list[str]:
        return [
            getattr(e, "thinking_level", "?")
            for e in await session.get_branch()
            if getattr(e, "type", "") == "thinking_level_change"
        ]

    await h.set_thinking_level("high")
    print("  right after the call    : session entries", await levels(), "pending", [type(p).__name__ for p in h._pending_session_writes])
    print("  #1 ended                :", await _finish(h, first))
    print("  after #1                : session entries", await levels(), "pending", [type(p).__name__ for p in h._pending_session_writes])


async def armT6() -> None:
    h, provider, first, session = await _in_backoff({1: "retry"})
    print("\nT6  navigate_tree() during #1's backoff")
    entries = await session.get_branch()
    target = entries[0].id
    try:
        await asyncio.wait_for(h.navigate_tree(target), WAIT)
        print("  navigate_tree()         : ACCEPTED (ran to completion);", _st(h))
    except AgentHarnessError as exc:
        print("  navigate_tree()         :", f"AgentHarnessError({exc.code!r}) {exc}")
    print("  #1 ended                :", await _finish(h, first))


async def armT7() -> None:
    from aelix_coding_agent.rpc.rpc_mode import _handle_compact
    from aelix_coding_agent.rpc.rpc_types import RpcCommandCompact

    h, provider, first, session = await _in_backoff({1: "retry"}, seed=True)
    print("\nT7  a manual compact() during #1's backoff")
    resp = await _handle_compact(h, RpcCommandCompact(id="c"))
    print("  RPC compact             :", type(resp).__name__, getattr(resp, "error", "") or "(compacted)")
    print("  after it                :", _st(h))
    print("  #1 ended                :", await _finish(h, first))


async def armT8() -> None:
    provider = Provider({1: "big"})
    session = await _seeded_session()
    h = AgentHarness(
        AgentHarnessOptions(session=session, stream_fn=provider, _summarizer_override=_override())
    )
    h._state.model = Model(id="m20k", context_window=20_000)
    h._state.auto_compaction_enabled = True
    from aelix_coding_agent.rpc.rpc_mode import _handle_get_state
    from aelix_coding_agent.rpc.rpc_types import RpcCommandGetState

    gap = asyncio.Event()
    release_gap = asyncio.Event()
    real_get_branch = session.get_branch

    async def get_branch(*a: Any, **k: Any) -> Any:
        if h.phase == "idle" and not gap.is_set() and first_started.is_set():
            gap.set()
            await release_gap.wait()
        return await real_get_branch(*a, **k)

    session.get_branch = get_branch  # type: ignore[method-assign]
    in_hook = asyncio.Event()
    release_hook = asyncio.Event()

    async def before_compact(event: Any, *_a: Any) -> None:
        in_hook.set()
        await release_hook.wait()

    h.hooks.on("session_before_compact", before_compact)  # type: ignore[call-overload]
    first_started = asyncio.Event()
    first_started.set()
    print("\nT8  the threshold-compaction check in #1's tail")
    first = asyncio.ensure_future(h.prompt("first"))
    gap_seen = await _wait_for(gap, WAIT)
    if gap_seen:
        state = await _handle_get_state(h, RpcCommandGetState(id="s"))
        d = state.data  # type: ignore[attr-defined]
        waiter = asyncio.ensure_future(h.wait_for_idle())
        print("  the branch read (gap)   :", _st(h), f"isStreaming={d.get('isStreaming')}",
              "wait_for_idle():", "returned" if await _wait_for_done(waiter) else "parked")
        release_gap.set()
    else:
        print("  the branch read (gap)   : never read with the phase idle")
    await asyncio.wait_for(in_hook.wait(), WAIT)
    state = await _handle_get_state(h, RpcCommandGetState(id="s"))
    d = state.data  # type: ignore[attr-defined]
    waiter2 = asyncio.ensure_future(h.wait_for_idle())
    print("  in session_before_compact:", _st(h), f"isStreaming={d.get('isStreaming')} isCompacting={d.get('isCompacting')}",
          "wait_for_idle():", "returned" if await _wait_for_done(waiter2) else "parked")
    release_hook.set()
    try:
        await asyncio.wait_for(first, WAIT)
        print("  #1                      : returned;", _st(h))
    except BaseException as exc:  # noqa: BLE001
        print("  #1                      :", f"{type(exc).__name__}: {exc}")


async def armT9(abort: bool = False) -> None:
    provider = Provider({1: "overflow"})
    session = await _seeded_session()
    h = AgentHarness(
        AgentHarnessOptions(session=session, stream_fn=provider, _summarizer_override=_override())
    )
    h._state.model = SimpleNamespace(context_window=200_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    h._state.auto_retry_enabled = False
    gap = asyncio.Event()
    release_gap = asyncio.Event()
    real_get_branch = session.get_branch

    async def get_branch(*a: Any, **k: Any) -> Any:
        if h.phase == "idle" and not gap.is_set():
            gap.set()
            await release_gap.wait()
        return await real_get_branch(*a, **k)

    session.get_branch = get_branch  # type: ignore[method-assign]
    in_hook = asyncio.Event()
    release_hook = asyncio.Event()

    async def before_compact(event: Any, *_a: Any) -> None:
        in_hook.set()
        await release_hook.wait()

    h.hooks.on("session_before_compact", before_compact)  # type: ignore[call-overload]
    title = "T10 abort() while the overflow compaction runs" if abort else "T9  overflow recovery in #1's tail"
    print(f"\n{title}")
    first = asyncio.ensure_future(h.prompt("first"))
    if await _wait_for(gap, WAIT):
        print("  overflow's boundary read (gap):", _st(h))
        release_gap.set()
    else:
        print("  overflow's boundary read (gap): never read with the phase idle")
    await asyncio.wait_for(in_hook.wait(), WAIT)
    print("  in session_before_compact    :", _st(h))
    if abort:
        await h.abort()
        print("  abort() returned             : abort_requested =", h._abort_requested)
    release_hook.set()
    try:
        await asyncio.wait_for(first, WAIT)
        print("  #1                           : returned;", _st(h))
    except BaseException as exc:  # noqa: BLE001
        print("  #1                           :", f"{type(exc).__name__}: {exc}")
    print("  provider calls               :", provider.calls, "(2 = the re-run went to the provider)")


async def armT11() -> None:
    h, provider, first, session = await _in_backoff({1: "retry"})
    print("\nT11 append_message during #1's backoff")
    n_state = len(h._state.messages)
    await h.append_message(UserMessage(content=[TextContent(text="appended")]))
    print("  state.messages grew by  :", len(h._state.messages) - n_state,
          "; pending:", [type(p).__name__ for p in h._pending_session_writes])
    print("  #1 ended                :", await _finish(h, first))
    user_lines = [
        t for e in await session.get_branch() if getattr(e, "type", "") == "message"
        for t in _texts([e.message])
    ]
    print("  user/assistant text in the session:", user_lines,
          "; pending:", [type(p).__name__ for p in h._pending_session_writes])


ARMS = {
    "T1": armT1, "T1s": lambda: armT1(steer=True), "T2": armT2, "T3": armT3, "T4": armT4,
    "T5": armT5, "T6": armT6, "T7": armT7, "T8": armT8, "T9": armT9,
    "T10": lambda: armT9(abort=True), "T11": armT11,
}


async def main(argv: list[str]) -> None:
    print("core.py:", core_mod.__file__)
    for name in argv or list(ARMS):
        await ARMS[name]()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
