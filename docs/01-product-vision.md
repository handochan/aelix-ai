# Product Vision

Status: Draft

## 정체성

Aelix는 단순한 코딩 에이전트가 아니라 범용 agent runtime이자 extension
platform입니다. 목표는 coding, office assistant, operations automation,
analysis platform agent, customer-site automation 같은 여러 agent 워크로드를
하나의 확장 가능한 런타임 위에서 실행하는 것입니다.

초기 구현의 가장 직접적인 기준점은 `pi agent`입니다. Aelix는 pi agent의 핵심
경험과 구조를 Python으로 거의 완벽에 가깝게 재구현하는 것을 목표로 하되, 너무
날것에 가까운 agent runtime에서 바로 시작하지 않도록 permission system과 최소
guardrail을 기본 내장 확장으로 포함합니다.

제품의 감성은 다음 표현에 가깝습니다.

- Agent Runtime
- Agent OS
- Engineering Intelligence
- Industrial AI Runtime
- Extension Platform

반대로 다음 느낌은 피합니다.

- 단순 chatbot
- 단일 목적 assistant
- coding-only tool
- 폐쇄적인 automation script runner

## 브랜드 방향

프로젝트 이름은 `Aelix`를 우선 사용합니다.

`Aelix`는 `AI agent`와 `helix`의 결합 느낌을 가지며, 다음 이미지를 의도합니다.

- evolving intelligence
- branching extension
- living agent ecosystem
- marketplace-driven growth
- specialized expert agents
- extensible runtime civilization

`DeepSight`는 Aelix가 받는 여러 domain platform 중 하나로만 둡니다(ADR-0006).
Aelix는 DeepSight가 존재하지 않는다고 가정해도 의미를 가지는 standalone
runtime/platform이며, DeepSight를 포함한 여러 domain extension를 실행할 수
있는 상위 runtime/platform으로 위치합니다.

## 제품 구조 방향

```text
Aelix
  Aelix Runtime
    core execution kernel
    policy engine
    orchestration layer
  Aelix Marketplace
    extension packs
    offline/customer-site distribution
    validation and trust metadata
  Aelix SDK
    pack authoring
    local development
    testing utilities
```

## 성공 기준

Aelix는 다음을 만족해야 합니다.

- pi agent의 핵심 agent runtime 경험을 Python 기반으로 충실히 구현해야 합니다.
- 작은 kernel 위에 다양한 extension를 얹을 수 있어야 합니다.
- marketplace를 통해 extension 생태계를 만들 수 있어야 합니다.
- 사내망, 폐쇄망, 고객사 배포를 고려해야 합니다.
- coding agent에 갇히지 않고 범용 agent runtime으로 확장되어야 합니다.
- 정책, 권한, 감사 가능성은 runtime의 핵심 개념이되, runtime core가 아닌 built-in extension으로 강제되어야 합니다(ADR-0004).
- permission system과 최소 guardrail은 optional add-on이 아니라 기본 번들에 포함된 built-in extension으로 안정적인 출발점에서 제공되어야 합니다.
