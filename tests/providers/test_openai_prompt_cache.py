"""Tests for ``clamp_openai_prompt_cache_key`` (pi parity).

Pi parity: ``packages/ai/src/api/openai-prompt-cache.ts`` @ 927e98068cda.
"""

from __future__ import annotations

from aelix_ai.providers._openai_prompt_cache import (
    OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH,
    clamp_openai_prompt_cache_key,
)


def test_max_length_constant() -> None:
    assert OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH == 64


def test_none_passes_through() -> None:
    assert clamp_openai_prompt_cache_key(None) is None


def test_empty_string_unchanged() -> None:
    assert clamp_openai_prompt_cache_key("") == ""


def test_short_key_unchanged() -> None:
    key = "session-abc"
    assert clamp_openai_prompt_cache_key(key) == key


def test_exactly_64_unchanged() -> None:
    key = "a" * 64
    assert clamp_openai_prompt_cache_key(key) == key
    assert len(clamp_openai_prompt_cache_key(key)) == 64


def test_65_is_truncated_to_64() -> None:
    key = "a" * 65
    out = clamp_openai_prompt_cache_key(key)
    assert out == "a" * 64
    assert len(out) == 64


def test_long_key_truncated_to_first_64() -> None:
    key = "".join(str(i % 10) for i in range(200))
    out = clamp_openai_prompt_cache_key(key)
    assert out == key[:64]
    assert len(out) == 64


def test_clamp_counts_code_points_not_utf16_units() -> None:
    # Pi uses Array.from (code points). 64 non-BMP code points are still
    # <= the limit and must pass through unchanged, even though they are
    # 128 UTF-16 units / many bytes.
    key = "🦊" * 64
    assert clamp_openai_prompt_cache_key(key) == key
    # 65 non-BMP code points -> truncated to first 64 code points.
    over = "🦊" * 65
    out = clamp_openai_prompt_cache_key(over)
    assert out == "🦊" * 64
    assert len(out) == 64
