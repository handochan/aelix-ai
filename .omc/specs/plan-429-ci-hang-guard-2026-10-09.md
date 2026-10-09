# #429 — bounded CI and pytest hang diagnostics

Base: main 64b82af7, code a0a9c328. Issue owner: handochan. Project 1: In progress.

Scope: CI lint/test job and pytest step wall limits, per-test timeout with
nonzero exit, post-summary interpreter-exit watchdog, regression tests and docs.
Completion root cause remains #428. No production runtime or dependency changes.

Reference: Pi f1b2e77f5b13b2a199b1052cb79c235451afe7d7's CI uses a Node test
workflow and provides no Python watchdog to reuse. Pytest's 9.0.0
src/_pytest/faulthandler.py registers faulthandler_exit_on_timeout; the
development floor must therefore move from >=8 to >=9.0.

Acceptance:
- Ubuntu/Windows lint/test jobs are bounded at 25/50 minutes, pytest at 20/45.
- A hang in a test call or fixture teardown emits thread stacks and fails.
- A green test summary followed by a non-daemon queue worker emits stacks and fails.
- A normal focused/full run exits normally; disabling forced exit also disables
  the final watchdog for intentional debugging.
- Current matrix/uv pins, lint/type/citation gates, independent review and CI pass.

Verification: tests/test_ci_hang_guard_429.py drives real pytest subprocesses,
copies the actual repository conftest and shortens only watchdog clocks. It does
not substitute a mock watchdog implementation. The job timeout configuration is
checked alongside the existing matrix and uv-pin gates.
