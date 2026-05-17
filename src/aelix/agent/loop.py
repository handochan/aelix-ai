"""Low-level agent loop — pi-agent-core ``runLoop`` ported to Python.

The two public entry points are:

- :func:`agent_loop` — start a fresh loop with prompt messages.
- :func:`agent_loop_continue` — resume from an existing context (used by retry
  paths after the last message in context is a ``user`` or ``toolResult``).

Both take an ``emit`` async callback that receives every :class:`AgentEvent`,
matching pi-agent-core's ``AgentEventSink`` signature. A high-level
:class:`~aelix.agent.agent.Agent` wraps this loop with state + subscribers.

Tool execution is sequential in Phase 1.1. The ``parallel`` mode and per-tool
overrides arrive in a later phase.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

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
    BeforeToolCallContext,
    BeforeToolCallResult,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ShouldStopAfterTurnContext,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from aelix.ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
)
from aelix.ai.streaming import Context, SimpleStreamOptions, StreamFn
from aelix.ai.tools import ToolExecutionContext, ToolResult, validate_tool_arguments

AgentEventSink = Callable[[AgentEvent], Awaitable[None]]


async def agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    *,
    emit: AgentEventSink,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> list[AgentMessage]:
    """Run the loop with new prompt messages appended to ``context.messages``."""

    new_messages: list[AgentMessage] = list(prompts)
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=[*context.messages, *prompts],
        tools=list(context.tools),
    )

    await emit(AgentStartEvent())
    await emit(TurnStartEvent())
    for prompt in prompts:
        await emit(MessageStartEvent(message=prompt))
        await emit(MessageEndEvent(message=prompt))

    await _run_loop(current_context, new_messages, config, signal, emit, stream_fn)
    return new_messages


async def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    *,
    emit: AgentEventSink,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> list[AgentMessage]:
    """Resume the loop from an existing context (retry path)."""

    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")
    if isinstance(context.messages[-1], AssistantMessage):
        raise ValueError("Cannot continue from message role: assistant")

    new_messages: list[AgentMessage] = []
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=list(context.messages),
        tools=list(context.tools),
    )

    await emit(AgentStartEvent())
    await emit(TurnStartEvent())
    await _run_loop(current_context, new_messages, config, signal, emit, stream_fn)
    return new_messages


# === Internal loop ===


async def _run_loop(
    initial_context: AgentContext,
    new_messages: list[AgentMessage],
    initial_config: AgentLoopConfig,
    signal: Any,
    emit: AgentEventSink,
    stream_fn: StreamFn | None,
) -> None:
    current_context = initial_context
    config = initial_config
    first_turn = True
    pending_messages: list[AgentMessage] = await _drain_queue(config.get_steering_messages)

    while True:  # outer: follow-up drain loop
        has_more_tool_calls = True

        while has_more_tool_calls or pending_messages:
            if not first_turn:
                await emit(TurnStartEvent())
            else:
                first_turn = False

            if pending_messages:
                for msg in pending_messages:
                    await emit(MessageStartEvent(message=msg))
                    await emit(MessageEndEvent(message=msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                pending_messages = []

            message = await _stream_assistant_response(
                current_context, config, signal, emit, stream_fn
            )
            new_messages.append(message)

            if message.stop_reason in ("error", "aborted"):
                await emit(TurnEndEvent(message=message, tool_results=[]))
                await emit(AgentEndEvent(messages=new_messages))
                return

            tool_calls = [
                c for c in message.content if isinstance(c, ToolCallContent)
            ]
            tool_results: list[ToolResultMessage] = []
            has_more_tool_calls = False

            if tool_calls:
                batch = await _execute_tool_calls(
                    current_context, message, config, signal, emit
                )
                tool_results.extend(batch.messages)
                has_more_tool_calls = not batch.terminate
                for result_msg in tool_results:
                    current_context.messages.append(result_msg)
                    new_messages.append(result_msg)

            await emit(TurnEndEvent(message=message, tool_results=tool_results))

            next_ctx = ShouldStopAfterTurnContext(
                message=message,
                tool_results=tool_results,
                context=current_context,
                new_messages=new_messages,
            )

            if config.prepare_next_turn is not None:
                update = await _maybe_await(config.prepare_next_turn(next_ctx))
                if isinstance(update, AgentLoopTurnUpdate):
                    if update.context is not None:
                        current_context = update.context
                    if update.model is not None:
                        config = replace(config, model=update.model)

            if config.should_stop_after_turn is not None:
                stop = await _maybe_await(config.should_stop_after_turn(next_ctx))
                if stop:
                    await emit(AgentEndEvent(messages=new_messages))
                    return

            pending_messages = await _drain_queue(config.get_steering_messages)

        follow_up = await _drain_queue(config.get_follow_up_messages)
        if follow_up:
            pending_messages = follow_up
            continue
        break

    await emit(AgentEndEvent(messages=new_messages))


# === Streaming the assistant response ===


async def _stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Any,
    emit: AgentEventSink,
    stream_fn: StreamFn | None,
) -> AssistantMessage:
    messages = context.messages
    if config.transform_context is not None:
        messages = await config.transform_context(messages, signal)

    llm_messages = await _maybe_await(config.convert_to_llm(messages))

    llm_context = Context(
        system_prompt=context.system_prompt,
        messages=list(llm_messages),
        tools=list(context.tools),
    )

    resolved_api_key: str | None = None
    if config.get_api_key is not None:
        resolved_api_key = await _maybe_await(config.get_api_key(config.model.provider))
    if not resolved_api_key:
        resolved_api_key = config.api_key

    options = SimpleStreamOptions(
        api_key=resolved_api_key,
        headers=dict(config.headers),
        metadata=dict(config.metadata),
        signal=signal,
    )

    fn = stream_fn or _resolve_stream_simple()

    partial: AssistantMessage | None = None
    partial_index: int | None = None
    final: AssistantMessage | None = None

    async for event in fn(config.model, llm_context, options):
        if event.type == "start":
            partial = event.partial
            context.messages.append(partial)
            partial_index = len(context.messages) - 1
            await emit(MessageStartEvent(message=partial))
        elif event.type in ("text_delta", "tool_call_delta"):
            if partial is None:
                continue
            await emit(
                MessageUpdateEvent(
                    message=partial, assistant_message_event=event
                )
            )
        elif event.type == "end":
            final = event.message
            if final.timestamp is None:
                final = replace(final, timestamp=time.time())
            if partial_index is not None:
                context.messages[partial_index] = final
            elif partial is None:
                context.messages.append(final)
            await emit(MessageEndEvent(message=final))

    if final is None:
        raise RuntimeError(
            "stream_fn ended without an 'end' event. "
            "Every stream must terminate with AssistantEndEvent."
        )
    return final


# === Sequential tool execution ===


class _ExecutedBatch:
    __slots__ = ("messages", "terminate")

    def __init__(self, messages: list[ToolResultMessage], terminate: bool) -> None:
        self.messages = messages
        self.terminate = terminate


async def _execute_tool_calls(
    context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Any,
    emit: AgentEventSink,
) -> _ExecutedBatch:
    tool_calls = [
        c for c in assistant_message.content if isinstance(c, ToolCallContent)
    ]
    tool_map = {t.name: t for t in context.tools}

    result_messages: list[ToolResultMessage] = []
    all_terminate = bool(tool_calls)

    for tc in tool_calls:
        tool = tool_map.get(tc.tool_name)
        await emit(
            ToolExecutionStartEvent(
                tool_call_id=tc.tool_call_id,
                tool_name=tc.tool_name,
                args=dict(tc.input),
            )
        )

        if tool is None:
            result = ToolResult(
                content=[TextContent(text=f"Unknown tool: {tc.tool_name}")],
                is_error=True,
            )
            result_messages.append(_to_tool_result_message(tc.tool_call_id, result))
            await emit(ToolExecutionEndEvent(tool_call_id=tc.tool_call_id, result=result, tool_name=tc.tool_name, is_error=result.is_error))
            all_terminate = False
            continue

        args = await validate_tool_arguments(tool, dict(tc.input))

        if config.before_tool_call is not None:
            before_ctx = BeforeToolCallContext(
                assistant_message=assistant_message,
                tool_call=tc,
                args=args,
                context=context,
            )
            decision = await _maybe_await(config.before_tool_call(before_ctx))
            if isinstance(decision, BeforeToolCallResult) and decision.block:
                blocked = ToolResult(
                    content=[
                        TextContent(
                            text=decision.reason
                            or "Blocked by built-in policy extension."
                        )
                    ],
                    is_error=True,
                )
                result_messages.append(
                    _to_tool_result_message(tc.tool_call_id, blocked)
                )
                await emit(
                    ToolExecutionEndEvent(tool_call_id=tc.tool_call_id, result=blocked, tool_name=tc.tool_name, is_error=blocked.is_error)
                )
                all_terminate = False
                continue

        if tool.execute is None:
            result = ToolResult(
                content=[
                    TextContent(
                        text=f"Tool '{tool.name}' has no execute callable."
                    )
                ],
                is_error=True,
            )
            result_messages.append(_to_tool_result_message(tc.tool_call_id, result))
            await emit(ToolExecutionEndEvent(tool_call_id=tc.tool_call_id, result=result, tool_name=tc.tool_name, is_error=result.is_error))
            all_terminate = False
            continue

        exec_ctx = ToolExecutionContext(tool_call_id=tc.tool_call_id, signal=signal)
        try:
            result = await tool.execute(args, exec_ctx)
        except Exception as exc:  # noqa: BLE001 — tool author errors must not break the loop
            result = ToolResult(
                content=[
                    TextContent(text=f"Tool '{tool.name}' raised: {exc}")
                ],
                is_error=True,
            )

        if config.after_tool_call is not None:
            after_ctx = AfterToolCallContext(
                assistant_message=assistant_message,
                tool_call=tc,
                args=args,
                result=result,
                is_error=result.is_error,
                context=context,
            )
            override = await _maybe_await(config.after_tool_call(after_ctx))
            if isinstance(override, AfterToolCallResult):
                result = _apply_after_override(result, override)

        if not result.terminate:
            all_terminate = False

        result_messages.append(_to_tool_result_message(tc.tool_call_id, result))
        await emit(ToolExecutionEndEvent(tool_call_id=tc.tool_call_id, result=result, tool_name=tc.tool_name, is_error=result.is_error))

    return _ExecutedBatch(result_messages, all_terminate)


# === Helpers ===


def _to_tool_result_message(tool_call_id: str, result: ToolResult) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=tool_call_id,
        content=list(result.content),
        is_error=result.is_error,
        timestamp=time.time(),
    )


def _apply_after_override(
    result: ToolResult, override: AfterToolCallResult
) -> ToolResult:
    return ToolResult(
        content=override.content if override.content is not None else result.content,
        details=override.details if override.details is not None else result.details,
        is_error=override.is_error if override.is_error is not None else result.is_error,
        terminate=override.terminate if override.terminate is not None else result.terminate,
    )


async def _drain_queue(
    fn: Callable[[], Awaitable[list[AgentMessage]] | list[AgentMessage]] | None,
) -> list[AgentMessage]:
    if fn is None:
        return []
    drained = await _maybe_await(fn())
    return list(drained or [])


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _resolve_stream_simple() -> StreamFn:
    # Imported lazily to keep ``aelix.ai.streaming`` free of agent imports.
    from aelix.ai.streaming import stream_simple

    return stream_simple


__all__ = [
    "AgentEventSink",
    "agent_loop",
    "agent_loop_continue",
]
