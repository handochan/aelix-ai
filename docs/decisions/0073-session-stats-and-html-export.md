# 0073. Sprint 6h₃ Phase 4.10 — Session Stats + HTML Export Wire Port

Status: Accepted (Sprint 6h₃ / Phase 4.10 / W6 shipped)
Date: 2026-05-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Sprint 6h₂ (ADR-0071/0072) closed Phase 4.9 with **22 supported / 7
deferred** RPC commands. Sprint 6h₃ ports the next 2 commands from
Pi `rpc-mode.ts` at SHA `734e08e`:

- `get_session_stats` (Pi `rpc-mode.ts:553-556` → `agent-session.ts:2901-2945`)
- `export_html` (Pi `rpc-mode.ts:558-561` → `coding-agent/src/core/export-html/`)

After Sprint 6h₃ the dispatcher matches Pi on **24 of 29** commands;
the remaining 5 session-tree commands (`switch_session` / `fork` /
`clone` / `get_fork_messages` / `get_last_assistant_text`) defer to
Sprint 6h₄ per ADR-0074 — they require porting Pi
`AgentSessionRuntime` + `SessionManager.getLeafId` + `rebindSession`
seam (P-126 Sprint 6f carry-forward).

The W4 code-review + W5 Pi parity audit produced **2 BLOCKING (Pi
parity) + 1 MAJOR (Pi error parity) + 1 MAJOR (Pi-shape default
path) + 1 W4 HIGH (pyright regression) + 2 W4 MEDIUM (dead code) +
1 W6 algorithm fix (totalMessages = len(messages)) + 1 W6 dict
fallback (usage reader)**. Sprint 6h₃ W6 applied every BLOCKING +
MAJOR + W4 fix.

## Decision

### Pi parity decisions

- **`contextUsage` wire shape Pi-correct (P-275 BLOCKING)** —
  emitted as the Pi-shape `{tokens, contextWindow, percent}`
  (`extensions/types.ts` `ContextUsage`), NOT the previously emitted
  `{usedTokens, limitTokens, remainingTokens}`. Aelix
  `ContextUsage` at `extensions/api.py:122-135` already matches Pi
  field names verbatim (snake_case ↔ camelCase mapping).
- **`totalMessages = len(messages)` (P-276 BLOCKING)** —
  `agent-session.ts:2935` reads `state.messages.length`. The W2
  ship summed `userMessages + assistantMessages + toolResults`; for
  the current 3-type universe the numerical result is identical,
  but Pi's algorithm is future-proofed against additive message
  types (custom / bashExecution / compaction). The W6 fix matches
  Pi byte-for-byte.
- **Pi error parity on `export_to_html` (P-279 MAJOR)** —
  `export-html.ts:242-248` raises early on in-memory or empty
  sessions. The harness owns the precondition checks; the
  renderer is a pure writer. The RPC outer dispatcher
  (`_handle_command`) surfaces the harness `RuntimeError` as a
  Pi-shape `RpcErrorResponse`.
- **Pi-shape default `outputPath` (P-281 MAJOR)** —
  `export-html.ts:273-277` defaults to a cwd-relative
  `aelix-session-<basename>.html` (Aelix substitutes `"aelix"` for
  Pi's `APP_NAME`). The W2 ship used `tempfile.NamedTemporaryFile`;
  the W6 fix matches Pi exactly. The harness passes the JSONL
  basename via the new `session_basename` kwarg.
- **`SessionStats` aggregator dict fallback (P-283)** — assistant
  `usage` may arrive as a fully typed `Usage` dataclass (Sprint 6f
  streaming path) **or** as a plain dict (legacy JSONL fixtures +
  provider passthrough). The new `_read(obj, key, default)` helper
  branches on `isinstance(obj, dict)`; otherwise falls back to
  `getattr` so dataclasses + duck-typed objects both work.
- **W4 HIGH (P-292) pyright regression** — `get_session_stats` and
  `export_to_html` originally branched on `hasattr(session,
  "messages")` even though `Session` is a storage wrapper with no
  `.messages` attribute. Pi's `session.messages` corresponds to
  Aelix's in-memory `AgentState.messages` (canonical accessor via
  the `messages` property at line 673). The W6 fix reads
  `self._state.messages` directly, removing the dead branch.
