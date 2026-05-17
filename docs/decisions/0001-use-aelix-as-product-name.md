# 0001. Use Aelix as the Product Name

Status: Accepted

## Context

초기 후보에는 Nexus, Helix, Forge, Aegis, Atlas, Fabric, DeepSight Nexus 등이
있었습니다. 프로젝트는 단순 coding agent보다 범용 agent runtime, extension
platform, marketplace ecosystem에 가깝습니다.

DeepSight는 이미 domain platform 성격을 가지고 있으므로, 새 runtime의 이름으로
바로 사용하는 것은 제품 경계를 흐릴 수 있습니다.

## Decision

프로젝트와 플랫폼 이름은 `Aelix`를 사용합니다.

## Rationale

`Aelix`는 `AI agent`와 `helix`의 느낌을 함께 가지며, agent evolution,
branching intelligence, living ecosystem, extension growth를 자연스럽게
표현합니다.

또한 특정 domain에 묶이지 않기 때문에 coding, office, ops, analysis,
customer-site automation으로 확장하기 좋습니다.

## Consequences

- Python package name은 `aelix`를 사용합니다.
- 제품 표현은 `Aelix Runtime`, `Aelix Marketplace`, `Aelix SDK`를 기본으로 합니다.
- DeepSight는 별도 domain platform으로 둡니다. Aelix 설계에서 DeepSight 요구는 reference로만 다루며, 자세한 위치는 ADR-0006을 따릅니다.

