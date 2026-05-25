"""Sprint 6h₁₀a (ADR-0104) — ``run_tui`` interactive shell entry.

The TUI is a third frontend sibling of ``run_print_mode`` / ``run_rpc_mode``:
it drives the same :class:`AgentHarness` and subscribes to the same
:data:`AgentEvent` stream, but renders to a Rich console (via
:class:`EventRenderer`) and reads input from a prompt-toolkit
:class:`PromptSession` instead of serializing to JSONL/stdout.

Structure mirrors ``modes/print_mode.py::run_print_mode``: signal handlers →
``set_rebind_session`` closure → ``subscribe`` → drive turns → dispose in
``finally``. Input parsing reuses ``cli/repl.py`` precedent (``handle_user_bash``
+ ``parse_input_line``).

aider "Option A" sequential ownership (ADR-0104 §arch): the prompt-toolkit
session owns the terminal only while awaiting input; the Rich renderer owns it
only while a turn runs. ``await harness.prompt(...)`` completes fully before the
next ``prompt_async``, so the two never contend for the cursor — no
``patch_stdout`` needed in the thin shell.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

from aelix_coding_agent.cli.repl import handle_user_bash
from aelix_coding_agent.tui.input import (
    PROMPT_STR,
    build_prompt_session,
    parse_input_line,
)
from aelix_coding_agent.tui.render import EventRenderer

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.runtime.agent_session_runtime import AgentSessionRuntime
    from prompt_toolkit import PromptSession


async def run_tui(
    runtime_host: AgentSessionRuntime,
    *,
    cwd: str,
    console: Console | None = None,
    session: PromptSession[str] | None = None,
    install_signal_handlers: bool = True,
) -> int:
    """Run the interactive TUI loop until ``/quit`` or EOF (Ctrl+D).

    :param runtime_host: the session runtime (owns the live harness + swaps).
    :param cwd: working directory for ``!`` bash passthrough.
    :param console: Rich output console; defaults to stdout (injectable for tests).
    :param session: prompt-toolkit session; defaults built (injectable for tests).
    :param install_signal_handlers: install SIGTERM/SIGHUP cleanup handlers on
        the loop. Mirrors ``run_rpc_mode``; pass ``False`` when embedding (tests,
        a host that owns process signals) to avoid leaking process-global
        handlers across event loops.
    :returns: process exit code (``0`` on clean exit).
    """

    out_console = console if console is not None else Console()
    prompt_session = session if session is not None else build_prompt_session()
    renderer = EventRenderer(out_console)

    # === Signal handlers (non-Windows; SIGINT stays KeyboardInterrupt) =======
    # Known limitation (carry-forward to Sprint 6h₁₀b): on SIGTERM/SIGHUP the
    # handler disposes + sys.exit()s from a loop callback while prompt-toolkit
    # may still own the terminal in raw mode, so terminal restore is not
    # coordinated. Acceptable for the thin shell (dispose is idempotent); the
    # cancel-the-prompt approach lands with the live chrome in 6h₁₀b.
    loop = asyncio.get_running_loop()
    signals_installed: list[int] = []
    if install_signal_handlers and sys.platform != "win32":
        def _handle_signal(sig: int) -> None:
            asyncio.ensure_future(_signal_cleanup_and_exit(runtime_host, sig))

        for sig_name in ("SIGTERM", "SIGHUP"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, _handle_signal, sig)
                signals_installed.append(sig)

    # === Rebind closure: keep the renderer subscribed across session swaps ===
    unsubscribe_holder: dict[str, Callable[[], None] | None] = {"u": None}

    async def _rebind(new_harness: AgentHarness) -> None:
        prior = unsubscribe_holder["u"]
        if prior is not None:
            with contextlib.suppress(Exception):
                prior()
        unsubscribe_holder["u"] = new_harness.subscribe(renderer.on_agent_event)

    runtime_host.set_rebind_session(_rebind)

    try:
        await runtime_host.harness.bootstrap()
        await _rebind(runtime_host.harness)
        await _input_loop(
            runtime_host, prompt_session, out_console, renderer, cwd=cwd
        )
    finally:
        unsub = unsubscribe_holder["u"]
        if unsub is not None:
            with contextlib.suppress(Exception):
                unsub()
        if sys.platform != "win32":
            for sig in signals_installed:
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.remove_signal_handler(sig)
        with contextlib.suppress(Exception):
            await runtime_host.dispose()

    return 0


async def _input_loop(
    runtime_host: AgentSessionRuntime,
    prompt_session: PromptSession[str],
    console: Console,
    renderer: EventRenderer,
    *,
    cwd: str,
) -> None:
    """Read → classify → drive the harness, one turn at a time."""

    while True:
        try:
            line = await prompt_session.prompt_async(PROMPT_STR)
        except KeyboardInterrupt:
            continue  # Ctrl+C clears the current line (conventional REPL UX)
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
                    console.print(Text(output.rstrip("\n")))
            continue

        # prompt — drive a full turn. A failed turn must NOT kill the REPL
        # (parity with run_print_mode's turn-loop guard): finalize any open
        # stream, render the error, and return to the prompt. Ctrl+C is a
        # best-effort abort after the interrupt unwinds the turn.
        try:
            await harness.prompt(parsed.text, source="interactive")
        except KeyboardInterrupt:
            with contextlib.suppress(Exception):
                await harness.abort()
        except Exception as exc:  # noqa: BLE001 — surface + survive a failed turn
            renderer.finalize()
            console.print(Text(f"✖ {exc}", style="bold red"))


async def _signal_cleanup_and_exit(runtime_host: AgentSessionRuntime, sig: int) -> None:
    """SIGTERM/SIGHUP handler — dispose then exit ``128 + sig`` (POSIX)."""

    with contextlib.suppress(Exception):
        await runtime_host.dispose()
    sys.exit(128 + sig)


__all__ = ["run_tui"]
