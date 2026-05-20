# 0075. Sprint 6h₄a Phase 4.11 — Session Navigation (read-only) RPC Commands

Status: Accepted (Sprint 6h₄a / Phase 4.11 / W6 shipped)
Date: 2026-05-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₃ (ADR-0073/0074) closed Phase 4.10 with **24 supported / 5
deferred** RPC commands. ADR-0074 enumerated the 5 remaining
session-tree commands as Sprint 6h₄ carry-forward: `switch_session` /
`fork` / `clone` / `get_fork_messages` / `get_last_assistant_text`.

Sprint 6h₄a partitions that carry-forward by **runtime requirement**:
the 2 read-only commands (`get_fork_messages` +
`get_last_assistant_text`) are pure session-state reads —
`session.getUserMessagesForForking()` reads `sessionManager.getEntries()`
+ `_state.messages`; `session.getLastAssistantText()` reverse-walks
`_state.messages`. Neither needs `AgentSessionRuntime`. The 3 remaining
session-tree commands (`switch_session` / `fork` / `clone`) defer to
**Sprint 6h₄b** per ADR-0076 — they require porting Pi
`AgentSessionRuntime` + `SessionManager.getLeafId()` + `rebindSession()`
seam (P-126 Sprint 6f multi-sprint carry-forward).

The W4 code-review + W5 Pi parity audit produced a **CLEAN verdict**
(zero BLOCKING / MAJOR / MINOR — only INFO/NIT observations). W5
raised 3 INFO findings (P-299/P-300/P-301) — all map to documented
Aelix-additive divergences (P-294/P-295/P-296) captured in the
binding spec §0. No code changes required from W4/W5; this sprint's
W6 work is ADR + amend + commit only.

## Decision

### Pi parity decisions

- **P-293 — Pi handler line drift captured** —
  ADR-0074 estimated `rpc-mode.ts:563-566` (`get_fork_messages`)
  and `:568-571` (`get_last_assistant_text`). Sprint 6h₄a W0
  verified against pinned SHA `734e08e` and found the actual lines
  at `591-594` and `596-599`. The closure pin
  (`tests/pi_parity/test_phase_4_11_strict_superset.py`) locks the
  verified values. ADR-0074 receives a line-citation correction
  note (see §"Related" below). No behavioural consequence — only
  line numbers are corrected.

- **P-294 — Aelix `Session.get_entries()` async (Pi sync)** —
  Pi `getUserMessagesForForking` reads
  `this.sessionManager.getEntries()` synchronously
  (`agent-session.ts:2871`). Aelix `Session.get_entries()` at
  `session/session.py:109` is `async def`. The harness method
  `AgentHarness.get_user_messages_for_forking()` MUST be `async def`
  to consume it. Internal async-boundary leak; no wire-shape impact.
  Documented Aelix-additive divergence.

- **P-295 — Inline anonymous shape named as frozen dataclass** —
  Pi declares the return type inline at `agent-session.ts:2870` as
  `Array<{entryId, text}>`. No named TS interface. Aelix introduces
  `ForkPointInfo` as a `@dataclass(frozen=True)` at
  `aelix_agent_core/harness/_fork_point.py` for type clarity. Wire
  shape stays Pi-camelCase verbatim: emitter produces
  `[{"entryId": ..., "text": ...}]`. Field names map snake_case
  (`entry_id` / `text`) ↔ camelCase (`entryId` / `text`).

- **P-296 — `_extract_user_message_text` content variant** —
  Pi `_extractUserMessageText` accepts `string | Array<{type, text?}>`
  (`agent-session.ts:2887`). Aelix `UserMessage.content` is always
  `list[TextContent | ImageContent]` — string branch unreachable
  today. Aelix `_extract_user_message_text` walks the list only;
  the string branch is kept as defensive code (matches Pi byte-for-
  byte even though only the list branch fires under the Aelix
  type system).

- **P-297 — `getLastAssistantText` aborted-empty filter** —
  Pi reverse-walks `this.messages` and skips assistant messages
  where `stopReason === "aborted" && content.length === 0`
  (`agent-session.ts:3063-3070`). Aelix `get_last_assistant_text`
  mirrors this verbatim using `self._state.messages` + `reversed()`
  + the same conjunction (`stop_reason == "aborted"` AND
  `len(content) == 0`). Single-message-aborted-empty edge case
  preserved.

- **P-298 SYNTHESIS — Pi key-omission parity preserved** —
  Pi `success(id, "get_last_assistant_text", { text })` with
  `text === undefined` triggers `JSON.stringify` to omit the `text`
  key entirely → `{"data": {}}` on wire. Aelix
  `_handle_get_last_assistant_text` builds the wire payload as
  `data = {"text": text} if text is not None else {}` to preserve
  Pi bit-for-bit. This matches the existing
  `_session_stats_to_dict` undefined-skip pattern shipped in
  Sprint 6h₃ (P-275) for `sessionFile` / `contextUsage`. Closure
  pin asserts empty dict on `None` and `{"text": ...}` on present.

