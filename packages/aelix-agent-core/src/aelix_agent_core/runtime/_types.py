"""Sprint 6h₄b/6h₅b types — :class:`HarnessFactory`,
:class:`RuntimeReplaceResult`, :class:`AgentSessionRuntimeDiagnostic`
(ADR-0077), :class:`ReplacedSessionContext`,
:class:`SessionImportFileNotFoundError`, and :data:`PI_STALENESS_MESSAGE`
(ADR-0083 — Sprint 6h₅b).

Pi parity: ``packages/agent/src/core/agent-session-runtime.ts:67-374``.
The Pi return shape for ``switchSession`` / ``newSession`` / ``fork`` /
``importFromJsonl`` is ``{cancelled: boolean, selectedText?: string}``;
the Aelix dataclass mirrors verbatim with snake_case Python fields
serializing to camelCase keys at the wire layer.

Sprint 6h₅b (Phase 4.15, ADR-0083) — adds:

- :class:`ReplacedSessionContext` — Pi parity Protocol mirror of
  ``extensions/types.ts:366-381``. Placed here (runtime sub-package) to
  avoid the cross-package circular import that would arise if it lived in
  ``aelix_coding_agent.extensions`` AND to bypass
  :class:`ExtensionContext.__getattribute__`'s staleness guard. Pi
  structural typing is preserved via :class:`typing.Protocol` —
  :class:`types.SimpleNamespace` instances returned by
  :meth:`AgentHarness.create_replaced_session_context` (P-357) conform
  structurally without needing to subclass.
- :class:`SessionImportFileNotFoundError` — raised by
  :meth:`AgentSessionRuntime.import_from_jsonl` (P-360) when the
  caller-supplied path does not exist.
- :data:`PI_STALENESS_MESSAGE` — Pi verbatim string from
  ``runner.ts:467``. Single source of truth shared by
  :meth:`ExtensionRunner.invalidate` (P-362) and
  :meth:`_ExtensionRuntime.invalidate` (default argument alignment) so
  both packages reference the same constant.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness


@dataclass(frozen=True)
class ReloadSeed:
    """State carried from a live runtime into its :data:`HarnessFactory`
    rebuild on :meth:`AgentSessionRuntime.reload` (Issue #24-FU / ADR-0177).

    pi parity: the ``flagValues`` object pi's ``reload`` threads into
    ``_buildRuntime`` (``agent-session.ts``), which seeds
    ``extensionsResult.runtime.flagValues`` BEFORE constructing the
    ``ExtensionRunner`` — so a re-run extension ``setup()`` reads the user's
    restored flag value instead of the ``register_flag`` DEFAULT.

    Only :attr:`flag_values` is carried today. The active-tool round-trip is
    handled at the runtime level (post-``_apply`` ``set_active_tools`` in
    :meth:`AgentSessionRuntime.reload`), NOT here, because the intersect that
    drops names a removed extension no longer provides needs the fully-merged
    tool registry — which only exists after the rebuild.
    """

    flag_values: Mapping[str, bool | str] | None = None


HarnessFactory = Callable[..., Awaitable["AgentHarness"]]
"""Aelix-additive: factory called by :class:`AgentSessionRuntime` to build
a NEW :class:`AgentHarness` bound to ``new_session`` (P-302/P-306).
Async so callers can ``await harness.bootstrap()`` inside the factory.

Pi parity rationale (P-302 — BINDING):
Pi reassigns ``this._session`` in-place at
``agent-session-runtime.ts:166-173``. Aelix CANNOT mirror that directly
because :class:`AgentHarness` captures ``_state.session_id`` at
``__init__`` (``harness/core.py:524``) and binds runtime actions / merges
tools / caches session_name during construction. The factory pattern
preserves all of these invariants by reconstructing the harness for each
new :class:`Session`.

Invocation shapes (Issue #24-FU): the factory is called ``factory(session)``
on the /new//fork//resume/switch replace paths and
``factory(session, reload_seed=ReloadSeed(...))`` on the reload path
(:meth:`AgentSessionRuntime._apply` passes the keyword ONLY when a seed is
present). The type is the permissive ``Callable[..., Awaitable[AgentHarness]]``
rather than a keyword-only :class:`typing.Protocol` on purpose: every existing
factory — production (``cli/entry.py``, ``rpc_ws``, the rpc noop) AND the ~40
test factories — takes a single positional ``session``, and a keyword-only
Protocol would make all of them structurally unassignable under strict pyright.
A factory opts into the seed by declaring ``*, reload_seed: ReloadSeed | None =
None``; one that does not is never handed the keyword at runtime.
"""


# === Sprint 6h₅b (Phase 4.15, ADR-0083) — Pi staleness constant ============

PI_STALENESS_MESSAGE = (
    "This extension ctx is stale after session replacement or reload. "
    "Do not use a captured pi or command ctx after ctx.newSession(), "
    "ctx.fork(), ctx.switchSession(), or ctx.reload(). For newSession, "
    "fork, and switchSession, move post-replacement work into withSession "
    "and use the ctx passed to withSession. For reload, do not use the "
    "old ctx after await ctx.reload()."
)
"""Pi verbatim staleness message — ``runner.ts:467``.

Sprint 6h₅b (ADR-0083, P-362) — single source of truth shared by
:meth:`ExtensionRunner.invalidate` (delegating method) and
:meth:`_ExtensionRuntime.invalidate` (default argument alignment) so a
caller that bypasses the runner sees the SAME message a caller routing
through the runner sees.
"""


# === Sprint 6h₅b (Phase 4.15, ADR-0083) — ReplacedSessionContext ===========


@runtime_checkable
class ReplacedSessionContext(Protocol):
    """Pi parity Protocol mirror — ``extensions/types.ts:366-381``.

    Sprint 6h₅b (ADR-0083, P-356) — BINDING. The Pi handle passed to the
    optional ``withSession`` callback after a session replacement. Aelix
    models it as a :class:`typing.Protocol` (structural typing) rather
    than a concrete subclass because:

    1. The :class:`AgentHarness` factory in :meth:`create_replaced_session_context`
       (P-357) returns a :class:`types.SimpleNamespace` (Pi
       ``Object.defineProperties`` clone idiom). ``SimpleNamespace``
       cannot subclass a Protocol — structural conformance via
       :data:`typing.runtime_checkable` is the only path.
    2. Placing the type in :mod:`aelix_agent_core.runtime._types` avoids
       a cross-package import cycle (a concrete class living in
       :mod:`aelix_coding_agent.extensions` would force
       :mod:`aelix_agent_core.harness.core` to import upward).
    3. The Pi handle deliberately bypasses
       :meth:`ExtensionContext.__getattribute__`'s staleness pre-check
       so post-replacement work runs against the NEW harness without
       tripping the OLD harness's stale flag.

    The Protocol surface mirrors :class:`ExtensionContext` plus the 6
    ``ExtensionCommandContext`` command methods (Pi
    ``extensions/types.ts:371`` ``ReplacedSessionContext extends
    ExtensionCommandContext`` — Pi ``:333-364``). Sprint 6h₅b W6 (P-364
    W5 MAJOR fix) widens the Protocol to include those 6 methods:
    ``wait_for_idle`` / ``new_session`` / ``fork`` / ``navigate_tree`` /
    ``switch_session`` / ``reload``. The factory
    :meth:`AgentHarness.create_replaced_session_context` wires them via
    the optional ``runtime`` kwarg threaded from
    :meth:`AgentSessionRuntime._finish_session_replacement`.
    """

    cwd: str
    model: Any
    session_manager: Any
    signal: Any
    has_ui: bool

    def is_idle(self) -> bool: ...
    def abort(self) -> None: ...
    def get_active_tools(self) -> list[str]: ...
    def get_system_prompt(self) -> str: ...
    def has_pending_messages(self) -> bool: ...
    def shutdown(self) -> None: ...
    def get_context_usage(self) -> Any | None: ...
    def compact(self, **kwargs: Any) -> None: ...

    async def send_message(
        self,
        message: Mapping[str, Any] | Any,
        options: Mapping[str, Any] | None = None,
    ) -> None: ...

    async def send_user_message(
        self,
        content: str | list[Any],
        options: Mapping[str, Any] | None = None,
    ) -> None: ...

    # === Sprint 6h₅b W6 — ExtensionCommandContext extension (P-364) ============
    # Pi ``extensions/types.ts:333-364`` — Sprint 6h₅b W5 MAJOR audit found
    # these 6 command methods missing from the W2 Protocol shape. Pi
    # ``ReplacedSessionContext extends ExtensionCommandContext`` so the 6
    # methods MUST appear on the Pi handle. ``Any`` payloads (rather than
    # the precise Pi-typed ``RuntimeReplaceResult`` shape) preserve duck
    # typing of the factory ``SimpleNamespace`` wiring without dragging the
    # full Pi types graph in.

    async def wait_for_idle(self) -> None: ...
    async def new_session(
        self,
        *,
        parent_session: str | None = None,
        setup: Any | None = None,
        with_session: Any | None = None,
    ) -> Any: ...
    async def fork(
        self,
        entry_id: str,
        *,
        position: str = "before",
        with_session: Any | None = None,
    ) -> Any: ...
    async def navigate_tree(
        self,
        target_id: str | None,
        options: Any | None = None,
    ) -> Any: ...
    async def switch_session(
        self,
        path: str,
        *,
        options: Any | None = None,
        with_session: Any | None = None,
    ) -> Any: ...
    async def reload(self) -> None: ...


# === Sprint 6h₅b (Phase 4.15, ADR-0083) — import error =====================


class SessionImportFileNotFoundError(Exception):
    """Pi parity: ``agent-session-runtime.ts:39-47`` ``SessionImportFileNotFoundError``.

    Sprint 6h₅b (ADR-0083, P-360 + W6 P-366 W5 MAJOR fix). Raised by
    :meth:`AgentSessionRuntime.import_from_jsonl` when the caller-supplied
    path cannot be resolved.

    Sprint 6h₅b W6 (P-366 W5 MAJOR fix) — message and attribute aligned
    verbatim with Pi:

    .. code-block:: typescript

       class SessionImportFileNotFoundError extends Error {
           readonly filePath: string;
           constructor(filePath: string) {
               super(`File not found: ${filePath}`);
               this.name = "SessionImportFileNotFoundError";
               this.filePath = filePath;
           }
       }

    The :attr:`file_path` attribute carries the resolved path string
    (Pi ``filePath``; Aelix snake_case rename). The message is the
    verbatim Pi ``File not found: ${filePath}`` template so extensions /
    RPC error layers comparing the rendered string round-trip cleanly.
    """

    def __init__(self, file_path: str) -> None:
        super().__init__(f"File not found: {file_path}")
        self.file_path = file_path


@dataclass(frozen=True)
class RuntimeReplaceResult:
    """Pi parity: shape of the value returned by ``switchSession`` /
    ``newSession`` / ``fork`` / ``importFromJsonl`` (Pi
    ``agent-session-runtime.ts:175-320`` return signatures).

    Wire-shape preserves Pi camelCase keys when serialized:
    ``{"cancelled": bool, "selectedText"?: str}``.
    """

    cancelled: bool
    selected_text: str | None = None


@dataclass(frozen=True)
class AgentSessionRuntimeDiagnostic:
    """Pi parity: ``AgentSessionRuntimeDiagnostic`` (Pi
    ``agent-session-runtime.ts`` diagnostics array element type).

    Minimal frozen wrapper carrying a code + human-readable message.
    Extended in Sprint 6h₄c+ as real diagnostics emerge from the four
    replace APIs.
    """

    code: str
    message: str


__all__ = [
    "PI_STALENESS_MESSAGE",
    "AgentSessionRuntimeDiagnostic",
    "HarnessFactory",
    "ReloadSeed",
    "ReplacedSessionContext",
    "RuntimeReplaceResult",
    "SessionImportFileNotFoundError",
]
