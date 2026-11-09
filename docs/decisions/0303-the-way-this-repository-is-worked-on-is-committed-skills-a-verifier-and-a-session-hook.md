# 0303. The way this repository is worked on is committed: agent skills, a verifier agent, a session hook and one gate runner

Status: Accepted (2026-11-09, #733)
Date: 2026-11-09
Issue: #733. Owner decisions of 2026-11-09, quoted in §1.
Relates: ADR-0281 (`.agents/skills` is a project skill source, read in a trusted project),
ADR-0218 (the committed copy gated byte-identical, which the skill mirror copies), ADR-0213
(the type gate), ADR-0248 (`check_citations.py --fix` refuses what it cannot place), ADR-0015
(imports only go down), #111, #143 and #190 (the packaging leaks the exclude lists answer),
#672 and #673 (the Windows clock the coarse-clock plugin reproduces), #293 (the complexity
gate `scripts/preflight.py` will run once it merges).
pi: `earendil-works/pi` `b9ac5c1a0` commits its own working knowledge too: `AGENTS.md`, and
`.pi/skills/*.md`, `.pi/prompts/` and `.pi/extensions/` for pi itself, plus a husky pre-commit
hook. aelix diverges knowingly (ADR-0235): its skills sit in the tool-neutral Agent Skills
layout because three tools read them (§2), `AGENTS.md` stays local (§5), and nothing runs at
commit time; the gates run when a contributor asks (§4).
Tests: `tests/test_agent_skills.py`, `tests/test_session_brief.py`, `tests/test_preflight.py`,
`tests/test_coarse_clock_plugin.py`, `tests/packaging_gate/test_build_hygiene.py` (the #733
probes and exclude entries).

## Context

What keeps work in this repository fast and correct was mostly not in the repository.
`CONTRIBUTING.md` and the pull-request template name the gates. They do not say which gates a
given change needs, how to run the TUI or a real model without touching your own `~/.aelix`,
which Windows and Python-version traps CI hits, how to verify independently, or how to land a
branch whose docs conflict. That lived in one maintainer machine's agent memory and in lane-rule
blocks copied from one workflow script to the next, out of date where they had been copied.
`AGENTS.md` and `CLAUDE.md` are gitignored by design, so they could not carry it either.

## Decision

### 1. The owner's decisions (2026-11-09)

1. "제안한 모든 스킬과 agent, hook 등은 가치가 있습니다. 이걸 모두 작업해서 클로드코드 타
   세션에 충돌하지 않게 작업 진행해줄 수 있나요" — all the proposed skills, the agent and the
   hook are wanted, built so they do not collide with other Claude Code sessions.
2. They are committed to this public repository ("전부 커밋"). Steps only a maintainer takes
   are written generically, with no absolute path and no personal id.
3. "'세션 하나에 작업 하나' 규칙은 완전히 완화해야합니다" — the one-session-one-task rule is
   removed. Related issues share one lane, one branch and one PR. There is one commit per issue
   when the changes separate, and one commit for the group (`Fixes #a #b`) when they are
   entangled, with the reason in the PR ("얽힘에 따라"). The `aelix-issue-run` skill carries
   this into the repository.

### 2. One canonical skill tree, one gated mirror

The skills are Agent Skills (`<name>/SKILL.md` with `name` and `description` frontmatter) in
`.agents/skills/`, the canonical copy. aelix reads that directory itself in a trusted project
(ADR-0281), and so do other tools that follow the layout. Claude Code reads `.claude/skills/`
from the session's directory up to the repository root, and not `.agents/skills/` (the Claude
Code skills reference, read 2026-11-09), so that tree is a committed byte-identical mirror: `scripts/sync_agent_skills.py`
writes it and removes what the canonical tree no longer has, and `--check` exits 1 on any drift.
This is ADR-0218's pattern, chosen over a symlink because a checkout without symlink support
(git's `core.symlinks=false`, the Windows default) turns one into a text file. A path that
changes between file and directory is reshaped in either direction: stale files go before
anything is copied, and a directory left where a file goes, by then holding only debris, is removed
(`test_sync_reshapes_a_path_that_changed_between_file_and_directory`). Both trees are
needed: aelix reads no `.claude/skills/` (ADR-0281 D8 leaves out even `~/.claude/skills`), and
Claude Code does not read `.agents/skills/`.

`tests/test_agent_skills.py` holds, for every skill:

- the mirror is byte-identical in both directions, with no extra file
  (`test_the_mirror_is_byte_identical_in_both_directions`);
- `SKILL.md`'s `name` is the directory name, matches `^[a-z0-9]+(-[a-z0-9]+)*$` within 64
  characters, and `description` is non-empty and at most 1024 characters;
- aelix's loader, `load_skills(dir, agents=True)` (what `core/skill_selection.py` calls for an
  `.agents/skills` source), loads every skill with no diagnostic;
