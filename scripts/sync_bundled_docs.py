#!/usr/bin/env python3
"""Copy ``docs/guides/*.md`` into the wheel-bundled ``aelix_coding_agent/docs/``.

#101 ships the user guides inside the wheel so an installed user can read them
with no network and no checkout (``aelix docs``). The bundled copies are
COMMITTED, not generated at build time — hatchling's ``packages`` selector is a
file-tree selector, so a file has to be on disk to ship.

Run this after editing anything in ``docs/guides/``.
``tests/test_docs_bundle_sync.py`` fails if you forget, in both directions:
a guide with no bundled copy, and a bundled copy with no guide.

Deleting is part of syncing. A renamed guide leaves an ORPHAN behind, and an
orphan is worse than a stale file — the ``aelix docs`` topic list is derived
from this directory, so the product keeps advertising it.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUIDES = REPO / "docs" / "guides"
BUNDLED = (
    REPO
    / "packages"
    / "aelix-coding-agent"
    / "src"
    / "aelix_coding_agent"
    / "docs"
)


def main() -> int:
    if not GUIDES.is_dir():
        print(f"error: {GUIDES} not found", file=sys.stderr)
        return 1

    BUNDLED.mkdir(parents=True, exist_ok=True)
    wanted = {p.name for p in GUIDES.glob("*.md")}

    copied = 0
    for src in sorted(GUIDES.glob("*.md")):
        dst = BUNDLED / src.name
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dst)
            copied += 1
            print(f"  copied {src.name}")

    removed = 0
    for stale in sorted(BUNDLED.glob("*.md")):
        if stale.name not in wanted:
            stale.unlink()
            removed += 1
            print(f"  removed orphan {stale.name}")

    print(f"{len(wanted)} guide(s) bundled — {copied} updated, {removed} removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
