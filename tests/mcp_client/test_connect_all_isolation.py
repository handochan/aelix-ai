"""A dying stdio server must not take the other servers with it (#125).

`McpClientManager.connect_all` documents "a single failing server never aborts
the others". A stdio server that spawns and then exits immediately used to
break that promise: the transport's anyio task group cancelled the connecting
task, and the resulting bare `CancelledError` walked past `connect`'s
`except Exception` and past `connect_all`'s `except McpConnectionError`.

The failure is timing-dependent — it needs the child to die while the caller is
inside the `initialize()` handshake — so the isolation test runs several rounds
rather than one.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from aelix_agent_core.contracts import McpServerContrib
from aelix_coding_agent.mcp import McpClientManager, McpConnectionError
from aelix_coding_agent.mcp.client import McpServerConnection

_ECHO_SERVER = Path(__file__).with_name("_echo_server.py")

# Rounds for the intermittent case. Standalone this reproduced ~5/8 before the
# fix; under load it was reliable.
_ROUNDS = 8


# Every launcher is argv for the current interpreter, never a `#!/bin/sh`
# script: CreateProcess does not read shebangs and rejected those with
# `[WinError 193] %1 is not a valid Win32 application` (#218).


@pytest.fixture(scope="module")
def good_command() -> list[str]:
    """A healthy stdio MCP server (the shared echo fixture)."""
    return [sys.executable, str(_ECHO_SERVER)]


@pytest.fixture(scope="module")
def dying_command() -> list[str]:
    """Spawns fine, then exits at once — the missing-binary shape of failure.

    Note this is a *successful* spawn. The pre-existing partial-failure test
    uses `command=None`, which is rejected during config validation and never
    reaches a transport, so it never exercised this path.
    """
    return [sys.executable, "-c", "raise SystemExit(0)"]


@pytest.fixture(scope="module")
def stalling_command() -> list[str]:
    """Spawns and stays alive but never answers, parking us in the handshake."""
    return [sys.executable, "-c", "import time; time.sleep(30)"]


def _stdio(name: str, command: list[str]) -> McpServerContrib:
    return McpServerContrib(
        name=name, transport="stdio", command=command[0], args=command[1:]
    )


@pytest.mark.asyncio
async def test_dying_server_does_not_abort_the_others(
    good_command: list[str], dying_command: list[str]
) -> None:
    """The healthy server connects even when a dying one is in the same batch.

    Ordering matters: the dying server goes *second*, so its cancellation lands
    on a task that has already connected someone. That is the ordering that
    used to lose the healthy server.
    """
    for round_index in range(_ROUNDS):
        manager = McpClientManager(
            [_stdio("good", good_command), _stdio("bad", dying_command)]
        )
        try:
            errors = await manager.connect_all()

            assert [type(e) for e in errors] == [McpConnectionError], (
                f"round {round_index}: expected exactly one McpConnectionError, "
                f"got {errors!r}"
            )
            assert manager.connections["good"].connected is True, (
                f"round {round_index}: the healthy server was taken down by its "
                f"neighbour"
            )
            assert manager.connections["bad"].connected is False

            # Still usable, not merely flagged connected: a task trapped in the
            # dead child's cancel scope raises CancelledError on the next await.
            tools = await manager.collect_agent_tools()
            assert "good__echo" in {t.name for t in tools}
        finally:
            await manager.disconnect_all()


@pytest.mark.asyncio
async def test_dying_server_reports_a_connection_error(dying_command: list[str]) -> None:
    """On its own, a dying server raises McpConnectionError — never CancelledError."""
    for _ in range(_ROUNDS):
        conn = McpServerConnection(_stdio("bad", dying_command))
        with pytest.raises(McpConnectionError, match="connect failed"):
            await conn.connect()
        assert conn.connected is False
        # The transport was unwound, so the task is not left inside a cancelled
        # scope; a plain await must not raise.
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_outer_cancellation_still_propagates(stalling_command: list[str]) -> None:
    """A real cancellation of the caller must not be reported as a dead server.

    The guard that converts a dying child's cancellation into an error must not
    also swallow a cancellation aimed at us. This lands the cancel *inside* the
    handshake, which is the only place the two are confusable.
    """
    manager = McpClientManager([_stdio("stalling", stalling_command)])
    task = asyncio.create_task(manager.connect_all())
    try:
        # Let connect() reach initialize() against a server that never answers.
        await asyncio.sleep(0.5)
        assert not task.done(), "server answered; the cancel would not land inside"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await manager.disconnect_all()
