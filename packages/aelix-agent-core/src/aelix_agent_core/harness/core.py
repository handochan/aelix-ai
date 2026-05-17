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

from aelix_ai.messages import AssistantMessage, ImageContent, TextContent, UserMessage
from aelix_ai.streaming import Model, StreamFn

from aelix_agent_core.agent import AgentListener
from aelix_agent_core.default_convert import default_convert_to_llm
from aelix_agent_core.harness.hooks import (
    AbortHookEvent,
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
    ModelSelectHookEvent,
    QueueUpdateHookEvent,
    ResourcesUpdateHookEvent,
    SavePointHookEvent,
    SessionBeforeCompactHookEvent,
    SessionBeforeCompactResult,
    SessionBeforeTreeHookEvent,
    SessionBeforeTreeResult,
    SessionCompactHookEvent,
    SessionTreeHookEvent,
    SettledHookEvent,
    ThinkingLevelSelectHookEvent,
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
    ToolExecutionMode,
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

    from aelix_agent_core.session import Session
    from aelix_agent_core.session.compaction import CompactResult


_log = logging.getLogger(__name__)

AgentHarnessPhase = Literal["idle", "turn", "compaction", "branch_summary"]
"""Sprint 4b (Phase 2.2.2 — ADR-0023): Phase Literal expanded.

Pi ``types.ts:262`` defines 5 values; Pi's ``"retry"`` is declared-but-unused
at SHA ``734e08e``, so Aelix omits it (P-15 — Aelix-additive omission tracked
in ADR-0023 §"Aelix-additive divergences"). The four shipped values mirror
Pi's transitions exactly:

- ``"idle"`` — no work in flight; prompt / steer / follow_up / compact /
  navigate_tree all legal.
- ``"turn"`` — :meth:`AgentHarness.prompt` is running.
- ``"compaction"`` — :meth:`AgentHarness.compact` is running.
- ``"branch_summary"`` — :meth:`AgentHarness.navigate_tree` is running.

All four method paths guard with ``raise AgentHarnessError("busy", ...)`` when
the phase is anything other than ``"idle"``.
"""
HarnessListener = AgentListener  # type alias — same shape as AgentListener


class AgentHarnessError(Exception):
    """Errors surfaced by the harness itself (busy/state/argument/hook)."""

    def __init__(
        self,
        code: Literal["busy", "invalid_state", "invalid_argument", "hook", "unknown", "compaction"],
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

    # === F-6 placeholders (Pi parity with AgentHarnessOptions) ===
    # Pi line citations are relative to SHA 734e08edf82ff315bc3d96472a6ebfa69a1d8016.
    # Sprint 3b wires 4 of the 7 placeholders into AgentState; the remaining 3
    # stay inert until owning ADRs land (Phase 2.2 / Phase 4).
    # Sprint 4a (ADR-0022): narrowed from ``Any | None`` to ``Session | None``.
    # Pi parity: ``AgentHarnessOptions.session`` (``types.ts:790``). When ``None``
    # the harness operates in the Aelix-additive backward-compat fallback path
    # (no append-on-message_end; flush dispatcher drops with debug log).
    session: Session | None = None  # Pi: types.ts:790
    env: dict[str, str] | None = None  # Phase 4 / Phase 2.2 deferred — ExecutionEnv ADR TBD. Pi: types.ts:562
    # Sprint 3b wired: resources is now dict[str, Any] (Pi type fix — see spec §D)
    # and flows into AgentState.resources at __init__ time.
    resources: dict[str, Any] | None = None  # Sprint 3b wired — flows into AgentState.resources. Pi: types.ts:565
    thinking_level: str | None = None  # Sprint 3b wired — flows into AgentState.thinking_level. Pi: types.ts:~576
    active_tool_names: list[str] | None = None  # Sprint 3b wired — flows via F-9 validator path. Pi: types.ts:~577
    get_api_key_and_headers: Callable[..., Any] | None = None  # Phase 4 / Phase 2.2 deferred — ADR-0038 provider. Pi: types.ts:~571
    stream_options: dict[str, Any] | None = None  # Sprint 3b wired — flows into AgentState.stream_options. Pi: types.ts:~574

    # === Sprint 3c (Phase 2.1.3) — parallel tool execution ===
    # Pi parity (types.ts:226-232): default "parallel". A single tool with
    # ``execution_mode == "sequential"`` downgrades the whole batch to
    # sequential at the loop dispatcher (agent-loop.ts:380-387).
    tool_execution: ToolExecutionMode = "parallel"

    # === Sprint 4b (Phase 2.2.2) — Aelix-additive test-only seams ===
    # Per P-14 (W1 finding), Aelix does NOT add Pi-divergent summarizer
    # callbacks to the public surface. These two underscore-prefixed fields
    # are explicit test-only seams documented in ADR-0023 §"Aelix-additive
    # divergences" — production callers MUST leave them as ``None`` and rely
    # on ``get_api_key_and_headers`` per Pi parity.
    _summarizer_override: Any | None = None
    _branch_summarizer_override: Any | None = None


# === Sprint 4a — pending session writes (Pi parity, agent-harness.ts:414-432 + 459-481) ===
#
# Pi defers state mutations that happen DURING a turn (set_model, set_thinking_level,
# append_message) onto a per-harness ``pendingSessionWrites`` queue. The queue is
# drained when the turn ends.
#
# **P-11 LOAD-BEARING REVERSAL (Sprint 4a):** Sprint 3b W4 MAJOR-1 introduced a
# ``PendingActiveToolsChangeWrite`` variant claiming Pi pushes an active-set
# change onto ``pendingSessionWrites``. **Verified at SHA 734e08e**: Pi
# ``setActiveTools`` (``agent-harness.ts:875-882``) does NOT push to the
# queue — it only validates and assigns. Pi ``flushPendingSessionWrites``
# (``agent-harness.ts:459-481``) has NO ``active_tools_change`` case. The
# variant has been **deleted** + the push site in ``set_active_tools`` has
# been removed. The active-set is a per-process runtime concept in Pi —
# restoration is via ``options.activeToolNames`` on harness construction,
# not session replay. See ADR-0022 §"Removed claims".
#
# **P-12:** Pi pending union is 8 dispatcher arms but only 3 push sites
# exist (message / model_change / thinking_level_change). Sprint 4a ships
# 8 dispatcher arms for defensive completeness; only 3 push sites exist
# (matching Pi exactly). The 5 unreachable arms are tested via synthetic
# injection in ``test_session_pending_writes_integration.py``.


@dataclass(frozen=True)
class PendingMessageWrite:
    """Pi push site: ``agent-harness.ts:674`` (``appendMessage`` during turn)."""

    message: AgentMessage
    type: Literal["message"] = "message"


@dataclass(frozen=True)
class PendingModelChangeWrite:
    """Pi push site: ``agent-harness.ts:851`` (``setModel`` during turn)."""

    provider: str
    model_id: str
    type: Literal["model_change"] = "model_change"


@dataclass(frozen=True)
class PendingThinkingLevelChangeWrite:
    """Pi push site: ``agent-harness.ts:866`` (``setThinkingLevel`` during turn)."""

    thinking_level: str
    type: Literal["thinking_level_change"] = "thinking_level_change"


@dataclass(frozen=True)
class PendingCustomWrite:
    """Defensive flush arm — Pi has no push site for ``custom`` at this SHA.

    The variant exists so the 8-arm dispatcher in
    ``flush_pending_session_writes`` matches Pi
    ``agent-harness.ts:459-481`` exactly. Future Pi versions or
    Aelix-additive callers may inject one via the queue directly.
    """

    custom_type: str
    data: Any | None = None
    type: Literal["custom"] = "custom"


@dataclass(frozen=True)
class PendingCustomMessageWrite:
    """Defensive flush arm — Pi has no push site for ``custom_message``."""

    custom_type: str
    content: Any = None
    display: bool = True
    details: Any | None = None
    type: Literal["custom_message"] = "custom_message"


@dataclass(frozen=True)
class PendingLabelWrite:
    """Defensive flush arm — Pi has no push site for ``label``."""

    target_id: str
    label: str | None = None
    type: Literal["label"] = "label"


@dataclass(frozen=True)
class PendingSessionInfoWrite:
    """Defensive flush arm — Pi has no push site for ``session_info``."""

    name: str | None = None
    type: Literal["session_info"] = "session_info"


@dataclass(frozen=True)
class PendingLeafWrite:
    """Defensive flush arm — Pi has no push site for ``leaf``."""

    target_id: str | None = None
    type: Literal["leaf"] = "leaf"


PendingSessionWrite = (
    PendingMessageWrite
    | PendingModelChangeWrite
    | PendingThinkingLevelChangeWrite
    | PendingCustomWrite
    | PendingCustomMessageWrite
    | PendingLabelWrite
    | PendingSessionInfoWrite
    | PendingLeafWrite
)


@dataclass(frozen=True)
class NavigateTreeOptions:
    """Pi ``NavigateTreeOptions`` (``types.ts:269-273``, Sprint 4b §C).

    Mirrors Pi exactly. All four fields are optional; the caller's call site
    typically provides ``summarize=True`` + ``custom_instructions=...``.
    """

    summarize: bool = False
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


@dataclass(frozen=True)
class NavigateTreeResult:
    """Pi ``NavigateTreeResult`` (``types.ts:269-273``, Sprint 4b §C).

    ``cancelled=True`` is returned when either the ``session_before_tree``
    hook handler short-circuited via ``SessionBeforeTreeResult(cancel=True)``
    OR ``target_id is None`` and there is nothing to navigate to.
    """

    cancelled: bool
    editor_text: str | None = None
    summary_entry: Any | None = None


@dataclass
class _TurnState:
    """Per-turn snapshot of state values resolved before ``_run`` (F-10).

    The harness rebuilds this on every :meth:`AgentHarness.prompt` call so that
    state mutations made by ``before_agent_start`` (e.g. chained system prompt)
    do not leak into subsequent turns. Outside of a turn, callers fall back to
    the long-lived ``self._state``.

    Sprint 4b (ADR-0025 §"Pending extensions") extends the snapshot with
    ``messages`` + ``session_id`` per ADR-0022 §"Sprint 4a → 4b transition
    plan". When :class:`Session` is attached the harness derives
    ``messages`` from ``session.build_context().messages`` at turn start;
    otherwise it copies ``state.messages`` (in-memory primary, backward
    compat per ADR-0022 §"Aelix-additive divergences" item 3).
    """

    system_prompt: str
    model: Model
    messages: list[AgentMessage] = field(default_factory=list)
    session_id: str | None = None


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
        from aelix_coding_agent.extensions.api import _ExtensionRuntime

        self._options = options
        self._runtime = options.runtime or _ExtensionRuntime()
        # Sprint 4a — Session is owned by the caller; we just hold a
        # reference and route writes/append_message through it. ``None``
        # keeps the Aelix backward-compat path (no session = drop
        # non-message variants with a debug log).
        self._session = options.session
        self._extensions: list[Extension] = list(options.extensions)
        self._listeners: list[HarnessListener] = []
        self._phase: AgentHarnessPhase = "idle"
        self._abort_requested = False
        # F-10: per-turn snapshot rebuilt at every prompt(). None when idle.
        self._turn_state: _TurnState | None = None
        # Sprint 3c §C.2 — track the in-flight turn task so abort() can call
        # ``task.cancel()`` on it. Aelix additive over Pi (Pi does NOT cancel
        # ``Promise.all`` on tool error). None when idle.
        self._current_turn_task: asyncio.Task[Any] | None = None

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
        # Sprint 4a — populate ``state.session_id`` from the attached
        # Session's metadata when present (Pi parity:
        # ``before_provider_request`` uses this). The metadata is stored
        # eagerly by every ``SessionStorage`` impl (Memory / Jsonl), so the
        # internal ``_metadata`` attribute is safe to peek synchronously.
        # ``get_metadata()`` itself is async per Pi Protocol — we do not
        # call it here because ``__init__`` cannot ``await``.
        if self._session is not None:
            storage = self._session.get_storage()
            metadata = getattr(storage, "_metadata", None)
            if metadata is not None:
                self._state.session_id = metadata.id

        # Sprint 3b — F-6 placeholder wire-up (§D). Pi parity: AgentHarness
        # constructor seeds these from options into state. ``active_tool_names``
        # routes through the F-9 validator path so unknown names raise at
        # construction time rather than reaching the loop.
        if options.thinking_level is not None:
            self._state.thinking_level = options.thinking_level
        if options.resources is not None:
            self._state.resources = dict(options.resources)
        if options.stream_options is not None:
            self._state.stream_options = dict(options.stream_options)

        # Action table installed for ExtensionContext + ExtensionAPI.
        # Sprint 5a (Phase 3.1, P-22 / P-28): the 15-method action surface
        # mixes real bindings (set/get_session_name, set_label, set_model,
        # set/get_thinking_level, get_all_tools, exec) with throwing stubs
        # for 5b/Phase 4 actions (send_message, send_user_message,
        # append_entry, get_commands). Real bindings delegate to the
        # ``self._action_*`` helpers defined below.
        from aelix_coding_agent.extensions.api import (
            ExtensionRuntimeActions as _RuntimeActions,
        )
        from aelix_coding_agent.extensions.api import (
            _make_throwing_stub as _stub,
        )

        self._runtime.bind_core(
            _RuntimeActions(
                # Sprint 3a originals.
                get_active_tools=self._action_get_active_tools,
                set_active_tools=self._action_set_active_tools,
                get_system_prompt=self._action_get_system_prompt,
                # Sprint 5a (Phase 3.1) — real bindings.
                set_session_name=self._action_set_session_name,
                get_session_name=self._action_get_session_name,
                set_label=self._action_set_label,
                set_model=self._action_set_model,
                get_thinking_level=self._action_get_thinking_level,
                set_thinking_level=self._action_set_thinking_level,
                get_all_tools=self._action_get_all_tools,
                exec=_stub("exec"),  # ExtensionAPI falls back to subprocess port.
                # Sprint 5b deferred — throwing stubs (ADR-0042).
                send_message=_stub("send_message"),
                send_user_message=_stub("send_user_message"),
                append_entry=_stub("append_entry"),
                get_commands=_stub("get_commands"),
            )
        )

        # Wire active_tool_names AFTER bind_core so the F-9 validator has the
        # populated tool list to check names against. Pi parity:
        # agent-harness.ts initialises active set during construction.
        if options.active_tool_names is not None:
            self._action_set_active_tools(list(options.active_tool_names))

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
        # Sprint 3b — next_turn queue + pending_session_writes (Pi parity,
        # agent-harness.ts:172 + 466-472).
        self._next_turn_queue: list[AgentMessage] = []
        self._pending_session_writes: list[PendingSessionWrite] = []
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
        # Sprint 4b §A: guard covers all non-idle phases (turn / compaction /
        # branch_summary). steer()/follow_up() remain enqueue-only per Pi
        # parity (Pi ``agent-harness.ts`` steer paths enqueue regardless of
        # phase). See ADR-0023 phase-machine table.
        if self._phase != "idle":
            raise AgentHarnessError(
                "busy",
                f"AgentHarness is busy (phase={self._phase!r}); use "
                "steer()/follow_up() while in a turn.",
            )
        # Flip phase synchronously BEFORE the first await so concurrent callers
        # see the guard immediately (C-2 re-entrancy fix).
        self._phase = "turn"
        self._idle_event.clear()
        try:
            user_msg = UserMessage(content=[TextContent(text=text)])
            # Sprint 3b — drain the next_turn queue (Pi parity, executeTurn
            # L466-472): messages queued via ``next_turn()`` while idle (or
            # during the previous turn) are prepended to this turn's prompt.
            drained_next = self._next_turn_queue
            self._next_turn_queue = []
            # F-3b-3 (W5 should-fix): Pi emits ``queue_update`` when the
            # next_turn queue is drained at start of the next turn (Pi
            # ``executeTurn`` L487). Emit before ``before_agent_start`` so
            # observers see the empty queue snapshot consistent with Pi.
            if drained_next:
                await self._emit_queue_update()
            # Fire the before_agent_start hook so extensions can inject messages
            # or rewrite the system prompt before the first turn.
            injected = await self._emit_before_agent_start(text)
            prompts: list[AgentMessage] = []
            if injected and injected.messages:
                prompts.extend(injected.messages)
            prompts.extend(drained_next)
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
        # Sprint 3b — enqueue paths emit ``queue_update`` (P-4: setters do
        # NOT, only enqueue paths do). Pi: ``agent-harness.ts`` steer path.
        await self._emit_queue_update()

    async def follow_up(self, text: str) -> None:
        self._follow_up_queue.enqueue(
            UserMessage(content=[TextContent(text=text)])
        )
        await self._emit_queue_update()

    async def abort(self) -> None:
        """Signal a cooperative abort. Hook handlers should check the signal.

        Sprint 3c §C.2 (Aelix additive over Pi): when a turn task is
        in-flight, call ``task.cancel()`` on it so any tool that ignores the
        cooperative signal still gets unwound. Pi does NOT cancel
        ``Promise.all`` on tool error; this is a strict superset (Pi-observing
        tools would have cancelled anyway via the signal).

        Race-safety: read of ``self._current_turn_task`` → local copy →
        ``cancel()`` on a done-or-already-cancelled Task is a no-op in
        asyncio. Single-threaded event loop guarantees no torn reads.

        Sprint 3d (P-10 closure): emit a dedicated ``AbortHookEvent`` carrying
        the snapshot of messages that were cleared from the steer and
        follow_up queues. Pi parity: ``agent-harness.ts`` ``abort()`` emits an
        ``abort`` own-event with ``clearedSteer`` / ``clearedFollowUp`` arrays
        captured BEFORE the queue ``clear()`` calls. ``queue_update`` is also
        emitted (post-clear) so observers can see both the explicit
        ``abort`` lifecycle signal AND the resulting empty-queue state.
        """

        self._abort_requested = True
        # Capture pre-clear snapshots for AbortHookEvent (P-10 closure).
        cleared_steer = list(self._steering_queue._messages)
        cleared_follow_up = list(self._follow_up_queue._messages)
        self._steering_queue.clear()
        self._follow_up_queue.clear()
        # Sprint 3c §C.2 — cancel in-flight turn task if any.
        turn_task = self._current_turn_task
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
        # Sprint 3d (P-10) — emit dedicated abort lifecycle event with the
        # cleared-queue snapshots. Closes the last Phase 2.1 emit-site gap.
        try:
            await self._hooks.emit(
                AbortHookEvent(
                    cleared_steer=cleared_steer,
                    cleared_follow_up=cleared_follow_up,
                )
            )
        except Exception as exc:
            raise AgentHarnessError(
                "hook",
                f"abort hook handler raised: {exc}",
            ) from exc
        # Sprint 3b — abort clears queues; emit ``queue_update`` so observers
        # see the empty state. Pi parity (``agent-harness.ts`` abort path).
        await self._emit_queue_update()

    # === Sprint 4b §B/§C — compact() + navigate_tree() ===

    async def compact(self, custom_instructions: str | None = None) -> CompactResult:
        """Pi ``compact()`` (``agent-harness.ts:689-745``, Sprint 4b §B).

        Phase flow:

        1. Guard: raise :class:`AgentHarnessError("busy")` if not idle.
        2. Flip ``self._phase`` to ``"compaction"``; clear ``_idle_event``.
        3. Build :class:`CompactionPreparation` from current branch entries.
        4. Emit :class:`SessionBeforeCompactHookEvent` carrying
           ``{preparation, branch_entries, custom_instructions, signal}``
           (P-17 payload extension).
        5. If the reducer returned ``SessionBeforeCompactResult(cancel=True)``
           → raise ``AgentHarnessError("invalid_state", "Compaction cancelled")``.
        6. If the reducer substituted ``compaction=CompactResult(...)`` → use
           it directly (skip LLM call, ``from_hook=True``).
        7. Else call :func:`compaction.compact` with ``self._state.model`` +
           ``options.get_api_key_and_headers`` (or ``_summarizer_override``
           for tests).
        8. Persist via :meth:`Session.append_compaction` and emit
           :class:`SessionCompactHookEvent`.
        9. ``finally``: restore ``self._phase = "idle"`` + set idle event.
        """

        from aelix_agent_core.session.compaction import (
            compact as compaction_compact,
        )
        from aelix_agent_core.session.compaction import (
            prepare_compaction,
        )

        if self._phase != "idle":
            raise AgentHarnessError(
                "busy",
                f"compact() requires idle harness (phase={self._phase!r})",
            )
        if self._session is None:
            raise AgentHarnessError(
                "invalid_state",
                "compact() requires options.session to be attached",
            )
        self._phase = "compaction"
        self._idle_event.clear()
        try:
            branch_entries = await self._session.get_branch()
            preparation = prepare_compaction(branch_entries, custom_instructions)
            if preparation is None:
                raise AgentHarnessError(
                    "invalid_state", "Nothing to compact"
                )
            try:
                hook_result = await self._hooks.emit(
                    SessionBeforeCompactHookEvent(
                        preparation=preparation,
                        branch_entries=list(branch_entries),
                        custom_instructions=custom_instructions,
                        signal=None,
                    )
                )
            except Exception as exc:
                raise AgentHarnessError(
                    "hook",
                    f"session_before_compact hook handler raised: {exc}",
                ) from exc
            if isinstance(hook_result, SessionBeforeCompactResult) and hook_result.cancel:
                raise AgentHarnessError(
                    "compaction",
                    hook_result.reason or "Compaction cancelled",
                )
            provided: CompactResult | None = None
            if isinstance(hook_result, SessionBeforeCompactResult):
                provided = hook_result.compaction
            if provided is not None:
                result = provided
                from_hook = True
            else:
                result = await compaction_compact(
                    self._state.model,
                    self._options.get_api_key_and_headers,
                    preparation,
                    custom_instructions,
                    _summarizer_override=self._options._summarizer_override,
                )
                from_hook = False
            entry_id = await self._session.append_compaction(
                summary=result.summary,
                first_kept_entry_id=result.first_kept_entry_id,
                tokens_before=result.tokens_before,
                details=result.details,
                from_hook=from_hook,
            )
            entry = await self._session.get_entry(entry_id)
            if entry is not None and entry.type == "compaction":
                try:
                    await self._hooks.emit(
                        SessionCompactHookEvent(
                            compaction_entry=entry,
                            from_hook=from_hook,
                        )
                    )
                except Exception as exc:
                    raise AgentHarnessError(
                        "hook",
                        f"session_compact hook handler raised: {exc}",
                    ) from exc
            return result
        finally:
            self._phase = "idle"
            self._idle_event.set()

    async def navigate_tree(
        self,
        target_id: str | None,
        options: NavigateTreeOptions | None = None,
    ) -> NavigateTreeResult:
        """Pi ``navigateTree()`` (``agent-harness.ts:747-867``, Sprint 4b §C).

        Phase flow:

        1. Guard busy → raise :class:`AgentHarnessError("busy")` if not idle.
        2. Flip ``self._phase`` to ``"branch_summary"``; clear idle event.
        3. ``target_id is None`` → noop, return ``cancelled=False``.
        4. Resolve target entry. ``user_message`` / ``custom_message`` →
           editor branch: extract text + parent_id (Pi ``:760-780``).
        5. Build :class:`BranchSummaryPreparation`; emit
           :class:`SessionBeforeTreeHookEvent` (P-18 payload with ``signal``).
        6. If reducer returned ``cancel=True`` → return ``cancelled=True``.
        7. If ``options.summarize`` AND no hook-provided summary AND entries
           are non-empty → call :func:`generate_branch_summary`.
        8. :meth:`Session.move_to` with the summary dict.
        9. Emit :class:`SessionTreeHookEvent` (P-19 ``new_leaf_id: str | None``).
        10. ``finally``: restore ``self._phase = "idle"``.
        """

        from aelix_agent_core.session.branch_summarization import (
            BranchSummaryPreparation,
            collect_entries_for_branch_summary,
            generate_branch_summary,
        )

        if self._phase != "idle":
            raise AgentHarnessError(
                "busy",
                f"navigate_tree() requires idle harness (phase={self._phase!r})",
            )
        if self._session is None:
            raise AgentHarnessError(
                "invalid_state",
                "navigate_tree() requires options.session to be attached",
            )
        if target_id is None:
            return NavigateTreeResult(cancelled=False)
        self._phase = "branch_summary"
        self._idle_event.clear()
        try:
            old_leaf_id = await self._session.get_leaf_id()
            if old_leaf_id == target_id:
                return NavigateTreeResult(cancelled=False)
            target_entry = await self._session.get_entry(target_id)
            if target_entry is None:
                raise AgentHarnessError(
                    "invalid_argument", f"Entry {target_id} not found"
                )
            entries, common_ancestor_id = await collect_entries_for_branch_summary(
                self._session, old_leaf_id, target_id
            )
            opts = options or NavigateTreeOptions()
            preparation = BranchSummaryPreparation(
                target_id=target_id,
                old_leaf_id=old_leaf_id,
                common_ancestor_id=common_ancestor_id,
                entries_to_summarize=list(entries),
                user_wants_summary=opts.summarize,
                custom_instructions=opts.custom_instructions,
                replace_instructions=opts.replace_instructions,
                label=opts.label,
            )
            try:
                hook_result = await self._hooks.emit(
                    SessionBeforeTreeHookEvent(
                        preparation=preparation,
                        signal=None,
                    )
                )
            except Exception as exc:
                raise AgentHarnessError(
                    "hook",
                    f"session_before_tree hook handler raised: {exc}",
                ) from exc
            if isinstance(hook_result, SessionBeforeTreeResult) and hook_result.cancel:
                return NavigateTreeResult(cancelled=True)
            summary_dict_override: dict[str, Any] | None = None
            summary_text: str | None = None
            summary_details: Any | None = None
            from_hook_summary = False
            if (
                isinstance(hook_result, SessionBeforeTreeResult)
                and hook_result.summary is not None
            ):
                summary_dict_override = hook_result.summary
                summary_text = summary_dict_override.get("summary")
                summary_details = summary_dict_override.get("details")
                from_hook_summary = True
            if (
                summary_text is None
                and opts.summarize
                and len(entries) > 0
            ):
                summary_text = await generate_branch_summary(
                    self._state.model,
                    self._options.get_api_key_and_headers,
                    entries,
                    opts.custom_instructions,
                    _summarizer_override=(
                        self._options._branch_summarizer_override
                    ),
                )
            # Editor-branch extraction (Pi `:760-780`): user_message /
            # custom_message → resolve new_leaf_id to parent_id + extract text.
            editor_text: str | None = None
            new_leaf_id: str | None
            if (
                target_entry.type == "message"
                and target_entry.message.role == "user"  # type: ignore[union-attr]
            ):
                new_leaf_id = target_entry.parent_id
                editor_text = _extract_text_content(
                    target_entry.message.content  # type: ignore[union-attr]
                )
            elif target_entry.type == "custom_message":
                new_leaf_id = target_entry.parent_id
                editor_text = _extract_text_content(target_entry.content)  # type: ignore[union-attr]
            else:
                new_leaf_id = target_id
            summary_payload: dict[str, Any] | None = None
            if summary_text:
                summary_payload = {
                    "summary": summary_text,
                    "details": summary_details,
                    "from_hook": from_hook_summary,
                }
            summary_id = await self._session.move_to(
                new_leaf_id, summary=summary_payload
            )
            summary_entry: Any | None = None
            if summary_id is not None:
                stored = await self._session.get_entry(summary_id)
                if stored is not None and stored.type == "branch_summary":
                    summary_entry = stored
            new_leaf_after = await self._session.get_leaf_id()
            try:
                await self._hooks.emit(
                    SessionTreeHookEvent(
                        new_leaf_id=new_leaf_after,
                        old_leaf_id=old_leaf_id,
                        summary_entry=summary_entry,
                        from_hook=from_hook_summary,
                    )
                )
            except Exception as exc:
                raise AgentHarnessError(
                    "hook",
                    f"session_tree hook handler raised: {exc}",
                ) from exc
            return NavigateTreeResult(
                cancelled=False,
                editor_text=editor_text,
                summary_entry=summary_entry,
            )
        finally:
            self._phase = "idle"
            self._idle_event.set()

    async def _emit_queue_update(self) -> None:
        """Helper: emit ``QueueUpdateHookEvent`` with current queue snapshots."""

        try:
            await self._hooks.emit(
                QueueUpdateHookEvent(
                    steer=list(self._steering_queue._messages),
                    follow_up=list(self._follow_up_queue._messages),
                    next_turn=list(self._next_turn_queue),
                )
            )
        except Exception as exc:
            raise AgentHarnessError(
                "hook",
                f"queue_update hook handler raised: {exc}",
            ) from exc

    # === Sprint 3b — 8 setters (Pi parity, agent-harness.ts:704-776) ===

    async def set_model(self, model: Model) -> None:
        """Replace the active model. Pi: ``agent-harness.ts:704-718``.

        Emits :class:`ModelSelectHookEvent`. When called during a turn the
        change is also queued onto ``_pending_session_writes`` so the eventual
        Session ADR-0022 path can persist it (Phase 2.2). State mutation is
        immediate either way.
        """

        previous = self._state.model
        self._state.model = model
        if self._phase == "turn":
            self._pending_session_writes.append(
                PendingModelChangeWrite(
                    provider=getattr(model, "api", ""),
                    model_id=getattr(model, "id", ""),
                )
            )
        try:
            await self._hooks.emit(
                ModelSelectHookEvent(
                    model=model, previous_model=previous, source="set"
                )
            )
        except Exception as exc:
            raise AgentHarnessError(
                "hook",
                f"model_select hook handler raised: {exc}",
            ) from exc

    async def set_thinking_level(self, level: str) -> None:
        """Replace the thinking level. Pi: ``agent-harness.ts:720-733``."""

        previous = self._state.thinking_level
        self._state.thinking_level = level
        if self._phase == "turn":
            self._pending_session_writes.append(
                PendingThinkingLevelChangeWrite(thinking_level=level)
            )
        try:
            await self._hooks.emit(
                ThinkingLevelSelectHookEvent(level=level, previous_level=previous)
            )
        except Exception as exc:
            raise AgentHarnessError(
                "hook",
                f"thinking_level_select hook handler raised: {exc}",
            ) from exc

    async def set_active_tools(self, tool_names: list[str]) -> None:
        """Public async wrapper over the F-9 sync action.

        Pi: ``agent-harness.ts:875-882``. Pi ``setActiveTools`` validates
        + assigns only — no event emission, no pending push. Aelix mirrors
        that exactly (P-4 spec verdict + P-11 reversal in Sprint 4a).

        **P-11 reversal (Sprint 4a, ADR-0022):** the prior Sprint 3b W4
        MAJOR-1 push site has been **removed**. Verified at SHA 734e08e Pi
        ``setActiveTools`` does NOT push to ``pendingSessionWrites`` and
        ``flushPendingSessionWrites`` has NO ``active_tools_change`` case.
        The active-set is a per-process runtime concept in Pi — restoration
        is via ``options.activeToolNames`` on harness construction, not
        session replay.
        """

        self._action_set_active_tools(tool_names)

    async def set_steering_mode(self, mode: QueueMode) -> None:
        """Update the steering queue mode. Pi: ``agent-harness.ts:743-745``.

        No event emitted — Pi parity (setters do NOT emit ``queue_update``;
        only enqueue paths do — P-4 verdict).
        """

        self._steering_queue.mode = mode

    async def set_follow_up_mode(self, mode: QueueMode) -> None:
        """Update the follow-up queue mode. Pi: ``agent-harness.ts:747-749``."""

        self._follow_up_queue.mode = mode

    async def set_resources(self, resources: dict[str, Any]) -> None:
        """Replace the resources dict. Pi: ``agent-harness.ts:751-760``.

        Emits :class:`ResourcesUpdateHookEvent` with cloned snapshots of the
        previous and current dicts so handlers can mutate without affecting
        live state.

        Sprint 3b uses ``dict(...)`` shallow clone. Phase 2.2 may deepen.
        """

        previous = dict(self._state.resources)
        new_state = dict(resources)
        self._state.resources = new_state
        try:
            await self._hooks.emit(
                ResourcesUpdateHookEvent(
                    resources=dict(new_state),
                    previous_resources=previous,
                )
            )
        except Exception as exc:
            raise AgentHarnessError(
                "hook",
                f"resources_update hook handler raised: {exc}",
            ) from exc

    async def set_stream_options(self, stream_options: dict[str, Any]) -> None:
        """Replace the stream-options dict. Pi: ``agent-harness.ts:762-764``.

        No event emitted — Pi parity. Shallow clone in Sprint 3b.
        """

        # TODO(Phase-4): deep-clone headers/metadata to mirror Pi
        # ``cloneStreamOptions`` (agent-harness.ts:96-127 region).
        self._state.stream_options = dict(stream_options)

    async def set_tools(
        self,
        tools: list[AgentTool],
        active_tool_names: list[str] | None = None,
    ) -> None:
        """Atomic replace of the tool list. Pi: ``agent-harness.ts:766-776``.

        Validates the proposed active set against the NEW tool list BEFORE
        mutating state — on validation failure, the call raises and state is
        left untouched (no partial mutation). Pi ``validateToolNames``
        (``agent-harness.ts:407-410``) is strict: any name in the active
        filter that is not present in the new tool list raises immediately,
        regardless of whether the caller provided the active set explicitly
        or relied on the prior state. F-3b-2 (W5 must-document) flips Aelix
        from the prior silent-widening behaviour to this Pi-parity raise.

        To intentionally widen the active filter when replacing tools, the
        caller must pass ``active_tool_names=[]`` (or a fresh explicit list).

        No event emitted — Pi parity.
        """

        new_names = {t.name for t in tools}
        if active_tool_names is not None:
            # Explicit active list — validate against new tool names.
            unknown = [n for n in active_tool_names if n not in new_names]
            if unknown:
                raise AgentHarnessError(
                    "invalid_argument",
                    f"set_tools: unknown tool name(s) in active set: {unknown!r}",
                )
            new_active: list[str] | None = list(active_tool_names)
        elif self._state.active_tool_names is None:
            # No prior filter and no override — nothing to validate.
            new_active = None
        else:
            # F-3b-2: Pi ``validateToolNames`` raises when the prior active
            # filter contains names that are no longer present in the new
            # tool list. Aelix mirrors this exactly — silent widening is not
            # Pi-equivalent and was a Sprint 3b W5 must-document divergence.
            stale = [n for n in self._state.active_tool_names if n not in new_names]
            if stale:
                raise AgentHarnessError(
                    "invalid_argument",
                    f"set_tools: current active_tool_names contains names "
                    f"not in new tools: {stale!r}. Pass active_tool_names=[] "
                    f"(or a fresh list) to intentionally widen the filter.",
                )
            new_active = list(self._state.active_tool_names)
        # Atomic mutation after all validation succeeded.
        self._state.tools = list(tools)
        self._state.active_tool_names = new_active

    # === Sprint 3b — next_turn / append_message (Pi: agent-harness.ts:572-582) ===

    async def next_turn(
        self,
        text: str | None = None,
        *,
        images: list[ImageContent] | None = None,
    ) -> None:
        """Enqueue a user message for the NEXT ``prompt()`` invocation.

        Pi: ``agent-harness.ts:572-575``. Always legal regardless of phase —
        the queue is drained at the start of the next ``prompt()`` call.
        Emits :class:`QueueUpdateHookEvent` with snapshots of all three queues.
        """

        content: list[Any] = []
        if text is not None:
            content.append(TextContent(text=text))
        if images:
            content.extend(images)
        message = UserMessage(content=content)
        self._next_turn_queue.append(message)
        # W4 MINOR: DRY — reuse the shared queue_update emit helper so the
        # snapshot logic and hook-error wrapping live in exactly one place.
        await self._emit_queue_update()

    async def append_message(self, message: AgentMessage) -> None:
        """Append a message to the conversation. Pi: ``agent-harness.ts:575-582``.

        Idle: appended directly to ``state.messages``.
        Turn: queued onto ``_pending_session_writes`` (drained at turn_end).

        No event emitted — Pi parity.
        """

        if self._phase == "idle":
            self._state.messages.append(message)
        else:
            self._pending_session_writes.append(PendingMessageWrite(message=message))

    async def flush_pending_session_writes(self) -> None:
        """Drain ``_pending_session_writes`` FIFO. Pi: ``agent-harness.ts:459-481``.

        Sprint 4a 8-arm match dispatcher (P-12). When a :class:`Session` is
        attached, routes each variant to the corresponding ``session.append_*``
        call. When ``session is None`` (Aelix backward-compat fallback per
        ADR-0022), :class:`PendingMessageWrite` is mirrored into
        ``state.messages`` (transitional behavior so existing tests keep
        passing) and the other 7 variants are dropped with a debug log.
        """

        if not self._pending_session_writes:
            return
        pending = self._pending_session_writes
        self._pending_session_writes = []
        for entry in pending:
            if self._session is not None:
                # Pi parity branch — every variant routes to a session
                # append_* call. Match exhaustiveness is enforced by
                # ``assert_never`` so adding a future variant without a
                # dispatcher arm fails the type check.
                match entry:
                    case PendingMessageWrite():
                        await self._session.append_message(entry.message)
                    case PendingModelChangeWrite():
                        await self._session.append_model_change(
                            entry.provider, entry.model_id
                        )
                    case PendingThinkingLevelChangeWrite():
                        await self._session.append_thinking_level_change(
                            entry.thinking_level
                        )
                    case PendingCustomWrite():
                        await self._session.append_custom_entry(
                            entry.custom_type, entry.data
                        )
                    case PendingCustomMessageWrite():
                        await self._session.append_custom_message_entry(
                            entry.custom_type,
                            entry.content,
                            entry.display,
                            entry.details,
                        )
                    case PendingLabelWrite():
                        await self._session.append_label(
                            entry.target_id, entry.label
                        )
                    case PendingSessionInfoWrite():
                        await self._session.append_session_name(
                            entry.name or ""
                        )
                    case PendingLeafWrite():
                        # Pi: ``this.session.getStorage().setLeafId(...)``.
                        await self._session.get_storage().set_leaf_id(
                            entry.target_id
                        )
                    case _ as unreachable:
                        assert_never(unreachable)
            else:
                # Aelix-additive backward-compat path (no session attached).
                # ``message`` lands in ``state.messages`` to preserve the
                # Sprint 3b behavior used by existing tests; the other 7
                # variants are dropped with a debug log.
                if isinstance(entry, PendingMessageWrite):
                    self._state.messages.append(entry.message)
                else:
                    _log.debug(
                        "dropping %r (no session attached — Sprint 4a fallback)",
                        entry,
                    )

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
        """Build a fresh :class:`ExtensionContext` for the current hook emit.

        Sprint 5a (Phase 3.1, P-23) wires the 8 new non-UI fields:
        ``has_ui`` (constant), ``session_manager``, ``model_registry``,
        ``signal``, ``has_pending_messages``, ``shutdown``,
        ``get_context_usage``, ``compact``.
        """

        from aelix_coding_agent.extensions.api import (
            ExtensionContext as _ExtensionContext,
        )

        # Adapter binding ``ReadonlySessionManager`` to the harness session.
        session_manager: Any | None
        if self._session is None:
            session_manager = None
        else:
            session = self._session

            class _SessionManagerView:
                def get_session(self) -> Any | None:
                    return session

            session_manager = _SessionManagerView()

        def _has_pending() -> bool:
            return bool(
                self._steering_queue._messages
                or self._follow_up_queue._messages
                or self._next_turn_queue
            )

        def _compact_action(
            *,
            custom_instructions: str | None = None,
            on_complete: Callable[[Any], Any] | None = None,
            on_error: Callable[[Exception], Any] | None = None,
        ) -> None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # No loop — Sprint 5a fire-and-forget is a noop.

            async def _runner() -> None:
                try:
                    result = await self.compact(custom_instructions)
                    if on_complete is not None:
                        on_complete(result)
                except Exception as exc:  # noqa: BLE001
                    if on_error is not None:
                        on_error(exc)

            loop.create_task(_runner())

        def _get_context_usage() -> Any | None:
            # Sprint 5a: until Phase 4 provider work threads cost details
            # through MessageEndHookEvent, return ``None`` (matches Pi
            # behaviour right after compaction).
            return None

        def _shutdown_default() -> None:
            # Sprint 5a (pre-CLI loop) shutdown signals abort + invalidates
            # the runtime. ADR-0042 supplies the richer "exit process" wiring.
            self._mark_abort()

        return _ExtensionContext(
            self._runtime,
            cwd=self._options.cwd,
            model=self._state.model,
            is_idle=lambda: self._phase == "idle",
            abort=lambda: self._mark_abort(),
            get_active_tools=self._action_get_active_tools,
            get_system_prompt=self._action_get_system_prompt,
            session_manager=session_manager,
            signal=None,  # Phase 4 provider work threads the real signal.
            has_pending_messages=_has_pending,
            shutdown=_shutdown_default,
            get_context_usage=_get_context_usage,
            compact=_compact_action,
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

    # === Sprint 5a (Phase 3.1) — action bindings (P-22) ===

    def _action_set_session_name(self, name: str) -> None:
        """Pi parity: ``ExtensionAPI.setSessionName`` (``types.ts:1200``).

        Delegates to :meth:`Session.append_session_name` when a session is
        attached. Pre-session harnesses raise
        :class:`AgentHarnessError("invalid_state")`.
        """

        if self._session is None:
            raise AgentHarnessError(
                "invalid_state",
                "set_session_name() requires options.session to be attached",
            )
        # Sprint 4a session append is async — schedule via create_task so the
        # synchronous extension API call returns immediately.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._session.append_session_name(name))
        except RuntimeError:
            # Fallback for sync callers outside an event loop (test seam).
            asyncio.run(self._session.append_session_name(name))

    def _action_get_session_name(self) -> str | None:
        """Pi parity: ``ExtensionAPI.getSessionName`` (``types.ts:1203``).

        Returns ``None`` when no session attached.
        """

        if self._session is None:
            return None
        try:
            # Probe for a running loop — when inside one, we cannot block-await
            # so expose only the future's result if already settled (Sprint 5b
            # can wire a sync cache mirroring Pi's TS-side cache).
            asyncio.get_running_loop()
            future = asyncio.ensure_future(self._session.get_session_name())
            return future.result() if future.done() else None
        except RuntimeError:
            return asyncio.run(self._session.get_session_name())

    def _action_set_label(self, entry_id: str, label: str | None) -> None:
        """Pi parity: ``ExtensionAPI.setLabel`` (``types.ts:1206``)."""

        if self._session is None:
            raise AgentHarnessError(
                "invalid_state",
                "set_label() requires options.session to be attached",
            )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._session.append_label(entry_id, label))
        except RuntimeError:
            asyncio.run(self._session.append_label(entry_id, label))

    async def _action_set_model(self, model: Model) -> bool:
        """Pi parity: ``ExtensionAPI.setModel`` (``types.ts:1228``).

        Sprint 5a delegates to :meth:`AgentHarness.set_model`. Returns
        ``True`` on success (Pi returns ``false`` when no API key is
        available; that branch lands with Phase 4 provider work — Sprint 5a
        always returns ``True``).
        """

        await self.set_model(model)
        return True

    def _action_get_thinking_level(self) -> str:
        """Pi parity: ``ExtensionAPI.getThinkingLevel`` (``types.ts:1231``)."""

        level = self._state.thinking_level
        return level if level is not None else "off"

    def _action_set_thinking_level(self, level: str) -> None:
        """Pi parity: ``ExtensionAPI.setThinkingLevel`` (``types.ts:1234``)."""

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.set_thinking_level(level))
        except RuntimeError:
            asyncio.run(self.set_thinking_level(level))

    def _action_get_all_tools(self) -> list[Any]:
        """Pi parity: ``ExtensionAPI.getAllTools`` (``types.ts:1215``).

        Snapshots the harness ``_state.tools`` as a list of
        :class:`~aelix_coding_agent.extensions.api.ToolInfo` views.
        """

        from aelix_coding_agent.extensions.api import ToolInfo

        return [
            ToolInfo(name=tool.name, description=getattr(tool, "description", None))
            for tool in self._state.tools
        ]

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
        # Sprint 4b §F — state.messages source flip: when a Session is
        # attached, derive the turn's messages list from
        # ``session.build_context().messages`` (Pi parity:
        # ``agent-harness.ts:419, 427`` rebuilds per turn). When None, keep
        # the in-memory primary (Sprint 3b backward compat per ADR-0022
        # §"Aelix-additive divergences" item 3).
        if self._session is not None:
            session_ctx = await self._session.build_context()
            turn_messages: list[AgentMessage] = list(session_ctx.messages)
            turn_session_id = self._state.session_id
        else:
            turn_messages = list(self._state.messages)
            turn_session_id = self._state.session_id
        # F-10: install per-turn snapshot so ``_current_system_prompt`` returns
        # the chained value resolved by ``before_agent_start`` for the duration
        # of this turn only. Sprint 4b extends with messages + session_id per
        # ADR-0025 §"Pending extensions".
        self._turn_state = _TurnState(
            system_prompt=system_prompt,
            model=self._state.model,
            messages=turn_messages,
            session_id=turn_session_id,
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
                # Sprint 3c §A.5 — Pi parity, default "parallel".
                tool_execution=self._options.tool_execution,
            )
            # F-9: apply the active-tool filter without mutating ``_state.tools``.
            active = self._state.active_tool_names
            if active is None:
                active_tools = list(self._state.tools)
            else:
                active_set = set(active)
                active_tools = [t for t in self._state.tools if t.name in active_set]
            # Sprint 4b §F — when Session attached, ``turn_messages`` is
            # already derived from ``session.build_context().messages`` above.
            # Otherwise it's a fresh copy of ``state.messages`` (backward
            # compat). Either way we pass the snapshot, not ``state.messages``
            # directly, so per-turn mutations don't leak.
            context = AgentContext(
                system_prompt=system_prompt,
                messages=list(turn_messages),
                tools=active_tools,
            )

            async def emit(event: AgentEvent) -> None:
                # Sprint 4a — Pi ``handleAgentEvent`` (``agent-harness.ts:483-510``)
                # primary write path: every ``message_end`` event is
                # persisted via ``session.appendMessage`` BEFORE the
                # observational re-emit. When no session is attached we
                # skip the call (Aelix backward-compat path per ADR-0022).
                if event.type == "message_end" and self._session is not None:
                    try:
                        await self._session.append_message(event.message)
                    except Exception as exc:  # noqa: BLE001
                        # Session failure during message_end is logged but
                        # does not break the lifecycle emit chain — the
                        # observational hook fan-out still runs so
                        # extensions see the event.
                        _log.debug(
                            "session.append_message raised on message_end: %r",
                            exc,
                            exc_info=True,
                        )
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
                # Sprint 3b — after a turn_end projection, flush pending
                # session writes (Pi: ``agent-harness.ts:417``) and emit
                # ``save_point`` with the had-pending flag.
                if event.type == "turn_end":
                    had_pending = bool(self._pending_session_writes)
                    await self.flush_pending_session_writes()
                    try:
                        await self._hooks.emit(
                            SavePointHookEvent(had_pending_mutations=had_pending)
                        )
                    except Exception as exc:  # noqa: BLE001
                        _log.debug(
                            "save_point hook handler raised: %r",
                            exc,
                            exc_info=True,
                        )

            try:
                # Sprint 3c §C.2 — wrap the loop in a task so abort() can
                # cancel it. We await the task (not the coroutine directly)
                # and surface CancelledError as the expected abort path.
                turn_task = asyncio.create_task(
                    agent_loop(
                        prompts,
                        context,
                        config,
                        emit=emit,
                        stream_fn=self._options.stream_fn,
                    )
                )
                self._current_turn_task = turn_task
                try:
                    new_messages = await turn_task
                except asyncio.CancelledError:
                    # Sprint 3c §C.2 — abort() called ``task.cancel()`` during
                    # an in-flight turn. Treat this as a normal abort path:
                    # we already cleared queues + flipped _abort_requested in
                    # abort(); just return without raising so callers (and the
                    # finally block above) restore idle state.
                    if self._abort_requested:
                        return []
                    raise
                finally:
                    self._current_turn_task = None
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
            # Sprint 3b populates ``next_turn_count`` from the queue size at
            # turn settlement (Pi parity, types.ts:491-494).
            try:
                await self._hooks.emit(
                    SettledHookEvent(next_turn_count=len(self._next_turn_queue))
                )
            except Exception as exc:  # noqa: BLE001
                _log.debug("settled hook handler raised: %r", exc, exc_info=True)
            return new_messages
        finally:
            # Safety net: guarantee a flush even if the loop crashed before
            # turn_end fired. Idempotent if turn_end already drained the queue.
            try:
                await self.flush_pending_session_writes()
            except Exception:  # noqa: BLE001
                _log.debug(
                    "flush_pending_session_writes raised in finally",
                    exc_info=True,
                )
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


def _extract_text_content(content: Any) -> str:
    """Pi parity (``agent-harness.ts:760-780``): extract text from message content.

    Pi: ``typeof content === "string" ? content : content.filter(c => c.type
    === "text").map(c => c.text).join("")``.
    """

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""


__all__ = [
    "AgentHarness",
    "AgentHarnessError",
    "AgentHarnessOptions",
    "AgentHarnessPhase",
    "HarnessListener",
    "NavigateTreeOptions",
    "NavigateTreeResult",
    "PendingCustomMessageWrite",
    "PendingCustomWrite",
    "PendingLabelWrite",
    "PendingLeafWrite",
    "PendingMessageWrite",
    "PendingModelChangeWrite",
    "PendingSessionInfoWrite",
    "PendingSessionWrite",
    "PendingThinkingLevelChangeWrite",
]
