# Handoff — beta.3 배치: #393 · #375 · #389 · #354 · #392 — 2026-10-08

실행 기록의 정본은 `.omc/specs/batch-beta3-389-392-354-375-393-plan-2026-10-07.md`다. 라운드별 결과는 `.omc/specs/batch-beta3-r{1,2,3,4}-results.json`과 `batch-beta3-r5-392-results.json`에 있다. 이 배치 전의 핸드오프는 `.omc/specs/handoff-security-batch-2026-10-07.md`다.

## 기준점

- **main의 마지막 코드 커밋은 `a18bcc1d`**(#392)다.

  | 커밋 | 이슈 | 한 줄 | 브랜치 CI | main CI |
  | --- | --- | --- | --- | --- |
  | `61f03b67` | #393 | CI와 release의 uv를 0.11.14에서 0.12.23으로 올렸다(checksum 고정). #131의 win32 skip은 uv 버전 조건으로 좁혔다 | 37638504976 6/6 | 37642266961 6/6 |
  | `cd6f7a5c` | #375 | `OPENROUTER_BASE_URL`을 `/model`을 포함한 모든 OpenRouter 모델 사본에 적용한다. 다루는 함수는 하나다 | 37669012582 6/6 | 37672307436 6/6 |
  | `089ef69c` | #389 | 승인 대화상자(bash, write, edit, other)는 본문을 다 보기 전에는 Yes를 받지 않고, 생략 없이 그린다. 보이는 것과 실행되는 것이 같다 | 37706804489 6/6 | (스택) |
  | `28dbcaf4` | #354 | 위임된 자식이 부모 모델을 물려받을 때만 thinking 레벨도 물려받는다(pi 방향) | 37706806788 6/6 | 37709141645 6/6 |
  | `a18bcc1d` | #392 | 카탈로그에서 온 설치와 update는 aelix의 installer-cwd(sentinel 2개)에서 실행한다 | 37719719075 6/6 (첫 37718382719는 Windows 타입 게이트 FAIL) | 37721932346 6/6 |

- **전체 스위트**(main `a18bcc1d`, `.aelix/`가 없는 새 worktree, `AELIX_*`와 `VIRTUAL_ENV` 해제): **13667 passed, 44 skipped, 104 warnings, 532.73 s, exit 0**. 출력은 `.omc/probes/final-suite-a18bcc1d.out`
- **ADR**: 새로 만든 ADR은 없다. amendment를 단 ADR은 다음과 같다.
  - 0253: #389, §11. §8은 superseded.
  - 0243: #354.
  - 0251: #375, §11.
  - 0255: #392, §12·§15.
  - 0200: #392, §12.
  - **다음 빈 번호는 0256이다.**
- **원격 브랜치**: 머지한 뒤 바로 지웠다. 원격에는 `main`만 있다.
- **다른 세션**: 다른 세션이 이 레포에서 작업 중이다. 건드리지 않았다.
  - worktree `/Users/handochan/dev/aelix-extensions/.worktrees/aelix-host-extension-settings`(`feat/extension-settings`)
  - #402(ext git fragment pin, security)
  - #403, #404(확장 `/settings` 토글)
- **pi 스냅샷**: `/tmp/pi-27c7b6ff4`. macOS는 `/tmp`를 3일 뒤 정리한다. 필요하면 `git -C ~/dev/pi archive <sha>`로 다시 만든다.
- **메인 루프 라이브 확인 키트**
  - `.omc/probes/375-live/main-live/`: 실제 OpenRouter와 기록용 gateway
  - `.omc/probes/354-live/main-live/`: 실제 glm-5.3-flash 위임
  - `.omc/probes/389-live/main-live/`: TUI pty

## 바로 시작할 것

**beta.3 필수 보안 P1**: 셋 다 #131·#392 계열이고 `cli/extension_*.py`를 고친다. 그래서 같은 배치에 넣지 말고 하나씩 한다.

- **#405**: `update`와 `install_extension(CatalogSpec)`이 cwd에서 같은 이름의 항목을 경로로 본다. `classify_target`이 `CatalogSpec`을 경로로 보지 않게 한다.
- **#394**: pip `-P`. 직접 입력한 install과 remove가 cwd의 `pip/`를 실행한다.
- **#402**(다른 세션이 등록했다): git fragment에 든 40-hex. 먼저 다른 세션이 진행 중인지 확인한다.

**#399**(P1): 확장 `ctx.ui.select`/`confirm`의 긴 제목. `tui/context.py`를 고친다. #179(picker sanitise)와 파일이 겹친다. #389 r2에서 실패한 내용이 이슈 본문에 있으니 읽고 시작한다.

## 이후 순서 (beta.3 필수)

- `cli/entry.py`를 고치는 이슈들. 서로 겹치므로 한 번에 하나씩 한다: #376(세션 모델 복원), #289(`--offline`이 서브커맨드 앞에 올 때), #286(-p에 defaultThinkingLevel 적용).
- `ci.yml`을 고치는 이슈들. 차례로 한다: #192(3.13 레그), #279(install.sh e2e).
- Windows flake: #381, #388, #406(macOS에서 부하 때).
- 릴리즈 출구 조건은 맨 끝에 한다: #382, #383, #384(Windows는 오너), #385.

있으면 좋은 것: #379, #395, #397, #398, #365, #371, #380, #386, #390, #391, #168, #178, #179, #285, #278, #292, #293, #266. 백로그: #396, #400, #401.

## 오너 판단

### 이번 배치에서 받은 것

- #392: (a)로 한다. 카탈로그 설치만 중립 디렉터리에서 실행한다.
- 머지된 원격 브랜치는 삭제한다.
- 주간 리밋이 풀린 뒤 이어서 진행한다.
- #392: **"범위 좁혀 마무리"**. 사용자 env와 user/system 설정은 신뢰 경계 안으로 본다. `UV_CONFIG_FILE`이 절대 경로여야 한다는 규칙만 남긴다. verify 1회 뒤 Codex 없이 머지한다.

### 메인 루프가 정한 것

오너가 다르게 정할 수 있다.

- **#389 범위**: 확장 select와 confirm은 #399로 떼어 냈다. r2에서 `/model`과 `/settings` 회귀를 만들어서 되돌렸다.
- **#389**: 탭과 마지막 줄바꿈은 구분해서 그리지 않는다. 문서에 적었다.
- **#354**: pi 방향으로 정했다. 프로필이 model이나 provider를 정하면 thinking을 물려받지 않는다. off는 명시적인 off로 넘긴다.
- **#393**: uv를 0.12.23으로 올렸다. 0.11.33이 아니다. 사용자가 받는 버전과 맞추기 위해서다.
- **#375**: 우선순위는 env, 모델별 baseUrl, provider baseUrl 순이다(launch와 같다). provider 이름은 대소문자까지 정확히 `openrouter`일 때만 적용한다.
- **#392**
  - 모든 `update`를 installer-cwd에서 실행한다. 레코드에는 출처가 없다.
  - HOME의 `uv.toml`과 `pyproject.toml`은 더 이상 카탈로그 설치에 적용되지 않는다. 조직 고정은 `~/.config/uv/uv.toml`에 둔다.

## 이 레포에서 실제로 물린 규칙

- **OpenAI 콘텐츠 필터는 보안 주제 리뷰를 막는다.** #389와 #392의 Codex r1이 0건으로 끝났다. 위협을 쓰지 말고 규칙을 쓰는 중립 문구로 바꾸면 끝까지 돈다. 예: "the dialog must not accept Yes until every line was drawn". 그래도 막히면 독립 Claude 교차 리뷰로 대신한다(r4).
- **StructuredOutput의 JSON 파싱 실패**: #354 r2에서 커밋은 됐지만 결과가 유실됐다. JSON_NOTE에 "raw 개행·백슬래시·따옴표 금지, 4000자"를 넣은 뒤로는 다시 일어나지 않았다. 유실되면 커밋 메시지를 요약으로 쓰고 verify만 따로 돌린다.
- **주간 리밋**: 레인 8개가 모두 null로 끝났지만 반쯤 쓴 흔적은 없었다(worktree를 점검했다). 같은 run을 resume하면 된다.
- **GitHub 이슈 생성 500은 본문 내용 때문일 수 있다.** 짧은 본문으로 만든 뒤 PATCH로 본문을 넣으면 된다(#399).
- **로컬 타입 게이트는 Darwin 기준이다.** pyright on win32는 POSIX 전용 `os.*` 이름을 모른다. 런타임 플래그로 감싼 분기도 분석하므로 `sys.platform != "win32"` 가드가 필요하다(#392 CI 37718382719). 바뀐 제품 파일은 `pyright --pythonplatform Windows`로 확인한다.
- **`grep -c '^#'`이 0이면 exit 1이다.** `&&` 체인에 넣으면 그 뒤가 실행되지 않는다(#392 push).
- **라이브 키트의 `real.sh`가 `.env` 전체를 export하면 오너의 `OPENROUTER_BASE_URL`까지 따라간다.** 키 하나만 읽는 변형을 쓴다(`354-live/main-live/real.py`).
- **스택 머지가 효과적이었다.** #389 → #354를 한 줄로 쌓아 두 브랜치 CI를 동시에 돌리고, main을 맨 위로 한 번에 ff했다.

## 반증된 것 — 다시 믿지 말 것

- "sentinel `uv.toml` 하나면 uv 탐색이 멈춘다" → `[project]`가 있는 상위 pyproject가 이긴다. 주석만 든 `pyproject.toml`도 함께 둬야 한다.
- "상대 경로 env를 거부하면 더 안전하다" → 이름 붙은 `UV_DEFAULT_INDEX`와 `PIP_FIND_LINKS=~/x`가 회귀했다. 사용자 env는 신뢰 경계 안이다.
- "select와 confirm에도 hold를 넣으면 된다" → 짧은 제목에 옵션이 많은 picker가 Enter를 영영 받지 않는다(`/model` 80×22).
- "#354: thinking off이면 reasoning을 아예 생략한다" → `off` 키가 없으면 `effort: none`을 보낸다. pi도 같다(#397).
- "#375: Ctrl+P, `--models`, 재개 세션도 영향을 받는다" → Ctrl+P 모델 순환은 없다. `--models`는 구현되지 않았다. 재개는 원래 정상이었다.
