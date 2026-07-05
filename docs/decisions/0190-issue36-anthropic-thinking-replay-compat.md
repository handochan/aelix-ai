# ADR-0190 — Anthropic thinking-block replay + `model.compat` detection (#36 anthropic side)

- **Status:** Accepted — LIVE (the `anthropic-messages` adapter is already un-hidden; this is user-visible the moment it lands).
- **Date:** 2026-07-05
- **Sprint:** Owner-set order `#65 → #67 → #36 → #61`. Closes the remaining anthropic half of #36 (owner-confirmed re-scope: `/login` model-wiring already shipped — ADR-0172/0175 — so the residual named scope is adapter-side `model.compat` detection + thinking-block replay). **Scope = "B-lite" + Gemini polish** (owner-confirmed via AskUserQuestion 2026-07-05).
- **Pi pin:** `earendil-works/pi@734e08e`, `packages/ai/src/providers/anthropic.ts`. The capture/replay/compat behavior is a faithful port; deliberate divergences are documented below.
- **Relates:** ADR-0135 (Anthropic extended-thinking request wiring — this ADR adds the *response capture + replay* half), ADR-0171 (openai-side `model.compat` session-affinity), ADR-0175 (gemini `/login` auto-register), ADR-0047/0049 (`ThinkingContent` type + shared `_transform_messages`). GitHub #36, parent #3.

## Context

Six concrete gaps left the aelix Anthropic adapter unable to faithfully round-trip extended thinking + tool use, and left the owner-named `model.compat` detection unported:

1. **Capture** — `content_block_start type="thinking"` emits `ThinkingStartEvent` but appends **nothing** to `output_content` (`anthropic.py:470-480`); `redacted_thinking` and `signature_delta` are entirely unhandled. Thinking signatures are never captured.
2. **Latent index-misalignment data-loss bug (real, live)** — because every *other* block type appends one entry at `content_block_start` but `thinking` appends none, the positional guards `index < len(output_content)` at `anthropic.py:512-515` (text body) and `:578-581` (tool-input finalize) **always fail for any block after a thinking block**. The finalize/mutate write-back is skipped, so a `tool_use` following a thinking block persists with **empty `input={}`** (arguments dropped) and text after thinking stays empty. `get_final_message()` does not mask it — its `.content` is never read; `AssistantDoneEvent.message` (built from `output_content`) is what the loop persists, so the empty-input tool call reaches the next model turn.
3. **Replay serialization** — `_anthropic_transforms._content_blocks_to_anthropic` (`:109-145`) serializes only Text/Image/ToolCall; `ThinkingContent` is dropped, so even a captured signature never travels back.
4. **Provenance** — the adapter never stamps `AssistantMessage.api/provider/model`, so `_transform_messages._is_same_model` (`:144-148`) is always `False` → every prior thinking block is treated cross-model → signatures never preserved.
5. **Shared-transform routing** — the Anthropic adapter uses its own local `_anthropic_transforms.transform_messages`, bypassing the shared cross-provider `_transform_messages.transform_messages` (same-model thinking preservation, cross-model drop/convert, orphan-tool synthesis, errored-turn drop) that openai/google already route through.
6. **Compat detection** — no `getAnthropicCompat` equivalent (only `supports_temperature`); the owner named "Anthropic-side `model.compat` detection fields" as remaining scope.

**"allowEmptySignature" correction.** The owner's comment named `allowEmptySignature` as an Anthropic compat field. At pin 734e08e it is **not** a compat field — `getAnthropicCompat` returns exactly four auto-detected fields (below). The behavior the owner meant is pi's **unconditional inline replay downgrade** (`anthropic.ts:1069`): a same-model thinking block with an empty signature (e.g. an aborted stream) is emitted as a plain `text` block rather than `{type:"thinking"}` (which the API rejects). We implement it hard-coded, not compat-gated.

## Decision

Ship **B-lite** — the load-bearing thinking-replay core + a faithful 4-field compat *detection* module wiring only the one field with a real, low-risk consumer (session-affinity) — plus the independent Gemini `/login` polish.

### Core thinking-replay (mandatory)

