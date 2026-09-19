# Claude Code 베타 이후 방향 문서 교차 검토

Status: Draft
Date: 2026-09-15
Baseline: `026bd15db229d86d61b74fd3b8c1d8eb0f750715` (local and GitHub main)

## 결론

Claude Code의 `direction-post-beta2-2026-09-15.md`는 공통 계약을 먼저 검증한다는 방향과
구체적인 코드 감사에서 추가 가치가 있다. 특히 core 역방향 의존, TUI closure,
기존 AgentProfile 재사용, RPC 제어 공백을 기존 Codex 초안에 반영했다.
일부 수치·구현 해석·일정은 수정하거나 조건부 제안으로 남겼다.

반영 문서: `docs/05-post-beta-direction.md`.
Claude 원문은 이 검토에서 수정하지 않았다. 첫 확인본(525행)의 SHA-256:
`fb265f1e5ae9eb3ec991a49ae83012f1d9ec5caae364860dd6f6d20ea80248d1`.

검토 도중 다른 작업에서 원문이 600행으로 갱신되어 추가 내용을 다시 읽었다.
마지막 확인본 SHA-256:
`189174c134047a802635058e8fb72f2c8b6d1bb4403325fd249bf1739cf89165`.
갱신본은 Ruff 6개·Pi의 새 JSONL·Codex와의 대조를 이미 반영했다.
아래 초기 판정 중 정정된 내용은 해결된 것으로 읽는다. 다만 §4.1/§7의 두 함수 기준선,
`parent_id` 설명, 일정·엄격한 선후관계 등의 잔여 차이는 유지된다.

## 1. 반영한 내용

| 내용 | 재확인한 근거 | 반영 방식 |
| --- | --- | --- |
| 기반/공통 계약/사례/사용 환경 네 트랙 | 두 문서의 전략 비교 | 기존 일곱 영역 위에 실행 순서와 동시 큰 작업 두 개 이하를 제안 |
| core → coding-agent 역방향 의존 | AST로 13개 import, 런타임 10·TYPE_CHECKING 3; import 차단 probe 실패 | 독립 런타임 검증을 선행 구조 과제로 추가 |
| TUI의 큰 closure | Ruff C901 `run_tui` 248; Radon closure 63개 | 기존 CLI/provider 중심 리팩토링 감사 범위에 TUI 포함 |
| 기존 AgentProfile 재사용 | profile.py의 model/tools/skills/extensions/system_prompt/approval_mode 등 | 새 정체성 포맷보다 설치·발견·실행 경로 우선 |
| system prompt 변경 hook 존재 | `BeforeAgentStartResult.system_prompt` | API 미구현과 예제·배포 부족 구분 |
| 자식 비용 수집은 이미 존재 | `SubagentUsage`, `roll_up_usage`, `render_subagent_result` | 안정된 실행 ID로 저장·조회·중복 없는 합산을 설계 |
| RPC 제어 공백 | 목록/명령 실행 handler 없음; UI 응답 즉시 return | 승인 왕복·목록·제한된 명령 실행을 웹 최소 계약에 추가 |
| Analytics 환경 분리 | pyproject: Python >=3.12, DuckDB/Polars/Plotly/Pydantic | 3.11+ 본체를 유지하는 가벼운 adapter + 별도 실행 환경 |
| 예제를 계약 검증에 사용 | 이미 존재하는 contributes 패밀리와 실제 consumer | 패밀리별 호출·실패·reload 검증 추가 |
| 작업 선점·라벨 운영 | CONTRIBUTING, AGENTS/CLAUDE 규칙 차이 | 이슈 소유권과 공통/도구별 규칙 정합성 작업 추가 |
| Chord 참조 | Pi 고정 SHA의 PLANNING.md | lifecycle·서비스·상태 복제 경계만 참고, 직접 도입 결정은 보류 |
| 인기 지표 수집 | 설계 제안 | 도입 시 빌드/예약 수집·캐시, 설치 신뢰 데이터와 분리 |

## 2. 수치와 사실을 바로잡은 부분

### 복잡도 집계와 CI 기준선

`uvx --from radon==6.0.1 radon cc -j packages src`는 종료 코드 0.

