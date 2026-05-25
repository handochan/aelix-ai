"""MCP client connection — single-server lifecycle (Sprint 6h₉d, ADR-0101).

Aelix-additive Tier 4a (ADR-0094) — Pi has no MCP client in core.
Reference: official ``mcp`` Python SDK 1.27.1 + MCP spec + Claude Code
``mcpServers`` config. Pi pin held at 734e08e (no Pi feature imported).

This module owns one MCP server connection: transport selection by
``McpServerContrib.transport`` (stdio / http / sse), the ``initialize``
handshake, ``list_tools`` / ``call_tool`` delegation, and clean
``AsyncExitStack`` teardown.

SDK-version note (1.27.1): the installed ``mcp`` exposes camelCase
attributes (``Tool.inputSchema``, ``InitializeResult.serverInfo`` /
``protocolVersion``, ``CallToolResult.isError``), NOT the snake_case names
some docs cite. The HTTP transport helper is ``streamablehttp_client`` and
yields a 3-tuple ``(read, write, get_session_id)``; stdio/sse yield a
2-tuple. ``ClientSession.call_tool`` takes ``read_timeout_seconds`` as a
``datetime.timedelta``. Code below targets these verified signatures.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

import mcp.types as mcp_types
from aelix_agent_core.contracts import McpServerContrib
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client


class McpConnectionError(Exception):
    """Raised on MCP server connect / handshake / transport failure."""


class McpServerConnection:
    """One MCP server connection (stdio / http / sse).

    Lifecycle::

        conn = McpServerConnection(contrib)
        await conn.connect()           # spawn/connect + initialize handshake
        tools = await conn.list_tools()
        result = await conn.call_tool(name, args)
        await conn.disconnect()        # AsyncExitStack unwind (subprocess kill)

    Or as an async context manager::

        async with McpServerConnection(contrib) as conn:
            ...
    """

    def __init__(self, contrib: McpServerContrib) -> None:
        self._contrib = contrib
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._server_info: mcp_types.Implementation | None = None
        self._protocol_version: str | int | None = None

    @property
    def name(self) -> str:
        return self._contrib.name

    @property
    def transport(self) -> str:
        return self._contrib.transport

    @property
    def connected(self) -> bool:
        return self._session is not None

    @property
    def server_info(self) -> mcp_types.Implementation | None:
        return self._server_info

    @property
    def protocol_version(self) -> str | int | None:
        # MCP ``InitializeResult.protocolVersion`` is typed ``str | int`` by
        # the SDK (date-strings like "2025-06-18" in practice, but the SDK
        # union admits int). Sprint 6h₉d fold-in §F: W5 MINOR-2 proposed
        # narrowing to ``str | None`` but direct SDK introspection
        # (``mcp.types.InitializeResult.model_fields['protocolVersion']``)
        # confirms the annotation is ``str | int`` — the executor's original
        # ``str | int | None`` is correct and is retained. W5 MINOR-2 rejected.
        return self._protocol_version

    async def connect(self) -> None:
        """Open the transport, create the session, run ``initialize()``.

        Idempotent — a second call while connected is a no-op.

        Raises:
            McpConnectionError: on spawn/connect/handshake failure
                (config validation, transport error, or initialize error).
        """
        if self._session is not None:
            return  # idempotent — already connected
        try:
            read, write = await self._open_transport()
            session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            init = await session.initialize()
            self._session = session
            # SDK 1.27.1 uses camelCase attributes on InitializeResult.
            self._server_info = init.serverInfo
            self._protocol_version = init.protocolVersion
        except Exception as exc:  # noqa: BLE001 — wrap all into McpConnectionError
            await self._exit_stack.aclose()
            self._exit_stack = AsyncExitStack()
            self._session = None
            self._server_info = None
            self._protocol_version = None
            if isinstance(exc, McpConnectionError):
                raise
            raise McpConnectionError(
                f"MCP server {self._contrib.name!r} ({self._contrib.transport}) "
                f"connect failed: {exc}"
            ) from exc

    async def _open_transport(self) -> tuple[Any, Any]:
        """Select + open the transport per ``McpServerContrib.transport``.

        Returns the ``(read, write)`` stream pair. The streamable-HTTP helper
        yields a 3-tuple ``(read, write, get_session_id)`` in SDK 1.27.1; the
        session-id callback is unused here and discarded.
        """
        t = self._contrib.transport
        if t == "stdio":
            if not self._contrib.command:
                raise McpConnectionError(
                    f"MCP server {self._contrib.name!r}: transport=stdio "
                    "requires [contributes.mcp_servers].command"
                )
            params = StdioServerParameters(
                command=self._contrib.command,
                # Sprint 6h₉d: McpServerContrib has no args field yet;
                # see §3.4 — args is a deferred McpServerContrib v2 field.
                args=[],
                env=(
                    {**os.environ, **self._contrib.env}
                    if self._contrib.env
                    # None → SDK get_default_environment() safe inherit.
                    else None
                ),
            )
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(params)
            )
            return read, write
        if t == "http":
            if not self._contrib.url:
                raise McpConnectionError(
                    f"MCP server {self._contrib.name!r}: transport=http requires url"
                )
            read, write, _get_session_id = await self._exit_stack.enter_async_context(
                streamablehttp_client(url=self._contrib.url)
            )
            return read, write
        if t == "sse":
            if not self._contrib.url:
                raise McpConnectionError(
                    f"MCP server {self._contrib.name!r}: transport=sse requires url"
                )
            # SSE is deprecated (MCP spec) — supported for legacy servers only.
            read, write = await self._exit_stack.enter_async_context(
                sse_client(url=self._contrib.url)
            )
            return read, write
        raise McpConnectionError(
            f"MCP server {self._contrib.name!r}: unknown transport {t!r}"
        )

    async def list_tools(self) -> list[mcp_types.Tool]:
        if self._session is None:
            raise McpConnectionError(f"{self._contrib.name!r} not connected")
        result = await self._session.list_tools()
        return list(result.tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        read_timeout_seconds: float | None = None,
    ) -> mcp_types.CallToolResult:
        if self._session is None:
            raise McpConnectionError(f"{self._contrib.name!r} not connected")
        # SDK 1.27.1 takes read_timeout_seconds as a datetime.timedelta.
        timeout = (
            timedelta(seconds=read_timeout_seconds)
            if read_timeout_seconds is not None
            else None
        )
        return await self._session.call_tool(
            name=name,
            arguments=arguments,
            read_timeout_seconds=timeout,
        )

    async def disconnect(self) -> None:
        await self._exit_stack.aclose()
        self._exit_stack = AsyncExitStack()
        self._session = None
        self._server_info = None
        self._protocol_version = None

    async def __aenter__(self) -> McpServerConnection:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()
