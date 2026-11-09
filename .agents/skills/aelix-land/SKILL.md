---
name: aelix-land
description: Use when an aelix-ai branch is verified and ready to land - rebasing it onto the commit about to merge, resolving the conflicts that docs and the citation lock always produce (ADR Status lines, the ADR index row, CHANGELOG, citations.lock.json), pushing, writing the PR body, watching CI by pinned run id with the job count checked, triaging a flake, merging with --rebase --match-head-commit, updating a pushed branch without a force push, recovering from a concurrent repack, and cleaning up worktrees, locks and the board afterwards.
---

# aelix-land

## When to use

Use this once a branch's commits are final and verified (aelix-issue-run, step 10, or
your own branch after aelix-preflight). Landing went wrong here more often in the
docs and the citation lock than in the code. Even lanes whose source files never
overlap conflict at rebase time: the citation gate drags every lane into every other
lane's files as line-number-only hunks, and every issue touches the CHANGELOG.

## Procedure

1. **Re-check `main`.**
   ```bash
   git fetch -q origin && git log --oneline -3 origin/main
   ```
   If another PR is about to merge, rebase onto its pushed head instead of today's
   `main`. Your branch then fast-forwards the moment it lands, which saves a second
   rebase.
2. **Rebase in the branch's own worktree** onto that commit: `git rebase <sha>`.
   Resolve each conflict by the rules in `references/rebase-doc-conflicts.md`: ADR
   `Status:` lines, the index row (a token-level three-way merge), CHANGELOG (keep both
   entries), and the citation lock (take the upstream side, then `--fix`).
3. **Check the rebased branch.**
   - `git range-diff <old-base>..<old-head> <new-base>..HEAD` shows no change you did
     not make.
   - `git diff <new-base> HEAD` holds only this branch's work.
   - Grep the merged docs for the other branches' issue numbers. A note such as "#n
     still open" goes stale when #n lands.
   - Run `git log --format=%B <new-base>..HEAD | grep -c '^#'`. A rebase strips message
     lines that start with `#`, so compare each message with its pre-rebase text and
     restore what was lost (`git commit --amend --cleanup=verbatim -F <file>`, before
     the push).
   - Run the aelix-preflight skill on the rebased head, with `--fix-citations` once.
     If the rebase changed code under your tests, rerun the full suite.
4. **Push once, after review ends.** `git push -u origin <branch>`. From then on, do
   not amend or force-push. If `main` moves under the open PR, update the branch with
   a merge commit (`references/pushed-branch-and-external-prs.md`).
5. **Open the PR.** `gh pr create --title "<the commit subject>" --body-file <file>`.
   - **What this changes / Why**: from the issue and the commit message.
   - **How it was verified**: the block `scripts/preflight.py --claims` wrote, the
     full-suite line with its commit, and the live checks with their output. Say what
     you did not run.
   - **`Closes #n`** for every issue, one per line.
   - **Grouping**: when one commit closes several issues, say why the changes could
     not be separated.
   - **Behaviour changes that hosts or users will notice**, named so the maintainer
     can call them out.
6. **Watch CI by pinned run id.** See `references/ci-watch.md`.
   ```bash
   gh run list --branch <branch> --limit 6 --json databaseId,headSha,status,conclusion,workflowName
   gh run watch <id> --exit-status --interval 30      # in the background; never wrapped in timeout
   gh run view <id> --json status,conclusion,jobs -q '.status, .conclusion, (.jobs | length)'
   ```
   `ci.yml` has 12 jobs: lint plus test on two operating systems by four Pythons, and
   two installer end-to-end jobs for each installer. Anything but `completed` is not
   done, and fewer jobs than expected means you are reading another workflow's run.
7. **Merge.** Run `git fetch -q origin` and check `origin/main` once more, right
   before the merge. Then:
   ```bash
   gh pr merge <pr> --rebase --match-head-commit <verified head sha>
   ```
   Never `--squash` a PR that carries per-issue commits: squash folds them into one.
   A fast-forward push of the verified head (`git push origin <sha>:refs/heads/main`)
   also marks the PR merged. Run it as its own command, not in an `&&` chain.
