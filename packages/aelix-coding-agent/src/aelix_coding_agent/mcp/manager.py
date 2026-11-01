"""MCP multi-server manager (Sprint 6h₉d, ADR-0101).

Owns a dict of :class:`McpServerConnection` keyed by server name. Connects all
declared servers, aggregates their tools as ``AgentTool`` instances
(namespaced ``<server>__<tool>`` to avoid cross-server collisions), and tears
down cleanly. HTTP/SSE reconnect uses Claude-Code-style exponential backoff
(5 attempts, 1s start, doubling, 32s cap); stdio crash is reactive (no
auto-reconnect — the local subprocess is gone and the caller must reconnect).

Aelix-additive Tier 4a — Pi has no MCP client in core. Pi pin held at
734e08e, no Pi feature imported.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import mcp.types as mcp_types
from aelix_agent_core.contracts import McpServerContrib
from aelix_agent_core.types import AgentTool

from aelix_coding_agent.mcp.adapter import mcp_tools_to_agent_tools
from aelix_coding_agent.mcp.client import McpConnectionError, McpServerConnection

if TYPE_CHECKING:
    from aelix_coding_agent.core.mcp_servers import DisabledMcpServer

# Reconnect backoff (HTTP/SSE only): attempt N sleeps min(1.0 * 2**N, 32.0)s.
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 32.0


class McpClientManager:
    """Multi-server MCP manager (connect-all / collect-tools / disconnect-all).

    One bad server never aborts the others (Harlequin pattern):
    :meth:`connect_all` returns the per-server errors instead of raising.

    #512 (ADR-0282): it is also the session's server INVENTORY, which is what
    ``/mcp`` and ``/extension`` read through ``core.mcp_servers.runtime_statuses``.
    ``disabled`` are the ``"enabled": false`` entries: held as data, with no
    :class:`McpServerConnection`, so nothing here can start one, and a
    contribution that shares a disabled name is not given a connection either
    (a disabled ``mcp.json`` entry keeps a same-name manifest server off, as an
    enabled one already won the name). ``failed`` are the names whose
    :meth:`connect_all` attempt raised.
    """

    def __init__(
        self,
        contribs: list[McpServerContrib],
        *,
        disabled: Iterable[DisabledMcpServer] = (),
    ) -> None:
        self._disabled: tuple[DisabledMcpServer, ...] = tuple(disabled)
        off = {entry.contrib.name for entry in self._disabled}
        self._connections: dict[str, McpServerConnection] = {
            c.name: McpServerConnection(c) for c in contribs if c.name not in off
        }
        self._failed: set[str] = set()

    @property
    def connections(self) -> dict[str, McpServerConnection]:
        return self._connections

    @property
    def disabled(self) -> tuple[DisabledMcpServer, ...]:
        return self._disabled

    @property
    def failed(self) -> frozenset[str]:
        return frozenset(self._failed)

    async def connect_all(self) -> list[McpConnectionError]:
        """Connect every declared server.

        Returns the per-server :class:`McpConnectionError` list (empty on full
        success). A single failing server never aborts the others.

        Catching only ``McpConnectionError`` here is deliberate, and it relies
        on ``McpServerConnection.connect`` reporting *every* server-side failure
        as one — including a server that dies mid-handshake, which surfaces
        underneath as a ``CancelledError`` (#125). A cancellation that reaches
        this loop is therefore our caller's, and must keep travelling.
        """
        errors: list[McpConnectionError] = []
        for name, conn in self._connections.items():
            try:
                await conn.connect()
            except McpConnectionError as exc:
                self._failed.add(name)
                errors.append(exc)
            else:
                self._failed.discard(name)
        return errors

    async def collect_agent_tools(self) -> list[AgentTool]:
        """``list_tools()`` across all connected servers as ``AgentTool``.

        Tools are namespaced ``<server-name>__<tool-name>`` to avoid
        cross-server collisions. Disconnected servers are skipped.
        """
        tools: list[AgentTool] = []
        for name, conn in self._connections.items():
            if not conn.connected:
                continue
            mcp_tools = await conn.list_tools()
            tools.extend(
                mcp_tools_to_agent_tools(conn, mcp_tools, name_prefix=name)
            )
        return tools

    async def call_tool_with_retry(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        *,
        max_attempts: int = 3,
    ) -> mcp_types.CallToolResult:
        """Call a tool on ``server`` with reconnect-on-failure.

        For HTTP/SSE servers: exponential backoff (``min(1*2**N, 32)`` seconds)
        with a fresh reconnect between attempts. For stdio: a single attempt
        (the local subprocess is gone on failure — reactive, no reconnect).
        """
        conn = self._connections.get(server)
        if conn is None:
            raise McpConnectionError(f"unknown MCP server {server!r}")

        if conn.transport == "stdio":
            # stdio is a local process — reactive, single attempt.
            # Sprint 6h₉d fold-in §F (W4 MINOR-3): wrap transport errors in
            # McpConnectionError for error-contract consistency with the
            # HTTP/SSE path. A crashed-but-not-disconnected subprocess leaves
            # _session non-None, so the raw anyio/broken-pipe exception would
            # otherwise escape unwrapped. McpConnectionError (already raised by
            # call_tool when _session is None) passes through unchanged.
            try:
                return await conn.call_tool(tool, args)
            except McpConnectionError:
                raise
            except Exception as exc:  # noqa: BLE001 — wrap for error contract
                raise McpConnectionError(
                    f"MCP server {server!r} call_tool {tool!r} failed (stdio, "
                    f"reactive — subprocess likely gone; caller must reconnect "
                    f"a fresh McpServerConnection): {exc}"
                ) from exc

        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                if not conn.connected:
                    await conn.connect()
                return await conn.call_tool(tool, args)
            except Exception as exc:  # noqa: BLE001 — retry then re-wrap
                last_exc = exc
                # Drop the dead session before the next reconnect attempt.
                await conn.disconnect()
                if attempt + 1 >= max_attempts:
                    break
                delay = min(
                    _BACKOFF_BASE_SECONDS * (2**attempt), _BACKOFF_CAP_SECONDS
                )
                await asyncio.sleep(delay)
        raise McpConnectionError(
            f"MCP server {server!r} call_tool {tool!r} failed after "
            f"{max_attempts} attempts: {last_exc}"
        ) from last_exc

    async def disconnect_all(self) -> None:
        """Tear down every connection (``AsyncExitStack`` unwind per server).

        Teardown runs in reverse (LIFO) connect order: each transport opens an
        anyio task-group cancel scope in the manager's task, and anyio requires
        sibling cancel scopes opened in one task to unwind strictly LIFO
        ("cannot exit cancel scope in a different task / out of order").
        """
        for conn in reversed(list(self._connections.values())):
            await conn.disconnect()
