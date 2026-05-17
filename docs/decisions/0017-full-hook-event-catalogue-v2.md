# 0017. Full Hook Event Catalogue v2

Status: Draft (Phase 2.1 finalization)
Supersedes: ADR-0011

## Context

ADR-0011은 Phase 1.2의 16개 event만 binding contract으로 등록했습니다. 이는
Phase 1.2 범위를 제한하기 위한 임시 결정이었습니다.

Pi `AgentHarnessEvent` + `ExtensionEvent` union은 약 30개 event를 포함합니다.
Pi는 loop-emitted `AgentEvent` (~10개)와 harness-emitted `AgentHarnessEvent`
(~30개)를 코드 레벨에서 구분합니다. ADR-0011은 이 구분 없이 16개를 단일
목록으로 등록했습니다.

1차 원칙(Pi parity)에 따라 모든 Pi event를 Phase 2.1에 등록해야 합니다.

## Decision

Phase 2.1에서 다음 14+ event를 `HookEventName` Literal에 추가합니다.
또한 코드에서 `AgentEvent`(loop-emitted)와 `AgentHarnessEvent`(harness-emitted)를
명시적으로 구분합니다.

### Phase 2.1에 추가될 events

| 그룹 | Event | Pi source |
| --- | --- | --- |
| Provider hooks | `before_provider_request` | `agent-harness.ts:250-291` |
| Provider hooks | `before_provider_payload` | `agent-harness.ts:358-389` |
| Provider hooks | `after_provider_response` | `agent-harness.ts:376-380` |
| Model/thinking | `model_select` | `agent-harness.ts` |
| Model/thinking | `thinking_level_select` | `agent-harness.ts` |
| Queue | `queue_update` | `agent-harness.ts` |
| Queue | `save_point` | `agent-harness.ts` |
| Queue | `abort` | `agent-harness.ts` |
| Input | `input` | `agent-harness.ts` |
| Input | `user_bash` | `agent-harness.ts` |
| Session (Phase 2.2 emit) | `session_start` | `agent-harness.ts:681-735` |
| Session (Phase 2.2 emit) | `session_before_switch` | `agent-harness.ts` |
| Session (Phase 2.2 emit) | `session_before_fork` | `agent-harness.ts` |
| Session (Phase 2.2 emit) | `session_before_tree` | `agent-harness.ts:737-835` |
| Session (Phase 2.2 emit) | `session_compact` | `agent-harness.ts:681-735` |
| Session (Phase 2.2 emit) | `session_tree` | `agent-harness.ts:737-835` |
| Session (Phase 2.2 emit) | `session_shutdown` | `agent-harness.ts` |
| Resource | `resources_discover` | `agent-harness.ts` |
| Resource | `resources_update` | `agent-harness.ts` |

Session group events는 Phase 2.1에 `HookEventName` Literal에 등록하되,
실제 emit site는 Phase 2.2 Session Manager 구현 시 추가합니다.

## Consequences

- `HookEventName` Literal을 16개에서 ~30개로 확장합니다.
- `HOOK_RESULT_TYPES` registry에 신규 event 항목을 추가합니다.
- 모든 reducer rule을 Pi 그대로 port합니다.
- `AgentEvent`(loop-emitted)와 `AgentHarnessEvent`(harness-emitted)를 코드에서
  명시적으로 구분합니다(타입 alias 또는 별도 Literal).
- drift 방어 테스트: `test_hook_event_name_literal_matches_pi_event_set`를 추가합니다.
  Pi event 목록을 fixture로 pin하고 Aelix Literal과 비교합니다.
- ADR-0011의 "binding contract은 16개" 조항은 이 ADR로 대체됩니다.
  이후 event 추가/제거/result type 변경은 새 ADR 또는 이 ADR의 개정으로 처리합니다.
