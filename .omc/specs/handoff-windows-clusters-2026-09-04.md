# 다음 세션 핸드오프 — windows 잔여 141건, 이슈 12건 + #109 코멘트로 전부 추적 (2026-09-04)

`handoff-204-premise-2026-09-03.md` §2("바로 시작할 것")를 끝냈다. 코드 변경은 없다 — 이 세션은 분류·검증·게시만 했다.

## 0. 기준점

| | |
| --- | --- |
| `main` | 이 문서 커밋 직전 `2d14a64` (origin/main은 `9cd7e53` — **push 안 했다**) |
| 분석한 CI 런 | windows-latest py3.11 · run **33755783029** · job 100649739441 · HEAD `9cd7e53` · **141 failed / 9181 passed / 61 skipped** |
| 로컬 스위트 | `9cd7e53`에서 `1 failed / 9370 passed`(darwin `/tmp` realpath 1건). 이 세션은 코드를 안 건드려 재실행 안 함 |
| 보드 | #205–#216 전부 project 1 **Backlog**에 등록됨 |

이전 핸드오프의 "143건"은 이 런에서 **141건**이었다(2건 차이는 추적하지 않았다 — flaky일 수 있다).

## 1. 클러스터 → 이슈 (141건 = 각 정확히 1회, 스크립트로 검증)

| 이슈 | 건수 | fix_type | 요지 |
| --- | ---: | --- | --- |
| **#205** `[extensions]` | 17 | **product** | `_file_url_to_path`가 `Path(unquote(parsed.path))` → `file:///C:/…`의 드라이브 문자 상실. discover 15 + catalog 2. 수정: `urllib.request.url2pathname` |
| **#206** `[tui]` | 17 | product(cosmetic)+test | rich `legacy_windows` → ROUNDED→SQUARE 치환(`╮` 탐지 7건). **width −1은 테스트 헬퍼(`height=24`)의 아티팩트**, 제품 Console은 폭을 잃지 않음(9건). +`NoConsoleScreenBufferError` 1건 |
| **#207** `[rpc]` | 5 | product+test | `RpcClient.stop()`에 win32 분기 없음(terminate=TerminateProcess라 grace 구간이 없음). 수정은 **#202와 같은 줄** — 따로 설계 금지. 나머지 POSIX 원시연산(fork, SIG_IGN, connect_read_pipe) |
| **#208** `[cli]` | 2 | product | `{target!r}` 백슬래시 2배; `sorted(Path)` case-fold로 README가 help 맨 앞을 벗어남 |
| **#209** `[test]` | 17 | test-port | 자식 env dict에 `SystemRoot` 없음 → `import asyncio`에서 WinError 10106. 5파일. **가장 싼 승리** |
| **#210** `[test]` | 17 | test-port | POSIX 구분자·드라이브 없는 경로·OS 리터럴을 기대값에 박음(5 하위 패턴) |
| **#211** `[test]` | 14 | test-classify | NTFS mode bit 0o600/0o700 단언 — 구조적 영구 빨강. **오너 결정 필요**(skip vs 재작성) |
| **#212** `[test]` | 11 | test-port | 테스트가 POSIX 셸 문법 가정; win32 arm 픽스처가 확장자 없는 `pwsh`라 `shutil.which` PATHEXT에 안 걸림; subprocess_hooks가 cmd.exe로 감 |
| **#213** `[test]` | 6 | test-port | `write_text()`에 `encoding=`/`newline=` 없음 → cp1252/CRLF |
| **#214** `[test]` | 5 | test-port | f-string으로 백슬래시 경로를 TOML basic string에 삽입 → `\U` 이스케이프 오류 |
| **#215** `[test]` | 2 | test-port | `-signal.SIGKILL` 단언; 제품 `_kill_signal()`은 이미 분기함(`b768c60`) |
| **#216** `[test]` | 1 | test-port | `test_auto_classifier_allow_ask_deny`가 `SHELL=/bin/bash` 고정 → Windows에 없어 PowerShell 폴백 → #204 강등. **#204의 결과이지 새 결함 아님** |
| **#109 코멘트** | 27 | test-port | (a) `#!/bin/sh` MCP 런처 14 · (c) `cwd="/tmp"` spawn 12 · (d) PATH `.split(":")` 1. 제품 결함 3건 반증 기록도 여기 |

## 2. 바로 시작할 것

**#209 (SystemRoot, 17건)** — 다섯 env dict에 `SystemRoot`(그리고 필요하면 `SYSTEMDRIVE`/`TEMP`) 추가.
제품 코드(`build_child_env`, `RpcClient._build_env`)는 `os.environ`을 상속하므로 **테스트만** 고친다.
이슈에 ⚠️ 추정이 하나 있다 — SystemRoot만으로 충분한지는 windows 러너에서 돌려봐야 안다. 17건 중 16건이
10106이고 1건(`test_the_relocated_child_would_execute_it_without_the_flag`)은 다른 층일 수 있다.

