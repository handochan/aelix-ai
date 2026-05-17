# 0013. message_end Reducer Policy — Observational in Phase 1.2

Status: Accepted (Phase 1.2 임시 결정)
Superseded by: Sprint 3b P-3 verdict (ADR-0018 deprecated); message_end remains observational; Pi has no replacement reducer at SHA 734e08e.

## Context

Pi agent의 `message_end` event는 **replacement reducer**를 지원합니다.
`/tmp/pi-ext-runner.ts:714-754`에서 handler가 `{ message: AgentMessage }`를
반환하면 runner가 role-preservation 검사를 거쳐 메시지를 교체합니다
(role mismatch 시 error emit + skip).

Phase 1.2 sprint spec(Section A3, D.1.13 M-2)은 이 reducer를 Phase 1.2에
포함하지 않고 observational로 시작하기로 결정했으며, 후속 ADR로 forward했습니다.

## Decision

Phase 1.2의 `message_end`는 **observational only**입니다.

- `MessageEndHookEvent`의 result type은 `None`입니다(`HOOK_RESULT_TYPES["message_end"]
  = None`).
- handler 반환값은 무시됩니다. reducer는 `_reducer_observational`을 사용합니다.
- 메시지 replace 기능은 Phase 2.x 이후 별도 ADR에서 검토합니다.

## Rationale

replacement reducer를 Phase 1.2에 포함하려면 두 가지가 추가로 필요합니다.

1. **Role-preservation 강제 로직** — handler가 반환한 메시지의 role이 원본과
   다를 때 runner가 error를 emit하거나 skip해야 합니다. Pi는 이를
   `ext-runner.ts`에서 처리하지만 Aelix 1.2 `HookBus`는 이 검사를 지원하지
   않습니다.
2. **Downstream loop message list 인덱싱** — replace가 `agent_loop`의 message
   list에 by-reference로 반영되려면 loop가 emit 결과를 receive해 list를
   mutation해야 합니다. 이는 ADR-0011의 D.1.5 args-mutation contract와 유사한
   위험(미정의 상태)을 만들 수 있습니다.

두 복잡성 모두 Phase 1.2 scope와 맞지 않으며, Phase 1.2의 lifecycle observational
hook 분리 정신(ADR-0014)과도 일치합니다.

## Consequences

- 현재 `MessageEndHookEvent`는 `HookEvent[None]` 에 상당하며, handler 반환값은
  harness에 도달하지 않습니다.
- `src/aelix/harness/hooks.py`에서 `_REDUCERS["message_end"] =
  _reducer_observational`로 구현되어 있습니다.
- `test_message_end_observational` 테스트가 이 결정을 pin합니다 — handler가
  무언가를 반환해도 loop의 message list가 변경되지 않음을 확인합니다.
- Phase 2.x 또는 향후 ADR-NNNN에서 replacement reducer를 추가할 수 있습니다.
  단, reducer 추가는 `message_end`의 result type을 `None`에서 변경하므로
  binding ADR로 처리해야 합니다(ADR-0011의 binding contract 조항).

---

Phase 1.2 임시 결정이며 Phase 2.1에서 ADR-0018로 대체될 예정.
