# Handoff — flake cluster #260·#261 · #313 · #315 · #321 (+ #333) — 2026-09-24

실행 기록 정본은 `.omc/specs/batch-260-313-315-321-plan-2026-09-24.md`이다(준비 → 레인별 1~6차 라운드 → 머지 순서가 시간순으로 있다; 세션은 2026-09-24 02:58에 시작해 09-25 새벽에 끝났다).

## 기준점

- **main의 마지막 코드 커밋 = `b13c546c`** (2026-09-25 00:28 KST 머지). 이 세션이 올린 커밋, 아래가 먼저다.

  | 커밋 | 이슈 | 한 줄 | 브랜치 CI |
  | --- | --- | --- | --- |
  | `0ad95e11` | #313 | process_tree 0.2 s 하한 → 사건 순서, 디렉터리 스윕 | 35945632317 6/6 |
  | `a9805d03` | #315 | tests/tui 벽시계 대기 전수 스윕, 공용 `tests/tui/_polling.py`, ADR-0107 정정 | 35945866506 6/6 |
  | `9b535076` | #333 | 밴드 테스트·env-sandbox 가드가 git 출력을 UTF-8로 (Windows cp1252) | 35962401876 6/6 |
  | `4df8d884` | #321 | 취소된 `prompt()`가 phase를 돌려준다 — claim은 flip 자리에서, ADR-0023 amendment | 35968400483 6/6 |
  | `b13c546c` | #260 #261 | bash drain이 시계가 아니라 파이프의 **증명**으로 끝난다, ADR-0238 #260 amendment | 36017790447 6/6 |

- 전체 스위트(main `b13c546c`, 로컬 darwin): **11212 passed, 23 skipped**, 94 warnings in 446.54 s (`uv sync --all-packages` 뒤, 이 세션의 다른 작업이 멈춘 상태에서). main 위에는 이 핸드오프 커밋(문서만)뿐 — `git log --format='' --name-only b13c546c..origin/main | sort | uniq -c`로 확인
- 새 ADR 번호 없음 — 0249-0252 미사용, **다음 빈 번호는 여전히 0249**.
- pi 참조: 오너 클론 `/Users/handochan/dev/pi`는 HEAD `6671c6047`, `origin/main`은 `a328aa89a`(2026-09-23)까지 fetch됨(워킹트리는 안 건드림). 이번 레인들은 `git archive`로 뜬 `/tmp/pi-a328aa89a`를 읽었다(재부팅 시 사라짐).

## 바로 시작할 것

**#325** — `test_an_abort_during_the_exit_path_drain_keeps_the_helper`의 Windows 하한 실패. #260과 같은 drain·같은 테스트 파일이라 #260 머지 뒤로 미뤘던 것. #260이 drain 종료를 증명 기반으로 바꿨으므로 **먼저 새 drain에서 재현부터** — #260 레인의 darwin 측정에서는 이 케이스 수치가 안 움직였다(2.024-2.031 s), win32 조기 종료가 고쳐졌는지는 **미측정**. windows-latest의 `time.monotonic`은 GetTickCount64(15.625 ms, #260 CI에서 확인).

그다음 **#334**(P1, idle tail) — #321이 알고 받아들인 두 회귀(ARM 18 계열, 23c)를 닫는 유일한 길이고, 결함 없이도 두 턴이 겹쳐 컨텍스트를 공유한다(라이브 `ALPHA\nBRAVO`). pi의 `_isAgentRunActive` 모양(claim을 재시도·compaction 꼬리 내내 쥔다)이 기준.

## 이후 순서

