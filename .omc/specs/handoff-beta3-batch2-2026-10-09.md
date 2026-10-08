# Handoff — beta.3 배치 2: #178 · #405 · #379 · #376 · #192 · #399 — 2026-10-09

실행 기록의 정본은 `.omc/specs/batch-beta3-b2-405-399-376-192-379-178-plan-2026-10-08.md`다. 라운드별 결과는 `.omc/specs/batch-b2-<n>-r<k>-results.json`에 있다. 이 배치 전의 핸드오프는 `.omc/specs/handoff-beta3-batch-2026-10-08.md`다.

## 기준점

- **main의 마지막 코드 커밋은 `a0a9c328`**(#399)이다. 이 배치부터 CI는 8잡이다. 3.13 레그 2개가 #192에서 추가됐다.

  | 커밋 | 이슈 | 한 줄 | 브랜치 CI | main CI |
  | --- | --- | --- | --- | --- |
  | `a3e8141a` | #178 | 위임 자식의 tool, profile, state 문자열을 batch panel처럼 정화한다(`panel._flatten`이 `terminal_text`를 쓴다) | 6/6 | 37750163303 6/6 |
  | `8428e16c` | #405 | `update`와 `install_extension(CatalogSpec)`이 레코드와 카탈로그 source를 철자로 읽는다. cwd에 같은 이름의 파일이 있어도 그 파일을 설치하지 않는다 | 37750311455 6/6 | 37753379886 6/6 |
  | `31681046` | #379 | /login token refresh가 502, 429를 받거나 연결이 없으면 모델 502처럼 재시도한다(pi의 transient 집합) | 37770800697 6/6 | 37773904964 |
  | `e47f1101` | #376 | 모델 플래그 없이 세션을 다시 열면 그 세션의 모델로 돌아간다. 확실히 실행할 수 없는 모델의 프롬프트는 세션에 쓰지 않는다 | 37781488068 6/6 | 37785475249 6/6 |
  | `6b5ffc81` | #192 | CI에 3.13을 추가했다. 3.13에서만 드러난 결함 두 가지를 고쳤다: Windows `isabs`와 gh-119710의 `wait_released`. TLS strict 안내는 허용 목록으로 바꿨다 | 37795642184 **8/8** | 37799830100 8/8 |
  | `a0a9c328` | #399 | 확장의 select와 confirm이 긴 제목을 grapheme 단위로 줄바꿈한다. 다 보기 전에는 응답을 받지 않는다. aelix 자체 picker는 main과 같다 | 37806642579 attempt 2 8/8 | 37855731066 8/8 |

- **전체 스위트**: main `a0a9c328`에서 돌렸다. 조건은 `.aelix/`가 없는 새 worktree, `AELIX_*`와 `VIRTUAL_ENV` 해제였다.
  - 결과: **15198 passed, 45 skipped, 104 warnings, 1091.63 s, exit 0**
  - 출력: `.omc/probes/final-suite-a0a9c328.out`
  - 같은 시간에 CI hang 조사 워크플로우가 로컬에서 돌고 있었다. 그래서 시간이 평소보다 길다.
- **ADR**: 새로 만든 ADR은 없다. amendment를 단 ADR은 다음과 같다.
  - 0199 §(l): #178
  - 0255 §16: #405
  - 0251 §12: #379
  - 0239: #376
  - 0238, 0241: #192
  - 0253 §11.3: #399
  - 0256은 다른 세션이 #404에 썼다. **다음 빈 번호는 0257이다.**
- **원격 브랜치**: 이 배치의 브랜치는 모두 지웠다. 원격에 남은 `fix/197`, `fix/318`, `fix/320`, `fix/417`, `feat/287`, `feat/extension-settings`, `release/memory-settings-compatibility`, `chore/memory-compatibility-release-feed`는 **다른 세션**의 것이다(`~/dev/aelix-ai-worktrees/codex-*`). 건드리지 않았다.
- **pi 스냅샷**: `/tmp/pi-1cedd3272`. macOS는 `/tmp`를 3일 뒤 정리한다.
- **라이브 키트**
  - `.omc/probes/376-live/main-live/`: 실제 openrouter 세션 재개
  - `.omc/probes/399-live/main-live/`: 확장 대화상자와 picker를 pty와 pyte로 읽고, fix와 main을 비교한다
  - `.omc/probes/192-live/r6verify/`: Linux docker 3.13.15와 3.14.7
  - `.omc/probes/ci-hang-executor-thread/`: CI hang 원인, 재현 plugin, 패치 3개

## 오너가 할 일

- **main의 required status checks에 다음 두 개를 추가한다**: `lint + test (ubuntu-latest, py3.13)`, `lint + test (windows-latest, py3.13)`. 지금은 이 둘이 빨개도 머지가 막히지 않는다.

## 바로 시작할 것

- **#402**(P1 security, 오너가 beta.3로 정했다): git spec의 `#fragment` 끝에 붙은 40-hex를 고정 커밋으로 읽는 문제다.
  - 다른 세션이 등록한 이슈다. 시작하기 전에 그 세션이 진행 중인지 확인한다.
  - `cli/extension_*.py`를 고친다. 그래서 #394와 같은 배치에 넣지 않는다.
- **#428**(P1): completion이 진행 중일 때 종료하면 aelix가 끝나지 않는다. CI가 6 h 멈춘 원인이다.
  - 수정안은 `.omc/probes/ci-hang-executor-thread/patches/fixA-offloop-completer.patch`다. 검증했고 적용은 하지 않았다.
  - 회귀 테스트를 추가한다. 독립 점검의 `fresh-check-2026-10-09/minimal_double_cancel.py` 모양을 쓴다.
  - 고치는 파일은 `tui/completion.py`와 `shell.py`다.
- **#429**(P1): CI에 `timeout-minutes`와 `faulthandler_timeout` + `faulthandler_exit_on_timeout`이 없다(`patches/ci-guard.patch`).
  - `ci.yml`을 고친다. 그래서 #279와는 차례로 한다.
  - **#429가 머지되기 전까지는 CI를 볼 때 hang guard를 쓴다**: 50분이 넘은 잡이 있으면 알린다(이 배치의 #399 main CI 감시 명령).

같은 배치에 넣을 후보는 #289(`cli/entry.py`)와 #179(`tui/context.py` picker)다. 다만 #179는 #424와 파일이 겹친다. #391도 후보다. 배치를 짜기 전에 파일이 겹치는지 다시 확인한다.

## 이후 순서

- `cli/entry.py`를 고치는 것들은 하나씩 한다: #289, #286.
- `ci.yml`을 고치는 것들은 차례로 한다: #429, #279.
- flake: #381, #388, #406. #406은 원인을 측정했다. marker를 읽을 때의 경합이고 수정은 간단하다.
- 이 배치의 후속: #424(확장 select의 ⋮ 표시), #407(/agents run 정화), #416(`has_configured_auth`를 pi와 맞추기), #412, #413, #415(model_change 기록), #408, #409, #410, #411, #414.
- 릴리즈 출구 조건은 맨 끝에 한다: #382, #383, #384(Windows는 오너), #385.

## 오너 판단

### 이번 배치에서 받은 것

- #402를 beta.3 P1로 올린다.
- #192는 3.13 레그만 추가하고 pyproject에 상한을 두지 않는다(#278에 맡긴다).
- 배치를 권고대로 한다(#405, #399, #376, #192, #379, #178).
- **"범위 좁혀 마무리"를 세 번 정했다.**
  - #405
  - #376: "키 검사 빼고 마무리". 자격 증명 검사를 빼고, 후속은 #416으로 넘긴다.
  - #399: oversized grapheme cluster는 알려진 제한으로 둔다. Codex는 다시 돌리지 않는다.

### 메인 루프가 정한 것

오너가 다르게 정할 수 있다.

- **#192**
  - 타임아웃된 hook이 백그라운드로 띄운 helper가 출력을 계속 쥐고 있으면, **모든 인터프리터에서** 1.0 s grace 뒤에 kill한다. 예전에는 helper가 살아남았다.
  - Windows 절대 경로 규칙(드라이브 또는 UNC)을 모든 인터프리터에서 3.13과 같게 맞췄다.
- **#376**: cloudflare에 `CLOUDFLARE_ACCOUNT_ID`가 없으면 거부한다. base URL에 placeholder가 남기 때문이다.
- **#399**: 하이라이트 아래에 여러 줄 옵션이 있으면 `⋮`로 그린다. main과 다르게 그리는 것인데, 고치지 않고 #424로 분리했다. 표시만 다르고 응답은 정상이다.
- **#428, #429는 P1**로 정했다. 제품이 종료에서 멈추고 CI가 6 h를 쓰는 문제이고, 수정은 간단하다.

## 이 레포에서 실제로 물린 규칙

- **CI에 시간 상한이 없다(#429).** #399의 ubuntu 3.13 잡이 `15210 passed`를 출력한 뒤 5h51m 동안 멈췄다. `gh run watch`는 아무 신호도 주지 않았고, 오너가 "이상하게 오래 걸린다"고 해서 알았다. 이제 감시 명령에 50분 guard를 넣는다.
  - 멈춘 잡의 로그는 취소한 뒤에야 받을 수 있다.
  - 취소된 잡은 `gh run rerun --failed`로 다시 돌린다.
- **gh-119710**: `Process.wait()`가 파이프가 닫힐 때가 아니라 자식이 종료될 때 끝난다. 이 변경이 들어간 버전은 **3.13.15와 3.14.7**이다. 3.14.5와 3.14.6에는 없다.
  - CI의 ubuntu 3.13은 3.13.16이라 이 변경이 들어 있고, windows는 3.13.15다.
  - 프로세스 트리의 종료를 기다릴 때는 `aelix_ai.utils._process_tree.wait_released()`를 쓴다.
- **3.13 Windows의 `ntpath.isabs('/x')`는 False다.** 절대 경로 판정은 `_is_absolute_path`(드라이브 또는 UNC)로 한다.
- **"범위 좁혀 마무리" 뒤에는 가벼운 마무리 워크플로우를 쓴다.** 구성은 amend 1개 + 독립 점검 1개다.
  - 점검 항목: range-diff로 제품 코드 변화가 없는지 본다. 새 행이 겨냥한 뮤턴트에서 빨갛게 되는지 sabotage로 확인한다. gates를 돈다.
  - 15–40분이면 끝난다. 전체 fix-round는 1.5–2.5 h다(#192 r7, #399 r5).
- **main이 라운드 도중에 움직이면 rebase만 하는 마무리로 충분하다.** 라운드 하나가 도는 동안 main이 두 번 움직였다(#376, #192). 독립 range-diff로 "충돌 해소만 했다"는 것을 확인한다.
- **레인에서 한 번 실패한 테스트를 방금 머지한 커밋 탓으로 돌리기 전에** 머지 전후를 부하 아래에서 비교한다(#406: 2560 × 2, p=0.31).
- **flake를 새 이슈로 올리기 전에 같은 테스트 이름으로 기존 이슈를 검색한다.** #427은 #406의 중복이어서 닫았다.
- **메인 루프의 TUI 확인은 제품 코드가 바뀌지 않는 마무리 라운드와 병렬로 돌릴 수 있다.** 직전 커밋으로 별도 worktree를 만들어 확인하고, 마무리 range-diff가 "제품 코드 변화 없음"인지만 대조한다(#399).

## 반증된 것 — 다시 믿지 말 것

- "#399의 CI hang은 #399나 #192 때문이다" → 아니다. #39(2026-07-12)부터 있던 `ThreadedCompleter` 경합이다. 두 번째 cancel이 오면 `q.get` worker가 남는다(#428).
- "ubuntu 3.11에서 스위트 중간에 멈춘 3건은 다른 결함이다" → 같은 결함일 가능성이 높다. 3.11의 `Runner.close`는 executor shutdown에 timeout이 없어서 테스트 안에서 멈춘다. 3.12 이상은 300 s 뒤 인터프리터 종료에서 멈춘다.
- "#406 flake는 #192의 reaper(`wait_released`) 회귀다" → 아니다. marker를 open과 write 사이에 읽는 경합이고, #220부터 있었다. 실패는 cancel보다 먼저 일어난다.
- "gh-119710은 3.14에 들어 있다" → 3.14.7부터다.
- "`wait_released`는 3.13.15 이전의 의미를 되돌린다" → 아니다. 더 엄격하다. 출력을 쥔 helper를 모든 인터프리터에서 kill한다(#192 r5 verify).
- "#376은 pi의 `hasConfiguredAuth`처럼 키를 검사하면 된다" → 헤더, `CUSTOM_HEADERS`, `AUTH_TOKEN`, ADC 인증이 회귀했다. 키 검사는 #416에서 pi와 맞춘 뒤에 한다.
- "#399의 최소 레이아웃에는 하이라이트된 옵션 전체가 들어가야 한다" → 짧은 질문을 영영 막는다(Codex r3). 첫 행만 들어가면 된다.
