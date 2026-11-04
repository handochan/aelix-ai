"""#670 — the ``@`` menu does not open on a line the input draws in shell mode.

#596 (ADR-0287 D4) stopped colouring ``@`` references in shell mode, since the
shell gets ``@path`` as literal text, but the completer still opened its file
menu there: ``!cat @READ`` + Tab became ``!cat @README.md`` and ran
``cat @README.md`` (No such file or directory), and the failed command and its
output reached the model. The owner's decision (a): no ``@`` menu in shell mode,
as the lexer draws no reference there. Tab path completion for shell lines is
not part of this.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from _polling import wait_until
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.completion import (
    FileMentionCompleter,
    OffLoopFileMentionCompleter,
    wants_completion,
)
from aelix_coding_agent.tui.shell import _build_input_completer
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

# The typed line, and whether the input draws it in shell mode.
_LINES = [
    ("!cat @READ", True),
    ("!!cat @READ", True),
    ("!cat\n@READ", True),
    ('!cat @"READ', True),
    ("!@READ", True),
    ("explain @READ", False),
    (" !cat @READ", False),  # a leading space makes it a prompt (ADR-0287 D1)
    ("@READ", False),
]


def _doc(text: str) -> Document:
    return Document(text, cursor_position=len(text))


@pytest.mark.parametrize(("text", "shell"), _LINES)
def test_completion_while_typing_is_off_on_a_shell_line(text: str, shell: bool) -> None:
    assert wants_completion(text) is not shell


@pytest.fixture
def work(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("hi\n")
    return tmp_path


def _completer(work: Path):  # noqa: ANN202
    return _build_input_completer(lambda: {}, [], str(work), respect_gitignore=lambda: True)


@pytest.mark.parametrize(("text", "shell"), _LINES)
def test_the_input_completer_offers_no_file_on_a_shell_line(
    work: Path, text: str, shell: bool
) -> None:
    event = CompleteEvent(completion_requested=True)  # Tab
    offered = [c.text for c in _completer(work).get_completions(_doc(text), event)]
    file_only = [c.text for c in FileMentionCompleter(str(work)).get_completions(_doc(text), event)]
    if shell:
        assert offered == [] and file_only == []
    else:
        assert any("README.md" in t for t in offered)
        assert any("README.md" in t for t in file_only)


@pytest.mark.parametrize(("text", "shell"), _LINES)
async def test_the_off_loop_menu_offers_no_file_on_a_shell_line(
    work: Path, text: str, shell: bool
) -> None:
    # The path the chrome takes: prompt-toolkit's async completion.
    event = CompleteEvent(completion_requested=True)

    async def collect(completer) -> list[str]:  # noqa: ANN001
        return [c.text async for c in completer.get_completions_async(_doc(text), event)]

    offered = await collect(_completer(work))
    off_loop = await collect(OffLoopFileMentionCompleter(FileMentionCompleter(str(work))))
    if shell:
        assert offered == [] and off_loop == []
    else:
        assert any("README.md" in t for t in offered)
        assert any("README.md" in t for t in off_loop)


# === the chrome ================================================================


@asynccontextmanager
async def _chrome(work: Path) -> AsyncGenerator[tuple[AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome(console=Console(file=io.StringIO(), force_terminal=True, width=80))
        chrome.set_command_completer(_completer(work))
        task = asyncio.create_task(chrome.run())
        try:
            await wait_until(lambda: chrome.app.is_running, what="the chrome to start")
            yield chrome, pipe
        finally:
            chrome.exit()
            await asyncio.wait_for(task, timeout=5)


async def test_tab_on_a_shell_line_leaves_the_command_as_typed(work: Path) -> None:
    async with _chrome(work) as (chrome, pipe):
        # Control first: the same words as a prompt open the menu as they are typed.
        pipe.send_text("cat @READ")
        await wait_until(
            lambda: (
                (state := chrome.buffer.complete_state) is not None
                and any("README.md" in c.text for c in state.completions)
            ),
            what="the @ menu to open on a prompt line",
        )
        chrome.buffer.reset()
        pipe.send_text("!cat @READ\t")
        await wait_until(lambda: chrome.buffer.text.startswith("!cat @READ"), what="typing")
        assert chrome.buffer.complete_while_typing() is False
        # Complete the way Tab does and wait for that completion to finish: it
        # offers nothing, so the command stays as typed.
        completer = chrome.buffer.completer
        assert completer is not None
        offered = [
            c
            async for c in completer.get_completions_async(
                chrome.buffer.document, CompleteEvent(completion_requested=True)
            )
        ]
        assert offered == []
        assert chrome.buffer.text == "!cat @READ"
        assert chrome.buffer.complete_state is None or not chrome.buffer.complete_state.completions
