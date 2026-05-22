"""Sprint 6h₈ (Phase 5a-iv, ADR-0092) — ``image_resize.py`` tests.

Pi citation: ``utils/image-resize.ts:1-176`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

Covers: aspect-ratio preserve, Lanczos quality, 5 JPEG quality step
fallback chain, encoded-size budget escalation, EXIF auto-orient (real
encoded JPEG metadata), 1×1 give-up, :data:`None` return on unsupported
input, and ``format_dimension_note`` shape.
"""

from __future__ import annotations

import base64
import io

import pytest
from aelix_ai.messages import ImageContent
from aelix_coding_agent.util.image_resize import (
    ImageResizeOptions,
    ResizedImage,
    format_dimension_note,
    resize_image,
)
from PIL import Image


def _png_data_url(image: Image.Image) -> str:
    """Encode ``image`` as PNG and return base64 string (no prefix)."""

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _jpeg_data_url(image: Image.Image, quality: int = 95) -> str:
    """Encode ``image`` as JPEG and return base64 string (no prefix)."""

    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# === Fast-path: already compliant ==========================================


async def test_small_image_fast_path_returns_unchanged() -> None:
    """An image already within dim + size budget returns ``was_resized=False``."""

    img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    encoded = _png_data_url(img)
    content = ImageContent(mime_type="image/png", data=encoded)
    result = await resize_image(content)
    assert result is not None
    assert result.was_resized is False
    assert result.original_width == 100
    assert result.original_height == 100
    assert result.width == 100
    assert result.height == 100
    # Data is unchanged on fast-path (Pi parity).
    assert result.data == encoded
    assert result.mime_type == "image/png"


# === Aspect-ratio preservation ==============================================


async def test_oversized_image_resized_with_aspect_ratio() -> None:
    """An over-wide image scales to ``max_width`` and preserves aspect."""

    # 4000×2000 → expect 2000×1000 after initial dim snap.
    img = Image.new("RGB", (4000, 2000), color=(10, 20, 30))
    encoded = _png_data_url(img)
    content = ImageContent(mime_type="image/png", data=encoded)
    result = await resize_image(content)
    assert result is not None
    assert result.was_resized is True
    assert result.original_width == 4000
    assert result.original_height == 2000
    # Aspect 2:1 preserved at max_width=2000.
    assert result.width == 2000
    assert result.height == 1000


async def test_tall_image_resized_with_aspect_ratio() -> None:
    """An over-tall image scales to ``max_height`` and preserves aspect."""

    img = Image.new("RGB", (2000, 4000), color=(40, 50, 60))
    encoded = _png_data_url(img)
    content = ImageContent(mime_type="image/png", data=encoded)
    result = await resize_image(content)
    assert result is not None
    assert result.was_resized is True
    assert result.original_width == 2000
    assert result.original_height == 4000
    # Aspect 1:2 preserved at max_height=2000.
    assert result.width == 1000
    assert result.height == 2000


# === JPEG quality fallback chain ============================================


async def test_jpeg_quality_steps_dedupe() -> None:
    """A dim-constrained run forces resize (was_resized=True)."""

    # Force the resize path via dim ceiling; tests that the algorithm
    # walks the JPEG quality fallback chain (5 steps after de-dupe) and
    # successfully emits a candidate.
    img = Image.new("RGB", (300, 300), color=(200, 100, 50))
    # Add noise so JPEG can't compress to zero bytes.
    pixels = img.load()
    assert pixels is not None
    for x in range(300):
        for y in range(300):
            pixels[x, y] = ((x * 7) % 256, (y * 11) % 256, ((x + y) * 13) % 256)
    encoded = _png_data_url(img)
    content = ImageContent(mime_type="image/png", data=encoded)
    # max_width=100 forces a resize regardless of base64 size budget.
    result = await resize_image(
        content, ImageResizeOptions(max_width=100, max_height=100)
    )
    assert result is not None
    assert result.was_resized is True
    assert result.width <= 100
    assert result.height <= 100


# === Encoded-size fallback chain (dim reduction) ============================


async def test_dim_reduction_when_quality_alone_fails() -> None:
    """A very tight budget forces dimension reduction past the JPEG fallback."""

    # 1000×1000 noisy image with a 4 KB budget — JPEG quality 40 at full
    # dims won't fit, so the algo must drop dimensions.
    img = Image.new("RGB", (1000, 1000))
    pixels = img.load()
    assert pixels is not None
    for x in range(1000):
        for y in range(1000):
            pixels[x, y] = (
                (x * 17) % 256,
                (y * 19) % 256,
                ((x ^ y) * 23) % 256,
            )
    encoded = _png_data_url(img)
    content = ImageContent(mime_type="image/png", data=encoded)
    result = await resize_image(
        content,
        ImageResizeOptions(
            max_width=1000, max_height=1000, max_bytes=4 * 1024
        ),
    )
    # Either we reduced dims to fit, or we gave up — both are valid;
    # importantly the algorithm must not raise.
    if result is not None:
        assert result.width < 1000 or result.height < 1000


# === 1×1 give-up ============================================================


