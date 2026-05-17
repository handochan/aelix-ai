"""Echo extension — a minimal demo tool.

The example exposes a single :class:`~aelix_agent_core.types.AgentTool`
that echoes its ``text`` argument back. The ``__main__`` demo wires this tool
into an :class:`~aelix_agent_core.agent.Agent` with a mock ``stream_fn`` so
the loop runs end-to-end without any LLM provider.

The Extension API (Phase 1.2 / ADR-0007) is shipped; a ``setup(aelix)``
factory can be added here to register the same tool through
``aelix.register_tool`` (matching the pi-coding-agent extension shape).

Sprint 5a (ADR-0041) demo: the optional ``setup(aelix)`` factory exercises
one of the new Phase 3.1 :class:`ExtensionAPI` methods —
:meth:`ExtensionAPI.register_command` — so the example doubles as a smoke
test for the expanded surface.
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
    """Sprint 5a demo factory.

    Registers the ``echo`` tool plus a single slash command via
    :meth:`ExtensionAPI.register_command` — one of the new Phase 3.1
    methods landed by ADR-0041.
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
