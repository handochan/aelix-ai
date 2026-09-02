# Windows 공식 지원 일정 및 로드맵 — 재검토 (2026-08-22)

2026-08-21 16:12–18:51 세션(`e9ddc410`)이 같은 제목으로 서브에이전트 57개를 돌리다
컨테이너 종료로 소실됐다. 산출물이 트랜스크립트에만 있었으므로 전부 유실. 이 문서는
그 검토를 재실행한 결과이며, 모든 판정은 HEAD(`2821b7f`)에서 실측한 것이다.

## 한 줄 결론

**#110의 로드맵은 실행이 앞서고 문서가 뒤처졌다.** P1·P1b·P2·P3·P4의 상당 부분이
`feat/windows-experimental-slice`로 이미 main에 들어와 있는데 Windows 이슈 8개 전부
**코멘트 0개**다. 반면 **P0(#103)은 한 줄도 손대지 않았다.** 로드맵이 P0를 첫 걸음으로
지목한 판단은 여전히 옳고, 지금은 그때보다 더 옳다 — 뒤따르는 코드가 이미 들어와 있어서
검증할 것이 생겼기 때문이다.

## 1. P0–P7 대조표

| P | 이슈 | #110의 기재 | 2026-08-22 실측 | 판정 |
|---|---|---|---|---|
| **P0** | #103 | 표기 정직성 + `windows-latest` CI | classifier 4개 그대로, `runs-on` 5개 전부 ubuntu | ❌ **미착수** |
| P1 | #104 | bash 셸 해석 | `_resolve_shell_win32` 이행. 이슈 **CLOSED 8/08** | ✅ 완료 |
| P1b | #105 | 프로세스 트리 kill | `tools/_process_tree.py`가 2/3 사이트 커버 | 🟡 부분 |
| P2 | #106 | `install.ps1` | 파일 + 패리티 테스트 존재, **실행 이력 0** | 🟡 부분 |
| P3 | #107 | RPC stdin 펌프 | 스레드 펌프 이행, `entry.py` readiness 게이트 미이행 | 🟡 절반 |
| P4 | #108 | F-1…F-6 | F-1/F-2 완료, **F-3·F-4·F-5·F-6 잔존** | 🟡 부분 |
| P5 | #46 | `msvcrt.locking` | 두 스토어 모두 미이행 (`msvcrt` 0건) | ❌ 미착수 |
| P6 | #109 | 스위트 그린 | 선행조건 W1 완료, 본체 미착수 | 🟡 선행조건만 |
| P7 | #110 | 폴리시 5건 | 1건 moot, 4건 잔존, **신규 3건** | 🟡 |

근거 커밋 (`feat/windows-experimental-slice` → `4de2d4f` beta-wave2 머지):

```
c2ab62b test(windows): sandbox HOME through a cross-platform helper (#109, #103)
42ec1fd fix(bash): resolve a Windows shell and force ASK when it is not bash (#104)
1d05191 fix(bash): recognise version-suffixed POSIX shells; document the AUTO change
c59a790 fix(tools): win32-safe process-tree kill at both owned spawn sites (#105)
c9242cc fix(rpc): pump stdin from a thread on Windows (#107)
0a451df feat(install): experimental Windows installer at checksum-gate parity (#106)
a201dba fix(install): pin the exact version in install.ps1 too (#106, B2 hijack)
58af48f fix(security): fold path components before matching the write guardrails
```

리포 루트의 `SLICE-STATUS.md`가 이 슬라이스의 자체 상태 문서다. 정직하고 정확하다 —
다만 **GitHub 쪽에는 한 글자도 반영되지 않았다.**

## 2. #110 본문에서 이제 틀린 것

1. **"셸 명령 실행이 stock Windows에서 매 호출 실패"** — #104로 해소됐다. 이제
   `$SHELL`(실존하는 경로일 때만) → `pwsh` → `powershell` → `%COMSPEC%` → `cmd.exe`로
   해석된다. 다만 대가가 있다: PowerShell은 bash 문법 분류기가 읽을 수 없으므로 AUTO
   모드가 **강제 ASK로 강등**된다. "동작한다"와 "쓸 만하다" 사이다.
2. **P7의 image-protocol 프로브 항목** — `tui/images.py`는 #163(ADR-0223)에서 제거됐다.
   어느 플랫폼에서도 인라인 이미지를 렌더하지 않는다. **Moot.**
3. **#46의 "Action: msvcrt.locking 폴백"** — 이슈 자체 STATUS가 폐기 처리했다. 두 fcntl
   사이트(`oauth/auth_storage.py:177`, `settings/storage.py:198`)는 None-guard라
   **크래시하지 않는다**. 조용히 락을 안 잡을 뿐이고, 그래서 더 나쁘다.

## 3. 로드맵에 없는 항목 (신규)

### N-1 · `preexec_fn=pdeathsig` — Windows에서 raise

`aelix_agents/print_channel.py:970`, `aelix_agents/rpc_channel.py:438`이
`preexec_fn=pdeathsig`를 넘긴다. `preexec_fn`은 POSIX 전용이라 Windows에서
`subprocess`가 거부한다. **멀티에이전트 위임 스폰 경로 전체가 죽는다.**
README.md:231이 이미 "delegation is unsupported on Windows"라고 적어둔 것과 일치하지만
이슈로는 어디에도 없다. `SLICE-STATUS.md`가 "다른 트랙 소유"로 남겨둔 항목이다.

### N-2 · 한 줄 대용량 붙여넣기는 Windows에서 접히지 않는다 (실측)

소실된 세션이 **죽기 2분 전에 붙잡고 있던 지점**이다(`last-tool-error.json` 18:48:59가
`prompt_toolkit/input/win32.py`의 273·292행을 뽑아낸 상태로 남아 있다). 재현했다:

`Win32Input._is_paste`는 `newline_count >= 1 and text_count >= 1`일 때만 참이다. 즉
Windows에서 `Keys.BracketedPaste`는 **개행이 포함된 붙여넣기에만 합성된다.**

| 붙여넣기 | win32가 paste로 인식 | aelix 접기 대상 | 실제 |
|---|---|---|---|
| 6줄 코드 블록 | True | True | 접힘 ✓ |
| **한 줄 minified JSON (1200자)** | **False** | **True** | **핸들러 미발화 → 접기 불가** |
| 짧은 한 줄 | False | False | 문자 단위 삽입 |
| 2줄 짧은 텍스트 | True | False | 그대로 삽입 ✓ |

`chrome.py`의 `_PASTE_COLLAPSE_MIN_CHARS = 1000` 갈래는 docstring이 명시하듯
"single very long line (e.g. minified JSON)"을 위한 것인데, 그 입력은 Windows에서
애초에 BracketedPaste가 되지 않는다 → **이슈 #81의 절반이 Windows에서 도달 불가능한
죽은 코드다.** 50KB 한 줄은 문자 단위로 에디터에 쏟아진다(리드로 비용은 실기 측정 필요).

부수 관찰(미검증): `chrome.py`가 `Ctrl+V`를 "클립보드 이미지 붙여넣기"에 바인딩하는데,
Windows Terminal에서 `Ctrl+V`는 터미널 레벨 기본 붙여넣기라 앱까지 오지 않을 수 있다.
실기 없이는 확정 불가.

### N-3 · stdout 인코딩

`sys.stdout.reconfigure(encoding="utf-8")`을 부르는 곳이 없다. Windows 콘솔은 레거시
코드페이지가 기본이라 비ASCII 출력과 TUI 박스 문자가 mojibake 또는 `UnicodeEncodeError`가
된다. **CI를 켜기 전에 처리해야 한다** — 안 그러면 무관해 보이는 실패 100건으로 보인다.

## 4. #108 잔존분 — 실측

- **F-3 세션 디렉터리 grant 사망** — `builtin/permission.py:244`가 여전히
  `os.path.normpath(path.replace("\\","/"))` 후 `"/" in norm`을 본다. Windows에서
  `ntpath.normpath`가 `/`를 `\`로 되돌리므로 검사가 실패 → 항상 정확-경로 pin.
  fail-closed라 안전하지만 기능은 죽는다.
- **F-4 grep 출력 손상 — 실행으로 확인:**

  ```
  $ python3 -c "from aelix_coding_agent.tools.grep import _relativize_rg_line as R; \
      print(R(r'C:\Users\me\proj\src\app.py:12:hit', r'C:\Users\me\proj', is_directory=True))"
  ('C:\\Users\\me\\proj\\src\\app.py:12:hit', False)          # 기대: ('src/app.py:12: hit', True)
  ```

  `for sep in (":", "-")` 루프가 **드라이브 문자 콜론을 먼저** 잡아 `candidate == "C"`가
  되고 base 매칭에 실패한다. 슬래시 방향과 무관하게 두 표기 모두 실패한다.
  `relativize_to_posix`를 import해두고도 이 경로에서 쓰지 못한다.
  파급: 모델이 절대경로를 받고, `is_match=False`라 `match_count`가 오르지 않아
  `limit` / "matches limit reached" 로직이 **도달 불가능한 죽은 코드**가 된다(50KB 캡까지 누적).
- **F-5 skills 메타데이터** — `harness/skills.py:504/517`의 `_dirname`/`_basename`이
  여전히 `/`만 안다.
- **F-6 `file://` 카탈로그** — `cli/extension_catalog.py:573`에 `url2pathname` 없음.

## 5. 그래서 일정은

**"Windows 공식 지원"까지의 잔여를 날짜로 약속할 수 없다.** 실기 러너에서 한 번도
돌려본 적이 없어서, #109가 예측한 실패 85~110건이 실제로 몇 건인지 아무도 모르기
때문이다. 이 숫자를 모르는 상태의 모든 일정은 추정이 아니라 희망이다.

약속할 수 있는 것은 **P0까지**다. 그 다음은 P0가 만들어내는 숫자를 보고 잡는다.

### 지금 P0가 하루짜리인 이유 (그리고 8/16 감사 때와 달라진 점)

감사가 못박은 P0의 선행조건 — "`setenv("HOME")` 24건을 CI 켜기 전에 먼저" — 은
**이미 끝났다**(`c2ab62b`, W1: 9개 파일 25개 사이트). 게다가
`tests/test_env_sandbox_windows.py:107`이 `git grep -n 'setenv("HOME"' -- tests/`를
돌려 회귀를 막는 가드 테스트까지 달았다. 지금 raw `setenv("HOME")`은 헬퍼와 그 자체
테스트 밖에 0건이다.

즉 **P0를 막던 유일한 blocker가 해소된 상태이고, 아무도 그걸 모른다.**

### 권고 순서

1. **P0-a (수 시간)** — `pyproject.toml` 4곳에서 `Operating System :: OS Independent`
   제거. README/README.ko에 Platform support 섹션 신설(현재 Windows 언급은 delegation
   한 줄뿐). 이건 릴리스 태그 결정과 묶여 있으므로 소유자 확인 필요.
2. **P0-b 직전 위생 (반나절)** — N-3(stdout 인코딩)을 먼저 넣는다. 안 넣으면 첫 CI
   결과가 인코딩 노이즈에 파묻힌다.
3. **P0-b (수 시간)** — `ci.yml:33-37` 매트릭스에 `windows-latest` +
   `continue-on-error: true`. **첫 실행은 빨간 게 정상이다.** 첫 green을 마일스톤으로
   삼지 말 것 — 위 항목 중 F-3·F-4·N-2·#46은 "조용히 틀린" 쪽이라 green을 통과한다.
4. **실측 숫자를 #110에 기록.** 여기서부터 비로소 일정 대화가 가능해진다.
5. 그 다음 우선순위 제안: **N-1(위임 전면 사망) → #108 F-4(모델 입력 오염) →
   #105 reaper 세 번째 사이트 → #107 후반부 → N-2 → #46 → F-3/F-5/F-6 → #106 실행 검증.**

### 먼저 해야 할 문서 작업 (코드 아님, 30분)

- **#110 본문 갱신** — §2의 오류 3건 정정, 대조표 반영. 지금 상태로는 이걸 집어드는
  사람이 **이미 끝난 일을 다시 한다.**
- **#104를 로드맵 표에서 CLOSED로 표시**, #105/#106/#107/#108/#109에 슬라이스 커밋
  링크 코멘트.
- **N-1·N-2·N-3을 이슈로 등록** — 셋 다 어느 이슈에도 없다.
- `SLICE-STATUS.md`를 #110에서 링크.

## 6. 현재 커버리지

| 지표 | 슬라이스 이전 | 2026-08-22 |
|---|---|---|
| Windows-asserting 테스트 | 0 | `platform="win32"` 주입 13건 |
| 차감형 `skipif win32` | 12 | 5 |
| win32 언급 테스트 파일 | — | 11 |
| 실제 win32 러너 실행 | 0 | **0** |

마지막 줄이 바뀌기 전까지 나머지는 전부 스냅샷이다.

## 부록 · 재현 명령

```bash
# P0 미착수 확인
grep -rn "OS Independent" --include=pyproject.toml .   # 4건
grep -rn "windows" .github/workflows/                  # 0건

# F-4 (grep 경로 손상)
python3 -c "from aelix_coding_agent.tools.grep import _relativize_rg_line as R; \
  print(R(r'C:\Users\me\proj\src\app.py:12:hit', r'C:\Users\me\proj', is_directory=True))"

# N-1 (preexec_fn)
grep -rn "preexec_fn" --include='*.py' packages/*/src/ | grep -v test

# #109 선행조건 완료 확인
grep -rn 'setenv("HOME"' tests/    # env_sandbox.py 와 그 자체 테스트뿐
```

N-2는 `prompt_toolkit/input/win32.py`가 import 시 `assert sys.platform == "win32"`를
걸어두므로 `ast`로 `_is_paste`만 떼어 실행해야 한다. (소실된 세션이 이 지점에서
`IndentationError`로 죽었다 — 메서드 본문을 순진하게 dedent하면 깨진다.)
