---
name: aelix-tests-that-bite
description: Use when writing or reviewing a test for a fix or a behaviour change in aelix-ai, before claiming the test guards anything. Covers proving the test fails without the fix (on the base commit, in a tree with its own venv), running mutants on the changed lines with this skill's scripts/mutants.py, the Windows and Python 3.11 and 3.14 traps that only CI shows (clock ties, cp1252, path reprs, wait_for, shield, recursion), the coarse-clock pytest plugin, reproducing races with their real trigger, and keeping tests out of the developer's ~/.aelix.
---

# aelix-tests-that-bite

## When to use

Use this whenever you add or change a test, and whenever you review one. A test that
passes with and without your change documents the change. It does not guard it
(CONTRIBUTING, "Tests are not optional here"). In this repository, the tests that
went wrong mostly did so in one of three ways: they never failed on the base, they
measured a different tree than the one they meant to, or they passed locally and
failed only on a CI leg.

## Procedure

1. **Reproduce on the base first.** Write the test before the fix where you can, and
   run it on the base commit. Record how it fails: the assertion and the value. "It
   fails" is not enough, because a test can fail for an unrelated reason, such as an
   import error.
2. **Make the fix, and see the test pass.**
3. **Show it bites.** Revert only the production hunk and see the test go red, or run
   the new test file in a worktree of the base:
   ```bash
   git worktree add --detach /tmp/<n>-base <base-sha>
   (cd /tmp/<n>-base && uv sync --frozen --all-packages)
   cp tests/<path>/test_new.py /tmp/<n>-base/tests/<path>/
   (cd /tmp/<n>-base && .venv/bin/python -m pytest -p no:cacheprovider -q tests/<path>/test_new.py)
   ```
   A copy needs its own venv. The workspace packages are editable installs, so a copy
   run with your checkout's interpreter imports your checkout's code. Check with
   `python -c "import aelix_ai; print(aelix_ai.__file__)"`.
4. **Run mutants on the lines you changed.**
   ```bash
   python .agents/skills/aelix-tests-that-bite/scripts/mutants.py \
     --spec mutants.json --tree . --check-import aelix_agent_core \
     --log /tmp/<n>/mutants-log.json -- python -m pytest -p no:cacheprovider -q -x <test files>
   ```
   Each mutant is one exact replacement on one changed line: flip a comparison, drop
   a guard, return early, swap a branch. The script runs the baseline first. It sets
   `PYTHONDONTWRITEBYTECODE=1`, deletes the mutated file's cached bytecode, restores
   the saved bytes, and checks their sha256. Run it in a tree you are not editing
   (commit first, or use a detached worktree with its own venv). Report
   `killed/total` and name every survivor. Re-run each survivor by hand, and against
   every test file that exercises the symbol, before calling it a gap.
5. **Run the platform passes your change needs** (the decision rules say which):
   ```bash
   uv run --frozen python scripts/preflight.py --pythons 3.11,3.14 --windows-clock --docker
   ```
   By hand, a separate environment per interpreter leaves your `.venv` untouched:
   ```bash
   env -u VIRTUAL_ENV UV_PROJECT_ENVIRONMENT=/tmp/<n>-py311 \
     uv run -q --python 3.11 --frozen python -m pytest -p no:cacheprovider -q <test files>
   ```
   The coarse Windows clock is a pytest plugin you load by name:
   ```bash
   uv run python -m pytest -p tests.plugins.coarse_clock -p no:cacheprovider -q <test files>
   ```
6. **Keep the test out of `~/.aelix`.** A test whose code resolves the agent dir
   isolates it. A test that runs the CLI calls `sandbox_home` from
   `tests/env_sandbox.py`. A test that builds harness options puts its module on the
   `own_agent_dir` fixture
   (`pytestmark = pytest.mark.usefixtures("own_agent_dir")`). A child process whose
   environment you build by hand gets `child_env(home)`. The session root is pinned
   for every test by `tests/conftest.py`. A guard fails any test that touches
   `~/.aelix`, with a `#458/#497` message. Do not turn it off.
