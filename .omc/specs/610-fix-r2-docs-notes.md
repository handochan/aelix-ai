# #610 fix round 2 — lane R2-docs notes (E1–E4)

Task list: `610-progress-2026-10-30.md`, "문서 (레인 R2-docs, R2-code 뒤)", items E1–E4.
The code these docs describe is `00708c39` (lane R2-code). I read it through `git show HEAD`, `610-fix-r2-code-notes.md` and the code itself (`child_session.find_resume_target`, `_check_child_file`, `place_children`, `SpawnReceipt._describe_parent`/`_allocate`, `print_channel.resume_limits`, `narrow_tools`, `tool._tail_notes`).

## Base

- Worktree `~/dev/aelix-ai-worktrees/claude-610`, branch `feat/610-agent-resume`.
- HEAD was `00708c39` at the start, and `git status --short` was empty. The task said HEAD would be `9cdf76d0`, but R2-code had committed `00708c39` on top of it, as the task also said it would.
- `env -u VIRTUAL_ENV uv run --no-sync python -c "import aelix_agents; print(aelix_agents.__file__)"` printed `…/aelix-ai-worktrees/claude-610/packages/aelix-coding-agent/src/aelix_agents/__init__.py` on 3.12.13.
- I did not edit the main checkout. I only read its untracked `.omc/specs/610-review-r2-verify-probes/` and `.omc/specs/610-live-drivers/`.
- I changed no code and no test file. Files changed:
  - `docs/decisions/0277-…md`;
  - `docs/decisions/0243-…md`: the #610 note under the record table only;
  - `docs/decisions/README.md`: the 0277 entry only;
  - `docs/guides/agent-profiles.md` and its bundled copy;
  - this file.
- `CHANGELOG.md` and ADR-0276 were searched and left unchanged. See E1.
- ADR-0277's `Status:` line and the index's `(Proposed 2026-10-30; #610)` are unchanged, as the task says.
- Scratch files are in `<session scratchpad>/610-r2-docs/`: `strings.py`, `check_quotes.py`, `check_resume_quotes.py`, `check_test_names.py`, `test_reopen_under_other_name.py`, and the citation pre/post diffs.

## Strings, printed by import (not from memory)

From `strings.py`, which imports `aelix_agents.child_session` and `aelix_agents.runtime` and prints `repr`. Only the lines the docs quote are copied here:

```text
cs.RESUME_NOT_YOURS = 'Resume refused: that agent was not delegated by you — the user started it (/agents run) or a client did — and only an agent you delegated can be resumed. Delegate the task anew.'
cs.RESUME_FORKED = 'Resume refused: that agent belongs to the conversation this one was forked from. Resume it there, or delegate the task anew.'
cs.RESUME_NO_SESSION = "Resume refused: this conversation is not saved in aelix's sessions directory (--no-session, or a file opened from elsewhere), so no agent it delegated has a session to continue. Delegate the task anew."
cs.RESUME_DAMAGED = "Resume refused: that agent's session record is damaged ({detail}). Delegate the task anew."
rt._TARGET_CHANGED = 'its record changed before the resume could start'
damage details (resume_damaged("…") in child_session): … its session directory is a symbolic link / its session file is a symbolic link / its session file is not a regular file / its session file has another name (a hard link) / its session file is reached through a link / its session header is not valid / …
```

- `tool._tail_notes` was read for limit 16. The note is `"Tools not granted to this agent: " + ", ".join(dropped) + " (it holds no tool you do not hold)."`.
- `print_channel.resume_limits` gives `dropped=tuple(tool for tool in stored.tools if tool not in allowed)`: the recorded tools only.
- `narrow_tools` gives the first run's `dropped = requested - allowed`: every profile tool that run was not given.

## E1 — the derivation rule and `RESUME_NOT_YOURS`

