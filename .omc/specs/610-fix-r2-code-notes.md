# #610 fix round 2 — lane R2-code notes (G1–G4)

- Base: `9cdf76d0` on `feat/610-agent-resume`, worktree `~/dev/aelix-ai-worktrees/claude-610`. HEAD was still `9cdf76d0` right before the commit.
- Interpreter check: `aelix_agents.__file__` = `…/aelix-ai-worktrees/claude-610/packages/aelix-coding-agent/src/aelix_agents/__init__.py` on 3.12.13 (worktree `.venv`), and also on 3.11.15 and 3.14.5 (throwaway venvs).
- Abbreviations: `A/` = `packages/aelix-coding-agent/src/aelix_agents/`, `T/` = `tests/agents_ext/`.

## G1 — the child file is derived as the first run placed it (verify r2 m1)

### Code

- `A/child_session.py:find_resume_target`
  - Now `placement = place_children(parent_path)`. At c6e766bd it was `place_children(os.path.realpath(parent_path))`.
  - `place_children` takes `os.path.abspath` itself. `SpawnReceipt._allocate` calls `place_children(self.parent_path)`, where `parent_path` is `os.path.abspath(session.session_file)`. So the lookup and the allocation now use one derivation.
  - The placement comment was rewritten.
- `A/child_session.py:_check_child_file`
  - The link rule's comparison is now:
    ```python
    parent_dir, children = os.path.split(directory)
    unlinked = os.path.join(os.path.realpath(parent_dir), children, os.path.basename(path))
    if os.path.normcase(os.path.realpath(path)) != os.path.normcase(unlinked):
        return resume_damaged("its session file is reached through a link")
    ```
    At c6e766bd the test was `realpath(path) != path`.
  - The two `lstat` checks before it are unchanged: the children's directory, then the file.
  - The signature is unchanged. `directory` is `placement.directory`, which is `<parent's directory>/<stem>`, so `os.path.split(directory)[0]` is the parent's directory.
- Docstrings and comments rewritten:
  - `find_resume_target`: step 6, and the placement comment;
  - `_check_child_file`;
  - `ResumeTarget.path`;
  - `A/runtime.py:resume_refusal`. Its phrase "a regular file reached through no link" became "a regular file with no link below the parent's directory". G1 made the old phrase false.
- G1 changes no model-facing string. It changes which setups reach which refusal.

### Behaviour now

- **A link at or above the parent's directory is taken as allocation took it.** This covers a linked sessions root, a linked bucket (bucket-shaped name or not) and a parent file that is itself a link. The resume is launched with the first run's `--session`, byte for byte.
- **A link in either component below the parent's directory is refused**, as at c6e766bd. Those components are the children's directory and the file. `lstat` refuses a symlink. The `realpath` comparison refuses a link that `lstat` does not report, such as a Windows junction (simulated here), and a `..`.
- **A parent reopened through another spelling of a link above its bucket still finds its child, under the new spelling.** Example: the first run through the real path, the resume through a linked root.
- **A parent reopened under another NAME of its own file gets `RESUME_FORKED`, as at 883ded81.** Example: once as the link `p.jsonl`, later as its target `real.jsonl`. The children live under `p/`.

### Tests

`T/test_agent_resume_tool_610.py` drives the real hook and `execute()` through `_call`. Its new helpers are `_delegated_under_a_link` and `_session_flag` (the `--session` value of `_argv(plan)`).

- **`test_a_link_at_or_above_the_parents_directory_resumes_on_the_first_runs_session`**
  - Cases: `sessions-root`, `parent-file`, `bucket-to-a-bucket-name`, `bucket-to-a-plain-name`.
  - Asserts:
    - the resume is ok and says "the child says hello";
    - `resumes` is the first key;
    - `plans[1].session_path == plans[0].session_path ==` the child file;
    - `_session_flag` is the same string for both plans.
  - The fourth case, `sessions-root`, is beyond the three link cases G1 listed (deviation 3).
