# 0026. Workspace-Root Pytest Layout

Status: Accepted (Sprint 2 shipped)
**Amended 2026-10-14 (#458): the shared `tests/conftest.py` gives every test its
own session root unless a wider-scoped fixture set a non-empty one, which stays
in effect until that fixture is torn down, and a best-effort guard
fails a test that does one of a listed set of writes under the developer's real
one in process. See `## Amendment (2026-10-14, #458)`.**

## Context

Pi는 각 `packages/*`가 자체 `vitest.config.ts`를 보유하는 per-package test
layout을 사용합니다. Pi의 JavaScript/TypeScript 생태계에서는 이 구조가
자연스럽습니다.

Aelix Sprint 2 spec §A는 workspace-root 단일 `tests/` layout을 선택했습니다.
이 결정의 명시적 근거가 필요합니다.

### 고려한 대안

1. **Per-package tests** (`packages/aelix-agent-core/tests/`,
   `packages/aelix-ai/tests/`): Pi와 구조적으로 일치하지만 Python pytest
   생태계에서 cross-package fixture 공유가 어렵습니다.
2. **Workspace-root shared `tests/`** (채택): 단일 testpaths로 모든 패키지를
   커버합니다.

## Decision

Aelix는 workspace-root 공유 `tests/` layout을 유지합니다.

이유:

1. **Cross-cutting fixture 중복 방지**: Sprint 1의 `test_loop_with_hooks` 등
   기존 tests가 `aelix_agent_core` + `aelix_ai` 양쪽을 import합니다.
   per-package split 시 fixture가 중복되거나 shared conftest가 필요해집니다.
2. **pytest 단일 testpaths**: pytest는 `[tool.pytest.ini_options] testpaths = ["tests"]`
   선언 하나로 모든 workspace 패키지를 across 테스트할 수 있습니다.
   vitest와 architectural mismatch가 아닙니다.
3. **Pi parity acceptance test 자연스러운 위치**: ADR-0029의 `tests/pi_parity/`
   lane은 cross-package fixture (`aelix_agent_core` + `aelix_ai` 동시 사용)를
   활용합니다. workspace-root가 자연스러운 홈입니다.

## Consequences

- Phase 3 coding-agent-only tests는 `tests/` 아래에 agent-core tests와 나란히
  위치합니다. 개념적 분리는 디렉토리 명명으로 처리합니다 (예:
  `tests/coding_agent/`).
- `uv run pytest` 단일 명령으로 workspace 전체를 테스트합니다.
- Phase 3에서 전체 tests 수가 ~400개를 초과하면 per-package split을 재고합니다.
  그 시점에 새 ADR로 갱신합니다.
- Pi와 다른 **의도된 divergence** (additive, 행동은 동일합니다).
  Pi의 per-package vitest는 JavaScript 생태계 idiom이고, Aelix의 workspace-root
  pytest는 Python 생태계 idiom입니다.

## Amendment (2026-10-14, #458): the suite isolates the session root itself

With one shared `tests/` run by a bare `uv run pytest`, nothing wraps the run.
Whatever isolation the suite has must live in the shared `tests/conftest.py`.
That conftest never set `AELIX_CODING_AGENT_SESSION_DIR`. The isolation most
CLI tests applied, `AELIX_CODING_AGENT_DIR` and `AELIX_SETTINGS_PATH`, moves
settings and auth but not sessions. So every test that ran the CLI without
sandboxing HOME wrote a `--<pytest tmp path>--` directory into the developer's
real `~/.aelix/sessions`. The owner's machine had about 7,450 of them, 492 from
a single day, and the stats dashboard counts each one as a project. Measured on
`529b997e` with a scratch HOME, a full run (16,701 tests) left six. All six came
from `tests/cli/test_tools_flag_validation.py`, whose CLI children were given
`dict(os.environ)`.

1. **Each test has its own session root. This pin is the fix.** The autouse
   `_isolated_session_root` points the variable at `<one temp dir>/<n>`. It
   covers in-process code that reads the variable and every child that inherits
   the test's environment (`env=None`, `dict(os.environ)`), which is the shape
   every leaking test had. Neither the root nor the run's `aelix-s-*` directory
   is created until something writes a session, and that directory is removed
   when the run ends. The root is per test, not per run, so one test's
   `--continue` or session list cannot find another test's sessions. The run
   also gets a root of its own (`_run_session_root`), because a module- or
   session-scoped fixture is set up before any test's pin. With a scratch HOME,
   the packaging gate's wheel builds start children at that point. The pin is a
   default: it applies only when, at the test's start, the variable is unset,
   empty or still the run's root. An empty value counts as unset because
   `get_session_dir()` reads it so, and a child under it would fall back to
   `~/.aelix/sessions`. So a module-, class- or session-scoped fixture that set
   the variable to a value reaches its tests and their children. That value
   stays in effect until the fixture is torn down: for the rest of its module or
   class, and for a session-scoped fixture that is not autouse, every test that
   runs after it until the run ends, whether or not that test requests it. A
   test that sets the variable itself wins, because it uses the same
   `monkeypatch` and the last write wins. An unconditional per-test pin, as
   review round 2 had it, overrode such a fixture's value. Every root lives
   under one short directory in the system temp dir
   (`aelix-s-<8 random hex>`), not under pytest's basetemp. A session
   path is the root, then a bucket that encodes the whole cwd, then a
   67-character file name (72 with its `.lock`), and Windows' `MAX_PATH` is 260.
   On a runner the root is at most 59 characters as configured.
   `C:\Users\runneradmin\.aelix\sessions` was 36, and a root under basetemp
   would be about 80.
2. **A best-effort guard fails a test that does one of a listed set of writes
   under the real root in process.** `tests/session_root_guard.py` adds an
   audit hook that judges these operations in the test process, by the path the
   call is given: a write-mode `open` (`w`, `a`, `x` or `+`; for `os.open`, a
   write or create flag), `mkdir`, `rename` / `replace` (either end), `remove`,
   `rmdir`, `chmod`, `link` and `symlink` (the new name), `truncate`, and
   `shutil.rmtree` of the root or of a path inside it. It is a net, not a
   proof. It misses at least a rename of an ancestor such as `~/.aelix` or the
   home and, on POSIX, an `rmtree` of one (there `rmtree` deletes through paths
   relative to a directory descriptor, which the guard does not judge; on
   Windows it deletes by full path, so the guard would see it once it reaches
   the root, reasoned from CPython and not measured), `utime` or `touch` of an
   existing file, `chflags`, `mkfifo`, `sqlite3`, a socket bound there,
   and a path that reaches the root through a symlink outside it; each was
   measured changing a stand-in root unseen. The protected roots are
   `~/.aelix/sessions` under the start-of-run home, as given and with the home
   resolved; the same under the password database's home (POSIX); and an
   exported `AELIX_CODING_AGENT_SESSION_DIR`, as given and with its parent
   resolved. A root that is the temp dir or contains it is not protected,
   because every `tmp_path` and every per-test root lives there. On macOS and
   Windows paths are compared without case, as APFS and NTFS compare names. The
   hook blocks the operation and fails the test in teardown, even when the
   code under test caught the error. That is aimed at in-process code that
   ignores the variable:
   `JsonlSessionRepo()` with no `sessions_root`, which `aelix-server`'s `/rpc`
   builds when a `TestClient` runs it in process. The guard never opens, lists
   or stats a protected root. It judges the operation, so the developer's own
   aelix, running alongside the suite, cannot make it flaky. It watches a
   test's setup, call and teardown, so a module- or session-scoped fixture is
   outside it, and only the run's root covers such a fixture.
3. **Children are not judged.** Neither part covers a child given an
   environment built by hand without the variable, a child started through a
   shell, `env` or `uv run` wrapper that drops it, or an `aelix-server` child.
   That server's `/rpc` builds `JsonlSessionRepo(fs=fs)` with no
   `sessions_root` and ignores the variable, a product bug left to a follow-up.
   The suite's hand-built child environments sandbox the home
   (`tests/env_sandbox.py` `child_env`), and that keeps them out. Review round 1
   also judged children by their argv and environment, and that was withdrawn.
   It misread the CLI's flags (a repeated `--session-dir`, `--session-dir ""`,
   `--no-session` given as a value), missed wrapped and `os.posix_spawn`
   children, and let an `aelix-server` child through. It also blocked the MCP
   SDK's stdio servers whenever the checkout or temp path contained `aelix_`:
   from such a checkout 13 MCP tests failed and 13 errored where the parent
   passed all 32.

**Divergence from pi** (ADR-0235: recorded, not argued). pi isolates the
whole run in `test.sh`: it uses `env -i` with a temporary `HOME` and
`USERPROFILE` (pi `e66e3e588`), outside the test runner. Aelix's suite runs
under a bare `uv run pytest`, so the isolation is per test and inside the
runner. The guard has no pi counterpart.

**Not done here.** The guard protects the session root only. With a scratch
HOME, four in-process tests opened `~/.aelix/agent/auth.json` for writing and
`chmod`ed `~/.aelix/agent`: three in `tests/cli/test_entry_router.py` and one
in `tests/cli/test_session_flags.py`. Widening the guard to `~/.aelix` waits for
those to be fixed. Two product gaps stay open: `aelix-server`'s `/rpc` ignores
the variable (above), and the CLI does not read `settings.json`'s `sessionDir`,
which pi does. Directories that earlier runs left behind are not removed
(their names contain `pytest-of-`). Removing them is the owner's call.

Tests: `tests/test_suite_session_isolation_458.py` covers this end to end
through the real conftest. Its child pytest proves that a test writing a real
root in process fails and writes nothing, that a child inheriting the pin
writes under it, and that the run removes its roots.
`tests/test_session_root_guard_458.py` covers each part of the guard.
