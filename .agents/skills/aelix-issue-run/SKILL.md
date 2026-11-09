---
name: aelix-issue-run
description: Use when a maintainer or a lead agent takes one or more aelix-ai board issues end to end with several agents or sessions in parallel. Covers the lane lifecycle (scout, group entangled issues into one lane, claim with an issue lock and the board, a worktree with its own venv, implement with checkpoints, an independent verify plus a cross-review, one full suite, at most two fix rounds, fold, hand to landing), the shared lane-rules.md that briefs point to, a lane-brief template, and Claude Code workflow specifics (model caps, null results, resume, waits).
---

# aelix-issue-run

## When to use

Use this when you run issues as **lanes**: one or more agents implementing in their own
worktrees while others verify, often with several sessions on one machine. A
contributor fixing one issue by hand needs the aelix-preflight, aelix-tests-that-bite
and aelix-close-out skills, not this one. Everything here exists because a lane lost
work, measured the wrong tree, or collided with another session.

A session is not a unit of work. Run as many lanes in one session as the file map
allows. The unit is the lane: one group of entangled issues is one worktree, one
branch and one PR (decided 2026-11-09, ADR-0303).

## The lifecycle

1. **Re-check the baseline.** Before trusting any handoff or plan, follow "Resuming"
   in the aelix-handoff skill: the `origin/main` SHA, the active locks, the worktrees,
   live background runs. `main` moves while you work.
2. **Scout.** For each issue: read the body and every comment
   (`gh issue view <n> --comments`), read the code paths end to end, and list the
   files it will touch. Read pi's code and issues for the same surface first (see
   `references/pi.md`). The issue body is often a full design. Treat it as the spec,
   but reread the code, because its line numbers drift.
3. **Group.** Issues that share files, a fix or a surface go into ONE lane, never into
   parallel lanes. Parallel lanes that share a file cost a rebase and pollute each
   other's measurements. Commit per issue when the changes separate cleanly. When they
   are entangled, make one commit that closes them all (`Fixes #a #b`), and say why in
   the PR. Do not split entangled work to get per-issue commits.
4. **Claim.** For each issue, write an issue lock (`references/issue-locks.md`) and move
   its board item to In progress (aelix-close-out, `references/board-and-follow-ups.md`).
   If another session's lock is active on the issue, or an outside contributor has
   claimed it, stop and ask. Reserve ADR numbers for the batch, so two lanes never take
   the same one.
5. **Make the worktree.**
   ```bash
   git fetch -q origin
   git worktree add <dir> -b <type>/<n>-<slug> origin/main
   (cd <dir> && uv sync --frozen --all-packages)
   (cd <dir> && env -u VIRTUAL_ENV uv run --frozen python -c "import aelix_ai; print(aelix_ai.__file__)")
   ```
   The last line must print a path inside `<dir>`. A worktree has no untracked files:
   no `AGENTS.md`, no `CLAUDE.md`, no `.env`. Give agents absolute paths for prose they
   must read from the main checkout. Gate scripts run by their relative path inside the
   worktree, because a main-checkout script path gates the main checkout.
6. **Brief and implement.** Fill `templates/lane-brief.md`. It points to
   `lane-rules.md` and does not copy it. Give each lane its own scratch directory
   (`/tmp/<n>-<role>/`) and its own evidence directory. The implementer commits a
   `wip(<area>): #<n> step N - <what>` checkpoint after each green step, and does not
   amend, rebase or push. Ask the implementer for an **entry-point sweep**: every path
   that reaches the same decision (other modes, RPC handlers, adapters, restored
   sessions, settings defaults), each marked guarded or not, with evidence.
7. **Persist every hand-back** as soon as it arrives, under the repository's durable
   notes directory (aelix-handoff skill). `/tmp` is not durable.
8. **Round 1: verify, cross-review and one full suite, in parallel.**
   - **Independent verify.** A fresh context gets the issue, the diff, the base and a
     base worktree with its own venv, and never the implementer's report or
     conclusions. Use the `aelix-verifier` agent (Claude Code), or brief any fresh
     reviewer with `templates/verify-brief.md`. Its verdict is READY or NOT READY.
   - **Cross-review by a different model**, for hard or risky changes
     (`references/cross-review.md`). Ask for inputs and interleavings, not opinions.
   - **The full suite, once,** run by the lead on the lane's head:
     `uv run pytest -p no:cacheprovider -q`. Never run two suites on one machine at
     once.
