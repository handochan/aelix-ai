# 0077. Sprint 6h₄b Phase 4.12 — `AgentSessionRuntime` Pi Port + `rebindSession` Seam (FOUNDATION ONLY)

Status: Accepted (Sprint 6h₄b / Phase 4.12 / W6 shipped)
Date: 2026-05-21
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₄a (ADR-0075/0076) closed Phase 4.11 with **26 supported /
3 deferred** RPC commands. The remaining 3 session-tree commands
(`switch_session` / `fork` / `clone`) deferred to Sprint 6h₄b per
ADR-0076 because they require Pi `AgentSessionRuntime` +
`SessionManager.getLeafId()` + the `rebindSession()` seam (P-126
Sprint 6f multi-sprint carry-forward).

Sprint 6h₄b ports the Pi `AgentSessionRuntime`
(`packages/coding-agent/src/core/agent-session-runtime.ts:67-374`) and
the `rebindSession` closure (`rpc-mode.ts:310-349`) as the
**FOUNDATION-ONLY** layer. **No new RPC commands are wired in this
sprint** — `SUPPORTED_COMMANDS` stays at 26; `DEFERRED_COMMANDS` stays
at 3; total stays at 29. Sprint 6h₄c (ADR-0078) wires
`switch_session` / `fork` / `clone` on top of this foundation without
re-touching the runtime layer.

## Decision

### Pi parity decisions (P-302 ~ P-310 + P-318)

- **P-302 (BINDING)** — **Harness-rebuild pattern** (NOT session-swap).
  Pi reassigns `this._session` in-place at
  `agent-session-runtime.ts:166-173` because Pi `AgentSession` is a
  stateless wrapper over `SessionManager`. Aelix `Session` lacks
  `dispose` / `subscribe` / `bindExtensions` — those live on
  `AgentHarness`, and `AgentHarness._session` is set ONCE in `__init__`
  (`harness/core.py:486`) with `_state.session_id` captured eagerly at
  `:524` from the storage metadata. Aelix `AgentSessionRuntime`
  therefore holds a `HarnessFactory: Callable[[Session],
  Awaitable[AgentHarness]]` and replaces by disposing the OLD harness
  + constructing a NEW harness from the factory. This preserves the
  `__init__` invariants (`_state.session_id`, action bindings, merged
  tools, cached session-name) that Aelix relies on but Pi's TS class
  does not. **Wire-level Pi parity is preserved** — `subscribe()`
  outputs and RPC handler returns remain observationally identical.

- **P-303** — `rebindSession` closure parity in shape, async in
  callable type. Pi `(rpc-mode.ts:310-349)` is `async (): Promise<void>`;
  Aelix mirrors as `async def rebind(new_harness: AgentHarness) -> None`.
  Pi closes over outer `let session` + `let unsubscribe`; Python
  closures can read but not rebind enclosing names, so Aelix attaches
  both to a lightweight `_Capture` container. The Pi `bindExtensions`
  / `commandContextActions` waveform (Pi `:315-345`) is **NOT WIRED in
  6h₄b** — Aelix `_runtime.bind_core` already ran during the NEW
  harness's `__init__` (P-302). The explicit action surface lands in
  6h₄c when the 3 DEFERRED RPC handlers move.

- **P-304** — Pi class structure (`67-374`) verified at SHA 734e08e.
  All 15 Pi member line-ranges asserted in the W0 fixture
  (`tests/pi_parity/fixtures/pi_agent_session_runtime_734e08e.json`).
  Aelix exposes both `runtime.harness` (current `AgentHarness`) AND
  `runtime.session` (read-through to `_harness._session`) — Pi name
  preserved for source-level grep parity. Both return LIVE references;
  callers MUST re-read after `setRebindSession`-triggered replacements.

- **P-305** — Pi `setRebindSession`
  (`agent-session-runtime.ts:99-101`) is fire-and-await: `await
  this.rebindSession?.(this._session)`. Aelix mirrors:
  `set_rebind_session(cb: Callable[[AgentHarness], Awaitable[None]])`
  and `await self._rebind_session(self._harness)` in the replace path.
  When `set_rebind_session` is not registered, the runtime no-ops (Pi
  optional-chaining parity `?.()`).

- **P-306 (BINDING)** — `_state.session_id` invariant preservation
  via harness-rebuild. The NEW harness produced by the factory MUST
  be bound to the NEW session BEFORE construction so the eager
  metadata read at `harness/core.py:521-524` resolves to the NEW
  session's ID. The factory contract therefore takes `Session` as
  input and returns a constructed `AgentHarness` (NOT an
  `AgentHarnessOptions`). MUST-FIX 4 ships a unit test
  (`test_state_session_id_on_new_harness_reflects_new_session_metadata`)
  asserting the invariant after `_apply_for_test`.

