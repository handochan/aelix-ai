"""Sprint 6h₁₀a (ADR-0104) — parse_input_line truth table (pure)."""

from __future__ import annotations

import pytest
from aelix_coding_agent.tui.input import ParsedInput, parse_input_line


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
        # Slash built-ins match on the first token — trailing args are ignored,
        # not misclassified as a prompt (Sprint 6h₁₂a fix).
        ("/reload now", "reload", ""),
        ("/quit please", "quit", ""),
        ("/exit 0", "quit", ""),
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
    assert parse_input_line(line) == ParsedInput(kind, text)  # type: ignore[arg-type]


def test_bang_bang_takes_precedence_over_single_bang() -> None:
    assert parse_input_line("!!x").kind == "bash_transient"
    assert parse_input_line("!x").kind == "bash"
