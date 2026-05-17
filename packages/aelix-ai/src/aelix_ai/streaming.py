"""Streaming primitives and Model description.

Phase 1.4 dispatch shell: ``stream_simple`` resolves ``model.api`` against the
provider registry (:mod:`aelix_ai.api_registry`) and delegates. With no
provider registered (Phase 1.4 default state) it raises
:class:`StreamSimpleError`. Agent loop callers (tests and examples) pass a
mock ``stream_fn`` explicitly. Real provider adapters arrive in Phase 4 under
``aelix_ai.providers``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from aelix_ai.messages import AssistantMessage, Message


@dataclass(frozen=True)
class Cost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(frozen=True)
class Model:
    """Pi-style provider-agnostic model description.

    Note: ``api`` is a runtime string, not a static generic parameter (see
    F-8 in ``ExtensionContext.model`` docstring). Use ``match model.api:`` for
    narrowing.
    """

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

    Distinct from ``AgentContext`` (in ``aelix_agent_core.types``) which carries
    ``AgentMessage`` history before ``convert_to_llm`` filters it.
    """

    system_prompt: str = ""
    messages: list[Message] = field(default_factory=list)
    # Tool objects, kept as ``Any`` here to avoid an import cycle with
    # ``aelix_ai.tools``. Callers pass ``list[Tool]``.
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


# === stream_simple dispatch shell (ADR-0038, Phase 1.4) ===


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


class StreamSimpleError(Exception):
    """Raised by :func:`stream_simple` when no provider matches ``model.api``.

    Codes (Phase 1.4):
        - ``"no_provider_registered"``: ``model.api`` has no registered provider.

    Phase 4 adapters (anthropic/openai/openrouter) will populate the registry
    via :func:`aelix_ai.api_registry.register_provider`; until then this error
    is the documented UX.
    """

    def __init__(self, code: Literal["no_provider_registered"], message: str) -> None:
        super().__init__(message)
        self.code = code


async def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Dispatch shell — Pi parity (``stream.ts:42-46``, SHA ``734e08e…``).

    Resolves ``model.api`` against the provider registry and returns the
    provider's async iterator. Real adapter bodies (Anthropic, OpenAI,
    OpenRouter) arrive in Phase 4 under ``aelix_ai.providers``; for tests and
    demos, pass an explicit ``stream_fn`` into the agent loop instead.

    Pi semantic parity: Pi ``streamSimple`` is a synchronous ``function`` that
    calls ``resolveApiProvider`` eagerly and throws before any streaming
    begins. To match that semantic in Python, this is a plain ``async def``
    (not an async generator) that resolves the provider **at call-time** and
    returns the provider's iterator. Callers therefore see
    :class:`StreamSimpleError` raised on ``await stream_simple(...)``, not on
    the first ``__anext__`` of the iterator.

    Usage::

        it = await stream_simple(model, context, options)
        async for event in it:
            ...

    Raises:
        StreamSimpleError: ``("no_provider_registered", ...)`` if ``model.api``
            has no registered provider. Raised eagerly at call-time.
    """

    from aelix_ai.api_registry import _resolve_provider

    provider = _resolve_provider(model.api)
    opts = options if options is not None else SimpleStreamOptions()
    return provider(model, context, opts)


__all__ = [
    "AssistantEndEvent",
    "AssistantMessageEvent",
    "AssistantStartEvent",
    "Context",
    "Cost",
    "Model",
    "SimpleStreamOptions",
    "StreamFn",
    "StreamSimpleError",
    "TextDeltaEvent",
    "ToolCallDeltaEvent",
    "stream_simple",
]
