# Updating a pushed branch, and landing an outside contributor's PR

## A pushed PR branch that `main` has moved under

Do not amend, rebase and force-push a branch that is already pushed. It rewrites the
history reviewers commented on, and an agent's permission classifier may refuse it.
Build a merge commit instead. Its first parent is the new `main`, its second the
reviewed head, and its tree is what a rebase would have produced:

```bash
tree=$(git merge-tree --write-tree origin/main <reviewed head>)   # resolve conflicts first if any
# check it equals the tree of a rebased copy, if you made one:  git rev-parse <rebased>^{tree}
commit=$(git commit-tree "$tree" -p origin/main -p <reviewed head> -F <message file>)
git push origin "$commit":refs/heads/<branch>
```

The push fast-forwards the branch. Fast-forwarding `main` to that commit later marks
the PR merged. Run the gates on the merge commit's tree (the citation lock needs the
per-side procedure in `rebase-doc-conflicts.md`), and wait for both CI runs on it.

So that this is rarely needed, push a lane's branch only after its review rounds are
done.

## An outside contributor's PR (from a fork)

Merge it without pushing to the fork and without squashing. A squash drops the
contributor's history and leaves the PR "closed" instead of "merged".

1. Review it like any lane: an independent verify, the live checks the surface needs,
   and the gates.
2. On a branch of the main repository (for example `merge/<pr>-<slug>`), make a
   `git merge --no-ff <pr head>` onto `main`, with `main` as the first parent. Put any
   maintainer follow-up (review fixes, the ADR, the CHANGELOG) in a separate commit on
   top.
3. Push that branch, and wait for its CI to finish with every job green.
4. Fast-forward `main` to the follow-up commit:
   `git push origin <follow-up sha>:refs/heads/main`. GitHub marks the PR merged, with
   the merge commit as its merge commit, and a `Fixes #n` in the PR closes the issue.
5. Credit the contributor, and describe the follow-up commit in the comment the
   maintainer posts or asks you to post.

The fork's CI waits at `action_required` until the maintainer approves the run
(CONTRIBUTING, "Fork CI needs the maintainer to press a button").
