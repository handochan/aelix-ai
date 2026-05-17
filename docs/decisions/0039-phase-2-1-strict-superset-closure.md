# 0039. Phase 2.1 Strict Superset Closure

Status: Accepted (Sprint 3d / Phase 2.1.4 shipped; **W6 amendment** — P-10 closed → Phase 2.1 is now 100% strict Pi-parity superset)
Closure date: **2026-05-17** (initial) / **2026-05-17** (W6 P-10 closure)
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## 1st-principle invariant

> **Aelix Phase 2.1 is a strict Pi-parity superset.** Every Pi-verified event
> and behaviour in the Phase 2.1 scope has a corresponding emit site or
> equivalent in the Aelix runtime, with each documented divergence captured
> as an explicit Aelix-additive ADR clause. Any Pi event that Aelix has not
> yet wired up MUST appear in the `DEFERRED_ALLOWLIST` of
> `tests/pi_parity/test_phase_2_1_strict_superset.py` with its owning ADR.
>
> Top-level principle (binding, all sprints): **"pi agent를 완전 동일하게
> 완벽하게 구현이 1차적 목표입니다. 변형 혹은 추가는 그 다음 중요도입니다."**

## Context

Sprint 3a (Phase 2.1.1) registered the Pi-verified 28-event `HookEventName`
Literal (ADR-0017). Sprint 3b (Phase 2.1.2) landed setter emit sites and
the `next_turn`/`append_message`/pending-write surfaces. Sprint 3c
(Phase 2.1.3) landed the parallel tool-execution dispatcher with the §E
ordering matrix (ADR-0021).

Sprint 3c's W5 Pi-parity audit identified two pre-existing carry-over gaps
that left Phase 2.1 short of a strict Pi-parity superset:

1. `Tool.execute` had no equivalent of Pi's `AgentToolUpdateCallback`, so
   `tool_execution_update` was registered in the catalogue but never emitted
   by the runtime.
2. `_run_loop` / `_execute_tool_calls_*` appended tool-result messages
   without emitting `message_start` / `message_end` for them, diverging from
   Pi's `emitToolResultMessage` helper.

Sprint 3d (Phase 2.1.4) closes both gaps. This ADR records the closure
invariant and the durable regression guard.

## Sprint 3a–3d findings roster (P-1 through P-10)

