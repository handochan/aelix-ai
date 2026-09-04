# 다음 세션 핸드오프 — windows 141 → 5, 이슈 14건 닫음 (2026-09-04)

`handoff-209-205-2026-09-04.md`의 후속. 한 세션에서 `handoff-windows-clusters-2026-09-04.md` §1의 클러스터를 #207·#206만 남기고 전부 닫았다(#211 포함, 10건). 전부 windows 레그 실측으로 닫았고, 각 이슈 코멘트에 run/job ID와 `comm` 명령이 있다.

## 0. 기준점

| | |
| --- | --- |
| `main` | `56f618e` (#206까지; 배치 A = #219 → #218 → #203 → #206 순서로 ff). push 됨. #207은 worktree `/tmp/wt-207` 브랜치 `fix/207-rpc-lifecycle-win32`에서 Workflow 진행 중이었다 |
| 로컬 스위트 | `175532a`에서 **9387 passed / 12 skipped / 0 failed** — darwin `/tmp` realpath 실패가 #210으로 사라져 이 세션에서 처음 완전 초록 |
| windows 실측 궤적 (py3.11) | 141 → 125(#209) → 109(+#205) → 106(#208) → 101(#213) → 96(#214) → 96(#216) → 94(#215) → 77(#210) → 65(#212) → 51(#211) → 배치 A 병렬: 38(#219)·35(#218)·32(#206) 각자 → **main `56f618e` 합산 5 failed / 9337 passed / 67 skipped** (run 33850722642 · job 100952662483; py3.12 레그도 5). 남은 5건 = #207 그대로, tabbed flake는 #206 폴링으로 소멸 |
| 닫은 이슈 | #209 #205 #208 #213 #214 #216 #215 #210 #212 #211 + 배치 A: #218 #219 #203 #206 (14건) |
| 남은 클러스터 | **#207**(rpc 5) — 오너 결정 (a): (1)(2)(3)(5) win32 skip + (4) `os.fork` 픽스처를 Popen으로 포팅. Windows soft-kill 자체는 #202 트랙 |

## 1. 바로 시작할 것

1. **#207 마무리** — `/tmp/wt-207`의 Workflow 결과(있으면) 확인 → 커밋 → push → 레그 측정(기준: main 5건, job 100952662483) → rebase/ff/close. 초록이면 **windows 레그 0 failed**.
2. **#103 CI 게이트 승격** — `.github/workflows/ci.yml`의 windows `continue-on-error: true` 두 곳(잡 레벨 `:38`, pyright 스텝 `:83`)을 내린다. 그 전에 windows 레그를 main에서 2~3회 더 돌려 flake 0을 확인(tabbed는 #206으로 소멸했지만 다른 flake가 있는지). pyright 스텝은 windows에서 실제로 통과하는지 로그 확인 필요(“advisory” 상태라 아무도 안 봤다).
3. 그 다음은 오너 정의의 ②③: #106 `install.ps1` 실기 잡, #204 PowerShell/cmd 분류기(ADR-0237) — 배치 B/C. #202는 #207 (a) 결정으로 나중.

잔여 5건의 정본 목록: `gh api repos/handochan/aelix-ai/actions/jobs/100952662483/logs`에서 `FAILED tests/` grep (전부 #207의 5개 id).

## 2. 이 레포에서 이번에 물린 것 (이전 handoff §4에 더해)

0. **배치 병렬(CLAUDE.md 3번 완화, 2026-09-04)이 실제로 됐다.** worktree 3개 + Workflow 3개 + windows 레그 3개 동시 → 4이슈(46건)를 한 번의 대기 시간에. 물린 것: (i) `uv sync`는 `--all-packages`를 붙여야 `aelix-server`가 들어온다(안 붙이면 `tests/server` 수집 에러·pyright 5 errors가 worktree에서만 난다). (ii) **worktree에서 `cd`한 뒤 `git merge --ff-only`/`git push origin main`을 치면 그 worktree의 브랜치에 적용된다** — 두 번 당했다. ff는 반드시 메인 트리(`/Users/handochan/dev/aelix-ai`)에서. (iii) `git branch -f main <sha>`는 main이 체크아웃된 트리에서는 거부된다 — 메인 트리가 clean·idle이면 `git merge --ff-only <sha>`가 답. (iv) `gh issue close`는 코멘트가 붙은 것을 `gh issue view --json comments`로 확인한 뒤 **별도 명령으로** — `&&` 체인 뒤 heredoc 다음 줄에 두면 체인이 끊겨도 실행된다(#215, #219 두 번 코멘트 없이 닫혀 재오픈).

1. **`&&` 체인 뒤에 heredoc + 별도 줄 명령을 붙이지 말 것.** #215에서 `merge-base --is-ancestor && branch -f && push && gh issue comment <<EOF … EOF` 다음 줄의 `gh issue close`가 체인 밖이라, ff 검사가 실패해 머지·코멘트는 안 됐는데 **이슈만 닫혔다.** 재오픈 → worktree에서 rebase → 재머지로 수습. close는 코멘트가 실제로 붙은 것을 확인한 뒤 따로 실행한다.
2. **브랜치를 main에서 자를 때 main이 이미 앞서 있으면 ff가 안 된다.** #215 브랜치는 #216 머지 전에 잘려서 `git branch -f main <sha>`가 거부됐다. 워크플로우가 트리를 쓰는 동안은 `git worktree add /tmp/wt-<n> <branch>`에서 rebase하고, 메인 `.venv`의 python으로 테스트(`/Users/handochan/dev/aelix-ai/.venv/bin/python -m pytest`).
3. **워크플로우 agent가 트리를 편집하는 동안 `git checkout`을 하지 말 것.** ref만 옮기는 `git branch -f main <sha>` + `git push origin main`은 안전하다(#210·#215에서 사용).
4. **citation-drift 게이트**(`tests/test_citation_drift.py`, `citations.lock.json`). 테스트 파일에 줄을 **삽입**하면 다른 파일이 `path:line`으로 인용한 줄이 밀려 ubuntu 게이트가 빨개진다(#210에서 `test_ep_manifest.py:1085`→`:1117`). 수리: `uv run python scripts/check_citations.py --fix`(인용 파일 + lockfile 수정). 워크플로우 프롬프트에 이 규칙을 넣은 뒤(#212·#211)로는 agent가 스스로 확인한다.
5. **반박 리뷰어(opus)가 실제로 잡은 것들** — 모델을 내려 쓰지 말 것: #213 가드가 POSIX에서 항진명제(`_write`를 bare로 바꿔도 초록), #210 `parametrize` 안 `realpath`가 collection 시점 평가라 Windows에서 드라이브가 다름, #212 `Path.exists` 패치가 `str()` 비교라 Windows에서 `\bin\bash`, #215 blanket skip이 doctrine 위반. 전부 "실행해서" 잡았다.
6. **skip 결정은 오너에게 묻는다**(#215 → (c), #211 → 초안). 둘 다 `tests/conftest.py`의 "genuinely unreachable rather than merely untested" carve-out에 해당하고, 적용은 **PER PAYLOAD**(테스트가 아니라 단언 단위). 새 마커: `escalation_reachable`(`test_print_channel_spawn.py`), `posix_modes_only`/`assert_mode`(`tests/posix_modes.py`).
7. **`tests/tui/test_context.py::test_tabbed_*` 두 테스트는 run마다 다르게 flaky**(#206 코멘트에 표). 실패 목록 diff의 ±1~2는 이것.
8. **windows 러너 사실(실측)**: PATH에 Git-Bash `bash.exe` 있음(#216 skip 안 탐, skipped 61 그대로), `_resolve_shell`은 pwsh로 해석(#212 `test_win32_prefers_pwsh` 초록), `python3`도 있음(#212 본문 추정 → 훅 테스트가 `sys.executable`로 바뀌어 더는 의존 안 함).
9. **Codex는 9/7 14:29까지 usage limit.** 이 세션의 10개 변경 전부 Codex 교차 리뷰 없이 Claude 2-lane(opus 반박 + sonnet 실행)만. 각 이슈 코멘트에 명시.

## 3. 반증된 것 — 다시 믿지 말 것

- ❌ "Windows에서 `test_sigterm_then_sigkill`의 `status`/summary 단언도 조정이 필요할 수 있다"(#215 본문 추정) — per-assertion 가드로 돌리니 **통과**. timeout envelope는 Windows에서 성립, 도달 불가한 것은 SIGKILL leg뿐. #202에 기록.
- ❌ "#214 5건은 TOML만 고치면 다 초록" — 본문 자신이 예고한 대로 `test_stdio_server_with_shell_exec_still_spawns`는 게이트 단언은 통과하고 `/bin/sh` 스폰에서 실패. (a) 버킷으로 이관.
- ❌ "`git+file://` 기대값을 `parametrize`에서 `realpath`로 계산하면 된다"(#210 (3)) — collection 시점 평가라 Windows에서 워크스페이스 드라이브가 붙는다. 본문에서 `test_target_source_key_of_a_file_url_normalises_the_path`로 분리.
- ❌ "141 − 닫은 이슈 합 = 잔여" — flake 때문에 run마다 ±1~2. 각 run의 `comm` 결과가 정본.

## 4. 변하지 않은 금지 사항

🔴 PUBLIC 저장소. `.omc/specs/recovery-report-dead-session-91.md`와 `docs/assets/*` 10건은 여전히 **의도적으로 미커밋**. `git add -A` 금지 — 이 세션도 파일을 명시해서 add 했다(워크플로우 agent들에게도 프롬프트로 금지).
