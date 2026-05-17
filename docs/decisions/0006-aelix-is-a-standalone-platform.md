# 0006. Aelix is a Standalone Platform

Status: Accepted

## Context

ADR-0001은 Aelix를 제품 이름으로 선택했고, ADR-0003은 pi agent를 primary
reference로 두었습니다. ADR-0001의 Consequences는 "DeepSight는 별도 domain
platform 또는 향후 extension ecosystem의 일부로 둡니다"라고 양가적으로 표현
했습니다.

`04-reference-projects.md`는 deepsight-ai를 internal reference로 두며 "Aelix를
다시 시작하게 된 직접적인 배경"이라고 설명합니다. `01-product-vision.md`는
"DeepSight가 특정 domain platform이라면 Aelix는 그 위에 위치하는 상위
runtime/platform"이라고 적었습니다.

그러나 Aelix가 누구를 위한 제품인지, DeepSight가 Aelix 설계에 어느 정도 bias를
주어야 하는지가 명시되지 않아 후속 결정이 흔들리기 쉬웠습니다.

## Decision

Aelix는 standalone runtime/platform 제품으로 정의합니다.

1. Aelix의 1st-class 청중은 다음 세 집단입니다.
   - 외부 개발자 (오픈소스 사용자, extension 작성자)
   - 사내 팀 (Aelix를 자체 플랫폼으로 채택하는 다른 부서/프로젝트)
   - Customer-site 운영자 (고객사 폐쇄망, on-prem, regulated 환경)

2. DeepSight는 Aelix가 받는 여러 use case 중 하나로만 위치합니다. Aelix 설계
   의사결정에서 DeepSight 요구는 reference 또는 example로만 다루고, bias로
   삼지 않습니다.

3. Aelix 설계는 DeepSight가 존재하지 않는다고 가정해도 의미가 있어야 합니다.

## Consequences

- ADR-0001 Consequences의 양가적 표현("별도 domain platform 또는 향후
  extension ecosystem의 일부")은 "별도 domain platform"으로 narrowing합니다.
- `01-product-vision.md`의 DeepSight 관련 표현은 약화하되, Aelix가 다양한
  domain platform을 받을 수 있는 상위 runtime이라는 일반 표현은 유지합니다.
- `04-reference-projects.md`의 deepsight-ai 섹션은 reference로 유지하되, Aelix
  동기로서의 weight는 낮춥니다. 다른 reference(opencode, Claude Code 등)와
  같은 등급으로 둡니다.
- Marketplace 결정(ADR-0005)의 multi-source index 정책은 외부 개발자 + 사내 +
  customer-site 세 청중에 대한 1st-class 약속과 일관됩니다.
- SDK는 internal helper가 아니라 외부 공개 가능한 product layer로 격상됩니다.
  단 SDK의 언어 범위와 우선순위는 별도 ADR(Open Question Q3)에서 결정합니다.
- Aelix를 단독으로 평가/배포할 수 있는 standalone 사용 경로(예: `python -m aelix`
  데모, 외부 quickstart)는 항상 유지합니다.

