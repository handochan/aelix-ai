#!/usr/bin/env python3
"""A short brief of where this checkout stands, for a Claude Code SessionStart hook (#733, ADR-0303).

``.claude/settings.json`` runs it at the start of every Claude Code session in
this repository, and Claude Code adds what it prints to the session's context.
It prints, when they exist:

* the checkout, its branch and HEAD, and ``origin/main`` as the LOCAL ref has it
  (nothing is fetched), with ahead/behind counts and how many paths are dirty;
* how many worktrees share this repository;
* the issue locks a maintainer's workflow keeps under
  ``<git common dir>/aelix-issue-locks/<n>/owner.json`` whose ``state`` is
  ``active``: other sessions working in this repository right now;
* the newest ``.omc/specs/handoff-*.md`` by the date in its name;
* where the gates and the project skills are.

It prints only what exists. A fresh clone has no locks, but it does have
handoffs: ``.omc/specs/`` is tracked (``.gitignore`` re-includes it), so a
clone or a new worktree checks out every committed handoff with the checkout's
time as its mtime. That is why the newest handoff is chosen by the
``YYYY-MM-DD`` in its name, not by mtime. In a linked worktree the main
checkout's ``.omc/specs`` is read too, untracked handoffs included, and wins a
tie on the date; mtime breaks what is left.

WHY IT IS SHAPED LIKE THIS. Several sessions work in one repository at once,
and this runs inside every one of them, so it must never get in their way:

* **It writes nothing.** Every git call passes ``--no-optional-locks``. A plain
  ``git status`` refreshes the index and takes ``index.lock`` to write it back,
  which races another session's commit in the same checkout.
  ``tests/test_session_brief.py`` watches the index while it runs.
* **No network** (no fetch, no ``gh``) and **no ``uv run``**, which can resync a
  ``.venv`` another session's test run is using. In a partial (promisor) clone
  even ``git status`` fetches a missing object on demand, so every git call runs
  with :data:`NO_TRANSPORT_ENV`: ``GIT_NO_LAZY_FETCH=1``, honoured by git 2.45 and
  later and by some earlier maintenance releases (2.39.5 measured), and, for a
  git without it, ``GIT_ALLOW_PROTOCOL`` set to a protocol that does not exist,
  so the fetch it starts reaches no remote.
* **Bounded.** Each git call has a 2 s timeout and the whole brief stops asking
  after about 2.5 s; the hook's own timeout is 10 s.
* **It cannot fail a session.** Any exception ends in at most one line, and the
  exit status is always 0.
* **Python 3.9, standard library only.** The hook runs whatever ``python3`` is
  on ``PATH``, which is not this project's interpreter: macOS ships 3.9.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BUDGET_S = 2.5
"""Seconds after which no further git call is started."""

CALL_TIMEOUT_S = 2.0
"""The most any one git call may take."""

MAX_LOCKS = 8
"""Active locks listed by name; the rest are counted."""

NO_TRANSPORT_ENV = {
    # A partial (promisor) clone fetches a missing object on demand, even for
    # ``git status``: a git that honours this fetches nothing. That is 2.45 and
    # later, and some earlier maintenance releases (2.39.5 measured).
    "GIT_NO_LAZY_FETCH": "1",
    # For a git that does not know that variable: allow only a protocol
    # that does not exist, so a lazy fetch it starts cannot reach any remote.
    "GIT_ALLOW_PROTOCOL": "none",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}
"""Set for every git call: no transport, no credential prompt, no optional lock."""

_START = time.monotonic()


def _git(cwd: str, *args: str) -> str | None:
    """One read-only git call: its stdout, or ``None`` when it failed or the budget is spent."""

    remaining = BUDGET_S - (time.monotonic() - _START)
    if remaining <= 0:
        return None
    env = dict(os.environ)
    env.update(NO_TRANSPORT_ENV)
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=min(CALL_TIMEOUT_S, remaining),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def _cut(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."


def _active_locks(common_dir: Path) -> list[str]:
    lines: list[str] = []
    try:
        owners = sorted((common_dir / "aelix-issue-locks").glob("*/owner.json"))
    except OSError:
        return lines
    active = []
    for path in owners:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("state") == "active":
            active.append(data)
    for data in active[:MAX_LOCKS]:
        issue = data.get("issue")
        branch = data.get("branch") or "-"
        phase = data.get("phase") or ""
        lines.append("  " + _cut(f"#{issue} {branch} {phase}", 108))
    if len(active) > MAX_LOCKS:
        lines.append(f"  +{len(active) - MAX_LOCKS} more")
    if lines:
        lines.insert(0, f"active issue locks ({len(active)}): other sessions may be working here")
    return lines


_NAME_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _newest_handoff(roots: list[Path]) -> str | None:
    """The newest ``handoff-*.md`` under ``<root>/.omc/specs`` by the date in its name.

    *roots* come most preferred first: on equal dates the earlier root wins,
    then the later mtime. A name with no date ranks below every dated one.
    """

    best: tuple[tuple[str, int, float], Path, str] | None = None
    for rank, root in enumerate(roots):
        try:
            candidates = list((root / ".omc" / "specs").glob("handoff-*.md"))
        except OSError:
            continue
        for path in candidates:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            dates = _NAME_DATE.findall(path.name)
            day = dates[-1] if dates else ""
            key = (day, -rank, mtime)
            if best is None or key > best[0]:
                best = (key, path, day)
    if best is None:
        return None
    _key, path, day = best
    if day:
        return f"newest handoff by the date in its name: {path} ({day})"
    return f"newest handoff (no name carries a date; by mtime): {path}"


def _tools_line(top: Path) -> str | None:
    parts = []
    if (top / "scripts" / "preflight.py").is_file():
        parts.append("gates: uv run --frozen python scripts/preflight.py --plan")
    try:
        skills = sorted(
            p.name for p in (top / ".agents" / "skills").glob("aelix-*") if (p / "SKILL.md").is_file()
        )
    except OSError:
        skills = []
    if skills:
        parts.append("skills (.agents/skills): " + ", ".join(skills))
    return "; ".join(parts) if parts else None


def brief(cwd: str) -> list[str]:
    """The lines to print for a session started in *cwd*; empty outside a git checkout."""

    paths = _git(cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir")
    if paths is None:  # git before 2.31 has no --path-format; its common dir may be relative
        paths = _git(cwd, "rev-parse", "--show-toplevel", "--git-common-dir")
    if paths is None:
        return []
    found = paths.splitlines()
    if len(found) < 2:
        return []
    top, common = Path(found[0]), Path(cwd, found[1])
    lines = ["aelix session brief (scripts/session_brief.py, read-only):"]

    status = _git(cwd, "status", "--porcelain=v2", "--branch")
    branch, head, dirty = "?", "?", None
    if status is not None:
        dirty = 0
        for line in status.splitlines():
            if line.startswith("# branch.head "):
                branch = line[len("# branch.head ") :]
            elif line.startswith("# branch.oid "):
                head = line[len("# branch.oid ") :][:8]
            elif line and not line.startswith("#"):
                dirty += 1
    lines.append(f"checkout {top} on {branch} at {head}")

    main = _git(cwd, "rev-parse", "--verify", "--quiet", "--short=8", "refs/remotes/origin/main")
    summary = []
    if main is not None:
        counts = _git(cwd, "rev-list", "--left-right", "--count", "HEAD...refs/remotes/origin/main")
        where = f"origin/main {main.strip()} (local ref, not fetched)"
        if counts is not None and len(counts.split()) == 2:
            ahead, behind = counts.split()
            where += f": HEAD ahead {ahead}, behind {behind}"
        summary.append(where)
    if dirty is not None:
        summary.append(f"dirty paths: {dirty}")
    if summary:
        lines.append("; ".join(summary))

    worktrees = _git(cwd, "worktree", "list", "--porcelain")
    if worktrees is not None:
        count = sum(1 for line in worktrees.splitlines() if line.startswith("worktree "))
        lines.append(f"worktrees: {count}")

    lines.extend(_active_locks(common))

    roots = [top]
    if common.name == ".git" and common.parent != top:
        # A linked worktree: the main checkout's handoffs too, untracked ones
        # included, and preferred on an equal date.
        roots.insert(0, common.parent)
    handoff = _newest_handoff(roots)
    if handoff:
        lines.append(handoff)

    tools = _tools_line(top)
    if tools:
        lines.append(tools)
    return lines


def main() -> int:
    try:
        lines = brief(os.getcwd())
        if lines:
            sys.stdout.write("\n".join(lines) + "\n")
    except BaseException as exc:  # never fail a session start
        with contextlib.suppress(BaseException):
            sys.stdout.write(f"session brief skipped ({type(exc).__name__})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
