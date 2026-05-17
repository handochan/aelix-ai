# 0021. Parallel-Mode Tool Execution + Per-Tool Override

Status: Accepted (Sprint 3c, Phase 2.1.3 — Sprint 3d §E matrix rows 3 + 6 implemented in Phase 2.1.4)

## Context

Pi loop의 default tool execution mode는 `"parallel"`입니다. 각 tool call에
`executionMode: "parallel" | "sequential"` override를 지정할 수 있습니다.
Pi `agent-loop.ts:380-387` (dispatcher) 그리고 `agent-loop.ts:446-505`
(parallel body) 에서 batch 내 tool call을 parallel로 실행하되, sequential
override를 가진 tool이 하나라도 있으면 batch 전체를 sequential로 실행합니다.
`tool_execution_end`는 tool completion 순서로 emit하고, `toolResult` 메시지는
assistant source 순서로 persist합니다.

Sprint 3c 이전 Aelix는 sequential-only였습니다. `AgentTool.execution_mode`
필드가 정의되어 있으나 `_execute_tool_calls`에서 사용되지 않았고,
`AgentLoopConfig.tool_execution`은 `"sequential"`이 default였습니다 —
undocumented Pi-divergence였습니다.

## Decision

Sprint 3c (Phase 2.1.3)에서 `_execute_tool_calls`를 router로 변환하고
parallel branch를 추가합니다.

- **기본**: parallel 실행 (Pi parity, `types.ts:226-232`).
- **Per-tool override**: `execution_mode="sequential"`인 tool이 batch에 하나라도
  있으면 batch 전체를 sequential로 실행합니다.
- **dispatcher**: `agent-loop.ts:380-387` 1:1 port (Aelix `loop.py`
  `_execute_tool_calls` router).
- **parallel body**: `agent-loop.ts:446-505` 3-phase port (Aelix
  `_execute_tool_calls_parallel`).
- **emit 순서**: `tool_execution_start`는 source order로 prep loop에서 emit;
  `tool_execution_end`는 completion order로 emit합니다.
- **persist 순서**: `toolResult` 메시지는 assistant source(tool call) 순서로
  message list에 추가합니다 (Pi `agent-loop.ts:495-499` 패턴).

```python
# Aelix Sprint 3c implementation (loop.py)
async def _execute_tool_calls(context, assistant_message, config, signal, emit):
    tool_calls = [c for c in assistant_message.content if isinstance(c, ToolCallContent)]
    tool_map = {t.name: t for t in context.tools}
    has_sequential = any(
        (tool_map.get(tc.tool_name) is not None
         and tool_map[tc.tool_name].execution_mode == "sequential")
        for tc in tool_calls
    )
    if config.tool_execution == "sequential" or has_sequential:
        return await _execute_tool_calls_sequential(...)
    return await _execute_tool_calls_parallel(...)
```

### §P-6 — Sprint 3c Pi citation update (W1 architect finding)

Sprint 3c architect verification at SHA `734e08e` confirmed the original ADR
text broadly matched Pi for Q1 (default parallel), Q3 (per-tool override),
and Q4 (sequential downgrade). Only the cited line ranges were stale.
**Fix**: dispatcher = `agent-loop.ts:380-387`; parallel body =
`agent-loop.ts:446-505`; default = `types.ts:226-232`.

### §E — Hook event ordering matrix (BINDING)

| Event | Order under parallel | Pi citation |
|---|---|---|
| `tool_call` hook | Source order, BEFORE `gather` | `agent-loop.ts:456-462` → `:569-578` |
| `tool_execution_start` | Source order, BEFORE `gather` | `agent-loop.ts:457-462` |
| `tool_execution_update` | **Interleaved + drained per-tool inside `_execute_and_finalize`** | `agent-loop.ts:617-625` (Sprint 3d landed) |
| `tool_execution_end` | **Completion order** | `agent-loop.ts:486` |
| `tool_result` hook | **Completion order** | `agent-loop.ts:650-674` |
| `message_start`/`message_end` (tool-result msg) | **Source order** | `agent-loop.ts:495-499` (Sprint 3d landed via `_emit_tool_result_message`) |
| `turn_end` | After all above | `agent-loop.ts:217` |

