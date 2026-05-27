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

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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
        entry exists for palette + ``/help`` listing only). The handler receives
        the live :class:`CommandContext` plus ``args`` — the text after the
        command word (``""`` when none); ``/help`` ignores it.
    """

    name: str
    description: str
    handler: Callable[[CommandContext, str], Awaitable[None]] | None = None


@dataclass
class CommandContext:
    """Everything a built-in handler needs to act on the live TUI.

    :param chrome: the live :class:`AelixChrome` (status/footer/input setters).
    :param harness: the agent harness (model, prompt, state).
    :param commit: commit a Rich renderable into scrollback (run_tui's output-queue
        committer).
    :param cwd: the session working directory.
    :param commands: the live command registry (so ``/help`` can list it).
    :param set_mode: optional callback ``run_tui`` wires so ``/mode`` can update
        the live footer steering-mode segment after :meth:`set_steering_mode`
        succeeds. ``None`` in headless tests / when no footer is attached.
    """

    chrome: AelixChrome
    harness: AgentHarness
    commit: Callable[[object], None]
    cwd: str
    commands: list[BuiltinCommand] = field(default_factory=list)
    set_mode: Callable[[str], None] | None = None
    refresh_footer: Callable[[], None] | None = None
    """``run_tui`` wires this to ``context._refresh_footer`` so ``/model`` can
    re-render the footer ``✱ {model}`` segment after a switch (the footer is a
    cached string recomposed only on refresh). ``None`` in headless tests."""


def build_help_renderable(commands: list[BuiltinCommand]) -> RenderableType:
    """Render the command table (``/name  description``) as a Rich panel."""

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    for command in commands:
        table.add_row(f"/{command.name}", command.description)
    return Panel(table, title="Commands", box=ROUNDED, border_style="cyan")


async def _help_handler(ctx: CommandContext, args: str) -> None:
    """``/help`` — commit the command table into scrollback (ignores ``args``)."""

    ctx.commit(build_help_renderable(ctx.commands))


async def _model_handler(ctx: CommandContext, args: str) -> None:
    """``/model [id]`` — no arg shows the current model; an id switches to it.

    Defensive: degrades with a committed message (never crashes) when the
    harness lacks ``current_model`` / ``set_model``, when model resolution
    fails, or when the switch raises.
    """

    if not args:
        model = getattr(ctx.harness, "current_model", None)
        model_id = getattr(model, "id", None) if model is not None else None
        if model_id:
            ctx.commit(Text(f"model: {model_id}"))
        else:
            ctx.commit(Text("No model set.", style="yellow"))
        return

    # ``hasattr`` guards the headless FakeHarness (no set_model); the typed
    # AgentHarness.set_model is then called directly so pyright sees a coroutine.
    if not hasattr(ctx.harness, "set_model"):
        ctx.commit(Text("Model switching is unavailable.", style="yellow"))
        return
    try:
        from aelix_coding_agent.cli.runtime_bootstrap import resolve_model

        model = resolve_model(args, None)
        await ctx.harness.set_model(model)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ model switch failed: {exc}", style="bold red"))
        return
    model_id = getattr(model, "id", args)
    if getattr(model, "provider", ""):
        ctx.commit(Text(f"model → {model_id}", style="green"))
    else:
        # resolve_model returns a bare Model (empty provider) when no adapter is
        # resolvable — the switch "succeeds" but turns will fail later. Caution
        # rather than green so the failure isn't deferred to a confusing point.
        ctx.commit(
            Text(
                f"model → {model_id} (no provider resolved — turns may fail; "
                "set OPENROUTER_API_KEY or pass a provider)",
                style="yellow",
            )
        )
    # The footer ✱ segment is a cached string — refresh it so it reflects the
    # new model immediately (not only on the next unrelated repaint).
    if ctx.refresh_footer is not None:
        with contextlib.suppress(Exception):
            ctx.refresh_footer()


async def _clear_handler(ctx: CommandContext, args: str) -> None:
    """``/clear`` — clear the terminal scrollback without killing the chrome."""

    clear = getattr(ctx.chrome, "clear", None)
    if not callable(clear):
        ctx.commit(Text("Clear is unavailable.", style="yellow"))
        return
    try:
        clear()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ clear failed: {exc}", style="bold red"))


async def _compact_handler(ctx: CommandContext, args: str) -> None:
    """``/compact [instructions]`` — compact context; report before/after."""

    if not hasattr(ctx.harness, "compact"):
        ctx.commit(Text("Compaction is unavailable.", style="yellow"))
        return
    try:
        result = await ctx.harness.compact(args or None)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ compact failed: {exc}", style="bold red"))
        return
    if result is None:
        ctx.commit(Text("Nothing to compact.", style="yellow"))
        return
    tokens_before = getattr(result, "tokens_before", None)
    summary = getattr(result, "summary", "") or ""
    body = Text()
    body.append("Compacted context.\n", style="green")
    if tokens_before is not None:
        body.append(f"tokens before: {tokens_before}\n")
    if summary:
        body.append(f"summary: {summary}")
    ctx.commit(Panel(body, title="Compact", box=ROUNDED, border_style="cyan"))


async def _cost_handler(ctx: CommandContext, args: str) -> None:
    """``/cost`` — show session token / cost / message usage as a small table."""

    if not hasattr(ctx.harness, "get_session_stats"):
        ctx.commit(Text("Session stats are unavailable.", style="yellow"))
        return
    try:
        stats = await ctx.harness.get_session_stats()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ cost failed: {exc}", style="bold red"))
        return
    tokens = getattr(stats, "tokens", None)
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    table.add_row("messages", str(getattr(stats, "total_messages", 0)))
    table.add_row("input tokens", str(getattr(tokens, "input", 0)))
    table.add_row("output tokens", str(getattr(tokens, "output", 0)))
    table.add_row("total tokens", str(getattr(tokens, "total", 0)))
    table.add_row("cost (USD)", f"{getattr(stats, 'cost', 0.0):.4f}")
    ctx.commit(Panel(table, title="Session usage", box=ROUNDED, border_style="cyan"))


async def _tools_handler(ctx: CommandContext, args: str) -> None:
    """``/tools`` — list the registered tools (name + description)."""

    # ``_action_get_all_tools`` is semi-private (documented coupling, Sprint
    # 6h₁₂d) — wrapping it would touch protected core.py. hasattr-guard the
    # headless FakeHarness, then call the typed method directly.
    tools: list[object] = []
    if hasattr(ctx.harness, "_action_get_all_tools"):
        try:
            tools = list(ctx.harness._action_get_all_tools())
        except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
            ctx.commit(Text(f"✖ tools failed: {exc}", style="bold red"))
            return
    if not tools:
        ctx.commit(Text("No tools registered.", style="yellow"))
        return
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    for tool in tools:
        name = getattr(tool, "name", str(tool))
        desc = getattr(tool, "description", None) or ""
        table.add_row(name, desc)
    ctx.commit(Panel(table, title="Tools", box=ROUNDED, border_style="cyan"))


async def _mode_handler(ctx: CommandContext, args: str) -> None:
    """``/mode [name]`` — show the steering mode, or set it + reflect the footer."""

    set_mode = getattr(ctx.harness, "set_steering_mode", None)
    if not args:
        mode = getattr(ctx.harness, "steering_mode", None)
        if mode is None:
            mode = getattr(getattr(ctx.harness, "_state", None), "steering_mode", None)
        ctx.commit(Text(f"mode: {mode}" if mode else "Steering mode is unavailable.",
                        style="yellow" if not mode else ""))
        return
    if not callable(set_mode):
        ctx.commit(Text("Mode switching is unavailable.", style="yellow"))
        return
    try:
        set_mode(args)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ mode switch failed: {exc}", style="bold red"))
        return
    if ctx.set_mode is not None:
        with contextlib.suppress(Exception):
            ctx.set_mode(args)
    ctx.commit(Text(f"mode → {args}", style="green"))


async def _export_handler(ctx: CommandContext, args: str) -> None:
    """``/export [path]`` — write the session transcript to an HTML file."""

    if not hasattr(ctx.harness, "export_to_html"):
        ctx.commit(Text("Export is unavailable.", style="yellow"))
        return
    try:
        # export_to_html is synchronous and returns the resolved path; it
        # raises on in-memory / empty sessions (Pi parity).
        path = ctx.harness.export_to_html(args or None)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ export failed: {exc}", style="bold red"))
        return
    ctx.commit(Text(f"exported → {path}", style="green"))


async def _thinking_handler(ctx: CommandContext, args: str) -> None:
    """``/thinking [level]`` — show the reasoning level, or set it.

    No arg shows the current level; an arg (e.g. ``low``/``medium``/``high``)
    sets it via the harness. Degrades gracefully on a harness lacking the API.
    """

    state = getattr(ctx.harness, "_state", None)
    current = getattr(state, "thinking_level", None)
    if not args:
        if current:
            ctx.commit(Text(f"thinking: {current}"))
        else:
            ctx.commit(Text("Thinking level is unavailable.", style="yellow"))
        return
    setter = getattr(ctx.harness, "set_thinking_level", None)
    if not callable(setter):
        ctx.commit(Text("Thinking level switching is unavailable.", style="yellow"))
        return
    try:
        await setter(args)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ thinking switch failed: {exc}", style="bold red"))
        return
    ctx.commit(Text(f"thinking → {args}", style="green"))


BUILTIN_COMMANDS: list[BuiltinCommand] = [
    BuiltinCommand("help", "List available commands", _help_handler),
    BuiltinCommand("model", "Show or switch the active model", _model_handler),
    BuiltinCommand("clear", "Clear the scrollback transcript", _clear_handler),
    BuiltinCommand("compact", "Compact the conversation context", _compact_handler),
    BuiltinCommand("cost", "Show session token / cost usage", _cost_handler),
    BuiltinCommand("thinking", "Show or set the reasoning level", _thinking_handler),
    BuiltinCommand("tools", "List registered tools", _tools_handler),
    BuiltinCommand("mode", "Show or set the steering mode", _mode_handler),
    BuiltinCommand("export", "Export the transcript to HTML", _export_handler),
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
