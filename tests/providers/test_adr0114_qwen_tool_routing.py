"""Sprint 6h₁₃ · ADR-0114 — OpenRouter Qwen3 tool-calling fixes.

Three compounding bugs blocked structured tool calls for
``qwen/qwen3.6-35b-a3b`` (and similar OpenRouter reasoning models):

1. OpenRouter load-balanced onto the "Ambient" endpoint, which streams
   only the ``<think>`` block then ``finish_reason=stop`` — no
   ``tool_calls``. Fixed by seeding ``open_router_routing`` to ignore it.
2. The OpenAI **Python** SDK rejects ``reasoning`` / ``provider`` as
   top-level kwargs (Pi's TS SDK forwards them); fixed by relocating the
   extension params into ``extra_body`` at the SDK boundary.
3. ``maxTokens == contextWindow`` (127 catalog models) requested the full
   window as *output* → 400 on strict endpoints; fixed by omitting the
   cap when it is meaningless.
"""

from __future__ import annotations

from typing import Any

from aelix_ai.providers._openai_compat import detect_compat, get_compat
from aelix_ai.providers.openai_completions import (
    _relocate_extra_body_params,
    build_params,
)
from aelix_ai.streaming import Context, Model

_QWEN_ID = "qwen/qwen3.6-35b-a3b"
_EXPECTED_ROUTING = {"ignore": ["Ambient"]}


def _model(**kwargs: Any) -> Model:
    base: dict[str, Any] = dict(
        api="openai-completions",
        id=_QWEN_ID,
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        reasoning=True,
        input=["text"],
        context_window=262144,
        max_tokens=4096,
    )
    base.update(kwargs)
    return Model(**base)


# === Bug 1: provider routing avoids the broken endpoint ===


def test_qwen_gets_ignore_ambient_routing() -> None:
    routing = detect_compat(_model()).open_router_routing
    assert routing == _EXPECTED_ROUTING
    # ...and survives the get_compat merge.
    assert get_compat(_model()).open_router_routing == _EXPECTED_ROUTING


def test_routing_override_is_copied_not_shared() -> None:
    """Mutating one result must not corrupt the shared policy dict."""
    first = detect_compat(_model()).open_router_routing
    first["ignore"].append("Tampered")
    second = detect_compat(_model()).open_router_routing
    assert second == _EXPECTED_ROUTING


def test_non_listed_openrouter_model_has_no_routing() -> None:
    other = _model(id="qwen/qwen3.6-27b")
    assert detect_compat(other).open_router_routing == {}


def test_non_openrouter_model_has_no_routing() -> None:
    local = _model(
        id=_QWEN_ID, provider="ollama", base_url="http://localhost:11434/v1"
    )
    assert detect_compat(local).open_router_routing == {}


def test_routing_flows_onto_build_params_provider() -> None:
    model = _model()
    params = build_params(model, Context(), None, get_compat(model), "short")
    assert params["provider"] == _EXPECTED_ROUTING


# === Bug 2: extension params relocate into extra_body ===


def test_relocate_moves_openrouter_extensions() -> None:
    params: dict[str, Any] = {
        "model": "m",
        "messages": [],
        "stream": True,
        "tools": [{"x": 1}],
        "tool_choice": "auto",
        "reasoning_effort": "high",  # native OpenAI — must stay
        "max_tokens": 1024,  # native — must stay
        "reasoning": {"effort": "none"},
        "provider": {"ignore": ["Ambient"]},
        "enable_thinking": True,
        "chat_template_kwargs": {"enable_thinking": False},
        "thinking": {"type": "enabled"},
        "tool_stream": True,
        "providerOptions": {"gateway": {"only": ["x"]}},
        "prompt_cache_retention": "24h",
    }
    out = _relocate_extra_body_params(params)
    extra = out["extra_body"]
    for key in (
        "reasoning",
        "provider",
        "enable_thinking",
        "chat_template_kwargs",
        "thinking",
        "tool_stream",
        "providerOptions",
        "prompt_cache_retention",
    ):
        assert key not in out, f"{key} should have moved into extra_body"
        assert key in extra
    # Native params stay top-level.
    for key in ("model", "messages", "stream", "tools", "tool_choice", "reasoning_effort", "max_tokens"):
        assert key in out
    assert "extra_body" not in extra


def test_relocate_merges_existing_extra_body() -> None:
    params: dict[str, Any] = {
        "extra_body": {"custom": 1},
        "reasoning": {"effort": "low"},
    }
    out = _relocate_extra_body_params(params)
    assert out["extra_body"] == {"custom": 1, "reasoning": {"effort": "low"}}
    assert "reasoning" not in out


def test_relocate_noop_without_extensions() -> None:
    params: dict[str, Any] = {"model": "m", "messages": [], "max_tokens": 8}
    out = _relocate_extra_body_params(params)
    assert "extra_body" not in out
    assert out == {"model": "m", "messages": [], "max_tokens": 8}


def test_relocate_top_level_wins_on_extra_body_collision() -> None:
    """Documented precedence: a relocated top-level key overrides a
    pre-existing ``extra_body`` entry of the same name."""
    params: dict[str, Any] = {
        "extra_body": {"reasoning": "OLD"},
        "reasoning": "NEW",
    }
    out = _relocate_extra_body_params(params)
    assert out["extra_body"]["reasoning"] == "NEW"
    assert "reasoning" not in out


# === Bug 3: max_tokens omitted when >= context_window ===


def test_max_tokens_omitted_when_equals_context_window() -> None:
    model = _model(max_tokens=262144, context_window=262144)
    params = build_params(model, Context(), None, get_compat(model), "short")
    assert "max_tokens" not in params
    assert "max_completion_tokens" not in params


def test_max_tokens_sent_when_below_context_window() -> None:
    # OpenRouter compat uses the ``max_completion_tokens`` field.
    model = _model(max_tokens=8192, context_window=262144)
    params = build_params(model, Context(), None, get_compat(model), "short")
    assert params["max_completion_tokens"] == 8192


def test_max_tokens_sent_when_context_window_unknown() -> None:
    # context_window == 0 means "unknown" — keep the cap rather than drop it.
    model = _model(max_tokens=4096, context_window=0)
    params = build_params(model, Context(), None, get_compat(model), "short")
    assert params["max_completion_tokens"] == 4096


def test_max_tokens_omitted_when_above_context_window() -> None:
    # Locks the ``>=`` semantics (2 catalog models have maxTokens > window).
    model = _model(max_tokens=300000, context_window=262144)
    params = build_params(model, Context(), None, get_compat(model), "short")
    assert "max_tokens" not in params
    assert "max_completion_tokens" not in params
