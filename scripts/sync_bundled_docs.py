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

THE GUIDE DIRECTORY IS FLAT, AND THAT IS ENFORCED HERE (#101 review L1). Every
glob in this pipeline is ``*.md`` rather than ``**/*.md``, so a guide written to
``docs/guides/advanced/x.md`` used to be skipped in complete silence — measured
on this tree with one planted nested file: ``tests/test_docs_bundle_sync.py``
7 passed, this script printed ``8 guide(s) bundled — 0 updated, 0 removed``,
and ``aelix docs`` offered the same 8 topics as before. Nothing anywhere said
the ninth file existed.

The close is a REFUSAL rather than a recursive glob, and the reason is that a
recursive glob would move the silence rather than remove it:

* the topic namespace is FLAT. ``help.registry.topics`` globs ``*.md`` too and
  names each topic by ``Path.stem``, so a recursively bundled ``advanced/x.md``
  would ship in the wheel and still never be offered as a topic — a second
  silent failure one layer along;
* flattening instead (``advanced/x.md`` -> ``x.md``) makes two guides with the
  same stem overwrite each other, silently, in a directory the product reads;
* every link test in ``tests/`` resolves guide links against one directory, so
  nesting would need a matching change in four more places to stay honest.

If nested guides are ever wanted, the change is a real one — a topic naming
scheme — not a glob character.
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


def nested_guides(guides_dir: Path) -> list[Path]:
    """Every ``.md`` under ``guides_dir`` that is NOT at its top level.

    Shared with ``tests/test_docs_bundle_sync.py`` so the refusal and the guard
    cannot disagree about what counts as nested. Returns paths relative to
    ``guides_dir``, sorted, so a failure message names the files.
    """

    if not guides_dir.is_dir():
        return []
    top_level = {p.resolve() for p in guides_dir.glob("*.md")}
    return sorted(
        p.relative_to(guides_dir)
        for p in guides_dir.rglob("*.md")
        if p.resolve() not in top_level
    )


def main() -> int:
    if not GUIDES.is_dir():
        print(f"error: {GUIDES} not found", file=sys.stderr)
        return 1

    nested = nested_guides(GUIDES)
    if nested:
        print(
            "error: docs/guides/ must be flat — these would never ship, never "
            "become an `aelix docs` topic, and never fail a test:\n  "
            + "\n  ".join(str(p) for p in nested)
            + "\nMove them to docs/guides/<name>.md, or out of docs/guides/.",
            file=sys.stderr,
        )
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
