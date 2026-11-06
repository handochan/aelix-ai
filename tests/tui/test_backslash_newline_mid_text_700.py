"""#700 — ``\\`` + Enter inserts a newline wherever the cursor is.

Before #700 the accept handler counted the backslashes at the END of the buffer
and also required the cursor to sit at the end, so ``abc\\|def`` (``|`` the
cursor) sent the line. The idiom (ADR-0121) is about the text the user just
typed: the backslashes right before the cursor.

Each case sets the draft and the cursor on the running chrome, then feeds one
Enter through the real key processor (``\\n`` = c-j, which the accept handler
also binds) and waits for it to be handled either way: a newline in the draft,
or a submitted / steered line.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from _polling import wait_until  # sibling helper (pytest prepend import mode)
from aelix_coding_agent.tui.chrome import AelixChrome
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

BS = "\\"


@asynccontextmanager
async def _chrome() -> AsyncGenerator[tuple[AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        task = asyncio.create_task(chrome.run())
        try:
            yield chrome, pipe
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=3)


async def _press_enter(chrome: AelixChrome, pipe: PipeInput, draft: str, cursor: int) -> None:
    await wait_until(lambda: chrome.app.is_running, what="the chrome application to start")
    before = draft
    chrome.buffer.document = Document(draft, cursor)
    pipe.send_text("\n")
    await wait_until(
        lambda: chrome.buffer.text != before or not chrome._input_queue.empty(),
        what="the Enter to be handled",
    )


@pytest.mark.parametrize(
    ("draft", "cursor", "expected", "expected_cursor"),
    [
        # one backslash right before the cursor, mid-text: the owner's report
        (f"abc{BS}def", 4, "abc\ndef", 4),
        # three: one is consumed, two stay literal
        (f"abc{BS * 3}def", 6, f"abc{BS * 2}\ndef", 6),
        # at the end, as before #700
        (f"abc{BS}", 4, "abc\n", 4),
        # a draft that already has lines
        (f"one\ntwo{BS}three", 8, "one\ntwo\nthree", 8),
        # the backslash ends an earlier line of the draft
        (f"one{BS}\ntwo", 4, "one\n\ntwo", 4),
    ],
)
async def test_an_odd_run_before_the_cursor_inserts_a_newline(
    draft: str, cursor: int, expected: str, expected_cursor: int
) -> None:
    async with _chrome() as (chrome, pipe):
        await _press_enter(chrome, pipe, draft, cursor)
        assert chrome.buffer.text == expected
        assert chrome.buffer.cursor_position == expected_cursor
        assert chrome._input_queue.empty()  # not submitted


@pytest.mark.parametrize(
    ("draft", "cursor"),
    [
        # two before the cursor: a literal backslash, the line is sent
        (f"abc{BS * 2}def", 5),
        # the buffer ends in one, but the cursor is at the start
        (f"abc{BS}", 0),
        # ... or in the middle, after no backslash
        (f"ab{BS}c{BS}", 2),
        (f"abc{BS}", 2),
    ],
)
async def test_no_odd_run_before_the_cursor_submits_the_line_as_typed(
    draft: str, cursor: int
) -> None:
    async with _chrome() as (chrome, pipe):
        fut = asyncio.ensure_future(chrome.get_input())
        await _press_enter(chrome, pipe, draft, cursor)
        assert await asyncio.wait_for(fut, timeout=5) == draft  # every backslash kept


async def test_mid_turn_the_newline_is_inserted_and_nothing_is_steered() -> None:
    # Enter mid-turn steers; the backslash check comes before that branch, so a
    # mid-text ``\\`` + Enter edits the draft and steers nothing.
    async with _chrome() as (chrome, pipe):
        steered: list[str] = []
        chrome.on_steer = lambda line: steered.append(str(line))
        chrome.set_running(True)
        await _press_enter(chrome, pipe, f"abc{BS}def", 4)
        assert chrome.buffer.text == "abc\ndef"
        assert steered == []
        # and the line, finished, still steers
        chrome.buffer.document = Document("abc\ndef", 7)
        pipe.send_text("\n")
        await wait_until(lambda: steered, what="the finished line to steer")
        assert steered == ["abc\ndef"]
