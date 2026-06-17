# 0135. Reasoning/Thinking End-to-End Wiring (close the P0 #1 no-op across harness + OpenAI + Anthropic)

Status: Accepted
Date: 2026-06-17
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

The exhaustive pi-parity gap inventory flagged **P0 #1**: the entire
thinking/reasoning stack was a no-op. `/thinking`, Shift+Tab, `--thinking`, and
`:level` all mutated and persisted `AgentState.thinking_level`, but the value
never reached a provider. `AgentLoopConfig` had no `reasoning` field;
`loop.py:244` built `SimpleStreamOptions` without it; so the harness'
`_make_stream_fn` line `reasoning=options.reasoning` was always `None`.

Initial trace work (4 parallel readers + a reconciler) produced a plan but
**inferred** two things it could not verify against the repo: the Anthropic
`budget_tokens` mapping (catalog `thinkingLevelMap` carries only sparse strings,
no token counts), and whether the OpenAI adapter needed a `thinkingLevelMap`
lookup. Rather than ship guesses, we read pi's actual source — the reference is
**fetchable** at the pin (`raw.githubusercontent.com/earendil-works/pi/734e08e/
packages/ai/src/providers/{anthropic,openai-completions,simple-options}.ts`).
Reading it corrected three conclusions:

1. **Anthropic is fully specified, not a defer/guess.** pi splits by model id:
   *adaptive* thinking (Opus 4.6+/Sonnet 4.6 → `output_config.effort`) vs
   *budget-based* (older → `thinking.budget_tokens`), with a concrete default
   budget table and `max_tokens` adjustment. The earlier inferred budgets
   (`low 4096`, `xhigh 32768`) were **wrong** — pi uses `low 2048`, and `xhigh`
   clamps to `high` (16384).
2. **OpenAI is NOT "zero edits."** pi's `buildParams` applies
   `model.thinkingLevelMap?.[effort] ?? effort` in the deepseek + openrouter
   branches (`openai-completions.ts:570-571, 577-578`). The Aelix port omitted
   this — a latent divergence, dormant only because reasoning was never
   populated, that would go hot the moment Layer 1 landed.
3. **`"off"` must arrive as `None`.** pi's `streamSimpleAnthropic` disables
   thinking via `if (!options.reasoning)`, so the string `"off"` would wrongly
   *enable* high thinking. The harness therefore maps `thinking_level == "off"`
   → `None`.

## Decision

The thinking level is carried as the **raw string** on
`SimpleStreamOptions.reasoning` (`"off"` → `None`); each provider adapter
resolves it natively. Three layers, all pi-faithful:

### Layer 1 — state → loop options
- `AgentLoopConfig` gains `reasoning: str | None = None` (`types.py`).
- `core.py:_run` snapshots `self._state.thinking_level` into `config.reasoning`,
  collapsing `"off"`/unset → `None`. Re-read every turn from `_state`, so a
  `set_thinking_level` between prompts takes effect on the next `prompt()` with
  **no `prepare_next_turn` dependency** (mirrors `set_model`).
- `loop.py:244` forwards `reasoning=config.reasoning` onto `SimpleStreamOptions`.
  The harness already copied `options.reasoning` into the final provider options
  (`_make_stream_fn`); it was simply starved.

