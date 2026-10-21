# 0114. Sprint 6h₁₃ — OpenRouter Qwen3 Tool-Calling Fixes

Status: Accepted (Sprint 6h₁₃ / W4 shipped) — amended 2026-10-21 (#563): every output cap an
adapter sends is cut to what the context window still holds, as pi does; the `max_tokens` guard's
omission of a model-default cap is kept, with a usage-aware estimate; review round 2 2026-10-22: the
estimate counts text at pi's 3.5 characters a token, so the cap never leaves less room than pi's
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

## Amendment (2026-10-21, #563) — every output cap fits what the window still holds

A request takes `prompt + max_tokens` of the window. An endpoint that checks that sum rejects a
long conversation's request once `prompt > context_window - max_tokens`; auto-compaction waits
for `prompt > context_window - 16384` (`harness/core.py`, `_AUTO_COMPACT_RESERVE_TOKENS`). With a
32000-token cap that leaves a 15616-token band in which every turn fails before compaction is
asked for. The fix #3 guard above covered only `maxTokens >= contextWindow`, and #144 widened it to
"the model's own cap does not fit"; a caller's cap, and the Anthropic adapter's catalog cap, went
out regardless.

### What was measured on `27fa21c5`

A local recorder that answers like a sum-enforcing endpoint (400 ``input length and `max_tokens`
exceed context limit: X + Y > W`` when `prompt + max_tokens > 60000`), a `models.json` model on
`anthropic-messages` with `contextWindow` 60000 and `maxTokens` 32000, and the real `aelix -p` /
`-c` CLI adding about 8039 tokens a turn (isolated HOME, agent dir, settings and session dir, a fake
key; `.omc/probes/b4-a-live/563/`):

- Turns 1-3 answered; turns 4, 5 and 6 failed with ``input length and `max_tokens` exceed context
  limit: 35102 + 32000 > 60000`` (then 43123, 51145). `is_context_overflow` did not read that text
  as an overflow, so overflow recovery did nothing.
- After turn 6 the error path's estimate (51087) crossed the threshold and a compaction ran. It
  kept 35101 tokens, and 35101 + 32000 still did not fit: turns 7 and 8 failed the same way. The
  session could not recover.

Read on the same commit: `anthropic.py` sent the catalog cap and clamped only rows with
`maxTokens >= contextWindow` (#149); `openai_completions.py` omitted a model-default cap that did
not fit, judged by the payload's characters alone, and sent a caller's cap "unconditionally";
`openai_responses.py` and both Google adapters sent a caller's cap unchanged and none of their own;
`openai_codex_responses.py` sends none.

### Decision

1. **pi's clamp** (`clampMaxTokensToContext`, `packages/ai/src/api/simple-options.ts:18-22` at
   `fdce48eb4`, pi `09f105957` for pi #6061) is `providers/_token_estimate.py`
   `clamp_max_tokens_to_context`: `min(cap, context_window - estimate - 4096)`, at least 1; with no
   known window, the cap.
2. **The estimate** is pi's `estimateContextTokens`: the last answer that reported usage (not
   `error`, not `aborted`, a positive total) anchors the count with its `total_tokens`, and what
   came after it is estimated from its text at pi's 3.5 characters a token, a non-ASCII
   character a whole token (an image is pi's 4800 characters, a flat 1372 tokens, not its base64
   length; review round 2 below). With no such answer the system prompt, the tools and every message are estimated. An
   answer with a later-stamped message before it does not anchor (pi's rule: after a compaction
   the kept answers carry the long conversation's usage). aelix stamps a turn's messages in seconds
   and a rebuilt compaction summary in milliseconds, so the comparison reads both as milliseconds.
3. **Where it applies.** `anthropic-messages`: both the base (the catalog cap or the caller's) and
   the ceiling the thinking budget may grow to, so every branch of `resolve_anthropic_thinking`
   sends a cap that fits — pi re-clamps after `adjustMaxTokensForThinking`
   (`anthropic-messages.ts:1004`); a window too full to hold a 1024 budget plus a 1024 answer turns
   budget thinking off, the existing rule. A caller's cap in `openai-completions`,
   `openai-responses` (never below 16, which OpenAI Responses rejects — pi #6265) and both Google
   adapters. The Codex adapter sends no cap, as in pi.
4. **The model-default rule here is kept, not replaced by pi's clamp.** `openai-completions` still
   omits its own cap when it does not fit, because omission leaves the provider the whole remaining
   window and fix #3 / #144 measured it working; pi clamps that cap instead (its `buildBaseOptions`
   defaults to `model.maxTokens`). The one change is the estimate: the larger of the payload's
   characters and the usage-anchored estimate, because text that tokenizes denser than 4 characters
   a token (code) under-counted and the cap went out. `openai-responses` and the Google adapters
   still send no cap of their own (pi sends the clamped default).
5. **The 2026-08-10 Anthropic measurement.** The first-party endpoint accepted
   `prompt + max_tokens > context_window` on claude-haiku-4-5 (thinking setting unrecorded); that
   is why #149 clamped only broken rows. It stays true and is now outweighed: the Anthropic adapter
   also serves endpoints that enforce the sum (MiniMax's Anthropic-compatible API, pi #6061), where
   the request fails with an error compaction never saw; and on the first-party endpoint the clamp
   costs at most the 4096-token margin plus the estimate's error, only once the window holds less
   than the cap, where an answer could never run past the window anyway. pi clamps everywhere. The
   UNSAT protection (`_UNSAT_ABSOLUTE_OUTPUT_CEILING`, 32000) is kept under the clamp: the window
   bound alone was measured not to stand in for an output ceiling a broken row never encodes.
6. **The sum check's errors are overflows.** `utils/overflow.py` reads Anthropic's ``input length
   and `max_tokens` exceed context limit`` and Hugging Face TGI's `` `inputs` tokens + `max_new_tokens`
   must be <= `` (aelix-only — not in pi's list), and pi's newer z.ai, DS4 and DashScope patterns
   (`prompt (?:is )?too long`, `prompt exceeds max length`, `prompt has N tokens, but the
   configured context size is N tokens`, `range of input length should be`). pi's
   provider-gated Cerebras rule and its 413 prefix rule are not ported (they read pi's own error
   formatting). The Anthropic text is the error as users report it, not measured here.

Not changed: the auto-compaction threshold (a fixed 16384; `compaction.reserveTokens` is #469), the
compaction summary's own caps (#506), and pi's compact-and-retry for an answer cut short by the
clamp (`isRecoverableLength`) — a follow-up.

### After, on this tree

The same probe: 8 of 8 turns answered. The cap went 32000, 32000, 28868, 20829, 12791, 4753, and
every request asked for `prompt + max_tokens` = 55931 of 60000 (the window less 4096, plus 27 of
estimate error). The threshold compaction ran after turn 6 (`tokensBefore` 51183), and the turns
after it fit (20933 and 12743) on the estimate that no longer anchors on the kept answers. With
the overflow patterns alone and no clamp, turn 4's error was read as an overflow and recovery
compacted and re-ran it once; turns 5 and 6 then failed again (`35167 + 32000`), so the patterns
are the safety net and the clamp is the fix.
`tests/providers/test_output_cap_clamp_563.py` (35 rows): 18 fail on `27fa21c5` with only the new
helper module added; 25 sabotages on a throwaway worktree — each fix piece reverted, and the
plausible wrong forms (the base clamped but not the thinking ceiling, a 1024 margin, a 0 floor, the
model default clamped instead of omitted, seconds compared with milliseconds raw) — all fail.

### Found while measuring, not changed here

After the first compaction in a session, neither threshold nor overflow compaction runs again:
`_check_auto_compaction`'s and `_try_overflow_recovery`'s staleness guards compare the last
answer's `timestamp` (seconds, `loop.py` `time.time()`) with the compaction boundary in
milliseconds, so every answer reads as older than the boundary. Measured with the same probe over
13 turns: one compaction, then turns 11-13 failed with `prompt is too long` (an overflow the
patterns do read) and no second compaction ran. Filed as #565 and fixed there (ADR-0176, amendment
2026-10-22): the guards read both timestamps in milliseconds.

### Review round 2 (2026-10-22)

The estimate was not pi's: it counted the text after the anchor at `ceil(len / 4)` and an image
as 1200 tokens, where pi's `estimate.ts` divides by 3.5 (`CHARS_PER_TOKEN`) and charges an image
4800 characters. On the same context aelix therefore asked for a larger cap than pi, and on an
endpoint that counts the text the way pi assumes the request went over the window where pi's
fits. Measured with pi's own `estimateContextTokens` at `fdce48eb4` (node) against aelix's: an
answer that reported 20000 tokens, then 150000 ASCII characters, window 90000, cap 32000 — pi
62858 and a cap of 23046, aelix 57500 and 28404 (62858 + 28404 = 91262 > 90000). Over eight
shapes (short and long text, mixed non-ASCII, an image, a tool call with thinking) aelix was
below pi in four.

`_token_estimate.py` now counts the context the clamp reads at 3.5 characters a token
(`estimate_context_text_tokens`: `ceil(ascii / 3.5)` plus a whole token per non-ASCII character),
an image as `ceil(4800 / 3.5)` = 1372, and, with no anchor, the tools as their JSON (name,
description, parameters) at the same rate, as pi's `estimateToolsTokens` does. Each block is
rounded up on its own where pi rounds a message once, so the count is never below pi's: on the
same eight shapes it equals pi's on the five plain-text ones and is 1, 2 and 1071 higher on the
image, the tool call and the non-ASCII one. The 150000-character case now gives pi's 62858 and
23046. The guard of the model's own cap in `openai-completions` (Decision 4) keeps
`estimate_text_tokens`' 4 characters for the payload and takes the larger of that and this
estimate. Evidence: `.omc/probes/b4-a-live/r2/` (`563-*`); the row
`test_clamp_reads_pis_estimate_where_four_characters_a_token_would_overflow` fails with the
divisor put back to 4.
