---
name: aelix-preflight
description: Use before pushing a branch, opening or updating a pull request, or saying a change is done in the aelix-ai repository. Runs scripts/preflight.py, which picks the gates a diff needs (ruff, the pyright gate, the citation lock, the docs bundle, import direction, the complexity ratchet, the band rule, packaging, the sdist size cap, pi parity, the control-v1 floor, the scans every test or product file must pass, the touched tests, Windows pyright) and writes a claims block for the PR. Also covers how to read each red gate, when to add Linux docker, Python 3.11 and 3.14, or the coarse Windows clock, and the full-suite run that is still owed at the end.
---

# aelix-preflight

## When to use

Run this before every push and before you write "tests pass" anywhere. CI runs five
commands (`ruff check`, `scripts/check_complexity.py --check`,
`scripts/check_control_v1.py --check`, `scripts/check_types.py` and the full pytest
suite) on eight legs, two operating systems by four Pythons
(`.github/workflows/ci.yml`). Most red CI rounds in this repository
came from gates a contributor did not know their diff needed. A round costs from
15 minutes to over an hour of wall clock. `scripts/preflight.py` reads your diff and
runs the gates that diff touches, so you find these failures locally.

## Procedure

Run everything from the repository root (`git rev-parse --show-toplevel`).

1. **Stage new files first.** `git add` every new test file before you run any gate.
   The citation gate and the HOME-sandbox test read `git ls-files`, so an untracked
   test is invisible to them, and a green local run does not cover what the commit
   holds. Preflight's `untracked-tests` gate fails until you do this.
2. **See the plan.**
   ```bash
   uv run --frozen python scripts/preflight.py --plan
   ```
   It prints each gate it would run and the changed file that triggered it, and runs
   no gate and no other program: it only reads the change with git. `--docker` asks
   `docker info` when that gate runs, not while planning. `--frozen` keeps that one
   `uv run` from re-locking, which is the form that is safe in a checkout other
   sessions share. The changed set is your committed `base...HEAD` plus staged, unstaged and
   untracked files that are not ignored. The base defaults to the merge-base with
   `origin/main`; pass `--base REF` to change it.
3. **Run it.**
   ```bash
   uv run --frozen python scripts/preflight.py                       # the gates the diff needs
   uv run --frozen python scripts/preflight.py --pythons 3.11,3.14   # see the decision rules
   uv run --frozen python scripts/preflight.py --docker --windows-clock
   ```
   Exit 0 means every gate that ran passed, 1 means one failed, and 2 means a usage
   error. The summary names every gate as passed, failed or skipped, with the reason.
   A gate with nothing to run is skipped, never passed. Use `--only a,b` or
   `--skip a,b` to rerun a subset after a fix; `--only pythons` needs `--pythons`
   too, and without it the run stops with exit 2.

   `pythons` is the one gate that calls `uv`. Another interpreter needs its own
   environment, and `uv run --isolated --frozen --all-packages -p 3.X` builds a
   throwaway one from `uv.lock` without writing to `.venv`. Every other gate runs
   through the interpreter running preflight, because a nested `uv run` can resync
   `.venv` under a test run. `tests/test_preflight.py` checks both.
4. **Fix what is red** (see "Reading a red gate" below), then rerun from step 2.
   Preflight never changes your tree unless you pass `--fix-citations`.
5. **Write the claims block.**
   ```bash
   uv run --frozen python scripts/preflight.py --claims -               # print it
   uv run --frozen python scripts/preflight.py --claims /tmp/claims.md  # or a file outside the tree
   ```
   It records each gate's exact command, exit code, duration and the tail of its
   output, with no environment values. Paste it into the PR's "How it was verified"
   section. Do not write it inside the checkout: an untracked `claims.md` at the root
   is a new top-level path, so every later run also starts the packaging gate, and
   `git add -A` would commit it. A `--claims` path whose directory does not exist is
   a usage error before any gate runs.