- **W4 MEDIUM-1** — `_handle_export_html` dropped its
  `getattr(cmd, "output_path", None)` indirection in favour of the
  direct attribute access `cmd.output_path`. Dataclass
  `RpcCommandExportHtml` declares the field, so `getattr` is
  pure cargo-cult.
- **W4 MEDIUM-2** — `_export_html.export_html` dropped the
  `path.parent and not path.parent.exists()` tautology — Path
  `parent.mkdir(parents=True, exist_ok=True)` is idempotent and
  always safe.
- **Line citation corrections (P-277/P-278/P-286)** — every
  `:475-478` / `:480-483` / `:2765+` reference (rpc_mode.py
  docstrings + spec + fixture + harness/core.py + closure pin)
  rewritten to the W5-audited `:553-556` / `:558-561` / `:2901-2945`.

### Aelix-additive divergences (deferred to Sprint 6h₅+)

- **HTML renderer reads `state.messages` (LLM context) only.** Pi
  reads `sm.getEntries()` (full session tree including pending /
  branched messages). Sprint 6h₃ ships the minimal LLM-context
  renderer; full session-tree entry source defers to Sprint 6h₅+
  per ADR-0074 carry-forward.
- **Inline CSS in minimal renderer (light styling).** No CSS
  framework, no syntax highlighting, no responsive layout, no
  image rendering. Deferred to Sprint 6h₅+ per ADR-0074.
- **`_get_context_usage_safe` stub returns `None`.** Pi
  `getContextUsage` reads the model registry + per-turn token
  tracking; Aelix wiring lands Sprint 6h₄+. The stub returning
  `None` matches Pi's `undefined` short-circuit when the model is
  unknown (Pi `agent-session.ts` — `getContextUsage` returns
  `undefined`). Sprint 6h₄ ADR will own the real implementation.

## Roster

**W0/W1 (binding spec):**

- P-268 — Pi `SessionStats` shape (10 fields, optional `contextUsage`).
- P-269 — Pi `get_session_stats` handler reads
  `session.getSessionStats()`; aggregator counts message types +
  sums `usage.cost.total`.
- P-270 — Pi `export_html` handler signature `{ path: string }`.
- P-271 — Aelix `harness._session` access pattern.
- P-272 — `tokens.cacheRead` / `cacheWrite` aggregation.
- P-273 — `contextUsage` may be `None` when model unknown.
- P-274 — `_handle_get_state` already provides some overlap;
  `get_session_stats` is the inventory deep-dive.

**W5/W4 must-fixes applied this sprint:**

- P-275 BLOCKING — `contextUsage` wire shape Pi-correct.
- P-276 BLOCKING — `totalMessages = len(messages)`.
- P-277 — Update line citation `475-478` → `553-556`.
- P-278 — Update line citation `480-483` → `558-561`.
- P-279 MAJOR — Pi error parity on `export_to_html`.
- P-281 MAJOR — Default `outputPath = aelix-session-<basename>.html`.
- P-283 — Aggregator usage dict fallback.
- P-292 HIGH (W4) — pyright regression fix (drop dead
  `hasattr(session, "messages")` branch).

**Sprint 6h₄/6h₅ carry-forward (deferred per ADR-0074):**

- P-280 — Pi HTML visual fidelity + session-tree entry source.
- P-282 — `_get_context_usage_safe` real implementation (model
  registry + per-turn token tracking).
- P-285 — README + ADR-0034 amend rows (this commit covers).
- P-286 — Pi-source-grep verification.
- P-291 — `session_id` live-read.

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₂ (start of 6h₃) | 22 | 7 | 29 |
| Sprint 6h₃ (this ADR) | **24** | **5** | 29 |

## References

- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:553-561` (handlers)
- `packages/coding-agent/src/core/agent-session.ts:212-223` (SessionStats)
- `packages/coding-agent/src/core/agent-session.ts:2901-2945` (getSessionStats)
- `packages/coding-agent/src/core/export-html/` (HTML emitter subsystem)
- `packages/coding-agent/src/core/export-html/export-html.ts:242-248` (precondition raises)
- `packages/coding-agent/src/core/export-html/export-html.ts:273-277` (default path)
- `packages/coding-agent/src/extensions/types.ts` (ContextUsage)

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₃ row).
- ADR-0072 — Sprint 6h₂ closure (precursor; left 7 deferred).
- ADR-0074 — Sprint 6h₃ closure pin + Sprint 6h₄ carry-forward.

## Phase

Sprint 6h₃ / Phase 4.10 / W6 (shipped).
