# 0300. A function's complexity only goes down: Ruff C901 over 20 is listed by name and score, and the list only shrinks

Status: Accepted (2026-11-09, #293)
Date: 2026-11-09
Issue: #293, the first slice of #284. Owner decisions: 2026-09-19 (the issue body;
[docs/05](../05-post-beta-direction.md) §11, decision 9) and 2026-11-09 (implemented beside B7, merged
after #723, the baseline made again on `main` right before the merge; the trend value counts each
method once, §9).
Relates: ADR-0015 (the import-direction gate, whose allowlist also only shrinks and leaves an edit to
the list to review; §6 below checks the baseline's edits instead), ADR-0213 (a gate that sees nothing
reads clean), ADR-0274 (CI time limits; this step took 1.25 to 1.31 s on the real tree on a macOS
laptop, §8, and has not been timed on a CI runner), ADR-0235 (pi is a reference, not a target).
pi: `earendil-works/pi` `origin/main` fetched 2026-11-09 has no complexity gate. Its `biome.json`
turns on biome's `recommended` rules, and `noExcessiveCognitiveComplexity` is not one of them. Nothing
to port.
Tests: `tests/test_complexity_ratchet_293.py`.

## Context

The owner decided on 2026-09-19: the gate is Ruff `C901`; a function not in the baseline may score at
most 20; the functions already over 20 go in `complexity-baseline.json` with their score; a score
that rises fails CI and a score that falls lowers the baseline, which therefore only goes down; no
`# noqa: C901`; the Radon recursive count is a value written down each release, not a gate; and
splitting a function into short ones only to lower the number, or turning conditionals into classes,
is not done.

docs/05 recorded that decision as "the gate is the Ruff `C901` that is already in CI". It was not: the
root `pyproject.toml` selects E, W, F, I, UP, B and SIM, and CI runs `uv run ruff check .`. The gate is
new, and the scores had been free to grow. Measured with the gate's own ruff command (ruff 0.15.13,
`packages/` and `src/`, Decision §1) on three trees exported with `git archive`:

