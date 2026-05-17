# 0005. Marketplace Supports Multi-Source Indexes

Status: Accepted

## Context

`03-architecture-principles.md`의 Principle 6은 marketplace가 public registry만
전제하면 안 되고 offline/customer-site 배포를 고려해야 한다고 정의합니다.
`02-initial-requirements.md`는 extension pack manifest 기반 marketplace를
가정하며, 초기 구현은 `aelix.toml`이라는 단일 manifest 형식을 가정했습니다.

그러나 단일 manifest 형식을 강제하는 것은 다음 문제를 만듭니다.

- pi agent는 별도 manifest 없이 npm/git package 그대로 사용
- 사내 customer-site는 자체 package 형식이나 internal index를 가질 수 있음
- 단일 형식 강제는 외부 개발자, 사내 팀, customer-site 청중을 모두 수용하기
  어려움

## Decision

Aelix Marketplace는 multi-source index 모델로 설계합니다. 초기 지원 source는
다음과 같습니다.

- npm registry
- git repository (직접 install)
- 사내 custom index (offline / customer-site)

단일 manifest 형식(`aelix.toml`)을 강제하지 않습니다. Index source는 plug-in
가능한 추상화로 정의하며, 각 source는 다음을 책임집니다.

- pack metadata 수집과 정규화
- pack 코드 위치 제공 또는 fetch
- source-specific trust 모델 (서명, hash, internal approval 등)

공통 marketplace 인터페이스는 source의 산출물(metadata, code location, trust
verdict)만 노출하며, 형식 차이를 source 안에서 흡수합니다.

## Consequences

- 기존 `aelix.toml` 형식 가정은 폐기합니다. 향후 marketplace 구현은 source
  abstraction부터 시작합니다.
- `marketplace.py`의 `MarketplaceIndex`, `ExtensionPack`, `ExtensionSpec`은
  index source 추상화로 재설계합니다.
- offline-friendly marketplace 요구(Principle 6)는 사내 custom index가 자연스
  럽게 처리합니다. 별도 offline mode가 아니라 source 추가로 달성합니다.
- Pack signing과 trust chain은 source별 책임이며, 공통 marketplace 인터페이스는
  각 source의 trust verdict만 노출합니다. 구체 신뢰 모델은 후속 ADR에서 결정
  합니다(Open Question Q2).
- Pack의 코드 import 시점은 source의 trust verdict가 통과한 후입니다.
  Principle 2 "Data Before Code"는 유지되지만 manifest 형식은 source 책임입니다.

