# 0114. Sprint 6h₁₃ — OpenRouter Qwen3 Tool-Calling Fixes

Status: Accepted (Sprint 6h₁₃ / W4 shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**
This sprint contains three *deliberate, documented divergences* from a verbatim Pi
port — each is a Python-SDK or OpenRouter-routing correctness fix that Pi's TypeScript
implementation does not need (or does not handle), justified case-by-case below.

## Context

The user reported that `qwen/qwen3.6-35b-a3b` — a widely-used hybrid-thinking MoE — works
for tool calling in **pi** and **opencode** but produced **no structured `tool_calls`** in
Aelix. A prior investigation wrongly concluded "model limitation." Re-investigation with raw
OpenRouter stream captures (key from `.env`, never logged) + end-to-end runs through the real
adapter found **three compounding bugs**, none of which is the model:

1. **Broken provider endpoint.** OpenRouter load-balances this model across 7 providers;
   3 declare `tools` support (Ambient, Parasail, AkashML). **Ambient** streams only the
   `<think>` block then `finish_reason=stop` — no `content`, no `<tool_call>` tags, no
   structured `tool_calls` (so there is nothing to parse client-side). `require_parameters:
   true` does **not** exclude it (it declares `tools` support); `sort` (throughput/price/
   latency) does not avoid it. Only naming providers works. Parasail ✅ and AkashML ✅ emit
   structured calls. *(Reference finding: pi sends NO provider routing and the SAME
   `reasoning:{effort:"none"}` for this model — so pi "working" is Parasail-routing luck, not
   a mechanism; opencode omits reasoning for `id.includes("qwen")` but still relies on the
   provider lottery. Confirmed against pi `openai-completions.ts`/`models.generated.ts` and
   opencode `transform.ts`.)*
2. **Python-SDK kwarg rejection.** `_open_stream` calls `create(**params)`. The OpenAI
   **Python** SDK raises `TypeError: got an unexpected keyword argument 'reasoning'` on
   OpenRouter extension keys (`reasoning`, `provider`, …). Pi's TS SDK forwards unknown
   top-level fields into the body; the Python SDK validates and rejects them. This was masked
   because the test fakes accept `**kwargs` and `gpt-4o-mini` emits none of these keys — so
   **every** OpenRouter reasoning model (and any model with provider routing) errored on the
   first chunk through the real SDK.
3. **`maxTokens == contextWindow`.** 127 of 942 catalog models list max output equal to the
   full context window. Sending it as the *output* cap leaves no room for the prompt → 400
   "this endpoint's maximum context length is N … you requested … in the output" on strict
   endpoints (which is exactly where fix #1 routes).

## The decisions

- **Provider routing (`_openai_compat.py`)**: `detect_compat` seeds `open_router_routing`
  from `_OPENROUTER_TOOL_ROUTING_OVERRIDES = {"qwen/qwen3.6-35b-a3b": {"ignore": ["Ambient"]}}`
  for OpenRouter models (with a defensive deep-copy of list values). The adapter already
  forwards `compat.open_router_routing` onto `params["provider"]`, so this is the existing
  Pi-parity seam. **Divergence rationale**: Pi sources routing only from catalog
  `compat.openRouterRouting`, which upstream leaves empty for this model — a catalog edit
  would be wiped on regeneration, so the policy lives in detection logic (survives regen). A
  user-supplied `model.compat` still overrides via `get_compat`. `require_parameters` is
  intentionally NOT set (does not exclude Ambient + over-constrains → 404).
- **`extra_body` relocation (`openai_completions.py`)**: `_relocate_extra_body_params` moves
  the non-OpenAI extension keys (`reasoning`, `provider`, `enable_thinking`,
  `chat_template_kwargs`, `thinking`, `tool_stream`, `providerOptions`, `prompt_cache_retention`)
  into `extra_body` at the SDK boundary — **after** the `on_payload` hook (so the hook still
  sees Pi-shaped flat params). `reasoning_effort`/`max_tokens`/`stream_options`/`store`/
  `prompt_cache_key` are native and stay top-level. On a key collision the relocated
  top-level value takes precedence over a pre-existing `extra_body` entry.
- **`max_tokens` guard (`build_params`)**: omit `max_tokens`/`max_completion_tokens` when
  `max_tokens >= context_window > 0` (the cap is meaningless; the provider clamps output to
  fit the context). `context_window == 0` (unknown) keeps the cap — the conservative choice.

## Consequences

- `qwen/qwen3.6-35b-a3b` produces structured tool calls reliably (5/5 raw, 4/4 end-to-end
  through the real adapter: `ToolCallStart → Delta×5 → ToolCallEnd → AssistantDone`).
- Fix #2 silently un-breaks **every** OpenRouter reasoning model and any future model with
  provider routing — previously a first-chunk `TypeError` through the real SDK.
- Fix #3 changes behavior for 129 models (`maxTokens >= contextWindow`): they now omit the
  cap. This is strictly more correct (none could use the full window as output); the standard
  spec behavior for an absent `max_tokens` is "generate until natural stop / context limit,"
  which is the intended outcome. Models with a real, smaller cap are unaffected.
- **Known / accepted**: hardcoded provider name `"Ambient"` is a denylist that may need
  revisiting if OpenRouter provider quality changes; the routing override is keyed on the
  canonical lowercase OpenRouter slug.

## Verification (W4)

- Gate: ruff clean; `uv run pyright` 8-error baseline (0 new); full `pytest` 2912 passed / 1
  skipped (+13 new ADR-0114 tests); protected paths byte-unchanged; schema `--check` pass.
- New tests (`tests/providers/test_adr0114_qwen_tool_routing.py`): routing seed + get_compat
  merge + copy-isolation + non-listed/non-OpenRouter negatives + `build_params` provider
  forwarding; `_relocate_extra_body_params` full key set + native-key retention + existing-
  `extra_body` merge + collision precedence + no-op; `max_tokens` omit at `==`/`>` + send at
  `<`/unknown.
- **Live**: raw OpenRouter capture isolated the cause (Ambient ❌ / Parasail ✅ / AkashML ✅;
  `ignore:["Ambient"]` 5/5; provider-sort never avoids Ambient); end-to-end through
  `stream_simple_openai_completions` on the real catalog model produced tool calls 4/4.
- **W4 code-reviewer (opus): COMMENT → addressed** — HIGH (`prompt_cache_retention` missing
  from the extra_body set) fixed in-sprint; MEDIUM (stale `require_parameters` comment;
  merge-precedence doc) fixed; LOW test gaps (collision precedence, `>` semantics) added.
