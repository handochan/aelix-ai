"""Sprint 6g₁ (ADR-0067 P-198): model_resolver port tests.

Covers Pi ``model-resolver.ts:1-637`` (7 public + 3 private helpers).
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model, ModelCost
from aelix_coding_agent.core.model_resolver import (
    DEFAULT_MODEL_PER_PROVIDER,
    InitialModelResult,
    ParsedModelResult,
    ResolveCliModelResult,
    RestoreModelResult,
    ScopedModel,
    _build_fallback_model,
    _is_alias,
    _try_match_model,
    find_exact_model_reference_match,
    find_initial_model,
    parse_model_pattern,
    resolve_cli_model,
    resolve_model_scope,
    restore_model_from_session,
)
from aelix_coding_agent.model_registry import ModelRegistry

# ── Helpers ─────────────────────────────────────────────────────────────


def _m(provider: str, model_id: str, *, name: str | None = None) -> Model:
    return Model(
        id=model_id,
        name=name or model_id,
        api="anthropic-messages",
        provider=provider,
        cost=ModelCost(input=1.0, output=1.0),
    )


class _StubRegistry:
    """Lightweight stand-in for ModelRegistry — controls get_all/get_available/find/has_configured_auth."""

    def __init__(
        self,
        *,
        models: list[Model] | None = None,
        available: list[Model] | None = None,
        configured_auth: set[tuple[str, str]] | None = None,
    ) -> None:
        self._models = models or []
        # If no explicit ``available`` list, default to ``models`` so
        # tests that don't care about auth gating still see everything.
        self._available = available if available is not None else self._models
        self._configured_auth = configured_auth or {
            (m.provider, m.id) for m in self._available
        }

    def get_all(self) -> list[Model]:
        return list(self._models)

    def get_available(self) -> list[Model]:
        return list(self._available)

    def find(self, provider: str, model_id: str) -> Model | None:
        for m in self._models:
            if m.provider == provider and m.id == model_id:
                return m
        return None

    def has_configured_auth(self, model: Model) -> bool:
        return (model.provider, model.id) in self._configured_auth


# ── _is_alias ────────────────────────────────────────────────────────────


def test_is_alias_latest_suffix_returns_true() -> None:
    assert _is_alias("claude-3-5-sonnet-latest") is True


def test_is_alias_dated_suffix_returns_false() -> None:
    assert _is_alias("claude-3-5-sonnet-20241022") is False
    assert _is_alias("model-20250929") is False


def test_is_alias_undated_id_returns_true() -> None:
    assert _is_alias("claude-opus-4-7") is True
    assert _is_alias("gpt-5.4") is True


# ── find_exact_model_reference_match ──────────────────────────────────


def test_find_exact_canonical_match() -> None:
    models = [_m("anthropic", "claude-opus-4-7"), _m("openai", "gpt-5.4")]
    res = find_exact_model_reference_match("anthropic/claude-opus-4-7", models)
    assert res is not None and res.id == "claude-opus-4-7"


def test_find_exact_bare_id_match() -> None:
    models = [_m("anthropic", "claude-opus-4-7"), _m("openai", "gpt-5.4")]
    res = find_exact_model_reference_match("claude-opus-4-7", models)
    assert res is not None and res.provider == "anthropic"


def test_find_exact_ambiguity_returns_none() -> None:
    """Same bare id across two providers is ambiguous → None."""

    models = [_m("anthropic", "claude"), _m("openrouter", "claude")]
    assert find_exact_model_reference_match("claude", models) is None


def test_find_exact_case_insensitive() -> None:
    models = [_m("anthropic", "claude-opus-4-7")]
    res = find_exact_model_reference_match(
        "ANTHROPIC/Claude-Opus-4-7", models
    )
    assert res is not None and res.id == "claude-opus-4-7"


def test_find_exact_empty_reference_returns_none() -> None:
    assert find_exact_model_reference_match("   ", [_m("a", "b")]) is None


# ── _try_match_model ────────────────────────────────────────────────


def test_try_match_alias_preferred_over_dated() -> None:
    models = [
        _m("anthropic", "claude-sonnet-4-5"),
        _m("anthropic", "claude-sonnet-4-5-20250929"),
    ]
    res = _try_match_model("sonnet", models)
    assert res is not None and res.id == "claude-sonnet-4-5"


def test_try_match_latest_dated_when_no_alias() -> None:
    models = [
        _m("anthropic", "claude-sonnet-20240620"),
        _m("anthropic", "claude-sonnet-20250929"),
    ]
    res = _try_match_model("sonnet", models)
    assert res is not None and res.id == "claude-sonnet-20250929"


def test_try_match_returns_none_when_no_partial() -> None:
    assert _try_match_model("missing", [_m("a", "b", name="Model B")]) is None


# ── parse_model_pattern ────────────────────────────────────────────


def test_parse_model_pattern_exact_match() -> None:
    models = [_m("anthropic", "claude-opus-4-7")]
    r = parse_model_pattern("claude-opus-4-7", models)
    assert r.model is not None
    assert r.model.id == "claude-opus-4-7"
    assert r.thinking_level is None
    assert r.warning is None


def test_parse_model_pattern_partial_match() -> None:
    models = [_m("anthropic", "claude-opus-4-7")]
    r = parse_model_pattern("opus", models)
    assert r.model is not None and r.model.id == "claude-opus-4-7"


def test_parse_model_pattern_colon_suffix_valid_thinking_level() -> None:
    models = [_m("anthropic", "claude-opus-4-7")]
    r = parse_model_pattern("claude-opus-4-7:high", models)
    assert r.model is not None and r.model.id == "claude-opus-4-7"
    assert r.thinking_level == "high"
    assert r.warning is None


def test_parse_model_pattern_colon_suffix_invalid_scope_mode_warns() -> None:
    models = [_m("anthropic", "claude-opus-4-7")]
    r = parse_model_pattern(
        "claude-opus-4-7:bogus",
        models,
        allow_invalid_thinking_level_fallback=True,
    )
    assert r.model is not None and r.model.id == "claude-opus-4-7"
    assert r.thinking_level is None
    assert r.warning is not None and "bogus" in r.warning


def test_parse_model_pattern_strict_invalid_returns_none() -> None:
    models = [_m("anthropic", "claude-opus-4-7")]
    r = parse_model_pattern(
        "claude-opus-4-7:bogus",
        models,
        allow_invalid_thinking_level_fallback=False,
    )
    assert r.model is None
    assert r.warning is None


def test_parse_model_pattern_recursive_valid_thinking_levels() -> None:
    """Pi parity: ``model:high:medium`` should resolve to model + medium.

    Recursion: outer call sees ``model:high:medium``; splits on the LAST
    ``:`` → prefix=``model:high``, suffix=``medium``. Suffix is valid →
    recurse on ``model:high``. Inner recursion splits to prefix=``model``,
    suffix=``high`` (valid) → recurses on ``model`` (exact match). Returns
    ``model`` + thinking_level=``high``. Outer call sees that result has
    no warning → returns ``model`` + thinking_level=``medium`` (outer
    suffix wins over inner suffix when there is no inner warning).
    """

    models = [_m("anthropic", "claude-opus-4-7")]
    r = parse_model_pattern("claude-opus-4-7:high:medium", models)
    assert r.model is not None
    assert r.thinking_level == "medium"
    assert r.warning is None


def test_parse_model_pattern_no_match_returns_none() -> None:
    r = parse_model_pattern("nonexistent", [_m("a", "b")])
    assert r.model is None and r.warning is None


# ── _build_fallback_model ──────────────────────────────────────────


def test_build_fallback_model_uses_default_id() -> None:
    base = _m("anthropic", "claude-opus-4-7")
    res = _build_fallback_model("anthropic", "claude-custom", [base])
    assert res is not None
    assert res.id == "claude-custom"
    assert res.name == "claude-custom"
    assert res.provider == "anthropic"
    # Cost / api inherited from base.
    assert res.cost == base.cost
    assert res.api == base.api


def test_build_fallback_model_no_provider_models_returns_none() -> None:
    assert _build_fallback_model("missing", "x", [_m("a", "b")]) is None


def test_build_fallback_model_uses_first_when_no_default() -> None:
    base = _m("unknown-provider", "model-a")
    res = _build_fallback_model("unknown-provider", "custom", [base])
    assert res is not None and res.id == "custom"


# ── resolve_model_scope ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_model_scope_single_pattern() -> None:
    reg = cast(
        ModelRegistry,
        _StubRegistry(models=[_m("anthropic", "claude-opus-4-7")]),
    )
    res = await resolve_model_scope(["claude-opus-4-7"], reg)
    assert len(res) == 1
    assert res[0].model.id == "claude-opus-4-7"
    assert res[0].thinking_level is None


@pytest.mark.asyncio
async def test_resolve_model_scope_glob_pattern() -> None:
    reg = cast(
        ModelRegistry,
        _StubRegistry(
            models=[
                _m("anthropic", "claude-sonnet-4-5"),
                _m("anthropic", "claude-opus-4-7"),
            ]
        ),
    )
    res = await resolve_model_scope(["*sonnet*"], reg)
    assert len(res) == 1
    assert res[0].model.id == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_resolve_model_scope_dedups_duplicates() -> None:
    reg = cast(
        ModelRegistry,
        _StubRegistry(models=[_m("anthropic", "claude-opus-4-7")]),
    )
    res = await resolve_model_scope(
        ["claude-opus-4-7", "anthropic/claude-opus-4-7"], reg
    )
    assert len(res) == 1


@pytest.mark.asyncio
async def test_resolve_model_scope_glob_with_thinking_level_suffix() -> None:
    reg = cast(
        ModelRegistry,
        _StubRegistry(
            models=[_m("anthropic", "claude-opus-4-7")],
        ),
    )
    res = await resolve_model_scope(["*opus*:high"], reg)
    assert len(res) == 1
    assert res[0].thinking_level == "high"


# ── resolve_cli_model ──────────────────────────────────────────────


def test_resolve_cli_model_provider_and_model() -> None:
    reg = cast(
        ModelRegistry,
        _StubRegistry(models=[_m("anthropic", "claude-opus-4-7")]),
    )
    res = resolve_cli_model(
        cli_provider="anthropic",
        cli_model="claude-opus-4-7",
        model_registry=reg,
    )
    assert res.model is not None and res.model.id == "claude-opus-4-7"
    assert res.error is None


def test_resolve_cli_model_no_cli_model_returns_empty() -> None:
    reg = cast(ModelRegistry, _StubRegistry(models=[]))
    res = resolve_cli_model(cli_model=None, model_registry=reg)
    assert res.model is None
    assert res.error is None


def test_resolve_cli_model_no_models_returns_error() -> None:
    reg = cast(ModelRegistry, _StubRegistry(models=[]))
    res = resolve_cli_model(cli_model="anything", model_registry=reg)
    assert res.model is None
    assert res.error is not None and "No models available" in res.error


def test_resolve_cli_model_unknown_provider_returns_error() -> None:
    reg = cast(
        ModelRegistry,
        _StubRegistry(models=[_m("anthropic", "claude-opus-4-7")]),
    )
    res = resolve_cli_model(
        cli_provider="bogus", cli_model="x", model_registry=reg
    )
    assert res.error is not None and "Unknown provider" in res.error


def test_resolve_cli_model_slash_inference() -> None:
    reg = cast(
        ModelRegistry,
        _StubRegistry(
            models=[
                _m("zai", "glm-5"),
                _m("anthropic", "claude-opus-4-7"),
            ]
        ),
    )
    res = resolve_cli_model(cli_model="zai/glm-5", model_registry=reg)
    assert res.model is not None
    assert res.model.provider == "zai"
    assert res.model.id == "glm-5"


def test_resolve_cli_model_openrouter_slash_in_id() -> None:
    """Pi parity: OpenRouter ids naturally contain slashes."""

    reg = cast(
        ModelRegistry,
        _StubRegistry(
            models=[_m("openrouter", "moonshotai/kimi-k2.6")],
        ),
    )
    res = resolve_cli_model(
        cli_model="moonshotai/kimi-k2.6", model_registry=reg
    )
    assert res.model is not None
    assert res.model.provider == "openrouter"
    assert res.model.id == "moonshotai/kimi-k2.6"


def test_resolve_cli_model_fallback_model_for_unknown_id() -> None:
    """CLI --provider + unknown model id → synthetic Model construction."""

    reg = cast(
        ModelRegistry,
        _StubRegistry(models=[_m("anthropic", "claude-opus-4-7")]),
    )
    res = resolve_cli_model(
        cli_provider="anthropic",
        cli_model="claude-experimental",
        model_registry=reg,
    )
    assert res.model is not None
    assert res.model.id == "claude-experimental"
    assert res.warning is not None and "not found" in res.warning


def test_resolve_cli_model_no_match_no_provider_returns_error() -> None:
    reg = cast(
        ModelRegistry,
        _StubRegistry(models=[_m("anthropic", "claude-opus-4-7")]),
    )
    res = resolve_cli_model(cli_model="totally-unknown", model_registry=reg)
    assert res.model is None
    assert res.error is not None and "not found" in res.error


# ── find_initial_model ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_initial_model_priority_1_cli_args() -> None:
    reg = cast(
        ModelRegistry,
        _StubRegistry(models=[_m("anthropic", "claude-opus-4-7")]),
    )
    res = await find_initial_model(
        cli_provider="anthropic",
        cli_model="claude-opus-4-7",
        model_registry=reg,
    )
    assert res.model is not None and res.model.id == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_find_initial_model_priority_2_scoped() -> None:
    base = _m("anthropic", "claude-opus-4-7")
    reg = cast(ModelRegistry, _StubRegistry(models=[base]))
    scoped = [ScopedModel(model=base, thinking_level="high")]
    res = await find_initial_model(
        scoped_models=scoped, model_registry=reg
    )
    assert res.model is not None and res.model.id == "claude-opus-4-7"
    assert res.thinking_level == "high"


@pytest.mark.asyncio
async def test_find_initial_model_priority_2_skipped_when_continuing() -> None:
    """isContinuing=True bypasses the scoped priority."""

    base = _m("anthropic", "claude-opus-4-7")
    reg = cast(ModelRegistry, _StubRegistry(models=[base]))
    scoped = [ScopedModel(model=base, thinking_level="high")]
    res = await find_initial_model(
        scoped_models=scoped,
        is_continuing=True,
        default_provider="anthropic",
        default_model_id="claude-opus-4-7",
        model_registry=reg,
    )
    # Priority 3 (saved default) wins.
    assert res.model is not None and res.model.id == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_find_initial_model_priority_3_saved_default() -> None:
    base = _m("anthropic", "claude-opus-4-7")
    reg = cast(ModelRegistry, _StubRegistry(models=[base]))
    res = await find_initial_model(
        default_provider="anthropic",
        default_model_id="claude-opus-4-7",
        default_thinking_level="medium",
        model_registry=reg,
    )
    assert res.model is not None and res.model.id == "claude-opus-4-7"
    assert res.thinking_level == "medium"


@pytest.mark.asyncio
async def test_find_initial_model_priority_4_known_default_per_provider() -> None:
    """The known default for ``anthropic`` is ``claude-opus-4-7`` — picked first."""

    base = _m("anthropic", "claude-opus-4-7")
    other = _m("openrouter", "moonshotai/kimi-k2.6")
    reg = cast(ModelRegistry, _StubRegistry(models=[other, base]))
    res = await find_initial_model(model_registry=reg)
    assert res.model is not None and res.model.id == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_find_initial_model_priority_5_first_available() -> None:
    """No known-default match → first available wins."""

    custom = _m("custom-provider", "custom-model")
    reg = cast(ModelRegistry, _StubRegistry(models=[custom]))
    res = await find_initial_model(model_registry=reg)
    assert res.model is not None and res.model.id == "custom-model"


@pytest.mark.asyncio
async def test_find_initial_model_no_models_returns_none() -> None:
    reg = cast(ModelRegistry, _StubRegistry(models=[]))
    res = await find_initial_model(model_registry=reg)
    assert res.model is None


# ── restore_model_from_session ─────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_model_happy_path() -> None:
    base = _m("anthropic", "claude-opus-4-7")
    reg = cast(ModelRegistry, _StubRegistry(models=[base]))
    res = await restore_model_from_session(
        "anthropic", "claude-opus-4-7", None, False, reg
    )
    assert isinstance(res, RestoreModelResult)
    assert res.model is base
    assert res.fallback_message is None


@pytest.mark.asyncio
async def test_restore_model_missing_falls_back_to_current() -> None:
    base = _m("anthropic", "claude-opus-4-7")
    reg = cast(ModelRegistry, _StubRegistry(models=[base]))
    current = _m("openai", "gpt-5.4")
    res = await restore_model_from_session(
        "anthropic", "claude-removed", current, False, reg
    )
    assert res.model is current
    assert res.fallback_message is not None
    assert "no longer exists" in res.fallback_message


@pytest.mark.asyncio
async def test_restore_model_no_auth_falls_back_to_current() -> None:
    base = _m("anthropic", "claude-opus-4-7")
    reg = cast(
        ModelRegistry,
        _StubRegistry(models=[base], available=[], configured_auth=set()),
    )
    current = _m("openai", "gpt-5.4")
    res = await restore_model_from_session(
        "anthropic", "claude-opus-4-7", current, False, reg
    )
    assert res.model is current
    assert res.fallback_message is not None
    assert "no auth configured" in res.fallback_message


@pytest.mark.asyncio
async def test_restore_model_no_current_falls_back_to_available() -> None:
    base = _m("anthropic", "claude-opus-4-7")
    other = _m("openai", "gpt-5.4")
    reg = cast(ModelRegistry, _StubRegistry(models=[base, other]))
    # Saved model missing, no current — should pick from available (known default).
    res = await restore_model_from_session(
        "anthropic", "claude-removed", None, False, reg
    )
    assert res.model is base  # known default for anthropic
    assert res.fallback_message is not None


@pytest.mark.asyncio
async def test_restore_model_no_models_returns_none() -> None:
    reg = cast(ModelRegistry, _StubRegistry(models=[]))
    res = await restore_model_from_session(
        "anthropic", "claude-removed", None, False, reg
    )
    assert res.model is None
    assert res.fallback_message is None


# ── Integration with real ModelRegistry ─────────────────────────────────


@pytest.mark.asyncio
async def test_find_initial_model_with_real_registry(tmp_path: Path) -> None:
    """Spot check: real ModelRegistry over the full catalog returns SOMETHING."""

    storage = AuthStorage(path=tmp_path / "auth.json")
    await storage.load()
    storage.set_runtime_api_key("anthropic", "test-key")
    reg = ModelRegistry.in_memory(storage)
    res = await find_initial_model(model_registry=reg)
    assert isinstance(res, InitialModelResult)
    assert res.model is not None
    assert res.model.provider == "anthropic"
    assert res.model.id == "claude-opus-4-7"


def test_resolve_cli_model_with_real_registry(tmp_path: Path) -> None:
    """Spot check: resolve_cli_model returns expected wire shape."""

    storage = AuthStorage(path=tmp_path / "auth.json")
    reg = ModelRegistry.in_memory(storage)
    res = resolve_cli_model(
        cli_provider="anthropic",
        cli_model="claude-opus-4-7",
        model_registry=reg,
    )
    assert isinstance(res, ResolveCliModelResult)
    assert res.model is not None
    assert res.model.id == "claude-opus-4-7"


def test_parsed_model_result_shape() -> None:
    r = ParsedModelResult()
    assert r.model is None
    assert r.thinking_level is None
    assert r.warning is None


def test_default_model_per_provider_has_32_entries() -> None:
    assert len(DEFAULT_MODEL_PER_PROVIDER) == 32
    assert DEFAULT_MODEL_PER_PROVIDER["anthropic"] == "claude-opus-4-7"
    assert DEFAULT_MODEL_PER_PROVIDER["openai"] == "gpt-5.4"
    assert DEFAULT_MODEL_PER_PROVIDER["openrouter"] == "moonshotai/kimi-k2.6"