- **`test_a_link_below_the_parents_directory_is_refused_at_the_hook_and_at_execute`**
  - Cases: (children's directory | file) × (hook | execute), under a bucket linked to a plain-named directory.
  - The first line of the result is exactly `Resume refused: that agent's session record is damaged (its session directory is a symbolic link | its session file is a symbolic link). Delegate the task anew.`
  - `_nothing_spent` holds: no second plan, the budget unchanged, the records unchanged, `_pending` empty, and no path in the text.

`T/test_agent_resume_610.py`:

- **`test_a_parent_opened_through_a_linked_directory_still_finds_its_child`**: the assertions changed because what the test pins changed.
  - `target.path` is the link spelling, `str(through_link.parent / "p" / <child name>)`.
  - `os.path.realpath(target.path)` is the first run's file.
  - Before G1 it asserted `target.path == <real path>`.
- **`test_a_link_lstat_does_not_report_in_the_childrens_directory_is_refused`** (beyond the list, deviation 3)
  - It replaces `child_session.os` with a proxy whose `lstat` follows links. That simulates a Windows junction, which `lstat` does not mark `S_IFLNK`.
  - The children's directory is then a symlink that `lstat` does not report, and the refusal is `its session file is reached through a link`.
  - It is the only test that kills the `unlinked from realpath(directory)` mutant.

### Oracle and extra evidence

- **The verifier's probe `test_r1_false_refusals.py`**, run from the worktree root with the command in the task. Output is in `/tmp/610-r2/oracle-*.txt`.
  - Before G1: `21 passed in 1.49s`. Two of the three LINK cases were refused:
    - `LINK parent-file … -> Resume refused: that agent belongs to the conversation this one was forked from. …`
    - `LINK bucket-dir-to-bucket-name … -> the child says hello`
    - `LINK bucket-dir-to-plain-name … -> Resume refused: this conversation is not saved in aelix's sessions directory …`
    - The TMP_LINK resume launched with `/private/tmp/…/p/sub-….jsonl`.
  - After G1, final tree: `21 passed in 1.45s`. All three LINK lines say `-> the child says hello`. The TMP_LINK resume now uses `/tmp/…/p/sub-….jsonl`, the first run's spelling.
  - The verifier's `run.txt` says 22 passed; that count includes `test_r1_real_child.py`.
- **The new G1 tests under c6e766bd's two lines** (resolved placement plus `realpath(path) == path`, applied together): `9 failed, 1 passed`. The bytes were restored and the sha256 matched. Why each one fails:
  - `sessions-root`, `bucket-to-a-bucket-name`, and the linked-directory test fail on the spelling assertion. The old rule resumed those cases, but under the resolved spelling.
  - `parent-file` gets `RESUME_FORKED`.
  - `bucket-to-a-plain-name` gets `RESUME_NO_SESSION`.
  - The four link-below cases never reach the link check, because the old rule refused at the hook with `RESUME_NO_SESSION`.
  - The junction-simulation test passes, because the old rule also catches that case.
- **A real child: the verifier's `test_r1_real_child.py`** (not required; deviation 7). A real aelix child under `/tmp/…`, cancelled mid-bash, then resumed twice.
  - Result: `1 passed in 2.81s`.
  - Both resumes printed `session_path /tmp/610-verify-r1/runs/real-8e5b36/s/--tmp-…-repo--/p/sub-2c46691fed08.jsonl`, which is the link spelling, the same as the first run's.
  - The header read `{'type': 'session', 'version': 3, …}`, and the mock model got `requests 3`.
  - The cut `bash` left `sleep 30` (PID 97277, PPID 1, started 06:46:06), as limit 14 (#619) predicts. I killed it.

## G2 — the five surviving mutants (verify r2 m2)

New tests, both in `T/test_agent_resume_610.py`:

- **`test_a_later_line_moves_neither_the_target_nor_the_listing`**
  - Cases: `another-child-session`, `a-resume-of-another-run`, `another-profile`, `another-task`.
  - A later line of the first run's key, with limits that agree, names another `child.session_id`, `resumes`, `profile` or `task_preview`.
  - `find_resume_target` returns an equal `ResumeTarget`, and the listing is byte-equal to its pinned text before the forge, `- <id> (scout, finished): "look at the thing"`.
- **`test_a_record_that_changed_what_the_dialog_showed_is_refused_at_the_launch`**
  - Cases: `profile`, `task_preview`.
  - After the target is found, every line of the first run is rewritten, its start included, and the parent is reopened.
  - `resume_granted` refuses with `… damaged (its record changed before the resume could start). Delegate the task anew.`, and nothing is spent.
  - Under the mutant `_same_target` (key, path and limits only), this resume LAUNCHED.

`_same_target`'s `rel` and `parent_session_id` get no cases of their own:
- `rel` is `<basename(directory)>/<basename(path)>`, so it is a function of `path`, which is compared already.
- `parent_session_id` is checked by `resume_granted` itself just before the lookup (`RESUME_CONVERSATION_CHANGED`). A different id from the lookup needs a race between those two awaits.
- `profile` and `task_preview` kill the mutant either way.

### Mutation run

- **Script:** a COPY of the verifier's `mut/mutate.py`, sha256 `d39046b8…57d8`, at `/tmp/610-r2/mutate.py`.
- **Changes from the original:**
  - `WT` points at claude-610.
  - Each file's bytes are saved before its first mutant, written back after every mutant, and every restore is confirmed by sha256. Nothing calls `git checkout --`.
  - The final check compares every touched file with its saved sha256. `git status` could not be used, because the tree had uncommitted work.
- **Mutant list:** the two F3 lines G1 rewrote got their new patterns. "F3 abspath placement" became the reverse mutant, "G1 resolved placement (c6e766bd)". Six mutants were added; they are shown below.
- **Safety copy:** before the run, the touched files were also copied to `/tmp/610-r2/backup/` (`backup-SHA256SUMS.txt`). After the run, every file compared equal to that copy.
- **Command:** `python3 /tmp/610-r2/mutate.py` (all mutants).
- **Result:** `SUMMARY 45 / 45 caught; every touched file back to its saved sha256: True` in 52 s. The test set is the verifier's: the three #610 files, the #604 reports file and the sweep test, 343 tests.

The five that survived round 2 are now caught:

| Mutant | Caught by |
|---|---|
| `_group_by_child` reads `run.data["child"]` | `test_a_later_line_moves_neither_the_target_nor_the_listing[another-child-session]` |
| `_lineage` firsts by `run.data["resumes"]` | `…[a-resume-of-another-run]` |
| listing preview from `first.data` | `…[another-task]` |
| listing profile from `first.data` | `…[another-profile]` |
| `_same_target` = (first_key, path, limits) | `test_a_record_that_changed_what_the_dialog_showed_is_refused_at_the_launch[profile-someone-else]` |

The added and changed mutants, all caught:

| Mutant | Caught by |
|---|---|
| G1 resolved placement (c6e766bd's line) | `test_a_parent_opened_through_a_linked_directory_still_finds_its_child` |
| F3 no realpath check (on the new comparison) | `test_a_child_path_with_a_parent_step_is_not_its_file` |
| G1 compare with `path` itself (c6e766bd's rule) | `test_a_parent_opened_through_a_linked_directory_still_finds_its_child` |
| G1 `unlinked` from `realpath(directory)` | `test_a_link_lstat_does_not_report_in_the_childrens_directory_is_refused` |
| G1 no children's-directory `lstat` | `test_a_directory_swapped_for_a_link_after_the_hook_is_refused` |
| G1 no file `S_ISLNK` check | `test_a_symlinked_child_file_is_refused` |
| G2 `_lineage` chain check by `run.data["resumes"]` | `…[a-resume-of-another-run]` |
| G4 `RESUME_NOT_YOURS` without the next step | `test_the_refusals_that_said_only_why_now_say_what_next` |

The other 37 mutants of round 2 are caught as in the verifier's record, with the same first failing test.

## G3 — comment only

`A/child_session.py:ChildLimits.from_record`, the comment above the `profile_source` check.

- Old:
  > Absolute, as ``cwd`` is (#610 F15): ``fresh_limits`` records ``ResolvedProfile.source_path``, which discovery always makes absolute, so a relative one was written by something else.
- New:
  > Absolute, as ``cwd`` is (#610 F15): ``fresh_limits`` records ``print_channel.profile_identity`` of the profile's ``source_path``, which is its ``os.path.abspath`` — discovery keeps a relative ``AELIX_CODING_AGENT_DIR`` relative, so ``source_path`` itself can be relative — and a relative one here was written by something else.

## G4 — `RESUME_NOT_YOURS`

This is the only model-facing string changed in round 2.

- Old: `Resume refused: that agent was not delegated by you — the user started it (/agents run) or a client did — and only an agent you delegated can be resumed.`
- New: `Resume refused: that agent was not delegated by you — the user started it (/agents run) or a client did — and only an agent you delegated can be resumed. Delegate the task anew.`

Pins:
- No test spelled the text. `test_an_agents_run_child_is_not_the_models_to_resume` compares with the constant, and still passes.
- `T/test_agent_resume_tool_610.py:test_the_refusals_that_said_only_why_now_say_what_next` now includes it in the ends-with check, and pins the full sentence.
- No ADR, guide or CHANGELOG text quotes the sentence. ADR-0277:580 says it "still says only why"; that is for E1.

## Commands and results

All from the worktree root, `env -u VIRTUAL_ENV`.

| What | Command | Result |
|---|---|---|
| #610 files, 3.12.13 | `uv run --no-sync python -m pytest -q -p no:cacheprovider` on the three #610 files | `282 passed in 3.42s` (267 at the start; 15 new test cases) |
| #610 + warning + drift, 3.12 | the same, plus `tests/cli/test_child_session_warning.py` and `tests/test_citation_drift.py` | `317 passed in 7.12s` (282 + 6 + 29) |
| `tests/agents_ext` + `tests/agents`, 3.12 | `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/agents_ext tests/agents` | `2637 passed, 11 skipped, 4 warnings in 79.76s` |
| #610 files, 3.11.15 | `/tmp/610-r2/venv3.11/bin/python -m pytest -q -p no:cacheprovider <three #610 files>` | `282 passed in 4.80s` |
| #610 files, 3.14.5 | the same with `/tmp/610-r2/venv3.14` | `282 passed in 5.30s` |
| new and changed tests only | `-k` over the 17 new or changed test cases | `17 passed, 264 deselected` on each of 3.12, 3.11 and 3.14; none skipped |
| oracle probe | the task's command | `21 passed`, three times `the child says hello` |
| `ruff check` | over every changed `.py` file | `All checks passed!` |
| type gate | `uv run --no-sync python scripts/check_types.py` | `OK gate: 0 errors across 303 files`, `PASS: type gate clean, narrowing spike still inverted` |
| citations | `scripts/check_citations.py --check`, after `--fix` | `citations OK — 914 gated, none drifted.` |

Notes on the runs:
- The 4 warnings in the agents run are the pre-existing timing `UserWarning`s, from `test_print_channel_spawn.py` (2) and `test_rpc_sprint_pins.py` (2).
- The throwaway venvs were built with `UV_PROJECT_ENVIRONMENT=/tmp/610-r2/venv3.X uv sync --frozen --python 3.X`. The worktree `.venv/pyvenv.cfg` has the same mtime before and after.
- `ruff format --check` would reformat these files at `9cdf76d0` too. `ci.yml` says format is intentionally not enforced.
- `pyright` on the two changed test files reports 6 errors, all in the tool file on pre-existing lines: 255, 622, 627, 662, 812 and 1268, all `reportOptionalMemberAccess`. Tests are outside the type gate. My additions start at line 1395.

### Citations

- `--fix` relocated 18 citations in 6 files: `batch.py`, `extension.py`, `panel.py`, `progress.py`, `T/test_batch_executor.py`, `T/test_tool_and_security.py`, plus the lock file.
  - I read every change. Each one is `runtime.py:N` → `runtime.py:N+1`, because `resume_refusal`'s docstring grew by one line.
- One citation could not be relocated automatically: `batch.py:415` `runtime.py:1428`. Its text, `self._publish(child, child.stream, on_event, spawn_model=spawn_model)`, now sits at both 1429 and 1547.
  - I moved it by hand to `1429`, the publish right after the registry insert, which is what that comment describes.
  - A second `--fix` gave `relocated 0`, `1 new citation(s) had no anchor; they are locked as written`.
  - The lock's `runtime.py:1428-1428` became `1429-1429`, with the same text.

## Deviations

1. **`_check_child_file` keeps its `(path, directory, …)` signature.** "The parent's directory" in the rule is `os.path.split(directory)[0]`. A `path` that is not `directory/<name>`, like the `..` test, is refused by the same comparison.
2. **`A/runtime.py:resume_refusal`'s docstring was edited.** G1 does not name it, but G1 made its "reached through no link" false.
3. **Tests beyond G1's list:**
   - the `sessions-root` case, the common setup: a linked HOME or macOS `/tmp`. Under the old rule it resumed under another spelling.
   - the link-below test with 4 cases, which shows that taking links above does not take any below, at the hook and at execute.
   - the junction simulation. It is the only test that kills the `realpath(directory)` mutant.
4. **The assertions of `test_a_parent_opened_through_a_linked_directory_still_finds_its_child` changed.** Its name is unchanged, so its citations still hold.
5. **`_same_target`'s `rel` and `parent_session_id` have no cases of their own.** The reason is in G2.
6. **The mutation copy differs from the verifier's.** It restores from saved bytes, the two G1-rewritten patterns are updated, and six mutants are added (above).
7. **I also ran the verifier's real-child probe.** It was not required.

## For R2-docs (E1, E4): doc lines that still describe the c6e766bd rule

I did not edit any of these:

- ADR-0277:235 (D6): `place_children(<the parent's file now, its links resolved>)`.
- ADR-0277:250-258 (D6, the "a link anywhere below the parent's directory" item). It says the placement comes from `os.path.realpath`, that the path must be its own `realpath`, that links above are "resolved", and that a linked parent file "is placed where that file really is".
- ADR-0277:550 (the D11 table): "none inside a bucket once its links are resolved".
- ADR-0277:580: "`RESUME_NOT_YOURS` still says only why" (G4).
- ADR-0243:176-177: "derives the file from where the parent's file really is now (its links resolved)".
- The guide, `aelix_coding_agent/docs/agent-profiles.md:381` and its bundled copy: "reached through a link". This is still true of a link below the parent's directory only.
- New test names for Verification:
  - `test_a_link_at_or_above_the_parents_directory_resumes_on_the_first_runs_session`
  - `test_a_link_below_the_parents_directory_is_refused_at_the_hook_and_at_execute`
  - `test_a_link_lstat_does_not_report_in_the_childrens_directory_is_refused`
  - `test_a_later_line_moves_neither_the_target_nor_the_listing`
  - `test_a_record_that_changed_what_the_dialog_showed_is_refused_at_the_launch`

## Not measured

- Windows: the junction case is only simulated, and `ntpath.realpath` was not run on 8.3 or `\\?\` paths. G1 keeps c6e766bd's shape there: the `realpath` of a prefix plus two names, compared with the `realpath` of the whole path.
- Linux.
- The full suite, which the lead runs.
- A real model.
