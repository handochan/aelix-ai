# 0009. Python-First SDK; Multi-Language Clients via Future RPC ADR

Status: Accepted (Phase 1.2 SDK choice)
Superseded by: ADR-0020 (RPC mode Phase 4)

## Context

`02-initial-requirements.md`의 Open Question Q3는 "Aelix SDK는 Python-only로
시작할 것인가, 다언어 manifest contract를 먼저 열 것인가"를 미결정 항목으로
두었습니다.

분석 결과 Q3는 세 축으로 분리할 수 있습니다.

- A1 Extension 작성 SDK: extension 코드를 어느 언어로 짤 수 있나
- A2 외부 클라이언트: 다른 언어 앱이 Aelix runtime을 호출
- A3 Manifest contract 다언어

A1은 ADR-0007(in-process)에서 사실상 Python으로 제한됐습니다. A3는
ADR-0005(multi-source index)에서 source가 형식을 책임지므로 이미 다언어
친화입니다. 따라서 실제 미결정 항목은 A2입니다.

Pi에서 다언어 클라이언트는 RPC mode(stdin/stdout JSON 프로토콜)로 처리됩니다.
예: Python 앱이 Pi를 자식 프로세스로 띄워 통신.

ADR-0006은 SDK를 internal helper가 아니라 외부 공개 product layer로 격상
했으나, 외부 공개가 곧 다언어 1차 지원을 뜻하지는 않습니다. Python SDK 자체가
외부에 공개되면 1단계 외부 공개는 충족됩니다.

## Decision

Aelix는 1차로 **Python SDK만** 제공합니다.

- Extension 작성: Python module (ADR-0007 in-process 결정의 자연스러운 귀결).
- 외부 클라이언트: 1차에는 별도 RPC contract를 정의하지 않습니다.
- 외부 공개 product layer로서의 약속(ADR-0006)은 Python SDK가 외부에 공개되는
  것으로 1단계 충족합니다.

다언어 외부 클라이언트가 명확한 요구가 될 때, Pi의 RPC mode를 참고해 별도
ADR로 contract를 정의합니다. 그 시점에는 다음을 포함해 결정합니다.

- 통신 채널 (stdin/stdout JSON, HTTP, Unix socket 등)
- 메시지 schema
- 인증/authorization
- error/streaming 규약

## Consequences

- 초기 SDK 구현은 `src/aelix/sdk/` (가칭) 아래 Python helper로 시작합니다.
- 외부 다언어 클라이언트는 1차 surface에 포함되지 않습니다. ADR-0006의 외부
  공개 약속과 충돌하지 않습니다 (Python SDK 외부 공개로 충족).
- Pi parity + ADR-0002 small kernel 정신과 정합합니다.
- 다언어 RPC ADR이 나오기 전까지 외부 통신은 비공식 또는 사용자 책임으로
  둡니다.
- `02-initial-requirements.md`의 "Extension author가 사용할 SDK"는 Python
  대상으로 좁혀 해석합니다.

---

Phase 1.2 임시 결정이며 Phase 4에서 ADR-0020으로 대체될 예정.

