"""Aelix agent runtime."""

from aelix.agent import Agent, AgentOptions, AgentState
from aelix.agent.types import AgentTool
from aelix.ai import (
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


def __getattr__(name: str) -> object:
    if name in ("AgentHarness", "AgentHarnessOptions"):
        from aelix.harness import AgentHarness, AgentHarnessOptions

        return {"AgentHarness": AgentHarness, "AgentHarnessOptions": AgentHarnessOptions}[name]
    if name in ("PolicyExtension", "GuardrailExtension"):
        from aelix.builtin import GuardrailExtension, PolicyExtension

        return {"PolicyExtension": PolicyExtension, "GuardrailExtension": GuardrailExtension}[name]
    raise AttributeError(f"module 'aelix' has no attribute {name!r}")
