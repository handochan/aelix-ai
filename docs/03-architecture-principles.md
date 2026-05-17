# Architecture Principles

Status: Draft

## 1. Small Kernel, Large Ecosystem

Aelix Runtime의 kernel은 작아야 합니다. Kernel은 execution context, extension
routing, policy gate, lifecycle hook처럼 모든 extension에 공통으로 필요한
기능만 담당합니다.

복잡한 domain 기능은 runtime core가 아니라 extension 또는 extension pack으로
분리합니다.

## 2. Data Before Code

Marketplace와 설치 흐름에서는 코드를 import하기 전에 metadata를 먼저 읽어야
합니다. Extension pack metadata는 데이터만으로 index, audit, approve, sign할 수
있어야 합니다.

Pack metadata 형식은 marketplace index source가 책임지며, 단일 형식을 강제하지
않습니다(ADR-0005).

## 3. Policy Before Execution

Extension 실행 전에는 항상 policy decision이 있어야 합니다. 단 정책 강제는
runtime core가 아니라 lifecycle hook을 구독하는 built-in extension이 수행합니다
(ADR-0004). 정책 계층은 다음을 판단할 수 있어야 합니다.

- extension permission
- actor and tenant context
- workspace or network boundary
- customer-site deployment rule
- audit requirement

초기 Aelix는 pi agent의 유연성을 지향하지만, permission system은 built-in
extension으로 기본 제공되어야 합니다. 이는 enterprise-grade policy suite를
처음부터 완성하겠다는 뜻이 아니라, 파일/쉘/네트워크/외부 도구 같은 위험한
동작에 대한 최소 실행 경계를 runtime의 기본값으로 둔다는 뜻입니다.

## 3.1 Minimal Guardrails by Default

Guardrail은 Aelix의 기본 안정성 계층이며 built-in extension으로 제공됩니다
(ADR-0004). 초기 guardrail은 다음에 집중합니다.

- destructive action 확인
- 권한 없는 workspace 외부 접근 차단
- network/tool 사용 전 permission 확인
- extension manifest와 실제 요청 권한의 불일치 탐지
- audit 가능한 invocation metadata 유지

Guardrail은 agent의 자율성을 제거하는 장치가 아니라, 날것의 agent runtime 위에
안정적인 시작점을 제공하는 장치로 설계합니다.

## 4. Explicit Execution Context

모든 invocation은 명시적인 execution context를 가져야 합니다. Context에는 최소한
다음 개념이 들어갑니다.

- invocation id
- actor
- tenant
- created time
- metadata

이 정보는 감사, 재현, debugging, policy decision의 기준이 됩니다.

## 5. Provider Independence

Aelix는 특정 LLM provider에 종속되지 않아야 합니다. Provider integration은
runtime core 바깥의 adapter 또는 extension로 다루는 것을 기본으로 합니다.

## 6. Offline-Friendly Marketplace

Marketplace는 public registry만 전제하면 안 됩니다. npm registry, git
repository, 사내 custom index 등 multi-source index 모델을 지원하며(ADR-0005),
고객사 폐쇄망에서는 사내 custom index가 pack bundle, local index, signed
artifact, manual approval workflow 같은 요구를 흡수합니다.

## 7. Stable Contracts, Replaceable Internals

Extension protocol, manifest schema, policy decision 같은 외부 contract는
신중하게 변경합니다. 내부 구현은 초기에는 단순하게 유지하고, 요구가 생길 때
교체할 수 있게 합니다.

## 8. Reference-Driven Implementation

Aelix는 완전히 추상적인 설계에서 시작하지 않습니다. pi agent를 중심 reference로
삼고, opencode, oh my opencode, Claude Code, oh my claudecode, clawcodex,
deepsight-ai의 구조와 시행착오를 계속 비교하며 구현합니다. Reference 목록과
각 프로젝트에서 볼 관점은 `04-reference-projects.md`에 유지합니다.