| tree | functions | over 20 | over 40 |
| --- | ---: | ---: | ---: |
| `026bd15` (the issue's measurement, 2026-09-19) | 3,324 | 30 | 6 |
| `6caaae09` (this change's base when written, 2026-11-09) | 5,521 | 53 | 14 |
| `fa742f77` (#723's tree, 2026-11-09) | 5,611 | 54 | 14 |
| `80ff2cc2` (main after #723 merged, the base this change lands on, 2026-11-10) | 5,631 | 54 | 14 |

The 30 and 6 are the issue's own numbers. #723's tree adds `aelix_agents/runtime.py`'s `_run` at 23
and raises `run_print_mode` from 57 to 58; the baseline committed here is `6caaae09`'s, and it is made
again on `main` right before this change merges (owner, 2026-11-09).

## Decision

1. **One ruff measurement covers every tracked function.** `scripts/check_complexity.py` runs the
   `ruff` that `uv.lock` pins, from the project's interpreter, over exactly the `.py` and `.pyi` files
   `git ls-files` lists under `packages/` and `src/` (the directories of the owner's measurement),
   named one by one, with only `C901` selected and `max-complexity` 0, so every `def` is reported
   with its score. Ruff lints a file it is given by name even where its own file discovery would skip
   it: its default excludes (`dist/`, `venv/`, `.venv/`, `site-packages/`, `_build/`,
   `node_modules/`), which apply even under `--isolated`, and `.gitignore`
   (`test_every_tracked_file_is_measured_wherever_it_is`). An untracked file is not measured; CI's
   checkout has none, and a local run names the untracked ones
   (`test_an_untracked_file_is_not_measured_and_is_named`). Outside a git checkout the gate cannot
   measure (`test_outside_a_git_checkout_the_gate_cannot_measure`). The files go to ruff in batches
   of at most 8,000 characters, because a Windows command line holds 32,767 and the list was 21,966
   on `6caaae09` (`test_files_go_to_ruff_in_batches_and_all_are_measured`). Two things stop the gate
   with exit 2 rather than let a function escape: anything ruff writes to stderr, such as
   `Failed to lint` a file that is not UTF-8, which ruff otherwise skips with exit 0
   (`test_a_file_ruff_does_not_measure_stops_the_gate`); and a `def` in a measured file that ruff did
   not report (`test_a_def_ruff_did_not_report_stops_the_measurement`). On `6caaae09` this measures
   325 files and gives the same 5,521 identities and scores as the directory walk it replaced.
   `--isolated` keeps ruff from reading any configuration file and `--ignore-noqa` from honouring any
   `# noqa`, so neither an `ignore`, `per-file-ignores` or `exclude` in a `pyproject.toml` nor a
   comment can take a function out of the measurement
   (`test_a_noqa_naming_c901_is_refused_and_does_not_hide_the_function`,
   `test_a_ruff_ignore_naming_c901_is_refused_and_does_not_hide_the_function`: the function is still
   reported as NEW). The score is Ruff's number as Ruff reports it. Ruff adds a nested function's whole
   score to the function it sits in and also reports the nested function under its own name, so a
   closure counts in its parent and on its own: an `outer` with 10 `if`s around an `inner` that scores
   15 scores 26 (`test_nested_functions_keep_ruffs_number_and_their_own_identity`). Moving a closure
   out of its parent lowers the parent. The two numbers are not added up anywhere.

2. **A function is `<path>::<qualname>`.** The path is relative to the repository root, keeps its
   letter case, and is written with `/` on every OS; a Windows path whose root differs only in letter
   case becomes the same identity (`test_a_windows_path_becomes_the_same_posix_identity`,
   `test_an_identity_keeps_the_case_of_its_path`). Both read the Windows path as `PureWindowsPath` on
   macOS; no Windows run has measured it yet, and the CI step on the Windows legs (§8) will be the
   first. Ruff reports only the bare name and a location, so the qualname comes from the file's AST:
   the `def` with that name whose header holds that line (also when a backslash puts the name on the
   line after `def`, `test_a_name_on_a_continuation_line_is_still_its_def`). A nested `def` with its
   parent's name that is the parent's first statement, decorated or not, starts on the line where the
   parent's header ends; there the `def` that starts on the reported line is the one ruff means
   (`test_a_nested_def_with_its_parents_name_as_its_first_statement`), and any other tie stops the
   gate with exit 2 (`test_an_ambiguous_definition_stops_the_measurement`). The qualname is spelled
   as `__qualname__` spells it (`Class.method`, `outer.<locals>.inner`, `factory.<locals>.Local.go`).
   Two definitions with one qualname in one file - a `def` in each arm of an `if`, a property's getter
   and setter, a name defined twice in one function - are numbered in source order: the first keeps
   the bare qualname, the n-th is `<qualname>#n`. `@overload` stubs are numbered on their own,
   `<qualname>#overload<n>`, so adding an overload does not renumber the implementation
   (`test_duplicate_qualnames_are_numbered_in_source_order`,
   `test_an_overload_above_a_listed_function_does_not_renumber_it`).

   Reordering same-named definitions, or adding one above another (an `if TYPE_CHECKING:` stub),
   renumbers them, and nothing in the source says which is which. So among the same-named definitions
   of one file the gate holds their scores as a set, not one by one: their scores over 20, highest
   first, may not pass the listed ones, highest first, and there may not be more of them. A NEW or
   RAISED definition whose name passes that test is reported as STALE, "same-named definitions
   renumbered"; `--update` writes the new identities, and the history check (§6) accepts the result,
   whether `--update` or a hand edit wrote it
   (`test_reordered_same_named_definitions_are_renumbered_by_update`,
   `test_two_listed_same_named_definitions_swapped_are_renumbered`,
   `test_a_stub_above_a_listed_function_renumbers_it`,
   `test_the_renumbering_written_by_hand_passes_the_history_check`). A renumbered definition that
   rose, or a second same-named one over 20, is still NEW, in the code check and in the history check
   (`test_a_renumbering_may_not_raise_a_score_or_add_an_entry`). What this costs: among the
   same-named definitions of one file, listed or not, scores can move from one to another as long as
   the set does not rise past the listed set, highest first. So an unlisted one can take a listed
   one's score without being NEW. With a listed `f` at 30 and an unlisted `f` at 5 in the other arm,
   and no reorder, the listed one falling to 5 while the unlisted one rises to 30 is reported as
   STALE, renumbered, and `--update` and the history check both pass it (round-2 verify probe S1).
   A property getter falling from 30 to 2 while its setter rises from 2 to 30 is the same (S1p).
   With three or more, several may rise: listed scores 30, 25 and 22 becoming 5, 30 and 25 is a
   renumbering too (S2). The probes are in `.omc/probes/293/verify-r2/probes/p_setdesign.out`,
   rerun on this change's script in `.omc/probes/293/finish/p_setdesign.out`. On `6caaae09` no
   listed entry is numbered, and the 85 numbered identities in the tree (80 overload stubs and 5
   property setters) score 1 or 2.

3. **`--check` names each failure and what to do about it.** It fails on a function over 20 that is not
   listed (NEW, with its score); a listed function above its score (RAISED, old -> new); a listed
   entry the tree no longer matches (STALE: its score fell, or it is at 20 or below now; GONE: no
   function has that identity; or a renumbering, §2); a `# noqa` comment naming `C901` or a selector
   that covers it (`C90`, `C9`, `C`) in a measured file, including `# ruff: noqa: C901`, but not the
   same text in a string or a `noqa` for other codes
   (`test_noqa_for_other_codes_and_noqa_in_strings_pass`); a ruff `ignore`, `extend-ignore`,
   `per-file-ignores` or `extend-per-file-ignores` entry naming one of those, or `ALL`, in a
   `pyproject.toml`, `ruff.toml` or `.ruff.toml` at the root or under the measured directories; and a
   baseline measured with another ruff (§7). The failure text says: make the function simpler, not split only to lower
   the number; `# noqa: C901` is refused and ignored; never add or raise an entry by hand; run
   `--update` for STALE; run `--rename` for a GONE entry that was renamed or moved, and it suggests
   the pair when a NEW function has the GONE one's last name and a score no higher
   (`test_a_new_function_over_twenty_fails_and_one_at_twenty_passes`,
   `test_a_raised_score_fails_with_old_and_new`, `test_a_rename_is_carried_by_rename_and_nothing_else`).
   It exits 2 when it cannot measure: git or ruff failed, ruff wrote to stderr or reported a file it
   could not parse, or the baseline is missing, not UTF-8, malformed, has a key that is not
   `<path>::<qualname>`, or lists a score of 20 or less
   (`test_a_file_ruff_cannot_parse_stops_the_measurement`,
   `test_a_missing_or_malformed_baseline_cannot_be_measured_against`,
   `test_unreadable_inputs_cannot_be_measured_against`,
   `test_a_baseline_key_must_name_a_path_and_a_function`).

4. **A stale entry fails `--check`.** A score that fell must be written down in the change that
   lowered it, so the lower number is the ceiling at once; otherwise the function could climb back to
   the old score without failing anything. The cost is one command in that change. This was the lead's
   recommendation for #293 and this change adopts it; it is not an owner decision
   (`test_a_lowered_score_fails_until_update_lowers_the_baseline`, which also climbs back to the old
   score after `--update` and fails).

5. **Three commands write the baseline, and only `--init` can raise it.**
   - `--update` writes only what lowers: a score that fell is rewritten; an entry at 20 or below, or
     GONE, is dropped; renumbered same-named definitions (§2) get their new identities. It never adds
     an entry or raises a score, reports what it did not write and exits 1 while a NEW or RAISED
     function remains; the entries of a name that has a NEW or RAISED definition it leaves as they
     are (HELD), so that once the code is fixed the renumbering still has them to fit under; with
     nothing to lower it leaves the file byte for byte
     (`test_update_drops_entries_within_the_limit_and_entries_gone`,
     `test_update_never_raises_a_score_or_adds_an_entry`,
     `test_a_renumbering_may_not_raise_a_score_or_add_an_entry`). It writes the baseline sorted
     (`test_the_baseline_is_written_sorted`).
   - `--rename OLD NEW` carries an entry to a function that was renamed or moved to another file or
     class. OLD must be listed and gone from the tree, NEW over 20, unlisted, and not above OLD's
     score; an equal score is the common case, a move
     (`test_rename_refuses_a_raise_an_old_name_still_there_and_a_function_within_the_limit`,
     `test_rename_carries_an_equal_score`). When OLD is still there under a same-named definition,
     the refusal says that a renumbering is `--update`'s. Moving a listed closure out of its parent to
     module level is such a rename. Moving a listed one out from before a same-named definition, or
     in above a listed one, takes two changes (Consequences).
   - `--init` writes the baseline from the tree as it is and prints what changed against the one it
     replaces. It is for the first baseline and for a Ruff upgrade (§7); anywhere else it is a hand
     raise, which §6 refuses (`test_init_lists_every_function_over_twenty_and_what_changed`). The
     regeneration right before the merge is
     `uv run python scripts/check_complexity.py --init`.

6. **CI compares the baseline with an earlier one (the history check).** Without it, raising the
   code and the listed number together passes `--check`, which is exactly what the owner's "a score
   that rises fails CI" forbids. `--check` reads the baseline at `git merge-base HEAD origin/main`
   (`--base` names another ref; `origin/main` is the default CI relies on,
   `test_the_history_check_compares_with_origin_main_by_default`) and pairs each of its entries, one
   to one, with an entry there that scored at least as high: its own; a same-named definition's of
   its file (a renumbering, §2); or one whose function is gone from the tree now (a rename, what
   `--rename` writes). An entry left unpaired fails, as a raise if it was there before and as an
   addition if not (`test_a_baseline_raised_by_hand_fails_against_the_merge_base`, where the code
   check alone passes; `test_an_entry_added_by_hand_fails_against_the_merge_base`;
   `test_a_rename_and_a_lowered_score_pass_against_the_merge_base`;
   `test_a_rename_at_the_same_score_passes_against_the_merge_base`;
   `test_a_rename_may_not_carry_a_higher_score_against_the_merge_base`;
   `test_an_addition_paired_with_a_function_still_in_the_tree_is_not_a_rename`;
   `test_one_vanished_entry_carries_one_rename_only`; an entry that kept its own key and score is
   paired first, so the leftover is named as the addition it is,
   `test_an_addition_beside_a_listed_same_named_definition_is_named_as_added`). It is the merge base, not `origin/main`'s tip,
   so a branch that main has since passed by lowering a score is not failed for it
   (`test_a_branch_behind_a_main_that_lowered_a_score_passes`). The check says so and passes when the
   merge base has no baseline (the change that adds it,
   `test_a_merge_base_without_a_baseline_has_nothing_to_compare`) or was measured with another ruff
   (§7). Without git or the ref it says so and passes, except under `--require-base`, which CI passes
   (`test_without_a_merge_base_the_history_check_says_so`); the CI checkout already fetches full
   history for ADR-0197's gate.

   On a push to `main` the merge base with `origin/main` is `HEAD` itself, so the comparison would be
   with the same file. CI therefore also passes `--github-event`: on a push to `main` the script
   compares with the commit that push replaced, the event's `before`, read from
   `$GITHUB_EVENT_PATH`; every other event, and the push that created `main`, keeps the merge base
   with `origin/main` (`test_a_push_to_main_compares_with_the_commit_it_replaced`,
   `test_other_github_events_keep_the_merge_base_with_origin_main`). What is caught where, measured on
   clones made the way `actions/checkout` v5.0.0 makes them (`fetch-depth: 0`,
   `+refs/heads/*:refs/remotes/origin/*`), with GitHub's environment for each event, running CI's
   command (2026-11-10, `.omc/probes/293/fix-r1/`):

   | event | compared with | code and baseline raised | entry added by hand | lowered and renamed | two PRs reuse one deleted entry |
   | --- | --- | --- | --- | --- | --- |
   | `pull_request` | merge base of GitHub's merge ref with `origin/main` | exit 1 | exit 1 | exit 0 | exit 0 when the run was made before the other PR landed |
   | push to another branch | the branch point with `origin/main` | exit 1 | exit 1 | exit 0 | exit 0 |
   | push to `main` | `before`, the `main` the push replaced | exit 1 | exit 1 | exit 0 | exit 1 |

   Without `--github-event` the push to `main` exits 0 in all four. The two-PR case: each PR deletes
   the same listed function and carries its entry with `--rename` to a function of its own; each run
   compares with the `main` both started from and passes, and GitHub does not run a PR again when the
   other lands. Once both are on `main` the baseline has one entry more than before, and only `main`'s
   run sees it. What the history check still cannot tell: a rename is paired by score, not by code,
   so deleting a function over 20 frees its score for one new function at or below it; that still
   needs a function over 20 deleted in the same change, and the diff shows both. And when a newer
   push cancels a `main` run (`ci.yml`'s concurrency group, `cancel-in-progress`), no completed run
   is guaranteed to compare the cancelled push: the newer run compares only with the commit it
   replaced. The cancelled run may already have compared, since the step runs right after sync and
   lint, and then its job log shows the result; this is read from `ci.yml`'s step order, not
   measured on GitHub. A force push to `main` fails the step under `--require-base` only when its
   `before` is not in the clone; when another branch still holds the old tip, the run compares with
   `git merge-base HEAD <before>` (round-2 verify,
   `.omc/probes/293/verify-r2/probes/p_ci_events_r2.out`).

7. **The ruler is the Ruff version.** The score depends on the ruff that `uv.lock` pins, and the
   baseline records the version it was measured with. `--check` fails, and `--update` and `--rename`
   refuse, when the running ruff is another version
   (`test_a_baseline_from_another_ruff_fails_until_init`), and a test holds the committed baseline's
   version equal to `uv.lock`'s
   (`test_the_committed_baseline_is_what_the_script_writes_for_the_locked_ruff`). A change that
   upgrades ruff re-measures: it bumps ruff in `uv.lock`, runs `--init`, commits the baseline, and
   changes nothing else. The history check then prints the old and new scores side by side instead of
   comparing them, and the reviewer reads that list; a code change in the same change would ride on
   the re-measurement, which is why there must be none. So that the list is not only stdout of a
   green step, in GitHub Actions the step also writes it to the job summary and raises a warning
   annotation naming the re-measurement (`test_a_ruff_upgrade_is_shown_not_compared`). This change's
   choice, not the owner's, over failing the step until a flag acknowledges it: CI's command is fixed
   in `ci.yml`, so the acknowledgement would have to be committed by the same change that
   re-measures, which adds no second pair of eyes, and the push that lands it on `main` would need it
   too; the summary and the annotation are where the reviewer and `gh run view` look. A stronger
   check, measuring the merge base's tree with the new ruff and comparing like with like, is not
   built.
   A ruff whose C901 message no longer reads `` `name` is too complex (N > M) `` stops the gate with
   exit 2 rather than misreading it.

8. **CI runs it on every leg.** `Complexity ratchet (ruff C901)` runs
   `uv run python scripts/check_complexity.py --check --require-base --github-event` after
   `Lint (ruff check)` in the
   test job, without `continue-on-error`
   (`test_ci_runs_the_ratchet_on_every_leg_before_the_tests`). On the real tree it took 1.25 to
   1.31 s including `uv run` (three runs on a macOS laptop under load, 2026-11-10; 0.94 to 1.00 s
   before the measurement named its files, 2026-11-09); it has not been timed on a CI runner. On
   every leg rather than once, because
   the Windows legs are where ruff's paths arrive with drive letters and backslashes and must still
   match the baseline's identities; a separate job would cost a checkout, uv and a sync for a step of
   about a second. Not measured on a
   Windows runner before this merge: the first CI run of this change is that measurement.

9. **The trend value is radon's count with each function, method and closure once, per release;
   the 2026-09-19 count is written beside it.** `uv run python scripts/check_complexity.py --trend`
   runs radon 6.0.1 through `uvx` (no dependency added) over `packages/` and `src/` and prints how
   many functions, methods and closures there are and how many score over 20 and over 50, counted
   two ways; RELEASING.md writes both lines into the release's CHANGELOG section. It is not a gate.
   radon's JSON lists every method twice, at the top of its file and again in its class's `methods`,
   and the trend value the owner set on 2026-09-19 in docs/05 (4,774 / 74 / 5) counted both: run on
   `026bd15` the script reproduces 4,774 / 74 / 5 that way and gives 3,320 / 65 / 5 with each method
   once. On 2026-11-09 the owner decided to switch: from then on each release writes the count with
   each method once as the trend value, and the old-style value beside it for comparison. The first
   line is the trend value, each once; the second is the 2026-09-19 way, labelled as counting each
   method twice and as being for comparison, not the trend value
   (`test_the_trend_value_counts_each_method_once_and_the_old_count_follows`). On `6caaae09`:
   5,508 / 133 / 12 each once, 7,969 / 168 / 13 the 2026-09-19 way.

## Consequences

- From this change on, a function in `packages/` or `src/` cannot grow, and a new one cannot pass 20,
  without a red CI step that names it. The batch plan in #293's body has the B7 stages that land after
  this one pass it.
- Every change that makes a listed function simpler carries a one-line baseline diff from `--update`.
- #284's refactors move closures and functions between files: a move of a listed one is a
  `--rename`, which the history check accepts because the old identity is gone and the score did not
  rise. Moving out a closure that is not listed lowers its parent, which is an `--update`. One
  exception takes two changes. Moving a listed definition out of a file in which a same-named
  definition follows it, or into a file above a same-named listed one, renumbers the others in the
  same change, and neither `--rename` nor `--update` writes that baseline: `--rename` is refused,
  because the old identity is still there on the sibling or the carried score would rise, and
  `--update` never adds the moved one (round-2 verify probes S3, S4 and S6,
  `.omc/probes/293/verify-r2/probes/p_setdesign.out`;
  `test_moving_a_listed_definition_out_from_before_a_same_named_one_takes_two_changes`). The route
  is two changes, the first merged before the second. To move one out, first reorder so that it is
  the last of its name and run `--update`, then move it and `--rename` its `#n` identity. To move one
  in, first put it below the same-named one with `--rename`, then reorder and run `--update`
  (`.omc/probes/293/verify-r2/probes/p_s3_route.out`, `.omc/probes/293/finish/p_routes.out`). This
  is documented, not supported: no listed entry had a same-named sibling on `6caaae09`
  (`.omc/probes/293/verify-r2/probes/p_real_siblings.out`).
- The gate measures `packages/` and `src/` only. `scripts/` and `tests/` are not measured, as they were
  not in the owner's measurement.
- The gate cannot see a split made only to lower the number. That rule (the owner's "속이지 않기")
  stays with review and with #284's issues, which name what each split serves.
- The baseline is a checkout-only gate input like `citations.lock.json`, so the root sdist excludes it.
- The gate needs a git checkout: it measures the files git tracks. A tree exported with
  `git archive` must be made a repository (`git init`, `git add -A`) before it can be measured.
