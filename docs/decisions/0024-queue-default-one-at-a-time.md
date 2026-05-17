# 0024. Queue Default `"one-at-a-time"` (Pi parity)

Status: Accepted (Phase 1.2 follow-up fix)

## Context

Pi `AgentOptions` / `AgentHarnessOptions`의 `steeringMode` / `followUpMode`
default는 `"one-at-a-time"`입니다. 하나의 turn이 완료되기 전에 다음 input이
queue되면 순서대로 처리합니다.

현재 Aelix는 `agent/agent.py:43-44`의 `steering_mode = "all"`과
`harness/core.py:121-122`의 `follow_up_mode = "all"`을 기본으로 사용합니다.
이는 의도 없는 silent divergence입니다(재평가 보고서 F-1).

`"all"` mode는 queue된 input을 모두 즉시 처리하므로 여러 concurrent turn이
interleave될 수 있습니다. Pi가 `"one-at-a-time"`을 기본으로 한 것은 사용자 경험
일관성과 session 상태 안전성을 위한 deliberate 설계입니다.

## Decision

즉시 flip합니다.

- `agent/agent.py:43-44`: `steering_mode = "all"` → `steering_mode = "one-at-a-time"`.
- `harness/core.py:121-122`: `follow_up_mode = "all"` → `follow_up_mode = "one-at-a-time"`.

이 변경은 Agent B (Pi parity quick fixes)가 처리합니다.

## Consequences

- Pi parity 회복. silent divergence 해소.
- 기존 테스트에서 `"all"` mode를 명시적으로 사용한 테스트는 `"all"`을 명시하도록
  수정합니다(기존 behavior를 테스트하는 경우).
- 단순 2-line default 변경. `"all"` mode 자체는 제거하지 않고 opt-in으로 유지합니다.
- `AgentOptions` / `AgentHarnessOptions` docstring에 default 변경을 반영합니다.
