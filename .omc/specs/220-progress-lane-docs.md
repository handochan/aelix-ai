# #220 — lane report: docs

Worktree `/tmp/wt-220`, branch `fix/220-agents-process-tree`, base `main` `6586f8a`.
Spec: `.omc/specs/220-design-2026-09-05.md` revision 2 (§G, §Pi, §I, §A.9, §F).
Nothing outside this lane's six files was edited. No code, no tests, no
`git` state-changing command was run.

## Files changed

| file | what |
| --- | --- |
| `docs/decisions/0238-…-had-to-die.md` | `Status:` amendment; "What stays open" #220 bullet → landed, by function; the "Until #220 lands…" sentence struck; two Consequences bullets (what did NOT change + Q1 with evidence + the win32 success-path consequence + Q6 by reference; and §A.3(b) as a decision taken in the owner's absence) |
| `docs/decisions/README.md` | row 0238 tail → `#220 … 착지(2026-09-05) … 남은 것: #221, #222`, in the row's existing Korean style |
| `README.md` | "Platform support" bullet: verdict whole **on Windows only**; asymmetry sentence kept verbatim; closing "what's left" line now names #222 |
| `README.ko.md` | the same in Korean, mirroring the English bullet's structure |
| `SLICE-STATUS.md` | header note `:15`; Remaining §1 → LANDED (#200, `4043d1c`); §2 heading → LANDED (#220) + a "Landed (2026-09-05, #220)" amendment; the `:80` prose `(#202 / #220)` → `(#202 built it, #220 adopted it here)` |
| `CHANGELOG.md` | the EXISTING #202 entry's last sentence edited in place (no sibling entry), plus the §A.9 win32 success-path sentence and the §A.3(b) exit-code sentence |

## Every `#220` mention outside `.omc/`, and what was done to each

Grep: `grep -rn '#220\|issues/220' . | grep -v '^\./\.omc/\|^\./\.git/\|^\./\.venv/\|scratchpad'`.

| site (pre-edit) | disposition |
| --- | --- |
| `CHANGELOG.md:341` | rewritten — "not converted yet (#220)" → "converted too (#220)", in place |
| `README.md:105` | rewritten — the "print channel and the reaper are **not** converted" sentence is gone; #220 now appears as the follow-on that landed |
| `README.ko.md:103` | same, in Korean |
| `SLICE-STATUS.md:15` | rewritten — "is #220 and is still open" → "followed on 2026-09-05 as #220 … amended in place and marked landed" |
| `SLICE-STATUS.md:80` | rewritten — `(#202 / #220)` → `(#202 built it, #220 adopted it here)` |
| `SLICE-STATUS.md:90` | kept verbatim (it is inside the dated 2026-09-05 "Amended" block and is a record of what was true then); the new "Landed" block immediately after it says what happened |
| `docs/decisions/README.md:144` | row tail rewritten |
| `docs/decisions/0238…:323` | the "What stays open" bullet, rewritten as landed |
| `docs/decisions/0238…:328` | part of the same bullet; the "docstrings … name #220" clause is gone (the docstrings are the code lane's to update) |
| `docs/decisions/0238…:336` | the "Until #220 lands…" sentence struck and replaced with one that keeps the README pointer and states where the split now falls |
| `packages/…/aelix_agents/print_channel.py:973` | **NOT MINE** — code lane (spec §B.2: "adopting it here is #220" becomes past tense) |
| `packages/…/aelix_agents/reaper.py:222`, `:371` | **NOT MINE** — code lane (spec §B.1 docstring list) |

## Commands run, with output tails

```
$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/docs tests/test_citation_drift.py
FAILED tests/test_citation_drift.py::test_no_citation_has_drifted
1 failed, 29 passed in 1.15s
```
The drift failures are **not this lane's**. Every drifted anchor is in a product
or test file the other lanes are editing concurrently:
```
$ … | grep -E '^E +[A-Za-z0-9_./-]+:[0-9]+:' | sed 's/:.*//' | sort -u
packages/…/aelix_agents/{batch,envelope,print_channel,reaper,runtime,stream}.py
tests/agents_ext/{test_batch_executor,test_envelope,test_print_channel_spawn,
test_reduce_consumes_real_print_mode_output,test_stream_reduce}.py
tests/test_citation_drift.py
```
Spec §F's five-step citation procedure is the verification lane's; it is not run here.

```
$ .venv/bin/python scripts/check_citations.py --check > … 2>&1; echo rc=$?
rc=1        (102 lines; `grep -inE 'slice-status|readme|changelog'` on it → no hits)
```
Root `*.md` **is** gated (`scripts/check_citations.py:32`, and
`tests/test_citation_drift.py:132` pins `SLICE-STATUS.md` as gated), so this
matters: this lane introduced **no** new `path:line` citation into `README.md`,
`README.ko.md`, `CHANGELOG.md` or `SLICE-STATUS.md`, and it *removed* two
(`print_channel.py:970`, `rpc_channel.py:438`, in the rewritten Remaining §1).
The checker reports nothing against any of the four.

```
$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/docs \
      tests/tui/test_update_notice.py tests/packaging_gate/test_build_hygiene.py
35 passed in 6.91s
```

```
$ git status --porcelain
 M CHANGELOG.md
 M README.ko.md
 M README.md
 M SLICE-STATUS.md
 M docs/decisions/0238-…-had-to-die.md
 M docs/decisions/README.md
 (plus three product files and one new test file belonging to other lanes)
```

## The "mutation check" for a documents lane

A docs lane has no product line to delete, so the equivalent discipline is: every
factual claim written here was checked against **this** tree before it was
written, and the ones that failed the check were rewritten rather than shipped.

1. **A claim that failed and was rewritten.** The ADR bullet first said the
   original `print_channel.py:969` citation "had already rotted". Checked:
   `git show 6586f8a:…/print_channel.py | sed -n '966,972p'` → line 969 is
   `start_new_session=True,`, exactly what ADR-0238 meant. The sentence now says
   the true thing instead: nothing in CI would notice such a number rotting, and
   this change edits the very line that citation pointed at.
2. **The self-citation was re-derived, not copied.** §G asks for "Q6 as already
   answered at its own `:151-155`". Measured in the amended file:
   `grep -n 'KILL_ON_JOB_CLOSE\` fires on parent death' -A 3` → the bullet is
   `:151-154`. Written as `:151-154`. It is stable under this lane's own edits
   because the `Status:` line stayed one line (see deviations) and every other
   edit is below `:154`.
3. **The `#200` attribution was verified before SLICE-STATUS §1 was struck.**
   `git show --stat 4043d1c` is "fix(agents): the delegation spawn refused to
   start on windows, quietly / #200", and `reaper.pdeathsig_preexec()` exists at
   `reaper.py:493` with the docstring that says why. Without that check the
   item would have been struck on the spec's word alone.
4. **The Status-line precedent was opened.** `0017-….md:3` and `0039-….md:3`
   both carry parenthesised amendments in `Status:` — and both are a **single
   line**, which is why this one was collapsed back to one (deviation 1).
5. **The "ungated by design" claim in the ADR bullet was opened.**
   `scripts/check_citations.py:36-39` ("Not gated: `docs/decisions/` and
   `.omc/specs/`") and `tests/test_citation_drift.py:130-133`
   (`("docs/decisions/0220-anything.md", False)`).

## Deviations from the spec, with reasons

1. **The `Status:` amendment is one long line, not three wrapped ones.** §G gives
   the text but not the wrapping; the two precedents it names (`0017:3`,
   `0039:3`) are both single-line, and a wrapped `Status:` sits directly above
   `Date:` where a wrapped continuation reads as a second field. Content is the
   spec's, verbatim in substance.
2. **Q6's self-citation is `:151-154`, not the spec's `:151-155`.** Measured
   above. `:155` is the first line of the *next* bullet ("A cooperative exit is
   a new code path on Windows"). The instruction not to invent a range wins over
   copying the spec's.
3. **The struck "Until #220 lands…" sentence is replaced, not deleted.** §G
   quotes only the first clause as the strike target; the second clause (the
   README's "Platform support" says which half is contained) is still true and
   is the ADR's only pointer to that section, so the sentence was rewritten
   around it and now says where the split falls after #220.
4. **`SLICE-STATUS.md:90` ("… is **#220**") is left verbatim.** It sits inside
   a block explicitly dated and labelled "Amended (2026-09-05)" — a record of
   what was true when it was written. The new "Landed (2026-09-05, #220)" block
   directly below it carries the update, which is the same shape the file
   already uses for the #202 amendment.
5. **The README bullet gained one sentence the spec did not dictate** — that a
   delegated child can now be asked to stop on Windows at all, and its
   cooperative exit is distinguishable from a hard kill by its exit code. It is
   the user-visible half of §A.3(b), which §G puts in the CHANGELOG; without it
   the README's "whole on Windows" is a claim with no consequence attached. The
   exit numbers themselves are **not** in the README.

## What could not be verified here

- **143 / 149 are `[U]`.** They appear in the ADR's owner's-absence bullet and
  in the CHANGELOG as *what the code now returns*, never as measured. The
  measured half — exit **1** with two tracebacks on `main` `6586f8a` — is
  attributed to the measurement (`220-progress` §1) and is the only number in
  those sentences stated as observed. §H.6(B) (POSIX) and §H.7 (windows leg)
  are where they become measured; nothing in this lane should be read as
  claiming they already are.
- **The win32 success-path consequence (§A.9)** — "a successful delegation now
  ends every surviving job member" — is written as deliberate and as the shape
  the rpc child already ships; it is `[U]` on a Windows host from this lane's
  seat. §D.2 pins it in tests and §H.7 measures it.
- **Q1's Darwin probe** is restated from the spec's `[M]` tag (posix-lane probe:
  `descendant_pids -> []`, `root alive False grandchild alive True`, then gone
  after `hard_kill`). This lane did not re-run it. The ADR paragraph says
  "measured on the owner's box" on that authority; if the posix lane's probe
  turns out not to have been run, that sentence is the one to pull.
- **Whether the four sites actually landed.** Every "landed" sentence in these
  documents is written against the spec's §A/§B, not against the other lanes'
  finished code. The verification lane must confirm the four sites are in the
  tree before this commit is made; if any lane ships short, the ADR's "All four
  sites are converted", the README's "whole on Windows" and the CHANGELOG's
  "converted too" are the three sentences that must move together.

## For other lanes

- `print_channel.py:973` ("adopting it here is #220") and `reaper.py:222` /
  `:371` ("is #220, not this") are the only remaining future-tense `#220`
  mentions in the tree outside `.omc/`. Spec §B.1/§B.2 assign them to the code
  lane; this lane did not touch them, and §G's sweep is not complete until they
  are past tense.
- Process artefacts §G also lists and this lane does not own: the #215 comment
  (its option-c skip retired), the board move, and
  `.omc/specs/handoff-220-2026-09-05.md` in the 17-file shape.
