# 0018. message_end Replacement Reducer (Pi parity)

Status: Draft (Phase 2.1)
Supersedes: ADR-0013

## Context

ADR-0013은 Phase 1.2에서 `message_end`를 observational only로 결정했습니다.
이는 role-preservation 강제 로직과 loop message list 인덱싱의 복잡성 때문이었으며,
Phase 1.2 scope와 맞지 않는다는 이유로 defer했습니다.

Pi agent의 `message_end` event는 replacement reducer를 지원합니다.
`/tmp/pi-ext-runner.ts:714-754`에서 handler가 `{ message: AgentMessage }`를
반환하면 runner가 role-preservation 검사를 거쳐 메시지를 교체합니다
(role mismatch 시 error emit + skip).

1차 원칙(Pi parity)에 따라 Phase 2.1에서 Pi와 동일하게 구현합니다.

## Decision

Phase 2.1에서 `message_end` reducer를 sequential replace로 구현합니다.

- `MessageEndEventResult.message?`가 `None`이 아니면 기존 message를 replace합니다.
- **role은 반드시 일치해야 합니다**: `assistant` → `assistant`, `user` → `user`.
  role이 일치하지 않으면 reducer가 error log + skip합니다.
- 각 handler의 반환값은 순차적으로 적용됩니다. 다음 handler는 이전 handler가
  replace한 결과를 봅니다(sequential replace chain).

```python
# 구현 예시 (Phase 2.1)
def _reducer_message_end(results, original_event):
    message = original_event.message
    for result in results:
        if result is None or result.message is None:
            continue
        if result.message.role != message.role:
            _log.error(
                "message_end handler returned message with mismatched role "
                "(expected %r, got %r) — skipping",
                message.role, result.message.role,
            )
            continue
        message = result.message
    return message
```

## Consequences

- `harness/hooks.py`의 `_REDUCERS["message_end"]`를 `_reducer_observational`에서
  `_reducer_message_end`(role-checking)로 교체합니다.
- `HOOK_RESULT_TYPES["message_end"]`를 `None`에서 `MessageEndEventResult`로 변경합니다.
  이는 ADR-0011의 binding contract 변경이므로 ADR-0017(v2 catalogue)과 함께 진행합니다.
- `agent/loop.py`에서 `MessageEndEvent` emit 시점에 replacement 적용:
  `context.messages[partial_index] = replaced`.
- 신규 테스트:
  - `test_message_end_replacement_preserves_role` — handler가 반환한 메시지가 루프에 반영됨
  - `test_message_end_role_mismatch_skips` — role 불일치 시 원본 메시지 유지
- ADR-0013의 `test_message_end_observational` 테스트는 Phase 2.1에서 제거 또는 갱신합니다.
