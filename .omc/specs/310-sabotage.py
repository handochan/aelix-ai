#!/usr/bin/env python3
"""#310 — does each piece of this change have a test that fails without it?

Eleven minimal reverts of what this branch did to ``scripts/check_citations.py``,
each applied to a FRESH tree taken from ``git archive HEAD`` — never to the
working tree — and each measured by running ``tests/test_citation_drift.py``
whole and counting which tests turn red.

    python .omc/specs/310-sabotage.py

WHY A FRESH TREE PER REVERT. The tests read the real lock and the real tracked
file list, so they cannot be run against a stub. ``git archive`` gives a byte
copy of the commit; ``git init`` + ``git add -A`` inside it hands
``_tracked_files`` the same list back without touching the worktree it came
from. Eleven trees, eleven pytest runs, about half a minute.

WHY EACH REVERT IS PINNED TO A UNIQUE STRING RATHER THAN A LINE NUMBER OR A
PATTERN. The first pass at this table selected #5 with "the last line matching
``if .*is_trivial_anchor``". That is ``cmd_lock``'s branch, not ``cmd_check``'s
— ``cmd_check``'s comes first in the file — so #5 silently re-applied #4, #4's
two-test result was counted a second time, and the commit message reported a
distribution nobody had measured. Every revert below asserts its anchor occurs
EXACTLY ONCE before it edits, so a mis-aimed revert is a crash instead of a
duplicate. That is the same failure this whole commit is about, one level up:
a claim about a run that the run does not support.

MEASURED 2026-09-23 on this branch, restacked onto `96f93c0c`: baseline
29 passed; 8 reverts caught by exactly one test (1 failed, 28 passed), 3 by two
(2 failed, 27 passed), 0 uncaught. The absolute counts belong to the tree — a
rebase that adds or removes a test moves them — while the 8/3/0 split is a
property of the change, and it did not move when this branch came off
`fad2e28`. Re-run this after any rebase. The baseline assertion below refuses
to print a table taken from a tree that was not green to begin with, which is
exactly how the stale ambiguity ratchet was caught after that restack.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = "scripts/check_citations.py"
TESTS = "tests/test_citation_drift.py"

#: ``.venv`` if this repo has one — ``uv run`` would resync it eleven times,
#: and each resync is a chance to import a package mid-rewrite.
PYTHON = REPO / ".venv" / "bin" / "python"

#: ``(n, what it reverts, file, exact text to find, what to put there)``.
REVERTS: list[tuple[int, str, str, str, str]] = [
    (
        1,
        "ambiguous_anchors: `> 1` -> `> 2`",
        SCRIPT,
        'if len(tree.relocate(k.rsplit(":", 1)[0], a)) > 1)',
        'if len(tree.relocate(k.rsplit(":", 1)[0], a)) > 2)',
    ),
    (
        2,
        "ambiguous_anchors gutted to `return []`",
        SCRIPT,
        'return sorted(k for k, a in anchors.items() if len(tree.relocate(k.rsplit(":", 1)[0], a)) > 1)',
        "return []",
    ),
    (
        3,
        "cmd_lock's return drops `remaining`",
        SCRIPT,
        "return 1 if (unanchorable or remaining or blank) else 0",
        "return 1 if (unanchorable or blank) else 0",
    ),
    (
        4,
        "cmd_lock's blank-anchor branch disabled",
        SCRIPT,
        "        if is_trivial_anchor(a):\n",
        "        if False:\n",
    ),
    (
        5,
        "cmd_check's blank-anchor branch disabled",
        SCRIPT,
        "        if have is not None and is_trivial_anchor(have):\n",
        "        if False:\n",
    ),
    (
        6,
        "is_trivial_anchor gutted to `return False`",
        SCRIPT,
        "return not anchor or all(_TRIVIAL.match(a) for a in anchor)",
        "return False",
    ),
    (
        7,
        "cmd_lock's `keep` branch neutered, its wiring left in place",
        SCRIPT,
        "        if keep is not None and c.key in keep:\n",
        "        if False:\n",
    ),
    (
        8,
        "cmd_fix stops passing `keep=`",
        SCRIPT,
        "return cmd_lock(quiet=False, remaining=len(stuck), keep={d.cite.key: d.expected for d in stuck})",
        "return cmd_lock(quiet=False, remaining=len(stuck))",
    ),
    (
        9,
        "_TRIVIAL weakened back to blank-only",
        SCRIPT,
        "_TRIVIAL = re.compile(r'^[\\s)\\]}\\'\"#,:]*$')",
        '_TRIVIAL = re.compile(r"^\\s*$")',
    ),
    (
        10,
        "the OVERWROTE report silenced outright",
        SCRIPT,
        "        if replaced and keep is None:\n",
        "        if False:\n",
    ),
    (
        11,
        "`if replaced and keep is None:` widened back to `if replaced:`",
        SCRIPT,
        "        if replaced and keep is None:\n",
        "        if replaced:\n",
    ),
]

FAILED = re.compile(r"^FAILED (\S+::\S+)", re.M)


def fresh_tree(dest: Path) -> None:
    dest.mkdir(parents=True)
    tar = subprocess.run(
        ["git", "-C", str(REPO), "archive", "HEAD"], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)


def run_tests(tree: Path) -> tuple[str, list[str]]:
    out = subprocess.run(
        [
            str(PYTHON) if PYTHON.exists() else sys.executable,
            "-m",
            "pytest",
            str(tree / TESTS),
            "-q",
            "-p",
            "no:cacheprovider",
            "--no-header",
            "-rf",
        ],
        cwd=str(tree),
        capture_output=True,
        text=True,
    )
    text = out.stdout + out.stderr
    tally = [ln for ln in text.splitlines() if " passed" in ln or " failed" in ln]
    names = sorted({f.split("::")[-1] for f in FAILED.findall(text)})
    return (tally[-1] if tally else "(no tally)"), names


def apply(tree: Path, rel: str, find: str, repl: str) -> None:
    path = tree / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(find)
    if n != 1:
        raise SystemExit(f"anchor occurs {n} times, expected exactly 1:\n  {find!r}")
    path.write_text(text.replace(find, repl), encoding="utf-8")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="cit-sabotage-"))
    rows: list[tuple[int, str, int]] = []
    try:
        base = root / "base"
        fresh_tree(base)
        tally, failed = run_tests(base)
        print(f"BASELINE (no revert): {tally}")
        if failed:
            raise SystemExit(f"baseline is not green ({failed}); the table would mean nothing")

        for n, label, rel, find, repl in REVERTS:
            tree = root / f"r{n:02d}"
            fresh_tree(tree)
            apply(tree, rel, find, repl)
            tally, names = run_tests(tree)
            rows.append((n, label, len(names)))
            print(f"#{n:2d} {label}\n      {tally}\n      caught by {len(names)}: {names}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    one = [r for r in rows if r[2] == 1]
    many = [r for r in rows if r[2] >= 2]
    none = [r for r in rows if r[2] == 0]
    print(f"\nTOTAL: {len(one)} caught by exactly one test, {len(many)} by two, {len(none)} uncaught.")
    for r in none:
        print(f"  UNCAUGHT: #{r[0]} {r[1]}")
    return 1 if none else 0


if __name__ == "__main__":
    raise SystemExit(main())
