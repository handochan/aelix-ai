"""Sprint 6h₁₀d (ADR-0107) §C — pyte snapshot harness for the live chrome.

Renders the *actual* escape stream of the long-running :class:`AelixChrome`
``Application`` into a captured buffer via a real ``Vt100_Output`` (no real TTY),
then replays that stream through a headless ``pyte`` terminal emulator and returns
the resulting screen grid. This validates the genuine rendered terminal layout —
catching cursor / region / wrapping issues that the ``DummyOutput`` headless tests
in ``test_chrome.py`` cannot see.

Determinism (mirrors the ``test_chrome.py`` idioms — read it for the base pattern):

- Fixed ``rows`` / ``cols`` via ``Vt100_Output(get_size=lambda: Size(...))``.
- Injected clock (``time_fn``) so the working-spinner frame is stable.
- ``enable_cpr=True`` on the output, then we *feed a synthetic CPR response*
  (``\x1b[<row>;<col>R``) on the pipe input once the app is running. The chrome
  gates every row behind ``renderer_height_is_known`` (``chrome.py:536-552``), which
  only flips once a Cursor-Position-Report arrives; without it nothing paints.
  This is the headless analogue of a real terminal answering the CPR query.
- No wall-clock sleeps as synchronisation points (#315). Every step waits for
  the EVENT it needs, through the shared :func:`_polling.wait_until`: the app
  running, its first (gated) paint (``render_counter``), the CPR answer landing
  (``renderer.height_is_known``), and a repaint that finished after the last
  state change. :func:`render_shell_to_screen` additionally polls the replayed
  GRID for the row its caller is about to assert on. The fixed 20/50/600 ms
  waits these replaced were bets on the runner's speed, and the windows-latest
  leg lost one ("the streamed paragraph never reached the grid").
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import Awaitable, Callable

import pyte
from _polling import wait_until  # sibling helper (pytest prepend import mode)
from aelix_coding_agent.tui.chrome import AelixChrome
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console

# Synthetic Cursor-Position-Report: flips ``renderer_height_is_known`` true so the
# chrome rows paint (a real terminal sends this in answer to the renderer's query).
_CPR_RESPONSE = "\x1b[10;1R"


async def await_first_paint(chrome: AelixChrome) -> None:
    """The app is up AND has painted once — the paint that sends the CPR query.

    Answering before that query exists is what the old fixed 20 ms bet against.
    """

    app = chrome.app
    await wait_until(lambda: app.is_running, what="the chrome Application to start")
    await wait_until(
        lambda: app.render_counter > 0, what="the chrome's first (height-gated) paint"
    )


async def repaint_settled(chrome: AelixChrome, *, why: str) -> None:
    """Invalidate, then wait for a repaint that STARTED after the invalidate.

    ``Application._redraw`` bumps ``render_counter`` and then writes and flushes
    the frame synchronously in the same call, so by the time this poll can
    observe the new count the frame is in the capture. ``not invalidated`` adds
    "and nothing scheduled another one meanwhile", i.e. the frame is the
    settled one rather than a stale predecessor.
    """

    app = chrome.app
    before = app.render_counter
    chrome.invalidate()
    await wait_until(
        lambda: app.render_counter > before and not app.invalidated,
        what=f"a settled repaint after {why}",
    )


def _replay(
    raw: str, *, rows: int, cols: int, include_history: bool, history_lines: int
) -> list[str]:
    """Replay a captured escape stream through pyte; the grid it leaves behind."""

    if include_history:
        history_screen = pyte.HistoryScreen(cols, rows, history=history_lines)
        pyte.Stream(history_screen).feed(raw)
        scrollback = [
            "".join(line[i].data if i in line else " " for i in range(cols))
            for line in history_screen.history.top
        ]
        return [*scrollback, *history_screen.display]
    screen = pyte.Screen(cols, rows)
    pyte.Stream(screen).feed(raw)
    return list(screen.display)


def _grid_dump(display: list[str]) -> str:
    return "last grid seen:\n" + "\n".join(
        f"{i:>3} {row.rstrip()!r}" for i, row in enumerate(display) if row.strip()
    )


async def render_chrome_to_screen(
    *,
    rows: int = 24,
    cols: int = 80,
    build_state: Callable[[AelixChrome], Awaitable[None] | None],
    time_fn: Callable[[], float] = lambda: 0.0,
) -> list[str]:
    """Drive the chrome to one painted frame; return ``pyte`` ``screen.display``.

    ``build_state`` receives the constructed (not-yet-running) chrome and applies
    state (``set_footer_line`` / ``set_status`` / descriptor ``registry.apply`` /
    ``add_float`` / …). It may be sync or async. The returned list is one string
    per terminal row (trailing-padded to ``cols``); use :func:`assert_row_contains`
    or scan it directly.
    """

    capture = io.StringIO()
    output = Vt100_Output(
        capture,
        get_size=lambda: Size(rows=rows, columns=cols),
        term="xterm-256color",
        enable_cpr=True,
    )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        chrome = AelixChrome(
            console=Console(file=io.StringIO(), force_terminal=True, width=cols),
            pt_input=pipe,
            pt_output=output,
            time_fn=time_fn,
        )
        result = build_state(chrome)
        if asyncio.iscoroutine(result):
            await result

        task = asyncio.create_task(chrome.run())
        try:
            await await_first_paint(chrome)
            pipe.send_text(_CPR_RESPONSE)  # answer the CPR query → height known
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

    screen = pyte.Screen(cols, rows)
    pyte.Stream(screen).feed(capture.getvalue())
    return list(screen.display)


async def render_shell_to_screen(
    *,
    runtime: object,
    rows: int = 24,
    cols: int = 80,
    drive: Callable[[AelixChrome], Awaitable[None]],
    settled: Callable[[list[str]], bool],
    settled_what: str,
    time_fn: Callable[[], float] = lambda: 0.0,
    settings_manager: object | None = None,
    include_history: bool = False,
    history_lines: int = 3000,
) -> list[str]:
    """Like :func:`render_chrome_to_screen`, but drives the REAL ``run_tui``.

    Issue #166 — the render width a user actually sees is chosen inside
    ``run_tui`` (it builds the :class:`EventRenderer`), so a harness that
    constructs its own chrome can never observe it. This one hands ``run_tui``
    the captured chrome and lets it wire everything itself; *drive* then pushes
    events through ``runtime.harness``'s subscribers exactly as a real turn
    would, and the committed output lands in scrollback via the production
    ``print_above`` pump.

    One deliberate difference from :func:`render_chrome_to_screen`: the injected
    Rich console writes to the SAME buffer as the vt100 output. In production
    the chrome's console is a bare ``Console()`` on the real stdout — the same
    stream the renderer writes to — so pointing both at one capture is the
    faithful arrangement, and it is what makes committed scrollback visible to
    ``pyte`` at all. (``render_chrome_to_screen`` sends it to a throwaway
    buffer, which is why chrome-only snapshots never show committed lines.)

    #315 — ``settled`` is REQUIRED, and it is the whole fix. Committed output
    reaches the capture through an async pump (queue → ``print_above_many`` →
    ``in_terminal``) that takes several loop turns, so "wait a bit, then
    assume it flushed" is a bet — ``drive`` used to sleep 50 ms and this
    helper 600 ms more, and on the windows-latest leg the paragraph was not
    there yet. Now the capture is replayed and ``settled(grid)`` polled until
    the row the caller is about to assert on has actually landed (the same
    grid shape this function returns), under the shared anti-hang bound;
    ``settled_what`` names that row in the failure, next to the bound, the
    wall clock spent and the last grid seen. There is deliberately no default:
    a caller that cannot say what it waits for would be back to a sleep.
    """

    from aelix_coding_agent.tui.shell import (
        run_tui,  # local: avoids a heavy import for chrome-only tests
    )

    capture = io.StringIO()
    output = Vt100_Output(
        capture,
        get_size=lambda: Size(rows=rows, columns=cols),
        term="xterm-256color",
        enable_cpr=True,
    )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        chrome = AelixChrome(
            console=Console(file=capture, force_terminal=True, width=cols),
            pt_input=pipe,
            pt_output=output,
            time_fn=time_fn,
        )
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                settings_manager=settings_manager,  # type: ignore[arg-type]
            )
        )
        try:
            await await_first_paint(chrome)
            # Answered but not awaited: every ``print_above`` resets the
            # renderer, so ``height_is_known`` can legitimately drop again, and
            # nothing asserted through this harness reads the chrome rows it
            # gates — only the committed scrollback, which is ungated.
            pipe.send_text(_CPR_RESPONSE)

            await drive(chrome)

            last: list[list[str]] = [[]]

            def _landed() -> bool:
                last[0] = _replay(
                    capture.getvalue(),
                    rows=rows,
                    cols=cols,
                    include_history=include_history,
                    history_lines=history_lines,
                )
                return settled(last[0])

            await wait_until(
                _landed, what=settled_what, detail=lambda: _grid_dump(last[0])
            )
            # No repaint after this, deliberately. Committed scrollback is
            # written synchronously by ``print_above``, so it is final the
            # moment ``settled`` sees it; a forced repaint here only paints the
            # chrome's rows under it and scrolls the top of a long commit off a
            # 24-row pyte screen (measured: the 60-column paragraph lost its
            # first row that way).
        finally:
            pipe.send_text("/quit\n")
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=5)

    if include_history:
        history_screen = pyte.HistoryScreen(cols, rows, history=history_lines)
        pyte.Stream(history_screen).feed(capture.getvalue())
        top = history_screen.history.top
        if len(top) >= history_lines:
            raise AssertionError(
                f"pyte scrollback saturated at {history_lines} lines — the oldest "
                "rows were evicted, so anything asserted about the TOP of this "
                "transcript would be measuring the buffer, not the render. "
                "Raise history_lines or shrink the fixture."
            )
        scrollback = [
            "".join(line[i].data if i in line else " " for i in range(cols))
            for line in top
        ]
        return [*scrollback, *history_screen.display]

    screen = pyte.Screen(cols, rows)
    pyte.Stream(screen).feed(capture.getvalue())
    return list(screen.display)


def assert_row_contains(display: list[str], text: str) -> int:
    """Assert *text* appears in some row of *display*; return that row index."""

    for index, row in enumerate(display):
        if text in row:
            return index
    rendered = "\n".join(f"{i:>2} {row!r}" for i, row in enumerate(display))
    raise AssertionError(f"{text!r} not found in any rendered row:\n{rendered}")


__all__ = [
    "assert_row_contains",
    "await_first_paint",
    "render_chrome_to_screen",
    "render_shell_to_screen",
    "repaint_settled",
]
