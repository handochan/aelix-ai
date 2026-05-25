"""MCP Tool → Aelix AgentTool adapter (Sprint 6h₉d, ADR-0101).

MCP ``Tool.inputSchema`` (JSON Schema) maps zero-transform to Aelix
``Tool.parameters``. ``call_tool`` is wrapped in a ``Tool.execute`` closure
matching the Aelix ``ToolExecute`` signature ``(args, context) -> ToolResult``.
``CallToolResult.content`` blocks map to Aelix ``ToolResult`` content
(``TextContent`` / ``ImageContent``), and ``CallToolResult.isError`` maps to
``ToolResult.is_error`` (MCP domain errors are NOT Python exceptions —
gotcha #5 from W0 research).

SDK-version note (1.27.1): block attributes are camelCase
(``ImageContent.mimeType`` / ``.data``) and the result error flag is
``CallToolResult.isError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import mcp.types as mcp_types
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import ImageContent, TextContent
from aelix_ai.tools import ToolContent, ToolExecutionContext, ToolResult

if TYPE_CHECKING:
    from aelix_coding_agent.mcp.client import McpServerConnection


def _content_blocks_to_tool_result(
    result: mcp_types.CallToolResult,
) -> ToolResult:
    """Map an MCP ``CallToolResult`` to an Aelix ``ToolResult``.

    - ``TextContent`` → Aelix ``TextContent``
    - ``ImageContent`` → Aelix ``ImageContent`` (base64 ``data`` + ``mime_type``)
    - other block types (audio / resource_link / embedded_resource) → a text
      fallback carrying a typed marker for Phase 6 richer handling
    - ``isError=True`` → ``ToolResult.is_error`` (MCP domain errors are not
      Python exceptions)

    ``structuredContent`` (when present) is preserved on ``ToolResult.details``.
    """
    content: ToolContent = []
    for block in result.content:
        if isinstance(block, mcp_types.TextContent):
            content.append(TextContent(text=block.text))
        elif isinstance(block, mcp_types.ImageContent):
            content.append(
                ImageContent(data=block.data, mime_type=block.mimeType)
            )
        else:
            # audio / resource_link / embedded_resource — Phase 6 will map
            # these richly; for now surface a typed marker as text so the
            # block is never silently dropped.
            block_type = getattr(block, "type", type(block).__name__)
            content.append(
                TextContent(text=f"[mcp:{block_type} content block]")
            )
    return ToolResult(
        content=content,
        details=result.structuredContent,
        is_error=bool(result.isError),
    )


def mcp_tool_to_agent_tool(
    conn: McpServerConnection,
    tool: mcp_types.Tool,
    *,
    name_prefix: str | None = None,
) -> AgentTool:
    """Wrap one MCP tool as an Aelix ``AgentTool``.

    Args:
        conn: the live MCP connection that backs ``call_tool``.
        tool: the MCP tool descriptor (name, description, inputSchema).
        name_prefix: optional namespace prefix to avoid collisions across
            servers (``"<prefix>__<tool>"``). When ``None``, the bare MCP tool
            name is used.

    The returned ``AgentTool``'s ``execute`` closure calls
    ``conn.call_tool(tool.name, args)`` and maps the result. The MCP
    ``inputSchema`` is passed verbatim as ``parameters`` (zero-transform).
    """
    qualified = f"{name_prefix}__{tool.name}" if name_prefix else tool.name

    async def _execute(
        args: dict[str, Any],
        _context: ToolExecutionContext,
    ) -> ToolResult:
        result = await conn.call_tool(tool.name, args)
        return _content_blocks_to_tool_result(result)

    return AgentTool(
        name=qualified,
        description=tool.description or "",
        # zero-transform JSON Schema passthrough (MCP inputSchema → parameters)
        parameters=tool.inputSchema,
        execute=_execute,
    )


def mcp_tools_to_agent_tools(
    conn: McpServerConnection,
    tools: list[mcp_types.Tool],
    *,
    name_prefix: str | None = None,
) -> list[AgentTool]:
    return [
        mcp_tool_to_agent_tool(conn, t, name_prefix=name_prefix) for t in tools
    ]
