"""Pi parity: ``cli/file-processor.ts`` text-only port.

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-387). Image branch DEFERRED to
Sprint 5a-iii (image-resize utility port). When an image extension is
detected, this module emits a warning to stderr and skips the file.

Pi citation: ``cli/file-processor.ts:1-100`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import expand_tilde_path

# Pi parity: ``cli/file-processor.ts`` image-extension probe set.
# When the file's suffix matches, Pi reads the bytes as base64 +
# optionally resizes via the image-resize utility. Aelix defers that
# branch (Sprint 5a-iii) — see ADR-0089 §"Carry-forward".
_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp"}
)


@dataclass
class ProcessedFiles:
    """Pi parity: ``{text, images}`` return shape.

    :attr:`text` is the concatenated ``<file name="...">...</file>``
    wrapped contents of every text file argument.

    :attr:`images` is reserved for the deferred image branch — always
    empty in Sprint 6h₆ (text-only).
    """

    text: str = ""
    images: list[Any] = field(default_factory=list)


async def process_file_arguments(
    file_args: list[str],
    *,
    cwd: str | None = None,
) -> ProcessedFiles:
    """Pi parity: ``processFileArguments`` (``cli/file-processor.ts``).

    Per-arg flow:

    1. :func:`expand_tilde_path` on the raw argument.
    2. Resolve relative paths against ``cwd`` (default :func:`Path.cwd`).
    3. Existence check — missing files print a Pi-shape error and
       :func:`sys.exit` with code 1 (Pi parity — Pi raises and the
       caller terminates).
    4. Empty-file probe — skip zero-byte files (Pi parity).
    5. Image-extension probe — Pi base64+resize branch DEFERRED;
       Aelix prints a warning to stderr and skips the file.
    6. Text branch — read as UTF-8 and wrap in Pi's
       ``<file name="...">...</file>`` block, appended to
       :attr:`ProcessedFiles.text`.
    """

    result = ProcessedFiles()
    cwd_path = Path(cwd) if cwd else Path.cwd()

    for file_arg in file_args:
        expanded = expand_tilde_path(file_arg)
        candidate = Path(expanded)
        path = candidate if candidate.is_absolute() else (cwd_path / candidate).resolve()

        if not path.exists():
            # Pi parity: missing file is a hard error (Pi throws and the
            # caller terminates). Aelix prints the diagnostic and exits.
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)

        try:
            stat = path.stat()
        except OSError as exc:
            print(f"Error reading {path}: {exc}", file=sys.stderr)
            sys.exit(1)

        if stat.st_size == 0:
            # Pi parity: zero-byte files are skipped silently.
            continue

        suffix = path.suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            # Image branch DEFERRED to Sprint 5a-iii — emit Pi-shape
            # warning and skip.
            print(
                f"Warning: image file {path} skipped "
                "(Sprint 6h₆ text-only — image branch deferred)",
                file=sys.stderr,
            )
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Error reading {path}: {exc}", file=sys.stderr)
            sys.exit(1)

        # Pi parity: wrap text content in ``<file name="...">...</file>``.
        result.text += f'<file name="{path.name}">\n{content}\n</file>\n'

    return result


__all__ = ["ProcessedFiles", "process_file_arguments"]
