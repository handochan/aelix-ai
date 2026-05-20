"""Runtime data model for the Aelix agent loop.

Mirrors the pi-agent-core ``types.ts`` declarations. ``AgentMessage`` is the
same as :data:`aelix_ai.messages.Message` in Phase 1.1; later phases can widen
this union with custom message types (UI notifications, system entries, etc.)
that are filtered out by ``convert_to_llm`` before each LLM call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
)
from aelix_ai.streaming import AssistantMessageEvent, Model
from aelix_ai.tools import Tool, ToolResult

# Custom message types extend this union in later phases.
AgentMessage = Message


# F-11: single source of truth for the convert_to_llm callable shape.
ConvertToLlmFn = Callable[
    [list[AgentMessage]],
    Awaitable[list[Message]] | list[Message],
]


ToolExecutionMode = Literal["parallel", "sequential"]
QueueMode = Literal["all", "one-at-a-time"]


@dataclass(frozen=True)
class AgentTool(Tool):
    """Tool definition with an optional per-tool execution mode.

    When any tool call in a batch targets a tool with
    ``execution_mode == "sequential"``, the whole batch executes sequentially
    regardless of the loop's global setting (matches pi-agent-core).
    """

    execution_mode: ToolExecutionMode | None = None


@dataclass(frozen=True)
class AgentContext:
    """The context the loop carries across turns.

    The ``messages`` list is intentionally mutable: the loop appends streaming
    partials and tool results during a turn (same shape as pi-agent-core's
    ``currentContext.messages.push(...)``).
    """

    system_prompt: str = ""
    messages: list[AgentMessage] = field(default_factory=list)
    tools: list[AgentTool] = field(default_factory=list)


@dataclass
class AgentState:
    """Mutable state owned by an :class:`Agent` instance."""

    system_prompt: str = ""
    model: Model = field(default_factory=Model)
    tools: list[AgentTool] = field(default_factory=list)
    # F-9: per-call active filter. None = all tools active. Filter never drops
    # entries from ``tools``; ``set_active_tools`` is non-destructive.
    active_tool_names: list[str] | None = None
    messages: list[AgentMessage] = field(default_factory=list)
    thinking_level: str = "off"
    # Sprint 3b additions — Pi parity (types.ts AgentHarnessOptions resources /
    # stream_options). Mutated by `set_resources` (emits ResourcesUpdate) and
    # `set_stream_options` (no emit), respectively.
    resources: dict[str, Any] = field(default_factory=dict)
    stream_options: dict[str, Any] = field(default_factory=dict)
    # Sprint 4a — Pi parity (state.session_id mirrors Pi
    # ``agent-harness.ts`` ``this.session.getMetadata().id`` snapshot).
    # Populated by ``AgentHarness.__init__`` when an external Session is
    # attached. ``None`` when no session is wired (Aelix-additive
    # backward-compat fallback per ADR-0022).
    session_id: str | None = None
    # Sprint 6h₂ (P-248): mode setters persist into AgentState in
    # addition to mutating the queue instances. The QueueMode literal
    # values match Pi ``rpc-types.ts:90-103``.
    steering_mode: str = "one-at-a-time"
    follow_up_mode: str = "one-at-a-time"
    # Sprint 6h₂ (P-249): auto-mode flags. Pi default is ``True`` per
    # fixture ``pi_rpc_mode_734e08e.json:rpc_session_state_shape``.
    auto_compaction_enabled: bool = True
    auto_retry_enabled: bool = True
    # Sprint 6h₂ (P-250): best-effort cancellation flags. ``abort_retry``
    # and ``abort_bash`` set these so a future retry-loop port and the
    # Sprint 5b bash tool can poll the intent.
    retry_aborted: bool = False
    bash_aborted: bool = False


# === Hook contexts and results ===


@dataclass(frozen=True)
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCallContent
    args: dict[str, Any]
    context: AgentContext


@dataclass(frozen=True)
class BeforeToolCallResult:
    block: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class AfterToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCallContent
    args: dict[str, Any]
    result: ToolResult
    is_error: bool
    context: AgentContext


@dataclass(frozen=True)
class AfterToolCallResult:
    content: list[TextContent | ImageContent] | None = None
    details: Any | None = None
    is_error: bool | None = None
    terminate: bool | None = None


@dataclass(frozen=True)
class ShouldStopAfterTurnContext:
    message: AssistantMessage
    tool_results: list[ToolResultMessage]
    context: AgentContext
    new_messages: list[AgentMessage]


@dataclass(frozen=True)
class AgentLoopTurnUpdate:
    context: AgentContext | None = None
    model: Model | None = None
    thinking_level: str | None = None


# === Agent event union ===


@dataclass(frozen=True)
class AgentStartEvent:
    type: Literal["agent_start"] = "agent_start"


@dataclass(frozen=True)
class TurnStartEvent:
    type: Literal["turn_start"] = "turn_start"


@dataclass(frozen=True)
class MessageStartEvent:
    message: AgentMessage
    type: Literal["message_start"] = "message_start"


@dataclass(frozen=True)
class MessageUpdateEvent:
    message: AgentMessage
    assistant_message_event: AssistantMessageEvent
    type: Literal["message_update"] = "message_update"


@dataclass(frozen=True)
class MessageEndEvent:
    message: AgentMessage
    type: Literal["message_end"] = "message_end"


@dataclass(frozen=True)
class ToolExecutionStartEvent:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    type: Literal["tool_execution_start"] = "tool_execution_start"


@dataclass(frozen=True)
class ToolExecutionUpdateEvent:
    tool_call_id: str
    partial_result: ToolResult
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    type: Literal["tool_execution_update"] = "tool_execution_update"


@dataclass(frozen=True)
class ToolExecutionEndEvent:
    tool_call_id: str
    result: ToolResult
    tool_name: str = ""
    is_error: bool = False
    type: Literal["tool_execution_end"] = "tool_execution_end"


@dataclass(frozen=True)
class TurnEndEvent:
    message: AssistantMessage
    tool_results: list[ToolResultMessage]
    type: Literal["turn_end"] = "turn_end"


@dataclass(frozen=True)
class AgentEndEvent:
    messages: list[AgentMessage]
    type: Literal["agent_end"] = "agent_end"


AgentEvent = (
    AgentStartEvent
    | TurnStartEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
    | TurnEndEvent
    | AgentEndEvent
)


# === Loop configuration ===


@dataclass
class AgentLoopConfig:
    """Configuration consumed by the low-level agent loop.

    ``frozen=False`` because pi-agent-core mutates ``config.model`` between
    turns (see ``prepare_next_turn``).
    """

    model: Model
    convert_to_llm: ConvertToLlmFn
    transform_context: (
        Callable[[list[AgentMessage], Any], Awaitable[list[AgentMessage]]] | None
    ) = None
    api_key: str | None = None
    get_api_key: Callable[[str], Awaitable[str | None] | str | None] | None = None
    get_steering_messages: Callable[[], Awaitable[list[AgentMessage]] | list[AgentMessage]] | None = None
    get_follow_up_messages: Callable[[], Awaitable[list[AgentMessage]] | list[AgentMessage]] | None = None
    before_tool_call: (
        Callable[[BeforeToolCallContext], Awaitable[BeforeToolCallResult | None] | BeforeToolCallResult | None] | None
    ) = None
    after_tool_call: (
        Callable[[AfterToolCallContext], Awaitable[AfterToolCallResult | None] | AfterToolCallResult | None] | None
    ) = None
    prepare_next_turn: (
        Callable[[ShouldStopAfterTurnContext], Awaitable[AgentLoopTurnUpdate | None] | AgentLoopTurnUpdate | None] | None
    ) = None
    should_stop_after_turn: (
        Callable[[ShouldStopAfterTurnContext], Awaitable[bool] | bool] | None
    ) = None
    # Sprint 3c (Phase 2.1.3): Pi parity default flipped from "sequential" to
    # "parallel". Pi `types.ts:226-232` documents JSDoc default = "parallel".
    # Callers that need the legacy sequential behaviour must pass it explicitly.
    tool_execution: ToolExecutionMode = "parallel"
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)


__all__ = [
    "AfterToolCallContext",
    "AfterToolCallResult",
    "AgentContext",
    "AgentEndEvent",
    "AgentEvent",
    "AgentLoopConfig",
    "AgentLoopTurnUpdate",
    "AgentMessage",
    "AgentStartEvent",
    "AgentState",
    "AgentTool",
    "BeforeToolCallContext",
    "BeforeToolCallResult",
    "ConvertToLlmFn",
    "MessageEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "QueueMode",
    "ShouldStopAfterTurnContext",
    "ToolExecutionEndEvent",
    "ToolExecutionMode",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "TurnEndEvent",
    "TurnStartEvent",
]
