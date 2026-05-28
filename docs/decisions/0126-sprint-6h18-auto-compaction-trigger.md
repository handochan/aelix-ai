# 0126. Sprint 6h₁₈ — Auto-compaction trigger (pi-faithful port, threshold-only v1)

Status: Accepted (6h₁₈ shipped)
Date: 2026-05-28
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

The TUI audit's only remaining HIGH item (#3 auto-compaction) — pi auto-runs
compaction after every turn when the context window is about to overflow, but
Aelix only had the manual `/compact` command (ADR-0117) and the
`auto_compaction_enabled` STATE flag with no trigger wired (deferred since Sprint
6h₂, see `core.py:478` "compaction/branch_summary/retry are deferred"). The flag
defaulted to `True` but was a no-op. Long sessions hit the context limit with no
auto-recovery.

This sprint is the **direct, pi-byte-faithful port of pi's `_checkCompaction`
threshold path** — the same kind of protected-core port that ADR-0117 (compaction
summarizer) already established. User explicitly approved this protected-core
change.

## Decision

Threshold-only v1 port of pi `_checkCompaction`. **One protected file
touched** (`harness/core.py`, ~50 LOC).

### Code changes

- `core.py:1049` — `prompt()` now calls `await self._check_auto_compaction()`
  after `_run()` returns. pi-equivalent site:
  `agent-session.ts:572-585 _processAgentEvent` invokes `_checkCompaction` after
  every `agent_end`. `_run` already reset `_phase` to "idle" in its finally, so
  `compact()` re-flips it to "compaction" exactly like manual `/compact`.

- `core.py:1293+` — new module constant + method after `compact()`:
  - `_AUTO_COMPACT_RESERVE_TOKENS = 16384` — pi `settings-manager.ts:681-683`
    default (`compaction.reserveTokens`). Aelix has no SettingsManager in core, so
    the reserve is a module constant with the same value.
  - `async def _check_auto_compaction(self) -> None` — pi `_checkCompaction`
    threshold path (`agent-session.ts:1766-1843`):
    1. Short-circuit on `auto_compaction_enabled` False / `model` None /
       `context_window` ≤ 0.
    2. Pick the last `AssistantMessage` from `_state.messages`.
    3. Branch on `stop_reason` (pi `agent-session.ts:1824-1840`):
       - error/aborted (or no assistant) → `estimate_context_tokens(messages)`
         (pi swaps to estimate because the assistant's `usage` isn't trustworthy
         on a failed turn).
       - normal → `calculate_context_tokens(last_assistant.usage)`.
    4. pi `compaction.ts:219-222 shouldCompact`:
       `context_tokens > context_window - reserve` → `await self.compact()`.

### Deferred to v2 (intentional)

- **Overflow path** (`agent-session.ts:1792-1815`): when the LLM itself returns a
  context-overflow error, pi re-runs the last turn via `agent.continue()` after
  auto-compacting. Threshold path catches the common case preemptively; overflow
  is a separate sprint with its own error-classification work.
- **`compaction_start` / `compaction_end` events**: pi emits these so the UI can
  show an "Auto-compacting…" spinner with Esc-to-cancel. v1 leverages `compact()`'s
  existing summary-stream output (ADR-0117) so the user still SEES the
  compaction happen in the transcript — the dedicated spinner is a polish item.
- Per-turn settings (`enabled`/`reserveTokens` overrides via a SettingsManager) —
  Aelix has no SettingsManager in core; constant is fine for v1.

## Consequences

- **ruff clean; pyright 0 errors** on `core.py` and `tests/test_compact.py`.
- **Tests**: 7 new unit tests in `tests/test_compact.py` (17 total in that file
  now pass) covering: flag disabled, no model, zero context_window, below
  threshold, above threshold via usage, error-turn → estimate path, and the
  integration through `prompt()` (auto-trigger fires after `_run`).
- **Protected core**: `harness/core.py` is the only file touched in
  `packages/aelix-agent-core`. `docs/contracts` byte-unchanged.
- **No regression**: the 10 existing `compact()` tests still pass; the flag
  default of `True` (pi parity) means existing sessions get auto-compaction
  automatically once context grows, with no API change.

## Code review (separate lane) — APPROVE-WITH-NITS → all HIGH/MEDIUM/LOW fixed

`code-reviewer`: port fidelity vs pi confirmed (`agent-session.ts:572-585`,
`1766-1843`, `1824-1840`; `compaction.ts:219-222`; `settings-manager.ts:681-683`);
phase machine consistent; error/InputHandled/busy short-circuits correctly skip
the auto-trigger. Two HIGH findings were **Aelix-specific failure modes** (not
pi-byte deviations) where the auto-trigger could turn a successful turn into a
propagated exception. All fixed:

- **[HIGH-1]** `compact()` raises `invalid_state` when `_session is None` (Aelix
  in-memory backward-compat per ADR-0022). FIXED: explicit `if self._session is
  None: return` short-circuit in `_check_auto_compaction`. New test
  `test_check_auto_compaction_no_op_when_no_session`.
- **[HIGH-2]** `compact()` raises `"Nothing to compact"` when
  `prepare_compaction` finds no viable cut (small kept-tail under
  `KEEP_RECENT_TOKENS=20_000`, no message entries, etc.) — surfaced as an
  unrelated exception after a successful turn. FIXED: narrow `try/except
  AgentHarnessError` matching `code=="invalid_state"` and message starts with
  `"Nothing to compact"` — swallow only that specific no-op case, propagate
  everything else (silent failure of a non-no-op compaction would mask real
  bugs). New tests: `test_check_auto_compaction_swallows_nothing_to_compact` +
  `test_check_auto_compaction_propagates_other_invalid_state_errors`.
- **[MEDIUM]** Test suite never exercised the real `compact()` path (all tests
  used a spy). FIXED: new `test_check_auto_compaction_real_compact_integration`
  letting the real pipeline run end-to-end + asserting the pi-shape "compacted
  into" summary marker appears in rebuilt `_state.messages` and a compaction
  entry was appended to the session.
- **[MEDIUM]** Auto-trigger must not fire on `InputHandled` short-circuit or
  busy-raise paths. FIXED: `test_check_auto_compaction_not_invoked_on_input_handled`
  + `test_check_auto_compaction_not_invoked_on_busy_raise`.
- **[LOW]** Class-level constant interleaved with methods. FIXED: moved
  `_AUTO_COMPACT_RESERVE_TOKENS` to module-level just before `class AgentHarness`.
- **[LOW]** Redundant `getattr` on a declared dataclass field. FIXED: direct
  `last_assistant.stop_reason` attribute access.

## Verification

- Pi-port fidelity: every branch + the threshold formula cite pi file:line in
  the inline comments (`agent-session.ts:572-585·1766-1843·1824-1840`,
  `compaction.ts:219-222`, `settings-manager.ts:681-683`).
- Tests: 23 in `tests/test_compact.py` (10 pre-existing + 13 new), all pass.
- Live smoke: TUI startup + normal turn — no regression. The threshold won't
  fire in a short live session (gpt-4o-mini's 128k window vs 16k reserve means
  ~112k tokens needed to trigger), but the call-chain runs after every turn.
