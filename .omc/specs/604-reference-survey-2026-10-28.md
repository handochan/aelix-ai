# #604 레퍼런스 조사 종합 — 감독 가능한 위임과 안전 복구 (2026-10-28)

이 문서는 #604의 0~4단계를 구현하기 전에 공개 오픈소스 레퍼런스를 조사한 결과를 종합한다. 오너의 결정(2026-10-28)은 다음과 같다.
- 위임 timeout을 전부 없앤다: 도구 인자, 프로필 키의 효과, 10분 기본값, 배치 전체 30분 상한.
- 0~4단계가 모두 필요하다. 모두 beta.3 마일스톤에 넣되, 2~4단계는 보장하지 않는다. 2~4단계가 #384 시작 전에 main에 들어오지 못하면 태그 뒤로 미룬다.

등록된 이슈:

| 단계 | 이슈 |
|---|---|
| 0 | #604 |
| 1 | #610 |
| 패널 | #132 |
| 2 | #611 |
| 3 | #123 |
| 4 | #612 |

판정은 `docs/05-post-beta-direction.md`의 2026-10-28 실행 기록에 있다.

기준점은 aelix `origin/main` = `3cec8c0c`이다. 조사는 모두 코드 읽기만 했고, 빌드하거나 실행하지 않았다. 원문 보고서 안의 `UNVERIFIED` 표시는 그대로 유효하다.

| 원문 | 대상 (읽은 SHA) |
|---|---|
| `604-ref-codex-2026-10-28.md` | openai/codex `dddc3c0a` (2026-10-27) |
| `604-ref-opencode-2026-10-28.md` | anomalyco/opencode `7b1e54a7` (v2.2.0), oh-my-opencode `dae75122`, opencode-background-agents `46cfb3de` |
| `604-ref-gemini-qwen-2026-10-28.md` | google-gemini/gemini-cli `363a1311`, QwenLM/qwen-code `32f6713c` |
| `604-ref-python-2026-10-28.md` | MoonshotAI/kimi-cli `9ab1286b`(아카이브됨), OpenHands/software-agent-sdk `ff62a333`, Codewhale(옛 DeepSeek TUI) `e66998be`, goose `398165c9` |
| `604-ref-deepagents-2026-10-28.md` | langchain-ai/deepagents `34130292`, langgraph `858891aa`, agent-protocol `eb5b840f` |
| (이 세션에서 직접 읽음) | earendil-works/pi `269afc66a`: `packages/durable/test/examples/22-subagent-background.ts` |

DeepSeek에는 공식 코딩 에이전트가 없다.
- DeepSeek 문서가 안내하는 Deep Code(`lessweb/deepcode-cli`)에는 서브에이전트가 없다.
- 커뮤니티 CLI들에는 가져올 것이 없다.
- 예외는 Codewhale(옛 DeepSeek TUI)의 자식 중단 요약(`budget_handback.rs`)이다.

## 1. timeout 제거 결정의 검증

