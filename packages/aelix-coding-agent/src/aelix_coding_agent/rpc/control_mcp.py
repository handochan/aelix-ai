"""Client-configured MCP servers for the RPC control surface (ADR-0275).

One owner task per configuration lease enters and exits every transport, so the
AnyIO contexts the MCP SDK opens close on the task that entered them. A
configuration replaces the previous lease only after the new one is connected.

The harness's explicit tool selection is the user's, so a lease never widens it:
new client tools join an explicit selection only when a tool of the same name was
already selected, and removing a selected tool narrows the selection instead of
failing (#594). Statuses carry the registered and client server names and the
transport only — never URLs, environment values or headers.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from aelix_agent_core.contracts import McpServerContrib

from aelix_coding_agent.mcp import McpClientManager

CLIENT_PREFIX = "acp_"
# Provider tool names are ``^[a-zA-Z0-9_-]{1,64}$`` at the strictest provider
# (OpenAI; Anthropic). ``acp_`` + a 32-character server key + ``__`` leaves 26
# characters for the server's own tool name.
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
_SERVER_KEY_LENGTH = 32
_PROVIDER_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
CONNECT_TIMEOUT_SECONDS = 45.0


def server_key(name: str) -> str:
    """The provider-safe key a client's server name is registered under.

    ACP server names are human-readable, so they are mapped rather than refused:
    every run of characters outside ``[A-Za-z0-9_-]`` becomes ``_`` and the key
    is cut to 32 characters (``"github.com"`` -> ``github_com``).
    """

    return _UNSAFE_NAME_CHARS.sub("_", name).strip("_")[:_SERVER_KEY_LENGTH]


def contributions(servers: Any) -> list[McpServerContrib]:
    if not isinstance(servers, list) or len(servers) > 32:
        raise ValueError("MCP servers must be a list of at most 32 entries")
    result = []
    keys: set[str] = set()
    for server in servers:
        if not isinstance(server, dict):
            raise ValueError("MCP server must be an object")
        name = server.get("name")
        if (
            not isinstance(name, str)
            or not 0 < len(name) <= 128
            or any(ord(ch) < 32 for ch in name)
        ):
            raise ValueError("MCP server names must be 1-128 printable characters")
        key = server_key(name)
        if not key or key in keys:
            raise ValueError(
                f"MCP server name {name!r} must map to a unique key of letters, digits, '_' or "
                "'-' (other characters become '_', and the key is cut to 32 characters)"
            )
        keys.add(key)
        transport = server.get("type", "stdio")
        if transport == "stdio":
            command, args, env = (
                server.get("command"),
                server.get("args", []),
                server.get("env", []),
            )
            if not isinstance(command, str) or not command or "\x00" in command:
                raise ValueError("MCP stdio command is required")
            if (
                not isinstance(args, list)
                or len(args) > 256
                or any(not isinstance(arg, str) or "\x00" in arg for arg in args)
            ):
                raise ValueError("Invalid MCP arguments")
            if not isinstance(env, list) or len(env) > 256:
                raise ValueError("Invalid MCP environment")
            variables = {}
            for entry in env:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("name"), str)
                    or not isinstance(entry.get("value"), str)
                ):
                    raise ValueError("MCP environment entries need string names and values")
                if (
                    not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry["name"])
                    or "\x00" in entry["value"]
                ):
                    raise ValueError("Invalid MCP environment variable")
                variables[entry["name"]] = entry["value"]
            result.append(
                McpServerContrib(
                    name=CLIENT_PREFIX + key,
                    transport="stdio",
                    command=command,
                    args=args,
                    env=variables,
                )
            )
        elif transport in {"http", "sse"}:
            url = server.get("url")
            if not isinstance(url, str):
                raise ValueError("MCP HTTP URL is required")
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("MCP HTTP URL must use http(s) without embedded credentials")
            headers = server.get("headers", [])
            if not isinstance(headers, list) or len(headers) > 64:
                raise ValueError("Invalid MCP headers")
            values = {}
            for entry in headers:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("name"), str)
                    or not isinstance(entry.get("value"), str)
                ):
                    raise ValueError("MCP header entries need names and values")
                if any(ch in entry["name"] + entry["value"] for ch in "\r\n\x00"):
                    raise ValueError("Invalid MCP header characters")
                values[entry["name"]] = entry["value"]
            result.append(
                McpServerContrib(
                    name=CLIENT_PREFIX + key, transport=transport, url=url, headers=values
                )
            )
        else:
            raise ValueError("Unsupported MCP transport")
    return result


def provider_safe_tools(tools: list[Any]) -> tuple[list[Any], list[dict[str, str]]]:
    """Split collected tools into those every provider accepts and skipped diagnostics.

    A server's own tool names are not ours to choose; one that would push a
    qualified name outside the provider pattern would make every turn fail at
    the provider, so it is left out and reported instead.
    """

    kept, skipped = [], []
    for tool in tools:
        if _PROVIDER_TOOL_NAME.fullmatch(tool.name):
            kept.append(tool)
        else:
            skipped.append(
                {"tool": tool.name, "reason": "name is not 1-64 characters of [A-Za-z0-9_-]"}
            )
    return kept, skipped


def _connect_failures(
    manager: McpClientManager, errors: list[Exception], client_names: dict[str, str]
) -> str:
    # connect_all reports one error per failed server, in declaration order. Name
    # the server and the error class only: the message can carry a URL.
    failed = [name for name, connection in manager.connections.items() if not connection.connected]
    parts = []
    for name, error in zip(failed, errors, strict=False):
        cause = error.__cause__ or error
        parts.append(f"{client_names.get(name, name)!r} ({type(cause).__name__})")
    return "MCP server(s) failed to connect: " + ", ".join(parts or ["unknown"])


def _carry_selection(
    active: list[str] | None, old_names: set[str], new_tools: list[Any]
) -> list[str] | None:
    """The explicit selection after replacing client tools; ``None`` stays ``None``."""

    if active is None:
        return None
    kept = [name for name in active if name not in old_names]
    carried = [tool.name for tool in new_tools if tool.name in active and tool.name not in kept]
    return kept + carried


class RpcMcpController:
    def __init__(
        self,
        *,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        manager_factory: Callable[[list[McpServerContrib]], McpClientManager] = McpClientManager,
    ) -> None:
        self.connect_timeout = connect_timeout
        self.manager_factory = manager_factory
        self.manager: McpClientManager | None = None
        self.client_names: dict[str, str] = {}
        self.tools: list[Any] = []
        self.skipped: list[dict[str, str]] = []
        self.owner: asyncio.Task[None] | None = None
        self.stop: asyncio.Event | None = None
        self.leases: dict[asyncio.Task[None], asyncio.Event] = {}

    async def configure(self, servers: Any, harness_provider: Callable[[], Any]) -> dict[str, Any]:
        manager = self.manager_factory(contributions(servers))
        client_names = {
            CLIENT_PREFIX + server_key(server["name"]): server["name"] for server in servers
        }
        ready: asyncio.Future[list[Any]] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()

        async def own() -> None:
            try:
                errors = await manager.connect_all()
                if errors:
                    raise ValueError(_connect_failures(manager, list(errors), client_names))
                tools = await manager.collect_agent_tools()
                ready.set_result(tools)
                await stop.wait()
            except BaseException as error:
                if not ready.done():
                    ready.set_exception(error)
                raise
            finally:
                await manager.disconnect_all()

        owner = asyncio.create_task(own())
        self.leases[owner] = stop
        owner.add_done_callback(lambda task: self.leases.pop(task, None))
        try:
            try:
                collected = await asyncio.wait_for(asyncio.shield(ready), self.connect_timeout)
            except TimeoutError:
                raise ValueError(
                    f"MCP servers did not finish connecting within {self.connect_timeout:g} seconds"
                ) from None
            tools, skipped = provider_safe_tools(collected)
            # Read the harness now, not when the request arrived: the lease is
            # applied to whichever harness is live once its servers are up.
            harness = harness_provider()
            old_ids = {id(tool) for tool in self.tools}
            old_names = {tool.name for tool in self.tools}
            base = [tool for tool in harness.state.tools if id(tool) not in old_ids]
            if {tool.name for tool in base} & {tool.name for tool in tools}:
                raise ValueError("Client MCP tools collide with existing runtime tools")
            selection = _carry_selection(harness.state.active_tool_names, old_names, tools)
            # ``set_tools`` validates before it mutates, so a refusal leaves the
            # harness untouched and there is nothing to roll back on it.
            await harness.set_tools(base + tools, selection)
        except BaseException:
            stop.set()
            if not ready.done():
                ready.cancel()
                owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
            raise
        previous_owner, previous_stop = self.owner, self.stop
        self.manager, self.tools, self.skipped = manager, tools, skipped
        self.client_names = client_names
        self.owner, self.stop = owner, stop
        if previous_stop:
            previous_stop.set()
        if previous_owner:
            await asyncio.shield(asyncio.gather(previous_owner, return_exceptions=True))
        return self.status()

    async def attach(self, harness: Any, previous: Any = None, reason: str = "resume") -> None:
        """Re-register the lease's tools on a replacement harness.

        The replacement's own selection rules: a reload preserves the user's
        selection, so client tools that were selected before it are selected
        again; any other replacement starts from the launch selection, which new
        client tools never widen.
        """

        if not self.tools:
            return
        ids = {id(tool) for tool in self.tools}
        base = [tool for tool in harness.state.tools if id(tool) not in ids]
        taken = {tool.name for tool in base}
        tools = [tool for tool in self.tools if tool.name not in taken]
        for tool in self.tools:
            if tool.name in taken:
                print(
                    f"[rpc] client MCP tool {tool.name!r} is shadowed by a runtime tool of the same name",
                    file=sys.stderr,
                    flush=True,
                )
        active = harness.state.active_tool_names
        selection = None if active is None else list(active)
        if selection is not None and reason == "reload" and previous is not None:
            before = previous.state.active_tool_names
            client = {tool.name for tool in tools}
            selected = client if before is None else client & set(before)
            selection += [
                tool.name for tool in tools if tool.name in selected and tool.name not in selection
            ]
        await harness.set_tools(base + tools, selection)

    def status(self) -> dict[str, Any]:
        servers = (
            [
                {
                    "name": connection.name,
                    "clientName": self.client_names.get(connection.name),
                    "transport": connection.transport,
                    "connected": connection.connected,
                }
                for connection in self.manager.connections.values()
            ]
            if self.manager
            else []
        )
        return {"servers": servers, "skippedTools": list(self.skipped)}

    async def close(self) -> None:
        owned = list(self.leases)
        for stop in list(self.leases.values()):
            stop.set()
        if owned:
            await asyncio.gather(*owned, return_exceptions=True)
        with contextlib.suppress(Exception):
            if self.owner is not None and self.owner.done() and not self.owner.cancelled():
                self.owner.exception()  # retrieve, so a dead lease does not log at GC
        self.manager, self.owner, self.stop = None, None, None
        self.tools, self.skipped, self.client_names = [], [], {}
