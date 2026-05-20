"""Sprint 6h₃ (ADR-0073, P-269) — RPC ``get_session_stats`` handler tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    Usage,
    UsageCost,
)
from aelix_coding_agent.rpc.rpc_mode import (
    _handle_get_session_stats,
    _session_stats_to_dict,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandGetSessionStats,
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


async def test_get_session_stats_returns_pi_camel_case_shape() -> None:
    """Pi parity: response ``data`` carries the camelCase wire shape."""

    harness = _make_harness()
    try:
        cmd = RpcCommandGetSessionStats(id="r1")
        response = await _handle_get_session_stats(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_session_stats"
        data = response.data
        assert isinstance(data, dict)
        # Required Pi camelCase keys.
        assert "sessionId" in data
        assert "userMessages" in data
        assert "assistantMessages" in data
        assert "toolCalls" in data
        assert "toolResults" in data
        assert "totalMessages" in data
        assert "tokens" in data
        assert "cost" in data
        # Tokens sub-dict is camelCase.
        tokens = data["tokens"]
        assert "input" in tokens
        assert "output" in tokens
        assert "cacheRead" in tokens
        assert "cacheWrite" in tokens
        assert "total" in tokens
    finally:
        await harness.dispose()


async def test_get_session_stats_empty_session_all_zero() -> None:
    """Pi parity: empty harness → all-zero stats on the wire."""

    harness = _make_harness()
    try:
        cmd = RpcCommandGetSessionStats(id="r2")
        response = await _handle_get_session_stats(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        assert response.data["userMessages"] == 0
        assert response.data["assistantMessages"] == 0
        assert response.data["toolCalls"] == 0
        assert response.data["toolResults"] == 0
        assert response.data["totalMessages"] == 0
        assert response.data["tokens"]["total"] == 0
        assert response.data["cost"] == 0.0
    finally:
        await harness.dispose()


async def test_get_session_stats_counts_messages_correctly() -> None:
    """Pi parity: counts reflect the in-memory message list."""

    msgs = [
        UserMessage(content=[TextContent(text="u")]),
        AssistantMessage(
            content=[
                TextContent(text="a"),
                ToolCallContent(
                    tool_call_id="c", tool_name="t", input={}
                ),
            ],
            usage=Usage(  # type: ignore[arg-type]
                input=100, output=50, cache_read=10, cache_write=5,
                cost=UsageCost(total=0.01),
            ),
        ),
        ToolResultMessage(
            tool_call_id="c", content=[TextContent(text="ok")]
        ),
    ]
    harness = _make_harness(initial_messages=msgs)
    try:
        cmd = RpcCommandGetSessionStats(id="r3")
        response = await _handle_get_session_stats(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        assert response.data["userMessages"] == 1
        assert response.data["assistantMessages"] == 1
        assert response.data["toolCalls"] == 1
        assert response.data["toolResults"] == 1
        assert response.data["totalMessages"] == 3
        assert response.data["tokens"]["input"] == 100
        assert response.data["tokens"]["output"] == 50
        assert response.data["tokens"]["cacheRead"] == 10
        assert response.data["tokens"]["cacheWrite"] == 5
        assert response.data["tokens"]["total"] == 165
        assert response.data["cost"] == 0.01
    finally:
        await harness.dispose()


async def test_get_session_stats_session_file_omitted_when_none() -> None:
    """Pi parity (JSON.stringify undefined-skip): ``sessionFile`` is
    OMITTED from the wire dict when :data:`None`.
    """

    harness = _make_harness()
    try:
        cmd = RpcCommandGetSessionStats(id="r4")
        response = await _handle_get_session_stats(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        assert "sessionFile" not in response.data
        assert "contextUsage" not in response.data
    finally:
        await harness.dispose()


async def test_dispatch_table_routes_get_session_stats() -> None:
    """The dispatcher table contains the real handler (not a deferred stub)."""

    table = build_dispatch_table()
    handler = table.get("get_session_stats")
    assert handler is not None
    name = getattr(handler, "__qualname__", repr(handler))
    assert "deferred" not in name.lower()


def test_session_stats_to_dict_includes_optional_fields_when_present() -> None:
    """Pi parity: when ``session_file`` / ``context_usage`` are present
    the wire dict includes them.
    """

    from aelix_agent_core.harness._session_stats import (
        SessionStats,
        SessionStatsTokens,
    )
    from aelix_coding_agent.extensions.api import ContextUsage

    cu = ContextUsage(tokens=1000, context_window=200_000, percent=0.5)
    stats = SessionStats(
        session_id="abc",
        user_messages=1,
        assistant_messages=1,
        tool_calls=0,
        tool_results=0,
        total_messages=2,
        tokens=SessionStatsTokens(total=42),
        cost=1.5,
        session_file="/tmp/x.jsonl",
        context_usage=cu,
    )
    wire = _session_stats_to_dict(stats)
    assert wire["sessionFile"] == "/tmp/x.jsonl"
    # Pi parity (P-275 W6): contextUsage wire shape is Pi-shape
    # {tokens, contextWindow, percent} — NOT {usedTokens, limitTokens,
    # remainingTokens}.
    assert wire["contextUsage"]["tokens"] == 1000
    assert wire["contextUsage"]["contextWindow"] == 200_000
    assert wire["contextUsage"]["percent"] == 0.5
