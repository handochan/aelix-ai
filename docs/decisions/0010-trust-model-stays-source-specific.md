# 0010. Trust Model Stays Source-Specific; No Unified Schema in 1st Cut

Status: Accepted

## Context

`02-initial-requirements.md`의 Open Question Q2는 "Marketplace pack signing과
trust chain은 어떤 형식으로 가져갈 것인가"를 미결정 항목으로 두었습니다.

ADR-0005는 marketplace를 multi-source index로 정의하면서 "Pack signing과
trust chain은 source별 책임이며, 공통 marketplace 인터페이스는 각 source의
trust verdict만 노출한다"고 부분 결정했습니다. Q2는 그 위에 "1차에 통합 trust
verdict schema를 도입할 것인가"라는 후속 질문으로 좁혀집니다.

다른 결정의 영향:

- ADR-0007 in-process 실행 → 코드가 import되면 막을 수 없음. trust 결정은
  import 전에 끝나야 한다는 제약은 source가 이미 책임집니다.
- ADR-0002 small kernel → 1차 protocol surface는 작아야 합니다.
- ADR-0006 customer-site 청중 → 진짜 trust 요구가 있으나, customer-site는
  사내 custom index를 통해 자기 trust 모델을 가져올 수 있습니다.

Pi 자체는 자체 signing/trust 모델이 없으며, npm package signature, git
commit signature 같은 source-native 도구에 위임합니다.

## Decision

1차 Aelix marketplace는 **통합 trust verdict schema를 정의하지 않습니다**.

- ADR-0005의 source-책임 모델을 그대로 유지합니다.
- 각 source는 자기 trust 모델을 자기 방식으로 표현합니다.
  - npm: registry signature, hash, version pinning
  - git: commit signature, tag signature
  - 사내 custom index: 내부 PKI, internal approval workflow, 보안 검증 결과
- Aelix marketplace 인터페이스는 source가 노출하는 verdict가 통과했는지
  아닌지만 받아들이며, 통과 기준은 source 안에서 정의합니다.

통합 audit 또는 cross-source verdict 비교가 명확한 요구가 될 때 별도 ADR로
통합 schema를 도입합니다. 그 시점에는 적어도 다음을 고려해 결정합니다.

- status, signer, signature_type, evidence 필드
- Sigstore / GPG / cosign 같은 표준과의 호환
- internal-only source가 schema에 대응하는 방법

## Consequences

- 1차 marketplace 구현은 source-specific trust 도구를 그대로 노출하고,
  Aelix가 자체 signature/hash format을 정의하지 않습니다.
- Customer-site 환경(ADR-0006)은 사내 custom index가 자기 trust 모델을 그대로
  가져오면 되며, Aelix가 새 형식을 요구하지 않습니다.
- Pi parity가 유지됩니다 (Pi도 source-native trust에 위임).
- 외부 개발자에게는 1차 진입장벽이 낮습니다. signing이 필수가 아니라 source의
  선택입니다.
- audit/감사 통합이 필요해질 때까지 통합 schema 결정을 미룹니다.

