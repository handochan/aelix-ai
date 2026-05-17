# 0011. Hook Event Catalogue v1

Status: Accepted (Phase 1.2 임시 결정)
Superseded by: ADR-0017 (Full Hook Event Catalogue v2, Phase 2.1)

## Context

ADR-0004는 runtime core가 lifecycle hook을 노출하고, policy/guardrail은
built-in extension이 해당 hook을 구독하여 강제한다고 결정했습니다.

Phase 1.2 sprint spec(Section F)은 후속 ADR로 hook event catalogue를 정식 등록할
것을 forward했습니다. Phase 1.2는 `src/aelix/harness/hooks.py`에서 16개 event를
ship합니다. catalogue가 ADR로 pin되지 않으면 implementer가 event 목록과 reducer
규칙을 코드에서 직접 읽어야 하고, drift 방어 test와 문서 사이에 단일 참조점이
없어집니다.

Custom event registration API는 Phase 1.3+에서 별도 ADR로 결정합니다
(현재 `HookBus.on`은 `HOOK_RESULT_TYPES`에 없는 event type에 대해 `KeyError`를
raise하는 엄격한 closed-set 방식입니다).

## Decision

Phase 1.2의 정식 hook event 16개를 아래 표로 등록합니다. 이 목록은
`src/aelix/harness/hooks.py`의 `HookEventName` Literal 및 `HOOK_RESULT_TYPES`
registry와 동일한 single source of truth입니다.

### Mutation reducer hooks

handler 반환값이 loop semantics에 영향을 줍니다. handler 예외는
`AgentHarnessError("hook", ...)` 로 wrap되어 loop를 즉시 abort합니다
(ADR-0014).

| Event | Result type | Reducer rule |
| --- | --- | --- |
| `context` | `ContextResult` | Sequential transform. 각 handler가 `messages`를 patch. 다음 handler는 이전 patch 결과를 봄. |
| `before_agent_start` | `BeforeAgentStartResult` | messages는 모든 handler에서 collect 후 prepend; `system_prompt`는 chain — 다음 handler가 이전 chain 결과를 봄. |
| `tool_call` | `ToolCallResult` | Sequential. `block=True` 반환 시 즉시 short-circuit. `args` dict는 by-reference mutation (D.1.5). |
| `tool_result` | `ToolResultPatch` | Sequential patch accumulation. unset field는 이전 값 보존. |
| `session_before_compact` | `SessionBeforeCompactResult` | Sequential. `cancel=True` 반환 시 즉시 short-circuit. |

### Lifecycle observational hooks

handler 반환값은 무시됩니다. handler 예외는 swallow + `_log.debug` 처리되어
loop가 계속 진행됩니다 (ADR-0014).

| Event | Lifecycle 단계 | 비고 |
| --- | --- | --- |
| `agent_start` | agent loop 시작 직전 | |
| `agent_end` | agent loop 완료 후 | `messages` 필드에 새 메시지 포함 |
| `turn_start` | 각 turn 시작 직전 | |
| `turn_end` | 각 turn 완료 후 | `message` 필드에 assistant 메시지 |
| `message_start` | 스트림 메시지 시작 | |
| `message_update` | 스트림 메시지 청크 수신 | |
| `message_end` | 메시지 완료 | observational only — Phase 1.2. ADR-0013 참조. |
| `tool_execution_start` | tool execute 호출 직전 | |
| `tool_execution_update` | tool 실행 중 진행 알림 | |
| `tool_execution_end` | tool execute 반환 후 | |
| `settled` | harness가 idle로 복귀 | Phase 1.2 placeholder. emit site는 `harness/core.py`. |

### Reserved (not in Phase 1.2)

다음 두 event는 Phase 2.1에서 추가 예정입니다. Phase 1.2 `HookEventName` Literal에
포함되지 않습니다.

- `before_provider_request` — LLM 요청 직전 stream option patch
- `before_provider_payload` — provider payload replace

## Consequences

- 16개 event 목록은 **binding contract**입니다. event 추가/제거/result type 변경은
  새 ADR로 처리해야 합니다.
- `src/aelix/harness/hooks.py`의 `HookEventName` Literal과
  `HOOK_RESULT_TYPES` dict는 항상 이 표와 동기화되어야 합니다.
  `test_hook_event_name_literal_matches_hook_result_types_keys` 테스트가
  drift를 방어합니다.
- Reducer rule이 ADR로 pin되므로 implementer가 코드를 직접 분석하지 않아도
  각 event의 handler 반환값이 어떻게 합산되는지 알 수 있습니다.
- Custom event registration API는 이 ADR의 범위 밖입니다. Phase 1.3+에서
  별도 ADR로 결정합니다.
- `session_before_compact`와 `settled`는 Phase 1.2에서 reducer/event class만
  정의된 상태이며 emit site는 후속 phase에서 추가됩니다.

---

Phase 1.2 임시 결정이며 Phase 2.1에서 ADR-0017로 대체될 예정.
