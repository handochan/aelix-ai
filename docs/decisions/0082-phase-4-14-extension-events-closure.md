# 0082. Sprint 6h₅a Phase 4.14 — Strict Superset Closure + 6h₅b / 6h₅c Carry-Forward (Extension events closed + Phase 4 RPC roster STILL CLOSED)

Status: Accepted (Sprint 6h₅a / Phase 4.14 / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Phase 4.14 closes the extension session lifecycle event wiring sprint
shipped under ADR-0081 with a strict-superset audit lane (W4
code-review opus + W5 architect opus Pi parity audit per ADR-0032).
The W4/W5 audit returned **1 BLOCKING (P-355 dispose ordering) + 6
MUST-FIX (P-344~P-347 W5 corrections + W4 MEDIUM switch_session order
+ W4 MINOR-3 required args)**, with **4 W4 MINOR cleanups**. Sprint
6h₅a W6 applied every must-fix item in 5 atomic commits.

This ADR records the closure pin invariants and the **extension event
Pi parity milestone**: Aelix now ships the 4 Pi extension session
lifecycle events (`session_start` / `session_before_switch` /
`session_before_fork` / `session_shutdown`) end-to-end on top of the
6h₄c `AgentSessionRuntime` foundation. The Phase 4 RPC roster STAYS
CLOSED at 29 / 0 / 29 — extension polish doesn't change the RPC
discriminator union.

## Decision

### Closure pin invariants

`tests/pi_parity/test_phase_4_14_extension_events.py` locks the
following Pi parity invariants at SHA `734e08e`:

- `len(get_args(HookEventName)) == 35` — **MAXIMUM** for the
  31-name Sprint 5a baseline + 4 extension session lifecycle events
  (`session_start` / `session_before_switch` / `session_before_fork` /
  `session_shutdown`).
- `HOOK_RESULT_TYPES` maps the 2 cancellable events to their result
  classes: `session_before_switch → SessionBeforeSwitchResult` /
  `session_before_fork → SessionBeforeForkResult`. The 2
  observational events map to `None`.
- `_REDUCERS` shares `_reducer_session_before` across all 4
  cancellable events (`session_before_compact` / `session_before_tree`
  / `session_before_switch` / `session_before_fork`). The 2
  observational events use `_reducer_observational`.
- `typing.get_overloads(ExtensionAPI.on) == 35` /
  `typing.get_overloads(HookBus.on) == 35` — 4 new overloads on top
  of the Sprint 5a 31-overload baseline (Sprint 5a 28 + 3 coding-agent
  + 4 extension session lifecycle = 35).
- Pi line citation drift detector — runtime docstrings + closure pin
  reference the Pi line ranges so future PRs cannot silently lose the
  binding:
  - `_teardown_current` → `:149-157`
  - `dispose` → `:366-373`
  - `_finish_session_replacement` → `:166-173`
  - `_emit_before_switch` → `:115-130`
  - `_emit_before_fork` → `:132-147`
  - `_emit_session_shutdown_event` helper → `runner.ts:177-189`
  - `session/session_cwd.py` references `session-cwd.ts:1-59`
  - `_extension_runner` references `runner.ts:680-712`
- Cancel-aggregation semantics: register 3 handlers, the 2nd returns
  `SessionBeforeSwitchResult(cancel=True)`, the 3rd MUST NOT run (Pi
  parity: `runner.ts:680-712` first-cancel-wins).
- Exception isolation: handler 1 raises with `error_mode="continue"`,
  the chain survives, handler 2's cancel wins.
- Reducer return-type union widened to accept both
  `SessionBeforeSwitchResult` and `SessionBeforeForkResult` (the
  legacy `SessionBeforeCompactResult` / `SessionBeforeTreeResult`
  arms still work — closure pin sanity-tests the legacy two arms).
- W0 fixture `pi_extension_events_734e08e.json` immutable + SHA-pinned;
  fixture pins the 4 line citations
  (`513-519` / `522-526` / `529-533` / `552-557`) and the Pi reducer +
  emit-helper + AgentSessionRuntime member citations.

### Dispose ordering correction pin (P-355)

The closure pin (`tests/runtime/test_agent_session_runtime_extension_events.py::test_dispose_uses_quit_reason_with_emit_first_order`)
asserts the dispose order is **EMIT → INVALIDATE → DISPOSE** matching
Pi `:366-373` verbatim. Any PR reverting to the W2 § J "intentional
asymmetry" trips. The runtime module docstring + the dispose docstring
both document the W2 misread + correction so the history is durable.

### Switch-session assert-before-emit ordering pin (W4 MEDIUM)

The closure pin (`tests/runtime/test_agent_session_runtime_session_cwd.py::test_assert_runs_after_repo_open_so_check_is_for_new_session`)
asserts `repo.open` resolves BEFORE `fs.exists(target_cwd)` AND
before `_emit_before_switch` (Pi `:184-189` ordering — Pi asserts
cwd before letting any extension cancel the swap).

### `SessionBeforeForkResult` field-shape pin (P-345)

The closure pin (`tests/pi_parity/test_phase_4_14_extension_events.py`)
asserts `SessionBeforeForkResult.__dataclass_fields__` contains
exactly `{"cancel", "skip_conversation_restore"}` matching Pi
`extensions/types.ts:1015-1022`.

### Pi-verbatim error/prompt format pin (P-346 / P-347)

The closure pin (`tests/session/test_session_cwd_helper.py::test_format_missing_session_cwd_error_matches_pi_verbatim`)
asserts the rendered string equals Pi `:30-37` verbatim. The
closure pin (`tests/session/test_session_cwd_helper.py::test_format_missing_session_cwd_prompt_matches_pi_verbatim`)
asserts the rendered prompt equals Pi `:40-42` verbatim.

### Extension event closure milestone

Aelix Phase 4.14 closes the P-307 / P-308 carry-forward roster from
ADR-0080 plus the `previousSessionFile` / `sessionStartEvent`
tracking gap plus the `assertSessionCwdExists` partial wiring (the
`switch_session` site). The 4 Pi extension session lifecycle events
now route end-to-end through `AgentSessionRuntime` →
`ExtensionRunner` → `HookBus` → `_reducer_*`.

### Sprint 6h₅b / 6h₅c carry-forward (Pi parity items still open)

The remaining Sprint 6h₅+ carry-forward roster from ADR-0080 splits
across two follow-on sprints. **None of these items touch the RPC
dispatch table** — they surface via in-process APIs, factory
patterns, or TUI commands.

**Sprint 6h₅b — runtime / extension polish (post-extension-event wiring):**

- **`with_session`** 2-stage callback for
  `finishSessionReplacement` (Pi `agent-session-runtime.ts:226-229` /
  `:289-291` / `:316-318` — P-314 carry-forward from ADR-0080).
  Surface: in-process API on `AgentSessionRuntime` accepting
  `Callable[[ReplacedSessionContext], Awaitable[None]] | None`.
- **`setup`** callback in `new_session` (Pi
  `agent-session-runtime.ts:226-229` — P-314 carry-forward sibling).
  Surface: in-process API on `AgentSessionRuntime.new_session`.
- **`set_rebind_session` / `set_before_session_invalidate`
  optional-cb signature widening** (Pi accepts `undefined` to
  clear — P-315 carry-forward from ADR-0080).
- **`forkFrom` cross-cwd import** — Pi can fork from a session whose
  cwd differs from the runtime's current cwd. Aelix `ForkOptions.cwd`
  currently inherits from the source metadata. Surface: in-process
  API + ForkOptions extension. (No RPC wire exists today.)
- **`import_from_jsonl` real runtime body** — Pi
  `agent-session-runtime.ts:329-364` is implementable but has no
  RPC `RpcCommand` discriminator at SHA `734e08e`. The Pi call site
  is the TUI `/import` command which doesn't go through RPC. Aelix
  STAYS STUBBED in Sprint 6h₅a; Sprint 6h₅b fills the body.
- **`session_start` bootstrap emit** (`reason="startup"` /
  `"reload"`). Pi emits `session_start` at factory bootstrap; Aelix's
  factory pattern (P-302) constructs the harness before the HookBus
  bridge is wired. Factory pattern change required to emit at
  bootstrap.
- **P-351 `ExtensionRunner.invalidate` semantics** — Pi sets an
  internal `invalidated` flag that gates `emit` / `has_handlers` so
  ALL events become no-ops post-invalidate. Aelix has the disposed-bus
  no-op fallback covering most cases (the bus dispose clears
  handlers); P-351 adds the explicit invalidate flag for the small
  window between `before_session_invalidate` and `harness.dispose`.

**Sprint 6h₅c — TUI / HTML / context-usage polish (carry-forwards from
ADR-0074 / ADR-0078 / ADR-0080):**

- **Pi HTML visual fidelity** (CSS framework, syntax highlighting,
  responsive layout) — carry-forward from ADR-0074.
- **`ImageContent` rendering in HTML export** — carry-forward from
  ADR-0074.
- **`outputPath` default resolution rules** (Pi
  `export-html.ts:273-277` — currently match; any future divergence
  re-opens) — tracked from ADR-0078.
- **`_get_context_usage_safe` real implementation** (P-282 — model
  registry + per-turn token tracking + last-assistant message
  tokens) — carry-forward from ADR-0074.
- **Live `session_id` read via session manager** (P-291) —
  carry-forward from ADR-0074.
- **Pi-source-grep verification tooling** (P-286) — carry-forward
  from ADR-0074.
- **Factory bootstrap `assertSessionCwdExists` call site** — Pi
  `:391`; Aelix factory pattern is different. Defer until the
  bootstrap-emit work in 6h₅b lands.
- **`importFromJsonl` `assertSessionCwdExists` call site** — Pi
  `:352`; Aelix `import_from_jsonl` is still stubbed (no RPC wire).
  Defer until Sprint 6h₅b fills the body.

### Items CONFIRMED dropped (per Sprint 6h₅a)

None this sprint. P-313 `HarnessFactory` 4-field refresh remains
DROPPED per ADR-0080 (harness-rebuild encapsulates services +
diagnostics + model_fallback_message via factory closure).

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₄c (start of 6h₅a) | 29 | 0 | 29 |
| Sprint 6h₅a (this ADR) | **29** | **0** | **29** |

**RPC roster UNCHANGED.** Extension polish doesn't change the
dispatch table. Phase 4 RPC remains CLOSED.

| Closure type | Counted | Notes |
|---|---|---|
| HookEventName Literal names | **35** | Sprint 5a 31 + Sprint 6h₅a 4 |
| `ExtensionAPI.on` overloads | **35** | mirrors `HookEventName` |
| `HookBus.on` overloads | **35** | mirrors `HookEventName` |
| `AgentHarnessEventName` names | **22** | Sprint 5a 18 + Sprint 6h₅a 4 |
| `_REDUCERS` registry size | **35** | mirrors `HookEventName` |
| `HOOK_RESULT_TYPES` registry | **35** | mirrors `HookEventName` |

## Consequences

- **Extension session lifecycle events are Pi-parity wired
  end-to-end.** The 4 Pi events route through
  `AgentSessionRuntime` → `ExtensionRunner` → `HookBus` →
  `_reducer_*`. Closure pin asserts the 35-name cascade, the shared
  reducer, the 35-overload count, and the first-cancel-wins
  short-circuit.
- **The ADR-0080 P-307 / P-308 carry-forwards CLOSE.** Both the
  `session_shutdown` emit (P-307) and the real
  `_emit_before_switch` / `_emit_before_fork` bodies (P-308) land
  this sprint with Pi parity. ADR-0080 carry-forward roster amend
  records the closure.
- **The W2 §J "intentional asymmetry" claim is REFUTED.** The
  closure pin asserts `dispose` and `_teardown_current` share the
  same order (**EMIT → INVALIDATE → DISPOSE**). Any PR reverting to
  the W2 reversed order trips immediately. The runtime + dispose
  docstrings document the misread + correction explicitly for
  durability.
- **`SessionBeforeForkResult` Pi shape locked.** The closure pin
  asserts `__dataclass_fields__` contains exactly `{cancel,
  skip_conversation_restore}`. Extension authors writing against Pi
  typings now compile against Aelix.
- **`session_cwd` error / prompt format Pi-verbatim.** The closure
  pin asserts both `format_missing_session_cwd_error` (P-346) and
  `format_missing_session_cwd_prompt` (P-347) render strings equal
  to Pi `:30-37` / `:40-42` verbatim.
- **`switch_session` assert-before-emit ordering pinned.** The
  closure pin (`tests/runtime/test_agent_session_runtime_session_cwd.py`)
  asserts `repo.open` + `fs.exists` resolve BEFORE
  `_emit_before_switch`, matching Pi `:184-189` ordering.
- **Phase 4 RPC roster STAYS CLOSED.** Extension polish has no
  dispatch impact. The forward-compat clause holds.
- **Sprint 6h₅b / 6h₅c carry-forward is split.** 6h₅b handles
  runtime / extension polish (with_session / setup / forkFrom /
  import_from_jsonl body / bootstrap session_start / P-351
  ExtensionRunner.invalidate); 6h₅c handles HTML visual fidelity +
  `_get_context_usage_safe` + `ImageContent`. Neither sprint touches
  the RPC dispatch table.

## References

- ADR-0081 — Sprint 6h₅a extension event Pi parity decisions
  (sibling ADR — 4 events + session_cwd helper + W5 P-355
  dispose-ordering correction + W4 MEDIUM switch_session order +
  W4 MINOR-3 required args).
- `tests/pi_parity/test_phase_4_14_extension_events.py` — closure
  pin (lock 35-name cascade + 35-overload count + Pi line citation
  drift + cancel-aggregation + exception isolation + reducer type
  widening + fixture pin).
- `tests/pi_parity/fixtures/pi_extension_events_734e08e.json` — W0
  fixture for Pi extension event Pi line citations + reducer +
  emit-helper + AgentSessionRuntime member citations.
- `tests/runtime/test_agent_session_runtime_extension_events.py` —
  9 wiring tests (P-340 / P-355 / P-338 / P-339 / P-343 / P-342 +
  defensive no-handlers + cancel-no-start).
- `tests/runtime/test_agent_session_runtime_session_cwd.py` — 3
  wiring tests (assert raises + happy path + assert-after-open
  ordering).
- `tests/session/test_session_cwd_helper.py` — 10 unit tests
  covering P-337 + P-346 verbatim format + P-347 prompt format +
  field shape change.
- `tests/session/test_session_file_property.py` — P-349
  cross-reference pin.
- `tests/extensions/test_extension_runner_emit_delegate.py` —
  P-333 ExtensionRunner emit/has_handlers bridge tests.
- `tests/pi_parity/test_hook_event_name_literal_pi_parity.py` —
  35-name cascade pin.
- `packages/agent/src/harness/extensions/types.ts:513-557` (4
  extension session lifecycle event line ranges — verified at SHA
  `734e08e`).
- `packages/agent/src/harness/extensions/types.ts:1015-1022` (Pi
  `SessionBeforeForkResult`).
- `packages/agent/src/core/agent-session-runtime.ts:115-198`
  (Pi runtime emit / teardown / finish_session_replacement /
  switch_session — line ranges verified at SHA `734e08e`).
- `packages/agent/src/core/agent-session-runtime.ts:366-373`
  (Pi `dispose` — P-341 / P-355 source verbatim).
- `packages/agent/src/harness/session/session-cwd.ts:1-59` (Pi
  session-cwd helper module — verbatim port target for Aelix
  `session/session_cwd.py`).

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₅a row this sprint).
- ADR-0081 — Sprint 6h₅a extension event Pi parity decisions
  (sibling ADR).
- ADR-0080 — Sprint 6h₄c Phase 4.13 strict-superset closure +
  Sprint 6h₅+ carry-forward roster (amended this sprint:
  P-307 / P-308 carry-forwards CLOSE per ADR-0081).
- ADR-0079 — Sprint 6h₄c wiring decisions (sibling-of-foundation;
  Sprint 6h₅a sits on top of the wired runtime replace APIs).
- ADR-0077 — `AgentSessionRuntime` Pi port + `rebindSession` seam
  (Sprint 6h₄b foundation).
- ADR-0017 v2 — Full hook event catalogue (Sprint 3a / Sprint 5a
  cumulative 31-event baseline — Sprint 6h₅a extends to 35).
- ADR-0019 v3 — Hook error policy v2 (`error_mode="continue"`
  isolation pin verified for the 2 new cancellable events).
- ADR-0036 — `AgentHarnessEventName` Pi-citation clarity alias
  (Sprint 6h₅a widens to 22 names).
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).
- ADR-0032 — Sprint workflow + W4/W5 audit mandatory gate.

## Phase

Sprint 6h₅a / Phase 4.14 / W6 (shipped — **extension event Pi
parity CLOSED**; Phase 4 RPC roster STAYS CLOSED).
