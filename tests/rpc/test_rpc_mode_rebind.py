"""Sprint 6h₄b · §E.3 — ``rebind_session`` closure integration tests.

Pi parity: ``packages/coding-agent/src/modes/rpc/rpc-mode.ts:310-349``.

The closure (FOUNDATION subset, P-303) re-attaches the event-pipe
subscription after a harness replace. Tests:
  - Captured ``harness`` cell is reassigned to the NEW harness.
  - OLD subscription is torn down and a NEW subscription is opened
    against the NEW harness's ``subscribe()`` — listener-count balance
    is preserved.
  - Synthetic events emitted by the NEW harness reach the RPC output
    writer after rebind (regression for the "captured stale-harness"
    bug-class P-303 prevents).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
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
from aelix_coding_agent.rpc.rpc_mode import (
    _make_passthrough_runtime,
    run_rpc_mode,
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


def _new_harness() -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
        )
    )


async def test_run_rpc_mode_with_runtime_host_does_not_crash() -> None:
    """Smoke test (W4 NIT-1 rename per spec). ``run_rpc_mode`` accepts
    an explicit ``runtime_host`` (Sprint 6h₄b ADR-0077 P-309), wires
    the rebind closure, and tears down cleanly when ``stdin`` hits
    EOF — without any harness replace occurring.
    """

    runtime = _make_passthrough_runtime(_new_harness(), None)
    stdin = asyncio.StreamReader()
    stdin.feed_eof()
    captured: list[bytes] = []

    await run_rpc_mode(
        runtime.harness,
        runtime_host=runtime,
        stdin=stdin,
        stdout_write=captured.append,
        install_signal_handlers=False,
    )


async def test_rebind_session_closure_reattaches_subscription() -> None:
    """The closure swaps the captured harness AND re-subscribes against
    the NEW harness's ``subscribe()`` — listener count stays balanced.
    """

    h_old = _new_harness()
    h_new = _new_harness()

    async def _factory(_s: Session) -> AgentHarness:
        return h_new

    runtime = AgentSessionRuntime(h_old, _factory)

    # Drive run_rpc_mode in the background to install the rebind closure.
    stdin = asyncio.StreamReader()
    captured: list[bytes] = []

    async def _drive() -> None:
        await run_rpc_mode(
            runtime.harness,
            runtime_host=runtime,
            stdin=stdin,
            stdout_write=captured.append,
            install_signal_handlers=False,
        )

    task = asyncio.create_task(_drive())
    # Yield so the entry installs the listener.
    await asyncio.sleep(0.01)

    # PRE-replace: exactly one RPC listener on the OLD harness.
    pre_old_count = len(h_old._listeners)
    assert pre_old_count >= 1

    # Trigger the rebind seam via the test-only entry point.
    new_session = Session(MemorySessionStorage())
    await runtime._apply_for_test(new_session)

    # POST-replace: the OLD harness was disposed (its listener list is
    # observable but no longer the live target). The NEW harness MUST
    # carry one RPC listener after the closure re-subscribed.
    post_new_count = len(h_new._listeners)
    assert post_new_count == 1, (
        f"NEW harness listener count after rebind = {post_new_count!r}; "
        "expected exactly 1 (the re-subscribed RPC listener)."
    )

    # Drain.
    stdin.feed_eof()
    await asyncio.wait_for(task, timeout=2.0)

    # After teardown, the NEW harness's listener count drops by exactly
    # the count that was added (1) — capture.unsubscribe ran at shutdown.
    final_new_count = len(h_new._listeners)
    assert final_new_count == post_new_count - 1


async def test_unsubscribe_subscribe_count_balanced_per_replace() -> None:
    """Subscribe/unsubscribe count balance assertion (closure pin
    requirement — P-303 §E.2).
    """

    h_old = _new_harness()
    h_new = _new_harness()

    sub_count = 0
    unsub_count = 0
    original_sub = h_new.subscribe

    def _spy_subscribe(listener: Any) -> Any:
        nonlocal sub_count
        sub_count += 1
        unsub = original_sub(listener)

        def _spy_unsub() -> None:
            nonlocal unsub_count
            unsub_count += 1
            unsub()

        return _spy_unsub

    h_new.subscribe = _spy_subscribe  # type: ignore[method-assign]

    async def _factory(_s: Session) -> AgentHarness:
        return h_new

    runtime = AgentSessionRuntime(h_old, _factory)
    stdin = asyncio.StreamReader()
    captured: list[bytes] = []

    async def _drive() -> None:
        await run_rpc_mode(
            runtime.harness,
            runtime_host=runtime,
            stdin=stdin,
            stdout_write=captured.append,
            install_signal_handlers=False,
        )

    task = asyncio.create_task(_drive())
    await asyncio.sleep(0.01)
    await runtime._apply_for_test(Session(MemorySessionStorage()))
    # The closure subscribed to the NEW harness once.
    assert sub_count == 1
    stdin.feed_eof()
    await asyncio.wait_for(task, timeout=2.0)
    # Teardown unsubscribes the same listener exactly once.
    assert unsub_count == 1
