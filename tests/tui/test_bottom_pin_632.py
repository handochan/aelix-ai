"""#632 (ADR-0280) — once the transcript fills the screen, the chrome stays on the last rows.

An in-flow modal (ADR-0159) taller than the rows below the app scrolls the
terminal up, and inline prompt-toolkit moves the app's first row down again only
when a line is printed above it. Before #632 the input, status and footer were
therefore left where the modal had been, with the rows below them empty, until
enough transcript arrived (measured on ``e19ab55e``: 9 to 13 empty rows after
``/model``, ``/settings``, ``/extension``, the delegation consent dialog — the
grids are in ``.omc/specs/632-evidence/``).

These tests drive the real ``AelixChrome`` against a real ``Vt100_Output`` whose
bytes are replayed through ``pyte`` as they are written, with every ``ESC[6n``
answered from the replayed cursor — what a terminal does. So the renderer's
height is known at a real terminal size and the pin triggers as it does live;
``DummyOutput`` reports 40 rows below the cursor on a 40-row screen and never
lets the chrome reach the bottom. One variant answers like Windows instead: no
CPR, and the rows below the cursor read synchronously.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import re
import sys
from collections.abc import AsyncIterator
from typing import Any

import pyte
import pytest
from _polling import wait_until  # sibling helper (pytest prepend import mode)
from aelix_coding_agent.tui import chrome as chrome_mod
from aelix_coding_agent.tui.chrome import _INPUT_PLACEHOLDER, AelixChrome
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.vt100 import Vt100_Output
from pyte.modes import LNM
from rich.console import Console
from rich.text import Text

_FOOTER = "FOOTER-632"
_CPR_ASK = "\x1b[6n"


def _screen(rows: int, cols: int) -> pyte.HistoryScreen:
    """A pyte screen whose LF also returns the carriage, as a tty's ``onlcr``
    makes it for Rich's ``\n`` (the capture has no tty to do that)."""

    screen = pyte.HistoryScreen(cols, rows, history=5000)
    screen.set_mode(LNM)
    return screen


class _TtyCapture(io.StringIO):
    """A capture that says it is a terminal: ``Vt100_Output.responds_to_cpr``
    asks ``stdout.isatty()``, and a plain ``StringIO`` would turn CPR off."""

    def isatty(self) -> bool:
        return True


class _Terminal:
    """The bytes the chrome writes, replayed through pyte as a terminal would.

    ``feed`` replays what arrived since the last call; with ``answer`` set, it
    computes each ``ESC[6n``'s reply (the replayed cursor at that byte) and, by
    ``replies``: writes it to the app's input at once (``"now"``), keeps it in
    ``held`` until :meth:`release` (``"hold"`` — a late answer), or drops it
    (``"drop"`` — no answer). ``resize`` changes the size the output reports
    and the pyte screen together.
    """

    def __init__(self, rows: int, cols: int) -> None:
        self.size = Size(rows=rows, columns=cols)
        self.capture: io.StringIO = _TtyCapture()
        self.screen = _screen(rows, cols)
        self._stream = pyte.Stream(self.screen)
        self._fed = 0
        self.answer: Any = None  # the pipe input, once the app runs
        self.cpr_answered = 0
        self.cpr_dropped = 0
        self.replies = "now"
        self.held: list[str] = []
        self.reads: list[int] = []  # capture length at every feed: a terminal's reads

    def feed(self) -> None:
        data = self.capture.getvalue()[self._fed :]
        self._fed += len(data)
        if data:
            self.reads.append(self._fed)
        parts = data.split(_CPR_ASK)
        for i, part in enumerate(parts):
            self._stream.feed(part)
            if i < len(parts) - 1 and self.answer is not None:
                y, x = self.screen.cursor.y + 1, self.screen.cursor.x + 1
                reply = f"\x1b[{y};{x}R"
                if self.replies == "hold":
                    self.held.append(reply)
                elif self.replies == "drop":
                    self.cpr_dropped += 1
                else:
                    self.answer.send_text(reply)
                    self.cpr_answered += 1

    def release(self, count: int | None = None) -> None:
        """Send the oldest ``count`` held answers (all, and answer at once from
        then on, when ``count`` is None)."""

        if count is None:
            self.replies = "now"
            count = len(self.held)
        for reply in self.held[:count]:
            self.answer.send_text(reply)
            self.cpr_answered += 1
        del self.held[:count]

    def resize(self, rows: int, cols: int) -> None:
        self.feed()
        self.size = Size(rows=rows, columns=cols)
        self.screen.resize(rows, cols)

    def rows(self) -> list[str]:
        self.feed()
        return [line.rstrip() for line in self.screen.display]

    def history(self) -> list[str]:
        cols = self.screen.columns
        return [
            "".join(line[i].data if i in line else " " for i in range(cols)).rstrip()
            for line in self.screen.history.top
        ]

    def dump(self) -> str:
        return "\n".join(f"{i:>2}|{row}" for i, row in enumerate(self.rows()))


