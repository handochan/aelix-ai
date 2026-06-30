"""Issue #55 — Anthropic adapter correctness 3-pack (pi #5251 / #5666 / #5738).

One test group per sub-fix:

* (a) pi #5251 — ``temperature`` is forwarded for models that support it but
  suppressed for Opus 4.7+ (``compat.supportsTemperature: false``) and whenever
  thinking is enabled (:func:`build_params`).
* (b) pi #5666 — a ``refusal`` stop reason maps to ``error`` AND preserves the
  provider ``stop_details.explanation`` text into the surfaced error message
  (full adapter path via a mock SDK stream).
* (c) pi #5738 — 1h-TTL cache writes (``Usage.cache_write_1h``) are priced at
  2× the model's base input rate; 5m writes keep the ``cache_write`` rate
  (:func:`aelix_ai.models.calculate_cost`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from aelix_ai.models import calculate_cost
from aelix_ai.providers._anthropic_transforms import (
    build_params,
    map_stop_reason_with_details,
    supports_temperature,
)
from aelix_ai.providers.anthropic import stream_anthropic
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    Context,
    Model,
    ModelCost,
    SimpleStreamOptions,
    Usage,
)

# === (a) pi #5251 — temperature gating =======================================


def _older_model() -> Model:
    # No compat flag → supportsTemperature defaults True (older Claude).
    return Model(api="anthropic-messages", id="claude-3-5-sonnet", provider="anthropic")


def _opus_47_plus_model() -> Model:
    return Model(
        api="anthropic-messages",
        id="claude-opus-4-8",
        provider="anthropic",
        compat={"forceAdaptiveThinking": True, "supportsTemperature": False},
    )


def test_supports_temperature_defaults_true_without_compat() -> None:
    assert supports_temperature(_older_model()) is True


def test_supports_temperature_false_for_opus_47_plus() -> None:
    assert supports_temperature(_opus_47_plus_model()) is False


def test_temperature_present_for_older_model() -> None:
    params = build_params(
        _older_model(), "", [], [], temperature=0.7, thinking_enabled=False
    )
    assert params["temperature"] == 0.7


def test_temperature_omitted_for_opus_47_plus_model() -> None:
    params = build_params(
        _opus_47_plus_model(), "", [], [], temperature=0.7, thinking_enabled=False
    )
    assert "temperature" not in params


def test_temperature_omitted_when_thinking_enabled() -> None:
    # Even an older model drops temperature while extended/adaptive thinking is on.
    params = build_params(
        _older_model(), "", [], [], temperature=0.7, thinking_enabled=True
    )
    assert "temperature" not in params


def test_temperature_omitted_when_unset() -> None:
    params = build_params(_older_model(), "", [], [], temperature=None)
    assert "temperature" not in params


def test_catalog_opus_47_carries_supports_temperature_false() -> None:
    # pi marks BOTH opus-4-7 and opus-4-8 ``supportsTemperature: false``
    # (anthropic.models.ts @ f2e9d75); the "Opus 4.7+" docs in
    # _anthropic_transforms.py / streaming.py only hold if the catalog flags
    # opus-4-7 too (the DEFAULT anthropic model). Pin every provider that
    # carries opus-4-7 so the docs and catalog stay consistent.
    from aelix_ai.models_generated import MODELS

    for provider in ("anthropic", "cloudflare-ai-gateway", "opencode"):
        model = MODELS[provider]["claude-opus-4-7"]
        assert model.compat is not None
        assert model.compat["supportsTemperature"] is False
        assert supports_temperature(model) is False
    # opus-4-8 was already flagged (anthropic) — confirm it is unchanged, and
    # a model pi does NOT flag (sonnet 4.6) still defaults to True.
    assert supports_temperature(MODELS["anthropic"]["claude-opus-4-8"]) is False
    assert supports_temperature(MODELS["anthropic"]["claude-sonnet-4-6"]) is True


# === (b) pi #5666 — refusal preserves stop_details text ======================


@dataclass
class _RefusalStopDetails:
    explanation: str = "Declined: request violates the cyber policy."
    type: str = "refusal"
    category: str | None = "cyber"


@dataclass
class _MockFinalMessage:
    stop_reason: str = "end_turn"
    stop_details: Any = None


@dataclass
class _MockResponse:
    status_code: int = 200
    headers: dict = field(default_factory=dict)


class _MockStream:
    def __init__(self, final_message: _MockFinalMessage) -> None:
        self._final_message = final_message
        self.response = _MockResponse()

    async def __aenter__(self) -> _MockStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def __aiter__(self) -> AsyncIterator[Any]:
        if False:  # pragma: no cover - no content blocks for a pre-output refusal
            yield None

    async def get_final_message(self) -> _MockFinalMessage:
        return self._final_message


class _MockMessages:
    def __init__(self, stream: _MockStream) -> None:
        self._stream = stream

    def stream(self, **_params: Any) -> _MockStream:
        return self._stream


class _MockClient:
    def __init__(self, stream: _MockStream) -> None:
        self.messages = _MockMessages(stream)


async def _collect(it: AsyncIterator[Any]) -> list[Any]:
    return [ev async for ev in it]


def test_map_stop_reason_with_details_refusal_carries_explanation() -> None:
    reason, message = map_stop_reason_with_details(
        "refusal", _RefusalStopDetails(explanation="No way, José.")
    )
    assert reason == "error"
    assert message == "No way, José."


def test_map_stop_reason_with_details_refusal_dict_details() -> None:
    reason, message = map_stop_reason_with_details(
        "refusal", {"explanation": "dict explanation"}
    )
    assert reason == "error"
    assert message == "dict explanation"


def test_map_stop_reason_with_details_refusal_fallback_when_no_details() -> None:
    reason, message = map_stop_reason_with_details("refusal", None)
    assert reason == "error"
    assert message == "The model refused to complete the request"


def test_map_stop_reason_with_details_non_refusal_has_no_message() -> None:
    # Non-refusal reasons carry no error message; aelix's stop-reason map keeps
    # ``end_turn`` verbatim (existing convention) and uses pi's ``toolUse`` spelling.
    assert map_stop_reason_with_details("end_turn") == ("end_turn", None)
    assert map_stop_reason_with_details("tool_use") == ("toolUse", None)


async def test_refusal_error_event_surfaces_stop_details_text() -> None:
    explanation = "Declined: request violates the cyber policy."
    stream = _MockStream(
        _MockFinalMessage(
            stop_reason="refusal",
            stop_details=_RefusalStopDetails(explanation=explanation),
        )
    )
    opts = SimpleStreamOptions(api_key="sk-test", client=_MockClient(stream))
    model = Model(api="anthropic-messages", id="claude-3-test", provider="anthropic")
    out = await _collect(stream_anthropic(model, Context(), opts))
    err = next(ev for ev in out if isinstance(ev, AssistantErrorEvent))
    assert err.reason == "error"
    # The refusal explanation is surfaced verbatim, not the generic fallback.
    assert err.error_message == explanation


# === (c) pi #5738 — 1h cache-write priced at 2x input ========================


def _cost_model() -> Model:
    return Model(
        api="anthropic-messages",
        id="claude-test",
        provider="anthropic",
        cost=ModelCost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75),
    )


def test_one_hour_cache_write_priced_at_2x_input() -> None:
    # 100k cache-write tokens, 40k of which used the 1h TTL.
    usage = Usage(cache_write=100_000, cache_write_1h=40_000)
    calculate_cost(_cost_model(), usage)
    # short (5m) slice: 3.75 * 60_000 / 1e6 = 0.225
    # long  (1h) slice: 3.0 * 2 * 40_000 / 1e6 = 0.240  (2x base input)
    assert usage.cost.cache_write == pytest.approx(0.225 + 0.240)


def test_one_hour_cache_write_equals_2x_base_input_rate() -> None:
    # Pure-1h write: the whole slice bills at exactly 2x the input rate.
    usage = Usage(cache_write=50_000, cache_write_1h=50_000)
    calculate_cost(_cost_model(), usage)
    assert usage.cost.cache_write == pytest.approx(3.0 * 2 * 50_000 / 1_000_000)


def test_cache_write_without_1h_keeps_short_rate() -> None:
    # Backward compatibility: cache_write_1h defaults to 0 → original formula.
    usage = Usage(cache_write=100_000)
    calculate_cost(_cost_model(), usage)
    assert usage.cost.cache_write == pytest.approx(3.75 * 100_000 / 1_000_000)


# === (c-e2e) pi #5738 — adapter wires SDK usage onto AssistantMessage.usage ===
#
# pi anthropic-messages.ts:549-559 reads input/output/cacheRead/cacheWrite +
# cacheWrite1h off the SDK usage; the Anthropic Python SDK accumulates them into
# ``stream.get_final_message().usage``. The adapter snapshots that onto
# ``AssistantMessage.usage`` so ``cache_write_1h`` is a real produced value (not
# only consumed by :func:`calculate_cost`).


@dataclass
class _CacheCreation:
    ephemeral_1h_input_tokens: int = 0
    ephemeral_5m_input_tokens: int = 0


@dataclass
class _SdkUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_creation: Any = None


@dataclass
class _UsageFinalMessage:
    stop_reason: str = "end_turn"
    stop_details: Any = None
    usage: Any = None


async def _run_adapter_with_usage(usage: Any) -> AssistantDoneEvent:
    stream = _MockStream(
        _UsageFinalMessage(stop_reason="end_turn", usage=usage)
    )
    opts = SimpleStreamOptions(api_key="sk-test", client=_MockClient(stream))
    model = Model(api="anthropic-messages", id="claude-test", provider="anthropic")
    out = await _collect(stream_anthropic(model, Context(), opts))
    return next(ev for ev in out if isinstance(ev, AssistantDoneEvent))


async def test_adapter_populates_cache_write_1h_from_sdk_usage() -> None:
    usage = _SdkUsage(
        input_tokens=1_200,
        output_tokens=350,
        cache_read_input_tokens=800,
        cache_creation_input_tokens=100_000,
        cache_creation=_CacheCreation(ephemeral_1h_input_tokens=40_000),
    )
    done = await _run_adapter_with_usage(usage)
    assert done.message.usage is not None
    u = done.message.usage
    # The 1h-TTL slice is surfaced (this is the value calculate_cost prices 2×).
    assert u["cache_write_1h"] == 40_000
    # The other Anthropic usage buckets come through with pi's field mapping.
    assert u["cache_write"] == 100_000
    assert u["input"] == 1_200
    assert u["output"] == 350
    assert u["cache_read"] == 800
    # total = input + output + cacheRead + cacheWrite (pi:557-558).
    assert u["total_tokens"] == 1_200 + 350 + 800 + 100_000


async def test_adapter_usage_defaults_cache_write_1h_to_zero() -> None:
    # 5m-only cache write (no ``cache_creation`` block) → cache_write_1h == 0.
    usage = _SdkUsage(
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=2_000,
        cache_creation=None,
    )
    done = await _run_adapter_with_usage(usage)
    assert done.message.usage["cache_write_1h"] == 0
    assert done.message.usage["cache_write"] == 2_000


async def test_adapter_leaves_usage_none_when_sdk_omits_it() -> None:
    # Existing dataclass mocks expose no ``usage`` attr → usage stays None
    # (no regression for callers/tests that never script usage).
    done = await _run_adapter_with_usage(None)
    assert done.message.usage is None