- no file in `.agents/`, `.claude/skills/`, `.claude/agents/` or `.claude/settings.json` that is
  tracked or could be added holds a home-directory path, an OpenRouter key prefix or a
  path-and-line citation. The citation gate does not scan these trees, so a line number written
  there would rot with nothing to say so.

`.claude/agents/*.md` gets the name, path and key checks. Every check also runs over a planted
tree in `tmp_path`, and `test_the_trees_are_not_empty` fails while there is no skill or agent,
so none of them can pass over nothing. The skills' two scripts (`tui_capture.py`,
`mutants.py`) each have a smoke test, and `test_the_skill_scripts_pass_pyright_on_windows`
runs `pyright --pythonplatform Windows` over copies of them in `tmp_path`: the project's pyright
`exclude` has `**/.*`, which drops a file under `.agents/` even when it is named on the command
line (measured: 1 of 2 named files analysed, 0 errors reported for the 6 the dropped one had).
That is also why `scripts/preflight.py`'s `pyright-windows` gate does not take them: it would
pass over them without reading them.

The seven skills and the agent (their text is #733's skill lane; their names and purposes are
fixed here):

| skill | for |
|---|---|
| `aelix-preflight` | run the gates a diff needs before you push |
| `aelix-live-check` | run the TUI or a real model safely |
| `aelix-tests-that-bite` | write tests that fail without the fix, and the cross-platform traps |
| `aelix-close-out` | what has to move before an issue closes |
| `aelix-issue-run` | the maintainer lane lifecycle, with the `lane-rules.md` briefs point to |
| `aelix-handoff` | write a handoff, and resume from one |
| `aelix-land` | rebase, CI, merge |

`.claude/agents/aelix-verifier.md` is an independent verifier: it is given the diff, the issue
and a base worktree, never the implementer's conclusions (`CLAUDE.md` rule 6).