Implementation invariant: `tool_execution_start` MUST fire in the prep loop
(Phase 1), NOT inside the per-tool closure. Immediates emit
`tool_execution_end` in the prep loop too so the end events of unknown /
hook-blocked tools stay paired in source order with their start events.

### Sprint 3d amendment — rows 3 and 6 implemented

Pre-Sprint 3d the matrix above was documented but two rows were not
backed by emit sites in code:

- **Row 3 (`tool_execution_update`)** — Pi's `executePreparedToolCall`
  (`agent-loop.ts:604-639`) collects every emit-callback invocation into a
  per-call `updateEvents` array and awaits `Promise.all(updateEvents)` in
  BOTH the happy and error paths before the tool result returns to the
  loop. Sprint 3d ports this drain semantic to
  `_execute_and_finalize` via `asyncio.gather(*update_events,
  return_exceptions=False)` and exposes the partial-emit callback through
  `ToolExecutionContext.on_partial` (see ADR-0017 Sprint 3d amendment for
  the type alias + Aelix-additive partial-emit exception containment).
- **Row 6 (`message_start` / `message_end` for tool-result messages)** —
  Pi's `emitToolResultMessage` (`agent-loop.ts:715-718`) emits both events
  per message. Sprint 3d adds the `_emit_tool_result_message` helper and
  calls it from the sequential immediate branch, the sequential prepared
  branch, and the parallel Phase 3 source-order loop. The sequential
  ordering was also fixed in lockstep (immediate + prepared branches now
  follow `end → emit_helper → append`, mirroring Pi
  `agent-loop.ts:434-438`).

Per-tool `updateEvents` arrays are scoped to a single `_execute_and_finalize`
call, mirroring Pi's two independent `Promise.all(updateEvents)` invocations.
Partials emitted by tool A and tool B may interleave freely under parallel
dispatch — neither call awaits the other.

## Consequences

- `agent/loop.py`의 `_execute_tool_calls`가 router + `_execute_tool_calls_sequential` +
  `_execute_tool_calls_parallel` 로 분리되었습니다.
- `_prepare_tool_call` shared helper로 sequential + parallel 두 경로가 동일한
  prep semantic을 공유합니다.
- `AgentTool.execution_mode` 필드를 실제로 읽어 parallel/sequential을 결정합니다.
- `AgentLoopConfig.tool_execution` default는 `"sequential"` → `"parallel"` 로 flip.
  Pi parity. 기존 callers가 sequential 동작을 원하면 명시적으로
  `tool_execution="sequential"`을 pass 해야 합니다 (backward-compat caveat,
  acceptance §I.8).
- `AgentHarnessOptions.tool_execution: ToolExecutionMode = "parallel"` 신규 public knob.
- **§G.13 / §G.14 test pins** — `tests/pi_parity/test_p7_no_sibling_cancel_on_error.py`
  와 `tests/pi_parity/test_p6_dispatcher_routing.py` 가 P-6 / P-7 verdict를
  regression-guard합니다.
- `tests/test_parallel_tool_execution.py`, `test_per_tool_sequential_override.py`,
  `test_hook_event_ordering_under_parallel.py`, `test_result_ordering_preserved.py`,
  `test_parallel_with_immediate_isError_mix.py`, `test_terminate_aggregation_parallel.py`,
  `test_parallel_tool_error_propagation.py`, `test_tool_call_hook_args_isolation.py`,
  `test_abort_cancels_in_flight_parallel.py`, `test_parallel_path_default_flip.py`
  를 신규 추가하여 §A-§F 전반을 cover합니다 (10 신규 + 11 pi-parity = 25).
