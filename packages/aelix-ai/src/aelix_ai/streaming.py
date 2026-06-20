"""Streaming primitives and Model description.

Phase 4 (Sprint 6a, ADR-0037 Accepted): the 12-variant ``AssistantMessageEvent``
union now mirrors Pi (``packages/ai/src/types.ts`` at SHA ``734e08e…``)
verbatim. See ADR-0037 for the full mapping.

``stream_simple`` resolves ``model.api`` against the provider registry
(:mod:`aelix_ai.api_registry`) and delegates to the registered provider
implementation. With no provider registered it raises
:class:`StreamSimpleError`. Real provider adapters live under
``aelix_ai.providers`` (Sprint 6a ships Anthropic; OpenAI/OpenRouter land
Sprint 6b/6c).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from aelix_ai.messages import AssistantMessage, Message, ToolCallContent


@dataclass(frozen=True)
class ModelCost:
    """Pi parity: ``ai/src/types.ts::Model.cost`` (Sprint 6f — ADR-0064).

    Costs are per **million tokens** (matches Pi's ``cost.X / 1_000_000``
    formula in :func:`aelix_ai.models.calculate_cost`). Sprint 6f renamed
    the class from ``Cost`` to ``ModelCost`` to match Pi's nominal type
    ``Model.cost`` (Pi ``models.ts:39-46``); the original ``Cost`` alias
    is preserved below for back-compat with Sprint 6a/6b code.
    """

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


# Sprint 6f back-compat alias — Sprint 6a/6b callers imported ``Cost``.
Cost = ModelCost


@dataclass
class UsageCost:
    """Pi parity: ``ai/src/types.ts::Usage.cost`` — per-call resolved cost.

    Sprint 6f adds this distinct from :class:`ModelCost`: :class:`ModelCost`
    holds the **per-million** rate stored on :class:`Model`, while
    :class:`UsageCost` holds the **resolved** cost computed by
    :func:`aelix_ai.models.calculate_cost`. Fields are MUTABLE so the
    calculate_cost helper can mutate in-place matching Pi behavior
    (Pi assigns to ``usage.cost.X`` directly).
    """

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass
class Usage:
    """Pi parity: ``ai/src/types.ts::Usage`` (Sprint 6f minimal port).

    Carries per-call token counts + the resolved :class:`UsageCost`. Sprint
    6f adds the minimal shape required by :func:`aelix_ai.models.calculate_cost`;
    Sprint 6g+ adapters can extend this with provider-specific fields.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: UsageCost = field(default_factory=UsageCost)


# Sprint 6g₁ (ADR-0067 P-199): Pi parity 32-provider KnownProvider Literal
# union from Pi ``ai/src/types.ts``. Used by ``defaultModelPerProvider``
# typing and Sprint 6f₁ ``Model.provider: str`` (the Literal narrows
# without breaking existing string-typed callers).
#
# Sprint 6g₂ W6 P-208 MAJOR fix: reordered verbatim from Pi
# ``packages/ai/src/types.ts:23-55`` at SHA 734e08e. The earlier Sprint
# 6g₁ port shipped the 32 strings in alphabetical order; Pi groups them
# semantically (first-party → OpenAI family → community providers →
# self-hosted → Xiaomi family). Closure pin
# ``test_known_provider_literal_order_matches_pi_types_ts`` locks the
# order against future drift.
KnownProvider = Literal[
    "amazon-bedrock",
    "anthropic",
    "google",
    "google-vertex",
    "openai",
    "azure-openai-responses",
    "openai-codex",
    "deepseek",
    "github-copilot",
    "xai",
    "groq",
    "cerebras",
    "openrouter",
    "vercel-ai-gateway",
    "zai",
    "mistral",
    "minimax",
    "minimax-cn",
    "moonshotai",
    "moonshotai-cn",
    "huggingface",
    "fireworks",
    "together",
    "opencode",
    "opencode-go",
    "kimi-coding",
    "cloudflare-workers-ai",
    "cloudflare-ai-gateway",
    "xiaomi",
    "xiaomi-token-plan-cn",
    "xiaomi-token-plan-ams",
    "xiaomi-token-plan-sgp",
]


