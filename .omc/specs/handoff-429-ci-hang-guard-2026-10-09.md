# Handoff — #429 CI hang guard (2026-10-09)

## 기준점

- main64b82af7/codea0a9c328. Branchfix/429-ci-hang-guard, isolated codex-429 worktree.
- Scope: bounded CI lint/test jobs and pytest steps, per-test timeout/forcedexit,
  post-summary watchdog. Completion root cause is separate #428.
- CI test job Ubuntu25/Windows50min; pytest step20/45min. Pytest tests240s,
  interpreter-exit watchdog180s. Development minimumpytest9.0; locked9.0.3.
- No production dependency or runtime behavior changed; no new ADR required.
  Developer documentation: docs/development/ci-hang-diagnostics.md.

## 검증과 독립 리뷰

- `uv run pytest tests/test_ci_hang_guard_429.py tests/test_ci_python_matrix.py tests/test_ci_uv_pin.py -q`:
  16passed in4.77s, process exit0.
- `uv run ruff check .`: All checks passed.
- `uv run python scripts/check_types.py`:287files,0errors; inverse spike3errors retained.
- `uv run python scripts/check_citations.py --check`:951gated,none drifted.
- Independent freshcontext reviewer copied the patch into a separate detached
  worktree; no source change requested and no blocking finding.
- Python3.12.13/pytest9.0.3:4tests passed4.25s.
- Python3.11.15/pytest9.0.0/--strict-config:4tests passed3.89s.
- 18additional realpytest subprocess cases: setup/call/teardown and post-summary
  queueworker hangs exit1 with stacks; normal exit0; forcedexitfalse and disabled
  plugin respect disabledwatchdog. Removing finalhook left1passed then an
  indefinitely waiting interpreter until the independent3s wallkill.
- Reviewpatch SHA256a38928037b4399dd98e1fc02f200b29930d310e26242ade6c1779e2e1fbdadca.
- Full local Python3.12 suite is running at initial handoff publication, output
  .omc/probes/429/full-suite-py312.out. Remote Windows/Linux matrix remains pending.
  Exact final suite/CI results belong in the PR before marking merge-ready.

## 바로 시작할 것

- Finish localfullsuite and remote8jobCI; ensure both defaultwatchdogs exit cleanly
  on successful jobs and Windows hangs produce stacks in new regressiontests.
- Move Project1 toInreview with PR; merge stays owner-controlled per applied
  aelix-issue-to-pr skill. No automatic main merge or ownerauth/settings changes.
- #428 implementation is separately locked in codex-428. Rebase its final issue
  onto this change once the preceding branch is approved/merged.

## 반증된 것

- Merely setting faulthandler_timeout logs the hang but does not fail the test.
  exit_on_timeouttrue is required, especially forPython3.11testteardown.
- A passing pytestsummary does not prove process exit; the finalwatchdog has its
  own actualblocked-thread regression and sabotage.
- The oldguardpatch omittedexit_on_timeout and did not raisepytest>=8floor.
- Pytest9.0.0 supports this option; this was verified from upstreamsource and
  actualminversion execution rather than assumingthelocked9.0.3isrequired.

## Windows CI repair and full local suite

- Initial source commit6a70349f local Python3.12 fullsuite:
  15222passed,25skipped,104warnings,878.16s,exit0.
- PR430 initialCI37927935852 Windows3.11 job113811296522 failed only
  test_a_green_summary_cannot_hide_a_stranded_thread. The child exited with a
  watchdog traceback but stdout held only the progress line, not the buffered
  final summary; production timers behaved as intended.
- The subprocess driver now uses Python `-u` so abrupt watchdog exit cannot
  discard the summary. Only the test's accelerated final-clock is lengthened
  from0.2to1.0s to allow pytest cleanup before the deliberately blocked join.
  Production watchdogs remain240/180seconds and CI25/50minutes unchanged.
- Final head must get a fresh platformCI; the initial fullsuite is bounded
  evidence for the production changes, followed by a focused test-driver recheck.
