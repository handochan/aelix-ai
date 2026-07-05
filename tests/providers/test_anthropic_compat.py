"""ADR-0190 (#36 anthropic side) — Anthropic-messages compat detection tests.

Pi parity: ``getAnthropicCompat`` (``anthropic.ts:167-178``). Every detected
provider / URL substring → expected compat field overrides, plus the
``model.compat`` snake_case + camelCase override-merge path.
"""

from __future__ import annotations

from aelix_ai.providers._anthropic_compat import (
    AnthropicMessagesCompat,
    detect_compat,
    get_compat,
)
from aelix_ai.streaming import Model


def _m(**kwargs) -> Model:
    base = dict(api="anthropic-messages", id="m", provider="anthropic", base_url="")
    base.update(kwargs)
    return Model(**base)


def test_default_anthropic_baseline() -> None:
    """Plain anthropic model → all True except affinity False."""

    compat = detect_compat(_m(base_url="https://api.anthropic.com"))
    assert compat.supports_eager_tool_input_streaming is True
    assert compat.supports_long_cache_retention is True
    assert compat.send_session_affinity_headers is False
    assert compat.supports_cache_control_on_tools is True


def test_fireworks_detection() -> None:
    """Fireworks → all False except affinity True."""

    compat = detect_compat(
        _m(provider="fireworks", base_url="https://api.fireworks.ai/inference/v1")
    )
    assert compat.supports_eager_tool_input_streaming is False
    assert compat.supports_long_cache_retention is False
    assert compat.send_session_affinity_headers is True
    assert compat.supports_cache_control_on_tools is False


def test_cloudflare_ai_gateway_anthropic_affinity() -> None:
    """cloudflare-ai-gateway with 'anthropic' in base_url → affinity True."""

    compat = detect_compat(
        _m(
            provider="cloudflare-ai-gateway",
            base_url="https://gateway.ai.cloudflare.com/v1/acct/gw/anthropic",
        )
    )
    assert compat.send_session_affinity_headers is True
    # The other three stay at baseline (only fireworks disables them).
    assert compat.supports_eager_tool_input_streaming is True
    assert compat.supports_long_cache_retention is True
    assert compat.supports_cache_control_on_tools is True


def test_cloudflare_ai_gateway_non_anthropic_no_affinity() -> None:
    """cloudflare-ai-gateway without 'anthropic' in base_url → affinity False."""

    compat = detect_compat(
        _m(
            provider="cloudflare-ai-gateway",
            base_url="https://gateway.ai.cloudflare.com/v1/acct/gw/openai",
        )
    )
    assert compat.send_session_affinity_headers is False


def test_provider_casing_normalized() -> None:
    """Aelix divergence (NOT pi parity): ``detect_compat`` compares the
    provider case-insensitively via ``.lower()``, consistent with
    ``_openai_compat.detect_compat``. Pi compares case-sensitively
    (``anthropic.ts:169``), so ``"Fireworks"`` would NOT match there.
    """

    compat = detect_compat(_m(provider="Fireworks"))
    assert compat.send_session_affinity_headers is True
    assert compat.supports_eager_tool_input_streaming is False


def test_get_compat_returns_detection_when_no_override() -> None:
    m = _m(provider="anthropic", base_url="https://api.anthropic.com")
    compat = get_compat(m)
    assert isinstance(compat, AnthropicMessagesCompat)
    assert compat.send_session_affinity_headers is False
    assert compat.supports_eager_tool_input_streaming is True


def test_get_compat_honors_snake_case_override() -> None:
    """A snake_case ``compat`` dict overrides detection."""

    m = _m(provider="anthropic", compat={"send_session_affinity_headers": True})
    compat = get_compat(m)
    assert compat.send_session_affinity_headers is True


def test_get_compat_honors_camel_case_override() -> None:
    """A camelCase ``compat`` dict flips the fireworks default back off."""

    m = _m(
        provider="fireworks",
        compat={"sendSessionAffinityHeaders": False},
    )
    # Detection alone would set affinity True; the camelCase override wins.
    assert detect_compat(m).send_session_affinity_headers is True
    compat = get_compat(m)
    assert compat.send_session_affinity_headers is False
    # Un-overridden fields keep their detected (fireworks) values.
    assert compat.supports_eager_tool_input_streaming is False
    assert compat.supports_long_cache_retention is False
    assert compat.supports_cache_control_on_tools is False


def test_get_compat_object_override() -> None:
    """A dataclass-style object override (snake_case attrs) is honored."""

    override = AnthropicMessagesCompat(supports_cache_control_on_tools=False)
    m = _m(provider="anthropic")
    object.__setattr__(m, "compat", override)
    compat = get_compat(m)
    assert compat.supports_cache_control_on_tools is False


def test_compat_has_4_fields() -> None:
    """Pi parity: ``AnthropicMessagesCompat`` has exactly 4 fields."""

    assert len(AnthropicMessagesCompat.__dataclass_fields__) == 4
