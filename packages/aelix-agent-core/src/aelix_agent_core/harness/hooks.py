"""Hook bus and event/result dataclasses for the AgentHarness.

This module defines the typed hook surface a Phase 1.2 ``AgentHarness`` uses
to talk to extensions. The design follows pi-agent-core's split between
``AgentHarness.emit*`` and ``ExtensionRunner.emit*``: each event has a Python
``@dataclass(frozen=True)`` payload and a matching result type, registered in
:data:`HOOK_RESULT_TYPES` for runtime introspection.

Typing model — per D.1.2 (Sprint 1 · Phase 1.2 spec) extended at Sprint 3a:

- :data:`HookEventName` is a closed ``Literal`` union of every Pi-verified
  event at the pinned SHA (10 loop projections + 25 own-events = 35 names —
  Sprint 6h₅a (Phase 4.14, ADR-0081) added 4 extension session lifecycle
  events on top of the Sprint 5a 31-name baseline).
- :class:`HookBus.on` carries 35 ``@overload`` declarations mirroring
  ``scripts/pyright_spike.py``. The runtime body accepts ``HookHandler``,
  defined as ``Callable[[HookEvent, ExtensionContext], Any | Awaitable[Any]]``.
- All event classes are frozen. Where a payload field (e.g. ``args``) needs
  to be mutated in-place by a handler chain, the field is a mutable container
  (``dict``/``list``) referenced by the frozen wrapper. Pi parity:
  ``/tmp/pi-ext-runner.ts`` ``emit_tool_call`` passes ``event.input`` by
  reference so handlers can patch arguments in place without re-validation.

Reducer rules (D.1.6 / B6) are implemented as private ``_reducer_*`` helpers
and invoked from :meth:`HookBus.emit` by event-type dispatch.

Sprint 3a additions (ADR-0017 v2, ADR-0019 v3, ADR-0030, ADR-0036):

- 13 new ``@dataclass(frozen=True)`` event classes covering Pi
  ``AgentHarnessOwnEvent`` (queue/setter/provider/session families) verified
  at SHA ``734e08e``.
- 3 new result types (``BeforeProviderRequestResult``,
  ``BeforeProviderPayloadResult``, ``SessionBeforeTreeResult``).
- ``SettledHookEvent`` gains a ``next_turn_count: int = 0`` field
  (populated in Sprint 3b once the ``next_turn`` queue exists).
- ``HookBus.on``/``ExtensionAPI.on`` accept per-handler
  ``error_mode: Literal["continue", "throw"] = "throw"`` (ADR-0019 v3 —
  default ``"throw"`` matches Pi shipped behavior; ``"continue"`` is an
  Aelix additive opt-in).
- New :data:`AgentHarnessEventName` alias (ADR-0036) for Pi citation clarity.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, overload

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
)
from aelix_ai.streaming import Model
from aelix_ai.tools import ToolResult

from aelix_agent_core.types import (
    AfterToolCallResult,
    AgentContext,
    AgentMessage,
)

if TYPE_CHECKING:
    # Avoid a runtime import cycle (D.1.9). ExtensionContext lives in
    # aelix_coding_agent.extensions.api which itself depends on this module's types.
    from aelix_coding_agent.extensions.api import ExtensionContext

    # Sprint 4b (Phase 2.2.2) — extended session_* event payloads (P-17/P-18/
    # P-19/P-20). Forward-declared via TYPE_CHECKING because session subsystem
    # is owned by ``aelix_agent_core.session`` (no runtime import cycle today).
    from aelix_agent_core.session.branch_summarization import (
        BranchSummaryPreparation,
        SummaryEntry,
    )
    from aelix_agent_core.session.compaction import (
        CompactionPreparation,
        CompactResult,
    )
    from aelix_agent_core.session.entries import SessionTreeEntry


_log = logging.getLogger(__name__)


# === Event name registry (ADR-0017 v2 / ADR-0036) ===

# Loop AgentEvent re-projections (10) — emitted by the bare agent_loop and
# projected onto HookEvents via :func:`_to_hook_event` in harness/core.py.
AgentEventName = Literal[
    "agent_start",
    "turn_start",
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "turn_end",
    "agent_end",
]

# Harness own-events (21 — Sprint 5a Phase 3.1 added 3) — Pi
# ``AgentHarnessOwnEvent`` at types.ts:595-612 + Pi
# ``coding-agent`` event types (input/user_bash/resources_discover) at SHA
# ``734e08e``. These are emitted by AgentHarness or by the
# ``aelix-coding-agent`` CLI loop directly (not via the loop projection).
#
# Sprint 5a (Phase 3.1, ADR-0017 §"Phase 3.1 event additions") REGISTERS
# 3 new events whose EMIT sites are deferred to Sprint 5b (ADR-0042 CLI
# loop). Pi truth (W1 P-24/P-25/P-26): the events DO exist in Pi at SHA
# 734e08e — they live in the ``coding-agent`` package, NOT
# ``agent-core``. Sprint 3a P-1 misclassified them as "wishlist-only";
# the spec §D corrects that.
AgentHarnessEventName = Literal[
    "queue_update",
    "save_point",
    "abort",
    "settled",
    "before_agent_start",
    "context",
    "before_provider_request",
    "before_provider_payload",
    "after_provider_response",
    "tool_call",
    "tool_result",
    "session_before_compact",
    "session_compact",
    "session_before_tree",
    "session_tree",
    "model_select",
    "thinking_level_select",
    "resources_update",
    # Sprint 5a additions (Phase 3.1) — Pi ``coding-agent`` at SHA 734e08e
    "input",
    "user_bash",
    "resources_discover",
    # Sprint 6h₅a additions (Phase 4.14, ADR-0081) — Pi extension session
    # lifecycle events at SHA ``734e08e`` (``extensions/types.ts:513-557``).
    "session_start",
    "session_before_switch",
    "session_before_fork",
    "session_shutdown",
]

# Union of both (31 names total — Sprint 5a Phase 3.1 added 3 own events).
# ADR-0036 keeps the two names disjoint so "is this a loop event or a
# harness own-event?" stays explicit and answerable.
HookEventName = Literal[
    # === Loop AgentEvent re-projections (10) ===  ← ADR-0036 projection
    "agent_start",
    "turn_start",
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "turn_end",
    "agent_end",
    # === Harness own-events (21) ===  ← Pi types.ts:595-612 + coding-agent (SHA 734e08e)
    "queue_update",
    "save_point",
    "abort",
    "settled",
    "before_agent_start",
    "context",
    "before_provider_request",
    "before_provider_payload",
    "after_provider_response",
    "tool_call",
    "tool_result",
    "session_before_compact",
    "session_compact",
    "session_before_tree",
    "session_tree",
    "model_select",
    "thinking_level_select",
    "resources_update",
    # Sprint 5a additions (Phase 3.1) — Pi ``coding-agent`` at SHA 734e08e
    "input",
    "user_bash",
    "resources_discover",
    # Sprint 6h₅a additions (Phase 4.14, ADR-0081) — Pi extension session
    # lifecycle events at SHA ``734e08e`` (``extensions/types.ts:513-557``).
    "session_start",
    "session_before_switch",
    "session_before_fork",
    "session_shutdown",
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


HookErrorMode = Literal["continue", "throw"]
"""Per-handler error policy (ADR-0019 v3).

- ``"throw"`` (default): re-raise handler exceptions out of the reducer.
  Matches Pi ``agent-harness.ts:200-220`` shipped behavior — the harness
  wraps the exception in ``AgentHarnessError("hook")`` upstream and aborts.
- ``"continue"`` (Aelix additive opt-in): log + swallow the exception and
  continue the reducer chain. Aelix-only feature preserving the Pi
  ``docs/hooks.md`` "Poking holes" future-design intent.