A consequence measured on a scratch copy of this tree with one fixture skill in
`.agents/skills/`: `has_trust_requiring_project_resources` turns `True` for the checkout, where it
is `False` without the skills. So aelix run in a checkout of this repository asks the project
trust question once (ADR-0281's gate, by design), and `-p` without `--approve` or a saved
decision prints a Notice line and runs without the project skills (both measured in a new
worktree by #733's round-1 verify, with a scratch agent dir; Consequences). The trust-related test files
(`tests/cli/test_project_trust.py`, `tests/cli/test_agents_skills_509.py`,
`tests/status/test_status_cli.py` and seven more) passed in that copy, 507 tests. Three other
files did not, in the full suite: Consequences has the cause and the fix.

### 3. A SessionStart hook, tracked, that cannot get in another session's way

`.claude/settings.json` is tracked and holds one thing, a SessionStart hook with a 10 s timeout
that runs `scripts/session_brief.py` with whichever of `python3` or `python` is on `PATH`, and
`|| true`. Claude Code runs a shell-form hook with `sh -c` on macOS and Linux and Git Bash on
Windows, sets `CLAUDE_PROJECT_DIR`, and adds a SessionStart hook's plain stdout to the session's
context (the Claude Code hooks reference, read 2026-11-09). The brief prints the checkout,
branch and HEAD, `origin/main` from the local ref with ahead/behind and a dirty count, the
worktree count, the active issue locks under the git common directory, the newest
`.omc/specs/handoff-*.md`, and where the gate runner and the skills are. It prints only what
exists: a repository with no lock, no `.omc` and no `origin/main` gets the header, the checkout
line, the dirty count and the worktree count (`test_a_fresh_clone_gets_only_what_exists`).
Outside a repository it prints nothing.

A clone of this repository does have handoffs: `.gitignore` re-includes `/.omc/specs/`, where 58
handoffs are tracked, and a clone or a new worktree checks them all out with the checkout's time
as their mtime, so the newest by mtime was an arbitrary old one (#733's round-1 verify named a
2026-08-19 handoff in a fresh clone whose newest was 2026-10-12). The brief therefore names the
newest by the `YYYY-MM-DD` in the file name, labelled "newest handoff by the date in its name". In a
linked worktree it reads the main checkout's `.omc/specs` too, untracked handoffs included, and
prefers it on an equal date; mtime breaks what is left, and a name with no date ranks last
(`test_the_newest_handoff_is_the_date_in_its_name_not_its_mtime`,
`test_a_linked_worktree_prefers_the_main_checkouts_handoffs`).

It lists at most 8 active locks by name and counts the rest, so it prints at most 16 lines
(`test_ten_active_locks_name_eight_and_the_brief_is_at_most_16_lines`). In this lane's worktree
on 2026-11-10 it printed 16, 10 active locks among them; in the main checkout before this merge it
printed 15, because the gate runner and the skills it names were not there yet.

Several sessions work in one checkout at once and this runs in every one of them, so:

- **It writes nothing.** Every git call passes `--no-optional-locks`, because a plain
  `git status` refreshes the index and takes `index.lock`, racing another session's commit.
  `test_it_never_writes_the_index_or_takes_index_lock` watches `.git/index` and `index.lock`
  while it runs on a repository whose index is stale, and `test_the_index_check_above_can_fail`
  shows a plain `git status` rewriting the same index.
- **No network, no `uv run`.** Its git subcommands are `rev-parse`, `status`, `rev-list` and
  `worktree`, and git is the only thing it starts (`test_the_script_is_python_3_9_and_standard_library_only`).
  A `uv run` can resync a `.venv` under another session's tests. In a partial (promisor) clone
  even `git status` fetches a missing object from the remote on demand (Codex's round-1 review
  reproduced it), so every git call runs with `GIT_NO_LAZY_FETCH=1`, honoured by 2.45 and later
  (git's `environment.c` reads it from v2.45.0 on) and by some earlier maintenance releases
  (git 2.39.5 honours it, measured by #733's round-2 verify), and with `GIT_ALLOW_PROTOCOL` naming
  no real protocol, so a git without it that starts the fetch reaches no remote
  (`test_a_partial_clone_starts_no_lazy_fetch`, `test_a_git_older_than_2_45_still_reaches_no_remote`).
- **Bounded, and it cannot fail a session.** Each git call has at most 2 s, or what is left of
  2.5 s, and none starts after 2.5 s (`test_no_git_call_starts_once_the_budget_is_spent`, on a
  clock that advances 1 s per call). With a `git` that hangs it exits 0 with nothing printed, and
  the test allows it 6 s including interpreter start (`test_a_hanging_git_is_cut_off_and_still_exits_0`). An exception ends in one line and exit 0.
  With no Python on `PATH` the hook command exits 0 and prints nothing.
- **Python 3.9, standard library only**, because the hook runs whatever `python3` is on `PATH`
  and macOS ships 3.9. It parses with `feature_version=(3, 9)` and ran under `/usr/bin/python3`
  (3.9.6) in the main checkout in 0.31 s.

On Windows without Git Bash, Claude Code runs a shell-form hook in PowerShell, where this
command fails; a failed SessionStart hook is a non-blocking notice and the session goes on.
That is not measured here.

### 4. One gate runner for a change: `scripts/preflight.py`

`uv run --frozen python scripts/preflight.py` reads the change (commits since the merge base with
`origin/main`, then `main`, or `--base`; staged; unstaged; untracked files git does not ignore)
and runs the gates it can fail, from a table where a gate is one row: `untracked-tests`, `ruff`,
`types` and `citations` always; `docs-bundle`, `import-direction`, `band`, `packaging`,
`pi-parity`, `test-hygiene`, `touched-tests` and `pyright-windows` when a changed path calls for
them; `docker`,
`pythons` and `windows-clock` only when asked; `--help` prints that table from the table itself
(`test_help_lists_every_gate_from_the_table`). `--plan` lists each gate with the path that
triggered it and starts no gate and no other program: the `docker` gate asks `docker info` when
it runs and is skipped then if docker does not answer
(`test_plan_starts_no_docker_and_the_docker_gate_asks_only_when_it_runs`). `--claims FILE` writes
the "How it was verified" block a PR asks for, with each command, exit status, duration and the
end of its output, and with the checkout's path, the home directory and the interpreter's path
replaced (`test_claims_record_each_gate_and_no_local_path`, `test_claims_replace_the_home_directory`).
`--claims -` prints it instead, because a `claims.md` left at the root is an untracked new
top-level path that starts the packaging gate on every later run; a `--claims` file whose
directory does not exist is a usage error before any gate runs. Output a console cannot encode
(a Korean path on cp1252) is printed as a backslash escape
(`test_a_path_the_console_cannot_encode_is_escaped_not_a_crash`).

It never runs the full suite (no pytest step is built without test paths,
`test_no_pytest_step_is_ever_the_full_suite`) and prints that command last as what the
contributor still owes. It calls `uv` for one gate only. Every other gate runs through the
interpreter running preflight, because a nested `uv run` can resync the `.venv`
(`test_every_gate_but_pythons_runs_through_this_interpreter` builds and runs every gate with
process creation recorded). The exception is `pythons`: another interpreter needs its own
environment, and `uv run --isolated --frozen --all-packages -p 3.X` is the one way to get
`uv.lock`'s packages on it without writing to `.venv`. `--isolated` builds a throwaway
environment, `--frozen` installs `uv.lock` as it is without re-resolving it, and `VIRTUAL_ENV` is
dropped from its environment. `test_the_pythons_gate_leaves_the_project_venv_and_lock_alone` runs
it with the real `uv`, offline, in a scratch project whose `pyproject.toml` has gained a dependency
the lock does not have: the interpreter runs, no `.venv` appears and `uv.lock` keeps its bytes. It changes
the tree only with `--fix-citations`. Exit 0 when every gate that ran passed, 1 when one failed,
2 for a usage error, `--only pythons` without `--pythons` among them; a gate with nothing to run
is skipped with the reason, never counted as passed
(`test_a_gate_with_no_step_is_skipped_and_an_empty_result_never_passes`).

`pyright-windows` runs `pyright --pythonplatform Windows` on the changed product and `scripts/`
files and counts only errors in those files. Measured before it was written: all 325 product
files at `6caaae09` give 0 errors under that flag (one warning), as CI's Windows type gate
requires, so a changed file's error is the change's.

`test-hygiene` runs the tests that read every file under `tests/` or every product source for a
rule each file must keep (`HYGIENE_TESTS`, 18 entries, 57 tests). `touched-tests` selects a
test file only by the module or script it names, so a change to a file they scan (a new test
file, say) does not select them, and a change to any file can fail them. Round 1 of #733 set
`HOME` directly in `tests/test_preflight.py`: preflight passed, and only the full suite's
`test_no_test_sets_home_outside_the_helper` failed. Preflight with this gate, run on that commit
(`4ab758a9`), fails on exactly that line, with 1 failed and 56 passed, and
`test_the_hygiene_gate_fails_on_a_home_set_outside_the_helper` repeats it in a scratch
repository. The set took 10 to 18 s in six runs on a laptop running other lanes, so it runs when
a path under `tests/` or a product `.py` changed rather than always
(`test_the_hygiene_gate_runs_on_any_test_path_or_product_module`). `band` and `pi-parity` keep
their own scans, and a scan of one subtree or of named files, such as
`tests/jobs/test_core_is_kind_agnostic_611.py`, is not in the set.

`tests/plugins/coarse_clock.py` is promoted from probe state: loaded only by
`python -m pytest -p tests.plugins.coarse_clock`, it makes `time.monotonic` tick in 15.625 ms
steps and report that resolution, as Windows does on Python 3.12 and older. On 2026-11-04 it
reproduced on macOS the failure and the hang #672/#673 had on the Windows py3.11 and py3.12 legs
only. `test_coarse_clock_plugin.py` checks the ticks and the resolution with the plugin and the
finer clock without it. `--windows-clock` runs the touched tests under it.

### 5. Packaging, and what stays local

`.gitignore` negates `/.claude/skills/` and `/.claude/agents/` next to `/.claude/settings.json`.
Every pyproject `exclude` list names `.claude/skills`, `.claude/agents` and `.agents`. Measured
with real `uv build` runs on a copy of this tree with those negations and planted files:

- with only `.claude` in the list, neither `.claude/skills/x/SKILL.md` nor `.claude/agents/x.md`
  shipped: a directory negation does not outrank `.claude` the way the file negation for
  `settings.json` did. Without `.claude` both shipped; with the two exact entries and no
  `.claude`, neither did. The exact entries are kept so the next negation cannot re-open #143;
- `.agents/` is ignored by nothing in `.gitignore`, and without its entry
  `.agents/skills/x/SKILL.md` shipped in the root sdist and a nested copy in the
  `aelix-coding-agent` wheel.

`tests/packaging_gate/test_build_hygiene.py` plants all three at the root of the belt-less build
tree and of the belted sdist, where only a root sdist sees them, and `.agents` under the
`aelix-coding-agent` package too. It builds that wheel twice: from its sdist, as `uv build
--package` does, where the sdist list already removed `.agents`, and straight from the source
tree (`test_the_wheel_built_directly_carries_no_developer_state`), where only the wheel's own
list stands in the way. It counts `.agents` as never-shipped in its predicate, and asserts
`.claude` and `.agents` stay out of the release sdist built from tracked files. The three new
`scripts/` files do ship in the root sdist, as every `scripts/` file does, and in no wheel
(`uv build --all-packages` of this tree, measured for #733). `AGENTS.md` and `CLAUDE.md` stay
untracked and local to a maintainer's checkout; the skills carry what other contributors need.

## Consequences

- A contributor or a session on another machine starts with the gates, the live-check recipe,
  the cross-platform traps and the landing procedure in the tree, and the skills are tested like
  code. Changing a skill means editing `.agents/skills/`, running the sync and committing both.
- Every Claude Code session in this repository gets the brief, at most 16 lines, at start.
- aelix shows a new question here. In a new worktree, #733's round-1 verify (scratch agent dir)
  saw aelix ask "Trust project folder? … found in .agents/skills" once, and `-p` print a Notice
  line on stderr until the folder is trusted. In a fresh clone, #733's round-2 verify measured the
  `-p` half only (scratch home and agent dir, `--offline`, no key): the same Notice line. The TUI
  question in a fresh clone was not measured. Trusting loads the
  seven skills beside the bundled ones, and "Trust parent folder" covers every worktree under that
  folder (ADR-0281). The main checkout already had a local `.aelix/extensions` and was already
  trusted, so nothing changes there.
- Three test files depended on the process cwd having no project resources, and failed once the
  repository had some: the interactive launches in `tests/cli/test_entry_router.py` (10) and
  `tests/cli/test_first_run_onboarding.py` (2) met the trust question on a fake TTY with no
  `fileno`, and `tests/status/test_status_trust_store.py` (9) read the real agent dir's
  `trust.json`, which the #458/#497 guard fails at teardown. The dependency was older than #733:
  on `6caaae09`, a scratch copy holding `.agents/skills/x/SKILL.md`, or a `.aelix/extensions/` file
  like a contributor's own, fails the same 21 tests. They now run in a cwd of their own
  (`monkeypatch.chdir(tmp_path)`).
- Two more test files depended on it without failing. Twelve tests in
  `tests/cli/test_agent_context.py` and two in `tests/cli/test_docs_signpost.py` build harness
  options with no `project_trusted`, so they imported and ran the cwd's `.aelix/extensions`, a
  contributor's own code, and passed. With a marker extension planted in the cwd,
  `test_agent_context.py` loaded it 17 times before and 0 after (#733's round-2 verify), and
  `test_docs_signpost.py` 2 times before and 0 after (#733's fix round 2). They run in a cwd of
  their own too.
- `scripts/preflight.py` has to learn each new gate. #293's complexity ratchet is one row once
  it merges.

## Limits

- `touched-tests` is an approximation: changed test files, plus test files that name a changed
  module by dotted path or by `from <parent> import <leaf>`, or a changed script by file name.
  Re-exports and relative imports inside `tests/` are missed. It prints the list.
- The hook's Windows behaviour and its run inside a real Claude Code session start were not
  measured by this lane; the script and the command were run directly. #733's round-1 verify saw
  the brief as a `hook_response` in a `claude -p` stream; the `resume`, `clear` and `compact`
  sources were not measured.
- A git without `GIT_NO_LAZY_FETCH` (older than 2.45 and not one of the maintenance releases that
  have it; 2.39.5 has it) still starts a local fetch process in a partial clone, and the protocol
  allow-list stops it before any transport: the test removes the variable to show that, and on a
  real git 2.39.5 with only the allow-list set the transport was not reached either (#733's
  round-2 verify). Only 2.39.5 and 2.54 were measured.
- `test-hygiene` holds whole-tree scans only. A scan of one subtree runs when its own gate's
  trigger fires, and two do not fire on a change to what they scan: `pi-parity` runs on a product
  `.py` only, so a change under `tests/pi_parity/` alone does not run
  `tests/pi_parity/test_source_read_encoding.py`, which reads every `tests/pi_parity/*.py`
  (#733's final verify, N7); and a new module under `jobs/` does not run
  `tests/jobs/test_core_is_kind_agnostic_611.py`. The full suite catches both.
- `--docker` builds from `git stash create` (tracked and staged changes) or `HEAD`, so an
  untracked file is not in the image; it says which touched tests it left out.
