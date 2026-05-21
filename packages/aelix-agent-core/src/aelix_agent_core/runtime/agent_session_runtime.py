"""Pi parity: ``packages/agent/src/core/agent-session-runtime.ts:67-374``.

Sprint 6h₄b (ADR-0077, P-302~P-310) — FOUNDATION-ONLY port. The class is
fully constructible and the rebind seam (``setRebindSession`` +
``finishSessionReplacement`` + the private ``apply`` / ``teardownCurrent``
helpers) is wired and unit-tested. The four public replace APIs
(``switch_session`` / ``new_session`` / ``fork`` / ``import_from_jsonl``)
are scaffolded but raise :class:`NotImplementedError` referencing
ADR-0078 (Sprint 6h₄c wires them when the 3 DEFERRED RPC handlers move).

Architectural decision (P-302): Aelix adopts **harness-rebuild** instead
of session-swap. Pi can swap ``_session`` directly because
``AgentSession`` is a stateless wrapper; Aelix ``AgentHarness`` captures
``_state.session_id`` at ``__init__`` (``harness/core.py:524``) and binds
runtime actions / merges tools / caches session_name during construction.
The harness factory pattern preserves all of these invariants.

Sprint 6h₄c (ADR-0079) — wiring sprint. The 4 public replace APIs from
6h₄b are filled with real bodies routed through ``JsonlSessionRepo.open``
/ ``JsonlSessionRepo.create`` / ``JsonlSessionRepo.fork`` (Aelix is
persisted-only — the Pi in-memory branch at ``:303-319`` is dropped).
``import_from_jsonl`` STAYS STUBBED — no RPC wire surface today.
Constructor extends with required keyword-only ``repo: JsonlSessionRepo``
+ ``fs: FileSystem``. The Sprint 6h₄b ``_apply_for_test`` test seam is
REMOVED — 6h₄b tests migrate to drive ``switch_session`` via the real
public API. P-329 deliberate convergence: Aelix handlers MUST NOT call
rebind manually — the runtime's ``_finish_session_replacement``
auto-invokes the registered callback as single source of truth (Pi
belt-and-braces handler-side rebind at ``rpc-mode.ts:565-567``/
``:573-575``/``:585-587`` is NOT mirrored).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aelix_agent_core.runtime._types import (
    AgentSessionRuntimeDiagnostic,
    HarnessFactory,
    RuntimeReplaceResult,
)
from aelix_agent_core.session.fs import FileSystem
from aelix_agent_core.session.jsonl_repo import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.jsonl_storage import load_jsonl_session_metadata
from aelix_agent_core.session.repo_utils import ForkOptions, ForkPosition

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.session.session import Session

_log = logging.getLogger(__name__)


def _extract_user_message_text(content: Any) -> str:
    """Pi parity: ``extractUserMessageText`` (``agent-session-runtime.ts:49-58``).

    Sprint 6h₄c (ADR-0079, P-325). Module-private mirror of Pi's inline
    helper — joins the ``text`` parts of a user message ``content`` value
    that may be either a plain string or a list of content parts. Pi
    narrows on ``part.type === "text" && typeof part.text === "string"``.
    """

    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if getattr(part, "type", None) == "text":
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


class AgentSessionRuntime:
    """Pi parity: ``AgentSessionRuntime`` (``agent-session-runtime.ts:67-374``).

    The runtime owns the LIVE :class:`AgentHarness` and exposes a rebind
    seam so callers (the ``rpc_mode`` event pipe) can refresh their
    captured ``harness`` reference after a session-replacement operation.

    Sprint 6h₄b ships the FOUNDATION:
      - constructor + getters,
      - ``set_rebind_session`` / ``set_before_session_invalidate``,
      - ``_apply`` / ``_teardown_current`` / ``_finish_session_replacement``
        (private; in 6h₄b tested through ``_apply_for_test`` — REMOVED in
        6h₄c per P-331),
      - ``dispose()`` (no-op-extra; defers to harness dispose),
      - stub ``_emit_before_switch`` / ``_emit_before_fork`` (return False).

    Sprint 6h₄c (ADR-0079) — wiring sprint. The 4 public replace APIs
    (``switch_session`` / ``new_session`` / ``fork`` /
    ``import_from_jsonl``) are filled with real bodies routed through
    :class:`JsonlSessionRepo` (Aelix is persisted-only — the Pi in-memory
    branch at ``:303-319`` is dropped). ``import_from_jsonl`` STAYS
    STUBBED — no RPC wire surface today.
    """

    def __init__(
        self,
        harness: AgentHarness,
        create_harness: HarnessFactory,
        *,
        repo: JsonlSessionRepo,
        fs: FileSystem,
        diagnostics: list[AgentSessionRuntimeDiagnostic] | None = None,
        model_fallback_message: str | None = None,
    ) -> None:
        """Pi parity: constructor signature mirrors
        ``agent-session-runtime.ts:67-74`` modulo the harness-rebuild
        adaptation (P-302). Pi positional args (in order):
        ``_session`` / ``_services`` / ``createRuntime`` /
        ``_diagnostics`` / ``_modelFallbackMessage``.

        Aelix maps:
          - ``_session``  → ``harness`` (P-302 — harness wraps Session)
          - ``_services`` → folded INTO harness (extension runtime / tools)
          - ``createRuntime`` → ``create_harness`` (factory: Session -> Harness)
          - ``_diagnostics`` → ``diagnostics``
          - ``_modelFallbackMessage`` → ``model_fallback_message``

        Sprint 6h₄c (ADR-0079, P-324) — required keyword-only ``repo`` +
        ``fs`` extension. The 4 replace bodies route through
        :class:`JsonlSessionRepo`; ``repo`` and ``fs`` are explicit and
        REQUIRED (no default) so accidental omission fails LOUD at
        construction rather than silently re-raising
        :class:`NotImplementedError` inside the replace bodies.
        """

        self._harness = harness
        self._create_harness = create_harness
        self._repo = repo
        self._fs = fs
        self._diagnostics: list[AgentSessionRuntimeDiagnostic] = (
            list(diagnostics) if diagnostics else []
        )
        self._model_fallback_message = model_fallback_message
        self._rebind_session: (
            Callable[[AgentHarness], Awaitable[None]] | None
        ) = None
        self._before_session_invalidate: Callable[[], None] | None = None

    # === Public getters (Pi `:79-97`) ===========================================

    @property
    def harness(self) -> AgentHarness:
        """Aelix-additive (P-304). The LIVE :class:`AgentHarness`. Callers
        MUST re-read after a ``setRebindSession``-triggered replacement.
        """
        return self._harness

    @property
    def session(self) -> Session | None:
        """Pi parity for ``runtimeHost.session`` (``:83-85``). Read-through
        to ``self._harness._session`` (P-304).
        """
        return self._harness._session

    @property
    def cwd(self) -> str | None:
        """Pi parity (``:87-89``). Reads through harness session metadata."""
        # Aelix `Session.get_metadata()` is async; expose the cached cwd
        # captured in the harness state if present, else None.
        session = self._harness._session
        if session is None:
            return None
        storage = session.get_storage()
        metadata = getattr(storage, "_metadata", None)
        return getattr(metadata, "cwd", None) if metadata is not None else None

    @property
    def diagnostics(self) -> list[AgentSessionRuntimeDiagnostic]:
        """Pi parity (``:91-93``)."""
        return list(self._diagnostics)

    @property
    def model_fallback_message(self) -> str | None:
        """Pi parity (``:95-97``)."""
        return self._model_fallback_message

    # === The seam (Pi `:99-113`) ================================================

    def set_rebind_session(
        self, cb: Callable[[AgentHarness], Awaitable[None]]
    ) -> None:
        """Pi parity: ``setRebindSession`` (``agent-session-runtime.ts:99-101``).

        Stores the callback invoked after every successful harness
        replacement (P-305). Pi signature: ``(session: AgentSession) =>
        Promise<void>``; Aelix passes the NEW harness instead (P-302).
        """
        self._rebind_session = cb

    def set_before_session_invalidate(
        self, cb: Callable[[], None]
    ) -> None:
        """Pi parity: ``setBeforeSessionInvalidate`` (``:111-113``).

        Pi signature is sync (``() => void``). Aelix mirrors.
        """
        self._before_session_invalidate = cb

    # === Private replace seam (Pi `:115-173`) ===================================

    async def _emit_before_switch(self) -> bool:
        """Pi parity: ``emitBeforeSwitch`` (``:115-130``). P-308 stub:
        Aelix has no ``session_before_switch`` hook event yet; returns
        ``False`` (never cancel). Real surface lands per ADR-0078.
        """
        return False

    async def _emit_before_fork(self) -> bool:
        """Pi parity: ``emitBeforeFork`` (``:132-147``). P-308 stub: see
        :meth:`_emit_before_switch`. Returns ``False``.
        """
        return False

    async def _teardown_current(self) -> None:
        """Pi parity: ``teardownCurrent`` (``:149-157``).

        Calls ``beforeSessionInvalidate?.()`` THEN disposes the current
        harness (Pi disposes ``_session``; Aelix disposes the harness
        wrapper — P-302). LIFO ordering preserved per Pi.
        """
        if self._before_session_invalidate is not None:
            try:
                self._before_session_invalidate()
            except Exception:
                _log.exception(
                    "AgentSessionRuntime.before_session_invalidate raised"
                )
        try:
            await self._harness.dispose()
        except Exception:
            _log.exception("AgentSessionRuntime.harness.dispose raised")

    async def _apply(self, new_session: Session) -> None:
        """Pi parity: ``apply`` (``:159-164``).

        Pi reassigns ``this._session = newSession``; Aelix uses the
        factory to construct a NEW harness bound to ``new_session``
        (P-302/P-306). The factory is awaited so async setup (e.g.
        ``await harness.bootstrap()``) is permitted.
        """
        new_harness = await self._create_harness(new_session)
        self._harness = new_harness

    async def _finish_session_replacement(
        self, new_session: Session
    ) -> None:
        """Pi parity: ``finishSessionReplacement`` (``:166-173``).

        Order:
          1. ``_teardown_current`` (dispose OLD harness),
          2. ``_apply`` (construct NEW harness from factory),
          3. ``rebind_session?.(new_harness)`` (P-305 fire-and-await).
        """
        await self._teardown_current()
        await self._apply(new_session)
        if self._rebind_session is not None:
            await self._rebind_session(self._harness)

    # === Public replace APIs (Pi `:175-364`) — Sprint 6h₄c real bodies ========

    async def switch_session(
        self,
        path: str,
        *,
        options: dict | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``switchSession`` (``agent-session-runtime.ts:175-198``).

        Sprint 6h₄c (ADR-0079, P-325) — real body. Pi 4-step waveform:
          1. ``emit_before_switch()`` → bail if cancelled (P-308 stub
             returns ``False`` in 6h₄c — real cancel hooks deferred to
             ADR-0080).
          2. Resolve target session via ``repo.open(load_jsonl_session_metadata(...))``.
          3. ``_finish_session_replacement(new_session)`` (LIFO: dispose
             OLD harness → ``_apply`` constructs NEW harness via factory
             → registered ``rebind_session`` callback fires).
          4. Return ``RuntimeReplaceResult(cancelled=False)``.

        Pi-divergence acknowledged: Pi's ``assertSessionCwdExists``
        (``:186``) is omitted — Aelix surfaces the equivalent error
        implicitly through :class:`SessionError("not_found")` /
        :class:`SessionError("storage")` when ``repo.open`` fails to find
        the file (``jsonl_repo.py:170-178``). Deferred per ADR-0080.
        """

        if await self._emit_before_switch():
            return RuntimeReplaceResult(cancelled=True)
        metadata = await load_jsonl_session_metadata(self._fs, path)
        new_session = await self._repo.open(metadata)
        await self._finish_session_replacement(new_session)
        return RuntimeReplaceResult(cancelled=False)

    async def new_session(
        self,
        *,
        parent_session: str | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``newSession`` (``agent-session-runtime.ts:200-232``).

        Sprint 6h₄c (ADR-0079, P-325 / P-330) — real body. Replaces the
        Sprint 6d stub at ``rpc_mode.py:309-347`` which rejected
        ``parent_session`` with an :class:`RpcErrorResponse`. Pi waveform:
          1. ``emit_before_switch()`` → bail if cancelled.
          2. ``repo.create(JsonlSessionCreateOptions(cwd=current_cwd,
             parent_session_path=parent_session))`` builds a fresh session
             under the current cwd, lineage-linked to ``parent_session``
             if supplied (Pi parity ``:213-215``).
          3. ``_finish_session_replacement(new_session)``.
          4. Return ``RuntimeReplaceResult(cancelled=False)``.

        Aelix omits Pi's optional ``setup`` 2-stage callback (Pi
        ``:226-229``) — carry-forward per ADR-0080 P-314.

        Aelix-additive simplification: Pi takes an options dict
        (``{parentSession?, setup?, withSession?}``). Aelix exposes ONLY
        ``parent_session`` as a keyword for 6h₄c. ``setup`` + ``withSession``
        defer per ADR-0080 (P-314).
        """

        if await self._emit_before_switch():
            return RuntimeReplaceResult(cancelled=True)
        cwd = self.cwd
        if cwd is None:
            raise RuntimeError(
                "new_session requires the current harness session to have a cwd"
            )
        new_session = await self._repo.create(
            JsonlSessionCreateOptions(
                cwd=cwd, parent_session_path=parent_session
            )
        )
        await self._finish_session_replacement(new_session)
        return RuntimeReplaceResult(cancelled=False)

    async def fork(
        self,
        entry_id: str,
        *,
        position: ForkPosition = "before",
    ) -> RuntimeReplaceResult:
        """Pi parity: ``fork`` (``agent-session-runtime.ts:234-320``).

        Sprint 6h₄c (ADR-0079, P-325) — real body. Pi has 3 branches
        (top + persisted + in-memory). Aelix is persisted-only — the
        in-memory branch (``:303-319``) is dropped (P-325 SYNTHESIS).
        The remaining waveform:
          1. ``emit_before_fork()`` → bail if cancelled.
          2. Resolve ``selected_entry`` via ``session.get_entry(entry_id)``;
             raise :class:`ValueError("Invalid entry ID for forking")` if
             missing (Pi parity ``:247``).
          3. Resolve ``target_leaf_id`` + optional ``selected_text``:
             - ``position=="at"`` → ``target_leaf_id = selected_entry.id``,
               ``selected_text = None``.
             - ``position=="before"`` → require ``selected_entry`` is a
               user message; ``target_leaf_id = selected_entry.parent_id``,
               ``selected_text = _extract_user_message_text(...)``.
          4. Resolve current session metadata for ``ForkOptions.cwd`` +
             ``parent_session_path``.
          5. ``new_session = await repo.fork(source_metadata,
             ForkOptions(cwd, entry_id=target_leaf_id, position="at",
             parent_session_path=current_session_path))``. ``position="at"``
             is correct because P-325 pre-computed the effective leaf via
             the Pi user-message walk above — passing it to ``ForkOptions``
             as ``"at"`` mirrors Pi's ``createBranchedSession(targetLeafId)``
             call at ``:285/:289/:307``.
          6. ``_finish_session_replacement(new_session)``.
          7. Return ``RuntimeReplaceResult(cancelled=False,
             selected_text=selected_text)``.
        """

        if await self._emit_before_fork():
            return RuntimeReplaceResult(cancelled=True)

        if self.session is None:
            raise RuntimeError("fork requires an active session")

        selected_entry = await self.session.get_entry(entry_id)
        if selected_entry is None:
            raise ValueError("Invalid entry ID for forking")

        selected_text: str | None = None
        if position == "at":
            target_leaf_id: str | None = selected_entry.id
        else:
            # position == "before"
            if (
                selected_entry.type != "message"
                or selected_entry.message.role != "user"  # type: ignore[union-attr]
            ):
                raise ValueError("Invalid entry ID for forking")
            target_leaf_id = selected_entry.parent_id
            selected_text = _extract_user_message_text(
                selected_entry.message.content  # type: ignore[union-attr]
            )

        metadata = await self.session.get_metadata()
        new_session = await self._repo.fork(
            source=metadata,
            options=ForkOptions(
                cwd=metadata.cwd,
                entry_id=target_leaf_id,
                position="at",
                parent_session_path=metadata.path,
            ),
        )
        await self._finish_session_replacement(new_session)
        return RuntimeReplaceResult(
            cancelled=False, selected_text=selected_text
        )

    async def import_from_jsonl(
        self,
        path: str,
        *,
        cwd: str | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``importFromJsonl`` (``:329-364``).

        Sprint 6h₄c — STAYS STUBBED. No RPC command in the Pi
        ``RpcCommand`` union (``rpc_types.py:309-340``) maps to this
        method as of SHA ``734e08e``; defer real body until a wire
        surface lands per ADR-0080. The Pi call site is the TUI
        ``/import`` command which doesn't go through RPC.
        """
        raise NotImplementedError(
            "AgentSessionRuntime.import_from_jsonl — no RPC wire surface "
            "(ADR-0080 carry-forward; deferred to Sprint 6h₅+)"
        )

    # === Dispose (Pi `:366-373`) ===============================================

    async def dispose(self) -> None:
        """Pi parity: ``dispose`` (``agent-session-runtime.ts:366-373``).

        Pi: ``beforeSessionInvalidate?.() → emit("session_shutdown") →
        await _session.dispose()``. Aelix 6h₄b: ``beforeSessionInvalidate?.()
        → await harness.dispose()``. The ``session_shutdown`` emit gap is
        recorded in ADR-0078 carry-forward (P-307).
        """
        if self._before_session_invalidate is not None:
            try:
                self._before_session_invalidate()
            except Exception:
                _log.exception(
                    "AgentSessionRuntime.before_session_invalidate raised"
                )
        await self._harness.dispose()


__all__ = ["AgentSessionRuntime"]
