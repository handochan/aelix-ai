"""Sprint 6h₈ (Phase 5a-iv, ADR-0092) — ``image_detect.py`` tests.

Magic-byte detection tests covering all 4 supported MIME types (JPEG /
PNG / GIF / WebP) + animated PNG rejection + truncated JPEG rejection
+ non-image returns ``None``. Uses Pillow to generate small test images
into ``tmp_path`` so the magic-byte signatures match real on-disk files.

Pi citation: ``utils/mime.ts:1-74`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import io
from pathlib import Path

from aelix_coding_agent.util.image_detect import (
    detect_image_mime_type,
    detect_image_mime_type_from_file,
)
from PIL import Image

# === Buffer-level magic-byte detection ======================================


def test_jpeg_signature_detected() -> None:
    """JPEG ``FF D8 FF E0`` is recognised."""

    buf = bytes((0xFF, 0xD8, 0xFF, 0xE0)) + b"\x00" * 32
    assert detect_image_mime_type(buf) == "image/jpeg"


def test_jpeg_signature_truncated_variant_rejected() -> None:
    """Pi ``mime.ts:8`` rejects ``FF D8 FF F7`` (truncated JPEG variant)."""

    buf = bytes((0xFF, 0xD8, 0xFF, 0xF7)) + b"\x00" * 32
    assert detect_image_mime_type(buf) is None


def test_png_signature_detected() -> None:
    """8-byte PNG signature + valid IHDR is recognised."""

    # PNG sig + IHDR(length=13) + "IHDR" + 13 bytes data + ... (we just
    # need an IHDR chunk header for `_is_png` and no acTL before IDAT).
    sig = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
    ihdr_len = (13).to_bytes(4, "big")
    ihdr = b"IHDR" + b"\x00" * 13 + b"\x00\x00\x00\x00"  # 13 data + 4 CRC
    # Add an IDAT chunk after IHDR so animated-PNG walker terminates.
    idat_len = (0).to_bytes(4, "big")
    idat = b"IDAT" + b"\x00\x00\x00\x00"
    buf = sig + ihdr_len + ihdr + idat_len + idat
    assert detect_image_mime_type(buf) == "image/png"


def test_animated_png_rejected() -> None:
    """APNG (``acTL`` chunk present before ``IDAT``) is rejected."""

    sig = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
    ihdr_len = (13).to_bytes(4, "big")
    ihdr = b"IHDR" + b"\x00" * 13 + b"\x00\x00\x00\x00"
    # Insert acTL chunk between IHDR and IDAT.
    actl_len = (8).to_bytes(4, "big")
    actl = b"acTL" + b"\x00" * 8 + b"\x00\x00\x00\x00"
    idat_len = (0).to_bytes(4, "big")
    idat = b"IDAT" + b"\x00\x00\x00\x00"
    buf = sig + ihdr_len + ihdr + actl_len + actl + idat_len + idat
    assert detect_image_mime_type(buf) is None


def test_gif_signature_detected() -> None:
    """ASCII ``GIF`` at offset 0 is recognised."""

    buf = b"GIF89a" + b"\x00" * 32
    assert detect_image_mime_type(buf) == "image/gif"


def test_webp_signature_detected() -> None:
    """ASCII ``RIFF`` at offset 0 + ``WEBP`` at offset 8 is recognised."""

    buf = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
    assert detect_image_mime_type(buf) == "image/webp"


def test_webp_missing_webp_marker_rejected() -> None:
    """``RIFF`` without ``WEBP`` marker is not a WebP (could be other RIFF)."""

    buf = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 32
    assert detect_image_mime_type(buf) is None


def test_unknown_format_returns_none() -> None:
    """Random non-image bytes return :data:`None`."""

    assert detect_image_mime_type(b"some random bytes here, definitely not an image") is None


def test_empty_buffer_returns_none() -> None:
    """Empty buffer returns :data:`None` (no signature can match)."""

    assert detect_image_mime_type(b"") is None


def test_short_buffer_returns_none() -> None:
    """Buffers shorter than the shortest signature return :data:`None`."""

    assert detect_image_mime_type(b"\xff") is None


# === File-level detection via tmp_path + Pillow =============================


async def test_real_jpeg_file_detected(tmp_path: Path) -> None:
    """A real JPEG written by Pillow magic-byte-detects to ``image/jpeg``."""

    img = Image.new("RGB", (8, 8), color=(255, 0, 0))
    path = tmp_path / "test.jpg"
    img.save(path, format="JPEG")
    mime = await detect_image_mime_type_from_file(path)
    assert mime == "image/jpeg"


async def test_real_png_file_detected(tmp_path: Path) -> None:
    """A real PNG written by Pillow magic-byte-detects to ``image/png``."""

    img = Image.new("RGB", (8, 8), color=(0, 255, 0))
    path = tmp_path / "test.png"
    img.save(path, format="PNG")
    mime = await detect_image_mime_type_from_file(path)
    assert mime == "image/png"


async def test_real_gif_file_detected(tmp_path: Path) -> None:
    """A real GIF written by Pillow magic-byte-detects to ``image/gif``."""

    img = Image.new("P", (8, 8), color=0)
    path = tmp_path / "test.gif"
    img.save(path, format="GIF")
    mime = await detect_image_mime_type_from_file(path)
    assert mime == "image/gif"


async def test_real_webp_file_detected(tmp_path: Path) -> None:
    """A real WebP written by Pillow magic-byte-detects to ``image/webp``."""

    img = Image.new("RGB", (8, 8), color=(0, 0, 255))
    path = tmp_path / "test.webp"
    img.save(path, format="WEBP")
    mime = await detect_image_mime_type_from_file(path)
    assert mime == "image/webp"


async def test_text_file_returns_none(tmp_path: Path) -> None:
    """A plain text file returns :data:`None`."""

    path = tmp_path / "plain.txt"
    path.write_text("hello, world — definitely not an image")
    mime = await detect_image_mime_type_from_file(path)
    assert mime is None


async def test_missing_file_returns_none(tmp_path: Path) -> None:
    """A missing file path returns :data:`None` (OSError swallowed)."""

    mime = await detect_image_mime_type_from_file(tmp_path / "does_not_exist.png")
    assert mime is None


async def test_file_with_image_extension_but_text_contents(tmp_path: Path) -> None:
    """Magic bytes trump the file extension (per Pi parity)."""

    path = tmp_path / "fake.png"
    path.write_text("this is actually text content")
    mime = await detect_image_mime_type_from_file(path)
    assert mime is None


# === Buffer round-trip from real Pillow encode ==============================


def test_jpeg_pillow_roundtrip_buffer() -> None:
    """A Pillow-encoded JPEG buffer round-trips through buffer detection."""

    img = Image.new("RGB", (4, 4), color=(123, 45, 67))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    assert detect_image_mime_type(buf.getvalue()) == "image/jpeg"


def test_png_pillow_roundtrip_buffer() -> None:
    """A Pillow-encoded PNG buffer round-trips through buffer detection."""

    img = Image.new("RGB", (4, 4), color=(123, 45, 67))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    assert detect_image_mime_type(buf.getvalue()) == "image/png"
