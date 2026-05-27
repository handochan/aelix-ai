"""Sprint 6h₁₀a (ADR-0104) — input line classification (pure).

:func:`parse_input_line` mirrors the ``run_repl`` token precedence
(``cli/repl.py:94-120``): ``/quit``/``/exit`` → quit, ``/reload`` → reload,
``!!cmd`` → transient bash, ``!cmd`` → context bash, else → prompt. Keeping it
pure makes the input surface unit-testable with zero prompt-toolkit dependency.

(Sprint 6h₁₀b: the input editor + history are now owned by
:class:`~aelix_coding_agent.tui.chrome.AelixChrome`'s long-running Application,
so the 6h₁₀a ``build_prompt_session`` helper was removed — the chrome builds its
own Buffer. Only the pure parser remains here.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

InputKind = Literal["prompt", "bash", "bash_transient", "reload", "quit", "empty"]


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
    # Slash built-ins match on the FIRST token so trailing args (which these
    # commands ignore) don't misclassify — e.g. "/reload now" → reload rather
    # than a prompt that later reports "Unknown command: /reload".
    first = stripped.split(maxsplit=1)[0]
    if first in ("/quit", "/exit"):
        return ParsedInput("quit")
    if first == "/reload":
        return ParsedInput("reload")
    if line.startswith("!!"):
        return ParsedInput("bash_transient", line[2:].strip())
    if line.startswith("!"):
        return ParsedInput("bash", line[1:].strip())
    return ParsedInput("prompt", stripped)


__all__ = ["InputKind", "ParsedInput", "parse_input_line"]
