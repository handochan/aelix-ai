"""Image resize utility (Pi parity ``utils/image-resize.ts``).

Sprint 6h₈ (Phase 5a-iv, ADR-0092, P-434/P-438). Port of Pi
``packages/coding-agent/src/utils/image-resize.ts`` (176 LOC). Pi uses
Photon (Rust/WASM) for image processing; Aelix uses Pillow which ships
type stubs since 10.0 and provides ``ImageOps.exif_transpose`` (replaces
~83 LOC of manual TIFF parsing in Pi).

Algorithm (Pi parity, binding):

1. Decode base64 → BytesIO → :func:`PIL.Image.open`.
2. Apply EXIF auto-orientation via :func:`PIL.ImageOps.exif_transpose`.
3. Fast-path: if width ≤ max_w AND height ≤ max_h AND base64 size <
   max_bytes — return as-is with ``was_resized=False``.
4. Calculate initial target dimensions (aspect-preserving, snap to
   max_w / max_h).
5. Iterative encode search: at each dimension level try PNG +
   ``jpegQuality`` then 4 fallback JPEG qualities (85, 70, 55, 40),
   first under ``max_bytes`` wins.
6. If none fit at the current dimensions, scale dims × 0.75 and retry
   until 1×1 or no further progress.
7. Give-up: return :data:`None`.

Pi citation: ``utils/image-resize.ts:1-176`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass

from aelix_ai.messages import ImageContent
from PIL import Image, ImageOps

# Pi parity: ``DEFAULT_MAX_BYTES`` (``image-resize.ts:23``). 4.5 MB
# headroom below Anthropic's 5 MB inline image limit.
_DEFAULT_MAX_BYTES: int = int(4.5 * 1024 * 1024)


@dataclass
class ImageResizeOptions:
    """Pi parity: ``ImageResizeOptions`` (``image-resize.ts:5-11``)."""

    max_width: int = 2000
    max_height: int = 2000
    max_bytes: int = _DEFAULT_MAX_BYTES
    jpeg_quality: int = 80


@dataclass(frozen=True)
class ResizedImage:
    """Pi parity: ``ResizedImage`` (``image-resize.ts:13-21``).

    Aelix snake_case mirror of Pi camelCase shape:
    ``{data, mimeType, originalWidth, originalHeight, width, height,
    wasResized}``.
    """

    data: str  # base64 payload (no data-URL prefix)
    mime_type: str  # ``"image/png"`` or ``"image/jpeg"``
    original_width: int
    original_height: int
    width: int
    height: int
    was_resized: bool


@dataclass(frozen=True)
class _EncodedCandidate:
    """Pi parity: ``EncodedCandidate`` (``image-resize.ts:33-37``)."""

    data: str
    encoded_size: int
    mime_type: str


def _encode_png(image: Image.Image) -> _EncodedCandidate:
    """Encode ``image`` as PNG and wrap as :class:`_EncodedCandidate`."""

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    raw = buf.getvalue()
    data = base64.b64encode(raw).decode("ascii")
    return _EncodedCandidate(
        data=data, encoded_size=len(data), mime_type="image/png"
    )


def _encode_jpeg(image: Image.Image, quality: int) -> _EncodedCandidate:
    """Encode ``image`` as JPEG at the given quality."""

    # JPEG cannot encode an alpha channel; flatten to RGB so the encode
    # succeeds for PNGs with transparency.
    if image.mode in ("RGBA", "LA", "P"):
        rgb = Image.new("RGB", image.size, (255, 255, 255))
        converted = image.convert("RGBA") if image.mode == "P" else image
        rgb.paste(
            converted,
            mask=converted.split()[-1] if converted.mode in ("RGBA", "LA") else None,
        )
        image_for_encode = rgb
    elif image.mode != "RGB":
        image_for_encode = image.convert("RGB")
    else:
        image_for_encode = image
    buf = io.BytesIO()
    image_for_encode.save(buf, format="JPEG", quality=quality)
    raw = buf.getvalue()
    data = base64.b64encode(raw).decode("ascii")
    return _EncodedCandidate(
        data=data, encoded_size=len(data), mime_type="image/jpeg"
    )


def _try_encodings(
    image: Image.Image, width: int, height: int, jpeg_qualities: list[int]
) -> list[_EncodedCandidate]:
    """Pi parity: ``tryEncodings`` (``image-resize.ts:107-118``).

    Resize via Lanczos and emit PNG + JPEG candidates (one per quality
    in ``jpeg_qualities``). Pi runs PNG first; Aelix mirrors.
    """

    resized = image.resize((width, height), Image.Resampling.LANCZOS)
    candidates: list[_EncodedCandidate] = [_encode_png(resized)]
    for quality in jpeg_qualities:
        candidates.append(_encode_jpeg(resized, quality))
    return candidates


def _dedupe_qualities(primary: int, fallback: list[int]) -> list[int]:
    """Pi parity: ``Array.from(new Set([opts.jpegQuality, 85, 70, 55, 40]))``.

    Order-preserving deduplication of the JPEG quality steps so
    ``jpegQuality=85`` does not double-encode the 85 fallback.
    """

    seen: set[int] = set()
    out: list[int] = []
    for q in [primary, *fallback]:
        if q not in seen:
            out.append(q)
            seen.add(q)
    return out


def _resize_sync(
    data_bytes: bytes,
    input_base64_size: int,
    mime_type_hint: str,
    options: ImageResizeOptions,
) -> ResizedImage | None:
    """Synchronous core of :func:`resize_image` (Pi parity body).

    Pi citation: ``image-resize.ts:62-160``.
    """

    try:
        raw_image = Image.open(io.BytesIO(data_bytes))
        # Force-load so any decode error surfaces here before we touch
        # ``raw_image`` attributes downstream.
        raw_image.load()
    except Exception:
        return None

    try:
        # Pi parity: ``applyExifOrientation`` (``image-resize.ts:79``).
        # Pillow's ``ImageOps.exif_transpose`` replaces ~83 LOC of Pi's
        # manual TIFF parsing — documented Aelix-additive divergence in
        # ADR-0092 §B.
        image: Image.Image
        try:
            transposed = ImageOps.exif_transpose(raw_image)
            image = transposed if transposed is not None else raw_image
        except Exception:
            image = raw_image

        original_width, original_height = image.size

        # Pi parity: already-compliant fast-path (``image-resize.ts:87-96``).
        # W4 MINOR fold-in: dropped the ``format_hint`` intermediate
        # because the ``mime_type_hint or "image/png"`` fallback chain
        # below covers every reachable branch (Pi's ``image-resize.ts:84``
        # ``format`` variable is dead-code after the file-processor path
        # always supplies a MIME hint).
        if (
            original_width <= options.max_width
            and original_height <= options.max_height
            and input_base64_size < options.max_bytes
        ):
            return ResizedImage(
                data=base64.b64encode(data_bytes).decode("ascii"),
                mime_type=mime_type_hint or "image/png",
                original_width=original_width,
                original_height=original_height,
                width=original_width,
                height=original_height,
                was_resized=False,
            )

        # Pi parity: initial dimension calculation (``image-resize.ts:98-106``).
        target_width = original_width
        target_height = original_height
        if target_width > options.max_width:
            target_height = round(
                target_height * options.max_width / target_width
            )
            target_width = options.max_width
        if target_height > options.max_height:
            target_width = round(
                target_width * options.max_height / target_height
            )
            target_height = options.max_height
        # Guard: never go below 1×1.
        target_width = max(1, target_width)
        target_height = max(1, target_height)

        # Pi parity: quality steps order-preserving dedupe
        # (``image-resize.ts:120``).
        quality_steps = _dedupe_qualities(options.jpeg_quality, [85, 70, 55, 40])

        current_width = target_width
        current_height = target_height

        # Pi parity: iterative encode + dimension fallback
        # (``image-resize.ts:124-155``).
        while True:
            candidates = _try_encodings(
                image, current_width, current_height, quality_steps
            )
            for candidate in candidates:
                if candidate.encoded_size < options.max_bytes:
                    return ResizedImage(
                        data=candidate.data,
                        mime_type=candidate.mime_type,
                        original_width=original_width,
                        original_height=original_height,
                        width=current_width,
                        height=current_height,
                        was_resized=True,
                    )

            if current_width == 1 and current_height == 1:
                break

            next_width = (
                1 if current_width == 1 else max(1, int(current_width * 0.75))
            )
            next_height = (
                1 if current_height == 1 else max(1, int(current_height * 0.75))
            )
            if next_width == current_width and next_height == current_height:
                break
            current_width = next_width
            current_height = next_height

        return None
    except Exception:
        return None


async def resize_image(
    img: ImageContent, options: ImageResizeOptions | None = None
) -> ResizedImage | None:
    """Pi parity: ``resizeImage`` (``image-resize.ts:62-160``).

    Decode ``img.data`` (base64), apply EXIF orientation, and either
    fast-path return when already compliant or iteratively re-encode at
    decreasing dimensions / JPEG qualities until either a candidate
    fits under ``options.max_bytes`` or the algorithm gives up (returns
    :data:`None`).

    Blocking image I/O runs via :func:`asyncio.to_thread` to keep the
    event loop free.
    """

    opts = options or ImageResizeOptions()

    # Pi parity: decode base64 once + measure the *base64 string* size
    # (NOT the binary size) — Pi compares ``inputBase64Size <
    # opts.maxBytes`` against the base64 payload length.
    try:
        data_bytes = base64.b64decode(img.data)
    except Exception:
        return None
    input_base64_size = len(img.data)
    mime_hint = img.mime_type or ""

    return await asyncio.to_thread(
        _resize_sync, data_bytes, input_base64_size, mime_hint, opts
    )


def format_dimension_note(result: ResizedImage) -> str | None:
    """Pi parity: ``formatDimensionNote`` (``image-resize.ts:166-174``).

    Emits the model-facing coordinate-mapping note used by
    screenshot tools and the file-processor wire so the model can
    multiply tool-reported coordinates back to the original image.
    Returns :data:`None` when the image was not resized (Pi parity).
    """

    if not result.was_resized:
        return None
    scale = result.original_width / result.width
    return (
        f"[Image: original {result.original_width}x{result.original_height}, "
        f"displayed at {result.width}x{result.height}. "
        f"Multiply coordinates by {scale:.2f} to map to original image.]"
    )


__all__: list[str] = [
    "ImageResizeOptions",
    "ResizedImage",
    "format_dimension_note",
    "resize_image",
]
