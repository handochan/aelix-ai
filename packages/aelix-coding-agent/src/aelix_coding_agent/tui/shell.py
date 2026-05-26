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
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.footer_data import AelixFooterData
from aelix_coding_agent.tui.input import parse_input_line
from aelix_coding_agent.tui.render import EventRenderer

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.runtime.agent_session_runtime import AgentSessionRuntime

_RENDER_WIDTH = 80


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

    out_chrome = chrome if chrome is not None else AelixChrome()
    footer = AelixFooterData(cwd=cwd)
    context = AelixTUIContext(out_chrome, footer)

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

    async def _rebind(new_harness: AgentHarness) -> None:
        prior = unsubscribe_holder["u"]
        if prior is not None:
            with contextlib.suppress(Exception):
                prior()
        unsubscribe_holder["u"] = new_harness.subscribe(renderer.on_agent_event)

    runtime_host.set_rebind_session(_rebind)

    def _on_interrupt() -> None:
        asyncio.ensure_future(_safe_abort(runtime_host.harness))

    out_chrome.on_interrupt = _on_interrupt

    chrome_task: asyncio.Task[None] | None = None
    pump_task: asyncio.Task[None] | None = None
    try:
        await runtime_host.harness.bootstrap()
        # Bind the real UI BEFORE the first session_start activation so
        # extensions never see the headless stub (ADR-0105 §1.3).
        runtime_host.harness.runtime.bind_ui(context)
        await _rebind(runtime_host.harness)
        chrome_task = asyncio.create_task(out_chrome.run())
        pump_task = asyncio.create_task(_output_pump(output_queue, out_chrome))
        await _input_loop(runtime_host, out_chrome, output_queue, renderer, cwd=cwd)
    finally:
        with contextlib.suppress(Exception):
            runtime_host.harness.runtime.bind_ui(HEADLESS_UI_CONTEXT)
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
