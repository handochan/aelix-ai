# Folding checkpoints into the final commits

Lanes commit `wip(...)` checkpoints and never amend or rebase. The lead rebuilds them
into the commits that land: one per issue when the changes separate, one per
entangled group otherwise. Two slips have produced commits that looked fine by subject
and passed every gate, and only a diff against the source exposed them. So the
procedure checks each rebuilt commit.

1. **Back up first.** `git update-ref refs/backup/<n>-<round> <lane head>` for each
   lane head you fold. Backups cost nothing and survive a bad reset.
2. **Rebuild in a separate worktree**, never in the lane's:
   `git worktree add --detach /tmp/<n>-fold <base>`.
3. **Drive it with `set -e`** (a `#!/bin/bash` script with `set -euo pipefail`, or
   Python). In an `a && b; c` chain, a failed `b` still runs `c`. A cherry-pick then
   landed on the still-staged previous issue, and one commit held two issues.
4. **Stage what you apply.** `git apply` without `--index` changes only the working
   tree, so `git commit` took the index and the commit lacked the fix. Use
   `git apply --index`, or edit then `git add -u`. A patch cut from the final tip may
   not apply to an earlier commit. Make that edit by exact string replacement at that
   commit.
5. **Check each rebuilt commit.** After each one, `git diff --stat <its source> HEAD`
   must show only the delta you meant to fold. At the end, `git diff <lane head> HEAD`
   must be empty, or hold exactly the difference you can name.
6. **Write messages from files.** Use `git commit -F <file>` with the message the lane
   wrote, and `--cleanup=verbatim` if any line must survive as written. Then run
   `git log -1 --format=%B | grep -c '^#'`. A line starting with `#` is stripped by a
   later rebase.
7. **Gate each commit** that will land on its own: ruff, the type gate and the
   citation gate at minimum (aelix-preflight).

If a git command fails mid-rebuild with "pack index unavailable" or "invalid object",
another session repacked the shared object store under you. See the aelix-land skill
("Concurrent repack") before you continue.
