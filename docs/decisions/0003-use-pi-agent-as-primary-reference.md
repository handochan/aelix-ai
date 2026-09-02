# 0003. Use pi agent as the Primary Reference

Status: Accepted — **`## Pi Parity Binding Rule` RETIRED by ADR-0235** (2026-09-03)

## Context

Aelix는 Python 기반 agent runtime으로 시작합니다. 초기 방향은 범용 runtime,
extension platform, marketplace였지만, 구현 기준이 너무 넓으면 초기 제품의
중심이 흐려질 수 있습니다.

사용자는 pi agent를 거의 완벽에 가깝게 Python으로 구현하고, 그 위에 기본
permission system과 최소 guardrail을 포함하기를 원합니다.

## Decision

Aelix의 primary implementation reference는 `pi agent`로 둡니다.

Aelix는 pi agent의 핵심 runtime 경험을 Python으로 충실히 구현하되, 안정적인
기반을 위해 다음을 built-in extension으로 포함합니다.

- permission system
- minimal guardrails
- audit-friendly execution context

## Consequences

- 초기 구현 우선순위는 abstract enterprise platform보다 pi agent parity에 둡니다.
- Permission과 guardrail은 나중에 붙이는 optional plugin이 아니라 기본 배포에 포함합니다.
- Marketplace와 SDK 설계도 pi agent-compatible mental model을 해치지 않아야 합니다.
- Reference comparison은 `docs/04-reference-projects.md`에서 계속 관리합니다.

## Pi Parity Binding Rule — RETIRED by ADR-0235

> **Retired (2026-09-03).** 이 규칙은 포팅이 진행되는 동안 유효했고, ADR-0086이
> A 단계 종료를 기록한 뒤로는 값을 하지 못했다. ADR-0235가 폐지한다 — Pi와의
> divergence는 더 이상 버그가 아니며 ADR로 정당화할 필요가 없다. Pi는 *검증된
> 참조 구현*으로 남고, 최신 상태를 참고한다. ADR-0003의 나머지(Pi를 primary
> reference로 두는 것, permission·guardrail의 built-in 포함)는 그대로 유효하다.
>
> 아래 원문은 이 규칙 아래 내려진 176개 ADR의 맥락을 위해 보존한다.

Aelix의 Pi와 다른 모든 동작은 명시적 ADR로 정당화되어야 합니다. ADR 없는 divergence는 버그로 간주됩니다. ADR-0004 (built-in policy/guardrail) 같은 의도적 divergence는 ADR로 명시되어야 하며, 그 외 silently diverging 코드는 fix 대상입니다.

