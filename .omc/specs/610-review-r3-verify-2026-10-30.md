No blocker and no major finding in round 2 (`9cdf76d0..b5b98267`). I found three minor findings and two wording nits. HEAD was b5b98267 before and after my work, and `git status --short` is empty. I edited nothing in the worktree, and mutants were restored with `git checkout --`. Probes and outputs are in `/tmp/610-verify-r2/`.

## Findings

**Blocker:** none. **Major:** none.

**m1. Nothing tests the listing's status word or its newest-first order.** This is old code and was not on G2's list.
- **Where:** `child_session.py:1687` prints `_last_status(latest.data)`. `:1300` picks `latest`, and `:1658`/`:1661` sort by its position.
- **Claimed but unpinned:** D7 (ADR-0277:331) and the guide (`agent-profiles.md:372`, "newest first, with how each last ended") both state this.
- **Mutants:** `_last_status(first.data)` and `return first, first`. Over all of `tests/agents_ext tests/agents`, each gives `2637 passed, 11 skipped` (`mut/listing-mutants-wide.txt`).
- **Effect:** a first run that was stopped and then resumed to the end would still be listed as "stopped". A child resumed recently would be ordered by its first run. This is display only and widens nothing.

**m2. Your question: a parent reopened under another NAME of its own file gets `RESUME_FORKED`.** The D6 sentence (ADR-0277:296-300) is true. I'd accept the behaviour, but the sentence the model gets is false for this case.
- **Probe:** `probes/test_g1_probe.py::test_g1_reopened_under_another_name`.
- **Four cases refused** with `Resume refused: that agent belongs to the conversation this one was forked from. Resume it there, or delegate the task anew.`:
  - first opened through the link, then through its target;
  - first through the target, then through a link made later;
  - a hard-link name `q.jsonl`;
  - `P.jsonl` for `p.jsonl` on case-insensitive APFS. No link is needed here.
- **Another spelling above the bucket resumes,** on the new spelling (`/private/tmp`, and `S/` for `s/`).
- **At 883ded81:** the same four are refused, so "as at 883ded81" holds.
- **Change from round 1:** at 9cdf76d0, target-then-link resumed (`g1-9cdf76d0.txt`); now it is refused.
- **Why acceptable:** nothing is widened. The setup needs a link to the file, a hard link, or a file name typed in another case.
- **What is off:** the model is told the conversation was "forked" and to "resume it there", and the guide's fork bullet does not cover this case. A Limits line would cover it; I would not change the code.

**m3. A parent file named `..jsonl` or `...jsonl` puts the child file in the wrong place.** This predates G1; the refusal has been there since F3.
- **Cause:** `place_children` (`child_session.py:575`) strips `.jsonl` and gets the stem `.` or `..`.
- **Where the child file lands:** for `..jsonl`, in the bucket itself (`--…--/./sub-….jsonl`); for `...jsonl`, in the sessions root (`s/sub-….jsonl`). The docstring says that cannot happen.
- **The resume:** refused with `its session file is reached through a link`, though there is no link. 883ded81 resumed both.
- **Severity:** it needs a parent file named by hand, so it is outside the threat model. Probe: `probes/test_dot_stem.py`.

**Nits (wording only):**
- **ADR-0277:1243** says "byte-identical across f3e76fbb, 883ded81 and c6e766bd …, and through /agents run and RPC over 5 000". The round-2 verify report compared those 5,000 with f3e76fbb only ("identical to f3e76fbb"; its SHA256SUMS lists only s-base and s-head).
- **ADR-0277:287** says the two derivations "disagree wherever a link sits at the parent's level". They disagree for any link at or above it. At 9cdf76d0, all 22 of my setups launched under another `--session` spelling, including a plain `/tmp` or `/var` root.

**Mutants that survive but are not findings:**
- `_same_target` ignoring `parent_session_id`: R2-code's deviation 5. `runtime.py:1113` checks the id just before the lookup at `:1118`.
- The mutant on `ResumeTarget.last_status`: it is equivalent, because only `_same_target`'s exclusion reads that field.

## Checked and correct

