"""WP-2 (ADR-0160) — AelixTUIContext.multiselect headless smoke tests.

Drives the checkbox picker via pipe-input keys under create_app_session, the same
harness the select() dialog tests use.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.footer_data import AelixFooterData
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

_OPTIONS = [
    ("a", "Alpha", "first"),
    ("b", "Bravo", "second"),
    ("c", "Charlie", "third"),
]


@asynccontextmanager
async def _ctx() -> AsyncGenerator[tuple[AelixTUIContext, AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        ctx = AelixTUIContext(chrome, AelixFooterData(cwd="."))
        task = asyncio.create_task(chrome.run())
        try:
            yield ctx, chrome, pipe
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=3)


async def _wait_float(chrome: AelixChrome, *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if chrome.is_modal_open():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("modal not mounted")


async def test_enter_confirms_initial_selection() -> None:
    async with _ctx() as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.multiselect("Pick", _OPTIONS, selected={"a", "c"})
        )
        await _wait_float(chrome)
        pipe.send_text("\r")  # Enter → confirm with no changes
        result = await asyncio.wait_for(fut, timeout=5)
        assert result is not None
        chosen, toggles = result
        assert chosen == {"a", "c"}
        assert toggles == {}


async def test_space_toggles_highlighted_then_enter() -> None:
    async with _ctx() as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.multiselect("Pick", _OPTIONS, selected=set())
        )
        await _wait_float(chrome)
        pipe.send_text(" ")  # toggle cursor row (Alpha) ON
        pipe.send_text("\r")  # confirm
        chosen, _toggles = await asyncio.wait_for(fut, timeout=5)
        assert chosen == {"a"}


async def test_space_untoggles_preselected() -> None:
    async with _ctx() as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.multiselect("Pick", _OPTIONS, selected={"a"})
        )
        await _wait_float(chrome)
        pipe.send_text(" ")  # toggle Alpha OFF (cursor starts at row 0)
        pipe.send_text("\r")
        chosen, _toggles = await asyncio.wait_for(fut, timeout=5)
        assert chosen == set()


async def test_escape_cancels_returns_none() -> None:
    async with _ctx() as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.multiselect("Pick", _OPTIONS, selected={"b"})
        )
        await _wait_float(chrome)
        pipe.send_text("\x1b")  # Esc
        result = await asyncio.wait_for(fut, timeout=5)
        assert result is None


async def test_extra_toggle_round_trips() -> None:
    async with _ctx() as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.multiselect(
                "Pick",
                _OPTIONS,
                selected={"a"},
                extra_toggles=[("flag", "A flag")],
            )
        )
        await _wait_float(chrome)
        # 3 options then the toggle row: down x3 → cursor on the toggle.
        pipe.send_text("\x1b[B\x1b[B\x1b[B")  # 3 × down arrow
        pipe.send_text(" ")  # toggle the flag ON
        pipe.send_text("\r")
        chosen, toggles = await asyncio.wait_for(fut, timeout=5)
        assert chosen == {"a"}
        assert toggles == {"flag": True}


async def test_extra_toggle_seeded_initial_state_round_trips() -> None:
    # WP-8: a ``(key, label, initial)`` triple seeds the toggle's checked state
    # from a persisted value. Confirming WITHOUT touching it must return True
    # (not silently reset to False) — the data-loss guard for /statusline.
    async with _ctx() as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.multiselect(
                "Pick",
                _OPTIONS,
                selected={"a"},
                extra_toggles=[("flag", "A flag", True)],
            )
        )
        await _wait_float(chrome)
        pipe.send_text("\r")  # confirm with no changes
        chosen, toggles = await asyncio.wait_for(fut, timeout=5)
        assert chosen == {"a"}
        assert toggles == {"flag": True}


async def test_type_to_filter_then_toggle() -> None:
    async with _ctx() as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.multiselect("Pick", _OPTIONS, selected=set())
        )
        await _wait_float(chrome)
        pipe.send_text("char")  # filters to "Charlie" → cursor on it
        pipe.send_text(" ")  # toggle Charlie ON
        pipe.send_text("\r")
        chosen, _toggles = await asyncio.wait_for(fut, timeout=5)
        assert chosen == {"c"}
