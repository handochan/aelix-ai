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
    StreamSimpleError,
    TextContent,
    Tool,
    ToolCallContent,
    ToolPartialCallback,
    ToolResult,
    ToolResultMessage,
    UserMessage,
    clear_providers,
    get_registered_providers,
    register_provider,
    unregister_provider,
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
    "StreamSimpleError",
    "TextContent",
    "Tool",
    "ToolCallContent",
    "ToolPartialCallback",
    "ToolResult",
    "ToolResultMessage",
    "UserMessage",
    "clear_providers",
    "get_registered_providers",
    "register_provider",
    "unregister_provider",
]
