"""Sprint 6g₁ (ADR-0067/0068) — Phase 4.7 closure pin.

Pi-strict superset assertions for:

- ``DEFAULT_MODEL_PER_PROVIDER`` map (P-198).
- ``KnownProvider`` Literal union (P-199).
- ``Model.compat`` passthrough field (P-200).
- ``_is_alias`` regex (P-198).
- ``parse_model_pattern`` recursive colon-suffix (P-198).
- Catalog 32 providers + canonical models present (P-197/P-203).
- Sprint 6f closure pin invariants still hold (no regressions).
"""

from __future__ import annotations

import dataclasses
import re
import typing

from aelix_ai.models import EXTENDED_THINKING_LEVELS
from aelix_ai.models_generated import MODELS
from aelix_ai.streaming import KnownProvider, Model, ModelCost
from aelix_coding_agent.core.defaults import (
    DEFAULT_THINKING_LEVEL,
    is_valid_thinking_level,
)
from aelix_coding_agent.core.model_resolver import (
    _DATE_SUFFIX_PATTERN,
    DEFAULT_MODEL_PER_PROVIDER,
    RestoreModelResult,
    _glob_match_pi_minimatch,
    _is_alias,
    parse_model_pattern,
)

# ── DEFAULT_MODEL_PER_PROVIDER ─────────────────────────────────────


def test_default_model_per_provider_has_exactly_32_entries() -> None:
    """Pi parity: ``model-resolver.ts:14-47`` — exactly 32 rows."""

    assert len(DEFAULT_MODEL_PER_PROVIDER) == 32


def test_default_model_per_provider_specific_values_match_pi() -> None:
    """Pi parity: spot-check rows against Pi ``model-resolver.ts``."""

    assert DEFAULT_MODEL_PER_PROVIDER["anthropic"] == "claude-opus-4-7"
    assert DEFAULT_MODEL_PER_PROVIDER["openai"] == "gpt-5.4"
    assert DEFAULT_MODEL_PER_PROVIDER["openrouter"] == "moonshotai/kimi-k2.6"
    assert DEFAULT_MODEL_PER_PROVIDER["azure-openai-responses"] == "gpt-5.4"
    assert DEFAULT_MODEL_PER_PROVIDER["openai-codex"] == "gpt-5.5"
    assert (
        DEFAULT_MODEL_PER_PROVIDER["amazon-bedrock"]
        == "us.anthropic.claude-opus-4-6-v1"
    )
    assert DEFAULT_MODEL_PER_PROVIDER["google"] == "gemini-3.1-pro-preview"
    assert DEFAULT_MODEL_PER_PROVIDER["xai"] == "grok-4.20-0309-reasoning"
    assert DEFAULT_MODEL_PER_PROVIDER["kimi-coding"] == "kimi-for-coding"
    assert DEFAULT_MODEL_PER_PROVIDER["zai"] == "glm-5.1"


# ── KnownProvider Literal ───────────────────────────────────────────


def test_known_provider_literal_has_32_distinct_values() -> None:
    """Pi parity: ``types.ts::KnownProvider`` is a 32-string Literal union."""

    args = typing.get_args(KnownProvider)
    assert len(args) == 32
    assert len(set(args)) == 32


def test_known_provider_literal_order_matches_pi_types_ts() -> None:
    """Pi parity (Sprint 6g₂ W6 P-208 MAJOR fix).

    Verbatim from ``packages/ai/src/types.ts:23-55`` at SHA 734e08e —
    Pi groups providers semantically (first-party → OpenAI family →
    community providers → self-hosted → Xiaomi family), NOT
    alphabetically. The Sprint 6g₁ port shipped alphabetical order;
    P-208 reordered to match Pi. This closure pin locks the order
    against future drift.
    """

    pi_order: list[str] = [
        "amazon-bedrock",
        "anthropic",
        "google",
        "google-vertex",
        "openai",
        "azure-openai-responses",
        "openai-codex",
        "deepseek",
        "github-copilot",
        "xai",
        "groq",
        "cerebras",
        "openrouter",
        "vercel-ai-gateway",
        "zai",
        "mistral",
        "minimax",
        "minimax-cn",
        "moonshotai",
        "moonshotai-cn",
        "huggingface",
        "fireworks",
        "together",
        "opencode",
        "opencode-go",
        "kimi-coding",
        "cloudflare-workers-ai",
        "cloudflare-ai-gateway",
        "xiaomi",
        "xiaomi-token-plan-cn",
        "xiaomi-token-plan-ams",
        "xiaomi-token-plan-sgp",
    ]
    assert list(typing.get_args(KnownProvider)) == pi_order


def test_known_provider_literal_matches_default_model_per_provider_keys() -> None:
    """Pi parity: ``KnownProvider`` keys = ``defaultModelPerProvider`` keys."""

    literal_set = set(typing.get_args(KnownProvider))
    map_set = set(DEFAULT_MODEL_PER_PROVIDER.keys())
    assert literal_set == map_set