**그 다음 #205** — 제품 결함 중 가장 큼. `url2pathname` 한 줄 + 양 OS에서 도는 회귀 테스트
(`file:///C:/x`, `file:///a/b%20c`). `extension_install.py:2892`의 `file://{target.resolve()}` 출력도 같은 이슈.

## 3. 이후 순서

#208(한 줄 둘) → #213 → #214 → #215 → #216 → #210 → #212 → #211(오너 결정 먼저) → #206 → #207(#202와 함께).
이전 순서(`#108 F-4 → #105 → #202 → #107 후반 → #201 → #46`)는 그대로 유효하고, #204 트랙은 병렬.

## 4. 이 레포에서 이번에 물린 것

1. **Opus 529는 workflow 전체를 조용히 비운다.** `agent()`는 API 에러에 null을 돌려주고 `parallel()`은 그걸
   그대로 담는다. 첫 런은 13/13 null → Merge에서 TypeError. 재시도+sonnet 폴백 래퍼(`robust()`)와 null 가드 없이
   Workflow를 돌리지 말 것. 두 번째 런에서도 Classify/Merge/Verify **전부 sonnet 폴백**이었고 Draft 일부·Critic·Revise만
   opus였다 — 그래서 Critic이 잡은 것이 많았다(아래 2).
2. **sonnet 분류 초안의 오류 유형 두 가지.** (a) 거짓 제품 결함 — "rich가 선언된 width에서 1을 뺀다"(실측:
   `Console(width=40, legacy_windows=True).width == 40`; 빼는 건 height까지 준 테스트 헬퍼). (b) 조작된 대조군 —
   다른 테스트의 줄(`test_rpc_channel.py:239`)을 증거로 인용. 둘 다 opus critic이 라이브러리 소스를 읽어 반박했다.
   **모델을 내려 쓸수록 verify 단계를 opus로 유지할 것.**
3. **Codex는 이번엔 한도 초과.** `codex exec`가 14개 초안+141줄 프롬프트(1932줄)에 "ultra" 추론으로 201k 토큰을 쓰고
   답 없이 `usage limit` (05:38 리셋). 큰 프롬프트는 초안별로 쪼갤 것. 이 게시물 13건은 **Codex 교차 리뷰 없이**
   Claude 2패스(critic → finalize → critic)만 거쳤다. 오너가 원하면 `/tmp/win-issues-codex-prompt.md`로 재실행 가능.
4. **이슈 본문에 `/tmp/…` 경로를 남기지 말 것.** 4개 초안의 검증 명령이 `/tmp/win-failed.txt`를 grep했다 — 독자에게는
   없는 파일. `gh api …/jobs/$JID/logs`로 만드는 명령을 앞에 붙였다.
5. **pytest 헤더 정규식.** 긴 테스트 이름은 `__ name ___`처럼 밑줄이 2개까지 줄어든다. `^_{3,}`는 블록을 놓친다 —
   `^_{2,}\s(.+?)\s_{2,}$`. 그리고 FAILED id 하나에 공백이 있다(`[git+file:///a/b%20c-git-/a/b c]`) — `awk '{print $2}'`가 자른다.
6. **`.venv`의 rich는 14.3.4, prompt_toolkit 3.0.52.** 라이브러리 동작 주장은 그 소스 줄을 인용했다(`console.py:1011-1012`,
   `box.py:405`). 버전이 바뀌면 줄이 움직인다.

## 5. 반증된 것 — 다시 믿지 말 것

- ❌ **"잔여 AssertionError에 제품 결함 0건."** (`handoff-windows-leg-2026-09-03b.md` §2) — 3건 확인(#205, #208 둘),
  네 번째 제품 표면 #207. 이전 핸드오프에 정정 노트를 붙였다.
- ❌ "rich `legacy_windows`가 폭을 1 줄인다." — 선언 width만 넘기면 안 줄인다. 테스트 헬퍼 아티팩트.
- ❌ "`test_auto_classifier_allow_ask_deny` 실패는 #204 강등 자체." — 직접 원인은 테스트가 박은 `/bin/bash`가 Windows에
  없어서. #204가 고쳐져도 이 테스트는 별도 수정 필요(#216).
- ❌ "`Remove-Item` → ALLOW" — 어제 반증됨, 오늘 이슈 12건 어디에도 다시 등장하지 않음을 critic이 확인.

## 6. 변하지 않은 금지 사항

🔴 PUBLIC 저장소. `.omc/specs/recovery-report-dead-session-91.md`와 `docs/assets/*` 10건은 여전히 **의도적으로
미커밋**. `git add -A` 금지. 이 세션은 `/tmp/win-issues/*`(초안)와 `/tmp/win*.txt|json`(증거)을 레포에 넣지 않았다 —
본문은 GitHub에 있고 증거는 CI 로그에서 재생성된다.
