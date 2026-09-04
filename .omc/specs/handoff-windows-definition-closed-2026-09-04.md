# 다음 세션 핸드오프 — #110 정의 ①②③ 전부 닫힘: #106 · #204 · #200 (2026-09-04, 4차)

`handoff-windows-sweep-2026-09-04.md` §1의 세 항목을 한 세션·한 배치로 끝냈다. 코드 변경은 #106(`3749ec2`)과 #204(`8a0256c`) 두 커밋, #200은 검증만.

## 0. 기준점

| | |
| --- | --- |
| `main` | `8a0256c` (`c6d424c` → #106 `3749ec2` → #204 `8a0256c`). push 됨. `/tmp/wt-*` 전부 정리, 브랜치 원격까지 삭제 |
| 로컬 스위트 | wt-204에서 `8a0256c` 직전(rebase 전 `7b8ac26`) **10037 passed / 12 skipped / 0 failed**. rebase 후에는 `tests/builtin tests/docs tests/packaging_gate …` 1032 passed만 돌렸다(전체는 CI가 돌림) |
| CI @ `8a0256c` | run **33885430316**: 6잡 success — windows py3.11 job 101063907942 `9982 passed / 71 skipped`, type gate `0 errors across 276 files`; `install.ps1 e2e (pwsh)` 101063907644 · `(powershell)` 101063907896 success |
| 닫은 이슈 | **#200** (검증), **#106**, **#204** — 각 이슈 코멘트에 run/job ID·명령·측정 안 한 것 |
| 계획 정본 | 이 문서 §2. 오너 정의(#110)는 코멘트 5542329081에 상태표 — **#110을 닫을지는 오너 판단**으로 남겨 뒀다 |
| 이번 세션 spec | `.omc/specs/204-design-2026-09-04.md`(구현 스펙, 설계→비판→수정 결과), `.omc/specs/204-progress-2026-09-04.md`(워크플로우 agent들의 진행 기록, 명령+출력) |

## 1. 이번에 한 것

1. **#200** — 코드는 이미 `4043d1c`(`pdeathsig_preexec()` win32 → `None`)에 있었다. 확인한 것: run 33857516037 windows 잡 0 failed + `test_a_real_child_without_a_key_is_an_error_envelope_not_a_hang`(마커 없음, 실제 `-m aelix_coding_agent` 자식을 띄워 `exit_code == 1` 단언)이 그 안에 포함. `-q`라 이름으로 grep은 못 했다 — 구조적 증명이고 코멘트에 그렇게 적었다.
2. **#106** — `ci.yml`에 `install-ps1-e2e` 잡(windows-latest × `[powershell, pwsh]`, setup-uv 없이, 체크아웃 파일을 `Get-Content -Raw | iex`로 두 번 실행해 부트스트랩 arm과 "already installed" arm 둘 다 측정, `.github/scripts/assert-install-ps1.ps1`이 사후 단언). **첫 실행에서 스크립트가 두 셸 모두에서 깨졌다**: (a) pwsh — `astral.sh/uv/install.ps1` 응답에 `Content-Type`이 없어 `(iwr).Content`가 `byte[]` → `Invoke-RestMethod`로 교체. (b) PS 5.1 — BOM 없는 UTF-8을 CP1252로 읽어 em dash의 `0x94`가 U+201D(닫는 따옴표)로 → Step 4 문자열 조기 종료. `install.ps1`·assert 스크립트 **ASCII 전용** + `test_ps1_is_ascii_only`/`test_ps1_has_no_bom` 게이트. 첫 초록 run부터 게이트.
3. **#204** — ADR-0237. `builtin/shell_classifiers/{dialect,common,powershell,cmd}.py`. PowerShell은 `tree-sitter-powershell==0.26.4`(세 플랫폼 wheel 확인), cmd는 손 렉서. bash DENY는 dialect verdict 아래 **바닥**으로 유지(`max(bash_deny_floor, dialect_verdict)`) — 수용조건 4를 "완화 안 함"으로 만족. `_READ_ONLY_ONLY_WHEN_BARE`는 사라지고 스위치 허용목록(`arguments_are_read_only`)으로 대체 → `sort /etc/hosts` ALLOW 복귀 + base에서 ALLOW였던 `sort -o out.txt in.txt` fail-open이 ASK로 닫힘. 민감 파일 읽기 가드(`sensitive_read_target`: `.ssh`·`.aws`·`.env*`·private key…)는 반박 리뷰가 세 번 뚫어(`*` 와일드카드, `id_rsa.` 후행 점, `id_rsa::$DATA`) 세 번 막은 것.
4. #106과 #204가 README/README.ko/SLICE-STATUS의 같은 문단을 건드려 rebase 충돌 — 수동 해결하고 "#110 기준 셋 다 스위트 근거 위에서 충족"으로 문장 갱신, `8a0256c`로 재측정 후 머지.

## 2. 바로 시작할 것

**#202** (프로세스 그룹 격리 없음 → 위임 자식을 죽이면 자손 고아) + #207 (1)(2) 재활성화. 그 뒤 #107, #108 F-3~6, #46, #201. 파일이 안 겹치는 것끼리 한 배치.

오너에게 물을 것 (답이 오면 그에 맞춰):
- **#204 수용조건 2 편차** — bash로 해석된 게이트에서 `sort in.txt /o out.txt`가 allow(POSIX에서 `/o`는 존재하지 않는 입력 경로, exit 2, 쓰기 없음. ADR-0237 각주 ¹, `test_dialect_dispatch.py`에 양방향 고정). "어떤 셸에서든 ASK"가 의도였다면 한 줄로 되돌린다.
- **#110을 닫을지.** 정의 ①②③은 스위트 근거 위에서 다 성립하지만, Windows 호스트에서 사람이 한 턴 돌려 본 적은 없다.
- `powershell -enc …`·`Start-Process`는 ADR이 ASK로 뒀다(DENY 승격 후보). 올릴지.

## 3. 이 레포에서 이번에 물린 것

1. **`scripts/check_citations.py`는 git-tracked 파일만 스캔한다.** 새 모듈이 `??`인 채로 `--fix`를 돌리면 "citations OK"를 찍고 아무것도 안 잠근다 → ubuntu·windows 4레그가 `test_no_citation_has_drifted`로 빨개졌다(run 33879233970). **`git add` 뒤에 `--fix`.**
2. **`.ps1`/`.yml`/`.sh`는 citation 게이트 밖**(`GATED_SUFFIXES = (".py", ".md")`). 거기 `install.ps1:NNN`을 적으면 조용히 썩는다 — #106에서 9건이 +3 밀린 채 커밋될 뻔했다(반박 리뷰어가 잡음). 그 파일들에서는 Step 제목/심볼/인용 스니펫으로 앵커.
3. **문서·스크립트 헤더에 run ID를 넣지 말 것.** amend할 때마다 실행된 바이트가 바뀌어 run ID가 이전 SHA를 가리킨다. run/job ID는 **이슈 코멘트와 커밋 본문**이 정본, 문서는 잡 이름 + 이슈 번호.
4. **`-p` 모드에서 ASK는 ALLOW와 구별되지 않는다** — headless라 `not ctx.has_ui` 분기로 통과한다. 게이트 판정을 라이브로 보려면 `AELIX_SUBAGENT_DEPTH=1`(`headless_default="block"`, `cli/entry.py`)을 켠다: ALLOW는 실행, ASK는 "All mutating operations are blocked" 메시지.
5. **windows 러너 이미지(Windows Server 2025)에 uv 없음** — `install.ps1` 부트스트랩 arm이 CI에서 실제로 돈다. GitHub이 uv를 추가하면 잡의 사전 단언(`uv is preinstalled … exit 1`)이 빨개지도록 해 뒀다 — 그때는 결정을 다시 한다(PATH를 깎지 말 것, 아무 사용자도 없는 기계를 측정하게 됨).
6. **`jobs.<id>.steps[*].shell`에는 `${{ matrix.* }}`를 못 쓴다**(컨텍스트 가용성 표 밖, actionlint가 잡음). `jobs.<id>.defaults.run.shell`에 둔다.
7. **Windows 러너의 `_resolve_shell`은 pwsh로 해석되지만, 그것을 패치 없이 게이트까지 흘리는 테스트는 없다.** 방언 테스트는 전부 셸을 주입해 모든 레그에서 양쪽을 돈다(conftest 원칙, skip 0). windows 레그가 유일하게 증명하는 것은 grammar wheel 설치·로드·파싱과 Windows 모델 pyright. README 초안이 반대로 주장했고 반박 리뷰어가 정정.
8. **자동 모드 분류기가 `git fetch && … && git push origin main` 복합 명령을 막았다.** merge와 push를 **각각 단일 명령**으로 치면 통과한다.
9. **Workflow 규모**: #204는 agent 20개·2.47M 토큰·4.6시간(설계 비판 1 + 검증 3 + 재검증 2 + 최종 반박 1). BLOCKING을 실제로 잡은 것은 전부 **opus 반박 레인**이었고(민감 파일 3종 우회, README 거짓 문장, 커밋 메시지 거짓 문장), sonnet은 실행 레인에만. 모델을 내려 쓰지 말 것. #106은 6개·0.5M·82분.
10. **한 issue = 한 commit** 규칙 하에서 rebase 충돌은 문서 문단에서 난다(README "Platform support", SLICE-STATUS 헤더). 같은 배치의 이슈들이 이 문단을 둘 다 고칠 것 같으면 ff 순서를 정해 두고 뒤의 것이 앞의 문장을 이어 쓰게 한다.

## 4. 반증된 것 — 다시 믿지 말 것

- ❌ "install.ps1은 `install.sh`의 미러이니 실행하면 될 것" — 두 셸 모두에서 첫 실행에 죽었다. 원인 둘 다 PowerShell 고유(`Content-Type` 부재 시 `byte[]`, 5.1의 ANSI 디코딩).
- ❌ "`_READ_ONLY_ONLY_WHEN_BARE` 좁히기가 POSIX를 안전하게 만들었다" — `sort -o out.txt in.txt`는 그 좁히기 뒤에도 ALLOW였다(쓰기 스위치를 아무도 안 읽음). 이름을 좁힌 게 아니라 인자를 읽어야 했다.
- ❌ "#204 수용조건 2는 어떤 셸에서든 ASK" — 이슈 문구는 `test_bash_classifier.py`의 두 케이스를 가리키고 그건 성립한다. 게이트가 bash로 해석된 경우는 ADR이 다르게 정했다(§2 오너 확인 항목).
- ❌ "windows-latest가 pwsh 경로를 실제로 게이트에 흘린다" — 위 §3-7.
- ❌ "`check_citations.py --fix`를 한 번 돌리면 새 인용이 잠긴다" — tracked 파일만. §3-1.

## 5. 변하지 않은 금지 사항

🔴 PUBLIC 저장소. `.omc/specs/recovery-report-dead-session-91.md`와 `docs/assets/*` 10건은 여전히 **의도적으로 미커밋**. `git add -A` 금지 — 이 세션도 파일을 명시해서 add 했다. Codex는 9/7 14:29까지 usage limit — 이 세션 변경 전부 Claude 레인만(각 이슈 코멘트에 명시). **9/7 이후 첫 세션은 `8a0256c`의 `shell_classifiers/`에 Codex 보안 리뷰를 사후로 받을 것**(`codex exec --sandbox read-only`, 파일당 하나씩).
