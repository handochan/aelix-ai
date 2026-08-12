#!/usr/bin/env python3
"""The pyright gate, with the two assertions an error count alone cannot make.

Run: ``python scripts/check_types.py`` (or ``uv run python scripts/check_types.py``).

Why this exists rather than a bare ``pyright`` in CI (ADR-0213, #140):

1. **A count of zero is ambiguous.** The gate spent its whole life configured as
   ``include = ["src", "packages/*/src", "scripts"]``, in which ``packages/*/src``
   matched NOTHING. It analysed 8 files, reported only a spike script's errors,
   and read exactly like a clean tree while 239 files of product source had never
   been checked at all. So this asserts ``filesAnalyzed`` too, and fails if the
   gate suddenly starts seeing far fewer files than it should.

2. **One file must NOT be clean.** ``scripts/pyright_spike.py`` ends in three
   deliberate negative assertions ("Inverse cases -- pyright MUST emit errors for
   narrowing to be considered working"). Zero errors there means the narrowing
   that ``ExtensionAPI.on``'s overloads rely on has regressed. It is excluded from
   the main gate and checked inverted here, through its own config -- naming it on
   pyright's command line does not work, because the repo config's ``exclude``
   applies to command-line paths too and silently analyses 0 files.

Exit: 0 all checks pass; 1 a check failed; 2 pyright could not be run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPIKE_CONFIG = REPO_ROOT / "scripts" / "pyright_spike_config.json"
SPIKE_SOURCE = REPO_ROOT / "scripts" / "pyright_spike.py"

# The tree is 246 analysable files today. The floor is deliberately close to it:
# it exists to catch an include that has stopped matching, not to be a running
# total, and a real drop of 25+ files means the config broke rather than that
# someone deleted a module. Raise it when the tree grows substantially.
MIN_FILES_ANALYSED = 220


def _run_pyright(args: list[str]) -> dict:
    exe = shutil.which("pyright")
    if exe is None:
        print("FAIL: pyright is not on PATH (uv sync --all-packages)", file=sys.stderr)
        raise SystemExit(2)
    proc = subprocess.run(
        [exe, "--outputjson", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return json.loads(proc.stdout)
    except ValueError:
        print(
            f"FAIL: pyright produced no JSON (exit {proc.returncode}).\n"
            f"stdout: {proc.stdout[:2000]}\nstderr: {proc.stderr[:2000]}",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


def main() -> int:
    failures: list[str] = []

    # --- The product gate: must SEE the tree, and must be clean ---------------
    report = _run_pyright([])
    summary = report["summary"]
    analysed = summary["filesAnalyzed"]
    errors = summary["errorCount"]

    if analysed < MIN_FILES_ANALYSED:
        failures.append(
            f"the gate analysed {analysed} files, under the {MIN_FILES_ANALYSED} "
            "floor — the `include` globs have almost certainly stopped matching. "
            "A gate that sees nothing reports zero errors and looks clean; that "
            "is the exact failure this floor exists to catch (ADR-0213)."
        )
    else:
        print(f"OK  gate: {analysed} files analysed (floor {MIN_FILES_ANALYSED})")

    if errors:
        failures.append(f"{errors} type error(s) in product code")
        for diagnostic in report["generalDiagnostics"]:
            if diagnostic.get("severity") != "error":
                continue
            path = diagnostic["file"].replace(f"{REPO_ROOT}/", "")
            line = diagnostic["range"]["start"]["line"] + 1
            message = " ".join(diagnostic["message"].split())
            rule = diagnostic.get("rule", "-")
            print(f"  [{rule}] {path}:{line}\n      {message}", file=sys.stderr)
    else:
        print(f"OK  gate: 0 errors across {analysed} files")

    # --- The spike: each negative assertion must still FIRE -------------------
    # Not "the spike reports some errors": deleting all three inverse cases still
    # left two unrelated errors in the file, so a bare count > 0 passed against a
    # spike that no longer asserted anything. Each `# expect:` line is checked on
    # its own line number instead.
    expected_lines = {
        number: text.split("# expect:", 1)[1].strip()
        for number, text in enumerate(
            SPIKE_SOURCE.read_text(encoding="utf-8").splitlines(), start=1
        )
        if "# expect:" in text
    }
    if len(expected_lines) < 3:
        failures.append(
            f"the narrowing spike carries {len(expected_lines)} `# expect:` "
            "assertions, fewer than the 3 it is supposed to make — someone "
            "deleted the inverse cases instead of leaving them failing"
        )

    spike = _run_pyright(["--project", str(SPIKE_CONFIG)])
    spike_analysed = spike["summary"]["filesAnalyzed"]
    if spike_analysed != 1:
        failures.append(
            f"the narrowing spike analysed {spike_analysed} files, expected 1 — "
            "the check did not run, so its verdict means nothing. (Naming the "
            "file on pyright's command line does exactly this: the repo config's "
            "`exclude` applies to command-line paths too.)"
        )
    else:
        erroring = {
            diagnostic["range"]["start"]["line"] + 1
            for diagnostic in spike["generalDiagnostics"]
            if diagnostic.get("severity") == "error"
        }
        silent = sorted(set(expected_lines) - erroring)
        if silent:
            failures.append(
                "the narrowing spike stopped reporting an error on "
                + ", ".join(
                    f"line {number} ({expected_lines[number]})" for number in silent
                )
                + ". These are deliberate NEGATIVE assertions: silence means "
                "`ExtensionAPI.on`'s overloads have STOPPED narrowing — a "
                "regression in the hook API's type safety, not an improvement. "
                "Do not 'fix' the spike."
            )
        else:
            print(
                f"OK  spike: all {len(expected_lines)} inverse assertions still "
                "error (narrowing alive)"
            )

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("\nPASS: type gate clean, narrowing spike still inverted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
