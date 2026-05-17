"""Aelix AI primitives — messages, tools, streaming.

Mirrors the pi-ai package shape so the agent loop stays provider-agnostic and
tests can supply a mock ``stream_fn`` (Phase 1.1) before real provider adapters
land in Phase 2 under ``aelix_ai.providers``.
"""

from aelix_ai.api_registry import (
    clear_providers,
    get_registered_providers,
    register_provider,
    unregister_provider,
)
from aelix_ai.messages import (
    AssistantMessage,
    ContentBlock,
    ImageContent,
    Message,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Cost,
    Model,
    SimpleStreamOptions,
    StreamFn,
    StreamSimpleError,
    TextDeltaEvent,
    ToolCallDeltaEvent,
    stream_simple,
)
from aelix_ai.tools import (
    Tool,
    ToolContent,
    ToolExecute,
    ToolExecutionContext,
    ToolPartialCallback,
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
    "StreamSimpleError",
    "TextContent",
    "TextDeltaEvent",
    "Tool",
    "ToolCallContent",
    "ToolCallDeltaEvent",
    "ToolContent",
    "ToolExecute",
    "ToolExecutionContext",
    "ToolPartialCallback",
    "ToolResult",
    "ToolResultMessage",
    "UserMessage",
    "clear_providers",
    "get_registered_providers",
    "register_provider",
    "stream_simple",
    "unregister_provider",
    "validate_tool_arguments",
]
