"""Aggregate #446 r2 A/B artifacts: python agg_446_ab.py <dir with m446ab-*/ab.jsonl>.

Per (py, load) cell, per version (before = e24ea2e8's test, after = the fix) and
param: runs, passed, failed (by the failure's first line), how many attempts the
passing runs took, and the premise lines the fix's attempts left, by kind.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def first_line(detail: str) -> str:
    text = detail.split("\n")[0]
    for key in (
        "assert None == 0",
        "the root never announced its tree",
        "did not bound the exit drain",
        "came back before the caller's own deadline",
        "every one of 3 attempts",
        "the timeout leg's kill left",
        "does not see the root's exit",
        "never raised",
        "before the caller's",
    ):
        if key in detail:
            return key
    return text[:120]


def main() -> None:
    root = Path(sys.argv[1])
    rows = []
    for f in sorted(root.glob("**/ab.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    cells = collections.defaultdict(list)
    for r in rows:
        cells[(r["py"], r["load"])].append(r)
    order = {"none": 0, "cpu3": 1, "cpu4": 2}
    totals = collections.defaultdict(collections.Counter)
    for key in sorted(cells, key=lambda k: (order.get(k[1], 9), k[0])):
        print(f"=== py{key[0]} load={key[1]}")
        timeouts = sorted({r["timeout"] for r in cells[key]}, key=lambda x: (x is None, x))
        for version in ("before", "after", "sibling"):
            for t in timeouts:
                sub = [r for r in cells[key] if r["version"] == version and r["timeout"] == t]
                if not sub:
                    continue
                c = collections.Counter(r["outcome"] for r in sub)
                fails = collections.Counter(first_line(r["detail"]) for r in sub if r["outcome"] != "passed")
                att = collections.Counter(len(r["attempts"]) for r in sub if r["outcome"] == "passed")
                prem = collections.Counter(k for r in sub for k, _ in r["premises"])
                totals[(version, t)]["runs"] += len(sub)
                totals[(version, t)]["failed"] += c.get("failed", 0)
                print(
                    f"  {version:6s} timeout={t}: runs={len(sub)} passed={c.get('passed', 0)} "
                    f"failed={c.get('failed', 0)} {dict(fails)} attempts(passed)={dict(sorted(att.items()))} "
                    f"premises={dict(prem)}"
                )
                for r in sub:
                    if version == "after" and t is not None and r["outcome"] != "passed":
                        print(f"    FAIL round {r['round']}: {r['detail'][:600]}")
    print("=== totals")
    for (version, t), c in sorted(totals.items(), key=lambda kv: (kv[0][0], kv[0][1] is None)):
        print(f"  {version:6s} timeout={t}: runs={c['runs']} failed={c['failed']}")


if __name__ == "__main__":
    main()
