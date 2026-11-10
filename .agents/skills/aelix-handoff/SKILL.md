---
name: aelix-handoff
description: Use when an aelix-ai session ends, before clearing or compacting a context, at every lane hand-back, and when starting a session that picks up earlier work. Covers writing a handoff in the five-part shape (baseline, start here, then, rules that bit, disproved), persisting lane hand-backs and evidence out of /tmp, and the resume procedure that re-checks the baseline (main SHA, issue locks, worktrees, live background runs, CI) before trusting anything the handoff says.
---

# aelix-handoff

## When to use

- **Writing:** at the end of every session, before you clear or compact a context,
  and each time a lane hands work back. Update the running handoff then, not at the
  end. A handoff is not a courtesy here. When a container was lost on 2026-08-21, every
  earlier transcript went with it, and the handoffs and spec notes were what
  survived.
- **Resuming:** at the start of any session that continues earlier work, and after
  any long pause.

## Writing a handoff

1. **Where.** Keep it in the repository's durable notes directory, not in `/tmp`.
   Maintainers use `.omc/specs/handoff-<topic>-<YYYY-MM-DD>.md`. `.omc/specs/` is
   tracked: `.gitignore` ignores the rest of `.omc/` and re-includes it with
   `!/.omc/specs/`, so a handoff is committable, and a clone or a new worktree has
   every committed one. The session brief names the newest by the date in its file
   name, so keep that date. One topic per file. Update it in place during the
   session.
2. **The shape.** Copy `templates/handoff.md`. It has five required parts, plus a
   "latest state" block on top:
   - **Baseline:** the `origin/main` SHA, the last full-suite result (with its commit
     and command), and the path of the canonical plan.
   - **Start here:** the one next action. If order matters, say why.
   - **Then:** what follows, in order.
   - **Rules that bit:** what actually went wrong in this session, each with its date
     and issue.
   - **Disproved, do not believe again:** premises this session showed to be false.
     This is often the most valuable part, because it stops the next session from
     walking into the same dead end.
3. **Per lane**, record its worktree, branch, base, scratch, brief path, lock state,
   last checkpoint SHA and round, and where its transcript is. A dead session can then
   be recovered lane by lane.
4. **No placeholders left.** A handoff that still holds `__MAIN__`, `TBD` or `<sha>`
   means the closing steps never ran. Fill it in, or write "not run" and why.
5. **Every number carries its command and commit.** "23,685 passed" without the SHA it
   ran on cannot be checked later.
6. **Persist hand-backs and evidence as they arrive.** Copy each lane's final report,
   and any later addendum from the same agent, to the notes directory (for example
   `<notes>/<n>-impl-report.md`). Copy the drivers and outputs the next session will
   need to `<notes>/probes/<n>/`. Cite those durable paths in the handoff, never only a
   `/tmp` path.
7. **Decisions are quoted.** Write a maintainer decision into the handoff only as a
   quote with its date. Label your own calls as yours.

## Resuming from a handoff

Re-check the baseline before you trust any line of it. Several sessions run against
one clone, and `main` has moved twice during a single session.

1. **Read the session-start brief.** In Claude Code the SessionStart hook
   (`scripts/session_brief.py`) prints the branch, HEAD against `origin/main`, the
   worktree count, the active issue locks and the newest handoff by the date in its
   file name (in a worktree, the main checkout's handoffs count too, untracked ones
   included). It reads local refs only, so fetch before comparing:
   ```bash
   git fetch -q origin
   git log --oneline -1 origin/main                  # against the handoff's baseline
   git log --oneline <handoff baseline>..origin/main # what landed since
   ```
2. **Check the locks.** List the active locks, and compare their `phase` with what the
   handoff says. Another session may have taken the next step.
   ```bash
   grep -l '"state": "active"' "$(git rev-parse --path-format=absolute --git-common-dir)"/aelix-issue-locks/*/owner.json
   ```
3. **Check the worktrees and the machine.** Run `git worktree list` and look for
   "prunable". After a long pause, check `uptime`: a reboot wipes `/tmp`. For each lane,
   check `git -C <wt> log --oneline <base>..HEAD` and `git -C <wt> status --short`.
   Uncommitted edits are a lane's half-finished state, not noise.
4. **Check live work.** Background jobs of a cleared context keep running. Look for
   them by a marker unique to the run, not by name:
   `ps -axww -o pid,ppid,etime,command | grep '<marker>'`.
5. **Check CI by pinned run id.** List runs with their workflow name, and pick the CI
   run, not the newest run of any workflow:
   `gh run list --branch <b> --limit 6 --json databaseId,headSha,status,conclusion,workflowName`.
   A failing job's log can be read before the run completes with
   `gh api repos/<owner>/<repo>/actions/jobs/<job id>/logs`.
6. **Treat the handoff's numbers as claims.** Rerun what a merge depends on (the full
   suite, the gates) on the commit you will merge.
7. **Then** start with "Start here", and update the handoff as you go.

### Recovering what `/tmp` lost

macOS deletes `/tmp` files that have not been accessed for 3 days, without a reboot,
and a reboot deletes everything there. Commits survive, because a worktree's branch
and objects live in the main repository:
`git worktree prune && git worktree add <dir> <branch> && (cd <dir> && uv sync --frozen --all-packages)`.
Files that agents wrote can often be rebuilt from their transcripts: the write
tool's content, heredocs, and the edits that followed, replayed in order.

## What bit this repo

- 2026-08-21: a lost container took every transcript. The handoffs, `.omc/wiki/` and
  the specs survived.
- 2026-09-05 (#221): a context was cleared while CI ran. The next one found a handoff
  with literal `__MAIN__` and `__CI__` placeholders, two uncommitted doc edits, and the
  old context's CI watch still running.
- 2026-09-30: a handoff pointed at flake plugins and probes in `/tmp`. macOS had
  cleaned them after 3 days without a reboot. They were rebuilt from transcripts.
- 2026-10-04/05 (#367): a reboot mid-batch wiped every `/tmp` worktree and kit. The
  kits copied to the notes directory after each round survived; the last round's did
  not.
- 2026-10-30 (#610): only a verify agent's main report was persisted, not its later
  addendum. A count from the raw sub-report reached the progress record, and a fix
  lane caught the mismatch two rounds later.

## Pointers

- `templates/handoff.md` (this skill): the skeleton.
- The aelix-issue-run skill, `references/issue-locks.md`: the lock fields.
- The aelix-issue-run skill, `references/claude-code.md`: where lane transcripts are,
  and how to recover a hand-back from them.
- The aelix-land skill: CI watching and the merge steps a handoff often leaves open.
