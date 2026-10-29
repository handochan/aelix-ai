I found no blocker and no major issue in the round under review. There are four minor findings. One of them, the first, is a real false refusal that 883ded81 did not have. HEAD was c6e766bd before and after my work, `git status --short` is empty, and I edited nothing in the worktree. Mutants were restored with `git checkout --`. Everything I wrote is under /tmp/610-verify-r1/. The four minor findings come first, then what I checked and found correct.

## Minor findings

**m1. A link at the bucket-directory or parent-file level now refuses every resume, with the wrong refusal text.** This is a regression from 883ded81.
- **Cause:** a first run's child file is placed by `place_children(self.parent_path)` at `child_session.py:833`, where `parent_path = os.path.abspath(raw_path)` (`:808`), so the link's name is used. The lookup uses `place_children(os.path.realpath(parent_path))` (`:1391`), so the target's name is used. The two disagree.
- **Inputs:** (a) the parent's `p.jsonl` is a symlink to `real.jsonl` in the same bucket; (b) the bucket directory is a symlink to a directory whose name is not bucket-shaped. Nothing is widened; the resume is refused, and the sentence the model gets is misleading.
- **Command:** `env -u VIRTUAL_ENV uv run --no-sync python -m pytest -q -s -p no:cacheprovider --rootdir=. -c pyproject.toml "/tmp/610-verify-r1/probes/test_r1_false_refusals.py::test_links_at_the_bucket_or_parent_file_level"`
- **At HEAD:**
  ```
  LINK parent-file … -> Resume refused: that agent belongs to the conversation this one was forked from. …
  LINK bucket-dir-to-bucket-name … -> the child says hello
  LINK bucket-dir-to-plain-name … -> Resume refused: this conversation is not saved in aelix's sessions directory …
  ```
- **The same probe with `PYTHONPATH` pointing at a 883ded81 tree:** all three cases print `the child says hello`.
- **Severity:** low. Links above the bucket all resume (checked below).

**m2. Five F2/F3 mutants survive, all on forged-record paths.** None of them widens a resume.
- Command: `python3 /tmp/610-verify-r1/mut/mutate.py`, output in `/tmp/610-verify-r1/mut/run1.txt`. All five end with `328 passed`:
  - `child_session.py:1258`: `_group_by_child` reading `run.data["child"]`;
  - `child_session.py:1282`: `_lineage` firsts by `run.data["resumes"]`;
  - `child_session.py:1668` and `:1669`: the listing's profile and preview read from `first.data`;
  - `runtime.py:1958`: `_same_target` reduced to first_key, path and limits. Deviation 4's extra fields (profile, rel, task_preview, parent id) are untested.
- Genuine lines of a key always agree, so these only matter when someone forges a record, and the downstream header and profile checks still refuse. Extending `test_what_the_first_run_was_is_read_off_its_start` to assert the listing, and to add a later line with another child or another `resumes`, would kill four of them.

**m3. Open item (a): the comment is real and wrong.** `child_session.py:318-320` says `fresh_limits` records `source_path`, "which discovery always makes absolute". Both halves are false:
- `fresh_limits` records `profile_identity(...)`, which is an abspath.
- Discovery keeps a relative `AELIX_CODING_AGENT_DIR` relative: `discovery._scan_dir` uses `file_path=str(entry)`. My probe printed `REL profile_source /private/tmp/…/agent/agents/scout.md source_path agent/agents/scout.md`.

The behaviour is correct, and the abspath is guarded by a test (the "profile_identity without abspath" mutant is caught). Only the comment needs fixing.

**m4. Open item (b): `RESUME_NOT_YOURS` (`child_session.py:410`) has no next step.** This is real but matters little. The listing excludes such agents, and `/agents run` output is display-only and shows no session id, so the model only gets such an id from outside the conversation. Even then the sentence is final. Adding " Delegate the task anew." would match F13.

## Checked and found correct

**F1–F15 are each implemented as the list says**, with the deviations stated in the code notes:
- F3 resolves links above the parent rather than refusing them; refusing them would refuse everyone on macOS.
- F4 uses `> 1`.
- F5 widens `RESUME_CWD_GONE`.
- F6 imports the loader's own parser.

**Mutants: 34 of 39 caught.** Every mutant named in the fix list is caught. The M1 test alone catches `data = latest.data` (`1 failed`).

**There is one resume door, and the new checks are on it:**
- `resume_granted(` has one caller, `extension.py:1560`, and `_Resume(` is built only at `runtime.py:1134`.
- `/agents run` (`commands.py:1320`), RPC (`control.py:796`), batches (`batch.py:445`) and the single run (`extension.py:1581`) never pass a resume.
- The parser refuses a resume with `tasks`, chain or parallel `mode`, `cwd` or `background`, and a `run` with an `id`.
- F3 and F8 run at both the hook and execute. F7 runs in the parser, so an extension that calls `resume_granted` directly skips it, which is outside the threat model.

