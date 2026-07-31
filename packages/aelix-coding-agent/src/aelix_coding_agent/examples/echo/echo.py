"""Echo extension — a minimal demo tool.

The example exposes a single :class:`~aelix_agent_core.types.AgentTool`
that echoes its ``text`` argument back. The ``__main__`` demo wires this tool
into an :class:`~aelix_agent_core.agent.Agent` with a mock ``stream_fn`` so
the loop runs end-to-end without any LLM provider.

:func:`setup` below IS the extension entry point (Phase 1.2 / ADR-0007,
matching the pi-coding-agent extension shape): Aelix imports this file and
calls it, and it registers the tool through ``aelix.register_tool``. It also
calls :meth:`ExtensionAPI.register_command` (Phase 3.1 / ADR-0041), so the
example doubles as a smoke test for that surface.

This is the worked example the self-extension signpost sends the agent to read
first (``cli/agent_context.py``, issue #117). Keep it short and keep every
statement in it true of the code below.
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.extensions.api import ExtensionAPI


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


def setup(aelix: ExtensionAPI) -> None:
    """The extension entry point — Aelix imports this file and calls this.

    Registers the ``echo`` tool plus a single slash command via
    :meth:`ExtensionAPI.register_command` (Phase 3.1, ADR-0041).
    """

    aelix.register_tool(echo_tool)

    def _hello_handler(*_args: Any, **_kwargs: Any) -> str:
        return "hello from echo extension"

    aelix.register_command(
        "hello",
        handler=_hello_handler,
        description="Sprint 5a demo command — returns a greeting.",
    )


__all__ = ["echo_tool", "setup"]