1. **#330**(P1) tests/tui·process_tree 밖 벽시계 판정 — 영여유 하한(`tests/rpc/test_rpc_client_shutdown.py:200`)이 #313 모양 그대로다.
2. **#341**(P1 — #260의 hard-cap 테스트 상한이 늦은-look 연장과 모순, cap을 가로지른 0.4 s+ 정지면 옳은 동작에 빨개진다; #325와 같은 파일군이라 그 뒤에) · #338(`output_unconfirmed`를 REPL/RPC가 안 드러냄) · #339(win32 잔여) · #340(windows py3.11 park가 hard cap 직전에 돈다) · #332(`_drain` 시계 seam — #260 뒤라 이제 가능) · #331(tests/tui negative window).
3. #335(취소된 재시도 bookkeeping) · #336(턴 시작 전 abort 무시 — TUI Esc, 메인 루프가 probe로 확인, main에서도 재현) · #337(core.py 맨 줄번호).
4. core.py 이슈는 한 배치에 하나: #314 · #319 · #320 · #326 (#321·#334와 같은 파일).
5. 그 밖: #317 · #318 · #322 · #323 · #324 · #327-#329 · #131.

## 오너 판단 (권고 포함)

1. **#260의 모델용 알림 문구.** 증명 없이 hard cap에 닿았을 때만 도구 결과 끝에 붙는다: `[Output may be incomplete: reading stopped 2s after the command ended, before its last output could be confirmed. Re-run it, or redirect its output to a file, if the end matters.]`(kill 레그는 `1s`, 로컬이 아닌 `BashOperations`는 숫자 없이). **권고: 그대로 둔다** — pi에는 없는 표시지만 조용한 손실보다 낫고, 드물다.
2. **hard cap의 의미가 바뀌었다.** "exit+2.0 s(kill+1.0 s)"에서 "그것, 또는 grace보다 더 늦은 look이 cap 앞 grace 안이나 뒤에 오면 그 look + 0.1 s 중 늦은 쪽(드레인당 한 번)"으로. ADR-0238·CHANGELOG·README 행에 정확히 적었다. **권고: 수용** — 프로세스 전체가 멈춘 뒤의 피할 수 있는 손실(프로브 3~7/20 → 0/20)을 grace 하나로 없앤다.
3. **deadline과의 거래.** reader가 굶으면 `timeout=1.0` 호출이 exit+2.0 s까지 붙잡힌다 — 호출자의 deadline은 헬퍼 출력만 자르고 명령 자신의 출력은 자르지 않는다. **권고: 수용.**
4. **kill 레그의 대가(교차 리뷰 N1).** reader가 kill 시각을 가로질러 kill cap 내내 멈추면 완전한 출력에도 알림이 붙는다(무표시 손실을 표시로 바꾼 값). 지금 이 경로의 신호를 보는 호출자는 신호를 드러내지 않는 RPC `bash`뿐. **권고: 수용**, #338에서 문구를 다시 본다.
5. **#321이 받아들인 회귀.** 재시도 대기 중 두 번째 `prompt()`가 들어와 두 턴이 겹친 상태에서만, ARM 18 계열과 23c가 base가 막던 세 번째 `prompt()`를 받는다. **권고: #334(P1)를 다음 core 배치의 첫 순위로** — 겹침 자체를 없애면 둘 다 닫힌다.
6. **#321에는 Codex가 돌지 않았다**(한도; 대신 독립 컨텍스트 Claude 리뷰가 must-fix를 찾아 고쳤다). 원하면 다음 세션에서 머지된 `4df8d884`에 Codex를 돌린다 — #260에는 머지 직전에 돌렸다.

- **원격 브랜치 정리 — 완료(2026-09-25 01:0x, 오너가 실행).** 분류기가 `git push --delete`를 막아 `/tmp/aelix-delete-remote-branches-0925.sh`로 넘겼고, 병합된 5개가 지워져 원격은 `main` 하나다. 로컬도 워크트리 하나·브랜치 `main` 하나. 핸드오프 커밋 `a6cc2704`의 main CI 36021489558은 6/6 green(배치 전 docs 커밋 세 개가 연달아 플레이크로 빨갰던 자리).

## 이 레포에서 실제로 물린 규칙

