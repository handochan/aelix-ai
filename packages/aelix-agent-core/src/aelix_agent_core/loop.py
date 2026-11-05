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
The parallel path holds one task per call and waits on them itself (NOT
``TaskGroup``) per P-7 reversal: Pi catches every tool error per-tool so
``Promise.all`` reject path is unreachable; TaskGroup's mandatory
sibling-cancel would be Pi-divergence. It never cancels a sibling on a tool
error, which is a result; it does on a cancel and on a harness failure that
leaves the call with no result (ADR-0290).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, TypeGuard

from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
)
from aelix_ai.streaming import Context, SimpleStreamOptions, StreamFn
from aelix_ai.tools import (
    ToolArgumentValidationError,
    ToolCancelled,
    ToolExecutionContext,
    ToolResult,
    validate_tool_arguments,
)

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
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)

# DEBUG throughout: the tree installs no logging handler, so a WARNING reaches
# ``logging.lastResort`` and paints over a live TUI (``harness/core.py``, #546).
_log = logging.getLogger(__name__)

# P0 #7 Wave 3 (ADR-0145): the sink may return a replacement ``AgentMessage``
# for ``message_end`` events (the message_end replacement reducer; pi
# ``emitMessageEnd`` returns the replaced message). For every other event the
# sink returns ``None``. The loop only inspects the return at the two
# ``message_end`` emit sites in :func:`_stream_assistant_response`; all other
# ``await emit(...)`` callers ignore it.
AgentEventSink = Callable[[AgentEvent], Awaitable[AgentMessage | None]]