@dataclass(frozen=True)
class Model:
    """Pi-style provider-agnostic model description.

    Note: ``api`` is a runtime string, not a static generic parameter (see
    F-8 in ``ExtensionContext.model`` docstring). Use ``match model.api:`` for
    narrowing.

    Sprint 6f (ADR-0064): added :attr:`thinking_level_map`. The existing
    ``cost`` / ``context_window`` / ``max_tokens`` fields already existed in
    Sprint 6b; Sprint 6f formalizes them against Pi ``Model`` shape.
    """

    id: str = "unknown"
    name: str = "unknown"
    api: str = "unknown"
    provider: str = "unknown"
    base_url: str = ""
    reasoning: bool = False
    input: list[str] = field(default_factory=list)  # e.g. ["text", "image"]
    cost: ModelCost = field(default_factory=ModelCost)
    context_window: int = 0
    max_tokens: int = 0
    # Sprint 6f (ADR-0064): per-level thinking budget map. Pi
    # ``Model.thinkingLevelMap?: { [level: string]: string | number | null }``.
    # ``None`` value at a level means "NOT supported"; absent key means
    # "no mapping" (falls back to forward/backward clamp scan in
    # :func:`aelix_ai.models.clamp_thinking_level`). ADDITIVE default
    # :data:`None` preserves Sprint 6a/6b/6c/6d/6e behavior.
    thinking_level_map: dict[str, str | int | None] | None = None
    # Sprint 6f W6 (P-178): per-model HTTP header overrides. Pi
    # ``Model.headers?: Record<string, string>`` (types.ts:549). Used by
    # provider adapters to inject API-specific headers (e.g., Copilot
    # ``Editor-Version``). ADDITIVE default :data:`None` preserves
    # Sprint 6a/6b/6c/6d/6e behavior. Wire-serialized by
    # :func:`aelix_coding_agent.rpc.rpc_mode._model_to_dict` when non-None.
    headers: dict[str, str] | None = None
    # Pi parity: types.ts Model.compat per-API discriminated union; Sprint 6g₁ passthrough as dict[str, Any] (Sprint 6g₂ types it)
    # Sprint 6g₁ (ADR-0067 P-200): per-API compat overrides. Pi
    # ``Model.compat?: OpenAICompletionsCompat | …`` discriminated union;
    # Sprint 6g₁ ships as ``dict[str, Any] | None`` passthrough. Sprint
    # 6g₂ migrates to a typed union when the OpenAICodexResponsesCompat
    # etc. dataclasses land. ADDITIVE default :data:`None` preserves
    # Sprint 6a/6b/6c/6d/6e/6f behavior.
    compat: dict[str, Any] | None = None


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
# Pi parity: ``packages/ai/src/types.ts:347-359`` (SHA 734e08e).
# 12-variant union — ADR-0037 Accepted (Sprint 6a / Phase 4.1).


@dataclass(frozen=True)
class AssistantStartEvent:
    """Pi ``{ type: "start"; partial: AssistantMessage }``."""

    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["start"] = "start"


@dataclass(frozen=True)
class TextStartEvent:
    """Pi ``{ type: "text_start"; contentIndex; partial }``."""

    content_index: int = 0
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["text_start"] = "text_start"


@dataclass(frozen=True)
class TextDeltaEvent:
    """Pi ``{ type: "text_delta"; contentIndex; delta; partial }``.

    Sprint 6a (P-39): ``content_index`` + ``partial`` backfilled with
    defaults so legacy callers keep working.
    """

    delta: str = ""
    content_index: int = 0
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["text_delta"] = "text_delta"


@dataclass(frozen=True)
class TextEndEvent:
    """Pi ``{ type: "text_end"; contentIndex; content; partial }``."""

    content_index: int = 0
    content: str = ""
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["text_end"] = "text_end"


