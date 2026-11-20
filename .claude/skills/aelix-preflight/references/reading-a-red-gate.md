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

## complexity

`scripts/check_complexity.py --check --base <base>` (ADR-0300, #293), the ratchet CI
runs on every leg; CI's step adds `--require-base --github-event`. It scores every
`.py` and `.pyi` git tracks under `packages/` and `src/` with Ruff's C901 and compares
them with `complexity-baseline.json`.

| What you see | What it means | What to do |
|---|---|---|
| `NEW` | a function not listed scores over 20 | make it simpler |
| `RAISED` | a listed function scores above its listed number | make it simpler |
| `STALE ... fell` or `within the limit now` | a listed score went down | `--update`, and commit the baseline with the change |
| `STALE ... same-named definitions` | a reorder or a stub renumbered same-named definitions | `--update`, and commit the baseline |
| `GONE` | no function has a listed identity now | `--rename OLD NEW` for a rename or a move; `--update` for a removal |
| `HISTORY` | the baseline gained an entry or a score against the merge base | restore those baseline lines and make the function simpler; only a ruff upgrade's `--init` raises one (ADR-0300 §5, §7) |
| `NOQA` | a `# noqa` comment names C901 or a prefix of it | remove it; the measurement ignores it anyway |
| `CONFIG` | a ruff config under `packages/`, `src/` or at the root has an ignore covering C901 (`ALL`, `C`, `C9`, `C90` or `C901`) | remove that ignore |
| `RUFF` | the installed ruff is not the one the baseline was measured with | sync to `uv.lock`'s ruff; a ruff upgrade runs `--init` in a change of its own (ADR-0300 §7) |

Make a function simpler by giving a responsibility its own home, not by splitting it
only to lower the number. The gate shows the findings and leaves out the script's own
14-line repair text, which with a note filled the 15 lines of output a failed gate
shows; run the script alone to read it. It leaves out only a line that starts with
`What to do:` and the indented lines under it, and a timed-out run ends with what the
script last printed on stdout and the kill note
(`tests/test_preflight.py::test_only_a_line_that_opens_the_repair_text_and_its_indented_lines_are_left_out`,
`::test_a_complexity_timeout_ends_with_what_it_printed_last_and_the_kill_note`).

An untracked file is not measured, and CI will measure it once it is committed: a
pass prints the script's `UNTRACKED` note under the result
(`::test_a_passing_complexity_gate_shows_the_untracked_note`), so `git add` it and
rerun. A change that deletes the script runs the gate, and it fails as CI's step would
(`::test_a_change_that_deletes_the_ratchet_runs_the_gate_and_fails_it`).

Preflight compares the baseline with the merge base of `--base`, and CI's push to
`main` compares it with that push's `before`: with a `--base` whose merge base
already holds a raised score and its raised baseline entry (`--base HEAD` once both
are committed, say), preflight passes and CI's push fails on `HISTORY` (Codex review
of #858). Known limits of the trigger: a ruff config that is a symlink is read
through the link, so a change to the file it points at alone leaves the gate skipped,
and the script also scans ruff configs that git ignores, which preflight's change set
does not list. Run the script alone after such a change.

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

## sdist-size

One test, `tests/packaging_gate/test_build_hygiene.py::test_the_release_sdist_stays_under_the_size_cap`,
on every change: every file the root sdist ships adds to it, and `packaging` runs only on
packaging inputs (#831). `packaging` deselects it, so it runs here only. It builds the
release sdist with `uv build --sdist` from the files `git ls-files` lists that are on
disk, as they are on disk: staged and unstaged edits are included, a tracked file
deleted or moved without `git rm` or `git mv` is left out, and untracked files are not
in it (`test_build_hygiene.py::test_a_tracked_file_missing_on_disk_is_left_out_of_the_release_tree`).

The gate names a temporary file in `AELIX_SDIST_SIZE_REPORT`, and the test writes
`{"size": N, "cap": C}` there as soon as it has measured. The gate passes only when
pytest exits 0 and that report holds a size at or under its cap, compared by preflight
itself, and then prints the size and the headroom under the result. What a failed
gate's output ends with:

| It ends with | What it means |
|---|---|
| `the published sdist is N B, X B over the C B cap`, then what to do | the sdist is too big |
| `the size test did not measure an sdist: pytest exited 0 ...` | the test never ran its body: a skip, a deselection, `--setup-only` or `--collect-only` (in `PYTEST_ADDOPTS`, say) |
| `... with an error before it measured one: <exception>` | building the sdist failed, `uv build` offline with an empty cache for one |
| `the size test measured N B, under the C B cap, and pytest exited R` | something other than the size failed the run |
| `... pytest exited R and the test wrote no report; its output above says why` | pytest stopped without a result for the test: exit 5 (a plain deselection) or 4 (a usage error) |
| `the size test's report did not read: ...` or `... is not a size and a cap: ...` | the report file is unreadable or malformed; something other than the size test wrote it |

(`tests/test_preflight.py::test_a_size_run_that_measured_nothing_fails_the_gate`,
`::test_an_error_before_the_size_was_measured_is_named_not_called_over_the_cap`,
`::test_the_size_verdict_compares_the_report_with_its_cap`.) The test fails over the
cap with `pytest.fail`, which `python -O` and `--assert=plain` leave in place
(`test_build_hygiene.py::test_the_cap_holds_under_python_O`). The cap is not a number to
raise: list the tarball by size (`tar -tvzf`), and keep out what need not ship, as
#387 (the demo recording) and #816 (`docs/decisions/`) did. The comment above
`MAX_SDIST_BYTES` records both. Without `uv` the gate is skipped, because the test
skips itself without it.

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
