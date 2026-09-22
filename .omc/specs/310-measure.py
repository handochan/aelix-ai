#!/usr/bin/env python3
"""#310 — does ``check_citations.py --fix`` re-lock what it could not relocate?

Runs against the branch BASE and against the branch, from a scratch copy of the
tree, and prints the numbers in ADR-0248 §1 and §3. Nothing is written inside
the repository: the whole measurement happens in a temporary directory.

    python .omc/specs/310-measure.py                  # this tree
    python .omc/specs/310-measure.py --rev 96f93c0c   # the tree at the branch base

WHAT IT DOES. Copies the tracked tree, inserts six lines into ``harness/core.py``
above a cited comment block, and edits one line INSIDE that block so exact
relocation cannot find it. Then: ``--check``, ``--fix``, ``--check`` again, and a
direct comparison of every anchor the lock holds afterwards against the lines the
file actually has at those numbers — which is the only evidence that counts here,
because the whole finding is that ``--check`` returning 0 can be a lie.

EVERY TOTAL HERE BELONGS TO A TREE, so each one names its tree. The drift and
relocation counts scale with how many citations the tree holds, and this branch
has been rebased once already (`fad2e28` -> `96f93c0c`, +10 anchors), which
moved all of them. The defect itself is invariant: 12 stuck, 6 keys, green.

MEASURED on `fad2e28`, where the defect was found, with the script as it stood
before this commit: 71 drifted, 59 relocated, 12 citation sites reported
un-relocatable, and all 12 re-locked anyway — 6 distinct lock keys rewritten
over a stale range — after which ``--check`` printed
``citations OK — 937 gated, none drifted.`` with exit code 0.

MEASURED on `96f93c0c`, this branch's base, with the same unfixed script:
77 drifted, 65 relocated, the same 12 stuck, the same 6 keys rewritten over a
stale range, and ``--check`` green again at ``950 gated``.

With the fix, on this tree: the same 77 / 65 / 12 as its base, 0 keys rewritten
over a stale range, ``--check`` rc=1 still naming the 12, and 6 anchors the
files demonstrably do not carry left standing for a human.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CORE = "packages/aelix-agent-core/src/aelix_agent_core/harness/core.py"
#: 0-indexed. The cited block is 1222-1224; this is the middle line of it.
EDIT_AT = 1222
EDIT_EXPECT = "# parity (Pi"
EDIT_TO = "        # parity (Pi ``agent-harness.ts`` steer paths always enqueue, any"
INSERT_AT = 1220


def _run(cwd: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, "scripts/check_citations.py", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout + p.stderr


def _materialise(repo: Path, dest: Path, rev: str | None) -> None:
    """The tracked tree only, as a git repo of its own — ``_tracked_files`` shells out."""

    src = f"{rev}^{{tree}}" if rev else "HEAD"
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", src], capture_output=True, check=True
    )
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["tar", "-xf", "-", "-C", str(dest)], input=archive.stdout, check=True)
    if rev is None:
        # The working tree may differ from HEAD; copy the files that matter.
        for rel in (CORE, "scripts/check_citations.py", "citations.lock.json"):
            shutil.copy2(repo / rel, dest / rel)
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "-c", "user.email=p@p", "-c", "user.name=p", "commit", "-qm", "b"],
        check=True,
    )


def _break_it(dest: Path) -> None:
    p = dest / CORE
    lines = p.read_text(encoding="utf-8").split("\n")
    assert EDIT_EXPECT in lines[EDIT_AT], f"line {EDIT_AT + 1} moved: {lines[EDIT_AT][:60]!r}"
    lines[EDIT_AT] = EDIT_TO
    lines[INSERT_AT:INSERT_AT] = [f"        # {c} {(c + ' ') * 28}" for c in "abcdef"]
    p.write_text("\n".join(lines), encoding="utf-8")


def _anchors(dest: Path) -> dict[str, list[str]]:
    return json.loads((dest / "citations.lock.json").read_text(encoding="utf-8"))["anchors"]


def _stale(dest: Path, anchors: dict[str, list[str]]) -> list[str]:
    """Anchors whose key names a range the file no longer has that text at.

    Read straight off disk rather than through the tool, because the tool is
    what is on trial.
    """

    out = []
    cache: dict[str, list[str]] = {}
    for key, want in anchors.items():
        rel, _, span = key.rpartition(":")
        if rel not in cache:
            try:
                text = (dest / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                cache[rel] = []
            else:
                lines = text.split("\n")
                if lines and lines[-1] == "":
                    lines.pop()
                cache[rel] = lines
        a, _, b = span.partition("-")
        # Capped at MAX_ANCHOR_LINES, as ``Tree.anchor`` caps it. Without this
        # the probe reported 141 false mismatches — every anchor longer than
        # eight lines — which is the probe lying about the tool, the same
        # failure one level out.
        start, end = int(a), int(b or a)
        have = [" ".join(x.split()) for x in cache[rel][start - 1 : min(end, start + 7)]]
        if have != want:
            out.append(key)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rev", help="measure the tree at this git rev instead of the working tree")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent.parent))
    args = ap.parse_args()
    repo = Path(args.repo)

    with tempfile.TemporaryDirectory(prefix="310-") as tmp:
        dest = Path(tmp) / "tree"
        _materialise(repo, dest, args.rev)
        before = _anchors(dest)
        rc, out = _run(dest, "--check")
        print(f"baseline --check rc={rc}  {out.splitlines()[0] if out else ''}")

        _break_it(dest)
        rc, out = _run(dest, "--check")
        drifted = out.count("no longer points at what it cited")
        print(f"\nafter breaking it: --check rc={rc}, {drifted} drifted")

        rc_fix, out = _run(dest, "--fix")
        relocated = next((line for line in out.splitlines() if line.startswith("relocated")), "")
        stuck = next((line for line in out.splitlines() if "could NOT" in line), "0 could NOT")
        print(f"--fix rc={rc_fix}\n  {relocated}\n  {stuck}")

        after = _anchors(dest)
        rewritten = sorted(k for k in after if k in before and before[k] != after[k])
        print(f"\nanchors rewritten over a stale range: {len(rewritten)}")
        for key in rewritten[:8]:
            print(f"  {key}\n      was: {before[key][0][:70] if before[key] else '(empty)'}")
            print(f"      now: {after[key][0][:70] if after[key] else '(empty)'}")

        rc_check, out = _run(dest, "--check")
        print(f"\n--check AFTER --fix: rc={rc_check}")
        print(f"  {out.splitlines()[0] if out else ''}")
        print(f"  anchors the file does not actually carry: {len(_stale(dest, after))}")
        print(
            "\nVERDICT: "
            + (
                "the gate went green over citations it had just failed to repair."
                if rc_check == 0 and rewritten
                else "the failures survived into the lock and the gate stayed red."
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
