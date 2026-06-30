# ADR-0171 — Backlog Batch 1 (Tier 2/3): register_provider replay, compat-metadata, overflow compaction, extension-dev

- **Status:** Accepted
- **Date:** 2026-06-30
- **Sprint:** Backlog Batch 1 — importance-ordered Tier 2/3 lanes (3-lane concurrent build)
- **Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`; parity references fetched at pi `main` (`927e98068cda276bf9188f4774fb927c89823388`).
- **Relates:** ADR-0143 (extensions-api / protected-core budget), ADR-0093 (`harness.reload`), ADR-0114 (provider compat / max-token cap), Phase 0 (commit `b81670b`). Backlog: GitHub #22, #36, #4, #5 (parent #3). Decisions: #8 (provider strategy), #52→#14 fold.

## Context

After Phase 0 (`b81670b`) the backlog was re-prioritised by **importance, not effort** (3-lens judge panel → "floor → moat"). The project owner selected Tier 2/3 for the next build, deferring the loop governor (#14, core-sensitive, design-first) and multi-agent (#16, watch pi), and made two decisions:

- **#8 — provider strategy:** response-API providers are implemented **natively** (no `litellm`). Closed.
- **#52 — Qwen3.6 text tool-call non-parse:** deferred (not currently reproducing) and **folded into #14's expanded scope** — the governor will handle *any* model emitting tool/think format embedded in content text, not a Qwen-specific parser.

**Overriding principle:** where a feature exists in pi, match pi parity first.

## Decision

Three lanes, built concurrently, edited in a conflict-safe order, with one integrated gate + adversarial review (+ a fix pass for the review findings).

### #22 — register_provider replay into the live ModelRegistry (Lane A; gates #15)
`ExtensionAPI.register_provider` only queued onto `_ExtensionRuntime.pending_provider_registrations`; the existing `bind_model_registry` (Sprint 5a, `api.py`) had **no caller**, so extension/custom providers silently never resolved. Fix: call `harness.runtime.bind_model_registry(model_registry)` at the single bootstrap point in `entry._harness_factory` (re-run safely on each harness rebuild; idempotent). Mirrors pi `ExtensionRunner.bindCore` `runner.ts:344-377` (flush `pendingProviderRegistrations` + immediate-effect rebind). This is a **prerequisite for #15** (new adapters register via the provider registry).

### #36 — explicit compat metadata over provider-name heuristics (Lane A; partial)
`_openai_compat`/`openai_completions` plumbed `compat.send_session_affinity_headers` but never **read** it. Now the OpenAI-completions adapter assembles the session-affinity header trio from resolved `model.compat` (gated on a cache session id, `cache_retention != "none"`), letting user-defined custom providers behave correctly without matching a built-in name. Mirrors pi `openai-completions.ts createClient` `:502-535`. Anthropic-side compat fields + newer detection providers are **follow-up** (out of cited scope).

### #4 — overflow-driven auto-compaction recovery (Lane B; the real body of #4)
`_check_auto_compaction` implemented only the threshold path; the LLM context-overflow re-run was "deferred to v2". Added `aelix_ai/utils/overflow.py` (`is_context_overflow` + patterns) and an outer recovery loop in `prompt()` (`_try_overflow_recovery`, per-turn `_overflow_recovery_attempted` guard) that detects a provider context-overflow error, compacts with `reason="overflow"` / `will_retry`, and re-runs — with pi's guards (no infinite retry, already-completed-response skip per pi #5720, nothing-to-compact safety per #4811). `_is_retryable_error` now **excludes** context-overflow (pi `agent-session.ts:2486`) so overflow routes straight to compact-and-retry instead of burning the auto-retry budget.

### #5 — extension development actually usable (Lane C)
(1) Filled the throwing `_ExtensionRuntime` stubs `exec` / `get_all_tools` / `get_commands` via the `bind_core` method table (pi `runner.ts` semantics). (2) Project Trust extension surface: `ctx.is_project_trusted()` (wired end-to-end), plus the `project_trust` decide/defer event-walk and `defaultProjectTrust` **mechanism** (pi v0.79.0-1, #5332/#5523) — implemented and tested.

## Divergences from pi

- **#22:** `bind_model_registry` uses `contextlib.suppress` for containment where pi `bindCore` calls `emitError` (follow-up); pi's "immediate-effect" rebind is achieved in aelix because, post-bind, `register_provider`'s best-effort fan-out targets the now-real `runtime.model_registry`.
- **#5 (bootstrap):** the `project_trust` event + `defaultProjectTrust` are **not yet invoked by the production bootstrap** — `entry._resolve_project_trust` does not pass `extensions=`/`default_project_trust=` (needs user/global extensions loaded *before* trust resolution + a `SettingsManager` source, tied to #44). Docstrings were corrected to state this honestly; `ctx.is_project_trusted()` is the one item wired end-to-end.
- **#36:** partial — only the OpenAI-compat session-affinity consumption landed.

## Follow-ups

- #22 `emitError` parity for replay failures; extension hot-unload provider cleanup (with #24/#53).
- #4 exclude the trailing overflow-error entry from the rebuilt re-run context; compaction-boundary staleness guard (pi `agent-session.ts:1827-1835`); dedicated `compaction_start/end` subscriber events (pi #5962).
- #5 production-bootstrap wiring of `project_trust` + `defaultProjectTrust` (with #44); `ctx.ui.select` inside the event.
- #36 Anthropic-side compat fields + newer detection providers.

## Verification

Gate **4242 collected / 4241 passed / 1 skipped / 0 failed**, **ruff clean**, **pyright 0 errors** on changed source (+ test files). Baseline 4170 → +72 tests. Adversarial review: no critical/high; 1 medium (#5 docstring overstatement) + 1 low (#4 retryable/overflow overlap), both resolved in the fix pass. New pyright ✘ from the batch resolved with scoped, behavior-preserving ignores (verified pi's `ModelRegistry` has no `getModels`).
