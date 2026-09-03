# 다음 세션 핸드오프 — Windows P0 (2026-09-03)

`main` = `24f7f7e` (origin과 동일). 계획 정본 = `.omc/specs/windows-support-prep-2026-09-03.md`.
스위트는 **green이 아니다** — macOS에서 `12 failed / 9250 passed / 12 skipped`,
12건 전부 플랫폼 이식성이고 실제 결함은 0이다. 숫자를 그대로 믿지 말고 §5를 먼저 읽을 것.

---

## 0. 시작 전 3분 (건너뛰지 말 것)

```bash
git fetch origin --quiet
git rev-parse --short origin/main       # 24f7f7e 가 아니면 아래 전부 재확인
```

🔴 **오너가 세션을 동시에 여러 개 돌린다.** 기록상 한 세션 도중 `main`이 두 번 움직인 적이
있다(`2ba3261` → `ef2286b` → `51d26fa`). 오래 도는 팬아웃 뒤에는 검증을 돌린 커밋이 아직
HEAD인지 반드시 대조할 것.

**"Windows 완전 지원"의 정의는 2026-09-03에 오너가 확정했다.** 셋을 **모두** 만족해야 한다:

| # | 조건 | 현재 |
|---|---|---|
| 1 | **#109** — 스위트가 실기 Windows에서 green | 실행 **0회** |
| 2 | **#106** — `install.ps1`이 실기 러너에서 end-to-end 성공 | 실행 **0회** |
| 3 | **#204** — AUTO 모드가 ASK로 강등되지 않음 | 강등 중 |

정의는 #110 본문 상단에도 기록돼 있다.

---

## 1. 바로 시작할 것 — N-3 (stdout 인코딩)

**#103보다 먼저다.** `sys.stdout.reconfigure(encoding="utf-8")`을 부르는 곳이 리포 전체에
**0건**이다. Windows 콘솔은 레거시 코드페이지가 기본이라 비ASCII 출력과 TUI 박스 문자가
mojibake 또는 `UnicodeEncodeError`가 된다.

🔴 **이걸 먼저 넣지 않으면 첫 CI 결과가 인코딩 노이즈 100건에 파묻힌다.** 실패 개수를
읽으려고 CI를 켜는 것이므로, 노이즈를 먼저 제거해야 그 숫자가 의미를 갖는다.

이슈는 따로 없다 — **#110의 P7 절이 인라인으로 추적**한다(`modes/print_mode.py` 항목).
P7은 스스로 "별도 이슈 미생성 — 여기서 추적"이라고 밝힌다.

```bash
grep -rn 'reconfigure' --include='*.py' packages/*/src/ src/ | grep -v test   # 0
```

## 2. 그 다음 — #103 (P0), 보드에서 이미 `Ready`

**P0를 막던 유일한 blocker는 이미 해소돼 있고 아무도 모른다.** 8/16 감사가 못박은
선행조건(`setenv("HOME")` 24건)은 `c2ab62b`(W1: 9파일 25사이트)로 끝났고,
`tests/test_env_sandbox_windows.py:107`이 `git grep`으로 회귀까지 막는다.

- **P0-a (수 시간)** — `pyproject.toml` **4곳**에서 `Operating System :: OS Independent`
  제거 + README/README.ko에 Platform support 섹션 신설.
  🔴 **릴리스 표기 결정이라 오너 확인이 필요하다.** "Windows 미지원"의 공식화다.
- **P0-b (수 시간)** — `ci.yml:33-37` 매트릭스에 `windows-latest` +
  `continue-on-error: true`.
  🔴 **첫 실행은 빨간 게 정상이다. 첫 green을 마일스톤으로 삼지 말 것** —
  #108 F-3·F-4, #46, #201, #203, P7의 S-4·S-8은 "조용히 틀린" 쪽이라 green을 통과한다.
- **실측 숫자를 #110에 기록.** 여기서부터 비로소 일정 대화가 가능하다.

## 3. 병렬 트랙 — #204 (P0의 숫자를 기다리지 않는다)