def _is_replacement(
    replacement: AgentMessage | None, original: AgentMessage
) -> TypeGuard[AgentMessage]:
    """True when ``message_end`` returned a real, applicable replacement.

    P0 #7 Wave 3 (ADR-0145): the sink returns ``None`` (no replacement) or a
    replacement :class:`AgentMessage`. ``_reducer_message_end`` already enforces
    the pi same-role invariant (``runner.ts:714`` — a role mismatch is logged +
    skipped, never returned), so this loop-side guard is defense-in-depth: it
    rejects ``None``, the identity no-op (frozen dataclass — ``is``-based swap),
    and any handler that somehow violated the role contract. Applies the swap at
    EVERY persisting ``message_end`` site (prompt, follow-up, tool-result, and
    the two assistant sites) so a replacement is never silently dropped — pi's
    ``_replaceMessageInPlace`` mutates in place at every message_end event.
    """

    return (
        replacement is not None
        and replacement is not original
        and replacement.role == original.role
    )


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
    # P0 #7 Wave 3 (ADR-0145): the prompt objects live in BOTH ``new_messages``
    # (``list(prompts)``) and ``current_context.messages`` (``[*context.messages,
    # *prompts]``). A ``message_end`` replacement here (pi
    # ``_replaceMessageInPlace`` applies at EVERY message_end) must propagate to
    # both — frozen-dataclass identity swap, same pattern as the assistant
    # sites. Index ``i`` in the enumeration maps to ``new_messages[i]`` and to
    # ``current_context.messages[len(context.messages) + i]``.
    base = len(context.messages)
    for i, prompt in enumerate(prompts):
        await emit(MessageStartEvent(message=prompt))
        replacement = await emit(MessageEndEvent(message=prompt))
        if _is_replacement(replacement, prompt):
            new_messages[i] = replacement
            ctx_index = base + i
            if (
                0 <= ctx_index < len(current_context.messages)
                and current_context.messages[ctx_index] is prompt
            ):
                current_context.messages[ctx_index] = replacement

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
    # ``Agent`` runs the loop in its caller's task, so one run's cancel must not outlive it.
    current = asyncio.current_task()
    if current is not None:
        _CANCEL_REACHED.discard(current)
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
                    # P0 #7 Wave 3 (ADR-0145): apply a ``message_end``
                    # replacement to the steering/follow-up message BEFORE it is
                    # appended, so the REPLACEMENT (not the original) lands in
                    # both ``current_context.messages`` and ``new_messages`` →
                    # persisted via ``_state.messages``. pi applies
                    # ``_replaceMessageInPlace`` at every message_end event.
                    replacement = await emit(MessageEndEvent(message=msg))
                    if _is_replacement(replacement, msg):
                        msg = replacement
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                pending_messages = []

            message = await _stream_assistant_response(
                current_context, config, signal, emit, stream_fn
            )
            new_messages.append(message)

            stop_response = (
                config.should_stop_after_response(message)
                if config.should_stop_after_response is not None
                else False
            )
            if stop_response or message.stop_reason in ("error", "aborted"):
                await emit(TurnEndEvent(message=message, tool_results=[]))
                await emit(AgentEndEvent(messages=new_messages))
                return

            tool_calls = [
                c for c in message.content if isinstance(c, ToolCallContent)
            ]
            tool_results: list[ToolResultMessage] = []
            has_more_tool_calls = False

            if tool_calls:
                # A cancel a listener swallowed while the answer streamed: the
                # message has committed, and no call of it starts (ADR-0290).
                _raise_if_cancel_pending(config.cancel_requested)
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
        # ADR-0135 (P0 #1): forward the per-turn thinking level so the
        # provider adapters can request reasoning. The harness already
        # copies ``options.reasoning`` into the final provider options
        # (``core.py`` ``_make_stream_fn``); without this line it was
        # always ``None`` and the entire thinking stack was a no-op.
        reasoning=config.reasoning,
    )

    fn = stream_fn or _resolve_stream_simple()
    emit = _with_message_start(emit)  # a message its stream never started gets one (#414)
    # #672, #673 (ADR-0290): a cancel a hook or listener caught and swallowed on the
    # way here must not send the next request.
    _raise_if_cancel_pending(config.cancel_requested)
    partial: AssistantMessage | None = None
    partial_index: int | None = None
    final: AssistantMessage | None = None

    # Sprint 6a (Phase 4.1, ADR-0037): 12-variant union consumer.
    # Terminal success: ``"done"`` (Pi) OR legacy ``"end"`` (deprecated alias).
    # Terminal failure: ``"error"`` (Pi `AssistantErrorEvent`) — the
    # adapter has already set ``output.stop_reason in {"aborted","error"}``
    # plus ``error_message`` on the message before emitting.
    #
    # P-39d SILENT DRIFT FIX: ``"toolcall_delta"`` (no underscore between
    # ``tool`` and ``call``) is the Pi canonical spelling.
    _UPDATE_EVENTS = frozenset({
        "text_start",
        "text_delta",
        "text_end",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
    })
    async for event in fn(config.model, llm_context, options):
        if event.type == "start":
            partial = event.partial
            context.messages.append(partial)
            partial_index = len(context.messages) - 1
            await emit(MessageStartEvent(message=partial))
        elif event.type in _UPDATE_EVENTS:
            if partial is None:
                continue
            await emit(
                MessageUpdateEvent(
                    message=partial, assistant_message_event=event
                )
            )
            _raise_if_cancel_pending(config.cancel_requested)
        # `==`-or rather than `in ("end", "done")`: identical at runtime for a
        # Literal field, but pyright narrows a union on `==` and does NOT narrow
        # it on `in`, so the `in` spelling made `event.message` an error against
        # all ten sibling event classes that carry no `message`.
        elif event.type == "end" or event.type == "done":
            final = event.message
            if final.timestamp is None:
                final = replace(final, timestamp=time.time())
            if partial_index is not None:
                context.messages[partial_index] = final
            elif partial is None:
                context.messages.append(final)
            # P0 #7 Wave 3 (ADR-0145): the sink returns a replacement message
            # from the ``message_end`` reducer (same role) or ``None``. Swap by
            # identity (``AgentMessage`` is a frozen dataclass) so the
            # replacement propagates to ``context.messages``, the loop return
            # (``final`` → ``new_messages``), and thus ``_state.messages``. The
            # reducer enforces same-role, so the replacement of an assistant
            # ``message_end`` is always an ``AssistantMessage``; the
            # ``isinstance`` guard makes that invariant explicit (and is a
            # defense-in-depth no-op if a non-assistant somehow slips through).
            replacement = await emit(MessageEndEvent(message=final))
            if (
                isinstance(replacement, AssistantMessage)
                and replacement is not final
            ):
                if partial_index is not None:
                    context.messages[partial_index] = replacement
                elif context.messages and context.messages[-1] is final:
                    context.messages[-1] = replacement
                final = replacement
        elif event.type == "error":
            # Sprint 6a (ADR-0037): Pi ``AssistantErrorEvent`` carries the
            # in-flight assistant message in ``error`` with ``stop_reason``
            # already set to ``"aborted"`` or ``"error"`` by the adapter.
            final = event.error
            if final.timestamp is None:
                final = replace(final, timestamp=time.time())
            if partial_index is not None:
                context.messages[partial_index] = final
            elif partial is None:
                context.messages.append(final)
            # P0 #7 Wave 3 (ADR-0145): identity-swap the replacement (if any)
            # — same as the ``done`` branch. An error/abort message has no
            # extension to replace it in practice, but the path must tolerate
            # (and apply) the new return without crashing. Same-role guard via
            # ``isinstance`` (an error ``final`` is also an ``AssistantMessage``).
            replacement = await emit(MessageEndEvent(message=final))
            if (
                isinstance(replacement, AssistantMessage)
                and replacement is not final
            ):
                if partial_index is not None:
                    context.messages[partial_index] = replacement
                elif context.messages and context.messages[-1] is final:
                    context.messages[-1] = replacement
                final = replacement

    if final is None:
        # #189 — this raise escapes to the glass: the TUI prints ``f"✖ {exc}"``
        # for anything that gets out of ``harness.prompt``, so the message
        # names what happened rather than which protocol the adapter broke.
        # THE CONTRACT, for whoever is writing that adapter: every stream must
        # terminate with ``AssistantDoneEvent``, ``AssistantErrorEvent``, or the
        # legacy ``AssistantEndEvent``. Reaching here means it terminated with
        # none of them.
        raise RuntimeError(
            "The model stream ended without a result: it stopped before "
            "sending a completion, an error, or an end event, so this turn "
            "produced nothing."
        )
    return final


