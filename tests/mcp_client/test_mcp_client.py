"""MCP client connection + adapter + manager tests (Sprint 6h₉d §D, ADR-0101).

Strategy: a real stdio MCP echo server (`_echo_server.py`, FastMCP) is spawned
as a subprocess to exercise the full `McpServerConnection` lifecycle —
transport open, `initialize` handshake, `list_tools`, `call_tool` (text +
`isError`), and clean `AsyncExitStack` teardown (no zombie subprocesses).

Because `McpServerContrib` (6h₉a) has no `args` field (v2 deferred), the echo
server is launched via a single-command wrapper script (no args), matching the
real connection path which always passes `args=[]`.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import mcp.types as mcp_types
import pytest
from aelix_agent_core.contracts import McpServerContrib
from aelix_ai.messages import ImageContent, TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.mcp import (
    McpClientManager,
    McpConnectionError,
    McpServerConnection,
)
from aelix_coding_agent.mcp.adapter import (
    _content_blocks_to_tool_result,
    mcp_tool_to_agent_tool,
    mcp_tools_to_agent_tools,
)
from aelix_coding_agent.mcp.client import StdioServerParameters

_ECHO_SERVER = Path(__file__).with_name("_echo_server.py")


@pytest.fixture(scope="session")
def echo_command(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A single-command launcher (no args) that runs the stdio echo server.

    McpServerContrib has no `args` field (v2 deferred), so we wrap the
    interpreter + server path in a tiny executable shell script and point
    `command` at that script directly.
    """
    script_dir = tmp_path_factory.mktemp("echo_launcher")
    launcher = script_dir / "echo_server.sh"
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{_ECHO_SERVER}" "$@"\n'
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
    yield str(launcher)


def _stdio_contrib(name: str, command: str) -> McpServerContrib:
    return McpServerContrib(name=name, transport="stdio", command=command)


# === 1. import-shadow guard ===========================================


def test_mcp_sdk_importable() -> None:
    """SDK `mcp` and local `aelix_coding_agent.mcp` both resolve (no shadow)."""
    import mcp  # noqa: PLC0415 — intentional in-test import
    from aelix_coding_agent.mcp.client import (  # noqa: PLC0415
        McpServerConnection as LocalConn,
    )

    assert mcp.types is mcp_types
    assert LocalConn is McpServerConnection


# === 2-8. connection lifecycle ========================================


@pytest.mark.asyncio
async def test_stdio_connect_initialize(echo_command: str) -> None:
    async with McpServerConnection(_stdio_contrib("echo", echo_command)) as conn:
        assert conn.connected is True
        assert conn.server_info is not None
        assert conn.server_info.name == "aelix-echo-test-server"
        assert conn.protocol_version is not None


@pytest.mark.asyncio
async def test_list_tools(echo_command: str) -> None:
    async with McpServerConnection(_stdio_contrib("echo", echo_command)) as conn:
        tools = await conn.list_tools()
        names = {t.name for t in tools}
        assert "echo" in names
        assert len(tools) >= 1


@pytest.mark.asyncio
async def test_call_tool_text_result(echo_command: str) -> None:
    async with McpServerConnection(_stdio_contrib("echo", echo_command)) as conn:
        result = await conn.call_tool("echo", {"text": "hi"})
        assert isinstance(result, mcp_types.CallToolResult)
        assert result.isError is False
        mapped = _content_blocks_to_tool_result(result)
        assert mapped.is_error is False
        assert any(
            isinstance(c, TextContent) and "echo: hi" in c.text
            for c in mapped.content
        )


@pytest.mark.asyncio
async def test_call_tool_is_error(echo_command: str) -> None:
    # `boom` raises server-side; FastMCP maps it to isError=True (NOT a
    # transport exception). The mapper must surface is_error, not raise.
    async with McpServerConnection(_stdio_contrib("echo", echo_command)) as conn:
        result = await conn.call_tool("boom", {})
        assert result.isError is True
        mapped = _content_blocks_to_tool_result(result)
        assert mapped.is_error is True


@pytest.mark.asyncio
async def test_disconnect_idempotent(echo_command: str) -> None:
    conn = McpServerConnection(_stdio_contrib("echo", echo_command))
    await conn.connect()
    await conn.disconnect()
    assert conn.connected is False
    # second disconnect is a no-op
    await conn.disconnect()
    assert conn.connected is False


