# 0248. A gate may not convert its own failures into green

Status: Accepted (2026-09-22)
Date: 2026-09-22
Amends: ADR-0224 §5 (the citation gate — this changes what `--fix` does *after* it
reports a relocation it could not make, and adds a property the lock format always
needed; nothing about exact-only relocation, the gated scope, or the `--lock`-after-repair
workflow changes). Relates: ADR-0197 §anchor convention, unchanged.
Issue: #310.
Probes: `.omc/specs/310-measure.py` — runs against the branch base and the branch and
prints the numbers in §1 and §3. `.omc/specs/310-threshold.py` — re-derives §3's
uniqueness-versus-length measurement, with `--sweep` for the retraction in it.
`.omc/specs/310-overwrite.py` — the ten-line counterexample in §2.
`.omc/specs/310-sabotage.py` — re-derives the eleven-revert table, each revert on a fresh
`git archive HEAD` tree.

`scripts/check_citations.py --fix` relocated what it could, named what it could not,
exited non-zero — and then re-locked the ones it could not, onto whatever text happened
to be sitting at the stale line number. The next `--check` printed
`citations OK — 937 gated, none drifted.` over citations nobody had repaired.

## 1. The measurement

Reproduced at `fad2e28`, which was `main` and this branch's base when the defect was
found, in a scratch copy of the tree: six lines inserted into `harness/core.py` above a
cited comment block, and one line *inside* that block edited so exact relocation cannot
find it. **Every figure in this section is a `fad2e28` figure**, including the line
numbers in the second table; the branch has since been restacked onto `96f93c0c` and they
have all moved. Re-running the probe there gives 77 drifted and 65 relocated against the
same 12 stuck and the same 6 keys, green at `950 gated` — the totals scale with the tree,
the defect does not.

| | |
| --- | --- |
| citations drifted | **71** |
| relocated by `--fix` | **59** |
| reported as "could NOT be relocated automatically" | **12** |
| of those 12, re-locked anyway onto the text now at the stale line | **12** |
| distinct lock keys rewritten over a stale range (the 12 share 6) | **6** |
| exit code of `--fix` | **1** (it did say so) |
| exit code of `--check` immediately afterwards | **0** — `citations OK — 937 gated, none drifted.` |

What the tool wrote into the lock for the six distinct stuck keys, against what `sed -n`
shows at those lines:

| anchor | re-locked as |
| --- | --- |
| `core.py:1222-1224` | three lines of the unrelated comment that moved into its place |
| `core.py:1842` | the middle of a comment about `KEEP_RECENT_TOKENS` |
| `core.py:2008` | a docstring line reading "Mirrors the listener loop in `_run`'s `emit` closure" |
| `core.py:2338` | `await self._hooks.emit(` |
| `core.py:4702` | `content=[],` |
| `core.py:4745` | `)` |

The same shape was hit for real in the #301 lane, which is where the issue came from.

**The exit code was never the problem.** `--fix` already returned 1 and already printed
every stuck citation by name. The damage is that it *also* destroyed the evidence, so the
next run of the gate — the thing CI actually asserts on — could not tell a repaired tree
from an abandoned one.

## 2. The decision

**`--fix` keeps the old anchor for every citation it failed to relocate.** The lock
continues to hold the text the citation was written against, `--check` continues to
report it as drifted, and the gate stays red until a human re-derives the number. A
relocation `--fix` declined to make is not an outcome it may record.

**`--lock` may still overwrite such an anchor, and now says so.** Refusing there would
strand the one case nothing else covers: a cited block edited *where it stands*, whose
citation is still correct and whose anchor is merely stale. `--lock` remains the escape
hatch — it is an explicit human act, not the automatic path — but it now prints every
anchor it replaced, with the old text, so the act is a decision rather than an accident.

That asymmetry is the whole of it: the automatic verb may not overwrite a failure, the
manual verb may, out loud.

