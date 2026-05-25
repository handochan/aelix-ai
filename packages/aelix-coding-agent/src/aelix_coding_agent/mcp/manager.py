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
from typing import Any

import mcp.types as mcp_types
from aelix_agent_core.contracts import McpServerContrib
from aelix_agent_core.types import AgentTool

from aelix_coding_agent.mcp.adapter import mcp_tools_to_agent_tools
from aelix_coding_agent.mcp.client import McpConnectionError, McpServerConnection

# Reconnect backoff (HTTP/SSE only): attempt N sleeps min(1.0 * 2**N, 32.0)s.
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 32.0


class McpClientManager:
    """Multi-server MCP manager (connect-all / collect-tools / disconnect-all).

    One bad server never aborts the others (Harlequin pattern):
    :meth:`connect_all` returns the per-server errors instead of raising.
    """

    def __init__(self, contribs: list[McpServerContrib]) -> None:
        self._connections: dict[str, McpServerConnection] = {
            c.name: McpServerConnection(c) for c in contribs
        }

    @property
    def connections(self) -> dict[str, McpServerConnection]:
        return self._connections

    async def connect_all(self) -> list[McpConnectionError]:
        """Connect every declared server.

        Returns the per-server :class:`McpConnectionError` list (empty on full
        success). A single failing server never aborts the others.
        """
        errors: list[McpConnectionError] = []
        for conn in self._connections.values():
            try:
                await conn.connect()
            except McpConnectionError as exc:
                errors.append(exc)
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
            return await conn.call_tool(tool, args)

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
        """Tear down every connection (``AsyncExitStack`` unwind per server)."""
        for conn in self._connections.values():
            await conn.disconnect()
