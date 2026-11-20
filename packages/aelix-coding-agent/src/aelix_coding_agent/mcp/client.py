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

import asyncio
import logging
import socket
import ssl
from collections.abc import Iterator
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
from datetime import timedelta
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

import httpx
import mcp.types as mcp_types
from aelix_agent_core.contracts import McpServerContrib
from aelix_ai.utils.terminal_text import Controls, safe_for_terminal
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import (
    StdioServerParameters,
    get_default_environment,
    stdio_client,
)
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError


class McpConnectionError(Exception):
    """Raised on MCP server connect / handshake / transport failure."""


class McpToolDiscoveryError(McpConnectionError):
    """Startup tools/list failed after a successful handshake."""


_aelix_transport = ContextVar("aelix_mcp_transport", default=False)


def _allow_sdk_transport_record(_record: logging.LogRecord) -> bool:
    return not _aelix_transport.get()


@contextmanager
def _quiet_sdk_transport() -> Iterator[None]:
    # MCP 1.27.1 logs peer responses and exception text in these transports.
    # The SDK's reader and writer tasks inherit this context when they start.
    # Other users of those loggers in the host keep their own logging behavior.
    for name in ("mcp.client.streamable_http", "mcp.client.sse"):
        logging.getLogger(name).addFilter(_allow_sdk_transport_record)
    token = _aelix_transport.set(True)
    try:
        yield
    finally:
        _aelix_transport.reset(token)


def validate_mcp_endpoint(url: str, *, allow_credentials: bool = True) -> str:
    """Validate before HTTPX can normalize spaces or pass an invalid port to DNS."""
    try:
        parsed = urlsplit(url)
        port = parsed.port  # Also rejects non-numeric and out-of-range ports.
    except ValueError:
        raise httpx.InvalidURL("invalid MCP endpoint") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or port == 0
        or (not allow_credentials and (parsed.username or parsed.password))
        or any(ord(char) <= 0x20 or char == "\x7f" for char in url)
    ):
        raise httpx.InvalidURL("invalid MCP endpoint")
    return url


def _failure_causes(error: BaseException) -> Iterator[BaseException]:
    """Walk SDK task groups and HTTPX/httpcore's typed wrappers."""
    pending = [error]
    seen: set[int] = set()
    while pending:
        cause = pending.pop()
        if id(cause) in seen:
            continue
        seen.add(id(cause))
        if isinstance(cause, BaseExceptionGroup):
            pending.extend(reversed(cause.exceptions))
            continue
        yield cause
        # httpcore can carry the original error as an argument even after
        # re-raising its wrapper with "from None".
        pending.extend(arg for arg in cause.args if isinstance(arg, BaseException))
        if cause.__cause__ is not None:
            pending.append(cause.__cause__)
        elif cause.__context__ is not None and not cause.__suppress_context__:
            pending.append(cause.__context__)


