#!/usr/bin/env python3
"""Mirror ``.agents/skills/`` into ``.claude/skills/``, byte for byte (#733, ADR-0303).

The project's agent skills live once, in ``.agents/skills/<name>/``: aelix reads
that directory itself in a trusted project (#509, ADR-0281), and so do other
tools that follow the Agent Skills layout. Claude Code reads
``.claude/skills/<name>/`` instead, so that tree is a COMMITTED COPY, kept equal
the way ``scripts/sync_bundled_docs.py`` keeps the bundled guides equal
(ADR-0218): edit the canonical tree, run this, commit both.

    uv run python scripts/sync_agent_skills.py           # write the mirror
    uv run python scripts/sync_agent_skills.py --check   # exit 1 on any drift

Deleting is part of syncing. A skill renamed or removed in ``.agents/skills``
leaves its old copy in the mirror, and Claude Code would go on offering it, so a
file in the mirror with no canonical counterpart is removed (and named), and so
is a directory that ends up empty. That makes ``.claude/skills/`` no place for
a personal skill: those belong in ``~/.claude/skills``.

Operating-system debris (``.DS_Store``, ``Thumbs.db``, ``__pycache__``) is
neither copied nor compared. A symbolic link in either tree is refused rather
than followed: a checkout without symlink support turns it into a text file,
and the two trees would then disagree by platform.

``tests/test_agent_skills.py`` runs :func:`drift` over the real trees and checks
each skill's frontmatter, that aelix's own loader takes every skill without a
diagnostic, and that no file in either tree holds a home path, a key or a line
citation. Standard library only.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANONICAL = REPO / ".agents" / "skills"
MIRROR = REPO / ".claude" / "skills"

#: Never copied, never compared: what editors and file managers leave behind.
JUNK_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__"})

_RESYNC = "uv run python scripts/sync_agent_skills.py"


class SkillTreeError(Exception):
    """A tree this script will not mirror (a symlink, or a mirror root that is not a directory)."""


class Drift:
    """How the mirror differs from the canonical tree, as POSIX paths relative to each root.

    A plain class rather than a dataclass: the tests load this file by path,
    and ``@dataclass`` needs the module registered in ``sys.modules``.
    """

    def __init__(self) -> None:
        self.missing: list[str] = []
        self.differs: list[str] = []
        self.extra: list[str] = []

    def __bool__(self) -> bool:
        return bool(self.missing or self.differs or self.extra)

    def report(self) -> str:
        lines = []
        lines += [f"  missing from the mirror: {p}" for p in self.missing]
        lines += [f"  differs from the canonical copy: {p}" for p in self.differs]
        lines += [f"  in the mirror only: {p}" for p in self.extra]
        return "\n".join(lines)


def _is_junk(rel: Path) -> bool:
    return any(part in JUNK_NAMES for part in rel.parts) or rel.suffix in {".pyc", ".pyo"}


def tree_files(root: Path) -> dict[str, Path]:
    """Every file under *root* that is mirrored, keyed by its POSIX path relative to *root*."""

    if not root.exists():
        return {}
    if root.is_symlink() or not root.is_dir():
        raise SkillTreeError(f"{root} is not a plain directory")
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if _is_junk(rel):
            continue
        if path.is_symlink():
            raise SkillTreeError(f"{path} is a symbolic link; skills are mirrored as plain files")
        if path.is_file():
            files[rel.as_posix()] = path
    return files


def drift(canonical: Path = CANONICAL, mirror: Path = MIRROR) -> Drift:
    """Compare the two trees in both directions, by bytes."""

    want = tree_files(canonical)
    have = tree_files(mirror)
    result = Drift()
    for rel, src in want.items():
        dst = have.get(rel)
        if dst is None:
            result.missing.append(rel)
        elif dst.read_bytes() != src.read_bytes():
            result.differs.append(rel)
    result.extra = sorted(rel for rel in have if rel not in want)
    return result


def _prune_empty_dirs(mirror: Path) -> None:
    """Remove every directory under *mirror* left empty, deepest first, so a parent emptied by its children goes too."""

    for directory in sorted((p for p in mirror.rglob("*") if p.is_dir() and not p.is_symlink()), reverse=True):
        if not _is_junk(directory.relative_to(mirror)) and not any(directory.iterdir()):
            directory.rmdir()


def sync(canonical: Path = CANONICAL, mirror: Path = MIRROR) -> Drift:
    """Make *mirror* equal *canonical*; return what it changed.

    Stale files go before anything is copied, so a path that changed between
    file and directory is reshaped in either direction: a stale file where a
    directory must go is already gone, and a directory where a file must go
    holds only debris by then and is removed.
    """

    found = drift(canonical, mirror)
    want = tree_files(canonical)
    for rel in found.extra:
        (mirror / rel).unlink()
    for rel in found.missing + found.differs:
        dst = mirror / rel
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(want[rel], dst)
    if mirror.is_dir():
        _prune_empty_dirs(mirror)
        if not canonical.exists() and not any(mirror.iterdir()):
            mirror.rmdir()
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror .agents/skills into .claude/skills.")
    parser.add_argument("--check", action="store_true", help="report drift and exit 1; change nothing")
    args = parser.parse_args(argv)
    try:
        if args.check:
            found = drift(CANONICAL, MIRROR)
            if found:
                print(
                    ".claude/skills/ has drifted from .agents/skills/:\n"
                    f"{found.report()}\nFix: {_RESYNC}",
                    file=sys.stderr,
                )
                return 1
            print(f".claude/skills/ matches .agents/skills/ ({len(tree_files(CANONICAL))} files).")
            return 0
        changed = sync(CANONICAL, MIRROR)
    except SkillTreeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for rel in changed.missing + changed.differs:
        print(f"  copied {rel}")
    for rel in changed.extra:
        print(f"  removed {rel}")
    print(
        f"{len(tree_files(CANONICAL))} file(s) mirrored — "
        f"{len(changed.missing) + len(changed.differs)} copied, {len(changed.extra)} removed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
