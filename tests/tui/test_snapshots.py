"""Sprint 6h₁₀d (ADR-0107) §C — pyte snapshots of the rendered chrome buffer.

These assert on the ACTUAL terminal grid produced by the live ``AelixChrome``
``Application`` (escape stream → ``Vt100_Output`` → ``pyte`` screen), not on the
internal render helpers. The harness (``_pyte`` sibling module) drives one painted
frame headlessly and returns ``screen.display``; we assert key substrings appear
at the expected rows.

§2.3 image-tier validation lives at the bottom: the Unicode (``rich-pixels``) tier
prints a colored block through a Rich ``Console`` into a captured buffer → pyte
shows colored cells; the ``NONE`` tier degrades to the ``[image: …]`` placeholder.
(term-image graphics tiers are dormant under Pillow≥11 — not exercised here.)
"""

from __future__ import annotations

import io
import os
import tempfile
from typing import Any

import pyte
import pytest
from _pyte import (  # sibling helper (pytest prepend import mode)
    assert_row_contains,
    render_chrome_to_screen,
)
from aelix_agent_core.contracts.descriptor import DescriptorEnvelope
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.descriptors import DescriptorRegistry, DescriptorRenderer
from aelix_coding_agent.tui.footer_data import AelixFooterData
from aelix_coding_agent.tui.images import ImageCapability, render_image
from PIL import Image
from rich.console import Console


def _env(kind: str, *, ns: str = "ext", id_: str = "a", **payload: Any) -> DescriptorEnvelope:
    body: dict[str, Any] = {"kind": kind, **payload}
    return DescriptorEnvelope(
        kind=kind, namespace=ns, id=id_, payload=body  # type: ignore[arg-type]
    )


def _wire(chrome: AelixChrome) -> DescriptorRenderer:
    """Build a registry+renderer bound to *chrome* (apply envelopes to render)."""
    footer = AelixFooterData(cwd=os.getcwd())
    registry = DescriptorRegistry()
    renderer = DescriptorRenderer(chrome, footer, registry)
    registry.on_apply = renderer.render
    registry.on_remove = renderer.clear
    # Stash the registry on the renderer-less side so callers reach apply via it.
    renderer._registry_for_test = registry  # type: ignore[attr-defined]
    return renderer


# === chrome snapshots ========================================================


async def test_base_chrome_input_prompt_and_footer_branch() -> None:
    def build(chrome: AelixChrome) -> None:
        chrome.set_footer_line("⎇ feature-x")
        chrome.set_editor_text("type here")

    display = await render_chrome_to_screen(build_state=build)
    # The input editor text paints (the focused BufferControl row).
    input_row = assert_row_contains(display, "type here")
    # The footer ``⎇ <branch>`` row paints at/near the bottom.
    footer_row = assert_row_contains(display, "⎇ feature-x")
    assert footer_row >= 1
    # Geometry: the footer is the LAST painted chrome row (nothing renders below
    # it) and the input editor sits above it — catches a region that bleeds past
    # the footer or overdraws the input, which a substring-only check would miss.
    last_nonempty = max(i for i, row in enumerate(display) if row.strip())
    assert footer_row == last_nonempty
    assert input_row < footer_row


async def test_status_item_and_footer_segment_via_descriptor() -> None:
    def build(chrome: AelixChrome) -> None:
        renderer = _wire(chrome)
        registry: DescriptorRegistry = renderer._registry_for_test  # type: ignore[attr-defined]
        registry.apply(_env("status-item", id_="s", text="READY"))
        registry.apply(_env("footer-segment", id_="f", text="seg-text", icon="●"))

    display = await render_chrome_to_screen(build_state=build)
    status_row = assert_row_contains(display, "READY")
    footer_row = assert_row_contains(display, "seg-text")
    # status row renders above the footer-segment row (chrome.py body order).
    assert status_row < footer_row


