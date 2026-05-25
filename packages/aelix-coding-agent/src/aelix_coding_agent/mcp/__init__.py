"""MCP client subpackage (Sprint 6h₉d, Tier 4a — ADR-0101).

Aelix-additive: Pi has no MCP client in core. Reference = MCP spec +
official ``mcp`` Python SDK 1.27.1 + Claude Code ``mcpServers`` config.

Import-shadow note: this subpackage is ``aelix_coding_agent.mcp``; the SDK
is the top-level ``mcp`` package. All SDK imports here and in submodules are
absolute (``from mcp import ...``) so Python resolves the SDK from
site-packages with no relative-shadow collision.
"""

from __future__ import annotations

from aelix_coding_agent.mcp.client import (
    McpConnectionError,
    McpServerConnection,
)

__all__ = [
    "McpConnectionError",
    "McpServerConnection",
]
