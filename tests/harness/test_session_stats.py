"""Sprint 6h₃ (ADR-0073, P-268/P-269/P-272) — SessionStats aggregator unit tests.

Pi parity: ``agent-session.ts:212-223`` SessionStats + ``:2765+``
``getSessionStats``. The aggregator is a pure function over a list of
:class:`Message`; these tests pin the per-role counts, token totals,
cost accumulation, and ``totalMessages`` invariant.
"""

from __future__ import annotations

from aelix_agent_core.harness._session_stats import (
    SessionStats,
    SessionStatsTokens,
    aggregate_session_stats,
)
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import Usage, UsageCost


def test_aggregate_empty_messages_returns_zeros() -> None:
    """Pi parity: empty session → every counter is 0."""

    stats = aggregate_session_stats("s1", [])
    assert isinstance(stats, SessionStats)
    assert stats.session_id == "s1"
    assert stats.user_messages == 0
    assert stats.assistant_messages == 0
    assert stats.tool_calls == 0
    assert stats.tool_results == 0
    assert stats.total_messages == 0
    assert stats.tokens == SessionStatsTokens()
    assert stats.cost == 0.0
    assert stats.session_file is None
    assert stats.context_usage is None


def test_aggregate_per_role_counts() -> None:
    """Pi parity: 3 user + 2 assistant + 1 tool_result → counts match."""

    messages = [
        UserMessage(content=[TextContent(text="u1")]),
        UserMessage(content=[TextContent(text="u2")]),
        UserMessage(content=[TextContent(text="u3")]),
        AssistantMessage(content=[TextContent(text="a1")]),
        AssistantMessage(content=[TextContent(text="a2")]),
        ToolResultMessage(tool_call_id="t", content=[TextContent(text="ok")]),
    ]
    stats = aggregate_session_stats("s", messages)
    assert stats.user_messages == 3
    assert stats.assistant_messages == 2
    assert stats.tool_results == 1
    # Pi parity invariant (P-276): totalMessages = len(messages) per
    # agent-session.ts:2935 (state.messages.length). For the current
    # 3-type universe this equals user + assistant + toolResults.
    assert stats.total_messages == len(messages)
    assert stats.total_messages == 6


def test_aggregate_tool_call_blocks_counted() -> None:
    """Pi parity: an assistant message with 2 toolCall blocks → tool_calls=2."""

    msg = AssistantMessage(
        content=[
            TextContent(text="thinking"),
            ToolCallContent(tool_call_id="c1", tool_name="t1", input={"a": 1}),
            ToolCallContent(tool_call_id="c2", tool_name="t2", input={"b": 2}),
        ]
    )
    stats = aggregate_session_stats("s", [msg])
    assert stats.tool_calls == 2
    # toolCalls are tallied per content block; the assistant message
    # itself remains a single assistant message.
    assert stats.assistant_messages == 1


def test_aggregate_tokens_and_cost_from_usage() -> None:
    """Pi parity: ``usage`` accumulates ``input/output/cacheRead/cacheWrite``
    and ``usage.cost.total`` accumulates into ``cost``.
    """

    u1 = Usage(
        input=100,
        output=50,
        cache_read=10,
        cache_write=5,
        cost=UsageCost(total=0.005),
    )
    u2 = Usage(
        input=200,
        output=80,
        cache_read=20,
        cache_write=10,
        cost=UsageCost(total=0.012),
    )
    msg1 = AssistantMessage(content=[TextContent(text="a1")], usage=u1)  # type: ignore[arg-type]
    msg2 = AssistantMessage(content=[TextContent(text="a2")], usage=u2)  # type: ignore[arg-type]
    stats = aggregate_session_stats("s", [msg1, msg2])
    assert stats.tokens.input == 300
    assert stats.tokens.output == 130
    assert stats.tokens.cache_read == 30
    assert stats.tokens.cache_write == 15
    # Pi parity invariant: total = input + output + cacheRead + cacheWrite.
    assert stats.tokens.total == 475
    assert stats.cost == 0.017


def test_aggregate_tokens_total_equals_sum_of_four() -> None:
    """Pi parity invariant: tokens.total = input + output + cacheRead + cacheWrite."""

    u = Usage(input=7, output=11, cache_read=13, cache_write=17)
    msg = AssistantMessage(content=[TextContent(text="a")], usage=u)  # type: ignore[arg-type]
    stats = aggregate_session_stats("s", [msg])
    assert stats.tokens.total == 7 + 11 + 13 + 17
    assert stats.tokens.total == (
        stats.tokens.input
        + stats.tokens.output
        + stats.tokens.cache_read
        + stats.tokens.cache_write
    )


