"""Minimal stdio MCP echo server fixture (Sprint 6h₉d test support).

Run as a subprocess over stdio by `test_mcp_client.py` to exercise the full
`McpServerConnection` lifecycle (transport + initialize handshake + list_tools
+ call_tool + AsyncExitStack teardown) against a real MCP server.

Tools:
    echo(text)        -> TextContent echoing ``text``
    boom()            -> CallToolResult(isError=True) domain error (NOT a
                         Python exception — exercises the is_error gotcha)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("aelix-echo-test-server")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the given text back to the caller."""
    return f"echo: {text}"


@mcp.tool()
def boom() -> str:
    """Always raise — FastMCP maps the exception to isError=True."""
    raise ValueError("boom: intentional domain error")


if __name__ == "__main__":
    mcp.run(transport="stdio")
