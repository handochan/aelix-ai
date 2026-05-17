"""Hook bus and event/result dataclasses for the AgentHarness.

This module defines the typed hook surface a Phase 1.2 ``AgentHarness`` uses
to talk to extensions. The design follows pi-agent-core's split between
``AgentHarness.emit*`` and ``ExtensionRunner.emit*``: each event has a Python
``@dataclass(frozen=True)`` payload and a matching result type, registered in
:data:`HOOK_RESULT_TYPES` for runtime introspection.

Typing model — per D.1.2 (Sprint 1 · Phase 1.2 spec):

- :data:`HookEventName` is a closed ``Literal`` union of every Phase 1.2 event.
- :class:`HookBus.on` carries 16 ``@overload`` declarations mirroring
  ``scripts/pyright_spike.py``. The runtime body accepts ``HookHandler``,
  defined as ``Callable[[HookEvent, ExtensionContext], Any | Awaitable[Any]]``.
- All event classes are frozen. Where a payload field (e.g. ``args``) needs
  to be mutated in-place by a handler chain, the field is a mutable container
  (``dict``/``list``) referenced by the frozen wrapper. Pi parity:
  ``/tmp/pi-ext-runner.ts`` ``emit_tool_call`` passes ``event.input`` by
  reference so handlers can patch arguments in place without re-validation.

Reducer rules (D.1.6 / B6) are implemented as private ``_reducer_*`` helpers
and invoked from :meth:`HookBus.emit` by event-type dispatch.
"""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, overload

from aelix.agent.types import (
    AfterToolCallResult,
    AgentContext,
    AgentMessage,
)
from aelix.ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
)
from aelix.ai.tools import ToolResult

if TYPE_CHECKING:
    # Avoid a runtime import cycle (D.1.9). ExtensionContext lives in
    # aelix.extensions.api which itself depends on this module's types.
    from aelix.extensions.api import ExtensionContext


# === Event name registry ===

HookEventName = Literal[
    "context",
    "before_agent_start",
    "tool_call",
    "tool_result",
    "message_end",
    "agent_start",
    "agent_end",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "session_before_compact",
    "settled",
]


# === Base classes ===


@dataclass(frozen=True)
class HookEvent:
    """Base class for hook events.

    All events are frozen dataclasses (D.1.1). The ``type`` field on each
    subclass is a ``Literal[...]`` so callers can ``match event.type`` or
    feed the bus by name. Mutable payload fields (e.g. ``args: dict``) are
    intentionally referenced rather than copied so handler patches are
    visible to subsequent handlers and to the loop (D.1.5).
    """


HookHandler = Callable[
    [HookEvent, "ExtensionContext"],
    Any,
]
"""Runtime handler signature.

Static narrowing for the public ``on(event, handler)`` surface is provided
by ``@overload`` declarations on :class:`HookBus` and ``ExtensionAPI``; the
runtime accepts any callable matching this loose shape and may return
``None`` or an awaitable.
"""


HookCleanup = Callable[[], Any]
"""Cleanup callable run on dispose. May return ``None`` or an awaitable."""


HookObserver = Callable[[HookEvent, "ExtensionContext"], Any]


@dataclass(frozen=True)
class HookRegistration:
    """Bookkeeping for a single registered handler.

    ``source`` is the extension path (or ``"<inline>"``/``None`` for harness
    or app-registered handlers).
    """

    event_type: HookEventName
    unsubscribe: Callable[[], None]
    source: str | None = None


# === Result types ===


@dataclass(frozen=True)
class ContextResult:
    """Result of a ``context`` handler.

    ``messages`` (when not ``None``) replaces the context message list for
    the next handler in the chain.
    """

    messages: list[AgentMessage] | None = None


