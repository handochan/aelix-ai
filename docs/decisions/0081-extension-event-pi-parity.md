# 0081. Sprint 6h₅a Phase 4.14 — Extension event Pi parity (4 events + `session_cwd` helper + W5 P-355 dispose ordering correction)

Status: Accepted (Sprint 6h₅a / Phase 4.14 / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₄c (ADR-0079 / ADR-0080) CLOSED the Phase 4 RPC roster at
29 / 0 / 29. The remaining Pi parity gaps were runtime / extension
polish items (P-307 / P-308 / P-314 / P-315 + `assertSessionCwdExists`
+ `previousSessionFile` / `sessionStartEvent` tracking + Pi `forkFrom`
cross-cwd + `setup` callback + `import_from_jsonl` body + Pi HTML
visual fidelity + `_get_context_usage_safe` + live `session_id` read +
`ImageContent` rendering) — none of which require RPC dispatch
changes.

Sprint 6h₅a (Phase 4.14) closes the **extension session lifecycle
event** subset of that carry-forward roster: P-307 `session_shutdown`
emit + P-308 real cancel hooks + cwd validation + previous-session-file
tracking + session-start event. The 4 new Pi extension events
(`session_start` / `session_before_switch` / `session_before_fork` /
`session_shutdown`) wire end-to-end on top of the 6h₄c
`AgentSessionRuntime` foundation, the `_finish_session_replacement`
3-step waveform, and the existing `HookBus` / `ExtensionRunner`
bridge. No RPC dispatch changes — counts stay 29 / 0 / 29.

W4 (code-review opus) + W5 (architect opus Pi parity audit) returned
**1 BLOCKING (P-355) + 6 MUST-FIX W5 items + 4 W4 MINOR cleanups**.
Sprint 6h₅a W6 applied all of them.

## Decision

### Pi parity decisions (P-332 ~ P-355)

- **P-332 — `HookEventName` widening to 35 names + 4 event
  dataclasses + 2 result types.** The 4 Pi extension events
  (`session_start` / `session_before_switch` / `session_before_fork` /
  `session_shutdown`) join the existing 31-name closed `Literal` union
  alongside their `@dataclass(frozen=True)` event payloads and the
  cancel-aware `SessionBeforeSwitchResult` / `SessionBeforeForkResult`
  result types. The 4 new events fold into `AgentHarnessEventName`
  (Pi sources them from `harness/extensions/types.ts`, not the
  `agent-harness.ts` own-event family, but Aelix folds both into a
  single ergonomic alias — same pattern as the Sprint 5a 3
  coding-agent events).

- **P-333 — `ExtensionRunner.emit` / `has_handlers` HookBus bridge.**
  `ExtensionRunner` already owned an `emit` / `has_handlers` shape
  injected via `_emit` / `_has_handlers` Optional callables at harness
  construction. Sprint 6h₅a wires the 4 new events through the same
  bridge — no new code path needed; the bus dispatches them via the
  reducer registry (shared `_reducer_session_before` for the 2
  cancellable events, `_reducer_observational` for the 2 lifecycle
  events).

- **P-334 — `_emit_session_shutdown_event` module-private helper.**
  Mirrors Pi's top-level `emitSessionShutdownEvent` export
  (`runner.ts:177-189`). Gated on
  `extension_runner.has_handlers("session_shutdown")` to avoid
  constructing the payload when no extension cares. Accepts the
  runner as `Any` to avoid the circular import via `harness.core`.

- **P-335 — `_reducer_session_before` widening.** The shared reducer
  for `session_before_compact` / `session_before_tree` accepts the
  2 new result types (`SessionBeforeSwitchResult` /
  `SessionBeforeForkResult`) so first-cancel-wins semantics propagate
  uniformly. Pi `runner.ts:680-712` is the citation.

- **P-336 — `ExtensionAPI.on` / `HookBus.on` overload count = 35.**
  4 new `@overload` declarations land on top of the Sprint 5a
  31-overload baseline. Closure-pin invariant uses
  `typing.get_overloads(...)` to count.

- **P-337 — `session/session_cwd.py` Pi port** of
  `packages/agent/src/harness/session/session-cwd.ts:1-59`. Aelix
  divergence: helper functions are `async` because the Aelix
  `FileSystem` Protocol is all-async (Pi uses sync `existsSync`).
  Wired into `switch_session` AFTER `repo.open` so the assertion
  exercises the NEW session's cwd (Pi line `:186`). The Pi factory
  bootstrap site (`:391`) and `importFromJsonl` site (`:352`) defer
  to Sprint 6h₅c (Aelix factory pattern P-302 — bootstrap site is
  different from Pi).

- **P-338 — Real `_emit_before_switch` body.** Pi
  `agent-session-runtime.ts:115-130` — gated on
  `has_handlers("session_before_switch")`, emits
  `SessionBeforeSwitchHookEvent`, returns `True` when ANY handler
  returned `SessionBeforeSwitchResult(cancel=True)`. **W4 MINOR-3**
  applied: parameters `reason` / `target_session_file` are required
  (no defaults) so every callsite supplies the Pi-shape explicitly.

- **P-339 — Real `_emit_before_fork` body.** Pi
  `agent-session-runtime.ts:132-147` — same cancel-aggregation
  semantics. Parameters `entry_id` / `position` required.

- **P-340 — `_teardown_current` ORDERING CORRECTION** to match Pi
  `agent-session-runtime.ts:149-157`. The Sprint 6h₄b implementation
  reversed Pi's order (invalidate-then-dispose with NO shutdown emit).
  Pi order:

  1. emit `session_shutdown` (extensions still see live harness
     state — last messages, current `session_file`, etc.).
  2. `before_session_invalidate?.()` (signals invalidation).
  3. `await harness.dispose()` (tears down HookBus + everything).

  **Race avoidance:** the `extension_runner` reference is captured at
  the TOP of the method BEFORE `harness.dispose()` is awaited (dispose
  tears down the HookBus → bridge becomes a no-op after).

- **P-341 — `dispose` adds missing `session_shutdown` emit with
  `reason="quit"`.** Pi `agent-session-runtime.ts:366-373` — Pi calls
  `emitSessionShutdownEvent` BEFORE `beforeSessionInvalidate` BEFORE
  `session.dispose`. Aelix `AgentHarness.dispose()` was missing the
  shutdown emit entirely.

- **P-342 — `previous_session_file` snapshot timing.** Pi reads
  `this.session.sessionFile` AT THE TOP of each replace API
  (`switch_session` line 184, `new_session` body, `fork` body) so the
  value comes from the OLD session. Aelix mirrors verbatim: snapshot
  captured BEFORE `_teardown_current` at all 3 sites, threaded into
  `_finish_session_replacement` for the `session_start` payload.

- **P-343 — `session_start` emit from `_finish_session_replacement`.**
  Pi emits `session_start` on the NEW harness's runner AFTER
  `rebind_session` (the OLD bus is disposed by step 1). Aelix mirrors:
  read `self._harness.extension_runner` AFTER `_apply` +
  `rebind_session?.()` to pick up the freshly constructed runner. The
  first `session_start` at bootstrap (`reason="startup"` /
  `"reload"`) is deferred to Sprint 6h₅b (factory pattern change
  required).

- **P-344 (W5 BLOCKING FIX) — Pi line citation corrections** verified
  at SHA `734e08e`. The Sprint W0 draft cited
  `extensions/types.ts:510-557` (an ambiguous span). W5 audit pinned
  the actual line ranges:

  - `SessionStartEvent`         → `extensions/types.ts:513-519`
  - `SessionBeforeSwitchEvent`  → `extensions/types.ts:522-526`
  - `SessionBeforeForkEvent`    → `extensions/types.ts:529-533`
  - `SessionShutdownEvent`      → `extensions/types.ts:552-557`

  Citations now consistent across `harness/hooks.py` event
  docstrings, the runtime module docstring, the closure pin
  (`tests/pi_parity/test_phase_4_14_extension_events.py`), and the
  W0 fixtures (`pi_extension_events_734e08e.json` +
  `pi_agent_harness_event_names_734e08e.json`).

- **P-345 (W5 BLOCKING FIX) —
  `SessionBeforeForkResult.skip_conversation_restore` Pi parity
  field.** Pi `extensions/types.ts:1015-1022`:

  ```typescript
  interface SessionBeforeForkResult {
      cancel?: boolean;
      skipConversationRestore?: boolean;
  }
  ```

  Aelix's Sprint 6h₅a W0 draft shipped only `cancel`. The field is
  re-added with default `None` (Pi: omitted/undefined). Aelix does
  not currently CONSUME the field (fork-restore semantics deferred
  to Sprint 6h₅b), but the dataclass shape MUST match Pi so
  extensions written against Pi typings compile against Aelix.

- **P-346 (W5 BLOCKING FIX) — `format_missing_session_cwd_error`
  Pi-verbatim format.** Pi `session-cwd.ts:30-37`:

  ```text
  Stored session working directory does not exist: <session_cwd>
  Session file: <session_file>            # only when sessionFile is set
  Current working directory: <fallback_cwd>
  ```

  The Sprint 6h₅a W0 draft used a non-Pi format
  (`"Session working directory does not exist: ..."` +
  `"Session file: ..."` + `"Fallback cwd: ..."` with a conditional
  third line). Forward-fix: header rewritten to Pi-verbatim,
  third line made unconditional (`Current working directory`), and
  the `SessionCwdIssue` field shape was changed to match Pi —
  `session_file: str | None` (optional; Pi
  `string | undefined`) and `fallback_cwd: str` (required
  non-optional; Pi `fallbackCwd: string` — Pi format ALWAYS
  renders it). The `get_missing_session_cwd_issue` helper returns
  `None` when the caller passes `fallback_cwd=None` (cannot
  construct the Pi-shape diagnostic).

- **P-347 (W5 BLOCKING FIX) — `format_missing_session_cwd_prompt`
  Pi port.** Pi `session-cwd.ts:40-42` ships a TUI confirmation
  prompt rendered when the user is offered the option to continue
  in the current cwd:

  ```text
  cwd from session file does not exist
  <session_cwd>

  continue in current cwd
  <fallback_cwd>
  ```

  Aelix's Sprint 6h₅a W0 draft omitted this helper entirely. W6
  ports it to `session/session_cwd.py` + exposes via `__all__` +
  unit-tests under `tests/session/test_session_cwd_helper.py`. No
  call site wires it yet (Aelix has no TUI surface today); the
  helper is included for extension authors and Sprint 6h₅+ TUI
  work.

- **P-348 ~ P-354 — minor structural items.** These cover the
  `AgentHarnessEventName` alias widening to 22 names + the
  `Session.session_file` property (already shipped in Sprint 4b
  but pinned here for cross-reference) + the `_TOOL_CALL_*` lookup
  tables continuing to compile after the `HookEventName` literal
  widening + the closure pin overlay on top of the Sprint 5a
  `tests/pi_parity/test_phase_3_1_strict_superset.py` (35-name
  cascade) + the Pi parity fixture `pi_extension_events_734e08e.json`
  metadata block (Pi SHA + 4 line citations + reducer pin + shared
  reducer citation + emit-helper citation + AgentSessionRuntime
  member citations + overload count after vs before).

- **P-355 (W5 BLOCKING FIX — dispose ordering correction).** The
  Sprint 6h₅a W2 implementation shipped `dispose` in the order
  **INVALIDATE → EMIT → DISPOSE** based on a spec §J misread of Pi
  `agent-session-runtime.ts:366-373`. The spec text claimed an
  "intentional asymmetry" between `_teardown_current` (EMIT →
  INVALIDATE → DISPOSE) and `dispose` (INVALIDATE → EMIT → DISPOSE)
  citing the difference in Pi behavior. **W5 verified at SHA
  `734e08e`: no such asymmetry exists in Pi.** Pi `dispose` body:

  ```typescript
  async dispose(): Promise<void> {
      await emitSessionShutdownEvent(this.session.extensionRunner, {
          type: "session_shutdown", reason: "quit",
      });
      this.beforeSessionInvalidate?.();
      this.session.dispose();
  }
  ```

  Pi emits FIRST, then invalidates, then disposes — same order as
  `teardownCurrent`. W6 forward-fix: `dispose` rewritten to **EMIT
  → INVALIDATE → DISPOSE**, matching `_teardown_current`. The
  runtime module docstring and the dispose docstring explicitly
  document the W2 misread + correction so any future PR reading the
  history doesn't reintroduce the asymmetry.

  The W5 audit re-derived this from the Pi source verbatim and
  raised P-355 as BLOCKING because the asymmetry (a) doesn't exist
  in Pi and (b) means handlers reading
  `harness.session.session_file` in a `session_shutdown(quit)`
  handler would have observed a post-invalidate harness, not the
  live one. Forward-fix preserves Pi parity AND the documented
  invariant ("extensions still see live harness state in
  `session_shutdown`").

### W4 MINOR cleanups applied this sprint

- **W4 MINOR-1** — `_emit_session_shutdown_event` helper is
  module-private (not exported). Closure pin asserts only via
  internal module import.
- **W4 MEDIUM (switch_session order)** — `switch_session` lifts the
  `repo.open` + `assert_session_cwd_exists` call BEFORE
  `_emit_before_switch` to match Pi `agent-session-runtime.ts:184-189`
  ordering. Pi asserts cwd before letting any extension cancel the
  swap so the error surfaces even when an extension would have
  cancelled. The W2 implementation emitted the cancel hook first,
  which swallowed the cwd error on a cancelled path.
- **W4 MINOR-3** — `_emit_before_switch` / `_emit_before_fork`
  parameters are required (no defaults). Forces every callsite to
  thread the Pi-shape (reason / entry_id) explicitly.
- **W4 MINOR-4** — Test file naming + comment alignment with the
  P-355 correction: `test_dispose_uses_quit_reason_with_emit_first_order`
  (renamed from `..._with_invalidate_first_order`); module
  docstring updated to reflect EMIT-FIRST symmetry.

### Aelix-additive simplifications

- The `session_start` emit at bootstrap (Pi `reason="startup"` /
  `"reload"`) is deferred to Sprint 6h₅b — Aelix's
  `AgentHarness` factory pattern (P-302) constructs the harness
  before the `HookBus` bridge is wired. Sprint 6h₅a's emit covers
  ALL post-bootstrap replace paths.

- Pi's factory-bootstrap call site for `assertSessionCwdExists`
  (`:391`) and the `importFromJsonl` site (`:352`) are NOT wired in
  Sprint 6h₅a. Aelix's factory pattern is different, and
  `import_from_jsonl` is still stubbed per ADR-0080 carry-forward.
  Both defer to Sprint 6h₅c.

## Roster

**W0 (binding spec, P-332..P-343):**

- P-332 — `HookEventName` widening to 35 names + 4 event dataclasses
  + 2 result types.
- P-333 — `ExtensionRunner.emit` / `has_handlers` HookBus bridge.
- P-334 — `_emit_session_shutdown_event` module-private helper.
- P-335 — `_reducer_session_before` widening to 4 cancellable arms.
- P-336 — 35 overload count for `ExtensionAPI.on` / `HookBus.on`.
- P-337 — `session/session_cwd.py` Pi port (async-adapted).
- P-338 — Real `_emit_before_switch` body.
- P-339 — Real `_emit_before_fork` body.
- P-340 — `_teardown_current` ORDERING CORRECTION (Pi order).
- P-341 — `dispose` adds `session_shutdown(quit)` emit.
- P-342 — `previous_session_file` snapshot BEFORE teardown.
- P-343 — `session_start` emit from `_finish_session_replacement`.

**W4 / W5 (audit, P-344..P-355):**

- P-344 — Pi line citation corrections
  (`510-557` ambiguous span → 513-519/522-526/529-533/552-557 verified
  at SHA `734e08e`).
- P-345 — `SessionBeforeForkResult.skip_conversation_restore` Pi
  parity field re-add.
- P-346 — `format_missing_session_cwd_error` Pi-verbatim format +
  `SessionCwdIssue` field shape change (`session_file` optional;
  `fallback_cwd` required).
- P-347 — `format_missing_session_cwd_prompt` Pi port +
  `__all__` export + unit test.
- P-348 — `AgentHarnessEventName` literal widening to 22 names.
- P-349 — `Session.session_file` cross-reference pin (no new code).
- P-350 — `_TOOL_CALL_*` lookup tables continue to compile under the
  widened `HookEventName` (no actual change; assertion-only).
- P-351 — `ExtensionRunner.invalidate` (Pi `runner.ts` invalidate
  semantics) carry-forward to Sprint 6h₅b — Pi sets an internal
  `invalidated` flag that gates `emit` / `has_handlers`; Aelix has
  the disposed-bus no-op fallback covering most cases.
- P-352 — Closure pin overlay on `test_phase_3_1_strict_superset.py`
  (35-name cascade — extending the cumulative event-name fixture).
- P-353 — Pi fixture metadata block — Pi SHA + 4 line citations +
  reducer citation + emit-helper citation + AgentSessionRuntime
  member citations.
- P-354 — Closure pin `tests/pi_parity/test_phase_4_14_extension_events.py`
  with 14 invariant checks (HookEventName == 35, HOOK_RESULT_TYPES
  registry, _REDUCERS sharing, overload counts, Pi line citation
  drift detector, cancel-aggregation, exception isolation, reducer
  type widening, fixture pin).

**W5 BLOCKING — dispose ordering correction:**

- P-355 — `dispose` rewritten to **EMIT → INVALIDATE → DISPOSE**
  (matches `_teardown_current` — the W2 §J "intentional asymmetry"
  rationale was a Pi misread; Pi order is uniform).

**W4 MINOR cleanups (W6 applied):**

- W4 MEDIUM — `switch_session` assert-before-emit ordering (Pi
  `:184-189` line ordering).
- W4 MINOR-3 — `_emit_before_*` required args (no defaults).
- W4 MINOR-4 — Dispose test rename + module docstring P-355
  alignment.

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₄c (start of 6h₅a) | 29 | 0 | 29 |
| Sprint 6h₅a (this ADR) | **29** | **0** | **29** |

**RPC roster UNCHANGED** — extension polish doesn't alter the
dispatch table. Phase 4 RPC remains CLOSED.

## Consequences

- **Extension session lifecycle events are now Pi-parity wired
  end-to-end.** Extensions can observe `session_start` /
  `session_shutdown` on every replace path (`switch_session` /
  `new_session` / `fork`), can return
  `SessionBeforeSwitchResult(cancel=True)` /
  `SessionBeforeForkResult(cancel=True)` to abort a replace
  before teardown, and see live harness state during shutdown.

- **The ADR-0080 P-307 / P-308 carry-forward CLOSES.** P-355's
  forward-fix matches Pi `dispose` semantics; P-308's real
  `_emit_before_*` bodies replace the Sprint 6h₄b no-op stubs.
  Future ADR-0080 amendment records the closure.

- **`assertSessionCwdExists` Pi parity carry-forward
  PARTIALLY CLOSES.** `switch_session` wires the assertion; the
  factory bootstrap site + `importFromJsonl` site defer to
  Sprint 6h₅c (Aelix factory pattern is different from Pi; no
  RPC import surface exists today).

- **`previousSessionFile` / `sessionStartEvent` tracking
  CLOSES** for all post-bootstrap replace paths. The bootstrap
  `session_start` (Pi `reason="startup"` / `"reload"`) defers to
  Sprint 6h₅b (factory pattern integration).

- **The W2 §J "intentional asymmetry" claim is REFUTED** — the
  ADR commits the correction explicitly so future PRs reading the
  history don't reintroduce the asymmetry. The docstring on
  `AgentSessionRuntime.dispose` and the module docstring both
  cite Pi `:366-373` verbatim and call out the W2 misread.

- **`SessionBeforeForkResult.skip_conversation_restore` is a no-op
  Pi-shape field** in Sprint 6h₅a — the fork waveform doesn't read
  it. The field exists so extensions written against Pi typings
  compile against Aelix. Sprint 6h₅b wires the conditional restore
  branch in `_finish_session_replacement(reason="fork")`.

- **`format_missing_session_cwd_prompt` is exported but unused** in
  Sprint 6h₅a. Aelix has no TUI surface today; the helper is
  available to extensions and to Sprint 6h₅+ TUI work without
  forcing a Sprint 6h₅a TUI integration.

- **Closure pin establishes 14 invariants:** 35-name
  HookEventName + HOOK_RESULT_TYPES registry + _REDUCERS sharing
  + 35 overload count + Pi line citation drift + cancel-aggregation
  short-circuit + exception isolation under `error_mode="continue"`
  + reducer type widening to both new cancellable arms + fixture
  pin (Pi SHA + 4 line citations). Any PR weakening any of them
  trips immediately.

## References

- `packages/agent/src/harness/extensions/types.ts:513-519` (Pi
  `SessionStartEvent` — verified at SHA `734e08e`)
- `packages/agent/src/harness/extensions/types.ts:522-526` (Pi
  `SessionBeforeSwitchEvent` — verified at SHA `734e08e`)
- `packages/agent/src/harness/extensions/types.ts:529-533` (Pi
  `SessionBeforeForkEvent` — verified at SHA `734e08e`)
- `packages/agent/src/harness/extensions/types.ts:552-557` (Pi
  `SessionShutdownEvent` — verified at SHA `734e08e`)
- `packages/agent/src/harness/extensions/types.ts:1015-1022` (Pi
  `SessionBeforeForkResult` — verified at SHA `734e08e`)
- `packages/agent/src/harness/extensions/runner.ts:177-189`
  (Pi `emitSessionShutdownEvent`)
- `packages/agent/src/harness/extensions/runner.ts:680-712`
  (Pi reducer cancel-aggregation)
- `packages/agent/src/harness/session/session-cwd.ts:1-59`
  (Pi session-cwd helper module — Aelix async-adapted)
- `packages/agent/src/harness/session/session-cwd.ts:30-37`
  (Pi `formatMissingSessionCwdError` — P-346 verbatim port)
- `packages/agent/src/harness/session/session-cwd.ts:40-42`
  (Pi `formatMissingSessionCwdPrompt` — P-347 port)
- `packages/agent/src/core/agent-session-runtime.ts:115-130`
  (Pi `emitBeforeSwitch`)
- `packages/agent/src/core/agent-session-runtime.ts:132-147`
  (Pi `emitBeforeFork`)
- `packages/agent/src/core/agent-session-runtime.ts:149-157`
  (Pi `teardownCurrent` — P-340 ordering source)
- `packages/agent/src/core/agent-session-runtime.ts:166-173`
  (Pi `finishSessionReplacement` — P-343 emit source)
- `packages/agent/src/core/agent-session-runtime.ts:184-189`
  (Pi `switchSession` assert-before-emit ordering — W4 MEDIUM)
- `packages/agent/src/core/agent-session-runtime.ts:366-373`
  (Pi `dispose` — P-341 / P-355 source verbatim)
- `aelix-agent-core/src/aelix_agent_core/harness/hooks.py`
  (AMEND — 4 new event dataclasses + 2 new result types + reducer
  widening + 35-overload count + result-types registry entries +
  reducer registry entries + Pi line citation corrections)
- `aelix-agent-core/src/aelix_agent_core/harness/_extension_runner.py`
  (AMEND — wires new events through the existing emit/has_handlers
  bridge)
- `aelix-agent-core/src/aelix_agent_core/harness/core.py`
  (AMEND — extension runner bridge wiring for new events)
- `aelix-agent-core/src/aelix_agent_core/session/session.py`
  (AMEND — `session_file` property pinned cross-reference)
- `aelix-agent-core/src/aelix_agent_core/session/session_cwd.py`
  (NEW — Pi port; P-346 verbatim error format + P-347 prompt
  helper + Pi-shape `SessionCwdIssue` field shape)
- `aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py`
  (AMEND — real `_emit_before_switch` / `_emit_before_fork` bodies
  + `_teardown_current` ORDERING CORRECTION + `dispose` P-341 emit
  + P-355 ordering correction + `previous_session_file` snapshot +
  `_finish_session_replacement` `session_start` emit + `switch_session`
  assert-before-emit (W4 MEDIUM) + W4 MINOR-3 required args)
- `aelix-coding-agent/src/aelix_coding_agent/extensions/api.py`
  (AMEND — 4 new `@overload` declarations on `ExtensionAPI.on`)
- `tests/runtime/test_agent_session_runtime.py`
  (AMEND — `_emit_before_*` stub-now-real tests updated to drive
  with required args)
- `tests/runtime/test_agent_session_runtime_extension_events.py`
  (NEW — 9 wiring tests: teardown order + dispose P-355 order +
  cancel short-circuits switch + cancel short-circuits fork +
  session_start emit on new harness with reason+previous + new
  reason + fork reason + previous_session_file snapshot timing +
  no-handlers emit no-op + cancelled replace doesn't emit start)
- `tests/runtime/test_agent_session_runtime_session_cwd.py`
  (NEW — 3 wiring tests: missing-cwd raises + happy path + assert
  runs after repo.open)
- `tests/session/test_session_cwd_helper.py`
  (NEW — 10 unit tests covering P-337 + P-346 verbatim error
  format + P-347 prompt format + P-346 SessionCwdIssue field shape
  change)
- `tests/session/test_session_file_property.py`
  (NEW — P-349 `Session.session_file` cross-reference pin)
- `tests/extensions/test_extension_runner_emit_delegate.py`
  (NEW — P-333 ExtensionRunner emit/has_handlers bridge tests)
- `tests/pi_parity/test_phase_4_14_extension_events.py`
  (NEW closure pin — 14 invariants)
- `tests/pi_parity/fixtures/pi_extension_events_734e08e.json`
  (NEW W0 fixture — Pi SHA + 4 line citations + reducer pin +
  AgentSessionRuntime member citations + overload counts)
- `tests/pi_parity/fixtures/pi_agent_harness_event_names_734e08e.json`
  (AMEND — 4 new extension session lifecycle events + 4 line
  citations)
- `tests/pi_parity/test_hook_event_name_literal_pi_parity.py`
  (AMEND — 35-name cascade pin)
- `tests/pi_parity/test_phase_3_1_strict_superset.py`
  (AMEND — cascade count update for 35-name fixture)
- `tests/test_hook_payload_roundtrip.py`
  (AMEND — 4 new events added to roundtrip coverage)
- `tests/test_overloads_extension_api.py`
  (AMEND — 35-overload count assertion)

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₅a row this sprint).
- ADR-0017 v2 — Full hook event catalogue (this ADR extends the
  catalogue with the 4 extension session lifecycle events).
- ADR-0019 v3 — Hook error policy v2 (`error_mode="continue"`
  isolation pin verified for the 2 new cancellable events).
- ADR-0036 — `AgentHarnessEventName` Pi-citation clarity alias
  (this ADR widens the alias to 22 names).
- ADR-0042 — Sprint 5b coding-agent event emit sites (Sprint 5a
  31-overload baseline carry-forward CLOSED by this ADR's
  35-overload count).
- ADR-0077 — `AgentSessionRuntime` Pi port + `rebindSession` seam
  (Sprint 6h₄b foundation — this ADR fills the P-307 / P-308
  carry-forward).
- ADR-0079 — Sprint 6h₄c wiring decisions (Sprint 6h₅a sits on
  top of the wired runtime replace APIs).
- ADR-0080 — Sprint 6h₄c Phase 4.13 strict-superset closure +
  Sprint 6h₅+ carry-forward roster (P-307 / P-308 CLOSE here per
  this ADR; remaining 6h₅+ items split across 6h₅b / 6h₅c).
- ADR-0082 — Sprint 6h₅a Phase 4.14 strict-superset closure pin +
  Sprint 6h₅b / 6h₅c carry-forward roster.
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).
- ADR-0032 — Sprint workflow + W4/W5 audit mandatory gate.

## Phase

Sprint 6h₅a / Phase 4.14 / W6 (shipped — extension event Pi
parity + P-307/P-308 CLOSES + W5 P-355 dispose-ordering
correction shipped).
