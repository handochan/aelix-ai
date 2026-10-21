# Product Vision

Status: Draft

## 정체성

Aelix는 단순한 코딩 에이전트가 아니라 범용 agent runtime이자 extension
platform입니다. 목표는 coding, office assistant, operations automation,
analysis platform agent, customer-site automation 같은 여러 agent 워크로드를
하나의 확장 가능한 런타임 위에서 실행하는 것입니다.

초기 구현의 가장 직접적인 기준점은 `pi agent`였고, 그 핵심 경험과 구조의 상당
부분이 이미 Python으로 포팅됐습니다. 이제 pi와의 완전한 parity는 목표가 아닙니다
(ADR-0235). pi는 따라가야 할 기준이 아니라 검증된 참조 구현이어서, 문제가 생기거나
새 표면을 다룰 때 먼저 확인하되 Aelix가 다르게 가는 것은 결함이 아닙니다. 너무
날것에 가까운 agent runtime에서 바로 시작하지 않도록 permission system과 최소
guardrail은 기본 내장 확장으로 포함합니다.

포지셔닝은 보안·self-host 우선입니다(#86, 2026-07-11 오너 확정). 약속은 네 가지를
따로 말하고, 하나로 뭉뚱그리지 않습니다. **실행 위치** — Aelix와 그 확장은 사용자의
머신이나 사용자가 운영하는 호스트에서 돌고, 사용자에 대한 telemetry는 없습니다. Aelix가
스스로 내보내는 요청(업데이트 확인, `rg`/`fd` 다운로드, 카탈로그 fetch)은 `--offline`이
끕니다(`git+https://` 설치 대상은 예외). **모델 전송 경로** — 프롬프트와 컨텍스트(읽은
파일, `AGENTS.md`)는 설정한 프로바이더로 갑니다. 원격 모델을 쓰면 그 내용은 네트워크
밖으로 나가며, 망 안에 머무르려면 자체 호스팅한 모델 엔드포인트가 필요합니다. 이미 보유한
GitHub Copilot 좌석으로도 동작합니다. **artifact 출처** — 빌드된 확장 artifact는 Ed25519
서명과 fail-closed인 `install --require-signature`로 출처를 검증할 수 있지만, 기본값은
아닙니다. **권한 enforcement** — policy와 guardrail은 lifecycle hook을 구독하는 교체
가능한 built-in extension이고(ADR-0004), 제품 표면(`AgentHarness`)의 모든 tool call은
hook bus를 지나는 관찰 가능한 이벤트입니다. in-process 확장은 sandbox가 아니며 Aelix
프로세스와 같은 권한으로 실행됩니다.

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

- pi agent에서 검증된 핵심 agent runtime 경험을 Python 기반으로 제공해야 합니다. pi는 참조
  구현이며 parity 자체가 기준은 아닙니다(ADR-0235).
- 작은 kernel 위에 다양한 extension를 얹을 수 있어야 합니다.
- marketplace를 통해 extension 생태계를 만들 수 있어야 합니다.
- 사내망, 폐쇄망, 고객사 배포를 고려해야 합니다.
- coding agent에 갇히지 않고 범용 agent runtime으로 확장되어야 합니다.
- 정책, 권한, 감사 가능성은 runtime의 핵심 개념이되, runtime core가 아닌 built-in extension으로 강제되어야 합니다(ADR-0004).
- permission system과 최소 guardrail은 optional add-on이 아니라 기본 번들에 포함된 built-in extension으로 안정적인 출발점에서 제공되어야 합니다.
