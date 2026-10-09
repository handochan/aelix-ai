# Handoff — #428 single-worker file completion — 2026-10-09

## 기준점

- 기준 main/branch HEAD: `64b82af7ccbcb66e52eb9a1c2fca874508ee1cb0`.
- issue branch: `fix/428-offloop-completion`.
- worktree: `/Users/handochan/dev/aelix-ai-worktrees/codex-428`.
- issue lock: main common git directory의 `codex-issue-locks/428`.
- 제품 diff는 확정했으며 커밋하지 않았다. 원격/이슈/보드 쓰기는 root가 담당한다.
- root가 #429를 `6a70349f`, PR #430으로 게시했다. 이 #428 diff에 #429 변경은
  넣지 않았다. 최종 기반 이동은 root가 #429 이후에 수행한다.
- Python 3.11.15, prompt-toolkit 3.0.52. own `.venv` editable 경로가 own worktree를
  가리킨다. 메인 및 기존 5개 PR worktree는 수정하지 않았다.

## 구현

`OffLoopFileMentionCompleter`가 `FileMentionCompleter`만 감싼다. mention이 없으면
executor job을 제출하지 않고, 있으면 `asyncio.to_thread` 한 job이 capped menu를
수집한다. shell의 실제 input builder가 이 wrapper를 사용한다. `ThreadedCompleter`
producer/consumer pair를 제거해 시작 전 producer 취소가 queue consumer를 남기는
경합을 없앴다. `run_tui` teardown 정책은 바꾸지 않았다.

3개 source의 이전 설명, 기존 wiring test 설명, CHANGELOG, ADR-0193 amendment와
색인, ADR-0238의 역사적 wrapper 설명을 수정했다. 새 ADR 번호는 쓰지 않았다.
인용 수정은 `check_citations.py --fix`가 만든 source/test 주석과 lock 갱신이다.
`aelix-agent-core`/`docs/contracts` diff는 비어 있다.

## 실행한 검증

증거 정본: own `.omc/probes/428/verification.json`.

- 새 회귀: `.venv/bin/pytest -q tests/tui/test_completion_shutdown.py`
  → **17 passed in 5.67s, exit 0**.
- 변경 전 standalone worktree의 Python으로 똑같은 실제 builder double-cancel test를
  실행했다. `@sample`, cancel 2회, queued producer → child의 executor shutdown이
  15초 timeout되어 **1 failed in 15.66s**. 이 기대한 실패는 pytest를 멈추지 않았다.
- focused TUI + import/band 경계:

  ```bash
  env -u NO_COLOR TERM=xterm-256color .venv/bin/pytest -q \
    tests/tui/test_completion_shutdown.py tests/tui/test_completion.py \
    tests/tui/test_completer_wiring.py tests/tui/test_run_tui_smoke.py \
    tests/tui/test_update_notice.py tests/cli/test_p2_import_direction.py \
    tests/agents/test_p2_band_boundaries.py
  ```

  → **205 passed, 1 warning in 22.09s, exit 0** (`focused-cleanenv.out`).
- `.venv/bin/ruff check .` → **All checks passed!**
- `uv run --no-sync python scripts/check_citations.py --fix` 후 `--check`
  → **951 gated, none drifted**, ambiguous/generic anchor 19개는 recorded-only.
