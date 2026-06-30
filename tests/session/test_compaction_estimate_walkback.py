"""#4 (partial) — pi #5526: ``estimate_context_tokens`` walk-back over a
truncated / malformed (all-zero) usage tail.

Pi parity: ``getAssistantUsage`` / ``estimateContextTokens``
(``compaction.ts:121-197``). The reverse walk must SKIP an assistant turn
whose usage is missing or sums to zero (a truncated response) and fall back
to the previous VALID assistant's usage, otherwise the live context is
under-reported and threshold auto-compaction is suppressed.
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.session.compaction import (
    _valid_assistant_usage,
    calculate_context_tokens,
    estimate_context_tokens,
    estimate_tokens,
)
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    UserMessage,
)


def _assistant(text: str, *, usage: Any, stop: str = "end_turn") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)], stop_reason=stop, usage=usage
    )


def test_estimate_walks_back_over_zero_total_tokens_tail() -> None:
    # Valid assistant (500) at index 0, then a trailing user message, then a
    # truncated assistant whose usage sums to zero. Without the #5526 guard
    # the walk would stop at the zero-usage tail → tokens=0; with the guard
    # it walks back to 500 and adds the heuristic estimate for the trailing.
    msgs: list[Any] = [
        _assistant("r1", usage={"total_tokens": 500}),  # 0 — valid
        UserMessage(content=[TextContent(text="a" * 16)]),  # 1 — +4 tokens
        _assistant("r2", usage={"total_tokens": 0}),  # 2 — truncated all-zero
    ]
    trailing = estimate_tokens(msgs[1]) + estimate_tokens(msgs[2])
    assert estimate_context_tokens(msgs).tokens == 500 + trailing


def test_estimate_walks_back_over_missing_usage_tail() -> None:
    # ``usage=None`` (malformed / truncated) tail → skipped, walk back to 800.
    msgs: list[Any] = [
        _assistant("r1", usage={"total_tokens": 800}),  # 0 — valid
        _assistant("r2", usage=None),  # 1 — malformed, no usage
    ]
    # trailing is just the heuristic for the malformed assistant ("r2" -> 0).
    assert estimate_context_tokens(msgs).tokens == 800 + estimate_tokens(msgs[1])


def test_estimate_walks_back_over_all_zero_component_usage_tail() -> None:
    # Usage present but every component is zero → calculate_context_tokens==0
    # → treated as invalid and skipped.
    zero = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0}
    msgs: list[Any] = [
        _assistant("r1", usage={"total_tokens": 1234}),  # 0 — valid
        _assistant("r2", usage=zero),  # 1 — all-zero components
    ]
    assert calculate_context_tokens(zero) == 0
    assert estimate_context_tokens(msgs).tokens == 1234 + estimate_tokens(msgs[1])


def test_estimate_falls_back_to_heuristic_when_no_valid_assistant() -> None:
    # Only zero-usage assistants → no valid usage → heuristic over ALL msgs.
    msgs: list[Any] = [
        UserMessage(content=[TextContent(text="a" * 16)]),  # +4
        _assistant("r" * 16, usage={"total_tokens": 0}),  # +4 heuristic, no usage credit
    ]
    expected = sum(estimate_tokens(m) for m in msgs)
    assert estimate_context_tokens(msgs).tokens == expected


def test_valid_assistant_usage_guard_matches_pi_get_assistant_usage() -> None:
    # The helper returns the usage dict only for a non-error/non-aborted
    # assistant with a positive token sum.
    assert _valid_assistant_usage(
        _assistant("ok", usage={"total_tokens": 10})
    ) == {"total_tokens": 10}
    # zero usage → None
    assert _valid_assistant_usage(_assistant("ok", usage={"total_tokens": 0})) is None
    # missing usage → None
    assert _valid_assistant_usage(_assistant("ok", usage=None)) is None
    # error / aborted stop_reason → None even with positive usage
    assert _valid_assistant_usage(
        _assistant("e", usage={"total_tokens": 99}, stop="error")
    ) is None
    assert _valid_assistant_usage(
        _assistant("a", usage={"total_tokens": 99}, stop="aborted")
    ) is None
    # non-assistant message → None
    assert _valid_assistant_usage(UserMessage(content=[TextContent(text="x")])) is None
