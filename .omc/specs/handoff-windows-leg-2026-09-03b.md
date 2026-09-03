# 다음 세션 핸드오프 — windows 레그, 433 → 143 (2026-09-03, 2차)

앞선 핸드오프 `handoff-n3-stdio-2026-09-03.md`의 후속. 그 문서의 §4(정체불명 편집)와 §5(#103)는
전부 종결됐다. 아래는 그 이후 21커밋.

## 0. 시작 전 2분 (건너뛰지 말 것)

```bash
git log --oneline -12
uv run pytest -p no:cacheprovider -q ; echo "exit=$?"   # 기준선: 1 failed / 9370 passed
```

🔴 **게이트는 exit code로 확인할 것. 출력 마지막 줄로 판단하지 말 것.**
이번 세션에서 `check_types.py`를 `tail -1`로 확인했다가 **빨간 게이트를 초록으로 읽고 ubuntu를
깨뜨린 채 push했다.** 실패 시 마지막 줄은 판정이 아니라 narrowing spike 안내다. exit code는
처음부터 1이었다. (`f8654ce`)

## 1. 현재 상태

| | |
| --- | --- |
| CI overall | **success** — ubuntu py3.11/3.12 초록(차단), windows advisory |
| windows py3.11 | **143 failed / 9179 passed / 61 skipped** (13:50) |
| 로컬 (darwin) | **1 failed / 9370 passed** |

로컬의 1건은 `tests/server/test_server.py::test_config_from_env_overrides` — darwin
`/private/tmp` vs `/tmp` realpath. 이 세션 내내 손대지 않았고 windows와 무관하다.

windows 추이: `433 → 238 → 143`. 마지막 구간은 **사라진 것 95, 새로 생긴 것 0.**

## 2. 다음에 할 것 — 권장 순서

### 🔴 먼저: #204 이슈 본문 정정 (30분, 큰 절약)

**#204의 핵심 전제가 틀렸고 아직 그대로다.** 다음 사람이 그걸 읽고 엉뚱한 절반을 만든다.

측정 결과(직접 재현):
```
ASK    Remove-Item -Recurse -Force C:\      ← 이슈는 "ALLOW"라고 주장
ASK    del /s /q C:\Windows                 ← 같음
ALLOW  date 01-01-2030                      ← 진짜 구멍 (cmd: 시스템 시계 변경)
ALLOW  sort in.txt /o out.txt               ← 진짜 구멍 (cmd: /O가 파일 쓰기)
```
파괴적 cmdlet 이름은 어떤 테이블에도 안 걸려 **미지 명령 → ASK**로 떨어진다. 진짜
mis-permissioning은 반대 모양 — 알려진 read-only **이름**의 인자를 아무도 안 읽는 것.
`0be16cd`가 그 셋을 좁혔고 `bash_classifier.py:80-93` 주석은 정정했다. 아직 틀린 채인 곳:
**이슈 #204 본문**, `permission.py:591-593`, `test_permission_shell_competence.py:6-8`,
`windows-support-prep-2026-09-03.md:73-74`, 그리고 이전 핸드오프 §6.

같은 조사에서 나온, 이슈가 언급조차 안 한 것: `permission.py:622`가 shell 검사보다 **먼저**
DENY를 반환해서 PowerShell `rm foo.txt`가 프롬프트 없이 하드 차단된다. 설계안은 이걸 제거하라고
했지만 **일부러 안 했다** — PowerShell 분류기가 생기기 전에 제거하면 대체물 없이 통제를 푸는 것이다.

### 그 다음: 잔여 143건의 클러스터 이슈 생성

**제품 결함은 현재 0건이다.** 137건 AssertionError 전수 분류에서 제품 버그는 3건이었고 전부
착륙했다(`c888052`, `ba1e21a`, `785ccf5`). 나머지는 테스트 부채이고, 이슈가 없어서 추적이 안 된다.

| 클러스터 | 건수 | 성격 |
| --- | ---: | --- |
| 자식 환경에서 `SystemRoot` 제거 → `WinError 10106` | 14 | **가장 싼 승리.** 자식이 `import asyncio`에서 죽는다. `_child_env()`가 `{PATH, PI_OFFLINE, PYTHONUNBUFFERED, PYTHONPATH}`만 만든다. `grep -rn SYSTEMROOT` → 0건 |
| Rich `legacy_windows` | 15 | 폭이 정확히 −1(`console.py`가 `width - legacy_windows`), 그리고 ROUNDED→SQUARE 박스라 `╮` 탐지가 실패 |
| POSIX mode 비트 단언 | 13 | `0o666 != 0o600`. 구조적으로 빨감 — 고칠 대상이 아니라 분류할 대상 |
| POSIX 경로 리터럴 | 11 | 테스트가 `/abs/x` 같은 리터럴을 넣고 POSIX 결과를 고정 |
| POSIX 셸/바이너리 픽스처 | 7 | `#!/bin/sh` 스크립트, `sh -c`, `printf … && mv` |
| CRLF / 인코딩 픽스처 | 6 | `write_text()`에 `newline=""`/`encoding=` 없음 |

`tools/test_grep_tool.py` 계열은 `785ccf5`로 해소됐다.

### 그 다음: 이전 핸드오프 §6 순서

`#108 F-4 → #105 → #202 → #107 후반 → #201 → #46`. 병렬 트랙 **#204**는 위 정정 후.

## 3. 🔴 하지 말 것 — 측정되지 않은 것을 확정으로 적기

이 세션의 가장 비싼 실수 두 개가 전부 이 형태였다. 아래는 **측정된 것과 아닌 것의 경계**다.

**측정됨** (windows runner에서 직접 실행):
- `os.kill(pid, 0)`은 windows에서 생존 확인이 아니다. `CTRL_C_EVENT`가 0이고 CPython
  `os_kill_impl`이 시그널 0을 `GenerateConsoleCtrlEvent`로 보낸다. 프로브: 호출은 정상 반환,
  대상(유휴 `time.sleep(30)`)은 1초 후 죽음.
- 그 네 사이트를 고치니 스위트가 완주했다 (3%에서 중단 → 13:50 완주, 신규 실패 0).

**측정되지 않음 — 이렇게 적지 말 것:**
- ❌ "`os.kill(pid,0)`은 **호출자 자신의** 콘솔에 Ctrl+C를 쏜다."
  프로브에서 `SELF RECEIVED SIGINT: False`였다. 재현되지 않았다.
- 그래서 **pytest가 정확히 어떻게 인터럽트됐는지는 여전히 미규명**이다. 수정이 중단을 없앤 것은
  사실이지만, 전달 경로는 밝혀지지 않았다.

**이걸 지금 파야 하나? 아니다.** 네 호출부를 모두 고쳐서 트리거가 재발화할 수 없고, 막힌 작업이
없다. 규명하려면 windows CI를 반복해야 하는데 얻는 것은 사용자 영향이 없는 "왜"다. 다시 열
가치가 생기는 시점은 **비슷한 중단이 다시 나타날 때**뿐이고, 그때는 이 문단이 출발점이다.

## 4. 이 레포에서 이번에 물린 것

1. **`skipif` 규약에는 예외가 있다.** 기본은 "조건을 주입해서 어디서나 돌려라"
   (`tests/cli/test_stdio_encoding_win32.py`). 하지만 **OS가 물리적으로 픽스처를 거부하면**
   `skipif`가 맞다 — NTFS는 `<`, `>`, `"`, 제어 문자를 파일명에 허용하지 않아서
   `mkdir('we"ird')`를 성공시키는 제품 수정은 존재하지 않는다. 선례:
   `tests/oauth/test_auth_storage.py:64`. `4e3b5bd`는 `mkdir_or_skip` 픽스처로 처리했고,
   `sub\x9bdir`(U+009B는 NTFS에서 합법)은 계속 돌게 파라미터별로 갈랐다.
2. **유리한 픽스처를 다섯 번 만났다.** 넷은 이번에 새로 쓴 테스트였고 "수정을 빼고 실패하는지
   확인" 절차로 걸렀다. 다섯 번째는 **원래 레포에 있던 것**이라 실재하는 grep 버그를 계속
   통과시키고 있었다(스텁 rg 줄에 `formatBlock` 공백이 미리 들어가 있어, 상대화되지 않은 줄도
   기대 부분문자열을 포함했다). **새 테스트마다 수정을 되돌려 실패를 확인할 것.**
3. **크래시는 실패를 숨긴다.** `fchmod` 200건을 걷어내니 그제서야 도달한 테스트가 드러났고,
   `preexec_fn`을 고치니 47건이 사라지는 동시에 새 층이 보였다. **잔여 숫자는 항상 하한이다.**
4. **동시 편집 시 `check_citations.py --fix`를 각자 돌리면 락이 깨진다.** 한 에이전트의
   작업 중 docstring(그 코드를 인용하고 있었다)에 앵커가 재배치됐다. 여럿이 병렬로 고칠 때는
   드리프트를 보고만 하고, **마지막에 한 번 일괄 relock**할 것 (`027fca7`).
5. **커널 동결 게이트를 넘으려면 ADR이 필요하다.** `packages/aelix-agent-core`는
   `test_p2_band_boundaries.py::test_kernel_untouched_vs_merge_base`가 잠근다. 게이트의 실패
   메시지가 절차를 알려준다 — allowlist 항목 + ADR. `ba1e21a`가 ADR-0236을 썼다.
   **다음 ADR은 0237부터.**
6. `os.kill(pid, 0)`은 `_pid_is_live`(`prompt_file.py`)로 통일됐다. **새로 복사하지 말 것.**

## 5. 변하지 않은 금지 사항

🔴 **이 저장소는 PUBLIC이다.** `.omc/specs/recovery-report-dead-session-91.md`는 **의도적으로
미커밋**이다 — 미완화된 cross-dist hijack 벡터를 재현 가능하게 서술한다. `docs/assets/*` 10건도
다른 세션 것이다. **`git add -A`를 쓰지 말 것.** 경로를 명시해서 스테이징할 것.

## 6. 재현 명령

```bash
# 게이트 (exit code로 판단)
uv run ruff check . ; echo $?
uv run python scripts/check_types.py ; echo $?
uv run python scripts/check_citations.py ; echo $?
uv run pytest -p no:cacheprovider -q ; echo $?     # 1 failed / 9370 passed 가 정상

# windows 레그 결과 받기 (요약 줄이 안 잡히면 로그가 아직 안 올라온 것)
RID=$(gh run list --workflow=ci.yml --branch main --limit 1 --json databaseId -q '.[0].databaseId')
JID=$(gh run view "$RID" --json jobs -q '.jobs[] | select(.name|test("windows.*3\\.11")) | .databaseId')
gh api "repos/handochan/aelix-ai/actions/jobs/$JID/logs" > /tmp/win.log
grep -aoE "[0-9]+ failed, [0-9]+ passed[^)]*\)" /tmp/win.log | tail -1
grep -aoE "FAILED tests/.*" /tmp/win.log | sed -E 's/^FAILED [^ ]+ - //' \
  | grep -oE "^[A-Za-z_.]*(Error|Exception|Failed)" | sort | uniq -c | sort -rn
```

windows 레그는 잡과 타입 게이트 모두 `continue-on-error`를 `matrix.os == 'windows-latest'`로
**한정**한다. ubuntu는 두 게이트 모두 차단이다. 잡 레벨에 무조건 `true`를 걸면 ubuntu 실패까지
advisory가 되어 게이트가 조용히 사라진다.
