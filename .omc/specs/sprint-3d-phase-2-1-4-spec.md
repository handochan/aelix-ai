# Sprint 3d · Phase 2.1.4 — Carry-Over Closure (BINDING SPEC)

Status: **Binding** (Architect-authored, READ-ONLY pass)
Author: Architect (Opus, READ-ONLY)
Date: 2026-05-17
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다. 변형 혹은 추가는 그 다음 중요도입니다."**

This sprint closes 2 pre-existing Pi-parity carry-over gaps identified by Sprint 3c W5, making Phase 2.1 a strict Pi-parity superset (Q5 requirement).

---

## §0 — P-9 INVESTIGATION (Pi `update_callback` + `emitToolResultMessage` — VERIFIED at SHA 734e08e)

### Findings

**Q1: Pi `Tool.execute` signature** — `agent/src/types.ts:357-375`:
```typescript
export type AgentToolUpdateCallback<T = any> = (partialResult: AgentToolResult<T>) => void;

execute: (
    toolCallId: string,
    params: Static<TParameters>,
    signal?: AbortSignal,
    onUpdate?: AgentToolUpdateCallback<TDetails>,
) => Promise<AgentToolResult<TDetails>>;
```
**4 params; callback is sync (returns `void`).** Aelix already collapsed `(toolCallId, signal)` into `ToolExecutionContext` — extending that pattern for `on_partial` is Aelix-idiomatic.

**Q2: Pi `executePreparedToolCall` shape** — `agent-loop.ts:604-639`:
- Local `updateEvents: Promise<void>[] = []`
- Callback constructs emit promise and pushes onto array
- `await Promise.all(updateEvents)` runs in BOTH happy and error paths (drain semantics)
- Callback is fire-and-forget at tool surface; runtime guarantees drain before result return
- Hook-handler exceptions during emit escape both paths (Pi has no per-emit catch)

**Q3: Pi `emitToolResultMessage` shape** — `agent-loop.ts:715-718`:
```typescript
await emit({ type: "message_start", message: toolResultMessage });
await emit({ type: "message_end",   message: toolResultMessage });
```
**Both** start AND end emitted. Single source of truth (helper-owned). Called from sequential `:436` and parallel `:498`. NOT from `runLoop`.

**Q4: Sequential path emits `tool_execution_update` too?** Yes — same helper.

**Q5: Other gaps?** No — spot-check confirms only these 2 carry-overs.

### P-9 Verdict
Both pre-existing global Aelix divergences confirmed. Local fixes, no ADR reversal. Pi-parity restoration straightforward.

---

## §A — `Tool.execute` signature (Gap A)

### A.1 Decision: extend `ToolExecutionContext.on_partial`

Aelix already diverged from Pi's 4-arg signature by collapsing `(toolCallId, signal)` into `ToolExecutionContext`. Adding 3rd param `(args, exec_ctx, on_partial)` would inconsistently re-split. **Extend context** instead.

**Backward-compat:** zero existing tools in `aelix-coding-agent/src/.../tools/` (verified). Zero breakage.

### A.2 `ToolExecutionContext` changes (`packages/aelix-ai/src/aelix_ai/tools.py`)

```python
from collections.abc import Awaitable, Callable

ToolPartialCallback = Callable[["ToolResult"], Awaitable[None] | None]
"""Fire-and-forget callback. Pi parity: AgentToolUpdateCallback (types.ts:358).
Runtime drains all partial-emit fan-out before result return (mirrors
Pi `await Promise.all(updateEvents)` at agent-loop.ts:630)."""

@dataclass(frozen=True)
class ToolExecutionContext:
    tool_call_id: str = ""
    signal: Any | None = None
    on_partial: ToolPartialCallback | None = None    # NEW Sprint 3d / P-9
```

Update `__all__`; re-export from `aelix_ai/__init__.py`.

### A.3 `Tool.execute` docstring (no signature change)
Document `exec_ctx.on_partial` as Pi-equivalent. Fire-and-forget contract. `None` when no harness registered (bare-loop callers) — tools MUST tolerate None.

---

## §B — `_execute_and_finalize` threading (Gap A — `loop.py`)

### B.1 Add `emit` param + build `_on_partial` closure

