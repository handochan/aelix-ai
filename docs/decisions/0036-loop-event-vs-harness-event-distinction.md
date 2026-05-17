# 0036. Loop `AgentEvent` vs Harness `HookEvent` Distinction (F-7)

Status: Accepted (Sprint 2.5 shipped + Sprint 3a expansion code-land)

## Phase 2.1.1 Update (Sprint 3a shipped, 2026-05-17)

Sprint 3a code-lands the type-level distinction:

- `AgentEventName` Literal (10 loop names) is added to
  `harness/hooks.py` as the loop projection alias.
- `AgentHarnessEventName` Literal (18 own-event names) is added to
  `harness/hooks.py` as the Pi-citation-friendly alias for Pi's
  `AgentHarnessOwnEvent` union.
- `HookEventName` (28 names) remains the public union and is constructed
  as the disjoint union of the two aliases (verified by
  `tests/pi_parity/test_hook_event_name_literal_pi_parity.py`).
- `_to_hook_event` is refactored to `match event.type:` + `assert_never`
  per ADR-0030. The return type changes from `HookEvent | None` to
  `HookEvent` (every loop event has a 1:1 hook projection).

## Context

F-7 (Sprint 1 re-eval) flagged that ADR-0017 conflates two distinct concepts:

1. **Loop events** — emitted by the bare `agent_loop()`
   (`packages/aelix-agent-core/src/aelix_agent_core/types.py:202-213`);
   10 variants, all stream-level.
2. **Harness events** — emitted by `AgentHarness`
   (`packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py:54-71`);
   16 today, ~28 in Phase 2.1.

Pi makes the same split explicitly:

- **Loop `AgentEvent`** at `packages/agent/src/types.ts:275-295` (SHA
  `734e08e…`): 10 variants identical to Aelix's set.
- **Harness `AgentHarnessEvent`** at `packages/agent/src/harness/types.ts:551-554`:
  `AgentEvent | AgentHarnessOwnEvent` (a 17-member own-event union covering
  lifecycle + provider + session + selector events).

Aelix code already implements the split (see :func:`_to_hook_event`
projection in `harness/core.py` — function-name anchor used in lieu of a
line range to avoid future line-drift), but no ADR cements the design —
ADR-0017 reads as if there's one big event union, which has caused
confusion in Phase 1.x critic-pass reviews.

## Decision

Affirm the two-tier model **explicitly**:

1. **`AgentEvent`** (`aelix_agent_core.types`) is the loop's stream-level
   union. Listeners subscribed via `Agent.subscribe()` or
   `AgentHarness.subscribe()` receive these. Phase 1.4 ships unchanged at 10
   events. This union mirrors Pi `AgentEvent` (`types.ts:275-295`).

2. **`HookEvent`** (`aelix_agent_core.harness.hooks`) is the harness's
   lifecycle/extension union. Handlers registered via `HookBus.on()` receive
   these. Phase 1.4 ships unchanged at 16; Phase 2.1 (ADR-0017) grows to ~28
   matching Pi's `AgentHarnessOwnEvent` set.

3. **Projection** — `_to_hook_event(event: AgentEvent) -> HookEvent | None`
   (see :func:`_to_hook_event` in `harness/core.py`) is the **single,
   canonical** translation point. A function-name anchor is used instead
   of a line range so the citation does not drift as the file evolves.
   Hook-only events (e.g. `tool_call`, `tool_result`, `before_agent_start`)
   are emitted directly by the harness, not via projection. Loop-only
   events with no hook counterpart return `None`.

4. **Naming** — Aelix uses `HookEvent` where Pi uses `AgentHarnessEvent`. We
   retain `HookEvent` for backward compatibility with ADR-0011 / ADR-0017
   and the existing public API. A type alias
   `AgentHarnessEvent = HookEvent` MAY be added in Phase 2.1 for Pi-citation
   clarity (not in Phase 1.4 scope).

5. **ADR-0017 amendment** — when ADR-0017 lands as Accepted in Phase 2.1, it
   MUST cross-reference this ADR and explicitly state that its 28-event
   catalogue concerns `HookEvent`, leaving the 10-event `AgentEvent` set
   untouched.

## Consequences

- Future contributors can no longer confuse "add a new event" between loop
  and harness — the ADR makes the question "is this stream-level or
  lifecycle-level?" explicit and answerable.
- ADR-0030 (assert_never) applies to **both** unions independently. Two
  separate exhaustive switches, not one combined one.
- The projection function `_to_hook_event` carries a docstring referencing
  this ADR (one-line addition; no behavior change).
- Pi parity tests under ADR-0029 split into `test_loop_event_set_matches_pi`
  and `test_hook_event_set_matches_pi_harness_own_event` (Phase 2.1+ work).

## Related

- ADR-0011 — Hook Event Catalogue v1 (the 16 hook events; Phase 1.2 temporary).
- ADR-0017 — Full Hook Event Catalogue v2 (Phase 2.1 expansion;
  this ADR is its companion).
- ADR-0029 — Pi-parity acceptance test harness (will own the split parity
  tests).
- ADR-0030 — assert_never exhaustiveness (this ADR doubles its scope —
  two unions instead of one).

## Phase

Sprint 2.5 / Phase 1.4 (model affirmed; HookEvent set expansion to ~28
deferred to Phase 2.1 under ADR-0017).
