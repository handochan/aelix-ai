# 0224. Line-number citations are pinned by their text, and could not be repaired mechanically

Status: Accepted (2026-08-16).
Date: 2026-08-16
Relates: ADR-0218 (#101 — the batch that broke 9 of the 65 citations it touched, and the
guides that ship in the wheel carrying two of them), ADR-0220 §8 (which recorded ~19 more
as an open, "real, separable job" — this is that job, and the number was wrong by a factor
of 25), ADR-0197 (the anchor convention `aelix_status/snapshot.py` states, amended in §6).
GitHub: #171 (the measurement), #101, #120.

**Renumbered 0221 → 0223 → 0224.** Twice, in one afternoon. A concurrent session landed
`0221-reasoning-streams-to-a-live-window-…` while this was being written; 0222 went to
`aelix status` from the same batch as this one; and then that same concurrent session moved
its own ADR from 0222 to **0223** — the number this file had already taken — for the same
reason. Convention as in ADR-0220's own renumber: whichever has not shipped moves, because
moving it touches nobody else's work.

Three collisions in three days is not bad luck. The number is claimed by writing a filename
and is not reserved anywhere, so any two sessions writing an ADR the same day collide by
construction. Worth fixing at the convention level; not fixed here.

## 1. The measurement that started it

This repo explains itself by citing its own source with line numbers —
`harness/core.py:572-576` — in docstrings, test module headers, and the guides bundled into
the wheel. Nobody had counted them. Counted, over the **live** sites only:

| | | how it was measured |
| --- | --- | --- |
| Gated citation sites | **793** | the scanner, over live sites only |
| No longer hold the text they were written against | **531 (67%)** | `git blame` → target file at that commit |
| Wrong — a reader lands somewhere the sentence is not about | **551 (69%)** | reading each sentence |

The second and third rows are different questions and the gap between them is the
finding. A citation can still hold its original anchor and be wrong (it was wrong when
written), or have lost it and be fine (the block moved and the number was already
approximate). Only the third row is what a reader experiences, and only reading the
sentence answers it.

Three incidents had already hit this and been treated as one-offs: #101 broke 9 of the 65
it touched; the #120/#167/#161 batch displaced five constructs and broke ~19 more. The
other ~520 predate both.

The final row is not an estimate. Every one of the 793 was read against the sentence
carrying it: **242 were already correct, 539 needed a new line range, 12 needed the prose
changed** because the construct is gone. And **189 of them — 24% — did not match their own
sentence on the day they were written**, which is the number §2 is about.

## 2. The measurement that changed the plan: it cannot be repaired mechanically

The obvious repair is mechanical, and it was built and run. `git blame` finds the commit
that wrote each citation; the target file *at that commit* gives the exact text at the
cited lines; that text is located in today's file; the number is rewritten. 455 relocations
applied cleanly, every one of them an exact block match.

An adversarial audit then prose-checked **247** of those 455 against the sentences carrying
them. **98 were wrong — 40%.**

The dominant cause is not the machinery. It is that **the citation did not point at what
its sentence said even on the day it was written** — 189 of 793, measured directly in the
full pass afterwards:

- `tests/cli/test_prompt_file_flags.py:12` — *"`_apply_prompt_files` runs at
  `cli/entry.py:1799`"*. 1799 is a lazy-import comment; the call is at 1842. Wrong at the
  blame commit too.
- `tests/builtin/test_permission_child_floor.py:141` — *"Branch (a) (`permission.py:404-405`)
  returns before branch (d)"*. 404-405 is inside a different function.
- `aelix_agents/tool.py:110`, `:187`, `:425` — one wrong pointer, copied three times.

So a faithful relocation is not a repair. It re-encodes the same error rate at new
coordinates, and it does so under a green gate, which is worse than leaving it alone.

**The decision that follows: the prose is the ground truth and the anchor is only
evidence.** All 793 were re-derived by reading the sentence that carries each one and
finding what it names in today's tree, with the mechanical answer retained only as a hint.
Twenty agents, one per batch of forty, every site decided and none skipped.

## 3. Two of the causes were bugs in the repair tool, and both are instructive

**`str.splitlines()` numbering.** Python also breaks lines on U+2028, U+2029, form feed and
vertical tab; `sed`, `awk`, `git blame`, editors and the tracebacks a reader compares
against break only on `\n`. Five tracked files contain such a character, and one of them is
`cli/agent_context.py`, this repo's most-cited target — so every citation into it came out
exactly one line low, silently. `split_lines()` exists for this and is used everywhere the
tool counts.

