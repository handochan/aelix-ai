# Issue 417: show renamed sessions in both resume pickers

## Baseline and plan

- Base: `e47f1101f8b7499125ff55f0ee18d8b9873d8cdf`; branch `fix/417-resume-session-names`.
- Isolated tree: `/Users/handochan/dev/aelix-ai-worktrees/codex-417`.
- Registered from owner request: <https://github.com/handochan/aelix-ai/issues/417>.
- Existing session_info writes are correct; both pickers share a formatter which ignores those entries. Repair label reading rather than alter the session schema.

## Implemented and verified

- Shared labels choose the latest valid saved name, then first user message, then `(no messages)`.
- Saved names use the same complete-file decoder and corruption recovery as session reopen. A name survives subsequent messages larger than 128 KB; malformed records and orphaned descendants are skipped. A valid blank/null/missing latest name clears the title.
- Names pass through existing terminal control sanitization and terminal cell truncation.
- `/rename` resolves as an alias of existing `/name`; both persist through the same handler.
- Source/bundled getting-started guide synchronized.
- Focused final gate: `uv run pytest -q tests/cli/test_resume_session_names_417.py tests/cli/test_session_labels.py tests/tui/test_commands.py` → 166 passed. Includes real JSONL repo create/name/name/reopen/list.
- `uv run pytest -q tests/packaging_gate/test_docs_bundle.py` → 6 passed.
- `uv run ruff check .` → All checks passed.
- `uv run python scripts/check_types.py` → 287 files, 0 errors, all 3 inverse spike assertions alive.
- `uv run python scripts/check_citations.py --check` → 955 gated, no drift (19 existing ambiguous anchors).
- Actual PTY `uv run aelix --provider openai-codex --model gpt-5.5 --session-dir /tmp/aelix-417-live-sessions --approve`: real model returned READY; `/rename Resume-417-first`, `/rename Resume-417-latest`, `/new`, `/resume` showed latest title; typed filter+Enter resumed its 2 messages. `/session` confirmed name and id `1dfa49a4-5f9e-45c1-a8a3-1640dbc37b12`.
- Quit/restarted with the same command plus `--resume`: numbered row 2 displayed `Resume-417-latest`; selected 2; `/session` confirmed same id/name and READY transcript. Both processes exited cleanly via `/quit`. Global settings and real sessions untouched.
- Independent reviewer issue_197 found a deeply nested malformed name could raise RecursionError and hide a previous valid title. Fixed per-record handling, added a 20000-level regression; reviewer recheck 41 passed, no outstanding finding.
- Full local suite: `uv run pytest -p no:cacheprovider -q` → 58 failed, 14428 passed, 45 skipped, 3 errors in 1008.73s. Terminal tests inherited `NO_COLOR=1` / `TERM=dumb`; scaffold fixture selected an obsolete system Python 3.9.
- Baseline renderer reproduced identical terminal failures; setting `TERM=xterm-256color` and unsetting `NO_COLOR` made all 136 pass on baseline and #318.
- Bootstrapped pip/hatchling only in this isolated venv, then reran all 61 failed/error nodes plus the final eight new name regressions with corrected terminal environment via `uv run --no-sync pytest -p no:cacheprovider -q` → 69 passed in 5.95s. No production or dependency-file change for environment issues.
- Local host macOS Python 3.12; complete Linux/Windows gates are PR CI scope. Final type gate again reports 287 files / 0 errors.

## Next

Implementation and independent review are complete. Create PR and move Project 1 to In review; collect complete remote CI. Leave issue open until approved merge. Keep original checkout intact.

## Disproved assumptions

- Saving a name does not automatically populate the resume metadata: the label function read only first user messages.
- The current builtin only accepted `/name`; the user's `/rename` spelling needed an alias.
- A tail-only name lookup loses names after long later messages.

## Delivery refresh after concurrent main movement

Claude Code merged #192 while this batch was running. Delivery base is now `6b5ffc814041de36866f79df71c51992ec76891c`. The original checkout was not edited by this workflow. Rebase completed without conflicts.

On this new base: focused tests 172 passed; Ruff passed; type gate 287 files / 0 errors with inverse assertions intact; citation gate 955 gated / none drifted. The initial full-suite and real-model observations above belong to the initial e47f1101 base. Fresh PR CI now includes Linux and Windows on Python 3.11, 3.12 and 3.13, plus both Windows installer shells.

## Independent review repair — 2026-10-09

The earlier "no outstanding finding" statement describes the original review,
not this later pass. A fresh review reproduced invalid common metadata changing
or clearing the picker label while core retained the saved title; an orphaned
rename after a corrupt ancestor and a JSON-escaped `session_info` type also
disagreed with reopen. See the review evidence in
`/Users/handochan/dev/aelix-ai-worktrees/codex-review-422/.omc/specs/handoff-review-422-2026-10-09.md`.