Searched with `grep -n -i -E 'realpath|really is|links resolved|its links|real path|reached through|through a link|link anywhere|NOT_YOURS|not delegated by you|derived from|derives|place_children'` in ADR-0277, ADR-0243, ADR-0276, the index, the guide and CHANGELOG, then read each hit in context.

| Place | Was | Now |
|---|---|---|
| ADR-0277 D6, first bullet | `place_children(<the parent's file now, its links resolved>)…` | `place_children(<the parent's file, as its session names it now>)…, as SpawnReceipt._allocate placed the first run's file (refined in review round 2, below)`. This is the `ResumeTarget.path` docstring's wording. |
| ADR-0277 D6, round-1 link bullet | "a link anywhere below the parent's directory… placement is derived from `os.path.realpath`… a link above… is resolved…; a parent whose own file is a link is placed where that file really is" | "a link below the parent's directory", in the children's directory or the file. After their `lstat`s, `realpath(file)` must be `realpath(parent's dir)` plus the two names; this also catches a junction and a `..`. It says the comparison is G1's, and states round 1's old rule as history. |
| ADR-0277 D6, new "Refined in review round 2 (G1…)" bullet | — | See below. |
| ADR-0277 D11 table, `RESUME_NO_SESSION` row | "none inside a bucket once its links are resolved" | "none inside a bucket, by the path its session names (D6)" |
| ADR-0277 D11 table, damage row | "a link on the way to the file" | "a link below the parent's directory — the children's directory or the file (D6) —" |
| ADR-0277 D11, F13 paragraph | "`RESUME_NOT_YOURS` still says only why." | "…was left saying only why, until review round 2 (G4, below)." |
| ADR-0277 D11, after the round-1 sentence list | — | "Refined in review round 2 (G4…)", with `RESUME_NOT_YOURS` quoted verbatim. It is the only model-facing sentence round 2 changed (R2-code notes, G4). |
| ADR-0277 limit 15 | "A file or directory swapped for a link…" | Names the file or the children's directory. Adds: a link at or above the parent's directory is followed at every step, the first run's included (D6, G1). |
| ADR-0277 "Not checked", Windows bullet | junctions "were not run there" | The same, plus: a junction as the children's directory is only simulated on macOS, with an `lstat` that follows links (G1). |
| ADR-0243 §3 #610 note | "derives the file from where the parent's file really is now (its links resolved)" | "derives the file as §1's allocation placed it — from the parent's file as its session names it now, and the first run's key —". The rest of the note is only re-wrapped. |
| Index, 0277 entry | "derived from where the parent really is now"; "a missing, damaged or linked file" | "derived as allocation placed it, from the parent's file as its session names it"; "a missing or damaged file or a link below the parent's directory". The tail is re-wrapped. |
| Guide (both copies) | "its session file is gone, damaged, reached through a link or under a second name (a hard link)" | "its session file is gone, damaged or under a second name (a hard link), it or the `<name>/` directory that holds it is a link (a link higher up, such as a sessions directory on another volume, is not refused)". `<name>/` is the guide's own name for the children's directory. |

**The G1 bullet in D6** says, from the R2-code notes and the round-2 verifier:

- Round 1's lookup took `realpath` of the parent's file, while `_allocate` had taken its `abspath`, so the two disagreed at the parent's level:
  - parent-file link → `RESUME_FORKED`;
  - bucket linked to a plain name → `RESUME_NO_SESSION`;
  - linked root or bucket → resumed, but under the resolved spelling.
- `883ded81` resumed all three. That is the verifier's probe at a `883ded81` tree.
- Now a link at or above the parent's directory is taken as allocation took it. A parent opened by the first run's path launches the resume with that run's `--session`, byte for byte.
- A parent reopened through another spelling of a link above its bucket finds its child under that spelling.
- A parent reopened under another NAME of its own file is refused `RESUME_FORKED`, as at `883ded81`.
- No sentence or damage detail changed.

**The "another NAME" sentence was probed, not taken on trust.** The probe is `test_reopen_under_other_name.py` in the scratch folder. It uses the tool test's `_delegated_under_a_link(tmp_path, "parent-file")`, then reopens the parent as `real.jsonl`. It was run from the worktree root with `env -u VIRTUAL_ENV uv run --no-sync python -m pytest -q -s -p no:cacheprovider --rootdir=. -c pyproject.toml <probe>`. Output:

```text
SAME NAME -> /private/var/folders/…/test_reopened_as_the_links_tar0/s/--private-var-folders-…-project--/p/sub-ec941b886975.jsonl
OTHER NAME -> Resume refused: that agent belongs to the conversation this one was forked from. Resume it there, or delegate the task anew.
1 passed in 0.39s
```

**Left unchanged, after reading:**

- `CHANGELOG.md`, the #610 entry. Nothing in it describes the derivation or quotes `RESUME_NOT_YOURS`. Its refusal list says "a missing, damaged or replaced session file" and "an agent started with `/agents run`", which are still true after G1 and G4. Only the guide said "reached through a link".
- ADR-0276's three #610 notes: no derivation, no link and no `RESUME_NOT_YOURS`. The grep hits ("place of D11's retry sentence", "a real model resuming…") are about other things.
- ADR-0243 §1 and Consequences (`place_children`, "its links may dangle"): original text, not #610's.
- The index's ADR-0243 row ("지금 부모 위치에서 자식 파일을 유도한다"): it does not say "real", and it is still true.
- ADR-0277 D5's `RESUME_NOT_YOURS` (name only) and the D11 table row (name only).
- The D11 damage-detail paragraph. `its session file is reached through a link` is still the detail, and the code emits it.

## E2 — limit 16

Added under a new "Added in review round 2:" line, after limit 15. Limits 1–15 are unchanged.

- **Title:** a resume's "Tools not granted" note names only what the intersection takes away now (round-2 live check, item 8).
- **Why:** this is D9's `dropped`, the recorded tools only. A profile tool the first run was not given is outside the ceiling, not taken away by this resume. The first run's result named the ones the profile listed then, and the note's reason, "it holds no tool you do not hold", would be wrong for them now.
- **The live evidence:**
  - Round 2 (`c6e766bd`, `l4_not_widened.py widen`): the resume got 2 of the profile's 5 tools and its result had no note. The first run's result had one.
  - The text of the first run's note is quoted from round 1's capture, as round 1 printed it: `Tools not granted to this agent: bash, grep, write (it holds no tool you do not hold).` The round-2 report does not quote the note's text.

## E3 — `### Live`

