"""Message and content block types.

Mirrors the pi-ai message model: ``user``, ``assistant``, and ``toolResult``
are the only shapes the LLM itself understands. Higher layers (agent loop,
harness) can extend ``AgentMessage`` with custom types and filter/convert them
before each LLM call (see ADR-0008 for the runtime layering).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class TextContent:
    text: str = ""
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ImageContent:
    source: str = ""  # data URL, base64 payload, or external URL
    type: Literal["image"] = "image"


@dataclass(frozen=True)
class ToolCallContent:
    tool_call_id: str = ""
    tool_name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    type: Literal["toolCall"] = "toolCall"


# Any content block that may appear inside a message body.
ContentBlock = TextContent | ImageContent | ToolCallContent


@dataclass(frozen=True)
class UserMessage:
    content: list[TextContent | ImageContent] = field(default_factory=list)
    timestamp: float | None = None
    role: Literal["user"] = "user"


@dataclass(frozen=True)
class AssistantMessage:
    content: list[TextContent | ToolCallContent] = field(default_factory=list)
    # "end_turn" | "tool_use" | "error" | "aborted" | ...
    stop_reason: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] | None = None
    timestamp: float | None = None
    role: Literal["assistant"] = "assistant"


@dataclass(frozen=True)
class ToolResultMessage:
    tool_call_id: str = ""
    content: list[TextContent | ImageContent] = field(default_factory=list)
    is_error: bool = False
    timestamp: float | None = None
    role: Literal["toolResult"] = "toolResult"


# Anything the LLM can directly consume.
Message = UserMessage | AssistantMessage | ToolResultMessage


__all__ = [
    "AssistantMessage",
    "ContentBlock",
    "ImageContent",
    "Message",
    "TextContent",
    "ToolCallContent",
    "ToolResultMessage",
    "UserMessage",
]