class _WindowsLikeOutput(Vt100_Output):
    """A vt100 output that answers like prompt-toolkit's Win32 one.

    No CPR (``responds_to_cpr`` False), and the rows below the cursor read
    synchronously — ``Win32Output.get_rows_below_cursor_position`` is
    ``srWindow.Bottom - dwCursorPosition.Y + 1`` (window rows minus the 0-based
    cursor row).
    """

    def __init__(self, term: _Terminal) -> None:
        super().__init__(
            term.capture, get_size=lambda: term.size, term="xterm-256color", enable_cpr=False
        )
        self._term = term

    def get_rows_below_cursor_position(self) -> int:
        self.flush()
        self._term.feed()
        return self._term.size.rows - self._term.screen.cursor.y


class _LegacyConsoleOutput(_WindowsLikeOutput):
    """A Windows console without VT: prompt-toolkit's legacy ``Win32Output``.

    It moves the cursor and clears the screen through the console API, which
    this fake performs with the vt100 sequences pyte understands — but raw
    text the CHROME writes (``output.write_raw``) is printed as text, as
    ``Win32Output.write_raw`` prints it through ``WriteConsoleW``.
    ``get_win32_screen_buffer_info`` without a ``vt100_output`` is the shape
    the chrome reads a legacy console from.
    """

    def get_win32_screen_buffer_info(self) -> None:
        return None

    def write_raw(self, data: str) -> None:
        if sys._getframe(1).f_code.co_filename.endswith("chrome.py"):
            data = data.replace("\x1b", "^[")
        super().write_raw(data)


def _row_of(rows: list[str], needle: str) -> int | None:
    found = [i for i, row in enumerate(rows) if needle in row]
    return found[-1] if found else None


@contextlib.asynccontextmanager
async def _running(
    rows: int,
    cols: int,
    *,
    windows_like: bool = False,
    legacy_console: bool = False,
    clock: dict[str, float] | None = None,
) -> AsyncIterator[tuple[AelixChrome, _Terminal]]:
    term = _Terminal(rows, cols)
    if legacy_console:
        output: Vt100_Output = _LegacyConsoleOutput(term)
        windows_like = True
    elif windows_like:
        output = _WindowsLikeOutput(term)
    else:
        output = Vt100_Output(
            term.capture, get_size=lambda: term.size, term="xterm-256color", enable_cpr=True
        )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        chrome = AelixChrome(
            console=Console(file=term.capture, force_terminal=True, width=cols),
            pt_input=pipe,
            pt_output=output,
            time_fn=(lambda: clock["now"]) if clock is not None else (lambda: 0.0),
        )
        chrome.set_footer_line(_FOOTER)
        term.answer = None if windows_like else pipe
        stop = asyncio.Event()

        async def _pump() -> None:
            while not stop.is_set():
                term.feed()
                await asyncio.sleep(0.002)

        pump = asyncio.create_task(_pump())
        task = asyncio.create_task(chrome.run())
        try:
            await wait_until(lambda: chrome.app.is_running, what="the chrome to start")
            await wait_until(
                lambda: _row_of(term.rows(), _FOOTER) is not None,
                what="the first painted footer",
                detail=term.dump,
            )
            yield chrome, term
        finally:
            stop.set()
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=5)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(pump, timeout=1)


async def _settled(chrome: AelixChrome, term: _Terminal, *, why: str) -> list[str]:
    """Invalidate, wait for a repaint that started after it, return the grid."""

    app = chrome.app
    before = app.render_counter
    chrome.invalidate()
    await wait_until(
        lambda: app.render_counter > before and not app.invalidated,
        what=f"a settled repaint after {why}",
    )
    await asyncio.sleep(0.02)  # the pump replays the frame's bytes
    return term.rows()


async def _fill_screen(chrome: AelixChrome, term: _Terminal, lines: int = 40) -> None:
    await chrome.print_above_many([Text(f"fill-{i:02d} transcript line") for i in range(lines)])
    await wait_until(
        lambda: chrome._pinned, what="the chrome to pin at the bottom", detail=term.dump
    )


def _modal(rows: int) -> Window:
    return Window(
        FormattedTextControl("\n".join(f"modal-{i:02d}" for i in range(rows))),
        dont_extend_height=True,
    )


async def _open_and_close(chrome: AelixChrome, term: _Terminal, rows: int) -> list[str]:
    chrome.mount_modal(_modal(rows))
    await wait_until(
        lambda: _row_of(term.rows(), f"modal-{rows - 1:02d}") is not None,
        what="the modal on screen",
        detail=term.dump,
    )
    chrome.unmount_modal()
    await wait_until(
        lambda: _row_of(term.rows(), _INPUT_PLACEHOLDER) is not None,
        what="the input row back after the modal closed",
        detail=term.dump,
    )
    return await _settled(chrome, term, why="the modal closed")


# === the pin =================================================================