```python
async def _execute_and_finalize(
    context, assistant_message, config, signal, prepared, emit,  # NEW emit param
):
    """... Sprint 3d (P-9): construct on_partial callback that emits
    ToolExecutionUpdateEvent per partial. Pi parity: executePreparedToolCall
    (agent-loop.ts:604-639). Drain happens in both happy and error paths."""

    update_events: list[asyncio.Task[None]] = []
    loop_ref = asyncio.get_running_loop()

    def _on_partial(partial: ToolResult) -> None:
        coro = emit(ToolExecutionUpdateEvent(
            tool_call_id=prepared.tool_call.tool_call_id,
            partial_result=partial,
            tool_name=prepared.tool.name,
            args=prepared.args,
        ))
        update_events.append(loop_ref.create_task(coro))

    exec_ctx = ToolExecutionContext(
        tool_call_id=prepared.tool_call.tool_call_id,
        signal=signal,
        on_partial=_on_partial,
    )

    try:
        result = await prepared.tool.execute(prepared.args, exec_ctx)
    except Exception as exc:
        if update_events:
            await asyncio.gather(*update_events, return_exceptions=False)  # drain in error path
        result = ToolResult(
            content=[TextContent(text=f"Tool '{prepared.tool.name}' raised: {exc}")],
            is_error=True,
        )
    else:
        if update_events:
            await asyncio.gather(*update_events, return_exceptions=False)  # drain in happy path

    # ... existing after_tool_call block unchanged ...
```

**Signature change:** `_execute_and_finalize` gains `emit` param. Update 2 callers (`_execute_tool_calls_sequential` L532, `_execute_tool_calls_parallel` L623 closure).

### B.2 `create_task` vs lazy coroutine pattern
**Decision:** `create_task` (eager scheduling), mirroring Pi `Promise.resolve(emit(...))`. Lazy coroutines would queue partials at end — Pi divergence.

### B.3 Callback error policy (Aelix-additive)
Pi lets hook-handler exceptions during partial-emit escape `executePreparedToolCall` entirely. Aelix's existing `_execute_and_finalize` try/except will catch them and convert to isError. **Document in ADR-0017 amendment as stricter-than-Pi safety net consistent with ADR-0019 v3 `error_mode` policy.** Not a hidden divergence — explicit Aelix-additive.

---

## §C — `_emit_tool_result_message` port (Gap B — `loop.py`)

### C.1 New helper

```python
async def _emit_tool_result_message(msg: ToolResultMessage, emit: AgentEventSink) -> None:
    """Pi parity emitToolResultMessage (agent-loop.ts:715-718). Both
    message_start AND message_end emitted. Single source of truth — sequential
    and parallel paths call this helper; _run_loop does NOT emit."""
    await emit(MessageStartEvent(message=msg))
    await emit(MessageEndEvent(message=msg))
```

### C.2 Sequential path ordering refactor

Pi order (`agent-loop.ts:434-438`): `emitToolExecutionEnd` → `createToolResultMessage` → `emitToolResultMessage` → push.

**Current Aelix order (BUG):**
```python
result_messages.append(_to_tool_result_message(...))  # append FIRST
await emit(ToolExecutionEndEvent(...))                # then emit end
# (no message_start/message_end at all)
```

**Pi-parity order:**
```python
msg = _to_tool_result_message(tc.tool_call_id, result)
await emit(ToolExecutionEndEvent(...))                # FIRST: end event
await _emit_tool_result_message(msg, emit)            # THEN: message events
result_messages.append(msg)                            # THEN: append
```

Apply transform in BOTH immediate branch (L517-530) and prepared branch (L532-547).

### C.3 Parallel path Phase 3 emit insertion

```python
# Phase 3 — source-order message emit (Pi agent-loop.ts:495-499).
result_messages: list[ToolResultMessage] = []
all_terminate = bool(ordered_results)
for finalized in ordered_results:
    msg = _to_tool_result_message(finalized.tool_call.tool_call_id, finalized.result)
    await _emit_tool_result_message(msg, emit)        # NEW Sprint 3d / P-9
    result_messages.append(msg)
    if not finalized.result.terminate:
        all_terminate = False
```

### C.4 `_run_loop` unchanged
Pi `runLoop` does NOT emit message events for tool-result messages — helper-owned. Aelix L177-179 already only appends, no emits. **No `_run_loop` change.**

---

## §D — Ordering matrix update (extension to Sprint 3c §E)

| Event | Order | Pi citation | Aelix site |
|---|---|---|---|
| 1. `tool_call` hook | source | agent-loop.ts:569-578 | loop.py prep loop (existing) |
| 2. `tool_execution_start` | source | agent-loop.ts:457-463 | loop.py:589-595 (existing) |
| 3. `tool_execution_update` | **interleave + drain inside _execute_and_finalize** | agent-loop.ts:616-630 | loop.py:_execute_and_finalize **NEW Sprint 3d** |
| 4. `tool_execution_end` | completion (parallel) / source (sequential) | agent-loop.ts:487 | loop.py:627-634 (existing) |
| 5. `tool_result` hook | source post-gather | agent-loop.ts:495-499 | loop.py Phase 3 (existing via after_tool_call_bridge) |
| 6. tool-result `message_start` / `message_end` | source post-gather | agent-loop.ts:715-718 | loop.py Phase 3 **NEW Sprint 3d** |

