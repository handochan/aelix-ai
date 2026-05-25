"""Sprint 6h₁₀a (ADR-0104) — input parsing + PromptSession builder tests.

``parse_input_line`` is exhaustively truth-tabled (pure). ``build_prompt_session``
is exercised headlessly with a prompt-toolkit pipe input + DummyOutput (no TTY).
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.tui.input import (
    ParsedInput,
    build_prompt_session,
    parse_input_line,
)
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput


@pytest.mark.parametrize(
    ("line", "kind", "text"),
    [
        ("", "empty", ""),
        ("   ", "empty", ""),
        ("\t\n", "empty", ""),
        ("/quit", "quit", ""),
        ("/exit", "quit", ""),
        ("  /quit  ", "quit", ""),
        ("/reload", "reload", ""),
        ("  /reload  ", "reload", ""),
        ("!ls -la", "bash", "ls -la"),
        ("!  ls  ", "bash", "ls"),
        ("!", "bash", ""),
        ("!!echo hi", "bash_transient", "echo hi"),
        ("!!", "bash_transient", ""),
        ("hello world", "prompt", "hello world"),
        ("  spaced prompt  ", "prompt", "spaced prompt"),
        ("/unknown command", "prompt", "/unknown command"),
        ("/foo", "prompt", "/foo"),
    ],
)
def test_parse_input_line_truth_table(line: str, kind: str, text: str) -> None:
    parsed = parse_input_line(line)
    assert parsed == ParsedInput(kind, text)  # type: ignore[arg-type]


def test_bang_bang_takes_precedence_over_single_bang() -> None:
    # "!!" must match before "!" (order in parse_input_line).
    assert parse_input_line("!!x").kind == "bash_transient"
    assert parse_input_line("!x").kind == "bash"


def test_build_prompt_session_constructs() -> None:
    with create_pipe_input() as pipe:
        session = build_prompt_session(pt_input=pipe, pt_output=DummyOutput())
        assert session.multiline is False


def test_build_prompt_session_with_history_path_uses_file_history(tmp_path) -> None:
    from prompt_toolkit.history import FileHistory

    session = build_prompt_session(history_path=str(tmp_path / "history"))
    assert isinstance(session.history, FileHistory)


def test_build_prompt_session_default_history_is_in_memory() -> None:
    from prompt_toolkit.history import InMemoryHistory

    with create_pipe_input() as pipe:
        session = build_prompt_session(pt_input=pipe, pt_output=DummyOutput())
    assert isinstance(session.history, InMemoryHistory)


async def test_prompt_session_reads_a_line() -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("hello there\n")
        session = build_prompt_session(pt_input=pipe, pt_output=DummyOutput())
        result = await session.prompt_async("» ")
    assert result == "hello there"
