"""#66 item 3 (corrected) — the chat input row hides while an in-flow modal is
open, and a 1-row spacer sits above the input only while a turn is working.

The original #66 item 3 made pickers FULL-SCREEN to hide scrollback. That was
wrong. The real intent: when a picker / settings panel opens the user types INTO
the panel, so the chat INPUT ROW ("❯ Type your message or @path/to/file") is
unused and confusing — hide ONLY that row. The chat scrollback keeps its natural
height, exactly as before (short panels render at content height, scrollback
stays visible above).

Two layers of coverage:

- pyte render (real painted grid): with a modal mounted the input placeholder is
  ABSENT and the modal is painted; with no modal the placeholder is PRESENT (the
  input returns). While working, a blank spacer sits between the working row and
  the input.
- filter-level: the ``ConditionalContainer`` filters that gate the input row and
  the working spacer evaluate correctly as ``_modal`` / ``_running`` /
  ``_working_visible`` toggle. ``renderer_height_is_known`` is already True in the
  headless ``DummyOutput`` session (verified), so the spacer's composite filter is
  exercised end-to-end here too.
"""

from __future__ import annotations

import io

from _pyte import assert_row_contains, render_chrome_to_screen  # sibling helper
from aelix_coding_agent.tui.chrome import _INPUT_PLACEHOLDER, AelixChrome
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import DummyOutput
from rich.console import Console


def _find_input_and_spacer(
    chrome: AelixChrome,
) -> tuple[ConditionalContainer, ConditionalContainer]:
    """Locate the input ``ConditionalContainer`` (content is the raw input Window)
    and the working spacer (the ``ConditionalContainer`` immediately above it)."""

    body = chrome.app.layout.container.content  # type: ignore[attr-defined]  # FloatContainer.content == HSplit
    kids = body.children
    idx = next(i for i, c in enumerate(kids) if getattr(c, "content", None) is chrome._input_window)
    input_cc = kids[idx]
    spacer_cc = kids[idx - 1]
    assert isinstance(input_cc, ConditionalContainer)
    assert isinstance(spacer_cc, ConditionalContainer)
    return input_cc, spacer_cc


# === pyte render — real painted grid ====================================


async def test_input_row_hidden_while_modal_open() -> None:
    # A mounted in-flow modal paints, and the input placeholder row disappears.
    def build(chrome: AelixChrome) -> None:
        chrome.mount_modal(Window(FormattedTextControl("MODAL-PANEL-XYZ"), height=1))

    display = await render_chrome_to_screen(build_state=build)
    assert_row_contains(display, "MODAL-PANEL-XYZ")  # modal painted at content height
    assert not any(_INPUT_PLACEHOLDER in row for row in display), (
        "input row must be hidden while a modal is open"
    )


async def test_input_row_visible_without_modal() -> None:
    # No modal → the input placeholder row is present (it returns after unmount).
    def build(chrome: AelixChrome) -> None:
        return None

    display = await render_chrome_to_screen(build_state=build)
    assert_row_contains(display, _INPUT_PLACEHOLDER)


async def test_working_spacer_sits_between_working_and_input() -> None:
    # While working, a blank spacer row separates the working row and the input:
    # input_row == working_row + 2, and the row between them is blank.
    def build(chrome: AelixChrome) -> None:
        chrome.set_working_visible(True)
        chrome.set_working_message("Crunching")
        chrome.set_running(True)

    display = await render_chrome_to_screen(build_state=build)
    working_row = assert_row_contains(display, "Crunching")
    input_row = assert_row_contains(display, _INPUT_PLACEHOLDER)
    assert input_row == working_row + 2, (
        f"expected a 1-row spacer between working ({working_row}) and input "
        f"({input_row})"
    )
    assert display[working_row + 1].strip() == "", "the spacer row must be blank"


# === filter-level — ConditionalContainer filters toggle =================


async def test_input_filter_hidden_on_modal_returns_on_unmount() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome(console=Console(file=io.StringIO(), force_terminal=True, width=80))
        input_cc, _spacer_cc = _find_input_and_spacer(chrome)

        assert chrome.is_modal_open() is False
        assert input_cc.filter() is True  # visible when idle
        chrome.mount_modal(Window(height=1))
        assert chrome.is_modal_open() is True
        assert input_cc.filter() is False  # hidden while a modal is open
        chrome.unmount_modal()
        assert chrome.is_modal_open() is False
        assert input_cc.filter() is True  # returns after unmount


async def test_working_spacer_filter_only_when_working() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome(console=Console(file=io.StringIO(), force_terminal=True, width=80))
        _input_cc, spacer_cc = _find_input_and_spacer(chrome)

        assert spacer_cc.filter() is False  # idle → no gap
        chrome.set_running(True)
        assert spacer_cc.filter() is True  # running → gap appears
        chrome.set_running(False)
        assert spacer_cc.filter() is False
        chrome.set_working_visible(True)
        assert spacer_cc.filter() is True  # working-visible → gap appears
        chrome.set_working_visible(False)
        assert spacer_cc.filter() is False  # back to idle → gap gone