The repair extracts one private pure core parse/recovery helper and reuses it
for label inspection, removes the tail/prefilter authority, and validates
`session_info.name` as string-or-null in the storage decoder. Fixtures now have
valid session headers/common fields/message blocks; actual persisted-session
regressions compare both picker paths with a fresh reopen. The full-file name
read/decode cost is accepted for correctness and recorded in ADR-0208's
amendment; preview timings are not name-lookup measurements. Picker inspection
does not log recovery or rewrite the file. Source completion still needs fresh
independent review and root's current-main rebase/CI before approval or commit.

### Repair verification (uncommitted 8aab077f worktree)

- `env -u NO_COLOR TERM=xterm-256color uv run --no-sync pytest -q tests/cli/test_resume_session_names_417.py tests/cli/test_session_labels.py tests/cli/test_resume_flag.py tests/tui/test_commands.py tests/session/test_session_corrupt_recovery.py tests/session/test_storage_conformance.py tests/session/test_jsonl_write_discipline.py tests/session/test_jsonl_fork_from.py tests/agents/test_p2_band_boundaries.py tests/test_docs_bundle_sync.py`: **381 passed in 2.53 s**.
- Built docs: `uv run --no-sync pytest -q tests/packaging_gate/test_docs_bundle.py`: **6 passed in 1.78 s**.
- `uv run --no-sync ruff check .`: **All checks passed**. Only the amended test file was formatted; existing source formatting was left intact.
- `uv run --no-sync python scripts/check_types.py`: **287 files, 0 errors, all 3 inverse assertions retained**. `uv run --no-sync pyright --pythonplatform Windows`: **0 errors, one existing covariance warning**.
- Citation sequence: first `--check` identified 3 drifted anchors; actual target lines were inspected (`create_entry_id` at 703-704, leaf assignment at 712, get-leaf/path ranges at 680-686 / 723-741). Then `--fix` relocated 3 citations in 3 files; final `--check`: **955 gated, none drifted**. `git diff --check`: exit 0.
- Actual-old-source sabotage in `codex-review-422`: copied the fixed regression file and ran the missing-id clear, ancestor/orphan, escaped-type, and numeric-name cases. **4 failed in 0.67 s**, respectively at wrong picker fallback, orphan title, stale title, and core `.strip()` failure. Identical cases on repaired source: **4 passed in 0.33 s**. Full old-code output is retained in `.omc/probes/417-repair/old-code-sabotage.log`.
- Repaired-source real OpenRouter/Haiku TUI: `/rename` twice, then injected a malformed rename, malformed ancestor, invalid numeric name and valid orphan rename into the scratch session. `/resume` and restarted `--resume` still displayed `Review417repair-latest`; `/session` showed unchanged id `22c363d3-5777-47e0-901f-560693dc2500`, the real `REVIEW417_READY` transcript, and 2 messages. Both processes exited 0. Screens explicitly contain the valid title and no invalid/orphan title. Evidence and driver: `.omc/probes/417-repair/`.
- Live cwd/agent/settings/session paths were scratch-isolated; only `OPENROUTER_API_KEY` was read from `.env` and it was never printed. Owner auth/settings were not used.
- Product source hashes: jsonl_storage.py `c0f7bd167b79e28e74eeda357087e9fb0e318626986bf421a69e9ad0800fb49d`; session_labels.py `804d8cf41ce0112ab4055ff170d36422423fd8538fb9407a1ec614eb55446a3f`; regression file `98e5014abc4fbbd886a15a98570182b724837e7c1a098635499183f14cfbed49`.
- Full suite, real Windows runtime and remote CI were not rerun in this repair pass. Root owns current-main integration, fresh independent review, commit/push/merge. No commit or remote mutation was performed here.

## Fresh acceptance pass and final hygiene

Fresh no-author-context verifier independently compared1,000persisted fixtures with main: reader semantics preserved except documented invalid-name schema repair;244tests passed. Actual isolatedTUI /rename,/resume,/name and restarted--resume retainedthe same recoveredname. Source lint/types/citations clean,287files0errors/1inheritedwarning. Earlier separate verifier ran937tests andcounterfactual4failures.

Whole-file read cost remains:8.37MB/20,002entries peak33.38MB/20labels1.62s; a separate32MiB singlelargepayload sample0.051s/peak96.9MiB. These are synthetic localmeasurements, not nativeWindows oractualcollectionperformance.

Root formattedonly the newly added condition (AST unchanged versusfreshfrozenreviewsource) andtyped the deliberatelymalformedJSON fixture generator asobject, removingone new optionaltest-typeerror.32name regressions passed0.47s;three actualsourcecitationtargets verifiedandrelocated,955gatednone drifted. Existingforcedtest-typeerrorsandwhole-sourceformatfailuresareinherited;the productiongateisclean. CurrentfullplatformCI is required onthefinalorderedpredecessorhead.
