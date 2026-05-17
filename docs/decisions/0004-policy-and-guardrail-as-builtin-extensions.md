# 0004. Policy and Guardrail as Built-in Extensions

Status: Accepted

## Context

ADR-0003은 Aelix가 pi agent의 핵심 runtime 경험을 Python으로 충실히 구현하되,
안정적인 기반을 위해 permission system과 minimal guardrail을 built-in extension
으로 포함한다고 결정했습니다.

그러나 초기 구현은 `PolicyEngine`을 runtime core(`AgentRuntime.invoke`)가 직접
호출하도록 만들어서 ADR-0003의 "built-in **extension**으로 포함" 표현을 실질적
으로 위반했습니다. 이로 인해 다음 문제가 생깁니다.

- core가 정책 모델을 직접 알고 있어 ADR-0002 "작은 kernel" 원칙과 충돌
- 정책을 끄거나 교체할 수 없어 ADR-0003이 의도한 유연성이 사라짐
- pi agent의 lifecycle event/tool intercept hook 멘탈 모델과 결이 어긋남

## Decision

Aelix Runtime core는 pi agent와 동등하게 minimal하게 유지합니다. core의 책임은
다음으로 제한합니다.

- extension invocation loop
- ExecutionContext 생성과 전달
- extension registry 관리
- lifecycle event와 tool intercept hook 노출

Policy와 guardrail은 runtime core에 박지 않고, **lifecycle hook을 구독하는
built-in extension** 형태로 제공합니다.

- 기본 배포 번들은 default policy extension과 minimal guardrail extension을 포함
  합니다.
- 사용자는 built-in extension을 끄거나 다른 extension으로 교체할 수 있어야
  합니다.
- core는 어떤 정책도 강제하지 않으며, extension은 hook을 통해 invocation을
  intercept, gate, modify할 수 있습니다.

## Consequences

- `runtime.py`는 정책 결정을 직접 호출하지 않습니다. policy 호출은 hook 발행으로
  대체됩니다.
- 기존 `policy.py`의 `PolicyEngine`은 built-in extension으로 재구성하여
  `src/aelix/builtin/` (가칭) 하위로 이동합니다.
- Extension protocol의 `permissions` 필드는 strict gate가 아니라 extension이
  해석할 수 있는 hint로만 유지합니다.
- `ExecutionContext`의 audit 필드(`actor`, `tenant`, `metadata`)는 유지합니다.
  audit는 pi parity와 직교한 요구이며 built-in audit extension이 활용합니다.
- pi agent의 lifecycle event 카테고리(lifecycle, resource, session, agent,
  model, tool)를 참고하여 hook API를 설계합니다.
- `03-architecture-principles.md`의 Principle 3, 3.1은 "core가 강제"가 아니라
  "built-in extension이 hook으로 강제"로 표현을 갱신해야 합니다.

