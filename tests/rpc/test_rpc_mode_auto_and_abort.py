"""Sprint 6h₂ (ADR-0071, P-249/P-250) — auto-mode + abort RPC handlers.

Pi parity:

- ``rpc-mode.ts:603-606`` ``set_auto_compaction``
- ``rpc-mode.ts:614-617`` ``set_auto_retry``
- ``rpc-mode.ts:619-622`` ``abort_retry``
- ``rpc-mode.ts:632-635`` ``abort_bash``

Each handler is a thin wrapper around the corresponding harness setter
and returns the Pi-shape ``RpcSuccessResponse``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

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
from aelix_coding_agent.rpc.rpc_mode import (
    _handle_abort_bash,
    _handle_abort_retry,
    _handle_set_auto_compaction,
    _handle_set_auto_retry,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandAbortBash,
    RpcCommandAbortRetry,
    RpcCommandSetAutoCompaction,
    RpcCommandSetAutoRetry,
    RpcSuccessResponse,
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
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _make_harness() -> AgentHarness:
    return AgentHarness(AgentHarnessOptions(stream_fn=_stream()))


async def test_set_auto_compaction_toggles_state() -> None:
    h = _make_harness()
    try:
        assert h.auto_compaction_enabled is True
        cmd = RpcCommandSetAutoCompaction(enabled=False, id="r1")
        response = await _handle_set_auto_compaction(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "set_auto_compaction"
        assert response.id == "r1"
        assert h.auto_compaction_enabled is False

        cmd2 = RpcCommandSetAutoCompaction(enabled=True, id="r1b")
        response = await _handle_set_auto_compaction(h, cmd2)
        assert isinstance(response, RpcSuccessResponse)
        assert h.auto_compaction_enabled is True
    finally:
        await h.dispose()


async def test_set_auto_retry_toggles_state() -> None:
    h = _make_harness()
    try:
        assert h.state.auto_retry_enabled is True
        cmd = RpcCommandSetAutoRetry(enabled=False, id="r2")
        response = await _handle_set_auto_retry(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "set_auto_retry"
        assert h.state.auto_retry_enabled is False
    finally:
        await h.dispose()


async def test_abort_retry_sets_state_flag() -> None:
    h = _make_harness()
    try:
        assert h.state.retry_aborted is False
        cmd = RpcCommandAbortRetry(id="r3")
        response = await _handle_abort_retry(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "abort_retry"
        assert h.state.retry_aborted is True
    finally:
        await h.dispose()


async def test_abort_bash_sets_state_flag() -> None:
    h = _make_harness()
    try:
        assert h.state.bash_aborted is False
        cmd = RpcCommandAbortBash(id="r4")
        response = await _handle_abort_bash(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "abort_bash"
        assert h.state.bash_aborted is True
    finally:
        await h.dispose()
