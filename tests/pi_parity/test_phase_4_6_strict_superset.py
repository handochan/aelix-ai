"""Sprint 6f · Phase 4.6 closure pin (ADR-0066).

Pi parity invariant: every Pi-verified surface in the Phase 4.6 scope
(ModelRegistry runtime, ``aelix_ai.models`` 7 helpers, seed catalog,
3 RPC commands, harness ``current_model``) has a corresponding binding
in Aelix.

Closure date: **2026-05-19**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

Roster: P-163 ~ P-169 (see ADR-0066 §A).
"""

from __future__ import annotations

import json
from pathlib import Path

from aelix_ai import models as aelix_models
from aelix_ai.models import EXTENDED_THINKING_LEVELS
from aelix_ai.models_generated import MODELS as SEED_CATALOG
from aelix_ai.oauth._registry import _OAUTH_DEFERRED_PROVIDERS
from aelix_ai.streaming import Model, ModelCost, Usage, UsageCost
from aelix_coding_agent.model_registry import (
    ModelRegistry,
    ProviderConfigInput,
    ResolvedRequestAuth,
)
from aelix_coding_agent.rpc.rpc_mode import (
    DEFERRED_COMMANDS,
    SUPPORTED_COMMANDS,
    _model_to_dict,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> dict:
    return json.loads(
        (_FIXTURES / "pi_model_registry_734e08e.json").read_text()
    )


# === §A — EXTENDED_THINKING_LEVELS exact 6 values =============================


def test_extended_thinking_levels_exact_pi_six() -> None:
    """Pi parity: ``models.ts:48`` 6 ordered values."""

    assert EXTENDED_THINKING_LEVELS == [
        "off",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    ]
    fixture = _load_fixture()
    assert fixture["extended_thinking_levels"] == EXTENDED_THINKING_LEVELS


# === §B — 7 helpers from models.ts exposed in aelix_ai.models =================


def test_seven_pi_helpers_exposed() -> None:
    """Pi parity (P-164): :mod:`aelix_ai.models` exposes 7 helpers."""

    required = {
        "get_model",
        "get_providers",
        "get_models",
        "calculate_cost",
        "get_supported_thinking_levels",
        "clamp_thinking_level",
        "models_are_equal",
    }
    for name in required:
        assert hasattr(aelix_models, name), f"Missing helper: {name}"


# === §C — ModelRegistry has 14 Pi public methods ==============================


def test_model_registry_14_methods() -> None:
    """Pi parity (P-165): 14 public methods on :class:`ModelRegistry`.

    Roster: 2 factories + 3 access + 5 auth + 2 lifecycle + 2 dynamic +
    1 display = 15 (the spec text "14" excludes the constructor; the
    fixture authoritatively enumerates the methods).
    """

    fixture = _load_fixture()
    pi_methods = (
        [f.split("(")[0].strip() for f in fixture["model_registry_class"]["factory_methods"]]
        + [f.split("(")[0].strip() for f in fixture["model_registry_class"]["public_methods_model_access"]]
        + [f.split("(")[0].strip() for f in fixture["model_registry_class"]["public_methods_auth"]]
        + [f.split("(")[0].strip() for f in fixture["model_registry_class"]["public_methods_lifecycle"]]
        + [f.split("(")[0].strip() for f in fixture["model_registry_class"]["public_methods_dynamic"]]
        + [f.split("(")[0].strip() for f in fixture["model_registry_class"]["public_methods_display"]]
    )
    # Strip "static " prefix from factory entries.
    pi_methods = [m.replace("static ", "") for m in pi_methods]
    # Pi → Aelix name mapping (camelCase → snake_case).
    pi_to_aelix = {
        "create": "create",
        "inMemory": "in_memory",
        "getAll": "get_all",
        "getAvailable": "get_available",
        "find": "find",
        "hasConfiguredAuth": "has_configured_auth",
        "getApiKeyAndHeaders": "get_api_key_and_headers",
        "getApiKeyForProvider": "get_api_key_for_provider",
        "getProviderAuthStatus": "get_provider_auth_status",
        "isUsingOAuth": "is_using_oauth",
        "refresh": "refresh",
        "getError": "get_error",
        "registerProvider": "register_provider",
        "unregisterProvider": "unregister_provider",
        "getProviderDisplayName": "get_provider_display_name",
    }
    for pi_name in pi_methods:
        aelix_name = pi_to_aelix.get(pi_name)
        assert aelix_name is not None, f"Pi method {pi_name!r} has no Aelix mapping"
        assert hasattr(ModelRegistry, aelix_name), (
            f"Pi method {pi_name!r} (aelix: {aelix_name!r}) missing from ModelRegistry"
        )


# === §D — DEFERRED_COMMANDS dropped 3 model commands ==========================


def test_set_model_dropped_from_deferred() -> None:
    assert "set_model" not in DEFERRED_COMMANDS


def test_cycle_model_dropped_from_deferred() -> None:
    assert "cycle_model" not in DEFERRED_COMMANDS


def test_get_available_models_dropped_from_deferred() -> None:
    assert "get_available_models" not in DEFERRED_COMMANDS


def test_three_model_commands_now_supported() -> None:
    assert "set_model" in SUPPORTED_COMMANDS
    assert "cycle_model" in SUPPORTED_COMMANDS
    assert "get_available_models" in SUPPORTED_COMMANDS


def test_deferred_count_dropped_to_16_by_sprint_6h() -> None:
    """Sprint 6f W2 (ADR-0065): 20 → 17 deferred.

    Sprint 6h₁ (ADR-0069 / P-219) drops ``get_commands`` → 17 → 16.
    """

    assert len(DEFERRED_COMMANDS) == 16


def test_supported_count_grew_to_13_by_sprint_6h() -> None:
    """Sprint 6f W2 (ADR-0065): 9 → 12 supported.

    Sprint 6h₁ (ADR-0069 / P-219) adds ``get_commands`` → 12 → 13.
    """

    assert len(SUPPORTED_COMMANDS) == 13
    assert "get_commands" in SUPPORTED_COMMANDS


# === §E — calculate_cost mutates in-place + per-million divisor ===============


def test_calculate_cost_mutates_in_place() -> None:
    """Pi parity (P-164): calculateCost mutates ``usage.cost`` in-place."""

    m = Model(
        id="m", provider="p",
        cost=ModelCost(input=10.0, output=20.0, cache_read=1.0, cache_write=2.0),
    )
    u = Usage(input=2_000_000, output=1_000_000, cache_read=500_000, cache_write=250_000)
    cost_before_id = id(u.cost)
    result = aelix_models.calculate_cost(m, u)
    assert id(u.cost) == cost_before_id, "u.cost identity preserved (in-place mutation)"
    assert result is u.cost, "Pi: calculateCost returns the same UsageCost instance"
    # Per-million divisor: 10.0 * 2M / 1M = 20.0
    assert u.cost.input == 20.0
    assert u.cost.output == 20.0  # 20.0 * 1M / 1M
    assert u.cost.cache_read == 0.5  # 1.0 * 500k / 1M
    assert u.cost.cache_write == 0.5  # 2.0 * 250k / 1M
    assert u.cost.total == 41.0


# === §F — clamp_thinking_level Pi-strict scan order ===========================


def test_clamp_thinking_level_forward_then_backward_then_default() -> None:
    """Pi parity (P-164): forward scan, backward scan, default fallback."""

    # Reasoning model, supports off + low + high only.
    m = Model(
        id="m", provider="p", reasoning=True,
        thinking_level_map={
            "off": "off",
            "minimal": None,  # NOT supported
            "low": "low",
            "medium": None,  # NOT supported
            "high": "high",
        },
    )
    # Forward scan: minimal (null) → low (supported).
    assert aelix_models.clamp_thinking_level(m, "minimal") == "low"
    # Forward scan: medium (null) → high (supported).
    assert aelix_models.clamp_thinking_level(m, "medium") == "high"
    # Backward scan: xhigh (absent) → high (supported).
    assert aelix_models.clamp_thinking_level(m, "xhigh") == "high"
    # Direct match.
    assert aelix_models.clamp_thinking_level(m, "low") == "low"
    # Unknown spelling → available[0].
    assert aelix_models.clamp_thinking_level(m, "bogus") == "off"


# === §G — Seed catalog ≥10 models across ≥3 providers =========================


def test_seed_catalog_at_least_10_models() -> None:
    total = sum(len(v) for v in SEED_CATALOG.values())
    assert total >= 10


def test_seed_catalog_at_least_3_providers() -> None:
    assert len(SEED_CATALOG) >= 3


# === §H — Sprint 6e closure preserved =========================================


def test_oauth_deferred_providers_still_drained() -> None:
    """Sprint 6e closure must NOT regress in Sprint 6f."""

    assert _OAUTH_DEFERRED_PROVIDERS == {}


# === §I — ResolvedRequestAuth + ProviderConfigInput shapes ====================


def test_resolved_request_auth_dataclass_shape() -> None:
    ok = ResolvedRequestAuth(ok=True, api_key="sk", headers={"X": "y"})
    assert ok.ok is True
    assert ok.api_key == "sk"
    assert ok.headers == {"X": "y"}
    err = ResolvedRequestAuth(ok=False, error="nope")
    assert err.ok is False
    assert err.error == "nope"


def test_provider_config_input_dataclass_shape() -> None:
    cfg = ProviderConfigInput(
        api_key="k",
        headers={"H": "1"},
        auth_header="Authorization",
    )
    assert cfg.api_key == "k"
    assert cfg.headers == {"H": "1"}
    assert cfg.auth_header == "Authorization"
    # OAuth + models default to None.
    assert cfg.oauth is None
    assert cfg.models is None


# === §J — _model_to_dict camelCase wire shape =================================


def test_model_to_dict_uses_camel_case_wire_keys() -> None:
    """Pi parity: serialize to Pi camelCase keys."""

    m = Model(
        id="x",
        name="X",
        provider="p",
        api="anthropic-messages",
        cost=ModelCost(input=1.0, output=2.0, cache_read=0.1, cache_write=0.2),
        thinking_level_map={"off": "off"},
        max_tokens=8000,
        context_window=100_000,
        reasoning=True,
        base_url="https://example.com",
        input=["text"],
    )
    d = _model_to_dict(m)
    # Required camelCase keys per Pi ``Model<Api>`` wire shape.
    assert d["id"] == "x"
    assert d["name"] == "X"
    assert d["provider"] == "p"
    assert d["api"] == "anthropic-messages"
    assert d["thinkingLevelMap"] == {"off": "off"}
    assert d["maxTokens"] == 8000
    assert d["contextWindow"] == 100_000
    assert d["baseUrl"] == "https://example.com"
    assert d["reasoning"] is True
    assert d["input"] == ["text"]
    # Cost nested camelCase.
    assert d["cost"]["input"] == 1.0
    assert d["cost"]["output"] == 2.0
    assert d["cost"]["cacheRead"] == 0.1
    assert d["cost"]["cacheWrite"] == 0.2


# === §K — Pi fixture immutability ============================================


def test_pi_sha_pinned_to_phase_4_6_baseline() -> None:
    fixture = _load_fixture()
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_fixture_loc_counts_present() -> None:
    """LOC counts are read-only metadata; drift trips an explicit failure."""

    fixture = _load_fixture()
    locs = fixture["pi_file_loc"]
    assert locs["models.ts"] == 92
    assert locs["model-registry.ts"] == 820
    assert locs["models.generated.ts"] == 10500


# === §L — Usage + UsageCost shapes ===========================================


def test_usage_and_usage_cost_shapes() -> None:
    """Sprint 6f W2 ADR-0064: ``Usage`` + ``UsageCost`` added."""

    u = Usage()
    assert u.input == 0
    assert isinstance(u.cost, UsageCost)
    assert u.cost.total == 0.0


# === §M — Model dataclass 4 new fields exist =================================


def test_model_dataclass_has_pi_required_fields() -> None:
    """Pi parity (P-167 / ADR-0064): cost / thinking_level_map / max_tokens
    / context_window all present.
    """

    fields = Model.__dataclass_fields__
    assert "cost" in fields
    assert "thinking_level_map" in fields
    assert "max_tokens" in fields
    assert "context_window" in fields


def test_model_dataclass_default_values_are_safe() -> None:
    """ADR-0064: all 4 new fields default-safe so Sprint 6a/6b/6c/6d/6e
    code does NOT regress.
    """

    m = Model()
    assert m.cost == ModelCost()
    assert m.thinking_level_map is None
    assert m.max_tokens == 0
    assert m.context_window == 0


# === §N — Cost back-compat alias ==============================================


def test_cost_alias_preserved_for_sprint_6a_6b_callers() -> None:
    """ADR-0064: ``Cost`` is preserved as an alias for ``ModelCost`` so
    Sprint 6a/6b callers (``from aelix_ai.streaming import Cost``) keep
    working.
    """

    from aelix_ai.streaming import Cost

    assert Cost is ModelCost


# === §O — Pi catalog ordering invariant =======================================


def test_seed_catalog_anthropic_first() -> None:
    """Pi parity: provider insertion order matches Pi ``models.generated.ts``.

    Sprint 6g₁ (ADR-0067 P-203) replaces the Sprint 6f₁ 13-seed catalog
    with the full 32-provider Pi catalog. Pi ``models.generated.ts`` key
    order is alphabetical (``amazon-bedrock`` first); Anthropic is still
    present so the cycle_model rotation enumerates it eventually. The
    original assertion was a seed-only invariant.
    """

    providers = list(SEED_CATALOG.keys())
    assert providers[0] == "amazon-bedrock"
    assert "anthropic" in providers


def test_seed_catalog_openrouter_uses_openai_completions_api() -> None:
    """OpenRouter models flow through the OpenAI-Completions adapter."""

    for model in SEED_CATALOG["openrouter"].values():
        assert model.api == "openai-completions"
        # Pi parity: OpenRouter base URL must be present.
        assert "openrouter" in model.base_url


# === §P — Model dataclass remains frozen ======================================


def test_model_dataclass_is_frozen() -> None:
    """ADR-0064: Model remains frozen — Sprint 6e Copilot ``modify_models``
    relies on ``dataclasses.replace`` (not in-place mutation).
    """

    import pytest

    m = Model(id="x", provider="p")
    with pytest.raises((AttributeError, Exception)):
        m.id = "y"  # type: ignore[misc]
