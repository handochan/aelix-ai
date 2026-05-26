"""Sprint 6h₁₀c (ADR-0106) §D — inline image rendering for the Aelix TUI.

Capability-gated, fallback-rich image output. The native graphics tier is driven
by ``term-image`` (auto-detecting the Kitty / iTerm2 escape protocols and emitting
an escape-*string* you print yourself — which fits the
``chrome.print_above`` → ``in_terminal`` → ``Console.print`` seam, ``chrome.py:246``).
The Unicode half-block tier uses ``term-image``'s ``BlockImage`` when available, else
``rich-pixels`` (a Rich renderable). When neither extra is installed (or any render
fails), output degrades to a ``[image: <path> W×H]`` text placeholder.

Both third-party imports are guarded (try/except at module load with a flag), so
capability detection works and :func:`render_image` returns the placeholder even
when the ``[images]`` extra is absent.

Two pure, injectable seams mirror the codebase precedent (``parse_input_line``
purity, ``AelixFooterData(cwd=)`` injection):

- :func:`detect_image_capability` — classifies the terminal from ``isatty`` + ``env``
  (both injectable; defaults read the real process), with the precedence in §2.
- :func:`render_image` — picks a renderer from the (detected or supplied) capability,
  degrading **inside** the function (graphics → Unicode → placeholder). It never
  raises into the output pump.

Verified against the actually-installed ``term-image`` (0.7.2) API: the graphics
classes raise ``StyleError`` at construction in an unsupported terminal, so the
graphics tier sets ``forced_support = True`` (capability is decided here, not by
term-image's own probe); sizing uses ``set_size(width=Size.FIT, frame_size=(cols,
lines))`` for whole-cell box fitting; the escape-string is captured via ``str(img)``.
term-image 0.7.2 ships **no** ``SixelImage`` class, so a detected ``SIXEL`` terminal
renders through the Unicode tier (§2 caveat 3 — sixel support is version-dependent).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import Enum

# --- guarded optional imports (the `[images]` extra) -----------------------
try:  # term-image: native Kitty/iTerm2 escape strings + Unicode BlockImage tier
    # term-image is intentionally NOT in the workspace env: its current release
    # caps Pillow<11, conflicting with the coding-agent's Pillow>=11 core pin
    # (see pyproject `[images]` extra note). The import is guarded; suppress the
    # resolver error so the project pyright baseline is unaffected.
    import term_image.image as _ti  # pyright: ignore[reportMissingImports]

    _HAS_TERM_IMAGE = True
except ImportError:  # pragma: no cover - exercised by the no-extra install path
    _ti = None  # type: ignore[assignment]
    _HAS_TERM_IMAGE = False

try:  # rich-pixels: Rich-renderable Unicode half-block fallback
    from rich_pixels import Pixels as _Pixels

    _HAS_RICH_PIXELS = True
except ImportError:  # pragma: no cover - exercised by the no-extra install path
    _Pixels = None  # type: ignore[assignment]
    _HAS_RICH_PIXELS = False


class ImageCapability(Enum):
    """The image-rendering tier supported by the active terminal."""

    KITTY = "kitty"
    ITERM2 = "iterm2"
    SIXEL = "sixel"
    UNICODE = "unicode"
    NONE = "none"


def detect_image_capability(
    *,
    isatty: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> ImageCapability:
    """Classify the terminal's image capability (pure; precedence per spec §2).

    Both inputs are injectable for hermetic testing: ``isatty`` defaults to
    ``sys.stdout.isatty()`` and ``env`` defaults to ``os.environ``.

    Precedence:

    1. Not a TTY → :attr:`ImageCapability.NONE` (checked first).
    2. Kitty graphics: ``KITTY_WINDOW_ID`` set, ``TERM`` containing ``kitty``,
       or ``TERM_PROGRAM=ghostty``.
    3. WezTerm (``TERM_PROGRAM=WezTerm``) → Kitty graphics.
    4. iTerm2: ``TERM_PROGRAM=iTerm.app`` or ``LC_TERMINAL=iTerm2``.
    5. sixel: ``TERM`` containing ``sixel``, or ``foot`` / ``mlterm``.
    6. else Unicode half-block, unless ``NO_COLOR`` is set or ``TERM`` is
       ``dumb``/empty → :attr:`ImageCapability.NONE`.
    """

    if isatty is None:
        try:
            import sys

            isatty = sys.stdout.isatty()
        except (AttributeError, ValueError, OSError):  # pragma: no cover - defensive
            isatty = False
    if env is None:
        env = os.environ

    if not isatty:
        return ImageCapability.NONE

    term = env.get("TERM", "").lower()
    term_program = env.get("TERM_PROGRAM", "")
    term_program_lower = term_program.lower()

    # Kitty graphics protocol: kitty itself, Ghostty, or WezTerm.
    if (
        env.get("KITTY_WINDOW_ID")
        or "kitty" in term
        or term_program_lower == "ghostty"
        or term_program == "WezTerm"
    ):
        return ImageCapability.KITTY

    # iTerm2 inline-images protocol.
    if term_program == "iTerm.app" or env.get("LC_TERMINAL") == "iTerm2":
        return ImageCapability.ITERM2

    # sixel-capable terminals.
    if "sixel" in term or "foot" in term or "mlterm" in term:
        return ImageCapability.SIXEL

    # Unicode half-block, gated by NO_COLOR / dumb terminal.
    if "NO_COLOR" in env or term in ("", "dumb"):
        return ImageCapability.NONE

    return ImageCapability.UNICODE


def text_placeholder(path: object, size: tuple[int, int] | None = None) -> str:
    """Return the ``[image: <path> W×H]`` text placeholder.

    *size*, when known, is the source image's ``(width, height)`` in pixels.
    """

    if size is not None:
        return f"[image: {path} {size[0]}×{size[1]}]"
    return f"[image: {path}]"


def _source_size(path: str) -> tuple[int, int] | None:
    """Best-effort source pixel size for the placeholder; ``None`` on any failure."""

    try:
        from PIL import Image as _PILImage

        with _PILImage.open(path) as img:
            return img.size
    except Exception:  # noqa: BLE001 - placeholder must never raise
        return None


def _render_graphics(path: str, capability: ImageCapability, max_cells: tuple[int, int]) -> str:
    """Build the term-image graphics class, force support, capture the escape-string.

    Raises on failure (caught by :func:`render_image`'s degrade chain).
    """

    assert _ti is not None  # guarded by caller (_HAS_TERM_IMAGE)
    image_cls = _ti.KittyImage if capability is ImageCapability.KITTY else _ti.ITerm2Image
    # Capability is decided here (env-based), not by term-image's own terminal
    # probe, so force support — otherwise construction raises StyleError when the
    # running process is not inside the target terminal.
    image_cls.forced_support = True
    img = image_cls.from_file(path)
    img.set_size(width=_ti.Size.FIT, frame_size=(max_cells[0], max_cells[1]))
    return str(img)


def _render_unicode(path: str, max_cells: tuple[int, int]) -> object | str:
    """Unicode half-block tier: term-image BlockImage (escape-string) or rich-pixels.

    Raises on failure (caught by :func:`render_image`'s degrade chain).
    """

    if _HAS_TERM_IMAGE and _ti is not None:
        img = _ti.BlockImage.from_file(path)
        img.set_size(width=_ti.Size.FIT, frame_size=(max_cells[0], max_cells[1]))
        return str(img)
    if _HAS_RICH_PIXELS and _Pixels is not None:
        # rich-pixels resizes in PIXELS; approximate cells→pixels (≈1 col ≈ 1px wide,
        # 1 row ≈ 2px tall for half-block) so the result stays within the box.
        return _Pixels.from_image_path(path, resize=(max_cells[0], max_cells[1] * 2))
    raise RuntimeError("no Unicode image renderer available")


def render_image(
    path: object,
    *,
    max_cells: tuple[int, int],
    capability: ImageCapability | None = None,
) -> object | str:
    """Render *path* to a Rich renderable / escape-string, or a text placeholder.

    *capability* defaults to :func:`detect_image_capability`. The graphics tiers
    (Kitty / iTerm2) emit a raw escape-string sized to a whole-cell ``max_cells``
    box; ``UNICODE`` (and a detected ``SIXEL`` terminal, which term-image 0.7.2
    cannot emit natively) render via the Unicode half-block tier; ``NONE`` or any
    failure returns :func:`text_placeholder`.

    Degrades **inside** this function (graphics → Unicode → placeholder) and never
    raises — safe to feed straight into the chrome output pump.
    """

    if capability is None:
        capability = detect_image_capability()

    path_str = os.fspath(path) if isinstance(path, (str, os.PathLike)) else str(path)

    if capability is ImageCapability.NONE:
        return text_placeholder(path, _source_size(path_str))

    # Graphics tier: try native escape-string, then degrade to Unicode.
    if capability in (ImageCapability.KITTY, ImageCapability.ITERM2):
        if _HAS_TERM_IMAGE:
            try:
                return _render_graphics(path_str, capability, max_cells)
            except Exception:  # noqa: BLE001 - degrade, never raise into the pump
                pass
        try:
            return _render_unicode(path_str, max_cells)
        except Exception:  # noqa: BLE001 - degrade to placeholder
            return text_placeholder(path, _source_size(path_str))

    # UNICODE / SIXEL (no native sixel class in term-image 0.7.2) → Unicode tier.
    try:
        return _render_unicode(path_str, max_cells)
    except Exception:  # noqa: BLE001 - degrade to placeholder
        return text_placeholder(path, _source_size(path_str))


__all__ = [
    "ImageCapability",
    "detect_image_capability",
    "render_image",
    "text_placeholder",
]