Per-tool `updateEvents` arrays scoped to per-`_execute_and_finalize` call — partials from tool A and B may interleave freely (matches Pi: two `Promise.all(updateEvents)` calls don't coordinate across tools).

---

## §E — Tests (+5 tests, 261 → 266)

### E.1 `test_tool_execution_update_event.py`
Tool calls `on_partial(p1)`, `on_partial(p2)`, `on_partial(p3)` then returns. Assert 3 `ToolExecutionUpdateEvent` emitted in order with correct payload. Assert all 3 emitted BEFORE `ToolExecutionEndEvent`.

### E.2 `test_tool_result_message_events_sequential.py`
Sequential path emits `MessageStartEvent` + `MessageEndEvent` for each tool-result message in source order. Assert per-tool: `ToolExecutionEndEvent` precedes `MessageStartEvent(toolresult)` precedes `MessageEndEvent(toolresult)`.

### E.3 `test_tool_result_message_events_parallel.py`
3 tools with inverted latency (tool 0 slow, tool 2 fast). Parallel path emits message events in SOURCE order. `tool_execution_end` for tc[2] precedes tc[0] (completion order) but `message_start(toolresult tc[0])` precedes `message_start(toolresult tc[1])` (source order).

### E.4 `test_on_partial_callback_drains_before_return.py`
Tool emits 5 partials then returns. Assert all 5 update hook handlers ran before the `tool_result` hook + `tool_execution_end` for that tool.

### E.5 `tests/pi_parity/test_phase_2_1_strict_superset.py`
**Phase 2.1 closure pin.** Load `pi_agent_harness_event_names_734e08e.json`. For each event name, grep Aelix code for an emit site. Maintain explicit deferred allowlist (Phase 2.2 / Phase 4 names with ADR citations). Assert zero gaps in Phase 2.1 scope.

---

## §F — ADR amendments + new ADR-0039

### F.1 ADR-0017
Add §"Tool execution update + tool-result message events landed Sprint 3d (Phase 2.1.4)". Document `_on_partial` pattern + Aelix-additive (stricter-than-Pi) per-tool partial-emit error containment.

### F.2 ADR-0021
Amend §E matrix row 6 from "carry-over pre-existing gap" to "implemented Sprint 3d". Add row 3 amendment (partial-emit drain semantics).

### F.3 **NEW ADR-0039 "Phase 2.1 Strict Superset Closure"**
- 1st-principle invariant statement
- Closure date + Pi SHA
- Roster of Sprint 3a/3b/3c/3d findings P-1 through P-9 (each with one-line resolution)
- Pointer to E.5 as durable regression guard
- Explicit deferred allowlist (Phase 2.2 / Phase 4 emit names + ADR refs)
- Forward-compat clause: future Pi events MUST land emit site in same sprint OR re-list as deferred

---

## §G — Acceptance checklist

1. 261 → 266 tests pass
2. ruff clean
3. pyright spike: 8 errors
4. Demo unchanged
5. E.5 passes (no Phase 2.1 Pi event has zero emit site)
6. Phase 2.1 ADRs all Accepted: 0017, 0019, 0021, 0027, 0030, 0034, 0036, **0039 (new)**
7. `_execute_and_finalize` accepts `emit`; both callers updated
8. `_emit_tool_result_message` helper present + called from both paths
9. `ToolExecutionContext.on_partial` field exists, typed, defaults None

---

## §H — Out of scope

- Phase 2.2 (Session/Compact/Tree ADR-0022, 0023)
- Pyright 142 cleanup (task #37)
- Real providers (Phase 4)
- ADR-0017 shallow→deep merge upgrade
- Per-tool concurrency semaphore / timeout (no Pi parity)

---

## §I — Implementation order

1. §A.2 `ToolExecutionContext.on_partial` + alias + `__all__` exports (gates B and E.1)
2. §B `_execute_and_finalize` adds `emit` param + `_on_partial` closure + drain. Update 2 callers.
3. §C.1 helper + §C.2 sequential refactor (3-step ordering: end → emit_helper → append)
4. §C.3 parallel Phase 3 emit insertion
5. §E.1-E.4 tests
6. §E.5 closure pin
7. ADR-0017 + ADR-0021 amendments
8. ADR-0039 drafted + Accepted

End of binding spec.