| 프로젝트 | 위임 실행 시간 제한 |
|---|---|
| pi | 없다. Esc로 중단하는 것만 있다. |
| Codex | 없다. `agents.job_max_runtime_seconds`를 지웠고 "Removed … retained as a no-op"로 남겼다. timeout은 **기다리는 시간**에만 있다: 기본 30초, 최소 10초, 최대 1시간. 기다림이 끝나도 자식은 계속 돈다. |
| OpenCode v2 | 없다. 에이전트별 `steps`를 선택적으로 둘 수 있고, 한도에 닿으면 도구 없이 최종 요약 단계를 돈다. kill하는 timeout을 넣었던 SessionRunner 스택(#6036)은 머지되지 않고 닫혔다. |
| Qwen Code | 기본 한도가 없다. 한도는 라운드 사이에서만 확인하고, 진행 중인 호출은 끊지 않는다. 한도에 닿으면 이유와 부분 결과를 돌려준다. |
| deepagents / LangGraph | 시간 제한이 없다. `recursion_limit`만 있다(9999, 넘으면 예외). |
| OpenHands | 시간 제한이 없다. 반복 500회, 선택적 예산(USD), stuck 감지가 있다. |
| Kimi CLI | 모델이 `timeout`을 지정할 수 있다. **#2408: 모델이 항상 120을 넣어서 사실상 120초 기본값이 됐다.** #604와 같은 실패다. |
| Gemini CLI | 기본 10분 / 20~30턴, 그리고 60초 최종 경고 턴이 있다. 사용자들이 60분 이상, 100~500턴을 요청했지만 거절됐다(#25768). 반례다. |

결론: timeout 제거는 주요 레퍼런스의 방향과 같다. "모델에게 kill timeout을 주지 않는다"에는 Kimi #2408이 직접 증거다.

## 2. 단계별로 따를 레퍼런스

### 0단계 — 끊지 않는 위임, 멈춘 자식의 결과

- **결과 형식은 Qwen `composeIncompleteResult`를 따른다** (`agent.ts:1541-1611`):
  - 첫 줄에 중단 이유를 둔다. 이 줄은 잘라내기에서 보호한다.
  - 재시도 조언을 붙인다. 예: "같은 호출을 반복하면 같은 결과가 난다".
  - 그다음 "Partial result follows:", 마지막 텍스트, 자식 transcript의 위치를 둔다.
  - aelix는 여기에 도구 기록, 자식 ID, 바꾼 파일을 더한다. 레퍼런스 어디에도 없는 수준이다. Codex는 중단된 자식에 대해 아무 메시지도 주지 않는다.
- **"마지막 텍스트"를 고르는 규칙:**
  - 비어 있지 않은 마지막 assistant 텍스트를 쓴다. deepagents는 Anthropic의 빈 `end_turn` 메시지를 건너뛴다.
  - 텍스트가 없으면 Codewhale 방식으로 요약한다: 도구 12개까지 `name target`, thinking 일부. 원칙은 "부모는 보고서가 아니라 증거를 받는다"이다.
- **성공이 아닌 것을 성공으로 보고하지 않는다.** 실제 사례:
  - OH #3742: 멈춘 자식이 빈 성공으로 보고됐다.
  - OpenCode #37852·#52378·#59051: 잘린 스트림이 정상 종료로 기록됐다.
  - Codex #36799: 상태를 도출하는 경로가 여러 개라 `completed:null`과 errored가 엇갈렸다.

  **상태는 한 곳에서 도출한다.**
- aelix 쪽 후보: PrintChannel이 `agent_end` 없이 exit 0으로 끝난 자식을 성공으로 본다는 측정이 있다(7/31, `rpc-supervisor-track-handoff.md` §2.2, `saw_agent_end`를 읽지 않음). 재확인한 뒤 0단계에 넣는다. OpenCode의 "첫 idle = 끝" 버그(#48826)와 같은 계열이다.
- 오류는 유형과 잘린 메시지로 전달한다. Codex는 약 900토큰으로 자르고, deepagents는 유형만 보낸다. 내부 정보가 새지 않게 하기 위해서다.

### 1단계 — resume

- **제한은 저장된 기록에서 복원한다.** 호출자의 설정으로 다시 만든 프로젝트는 모두 버그가 났다:
  - Codex #26718·#36433: resume하면 모델과 reasoning이 바뀐다.
  - OpenHands: 호출자가 넘긴 `subagent_type`으로 재구성한다.
  - OpenCode: continue할 때 agent가 바뀐다.
- **저장 모델로 따를 것:**
  - Kimi `meta.json`: 원자적으로 기록하고, type·model·system prompt를 재사용한다. 동시 resume은 거부하고, spawn 전에 running으로 표시한다.
  - Qwen sidecar: approval mode, 허용·금지 도구, CLI 플래그, 소유자 pid·호스트, resumeCount를 저장한다.
- **aelix 적용:**
  - ADR-0243 영수증에는 이미 profile, 권한 모드, 요청 모델이 있다. 여기에 축소된 도구 목록, cwd, thinking, context-files 여부를 더한다.
  - 복원할 때는 **저장된 것과 지금 부모 권한의 교집합**을 쓴다. Codex는 권한 교집합을 쓰고, Qwen은 신뢰하지 않는 폴더에서 권한을 낮춘다.
- **소유권 확인:** 이 부모 세션의 자식만 대상으로 하고, 경로가 아니라 ID로 받는다(OpenCode의 `parentID === caller` 확인).
- **resume할 수 없는 이유를 명시한다**(Qwen): transcript 없음, 정체성 불일치, cwd 불일치 등.
- **끊긴 도구 호출 보정:**
  - deepagents `PatchToolCallsMiddleware`는 "did not complete - no result was recorded"를 **error 상태**로 넣는다. 예전에 '사용자가 취소함 + 성공'으로 표시했던 것이 #5546이다.
  - aelix의 provider 변환은 "No result provided"(is_error)를 합성한다(7/31 측정). 문구와 상태를 확인한다.
- **이어하기 지시 문구:**
  - OpenCode: "Continue from where you left off without repeating completed work."
  - Qwen: "Continue working on the current task from the last completed step."

### 2단계 — 백그라운드

- **기다림과 실행을 분리한다.**
  - Codex wait는 최소 10초로 맞추고, 그렇게 했다고 모델에게 알린다. 폴링으로 토큰이 약 6.78억 개 소모된 #39854 이후의 조치다.
  - OpenCode `Job.wait(timeout)`과 `Job.cancel`은 별개다.
  - Kimi `TaskOutput(block, timeout)`은 시간이 지나면 스냅샷을 준다.
- **timeout을 대신할 UX:** OpenCode `Job.block`은 "끝남"과 "백그라운드로 옮겨짐"을 경주시킨다. `ctrl+b`를 누르면 전경 작업이 다시 시작하지 않고 그대로 백그라운드로 옮겨진다. 오래 걸리는 전경 위임을 사용자가 넘기는 키다.
- **완료 전달**은 레퍼런스들이 가장 많이 데인 곳이다.
  - **정확히 한 번 전달:**
    - OpenCode: notificationID를 주입 메시지 id로 재사용하고, 입장하면 marker를 지운다.
    - Kimi: pending→claimed→acked 단계와 오래된 claim 회수, dedupe 키를 쓰고, 복원할 때 이미 기록에 있는 id는 ack한다.
    - Qwen: 등록된 자식마다 최종 알림을 정확히 하나 낸다. 취소에 응답이 없으면 5초 뒤 대비 타이머가 알림을 낸다.
    - deepagents: (task, run, status)로 중복을 막는다.
  - **부모가 바쁘면 다음 경계에서 주입하고, 쉬고 있으면 깨운다.** 반례 두 개:
    - Codex는 기본적으로 쉬는 부모를 깨우지 않는다. #47762에서 결과가 끝내 돌아오지 않았다.
    - Qwen은 부모가 쉴 때만 전달한다. #12207에서 12~19분 늦게 한꺼번에 도착했다.
  - **사용자 질문이나 승인을 기다리는 부모는 깨우지 않는다**(deepagents). 깨우기 직전에 interrupt와 plan 상태를 다시 확인한다(Codex #56330).
  - **알림 형식:**
    - `[SYSTEM NOTIFICATION - NOT USER INPUT]` 머리말, escape된 자식 텍스트, 상태별 다음 행동 힌트, 메타 표식을 붙인다(deepagents).
    - 알림을 만드는 코드는 하나만 둔다. Qwen #12038에서는 7벌이 서로 어긋났다.
  - **여러 완료 묶기**(oh-my-opencode): 중간 결과는 턴을 열지 않고 주입하고, 모두 끝나거나 하나가 실패하면 깨운다.
- **`-p` 모드:**
  - "첫 idle = 끝"으로 보지 않는다. OpenCode #48826에서는 자식이 백그라운드 작업을 남긴 채 턴을 끝냈고, #48361에서는 `run`이 백그라운드 자식을 남기고 종료했다.
  - 남은 의무를 모두 센다.
  - SIGINT를 받으면 모두 중단하고 알림을 flush한다(Qwen).
  - 실패하면 알림을 비우고 원래 exit code를 지킨다(Kimi).
- **기동 시 정리:**
  - 고아가 된 자식은 lost로 표시하고 resume 가능한 상태로 둔다(Kimi `recover()`). aelix는 pid/pgid로 판단한다.
  - **부모 모델에게도 알린다.** Qwen #14273은 자식을 paused로 복원했지만 아무도 깨우지 않아 무인 세션이 멈췄다.
- **동시성:**
  - 끝난 자식은 슬롯에서 뺀다(Codex #22779·#39694).
  - 알림 큐는 결과를 버리지 않고, 버린 것은 요약으로 알린다(Qwen).
- **asyncio 위생:**
  - 태스크를 강하게 참조하고 done 콜백으로 정리한다(Kimi).
  - `asyncio.wait`를 try/finally 밖에 두지 않는다. Kimi `run_soul`이 그래서 #2615가 났다: 종료로 표시된 자식이 26분 동안 계속 LLM을 호출했다.
  - deepagents `_join_task_deferring_cancellation`을 그대로 옮길 만하다.
  - **종료 상태는 자식의 종료 경로만 기록한다**(Kimi #2615, deepagents #6234, OpenCode #54658).
  - 정리가 끝나기 전에 "취소됨"이라고 보고하지 않는다(deepagents #7133).
  - aelix의 #351·#357·#359도 같은 계열이다.

### 3단계 — 개입

- **동사 세트:**
  - Codex V2:
    - `send_message`: 큐에만 넣는다.
    - `followup_task`: 쉬는 자식이면 턴을 시작하고, 도는 자식이면 경계에서 전달한다.
    - `interrupt_agent`: 자식은 resume할 수 있는 상태로 남고, 자기 자신은 끊을 수 없다.
  - Qwen `send_message`는 자식 상태에 따라 다르게 동작한다:

    | 자식 상태 | 동작 |
    |---|---|
    | 실행 중 | 다음 도구 라운드 경계에서 전달 |
    | 일시정지 | 메시지로 재개 |
    | 완료 | 되살림 |
    | 취소·실패 | 오류 |
- **aelix가 앞선 점:** harness의 steer/follow_up 큐가 턴 경계에서 비워진다(pi parity). 진짜 경계 주입이다. LangGraph에는 없고, cancel+restart로 흉내 내다 진행 중이던 단계를 잃는다(#9046, 메인테이너가 연 이슈).
- **레퍼런스가 도와주지 못하는 부분:** 실행 중인 자식에게 명령을 전달하는 경로는 RPC 채널이 필요하다(#123 W3/W4). 레퍼런스는 모두 자식을 같은 프로세스 안에서 돌리므로 이 부분에는 참고가 되지 않는다.
- **정리 요청(wrap up now):**
  - Gemini 최종 경고 턴의 문구를 쓴다.
  - OpenCode `MAX_STEPS_PROMPT` 방식으로 `tool_choice: none`을 건다. 도구 정의는 남겨 캐시를 보존한다.
  - **원래 중단 이유를 유지한다.** Gemini #22323은 회복된 자식을 GOAL로 보고해서 끊겼던 사실을 숨겼다.
  - 종료나 세션 전환에는 LangGraph의 drain을 쓴다: 경계에서 멈추고 resume할 수 있는 상태로 남긴다.
- **멈춤 감지 — 죽이지 않는다:**
  - OpenHands의 idle 시계(모든 업데이트에 리셋)와 stuck 패턴, nudge 한 번을 쓴다. 패턴: 같은 행동+관찰 4회, 오류 3회, 독백 3회, 교대 6회.
  - 패널에 "정체"로 표시하고 부모와 사용자에게 알린다.
  - timeout이 없어진 자리를 채우는 장치다. OpenCode #46461·#51291에서는 이런 가드가 없어 스트림 정체로 20분, 반복으로 50분이 흘렀다.

### 4단계 — 호스트 노출

- **ACP:**
  - Qwen 방식: 자식의 도구 호출을 중첩 `tool_call`(`_meta.parentToolCallId`, `subagentType`)로 내보낸다. 중첩을 모르는 호스트에는 부모 카드에 진행 상황을 붙인다(`_meta.subagentProgress`). 자식의 승인 요청은 `session/request_permission`으로 보낸다.
  - OpenCode 방식: `_meta["opencode/child-session"]`, 그리고 capability로 켜는 확장 알림 `opencode/session/child_update`.
  - 종료 이유 목록은 Gemini를 따른다: completed|failed|aborted|max_turns|max_budget|max_time|refusal|elicitation.
- **RPC:**
  - Codex app-server:
    - 자식을 독립 thread로 다룬다.
    - `CollabAgentToolCall`의 `agentsStates`와 `SubAgentActivity`를 쓴다.
    - parent/ancestor로 자식 목록을 조회하고, id로 steer·interrupt를 보낸다.
  - Agent Protocol:
    - namespace 트리에 toolCallId를 원인으로 붙인다.
    - `input.inject`, `cancel?wait&action`을 쓰고, `Last-Event-ID`로 재연결한다.
    - **`on_disconnect`의 기본값이 cancel인 것은 따르지 않는다.**
  - OpenCode: 유실될 수 있는 SSE 스트림과, 커서로 다시 읽을 수 있는 내구성 로그를 둘 다 둔다.
- **피할 것:**
  - 자식 이벤트를 버리는 것. Kimi ACP, Gemini, deepagents가 그렇다.
  - 승인 요청 라우팅 누락(deepagents #4033).
  - 이벤트 펌프 안에서 승인을 기다리는 것. Kimi wire에서 교착이 났다.
- ACP에는 아직 표준 steer가 없다(Gemini #29900, Qwen #11513 모두 열려 있음). aelix는 `aelix_control` 확장(ADR-0275)으로 낸다.

## 3. 프로세스-per-자식은 유지한다

레퍼런스 대부분(Codex, OpenCode, Qwen, Gemini, Kimi, OpenHands, deepagents)은 자식을 같은 프로세스 안에서 돌린다. 그 방식에만 있는 버그가 있다:
- Qwen #15083: 사용자 프롬프트가 자식의 문맥(도구 정책·모델)에서 실행됐다.
- Kimi #1768: 백그라운드 자식들이 CLI를 멈췄다.
- OpenHands #4537: 부모 잠금을 쥔 채 자식이 돌아 UI가 멈췄다.

aelix의 프로세스 모델은 이 버그들을 피하므로 유지한다. 대가는 두 가지다.
- 개입(3단계)에는 RPC 채널이 필요하다.
- 종료 대기에 상한과 SIGTERM→SIGKILL이 필요하다. Codex `close_agent`는 무한 대기(#25426)와 MCP 프로세스 누수(#35435)를 겪었다. aelix는 reaper가 이미 이것을 다룬다.

## 4. 공수 영향

| 단계 | 전 추정(세션) | 레퍼런스 반영 | 이유 |
|---|---|---|---|
| 0 | 1 | 1 | Qwen 결과 형식을 명세로 쓴다 |
| 1 | 2 | 1.5~2 | Kimi meta store(약 250줄)를 거의 그대로 옮긴다 |
| 2 | 5~7 | 4~5 | Kimi 알림 파이프라인, OpenCode Job 의미, deepagents notifier가 명세와 코드가 된다 |
| 3 | 4~6 | 4~5 | RPC 채널 작업이 대부분이라 레퍼런스 도움이 적다 |
| 4 | 2~3 | 2 | ACP/RPC 형태가 이미 검증돼 있다 |
| 합계 | 14~19 | 12~15 | |

크게 줄지는 않는다. 더 큰 이득은 견고함이다. 아래 §5의 실제 버그 목록을 테스트로 먼저 넣는다.

## 5. 미리 넣을 테스트 — 레퍼런스가 실제로 밟은 버그

| # | 단계 | 테스트 | 근거 |
|---|---|---|---|
| 1 | 0 | 모델이 위임 timeout을 줄 수 없다 | Kimi #2408 |
| 2 | 0 | 멈추거나 실패한 자식이 성공으로 보고되지 않는다. 상태는 한 곳에서 도출한다 | OH #3742, OpenCode #37852 계열, Codex #36799 |
| 3 | 0 | `agent_end` 없이 exit 0으로 끝난 자식은 성공이 아니다 | aelix 7/31 측정, OpenCode #48826 계열 |
| 4 | 0 | 텍스트가 없는 자식도 증거 digest를 돌려준다 | Codewhale |
| 5 | 1 | resume은 저장된 제한과 현재 부모 권한의 교집합으로 띄운다 | Codex #26718·#36433, OpenHands |
| 6 | 1 | 끊긴 도구 호출은 "결과 없음/오류"로 보정한다. "사용자 취소+성공"은 쓰지 않는다 | deepagents #5546 |
| 7 | 1 | resume할 수 없으면 이유를 말한다 | Qwen |
| 8 | 2 | 쉬는 부모는 깨어나서 결과를 받는다 | Codex #47762 |
| 9 | 2 | 바쁜 부모는 다음 경계에서 받는다. 쉴 때까지 미루지 않는다 | Qwen #12207 |
| 10 | 2 | 승인이나 질문을 기다리는 부모는 깨우지 않고, 깨우기 직전에 interrupt를 다시 확인한다 | deepagents, Codex #56330 |
| 11 | 2 | 완료 알림은 재시작을 거쳐도 정확히 한 번 간다 | OpenCode, Kimi |
| 12 | 2 | 종료 상태는 프로세스가 죽은 뒤에만 기록하고, 취소 보고는 정리 후에 한다 | Kimi #2615, deepagents #6234·#7133, OpenCode #54658 |
| 13 | 2 | `-p`는 모든 자식과 그 백그라운드 의무가 끝날 때까지 기다리고 exit code를 지킨다 | OpenCode #48361·#48826, Kimi |
| 14 | 2 | 재시작 후 고아가 된 자식은 lost로 표시되고 부모에게 알려진다 | Qwen #14273, OH #5074 |
| 15 | 2 | 끝난 자식은 동시성 슬롯을 차지하지 않는다 | Codex #22779 |
| 16 | 2 | 자식 생성 실패가 조용히 사라지지 않는다 | Qwen #14410 |
| 17 | 2 | 알림은 한 곳에서 만들고 자식 문자열은 escape한다 | Qwen #12038, deepagents |
| 18 | 2 | 짧은 간격의 wait 폴링을 막는다(최소 대기 시간) | Codex #39854 |
| 19 | 2 | 취소된 백그라운드 자식도 부분 결과를 전달한다 | Qwen `finalizeCancelled` |
| 20 | 3 | 정리 요청 후의 결과는 원래 중단 이유를 유지한다 | Gemini #22323 |
| 21 | 3 | 메시지는 id로 지정한 자식에게만 간다. 브로드캐스트하지 않는다 | Gemini |
| 22 | 4 | ACP 호스트에 자식 이벤트가 보이고, 승인 요청도 호스트에 도달한다 | Kimi·Gemini·deepagents 반례, deepagents #4033 |
| 23 | 4 | 이벤트 펌프가 승인 대기로 막히지 않는다 | Kimi wire |