9. **Fix rounds: two at most.** Each fix round gets a brief that lists the findings by
   id, each with its reproducer. The round's verify checks only the delta: run
   `git range-diff <old-base>..<old-head> <base>..<new-head>`, the tests the delta
   touches, and the fast gates. The cross-review runs in round 1 only. If round 2's
   verify still finds peripheral edges, **narrow and finish** without a third round:
   fix what is cheap, record the rest as known limits in the ADR, and file follow-ups.
   If round 2 shows the core fix itself is broken, stop and ask the maintainer.
   (Working rule since 2026-10-21, after a 30-day measurement put fix and verify
   rounds at 63% of lane spend.)
10. **Fold.** Rebuild the checkpoints into the final commits (per issue or per group)
    by the procedure in `references/fold.md`. Then check that the folded tree equals the
    verified tree (`git diff <verified-head> <folded-head>` is empty), or verify the
    difference.
11. **Close out and land.** Run the aelix-close-out checklist, then the aelix-land skill.
    Update each lock's `phase` as it goes, and set `state` to `done` at merge.

## Decision rules

- **A lane needs something only you can do** (a public write such as filing an
  issue, an owner decision): do it yourself, and put the result into the next lane's
  brief or the fix list. Do not message a running lane.
- **A decision needs the maintainer:** stop that lane at that point, and ask with
  the evidence. Never write "the owner decided" into a brief unless you are quoting a
  real message. Briefs are copied into ADRs.
- **Two lanes would touch one file:** merge them into one lane, or run them one after
  the other. Never run them in parallel.
- **A lane reports a race you cannot reproduce:** reproduce it with the lane's own
  trigger before you drop it (aelix-tests-that-bite).
- **The surface is new to aelix:** read pi first. Diverging is allowed (ADR-0235),
  but decide it knowingly and say why.
- **Loops of mutating `gh` calls:** drive them from Python with argument lists, and
  list what was actually created afterwards. zsh does not split words, and macOS bash
  3.2 has no associative arrays.

## Claude Code specifics

These apply when the lanes are Claude Code subagents or Workflow agents.
`references/claude-code.md` has the detail and the recovery procedures;
`templates/workflow.js` is a skeleton.

- **The final message is the return value.** A lane that ends its turn to "wait" kills
  every background process it started, and nothing can wake it. Every brief says so.
- **No single wait over about 4 minutes, and no Monitor.** Lane calls cache for 5
  minutes, so a longer idle gap rewrites the whole context. Monitor notices have been
  lost, and lanes then sat idle for hours. Poll with bounded loops instead.
- **Never SendMessage a workflow lane, running or returned.** It spawns a second
  writer from the transcript, in the same worktree. TaskStop reaches only that copy.
- **Name the model on every `agent()` call**, and treat `null` as a failure: a 529
  overload, a session limit or a StructuredOutput parse failure. On a session limit,
  stop and resume with `resumeFromRunId` after the reset. Do not retry.
- **Keep structured returns small.** Use plain strings with newlines escaped, each
  under its limit, and put long evidence in a file cited by path.
- **The scratchpad is per session, not per agent.** Give every lane its own scratch
  directory.

## What bit this repo

- 2026-11-04, impl-672-673 (Claude Code): a lane ended its turn "waiting on the full suite". The
  suite died at 5% and the report was lost.
- 2026-11-03/04 (Claude Code): two lanes waited on a Monitor whose notice never came, and sat idle
  for 6 and 10.5 hours.
- 2026-09-06 and 2026-11-06 (Claude Code): a SendMessage to a finished lane, and later to a running
  one, started a second writer in the same worktree.
- 2026-09-03/04 (Claude Code Workflow): the model API returned 529 for about 2 hours, and `parallel()` kept the 13
  nulls. The script died at the merge step.
- 2026-10-06, #363 (Claude Code Workflow): a finished lane's 7.9 KB structured return failed to parse 5
  times, and its review stage never ran. The commit was intact.
- 2026-09-22 (Claude Code): two parallel lanes wrote `msg.txt` in one shared scratchpad, and one
  overwrote the other.
- 2026-09-20 and 2026-09-30: Codex cross-reviews found real defects after
  implement, review, fix and verify had all passed, in #299, #301 and #344.
- 2026-10-21: a 30-day measurement found that 45% of fix rounds were round 3 or later,
  at 1.5 to 2.5 hours each. That led to the two-round cap.

## Pointers

- `lane-rules.md` (this skill): the rules every lane brief points to.
- `templates/lane-brief.md`, `templates/verify-brief.md`, `templates/workflow.js`.
- `references/issue-locks.md`, `references/cross-review.md`, `references/fold.md`,
  `references/pi.md`, `references/claude-code.md`.
- `.claude/agents/aelix-verifier.md`: the independent verifier (Claude Code).
- The aelix-handoff, aelix-close-out and aelix-land skills: the stages around this one.
