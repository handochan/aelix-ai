#!/usr/bin/env python3
"""#310 — is "an anchor too short to gate" a thing length can measure?

ADR-0248 §3 rules that the boundary for "this anchor cannot name one place" is
UNIQUENESS IN THE TARGET, not the anchor's length. This script is the
measurement behind that, and it exists because the first version of the number
in the ADR could not be reproduced by anyone, including its author.

    python .omc/specs/310-threshold.py                   # this tree's lock
    python .omc/specs/310-threshold.py --rev 96f93c0c    # the lock at the base
    python .omc/specs/310-threshold.py --sweep           # 1220 combinations per lock

WHAT IT MEASURES. For every anchor in ``citations.lock.json`` it asks the real
gate's own relocator how many places in the target file that anchor matches.
Anchors matching 2+ are the population a length rule would have to separate. It
then applies ten different readings of "the anchor is shorter than N characters"
and reports, for each: how many anchors it flags, how many of THOSE are
perfectly unique (false alarms), and how many of the genuinely ambiguous ones it
still misses.

EVERY FIGURE BELOW NAMES THE LOCK IT CAME FROM, and the lock moves under this
branch every time it is rebased. These were re-measured on 2026-09-23 after the
branch was restacked from `fad2e28` onto `96f93c0c` (#311, #194 and #309 landed
in between), which took both locks from 542 anchors to 552 and moved every
total here. The argument did not move: see the paragraph after the tables.

MEASURED against the BRANCH BASE `96f93c0c` (552 anchors, 21 of them ambiguous):

    sum(len(line))       < 24  ->  flags  27,  21 unique among them,  misses 15 of 21
    len(' '.join(lines)) < 24  ->  flags  27,  21 unique among them,  misses 15 of 21
    len(first line)      < 24  ->  flags 101,  95 unique among them,  misses 15 of 21
    max(len(line))       < 24  ->  flags  29,  23 unique among them,  misses 15 of 21

MEASURED the same day against THIS BRANCH's lock (552 anchors again — the two
blank ones are repaired, not removed — 19 of them ambiguous):

    sum(len(line))       < 24  ->  flags  25,  21 unique among them,  misses 15 of 19
    len(' '.join(lines)) < 24  ->  flags  25,  21 unique among them,  misses 15 of 19
    len(first line)      < 24  ->  flags  99,  95 unique among them,  misses 15 of 19
    max(len(line))       < 24  ->  flags  27,  23 unique among them,  misses 15 of 19

Both locks hold 552 anchors, so the anchor count does not tell them apart and
every figure quoted elsewhere has to name its lock. At 24 characters length
flags 27 at the base and 25 here, is wrong about 21 of them either way, and
reaches 6 of the 21 there against 4 of the 19 here. ``Tree.relocate`` answers
the real question for the whole lock in 0.6 s, so the weaker proxy buys nothing.

THE CORRECTION THIS SCRIPT RECORDS. ADR-0248 §3 first said "flags 26 anchors of
which 19 name exactly one place, and still misses 7 of the 20". That is
self-contradictory before it is even checked — 26 flagged minus 19 unique is 7
CAUGHT, so 13 are missed, not 7 — and ``--sweep`` finds ``(26, 19, 7)`` in none
of the 1220 combinations it tries per lock (10 metrics x thresholds 4..64 x
``<`` and ``<=``), nor ``(26, 19)`` at any threshold. Run it against both
locks and that is 2440 combinations with no match anywhere — re-swept against
both post-restack locks on 2026-09-23 and still NONE on each. The conclusion
was right for different reasons than the ones written down; the numbers above
replace them.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent.parent


def load_script(repo: Path) -> ModuleType:
    """Import ``check_citations`` with its REPO/LOCK_PATH bound to ``repo``."""

    spec = importlib.util.spec_from_file_location("_cc", repo / "scripts" / "check_citations.py")
    assert spec and spec.loader
    cc = importlib.util.module_from_spec(spec)
    sys.modules["_cc"] = cc
    spec.loader.exec_module(cc)
    cc.REPO = repo
    cc.LOCK_PATH = repo / "citations.lock.json"
    return cc


def metrics(cc: ModuleType) -> dict[str, object]:
    """Ten readings of "how long is this anchor", so the answer is not one metric."""

    return {
        "sum(len(line))": lambda a: sum(len(x) for x in a),
        "len(' '.join)": lambda a: len(" ".join(a)),
        "len(''.join)": lambda a: len("".join(a)),
        "len(first line)": lambda a: len(a[0]) if a else 0,
        "max(len(line))": lambda a: max((len(x) for x in a), default=0),
        "min(len(line))": lambda a: min((len(x) for x in a), default=0),
        "len(norm(' '.join))": lambda a: len(cc.norm(" ".join(a))),
        "sum(len(norm(line)))": lambda a: sum(len(cc.norm(x)) for x in a),
        "len(longest line)": lambda a: len(max(a, key=len)) if a else 0,
        "len(most distinctive)": lambda a: len(
            max([x for x in a if not cc._TRIVIAL.match(x)] or [""], key=len)
        ),
    }


def report(repo: Path, label: str, sweep: bool) -> None:
    cc = load_script(repo)
    anchors = json.loads((repo / "citations.lock.json").read_text(encoding="utf-8"))["anchors"]
    tree = cc.Tree()
    hits = {k: len(tree.relocate(k.rsplit(":", 1)[0], a)) for k, a in anchors.items()}
    ambiguous = {k for k, v in hits.items() if v > 1}

    print(f"\n=== {label}: {len(anchors)} anchors, {len(ambiguous)} of them ambiguous ===")
    for name, m in metrics(cc).items():
        flagged = {k for k, a in anchors.items() if m(a) < 24}
        unique = sum(1 for k in flagged if hits[k] == 1)
        print(
            f"  {name:22} < 24 -> flags {len(flagged):4}, "
            f"{unique:4} unique among them, misses {len(ambiguous - flagged):3} "
            f"of {len(ambiguous)}"
        )

    if not sweep:
        return
    exact, pair = [], []
    for name, m in metrics(cc).items():
        for thr in range(4, 65):
            for op, cmp in (("<", lambda v, t: v < t), ("<=", lambda v, t: v <= t)):
                flagged = {k for k, a in anchors.items() if cmp(m(a), thr)}
                unique = sum(1 for k in flagged if hits[k] == 1)
                missed = len(ambiguous - flagged)
                if (len(flagged), unique, missed) == (26, 19, 7):
                    exact.append(f"{name} {op} {thr}")
                if (len(flagged), unique) == (26, 19):
                    pair.append(f"{name} {op} {thr} (misses {missed})")
    total = len(metrics(cc)) * 61 * 2
    print(f"  swept {total} combinations (10 metrics x 4..64 x two operators)")
    print(f"    matching the retracted claim (26 flagged, 19 unique, 7 missed): {exact or 'NONE'}")
    print(f"    matching (26 flagged, 19 unique) at any miss count:            {pair or 'NONE'}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rev", help="measure the lock and script at this git revision instead")
    p.add_argument("--sweep", action="store_true", help="also search every metric x threshold")
    args = p.parse_args(argv)

    if not args.rev:
        report(REPO, "this tree", args.sweep)
        return 0
    with tempfile.TemporaryDirectory(prefix="cit-thresh-") as tmp:
        dest = Path(tmp) / "t"
        dest.mkdir(parents=True)
        archive = subprocess.run(
            ["git", "-C", str(REPO), "archive", args.rev], capture_output=True, check=True
        )
        subprocess.run(["tar", "-xf", "-", "-C", str(dest)], input=archive.stdout, check=True)
        subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)
        report(dest, args.rev, args.sweep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
