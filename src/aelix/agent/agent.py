"""Stateful :class:`Agent` wrapper around the low-level loop.

Mirrors pi-agent-core's ``Agent`` class: holds a mutable :class:`AgentState`,
exposes ``prompt`` / ``steer`` / ``follow_up`` / ``subscribe`` / ``abort``,
and feeds the loop with steering/follow-up queues drained at turn boundaries.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aelix.agent.default_convert import default_convert_to_llm
from aelix.agent.loop import agent_loop
from aelix.agent.types import (
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentState,
    QueueMode,
)
from aelix.ai.messages import TextContent, UserMessage
from aelix.ai.streaming import StreamFn

_log = logging.getLogger(__name__)

AgentListener = Callable[[AgentEvent], Awaitable[None] | None]


@dataclass
class AgentOptions:
    initial_state: AgentState | None = None
    convert_to_llm: Callable[..., Any] | None = None
    transform_context: Callable[..., Any] | None = None
    stream_fn: StreamFn | None = None
    get_api_key: Callable[[str], Any] | None = None
    before_tool_call: Callable[..., Any] | None = None
    after_tool_call: Callable[..., Any] | None = None
    prepare_next_turn: Callable[..., Any] | None = None
    should_stop_after_turn: Callable[..., Any] | None = None
    steering_mode: QueueMode = "one-at-a-time"
    follow_up_mode: QueueMode = "one-at-a-time"


class _MessageQueue:
    """Queue draining in ``all`` or ``one-at-a-time`` mode (pi-agent-core)."""

    def __init__(self, mode: QueueMode) -> None:
        self.mode: QueueMode = mode
        self._messages: list[AgentMessage] = []

    def enqueue(self, message: AgentMessage) -> None:
        self._messages.append(message)

    def has_items(self) -> bool:
        return bool(self._messages)

    def drain(self) -> list[AgentMessage]:
        if self.mode == "all":
            drained, self._messages = self._messages, []
            return drained
        if not self._messages:
            return []
        first = self._messages[0]
        self._messages = self._messages[1:]
        return [first]

    def clear(self) -> None:
        self._messages = []


class Agent:
    """Stateful agent — pi-agent-core's ``Agent`` ported to Python."""

    def __init__(self, options: AgentOptions | None = None) -> None:
        self._options = options or AgentOptions()
        self._state = self._options.initial_state or AgentState()
        self._listeners: list[AgentListener] = []
        self._steering_queue = _MessageQueue(self._options.steering_mode)
        self._follow_up_queue = _MessageQueue(self._options.follow_up_mode)
        self._is_streaming = False

    # === Public properties ===

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def is_streaming(self) -> bool:
        return self._is_streaming

    @property
    def messages(self) -> list[AgentMessage]:
        return self._state.messages

    # === Subscription ===

    def subscribe(self, listener: AgentListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe

    # === Driving the loop ===

    async def prompt(self, text: str) -> list[AgentMessage]:
        if self._is_streaming:
            raise RuntimeError(
                "Agent is busy. Use steer()/follow_up() while streaming."
            )
        user_msg = UserMessage(content=[TextContent(text=text)])
        return await self._run([user_msg])

    async def steer(self, text: str) -> None:
        self._steering_queue.enqueue(
            UserMessage(content=[TextContent(text=text)])
        )

    async def follow_up(self, text: str) -> None:
        self._follow_up_queue.enqueue(
            UserMessage(content=[TextContent(text=text)])
        )

    # === Internal run ===

    async def _run(self, prompts: list[AgentMessage]) -> list[AgentMessage]:
        self._is_streaming = True
        try:
            config = AgentLoopConfig(
                model=self._state.model,
                convert_to_llm=(
                    self._options.convert_to_llm or default_convert_to_llm
                ),
                transform_context=self._options.transform_context,
                get_api_key=self._options.get_api_key,
                get_steering_messages=self._drain_steering,
                get_follow_up_messages=self._drain_follow_up,
                before_tool_call=self._options.before_tool_call,
                after_tool_call=self._options.after_tool_call,
                prepare_next_turn=self._options.prepare_next_turn,
                should_stop_after_turn=self._options.should_stop_after_turn,
            )
            context = AgentContext(
                system_prompt=self._state.system_prompt,
                messages=list(self._state.messages),
                tools=list(self._state.tools),
            )

            async def emit(event: AgentEvent) -> None:
                # Snapshot the listener list so removals during emit are safe.
                for listener in list(self._listeners):
                    try:
                        result = listener(event)
                        if inspect.isawaitable(result):
                            await result
                    except Exception:
                        # Listener failures must never break the loop.
                        _log.debug("listener raised", exc_info=True)

            new_messages = await agent_loop(
                prompts,
                context,
                config,
                emit=emit,
                stream_fn=self._options.stream_fn,
            )
            self._state.messages.extend(new_messages)
            return new_messages
        finally:
            self._is_streaming = False

    async def _drain_steering(self) -> list[AgentMessage]:
        return self._steering_queue.drain()

    async def _drain_follow_up(self) -> list[AgentMessage]:
        return self._follow_up_queue.drain()


__all__ = ["Agent", "AgentOptions"]
