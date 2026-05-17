# Sprint 3c · Phase 2.1.3 — Parallel Tool Execution + Per-Tool Override (BINDING SPEC)

Status: **Binding** (Architect-authored, READ-ONLY pass)
Author: Architect (Opus, READ-ONLY)
Date: 2026-05-17
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다. 변형 혹은 추가는 그 다음 중요도입니다."**

---

## §0 — STEP 1: P-6/P-7/P-8 INVESTIGATION (Pi parallel tool execution semantics — VERIFIED at SHA 734e08e)

Every Pi citation below was fetched and re-verified at the pinned SHA. Three Draft-ADR-vs-Pi divergences are identified (P-6, P-7, P-8). Each receives an explicit verdict.

### Findings

**Q1: Does Pi execute tools in parallel by default?**
**Yes.** Three sources agree:
- `types.ts:226-232` — `AgentLoopConfig.toolExecution?: ToolExecutionMode` with JSDoc `"Default: \"parallel\""`.
- `types.ts:11-15` — `ToolExecutionMode = "sequential" | "parallel"`.
- `agent-loop.ts:380-387` — dispatcher routes to `executeToolCallsParallel` unless `config.toolExecution === "sequential"` OR any tool's `executionMode === "sequential"`.

**Q2: What API does Pi use for parallel execution?**
`agent-loop.ts:491-493`:
```typescript
const orderedFinalizedCalls = await Promise.all(
    finalizedCalls.map((entry) => (typeof entry === "function" ? entry() : Promise.resolve(entry))),
);
```
**`Promise.all`** — but Pi's per-tool `try/catch` inside `executePreparedToolCall` (`agent-loop.ts:609-637`) and `finalizeExecutedToolCall` (`agent-loop.ts:651-674`) converts every tool error into an isError tool result. **Net Pi behavior: no `Promise.all` rejection path is reachable from tool errors.**

**Q3: Per-tool `executionMode` override — exists?**
**Yes.** `types.ts:380-383` — `AgentTool.executionMode?: ToolExecutionMode`.

**Q4: Mixed-mode handling**
`agent-loop.ts:380-387`: **any single `sequential` tool downgrades the ENTIRE batch to sequential.** No partition / hybrid mode.

**Q5: Result ordering**
- `tool_execution_start` events fire **in tool-call source order** (`agent-loop.ts:456-462`) BEFORE `Promise.all` await.
- `tool_execution_end` fires **in completion order** (each closure at `agent-loop.ts:486`).
- `tool_result` messages **in source order** because `Promise.all` preserves array order.

**Q6: Hook event ordering details:**
- `tool_call` hook: fires inside `prepareToolCall` (`agent-loop.ts:569-578`) inside source-order prep loop — **before any execution starts**
- `tool_execution_start`: source order, before `Promise.all`
- `tool_execution_update`: interleaved (concurrent)
- `tool_execution_end`: completion order
- `tool_result` hook: completion order
- `message_start` / `message_end` for tool-result message: source order (after `Promise.all`)

**Q7: Abort semantics:** Pi threads `AbortSignal` cooperatively. Pi does NOT call `controller.abort()` on sibling failure.

**Q8: Error propagation:** Tool exceptions → isError results. `Promise.all` rejection path **unreachable** from tool work. Sibling cancellation on first-error **does NOT occur in Pi**.

**Q9: `tool_call` hook with `block=True`:** Per-tool short-circuit, NOT batch.

**Q10: D.1.5 args-reference contract under parallel:** Per-tool args dicts isolated; no shared surface.

### Pi Parity Drift Verdicts

#### **P-6: ADR-0021 BROADLY CORRECT — wording fix only**
ADR-0021 Draft text matches Pi for Q1, Q3, Q4. Only the cited line ranges are stale. **Fix:** add `agent-loop.ts:380-387` (dispatcher) + `:446-505` (parallel body); add §E ordering matrix.

#### **P-7: ADR-0027 LOAD-BEARING REVERSAL — switch from `TaskGroup` to `asyncio.gather`**
ADR-0027 Draft claims `asyncio.TaskGroup` mirrors Pi `Promise.all` early-exit. **Wrong at SHA 734e08e.** Pi catches every tool exception per-tool; `Promise.all` reject path is unreachable; Pi **never** cancels siblings. TaskGroup's mandatory sibling-cancel would be Pi-divergence, not parity.

**Verdict:** use `asyncio.gather(*coros, return_exceptions=False)`. Rationale:
1. Pi parity (1st principle): Pi never cancels siblings on tool error
2. Abort threading: `AgentHarness.abort()` already sets flag; cooperative `signal` mirrors Pi
3. `ExceptionGroup` not needed — Pi has no aggregated-exception type
4. TaskGroup rejected — mandatory cancel-siblings is Pi divergence

