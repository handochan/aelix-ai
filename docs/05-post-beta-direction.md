# 베타 이후 제품 방향과 운영 제안

Status: Draft
Date: 2026-09-15

이 문서는 사용자가 제시한 고민을 현재 저장소·GitHub·Pi의 공식 자료와 대조한
의사결정 초안이다. 배포 일정, 새 공개 API, 지원 범위를 확정하지 않는다.
기존 Accepted ADR을 대체하지 않으며, 실행은 기존 이슈를 먼저 연결하고 이슈별로 진행한다.

같은 날 작성된 Claude Code의 `.omc/specs/direction-post-beta2-2026-09-15.md`와 비교하고,
핵심 차이를 코드·정적 측정·Pi 소스로 재확인해 반영했다. 비교 판정과 명령은
[교차 검토 기록](../.omc/specs/review-direction-post-beta2-codex-2026-09-15.md)에 남긴다.

2026-09-15 두 초안을 이 문서 하나로 합쳤다. **이 문서가 정본**이고, Claude 초안은 세션 기록으로만 남는다.
합치면서 Claude 초안에서 가져온 것은 Pi 변화 요약과 구조 이동(§3), Pi 배포 형태와 예제 후보(§6),
차별성 증거 후보와 추가 결정 항목(§11), 그리고 부록 A~D(분리 후보 실측, 최소 변경 후보, 원문 12항 대응,
측정 명령)이다. 가져오지 않은 것(날짜가 박힌 일정, 예제·패키지 최소 개수, 전체 RPC parity 선행, 48시간
응답)과 그 이유는 교차 검토 기록 §3에 있다.

## 1. 제안하는 방향

장기 비전은 **작은 Python 커널 위에서 업무별 에이전트 제품을 만들고, 설치하고,
중단 이후에도 실행을 추적·복구할 수 있는 확장 플랫폼**이다.

가까운 제품 목표는 **개발자와 데이터 실무자가 자기 환경에서 유용한 작업을 끝내고,
그 작업을 다시 열어 확인할 수 있는 Aelix**로 둔다. 코딩은 기본 사용 경험이며,
데이터 분석은 범용성을 검증할 첫 번째 다른 업무 영역으로 삼는다.

향후 투자는 세 가지 질문으로 판단한다.

1. 사용자가 작업과 결과를 믿고 다시 사용할 수 있는가?
2. 도메인 기능을 코어 수정 없이 확장으로 전달할 수 있는가?
3. 처음 온 사용자와 기여자가 스스로 성공할 수 있는가?

**안정화, 문서, 확장 사례를 함께 진행하되, 런타임·서버·웹·데스크톱을 한꺼번에
완성하려 하지 않는다.** 구조 개편은 실제 사례에서 드러난 계약의 빈틈을 해결하는 단위로 한다.

## 2. 조사 기준과 확인한 사실