**And `--fix` may not claim an in-place edit it cannot see.** `cmd_lock`'s overwrite
report justified its wording — *"anchors whose block was edited in place"* — with an
invariant that is false: *"under `--fix` this list is empty by construction"*. Ten lines
break it (`.omc/specs/310-overwrite.py`): two citations, both blocks pushed down, the
first relocating **onto the exact range the second used to hold**. The key changes hands
with nothing edited anywhere, the lock `--fix` writes is correct, and the report called it
an in-place edit and told the reader to go find "a bug in `keep`" that is not there. It
did not fire on the real tree, so it was latent rather than live — but it is this ADR's
own thesis one level down, in the diagnostic instead of the data. The report now belongs
to `--lock` alone, and states only what it knows: *this range was pinned to other text.*
Under `--fix` a genuine in-place edit cannot reach the report at all, because it fails to
relocate and leaves through `keep` — which is the first decision in this section.

### 2a. The documented repair path changes with it

`--fix` could always exit non-zero; what is new is that it now leaves a **red gate** for a
human rather than a green one. Three files told an outside contributor that the repair was
one command and the diff was line numbers only — `CONTRIBUTING.md` §1, the pull-request
template, and the first-PR welcome bot — and all three are corrected here. The measurement
that forced it: the documented path (edit, `--fix`, run the gate test) on the branch base
`96f93c0c` gives `--fix` rc=1 and **1 passed**; on this branch the same path gives rc=1
and **1 failed**.
Someone following the old instructions would have hit a red CI with no documented next
step. The next step is now written down in all three, and in the failure message
`tests/test_citation_drift.py` prints when the gate goes red in CI: `--fix` names the
citations it could not place, and those want a line number read off the file by hand,
never a delta added to the old one.

The *other* half of that claim survives intact, and was measured rather than assumed: the
lock diff is **still** line numbers only. On the same edit, the base `96f93c0c`'s `--fix`
writes a **44-line** lock diff containing **6 anchors whose key kept its name while its
text was rewritten**; this branch writes **37** and **0** of them. Keeping a stuck anchor
puts nothing new in the diff — it leaves the entry exactly as it was. (Both figures move
with the tree: the same measurement at `fad2e28`, before this branch was restacked, read
39 against 32. The totals travel with the lock; the seven-line gap between the two sides
is what does not.)

## 3. An anchor that cannot name one place was never a gate

`--check` passes a citation when the text at the cited range equals the locked text. If
that text is `)`, or `try:`, or a blank line, the comparison succeeds against whatever
line the drift lands on. Measured over the 552 anchors in `citations.lock.json` **as it
stood at the branch base `96f93c0c`** — this commit repairs two of the rows below, so on
the shipped lock the first row reads 533, the last two rows are gone, and the anchor count
is unchanged at 552:

| matches in its own target file | anchors |
| --- | --- |
| 1 (gating) | 531 |
| 2–4 | 17 |
| 12 (`try:`) | 1 |
| 36 (`raise AgentHarnessError(`) | 1 |
| 181 (a blank line) | 1 |
| 344 (a blank line) | 1 |

The two blank ones were not hypothetical. `session/read_only.py` cited
`runtime/agent_session_runtime.py:266` for the `AgentSessionRuntime.cwd` reach into
`_metadata`; line 266 is blank and the property is 15 lines further down. `tui/context.py`
cited `tests/agents_ext/test_spawn_consent.py:1292` for an assertion that lives at 1346,
54 lines away. Both had been green since the lock was written, and the gate had no way to
say otherwise. **Both are repaired in this commit**, by widening each citation to the
construct its sentence is about.

**The boundary is uniqueness in the target, not length or token count**, and that was
chosen by measuring both, and — since both locks hold 552 anchors — each figure has to say
which lock it came from. **At the branch base `96f93c0c`**, with 21 ambiguous, a
24-character threshold on the anchor's text flags 27 anchors of which **21 name exactly
one place perfectly well**, and it catches **6 of the 21** that do not. **On the shipped
lock**, with 19, the same threshold flags 25, the same 21 are innocent, and it catches
**4 of the 19**.
Either way length is wrong about 21 innocents to reach a handful of the guilty; it is not a
weaker version of the right question, it is a different one. `Tree.relocate` already
answers the right one — *can this text name one place?* — for 0.6 s over the whole lock.

