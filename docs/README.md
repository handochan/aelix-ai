# Aelix Docs

이 디렉터리는 Aelix의 제품 방향, 런타임 설계, 확장 생태계, 의사결정 기록을
관리하는 living documentation 공간입니다.

## 문서 구조

- `00-conventions.md`: 문서 작성 및 변경 컨벤션
- `01-product-vision.md`: 제품 정체성, 브랜드 방향, 목표 사용자
- `02-initial-requirements.md`: 초기 요구사항과 설계 제약
- `03-architecture-principles.md`: 런타임/확장/마켓플레이스 설계 원칙
- `04-reference-projects.md`: 계속 참조할 외부/내부 프로젝트 목록
- `decisions/`: 중요한 설계 결정 기록. 인덱스는 [`decisions/README.md`](decisions/README.md) 참조

## 문서 상태

각 문서는 상단에 상태를 표시합니다.

- `Draft`: 논의 중이며 변경 가능성이 큼
- `Accepted`: 현재 기준 설계로 채택됨
- `Deprecated`: 더 이상 따르지 않음
- `Superseded`: 다른 문서나 결정으로 대체됨

## 운영 원칙

Aelix 설계는 계속 진화될 수 있으므로 문서는 최종 명세가 아니라 현재 합의된
판단의 기록입니다. 큰 방향 변경은 기존 문서를 조용히 덮어쓰기보다
`decisions/`에 결정 기록을 남기고, 관련 문서의 상태나 참조를 갱신합니다.