def test_known_provider_literal_includes_canonical_providers() -> None:
    """Sanity: well-known providers present in the Literal."""

    args = typing.get_args(KnownProvider)
    for required in (
        "anthropic", "openai", "openrouter", "google", "azure-openai-responses",
        "github-copilot", "openai-codex", "amazon-bedrock", "zai", "xai",
    ):
        assert required in args, required


# ── Model.compat field ──────────────────────────────────────────────


def test_model_dataclass_has_compat_field() -> None:
    """Pi parity (P-200): :class:`Model` carries a ``compat`` field."""

    field_names = {f.name for f in dataclasses.fields(Model)}
    assert "compat" in field_names


def test_model_compat_default_is_none() -> None:
    """ADDITIVE default :data:`None` preserves Sprint 6a–6f behavior."""

    m = Model()
    assert m.compat is None


def test_model_compat_accepts_dict_passthrough() -> None:
    """Sprint 6g₁ stores compat as ``dict[str, Any] | None`` passthrough."""

    compat_dict = {
        "supportsDeveloperRole": False,
        "thinkingFormat": "zai",
        "zaiToolStream": True,
    }
    m = Model(compat=compat_dict)
    assert m.compat == compat_dict


# ── _is_alias regex ────────────────────────────────────────────────


def test_is_alias_regex_matches_pi_pattern() -> None:
    """Pi parity: ``/-\\d{8}$/`` literal — verify regex pattern."""

    assert _DATE_SUFFIX_PATTERN.pattern == r"-\d{8}$"


def test_is_alias_pi_test_vectors() -> None:
    """Pi parity behavior on the canonical alias/dated id shapes."""

    # Latest suffix → alias.
    assert _is_alias("claude-3-5-sonnet-latest") is True
    # Dated YYYYMMDD → NOT alias.
    assert _is_alias("claude-3-5-sonnet-20241022") is False
    assert _is_alias("claude-3-5-sonnet-20250929") is False
    # Undated → alias.
    assert _is_alias("claude-opus-4-7") is True
    # Too few digits → still alias.
    assert _is_alias("model-2024") is True


# ── parse_model_pattern recursive colon-suffix ─────────────────────


def _stub_models() -> list[Model]:
    return [
        Model(
            id="claude-opus-4-7",
            name="Claude Opus 4.7",
            api="anthropic-messages",
            provider="anthropic",
            cost=ModelCost(input=15.0, output=75.0),
        ),
    ]


def test_parse_model_pattern_recursive_colon_suffix_high_medium() -> None:
    """Pi parity: ``model:high:medium`` → outer suffix wins (medium).

    Inner recursion resolves ``model:high`` to (model, thinking=high) with
    no warning, so the outer call returns (model, thinking=medium) because
    the outer suffix wins when inner has no warning.
    """

    models = _stub_models()
    r = parse_model_pattern("claude-opus-4-7:high:medium", models)
    assert r.model is not None
    assert r.thinking_level == "medium"
    assert r.warning is None


def test_parse_model_pattern_colon_suffix_valid_thinking_level() -> None:
    """Pi parity: ``model:high`` → (model, thinking=high)."""

    r = parse_model_pattern("claude-opus-4-7:high", _stub_models())
    assert r.model is not None
    assert r.thinking_level == "high"


def test_parse_model_pattern_invalid_strict_returns_none() -> None:
    """Pi parity (strict): ``model:invalid`` → None when fallback disabled."""

    r = parse_model_pattern(
        "claude-opus-4-7:invalid",
        _stub_models(),
        allow_invalid_thinking_level_fallback=False,
    )
    assert r.model is None


def test_parse_model_pattern_invalid_scope_warns() -> None:
    """Pi parity (scope mode): ``model:invalid`` → (model, warn) by default."""

    r = parse_model_pattern("claude-opus-4-7:invalid", _stub_models())
    assert r.model is not None
    assert r.warning is not None
    assert "invalid" in r.warning


# ── Catalog provider count + canonical models present ──────────────


def test_catalog_has_32_providers() -> None:
    """Pi parity (P-197): 32-provider catalog."""

    assert len(MODELS) == 32


def test_catalog_has_canonical_providers() -> None:
    """Anthropic / OpenAI / OpenRouter all present (P-203)."""

    assert "anthropic" in MODELS
    assert "openai" in MODELS
    assert "openrouter" in MODELS


def test_catalog_anthropic_claude_opus_4_7_exists() -> None:
    """Pi parity (P-203): canonical anthropic default exists."""

    assert "claude-opus-4-7" in MODELS["anthropic"]
    m = MODELS["anthropic"]["claude-opus-4-7"]
    assert m.api == "anthropic-messages"
    assert m.cost.input > 0.0


# ── Defaults ───────────────────────────────────────────────────────


def test_default_thinking_level_constant_is_medium() -> None:
    """Pi parity (Sprint 6g₂ W6 P-205 BLOCKING fix).

    Pi ``packages/coding-agent/src/core/defaults.ts:3`` at SHA 734e08e
    exports ``DEFAULT_THINKING_LEVEL: ThinkingLevel = "medium"``. The
    earlier Sprint 6g₁ pin asserted ``"off"`` per the W1 draft spec —
    P-205 corrected to byte-equivalent ``"medium"``.
    """

    assert DEFAULT_THINKING_LEVEL == "medium"