- Claude의 최상위 blocks 3,592와 A2882/B437/C209/D39/E15/F10은 재현됐다.
- Codex의 함수·메서드 4,331과 CC >20 73개, >50 5개는 클래스 요약을 메서드로 대체한 집계다.
- 두 집계는 분모가 다르며 중첩 closure까지 전부 펼친 함수 개수가 아니다.
- 갱신본의 재귀 집계도 확인했다: 함수·메서드·closure 4,774, CC >20 74, >50 5.
  앞으로 비교할 기준은 이 재귀 집계로 명시한다.
- `_async_main` Radon 109도 재현됐다. 원문의 "109가 재현되지 않았다"는 문장과
  뒤의 "Radon 109 / Ruff 82" 설명은 서로 다르다. 점수는 도구·집계 방식과 함께 표기한다.

Ruff 0.15.13의 다음 전체 범위 검사에서 종료 코드 1, 진단 6개를 확인했다.

```sh
uv run --no-sync ruff check --select C901 \
  --config 'lint.mccabe.max-complexity=40' --output-format json packages src
```

| 함수 | Ruff C901 |
| --- | ---: |
| run_tui | 248 |
| _async_main | 82 |
| parse_args | 80 |
| stream_openai_completions | 54 |
| run_print_mode | 42 |
| process_responses_stream | 42 |

따라서 상한 40에 예외 두 개만 두면 된다는 계획은 전체 범위에는 성립하지 않는다.
갱신본의 측정 표는 6개로 바뀌었지만 예외 두 개라는 실행 계획은 여전히 남아 있다.
`# noqa`로 기존 함수를 숨기면 점수가 더 올라가도 그 진단은 보이지 않는다.
래칫은 예외까지 측정하고 함수별 기존 점수보다 악화했는지 비교해야 한다.
상한 40은 아직 합의된 CI 정책이 아니다. CI 설정은 수정하지 않았다.

### 세션 계보·복구

`SessionInfoEntry.parent_id`는 **같은 세션의 entry 부모**다. `Session.set_name`도 현재 leaf ID를
이 필드에 넣는다. fork 전용이라는 원문 설명은 부정확하다.
세션 간 관계에 쓰이는 `parent_session_path`와 별도로 spawn 관계를 설계하는 권고는 반영했다.

`--no-session` 제거·파일 저장·rollup 연결은 복구 구현의 일부다.
기록 ID, 저장 완료 시점, 재시도·부분 실패, 보관·권한, 세션 이동/분기, 두 채널 호환성,
부모/자식 비용 이중 합산 방지 검증 없이 #199 전체 완료로 보지 않는다.
자식 결과에는 이미 사용량 텍스트와 `details` 전달 경로가 있다.
"비용을 재지 않음/텍스트 외 정보가 전혀 없음"으로 일반화하지 않는다.

### RPC와 서버

승인 타입은 존재하지만 `rpc_mode._on_line`이 `extension_ui_response`를 버린다.
따라서 타입 존재가 승인 왕복 구현을 뜻하지 않는다.
세션 목록 handler는 없지만 `new_session`, `switch_session`, `fork`, `clone`은 있다.
현재 서버의 per-connection factory는 새 세션을 만들고 확장을 로드하지 않는다.
이것을 서버 부재 또는 세션 전환 API 전체 부재로 서술하지 않는다.

### Pi JSONL과 경쟁 주장

고정 소스 `8a7b0c03dfb702663acafb6dc29f8acaa4ffe391`의
`packages/agent/src/harness/session/jsonl/`에는 `repo.ts`, `storage.ts`, `legacy-v3.ts` 등이 있다.
legacy 구현 삭제는 JSONL 형식의 포기와 다르다. 저장소 선택의 근거를 수정했다.
Claude 갱신본도 이 점을 정정했으므로 현재 두 문서는 이 부분에서 일치한다.

같은 소스의 subagent 예제에는 `--no-session`과 사용량·cost·details 처리 모두 있다.
독립적인 자식 기록/복구는 비교할 유효한 축이지만 "비용 표시 자체가 Pi에 없음"이나
"Aelix 멀티에이전트가 우월함"까지 증명하지는 않는다.
Pi 패키지 manifest의 전용 항목 부재도 hook/extension으로 구성할 수 없다는 뜻은 아니다.
차별화는 지원하는 설치·실행·복구 경험으로 검증한다.

## 3. 조건부로 반영하거나 채택하지 않은 제안