@pytest.mark.asyncio
async def test_connect_idempotent(echo_command: str) -> None:
    conn = McpServerConnection(_stdio_contrib("echo", echo_command))
    await conn.connect()
    session_before = conn._session  # noqa: SLF001 — assert no re-spawn
    await conn.connect()  # idempotent
    assert conn._session is session_before  # noqa: SLF001
    await conn.disconnect()


@pytest.mark.asyncio
async def test_async_context_manager(echo_command: str) -> None:
    contrib = _stdio_contrib("echo", echo_command)
    async with McpServerConnection(contrib) as conn:
        assert conn.connected is True
        tools = await conn.list_tools()
        assert tools
    assert conn.connected is False


# === 9-11. transport error guards =====================================


@pytest.mark.asyncio
async def test_stdio_missing_command_raises() -> None:
    contrib = McpServerContrib(name="bad", transport="stdio", command=None)
    conn = McpServerConnection(contrib)
    with pytest.raises(McpConnectionError, match="requires"):
        await conn.connect()
    assert conn.connected is False


@pytest.mark.asyncio
async def test_http_missing_url_raises() -> None:
    contrib = McpServerContrib(name="bad", transport="http", url=None)
    conn = McpServerConnection(contrib)
    with pytest.raises(McpConnectionError, match="requires url"):
        await conn.connect()
    assert conn.connected is False


@pytest.mark.asyncio
async def test_unknown_transport_raises() -> None:
    # Pydantic blocks invalid transport literals at construction; forge a valid
    # instance then mutate the private attr to exercise the internal guard.
    contrib = McpServerContrib(
        name="bad", transport="stdio", command="/bin/true"
    )
    object.__setattr__(contrib, "transport", "carrier-pigeon")
    conn = McpServerConnection(contrib)
    with pytest.raises(McpConnectionError, match="unknown transport"):
        await conn.connect()


# === 12-14. adapter ===================================================


def _fake_tool(name: str = "echo") -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name,
        description="echo a string",
        inputSchema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )


def test_mcp_tool_to_agent_tool_schema_passthrough() -> None:
    tool = _fake_tool()
    agent_tool = mcp_tool_to_agent_tool(conn=None, tool=tool)  # type: ignore[arg-type]
    # zero-transform: parameters IS the MCP inputSchema dict
    assert agent_tool.parameters == tool.inputSchema
    assert agent_tool.parameters is tool.inputSchema
    assert agent_tool.name == "echo"
    assert agent_tool.description == "echo a string"


def test_mcp_tool_namespace_prefix() -> None:
    tool = _fake_tool()
    agent_tool = mcp_tool_to_agent_tool(
        conn=None,  # type: ignore[arg-type]
        tool=tool,
        name_prefix="srv",
    )
    assert agent_tool.name == "srv__echo"


def test_mcp_tools_to_agent_tools_batch() -> None:
    tools = [_fake_tool("echo"), _fake_tool("ping")]
    agent_tools = mcp_tools_to_agent_tools(
        conn=None,  # type: ignore[arg-type]
        tools=tools,
        name_prefix="srv",
    )
    assert [t.name for t in agent_tools] == ["srv__echo", "srv__ping"]


@pytest.mark.asyncio
async def test_agent_tool_execute_calls_call_tool(echo_command: str) -> None:
    async with McpServerConnection(_stdio_contrib("echo", echo_command)) as conn:
        tools = await conn.list_tools()
        echo_tool = next(t for t in tools if t.name == "echo")
        agent_tool = mcp_tool_to_agent_tool(conn, echo_tool)
        assert agent_tool.execute is not None
        result = await agent_tool.execute({"text": "yo"}, ToolExecutionContext())
        assert isinstance(result, ToolResult)
        assert result.is_error is False
        assert any(
            isinstance(c, TextContent) and "echo: yo" in c.text
            for c in result.content
        )


def test_content_blocks_image_mapping() -> None:
    result = mcp_types.CallToolResult(
        content=[
            mcp_types.ImageContent(
                type="image", data="QUJD", mimeType="image/png"
            )
        ]
    )
    mapped = _content_blocks_to_tool_result(result)
    assert len(mapped.content) == 1
    img = mapped.content[0]
    assert isinstance(img, ImageContent)
    assert img.data == "QUJD"
    assert img.mime_type == "image/png"


# === 15-17. manager ===================================================