def test_is_valid_thinking_level_matches_extended_thinking_levels() -> None:
    """``is_valid_thinking_level`` membership = EXTENDED_THINKING_LEVELS."""

    for level in EXTENDED_THINKING_LEVELS:
        assert is_valid_thinking_level(level)
    for bogus in ("nope", "extreme"):
        assert not is_valid_thinking_level(bogus)


# ── No regressions on Sprint 6f closure pin ───────────────────────


def test_sprint_6f_extended_thinking_levels_unchanged() -> None:
    """Sprint 6f invariant: 6 levels in canonical order."""

    assert EXTENDED_THINKING_LEVELS == [
        "off", "minimal", "low", "medium", "high", "xhigh",
    ]


def test_sprint_6f_model_dataclass_is_frozen() -> None:
    """Sprint 6f invariant: :class:`Model` is frozen (immutable)."""

    assert Model.__dataclass_params__.frozen is True


def test_sprint_6f_model_has_thinking_level_map_field() -> None:
    """Sprint 6f₁ invariant: ``thinking_level_map`` field still present."""

    field_names = {f.name for f in dataclasses.fields(Model)}
    assert "thinking_level_map" in field_names


def test_sprint_6f_model_has_headers_field() -> None:
    """Sprint 6f W6 invariant: ``headers`` field still present."""

    field_names = {f.name for f in dataclasses.fields(Model)}
    assert "headers" in field_names


# ── Regex sanity ───────────────────────────────────────────────────


def test_date_suffix_pattern_compiles_to_pi_regex() -> None:
    """Defensive: regex compiles and matches Pi semantics."""

    assert isinstance(_DATE_SUFFIX_PATTERN, re.Pattern)
    assert _DATE_SUFFIX_PATTERN.search("foo-20241022") is not None
    assert _DATE_SUFFIX_PATTERN.search("foo-202410") is None
    assert _DATE_SUFFIX_PATTERN.search("foo-latest") is None


# ── Sprint 6g₂ W6 P-207 glob `/`-boundary regressions ────────────


def test_glob_match_pi_minimatch_bare_pattern_matches_bare_id() -> None:
    """``*sonnet*`` against bare ``claude-sonnet-4-5`` — 1 vs 1 segment."""

    assert _glob_match_pi_minimatch("claude-sonnet-4-5", "*sonnet*") is True


def test_glob_match_pi_minimatch_bare_pattern_rejects_qualified_id() -> None:
    """``*sonnet*`` against ``anthropic/claude-sonnet-4-5`` — 2 vs 1 segments.

    Pi ``minimatch`` rejects: ``*`` does not cross ``/``. The earlier
    Sprint 6g₁ port (using :func:`fnmatch.fnmatchcase` directly) would
    have matched here. Pi parity preserved via the bare-id probe in
    :func:`resolve_model_scope`.
    """

    assert _glob_match_pi_minimatch(
        "anthropic/claude-sonnet-4-5", "*sonnet*"
    ) is False


def test_glob_match_pi_minimatch_provider_pattern_matches_qualified_id() -> None:
    """``anthropic/*sonnet*`` against ``anthropic/claude-sonnet-4-5`` — 2 vs 2."""

    assert _glob_match_pi_minimatch(
        "anthropic/claude-sonnet-4-5", "anthropic/*sonnet*"
    ) is True


def test_glob_match_pi_minimatch_rejects_segment_count_mismatch() -> None:
    """``openai/*`` against ``openrouter/openai/gpt-5`` — 3 vs 2 segments.

    Pi parity: minimatch refuses to cross the segment boundary, so a
    2-segment pattern can NEVER match a 3-segment haystack. The
    earlier Sprint 6g₁ port (``fnmatch.fnmatchcase``) would have
    matched here too — P-207 fix.
    """

    assert _glob_match_pi_minimatch(
        "openrouter/openai/gpt-5", "openai/*"
    ) is False


def test_glob_match_pi_minimatch_case_insensitive() -> None:
    """Pi ``minimatch(..., {nocase: true})`` parity via ``.casefold()``."""

    assert _glob_match_pi_minimatch("CLAUDE-OPUS-4-7", "claude-*") is True
    assert _glob_match_pi_minimatch("Anthropic/Claude", "anthropic/*") is True


# ── Sprint 6g₂ W6 P-206 RestoreModelResult typed dataclass ──────


def test_restore_model_result_is_frozen_dataclass() -> None:
    """Pi parity (P-206): :class:`RestoreModelResult` mirrors the other
    four resolver return shapes — frozen, 2-field, optional defaults.
    """

    import dataclasses

    assert dataclasses.is_dataclass(RestoreModelResult)
    assert RestoreModelResult.__dataclass_params__.frozen is True
    fields = {f.name for f in dataclasses.fields(RestoreModelResult)}
    assert fields == {"model", "fallback_message"}


def test_restore_model_result_defaults() -> None:
    """Pi parity: both fields default to :data:`None`."""

    r = RestoreModelResult()
    assert r.model is None
    assert r.fallback_message is None
