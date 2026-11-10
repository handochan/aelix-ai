# Reading a red gate

One section per gate in `scripts/preflight.py`, in its order. Each says what the
gate measures, the usual cause of red, and the repair. The scripts' own docstrings
are the authority. This file only points into them.

## untracked-tests

A `tests/**/*.py` file exists that git does not track and does not ignore. The
citation gate (`scripts/check_citations.py`) and the HOME-sandbox test
(`tests/test_env_sandbox_windows.py`, which `git grep`s for a bare
`setenv("HOME"`) read only tracked files. A suite run before `git add` measures a
tree the commit does not hold.

Repair: `git add <file>`, then rerun every gate. If the file is a throwaway probe,
move it out of `tests/` instead.

## ruff

`ruff check .`, the same command CI runs. `ruff format` is not enforced
(CONTRIBUTING), because the code relies on hand-laid formatting. Do not reformat
files you did not otherwise change, since that buries your diff.

## types

`scripts/check_types.py` runs pyright and asserts two things beyond the error
count. It asserts `filesAnalyzed` is near the tree's size, because a broken
`include` once analysed 8 files and read as clean. It also asserts that
`scripts/pyright_spike.py` still fails, which proves narrowing works (ADR-0213).
A sudden drop in files analysed means the config broke, not that the tree got
smaller. Do not run a bare `pyright` in its place.

## citations

`citations.lock.json` pins the normalised text at every gated `path:line`
citation in `packages/**`, `tests/**`, `docs/guides/**` and root `*.md`.

| What you see | What it means | What to do |
|---|---|---|
| `--check` names drifted citations | lines moved above them | `--fix`, then `--check` |
| `--fix` exits non-zero and names citations | it could not find the cited text in exactly one place | re-derive each number by hand; the gate stays red until you do (ADR-0248) |
| `--fix` prints `N new citation(s) had no anchor; they are locked as written` | new citations were pinned to whatever sits at their numbers | read each cited line yourself; fix the number; rerun `--fix` |
| `--lock` prints anchors it replaced | you accepted the tree as it stands | confirm each one is a block edited in place whose number is still right |

A green `--check` straight after `--fix` is not evidence that the numbers are
right. It compares against what it just wrote. After a large move, run
`git diff citations.lock.json` and check that the changed keys are the ones you
meant to change.

Never write a made-up `path.py` plus colon plus digits example in prose or a test
string. The scanner reads it as a real citation. Rename the literal instead of
locking it.

During a merge or rebase, the gate needs more care. See the aelix-land skill:
unmerged paths count three times, and one lock cannot describe both sides.

## docs-bundle

`tests/test_docs_bundle_sync.py` and `tests/cli/test_docs_signpost.py`. Every
guide in `docs/guides/` has a byte-identical copy under
`packages/aelix-coding-agent/src/aelix_coding_agent/docs/`, in both directions
(ADR-0218). Repair: `uv run python scripts/sync_bundled_docs.py`, which also
removes orphans. The guide directory is flat: a nested guide is refused.

## import-direction

`scripts/check_import_direction.py` (ADR-0015, #292). The stack is `aelix-ai` ←
`aelix-agent-core` ← `aelix-coding-agent` ← `aelix-server` and `aelix`. That
holds inside a function, under `TYPE_CHECKING` and through
`importlib.import_module` too. The script's `ALLOWLIST` must match the tree
exactly. Do not add to it: pass a Protocol or a callback down, or move the shared
type down. `--list` prints every reverse-import site.

## band

`tests/agents/test_p2_band_boundaries.py` (ADR-0197). The kernel holds no
delegation surface. Product-core holds the contract and nothing that spawns
processes or takes consent. The bundled `aelix_agents` extension holds the rest.
The test reads the source tree, so it catches a placement mistake that every
behavioural test passes. A #721 lane missed it.

## packaging

`tests/packaging_gate/`. `test_build_hygiene.py` copies the repository without
its `.gitignore`, plants developer state and fake secrets (agent state under
`.omc/`, `.claude/` files, bytecode and more), builds, and fails on any planted
path that reaches a wheel or the sdist. With `.gitignore` gone, only the
`pyproject.toml` `exclude` lists stand between them. `.gitignore` and every
`exclude` list move together. Read both files' comments before you change either: they
record real leaks, among them a whole `.claude/worktrees/` tree in an sdist.

## pi-parity

`tests/pi_parity/` holds regression tests for behaviour already ported from pi
(ADR-0235). Parity is not a goal any more, but these tests stay. Do not delete one
to make a change pass. If you diverge from pi on purpose, change the test's
expectation in the same commit, with the reason.

## test-hygiene

`HYGIENE_TESTS` in `scripts/preflight.py`: the tests that read every file under
`tests/` or every product source for a rule each file must keep. Two examples:
`tests/test_env_sandbox_windows.py` (no test sets `HOME` itself; use
`tests.env_sandbox.sandbox_home`) and `tests/test_user_facing_raise_strings.py`
(no `raise` message names an internal). `touched-tests` picks a test file only
by the module or script it names, so a change to a file they scan does not run
them; before this gate only the full suite did (#733). The failure names the
file that breaks the rule, usually with the line. Fix that file, not the scan.
It runs when a path under `tests/` or a product `.py` changed.

## touched-tests

Every changed test file, plus every test file whose text names a changed product
module's dotted path. This is an approximation, so read the printed list. The run
uses `-p no:cacheprovider -q -W error::pytest.PytestUnraisableExceptionWarning`.
A coroutine never awaited or a task destroyed while pending fails here, before CI
turns it into an intermittent warning.

If a test fails only under load (this machine runs other suites), rerun it alone
and record both counts. One failure under load is not evidence either way.

## pyright-windows

`pyright --pythonplatform Windows` on the changed product files. A branch guarded
only by a runtime check (`hasattr(os, "O_NOFOLLOW")`) is still analysed.
`if sys.platform != "win32":` is narrowed. This gate catches only types. It never
sees path semantics: `Path("/abs").is_absolute()` is False on Windows, and
pyright says nothing about it (PR #639).