- **Capture + index invariant** (`anthropic.py._translate_event`). Append a `ThinkingContent` at `content_block_start` for **both** `thinking` (`thinking="", thinking_signature=""`) **and** a new `redacted_thinking` branch (`thinking="[Reasoning redacted]", thinking_signature=<content_block.data>, redacted=True`). Add a `signature_delta` arm to `content_block_delta` that accumulates `delta.signature` and rebuilds `output_content[index]`; `thinking_delta` writes the rebuilt `ThinkingContent` back to `output_content[index]` preserving signature+redacted. This makes every block type append exactly one entry at start → Anthropic `index == output_content` position → the text/tool write-back guards fire again. Mirrors `anthropic.ts:527-545, 598-604`.
- **Provenance stamp** — set `api=model.api, provider=model.provider, model=model.id` on **both** the success output (`:369-375`) and the error output (`:422-427`), mirroring `openai_completions.py:1300-1308`.
- **Shared-transform routing** — `_anthropic_transforms.transform_messages(messages, model)` calls `_transform_messages.transform_messages(messages, model, normalize_tool_call_id=…)` **first**, then runs the existing per-shape map + tool_result coalescing over the returned list; `build_params` passes `model`. The normalize hook is pi's unconditional `re.sub(r"[^a-zA-Z0-9_-]", "_", id)[:64]` (`anthropic.ts:990-991`). Mirrors `openai_completions.py:325` / `_google_shared.py:57`.
- **Replay serialization** — a `ThinkingContent` branch in `_content_blocks_to_anthropic` implementing pi's exact 4-way order (`anthropic.ts:1056-1080`): `redacted → {type:"redacted_thinking", data: thinking_signature}`; `elif thinking.strip()=="" → skip`; `elif not thinking_signature → {type:"text", text: thinking}` (unconditional downgrade); `else → {type:"thinking", thinking, signature: thinking_signature}`.

### Compat detection (B-lite)

- **New `_anthropic_compat.py`** mirroring `_openai_compat.py`: a frozen `AnthropicMessagesCompat` with pi's **exact 4 fields** — `supports_eager_tool_input_streaming`, `supports_long_cache_retention`, `send_session_affinity_headers`, `supports_cache_control_on_tools`; `detect_compat(model)` computing `is_fireworks = provider=="fireworks"` and `is_cloudflare_ai_gateway_anthropic = provider=="cloudflare-ai-gateway" and "anthropic" in base_url`, yielding `send_affinity = fireworks or cfaig_anthropic`, the other three `= not fireworks` (`anthropic.ts:167-177`); `get_compat(model)` merging an explicit `model.compat` override with camelCase aliases, exactly like `_openai_compat.get_compat`.
- **Wire session-affinity only** — in the API-key client-build branch (`anthropic.py:287-297`), when `opts.session_id` **and** `get_compat(model).send_session_affinity_headers`, inject `{"x-session-affinity": opts.session_id}` into `default_headers`. Mirrors `anthropic.ts:862-863` and `openai_completions.py:1014-1017`. This is a plain header, **not** an `anthropic-beta` value, so it does not touch the delicate beta-CSV merge.

### Gemini `/login` polish (independent)

- `login_wizard._fetch_openai_model_ids`: for `google-*` apis, filter Gemini ListModels items to those whose `supportedGenerationMethods` includes `generateContent` (drops embedding/imagen/aqa), and follow `nextPageToken` until exhausted so all models list. (The optional-polish item deferred in ADR-0175 §Remaining.)

## Detected-but-deferred / out of scope (follow-up issue)

- **`supports_long_cache_retention` + `supports_cache_control_on_tools`** are **detected and override-mergeable but inert** — aelix's native Anthropic `build_params` emits **zero** `cache_control` breakpoints today (cache-control lives only in `openai_completions.py` for the OpenRouter passthrough). Wiring them requires porting pi's cache-control-breakpoint subsystem (`getCacheControl`, breakpoints on system/last-user/last-tool, 1h TTL) — filed as a separate follow-up.
- **`supports_eager_tool_input_streaming`** is detected but **not wired**: consuming it means either `eager_input_streaming:true` per tool param **or** the mutually-exclusive `fine-grained-tool-streaming-2025-05-14` beta, whose merge must not clobber the interleaved-thinking/OAuth betas under aelix's established "caller `anthropic-beta` wins" contract (ADR-0135). Deferred with the cache work.
- Newer-pi (post-734e08e; owner cited `6184307c`) detection-provider survey/seeding — post-pin drift, owner-gated.
- pi's `blocks.findIndex(b => b.index === event.index)` index-decoupling — the append-at-start invariant makes positional access correct; the findIndex refactor is optional robustness, deferred.
- OAuth-branch session-affinity, cloudflare base-URL rewriting, `to/fromClaudeCodeName` tool-name remap — orthogonal.

