# 0074. Sprint 6h₃ Phase 4.10 — Strict Superset Closure

Status: Accepted (Sprint 6h₃ / Phase 4.10 / W6 shipped)
Date: 2026-05-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Phase 4.10 closes the 2-command Pi parity wiring shipped under
ADR-0073 with a strict-superset audit lane (W4 code-review opus +
W5 architect opus Pi parity audit per ADR-0032). The W4/W5 audit
produced **2 BLOCKING + 2 MAJOR + 1 W4 HIGH + 2 W4 MEDIUM** must-fix
items + **5 carry-forwards** to Sprint 6h₄/6h₅. This ADR records the
closure pin invariants + the Sprint 6h₄ carry-forward roster.

## Decision

### Closure pin invariants

`tests/pi_parity/test_phase_4_10_strict_superset.py` locks the
following Pi parity invariants at SHA `734e08e`:

- `SUPPORTED_COMMANDS` len == **24**.
- `DEFERRED_COMMANDS` len == **5**, all owned by ADR-0074.
- The 2 newly wired commands (`get_session_stats` / `export_html`)
  route through real (non-stub) handlers.
- `SessionStats` shape is exactly 10 fields per
  `agent-session.ts:212-223`.
- `SessionStatsTokens` sub-shape is exactly 5 fields.
- `_session_stats_to_dict` emits the Pi-camelCase wire shape;
  `contextUsage` follows Pi's `{tokens, contextWindow, percent}`
  shape (P-275).
- Aggregator algorithm matches Pi byte-for-byte —
  `totalMessages = len(messages)` (P-276); per-message
  `tokens.{input,output,cacheRead,cacheWrite}` accumulation +
  `usage.cost.total` summation across assistant messages.
- `export_html` produces a syntactically valid HTML5 document.
- Response wire shape is exactly `{path: str}`.
- 5 remaining deferred commands all cite ADR-0074 as
  deferred-owner.
- Pi fixture line numbers match the W5-audited line ranges
  (`:553-556` / `:558-561` / `:2901-2945`).

### Sprint 6h₄ carry-forward roster (5 session-tree commands)

Each remains in `DEFERRED_COMMANDS` with the Pi `RpcErrorResponse`
stub error message citing `ADR-0074`:

- **`switch_session`** — Pi `rpc-mode.ts:528-535`. Requires porting
  Pi `AgentSessionRuntime.switchSession`. Aelix has no
  `AgentSessionRuntime` yet; the runtime host layer is part of the
  Sprint 6h₄ scope.
- **`fork`** — Pi `rpc-mode.ts:537-544`. Requires
  `AgentSessionRuntime.fork`. Same runtime-host blocker as
  `switch_session`.
- **`clone`** — Pi `rpc-mode.ts:546-557`. Requires
  `SessionManager.getLeafId` (lookup the most-recent entry in the
  session tree) + `AgentSessionRuntime.fork(at)` (fork at a
  specific entry).
- **`get_fork_messages`** — Pi `rpc-mode.ts:563-566`. Requires
  porting `session.getUserMessagesForForking` — reads the user
  messages that would be replayed if the session forked here.
- **`get_last_assistant_text`** — Pi `rpc-mode.ts:568-571`. Requires
  porting `session.getLastAssistantText` — convenience accessor for
  TUI / IDE clients.

### Sprint 6h₄/6h₅ carry-forward — non-command items

- **`rebindSession` seam (P-126 Sprint 6f carry-forward)** —
  required by `switch_session` / `fork`. Pi `AgentHarness` exposes
  `rebindSession(newSession)` to swap the active session without
  tearing down the loop; Aelix has no equivalent.
- **`_get_context_usage_safe` real implementation (P-282)** —
  Sprint 6h₃ ships the `None`-returning stub. The real
  implementation reads the model registry (Sprint 6f) + per-turn
  token tracking + last-assistant message tokens. Owner ADR TBD
  in Sprint 6h₄.
- **Pi HTML visual fidelity + session-tree entry source (P-280)**
  — full CSS framework, syntax highlighting, responsive layout,
  image rendering, `sm.getEntries()` source. Sprint 6h₅+ per
  ADR-0073 minimal-renderer scope decision.
- **`session_id` live-read (P-291)** — current implementation
  reads `self._state.session_id`; Pi reads via the session manager
  (could drift if `rebindSession` is called). Sprint 6h₄+.
- **Pi-source-grep verification (P-286)** — the W5 line-citation
  audit was manual; Sprint 6h₄+ should land a tool that
  cross-references every `:LLL-LLL` line citation against the
  pinned Pi SHA in CI.

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₂ (start of 6h₃) | 22 | 7 | 29 |
| Sprint 6h₃ (this ADR) | **24** | **5** | 29 |

## Consequences

- **24 of 29** Pi RpcCommand discriminators live.
- The 5 remaining session-tree commands + Pi gaps (HTML visual
  fidelity, real `_get_context_usage_safe`, `rebindSession` seam,
  live `session_id` read, Pi-source-grep verification) are formally
  tracked under this ADR's carry-forward roster.
- The forward-compat clause holds: any PR landing a deferred
  command MUST drop it from `DEFERRED_COMMANDS` in the same PR
  (enforced by the closure pin).
- The `SessionStats` wire shape is now observable by RPC clients
  with full Pi parity — token totals, cost, per-role counts,
  optional `contextUsage` all match the Pi
  `agent-session.ts:212-223` interface.
- Default `export_html` output paths land in cwd-relative
  `aelix-session-<basename>.html` files instead of the W2 tempfile
  — matches Pi `export-html.ts:273-277` exactly so RPC clients can
  predict the path.

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₃ row).
- ADR-0073 — 2-command harness + handler additions + minimal HTML
  emitter.
- ADR-0072 — Sprint 6h₂ Phase 4.9 closure (precursor pattern).
- ADR-0070 — Sprint 6h₁ Phase 4.8 closure (precursor pattern).

## Phase

Sprint 6h₃ / Phase 4.10 / W6 (shipped).

## Line-citation correction (Sprint 6h₄a W0 verification)

ADR-0074 cited `rpc-mode.ts:563-566` (`get_fork_messages`) and
`:568-571` (`get_last_assistant_text`). Sprint 6h₄a W0 fetched the
Pi source at SHA `734e08e` and verified actual lines:

- `get_fork_messages` → `rpc-mode.ts:591-594`
- `get_last_assistant_text` → `rpc-mode.ts:596-599`

Sprint 6h₄a closure pin
(`tests/pi_parity/test_phase_4_11_strict_superset.py`) locks the
verified values. P-293 captures the drift. No behavioural
consequence — the ADR's algorithmic content (defer to Sprint 6h₄
since runtime port required) remains valid; only line numbers
are corrected.
