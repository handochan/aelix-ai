# 다음 세션 핸드오프 — N-3 완료, 다음은 #103 (2026-09-03)

`main` = `e6a346d` (N-3). 직전 기준점은 `d30da4a`.
계획 정본은 여전히 `.omc/specs/windows-support-prep-2026-09-03.md`.

스위트는 **green이 아니다** — `12 failed / 9291 passed / 12 skipped`.
12건은 전부 이전 세션이 기록한 그대로의 플랫폼 이식성이고 실제 결함 0이다
(11건 `/proc/self/fd`, 1건 `/tmp`→`/private/tmp`). passed가 `9250 → 9291`로 올라간
`+41`이 이번에 추가한 테스트 전부다. **회귀 0.**

---

## 0. 시작 전 3분 (건너뛰지 말 것)

```bash
git fetch origin --quiet
git rev-parse --short origin/main       # e6a346d 가 아니면 아래 전부 재확인
```

🔴 **오너가 세션을 동시에 여러 개 돌린다.** 이번 세션이 그 증거를 하나 더 만들었다 —
§4를 반드시 읽을 것. 오래 도는 팬아웃 뒤에는 검증을 돌린 커밋이 아직 HEAD인지 대조한다.

---

## 1. 이번 세션이 한 일

### N-3 (stdout/stdin 인코딩) — 완료, `e6a346d`

`#110`의 P7이 인라인 추적하던 항목. 본문은 `modes/print_mode.py`를 지목했지만
**거기가 아니다** — 테스트가 in-process로 호출하는 자리라 reconfigure가 pytest 세션으로
샌다. 실제 구현은 신규 `packages/aelix-coding-agent/src/aelix_coding_agent/util/stdio.py`,
호출은 콘솔 스크립트 엔트리 **3곳**의 첫 문장:

| 파일 | 함수 |
|---|---|
| `cli/entry.py` | `main_sync` |
| `src/aelix/__main__.py` | `main` |
| `packages/aelix-server/src/aelix_server/main.py` | `main_sync` |

**정책** — 인코딩을 다시 쓰는 건 **리다이렉트된 출력 스트림 하나뿐**이다. 파이프에는
코드페이지가 없으니 UTF-8이 유일한 상호운용 타깃이다. 레거시 콘솔(tty)은 코드페이지를
유지하고 에러 핸들러만 완화한다(재인코딩하면 mojibake). **UTF-8 스트림은 손대지 않는다** —
이 조기 반환이 POSIX·macOS·최신 Windows 콘솔(PEP 528)에서 no-op을 보장하고, 기존 스위트를
건드리지 않는 이유다.

`#110` P7 항목은 취소선 + 완료 주석으로 갱신했다.

## 2. 반증된 것 — 다시 믿지 말 것

