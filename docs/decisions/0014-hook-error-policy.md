# 0014. Hook Error Policy — Mutation hooks throw; Lifecycle observational hooks swallow+log

Status: Accepted (Phase 1.2 임시 결정)
Superseded by: ADR-0019 (Hook Error Policy v2 — Pi continue default, Phase 2.1)

## Context

Hook handler가 예외를 던질 때 harness가 어떻게 처리할지는 Phase 1.2 spec
Section F(ADR-0014 sketch)와 G(R2)에서 미결정 항목으로 표시되었습니다.

Pi agent는 `"continue"` default를 사용합니다 — extension handler에서 예외가
발생하면 무시하고 다음 handler를 계속 실행합니다.

Phase 1.2 구현 중 코드리뷰(W5 C-1)에서 lifecycle observational handler 예외가
agent loop를 깨는 버그가 발견되었습니다. 이를 수정하면서 mutation hook과
lifecycle observational hook의 예외 처리를 **명시적으로 분리**하는 정책이
결정되었습니다.

## Decision

Phase 1.2는 hook을 두 분류로 나누어 예외 처리를 달리합니다.

### Mutation reducer hooks

`context`, `before_agent_start`, `tool_call`, `tool_result`,
`session_before_compact` — handler 반환값이 loop semantics를 좌우합니다.

handler 예외 → **`AgentHarnessError("hook", ..., cause=exc)` 로 wrap 후
`raise from exc`**. Loop가 즉시 abort됩니다.

```python
# src/aelix/harness/core.py (대표 예시)
try:
    result = await self._hooks.emit(ContextHookEvent(messages=...))
except Exception as exc:
    raise AgentHarnessError(
        "hook", f"context hook handler raised: {exc}", cause=exc
    ) from exc
```

### Lifecycle observational hooks

`agent_start`, `agent_end`, `turn_start`, `turn_end`, `message_start`,
`message_update`, `message_end`, `tool_execution_start`,
`tool_execution_update`, `tool_execution_end`, `settled` — handler 반환값은
무시됩니다.

handler 예외 → **`_log.debug(..., exc_info=True)` 후 swallow**. Loop가
계속 진행됩니다.

```python
# src/aelix/harness/core.py:523-528
try:
    await self._hooks.emit(hook_payload)
except Exception as exc:  # noqa: BLE001
    _log.debug("lifecycle hook handler raised: %r", exc, exc_info=True)
```

## Rationale

- **Mutation hook**: 예외 발생 시 reducer 결과가 정의되지 않은 상태입니다
  (예: `tool_call` block 여부 불명확, `context` messages 변환 미완료).
  정의되지 않은 상태에서 loop를 계속하면 더 심각한 오류로 이어질 수 있으므로
  즉시 abort가 올바른 선택입니다. 디버깅도 용이합니다.
- **Lifecycle observational hook**: 반환값이 loop에 영향을 주지 않습니다.
  관찰자 코드(telemetry, logging, UI 갱신 등)의 버그가 production loop를
  중단시키면 안 됩니다. `debug` 레벨 로깅으로 개발자가 문제를 인지할 수 있게
  하되 loop는 유지합니다.
- Pi는 mutation/lifecycle 구분 없이 `"continue"` default를 사용했으나,
  Aelix는 production 안전성과 디버깅 편의를 함께 충족하기 위해 분리합니다.

## Consequences

- `src/aelix/harness/core.py:523-528`이 lifecycle emit을 `try/except Exception`
  + `_log.debug`로 처리합니다.
- Mutation reducer 호출부(`_transform_context_bridge`, `_before_tool_call_bridge`,
  `_after_tool_call_bridge`, `_emit_before_agent_start`)는 각각 개별
  `try/except`로 `AgentHarnessError`를 raise합니다.
- `test_lifecycle_handler_exception_does_not_break_prompt` 테스트가
  lifecycle observational 분기를 pin합니다.
- `test_handler_raises_propagates_as_harness_error` 테스트가 mutation hook
  분기를 pin합니다.
- `HookBus.emit` 자체는 예외를 swallow하지 않습니다. 예외 처리 책임은
  harness 호출부(`core.py`)에 있습니다. 이는 bus와 harness 사이의 책임 분리를
  명확히 합니다.
- 향후 `errorMode: "continue" | "throw"` config option을 mutation hook에도
  확장하거나, per-handler `errorMode` 지정이 필요할 경우 별도 ADR로
  처리합니다.

---

Phase 1.2 임시 결정이며 Phase 2.1에서 ADR-0019로 대체될 예정.
