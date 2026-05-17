# 0021. Parallel-Mode Tool Execution + Per-Tool Override

Status: Draft (Phase 2.1)

## Context

Pi loop의 default tool execution mode는 `"parallel"`입니다. 각 tool call에
`executionMode: "parallel" | "sequential"` override를 지정할 수 있습니다.
Pi `agent-loop.ts:619-625, 693-700`에서 batch 내 tool call을 parallel로 실행하되,
sequential override를 가진 tool이 하나라도 있으면 batch 전체를 sequential로
실행합니다. `tool_execution_end`는 tool completion 순서로 emit하고, `toolResult`
메시지는 assistant source 순서로 persist합니다.

현재 Aelix는 sequential-only입니다. `AgentTool.execution_mode` 필드가 정의되어
있으나 `_execute_tool_calls`에서 사용되지 않습니다. 이는 undocumented divergence입니다.

## Decision

Phase 2.1에서 `_execute_tool_calls`에 parallel branch를 추가합니다.

- **기본**: parallel 실행 (Pi parity).
- **Per-tool override**: `execution_mode="sequential"`인 tool이 batch에 하나라도
  있으면 batch 전체를 sequential로 실행합니다.
- **emit 순서**: `tool_execution_end`는 completion 순서로 emit합니다.
- **persist 순서**: `toolResult` 메시지는 assistant source(tool call) 순서로
  message list에 추가합니다. 이는 Pi `agent-loop.ts:693-700` 패턴과 동일합니다.

```python
# 개념적 구현 (Phase 2.1)
async def _execute_tool_calls(self, tool_calls):
    if any(tc.execution_mode == "sequential" for tc in tool_calls):
        return await self._execute_sequential(tool_calls)
    return await self._execute_parallel(tool_calls)
```

## Consequences

- `agent/loop.py`의 `_execute_tool_calls`를 parallel branch를 포함하도록 확장합니다.
- `AgentTool.execution_mode` 필드를 실제로 읽어 parallel/sequential을 결정합니다.
- 신규 테스트: `test_parallel_tool_execution_respects_per_tool_override` —
  sequential override 없는 batch는 parallel 실행, sequential override 있는 batch는
  sequential 실행을 검증합니다.
- `tool_execution_end` emit 순서와 message persist 순서를 분리하는 별도 테스트를 추가합니다.
