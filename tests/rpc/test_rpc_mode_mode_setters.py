"""Sprint 6h₂ (ADR-0071, P-248) — ``set_steering_mode`` /
``set_follow_up_mode`` RPC handlers.

Pi parity: ``rpc-mode.ts:585-593``. The handlers catch
:exc:`ValueError` from the runtime-validating sync setters and surface
an :class:`RpcErrorResponse` so RPC clients see the Pi-shape
``{success: false, error: ...}`` envelope on bad input.
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
    _handle_set_follow_up_mode,
    _handle_set_steering_mode,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandSetFollowUpMode,
    RpcCommandSetSteeringMode,
    RpcErrorResponse,
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
    return AgentHarness(
        AgentHarnessOptions(
            steering_mode="one-at-a-time",
            follow_up_mode="one-at-a-time",
            stream_fn=_stream(),
        )
    )


async def test_set_steering_mode_handler_updates_harness_state() -> None:
    h = _make_harness()
    try:
        cmd = RpcCommandSetSteeringMode(mode="all", id="r1")
        response = await _handle_set_steering_mode(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "set_steering_mode"
        assert response.id == "r1"
        assert h.state.steering_mode == "all"
        assert h._steering_queue.mode == "all"
    finally:
        await h.dispose()


async def test_set_follow_up_mode_handler_updates_harness_state() -> None:
    h = _make_harness()
    try:
        cmd = RpcCommandSetFollowUpMode(mode="all", id="r2")
        response = await _handle_set_follow_up_mode(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "set_follow_up_mode"
        assert h.state.follow_up_mode == "all"
        assert h._follow_up_queue.mode == "all"
    finally:
        await h.dispose()


async def test_set_steering_mode_invalid_returns_error_envelope() -> None:
    """Pi parity: bypass the type narrow + emit an error envelope."""

    h = _make_harness()
    try:
        # Construct via ``object.__setattr__`` to bypass the
        # ``Literal["all", "one-at-a-time"]`` type narrow — the dataclass
        # is frozen so direct assignment would fail.
        cmd = RpcCommandSetSteeringMode(mode="all", id="r3")
        object.__setattr__(cmd, "mode", "bogus")
        response = await _handle_set_steering_mode(h, cmd)
        assert isinstance(response, RpcErrorResponse)
        assert response.command == "set_steering_mode"
        assert "steering_mode" in response.error
    finally:
        await h.dispose()


async def test_set_follow_up_mode_invalid_returns_error_envelope() -> None:
    h = _make_harness()
    try:
        cmd = RpcCommandSetFollowUpMode(mode="all", id="r4")
        object.__setattr__(cmd, "mode", "bogus")
        response = await _handle_set_follow_up_mode(h, cmd)
        assert isinstance(response, RpcErrorResponse)
        assert response.command == "set_follow_up_mode"
        assert "follow_up_mode" in response.error
    finally:
        await h.dispose()
