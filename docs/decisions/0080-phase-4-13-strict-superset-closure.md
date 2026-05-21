# 0080. Sprint 6h₄c Phase 4.13 — Strict Superset Closure + Phase 4 RPC Roster CLOSED

Status: Accepted (Sprint 6h₄c / Phase 4.13 / W6 shipped)
Date: 2026-05-21
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Phase 4.13 closes the wiring sprint shipped under ADR-0079 with a
strict-superset audit lane (W4 code-review opus + W5 architect opus
Pi parity audit per ADR-0032). The W4/W5 audit returned **0
BLOCKING / 0 MAJOR**, with **4 MINOR W4 cleanups** + **2 W5
documented divergences (ratified)**. Sprint 6h₄c W6 applied every
MINOR cleanup in 5 atomic commits.

This ADR records the closure pin invariants and the **Phase 4 RPC
roster CLOSURE milestone**: Aelix now ships full Pi parity for the
29-variant `RpcCommand` discriminator union at SHA `734e08e`. **This
is the LAST RPC sprint for Phase 4.** Phase 5 Pi-parity work moves
to runtime / extension polish (P-307 / P-308 / P-314 / P-315 +
session-cwd validation + cross-cwd import + `sessionStartEvent` +
TUI `/import` body) per the Sprint 6h₅+ carry-forward roster below.

## Decision

### Closure pin invariants

`tests/pi_parity/test_phase_4_13_strict_superset.py` locks the
following Pi parity invariants at SHA `734e08e`:

- `len(SUPPORTED_COMMANDS) == 29` — **MAXIMUM** for the 29-variant
  Pi `RpcCommand` union.
- `len(DEFERRED_COMMANDS) == 0` — **EMPTY** dict literal.
- `SUPPORTED_COMMANDS == RPC_COMMAND_TYPES` — **full set equality**.
  The 3 newly wired commands (`switch_session` / `fork` / `clone`)
  are in `SUPPORTED_COMMANDS` and absent from `DEFERRED_COMMANDS`.
- `DEFERRED_COMMANDS == {}` literal empty — any future PR that
  adds a deferred entry trips immediately.
- Each of the 3 new handlers (`_handle_switch_session`,
  `_handle_fork`, `_handle_clone`) is a real callable (NOT a
  `_make_deferred_handler` stub).
- `_handle_new_session` MOVED from
  `_SUPPORTED_HANDLERS_HARNESS_ONLY` to
  `_SUPPORTED_HANDLERS_RUNTIME_HOST` per P-330.
- `_SUPPORTED_HANDLERS_RUNTIME_HOST` carries exactly 4 entries:
  `new_session` + `switch_session` + `fork` + `clone`.
- `AgentSessionRuntime.__init__` requires keyword-only `repo` +
  `fs` (P-324 constructor signature pin).
- `_apply_for_test` test seam REMOVED — `not hasattr(
  AgentSessionRuntime, "_apply_for_test")` (P-331).
- `_extract_user_message_text` module-private helper exists.
- Pi line citations present in handler docstrings: `563-569`
  (`switch_session`) / `571-577` (`fork`) / `579-589` (`clone`) /
  `277-282` (`new_session`).
- Wire shapes:
  - `fork` returns `{cancelled, text?}` — `text` key OMITTED when
    `selected_text is None` (P-327 / P-298).
  - `clone` returns `{cancelled}` only — `text` key NEVER emitted
    (P-328 / Pi line 588).
  - `switch_session` returns `{cancelled}` (Pi line 568).
- P-328 ordering: spying on `session.get_leaf_id` + `harness.dispose`
  asserts `get_leaf_id` resolves BEFORE `dispose`.
- P-329: `runtime._rebind_session` awaited EXACTLY ONCE per
  `_handle_switch_session` / `_handle_fork` / `_handle_clone` call
  (NOT twice per Pi belt-and-braces).
- W0 fixture `pi_runtime_wire_734e08e.json` immutable + SHA-pinned.

### Phase 4 RPC milestone

