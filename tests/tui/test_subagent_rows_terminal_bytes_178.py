"""#178 — a delegated child's own strings, through the REAL chrome, to the BYTES
the terminal receives.

The per-child status row is the one surface of #178 the shipped TUI actually
paints: ``SubagentProgressBridge`` → ``ui.set_status`` → ``chrome._render_status``,
which replaces ``\\n`` and nothing else, then ``FormattedTextControl(ANSI(row))``.
prompt_toolkit's ``ANSI`` parser obeys SGR and passes whatever sits between
``\\x01`` and ``\\x02`` to the terminal untouched as a ``ZeroWidthEscape``.
MEASURED on ``8f7d98aa``: a ``current_tool`` carrying
``\\x01\\x1b]52;c;…\\x07\\x02`` put an OSC 52 clipboard write into the output
stream, and ``\\x1b[31m`` painted the rest of the row red.

So the assertions here are on the captured ``Vt100_Output`` stream and on the
cell attributes ``pyte`` replays from it, never on the string handed to
``set_status`` — that string is what the unit rows in
``tests/agents_ext/test_child_authored_strings_178.py`` already pin.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from typing import Any

import pyte
from _polling import wait_until  # sibling helper (pytest prepend import mode)
from _pyte import await_first_paint, repaint_settled  # sibling helper
from aelix_agents.progress import SubagentProgressBridge
from aelix_coding_agent.subagent_contract import SubagentProgress
from aelix_coding_agent.tui.chrome import AelixChrome
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console

_ROWS, _COLS = 24, 120
_CPR_RESPONSE = "\x1b[10;1R"
_OSC52 = "\x1b]52;c;SGVsbG8=\x07"


class _ChromeUi:
    """The two ``ExtensionUIContext`` verbs the bridge calls, forwarded to a real
    chrome — the same two ``tui/context.py`` forwards in production."""

    def __init__(self, chrome: AelixChrome) -> None:
        self.chrome = chrome

    def set_status(self, key: str, text: str | None) -> None:
        self.chrome.set_status(key, text)

    def set_widget(self, key: str, content: list[str] | None, options: object = None) -> None:
        self.chrome.set_widget(key, content)


class _Runtime:
    def __init__(self, ui: Any) -> None:
        self.ui = ui


class _Bus:
    def emit(self, channel: str, payload: object) -> None:
        return None


class _Api:
    def __init__(self, ui: Any) -> None:
        self.events = _Bus()
        self.runtime = _Runtime(ui)


async def _paint(drive: Any) -> tuple[str, pyte.Screen]:
    """One settled frame of a real chrome after ``drive(bridge)``; the raw
    output stream and the ``pyte`` screen it leaves behind."""

    capture = io.StringIO()
    output = Vt100_Output(
        capture,
        get_size=lambda: Size(rows=_ROWS, columns=_COLS),
        term="xterm-256color",
        enable_cpr=True,
    )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        chrome = AelixChrome(
            console=Console(file=io.StringIO(), force_terminal=True, width=_COLS),
            pt_input=pipe,
            pt_output=output,
            time_fn=lambda: 0.0,
        )
        drive(SubagentProgressBridge(_Api(_ChromeUi(chrome))))
        task = asyncio.create_task(chrome.run())
        try:
            await await_first_paint(chrome)
            pipe.send_text(_CPR_RESPONSE)
            renderer = chrome.app.renderer
            await wait_until(
                lambda: renderer.height_is_known,
                what="the synthetic CPR answer to reach the renderer",
            )
            await repaint_settled(chrome, why="the CPR answer ungated the chrome rows")
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=3)

    raw = capture.getvalue()
    screen = pyte.Screen(_COLS, _ROWS)
    pyte.Stream(screen).feed(raw)
    return raw, screen


def _cells_of(screen: pyte.Screen, text: str) -> list[Any]:
    for y, row in enumerate(screen.display):
        x = row.find(text)
        if x >= 0:
            return [screen.buffer[y][x + i] for i in range(len(text))]
    rendered = "\n".join(f"{i:>2} {row.rstrip()!r}" for i, row in enumerate(screen.display))
    raise AssertionError(f"{text!r} is on no row of the painted screen:\n{rendered}")


async def test_a_child_tool_name_cannot_write_the_clipboard_or_paint_the_status_row() -> None:
    hostile = SubagentProgress(
        id="sub-1",
        profile="scout",
        state="running",
        current_tool=f"read\x1b[31mFAKE\x01{_OSC52}\x02",
        elapsed_ms=1_000,
    )
    raw, screen = await _paint(lambda bridge: bridge(hostile))

    # Positive control: the row really was painted, so the absences below are
    # about the escape, not about an empty frame.
    cells = _cells_of(screen, "FAKE")
    assert "\x1b]52;" not in raw
    assert all(cell.fg == "default" for cell in cells), [cell.fg for cell in cells]


async def test_a_profile_name_cannot_clear_the_screen_from_the_status_row() -> None:
    hostile = SubagentProgress(
        id="sub-1",
        profile="scout\x1b[2J‮",
        state="running",
        current_tool="read",
        elapsed_ms=1_000,
    )
    raw, screen = await _paint(lambda bridge: bridge(hostile))

    _cells_of(screen, "agent scout")
    assert "‮" not in raw
    # ``\x1b[2J`` is also what prompt_toolkit's own erase would be, so the
    # child's copy is detected by its neighbour: the literal profile text.
    assert "scout\x1b[2J" not in raw


async def test_a_batch_panel_row_cannot_carry_a_bidi_override() -> None:
    """The widget already flattened C0/DEL/C1; #178 moved it onto the shared
    helper, which is what deletes U+202E. Measured on ``8f7d98aa``: it was in
    the panel row."""

    members = [
        SubagentProgress(
            id=f"sub-{i}",
            profile="scout",
            state="running",
            current_tool="read‮evil",
            elapsed_ms=1_000,
        )
        for i in range(2)
    ]

    def drive(bridge: SubagentProgressBridge) -> None:
        bridge.begin_group("tc-1", expected=2)
        for index, member in enumerate(members):
            bridge.adopt(member.id, "tc-1", index=index)
            bridge(member)

    raw, screen = await _paint(drive)

    _cells_of(screen, "[1/2] running · readevil")
    assert "‮" not in raw