1. 🔴 **"errors가 strict만 아니면 보존해도 된다"는 틀렸다.**
   `surrogateescape`는 **디코드에서는 total이지만 인코드에서는 raise한다** — cp949 스트림에
   `"█"` 쓰면 `UnicodeEncodeError`. 실측했다. 자기가 만든 surrogate만 왕복시키고 진짜로
   인코드 불가능한 문자에는 raise한다. 지금은 방향별로 "raise할 수 있는가"를 묻는다
   (`_TOTAL_ENCODE_ERRORS` / `_TOTAL_DECODE_ERRORS`).

   **왜 이게 Windows에서 치명적인가 — 출처를 달아 둔다.** CPython
   `Python/initconfig.c::config_get_stdio_errors`가 `#else`(= `MS_WINDOWS`) 가지에서
   **무조건** `surrogateescape`를 돌려준다. 주석 그대로 *"On Windows, always use
   surrogateescape by default"*. 3.11·3.12 양쪽에서 동일하다(소스 확인).

   여기서 콘솔과 리다이렉트를 갈라 봐야 정확하다:

   | Windows stdout | encoding | errors | 이 모듈의 처리 |
   |---|---|---|---|
   | 콘솔 | `utf-8` (`_WindowsConsoleIO`, PEP 528) | surrogateescape | `_is_utf8` 조기 반환 → **no-op** |
   | **리다이렉트(= CI)** | **ANSI 코드페이지** (cp949 등) | **surrogateescape** | **여기가 고치는 가지** |

   즉 옛 규칙("strict만 아니면 보존")은 **정확히 CI 경로에서** surrogateescape를 보존했을
   것이고, 그러면 인코드는 여전히 raise한다. macOS·Linux 테스트는 전부 green인 채로
   **N-3이 존재하는 이유인 그 경로만 계속 깨진다.**
   (`sys.stderr`는 항상 `backslashreplace`라 애초에 위험하지 않았다.)

   ⚠️ **실기 Windows에서 관측한 것이 아니다 — CPython 소스에서 읽은 것이다.**
   첫 `windows-latest` 실행이 확인해 줘야 한다.

   🔴 **그리고 트리거 자체는 Windows 전용이 아니다 — 이 문서의 첫 판본이 틀렸다.**
   같은 함수가 surrogateescape에 도달하는 경로는 **셋**이다: UTF-8 모드(PEP 540),
   레거시 `C`/`POSIX` 로케일, 그리고 **PEP 538 coercion target** — 여기에 `C.UTF-8`이
   들어간다. **`C.UTF-8`은 대부분의 컨테이너·CI 이미지의 기본 로케일이다.**
   이 기계에서 실측:

   | env | `utf8_mode` | `stdout.errors` |
   |---|---|---|
   | ambient (`LANG=C.UTF-8`) | **0** | **surrogateescape** |
   | `LC_ALL=en_US.UTF-8` | 0 | strict |
   | `LC_ALL=C` | 1 | surrogateescape |

   즉 옛 규칙은 **평범한 Linux CI에서도** raise하는 핸들러를 보존했을 것이다. 거기서
   드러나지 않았던 이유는 단 하나 — **UTF-8 스트림은 뭐든 인코드되니 raise 지점에
   도달하지 않는다.** 조건이 **둘** 겹쳐야 터지고, Windows가 대는 건 두 번째뿐이다:

   1. total이 아닌 핸들러가 보존됨 — CI에서 거의 보편적
   2. 실제로 실패할 수 있는 코덱 — 레거시 코드페이지

   **첫 판본은 1번을 Windows 고유로 적었다. 아니다.** 고친 규칙은 처음 생각했던 것보다
   더 많은 플랫폼에서 유효하다.

2. 🔴 **stdin 코덱을 "UTF-8 먼저"로 고르면 안 된다.**
   짧은 코드페이지 문자열이 그 자체로 유효한 UTF-8일 수 있다:
   `cp949 "책"` = `c3 a5` = 유효한 UTF-8 `"å"`, `cp949 "짜"` = `c2 a5` = `"¥"`.
   초안의 테스트는 **하필 UTF-8로 무효한 문구**를 골라서 통과했다.

   🔴 **이 실패 모드는 크래시가 아니라 조용한 오염이다.** UTF-8 먼저 시도해도 raise하지
   않는다 — 한글 대신 그럴듯한 라틴 문자를 돌려주고 그대로 모델에 넘어간다. **원래 버그보다
   나쁘다**(원래는 최소한 시끄럽게 죽었다). 그리고 **"죽지 않는다"만 검사하는 fixture로는
   원리상 잡히지 않는다.** #203과 정확히 같은 모양이다.
   지금은 **선언된 인코딩 먼저, UTF-8 나중**이다. 이 순서가 함수를 *strictly additive*로
   만든다 — 전에 디코드되던 입력은 전부 동일하게 디코드되고, **전에 죽던 입력만** 새 경로를
   탄다. 반대 방향엔 이 위험이 없다: UTF-8 한글은 cp949로 무효다(`0xec`는 적법한 lead가 아님).

3. **"reconfigure만 넣으면 N-3 끝"이 아니다 — `sys.stdin`이 절반이다.**
   `echo 안녕하세요 | PYTHONIOENCODING=cp949 aelix -p`가 `_read_piped_stdin`에서
   `UnicodeDecodeError`로 죽었다. 출력 쪽만 고친 뒤에도 계속 죽었다.
   신규 `read_all_text`가 바이트에서 디코드하고 `entry.py:367`이 그걸 쓴다.

4. **stdin의 end-to-end 테스트는 만들 수 없다 — 만들면 공허하게 통과한다.**
   hermetic한 자식은 전부 모델 선택 단계(`entry.py:3061`)에서 죽고, 그 출력은 stdin이
   비었을 때와 채워졌을 때가 **완전히 동일**하다. 게다가 그 크래시는 에러 핸들러 완화만으로도
   이미 막히므로 **디코드를 되돌려도 통과한다**(실측). #203과 같은 모양이라 만들지 않았다.
   디코드는 단위 테스트, 배선은 monkeypatch로 고정했다.
   반대로 **`--help` e2e는 진짜다** — hardening을 끄면 rc가 1이 된다(실측).

