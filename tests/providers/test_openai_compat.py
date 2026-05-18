"""Sprint 6b (Phase 4.2, §F) — OpenAI completions compat detection tests.

Pi parity: ``detectCompat`` (``openai-completions.ts:1062-1121``). Every
detected provider / URL substring → expected compat field overrides.
"""

from __future__ import annotations

from aelix_ai.providers._openai_compat import (
    OpenAICompletionsCompat,
    detect_compat,
    get_compat,
)
from aelix_ai.streaming import Model


def _m(**kwargs) -> Model:
    base = dict(api="openai-completions", id="m", provider="openai", base_url="")
    base.update(kwargs)
    return Model(**base)


def test_default_openai_compat() -> None:
    compat = detect_compat(_m(base_url="https://api.openai.com/v1"))
    assert compat.supports_store is True
    assert compat.supports_developer_role is True
    assert compat.supports_reasoning_effort is True
    assert compat.max_tokens_field == "max_completion_tokens"
    assert compat.thinking_format == "openai"
    assert compat.cache_control_format is None
    assert compat.supports_strict_mode is True


def test_openrouter_thinking_format() -> None:
    compat = detect_compat(
        _m(provider="openrouter", base_url="https://openrouter.ai/api/v1")
    )
    assert compat.thinking_format == "openrouter"
    assert compat.supports_strict_mode is True


def test_openrouter_anthropic_cache_control() -> None:
    compat = detect_compat(
        _m(
            id="anthropic/claude-3-5-sonnet",
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
        )
    )
    assert compat.cache_control_format == "anthropic"


def test_openrouter_non_anthropic_cache_control_none() -> None:
    compat = detect_compat(
        _m(
            id="openai/gpt-4",
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
        )
    )
    assert compat.cache_control_format is None


def test_deepseek_thinking_format() -> None:
    compat = detect_compat(
        _m(provider="deepseek", base_url="https://api.deepseek.com/v1")
    )
    assert compat.thinking_format == "deepseek"
    assert compat.requires_reasoning_content_on_assistant_messages is True
    assert compat.supports_store is False
    assert compat.supports_developer_role is False


def test_xai_grok_no_reasoning_effort() -> None:
    compat = detect_compat(_m(provider="xai", base_url="https://api.x.ai/v1"))
    assert compat.supports_reasoning_effort is False
    assert compat.supports_store is False


def test_zai_thinking_format() -> None:
    compat = detect_compat(_m(provider="zai", base_url="https://api.z.ai/v1"))
    assert compat.thinking_format == "zai"
    assert compat.supports_reasoning_effort is False
    assert compat.supports_store is False


def test_together_uses_max_tokens_field() -> None:
    compat = detect_compat(
        _m(provider="together", base_url="https://api.together.ai/v1")
    )
    assert compat.max_tokens_field == "max_tokens"
    assert compat.supports_reasoning_effort is False
    assert compat.supports_strict_mode is False
    assert compat.supports_long_cache_retention is False
    assert compat.thinking_format == "together"


def test_together_xyz_url_detected() -> None:
    compat = detect_compat(_m(base_url="https://api.together.xyz/v1"))
    assert compat.thinking_format == "together"


def test_moonshot_compat() -> None:
    compat = detect_compat(
        _m(provider="moonshotai", base_url="https://api.moonshot.cn/v1")
    )
    assert compat.max_tokens_field == "max_tokens"
    assert compat.supports_reasoning_effort is False
    assert compat.supports_strict_mode is False


def test_moonshot_cn_alias() -> None:
    compat = detect_compat(_m(provider="moonshotai-cn"))
    assert compat.supports_reasoning_effort is False


def test_cerebras_non_standard() -> None:
    compat = detect_compat(_m(provider="cerebras", base_url="https://cerebras.ai/v1"))
    assert compat.supports_store is False
    assert compat.supports_developer_role is False


def test_chutes_uses_max_tokens() -> None:
    compat = detect_compat(_m(base_url="https://chutes.ai/v1"))
    assert compat.max_tokens_field == "max_tokens"
    assert compat.supports_store is False


def test_opencode_non_standard() -> None:
    compat = detect_compat(_m(provider="opencode", base_url="https://opencode.ai/v1"))
    assert compat.supports_store is False


def test_url_substring_detection_wins_over_unknown_provider() -> None:
    """Pi parity: if provider id is unknown, base_url substring drives detection."""

    compat = detect_compat(
        _m(provider="my-custom", base_url="https://openrouter.ai/proxy")
    )
    assert compat.thinking_format == "openrouter"


def test_get_compat_returns_detection_when_no_override() -> None:
    m = _m(provider="openai", base_url="https://api.openai.com/v1")
    compat = get_compat(m)
    assert isinstance(compat, OpenAICompletionsCompat)
    assert compat.thinking_format == "openai"


def test_get_compat_honors_dict_override(monkeypatch) -> None:
    """If a model carries an explicit ``compat`` dict, it overrides detection."""

    m = _m(provider="openai", base_url="https://api.openai.com/v1")
    # Aelix Model is a frozen dataclass; opportunistic ``compat`` attr.
    object.__setattr__(m, "compat", {"thinking_format": "deepseek"})
    compat = get_compat(m)
    assert compat.thinking_format == "deepseek"


def test_compat_has_17_fields() -> None:
    """Pi parity: ``ResolvedOpenAICompletionsCompat`` has 17 fields."""

    assert len(OpenAICompletionsCompat.__dataclass_fields__) == 17


def test_cloudflare_workers_ai_long_cache_disabled() -> None:
    compat = detect_compat(_m(base_url="https://api.cloudflare.com/v1"))
    assert compat.supports_long_cache_retention is False
    assert compat.supports_store is False


def test_cloudflare_ai_gateway_strict_mode_disabled() -> None:
    compat = detect_compat(_m(base_url="https://gateway.ai.cloudflare.com/v1"))
    assert compat.supports_strict_mode is False
    assert compat.supports_reasoning_effort is False
    assert compat.max_tokens_field == "max_tokens"
