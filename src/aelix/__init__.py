"""Aelix umbrella — convenience re-exports across workspace packages.

For tighter dependency footprints, import directly from the per-package
modules (``aelix_ai``, ``aelix_agent_core``, ``aelix_coding_agent``) instead
of from this umbrella.
"""

from aelix_agent_core import (
    Agent,
    AgentOptions,
    AgentState,
    AgentTool,
)
from aelix_agent_core.harness import (
    AgentHarness,
    AgentHarnessOptions,
)
from aelix_ai import (
    AssistantMessage,
    Message,
    Model,
    TextContent,
    Tool,
    ToolCallContent,
    ToolResult,
    ToolResultMessage,
    UserMessage,
)
from aelix_coding_agent.builtin import (
    GuardrailExtension,
    PolicyExtension,
)

__all__ = [
    "Agent",
    "AgentHarness",
    "AgentHarnessOptions",
    "AgentOptions",
    "AgentState",
    "AgentTool",
    "AssistantMessage",
    "GuardrailExtension",
    "Message",
    "Model",
    "PolicyExtension",
    "TextContent",
    "Tool",
    "ToolCallContent",
    "ToolResult",
    "ToolResultMessage",
    "UserMessage",
]
