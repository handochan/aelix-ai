# ADR-0176 — overflow-compaction follow-ups: trailing-error exclusion, staleness guards, compaction events (#4)

- **Status:** Accepted — LIVE. #391 amendment 2026-10-22 (the failed answer the recovery drops before its compaction is put back on every way out that does not re-run the turn, and the threshold check after it passes over that answer - review round 2; see the end).
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

Knowingly left (documented, non-blocking): a post-success `SessionCompactHookEvent` handler raise makes `compaction_end` report `result=None`/error though the compaction physically took effect (rare; semantically "compact() raised"); the unconditional first pop on the will_retry path is dead after `compact()`'s rebuild but kept pi-faithful (pop→compact→pop) — *2026-10-22 (#391): and where no rebuild and no re-run followed, it lost the failed answer; now put back, see the amendment*; `compact()` cancellation (`CancelledError`, a BaseException) bypasses the error emit and propagates untouched, so a cancelled compaction emits a `compaction_start` with no end (intentional clean-propagation trade-off).

## Verification

Full suite `python3 -m pytest tests/ -q` → **4506 passed / 0 failed / 1 skipped**; `ruff check` clean; `.venv/bin/pyright` on the 3 source files → **0 errors**. 9 new tests: FU1 real-compact second-pop for the error overflow (drives the REAL rebuild — the existing pop test stubs `compact()` so it never exercised it; confirms the session round-trip preserves `stop_reason`/`error_message`) + the length-overflow refinement; FU2 Guard A skip + fresh-fires contrast, Guard B skip + fresh-fires contrast; FU3 start/end on success, `compaction_end` on "Nothing to compact", `will_retry` on overflow. The `tests/pi_parity/test_assert_never_exhaustiveness.py` union-drift guard was updated to track the 2 new listener-only variants.

## Follow-ups

- TUI `_on_agent_event` "Compacting context…" indicator for the new events (auto/overflow compaction UI parity).
- pi #5877 `tokens_after` → `compaction_end.result` (with #34's post-compaction context display).

## Amendment (2026-10-22, #391) — a recovery that does not re-run keeps the failed answer

Pi reference: `earendil-works/pi@fdce48eb4`.

### What was measured on `c3f2b5bb`

FU1 keeps a first pop before the compaction: the failed answer leaves the live context, the
compaction rebuilds it from the session branch (which still holds the answer), and the second pop
drops it again before the re-run. When the compaction raised, nothing rebuilt the context and no
re-run came: it ended on the user's message while the session file ended on the answer. The real
CLI against a local Anthropic-Messages recorder that refuses the turn with HTTP 400 `prompt is too
long: 250000 tokens > 200000 maximum` (isolated HOME, agent dir, settings, session dir and cwd, a
fake key, `retry.maxRetries: 2`, `compaction.keepRecentTokens: 5`; `.omc/probes/b4-a-live/391/`):

- the session's first turn, so "Nothing to compact" (the issue): `-p` exit 0 with stdout and stderr
  empty; `--mode json` exit 0, its stream carrying the error's `message_end` and `compaction_end`
  `Nothing to compact`; `--mode rpc` `get_messages` ended on the prompt (one message);
- two seed turns, then the overflow with a summary that fails `502` past its retries: `-p` exit 0,
  stdout empty, stderr only #546's `Context overflow recovery failed: Error code: 502 …`; json
  exit 0; rpc `get_last_assistant_text` answered the PREVIOUS turn's `the answer is 42`;
- an extension's `session_before_compact` cancel: exit 0, nothing on stdout or stderr;
- an extension's `ctx.abort()` in `session_compact` (the compaction was written, the abort stopped
  the re-run; the second pop had run): exit 0, nothing;
- the TUI (a 120x40 pty) draws `✖ Error code: 400 … prompt is too long …` from the error's
  `message_end`, before and after this change;
- controls: the summary answers and the re-run answers, exit 0 with the answer; the re-run
  overflows again, exit 1 with the error (that path never popped).

pi omits the attempt durably before its compaction (`_omitRecoveryAttempt`: a `context_edit` with
no replacement, then `_refreshFinalizedContext`, `agent-session.ts:3072-3074`, `:1241-1257`);
`prepareCompaction` finding nothing returns `false` with no event (`:3141-3144`), and its print mode
judges `state.messages[length - 1]` (`print-mode.ts:141`), so pi ends the same run with no output
and exit 0 (read, not run). pi restores nothing.

### Decision

`_try_overflow_recovery` keeps the first pop (`_drop_failed_answer`) and puts the same message back
(`_restore_failed_answer`) on every way out that does not re-run the turn:

- `compact()` raised - "Nothing to compact", a summary that failed or was refused, an extension's
  cancel, a hook's or an auth callback's raise - or a `BaseException` came out of it (a cancelled
  prompt task; re-raised after the restore);
- an abort landed during a compaction that succeeded: checked after the second pop, under the
  prompt's claim, as the threshold check reads it. The prompt's own check after the call stays.

