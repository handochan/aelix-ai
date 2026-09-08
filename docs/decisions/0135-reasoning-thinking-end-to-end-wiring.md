# 0135. Reasoning/Thinking End-to-End Wiring (close the P0 #1 no-op across harness + OpenAI + Anthropic)

Status: Accepted (**#250 amendment 2026-09-08** — `xhigh` is a budget tier of its
own, 32768, on the rows that offer it, and `stream_anthropic` clamps the level
against the model row like every sibling adapter; **Correction 2, 2026-09-09** —
the carve is capped so tiers cannot invert, an unbuildable budget disables
thinking, and the Gemini fallback is `-1` rather than the family ceiling; see
the Amendment and Correction 2 below)
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
   clamps to `high` (16384). (`low 2048` still stands. The `xhigh` half was
   **reversed by the #250 amendment below**: 32768 is what aelix sends, and the
   2026-06-17 sentence remains true only of pi.)
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
  medium:8192, high:16384, xhigh:32768}` — the fifth row added by the #250
  amendment; pi has four — `INTERLEAVED_THINKING_BETA`).
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
  budget `budget_tokens` (incl. xhigh as its own 32768 tier, #250), off→disabled,
  non-reasoning omitted,
  interleaved beta on budget/API-key/OAuth paths and off on adaptive/non-reasoning,
  helper units.

## Cross-references
- ADR-0114 (OpenRouter `extra_body` relocation — now hot for reasoning models).
- ADR-0118 (developer-role gating — independent of `reasoning_effort`).
- ADR-0065 (`clamp_thinking_level`); ADR-0045 (`SimpleStreamOptions.reasoning`);
  ADR-0064 (`thinking_level_map` field); ADR-0034 (pi pin / fetch method).
- Closes gap-inventory **P0 #1** (`.omc/specs/pi-parity-gap-inventory.md`).

## Amendment (#250, 2026-09-08) — `xhigh` becomes a budget tier of its own

**On this ADR's binding principle.** The "pi agent를 완전 동일하게 완벽하게
구현이 1차적 목표입니다" framing at the top of this file was retired by
**ADR-0235**: pi is a verified reference implementation, not a target, and a
divergence from it no longer requires its own ADR. This amendment is such a
divergence, recorded here because it reverses a decision *this* ADR made.

**Measured before the change** (`anthropic/claude-opus-5`,
`default_max_tokens=32000`): `high` and `xhigh` both produced
`{"thinking": {"type": "enabled", "budget_tokens": 16384, "display":
"summarized"}}` with `max_tokens` 48384 — byte-identical requests, while the
statusline read `🧠 xhigh`. `clamp_reasoning` folded `xhigh` onto `high`
(pi `simple-options.ts:22-24`), and that lookup is the only budget resolution
on the Anthropic budget path.

**Decision.** `_DEFAULT_THINKING_BUDGETS` gains `"xhigh": 32768` — twice
`high`, the value `_BUDGET_2_5_PRO` uses for its own top tier, and the value
this ADR's Context §1 inferred in 2026-06-17 before pi parity overrode it.
`clamp_reasoning` keeps its name and its single call site, takes an optional
`budgets: dict[str, int] | None` (the **merged** table, defaults plus
`custom_budgets`), and clamps a spelling the table does not know to
`"medium"` — the budget the `.get` fallback in
`adjust_max_tokens_for_thinking` already gave it. `"medium"` and not `"high"`
because unvalidated levels really do arrive: `set_thinking_level` assigns
without validating, and `"off"` reaches this function because
`resolve_anthropic_thinking` gates on `if not reasoning` and `"off"` is
truthy (Context §3). Clamping unknowns to `"high"` would have silently
doubled their budget from 8192 to 16384. The `.get` fallback at the call site
stays, so a future "restore pi parity" edit inside `clamp_reasoning` produces
a wrong number rather than an unhandled `KeyError`.

**The output-cap regime 32768 does not escape.** `_effective_output_cap`
(`anthropic.py`) clamps a row whose `maxTokens >= contextWindow` to
`_UNSAT_ABSOLUTE_OUTPUT_CEILING = 32000`, and that value is both the budget
base and the hard clamp. For any budget `B > high`, an effective cap in
`(16384, B]` leaves the visible answer exactly `_MIN_OUTPUT_TOKENS` (1024)
tokens, and a cap in `(16384, 17408)` made `xhigh` send a *smaller* budget
than `high` — the second window was set by `high`'s own carve boundary and was
**independent of `B`**. Measured: none of the 20 rows that OFFER `xhigh` is
clamped and none of their caps lands in either window (16384 / 64000 /
128000), so both regimes need a `maxTokens`/`contextWindow` override in
`models.json` to reach. That is why lowering the constant is not a fix, and the
first window is stated in the CHANGELOG rather than engineered around. The
second — the inversion — **was** engineered away in the end; the past tense in
this paragraph belongs to "Correction 2" below, which reverses the decision the
next paragraph records.

**Correction (beta2 review).** The paragraph above was true only of the rows
that offer `xhigh`, and the first revision of this change let the 32768 row
reach the rows that do not. `--thinking xhigh` is validated against the
spelling, not the model (`cli/args.py`), `cli/entry.py` forwards it verbatim
and `harness/core.py` snapshots the state level with no model check — and
`stream_anthropic` was the ONE adapter that did not run `opts.reasoning`
through `clamp_thinking_level` (its five siblings all do). Until this change
`clamp_reasoning`'s `xhigh → high` fold was standing in for that clamp.
Measured with the fold gone and no clamp: `anthropic/claude-opus-4-1`
(cap 32000, `xhigh` not offered) sent `budget_tokens: 30976` and left the
visible answer 1024 tokens, against 16384 / 15616 on 0985fcf — 28 catalog rows
have a cap in `(16384, 32768]` and 46 more are clamped into it by
`_effective_output_cap`. **Decision: `stream_anthropic` now clamps with
`clamp_thinking_level` like every sibling adapter.** On the 20 offering rows
it is the identity, on the other 252 it maps `xhigh` back to `high`
(backward scan, the contract `tests/pi_parity/test_phase_4_6_strict_superset.py`
already pins), and `None` stays `None`. Not checked against pi — the pin at
the top of this file is not fetched here, and ADR-0235 no longer requires the
comparison. The alternative — flooring the carve so `xhigh` can never leave
less room than `high` — was rejected *as a substitute*, because on its own it
would have left this adapter still disagreeing with its five siblings about
which levels a row supports. Correction 2 adopts it **as well**: the two fix
different things, and only the clamp fixes the level.

**Rejected.** *24576* — it stays under the 32000 ceiling but only moves the
first window's edge and does nothing to the second, so it buys no regime;
the tie goes to the number the tables and this ADR already use. *65536* — on
the 64000-cap `github-copilot` rows it would always shrink to 62976, so the
constant would misdescribe what is sent. *`max_tokens // 2`* — the wire value
would then depend on the caller's override, so no test could pin a number.

