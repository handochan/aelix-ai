# 0076. Sprint 6h₄a Phase 4.11 — Strict Superset Closure

Status: Accepted (Sprint 6h₄a / Phase 4.11 / W6 shipped)
Date: 2026-05-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Phase 4.11 closes the 2-command read-only session-navigation wiring
shipped under ADR-0075 with a strict-superset audit lane (W4
code-review opus + W5 architect opus Pi parity audit per ADR-0032).
The W4/W5 audit returned a **CLEAN verdict** — zero BLOCKING /
MAJOR / MINOR findings; only INFO observations (P-299/P-300/P-301)
mapping to documented Aelix-additive divergences
(P-294/P-295/P-296). No code changes resulted from W4 or W5.

This ADR records the closure pin invariants + the Sprint 6h₄b
carry-forward roster. ADR-0076 is the parent owner of the 3
remaining session-tree commands deferred to Sprint 6h₄b.

## Decision

### Closure pin invariants

`tests/pi_parity/test_phase_4_11_strict_superset.py` locks the
following Pi parity invariants at SHA `734e08e`:

- `SUPPORTED_COMMANDS` len == **26**.
- `DEFERRED_COMMANDS` len == **3**, all owned by ADR-0076.
- `SUPPORTED ∪ DEFERRED == RPC_COMMAND_TYPES` preserved at 29.
- The 2 newly wired commands (`get_fork_messages` /
  `get_last_assistant_text`) route through real (non-stub)
  handlers.
- `ForkPointInfo` frozen dataclass present at
  `aelix_agent_core/harness/_fork_point.py` with fields
  `(entry_id: str, text: str)`.
- `get_fork_messages` wire shape: `{messages: ForkPointInfo[]}`
  serialized as `[{"entryId": ..., "text": ...}]` (Pi-camelCase).
- `get_last_assistant_text` wire shape: `{text: str}` when present,
  `{}` (empty object) when `None` — Pi key-omission parity (P-298
  SYNTHESIS).
- `AgentHarness.get_user_messages_for_forking()` is `async def`
  (P-294 — Aelix `Session.get_entries()` async).
- `AgentHarness.get_last_assistant_text()` filters assistant
  messages where `stop_reason == "aborted" AND len(content) == 0`
  (P-297).
- Pi fixture line numbers match the W0-verified line ranges
  (`:591-594` / `:596-599`).
- 3 remaining deferred commands all cite ADR-0076 as
  deferred-owner.

### Sprint 6h₄b carry-forward roster (3 session-tree commands)

Each remains in `DEFERRED_COMMANDS` with the Pi `RpcErrorResponse`
stub error message citing `ADR-0076`:

- **`switch_session`** — Pi `rpc-mode.ts:601-608` (verify in W0 of
  Sprint 6h₄b — current ADR-0074 cite was `:528-535`, may have
  drifted). Requires porting Pi `AgentSessionRuntime.switchSession`.
  Aelix has no `AgentSessionRuntime` yet; the runtime host layer
  is part of the Sprint 6h₄b scope.
- **`fork`** — Pi `rpc-mode.ts` regular fork variant (verify line
  range in W0 of Sprint 6h₄b). Requires
  `AgentSessionRuntime.fork`. Same runtime-host blocker as
  `switch_session`.
- **`clone`** — Requires `SessionManager.getLeafId()` (lookup the
  most-recent entry in the session tree) +
  `AgentSessionRuntime.fork({position: "at"})` (fork at a specific
  entry). Combines both runtime + session-manager blockers.

### Sprint 6h₄b carry-forward — non-command items

- **`AgentSessionRuntime` full port** — from Pi
  `coding-agent/src/runtimes/agent-session-runtime.ts` (or
  equivalent — verify exact path + LOC in W0 of Sprint 6h₄b).
  Required by all 3 session-tree commands. The runtime is the
  layer that owns the active `Session` reference and exposes
  `switchSession` / `fork` / `fork({at})` to the RPC dispatcher
  without requiring the dispatcher to know how to construct or
  swap sessions directly.
- **`SessionManager.getLeafId()` port** — required by `clone`.
  Reads the most-recent entry in the current session tree.
  Aelix `Session.get_entries()` already exists (async); leaf-id
  selection is a thin reducer over that list.
- **`rebindSession()` seam (P-126 — Sprint 6f multi-sprint
  carry-forward)** — required by `switch_session` / `fork`. Pi
  `AgentHarness` exposes `rebindSession(newSession)` to swap the
  active session without tearing down the loop; Aelix has no
  equivalent. This carry-forward has accumulated across Sprint 6f
  → 6h₁ → 6h₂ → 6h₃ → 6h₄a; Sprint 6h₄b is its target close-out
  sprint.

### Cumulative carry-forwards from earlier ADRs (still open)

- **From ADR-0074 (Sprint 6h₃):**
  - Pi HTML visual fidelity (CSS framework, syntax highlighting,
    responsive layout — Sprint 6h₅+).
  - `ImageContent` rendering in HTML export — Sprint 6h₅+.
  - Pi exact `outputPath` default resolution rules (current Aelix
    impl matches Pi `export-html.ts:273-277`; any future
    divergence would re-open).
  - `_get_context_usage_safe` real implementation (P-282 — model
    registry + per-turn token tracking + last-assistant message
    tokens). Sprint 6h₄b candidate.
  - Live `session_id` read via session manager (P-291) —
    Sprint 6h₄b candidate.
  - Pi-source-grep verification tooling (P-286) — Sprint 6h₄+.