async def test_status_item_removal_clears_from_grid() -> None:
    # Negative assertion: an applied-then-removed descriptor must be ABSENT from
    # the rendered grid (validates the clear path at the real-buffer level, not
    # just that apply painted something).
    def build(chrome: AelixChrome) -> None:
        renderer = _wire(chrome)
        registry: DescriptorRegistry = renderer._registry_for_test  # type: ignore[attr-defined]
        env = _env("status-item", id_="s", text="GONE-SOON")
        registry.apply(env)
        # ``removed`` lives on the envelope, not the payload — copy + flip it.
        registry.apply(env.model_copy(update={"removed": True}))

    display = await render_chrome_to_screen(build_state=build)
    assert not any("GONE-SOON" in row for row in display), "removed status-item still on grid"


async def test_toast_descriptor_shows_in_float_region() -> None:
    def build(chrome: AelixChrome) -> None:
        renderer = _wire(chrome)
        registry: DescriptorRegistry = renderer._registry_for_test  # type: ignore[attr-defined]
        # auto_dismiss_ms=0 → sticky (no loop timer needed; deterministic).
        registry.apply(_env("toast", id_="t", text="SAVED-OK", auto_dismiss_ms=0))

    display = await render_chrome_to_screen(build_state=build)
    # The toast is a top-right Float; its text appears somewhere in the grid.
    assert_row_contains(display, "SAVED-OK")


async def test_breadcrumb_descriptor_shows_joined_chain_in_header() -> None:
    def build(chrome: AelixChrome) -> None:
        renderer = _wire(chrome)
        registry: DescriptorRegistry = renderer._registry_for_test  # type: ignore[attr-defined]
        registry.apply(_env("breadcrumb", id_="b1", label="Home"))
        registry.apply(_env("breadcrumb", id_="b2", label="Repo"))
        registry.apply(_env("breadcrumb", id_="b3", label="File"))

    display = await render_chrome_to_screen(build_state=build)
    header_row = assert_row_contains(display, "Home › Repo › File")
    # The header is the first chrome row (chrome.py body order: header first).
    assert header_row == 0


# === §2.3 image Unicode-tier + placeholder ===================================


def _render_to_pyte(
    renderable: object, *, cols: int = 80, rows: int = 12, markup: bool = True
) -> pyte.Screen:
    buffer = io.StringIO()
    # ``markup=False`` for the literal ``[image: …]`` placeholder string — Rich would
    # otherwise parse the leading ``[image: …]`` as a (bogus) markup tag and drop it.
    Console(
        file=buffer, force_terminal=True, width=cols, color_system="truecolor", markup=markup
    ).print(renderable)
    screen = pyte.Screen(cols, rows)
    pyte.Stream(screen).feed(buffer.getvalue())
    return screen


def test_image_unicode_tier_renders_colored_cells(tmp_path: Any) -> None:
    png = tmp_path / "tiny.png"
    Image.new("RGB", (4, 4), (255, 0, 0)).save(os.fspath(png))

    renderable = render_image(os.fspath(png), max_cells=(8, 4), capability=ImageCapability.UNICODE)
    # Unicode tier yields a Rich renderable (rich-pixels Pixels), not a placeholder.
    assert not isinstance(renderable, str)

    screen = _render_to_pyte(renderable)
    nonempty = [row for row in screen.display if row.strip()]
    assert nonempty, "Unicode image rendered an empty grid"
    colored = sum(
        1
        for row in screen.buffer.values()
        for cell in row.values()
        if cell.fg != "default" or cell.bg != "default"
    )
    assert colored > 0, "expected colored cells from the half-block image"


def test_image_none_capability_renders_text_placeholder() -> None:
    with tempfile.TemporaryDirectory() as d:
        png = os.path.join(d, "tiny.png")
        Image.new("RGB", (4, 4)).save(png)
        result = render_image(png, max_cells=(8, 4), capability=ImageCapability.NONE)

    assert isinstance(result, str)
    assert result.startswith("[image:")
    assert "tiny.png" in result

    screen = _render_to_pyte(result, markup=False)
    assert_screen_contains(screen, "[image:")


def assert_screen_contains(screen: pyte.Screen, text: str) -> None:
    if not any(text in row for row in screen.display):
        rendered = "\n".join(repr(row) for row in screen.display)
        raise AssertionError(f"{text!r} not found in screen:\n{rendered}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
