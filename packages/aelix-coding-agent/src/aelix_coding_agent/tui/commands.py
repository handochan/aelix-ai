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
    from typing import Any

    from aelix_agent_core.harness.core import AgentHarness
    from aelix_ai.settings import SettingsManager
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
    model_picker: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_open_model_picker`` flow: a ``ctx.ui.select``
    over ``ModelRegistry.get_available()`` with a per-highlight detail footer
    (modality / context-window / base-url / api-key) → ``harness.set_model``. The
    no-arg ``/model`` handler awaits it; ``None`` in headless tests / when no model
    registry is attached, in which case ``/model`` falls back to a status print
    (Sprint 6h₂₆, ADR-0154)."""
    expand_lookup: Callable[[int], str | None] | None = None
    """``run_tui`` wires this to the live ``EventRenderer.get_expanded`` so
    ``/expand N`` can recover the full, untruncated body of a tool-result card
    whose ``… (+N more lines · /expand N)`` footer elided it. ``None`` in
    headless tests / when no renderer is attached."""
    resume_session: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_resume_session`` flow (list sessions →
    picker → ``runtime.switch_session`` hot-swap → transcript replay). The
    ``/resume`` handler just awaits it; ``None`` in headless tests / when no
    session repo is attached (Sprint 6h₁₄b, ADR-0122)."""
    new_session: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_new_session`` flow (``runtime.new_session``
    fresh-session hot-swap → clear + banner). The ``/new`` handler awaits it;
    ``None`` in headless tests (Sprint 6h₁₅, ADR-0123)."""
    settings_action: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_open_settings`` flow (a ``ctx.ui.select``
    menu that toggles/cycles the live harness settings — steering mode, follow-up
    mode, thinking visibility/level). The ``/settings`` handler awaits it; ``None``
    in headless tests (Sprint 6h₁₇, ADR-0125)."""
    import_session: Callable[[str], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_import_session`` flow
    (``runtime.import_from_jsonl(path)`` → repaint). The ``/import`` handler
    parses the path arg then awaits it; ``None`` in headless tests
    (Sprint 6h₂₁, ADR-0129). Pi parity:
    :func:`AgentSessionRuntime.import_from_jsonl` (``agent-session-runtime.ts:329-364``)."""
    fork_session: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_fork_session`` flow (resolve the most
    recent user entry → ``runtime.fork(entry_id, position="before")`` → repaint).
    The ``/fork`` handler awaits it; ``None`` in headless tests
    (Sprint 6h₂₁, ADR-0129). Pi parity:
    :func:`AgentSessionRuntime.fork` (``agent-session-runtime.ts:234-320``,
    ``position="before"`` branch)."""
    clone_session: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_clone_session`` flow (resolve the leaf
    entry → ``runtime.fork(leaf_id, position="at")`` so ALL entries are kept →
    repaint). The ``/clone`` handler awaits it; ``None`` in headless tests
    (Sprint 6h₂₁, ADR-0129). Pi parity: same ``runtime.fork`` API, ``position="at"``
    at the leaf (no truncation)."""
    tree_action: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_tree_action`` flow (walk
    ``session.get_metadata().parent_session_path`` recursively through the repo,
    render the lineage as a table). The ``/tree`` handler awaits it; ``None`` in
    headless tests (Sprint 6h₂₁, ADR-0129)."""
    is_editor_open: Callable[[], bool] | None = None
    """``run_tui`` wires this to ``editor_open_ref["open"]`` so the input loop
    can short-circuit a buffered/pasted Enter that lands while ``$EDITOR`` is
    still applying its result (W-review HIGH-1, Sprint 6h₂₃, ADR-0131).
    ``None`` in headless tests / when no editor is wired."""
    thinking_picker: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_open_thinking_picker`` flow: a
    ``ctx.ui.select`` over ``get_supported_thinking_levels(current_model)`` →
    ``harness.set_thinking_level``. The no-arg ``/thinking`` handler awaits it;
    ``None`` in headless tests / when no picker is wired, in which case
    ``/thinking`` falls back to its status print (Sprint 6h₂₇, ADR-0155)."""
    mcp_status: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_open_mcp_status`` flow (a read-only
    panel over the live ``McpClientManager``: servers, transport, state, tool
    counts). The ``/mcp`` handler awaits it; ``None`` in headless tests / when
    no MCP manager is attached, in which case ``/mcp`` degrades with a committed
    message (Sprint 6h₂₇, ADR-0155)."""
    cycle_permission_mode: Callable[[], None] | None = None
    """``run_tui`` wires this to its ``_cycle_permission`` flow (advance the held
    ``PermissionPosture`` + toast + footer repaint) so the ``/permissions``
    command can cycle the posture from the prompt. shift+tab is the PRIMARY
    affordance; the slash command is optional sugar. ``None`` in headless tests /
    when no posture is wired (WP-0, ADR-0157)."""
    permission_mode: Callable[[], str | None] | None = None
    """``run_tui`` wires this to read the current posture badge/name so
    ``/permissions`` (no-arg) can surface it. ``None`` in headless tests."""
    statusline_action: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_open_statusline`` flow (a multi-checkbox
    picker over the footer-segment registry that persists the enabled-id set to
    the coding-agent-owned statusline store + repaints the footer). The
    ``/statusline`` handler awaits it; ``None`` in headless tests (WP-2,
    ADR-0160)."""
    settings_manager: SettingsManager | None = None
    """``run_tui`` threads the held :class:`SettingsManager` (constructed once in
    entry.py via ``SettingsManager.create``) so ``/settings`` (ImplConsumers,
    ADR-0161) + ``/scoped-models`` can read/persist the pi-parity settings via
    its existing get_*/set_*/flush API. ``None`` in headless tests (WP-2,
    ADR-0160)."""
    scoped_models_action: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_open_scoped_models`` flow (a multi-checkbox
    picker over ``ModelRegistry.get_available()`` that reads/writes the
    ``enabled_models`` allow-list via the held SettingsManager — global scope, pi
    parity). The ``/scoped-models`` handler awaits it; ``None`` in headless tests /
    when no registry or settings manager is attached (ImplConsumers, ADR-0161)."""
    login_action: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_open_login`` flow (the auth wizard:
    OAuth / built-in API key / custom provider → ``AuthStorage``). The ``/login``
    handler awaits it; ``None`` in headless tests / when no auth storage is
    attached, in which case ``/login`` degrades with a committed message
    (WP-8, Feature 1)."""
    logout_action: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_open_logout`` flow (list stored
    credentials → picker → confirm → ``AuthStorage.logout``). The ``/logout``
    handler awaits it; ``None`` in headless tests / when no auth storage is
    attached (WP-8, Feature 1)."""
    stats_action: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_open_stats`` flow (the usage dashboard: a
    framed tabbed viewer over the harness ``SessionStats`` + the TUI-side
    ``SessionActivityTracker`` snapshot — Session / Activity / Efficiency tabs).
    The ``/stats`` handler awaits it; ``None`` in headless tests (WP-8,
    Feature 2)."""
    extension_action: Callable[[], Awaitable[None]] | None = None
    """``run_tui`` wires this to its ``_open_extension`` flow (a read-only framed
    tabbed viewer over the discovered extensions + the live MCP manager —
    Installed / Discover / Sources tabs). The ``/extension`` handler awaits it;
    ``None`` in headless tests (WP-8, Feature 3)."""


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
    """``/model [id]`` — no arg opens the rich picker; an id switches directly.

    No-arg opens the interactive picker (searchable provider-tagged list + a
    detail footer) when the host wired one, else prints the current model. An
    explicit id (``/model openai/gpt-4o``) skips the picker and switches directly.

    Defensive: degrades with a committed message (never crashes) when the
    harness lacks ``current_model`` / ``set_model``, when model resolution
    fails, or when the switch raises.
    """

    if not args:
        # Sprint 6h₂₆ (ADR-0154) — no-arg /model opens the rich picker when the
        # host wired it; falls back to a one-line status print headlessly / when
        # no model registry is attached (FakeHarness tests, RPC).
        if ctx.model_picker is not None:
            await ctx.model_picker()
            return
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
        from aelix_coding_agent.core.runnable_models import (
            is_runnable,
            unsupported_message,
        )

        model = resolve_model(args, None)
        # WP-8 follow-up — guard an explicit id whose api has no adapter (e.g.
        # ``/model gpt-5.x`` → openai-responses): surface the actionable reason,
        # not the cryptic ``No provider registered for api=...`` the loop raises.
        if not is_runnable(model):
            ctx.commit(Text(unsupported_message(model), style="bold red"))
            return
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
    """``/compact [instructions]`` — compact context; report before/after.

    "Nothing to compact" is an expected NO-OP, not a failure: the harness
    signals it by RAISING ``AgentHarnessError(code="invalid_state",
    "Nothing to compact")`` (``core.py``) — it never returns ``None``. We
    discriminate that one raise (duck-typed on ``.code`` + message, mirroring
    the harness's own auto-compaction guard at ``core.py:1523``) and render it
    NEUTRAL yellow, while every genuine failure still surfaces in red.
    """

    if not hasattr(ctx.harness, "compact"):
        ctx.commit(Text("Compaction is unavailable.", style="yellow"))
        return
    try:
        result = await ctx.harness.compact(args or None)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        if (
            getattr(exc, "code", None) == "invalid_state"
            and "Nothing to compact" in str(exc)
        ):
            ctx.commit(Text("Nothing to compact.", style="yellow"))
            return
        ctx.commit(Text(f"✖ compact failed: {exc}", style="bold red"))
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


async def _skills_handler(ctx: CommandContext, args: str) -> None:
    """``/skills`` — list the skills loaded into the harness (name + description).

    Issue #12: skills are loaded at startup (``entry.py``) and stored via
    ``harness.set_skills``. This is a read-only consumer of the
    ``harness.skills`` property; prompt injection into the system prompt is a
    separate follow-up.
    """

    skills: list[object] = []
    if hasattr(ctx.harness, "skills"):
        try:
            skills = list(ctx.harness.skills)
        except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
            ctx.commit(Text(f"✖ skills failed: {exc}", style="bold red"))
            return
    if not skills:
        ctx.commit(Text("No skills loaded.", style="yellow"))
        return
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    for skill in skills:
        name = getattr(skill, "name", str(skill))
        desc = getattr(skill, "description", None) or ""
        # A skill the model cannot auto-invoke is still listed for the user,
        # but flagged so the distinction is visible.
        if getattr(skill, "disable_model_invocation", False):
            desc = f"{desc} (model-invocation disabled)".strip()
        table.add_row(name, desc)
    ctx.commit(Panel(table, title="Skills", box=ROUNDED, border_style="cyan"))


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


async def _permissions_handler(ctx: CommandContext, args: str) -> None:
    """``/permissions`` — show the permission posture, or cycle it (WP-0).

    No-arg shows the current posture; ``/permissions cycle`` (or any arg)
    advances it. shift+tab is the PRIMARY affordance; this is optional sugar.
    """

    if ctx.cycle_permission_mode is None:
        ctx.commit(
            Text(
                "Permission posture switching is unavailable. Use shift+tab in "
                "the interactive TUI.",
                style="yellow",
            )
        )
        return
    if args.strip():
        ctx.cycle_permission_mode()
        return
    current = ctx.permission_mode() if ctx.permission_mode is not None else None
    ctx.commit(
        Text(
            f"permission mode: {current or 'default'}  "
            "(shift+tab or /permissions cycle to change)",
            style="cyan",
        )
    )


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
    """``/thinking [level]`` — no arg opens the picker; a level sets it directly.

    No-arg opens the interactive level picker (Sprint 6h₂₇, ADR-0155) when the
    host wired one, else prints the current level. An explicit level
    (``/thinking high``) skips the picker and sets it via the harness. Degrades
    gracefully on a harness lacking the API.
    """

    state = getattr(ctx.harness, "_state", None)
    current = getattr(state, "thinking_level", None)
    setter: Callable[[str], Awaitable[None]] | None = getattr(
        ctx.harness, "set_thinking_level", None
    )
    supported = state is not None or callable(setter)
    if not args:
        # Sprint 6h₂₇ (ADR-0155) — no-arg /thinking opens the level picker when
        # the host wired it; falls back to a one-line status print headlessly /
        # when no picker is attached (FakeHarness tests, RPC).
        if ctx.thinking_picker is not None:
            try:
                await ctx.thinking_picker()
            except Exception as exc:  # noqa: BLE001 — never kill the REPL
                ctx.commit(Text(f"✖ thinking picker failed: {exc}", style="bold red"))
            return
        if current:
            ctx.commit(Text(f"thinking: {current}"))
        elif supported:
            # ``thinking_level`` defaults to None (= off) on a fresh session —
            # that's "unset", not "feature missing".
            ctx.commit(Text("thinking: off"))
        else:
            ctx.commit(Text("Thinking level is unavailable.", style="yellow"))
        return
    if not callable(setter):
        ctx.commit(Text("Thinking level switching is unavailable.", style="yellow"))
        return
    try:
        await setter(args)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ thinking switch failed: {exc}", style="bold red"))
        return
    ctx.commit(Text(f"thinking → {args}", style="green"))


async def _expand_handler(ctx: CommandContext, args: str) -> None:
    """``/expand N`` — re-print the full body of a truncated tool-result card.

    ``N`` is the id shown on a truncated card's ``… (+K more lines · /expand N)``
    footer. Degrades with a committed message (never crashes) on a missing
    lookup, a non-numeric / absent arg, or an unknown id.
    """

    lookup = ctx.expand_lookup
    if lookup is None:
        ctx.commit(Text("Expand is unavailable.", style="yellow"))
        return
    token = args.split()[0] if args.split() else ""
    if not token.isdigit():
        ctx.commit(
            Text(
                "Usage: /expand <N> — N is the id on a truncated card's footer.",
                style="yellow",
            )
        )
        return
    full = lookup(int(token))
    if full is None:
        ctx.commit(Text(f"No expandable result #{token}.", style="yellow"))
        return
    ctx.commit(
        Panel(Text(full), title=f"tool result #{token}", box=ROUNDED, border_style="cyan")
    )


async def _resume_handler(ctx: CommandContext, args: str) -> None:
    """``/resume`` — pick a previous session and hot-swap to it (ignores args).

    Delegates to the host-wired ``resume_session`` flow (picker → switch_session
    → transcript replay). Degrades with a committed message when unavailable or
    on any failure (never crashes the REPL).
    """

    if ctx.resume_session is None:
        ctx.commit(Text("Resume is unavailable.", style="yellow"))
        return
    try:
        await ctx.resume_session()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ resume failed: {exc}", style="bold red"))


async def _settings_handler(ctx: CommandContext, args: str) -> None:
    """``/settings`` — open the settings menu (ignores args).

    Delegates to the host-wired ``settings_action`` flow: a looping select menu
    over the SettingsManager-backed rows (theme, default model, steering/follow-up
    modes, thinking visibility/level, autocomplete size, image handling, …) that
    toggles/cycles/inputs each setting and persists it (the live rows also apply to
    the current session). Degrades when unavailable / on failure (ImplConsumers,
    ADR-0161).
    """

    if ctx.settings_action is None:
        ctx.commit(Text("Settings are unavailable.", style="yellow"))
        return
    try:
        await ctx.settings_action()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ settings failed: {exc}", style="bold red"))


async def _scoped_models_handler(ctx: CommandContext, args: str) -> None:
    """``/scoped-models`` — choose which models are enabled (ignores args).

    Delegates to the host-wired ``scoped_models_action`` flow (a multi-checkbox
    picker over ``ModelRegistry.get_available()`` that reads/writes the
    ``enabled_models`` allow-list via the held SettingsManager — global scope, pi
    parity). Degrades when unavailable / on failure (ImplConsumers, ADR-0161).
    """

    if ctx.scoped_models_action is None:
        ctx.commit(Text("Scoped models are unavailable.", style="yellow"))
        return
    try:
        await ctx.scoped_models_action()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ scoped-models failed: {exc}", style="bold red"))


async def _statusline_handler(ctx: CommandContext, args: str) -> None:
    """``/statusline`` — configure which footer segments render (ignores args).

    Delegates to the host-wired ``statusline_action`` flow (a multi-checkbox
    picker over the footer-segment registry → persist the enabled-id set to the
    coding-agent-owned statusline store → repaint the footer). Degrades when
    unavailable / on failure (WP-2, ADR-0160).
    """

    if ctx.statusline_action is None:
        ctx.commit(Text("Statusline is unavailable.", style="yellow"))
        return
    try:
        await ctx.statusline_action()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ statusline failed: {exc}", style="bold red"))


async def _new_handler(ctx: CommandContext, args: str) -> None:
    """``/new`` — start a fresh session (ignores args).

    Delegates to the host-wired ``new_session`` flow (new_session hot-swap →
    clear + banner). Degrades when unavailable / on failure.
    """

    if ctx.new_session is None:
        ctx.commit(Text("New session is unavailable.", style="yellow"))
        return
    try:
        await ctx.new_session()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ new session failed: {exc}", style="bold red"))


async def _import_handler(ctx: CommandContext, args: str) -> None:
    """``/import <path>`` — import a JSONL session file and swap to it.

    Delegates to the host-wired ``import_session`` flow (which calls
    ``runtime.import_from_jsonl(path)``, then repaints the transcript). Pi
    parity: ``slash-commands.ts`` ``/import`` → ``importFromJsonl``
    (``agent-session-runtime.ts:329-364``). Sprint 6h₂₁ (ADR-0129).
    """

    path = args.strip()
    if not path:
        ctx.commit(
            Text(
                "Usage: /import <path> — absolute or relative path to a .jsonl session file.",
                style="yellow",
            )
        )
        return
    if ctx.import_session is None:
        ctx.commit(Text("Import is unavailable.", style="yellow"))
        return
    try:
        await ctx.import_session(path)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ import failed: {exc}", style="bold red"))


async def _fork_handler(ctx: CommandContext, args: str) -> None:
    """``/fork`` — fork the current session at the most recent user message (ignores args).

    Delegates to the host-wired ``fork_session`` flow (resolve the most recent
    user entry via ``session.get_entries()`` → ``runtime.fork(entry_id,
    position="before")`` → repaint). The new session contains entries up to
    BEFORE the resolved user message (Pi parity:
    ``agent-session-runtime.ts:262-280``, ``position="before"`` branch). Sprint
    6h₂₁ (ADR-0129).
    """

    if ctx.fork_session is None:
        ctx.commit(Text("Fork is unavailable.", style="yellow"))
        return
    try:
        await ctx.fork_session()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ fork failed: {exc}", style="bold red"))


async def _clone_handler(ctx: CommandContext, args: str) -> None:
    """``/clone`` — clone the current session (whole transcript) into a new file.

    Delegates to the host-wired ``clone_session`` flow (resolve the leaf entry
    → ``runtime.fork(leaf_id, position="at")`` so the new session keeps ALL
    entries → repaint). Pi parity: ``slash-commands.ts`` ``/clone`` semantics
    (clone-without-truncation) over the same ``runtime.fork`` surface. Sprint
    6h₂₁ (ADR-0129).
    """

    if ctx.clone_session is None:
        ctx.commit(Text("Clone is unavailable.", style="yellow"))
        return
    try:
        await ctx.clone_session()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ clone failed: {exc}", style="bold red"))


async def _tree_handler(ctx: CommandContext, args: str) -> None:
    """``/tree`` — show the parent-session lineage of the current session (ignores args).

    Delegates to the host-wired ``tree_action`` flow (walks
    ``session.get_metadata().parent_session_path`` recursively through the
    repo, rendering each ancestor as a row). Pi parity: ``slash-commands.ts``
    ``/tree`` shows the branch lineage. Sprint 6h₂₁ (ADR-0129).
    """

    if ctx.tree_action is None:
        ctx.commit(Text("Tree is unavailable.", style="yellow"))
        return
    try:
        await ctx.tree_action()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ tree failed: {exc}", style="bold red"))


async def _hooks_handler(ctx: CommandContext, args: str) -> None:
    """``/hooks`` — list registered hook handlers per event type (read-only).

    Sprint 6h₂₇ (ADR-0155, WP-7). Read-only viewer over the harness
    :class:`HookBus`: for each hook event that has at least one handler, show the
    handler count. Read-only — edit ``settings.json`` to add/remove hooks.
    Degrades with a committed message when the harness has no ``HookBus``
    (headless / FakeHarness). Ignores ``args`` (like ``/tools`` / ``/session``).
    """

    # ``harness.hooks`` is a public @property returning the HookBus; ``_handlers``
    # is the semi-private event-name → handler-list map (same coupling tier as
    # ``_action_get_all_tools`` in _tools_handler). Read-only; no protected-core
    # mutation.
    hooks = getattr(ctx.harness, "hooks", None)
    handlers = getattr(hooks, "_handlers", None) if hooks is not None else None
    if not isinstance(handlers, dict):
        ctx.commit(Text("Hooks are unavailable.", style="yellow"))
        return
    # Only events with ≥1 handler (the 35-event union is mostly empty → noise);
    # mirrors the banner Feature A counting.
    rows = sorted((name, len(hs)) for name, hs in handlers.items() if hs)
    if not rows:
        ctx.commit(Text("No hook handlers registered.", style="yellow"))
        return
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)  # event type
    table.add_column(style="white", justify="right")  # handler count
    for name, count in rows:
        table.add_row(name, str(count))
    table.add_row("", "")
    table.add_row(
        Text("read-only", style="dim"),
        Text("edit settings.json to change", style="dim"),
    )
    ctx.commit(Panel(table, title="Hooks", box=ROUNDED, border_style="cyan"))


async def _mcp_handler(ctx: CommandContext, args: str) -> None:
    """``/mcp`` — show MCP server status (servers, state, tool counts); ignores args.

    Sprint 6h₂₇ (ADR-0155, WP-7). Delegates to the host-wired ``mcp_status`` flow
    (a read-only panel over the live ``McpClientManager``). Degrades with a
    committed message when no MCP manager is wired (headless / no servers) and on
    any failure (never crashes the REPL).
    """

    if ctx.mcp_status is None:
        ctx.commit(Text("MCP is unavailable.", style="yellow"))
        return
    try:
        await ctx.mcp_status()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ mcp failed: {exc}", style="bold red"))


async def _login_handler(ctx: CommandContext, args: str) -> None:
    """``/login`` — sign in / add a provider API key (ignores args).

    Sprint WP-8 (Feature 1). Delegates to the host-wired ``login_action`` flow
    (the auth wizard: OAuth / built-in API key / custom provider →
    ``AuthStorage``). Degrades with a committed message when unavailable / on any
    failure (never crashes the REPL).
    """

    if ctx.login_action is None:
        ctx.commit(Text("Login is unavailable.", style="yellow"))
        return
    try:
        await ctx.login_action()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ login failed: {exc}", style="bold red"))


async def _logout_handler(ctx: CommandContext, args: str) -> None:
    """``/logout`` — remove a provider's stored credentials (ignores args).

    Sprint WP-8 (Feature 1). Delegates to the host-wired ``logout_action`` flow
    (list stored credentials → picker → confirm → ``AuthStorage.logout``).
    Degrades with a committed message when unavailable / on any failure.
    """

    if ctx.logout_action is None:
        ctx.commit(Text("Logout is unavailable.", style="yellow"))
        return
    try:
        await ctx.logout_action()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ logout failed: {exc}", style="bold red"))


async def _stats_handler(ctx: CommandContext, args: str) -> None:
    """``/stats`` — session usage dashboard (tools, tokens, models); ignores args.

    Sprint WP-8 (Feature 2). Delegates to the host-wired ``stats_action`` flow (a
    framed tabbed viewer over the harness ``SessionStats`` + the TUI-side
    ``SessionActivityTracker`` snapshot — Session / Activity / Efficiency tabs).
    Degrades with a committed message when unavailable / on any failure.
    """

    if ctx.stats_action is None:
        ctx.commit(Text("Stats are unavailable.", style="yellow"))
        return
    try:
        await ctx.stats_action()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ stats failed: {exc}", style="bold red"))


async def _extension_handler(ctx: CommandContext, args: str) -> None:
    """``/extension`` — manage installed extensions + MCP servers (ignores args).

    Sprint WP-8 (Feature 3). Delegates to the host-wired ``extension_action`` flow
    (a read-only framed tabbed viewer over the discovered extensions + the live
    MCP manager — Installed / Discover / Sources tabs). Degrades with a committed
    message when unavailable / on any failure (never crashes the REPL).
    """

    if ctx.extension_action is None:
        ctx.commit(Text("Extension manager is unavailable.", style="yellow"))
        return
    try:
        await ctx.extension_action()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ extension manager failed: {exc}", style="bold red"))


def _context_bar(used: int, window: int, threshold: int, width: int = 32) -> Text:
    """A small 3-segment context-usage bar (PURE).

    ``used`` (cyan) ▸ ``free up to the compaction threshold`` (green) ▸
    ``autocompact buffer`` (yellow). Segment widths are proportional to
    ``window``; the segments always sum to ``width`` (the buffer absorbs
    rounding so the bar never over/underflows). Defensive on a zero/odd window.
    """

    bar = Text()
    if window <= 0:
        return bar
    used = max(min(used, window), 0)
    threshold = max(min(threshold, window), 0)
    used_cells = round(used / window * width)
    # Free band = the room left before compaction triggers.
    free_to_threshold = max(threshold - used, 0)
    free_cells = round(free_to_threshold / window * width)
    used_cells = min(used_cells, width)
    free_cells = min(free_cells, width - used_cells)
    buffer_cells = width - used_cells - free_cells  # absorbs rounding
    bar.append("█" * used_cells, style="cyan")
    bar.append("█" * free_cells, style="green")
    bar.append("█" * buffer_cells, style="yellow")
    return bar


def _estimate_context_categories(ctx: CommandContext, window: int) -> list[str]:
    """Gather the live context sources + render the estimated-composition lines.

    WP-8 (Feature 4). Each source is read defensively (the seams are semi-private
    / may be absent on a sparse harness — same coupling tier as
    :func:`_tools_handler`'s ``_action_get_all_tools`` use) and is OMITTED when
    unreachable. Returns ``[]`` when no category has a non-trivial source (the
    caller then skips the section entirely). Never raises — a gather failure
    degrades to no section, never crashes the ``/context`` handler.
    """

    harness = ctx.harness

    # System prompt — the current effective prompt string. ``_action_get_system_prompt``
    # is the read seam (semi-private, like ``_action_get_all_tools``); guard it.
    system_prompt: str | None = None
    # Annotate the duck-typed read seams so pyright keeps the call-result types
    # (CI runs only ruff+pytest, not pyright — these annotations keep the local
    # type discipline so a future regression in the gather is caught by a type
    # check rather than silently slipping through).
    getter: Callable[[], str] | None = getattr(
        harness, "_action_get_system_prompt", None
    )
    if callable(getter):
        with contextlib.suppress(Exception):
            system_prompt = getter()

    # Built-in tools — ToolInfo(name/description) views (full JSON schemas are not
    # exposed; the name+description text is the best available estimate seam).
    tool_schemas: list[object] = []
    tools_getter: Callable[[], list[Any]] | None = getattr(
        harness, "_action_get_all_tools", None
    )
    if callable(tools_getter):
        with contextlib.suppress(Exception):
            tool_schemas = list(tools_getter() or [])

    # Messages — the public live transcript property.
    messages: list[object] = []
    with contextlib.suppress(Exception):
        messages = list(getattr(harness, "messages", []) or [])

    # Memory — the loaded AGENTS.md text for this cwd (may be absent → omitted).
    memory_text: str | None = None
    with contextlib.suppress(Exception):
        from aelix_coding_agent.cli.agent_context import (  # noqa: PLC0415
            discover_context_files,
        )

        memory_text = discover_context_files(ctx.cwd) or None

    try:
        from aelix_coding_agent.tui.context_usage import (  # noqa: PLC0415
            build_category_lines,
            estimate_categories,
        )

        categories = estimate_categories(
            system_prompt=system_prompt,
            tool_schemas=tool_schemas,
            messages=messages,
            memory_text=memory_text,
        )
        if not categories:
            return []
        return build_category_lines(categories, window)
    except Exception:  # noqa: BLE001 — the section is best-effort; never crash
        return []


async def _context_handler(ctx: CommandContext, args: str) -> None:
    """``/context`` — context-window usage bar + compaction thresholds.

    Sprint 6h₂₇ (ADR-0155, WP-7) + WP-8 (Feature 4). Read-only over
    ``harness.get_session_stats().context_usage``: the MEASURED Used / Free /
    Autocompact-buffer + token totals + percent + the compaction threshold (the
    authoritative numbers). WP-8 additionally appends a HEURISTIC estimated
    per-category composition section (system prompt / built-in tools / memory /
    messages) when those sources are reachable — labelled as an estimate that may
    not sum to the measured total. The section is OMITTED when no source is
    reachable. Never crashes the REPL.
    """

    if not hasattr(ctx.harness, "get_session_stats"):
        ctx.commit(Text("Context usage is unavailable.", style="yellow"))
        return
    try:
        stats = await ctx.harness.get_session_stats()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ context failed: {exc}", style="bold red"))
        return
    usage = getattr(stats, "context_usage", None)
    window = getattr(usage, "context_window", 0) or 0
    tokens = getattr(usage, "tokens", None)
    percent = getattr(usage, "percent", None)
    # WP-8 (Feature 4) — when no live usage has been measured yet (a fresh
    # session, before the first turn), fall back to the bound model's STATIC
    # context-window so /context is useful on session open instead of an
    # "unavailable" line. The Used/Free rows still read "n/a" until the first
    # turn supplies real usage; only the window size + the estimated composition
    # come from the static source. Guarded — degrades to the prior message when
    # no model is bound either.
    if window <= 0:
        model = getattr(ctx.harness, "current_model", None)
        window = getattr(model, "context_window", 0) or 0
    if window <= 0:
        ctx.commit(
            Text("Context usage unavailable (no model bound yet).", style="yellow")
        )
        return

    # The autocompact reserve is read-only from protected core, fully guarded so
    # headless / a core without the symbol degrades to the documented 16384.
    reserve = 16384
    with contextlib.suppress(Exception):
        from aelix_agent_core.harness.core import (  # noqa: PLC0415
            _AUTO_COMPACT_RESERVE_TOKENS as reserve,
        )
    from aelix_coding_agent.cli.list_models import (  # noqa: PLC0415
        format_token_count as fmt,
    )

    threshold = max(window - reserve, 0)
    used = tokens if isinstance(tokens, int) else None
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    table.add_row("context window", fmt(window))
    if used is not None:
        free = max(window - used, 0)
        pct = percent if isinstance(percent, (int, float)) else (used / window * 100)
        table.add_row("used", f"{fmt(used)}  ({pct:.0f}%)")
        table.add_row("free", fmt(free))
        table.add_row("autocompact buffer", fmt(reserve))
        table.add_row(
            "compacts at", f"{fmt(threshold)}  ({threshold / window * 100:.0f}%)"
        )
        table.add_row("", _context_bar(used, window, threshold))
    else:
        # tokens=None sentinel: post-compaction-no-usage-yet OR not measured.
        table.add_row("used", "n/a (no post-turn usage yet)")
        table.add_row(
            "compacts at", f"{fmt(threshold)}  ({threshold / window * 100:.0f}%)"
        )

    # WP-8 (Feature 4) — estimated per-category composition. The measured Used /
    # Free table above stays authoritative; this section is a HEURISTIC
    # (ceil(len/4)) breakdown of the live sources (system prompt, tools, memory,
    # messages), each guarded so an unreachable source is simply omitted. We only
    # append it when ≥1 category is produced (an all-empty estimate adds nothing).
    category_lines = _estimate_context_categories(ctx, window)
    ctx.commit(Panel(table, title="Context", box=ROUNDED, border_style="cyan"))
    if category_lines:
        body = Text()
        body.append(
            "Estimated composition (≈, may not sum to the measured total)\n",
            style="dim",
        )
        body.append("\n".join(category_lines))
        ctx.commit(Panel(body, title="Context composition", box=ROUNDED, border_style="cyan"))


# Aelix TUI keybindings (static — the actual bindings wired in chrome.py). Kept
# next to the registry so /hotkeys and the real bindings can't silently drift.
_HOTKEYS: list[tuple[str, str]] = [
    ("Enter", "Submit message (or steer the running turn)"),
    ("\\ + Enter", "Insert a newline (multi-line input)"),
    ("Alt+Enter", "Queue a follow-up message (while a turn runs)"),
    ("Alt+↑", "Restore queued messages back into the editor"),
    ("Ctrl+T", "Toggle thinking-block visibility"),
    ("Ctrl+V", "Paste a clipboard image (inserts the temp-file path)"),
    ("Ctrl+G", "Open the current input in $EDITOR (vim/nano/…) for long prompts"),
    ("Esc", "Interrupt the running turn"),
    ("Ctrl+C", "Interrupt the turn / clear the input line"),
    ("Ctrl+D", "Exit (on an empty line)"),
    ("Tab / Ctrl+Space", "Autocomplete (slash commands, @file paths)"),
    ("@path", "Mention a file path (autocompletes)"),
    ("! cmd / !! cmd", "Run a bash command (in / out of context)"),
    ("Ctrl+A / Ctrl+E", "Move to line start / end"),
    ("Ctrl+W / Ctrl+K / Ctrl+U", "Delete word back / to line end / to line start"),
    ("↑ / ↓", "Input history (previous / next)"),
]


async def _hotkeys_handler(ctx: CommandContext, args: str) -> None:
    """``/hotkeys`` — show the keyboard shortcuts as a table (ignores args)."""

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    for key, desc in _HOTKEYS:
        table.add_row(key, desc)
    ctx.commit(Panel(table, title="Keyboard shortcuts", box=ROUNDED, border_style="cyan"))


def _last_assistant_text(harness: AgentHarness) -> str:
    """The text of the most recent assistant message (``""`` if none)."""

    messages = list(getattr(harness, "messages", []) or [])
    for msg in reversed(messages):
        if getattr(msg, "role", None) != "assistant":
            continue
        parts = [
            getattr(b, "text", "") or ""
            for b in (getattr(msg, "content", []) or [])
            if getattr(b, "type", None) == "text"
        ]
        text = "\n".join(p for p in parts if p)
        if text.strip():
            return text
    return ""


async def _copy_handler(ctx: CommandContext, args: str) -> None:
    """``/copy`` — copy the last assistant message to the clipboard (ignores args)."""

    text = _last_assistant_text(ctx.harness)
    if not text.strip():
        ctx.commit(Text("Nothing to copy (no assistant message yet).", style="yellow"))
        return
    copy = getattr(ctx.chrome, "copy_to_clipboard", None)
    if not callable(copy) or not copy(text):
        ctx.commit(Text("Clipboard copy is unavailable.", style="yellow"))
        return
    ctx.commit(Text(f"Copied last message ({len(text)} chars) to clipboard.", style="green"))


async def _session_handler(ctx: CommandContext, args: str) -> None:
    """``/session`` — show session id / cwd / name / file + usage (ignores args)."""

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    session = getattr(ctx.harness, "session", None)
    if session is not None:
        with contextlib.suppress(Exception):
            meta = await session.get_metadata()
            table.add_row("id", str(getattr(meta, "id", "—")))
            if getattr(meta, "cwd", None):
                table.add_row("cwd", str(meta.cwd))
        with contextlib.suppress(Exception):
            name = await session.get_session_name()
            if name:
                table.add_row("name", name)
        if getattr(session, "session_file", None):
            table.add_row("file", str(session.session_file))
    if hasattr(ctx.harness, "get_session_stats"):
        with contextlib.suppress(Exception):
            stats = await ctx.harness.get_session_stats()
            table.add_row("messages", str(getattr(stats, "total_messages", 0)))
            tokens = getattr(stats, "tokens", None)
            table.add_row("tokens", str(getattr(tokens, "total", 0)))
            table.add_row("cost (USD)", f"{getattr(stats, 'cost', 0.0):.4f}")
    ctx.commit(Panel(table, title="Session", box=ROUNDED, border_style="cyan"))


async def _name_handler(ctx: CommandContext, args: str) -> None:
    """``/name [text]`` — show or set the session display name."""

    session = getattr(ctx.harness, "session", None)
    if session is None:
        ctx.commit(Text("Session naming is unavailable.", style="yellow"))
        return
    if not args:
        try:
            name = await session.get_session_name()
        except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
            ctx.commit(Text(f"✖ {exc}", style="bold red"))
            return
        ctx.commit(Text(f"session name: {name}" if name else "session name: (unset)"))
        return
    try:
        # ``append_session_name`` is a core Session method (every backend has it).
        await session.append_session_name(args)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ name failed: {exc}", style="bold red"))
        return
    ctx.commit(Text(f"session name → {args}", style="green"))


BUILTIN_COMMANDS: list[BuiltinCommand] = [
    BuiltinCommand("help", "List available commands", _help_handler),
    BuiltinCommand("hotkeys", "Show keyboard shortcuts", _hotkeys_handler),
    BuiltinCommand("model", "Show or switch the active model", _model_handler),
    BuiltinCommand("login", "Sign in / add a provider API key", _login_handler),
    BuiltinCommand("logout", "Remove a provider's stored credentials", _logout_handler),
    BuiltinCommand("clear", "Clear the scrollback transcript", _clear_handler),
    BuiltinCommand("compact", "Compact the conversation context", _compact_handler),
    BuiltinCommand("cost", "Show session token / cost usage", _cost_handler),
    BuiltinCommand("stats", "Session usage statistics (tools, tokens, models)", _stats_handler),
    BuiltinCommand("session", "Show session info (id, cwd, name, usage)", _session_handler),
    BuiltinCommand("name", "Show or set the session name", _name_handler),
    BuiltinCommand("thinking", "Show, pick, or set the reasoning level", _thinking_handler),
    BuiltinCommand("tools", "List registered tools", _tools_handler),
    BuiltinCommand("skills", "List loaded skills", _skills_handler),
    BuiltinCommand("hooks", "List registered hook handlers (read-only)", _hooks_handler),
    BuiltinCommand("mcp", "Show MCP server status (servers, state, tool counts)", _mcp_handler),
    BuiltinCommand("extension", "Manage installed extensions + MCP servers", _extension_handler),
    BuiltinCommand("context", "Show context-window usage + compaction thresholds", _context_handler),
    BuiltinCommand("mode", "Show or set the steering mode", _mode_handler),
    BuiltinCommand(
        "permissions", "Show or cycle the permission posture (shift+tab)", _permissions_handler
    ),
    BuiltinCommand("settings", "View and change settings (modes, theme, thinking, …)", _settings_handler),
    BuiltinCommand(
        "scoped-models", "Choose which models are enabled", _scoped_models_handler
    ),
    BuiltinCommand("statusline", "Configure the status line segments", _statusline_handler),
    BuiltinCommand("expand", "Show the full output of a truncated tool result", _expand_handler),
    BuiltinCommand("export", "Export the transcript to HTML", _export_handler),
    BuiltinCommand("copy", "Copy the last assistant message to the clipboard", _copy_handler),
    BuiltinCommand("resume", "Resume a previous session", _resume_handler),
    BuiltinCommand("new", "Start a fresh session", _new_handler),
    BuiltinCommand("import", "Import a JSONL session file and swap to it", _import_handler),
    BuiltinCommand("fork", "Fork the current session at the last user message", _fork_handler),
    BuiltinCommand("clone", "Clone the current session into a new file", _clone_handler),
    BuiltinCommand("tree", "Show the parent-session lineage", _tree_handler),
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