**Codex's round-1 probes rerun at HEAD** (I added `resumes_agents = True` to its wrapper channel; output in `/tmp/610-verify-r1/codex/run-at-head.txt`):
- The 9 counterexamples for Codex 1, 3 (three cases), 4, 5, 8, 9 and 10 are now refused with nothing spent. For example: `RPC_CHANNEL error=True budget_spent=0 records_added=0`.
- Codex 2, 6, 7 and the 3-residual still reproduce; ADR-0277 documents these as limits 11, 12, 13 and 15.
- The checks that held in round 1 still hold: the 972 argv combinations, overlapping resumes, the 6 adapters, usage counting, and the bounded head read.

**F2: real histories never trip "records disagree".** File: `/tmp/610-verify-r1/probes/test_r1_false_refusals.py`; result `22 passed`, output in `/tmp/610-verify-r1/probes/run.txt`. All driven through the real hook and `execute()`:
- Esc cancel, then three resumes, the last after reopening the parent from disk.
- `_shutdown()` (the `/new`, `/resume`, `/fork` and quit path) and `stop_all`, then reopen and resume; the recorded phases were `['start', 'settle']`.
- Esc during the opening append, which leaves two start lines (`MIDOPEN starts 2`), then resume.
- A settle written by `bare_records`, then resume.
- A cancelled resume, then a resume.
- A→B→C with a reopen between B and C: `ABC modes auto-accept-edits plan auto-accept-edits tools ('bash','read','write') ('read',) ('bash','read','write') preview the FIRST task`.

**F3/F4/F5/F15: no false refusals through links above the parent's directory.** All of these resume:
- `/tmp`, and an unresolved `/var/folders` root;
- a linked HOME;
- a relative `AELIX_CODING_AGENT_DIR` with a relative sessions root;
- a cwd given as none, relative, absolute-unresolved or absolute-real, and a parent cwd that is itself a link.

The recorded cwd is always the realpath. Bundled profiles resume. A writer lock held through the `/tmp` spelling is seen through the real path (`IN_USE`).

**F6, round-trip through the real writer:** a real aelix child, cancelled mid-bash under the `/tmp` link, resumed twice; both resumes ok, with header `version 3`. That cut `bash` left a `sleep 30` behind, reparented to PID 1; I killed it. This is limit 14 (#619).

**The `run` path's argv is byte-identical to f3e76fbb:**
- 20,000 sampled combinations over 16 dimensions went through the real `spawn_granted` → `PrintChannel`. The dimensions: profile tools, builtin_tools and context files; thinking and model/provider pins on both the profile and the parent; the parent's tools and context files; 5 permission modes; the batch floor; 4 cwd kinds; absolute and relative sources. Argv, cwd and env are byte-identical across f3e76fbb, 883ded81 and HEAD (`cmp`, 5,599 distinct argvs).
- 5,000 combinations through `spawn`, the `/agents run` and RPC door: identical to f3e76fbb.
- Fresh receipts, 8,000 records: 883ded81 and HEAD differ only in `limits.profile_source` for relative sources (3,936 records).
- Rendered text, 883ded81 → HEAD: only the F9 guidance, the F9 cancel head and the F12 head (1525 → 1598) differ. The fresh disclosure and the fresh dialog are identical.

**Windows (not run on Windows):**
- `pyright --pythonplatform Windows` over the 27 changed .py files: 0 errors in product files. The Windows-only errors are in pre-existing POSIX test lines outside the delta.
- The new tests' symlink and hard-link cases are `skipif(not POSIX)`, and the `/repo` and `/home` fixtures are wrapped in `abspath`.
- My `ntpath` simulation ran on a 3.12 host, so the 3.13+ `isabs` rule was reasoned about, not executed. Not measured at all: Windows `st_nlink`, junctions, `realpath`.

**Docs:**
- The two guide copies are identical (`cmp`).
- Every cited test name exists: ADR-0277 135, ADR-0243 4, ADR-0276 10, CHANGELOG 17.
- The 8 `RESUME_*` bullets in ADR-0277 D11 match the code verbatim.
- Head sizes 1598, 1170 and 1525 are correct.
- The cancel head, guidance, disclosure and `Permission:` row match the code. Of 39 quoted strings, the 11 that are not literal matches are templates, "was" text, or a call shape (`/tmp/610-verify-r1/docs/quotes-report.txt`).

**Tests and gates:**
- The three #610 files, the #604 reports file and the sweep test: `328 passed`.
- `tests/agents_ext`, `tests/agents` and related: `2672 passed, 11 skipped`.
- `citations OK — 914 gated`, ruff `All checks passed!`, type gate `PASS`.