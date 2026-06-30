"""Context-overflow detection (Pi parity: ``packages/ai/src/utils/overflow.ts``
at SHA 927e980).

Detects when a provider has rejected (or silently truncated) a request whose
input exceeded the model's context window, so the agent harness can recover by
compacting and re-running the failed turn (issue #4 Lane B).

Aelix adaptations of the verbatim Pi port:

- Pi's ``AssistantMessage.usage`` is a typed object; Aelix's is a defensive
  ``dict[str, Any] | None`` (mirrors :func:`aelix_agent_core.session.compaction.
  calculate_context_tokens`). :func:`_usage_field` reads either shape.
- Pi normalizes every provider's "completed" stop reason to ``"stop"``; the
  Aelix Anthropic adapter keeps ``"end_turn"`` (``_anthropic_transforms.py``),
  so the silent-overflow success case accepts both ``"stop"`` and ``"end_turn"``
  (see :data:`_SUCCESS_STOP_REASONS`).
"""

from __future__ import annotations

import re
from typing import Any

# Stop reasons that mark a *successfully completed* assistant answer. Pi uses
# only ``"stop"``; Aelix's Anthropic adapter emits ``"end_turn"`` for the same
# state, so both are treated as "completed" for the silent-overflow check and
# for the harness's ``will_retry`` decision.
_SUCCESS_STOP_REASONS = ("stop", "end_turn")

#: Regex patterns to detect context-overflow errors from different providers.
#: Verbatim port of Pi's ``OVERFLOW_PATTERNS`` (overflow.ts). See the Pi source
#: for the per-provider example error messages each pattern matches.
_OVERFLOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"prompt is too long", re.IGNORECASE),  # Anthropic token overflow
    re.compile(r"request_too_large", re.IGNORECASE),  # Anthropic HTTP 413 byte overflow
    re.compile(r"input is too long for requested model", re.IGNORECASE),  # Amazon Bedrock
    re.compile(r"exceeds the context window", re.IGNORECASE),  # OpenAI
    re.compile(
        r"exceeds (?:the )?(?:model'?s )?maximum context length"
        r"(?: of [\d,]+ tokens?|\s*\([\d,]+\))",
        re.IGNORECASE,
    ),  # OpenAI-compatible proxies (LiteLLM)
    re.compile(r"input token count.*exceeds the maximum", re.IGNORECASE),  # Google (Gemini)
    re.compile(r"maximum prompt length is \d+", re.IGNORECASE),  # xAI (Grok)
    re.compile(r"reduce the length of the messages", re.IGNORECASE),  # Groq
    re.compile(r"maximum context length is \d+ tokens", re.IGNORECASE),  # OpenRouter
    re.compile(
        r"exceeds (?:the )?maximum allowed input length of [\d,]+ tokens?",
        re.IGNORECASE,
    ),  # OpenRouter/Poolside
    re.compile(
        r"input \(\d+ tokens\) is longer than the model'?s context length \(\d+ tokens\)",
        re.IGNORECASE,
    ),  # Together AI
    re.compile(r"exceeds the limit of \d+", re.IGNORECASE),  # GitHub Copilot
    re.compile(r"exceeds the available context size", re.IGNORECASE),  # llama.cpp server
    re.compile(r"greater than the context length", re.IGNORECASE),  # LM Studio
    re.compile(r"context window exceeds limit", re.IGNORECASE),  # MiniMax
    re.compile(r"exceeded model token limit", re.IGNORECASE),  # Kimi For Coding
    re.compile(r"too large for model with \d+ maximum context length", re.IGNORECASE),  # Mistral
    re.compile(r"model_context_window_exceeded", re.IGNORECASE),  # z.ai surfaced as error text
    re.compile(r"prompt too long; exceeded (?:max )?context length", re.IGNORECASE),  # Ollama
    re.compile(r"context[_ ]length[_ ]exceeded", re.IGNORECASE),  # Generic fallback
    re.compile(r"too many tokens", re.IGNORECASE),  # Generic fallback
    re.compile(r"token limit exceeded", re.IGNORECASE),  # Generic fallback
    re.compile(r"^4(?:00|13)\s*(?:status code)?\s*\(no body\)", re.IGNORECASE),  # Cerebras
]

#: Patterns that indicate non-overflow errors (rate limiting, server errors).
#: Verbatim port of Pi's ``NON_OVERFLOW_PATTERNS``. Excluded from detection even
#: when they also match an overflow pattern (e.g. Bedrock throttling formats as
#: "Too many tokens, please wait" which matches ``/too many tokens/i``).
_NON_OVERFLOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(Throttling error|Service unavailable):", re.IGNORECASE),  # AWS Bedrock
    re.compile(r"rate limit", re.IGNORECASE),  # Generic rate limiting
    re.compile(r"too many requests", re.IGNORECASE),  # Generic HTTP 429
]


def _usage_field(usage: Any, *keys: str) -> int:
    """Read the first present token count from a dict- or object-shaped usage.

    Mirrors the defensive camelCase/snake_case reads in
    :func:`aelix_agent_core.session.compaction.calculate_context_tokens`.
    """

    if usage is None:
        return 0
    for key in keys:
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        if value:
            return int(value)
    return 0


def is_context_overflow(message: Any, context_window: int | None = None) -> bool:
    """Pi ``isContextOverflow`` — is this assistant message a context overflow?

    Handles three cases (Pi parity):

    1. **Error-based overflow** — most providers return ``stop_reason="error"``
       with a recognizable message (and not a rate-limit/throttle message).
    2. **Silent overflow** (z.ai style) — a *successful* response whose
       ``input + cache_read`` usage exceeds ``context_window``.
    3. **Length-stop overflow** (Xiaomi MiMo style) — the server truncated the
       oversized input to fill the window, leaving ``output == 0`` and
       ``input + cache_read`` filling (>= 99% of) the window.

    ``context_window`` is required for cases 2 and 3; pass ``None``/``0`` to
    check error-message patterns only.
    """

    stop_reason = getattr(message, "stop_reason", None)
    error_message = getattr(message, "error_message", None)

    # Case 1: error-message patterns.
    if stop_reason == "error" and error_message:
        is_non_overflow = any(p.search(error_message) for p in _NON_OVERFLOW_PATTERNS)
        if not is_non_overflow and any(
            p.search(error_message) for p in _OVERFLOW_PATTERNS
        ):
            return True

    if not context_window:
        return False

    usage = getattr(message, "usage", None)

    # Case 2: silent overflow on a completed answer (usage exceeds the window).
    if stop_reason in _SUCCESS_STOP_REASONS:
        input_tokens = _usage_field(usage, "input_tokens", "input") + _usage_field(
            usage, "cache_read", "cacheRead"
        )
        if input_tokens > context_window:
            return True

    # Case 3: length-stop overflow (server truncated oversized input, no room
    # left to generate).
    if stop_reason == "length" and _usage_field(usage, "output_tokens", "output") == 0:
        input_tokens = _usage_field(usage, "input_tokens", "input") + _usage_field(
            usage, "cache_read", "cacheRead"
        )
        if input_tokens >= context_window * 0.99:
            return True

    return False


def get_overflow_patterns() -> list[re.Pattern[str]]:
    """Return a copy of the overflow patterns (Pi ``getOverflowPatterns``)."""

    return list(_OVERFLOW_PATTERNS)


__all__ = [
    "get_overflow_patterns",
    "is_context_overflow",
]
