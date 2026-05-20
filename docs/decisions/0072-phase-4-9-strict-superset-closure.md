# 0072. Phase 4.9 Strict Superset Closure

Status: Accepted (Sprint 6h₂ / Phase 4.9 / W6 shipped)
Date: 2026-05-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Phase 4.9 closes the 9-command Pi parity wiring shipped under
ADR-0071 with a strict-superset audit lane (W4 code-review opus +
W5 architect opus Pi parity audit per ADR-0032). The W4 review
produced **6 BLOCKING + 4 MAJOR + 3 MINOR** must-fix items; W5
identified 5 additional Pi gaps that require subsystems Aelix
does not yet have. This ADR records the closure pin invariants
+ the Sprint 6h₃ carry-forward roster.

## Decision

### Roster (P-245 ~ P-267)

**Sprint 6h₂ W0 (W1 spec):**

- P-245 — All 9 RpcCommand types already exist in `rpc_types.py`
  (Sprint 6d port).
- P-246 — Harness `steer(text)` / `follow_up(text)` accept new
  `images: list[ImageContent] | None = None` (W6: keyword-only via
  P-263).
- P-247 — `harness.set_thinking_level` exists; `cycle_thinking_level`
  is a thin wrapper (W6: P-254 corrects the guard).
- P-248 — Add `set_steering_mode` / `set_follow_up_mode` (W6: P-261
  documents the Aelix-additive runtime check).
- P-249 — Add `_state.auto_compaction_enabled` /
  `_state.auto_retry_enabled` (both default `True`) +
  `set_auto_compaction_enabled` / `set_auto_retry_enabled`.
- P-250 — `abort_retry` / `abort_bash` are state-flag setters only
  (retry loop + bash cancellation-token threading deferred).
- P-251 — `_decode_images` helper for the RPC wire (W6: P-262
  strict-camelCase + required-field validation).
- P-252 — `_handle_get_state` reads `harness.auto_compaction_enabled`
  (W6: P-264 adds symmetric `auto_retry_enabled` surface).
- P-253 — `_MessageQueue.set_mode` helper (W6: P-265 adds defensive
  validation).

**Sprint 6h₂ W4 code-review:**

- **W4 LOW-1** — `_decode_images` strict (covered by P-262).
- **W4 LOW-2** — `bool(enabled)` coercion retained in
  `set_auto_*_enabled` setters as defensive against buggy adapters.
- **W4 LOW-3** — `typing.cast(QueueMode, mode)` instead of
  `# type: ignore` in `set_steering_mode` / `set_follow_up_mode`.
- **W4 NIT** — `build_dispatch_table` docstring updated to
  "22 supported + 7 deferred = 29 total".
- **W4 NIT** — Deferred handler factory error string drops the
  "Sprint 6d" prefix.
- **W4 NIT** — `tests/pi_parity/test_phase_4_6_strict_superset.py`
  test functions renamed to `_by_sprint_6h_2`.

**Sprint 6h₂ W5 Pi parity audit:**

- **P-254 BLOCKING** (closed) — `cycle_thinking_level` missing
  `supportsThinking()` guard.
- **P-258 BLOCKING** (closed) — Docstring + spec line citations
  wrong (528-635 vs actual 483-547); W0 fixture line numbers
  corrected.
- **P-261 BLOCKING** (closed-with-documented-divergence) — RPC mode
  validation: documented as Aelix runtime port of Pi's TS narrow.
- **P-262 BLOCKING** (closed) — `_decode_images` over-permissive
  (accepts both camelCase + snake_case, silent missing fields).
- **P-263 MAJOR** (closed) — `images` keyword-only marker.
- **P-264 BLOCKING** (closed) — Add `auto_retry_enabled` public
  property + `RpcSessionState` extension.
- **P-265 BLOCKING** (closed) — `_MessageQueue.set_mode` defensive
  validation.

**Sprint 6h₃ carry-forward (deferred per ADR-0072):**