"""


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


@dataclass(frozen=True)
class BeforeProviderRequestResult:
    """Pi parity: ``types.ts`` ``BeforeProviderRequestResult``.

    ``stream_options`` patch is applied via reducer in Sprint 3a (registered);
    emit site lives in Phase 4 provider adapter (ADR-0038).
    """

    stream_options: dict[str, Any] | None = None


@dataclass(frozen=True)
class BeforeProviderPayloadResult:
    """Pi parity: ``types.ts`` ``BeforeProviderPayloadResult``. ``payload`` chained.

    **``payload=None`` semantics (W4 clarification):**
    The "no patch — skip this hook entirely" signal is for a handler to
    return a **bare** ``None`` (NOT a result wrapper). The reducer only
    chains when the handler returns a :class:`BeforeProviderPayloadResult`
    instance, so a bare ``None`` is treated as "no opinion" and the chain
    falls through with the prior ``current`` payload unchanged.

    Returning a wrapper with the field omitted —
    ``BeforeProviderPayloadResult()`` — is equivalent to
    ``BeforeProviderPayloadResult(payload=None)`` and is currently treated
    as an **explicit overwrite to ``None``** by the reducer (``current``
    becomes ``None`` and the chain is marked modified). This mirrors Pi
    ``BeforeProviderPayloadResult`` (`types.ts` ``BeforeProviderPayloadResult``)
    where the field value carries the explicit replacement payload — Pi
    TS-undefined and Aelix ``None`` play the same "explicit empty" role.

    To intentionally clear / replace the payload **without** setting
    ``None``, pass an empty value for the provider payload shape (e.g.
    ``BeforeProviderPayloadResult(payload={})`` for a dict-shaped payload,
    ``BeforeProviderPayloadResult(payload="")`` for a string-shaped one).
    """

    payload: Any = None


@dataclass(frozen=True)
class SessionBeforeTreeResult:
    """Pi parity: ``types.ts`` ``SessionBeforeTreeResult``.

    ``cancel=True`` short-circuits the chain (no later handler runs).
    The remaining optional fields are merged via the same generic
    ``session_before`` reducer as ``SessionBeforeCompactResult``.
    """

    cancel: bool = False
    summary: dict[str, Any] | None = None  # {summary: str, details?: Any}
    custom_instructions: str | None = None
    replace_instructions: bool | None = None
    label: str | None = None


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
    ADR-0018 (Sprint 3b). Sprint 3a leaves this unchanged per spec §H.
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


# --- Session events (Sprint 4b Phase 2.2.2 — payload extensions per P-17/P-20) ---


@dataclass(frozen=True)
class SessionBeforeCompactResult:
    """Pi ``SessionBeforeCompactResult`` (``types.ts:339-342``, P-20).

    Sprint 4b extended the Sprint 3a 2-field stub ``{cancel, reason}`` to the
    Pi-parity ``{cancel?, compaction?}`` shape. The ``compaction`` field lets a
    hook substitute the LLM call entirely (Pi ``agent-harness.ts:705-708`` —
    when ``hookResult?.compaction`` is set, the harness skips ``compact(...)``
    and uses the hook-provided result with ``from_hook=True``).

    ``reason`` is retained as an Aelix-additive convenience for diagnostic
    messages on cancelled paths; Pi uses bare ``cancel?: boolean``.
    """

    cancel: bool = False
    reason: str | None = None
    compaction: CompactResult | None = None


@dataclass(frozen=True)
class SessionBeforeCompactHookEvent(HookEvent):
    """Pi ``SessionBeforeCompactHookEvent`` (P-17, Sprint 4b extension).

    Pi ``agent-harness.ts:706-711`` payload: ``{preparation, branchEntries,
    customInstructions, signal}``. Sprint 3a registered the empty stub; this
    Sprint 4b extension lands the full Pi payload to match the emit site
    introduced in :meth:`AgentHarness.compact`.
    """

    preparation: CompactionPreparation | None = None
    branch_entries: list[SessionTreeEntry] = field(default_factory=list)
    custom_instructions: str | None = None
    signal: Any | None = None
    type: Literal["session_before_compact"] = "session_before_compact"


# --- Sprint 3a additions (Pi ``AgentHarnessOwnEvent`` parity at SHA 734e08e) ---


@dataclass(frozen=True)
class QueueUpdateHookEvent(HookEvent):
    """Emitted whenever steer/follow_up/next_turn queues change.

    Pi parity: ``types.ts:474-478`` (SHA 734e08e). Emitted by setters and
    enqueue paths in Sprint 3b. Sprint 3a registers the type only.
    """

    steer: list[AgentMessage] = field(default_factory=list)
    follow_up: list[AgentMessage] = field(default_factory=list)
    next_turn: list[AgentMessage] = field(default_factory=list)
    type: Literal["queue_update"] = "queue_update"


@dataclass(frozen=True)
class SavePointHookEvent(HookEvent):
    """Emitted before returning to idle after a turn if mutations were pending.

    Pi parity: ``types.ts:480-483`` (SHA 734e08e), emitted at
    ``agent-harness.ts:417``. Sprint 3a: type only; emit Sprint 3b.
    """

    had_pending_mutations: bool = False
    type: Literal["save_point"] = "save_point"


@dataclass(frozen=True)
class AbortHookEvent(HookEvent):
    """Emitted when ``abort()`` clears queued steer/follow_up messages.

    Pi parity: ``types.ts:485-489`` (SHA 734e08e). Sprint 3a: type only.
    """

    cleared_steer: list[AgentMessage] = field(default_factory=list)
    cleared_follow_up: list[AgentMessage] = field(default_factory=list)
    type: Literal["abort"] = "abort"


@dataclass(frozen=True)
class BeforeProviderRequestHookEvent(HookEvent):
    """Emitted before the provider HTTP request is built.

    Pi parity: ``types.ts:510-515`` + ``agent-harness.ts:232-250`` (SHA 734e08e).
    Sprint 3a registers type + result + reducer; **no emit site lands in 3a**.
    Phase 4 provider adapter (ADR-0038) is the emit owner.
    """

    model: Model | None = None
    session_id: str = ""
    stream_options: dict[str, Any] = field(default_factory=dict)
    type: Literal["before_provider_request"] = "before_provider_request"


@dataclass(frozen=True)
class BeforeProviderPayloadHookEvent(HookEvent):
    """Emitted with the provider-specific payload immediately before send.

    Pi parity: ``types.ts:517-521`` + ``agent-harness.ts:265-280`` (SHA 734e08e).
    Sprint 3a: type + result + reducer only. Emit site Phase 4.
    """

    model: Model | None = None
    payload: Any = None
    type: Literal["before_provider_payload"] = "before_provider_payload"


@dataclass(frozen=True)
class AfterProviderResponseHookEvent(HookEvent):
    """Emitted after the provider HTTP response headers are received.

    Pi parity: ``types.ts:523-527`` + ``agent-harness.ts:275`` (SHA 734e08e).
    Observational. Sprint 3a: type only. Emit Phase 4.
    """

    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    type: Literal["after_provider_response"] = "after_provider_response"


@dataclass(frozen=True)
class SessionCompactHookEvent(HookEvent):
    """Emitted after a compaction entry has been appended to the session.

    Pi parity: ``types.ts:554-558`` + ``agent-harness.ts:582`` (SHA 734e08e).
    Sprint 3a: type only. Emit Phase 2.2 (ADR-0023 ``compact()``).
    """

    compaction_entry: Any = None  # Phase 2.2 (ADR-0022) — Session entry type
    from_hook: bool = False
    type: Literal["session_compact"] = "session_compact"


@dataclass(frozen=True)
class SessionBeforeTreeHookEvent(HookEvent):
    """Pi ``SessionBeforeTreeHookEvent`` (P-18, Sprint 4b extension).

    Pi ``agent-harness.ts:765`` payload: ``{preparation, signal}``. Sprint 3a
    shipped only ``preparation``; this Sprint 4b extension adds ``signal`` to
    match Pi and the new emit site in :meth:`AgentHarness.navigate_tree`.
    """

    preparation: BranchSummaryPreparation | None = None
    signal: Any | None = None
    type: Literal["session_before_tree"] = "session_before_tree"


@dataclass(frozen=True)
class SessionTreeHookEvent(HookEvent):
    """Pi ``SessionTreeHookEvent`` (P-19, Sprint 4b extension).

    Pi ``types.ts:303-309`` types ``newLeafId: string | null``. Sprint 4b
    narrows the Aelix default from ``str = ""`` to ``str | None = None`` to
    match Pi exactly. ``old_leaf_id`` is likewise narrowed; ``summary_entry``
    gets a proper :class:`SummaryEntry` annotation.
    """

    new_leaf_id: str | None = None
    old_leaf_id: str | None = None
    summary_entry: SummaryEntry | None = None
    from_hook: bool = False
    type: Literal["session_tree"] = "session_tree"


# === Sprint 6h₅a (Phase 4.14, ADR-0081) — extension session lifecycle events ===
# Pi parity (SHA 734e08e) — W5 P-344 line citation corrections:
#   - SessionStartEvent          → extensions/types.ts:513-519
#   - SessionBeforeSwitchEvent   → extensions/types.ts:522-526
#   - SessionBeforeForkEvent     → extensions/types.ts:529-533
#   - SessionShutdownEvent       → extensions/types.ts:552-557
#   - SessionBeforeSwitchResult  → extensions/types.ts (cancel?: boolean)
#   - SessionBeforeForkResult    → extensions/types.ts:1015-1022
#     (cancel?, skipConversationRestore?)


@dataclass(frozen=True)
class SessionBeforeSwitchResult:
    """Pi parity: ``SessionBeforeSwitchResult`` (``extensions/types.ts``).

    Sprint 6h₅a (Phase 4.14, ADR-0081, P-332). ``cancel=True``
    short-circuits the reducer (first-cancel-wins via the shared
    :func:`_reducer_session_before`) and the active replace API
    (``switch_session`` / ``new_session``) returns
    :class:`RuntimeReplaceResult(cancelled=True)`.
    """

    cancel: bool = False


@dataclass(frozen=True)
class SessionBeforeForkResult:
    """Pi parity: ``SessionBeforeForkResult`` (``extensions/types.ts:1015-1022``).

    Sprint 6h₅a (Phase 4.14, ADR-0081, P-332 / P-345). Same
    cancel-aggregation semantics as :class:`SessionBeforeSwitchResult`
    (shared reducer).

    P-345 (W5 BLOCKING FIX): adds ``skip_conversation_restore``
    Pi-parity field. Pi ``extensions/types.ts:1015-1022``:

    .. code-block:: typescript

       interface SessionBeforeForkResult {
           cancel?: boolean;
           skipConversationRestore?: boolean;
       }

    The field is ``None`` by default (Pi: omitted/undefined). Aelix does
    not currently consume it (fork-restore semantics deferred to
    Sprint 6h₅b), but the dataclass shape must match Pi so extensions
    written against Pi typings can compile against Aelix.
    """

    cancel: bool = False
    skip_conversation_restore: bool | None = None


@dataclass(frozen=True)
class SessionStartHookEvent(HookEvent):
    """Pi ``SessionStartEvent`` (``extensions/types.ts:513-519``).

    Sprint 6h₅a (Phase 4.14, ADR-0081, P-332). Emitted after each
    successful session replacement (``switch_session`` / ``new_session`` /
    ``fork``) from the NEW harness's ``HookBus`` (the OLD bus has been
    disposed by ``_teardown_current``). The ``reason="startup"`` /
    ``"reload"`` first-emit at harness bootstrap is deferred to Sprint
    6h₅b (P-343 carry-forward).
    """

    reason: Literal["startup", "reload", "new", "resume", "fork"] = "startup"
    previous_session_file: str | None = None
    type: Literal["session_start"] = "session_start"


@dataclass(frozen=True)
class SessionBeforeSwitchHookEvent(HookEvent):
    """Pi ``SessionBeforeSwitchEvent`` (``extensions/types.ts:522-526``).

    Sprint 6h₅a (Phase 4.14, ADR-0081, P-332). Emitted before
    ``switch_session`` (``reason="resume"``) or ``new_session``
    (``reason="new"``) tears down the current harness — extensions may
    return :class:`SessionBeforeSwitchResult(cancel=True)` to abort the
    replace.
    """

    reason: Literal["new", "resume"] = "resume"
    target_session_file: str | None = None
    type: Literal["session_before_switch"] = "session_before_switch"


@dataclass(frozen=True)
class SessionBeforeForkHookEvent(HookEvent):
    """Pi ``SessionBeforeForkEvent`` (``extensions/types.ts:529-533``).

    Sprint 6h₅a (Phase 4.14, ADR-0081, P-332). Emitted before ``fork``
    tears down the current harness — extensions may return
    :class:`SessionBeforeForkResult(cancel=True)` to abort the fork.
    """

    entry_id: str = ""
    position: Literal["before", "at"] = "before"
    type: Literal["session_before_fork"] = "session_before_fork"


@dataclass(frozen=True)
class SessionShutdownHookEvent(HookEvent):
    """Pi ``SessionShutdownEvent`` (``extensions/types.ts:552-557``).

    Sprint 6h₅a (Phase 4.14, ADR-0081, P-332). Emitted at the top of
    ``_teardown_current`` (``reason="new"|"resume"|"fork"``) and at the
    top of ``dispose`` (``reason="quit"``). The ``"reload"`` reason is
    declared for Pi-parity but no Aelix emit site uses it in 6h₅a.
    """

    reason: Literal["quit", "reload", "new", "resume", "fork"] = "quit"
    target_session_file: str | None = None
    type: Literal["session_shutdown"] = "session_shutdown"


@dataclass(frozen=True)
class ModelSelectHookEvent(HookEvent):
    """Emitted by ``set_model()`` (and ``restore`` path).

    Pi parity: ``types.ts:574-579`` + ``agent-harness.ts:648`` (SHA 734e08e).
    Sprint 3a: type only. Emit in Sprint 3b ``set_model()`` implementation.
    """

    model: Model | None = None
    previous_model: Model | None = None
    source: Literal["set", "restore"] = "set"
    type: Literal["model_select"] = "model_select"


@dataclass(frozen=True)
class ThinkingLevelSelectHookEvent(HookEvent):
    """Emitted by ``set_thinking_level()``.

    Pi parity: ``types.ts:581-585`` + ``agent-harness.ts:660`` (SHA 734e08e).
    Sprint 3a: type only. Emit Sprint 3b.
    """

    level: str = "off"
    previous_level: str = "off"
    type: Literal["thinking_level_select"] = "thinking_level_select"


@dataclass(frozen=True)
class ResourcesUpdateHookEvent(HookEvent):
    """Emitted by ``set_resources()``.

    Pi parity: ``types.ts:587-593`` + ``agent-harness.ts:689`` (SHA 734e08e).
    ``resources`` and ``previous_resources`` are AgentHarnessResources shapes;
    Phase 1.4 placeholder uses ``dict[str, Any]``. Sprint 3a: type only.
    """

    resources: dict[str, Any] = field(default_factory=dict)
    previous_resources: dict[str, Any] = field(default_factory=dict)
    type: Literal["resources_update"] = "resources_update"


@dataclass(frozen=True)
class SettledHookEvent(HookEvent):
    """Observational event emitted when the harness returns to idle.

    Pi parity: ``types.ts:491-494`` (SHA 734e08e). The ``next_turn_count``
    field is added in Sprint 3a (event-type extension); the populating value
    remains ``0`` until Sprint 3b implements the ``next_turn`` queue.
    """

    next_turn_count: int = 0  # NEW in 3a — Pi parity; populated in 3b
    type: Literal["settled"] = "settled"


# === Sprint 5a (Phase 3.1) — 3 new events registered (P-24/P-25/P-26) ===
#
# Emit sites are deferred to Sprint 5b (ADR-0042 CLI loop). These dataclasses
# + their result types + reducers + @overloads land in Sprint 5a so the
# type surface is stable for extension authors and the
# ``DEFERRED_ALLOWLIST`` invariant is honoured (ADR-0041 closure pin).
#
# Pi sources (SHA ``734e08e``):
# - ``InputEvent``               → ``coding-agent/.../extensions/types.ts:619-625``
# - ``UserBashEvent``            → ``coding-agent/.../extensions/types.ts:602-609``
# - ``ResourcesDiscoverEvent``   → ``coding-agent/.../extensions/types.ts:512-517``


@dataclass(frozen=True)
class InputContinue:
    """Reducer result — "no opinion, fall through to next handler"."""

    action: Literal["continue"] = "continue"


@dataclass(frozen=True)
class InputTransform:
    """Reducer result — replace the input text/images for subsequent handlers + the CLI loop."""

    text: str = ""
    images: list[ImageContent] | None = None
    action: Literal["transform"] = "transform"


@dataclass(frozen=True)
class InputHandled:
    """Reducer result — short-circuit: extension handled the input, do not run CLI loop."""

    action: Literal["handled"] = "handled"


InputResult = InputContinue | InputTransform | InputHandled
"""Pi ``InputEventResult`` (``types.ts:762-765``). The reducer treats a bare
``None`` return as "no opinion" (equivalent to :class:`InputContinue`).
"""


class BashOperations(Protocol):
    """Minimal stub Protocol for Pi ``BashOperations``.

    Sprint 5a registers the type surface only — the full BashOperations
    Protocol (with ``execute``, ``executeInteractive``, etc.) lands in
    Sprint 5b alongside the built-in ``bash`` coding tool (ADR-0042 /
    ADR-0043). The Protocol carries no required methods at 5a; future
    sprints widen it as the CLI loop demands.
    """


class BashResult(Protocol):
    """Minimal stub Protocol for Pi ``BashResult``.

    See :class:`BashOperations` for the deferred-binding rationale.
    """


@dataclass(frozen=True)
class UserBashResult:
    """Pi ``UserBashEventResult`` (``types.ts:986-994``).

    ``operations`` lets an extension swap the bash dispatcher;
    ``result`` lets the extension fully replace execution (Pi
    ``isOperationsMissing && result?`` short-circuit).
    """

    operations: BashOperations | None = None
    result: BashResult | None = None


@dataclass(frozen=True)
class ResourcesDiscoverResult:
    """Pi ``ResourcesDiscoverResult`` (``types.ts:502-509``).

    Each list is collected + de-duplicated across handlers by
    :func:`_reducer_resources_discover` (Pi
    ``agent-session.ts:2059-2068``).
    """

    skill_paths: list[str] | None = None
    prompt_paths: list[str] | None = None
    theme_paths: list[str] | None = None


@dataclass(frozen=True)
class InputHookEvent(HookEvent):
    """Pi ``InputEvent`` (``types.ts:619-625``).

    Emitted by the CLI input loop after user text/images are received but
    before they reach :meth:`AgentHarness.prompt`. Sprint 5a registers
    only; emit lands in Sprint 5b (ADR-0042).
    """

    text: str = ""
    images: list[ImageContent] | None = None
    source: Literal["interactive", "rpc", "extension"] = "interactive"
    type: Literal["input"] = "input"


@dataclass(frozen=True)
class UserBashHookEvent(HookEvent):
    """Pi ``UserBashEvent`` (``types.ts:602-609``).

    Emitted when a user bash command is dispatched via the ``!`` (in-context)
    or ``!!`` (excluded) prefix in the CLI loop. Sprint 5a registers only;
    emit lands in Sprint 5b (ADR-0042 CLI loop).
    """

    command: str = ""
    exclude_from_context: bool = False
    cwd: str = ""
    type: Literal["user_bash"] = "user_bash"


@dataclass(frozen=True)
class ResourcesDiscoverHookEvent(HookEvent):
    """Pi ``ResourcesDiscoverEvent`` (``types.ts:512-517``).

    Fired after ``session_start`` to let extensions contribute additional
    skill / prompt / theme paths. Sprint 5a registers only; emit lands in
    Sprint 5b (ADR-0042 CLI loop) when the resource discovery surface is
    online.
    """

    cwd: str = ""
    reason: Literal["startup", "reload"] = "startup"
    type: Literal["resources_discover"] = "resources_discover"


# === Sprint 5b (Phase 3.2) — tool-typed ToolCallEvent variants (P-31, ADR-0043) ===
#
# Pi parity (``coding-agent/src/core/extensions/types.ts:771-830``): Pi ships a
# discriminated union of tool-typed ``ToolCallEvent`` variants — 7 known +
# 1 ``CustomToolCallEvent`` fallback. Aelix mirrors via dataclass subclasses
# on :class:`ToolCallHookEvent`; base class stays constructible so existing
# tests keep passing. Symmetric :class:`ToolResultHookEvent` variants mirror
# Pi ``types.ts:833-876``.


BUILTIN_TOOL_NAMES: frozenset[str] = frozenset(
    {"bash", "read", "edit", "write", "grep", "find", "ls"}
)
"""Pi parity ``allToolNames`` (``coding-agent/src/core/tools/index.ts``)."""


@dataclass(frozen=True)
class BashToolCallHookEvent(ToolCallHookEvent):
    """Pi parity ``BashToolCallEvent``."""

    tool_name: str = "bash"


@dataclass(frozen=True)
class ReadToolCallHookEvent(ToolCallHookEvent):
    """Pi parity ``ReadToolCallEvent``."""

    tool_name: str = "read"


@dataclass(frozen=True)
class EditToolCallHookEvent(ToolCallHookEvent):
    """Pi parity ``EditToolCallEvent``."""

    tool_name: str = "edit"


@dataclass(frozen=True)
class WriteToolCallHookEvent(ToolCallHookEvent):
    """Pi parity ``WriteToolCallEvent``."""

    tool_name: str = "write"


@dataclass(frozen=True)
class GrepToolCallHookEvent(ToolCallHookEvent):
    """Pi parity ``GrepToolCallEvent``."""

    tool_name: str = "grep"


@dataclass(frozen=True)
class FindToolCallHookEvent(ToolCallHookEvent):
    """Pi parity ``FindToolCallEvent``."""

    tool_name: str = "find"


@dataclass(frozen=True)
class LsToolCallHookEvent(ToolCallHookEvent):
    """Pi parity ``LsToolCallEvent``."""

    tool_name: str = "ls"


@dataclass(frozen=True)
class CustomToolCallHookEvent(ToolCallHookEvent):
    """Pi parity ``CustomToolCallEvent`` — for any tool whose name does not
    match the 7 built-ins.
    """


_TOOL_CALL_EVENT_CLS_BY_NAME: dict[str, type[ToolCallHookEvent]] = {
    "bash": BashToolCallHookEvent,
    "read": ReadToolCallHookEvent,
    "edit": EditToolCallHookEvent,
    "write": WriteToolCallHookEvent,
    "grep": GrepToolCallHookEvent,
    "find": FindToolCallHookEvent,
    "ls": LsToolCallHookEvent,
}


def make_tool_call_event(
    *,
    tool_call_id: str,
    tool_name: str,
    args: dict[str, Any],
    assistant_message: AssistantMessage | None = None,
    context: AgentContext | None = None,
) -> ToolCallHookEvent:
    """Factory dispatching to the typed variant matching ``tool_name``.

    Pi parity: ``_make_tool_call_event`` factory (ADR-0043 §C.2). Unknown
    tool names route to :class:`CustomToolCallHookEvent`.
    """

    cls = _TOOL_CALL_EVENT_CLS_BY_NAME.get(tool_name, CustomToolCallHookEvent)
    return cls(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        args=args,
        assistant_message=assistant_message,
        context=context,
    )


@dataclass(frozen=True)
class BashToolResultHookEvent(ToolResultHookEvent):
    tool_name: str = "bash"


@dataclass(frozen=True)
class ReadToolResultHookEvent(ToolResultHookEvent):
    tool_name: str = "read"


@dataclass(frozen=True)
class EditToolResultHookEvent(ToolResultHookEvent):
    tool_name: str = "edit"


@dataclass(frozen=True)
class WriteToolResultHookEvent(ToolResultHookEvent):
    tool_name: str = "write"


@dataclass(frozen=True)
class GrepToolResultHookEvent(ToolResultHookEvent):
    tool_name: str = "grep"


@dataclass(frozen=True)
class FindToolResultHookEvent(ToolResultHookEvent):
    tool_name: str = "find"


@dataclass(frozen=True)
class LsToolResultHookEvent(ToolResultHookEvent):
    tool_name: str = "ls"


@dataclass(frozen=True)
class CustomToolResultHookEvent(ToolResultHookEvent):
    """Pi parity ``CustomToolResultEvent`` fallback."""


_TOOL_RESULT_EVENT_CLS_BY_NAME: dict[str, type[ToolResultHookEvent]] = {
    "bash": BashToolResultHookEvent,
    "read": ReadToolResultHookEvent,
    "edit": EditToolResultHookEvent,
    "write": WriteToolResultHookEvent,
    "grep": GrepToolResultHookEvent,
    "find": FindToolResultHookEvent,
    "ls": LsToolResultHookEvent,
}


def make_tool_result_event(
    *,
    tool_call_id: str,
    tool_name: str,
    args: dict[str, Any],
    content: list[Any],
    details: Any | None = None,
    is_error: bool = False,
    terminate: bool = False,
) -> ToolResultHookEvent:
    """Symmetric factory for :class:`ToolResultHookEvent` (Pi ADR-0043 §C.2)."""

    cls = _TOOL_RESULT_EVENT_CLS_BY_NAME.get(tool_name, CustomToolResultHookEvent)
    return cls(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        args=args,
        content=content,
        details=details,
        is_error=is_error,
        terminate=terminate,
    )


def is_tool_call_event_type(tool_name: str, event: ToolCallHookEvent) -> bool:
    """Pi parity ``isToolCallEventType`` (``types.ts:934-940``)."""

    return event.tool_name == tool_name


def is_tool_result_event_type(
    tool_name: str, event: ToolResultHookEvent
) -> bool:
    """Pi parity ``isToolResultEventType``."""

    return event.tool_name == tool_name


# === Runtime registry ===


HOOK_RESULT_TYPES: dict[HookEventName, type | None] = {
    # === existing 16 entries ===
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
    # === Sprint 3a additions (12 — settled already mapped) ===
    "queue_update": None,
    "save_point": None,
    "abort": None,
    "before_provider_request": BeforeProviderRequestResult,
    "before_provider_payload": BeforeProviderPayloadResult,
    "after_provider_response": None,
    "session_compact": None,
    "session_before_tree": SessionBeforeTreeResult,
    "session_tree": None,
    "model_select": None,
    "thinking_level_select": None,
    "resources_update": None,
    # === Sprint 5a (Phase 3.1) additions — 3 registered events ===
    "input": InputContinue,  # union representative — reducer accepts all 3 InputResult arms
    "user_bash": UserBashResult,
    "resources_discover": ResourcesDiscoverResult,
    # === Sprint 6h₅a (Phase 4.14, ADR-0081) additions — 4 extension events ===
    "session_start": None,                                # observational
    "session_before_switch": SessionBeforeSwitchResult,
    "session_before_fork": SessionBeforeForkResult,
    "session_shutdown": None,                             # observational
}


# === Reducers (D.1.6 / B6) ===


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


# Per-handler error_mode plumbing (ADR-0019 v3). Reducers receive a list of
# ``(handler, error_mode)`` pairs and route every handler invocation through
# :func:`_safe_invoke`, which honors the per-handler policy.
HandlerEntry = tuple[HookHandler, HookErrorMode]


async def _safe_invoke(
    handler: HookHandler,
    event: HookEvent,
    ctx: ExtensionContext,
    error_mode: HookErrorMode,
) -> Any:
    """Invoke ``handler`` under its ``error_mode`` policy.

    - ``"throw"`` (default, Pi parity): re-raise the exception so the reducer
      bubbles it up to :meth:`HookBus.emit` and the harness wraps it as
      ``AgentHarnessError("hook", ...)``.
    - ``"continue"`` (Aelix additive opt-in, ADR-0019 v3): log + swallow.
    """

    try:
        return await _maybe_await(handler(event, ctx))
    except Exception as exc:
        if error_mode == "throw":
            raise
        _log.debug("hook handler raised (continuing): %r", exc, exc_info=True)
        return None


async def _reducer_context(
    handlers: list[HandlerEntry],
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
    for handler, mode in handlers:
        patched = ContextHookEvent(messages=current_messages)
        raw = await _safe_invoke(handler, patched, ctx, mode)
        if isinstance(raw, ContextResult) and raw.messages is not None:
            current_messages = list(raw.messages)
            modified = True
    if not modified:
        return None
    return ContextResult(messages=current_messages)


async def _reducer_before_agent_start(
    handlers: list[HandlerEntry],
    event: BeforeAgentStartHookEvent,
    ctx: ExtensionContext,
) -> BeforeAgentStartResult | None:
    """Collect messages from all handlers; chain ``system_prompt``."""

    collected: list[AgentMessage] = []
    current_prompt = event.system_prompt
    modified_prompt = False
    for handler, mode in handlers:
        chained = BeforeAgentStartHookEvent(
            prompt=event.prompt,
            system_prompt=current_prompt,
            images=event.images,
        )
        raw = await _safe_invoke(handler, chained, ctx, mode)
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
    handlers: list[HandlerEntry],
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
    for handler, mode in handlers:
        raw = await _safe_invoke(handler, event, ctx, mode)
        if isinstance(raw, ToolCallResult):
            if raw.block:
                return raw
            last_observational = raw
    return last_observational


async def _reducer_tool_result(
    handlers: list[HandlerEntry],
    event: ToolResultHookEvent,
    ctx: ExtensionContext,
) -> ToolResultPatch | None:
    """Sequential patch accumulation. Unset fields preserve the prior value."""

    accumulated: ToolResultPatch | None = None
    current_event = event
    for handler, mode in handlers:
        raw = await _safe_invoke(handler, current_event, ctx, mode)
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
    handlers: list[HandlerEntry],
    event: HookEvent,
    ctx: ExtensionContext,
) -> (
    SessionBeforeCompactResult
    | SessionBeforeTreeResult
    | SessionBeforeSwitchResult
    | SessionBeforeForkResult
    | None
):
    """Sequential, ``cancel=True`` short-circuits, else last truthy wins.

    Shared across ``session_before_compact``, ``session_before_tree``,
    ``session_before_switch``, and ``session_before_fork``; all four
    result types expose a ``cancel`` field with the same first-cancel-wins
    semantics (Pi parity: ``runner.ts:680-712``).

    Sprint 6h₅a (Phase 4.14, ADR-0081, P-332) — widened to accept the 2
    new result types ``SessionBeforeSwitchResult`` /
    ``SessionBeforeForkResult`` for the new ``session_before_switch`` /
    ``session_before_fork`` events.
    """

    last: (
        SessionBeforeCompactResult
        | SessionBeforeTreeResult
        | SessionBeforeSwitchResult
        | SessionBeforeForkResult
        | None
    ) = None
    for handler, mode in handlers:
        raw = await _safe_invoke(handler, event, ctx, mode)
        if isinstance(
            raw,
            SessionBeforeCompactResult
            | SessionBeforeTreeResult
            | SessionBeforeSwitchResult
            | SessionBeforeForkResult,
        ):
            if raw.cancel:
                return raw
            last = raw
    return last


def _apply_stream_options_patch(
    base: dict[str, Any], patch: dict[str, Any] | None
) -> dict[str, Any]:
    """Sprint 6a (P-41) — Pi ``applyStreamOptionsPatch`` deep-merge port.

    Pi parity: ``agent-harness.ts:89-129`` (SHA 734e08e). Deep merges
    ``patch`` into a clone of ``base`` with delete-on-``None`` semantics
    for nested ``headers`` and ``metadata`` keys (Pi: TS-undefined deletes
    the key rather than overwriting with undefined).

    Scalar keys (``transport`` / ``timeoutMs`` / ``maxRetries`` /
    ``maxRetryDelayMs`` / ``cacheRetention``): present-in-patch wins,
    including explicit ``None`` (Pi ``Object.hasOwn(patch, key)``).

    ``headers`` / ``metadata``: deep merge with key-level delete-on-None.
    Result is collapsed to ``None`` when the merged dict ends up empty.

    Returns the merged dict. Pi (``agent-harness.ts:103``) returns the
    cloned base when ``patch`` is falsy / undefined; we preserve that
    semantic by returning the base shallow-clone.
    """

    if patch is None:
        return dict(base)

    result: dict[str, Any] = dict(base)

    for key in (
        "transport",
        "timeoutMs",
        "maxRetries",
        "maxRetryDelayMs",
        "cacheRetention",
    ):
        if key in patch:
            result[key] = patch[key]

    if "headers" in patch:
        patch_headers = patch["headers"]
        if patch_headers is None:
            result.pop("headers", None)
        else:
            headers = dict(result.get("headers") or {})
            for k, v in patch_headers.items():
                if v is None:
                    headers.pop(k, None)
                else:
                    headers[k] = v
            if headers:
                result["headers"] = headers
            else:
                result.pop("headers", None)

    if "metadata" in patch:
        patch_metadata = patch["metadata"]
        if patch_metadata is None:
            result.pop("metadata", None)
        else:
            metadata = dict(result.get("metadata") or {})
            for k, v in patch_metadata.items():
                if v is None:
                    metadata.pop(k, None)
                else:
                    metadata[k] = v
            if metadata:
                result["metadata"] = metadata
            else:
                result.pop("metadata", None)

    return result


async def _reducer_before_provider_request(
    handlers: list[HandlerEntry],
    event: BeforeProviderRequestHookEvent,
    ctx: ExtensionContext,
) -> BeforeProviderRequestResult | None:
    """Sequential patch chain. Each handler sees previous-chained stream_options.

    Pi parity: ``agent-harness.ts:232-250`` (SHA 734e08e) — handlers iterate,
    each receiving ``cloneStreamOptions(current)``; ``applyStreamOptionsPatch``
    updates ``current`` if a handler returns ``result.streamOptions``.

    Sprint 6a (P-41 fix): the previous shallow ``dict.update`` merge has
    been replaced with :func:`_apply_stream_options_patch`, a verbatim
    port of Pi ``applyStreamOptionsPatch`` (``agent-harness.ts:89-129``)
    with delete-on-``None`` semantics for nested ``headers`` /
    ``metadata`` keys.
    """

    current: dict[str, Any] = dict(event.stream_options)
    modified = False
    for handler, mode in handlers:
        chained = BeforeProviderRequestHookEvent(
            model=event.model,
            session_id=event.session_id,
            stream_options=dict(current),
        )
        raw = await _safe_invoke(handler, chained, ctx, mode)
        if (
            isinstance(raw, BeforeProviderRequestResult)
            and raw.stream_options is not None
        ):
            current = _apply_stream_options_patch(current, raw.stream_options)
            modified = True
    return BeforeProviderRequestResult(stream_options=current) if modified else None


async def _reducer_before_provider_payload(
    handlers: list[HandlerEntry],
    event: BeforeProviderPayloadHookEvent,
    ctx: ExtensionContext,
) -> BeforeProviderPayloadResult | None:
    """Sequential payload chain — each handler sees previous handler's payload.

    Pi parity: ``agent-harness.ts:265-280`` (SHA 734e08e).

    **``payload=None`` semantics (W4 / mirrors
    :class:`BeforeProviderPayloadResult` docstring):**
    A handler that wants to **skip** the payload mutation hook entirely
    should return ``None`` (the bare value, NOT a result wrapper). The
    reducer only chains when the handler returns a
    :class:`BeforeProviderPayloadResult` instance — a bare ``None`` is
    treated as "no opinion" and the chain falls through unchanged.

    Returning ``BeforeProviderPayloadResult(payload=None)`` is currently
    treated as **"explicit clear to None"** — ``current`` becomes ``None``
    and the chain is marked modified. This mirrors Pi
    ``BeforeProviderPayloadResult`` where the dataclass field is the
    explicit replacement value, distinct from "the handler returned no
    result wrapper at all". The
    :class:`BeforeProviderPayloadResult` docstring is the canonical
    description of the "absent vs explicit empty / None" distinction —
    handlers wanting to clear without setting `None` should pass an
    explicit empty value for the provider payload shape (e.g. ``{}``).
    """

    current = event.payload
    modified = False
    for handler, mode in handlers:
        chained = BeforeProviderPayloadHookEvent(model=event.model, payload=current)
        raw = await _safe_invoke(handler, chained, ctx, mode)
        if isinstance(raw, BeforeProviderPayloadResult):
            current = raw.payload
            modified = True
    return BeforeProviderPayloadResult(payload=current) if modified else None


async def _reducer_observational(
    handlers: list[HandlerEntry],
    event: HookEvent,
    ctx: ExtensionContext,
) -> None:
    """Run every handler for side effects; ignore return values.

    Handlers run serially in registration order. Return values are ignored.
    """

    for handler, mode in handlers:
        await _safe_invoke(handler, event, ctx, mode)
    return None


# === Sprint 5a (Phase 3.1) — input / user_bash / resources_discover reducers ===


async def _reducer_input(
    handlers: list[HandlerEntry],
    event: InputHookEvent,
    ctx: ExtensionContext,
) -> InputResult | None:
    """Pi parity reducer for ``input`` (``agent-session.ts:987-1015`` region).

    - First handler returning :class:`InputHandled` short-circuits the chain
      (the CLI loop will NOT process the input).
    - :class:`InputTransform` chains: each transform sees the previously
      transformed text/images.
    - :class:`InputContinue` (or bare ``None``) is passthrough.
    """

    current = event
    last_result: InputResult | None = None
    for handler, mode in handlers:
        raw = await _safe_invoke(handler, current, ctx, mode)
        if isinstance(raw, InputHandled):
            return raw
        if isinstance(raw, InputTransform):
            last_result = raw
            current = InputHookEvent(
                text=raw.text,
                images=raw.images,
                source=event.source,
            )
            continue
        if isinstance(raw, InputContinue):
            last_result = raw
            continue
        # bare None → no opinion; equivalent to InputContinue.
    return last_result


async def _reducer_user_bash(
    handlers: list[HandlerEntry],
    event: UserBashHookEvent,
    ctx: ExtensionContext,
) -> UserBashResult | None:
    """Pi parity reducer for ``user_bash`` (``agent-session.ts:1403`` region).

    Returns the LAST :class:`UserBashResult` produced by a handler — Pi's
    CLI loop picks ``result?`` (full replacement) when set, otherwise uses
    ``operations`` (custom dispatcher). Earlier handlers are observational.
    """

    last: UserBashResult | None = None
    for handler, mode in handlers:
        raw = await _safe_invoke(handler, event, ctx, mode)
        if isinstance(raw, UserBashResult):
            last = raw
    return last


async def _reducer_resources_discover(
    handlers: list[HandlerEntry],
    event: ResourcesDiscoverHookEvent,
    ctx: ExtensionContext,
) -> ResourcesDiscoverResult | None:
    """Pi parity reducer for ``resources_discover``
    (``agent-session.ts:2059-2068``).

    Each handler may contribute paths to any of three buckets
    (skill / prompt / theme). The reducer concatenates them in handler
    order, then de-duplicates within each bucket preserving first occurrence.
    """

    skills: list[str] = []
    prompts: list[str] = []
    themes: list[str] = []
    seen = False
    for handler, mode in handlers:
        raw = await _safe_invoke(handler, event, ctx, mode)
        if not isinstance(raw, ResourcesDiscoverResult):
            continue
        seen = True
        if raw.skill_paths:
            skills.extend(raw.skill_paths)
        if raw.prompt_paths:
            prompts.extend(raw.prompt_paths)
        if raw.theme_paths:
            themes.extend(raw.theme_paths)
    if not seen:
        return None

    def _dedup(items: list[str]) -> list[str] | None:
        if not items:
            return None
        seen_set: set[str] = set()
        out: list[str] = []
        for item in items:
            if item in seen_set:
                continue
            seen_set.add(item)
            out.append(item)
        return out

    return ResourcesDiscoverResult(
        skill_paths=_dedup(skills),
        prompt_paths=_dedup(prompts),
        theme_paths=_dedup(themes),
    )


_REDUCERS: dict[HookEventName, Callable[..., Awaitable[Any]]] = {
    # === existing entries ===
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
    # === Sprint 3a additions ===
    "queue_update": _reducer_observational,
    "save_point": _reducer_observational,
    "abort": _reducer_observational,
    "before_provider_request": _reducer_before_provider_request,
    "before_provider_payload": _reducer_before_provider_payload,
    "after_provider_response": _reducer_observational,
    "session_compact": _reducer_observational,
    "session_before_tree": _reducer_session_before,
    "session_tree": _reducer_observational,
    "model_select": _reducer_observational,
    "thinking_level_select": _reducer_observational,
    "resources_update": _reducer_observational,
    # === Sprint 5a (Phase 3.1) additions ===
    "input": _reducer_input,
    "user_bash": _reducer_user_bash,
    "resources_discover": _reducer_resources_discover,
    # === Sprint 6h₅a (Phase 4.14, ADR-0081) additions ===
    "session_start": _reducer_observational,
    "session_before_switch": _reducer_session_before,
    "session_before_fork": _reducer_session_before,
    "session_shutdown": _reducer_observational,
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
# Sprint 3a additions ---------------------------------------------------------
QueueUpdateHandler = Callable[
    [QueueUpdateHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
SavePointHandler = Callable[
    [SavePointHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
AbortHandler = Callable[
    [AbortHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
BeforeProviderRequestHandler = Callable[
    [BeforeProviderRequestHookEvent, "ExtensionContext"],
    BeforeProviderRequestResult | None | Awaitable[BeforeProviderRequestResult | None],
]
BeforeProviderPayloadHandler = Callable[
    [BeforeProviderPayloadHookEvent, "ExtensionContext"],
    BeforeProviderPayloadResult | None | Awaitable[BeforeProviderPayloadResult | None],
]
AfterProviderResponseHandler = Callable[
    [AfterProviderResponseHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
SessionCompactHandler = Callable[
    [SessionCompactHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
SessionBeforeTreeHandler = Callable[
    [SessionBeforeTreeHookEvent, "ExtensionContext"],
    SessionBeforeTreeResult | None | Awaitable[SessionBeforeTreeResult | None],
]
SessionTreeHandler = Callable[
    [SessionTreeHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
ModelSelectHandler = Callable[
    [ModelSelectHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
ThinkingLevelSelectHandler = Callable[
    [ThinkingLevelSelectHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
ResourcesUpdateHandler = Callable[
    [ResourcesUpdateHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
# Sprint 5a (Phase 3.1) additions --------------------------------------------
InputHandler = Callable[
    [InputHookEvent, "ExtensionContext"],
    InputResult | None | Awaitable[InputResult | None],
]
UserBashHandler = Callable[
    [UserBashHookEvent, "ExtensionContext"],
    UserBashResult | None | Awaitable[UserBashResult | None],
]
ResourcesDiscoverHandler = Callable[
    [ResourcesDiscoverHookEvent, "ExtensionContext"],
    ResourcesDiscoverResult | None | Awaitable[ResourcesDiscoverResult | None],
]
# Sprint 6h₅a (Phase 4.14, ADR-0081) additions ---------------------------------
SessionStartHandler = Callable[
    [SessionStartHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
SessionBeforeSwitchHandler = Callable[
    [SessionBeforeSwitchHookEvent, "ExtensionContext"],
    SessionBeforeSwitchResult | None | Awaitable[SessionBeforeSwitchResult | None],
]
SessionBeforeForkHandler = Callable[
    [SessionBeforeForkHookEvent, "ExtensionContext"],
    SessionBeforeForkResult | None | Awaitable[SessionBeforeForkResult | None],
]
SessionShutdownHandler = Callable[
    [SessionShutdownHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]


# === The bus ===


class HookBus:
    """Holds per-event handler lists and dispatches via the right reducer.

    Construction takes a ``ctx_factory`` returning the current
    :class:`ExtensionContext` (built fresh per emit so stale-detection works
    when the harness is disposed mid-flight).

    Sprint 3a (ADR-0019 v3): each registration also stores a per-handler
    ``error_mode`` (default ``"throw"`` — matches Pi shipped behavior).
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
        # ADR-0019 v3 — per-(event_type, id(handler)) error policy. Default
        # entry for any handler missing from this map is ``"throw"`` so any
        # historical callsite that omitted the kwarg keeps Pi-parity behavior.
        self._error_modes: dict[tuple[HookEventName, int], HookErrorMode] = {}

    # --- Subscription overloads (D.1.2 + ADR-0019 v3) ---

    # NOTE: 35 overloads below mirror :data:`HookEventName` (Sprint 6h₅a
    # Phase 4.14 added 4 extension session lifecycle events on top of the
    # Sprint 5a 31-overload baseline). Every overload exposes the
    # ``error_mode`` kwarg (default ``"throw"`` = Pi parity).

    @overload
    def on(
        self,
        event_type: Literal["context"],
        handler: ContextHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["before_agent_start"],
        handler: BeforeAgentStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["tool_call"],
        handler: ToolCallHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["tool_result"],
        handler: ToolResultHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["message_end"],
        handler: MessageEndHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["agent_start"],
        handler: AgentStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["agent_end"],
        handler: AgentEndHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["turn_start"],
        handler: TurnStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["turn_end"],
        handler: TurnEndHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["message_start"],
        handler: MessageStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["message_update"],
        handler: MessageUpdateHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["tool_execution_start"],
        handler: ToolExecutionStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["tool_execution_update"],
        handler: ToolExecutionUpdateHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["tool_execution_end"],
        handler: ToolExecutionEndHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["session_before_compact"],
        handler: SessionBeforeCompactHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["settled"],
        handler: SettledHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    # --- Sprint 3a additions ---
    @overload
    def on(
        self,
        event_type: Literal["queue_update"],
        handler: QueueUpdateHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["save_point"],
        handler: SavePointHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["abort"],
        handler: AbortHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["before_provider_request"],
        handler: BeforeProviderRequestHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["before_provider_payload"],
        handler: BeforeProviderPayloadHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["after_provider_response"],
        handler: AfterProviderResponseHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["session_compact"],
        handler: SessionCompactHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["session_before_tree"],
        handler: SessionBeforeTreeHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["session_tree"],
        handler: SessionTreeHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["model_select"],
        handler: ModelSelectHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["thinking_level_select"],
        handler: ThinkingLevelSelectHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["resources_update"],
        handler: ResourcesUpdateHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    # --- Sprint 5a (Phase 3.1) additions ---
    @overload
    def on(
        self,
        event_type: Literal["input"],
        handler: InputHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["user_bash"],
        handler: UserBashHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["resources_discover"],
        handler: ResourcesDiscoverHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    # --- Sprint 6h₅a (Phase 4.14, ADR-0081) additions ---
    @overload
    def on(
        self,
        event_type: Literal["session_start"],
        handler: SessionStartHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["session_before_switch"],
        handler: SessionBeforeSwitchHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["session_before_fork"],
        handler: SessionBeforeForkHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event_type: Literal["session_shutdown"],
        handler: SessionShutdownHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...

    def on(  # pyright: ignore[reportInconsistentOverload]
        self,
        event_type: HookEventName,
        handler: HookHandler,
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]:
        """Register a handler. Returns an unsubscribe callable.

        Raises ``KeyError`` for unknown event types — strict by design per
        spec B5 (typo defence beats Pi's permissive empty-set fallback).

        ``error_mode`` (ADR-0019 v3) defaults to ``"throw"`` matching Pi
        shipped behavior. ``"continue"`` is an Aelix additive opt-in: handler
        exceptions are logged + swallowed and the reducer continues.

        NOTE: 35 ``@overload`` declarations above provide static narrowing
        per event name (handler param typed as ``XxxHandler`` with
        ``XxxHookEvent`` payload — Sprint 6h₅a Phase 4.14 added 4 on top
        of the Sprint 5a 31-overload baseline). The runtime impl uses the
        generic ``HookHandler`` signature (``HookEvent`` union) which
        pyright cannot reconcile with the narrowed overloads — pyright
        lacks the contravariance proof. The narrowing is verified by
        ``scripts/pyright_spike.py`` which exercises each overload against
        a concrete handler and asserts pyright sees the narrowed payload
        type. Suppression is scoped to ``reportInconsistentOverload`` only.
        """

        if event_type not in HOOK_RESULT_TYPES:
            raise KeyError(f"Unknown hook event: {event_type!r}")
        bucket = self._handlers.setdefault(event_type, [])
        bucket.append(handler)
        key = (event_type, id(handler))
        self._sources[key] = source
        self._error_modes[key] = error_mode
        if cleanup is not None:
            self._cleanups.append(cleanup)

        def unsubscribe() -> None:
            try:
                bucket.remove(handler)
            except ValueError:
                return
            self._sources.pop(key, None)
            self._error_modes.pop(key, None)

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
        re-raised so the harness can wrap them in :class:`AgentHarnessError`
        when the per-handler ``error_mode="throw"`` (default).
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
            # Pair each handler with its registered error_mode. Missing entries
            # (shouldn't happen under normal use) fall back to ``"throw"``
            # which preserves the pre-3a / Pi-parity behavior.
            entries: list[HandlerEntry] = [
                (h, self._error_modes.get((event_type, id(h)), "throw"))
                for h in handlers
            ]
            result = await reducer(entries, event, ctx)
        for observer in observers:
            # Observer return values are intentionally not collected; observer
            # errors swallow (listener-style) to keep telemetry from breaking
            # the chain (Pi parity with subscribe()).
            try:
                await _maybe_await(observer(event, ctx))
            except Exception as exc:
                _log.debug("hook observer raised: %r", exc, exc_info=True)
        return result

    async def dispose(self) -> None:
        """Run all cleanups LIFO, then clear handlers and observers.

        After ``dispose()``, handlers and observers are cleared but the bus
        remains usable — new registrations are accepted. To prevent re-use,
        the owning :class:`~aelix_agent_core.harness.core.AgentHarness` should not be
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
        self._error_modes.clear()


__all__ = [
    "BUILTIN_TOOL_NAMES",
    "HOOK_RESULT_TYPES",
    "AbortHandler",
    "AbortHookEvent",
    "AfterProviderResponseHandler",
    "AfterProviderResponseHookEvent",
    "AgentEndHandler",
    "AgentEndHookEvent",
    "AgentEventName",
    "AgentHarnessEventName",
    "AgentStartHandler",
    "AgentStartHookEvent",
    "BashOperations",
    "BashResult",
    "BashToolCallHookEvent",
    "BashToolResultHookEvent",
    "BeforeAgentStartHandler",
    "BeforeAgentStartHookEvent",
    "BeforeAgentStartResult",
    "BeforeProviderPayloadHandler",
    "BeforeProviderPayloadHookEvent",
    "BeforeProviderPayloadResult",
    "BeforeProviderRequestHandler",
    "BeforeProviderRequestHookEvent",
    "BeforeProviderRequestResult",
    "ContextHandler",
    "ContextHookEvent",
    "ContextResult",
    "CustomToolCallHookEvent",
    "CustomToolResultHookEvent",
    "EditToolCallHookEvent",
    "EditToolResultHookEvent",
    "FindToolCallHookEvent",
    "FindToolResultHookEvent",
    "GrepToolCallHookEvent",
    "GrepToolResultHookEvent",
    "HandlerEntry",
    "HookBus",
    "HookCleanup",
    "HookErrorMode",
    "HookEvent",
    "HookEventName",
    "HookHandler",
    "HookObserver",  # exported: observers are part of the documented public API
    "HookRegistration",
    "InputContinue",
    "InputHandled",
    "InputHandler",
    "InputHookEvent",
    "InputResult",
    "InputTransform",
    "LsToolCallHookEvent",
    "LsToolResultHookEvent",
    "MessageEndHandler",
    "MessageEndHookEvent",
    "MessageStartHandler",
    "MessageStartHookEvent",
    "MessageUpdateHandler",
    "MessageUpdateHookEvent",
    "ModelSelectHandler",
    "ModelSelectHookEvent",
    "QueueUpdateHandler",
    "QueueUpdateHookEvent",
    "ReadToolCallHookEvent",
    "ReadToolResultHookEvent",
    "ResourcesDiscoverHandler",
    "ResourcesDiscoverHookEvent",
    "ResourcesDiscoverResult",
    "ResourcesUpdateHandler",
    "ResourcesUpdateHookEvent",
    "SavePointHandler",
    "SavePointHookEvent",
    "SessionBeforeCompactHandler",
    "SessionBeforeCompactHookEvent",
    "SessionBeforeCompactResult",
    "SessionBeforeForkHandler",
    "SessionBeforeForkHookEvent",
    "SessionBeforeForkResult",
    "SessionBeforeSwitchHandler",
    "SessionBeforeSwitchHookEvent",
    "SessionBeforeSwitchResult",
    "SessionBeforeTreeHandler",
    "SessionBeforeTreeHookEvent",
    "SessionBeforeTreeResult",
    "SessionCompactHandler",
    "SessionCompactHookEvent",
    "SessionShutdownHandler",
    "SessionShutdownHookEvent",
    "SessionStartHandler",
    "SessionStartHookEvent",
    "SessionTreeHandler",
    "SessionTreeHookEvent",
    "SettledHandler",
    "SettledHookEvent",
    "ThinkingLevelSelectHandler",
    "ThinkingLevelSelectHookEvent",
    "ToolCallHandler",
    "ToolCallHookEvent",
    "ToolCallResult",
    "ToolExecutionEndHandler",
    "ToolExecutionEndHookEvent",
    "ToolExecutionStartHandler",
    "ToolExecutionStartHookEvent",
    "ToolExecutionUpdateHandler",
    "ToolExecutionUpdateHookEvent",
    "ToolResultHandler",
    "ToolResultHookEvent",
    "ToolResultPatch",
    "TurnEndHandler",
    "TurnEndHookEvent",
    "TurnStartHandler",
    "TurnStartHookEvent",
    "UserBashHandler",
    "UserBashHookEvent",
    "UserBashResult",
    "WriteToolCallHookEvent",
    "WriteToolResultHookEvent",
    "is_tool_call_event_type",
    "is_tool_result_event_type",
    "make_tool_call_event",
    "make_tool_result_event",
]
