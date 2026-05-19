"""Seed model catalog — Sprint 6f₁ minimal subset (ADR-0065).

Pi parity: ``packages/ai/src/models.generated.ts`` (SHA 734e08e, 428 KB,
~10,500 LOC). Sprint 6f₁ ships ~12 seed models across 3 providers so the
ModelRegistry runtime + ``set_model`` / ``cycle_model`` /
``get_available_models`` RPC commands have a non-empty catalog to
enumerate.

Sprint 6g replaces this with the full Pi catalog (full data transfer or
JSON-loaded). The :data:`MODELS` dict shape contract (``dict[provider,
dict[model_id, Model]]``) is binding and Sprint 6g+ MUST NOT break it.

Costs are per-million-tokens (matches Pi ``ai/src/types.ts::Model.cost``
+ public Anthropic / OpenAI pricing as of 2026-05). The seed values are
realistic but NOT load-bearing for the runtime — the ModelRegistry treats
:data:`MODELS` as an opaque catalog source.
"""

from __future__ import annotations

from aelix_ai.streaming import Model, ModelCost

# Pi parity: ``models.generated.ts`` exports ``export const MODELS:
# Record<string, Record<string, Model<Api>>>``. Aelix mirrors with a
# plain dict literal; insertion order = canonical enumeration order
# for ``cycle_model`` rotation (matches Pi's Map iteration order).

# Pi thinking-level map convention: a value of ``None`` (Pi ``null``)
# means the level is NOT supported. The presence/absence of a key is
# consulted by :func:`aelix_ai.models.get_supported_thinking_levels`.

# === Anthropic ================================================================

_ANTHROPIC_THINKING_MAP: dict[str, str | int | None] = {
    "off": "off",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
}

_ANTHROPIC_MODELS: dict[str, Model] = {
    "claude-sonnet-4-5": Model(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        provider="anthropic",
        api="anthropic-messages",
        cost=ModelCost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75),
        thinking_level_map=_ANTHROPIC_THINKING_MAP,
        max_tokens=64000,
        context_window=200000,
        reasoning=True,
        input=["text", "image"],
    ),
    "claude-opus-4-7": Model(
        id="claude-opus-4-7",
        name="Claude Opus 4.7",
        provider="anthropic",
        api="anthropic-messages",
        cost=ModelCost(input=15.0, output=75.0, cache_read=1.5, cache_write=18.75),
        thinking_level_map=_ANTHROPIC_THINKING_MAP,
        max_tokens=32000,
        context_window=200000,
        reasoning=True,
        input=["text", "image"],
    ),
    "claude-haiku-4-5": Model(
        id="claude-haiku-4-5",
        name="Claude Haiku 4.5",
        provider="anthropic",
        api="anthropic-messages",
        cost=ModelCost(input=0.8, output=4.0, cache_read=0.08, cache_write=1.0),
        thinking_level_map=_ANTHROPIC_THINKING_MAP,
        max_tokens=8192,
        context_window=200000,
        reasoning=False,
        input=["text", "image"],
    ),
}

# === OpenAI ===================================================================

_OPENAI_REASONING_THINKING_MAP: dict[str, str | int | None] = {
    "off": "off",
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
}

_OPENAI_MODELS: dict[str, Model] = {
    "gpt-4o": Model(
        id="gpt-4o",
        name="GPT-4o",
        provider="openai",
        api="openai-completions",
        cost=ModelCost(input=2.5, output=10.0, cache_read=1.25, cache_write=0.0),
        thinking_level_map=None,
        max_tokens=16384,
        context_window=128000,
        reasoning=False,
        input=["text", "image"],
    ),
    "gpt-4o-mini": Model(
        id="gpt-4o-mini",
        name="GPT-4o mini",
        provider="openai",
        api="openai-completions",
        cost=ModelCost(input=0.15, output=0.6, cache_read=0.075, cache_write=0.0),
        thinking_level_map=None,
        max_tokens=16384,
        context_window=128000,
        reasoning=False,
        input=["text", "image"],
    ),
    "o1-preview": Model(
        id="o1-preview",
        name="OpenAI o1-preview",
        provider="openai",
        api="openai-completions",
        cost=ModelCost(input=15.0, output=60.0, cache_read=7.5, cache_write=0.0),
        thinking_level_map=_OPENAI_REASONING_THINKING_MAP,
        max_tokens=32768,
        context_window=128000,
        reasoning=True,
        input=["text"],
    ),
}