완료 정의 3번이고 셋 중 가장 크다. `permission.py:624`가 `is_classifiable_shell`로
게이트하고 `_CLASSIFIABLE_SHELLS`에 `pwsh`/`powershell`/`cmd`가 없어서, Windows AUTO
사용자는 **모든 명령에 승인 프롬프트**를 본다.

🔴 **강등을 되돌리는 작업이 아니다.** `bash_classifier.py:77-87`이 이유를 적어뒀다 —
bash 문법에 PowerShell을 먹이면 `Remove-Item -Recurse -Force C:\`가 **ALLOW로 나온다.**
미탐지가 아니라 적극적 오권한 부여다. 필요한 건 **PowerShell/cmd 전용 분류기**이고,
ADR-0158의 "가장 엄격한 판정이 이긴다" 합성 규칙을 유지해야 한다. ADR이 따라붙는다.

🔴 정정 (2026-09-03): 위 `Remove-Item` ALLOW 사례는 실측 결과 거짓이다 — 그런 이름은
테이블에 없어 ASK로 떨어진다. 실측된 오권한은 반대 모양이다: `date`·`sort`처럼 알려진
읽기 전용 이름의 인자를 안 읽어 cmd 하에서 ALLOW로 새는 것(0be16cd 이전). 근거:
`bash_classifier.py:83-95`, `0be16cd`.

## 4. 이후 순서

**#200 → #108 F-4 → #105 세 번째 사이트 → #202 → #107 후반 → #201 → #46 →
#108 F-3/F-5/F-6 → #106 실행 검증 → #110 P7(S-4 `%APPDATA%` / S-8 `Ctrl+G`).**

#200이 맨 앞인 이유: `preexec_fn=pdeathsig`가 Windows에서 raise해 **위임 스폰 경로가
통째로 죽는다**(`print_channel.py:970`, `rpc_channel.py:438`).

## 5. 이번 세션에서 반증된 것 (다시 믿지 말 것)

1. 🔴 **"N-1·N-2·N-3 셋 다 어느 이슈에도 없다"(8/22 handoff §3) — 틀렸다.**
   #110의 P7 절이 **N-3(stdout 인코딩)·S-4(`%APPDATA%`)·S-8(`Ctrl+G`)를 처음부터
   인라인 추적**하고 있었다. 진짜 미등록은 3건이었고 전부 등록했다(#200 #201 #202).
   **선행 문서의 "이슈 없음" 주장은 #110 본문을 직접 열어 확인할 것.**
2. **`grep 'Ctrl+G'`는 오탐을 낸다.** `+`가 정규식 메타문자다. `grep -F`로 재라.
   이 함정이 위 1번 오류를 한 번 더 재생산할 뻔했다.
3. **macOS의 12건 실패는 결함이 아니다.** 11건이 `/proc/self/fd`(Linux 전용 procfs,
   `test_client_lifetime_over_completed_turns.py:516`), 1건이 `/tmp`→`/private/tmp`
   심링크. 11건은 **Windows에서도 같이 실패한다** — 정직하게 시끄러운 부류다.
4. **`reaper.py:98`의 `/proc` 사용은 버그가 아니다.** `if not _PROC.is_dir(): return []`로
   가드돼 있고 docstring이 macOS/Windows를 명시한다. 조사하다 시간 쓰지 말 것.
5. **보드는 낡지 않았다 — CLI 출력이 낡았다.** `gh project item-list`의 item-level
   `title`이 #105·#106·#107·#108에서 8/08 제목 변경을 반영하지 않는다. `content.title`은
   최신이고 웹 UI도 정상이다.
6. **#104는 "매 호출 실패"를 남기지 않았다.** 셸 실행은 고쳐졌다. 남은 간극은 AUTO→ASK
   강등(#204)이고, #110 본문의 옛 주장은 취소선으로 정정해 뒀다.

## 6. 이 레포에서 실제로 물린 규칙

1. **`.gitignore`와 각 `pyproject.toml`의 `exclude`는 함께 움직인다.** hatchling이
   `.gitignore` 줄을 자기 exclude 스펙에 이어 붙이므로 "무시되니까 안 실린다"가 성립하지
   않는다. 바꿨으면 `tests/packaging_gate/`로 실측하고, 가능하면 진짜 `uv build --sdist`로
   확인할 것(이번 세션 기준 9.3 MB, `.omc`/`.claude`/워크트리 유출 0).
2. **디렉터리 이름을 `.gitignore`에 넣을 땐 앵커할 것.** 앵커 없는 규칙은 워크트리 안에서
   돈 빌드의 루트 경로 자체와 매칭돼 hatchling이 파일 전체를 버린다(#143 전례).
3. **`.omc/specs/`는 커밋 대상이다.** `.gitignore`가 `!/.omc/specs/`로 열어뒀고 주석이
   이유를 적었다 — *P1의 계획은 unstaged로 남아 정확히 그렇게 유실됐다.* 현재 108건 추적 중.
4. 🔴 **이 저장소는 PUBLIC이다.** `.omc/specs/recovery-report-dead-session-91.md` **1건은
   의도적으로 미커밋 상태다** — 미완화된 cross-dist hijack 벡터를 재현 가능하게 서술하고,
   같은 문서가 "private vulnerability reporting 채널이 아직 없다"고 기록한다.
   **커밋하지 말 것.** `SECURITY.md`의 죽은 버튼을 먼저 열고 벡터를 고친 뒤가 순서다.
5. **`gh issue view --json projectItems`는 스코프가 없으면 에러가 아니라 빈 배열**을
   돌려준다. "보드에 없다"고 잘못 결론 내기 쉽다. GraphQL로 직접 쳐야
   `INSUFFICIENT_SCOPES`가 뜬다. 현재 토큰에는 `project` 스코프가 있다.
6. **워크트리는 `.claude/worktrees/` 아래에 있다**(현재 9개 + main = 10).
   `.gitignore`와 pyproject 5개 × exclude 2개가 이미 `.claude`를 막고 있어서 그 자리가
   유일하게 안전한 곳이다. 루트에 `.worktrees/`를 파면 10곳에 exclude를 더해야 한다.

## 7. 아직 열린 오너 결정

1. 🔴 **`v0.1.0` 마일스톤 — 마감 2026-08-29가 지났고 open 11 / closed 0.** Windows 14건 중
   마일스톤이 있는 건 **#103뿐**이고 신규 5건(#200~#204)은 없다. 완료 정의는 확정됐지만
   1·2번은 P0가 만드는 숫자에 의존하므로 지금 날짜를 박으면 희망이 된다.
   **#204만은 숫자와 무관해서 지금 마일스톤에 넣을 수 있다.**
2. **P0-a의 릴리스 표기** — §2 참조.
3. **PyPI 토큰 / GitHub PAT 로테이션** — `env.txt`가 평문으로 컨테이너 밖을 나왔다.
   커밋된 문서에 토큰 값은 없지만 노출 사실은 공개 기록으로 남아 있다.

## 8. 재현 명령

```bash
grep -rn "OS Independent" --include=pyproject.toml . | grep -v .claude/worktrees   # 4
grep -rni "windows" .github/workflows/                                             # 0
grep -rn 'reconfigure' --include='*.py' packages/*/src/ src/ | grep -v test        # 0
grep -rn "preexec_fn" --include='*.py' packages/*/src/ | grep -v test              # 2

# F-4 (grep 경로 손상) — 기대: ('src/app.py:12: hit', True)
.venv/bin/python -c "from aelix_coding_agent.tools.grep import _relativize_rg_line as R; \
  print(R(r'C:\Users\me\proj\src\app.py:12:hit', r'C:\Users\me\proj', is_directory=True))"

# 스위트 (약 4분)
.venv/bin/python -m pytest -q -p no:randomly     # 12 failed / 9250 passed 가 현재 정상
```