Aelix Phase 4 RPC roster matches Pi 29/29 commands at SHA
`734e08e`. **No DEFERRED items remain in the RPC dispatch table.**
This is the LAST RPC sprint for Phase 4 — every Pi `RpcCommand`
discriminator now routes through a real handler with a Pi-shape
wire envelope. The cascade pin allowlists in 4.4 / 4.6 / 4.8 / 4.9
/ 4.10 / 4.11 / 4.12 trace the count progression `9 → 12 → 13 → 22
→ 24 → 26 → 26 (foundation) → 29` to the closure pin.

### Sprint 6h₅+ carry-forward (Pi parity items NOT in RPC dispatch)

These are runtime / extension polish items that have no Pi RPC
discriminator at SHA `734e08e`. They surface via in-process APIs or
TUI commands, not the JSONL wire. Sprint 6h₅+ picks them up
incrementally without re-touching the RPC roster.

- **P-307** — `session_shutdown` extension event emit from
  `AgentHarness.dispose()` (Pi `agent-session-runtime.ts:366-373`
  emits through `_session` before disposal; Aelix
  `AgentHarness.dispose()` at `harness/core.py:1961-1976` does not).
  Surface: extension event hook payload.
- **P-308** — Real `session_before_switch` /
  `session_before_fork` extension cancel hooks (currently
  `_emit_before_switch` / `_emit_before_fork` return no-op `False`).
  Pi `agent-session-runtime.ts:115-130` / `:132-147` emits hook
  events that may return `cancelled=True`. Surface: extension event
  hook payload.
- **P-314** — `with_session: Callable[[ReplacedSessionContext],
  Awaitable[None]] | None = None` 2-stage callback for
  `finishSessionReplacement`. Surface: in-process API on
  `AgentSessionRuntime`.
- **P-315** — `set_rebind_session` /
  `set_before_session_invalidate` optional-cb signature widening
  (Pi accepts `undefined` to clear). Surface: in-process API on
  `AgentSessionRuntime`.
- **`assertSessionCwdExists`** Pi parity (cwd-on-disk validation
  before session swap). Pi `session-cwd.ts` checks the cwd actually
  exists on disk before completing the swap. Aelix surfaces the
  equivalent error implicitly through `SessionError("not_found")`
  / `SessionError("storage")` when `repo.open` fails. Surface:
  runtime validation step (no RPC visible diff).
- **`previousSessionFile`** / **`sessionStartEvent`** tracking
  (extension event payload). Pi emits a session-start event
  carrying the previous session file path on replace. Aelix does
  not yet emit this event. Surface: extension event hook payload.
- **Pi `forkFrom` cross-cwd import** — Pi can fork from a session
  whose cwd differs from the runtime's current cwd. Aelix
  `ForkOptions.cwd` currently inherits from the source metadata.
  Surface: in-process API + ForkOptions extension. (No RPC wire
  exists today.)
- **Pi `setup` callback in `new_session`** — Pi
  `agent-session-runtime.ts:226-229` accepts a `setup` callback
  invoked after the new session is constructed. Aelix exposes only
  `parent_session` as a keyword. Surface: in-process API on
  `AgentSessionRuntime.new_session`.
- **`import_from_jsonl` runtime body** — Pi
  `agent-session-runtime.ts:329-364` is implementable but has no
  RPC `RpcCommand` discriminator at SHA `734e08e`. The Pi call
  site is the TUI `/import` command which doesn't go through RPC.
  Aelix STAYS STUBBED with `NotImplementedError`. Surface: in-
  process API (when a TUI surface is built).

### Cumulative carry-forwards from earlier ADRs (still open)

- **From ADR-0078 (Sprint 6h₄b):**
  - Pi HTML visual fidelity (CSS framework, syntax highlighting,
    responsive layout — Sprint 6h₅+).
  - `ImageContent` rendering in HTML export — Sprint 6h₅+.
  - `outputPath` default resolution rules (Pi
    `export-html.ts:273-277` — currently match; any future
    divergence re-opens).
- **From ADR-0074 (Sprint 6h₃) / ADR-0073:**
  - `_get_context_usage_safe` real implementation (P-282 — model
    registry + per-turn token tracking + last-assistant message
    tokens).
  - Live `session_id` read via session manager (P-291).
  - Pi-source-grep verification tooling (P-286).

