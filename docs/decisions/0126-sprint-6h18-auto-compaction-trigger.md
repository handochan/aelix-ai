# 0126. Sprint 6h₁₈ — Auto-compaction trigger (pi-faithful port, threshold-only v1)

Status: Accepted (6h₁₈ shipped) — #334 amendment 2026-09-25 (the check runs nested under the prompt's claim; see Code changes) — #469 amendment 2026-10-22 (the threshold, the cut and `enabled` read settings.json `compaction`; see the end)
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
  **#334 amendment 2026-09-25:** no longer true. `_run` no longer sets the
  phase idle — the prompt holds its claim until it returns — and the threshold
  compaction runs *nested* under that claim (`compact(_claim=…)`): phase
  `"compaction"` while it runs, `"turn"` again after, the idle event clear
  throughout; manual `/compact` keeps the idle guard. The check is also skipped
  after an `abort()` (pi's `_agentRunAbortRequested`). See ADR-0023's #334
  amendment.

- `core.py:1293+` — new module constant + method after `compact()`:
  - `_AUTO_COMPACT_RESERVE_TOKENS = 16384` — pi `settings-manager.ts:681-683`
    default (`compaction.reserveTokens`). Aelix has no SettingsManager in core, so
    the reserve is a module constant with the same value. *(2026-10-22, #469: the
    premise had expired - the harness has had one since issue 44; the constant is
    now the fallback for `compaction.reserveTokens`, see the amendment.)*
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
  Aelix has no SettingsManager in core; constant is fine for v1. *(Done 2026-10-22,
  #469, with `keepRecentTokens` - see the amendment.)*

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

## Amendment (2026-10-22, #469) — the threshold, the cut and `enabled` read settings.json's `compaction`

Pi reference: `earendil-works/pi@fdce48eb4`.

### What was measured on `3ed47995`

The decision above kept the reserve a module constant because "Aelix has no SettingsManager in
core"; ADR-0128's #408 amendment found that premise expired (the CLI's one `SettingsManager`
reaches every harness it builds, `settings_manager=`) and made the retry read `retry.*`, leaving
`compaction.*` unread. Nothing read it: `_check_auto_compaction` compared against
`_AUTO_COMPACT_RESERVE_TOKENS`, `prepare_compaction` cut with `KEEP_RECENT_TOKENS` (20000), both
automatic compactions and rpc `get_state` read only `AgentState.auto_compaction_enabled`, and rpc
`set_auto_compaction` set only that. The real `aelix --mode rpc`, a local Anthropic-Messages
recorder (window 200000), isolated HOME, agent dir, settings, session dir and cwd, a fake key
(kits and outputs: `.omc/probes/b4-a-live/469/`):

- `compaction.keepRecentTokens: 20`, three short turns, then `compact`: `Nothing to compact` -
  the owner's 2026-10-21 measurement - the same answer as with `{}`;
- `reserveTokens: 60000` with every answer reporting 150000 input tokens: no compaction after
  any of three turns (the constant's threshold is 183616);
- `enabled: false` with answers at 190000: a threshold compaction ran (and a compaction entry
  was written), and `get_state` said `autoCompactionEnabled: true`;
- `enabled: false` and a `prompt is too long` 400 on the third turn: overflow compaction and a
  re-run, as with no setting;
- `set_auto_compaction false`: `success: true`, `autoCompactionEnabled: false`, the settings
  file untouched.

### Decision

pi reads `settingsManager.getCompactionSettings(model)` wherever it decides
(`agent-session.ts` `_exceedsCompactionThreshold` `:768-775`, `compact` `:2794-2810`,
`_checkCompaction` `:2977-2983`, `_runAutoCompaction` `:3127-3142`); aelix now reads settings.json
`compaction` at the same points through `_compaction_policy` (`harness/core.py`, at the end of the
module so that no locked citation of the file moves), #408's pattern:

- `compaction.enabled` gates the threshold check and the overflow recovery (pi returns before
  both when `!settings.enabled`, `:2982-2983`). pi's `autoCompactionEnabled` getter and
  `setAutoCompactionEnabled` setter ARE the setting (`:3279-3286`):
  `AgentHarness.auto_compaction_enabled` returns the settings value when the settings carry a
  boolean there, else the harness's toggle; `set_auto_compaction_enabled` (rpc
  `set_auto_compaction`) calls `SettingsManager.set_compaction_enabled` first, which writes the
  GLOBAL settings.json, then sets the toggle. A project `compaction.enabled` still wins in the
  merged view, as in pi. `set_compaction_enabled` now replaces a global `compaction` that is
  not an object (it raised `AttributeError` on `"compaction": true`), as #408 did for `retry`.
- `compaction.reserveTokens` sets the threshold: `context_tokens > context_window -
  reserveTokens` (pi `shouldCompact`, `compaction.ts:267-270`).
- `compaction.keepRecentTokens` is the cut's budget for every compaction - manual, threshold
  and overflow, all of which run `compact()`. `prepare_compaction` gains pi's second argument as
  the keyword `settings=` (an `aelix_ai.settings.types.CompactionSettings`; pi
  `prepareCompaction(pathEntries, settings)`, `compaction.ts:859-923`) and the preparation carries
  it as `settings`, pi's `preparation.settings`, which a `session_before_compact` handler reads:
  the three values resolved (`enabled` as the harness answers it). Without `settings`, or with
  its `keep_recent_tokens` `None`, `KEEP_RECENT_TOKENS` applies as before.
- **Fallbacks.** A value the settings do not carry falls back to `_AUTO_COMPACT_RESERVE_TOKENS`
  or `session.compaction.KEEP_RECENT_TOKENS`, read at that moment (#408's reason: a test that
  monkeypatches a constant keeps governing a harness whose settings are `{}`). Read at each
  decision, so a `/reload` applies to the next check or compaction.

### Choices made under the decision (ADR-0235)

- **A value pi refuses is ignored, not raised.** pi's `getCompactionTokenSetting`
  (`settings-manager.ts:833-859`) throws `Invalid compaction.<key> setting: … Expected a
  non-negative safe integer` for anything else; a raise inside aelix's post-turn check would fail
  the turn that just succeeded (#408's rule). So a string, a boolean, `null`, a container, a
  negative or fractional number, NaN, an infinity or a number above 2^53 - 1 falls back to the
  default; `20000.0` counts as `20000` (the number JavaScript reads). `0` is a value (pi accepts
  it): `keepRecentTokens: 0` keeps only the last message, `reserveTokens: 0` compacts only past
  the window. A non-boolean `enabled` and a `compaction` that is not an object are ignored.
- **Not covered.** pi's per-model `compaction.modelOverrides` (`"provider/id": {reserveTokens,
  keepRecentTokens}`, newer than the settings types aelix ported) is not modelled: the loader
  drops the key. `branchSummary.reserveTokens` and `branchSummary.skipPrompt` are still not read
  (aelix's branch summary serializes the whole branch, no token-budget walk). The TUI's `/context`
  imports `_AUTO_COMPACT_RESERVE_TOKENS` for its `autocompact buffer` row and so still shows
  16384 (`tui/commands.py`, another lane's file in this batch). The `aelix-server` WebSocket
  harness has no settings manager and keeps the constants and the in-memory toggle (ADR-0128's
  reason).

### Tests and sabotage

`tests/harness/test_compaction_settings_469.py` (45 rows: the reader and its fallbacks and wrong
types, each key moving the threshold or the cut both ways, `0`, a settings change between checks,
`enabled` on the threshold and the overflow paths and its precedence over the toggle, the getter,
rpc `get_state`, rpc `set_auto_compaction` writing the global file, a non-object block, the
project's precedence, a failed write, the preparation's `settings`, `prepare_compaction` with and
without `settings`). On `3ed47995`, with a shim for the reader the base lacks, 21 fail; the 24
that pass are controls (no manager, `{}`, the wrong-type rows that expect the defaults, the toggle
without a setting). Twenty-five mutations on a throwaway worktree, each alone - each piece reverted
(the threshold, the two gates, the getter, the setter's write, the write's order, `compact()`
passing no settings, the cut ignoring them, the preparation not carrying them,
`set_compaction_enabled`'s old `is None`) and wrong forms (no type check, a boolean or a negative,
fractional or unsafe count accepted, every float refused, a truthy `enabled`, literal fallbacks, a
falsy count falling back in the reader or the cut, a policy read once and cached, attribute access
on a non-object block, the toggle having to agree with the setting, the preparation's `enabled`
from the toggle, the harness reading `get_compaction_settings()`) - each turn at least one row red.
The same rpc runs on this tree: `compact` succeeds with `keepRecentTokens: 20` (the kept tail
starts at the last prompt), `reserveTokens: 60000` compacts after the second turn, `enabled:
false` compacts neither on the threshold nor on the overflow and `get_state` says `false`,
`set_auto_compaction false` writes `{"compaction": {"enabled": false}}`; the `{}` controls behave as
on the base.
