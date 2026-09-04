# 다음 세션 핸드오프 — windows 141 → 0, 레그 게이트 승격, 이슈 17건 닫음 (2026-09-04)

`handoff-209-205-2026-09-04.md`의 후속. 한 세션에서 `handoff-windows-clusters-2026-09-04.md` §1의 클러스터를 #207·#206만 남기고 전부 닫았다(#211 포함, 10건). 전부 windows 레그 실측으로 닫았고, 각 이슈 코멘트에 run/job ID와 `comm` 명령이 있다.

## 0. 기준점

| | |
| --- | --- |
| `main` | `58a8b5e` (#207 `beffc2f` → #103 세 커밋 `76e6fc4` `9e59cd4` `58a8b5e`). push 됨. worktree 전부 정리됨 |
| 로컬 스위트 | `175532a`에서 **9387 passed / 12 skipped / 0 failed** — darwin `/tmp` realpath 실패가 #210으로 사라져 이 세션에서 처음 완전 초록 |
| windows 실측 궤적 (py3.11) | 141 → 125(#209) → 109(+#205) → 106(#208) → 101(#213) → 96(#214) → 96(#216) → 94(#215) → 77(#210) → 65(#212) → 51(#211) → 배치 A 병렬: 38(#219)·35(#218)·32(#206) 각자 → **main `56f618e` 합산 5 failed / 9337 passed / 67 skipped** (run 33850722642 · job 100952662483; py3.12 레그도 5). 남은 5건 = #207 그대로, tabbed flake는 #206 폴링으로 소멸 |
| 닫은 이슈 | #209 #205 #208 #213 #214 #216 #215 #210 #212 #211 + 배치 A: #218 #219 #203 #206 + #207 #103 **#109** (17건) |
| windows 레그 | **게이트**(`continue-on-error` 제거). run 33856112601 @ `58a8b5e`: windows py3.11/3.12 type gate 0 errors + 9338 passed / 71 skipped, 잡 success. 연속 3회 clean |

## 1. 바로 시작할 것

오너 정의(#110)의 ①은 끝났다. 남은 ②③은 파일이 안 겹치니 **한 배치에 병렬**:

1. **#106 `install.ps1` 실기 e2e** — windows-latest 잡을 하나 붙여 스크립트를 실제로 실행(릴리스 다운로드·SHA256SUMS·uv 설치까지). 실행 이력 0이라 첫 run에서 무엇이 나올지 모른다 — 측정 먼저.
2. **#204 PowerShell/cmd 분류기** — ADR-0237(본문이 번호를 예약해 둠) → 설계 → 구현 → 테스트 → `uv run aelix`로 라이브. 가장 긴 막대(2~3세션). bash 문법기에 PowerShell을 먹이면 `Remove-Item -Recurse -Force C:\`가 ALLOW로 나오는 문제라 **문법 확장이 아니라 별도 분류기**.
3. **#200 확인 후 닫기** — `rpc_client.py:395`의 `preexec_fn` 가드 + #209 이후 real-child 테스트 통과가 근거. 실제로 위임 스폰이 Windows에서 되는지 한 번 확인.
4. 그 뒤 #202(+#207 (1)(2) 재활성화), #107, #108 F-3~6, #46, #201.

주의: 이제 windows 레그가 **게이트**다. 새 `fcntl`/`/`/`select()`가 main에 못 들어간다 — 반대로 Windows-model pyright도 게이트라 `pty`/`termios`/`fcntl` 이름을 쓰면 `# pyright: ignore[reportAttributeAccessIssue]`(f8654ce·76e6fc4 형태)가 필요하다.

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
9. **Codex는 9/7 14:29까지 usage limit.** 이 세션의 변경 전부 Codex 교차 리뷰 없이 Claude 2-lane(opus 반박 + sonnet 실행)만. 각 이슈 코멘트에 명시.
10. **반박 리뷰어가 문서의 거짓 주장을 잡았다** — #103 README 초안 "type gate was clean at beffc2f"(실제로는 15 errors, `continue-on-error`가 step 결론을 success로 가려 검증된 것처럼 보였음). advisory 스텝의 `gh run view` conclusion은 믿지 말고 로그의 `FAIL:`을 볼 것.
11. **Windows 러너의 pyright는 호스트 플랫폼 모델로 돈다.** POSIX 전용 이름 15건이 거기서만 error. `pythonPlatform=Linux`로 덮지 않기로 했다(ci.yml 주석) — Windows arm의 타입 오류를 잡는 유일한 게이트라서.
12. **`docs/guides/*.md`는 wheel에 번들된다.** 고치면 `uv run python scripts/sync_bundled_docs.py`로 사본을 재동기화해야 `test_docs_bundle_sync`가 통과한다.

## 3. 반증된 것 — 다시 믿지 말 것

- ❌ "Windows에서 `test_sigterm_then_sigkill`의 `status`/summary 단언도 조정이 필요할 수 있다"(#215 본문 추정) — per-assertion 가드로 돌리니 **통과**. timeout envelope는 Windows에서 성립, 도달 불가한 것은 SIGKILL leg뿐. #202에 기록.
- ❌ "#214 5건은 TOML만 고치면 다 초록" — 본문 자신이 예고한 대로 `test_stdio_server_with_shell_exec_still_spawns`는 게이트 단언은 통과하고 `/bin/sh` 스폰에서 실패. (a) 버킷으로 이관.
- ❌ "`git+file://` 기대값을 `parametrize`에서 `realpath`로 계산하면 된다"(#210 (3)) — collection 시점 평가라 Windows에서 워크스페이스 드라이브가 붙는다. 본문에서 `test_target_source_key_of_a_file_url_normalises_the_path`로 분리.
- ❌ "141 − 닫은 이슈 합 = 잔여" — flake 때문에 run마다 ±1~2. 각 run의 `comm` 결과가 정본.

## 4. 변하지 않은 금지 사항

🔴 PUBLIC 저장소. `.omc/specs/recovery-report-dead-session-91.md`와 `docs/assets/*` 10건은 여전히 **의도적으로 미커밋**. `git add -A` 금지 — 이 세션도 파일을 명시해서 add 했다(워크플로우 agent들에게도 프롬프트로 금지).
