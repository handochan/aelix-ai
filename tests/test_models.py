"""Pi parity: ``packages/ai/src/models.ts`` 7 helpers + EXTENDED_THINKING_LEVELS.

Sprint 6f W2 (ADR-0065) verifies the verbatim port of:

- :func:`aelix_ai.models.get_model`
- :func:`aelix_ai.models.get_providers`
- :func:`aelix_ai.models.get_models`
- :func:`aelix_ai.models.calculate_cost` (mutates ``usage.cost`` in place)
- :func:`aelix_ai.models.get_supported_thinking_levels`
- :func:`aelix_ai.models.clamp_thinking_level` (forward then backward scan)
- :func:`aelix_ai.models.models_are_equal`
"""

from __future__ import annotations

import pytest
from aelix_ai.models import (
    EXTENDED_THINKING_LEVELS,
    calculate_cost,
    clamp_thinking_level,
    get_model,
    get_models,
    get_providers,
    get_supported_thinking_levels,
    models_are_equal,
)
from aelix_ai.streaming import Model, ModelCost, Usage

# === get_model =================================================================


def test_get_model_returns_model_for_known_provider_id() -> None:
    m = get_model("anthropic", "claude-sonnet-4-5")
    assert m is not None
    assert m.id == "claude-sonnet-4-5"
    assert m.provider == "anthropic"


def test_get_model_returns_none_for_unknown_provider() -> None:
    assert get_model("does-not-exist", "anything") is None


def test_get_model_returns_none_for_unknown_model_id() -> None:
    assert get_model("anthropic", "claude-non-existent") is None


# === get_providers / get_models ===============================================


def test_get_providers_returns_seed_provider_set() -> None:
    providers = get_providers()
    # Seed catalog (Sprint 6f₁) ships 3 providers.
    assert set(providers) >= {"anthropic", "openai", "openrouter"}


def test_get_providers_preserves_insertion_order() -> None:
    """Pi parity: Map iteration order = insertion order (cycle_model relies on this).

    Sprint 6g₁ (ADR-0067 P-203) ships the full 32-provider Pi catalog;
    providers are inserted in Pi ``models.generated.ts`` key order
    (alphabetical), so the first key is ``amazon-bedrock``. Anthropic
    remains present, but order is now Pi-driven.
    """

    providers = get_providers()
    # Pi parity: ``models.generated.ts`` starts with ``amazon-bedrock``.
    assert providers[0] == "amazon-bedrock"
    # Anthropic still present (Sprint 6f₁ invariant).
    assert "anthropic" in providers


def test_get_models_returns_list_for_known_provider() -> None:
    anthropic_models = get_models("anthropic")
    assert len(anthropic_models) >= 1
    assert all(m.provider == "anthropic" for m in anthropic_models)


def test_get_models_returns_empty_for_unknown_provider() -> None:
    assert get_models("nonexistent-provider") == []


# === calculate_cost ============================================================


def test_calculate_cost_mutates_usage_cost_in_place() -> None:
    """Pi parity: ``models.ts:39-46`` — calculateCost mutates usage.cost."""

    model = Model(
        id="m",
        provider="p",
        cost=ModelCost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75),
    )
    usage = Usage(input=1_000_000, output=2_000_000, cache_read=500_000, cache_write=100_000)
    result = calculate_cost(model, usage)
    # Pi formula: (model.cost.X / 1_000_000) * usage.X for each layer.
    assert usage.cost.input == pytest.approx(3.0)  # 3.0 * 1M / 1M
    assert usage.cost.output == pytest.approx(30.0)  # 15.0 * 2M / 1M
    assert usage.cost.cache_read == pytest.approx(0.15)  # 0.3 * 500k / 1M
    assert usage.cost.cache_write == pytest.approx(0.375)  # 3.75 * 100k / 1M
    assert usage.cost.total == pytest.approx(33.525)  # sum
    # Return value is the same in-place ``UsageCost``.
    assert result is usage.cost


def test_calculate_cost_zero_usage_yields_zero_cost() -> None:
    model = Model(id="m", provider="p", cost=ModelCost(input=10.0, output=20.0))
    usage = Usage()  # all zeros
    calculate_cost(model, usage)
    assert usage.cost.input == 0.0
    assert usage.cost.output == 0.0
    assert usage.cost.total == 0.0


# === EXTENDED_THINKING_LEVELS =================================================


def test_extended_thinking_levels_is_exact_six_pi_values() -> None:
    """Pi parity: ``models.ts:48`` — 6 values, ordered."""

    assert EXTENDED_THINKING_LEVELS == [
        "off",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    ]