#### **P-8: ADR-0021 ordering wording understated**
ADR-0021 documents `tool_execution_end` completion order + tool-result message source order correctly but omits: `tool_call` source order before exec; `tool_execution_start` source order before exec; `tool_execution_update` interleave allowed. **Amend with §E 7-row matrix.**

---

## §A — Parallel execution dispatcher

### A.1 Public API surface

In `packages/aelix-agent-core/src/aelix_agent_core/loop.py`:

1. `_execute_tool_calls` becomes a **router**:
```python
async def _execute_tool_calls(context, assistant_message, config, signal, emit) -> _ExecutedBatch:
    tool_calls = [c for c in assistant_message.content if isinstance(c, ToolCallContent)]
    tool_map = {t.name: t for t in context.tools}
    has_sequential = any(
        (tool_map.get(tc.tool_name) and tool_map[tc.tool_name].execution_mode == "sequential")
        for tc in tool_calls
    )
    if config.tool_execution == "sequential" or has_sequential:
        return await _execute_tool_calls_sequential(...)
    return await _execute_tool_calls_parallel(...)
```

2. Existing sequential body becomes `_execute_tool_calls_sequential(...)` — **rename only, zero behavior change**.

### A.2 `_execute_tool_calls_parallel` shape

Mirrors Pi `executeToolCallsParallel` (`agent-loop.ts:446-505`) phase-for-phase:

```python
async def _execute_tool_calls_parallel(context, assistant_message, config, signal, emit) -> _ExecutedBatch:
    # Phase 1 — sequential preparation (Pi agent-loop.ts:456-489)
    pending: list[_PendingEntry] = []
    for tc in tool_calls:
        await emit(ToolExecutionStartEvent(...))
        prepared = await _prepare_tool_call(...)
        if prepared.kind == "immediate":
            await emit(ToolExecutionEndEvent(...))  # synchronous for immediates
            pending.append(_Finalized(toolCall=tc, result=prepared.result, ...))
        else:
            pending.append(_PendingFactory(prepared))

    # Phase 2 — parallel execution (Pi agent-loop.ts:491-493, with P-7 reversal)
    async def _run(factory):
        executed = await _execute_prepared_tool_call(factory.prepared, signal, emit)
        finalized = await _finalize_executed_tool_call(...)
        await emit(ToolExecutionEndEvent(...))  # COMPLETION ORDER
        return finalized

    coros = [
        _run(entry) if isinstance(entry, _PendingFactory) else _identity(entry)
        for entry in pending
    ]
    ordered_results = await asyncio.gather(*coros, return_exceptions=False)
    # return_exceptions=False is correct: _run never raises (per-tool catches);
    # only hook raises (AgentHarnessError("hook")) escape — those MUST propagate.

    # Phase 3 — emit tool-result messages in SOURCE ORDER (Pi agent-loop.ts:495-499)
    result_messages: list[ToolResultMessage] = []
    all_terminate = True
    for finalized in ordered_results:
        msg = _to_tool_result_message(finalized.tool_call.tool_call_id, finalized.result)
        result_messages.append(msg)
        if not finalized.result.terminate:
            all_terminate = False

    return _ExecutedBatch(result_messages, all_terminate and bool(ordered_results))
```

### A.3 `_prepare_tool_call` extraction

Extract pure-prep helper shared by sequential + parallel paths. Returns `_Prepared(tool, args, before_decision)` or `_Immediate(result, is_error)`.

### A.4 Default flip — `AgentLoopConfig.tool_execution` `"sequential"` → `"parallel"`

`types.py:253`: flip to `"parallel"` (Pi parity). Audit `test_agent_loop.py` / `test_agent_harness.py` / `test_loop_with_hooks.py` for incidentally-sequential-ordering assumptions.

### A.5 `AgentHarnessOptions.tool_execution` — new public knob

```python
@dataclass
class AgentHarnessOptions:
    ...
    tool_execution: ToolExecutionMode = "parallel"  # Pi parity default
```

Wire into `AgentHarness._run` `AgentLoopConfig(... tool_execution=self._options.tool_execution)`.

---

## §B — Per-tool `execution_mode` resolution rule

```
Step 1 — has_sequential_override:
    has_seq = any(
        tool_map.get(tc.tool_name) is not None
        AND tool_map[tc.tool_name].execution_mode == "sequential"
        for tc in tool_calls
    )

Step 2 — Effective mode:
    if config.tool_execution == "sequential": mode = "sequential"
    elif has_seq: mode = "sequential"          # batch-wide downgrade (Pi parity)
    else: mode = "parallel"

Step 3 — Per-tool "parallel" or None: treated identically.
Step 4 — Unknown tool: does NOT contribute to has_seq; becomes immediate isError.
```

**Pi citation:** `agent-loop.ts:380-387` + `types.ts:380-383`.

