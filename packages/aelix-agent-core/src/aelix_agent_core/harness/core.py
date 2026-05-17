"""AgentHarness — the hook-aware layer above the low-level agent loop.

Architecture (Phase 1.2):

- :class:`AgentHarness` owns a :class:`~aelix_agent_core.harness.hooks.HookBus`, a
  shared :class:`~aelix_coding_agent.extensions.api._ExtensionRuntime`, and an
  :class:`AgentState`. It calls :func:`agent_loop` with its own callback
  bridges that translate ``before_tool_call`` / ``after_tool_call`` /
  ``transform_context`` into hook emits.
- The Phase 1.1 :class:`~aelix_agent_core.agent.Agent` stays as-is. Direct callers
  of :func:`agent_loop` still get the original callback path without any
  hook overhead.

The callback↔hook bridge respects D.1.5: the dict reference passed in
``BeforeToolCallContext.args`` is the same dict that flows into the
:class:`~aelix_agent_core.harness.hooks.ToolCallHookEvent`. Mutations made by a hook
handler are therefore visible to ``tool.execute`` and to the
``after_tool_call`` callback chain.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, assert_never

from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import Model, StreamFn

from aelix_agent_core.agent import AgentListener
from aelix_agent_core.default_convert import default_convert_to_llm
from aelix_agent_core.harness.hooks import (
    AgentEndHookEvent,
    AgentStartHookEvent,
    BeforeAgentStartHookEvent,
    BeforeAgentStartResult,
    ContextHookEvent,
    ContextResult,
    HookBus,
    HookEvent,
    MessageEndHookEvent,
    MessageStartHookEvent,
    MessageUpdateHookEvent,
    SettledHookEvent,
    ToolCallHookEvent,
    ToolCallResult,
    ToolExecutionEndHookEvent,
    ToolExecutionStartHookEvent,
    ToolExecutionUpdateHookEvent,
    ToolResultHookEvent,
    ToolResultPatch,
    TurnEndHookEvent,
    TurnStartHookEvent,
)
from aelix_agent_core.loop import agent_loop
from aelix_agent_core.types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentState,
    AgentTool,
    BeforeToolCallContext,
    BeforeToolCallResult,
    ConvertToLlmFn,
    MessageEndEvent,
    MessageStartEvent,
    QueueMode,
    TurnEndEvent,
)

if TYPE_CHECKING:
    # Imported at type-check time only to break the harness↔extensions
    # runtime import cycle (D.1.9). The concrete classes are resolved via
    # local imports inside :class:`AgentHarness.__init__` and
    # :meth:`AgentHarness._make_context`.
    from aelix_coding_agent.extensions.api import (
        Extension,
        ExtensionContext,
        _ExtensionRuntime,
    )


_log = logging.getLogger(__name__)

AgentHarnessPhase = Literal["idle", "turn"]
HarnessListener = AgentListener  # type alias — same shape as AgentListener


class AgentHarnessError(Exception):
    """Errors surfaced by the harness itself (busy/state/argument/hook)."""

    def __init__(
        self,
        code: Literal["busy", "invalid_state", "invalid_argument", "hook", "unknown"],
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class AgentHarnessOptions:
    """Constructor options for :class:`AgentHarness`.

    Per D.1.7, ``runtime`` may be supplied (typically from
    ``load_extensions(...).runtime``) so the same runtime instance shared by
    every extension also drives this harness's action bindings. When ``None``
    the harness creates an empty runtime.
    """

    model: Model = field(default_factory=Model)
    extensions: list[Extension] = field(default_factory=list)
    tools: list[AgentTool] = field(default_factory=list)
    system_prompt: str = ""
    initial_messages: list[AgentMessage] = field(default_factory=list)
    convert_to_llm: ConvertToLlmFn | None = None
    transform_context: Callable[[list[AgentMessage], Any], list[AgentMessage] | Awaitable[list[AgentMessage]]] | None = None
    get_api_key: Callable[[str], Awaitable[str | None] | str | None] | None = None
    steering_mode: QueueMode = "one-at-a-time"
    follow_up_mode: QueueMode = "one-at-a-time"
    stream_fn: StreamFn | None = None
    after_tool_call: Callable[..., Any] | None = None
    runtime: _ExtensionRuntime | None = None
    cwd: str = "."

    # === Phase 2.1 placeholders (F-6, ADR-0017) — no behavior in Phase 1.4 ===
    # Each field exists so Phase 2.1 can wire behavior without breaking
    # constructors written against the Phase 1.4 signature. Pi line citations
    # are relative to SHA 734e08edf82ff315bc3d96472a6ebfa69a1d8016.
    session: Any | None = None  # Phase 2.2 placeholder (ADR-0022 — see spec). Pi: types.ts:563
    env: dict[str, str] | None = None  # Phase 2.1 placeholder (ADR-0017 — see spec). Pi: types.ts:562
    resources: list[Any] | None = None  # Phase 2.1 placeholder (ADR-0017 — see spec). Pi: types.ts:565
    thinking_level: str | None = None  # Phase 2.1 placeholder (ADR-0017 — see spec). Pi: types.ts:~576
    active_tool_names: list[str] | None = None  # Phase 2.1 placeholder (ADR-0017 — see spec). Pi: types.ts:~577
    get_api_key_and_headers: Callable[..., Any] | None = None  # Phase 2.1 placeholder (ADR-0017 — see spec). Pi: types.ts:~571
    stream_options: dict[str, Any] | None = None  # Phase 2.1 placeholder (ADR-0017, ADR-0037 — see spec). Pi: types.ts:~574


@dataclass
class _TurnState:
    """Per-turn snapshot of state values resolved before ``_run`` (F-10).

    The harness rebuilds this on every :meth:`AgentHarness.prompt` call so that
    state mutations made by ``before_agent_start`` (e.g. chained system prompt)
    do not leak into subsequent turns. Outside of a turn, callers fall back to
    the long-lived ``self._state``.
    """

    system_prompt: str
    model: Model


class _MessageQueue:
    """Mirror of :class:`aelix_agent_core.agent._MessageQueue` (kept independent)."""

    def __init__(self, mode: QueueMode) -> None:
        self.mode: QueueMode = mode
        self._messages: list[AgentMessage] = []

    def enqueue(self, message: AgentMessage) -> None:
        self._messages.append(message)

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


class AgentHarness:
    """Hook-aware orchestrator built on top of :func:`agent_loop`.

    Phase 1.2 scope: ``idle`` / ``turn`` phases only (D.1.10 / B9).
    ``compaction`` / ``branch_summary`` / ``retry`` are deferred. ``steer()``
    and ``follow_up()`` are always legal and simply enqueue when idle —
    the next ``prompt()`` drains the queues (Pi parity, D.1.10).

    Requires ``aelix-coding-agent`` to be installed at the moment of
    construction; ``aelix-agent-core`` does not declare it as runtime
    dependency to preserve a clean ``import aelix_agent_core`` for clients
    that only consume the loop or hook bus.
    """

    def __init__(self, options: AgentHarnessOptions) -> None:
        # Local imports break the harness↔extensions runtime cycle (D.1.9):
        # ``extensions/api.py`` already imports types from ``harness/hooks``.
        from aelix_coding_agent.extensions.api import ExtensionRuntimeActions, _ExtensionRuntime

        self._options = options
        self._runtime = options.runtime or _ExtensionRuntime()
        self._extensions: list[Extension] = list(options.extensions)
        self._listeners: list[HarnessListener] = []
        self._phase: AgentHarnessPhase = "idle"
        self._abort_requested = False
        # F-10: per-turn snapshot rebuilt at every prompt(). None when idle.
        self._turn_state: _TurnState | None = None

        # Build a merged tool list with application-supplied tools winning
        # over extension-registered ones with the same name (D.1.13 M-9).
        merged: dict[str, AgentTool] = {}
        for extension in self._extensions:
            for name, tool in extension.tools.items():
                merged.setdefault(name, tool)
        for tool in options.tools:
            merged[tool.name] = tool

        self._state = AgentState(
            system_prompt=options.system_prompt,
            model=options.model,
            tools=list(merged.values()),
            messages=list(options.initial_messages),
        )

        # Action table installed for ExtensionContext + ExtensionAPI.
        self._runtime.bind_core(
            ExtensionRuntimeActions(
                get_active_tools=self._action_get_active_tools,
                set_active_tools=self._action_set_active_tools,
                get_system_prompt=self._action_get_system_prompt,
            )
        )

        # Hook bus + handler wiring.
        # ADR-0019 v3: per-handler ``error_mode`` is carried on
        # ``Extension.handler_error_modes`` (keyed by ``(event, id(handler))``)
        # and threaded into ``HookBus.on(...)`` here. Default is ``"throw"``
        # which preserves Pi shipped behavior for any extension that didn't
        # opt into ``"continue"``.
        self._hooks = HookBus(ctx_factory=self._make_context)
        for extension in self._extensions:
            for event_name, handler_list in extension.handlers.items():
                for handler in handler_list:
                    mode = extension.handler_error_modes.get(
                        (event_name, id(handler)), "throw"
                    )
                    self._hooks.on(
                        event_name,
                        handler,
                        source=extension.name,
                        error_mode=mode,
                    )
            for cleanup in extension.cleanups:
                self._hooks.add_cleanup(cleanup)

        self._steering_queue = _MessageQueue(options.steering_mode)
        self._follow_up_queue = _MessageQueue(options.follow_up_mode)
        self._idle_event = asyncio.Event()
        self._idle_event.set()

    # === Public properties ===

    @property
    def phase(self) -> AgentHarnessPhase:
        return self._phase

    @property
    def hooks(self) -> HookBus:
        return self._hooks

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def runtime(self) -> _ExtensionRuntime:
        return self._runtime

    @property
    def is_idle(self) -> bool:
        return self._phase == "idle"

    @property
    def messages(self) -> list[AgentMessage]:
        return self._state.messages

    # === Subscription ===

    def subscribe(self, listener: HarnessListener) -> Callable[[], None]:
        """Register an :class:`AgentEvent` listener (mirrors ``Agent.subscribe``)."""

        self._listeners.append(listener)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe

    # === Driving the loop ===

    async def prompt(self, text: str) -> list[AgentMessage]:
        if self._phase != "idle":
            raise AgentHarnessError(
                "busy",
                "AgentHarness is busy; use steer()/follow_up() while in a turn.",
            )
        # Flip phase synchronously BEFORE the first await so concurrent callers
        # see the guard immediately (C-2 re-entrancy fix).
        self._phase = "turn"
        self._idle_event.clear()
        try:
            user_msg = UserMessage(content=[TextContent(text=text)])
            # Fire the before_agent_start hook so extensions can inject messages
            # or rewrite the system prompt before the first turn.
            injected = await self._emit_before_agent_start(text)
            prompts: list[AgentMessage] = []
            if injected and injected.messages:
                prompts.extend(injected.messages)
            prompts.append(user_msg)
            system_prompt = (
                injected.system_prompt
                if injected and injected.system_prompt is not None
                else self._state.system_prompt
            )
            return await self._run(prompts, system_prompt=system_prompt)
        except Exception:
            # If anything before _run raises, reset phase so the harness is
            # usable again (note: _run resets phase in its own finally block).
            if self._phase == "turn":
                self._phase = "idle"
                self._idle_event.set()
            raise

    async def steer(self, text: str) -> None:
        # D.1.10: enqueue regardless of phase — Pi parity.
        self._steering_queue.enqueue(
            UserMessage(content=[TextContent(text=text)])
        )

    async def follow_up(self, text: str) -> None:
        self._follow_up_queue.enqueue(
            UserMessage(content=[TextContent(text=text)])
        )

    async def abort(self) -> None:
        """Signal a cooperative abort. Hook handlers should check the signal."""

        self._abort_requested = True
        self._steering_queue.clear()
        self._follow_up_queue.clear()

    async def wait_for_idle(self) -> None:
        await self._idle_event.wait()

    async def dispose(self) -> None:
        """Tear down the harness — abort + wait + cleanups LIFO (D.1.13 M-4)."""

        if self._phase != "idle":
            await self.abort()
            with contextlib.suppress(Exception):
                await self.wait_for_idle()
        await self._hooks.dispose()
        # Invalidate AFTER cleanups so cleanup callables can still inspect ctx.
        self._runtime.invalidate("AgentHarness has been disposed")

    # === Internal: callback bridges ===

    def _make_context(self) -> ExtensionContext:
        """Build a fresh :class:`ExtensionContext` for the current hook emit."""

        from aelix_coding_agent.extensions.api import ExtensionContext as _ExtensionContext

        return _ExtensionContext(
            self._runtime,
            cwd=self._options.cwd,
            model=self._state.model,
            is_idle=lambda: self._phase == "idle",
            abort=lambda: self._mark_abort(),
            get_active_tools=self._action_get_active_tools,
            get_system_prompt=self._action_get_system_prompt,
        )

    def _mark_abort(self) -> None:
        self._abort_requested = True

    def _current_system_prompt(self) -> str:
        """F-10: prefer per-turn snapshot, else fall back to long-lived state."""

        if self._turn_state is not None:
            return self._turn_state.system_prompt
        return self._state.system_prompt

    def _action_get_active_tools(self) -> list[str]:
        # F-9: ``active_tool_names is None`` means "every registered tool is
        # active"; otherwise return registered tools filtered by the set.
        if self._state.active_tool_names is None:
            return [tool.name for tool in self._state.tools]
        active = set(self._state.active_tool_names)
        return [tool.name for tool in self._state.tools if tool.name in active]

    def _action_set_active_tools(self, names: list[str]) -> None:
        # F-9: non-destructive — record the active filter, don't drop tools.
        known = {t.name for t in self._state.tools}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise AgentHarnessError(
                "invalid_argument",
                f"set_active_tools: unknown tool name(s): {unknown!r}",
            )
        self._state.active_tool_names = list(names)

    def _action_get_system_prompt(self) -> str:
        return self._current_system_prompt()

    async def _before_tool_call_bridge(
        self, ctx: BeforeToolCallContext
    ) -> BeforeToolCallResult | None:
        """Translate the loop callback into a ``tool_call`` hook emit.

        Per D.1.5 we pass ``ctx.args`` by reference — no defensive copy. This
        is the contract that lets a handler mutate the dict and have the
        mutation reach ``tool.execute`` and the after-callback.
        """

        hook_event = ToolCallHookEvent(
            tool_call_id=ctx.tool_call.tool_call_id,
            tool_name=ctx.tool_call.tool_name,
            args=ctx.args,  # SAME REFERENCE — see D.1.5
            assistant_message=ctx.assistant_message,
            context=ctx.context,
        )
        try:
            result = await self._hooks.emit(hook_event)
        except Exception as exc:
            raise AgentHarnessError(
                "hook",
                f"tool_call hook handler raised: {exc}",
            ) from exc
        if isinstance(result, ToolCallResult) and result.block:
            return BeforeToolCallResult(block=True, reason=result.reason)
        return None

    async def _after_tool_call_bridge(
        self, ctx: AfterToolCallContext
    ) -> AfterToolCallResult | None:
        """Translate the loop callback into a ``tool_result`` hook emit.

        Composition order per D.1.6: hook patches FIRST, then the
        application-supplied ``after_tool_call`` override runs on top.
        """

        hook_event = ToolResultHookEvent(
            tool_call_id=ctx.tool_call.tool_call_id,
            tool_name=ctx.tool_call.tool_name,
            args=ctx.args,
            content=list(ctx.result.content),
            details=ctx.result.details,
            is_error=ctx.result.is_error,
        )
        try:
            hook_patch = await self._hooks.emit(hook_event)
        except Exception as exc:
            raise AgentHarnessError(
                "hook",
                f"tool_result hook handler raised: {exc}",
            ) from exc

        combined: AfterToolCallResult | None = (
            hook_patch if isinstance(hook_patch, ToolResultPatch) else None
        )

        app_callback = self._options.after_tool_call
        if app_callback is not None:
            raw = app_callback(ctx)
            if inspect.isawaitable(raw):
                raw = await raw
            if isinstance(raw, AfterToolCallResult):
                if combined is None:
                    combined = raw
                else:
                    combined = AfterToolCallResult(
                        content=raw.content if raw.content is not None else combined.content,
                        details=raw.details if raw.details is not None else combined.details,
                        is_error=(
                            raw.is_error
                            if raw.is_error is not None
                            else combined.is_error
                        ),
                        terminate=(
                            raw.terminate
                            if raw.terminate is not None
                            else combined.terminate
                        ),
                    )
        return combined

    async def _transform_context_bridge(
        self,
        messages: list[AgentMessage],
        _signal: Any,
    ) -> list[AgentMessage]:
        """Emit ``context`` hook, then chain the app-supplied transform."""

        try:
            result = await self._hooks.emit(ContextHookEvent(messages=list(messages)))
        except Exception as exc:
            raise AgentHarnessError(
                "hook",
                f"context hook handler raised: {exc}",
            ) from exc
        out = (
            list(result.messages)
            if isinstance(result, ContextResult) and result.messages is not None
            else list(messages)
        )
        if self._options.transform_context is not None:
            raw = self._options.transform_context(out, _signal)
            if inspect.isawaitable(raw):
                raw = await raw
            if raw is not None:
                out = list(raw)
        return out

    async def _emit_before_agent_start(
        self, prompt: str
    ) -> BeforeAgentStartResult | None:
        try:
            result = await self._hooks.emit(
                BeforeAgentStartHookEvent(
                    prompt=prompt,
                    system_prompt=self._state.system_prompt,
                )
            )
        except Exception as exc:
            raise AgentHarnessError(
                "hook",
                f"before_agent_start hook handler raised: {exc}",
            ) from exc
        return result if isinstance(result, BeforeAgentStartResult) else None

    # === Run loop ===

    async def _run(
        self,
        prompts: list[AgentMessage],
        *,
        system_prompt: str,
    ) -> list[AgentMessage]:
        self._phase = "turn"
        self._abort_requested = False
        self._idle_event.clear()
        # F-10: install per-turn snapshot so ``_current_system_prompt`` returns
        # the chained value resolved by ``before_agent_start`` for the duration
        # of this turn only.
        self._turn_state = _TurnState(
            system_prompt=system_prompt,
            model=self._state.model,
        )
        try:
            config = AgentLoopConfig(
                model=self._state.model,
                convert_to_llm=(
                    self._options.convert_to_llm or default_convert_to_llm
                ),
                transform_context=self._transform_context_bridge,
                get_api_key=self._options.get_api_key,
                get_steering_messages=self._drain_steering,
                get_follow_up_messages=self._drain_follow_up,
                before_tool_call=self._before_tool_call_bridge,
                after_tool_call=self._after_tool_call_bridge,
            )
            # F-9: apply the active-tool filter without mutating ``_state.tools``.
            active = self._state.active_tool_names
            if active is None:
                active_tools = list(self._state.tools)
            else:
                active_set = set(active)
                active_tools = [t for t in self._state.tools if t.name in active_set]
            context = AgentContext(
                system_prompt=system_prompt,
                messages=list(self._state.messages),
                tools=active_tools,
            )

            async def emit(event: AgentEvent) -> None:
                # Dispatch to local listeners first.
                for listener in list(self._listeners):
                    try:
                        raw = listener(event)
                        if inspect.isawaitable(raw):
                            await raw
                    except Exception:  # noqa: BLE001 — listener errors must not break
                        _log.debug("listener raised", exc_info=True)
                # Then fan-out to the hook bus as observational lifecycle events.
                # ADR-0030: ``_to_hook_event`` returns ``HookEvent`` (not
                # ``HookEvent | None``) — every AgentEvent has a 1:1 projection.
                hook_payload = _to_hook_event(event)
                try:
                    await self._hooks.emit(hook_payload)
                except Exception as exc:  # noqa: BLE001
                    # Listener-style projection: lifecycle hook errors are
                    # swallowed so a faulty observer cannot break the loop.
                    # Matches Pi ``subscribe()`` behavior
                    # (``agent-harness.ts:649-660``).
                    _log.debug(
                        "lifecycle hook handler raised: %r", exc, exc_info=True
                    )

            try:
                new_messages = await agent_loop(
                    prompts,
                    context,
                    config,
                    emit=emit,
                    stream_fn=self._options.stream_fn,
                )
            except AgentHarnessError as exc:
                # Pi parity: synthesize failure assistant message + emit closure events.
                failure = AssistantMessage(
                    content=[TextContent(text=f"[error] {exc}")],
                    stop_reason="error",
                    error_message=str(exc),
                )
                self._state.messages.append(failure)
                for closure_event in (
                    MessageStartEvent(message=failure),
                    MessageEndEvent(message=failure),
                    TurnEndEvent(message=failure, tool_results=[]),
                    AgentEndEvent(messages=list(self._state.messages)),
                ):
                    try:
                        await emit(closure_event)
                    except Exception as emit_exc:  # noqa: BLE001
                        _log.debug("emit during hook-fail close-out raised: %r", emit_exc, exc_info=True)
                raise
            self._state.messages.extend(new_messages)
            # Settled event lets observers know we're back to idle.
            try:
                await self._hooks.emit(SettledHookEvent())
            except Exception as exc:  # noqa: BLE001
                _log.debug("settled hook handler raised: %r", exc, exc_info=True)
            return new_messages
        finally:
            self._phase = "idle"
            self._turn_state = None
            self._idle_event.set()

    async def _drain_steering(self) -> list[AgentMessage]:
        return self._steering_queue.drain()

    async def _drain_follow_up(self) -> list[AgentMessage]:
        return self._follow_up_queue.drain()


# === AgentEvent → HookEvent mapping ===


def _to_hook_event(event: AgentEvent) -> HookEvent:
    """Project a low-level :class:`AgentEvent` onto its observational hook event.

    This is the single, canonical translation point between the loop's
    stream-level :class:`AgentEvent` union and the harness's lifecycle
    :class:`HookEvent` union (ADR-0036). After Sprint 3a, projects 10 loop
    names into the 28-name HookEvent union; the 18 own-events are emitted
    directly by the harness, not via projection.

    ADR-0030: this function uses ``match event.type:`` + ``assert_never`` so
    pyright fails the build if a new :data:`AgentEvent` variant is added
    without a corresponding case here. The return type is :class:`HookEvent`
    (not ``HookEvent | None``) — every :data:`AgentEvent` has a 1:1 hook
    projection.
    """

    match event.type:
        case "agent_start":
            return AgentStartHookEvent()
        case "agent_end":
            return AgentEndHookEvent(messages=list(event.messages))
        case "turn_start":
            return TurnStartHookEvent()
        case "turn_end":
            return TurnEndHookEvent(message=event.message)
        case "message_start":
            return MessageStartHookEvent(message=event.message)
        case "message_update":
            return MessageUpdateHookEvent(message=event.message)
        case "message_end":
            return MessageEndHookEvent(message=event.message)
        case "tool_execution_start":
            return ToolExecutionStartHookEvent(
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                args=event.args,
            )
        case "tool_execution_update":
            return ToolExecutionUpdateHookEvent(
                tool_call_id=event.tool_call_id,
                partial_result=event.partial_result,
                tool_name=event.tool_name,
                args=event.args,
            )
        case "tool_execution_end":
            return ToolExecutionEndHookEvent(
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                is_error=event.is_error,
            )
        case _ as unreachable:
            assert_never(unreachable)


__all__ = [
    "AgentHarness",
    "AgentHarnessError",
    "AgentHarnessOptions",
    "AgentHarnessPhase",
    "HarnessListener",
]