- **Windows 러너만의 두 함정**(메모리 `windows-runner-traps-no-local-run-shows`): (1) CPython ≤3.12의 `time.monotonic()`은 ~15.6 ms 눈금 — 시각 **순서**로 증명하는 설계는 `>=`에서 동순위를 받아 불건전해진다(#260 P2, CI 35952321924). (2) `text=True` subprocess는 cp1252로 디코드 — `₁`(0x81) 하나에 reader 스레드가 죽고 `stdout=None`(#333, CI 35952181466). darwin·Linux Docker로는 둘 다 안 보인다.
- **독립 컨텍스트 교차 리뷰가 값을 했다**(오너 지시 14:4x, 메모리 `cross-review-fallback-is-independent-claude`): 레인 주장 없이 이슈·커밋만 준 opus 리뷰가 #321에서 내부 리뷰 2회·검증 2회가 놓친 **영구 busy 회귀**(첫 `_run` flip 무claim)를 probe로 잡았다. #260에서는 must-fix 없이 should-fix 둘(타이머 게이트 테스트, 늦은 drain의 거짓 알림). Gemini(무료 flash)는 40분 할당량 재시도에 묶여 보고서 0.
- **플레이크를 고치는 배치는 새 테스트도 정지 주입으로 돌린다** — `/tmp/260-work/xreview/stall_plugin.py`(pytest 프로세스 전체를 SIGSTOP)가 #260의 새 테스트 셋의 타이머 의존을 드러냈다. 제품이 옳아도 타이머가 drain을 이겨야 초록인 테스트는 새 플레이크다.
- **스택 머지의 비용**: 아래 커밋이 바뀔 때마다 위 브랜치를 재스택·재CI 해야 한다 — #260은 이번 배치에서 다섯 번 재스택됐다(D 재스택, #333 삽입, #333 amend, D 4차, D 머지 뒤 main). 늦게 끝날 레인을 스택 맨 위에 둔 것은 맞았다. #260은 6차까지 갔다: 설계·비판 → 구현 → 3번의 Windows/정지 실측 수정 → 두 번의 독립 교차 리뷰 반영.
- **머지 직전 Codex**(#260, 00:0x 한도 해제 뒤, effort high, 11만 토큰): POSIX 무표시 손실·pin 규칙·win32 결함 없음; 발견 둘(windows py3.11 park 스핀, hard-cap 테스트 상한)은 #340·#341로. 임시 워크트리에서 돌려 레인 트리를 건드리지 않게 했다.
- 인용 게이트의 digit churn: `core.py` 줄이 밀리면 ~40개 파일의 주석 숫자가 같이 움직인다(#321: 100 citations, 41 files). 4단계 규칙(`--check` → 대상 직접 읽기 → `--fix` → lock diff가 동일 텍스트 이동뿐인지)으로 매번 처리했다.

## 반증된 것 — 다시 믿지 말 것

- "파일 **내용**을 git으로 읽는 테스트 헬퍼는 밴드 테스트 하나뿐" — 거짓, `tests/test_env_sandbox_windows.py`의 `git grep` 두 곳도(#333 1차 메시지가 틀렸고 검증자가 AST 스캔으로 찾음).
- "claim은 phase를 세우는 모든 곳에서 잡힌다"(#321 둘째 버전) — 거짓, 첫 `_run`의 flip이 빠져 있었다.
- "ARM 18은 두 번째 prompt가 **턴 중**일 때만" — 거짓, 사전 훅 안에서도 같다.
- "로컬과 Linux에서 결정적으로 초록 = Windows에서도 초록"(#260 3개 테스트) — 거짓, 동순위 시계.
- "#260이 #325의 Windows 조기 종료를 고친다" — 보이지 않았다(미측정).
- "Gemini CLI로 교차 리뷰를 대신한다" — 이 계정의 무료 티어에서는 성립하지 않았다.
