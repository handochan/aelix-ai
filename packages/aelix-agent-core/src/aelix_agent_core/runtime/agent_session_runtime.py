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
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from aelix_agent_core.runtime._types import (
    AgentSessionRuntimeDiagnostic,
    HarnessFactory,
    RuntimeReplaceResult,
)

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.session.session import Session

_log = logging.getLogger(__name__)


class AgentSessionRuntime:
    """Pi parity: ``AgentSessionRuntime`` (``agent-session-runtime.ts:67-374``).

    The runtime owns the LIVE :class:`AgentHarness` and exposes a rebind
    seam so callers (the ``rpc_mode`` event pipe) can refresh their
    captured ``harness`` reference after a session-replacement operation.

    Sprint 6h₄b ships the FOUNDATION:
      - constructor + getters,
      - ``set_rebind_session`` / ``set_before_session_invalidate``,
      - ``_apply`` / ``_teardown_current`` / ``_finish_session_replacement``
        (private; tested through the ``_apply_for_test`` test seam),
      - ``dispose()`` (no-op-extra; defers to harness dispose),
      - stub ``_emit_before_switch`` / ``_emit_before_fork`` (return False).

    The four public replace APIs (``switch_session`` / ``new_session`` /
    ``fork`` / ``import_from_jsonl``) raise :class:`NotImplementedError`
    referencing ADR-0078 — Sprint 6h₄c implements them.
    """

    def __init__(
        self,
        harness: AgentHarness,
        create_harness: HarnessFactory,
        *,
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
        """

        self._harness = harness
        self._create_harness = create_harness
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

    # === Test seam (Aelix-additive; closure-pin entry point) ===================

    async def _apply_for_test(self, new_session: Session) -> None:
        """Aelix-additive test seam. Drives the full replace path
        WITHOUT requiring any of the 4 still-stubbed public APIs.

        Used by the closure pin and ``test_rebind_session_closure``. NOT
        part of the Pi surface; explicit ``_for_test`` suffix +
        underscore prefix discourage accidental call from production
        code paths. Sprint 6h₄c may remove this helper once
        ``switch_session`` lands.
        """
        await self._finish_session_replacement(new_session)

    # === Public replace APIs (Pi `:175-364`) — STUBBED for 6h₄b ===============

    async def switch_session(
        self,
        path: str,
        *,
        options: dict | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``switchSession`` (``agent-session-runtime.ts:175-198``).

        Sprint 6h₄b stub — raises :class:`NotImplementedError`. Sprint
        6h₄c (ADR-0078) implements + wires the matching RPC handler.
        """
        raise NotImplementedError(
            "AgentSessionRuntime.switch_session — Sprint 6h₄c (ADR-0078)"
        )

    async def new_session(
        self, *, options: dict | None = None
    ) -> RuntimeReplaceResult:
        """Pi parity: ``newSession`` (``:200-232``). Stub — see
        :meth:`switch_session`."""
        raise NotImplementedError(
            "AgentSessionRuntime.new_session — Sprint 6h₄c (ADR-0078)"
        )

    async def fork(
        self,
        entry_id: str,
        *,
        options: dict | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``fork`` (``:234-320``). Stub — see
        :meth:`switch_session`."""
        raise NotImplementedError(
            "AgentSessionRuntime.fork — Sprint 6h₄c (ADR-0078)"
        )

    async def import_from_jsonl(
        self,
        path: str,
        *,
        cwd: str | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``importFromJsonl`` (``:329-364``). Stub — see
        :meth:`switch_session`."""
        raise NotImplementedError(
            "AgentSessionRuntime.import_from_jsonl — Sprint 6h₄c (ADR-0078)"
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
