# ADR-0176 — overflow-compaction follow-ups: trailing-error exclusion, staleness guards, compaction events (#4)

- **Status:** Accepted — LIVE.
- **Date:** 2026-06-30
- **Sprint:** Moat chain — quick win (folded in after #36). Closes the three remaining #4 follow-ups; the #4 overflow body shipped in Batch 1 (ADR-0171).
- **Pi pin:** `earendil-works/pi@734e08e`. All three are faithful back-ports of pi `packages/coding-agent/src/core/agent-session.ts` (overflow body cites SHA 927e980; behaviour identical at 734e08e).
- **Relates:** ADR-0171 (#4 overflow body + #22 register_provider replay), ADR-0117 (compaction summarizer / rebuild), ADR-0126/0128 (threshold auto-compaction + auto-retry events — the AutoRetry* pair this mirrors). GitHub #4.

## Context

The #4 overflow-recovery machinery (detect provider context-overflow → compact → re-run) shipped in Batch 1, but three precise pi refinements were deferred. A read-only recon confirmed all three missing (zero `compaction_start`/`compaction_end` in the tree; no staleness/boundary guard; the post-rebuild error-exclusion absent).

## Decision

**FU1 — exclude the trailing overflow-error from the rebuilt re-run context** (pi `agent-session.ts:1988-1994`). `_try_overflow_recovery` already pops the error from the LIVE `_state.messages` before compacting, but `compact()` then rebuilds `_state.messages` from the session branch — which RE-ADDS the error (still persisted in history) as the last kept-tail entry, so the re-run replayed it. Add a SECOND pop AFTER the successful `compact(reason="overflow", will_retry=True)`, guarded on `isinstance(_state.messages[-1], AssistantMessage) and stop_reason == "error"` so a legitimate trailing message is never dropped. History keeps the error; only the live re-run context excludes it.

**FU2 — compaction-boundary staleness guards** (pi `:1781-1789` Guard A, `:1827-1835` Guard B). New `get_latest_compaction_boundary_ms(branch)` returns the latest compaction entry's timestamp in unix-ms (`None` = no compaction yet). Guard A (in BOTH `_check_auto_compaction` and `_try_overflow_recovery`): skip when the most-recent assistant predates the boundary — a stale pre-compaction usage/error must not re-trigger compaction on the first prompt after a compaction. Guard B (the error/aborted estimate branch of `_check_auto_compaction`): using the new `_EstimateResult.last_usage_index`, skip when the estimate's usage-source message predates the boundary.

> **aelix divergence (documented):** pi messages always carry a timestamp; aelix `AssistantMessage.timestamp` is `float | None` — live adapters leave it `None`, only session-rebuilt messages carry float ms. So `timestamp is None` is treated as **current / not-stale** (a freshly-generated overflow still recovers; only a rebuilt pre-boundary message with a float ms is skipped). Comparison is `<= boundary` (pi parity).

**FU3 — `compaction_start` / `compaction_end` subscriber events** (pi `:128-140` + emit sites). `compact()` emits `CompactionStartEvent(reason)` right after the phase flip and `CompactionEndEvent(reason, result, aborted, will_retry, error_message)` on BOTH the success path and an `except Exception` path (pi emits `compaction_end` even on the swallowed "Nothing to compact" raise, so threshold/overflow callers that swallow it still see a matched pair). These are **listener-only** events (via `_emit_to_subscribers`, NOT the hook bus), mirroring the AutoRetry* pair (ADR-0128): added to the `AgentEvent` union and to the `_to_hook_event` match as an unreachable raising case. `_emit_to_subscribers` swallows listener errors, so neither emit can break compaction or mask a body exception; `CancelledError`/`KeyboardInterrupt` (BaseException) bypass the error emit and propagate untouched. The `busy`/`no-session` guards raise BEFORE the start emit, so a rejected compact emits no orphan start.

**Scope:** events only — the TUI "Compacting context…" indicator is a deliberate fast-follow (auto/overflow compaction has zero UI surface today; wiring `_on_agent_event` belongs with TUI work, kept out of this protected-core diff). pi #5877 `tokens_after` (post-compaction estimate populating `compaction_end.result`) stays OUT, per the issue's explicit 3-item list (tied to #34).

## Adversarial review + fixes applied

Separate-context code-review verdict: **APPROVE-WITH-NITS** — all 8 attack points (FU3 exception handling, start placement, FU1 over-pop safety, FU2 over/under-suppression, NPE safety, Guard-B index, union exhaustiveness, pi parity) confirmed sound; 0 CRITICAL/HIGH. Three findings were applied:

- **Perf (LOW):** the FU2 boundary `get_branch()` ran on every turn. In `_try_overflow_recovery` Guard A now runs AFTER the `is_context_overflow` check (so the boundary read happens only when an overflow is actually present); in `_check_auto_compaction` the boundary + Guard A/B now run inside the threshold-tripped block (so a non-triggering turn pays no `get_branch()`). Both are behavior-preserving (the guards only ever *prevent* compaction).
- **Correctness (LOW-conf MED):** the FU1 second pop guarded on pi's narrow `stop_reason == "error"`, which missed a `stop_reason == "length"` overflow (aelix's `is_context_overflow` Case 3 — server-truncated oversized input — also reaches the will_retry path). The guard now uses `is_context_overflow(...)`, a deliberate refinement aligned with aelix's broader overflow detection. A legitimate trailing assistant is never an overflow, so it is never dropped. (+1 locking test.)
- **Cosmetic (LOW):** `_emit_to_subscribers` logged "auto-retry listener raised" for any listener; genericized to "subscriber listener raised" now that it also fans compaction events.

Knowingly left (documented, non-blocking): a post-success `SessionCompactHookEvent` handler raise makes `compaction_end` report `result=None`/error though the compaction physically took effect (rare; semantically "compact() raised"); the unconditional first pop on the will_retry path is dead after `compact()`'s rebuild but kept pi-faithful (pop→compact→pop); `compact()` cancellation (`CancelledError`, a BaseException) bypasses the error emit and propagates untouched, so a cancelled compaction emits a `compaction_start` with no end (intentional clean-propagation trade-off).

## Verification

Full suite `python3 -m pytest tests/ -q` → **4506 passed / 0 failed / 1 skipped**; `ruff check` clean; `.venv/bin/pyright` on the 3 source files → **0 errors**. 9 new tests: FU1 real-compact second-pop for the error overflow (drives the REAL rebuild — the existing pop test stubs `compact()` so it never exercised it; confirms the session round-trip preserves `stop_reason`/`error_message`) + the length-overflow refinement; FU2 Guard A skip + fresh-fires contrast, Guard B skip + fresh-fires contrast; FU3 start/end on success, `compaction_end` on "Nothing to compact", `will_retry` on overflow. The `tests/pi_parity/test_assert_never_exhaustiveness.py` union-drift guard was updated to track the 2 new listener-only variants.

## Follow-ups

- TUI `_on_agent_event` "Compacting context…" indicator for the new events (auto/overflow compaction UI parity).
- pi #5877 `tokens_after` → `compaction_end.result` (with #34's post-compaction context display).
