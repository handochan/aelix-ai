"""Session-owned MCP connections with same-task transport cleanup."""
from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Any
from urllib.parse import urlsplit

from aelix_agent_core.contracts import McpServerContrib

from aelix_coding_agent.mcp import McpClientManager


def contributions(servers: Any) -> list[McpServerContrib]:
    if not isinstance(servers, list) or len(servers) > 32:
        raise ValueError("MCP servers must be a list of at most 32 entries")
    result = []
    names: set[str] = set()
    for server in servers:
        if not isinstance(server, dict):
            raise ValueError("MCP server must be an object")
        name = server.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name) or name in names:
            raise ValueError("MCP server names must be unique bounded identifiers")
        names.add(name)
        transport = server.get("type", "stdio")
        if transport == "stdio":
            command, args, env = server.get("command"), server.get("args", []), server.get("env", [])
            if not isinstance(command, str) or not command or "\x00" in command:
                raise ValueError("MCP stdio command is required")
            if not isinstance(args, list) or len(args) > 256 or any(not isinstance(arg, str) or "\x00" in arg for arg in args):
                raise ValueError("Invalid MCP arguments")
            if not isinstance(env, list) or len(env) > 256:
                raise ValueError("Invalid MCP environment")
            variables = {}
            for entry in env:
                if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("value"), str):
                    raise ValueError("MCP environment entries need string names and values")
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry["name"]) or "\x00" in entry["value"]:
                    raise ValueError("Invalid MCP environment variable")
                variables[entry["name"]] = entry["value"]
            result.append(McpServerContrib(name="acp_" + name, transport="stdio", command=command, args=args, env=variables))
        elif transport in {"http", "sse"}:
            url = server.get("url")
            if not isinstance(url, str):
                raise ValueError("MCP HTTP URL is required")
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("MCP HTTP URL must use http(s) without embedded credentials")
            headers = server.get("headers", [])
            if not isinstance(headers, list) or len(headers) > 64:
                raise ValueError("Invalid MCP headers")
            values = {}
            for entry in headers:
                if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("value"), str):
                    raise ValueError("MCP header entries need names and values")
                if any(ch in entry["name"] + entry["value"] for ch in "\r\n\x00"):
                    raise ValueError("Invalid MCP header characters")
                values[entry["name"]] = entry["value"]
            result.append(McpServerContrib(name="acp_" + name, transport=transport, url=url, headers=values))
        else:
            raise ValueError("Unsupported MCP transport")
    return result


class RpcMcpController:
    def __init__(self) -> None:
        self.manager: McpClientManager | None = None
        self.tools: list[Any] = []
        self.owner: asyncio.Task[None] | None = None
        self.stop: asyncio.Event | None = None
        self.leases: dict[asyncio.Task[None], asyncio.Event] = {}

    async def configure(self, servers: Any, harness: Any) -> dict[str, Any]:
        manager = McpClientManager(contributions(servers))
        original_tools = list(harness.state.tools)
        ready: asyncio.Future[list[Any]] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()

        async def own() -> None:
            try:
                errors = await manager.connect_all()
                if errors:
                    raise ValueError("One or more MCP servers failed to connect")
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
            tools = await asyncio.wait_for(asyncio.shield(ready), 45)
            old_ids = {id(tool) for tool in self.tools}
            base = [tool for tool in harness.state.tools if id(tool) not in old_ids]
            if {tool.name for tool in base} & {tool.name for tool in tools}:
                raise ValueError("Client MCP tools collide with existing runtime tools")
            await harness.set_tools(base + tools)
        except BaseException:
            with contextlib.suppress(Exception):
                await harness.set_tools(original_tools)
            stop.set()
            if not ready.done():
                ready.cancel()
                owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
            raise
        previous_owner, previous_stop = self.owner, self.stop
        self.manager, self.tools, self.owner, self.stop = manager, tools, owner, stop
        if previous_stop:
            previous_stop.set()
        if previous_owner:
            await asyncio.shield(asyncio.gather(previous_owner, return_exceptions=True))
        return self.status()

    async def attach(self, harness: Any) -> None:
        if self.tools:
            ids = {id(tool) for tool in self.tools}
            await harness.set_tools([tool for tool in harness.state.tools if id(tool) not in ids] + self.tools)

    def status(self) -> dict[str, Any]:
        return {"servers": [{"name": connection.name, "transport": connection.transport,
                             "connected": connection.connected}
                            for connection in self.manager.connections.values()] if self.manager else []}

    async def close(self) -> None:
        owned = list(self.leases)
        for stop in list(self.leases.values()):
            stop.set()
        if owned:
            await asyncio.gather(*owned, return_exceptions=True)
        self.manager, self.owner, self.stop = None, None, None
        self.tools = []
