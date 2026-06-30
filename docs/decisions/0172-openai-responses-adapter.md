# ADR-0172 — openai-responses provider adapter (#15, Workflow A: dormant build)

- **Status:** Accepted (Workflow A landed dormant/unregistered; un-hide = Workflow B)
- **Date:** 2026-06-30
- **Sprint:** Backlog Batch 2 — #15 provider adapters, importance-ordered Tier 5
- **Pi pin:** `earendil-works/pi@734e08e`; this adapter ported faithfully from pi `main` SHA `927e98068cda276bf9188f4774fb927c89823388`.
- **Relates:** ADR-0047 (openai-completions adapter — the template), ADR-0114 (provider compat / max-token cap), ADR-0171 (#22 register_provider replay — the prerequisite). Backlog: GitHub #15 (parent #3); decision #8 (native, no litellm); #49 (the "N hidden — no adapter" report).

## Context

`openai-responses` was the unbuilt "D8" adapter behind the "(N model(s) hidden — their API has no adapter… openai-responses / Copilot gpt-5.x)" message: 81 catalog models (openai 42, opencode 16, cloudflare-ai-gateway 16, github-copilot 7) were hidden by `runnable_models.partition_runnable` because no adapter registered `api="openai-responses"`. Decision #8: implement it NATIVELY (no litellm). #22 (ADR-0171) made `register_provider` actually replay into the live registry, which is the prerequisite for a late-registered adapter to surface its models.

This is XL protected-core work, so it was done very carefully: read-only recon + design first (3-lens), then a staged build behind a 4-lens adversarial review, split so the adapter lands **dormant** (this ADR / Workflow A) and the one-line un-hide is a separate commit (Workflow B) with 1-line revert.

## Decision

Mirror pi's two-file split + helpers; keep the adapter **dormant** until Workflow B.

**Structure** (all under `packages/aelix-ai/src/aelix_ai/providers/`):
- `openai_responses.py` — thin adapter: `build_params` (input items, `store=False` ALWAYS, prompt_cache_key/retention, max_output_tokens, reasoning block, tools), `_open_responses_stream` via `client.responses.with_raw_response.create`, `stream_openai_responses` / sync `stream_simple_openai_responses`, `register_all()` (defined, **not called**).
- `_openai_responses_shared.py` — the engine: `convert_responses_messages` / `convert_responses_tools`, `process_responses_stream`, `encode/parse_text_signature_v1`, `map_stop_reason`. Reusable by future azure/codex adapters (pi's split).
- helpers: `_short_hash.py` (byte-faithful Math.imul, cross-verified vs pi's JS under Node), `_openai_prompt_cache.py` (64-codepoint clamp), `_openai_responses_compat.py` (`OpenAIResponsesCompat`, 3 flags), `_github_copilot_headers.py`.

**The three landmines, handled pi-faithfully:**
1. **Out-of-order reasoning (pi #6009)** — ALL stream state keyed by `event.output_index` in a `dict[int, slot]` (not arrival order); content blocks pushed at `output_item.added` (content_index frozen), deltas routed by output_index.
2. **Encrypted-reasoning roundtrip (pi #5114)** — request sets `include=['reasoning.encrypted_content']` only on the reasoning branch; `output_item.done` stores the **full** ResponseReasoningItem (incl `encrypted_content`) as JSON into `thinking_signature`; `convert_responses_messages` replays it verbatim next turn. **Serialization OMITS `None` keys** (`model_dump(exclude_none=True)` + dict filter) to match pi's `JSON.stringify` undefined-omission — otherwise `content:null`/`status:null` trigger an OpenAI 400 (caught by the 4-lens review; a real-pydantic-item test guards it, since plain-dict tests bypass `model_dump`).
3. **github-copilot reasoning-off exclusion** — the 7 copilot gpt-5.x models are excluded from the `reasoning:{effort:off}` branch (`model.provider == 'github-copilot'` gate, pi parity).

**Decisions:** service-tier cost multipliers DROPPED in v1 (aelix keeps the token-dict usage convention, cost resolved downstream — divergence documented in the module docstring; current catalog has no non-default tier). reasoning_summary defaults `'auto'`. Abort is best-effort/post-hoc (the Python `openai` SDK has no AbortSignal binding — matches `openai_completions.py`; pi forwards it but Python cannot). openai pin raised to `>=1.66` (Responses GA, `client.responses`).

**Protected-core additive fields** (frozen, defaulted — safe): `TextContent.text_signature: str = ''`, `AssistantMessage.response_id: str | None = None`, `Usage.reasoning: int = 0`. `ThinkingContent.thinking_signature` reused to hold the full reasoning-item JSON (semantic overload vs completions, documented).

## Verification

Gate **4345 collected / 4344 passed / 0 failed / ruff clean / project-pyright 0 errors** on the 6 new files (+~100 tests). short_hash byte-perfect vs pi's JS (Node) incl. surrogate-pair emoji + >64 truncation; SDK signature confirmed to accept store/reasoning/include/prompt_cache_key/max_output_tokens (and reject prompt_cache_retention → relocated to extra_body). 4-lens adversarial review: 1 HIGH (serialization `null` injection) + 1 MEDIUM (abort) + 2 LOW, all resolved. Adapter confirmed **dormant** (no `register_all` in runtime_bootstrap; guarded by `test_importing_adapter_does_not_register_it`).

## Follow-ups

- **Workflow B (next):** un-hide — add `openai_responses.register_all()` to `runtime_bootstrap.register_providers()` (the one line that surfaces the hidden models), verify the counts, confirm cloudflare-ai-gateway + opencode env-key auth, and run a real 1-turn live smoke (fixtures/SDK-signature cannot catch a live-endpoint shape difference).
- azure-openai-responses / openai-codex-responses adapters (reuse the shared engine).
- service-tier cost handling downstream (if a non-default-tier model enters the catalog).
- native **gemini** adapter — the remaining half of #15 (separate batch).