The answer goes back at the end, where it was (nothing is appended to the live context while a
compaction runs), unless the context already ends on it: a compaction that rebuilt the context and
then raised (a `session_compact` hook) left the branch's copy there - the same object, or an
overflow in its place - and a second copy is not added. The live context is then what the session
file holds, and ADR-0268 §1 judges the failed answer: `-p` and `--mode json` exit 1, text mode
writes the provider's error to stderr (after #546's line when the compaction failed), json's stderr
stays clean, rpc `get_messages` ends on it and `get_last_assistant_text` no longer offers the
previous answer. The helpers sit at the end of `harness/core.py`; the edited block keeps its line
count, so no locked citation of the file moves.

### Choices made under the decision (ADR-0235)

- **Diverges from pi on purpose.** pi's omission is durable in its session (the `context_edit`),
  aelix's drop was live only, so aelix's live context and its session file disagreed after a
  recovery that gave up; putting the answer back makes them agree and lets the exit-code rule see
  the failure.
- **Drop, then put back - not "never drop".** aelix's compaction reads the session branch, not the
  live context, so the first pop changes only what an observer sees during the compaction (an
  extension's handlers, a `compaction_end` subscriber). Never dropping would fix the same rows (the
  sabotage run below shows it: only the helpers' own test catches that mutant), but it changes what
  those observers see; pi's order is kept.
- **`aelix --help` stays silent on exit codes**, as pi's does; the getting-started guide's "Exit
  codes" section says it (ADR-0268, Not covered).
- **Not covered.** The auto-retry's cancelled backoff (`_handle_retryable_error`, an `abort_retry()`
  from rpc, Esc in the TUI, or an extension's `ctx.abort()` during the wait) drops a retriable error
  the same way and re-runs nothing: measured in-process, the live context ends on the prompt while
  the session ends on the `502` (`retry-cancel-probe.out` in the kit). pi does the same
  (`_prepareRetry` → `_omitRecoveryAttempt`); left for a follow-up. On the abort path
  `compaction_end` still says `will_retry: true` though no re-run follows (pi alike).

### Review round 2 (2026-10-22) — the threshold check passes over the overflow case

With the failed answer back, `prompt()`'s threshold check (`_check_auto_compaction`), which runs
after the overflow recovery in the same pass, read the restored error, took its error-path
estimate (the last good answer's usage plus the long prompt) and tripped the threshold: a second
compaction ran straight after the failed one - a second summary request with its own retries, a
second `session_before_compact`, and on `-p` a second line (`Auto-compaction failed: ...`) before
the provider's error. Measured with the real CLI (two seeds reporting 150000 of a 200000 window,
then a 240000-character prompt refused as too long, `keepRecentTokens` 5): on `c64bec3f` a 400
summary was asked twice and `--mode json` showed an `overflow` and a `threshold`
`compaction_end`; a 502 summary was asked 18 times in 8.5 s. pi's `_checkCompaction` takes an
overflow on the last answer as its case 1 or 2 and returns from it - a compaction without a
re-run, a compact-and-retry, the refusal of a second one, a failed compaction - so its threshold
case 3 never runs on that answer (`agent-session.ts:3040-3077` @ fdce48eb4).

aelix splits the two cases between `_try_overflow_recovery` and `_check_auto_compaction`; the
threshold check now passes over a last answer that is the overflow recovery's case
(`harness/core.py` `_overflow_case`: the same tests in the same order - another model's message
or #379's setup failure is not its case, then `is_context_overflow`). That covers the failed
compaction above, a silent overflow whose compaction failed (an earlier row asserted the second
attempt; it now asserts none) and a refused second recovery. After the fix the same run asks the
400 summary once, `-p` stderr has exactly the `Context overflow recovery failed: ...` line and
the provider's error, `--mode json` shows one `overflow` end, and the 502 summary is asked 9
times in 4.6 s (`.omc/probes/b4-a-live/r2/391-*`). A good answer past the threshold is still
compacted in its prompt. New rows in `tests/harness/test_overflow_recovery_gives_up_391.py`
(a 400 and a 502 summary, an extension's cancel asked once, the control); they fail on
`c64bec3f` (2 and 6 summary requests, the extension asked twice). Two Guard A rows in
`tests/harness/test_overflow_recovery.py` reported usage past the window, which made their answer
the overflow case's; they now report usage inside it, so they still test the guard.

### Tests and sabotage

`tests/harness/test_overflow_recovery_gives_up_391.py` (14 rows: nothing to compact, a summary 502
past its retries, a refused summary, a raising auth callback, an extension's cancel, a raising
`session_before_compact` and `session_compact` hook, an abort during a compaction that succeeds, a
cancelled prompt, a `BaseException`, a recovered re-run, a re-run that overflows again, and the two
helpers) and `tests/cli/test_print_mode_overflow_gives_up_391.py` (9: text and json for nothing to
compact, a failed and a cancelled compaction, a recovered turn, and a later prompt that answers).
On `c3f2b5bb` 17 of the 23 fail; the six that pass are five controls and the `session_compact` row,
which guards against a second copy. Twelve mutations on a throwaway worktree, each alone - each
piece reverted and wrong forms (a restore for an `Exception` only or for "Nothing to compact" only, no
abort check, a restore before the re-run too, an unconditional append, an identity-only guard, an
insert at the front, a drop that returns nothing or does not pop) - each turn a row red.
