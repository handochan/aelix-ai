# 0030. Hook Event Exhaustiveness via assert_never

Status: Draft (Phase 2.1 implementation)

Relates to: ADR-0017 (Full Hook Event Catalogue v2)

## Context

현재 `_to_hook_event` (`harness/core.py:628-665`)는 chain of `if t == "..."` 구조로
구현되어 있으며, default fallback에서 `None`을 반환합니다:

```python
def _to_hook_event(event: RawEvent) -> HookEvent | None:
    t = event["type"]
    if t == "message_start":
        return MessageStartEvent(...)
    if t == "tool_call":
        return ToolCallEvent(...)
    # ... 14 more branches ...
    return None  # unknown event type — silent omission
```

Pi는 TypeScript `switch (event.type)` + exhaustive narrowing을 사용합니다:

```typescript
switch (event.type) {
  case "message_start": return new MessageStartEvent(...)
  // ...
  default: assertNever(event.type)  // TypeScript compile error on new type
}
```

ADR-0017이 event catalogue를 16개에서 ~30개로 확장할 때, 새 event를 추가하면서
`_to_hook_event`에 branch를 추가하지 않으면 **silent omission**이 발생합니다.
이 버그는 런타임에서도 잡히지 않습니다.

## Decision

Phase 2.1에서 `_to_hook_event` 패턴을 `match event["type"]:` +
`case _: assert_never(event["type"])`로 변환합니다:

```python
from typing import assert_never

def _to_hook_event(event: RawEvent) -> HookEvent:
    match event["type"]:
        case "message_start":
            return MessageStartEvent(...)
        case "tool_call":
            return ToolCallEvent(...)
        # ... all ~30 branches ...
        case _ as unreachable:
            assert_never(unreachable)
```

`assert_never`는 Python 3.11+ `typing` 모듈에 포함됩니다.

Pyright / mypy는 `case _: assert_never(...)` 패턴을 정적으로 검사합니다.
새 `HookEventName` Literal이 추가되고 `match` branch가 없으면 **type check 단계에서
오류**가 발생합니다.

## Consequences

- 새 hook event 추가 시 `_to_hook_event` branch 누락 → pyright build fail.
  CI에서 즉시 감지됩니다.
- Pi의 `switch` fall-through 위험이 없습니다. Pi TypeScript는 exhaustive check를
  위해 `assertNever` 헬퍼가 필요하지만 Aelix는 `typing.assert_never`를 표준
  라이브러리에서 직접 사용합니다.
- `None` 반환 타입이 제거됩니다. caller에서 `if event is None: continue` 패턴을
  제거할 수 있습니다.
- ADR-0017 land 시 함께 적용합니다. 28 event로 전환하는 시점에서 효과가
  극대화됩니다.
- Aelix-specific safety improvement입니다. Pi-side에는 동일 수준의 정적 보장이
  없습니다 (Pi parity gap §4-item-2 참조).
- `typing.assert_never`는 Python 3.11+ requirement를 강화합니다. 이미 충족된
  요건입니다 (ADR-0015, Sprint 2 pyproject.toml).