@dataclass(frozen=True)
class BeforeAgentStartResult:
    """Result of a ``before_agent_start`` handler.

    ``messages`` are collected from every handler and prepended to the run
    in order; ``system_prompt`` is chained — the next handler sees the
    previously chained prompt.

    ``system_prompt=None`` means "no opinion" and leaves the current chained
    prompt unchanged. Any string value (including empty string ``""``) replaces
    the chained system prompt — empty string is a valid intentional replacement.
    """

    messages: list[AgentMessage] | None = None
    system_prompt: str | None = None


@dataclass(frozen=True)
class ToolCallResult:
    """Result of a ``tool_call`` handler.

    Per D.1.3 only ``block=True`` affects the loop; ``reason`` surfaces in
    the synthesized tool-result message when blocked. Non-block results
    are observational only.
    """

    block: bool = False
    reason: str | None = None


# Alias per D.1.6 — single source of truth for the tool-result patch shape.
ToolResultPatch = AfterToolCallResult


# === Event payloads ===


@dataclass(frozen=True)
class ContextHookEvent(HookEvent):
    """Emitted at the start of every turn before context is sent to the LLM.

    Handlers may return :class:`ContextResult` with a patched ``messages``
    list. The list itself is treated as immutable; handlers return a new list.
    """

    messages: list[AgentMessage] = field(default_factory=list)
    type: Literal["context"] = "context"


@dataclass(frozen=True)
class BeforeAgentStartHookEvent(HookEvent):
    """Emitted once before the first turn of an :meth:`AgentHarness.prompt`."""

    prompt: str = ""
    system_prompt: str = ""
    images: list[ImageContent] | None = None
    type: Literal["before_agent_start"] = "before_agent_start"


@dataclass(frozen=True)
class ToolCallHookEvent(HookEvent):
    """Emitted before each tool ``execute`` call.

    Per D.1.5, ``args`` is the *same dict reference* passed to the loop's
    ``tool.execute`` and to the ``after_tool_call`` callback. Handlers may
    mutate this dict in place (``event.args["foo"] = 1``); later handlers,
    the tool, and the after-callback all observe the mutation. The frozen
    dataclass wrapper only forbids attribute reassignment.
    """

    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    assistant_message: AssistantMessage | None = None
    context: AgentContext | None = None
    type: Literal["tool_call"] = "tool_call"


@dataclass(frozen=True)
class ToolResultHookEvent(HookEvent):
    """Emitted after a tool ``execute`` call returns.

    Handlers may return :class:`ToolResultPatch` (alias for
    ``AfterToolCallResult``) to override content/details/is_error/terminate.
    Patches are accumulated left-to-right (D.1.6); the application-supplied
    ``after_tool_call`` callback (if any) runs on top of the hook stack.
    """

    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    content: list[TextContent | ImageContent] = field(default_factory=list)
    details: Any | None = None
    is_error: bool = False
    terminate: bool = False
    type: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True)
class MessageEndHookEvent(HookEvent):
    """Emitted at the end of every message (user, assistant, tool result).

    Observational only in Phase 1.2 — replacement reducer is deferred to
    ADR-0013 (per D.1.13 M-2).
    """

    message: AgentMessage | None = None
    type: Literal["message_end"] = "message_end"


# --- Lifecycle (observational) events ---


@dataclass(frozen=True)
class AgentStartHookEvent(HookEvent):
    type: Literal["agent_start"] = "agent_start"


@dataclass(frozen=True)
class AgentEndHookEvent(HookEvent):
    messages: list[AgentMessage] = field(default_factory=list)
    type: Literal["agent_end"] = "agent_end"


@dataclass(frozen=True)
class TurnStartHookEvent(HookEvent):
    type: Literal["turn_start"] = "turn_start"


@dataclass(frozen=True)
class TurnEndHookEvent(HookEvent):
    message: AssistantMessage | None = None
    type: Literal["turn_end"] = "turn_end"


@dataclass(frozen=True)
class MessageStartHookEvent(HookEvent):
    message: AgentMessage | None = None
    type: Literal["message_start"] = "message_start"


