#!/usr/bin/env python3
"""#310 — ``--fix`` cannot know a block was "edited in place", so it may not say so.

    python .omc/specs/310-overwrite.py

``cmd_lock`` reports every anchor it rewrote under a key the lock already held,
and an earlier revision of it called that list "anchors whose block was edited
in place", on the reasoning that under ``--fix`` the list is empty by
construction. Both halves are false, and this is the ten-line counterexample.

WHAT IT BUILDS. One target file with two cited blocks, ``X`` at line 3 and ``Y``
at line 6, and one citing file naming both. Lock it. Then push both blocks down
by three lines and run ``--fix``. Both relocate exactly — nothing is edited
anywhere — but ``X`` arrives at line 6, which is the key ``Y`` used to hold, so
the key changes hands and lands in the "replaced" list.

MEASURED 2026-09-22, against the script as it stood before this fix:

    before: {'...mod.py:3-3': ['XXX_the_x_block'], '...mod.py:6-6': ['YYY_the_y_block']}
    fix rc: 0
    after : {'...mod.py:6-6': ['XXX_the_x_block'], '...mod.py:9-9': ['YYY_the_y_block']}
    'OVERWROTE' printed by --fix? True
       OVERWROTE 1 anchor(s) whose block was edited in place:
         packages/p/src/m/mod.py:6-6
             was: YYY_the_y_block

The lock it wrote is CORRECT — every citation is pinned to its own text. Only
the sentence about it was wrong, and it additionally told the next reader to go
looking for "a bug in ``keep``" that does not exist. That is the same class of
defect ADR-0248 §2 forbids, one level down: diagnostic rather than data.

The fix prints the report for ``--lock`` only, where an overwrite is a human
decision, and states just what it knows — the range was pinned to other text.
Under ``--fix`` a genuine in-place edit cannot reach the report at all: it fails
to relocate and leaves through ``keep``, which is the whole of #310. Expected
output after the fix is ``'OVERWROTE' printed by --fix? False`` with the same
two lock entries, and a ``--lock`` on an edited-in-place block still printing.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent.parent
TARGET = "packages/p/src/m/mod.py"
CITING = "tests/probe.py"


def load_script(repo: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_cc", REPO / "scripts" / "check_citations.py")
    assert spec and spec.loader
    cc = importlib.util.module_from_spec(spec)
    sys.modules["_cc"] = cc
    spec.loader.exec_module(cc)
    cc.REPO = repo
    cc.LOCK_PATH = repo / "citations.lock.json"
    cc._tracked_files = lambda: [TARGET, CITING]
    return cc


def main() -> int:
    # Built rather than written out, so this file's own prose carries no
    # `<stem>.py:NNN` the citation scanner would read as a live pointer.
    stem = "mod" + ".py"
    with tempfile.TemporaryDirectory(prefix="cit-over-") as tmp:
        root = Path(tmp)
        cc = load_script(root)
        for rel in (TARGET, CITING):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / TARGET).write_text("a\nb\nXXX_the_x_block\nc\nd\nYYY_the_y_block\ne\n")
        (root / CITING).write_text(f"# see {stem}:3 and {stem}:6 for it\n")

        print("lock rc:", cc.cmd_lock(quiet=True))
        print(" before:", cc.load_lock())

        # Three lines in front of each block. X's new home is Y's old key.
        (root / TARGET).write_text(
            "a\nb\nc\nd\ne\nXXX_the_x_block\nf\ng\nYYY_the_y_block\nh\n"
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cc.cmd_fix()
        out = buf.getvalue()

        print(" fix rc:", rc)
        print(" after :", cc.load_lock())
        print(" citing prose now:", (root / CITING).read_text().strip())
        print(" 'OVERWROTE' printed by --fix?", "OVERWROTE" in out)
        for line in out.splitlines():
            if "OVERWROTE" in line or "was:" in line:
                print("   " + line.strip())

        # Control: the case the report is FOR. Edit Y where it stands and use
        # the manual verb. This one must still speak.
        (root / TARGET).write_text(
            "a\nb\nc\nd\ne\nXXX_the_x_block\nf\ng\nYYY_edited_in_place\nh\n"
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cc.cmd_lock()
        lock_out = buf.getvalue()
        print(" control — 'OVERWROTE' printed by --lock?", "OVERWROTE" in lock_out)
        for line in lock_out.splitlines():
            if "OVERWROTE" in line or "was:" in line:
                print("   " + line.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
