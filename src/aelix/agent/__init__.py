"""Aelix agent runtime — pi-agent-core ported to Python.

Layering (mirrors pi-agent-core):
- :mod:`aelix.agent.types` declares the runtime data model and event union.
- :mod:`aelix.agent.loop` runs the low-level agent loop.
- :mod:`aelix.agent.agent` wraps the loop in a stateful :class:`Agent` class.
- :mod:`aelix.agent.default_convert` provides the default ``convert_to_llm``.
"""

from aelix.agent.agent import Agent, AgentOptions
from aelix.agent.default_convert import default_convert_to_llm
from aelix.agent.loop import AgentEventSink, agent_loop, agent_loop_continue
from aelix.agent.types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentStartEvent,
    AgentState,
    AgentTool,
    BeforeToolCallContext,
    BeforeToolCallResult,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    QueueMode,
    ShouldStopAfterTurnContext,
    ToolExecutionEndEvent,
    ToolExecutionMode,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)

__all__ = [
    "AfterToolCallContext",
    "AfterToolCallResult",
    "Agent",
    "AgentContext",
    "AgentEndEvent",
    "AgentEvent",
    "AgentEventSink",
    "AgentLoopConfig",
    "AgentLoopTurnUpdate",
    "AgentMessage",
    "AgentOptions",
    "AgentStartEvent",
    "AgentState",
    "AgentTool",
    "BeforeToolCallContext",
    "BeforeToolCallResult",
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
    "agent_loop",
    "agent_loop_continue",
    "default_convert_to_llm",
]