@pytest.mark.parametrize(("rows", "cols"), [(24, 80), (30, 80), (40, 120)])
async def test_a_full_screen_keeps_the_chrome_on_the_last_rows_after_a_modal(
    rows: int, cols: int
) -> None:
    async with _running(rows, cols) as (chrome, term):
        await _fill_screen(chrome, term)
        grid = await _open_and_close(chrome, term, rows=rows - 8)

        assert _row_of(grid, _FOOTER) == rows - 1, term.dump()
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        assert input_row is not None and input_row >= rows - 5, term.dump()
        # The rows the modal used are now ABOVE the input, not below the footer.
        last_text = _row_of(grid, "fill-39")
        assert last_text is not None and last_text < input_row - 1, term.dump()
        assert all(not row for row in grid[last_text + 1 : input_row - 1]), term.dump()

        # The next print writes into that band; the chrome does not move.
        await chrome.print_above(Text("after-the-modal line"))
        grid = await _settled(chrome, term, why="the next print")
        assert _row_of(grid, _FOOTER) == rows - 1, term.dump()
        assert _row_of(grid, "after-the-modal line") == last_text + 1, term.dump()

        # Nothing lost, nothing printed twice, no blank run pushed into history.
        seen = term.history() + grid
        for i in range(40):
            assert sum(f"fill-{i:02d} " in row for row in seen) == 1, i


async def test_a_screen_the_transcript_has_not_filled_lays_out_as_before() -> None:
    async with _running(24, 80) as (chrome, term):
        await chrome.print_above_many([Text(f"short-{i}") for i in range(3)])
        grid = await _settled(chrome, term, why="three lines")
        footer = _row_of(grid, _FOOTER)
        assert footer is not None and footer < 12, term.dump()

        # A modal that fits: the chrome comes back to the same rows.
        grid = await _open_and_close(chrome, term, rows=5)
        assert _row_of(grid, _FOOTER) == footer, term.dump()
        assert not chrome._pinned

        # A modal that scrolls a screen the transcript has not filled: still not
        # pinned, so the chrome follows the transcript as it did before #632.
        top_before = _row_of(term.rows(), "short-0")
        grid = await _open_and_close(chrome, term, rows=18)
        assert _row_of(grid, "short-0") != top_before, "the modal did not scroll: " + term.dump()
        assert not chrome._pinned
        last_text = _row_of(grid, "short-2")
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        assert last_text is not None and input_row is not None, term.dump()
        assert input_row - last_text <= 3, term.dump()
        assert (_row_of(grid, _FOOTER) or 0) < 23, term.dump()


async def test_a_resize_while_pinned_keeps_the_chrome_on_the_new_last_rows() -> None:
    async with _running(30, 80) as (chrome, term):
        await _fill_screen(chrome, term)
        chrome.mount_modal(_modal(20))
        await wait_until(
            lambda: _row_of(term.rows(), "modal-19") is not None, what="the modal on screen"
        )
        term.resize(24, 80)
        chrome.app._on_resize()  # what prompt-toolkit's SIGWINCH handler calls
        await _settled(chrome, term, why="the resize")
        chrome.unmount_modal()
        await wait_until(
            lambda: _row_of(term.rows(), _INPUT_PLACEHOLDER) is not None,
            what="the input row back after the modal closed",
            detail=term.dump,
        )
        grid = await _settled(chrome, term, why="the modal closed")
        assert chrome._pinned
        assert _row_of(grid, _FOOTER) == 23, term.dump()


# === the settle step and the hold ===========================================


async def test_a_print_into_the_band_paints_the_chrome_once_and_only_on_the_last_rows() -> None:
    async with _running(24, 80) as (chrome, term):
        await _fill_screen(chrome, term)
        await _open_and_close(chrome, term, rows=14)
        assert chrome._band_rows > 0

        start = len(term.capture.getvalue())
        await chrome.print_above(Text("into-the-band line"))
        await _settled(chrome, term, why="the print")
        written = term.capture.getvalue()[start:]

        # Replay the print's bytes from the screen it started on, stopping at
        # every read the terminal made and after every erase, CPR request and
        # bracket end: a terminal WITHOUT CSI 2026 may show any of these states,
        # one WITH it shows the ones at ``?2026l``.
        replay = _screen(24, 80)
        stream = pyte.Stream(replay)
        stream.feed(term.capture.getvalue()[:start])
        marks = r"\x1b\[0?J|\x1b\[6n|\x1b\[\?2026l"
        cuts = sorted(
            {m.end() for m in re.finditer(marks, written)}
            | {r - start for r in term.reads if start < r < start + len(written)}
            | {len(written)}
        )
        prev = 0
        at_bracket_end: list[list[str]] = []
        for cut in cuts:
            stream.feed(written[prev:cut])
            prev = cut
            rows = [line.rstrip() for line in replay.display]
            input_row = _row_of(rows, _INPUT_PLACEHOLDER)
            # Never the input drawn up in the band, under the printed line.
            assert input_row in (None, 20), "\n".join(rows)
            if written[:cut].endswith("\x1b[?2026l"):
                at_bracket_end.append(rows)
        assert len(cuts) >= 4, f"too few states to see a bounce: {cuts}"
        assert at_bracket_end, "the print wrote no CSI 2026 bracket"
        # What a 2026 terminal paints: the whole chrome, on the last rows.
        for rows in at_bracket_end:
            assert _row_of(rows, _FOOTER) == 23, "\n".join(rows)
            assert _row_of(rows, _INPUT_PLACEHOLDER) == 20, "\n".join(rows)