- **T3는 T2 완성 뒤에만:** 배포·호환성을 약속할 다수 Pack은 관련 계약 뒤에 둔다.
  기존 API의 작은 예제, Analytics prototype, 시작 문서는 먼저 만들어 계약을 검증한다.
- **전체 RPC 갭 0 이후 서버:** 첫 웹 흐름의 승인·목록·실행·재접속 계약을 진입 조건으로 삼는다.
  모든 TUI 명령의 1:1 복제를 시작 조건으로 삼지 않는다.
- **beta.3에 2~3주:** #262, 내구성, TUI/CLI 분할, CI와 운영·문서를 모두 넣는 일정은 재현·설계·
  리뷰 여력 증거가 부족하다. 릴리스 대표 성과는 채택하고 날짜·버전별 전체 범위는 확정하지 않는다.
- **운영모델 하루 / 48시간 응답:** 외부 응답 약속은 현재 CONTRIBUTING의 무SLA 방침과 조율하고
  실제 여력을 확인한다. 이번 검토로 SLA를 만들지 않는다.
- **공식 Pack 최소 10 / 예제 15~20:** 유지보수 여력과 사용 증거 없이 최소선을 고정하지 않는다.
  공식 제품과 작은 계약 예제의 개수를 분리한다.
- **Optional callback이므로 TUI 이동 안전:** 모듈 이동 후보로는 좋지만 캡처·최신 harness·취소·
  키바인딩·렌더링 검증을 생략할 근거는 아니다. 여러 이슈를 한 수정으로 묶지 않는다.
- **profile 발견되면 spawn 완성:** extension scope, provenance, 권한 상한, 명시적 extension/skill
  전달, 충돌과 제거를 검증해야 한다. theme/settings/default_agent는 단계적으로 결정한다.
- **session-redact 예제로 #138 해결:** 기록 변경은 복구·재현성을 바꾼다. 로그·저장·표시 중 어떤
  경계인지 정하고 모든 기록 경로에 적용되는지 검증해야 한다. 단일 hook 예제로 종료하지 않는다.
- **Chord 규칙 = Aelix core 규칙:** 범용 플러그인 기반과 에이전트 커널은 추상화 수준이 다르다.
  역방향 의존은 고치되 에이전트 커널의 model/tool/hook 개념까지 자동 제거하지 않는다.
- **별점·다운로드·스타와 경쟁 우위:** 원문의 모든 시장 규모 수치는 재조사하지 않았다.
  채택한 전략의 근거로 사용하지 않으며, 이후 필요할 때 출처·시점을 고정해 검증한다.
- **갱신본의 완료 이슈 12건:** 완료 대조의 후보로 사용한다. #142 본문에는 version assert 외에
  GitHub environment, publishers, rehearsal, metadata 요구도 있으므로 일부 수정만으로
  이슈 전체를 완료로 보지 않는다. 이번에는 후보 전체의 완료 조건을 검증하거나 이슈를 닫지 않았다.

## 4. 검증 명령과 범위

- 코드 AST의 `ImportFrom`을 TYPE_CHECKING 여부와 함께 분류: 13 = runtime 10 + type-only 3.
- workspace Python에서 `MetaPathFinder`로 coding-agent import만 차단:
  `AgentHarness` 모듈 import 성공, 생성자에서 `ModuleNotFoundError` 재현.
  깨끗한 wheel 설치 실험은 하지 않았다.
- Radon 및 Ruff 명령은 위 결과 참조. 시스템 Python 3.9의 AST는 match 문을 파싱하지 못해,
  분석 명령을 workspace Python으로 다시 실행했다.
- `gh issue view 262`: OPEN, 3/3 실패 사용자 보고 확인. 이번에는 실제 모델 재현하지 않았다.
- GitHub main은 조사 시작점과 동일한 SHA.
- Pi 고정 소스를 GitHub contents API로 조회해 Chord 계획·JSONL 트리·subagent 예제 확인.
- Claude 원문의 두 SHA-256으로 검토 중 동시 갱신을 감지하고 추가 부분을 재검토했다.
  새 문서의 로컬 링크·코드 fence·공백도 확인했다.

코드·CI·GitHub 이슈/Project/설정은 변경하지 않았다.
전체 pytest, 실제 모델·TUI·서버 실행은 이번 문서 비교 범위에서 수행하지 않았다.
