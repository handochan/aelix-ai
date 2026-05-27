"""Sprint 6h₁₂a (ADR-0110) — built-in slash-command core (registry + /help).

The TUI has descriptor/extension command "rails" (``DescriptorCommandCompleter``,
``_match_management_modal``) but no **first-party** command vocabulary. This
module is the built-in command core: a frozen registry, a dispatch context, the
``/help`` handler + table, and a PURE :func:`match_command` lookup.

Two-layer split (kept deliberate): ``input.py::parse_input_line`` stays PURE and
owns ``/quit``/``/exit`` (→ ``quit``) and ``/reload`` (→ ``reload``); the registry
here owns everything else (``/help`` + future Sprint-D handlers). The quit/exit/
reload entries below carry ``handler=None`` — they exist only so the palette and
``/help`` listing show them; their behavior is dispatched by ``parse_input_line``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aelix_agent_core.harness.core import AgentHarness
    from rich.console import RenderableType

    from aelix_coding_agent.tui.chrome import AelixChrome


@dataclass(frozen=True)
class BuiltinCommand:
    """A first-party slash command.

    :param name: the command word with no leading ``/`` (e.g. ``"help"``).
    :param description: one-line palette / ``/help`` description.
    :param handler: the async dispatch callable, or ``None`` when the command is
        dispatched elsewhere (``parse_input_line`` owns quit/exit/reload; the
        entry exists for palette + ``/help`` listing only).
    """

    name: str
    description: str
    handler: Callable[[CommandContext], Awaitable[None]] | None = None


@dataclass
class CommandContext:
    """Everything a built-in handler needs to act on the live TUI.

    :param chrome: the live :class:`AelixChrome` (status/footer/input setters).
    :param harness: the agent harness (model, prompt, state).
    :param commit: commit a Rich renderable into scrollback (run_tui's output-queue
        committer).
    :param cwd: the session working directory.
    :param commands: the live command registry (so ``/help`` can list it).
    """

    chrome: AelixChrome
    harness: AgentHarness
    commit: Callable[[object], None]
    cwd: str
    commands: list[BuiltinCommand] = field(default_factory=list)


def build_help_renderable(commands: list[BuiltinCommand]) -> RenderableType:
    """Render the command table (``/name  description``) as a Rich panel."""

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    for command in commands:
        table.add_row(f"/{command.name}", command.description)
    return Panel(table, title="Commands", box=ROUNDED, border_style="cyan")


async def _help_handler(ctx: CommandContext) -> None:
    """``/help`` — commit the command table into scrollback."""

    ctx.commit(build_help_renderable(ctx.commands))


BUILTIN_COMMANDS: list[BuiltinCommand] = [
    BuiltinCommand("help", "List available commands", _help_handler),
    BuiltinCommand("quit", "Exit Aelix", None),
    BuiltinCommand("exit", "Exit Aelix", None),
    BuiltinCommand("reload", "Reload extensions + resources", None),
]


def slash_word(text: str) -> str:
    """The leading slash command word (no ``/``), or ``""`` (PURE).

    ``"/help extra"`` → ``"help"``; a non-slash line, bare ``/``, or ``/ `` → ``""``.
    Shared by :func:`match_command` and the shell's unknown-command label so the
    two can never disagree on what the typed command word was.
    """

    if not text.startswith("/"):
        return ""
    parts = text[1:].split(maxsplit=1)
    return parts[0] if parts else ""


def match_command(text: str, commands: list[BuiltinCommand]) -> BuiltinCommand | None:
    """Resolve a ``/<word>`` line to a built-in command (PURE).

    Parses the leading slash word (case-sensitive, exact name) and looks it up.
    Returns ``None`` for a non-slash line, an empty body (bare ``/``), or no match.
    """

    word = slash_word(text)
    if not word:
        return None
    for command in commands:
        if command.name == word:
            return command
    return None


__all__ = [
    "BUILTIN_COMMANDS",
    "BuiltinCommand",
    "CommandContext",
    "build_help_renderable",
    "match_command",
    "slash_word",
]
