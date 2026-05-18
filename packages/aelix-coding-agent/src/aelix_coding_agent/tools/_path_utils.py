"""Path resolution helpers (Pi parity ``core/tools/path-utils.ts``)."""

from __future__ import annotations

from pathlib import Path


def resolve_to_cwd(path: str, cwd: str) -> str:
    """Resolve ``path`` relative to ``cwd`` and return absolute.

    Pi parity ``resolveToCwd`` (``core/tools/path-utils.ts``): absolute paths
    are honored verbatim; relatives are joined against cwd.
    """

    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(Path(cwd) / p)


def resolve_read_path(path: str, cwd: str) -> str:
    """Resolve a path for read-style operations.

    Pi parity ``getReadmePath`` mirror — tolerates absolute paths inside cwd
    or under ``~/.aelix`` config dir; otherwise joins relative to cwd.
    """

    p = Path(path).expanduser()
    if p.is_absolute():
        return str(p)
    return str(Path(cwd) / p)


def is_within(child: str, parent: str) -> bool:
    """True when ``child`` resolves inside ``parent`` (no traversal escape)."""

    try:
        c = Path(child).resolve()
        p = Path(parent).resolve()
        c.relative_to(p)
        return True
    except (ValueError, OSError):
        return False


__all__ = ["is_within", "resolve_read_path", "resolve_to_cwd"]