1. **G1, links at or above the parent's directory** (`test_g1_links_at_or_above_resume_on_the_first_runs_spelling`): 11 setups, each under an unresolved `/tmp` root and an unresolved `/var/folders` root. Result: `52 passed`.
   - The setups:
     - plain;
     - a linked sessions root;
     - a link two levels above;
     - a parent file linked into its own bucket, into another bucket, and outside any bucket;
     - a bucket linked to a bucket-shaped name and to a plain name;
     - a relative `--session`;
     - `s//./b/../b/p.jsonl`;
     - `//tmp/…`.
   - At HEAD: 22 of 22 resume, with `--session` the same string as the first run's.
   - At 9cdf76d0: 22 of 22 use a different spelling, and 6 are refused (`FORKED` or `NO_SESSION`). At 883ded81: 22 of 22 use the same spelling.
   - On 3.11.15 and 3.14.5: `53 passed` (the same 52 plus a check of which tree was imported), with the same results.
2. **Links below the parent's directory** (`test_g1_links_below_are_refused_with_nothing_spent`): 24 of 24 refused with the damage sentence.
   - Cases: four kinds of link (directory symlink, file symlink, file hard link, directory linked to a copy under another stem), at the hook and at execute, under three setups.
   - Nothing spent: no second plan, the budget unchanged, the records unchanged, no pending approval, no path in the text.
   - At 883ded81, 12 of these 24 resumed.
3. **The oracle** (`probes/test_r1_false_refusals.py`, run from a copy): `21 passed`. All three LINK cases print `the child says hello`, and the TMP_LINK resume uses the `/tmp/…` spelling.
4. **A real child** (`test_r1_real_child.py`): `1 passed`.
   - Both resumes: `session_path /tmp/610-verify-r2/runs/real-3aaf77/s/--tmp-…/p/sub-215d260f92c0.jsonl`, header `version 3`.
   - The cut bash left `sleep 30` (PID 48227, PPID 1, cwd in my run folder). I killed it; this is limit 14.
5. **Writer lock:** held through either spelling, the other spelling gets `RESUME_IN_USE` both ways (the sidecar comes from the resolved path).
6. **G2 mutants** (`mut/mutate.py`, output in `mut/run.txt`): `SUMMARY 50 / 54 caught; git status clean: True`.
   - The inherited list (two G1 patterns updated): 45 of 45 caught, including round 2's five survivors and R2-code's six additions.
   - My nine: 5 caught, 4 survived. The four are the two listing mutants (m1) and the two notes above.
7. **G4:**
   - **Pinned:** `test_the_refusals_that_said_only_why_now_say_what_next` pins the whole sentence, and the "NOT_YOURS without next" mutant is caught.
   - **Quoted:** only ADR-0277 D11 quotes it, verbatim, and all 9 `RESUME_*` bullets equal HEAD's constants.
   - **Nothing else changed:** across the delta it is the only module-level string that changed, and the set of damage details is the same.
8. **The `run` path:** 20,000 combinations through `spawn_granted` and 5,000 through `spawn`. Over 16 dimensions the dumps from f3e76fbb and HEAD are identical (`cmp`; sha256 `278e328e…` and `11fb17ee…`; 5,599 and 1,390 distinct argvs). Files are in `argv/`.
9. **Docs:**
   - **Status and review sections:** each claim in the Status paragraph maps to F2–F11 or G1/G2/G4. Every number in "Review round 1" and "Review round 2" matches the progress file and both verify reports.
   - **Live:** every fenced line and quote in `### Live` occurs in the two live reports. The drivers folder it cites exists.
   - **Limit 16:** matches `print_channel.py:792` and `tool.py:1325`.
   - **D6, D11, index:** the D6 G1 bullet, the D11 table and the index entry match the code. The ADR-0243 note matches G1.
   - **Guide:** its two copies are identical (`cmp`).
   - **Test names:** ADR-0277 cites 140 and none is missing. ADR-0243 cites 4, ADR-0276 10 and CHANGELOG 17, none missing.
   - **Citations:** `citations OK — 914 gated, none drifted.` The citation moved by hand, `runtime.py:1429`, is the publish right after the registry insert.
10. **Tests:**
    - The three #610 files: `282 passed in 4.00s`.
    - `tests/agents_ext tests/agents`: `2637 passed, 11 skipped, 4 warnings in 82.45s` (`suite-agents.txt`).
    - The docs-sync, citation-drift, sweep and #604 test files: `106 passed`.