- The heading is `### Live`. The checklist is gone.
- One intro paragraph covers:
  - the two rounds, with SHAs and report files;
  - the setup: the drivers, HOME and the agent dir per run, the scripted mock, the real model, and the tap;
  - that no capture holds the key;
  - where the drivers are;
  - that no live check ran after fix round 2.
- Three groups, kept apart:
  1. **What #610 decides.** L1–L5. Each check names its driver and command, and the SHA of each quote.
     - L1: the decisive wire lines, the Anthropic `tool_result`, the receipts, the argv, and the F9 cancel report verbatim. Also the F10 rows, and the `/private/tmp` `--session` spelling at `c6e766bd` against `/tmp` at `54cd7f61` (L4's argv), which is the spelling G1 restored.
     - L2: haiku's call, the resumed child's first request and what it did, in both rounds. The done criterion held in both.
     - L3: run 1's result at `54cd7f61` verbatim, the F9 guidance at `c6e766bd`, the 7 → 9 lines, `plan` and why, and the SIGKILL run.
     - L4: round 2's table, round 1's full argv, and `l4_third`'s yolo/plan/yolo.
     - L5: round 1's dialog verbatim, and the F11 row at all three sizes.
     - The criterion held in both rounds. Round 1's wording findings (live items 6, 3 and 10) became F9–F11.
  2. **Model behaviour.**
     - A killed child resumed unasked (`l3_real`, both rounds).
     - "what happened?" got no resume.
     - A cut sleep reported as finished (children in round 2, the L2 parent in round 1, `l3_real`'s final answer), and the parent's instructions that skipped step 2.
  3. **Existing behaviour #610 does not change.**
     - The tool card cut (`_compact_args`).
     - The 80-column notice row and status bar cuts.
     - 80x24: the 21-row dialogs, with round 2's table and bottom rows.
     - #619 leftovers.
     - `✖ Operation aborted` only (#132).
     - Consecutive user-role messages on the Anthropic wire: the child in round 1, the parent in round 2.
- It closes with what was not checked live (Windows, Linux, anything after fix round 2), and what is still to run at the merge commit (the full suite, the CI legs).

**Quote check.** `check_quotes.py` checks every fenced-block line, every code span of 12+ characters, and every double-quoted string of 12+ characters in `### Live` and in limit 16. Each must occur in one of the two reports, with whitespace normalised. Result: `checked 145, missing 5`. All five are expected:

- the two report file names;
- `.omc/specs/610-live-drivers/`, a path from the progress file;
- `--session /tmp/610-live/runs/l4-widen/…`, an elided prefix of r1's L4 argv, which does occur;
- "The posture is the lower of the two", the guide's paragraph title.

Verdicts and classifications come from the progress file's "리뷰 2회차" (판정) and "이번에 고치지 않는 것" lists. "(#132's follow-up)" and "a follow-up candidate" are from there.

## E4 — Verification

- **Intro:** the bullets now name the fixes of review rounds 1 (`Fn`) and 2 (`Gn`).
- **D5–D7, "Round 2":**
  - G1: two tool tests (4 cases each, POSIX), the junction simulation, and the changed assertion of `test_a_parent_opened_through_a_linked_directory_still_finds_its_child`;
  - G2: `test_a_later_line_moves_neither_the_target_nor_the_listing` (4 cases).
- **D11/D12, "Round 2":**
  - `test_a_record_that_changed_what_the_dialog_showed_is_refused_at_the_launch` (G2, 2 cases; it launched under the key/path/limits-only `_same_target`);
  - `test_the_refusals_that_said_only_why_now_say_what_next`, which now holds `RESUME_NOT_YOURS` (G4).
- **Mutation paragraph:**
  - Round 1's "placement from `abspath`" mutant is now annotated "(the rule again since G1)".
  - Added R2-code's run: 45/45, the 39 verifier mutants with 2 new patterns plus 6 added, over 343 tests, restored from saved bytes and checked by sha256.
  - The five round-2 survivors, and G1/G2/G4's own mutants, are named by what they remove.
- **"What the lanes ran":** added R1-docs (586 passed, 20 skipped), R2-code and R2-docs (586 passed, 20 skipped). R2-code's numbers:
  - three #610 files, 282 test cases (267 before), 282 passed on each of 3.12.13, 3.11.15 and 3.14.5;
  - `tests/agents_ext` + `tests/agents`: 2 637 passed, 11 skipped;
  - the oracle probes: 21 passed.
  - "Not run by the lanes: the full suite" now says each review round ran it.
- **New `### Review round 1 (54cd7f61)`**, from the progress file's "리뷰 1회차" and the review files:
  - suite r0: `1 failed, 19092 passed, 49 skipped, 114 warnings in 865.74s`, and the failing test;
  - Codex: 0/8/2, 23 probes passed, and what it confirmed (972 argv combinations, overlapping resumes, 6 adapters, `runs=2`);
  - the independent check: 0/1/6 plus doc mismatches, 2 712 passed and 11 skipped, 337 on each of 3.11/3.14, 1 296 argv combinations, 27/30 mutants;
  - live L1–L5;
  - the fix-round SHAs.
- **New `### Review round 2 (c6e766bd)`**:
  - suite r1: `19135 passed, 49 skipped, 114 warnings in 854.20s`, exit 0, with its command;
  - the delta check: 0/0/4, with m1–m4 each mapped to G1–G4;
  - what it found correct: 34/39 mutants, one door, 9 Codex counterexamples refused, probes 22 passed, the linked setups, 20 000 combinations / 5 599 distinct argvs, 5 000 through `/agents run` and RPC, docs quotes, pyright Windows 0 product errors;
  - the live re-check: 13 resumes, 0 refused;
  - the lead's verdict, and fix round 2 (`00708c39`).
- **Header and Decision intro:** they name review round 2, G1–G4 (`#610 Gn` in the code), the review and live report files, and the four fix-lane notes files. "Refined in review round 2" is defined with `Gn` and the round-2 verify's mn.

**Not copied: a docs-check count.** The progress file's 리뷰 1회차 says "하위 에이전트의 문서 점검: … 과장·오인용 6곳". The round-1 verify report says its docs subagent "did not deliver a report, even after two requests". The two disagree, so the ADR says only "documentation mismatches", which both support. That is open for the lead.

## Checks

**Code strings.** `check_resume_quotes.py` imports the module. Every `- \`RESUME_X\`: \`…\`` bullet in ADR-0277 equals its constant, and every quoted `its …` damage detail is one the code emits or `runtime._TARGET_CHANGED`. Result: `checked 16, bad 0`.

```text
OK  RESUME_NEEDS_ID / RESUME_UNKNOWN_ID / RESUME_NO_SESSION / RESUME_UNREADABLE / RESUME_LOCK_ERROR / RESUME_CWD_GONE / RESUME_CONVERSATION_CHANGED / RESUME_UNSUPPORTED_CHANNEL / RESUME_NOT_YOURS
OK  detail: its record changed before the resume could start, its records disagree about the limits it ran with, its session file has another name (a hard link), its session file is not a regular file, its session header is not valid
```

The regex did not catch the line-wrapped span `its\n session file is reached through a link`. It was checked by hand: it is a `resume_damaged` detail in `_check_child_file`.

**Cited test names.** `check_test_names.py` checks that every `test_*` name has a `def`/`async def` under `tests/`, or is a test file's stem:

| Doc | Names cited | Missing defs |
|---|---|---|
| ADR-0277 | 140 (135 at `c6e766bd`; the 5 new G1/G2 names) | 0 |
| ADR-0243 | 4 | 0 |
| ADR-0276 | 10 | 0 |
| CHANGELOG (Unreleased) | 13 | 0 |
| CHANGELOG (whole file) | 17 | 0 |
| guide | 0 | — |

No ADR text cites code by line number. Cited symbols: `SpawnReceipt._allocate`, `ChildLimits.from_record`, `_compact_args`, `_same_target`, `_lineage`.

## Commands and outputs

All were run from the worktree root with `env -u VIRTUAL_ENV uv run --no-sync …` on 3.12.13.

**Bundled guide:** `python scripts/sync_bundled_docs.py` printed `copied agent-profiles.md` / `12 guide(s) bundled — 1 updated, 0 removed.`. Then `cmp docs/guides/agent-profiles.md packages/aelix-coding-agent/src/aelix_coding_agent/docs/agent-profiles.md` showed them identical. The guide was edited once, before that sync.

**Citations:**

- Before: `git diff > pre-fix.diff`, and the lock's sha256 was `2fe4b639…f6a6`.
- `python scripts/check_citations.py --fix` printed `relocated 0 citation(s) across 0 file(s).` / `locked 540 anchor(s) over 914 gated citation(s) (42 ungated, 0 out of range, 17 too generic to gate).`
- After: `git diff > post-fix.diff`, and `cmp` with the pre-fix diff showed them identical, so `--fix` changed nothing. The lock's sha256 was unchanged.
- `--check` printed `citations OK — 914 gated, none drifted.`
- ADRs are not gated (`docs/guides/**` and root `*.md` are). The guide edit adds no citation.

**Ruff:** `ruff check $(git diff --name-only)` printed `warning: No Python files found under the given path(s)` / `All checks passed!`. No `.py` file changed.

**Type gate:** `python scripts/check_types.py` printed `OK  gate: 0 errors across 303 files` / `PASS: type gate clean, narrowing spike still inverted`.

**Whitespace:** `git diff --check` was clean.

**Tests.** The CHANGELOG readers were found with the glob quoted: `grep -rl 'CHANGELOG' tests/ --include='*.py'` lists 22 files. Four of them open `CHANGELOG.md`: `test_yolo_disclosure_copy`, `test_catalog_relative_source_131`, `test_build_hygiene` and `test_ci_hang_guard_429`. The other 18 only mention it, and `CHANGELOG.md` is unchanged.

`python -m pytest -q -p no:cacheprovider tests/docs tests/test_docs_bundle_sync.py tests/cli/test_docs_signpost.py tests/cli/test_docs_command.py tests/packaging_gate/test_docs_bundle.py tests/agents_ext/test_yolo_disclosure_copy.py tests/test_citation_drift.py tests/cli/test_catalog_relative_source_131.py tests/packaging_gate/test_build_hygiene.py tests/test_ci_hang_guard_429.py`:

- at `00708c39`, before any edit: `586 passed, 20 skipped in 23.51s`;
- on the edited tree: `586 passed, 20 skipped in 23.60s`;
- after the last ADR edit and this file: `586 passed, 20 skipped in 24.00s`.

The quote, string and name checks above were re-run on that final tree, with the same results (145/5 expected, 16/0, 140/4/10/13 names with 0 missing). After it, `cmp` showed the guide copies identical, `--check` printed `citations OK — 914 gated, none drifted.`, and `git diff --check` was clean.

As a sanity check, not required, the three #610 test files on the final tree gave `282 passed in 3.56s`, R2-code's count. No code changed in this lane.

Not run: the full suite (the lead's), Windows, Linux, and any live check.

## Deviations

1. **`CHANGELOG.md` is unchanged.** E1 names it as a target, but nothing in it describes the derivation or quotes `RESUME_NOT_YOURS`.
2. **E1 also touched limit 15 and the Windows bullet.** The progress file's E1 lists "Limits의 링크 항목", which are those two.
3. **The ADR header and the Decision intro were extended.** They name review round 2's files and the `Gn` convention, so the new "Refined in review round 2" notes and the `#610 Gn` references in the code resolve.
4. **D6 says what happens to a parent reopened under another name** of its own file (`RESUME_FORKED`, as at `883ded81`). I probed this instead of taking it from the code notes. It is not a new Limits item: E2 asks for exactly one, and this is not a G1 change.
5. **The L1 entry records the `--session` spelling seen at `c6e766bd`** (`/private/tmp/…`) next to round 1's `/tmp/…` (L4). This is live evidence of verify m1's second half. Both strings are verbatim from the reports. That round 1's spelling is allocation's comes from the code at `54cd7f61` (`git show 54cd7f61:…child_session.py`: `place_children(parent_path)`).
6. **Round 1's docs-check count was not copied** (see E4): the progress file and the verify report disagree.

## Open for the lead

- ADR-0277's `Status:` line and the index's `(Proposed 2026-10-30; #610)`, as the task says.
- The full suite at the merge commit, and CI.
- No live check has run since fix round 2. G1 is covered by the tests and by the verifier's oracle probe (R2-code: `21 passed`, three times "the child says hello", and the TMP_LINK resume on the first run's `/tmp` spelling).
- The progress file says the round-1 docs subagent found "과장·오인용 6곳". The verify report says that subagent never delivered.