- **P-307 (CARRY-FORWARD to 6h₄c per ADR-0078)** —
  `session_shutdown` extension event emit missing in Aelix
  `_teardown_current` / `dispose`. Pi `dispose()`
  (`agent-session-runtime.ts:366-373`) emits `session_shutdown`
  through `_session` before disposing; Aelix `AgentHarness.dispose()`
  does NOT emit this event today (`harness/core.py:1961-1976`). Gap
  recorded in ADR-0078 carry-forward; emit added when extension event
  infra is extended in a future sprint.

- **P-308 (CARRY-FORWARD to 6h₄c)** —
  `session_before_switch` / `session_before_fork` extension cancel
  events stubbed as no-ops returning `cancelled=False` (Pi
  `emitBeforeSwitch` / `emitBeforeFork` at
  `agent-session-runtime.ts:115-130` / `:132-147`). Aelix has no
  `session_before_switch` / `session_before_fork` hook events today.
  Real cancel hooks when 6h₄c wires runtime methods on top of this
  foundation.

- **P-309** — `run_rpc_mode` signature shim. ADDITIVE signature change:
  the entry function accepts `runtime_host: AgentSessionRuntime | None =
  None` (and companion `harness_factory: HarnessFactory | None = None`)
  for backward compatibility. When `None`, the entry wraps the passed
  harness in a passthrough runtime constructed by
  `_make_passthrough_runtime(harness, None)`. The passthrough's
  factory is a `_noop_factory` that RAISES `RuntimeError` on invocation
  (W4 LOW-3 — fail loudly instead of silently re-binding to the same
  stale harness). The 26 existing handlers receive the same `harness`
  argument they receive today; 6h₄c can pass an explicit `runtime_host`
  to enable replace paths for the 3 deferred handlers without
  re-touching the signature.

- **P-310** — `_emit_before_switch` / `_emit_before_fork` async no-op
  stubs preserve the Pi async signature so 6h₄c can fill in the body
  without breaking callers. The 4 public replace APIs
  (`switch_session` / `new_session` / `fork` / `import_from_jsonl`)
  raise `NotImplementedError("Sprint 6h₄c — ADR-0078")`; the PRIVATE
  seam (`_apply` / `_teardown_current` / `_finish_session_replacement`)
  is fully implemented and exercised through the test-only
  `_apply_for_test(new_session)` entry.

- **P-318** — Diagnostics list copied on constructor + getter.
  Aelix-additive safety: Pi takes a list reference; Aelix `__init__`
  stores `list(diagnostics) if diagnostics else []` and
  `runtime.diagnostics` returns a fresh copy so caller mutations cannot
  bleed into the runtime's internal state.

### W5 Aelix-additive divergences (P-313 ~ P-316 — informational, all carry-forward)

- **P-313** — Pi `apply()` writes 4 fields (`_session`, `_services`,
  `_diagnostics`, `_modelFallbackMessage`); Aelix `_apply()` writes 1
  (`_harness`). **Carry-forward to 6h₄c (ADR-0078):** widen
  `HarnessFactory` signature or add accessors so the full Pi
  field-refresh shape lands when the public replace APIs wire up.

- **P-314** — Pi `finishSessionReplacement` has `withSession?` 2-stage
  callback; Aelix omits. **Carry-forward to 6h₄c:** wire `with_session`
  param when 6h₄c implements public replace APIs.

- **P-315** — `set_rebind_session` / `set_before_session_invalidate`
  non-optional signatures (Pi accepts `undefined` to clear). Aelix-
  additive narrowing.

- **P-316** — `rebind_session` closure: Pi re-reads
  `runtimeHost.session` internally; Aelix takes `new_harness` as arg.
  Wire-equivalent; documentation-only divergence.

### W6 must-fixes applied this sprint

- **MUST-FIX 1** — ADR rebrand 0076 → 0078 (W4 MEDIUM-1 + W5 P-312 +
  W5 P-319). DEFERRED owner strings rebranded; cascade pin allowlists
  in 4.4 / 4.9 / 4.10 / 4.11 extended with ADR-0078 prefix; closure
  pin in 4.12 tightened to require ADR-0078 ONLY.
- **MUST-FIX 2** — `tests/rpc/test_rpc_mode_runtime_shim.py` added
  with 7 back-compat regression tests (P-309 / P-311).
- **MUST-FIX 3** — `_noop_factory` raises `RuntimeError` instead of
  returning the same harness (W4 LOW-3).
- **MUST-FIX 4** — P-306 `_state.session_id` invariant test added
  (W4 NIT-2).
