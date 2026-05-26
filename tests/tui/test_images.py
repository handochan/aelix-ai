"""Sprint 6h₁₀c (ADR-0106) §D — inline image capability + render/fallback tests.

Hermetic: ``detect_image_capability`` and ``render_image`` are driven via injected
``isatty`` / ``env`` — no real terminal is touched. The graphics tiers degrade to a
placeholder when ``term-image`` is absent, so the suite is green with or without the
``[images]`` extra installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_coding_agent.tui.images import (
    ImageCapability,
    detect_image_capability,
    render_image,
    text_placeholder,
)

# ---------------------------------------------------------------------------
# detect_image_capability — capability matrix (injected env/isatty)
# ---------------------------------------------------------------------------


def test_not_a_tty_is_none() -> None:
    # not-a-TTY wins even when an env says kitty.
    assert (
        detect_image_capability(isatty=False, env={"KITTY_WINDOW_ID": "1"})
        is ImageCapability.NONE
    )


def test_kitty_window_id() -> None:
    assert (
        detect_image_capability(isatty=True, env={"KITTY_WINDOW_ID": "3", "TERM": "xterm"})
        is ImageCapability.KITTY
    )


def test_term_xterm_kitty() -> None:
    assert (
        detect_image_capability(isatty=True, env={"TERM": "xterm-kitty"})
        is ImageCapability.KITTY
    )


def test_term_program_ghostty() -> None:
    assert (
        detect_image_capability(isatty=True, env={"TERM_PROGRAM": "ghostty", "TERM": "xterm"})
        is ImageCapability.KITTY
    )


def test_term_program_wezterm() -> None:
    assert (
        detect_image_capability(isatty=True, env={"TERM_PROGRAM": "WezTerm", "TERM": "xterm"})
        is ImageCapability.KITTY
    )


def test_iterm2_term_program() -> None:
    assert (
        detect_image_capability(isatty=True, env={"TERM_PROGRAM": "iTerm.app", "TERM": "xterm"})
        is ImageCapability.ITERM2
    )


def test_iterm2_lc_terminal() -> None:
    assert (
        detect_image_capability(isatty=True, env={"LC_TERMINAL": "iTerm2", "TERM": "xterm"})
        is ImageCapability.ITERM2
    )


def test_sixel_term() -> None:
    assert (
        detect_image_capability(isatty=True, env={"TERM": "xterm-sixel"})
        is ImageCapability.SIXEL
    )


def test_sixel_foot() -> None:
    assert detect_image_capability(isatty=True, env={"TERM": "foot"}) is ImageCapability.SIXEL


def test_plain_xterm_is_unicode() -> None:
    assert detect_image_capability(isatty=True, env={"TERM": "xterm"}) is ImageCapability.UNICODE


def test_no_color_is_none() -> None:
    assert (
        detect_image_capability(isatty=True, env={"NO_COLOR": "1", "TERM": "dumb"})
        is ImageCapability.NONE
    )


def test_dumb_term_is_none() -> None:
    assert detect_image_capability(isatty=True, env={"TERM": "dumb"}) is ImageCapability.NONE


def test_empty_term_is_none() -> None:
    assert detect_image_capability(isatty=True, env={}) is ImageCapability.NONE


def test_precedence_kitty_over_iterm() -> None:
    # A kitty signal beats a concurrent iTerm2 signal (kitty is checked first).
    cap = detect_image_capability(
        isatty=True,
        env={"KITTY_WINDOW_ID": "1", "TERM_PROGRAM": "iTerm.app"},
    )
    assert cap is ImageCapability.KITTY


# ---------------------------------------------------------------------------
# text_placeholder — format
# ---------------------------------------------------------------------------


def test_placeholder_with_size() -> None:
    assert text_placeholder("/tmp/cat.png", (640, 480)) == "[image: /tmp/cat.png 640×480]"


def test_placeholder_without_size() -> None:
    assert text_placeholder("/tmp/cat.png") == "[image: /tmp/cat.png]"


# ---------------------------------------------------------------------------
# render_image — fallback (no real terminal)
# ---------------------------------------------------------------------------


def _make_png(tmp_path: Path) -> Path:
    pytest.importorskip("PIL", reason="Pillow required to author the test fixture image")
    from PIL import Image as PILImage

    p = tmp_path / "fixture.png"
    PILImage.new("RGB", (8, 4), (10, 20, 30)).save(p)
    return p


def test_render_none_capability_returns_placeholder(tmp_path: Path) -> None:
    png = _make_png(tmp_path)
    out = render_image(png, max_cells=(20, 10), capability=ImageCapability.NONE)
    assert isinstance(out, str)
    assert out.startswith(f"[image: {png}")
    # source size is read for the placeholder when the file is a valid image.
    assert "8×4" in out


def test_render_missing_path_returns_placeholder() -> None:
    # A bogus/missing path must degrade to a placeholder, never raise.
    out = render_image(
        "/no/such/file.png",
        max_cells=(20, 10),
        capability=ImageCapability.UNICODE,
    )
    assert isinstance(out, str)
    assert out.startswith("[image: /no/such/file.png")


def test_render_graphics_missing_path_returns_placeholder() -> None:
    # Graphics tier on a missing path also degrades to placeholder (no raise).
    out = render_image(
        "/no/such/file.png",
        max_cells=(20, 10),
        capability=ImageCapability.KITTY,
    )
    assert isinstance(out, str)
    assert out.startswith("[image: /no/such/file.png")


def test_render_unicode_returns_renderable_or_placeholder(tmp_path: Path) -> None:
    # With the [images] extra (term-image or rich-pixels) a valid image yields a
    # non-placeholder renderable/escape-string; without it, a placeholder. Either
    # way it must not raise.
    png = _make_png(tmp_path)
    out = render_image(png, max_cells=(20, 10), capability=ImageCapability.UNICODE)
    assert out is not None
    if isinstance(out, str) and out.startswith("[image:"):
        # No Unicode renderer installed → placeholder path exercised.
        assert str(png) in out


def test_render_sixel_uses_unicode_or_placeholder(tmp_path: Path) -> None:
    # term-image 0.7.2 ships no SixelImage; a detected SIXEL terminal must route
    # through the Unicode tier (or placeholder) and never raise.
    png = _make_png(tmp_path)
    out = render_image(png, max_cells=(20, 10), capability=ImageCapability.SIXEL)
    assert out is not None
