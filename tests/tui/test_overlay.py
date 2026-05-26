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