@dataclass(frozen=True)
class MessageUpdateHookEvent(HookEvent):
    message: AgentMessage | None = None
    type: Literal["message_update"] = "message_update"


@dataclass(frozen=True)
class ToolExecutionStartHookEvent(HookEvent):
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    type: Literal["tool_execution_start"] = "tool_execution_start"


@dataclass(frozen=True)
class ToolExecutionUpdateHookEvent(HookEvent):
    tool_call_id: str = ""
    partial_result: ToolResult | None = None
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    type: Literal["tool_execution_update"] = "tool_execution_update"


@dataclass(frozen=True)
class ToolExecutionEndHookEvent(HookEvent):
    tool_call_id: str = ""
    tool_name: str = ""
    is_error: bool = False
    type: Literal["tool_execution_end"] = "tool_execution_end"


# --- Session events (Phase 1.2 reducer-only stub — no emit site yet) ---


@dataclass(frozen=True)
class SessionBeforeCompactResult:
    """Generic ``session_before_*`` result. ``cancel=True`` short-circuits."""

    cancel: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class SessionBeforeCompactHookEvent(HookEvent):
    """Stub for the ``session_before_*`` family (Phase 1.3+)."""

    type: Literal["session_before_compact"] = "session_before_compact"


@dataclass(frozen=True)
class SettledHookEvent(HookEvent):
    """Observational event emitted when the harness returns to idle."""

    type: Literal["settled"] = "settled"


# === Runtime registry ===


HOOK_RESULT_TYPES: dict[HookEventName, type | None] = {
    "context": ContextResult,
    "before_agent_start": BeforeAgentStartResult,
    "tool_call": ToolCallResult,
    "tool_result": ToolResultPatch,
    "message_end": None,
    "agent_start": None,
    "agent_end": None,
    "turn_start": None,
    "turn_end": None,
    "message_start": None,
    "message_update": None,
    "tool_execution_start": None,
    "tool_execution_update": None,
    "tool_execution_end": None,
    "session_before_compact": SessionBeforeCompactResult,
    "settled": None,
}


# === Reducers (D.1.6 / B6) ===


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _reducer_context(
    handlers: list[HookHandler],
    event: ContextHookEvent,
    ctx: ExtensionContext,
) -> ContextResult | None:
    """Sequential transform — each handler sees the previous patch.

    ## Notes

    ``messages`` is shallow-copied from ``event.messages`` at entry. Pi's
    ``structuredClone`` is not replicated (cost), but messages are frozen
    dataclasses so attribute mutation is impossible. Mutable fields inside
    messages (e.g. ``UserMessage.content`` list, ``ToolResultMessage.content``
    list) can be mutated and that mutation WILL propagate to subsequent
    handlers. Handlers MUST NOT mutate message content in place if isolation
    is required; instead, construct new message instances.
    """

    current_messages = list(event.messages)
    modified = False
    for handler in handlers:
        patched = ContextHookEvent(messages=current_messages)
        raw = await _maybe_await(handler(patched, ctx))
        if isinstance(raw, ContextResult) and raw.messages is not None:
            current_messages = list(raw.messages)
            modified = True
    if not modified:
        return None
    return ContextResult(messages=current_messages)


async def _reducer_before_agent_start(
    handlers: list[HookHandler],
    event: BeforeAgentStartHookEvent,
    ctx: ExtensionContext,
) -> BeforeAgentStartResult | None:
    """Collect messages from all handlers; chain ``system_prompt``."""

    collected: list[AgentMessage] = []
    current_prompt = event.system_prompt
    modified_prompt = False
    for handler in handlers:
        chained = BeforeAgentStartHookEvent(
            prompt=event.prompt,
            system_prompt=current_prompt,
            images=event.images,
        )
        raw = await _maybe_await(handler(chained, ctx))
        if isinstance(raw, BeforeAgentStartResult):
            if raw.messages:
                collected.extend(raw.messages)
            if raw.system_prompt is not None:
                current_prompt = raw.system_prompt
                modified_prompt = True
    if not collected and not modified_prompt:
        return None
    return BeforeAgentStartResult(
        messages=collected or None,
        system_prompt=current_prompt if modified_prompt else None,
    )