| ID   | Origin              | Subject                                                   | Resolution |
| ---- | ------------------- | --------------------------------------------------------- | ---------- |
| P-1  | Sprint 3a           | 7-event docs/hooks.md wishlist conflated with shipped types | Dropped — registered exactly the 28 Pi-verified names (ADR-0017). |
| P-2  | Sprint 3a           | Hook error policy ergonomics (Pi `throw` vs Aelix continue) | ADR-0019 v3 — Pi `throw` default + per-handler `error_mode="continue"` opt-in. |
| P-3  | Sprint 3b           | `message_end` replacement reducer (ADR-0018 candidate)    | Pi has no replacement reducer at SHA 734e08e — ADR-0018 deprecated, `message_end` stays observational. |
| P-4  | Sprint 3b           | Setter emit-site truth table (queue_update over-emit suspicion) | Verified Pi only enqueue paths emit `queue_update`; setters do not. Drift fixture pinned (`pi_setter_emit_sites_734e08e.json`). |
| P-5  | Sprint 3c           | Tool execution dispatcher cited stale line ranges         | Citations refreshed to `agent-loop.ts:380-387` / `:446-505` / `types.ts:226-232`. |
| P-6  | Sprint 3c           | Default `tool_execution` mode flip                        | Flipped from `"sequential"` → `"parallel"` (Pi parity); backward-compat caveat in ADR-0021 §I. |
| P-7  | Sprint 3c           | `asyncio.TaskGroup` would auto-cancel siblings on tool error | Reversed to `asyncio.gather(*coros, return_exceptions=False)` — Pi never cancels siblings (ADR-0027). |
| P-8  | Sprint 3c           | `_run_loop` already only appends tool-result messages (no emits) | Confirmed no double-emit risk; helper-owned emit pattern preserved. |
| P-9  | Sprint 3d           | `tool_execution_update` emit site missing + tool-result `message_start/end` missing | Landed `_on_partial` closure + drain semantics in `_execute_and_finalize`; landed `_emit_tool_result_message` helper called from sequential immediate, sequential prepared, and parallel Phase 3 (ADR-0017 + ADR-0021 amendments). |
| P-10 | Sprint 3d (W5 → W6) | `AbortHookEvent` registered in Sprint 3a but never emitted — `AgentHarness.abort()` only emitted `queue_update` | **Closed in Sprint 3d W6.** Landed dedicated emit site in `AgentHarness.abort()` (`harness/core.py`): captures pre-clear snapshots of `_steering_queue` / `_follow_up_queue`, emits `AbortHookEvent(cleared_steer=..., cleared_follow_up=...)` BEFORE `queue_update`. Regression suite: `tests/test_abort_event_emit.py` (3 tests covering populated queues, empty queues, and abort→queue_update ordering). Closure pin `tests/pi_parity/test_phase_2_1_strict_superset.py` moves `abort` from `DEFERRED_ALLOWLIST` into `_HARNESS_OWN_EMIT_SUBSTRINGS`. |
| P-11 | Sprint 4a W1 | Sprint 3b W4 MAJOR-1 added `PendingActiveToolsChangeWrite` based on fabricated Pi claim. **Pi `setActiveTools` (`agent-harness.ts:875-882`) does NOT push pending writes** — verified at SHA `734e08e`. Variant + push site deleted in Sprint 4a; regression test in `test_session_pending_writes_integration.py` prevents reintroduction. ADR-0022 §"Removed claims" documents reversal. |

## Decision

### Closure

With Sprint 3d shipped, the following hold:

- Every Pi loop `AgentEvent` (10 names) has at least one emit site in
  `packages/aelix-agent-core/src/aelix_agent_core/loop.py`.
- Every Pi harness-own event in the Phase 2.1 binding scope has an emit
  site in `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py`
  OR an explicit entry in `DEFERRED_ALLOWLIST` with its owning ADR.
- `tests/pi_parity/test_phase_2_1_strict_superset.py` (E.5 closure pin)
  enforces both halves of the invariant as a regression guard.

The P-11 lockdown test (`test_session_pending_writes_integration.py`) is included
in the closure pin guard — it prevents `PendingActiveToolsChangeWrite` from being
silently reintroduced and ensures the 8-variant `PendingSessionWrite` union matches
the Pi-verified surface.

### Durable regression guard (E.5 closure pin)

`tests/pi_parity/test_phase_2_1_strict_superset.py` is the binding
mechanization of this invariant. It loads
`tests/pi_parity/fixtures/pi_agent_harness_event_names_734e08e.json`
(SHA-pinned per ADR-0034) and fails when:

1. A Pi loop event has no emit site in `loop.py`.
2. A Phase 2.1 harness-own event has no emit site in `harness/core.py`.
3. A Pi own-event is neither emitted in code nor listed in
   `DEFERRED_ALLOWLIST`.
4. A `DEFERRED_ALLOWLIST` entry has gained an emit site (forward-compat
   guard — see §"Forward-compat clause" below).

### Explicit deferred allowlist (post W6 P-10 closure)

The following Pi own-events are intentionally deferred. Each entry cites
the owning ADR that will land its emit site. **All remaining entries are
Phase 2.2 (Session Manager) or Phase 4 (Provider adapter) owned — zero
Phase 2.1 own-events remain deferred.**