- **From ADR-0073 (Sprint 6h₃):**
  - `_get_context_usage_safe` real implementation (same as
    above — tracked under ADR-0073 carry-forward).

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₃ (start of 6h₄a) | 24 | 5 | 29 |
| Sprint 6h₄a (this ADR) | **26** | **3** | 29 |

## Consequences

- **26 of 29** Pi RpcCommand discriminators live.
- The 3 remaining session-tree commands + open Pi gaps
  (`AgentSessionRuntime`, `SessionManager.getLeafId`,
  `rebindSession`, Pi HTML visual fidelity, real
  `_get_context_usage_safe`, live `session_id` read, Pi-source-grep
  verification) are formally tracked under this ADR's
  carry-forward roster.
- The forward-compat clause holds: any PR landing a deferred
  command MUST drop it from `DEFERRED_COMMANDS` in the same PR
  (enforced by the closure pin).
- The W4/W5 CLEAN verdict closes Sprint 6h₄a's audit lane without
  must-fixes — the architect-authored binding spec §0
  pre-resolved every divergence the audit could have raised. This
  is the first sprint since 6a where W4+W5 returned a 0-finding
  verdict, validating the pre-emptive ADR / spec write-up pattern.
- Session navigation read surface (`get_fork_messages` +
  `get_last_assistant_text`) is now observable by RPC clients
  with full Pi parity — TUI / IDE clients can enumerate fork
  points and tail the last assistant text without spawning a
  full session-tree port. Sprint 6h₄b will add the write surface
  (`switch_session` / `fork` / `clone`).

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₄a row).
- ADR-0075 — 2 read-only session-navigation handlers +
  `ForkPointInfo` frozen dataclass + harness methods.
- ADR-0074 — Sprint 6h₃ closure (amended with the P-293
  line-citation correction note this sprint).
- ADR-0072 — Sprint 6h₂ Phase 4.9 closure (precursor pattern).
- ADR-0070 — Sprint 6h₁ Phase 4.8 closure (precursor pattern).
- ADR-0077 — `AgentSessionRuntime` Pi port + `rebindSession` seam
  (Sprint 6h₄b FOUNDATION-ONLY).
- ADR-0078 — Sprint 6h₄b Phase 4.12 closure pin + Sprint 6h₄c
  wiring carry-forward (NEW owner of the 3 deferred session-tree
  commands rebranded from ADR-0076 per spec §D.5).

## Sprint 6h₄b foundation update

Sprint 6h₄b ported Pi `AgentSessionRuntime`
(`packages/coding-agent/src/core/agent-session-runtime.ts:67-374`)
+ the `rebindSession` seam (`rpc-mode.ts:310-349`) as the
FOUNDATION-ONLY layer per ADR-0077. **NO RPC commands wired in
6h₄b.** DEFERRED ownership of `switch_session` / `fork` / `clone`
rebranded from ADR-0076 → ADR-0078 (Sprint 6h₄c) per spec §D.5 —
the cascade pin allowlists in
`tests/pi_parity/test_phase_4_4`/`4_9`/`4_10`/`4_11_strict_superset.py`
were extended with the ADR-0078 prefix in the same W6 commit so the
rebrand stays observably green. Runtime infrastructure
(`AgentSessionRuntime` + 4 stubbed replace APIs + `_apply` /
`_teardown_current` / `_finish_session_replacement` private seam +
passthrough shim) is now ready for 6h₄c handler wiring on top.
ADR-0076 closure pin assertions remain valid — counts unchanged at
26 / 3 / 29.

## Sprint 6h₄c PHASE 4 RPC CLOSURE

Sprint 6h₄c wired the 3 remaining session-tree commands
(`switch_session` / `fork` / `clone`) on top of the 6h₄b runtime
foundation. **PHASE 4 RPC roster CLOSED** at 29 SUPPORTED / 0
DEFERRED / 29 total = full Pi parity for `RpcCommand`. The
ADR-0076 carry-forward roster for the 3 deferred session-tree
commands fully drains; Sprint 6h₅+ carry-forward (runtime /
extension polish — P-307 / P-308 / P-314 / P-315 +
`assertSessionCwdExists` + `previousSessionFile` /
`sessionStartEvent` + Pi `forkFrom` + Pi `setup` callback +
`import_from_jsonl` real body) is enumerated in ADR-0080.

## Line-citation correction (Sprint 6h₄c W0 verification — P-323)

ADR-0076 carry-forward roster cited `rpc-mode.ts:528-557` for the
3 session-tree handlers. Sprint 6h₄c W0 verified at SHA `734e08e`
puts the actual sites at `:563-589` — ~35 lines off (mirror of
Sprint 6h₄a P-293 drift discovery against the original ADR-0074
estimates). Sprint 6h₄c closure pin
`tests/pi_parity/test_phase_4_13_strict_superset.py` locks the
verified line ranges:

- `switch_session` — `rpc-mode.ts:563-569` (Pi handler body 7 lines)
- `fork` — `rpc-mode.ts:571-577` (Pi handler body 7 lines)
- `clone` — `rpc-mode.ts:579-589` (Pi handler body 11 lines)

No behavioral consequence — ADR-0076 algorithmic content remains
valid; line citations corrected forward via ADR-0079 W0 fixture
+ ADR-0080 closure pin.

## Phase

Sprint 6h₄a / Phase 4.11 / W6 (shipped). Foundation update added
Sprint 6h₄b / Phase 4.12 / W6 (2026-05-21). Phase 4 RPC roster
CLOSED Sprint 6h₄c / Phase 4.13 / W6 (2026-05-21).
