# 0145. P0 #7 Wave 3 — `message_end` Replacement Reducer (reverts ADR-0018)

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)
Supersedes: ADR-0013, ADR-0018 (both based on a layer mix-up — see below)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context — the layer mix-up

Gap-inventory **P0 #7 item 5**, Wave 3 of the recon's 3-wave split (Wave 1 = ADR-0143, Wave 2 =
ADR-0144). ADR-0013 (Phase 1.2) made `message_end` observational; ADR-0018 then proposed the pi
replacement reducer but was **Deprecated** on the claim *"Pi has no message_end reducer at SHA
734e08e."* That claim was a **layer mix-up**: pi's *agent-harness* layer indeed has no reducer, but the
reducer lives in pi's **extension-runner** layer — `runner.ts:714` `emitMessageEnd` (role-preserving
sequential replacement), consumed by `agent-session.ts:669-678` via `_replaceMessageInPlace`. Aelix's
extension layer mirrors pi's extension-runner, so the reducer belongs here. Verified verbatim against
the pin before reverting. (`.omc/specs/sprint-p0-7-wave3-message-end-spec.md` captures the full spike.)

## Decision — implement the replacement reducer, frozen-dataclass-safe

### `harness/hooks.py`
- New `MessageEndEventResult { message: AgentMessage | None }` (pi `MessageEndEventResult`).
- `HOOK_RESULT_TYPES["message_end"] = MessageEndEventResult` (was `None`);
  `_REDUCERS["message_end"] = _reducer_message_end` (was `_reducer_observational`).
- `_reducer_message_end` — verbatim pi `emitMessageEnd`: sequential chain rebuilding the event's
  `message` each iteration (handler N sees N-1's replacement); a role mismatch is **logged + skipped**
  (aelix equivalent of pi's `emitError` — it does **not** raise); returns the replaced `AgentMessage`
  or `None` (= pi `undefined`, "no replacement"). `MessageEndHandler` return type updated.

### `harness/core.py` emit closure (reorder)
The closure now returns `AgentMessage | None`. For `message_end` it **reorders** vs the old
observational persist-then-emit path: (1) run the hook reduction FIRST; (2) compute the final message
(replacement when valid and not the same object, else original); (3) persist the **replacement** via
`session.append_message`; (4) fire local listeners with the **original** event (observers stay
observational); (5) `return reduced` so the loop applies it — and crucially **does not** re-run the
generic `_to_hook_event` fan-out (for message_end that fan-out *is* the reduction). Every other event
type is unchanged and returns `None`.

### `loop.py` — loop-return + identity swap at EVERY message_end site
`AgentMessage` is a frozen dataclass, so pi's in-place `_replaceMessageInPlace` does not translate.
Instead `AgentEventSink` returns `AgentMessage | None` and the loop swaps the object by identity. pi
applies the replacement at **every** message_end event, so the loop does too — via a shared
`_is_replacement(replacement, original)` guard (not-None, not-identical, same-role):
- **prompt** messages (`agent_loop`): swap `new_messages[i]` + `current_context.messages[base+i]`;
- **steering/follow-up** (`_run_loop`): swap `msg` before it is appended to both lists;
- **assistant** done/error (`_stream_assistant_response`): swap `context.messages[partial_index]` (or
  the tail) + the returned `final`;
- **tool-result** (`_emit_tool_result_message` now returns the possibly-replaced message; both call
  sites reassign `msg` before appending to the batch).
The swapped object flows through `new_messages` → `core.py:_state.messages.extend(new_messages)` (the
no-session writeback) and is persisted via the closure (the session path).

### Tests + ADRs
- Replaced the old pin test (`test_message_end_remains_observational.py`, deleted) with
  `test_message_end_replacement_reducer.py`: registry wiring; pure-reducer (chain / role-mismatch
  skip+warn+no-raise / None); end-to-end **no-session** (replacement in loop return + `state.messages`,
  by identity); end-to-end **with-session** (the PERSISTED entry is the replacement — proves the
  reorder); and no-regression (no handler → original persisted; emit returns `None` → original kept).
- `test_session_message_end_wiring.py` updated for the reorder: the at-emit persisted-entry count is now
  `[0, 1]` (reduce-before-persist) instead of `[1, 2]` (persist-before-emit); final persisted state
  unchanged (2 entries).
- This ADR supersedes ADR-0013 + ADR-0018.

## Verification

- Implemented + 6-lens review + an independent re-review (the first review's output was lost to a
  network drop). Manual line-by-line audit of hooks.py/core.py/loop.py confirmed pi-fidelity, the
  persistence reorder, the every-site loop-return, role-preservation, and no double hook-emit.
- Full gate: **3450 passed, 1 skipped** (+11 tests); only the 3 pre-existing `test_append_system_prompt`
  AGENTS.md cwd-coupling failures remain (unrelated). ruff clean.
- Diff confined to `harness/hooks.py`, `harness/core.py`, `loop.py`, `harness/__init__.py`, and tests —
  no contract/rpc/mcp shape change.

## Consequence

`message_end` is no longer observational: an extension can rewrite/redact a finalized message (same
role) and the replacement is persisted + carried into agent state for the next turn — full pi parity.
This is the last item of P0 #7 (extensions-api); with Wave 1 (ADR-0143) + Wave 2 (ADR-0144), P0 #7 is
closed.
