"""Path resolution helpers (Pi parity ``core/tools/path-utils.ts``)."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

# Pi parity ``expandPath`` special-space class: NBSP, en/em spaces, narrow/
# medium math spaces, ideographic space — all collapsed to ASCII space.
_UNICODE_SPACES = re.compile("[  -   　]")


def expand_path(path: str) -> str:
    """Pi parity ``expandPath`` (``path-utils.ts``).

    NFC-normalizes, collapses special unicode spaces to ASCII, strips a single
    leading ``@`` (model file-mention artifact), and expands a LEADING ``~`` /
    ``~/`` to the home dir (NOT ``~user`` — pi only expands the bare leading
    tilde). Does not resolve against cwd; callers do that.
    """

    if not path:
        return path
    p = unicodedata.normalize("NFC", path)
    p = _UNICODE_SPACES.sub(" ", p)
    if p.startswith("@"):
        p = p[1:]
    if p == "~":
        return os.path.expanduser("~")
    if p.startswith("~/"):
        return os.path.join(os.path.expanduser("~"), p[2:])
    return p


def resolve_to_cwd(path: str, cwd: str) -> str:
    """Resolve ``path`` relative to ``cwd`` and return absolute.

    Pi parity ``resolveToCwd`` (``core/tools/path-utils.ts``): runs
    :func:`expand_path` first, then honors absolute paths verbatim and joins
    relatives against cwd.
    """

    p = Path(expand_path(path))
    if p.is_absolute():
        return str(p)
    return str(Path(cwd) / p)


def relativize_to_posix(file_path: str, base: str) -> str:
    """Pi parity ``formatPath`` — path relative to ``base`` with POSIX
    separators; basename fallback when ``file_path`` is outside ``base``.
    """

    try:
        rel = os.path.relpath(file_path, base)
    except ValueError:  # different drives on Windows
        return os.path.basename(file_path)
    if rel and not rel.startswith(".."):
        return rel.replace(os.sep, "/")
    return os.path.basename(file_path)


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


__all__ = [
    "expand_path",
    "is_within",
    "relativize_to_posix",
    "resolve_read_path",
    "resolve_to_cwd",
]