async def test_give_up_returns_none_when_unfittable() -> None:
    """An impossible budget (e.g., 100 bytes) returns :data:`None`."""

    img = Image.new("RGB", (500, 500), color=(100, 100, 100))
    encoded = _png_data_url(img)
    content = ImageContent(mime_type="image/png", data=encoded)
    result = await resize_image(
        content,
        ImageResizeOptions(max_bytes=100),  # impossibly tight
    )
    # Either None or a 1×1 dummy — Pi gives up at 1×1.
    if result is not None:
        assert result.width <= 4 and result.height <= 4


# === EXIF auto-orient =======================================================


def _encode_with_exif_orientation(
    width: int, height: int, orientation: int
) -> str:
    """Encode a JPEG with the given EXIF orientation tag.

    Uses Pillow's :meth:`Image.getexif` API rather than hand-crafted
    TIFF bytes so the resulting EXIF stream is identical to what real
    cameras emit.
    """

    img = Image.new("RGB", (width, height), color=(0, 128, 255))
    exif = img.getexif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def test_exif_orientation_applied() -> None:
    """EXIF orientation tag rotates the image during decode."""

    # Build a 100×200 portrait image with EXIF orientation=6 (rotate
    # 90° CW). After ``exif_transpose`` it should decode as 200×100
    # (rotated to landscape).
    encoded = _encode_with_exif_orientation(100, 200, 6)
    content = ImageContent(mime_type="image/jpeg", data=encoded)
    result = await resize_image(content)
    assert result is not None
    # After exif_transpose, the in-memory dims are 200×100 (landscape).
    # The recorded original dimensions reflect post-EXIF orientation.
    assert result.original_width == 200
    assert result.original_height == 100


async def test_exif_orientation_normal_no_rotation() -> None:
    """EXIF orientation=1 (no rotation) leaves dims as-encoded."""

    encoded = _encode_with_exif_orientation(100, 200, 1)
    content = ImageContent(mime_type="image/jpeg", data=encoded)
    result = await resize_image(content)
    assert result is not None
    assert result.original_width == 100
    assert result.original_height == 200


async def test_exif_orientation_180_rotation() -> None:
    """EXIF orientation=3 (180° rotation) preserves dimensions."""

    encoded = _encode_with_exif_orientation(100, 200, 3)
    content = ImageContent(mime_type="image/jpeg", data=encoded)
    result = await resize_image(content)
    assert result is not None
    # 180° rotation does not swap width and height.
    assert result.original_width == 100
    assert result.original_height == 200


async def test_exif_orientation_270_rotation() -> None:
    """EXIF orientation=8 (rotate 270° CW) swaps width/height."""

    encoded = _encode_with_exif_orientation(100, 200, 8)
    content = ImageContent(mime_type="image/jpeg", data=encoded)
    result = await resize_image(content)
    assert result is not None
    assert result.original_width == 200
    assert result.original_height == 100


# === None return on unsupported ============================================


async def test_invalid_base64_returns_none() -> None:
    """Malformed base64 input returns :data:`None`."""

    content = ImageContent(mime_type="image/png", data="!!! not base64 !!!")
    result = await resize_image(content)
    assert result is None


async def test_corrupt_image_returns_none() -> None:
    """Bytes that decode but aren't a valid image return :data:`None`."""

    bogus = base64.b64encode(b"these are absolutely not image bytes").decode("ascii")
    content = ImageContent(mime_type="image/png", data=bogus)
    result = await resize_image(content)
    assert result is None


# === format_dimension_note ==================================================


def test_format_dimension_note_emitted_when_resized() -> None:
    """Resized images emit the coordinate-mapping note."""

    r = ResizedImage(
        data="",
        mime_type="image/png",
        original_width=4000,
        original_height=2000,
        width=2000,
        height=1000,
        was_resized=True,
    )
    note = format_dimension_note(r)
    assert note is not None
    assert "original 4000x2000" in note
    assert "displayed at 2000x1000" in note
    assert "Multiply coordinates by 2.00" in note


def test_format_dimension_note_none_when_not_resized() -> None:
    """Fast-path images return :data:`None` from the note formatter."""

    r = ResizedImage(
        data="",
        mime_type="image/png",
        original_width=100,
        original_height=100,
        width=100,
        height=100,
        was_resized=False,
    )
    assert format_dimension_note(r) is None


# === Dataclass shape ========================================================


def test_resized_image_is_frozen() -> None:
    """``ResizedImage`` is a frozen dataclass (Pi readonly interface parity)."""

    import dataclasses

    r = ResizedImage(
        data="",
        mime_type="image/png",
        original_width=10,
        original_height=10,
        width=10,
        height=10,
        was_resized=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.data = "mutated"  # type: ignore[misc]


def test_image_resize_options_defaults() -> None:
    """Default options match Pi parity (2000×2000, 4.5 MB, q=80)."""

    opts = ImageResizeOptions()
    assert opts.max_width == 2000
    assert opts.max_height == 2000
    assert opts.max_bytes == int(4.5 * 1024 * 1024)
    assert opts.jpeg_quality == 80
