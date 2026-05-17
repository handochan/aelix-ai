"""Aelix AI primitives — messages, tools, streaming.

Mirrors the pi-ai package shape so the agent loop stays provider-agnostic and
tests can supply a mock ``stream_fn`` (Phase 1.1) before real provider adapters
land in Phase 2 under ``aelix.ai.providers``.
"""

from aelix.ai.messages import (
    AssistantMessage,
    ContentBlock,
    ImageContent,
    Message,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix.ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Cost,
    Model,
    SimpleStreamOptions,
    StreamFn,
    TextDeltaEvent,
    ToolCallDeltaEvent,
    stream_simple,
)
from aelix.ai.tools import (
    Tool,
    ToolContent,
    ToolExecute,
    ToolExecutionContext,
    ToolResult,
    validate_tool_arguments,
)

__all__ = [
    "AssistantEndEvent",
    "AssistantMessage",
    "AssistantMessageEvent",
    "AssistantStartEvent",
    "ContentBlock",
    "Context",
    "Cost",
    "ImageContent",
    "Message",
    "Model",
    "SimpleStreamOptions",
    "StreamFn",
    "TextContent",
    "TextDeltaEvent",
    "Tool",
    "ToolCallContent",
    "ToolCallDeltaEvent",
    "ToolContent",
    "ToolExecute",
    "ToolExecutionContext",
    "ToolResult",
    "ToolResultMessage",
    "UserMessage",
    "stream_simple",
    "validate_tool_arguments",
]
