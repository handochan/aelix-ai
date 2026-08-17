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
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aelix_coding_agent.tui.stats_dashboard import format_session_cost

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any

    from aelix_agent_core.harness.core import AgentHarness
    from aelix_ai.settings import SettingsManager
    from rich.console import RenderableType

    from aelix_coding_agent.agents import AgentProfile, ProfileDiscoveryResult
    from aelix_coding_agent.subagent_contract import SubagentResult
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
    :param aliases: alternate command words that resolve to this SAME command
        (e.g. ``/exit`` is an alias of ``/quit``). Aliases are matched by
        :func:`match_command` but are NOT listed separately in ``/help`` or
        autocomplete — the command appears exactly once in both.
    """

    name: str
    description: str
    handler: Callable[[CommandContext, str], Awaitable[None]] | None = None
    aliases: tuple[str, ...] = ()


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
    trust_action: Callable[[], Awaitable[None]] | None = None
    """``/trust`` — the project-trust selector (#112, pi ``showTrustSelector``).

    Host-wired like ``login_action``. ``None`` means the host did not provide
    one (non-TUI callers, older hosts), and the handler degrades with a message.
    """
    """``run_tui`` wires this to its ``_open_model_picker`` flow: a ``ctx.ui.select``
    over ``ModelRegistry.get_available()`` with a per-highlight detail footer
    (modality / context-window / base-url / api-key) → ``harness.set_model``. The
    no-arg ``/model`` handler awaits it; ``None`` in headless tests / when no model
    registry is attached, in which case ``/model`` falls back to a status print
    (Sprint 6h₂₆, ADR-0154)."""
    model_registry: Any | None = None
    """``run_tui`` wires the live :class:`ModelRegistry` here so ``/model <id>``
    can adopt the modify_models-injected proxy-ep ``base_url`` (github-copilot
    Business/Enterprise host) instead of the static catalog default that
    ``resolve_model`` returns. ``None`` in headless tests."""
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
    agent_profiles: Callable[[], ProfileDiscoveryResult] | None = None
    """``run_tui`` wires this to ``AgentProfileService.list`` — the discovered
    agent profiles plus the scan diagnostics — so ``/agents list`` and
    ``/agents show`` can render WITHOUT importing the service (headless tests
    pass ``None`` and both degrade to a committed message). ADR-0196.

    ``/agents show`` reads the same result as ``/agents list`` deliberately: a
    profile the user can SEE listed must always be inspectable, including the
    broken one they are trying to debug — which the fatal
    ``AgentProfileService.show`` resolve would refuse."""
    use_agent: Callable[[str | None], Awaitable[str]] | None = None
    """``run_tui`` wires this to ``AgentProfileService.use`` over the LIVE
    harness: swap the whole agent identity (system prompt, skills, tools, model,
    thinking level) in place — no reload, no lost history. ``None`` clears back
    to the CLI baseline. Returns the status line the handler commits; raises
    ``ProfileError`` for anything it refuses. ``None`` in headless tests / when
    entry.py built no service (ADR-0196)."""


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

    An argument in EITHER form — a bare id or ``<provider>/<id>`` — resolves
    against the live registry via
    :func:`~aelix_coding_agent.core.model_argument.resolve_model_argument`, over
    the same auth-filtered, allow-list-narrowed pool the picker offers, so it
    lands on a properly scoped ``(provider, id)`` or is refused outright. It used
    to go straight to :func:`resolve_model`, whose launch-time environment
    heuristics re-stamped the session's own provider onto ids that provider does
    not serve; nothing downstream could catch it and the first symptom was the
    provider's ``400 … is not a valid model ID`` on the next send (#134).

    A third outcome (#136): a ``<provider>/<id>`` whose id this build's catalog
    never saw, under a provider the session IS credentialled for, resolves with a
    ``caution``. The switch and the persist happen exactly as usual — the shape
    round-trips through :func:`resolve_model` on the next launch — but the line
    printed is a yellow ``⚠ switched to …``, deliberately NOT the green
    ``model → …``. That prefix is how the tests tell a success line from
    everything else, so reusing it would quietly disarm those assertions and make
    an unverified id indistinguishable from a catalogued one.

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
    # #136 — set only on the backfill path; read at the print site below, which
    # lives outside this try. Default None keeps every other path green.
    caution: str | None = None
    try:
        from aelix_coding_agent.cli.runtime_bootstrap import (
            enrich_copilot_base_url,
            resolve_model,
        )
        from aelix_coding_agent.core.model_argument import resolve_model_argument
        from aelix_coding_agent.core.runnable_models import (
            is_runnable,
            unsupported_message,
        )

        resolution = await resolve_model_argument(
            args,
            registry=ctx.model_registry,
            current_model=getattr(ctx.harness, "current_model", None),
            settings_manager=ctx.settings_manager,
            warn=lambda message: ctx.commit(Text(message, style="yellow")),
        )
        if resolution.error is not None:
            # Refuse HERE, before any switch/persist/success line: a mismatched
            # provider that is only reported at send time costs the user the turn
            # AND misattributes the failure to the provider (#134).
            ctx.commit(Text(f"✖ {resolution.error}", style="bold red"))
            return
        if resolution.model is not None:
            # A registry hit is ALREADY the modify_models-injected copy, so it
            # carries the proxy-ep base_url enrich_copilot_base_url exists to add.
            model = resolution.model
            caution = resolution.caution
        else:
            # UNDECIDED — no usable registry (headless / RPC / test doubles); the
            # interactive TUI always builds one. Unchanged launch-path resolution.
            # Adopt the registry's proxy-ep base_url for github-copilot (enterprise/
            # business host); resolve_model alone returns the static individual host.
            model = enrich_copilot_base_url(
                resolve_model(args, None), ctx.model_registry
            )
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
    provider = getattr(model, "provider", "")
    if provider:
        # Persist as the default (pi parity: setModel → setDefaultModelAndProvider,
        # agent-session.ts:1416-1425) so the switch SURVIVES restart / /new — the
        # same behaviour as /settings → Default model. Only when a provider is
        # resolved (a bare, providerless model is a soft-fail we never pin).
        if ctx.settings_manager is not None:
            with contextlib.suppress(Exception):
                ctx.settings_manager.set_default_model_and_provider(provider, model_id)
                await ctx.settings_manager.flush()
        if caution is not None:
            # #136 — the switch is real (and persisted, so it survives restart),
            # but the id was never in the catalog: it inherited the provider's
            # protocol and host, nothing more. A DIFFERENT verb and a warning
            # glyph, never "model →": that prefix is the green-success
            # discriminator the /model tests assert on, and an unverified id must
            # not be able to impersonate a catalogued one.
            ctx.commit(
                Text(
                    f"⚠ switched to {provider}/{model_id} — {caution}",
                    style="yellow",
                )
            )
        else:
            # Name the provider: the pair is what actually switched, and a bare id
            # made a wrong-provider resolution look identical to a right one (#134).
            ctx.commit(Text(f"model → {model_id} ({provider})", style="green"))
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
    the harness's own auto-compaction guard at ``core.py:1842``) and render it
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
    # Optional DISPLAY gate (aelix-original): when ``hide_compaction_summary`` is
    # on, show only a terse confirmation instead of the full summary panel. The
    # summary always stays in the LLM context — this gates the transcript only.
    # Guarded: a missing settings manager (headless tests) shows the full panel.
    hide_summary = False
    if ctx.settings_manager is not None:
        with contextlib.suppress(Exception):
            hide_summary = ctx.settings_manager.get_hide_compaction_summary()
    if hide_summary:
        line = Text("✓ Compacted context.", style="green")
        if tokens_before is not None:
            line.append(f"  (was ~{tokens_before} tokens)", style="dim")
        ctx.commit(line)
        return
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
    table.add_row("cost (USD)", format_session_cost(stats, prefix=""))
    ctx.commit(Panel(table, title="Session usage", box=ROUNDED, border_style="cyan"))


def active_tool_views(harness: object) -> list[object]:
    """The tools the model can actually call, as ``ToolInfo``-shaped views.

    ``_action_get_all_tools`` snapshots ``_state.tools`` — every REGISTERED
    tool — but a turn sends only the tools that survive the active filter
    (``harness/core.py:4338-4344`` intersects ``_state.tools`` with
    ``_state.active_tool_names``). Under ``--no-tools`` / ``--tools a,b`` the
    two lists differ, so a readout built on the registered list advertises
    tools the model does not have.

    ``_action_get_active_tools`` applies that same intersection and is the
    truthful source; it yields names only, so the registered views are filtered
    by it to keep the descriptions. A harness without the active seam (headless
    fakes, embedders) carries no filter to apply, so its registered list stands.

    Both readouts are display-only — neither may change which tools are enabled.
    """

    if not hasattr(harness, "_action_get_all_tools"):
        return []
    tools = list(harness._action_get_all_tools())  # type: ignore[attr-defined]
    getter = getattr(harness, "_action_get_active_tools", None)
    if not callable(getter):
        return tools
    active = {
        str(name) for name in cast("Iterable[object]", getter() or ())
    }
    return [t for t in tools if getattr(t, "name", str(t)) in active]


async def _tools_handler(ctx: CommandContext, args: str) -> None:
    """``/tools`` — list the tools the model can actually call."""

    # ``_action_get_all_tools`` / ``_action_get_active_tools`` are semi-private
    # (documented coupling, Sprint 6h₁₂d) — wrapping them would touch protected
    # core.py. ``active_tool_views`` hasattr-guards both for the headless
    # FakeHarness, then calls the typed methods directly.
    try:
        tools = active_tool_views(ctx.harness)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ tools failed: {exc}", style="bold red"))
        return
    if not tools:
        ctx.commit(Text("No tools active.", style="yellow"))
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


# === /agents (ADR-0196) =====================================================
# Kept beside /skills: an agent profile is the same shape of resource (a
# markdown file with YAML frontmatter, discovered from a user tier plus a
# trust-gated project tier), it just carries the agent's IDENTITY rather than a
# capability the model may reach for.

_AGENTS_USAGE = (
    "Usage: /agents [list | show <name> | use <name> | use --none | "
    "run <name> <task>]"
)
"""One source for the usage text, shared by every error path below so the help
the user is shown can never drift from what the dispatch actually accepts."""

_AGENTS_BODY_PREVIEW_LINES = 12
"""How much of a profile's markdown body ``/agents show`` previews. The body IS
the system prompt and is routinely hundreds of lines; the panel exists to
IDENTIFY a profile, not to reproduce it."""


def _profile_cell(value: object) -> str:
    """Render one profile field as a table cell — ``—`` when unset, never ``None``.

    A literal ``None`` in a table is the classic "is that the string or the
    absence?" ambiguity. :attr:`AgentProfile.tools` gets its own renderer
    (:func:`_profile_tools_cell`) because for that ONE field the two readings
    mean opposite things.
    """

    if value is None:
        return "—"
    text = str(value)
    return text if text.strip() else "—"


def _profile_tools_cell(profile: AgentProfile, live_tools: Any = None) -> Text:
    """``tools`` is THREE-valued and the render must not flatten it.

    ``None`` (key absent) = inherit the ambient tool set; ``()`` (``tools: []``)
    = NO tools at all; a list = an allowlist. Showing the first two identically
    would display opposite intents as the same thing — the same collapse that
    made ``--tools ''`` enable everything (``entry.py:653-675``).

    #155 — annotated when a name in the allowlist is not registered in THIS
    session. A profile's ``tools:`` is the persistent form of that defect: an
    unknown name there fails EVERY launch of the profile until the file is
    edited, so the row is where the user should find out, not the next startup.
    Same annotate-in-place shape as :func:`_profile_model_cell` (#152), and the
    same rule — never hide the profile, because it is still the user's file and
    the name may be an extension tool that a build loading that extension does
    register.

    ``live_tools`` is the session's real tool list, NOT ``ALL_TOOL_NAMES``:
    validating against the seven built-ins would flag every legitimate
    extension and MCP tool as broken (``--tools echo`` with the echo extension
    is measurably valid). :data:`None` means "no registry available" and
    annotates nothing — the same swallow-and-degrade rule the model cell uses,
    for the same reason.
    """

    if profile.tools is None:
        return Text("—")
    if not profile.tools:
        return Text("(none)")
    cell = Text(", ".join(profile.tools))
    if live_tools is None:
        return cell
    try:
        known = {getattr(tool, "name", tool) for tool in live_tools}
        unknown = [name for name in profile.tools if name not in known]
        if unknown:
            cell.append(f"  (unknown here: {', '.join(unknown)})", style="yellow")
    except Exception:  # noqa: BLE001 — an unannotated row beats a broken list
        return cell
    return cell


def _profile_model_cell(profile: AgentProfile, registry: Any) -> Text:
    """The MODEL cell, annotated when this build cannot run that model (#152).

    ``/agents use`` now refuses such a profile, but a refusal after the user has
    picked is the late half of the answer — the row itself should say so first.
    This is the same annotate-in-place shape ``/login`` and ``/scoped-models``
    settled on: never hide a profile, because it is still the user's file and
    still selectable with ``--agent`` in a build that HAS the adapter.

    Resolution failures are swallowed on purpose: an un-annotated row is a far
    smaller harm than a ``/agents list`` that raises, and the refusal in
    ``AgentProfileService.use`` is the load-bearing gate either way.
    """

    cell = Text(_profile_cell(profile.model))
    if profile.model is None:
        return cell
    try:
        from aelix_coding_agent.cli.runtime_bootstrap import resolve_model
        from aelix_coding_agent.core.runnable_models import is_runnable

        model = resolve_model(profile.model, profile.provider, registry, None)
        if not is_runnable(model):
            cell.append("  (no adapter in this build)", style="yellow")
    except Exception:  # noqa: BLE001 — an unannotated row beats a broken list
        return cell
    return cell


def _render_agents_table(
    result: ProfileDiscoveryResult, registry: Any = None, live_tools: Any = None
) -> RenderableType:
    """``/agents list`` — name / scope / model / tools / description.

    ``live_tools`` (#155) is the session's registered tool list, used to flag a
    profile whose ``tools:`` names something this build has no tool for. Like
    ``registry``, it defaults to :data:`None` = do not annotate, so the many
    render-only tests keep calling this with one argument.
    """

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="magenta", no_wrap=True)
    table.add_column(style="white", no_wrap=True)
    # TOOLS and DESCRIPTION wrap — an allowlist or a one-liner can be long, and
    # pushing the remaining columns off the terminal is worse than two rows.
    table.add_column(style="white")
    table.add_column(style="white")
    table.add_row("NAME", "SCOPE", "MODEL", "TOOLS", "DESCRIPTION", style="dim")
    for profile in sorted(result.profiles, key=lambda p: p.name):
        table.add_row(
            profile.name,
            profile.scope,
            _profile_model_cell(profile, registry),
            _profile_tools_cell(profile, live_tools),
            _profile_cell(profile.description),
        )
    return Panel(table, title="Agent profiles", box=ROUNDED, border_style="cyan")


def _render_agents_diagnostics(result: ProfileDiscoveryResult) -> RenderableType | None:
    """The scan diagnostics, or ``None`` when the scan was clean.

    Rendered rather than dropped because two of them are load-bearing and this
    is their ONLY surface: a project profile that overrides a user profile of
    the same name (project wins — the warning naming both absolute paths is half
    of the ratified mitigation, the startup confirmation prompt is the other
    half), and a profile that failed to parse, which by definition is absent
    from the table above.
    """

    if not result.diagnostics:
        return None
    body = Text()
    for diag in result.diagnostics:
        error = diag.type == "error"
        body.append(
            f"{'✖' if error else '!'} {diag.message}\n",
            style="bold red" if error else "yellow",
        )
        body.append(f"  {diag.path}\n", style="dim")
    # ``Text.rstrip`` mutates IN PLACE and returns ``None`` — it cannot be
    # inlined into the Panel call.
    body.rstrip()
    return Panel(
        body,
        title="Agent profile diagnostics",
        box=ROUNDED,
        border_style="yellow",
    )


def _render_agent_profile(profile: AgentProfile) -> list[RenderableType]:
    """``/agents show <name>`` — the parsed fields, then the DRY RUN.

    The second panel is the auditable half: the exact flags
    ``resolver.profile_to_flags`` would emit (shell-quoted, so a path with a
    space is unambiguous) plus the head of the body that becomes the system
    prompt. "What will this identity actually do" is answerable before running
    it, not after.
    """

    import shlex

    from aelix_coding_agent.agents import profile_to_flags

    fields = Table.grid(padding=(0, 2))
    fields.add_column(style="bold cyan", no_wrap=True)
    fields.add_column(style="white")
    fields.add_row("name", profile.name)
    fields.add_row("description", profile.description)
    fields.add_row("scope", profile.scope)
    fields.add_row("file", profile.file_path)
    # Unset optionals are OMITTED here rather than dashed: this panel is a dry
    # run of what the profile DOES, and a row saying "nothing" is noise. (The
    # list table above cannot omit — its columns have to line up.)
    if profile.model is not None:
        fields.add_row("model", profile.model)
    if profile.provider is not None:
        fields.add_row("provider", profile.provider)
    if profile.thinking is not None:
        fields.add_row("thinking", profile.thinking)
    fields.add_row("tools", _profile_tools_cell(profile))
    fields.add_row("builtin tools", "yes" if profile.builtin_tools else "no")
    fields.add_row("system prompt", profile.system_prompt)
    fields.add_row("context files", "yes" if profile.context_files else "no")
    fields.add_row("inherit skills", "yes" if profile.inherit_skills else "no")
    if profile.skills:
        fields.add_row("skills", "\n".join(profile.skills))
    fields.add_row("inherit extensions", "yes" if profile.inherit_extensions else "no")
    if profile.extensions:
        fields.add_row("extensions", "\n".join(profile.extensions))

    dry_run = Text()
    dry_run.append("flags  ", style="bold cyan")
    dry_run.append(
        shlex.join(profile_to_flags(profile, prompt_path=profile.file_path))
    )
    lines = profile.body.strip().splitlines()
    if lines:
        dry_run.append("\n\nsystem prompt\n", style="bold cyan")
        dry_run.append("\n".join(lines[:_AGENTS_BODY_PREVIEW_LINES]))
        hidden = len(lines) - _AGENTS_BODY_PREVIEW_LINES
        if hidden > 0:
            dry_run.append(f"\n… (+{hidden} more lines)", style="dim")

    return [
        Panel(
            fields,
            title=f"Agent profile: {profile.name}",
            box=ROUNDED,
            border_style="cyan",
        ),
        Panel(dry_run, title="Dry run", box=ROUNDED, border_style="cyan"),
    ]


# === /agents run (ADR-0197 §(c)/§(f), P2) ====================================
# A product-core BUILT-IN, and it has to be: ``shell.py:3215-3232`` runs
# ``match_command`` (built-ins) first and only falls through to
# ``dispatch.try_execute`` when no built-in claims the word, while
# ``extensions/command_dispatch.py:76-85`` splits an extension command on the
# FIRST SPACE — so an extension command name can never contain one. ``/agents``
# is already a built-in, therefore ``/agents run`` can only be served here.
# Spec §6.3's ``register_command("run", …)`` under ``/agents`` is not
# implementable as written.
#
# Everything below is INTERFACE ONLY (ADR-0008 as amended by ADR-0197): it calls
# a bound Protocol (``harness.runtime.subagents``), and it never spawns, never
# builds child argv, never parses a child stream and never authors consent
# policy. ``spawn()`` takes its own spawn-time consent internally (§(i)); the
# grant type is deliberately unreachable from here.

_DELEGATION_UNAVAILABLE = (
    "Delegation is unavailable. Relaunch with --agents, or enable "
    "[features] agents in /settings and restart aelix."
)
"""``--agents`` FIRST, and the restart said out loud.

The previous wording — "Enable it with [features] agents (/settings) or relaunch
with --agents" — read as though the toggle alone were sufficient, and it is not:
the flag is consumed once per process when the harness is built, so a user who
followed the first clause saw the toggle succeed, ran the same command, and got
this same line again with nothing to tell them why. ``--agents`` leads because it
is the path that works right now."""

_PROJECT_SCOPE_MARKER = "per-identity confirmation"
"""BACK-COMPAT FALLBACK ONLY. The typed refusal is
:class:`~aelix_coding_agent.subagent_contract.ProjectScopeRefused`.

This substring used to be the WHOLE test (P2 review, MEDIUM #8), and the phrase
is produced by exactly one implementation — ``aelix_agents/runtime.py``. A
second runtime implementing ``resolve_profile`` exactly as the Protocol
docstring instructed, raising e.g. ``ProfileError("profile 'x' is project-local
and requires confirmation")``, fell straight through to
``✖ agents run failed:``; there was then NO path in the product to run a
project-scoped profile under it, because the confirmation dialog was never
offered and the user could not cure it. The contract now DECLARES the refusal
type, so a second implementer has something to raise.

Kept as a fallback because a runtime built against an older contract cannot
raise a class that did not exist yet. A miss is FAIL-CLOSED either way: an
unrecognised refusal is surfaced verbatim and no confirmation is offered, so the
worst case is a user being told to re-run rather than a profile running
unconfirmed.

Deliberately NARROWER than ``"project-local"``: ``agents/discovery.py:357-363``
raises a different project-local refusal — the DIRECTORY is untrusted — that no
per-identity answer can cure (it needs ``--approve`` at launch, and the second
resolve would raise it again). Prompting for a decision that changes nothing is
how users learn to click through consent dialogs. The typed class carries the
same distinction: it names the IDENTITY gate, never the directory gate.
"""


def _subagent_runtime(ctx: CommandContext) -> Any | None:
    """The bound :class:`SubagentRuntime`, or ``None`` when delegation is off.

    ``None`` is the ORDINARY state in P2 — ``[features] agents`` defaults to
    False and a delegated child never binds a runtime at all — so every caller
    degrades with a committed message rather than raising. Read through the LIVE
    ``ctx.harness`` on every call: ``shell.py`` re-points the command context at
    the new harness on /new, /fork, /resume and /reload, and each of those builds
    a fresh ``_ExtensionRuntime`` whose seam the extension re-binds.
    """

    return getattr(getattr(ctx.harness, "runtime", None), "subagents", None)


def _is_project_scope_refusal(exc: Exception) -> bool:
    """Did ``resolve_profile`` refuse because the profile is project-scoped?

    TYPE FIRST, substring second (P2 review, MEDIUM #8). The typed check is what
    lets a second orchestration runtime serve this confirmation path at all; the
    substring is the documented back-compat fallback for a runtime built against
    a contract that predates the class. See :data:`_PROJECT_SCOPE_MARKER`.

    Function-local import so the contract stays a leaf module and this file does
    not pay for it on every startup — the same rule ``extensions/api.py`` follows.
    """

    from aelix_coding_agent.subagent_contract import ProjectScopeRefused

    if isinstance(exc, ProjectScopeRefused):
        return True
    return _PROJECT_SCOPE_MARKER in str(exc)


def _project_agent_source_path(ctx: CommandContext, name: str) -> str | None:
    """Best-effort absolute path of the project-scoped profile ``name``.

    Shown in the confirmation dialog because the PATH is the part a human can go
    and read; a bare name is exactly what a name collision weaponises (a project
    profile WINS against the user's own of the same name — ``agents/service.py:100-101``).
    Resolved through the same listing ``/agents show`` uses, and ``None`` when no
    listing is wired, in which case the dialog says so rather than inventing one.
    """

    lookup = ctx.agent_profiles
    if lookup is None:
        return None
    try:
        result = lookup()
    except Exception:  # noqa: BLE001 — a broken listing must not block the gate
        return None
    match = next(
        (p for p in result.profiles if p.name == name and p.scope == "project"),
        None,
    )
    return None if match is None else match.file_path


async def _confirm_project_agent_for_run(
    ctx: CommandContext, name: str, source_path: str | None
) -> bool:
    """The per-identity gate for a project-scoped ``/agents run`` (finding B5).

    SECURITY. Directory trust is a yes-once decision ancestors inherit
    (``cli/project_trust.py:71-72``); it is NOT consent to a project-local
    IDENTITY that showed up in the repo later. ``/agents run`` is user-typed, so
    it MAY take that consent — the model-driven ``agent`` tool never can, and
    fail-closes with no prompt (``aelix_agents/runtime.py``'s
    ``allow_project=False`` default). That asymmetry is the point: there the
    model chose the name.

    The copy is ``cli/entry.py``'s, shared rather than duplicated. The TRANSPORT
    is not: ``_confirm_project_agent`` drives a dedicated one-shot
    ``prompt_toolkit.Application`` built for the pre-``run_tui`` window, which
    cannot run while the REPL's own Application is live. This uses the extension
    UI seam instead — ``shell.py:2518`` binds the real TUI context onto
    ``harness.runtime`` and re-binds it on every rebuild (``:1565``), so the
    modal here is the same surface the permission dialog uses.

    FAIL-CLOSED in every direction: no seam, a headless binding (whose ``select``
    raises), a dialog that blows up, Esc (``None``) and any answer that is not
    the affirmative option all return False. ``BaseException`` is deliberately
    NOT caught — a ``CancelledError`` means the REPL is tearing down, and the
    right response is to propagate rather than synthesise a decision the human
    never made.
    """

    # The three ``getattr`` hops are deliberate — a missing or mid-rebuild runtime
    # must degrade to a decline, never raise — but they cost the static type:
    # ``AgentHarness.runtime`` is the KERNEL's ``_ExtensionRuntime | None``
    # (``harness/core.py:246``), so the chain widens to ``object`` and ``callable()``
    # then narrows THAT to ``(...) -> object``, which makes the guarded ``await``
    # below a type error. The annotation states the seam's real shape
    # (``extensions/ext_ui.py:186-193``) so the narrowing lands on it instead;
    # ``callable()`` remains the only runtime gate.
    select: Callable[..., Awaitable[Any]] | None = getattr(
        getattr(getattr(ctx.harness, "runtime", None), "ui", None), "select", None
    )
    if not callable(select):
        return False
    from aelix_coding_agent.cli.entry import (
        PROJECT_AGENT_CONFIRM_OPTIONS,
        project_agent_confirm_body,
    )

    body = project_agent_confirm_body(name, source_path or "(path unavailable)")
    try:
        answer = await select(body, list(PROJECT_AGENT_CONFIRM_OPTIONS))
    except Exception:  # noqa: BLE001 — a broken dialog is a decline, never a yes
        return False
    # Matched by IDENTITY against the rendered affirmative, never by substring:
    # a profile is free to name itself "Run this profile".
    return answer == PROJECT_AGENT_CONFIRM_OPTIONS[0]


# ``str.translate`` table deleting C0, DEL and C1 — the same set
# ``panel._flatten`` and ``consent._sanitize_field`` refuse. Restated inline, NOT
# imported: the band rule (ADR-0197) forbids product-core importing
# ``aelix_agents``. ``0x9b`` is in the set because it IS a one-byte CSI — the
# spelling of ``\x1b[`` that survives a scrub which only chases the ESC byte.
_CONTROL_KILL = dict.fromkeys([*range(0x20), 0x7F, *range(0x80, 0xA0)])


def sanitize_for_terminal(value: str) -> str:
    """De-fang one attacker-influenceable string before it is RENDERED (issue #121).

    :data:`_CONTROL_KILL` with no width bound and no truncation marker — for
    values that must survive intact, such as a path. :func:`_sanitize_child_field`
    is the variant for grid CELLS, where a width budget also has to be enforced.

    PUBLIC because the startup banner needs it: ``shell._build_banner`` renders
    the session ``cwd`` verbatim, and POSIX permits every byte but ``/`` and NUL
    in a path component, so a directory that arrives with a ``git clone`` steers
    the terminal. Rich is NOT a defence — measured by rendering the banner over a
    directory named ``proj\\x1b]0;pwned\\x07\\x1b[31mZ`` through a ``no_color``
    console::

        cwd:  /tmp/…/proj\\x1b]0;pwned\\x1b[31mZ

    Rich dropped the BEL and passed the ESC through, so what reached the terminal
    was an unterminated OSC title-set sequence — which then eats every byte
    printed after it until some terminator turns up.

    DELETION, not escaping, matching :func:`_sanitize_child_field` and
    ``consent._sanitize_field``: what survives ``\\x1b[8m`` is the inert literal
    ``[8m``, which renders as itself and reads as visibly odd. The table also
    covers ``\\n`` and ``\\t``, so a path carrying a newline cannot break a
    one-row panel layout either.
    """

    return value.translate(_CONTROL_KILL)


def _sanitize_child_field(value: str, width: int = 40) -> str:
    """Bound and de-fang one CHILD-AUTHORED string for the result grid.

    ``model``/``provider`` are read off the child's own ``message_end`` verbatim
    (``stream.py:559-563`` → ``envelope.py:384-385``), so they are attacker
    controlled exactly like ``current_tool``. Rich ``Text`` disables MARKUP
    parsing but writes raw content ESC / C1 straight to the terminal — a
    ``\\x1b[2J`` clears the parent's screen and a one-byte ``\\x9b`` drives its
    cursor — so ``Text`` alone is not a defence and the bytes must be removed
    here.

    The three steps ``panel._flatten`` takes, replicated because the band rule
    forbids importing it: collapse whitespace (kills newlines), delete C0 / DEL /
    C1 (kills ESC and the one-byte CSI), then bound the width so a multi-kilobyte
    model cannot blow out the panel. Returns ``""`` for a value that was nothing
    but control bytes — the caller treats that as "nothing to name".
    """

    flat = " ".join(value.split()).translate(_CONTROL_KILL)
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _render_subagent_result(result: SubagentResult) -> list[RenderableType]:
    """Render one :class:`SubagentResult` for scrollback (ADR-0197 §(l)).

    A DECLINE gets its own one-line yellow rendering rather than a red failure
    panel: nothing ran, nothing failed, and a scary panel would train users to
    stop reading them.

    ``permission_mode`` is on its own row and is never elided. It is the posture
    the child ACTUALLY ran under — the clamp (§(e)), possibly raised by one
    explicit human answer at the consent dialog (§(i)) — and it is the only place
    the user can see after the fact what authority they granted.
    """

    from rich.console import Group

    if result.status == "declined":
        return [
            Text(
                f"Delegation to {result.profile!r} declined — nothing was started.",
                style="yellow",
            )
        ]

    summary = (result.summary or "").strip()
    body = Text(summary or "(no output)")
    if result.truncated:
        body.append("\n\n… output truncated to the profile's cap.", style="dim")
    if result.error:
        body.append(f"\n\n{result.error}", style="red")

    usage = result.usage
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column(style="white")
    grid.add_row("status", result.status)
    # WHICH MODEL THE CHILD ACTUALLY RAN (``SubagentResult.model``, read off its
    # own ``message_end``). Surfaced here so the substitution a profile with no
    # ``model:`` triggers — the persisted default at a different price — stops
    # being visible only on the bill. ``model`` AND ``provider`` are
    # CHILD-AUTHORED and unsanitised at the source, so EACH is de-fanged through
    # :func:`_sanitize_child_field` (whitespace collapsed, control chars deleted,
    # width bound) before it reaches ``Text`` — ``Text`` blocks Rich MARKUP but
    # not raw ESC/C1, so it is not a defence on its own. The row is omitted when
    # there is nothing left to name: ``model`` absent (a child that errored
    # before any ``message_end``) or a value that sanitised to empty — no ``—``
    # row for it. ``provider/id`` is composed from the sanitised pieces with no
    # dangling ``/`` when the provider is absent.
    model = _sanitize_child_field(result.model) if result.model else ""
    if model:
        provider = (
            _sanitize_child_field(result.provider) if result.provider else ""
        )
        grid.add_row("model", Text(f"{provider}/{model}" if provider else model))
    # ``—`` rather than an omitted row: "which posture did that child get" must
    # be answerable even when a runtime declined to say.
    grid.add_row("permission", result.permission_mode or "—")
    grid.add_row("turns", str(usage.turns))
    grid.add_row(
        "tokens",
        f"in {usage.input} · out {usage.output} · cache-read {usage.cache_read}",
    )
    grid.add_row("cost", f"${usage.cost:.4f}")
    grid.add_row("elapsed", f"{result.elapsed_ms / 1000:.1f}s")
    if result.dropped_tools:
        # The child's tool set is the INTERSECTION with the parent's live grant,
        # so a profile asking for more silently gets less — say so, or a
        # half-equipped agent looks like a bad model.
        grid.add_row("dropped tools", ", ".join(result.dropped_tools))
    if result.dropped_lines:
        grid.add_row("dropped lines", str(result.dropped_lines))

    parts: list[RenderableType] = [body, grid]
    details = result.details
    if details and len(details) > len(summary):
        parts.append(
            Text(
                f"Full output: {len(details.encode('utf-8'))} bytes "
                "(the summary above is capped).",
                style="dim",
            )
        )
    return [
        Panel(
            Group(*parts),
            title=f"Subagent: {result.profile}",
            box=ROUNDED,
            border_style="green" if result.ok else "red",
        )
    ]


async def _agents_handler(ctx: CommandContext, args: str) -> None:
    """``/agents [list | show <name> | use <name> | use --none]`` (ADR-0196).

    - no arg / ``list`` — every discoverable profile + the scan diagnostics.
    - ``show <name>`` — the parsed fields and the dry-run flags/prompt.
    - ``use <name>`` — swap the LIVE identity (prompt, skills, tools, model,
      thinking) with no reload and no lost history.
    - ``use --none`` — restore the identity the process was launched with.
    - ``run <name> <task>`` — delegate one task to a subagent (ADR-0197, P2).

    Both callbacks are optional: headless tests and any host that built no
    service leave them ``None``, and every branch degrades to a committed
    message rather than raising. Nothing here spawns: ``run`` calls a bound
    Protocol and the spawn itself lives entirely in the ``aelix-agents``
    extension.
    """

    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub in ("list", "show"):
        if ctx.agent_profiles is None:
            ctx.commit(Text("Agent profiles are unavailable.", style="yellow"))
            return
        try:
            result = ctx.agent_profiles()
        except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
            ctx.commit(Text(f"✖ agents failed: {exc}", style="bold red"))
            return
        if sub == "list":
            if not result.profiles and not result.diagnostics:
                ctx.commit(Text("No agent profiles found.", style="yellow"))
                return
            if result.profiles:
                # #155 — the live registry, so an extension/MCP tool named in a
                # profile is not mis-flagged as unknown.
                live_tools = getattr(
                    getattr(ctx.harness, "state", None), "tools", None
                )
                ctx.commit(
                    _render_agents_table(result, ctx.model_registry, live_tools)
                )
            diagnostics = _render_agents_diagnostics(result)
            if diagnostics is not None:
                ctx.commit(diagnostics)
            return
        if not rest:
            ctx.commit(
                Text(f"/agents show needs a profile name. {_AGENTS_USAGE}", style="yellow")
            )
            return
        # Resolved against the LISTED profiles (not the fatal
        # ``AgentProfileService.show``) so anything visible in /agents list is
        # always inspectable — including the broken profile being debugged.
        match = next((p for p in result.profiles if p.name == rest), None)
        if match is None:
            known = ", ".join(sorted(p.name for p in result.profiles)) or "(none)"
            ctx.commit(
                Text(
                    f"✖ unknown agent profile {rest!r}; available: {known}",
                    style="bold red",
                )
            )
            return
        for renderable in _render_agent_profile(match):
            ctx.commit(renderable)
        return

    if sub == "use":
        if ctx.use_agent is None:
            ctx.commit(Text("Agent profile switching is unavailable.", style="yellow"))
            return
        if not rest:
            # Deliberately NOT "clear on a bare /agents use": clearing the
            # identity is a real change and has to be asked for by name.
            ctx.commit(
                Text(
                    f"/agents use needs a profile name (or --none). {_AGENTS_USAGE}",
                    style="yellow",
                )
            )
            return
        try:
            status = await ctx.use_agent(None if rest == "--none" else rest)
        except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
            ctx.commit(Text(f"✖ agents use failed: {exc}", style="bold red"))
            return
        ctx.commit(Text(status, style="green"))
        return

    if sub == "run":
        runtime = _subagent_runtime(ctx)
        if runtime is None:
            ctx.commit(Text(_DELEGATION_UNAVAILABLE, style="yellow"))
            return
        name, _, task = rest.partition(" ")
        task = task.strip()
        if not name or not task:
            ctx.commit(
                Text(f"/agents run needs a name and a task. {_AGENTS_USAGE}", style="yellow")
            )
            return
        # ``allow_project`` starts FALSE and is only ever raised by the explicit
        # per-identity confirmation below (ADR-0197 §(f)) — the identity door is
        # deliberately STRICTER than the authority door, and it fails closed.
        try:
            resolved = runtime.resolve_profile(name)
        except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
            if not _is_project_scope_refusal(exc):
                ctx.commit(Text(f"✖ agents run failed: {exc}", style="bold red"))
                return
            path = _project_agent_source_path(ctx, name)
            if not await _confirm_project_agent_for_run(ctx, name, path):
                ctx.commit(Text("Project agent profile declined.", style="yellow"))
                return
            try:
                resolved = runtime.resolve_profile(name, allow_project=True)
            except Exception as confirmed_exc:  # noqa: BLE001
                ctx.commit(
                    Text(f"✖ agents run failed: {confirmed_exc}", style="bold red")
                )
                return
        try:
            # ``spawn`` takes its OWN spawn-time consent (§(i)) and returns a
            # ``status="declined"`` result rather than raising when the human
            # says no — so an exception here is a real failure, not a refusal.
            result = await runtime.spawn(resolved, task)
        except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
            ctx.commit(Text(f"✖ agents run failed: {exc}", style="bold red"))
            return
        for renderable in _render_subagent_result(result):
            ctx.commit(renderable)
        return

    ctx.commit(Text(f"Unknown /agents subcommand {sub!r}. {_AGENTS_USAGE}", style="yellow"))


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


async def _trust_handler(ctx: CommandContext, args: str) -> None:
    """``/trust`` — decide project trust for this folder (ignores args).

    pi parity (``interactive-mode.ts:2953``). Before #112 aelix had no way to
    answer the trust question after startup: the one-shot selector runs before
    the TUI exists, so declining — or never being asked, then watching resources
    appear — left restarting as the only option.
    """

    if ctx.trust_action is None:
        ctx.commit(Text("Project trust is unavailable.", style="yellow"))
        return
    try:
        await ctx.trust_action()
    except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ trust failed: {exc}", style="bold red"))


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


async def _extension_new(ctx: CommandContext, name: str) -> None:
    """``/extension new <name>`` — ASK where it goes, then scaffold it (#161).

    THE MEASUREMENT THAT PUT THIS HERE. Issue #161 offered three shapes and
    said to choose on a live-model probe. Shape 1 — the self-extension block
    asks in prose — was built and probed against a real model in a real
    interactive TUI, twice with each wording. It complied in ONE of four runs,
    and the variable was how the USER phrased the request: "Please add it" got
    an ask out of the strengthened wording, "Do the work." did not, and the
    shipped wording got no ask at all. Compliance that depends on the user's
    phrasing is not a property anyone can ship.

    So the choice becomes a dialog the user drives, which is the one thing a
    prompt cannot be talked out of. The permission ladder could not carry it —
    ``builtin/permission.py`` is allow/deny only and has no "somewhere else
    instead" — so it lives on a command.

    WHAT THIS DOES NOT FIX, stated plainly: a command the model never invokes
    changes nothing. The self-extension block therefore names it, and still
    carries both absolute paths for the case where the model writes directly
    anyway. The honest claim is "the user has a first-class way to make the
    choice, and the model has a one-line way to hand it to them" — not "the
    model now asks".
    """

    from aelix_coding_agent.cli.config import get_agent_dir
    from aelix_coding_agent.extensions.scaffold import (
        create_extension,
        extension_targets,
        validate_extension_name,
    )

    problem = validate_extension_name(name)
    if problem is not None:
        ctx.commit(Text(problem, style="yellow"))
        return
    name = name.strip()

    targets = extension_targets(ctx.cwd, get_agent_dir())
    # The same three ``getattr`` hops ``_confirm_project_agent`` uses, and for
    # the same reason: a missing or mid-rebuild runtime must degrade, never
    # raise. See that function for why the annotation is needed to keep the
    # guarded ``await`` type-checkable.
    select: Callable[..., Awaitable[Any]] | None = getattr(
        getattr(getattr(ctx.harness, "runtime", None), "ui", None), "select", None
    )
    if not callable(select):
        # No dialog surface (headless, or a host that wired no UI). Refusing is
        # right: the whole point of this command is the question, and silently
        # picking one target is the behaviour it exists to replace.
        ctx.commit(
            Text(
                "/extension new needs an interactive session to ask where the "
                "extension should go. Write the file yourself, to one of:\n"
                f"  {targets['global'] / (name + '.py')}   (every project)\n"
                f"  {targets['project'] / (name + '.py')}   (this project only)",
                style="yellow",
            )
        )
        return

    options = [
        "Every project — available wherever you run aelix, no trust gate",
        "This project only — ships to everyone who clones it, trust-gated",
    ]
    try:
        chosen = await select(f"Where should the {name!r} extension go?", options)
    except Exception as exc:  # noqa: BLE001 — a broken dialog writes nothing
        ctx.commit(Text(f"✖ could not ask where to put it: {exc}", style="bold red"))
        return
    if chosen is None:
        ctx.commit(Text("Cancelled — nothing was written.", style="yellow"))
        return
    # Matched by IDENTITY against the rendered option, never by substring — the
    # same rule ``_confirm_project_agent`` follows.
    scope = "global" if chosen == options[0] else "project"

    try:
        path = create_extension(targets[scope], name)
    except FileExistsError as exc:
        ctx.commit(
            Text(f"{exc} already exists — pick another name.", style="bold red")
        )
        return
    except OSError as exc:  # noqa: BLE001 — surface, never kill the REPL
        ctx.commit(Text(f"✖ could not create the extension: {exc}", style="bold red"))
        return

    lines = Text()
    lines.append(f"Created {path}\n", style="green")
    if scope == "project":
        # The tier that fails SILENTLY when the project is untrusted — measured
        # in ``_extension_signpost``'s docstring, and the reason the global
        # target is listed first there. Saying so here is the whole value of
        # asking rather than guessing.
        lines.append(
            "This project must be trusted for it to load; an untrusted project "
            "skips it with no error.\n",
            style="yellow",
        )
    lines.append("Edit it, then /reload (or restart aelix) to load it.")
    ctx.commit(lines)


async def _extension_handler(ctx: CommandContext, args: str) -> None:
    """``/extension [new <name>]`` — the manager, or the scaffold flow.

    Sprint WP-8 (Feature 3). With no arguments, delegates to the host-wired
    ``extension_action`` flow (a read-only framed tabbed viewer over the
    discovered extensions + the live MCP manager — Installed / Discover /
    Sources tabs). Degrades with a committed message when unavailable / on any
    failure (never crashes the REPL).

    ``new <name>`` is issue #161's shape 2 — see :func:`_extension_new`. It is a
    SUBCOMMAND rather than a command of its own because ``/extension`` already
    owns this noun, and a second top-level verb would be a second thing for the
    signpost to name.
    """

    subcommand = args.strip()
    if subcommand.split(maxsplit=1)[:1] == ["new"]:
        rest = subcommand.split(maxsplit=1)
        await _extension_new(ctx, rest[1] if len(rest) > 1 else "")
        return

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

    THE CATEGORIES ARE DISJOINT (issue #121). ``System prompt`` and ``Memory
    files`` are two slices of ONE string, split by
    :func:`~aelix_coding_agent.tui.project_context.split_project_context`, not
    two independently gathered sources. They used to be the latter, and the
    section then double-counted the project context under every session that had
    one and invented it under ``-nc``; see the comment at the memory gather for
    the measurements. Anything added here that also lands in the system prompt
    has to be subtracted from it the same way.
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
    # ACTIVE only: a gated-off tool is never sent, so its schema costs no context
    # tokens and counting it would overstate the composition.
    tool_schemas: list[object] = []
    with contextlib.suppress(Exception):
        tool_schemas = active_tool_views(harness)

    # Messages — the public live transcript property.
    messages: list[object] = []
    with contextlib.suppress(Exception):
        messages = list(getattr(harness, "messages", []) or [])

    # Memory — the project context that is REALLY in the prompt read above, and
    # SPLIT OUT of it so the two rows are disjoint.
    #
    # This used to call ``discover_context_files(ctx.cwd)`` directly, i.e. it
    # asked the FILESYSTEM a question only the assembled prompt can answer, and
    # was wrong in both directions at once. Measured on the pre-change build,
    # one 7175-char AGENTS.md (1794 estimated tokens), window 200000:
    #
    #   without -nc : System prompt 2.6K + Memory files 1.8K  <- 1794 counted TWICE
    #   with    -nc : System prompt 837  + Memory files 1.8K  <- 1794 PHANTOM
    #
    # The double count was the same text twice over: ``cli/entry.py:1229-1231``
    # appends the chunk to ``append_system_prompt`` and ``harness/core.py:596-602``
    # joins it INTO the very string ``system_prompt`` already holds. The phantom
    # was that discovery never sees ``--no-context-files`` — that gate sits one
    # level up, at ``cli/entry.py:1228``.
    #
    # :func:`split_project_context` answers from the assembled prompt instead,
    # and by CONTAINMENT rather than by recognising a header (its module
    # docstring says why: the chunk's shape changed inside this same issue).
    # Guarded like every other seam here — a failure omits the row and leaves the
    # full prompt charged to ``System prompt``, which is an understatement of one
    # row rather than either defect above.
    memory_text: str | None = None
    with contextlib.suppress(Exception):
        from aelix_coding_agent.tui.project_context import (  # noqa: PLC0415
            split_project_context,
        )

        system_prompt, memory = split_project_context(system_prompt, ctx.cwd)
        memory_text = memory or None

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
            table.add_row("cost (USD)", format_session_cost(stats, prefix=""))
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
    BuiltinCommand("trust", "Decide project trust for this folder", _trust_handler),
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
    BuiltinCommand(
        "agents",
        "List, show, switch, or delegate to agent profiles",
        _agents_handler,
    ),
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
    BuiltinCommand("quit", "Exit Aelix (alias: /exit)", None, aliases=("exit",)),
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

    Parses the leading slash word (case-sensitive) and looks it up by exact name
    OR by one of the command's :attr:`~BuiltinCommand.aliases` (``/exit`` →
    ``quit``). Returns ``None`` for a non-slash line, an empty body (bare ``/``),
    or no match.
    """

    word = slash_word(text)
    if not word:
        return None
    for command in commands:
        if command.name == word or word in command.aliases:
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