@dataclass(frozen=True)
class ThinkingStartEvent:
    """Pi ``{ type: "thinking_start"; contentIndex; partial }``."""

    content_index: int = 0
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["thinking_start"] = "thinking_start"


@dataclass(frozen=True)
class ThinkingDeltaEvent:
    """Pi ``{ type: "thinking_delta"; contentIndex; delta; partial }``."""

    delta: str = ""
    content_index: int = 0
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["thinking_delta"] = "thinking_delta"


@dataclass(frozen=True)
class ThinkingEndEvent:
    """Pi ``{ type: "thinking_end"; contentIndex; content; partial }``."""

    content_index: int = 0
    content: str = ""
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["thinking_end"] = "thinking_end"


@dataclass(frozen=True)
class ToolCallStartEvent:
    """Pi ``{ type: "toolcall_start"; contentIndex; partial }``."""

    content_index: int = 0
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["toolcall_start"] = "toolcall_start"


@dataclass(frozen=True)
class ToolCallDeltaEvent:
    """Pi ``{ type: "toolcall_delta"; contentIndex; delta; partial }``.

    Sprint 6a (P-39d SILENT DRIFT FIX): ``type`` Literal is ``"toolcall_delta"``
    (no underscore between ``tool`` and ``call``) to match Pi exactly.

    Sprint 6a (P-39): ``content_index`` + ``partial`` backfilled with
    defaults. Legacy ``tool_call_id`` / ``tool_name`` / ``input_delta``
    accessors preserved as deprecated properties so existing tests keep
    parsing. Pi's wire shape uses ``delta`` for the raw JSON chunk; we
    keep the bare ``delta`` field as canonical and expose ``input_delta``
    as a read-only alias.
    """

    delta: str = ""  # raw JSON chunk (Pi field name)
    content_index: int = 0
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    tool_call_id: str = ""  # legacy — Pi does not carry this on the delta
    tool_name: str = ""  # legacy — Pi does not carry this on the delta
    type: Literal["toolcall_delta"] = "toolcall_delta"

    @property
    def input_delta(self) -> str:
        """Deprecated alias for :attr:`delta` (Sprint 1–5 spelling)."""

        return self.delta


@dataclass(frozen=True)
class ToolCallEndEvent:
    """Pi ``{ type: "toolcall_end"; contentIndex; toolCall; partial }``."""

    content_index: int = 0
    tool_call: ToolCallContent = field(default_factory=ToolCallContent)
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["toolcall_end"] = "toolcall_end"


@dataclass(frozen=True)
class AssistantDoneEvent:
    """Pi ``{ type: "done"; reason; message }``.

    ``reason`` is one of Pi's three terminal-success stop reasons
    (``"stop"`` | ``"length"`` | ``"toolUse"``). The matching Pi
    ``StopReason`` enum value ``"toolUse"`` is preserved verbatim for
    byte-parity with provider adapters; the legacy Aelix ``"tool_use"``
    spelling continues to flow through :attr:`AssistantMessage.stop_reason`
    because the agent loop never inspects this ``reason`` field directly —
    it reads ``message.stop_reason`` exclusively.
    """

    reason: Literal["stop", "length", "toolUse"] = "stop"
    message: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["done"] = "done"


@dataclass(frozen=True)
class AssistantErrorEvent:
    """Pi ``{ type: "error"; reason; error }``.

    Sprint 6a addition (ADR-0037): emits when the provider stream fails
    or is aborted. ``reason`` is one of Pi's two terminal-failure stop
    reasons (``"aborted"`` | ``"error"``).
    """

    reason: Literal["aborted", "error"] = "error"
    error: AssistantMessage = field(default_factory=AssistantMessage)
    error_message: str | None = None
    type: Literal["error"] = "error"


