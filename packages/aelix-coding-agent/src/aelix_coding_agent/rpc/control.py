"""Additive, versioned runtime control API for non-terminal hosts (ADR-0275).

This is separate from the 29-command Pi compatibility roster. Nothing here
invokes a model or falls through from an unknown command to a model prompt.

Lifecycle rules (#594):

- Mutations serialize on one lock and need an idle runtime with no classic RPC
  command in flight. Classic commands that start a turn or replace the session
  are refused while a mutation holds the lock; while a session lifecycle
  transition runs, every classic command that is not a read or an abort is
  refused.
- A lifecycle transition is any harness replacement the control surface starts:
  ``resources/reload``, ``sessions/branch``, and an extension command's own
  ``ctx.new_session`` / ``fork`` / ``switch_session`` / ``reload`` (command
  contexts get :class:`_GuardedSessionRuntime`). Each disposes the old harness
  before the factory builds the new one, so it always runs to completion.
- ``commands/cancel`` targets one operation: by ``requestId``, or the running
  one. Only operations that undo themselves are cancellable once running. A
  direct transition finishes and the cancel says it was not cancelled; a
  cancelled command whose own transition is running stops when it finishes.
- Shutdown refuses new mutations, cancels queued and cancellable ones, settles
  open dialogs as cancelled, and lets a running lifecycle transition finish.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, TypeVar, cast

from aelix_agent_core.harness.prompt_templates import (
    format_prompt_template_invocation,
    parse_command_args,
)
from aelix_agent_core.harness.skills import format_skill_invocation
from aelix_agent_core.harness.tool_policy import ToolPolicy
from aelix_agent_core.session.session_lock import SessionLockContention, WriterKind
from aelix_ai.models import get_supported_thinking_levels

from aelix_coding_agent.builtin.permission_mode import PermissionMode, authority_rank
from aelix_coding_agent.core.resource_filter import validate_entries
from aelix_coding_agent.extensions.command_dispatch import (
    CommandDispatchService,
    CommandSurfaceBindings,
)
from aelix_coding_agent.rpc.control_children import (
    NO_DELEGATED_AGENTS,
    SESSION_CHANGE_IN_PROGRESS,
    SETTLE_GRACE_SECONDS,
    Intervention,
    children_call,
    intervene,
)
from aelix_coding_agent.rpc.control_mcp import RpcMcpController
from aelix_coding_agent.rpc.control_transcript import (
    BINDING_CHANGING,
    BINDING_UNAVAILABLE,
    TranscriptError,
    TranscriptService,
)
from aelix_coding_agent.rpc.control_transcript import METHOD as TRANSCRIPT_METHOD
from aelix_coding_agent.rpc.control_ui import RpcCommandUI
from aelix_coding_agent.subagent_contract import HostResume

if TYPE_CHECKING:
    from aelix_coding_agent.core.agent_selection import DelegationView

_T = TypeVar("_T")

# Explicit setters only: credentials, arbitrary attribute access, and project
# trust are not writable through an introspection endpoint.
SETTING_FIELDS: dict[str, tuple[str, type, tuple[Any, ...] | None]] = {
    "default_provider": ("default_provider", str, None),
    "default_model": ("default_model", str, None),
    "default_thinking_level": (
        "default_thinking_level",
        str,
        ("off", "minimal", "low", "medium", "high", "xhigh", "max"),
    ),
    "steering_mode": ("steering_mode", str, ("all", "one-at-a-time")),
    "follow_up_mode": ("follow_up_mode", str, ("all", "one-at-a-time")),
    "compaction_enabled": ("compaction_enabled", bool, None),
    "retry_enabled": ("retry_enabled", bool, None),
    "hide_thinking_block": ("hide_thinking_block", bool, None),
    "hide_compaction_summary": ("hide_compaction_summary", bool, None),
    "respect_gitignore": ("respect_gitignore", bool, None),
    "check_for_updates": ("check_for_updates", bool, None),
    "check_model_catalog": ("check_model_catalog", bool, None),
    "skill_paths": ("skill_paths", list, None),
    "prompt_template_paths": ("prompt_template_paths", list, None),
    # #795 (ADR-0304 D12): the GLOBAL file's ``agents`` on/off list.
    "agent_profiles": ("agent_profiles", list, None),
    "enabled_models": ("enabled_models", list, None),
}

#: The settings keys that are #511's resource lists, and their JSON names: RPC
#: reads and writes them through ``SettingsManager.read_list`` / ``edit_list``.
RESOURCE_LIST_KEYS: dict[str, str] = {
    "skill_paths": "skills",
    "prompt_template_paths": "prompts",
    "agent_profiles": "agents",
}

READ_ONLY_METHODS = frozenset(
    {
        "capabilities",
        TRANSCRIPT_METHOD,
        "resources/list",
        "settings/get",
        "permissions/get",
        "permissions/evaluate",
        "mcp/status",
        "extensions/list",
        "auth/status",
        "agents/list",
        "agents/show",
        "selections/list",
        # #612 (ADR-0307): a host's view of the delegated children, lock-free.
        "children/list",
        "children/events",
    }
)
MUTATING_METHODS = frozenset(
    {
        "commands/run",
        "resources/reload",
        "settings/set",
        "permissions/set",
        "tools/set_active",
        "ui/enable",
        "mcp/configure",
        "auth/set_api_key",
        "auth/logout",
        "auth/login",
        "agents/use",
        "agents/run",
        "sessions/branch",
        "selections/set",
        # #612 (owner Q4, 2026-11-18): the host's wake — one notice turn.
        "children/notices",
        # #836 (ADR-0318): a stopped agent resumed in the foreground, as agents/run runs one.
        "children/resume",
    }
)
# Running operations that undo themselves when cancelled: a command handler and a
# delegation are interrupted where they stand, a login abandons its flow, an
# MCP configuration rolls back to the previous lease, and a notice turn ends as
# a cancelled turn does.
CANCELLABLE_METHODS = frozenset(
    {
        "commands/run",
        "agents/run",
        "auth/login",
        "mcp/configure",
        "children/notices",
        "children/resume",
    }
)
# #612 (ADR-0307, contract r5 §3.4): a host's interventions on a running child.
# Admitted BEFORE the mutation lock — they are answered while ``agents/run`` holds
# it — but fenced against every session change, bound to the harness generation
# that admitted them, and settled when a change begins. Not ``_Operation``s:
# ``commands/cancel {}`` cancels "the running one", and an intervention is not it.
CONCURRENT_METHODS = frozenset({"children/steer", "children/wrap_up", "children/stop"})
# Session lifecycle transitions: never interrupted half-way (a reload cancelled
# between disposing the old harness and installing the new one leaves a disposed
# harness live).
LIFECYCLE_METHODS = frozenset({"resources/reload", "sessions/branch"})
# Mutations that are safe while a turn runs. ``permissions/set`` is one too when
# it only tightens (#853, :meth:`RpcControlPlane.tightens`).
TURN_SAFE_METHODS = frozenset({"ui/enable"})

CLASSIC_RUNNING = "A classic RPC command is still running; retry when it has answered"
IDLE_ONLY = "Runtime mutations require an idle session"


def narrows(current: PermissionMode, wanted: PermissionMode) -> bool:
    """#853 — whether ``wanted`` blocks, wherever this plane serves, at least what ``current`` blocks.

    The modes a mid-turn ``permissions/set {tighten: true}`` may go to (fix
    round 1, Codex C3; the lead's call). Not the authority rank alone: ``auto``'s
    shell classifier blocks a command that ``default`` and ``auto-accept-edits``
    let an RPC parent run (it has no approval UI, so both reach the headless
    verdict, which allows), so from ``auto`` only ``plan`` and ``auto`` itself
    narrow. From every other mode, each mode ranked at or below it does: the
    parent's own gate blocks nothing in ``default``, ``auto-accept-edits`` or
    ``yolo`` there, and a delegated child's posture follows the rank (its floor
    is ``child_permission_mode``'s, which never runs a child at ``auto``).
    Measured against the real gate, the child clamp and the parent's arbiter,
    with and without RPC dialogs and child approvals:
    ``tests/rpc/test_permissions_tighten_853.py::test_the_admitted_pairs_are_the_measured_ones``.
    """

    if wanted is current or wanted is PermissionMode.PLAN:
        return True
    if current is PermissionMode.AUTO:
        return False
    return authority_rank(wanted) < authority_rank(current)


# Classic RPC commands that never change the runtime.
CLASSIC_READ_ONLY = frozenset(
    {
        "get_state",
        "get_messages",
        "get_available_models",
        "get_commands",
        "get_session_stats",
        "get_fork_messages",
        "get_last_assistant_text",
        "export_html",
        "abort",
        "abort_retry",
        "abort_bash",
    }
)
# Classic RPC commands that start a turn or replace the session.
CLASSIC_TRANSITIONS = frozenset(
    {"prompt", "new_session", "switch_session", "fork", "clone", "compact"}
)
# The classic commands that replace the session (#663, verify V2): while one runs
# it counts as a lifecycle transition, so a ``prompt`` (or any other mutator) is
# refused instead of running on the harness it is tearing down.
CLASSIC_SWAPS = frozenset({"new_session", "switch_session", "fork", "clone"})


@dataclass
class _Operation:
    method: str
    request_id: object
    task: asyncio.Task[Any] | None
    active: bool = False
    cancellable: bool = False
    """Set by a method while it waits on something a cancel may end (#624: the
    ``selections/set`` consent dialog, ADR-0305 D4), outside ``CANCELLABLE_METHODS``."""


async def _finish_uncancelled(awaitable: Awaitable[_T]) -> _T:
    """Run ``awaitable`` to completion even if the caller is cancelled meanwhile.

    The caller keeps waiting (and therefore keeps holding the mutation lock) until
    the transition is over; a cancellation received meanwhile is re-raised after.
    """

    task = asyncio.ensure_future(awaitable)
    interrupted = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done():
                raise
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError
    return result


class _GuardedSessionRuntime:
    """The session runtime an extension command's context drives over RPC (#594).

    ``new_session`` / ``fork`` / ``switch_session`` / ``reload`` replace the
    harness, so through this view they run as protected lifecycle transitions of
    the control operation that dispatched the command
    (:meth:`RpcControlPlane.transition`). Every other attribute is the runtime's
    own.
    """

    def __init__(self, plane: RpcControlPlane) -> None:
        self._plane = plane

    def __getattr__(self, name: str) -> Any:
        return getattr(self._plane.runtime, name)

    async def new_session(self, *args: Any, **kwargs: Any) -> Any:
        return await self._plane.transition(
            lambda: self._plane.runtime.new_session(*args, **kwargs)
        )

    async def fork(self, *args: Any, **kwargs: Any) -> Any:
        return await self._plane.transition(lambda: self._plane.runtime.fork(*args, **kwargs))

    async def switch_session(self, *args: Any, **kwargs: Any) -> Any:
        return await self._plane.transition(
            lambda: self._plane.runtime.switch_session(*args, **kwargs)
        )

    async def reload(self) -> Any:
        return await self._plane.transition(self._plane.runtime.reload)


class BranchReturnContention(ValueError):
    """The branch committed, then the explicit return switch met contention."""

    def __init__(self, message: str, contention: SessionLockContention) -> None:
        super().__init__(message)
        self.holder_kind: WriterKind = contention.holder_kind


class RpcControlPlane:
    """Operate on the live runtime, including after session replacement."""

    def __init__(
        self,
        runtime_host: Any,
        *,
        permission_posture: Any = None,
        settings_manager: Any = None,
        auth_storage: Any = None,
        model_registry: Any = None,
        agent_service: Any = None,
        extension_errors: Any = None,
        emit: Any = None,
        selections: Mapping[str, Any] | None = None,
        jobs: Any = None,
        permissions: Any = None,
        transcript: TranscriptService | None = None,
        session_lock_errors: bool = False,
    ) -> None:
        self.runtime = runtime_host
        self.session_lock_errors = session_lock_errors
        self.transcript = transcript
        self._transcript_epoch = 0
        self.posture = permission_posture
        self.settings = settings_manager
        self.auth = auth_storage
        self.agents = agent_service
        # #624 (ADR-0305 D1): ``kind -> control`` (``.selection``, ``.kind``),
        # the same objects the TUI's panels use; empty where the host has none.
        self.selections: dict[str, Any] = dict(selections or {})
        self.extension_errors = extension_errors if extension_errors is not None else []
        self.emit = emit or (lambda event: None)
        self.subscriptions: list[Any] = []
        self.model_registry = model_registry
        self.lock = asyncio.Lock()
        self.ui = RpcCommandUI(emit or (lambda event: None))
        self.mcp = RpcMcpController()
        self.operations: list[_Operation] = []
        self.classic_inflight = 0
        self.transitioning = 0
        self.classic_swapping = 0
        self.closing = False
        # #612 (ADR-0307): the process's job service (``children/notices``, the
        # ``aelix_notice`` wake) and its permission extension (the approval
        # back-channel, ADR-0308), each ``None`` where the host has none.
        self.jobs = jobs
        self.permissions = permissions
        #: The admitted ``CONCURRENT_METHODS`` requests still running (contract r5 §3.4).
        self.interventions: list[Intervention] = []
        #: Raised at every harness binding; an intervention keeps the one that admitted it.
        self.generation = 0
        #: This generation's ``SubagentControls`` (the seam's optional ``controls``), or ``None``.
        self.controls: Any = None
        #: Its hub subscription, kept apart from :attr:`subscriptions` so that it can
        #: outlive them at the host's end (:meth:`retire`).
        self._child_subscription: Callable[[], None] | None = None
        self.bind_progress(self.harness)

    @property
    def harness(self) -> Any:
        return self.runtime.harness

    # === Operation lifecycle ==================================================

    async def handle(
        self, method: object, params: object, request_id: object = None
    ) -> dict[str, Any]:
        if not isinstance(method, str) or not isinstance(params, dict):
            raise ValueError("Control method and params must be a string and object")
        if method == "commands/cancel":
            return self.cancel(params)
        if method in READ_ONLY_METHODS:
            if method == TRANSCRIPT_METHOD:
                return await self._dispatch(method, params, request_id)
            return await self._dispatch(method, params)
        if method in CONCURRENT_METHODS:
            return await self._concurrent(method, params)
        if method not in MUTATING_METHODS:
            raise ValueError(f"Unsupported runtime control method: {method}")
        if self.closing:
            raise ValueError("The RPC host is shutting down")
        if self._tightens_now(method, params):
            return self._set_mode(params)
        operation = _Operation(method, request_id, asyncio.current_task())
        self.operations.append(operation)
        try:
            async with self.lock:
                if self.closing:
                    raise ValueError("The RPC host is shutting down")
                operation.active = True
                if method not in TURN_SAFE_METHODS:
                    self._require_quiescent(any_phase=self.tightens(method, params))
                if method in LIFECYCLE_METHODS:
                    return await self.transition(lambda: self._dispatch(method, params))
                return await self._dispatch(method, params)
        finally:
            operation.active = False
            self.operations.remove(operation)

    async def transition(self, start: Callable[[], Awaitable[_T]]) -> _T:
        """Run one harness replacement to completion under the caller's operation.

        The caller already holds the mutation lock (a direct lifecycle method, or a
        command whose context asked for a replacement), so the lock stays held
        until the new harness is installed and rebound. While it runs, classic
        mutators are refused (:meth:`classic_gate`); a cancellation of the
        enclosing operation, by ``commands/cancel`` or shutdown, takes effect only
        once it has finished.
        """

        if self.closing:
            raise RuntimeError("The RPC host is shutting down")
        if self.classic_inflight:
            raise RuntimeError(
                "A classic RPC command is still running; retry the session change when it has answered"
            )
        self.transitioning += 1
        self._transcript_epoch += 1
        try:
            return await _finish_uncancelled(self._settled_then(start))
        finally:
            self.transitioning -= 1

    async def _settled_then(self, start: Callable[[], Awaitable[_T]]) -> _T:
        """#612 — the children's interventions of the outgoing harness end first, then ``start``.

        One shielded step (fix round 1, Codex C-M2): a ``commands/cancel`` answered
        ``deferred`` that lands while the settle waits must not drop the replacement.
        """

        await self.settle_interventions()
        return await start()

    def tightens(self, method: str, params: dict[str, Any]) -> bool:
        """#853 — a ``permissions/set {mode, tighten: true}`` to a mode that :func:`narrows` the current one.

        Such a request skips the turn's idle check, as ``ui/enable`` does (the
        lead's call, adopting the rule #853 quotes, narrowed in fix round 1 to
        :func:`narrows`, Codex C3). ONLY that check: while a classic command
        runs it is refused as before, :data:`CLASSIC_RUNNING`, a classic session
        swap included (fix round 1, verify V-F1, Codex C5), so an idle answer is
        v1's in every state. ``tighten`` other than ``true``, a mode that is not
        one, one that does not narrow, or a host with no posture is not a
        tighten, and goes through both checks as before. What the write reaches
        is what shift+tab's reaches during a turn: the next tool call, a
        delegated child's open approval request (answered ``no`` for ``plan``,
        ADR-0308) and each live child's floor (``tighten_all``, ADR-0306 D8) —
        ``tests/rpc/test_permissions_tighten_853.py``.
        """

        if method != "permissions/set" or params.get("tighten") is not True or self.posture is None:
            return False
        try:
            wanted = PermissionMode(params.get("mode"))
        except (TypeError, ValueError):
            return False
        return narrows(self.posture.get(), wanted)

    def _tightens_now(self, method: str, params: dict[str, Any]) -> bool:
        """#853 — a tighten answered AHEAD of the mutation lock (fix round 1, Codex C4; the lead's call).

        As ``children/stop`` and ``children/steer`` are admitted before it
        (#612): a tighten sent while a ``children/notices`` turn, an
        ``agents/run`` or a ``children/resume`` holds the lock reaches that
        turn's next tool call and that run's child, instead of waiting for them
        to end. It writes the posture with no ``await`` (:meth:`_set_mode`), so
        no session change can begin while it runs and it has no "unknown"
        outcome. Not ahead of the lock, and so waiting for it as before, while
        a session change holds it (``transitioning``: the change finishes
        first, and the posture, which every harness of this process shares,
        then takes the tighten), or while another ``permissions/set`` waits
        for it, so two writes land in the order they were sent. A classic
        command running is the refusal it always was (:meth:`_set_mode` checks
        it).
        """

        if self.transitioning or not self.tightens(method, params):
            return False
        return not any(op.method == "permissions/set" for op in self.operations)

    def _set_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        """A tighten admitted ahead of the lock (:meth:`_tightens_now`): the classic check, then the write."""

        if self.classic_inflight:
            raise ValueError(CLASSIC_RUNNING)
        return self._write_mode(params)

    def _write_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.posture is None:
            raise ValueError("This RPC host has no permission posture")
        self.posture.set(PermissionMode(params.get("mode")))
        return {"mode": self.posture.get().value}

    def _require_quiescent(self, *, any_phase: bool = False) -> None:
        """The idle checks a mutation passes: no classic command running, and (unless ``any_phase``) an idle harness."""

        if self.classic_inflight:
            raise ValueError(CLASSIC_RUNNING)
        if not any_phase and self.harness.phase != "idle":
            raise ValueError(IDLE_ONLY)

    def cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        target = params.get("requestId")
        if target is not None and not isinstance(target, str | int):
            raise ValueError("requestId must be a string or an integer")
        operation = next(
            (
                op
                for op in self.operations
                if (op.request_id == target if target is not None else op.active)
            ),
            None,
        )
        if operation is None or operation.task is None or operation.task.done():
            return {"cancelled": False}
        if (
            operation.active
            and operation.method not in CANCELLABLE_METHODS
            and not operation.cancellable
        ):
            return {"cancelled": False, "method": operation.method, "reason": "not_cancellable"}
        operation.task.cancel()
        if operation.active and self.transitioning:
            # The command's own session replacement finishes first; the cancel
            # lands as it returns (:func:`_finish_uncancelled`).
            return {"cancelled": True, "method": operation.method, "deferred": True}
        return {"cancelled": True, "method": operation.method}

    def begin_shutdown(self) -> None:
        """The client is gone: nothing new starts and nothing queued runs."""

        self.closing = True
        self.ui.close()
        self._cancel_interventions("shutdown")
        for operation in list(self.operations):
            task = operation.task
            if task is None or task.done():
                continue
            if not operation.active or operation.method in CANCELLABLE_METHODS:
                task.cancel()

    def classic_gate(self, command_type: object) -> tuple[str | None, bool]:
        """Admit a classic RPC command: ``(refusal, counted)``.

        A counted command must be released with :meth:`classic_done`.
        """

        if not isinstance(command_type, str) or command_type in CLASSIC_READ_ONLY:
            return None, False
        running = next((op.method for op in self.operations if op.active), None)
        if self.transitioning or self.classic_swapping:
            return (
                f"{command_type} is unavailable while a session lifecycle transition runs"
                + (f" (runtime control operation {running})" if running else ""),
                False,
            )
        if command_type in CLASSIC_TRANSITIONS and self.lock.locked():
            return (
                f"{command_type} is unavailable while a runtime control operation"
                f"{f' ({running})' if running else ''} is in progress",
                False,
            )
        self.classic_inflight += 1
        if command_type in CLASSIC_SWAPS:
            self.classic_swapping += 1
            self._transcript_epoch += 1
        return None, True

    def classic_done(self, command_type: object = None) -> None:
        self.classic_inflight = max(0, self.classic_inflight - 1)
        if command_type in CLASSIC_SWAPS:
            self.classic_swapping = max(0, self.classic_swapping - 1)

    # === Children's interventions (#612, ADR-0307; contract r5 §3.4) ============

    async def _concurrent(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Admit one ``CONCURRENT_METHODS`` request: lock-free, fenced, bound to this generation.

        Refused while the host shuts down or any session change runs — a control
        lifecycle method (``transitioning``) or a classic swap
        (``classic_swapping``). Admitted, it calls only the controls it captured
        here, and :meth:`settle_interventions` can end it.
        """

        if self.closing:
            raise ValueError("The RPC host is shutting down")
        if self.transitioning or self.classic_swapping:
            raise ValueError(SESSION_CHANGE_IN_PROGRESS)
        controls = self.controls
        if controls is None:
            raise ValueError(NO_DELEGATED_AGENTS)
        record = Intervention(method, asyncio.current_task(), self.generation, controls)
        self.interventions.append(record)
        try:
            return await intervene(record, params)
        finally:
            self.interventions.remove(record)

    def _cancel_interventions(self, why: str) -> list[asyncio.Task[Any]]:
        """Cancel every admitted intervention; each answers by its stage (``why`` names the cause)."""

        cancelled: list[asyncio.Task[Any]] = []
        for record in list(self.interventions):
            task = record.task
            if task is None or task.done():
                continue
            record.why = record.why or why
            task.cancel()
            cancelled.append(task)
        return cancelled

    async def settle_interventions(self) -> None:
        """A session change begins: cancel the interventions in flight and wait for them, at most 1 s.

        Fencing, not retirement: the controls stay this generation's until a new
        harness binds (:meth:`bind_progress`), so a change that is vetoed or fails
        admits again on the same generation.
        """

        cancelled = self._cancel_interventions("session")
        if cancelled:
            await asyncio.wait(cancelled, timeout=SETTLE_GRACE_SECONDS)

    async def settle_classic(self, command_type: object) -> None:
        """:meth:`settle_interventions` before a classic command that replaces the session runs.

        ``command_type`` is the client's raw ``type``: anything not a string is no
        swap, and is answered ``parse`` by the dispatcher (fix round 1, Codex C-M5:
        an unhashable one raised ``TypeError`` here and lost its reply).
        """

        if isinstance(command_type, str) and command_type in CLASSIC_SWAPS:
            await self.settle_interventions()

    # === Methods ==============================================================

    @property
    def transcript_available(self) -> bool:
        return (
            self.transcript is not None
            and self.transcript.available
            and not (self.closing or self.transitioning or self.classic_swapping)
        )

    @property
    def transcript_fence(self) -> tuple[int, tuple[int, str | None] | None]:
        return self._transcript_epoch, self.transcript.fence if self.transcript else None

    async def transcript_action(
        self, method: str, params: dict[str, Any], request_id: object
    ) -> dict[str, Any]:
        service = self.transcript
        if method != TRANSCRIPT_METHOD or service is None or self.closing:
            raise TranscriptError(BINDING_UNAVAILABLE)
        if self.transitioning or self.classic_swapping or service.runtime.transcript_transitioning:
            raise TranscriptError(BINDING_CHANGING)
        fence = self.transcript_fence
        result = await service.list(params, request_id)
        if not self.transcript_available or self.transcript_fence != fence:
            raise TranscriptError(BINDING_CHANGING)
        return result

    def capabilities(self) -> dict[str, Any]:
        methods = [
            "commands/run",
            "commands/cancel",
            "resources/list",
            "resources/reload",
            "tools/set_active",
            "ui/enable",
            "mcp/configure",
            "mcp/status",
            "extensions/list",
            "sessions/branch",
        ]
        if self.settings is not None:
            methods += ["settings/get", "settings/set"]
        if self.posture is not None:
            methods += ["permissions/get", "permissions/set", "permissions/evaluate"]
        if self.auth is not None:
            methods += ["auth/status", "auth/set_api_key", "auth/logout", "auth/login"]
        if self.agents is not None:
            methods += ["agents/list", "agents/show", "agents/use"]
        subagents = getattr(getattr(self.harness, "runtime", None), "subagents", None)
        if subagents is not None:
            methods.append("agents/run")
        if self.selections:
            methods += ["selections/list", "selections/set"]
        # #612 (contract r5 §3.5): each half on its own service — jobs with the
        # agents off list ``children/notices`` alone.
        if self.controls is not None:
            methods += [
                "children/list",
                "children/events",
                "children/steer",
                "children/wrap_up",
                "children/stop",
            ]
        if self.jobs is not None:
            methods.append("children/notices")
        if isinstance(subagents, HostResume):
            methods.append("children/resume")  # #836 (ADR-0318)
        if self.transcript_available:
            methods.append(TRANSCRIPT_METHOD)
        model = self.harness.current_model
        return {
            "version": 1,
            "methods": methods,
            "permissionModes": [mode.value for mode in PermissionMode] if self.posture else [],
            "thinkingLevels": get_supported_thinking_levels(model) if model else ["off"],
            "activeAgent": self.agents.active.name if self.agents and self.agents.active else None,
            "subagentsEnabled": subagents is not None,
            "selectionKinds": sorted(self.selections),
            **({"sessionLockErrors": 1} if self.session_lock_errors else {}),
        }

    async def _dispatch(
        self, method: str, params: dict[str, Any], request_id: object = None
    ) -> dict[str, Any]:
        if method == "capabilities":
            return self.capabilities()
        family = self._families(request_id).get(method.partition("/")[0])
        if family is not None:
            return await family(method, params)
        if method == "permissions/evaluate":
            return self.evaluate_permission(params)
        if method == "mcp/status":
            return self.mcp.status()
        if method == "extensions/list":
            from importlib.metadata import entry_points

            from aelix_coding_agent.cli.extension_install import list_installed_extensions

            references = {
                entry.name: entry.value for entry in entry_points(group="aelix.extensions")
            }
            return {
                "extensions": [
                    {
                        "name": entry.ep_name,
                        "distribution": entry.dist_name,
                        "version": entry.version,
                        "entry": references.get(entry.ep_name),
                    }
                    for entry in list_installed_extensions()
                ]
            }
        if method == "resources/list":
            return self.resources()
        if method == "settings/get":
            if self.settings is None:
                raise ValueError("This RPC host has no settings manager")
            return {
                "settings": {
                    key: (
                        self._resource_list(key)
                        if key in RESOURCE_LIST_KEYS
                        else getattr(self.settings, "get_" + field)()
                    )
                    for key, (field, _kind, _choices) in SETTING_FIELDS.items()
                },
                "schema": [
                    {
                        "key": key,
                        "type": {str: "string", bool: "boolean", list: "string_array"}[kind],
                        "choices": choices,
                        "scope": "user",
                        "requiresReload": True,
                    }
                    for key, (_field, kind, choices) in SETTING_FIELDS.items()
                ],
            }
        if method == "permissions/get":
            if self.posture is None:
                raise ValueError("This RPC host has no permission posture")
            return {"mode": self.posture.get().value}
        if method == "sessions/branch":
            return await self.branch_session(params)
        if method == "mcp/configure":
            return await self.mcp.configure(params.get("servers"), lambda: self.harness)
        if method == "permissions/set":
            return self._write_mode(params)
        if method == "settings/set":
            return await self.set_setting(params)
        if method == "resources/reload":
            result = await self.runtime.reload()
            return {"cancelled": result.cancelled}
        if method == "tools/set_active":
            names = params.get("names")
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise ValueError("Active tool names must refer to loaded tools")
            # #510 (ADR-0283, owner decision 5) — a name the RUN keeps off is
            # REFUSED with the reason, never dropped: an excluded tool (not
            # loaded at all) and one a flag or profile locks off. A name the
            # ``defaultTools`` setting turns off is turned on, as the panel
            # can: dropped from the policy's saved-off set until the settings
            # are read again (``resources/reload``, a new session), never
            # written. An extension's call drops all three.
            refusal = getattr(self.harness, "tool_refusal", None)
            if callable(refusal):
                for name in names:
                    reason = refusal(name)
                    if reason is not None:
                        raise ValueError(f"Cannot turn on {name}: {reason}")
            available = {tool.name for tool in self.harness.runtime.actions.get_all_tools()}
            if any(n not in available for n in names):
                raise ValueError("Active tool names must refer to loaded tools")
            policy = getattr(self.harness, "tool_policy", None)
            revived = (
                {n for n in names if n in policy.saved_off and policy.lock(n) is None}
                if isinstance(policy, ToolPolicy)
                else set()
            )
            if isinstance(policy, ToolPolicy) and revived:
                self.harness.restore_active_tools(
                    names, policy=replace(policy, saved_off=policy.saved_off - revived)
                )
            else:
                await self.harness.set_active_tools(names)
            return {"activeTools": self.harness.runtime.actions.get_active_tools()}
        if method == "commands/run":
            return await self.run_command(params)
        raise ValueError(f"Unsupported runtime control method: {method}")

    async def run_command(self, params: dict[str, Any]) -> dict[str, Any]:
        text = params.get("text")
        if not isinstance(text, str) or not text.startswith("/") or len(text) > 100_000:
            raise ValueError("Command text must start with / and fit in 100000 characters")
        messages: list[dict[str, str]] = []
        # The handler's ``ctx`` drives the runtime through the guarded view, so a
        # session replacement it starts is a protected transition (#594 review r1).
        service = CommandDispatchService(
            lambda: self.runtime.harness, session_runtime=_GuardedSessionRuntime(self)
        )
        command_runtime = self.harness.runtime
        previous_ui = command_runtime.ui
        if self.ui.enabled:
            command_runtime.bind_ui(self.ui)
        try:
            result = await service.try_execute(
                text,
                CommandSurfaceBindings(
                    emit_text=lambda text: messages.append({"type": "text", "text": text}),
                    emit_error=lambda text: messages.append({"type": "error", "text": text}),
                ),
            )
        finally:
            command_runtime.bind_ui(previous_ui)
        if result.outcome.value == "not_a_command":
            name, _, args = text[1:].partition(" ")
            for template in self.harness.prompt_templates:
                if template.name == name:
                    return {
                        "outcome": "prompt",
                        "command": name,
                        "prompt": format_prompt_template_invocation(
                            template, parse_command_args(args)
                        ),
                    }
            for skill in self.harness.skills:
                if name == "skill:" + skill.name:
                    return {
                        "outcome": "prompt",
                        "command": name,
                        "prompt": format_skill_invocation(skill, args or None),
                    }
        return {"outcome": result.outcome.value, "command": result.command, "messages": messages}

    def evaluate_permission(self, params: dict[str, Any]) -> dict[str, Any]:
        """The built-in gate's verdict for one call, by the tool's provenance (#594).

        ``tool`` names a tool in the live registry; its provenance is read from the
        object, never from the name, exactly as the gate reads it. Without ``tool``
        the arguments are taken as a call to aelix's own ``bash`` — the shape the
        ACP adapter sends after its approval bridge reported that provenance.
        """

        from aelix_coding_agent.builtin.permission import evaluate_tool_call
        from aelix_coding_agent.tools.provenance import builtin_provenance

        if self.posture is None:
            raise ValueError("This RPC host has no permission posture")
        args = params.get("args")
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        tool_name = params.get("tool")
        if tool_name is None:
            provenance: Any = "bash"
        elif isinstance(tool_name, str):
            tool = None
            for candidate in self.harness.state.tools:  # the LAST of a name runs (loop.py)
                if candidate.name == tool_name:
                    tool = candidate
            provenance = None if tool is None else builtin_provenance(tool)
        else:
            raise ValueError("tool must be a tool name")
        mode = self.posture.get()
        decision = evaluate_tool_call(
            mode, provenance, args, self.runtime.cwd or ".", settings=self.settings
        )
        return {"decision": decision, "provenance": provenance, "mode": mode.value}

    def resources(self) -> dict[str, Any]:
        harness = self.harness
        service = CommandDispatchService(lambda: self.runtime.harness, session_runtime=self.runtime)
        runtime = harness.runtime
        return {
            "commands": [
                {"name": name, "description": description}
                for name, description in service.list_commands()
            ],
            "tools": [
                {"name": tool.name, "description": tool.description}
                for tool in runtime.actions.get_all_tools()
            ],
            "activeTools": runtime.actions.get_active_tools(),
            "skills": [
                {"name": skill.name, "description": skill.description, "filePath": skill.file_path}
                for skill in harness.skills
            ],
            "prompts": [
                {"name": prompt.name, "description": prompt.description}
                for prompt in harness.prompt_templates
            ],
            "projectTrusted": harness.project_trusted,
            "mcp": self.mcp.status()["servers"],
            "extensionErrors": [
                {"source": str(error.path), "message": str(error.error)}
                for error in self.extension_errors
            ],
            "extensions": [
                {"name": extension.name, "source": getattr(extension, "resolved_path", None)}
                for extension in harness.extension_runner.extensions
            ],
            "hooks": [
                {
                    "extension": getattr(extension, "resolved_path", None) or extension.name,
                    "events": list(extension.handlers),
                }
                for extension in harness.extension_runner.extensions
            ],
        }

    async def set_setting(self, params: dict[str, Any]) -> dict[str, Any]:
        key = params.get("key")
        if self.settings is None or not isinstance(key, str) or key not in SETTING_FIELDS:
            raise ValueError("Unknown or unavailable settings key")
        field, kind, choices = SETTING_FIELDS[key]
        value = params.get("value")
        if type(value) is not kind or (choices is not None and value not in choices):
            raise ValueError(f"Invalid setting value for {key}")
        if isinstance(value, list) and (
            len(value) > 256 or any(not isinstance(v, str) or not v or len(v) > 4096 for v in value)
        ):
            raise ValueError("Setting must be a bounded list of non-empty strings")
        if isinstance(value, str) and (not value or len(value) > 4096 or "\x00" in value):
            raise ValueError("Setting must be a bounded, non-empty string")
        if key in RESOURCE_LIST_KEYS and isinstance(value, list):
            # #511 (ADR-0279 D4) — the lists the /skill panel edits go through the
            # same strict, cross-process edit: read on disk under the lock, and a
            # file that did not load or cannot be written RAISES instead of
            # being skipped behind a success.
            entries = [str(v) for v in value]
            try:
                await self.settings.edit_list(
                    "global", RESOURCE_LIST_KEYS[key], lambda _old: list(entries)
                )
            except Exception as exc:  # noqa: BLE001 — reported as the write's verdict
                raise ValueError(f"Setting {key!r} was not saved: {exc}") from exc
            return {"key": key, "value": self._resource_list(key), "requiresReload": True}
        # Errors already queued belong to earlier work; report them where the CLI
        # reports them and keep this write's verdict its own.
        for error in self.settings.drain_errors():
            print(f"Warning: settings ({error.scope}): {error.error}", file=sys.stderr)
        # #511 (C18): a global file that did not load is never written by a
        # setter (it is skipped, not recorded), so draining above left nothing to
        # report a skipped write by. Refuse it here instead of returning success.
        load_error = getattr(self.settings, "load_error", None)
        failed_load = load_error("global") if callable(load_error) else None
        if failed_load is not None:
            raise ValueError(
                f"Setting {key!r} was not saved: the global settings file did not load "
                f"({failed_load})"
            )
        getattr(self.settings, "set_" + field)(value)
        # Setters only enqueue the write; every native writer awaits it (#594).
        await self.settings.flush()
        failures = self.settings.drain_errors()
        if failures:
            reasons = "; ".join(f"{error.scope}: {error.error}" for error in failures)
            raise ValueError(f"Setting {key!r} was not saved: {reasons}")
        return {
            "key": key,
            "value": getattr(self.settings, "get_" + field)(),
            "requiresReload": True,
        }

    def _resource_list(self, key: str) -> list[str]:
        """The GLOBAL file's list for a resource key, read now and validated (#511, C18).

        ``settings/set`` writes the global scope, so this reads the same one
        (the merged getter showed a trusted project's list in its place). A
        value that is not a list of strings reads as ``[]`` rather than as a
        list of characters; an unreadable file raises.
        """

        entries, _problems = validate_entries(
            self.settings.read_list("global", RESOURCE_LIST_KEYS[key])
        )
        return entries

    async def auth_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.auth is None:
            raise ValueError("This RPC host has no authentication storage")
        provider = params.get("provider")
        if method == "auth/status":
            providers = {provider} if isinstance(provider, str) else set(self.auth.list())
            if self.model_registry is not None and provider is None:
                providers.update(model.provider for model in self.model_registry.get_all())
            statuses = []
            for name in sorted(providers):
                status = await self.auth.get_auth_status(name)
                statuses.append(
                    {"provider": name, "configured": status.configured, "source": status.source}
                )
            return {"providers": statuses}
        if not isinstance(provider, str) or not provider or len(provider) > 128:
            raise ValueError("Provider id is required")
        if method == "auth/set_api_key":
            key = params.get("key")
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 16384
                or any(c in key for c in "\r\n\x00")
            ):
                raise ValueError("A non-empty provider API key is required")
            await self.auth.set_api_key(provider, key)
        elif method == "auth/logout":
            await self.auth.logout(provider)
        elif method == "auth/login":
            if not self.ui.enabled:
                raise ValueError("OAuth requires the RPC dialog client")
            from aelix_ai.oauth.types import OAuthLoginCallbacks

            async def on_auth(info: Any) -> None:
                reply = await self.ui.request("oauth", url=info.url, instructions=info.instructions)
                if reply.get("cancelled"):
                    raise ValueError("Authentication cancelled")

            async def on_prompt(prompt: Any) -> str:
                value = await self.ui.input(prompt.message, prompt.placeholder)
                if value is None:
                    raise ValueError("Authentication cancelled")
                return value

            async def on_select(prompt: Any) -> str | None:
                reply = await self.ui.request(
                    "oauthSelect",
                    title=prompt.message,
                    options=[{"id": option.id, "label": option.label} for option in prompt.options],
                )
                value = reply.get("value")
                return value if value in [option.id for option in prompt.options] else None

            await self.auth.login(
                provider,
                OAuthLoginCallbacks(
                    on_auth=on_auth,
                    on_prompt=on_prompt,
                    on_select=on_select,
                    on_progress=lambda text: self.ui.notice("authProgress", message=text),
                ),
            )
        else:
            raise ValueError("Unsupported authentication action")
        return await self.auth_action("auth/status", {"provider": provider})

    def bind_progress(self, harness: Any) -> None:
        self.unbind_progress()
        self._bind_children(harness)
        runtime = getattr(harness, "runtime", None)
        bus = getattr(runtime, "event_bus", None)
        if bus is None:
            return
        fields = (
            "id",
            "profile",
            "state",
            "current_tool",
            "elapsed_ms",
            "tokens",
            "cost",
            "model",
            "provider",
        )
        for channel in ("subagent_start", "subagent_tool", "subagent_end"):

            def receive(value: Any, event: str = channel) -> None:
                data = {key: getattr(value, key, None) for key in fields}
                for key in ("tokens", "cost"):
                    usage = data[key]
                    if isinstance(usage, float) and not math.isfinite(usage):
                        data[key] = None
                self.emit(
                    {
                        "type": "aelix_subagent",
                        "event": event,
                        "data": data,
                    }
                )

            self.subscriptions.append(bus.on(channel, receive))

    def unbind_progress(self, *, children: bool = True) -> None:
        """End every event subscription; with ``children=False``, all but the children's (:meth:`retire`)."""

        for unsubscribe in self.subscriptions:
            unsubscribe()
        self.subscriptions.clear()
        if children:
            self.unbind_children()

    def unbind_children(self) -> None:
        """End the children's hub subscription (:meth:`_bind_children`); idempotent."""

        unsubscribe, self._child_subscription = self._child_subscription, None
        if unsubscribe is not None:
            unsubscribe()

    def retire(self) -> None:
        """The host is gone and the build disposed: end the children's subscription, retire the controls.

        ``run_rpc_mode``'s last step (#612 fix round 1, Codex C-M4). Until here the
        children's subscription outlived the other channels (``unbind_progress(children=False)``), so
        the terminal frames the disposal's ``session_shutdown`` publishes reach a
        host that half-closed its input and still reads.
        """

        self.unbind_children()
        controls = self.controls
        if controls is not None:
            with contextlib.suppress(Exception):
                controls.close()

    def _bind_children(self, harness: Any) -> None:
        """A harness binds (#612, contract r5 §3.4, §4.5): a new generation, its controls and its notices.

        The OLD generation's controls are retired here, at the rebind, never at a
        fence: ``close()`` refuses their interventions from now on, and the old
        hub's subscription has just ended (:meth:`unbind_progress`) — after the
        teardown it saw, whose frames (the terminal states of the children it
        stopped) reached the host. Not a fourth bus channel: the hub is
        subscribed through the seam, so ``aelix_subagent``'s three stay three.

        The notice inbox is subscribed when the host has a job service, and
        PRIMED: notices already pending (a startup restores them before this
        listener exists) raise one ``aelix_notice`` at once.
        """

        previous = self.controls
        self.generation += 1
        runtime = getattr(harness, "runtime", None)
        controls = getattr(getattr(runtime, "subagents", None), "controls", None)
        self.controls = controls
        if previous is not None and previous is not controls:
            with contextlib.suppress(Exception):
                previous.close()
        if controls is not None:
            self._child_subscription = controls.subscribe(self._emit_child)
        if self.jobs is not None and getattr(harness, "notices_enabled", False) is True:
            self.subscriptions.append(harness.add_notice_listener(self._on_notice))
            if getattr(harness, "pending_notices", 0):
                self._on_notice("queued")

    def _emit_child(self, event: str, data: dict[str, Any]) -> None:
        self.emit({"type": "aelix_child", "event": event, "data": data})

    def _on_notice(self, change: str) -> None:
        if change == "queued":
            self.emit({"type": "aelix_notice", "event": "queued", "data": {}})

    async def agent_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "agents/run":
            return await self.run_agent(params)
        if self.agents is None:
            raise ValueError("Agent profile service is unavailable")
        name = params.get("name")
        if method == "agents/list":
            # #795 (ADR-0304 D12) — the applied ``agents`` lists' answer per
            # profile, from the service's delegation view when it has one (a
            # host's own service may not: then every profile reads enabled).
            delegation = getattr(self.agents, "delegation", None)
            view = cast("DelegationView | None", delegation() if callable(delegation) else None)
            profiles = view.discovered if view is not None else self.agents.list()
            return {
                "profiles": [
                    {
                        "name": profile.name,
                        "description": profile.description,
                        "scope": profile.scope,
                        "filePath": profile.file_path,
                        "enabled": view.on(profile.name) if view is not None else True,
                        "decidedBy": (
                            view.states.get(profile.name, (True, None))[1]
                            if view is not None
                            else None
                        ),
                    }
                    for profile in profiles.profiles
                ],
                "diagnostics": [asdict(diagnostic) for diagnostic in profiles.diagnostics],
            }
        if not isinstance(name, str) or not name or len(name) > 256:
            raise ValueError("Agent profile name is required")
        if method == "agents/show":
            return {"profile": asdict(self.agents.show(name))}
        if method == "agents/use":
            message = await self.agents.use(
                None if name == "--none" else name, harness=self.harness
            )
            return {"message": message}
        raise ValueError("Unsupported agent profile action")

    async def run_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        from aelix_coding_agent.agents.sendable import unsendable_refusal
        from aelix_coding_agent.subagent_contract import ProjectScopeRefused

        if "allowProject" in params:
            raise ValueError(
                "allowProject is not accepted: a project agent profile needs native consent "
                "(--approve at launch, or the RPC confirmation dialog with ui/enable)"
            )
        name = params.get("name")
        if not isinstance(name, str) or not name or len(name) > 256:
            raise ValueError("Agent profile name is required")
        task = params.get("task")
        if not isinstance(task, str) or not task or len(task) > 100_000:
            raise ValueError("Agent task must be a non-empty bounded string")
        # #627: a NUL or half of a character is refused here, before the profile
        # is resolved — a project profile's confirmation dialog would otherwise
        # be answered for a task that cannot run.
        refusal = unsendable_refusal(task, where="The task", resend="the task")
        if refusal is not None:
            raise ValueError(refusal)
        stopped = getattr(self.runtime, "stopped", None)
        if isinstance(stopped, str):
            # #663 (verify V7) — the stopped harness unbound its delegation seam;
            # what is wrong is the stop, not the --agents setting.
            raise ValueError(stopped)
        runtime = self.harness.runtime.subagents
        if runtime is None:
            raise ValueError("Subagents are disabled; explicitly enable agent delegation")
        command_runtime = self.harness.runtime
        previous_ui = command_runtime.ui
        if self.ui.enabled:
            command_runtime.bind_ui(self.ui)
        try:
            try:
                resolved = runtime.resolve_profile(name)
            except ProjectScopeRefused:
                # Resolved only to show (and later run) exactly the file being
                # consented to; nothing spawns without the consent below.
                resolved = runtime.resolve_profile(name, allow_project=True)
                if not await self._consent_to_project_profile(resolved):
                    raise ValueError(
                        f"Project agent profile {name!r} was not run: it needs per-identity "
                        "consent (--approve at launch, or the RPC confirmation dialog with ui/enable)"
                    ) from None
            result = asdict(await runtime.spawn(resolved, task))
        finally:
            command_runtime.bind_ui(previous_ui)
        for key in ("details", "tool_trail"):
            text = result.get(key)
            if isinstance(text, str) and len(text) > 100_000:
                result[key] = text[:100_000]
                result[key + "Truncated"] = True
        return {"result": result}

    async def _consent_to_project_profile(self, resolved: Any) -> bool:
        """The per-identity consent ``/agents run`` takes (ADR-0197 §(f)).

        Launch approval (``--approve``, the profile service's ``confirm_project``)
        or, when the host enabled RPC dialogs, the same confirmation the TUI asks,
        naming the file that will run. Anything else declines.
        """

        confirm_project: Callable[..., Awaitable[bool]] | None = getattr(
            self.agents, "confirm_project", None
        )
        if callable(confirm_project) and await confirm_project(resolved.profile):
            return True
        if not self.ui.enabled:
            return False
        from aelix_coding_agent.cli.entry import (
            PROJECT_AGENT_CONFIRM_OPTIONS,
            project_agent_confirm_body,
        )

        answer = await self.ui.select(
            project_agent_confirm_body(resolved.name, resolved.source_path or "(path unavailable)"),
            list(PROJECT_AGENT_CONFIRM_OPTIONS),
        )
        return answer == PROJECT_AGENT_CONFIRM_OPTIONS[0]

    async def branch_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a branch file through the native fork/clone and come back (ADR-0275).

        The return is a real ``switch_session``: the original session's harness is
        rebuilt (``session_shutdown``/``session_start`` fire, as on ``/resume``) and
        the tool selection made before the branch is restored. A return that is
        vetoed or fails is an error that names the branch file and the session
        the runtime is actually on.
        """

        original = self.harness.session_file
        if not original or self.runtime.session is None:
            raise ValueError("No persisted session is available to branch")
        kind = params.get("kind")
        if kind not in {"fork", "clone"}:
            raise ValueError("Branch kind must be fork or clone")
        entry = params.get("entryId")
        if kind == "clone":
            entry = await self.runtime.session.get_leaf_id()
        if not isinstance(entry, str) or not entry:
            raise ValueError("A conversation entry is required to branch")
        selection = self.harness.state.active_tool_names
        selection = None if selection is None else list(selection)
        try:
            result = await self.runtime.fork(
                entry, **({"position": "at"} if kind == "clone" else {})
            )
        except BaseException:
            if self.harness.session_file != original:
                try:
                    await self.runtime.switch_session(original)
                except Exception as error:  # noqa: BLE001 — the fork's failure is the one reported
                    print(
                        f"[rpc] branch rollback to {original} failed: {error!r}",
                        file=sys.stderr,
                        flush=True,
                    )
            raise
        if result.cancelled:
            return {"cancelled": True}
        branch, title = self.harness.session_file, self.harness.session_name
        try:
            restored = await self.runtime.switch_session(original)
        except Exception as error:
            message = (
                f"Created branch {branch}, but returning to {original} failed "
                f"({error or type(error).__name__}); the runtime is now on {self.harness.session_file}"
            )
            if isinstance(error, SessionLockContention):
                raise BranchReturnContention(message, error) from error
            raise ValueError(message) from error
        if restored.cancelled:
            raise ValueError(
                f"Created branch {branch}, but an extension vetoed returning to {original}; "
                f"the runtime is now on {self.harness.session_file}"
            )
        await self._restore_selection(selection)
        return {"cancelled": False, "sessionFile": branch, "title": title, "restored": True}

    async def _restore_selection(self, selection: list[str] | None) -> None:
        # #510 — through the harness's commit: names gone from the registry
        # drop, and its policy clamps the rest.
        restore = getattr(self.harness, "restore_active_tools", None)
        if callable(restore):
            restore(selection)
            return
        live = [tool.name for tool in self.harness.state.tools]
        if selection is None:
            if self.harness.state.active_tool_names is not None:
                await self.harness.set_active_tools(live)
            return
        await self.harness.set_active_tools([name for name in selection if name in live])

    # === Method families (#624, ADR-0305 D10) ==================================

    def _families(
        self, request_id: object = None
    ) -> dict[str, Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]]:
        """A method's first segment -> its handler, called with ``(method, params)``.

        Routing only: what is admitted, and under which lock, is decided before
        this in :meth:`handle` by the method sets (``READ_ONLY_METHODS``,
        ``MUTATING_METHODS``, ``CANCELLABLE_METHODS``, ``TURN_SAFE_METHODS``, ...).
        Each handler refuses when its service is absent. A new family is one
        entry here (ADR-0302 D9), at no cost to :meth:`_dispatch` (ADR-0300).
        """

        return {
            "auth": self.auth_action,
            "agents": self.agent_action,
            "selections": self.selection_action,
            "children": self.children_action,
            "transcript": lambda method, params: self.transcript_action(method, params, request_id),
            "ui": self.ui_action,
        }

    async def ui_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """``ui/enable`` (ADR-0275), and since #612 its ``childApprovals`` (ADR-0308).

        ``{enabled: true, childApprovals: true}`` makes this host the one who answers
        a delegated child's approval request: the process's ``PermissionExtension``
        gets this plane's ``childApproval`` dialog as its ``child_approver``, so a
        child spawned from now on under a ``default`` parent runs ``default`` and
        asks (owner Q-A). Absent, ``false``, ``enabled`` not ``true``, or no
        delegation seam on the bound runtime: nothing is bound, and an earlier
        binding of this plane's is taken back with the requests it has open
        (each answered "no one can answer approvals here"). The result carries
        ``childApprovals`` only when the request did, so ``ui/enable`` without it
        answers as it did in v1.
        """

        if method != "ui/enable":
            raise ValueError(f"Unsupported runtime control method: {method}")
        self.ui.enabled = params.get("enabled") is True and not self.closing
        bound = self._bind_child_approvals(params.get("childApprovals") is True)
        result: dict[str, Any] = {"enabled": self.ui.enabled}
        if "childApprovals" in params:
            result["childApprovals"] = bound
        return result

    def _bind_child_approvals(self, wanted: bool) -> bool:
        """Bind (or take back) this plane's dialog as the child approver; whether it is bound."""

        permissions = self.permissions
        if permissions is None:
            return False
        bound = wanted and self.ui.enabled and self.controls is not None
        mine = self.ui.child_approval
        if bound:
            permissions.child_approver = mine
        elif getattr(permissions, "child_approver", None) == mine:
            permissions.child_approver = None
            self.ui.withdraw_child_approvals()  # its open requests too (fix round 1, P-L3)
        return bound

    async def children_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """``children/list``, ``children/events``, ``children/notices`` (#612, ADR-0307) and
        ``children/resume`` (#836, ADR-0318).

        The three ``CONCURRENT_METHODS`` never come here: :meth:`handle` admits
        them before the lock (:meth:`_concurrent`).
        """

        return await children_call(self, method, params)

    async def selection_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """``selections/list`` and ``selections/set`` (ADR-0305), when this host serves a kind."""

        if not self.selections:
            raise ValueError("This RPC host serves no selections")
        from aelix_coding_agent.rpc.control_selection import selection_call

        return await selection_call(self, method, params)

    def current_operation(self) -> _Operation | None:
        """The running mutation of the calling task, if it is one."""

        task = asyncio.current_task()
        return next((op for op in self.operations if op.active and op.task is task), None)


def control_error_message(error: BaseException) -> str:
    """A correlated error never carries an empty message (``TimeoutError()`` does)."""

    return str(error) or type(error).__name__
