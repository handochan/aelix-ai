# Handoff — pi 방향 배치: #362 · #350 · #367 · #370 · #369 · #363 · #368 — 2026-10-06

실행 기록 정본은 `.omc/specs/batch-362-350-pi-direction-plan-2026-10-02.md`이다. 준비, 레인별 라운드, Codex, CI, 머지 순서가 시간순으로 있다. 조사 결과는 `.omc/specs/next-issues-research-2026-10-06.json`이다(배열: [0]=#363, [1]=#368, [2]=#370, [3]=#369). 세션은 2026-10-02에 시작했고, 그사이 압축 두 번과 재부팅 한 번이 있었다.

## 기준점

- **main의 마지막 코드 커밋 = `986f5916`**. 이 세션이 올린 커밋은 다음과 같다.

  | 커밋 | 이슈 | 한 줄 | 브랜치 CI | main CI |
  | --- | --- | --- | --- | --- |
  | `21db8c8a` | #350 | 백그라운드 helper의 종료 뒤 출력 꼬리는 best-effort다(pi와 같음). 문서만 | 36975608165 6/6 | — |
  | `5dee21d1` | #362 | `--model`이 pi의 해석 순서를 따른다. 보완 1(`.env` 자격은 경로를 정하지 못한다), 보완 2(카탈로그 밖 id는 자기 OR 키로만) | 37032653260 6/6 | 37035221427 6/6 |
  | `62e2238b` | #367 | session_start에서 등록된 프로바이더로는 시작 시 전환하지 않고 pi처럼 거부한다. 턴 게이트, `LateRouteHold.apply`, landing 규칙 | 37356882668 6/6 | 37359718181 6/6 |
  | `a7435b9f` | #370 | `--api-key`와 어느 프로바이더도 받지 않는 접두는 pi처럼 not found로 끝난다. typed key는 OpenRouter로 가지 않는다 | 37375237207 6/6 | 37377731047 6/6 |
  | `547099f3` | #369 | 신뢰하지 않은 프로젝트의 `.aelix/settings.json`은 읽지도 쓰지도 않는다(pi `89a92207f`). settings.json도 trust 리소스다 | 37384593826 6/6 | 37386874825 6/6 |
  | `9456a57c` | #363 | 키 순서 (c)(runtime → auth.json → models.json apiKey → env). stored가 provider를 소유한다. 시작 시 `/model`과 같은 합성 모델을 쓴다 | 37391508061 6/6 | 37393577331 6/6 |
  | `986f5916` | #368 | `--provider`만 있고 `--model`이 없으면 모든 모드에서 시작 전에 pi 문구로 exit 1 | 37396141277 6/6 | 37398119003 6/6 (attempt 2; attempt 1의 windows py3.12 실패는 플레이크 #381) |

- 전체 스위트(main `986f5916`, `.aelix/`가 없는 새 워크트리, `uv sync --all-packages`, `AELIX_*` 해제): **12124 passed, 23 skipped, 104 warnings, 464.52 s, exit 0 (실패 0)**.
- **ADR**: 0250(#362, 이후 #367, #370, #369, #368이 amend), 0251(#363), 0252(#369)을 썼다. **다음 빈 번호는 0253이다.** ADR-0249, 0140, 0149, 0178에는 dated note를 달았다.
- **pi 참조**: 스냅샷은 `/tmp/pi-b223082bb`(origin/main `b223082bb`, 2026-10-06)와 `/tmp/pi-88ff80b98`이다. `/tmp`는 3일 뒤나 재부팅 때 지워지므로, 다시 만들려면 `git -C ~/dev/pi fetch && git -C ~/dev/pi archive <sha> packages scripts | tar -x -C /tmp/pi-<sha>`.
- **키트(지속 사본)**: `.omc/probes/{362,367,368,369,370,363}-live/`. 레인별 impl, verify, codex, rebase 출력과 메인 루프의 TUI·라이브 드라이버(`main/`, `main-tui/`)가 있다. pty 드라이버는 `.omc/probes/367-live/main-tui/mydrive.py`(pyte HistoryScreen)이다.
- **원격 브랜치 정리(오너 몫, 분류기가 막는다)**: 실행 전에 `git ls-remote --heads origin`으로 병합된 브랜치인지 확인한다.
  `! git push origin --delete fix/341-drain-bounds-from-the-drains-own-record fix/344-a-registry-prefix-beats-openrouter-from-env fix/350-helper-tail-after-exit-is-best-effort fix/351-rpc-terminator-lets-the-cancel-through fix/362-model-routing-follows-pi fix/363-own-key-before-env-and-composed-launch fix/367-late-provider-refused fix/369-project-settings-follow-trust fix/370-api-key-unknown-prefix-is-not-found fix/368-provider-requires-model`

## 바로 시작할 것

**#374(P1)부터 한다.** 키 없는 커스텀 `anthropic-messages` 프로바이더에서 Anthropic SDK가 `ANTHROPIC_API_KEY`를 스스로 읽어 커스텀 게이트웨이로 보낸다. 사용자 자격이 제3자 host로 가는 누출이다.

- pi(`anthropic-messages.ts` @ b223082bb)는 키도 헤더도 없으면 `assertRequestAuth`에서 `No API key for provider`를 낸다(:325). SDK에는 `apiKey: apiKey ?? null`을 넘긴다(:1069).
- aelix `_anthropic_client.py`는 `api_key=None`일 때 인자를 빼서 SDK가 env를 읽게 둔다.
- 같은 레인에서 Google SDK와 Vertex ADC 등 다른 어댑터가 provider 이름과 상관없이 env를 읽는지도 훑는다.

그다음은 이전 핸드오프(2026-10-01)의 "바로 시작할 것"으로 돌아간다. beta.3 보안 행의 **#188**(P0, MCP·skills 도구가 승인과 PLAN을 우회)과 **#131**(P0, 카탈로그 상대 source 해석)이다.

## 이후 순서

- **#365**: 확장이 내장 이름으로 등록하는 경우다. #363이 남긴 확장 `api_key` 순서와 headers 병합이 여기서 풀린다(#365 코멘트).
- **#371**: `--mode rpc`에 model registry가 없다. hold된 RPC 세션은 빠져나갈 수 없고, 미해결 경로의 RPC launch는 조용히 시작한다.
- **#379**: OAuth refresh가 502나 네트워크 오류로 실패해도 재시도하지 않는다. **오너가 pi처럼 재시도하기로 결정했다**(2026-10-06).
- **#375**: `OPENROUTER_BASE_URL`이 launch에만 적용된다. `openrouter/auto` id 이동도 같은 이슈에 있다.
- **#376**: 모델 플래그 없는 `--continue` 세션에서 compaction이 실패한다.
- 문구와 UX:
  - #372: fire-and-forget trigger의 거부 사유.
  - #373: 새 세션인데 "Resumed session"이 표시된다.
  - #377: `/model` 피커가 키 출처를 잘못 보여 준다.
  - #378: hold된 not-found placeholder의 턴 거부 문구.
  - #380: 신뢰 프롬프트 줄바꿈.
- 테스트 위생:
  - **#381**: windows py3.12에서 `test_every_byte_is_delivered_under_a_loaded_loop`가 20초 timeout으로 한 번 실패했다(재실행은 통과). #260·#261과 증상이 다르다.
  - #352
  - #366: 실제 `~/.aelix/sessions`에 쓴다. 오너가 `~/.aelix/sessions`를 정리해야 한다.
  - #364: 인용 게이트

## 오너 판단

### 원칙 (2026-10-02, 받은 것)

- **"정해야 할 것은 모두 pi 방향"**, 보완 2개를 더한다.
  - 보완 1: `.env` 자격은 경로를 정하지 못한다.
  - 보완 2: 카탈로그 밖 id는 사용자 자신의 OR 키로만 OpenRouter로 보낸다.
- 대소문자 규칙은 지금대로 둔다.

### 메인 루프가 그 원칙으로 정한 것 (다르게 원하시면 되돌린다)

- **#367**:
  - 늦게 등록된 프로바이더는 pending launch에서만 거부한다. 등록된 경로로 해석된 launch는 그 경로에 남는다(pi).
  - settings 쌍이 늦은 프로바이더를 가리키면 거부한다. pi는 첫 사용 가능 모델로 대체한다.
  - rebuild는 launch 입력을 다시 해석해 hold로 돌아간다. pi는 세션 모델을 유지한다.
  - 아직 hold되지 않은 rebuild의 session_start에는 게이트를 걸지 않는다.
  - 거부된 턴의 메시지는 대화에 남아 다음 프롬프트와 함께 나간다.
  - 지연 없이 spawn된 task의 `set_model`과 trigger는 hold 적용 중이면 막힌다(엄격).
  - "늦게 등록"은 세션 시작 창 전체를 뜻한다. session_start 핸들러뿐 아니라 그 핸들러가 건 턴의 input/before_agent_start 핸들러와 task도 포함한다. pi는 어떤 핸들러보다 먼저 모델을 확정하기 때문이다.
- **#370**:
  - `--api-key` 아래에서는 guard 2를 쓰지 않는다.
  - 힌트는 세그먼트가 비어 있지 않은 모든 slash 문자열에 붙인다.
  - 카탈로그에 있는 OR id(195개)는 pi처럼 typed key와 함께 OpenRouter로 간다.
  - `/agents use`는 launch 입력을 다시 해석할 때만 typed key를 본다.
  - 전역 settings나 **신뢰한** 프로젝트 settings의 `defaultProvider=openrouter`는 typed key를 OpenRouter로 보낸다(사용자의 선택으로 본 잔여, ADR-0250 §2.7).
- **#369**:
  - settings.json을 trust 리소스로 넣었다.
  - post-`/login` 선택은 전역 settings만 읽는다.
  - extension sources는 전역 settings만 읽는다.
  - `/trust` 뒤에는 재시작을 안내하고, "this session only" 선택지는 없다(pi).
  - 신뢰한 프로젝트의 쌍에 대한 보강은 하지 않았다.
  - `--list-models`는 비대화형으로 판단한다.
- **#363**:
  - 3단계는 models.json `apiKey`만이다. 확장 키는 #365 전까지 env 뒤에 둔다.
  - stored가 모든 경우를 소유한다. pi 실측으로 보면 빈 stored `api_key`에서 **pi는 env를 읽어** 재지정 게이트웨이로 벤더 키를 보낸다. aelix는 더 엄격하다.
  - config 문법은 bare=env로 둔다(divergence).
  - **pi는 refresh가 502일 때 재시도하고 aelix는 하지 않는다**(#379).
- **#368**:
  - `--provider`만 주면 **interactive와 RPC를 포함한 모든 모드에서 exit 1**이다. argv 사용법 오류이므로 §2.4의 hold 대상이 아니다.
  - 프로필이 없으면 trust 질문보다 먼저 끝난다(pi보다 엄격하다).
  - 키 없는 `--provider openrouter`와 `OPENROUTER_DEFAULT_MODEL` 조합은 a7435b9f처럼 interactive와 rpc가 시작한다.
- **ADR-0250 §7**의 #362 결정들: 보완 2 확장, step 3b, `/model openrouter/` 엄격성, defaultProvider 규칙.

### 오너가 배치 뒤에 정한 것 (2026-10-06)

- **빈 stored `api_key`의 엄격한 실패는 유지한다**(ADR-0251 §4·§7). pi처럼 env로 넘어가면 재지정 게이트웨이로 벤더 키가 간다.
- **일시적 원인(5xx·네트워크)으로 실패한 OAuth refresh는 pi처럼 재시도한다.** #379에서 구현한다. 401·403과 `StoredCredentialError`는 재시도하지 않고, 재시도하는 동안에도 env로 넘어가지 않는다.
- **#367의 pi와 다른 두 가지는 그대로 둔다**(ADR-0250 §7 item 15):
  - settings 쌍이 늦은 프로바이더를 가리키면 거부한다.
  - 재빌드는 launch 입력을 다시 해석하고 hold로 돌아간다.

### 오너 환경에 직접 닿는 변화

- 오너의 anthropic과 github-copilot OAuth는 만료돼 있다(2026-10-06 확인). **#363 뒤로 anthropic 프로바이더 요청은 refresh를 시도하고, 실패하면 `/login`을 요구한다.** 예전처럼 `.env`의 `ANTHROPIC_API_KEY`로 조용히 내려가지 않는다.
- openai-codex OAuth는 유효하다. 실측에서 `pong`이었고, `~/.aelix` 쓰기는 0이었다.
- 오너 레포의 untracked `.aelix/extensions/hello.py`가 로드 오류를 낸다(`register_tool` import). #352와 관련 있다.

## 이 레포에서 실제로 물린 규칙

- **멈춤 기준(#367 9라운드)**: aelix가 보장하는 것은 자기 결정과 자기 문구다. 판정 이후 확장의 행동은 비차단이다(§6에 적는다). Codex가 문구와 mutant만 찾기 시작하면 수렴한 것이다. 행으로 고치고, 새 맥락 verify를 한 번 돌리고 멈춘다.
- **StructuredOutput 파싱 실패**: 끝난 레인의 결과가 사라진다(#363 r1). 커밋은 남는다. 레인 프롬프트에 "문자열 6000자 이하, 긴 증거는 파일로"를 붙인다(메모리 `workflow-agents-opus-ceiling-and-529`).
- **직렬 머지의 rebase 비용**: 같은 문서(ADR-0250, README 행, CHANGELOG, 가이드, `citations.lock.json`)를 만지는 레인은 하나 머지할 때마다 나머지를 rebase해야 한다. #369는 두 번, #363은 한 번 했다. rebase마다 range-diff verify를 했다. 다음 머지될 커밋 위에 미리 rebase하면(#368을 `9456a57c` 위로) 한 번을 아낄 수 있다.
- **rebase나 amend가 메시지의 `#` 줄을 남긴다**: rebase 레인과 메인 루프가 쓴 증거 줄이 `#363 files…`, `#370's notes…`처럼 `#`로 시작했다. 커밋 전에 `grep -c '^#'`가 0인지 확인한다.
- **`AELIX_CODING_AGENT_DIR`/`AELIX_SETTINGS_PATH`를 export한 채로 전체 스위트를 돌리면** `tests/cli/test_entry_router.py` 3행이 실패한다. 스위트 전에 해제한다.
- **GitHub push 500**: 30초 뒤 재시도에 성공했다.
- **재부팅이 `/tmp`를 통째로 지운다**(10-04): 워크트리, 키트, pi 스냅샷이 모두 사라졌다. 커밋은 main 저장소 ref에 살아 있어 `worktree prune && add`로 복구했다. 라운드마다 키트를 `.omc/probes/`로 복사한다.

## 반증된 것 — 다시 믿지 말 것

- "#363의 키 순서는 pi 06-22 병합에서 바뀌었다" → 바꾼 것은 **`9993c9690`(2026-07-14)**이다.
- "pi는 빈 stored `api_key`에 키를 주지 않는다" → pi의 `envApiKeyAuth`는 **env를 읽는다**.
- "pi는 OAuth refresh 실패를 재시도하지 않는다" → `lazyStream`과 `isRetryableAssistantError`가 **502와 fetch failed는 재시도한다**.
- "#368: aelix는 이미 exit 1이다(메시지만 다르다)" → print/json만 그렇다. interactive와 RPC는 시작했다.
- "이른 검사는 확장이 추가할 레지스트리 상태를 읽지 않는다"(#368 r1) → 확장이 주는 OR 키를 못 봐서 rung이 깨졌다. 판정은 argv만 보고, 자격은 step 0만 본다.
- "`/agents use`가 typed key를 모든 해석에 쓰면 일관된다"(#370 r1) → 프로필 자신의 `model:`까지 거부해 범위를 넘었다.
- "late = session_start 핸들러가 등록한 것" → 실제 창은 빌드 끝부터 aelix의 검사까지다. fire-and-forget trigger의 핸들러와 지연 없는 task가 emit 뒤에 등록해도 late다.