async def test_without_cpr_the_settle_step_leaves_the_repaint_to_in_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _running(24, 80, windows_like=True) as (chrome, term):
        await _fill_screen(chrome, term)
        waits: list[int] = []
        real_wait = chrome.app.renderer.wait_for_cpr_responses

        def counting_wait(*args: Any, **kwargs: Any) -> Any:
            if sys._getframe(1).f_code.co_name == "_settle_pinned":
                waits.append(1)
            return real_wait(*args, **kwargs)

        monkeypatch.setattr(chrome.app.renderer, "wait_for_cpr_responses", counting_wait)
        grid = await _open_and_close(chrome, term, rows=14)
        await chrome.print_above(Text("windows-like line"))
        grid = await _settled(chrome, term, why="the print")

        assert waits == []
        assert term.cpr_answered == 0
        assert _row_of(grid, _FOOTER) == 23, term.dump()
        assert _row_of(grid, _INPUT_PLACEHOLDER) == 20, term.dump()


async def test_a_raising_redraw_drops_the_pin_and_lays_out_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _running(24, 80) as (chrome, term):
        await _fill_screen(chrome, term)
        await _open_and_close(chrome, term, rows=14)
        real_redraw = chrome.app._redraw

        def redraw(*args: Any, **kwargs: Any) -> None:
            if sys._getframe(1).f_code.co_name == "_settle_pinned":
                raise AttributeError("a prompt-toolkit release moved _redraw")
            real_redraw(*args, **kwargs)

        monkeypatch.setattr(chrome.app, "_redraw", redraw)
        await chrome.print_above(Text("first print after the failure"))  # must not raise
        assert not chrome._pinned
        assert chrome._pin_failed
        grid = await _settled(chrome, term, why="the failed settle")
        # Today's layout: the chrome right under the transcript, the rest below.
        last_text = _row_of(grid, "first print after the failure")
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        assert last_text is not None and input_row is not None, term.dump()
        assert input_row - last_text <= 3, term.dump()
        assert (_row_of(grid, _FOOTER) or 0) < 23, term.dump()

        # Never pinned again in this session, however full the screen gets.
        await chrome.print_above_many([Text(f"more-{i}") for i in range(30)])
        await _settled(chrome, term, why="a full screen again")
        assert not chrome._pinned


def test_the_hold_gives_up_after_its_limit() -> None:
    clock = {"now": 0.0}
    output = Vt100_Output(
        io.StringIO(), get_size=lambda: Size(rows=24, columns=80), enable_cpr=True
    )
    with create_pipe_input() as pipe:
        chrome = AelixChrome(
            console=Console(file=io.StringIO()),
            pt_input=pipe,
            pt_output=output,
            time_fn=lambda: clock["now"],
        )
    renderer = chrome.app.renderer
    chrome._pinned = True
    chrome._band_rows = 6
    loop = asyncio.new_event_loop()
    try:
        renderer._waiting_for_cpr_futures.append(loop.create_future())
        _assert_the_hold_gives_up(chrome, clock)
    finally:
        loop.close()


def _assert_the_hold_gives_up(chrome: AelixChrome, clock: dict[str, float]) -> None:
    # Pinned, a band open, a CPR asked and not answered: held, until the limit.
    assert chrome._hold_for_cpr()
    clock["now"] = chrome_mod._CPR_HOLD_LIMIT
    assert chrome._hold_for_cpr()
    clock["now"] = chrome_mod._CPR_HOLD_LIMIT + 0.01
    assert not chrome._hold_for_cpr()

    # No band, not pinned, or the height known: never held.
    chrome._hold_since = None
    chrome._band_rows = 0
    assert not chrome._hold_for_cpr()
    chrome._band_rows = 6
    chrome._pinned = False
    assert not chrome._hold_for_cpr()
    chrome._pinned = True
    chrome.app.renderer._min_available_height = 5
    assert not chrome._hold_for_cpr()


def test_a_dropped_fresh_query_is_replaced_by_the_next_one() -> None:
    # prompt-toolkit's ``wait_for_cpr_responses`` empties the whole queue when
    # it gives up, so the query clear() asked can vanish unanswered. The pin
    # must then wait for the next query a print asks, not for one that never
    # resolves (which would keep the chrome from pinning for the session).
    output = Vt100_Output(
        _TtyCapture(), get_size=lambda: Size(rows=24, columns=80), enable_cpr=True
    )
    with create_pipe_input() as pipe:
        chrome = AelixChrome(console=Console(file=io.StringIO()), pt_input=pipe, pt_output=output)
    renderer = chrome.app.renderer
    loop = asyncio.new_event_loop()
    try:
        cleared = loop.create_future()
        renderer._waiting_for_cpr_futures.append(cleared)
        chrome._arm_fresh_cpr()
        assert chrome._fresh_cpr is cleared
        assert not chrome._fresh_cpr_answered()

        chrome._rearm_fresh_cpr()  # still queued: keep waiting for it
        assert chrome._fresh_cpr is cleared
        renderer._waiting_for_cpr_futures.clear()  # the timeout's reset
        printed = loop.create_future()
        renderer._waiting_for_cpr_futures.append(printed)  # the next print's query
        chrome._rearm_fresh_cpr()
        assert chrome._fresh_cpr is printed
        printed.set_result(None)
        assert chrome._fresh_cpr_answered()
        assert chrome._fresh_cpr is None
    finally:
        loop.close()