**Scope.** Dispatch is keyed on `model.api` alone, so every row with
`api == "anthropic-messages"` that offers `xhigh` and is not on the
`supports_adaptive_thinking` whitelist is affected — **20 rows**: four
Anthropic ids (`claude-fable-5`, `claude-opus-4-8`, `claude-opus-5`,
`claude-sonnet-5`), their four `github-copilot` mirrors, and twelve
`vercel-ai-gateway` `openai/gpt-5.2`…`gpt-5.5` rows. Two of the twelve —
`openai/gpt-5.2-chat`, `openai/gpt-5.3-chat`, cap 16384 — are unchanged,
because the carve shrinks both tiers to 15360 there, so the request Aelix
builds differs on **18**. The adaptive path (Opus 4.6/4.7, Sonnet 4.6) is
untouched.

**Correction 2 turned the "note as a finding" here into a measurement, and it
takes 8 of those 18 out of the count that matters.**
`supports_adaptive_thinking` knows only `opus-4-6/4-7` and `sonnet-4-6`, so
`claude-opus-4-8`, `claude-opus-5`, `claude-sonnet-5` and `claude-fable-5`
take the budget path at all. The request Aelix builds for those four was sent
verbatim to `api.anthropic.com` on 2026-09-09, at `high` and at `xhigh`, and
all eight return `400 invalid_request_error` — *"`thinking.type.enabled` is not
supported for this model. Use `thinking.type.adaptive` and
`output_config.effort` to control thinking behavior."*

