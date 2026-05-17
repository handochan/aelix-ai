# Documentation Conventions

Status: Accepted

## 목적

이 문서는 Aelix 설계 문서를 일관되게 유지하기 위한 기준입니다. Aelix는
agent runtime, extension platform, marketplace를 함께 다루므로 제품 방향과
기술 설계가 쉽게 섞일 수 있습니다. 문서는 역할별로 분리하고, 변경 이유를
남기는 것을 우선합니다.

## 파일 명명

일반 문서는 번호 접두사를 사용합니다.

```text
00-conventions.md
01-product-vision.md
02-initial-requirements.md
03-architecture-principles.md
```

번호는 읽는 순서를 의미합니다. 문서가 추가되어도 기존 번호는 불필요하게
변경하지 않습니다.

중요한 설계 결정은 `decisions/` 아래에 ADR 형식으로 작성합니다.

```text
decisions/0001-use-aelix-as-product-name.md
decisions/0002-start-with-small-runtime-kernel.md
```

## 문서 상단 메타데이터

각 문서는 제목 아래에 상태를 둡니다.

```text
Status: Draft
```

상태 값은 다음 중 하나를 사용합니다.

- `Draft`: 논의 중인 문서
- `Accepted`: 현재 설계 기준
- `Deprecated`: 폐기되었지만 역사적 맥락 때문에 보존
- `Superseded`: 다른 문서나 ADR로 대체됨

필요하면 `Superseded by: ...`를 추가합니다.

## 작성 규칙

- 요구사항은 `MUST`, `SHOULD`, `MAY`를 사용해 강도를 구분합니다.
- 확정된 사실과 가정은 분리합니다.
- 제품 이름, 런타임 개념, marketplace 개념은 일관된 용어를 사용합니다.
- 큰 변경은 기존 문서를 삭제하기보다 새 ADR을 추가합니다.
- 구현 세부사항은 코드와 동기화되어야 하며, 오래된 문서는 상태를 갱신합니다.

## 용어 규칙

- `Aelix`: 전체 제품 및 플랫폼 이름
- `Aelix Runtime`: Python 기반 agent runtime
- `AgentRuntime`: 코드상의 런타임 kernel 객체
- `Extension`: 런타임이 실행할 수 있는 확장 단위
- `Extension Pack`: marketplace에 배포 가능한 extension 묶음
- `Marketplace`: extension pack의 검색, 배포, 검증, 설치 흐름
- `Policy Engine`: extension 실행 전 권한과 정책을 판단하는 built-in extension. runtime core가 아니라 lifecycle hook을 구독해 정책을 강제합니다(ADR-0004).
- `Guardrail`: 파괴적/위험한 동작에 대한 최소 방어선을 제공하는 built-in extension(ADR-0004).
- `Lifecycle Hook`: extension이 invocation 흐름(lifecycle, resource, session, agent, model, tool)을 intercept, gate, modify할 수 있도록 runtime core가 노출하는 확장 지점.
- `Marketplace Index Source`: extension pack metadata와 코드 위치를 노출하는 marketplace 후보(npm, git, 사내 custom 등). 각 source는 자기 형식과 trust 모델을 책임집니다(ADR-0005).

