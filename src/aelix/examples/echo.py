"""Echo extension — a minimal demo tool.

In Phase 1.1 the example exposes a single :class:`~aelix.agent.types.AgentTool`
that echoes its ``text`` argument back. The ``__main__`` demo wires this tool
into an :class:`~aelix.agent.Agent` with a mock ``stream_fn`` so the loop runs
end-to-end without any LLM provider.

Once the Extension API arrives (Phase 1.2), this module will additionally
expose a ``setup(aelix)`` factory that registers the same tool through
``aelix.register_tool`` (matching the pi-coding-agent extension shape).
"""

from __future__ import annotations

from typing import Any

from aelix.agent.types import AgentTool
from aelix.ai.messages import TextContent
from aelix.ai.tools import ToolExecutionContext, ToolResult


async def _echo_execute(
    args: dict[str, Any], context: ToolExecutionContext
) -> ToolResult:
    text = str(args.get("text", ""))
    return ToolResult(
        content=[TextContent(text=f"echoed: {text}")],
    )


echo_tool: AgentTool = AgentTool(
    name="echo",
    description="Echoes back the provided text.",
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to echo back.",
            }
        },
        "required": ["text"],
    },
    execute=_echo_execute,
)


__all__ = ["echo_tool"]