8. **After the merge.**
   - Wait for `main`'s CI on the merged SHA before pushing anything else to `main`.
     `ci.yml`'s concurrency group is per ref with `cancel-in-progress`, so a docs push
     right after a merge cancels the code commit's run. A `cancelled` main run is not a
     failure if a later run on a tree that differs only in docs is green. Check that,
     rather than rerunning blindly.
   - Clean up. `git worktree remove <dir>` for the branch's worktree and any verify or
     base worktrees, and `git branch -d <branch>` locally. Set each issue lock to
     `state: done` with `head` and `pr`. Check that each issue is closed and its board
     item is Done (aelix-close-out). A remote branch delete may need the maintainer to
     run it.

## Decision rules

- **`--fix` prints "no anchor; locked as written" during a rebase:** stop. A line
  where `main` changed one citation and your branch another loses yours. Merge both
  numbers by hand, read the cited line, and rerun `--fix`.
- **A conflict in `citations.lock.json`:** take the upstream side whole, `git add`
  every resolved file, and only then run the gate. Unmerged paths appear three times
  in `git ls-files`, and the gate counts them three times.
- **Merging `main` into a pushed branch** (not rebasing it): one lock cannot describe
  a merged tree, because one number means different lines on the two sides.
  `references/rebase-doc-conflicts.md` has the per-side procedure.
- **A git command fails with "packfile ... index unavailable" or "invalid object"
  mid-rebuild:** another session repacked the shared object store. Run
  `git -c core.commitGraph=false fsck --connectivity-only --no-dangling` (silent and
  rc 0 means intact), then `git commit-graph write --reachable`. Treat the interrupted
  step as not done: reset your own rebuild branch to its last good commit and redo it.
- **A CI job fails:** search open issues for the test name before you blame the PR
  (`references/ci-watch.md`, flake triage).
- **A fork PR shows no checks:** its run waits at `action_required` for the
  maintainer's approval. That is the gate, not your branch.

## What bit this repo

- 2026-09-06 and 2026-09-08: issues amending one ADR landed in sequence. Prefix and
  suffix splicing of the one-line index row duplicated a clause twice; only a
  token-level merge got it right.
- #310, 2026-09-20: a green `--check` after `--fix` was not evidence. `--fix` had
  re-locked anchors it could not relocate.
- 2026-09-30 (#344): a rebase silently stripped commit-message lines starting with
  `#98` and `#363`.
- #654, 2026-11-03: merging `main` into a PR branch three times inflated gated
  citations from 902 to 1,428, and a `--fix` from `main`'s lock moved two citations
  onto the wrong code under a green `--check`.
- 2026-09-20: `gh run list --limit 1` returned the Dependency Graph run, which reported
  `success` with one job while CI was still running.
- 2026-10-31 (#616): two background sleep loops polling a run returned while it was
  still `in_progress`, and a 12-job run read as "success=8, null=4".
- 2026-11-05: another session's repack broke a cherry-pick mid-rebuild. Nothing was
  lost; the step was redone.
- 2026-10-27 (#386, #172): force pushes of a pushed branch were refused. A merge
  commit, with `main` as its first parent, fast-forwarded both the branch and `main`.
- PR #637, 2026-10-31: a two-issue PR was merged with `--rebase --match-head-commit`,
  and `main` got both per-issue commits, its tree identical to the verified head.

## Pointers

- `references/rebase-doc-conflicts.md`: the conflict rules, and the citation lock
  across a rebase or a merge.
- `references/ci-watch.md`: pinned runs, job counts, time limits, hangs, flake triage.
- `references/pushed-branch-and-external-prs.md`: the merge-commit update, and fork PRs.
- `.github/workflows/ci.yml`: the jobs and the concurrency group.
- `docs/development/ci-hang-diagnostics.md` and ADR-0274: the CI time limits.
- `CONTRIBUTING.md`, "Fork CI needs the maintainer to press a button".
