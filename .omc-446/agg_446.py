"""Aggregate #446 measure artifacts: python agg_446.py <dir with m446-py*/m446.jsonl>.

Per (py, load): the REAL test's outcomes per param, the twin's misses (deadline arm
timed out), the root's exit relative to S (the instant before the product's Popen;
the deadline is S + 2.0 on win32), and for every miss and the slowest non-misses the
phases: pwsh created -> python root created -> first statement -> stdlib -> aelix_ai
import -> holder spawned -> announced -> python end -> pwsh exit -> product's wait
return, with CPU next to wall where the clocks allow (CPU << wall = the phase WAITED:
scheduling or I/O; CPU ~ wall = the phase WORKED).
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = min(len(xs) - 1, max(0, round(p * (len(xs) - 1))))
    return xs[k]


def fmt(xs: list[float]) -> str:
    if not xs:
        return "n=0"
    return (
        f"n={len(xs)} min={min(xs):.3f} p50={q(xs, 0.5):.3f} p90={q(xs, 0.9):.3f} "
        f"p99={q(xs, 0.99):.3f} max={max(xs):.3f}"
    )


def phases(r: dict) -> str:
    def g(k: str) -> float | None:
        return r.get(k)

    def d(a: str, b: str) -> str:
        x, y = g(a), g(b)
        return "  ?  " if x is None or y is None else f"{y - x:6.3f}"

    cpu_pwsh = r.get("root_cpu")
    py_first_cpu = r.get("py_first_cpu")
    imp_cpu = None
    if r.get("py_aelix_cpu") is not None and r.get("py_stdlib_cpu") is not None:
        imp_cpu = r["py_aelix_cpu"] - r["py_stdlib_cpu"]
    out = (
        f"pwsh_created={g('root_created')} pwsh->py_created {d('root_created', 'py_created')} "
        f"py_created->first {d('py_created', 'py_first')} (cpu {py_first_cpu}) "
        f"stdlib {d('py_first', 'py_stdlib')} aelix_import {d('py_stdlib', 'py_aelix')} (cpu "
        f"{None if imp_cpu is None else round(imp_cpu, 3)}) spawn {d('py_aelix', 'py_spawned')} "
        f"announce {d('py_spawned', 'py_announced')} py_end@{g('py_end')} "
        f"py_end->pwsh_exit {d('py_end', 'root_exited')} pwsh_exit@{g('root_exited')} "
        f"pwsh_cpu={cpu_pwsh} wait_in@{g('wait_in')} wait_out@{g('wait_out')} "
        f"timeout_at@{g('wait_timeout_at')} state@timeout={g('state_at_timeout')} "
        f"kill_in@{g('kill_in')} kill_state={g('kill_state')} holder_first@{g('holder_first')} "
        f"end={g('end')} ticks={g('ticks')} elapsed={g('elapsed')}"
    )
    return out


def why(r: dict, deadline: float) -> str:
    """Where a miss's time went, from the stamps."""

    py_end = r.get("py_end")
    if py_end is None:
        reached = [k for k in ("first", "stdlib", "aelix", "spawned", "announced") if r.get(f"py_{k}") is not None]
        last = reached[-1] if reached else "none"
        return f"START-UP: the python root never reached its end (last stamp: {last})"
    if py_end >= deadline:
        return f"START-UP: the python root ended {py_end:.3f}s after S, past the {deadline}s deadline"
    rx = r.get("root_exited")
    to = r.get("wait_timeout_at")
    if rx is not None and to is not None and rx < to:
        return f"EXIT OBSERVATION: pwsh exited at {rx:.3f}s, the product timed out at {to:.3f}s"
    return (
        f"PWSH TEARDOWN: the python root ended at {py_end:.3f}s, pwsh exited at {rx}s "
        f"(after the deadline)"
    )


def main() -> None:
    root = Path(sys.argv[1])
    rows = []
    for f in sorted(root.glob("**/m446.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    cells = collections.defaultdict(list)
    for r in rows:
        cells[(r["py"], r["load"])].append(r)
    loads = {"none": 0, "cpu3": 1, "cpu4": 2}
    for key in sorted(cells, key=lambda k: (loads.get(k[1], 9), k[0])):
        rs = cells[key]
        print(f"=== py{key[0]} load={key[1]}")
        for kind in ("real", "twin"):
            for t in sorted({r["timeout"] for r in rs if r["kind"] == kind}, key=lambda x: (x is None, x)):
                sub = [r for r in rs if r["kind"] == kind and r["timeout"] == t]
                if kind == "real":
                    c = collections.Counter(r["outcome"] for r in sub)
                    fails = [r["detail"].splitlines()[0][:120] for r in sub if r["outcome"] != "passed"]
                    print(f"  real timeout={t}: {dict(c)} {fails}")
                else:
                    ends = collections.Counter(r["end"] for r in sub)
                    misses = [r for r in sub if r.get("timed_out")]
                    print(f"  twin timeout={t}: n={len(sub)} ends={dict(ends)} timed_out={len(misses)}")
                    exits = [r["root_exited"] for r in sub if r.get("root_exited") is not None and not r.get("timed_out")]
                    if not exits:
                        exits = [r["py_end"] for r in sub if r.get("py_end") is not None and not r.get("timed_out")]
                    print(f"    root exit after S (rows that exited on their own): {fmt(exits)}")
                    for name, a, b in (
                        ("pwsh->py_created", "root_created", "py_created"),
                        ("py_created->first", "py_created", "py_first"),
                        ("aelix import", "py_stdlib", "py_aelix"),
                        ("first->py_end", "py_first", "py_end"),
                        ("py_end->pwsh_exit", "py_end", "root_exited"),
                        ("pwsh_exit->wait_out", "root_exited", "wait_out"),
                    ):
                        xs = [r[b] - r[a] for r in sub if r.get(a) is not None and r.get(b) is not None]
                        print(f"    {name:20s} {fmt(xs)}")
                    cpu = [r["root_cpu"] for r in sub if r.get("root_cpu") is not None]
                    print(f"    pwsh cpu (kernel+user) {fmt(cpu)}")
                    if t is not None:
                        for r in misses:
                            print(f"    MISS round {r['round']}: {why(r, t)}")
                            print(f"      {phases(r)}")
                        ok = sorted((r for r in sub if not r.get("timed_out") and r.get("root_exited") is not None), key=lambda r: r["root_exited"])
                        for r in ok[-2:]:
                            print(f"    slowest non-miss round {r['round']}: root exit {r['root_exited']:.3f}")
                            print(f"      {phases(r)}")


if __name__ == "__main__":
    main()
