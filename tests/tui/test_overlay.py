"""Sprint 6h₁₀b (ADR-0105) — overlay anchor mapping + OverlayHandle tests.

``make_float`` and ``AelixOverlayHandle`` are unit-tested directly here (the
modal *runner* ``show_modal`` is exercised through the dialog tests in
``test_context.py``). No app session needed: Float/Window construct standalone
and the handle's hide/show path only calls ``chrome.invalidate``.
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.extensions.widget_protocols import OverlayHandle, OverlayOptions
from aelix_coding_agent.tui.overlay import AelixOverlayHandle, make_float
from prompt_toolkit.layout import Window


def test_make_float_center_pins_no_edges() -> None:
    f = make_float(Window(), OverlayOptions(anchor="center"))
    assert f.left is None and f.right is None and f.top is None and f.bottom is None


@pytest.mark.parametrize(
    ("anchor", "expect_top", "expect_bottom", "expect_left", "expect_right"),
    [
        ("top-left", True, False, True, False),
        ("top-right", True, False, False, True),
        ("bottom-left", False, True, True, False),
        ("bottom-right", False, True, False, True),
        ("top-center", True, False, False, False),
        ("bottom-center", False, True, False, False),
        ("left-center", False, False, True, False),
        ("right-center", False, False, False, True),
    ],
)
def test_make_float_anchor_edges(
    anchor: str,
    expect_top: bool,
    expect_bottom: bool,
    expect_left: bool,
    expect_right: bool,
) -> None:
    f = make_float(Window(), OverlayOptions(anchor=anchor))  # type: ignore[arg-type]
    assert (f.top is not None) is expect_top
    assert (f.bottom is not None) is expect_bottom
    assert (f.left is not None) is expect_left
    assert (f.right is not None) is expect_right


def test_make_float_width_and_offset() -> None:
    f = make_float(Window(), OverlayOptions(anchor="top-left", width=40, offset_x=2, offset_y=3))
    assert f.width == 40
    assert f.top == 3
    assert f.left == 2


def test_make_float_default_options_centered() -> None:
    f = make_float(Window())
    assert f.left is None and f.top is None


class _FakeChrome:
    def __init__(self) -> None:
        self.invalidated = 0
        self.focused: object = None

    def invalidate(self) -> None:
        self.invalidated += 1

    def focus(self, content: object) -> None:
        self.focused = content

    def focus_input(self) -> None:
        self.focused = "input"


def test_overlay_handle_hide_show() -> None:
    chrome = _FakeChrome()
    hidden = {"v": False}
    content = Window()
    handle = AelixOverlayHandle(chrome, content, hidden)  # type: ignore[arg-type]
    assert isinstance(handle, OverlayHandle)
    assert handle.is_hidden() is False
    handle.hide()
    assert handle.is_hidden() is True and hidden["v"] is True
    handle.set_hidden(False)
    assert handle.is_hidden() is False
    assert chrome.invalidated >= 2  # each toggle invalidates


def test_overlay_handle_focus_routes_to_chrome() -> None:
    chrome = _FakeChrome()
    content = Window()
    handle = AelixOverlayHandle(chrome, content, {"v": False})  # type: ignore[arg-type]
    handle.focus()
    assert chrome.focused is content
    handle.unfocus()
    assert chrome.focused == "input"
    # is_focused is defensive (no real app) → False, never raises.
    assert handle.is_focused() is False


# === WP-8 (Feature 5) — modal reserve grows with the multi-line footer ======


class _FakeBuffer:
    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeReserveChrome:
    """Minimal chrome for ``_reserve_rows``: a buffer + a footer_line_count."""

    def __init__(self, *, footer_rows: int, buffer_text: str = "") -> None:
        self._footer_rows = footer_rows
        self.buffer = _FakeBuffer(buffer_text)

    def footer_line_count(self) -> int:
        return self._footer_rows


def test_reserve_rows_single_line_footer_matches_floor() -> None:
    from aelix_coding_agent.tui.overlay import _MODAL_RESERVE_ROWS, _reserve_rows

    chrome = _FakeReserveChrome(footer_rows=1)
    # 1-row idle input + status(1) + footer(1) = 3, below the floor → floor wins.
    assert _reserve_rows(chrome) == _MODAL_RESERVE_ROWS  # type: ignore[arg-type]


def test_reserve_rows_grows_with_multiline_footer() -> None:
    from aelix_coding_agent.tui.overlay import _MODAL_RESERVE_ROWS, _reserve_rows

    one = _reserve_rows(_FakeReserveChrome(footer_rows=1))  # type: ignore[arg-type]
    three = _reserve_rows(_FakeReserveChrome(footer_rows=3))  # type: ignore[arg-type]
    # A 3-row footer reserves strictly more than a 1-row footer (input 1 +
    # status 1 + footer 3 = 5 vs the floor 5 → equal here, so push input up).
    tall = _reserve_rows(
        _FakeReserveChrome(footer_rows=3, buffer_text="a\nb")  # 2-row input
    )  # type: ignore[arg-type]
    assert three >= one
    # input(2) + status(1) + footer(3) = 6 > the floor.
    assert tall == 6
    assert tall > _MODAL_RESERVE_ROWS


def test_reserve_rows_missing_counter_falls_back_to_floor() -> None:
    from aelix_coding_agent.tui.overlay import _MODAL_RESERVE_ROWS, _reserve_rows

    class _NoCounter:
        buffer = _FakeBuffer("")

    # No footer_line_count attr → assumes a 1-row footer, floor applies.
    assert _reserve_rows(_NoCounter()) == _MODAL_RESERVE_ROWS  # type: ignore[arg-type]