> **Amended 2026-09-23, before this ADR was ever pushed.** The paragraph above first
> claimed "flags 26 anchors of which 19 name exactly one place, and still misses 7 of the
> 20". That was wrong twice. It is self-contradictory — 26 flagged minus 19 unique is 7
> *caught*, hence 13 missed, not 7 — and it matches no reading of "length": a sweep of ten
> metrics × thresholds 4..64 × both operators × both locks (2440 combinations,
> `.omc/specs/310-threshold.py`) produces `(26, 19, 7)` nowhere, and `(26, 19)` nowhere
> either. The decision survives the correction and the real numbers argue for it harder,
> which is the only reason this is an amendment rather than a reversal. It is recorded
> here because a commit whose thesis is *a tool may not assert something untrue about its
> own run* may not rest on a number nobody could re-derive. Every other figure in this
> section was re-measured and stood: the anchor count, the distribution table, the
> ambiguous count before and after, and the 0.6 s.

> **Re-measured 2026-09-23, after this branch was restacked**, from `fad2e28` onto
> `96f93c0c` — #311, #194 and #309 landed underneath it. That moved the lock: 542 anchors
> became 552, and with them every total in this section. The figures above are the
> post-restack ones. What did *not* move is the argument: the repair still takes the
> ambiguous count down by exactly two, both locks still hold the same anchor count so a
> figure that does not name its lock still names nothing, the 21 false alarms are still
> 21, and the sweep still finds the retracted `(26, 19, 7)` nowhere in either lock. For
> the record, the same rows read 542 anchors / 522 unique / 16 in the 2–4 band and
> 20 ambiguous before, 18 after, while the branch sat on `fad2e28`. This is the second
> time a figure in this section has had to be corrected, and the lesson is the same one
> the section is about: **a number that does not name the tree it was taken from is not a
> measurement**, and a rebase is enough to make it false.

**Blank- and punctuation-only anchors are refused outright** (`is_trivial_anchor`): they
assert nothing, there were exactly two, and both are now gone. **The merely ambiguous 19
are counted, not refused.** Refusing them would fail 19 live citations at once, in files
other lanes are editing, and the repair for each is a judgement about
what its sentence means — the same finding as ADR-0224 §2, which is why this is a ratchet
and not a repair. The count is pinned as a literal in `tests/test_citation_drift.py`, not
in the lock's `stats`, because the tool regenerates the lock and a pin inside it would let
any `--fix` raise its own ceiling in silence.

## 4. What this does not change

- **Relocation is still exact-only** (ADR-0224 §5). Nothing here makes the tool guess more;
  it makes it forget less.
- **The gated scope is untouched** — `packages/**`, `tests/**`, `docs/guides/**`, root
  `*.md`, and ADRs and `.omc/specs/` still allowed to age.
- **The lock still cannot certify correctness, only stability.** A citation that was wrong
  on the day it was written stays wrong and stays green, exactly as ADR-0224 §7 says —
  except in the one sub-case this ADR closes, where the anchor was so generic that the
  gate could not even offer stability.
- **The lock churned by four entries and nothing else**: two blank anchors out, two
  repaired anchors in, plus two new `stats` keys — 10 insertions and 6 deletions against
  `96f93c0c`, every other one of the 552 anchors byte-identical. Deliberate: this file is
  on every lane's rebase path, and this branch has already been dragged across one.

## 5. What is NOT closed

- **19 citations still rest on an anchor their own file repeats.** They are recorded and
  frozen, not fixed. Working the set down means widening each citation by hand; the list
  is `scripts/check_citations.py --report`. The count is a property of the tree, not of
  this change: a rebase that brings new citations in can raise it, and did — it was 18
  when this branch sat on `fad2e28`, and the restack onto `96f93c0c` added one.
- **`--lock` can still enshrine a wrong number**, now loudly. The loudness is a report, not
  a gate; nothing forces a human to read it.
- **The overwrite report shows one line of each anchor it replaces.** It prints
  `previous[k][0]`, truncated at 90 characters, so for a multi-line block edited in place a
  reader sees the first line and has to take the rest on trust. That is the same reader the
  report exists to turn into a decision-maker, so it is a real limit on it — small enough to
  leave for its own change rather than widen this one.
- **A stuck anchor blocks its whole key.** If a citation `--fix` failed to relocate shares
  its exact `path:start-end` with one that relocated *into* that range, the kept text wins
  and the arriving citation reads as drifted. Theoretical — not observed in this tree —
  and red is the safe direction, so it is recorded rather than handled.
- **Nothing checks the prose.** Unchanged from ADR-0224 §7.