## Deliberate divergences from pi (documented)

1. **Session-affinity reaches cloudflare-ai-gateway anthropic models via aelix's generic API-key path** (aelix has no dedicated cloudflare client branch; pi injects the header only on its API-key branch and omits it on its cloudflare branch). Benign; documented.
2. **Tool-call-id normalization is newly applied to the Anthropic path** via the shared transform's `normalize_tool_call_id`. Native `toolu_…` ids are no-ops; only cross-model ids rewrite (and only on the not-same-model path), so the common same-model round-trip is untouched.
3. **Positional `output_content` + mandatory append** instead of pi's `findIndex` decoupling.
4. **Interleaved-beta scope** stays narrower per the pre-existing ADR-0135 "caller `anthropic-beta` wins" contract (unchanged here).
5. **`detect_compat` compares `provider` case-insensitively** (`.lower()`), consistent with `_openai_compat.detect_compat`, whereas pi's `getAnthropicCompat` compares `model.provider` case-sensitively (`anthropic.ts:169`). Effectively unreachable — catalog provider slugs are canonical lowercase — and the normalization is the more robust choice for a user-supplied custom provider.

## Landmines (for the implementer)

- **Provenance + routing are inseparable.** Routing through the shared transform while building output without the `api/provider/model` trio makes `_is_same_model` always `False` → all prior thinking treated cross-model → downgraded to text → signatures **never** replay → the feature silently no-ops with green streaming tests. Ship the stamp with the routing.
- **Two-part append invariant.** Append `ThinkingContent` at `content_block_start` for **both** `thinking` **and** `redacted_thinking`. Adding only `thinking` leaves the off-by-one on redacted turns. Failure mode is **silent data loss** (empty args / empty text), not a crash.
- **Empty-signature → text downgrade is still reachable after the shared transform** — the shared pass *keeps* a same-model non-empty thinking block with an empty signature (`_transform_messages.py:182-185`), so the replay branch must implement the unconditional downgrade; do not assume the shared pass stripped all unsigned thinking, and do not compat-gate it.
- **Coalescing runs after the shared pass.** `_anthropic_transforms` tool_result coalescing must map over the shared transform's **output** (it may insert synthetic tool results via orphan synthesis and drop errored/aborted turns).
- **Redacted payload lives in `thinking_signature`** (with `redacted=True`, `thinking="[Reasoning redacted]"`) — not a new field; replay reads it back as `data=thinking_signature`.
- **`content_index` on emitted events stays the raw Anthropic index** (== list position post-fix); do not switch to `len-1`.

## Verification

Full suite green via the project CLI (baseline ~4968) + `ruff` clean + `.venv/bin/pyright` on changed files (0 errors) — **not** injected editor diagnostics (historically spurious in this repo). New tests: an interleaved `thinking → tool_use` capture fixture asserting signatures + tool args survive (the exact currently-untested gap the latent bug hid in); a `redacted_thinking → text` capture fixture; a 4-way replay matrix (redacted-keep / empty-skip / empty-signature-downgrade / signed-keep) with same- vs cross-model routing round-trip; 4-field compat detection (fireworks / cloudflare-ai-gateway) + override merge; Gemini ListModels `generateContent` filter + pagination. All mock-based — live 1-turn Anthropic extended-thinking smoke is folded into **#61**.

## Follow-ups

- Cache-control-breakpoint subsystem (wires `supports_long_cache_retention` + `supports_cache_control_on_tools`) + eager/fine-grained tool-input streaming (wires `supports_eager_tool_input_streaming`) — new issue.
- Newer-pi detection-provider survey/seeding — owner-gated.
- Live extended-thinking smoke with a real Anthropic key — **#61**.