def _connect_failure_reason(transport: str, error: BaseException) -> str:
    """Format a one-line diagnostic without quoting transport exception text."""
    if isinstance(error, asyncio.CancelledError):
        if transport == "stdio":
            return "the server exited during the initialize handshake"
        return "initialize handshake cancelled"

    causes = list(_failure_causes(error))
    for cause in causes:
        if isinstance(cause, McpError):
            return safe_for_terminal(cause.error.message, controls=Controls.SPACE, max_chars=200)
        if transport == "stdio" and isinstance(cause, OSError):
            return safe_for_terminal(str(cause), controls=Controls.SPACE, max_chars=200)
        if isinstance(cause, httpx.HTTPStatusError):
            code = cause.response.status_code
            try:
                return f"HTTP {code} {HTTPStatus(code).phrase}"
            except ValueError:
                return f"HTTP {code}"
        if isinstance(cause, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
            return "invalid URL"
        if isinstance(cause, ssl.SSLCertVerificationError):
            return "TLS certificate verification failed"
        if isinstance(cause, socket.gaierror):
            return "DNS resolution failed (ConnectError)"
        if isinstance(cause, ConnectionRefusedError):
            return "connection refused (ConnectError)"
        if type(cause) is RuntimeError and str(cause).startswith(
            "Unsupported protocol version from the server: "
        ):
            # The pinned SDK has no typed protocol-version exception.
            return "unsupported MCP protocol version"
    return type(causes[0] if causes else error).__name__


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
                A server that dies mid-handshake reports here too, so that a
                caller connecting several servers can carry on with the rest.
            asyncio.CancelledError: only when the *caller* was cancelled.
        """
        if self._session is not None:
            return  # idempotent — already connected
        task = asyncio.current_task()
        cancelling_before = task.cancelling() if task is not None else 0
        try:
            with _quiet_sdk_transport():
                read, write = await self._open_transport()
                session = await self._exit_stack.enter_async_context(
                    ClientSession(read, write)
                )
                init = await session.initialize()
            self._session = session
            # SDK 1.27.1 uses camelCase attributes on InitializeResult.
            self._server_info = init.serverInfo
            self._protocol_version = init.protocolVersion
        except BaseException as exc:
            # BaseException, not Exception, because of one specific failure: a
            # stdio server that spawns and then dies (missing interpreter, bad
            # entry point, instant crash). The transport helper runs its reader
            # and writer in an anyio task group, and `enter_async_context` opens
            # that group's cancel scope in OUR task. When the child dies the
            # group cancels the scope, which reaches us as a bare CancelledError
            # — a BaseException — raised out of the `initialize()` above.
            #
            # With `except Exception` that error walked straight past this
            # handler, so the unwind below never ran and the dead transport's
            # cancel scope stayed *entered and cancelled*. Our task was then
            # trapped inside it: every later await re-raised CancelledError, so
            # one dead server took down every server after it in connect_all —
            # the exact opposite of the contract that module documents. Measured
            # here: 5 of 8 runs with a healthy server ahead of a dying one.
            #
            # Unwinding is what fixes it, because exiting the scope is what lets
            # anyio absorb the cancellation it raised. That also tells us whose
            # cancellation it was, which is the thing we must not get wrong.
            teardown_error = await self._reset_transport()
            cancelling_after = task.cancelling() if task is not None else 0
            if cancelling_after > cancelling_before:
                # anyio declined to absorb it: this cancellation is not the
                # dead child's, it is a real cancellation of our caller. Honour
                # it. Reporting it as a connect failure would strand the caller.
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise asyncio.CancelledError from exc
            if isinstance(exc, McpConnectionError):
                raise
            transport = self._contrib.transport
            # Streamable HTTP's background request cancels initialize, then
            # surfaces its actual failure when the transport context exits.
            failure = exc
            if (
                transport != "stdio"
                and isinstance(exc, asyncio.CancelledError)
                and isinstance(teardown_error, Exception)
            ):
                failure = teardown_error
            reason = _connect_failure_reason(transport, failure)
            raise McpConnectionError(
                f"MCP server {self._contrib.name!r} ({transport}) connect failed: {reason}"
            ) from failure

    async def _reset_transport(self) -> BaseException | None:
        """Unwind the transport and return to the never-connected state.

        Return the teardown error so a cancelled remote handshake can recover
        its request failure. A direct handshake error takes precedence.
        """
        try:
            with _quiet_sdk_transport():
                await self._exit_stack.aclose()
        except BaseException as error:
            return error
        finally:
            self._exit_stack = AsyncExitStack()
            self._session = None
            self._server_info = None
            self._protocol_version = None
        return None

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
                args=list(self._contrib.args),
                # `env` ADDS variables. It does NOT widen what the child
                # inherits, and declaring it must never be the thing that
                # decides how much of our environment a spawned program sees.
                #
                # It used to. The old expression was
                # ``{**os.environ, **env} if env else None``, so a manifest
                # with no ``env`` got the SDK's safe set and a manifest with
                # ANY ``env`` got the parent environment WHOLE. Measured on
                # this box: 5 variables (HOME, PATH, SHELL, TERM, USER) versus
                # 131, the latter including GITHUB_TOKEN — and in a real user's
                # shell, every provider API key they have exported.
                #
                # The trap was that the reason to write ``env`` is trivial. A
                # line as innocuous as ``env = { NOTES_LOG = "debug" }`` reads
                # as "add one variable" to every author and reviewer, while
                # actually handing an npx-downloaded program the user's keys.
                # The manifest — the artifact whose whole promise is that you
                # can read it and know what a pack does — was the thing hiding
                # it, and the most harmless-looking line was the trigger.
                #
                # Overlaying the SDK's default instead means the declaration
                # now means what it looks like. A server that genuinely needs a
                # value from the parent environment has no route here on
                # purpose; give it one explicitly (an ``env_passthrough`` list
                # naming the variables) rather than by making every server
                # inherit everything.
                env=(
                    {**get_default_environment(), **self._contrib.env}
                    if self._contrib.env
                    # None → the SDK applies get_default_environment() itself.
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
            url = validate_mcp_endpoint(self._contrib.url)
            read, write, _get_session_id = await self._exit_stack.enter_async_context(
                streamablehttp_client(url=url, headers=self._contrib.headers or None)
            )
            return read, write
        if t == "sse":
            if not self._contrib.url:
                raise McpConnectionError(
                    f"MCP server {self._contrib.name!r}: transport=sse requires url"
                )
            # SSE is deprecated (MCP spec) — supported for legacy servers only.
            url = validate_mcp_endpoint(self._contrib.url)
            read, write = await self._exit_stack.enter_async_context(
                sse_client(url=url, headers=self._contrib.headers or None)
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

    async def discover_tools(self) -> list[mcp_types.Tool]:
        """Discover startup tools while this transport can still be unwound."""
        task = asyncio.current_task()
        cancelling_before = task.cancelling() if task is not None else 0
        try:
            return await self.list_tools()
        except (Exception, asyncio.CancelledError) as exc:
            teardown_error = await self._reset_transport()
            if task is not None and task.cancelling() > cancelling_before:
                raise asyncio.CancelledError from exc
            failure = teardown_error if isinstance(exc, asyncio.CancelledError) else exc
            reason = (
                _connect_failure_reason(self.transport, failure)
                if isinstance(failure, Exception)
                else "connection closed during tool discovery"
            )
            raise McpToolDiscoveryError(
                f"MCP server {self.name!r} ({self.transport}) tools/list failed: {reason}"
            ) from (failure or exc)

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
        with _quiet_sdk_transport():
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