### W5 INFO findings (P-299/P-300/P-301)

All 3 W5 INFO findings map to the Aelix-additive divergences
already documented in this ADR:

- **P-299 (async harness method)** — mirrors P-294 (sync Pi vs
  async Aelix). No fix required; deliberate consequence of
  `Session.get_entries()` being async since Sprint 4a.
- **P-300 (named dataclass vs inline anonymous)** — mirrors P-295
  (frozen `ForkPointInfo` for type clarity). No fix required;
  wire shape is Pi-identical.
- **P-301 (defensive string branch)** — mirrors P-296 (`UserMessage.
  content` is list-only). No fix required; defensive branch
  matches Pi source even though Aelix's type system makes it
  unreachable.

No code changes resulted from W4 or W5. The W6 lane is
documentation + commits only.

### Aelix-additive divergences (deferred / tracked)

- **3 remaining session-tree commands** —
  `switch_session` / `fork` / `clone` defer to Sprint 6h₄b per
  ADR-0076 (they need `AgentSessionRuntime` port +
  `SessionManager.getLeafId()` + `rebindSession()` seam).
- **`AgentSessionRuntime` full port** — from Pi
  `coding-agent/src/runtimes/agent-session-runtime.ts` (or
  equivalent — verify in 6h₄b W0). Required by `switch_session`
  and `fork`.
- **`rebindSession()` seam** — P-126 Sprint 6f multi-sprint
  carry-forward. Pi `AgentHarness` exposes `rebindSession(newSession)`
  to swap the active session without tearing down the loop; Aelix
  has no equivalent.

## Roster

**W0 (binding spec, P-293..P-298):**

- P-293 — Pi handler line drift verified (591-594 / 596-599).
- P-294 — Aelix `Session.get_entries()` async vs Pi sync.
- P-295 — `ForkPointInfo` frozen dataclass naming.
- P-296 — `_extract_user_message_text` content list-only walk
  with defensive string branch.
- P-297 — `getLastAssistantText` aborted-empty filter.
- P-298 SYNTHESIS — Pi key-omission parity for `text` undefined.

**W4/W5 must-fixes applied this sprint:**

- *None.* W4 + W5 returned CLEAN verdict.

**W5 INFO observations (no fix; documented divergences):**

- P-299 — async harness method (maps to P-294).
- P-300 — named dataclass vs inline anonymous (maps to P-295).
- P-301 — defensive string branch (maps to P-296).

**Sprint 6h₄b carry-forward (deferred per ADR-0076):**

- 3 session-tree commands (`switch_session` / `fork` / `clone`).
- Pi `AgentSessionRuntime` port (runtimeHost.switchSession /
  fork / `at` position).
- `SessionManager.getLeafId()` for the `clone` command.
- `rebindSession()` seam (P-126 Sprint 6f carry-forward —
  multi-sprint accumulation).

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₃ (start of 6h₄a) | 24 | 5 | 29 |
| Sprint 6h₄a (this ADR) | **26** | **3** | 29 |

## References

- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:591-594`
  (`get_fork_messages` handler — verified at SHA `734e08e`)
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:596-599`
  (`get_last_assistant_text` handler — verified at SHA `734e08e`)
- `packages/coding-agent/src/core/agent-session.ts:2867-2900`
  (`getUserMessagesForForking` source)
- `packages/coding-agent/src/core/agent-session.ts:2887-2898`
  (`_extractUserMessageText` source)
- `packages/coding-agent/src/core/agent-session.ts:3063-3070`
  (`getLastAssistantText` source)
- `aelix-agent-core/src/aelix_agent_core/harness/_fork_point.py`
  (NEW — `ForkPointInfo` frozen dataclass)
- `aelix-agent-core/src/aelix_agent_core/harness/core.py`
  (`get_user_messages_for_forking` + `get_last_assistant_text` +
  `_extract_user_message_text`)
- `aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py`
  (`_handle_get_fork_messages` + `_handle_get_last_assistant_text`
  + dispatcher entries + DEFERRED 5 → 3)
- `tests/pi_parity/test_phase_4_11_strict_superset.py`
  (closure pin — 26 supported / 3 deferred)
- `tests/pi_parity/fixtures/pi_session_navigation_734e08e.json`
  (W0 fixture — verified lines)

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₄a row).
- ADR-0073 — Sprint 6h₃ session stats + HTML export precursor.
- ADR-0074 — Sprint 6h₃ closure (amended this sprint with the
  line-citation correction note for P-293).
- ADR-0076 — Sprint 6h₄a Phase 4.11 closure pin + Sprint 6h₄b
  carry-forward parent.

## Phase

Sprint 6h₄a / Phase 4.11 / W6 (shipped).