| Pi event name              | Owning ADR / phase                                                    |
| -------------------------- | --------------------------------------------------------------------- |
| `before_provider_request`  | ADR-0038 (Phase 4 provider adapter)                                   |
| `before_provider_payload`  | ADR-0038 (Phase 4 provider adapter)                                   |
| `after_provider_response`  | ADR-0038 (Phase 4 provider adapter)                                   |
| `session_before_compact`   | ADR-0022 / ADR-0023 (Phase 2.2 Session Manager + Compaction)          |
| `session_compact`          | ADR-0022 / ADR-0023 (Phase 2.2 Session Manager + Compaction)          |
| `session_before_tree`      | ADR-0022 / ADR-0023 (Phase 2.2 Session Manager + Compaction)          |
| `session_tree`             | ADR-0022 / ADR-0023 (Phase 2.2 Session Manager + Compaction)          |

P-10 closure (Sprint 3d W6): `abort` was previously listed here as the
last Phase 2.1 deferred entry. The emit site landed in `harness/core.py`
`AgentHarness.abort()` and the entry moved into the closure pin's
`_HARNESS_OWN_EMIT_SUBSTRINGS` map. Phase 2.1 is now 100% strict
Pi-parity superset; the only deferred events are in Phase 2.2 / Phase 4
scope.

### Forward-compat clause

Future Pi events introduced upstream from this SHA MUST follow the same
contract:

1. Land the emit site in the **same sprint** that adds the event to the
   `HookEventName` Literal, OR
2. Add the event to `DEFERRED_ALLOWLIST` with its owning ADR (or Draft
   ADR) reference in the same PR.

Adding the type without doing either is a strict-superset contract
violation — the closure pin will fail.

Conversely, when a deferred entry's emit site lands, the same PR MUST drop
the entry from `DEFERRED_ALLOWLIST`. The
`test_deferred_allowlist_entries_remain_unemitted` test enforces this.

Sprint 4b (Phase 2.2.2) closed the 4 session_* emit sites tracked here as deferred. ADR-0040 documents Phase 2.2 closure with full P-11~P-20 roster.

The 4 `session_*` entries (`session_before_compact`, `session_compact`,
`session_before_tree`, `session_tree`) have been removed from `DEFERRED_ALLOWLIST`
in `tests/pi_parity/test_phase_2_1_strict_superset.py` — their emit sites landed
in `AgentHarness.compact()` and `AgentHarness.navigate_tree()` (`harness/core.py`).
The remaining `DEFERRED_ALLOWLIST` contains only the 3 Phase 4 entries below.

## Consequences

- Phase 2.1 ADRs `Accepted`: 0017, 0019, 0021, 0027, 0030, 0034, 0036,
  **0039 (this ADR)**.
- The closure pin becomes the single mechanical truth for Phase 2.1
  Pi-parity superset claims. Future Phase 2.1 amendments must keep the
  fixture, the allowlist, and the emit-site map mutually consistent.
- **W6 amendment:** The P-10 `abort` finding is closed in the same
  Sprint 3d cycle that surfaced it — `AgentHarness.abort()` now emits a
  dedicated `AbortHookEvent` with pre-clear `cleared_steer` /
  `cleared_follow_up` snapshots, matching Pi `agent-harness.ts`
  `abort()`. Phase 2.1 is now 100% strict Pi-parity superset: every Pi
  own-event in Phase 2.1 binding scope has an emit site in
  `harness/core.py`.
- The roster of P-1 through P-10 is preserved here as the canonical
  audit trail for Phase 2.1 Pi-parity work.

## Relationships

- Supersedes the implicit "Phase 2.1 is strict superset" claim made in
  ADR-0017 Sprint 3a context — now mechanically enforced.
- Cross-references: ADR-0017 (catalogue), ADR-0019 (error policy v3),
  ADR-0021 (parallel exec ordering matrix), ADR-0027 (gather vs
  TaskGroup), ADR-0029 (Pi-parity test harness), ADR-0030 (assert_never
  exhaustiveness), ADR-0034 (Pi SHA pin), ADR-0036 (loop vs harness
  event distinction).
- Forward dependencies: ADR-0022 / ADR-0023 (session emit sites — Phase
  2.2), ADR-0038 (provider emit sites — Phase 4).