- `uv run --no-sync --python 3.11 python scripts/check_types.py`
  → **287 files analysed, 0 errors, 3 inverse spike 유지, exit 0** (`type-gate.out`).
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python --pythonplatform Windows`
  → **0 errors, 1 기존 SessionStorage covariance warning** (`pyright-windows.out`).
- `git diff --check` → 출력 없음.

직접 실행한 full suite, Windows runtime, Python 3.12/3.13 runtime 결과는 없다.
최종 live TUI/실제 모델/원격 CI는 root 작업으로 남아 있다. independent scratch
repro matrix와 live driver/guide도 `.omc/probes/428/`에 보존했다.

## 바로 시작할 것

1. root가 제품 diff를 새 컨텍스트 Claude로 독립 교차 리뷰한다. 제품 파일 쓰기를
   멈춘 상태로 넘겼다. 리뷰의 concrete input/실행 결과를 받아 결함만 고친다.
2. root가 `.omc/probes/428/LIVE-GUIDE.md`대로 실제 모델 TUI 확인을 수행한다.
   `live_tui_driver.py`는 py_compile/--help만 확인했고 자동 실행하지 않았다.
3. #429 최종 기반으로 이동하고 citation/필요한 gate를 재확인한다. root의 검토 승인
   뒤 #428만 커밋/PR로 내고, 자기 Windows CI를 확인한다.
4. 이슈를 닫기 전에 root가 문서/보드/실제 측정 결과를 함께 갱신한다.

## 이 레포에서 실제로 물린 규칙

- 오너 `~/.aelix`/`auth.json`을 건드리지 않는다. 테스트 자식은 agent/settings path를
  tmp에 격리하고, live driver는 scratch cwd까지 별도로 쓴다.
- live driver는 supplied `.env`에서 `OPENROUTER_API_KEY`만 선택하고 키를 출력하지
  않는다. raw capture에서도 그 값은 제거한다. 다른 `.env` 값은 자식에게 보내지 않는다.
- 장시간 pytest 중 같은 `.venv`를 resync하지 않는다. `uv run --no-sync`를 쓴다.
- CI는 `ruff format --check`를 의도적으로 강제하지 않는다. 큰 signature를 접는
  formatter 변경은 제품 수정과 섞지 않는다. 새/변경 범위의 스타일만 유지한다.
- 로컬의 bare pyright는 PATH에서 바깥 Python을 찾았다. own Python을 명시하거나
  uv 실행 환경에서 실제 `scripts/check_types.py`를 돌린다.

## 반증된 것 — 다시 믿지 말 것

- "FileMentionCompleter는 항상 모든 결과를 만든 뒤 yield한다"는 정확하지 않다.
  fuzzy arm은 그렇지만 directory arm은 항목별로 yield했다. #428은 directory menu도
  capped list가 완성된 뒤 한 batch로 보여 준다. 값/순서/표현은 동일하다.
- "single worker이면 실행 중 filesystem call도 취소된다"는 아니다. 이미 시작된
  walk/stat은 여전히 끝나야 한다. 이번 범위는 orphaned queue consumer 제거다.
- "기존 runner만 지연한 stress에서 fixA가 0 leak이므로 경쟁 조건을 많이 통과했다"
  는 아니다. fixA는 그 runner 자체를 없애 그 지연 대상이 0개다. 새 회귀는 실제
  builder와 event-gated executor를 통해 queued/running job과 natural process exit를
  직접 확인한다.
- "집중 테스트의 최초 색/렌더 실패는 #428 회귀다"는 아니다. tool 환경은
  `NO_COLOR=1`, `TERM=dumb`였다. 변경 전 baseline도 동일 환경에서 같은 2개가 실패
  (0.61s), clean env에서는 2 passed (0.93s); fixed clean env 전체는 205 passed다.
- "bare `.venv/bin/pyright`의 missing-import 192건은 제품 타입 오류다"는 아니다.
  외부 Python 경로 선택이었다. 정확한 gate는 287파일/0오류로 통과했다.

## Root live check and cross-review

- Fresh-context Claude Opus read-only cross-review exited0, no production correctness blocker found. It independently traced prompt_toolkit3.0.52 cancellation, producer/consumer Futures and completion contracts; it did not execute tests. Prompt/result retained .omc/probes/428-cross-review/.
- Low test-diagnostics finding was repaired: timeout/nonzero child failures now include stdout/stderr. Root reran new17regressions:17passed in2.22s. Product code is unchanged from the cross-reviewed version.
- Root directly executed uv run aelix in100x24PTY, scratch cwd/agent/settings, only OPENROUTER_API_KEY selected fromdotenv, no ownerauth/settings changes. Real openrouter/openai/gpt-4o-mini LIVE_OK response, slash/filemenus/Tab, /quit exit0 in0.123s.
- Delayed completion positivecontrol entered actual asyncio_0 worker before/quit, exit0 in0.432s. A running filesystem job is still joined; no claim of cancelling every blocked filesystem call.
- Root inspected menu/selection/emitted-response screens. Evidence .omc/probes/428/live-normal-r2/ andlive-delayed/. Initial live-normal run incorrectly required response to remain in the final screen and timedout despite emittedLIVE_OK; correcteddriver uses emittedbytes andcaptures the response frame, then verifies naturalexit.
- Publication should follow429; current branch will rebase onto its approved/predecessorcommit before final gates. Final CI remains pending.

## Publication base check

Branch rebased onto #429's corrected predecessor8a71c9bc. The only conflict was two independent CHANGELOG entries; both were retained. Range-diff showed only changelog context movement. Productfiles/tests-tui diff versus pre-rebasef4726b62 is empty. Citationsfix relocated0;951gated none drifted. On the predecessor combination, completion/wiring/menus+hangguard scope:98passed,1existing walkcapwarning,in5.37s. Root will publish a dependent PR against fix/429-ci-hang-guard so its review diff contains only #428 and its CI also has #429's guards.