# === get_supported_thinking_levels ============================================


def test_get_supported_thinking_levels_non_reasoning_only_off() -> None:
    """Pi parity: non-reasoning models support only ``"off"``."""

    m = Model(id="m", provider="p", reasoning=False)
    assert get_supported_thinking_levels(m) == ["off"]


def test_get_supported_thinking_levels_reasoning_with_full_map() -> None:
    """Pi parity: every non-null mapped level supported; xhigh requires key."""

    thinking_map = {
        "off": "off",
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
    }
    m = Model(
        id="m", provider="p", reasoning=True, thinking_level_map=thinking_map
    )
    levels = get_supported_thinking_levels(m)
    # xhigh absent from map → not supported.
    assert "xhigh" not in levels
    assert "off" in levels
    assert "minimal" in levels
    assert "low" in levels
    assert "medium" in levels
    assert "high" in levels


def test_get_supported_thinking_levels_null_means_not_supported() -> None:
    """Pi parity: ``thinkingLevelMap[level] === null`` excludes the level."""

    thinking_map = {
        "off": "off",
        "minimal": None,  # NOT supported
        "low": "low",
        "medium": None,  # NOT supported
        "high": "high",
    }
    m = Model(
        id="m", provider="p", reasoning=True, thinking_level_map=thinking_map
    )
    levels = get_supported_thinking_levels(m)
    assert "minimal" not in levels
    assert "medium" not in levels
    assert "low" in levels
    assert "high" in levels


# === clamp_thinking_level ======================================================


def test_clamp_thinking_level_returns_level_when_supported() -> None:
    m = Model(
        id="m",
        provider="p",
        reasoning=True,
        thinking_level_map={"off": "off", "low": "low", "high": "high"},
    )
    assert clamp_thinking_level(m, "low") == "low"
    assert clamp_thinking_level(m, "high") == "high"


def test_clamp_thinking_level_forward_scan_to_next_supported() -> None:
    """Pi parity: ``minimal`` mapped to null → forward to ``low``."""

    m = Model(
        id="m",
        provider="p",
        reasoning=True,
        thinking_level_map={
            "off": "off",
            "minimal": None,  # NOT supported
            "low": "low",
            "high": "high",
        },
    )
    # minimal -> null (unsupported) → forward scan finds low.
    assert clamp_thinking_level(m, "minimal") == "low"


def test_clamp_thinking_level_backward_scan_when_no_forward_match() -> None:
    """Pi parity: ``xhigh`` unsupported (absent) → backward to ``high``."""

    m = Model(
        id="m",
        provider="p",
        reasoning=True,
        thinking_level_map={"off": "off", "low": "low", "high": "high"},
    )
    # xhigh absent → forward scan exhausts → backward: xhigh-1 == high.
    assert clamp_thinking_level(m, "xhigh") == "high"


def test_clamp_thinking_level_unknown_returns_first_supported() -> None:
    """Pi parity: unknown spelling → ``available[0]``."""

    m = Model(
        id="m",
        provider="p",
        reasoning=True,
        thinking_level_map={"off": "off", "low": "low"},
    )
    # Unknown level → returns available[0] which is "off".
    assert clamp_thinking_level(m, "bogus") == "off"


def test_clamp_thinking_level_non_reasoning_clamps_to_off() -> None:
    m = Model(id="m", provider="p", reasoning=False)
    assert clamp_thinking_level(m, "high") == "off"


def test_clamp_thinking_level_none_input_returns_none_back_compat() -> None:
    """Sprint 6b back-compat: ``None`` input → ``None`` output (Aelix-only)."""

    m = Model(id="m", provider="p", reasoning=True)
    assert clamp_thinking_level(m, None) is None


# === models_are_equal ==========================================================


def test_models_are_equal_same_id_and_provider() -> None:
    a = Model(id="x", provider="p", name="A")
    b = Model(id="x", provider="p", name="B")  # different name — Pi ignores it
    assert models_are_equal(a, b)


def test_models_are_equal_different_id() -> None:
    a = Model(id="x", provider="p")
    b = Model(id="y", provider="p")
    assert not models_are_equal(a, b)


def test_models_are_equal_different_provider() -> None:
    a = Model(id="x", provider="p1")
    b = Model(id="x", provider="p2")
    assert not models_are_equal(a, b)


def test_models_are_equal_none_either_side_is_false() -> None:
    m = Model(id="x", provider="p")
    assert not models_are_equal(None, m)
    assert not models_are_equal(m, None)
    assert not models_are_equal(None, None)
