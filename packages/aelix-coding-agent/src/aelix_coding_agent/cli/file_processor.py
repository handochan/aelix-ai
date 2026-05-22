"""Pi parity: ``cli/file-processor.ts`` port (text + image branches).

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-387) shipped the text-only port
with the image branch DEFERRED. Sprint 6h₈ (Phase 5a-iv, ADR-0092, §B)
wires the real image branch: magic-byte detection via
:func:`image_detect.detect_image_mime_type_from_file` + optional
in-process resize via :func:`image_resize.resize_image` + Pi-shape
``<file name="…">[dimension note]</file>`` text wrapping.

Pi citation: ``cli/file-processor.ts:1-100`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aelix_ai.messages import ImageContent

from ..util.image_detect import detect_image_mime_type_from_file
from ..util.image_resize import format_dimension_note, resize_image
from .config import expand_tilde_path

# Pi parity: ``cli/file-processor.ts`` image-extension probe set. Kept
# as a fast-path optimisation — Sprint 6h₈ wires real magic-byte
# detection via ``image_detect`` for files that pass this filter as
# well as for files that do not match (Pi parity: magic bytes win).
_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp"}
)


@dataclass
class ProcessedFiles:
    """Pi parity: ``{text, images}`` return shape.

    :attr:`text` is the concatenated ``<file name="...">...</file>``
    wrapped contents of every file argument (text + image references).

    :attr:`images` carries the decoded + optionally-resized
    :class:`ImageContent` blocks for each image arg.
    """

    text: str = ""
    images: list[Any] = field(default_factory=list)


async def process_file_arguments(
    file_args: list[str],
    *,
    cwd: str | None = None,
    auto_resize_images: bool = True,
) -> ProcessedFiles:
    """Pi parity: ``processFileArguments`` (``cli/file-processor.ts``).

    Per-arg flow:

    1. :func:`expand_tilde_path` on the raw argument.
    2. Resolve relative paths against ``cwd`` (default :func:`Path.cwd`).
    3. Existence check — missing files print a Pi-shape error and
       :func:`sys.exit` with code 1 (Pi parity).
    4. Empty-file probe — skip zero-byte files (Pi parity).
    5. Magic-byte image probe (Sprint 6h₈, Pi parity): call
       :func:`image_detect.detect_image_mime_type_from_file`. If a MIME
       type is returned, read full bytes, base64-encode, optionally
       resize via :func:`image_resize.resize_image`, then append the
       :class:`ImageContent` to :attr:`ProcessedFiles.images` and emit
       a ``<file name="…">[dimension note]</file>`` reference into the
       text stream.
    6. Text branch — read as UTF-8 and wrap in
       ``<file name="...">...</file>``.

    Parameters
    ----------
    file_args:
        Raw CLI ``@file`` arguments (``@`` already stripped by the
        parser).
    cwd:
        Override cwd for relative-path resolution. Defaults to
        :func:`Path.cwd`.
    auto_resize_images:
        Whether to auto-resize images to fit Pi's 2000×2000 / 4.5 MB
        defaults. Pi default is :data:`True`; the CLI layer threads the
        ``SettingsManager`` ``images.auto_resize`` getter here in
        Sprint 6h₈+. When :data:`False`, images are forwarded as-is.
    """

    result = ProcessedFiles()
    cwd_path = Path(cwd) if cwd else Path.cwd()

    for file_arg in file_args:
        expanded = expand_tilde_path(file_arg)
        candidate = Path(expanded)
        path = candidate if candidate.is_absolute() else (cwd_path / candidate).resolve()

        if not path.exists():
            # Pi parity: missing file is a hard error.
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

        # Sprint 6h₈: magic-byte image detection (Pi parity). The
        # extension-only fast-path stays as a quick filter, but magic
        # bytes are the source of truth — files with image extensions
        # whose bytes disagree fall through to the text branch (Pi
        # parity per ``file-processor.ts:48-50``).
        mime_type = await detect_image_mime_type_from_file(path)

        if mime_type is not None:
            # === Image branch =============================================
            try:
                raw_bytes = path.read_bytes()
            except OSError as exc:
                print(f"Error reading {path}: {exc}", file=sys.stderr)
                sys.exit(1)
            base64_content = base64.b64encode(raw_bytes).decode("ascii")

            dimension_note: str | None = None

            if auto_resize_images:
                original = ImageContent(
                    mime_type=mime_type, data=base64_content
                )
                resized = await resize_image(original)
                if resized is None:
                    # Pi parity (``file-processor.ts:60-62``): emit the
                    # placeholder text reference and skip the image.
                    # Aelix-additive divergence: uses ``path.name``
                    # (basename) to match the Sprint 6h₆ text branch
                    # convention (Pi uses ``absolutePath``).
                    result.text += (
                        f'<file name="{path.name}">'
                        "[Image omitted: could not be resized below the "
                        "inline image size limit.]</file>\n"
                    )
                    continue
                # Pi parity: ``formatDimensionNote`` returns ``undefined``
                # when ``wasResized=False``; Aelix's
                # :func:`format_dimension_note` returns :data:`None` in
                # the same case.
                dimension_note = format_dimension_note(resized)
                attachment = ImageContent(
                    mime_type=resized.mime_type,
                    data=resized.data,
                )
            else:
                attachment = ImageContent(
                    mime_type=mime_type, data=base64_content
                )

            result.images.append(attachment)

            # Pi parity (``file-processor.ts:67-73``): emit dimension
            # note (or empty body) inside a ``<file name="…">`` block.
            # Aelix-additive divergence: uses ``path.name`` (basename)
            # to match the Sprint 6h₆ text branch convention (Pi uses
            # ``absolutePath``).
            if dimension_note is not None:
                result.text += f'<file name="{path.name}">{dimension_note}</file>\n'
            else:
                result.text += f'<file name="{path.name}"></file>\n'
            continue

        # Pi parity: when magic-byte detection returns None we fall
        # through to the text branch unconditionally. W4 MINOR fold-in:
        # removed a residual ``_ = path.suffix.lower() in _IMAGE_EXTENSIONS``
        # no-op expression that suggested an extension fast-path which
        # the implementation never wired (`_IMAGE_EXTENSIONS` is kept
        # for documentation / future fast-path reinstatement only).

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Error reading {path}: {exc}", file=sys.stderr)
            sys.exit(1)

        # Pi parity: wrap text content in ``<file name="...">...</file>``.
        result.text += f'<file name="{path.name}">\n{content}\n</file>\n'

    return result


__all__ = ["ProcessedFiles", "process_file_arguments"]