- Aelix 로컬 `main`과 GitHub `main`: `026bd15db229d86d61b74fd3b8c1d8eb0f750715`.
- 최신 공개 릴리스: [v0.1.0-beta.2](https://github.com/handochan/aelix-ai/releases/tag/v0.1.0-beta.2),
  2026-09-09 공개. 릴리스 태그와 조사한 main은 구분한다.
- 해당 main의 [CI 34705932974](https://github.com/handochan/aelix-ai/actions/runs/34705932974)는 성공.
  이번 조사에서 전체 pytest나 실제 모델/TUI/서버 실행은 하지 않았다.
- Pi 최신 공개 릴리스는 [v0.85.1](https://github.com/earendil-works/pi/releases/tag/v0.85.1),
  조사 소스는 main `8a7b0c03dfb702663acafb6dc29f8acaa4ffe391`.
  main의 설계·구현을 모두 해당 릴리스에 포함된 기능으로 간주하지 않는다.

| 영역 | 이번에 확인한 상태 | 방향에 주는 의미 |
| --- | --- | --- |
| 제품 비전 | `01-product-vision.md`의 Pi 재현 목표와 ADR-0235가 불일치. #266이 이미 추적 | 새 전략 수립과 기존 문서 정합성을 함께 처리 |
| 패키지 독립성 | agent-core → coding-agent import 13곳 중 런타임 10곳, TYPE_CHECKING 3곳. coding-agent import를 차단하면 Harness 생성 실패 | 독립 런타임이라는 약속 전에 의존성 역전과 독립 사용 검증 |
| 내구성 | JSONL 저장 및 복구 구현·ADR-0208이 존재. 자식 실행에는 `--no-session` 사용 | 기존 저장 기반을 활용하되 자식 실행의 수명·기록 계약을 설계 |
| 확장 계약 | 설치·카탈로그·manifest·Protocol 기반 교체 지점이 이미 존재. #253은 skills/agents 묶음 추가 요구 | 새로운 확장 체계를 따로 만들기보다 기존 Extension Pack을 발전 |
| 공식 카탈로그 | 기본 URL HTTP 200, `extensions: []` | 검색 엔진보다 실제 설치 가능한 공식 패키지 공급이 우선 |
| 서버 | `/healthz`, `/schemas/{name}`, `/rpc`; 단일 RPC 연결; 기본 localhost; 인증 없음 | 세션 운영 서버로 키우기 위한 수명·인증·동시성 설계 필요 |
| 서버 확장 | 현재 WebSocket harness factory는 확장을 로드하지 않는다고 명시 | TUI 기능과 확장이 웹에서 자동으로 동작한다고 볼 수 없음 |
| RPC 제어 | 세션 목록·명령 실행 전용 handler 없음. `extension_ui_response`는 인식 후 버림 | 승인 왕복과 세션 탐색을 최소 웹 흐름의 선행 계약으로 지정 |
| Analytics | 로컬 분석 프로토타입과 단일 파일 Aelix 어댑터 존재. README는 공식 패키지·실제 모델 smoke 미완료로 명시 | 이미 있는 검증 가능한 분석 흐름을 좁은 대표 사례로 활용 |
| 기여 운영 | CONTRIBUTING, SECURITY, PR 템플릿, SHA 고정 Actions, 필수 CI 존재 | 준비를 처음부터 다시 만들 필요는 없음 |
| GitHub 보호 | private vulnerability reporting 꺼짐; 필수 리뷰 없음; 관리자 예외 허용; 필수 검사 6개 | 기여자 확대 전 비공개 신고·권한·리뷰 운영 보완 |

근거: [ADR-0235](decisions/0235-the-pin-never-moved-and-the-rule-outlived-the-port.md),
[ADR-0208](decisions/0208-session-durability-is-kernel-maintenance.md),
[자식 RPC 실행](../packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py),
[서버](../packages/aelix-server/src/aelix_server/rpc_ws.py),
[공식 카탈로그](https://handochan.github.io/aelix-marketplace/catalog.json),
[기여 가이드](../CONTRIBUTING.md).
Analytics 확인 경로는 `/Users/handochan/dev/aelix-extensions/aelix-analytics/README.md`이다.
GitHub 설정은 API로 읽었으며 변경하지 않았다.

### 복잡도 측정

실행 명령: `uvx --from radon==6.0.1 radon cc -j packages src`.
종료 코드 0. JSON의 함수 및 클래스 메서드를 집계했으며 클래스 종합 점수는 중복 집계하지 않았다.
범위는 `packages/`와 `src/`이며 예제 등 그 안의 코드도 포함한다. 루트 `tests/`는 제외한다.

- 함수·메서드 4,331개; 순환 복잡도 20 초과 73개; 50 초과 5개.
- `_async_main`: 109; `parse_args`: 92; `stream_openai_completions`: 82;
  `process_responses_stream`: 57; `build_params`: 53.
- 이는 분기 경로에 관한 정적 지표다. 인지 복잡도, 결함률, 실행 성능, 전체 아키텍처 품질을
  측정한 수치가 아니다. 전체 코드가 같은 수준으로 복잡하다는 결론도 아니다.

Claude 초기 문서의 3,592블록도 재현됐다. 이는 클래스 요약과 최상위 함수의 집계이며,
위 4,331은 클래스 요약 대신 메서드를 센 값이다. 두 집계 모두 중첩 closure를 펼친 총계가 아니다.
갱신된 Claude 문서의 중첩 closure 포함 집계도 재현했다: **함수·메서드·closure 4,774개,
CC 20 초과 74개, 50 초과 5개**. 앞으로 비교할 때는 이 재귀 집계를 같은 방식으로 사용한다.

추가 도구는 Ruff 0.15.13이다.
명령: `uv run --no-sync ruff check --select C901 --config 'lint.mccabe.max-complexity=40' --output-format json packages src`.
종료 코드 1, C901 진단 **6개**:

| 함수 | Ruff C901 복잡도 |
| --- | ---: |
| `run_tui` | 248 |
| `_async_main` | 82 |
| `parse_args` | 80 |
| `stream_openai_completions` | 54 |
| `run_print_mode` | 42 |
| `process_responses_stream` | 42 |

`run_tui`는 Radon에서 44이며 별도 closure 63개를 갖는다. 이처럼 도구가 함수·중첩 함수와
분기를 세는 방식이 다르므로 두 점수를 같은 척도로 섞지 않는다.
**TUI의 거대한 closure 배선도 우선 감사 대상**이다. Claude 갱신본의 측정 표도 6개로 정정됐지만
일부 계획 절에 남은 "40 초과 두 함수"는 전체 검사 범위의 결과로 채택하지 않는다.
`# noqa`로 예외를 숨기면 그 함수의 악화를 잡지 못하므로,
진단을 계속 수집하고 함수별 허용 기준선과 비교하는 방식을 설계한다.

### 열린 이슈와 실제 완료 상태

Project에서 #199·#253·#264·#284는 Backlog였다. #198은 In progress지만 CHANGELOG에는
해당 resume/thinking 수정 기록이 있다. 이 차이는 **완료 증거를 대조할 이유**이며,
이번 조사만으로 이슈를 닫을 근거는 아니다(이슈별 대조 결과는 §11 실행 기록).

따라서 열린 이슈 수를 그대로 미구현 기능 수로 세지 않는다. 각 이슈를
재현됨 / 수정됐으나 검증·종결 대기 / 추가 설계 필요 / 중복·대체됨으로 먼저 판정한다.
Claude 갱신본이 추가한 beta.2 수정 이슈 목록은 완료 대조 후보로 사용한다.
예를 들어 #142는 버전 검사뿐 아니라 릴리스 환경·publisher·rehearsal 등을 포함하므로,
일부 수정이나 CHANGELOG 언급만으로 이슈 전체 완료를 선언하지 않는다.

## 3. Pi에서 참고할 변화와 Aelix의 선택

Pi의 현재 [Harness 명세](https://github.com/earendil-works/pi/blob/8a7b0c03dfb702663acafb6dc29f8acaa4ffe391/packages/agent/docs/harness.md)는
대화 저장뿐 아니라 operation의 수락·실행·취소·종료 상태를 지속하는 런타임을 설명한다.
대화 기록, 가변 실행 상태, 사용량 원장을 구분하고 저장소의 원자적 변경을 기반으로 삼는다.
[harness v2 r2 PR](https://github.com/earendil-works/pi/pull/7669)은 실제 병합됐다.

[Tool durability 설계](https://github.com/earendil-works/pi/blob/8a7b0c03dfb702663acafb6dc29f8acaa4ffe391/packages/agent/docs/tool-durability.md)는
병렬 도구의 완료 순서와 대화에 결과가 들어가는 순서를 분리한다. 앞 도구가 멈춰 있어도
뒤에서 끝난 도구 결과를 먼저 저장해야 재시작 때 이미 완료한 작업을 반복하지 않는다.

다만 Pi 명세 자체도 `watchSession`, 검색, 일부 telemetry 및 JSONL snapshot compaction 등의
미완료 항목을 명시한다. [서버](https://github.com/earendil-works/pi/blob/8a7b0c03dfb702663acafb6dc29f8acaa4ffe391/packages/server/README.md)도
experimental이다. 제안·병합 코드·공개 릴리스·실제 지원을 구분해야 한다.

Aelix의 선택은 다음과 같다.

- 참조할 것: 실행 상태, 저장 시점, 복구 규칙, 단일 작성자 소유권, 연결과 실행 수명 분리,
  확장 계약 검증 방식.
- 별도로 판단할 것: 저장소 교체, Python API, 패키지 경계, 사용자 기본 경험,
  정책 확장, 데이터 업무 흐름.
- Pi 동향 검토는 정기적으로 adopt / adapt / defer / reject와 근거를 남긴다.
  ADR-0235에 따라 전체 parity를 다시 로드맵으로 삼지 않는다.

추가로 [Chord 계획](https://github.com/earendil-works/pi/blob/8a7b0c03dfb702663acafb6dc29f8acaa4ffe391/packages/chord/PLANNING.md)의
플러그인 lifecycle·서비스 호출·상태 복제·독립 패키징 경계를 참고한다. Chord는 범용 플러그인 기반이고
Aelix agent-core는 에이전트 런타임이므로, Chord의 에이전트 어휘 배제 규칙을 그대로 옮기지는 않는다.
현재 Chord 공개 계약도 안정화 전이다.

Pi main에는 새 `session/jsonl`의 repo/storage와 legacy-v3 처리 코드가 존재한다.
**legacy JSONL 구현 삭제를 JSONL 형식 전체의 포기로 해석하지 않는다.** Aelix 저장소 교체 여부는
원자성·복구·동시 쓰기·마이그레이션 요구를 측정해 결정한다.

### Pi 변화 요약과 다른 구조 이동

Aelix의 인용 앵커는 v0.74.1(ADR-0034)이고 #54는 v0.80.2까지를 추적했다. 그 뒤의 변화 중 설계 판단에
닿는 것만 적는다(coding-agent CHANGELOG 기준, 2026-09-15 확인).

| 버전 | 변화 | Aelix 대응 |
| --- | --- | --- |
| 0.79.0 | 로컬 입력에 project trust, 확장이 trust 결정 | Aelix는 이미 project trust 게이트가 있다. #282(AGENTS.md walk-up 정책)는 "신뢰 안 된 디렉터리는 읽지 않는다"로 답한다 |
| 0.80.8 | `ModelRuntime`이 `ModelRegistry`/`AuthStorage` 대체, provider가 `/login` 소유 | adopt/adapt/defer 판정 대상 |
| 0.84.0 | lane 기반 `Session`/`SessionRepo`, durable operation record, `AgentHarness` 기본 export, legacy repo 삭제, `AGENTS.override.md`(자기 디렉터리에서 대체) | 내구성 계약(§5)의 참조. `AGENTS.override.md`는 가져올 만하다 |
| 0.85.0 | `SessionManager.inMemory()`, 외부 저장 세션 복원 | 서버 세션 소유(§8)의 참조 |

#54(upstream 동기화 추적)의 목표를 "커밋을 따라잡는다"에서 **"위 네 결정과 Chord 계획을 읽고 각각
adopt / adapt / defer / reject를 ADR 한 줄로 남긴다"**로 바꾸는 것을 제안한다. ADR-0235가 말한
"증거로 읽는다"의 실행 형태다.

레포 구조에서도 참고할 이동이 있었다(2026-09-15 관찰, 전략 근거가 아니라 배치 판단의 참고).

- `mom`(Slack 봇)과 `web-ui`가 모노레포에서 빠져 별도 레포 `earendil-works/pi-chat`으로 갔다. **모노레포는
  런타임으로 좁아지고 제품 표면은 밖으로 나갔다.** Analytics를 독립 패키지 + 어댑터로 두는 §6의 배치와
  같은 방향이고, Aelix 웹 클라이언트도 본체 레포 밖이 자연스럽다.
- `telemetry`가 vendor-neutral 계약 + 적합성 테스트로 추출됐다(RFC 0019). 자식 사용량·Analytics 기록의
  이벤트 스키마를 정할 때 참고하되 지금 채택할 것은 아니다. `evals`는 비공개 워크스페이스 패키지로 추가됐다.
- 서브에이전트는 여전히 예시 확장이다(에이전트당 프로세스, single/parallel/chain, 에이전트 정의는
  `~/.pi/agent/agents/*.md`·`.pi/agents/*.md`). **별도 자식 세션 저장소는 없고 부모 세션이 유일한
  기록**이다. Aelix가 자식 기록·복구를 1급으로 만들면 비교 가능한 축이 생기지만, 그것이 우월함의
  증명은 아니다 — 설치·실행·복구 경험으로 검증한다.
- 신규 기여자의 이슈·PR을 기본 자동 종료한다. Aelix가 기여를 받는 쪽에 서는 것은 가능한 차별점이지만
  §9의 준비 없이 열면 #217/#223이 반복된다.

## 4. 작업 체계: 일곱 축

| 축 | 가까운 산출물 | 완료를 판단하는 증거 |
| --- | --- | --- |
| 베타 안정화 | 실제 사용자 여정 회귀 시나리오와 릴리스 게이트 | 깨끗한 설치·업그레이드·실제 모델·중단·resume 결과 |
| 내구성·구조 | 실행/기록 계약, 복잡도 기준선, 집중 리팩토링 | 장애 주입 복구, 데이터 손실·중복 실행 검증 |
| 확장 개발 경험 | Pack 계약 보완, testkit, 간단한 설치·실행 | 코어 수정 없이 설치·실행·업데이트·제거 |
| 사례·생태계 | 유지보수 가능한 공식 패키지와 대표 데모 | 초보 사용자와 외부 작성자가 재현 |
| 문서·홈페이지 | 첫 실행부터 확장 작성까지 이어지는 문서 | 문서만 보고 성공, CLI·웹 문서 드리프트 검사 |
| 기여·운영 | 이슈 소유권, PR 기준, 보안 신고·릴리스 권한 | 다른 사람이 한 이슈를 재현→PR→검증까지 완료 |
| 서버·UI | 세션 운영 API와 웹의 최소 완결 흐름 | 재접속·승인·확장 렌더링·세션 전환 E2E |

실행 관점에서는 위 일곱 축을 네 트랙으로 묶는다.

1. **기반:** 안정화·복잡도 관리·기여 운영. 다른 트랙과 함께 지속한다.
2. **공통 계약:** 패키지 독립성·내구성·Pack·RPC/UI 계약.
3. **사용 사례:** 예제·공식 확장·Analytics·문서·카탈로그로 공통 계약을 검증한다.
4. **사용 환경:** 최소 제어 계약이 검증된 뒤 서버·웹·데스크톱으로 넓힌다.

공통 계약 초안과 작은 사용 사례는 왕복하며 발전시킨다. 다수 패키지의 지원 약속은 필요한 계약을
검증한 뒤 넓히지만, 기존 API로 가능한 예제·초보자 문서까지 모든 Pack 설계 완료를 기다리지는 않는다.
서버도 전체 TUI/RPC parity가 아니라 첫 사용자 흐름에 필요한 계약을 진입 조건으로 삼는다.

1인 유지보수 기준으로 **동시에 활성화하는 큰 작업 흐름은 우선 두 개 이하**를 제안한다.
기반 하나와 계약/사례 하나로 두되 같은 파일을 동시에 수정하지 않고 이슈별 변경을 분리한다.
실제 동시 진행 상한은 리뷰 여력으로 재조정한다. 릴리스마다 대표 사용자 성과 하나를 정한다.

안정화에 개발 여력 60%, 확장 사례·필요한 구조 개선에 25%, 문서·기여 운영에 15%를
초기 배분안으로 제안한다. 담당 인원과 실제 결함량을 측정한 뒤 매주 조정한다.
문서는 각 작업 완료 조건에도 포함한다. 이 비율은 일정·처리량 약속이 아니다.

## 5. 내구성과 리팩토링

### 내구성의 단계

1. **기록 보존:** 부모·자식 session/run 연결, tool 입력·결과, 모델·확장 버전,
   사용량·비용, 종료 이유를 다시 조회할 수 있다.
2. **중단 판정:** 정상 완료, 실패, 취소, 실행 중 연결 단절, 결과 불명 상태를 구분한다.
3. **복구:** 확인된 완료 결과는 재사용하고, 재실행 안전성이 명시된 작업만 재실행한다.
   외부 변경 성공 후 기록 전에 죽은 작업은 자동 성공이나 자동 재시도로 처리하지 않는다.
4. **운영 지속:** 실행은 UI 연결보다 오래 살 수 있으며 예약·재접속으로 같은 상태를 확인한다.

일반 shell·외부 API의 정확히 한 번 실행을 일괄 보장할 수는 없다. 안정된 invocation ID,
대상 API의 idempotency key, 실행 결과 조회 또는 사람의 판단을 조합해야 한다.
사용량도 부모 합계와 자식 기록을 중복 계산하지 않고, 실패·재시도 비용과 미확정 사용량을 구분한다.

기존 `SubagentUsage`와 `aggregate.roll_up_usage`는 재사용할 자산이다. 입력·출력·비용은 누계지만
`SubagentUsage.tokens`는 문맥 사용 수준이므로 배치 합산 시 최대값을 사용한다.
현재 세션 통계는 assistant message 사용량을 집계하며 자식 사용량을 별도로 소비하지 않는다.
부모 도구 결과에는 사용량 문자열과 `details` 전달 경로가 이미 있으므로 "비용을 전혀 재지 않는다"고
표현하지 않는다. 필요한 것은 안정된 실행 ID에 묶인 지속 기록과 정확한 조회·합산이다.

`SessionInfoEntry.parent_id`는 같은 세션의 이전 entry를 가리키며 fork 전용 필드가 아니다.
세션 간 계보에는 별도의 `parent_session_path`가 쓰인다. spawn 계보는 이 둘과 구분해서 설계한다.
`--no-session` 제거만으로 복구·중복 방지·보관 정책까지 완성되지는 않는다.
#262의 3/3 실패는 공개 이슈의 사용자 보고로 확인했으며 이번 조사에서 라이브 재현하지 않았다.
이 회귀의 재현·수정·실제 모델 검증을 자식 기록 기능보다 먼저 진행한다.

### 유지할 경계

- **kernel:** 일반 실행·세션 저장 정확성, lifecycle, hook, cancellation의 공통 계약.
- **built-in / 확장:** 권한·가드레일 정책, 팀 구성, 역할 선택, spawn·스케줄링 전략.
- **server:** 실행 소유권, 세션 접근 API, 인증, 연결 수명, 작업 호출과 관찰.
- **TUI·웹·데스크톱:** 공통 동작 계약을 사용하는 표시·입력 어댑터.

단방향 의존성과 core의 extension 비의존은 지켜야 할 설계 원칙이다.
**현재 Harness 구현은 이 원칙을 위반한다.** `AgentHarness.__init__`가 coding-agent의
`_ExtensionRuntime`과 action 타입을 import하며, 외부에서 runtime을 주입해도 import 자체는 실행된다.
coding-agent import를 차단한 probe에서 core 모듈 import는 성공했지만 Harness 생성은
`ModuleNotFoundError`로 실패했다. 이는 깨끗한 환경의 wheel 설치 실험과는 구분한다.

우선 역방향 의존성이 늘지 않도록 정적 검사를 설계하고, 공통 계약을 하위 계층에 두거나
Protocol/콜백으로 의존성을 주입한다. 기존 `bind_core` 배선은 출발점이며 완성된 해결책은 아니다.
완료 조건에는 **coding-agent 없이 core wheel과 선언된 의존성만 설치해 Harness를 생성·사용하는 검증**을 둔다.
일반 실행 식별자를 추가하는 설계도 ADR-0197의 subagent 경계·테스트와 충돌 여부를 명시적으로 검토한다.
도메인 팀 정책을 kernel에 넣어 해결하지 않는다.

### 리팩토링 방법

이미 `SessionStorage`와 `SubagentRuntime` 같은 Protocol이 있다. 디자인 패턴의 유무보다
**다른 구현으로 교체했을 때 호출부 수정이 필요한지, 실제 lifecycle까지 연결되는지**를 감사한다.

| 문제 | 적절한 분리 방식 | 확인할 동작 |
| --- | --- | --- |
| CLI 분기 집중 | 인자 해석, 설정 해석, bootstrap, 모드 실행 분리 | 전역 옵션 위치·오프라인·출력 모드 일관성 |
| TUI closure 집중 | `CommandContext`에 전달하는 modal/session callback의 의존성 명시 | 최신 harness 참조·모달 취소·키보드·레이아웃 보존 |
| provider 호환 분기 | 검증된 정책 데이터 + 필요한 전략/어댑터 | 모델별 요청·thinking·stream·취소 의미 보존 |
| 실행 lifecycle | 명시적 상태 전이와 부작용 경계 | retry·abort·resume·완료 경합 |
| 저장소 | 기존 Protocol과 공통 적합성 테스트 | JSONL/Memory에서 같은 계약 |
| 확장·UI | 의미 있는 요청·결과와 frontend 어댑터 | 권한·오류·표시 fallback 일관성 |

우선순위는 복잡도 × 변경 빈도 × 결함 이력 × 사용자 영향으로 판단한다.
이번에는 복잡도만 측정했으므로 나머지 축은 #284 감사에서 보완한다.
먼저 현재 동작을 고정하는 회귀 검증을 확보하고 한 책임씩 옮긴다.
새 변경의 복잡도 악화를 막는 기준부터 도입하며, 예외에는 근거·후속 이슈를 둔다.
짧은 함수로 나눠 수치만 낮추거나, 모든 조건문을 클래스로 바꾸는 것은 완료 기준이 아니다.

Claude 문서가 제시한 구체적 분리 후보인 TUI modal callback, CLI early-exit 라우팅,
Harness action/hook bridge, 설치기의 서명·소스·index 설정을 #284의 조사 입력으로 사용한다.
Optional callback이라는 사실만으로 안전한 이동이 증명되지는 않는다. closure 캡처,
session 교체 뒤 최신 객체 참조, 취소·cleanup·등록 순서부터 확인하고 이슈별로 분할한다.

## 6. Extension Pack과 대표 사례

### 하나의 Pack으로 다른 에이전트 제품을 제공

기존 Extension Pack을 발전시켜 다음을 선언·배포하도록 설계한다.

- 제품/에이전트 정체성과 system prompt 구성
- 도구·스킬·prompt template·agent profile·MCP 연결
- 권한 요청, 자원 접근, 실행 환경·의존성
- 선택적인 UI 기여와 headless 동작
- 실행 진입점, 설정 schema, 호환 runtime/API 버전

정체성 모델은 **이미 있는 `AgentProfile`을 우선 재사용**한다. 현재 profile에는 모델·도구·스킬·확장,
system prompt append/replace·권한 요청·timeout 등이 있다. `aelix --agent <name>`도 존재한다.
가까운 빈틈은 이를 패키지로 배포하고 발견하는 경로와 간단한 실행 진입점이다.
system prompt 변경도 `BeforeAgentStartResult.system_prompt` hook으로 가능하므로
새 API가 필요한 경우와 예제·배포가 부족한 경우를 구분한다.

`contributes.agents`/`contributes.skills`를 첫 구현 범위로 삼고 theme/settings 프리셋,
`default_agent`는 후속 범위로 검토한다. 로더에 디렉터리를 추가할 때도 패키지 출처·trust,
동명 profile 우선순위, 경로 범위, 재로드·제거, 자식에게 전달되는 확장 집합을 함께 검증해야 한다.

핵심 설계 쟁점은 prompt·설정 우선순위, profile 이름 충돌, 프로젝트 trust,
부모로부터 받는 권한 상한, 패키지 제거 후 기존 세션 복구, 버전 잠금·마이그레이션이다.
의존성 격리는 서로 다른 Python 패키지의 충돌을 줄이는 수단이며 보안 sandbox와 구분한다.

제안 UX 예시이며 현재 동작하는 명령이라는 뜻은 아니다.

```text
aelix install <pack>
aelix run <pack-or-profile>
```

기존 install resolver·검증·오프라인 소스를 재사용하고 이전 CLI에는 호환 경로를 둔다.
`run`은 설치된 버전과 해석된 정체성을 명확히 보여준다.
모델의 `spawn(profile=...)`도 같은 profile 해석·권한·기록 계약을 사용해야 한다.
관련 기존 이슈는 #253, #287이며 root CLI 라우팅은 #289와 함께 검토한다.

참조 형태(Pi `packages.md`, 2026-09-15 확인): `pi install npm:<pkg>@<ver>` · `git:<host>/<repo>@<tag>` ·
https/ssh URL · 로컬 경로, `pi remove` · `pi list` · `pi update --extensions`, 설치 없이 1회 실행하는
`pi -e <spec>`. 패키지는 extensions·skills·prompt template·themes를 묶고 `package.json`의 `pi` 키 또는
관례 디렉터리로 선언한다. 문서상 system prompt·권한 프리셋·실행 정체성은 패키지 항목이 아니지만, hook과
확장으로 구성할 수 없다는 뜻은 아니다. Aelix의 `-e`는 이미 경로와 dotted module을 받으므로 카탈로그
spec까지 받게 하는 것은 작은 확장이다. 시스템 프롬프트 파일 관례는 `.pi/SYSTEM.md`·`~/.pi/agent/SYSTEM.md`
대체와 `APPEND_SYSTEM.md` 추가이고, #287이 같은 모양이다.

### 예제와 공식 제품을 구분

| 종류 | 목적 | 가까운 후보 |
| --- | --- | --- |
| 작은 예제 | 하나의 확장 지점을 읽고 재현 | 도구, hook, 상태 저장, 승인, UI, profile |
| 유지보수하는 공식 패키지 | 반복 사용 가능한 업무 해결 | 제한된 web fetch·조사, 재현 가능한 코드 검토 |
| 대표 도메인 제품 | 범용성과 차별성 검증 | Analytics의 작은 분석 흐름 |

초기에는 공식 패키지 2~3개와 단일 주제 예제 6~8개를 목표 후보로 둔다.
개수보다 owner·설치·호환성·오류·문서·검증을 끝까지 유지할 수 있는지가 우선이다.

모든 확장에 사용할 testkit은 설치/업그레이드/제거, 권한 거부, 로드 실패 격리,
중단/cleanup, session resume/fork, 의존성 충돌, TUI/headless 동작을 검증한다.
UI나 서버를 요구하는 확장은 그 지원 범위를 별도 표시한다.

예제 목록은 **contributes 패밀리 × 실제 사용 흐름**의 표로 관리한다. import 성공뿐 아니라 등록한
도구·hook·UI가 실제 소비되는지를 검사한다. `/reload` 예제는 중복 등록, 살아남은 process/task,
실패한 재로드 뒤 기존 기능을 검증하는 사례로 활용한다. 설치 가능한 공식 제품 개수와
작은 계약 예제 개수는 별도로 관리한다.

현재 실제 예제는 `echo`·`selfhosted`·`starter` 셋이다(`telnaut` 디렉터리는 비어 있다). Pi는 78개
(파일 69 + 디렉터리 9, README에는 69만 문서화)이며 분포는 Commands & UI 28 · Custom tools 13 · Messages/
session/resources 8 · Lifecycle & safety 7 · System prompt & compaction 7 · Providers/deps/process 4 · Git 3이다.
개수를 쫓지 않는다. 다만 Pi의 safety 7개(permission-gate · protected-paths · dirty-repo-guard 등)는 Aelix에서는
built-in permission의 **프리셋 예제**로 바뀌는 자리라, 차별점이 보이는 예제가 무엇인지 알려 준다.

후보(패밀리 커버리지와 실제 사용을 반씩; 개수·순서는 제안):

| 후보 | 패밀리 | 왜 |
| --- | --- | --- |
| permission 프리셋 pack (read-only / review / 자유) | profile · hooks | built-in permission을 보이게 하는 가장 싼 예제. system prompt hook 예제를 겸한다 |
| cost-ledger | descriptors · 세션 API | 자식 사용량 기록(§5)의 첫 소비자 |
| web-fetch(#18) · memory(#17) | tools | 백로그에 있고 누구나 쓴다. 공식 패키지 후보 |
| git commit guard | subprocess hooks | hook 패밀리의 실전 예제 |
| 한국어 입력·붙여넣기 보정 위젯 | tui_widgets(ui_tui_trusted) | 한국어 사용자가 실제로 아픈 곳 |
| selfhosted(있음) + Copilot 좌석 프로필 | providers · profile | #86 기둥의 데모. ToS 확인(§11) 전에는 문서에만 |
| session redact | hooks · 저장 경계 | #138의 탐색용. 로그·저장·표시 중 어느 경계인지 정해야 하며 hook 예제 하나로 #138을 닫지 않는다 |
| Analytics pack | profile · tools · skills | 대표 도메인 제품(아래) |

### Analytics의 권장 검증 흐름

**데이터 등록 → 질문 → 분석/차트 → 원본·실행 코드·검증 결과 확인 → 선택 영역 후속 분석 →
세션을 다시 열어 작업 추적**을 하나의 대표 경험으로 잡는다.

현재 README가 설명하는 register/transform/view/select/verify 흐름과 어댑터를 출발점으로 한다.
통계적 타당성이나 인과관계 보장을 단순 재실행 일치와 혼동하지 않는다.
전체 Studio를 기다리지 말고 모델과 연결한 작은 E2E를 먼저 검증한다.

분석 엔진·데이터 계약은 독립 패키지로 유지하고, Aelix 도구·스킬·실행 진입점과
선택적 웹 UI를 어댑터로 연결하는 방식을 추천한다. 플랫폼 확장 API가 아직 부족하면
독립 웹 화면을 사용하면서 실제 필요한 UI 계약만 추출한다.

Analytics는 `requires-python >=3.12`이며 Aelix 본체는 3.11+이다.
DuckDB·Polars·Plotly 등의 의존성과 인터프리터 요구를 본체 환경에 강제하지 않도록,
가벼운 Pack/어댑터가 별도 실행 환경의 분석 CLI·서비스를 호출하는 현재 경계를 유지한다.

## 7. 카탈로그·홈페이지·문서

공식 카탈로그의 초기 완료 조건은 **사용자가 확장을 발견하고, 믿을 근거와 실행 예를 보고,
설치·첫 작업에 성공하는 것**이다.

카탈로그 상세에는 카테고리, 용도, 설치 명령, 지원 Aelix/OS/UI 버전, 권한·네트워크 요구,
maintainer, 소스·라이선스, 업데이트 시점, 데모·문서, artifact hash·서명 상태를 표시한다.
공식 / community / experimental의 책임 범위를 구분하고, 폐기·문제 버전 안내도 준비한다.

별점·스타·다운로드는 이후 탐색 지표다. GitHub stars는 인기 지표이고,
registry 다운로드는 고유 사용자 수가 아니다. 정의·출처·집계 시각이 없으면 숫자를 노출하지 않는다.
서명 또한 출처 증명이며 안전성 심사를 대신하지 않는다.
이 지표를 도입할 때는 빌드/예약 작업에서 수집·캐시해 방문자마다 외부 API를 호출하지 않는 방식을
우선한다. 단 설치에 쓰는 카탈로그의 신뢰 데이터와 인기 지표는 구분하고, 수집 실패·오래된 값을 표시한다.
두 저장소를 합치는 결정과 사이트의 탐색·디자인을 통합하는 결정도 구분한다.

홈페이지 정보 구조:

1. **Home:** 누구에게 어떤 일이 가능한지, 실제 짧은 데모, 현재 지원 범위.
2. **Start:** 설치 → 인증 → 첫 작업 → 저장/resume → 첫 확장.
3. **Docs:** 사용자 가이드, 확장 작성, API, 운영, 문제 해결.
4. **Extensions:** 카탈로그, 상세, 설치, 호환성, 작성·등록 안내.
5. **Releases:** 안정/베타 구분, 변경점, 알려진 문제, 업그레이드·마이그레이션.
6. **Contribute:** 방향, Ready 이슈, 개발·검증·리뷰 흐름.

#264~#277에 이미 문서 체계와 사이트 작업이 있다. 현재 계획의 정적 문서 생성과
기존 `site/`를 먼저 활용하며, 이 초안이 별도 사이트 프레임워크를 도입하는 결정은 아니다.
소스 가이드 → 휠 번들 → `aelix docs` → 홈페이지의 내용과 버전 표시를 맞춘다.
드리프트 검사는 항목 존재를 검증하고, 초보자 실제 수행은 문서의 이해 가능성을 검증한다.

## 8. 서버·웹·데스크톱

초기 서버 목표는 **하나의 신뢰하는 환경에서 여러 세션을 관리하고 UI가 다시 연결해 이어 쓰는 것**이다.

첫 범위:

- 세션 목록·조회·생성·연결, prompt/stream/abort, 재접속 시 snapshot과 이후 event 전달.
- 실행 소유권과 중복 prompt 방지; 두 UI의 동시 쓰기 정책.
- 인증, 허용 host/origin, workspace 경계, 비밀정보를 제외한 진단.
- 공통 bootstrap을 통한 확장·스킬·설정 로드, 승인 요청과 응답.
- 확장 목록·상태; 서버 환경의 설치/업데이트는 승인과 세션 버전 영향이 보이는 관리 작업.

구체적인 선행 공백은 세션 **목록**, 승인 요청/응답 연결, 등록된 명령의 실행이다.
현재 RPC에는 `new_session`·`switch_session`·`fork`·`clone`이 있으므로 재개 기능이 전혀 없다고
표현하지 않는다. 그러나 `extension_ui_response`를 버리는 현재 경로는 승인 왕복을 완성하지 못한다.
`get_commands`의 나열 역시 실행 계약을 대신하지 않는다. 필요한 명령 실행에는 허용 범위,
권한 검사, 인자 schema, 취소·오류 응답을 함께 설계한다.

세션은 서버가 소유하고 TUI/웹은 연결해서 사용하는 방향을 검토한다. 이는 단일 작성자 소유권과
복수 관찰 화면을 분리하는 방법이다. 독립 CLI가 같은 저장소를 직접 쓰는 경로도 통제해야 하므로
서버를 두는 것만으로 기존 동시 쓰기 결함이 자동 해결되지는 않는다.

브라우저가 끊긴 것과 사용자가 실행을 취소한 것을 구분한다.
서버 재시작 복구 범위는 내구성 계약과 함께 명시한다.
인증 없는 현재 skeleton을 그대로 원격 운영 서버로 공개하지 않는다.

cron은 그 다음에 실행 API를 호출하는 scheduler/service 또는 확장으로 붙인다.
timezone, missed run, 중복 실행, 겹치는 실행, retry, 비용 상한, 비대화형 권한을 먼저 정의한다.
사람의 승인이 필요한 예약 실행은 대기/거부 규칙을 갖고 무인 상태에서 권한을 넓히지 않는다.
멀티테넌트 SaaS·분산 실행·고가용성은 이 초기 범위 이후다.

웹은 세션 목록 → 생성 → 대화 → 도구 결과 → 승인 → 재연결의 완결 흐름부터 시작한다.
TUI의 모든 기능은 공통 동작 계약에 매핑하되, 터미널 키·Python 위젯을 웹에서 그대로
실행할 수 있다는 약속은 하지 않는다. 공통 form/choice/panel/result descriptor와
frontend별 renderer, 미지원 기능의 설명·fallback을 둔다.
임의 웹 확장 코드는 권한이 제한된 별도 실행/표시 경계를 설계한다.

데스크톱은 웹과 같은 클라이언트·확장 계약을 재사용하는 순서가 적절하다.
로컬 OS 통합·오프라인·설치 경험에서 실제 이득이 확인될 때 독립 배포한다.

## 9. 기여자와 GitHub 운영

### 이슈와 PR

작업 흐름 제안: Inbox → Triage → Ready → In progress → In review → Done,
진행 불가 사유는 Blocked로 명시한다. 기존 Project 상태와 매핑해서 도입한다.

Ready에는 사용자 문제, 범위, 관련 ADR/계약, 완료 기준, 검증 방법, 담당자를 적는다.
개인당 주 작업 하나, 저장소 전체 동시 진행 상한은 실제 리뷰 여력에 맞춘다.
Done은 코드뿐 아니라 검증 증거·문서·ADR·Project 반영까지 포함한다.

라벨은 `area/*`, 종류, 우선순위의 세 축으로 정리하고 마일스톤은 합의한 릴리스 약속에 연결한다
(2026-09-19 실행: `area/*` + 기존 `bug`·`enhancement`·`documentation` + `refactor`, 우선순위는 보드 필드 — §11 실행 기록).
착수/담당 표시를 기존 CONTRIBUTING의 작업 선점 규약과 연결한다. 응답 시간은 먼저 측정하며,
48시간 응답 같은 외부 약속은 담당 여력이 확인되기 전 확정하지 않는다.

PR 검토 순서는 제품 목표 적합성 → 패키지/확장 경계 → 안전성과 복구 → 호환성 →
동작 검증 → 문서·운영 영향이다. AI 작성 여부와 무관하게 같은 기준을 쓴다.
리뷰 에이전트의 통과 판정은 필수 CI와 사람이 책임지는 merge 정책을 대신하지 않는다.

### 보안·품질 운영의 다음 준비

- 비공개 취약점 신고 경로를 열고 SECURITY.md를 실제 설정과 일치시킨다.
- 영역별 리뷰 책임을 정하고 required review/CODEOWNERS 적용 범위를 검토한다.
  1인 운영에서 불가능한 승인 규칙을 만들지 말고 bootstrap 예외·릴리스 책임을 문서화한다.
- fork PR 실행과 release 권한을 분리한다. 외부 코드 실행에 지속적인 개인 머신·비밀정보를 주지 않는다.
- dependency·secret 검사, manifest·패키징·문서 계약 검사와 재현 가능한 CI를 점검한다.
- CI의 실제 Python/OS와 사용자 설치 경로를 맞춘다. 현재 CI는 Ubuntu/Windows,
  Python 3.11/3.12이며 install.sh E2E와 3.13 관련 열린 이슈를 재판정해야 한다.
- 릴리스에는 candidate artifact 자체의 설치/업그레이드 검증, 알려진 문제,
  이전 버전으로 돌아갈 수 있는 범위와 세션 마이그레이션 방침을 포함한다.

AGENTS.md는 불변식과 문서 진입점으로 짧게 유지한다. 빠르게 변하는 규칙·체크리스트·현황은
committed docs/이슈/Project가 소유하고 개발 도구별 어댑터는 그 규칙을 참조한다.
현재 AGENTS.md의 한 세션 한 작업과 CLAUDE.md의 독립 이슈 병렬 허용은 서로 다르다.
공통 불변식·외부 기여자의 의무·도구별 세션 운영을 구분해 정합성을 맞추는 작업을 추가한다.
이번 전략 검토로 기존 작업 규칙을 변경한 것은 아니다.

기여자 모집을 모든 준비 이후로 미룰 필요는 없다. 재현·문서·예제·독립 확장에 참여할
2~3명의 지속 사용자를 먼저 찾고, 작은 완료 경험 → 영역 책임 → 리뷰/릴리스 권한으로 넓힌다.
연락·초대·권한 변경은 별도 실행 작업이다.

AGENTS.md를 기여자용으로 손볼 때는 메인테이너의 도구별 절차(OMC·workflow·handoff)와 기여자가 지킬
불변식·게이트를 분리한다. 지금은 둘이 섞여 있어 외부인이 읽으면 절반이 자기 얘기가 아니다.
외부 PR 3건 중 2건(#217, #223)이 citation lock과 메인테이너 선착으로 버려졌으므로, 클레임 규약과
`check_citations.py --fix`의 자동화(pre-commit 또는 PR 봇)는 기여자를 받기 전에 둔다.

## 10. 배포 순서와 가까운 실행 순서

버전 번호와 날짜보다 각 배포의 사용자 약속을 먼저 정한다.

| 단계 | 사용자 약속 | 출구 조건 |
| --- | --- | --- |
| 다음 안정화 배포 | 설치하고 작업을 이어 쓸 수 있다 | 중대 이슈 판정·해결, candidate 설치/업그레이드, 실제 모델/TUI, 알려진 제한 |
| 확장 경험 베타 | 업무별 Pack을 설치하고 사용할 수 있다 | #253 계약, 간단한 CLI, testkit, 공식 패키지 2~3개, 첫 사용 문서 |
| 서버·웹 preview | 같은 환경의 세션을 웹으로 이어 쓴다 | 세션 소유권·인증·재접속·승인·확장 한 사례 E2E |
| GA 후보 | 선언한 지원 범위를 지속 유지한다 | 포맷/API 호환·마이그레이션, 지원 환경 실측, 운영·보안·릴리스 책임 |

GA가 모든 웹·데스크톱·cron 기능의 완성을 의미할 필요는 없다.
터미널·SDK의 지원 범위를 먼저 안정화하고 서버·웹을 preview로 유지하는 선택이 가능하다.

릴리스마다 정할 "대표 사용자 성과"의 문구 예시(순서는 제안, 날짜는 두지 않는다):
다음 안정화 배포 = "멀티에이전트가 다시 돌고(#262) 자식 실행이 기록된다", 확장 경험 베타 =
"설치 하나로 다른 에이전트 제품이 된다(`aelix install` → `aelix run`)", 그 다음 = "Analytics가 Aelix 위에서
실제 모델로 돈다". 각 배포 뒤 오너의 Windows 라이브 체크는 유지한다 — beta.2에서 통합 CI가 잡은 회귀가
그 가치를 보여 줬다.

### 기존 이슈를 활용한 시작 순서

| 먼저 할 판단/작업 | 연결할 기존 이슈 | 주의 |
| --- | --- | --- |
| 베타 현황·이미 고친 이슈 정리 | #75, #111, #198 등 | 과거 readiness 수치·체크리스트를 현재 증거로 재검증 |
| 중대 결함 재현·릴리스 차단 판정 | #137, #188, #131, #289, #288, #262, #199 | 열린 제목만으로 현재 재현을 확정하지 않음 |
| 기본 경험·provider·출력 안정화 | #285, #286, #260, #261, #279, #278, #192 | 실제 candidate와 모델/OS에서 확인 |
| 비전·문서 정합성, 문서 기반 | #86, #264, #265, #266, #267 | #86의 과거 시장·보안 주장은 현재 제품 증거로 재검토 |
| 독립 런타임·내구성 계약과 집중 리팩토링 | #199, #137, #284 및 의존성 위반 후속 이슈 | 역방향 import와 TUI closure를 감사 범위에 포함 |
| Pack·정체성 묶음과 실행 UX | #253, #287, #93, #94 | 공개 manifest·권한·실제 consumer 검증 |
| 공식 패키지·카탈로그 첫 사용 | #18, #45, #16, #76 및 Analytics | demo와 유지보수 제품의 표시를 구분 |
| 서버/웹 진입 | #271, #275 및 별도 설계 이슈 | 문서 작업을 서버 구현 완료로 간주하지 않음 |

첫 작업 묶음은 **이슈 현황 대조 → #262 등 핵심 회귀 재현과 안정화 배포 범위 →
비전·규칙 문서 정합성 → 독립 런타임·내구성 계약 감사 → 작은 대표 확장 검증** 순서를 추천한다.
각 항목은 별도 이슈 단위로 실행하고, 전체 로드맵을 하나의 거대 PR로 만들지 않는다.

### 운영 리듬과 지표

- 주간: 재현·triage, 가장 큰 사용자 실패 하나, 실제 데모 한 흐름, 기여 PR·문서 상태 점검.
- 배포 전: 지원 조합과 candidate 고정, 변경 위험에 따른 라이브 검증, 이전 세션 열기,
  설치·업그레이드·확장 호환 검사.
- 배포 후: crash/설치/권한/데이터 손실 이슈를 빠르게 분류하고 필요하면 hotfix.
  상시 대응 인력이 없는 동안 응답 SLA를 약속하지 않는다.
- 지표: 첫 작업 성공률, 재개 성공률, 확인된 기록 손실·중복 실행, 대표 흐름 성공률,
  확장 설치→첫 사용 성공률, 외부 작성자가 완료한 Pack, 리뷰 대기 시간.
  초기에는 지원자 관찰·opt-in 진단으로 측정하고 기본 원격 수집을 전제하지 않는다.

## 11. 포지셔닝과 남은 결정

추천하는 전략 가설은 **Python으로 확장하고, 자기 환경에서 운영하며,
작업·분석 결과를 추적하고 검증할 수 있는 에이전트**다.
Python이라는 언어 자체보다 실제 업무 완수·데이터/실행 기록·확장 전달 경험을 차별성 증거로 삼는다.

#86에는 보안/self-host 우선 포지셔닝이 기록되어 있다. 이를 무시하고 새 전략을 확정하지 않는다.
다만 원격 모델을 사용하면서 데이터가 절대 네트워크 밖으로 나가지 않는다고 표현하거나,
in-process 확장을 sandbox로 표현하는 약속은 제품 근거와 맞지 않는다.
실행 위치, 모델 전송 경로, artifact 출처, 권한 enforcement를 각각 설명한다.

차별성 증거 후보(만들어서 보여 주기 전에는 주장이 아니다):

1. 설치 하나로 다른 제품이 된다 — `aelix install <pack> && aelix run <pack>`.
2. 자식 에이전트까지 기록·비용·재개가 남는다 — §5의 1단계가 완성될 때.
3. 서명된 카탈로그 + 오프라인/폐쇄망 설치 + 이미 보유한 좌석 — 이미 출하됐고 보여 주는 데모만 없다.
4. 차트를 클릭하면 원본 행과 재현 코드가 나온다 — Analytics, 코딩이 아닌 첫 업무.
5. Aelix에게 확장을 만들게 하고 `/reload` — Pi와 동률이며, 없으면 비교에서 진다(#53).

앞으로 확정할 결정은 다음 순서다.

1. 가까운 주 사용자: 개발자 중심 사용 경험에 데이터 사례를 추가할지,
   데이터 실무자를 우선할지. 초기 제안은 전자다.
2. 다음 배포의 지원 약속과 중대 결함 차단 기준.
3. 내구성 범위: 기록·중단 판정부터 보장하고 안전한 복구를 단계적으로 추가.
4. Pack의 공개 계약·호환성 및 설치/실행 UX.
5. 서버의 첫 운영 범위와 웹 확장 모델.
6. `[features] agents`를 기본 on으로 바꿀 시점. #16은 "P3 착지 시 재결정"이라 적었고 기록이 없다.
   #262를 고치면서 결정한다.
7. Copilot 좌석 소비의 ToS 서면 검증(#86이 마케팅 전 필수로 적었고 아직 기록이 없다). 결과에 따라
   기둥 2를 유지하거나 native BYOK로 내린다.
8. 동시에 활성화하는 큰 작업 흐름 상한(§4의 두 개 제안)을 받아들일지, 그리고 그 배분.
9. 복잡도 기준선 정책: 상한 값, 기존 초과 함수의 함수별 기준선, `# noqa` 금지 여부(§2).
10. 세션 저장소: JSONL 유지 + operation record 추가(두 초안 모두의 권고) vs 교체. §3의 측정 뒤 결정.

### 오너 결정 기록 (2026-09-19)

위 1~10에 대한 오너의 답과, 같은 날 확인한 사실이다. main은 여전히 `026bd15`.

| # | 결정 | 따라오는 것 |
| --- | --- | --- |
| 1 | 주 사용자는 **개발자와 데이터 실무자 둘 다**. 첫 공식 확장은 Analytics, 소프트웨어 엔지니어링 확장도 준비한다 | Pack 계약(4)의 첫 소비자가 Analytics다. 계약 범위는 "Analytics pack이 실제로 쓰는 것"에서 출발한다 |
| 2·3 | 다음 배포와 내구성 범위 모두 **안정성·내구성 우선** | 차단 기준은 데이터 손실·보안·기록 누락. §5의 1단계(기록 보존)가 다음 배포의 약속 |
| 4 | **권고 범위 채택**: `contributes.agents`·`contributes.skills` + 로더의 extension tier + `aelix install`/`aelix run` + manifest 호환 버전 범위. theme·settings 프리셋·`default_agent`는 후속 | Analytics 공개 전에 ADR로 확정(#253에 기록) |
| 5 | 서버는 **개인 범위부터 순차적으로**: 기본 세션 관리 → cron 등 예약 작업 → 웹(세션·프로젝트, 파일 미리보기, 확장 관리, TUI가 지원하는 기능 전반) | 세션 관리는 RPC 목록·승인 브리지가 선행(부록 B 9). 파일 미리보기는 현재 RPC 29개에 없는 **읽기 전용·workspace 경계 안의 새 명령**이 필요하다(`bash`는 웹에 그대로 열 수 없다). cron은 무인 실행의 권한 규칙(§8)이 먼저 |
| 6 | agents 기본 on의 조건: 내구성·안정성, **자식 세션이 기록될 것**, **자식의 구체 내용은 부모 컨텍스트에 들어가지 않고 결과만 들어갈 것**, 프로필이 상세하게 지원될 것 | 아래 확인 결과 참조. 첫 둘은 미충족, 셋째·넷째는 충족 |
| 7 | Copilot Enterprise 좌석 **작동은 확인됨** | 확인된 것은 기능이다. #86이 적은 ToS 서면 검증은 별개의 사업 판단으로 남는다 — 문구는 "보유한 좌석으로 동작한다"까지, 보증 표현은 쓰지 않는다 |
| 8 | **동시에 활성화하는 큰 작업 흐름은 둘** — A = 안정성·내구성, B = Analytics pack과 그것이 요구하는 Pack 계약. 서버·웹은 A의 자식 세션 기록 뒤에 잇는다 | 같은 파일을 동시에 고치지 않고 이슈별 변경을 분리 |
| 9 | **Ruff `C901` 상한 20 + 함수별 기준선 파일, `# noqa` 금지** | 게이트 이슈 #293, 40 초과 여섯은 #284 아래 개별 이슈 |
| 10 | 권고안 채택: **JSONL 유지 + operation record 추가** | 아래 "저장 방식" 참조. 적합성 스위트와 `CustomEntry` 규칙은 #294 |

**4번이 묻는 것.** 확장 작성자가 manifest에 무엇을 선언할 수 있고(agents·skills 기여, profile 필드, hook 이름), Aelix가 버전이 올라가도
그중 무엇을 깨지 않겠다고 약속하며(`requires_aelix` 같은 호환 범위), 사용자가 무엇을 타이핑해 설치·실행하는가(`aelix install <pack>` →
`aelix run <pack>`)이다. Analytics가 첫 공식 확장으로 나가면 그것이 쓰는 표면이 사실상의 공개 계약이 되므로 **Analytics 공개 전에**
정해야 한다. 권고 범위: `contributes.agents`·`contributes.skills` + 로더의 extension tier + `aelix install`/`aelix run` + manifest의 호환
버전 범위. theme·settings 프리셋·`default_agent`는 뒤로.

**8번이 묻는 것.** 한 사람이 동시에 크게 벌이는 작업 흐름을 **두 개로 제한**할지다. 위 결정에 맞추면 흐름 A = 안정성·내구성(2·3·6:
완료 대조, 자식 세션 기록, 역방향 import 게이트), 흐름 B = Analytics pack과 그것이 요구하는 Pack 계약(1·4). 서버·웹(5)은 A의 자식 세션
기록이 끝난 뒤 그 자리를 잇는다 — 서버가 소유할 세션이 먼저 내구적이어야 하기 때문이다. 오너가 이 권고를 받아들였다.

**9번 — 채택된 권고(복잡도 기준선).** 게이트는 이미 CI에 있는 Ruff `C901`로 건다. 2026-09-19 실측한 초과 함수 수는 상한 10에서 160, 15에서 58,
**20에서 30**, 25에서 16, 30에서 10, 40에서 6이다. 권고: **새 함수·기준선에 없는 함수의 상한은 20**, 이미 넘는 30개는
`complexity-baseline.json`(함수 → 점수)에 적고 **점수가 오르면 실패, 내려가면 기준선을 낮춰 다시 쓴다**(내려가기만 하는 래칫).
`# noqa: C901`은 쓰지 않는다 — 숨기면 악화를 못 본다. 40 초과 여섯은 #284 아래 개별 이슈와 목표 점수를 갖고, `run_tui`와
`_async_main`+`parse_args`를 먼저 한다(열린 버그 #289·TUI 배치와 겹친다). Radon 재귀 집계(4,774 / CC>20 74)는 게이트가 아니라
릴리스마다 적는 추세값이다.

**6번 확인 결과 (2026-09-19, 실제 모델 1회 + 코드).** 명령: scratch cwd에서
`aelix --agents --provider openrouter --model anthropic/claude-haiku-4.5 --permission-mode plan --session-dir <scratch> --mode json -p "<explorer에게 note.txt를 읽게 하는 지시>"`,
Python 3.12.13.

- 위임은 성공했다(`agent explorer · ok · plan · $0.0110 · 5.7s`, exit 0). **#262의 오류 문자열
  `'typing.Union' object has no attribute '__discriminator__'`는 ADR-0241·CHANGELOG가 Python 3.14 + openai SDK 문제로 기록한 것과
  같고, #263(설치기가 3.11~3.13을 요청)이 main에 들어가 있다.** #262는 "수정됐으나 검증·종결 대기" 후보다 — 3.13 설치본에서 병렬
  배치를 한 번 돌려 확인하고 닫는다. 이미 3.14로 깔린 사용자는 재설치가 필요하다.
- **부모 컨텍스트에는 결과만 들어간다 — 충족.** 부모 모델이 받은 toolResult는 129자(자식의 최종 답 + 사용량 footer)였고 자식의
  `read` 호출은 부모 메시지에 없다. 코드로도 `render_subagent_result`는 `summary` + 주석 + footer만 content에 넣고, 원문(`details`)은
  tool card용이며, tool trail은 chain 모드에서 **다음 자식**에게만 간다. 주의: `summary` 상한(`output_cap`)의 기본값이 51,200바이트라
  말 많은 자식 여럿이면 부모 컨텍스트를 크게 쓴다 — 기본값을 낮추거나 profile별로 조이는 것을 같이 검토한다.
- **자식 세션은 기록되지 않는다 — 미충족.** `--session-dir`에는 부모 세션 파일 하나(2,409바이트)만 생겼다. 부모에 저장된 toolResult에는
  `details` 키가 없고(#168), 자식의 비용 $0.0110은 결과 텍스트 footer에만 있으며 세션 usage 기록에는 없다. 즉 자식의 구체 내용은
  실행이 끝나면 **어디에도 남지 않는다.** 오너의 요구(구체 내용은 부모 밖에, 그러나 기록은 될 것)는 지금의 content/details 분리
  위에 그대로 얹힌다: `details` 자리에 **자식 세션을 가리키는 링크**를 두고 구체 내용은 자식 세션 파일이 갖는다(부록 B 2~4).
- **프로필 17개 키는 전부 소비처가 있다 — 충족(코드 확인).** 자식 argv로 가는 것: `model`·`provider`·`tools`·`builtin_tools`·`skills`·
  `inherit_skills`·`extensions`·`inherit_extensions`·`context_files`·`thinking`·`system_prompt`(append/replace)+본문
  (`agents/resolver.py` `profile_to_flags`). 부모 쪽에서 집행하는 것: `role`(`print_channel.py`, leaf면 위임 도구를 주지 않음),
  `output_cap`(두 채널), `timeout_ms`(`batch.py`: 호출 인자 > profile > 기본), `approval_mode`(`consent.py`·`batch.py`).
  알려진 빈틈: 한 호출에 서로 다른 profile을 섞는 것은 미지원(ADR-0199가 P4로 미룸) · CLI `--model`이 profile의 model을 조용히 가린다(#157) ·
  RPC 채널은 프로덕션 호출자가 없다(#123) · 확장이 profile을 실어 나르는 tier가 없다(#253) · 위임 중 개입·중단이 없다(P4).
  `uv run --no-sync pytest tests/agents tests/agents_ext -q`는 2026-09-19 main에서 **1712 passed / 11 skipped**(96초)였다.

**저장 방식 — Pi는 어떻게 하는가, 초기에 전환하면 이득이 큰가.** Pi가 바꾼 것은 **엔진이 아니라 데이터 모델**이다.
[harness 명세](https://github.com/earendil-works/pi/blob/8a7b0c03dfb702663acafb6dc29f8acaa4ffe391/packages/agent/docs/harness.md) §0.3·§1.7 기준:

- 저장소는 셋이다 — **entries**(대화 트리, write-once) · **values/lists**(현재 가변 상태: operation state, 대기 중 입력, tool 진행) ·
  **usage ledger**(비용, append-only). 모든 쓰기는 원자 트랜잭션이고, durable 전이마다 `operationState`를 **완전한 현재 상태로 교체**해
  복구가 저널 재생 없이 그 지점에서 시작한다. provider 요청과 tool 호출은 intent → 효과 → settlement 두 커밋으로 감싼다.
- 백엔드는 Memory · JSONL · SQLite 셋이고 **같은 적합성 스위트를 통과**한다. coding agent는 여전히 JSONL이다(format 4, `JsonlSessionRepo`,
  원자적 rename). SQLite(`session-backends/sqlite-node`, 세션당 파일 하나가 기본)는 server/worker 쪽 백엔드다. **format 3은 계속 읽는다**
  (legacy-v3 코드) — Aelix의 현재 헤더가 바로 `version: 3`이다.
- JSONL의 비용을 Pi 스스로 적어 둔다: 열 때 파일 전체를 RAM으로 재생 · fsync 약속 없음(프로세스 크래시 수준) · compaction(J1) 미구현이라
  값 교체가 전부 죽은 바이트로 남는다(연속 출력 tool은 10분에 ~15 MiB). 그리고 **어느 백엔드도 두 번째 writer를 감지하지 못한다** —
  소유권은 호스트 책임이다. 즉 SQLite로 가도 #137은 풀리지 않는다.

초기 전환의 이득은 **엔진과 모델을 나눠서** 봐야 한다.

- **엔진 전환(JSONL → SQLite)의 이득은 지금은 작다.** SQLite가 주는 것은 여러 세션에 걸친 색인 조회·검색·통계, 상태의 in-place 교체,
  쓰는 중의 일관된 읽기다 — 전부 서버 트랙의 요구이고 단일 사용자 TUI의 요구가 아니다. 비용은 grep·복사·내보내기가 되는 세션 파일을
  잃는 것, Windows·NFS에서의 WAL sidecar, 베타 사용자 세션 마이그레이션이다.
- **모델 전환의 이득은 초기일수록 크다.** format 3으로 쓰인 세션은 영원히 읽어야 한다(Pi가 legacy-v3를 들고 가는 이유). 그래서 지금 할 것은
  (1) spawn 계보·usage ledger·operation record(시작/정산)를 **새 entry 타입으로 추가**하고 헤더 버전과 마이그레이션 훅을 두는 것,
  (2) "트랜잭션 하나 = 물리적 한 줄" 규칙과 찢어진 마지막 줄 폐기(ADR-0208이 절반을 이미 했다), (3) 기존 `SessionStorage` Protocol 위에
  **JSONL·Memory 공통 적합성 스위트**를 두는 것이다. (3)이 있으면 서버가 색인을 요구하는 날 SQLite는 **마이그레이션이 아니라 백엔드 추가**가
  된다. **새 기록은 새 `type`이 아니라 기존 `CustomEntry`(`type: "custom"` + `custom_type`)에 싣는다.** 확인한 바로는
  `entry_from_json`이 모르는 `type`에 `ValueError`를 던지고, 로더(ADR-0208)는 그 줄을 "손상"으로 건너뛰면서 **그 줄을 부모로 둔
  entry들까지 가지치기한다.** 새 type을 트리 중간에 넣으면 옛 Aelix는 그 뒤 대화를 통째로 잃는다. `CustomEntry`는 옛 버전이 이미
  읽으므로 공짜인 하위 호환 운반체다.

### 실행 기록 (2026-09-19) — 방향을 보드로 옮긴 것

오너 승인으로 같은 날 실행했다. 코드는 바꾸지 않았다.

**완료 대조.** beta.2에서 머지된 채 열려 있던 이슈를 이슈별 증거로 판정했다. 모든 수정 커밋이 `v0.1.0-beta.2` 태그 안에 있고
main CI(run 34705932974)는 여섯 레그 전부 green이다.

| 판정 | 이슈 | 근거 |
| --- | --- | --- |
| 종결 | #198 #244 #247 #248 #249 #251 | 2026-09-09 TUI 라이브 확인 기록(`handoff-beta2-2026-09-08.md`) |
| 종결 | #239 #240 #241 #243 | `windows-latest` py3.11·py3.12 레그 green(통합 run 34283397963 + main). **Windows 데스크톱 라이브 결과는 저장소에 기록이 없다**고 코멘트에 적었다 |
| 종결 | #258 | 2026-09-19 라이브, api.anthropic.com: `claude-sonnet-5`·`claude-fable-5-1` × high/off 네 쌍 모두 OK(수정 전 400) |
| 종결 | #262 | ADR-0241이 원인 확정(Python 3.14 × `openai<2`), 수정 #263은 main에 있고 설치기는 main에서 받아진다. 2026-09-19 Python 3.12.13에서 같은 모양(explorer · parallel · 3 tasks) 3 ok / 0 failed. 3.14 기존 설치는 재설치 필요 |
| **열어 둠** | #142 | ①(extras assert)은 출하, 네 이름 모두 PyPI에 `0.1.0b2` 게시 확인. 남음: `pypi` environment의 `protection_rules=0`(required reviewer 없음), GA 메타데이터 |
| **열어 둠** | #172 | beta.2의 빌드타임 갱신(+102)만 출하. 런타임 overlay·갱신 파이프라인 미착수 |

열린 이슈는 141 → 134(종결 12, 신규 5). 닫힌 12건은 보드에서 Done으로 옮겨졌다.

**마일스톤 `v0.1.0-beta.3`.** 사용자 약속은 "설치하고 작업을 이어 쓸 수 있다 — 멀티에이전트가 돌고 자식 실행이 기록된다", 출구 조건은 §10의
"다음 안정화 배포" 행 그대로이고 날짜는 두지 않았다. 범위는 **후보**다 — 출구 조건이 배포를 결정한다.

| 묶음 | 이슈 |
| --- | --- |
| 기록·내구성 | #199(P0) #168 #194 #260(P0) #261 #294(신규) |
| 보안 | #131(P0) — #188·#137은 이미 `v0.1.0` 마일스톤의 P0라 옮기지 않았다. **beta.3로 당길지는 오너 판단** |
| 기본 경험 회귀 | #285 #286 #289 #288 #259 #256 #157 |
| 설치 경로 | #279 #278 #192 |
| 구조 게이트 | #292(신규, 역방향 import) #293(신규, 복잡도 래칫) |
| 문서 정합 | #266 #265 #267 |

**신규 이슈.** #290(RPC 웹 최소 제어 계약: 승인 브리지·`list_sessions`·제한된 명령 실행) · #291(읽기 전용·workspace 경계 파일 미리보기) ·
#292 · #293 · #294. #290·#291은 서버·웹 트랙이라 마일스톤 밖이다.

**라벨.** `area/*` 21개 + `refactor` + `claimed`를 만들고 제목 접두어로 94건에 붙였다. 종류 축은 기존 `bug`·`enhancement`·`documentation`을
그대로 쓰고, **우선순위는 라벨이 아니라 보드 필드**에 둔다(기존 관행). 접두어가 없어 영역을 못 붙인 35건은 다음 트리아지 대상이다:
#6 #25 #26 #27 #29 #30 #33 #34 #35 #40 #41 #42 #43 #45 #46 #47 #53 #54 #61 #69 #83 #84 #85 #86 #98 #110 #123 #124 #127 #128 #148 #197
#254 #257 #283.

**결정을 이슈에 남긴 곳.** #253(Pack 계약 범위) · #199(agents 조건과 라이브 확인, 설계 메모) · #284(복잡도 정책) · #16(agents 기본 on 조건) ·
#86(주 사용자, Copilot 기능 확인과 ToS의 구분, 태그라인 정직성).

비전은 넓게 두되, 다음 배포의 약속은 좁고 검증 가능하게 둔다.

## 부록 A. 리팩토링 분리 후보 — 읽기 전용 감사 실측 (main `026bd15` 기준)

#284의 조사 입력이다. 줄 번호는 `026bd15` 시점의 스냅샷이며 이 문서는 citation gate 밖이므로 코드가 움직이면
어긋난다 — 착수할 때 심볼 이름으로 다시 찾는다. 넷 중 셋은 저자가 이미 경계를 그어 놓았고, 옮기는 것이지
다시 설계하는 것이 아니다. 단 Optional callback이라는 사실이 이동의 안전을 증명하지는 않는다(§5).

| 파일 | 이미 있는 seam | 분리 후보(대략 줄 수) |
| --- | --- | --- |
| `harness/core.py` 4,815줄 | `bind_core(_RuntimeActions(...))` :653–681이 18개 `_action_*`를 키워드 표로 나열 · 배너 `Internal: callback bridges` :3404 · 배너 `auto-retry state machine` :1997 | (a) extension action 표 + context 팩토리 :3406–3983 ≈570 · (b) hook/provider emit 브리지 :3984–4317 ≈334(+세션 lifecycle emit ≈88) · (c) auto-compaction + auto-retry :1735–2129 ≈380(호출점은 `prompt()` 4곳) · `Pending*Write` 8종 + dispatcher :317–404, :3030–3100 ≈160. 같은 자리에서 역방향 import(:558 등)를 주입으로 뒤집는다 |
| `cli/entry.py` 3,300줄 | 진짜 seam은 early-exit verb 체인 :1831–1939(`extension`·`docs`·`status`·`--help`·`--version`·`--list-models`·`--export`) · 종단 dispatch 3-arm :3041/:3090/:3121 | (a) verb 체인 → `try_early_exit(argv, parsed)` ≈110 — #289("argv[0]만 본다")가 바로 이 자리 · (b) 세션 해석 + resume 피커 :376–718 ≈343 · (c) prompt-file + system-prompt 조립 :776–908, :1187–1320 ≈265(#287 SYSTEM.md 로더가 들어갈 자리). `cli/args.py:351` `parse_args`(Ruff 80)는 같은 뿌리(플래그 표가 코드로 펼쳐짐)라 같은 이슈 묶음 |
| `cli/extension_install.py` 4,491줄 | `# === … ===` 섹션 배너 + `run_extension_command_async`의 `if sub == …` 11-verb 체인 :4402–4470 | (a) Ed25519 keygen/sign/trust :3866–4242 ≈380(SettingsManager 생성 전에 라우팅되어 공유 상태 없음) · (b) source-list 영속 + `source` verb :1797–2279 ≈490 · (c) pip↔uv ambient index :595–1010 ≈415(순수 함수) · manifest-binding :2280–2754 ≈470. pack 설치 경로(§6)를 넣기 직전에 |
| `tui/shell.py` 3,918줄 | `run_tui` :452–3185(2,734줄, Ruff C901 248, 중첩 함수 ~60개) — `CommandContext(...)` :1981–2013이 평평한 키워드 표, 필드는 `tui/commands.py`에서 Optional로 타입됨 · 선례 `_drive_compaction_indicator` :305는 이미 모듈 수준 | (a) 세션 lifecycle 6종 :899–1224 ≈326(공유 의존 `_rebind` :2544) · (b) settings/picker 모달 16종 :1225–1967 ≈743 — 실작업은 이미 `settings_rows`/`model_picker`/`login_wizard`/`stats_dashboard`에 있고 남은 것은 배선 · (c) retry countdown :2206–2368 ≈163 · `_build_banner` :3329–3545 ≈217. 이동 뒤 closure 캡처·세션 교체 후 최신 harness 참조·취소·키바인딩·렌더링을 `uv run aelix`로 직접 확인 |

provider 층은 세 번째 군집이다(Radon: `stream_openai_completions` 82 · `process_responses_stream` 57 ·
`build_params` 53 · `detect_compat` 48 · `stream_anthropic` 46 · `convert_messages` 45). §5의 처방(정책 데이터 +
얇은 어댑터)을 따르되, #61(provider 라이브 스모크)이 먼저 있어야 안전하게 손댈 수 있다.

## 부록 B. 감사가 뽑은 최소 변경 후보

순서는 제안이고 각 항목은 별도 이슈다. 하나를 끝냈다고 그 이슈 전체가 닫히지는 않는다(§5의 검증 조건).

| # | 변경 | 앵커 | 주의 |
| --- | --- | --- | --- |
| 1 | #262 재현·수정·실제 모델 검증 | `aelix_agents/` | 자식 기록 기능보다 먼저 |
| 2 | 두 채널의 `--no-session` 제거 | `agents/resolver.py` `profile_to_argv` · `aelix_agents/rpc_channel.py` | 저장 위치·보관·권한 정책이 같이 온다 |
| 3 | 자식 세션과 부모의 spawn 계보 기록 | 새 필드 또는 새 entry 타입 | `SessionInfoEntry.parent_id`(엔트리 트리 부모)와 `parent_session_path`(fork/clone)를 재사용하지 않는다 |
| 4 | `aggregate.roll_up_usage`를 세션 통계에 연결 | `harness/_session_stats.py` | 부모/자식 이중 합산 금지, `tokens`는 최대값 |
| 5 | `Contributes`에 `skills`·`agents` 추가 | `contracts/manifest.py` `Contributes`(`extra="forbid"`) | schema·가이드·카탈로그·ADR이 같이 움직인다(#253) |
| 6 | skills·profile 로더에 extension tier | `cli/entry.py` `_resolve_skill_dirs` · `agents/discovery.py` | 출처·trust·동명 우선순위·재로드·제거 검증 |
| 7 | `default_agent` 설정 키 | `cli/args.py` `--agent` 읽는 자리 | 후속 범위(§6) |
| 8 | `aelix run <profile>` verb + `aelix install` 별칭 | `cli/entry.py` early-exit 체인 | 부록 A entry.py (a)와 같은 묶음 |
| 9 | RPC `list_sessions` · 승인 응답 상관(`extension_ui_response` 브리지) · 제한된 명령 실행 | `rpc/rpc_mode.py` 핸들러 표 · `_on_line` | 허용 범위·권한·인자 schema·취소 응답을 같이 |
| 10 | `shell.py` 모달 클로저를 `CommandContext` 뒤로 이동 | `tui/shell.py` | 부록 A shell.py (b); TUI 직접 확인 |

## 부록 C. 오너 원문 12항 → 이 문서의 위치

| 원문 | 위치 |
| --- | --- |
| 베타 테스트·버그 안정화 · 이슈 빨리 쳐내기 | §2 완료 대조 → §10 시작 순서 · §9 이슈 흐름 |
| 디자인 패턴·복잡도·리팩토링 | §2 복잡도 · §5 리팩토링 방법 · 부록 A |
| Pi 최근 동향(durable substrate) | §3 |
| 멀티에이전트 세션 기록 · substrate 견고성 · 코드 구조 정합 | §5 내구성 단계 · 유지할 경계(역방향 import) · 부록 B 1~4 |
| 기여자 대비(보안·품질·CI/CD·AGENTS.md·운영모델) | §9 |
| 마켓플레이스 카탈로그 0 · analytics · 와우 포인트 | §6 · §7 · §11 차별성 증거 후보 |
| 확장 예시 부족 | §6 예제와 공식 제품 |
| 문서·홈페이지·마켓 페이지 · `aelix install` | §7 · §6 |
| 확장 = 제품(system.md + 도구 + 스킬 묶음, 바로 실행, 정체성 스폰) | §6 Pack · 부록 B 5~8 |
| aelix server(세션·cron·API·인증) | §8 |
| 웹/데스크톱(세션 조회·생성·진행, 확장 동작, 앱 확장성) | §8 · 부록 B 9 |

## 부록 D. 측정 명령

```sh
gh issue list --state open --limit 300 --json number --jq length                       # 141 (2026-09-15)
for n in 198 239 240 241 243 244 247 248 249 250 251 142 172; do gh issue view $n --json state; done   # 12 OPEN, #250만 CLOSED
gh api repos/handochan/aelix-ai/branches/main/protection                                # 필수 리뷰 없음 · enforce_admins false · 필수 검사 6
gh api repos/handochan/aelix-ai/private-vulnerability-reporting --jq .enabled           # false
uvx --from radon==6.0.1 radon cc -j packages src   # 클래스 methods·closures까지 재귀 집계: 4,774 · >20 74 · >50 5
uv run --no-sync ruff check --select C901 --config 'lint.mccabe.max-complexity=40' packages src   # 6 functions
grep -rn "from aelix_coding_agent\|import aelix_coding_agent" packages/aelix-agent-core/src       # 13 (runtime 10, TYPE_CHECKING 3)
curl -s https://handochan.github.io/aelix-marketplace/catalog.json                       # extensions: []
gh api repos/earendil-works/pi/pulls/9131 --jq .merged   # false (Durable Objects backend, 미병합)
gh api repos/earendil-works/pi/pulls/7669 --jq .merged   # true  (harness v2 r2)
```
