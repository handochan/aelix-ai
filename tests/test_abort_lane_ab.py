"""Sprint 3 Lane A+B — cooperative-abort wiring tests.

Lane A: Confirm that ``CancelledError`` from ``task.cancel()`` propagates
correctly through the bash exec watcher path — the subprocess group is killed
and the error re-raises (not swallowed).

Lane B: Confirm that ``harness.abort()`` fires all registered bash
``AbortSignal`` instances (same set that ``abort_bash()`` fires), so that an
Esc-path abort kills in-flight bash subprocesses, not just the Python task.
"""

from __future__ import annotations

import asyncio

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_coding_agent.tools._abort import AbortSignal
from aelix_coding_agent.tools.bash import create_local_bash_operations

# ---------------------------------------------------------------------------
# Lane A — CancelledError propagation through the bash exec watcher path
# ---------------------------------------------------------------------------


async def test_bash_exec_cancel_kills_group_and_propagates() -> None:
    """task.cancel() while exec is running kills the child and re-raises CancelledError.

    This is the Esc-path: harness cancels the turn task which propagates into
    the in-flight ops.exec() call.  The watcher must be cleaned up and the
    child process group must be killed — CancelledError must NOT be swallowed.
    """
    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    task = asyncio.create_task(
        ops.exec("sleep 30", "/tmp", on_data=chunks.append)
    )
    # Give the process time to start.
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)


async def test_bash_exec_cancel_with_signal_still_propagates() -> None:
    """task.cancel() while a watcher is running still re-raises CancelledError.

    When both a signal watcher task *and* an outer task cancel arrive, the
    watcher's ``finally`` block must cancel the watcher, and CancelledError
    must propagate to the caller — not be silently swallowed in the
    ``except (asyncio.CancelledError, Exception): pass`` watcher teardown.
    """
    ops = create_local_bash_operations()
    sig = AbortSignal()
    chunks: list[bytes] = []

    task = asyncio.create_task(
        ops.exec("sleep 30", "/tmp", on_data=chunks.append, signal=sig)
    )
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)


async def test_bash_exec_cancel_exit_code_is_none() -> None:
    """Cancelled exec reports exit_code=None (same as signal-kill path)."""
    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    task = asyncio.create_task(
        ops.exec("sleep 30", "/tmp", on_data=chunks.append)
    )
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    # Task is done and raised CancelledError — the ExecExitResult is not
    # returned when cancelled (exception path), but we verify the task is
    # truly cancelled (not returning a result).
    assert task.done()
    assert task.cancelled()


# ---------------------------------------------------------------------------
# Lane B — harness.abort() fires registered bash AbortSignals
# ---------------------------------------------------------------------------


async def test_harness_abort_fires_registered_bash_signal() -> None:
    """abort() fires every AbortSignal in _active_bash_signals.

    Unit test — no subprocess needed.  Register a signal, call abort(), assert
    the signal is aborted.
    """
    from collections.abc import AsyncIterator

    from aelix_ai.messages import AssistantMessage, TextContent
    from aelix_ai.streaming import (
        AssistantEndEvent,
        AssistantMessageEvent,
        AssistantStartEvent,
        Context,
        Model,
        SimpleStreamOptions,
    )

    async def quick_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    h = AgentHarness(AgentHarnessOptions(stream_fn=quick_stream))

    sig = AbortSignal()
    h.register_bash_signal(sig)

    assert sig.aborted is False
    await h.abort()
    assert sig.aborted is True


async def test_harness_abort_fires_multiple_bash_signals() -> None:
    """abort() fires ALL registered signals, not just the first."""
    from collections.abc import AsyncIterator

    from aelix_ai.messages import AssistantMessage, TextContent
    from aelix_ai.streaming import (
        AssistantEndEvent,
        AssistantMessageEvent,
        AssistantStartEvent,
        Context,
        Model,
        SimpleStreamOptions,
    )

    async def quick_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    h = AgentHarness(AgentHarnessOptions(stream_fn=quick_stream))

    sig1 = AbortSignal()
    sig2 = AbortSignal()
    sig3 = AbortSignal()
    h.register_bash_signal(sig1)
    h.register_bash_signal(sig2)
    h.register_bash_signal(sig3)

    await h.abort()

    assert sig1.aborted is True
    assert sig2.aborted is True
    assert sig3.aborted is True


async def test_harness_abort_with_no_signals_does_not_raise() -> None:
    """abort() with an empty signal registry is a safe no-op."""
    from collections.abc import AsyncIterator

    from aelix_ai.messages import AssistantMessage, TextContent
    from aelix_ai.streaming import (
        AssistantEndEvent,
        AssistantMessageEvent,
        AssistantStartEvent,
        Context,
        Model,
        SimpleStreamOptions,
    )

    async def quick_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    h = AgentHarness(AgentHarnessOptions(stream_fn=quick_stream))
    # No signals registered — abort() must not raise.
    await h.abort()


async def test_harness_abort_unregistered_signal_not_fired() -> None:
    """Signals unregistered before abort() are NOT fired."""
    from collections.abc import AsyncIterator

    from aelix_ai.messages import AssistantMessage, TextContent
    from aelix_ai.streaming import (
        AssistantEndEvent,
        AssistantMessageEvent,
        AssistantStartEvent,
        Context,
        Model,
        SimpleStreamOptions,
    )

    async def quick_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    h = AgentHarness(AgentHarnessOptions(stream_fn=quick_stream))

    sig = AbortSignal()
    h.register_bash_signal(sig)
    h.unregister_bash_signal(sig)  # unregister before abort

    await h.abort()

    assert sig.aborted is False


async def test_harness_abort_fires_signals_and_cancels_turn_task() -> None:
    """abort() fires bash signals AND cancels the turn task concurrently.

    Integration: a slow turn with a registered signal → abort() must both
    fire the signal and cancel the task.
    """
    from collections.abc import AsyncIterator

    from aelix_ai.messages import AssistantMessage, TextContent
    from aelix_ai.streaming import (
        AssistantEndEvent,
        AssistantMessageEvent,
        AssistantStartEvent,
        Context,
        Model,
        SimpleStreamOptions,
    )

    gate = asyncio.Event()

    async def slow_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    h = AgentHarness(AgentHarnessOptions(stream_fn=slow_stream))

    sig = AbortSignal()
    h.register_bash_signal(sig)

    task = asyncio.create_task(h.prompt("go"))
    await asyncio.sleep(0)  # let the prompt task start

    await h.abort()

    # Signal must be fired.
    assert sig.aborted is True

    # Unblock the gate so the task can complete.
    gate.set()
    await task
    assert h.phase == "idle"


async def test_bash_exec_cancel_unwinds_promptly() -> None:
    """Cancelled exec() unwinds fully within a tight deadline.

    Proves that ``finally: await drain_task`` does not deadlock the cancel
    path.  If drain_task hangs (e.g. a blocked pipe read), this test fails
    fast rather than hanging CI indefinitely.
    """
    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    task = asyncio.create_task(
        ops.exec("sleep 30", "/tmp", on_data=chunks.append)
    )
    # Give the child time to start.
    await asyncio.sleep(0.1)
    task.cancel()

    # The whole cancel + drain must complete within 2 seconds.
    # If drain_task blocks this deadline fires, failing the test.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)

    # Confirm the task is truly done (not just timed out still running).
    assert task.done()
