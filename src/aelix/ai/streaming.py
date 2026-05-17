"""Streaming primitives and Model description.

This is a Phase 1.1 stub: ``stream_simple`` raises ``NotImplementedError``.
Agent loop callers (tests and examples) pass a mock ``stream_fn`` explicitly.
Real provider adapters arrive in Phase 2 under ``aelix.ai.providers``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from aelix.ai.messages import AssistantMessage, Message


@dataclass(frozen=True)
class Cost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(frozen=True)
class Model:
    """Pi-style provider-agnostic model description."""

    id: str = "unknown"
    name: str = "unknown"
    api: str = "unknown"
    provider: str = "unknown"
    base_url: str = ""
    reasoning: bool = False
    input: list[str] = field(default_factory=list)  # e.g. ["text", "image"]
    cost: Cost = field(default_factory=Cost)
    context_window: int = 0
    max_tokens: int = 0


@dataclass(frozen=True)
class Context:
    """LLM-level context — what the provider call actually sees.

    Distinct from ``AgentContext`` (in ``aelix.agent.types``) which carries
    ``AgentMessage`` history before ``convert_to_llm`` filters it.
    """

    system_prompt: str = ""
    messages: list[Message] = field(default_factory=list)
    # Tool objects, kept as ``Any`` here to avoid an import cycle with
    # ``aelix.ai.tools``. Callers pass ``list[Tool]``.
    tools: list[Any] = field(default_factory=list)


# === Assistant message stream events ===


@dataclass(frozen=True)
class AssistantStartEvent:
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["start"] = "start"


@dataclass(frozen=True)
class TextDeltaEvent:
    delta: str = ""
    type: Literal["text_delta"] = "text_delta"


@dataclass(frozen=True)
class ToolCallDeltaEvent:
    tool_call_id: str = ""
    tool_name: str = ""
    input_delta: str = ""  # raw JSON chunk
    type: Literal["tool_call_delta"] = "tool_call_delta"


@dataclass(frozen=True)
class AssistantEndEvent:
    message: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["end"] = "end"


AssistantMessageEvent = (
    AssistantStartEvent | TextDeltaEvent | ToolCallDeltaEvent | AssistantEndEvent
)


# === stream_simple stub ===


@dataclass(frozen=True)
class SimpleStreamOptions:
    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    signal: Any | None = None  # AbortSignal placeholder for Phase 2


# A stream function returns an async iterator of message events. In pi-ai,
# ``streamSimple`` may return either an iterable or a promise of one. We model
# the simpler case here: callers ``async for event in stream_fn(...)``.
StreamFn = Callable[
    [Model, Context, SimpleStreamOptions],
    AsyncIterator[AssistantMessageEvent],
]


async def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions,
) -> AsyncIterator[AssistantMessageEvent]:
    """Phase 1.1 stub.

    Real provider adapters (Anthropic, OpenAI, OpenRouter, ...) arrive in
    Phase 2 under ``aelix.ai.providers``. For now, callers in tests and
    examples pass an explicit mock ``stream_fn`` into the agent loop.
    """

    raise NotImplementedError(
        "stream_simple is a Phase 1.1 stub. "
        "Provide stream_fn explicitly to agent_loop()."
    )
    # Unreachable yield turns this coroutine into an async generator so the
    # return type annotation matches at runtime.
    yield AssistantStartEvent()  # pragma: no cover


__all__ = [
    "AssistantEndEvent",
    "AssistantMessageEvent",
    "AssistantStartEvent",
    "Context",
    "Cost",
    "Model",
    "SimpleStreamOptions",
    "StreamFn",
    "TextDeltaEvent",
    "ToolCallDeltaEvent",
    "stream_simple",
]
