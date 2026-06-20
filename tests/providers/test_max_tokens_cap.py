"""P0 #6 — provider adapters honor ``options.max_tokens`` (REAL payload-level cap).

The compaction summarizer sets ``SimpleStreamOptions.max_tokens`` to a bounded
value (``floor(0.8 * reserveTokens)`` etc.); these tests prove the cap reaches
the request payload — that it is NOT a dangling cosmetic field. Pi semantics:
``base.maxTokens = options.maxTokens ?? model.maxTokens``.

- OpenAI-completions: assert ``build_params`` emits ``options.max_tokens`` as the
  value of the ``max_tokens`` / ``max_completion_tokens`` field, taking
  precedence over the model default AND bypassing the ADR-0114 context-window
  guard (the cap is explicit + small).
- Anthropic: assert the captured SDK ``params["max_tokens"]`` equals
  ``options.max_tokens`` for a non-reasoning summarizer model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.providers._openai_compat import detect_compat, get_compat
from aelix_ai.providers.anthropic import stream_anthropic
from aelix_ai.providers.openai_completions import build_params
from aelix_ai.streaming import Context, Model, SimpleStreamOptions


def _oai_model(**kwargs: Any) -> Model:
    base: dict[str, Any] = dict(
        api="openai-completions",
        id="gpt-4o-mini",
        provider="openai",
        base_url="https://api.openai.com/v1",
        input=["text"],
        max_tokens=4096,
    )
    base.update(kwargs)
    return Model(**base)


# === OpenAI-completions ====================================================


def test_openai_options_max_tokens_overrides_model_default() -> None:
    model = _oai_model(max_tokens=4096)
    compat = detect_compat(model)
    opts = SimpleStreamOptions(max_tokens=13107)  # floor(0.8 * 16384)
    params = build_params(model, Context(), opts, compat, "short")
    # gpt-4o-mini uses the ``max_completion_tokens`` field.
    assert params["max_completion_tokens"] == 13107
    assert "max_tokens" not in params


def test_openai_options_max_tokens_bypasses_context_window_guard() -> None:
    # ADR-0114: when max_tokens >= context_window the model-default cap is
    # omitted. An explicit options.max_tokens must STILL be emitted (it is a
    # small intentional cap, never the full window).
    model = _oai_model(max_tokens=128_000, context_window=128_000)
    compat = detect_compat(model)
    opts = SimpleStreamOptions(max_tokens=8192)
    params = build_params(model, Context(), opts, compat, "short")
    assert params["max_completion_tokens"] == 8192


def test_openai_no_options_max_tokens_keeps_model_default() -> None:
    # Regression: without options.max_tokens the existing model-default path
    # (and ADR-0114 guard) is unchanged.
    model = _oai_model(max_tokens=2048, provider="together", base_url="https://api.together.ai/v1")
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params["max_tokens"] == 2048


def test_openai_max_tokens_field_name_respected() -> None:
    # ``together`` uses the ``max_tokens`` field name; the override must use it.
    model = _oai_model(provider="together", base_url="https://api.together.ai/v1")
    compat = get_compat(model)
    opts = SimpleStreamOptions(max_tokens=777)
    params = build_params(model, Context(), opts, compat, "short")
    assert params["max_tokens"] == 777
    assert "max_completion_tokens" not in params


# === Anthropic =============================================================


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
    def __init__(self, stream: _MockStream, captured: dict) -> None:
        self._stream = stream
        self._captured = captured

    def stream(self, **params: Any) -> _MockStream:
        self._captured["params"] = params
        return self._stream


class _MockAnthropicClient:
    def __init__(self, stream: _MockStream, captured: dict) -> None:
        self.messages = _MockMessages(stream, captured)


def _anthropic_model(**kwargs: Any) -> Model:
    base: dict[str, Any] = dict(
        api="anthropic-messages",
        id="claude-3-5-haiku",
        provider="anthropic",
        max_tokens=8192,
    )
    base.update(kwargs)
    return Model(**base)


async def test_anthropic_options_max_tokens_in_payload() -> None:
    captured: dict = {}
    stream = _MockStream(response=_MockResponse())
    opts = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(stream, captured),
        max_tokens=13107,  # floor(0.8 * 16384)
    )
    async for _ in stream_anthropic(_anthropic_model(), Context(), opts):
        pass
    # Non-reasoning model → the base output cap equals options.max_tokens,
    # replacing the ``model.max_tokens or 4096`` default.
    assert captured["params"]["max_tokens"] == 13107


async def test_anthropic_no_options_max_tokens_uses_model_default() -> None:
    captured: dict = {}
    stream = _MockStream(response=_MockResponse())
    opts = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(stream, captured),
    )
    async for _ in stream_anthropic(_anthropic_model(max_tokens=8192), Context(), opts):
        pass
    # No override → falls back to model.max_tokens (8192).
    assert captured["params"]["max_tokens"] == 8192
