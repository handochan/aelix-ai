# 0007. In-Process Extension Execution by Default

Status: Accepted

## Context

`02-initial-requirements.md`의 Open Question Q1은 "Extension isolation:
process / container / remote 중 무엇을 기본으로 할 것인가"를 미결정 항목으로
두었습니다.

Pi 조사 결과는 다음과 같습니다.

- Pi는 isolation을 의도적으로 구현하지 않습니다 ("primitives, not features").
- Extension은 in-process TypeScript module로 실행됩니다.
- 공식 권장 격리는 "Run in a container" 같은 OS-level 외부 격리입니다.
- Permission system은 extension이 자기 안에서 confirm 흐름을 짜는 패턴입니다.
- OpenClaw가 Pi를 embed할 때도 in-process로 실행하고 tool filtering, session
  manager wrapping, optional sandbox로 가드를 외부에서 얹습니다.

ADR-0003은 Aelix가 pi agent의 핵심 runtime 경험을 충실히 재구현한다고
결정했습니다. ADR-0004는 policy/guardrail을 built-in extension의 lifecycle
hook으로 강제한다고 결정했습니다. ADR-0005는 marketplace를 multi-source
index로 설계하며 source별 trust verdict를 노출한다고 결정했습니다.

이 흐름에서 isolation을 protocol 차원으로 강제하는 것은 다음 문제를 만듭니다.

- pi parity와 어긋남 (Pi는 isolation을 두지 않음)
- ADR-0002 "small kernel" 원칙과 충돌
- 실제 trust 강화는 lifecycle hook과 marketplace source의 trust verdict가
  이미 책임지고 있음

## Decision

Aelix는 extension을 **in-process**로 실행합니다.

- runtime protocol에 isolation 개념을 두지 않습니다. manifest에 isolation
  필드를 두지 않으며, dispatcher 추상화를 미리 정의하지 않습니다.
- container, sandbox, remote execution 같은 강한 격리가 필요할 때는 다음 중
  하나로 흡수합니다.
  - OS-level 외부 격리 (Docker, podman, VM 등 — Aelix 외부 운영 책임)
  - sandbox built-in extension (lifecycle hook으로 위험한 동작을 가로채는 형태)
  - marketplace source의 trust verdict (사내 custom index가 신뢰 경계를 사전
    정의)

## Consequences

- runtime core는 extension invocation을 in-process 호출로 처리합니다.
  별도 dispatcher 계층을 두지 않습니다.
- pi agent의 in-process 모델과 정확히 같은 mental model이 유지됩니다.
- 강한 isolation 요구는 runtime protocol을 바꾸지 않고도 extension/sandbox/
  OS layer에서 처리할 수 있어 protocol 안정성이 보전됩니다.
- Customer-site 청중(ADR-0006)에 대한 격리 강화는 사내 custom index의 trust
  verdict와 OS-level 격리로 흡수합니다.
- 향후 in-process 모델로 막을 수 없는 위협이 명확해질 때만 isolation을
  protocol에 도입하기로 합니다. 그 시점에는 별도 ADR로 명시합니다.

