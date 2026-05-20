"""Sprint 6h₄a (ADR-0075, P-293/P-295) — RPC ``get_fork_messages`` handler tests.

Pi parity: ``rpc-mode.ts:591-594`` →
``session.getUserMessagesForForking()``
(``agent-session.ts:2870-2885``). Response shape:
``{messages: Array<{entryId, text}>}``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import MemorySessionStorage
from aelix_agent_core.session.session import Session
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
    _handle_get_fork_messages,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandGetForkMessages,
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


def _make_harness(session: Session | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            session=session,
        )
    )


async def test_empty_harness_returns_empty_messages_array() -> None:
    """Pi parity: empty harness → ``{"messages": []}``."""

    harness = _make_harness()
    try:
        cmd = RpcCommandGetForkMessages(id="r1")
        response = await _handle_get_fork_messages(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_fork_messages"
        assert response.data == {"messages": []}
    finally:
        await harness.dispose()


async def test_with_fork_points_returns_pi_camel_case_shape() -> None:
    """Pi parity: serialized records are ``{entryId, text}`` camelCase."""

    session = Session(MemorySessionStorage())
    u1 = await session.append_message(
        UserMessage(content=[TextContent(text="first")])
    )
    await session.append_message(AssistantMessage(content=[]))
    u2 = await session.append_message(
        UserMessage(content=[TextContent(text="second")])
    )
    harness = _make_harness(session=session)
    try:
        cmd = RpcCommandGetForkMessages(id="r2")
        response = await _handle_get_fork_messages(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_fork_messages"
        assert isinstance(response.data, dict)
        messages = response.data["messages"]
        assert messages == [
            {"entryId": u1, "text": "first"},
            {"entryId": u2, "text": "second"},
        ]
    finally:
        await harness.dispose()


async def test_each_dict_has_exactly_two_keys() -> None:
    """Pi parity: each entry is exactly ``{entryId, text}`` — no extras."""

    session = Session(MemorySessionStorage())
    await session.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )
    harness = _make_harness(session=session)
    try:
        cmd = RpcCommandGetForkMessages(id="r3")
        response = await _handle_get_fork_messages(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        messages = response.data["messages"]
        assert len(messages) == 1
        assert set(messages[0].keys()) == {"entryId", "text"}
    finally:
        await harness.dispose()


async def test_response_command_field_is_get_fork_messages() -> None:
    """Pi parity: the response ``command`` discriminator matches Pi."""

    harness = _make_harness()
    try:
        cmd = RpcCommandGetForkMessages(id="r4")
        response = await _handle_get_fork_messages(harness, cmd)
        assert response.command == "get_fork_messages"
    finally:
        await harness.dispose()


async def test_dispatch_table_routes_to_real_handler() -> None:
    """The dispatcher binds ``get_fork_messages`` to the real handler."""

    table = build_dispatch_table()
    handler = table.get("get_fork_messages")
    assert handler is not None
    name = getattr(handler, "__qualname__", repr(handler))
    assert "deferred" not in name.lower()
    harness = _make_harness()
    try:
        cmd = RpcCommandGetForkMessages(id="r5")
        response = await handler(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_fork_messages"
    finally:
        await harness.dispose()
