# Windows 완전 지원 — 착수 준비 (2026-09-03)

기준점 `main` = `6ea4793`. 아래 판정은 전부 이 커밋에서 **재실측**한 것이다.
선행 문서: `.omc/specs/handoff-windows-roadmap-2026-08-22.md`, 리포 루트 `SLICE-STATUS.md`,
이슈 #110(메타 로드맵 — 이 세션에서 갱신함).

## 한 줄 결론

**착수를 막는 것은 코드가 아니라 숫자의 부재다.** 실기 Windows 러너에서 이 스위트가
한 번도 돌지 않았고(여전히 **0회**), #109가 예측한 실패 85~110건의 실제 값을 아무도
모른다. **P0(#103)이 그 숫자를 만드는 유일한 작업이고, 그것을 막던 blocker는 이미
해소돼 있다**(`c2ab62b`, W1). #103은 보드에서 `Ready`로 올려 두었다.

## 0. "완전한 지원"의 정의 — 오너 확정

**아래 셋을 모두** 만족해야 완료다. 하나라도 빠지면 "지원"이 아니라 "동작"이다.

| # | 조건 | 현재 |
|---|---|---|
| 1 | **#109** — 테스트 스위트가 실기 Windows에서 green | 실행 0회 |
| 2 | **#106** — `install.ps1`이 실기 `windows-latest`에서 end-to-end 실행 성공 | 실행 0회 |
| 3 | **#204** — AUTO 모드가 Windows에서 ASK로 강등되지 않음 | 강등 중 |

1·2는 P0(#103)이 만드는 숫자에 의존한다. **3은 그와 독립적으로 지금 착수 가능하고**,
셋 중 가장 크다.

## 1. 이 세션에서 한 일

| | |
|---|---|
| #110 본문 | 오류 3건 정정(원문 보존, 취소선), 실측 대조표 추가, `SLICE-STATUS.md` 링크 |
| 신규 이슈 | **#200 #201 #202 #203** — 아래 §3 |
| 보드 | 신규 4건 등록, **#103 → `Ready`** |
| 베이스라인 | macOS 전체 스위트 실측 — §4 |

## 2. 정정 — 8/22 handoff가 틀린 곳, 그리고 내가 이어받은 곳

8/22 handoff는 *"N-1·N-2·N-3 셋 다 어느 이슈에도 없다"* 고 적었다. **틀렸다.**
#110의 P7 절이 처음부터 여러 항목을 **인라인으로 추적**하고 있었다 — P7은
*"별도 이슈 미생성 — 여기서 추적"* 이라고 스스로 명시한다.

이 문서의 첫 판본은 그 주장을 검증 없이 이어받아 "미등록 7건"이라고 적었다.
#110 본문을 고정 문자열로 대조해 확인한 결과는 이렇다:

| 항목 | #110 P7에 있었나 | 실제 처리 |
|---|---|---|
| N-3 stdout 인코딩 | ✅ 있음 (`modes/print_mode.py`) | P7에 유지 — CI 직전 선행 작업 |
| S-4 `%APPDATA%` | ✅ 있음 | P7에 유지 |
| S-8 `Ctrl+G` → `vi` | ✅ 있음 | P7 유지 + **라인 드리프트 정정** (`:1758` → `:2559`) |
| N-1 `preexec_fn` | ❌ 없음 | → **#200** |
| N-2 한 줄 붙여넣기 | ❌ 없음 | → **#201** |
| S-10 자손 고아화 | ❌ 없음 | → **#202** |
| (신규 발견) 공허한 통과 | ❌ 없음 | → **#203** |

교훈: `grep`에 `Ctrl+G`를 정규식으로 넘기면 `+`가 메타문자라 오탐이 난다. 고정 문자열로
다시 재야 했다.

## 3. 신규 등록 이슈

- **#200** `preexec_fn=pdeathsig`가 Windows에서 raise → **위임 스폰 경로 전면 사망.**
  `aelix_agents/print_channel.py:970`, `rpc_channel.py:438`. `SLICE-STATUS.md`가
  "다른 트랙 소유"로 남겨 뒀고 `README.md:231`이 이미 미지원을 명시하지만, 이슈는 없었다.
- **#201** Windows는 **개행 없는 붙여넣기를 bracket하지 않는다**
  (`Win32Input._is_paste`는 `newline_count >= 1`을 요구). `chrome.py:172`의
  `_PASTE_COLLAPSE_MIN_CHARS = 1000` 갈래 — 즉 **#81 접기의 절반이 Windows에서 죽은 코드**다.
- **#202** 프로세스 그룹 부재로 `terminate()`/`kill()`이 자손을 고아로 남긴다.
  `subprocess_hooks.py:176,181` · `rpc_client.py:476,486` · **`oauth/_resolve_config.py:80,90`**
  (마지막은 `SLICE-STATUS.md` 목록에도 없던 사이트). 해법은 이미 있다 —
  W3의 `tools/_process_tree.kill_process_tree`.
- **#203** 🔴 **프로세스 kill 테스트 2건이 Linux 밖에서 공허하게 통과한다.** §4 참조.
- **#204** 🔴 **AUTO의 ALLOW 판정이 Windows에서 ASK로 강등된다**(DENY는 셸 검사 전에
  그대로 나간다) — `permission.py`의 `_auto_classify_bash`가 `is_classifiable_shell`로
  게이트하고 `_CLASSIFIABLE_SHELLS`에 PowerShell·`cmd`가 없다.
  강등 자체는 **옳다** — 다만 `Remove-Item`류가 ALLOW로 새서가 아니다(그런 이름은 분류
  테이블에 없어 ASK로 떨어진다, 실측). 진짜 오권한은 반대 모양이었다: `date`·`sort`처럼
  **테이블이 읽기 전용으로 아는 이름**의 인자를 ALLOW 티어가 안 읽어, cmd 하
  `date 01-01-2030`(시계 변경)·`sort in.txt /o out.txt`(파일 쓰기)가 **0be16cd 이전엔**
  ALLOW로 샜다. 해법은 문법 확장이 아니라 **PowerShell/cmd 전용 분류기**
  (`bash_classifier.py:83-95`, `0be16cd`).

## 4. 베이스라인 — macOS에서 실측

```
12 failed, 9250 passed, 12 skipped in 249.90s
```

**12건 전부 플랫폼 이식성이고 실제 결함이 아니다.**

- **11건** — `tests/providers/test_client_lifetime_over_completed_turns.py:516`의
  `_fd_count()`가 `os.listdir("/proc/self/fd")`. Linux 전용 procfs →
  `FileNotFoundError`. **Windows에서도 동일하게 실패한다.** "시끄럽게 깨지는" 정직한 부류.
- **1건** — `tests/server/test_server.py::test_config_from_env_overrides`.
  macOS에서 `/tmp`이 `/private/tmp` 심링크라 `ServerConfig.from_env()`의 경로 해석 결과가
  테스트의 기대와 다르다.

### 🔴 통과했기 때문에 이 12건에 없는 것

`tests/tools/test_subprocess_helper.py:161-175`와 `tests/tools/test_abort_signal.py:144-158`은
`/proc/<pid>/status`의 **부재**를 이렇게 읽는다:

```python
if not os.path.exists(proc_status):
    # Process fully reaped — definitely dead.
    last_state = "gone"; break
```

macOS·Windows에는 그 파일이 **존재한 적이 없다.** 루프는 첫 반복에서 빠져나오고,
자식이 실제로 죽었는지는 **한 번도 검증되지 않은 채 green**이 된다.

하필 프로세스 트리 kill 영역이다 — Windows에 프로세스 그룹이 없어 자손이 고아가 되는
바로 그 영역(#105, #202). **첫 green을 마일스톤으로 삼으면 안 되는 이유의 가장 구체적인
사례**이고, #109의 예상 실패 목록에는 원리상 잡히지 않는다(실패하지 않으니까). → #203

### 여기서 나오는 값싼 제안

이 스위트는 **Linux 밖에서 돈 적이 없다.** CI의 `runs-on` 5개가 전부 ubuntu다.
macOS 하나만으로 이식성 결함 12건 + 공허한 통과 2건이 나왔다. **`macos-latest` 레그는
`windows-latest`보다 싸고, 같은 부류의 결함을 먼저 걸러낸다** — Windows 첫 빨간 결과의
노이즈를 줄이는 방법으로 검토할 가치가 있다. (제안일 뿐, 이슈로 등록하지 않았다.)

## 5. 보드 — 확인 완료

`gh auth refresh -s read:project,project` 후 읽음. 보드 `1 aelix agent dev`(195건):
Done 99 / Backlog 84 / In progress 4 / Ready 3 / reject 3 / In review 2.

Windows 9건은 전부 보드에 있었다. **#104만 `Done`, 나머지 8건은 `Backlog`** — 최우선인
#103도 그랬다. 이 세션에서 **#103을 `Ready`로** 올리고 신규 4건을 등록했다.

⚠️ **스크립팅 함정 2건 (보드 결함 아님):**
1. `gh project item-list`의 **item-level `title`이 낡는다** — #105·#106·#107·#108에서
   8/08 제목 변경이 반영되지 않았다. `content.title`은 최신이다. 웹 UI는 라이브 제목을
   보여주므로 CLI 출력만의 문제다.
2. `gh issue view --json projectItems`는 스코프가 없으면 **에러가 아니라 빈 배열**을
   돌려준다. 이걸 믿으면 "보드에 없다"고 잘못 결론 낸다. GraphQL로 직접 쳐야
   `INSUFFICIENT_SCOPES`가 뜬다.

## 6. 착수 순서

1. ~~문서 정리~~ **완료** — #110 갱신, #200~#203 등록, 보드 반영.
2. **N-3 (stdout 인코딩)** — #110 P7. CI보다 먼저. 안 넣으면 첫 CI 결과가 인코딩
   노이즈에 파묻힌다.
3. **P0-a (#103 전반)** — `pyproject.toml` 4곳에서 `OS Independent` 제거 +
   README/README.ko에 Platform support 섹션. **릴리스 표기 결정이라 오너 확인 필요.**
4. **P0-b (#103 후반)** — `ci.yml` 매트릭스에 `windows-latest` + `continue-on-error: true`.
   🔴 **첫 실행은 빨간 게 정상이다.** F-3·F-4·#46·#201·#203·S-4·S-8은 "조용히 틀린"
   쪽이라 green을 통과한다.
5. **실측 숫자를 #110에 기록.** 여기서부터 일정 대화가 가능하다.
6. 이후: **#200 → #108 F-4 → #105 세 번째 사이트 → #202 → #107 후반 → #201 → #46 →
   F-3/F-5/F-6 → #106 실행 검증 → P7(S-4/S-8).**
7. **병렬 트랙 — #204 (PowerShell/cmd 분류기).** P0의 숫자를 기다리지 않는다. 완료
   정의의 3번이고 셋 중 가장 크므로, 위 순서와 **동시에** 시작하는 편이 낫다.

## 7. 오너 결정이 필요한 것

1. 🔴 **`v0.1.0` 마일스톤** — 마감 **2026-08-29가 지났고 open 11 / closed 0**이다.
   Windows 이슈 중 마일스톤이 있는 것은 **#103뿐**이고 신규 4건도 없다. 마일스톤을
   재조정할지, Windows 전용 마일스톤을 팔지는 릴리스 계획이라 손대지 않았다.
2. **P0-a는 릴리스 표기 결정** — `OS Independent` 제거는 "Windows 미지원"의 공식화다.
3. ~~"완전한 지원"의 정의~~ → **확정됨 (2026-09-03, 오너).** §0 참조. #110 본문에 기록했다.

## 8. 재현 명령

```bash
grep -rn "OS Independent" --include=pyproject.toml . | grep -v .claude/worktrees   # 4
grep -rni "windows" .github/workflows/                                             # 0
grep -rn 'reconfigure' --include='*.py' packages/*/src/ src/ | grep -v test        # 0
grep -rn "preexec_fn" --include='*.py' packages/*/src/ | grep -v test              # 2 스폰 사이트

# F-4 (grep 경로 손상) — 기대: ('src/app.py:12: hit', True)
.venv/bin/python -c "from aelix_coding_agent.tools.grep import _relativize_rg_line as R; \
  print(R(r'C:\Users\me\proj\src\app.py:12:hit', r'C:\Users\me\proj', is_directory=True))"

# 커버리지 스냅샷
grep -rn 'platform="win32"' tests/ --include='*.py' | wc -l    # 13 (주입형)
grep -rn 'skipif.*win32' tests/ --include='*.py' | wc -l       #  5 (차감형)
```

N-2(#201)는 `prompt_toolkit/input/win32.py`가 import 시 `assert sys.platform == "win32"`를
걸므로 `ast`로 `_is_paste`만 떼어 실행해야 한다. 메서드 본문을 순진하게 dedent하면
`IndentationError`로 죽는다 — 소실된 8/21 세션이 정확히 거기서 멈췄다.
