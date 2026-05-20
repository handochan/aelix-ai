"""Sprint 6h₄a (ADR-0075, P-293/P-298) — RPC ``get_last_assistant_text`` handler tests.

Pi parity: ``rpc-mode.ts:596-599`` → ``session.getLastAssistantText()``
(``agent-session.ts:3059-3081``). Response shape:
``{text: string}`` when present, ``{}`` when absent (Pi
``JSON.stringify({text: undefined})`` key-omission — P-298 SYNTHESIS).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import (
    _handle_get_last_assistant_text,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandGetLastAssistantText,
    RpcSuccessResponse,
)


def _quiet_stream_fn() -> Any:
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


def _make_harness(initial_messages: list[Any] | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            initial_messages=initial_messages or [],
        )
    )


async def test_returns_text_key_when_present() -> None:
    """Pi parity: assistant text present → ``data == {"text": "hello"}``."""

    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[TextContent(text="hello")],
                stop_reason="end_turn",
            )
        ]
    )
    try:
        cmd = RpcCommandGetLastAssistantText(id="r1")
        response = await _handle_get_last_assistant_text(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_last_assistant_text"
        assert response.data == {"text": "hello"}
    finally:
        await harness.dispose()


async def test_empty_data_dict_when_text_is_none() -> None:
    """P-298 SYNTHESIS lock: no emittable assistant → ``data == {}``.

    Pi ``JSON.stringify({text: undefined})`` omits the ``text`` key.
    The handler MUST emit an empty dict (not ``{"text": None}``) so
    the wire bytes match Pi byte-for-byte.
    """

    harness = _make_harness()  # No assistant messages.
    try:
        cmd = RpcCommandGetLastAssistantText(id="r2")
        response = await _handle_get_last_assistant_text(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_last_assistant_text"
        # CRITICAL Pi key-omission parity (P-298 SYNTHESIS).
        assert response.data == {}
        assert "text" not in response.data
    finally:
        await harness.dispose()


async def test_empty_data_dict_when_only_user_messages() -> None:
    """Pi parity: user-only history → ``data == {}`` (no ``text`` key)."""

    harness = _make_harness(
        initial_messages=[
            UserMessage(content=[TextContent(text="just me")]),
        ]
    )
    try:
        cmd = RpcCommandGetLastAssistantText(id="r3")
        response = await _handle_get_last_assistant_text(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.data == {}
    finally:
        await harness.dispose()


async def test_empty_data_dict_when_whitespace_only_assistant() -> None:
    """P-298 parity: ``"   "`` trims to empty → ``data == {}`` on wire."""

    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[TextContent(text="   \n  ")],
                stop_reason="end_turn",
            )
        ]
    )
    try:
        cmd = RpcCommandGetLastAssistantText(id="r4")
        response = await _handle_get_last_assistant_text(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.data == {}
    finally:
        await harness.dispose()


async def test_response_command_field_is_get_last_assistant_text() -> None:
    """Pi parity: the response ``command`` discriminator matches Pi."""

    harness = _make_harness()
    try:
        cmd = RpcCommandGetLastAssistantText(id="r5")
        response = await _handle_get_last_assistant_text(harness, cmd)
        assert response.command == "get_last_assistant_text"
    finally:
        await harness.dispose()


async def test_dispatch_table_routes_to_real_handler() -> None:
    """The dispatcher binds ``get_last_assistant_text`` to the real handler."""

    table = build_dispatch_table()
    handler = table.get("get_last_assistant_text")
    assert handler is not None
    name = getattr(handler, "__qualname__", repr(handler))
    assert "deferred" not in name.lower()
    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[TextContent(text="dispatched")],
                stop_reason="end_turn",
            )
        ]
    )
    try:
        cmd = RpcCommandGetLastAssistantText(id="r6")
        response = await handler(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.data == {"text": "dispatched"}
    finally:
        await harness.dispose()
