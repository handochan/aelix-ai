# 0027. asyncio.gather for Parallel Tool Execution

Status: Accepted (Sprint 3c, Phase 2.1.3) — DECISION REVERSED from TaskGroup to asyncio.gather per Sprint 3c P-7 finding

Relates to: ADR-0021 (Parallel-Mode Tool Execution)

## Context

ADR-0021 schedules parallel tool execution for Sprint 3c (Phase 2.1.3).
Pi uses `Promise.all` to execute parallel tool calls.

```typescript
// Pi agent-loop.ts:491-493
const orderedFinalizedCalls = await Promise.all(
    finalizedCalls.map((entry) => (typeof entry === "function" ? entry() : Promise.resolve(entry))),
);
```

Pi's `AbortSignal` is threaded cooperatively from the caller.

Python 3.11+ provides `asyncio.TaskGroup`. The original Draft of this ADR
proposed `TaskGroup` for structured concurrency.

### Original alternatives considered

1. **`asyncio.gather()`**: Closest direct analog to `Promise.all`.
2. **`asyncio.TaskGroup`** (originally proposed): structured concurrency
   with auto-cancel of siblings on first task failure.

## §P-7 — Sprint 3c REVERSAL (load-bearing)

Sprint 3c architect verification at SHA `734e08e` revealed the original
TaskGroup decision was **Pi-divergent, not Pi-parity**.

**Pi evidence**:

- `agent-loop.ts:609-637` (`executePreparedToolCall`) and `:651-680`
  (`finalizeExecutedToolCall`) wrap every tool call in `try/catch`. Tool
  exceptions are converted into `isError` tool results before they can reach
  the `Promise.all` boundary.
- `agent-harness.ts:200-220` shows the hook bus catches handler exceptions
  inside the prepare/finalize path; only specific hook-bus errors are
  rethrown to the harness for failure-message synthesis.
- **Net Pi behaviour**: the `Promise.all` reject path is unreachable from
  tool work. Pi NEVER cancels siblings on a tool error.

**Verdict**: TaskGroup's mandatory sibling-cancel on first failure would be
**Pi-divergence**, not Pi-parity. Reverse to
`asyncio.gather(*coros, return_exceptions=False)`.

## Decision

Sprint 3c parallel tool implementation uses `asyncio.gather` (NOT TaskGroup):

```python
# Aelix loop.py _execute_tool_calls_parallel
async def _run(prep: _Prepared) -> _Finalized:
    result = await _execute_and_finalize(...)  # per-tool try/catch within
    await emit(ToolExecutionEndEvent(...))     # completion order
    return _Finalized(...)

coros = [_run(entry) if isinstance(entry, _Prepared) else _identity(entry)
         for entry in pending]
ordered_results = await asyncio.gather(*coros, return_exceptions=False)
```

**Rationale**:

1. **Pi parity (1st principle)**: Pi never cancels siblings on tool error
   (`agent-loop.ts:609-637, 651-680`). `asyncio.gather(..., return_exceptions=False)`
   matches because `_run` never raises for tool work — per-tool catches in
   `_execute_and_finalize` swallow tool errors into isError results.
2. **Abort threading**: `AgentHarness.abort()` already sets the abort flag
   and clears queues; cooperative `signal` mirrors Pi's `AbortSignal`. Sprint
   3c §C.2 adds Aelix-additive `task.cancel()` on abort for tools that ignore
   the cooperative signal.
3. **`ExceptionGroup` not needed**: Pi has no aggregated-exception type.
   Only `AgentHarnessError("hook")` raised by the harness's tool_result
   bridge can escape `_run` — and that MUST propagate so the harness can
   synthesize a failure assistant message.
4. **TaskGroup rejected**: mandatory cancel-siblings on first failure is
   Pi-divergence.

## Consequences

- Pi `Promise.all` observable behaviour is matched exactly: per-tool error
  isolation, source-order results, no sibling-cancellation.
- Python 3.11+ requirement remains (Sprint 2 monorepo already commits to
  `>=3.11`), but is no longer strictly necessary for the parallel
  dispatcher — `asyncio.gather` is available in 3.8+. The 3.11+ floor is
  still appropriate because other Aelix features (PEP 654 `except*` in tests,
  `assert_never`, etc.) use 3.11 syntax.
- The original Draft's `except* ToolExecutionError:` consequence is
  **removed**. `ExceptionGroup` no longer applies — tool errors do not
  aggregate; they live in their respective tool-result messages as
  `is_error=True`.
- `tests/pi_parity/test_p7_no_sibling_cancel_on_error.py` regression-guards
  the P-7 verdict: a raising tool MUST NOT cancel its siblings.
- ADR-0021 implementation binding remains — this ADR is the Sprint 3c
  implementation choice for the parallel branch.
- Aelix Sprint 3c additive over Pi: `AgentHarness.abort()` calls
  `task.cancel()` on the in-flight turn task (§C.2). This is a strict
  superset of Pi (Pi-observing tools would have cancelled anyway via the
  signal); guards against tools that ignore signal.
