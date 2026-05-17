# 0019. Hook Error Policy v2 — Pi `"continue"` Default + per-handler `errorMode="throw"` opt-in

Status: Draft (Phase 2.1)
Supersedes: ADR-0014

## Context

ADR-0014는 hook을 두 분류로 나누어 예외 처리를 달리했습니다: mutation hook 예외는
`AgentHarnessError("hook")`로 raise + loop abort, lifecycle observational 예외는
swallow + `_log.debug`. 이는 Aelix가 Pi와 의도적으로 다른 정책을 취한 첫 번째 사례입니다.

Pi는 `packages/agent/docs/hooks.md:355-365`에서 모든 hook에 `"continue"` default를
명시합니다 — extension handler에서 예외가 발생하면 무시하고 loop를 계속 실행합니다.
이는 extension 버그가 user agent를 종료시키지 않도록 deliberate하게 설계된 것입니다.

ADR-0014의 mutation throw 정책은 "production 안전성과 디버깅 편의를 위한 Aelix
divergence"로 기록됐습니다. 그러나 1차 원칙(Pi parity) 재확인 후 이 divergence는
명시적 ADR(이 문서)로 대체되어야 합니다. Aelix도 Pi와 같은 `"continue"` default를
채택하되, per-handler `error_mode="throw"` opt-in으로 디버깅 강점을 보존합니다.

## Decision

Phase 2.1에서 hook error policy를 Pi `"continue"` default로 통일합니다.

### Default: `"continue"` (Pi parity)

모든 hook handler 예외를 `_log.debug(..., exc_info=True)` 후 swallow합니다.
mutation hook / lifecycle observational hook 구분 없이 동일하게 적용합니다.

```python
# HookBus 내부 handler 실행 (개념적 예시)
try:
    result = await handler(event)
except Exception as exc:
    _log.debug("hook handler raised (continuing): %r", exc, exc_info=True)
    result = None  # reducer에 None 전달
```

### per-handler opt-in: `error_mode="throw"`

`error_mode="throw"`를 지정한 handler에서 예외가 발생하면 기존 ADR-0014 행동
(`AgentHarnessError("hook")` raise + loop abort)을 유지합니다.

```python
# Extension API 사용 예시
pi.on("tool_call", handler, error_mode="throw")
harness.hooks.on("context", handler, error_mode="throw")
```

### 기존 테스트 호환

Phase 2.1 전까지 기존 mutation hook throw 테스트는 `error_mode="throw"`를
명시적으로 지정하도록 갱신합니다. 이로써 silent swallow 버그를 방어하면서
Pi parity를 회복합니다.

## Consequences

- `HookBus.on` 시그니처에 `error_mode: Literal["continue", "throw"] = "continue"` 추가.
- `ExtensionAPI.on` 동일.
- `HookBus` 내부 dispatch loop에서 handler별 `error_mode` 확인 후 분기.
- 기존 mutation hook `try/except` 호출부(`_transform_context_bridge` 등)는
  Phase 2.1에서 `error_mode="throw"` 등록 방식으로 리팩터링합니다.
- 신규 테스트: `test_hook_error_continue_default` — `error_mode` 기본값에서
  handler 예외가 loop를 abort하지 않음을 확인합니다.
- 기존 테스트 갱신: `test_handler_raises_propagates_as_harness_error`는 `error_mode="throw"` 명시.
- Aelix 1차 원칙(Pi parity) + 2차 원칙(디버깅 강점) 모두 충족합니다.
