"""Seed catalog validation — Sprint 6f W2 (ADR-0065 §D).

Verifies the Sprint 6f₁ seed :data:`aelix_ai.models_generated.MODELS`
satisfies the binding-spec invariants:

- ≥10 models across ≥3 providers.
- Every entry validates against the extended :class:`Model` dataclass.
- Per-provider sub-dict shape preserves insertion order
  (cycle_model rotation relies on this).
"""

from __future__ import annotations

import json

import pytest
from aelix_ai.models_generated import MODELS, _load_catalog
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
    """Sprint 6g₁ (P-197): every entry has a :class:`ModelCost` instance.

    Pi parity: Pi ``models.generated.ts`` uses ``-1000000.0`` as a
    sentinel for "no fixed cost" (e.g. ``openrouter/auto``). The
    sentinel is preserved verbatim by the JSON loader. The original
    Sprint 6f₁ ``>= 0.0`` invariant was a seed-only assumption that
    does not hold against the full Pi catalog.
    """

    for per_provider in MODELS.values():
        for model in per_provider.values():
            assert isinstance(model.cost, ModelCost)


def test_catalog_provider_order_matches_pi_models_generated() -> None:
    """Pi parity: provider insertion order matches Pi ``models.generated.ts``.

    Sprint 6g₁ (P-203) ships the full 32-provider catalog. Provider
    insertion order follows Pi ``models.generated.ts`` key order (sorted
    alphabetically in the Pi source), preserved by the JSON loader.
    """

    providers = list(MODELS.keys())
    # Pi catalog starts with ``amazon-bedrock`` (alphabetical first).
    assert providers[0] == "amazon-bedrock"
    # Anthropic / OpenAI / OpenRouter all present (Sprint 6f₁ invariant).
    assert {"anthropic", "openai", "openrouter"} <= set(providers)
    # 32-provider catalog (Pi parity, ADR-0067 P-197).
    assert len(providers) == 32


def test_load_catalog_raises_keyerror_on_missing_required_field(
    tmp_path, monkeypatch
) -> None:
    """Sprint 6g₂ W6 P-209 regression — :func:`_load_catalog` fail-fast.

    Pi-required fields (``id``, ``name``, ``api``, ``provider``,
    ``baseUrl``, ``reasoning``, ``input``, ``contextWindow``,
    ``maxTokens``) MUST raise :exc:`KeyError` when missing. The
    earlier Sprint 6g₁ loader silently substituted ``entry["id"]`` for
    a missing ``name``, ``""`` for a missing ``baseUrl``, etc., which
    hid catalog corruption at import time.
    """

    bad_catalog = {
        "anthropic": {
            "claude-opus-4-7": {
                # Required `name` deliberately missing — Pi-required.
                "id": "claude-opus-4-7",
                "api": "anthropic-messages",
                "provider": "anthropic",
                "baseUrl": "https://api.anthropic.com",
                "reasoning": True,
                "input": ["text"],
                "cost": {
                    "input": 15.0,
                    "output": 75.0,
                    "cacheRead": 1.5,
                    "cacheWrite": 18.75,
                },
                "contextWindow": 200000,
                "maxTokens": 8192,
            }
        }
    }
    bad_json = tmp_path / "models_generated.json"
    bad_json.write_text(json.dumps(bad_catalog), encoding="utf-8")
    monkeypatch.setattr(
        "aelix_ai.models_generated._CATALOG_JSON_PATH", bad_json
    )
    with pytest.raises(KeyError, match="name"):
        _load_catalog()


def test_flagship_models_resolve() -> None:
    """Since-pin refresh: latest flagship ids resolve from pi HEAD's catalog.

    Adds ``claude-opus-4-8`` / ``claude-fable-5`` under ``anthropic`` and
    ``MiniMax-M3`` under ``minimax``, matching pi HEAD
    ``packages/ai/src/models.generated.ts``. Each must deserialize to a
    :class:`Model` with reasoning enabled and a sane context window.
    """

    flagships = {
        ("anthropic", "claude-opus-4-8"): 1_000_000,
        ("anthropic", "claude-fable-5"): 1_000_000,
        ("anthropic", "claude-sonnet-4-6"): 1_000_000,
        ("minimax", "MiniMax-M3"): 512_000,
    }
    for (provider, model_id), context_window in flagships.items():
        assert provider in MODELS, provider
        assert model_id in MODELS[provider], f"{provider}/{model_id}"
        model = MODELS[provider][model_id]
        assert isinstance(model, Model), f"{provider}/{model_id}"
        assert model.provider == provider
        assert model.id == model_id
        assert model.reasoning is True, f"{provider}/{model_id}"
        assert model.context_window == context_window, f"{provider}/{model_id}"