def test_aggregate_session_file_threaded_through() -> None:
    """Pi parity: ``sessionFile`` is propagated verbatim from the caller."""

    stats = aggregate_session_stats(
        "s", [], session_file="/tmp/x.jsonl"
    )
    assert stats.session_file == "/tmp/x.jsonl"


def test_aggregate_context_usage_threaded_through() -> None:
    """Pi parity: ``contextUsage`` is propagated verbatim (may be None)."""

    from aelix_coding_agent.extensions.api import ContextUsage

    cu = ContextUsage(tokens=1000, context_window=200_000, percent=0.5)
    stats = aggregate_session_stats("s", [], context_usage=cu)
    assert stats.context_usage is cu


def test_aggregate_assistant_without_usage_does_not_crash() -> None:
    """Pi parity: missing ``usage`` → tokens/cost stay 0."""

    msg = AssistantMessage(content=[TextContent(text="a")])
    stats = aggregate_session_stats("s", [msg])
    assert stats.assistant_messages == 1
    assert stats.tokens.total == 0
    assert stats.cost == 0.0


def test_aggregate_assistant_with_usage_without_cost_does_not_crash() -> None:
    """Pi parity: ``usage`` present but ``usage.cost.total`` is 0."""

    u = Usage(input=10, output=5)  # default UsageCost has total=0.0
    msg = AssistantMessage(content=[TextContent(text="a")], usage=u)  # type: ignore[arg-type]
    stats = aggregate_session_stats("s", [msg])
    assert stats.tokens.input == 10
    assert stats.tokens.output == 5
    assert stats.cost == 0.0


def test_session_stats_dataclass_is_frozen() -> None:
    """The :class:`SessionStats` dataclass is frozen (Pi parity:
    Pi's ``SessionStats`` is an interface — immutable by convention).
    """

    import pytest

    stats = aggregate_session_stats("s", [])
    with pytest.raises((AttributeError, Exception)):
        stats.cost = 1.0  # type: ignore[misc]


def test_session_stats_has_ten_fields() -> None:
    """Pi parity: ``SessionStats`` shape is 10 fields exactly
    (``agent-session.ts:212-223``).
    """

    fields = set(SessionStats.__dataclass_fields__.keys())
    assert fields == {
        "session_id",
        "user_messages",
        "assistant_messages",
        "tool_calls",
        "tool_results",
        "total_messages",
        "tokens",
        "cost",
        "session_file",
        "context_usage",
    }


def test_session_stats_tokens_has_five_fields() -> None:
    """Pi parity: ``SessionStats.tokens`` sub-shape is 5 fields."""

    fields = set(SessionStatsTokens.__dataclass_fields__.keys())
    assert fields == {"input", "output", "cache_read", "cache_write", "total"}


def test_aggregate_accepts_dict_shape_usage() -> None:
    """Pi parity (P-283 W6): aggregator tolerates dict-shape ``usage``
    payloads (legacy JSONL fixtures, provider passthrough) the same
    way it tolerates dataclass-shape :class:`Usage`.
    """

    usage_dict = {
        "input": 50,
        "output": 25,
        "cache_read": 5,
        "cache_write": 2,
        "cost": {"total": 0.003},
    }
    msg = AssistantMessage(content=[TextContent(text="a")], usage=usage_dict)  # type: ignore[arg-type]
    stats = aggregate_session_stats("s", [msg])
    assert stats.tokens.input == 50
    assert stats.tokens.output == 25
    assert stats.tokens.cache_read == 5
    assert stats.tokens.cache_write == 2
    assert stats.tokens.total == 82
    assert stats.cost == 0.003


def test_aggregate_total_messages_uses_len_pi_parity() -> None:
    """Pi parity (P-276 W6): ``totalMessages = len(messages)`` matches
    ``agent-session.ts:2935`` (Pi reads ``state.messages.length`` —
    NOT a type-filtered sum).
    """

    messages = [
        UserMessage(content=[TextContent(text="u")]),
        AssistantMessage(content=[TextContent(text="a")]),
        ToolResultMessage(tool_call_id="t", content=[TextContent(text="ok")]),
    ]
    stats = aggregate_session_stats("s", messages)
    assert stats.total_messages == len(messages) == 3