@dataclass(frozen=True)
class AssistantEndEvent(AssistantDoneEvent):
    """**DEPRECATED** — legacy alias for :class:`AssistantDoneEvent` (Pi ``done``).

    Pre-Phase-4 Aelix called this event ``end``; Pi calls it ``done``.
    Sprint 6a renames the canonical class to :class:`AssistantDoneEvent`
    (P-39a) and keeps this subclass so existing test mocks that yield
    ``AssistantEndEvent(message=...)`` keep working. The ``type`` Literal
    overrides the parent to ``"end"`` — the loop consumer accepts both
    ``"end"`` and ``"done"`` as terminal-success events.

    New code should prefer :class:`AssistantDoneEvent` directly.
    """

    type: Literal["end"] = "end"  # type: ignore[assignment]


AssistantMessageEvent = (
    AssistantStartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | AssistantDoneEvent
    | AssistantErrorEvent
    | AssistantEndEvent  # legacy (deprecated subclass of AssistantDoneEvent)
)


# === stream_simple dispatch shell (ADR-0038, Phase 1.4) ===


@dataclass(frozen=True)
class ProviderResponse:
    """Pi ``ProviderResponse`` (``packages/ai/src/types.ts``).

    Surfaced through the Sprint 6a ``on_response`` callback so the harness
    can fan out :class:`AfterProviderResponseHookEvent`.
    """

    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SimpleStreamOptions:
    """Pi ``SimpleStreamOptions`` shape (Sprint 6a expansion).

    ADR-0045: Phase 4 fills in the cache_retention / transport / timeout /
    callback fields previously stubbed in Sprint 1.4. All new fields are
    optional with sensible defaults so legacy callers (test mocks) keep
    working without modification.
    """

    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    signal: Any | None = None  # AbortSignal-shaped (Sprint 6a)

    # --- P0 #6 (compaction fidelity): per-turn output-token cap ---
    # Pi ``SimpleStreamOptions.maxTokens`` (providers/simple-options.ts). When
    # set, this overrides the model default output cap at the adapter payload
    # level (pi ``base.maxTokens = options.maxTokens ?? model.maxTokens``). The
    # compaction summarizer uses it to bound the summary length
    # (``floor(0.8 * reserveTokens)``) and the turn-prefix summary
    # (``floor(0.5 * reserveTokens)``); branch summaries cap at ``2048``.
    max_tokens: int | None = None

    # --- Sprint 6a (ADR-0045) provider-adapter extensions ---
    cache_retention: Literal["none", "short", "long"] | None = None
    transport: Literal["sse", "websocket", "websocket-cached", "auto"] | None = None
    timeout_ms: int | None = None
    max_retries: int | None = None
    max_retry_delay_ms: int | None = None
    reasoning: str | None = None
    session_id: str | None = None
    # Callbacks — used by the harness's ``_make_stream_fn`` to wire the
    # ``before_provider_payload`` and ``after_provider_response`` emit
    # sites. Adapters that don't fire them are still Pi-parity correct.
    on_payload: Callable[[Any, Model], Any] | None = None
    on_response: Callable[[ProviderResponse, Model], Any] | None = None
    # Optional pre-built SDK client (Anthropic / OpenAI / OpenRouter).
    # Pi parity: ``options.client`` in provider adapters.
    client: Any | None = None


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
    provider's async iterator. Sprint 6a (Phase 4.1) lands the Anthropic
    adapter under ``aelix_ai.providers.anthropic``; for tests and demos,
    pass an explicit ``stream_fn`` into the agent loop instead.

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
    "AssistantDoneEvent",
    "AssistantEndEvent",
    "AssistantErrorEvent",
    "AssistantMessageEvent",
    "AssistantStartEvent",
    "Context",
    "Cost",
    "KnownProvider",
    "Model",
    "ModelCost",
    "ProviderResponse",
    "Usage",
    "UsageCost",
    "SimpleStreamOptions",
    "StreamFn",
    "StreamSimpleError",
    "TextDeltaEvent",
    "TextEndEvent",
    "TextStartEvent",
    "ThinkingDeltaEvent",
    "ThinkingEndEvent",
    "ThinkingStartEvent",
    "ToolCallDeltaEvent",
    "ToolCallEndEvent",
    "ToolCallStartEvent",
    "stream_simple",
]
