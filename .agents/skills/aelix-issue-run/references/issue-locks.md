# Issue locks

Several sessions and tools work in one clone at once, and each one can see only its
own context. An issue lock is a small JSON file that says who owns an issue right now.
It lives in the git common directory, so every worktree of the clone sees the same
lock, and it is never committed.

```text
$(git rev-parse --path-format=absolute --git-common-dir)/aelix-issue-locks/<issue>/owner.json
```

## Fields

These are the fields nearly every lock carries (69 locks in one clone on 2026-11-09:
each field below was in at least 67 of them):

| field | meaning |
|---|---|
| `issue` | the issue number |
| `owner` | the tool or person holding it (`Claude Code`, `Codex`, or a contributor's login for an outside claim) |
| `run_id` | the session or run that holds it, so a later session can find its transcript |
| `branch`, `worktree`, `base` | where the work is, and the commit it was cut from |
| `state` | `active` while held; `done` or `complete` once merged or released |
| `phase` | one line on where the work stands: `impl`, `round 1: verify + cross`, `merged PR #n as <sha>` |
| `authorization` | what the work rests on, quoting the maintainer's message and its date |
| `started_at`, `updated_at` | UTC ISO-8601 timestamps |

Optional fields: `board_item`, `head`, `pr`, `files` (what the lane will touch, so
other sessions can avoid it), `handoff` (the handoff file).

## Rules

- **Claim atomically.** Create the issue's directory with `mkdir`, which fails if it
  exists. If it exists, read `owner.json`. If `state` is `active` and the lock is not
  yours, stop and ask. Never take a lock over because it looks old.
- **An outside contributor's claim is a reservation.** Do not overwrite it with
  internal work, and do not reassign it because time has passed.
- **Update `phase` and `updated_at` at each stage.** Set `state` to `done` and record
  `head` and `pr` at merge. Do not delete a lock: finished locks are the record of
  who did what.
- **Read others' locks; never write them.**
- The SessionStart hook (`scripts/session_brief.py`) prints every active lock at the
  start of a Claude Code session.

## A claim, in Python

```python
import datetime, json, os, subprocess, sys

issue, branch, worktree, base, why = sys.argv[1:6]
common = subprocess.run(
    ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
    capture_output=True, encoding="utf-8", check=True,
).stdout.strip()
lock_dir = os.path.join(common, "aelix-issue-locks", issue)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
try:
    os.makedirs(os.path.dirname(lock_dir), exist_ok=True)
    os.mkdir(lock_dir)                              # fails if anyone holds or held it
except FileExistsError:
    with open(os.path.join(lock_dir, "owner.json"), encoding="utf-8") as fh:
        held = json.load(fh)
    if held.get("state") == "active":
        sys.exit(f"#{issue} is held: {held.get('owner')} {held.get('run_id')} ({held.get('phase')})")
lock = {
    "issue": int(issue), "owner": "<tool or login>", "run_id": os.environ.get("RUN_ID", ""),
    "branch": branch, "worktree": worktree, "base": base, "state": "active",
    "phase": "impl", "authorization": why, "started_at": now, "updated_at": now,
}
tmp = os.path.join(lock_dir, "owner.json.tmp")
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(lock, fh, indent=2, ensure_ascii=False)
os.replace(tmp, os.path.join(lock_dir, "owner.json"))
```

Re-claiming a lock whose `state` is `done` (an issue reopened) keeps the old file's
history: copy it to `owner.<date>.json` before you write the new one.
