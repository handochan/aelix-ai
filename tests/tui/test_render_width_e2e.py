"""Issue #166 step 2 — make render width FALSIFIABLE before changing it.

Why this file exists before the fix does. The suite structurally cannot observe
this class of bug today: inside the venv ``sys.stdout.isatty()`` is False and
``shutil.get_terminal_size()`` returns Python's own fallback of 80 — the very
number under test — and no test in the repo opens a PTY. So a dynamic-width
implementation would resolve to 80 in every test process and the suite would go
green having proven nothing.

The ``_pyte`` harness is immune to that: it injects a fake ``Size`` into BOTH the
prompt-toolkit output and the Rich console, then replays the real escape stream
through a terminal emulator. Its ``cols`` parameter has existed since Sprint
6h₁₀d and has never been passed anything but its default 80.

Two of the tests below landed as ``xfail(strict=True)`` describing behaviour that
did not exist yet, and the markers were deleted by the commit that delivered it —
which is the point: a gate that passes before AND after a fix measures nothing.
Strict meant the suite would BREAK the moment they started passing, so the fix
could not land quietly.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from aelix_agent_core.types import MessageStartEvent
from aelix_ai.messages import AssistantMessage
from aelix_ai.streaming import TextDeltaEvent, TextEndEvent

if TYPE_CHECKING:
    from aelix_coding_agent.tui.chrome import AelixChrome

# One long unbroken paragraph: no markdown structure, so every rendered row is a
# pure wrapping decision. Long enough to wrap several times even at 200 columns.
_PARAGRAPH = (
    "The sea breathes in slow ancient rhythms and its surface shimmers with a "
    "silver light that shifts with every passing cloud while waves roll endlessly "
    "toward the shore folding over themselves in white cascades before drawing "
    "back again whispering over smooth worn stones and beneath the restless blue "
    "whole worlds drift in quiet darkness where currents thread through forests "
    "of kelp and valleys carved by patient time."
)

# ``breathes`` sits in the paragraph's first line and nowhere else on screen, so
# it identifies the committed assistant text among banner/chrome rows.


def _msg_update(stream_event: Any) -> Any:
    from aelix_agent_core.types import MessageUpdateEvent

    return MessageUpdateEvent(message=AssistantMessage(), assistant_message_event=stream_event)


async def _render_paragraph_at(cols: int) -> list[str]:
    """Run the real ``run_tui`` at *cols* and stream one paragraph through it."""

    from _pyte import render_shell_to_screen  # sibling helper
    from test_run_tui_smoke import FakeHarness, FakeRuntime  # sibling module

    runtime = FakeRuntime(FakeHarness())

    async def drive(_chrome: AelixChrome) -> None:
        # The renderer subscribed to the harness during run_tui startup; push a
        # turn's worth of stream events through exactly that seam.
        listener = runtime.harness.subscribers[0]
        listener(MessageStartEvent(message=AssistantMessage()))  # type: ignore[operator]
        listener(_msg_update(TextDeltaEvent(delta=_PARAGRAPH)))  # type: ignore[operator]
        listener(_msg_update(TextEndEvent(content=_PARAGRAPH)))  # type: ignore[operator]
        await asyncio.sleep(0.05)

    return await render_shell_to_screen(runtime=runtime, rows=24, cols=cols, drive=drive)


def _first_paragraph_row_width(display: list[str]) -> int:
    """Cells occupied by the FIRST rendered row of the streamed paragraph.

    Only the first row is measured, and that restriction is load-bearing rather
    than lazy. ``print_above`` commits inside ``in_terminal()``, which erases the
    chrome and repaints it around every write; ``pyte.Screen`` has no scrollback
    and models that as overwriting in place, so the paragraph's LATER rows come
    back with large leading-space runs and partial content — measured artifacts
    of cursor positioning, not of wrapping. (A first pass at this file asserted
    ``max(len(row.rstrip()))`` over all matching rows and got 193 on a
    200-column terminal from a row whose first 120 cells were blank. It reported
    the bug as already fixed.)

    The first row is written immediately after the erase, at column 0, and is
    stable: measured 56 / 79 / 79 for terminals of 60 / 80 / 200 columns.
    """

    row = next((r for r in display if "breathes" in r), None)
    assert row is not None, "the streamed paragraph never reached the grid"
    return len(row.rstrip())


# === invariant that holds today and must keep holding =======================


@pytest.mark.parametrize("cols", [60, 80, 200])
async def test_streamed_text_never_overflows_the_terminal(cols: int) -> None:
    """The rendered row must never be wider than the terminal, at any width.

    Passes today; it is the guard rail for step 3. This is the assertion that
    catches a dynamic width reading the wrong number — in particular one taken
    from Rich (which consults ``$COLUMNS`` before the ioctl) rather than from
    prompt-toolkit, which is how the chrome and the scrollback would end up
    laid out at two different widths in one frame.
    """

    width = _first_paragraph_row_width(await _render_paragraph_at(cols))
    assert width <= cols, f"a streamed row was {width} cells on a {cols}-column terminal"


# === the defect itself, pinned as xfail until step 3 ========================


async def test_wide_terminal_uses_more_than_eighty_columns() -> None:
    """On a 200-column terminal the answer must not sit in an 80-cell ribbon.

    Was 79 cells at cols=200 — identical to cols=80, leaving 120 columns of the
    user's terminal unused. This was xfail(strict) until the width became a live
    reader.
    """

    width = _first_paragraph_row_width(await _render_paragraph_at(200))
    assert width > 80, f"the streamed row was {width} cells on a 200-column terminal"


async def test_run_tui_sizes_the_event_renderer_from_the_live_terminal() -> None:
    """The wiring half: ``run_tui`` must not hand the renderer a constant.

    Spies on the ``EventRenderer`` the shell actually constructs and resolves
    whatever it was given for ``width`` — an int today, a callable after step 3.
    """

    import io

    import aelix_coding_agent.tui.shell as shell_mod
    from aelix_coding_agent.tui.chrome import AelixChrome
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output.vt100 import Vt100_Output
    from test_run_tui_smoke import FakeHarness, FakeRuntime

    cols = 111  # not 80, and below the readability ceiling so it passes through
    widths: list[Any] = []
    real_cls = shell_mod.EventRenderer

    class _SpyRenderer(real_cls):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            widths.append(kwargs.get("width"))
            super().__init__(*args, **kwargs)

    shell_mod.EventRenderer = _SpyRenderer  # type: ignore[misc]
    try:
        capture = io.StringIO()
        output = Vt100_Output(
            capture,
            get_size=lambda: Size(rows=24, columns=cols),
            term="xterm-256color",
            enable_cpr=True,
        )
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
            chrome = AelixChrome(pt_input=pipe, pt_output=output)
            runtime = FakeRuntime(FakeHarness())
            task = asyncio.ensure_future(
                shell_mod.run_tui(
                    runtime,  # type: ignore[arg-type]
                    cwd=".",
                    chrome=chrome,
                    install_signal_handlers=False,
                )
            )
            for _ in range(500):
                await asyncio.sleep(0.01)
                if chrome.app.is_running:
                    break
            pipe.send_text("/quit\n")
            await asyncio.wait_for(task, timeout=5)
    finally:
        shell_mod.EventRenderer = real_cls  # type: ignore[misc]

    assert widths, "run_tui built no EventRenderer"
    given = widths[0]
    # A CALLABLE, not a number: ``_new_stream`` re-resolves it per assistant
    # message, which is what makes a resize take effect at all.
    assert callable(given), f"run_tui passed a fixed width ({given!r})"
    assert given() == cols, f"EventRenderer resolved {given()} on a {cols}-column terminal"