**Continuation mis-attribution.** A bare `` `:189-193` `` continues the file named before
it, which may be on an *earlier* line. Taking "the last full citation on the same line"
pointed `reaper.py:278`'s continuation at `agents/resolver.py` instead of
`modes/print_mode.py` and rewrote a **correct** citation into a wrong one. A continuation
now inherits only from a full citation that *precedes* it on its own line; anything else is
left ungated rather than guessed at.

## 4. What is gated, and what is deliberately left to rot

**Gated** — `packages/**`, `tests/**`, `docs/guides/**`, root `*.md`. What a reader follows
*today*: running code, its tests, and the guides that ship to users.

**Not gated** — `docs/decisions/` and `.omc/specs/`. An ADR is a dated record of a decision
and a sprint plan is a record of a plan. Rewriting their numbers to match today's tree would
make them claim they were written about code that did not exist yet. They are allowed to
age, and a test asserts they are genuinely never scanned rather than merely never noticed.

**Also not gated** — 27 citations whose path does not resolve to exactly one tracked `.py`
file: pi's TypeScript, the mcp SDK, httpx internals, and bare stems two files share
(`stream.py`, `types.py`). Two tie-breakers recover most of the ambiguous ones — prefer a
candidate in the citing file's own directory, then in its own source module — and the rest
stay ungated. The count is pinned so that set cannot grow quietly: rename a module so two
files share a stem and thirty citations would otherwise stop being checked with nothing
printed.

## 5. The gate

`citations.lock.json` records, for every gated citation, the normalised source text at the
cited range. `scripts/check_citations.py --check` re-reads the tree and fails on drift with
the new line number in the message; `--fix` applies the relocations and rewrites the lock;
`tests/test_citation_drift.py` runs `--check` in CI.

Pinning the *text* rather than the number is what makes the discipline survive a busy
branch: a refactor that moves 200 lines costs one `--fix`, not 200 re-derivations.

**Relocation is exact-only, and that was a correction.** The first relocator fell back to
the block's most distinctive surviving line minus its offset. Measured across all 479
drifted citations, that put `agents/resolver.py:183-206` on a bare `profile: AgentProfile,`
parameter line and `harness/loop.py:396-405` on an unrelated `isinstance` comment — the
fallback assumes the lines *above* the surviving one survived too, which is exactly what a
rewrite breaks. A block that was edited rather than moved is now reported for a human, with
the near-match printed as an advisory hint that is never applied.

**The lock cannot certify correctness, only stability.** `--lock` freezes whatever is there.
It is generated once, after the repair, and the repair is what carries the correctness
claim.

## 6. ADR-0197's anchor convention is amended, not overruled

`aelix_status/snapshot.py` states the convention this practice grew up under:

> every `file:line` below was re-read against this tree. They are the EVIDENCE for a
> decision, not a maintenance contract — `cli/entry.py` in particular moves under every
> concurrent track, so nothing there is cited by line at all.

Both halves stand, and the measurement sharpens the first one. A citation *is* evidence for
a decision, its author is not signing up to maintain it, and that is exactly why 490 rotted
— and why a third of them were wrong before anything moved at all. What changes is who
maintains it: the tool does, so "not a maintenance contract" stops meaning "may quietly
become false". The second half — that a file churning under concurrent tracks should not be
cited by line at all — remains the better answer where it applies.

## 7. What is NOT closed

- **The convention itself is an open question.** A citation that named the construct —
  `cli/entry.py::_apply_prompt_files` — would be checkable forever and immune to line
  drift. Rewriting 793 sites is comparable work to the repair, and it would end the class
  rather than gate it. Recorded in #171; not decided.
- **`--check` cannot see a citation that was wrong when written.** Nothing mechanical can.
  That is the whole finding in §2, and the gate inherits it: it guarantees stability from
  the moment of locking, not correctness before it.
- **Prose is not checked.** A citation can point at the right lines while the sentence
  around it describes something else.
- **ADRs, `.omc/specs/` and `docs/` outside `guides/` remain stale**, on purpose (§4).
  Anyone reading an ADR's line numbers against today's tree will still be misled; the ADR's
  date is the only warning.
- **Deleted-then-recreated files.** A target removed and re-added under the same path
  relocates against the new content with no signal that it is a different file.