async def _reducer_tool_call(
    handlers: list[HookHandler],
    event: ToolCallHookEvent,
    ctx: ExtensionContext,
) -> ToolCallResult | None:
    """Sequential. First ``block=True`` short-circuits.

    Per D.1.3 non-block results are observational only — the loop only acts
    on ``block``. ``reason`` for non-blocking results is dropped because
    ``agent/loop.py`` (Phase 1.1) checks ``decision.block`` exclusively.
    Args mutation across handlers is in-place on the shared dict (D.1.5).
    """

    last_observational: ToolCallResult | None = None
    for handler in handlers:
        raw = await _maybe_await(handler(event, ctx))
        if isinstance(raw, ToolCallResult):
            if raw.block:
                return raw
            last_observational = raw
    return last_observational


async def _reducer_tool_result(
    handlers: list[HookHandler],
    event: ToolResultHookEvent,
    ctx: ExtensionContext,
) -> ToolResultPatch | None:
    """Sequential patch accumulation. Unset fields preserve the prior value."""

    accumulated: ToolResultPatch | None = None
    current_event = event
    for handler in handlers:
        raw = await _maybe_await(handler(current_event, ctx))
        if not isinstance(raw, ToolResultPatch):
            continue
        if accumulated is None:
            accumulated = raw
        else:
            accumulated = ToolResultPatch(
                content=raw.content if raw.content is not None else accumulated.content,
                details=raw.details if raw.details is not None else accumulated.details,
                is_error=raw.is_error if raw.is_error is not None else accumulated.is_error,
                terminate=raw.terminate if raw.terminate is not None else accumulated.terminate,
            )
        # Next handler sees the accumulated patch projected back onto the event.
        current_event = ToolResultHookEvent(
            tool_call_id=event.tool_call_id,
            tool_name=event.tool_name,
            args=event.args,
            content=(
                list(accumulated.content)
                if accumulated.content is not None
                else current_event.content
            ),
            details=(
                accumulated.details
                if accumulated.details is not None
                else current_event.details
            ),
            is_error=(
                accumulated.is_error
                if accumulated.is_error is not None
                else current_event.is_error
            ),
            terminate=(
                accumulated.terminate
                if accumulated.terminate is not None
                else current_event.terminate
            ),
        )
    return accumulated


async def _reducer_session_before(
    handlers: list[HookHandler],
    event: HookEvent,
    ctx: ExtensionContext,
) -> SessionBeforeCompactResult | None:
    """Sequential, ``cancel=True`` short-circuits, else last truthy wins."""

    last: SessionBeforeCompactResult | None = None
    for handler in handlers:
        raw = await _maybe_await(handler(event, ctx))
        if isinstance(raw, SessionBeforeCompactResult):
            if raw.cancel:
                return raw
            last = raw
    return last


async def _reducer_observational(
    handlers: list[HookHandler],
    event: HookEvent,
    ctx: ExtensionContext,
) -> None:
    """Run every handler for side effects; ignore return values.

    Handlers run serially in registration order. Return values are ignored.
    """

    for handler in handlers:
        await _maybe_await(handler(event, ctx))
    return None


_REDUCERS: dict[HookEventName, Callable[..., Awaitable[Any]]] = {
    "context": _reducer_context,
    "before_agent_start": _reducer_before_agent_start,
    "tool_call": _reducer_tool_call,
    "tool_result": _reducer_tool_result,
    "session_before_compact": _reducer_session_before,
    "message_end": _reducer_observational,
    "agent_start": _reducer_observational,
    "agent_end": _reducer_observational,
    "turn_start": _reducer_observational,
    "turn_end": _reducer_observational,
    "message_start": _reducer_observational,
    "message_update": _reducer_observational,
    "tool_execution_start": _reducer_observational,
    "tool_execution_update": _reducer_observational,
    "tool_execution_end": _reducer_observational,
    "settled": _reducer_observational,
}