# === clear() =================================================================


async def test_clear_unpins_and_starts_the_renderer_over_at_the_top() -> None:
    async with _running(24, 80) as (chrome, term):
        await _fill_screen(chrome, term)
        await _open_and_close(chrome, term, rows=14)
        assert chrome._pinned

        chrome.clear()
        assert not chrome._pinned
        await wait_until(
            lambda: _row_of(term.rows(), _FOOTER) is not None,
            what="the chrome drawn again after clear()",
            detail=term.dump,
        )
        grid = await _settled(chrome, term, why="clear()")
        # The first frame is whole and at the top: the prefixed input row, then
        # the footer — not the old frame's tail drawn from the old cursor.
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        assert input_row is not None and input_row <= 2, term.dump()
        assert grid[input_row].startswith("❯ "), term.dump()
        assert (_row_of(grid, _FOOTER) or 99) <= 5, term.dump()
        assert not chrome._pinned  # not set again from the old screen's numbers

        await chrome.print_above_many([Text(f"after-clear-{i}") for i in range(3)])
        grid = await _settled(chrome, term, why="three lines after clear()")
        assert not chrome._pinned
        assert (_row_of(grid, _FOOTER) or 99) < 12, term.dump()


async def test_the_first_frame_after_clear_is_the_whole_chrome() -> None:
    # Before #632 the renderer kept diffing against the screen clear() had just
    # erased, so only the cells that changed were written: the frame showed
    # the placeholder without its prefix and no footer, from the wrong row.
    async with _running(24, 80) as (chrome, term):
        await chrome.print_above_many([Text(f"before-clear-{i}") for i in range(3)])
        await _settled(chrome, term, why="three lines")
        assert not chrome._pinned

        chrome.clear()
        await _settled(chrome, term, why="clear()")
        await wait_until(
            lambda: _row_of(term.rows(), _FOOTER) is not None,
            what="the footer in the first frame after clear()",
            detail=term.dump,
        )
        grid = term.rows()
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        assert input_row is not None and input_row <= 2, term.dump()
        assert grid[input_row].startswith("❯ "), term.dump()
        assert _row_of(grid, "before-clear-") is None, term.dump()


# === #84's row ===============================================================


def test_the_clear_on_shrink_row_claims_no_behaviour() -> None:
    # #632 left ``clear_on_shrink`` inert (#84). Its help used to say "Clear
    # scrollback when the terminal shrinks" — neither pi's meaning (a full
    # redraw when the CONTENT shrinks) nor what keeps the chrome down now (the
    # pin, which needs no setting). It names where the key comes from and that
    # nothing reads it, inside the 78 cells the detail panel shows uncut.
    from aelix_ai.settings.settings_manager import SettingsManager
    from aelix_coding_agent.tui.context import _PICK_MAX_WIDTH, _visible_len
    from aelix_coding_agent.tui.settings_rows import build_settings_rows

    sm = SettingsManager.in_memory({})
    row = next(r for r in build_settings_rows(sm) if r.key == "clear_on_shrink")
    assert "not yet wired" in row.help
    assert "scrollback" not in row.help.lower()
    assert "bottom" not in row.help.lower()
    assert _visible_len(row.help) <= _PICK_MAX_WIDTH
    assert row.read(sm) == "off"


# === late, missing and stale CPR answers (round 1: C1, C3, C4, C5) ==========


async def _band(chrome: AelixChrome, term: _Terminal) -> None:
    """Pinned, with a band open above the chrome (24 rows: input row 20)."""

    await _fill_screen(chrome, term)
    await _open_and_close(chrome, term, rows=14)
    assert chrome._band_rows > 0, term.dump()


async def _print_with_the_answer_held(
    chrome: AelixChrome, term: _Terminal, text: str
) -> asyncio.Task[None]:
    """Start a print whose CPR answer the terminal holds back; return its task."""

    term.replies = "hold"
    task = asyncio.create_task(chrome.print_above(Text(text)))
    await wait_until(lambda: bool(term.held), what="the print's CPR query", detail=term.dump)
    await wait_until(
        lambda: _row_of(term.rows(), text) is not None, what="the printed line", detail=term.dump
    )
    return task


async def _render_again(chrome: AelixChrome) -> None:
    app = chrome.app
    before = app.render_counter
    chrome.invalidate()
    await wait_until(lambda: app.render_counter > before, what="a repaint while held")
    await asyncio.sleep(0.02)