### Layer 2 — OpenAI-completions `build_params`
- A `_native_effort(model, effort)` helper mirrors pi's `thinkingLevelMap?.[e]
  ?? e` (faithful `??`: only a missing/`None` map value falls back; a present
  value — even `""` — is used verbatim), applied in **all four** reasoning
  branches pi maps: deepseek (`:570-571`), openrouter (`:577-578`), together
  (`:590`), and the OpenAI-style default (`:594`). The openrouter `off` branch
  honors an explicit `thinkingLevelMap.off`, omitting `reasoning` only when it is
  explicitly `null` (`:580-581`); a new OpenAI-style off-string branch emits
  `thinkingLevelMap.off` when it is a string (`:595-600`). No change to
  `stream_simple_openai_completions`, which already clamps `opts.reasoning` →
  `reasoning_effort` (pi-parity P-62, verified correct) — clamp happens before
  the map, exactly as pi.

### Layer 3 — Anthropic request thinking param
- New `resolve_anthropic_thinking(model, reasoning, default_max_tokens)` in
  `_anthropic_transforms.py` (+ helpers `supports_adaptive_thinking`,
  `map_thinking_level_to_effort`, `clamp_reasoning`,
  `adjust_max_tokens_for_thinking`, default budgets `{minimal:1024, low:2048,
  medium:8192, high:16384}`, `INTERLEAVED_THINKING_BETA`).
  - non-reasoning model → no thinking param;
  - reasoning model, no level → `thinking={type:"disabled"}`;
  - adaptive model → `thinking={type:"adaptive", display:"summarized"}` +
    `output_config={effort}` (effort from `thinkingLevelMap` else coarse
    fallback);
  - older reasoning model → `thinking={type:"enabled", budget_tokens, display}`
    with `budget_tokens` carved from (and below) `max_tokens`, + the
    `interleaved-thinking-2025-05-14` beta header (`_with_interleaved_beta` in
    `anthropic.py`; adaptive models skip it, pi:784).
- Anthropic SDK 0.102.0 natively accepts `thinking` / `output_config` /
  `budget_tokens` kwargs (verified) — no `extra_body` relocation needed.

## Consequences

- The full thinking stack is live for the OpenAI-completions family (OpenRouter,
  DeepSeek, Qwen/zai, Together, Groq, Fireworks, native o-series) and Anthropic
  (adaptive + budget). One Layer-1 change lit up the OpenAI family; Layer 2
  fixed the latent map bug it activated; Layer 3 added the Anthropic request side.
- Pi-faithful simplifications (Aelix `SimpleStreamOptions` does not yet plumb
  `options.maxTokens` / `temperature` / `thinkingBudgets` / `thinkingDisplay`):
  the budget path uses `model.max_tokens` as its base (so thinking is carved from
  within the model cap), `display` is the pi default `"summarized"`, custom
  budgets are unsupported, and temperature-vs-thinking exclusivity is moot (the
  adapter never sets temperature today). All match pi defaults; the knobs are a
  future extension, not a divergence.
- **Interleaved-thinking beta — deliberate narrower scope.** Aelix sends the
  `interleaved-thinking-2025-05-14` beta only on the active budget-thinking path
  (non-adaptive reasoning model with a level set). pi sends it for *every*
  non-adaptive model — even non-reasoning / "off" — and lets a caller's
  `anthropic-beta` *replace* it via `mergeHeaders` ordering. Matching that fully
  would (a) break aelix's established "caller `anthropic-beta` wins" setdefault
  contract (`tests/oauth`) and (b) require a `mergeHeaders` refactor of the
  OAuth header path — both out of ADR-0135's reasoning scope. Gating on active
  thinking is the only case where interleaved thinking is functional. Full pi
  parity (universal beta + caller-replace semantics) is a tracked follow-up.
- **Deferred:** mid-run application of `set_thinking_level` during an active
  multi-turn loop (the `AgentLoopTurnUpdate.thinking_level` field + a
  `prepare_next_turn` bridge); this matches current `set_model` behavior.
- **Follow-up (pre-existing, out of scope):** OAuth `anthropic-beta` omits
  `claude-code-20250219` (pi:849); non-reasoning Anthropic `max_tokens` default
  is `4096` vs pi's `model.maxTokens/3` (pi:893). Neither introduced here.

## Pi parity citations (SHA 734e08e)
- `packages/ai/src/providers/openai-completions.ts:421-438` (streamSimple clamp +
  `"off"`→undefined), `:558-588` (per-format thinking, incl. thinkingLevelMap).
- `packages/ai/src/providers/anthropic.ts:165, 692-767, 784-790, 939-968`
  (adaptive vs budget, effort map, beta header, buildParams thinking block).
- `packages/ai/src/providers/simple-options.ts:22-50` (clampReasoning,
  adjustMaxTokensForThinking + default budgets).

## Tests (36, all green)
- `tests/test_adr0135_reasoning_state_wiring.py` — Layer 1: loop forwards
  `config.reasoning`; harness snapshots `thinking_level`; `"off"`→`None`;
  per-turn re-read; same-session off↔level transitions; mid-turn
  `set_thinking_level` does NOT affect the current turn (snapshot-once).
- `tests/providers/test_adr0135_reasoning_wiring.py` — Layer 2: thinkingLevelMap
  applied across deepseek/openrouter/together/default + `off`/null + off-string
  branches + no-map regression + **end-to-end clamp→map through `stream_simple`**;
  Layer 3: adaptive `output_config` (map-driven, not tautological) + fallback,
  budget `budget_tokens` (incl. xhigh→high), off→disabled, non-reasoning omitted,
  interleaved beta on budget/API-key/OAuth paths and off on adaptive/non-reasoning,
  helper units.

## Cross-references
- ADR-0114 (OpenRouter `extra_body` relocation — now hot for reasoning models).
- ADR-0118 (developer-role gating — independent of `reasoning_effort`).
- ADR-0065 (`clamp_thinking_level`); ADR-0045 (`SimpleStreamOptions.reasoning`);
  ADR-0064 (`thinking_level_map` field); ADR-0034 (pi pin / fetch method).
- Closes gap-inventory **P0 #1** (`.omc/specs/pi-parity-gap-inventory.md`).