# === OpenRouter ===============================================================

_OPENROUTER_MODELS: dict[str, Model] = {
    "anthropic/claude-sonnet-4-5": Model(
        id="anthropic/claude-sonnet-4-5",
        name="Anthropic Claude Sonnet 4.5 (via OpenRouter)",
        provider="openrouter",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        cost=ModelCost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75),
        thinking_level_map=_ANTHROPIC_THINKING_MAP,
        max_tokens=64000,
        context_window=200000,
        reasoning=True,
        input=["text", "image"],
    ),
    "openai/gpt-4o": Model(
        id="openai/gpt-4o",
        name="OpenAI GPT-4o (via OpenRouter)",
        provider="openrouter",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        cost=ModelCost(input=2.5, output=10.0, cache_read=1.25, cache_write=0.0),
        thinking_level_map=None,
        max_tokens=16384,
        context_window=128000,
        reasoning=False,
        input=["text", "image"],
    ),
    "openai/o1-preview": Model(
        id="openai/o1-preview",
        name="OpenAI o1-preview (via OpenRouter)",
        provider="openrouter",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        cost=ModelCost(input=15.0, output=60.0, cache_read=7.5, cache_write=0.0),
        thinking_level_map=_OPENAI_REASONING_THINKING_MAP,
        max_tokens=32768,
        context_window=128000,
        reasoning=True,
        input=["text"],
    ),
    "meta-llama/llama-3.1-70b-instruct": Model(
        id="meta-llama/llama-3.1-70b-instruct",
        name="Meta Llama 3.1 70B Instruct (via OpenRouter)",
        provider="openrouter",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        cost=ModelCost(input=0.5, output=0.75, cache_read=0.0, cache_write=0.0),
        thinking_level_map=None,
        max_tokens=8192,
        context_window=128000,
        reasoning=False,
        input=["text"],
    ),
    # Sprint 6f W6 (P-174): expand seed margin from exact-10 to 13 so
    # ``test_seed_catalog_at_least_10_models`` has headroom against
    # accidental Sprint 6g catalog consolidation. Three additional
    # popular OpenRouter aliases below — anthropic Claude Opus 3.5,
    # OpenAI o1-mini, Google Gemini 1.5 Pro (long-context).
    "anthropic/claude-opus-3-5": Model(
        id="anthropic/claude-opus-3-5",
        name="Claude Opus 3.5 (via OpenRouter)",
        provider="openrouter",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        cost=ModelCost(input=15.0, output=75.0, cache_read=1.5, cache_write=18.75),
        thinking_level_map=None,
        max_tokens=8192,
        context_window=200000,
        reasoning=False,
        input=["text", "image"],
    ),
    "openai/o1-mini": Model(
        id="openai/o1-mini",
        name="OpenAI o1-mini (via OpenRouter)",
        provider="openrouter",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        cost=ModelCost(input=3.0, output=12.0, cache_read=1.5, cache_write=0.0),
        thinking_level_map=_OPENAI_REASONING_THINKING_MAP,
        max_tokens=65536,
        context_window=128000,
        reasoning=True,
        input=["text"],
    ),
    "google/gemini-pro-1.5": Model(
        id="google/gemini-pro-1.5",
        name="Gemini 1.5 Pro (via OpenRouter)",
        provider="openrouter",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        cost=ModelCost(input=1.25, output=5.0, cache_read=0.3125, cache_write=0.0),
        thinking_level_map=None,
        max_tokens=8192,
        context_window=2_000_000,
        reasoning=False,
        input=["text", "image"],
    ),
}

# === Pi-shape catalog ========================================================
# Pi parity: ``models.generated.ts`` exports a single ``MODELS`` constant.
# Provider insertion order = ``get_providers()`` order.

MODELS: dict[str, dict[str, Model]] = {
    "anthropic": _ANTHROPIC_MODELS,
    "openai": _OPENAI_MODELS,
    "openrouter": _OPENROUTER_MODELS,
}


__all__ = ["MODELS"]
