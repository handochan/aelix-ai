"""Run string-replacement mutants against a test command, in the way that does not lie.

Each mutant replaces one exact snippet in one file, runs the command, and restores
the file. A mutant is KILLED when the command fails (or times out), and SURVIVES
when it still passes: a survivor is a line no test guards. Stdlib only::

    python .agents/skills/aelix-tests-that-bite/scripts/mutants.py \
        --spec mutants.json --tree <dir> --check-import aelix_agent_core \
        --log mutants-log.json -- python -m pytest -p no:cacheprovider -q -x tests/harness

``mutants.json`` is a list of objects::

    [{"id": "M1", "file": "packages/x/src/x/mod.py", "old": "if a >= b:", "new": "if a > b:"}]

``old`` must occur exactly once in ``file`` (paths are relative to ``--tree``).

WHAT IT GUARDS AGAINST, each of which has given this repository a false result:

* A stale ``.pyc``. Two mutants of one file with the same size, written within one
  second, share the size and mtime-second that the bytecode cache checks, so the
  second run imported the FIRST mutant (a false "survived"). Every run gets
  ``PYTHONDONTWRITEBYTECODE=1`` and the mutated file's cached bytecode is deleted.
* A revert that loses work. ``git checkout -- file`` restores the index copy and
  silently drops uncommitted edits. This script restores the bytes it saved and
  checks their sha256.
* Testing the wrong tree. An editable install in a shared venv can import the
  packages of another checkout, so mutating a copy would change nothing the tests
  load. ``--check-import MODULE`` asserts that MODULE resolves inside ``--tree``.
* A broken baseline. The unmutated command must pass first, or every mutant would
  read as killed.

Re-run every survivor by hand before you report it. Exit: 0 every mutant killed,
1 at least one survived, 2 a setup problem (the baseline failed, a snippet was not
unique, a restore did not match).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def drop_bytecode(source: Path) -> None:
    cache = source.parent / "__pycache__"
    if cache.is_dir():
        for pyc in cache.glob(f"{source.stem}.*.pyc"):
            pyc.unlink()


def run(command: list[str], tree: Path, timeout: float) -> tuple[str, int | None, float, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        cwd=tree,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        state = "passed" if proc.returncode == 0 else "failed"
        code: int | None = proc.returncode
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)  # only the group this script started
        else:
            proc.kill()
        out, _ = proc.communicate()
        state, code = "timeout", None
    text = out.decode("utf-8", errors="replace")
    tail = "\n".join(text.strip().splitlines()[-8:])
    return state, code, time.monotonic() - started, tail


def check_import(module: str, command: list[str], tree: Path) -> str | None:
    python = command[0] if Path(command[0]).name.startswith("python") else sys.executable
    probe = subprocess.run(
        [python, "-c", f"import {module}; print({module}.__file__)"],
        cwd=tree,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
    )
    where = probe.stdout.strip()
    if probe.returncode != 0 or not where:
        return f"cannot import {module}: {probe.stderr.strip()[-300:]}"
    if not Path(where).resolve().is_relative_to(tree):
        return f"{module} resolves to {where}, outside {tree}: the mutants would not be loaded"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--spec", required=True, help="JSON list of {id, file, old, new}")
    ap.add_argument("--tree", default=".", help="the tree to mutate (default: the cwd)")
    ap.add_argument(
        "--timeout", type=float, default=900.0, help="seconds per run; a timeout counts as killed"
    )
    ap.add_argument(
        "--check-import", metavar="MODULE", help="assert MODULE imports from inside --tree"
    )
    ap.add_argument("--log", help="write every result here as JSON")
    ap.add_argument("command", nargs="+", help="the test command, after --")
    args = ap.parse_args()

    tree = Path(args.tree).resolve()
    mutants = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    for m in mutants:
        text = (tree / m["file"]).read_text(encoding="utf-8")
        hits = text.count(m["old"])
        if hits != 1:
            print(
                f"{m['id']}: 'old' occurs {hits} times in {m['file']}; it must occur once",
                file=sys.stderr,
            )
            return 2
    if args.check_import:
        problem = check_import(args.check_import, args.command, tree)
        if problem:
            print(f"setup: {problem}", file=sys.stderr)
            return 2

    state, code, secs, tail = run(args.command, tree, args.timeout)
    print(f"baseline: {state} (rc {code}, {secs:.1f} s)")
    if state != "passed":
        print(f"setup: the unmutated command must pass first\n{tail}", file=sys.stderr)
        return 2

    results = []
    for m in mutants:
        path = tree / m["file"]
        original = path.read_bytes()
        digest = sha256(original)
        try:
            text = original.decode("utf-8")
            path.write_bytes(text.replace(m["old"], m["new"], 1).encode("utf-8"))
            drop_bytecode(path)
            state, code, secs, tail = run(args.command, tree, args.timeout)
        finally:
            path.write_bytes(original)
            drop_bytecode(path)
        if sha256(path.read_bytes()) != digest:
            print(
                f"{m['id']}: {m['file']} did not restore to its saved bytes; stop", file=sys.stderr
            )
            return 2
        verdict = "SURVIVED" if state == "passed" else "killed"
        results.append(
            {
                **m,
                "verdict": verdict,
                "state": state,
                "rc": code,
                "seconds": round(secs, 1),
                "tail": tail,
            }
        )
        print(f"{m['id']}: {verdict} ({state}, rc {code}, {secs:.1f} s)")

    killed = sum(r["verdict"] == "killed" for r in results)
    survivors = [r["id"] for r in results if r["verdict"] == "SURVIVED"]
    print(
        f"killed {killed}/{len(results)}"
        + (f"; survived: {', '.join(survivors)}" if survivors else "")
    )
    if args.log:
        Path(args.log).write_text(
            json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
