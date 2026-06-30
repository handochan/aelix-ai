"""Issue #4 Lane B — context-overflow detection.

Pi parity: ``packages/ai/test/context-overflow.test.ts`` /
``packages/ai/test/overflow.test.ts`` (SHA 927e980). Ports the provider
error-message matrix plus the silent- and length-overflow cases for
:func:`aelix_ai.utils.overflow.is_context_overflow`.
"""

from __future__ import annotations

import pytest
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.utils.overflow import get_overflow_patterns, is_context_overflow


def _err(message: str) -> AssistantMessage:
    return AssistantMessage(content=[], stop_reason="error", error_message=message)


# ── Case 1: error-message patterns (per provider) ───────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "prompt is too long: 213462 tokens > 200000 maximum",  # Anthropic
        '413 {"error":{"type":"request_too_large","message":"Request exceeds"}}',  # Anthropic 413
        "input is too long for requested model",  # Bedrock
        "Your input exceeds the context window of this model",  # OpenAI
        "Requested token count exceeds the model's maximum context length of 131072 tokens",  # LiteLLM
        "Input length (265330) exceeds model's maximum context length (262144).",  # OpenAI-compat
        "The input token count (1196265) exceeds the maximum number of tokens allowed",  # Gemini
        "This model's maximum prompt length is 131072 but the request contains 537812 tokens",  # xAI
        "Please reduce the length of the messages or completion",  # Groq
        "This endpoint's maximum context length is 8192 tokens. However, you requested 9000",  # OpenRouter
        "Input length 100 exceeds the maximum allowed input length of 50 tokens.",  # Poolside
        "The input (5000 tokens) is longer than the model's context length (4096 tokens).",  # Together
        "prompt token count of 9000 exceeds the limit of 8000",  # Copilot
        "the request exceeds the available context size, try increasing it",  # llama.cpp
        "tokens to keep from the initial prompt is greater than the context length",  # LM Studio
        "invalid params, context window exceeds limit",  # MiniMax
        "Your request exceeded model token limit: 100000 (requested: 120000)",  # Kimi
        "Prompt contains 9000 tokens ... too large for model with 8000 maximum context length",  # Mistral
        "context_length_exceeded",  # generic
        "too many tokens",  # generic
        "token limit exceeded",  # generic
        "400 (no body)",  # Cerebras
        "413 status code (no body)",  # Cerebras
    ],
)
def test_error_message_overflow_detected(message: str) -> None:
    assert is_context_overflow(_err(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "Throttling error: Too many tokens, please wait before trying again.",  # Bedrock throttle
        "rate limit exceeded",  # generic rate limit
        "429 Too Many Requests",  # HTTP 429
    ],
)
def test_non_overflow_errors_excluded(message: str) -> None:
    # These match (or look like) overflow text but are rate-limit/throttle
    # errors — the NON_OVERFLOW_PATTERNS exclusion must win.
    assert is_context_overflow(_err(message)) is False


def test_unrelated_error_is_not_overflow() -> None:
    assert is_context_overflow(_err("invalid API key")) is False


def test_non_error_stop_reason_with_overflow_text_is_not_case1() -> None:
    # Case 1 only fires for stop_reason == "error".
    m = AssistantMessage(
        content=[TextContent(text="prompt is too long")],
        stop_reason="end_turn",
    )
    assert is_context_overflow(m) is False


# ── Case 2: silent overflow (usage exceeds window on a completed answer) ─────


def test_silent_overflow_on_stop() -> None:
    m = AssistantMessage(content=[], stop_reason="stop", usage={"input": 250_000})
    assert is_context_overflow(m, 200_000) is True


def test_silent_overflow_on_end_turn_aelix_anthropic() -> None:
    # Aelix's Anthropic adapter emits "end_turn" (not "stop") for completion.
    m = AssistantMessage(
        content=[],
        stop_reason="end_turn",
        usage={"input": 100_000, "cache_read": 150_000},
    )
    assert is_context_overflow(m, 200_000) is True


def test_silent_overflow_requires_window() -> None:
    m = AssistantMessage(content=[], stop_reason="stop", usage={"input": 250_000})
    assert is_context_overflow(m, None) is False
    assert is_context_overflow(m, 0) is False


def test_completed_under_window_is_not_overflow() -> None:
    m = AssistantMessage(content=[], stop_reason="end_turn", usage={"input": 100})
    assert is_context_overflow(m, 200_000) is False


# ── Case 3: length-stop overflow (truncated input, no room to generate) ──────


def test_length_stop_overflow() -> None:
    m = AssistantMessage(
        content=[], stop_reason="length", usage={"input": 199_000, "output": 0}
    )
    assert is_context_overflow(m, 200_000) is True


def test_length_stop_with_output_is_not_overflow() -> None:
    # Real length-limited generation produced output — not an input overflow.
    m = AssistantMessage(
        content=[], stop_reason="length", usage={"input": 199_000, "output": 500}
    )
    assert is_context_overflow(m, 200_000) is False


def test_length_stop_below_threshold_is_not_overflow() -> None:
    m = AssistantMessage(
        content=[], stop_reason="length", usage={"input": 100_000, "output": 0}
    )
    assert is_context_overflow(m, 200_000) is False


# ── camelCase usage keys are read defensively ───────────────────────────────


def test_camelcase_cache_read_key() -> None:
    m = AssistantMessage(
        content=[], stop_reason="stop", usage={"input": 100_000, "cacheRead": 150_000}
    )
    assert is_context_overflow(m, 200_000) is True


def test_get_overflow_patterns_returns_copy() -> None:
    a = get_overflow_patterns()
    b = get_overflow_patterns()
    assert a is not b
    assert len(a) > 0
