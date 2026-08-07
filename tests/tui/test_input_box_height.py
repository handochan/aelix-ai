"""Regression guard for the "input box keeps growing over a session" bug.

The inline prompt-toolkit renderer reserves ``max(min_available, last_screen,
preferred)`` rows and only clears that floor inside ``run_in_terminal``; between
turns the reserved height stays frozen at the tallest recent turn. The chat
input ``Window`` used to be the ONE body child whose ``max`` (10) exceeded its
``preferred``, so ``HSplit._divide_heights`` fed it every spare row after the
preferred pass — the editor crept upward a row at a time, decoupled from its
own content (an EMPTY buffer resolved to 10 rows under a tall floor).

The fix makes the editor CONTENT-BOUNDED via ``dont_extend_height=True`` (which
clamps the reported ``max`` down to ``preferred == the buffer's line count``),
with ``preferred`` left unspecified so it tracks the content. These tests pin
both halves: the reported dimension is content-bounded (``max == preferred``),
and the editor's ACTUAL divided height under a tall reserved floor equals its
content height, not the floor.

Headless (``DummyOutput``), app NOT running — ``Window.preferred_height`` and
``HSplit._divide_heights`` are pure layout maths and need no live terminal.
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aelix_coding_agent.tui.chrome import AelixChrome
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout.containers import ConditionalContainer, WritePosition
from prompt_toolkit.output import DummyOutput
from rich.console import Console

_WIDTH = 80


@asynccontextmanager
async def _chrome() -> AsyncGenerator[AelixChrome]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        console = Console(file=io.StringIO(), force_terminal=True, width=_WIDTH)
        chrome = AelixChrome(console=console, pt_input=pipe, pt_output=DummyOutput())
        try:
            yield chrome
        finally:
            with contextlib.suppress(Exception):
                chrome.exit()


def _set_text(chrome: AelixChrome, text: str) -> None:
    chrome.buffer.document = Document(text)


def _input_assigned_rows(chrome: AelixChrome, reserved_floor: int) -> int:
    """Divide the REAL body HSplit under a tall reserved floor and return the
    rows handed to the input window — the exact path the creep travelled."""
    body = chrome.app.layout.container.content  # FloatContainer -> body HSplit
    sizes = body._divide_heights(
        WritePosition(xpos=0, ypos=0, width=_WIDTH, height=reserved_floor)
    )
    assert sizes is not None, "body did not fit in the reserved floor"
    for child, size in zip(body._all_children, sizes, strict=True):
        if isinstance(child, ConditionalContainer) and child.content is chrome._input_window:
            return size
    raise AssertionError("input conditional not found among body children")


def _widget_row_rows(chrome: AelixChrome, *, above: bool, reserved_floor: int = 30) -> int:
    """Rows the divided body hands the widgets-above/below ConditionalContainer.

    Identifies the row by its render callback (the inline Window has no stored
    handle) so we read the SAME container the layout renders."""
    body = chrome.app.layout.container.content
    render = chrome._render_widgets_above if above else chrome._render_widgets_below
    sizes = body._divide_heights(
        WritePosition(xpos=0, ypos=0, width=_WIDTH, height=reserved_floor)
    )
    assert sizes is not None, "body did not fit in the reserved floor"
    for child, size in zip(body._all_children, sizes, strict=True):
        control = getattr(getattr(child, "content", None), "content", None)
        if isinstance(child, ConditionalContainer) and getattr(control, "text", None) == render:
            return size
    raise AssertionError("widget row not found among body children")


async def test_empty_widget_rows_collapse_and_show_when_populated() -> None:
    """Option B: ``widgets_above``/``widgets_below`` reserve 0 rows when they hold
    no lines (they used to reserve a permanent blank row each), and render their
    content when populated. The gate reads ``any(...values())``, so a slot cleared
    to an EMPTY LIST collapses too — ``bool(dict)`` alone would not."""
    async with _chrome() as chrome:
        # Empty: both rows collapse to 0.
        assert _widget_row_rows(chrome, above=True) == 0
        assert _widget_row_rows(chrome, above=False) == 0

        # Populated above: exactly its line count; below stays collapsed.
        chrome.set_widget("panel", ["[1/2] running", "[2/2] done"], above=True)
        assert _widget_row_rows(chrome, above=True) == 2
        assert _widget_row_rows(chrome, above=False) == 0

        # Cleared (key popped): collapses again.
        chrome.set_widget("panel", None, above=True)
        assert _widget_row_rows(chrome, above=True) == 0

        # A slot cleared to an EMPTY LIST also collapses (any(values()) is False).
        chrome.set_widget("x", [], above=True)
        assert _widget_row_rows(chrome, above=True) == 0


async def test_input_window_dimension_is_content_bounded() -> None:
    """The reported dimension tracks the buffer's line count and, crucially, has
    ``max == preferred`` so the window can never absorb the HSplit's slack."""
    async with _chrome() as chrome:
        win = chrome._input_window
        cases = {"": 1, "a\nb\nc": 3, "\n".join(["x"] * 15): 10}  # 15 lines -> capped at 10
        for text, expected in cases.items():
            _set_text(chrome, text)
            # A GENEROUS available height must not inflate a content-bounded editor.
            dim = win.preferred_height(_WIDTH, 30)
            assert dim.preferred == expected, (text, dim.preferred)
            assert dim.max == dim.preferred, (
                "input must be content-bounded (max == preferred) so it cannot "
                f"absorb reserved slack; got max={dim.max} preferred={dim.preferred}"
            )
            assert dim.min == 1


async def test_empty_input_height_is_independent_of_available_height() -> None:
    """The bug signature: an empty editor's resolved height tracked the available
    height. Post-fix it stays 1 whether the renderer offers 2 rows or 30."""
    async with _chrome() as chrome:
        _set_text(chrome, "")
        for max_available in (2, 10, 30, 100):
            assert chrome._input_window.preferred_height(_WIDTH, max_available).preferred == 1


async def test_input_does_not_absorb_reserved_floor() -> None:
    """Full-mechanism guard: even when the body is divided under a floor far
    taller than the content (the ratcheted idle reserve), the editor gets its
    CONTENT height, not the floor. This is precisely what crept before the fix
    (empty buffer -> 10 rows at floor 20)."""
    async with _chrome() as chrome:
        _set_text(chrome, "")
        for floor in (8, 20, 30):
            assert _input_assigned_rows(chrome, floor) == 1, floor

        _set_text(chrome, "a\nb\nc")
        for floor in (8, 20, 30):
            assert _input_assigned_rows(chrome, floor) == 3, floor

        # A 15-line draft is capped at the max of 10 and scrolls internally,
        # regardless of how much taller the reserved floor is.
        _set_text(chrome, "\n".join(["x"] * 15))
        for floor in (20, 30):
            assert _input_assigned_rows(chrome, floor) == 10, floor