---

## §C — Abort / cancellation under parallel

### C.1 Pi semantics (per Q7)
Pi threads `AbortSignal` cooperatively. Pi does NOT call `controller.abort()` on sibling failure.

### C.2 Aelix Sprint 3c contract
1. `_execute_tool_calls_parallel` does NOT actively cancel `gather` on tool error (P-7 reversal).
2. `AgentHarness.abort()` continues setting `_abort_requested` + clearing queues. **Add:** track in-flight `gather` task on harness; on abort call `task.cancel()`. This is **Aelix additive** (Pi doesn't cancel `Promise.all`) but justified: strict superset of Pi (Pi tools observing signal would have cancelled anyway); guards against tools that ignore signal.
3. `ToolExecutionContext.signal` populated from loop's `signal` parameter under parallel path.

### C.3 Hook event during abort
`AbortHookEvent` already fires from `AgentHarness.abort()`. No new emit in Sprint 3c.

---

## §D — Error propagation

| Error source | Pi behavior | Aelix Sprint 3c | Status |
|---|---|---|---|
| Tool `execute` raises | Caught → isError | Caught → isError | Parity |
| `before_tool_call`/`tool_call` hook raises | Bubbles (`agent-harness.ts:200-220`) | `AgentHarnessError("hook")` re-raised, escapes `gather` | Parity |
| `after_tool_call`/`tool_result` hook raises | Caught in finalize → isError | Caught in finalize → isError | Parity |
| `emit(...)` raises | Bubbles | Bubbles | Parity |
| Validation fails | isError (`agent-loop.ts:593-599`) | isError | Parity |
| Sibling task raises | NOT cancelled (`Promise.all` reject path unreachable) | NOT cancelled (`gather(return_exceptions=False)`; per-tool catch ensures no escape) | Parity (P-7 reversal) |

`return_exceptions=False` intentional: only hook-raised `AgentHarnessError("hook")` escapes `_run` — and those MUST propagate to harness for failure-message synthesis.

---

## §E — Hook event ordering under concurrency (BINDING matrix)

| Event | Order under parallel | Pi citation |
|---|---|---|
| `tool_call` hook | Source order, BEFORE `gather` | `agent-loop.ts:456-462` → `:569-578` |
| `tool_execution_start` | Source order, BEFORE `gather` | `agent-loop.ts:457-462` |
| `tool_execution_update` | **Interleaved** | `agent-loop.ts:617-625` |
| `tool_execution_end` | **Completion order** | `agent-loop.ts:486` |
| `tool_result` hook | **Completion order** | `agent-loop.ts:650-674` |
| `message_start`/`message_end` (tool-result msg) | **Source order** | `agent-loop.ts:495-499` |
| `turn_end` | After all above | `agent-loop.ts:217` |

### E.1 Implementation notes
- `tool_execution_start` emitted in **prep loop** (Phase 1), NOT inside closure
- `tool_execution_update` events from `_execute_prepared_tool_call` flow through same `emit` callback — single event loop serializes within tool; interleaves across tools

### E.2 Hook bus reentrancy
`HookBus.emit` is safe under concurrent calls because handler lookups are snapshot copies; reducers iterate sequentially within a single emit; two concurrent emits each get their own snapshot. **No new locking required.**

---

## §F — D.1.5 args-reference contract under parallelism

### F.1 Pi behavior
Each `AgentToolCall.arguments` is own dict. `prepareToolCall` returns same reference or new object. `beforeToolCall` hook payload carries `args: validatedArgs` — same reference as eventually passed to `tool.execute`.

### F.2 Aelix invariants under parallel
1. **Per tool_call_id, args dict is SAME REFERENCE** across `ToolCallHookEvent.args`, `tool.execute(args, exec_ctx)`, `AfterToolCallContext.args`, `ToolResultHookEvent.args`.
2. **Across tool_call_ids, args dicts are DISJOINT.**
3. Validation returns shallow copy; post-validation dict is canonical reference.

### F.3 Concurrency hazard
**None per-tool.** No shared dict surface across tools.

---

## §G — Tests (14 new + 1 fixture)

All under `/workspaces/aelix-ai/tests/`. Deterministic mocks; `asyncio.Event` + `asyncio.sleep(0)` for concurrency proofs.

| # | File | Asserts |
|---|---|---|
| G.1 | `test_parallel_tool_execution.py` | 3 parallel tools complete concurrently; default `"parallel"` after §A.4 |
| G.2 | `test_per_tool_sequential_override.py` | 1 seq + 2 par → batch sequential; per-tool start times strictly increasing |
| G.3 | `test_abort_cancels_in_flight_parallel.py` | mid-flight `abort()` → task cancel (§C.2 Aelix additive) |
| G.4 | `test_parallel_tool_error_propagation.py` | one tool raises → isError; siblings complete (Pi parity); `gather` did not raise |
| G.5 | `test_tool_call_hook_args_isolation.py` | per-tool args mutations isolated |
| G.6 | `test_hook_event_ordering_under_parallel.py` | §E table: source-vs-completion ordering verified with controllable latency |
| G.7 | `test_result_ordering_preserved.py` | slow at idx 0, fast at 1+2 → messages = [r0,r1,r2] regardless of completion |
| G.8 | `test_parallel_with_immediate_isError_mix.py` | unknown + valid + blocked-by-hook in same batch; immediates emit `tool_execution_end` in source order during prep |
| G.9 | `test_terminate_aggregation_parallel.py` | batch terminates iff EVERY result `terminate=True` (Pi `agent-loop.ts:533-535`) |
| G.10 | `test_parallel_path_default_flip.py` | regression guard: no explicit `tool_execution=` uses parallel path |
| G.11 | `pi_parity/test_parallel_exec_matches_pi.py` | fixture-pinned Pi semantics |
| G.12 | `pi_parity/fixtures/pi_parallel_exec_semantics_734e08e.json` | Pi expected emit order: 3-tool happy / mixed-mode / per-tool error / abort |
| G.13 | `pi_parity/test_p7_no_sibling_cancel_on_error.py` | P-7 verdict: raising tool does NOT cancel siblings |
| G.14 | `pi_parity/test_p6_dispatcher_routing.py` | §B resolution rule for 6 combinations |

**Test delta:** +14 tests (G.12 = fixture, not test). Estimated final: **236 baseline + 14 = ~250-255** (audit may force 2-3 splits in existing tests).

---

## §H — ADR amendments

### H.1 ADR-0021 — Draft → **Accepted (Sprint 3c shipped)**
- Status header: `Status: Accepted (Sprint 3c, Phase 2.1.3)`
- Add §P-6 Pi citations: `agent-loop.ts:380-387` (dispatcher), `:446-505` (parallel body), `types.ts:226-232` (default)
- Add §E ordering matrix verbatim
- Add §G.13 / G.14 test pin references

### H.2 ADR-0027 — Draft → **Accepted (Sprint 3c shipped) DECISION REVERSED**
- Status: `Accepted (Sprint 3c, Phase 2.1.3) — DECISION REVERSED from TaskGroup to asyncio.gather per Sprint 3c P-7 finding`
- Add §P-7 reversal subsection with Pi-line evidence (`agent-harness.ts:200-220`, `agent-loop.ts:609-637, 651-680`)
- Update §Decision code example to `asyncio.gather(*coros, return_exceptions=False)`
- Remove `except* ToolExecutionError:` consequence — `ExceptionGroup` no longer applies
- Add consequence: "Python 3.11+ requirement no longer strictly necessary for parallel dispatcher; Sprint 2 monorepo already commits to 3.11+"

### H.3 ADR-0017 — additive subsection
Add §"Tool execution dispatch landed Sprint 3c (Phase 2.1.3)": cite §E ordering matrix.

### H.4 ADR-0019 — no change required

### H.5 README index update
Flip 0021 and 0027 status badges to Accepted. Add Sprint 3c ADRs sub-table.

---

## §I — Acceptance checklist

1. Test count: 236 → 250-255
2. Ruff clean
3. Pyright spike: 8 errors baseline
4. Demo unchanged
5. Pi-parity drift pin G.11+G.12 passes
6. Concurrency stability: G.1, G.3, G.6 run ≥100 iterations locally without flake (don't add to default CI)
7. Phase 2.1 ADRs Accepted at sprint end: 0017, 0019, 0021, 0027, 0030, 0036
8. Backward compat: callers relying on sequential default of `agent_loop` must opt in via `tool_execution="sequential"`. Document in ADR-0021 update.

---

## §J — Out of scope

- Phase 2.2 Session/Compact/Tree (ADR-0022, 0023)
- Pyright 142 errors cleanup (task #37)
- Real provider streaming (Phase 4)
- ADR-0017 shallow→deep merge upgrade (Phase 4)
- Aelix-additive features beyond `task.cancel()` on abort: no aggregated exception type, no per-tool timeout, no concurrency semaphore (Pi has none)

---

## §K — Implementation order

1. §A.1 (router) + §A.3 (`_prepare_tool_call`) — zero behavior change
2. §A.2 (`_execute_tool_calls_parallel`) with **default still `"sequential"`** — add G.1-G.10 gated on explicit `tool_execution="parallel"`
3. §A.4 default flip + §A.5 `AgentHarnessOptions.tool_execution`
4. Audit existing tests for ordering assumptions
5. §G.11-G.14 Pi-parity pins
6. §C.2 abort `task.cancel()` integration
7. Update ADRs per §H

End of binding spec.
