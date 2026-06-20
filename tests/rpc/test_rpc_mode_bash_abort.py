"""Sprint 3 cooperative-abort — RPC _handle_bash cancellation tests.

Covers:
- ``cancelled=True`` in the response when :meth:`AgentHarness.abort_bash`
  fires while the exec is in flight.
- ``cancelled=False`` on normal (non-aborted) completion.
- The AbortSignal is unregistered from the harness after the exec (no leak).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import _handle_bash
from aelix_coding_agent.rpc.rpc_types import RpcCommandBash, RpcSuccessResponse


def _stream_fn() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _make_harness(tmp_path) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream_fn(),
            cwd=str(tmp_path),
        )
    )


async def test_handle_bash_cancelled_true_when_abort_bash_fires(tmp_path) -> None:
    """``cancelled`` is True when harness.abort_bash() fires during exec."""

    harness = _make_harness(tmp_path)
    cmd = RpcCommandBash(command="sleep 30", id="b1")

    handle_task = asyncio.create_task(_handle_bash(harness, cmd))

    # Give the exec time to start, then fire abort_bash.
    await asyncio.sleep(0.15)
    harness.abort_bash()

    response = await asyncio.wait_for(handle_task, timeout=5.0)
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "bash"
    data = response.data
    assert isinstance(data, dict)
    assert data["cancelled"] is True
    assert data["exitCode"] is None

    await harness.dispose()


async def test_handle_bash_cancelled_false_on_normal_completion(tmp_path) -> None:
    """``cancelled`` is False when the command exits normally."""

    harness = _make_harness(tmp_path)
    cmd = RpcCommandBash(command="echo hello", id="b2")

    response = await asyncio.wait_for(_handle_bash(harness, cmd), timeout=5.0)
    assert isinstance(response, RpcSuccessResponse)
    data = response.data
    assert isinstance(data, dict)
    assert data["cancelled"] is False
    assert data["exitCode"] == 0
    assert "hello" in data["output"]

    await harness.dispose()


async def test_handle_bash_signal_unregistered_after_exec(tmp_path) -> None:
    """The AbortSignal is unregistered from the harness after normal exec."""

    harness = _make_harness(tmp_path)
    assert len(harness._active_bash_signals) == 0

    cmd = RpcCommandBash(command="echo check", id="b3")
    await asyncio.wait_for(_handle_bash(harness, cmd), timeout=5.0)

    # Registry must be empty — no signal leak.
    assert len(harness._active_bash_signals) == 0

    await harness.dispose()


async def test_handle_bash_signal_unregistered_after_abort(tmp_path) -> None:
    """The AbortSignal is unregistered even when the exec is aborted."""

    harness = _make_harness(tmp_path)
    cmd = RpcCommandBash(command="sleep 30", id="b4")

    handle_task = asyncio.create_task(_handle_bash(harness, cmd))
    await asyncio.sleep(0.15)
    harness.abort_bash()

    await asyncio.wait_for(handle_task, timeout=5.0)

    # Signal must be cleaned up regardless of abort path.
    assert len(harness._active_bash_signals) == 0

    await harness.dispose()


async def test_handle_bash_signal_unregistered_after_exec_raises(
    tmp_path, monkeypatch
) -> None:
    """The AbortSignal is unregistered even when ops.exec raises an exception.

    This covers the ``finally: harness.unregister_bash_signal(sig)`` on the
    exception path — a leaked signal after an unexpected exec failure would
    cause a later ``abort_bash`` to fire a stale (already-satisfied) signal.
    """
    class _FailingOps:
        async def exec(self, *args, **kwargs):
            raise RuntimeError("simulated exec failure")

    # Patch create_local_bash_operations inside rpc_mode's local scope.
    # _handle_bash imports it at call time (inside the function body), so
    # we patch the module it imports from.
    import aelix_coding_agent.tools.bash as _bash_mod

    monkeypatch.setattr(_bash_mod, "create_local_bash_operations", lambda **_kw: _FailingOps())

    # Also patch the reference imported inside _handle_bash's function body.
    # Because _handle_bash does a local ``from ... import create_local_bash_operations``
    # at call time we need to patch it in the source module so the import resolves
    # to our fake.
    harness = _make_harness(tmp_path)
    assert len(harness._active_bash_signals) == 0

    cmd = RpcCommandBash(command="echo irrelevant", id="b5")

    with pytest.raises(RuntimeError, match="simulated exec failure"):
        await asyncio.wait_for(_handle_bash(harness, cmd), timeout=5.0)

    # The finally-unregister must have run — no signal leak.
    assert len(harness._active_bash_signals) == 0

    await harness.dispose()
