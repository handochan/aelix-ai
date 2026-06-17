"""ADR-0135 (P0 #1) — provider-layer reasoning resolution.

Layer 2 (OpenAI-completions): ``build_params`` applies ``thinkingLevelMap``
in the deepseek + openrouter branches (pi ``openai-completions.ts:567-582``).

Layer 3 (Anthropic): the request builder now emits a ``thinking`` param —
adaptive ``effort`` for Opus 4.6+/Sonnet 4.6, ``budget_tokens`` for older
reasoning models — plus the interleaved-thinking beta header for the
budget-based path (pi ``anthropic.ts`` + ``simple-options.ts``, SHA 734e08e).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.providers._anthropic_transforms import (
    INTERLEAVED_THINKING_BETA,
    adjust_max_tokens_for_thinking,
    clamp_reasoning,
    map_thinking_level_to_effort,
    resolve_anthropic_thinking,
)
from aelix_ai.providers._openai_compat import get_compat
from aelix_ai.providers.anthropic import _with_interleaved_beta, stream_anthropic
from aelix_ai.providers.openai_completions import (
    OpenAICompletionsOptions,
    build_params,
    stream_simple_openai_completions,
)
from aelix_ai.streaming import Context, Model, SimpleStreamOptions


def _or_model(**kwargs: Any) -> Model:
    base: dict[str, Any] = dict(
        api="openai-completions",
        id="gpt-4",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        input=["text"],
        max_tokens=1024,
        reasoning=True,
    )
    base.update(kwargs)
    return Model(**base)


# === Layer 2: OpenAI thinkingLevelMap application ==========================


def test_openrouter_thinking_level_map_applied() -> None:
    model = _or_model(thinking_level_map={"high": "high", "xhigh": "max"})
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="xhigh")
    params = build_params(model, Context(), opts, compat, "short")
    # xhigh → "max" via the model's thinkingLevelMap (pi:577-578).
    assert params["reasoning"] == {"effort": "max"}


def test_openrouter_no_map_passes_level_through() -> None:
    model = _or_model()  # no thinking_level_map
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts, compat, "short")
    assert params["reasoning"] == {"effort": "high"}


def test_openrouter_off_null_omits_reasoning() -> None:
    model = _or_model(thinking_level_map={"off": None})
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    # Explicit ``off: null`` → omit ``reasoning`` entirely (pi:580).
    assert "reasoning" not in params


def test_openrouter_off_string_used() -> None:
    model = _or_model(thinking_level_map={"off": "disable"})
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params["reasoning"] == {"effort": "disable"}


def test_openrouter_no_map_off_defaults_none() -> None:
    """Regression: pre-ADR-0135 behaviour preserved when no map present."""

    model = _or_model()
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params["reasoning"] == {"effort": "none"}


def test_deepseek_thinking_level_map_applied() -> None:
    model = _or_model(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        thinking_level_map={"medium": "med-native"},
    )
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="medium")
    params = build_params(model, Context(), opts, compat, "short")
    assert params["thinking"] == {"type": "enabled"}
    assert params["reasoning_effort"] == "med-native"


def test_deepseek_off_disables_thinking() -> None:
    model = _or_model(
        provider="deepseek", base_url="https://api.deepseek.com/v1"
    )
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in params


def test_together_thinking_level_map_applied() -> None:
    model = _or_model(
        provider="together",
        base_url="https://api.together.ai/v1",
        thinking_level_map={"high": "hi-native"},
    )
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts, compat, "short")
    assert params["reasoning"] == {"enabled": True}
    if compat.supports_reasoning_effort:
        # pi:589-590 only sets reasoning_effort when supports_reasoning_effort.
        assert params["reasoning_effort"] == "hi-native"


def test_default_openai_style_thinking_level_map_applied() -> None:
    # OpenAI-style fallback branch (no special thinking_format) maps too.
    model = _or_model(
        provider="openai",
        base_url="https://api.openai.com/v1",
        thinking_level_map={"high": "hi-native"},
    )
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts, compat, "short")
    if compat.supports_reasoning_effort:
        assert params["reasoning_effort"] == "hi-native"


def test_default_off_string_emitted_when_no_effort() -> None:
    # pi:595-600 — with no requested effort, an explicit string off mapping is
    # emitted on the OpenAI-style path.
    model = _or_model(
        provider="openai",
        base_url="https://api.openai.com/v1",
        thinking_level_map={"off": "off-native"},
    )
    compat = get_compat(model)
    if not compat.supports_reasoning_effort:
        return
    params = build_params(model, Context(), None, compat, "short")
    assert params.get("reasoning_effort") == "off-native"


# === Layer 2: end-to-end clamp → map through stream_simple ================


class _EmptyAsyncIter:
    def __aiter__(self) -> _EmptyAsyncIter:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _RawResp:
    status_code = 200
    headers: dict[str, Any] = {}


class _RawWrap:
    def __init__(self) -> None:
        self.http_response = _RawResp()

    def parse(self) -> _EmptyAsyncIter:
        return _EmptyAsyncIter()


class _WithRaw:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    async def create(self, **kwargs: Any) -> _RawWrap:
        self._captured["params"] = kwargs
        return _RawWrap()


class _FakeOpenAI:
    def __init__(self) -> None:
        self.captured: dict[str, Any] = {}
        self.chat = type(
            "_Chat", (), {"completions": type(
                "_Comp", (), {"with_raw_response": _WithRaw(self.captured)}
            )()}
        )()


async def test_stream_simple_clamps_then_maps() -> None:
    """End-to-end: stream_simple clamps xhigh→high (model lacks xhigh) BEFORE
    build_params applies the thinkingLevelMap (high→native)."""

    model = _or_model(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        thinking_level_map={"high": "hi-native"},  # xhigh absent → unsupported
    )
    fake = _FakeOpenAI()
    opts = SimpleStreamOptions(reasoning="xhigh", client=fake, api_key="k")
    async for _ in stream_simple_openai_completions(model, Context(), opts):
        pass
    # xhigh clamped to high, then high mapped to "hi-native".
    assert fake.captured["params"]["reasoning_effort"] == "hi-native"


# === Layer 3: Anthropic thinking helpers (unit) ============================


def _ant_model(**kwargs: Any) -> Model:
    base: dict[str, Any] = dict(
        api="anthropic-messages",
        id="claude-3-7-sonnet",
        provider="anthropic",
    )
    base.update(kwargs)
    return Model(**base)


def test_clamp_reasoning_xhigh_to_high() -> None:
    assert clamp_reasoning("xhigh") == "high"
    assert clamp_reasoning("medium") == "medium"


def test_adjust_max_tokens_carves_budget_below_max() -> None:
    # Roomy model: budget stays at the medium default, max_tokens capped.
    max_tokens, budget = adjust_max_tokens_for_thinking(64000, 64000, "medium")
    assert max_tokens == 64000
    assert budget == 8192


def test_adjust_max_tokens_shrinks_budget_when_tight() -> None:
    # Tight model: budget exceeds max_tokens → shrink to leave 1024 output.
    max_tokens, budget = adjust_max_tokens_for_thinking(4096, 4096, "high")
    assert max_tokens == 4096
    assert budget == 4096 - 1024


def test_map_effort_prefers_thinking_level_map() -> None:
    model = _ant_model(thinking_level_map={"xhigh": "max"})
    assert map_thinking_level_to_effort(model, "xhigh") == "max"
    # Unmapped levels fall back to the coarse mapping (pi anthropic.ts:715-725).
    assert map_thinking_level_to_effort(model, "minimal") == "low"
    assert map_thinking_level_to_effort(model, "low") == "low"
    assert map_thinking_level_to_effort(model, "medium") == "medium"
    assert map_thinking_level_to_effort(model, "high") == "high"
    # Unknown / None default to "high".
    assert map_thinking_level_to_effort(model, None) == "high"
    assert map_thinking_level_to_effort(model, "ultra") == "high"


def test_adjust_max_tokens_xhigh_uses_high_budget() -> None:
    # xhigh clamps to high internally (clamp_reasoning) → 16384 budget.
    max_tokens, budget = adjust_max_tokens_for_thinking(64000, 64000, "xhigh")
    assert budget == 16384


def test_resolve_thinking_non_reasoning_model_emits_nothing() -> None:
    model = _ant_model(reasoning=False, max_tokens=8192)
    extra, max_tokens, needs_beta = resolve_anthropic_thinking(model, "high", 8192)
    assert extra == {}
    assert max_tokens == 8192  # passthrough, unchanged
    # Deliberate narrower scope than pi: aelix sends the interleaved beta only
    # on the active budget-thinking path, so a non-reasoning model does NOT.
    assert needs_beta is False


def test_resolve_thinking_off_does_not_need_beta() -> None:
    model = _ant_model(id="claude-3-7-sonnet", reasoning=True, max_tokens=8192)
    extra, _max, needs_beta = resolve_anthropic_thinking(model, None, 8192)
    assert extra == {"thinking": {"type": "disabled"}}
    # Thinking off → not active → no interleaved beta (narrower than pi).
    assert needs_beta is False


def test_with_interleaved_beta_appends() -> None:
    assert _with_interleaved_beta(None, False) is None
    out = _with_interleaved_beta({"anthropic-beta": "oauth-2025-04-20"}, True)
    assert out is not None
    assert out["anthropic-beta"] == (
        f"oauth-2025-04-20,{INTERLEAVED_THINKING_BETA}"
    )


# === Layer 3: Anthropic request integration (mock client) ==================


@dataclass
class _MockFinalMessage:
    stop_reason: str = "end_turn"


@dataclass
class _MockResponse:
    status_code: int = 200
    headers: dict = field(default_factory=lambda: {"x-test": "1"})


class _MockStream:
    def __init__(self, response: Any = None) -> None:
        self.response = response

    async def __aenter__(self) -> _MockStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def __aiter__(self) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover

    async def get_final_message(self) -> _MockFinalMessage:
        return _MockFinalMessage()


class _MockMessages:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    def stream(self, **params: Any) -> _MockStream:
        self._captured["params"] = params
        return _MockStream(response=_MockResponse())


class _MockAnthropicClient:
    def __init__(self, captured: dict) -> None:
        self.messages = _MockMessages(captured)


async def _capture_anthropic_params(
    model: Model, reasoning: str | None
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    opts = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(captured),
        reasoning=reasoning,
    )
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    return captured["params"]


async def test_anthropic_adaptive_uses_thinking_level_map() -> None:
    # thinking_level_map remaps the REQUESTED level → proves the map (not the
    # coarse fallback) drives output_config end-to-end through stream_anthropic.
    model = _ant_model(
        id="claude-opus-4-6",
        reasoning=True,
        max_tokens=64000,
        thinking_level_map={"high": "max"},
    )
    params = await _capture_anthropic_params(model, "high")
    assert params["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert params["output_config"] == {"effort": "max"}


async def test_anthropic_adaptive_fallback_effort_without_map() -> None:
    model = _ant_model(id="claude-opus-4-7", reasoning=True, max_tokens=64000)
    params = await _capture_anthropic_params(model, "medium")
    # No map key for "medium" → coarse fallback "medium".
    assert params["output_config"] == {"effort": "medium"}


async def test_anthropic_budget_emits_budget_tokens() -> None:
    model = _ant_model(id="claude-3-7-sonnet", reasoning=True, max_tokens=64000)
    params = await _capture_anthropic_params(model, "medium")
    assert params["thinking"]["type"] == "enabled"
    assert params["thinking"]["budget_tokens"] == 8192
    assert params["max_tokens"] == 64000


async def test_anthropic_off_disables_thinking() -> None:
    model = _ant_model(id="claude-3-7-sonnet", reasoning=True, max_tokens=64000)
    params = await _capture_anthropic_params(model, None)
    assert params["thinking"] == {"type": "disabled"}


async def test_anthropic_non_reasoning_omits_thinking() -> None:
    model = _ant_model(id="claude-haiku", reasoning=False, max_tokens=8192)
    params = await _capture_anthropic_params(model, "high")
    assert "thinking" not in params


# === Layer 3: interleaved-thinking beta header =============================


async def test_anthropic_budget_sets_interleaved_beta(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> _MockAnthropicClient:
        captured["default_headers"] = kwargs.get("default_headers")
        return _MockAnthropicClient({})

    monkeypatch.setattr(
        "aelix_ai.providers.anthropic.create_async_client", fake_create
    )
    model = _ant_model(id="claude-3-7-sonnet", reasoning=True, max_tokens=64000)
    opts = SimpleStreamOptions(api_key="sk-test", reasoning="high")
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    headers = captured["default_headers"] or {}
    assert INTERLEAVED_THINKING_BETA in headers.get("anthropic-beta", "")


async def test_anthropic_adaptive_skips_interleaved_beta(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> _MockAnthropicClient:
        captured["default_headers"] = kwargs.get("default_headers")
        return _MockAnthropicClient({})

    monkeypatch.setattr(
        "aelix_ai.providers.anthropic.create_async_client", fake_create
    )
    model = _ant_model(id="claude-opus-4-7", reasoning=True, max_tokens=64000)
    opts = SimpleStreamOptions(api_key="sk-test", reasoning="high")
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    headers = captured["default_headers"] or {}
    assert INTERLEAVED_THINKING_BETA not in headers.get("anthropic-beta", "")


async def test_anthropic_oauth_path_appends_interleaved_beta(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> _MockAnthropicClient:
        captured["default_headers"] = kwargs.get("default_headers")
        return _MockAnthropicClient({})

    monkeypatch.setattr(
        "aelix_ai.providers.anthropic.create_async_client", fake_create
    )
    model = _ant_model(id="claude-3-7-sonnet", reasoning=True, max_tokens=64000)
    # OAuth token (sk-ant-oat…) routes through the oauth header branch.
    opts = SimpleStreamOptions(api_key="sk-ant-oat-budget", reasoning="high")
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    beta = (captured["default_headers"] or {}).get("anthropic-beta", "")
    assert "oauth-2025-04-20" in beta
    assert INTERLEAVED_THINKING_BETA in beta


async def test_anthropic_non_reasoning_omits_interleaved_beta(
    monkeypatch: Any,
) -> None:
    # Deliberate narrower scope than pi: non-reasoning models get no interleaved
    # beta (aelix gates it on active budget-thinking, preserving the
    # caller-anthropic-beta-wins contract — see ADR-0135).
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> _MockAnthropicClient:
        captured["default_headers"] = kwargs.get("default_headers")
        return _MockAnthropicClient({})

    monkeypatch.setattr(
        "aelix_ai.providers.anthropic.create_async_client", fake_create
    )
    model = _ant_model(id="claude-haiku", reasoning=False, max_tokens=8192)
    opts = SimpleStreamOptions(api_key="sk-test", reasoning="high")
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    beta = (captured["default_headers"] or {}).get("anthropic-beta", "")
    assert INTERLEAVED_THINKING_BETA not in beta
