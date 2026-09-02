# Handoff — #170 / #169 reasoning streaming (branch `feat/stream-thinking`)

Worktree: `/workspaces/aelix-think`. Branch: `feat/stream-thinking`, rebased onto
`origin/main` (`3ae68f0`).

## Commits

| sha | what |
|---|---|
| `ff6b306` | feat: reasoning streams to the live tail instead of landing all at once (#170, #169) |
| `f220515` | fix: the reasoning window is acquired but never released (the review round) |
| `0e16dc5` | docs: ADR-0221 records the live-window contract |

## Gates

Suite **8749 passed / 1 skipped** (8729 → +20 new tests), type gate **259 files 0 errors**,
ruff clean. Post-rebase re-run confirmed before merge.

## What the review round fixed

A five-lens adversarial review of `5834acd` returned 9 findings; all 9 survived independent
verification. Four were **the same shape**: `_push_thinking_tail` acquires the live window and
nothing releases it.

1. **HIGH — orphan tail.** `turn_end(aborted)`, `message_end(error)`, the stream `error` arm and
   `finalize()` all left reasoning pinned above the prompt for the rest of the session. Abort
   emits ONLY `turn_end`, and `_finalize_text` is a no-op when no TEXT stream is open — exactly
   the abort-during-reasoning case. Fixed with `_close_thinking()` on every terminal path.
2. **MEDIUM — Ctrl+T stranded it.** The release was gated on the CURRENT `hide_thinking`, but
   the tail was painted under the old value. Now gated on `_thinking_tail_shown`, and
   `hide_thinking` is a property that releases on the ON transition.
3. **MEDIUM — reasoning clobbered the answer window.** Same last-writer-wins sink; the answer
   now owns it while it streams, and a reopened `content_index` un-retires itself.
4. **HIGH — quadratic.** Every frame re-rendered the whole accumulator: **226ms per frame at
   150KB**, ~98% of rendered lines discarded, on the prompt-toolkit loop. Fixed with a
   boundary-resynchronised slice (**226ms → 5.9ms**) plus `StreamRenderer`'s adaptive governor.
5. Three coverage gaps: `plain_lines` had no tests, the throttle test pinned no rate, nothing
   crossed a message boundary.

## Landmines found here (worth carrying forward)

- 🔴 **A live WINDOW cannot be gated by a sink assertion.** The failure is the ABSENCE of a
  later write. Every one of these four survived a green suite for that reason. Windows need a
  test that reads the painted frame back.
- 🔴 **My own fix invalidated my own test.** The reopen-discard (fix 3) masks the per-message
  `_thinking_done` reset, so the test written to pin that reset passed with the reset deleted
  (full suite 8749 green). The reset is load-bearing only for a block delivered as
  `thinking_end` ALONE. Caught by the sabotage battery, not by review.
- 🔴 **A sabotage that breaks SYNTAX is not a sabotage.** `sed`-deleting the discard line left
  an `if` with only comments → IndentationError → the run reported "no summary", which the
  harness first read as MISSED. Always `ast.parse` the sabotaged file.
- 🔴 **Ctrl+C is the abort key, not Esc** (`chrome.py:892`). The first live probe sent `\x1b`
  and every turn completed; I nearly reported "abort is clean" from runs where no abort ever
  happened.
- 🔴 **The banner already contains dim-italic SGR.** "Wait for the first `\x1b[2;3m`, then
  abort" fired before the turn was running. Scope marker detection to bytes after submission.
- 🔴 **Instrument live probes to report vacuity.** Several real-model runs aborted before any
  reasoning existed; the screen was clean on BOTH builds and looked like a pass. The harness
  now prints `reasoning repaints BEFORE the Ctrl+C` and labels the run VACUOUS.
- `ruff check --select I001 --fix` is the right scope for import order — do not run
  `ruff format` on directories (the repo is not format-clean; CI runs `ruff check` only).

## Verification artefacts (scratchpad)

- `probe_fixes.py` — 33 checks; 33/33 on HEAD, 21/33 on `5834acd` (12 falsifiable).
- `sabotage.sh` — 11 sabotages, one per fix; all caught by the test that names the defect.
- `live_pty.py` — drives the real TUI over a pty against a real model; `AELIX_WT` selects the
  build, arg 2 = seconds from submit to Ctrl+C (`<0` = let it finish).
- `before5834/` — detached copy of `5834acd` for A/B.

## Not verified (stated, not glossed)

An abort landing inside the **reasoning-before-answer** window against a real model. That
window is under a second on openrouter/gpt-5.5; runs that reached it had already begun the
answer. Covered deterministically by `tests/tui/test_thinking_stream_live.py`, which fails on
the parent commit with `assert not True`.

## Remaining

- **#163** still open, awaiting the owner's decision. `tui/images.py` has zero production
  callers but is advertised in README + extras. My recommendation: remove. The owner asked
  whether Ctrl+V image paste would be affected — it would not: Ctrl+V paste is already wired
  at `shell.py:2267` and is INPUT-side, while #163 is OUTPUT-side rendering.
- **#168** (`toolResult` loses `details`) open by design.