| id | `high` | `xhigh` |
|---|---|---|
| `claude-opus-5` | `req_011CerQpNc69m6CAsK93KroH` | `req_011CerQpLuQC5P5Xaw7MzyVQ` |
| `claude-fable-5` | `req_011CerQpRVUJivivpJ9SmDHp` | `req_011CerQpQ3uQqfCeUsKaPg9M` |
| `claude-opus-4-8` | `req_011CerQpUWYMoCu4L9t8WS7w` | `req_011CerQpSw3MHtbeDFKJYEpi` |
| `claude-sonnet-5` | `req_011CerQpXRAezGeumCSuhkSk` | `req_011CerQpVws2QN9E2uMrD6yC` |

What is rejected is the `thinking.type` value, identical on every budget-path
level, so the two measured levels stand for all five: thinking has never worked
on those rows at any level. `claude-opus-4-7` — an id this whitelist *does*
carry — answered normally at both levels through the adaptive branch
(`req_011CerQpYrVRK4GK5pf5sJ8x`, `req_011CerQpdX2jsWYUfDcBa1x6`), so the branch
is sound and only its membership test is wrong. The same 400 lands on 0985fcf,
so it is pre-existing: neither introduced nor fixed here. It is filed as
[#258](https://github.com/handochan/aelix-ai/issues/258) rather than repaired
in this commit because the repair moves every level on those rows onto a
different request shape, and that deserves its own review and its own live
pass. The issue also records what this round found in the catalog: `models_generated.json` already carries
`compat.forceAdaptiveThinking: true` on exactly those four ids plus
`claude-opus-4-7`, and nothing in the package reads it — so the fix is to
consult the row, not to lengthen the marker list. Their four `github-copilot`
mirrors are the same models behind a proxy and were **not** measured. So of the
18 changed rows the new budget can only take effect on the **ten**
`vercel-ai-gateway` `openai/gpt-5.x` rows, against which no request was made.

**Google: the resolvers become total, and the `xhigh` row is declined.**
Issue #250 also asked for an `xhigh` row in the Gemini 2.x integer
`thinkingBudget` tables. We do something else, deliberately. The two bare
index lookups (`_google_shared.get_google_budget`,
`google_vertex._vertex_google_budget`) raised an unhandled `KeyError` —
reproduced on both adapters with `"thinkingLevelMap": {"xhigh": "xhigh"}` on
`gemini-2.5-pro`, a documented user-editable field of `models.json` — and
because they escape synchronously from the *sync* stream factory they never
became an `AssistantErrorEvent`. They now fall back to `-1`, the API's dynamic
budget (**Correction 2** — the first revision fell back to the family's `high`
row). No `xhigh` row is added because for Gemini 2.5 the `high` entry already
*is* the API's `thinkingBudget` ceiling (32768 pro, 24576 both flash tables),
so such a row would either duplicate `high` — a fake tier — or be rejected
with a 400. That is the honest asymmetry with the Anthropic half, where the
ceiling is `max_tokens` rather than an API constant.

**Also.** `ThinkingBudgetsSettings` gains an `xhigh` field plus its
`NESTED_PY_TO_JSON` row (without the row the key is dropped silently at the
JSON boundary). It is schema only: `get_thinking_budgets()` has no caller and
neither provider passes `custom_budgets`, so all five fields are equally
unread. `EXTENDED_THINKING_LEVELS` is unchanged at six levels.

## Correction 2 (#250 Codex cross-review, 2026-09-09)

The cross-review found three defects in the amendment above and one false
sentence in its CHANGELOG entry. The reviewer scoped them all to overrides and
embedders — all 20 shipped rows offering `xhigh` do remain strictly valid, and
the two 16384-cap rows still send 15360 — and that would have been enough
(`models.json` overrides are a documented feature, `SimpleStreamOptions` is
public). Working through defect 1 turned up one case that needs neither: see
"Correction to the review's scope" under defect 1.

**1. The carve is capped, not shrunk — `adjust_max_tokens_for_thinking`
diverges from pi.** Pi shrinks the budget only once it has swallowed the cap
whole (`if (maxTokens <= thinkingBudget)`, simple-options.ts:26-50). With a
tier budget `B` that leaves the window `(B, B + 1024)` where the visible answer
gets **less** than `_MIN_OUTPUT_TOKENS` — at `maxTokens: 32769` with `xhigh`,
exactly one token — and because the window moves with `B`, two tiers could
cross: measured on 73d167a, `adjust(17000, 17000, "xhigh")` returned 15976
against `"high"`'s 16384, so asking for *more* reasoning got *less*. **Decision:
the budget is `max(0, min(B, model_max_tokens - _MIN_OUTPUT_TOKENS))`,** computed
before `max_tokens`. That is monotonic in `B` by construction, so no cap can
invert two tiers again, and the minimum answer room the docstring promises is
now delivered — **for the carve, which is all it was ever a promise about.**
The beta2 re-review caught that sentence stated unqualified in its first
revision. The room is `min(base_max_tokens, model_max_tokens - budget)`; the
cap above bounds only the right operand. A caller passing a `base_max_tokens`
under 1024 still gets that many visible tokens — `adjust(1, 1025, "high")` is
`(2, 1)` — because that is the number it asked for, and lifting it to 1024
would return more answer than was requested. Not reachable in-tree (compaction
sets `max_tokens` but never `reasoning`; the harness sets `reasoning` but never
`max_tokens`), so this is an embedder-facing invariant, and
`test_the_carve_never_takes_more_answer_room_than_it_must` now walks both arms
of that `min` rather than only `base == cap`. On the shipped catalog the two rules agree at every
*declared* cap (none lands in a window — the closest, 8192 / 16384 / 32768, sit
on the boundary), which is why every existing expected value in
`tests/providers/` is unchanged. `tests/providers/test_adr0135_reasoning_wiring.py`
walks the property over every cap from 1025 to 3999 and then strides to 40000
by 11, rather than pinning the one value the review reported.

**Correction to the review's scope, measured here.** The review says these
failures "require caller/model overrides". The inversion does — it also needs
the row to offer `xhigh`, and no row whose cap is *computed* does. The lost
answer room does not: `_effective_output_cap` clamps the 46 rows declaring
`maxTokens >= contextWindow` to `min(context_window - prompt - margin, 32000)`,
so their cap is a function of the prompt and a long enough prompt walks it into
a window. Measured on the shipped `accounts/fireworks/models/deepseek-v3p1`
row (window == cap == 163840, `high` offered, nothing overridden): a
~583k-character prompt yields an effective cap of ~17000, where the old rule
sent `budget_tokens: 16384` and left the answer ~626 tokens. Pinned by
`test_a_long_prompt_alone_reaches_the_carve_hole_on_a_shipped_row`, which
searches for the prompt length instead of pinning it, because that number
belongs to the shared token estimate.

**2. Thinking turns itself off rather than build a request Anthropic rejects.**
`budget_tokens: budget or 1024` (pi's `|| 1024`, anthropic.ts) could only ever
send an invalid request: at `"maxTokens": 1024` the carve returns 0 and the
fallback sent `budget_tokens: 1024` with `max_tokens: 1024` — the API requires
the budget to be *strictly* smaller — and at 512 the budget exceeded the whole
request. **Decision: below `_MIN_THINKING_BUDGET + _MIN_OUTPUT_TOKENS` (2048),
`resolve_anthropic_thinking` returns `{"thinking": {"type": "disabled"}}`.** The
alternative was to clamp the budget under `max_tokens`, but under 2048 the only
clamp available is a budget below Anthropic's own 1024 floor — a different 400,
not a fix. `disabled` is a shape this function already emits, so the turn
answers. `_MIN_THINKING_BUDGET` is a new constant, numerically equal to
`_MIN_OUTPUT_TOKENS` and kept separate because it states the API's rule rather
than aelix's reserve. The first revision of this paragraph labelled that rule
**READ** — from the Claude API reference, "must be less than `max_tokens`,
minimum 1024 — errors otherwise" — and said no model on hand could exercise it,
because the four ids Scope names reject the `thinking.type: "enabled"` shape
before any budget is validated. That second half was wrong: 13 other
first-party rows still take the budget path. **Both halves of the rule are now
MEASURED** on `claude-haiku-4-5` against `api.anthropic.com`, 2026-09-09 —
`budget_tokens: 512` under `max_tokens: 4096` returns *"thinking.enabled.
budget_tokens: Input should be greater than or equal to 1024"*
(`req_011CerQpihKtfUSfimNLoaPZ`); `budget_tokens: 2048` with `max_tokens: 2048`
returns *"`max_tokens` must be greater than `thinking.budget_tokens`"*
(`req_011CerQpkFasPMAjVBdaPMDK`); `budget_tokens: 1024` under `max_tokens:
2048` answers with a `thinking` block (`req_011CerQpmN4cr9DZAtFwqYzn`).

**3. The Gemini fallback goes DOWN, not up.** The amendment above made an
unknown effort resolve to the family's `high` row, which is the API's
`thinkingBudget` ceiling — so a `thinkingLevelMap` value Aelix could not
interpret bought the most expensive request the family can make. Failing open
to the maximum is the wrong direction for a billable knob. **Decision: `-1`, the
API's dynamic budget.** It invents no number, keeps thinking on, and is already
what both resolvers answer for a model id they do not recognise.

*Which value that actually is, added by the beta2 re-review:* `xhigh`, and
in-tree only `xhigh`. `_thinking_for_simple` passes a level
`clamp_thinking_level` has already reduced to a member of
`EXTENDED_THINKING_LEVELS`, and maps `off`/`None` to `high` itself, so the four
table keys cover every other spelling. The re-review then asked why the two
*thinking-level* resolvers (`get_thinking_level`,
`google_vertex._vertex_thinking_level`) still end `return "HIGH"` for that same
`xhigh` if failing open to the maximum is wrong. **They are not fixed, because
they are not the same case.** `thinkingLevel` has no `-1`. `get_google_budget`
already answers `-1` for a model id it does not recognise, so the dynamic
budget is a value this codebase itself uses to mean "no opinion"; every branch
of `get_thinking_level` returns one of the four scale points, and Google
defines a dynamic setting only for `thinkingBudget` (read from the API docs,
not measured). So `HIGH` is a clamp to the top of the scale rather than a guess
— the same thing the
Anthropic adapter's `clamp_thinking_level` does with `xhigh` on a row that does
not offer it. One rule covers both: use the API's "you decide" value where one
exists, clamp to the ceiling where it does not. Pinned by
`test_xhigh_clamps_to_the_top_of_the_thinking_level_scale`, which also asserts
the reachable effort set, so an `off`/`None` leak or a sixth level — either of
which would make that branch a real fail-open — fails there.

**4. `SimpleStreamOptions.max_tokens` grows the payload — intended, and now
said so.** With `reasoning="xhigh", max_tokens=16384` on `claude-opus-5` the
request carries `max_tokens: 49152`. The field's comment called itself a
payload-level cap; the arithmetic is pi's `min(base + budget, model.maxTokens)`,
whose own parameter comment ("Undefined means no explicit caller cap. Use the
model cap and fit thinking inside it.") settles that a *defined* base is a base
to add the budget to. What the caller's number bounds is the visible answer, and
that invariant holds. **Decision: the words were wrong, not the maths** — the
comment on the field was rewritten and the invariant pinned by a test.

**Not fixed here — `"off"` as a string does not turn thinking off.** Reachable
through `SimpleStreamOptions(reasoning="off")`: `resolve_anthropic_thinking`
gates on `if not reasoning` and `"off"` is truthy (Context §3), so it reaches
the budget path and `clamp_reasoning` sends it to `"medium"`. #250 did not
change that number and neither does this correction — but the adapter-side
`clamp_thinking_level` added above **does** change what two rows send, which
the beta2 re-review found missing from the CHANGELOG. Measured across all 287
`anthropic-messages` reasoning rows, that clamp differs from its input in
exactly two ways: `xhigh → high` on 257 rows, and `off → minimal` on the two
that declare `"off": null` (`anthropic/claude-fable-5`,
`anthropic/claude-sonnet-5`). On those two a literal `reasoning="off"` now
sends `budget_tokens: 1024` where 0985fcf sent 8192. Still embedder-only — the
harness collapses `off` to `None` first — and now stated in the CHANGELOG. It
is filed as [#259](https://github.com/handochan/aelix-ai/issues/259) rather
than fixed here because it is not an Anthropic-adapter bug: both Google
adapters map `"off"` to `"high"` explicitly (`google_generative_ai.py:308`,
`google_vertex.py:432`), which is worse, and all three inherit it
from pi — so the fix is one cross-adapter contract for the literal string on a
public
field, with the ADR amendment that goes with it. The harness collapses `"off"`
to `None` before any adapter sees it, so the reachable surface is embedders.
`tests/providers/test_adr0135_reasoning_wiring.py::test_off_passed_as_a_string_still_enables_thinking`
characterises it so the next change to this code cannot make it worse silently.