- **P-255 / P-256** — Pi `SettingsManager`
  (`coding-agent/src/core/settings-manager.ts`) disk persistence.
  Aelix has no `SettingsManager` port; mode + auto-mode flags reset
  to constructor default on restart. Sprint 6h₃ ships the port.
- **P-257** — Pi `agent-harness.ts` retry loop with
  `AbortController`. Aelix has no retry loop yet; `retry_aborted`
  is a single-shot flag.
- **P-259** — `queue_update` event payload Pi-shape `string[]` vs
  Aelix `list[UserMessage]` — pre-existing Sprint 3b divergence;
  amplified by images. Fix requires queue storage refactor.
- **P-260** — `steer` / `follow_up` Pi-side
  `_throwIfExtensionCommand` + `_expandSkillCommand` +
  `expandPromptTemplate` expanders. Aelix expanders missing.
- **P-266** — `cycle_thinking_level` sync vs async asymmetry
  (documented Pi divergence — Aelix `set_thinking_level` is async).
- **P-267** — Spec citation tweak — `SettingsManager` upstream
  source.

**Sprint 6h₃ command carry-forward (7 commands):**

- 5 session-tree commands (`switch_session` / `fork` / `clone` /
  `get_fork_messages` / `get_last_assistant_text`).
- 2 session-inspection commands (`get_session_stats` / `export_html`).

Each entry remains in `DEFERRED_COMMANDS` with the Pi `RpcErrorResponse`
stub error message citing `ADR-0072` — the closure pin
(`tests/pi_parity/test_phase_4_9_strict_superset.py`) asserts
`SUPPORTED ∪ DEFERRED == RPC_COMMAND_TYPES` preserved at 29.

### Closure pin invariants

`tests/pi_parity/test_phase_4_9_strict_superset.py` (28 tests):

- `SUPPORTED_COMMANDS` len == **22**.
- `DEFERRED_COMMANDS` len == **7**, all owned by ADR-0072.
- The 9 newly wired commands all route through real (non-stub)
  handlers.
- `cycle_thinking_level` rotation matches Pi (forward off → minimal
  → ... → high → off) and respects the `supportsThinking()` guard.
- `set_steering_mode("invalid")` / `set_follow_up_mode("invalid")`
  raise `ValueError`; the RPC handler surfaces a Pi-shape
  `RpcErrorResponse`.
- `_handle_get_state` reflects real `auto_compaction_enabled` +
  `auto_retry_enabled` state.
- `_decode_images` is strict-camelCase and rejects snake_case +
  missing-field payloads.
- W0 fixture line numbers match the W5-audited Pi line ranges at
  SHA `734e08e`.

### Sprint 6d + 6f closure-pin updates

- `tests/pi_parity/test_phase_4_4_strict_superset.py` —
  `RpcSessionState` 12 → 13 fields (per P-264).
- `tests/pi_parity/test_phase_4_6_strict_superset.py` — count
  assertions DEFERRED 16 → 7 + SUPPORTED 13 → 22 (W4 NIT renames).

## Consequences

- **22 of 29** Pi RpcCommand discriminators live.
- The 7 remaining commands + 5 deferred Pi gaps + 1 spec citation
  tweak are formally tracked under ADR-0072's carry-forward roster.
- The forward-compat clause holds: any PR landing a deferred
  command MUST drop it from `DEFERRED_COMMANDS` in the same PR
  (enforced by the closure pin).
- `_decode_images` strictness narrows the wire surface — a Sprint
  6h₂ adapter sending snake_case `mime_type` now errors instead of
  silently corrupting the queue.
- The `auto_retry_enabled` wire field is now observable by RPC
  clients; the Sprint 6h₃ retry-loop port will consume the flag
  without changing the wire shape.

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₂ row).
- ADR-0071 — 9-command harness + handler additions.
- ADR-0066 — Sprint 6f Phase 4.6 closure (precursor pattern).
- ADR-0070 — Sprint 6h₁ Phase 4.8 closure (precursor pattern).

## Phase

Sprint 6h₂ / Phase 4.9 / W6 (shipped).
