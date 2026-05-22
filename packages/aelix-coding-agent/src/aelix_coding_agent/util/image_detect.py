"""Magic-byte image MIME detection (Pi parity ``utils/mime.ts``).

Sprint 6h₈ (Phase 5a-iv, ADR-0092, P-435). Port of Pi
``packages/coding-agent/src/utils/mime.ts`` (74 LOC). Reads the first
4100 bytes of a file (sufficient for PNG ``acTL`` chunk scan) and
dispatches via magic-byte signatures to one of four supported MIME
types — JPEG, PNG, GIF, WebP — or :data:`None` when the file is not
a supported image format.

Pi citation: ``utils/mime.ts:1-74`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

# Pi parity: ``IMAGE_TYPE_SNIFF_BYTES`` (``mime.ts:3``).
_IMAGE_TYPE_SNIFF_BYTES: int = 4100

# Pi parity: ``PNG_SIGNATURE`` (``mime.ts:4``).
_PNG_SIGNATURE: bytes = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))


def _starts_with(buffer: bytes, prefix: bytes) -> bool:
    """Pi ``startsWith`` (``mime.ts:64-67``)."""

    if len(buffer) < len(prefix):
        return False
    return buffer[: len(prefix)] == prefix


def _starts_with_ascii(buffer: bytes, offset: int, text: str) -> bool:
    """Pi ``startsWithAscii`` (``mime.ts:69-74``)."""

    if len(buffer) < offset + len(text):
        return False
    return all(buffer[offset + index] == ord(ch) for index, ch in enumerate(text))


def _read_uint32_be(buffer: bytes, offset: int) -> int:
    """Pi ``readUint32BE`` (``mime.ts:56-62``).

    Reads a 32-bit big-endian unsigned integer at ``offset``. Out-of-range
    bytes are treated as ``0`` (Pi parity — Pi defaults missing bytes via
    ``buffer[i] ?? 0``).
    """

    def _at(i: int) -> int:
        if 0 <= i < len(buffer):
            return buffer[i]
        return 0

    return (
        (_at(offset) << 24)
        + (_at(offset + 1) << 16)
        + (_at(offset + 2) << 8)
        + _at(offset + 3)
    )


def _is_png(buffer: bytes) -> bool:
    """Pi ``isPng`` (``mime.ts:35-39``).

    Requires the 8-byte PNG signature followed by an IHDR chunk header
    with length 13.
    """

    return (
        len(buffer) >= 16
        and _read_uint32_be(buffer, len(_PNG_SIGNATURE)) == 13
        and _starts_with_ascii(buffer, 12, "IHDR")
    )


def _is_animated_png(buffer: bytes) -> bool:
    """Pi ``isAnimatedPng`` (``mime.ts:41-54``).

    Walks PNG chunks starting after the 8-byte signature. If an ``acTL``
    chunk is encountered before the first ``IDAT`` chunk, the file is an
    animated PNG (APNG). Otherwise returns :data:`False`.
    """

    offset = len(_PNG_SIGNATURE)
    while offset + 8 <= len(buffer):
        chunk_length = _read_uint32_be(buffer, offset)
        chunk_type_offset = offset + 4
        if _starts_with_ascii(buffer, chunk_type_offset, "acTL"):
            return True
        if _starts_with_ascii(buffer, chunk_type_offset, "IDAT"):
            return False
        next_offset = offset + 8 + chunk_length + 4
        if next_offset <= offset or next_offset > len(buffer):
            return False
        offset = next_offset
    return False


def detect_image_mime_type(buffer: bytes) -> str | None:
    """Pi ``detectSupportedImageMimeType`` (``mime.ts:6-21``).

    Dispatches on magic-byte signatures. Recognises JPEG / PNG / GIF /
    WebP only. Returns the canonical MIME type string (e.g.
    ``"image/jpeg"``) or :data:`None` when the buffer does not match any
    supported signature.

    - JPEG: ``FF D8 FF`` first 3 bytes; reject when 4th byte == ``0xF7``
      (truncated JPEG variant Pi rejects via ``mime.ts:8``).
    - PNG: 8-byte signature ``89 50 4E 47 0D 0A 1A 0A`` AND IHDR chunk
      length == 13 AND NOT animated APNG (``acTL`` chunk absent before
      ``IDAT``).
    - GIF: ASCII ``GIF`` at offset 0 (Pi ``mime.ts:13``).
    - WebP: ASCII ``RIFF`` at offset 0 + ASCII ``WEBP`` at offset 8.
    """

    if _starts_with(buffer, bytes((0xFF, 0xD8, 0xFF))):
        # Pi parity: ``mime.ts:8`` rejects truncated JPEG variant when
        # the 4th byte equals ``0xF7``.
        return None if (len(buffer) >= 4 and buffer[3] == 0xF7) else "image/jpeg"
    if _starts_with(buffer, _PNG_SIGNATURE):
        return (
            "image/png"
            if (_is_png(buffer) and not _is_animated_png(buffer))
            else None
        )
    if _starts_with_ascii(buffer, 0, "GIF"):
        return "image/gif"
    if _starts_with_ascii(buffer, 0, "RIFF") and _starts_with_ascii(
        buffer, 8, "WEBP"
    ):
        return "image/webp"
    return None


async def detect_image_mime_type_from_file(path: str | Path) -> str | None:
    """Pi ``detectSupportedImageMimeTypeFromFile`` (``mime.ts:23-32``).

    Reads up to the first ``IMAGE_TYPE_SNIFF_BYTES`` (4100) bytes from
    the file at ``path`` and dispatches via :func:`detect_image_mime_type`.
    Returns :data:`None` on any I/O error (Pi raises; Aelix's
    file-processor caller does the existence check ahead of time so a
    swallow-and-return-``None`` is the most useful boundary here).

    Blocking I/O runs via :func:`asyncio.to_thread`.
    """

    def _read_head() -> bytes:
        with open(path, "rb") as f:
            return f.read(_IMAGE_TYPE_SNIFF_BYTES)

    try:
        head = await asyncio.to_thread(_read_head)
    except OSError:
        return None
    return detect_image_mime_type(head)


__all__ = [
    "detect_image_mime_type",
    "detect_image_mime_type_from_file",
]