6. **Run the full suite once, last.** Preflight never runs it, and prints it as the
   step you still owe:
   ```bash
   uv run pytest -p no:cacheprovider -q        # about 6 minutes or more on a laptop
   ```
   Start it last and touch nothing in that checkout until it ends. Any `uv run` in
   the same checkout while it runs can resync `.venv` under it. Do not run two
   suites on one machine at once (another session's suite included): they slow each
   other and make timing tests fail for no reason.

### In a git worktree

If your shell exports `VIRTUAL_ENV` (a maintainer shell often points it at the
main checkout's `.venv`), use `env -u VIRTUAL_ENV uv run --frozen ...`. Confirm the
tree once:

```bash
env -u VIRTUAL_ENV uv run --frozen python -c "import aelix_ai; print(aelix_ai.__file__)"
```

It must print a path inside the worktree you are gating. Run repository scripts by
their relative path from the worktree root: a script run by another checkout's path
gates that other checkout.

## Decision rules

- **You changed a POSIX-only or Linux-only function, or a test file that has
  `@linux_only` or `skipif(sys.platform ...)` marks:** add `--docker`. macOS skips
  Linux-only tests, so only a container runs them. Check the touched-test list that
  preflight prints. Every test file that calls the changed symbol
  (`grep -rl <symbol> tests/`) must be on it. If one is missing, run it in the
  container yourself.
- **Your change or test orders two instants, waits on a timer, or counts loop
  turns:** add `--windows-clock` and `--pythons 3.11,3.14`. Windows on Python 3.12
  and older steps `time.monotonic()` every 15.625 ms. CPython 3.11's
  `asyncio.wait_for` adds loop turns. 3.14's `asyncio.shield` logs a cancelled gather.
  The aelix-tests-that-bite skill has the traps.
- **You changed product `.py` files:** the `pyright-windows` gate runs. Local
  pyright analyses your own platform only. Guard POSIX-only `os.*` calls with
  `sys.platform != "win32"`, not with `hasattr`: pyright narrows on the first and
  not on the second.
- **You changed a `.py` under `packages/`, a path under `src/`, the root `pyproject.toml`, or a
  `packages/*/pyproject.toml`:** the `import-direction` gate runs `scripts/check_import_direction.py`
  (#292, #870).
- **You changed `rpc/control*.py`, `rpc/rpc_mode.py`, `tests/rpc/control_v1/`,
  `docs/contracts/control-v1.md`, `scripts/check_control_v1.py` or `scripts/check_complexity.py`:**
  the `control-v1` gate runs, including when a checker script is deleted (#871). It checks that the floor
  (`tests/rpc/control_v1/floor_v1.json`) still holds the floor at the merge base
  (`scripts/check_control_v1.py --check --base <base>`), then runs `tests/rpc/control_v1`, a
  real `--mode rpc` child included. Version 1 only grows (ADR-0302):
  - a new fixture or case says `"since": "#<issue>"`; run
    `uv run python scripts/check_control_v1.py --update` and commit the floor with it;
  - a `since: "v1"` case is frozen as written, so editing one fails the gate. A correction is a
    withdrawal (`--update --withdraw <method>:<case> --note <YYYY-MM-DD> --issue '#<n>'`) with a
    dated note on ADR-0302;
  - an addition leaves `V1_SHA256` in `tests/rpc/control_v1/test_additive_only.py` alone. If
    it moved, a v1 case was edited.
- **You changed a `.py` or `.pyi` under `packages/` or `src/`, a ruff config there or
  at the root, `complexity-baseline.json`, `uv.lock` or `scripts/check_complexity.py`:**
  the `complexity` gate runs `scripts/check_complexity.py --check --base <base>`, the
  ratchet CI runs on every leg with `--require-base --github-event` added (ADR-0300). A
  raised score fails it (RAISED), and so does a lowered one (STALE): run
  `uv run python scripts/check_complexity.py --update` and commit
  `complexity-baseline.json` with the change. It measures the files git tracks, so
  `git add` a new module first: a pass with one untracked prints the script's
  `UNTRACKED` note under the result.
- **You edited `docs/guides/`:** run `uv run python scripts/sync_bundled_docs.py`.
  The wheel ships a byte-identical copy that `tests/test_docs_bundle_sync.py` gates
  in both directions (ADR-0218).
- **You touched `.gitignore`, a `pyproject.toml`, `.gitattributes` or a new
  top-level path:** the packaging gate runs `tests/packaging_gate/`. Read the
  comments in `.gitignore` and the `exclude` blocks first. hatchling appends
  `.gitignore` to its exclude spec, so "ignored" does not mean "not shipped".
- **Any change:** the `sdist-size` gate builds the release sdist from the tracked files
  as they are on disk and checks it against its 6 MB cap. It passes only on the size
  the test reports, so a run that measured nothing (a skip, a deselection,
  `--setup-only`) fails it, and a pass prints the size and the headroom. Docs and code
  alone reached the cap twice, #387 and #816; the second time only the full suite
  caught it (#831).
- **You are about to write a measurement down as a reason** (in an ADR, a CHANGELOG
  line or a comment): check that the command you measured is the one the product
  runs. Installer paths in particular diverge: by name from an index,
  `--find-links`, a wheel file, a project directory and pip each behave differently.

## Reading a red gate

Details are in `references/reading-a-red-gate.md`. The two that cost the most:

- **`citations`.** Comments and tests cite source by path and line, and
  `citations.lock.json` pins the text each citation pointed at. Run
  `uv run python scripts/check_citations.py --fix`, then `--check`.
  - If `--fix` exits non-zero, it has named citations it could not place. The gate
    stays red on purpose (ADR-0248, #310). Open each file, find the construct the
    sentence is about, and write that line number. Never add a delta to the old one.
  - If `--fix` prints `N new citation(s) had no anchor; they are locked as written`,
    stop. It pinned whatever text sits at those numbers. Read every cited line
    yourself before you commit.
  - `--lock` is only for a cited block edited in place whose number is still right.
    It prints every anchor it replaces. Read that list.
- **`untracked-tests`.** `git add` the file. Do not lock a citation that only a test
  literal created (a string shaped like `name.py` plus a colon and digits). Rename
  the literal.

## What bit this repo

- #623/#624, branch `e2922307`: the release sdist was 6,059,123 B, over its 6,000,000 B
  cap. Preflight skipped the packaging gate, which runs only on packaging inputs, and
  only the full suite failed it (#831). The `sdist-size` gate now runs on every change.
- PR #840 (#612): two listed scores fell, and CI's complexity step failed all 8 lint
  + test jobs on STALE (runs 44600478328 and 44600556054). Preflight had no
  complexity gate then (#858).
- #733, 2026-11-10: a test that set `HOME` directly passed every gate preflight
  ran then, and only the full suite's `tests/test_env_sandbox_windows.py` failed it.
  A change to a file a whole-tree scan reads does not make `touched-tests` pick it.
  The `test-hygiene` gate now runs those scans.
- 2026-10-06, #131 and #177: two lanes ran the whole suite with their new tests
  untracked and reported it green. The commits were red on the citation gate and the
  HOME-sandbox gate.
- PR #728 (#721), 2026-11-08: the local suite was green and four lanes had run
  docker, but only on the new tests. All 8 ubuntu jobs failed on two older
  `@linux_only` cases in a file the change touched.
- #392, 2026-10-08: both Windows legs failed the type gate before any test ran.
  pyright on win32 does not know `os.O_NOFOLLOW` or `os.fchmod` behind a `hasattr`
  guard.
- 2026-09-12: a `uv run` during a background suite resynced `.venv` and produced 59
  phantom `ImportError` failures. A clean rerun had 0.
- 2026-10-28: `uv run --active` in a worktree imported the main checkout's stale
  packages, and a doc test passed against the wrong code.
- #310 and ADR-0248: `--fix` used to re-lock the citations it could not relocate,
  and the next `--check` was green over 12 citations nobody had repaired.
- PR #694, 2026-11-05: "no anchor; locked as written" pinned a citation onto the
  wrong comment after a rebase, and `--check` was green.
- 2026-09: a measurement of `uv tool install <project dir>` was written into an ADR
  and a CHANGELOG entry as the reason a fix was temporary. The installer's real
  path behaved the other way. A cross-review caught it.

## Pointers

- `CONTRIBUTING.md`, "The gates your PR has to pass": the four gates that are easy
  to miss, the HOME isolation rules, and what "it passes" means.
- `scripts/preflight.py --help`: the gate table, printed from the table itself
  (`GATES`, one row per gate).
- `scripts/check_citations.py`: its module docstring explains `--fix`, `--lock` and
  what is gated (`packages/**`, `tests/**`, `docs/guides/**`, root `*.md`).
- `scripts/check_types.py`: why the gate asserts `filesAnalyzed` (ADR-0213).
- `scripts/check_import_direction.py` and ADR-0015: imports only go down.
- `tests/agents/test_p2_band_boundaries.py` and ADR-0197: the three-band rule for
  delegation code.
- `docs/development/ci-hang-diagnostics.md`: CI time limits and the hang watchdog.
- The aelix-tests-that-bite skill: platform traps, and proving a test bites.