# === Tool execution: router + sequential + parallel ===

# What the model reads for a call a cancel cut, when the tool had nothing of its own
# to say (#673, ADR-0290). "No result provided" now means that a call never began, or
# that a hook failed on it.
_CUT_TEXT = "Cancelled: the user stopped the turn while this tool ran, so it may have partly run."

# How long a second cancel leaves the calls of a parallel batch to end before the
# batch stops waiting for them (ADR-0290 §5). It is what a tool stuck in its cleanup
# needs to end on that cancel and give its own result, and a person can not see it.
_STRAGGLER_GRACE_SECONDS = 0.05

# Calls a second cancel stopped waiting for and that have not ended yet. Held so that
# nothing collects a running task, and so that what one raises in the end is read.
_STRAGGLERS: set[asyncio.Task[Any]] = set()

# Tasks a cancel is known to have reached, once the exception is gone (#698, ADR-0290):
# one this loop caught and did not raise, or a call it cancelled itself. A task's count
# cannot say this. It also stays up with no cancel behind it: on 3.11 and 3.12 a stdlib
# ``TaskGroup`` whose child fails after its body leaves it one up, as aiohttp <= 3.10.5
# does on a timeout, and any of that can run on the turn's task.
_CANCEL_REACHED: weakref.WeakSet[asyncio.Task[Any]] = weakref.WeakSet()


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

    ``abandoned``: a second cancel stopped the batch waiting for this call. The batch
    has given it a result, so whatever it does from here on goes nowhere (ADR-0290 §5).
    ``ended_with``: the result ``tool_execution_end`` went out with, so that the batch
    neither emits a second end event nor loses a result a listener is holding.
    """

    tool_call: ToolCallContent
    tool: Any  # AgentTool — typed as Any to avoid a circular import
    args: dict[str, Any]
    abandoned: bool = False
    ended_with: ToolResult | None = None


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

    try:
        args = await validate_tool_arguments(tool, dict(tc.input))
    except ToolArgumentValidationError as exc:
        # Issue #13: malformed tool-args become a structured, model-readable
        # error RESULT (re-grounding) — never an uncaught crash of the turn.
        # Mirrors the unknown-tool _Immediate above and pi's prepareToolCall
        # catch (``agent-loop.ts``).
        return _Immediate(
            tool_call=tc,
            tool_name=tc.tool_name,
            result=ToolResult(
                content=[TextContent(text=str(exc))],
                is_error=True,
            ),
        )

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
    emit: AgentEventSink,
) -> ToolResult:
    """Run ``tool.execute`` + ``after_tool_call`` override. Never raises.

    A cancel reaching this call is not raised here (#673, ADR-0290), so that the caller
    can emit the end event and commit first, and then raise it. A cut call's result is
    what the tool said in
    :class:`~aelix_ai.tools.ToolCancelled`, or :data:`_CUT_TEXT`. It goes through
    ``after_tool_call`` like any other, already an error, because a redaction hook must
    see partial output before it reaches the context. If that hook raises or is
    cancelled, the result is :data:`_CUT_TEXT` and carries no tool output, never the
    unredacted one.

    "A cancel reached this call" is this task's count having risen across ``execute``,
    or :func:`_cancel_pending`, and not the exception a tool raised. A ``CancelledError``
    from a task nobody cancelled is a tool bug (it awaited something else that was
    cancelled, say), so it is a result like any other exception and the turn goes on. A
    tool that catches a real cancel and returns, or raises something else out of it, has
    not taken it back: its result stands and the caller still stops the batch.

    Per Pi parity (``agent-loop.ts:609-637, 651-680``), exceptions from
    ``tool.execute`` are caught and converted to ``isError`` results;
    ``after_tool_call`` hook exceptions are caught here as well so the parallel
    path's ``asyncio.gather`` never sees a tool-induced exception. Only
    ``AgentHarnessError("hook")`` raised by the harness bridge for the
    ``tool_result`` hook is allowed to escape (it MUST propagate so the
    harness can synthesize a failure assistant message).

    Sprint 3d (P-9): build the ``on_partial`` callback that emits
    :class:`ToolExecutionUpdateEvent` per partial result. Pi parity:
    ``executePreparedToolCall`` (``agent-loop.ts:604-639``) — every emit task
    is drained via ``asyncio.gather`` BOTH on the happy path and on the
    tool-raised exception path, mirroring Pi's
    ``await Promise.all(updateEvents)`` at ``:630``.

    Aelix-additive (intentional): hook-handler exceptions raised inside the
    partial-emit fan-out are caught by the outer ``try/except`` and converted
    to an ``isError`` tool result. Pi lets such exceptions escape
    ``executePreparedToolCall`` entirely. This stricter containment is
    consistent with ADR-0019 v3 ``error_mode`` policy and documented in
    ADR-0017's Sprint 3d amendment.
    """

    # ``Future``, not ``Task``: the sink is typed ``Awaitable``, and
    # ``ensure_future`` below returns a Task for the ``async def`` sinks this
    # repo actually has, but a wrapping Future for any other awaitable. The
    # element type follows the sink's real result (``AgentMessage | None``); the
    # old ``Task[None]`` was wrong on both counts and only went unnoticed because
    # nothing type-checked this file.
    update_events: list[asyncio.Future[AgentMessage | None]] = []
    loop_ref = asyncio.get_running_loop()
    # Pi #5573 (``agent-loop.ts:634, 645, 656, 660, 666``): a tool that invokes
    # its ``on_partial`` callback AFTER ``execute`` has resolved — e.g. from
    # leftover async work it spawned — must not schedule a stale
    # ``ToolExecutionUpdateEvent``. Pi tracks this with ``acceptingUpdates``;
    # once the call settles the callback becomes a no-op. We mirror that with a
    # ``settled`` flag flipped True the instant ``execute`` returns/raises
    # (before draining ``update_events``), so late callbacks are ignored.
    settled = False
    cut = False
    requested = config.cancel_requested

    def _on_partial(partial: ToolResult) -> None:
        # Pi parity ``if (!acceptingUpdates) return;`` — once settled, drop the
        # late progress callback (no task scheduled, no event emitted).
        if settled:
            return
        # Eagerly schedule the emit (Pi parity: ``Promise.resolve(emit(...))``).
        # Lazy coroutines would queue all partials at the gather point — that
        # would change ordering relative to Pi.
        coro = emit(
            ToolExecutionUpdateEvent(
                tool_call_id=prepared.tool_call.tool_call_id,
                partial_result=partial,
                tool_name=prepared.tool.name,
                args=prepared.args,
            )
        )
        # ``ensure_future``, not ``create_task``: ``AgentEventSink`` returns an
        # ``Awaitable``, and ``create_task`` accepts only a coroutine — a sink
        # implemented as a plain callable returning a future would raise here.
        # ``emit(...)`` is still CALLED on the line above, so the eager ordering
        # the comment above depends on is unchanged; for an ``async def`` sink
        # ensure_future does exactly what create_task did.
        update_events.append(asyncio.ensure_future(coro, loop=loop_ref))

    exec_ctx = ToolExecutionContext(
        tool_call_id=prepared.tool_call.tool_call_id,
        signal=signal,
        on_partial=_on_partial,
        # P0 #3 HEAVY (ADR-0139). Pi parity ``ctx.model`` — thread the turn's
        # resolved model so tools (read's non-vision image note) can inspect
        # ``model.input``. Single-line surgical wiring of a non-protected
        # ToolExecutionContext field; ``config.model`` is already in scope.
        model=config.model,
    )
    cancels = _own_cancels()
    try:
        result = await prepared.tool.execute(prepared.args, exec_ctx)
    except asyncio.CancelledError as exc:
        said = exc.result if isinstance(exc, ToolCancelled) else None
        said = said if isinstance(said, ToolResult) else None
        if _note_cancel_caught(cancels) or _cancel_pending(requested):
            # #673 — the call was cut. A record of it is true whoever stopped the turn.
            cut = True
            # An error before any hook reads it: a redactor may handle only errors.
            # A cut call never reads as one that succeeded.
            result = replace(said, is_error=True) if said is not None else _cut_result()
        elif said is not None:
            result = replace(said, is_error=True)
        else:
            result = ToolResult(
                content=[TextContent(text=f"Tool '{prepared.tool.name}' raised CancelledError")],
                is_error=True,
            )
    except Exception as exc:  # noqa: BLE001 — tool author errors must not break the loop
        result = ToolResult(
            content=[
                TextContent(text=f"Tool '{prepared.tool.name}' raised: {exc}")
            ],
            is_error=True,
        )
    # Pi #5573 (``agent-loop.ts:645, 656``): stop accepting progress callbacks the
    # instant the call settles, BEFORE draining, on every leg, mirroring
    # ``acceptingUpdates = false``, so leftover async work firing ``on_partial``
    # during/after the drain cannot enqueue a stale update event.
    settled = True
    # A second cancel gave up on this call and answered for it: no hook, no drain.
    if prepared.abandoned:
        return result
    # Drain on every leg so partial-emit hook handlers complete before the result
    # reaches the loop. Pi parity ``agent-loop.ts:630`` runs the same
    # ``Promise.all(updateEvents)`` on the happy path and under the surrounding
    # catch block. A cut call keeps its cancel whatever the handlers did. A cancel
    # landing in the drain cancels the updates with it, so none arrives after the
    # end event, and does not cost a result that is already in hand.
    if update_events:
        cancels = _own_cancels()
        try:
            await asyncio.gather(*update_events, return_exceptions=cut)
        except asyncio.CancelledError:
            _note_cancel_caught(cancels)
            _log.debug("a cancel landed in the updates of %s", prepared.tool_call.tool_call_id)
        except Exception:
            if not _cancel_pending(requested):
                raise
            _log.debug(
                "an update failed for cancelled call %s",
                prepared.tool_call.tool_call_id,
                exc_info=True,
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
        cancels = _own_cancels()
        try:
            override = await _maybe_await(config.after_tool_call(after_ctx))
        except asyncio.CancelledError:
            _note_cancel_caught(cancels)
            _log.debug(
                "a cancel landed in the after_tool_call hook of %s; its output is withheld",
                prepared.tool_call.tool_call_id,
            )
            return _cut_result()
        except Exception:
            if not _cancel_pending(requested):
                raise
            _log.debug(
                "the after_tool_call hook failed on cancelled call %s; its output is withheld",
                prepared.tool_call.tool_call_id,
                exc_info=True,
            )
            return _cut_result()
        if isinstance(override, AfterToolCallResult):
            result = _apply_after_override(result, override)

    # Always an error: a call that was cut never reads as one that succeeded, even
    # to a hook that said otherwise.
    if cut and not result.is_error:
        result = replace(result, is_error=True)
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

    #672, #673 (ADR-0290): a cancel landing once a call began does not cost its
    result. The rest of the three steps runs and the cancel goes on after it, so a
    later call stays unstarted. That holds for a cancel a tool, a hook or a listener
    swallowed too: what stops the batch is :func:`_cancel_pending`, not the exception.
    """

    result_messages: list[ToolResultMessage] = []
    all_terminate = bool(tool_calls)
    requested = config.cancel_requested

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
            # Sprint 3d (P-9, Pi parity ``agent-loop.ts:434-438``):
            # ``emitToolExecutionEnd`` → ``createToolResultMessage`` →
            # ``emitToolResultMessage`` → push. Both immediate and prepared
            # branches must follow this 3-step order; the previous Aelix
            # ordering appended the message BEFORE the end event and never
            # emitted ``message_start`` / ``message_end`` at all.
            await _emit_tool_end(
                tc.tool_call_id, prepared.tool_name, prepared.result, emit, requested
            )
            msg = await _commit_tool_result(
                tc.tool_call_id, tc.tool_name, prepared.result, emit, requested
            )
            if msg is not None:
                result_messages.append(msg)
            _raise_if_cancel_pending(requested)
            all_terminate = False
            continue

        # The start event or ``before_tool_call`` swallowed a cancel: nothing began.
        _raise_if_cancel_pending(requested)
        result = await _execute_and_finalize(
            context, assistant_message, config, signal, prepared, emit
        )

        if not result.terminate:
            all_terminate = False

        # Sprint 3d (P-9, Pi parity ``agent-loop.ts:434-438``): same
        # 3-step order as the immediate branch — end event fires first,
        # then the tool-result message ``message_start`` / ``message_end``
        # pair via the shared helper, then we append to the batch list.
        await _emit_tool_end(tc.tool_call_id, tc.tool_name, result, emit, requested)
        msg = await _commit_tool_result(tc.tool_call_id, tc.tool_name, result, emit, requested)
        if msg is not None:
            result_messages.append(msg)
        # What stops the batch: a cut call's, or one a tool or a sink swallowed.
        _raise_if_cancel_pending(requested)

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
    2. **Phase 2 — parallel exec** in one held task per call, which
       :func:`_wait_for_calls` waits on. P-7 reversal: NOT ``TaskGroup`` — Pi never
       cancels siblings on tool error. Each ``_run`` closure executes prepared
       tool + finalize + emits ``tool_execution_end`` in **completion order**.
    3. **Phase 3 — source-order message emit**: iterate the finalized entries
       in source order and append tool-result messages.

    #672 (ADR-0290): a cancel in Phase 1 or Phase 2 skips none of Phase 3. Every
    entry known to be finalized commits, in source order, and the cancel goes on
    after it. The barrier stays: a result reaches the file at Phase 3, not when
    its tool ends, so the file's order stays the model's. #673: a call the cancel
    cut does not raise, it is an entry like the others. A harness failure in one
    call (never a tool error, which is a result) goes the same way out: the other
    calls are cancelled and committed, then it is raised.
    """

    # Each entry is either an immediate _Finalized (no execute path) or a
    # prepared call awaiting Phase-2 execution. ``tasks`` runs parallel to it
    # once Phase 2 starts, so what each call ended with is read off its own task.
    pending: list[_Finalized | _Prepared] = []
    tasks: list[asyncio.Task[_Finalized] | None] = []
    turn = asyncio.current_task()
    turn_cancels = 0
    requested = config.cancel_requested

    async def _run(prep: _Prepared) -> _Finalized:
        # A cancel that came ahead of this first step: the call never begins. The
        # batch hands a cancel on only when it next runs, a loop turn behind this.
        if (turn is not None and turn.cancelling() > turn_cancels) or _cancel_pending(requested):
            raise asyncio.CancelledError()

        async def sink(event: AgentEvent) -> AgentMessage | None:
            return None if prep.abandoned else await emit(event)

        result = await _execute_and_finalize(
            context, assistant_message, config, signal, prep, sink
        )
        prep.ended_with = result
        # tool_execution_end fires in COMPLETION order (§E row 4).
        await _emit_tool_end(prep.tool_call.tool_call_id, prep.tool.name, result, sink, requested)
        return _Finalized(tool_call=prep.tool_call, tool_name=prep.tool.name, result=result)

    cancelled = False
    failure: BaseException | None = None
    try:
        # Phase 1 — sequential preparation (Pi agent-loop.ts:456-489).
        for tc in tool_calls:
            # A cancel a hook or listener swallowed: the rest of the batch stays
            # unstarted, as it does for one that got through.
            _raise_if_cancel_pending(requested)
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
                # In the list BEFORE the await: a cancel landing in the end
                # event must not cost a result that already exists (#672).
                pending.append(
                    _Finalized(
                        tool_call=tc,
                        tool_name=prepared.tool_name,
                        result=prepared.result,
                    )
                )
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
            else:
                pending.append(prepared)

        # Phase 2 — parallel execution (Pi agent-loop.ts:491-493, P-7 reversal). With no
        # await from here to the wait, in which the turn is parked: what its count stands
        # at now can be stale, and a rise from now on is somebody cancelling it (#698).
        turn_cancels = turn.cancelling() if turn is not None else 0
        tasks = [
            asyncio.ensure_future(_run(entry)) if isinstance(entry, _Prepared) else None
            for entry in pending
        ]
    except asyncio.CancelledError:
        cancelled = True
    else:
        # _run never raises for tool work (per-tool catches in
        # _execute_and_finalize); only AgentHarnessError raised by the harness's
        # tool_result bridge can escape, and that MUST reach the harness for
        # failure-message synthesis, once the rest of the batch has ended.
        failure = await _wait_for_calls(tasks)

    # In one pass with no await, ahead of the end events below, in which any of them
    # could otherwise finish and speak after all.
    for entry, task in zip(pending, tasks, strict=False):
        if isinstance(entry, _Prepared) and task is not None and not task.done():
            entry.abandoned = True
            _STRAGGLERS.add(task)
            task.add_done_callback(_forget_straggler)

    finalized_entries: list[_Finalized] = []
    for index, entry in enumerate(pending):
        if isinstance(entry, _Finalized):
            finalized_entries.append(entry)
            continue
        if entry.abandoned:
            result = entry.ended_with
            if result is None:
                result = _cut_result()
                await _emit_tool_end(
                    entry.tool_call.tool_call_id, entry.tool.name, result, emit, requested
                )
            finalized_entries.append(
                _Finalized(tool_call=entry.tool_call, tool_name=entry.tool.name, result=result)
            )
            continue
        task = tasks[index] if tasks else None
        # No task: the cancel landed in Phase 1. A cancelled task: the cancel
        # reached it before its first step (``_run`` never raises one once it is
        # running). Either way the call never began, so there is no result to give.
        if task is None or task.cancelled():
            continue
        call_failure = task.exception()
        if call_failure is not None:
            # The failure the batch raises below, or one that came with it or with a
            # cancel. Either way this call has no result.
            _log.debug(
                "tool call %s failed: %r", entry.tool_call.tool_call_id, call_failure
            )
            continue
        finalized_entries.append(task.result())

    # Phase 3 — source-order message emit (Pi agent-loop.ts:495-499).
    # Sprint 3d (P-9): emit the ``message_start`` / ``message_end`` pair for
    # each tool-result message via the shared helper, in source order. Pi
    # parity ``emitToolResultMessage`` (``agent-loop.ts:715-718``); single
    # source of truth shared with the sequential path.
    result_messages: list[ToolResultMessage] = []
    all_terminate = bool(finalized_entries)
    for finalized in finalized_entries:
        # P0 #7 Wave 3 (ADR-0145): CAPTURE the (possibly-replaced) message —
        # mirror the two sequential sites (lines 706/731). ``_emit_tool_result_message``
        # RETURNS the swapped ``ToolResultMessage``; discarding it here (the prior
        # ``await _emit_tool_result_message(msg, emit)`` form) silently dropped a
        # tool-result ``message_end`` replacement on the DEFAULT parallel path —
        # the replacement got persisted to the session but never reached
        # ``result_messages`` → ``new_messages`` → ``_state.messages``, diverging
        # the in-memory agent state from the session and feeding the LLM the
        # unredacted original next turn. The helper guards role/identity via
        # ``_is_replacement`` + ``isinstance(ToolResultMessage)``.
        try:
            msg = await _commit_tool_result(
                finalized.tool_call.tool_call_id,
                finalized.tool_call.tool_name,
                finalized.result,
                emit,
                requested,
            )
        except Exception:
            # With a failure on its way up, a sink's own must not take its place or
            # cost the results after this one. Without one it propagates as ever.
            if failure is None:
                raise
            _log.debug(
                "committing the result of tool call %s failed",
                finalized.tool_call.tool_call_id,
                exc_info=True,
            )
            continue
        if msg is not None:
            result_messages.append(msg)
        if not finalized.result.terminate:
            all_terminate = False

    if cancelled or _cancel_pending(requested):
        # A sink's failure on the way out does not replace the cancel (ADR-0290 §5).
        raise asyncio.CancelledError()
    if failure is not None:
        raise failure
    return _ExecutedBatch(result_messages, all_terminate)


# === Helpers ===


def _cut_result() -> ToolResult:
    return ToolResult(content=[TextContent(text=_CUT_TEXT)], is_error=True)


def _cancel_pending(requested: Callable[[], bool] | None) -> bool:
    """Whether a cancel is known to have come, with no exception on its way up (#698, ADR-0290).

    Whoever sent it says so (*requested*, ``AgentLoopConfig.cancel_requested``), or this
    loop saw it (:data:`_CANCEL_REACHED`). That is how a cancel someone caught and went on
    from still stops the batch. This does not read the task's count: a count that is up
    says nothing, on 3.11 and 3.12 no cancel has to be behind it, and nobody can tell one
    that is from one that is not.
    """

    task = asyncio.current_task()
    if task is not None and task in _CANCEL_REACHED:
        return True
    return requested is not None and requested()


def _raise_if_cancel_pending(requested: Callable[[], bool] | None) -> None:
    if _cancel_pending(requested):
        raise asyncio.CancelledError()


def _own_cancels() -> int:
    """This task's count, for :func:`_note_cancel_caught` to read a rise against."""

    task = asyncio.current_task()
    return task.cancelling() if task is not None else 0


def _note_cancel_caught(cancels: int) -> bool:
    """Remember a ``CancelledError`` caught here and not raised, if a cancel sent it.

    Which is the count having risen across the await it came out of, *cancels* being what
    the count stood at before it. A value already up is not that, and neither is a
    ``CancelledError`` a tool or a hook raised with no cancel behind it: that stays what it
    would be with the count at zero.
    """

    task = asyncio.current_task()
    if task is None or task.cancelling() <= cancels:
        return False
    _CANCEL_REACHED.add(task)
    return True


def _to_tool_result_message(
    tool_call_id: str, tool_name: str, result: ToolResult
) -> ToolResultMessage:
    """The call's name goes on the result (#686): a Gemini model's response has nothing else."""

    return ToolResultMessage(
        tool_call_id=tool_call_id,
        content=list(result.content),
        is_error=result.is_error,
        timestamp=time.time(),
        tool_name=tool_name,
    )


async def _wait_for_calls(
    tasks: list[asyncio.Task[_Finalized] | None],
) -> BaseException | None:
    """Phase 2's wait. Never raises a cancel, and returns the first failure.

    ``asyncio.wait`` and not ``gather``, because of what a repeated cancel does (#672,
    #673, ADR-0290): a ``gather`` hands each one to its children and never wakes its
    caller while one of them is still running, so no number of cancels could end the
    wait for a tool that ignores them. Each one lands in this wait instead.

    The first cancel, or a call failing, cancels the calls still running, and the wait
    goes on: their records are the point. A second cancel is handed on too, and after
    :data:`_STRAGGLER_GRACE_SECONDS` stops the wait, with whatever is still running
    left for the caller to answer for. A tool that ignores cancellation therefore holds
    the turn for one Esc and not for two.
    """

    turn = asyncio.current_task()
    # The turn is parked in this wait, so a rise from here on is somebody cancelling it.
    cancels = turn.cancelling() if turn is not None else 0
    stopping = False
    failure: BaseException | None = None
    while True:
        running = [task for task in tasks if task is not None and not task.done()]
        if failure is None:
            failure = _first_failure(tasks)
            if failure is not None and not stopping:
                stopping = True
                _cancel_calls(running)
        if not running:
            return failure
        try:
            await asyncio.wait(running, return_when=asyncio.FIRST_EXCEPTION)
        except asyncio.CancelledError:
            # Nothing but a cancel of this task raises this out of ``asyncio.wait``.
            if turn is not None:
                _CANCEL_REACHED.add(turn)
            # Two cancels in one loop turn arrive here as one exception and two counts.
            second = stopping or (turn is not None and turn.cancelling() > cancels + 1)
            stopping = True
            _cancel_calls(running)
            if second:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait(running, timeout=_STRAGGLER_GRACE_SECONDS)
                return failure or _first_failure(tasks)


def _cancel_calls(running: list[asyncio.Task[_Finalized]]) -> None:
    for task in running:
        _CANCEL_REACHED.add(task)
        task.cancel()


def _first_failure(tasks: list[asyncio.Task[_Finalized] | None]) -> BaseException | None:
    for task in tasks:
        if task is not None and task.done() and not task.cancelled():
            failure = task.exception()
            if failure is not None:
                return failure
    return None


def _forget_straggler(task: asyncio.Task[Any]) -> None:
    _STRAGGLERS.discard(task)
    if not task.cancelled() and task.exception() is not None:
        _log.debug("a call the turn stopped waiting for failed", exc_info=task.exception())


async def _emit_tool_end(
    tool_call_id: str,
    tool_name: str,
    result: ToolResult,
    emit: AgentEventSink,
    requested: Callable[[], bool] | None,
) -> None:
    """``tool_execution_end`` for a call whose result exists.

    The call is over, so a cancel landing in a listener or hook here must not cost
    its result (#672, ADR-0290): the caller commits it and re-raises afterwards.
    With a cancel on its way up, a sink's failure must not replace it.
    """

    cancels = _own_cancels()
    try:
        await emit(
            ToolExecutionEndEvent(
                tool_call_id=tool_call_id,
                result=result,
                tool_name=tool_name,
                is_error=result.is_error,
            )
        )
    except asyncio.CancelledError:
        _note_cancel_caught(cancels)
        _log.debug("a cancel landed in tool_execution_end of %s", tool_call_id)
    except Exception:
        if not _cancel_pending(requested):
            raise
        _log.debug("tool_execution_end failed for cancelled call %s", tool_call_id, exc_info=True)


async def _commit_tool_result(
    tool_call_id: str,
    tool_name: str,
    result: ToolResult,
    emit: AgentEventSink,
    requested: Callable[[], bool] | None,
) -> ToolResultMessage | None:
    """Commit one result. Returns the message, or ``None`` if it is lost.

    #672 (ADR-0290): this is how the way out stays both complete and leavable. A
    cancel landing in ``message_end`` skips this one result, whose message is ``None``,
    and the caller goes on to the rest and re-raises after them. So a hook parked there
    cannot hold the turn past another Esc, at the price of the result it held: nothing
    can tell whether a cancelled sink got as far as the commit, and running it again
    would write the result twice. ``message_start`` commits nothing, so a cancel landing
    there costs nothing (:func:`_emit_tool_result_message`).
    """

    msg = _to_tool_result_message(tool_call_id, tool_name, result)
    cancels = _own_cancels()
    try:
        return await _emit_tool_result_message(msg, emit, requested)
    except asyncio.CancelledError:
        _note_cancel_caught(cancels)
        _log.debug("a cancel landed while committing the result of tool call %s", tool_call_id)
        return None
    except Exception:
        if not _cancel_pending(requested):
            raise
        _log.debug("committing the result of cancelled call %s failed", tool_call_id, exc_info=True)
        return None


async def _emit_tool_result_message(
    msg: ToolResultMessage, emit: AgentEventSink, requested: Callable[[], bool] | None
) -> ToolResultMessage:
    """Emit ``message_start`` + ``message_end`` for a tool-result message.

    Pi parity: ``emitToolResultMessage`` (``agent-loop.ts:715-718``). Both
    events emitted — single source of truth. The sequential path
    (``_execute_tool_calls_sequential``) and the parallel-path Phase 3
    source-order loop both call this helper; ``_run_loop`` does NOT emit
    message events for tool-result messages so this helper is the only
    site, mirroring Pi where ``runLoop`` defers to ``emitToolResultMessage``.

    P0 #7 Wave 3 (ADR-0145): ``message_end`` is a replacement reducer. pi
    applies ``_replaceMessageInPlace`` at EVERY message_end event, including
    tool-result messages, so a handler that rewrites/redacts a tool result
    takes effect. This helper RETURNS the (possibly-replaced) message; the
    caller swaps the object it appends to its batch list so the replacement
    flows into ``new_messages`` → persisted ``_state.messages``. ``role`` is
    enforced same by the reducer (``tool_result``); the loop-side
    :func:`_is_replacement` guard is defense-in-depth.

    #672 (ADR-0290): ``message_start`` commits nothing, so a cancel landing there costs
    nothing. ``message_end`` still runs, and :func:`_cancel_pending` keeps the cancel.
    """

    cancels = _own_cancels()
    try:
        await emit(MessageStartEvent(message=msg))
    except asyncio.CancelledError:
        _note_cancel_caught(cancels)
        _log.debug("a cancel landed in message_start of tool call %s", msg.tool_call_id)
    except Exception:
        if not _cancel_pending(requested):
            raise
        _log.debug(
            "message_start failed for cancelled call %s", msg.tool_call_id, exc_info=True
        )
    replacement = await emit(MessageEndEvent(message=msg))
    if _is_replacement(replacement, msg) and isinstance(
        replacement, ToolResultMessage
    ):
        return replacement
    return msg


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


def _with_message_start(emit: AgentEventSink) -> AgentEventSink:
    """``emit`` for one assistant message, giving it a ``message_start`` first (#414).

    pi's loop emits ``message_start`` for a message whose stream sent no
    ``start`` - the ``done`` or ``error`` arrives with no partial - right before
    its ``message_end``, carrying the final message
    (``packages/agent/src/agent-loop.ts:451-453`` @ 3daa20280). Every built-in
    adapter sends ``start`` only once its stream is open (``anthropic.py``,
    ``openai_completions.py``, ``openai_responses.py``,
    ``openai_codex_responses.py``, ``google_generative_ai.py``,
    ``google_vertex.py``), so a request that failed before that - a ``502``, a
    refused key - ended with ``message_end`` alone: a TUI, ``--mode json`` or
    rpc client, or a ``message_start`` hook saw an end with no start.

    :func:`_stream_assistant_response` sends every event of its message through
    this wrapper, so an extension's stream function is covered too. The wrapped
    ``message_start`` carries the same object as the ``message_end`` that
    follows (a frozen dataclass), and what ``message_end`` returns (a hook's
    replacement) is returned unchanged.
    """

    started = False

    async def sink(event: AgentEvent) -> AgentMessage | None:
        nonlocal started
        if event.type == "message_start":
            started = True
        elif event.type == "message_end" and not started:
            started = True
            await emit(MessageStartEvent(message=event.message))
        return await emit(event)

    return sink


__all__ = [
    "AgentEventSink",
    "agent_loop",
    "agent_loop_continue",
]