# === Handler narrowing aliases (D.1.2) ===


ContextHandler = Callable[
    [ContextHookEvent, "ExtensionContext"],
    ContextResult | None | Awaitable[ContextResult | None],
]
BeforeAgentStartHandler = Callable[
    [BeforeAgentStartHookEvent, "ExtensionContext"],
    BeforeAgentStartResult | None | Awaitable[BeforeAgentStartResult | None],
]
ToolCallHandler = Callable[
    [ToolCallHookEvent, "ExtensionContext"],
    ToolCallResult | None | Awaitable[ToolCallResult | None],
]
ToolResultHandler = Callable[
    [ToolResultHookEvent, "ExtensionContext"],
    ToolResultPatch | None | Awaitable[ToolResultPatch | None],
]
MessageEndHandler = Callable[
    [MessageEndHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
AgentStartHandler = Callable[
    [AgentStartHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
AgentEndHandler = Callable[
    [AgentEndHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
TurnStartHandler = Callable[
    [TurnStartHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
TurnEndHandler = Callable[
    [TurnEndHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
MessageStartHandler = Callable[
    [MessageStartHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
MessageUpdateHandler = Callable[
    [MessageUpdateHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
ToolExecutionStartHandler = Callable[
    [ToolExecutionStartHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
ToolExecutionUpdateHandler = Callable[
    [ToolExecutionUpdateHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
ToolExecutionEndHandler = Callable[
    [ToolExecutionEndHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
SessionBeforeCompactHandler = Callable[
    [SessionBeforeCompactHookEvent, "ExtensionContext"],
    SessionBeforeCompactResult | None | Awaitable[SessionBeforeCompactResult | None],
]
SettledHandler = Callable[
    [SettledHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]


# === The bus ===


class HookBus:
    """Holds per-event handler lists and dispatches via the right reducer.

    Construction takes a ``ctx_factory`` returning the current
    :class:`ExtensionContext` (built fresh per emit so stale-detection works
    when the harness is disposed mid-flight).
    """

    def __init__(
        self,
        ctx_factory: Callable[[], ExtensionContext],
    ) -> None:
        self._ctx_factory = ctx_factory
        self._handlers: dict[HookEventName, list[HookHandler]] = {}
        self._observers: list[HookObserver] = []
        self._cleanups: list[HookCleanup] = []
        self._sources: dict[tuple[HookEventName, int], str | None] = {}

    # --- Subscription overloads (D.1.2) ---

    @overload
    def on(
        self,
        event_type: Literal["context"],
        handler: ContextHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["before_agent_start"],
        handler: BeforeAgentStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["tool_call"],
        handler: ToolCallHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["tool_result"],
        handler: ToolResultHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["message_end"],
        handler: MessageEndHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["agent_start"],
        handler: AgentStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["agent_end"],
        handler: AgentEndHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["turn_start"],
        handler: TurnStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["turn_end"],
        handler: TurnEndHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["message_start"],
        handler: MessageStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["message_update"],
        handler: MessageUpdateHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["tool_execution_start"],
        handler: ToolExecutionStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["tool_execution_update"],
        handler: ToolExecutionUpdateHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["tool_execution_end"],
        handler: ToolExecutionEndHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["session_before_compact"],
        handler: SessionBeforeCompactHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["settled"],
        handler: SettledHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]: ...

    def on(
        self,
        event_type: HookEventName,
        handler: HookHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]:
        """Register a handler. Returns an unsubscribe callable.

        Raises ``KeyError`` for unknown event types — strict by design per
        spec B5 (typo defence beats Pi's permissive empty-set fallback).
        """

        if event_type not in HOOK_RESULT_TYPES:
            raise KeyError(f"Unknown hook event: {event_type!r}")
        bucket = self._handlers.setdefault(event_type, [])
        bucket.append(handler)
        self._sources[(event_type, id(handler))] = source
        if cleanup is not None:
            self._cleanups.append(cleanup)

        def unsubscribe() -> None:
            try:
                bucket.remove(handler)
            except ValueError:
                return
            self._sources.pop((event_type, id(handler)), None)

        return unsubscribe

    def observe(self, observer: HookObserver) -> Callable[[], None]:
        """Register a read-only observer that sees every event.

        Observer return values are ignored. Useful for telemetry/audit
        extensions that should not influence the reducer chain.
        """

        self._observers.append(observer)

        def unsubscribe() -> None:
            try:
                self._observers.remove(observer)
            except ValueError:
                return

        return unsubscribe

    def add_cleanup(self, cleanup: HookCleanup) -> Callable[[], None]:
        """Register a cleanup callable run LIFO on :meth:`dispose`."""

        self._cleanups.append(cleanup)

        def unregister() -> None:
            try:
                self._cleanups.remove(cleanup)
            except ValueError:
                return

        return unregister

    def has_handlers(self, event_type: HookEventName) -> bool:
        return bool(self._handlers.get(event_type))

    async def emit(self, event: HookEvent) -> Any:
        """Dispatch ``event``, run reducers + observers, return aggregated result.

        Per D.1.13 M-3, this returns ``None`` immediately when no handlers
        and no observers are registered. Reducer/handler exceptions are
        re-raised so the harness can wrap them in :class:`AgentHarnessError`.
        """

        event_type = getattr(event, "type", None)
        if event_type is None or event_type not in HOOK_RESULT_TYPES:
            raise KeyError(f"Unknown or invalid hook event payload: {event!r}")
        # Snapshot to keep iteration safe if a handler unsubscribes itself.
        handlers = list(self._handlers.get(event_type, ()))
        observers = list(self._observers)
        if not handlers and not observers:
            return None
        ctx = self._ctx_factory()
        reducer = _REDUCERS[event_type]
        result: Any = None
        if handlers:
            result = await reducer(handlers, event, ctx)
        for observer in observers:
            await _maybe_await(observer(event, ctx))  # return value is intentionally not collected
        return result

    async def dispose(self) -> None:
        """Run all cleanups LIFO, then clear handlers and observers.

        After ``dispose()``, handlers and observers are cleared but the bus
        remains usable — new registrations are accepted. To prevent re-use,
        the owning :class:`~aelix.harness.core.AgentHarness` should not be
        reused after disposal.
        """

        # Snapshot + clear so cleanups that re-enter the bus see a clean slate.
        cleanups = list(self._cleanups)
        self._cleanups.clear()
        for cleanup in reversed(cleanups):
            # Cleanups must not break shutdown — swallow any error.
            with contextlib.suppress(Exception):
                await _maybe_await(cleanup())
        self._handlers.clear()
        self._observers.clear()
        self._sources.clear()


__all__ = [
    "AgentEndHookEvent",
    "AgentStartHookEvent",
    "BeforeAgentStartHookEvent",
    "BeforeAgentStartResult",
    "ContextHookEvent",
    "ContextResult",
    "HOOK_RESULT_TYPES",
    "HookBus",
    "HookCleanup",
    "HookEvent",
    "HookEventName",
    "HookHandler",
    "HookObserver",  # exported: observers are part of the documented public API
    "HookRegistration",
    "MessageEndHookEvent",
    "MessageStartHookEvent",
    "MessageUpdateHookEvent",
    "SessionBeforeCompactHookEvent",
    "SessionBeforeCompactResult",
    "SettledHookEvent",
    "ToolCallHookEvent",
    "ToolCallResult",
    "ToolExecutionEndHookEvent",
    "ToolExecutionStartHookEvent",
    "ToolExecutionUpdateHookEvent",
    "ToolResultHookEvent",
    "ToolResultPatch",
    "TurnEndHookEvent",
    "TurnStartHookEvent",
]
