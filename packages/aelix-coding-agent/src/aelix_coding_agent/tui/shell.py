"""Sprint 6h₁₀a (ADR-0104) / 6h₁₀b (ADR-0105) — ``run_tui`` interactive shell.

Sprint 6h₁₀b rework: ``run_tui`` drives a single long-running
:class:`~aelix_coding_agent.tui.chrome.AelixChrome` ``Application`` (persistent
status / footer / spinner / input) instead of per-turn ``PromptSession.
prompt_async``. It binds a concrete :class:`~aelix_coding_agent.tui.context.
AelixTUIContext` via ``harness.runtime.bind_ui`` so loaded extensions can drive
the UI, subscribes an :class:`~aelix_coding_agent.tui.render.EventRenderer`, and
runs an **output pump** that flushes committed Rich renderables above the chrome
via ``chrome.print_above`` (``in_terminal``) in order. The in-progress streamed
window rides in the chrome's stream widget.

Lifecycle parity with ``run_print_mode``: signal handlers → ``set_rebind_session``
→ ``bootstrap`` → drive turns → dispose in ``finally`` (also unbinds the UI back
to headless). A failed turn does not kill the REPL. Input parsing reuses
``cli/repl.py`` precedent (``handle_user_bash`` + ``parse_input_line``).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from rich.text import Text

from aelix_coding_agent.cli.repl import handle_user_bash
from aelix_coding_agent.extensions import HEADLESS_UI_CONTEXT
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.commands import (
    BUILTIN_COMMANDS,
    BuiltinCommand,
    CommandContext,
    match_command,
    slash_word,
)
from aelix_coding_agent.tui.completion import DescriptorCommandCompleter
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.descriptors import (
    DescriptorRegistry,
    DescriptorRenderer,
    ListModulesProbe,
)
from aelix_coding_agent.tui.footer_data import AelixFooterData
from aelix_coding_agent.tui.input import parse_input_line
from aelix_coding_agent.tui.render import EventRenderer

if TYPE_CHECKING:
    from aelix_agent_core.contracts.descriptor import DescriptorEnvelope
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.runtime.agent_session_runtime import AgentSessionRuntime

_RENDER_WIDTH = 80


def _format_context_label(usage: object) -> str | None:
    """Format a harness ``ContextUsage`` into a footer segment.

    ``"◔ 42% · 84K/200K"`` when both percent and token counts are known;
    degrades to whichever part is available; ``None`` when usage is missing
    (e.g. model registry not wired — Pi returns undefined there too).
    """
    if usage is None:
        return None
    from aelix_coding_agent.cli.list_models import format_token_count

    percent = getattr(usage, "percent", None)
    tokens = getattr(usage, "tokens", None)
    window = getattr(usage, "context_window", None)
    parts: list[str] = []
    if isinstance(percent, int | float):
        parts.append(f"{percent:.0f}%")
    if isinstance(tokens, int) and isinstance(window, int) and window > 0:
        parts.append(f"{format_token_count(tokens)}/{format_token_count(window)}")
    return f"◔ {' · '.join(parts)}" if parts else None


async def run_tui(
    runtime_host: AgentSessionRuntime,
    *,
    cwd: str,
    chrome: AelixChrome | None = None,
    install_signal_handlers: bool = True,
) -> int:
    """Run the interactive TUI (persistent chrome) until ``/quit`` or EOF.

    :param chrome: injectable for tests (headless pipe input + DummyOutput).
    :param install_signal_handlers: pass ``False`` when embedding (tests / a host
        that owns process signals) — mirrors ``run_rpc_mode``.
    """

    if chrome is not None:
        out_chrome = chrome
    else:
        # Persist input history across sessions (↑/↓ + Ctrl+R) — the chrome
        # already supports it; run_tui just never passed a path before.
        from pathlib import Path as _Path

        from aelix_coding_agent.cli.config import get_agent_dir

        out_chrome = AelixChrome(
            history_path=str(_Path(get_agent_dir()) / "tui_input_history")
        )
    footer = AelixFooterData(cwd=cwd)

    def _model_id() -> str | None:
        model = getattr(runtime_host.harness, "current_model", None)
        return getattr(model, "id", None) if model is not None else None

    def _steering_mode() -> str | None:
        # Live steering mode from the harness ("one-at-a-time"/"all") so the
        # footer ⏵⏵ segment reflects reality, not a hardcoded placeholder.
        return getattr(runtime_host.harness, "steering_mode", None)

    context = AelixTUIContext(
        out_chrome,
        footer,
        model_provider=_model_id,
        mode_provider=_steering_mode,
        cwd=cwd,
        mode="default",
    )

    # Output pump seam: the synchronous renderer queues TAGGED commands; the
    # pump applies them above the chrome in order. Routing the live-tail update
    # through the same queue as committed lines keeps them ordered — otherwise a
    # synchronous tail-clear would race the async (in_terminal) commit flush and
    # the just-finalized text could vanish before it reaches scrollback.
    output_queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    def _commit(renderable: object) -> None:
        output_queue.put_nowait(("commit", renderable))

    def _set_tail(ansi: str) -> None:
        output_queue.put_nowait(("tail", ansi))

    renderer = EventRenderer(commit=_commit, set_tail=_set_tail, width=_RENDER_WIDTH)

    # Sprint 6h₁₂a (ADR-0110) — first-party command core. The registry is static
    # for the session; the context carries the live chrome/harness/commit/cwd so
    # handlers (e.g. /help) can act on the running TUI.
    commands = list(BUILTIN_COMMANDS)

    def _set_mode(mode: str) -> None:
        # Reflect a /mode switch in the live footer ⏵⏵ segment (Sprint 6h₁₂d).
        context._mode = mode
        context._refresh_footer()

    command_ctx = CommandContext(
        chrome=out_chrome,
        harness=runtime_host.harness,
        commit=_commit,
        cwd=cwd,
        commands=commands,
        set_mode=_set_mode,
        refresh_footer=context._refresh_footer,
    )

    loop = asyncio.get_running_loop()
    signals_installed: list[int] = []
    if install_signal_handlers and sys.platform != "win32":
        def _handle_signal() -> None:
            # Request a clean shutdown: EOF the input loop + stop the chrome so
            # run_tui's finally runs full teardown (unbind UI, unsubscribe,
            # dispose, restore terminal). Avoids sys.exit from a detached task,
            # which bypassed teardown and left the terminal in raw mode.
            out_chrome.request_eof()
            out_chrome.exit()

        for sig_name in ("SIGTERM", "SIGHUP"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, _handle_signal)
                signals_installed.append(sig)

    unsubscribe_holder: dict[str, Callable[[], None] | None] = {"u": None}

    context_usage_tasks: set[asyncio.Task[None]] = set()

    async def _refresh_context_usage() -> None:
        # Pull the context-window meter after each turn (async; walks messages,
        # so NOT per-frame). Degrades to no segment when usage is unavailable.
        get_stats = getattr(runtime_host.harness, "get_session_stats", None)
        if get_stats is None:
            return
        try:
            stats = await get_stats()
        except Exception:  # noqa: BLE001 — a stats hiccup must not kill the TUI
            return
        context.set_context_label(
            _format_context_label(getattr(stats, "context_usage", None))
        )

    def _on_agent_event(event: object) -> None:
        renderer.on_agent_event(event)  # type: ignore[arg-type]
        if getattr(event, "type", None) == "turn_end":
            # Keep a strong reference so the task isn't GC'd before it runs.
            task = loop.create_task(_refresh_context_usage())
            context_usage_tasks.add(task)
            task.add_done_callback(context_usage_tasks.discard)

    async def _rebind(new_harness: AgentHarness) -> None:
        prior = unsubscribe_holder["u"]
        if prior is not None:
            with contextlib.suppress(Exception):
                prior()
        unsubscribe_holder["u"] = new_harness.subscribe(_on_agent_event)

    runtime_host.set_rebind_session(_rebind)

    def _on_interrupt() -> None:
        asyncio.ensure_future(_safe_abort(runtime_host.harness))

    out_chrome.on_interrupt = _on_interrupt

    descriptor_unsub: Callable[[], None] | None = None
    descriptor_renderer: DescriptorRenderer | None = None
    chrome_task: asyncio.Task[None] | None = None
    pump_task: asyncio.Task[None] | None = None
    try:
        await runtime_host.harness.bootstrap()
        # Bind the real UI BEFORE the first session_start activation so
        # extensions never see the headless stub (ADR-0105 §1.3).
        runtime_host.harness.runtime.bind_ui(context)
        # Repaint the footer now the harness is bootstrapped so the live model
        # id (read via model_provider) shows from the first frame (Sprint 6h₁₂b).
        context._refresh_footer()
        await _rebind(runtime_host.harness)
        # Tier-2 descriptor probe (ADR-0095 / Sprint 6h₁₀c §C): build the keyed
        # registry + per-kind renderer, subscribe to the ui:list-modules channel,
        # then emit one synchronous probe so loaded extensions append descriptors.
        descriptor_unsub, descriptor_renderer = _wire_descriptors(
            runtime_host, out_chrome, footer, context, loop, renderer, commands
        )
        # No descriptor wiring (headless fakes without an event_bus) → the palette
        # still offers built-ins. Install the union completer directly.
        if descriptor_renderer is None:
            out_chrome.set_command_completer(
                DescriptorCommandCompleter(lambda: {}, builtins=commands)
            )
        chrome_task = asyncio.create_task(out_chrome.run())
        pump_task = asyncio.create_task(_output_pump(output_queue, out_chrome))
        _commit(_build_banner(runtime_host.harness, cwd))
        await _input_loop(
            runtime_host,
            out_chrome,
            output_queue,
            renderer,
            descriptor_renderer,
            command_ctx,
            cwd=cwd,
        )
    finally:
        with contextlib.suppress(Exception):
            runtime_host.harness.runtime.bind_ui(HEADLESS_UI_CONTEXT)
        if descriptor_unsub is not None:
            with contextlib.suppress(Exception):
                descriptor_unsub()
        unsub = unsubscribe_holder["u"]
        if unsub is not None:
            with contextlib.suppress(Exception):
                unsub()
        out_chrome.exit()
        for task in (pump_task, chrome_task):
            if task is not None:
                task.cancel()
                # CancelledError is a BaseException — suppress(Exception) misses
                # it; awaiting a just-cancelled task re-raises it.
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if sys.platform != "win32":
            for sig in signals_installed:
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.remove_signal_handler(sig)
        with contextlib.suppress(Exception):
            await runtime_host.dispose()

    return 0


def _wire_descriptors(
    runtime_host: AgentSessionRuntime,
    chrome: AelixChrome,
    footer: AelixFooterData,
    context: AelixTUIContext,
    loop: asyncio.AbstractEventLoop,
    event_renderer: EventRenderer,
    builtins: list[BuiltinCommand],
) -> tuple[Callable[[], None] | None, DescriptorRenderer | None]:
    """Build the descriptor registry + renderer, subscribe + emit one probe.

    Returns ``(unsubscribe, descriptor_renderer)``. The unsubscribe callable (or
    ``None`` when the runtime exposes no ``event_bus`` — e.g. headless test fakes)
    leaves all other run_tui behavior intact. ``refresh_footer`` is wired to the
    context's single footer composer so footer-segment descriptors don't clobber
    the ``⎇ branch`` line. The returned :class:`DescriptorRenderer` lets the input
    loop route ``/command`` lines that match a stored management-modal (§C).
    """

    event_bus = getattr(getattr(runtime_host.harness, "runtime", None), "event_bus", None)
    if event_bus is None:
        return None, None

    registry = DescriptorRegistry()
    renderer = DescriptorRenderer(
        chrome,
        footer,
        registry,
        loop=loop,
        refresh_footer=context._refresh_footer,
        event_bus=event_bus,
        confirm=lambda message: context.confirm("Confirm", message),
    )
    registry.on_apply = renderer.render
    registry.on_remove = renderer.clear

    # Surface built-ins ∪ the (live) descriptor command-routes through the input
    # completer. The completer reads ``renderer.command_routes`` by reference on
    # every keystroke (descriptors applied/removed after this point change
    # completions live); built-ins are static and win on a name clash (§B).
    chrome.set_command_completer(
        DescriptorCommandCompleter(lambda: renderer.command_routes, builtins=builtins)
    )

    # §B — late-bind the live tool-renderer-desc lookup onto the EventRenderer so
    # tool_execution_end can intercept matching tools by reference (descriptors
    # applied/removed later change interception live, like command-routes).
    event_renderer.get_tool_renderer_desc = lambda tool_name: _lookup_tool_renderer_desc(
        registry, tool_name
    )
    event_renderer.descriptor_renderer = renderer

    unsubscribe = event_bus.on("ui:list-modules", registry.collect)
    probe = ListModulesProbe()
    event_bus.emit("ui:list-modules", probe)
    return unsubscribe, renderer


def _lookup_tool_renderer_desc(
    registry: DescriptorRegistry, tool_name: str
) -> DescriptorEnvelope | None:
    """Return the stored tool-renderer-desc envelope matching ``tool_name`` (§B)."""
    for env in registry.by_kind("tool-renderer-desc"):
        if getattr(env.payload, "tool_name", None) == tool_name:
            return env
    return None


def _match_management_modal(
    descriptor_renderer: DescriptorRenderer, text: str
) -> DescriptorEnvelope | None:
    """Match a submitted prompt line to a stored management-modal command (§C).

    A ``/<command>`` line whose command equals a stored management-modal's
    ``command`` discriminator returns that envelope (so the shell opens it
    instead of prompting the model). Non-slash lines never match.
    """
    if not text.startswith("/"):
        return None
    # Guard on the split result: "/ " → [] (whitespace-only body), not an IndexError.
    parts = text[1:].split(maxsplit=1)
    command = parts[0] if parts else ""
    if not command:
        return None
    for env in descriptor_renderer.registry.by_kind("management-modal"):
        if getattr(env.payload, "command", None) == command:
            return env
    return None


def _build_banner(harness: AgentHarness, cwd: str) -> object:
    """Build the startup banner (Aelix + model id + cwd + /help hint).

    The model id reads ``harness.current_model.id``; degrades gracefully to
    ``unknown`` when the harness exposes no model (e.g. headless test fakes).
    """
    from rich.box import ROUNDED
    from rich.panel import Panel

    model = getattr(harness, "current_model", None)
    model_id = getattr(model, "id", None) or "unknown"
    body = Text()
    body.append("Aelix\n", style="bold cyan")
    body.append(f"model: {model_id}\n")
    body.append(f"cwd:   {cwd}\n")
    body.append("Type /help for commands.", style="dim")
    return Panel(body, box=ROUNDED, border_style="cyan", expand=False)


async def _output_pump(queue: asyncio.Queue[tuple[str, object]], chrome: AelixChrome) -> None:
    """Apply tagged output commands in order: commit → scrollback, tail → widget."""

    while True:
        kind, payload = await queue.get()
        if kind == "commit":
            with contextlib.suppress(Exception):
                await chrome.print_above(payload)
        elif kind == "tail":
            ansi = payload if isinstance(payload, str) else ""
            chrome.set_widget("__stream__", ansi.split("\n") if ansi else None, above=True)


async def _input_loop(
    runtime_host: AgentSessionRuntime,
    chrome: AelixChrome,
    output_queue: asyncio.Queue[tuple[str, object]],
    renderer: EventRenderer,
    descriptor_renderer: DescriptorRenderer | None,
    command_ctx: CommandContext,
    *,
    cwd: str,
) -> None:
    """Read → classify → drive the harness, one turn at a time."""

    while True:
        try:
            line = await chrome.get_input()
        except EOFError:
            return  # Ctrl+D exits

        parsed = parse_input_line(line)
        harness = runtime_host.harness

        if parsed.kind == "quit":
            return
        if parsed.kind == "empty":
            continue
        if parsed.kind == "reload":
            await harness.reload_resources()
            continue
        # Sprint 6h₁₂a (ADR-0110) — a `prompt`-kind `/`-line resolves through the
        # command core BEFORE going to the model: (1) built-in registry handler,
        # (2) descriptor management-modal (§C), (3) else an "unknown command" hint
        # (a bare /x is NOT sent to the model). quit/exit/reload are already
        # handled above via parse_input_line (which stays PURE for cli/repl.py);
        # their metadata-only registry entries never reach this branch.
        if parsed.kind == "prompt" and parsed.text.startswith("/"):
            command = match_command(parsed.text, command_ctx.commands)
            if command is not None and command.handler is not None:
                # args = the text after the command word (Sprint 6h₁₂d). The
                # word is isolated by slash_word so this can never disagree with
                # the dispatch on what the typed command word was.
                args = parsed.text[len("/" + slash_word(parsed.text)):].strip()
                await command.handler(command_ctx, args)
                continue
            if descriptor_renderer is not None:
                modal = _match_management_modal(descriptor_renderer, parsed.text)
                if modal is not None:
                    descriptor_renderer.open_modal(modal)
                    continue
            label = "/" + slash_word(parsed.text)
            output_queue.put_nowait(
                ("commit", Text(f"Unknown command: {label} — type /help", style="yellow"))
            )
            continue
        if parsed.kind in ("bash", "bash_transient"):
            if parsed.text:
                output = await handle_user_bash(
                    harness,
                    parsed.text,
                    exclude_from_context=(parsed.kind == "bash_transient"),
                    cwd=cwd,
                )
                if output.strip():
                    output_queue.put_nowait(("commit", Text(output.rstrip("\n"))))
            continue

        # prompt — drive a full turn while the chrome stays live (spinner on).
        # A failed turn must not kill the REPL (parity with run_print_mode).
        # Echo the user's own line into the transcript (Sprint 6h₁₂b) so the
        # assistant reply has its visible question above it — prompt path only
        # (bash / commands / empty already returned/continued before here).
        output_queue.put_nowait(("commit", Text(f"» {parsed.text}", style="bold")))
        chrome.set_running(True)
        try:
            await harness.prompt(parsed.text, source="interactive")
        except Exception as exc:  # noqa: BLE001 — surface + survive a failed turn
            renderer.finalize()  # commit partial + clear the live stream window
            output_queue.put_nowait(("commit", Text(f"✖ {exc}", style="bold red")))
        finally:
            chrome.set_running(False)


async def _safe_abort(harness: AgentHarness) -> None:
    with contextlib.suppress(Exception):
        await harness.abort()


__all__ = ["run_tui"]