### Items CONFIRMED dropped (per ADR-0080)

- **P-313 `HarnessFactory` 4-field refresh — DROPPED.** Aelix
  harness rebuild encapsulates services + diagnostics +
  model_fallback_message INSIDE the new harness construction (the
  factory closure carries the application's template options). The
  Pi 4-field `apply()` shape was an artifact of Pi's session-swap
  pattern; harness-rebuild makes it redundant. **Not** in
  Sprint 6h₅+ carry-forward.

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₄b (start of 6h₄c) | 26 | 3 | 29 |
| Sprint 6h₄c (this ADR) | **29** | **0** | **29** |

**PHASE 4 RPC ROSTER CLOSED.**

## Consequences

- **Aelix RPC dispatch matches Pi 29/29** at SHA `734e08e`. The
  forward-compat clause holds with a stronger contract: any PR
  that breaks `SUPPORTED_COMMANDS == RPC_COMMAND_TYPES` trips the
  closure pin.
- **`DEFERRED_COMMANDS` is empty.** The Sprint 6d through Sprint
  6h₄a accumulation of deferred handlers is fully drained. The
  ADR-0058 `parent_session` carry-forward (Sprint 6d) CLOSES via
  P-330. Sprint 6h₄a / 6h₄b carry-forward of session-tree
  commands CLOSES via P-323 ~ P-331.
- **Phase 4 RPC milestone reached** — this is the LAST RPC sprint
  for Phase 4. All future Phase 4 work is runtime / extension
  polish (carry-forward roster above), not new RPC handlers.
- **Closure pin establishes 5 strict invariants beyond count
  parity**: 4-entry `RUNTIME_HOST` arity class, 2 wire shape
  invariants (fork text-omission + clone text-drop), 1 ordering
  invariant (leaf_id pre-capture), 1 single-rebind invariant
  (P-329), and 1 removed-seam invariant (`_apply_for_test`). Any
  PR weakening any of them trips.
- **Runtime layer fully populated** — 3 of 4 stub bodies filled
  via `JsonlSessionRepo` integration. `import_from_jsonl` stays
  stubbed (no RPC surface today) per the Sprint 6h₅+ roster
  above.
- **The cascade closure pins** (4.4 / 4.6 / 4.8 / 4.9 / 4.10 /
  4.11 / 4.12) are updated with the new SUPPORTED=29 / DEFERRED=0
  counts so the historical record stays consistent.

## References

- ADR-0079 — Sprint 6h₄c wiring decisions (sibling ADR — runtime
  body fills + 3 new handlers + W4 MINOR cleanups + W5 ratified
  drift).
- `tests/pi_parity/test_phase_4_13_strict_superset.py` — closure
  pin (lock 29 / 0 / 29 + handler / wire / ordering / rebind /
  seam invariants).
- `tests/pi_parity/fixtures/pi_runtime_wire_734e08e.json` — W0
  fixture for Pi runtime wire body verification.
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:563-589`
  (3 session-tree case sites — verified at SHA `734e08e`).
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:277-282`
  (Pi `new_session` handler — Sprint 6d stub replacement target).

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₄c row this sprint).
- ADR-0079 — Sprint 6h₄c wiring decisions + W4 MINOR cleanups
  (sibling ADR).
- ADR-0078 — Sprint 6h₄b Phase 4.12 closure pin + Sprint 6h₄c
  wiring carry-forward (amended this sprint with foundation →
  wiring complete note).
- ADR-0077 — `AgentSessionRuntime` Pi port + `rebindSession` seam
  (FOUNDATION-ONLY; this ADR's wiring lands on top).
- ADR-0076 — Sprint 6h₄a Phase 4.11 closure (amended this sprint
  with PHASE 4 RPC CLOSURE note).
- ADR-0058 — Sprint 6d RPC mode + DEFERRED_COMMANDS introduction
  (`parent_session` rejection branch CLOSES here).
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).
- ADR-0032 — Sprint workflow + W4/W5 audit mandatory gate.

## Phase

Sprint 6h₄c / Phase 4.13 / W6 (shipped — **PHASE 4 RPC ROSTER
CLOSED**).
