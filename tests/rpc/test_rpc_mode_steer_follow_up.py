"""Sprint 6h₂ (ADR-0071, P-245~P-251) — ``steer`` / ``follow_up`` handlers.

Pi parity: ``rpc-mode.ts:528-536``. Each handler decodes the optional
``images`` wire payload via :func:`_decode_images` and forwards to the
corresponding harness coroutine.
"""

from __future__ import annotations

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
from aelix_coding_agent.rpc.rpc_mode import (
    _decode_images,
    _handle_follow_up,
    _handle_steer,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandFollowUp,
    RpcCommandSteer,
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
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
        )
    )


async def test_handle_steer_enqueues_and_returns_success() -> None:
    h = _make_harness()
    try:
        cmd = RpcCommandSteer(message="go", id="r1")
        response = await _handle_steer(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "steer"
        assert response.id == "r1"
        # Steering queue captured exactly one message.
        assert len(h._steering_queue._messages) == 1
    finally:
        await h.dispose()


async def test_handle_steer_with_images_attaches_image_blocks() -> None:
    h = _make_harness()
    try:
        cmd = RpcCommandSteer(
            message="see this",
            images=[{"mimeType": "image/png", "data": "abc"}],
            id="r2",
        )
        response = await _handle_steer(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        msg = h._steering_queue._messages[0]
        # text + 1 image block
        assert len(msg.content) == 2  # type: ignore[union-attr]
    finally:
        await h.dispose()


async def test_handle_follow_up_enqueues_and_returns_success() -> None:
    h = _make_harness()
    try:
        cmd = RpcCommandFollowUp(message="bye", id="r3")
        response = await _handle_follow_up(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "follow_up"
        assert response.id == "r3"
        assert len(h._follow_up_queue._messages) == 1
    finally:
        await h.dispose()


async def test_handle_follow_up_snake_case_mime_type_raises() -> None:
    """Sprint 6h₂ W6 (P-262 BLOCKING): the decoder is strict-camelCase
    only. Pi wires ``ImageContent.mimeType`` (TS type narrows at
    compile time); a snake_case payload is a contract violation and
    raises :exc:`ValueError`. The outer dispatcher surfaces the failure
    as a Pi-shape :class:`RpcErrorResponse`.
    """

    h = _make_harness()
    try:
        with pytest.raises(ValueError, match="missing required 'mimeType'"):
            _decode_images(
                [{"mime_type": "image/jpeg", "data": "zz"}]
            )
    finally:
        await h.dispose()


def test_decode_images_none_returns_none() -> None:
    assert _decode_images(None) is None
    assert _decode_images([]) is None
