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
  gates every row behind ``renderer_height_is_known`` (``chrome.py:142``), which
  only flips once a Cursor-Position-Report arrives; without it nothing paints.
  This is the headless analogue of a real terminal answering the CPR query.
- No real sleeps as synchronisation points beyond short event-loop yields; we poll
  ``chrome.app.is_running`` like the existing tests rather than guessing a delay.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import Awaitable, Callable

import pyte
from aelix_coding_agent.tui.chrome import AelixChrome
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console

# Synthetic Cursor-Position-Report: flips ``renderer_height_is_known`` true so the
# chrome rows paint (a real terminal sends this in answer to the renderer's query).
_CPR_RESPONSE = "\x1b[10;1R"


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
            for _ in range(500):
                await asyncio.sleep(0.01)
                if chrome.app.is_running:
                    break
            else:  # pragma: no cover - defensive: app never started
                raise RuntimeError("chrome Application did not start")

            await asyncio.sleep(0.02)  # let the first (gated) paint flush
            pipe.send_text(_CPR_RESPONSE)  # answer the CPR query → height known
            await asyncio.sleep(0.05)
            chrome.invalidate()
            await asyncio.sleep(0.05)  # let the now-ungated repaint flush
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=3)

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


__all__ = ["assert_row_contains", "render_chrome_to_screen"]