- **MUST-FIX 5** — Empty test renamed to smoke-test convention
  (W4 NIT-1).

## Roster

**W0 (binding spec, P-302..P-310, P-318):**

- P-302 — Harness-rebuild vs session-swap; BINDING decision.
- P-303 — `rebindSession` closure shape parity + Python `_Capture` adapter.
- P-304 — Runtime class shape; `runtime.harness` + `runtime.session`
  getters.
- P-305 — `setRebindSession` fire-and-await mirror.
- P-306 — `_state.session_id` invariant preservation (BINDING).
- P-307 — `session_shutdown` emit gap (carry-forward).
- P-308 — `session_before_switch` / `session_before_fork` stubs
  (carry-forward).
- P-309 — `run_rpc_mode` signature shim.
- P-310 — Async no-op stubs preserving Pi shape.
- P-318 — Diagnostics list copied (Aelix-additive safety).

**W4/W5 INFO findings (carry-forward — informational):**

- P-313 — Pi `apply()` writes 4 fields (carry-forward to 6h₄c).
- P-314 — `withSession?` 2-stage callback (carry-forward to 6h₄c).
- P-315 — Optional-cb signature narrowing (Aelix-additive).
- P-316 — `rebind_session` closure re-read pattern (documentation).

**Sprint 6h₄c carry-forward (per ADR-0078):**

- 3 session-tree commands wire (`switch_session` / `fork` / `clone`).
- Real `_emit_before_switch` / `_emit_before_fork` extension events
  (P-308 fill-in).
- P-307 `session_shutdown` extension event emit.
- P-313 widen `HarnessFactory` for full Pi field refresh.
- P-314 `with_session` 2-stage callback wire.

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₄a (start of 6h₄b) | 26 | 3 | 29 |
| Sprint 6h₄b (this ADR) | **26** | **3** | **29** |

**Counts UNCHANGED** — foundation sprint, no RPC commands wired.

## References

- `packages/coding-agent/src/core/agent-session-runtime.ts:67-374`
  (Pi `AgentSessionRuntime` class — verified at SHA `734e08e`)
- `packages/coding-agent/src/core/agent-session-runtime.ts:99-101`
  (`setRebindSession`)
- `packages/coding-agent/src/core/agent-session-runtime.ts:111-113`
  (`setBeforeSessionInvalidate`)
- `packages/coding-agent/src/core/agent-session-runtime.ts:149-157`
  (`teardownCurrent`)
- `packages/coding-agent/src/core/agent-session-runtime.ts:159-164`
  (`apply`)
- `packages/coding-agent/src/core/agent-session-runtime.ts:166-173`
  (`finishSessionReplacement`)
- `packages/coding-agent/src/core/agent-session-runtime.ts:366-373`
  (`dispose`)
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:306-308`
  (registration site of the rebind closure)
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:310-349`
  (`rebindSession` closure body)
- `aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py`
  (NEW — Pi port + private replace seam)
- `aelix-agent-core/src/aelix_agent_core/runtime/_types.py`
  (NEW — `HarnessFactory` + `RuntimeReplaceResult` + diagnostic)
- `aelix-agent-core/src/aelix_agent_core/runtime/__init__.py`
  (NEW package init)
- `aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py`
  (AMEND — `runtime_host` shim + `rebind_session` closure +
  `_make_passthrough_runtime` helper + DEFERRED owner rebrand)
- `tests/runtime/test_agent_session_runtime.py`
  (unit + rebind seam + P-306 invariant)
- `tests/rpc/test_rpc_mode_rebind.py` (rebind closure integration)
- `tests/rpc/test_rpc_mode_runtime_shim.py`
  (NEW — 7 P-309 / P-311 back-compat regression tests)
- `tests/pi_parity/test_phase_4_12_strict_superset.py`
  (closure pin — 26 / 3 / 29 unchanged + DEFERRED owners cite
  ADR-0078)
- `tests/pi_parity/fixtures/pi_agent_session_runtime_734e08e.json`
  (W0 fixture — verified Pi line ranges)

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₄b row this sprint).
- ADR-0076 — Sprint 6h₄a Phase 4.11 closure (amended this sprint —
  ownership of `switch_session` / `fork` / `clone` rebrands from
  ADR-0076 → ADR-0078 per spec §D.5).
- ADR-0078 — Sprint 6h₄b Phase 4.12 closure pin + Sprint 6h₄c wiring
  carry-forward.
- ADR-0075 — Sprint 6h₄a 2 read-only session-navigation handlers
  precursor (sibling).
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).

## Phase

Sprint 6h₄b / Phase 4.12 / W6 (shipped — FOUNDATION-ONLY).
