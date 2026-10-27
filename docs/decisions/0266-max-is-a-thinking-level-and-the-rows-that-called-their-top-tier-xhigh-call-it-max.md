# 0266. `max` is a thinking level of its own, and the rows that called their top tier `xhigh` call it `max`

Status: Accepted (2026-10-20, the owner's decision on #433; corrected 2026-10-21 by the owner's
decisions on OpenRouter's DeepSeek V4 rows and on the map-less DeepSeek V4 rows, review round 2);
amended 2026-10-27 (#172's fold-back: decision 5's rules applied by hand to the rows it adds)
Date: 2026-10-20
Issue: #433
Amends: ADR-0064 (the level scale, dated note); ADR-0135 ("`EXTENDED_THINKING_LEVELS` is
unchanged at six levels", and the budget table's top row, dated note). ADR-0065's clamp is
unchanged. Keeps ADR-0264 true: "the 24 rows #433 reclassifies" and "a derived `max` is kept
after #433".

## Context

pi added a seventh thinking level in `fbdd46389` (2026-07-09, "feat(ai): add max thinking
level"): `EXTENDED_THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]`
(`packages/ai/src/models.ts:1242` at `pi@fdce48eb4`, 2026-10-20). `getSupportedThinkingLevels`
gates `max` exactly like `xhigh` - offered only where a model's `thinkingLevelMap` names it
(`:1250`) - and `clampThinkingLevel` is unchanged: up first, then down (`:1255-1274`). The same
commit renamed every map that spelled a provider's native top tier `xhigh: "max"` (DeepSeek V4,
GLM 5.2, Claude Opus 4.6) to `max: "max"`, and gave every adaptive Claude model `max`
(`packages/ai/scripts/generate-models.ts:1131-1158` at `fdce48eb4`). A later pi fix gave
OpenRouter's DeepSeek V4 rows `xhigh: "xhigh"` and no `max` again, because OpenRouter answered
DeepSeek's `max` with a 400 (pi #4801, `packages/ai/CHANGELOG.md:1020`; the rule is
`generate-models.ts:1165-1182`, which also gives every map-less DeepSeek V4 completions row a map).

aelix had six levels in three fixed lists (`aelix_ai/models.py`, `cli/args.py`,
`models_json.py`) and two `Literal` types (`aelix_ai/settings/types.py`, the OpenAI adapters'
`reasoning_effort`). MEASURED on `7faf4b9a` with the real CLI against a local recorder (fake key;
`.omc/probes/433-live/impl/wire.before.txt`):

- `--thinking max` was dropped with a warning; `defaultThinkingLevel: "max"` started the harness
  at `off`, because the clamp answers an unknown spelling with the model's first level. Both sent
  `thinking: {"type": "disabled"}` on `anthropic/claude-opus-4-6`, `claude-opus-4-7` and
  `claude-sonnet-4-5`, and on `openrouter/deepseek/deepseek-v4-pro`. In the TUI, `/thinking max`
  confirmed `thinking → max (off)`.
- 24 shipped rows mapped `xhigh` to the provider's `max`, so their picker said `xhigh (max)` and
  offered no `max`.

## Decision

1. **The scale is pi's seven**, in pi's order, in every list and type that names the levels:
   `EXTENDED_THINKING_LEVELS`, `VALID_THINKING_LEVELS`, `models_json._THINKING_LEVELS`,
   `settings.types.ThinkingLevel`, `models.ThinkingLevel` and the two adapter `Literal`s.
   Everything else reads one of these (`is_valid_thinking_level`, agent profiles, the delegated
   child's `--thinking`, the `/thinking` picker, the `/settings` cycle, the statusline).
2. **`max` is gated like `xhigh`**: a model offers it only where its map names it with a non-null
   value (`get_supported_thinking_levels`).
3. **The clamp is unchanged** (ADR-0065). With `max` above `xhigh`, a saved `xhigh` on a row that
   names only `max` scans up and lands on `max`.
4. **The 24 rows are reclassified** (the owner's decision of 2026-10-20 on #433, corrected
   2026-10-21). The 24 rows whose map said `"xhigh": "max"` are Claude Opus 4.6 on every
   provider (12), DeepSeek V4 (8) and GLM 5.2 (4). 21 of them say `"max": "max"` and have no
   `xhigh` key: a `defaultThinkingLevel` or session level of `xhigh` there clamps to `max` and
   sends exactly what `7faf4b9a` sent for `xhigh` (`output_config.effort: "max"`,
   `reasoning_effort: "max"`); only the name on screen moves. The other 3 are OpenRouter's
   DeepSeek V4 rows (`deepseek/deepseek-v4-pro`, `-flash`, `-flash:free`), which follow pi:
   `{..., "xhigh": "xhigh", "max": null}`. **That is a wire change**: a saved `xhigh` there sent
   `reasoning_effort: "max"` on `7faf4b9a` and sends `"xhigh"` now, and `max` is not offered
   (it clamps down to `xhigh`). The session file is not rewritten.
5. **pi's other `max` rules are applied to the rows aelix ships** (`generate-models.ts` at
   `fdce48eb4`): `opus-4-6`/`sonnet-4-6` merge `{max}` (`:1134-1141`); `opus-4-7`/`4-8`/`5`,
   `sonnet-5`, `haiku-5` merge `{xhigh, max}` (`:1142-1155`); `fable-5` merges
   `{off: null, xhigh, max}` (`:1156-1158`); `supportsOpenAiMax` (`gpt-5.6` or `gpt-6` on the
   four OpenAI APIs) merges `{max}` (`:600-607`, `:1122-1124`); and an `openai-completions` row
   whose id contains `deepseek-v4` and that has no map gets one (`:1165-1182`, the owner's
   decision of 2026-10-21): OpenRouter's `{minimal, low, medium: null, high, xhigh: "xhigh",
   max: null}`, the Flash rows on `deepseek`, `opencode`, `opencode-go` the Flash map (`low: "low"`, `:285-288`),
   every other one `{minimal, low, medium: null, high: "high", max: "max"}` (`:278-284`; the
   Azure map has no shipped row). That rule reaches 15 rows: 4 on OpenRouter, 4 Flash rows, 7
   others. A merge is pi's `mergeThinkingLevelMap` (`:529-531`). Two exceptions, both recorded
   below.
6. **What each adapter sends for `max`** - the map's value through the existing paths, measured
   in `tests/test_max_thinking_level_433.py` and the recorder:

   | adapter | `max` sends | where |
   |---|---|---|
   | anthropic-messages, adaptive row | `output_config.effort: "max"` | Opus 4.6 and later, Sonnet 4.6 and later, Fable 5 |
   | anthropic-messages, budget row | `xhigh`'s 32768 budget | only a `models.json` map can offer `max` there; pi folds both onto `high` |
   | openai-completions (OpenRouter format) | `reasoning.effort: "max"` | OpenRouter Claude rows. OpenRouter's `~deepseek/deepseek-v4-flash-latest` offers no `max`: `xhigh` sends `reasoning.effort: "xhigh"` |
   | openai-completions (DeepSeek format) | `reasoning_effort: "max"` + `thinking.enabled` | DeepSeek V4 on `deepseek`, `opencode`, `opencode-go`. OpenRouter's (this format) offer no `max`: `xhigh` sends `reasoning_effort: "xhigh"` |
   | openai-completions (OpenAI format) | `reasoning_effort: "max"` | GLM 5.2 on `fireworks` and `opencode-go`; DeepSeek V4 on `cloudflare-workers-ai`. `nvidia`'s DeepSeek V4 rows send no effort at any level (`supportsReasoningEffort: false`) |
   | openai-completions (Z.AI format) | `enable_thinking: true` only, as for `high` | GLM 5.2 on `zai` and `zai-coding-cn`: aelix's branch sends no effort (`openai_completions.py:780-781`), so `max` thinks as `high` does there, as `xhigh` did on `7faf4b9a`. pi's branch sends `thinking` plus the mapped `reasoning_effort` (`packages/ai/src/api/openai-completions.ts:884-896`): a follow-up |
   | openai-responses / codex | `reasoning.effort: "max"` | `gpt-5.6`, `gpt-6-astra` |
   | Google | `HIGH` / dynamic `-1`, as `xhigh` | no Google row names `max` |
   | bedrock-converse-stream | - | aelix has no Bedrock adapter; the maps follow pi and are inert |

7. **No `max` budget setting.** `thinkingBudgets` keeps its five keys (pi's stop at `high`);
   `max` borrows `xhigh`'s row on the budget path so a higher level never sends a smaller budget.
8. **The pi anchor of `models.ts` reads at `fdce48eb4` for the level scale and the two gates**
   (ADR-0235: an interpretation anchor). At `734e08e` (ADR-0034's pin) the file was 92 lines and
   the helpers sat at `:48-80`; at `fdce48eb4` it is 1,283 lines and they sit at `:1242-1274`.
   `aelix_ai/models.py`'s header says so; every other anchor in that file still reads at
   `734e08e`. The `734e08e` fixture in `tests/pi_parity/fixtures/` keeps its six levels: the
   parity tests now assert pi's seven and that the `734e08e` six are their first six, in order.
9. **A refresh keeps the maps.** `scripts/refresh_catalog.py` is additive-only and writes no
   `thinkingLevelMap`, so it cannot reintroduce `xhigh: "max"`
   (`test_a_refresh_from_upstream_cannot_undo_the_reclassification`). ADR-0264's #172 generator
   derives maps for new ids; with `max` in the build, a derived `max` is kept and sent as `max`.
10. **Every path that applies a saved or profile level fits it to the model** (review round 2).
    Round 1 left three paths asking whether the row lists the level *as written*, so a saved
    `xhigh` on a row that names `max` and no `xhigh` was treated as foreign there:
    - `/agents use` (`agents/service.py`) applies a profile's `thinking:` clamped to a settled
      model, as the launch clamps `defaultThinkingLevel`: `thinking: xhigh` on Opus 4.6 runs at
      `max` (round 1: skipped, the request kept the previous level), `thinking: max` on a row
      without it at the row's top level (`claude-sonnet-4-5`: `high`). On a model that cannot
      reason the level lands as `off`, where it was skipped (the request carries no reasoning
      either way); on a held launch's placeholder a level it does not list is still skipped.
    - The TUI's record of `defaultThinkingLevel` (`cli/entry.py` `_record_tui_default_level`)
      writes a default of `xhigh` that the launch moved up to `max` as written, `xhigh`, as
      `7faf4b9a` recorded it on the 21 renamed rows, so a reopen after the default changes comes
      back at `xhigh` and sends `max` (round 1: nothing recorded, the reopen followed the new
      default). The rule also covers the rows that gained `max` without `xhigh` in #433 (Sonnet
      4.6, the 11 map-less DeepSeek V4 rows off OpenRouter), which `7faf4b9a` ran at `high` and
      did not record. Every other default the launch had to fit is still not recorded.
    - `cycle_thinking_level` (`harness/core.py`; the `/settings` Thinking level row and RPC
      `cycle_thinking_level` - aelix's shift+tab cycles the permission posture) fits the live
      level before the step, as pi's live level always is fitted (its setter clamps,
      `agent-session.ts:2642-2681`). An unfitted `xhigh` from `--thinking` or an RPC
      `set_thinking_level` on Opus 4.6 steps as `max` and wraps to `off`, as on `7faf4b9a`
      (round 1: `minimal`); on a row with neither top level it steps as `high`, also to `off`
      (`7faf4b9a`: `minimal`).
    `--thinking` and an RPC `set_thinking_level` still put the level on the harness as written;
    every adapter clamps it at request build.

## Not applied (and why)

- **The `google-vertex` Claude ids** (8 rows: `claude-opus-4-6@default` and its siblings). They
  are aelix-only rows on the Gemini transport, which sends one dynamic budget for every level;
  offering `xhigh`/`max` there would name tiers the request cannot carry.
- **`max` on `gpt-5.6` rows whose map does not offer `xhigh` yet** (10 rows on `opencode`,
  `opencode-go` and `openrouter`). pi gives them `xhigh` by a different rule
  (`supportsOpenAiXhigh`, `:589-598`); adding `max` alone would turn a saved `xhigh` into `max`
  instead of `xhigh`.
- **pi rules that are not about `max`**, left to their own issues: DeepSeek V4 Flash's
  `low: "low"` (`:285-288`) on the three Flash rows that carried a map before #433
  (`deepseek/deepseek-v4-flash`, `opencode-go/deepseek-v4-flash`, `opencode/deepseek-v4-flash-free`;
  the map-less Flash rows get it with pi's map-less rule above, so the two kinds differ on `low`
  until then); Fireworks GLM 5.2's `low`/`medium: null` (`:1228-1232`); pi now derives Z.AI's
  GLM 5.2 map from models.dev (`3de00332f`); and pi's Z.AI thinking branch, which sends an
  effort where aelix's sends only `enable_thinking` (Decision 6).

## Consequences

- A saved `xhigh` sends what it sent on the 21 renamed rows of the 24. On rows that gain `max`
  without `xhigh` (Sonnet 4.6, 12 rows) a saved `xhigh` now runs at `max` instead of `high` -
  pi's behaviour, and measured: `defaultThinkingLevel: "xhigh"` on `anthropic/claude-sonnet-4-6`
  sent effort `high` on `7faf4b9a` and `max` now (`wire.extra.txt`, E4).
- Rows that offered neither top level and now offer both (31 rows, Opus 4.8+/Sonnet 5 mirrors)
  send `xhigh` for `xhigh` instead of `high` (E2: OpenRouter `anthropic/claude-opus-5`).
- The Fable mirrors no longer offer `off` (pi's rule; the anthropic-messages ones already never
  sent `disabled`, ADR-0135 #258). On OpenRouter's Fable rows an unset level now starts at
  `minimal` and sends `reasoning.effort: "minimal"` instead of `"none"` (E1).
- On OpenRouter's 3 DeepSeek V4 rows of the 24 a saved `xhigh` sends `reasoning_effort: "xhigh"`
  where `7faf4b9a` sent `"max"` (the owner's decision, pi #4801).
- The 15 map-less DeepSeek V4 rows take only DeepSeek's tiers now (measured,
  `.omc/probes/433-live/r2/sweep-r2-rows.txt`): `minimal`, `low` and `medium` move up to `high`
  (on the 4 Flash rows `minimal` moves to `low` and `low` stays), and `xhigh`, which clamped down
  to `high`, moves up to `max` (to `xhigh` on OpenRouter's 4). On `nvidia`'s 4 nothing on the
  wire moves: they send no effort at any level.
- The `thinking_level_display` rule needs no change: `xhigh (max)` now means "clamped up".

## Evidence

`.omc/probes/433-live/impl/` (probes, recorder logs, TUI frames from a pty, the row list as
`reclassified-rows.md`; round 1), `.omc/probes/433-live/r2/` (review round 2: each regression
reproduced on round 1's `fcd2e8d4`, the real CLI against a local recorder, the adapter sweep of
the changed rows, pty frames), `tests/test_max_thinking_level_433.py`,
`tests/cli/test_max_thinking_level_433.py`, `tests/harness/test_harness_cycle_thinking_level.py`.

## Appendix: every changed row

changed rows: 132; distinct (old -> new) pairs: 17; no other field and no row set changed. Read from `models_generated.json` at `7faf4b9a` and after #433 (review round 2); the row set is the same.

- `null` → `{"xhigh": "xhigh", "max": "max"}` (31): `amazon-bedrock/anthropic.claude-opus-4-8`, `amazon-bedrock/anthropic.claude-opus-5`, `amazon-bedrock/anthropic.claude-sonnet-5`, `amazon-bedrock/au.anthropic.claude-opus-4-8`, `amazon-bedrock/au.anthropic.claude-opus-5`, `amazon-bedrock/au.anthropic.claude-sonnet-5`, `amazon-bedrock/eu.anthropic.claude-opus-4-8`, `amazon-bedrock/eu.anthropic.claude-opus-5`, `amazon-bedrock/eu.anthropic.claude-sonnet-5`, `amazon-bedrock/global.anthropic.claude-opus-4-8`, `amazon-bedrock/global.anthropic.claude-opus-5`, `amazon-bedrock/global.anthropic.claude-sonnet-5`, `amazon-bedrock/jp.anthropic.claude-opus-4-8`, `amazon-bedrock/jp.anthropic.claude-opus-5`, `amazon-bedrock/jp.anthropic.claude-sonnet-5`, `amazon-bedrock/us.anthropic.claude-opus-4-8`, `amazon-bedrock/us.anthropic.claude-opus-5`, `amazon-bedrock/us.anthropic.claude-sonnet-5`, `opencode/claude-opus-4-8`, `opencode/claude-opus-5`, `opencode/claude-sonnet-5`, `openrouter/anthropic/claude-opus-4.8`, `openrouter/anthropic/claude-opus-4.8-fast`, `openrouter/anthropic/claude-opus-5`, `openrouter/anthropic/claude-opus-5-fast`, `openrouter/anthropic/claude-sonnet-5`, `vercel-ai-gateway/anthropic/claude-opus-4.8`, `vercel-ai-gateway/anthropic/claude-opus-4.8-fast`, `vercel-ai-gateway/anthropic/claude-opus-5`, `vercel-ai-gateway/anthropic/claude-opus-5-fast`, `vercel-ai-gateway/anthropic/claude-sonnet-5`

- `{"xhigh": "xhigh"}` → `{"xhigh": "xhigh", "max": "max"}` (20): `amazon-bedrock/anthropic.claude-opus-4-7`, `amazon-bedrock/eu.anthropic.claude-opus-4-7`, `amazon-bedrock/global.anthropic.claude-opus-4-7`, `amazon-bedrock/jp.anthropic.claude-opus-4-7`, `amazon-bedrock/us.anthropic.claude-opus-4-7`, `anthropic/claude-opus-4-7`, `anthropic/claude-opus-4-8`, `anthropic/claude-opus-5`, `anthropic/claude-sonnet-5`, `cloudflare-ai-gateway/claude-opus-4-7`, `github-copilot/claude-opus-4.7`, `github-copilot/claude-opus-4.8`, `github-copilot/claude-opus-5`, `github-copilot/claude-sonnet-5`, `opencode/claude-opus-4-7`, `openrouter/anthropic/claude-opus-4.7`, `openrouter/anthropic/claude-opus-4.7-fast`, `openrouter/openai/gpt-6-astra`, `openrouter/openai/gpt-6-astra-pro`, `vercel-ai-gateway/anthropic/claude-opus-4.7`

- `null` → `{"off": null, "xhigh": "xhigh", "max": "max"}` (13): `amazon-bedrock/anthropic.claude-fable-5`, `amazon-bedrock/anthropic.claude-fable-5-1`, `amazon-bedrock/eu.anthropic.claude-fable-5`, `amazon-bedrock/global.anthropic.claude-fable-5`, `amazon-bedrock/global.anthropic.claude-fable-5-1`, `amazon-bedrock/us.anthropic.claude-fable-5`, `amazon-bedrock/us.anthropic.claude-fable-5-1`, `opencode/claude-fable-5`, `opencode/claude-fable-5-1`, `openrouter/anthropic/claude-fable-5`, `openrouter/anthropic/claude-fable-5.1`, `vercel-ai-gateway/anthropic/claude-fable-5`, `vercel-ai-gateway/anthropic/claude-fable-5.1`

- `null` → `{"max": "max"}` (12): `amazon-bedrock/anthropic.claude-sonnet-4-6`, `amazon-bedrock/au.anthropic.claude-sonnet-4-6`, `amazon-bedrock/eu.anthropic.claude-sonnet-4-6`, `amazon-bedrock/global.anthropic.claude-sonnet-4-6`, `amazon-bedrock/jp.anthropic.claude-sonnet-4-6`, `amazon-bedrock/us.anthropic.claude-sonnet-4-6`, `anthropic/claude-sonnet-4-6`, `cloudflare-ai-gateway/claude-sonnet-4-6`, `github-copilot/claude-sonnet-4.6`, `opencode/claude-sonnet-4-6`, `openrouter/anthropic/claude-sonnet-4.6`, `vercel-ai-gateway/anthropic/claude-sonnet-4.6`

- `{"xhigh": "max"}` → `{"max": "max"}` (12): `amazon-bedrock/anthropic.claude-opus-4-6-v1`, `amazon-bedrock/au.anthropic.claude-opus-4-6-v1`, `amazon-bedrock/eu.anthropic.claude-opus-4-6-v1`, `amazon-bedrock/global.anthropic.claude-opus-4-6-v1`, `amazon-bedrock/us.anthropic.claude-opus-4-6-v1`, `anthropic/claude-opus-4-6`, `cloudflare-ai-gateway/claude-opus-4-6`, `github-copilot/claude-opus-4.6`, `opencode/claude-opus-4-6`, `openrouter/anthropic/claude-opus-4.6`, `openrouter/anthropic/claude-opus-4.6-fast`, `vercel-ai-gateway/anthropic/claude-opus-4.6`

- `null` → `{"minimal": null, "low": null, "medium": null, "high": "high", "max": "max"}` (7): `cloudflare-workers-ai/@cf/deepseek-ai/deepseek-v4-flash-0731`, `cloudflare-workers-ai/@cf/deepseek-ai/deepseek-v4-pro-0813`, `nvidia/deepseek-ai/deepseek-v4-flash`, `nvidia/deepseek-ai/deepseek-v4-flash-0731`, `nvidia/deepseek-ai/deepseek-v4-pro`, `nvidia/deepseek-ai/deepseek-v4-pro-0813`, `opencode/deepseek-v4-pro`

- `{"off": null, "xhigh": "xhigh"}` → `{"off": null, "xhigh": "xhigh", "max": "max"}` (6): `anthropic/claude-fable-5`, `anthropic/claude-fable-5-1`, `github-copilot/gpt-5.6-luna`, `github-copilot/gpt-5.6-sol`, `github-copilot/gpt-5.6-terra`, `opencode/gpt-6-astra`

- `{"minimal": null, "low": null, "medium": null, "high": "high", "xhigh": "max"}` → `{"minimal": null, "low": null, "medium": null, "high": "high", "max": "max"}` (5): `deepseek/deepseek-v4-flash`, `deepseek/deepseek-v4-pro`, `opencode-go/deepseek-v4-flash`, `opencode-go/deepseek-v4-pro`, `opencode/deepseek-v4-flash-free`

- `{"off": "none", "xhigh": "xhigh"}` → `{"off": "none", "xhigh": "xhigh", "max": "max"}` (5): `openai/gpt-5.6`, `openai/gpt-5.6-luna`, `openai/gpt-5.6-sol`, `openai/gpt-5.6-terra`, `openai/gpt-6-astra`

- `null` → `{"minimal": null, "low": "low", "medium": null, "high": "high", "max": "max"}` (4): `deepseek/deepseek-v4-flash-vision-exp`, `opencode-go/deepseek-v4-flash-vision-exp`, `opencode/deepseek-v4-flash`, `opencode/deepseek-v4-flash-vision-exp`

- `null` → `{"minimal": null, "low": null, "medium": null, "high": "high", "xhigh": "xhigh", "max": null}` (4): `openrouter/deepseek/deepseek-v4-flash-0731`, `openrouter/deepseek/deepseek-v4-flash-vision-exp`, `openrouter/deepseek/deepseek-v4-pro-0813`, `openrouter/~deepseek/deepseek-v4-flash-latest`

- `{"xhigh": "xhigh", "minimal": "low"}` → `{"xhigh": "xhigh", "minimal": "low", "max": "max"}` (4): `openai-codex/gpt-5.6`, `openai-codex/gpt-5.6-luna`, `openai-codex/gpt-5.6-sol`, `openai-codex/gpt-5.6-terra`

- `{"minimal": null, "low": null, "medium": null, "high": "high", "xhigh": "max"}` → `{"minimal": null, "low": null, "medium": null, "high": "high", "xhigh": "xhigh", "max": null}` (3): `openrouter/deepseek/deepseek-v4-flash`, `openrouter/deepseek/deepseek-v4-flash:free`, `openrouter/deepseek/deepseek-v4-pro`

- `{"minimal": null, "low": "high", "medium": "high", "high": "high", "xhigh": "max"}` → `{"minimal": null, "low": "high", "medium": "high", "high": "high", "max": "max"}` (2): `zai-coding-cn/glm-5.2`, `zai/glm-5.2`

- `{"xhigh": "xhigh"}` → `{"xhigh": "xhigh", "off": null, "max": "max"}` (2): `github-copilot/claude-fable-5`, `github-copilot/claude-fable-5.1`

- `{"off": "none", "minimal": null, "low": "high", "medium": "high", "xhigh": "max"}` → `{"off": "none", "minimal": null, "low": "high", "medium": "high", "max": "max"}` (1): `fireworks/accounts/fireworks/models/glm-5p2`

- `{"off": null, "minimal": null, "low": null, "medium": null, "high": "high", "xhigh": "max"}` → `{"off": null, "minimal": null, "low": null, "medium": null, "high": "high", "max": "max"}` (1): `opencode-go/glm-5.2`

## Amendment (2026-10-27, #172) - the fold-back's rows take decision 5's rules by hand

The generator still writes only the map it derives (decision 9), so the 348 rows the #172
fold-back added arrived without decision 5's rules where those apply. Applied by hand in that
commit, to 24 rows:
- `{xhigh, max}` merged on 20 Bedrock Claude 4.7+ and 5.x rows, as pi's `mergeThinkingLevelMap`
  does.
- The DeepSeek V4 rule on the four `openai-completions` V4.1 Flash rows that came with no map:
  - `opencode` (2) and `opencode-go` take the Flash map (`low: "low"`), because models.dev lists
    `low` for them. pi's `includes("deepseek-v4-flash")` misses the `.1`, and pi.dev's own rows
    have `low`.
  - `nvidia` takes the V4 map, as its shipped V4 rows do.

The rule is for a row "that has no map". The two OpenRouter V4.1 Flash rows and the Cloudflare
one came with maps derived from OpenRouter's `supported_efforts` (`[max, high, low, none]`) and
models.dev's effort list, and keep them. A review caught the first version of the commit
overwriting them.

`test_every_deepseek_v4_completions_row_follows_pis_rule` keeps its exact checks. It names those
three rows as derived-map rows, which must offer `high`, and counts the 27 the rule covers (23 +
4). `test_pis_max_rules_hold_on_every_row_they_cover` is unchanged. ADR-0264 §10 (2026-10-27)
lists the fold-back's other corrections.
