"""Low-level agent loop — pi-agent-core ``runLoop`` ported to Python.

The two public entry points are:

- :func:`agent_loop` — start a fresh loop with prompt messages.
- :func:`agent_loop_continue` — resume from an existing context (used by retry
  paths after the last message in context is a ``user`` or ``toolResult``).

Both take an ``emit`` async callback that receives every :class:`AgentEvent`,
matching pi-agent-core's ``AgentEventSink`` signature. A high-level
:class:`~aelix_agent_core.agent.Agent` wraps this loop with state + subscribers.

Sprint 3c (Phase 2.1.3): Tool execution dispatches to either the sequential
or the parallel path. Pi parity default is ``"parallel"`` (Pi
``agent-loop.ts:380-387`` + ``types.ts:226-232``); any tool with
``execution_mode == "sequential"`` downgrades the whole batch to sequential.
The parallel path uses ``asyncio.gather(*coros, return_exceptions=False)``
(NOT ``TaskGroup``) per P-7 reversal: Pi catches every tool error per-tool so
``Promise.all`` reject path is unreachable; TaskGroup's mandatory
sibling-cancel would be Pi-divergence.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
)
from aelix_ai.streaming import Context, SimpleStreamOptions, StreamFn
from aelix_ai.tools import ToolExecutionContext, ToolResult, validate_tool_arguments

from aelix_agent_core.types import (
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


# === Tool execution: router + sequential + parallel ===


class _ExecutedBatch:
    __slots__ = ("messages", "terminate")

    def __init__(self, messages: list[ToolResultMessage], terminate: bool) -> None:
        self.messages = messages
        self.terminate = terminate


@dataclass
class _Prepared:
    """Result of :func:`_prepare_tool_call` when the tool is ready to execute.

    ``args`` is the validated args dict; D.1.5 contract requires the SAME
    reference to flow into both the (already-emitted) ``before_tool_call``
    bridge and ``tool.execute``.
    """

    tool_call: ToolCallContent
    tool: Any  # AgentTool — typed as Any to avoid a circular import
    args: dict[str, Any]


@dataclass
class _Immediate:
    """An already-finalized tool call: unknown tool / no execute / hook-blocked.

    The sequential path emits ``tool_execution_end`` synchronously when an
    immediate is produced; the parallel path emits it during the prep loop
    (Phase 1) so source ordering is preserved per §E.
    """

    tool_call: ToolCallContent
    tool_name: str
    result: ToolResult


@dataclass
class _Finalized:
    """A finalized tool call ready for source-order message emit (parallel path)."""

    tool_call: ToolCallContent
    tool_name: str
    result: ToolResult


async def _prepare_tool_call(
    context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    tc: ToolCallContent,
    tool_map: dict[str, Any],
) -> _Prepared | _Immediate:
    """Shared prep step: unknown / validate args / before_tool_call / no-execute.

    Returns :class:`_Immediate` for any branch that yields a fully-formed
    :class:`ToolResult` without invoking ``tool.execute``; returns
    :class:`_Prepared` otherwise. The args dict on a :class:`_Prepared` is the
    canonical reference per F.2 / D.1.5.

    NOTE: this helper does NOT emit any events — emit ordering is the
    responsibility of the caller (sequential vs parallel paths differ).
    """

    tool = tool_map.get(tc.tool_name)
    if tool is None:
        return _Immediate(
            tool_call=tc,
            tool_name=tc.tool_name,
            result=ToolResult(
                content=[TextContent(text=f"Unknown tool: {tc.tool_name}")],
                is_error=True,
            ),
        )

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
            return _Immediate(
                tool_call=tc,
                tool_name=tc.tool_name,
                result=ToolResult(
                    content=[
                        TextContent(
                            text=decision.reason
                            or "Blocked by built-in policy extension."
                        )
                    ],
                    is_error=True,
                ),
            )

    if tool.execute is None:
        return _Immediate(
            tool_call=tc,
            tool_name=tc.tool_name,
            result=ToolResult(
                content=[
                    TextContent(text=f"Tool '{tool.name}' has no execute callable.")
                ],
                is_error=True,
            ),
        )

    return _Prepared(tool_call=tc, tool=tool, args=args)


async def _execute_and_finalize(
    context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Any,
    prepared: _Prepared,
) -> ToolResult:
    """Run ``tool.execute`` + ``after_tool_call`` override. Never raises.

    Per Pi parity (``agent-loop.ts:609-637, 651-680``), exceptions from
    ``tool.execute`` are caught and converted to ``isError`` results;
    ``after_tool_call`` hook exceptions are caught here as well so the parallel
    path's ``asyncio.gather`` never sees a tool-induced exception. Only
    ``AgentHarnessError("hook")`` raised by the harness bridge for the
    ``tool_result`` hook is allowed to escape (it MUST propagate so the
    harness can synthesize a failure assistant message).
    """

    exec_ctx = ToolExecutionContext(
        tool_call_id=prepared.tool_call.tool_call_id, signal=signal
    )
    try:
        result = await prepared.tool.execute(prepared.args, exec_ctx)
    except Exception as exc:  # noqa: BLE001 — tool author errors must not break the loop
        result = ToolResult(
            content=[
                TextContent(text=f"Tool '{prepared.tool.name}' raised: {exc}")
            ],
            is_error=True,
        )

    if config.after_tool_call is not None:
        after_ctx = AfterToolCallContext(
            assistant_message=assistant_message,
            tool_call=prepared.tool_call,
            args=prepared.args,
            result=result,
            is_error=result.is_error,
            context=context,
        )
        override = await _maybe_await(config.after_tool_call(after_ctx))
        if isinstance(override, AfterToolCallResult):
            result = _apply_after_override(result, override)

    return result


async def _execute_tool_calls(
    context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Any,
    emit: AgentEventSink,
) -> _ExecutedBatch:
    """Router: dispatch to sequential or parallel path per §A.1 / §B.

    Pi parity (``agent-loop.ts:380-387``): a single tool with
    ``execution_mode == "sequential"`` downgrades the entire batch to
    sequential, regardless of the global ``config.tool_execution`` setting.
    """

    tool_calls = [
        c for c in assistant_message.content if isinstance(c, ToolCallContent)
    ]
    tool_map = {t.name: t for t in context.tools}
    has_sequential = any(
        (
            tool_map.get(tc.tool_name) is not None
            and getattr(tool_map[tc.tool_name], "execution_mode", None) == "sequential"
        )
        for tc in tool_calls
    )
    if config.tool_execution == "sequential" or has_sequential:
        return await _execute_tool_calls_sequential(
            context, assistant_message, config, signal, emit, tool_calls, tool_map
        )
    return await _execute_tool_calls_parallel(
        context, assistant_message, config, signal, emit, tool_calls, tool_map
    )


async def _execute_tool_calls_sequential(
    context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Any,
    emit: AgentEventSink,
    tool_calls: list[ToolCallContent],
    tool_map: dict[str, Any],
) -> _ExecutedBatch:
    """Sequential body — Phase 1.1 behaviour preserved verbatim.

    Each tool: emit ``tool_execution_start`` → prep → (immediate branch emits
    ``tool_execution_end`` and appends message; prepared branch executes +
    finalize + emit ``tool_execution_end`` + append message).
    """

    result_messages: list[ToolResultMessage] = []
    all_terminate = bool(tool_calls)

    for tc in tool_calls:
        await emit(
            ToolExecutionStartEvent(
                tool_call_id=tc.tool_call_id,
                tool_name=tc.tool_name,
                args=dict(tc.input),
            )
        )
        prepared = await _prepare_tool_call(
            context, assistant_message, config, tc, tool_map
        )

        if isinstance(prepared, _Immediate):
            result_messages.append(
                _to_tool_result_message(tc.tool_call_id, prepared.result)
            )
            await emit(
                ToolExecutionEndEvent(
                    tool_call_id=tc.tool_call_id,
                    result=prepared.result,
                    tool_name=prepared.tool_name,
                    is_error=prepared.result.is_error,
                )
            )
            all_terminate = False
            continue

        result = await _execute_and_finalize(
            context, assistant_message, config, signal, prepared
        )

        if not result.terminate:
            all_terminate = False

        result_messages.append(_to_tool_result_message(tc.tool_call_id, result))
        await emit(
            ToolExecutionEndEvent(
                tool_call_id=tc.tool_call_id,
                result=result,
                tool_name=tc.tool_name,
                is_error=result.is_error,
            )
        )

    return _ExecutedBatch(result_messages, all_terminate)


async def _execute_tool_calls_parallel(
    context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Any,
    emit: AgentEventSink,
    tool_calls: list[ToolCallContent],
    tool_map: dict[str, Any],
) -> _ExecutedBatch:
    """Parallel body — Pi parity with ``agent-loop.ts:446-505``.

    Preconditions: caller MUST have already routed via ``_execute_tool_calls``
    (the router) which guarantees ``config.tool_execution != "sequential"``
    AND no tool has ``execution_mode == "sequential"``. Calling this directly
    outside the router bypasses Pi-parity sequential downgrade and is
    incorrect for production code (G.14 ``test_p6_dispatcher_routing.py``
    calls it directly only to verify routing behavior in isolation).

    Three phases per spec §A.2 / §E:

    1. **Phase 1 — sequential prep** (source order): emit
       ``tool_execution_start`` per tool, run ``_prepare_tool_call``. Immediate
       results emit ``tool_execution_end`` here (so source ordering of end
       events is preserved for immediates per §E.1).
    2. **Phase 2 — parallel exec** via ``asyncio.gather(*coros,
       return_exceptions=False)``. P-7 reversal: NOT ``TaskGroup`` — Pi never
       cancels siblings on tool error. Each ``_run`` closure executes prepared
       tool + finalize + emits ``tool_execution_end`` in **completion order**.
    3. **Phase 3 — source-order message emit**: ``gather`` preserves source
       order; iterate ``ordered_results`` and append tool-result messages.
    """

    # Phase 1 — sequential preparation (Pi agent-loop.ts:456-489).
    # Each entry is either an immediate _Finalized (no execute path) or a
    # prepared call awaiting Phase-2 execution.
    pending: list[_Finalized | _Prepared] = []
    for tc in tool_calls:
        await emit(
            ToolExecutionStartEvent(
                tool_call_id=tc.tool_call_id,
                tool_name=tc.tool_name,
                args=dict(tc.input),
            )
        )
        prepared = await _prepare_tool_call(
            context, assistant_message, config, tc, tool_map
        )
        if isinstance(prepared, _Immediate):
            # §E.1: immediates emit tool_execution_end in the prep loop so
            # their end events stay in source order alongside their start
            # events. Pi parity: synchronous resolution path during prep.
            await emit(
                ToolExecutionEndEvent(
                    tool_call_id=tc.tool_call_id,
                    result=prepared.result,
                    tool_name=prepared.tool_name,
                    is_error=prepared.result.is_error,
                )
            )
            pending.append(
                _Finalized(
                    tool_call=tc,
                    tool_name=prepared.tool_name,
                    result=prepared.result,
                )
            )
        else:
            pending.append(prepared)

    # Phase 2 — parallel execution (Pi agent-loop.ts:491-493, P-7 reversal).
    async def _run(prep: _Prepared) -> _Finalized:
        result = await _execute_and_finalize(
            context, assistant_message, config, signal, prep
        )
        # tool_execution_end fires in COMPLETION order (§E row 4).
        await emit(
            ToolExecutionEndEvent(
                tool_call_id=prep.tool_call.tool_call_id,
                result=result,
                tool_name=prep.tool.name,
                is_error=result.is_error,
            )
        )
        return _Finalized(
            tool_call=prep.tool_call,
            tool_name=prep.tool.name,
            result=result,
        )

    async def _identity(entry: _Finalized) -> _Finalized:
        return entry

    coros: list[Awaitable[_Finalized]] = [
        _run(entry) if isinstance(entry, _Prepared) else _identity(entry)
        for entry in pending
    ]
    # return_exceptions=False is correct: _run never raises for tool work
    # (per-tool catches in _execute_and_finalize); only AgentHarnessError
    # raised by the harness's tool_result bridge can escape and that MUST
    # propagate to the harness for failure-message synthesis.
    ordered_results: list[_Finalized] = await asyncio.gather(
        *coros, return_exceptions=False
    )

    # Phase 3 — source-order message emit (Pi agent-loop.ts:495-499).
    result_messages: list[ToolResultMessage] = []
    all_terminate = bool(ordered_results)
    for finalized in ordered_results:
        result_messages.append(
            _to_tool_result_message(finalized.tool_call.tool_call_id, finalized.result)
        )
        if not finalized.result.terminate:
            all_terminate = False

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
    # Imported lazily to keep ``aelix_ai.streaming`` free of agent imports.
    # F-1.4: ``stream_simple`` is now ``async def`` (eager-raise, Pi parity);
    # the loop's ``async for ev in fn(...)`` shape needs an async-generator
    # adapter that awaits the coroutine then yields from its iterator.
    from aelix_ai.streaming import stream_simple

    async def _adapter(model, ctx, options):  # type: ignore[no-untyped-def]
        iterator = await stream_simple(model, ctx, options)
        async for event in iterator:
            yield event

    return _adapter


__all__ = [
    "AgentEventSink",
    "agent_loop",
    "agent_loop_continue",
]
