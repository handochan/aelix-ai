# 0027. asyncio.TaskGroup for Parallel Tool Execution

Status: Draft (Phase 2.1 implementation)

Relates to: ADR-0021 (Parallel-Mode Tool Execution)

## Context

ADR-0021은 parallel tool execution을 Phase 2.1에 schedule했습니다.
Pi는 `Promise.all`로 parallel tool call을 실행합니다:

```typescript
// Pi agent-harness.ts (simplified)
const results = await Promise.all(
  toolCalls.map(tc => executeTool(tc, abortSignal))
);
```

Pi의 `AbortSignal`은 외부에서 cancel signal을 주입하는 방식입니다.

Python 3.11+는 `asyncio.TaskGroup`을 제공합니다. 이는 structured concurrency를
언어 레벨에서 지원합니다.

### 고려한 대안

1. **`asyncio.gather()`**: `Promise.all`과 가장 직접적인 대응. 그러나 한 task
   실패 시 sibling cancellation이 자동으로 일어나지 않습니다.
   `return_exceptions=True` 시 모든 exception을 수집하지만 sibling은 계속 실행합니다.
2. **`asyncio.TaskGroup`** (채택): structured concurrency. 한 task 실패 시
   sibling auto-cancel. `ExceptionGroup`으로 모든 failure를 aggregate합니다.

## Decision

Phase 2.1 parallel tool implementation에서 `asyncio.TaskGroup`을 사용합니다:

```python
async def _execute_parallel(
    self, tool_calls: list[ToolCall]
) -> list[ToolResult]:
    results: list[ToolResult] = []
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(self._execute_tool(tc)) for tc in tool_calls]
    return [t.result() for t in tasks]
```

선택 이유:

1. **Structured concurrency**: 한 tool 실패 시 sibling이 자동으로 cancel됩니다.
   Pi의 `Promise.all` early-exit 동작과 동일한 observable behavior입니다.
2. **Aggregated `ExceptionGroup`**: 모든 parallel failure를 한 번에 수집합니다.
   Pi가 `Promise.allSettled` 없이 `Promise.all`을 쓰는 것과 first-error
   early-exit 언어로 일치합니다.
3. **`abort()` 통합**: Aelix `abort()` hook이 `TaskGroup`을 직접 cancel합니다.
   Pi의 `AbortSignal` threading보다 깔끔하고 Python 관용적입니다.

## Consequences

- Pi의 `Promise.all` behavior와 observable difference가 최소화됩니다. 두 구현
  모두 first-error early-exit 언어입니다.
- Python 3.11+ requirement가 강화됩니다. Sprint 2 pyproject.toml에
  `requires-python = ">=3.11"`이 이미 선언되어 있어 추가 비용이 없습니다.
- `ExceptionGroup`을 처리하는 caller는 `except* ToolExecutionError:` syntax를
  사용합니다 (Python 3.11+ PEP 654).
- ADR-0021 implementation 시 binding됩니다. ADR-0021이 `asyncio.gather()`를
  언급하는 경우 이 ADR로 override합니다.
- Aelix-only improvement: Pi-side에 상응하는 structured concurrency 보장이
  없습니다 (additive divergence).