7. **`git add` the new test file** before running the gates.

## Decision rules

- **The code orders two instants, or the test waits on a timer:** run it under the
  coarse clock and on 3.11. Ask what a tie means, and assert on the code's own clock
  readings rather than on slack.
- **A helper waits for a task:** make it cancel the task on its way to failing, as
  `tests/harness/_cancelled_turn.py` `settle` does. A turn left held blocks
  `asyncio.run`'s teardown, which cancels once and waits. The failure then shows as
  the CI job's whole time limit, under the next test's name.
- **A callback reads state the test fills in:** fill it before the first `await`.
  3.11's `wait_for` adds loop turns.
- **You wait on a future without cancelling it:** use
  `asyncio.wait({fut}, timeout=...)`, not `wait_for(shield(fut))`, when it can be a
  gather (3.14).
- **You test a `RecursionError` branch:** force it with a monkeypatch. Never pin it
  from real deep input.
- **A fixture needs an illegal Windows name, a trailing space, or `..` after a
  symlink:** keep the logic assertion on every platform and skip only the on-disk
  part, with a reason. Copy a sibling test's platform marks.
- **A subprocess reads text:** `encoding="utf-8", errors="replace"`, never a bare
  `text=True`. **A fixture needs an absolute path:** use `tmp_path`, never
  `Path("/abs")`.
- **Someone claims a race and your probe does not reproduce it:** ask for their exact
  trigger and run that. A race that lives in one loop turn is invisible to a probe
  that lands one turn early. Record "not reproduced with <precondition>" as data,
  never as "refuted".
- **A TUI key race shows only in CI:** loop the scenario N times inside one pytest
  process. Then wrap `app.key_processor.feed` and `_call_handler` to log which handler
  took each key. Pin the fix with a deterministic test that feeds the key in the same
  loop turn as the trigger.
- **A test fails once under load:** rerun it alone and record both counts. One
  failure on a busy machine is not evidence either way.

## What bit this repo

- 2026-10-28 (#609): a mutation loop reverted with `git checkout -- shell.py` and
  silently dropped an uncommitted docstring edit in the same file.
- 2026-10-31 (#627 verify): two mutants of one file, of equal size, written within
  one second. The second run loaded the first mutant's `.pyc` and reported "2
  passed". Rerun by hand, it failed 2.
- #334, 2026-09-25: a test passed three lane rounds and an independent verify, all on
  3.12, and failed on ubuntu py3.11 with `KeyError: 'waiter'`.
- PR #615 (#604): three TUI tests failed on ubuntu py3.14 only, from a shielded gather
  the loop logged.
- #672 and #673, 2026-11-04: the Windows 3.11 and 3.12 jobs failed one test, then hit
  the step limit at 46%. The coarse-clock plugin reproduced both on macOS in one run.
- #330, 2026-09-25: a probe "could not reproduce" a lane's race in 21 runs, and the
  finding was nearly dropped. With the lane's own trigger it reproduced 4 of 12, and
  was filed as #351 (P1).
- PR #696, 2026-11-05: a modal Esc race failed 1 of 25 separate local runs and 11 of
  12 when looped in one process.
- #434 and PR #639: full local suites were green, and every Windows leg failed. See
  `references/platform-traps.md`.

## Pointers

- `references/platform-traps.md`: each Windows and Python-version trap, with its issue.
- `scripts/mutants.py` (this skill): the mutation runner; its docstring has the rules.
- `tests/plugins/coarse_clock.py`: what the coarse clock reproduces, and what it does
  not.
- `CONTRIBUTING.md`, "Keep tests out of your home's `~/.aelix`": the full isolation
  text.
- `tests/env_sandbox.py` (`sandbox_home`, `child_env`) and
  `tests/session_root_guard.py` (what the guard judges and what it misses).
- `docs/development/ci-hang-diagnostics.md`: the 240 s per-test faulthandler and the
  step limits.