async def test_while_the_answer_is_late_no_chrome_row_is_drawn_in_the_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A limit no run reaches: the settle waits for the answer, the hold holds.
    monkeypatch.setattr(chrome_mod, "_CPR_HOLD_LIMIT", 60.0)
    async with _running(24, 80) as (chrome, term):
        await _band(chrome, term)
        task = await _print_with_the_answer_held(chrome, term, "late-answer line")

        # The repaint before the answer, and one more while it is still late:
        # nothing of the chrome in the band (it would sit under the printed
        # line and jump down a round trip later), nothing elsewhere either.
        for _ in range(2):
            grid = term.rows()
            assert _row_of(grid, _INPUT_PLACEHOLDER) is None, term.dump()
            assert _row_of(grid, _FOOTER) is None, term.dump()
            assert not task.done()
            await _render_again(chrome)

        term.release()
        await asyncio.wait_for(task, timeout=5)
        grid = await _settled(chrome, term, why="the late answer")
        assert _row_of(grid, _FOOTER) == 23, term.dump()
        assert _row_of(grid, _INPUT_PLACEHOLDER) == 20, term.dump()
        assert chrome._pinned and not chrome._pin_failed


async def test_the_hold_runs_out_and_the_chrome_is_drawn_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chrome_mod, "_CPR_HOLD_LIMIT", 60.0)
    clock = {"now": 0.0}
    async with _running(24, 80, clock=clock) as (chrome, term):
        await _band(chrome, term)
        task = await _print_with_the_answer_held(chrome, term, "slow-link line")
        assert _row_of(term.rows(), _INPUT_PLACEHOLDER) is None, term.dump()

        clock["now"] += chrome_mod._CPR_HOLD_LIMIT + 0.01
        await _render_again(chrome)
        grid = term.rows()
        # Past the limit: the frame prompt-toolkit draws before an answer —
        # the input right under the printed line, the CPR-gated rows absent.
        printed = _row_of(grid, "slow-link line")
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        assert printed is not None and input_row is not None, term.dump()
        assert printed < input_row <= printed + 3, term.dump()
        assert _row_of(grid, _FOOTER) is None, term.dump()

        term.release()
        await asyncio.wait_for(task, timeout=5)
        grid = await _settled(chrome, term, why="the answer")
        assert _row_of(grid, _FOOTER) == 23, term.dump()
        assert _row_of(grid, _INPUT_PLACEHOLDER) == 20, term.dump()


