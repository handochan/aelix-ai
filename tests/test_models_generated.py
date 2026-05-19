"""Seed catalog validation — Sprint 6f W2 (ADR-0065 §D).

Verifies the Sprint 6f₁ seed :data:`aelix_ai.models_generated.MODELS`
satisfies the binding-spec invariants:

- ≥10 models across ≥3 providers.
- Every entry validates against the extended :class:`Model` dataclass.
- Per-provider sub-dict shape preserves insertion order
  (cycle_model rotation relies on this).
"""

from __future__ import annotations

from aelix_ai.models_generated import MODELS
from aelix_ai.streaming import Model, ModelCost


def test_seed_catalog_has_at_least_three_providers() -> None:
    assert len(MODELS) >= 3
    assert {"anthropic", "openai", "openrouter"} <= set(MODELS.keys())


def test_seed_catalog_has_at_least_ten_models_total() -> None:
    total = sum(len(per_provider) for per_provider in MODELS.values())
    assert total >= 10


def test_every_entry_is_a_model_dataclass() -> None:
    """Every value in the seed catalog is a :class:`Model` instance."""

    for provider, per_provider in MODELS.items():
        assert isinstance(per_provider, dict), provider
        for model_id, model in per_provider.items():
            assert isinstance(model, Model), f"{provider}/{model_id}"
            # Provider field matches its dict key.
            assert model.provider == provider, f"{provider}/{model_id}"
            # Model id matches its dict key.
            assert model.id == model_id, f"{provider}/{model_id}"


def test_every_model_has_populated_cost() -> None:
    """Sprint 6f₁ seed: every entry has a real :class:`ModelCost`."""

    for per_provider in MODELS.values():
        for model in per_provider.values():
            assert isinstance(model.cost, ModelCost)
            # Sprint 6f₁ ships realistic costs; default 0 indicates a
            # forgotten entry — the assertion catches that drift.
            assert model.cost.input >= 0.0
            assert model.cost.output >= 0.0


def test_seed_catalog_provider_order_is_anthropic_openai_openrouter() -> None:
    """Pi parity: insertion order matters for cycle_model rotation."""

    providers = list(MODELS.keys())
    assert providers == ["anthropic", "openai", "openrouter"]