5. **`check_citations.py`는 `git ls-files`로 인용 파일을 찾는다.**
   새 파일이 untracked인 채로 `--fix`를 돌리면 **로컬은 green인데 `git add` 하는 순간 red**가
   된다. 실측: staged 전 exit 0 → staged 후 exit 1. **새 파일을 먼저 stage하고 나서 `--fix`.**

## 3. 마일스톤 — 정리 완료 (오너 승인)

| | 이전 | 이후 |
|---|---|---|
| `v0.1.0-beta.1` | open, 마감 2026-08-20 경과, open 3 | **closed.** 릴리스는 2026-08-20에 예정대로 나갔다. 남은 #186 #111 #84는 `v0.1.0`으로 |
| `v0.1.0` | 마감 2026-08-29 경과, open 11 / closed 0 | **마감일 제거.** open 14. #73(오너 수동)과 #103(아직 없는 CI 숫자)에 물려 있어 날짜를 박으면 희망이 된다 |
| — | — | **`Windows support` 신규 (마일스톤 #3).** 완료 정의 3조건(#109 #106 #204) + 선행 9건. 마감일 없음 |

**#103은 일부러 `v0.1.0`에 남겼다** — pyproject 4곳의 `OS Independent`를 걷어내는 릴리스
정직성 작업이라 GA 게이트다. Windows 트랙의 P0이기도 하지만 GitHub은 마일스톤을 하나만 준다.

⚠️ **#111은 "공지 전 트랙"이다.** beta.1 공지가 실제로 나갔는지 확인하지 못한 채
`v0.1.0`으로 옮겼다. 공지가 이미 끝났다면 #111의 남은 범위를 다시 봐야 한다.

📌 **진짜 문제는 따로 있다.** 최근 30일 closed 52건 중 마일스톤이 붙은 건 **3건**이다.
마일스톤이 실제 작업의 약 6%만 추적한다. 날짜를 고치는 것보다 이쪽이 크다.

## 4. 이번 세션의 사건 — 정체불명 편집

세션 도중(01:21) 작업 트리에 `harden_stdio()`가 **커밋 없이** 나타났다. 내 서브에이전트가
쓴 게 아니고(전 트랜스크립트 감사: repo 파일 조작 0, 쓰기는 전부 scratchpad),
**어느 Claude 세션도 아니다**(peer 세션 `aelix-ai-59`가 `~/.claude/projects` 전역 grep으로
확인). 유력 후보는 이 레포에 스코프된 **Codex 에이전트**(`PATH=.../aelix-ai/.venv/bin`인
`codex app-server` 프로세스 5개+) 또는 **VS Code 직접 편집**이다.

오너 승인을 받아 **되돌리지 않고 그 위에 얹었다.** 원본은 보존돼 있다:

- **태그 `wip/harden-stdio-unattributed` → `40ce1b2`** ← 이걸 써라.
  ⚠️ `git stash create`가 뱉는 SHA는 **dangling commit**이라 아무도 참조하지 않으면
  `git gc`가 수거한다. 그래서 태그로 고정했다. 로컬 lightweight 태그라 `git push`로는
  안 올라간다 — 원격에도 남기려면 `git push origin wip/harden-stdio-unattributed`.
  (세션 scratchpad에 뜬 `.patch` 파일은 임시 디렉터리라 durable하지 않다.)
- 그 구현이 가진 결함 5건(리다이렉트 시 UTF-8 아님 / stdin 미커버 / citation 게이트 red /
  `python -m aelix` 기동 2배 / 테스트 0)은 전부 이번 커밋에서 해소했다.

**교훈:** 이 레포에서 `git status`는 세션 시작 시점의 스냅샷일 뿐이다. 팬아웃 전후로 다시 봐라.

## 5. 바로 시작할 것 — #103 (P0)

N-3이 끝났으니 이제 CI를 켜도 첫 결과가 인코딩 노이즈에 묻히지 않는다.

- **P0-a** — `pyproject.toml` **4곳**에서 `Operating System :: OS Independent` 제거 +
  README/README.ko에 Platform support 섹션 신설.
  🔴 **릴리스 표기 결정이라 오너 확인이 필요하다.** "Windows 미지원"의 공식화다.
- **P0-b** — `ci.yml:33-37` 매트릭스에 `windows-latest` + `continue-on-error: true`.
  🔴 **첫 실행은 빨간 게 정상이다. 첫 green을 마일스톤으로 삼지 말 것** —
  #108 F-3·F-4, #46, #201, #203, P7의 S-4·S-8은 "조용히 틀린" 쪽이라 green을 통과한다.
- **실측 숫자를 #110에 기록.** 여기서부터 일정 대화가 가능하다.

계획 정본 §4의 값싼 제안이 아직 유효하다: **`macos-latest` 레그가 `windows-latest`보다 싸고
같은 부류를 먼저 걸러낸다.** 지금 CI의 `runs-on` 5개는 전부 ubuntu다.

## 6. 이후 순서

**#200 → #108 F-4 → #105 세 번째 사이트 → #202 → #107 후반 → #201 → #46 →
#108 F-3/F-5/F-6 → #106 실행 검증 → #110 P7(S-4 `%APPDATA%` / S-8 `Ctrl+G`).**

**병렬 트랙 — #204.** 완료 정의 3번이고 셋 중 가장 크다. P0의 숫자를 기다리지 않는다.
🔴 강등을 되돌리는 작업이 **아니다** — `bash_classifier.py:77-87`이 이유를 적어뒀다.
bash 문법에 PowerShell을 먹이면 `Remove-Item -Recurse -Force C:\`가 **ALLOW로 나온다.**
필요한 건 PowerShell/cmd 전용 분류기이고 ADR-0158의 "가장 엄격한 판정이 이긴다"를 지켜야 한다.

## 7. 이 레포에서 실제로 물린 규칙

1. **교차 리뷰는 형식이 아니다.** 5개 렌즈 × 적대적 검증(에이전트 119개)이 §2의 블로커
   2건을 **놓쳤고**, Codex가 잡았다. CLAUDE.md 8번이 있는 이유다. 어렵거나 리스크가 큰
   작업은 반드시 `codex exec`에도 물릴 것.
   ⚠️ `omc ask codex --file`은 stdin을 기다리며 멈춘다. `codex exec "$(cat …)" < /dev/null`로 쓸 것.
2. **`.gitignore`와 각 `pyproject.toml`의 `exclude`는 함께 움직인다.** hatchling이
   `.gitignore`를 자기 exclude 스펙에 이어 붙인다. 바꿨으면 `tests/packaging_gate/`로 실측.
3. **`.omc/specs/`는 커밋 대상이다**(`.gitignore`가 `!/.omc/specs/`로 열어뒀다).
4. 🔴 **이 저장소는 PUBLIC이다.** `.omc/specs/recovery-report-dead-session-91.md` 1건은
   **의도적으로 미커밋**이다 — 미완화된 cross-dist hijack 벡터를 재현 가능하게 서술한다.
   **커밋하지 말 것.** 이번 세션도 스테이징에서 제외했다.
   `docs/assets/*` 10건도 이 세션 것이 아니라 그대로 뒀다.
5. **워크트리는 `.claude/worktrees/` 아래**(exclude가 이미 `.claude`를 막는 유일하게 안전한 자리).

## 8. 재현 명령

```bash
# N-3이 살아 있는지 (셋 다 통과해야 한다)
PYTHONIOENCODING=cp949 uv run aelix --help > /tmp/h.txt   # rc=0, 그리고
python3 -c "print(open('/tmp/h.txt','rb').read().decode('utf-8')[:0])"  # 예외 없음
printf '%s' "한 단어로만 답해줘: 대한민국의 수도는?" | PYTHONIOENCODING=cp949 uv run aelix -p   # 서울
python3 -c "open('/tmp/k','wb').write('한 단어로만 답해줘: 프랑스의 수도는?'.encode('cp949'))"
PYTHONIOENCODING=cp949 uv run aelix -p < /tmp/k                                              # 파리

uv run python -m pytest tests/cli/test_stdio_encoding_win32.py -q -p no:randomly   # 41 passed
uv run python scripts/check_citations.py   # exit 0  (새 파일은 stage 후에 --fix!)
uv run ruff check .                        # All checks passed!

# #103 착수 지점
grep -rn "OS Independent" --include=pyproject.toml . | grep -v .claude/worktrees   # 4
grep -rni "windows" .github/workflows/                                             # 0

# 스위트 (약 4분) — 12 failed / 9291 passed 가 현재 정상
uv run python -m pytest -q -p no:randomly
```
