"""Sprint 6h₁₀a (ADR-0104) — input parsing + prompt-toolkit session builder.

:func:`parse_input_line` is a pure function mirroring the ``run_repl`` token
precedence (``cli/repl.py:94-120``): ``/quit``/``/exit`` → quit, ``/reload`` →
reload, ``!!cmd`` → transient bash, ``!cmd`` → context bash, else → prompt.
Keeping it pure makes the bulk of the input surface unit-testable with zero
prompt-toolkit dependency.

:func:`build_prompt_session` builds the inline single-line editor. Multi-line
input (Alt+Enter), autocomplete provider stacking, and a custom editor
component are deferred to Sprint 6h₁₀b (the ExtensionUIContext editor surface).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory, History, InMemoryHistory

if TYPE_CHECKING:
    from prompt_toolkit.input.base import Input
    from prompt_toolkit.output.base import Output

InputKind = Literal["prompt", "bash", "bash_transient", "reload", "quit", "empty"]

PROMPT_STR = "» "  # parity with cli/repl.py


@dataclass(frozen=True)
class ParsedInput:
    """Outcome of classifying one input line."""

    kind: InputKind
    text: str = ""


def parse_input_line(line: str) -> ParsedInput:
    """Classify a raw input line (pure; precedence matches ``run_repl``).

    ``!``/``!!`` are detected on the raw line (leading sigil); the command body
    is stripped. An empty body (bare ``!``) yields a ``bash`` kind with empty
    text — the shell skips empty bodies, matching ``run_repl``'s ``if cmd:``.
    """

    stripped = line.strip()
    if not stripped:
        return ParsedInput("empty")
    if stripped in ("/quit", "/exit"):
        return ParsedInput("quit")
    if stripped == "/reload":
        return ParsedInput("reload")
    if line.startswith("!!"):
        return ParsedInput("bash_transient", line[2:].strip())
    if line.startswith("!"):
        return ParsedInput("bash", line[1:].strip())
    return ParsedInput("prompt", stripped)


def build_prompt_session(
    *,
    history_path: str | None = None,
    pt_input: Input | None = None,
    pt_output: Output | None = None,
) -> PromptSession[str]:
    """Build the inline single-line :class:`PromptSession`.

    :param history_path: persist input history to this file; in-memory when None.
    :param pt_input: prompt-toolkit input source (tests inject a pipe input).
    :param pt_output: prompt-toolkit output sink (tests inject a DummyOutput).
    """

    history: History = FileHistory(history_path) if history_path else InMemoryHistory()
    return PromptSession(
        history=history,
        multiline=False,
        complete_while_typing=False,
        input=pt_input,
        output=pt_output,
    )


__all__ = ["PROMPT_STR", "InputKind", "ParsedInput", "build_prompt_session", "parse_input_line"]
