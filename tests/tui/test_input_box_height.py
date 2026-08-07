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
from prompt_toolkit.application.current import set_app
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
    """Rows the divided body hands the widgets-above/below row.

    Identifies the row by its render callback (the inline Window has no stored
    handle) so we read the SAME container the layout renders.

    Measured under ``set_app`` with a bumped ``render_counter``:
    ``FormattedTextControl`` evaluates its text callable once per render pass and
    caches the fragments under ``get_app().render_counter`` (``_fragment_cache``,
    maxsize 1). The app is NOT running here, so ``get_app()`` would hand back a
    fresh ``DummyApplication`` whose counter is always 0 — every measurement after
    the first would then read STALE content (an empty first reading would pin the
    row at 1 forever). ``set_app`` makes the real app current and the bump
    simulates the next render pass, exactly as the redraw loop does.
    """
    body = chrome.app.layout.container.content
    render = chrome._render_widgets_above if above else chrome._render_widgets_below
    with set_app(chrome.app):
        chrome.app.render_counter += 1
        sizes = body._divide_heights(
            WritePosition(xpos=0, ypos=0, width=_WIDTH, height=reserved_floor)
        )
    assert sizes is not None, "body did not fit in the reserved floor"
    for child, size in zip(body._all_children, sizes, strict=True):
        inner = child.content if isinstance(child, ConditionalContainer) else child
        if getattr(getattr(inner, "content", None), "text", None) == render:
            return size
    raise AssertionError("widget row not found among body children")


async def test_widgets_above_row_survives_the_stream_tail_lifecycle() -> None:
    """The widgets-above row must NEVER collapse to 0, even with no widgets.

    This is a layout STABILISER, not dead space — do not "optimise" it away by
    gating the row on a non-empty filter. The streaming tail lives in this exact
    row: ``shell.py`` writes it with ``set_widget("__stream__", …, above=True)``
    and clears it with a falsy tail, which becomes ``set_widget(…, None)`` and
    POPS the key — so an empty ``_widgets_above`` is a normal mid-stream state,
    not just an idle one.

    Crucially, that clear is applied inside ``print_above_many``'s
    ``apply_before_redraw`` hook, i.e. INSIDE the erase/CPR two-phase fold-in
    that the round-3 flicker fix made atomic. If the row can collapse, every
    mid-stream tail clear turns an atomic CONTENT update into a HEIGHT change
    and shifts the input/status/footer up a row — visible flicker on every
    chunk. An always-present 1-row floor keeps the fold-in a pure content swap.

    Drives the REAL chrome through the tail lifecycle exactly as the shell does.
    """
    async with _chrome() as chrome:
        # Idle / no tail yet — the row is already reserved.
        assert _widget_row_rows(chrome, above=True) >= 1

        # Tail arrives (3 lines) — grows to the content height.
        chrome.set_widget("__stream__", ["a", "b", "c"], above=True)
        assert _widget_row_rows(chrome, above=True) == 3

        # Tail cleared mid-stream (the key is POPPED, leaving the dict empty).
        # The height must NOT drop to 0 — that is the flicker regression.
        chrome.set_widget("__stream__", None, above=True)
        assert _widget_row_rows(chrome, above=True) >= 1, (
            "widgets-above collapsed to 0 rows when the stream tail cleared; "
            "the mid-stream clear now changes HEIGHT inside the print_above "
            "fold-in instead of being an atomic content update (flicker)"
        )

        # A second chunk re-populates the same row, and clearing it again holds
        # the floor — the row is stable across repeated chunk cycles.
        chrome.set_widget("__stream__", ["next chunk"], above=True)
        assert _widget_row_rows(chrome, above=True) == 1
        chrome.set_widget("__stream__", None, above=True)
        assert _widget_row_rows(chrome, above=True) >= 1


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
