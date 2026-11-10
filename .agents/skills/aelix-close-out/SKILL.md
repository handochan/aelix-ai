---
name: aelix-close-out
description: Use before closing an aelix-ai issue, marking a lane done, or writing the final commit message, CHANGELOG line, ADR text or PR body for a change. A checklist of what has to move with the code (a new, amended or superseded ADR, the ADR index row, CHANGELOG [Unreleased], both copies of an edited guide, SLICE-STATUS.md, the board, follow-up issues), the rule that every claim sentence names its measurement and the condition it holds under, how to quote decisions, and how to draft and fact-check follow-up issues and comments before they are posted.
---

# aelix-close-out

## When to use

Use this when the code works and you are about to call the work finished: before the
final commit message, before a PR leaves draft, and before an issue is closed.
Repeatedly in this project, the code moved and the docs stayed behind. An issue is
not done while an ADR, a guide or the board still describes the old behaviour.

## Procedure: the checklist

Go through every row. Write "n/a" with a reason rather than skipping one.

1. **Decision record.** Did the change make, change or reverse a design decision?
   - A new decision gets a new ADR, `docs/decisions/NNNN-<slug>.md`, with a status
     value from `docs/00-conventions.md`. Recent ADRs show the shape: a title,
     `Status:`, `Date:`, `Issue:`, the context, the decision, the rejected options and
     the consequences. Take the next free number, and check that another open branch
     has not reserved it.
   - A change to an existing decision gets a dated note in that ADR (a
     `## Note (YYYY-MM-DD, #n): <what changed>` section) and an `Amended YYYY-MM-DD
     (#n): ...` clause on its `Status:` line. Recent ADRs show the shape.
   - A reversed decision gets a new ADR, and the old one gets `Superseded by:`
     (`docs/00-conventions.md`).
   - Pi: say what pi does for the same surface, at which pi commit, and whether this
     diverges on purpose (ADR-0235: parity is not a goal, but the divergence is
     stated).
2. **ADR index row.** `docs/decisions/README.md` has one entry per ADR, newest first.
   The row summarises the decision, and an amendment adds an `Amended ...` clause to
   the amended ADR's row. Rows run long. Edit the clause you mean and leave the rest.
3. **CHANGELOG.** Add an entry under `[Unreleased]`, in the section that fits
   (`Added`, `Changed`, `Fixed`, `Security`). Each entry opens with a bold summary
   that ends with its references, `**<summary> (#n, ADR-NNNN).**`. Write what a user
   notices, and under which condition.
4. **Guides.** If you edited `docs/guides/`, run
   `uv run python scripts/sync_bundled_docs.py` so the wheel's copy matches
   (`tests/test_docs_bundle_sync.py`).
5. **Other status documents.** `SLICE-STATUS.md` is the Windows slice's record:
   update it when Windows behaviour or its known gaps change. If the change
   contradicts any other doc, find it with
   `git grep -n "<old behaviour words>" -- docs README.md CONTRIBUTING.md`.
6. **Claims pass** (see the decision rules). Reread every sentence you added to the
   ADR, the index row, the CHANGELOG, the guides, the commit subject and the PR body.
7. **Board.** Move the issue's item. Look the ids up each time, and never hard-code
   them. See `references/board-and-follow-ups.md`.
8. **Follow-up issues.** Draft what the narrowed scope leaves open, fact-check the
   drafts independently, and post only the checked text.
   `references/board-and-follow-ups.md` has the procedure.
9. **Gates.** Docs edits move citations too. Run the aelix-preflight skill again
   after the last doc edit.

## Decision rules

- **Every claim names its measurement and its condition.** "A stop ends the command"
  is wrong if you measured it only while the command's shell was alive. Write "ends
  the command while its shell runs (`test_x`, macOS and Linux)". Name the test, the
  live run or the ADR behind each user-facing sentence. Do not write wider than you
  measured. Where you did not measure, say so.
- **After a correction, look for siblings.** `git grep` the same claim in the other
  docs, docstrings and the commit message. A fix in one place and the old sentence in
  another is a contradiction.
- **Decisions are quoted, never inferred.** A decision is attributed to the
  maintainer or the owner only by quoting a real message from them, with its date.
  If you made a call yourself, write it as yours ("the lane's call", "the
  implementer chose ..."). Briefs get copied into ADRs, so a decision invented in a
  brief becomes a false record.
- **Counting "N of M documented":** read the defining symbol (with the AST, or a
  regex using `re.S`). Check both the key and the label spelling in the docs. A
  comment next to the symbol is not a source. Two such comments in this tree have
  miscounted their own lists.
- **Close an issue only when the "Done when" in its body holds,** each line measured.
  If something narrowed, say what in the closing comment and link the follow-up.

## What bit this repo

- #691, 2026-11-06/07: both delta verifies came back NOT READY on doc sentences
  alone. The code held. Round 1 wrote "a stop ends `(cmd &)`", which holds only while
  the shell that ran it is alive. Round 2's correction overcorrected the other way.
- #698, 2026-11-06: a brief said "the owner accepts this as a limit". It was the
  lead's own judgement, and lanes copy brief wording into ADRs.
- 2026-09-12, sizing epic #264: grep counts of documented surfaces were wrong on
  three of five surfaces (label against key, substring hits, multi-line
  definitions). The numbers had already gone into issue bodies.
- B3 follow-ups, 2026-10-21: 39 new issue drafts were fact-checked, but the 12
  comments on existing issues were not. Two had real errors and five had citation
  slips, and seven correction comments followed.
- #604 follow-ups, 2026-10-28: an independent check found that 5 of 6 drafts needed
  corrections, including a comment listing as "new" defects that were already open
  issues.
- 2026-09-20: pi issues closed `NOT_PLANNED` read like a decision against. They had
  been closed by pi's bot, which closes every new contributor's issue, and one held
  the best design on offer.

## Pointers

- `docs/00-conventions.md`: document and ADR format, the status values,
  `Superseded by:`.
- `docs/decisions/README.md`: the index, and the shape of a row.
- `CHANGELOG.md`, `[Unreleased]`: recent entries show the voice.
- `references/board-and-follow-ups.md`: board moves by lookup, and the follow-up
  procedure.
- The aelix-land skill: what happens after close-out (rebase, CI, merge).