@pytest.mark.asyncio
async def test_manager_connect_all_partial_failure(echo_command: str) -> None:
    good = _stdio_contrib("good", echo_command)
    bad = McpServerContrib(name="bad", transport="stdio", command=None)
    manager = McpClientManager([good, bad])
    try:
        errors = await manager.connect_all()
        # one bad server returns an error; the good one still connects
        assert len(errors) == 1
        assert manager.connections["good"].connected is True
        assert manager.connections["bad"].connected is False
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_manager_collect_agent_tools_namespaced(
    echo_command: str,
) -> None:
    s1 = _stdio_contrib("alpha", echo_command)
    s2 = _stdio_contrib("beta", echo_command)
    manager = McpClientManager([s1, s2])
    try:
        await manager.connect_all()
        tools = await manager.collect_agent_tools()
        names = {t.name for t in tools}
        assert "alpha__echo" in names
        assert "beta__echo" in names
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_manager_disconnect_all(echo_command: str) -> None:
    manager = McpClientManager([_stdio_contrib("a", echo_command)])
    await manager.connect_all()
    assert manager.connections["a"].connected is True
    await manager.disconnect_all()
    assert manager.connections["a"].connected is False


@pytest.mark.asyncio
async def test_manager_call_tool_with_retry_stdio(echo_command: str) -> None:
    manager = McpClientManager([_stdio_contrib("a", echo_command)])
    try:
        await manager.connect_all()
        result = await manager.call_tool_with_retry("a", "echo", {"text": "z"})
        assert result.isError is False
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_manager_unknown_server_raises(echo_command: str) -> None:
    manager = McpClientManager([_stdio_contrib("a", echo_command)])
    with pytest.raises(McpConnectionError, match="unknown MCP server"):
        await manager.call_tool_with_retry("nope", "echo", {})


# === 18. env=None default inherit =====================================


def test_env_none_default_inherit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty contrib.env → StdioServerParameters.env stays None (safe inherit).

    `env={}` would break the subprocess (no PATH); `env=None` lets the SDK
    apply `get_default_environment()`. We assert the connection builds params
    with `env=None` when contrib.env is empty, and merges os.environ when set.
    """
    captured: dict[str, StdioServerParameters] = {}

    class _Spy:
        def __init__(self, params: StdioServerParameters) -> None:
            captured["params"] = params

        async def __aenter__(self):  # noqa: ANN204
            raise McpConnectionError("spy-stop")  # abort before real spawn

        async def __aexit__(self, *exc: object) -> None:
            return None

    import aelix_coding_agent.mcp.client as client_mod

    monkeypatch.setattr(client_mod, "stdio_client", lambda params: _Spy(params))

    async def _run(contrib: McpServerContrib) -> None:
        conn = McpServerConnection(contrib)
        with pytest.raises(McpConnectionError):
            await conn.connect()

    import asyncio

    # empty env → None
    asyncio.run(_run(_stdio_contrib("e", "/bin/true")))
    assert captured["params"].env is None

    # non-empty env → merged with os.environ
    captured.clear()
    contrib = McpServerContrib(
        name="e", transport="stdio", command="/bin/true", env={"FOO": "bar"}
    )
    asyncio.run(_run(contrib))
    env = captured["params"].env
    assert env is not None
    assert env["FOO"] == "bar"
    assert "PATH" in env or len(env) > 1  # os.environ merged in
    assert os.environ  # sanity


@pytest.mark.asyncio
async def test_stdio_args_flow_to_server_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """McpServerContrib.args reaches StdioServerParameters (npx-style servers).

    Proves the ADR fix replacing the hardcoded ``args=[]``. Captures the params
    at the ``stdio_client`` seam and short-circuits before a real spawn.
    """

    import aelix_coding_agent.mcp.client as client_mod

    captured: dict[str, object] = {}

    def _fake_stdio_client(params: object) -> object:
        captured["command"] = params.command  # type: ignore[attr-defined]
        captured["args"] = list(params.args)  # type: ignore[attr-defined]
        raise RuntimeError("stop-before-spawn")

    monkeypatch.setattr(client_mod, "stdio_client", _fake_stdio_client)

    contrib = McpServerContrib(
        name="fs",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    )
    conn = McpServerConnection(contrib)
    try:
        async with conn:
            pass
    except Exception:  # noqa: BLE001 — the fake raises to avoid a real spawn
        pass

    assert captured.get("command") == "npx"
    assert captured.get("args") == [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/tmp",
    ]
