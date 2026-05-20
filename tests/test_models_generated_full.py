"""Sprint 6g₁ (ADR-0067 P-197/P-203): full 32-provider catalog integration tests."""

from __future__ import annotations

from pathlib import Path

from aelix_ai.models_generated import MODELS
from aelix_ai.streaming import Model


def test_full_catalog_provider_count_is_32() -> None:
    """Pi parity: 32 providers in ``models.generated.ts``."""

    assert len(MODELS) == 32


def test_full_catalog_total_model_count_at_least_200() -> None:
    """Pi parity: P-204 estimates ~200+ models. Actual is ~942."""

    total = sum(len(per_provider) for per_provider in MODELS.values())
    assert total >= 200


def test_full_catalog_anthropic_has_claude_opus_4_7() -> None:
    """Pi parity: anthropic catalog includes the canonical default."""

    assert "anthropic" in MODELS
    assert "claude-opus-4-7" in MODELS["anthropic"]
    m = MODELS["anthropic"]["claude-opus-4-7"]
    assert isinstance(m, Model)
    assert m.provider == "anthropic"
    assert m.cost.input != 0.0  # non-empty cost


def test_full_catalog_openai_has_gpt_5_4() -> None:
    """Pi parity: openai catalog includes the canonical default ``gpt-5.4``."""

    assert "openai" in MODELS
    assert "gpt-5.4" in MODELS["openai"]


def test_full_catalog_openrouter_has_many_models() -> None:
    """OpenRouter aggregates many provider models — spec expects > 50."""

    assert "openrouter" in MODELS
    assert len(MODELS["openrouter"]) > 50


def test_full_catalog_zai_has_compat_field() -> None:
    """Pi parity: ``zai`` provider includes at least one model with compat.

    P-200 fixture cites ``zai/glm-5v-turbo`` carrying
    ``{"supportsDeveloperRole": false, "thinkingFormat": "zai",
    "zaiToolStream": true}``.
    """

    assert "zai" in MODELS
    has_compat = any(m.compat is not None for m in MODELS["zai"].values())
    assert has_compat, "Expected at least one zai model with compat field"


def test_full_catalog_json_file_exists() -> None:
    """Sanity: catalog JSON file is present alongside the loader."""

    p = Path(
        Path(__file__).parent.parent
        / "packages"
        / "aelix-ai"
        / "src"
        / "aelix_ai"
        / "models_generated.json"
    )
    assert p.exists()
    assert p.stat().st_size > 100_000  # non-trivial size (~496KB)


def test_full_catalog_all_entries_are_model_dataclasses() -> None:
    """Every catalog entry deserializes to a :class:`Model` dataclass."""

    for provider, per_provider in MODELS.items():
        for model_id, model in per_provider.items():
            assert isinstance(model, Model), f"{provider}/{model_id}"
            assert model.provider == provider
            assert model.id == model_id
