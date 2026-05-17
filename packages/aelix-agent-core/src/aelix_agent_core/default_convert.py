"""Default ``convert_to_llm``: passes through standard LLM message types.

In Phase 1.1 ``AgentMessage == Message``, so this is the identity filter.
Later phases that widen ``AgentMessage`` with custom types will rely on the
``isinstance`` check below to drop UI-only entries before each LLM call,
matching pi-agent-core's ``defaultConvertToLlm`` semantics.
"""

from __future__ import annotations

from aelix_ai.messages import (
    AssistantMessage,
    Message,
    ToolResultMessage,
    UserMessage,
)

from aelix_agent_core.types import AgentMessage


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    return [
        m
        for m in messages
        if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
    ]


__all__ = ["default_convert_to_llm"]
