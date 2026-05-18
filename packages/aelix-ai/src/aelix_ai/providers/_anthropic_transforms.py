"""Anthropic message-shape helpers — Sprint 6a (ADR-0045 §B).

Ports the relevant portions of Pi ``providers/anthropic.ts`` +
``providers/transform-messages.ts`` so the adapter body in
``providers/anthropic.py`` stays readable.

The function shapes mirror Pi's helpers:

- :func:`transform_messages` — convert Aelix ``Message`` → Anthropic SDK
  ``MessageParam`` list (user / assistant / tool_result).
- :func:`build_params` — assemble the SDK ``messages.stream`` kwargs
  (system prompt, tools, max_tokens, …).
- :func:`map_stop_reason` — Anthropic ``stop_reason`` → Aelix
  ``StopReason`` (``end_turn``/``tool_use``/``max_tokens``/``error``).
"""

from __future__ import annotations

from typing import Any

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import Model

# Anthropic ``stop_reason`` → Aelix-shaped strings. Sprint 6b W6 (P-57):
# Aelix now uses Pi's ``"toolUse"`` spelling verbatim across every adapter.
# The agent loop only compares against ``"error" | "aborted"`` so the
# spelling change is invisible to terminal-detection.
_ANTHROPIC_STOP_REASON_MAP: dict[str | None, str] = {
    "end_turn": "end_turn",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "toolUse",
    "pause_turn": "stop",
    "refusal": "error",
}


def map_stop_reason(anthropic_reason: str | None) -> str:
    """Map Anthropic SDK ``stop_reason`` to Aelix ``AssistantMessage.stop_reason``.

    Unknown reasons fall through as the raw string so callers can
    inspect them; the agent loop's terminal-detection compares against
    ``("error", "aborted")`` so a benign unknown reason continues the loop.
    """

    return _ANTHROPIC_STOP_REASON_MAP.get(anthropic_reason, anthropic_reason or "stop")


def _content_blocks_to_anthropic(
    blocks: list[Any],
) -> list[dict[str, Any]]:
    """Convert Aelix content blocks → Anthropic SDK content blocks."""

    out: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextContent):
            out.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            # Anthropic expects {type:image, source:{type, media_type, data}}.
            # Sprint 6b (P-61): prefer the new ``mime_type`` + ``data``
            # split fields; fall back to the legacy ``source`` data-URL /
            # base64 string when ``data`` is empty so pre-6b callers keep
            # working.
            mime = block.mime_type or "image/png"
            data = block.data if block.data else block.source
            out.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": data,
                    },
                }
            )
        elif isinstance(block, ToolCallContent):
            out.append(
                {
                    "type": "tool_use",
                    "id": block.tool_call_id,
                    "name": block.tool_name,
                    "input": block.input,
                }
            )
    return out


def transform_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert Aelix ``Message`` list → Anthropic SDK ``MessageParam`` list.

    Pi parity: ``providers/transform-messages.ts``. Tool results are
    grouped into the most recent ``user`` message (Anthropic requires
    tool_result blocks inside a user message) per Pi's behavior.
    """

    out: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            out.append(
                {
                    "role": "user",
                    "content": _content_blocks_to_anthropic(list(msg.content)),
                }
            )
        elif isinstance(msg, AssistantMessage):
            out.append(
                {
                    "role": "assistant",
                    "content": _content_blocks_to_anthropic(list(msg.content)),
                }
            )
        elif isinstance(msg, ToolResultMessage):
            # Anthropic expects tool_result blocks inside a user message.
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": _content_blocks_to_anthropic(list(msg.content)),
                "is_error": msg.is_error,
            }
            # Coalesce into preceding user message when possible (Pi parity).
            if out and out[-1]["role"] == "user" and isinstance(
                out[-1]["content"], list
            ):
                out[-1]["content"].append(tool_result_block)
            else:
                out.append(
                    {
                        "role": "user",
                        "content": [tool_result_block],
                    }
                )
    return out


def build_params(
    model: Model,
    system_prompt: str,
    messages: list[Message],
    tools: list[Any],
    *,
    max_tokens: int = 4096,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble ``messages.create`` kwargs for the Anthropic SDK.

    Pi parity: ``providers/anthropic.ts:489-505``-region ``buildParams``.

    ``extra`` lets callers inject ``cache_retention`` / ``thinking`` /
    other provider-specific top-level kwargs without forking this helper.
    """

    params: dict[str, Any] = {
        "model": model.id or model.name,
        "max_tokens": max_tokens,
        "messages": transform_messages(messages),
    }
    if system_prompt:
        params["system"] = system_prompt
    if tools:
        sdk_tools: list[dict[str, Any]] = []
        for tool in tools:
            if hasattr(tool, "to_anthropic_param"):
                sdk_tools.append(tool.to_anthropic_param())
            else:
                # Generic ``Tool``-shaped object — best effort.
                schema = getattr(tool, "input_schema", None) or getattr(
                    tool, "parameters", {}
                )
                sdk_tools.append(
                    {
                        "name": getattr(tool, "name", "unknown"),
                        "description": getattr(tool, "description", ""),
                        "input_schema": schema or {"type": "object", "properties": {}},
                    }
                )
        params["tools"] = sdk_tools
    if extra:
        params.update(extra)
    return params


def is_oauth_token(api_key: str | None) -> bool:
    """Pi parity ``providers/anthropic.ts:769`` — detect Anthropic OAuth bearer.

    Anthropic OAuth tokens (issued via the claude.ai login flow) all
    start with ``sk-ant-oat``. The SDK accepts them as API keys but the
    full OAuth flow (token refresh, etc.) is owned by Sprint 6c — Sprint
    6a rejects OAuth tokens with ``AgentHarnessError("auth", ...)``.
    """

    return bool(api_key and api_key.startswith("sk-ant-oat"))


__all__ = [
    "build_params",
    "is_oauth_token",
    "map_stop_reason",
    "transform_messages",
]
