# Initial Requirements

Status: Draft

## 배경

이 프로젝트는 Python 기반 AI agent runtime으로 시작합니다. 초기 대화에서의
핵심 방향은 "아주 유연하게 확장 가능한 구조"와 "확장 marketplace"입니다.
따라서 구현은 단일 에이전트 기능보다 runtime kernel, extension contract,
extension ecosystem을 먼저 안정화하는 방향으로 진행합니다.

구현 기준은 `pi agent`를 Python으로 거의 완벽하게 재구현하는 것입니다. 다만
pi agent가 제공하는 날것에 가까운 유연성을 그대로 복제하는 데서 끝나지 않고,
기본 빌트인 확장으로 permission system과 최소 guardrail을 포함해 더 안정적인
기반에서 시작합니다.

## 핵심 요구사항

### Runtime

- Aelix는 Python 기반 agent runtime이어야 합니다.
- Aelix는 pi agent의 핵심 agent runtime 모델과 사용 경험을 Python으로 충실히 구현해야 합니다.
- Runtime은 특정 agent 유형이나 특정 domain에 묶이면 안 됩니다.
- Runtime core는 작고 명확해야 하며, 확장은 extension로 분리해야 합니다.
- Runtime은 coding, office, ops, analysis, platform automation을 모두 수용할 수 있어야 합니다.
- Runtime은 장기적으로 orchestration, policy engine, multi-agent coordination을 포함할 수 있어야 합니다.

### Extensibility

- 기능은 `Extension` 단위로 확장되어야 합니다.
- Extension는 독립적으로 발견, 설치, 검증, 실행될 수 있어야 합니다.
- Extension author가 사용할 SDK가 필요합니다.
- Runtime 내부 구현과 extension 구현은 강하게 결합되면 안 됩니다.
- Extension metadata는 코드 실행 없이 읽고 검증할 수 있어야 합니다.
- Permission system과 최소 guardrail은 built-in extension 또는 built-in runtime extension으로 제공되어야 합니다.

### Marketplace

- 확장 marketplace를 구축할 수 있는 구조여야 합니다.
- Marketplace의 기본 단위는 extension pack입니다.
- Marketplace는 multi-source index 모델로 동작해야 하며, npm registry, git repository, 사내 custom index를 1st-class source로 지원해야 합니다(ADR-0005).
- Pack metadata 형식은 index source가 책임지며, 단일 manifest 형식을 강제하지 않습니다. 다만 모든 source는 id, name, version, description, entrypoint, permissions에 해당하는 정규화된 metadata를 노출해야 합니다.
- Marketplace는 장기적으로 검색, 검증, 서명, 설치, 업데이트, 폐쇄망 배포를 지원해야 합니다. Trust 모델과 서명 형식은 source별 책임이며 후속 ADR에서 결정합니다.

### Enterprise and Customer-Site Use

- 고객사 폐쇄망과 사내 플랫폼 배포를 고려해야 합니다.
- 권한, 정책, 감사 로그는 runtime의 주변 기능이 아니라 핵심 설계 요소여야 하며, runtime core가 아닌 built-in extension으로 강제되어야 합니다(ADR-0004).
- 외부 네트워크 접근이 없는 환경에서도 extension pack을 관리할 수 있어야 합니다.
- Runtime은 domain platform 위나 내부에 embedding될 수 있어야 합니다.
- Guardrail은 과도하게 폐쇄적인 정책 엔진이 아니라 초기 안정성을 위한 최소 방어선으로 시작해야 합니다.

### Product Scope

- Aelix는 단순 coding agent가 아닙니다.
- Aelix는 "Enterprise Agent OS"에 가까운 방향을 지향합니다.
- DeepSight는 우선 별도 domain platform으로 보고, Aelix는 범용 상위 runtime으로 둡니다.

## 초기 비목표

다음은 초기 단계에서 바로 해결하지 않습니다.

- 완전한 marketplace 서버 구현
- extension sandboxing의 최종 보안 모델
- multi-agent planner의 최종 알고리즘
- 특정 LLM provider에 종속된 agent loop
- UI marketplace 구현
- 복잡한 enterprise compliance suite 전체 구현

이 항목들은 설계상 고려하되, 초기 kernel을 복잡하게 만들 정도로 앞당기지 않습니다.

## 열린 질문

초기 5개 열린 질문은 모두 ADR로 정리되었습니다. 결정 인덱스와 현재 상태는
[`decisions/README.md`](decisions/README.md)를 참조합니다.

- Extension isolation은 process, container, remote execution 중 무엇을 기본으로 할 것인가? → ADR-0007 (in-process).
- Marketplace pack signing과 trust chain은 어떤 형식으로 가져갈 것인가? → ADR-0005 + ADR-0010 (source-책임).
- Aelix SDK는 Python-only로 시작할 것인가, 다언어 manifest contract를 먼저 열 것인가? → ADR-0009 (Python-first).
- Agent orchestration은 runtime core에 둘 것인가, extension layer로 둘 것인가? → ADR-0008 (loop=core, orchestration=extension).
- DeepSight와의 통합은 product integration인가 extension pack인가? → ADR-0006 (Mode A Product Integration).
