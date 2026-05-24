"""Sprint 5b §B.2 — minimal CLI REPL with ``!``/``!!`` bash parser.

Pi parity surface: enough to exercise ``user_bash`` emit + extension command
interception + ``/reload`` dispatch into
:meth:`AgentHarness.reload_resources`. Full TUI / interactive-mode.ts (5528
LOC) is Phase 5c-tui owned (Sprint 6h₁₀b, see ADR-0100).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from aelix_agent_core.harness.hooks import (
    UserBashHookEvent,
    UserBashResult,
)

from aelix_coding_agent.tools.bash import (
    BashOperations,
    create_local_bash_operations,
)

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness


async def handle_user_bash(
    harness: AgentHarness,
    command: str,
    *,
    exclude_from_context: bool,
    cwd: str,
) -> str:
    """Emit ``user_bash``; execute via injected ops or local default.

    Pi parity (``interactive-mode.ts:5403-5466``): emit lets extensions
    intercept; ``result``-bearing reducer return short-circuits execution;
    otherwise an injected ``operations`` (or the local default) runs.
    Returns the captured stdout/stderr buffer.
    """

    event_result = await harness.hooks.emit(
        UserBashHookEvent(
            command=command,
            exclude_from_context=exclude_from_context,
            cwd=cwd,
        )
    )
    operations: BashOperations | None = None
    fully_handled = False
    output = ""
    if isinstance(event_result, UserBashResult):
        operations = event_result.operations  # type: ignore[assignment]
        if event_result.result is not None:
            fully_handled = True
            output = getattr(event_result.result, "output", "") or ""
    if not fully_handled:
        ops: BashOperations = operations or create_local_bash_operations()
        chunks: list[bytes] = []
        await ops.exec(command, cwd, on_data=chunks.append, signal=None)
        output = b"".join(chunks).decode("utf-8", errors="replace")
    # Sprint 6h₅d §E (P-384 / MINOR-3): read through
    # :attr:`AgentHarness.session` and narrow once locally.
    session = harness.session
    if not exclude_from_context and session is not None:
        with contextlib.suppress(Exception):
            await session.append_custom_entry(
                custom_type="bash_execution",
                data={"command": command, "output": output},
            )
    return output


async def run_repl(harness: AgentHarness, *, cwd: str) -> None:
    """Minimal stdin → AgentHarness REPL.

    Recognised tokens:

    - ``!<cmd>`` — emit ``user_bash`` + include output in session context
    - ``!!<cmd>`` — emit ``user_bash`` + exclude from context (transient)
    - ``/reload`` — call :meth:`AgentHarness.reload_resources`
    - ``/quit`` / ``/exit`` — exit the REPL
    - anything else — :meth:`AgentHarness.prompt` (triggers ``input`` emit)
    """

    await harness.bootstrap()
    while True:
        try:
            line = await asyncio.to_thread(input, "» ")
        except EOFError:
            return
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in ("/quit", "/exit"):
            return
        if stripped == "/reload":
            await harness.reload_resources()
            continue
        if line.startswith("!!"):
            cmd = line[2:].strip()
            if cmd:
                out = await handle_user_bash(
                    harness, cmd, exclude_from_context=True, cwd=cwd
                )
                if out:
                    print(out, end="" if out.endswith("\n") else "\n")
            continue
        if line.startswith("!"):
            cmd = line[1:].strip()
            if cmd:
                out = await handle_user_bash(
                    harness, cmd, exclude_from_context=False, cwd=cwd
                )
                if out:
                    print(out, end="" if out.endswith("\n") else "\n")
            continue
        await harness.prompt(line)


__all__ = ["handle_user_bash", "run_repl"]