@pytest.mark.parametrize("limit", [None, 60.0], ids=["after-the-limit", "within-a-long-limit"])
async def test_a_missing_cpr_answer_drops_the_pin_for_the_session(
    limit: float | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Either the print stops waiting at the limit and the background end of
    # the wait finds no answer, or (a limit past prompt-toolkit's 1 s) the
    # wait itself returns with no answer.
    if limit is not None:
        monkeypatch.setattr(chrome_mod, "_CPR_HOLD_LIMIT", limit)
    async with _running(24, 80) as (chrome, term):
        await _band(chrome, term)
        term.replies = "drop"
        # The print returns at the limit; prompt-toolkit's wait goes on and
        # gives up after a second, returning normally. No answer, no
        # measurement: the fallback, as for an exception.
        await asyncio.wait_for(chrome.print_above(Text("unanswered line")), timeout=5)
        assert term.cpr_dropped >= 1
        await wait_until(lambda: not chrome._pinned, what="the pin dropped", detail=term.dump)
        assert chrome._pin_failed

        term.replies = "now"
        await chrome.print_above(Text("answered again"))
        grid = await _settled(chrome, term, why="an answered print")
        # Today's layout: the chrome right under the transcript, not pinned.
        printed = _row_of(grid, "answered again")
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        assert printed is not None and input_row is not None, term.dump()
        assert input_row - printed <= 3, term.dump()
        assert (_row_of(grid, _FOOTER) or 0) < 23, term.dump()
        assert not chrome._pinned


async def test_an_answer_past_the_limit_releases_the_print_and_keeps_the_pin() -> None:
    async with _running(24, 80) as (chrome, term):
        await _band(chrome, term)
        term.replies = "hold"
        # The output pump waits on this print: past the limit it goes on, and
        # the chrome is drawn as before #632 until the answer comes.
        await asyncio.wait_for(chrome.print_above(Text("slow-answer line")), timeout=5)
        assert term.held, "no CPR query to hold"
        # Returned while the query is still outstanding — not after the 1 s
        # wait, which would have emptied the queue.
        assert chrome.app.renderer.waiting_for_cpr
        assert chrome._sync_depth == 0  # the bracket is closed
        assert chrome._pinned and not chrome._pin_failed

        term.release()
        await wait_until(
            lambda: _row_of(term.rows(), _FOOTER) == 23,
            what="the chrome on the last rows once the answer came",
            detail=term.dump,
        )
        grid = await _settled(chrome, term, why="the answer")
        assert _row_of(grid, _INPUT_PLACEHOLDER) == 20, term.dump()
        assert chrome._pinned and not chrome._pin_failed


async def test_two_prints_at_once_do_not_read_each_other_as_a_missing_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The pump and a descriptor can print at once. The first print's wait ends
    # on the answer the second one's ``in_terminal`` also waited for; by the
    # time the first looks, the second has asked again. That is not a failure.
    monkeypatch.setattr(chrome_mod, "_CPR_HOLD_LIMIT", 60.0)
    async with _running(24, 80) as (chrome, term):
        await _band(chrome, term)
        first = await _print_with_the_answer_held(chrome, term, "first of two")
        second = asyncio.create_task(chrome.print_above(Text("second of two")))
        await asyncio.sleep(0.05)
        term.release(1)  # the first query's answer; the second's will be held
        await wait_until(lambda: bool(term.held), what="the second print's query")
        await asyncio.wait_for(first, timeout=5)
        assert chrome._pinned and not chrome._pin_failed
        term.release()
        await asyncio.wait_for(second, timeout=5)
        grid = await _settled(chrome, term, why="both prints")
        assert chrome._pinned and not chrome._pin_failed
        assert _row_of(grid, _FOOTER) == 23, term.dump()


async def test_a_2026_terminal_paints_the_chrome_when_the_answer_is_late() -> None:
    # Round-2 M2: past the limit the bracket closes. Still held, the frame a
    # CSI 2026 terminal then painted had no chrome row, for the rest of the
    # round trip (510 ms with answers 0.5 s late, live). The settle ends the
    # hold and repaints first, so the bracket ends on the frame drawn before
    # an answer, as before #632.
    async with _running(24, 80) as (chrome, term):
        await _band(chrome, term)
        start = len(term.capture.getvalue())
        term.replies = "hold"
        await asyncio.wait_for(chrome.print_above(Text("late line")), timeout=5)
        written = term.capture.getvalue()[start:]
        cut = written.rfind("\x1b[?2026l")
        assert cut >= 0, "the print wrote no CSI 2026 bracket"
        replay = _screen(24, 80)
        pyte.Stream(replay).feed(term.capture.getvalue()[: start + cut + len("\x1b[?2026l")])
        rows = [line.rstrip() for line in replay.display]
        dump = "\n".join(f"{i:2d}|{r}" for i, r in enumerate(rows))
        assert _row_of(rows, _INPUT_PLACEHOLDER) is not None, "no chrome row painted:\n" + dump
        assert chrome._pinned and not chrome._pin_failed

        term.release()
        await wait_until(
            lambda: _row_of(term.rows(), _FOOTER) == 23,
            what="the chrome on the last rows once the answer came",
            detail=term.dump,
        )


async def test_a_resize_while_a_late_answer_is_outstanding_keeps_the_pin() -> None:
    # Round-2 M1: the print stopped waiting at the limit; its wait goes on in
    # the background. A resize asks its own CPR before the print's answer
    # comes. That answer arriving is not a missing answer, although the
    # resize's query is still open.
    async with _running(24, 80) as (chrome, term):
        await _band(chrome, term)
        term.replies = "hold"
        await asyncio.wait_for(chrome.print_above(Text("slow-link line")), timeout=5)
        assert term.held and chrome._pinned
        held = len(term.held)
        term.resize(30, 80)
        chrome.app._on_resize()
        await wait_until(lambda: len(term.held) > held, what="the resize's CPR query")
        term.release(1)  # the print's own answer
        await asyncio.sleep(0.2)
        assert chrome._pinned and not chrome._pin_failed, "dropped although the answer came"

        term.release()
        await wait_until(
            lambda: _row_of(term.rows(), _FOOTER) == 29,
            what="the chrome on the new last rows",
            detail=term.dump,
        )
        assert chrome._pinned and not chrome._pin_failed


def test_the_cpr_limit_is_the_measured_tenth_of_a_second() -> None:
    # ADR-0280 D4: a local answer ends the hold in about 7 ms per turn; at
    # 0.5 s the chrome stayed off for 1013 ms per turn with a 0.5 s-late
    # answer, at 0.1 s for 108 ms. A change here is a change to that table.
    assert chrome_mod._CPR_HOLD_LIMIT == 0.1


async def test_an_answer_to_a_query_from_before_clear_does_not_pin_the_empty_screen() -> None:
    async with _running(24, 80) as (chrome, term):
        # Pinned with no band: the print's query is asked with the cursor on
        # the chrome's first row, near the bottom (row 20 of 24).
        await _fill_screen(chrome, term)
        task = await _print_with_the_answer_held(chrome, term, "before-clear line")
        stale = list(term.held)
        assert stale and int(stale[0][2:].split(";")[0]) >= 20, stale

        chrome.clear()  # asks again; the stale answer is still on its way
        await wait_until(lambda: len(term.held) > len(stale), what="clear()'s CPR query")
        # The stale answer (the cursor near the bottom of the old screen)
        # arrives first and is rendered from, then the one to clear()'s query
        # (the top).
        term.release(1)
        await asyncio.wait_for(task, timeout=5)
        await _render_again(chrome)
        assert not chrome._pinned, "pinned from the stale answer:\n" + term.dump()
        term.release()
        grid = await _settled(chrome, term, why="both answers")

        assert not chrome._pinned, term.dump()
        assert not chrome._pin_failed  # clear() made the settle moot, not failed
        assert (_row_of(grid, _FOOTER) or 99) <= 5, term.dump()
        await chrome.print_above_many([Text(f"after-clear-{i}") for i in range(3)])
        grid = await _settled(chrome, term, why="three lines")
        assert not chrome._pinned
        assert (_row_of(grid, _FOOTER) or 99) < 12, term.dump()


async def test_a_resize_during_an_outstanding_query_ends_on_the_new_last_rows() -> None:
    async with _running(24, 80) as (chrome, term):
        await _band(chrome, term)
        task = await _print_with_the_answer_held(chrome, term, "before-resize line")
        held = len(term.held)
        term.resize(30, 80)
        chrome.app._on_resize()  # erases, asks again, repaints
        await wait_until(lambda: len(term.held) > held, what="the resize's CPR query")
        term.release()
        await asyncio.wait_for(task, timeout=5)
        await _settled(chrome, term, why="the answers")
        grid = await _settled(chrome, term, why="a second repaint")
        assert chrome._pinned and not chrome._pin_failed
        assert _row_of(grid, _FOOTER) == 29, term.dump()


async def test_a_print_during_a_dialog_keeps_the_dialog_out_of_the_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_coding_agent.tui.overlay import show_modal
    from prompt_toolkit.key_binding import KeyBindings

    # The hold's clock is frozen at 0: a limit no run reaches keeps the settle
    # from ending the hold at the real 0.1 s while the frames are read.
    monkeypatch.setattr(chrome_mod, "_CPR_HOLD_LIMIT", 60.0)
    async with _running(24, 80) as (chrome, term):
        await _band(chrome, term)

        def build(result: asyncio.Future[Any]) -> Window:
            kb = KeyBindings()

            @kb.add("y")
            def _yes(event: object) -> None:
                result.set_result("yes")

            return Window(
                FormattedTextControl(
                    "dialog-0\ndialog-1\ndialog-2", focusable=True, key_bindings=kb
                ),
                dont_extend_height=True,
            )

        dialog = asyncio.create_task(show_modal(chrome, build))
        await wait_until(
            lambda: _row_of(term.rows(), "dialog-2") is not None,
            what="the dialog",
            detail=term.dump,
        )
        home = await _settled(chrome, term, why="the dialog")
        rows = [_row_of(home, f"dialog-{i}") for i in range(3)]
        assert rows[2] is not None and rows[2] >= 19, term.dump()

        task = await _print_with_the_answer_held(chrome, term, "printed under a dialog")
        for _ in range(2):
            grid = term.rows()
            for i in range(3):
                assert _row_of(grid, f"dialog-{i}") in (None, rows[i]), term.dump()
            await _render_again(chrome)

        term.release()
        await asyncio.wait_for(task, timeout=5)
        grid = await _settled(chrome, term, why="the answer")
        assert [_row_of(grid, f"dialog-{i}") for i in range(3)] == rows, term.dump()
        chrome.app.input.send_text("y")  # type: ignore[attr-defined]
        assert await asyncio.wait_for(dialog, timeout=5) == "yes"


# === a tall draft on a screen the transcript has not filled (F2) ============


async def test_a_tall_draft_does_not_pin_a_screen_the_transcript_has_not_filled() -> None:
    async with _running(34, 80) as (chrome, term):
        await chrome.print_above_many([Text(f"banner-{i}") for i in range(22)])
        await _settled(chrome, term, why="a banner")
        chrome.buffer.text = "\n".join(f"draft line {i}" for i in range(12))
        grid = await _settled(chrome, term, why="a 12-line draft")
        assert _row_of(grid, _FOOTER) == 33, term.dump()  # the draft reached the bottom
        assert not chrome._pinned

        chrome.buffer.text = ""
        grid = await _settled(chrome, term, why="the draft cleared")
        assert not chrome._pinned
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        last = _row_of(grid, "banner-21")
        assert input_row is not None and last is not None, term.dump()
        assert input_row - last <= 3, term.dump()


# === clear() on a legacy Windows console (C2) ===============================


async def test_clear_on_a_legacy_windows_console_draws_the_prompt_once() -> None:
    async with _running(24, 80, legacy_console=True) as (chrome, term):
        await chrome.print_above_many([Text(f"legacy-{i}") for i in range(5)])
        await _settled(chrome, term, why="five lines")

        chrome.clear()
        grid = await _settled(chrome, term, why="clear()")
        prompts = [i for i, row in enumerate(grid) if _INPUT_PLACEHOLDER in row]
        assert len(prompts) == 1, term.dump()
        assert prompts[0] <= 2, term.dump()
        assert _row_of(grid, "legacy-") is None, term.dump()
        assert _row_of(grid, "^[") is None, "raw ANSI printed as text:\n" + term.dump()
