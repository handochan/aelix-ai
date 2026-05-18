"""Sprint 6b (Phase 4.2, §G) — OpenRouter routing tests.

Pi parity: OpenRouter is NOT a separate KnownApi (P-48). Models
configured with ``provider="openrouter"`` + ``baseUrl`` pointing at
``openrouter.ai`` flow through the ``openai-completions`` adapter with
auto-detected compat (``thinking_format == "openrouter"``,
``OPENROUTER_API_KEY`` env var, ``reasoning: {effort}`` param shape).
"""

from __future__ import annotations

import pytest
from aelix_ai.providers._env_api_keys import get_env_api_key
from aelix_ai.providers._openai_compat import detect_compat, get_compat
from aelix_ai.providers.openai_completions import (
    OpenAICompletionsOptions,
    build_params,
)
from aelix_ai.streaming import Context, Model


def _openrouter_model(
    *, model_id: str = "openai/gpt-4", reasoning: bool = False
) -> Model:
    return Model(
        api="openai-completions",
        id=model_id,
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        reasoning=reasoning,
    )


def test_openrouter_compat_thinking_format() -> None:
    compat = detect_compat(_openrouter_model())
    assert compat.thinking_format == "openrouter"


def test_openrouter_env_var_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-token")
    assert get_env_api_key("openrouter") == "or-test-token"


def test_openrouter_reasoning_effort_param_shape() -> None:
    model = _openrouter_model(reasoning=True)
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts, compat, "short")
    assert params.get("reasoning") == {"effort": "high"}


def test_openrouter_reasoning_disabled_when_no_effort() -> None:
    """No reasoning_effort still produces a ``reasoning: {effort: "none"}`` param.

    Pi parity: ``openai-completions.ts:580-582`` falls back to the
    ``"off"`` slot of ``thinkingLevelMap`` (or the literal ``"none"``).
    """

    model = _openrouter_model(reasoning=True)
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params.get("reasoning") == {"effort": "none"}


def test_openrouter_anthropic_model_uses_anthropic_cache_control() -> None:
    """Models like ``anthropic/claude-3-*`` on OpenRouter get cache_control."""

    model = _openrouter_model(model_id="anthropic/claude-3-5-sonnet")
    compat = get_compat(model)
    assert compat.cache_control_format == "anthropic"


def test_openrouter_non_anthropic_no_cache_control() -> None:
    model = _openrouter_model(model_id="openai/gpt-4")
    compat = get_compat(model)
    assert compat.cache_control_format is None


def test_openrouter_supports_strict_mode_by_default() -> None:
    compat = detect_compat(_openrouter_model())
    assert compat.supports_strict_mode is True


def test_openrouter_compat_supports_long_cache_retention() -> None:
    compat = detect_compat(_openrouter_model())
    assert compat.supports_long_cache_retention is True


def test_openrouter_routing_dict_forwarded_when_provided() -> None:
    """``model.compat.openRouterRouting`` flows onto ``params['provider']``."""

    model = _openrouter_model()
    # Aelix Model is frozen — opportunistic ``compat`` attribute lets
    # callers/test fixtures inject one without touching the dataclass.
    object.__setattr__(model, "compat", {"openRouterRouting": {"order": ["anthropic"]}})
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params.get("provider") == {"order": ["anthropic"]}


def test_openrouter_url_substring_detection() -> None:
    """``baseUrl`` containing ``openrouter.ai`` alone triggers detection."""

    m = Model(
        api="openai-completions",
        id="m",
        provider="my-custom",
        base_url="https://proxy.openrouter.ai/v1",
    )
    compat = detect_compat(m)
    assert compat.thinking_format == "openrouter"
