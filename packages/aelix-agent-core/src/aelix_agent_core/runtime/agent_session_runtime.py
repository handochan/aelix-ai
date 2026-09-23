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
``_state.session_id`` at ``__init__`` (``harness/core.py:635``) and binds
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

Sprint 6h₅a (ADR-0081, P-307/P-308/P-337 closure) — extension event Pi
parity. The 4 new Pi events (``session_start`` / ``session_before_switch``
/ ``session_before_fork`` / ``session_shutdown``) are wired end-to-end:

  - ``_emit_before_switch`` / ``_emit_before_fork`` (P-338/P-339) — real
    bodies replace the Sprint 6h₄b no-arg stubs; signatures mirror Pi
    ``agent-session-runtime.ts:115-130`` / ``:132-147``. W4 MINOR-3:
    parameters are required (no defaults) so every callsite supplies
    the Pi-shape (reason / entry_id) explicitly.
  - ``_teardown_current`` (P-340) — ORDERING CORRECTION to Pi order
    ``emit_shutdown → before_session_invalidate → dispose`` (Sprint
    6h₄b shipped the reversed order). Extension runner reference is
    captured BEFORE dispose to avoid the bus-teardown race.
  - ``dispose`` (P-341) — adds missing ``session_shutdown`` emit with
    ``reason="quit"``. W5 P-355 BLOCKING FIX: order corrected to
    EMIT → INVALIDATE → DISPOSE (matches ``_teardown_current``; the
    W2 "intentional asymmetry" §J rationale was based on a spec misread
    of Pi ``:366-373`` — Pi has no asymmetry).
  - ``switch_session`` assert-before-emit ordering (W4 MEDIUM):
    ``repo.open`` + ``assert_session_cwd_exists`` run BEFORE
    ``_emit_before_switch`` (Pi ``:186`` line-189 ordering — Pi
    asserts cwd before letting extensions cancel the swap so the
    error surfaces even when an extension would have cancelled).
  - ``previous_session_file`` snapshot (P-342) — captured BEFORE
    ``_teardown_current`` at all 3 replace sites and threaded into
    ``_finish_session_replacement`` for the ``session_start`` payload.
  - ``session_start`` emit (P-343) — fired from
    ``_finish_session_replacement`` AFTER ``rebind_session`` on the
    NEW harness's runner (the OLD bus is disposed by step 1).
  - ``assert_session_cwd_exists`` (P-337) — wired in ``switch_session``
    AFTER ``repo.open`` so the assertion checks the NEW session's cwd.
    Pi factory site (``:391``) + ``importFromJsonl`` site (``:352``)
    are deferred to Sprint 6h₅c.

Pi event line citations (W5 P-344 corrections — verified at SHA
``734e08e``): ``SessionStartEvent`` ``extensions/types.ts:513-519``,
``SessionBeforeSwitchEvent`` ``:522-526``, ``SessionBeforeForkEvent``
``:529-533``, ``SessionShutdownEvent`` ``:552-557``.
``SessionBeforeForkResult`` (P-345) ``:1015-1022``
(``cancel?, skipConversationRestore?``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

from aelix_agent_core.harness.hooks import SessionStartHookEvent
from aelix_agent_core.runtime._types import (
    PI_STALENESS_MESSAGE,
    AgentSessionRuntimeDiagnostic,
    HarnessFactory,
    ReloadSeed,
    ReplacedSessionContext,
    RuntimeReplaceResult,
    SessionImportFileNotFoundError,
)
from aelix_agent_core.session.fs import FileSystem
from aelix_agent_core.session.jsonl_repo import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.jsonl_storage import load_jsonl_session_metadata
from aelix_agent_core.session.read_only import ReadOnlySessionStorage
from aelix_agent_core.session.repo_utils import (
    ForkEntryId,
    ForkOptions,
    ForkPosition,
    fork_at_leaf,
)
from aelix_agent_core.session.session_cwd import assert_session_cwd_exists
from aelix_agent_core.session.session_lock import (
    SessionWriterLock,
    resolve_session_path,
)
from aelix_agent_core.session.storage import JsonlSessionMetadata, SessionError

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.session.session import Session

#: What a surface answers when the file a swap targets is already owned by
#: another live process (#137, ADR-0244 D1). The kernel implements all three;
#: which of them a given surface OFFERS is the surface's call — the in-session
#: TUI prompt offers ``fork`` and ``cancel``, because turning a live writable
#: REPL into read-only chrome mid-session is a different change.
SessionContendedChoice = Literal["fork", "read_only", "cancel"]

_log = logging.getLogger(__name__)


async def _emit_session_shutdown_event(
    extension_runner: Any,
    reason: Literal["quit", "reload", "new", "resume", "fork"],
    target_session_file: str | None = None,
) -> bool:
    """Pi parity: ``emitSessionShutdownEvent`` (``runner.ts:177-189``).

    Sprint 6h₅a (Phase 4.14, ADR-0081, P-334). Module-private helper
    mirroring Pi's top-level export. Gates on
    ``has_handlers("session_shutdown")`` to avoid constructing the event
    payload when no extension cares. The ``extension_runner`` parameter
    is typed as :class:`Any` to avoid importing
    :class:`~aelix_agent_core.harness._extension_runner.ExtensionRunner`
    (circular import via ``harness.core``); callers pass the harness's
    ``extension_runner`` attribute.

    Returns ``True`` when the event was emitted, ``False`` when skipped
    (no handlers registered).
    """

    from aelix_agent_core.harness.hooks import SessionShutdownHookEvent

    if not extension_runner.has_handlers("session_shutdown"):
        return False
    await extension_runner.emit(
        SessionShutdownHookEvent(
            type="session_shutdown",
            reason=reason,
            target_session_file=target_session_file,
        )
    )
    return True


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
            Callable[[AgentHarness, str], Awaitable[None]] | None
        ) = None
        self._before_session_invalidate: Callable[[], None] | None = None
        # #137 / ADR-0244. Injected, never constructed here — the CLI takes
        # the startup lock before any runtime exists (it has to: a session
        # that is already owned may never get a harness at all) and hands it
        # over. ``None`` therefore means "this embedder does not participate
        # in session ownership", which is what tests and library callers want.
        self._writer_lock: SessionWriterLock | None = None
        self._manages_writer_lock = False
        self._on_session_contended: (
            Callable[[str], Awaitable[SessionContendedChoice]] | None
        ) = None

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
        to :attr:`AgentHarness.session` (P-304).

        Sprint 6h₅d §E (P-384 / MINOR-3): migrated from the prior
        private-attribute reach on the harness's internal session slot to
        :attr:`AgentHarness.session`. The property body is unchanged —
        the public accessor returns the same underlying value — but the
        indirection keeps the runtime layer free of private reaches.
        """
        return self._harness.session

    @property
    def cwd(self) -> str | None:
        """Pi parity (``:87-89``). Reads through harness session metadata."""
        # Aelix `Session.get_metadata()` is async; expose the cached cwd
        # captured in the harness state if present, else None.
        session = self._harness.session
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
        self, cb: Callable[[AgentHarness, str], Awaitable[None]]
    ) -> None:
        """Pi parity: ``setRebindSession`` (``agent-session-runtime.ts:99-101``).

        Stores the callback invoked after every successful harness
        replacement (P-305). Pi signature: ``(session: AgentSession) =>
        Promise<void>``; Aelix passes the NEW harness PLUS the replace
        ``reason`` (issue #24 — ``"new"|"resume"|"fork"|"reload"``) so a
        surface can branch on it (e.g. the TUI preserves its visible
        transcript + ``/stats`` lifetime on ``"reload"`` but resets them on a
        session swap).
        """
        self._rebind_session = cb

    def set_before_session_invalidate(
        self, cb: Callable[[], None]
    ) -> None:
        """Pi parity: ``setBeforeSessionInvalidate`` (``:111-113``).

        Pi signature is sync (``() => void``). Aelix mirrors.
        """
        self._before_session_invalidate = cb

    # === Session ownership (#137, ADR-0244) — Aelix-additive ====================
    #
    # Two setters rather than ``__init__`` keywords, for the reason
    # ``set_rebind_session`` above is one: the CLI does not construct this
    # class. It calls ``create_agent_session_runtime``, whose parameter list is
    # fixed and shared with every other caller. A new required kwarg there
    # would have to be threaded through a factory that has no business knowing
    # about UI decisions, and an optional one would be silently dropped by any
    # caller that forgets it — which is exactly how an injected callback ends
    # up permanently ``None`` and every ``/resume`` refuses instead of asking.

    def set_writer_lock(self, lock: SessionWriterLock | None) -> None:
        """Adopt the lock the CLI took on the startup session.

        The runtime becomes responsible for moving it on every session swap
        and for releasing it in :meth:`dispose`. Calling this AT ALL — even
        with ``None``, which is what a read-only viewer passes — is what
        opts this runtime into enforcing ownership; a runtime nobody calls it
        on (an embedder, a test, ``--no-session``) neither takes locks nor
        leaves sidecars behind.
        """
        self._writer_lock = lock
        self._manages_writer_lock = True

    def set_on_session_contended(
        self, cb: Callable[[str], Awaitable[SessionContendedChoice]] | None
    ) -> None:
        """Install the "this file is owned — what now?" question (D1).

        The callback receives the contended session path and answers
        ``"fork"``, ``"read_only"`` or ``"cancel"``. Left unset, an in-session
        swap onto an owned file raises ``SessionError("storage", …)``, which
        is the right default for every non-interactive surface: a process that
        cannot ask must not choose data loss on the user's behalf. That is
        also what keeps RPC honest — ``rpc_mode`` never installs this, so
        ``_handle_switch_session`` turns the refusal into a structured error
        on the wire instead of painting a full-screen selector into its own
        JSONL response stream.
        """
        self._on_session_contended = cb

    @property
    def writer_lock(self) -> SessionWriterLock | None:
        """The lock this runtime currently holds, if any."""
        return self._writer_lock

    def _acquire_writer_lock(self, path: str) -> SessionWriterLock | None:
        """Take the writer lock for ``path``; ``None`` when someone else owns it.

        Only ever called when :attr:`_manages_writer_lock`, so ``None`` has
        exactly one meaning here: **another live process owns the file**. A
        sidecar we cannot use at all raises instead — see
        :attr:`SessionWriterLock.error` — because telling the user their
        session is open in another terminal when the truth is a root-owned
        lock file sends them looking for a terminal that does not exist.

        Returns the CURRENT lock unchanged when ``path`` is the file we
        already hold: re-acquiring our own file is not contention, and
        releasing first in order to re-take it would open a window for a
        third terminal to slip in. Compared on the RESOLVED path, so a second
        spelling of the file we already own (a symlink, ``..``) is recognised
        as ours rather than deadlocking against our own descriptor.
        """

        current = self._writer_lock
        if current is not None and current.resolved_path == resolve_session_path(path):
            return current
        candidate = SessionWriterLock(path)
        if not candidate.try_acquire():
            if candidate.error is not None:
                raise SessionError(
                    "storage",
                    f"cannot take the writer lock for {path}: {candidate.error}",
                    cause=candidate.error,
                )
            return None
        return candidate

    def _writer_lock_for_new_file(self, session: Session) -> SessionWriterLock | None:
        """The lock for a session file this process has just created.

        Brand-new files cannot be contended, so this is a move of ownership
        rather than a question. ``None`` means "no ownership to move" and
        nothing else: this runtime does not manage locks, or the new session
        has no file (in-memory storage).

        A brand-new file that IS owned is not the D1 question — it is another
        terminal's ``--continue`` having picked up the newest file by mtime
        microseconds after we published it. Review found that case folded into
        the same ``None`` as the two above, so the caller released the old
        lock, installed nothing, and carried on writing: fail-open, silent,
        into a file someone else owns. It raises now. Nothing has been torn
        down at this point, so the caller keeps the session it already owns.
        """

        if not self._manages_writer_lock:
            return None
        path = session.session_file
        if path is None:
            return None
        lock = self._acquire_writer_lock(path)
        if lock is None:
            raise SessionError(
                "storage",
                "the session file this process just created is already open "
                f"in another terminal: {path}",
            )
        return lock

    def _release_unadopted(self, lock: SessionWriterLock | None) -> None:
        """Drop a lock a failed swap took but never adopted.

        :meth:`_finish_session_replacement` installs the target lock the
        instant the new harness goes live, so reaching here with it still
        un-adopted means the swap died *before* that point and this process is
        still on the old session holding the old lock. Releasing is then the
        only way the kernel lock ever goes away — the object is a local, so
        after the frame unwinds nothing is left that can release it, and the
        file stays unopenable by every terminal including this one until the
        process exits. (``filelock``'s ``__del__`` is not a guarantee: the
        lock stays reachable from the traceback for as long as the exception
        is being handled.)
        """

        if lock is None or lock is self._writer_lock:
            return
        lock.release()

    def _install_writer_lock(self, lock: SessionWriterLock | None) -> None:
        """Adopt ``lock`` and let go of the previous one.

        Called only after a replacement has actually succeeded, so a refused
        swap keeps both the old session AND its lock — a live harness writing
        to a file it no longer owns is the defect this whole lane exists to
        remove.
        """

        previous = self._writer_lock
        if previous is not None and previous is not lock:
            previous.release()
        self._writer_lock = lock

    async def _resolve_contended_target(
        self,
        path: str,
        metadata: JsonlSessionMetadata,
        opened: Session,
    ) -> tuple[Session | None, SessionWriterLock | None]:
        """Ask what to do about ``path``, which another live process owns.

        ``opened`` is the target the caller has already loaded — reading it
        was never the problem, so the read-only answer wraps that rather than
        loading the same file a second time.

        Returns ``(session, lock)``; ``(None, None)`` is "cancel", the
        caller's signal to return ``RuntimeReplaceResult(cancelled=True)``
        without touching the live session.
        """

        from aelix_agent_core.session.session import Session as _Session

        ask = self._on_session_contended
        if ask is None:
            raise SessionError(
                "storage",
                f"session is already open in another terminal: {path}",
            )
        choice = await ask(path)
        if choice == "cancel":
            return None, None
        if choice == "fork":
            # Fork the TARGET, not the live session: the user asked to carry
            # on *that* conversation. ``fork_from`` publishes the whole of it
            # into a brand-new file under the same cwd — a file nobody can be
            # holding, so its lock always succeeds. (:meth:`fork` is
            # hard-wired to ``self.session`` and would have branched the wrong
            # history here; that is the mechanism the design was missing.)
            forked = await self._repo.fork_from(metadata, metadata.cwd)
            # Same seam ``/new`` and ``/fork`` use, for the same reason: a
            # brand-new file that somehow IS owned must fail closed rather
            # than hand back a ``None`` the caller cannot tell from "no
            # ownership to move".
            return forked, self._writer_lock_for_new_file(forked)
        # read_only — no lock at all. A shared-reader lock would exclude the
        # legitimate owner, which is the one situation this option exists for.
        return _Session(ReadOnlySessionStorage(opened.get_storage())), None

    # === Private replace seam (Pi `:115-173`) ===================================

    async def _emit_before_switch(
        self,
        reason: Literal["new", "resume"],
        target_session_file: str | None,
    ) -> bool:
        """Pi parity: ``emitBeforeSwitch`` (``agent-session-runtime.ts:115-130``).

        Sprint 6h₅a (Phase 4.14, ADR-0081, P-338) — real body replaces
        the Sprint 6h₄b P-308 stub. Gates on
        ``has_handlers("session_before_switch")`` so the payload is not
        constructed when no extension cares. Returns ``True`` when ANY
        handler returned :class:`SessionBeforeSwitchResult(cancel=True)`
        (Pi first-cancel-wins via the shared
        :func:`_reducer_session_before`).
        """

        from aelix_agent_core.harness.hooks import (
            SessionBeforeSwitchHookEvent,
            SessionBeforeSwitchResult,
        )

        runner = self._harness.extension_runner
        if not runner.has_handlers("session_before_switch"):
            return False
        result = await runner.emit(
            SessionBeforeSwitchHookEvent(
                type="session_before_switch",
                reason=reason,
                target_session_file=target_session_file,
            )
        )
        return isinstance(result, SessionBeforeSwitchResult) and result.cancel is True

    async def _emit_before_fork(
        self,
        entry_id: str,
        position: Literal["before", "at"],
    ) -> bool:
        """Pi parity: ``emitBeforeFork`` (``agent-session-runtime.ts:132-147``).

        Sprint 6h₅a (Phase 4.14, ADR-0081, P-339) — real body replaces
        the Sprint 6h₄b P-308 stub. Same first-cancel-wins semantics as
        :meth:`_emit_before_switch`.
        """

        from aelix_agent_core.harness.hooks import (
            SessionBeforeForkHookEvent,
            SessionBeforeForkResult,
        )

        runner = self._harness.extension_runner
        if not runner.has_handlers("session_before_fork"):
            return False
        result = await runner.emit(
            SessionBeforeForkHookEvent(
                type="session_before_fork",
                entry_id=entry_id,
                position=position,
            )
        )
        return isinstance(result, SessionBeforeForkResult) and result.cancel is True

    async def _teardown_current(
        self,
        reason: Literal["quit", "reload", "new", "resume", "fork"] = "quit",
        target_session_file: str | None = None,
    ) -> None:
        """Pi parity: ``teardownCurrent`` (``agent-session-runtime.ts:149-157``).

        Sprint 6h₅a (Phase 4.14, ADR-0081, P-340) — ORDERING CORRECTION
        to match Pi. The Sprint 6h₄b implementation reversed Pi's order
        (invalidate-then-dispose with NO shutdown emit). Pi order is:

          1. emit ``session_shutdown`` (extensions still see live harness
             state — last messages, current ``session_file``, etc).
          2. ``before_session_invalidate?.()`` (signals invalidation).
          3. ``await harness.dispose()`` (tears down HookBus +
             everything).

        **Race avoidance:** the ``extension_runner`` reference is
        captured at the TOP of the method BEFORE
        ``harness.dispose()`` is awaited (dispose tears down the
        HookBus → bridge becomes a no-op after).
        """

        # CRITICAL — capture runner BEFORE invalidate/dispose tears it
        # down. ``harness.dispose()`` clears the HookBus, after which
        # the runner bridge callables become no-ops.
        runner = self._harness.extension_runner
        try:
            await _emit_session_shutdown_event(runner, reason, target_session_file)
        except Exception:
            _log.exception("AgentSessionRuntime.session_shutdown emit raised")

        # Sprint 6h₅b (Phase 4.15, ADR-0083, P-363) — invalidate the OLD
        # runner between EMIT and ``before_session_invalidate``. Pi parity
        # (``runner.ts:466-473``): handlers that fire AFTER this point
        # see ``ctx.assert_active()`` raise :class:`ExtensionError("stale")`
        # carrying the Pi verbatim message from :data:`PI_STALENESS_MESSAGE`.
        try:
            runner.invalidate(PI_STALENESS_MESSAGE)
        except Exception:
            _log.exception("AgentSessionRuntime.runner.invalidate raised")

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

    async def _apply(
        self, new_session: Session, *, reload_seed: ReloadSeed | None = None
    ) -> None:
        """Pi parity: ``apply`` (``:159-164``).

        Pi reassigns ``this._session = newSession``; Aelix uses the
        factory to construct a NEW harness bound to ``new_session``
        (P-302/P-306). The factory is awaited so async setup (e.g.
        ``await harness.bootstrap()``) is permitted.

        ``reload_seed`` (Issue #24-FU) is passed ONLY on the reload path
        (:meth:`reload`); the /new//fork//resume/switch replace paths call the
        factory with just ``new_session``. The keyword is forwarded to the
        factory ONLY when a seed is present so single-positional factories
        (tests, ``rpc_ws``, the rpc noop) are never handed an unexpected kwarg
        — see the :data:`HarnessFactory` docstring.
        """
        if reload_seed is not None:
            new_harness = await self._create_harness(
                new_session, reload_seed=reload_seed
            )
        else:
            new_harness = await self._create_harness(new_session)
        self._harness = new_harness

    async def _finish_session_replacement(
        self,
        new_session: Session,
        *,
        reason: Literal["new", "resume", "fork"] = "resume",
        previous_session_file: str | None = None,
        target_session_file: str | None = None,
        setup: Callable[[Any], Awaitable[None]] | None = None,
        with_session: Callable[[ReplacedSessionContext], Awaitable[None]] | None = None,
        writer_lock: SessionWriterLock | None = None,
    ) -> None:
        """Pi parity: ``finishSessionReplacement`` (``agent-session-runtime.ts:166-173``).

        ``writer_lock`` (#137, Aelix-additive) is the lock the caller took for
        ``new_session``; it is adopted immediately after ``_apply`` — see the
        comment there for why that is the only safe instant.

        Order:
          1. ``_teardown_current(reason, target_session_file)`` (Sprint
             6h₅a: emits ``session_shutdown`` FIRST per Pi, then
             ``before_session_invalidate``, then disposes OLD harness).
          2. ``_apply`` (construct NEW harness from factory).
          3. Sprint 6h₅b (Phase 4.15, ADR-0083, P-359) — ``setup`` callback
             AFTER ``_apply`` BEFORE rebind. Pi parity ``:226-229``: the
             optional setup runs against the NEW harness's
             :class:`ReadonlySessionManager` while the harness is still
             un-rebound (so the caller can mutate state / inject messages
             before the wire layer captures the new reference). After
             setup, ``harness._state.messages`` is rebuilt from
             ``new_session.build_context().messages`` so any
             ``session.append_*`` calls made inside ``setup`` reflect in
             the active turn context.
          4. ``rebind_session?.(new_harness)`` (P-305 fire-and-await).
          5. Sprint 6h₅a (Phase 4.14, ADR-0081, P-343) — emit
             ``session_start`` on the NEW harness's ``extension_runner``
             (the OLD bus is disposed by step 1).
          6. Sprint 6h₅b (Phase 4.15, ADR-0083, P-358) — ``with_session``
             callback AFTER rebind + ``session_start`` emit. Pi parity
             ``:172-173``: receives the :class:`ReplacedSessionContext`
             handle built from :meth:`AgentHarness.create_replaced_session_context`
             on the NEW (post-replace) harness so the caller can run
             post-replacement work without tripping the OLD harness's
             stale guard.
        """

        from aelix_agent_core.harness.hooks import SessionStartHookEvent
        from aelix_agent_core.session.context import (
            build_session_context,
            resolve_resumed_thinking_level,
        )

        # #198 — snapshot the live thinking level BEFORE teardown, for the same
        # reason ``previous_session_file`` is snapshotted at the call sites: the
        # OLD harness is gone after ``_teardown_current`` and the factory builds
        # the NEW one from options alone, with no idea what level the user was
        # working at.
        previous_level = self._harness._state.thinking_level

        await self._teardown_current(reason, target_session_file)
        await self._apply(new_session)

        # #137 / ADR-0244 — ownership moves in the same breath as the harness,
        # and here rather than at the four call sites. ``_apply`` has just put
        # the live harness on ``new_session``; everything below this line
        # writes to it. Installing after the whole method returned — which is
        # what review found — meant that any raise below (ENOSPC on the
        # ``append_thinking_level_change`` a few lines down, a ``rebind_session``
        # that throws, ``with_session``) left this process live on the NEW
        # session while still holding the OLD file's lock, with the new lock a
        # dead local that nothing could ever release. Measured: two
        # ``FileLock``s on one path inside one process are mutually exclusive
        # under ``fcntl.flock``, so a retry of the same ``/import`` was then
        # told "open in another terminal" — about this process's own orphan.
        #
        # Before this point a raise leaves us on the OLD session, and the
        # caller's ``_release_unadopted`` drops the lock it took.
        if self._manages_writer_lock:
            self._install_writer_lock(writer_lock)

        # P-359 — setup AFTER apply, BEFORE rebind.
        if setup is not None:
            new_ctx = self._harness._make_context()
            session_manager = new_ctx.session_manager  # type: ignore[attr-defined]
            await setup(session_manager)
        # Rebuild the harness's long-lived ``_state.messages`` from the NEW
        # session's build_context on EVERY replacement (#122). Without this a
        # resume (setup=None) left ``_state.messages == []`` — a freshly built
        # harness never seeds ``initial_messages`` from its session — so
        # ``get_session_stats`` / ``_get_context_usage_safe`` read an empty
        # transcript and /context, /cost, /session, /stats all showed ZERO until
        # the next turn. It ALSO reflects any ``session.append_*`` performed inside
        # ``setup`` (the prior in-``if`` behaviour). Pi parity ``:226-229``. A
        # REPLACE assignment (idempotent; the next turn only ``extend``s the
        # delta), safe for /new + /fork too (an empty / forked session's
        # build_context yields exactly that session's message set).
        # One branch read feeds both rebuilds (``build_context`` IS
        # ``build_session_context(await get_branch())``).
        entries = await new_session.get_branch()
        session_ctx = build_session_context(entries)
        self._harness._state.messages = list(session_ctx.messages)

        # #198 — restore the thinking level the same way. Aelix rebuilds the
        # harness on a swap; pi does not (its ``AgentSession`` survives
        # ``switchSession``, ``agent-session-runtime.ts:256`` at ``pi@da840b6``),
        # which is why pi never needed this and why ``/resume`` here threw away
        # the level set in this very process. The target session's own recorded
        # level wins — including an explicit ``off``, which is a decision, not an
        # absence — and ``fallback`` carries the live level into a session that
        # has none, so resuming into any session written before #198 does not
        # snap back to ``off``. Clamped against the NEW harness's model (read
        # after ``_apply``), so an ``xhigh`` session on a ``high``-max model
        # resumes at ``high`` instead of being dropped. Assigned, not routed
        # through ``set_thinking_level``: firing ``thinking_level_select`` at
        # extensions would report a choice the user did not make, and the
        # messages rebuild above already assigns kernel state directly.
        restored_level = resolve_resumed_thinking_level(
            entries,
            self._harness.current_model,
            fallback=previous_level,
        )
        if restored_level is not None:
            self._harness._state.thinking_level = restored_level
            # A carried-forward level is RECORDED, not just assigned: otherwise
            # ``/new`` at ``medium`` produces a session whose file says ``off``
            # and the bug reproduces the next time that session is opened. Only
            # when the target had no level of its own (the fold below is the same
            # last-wins scan the helper ran) and the carried value is not the
            # kernel's unset sentinel — a fresh session at ``off`` stays clean.
            target_has_level = any(
                e.type == "thinking_level_change" for e in entries
            )
            # #137 — and not when the session we just landed on refuses
            # writes. ``_resolve_contended_target``'s read-only arm hands this
            # method a :class:`ReadOnlySessionStorage`, whose whole purpose is
            # that this process does not append to a file another terminal
            # owns; an unguarded append here would raise
            # ``SessionError("read_only")`` at the one point where the old
            # harness is already disposed and the new one is already live —
            # a half-swap. Same guard, same reason, as ``entry.py``'s
            # ``_seed_startup_state(read_only=…)`` on the startup path.
            if (
                not target_has_level
                and restored_level != "off"
                and not isinstance(
                    new_session.get_storage(), ReadOnlySessionStorage
                )
            ):
                await new_session.append_thinking_level_change(restored_level)

        if self._rebind_session is not None:
            await self._rebind_session(self._harness, reason)

        # P-343 — emit session_start on the NEW harness's runner. The OLD
        # runner is disposed by step 1; reading ``_harness`` here picks up
        # the freshly constructed one.
        new_runner = self._harness.extension_runner
        if new_runner.has_handlers("session_start"):
            try:
                await new_runner.emit(
                    SessionStartHookEvent(
                        type="session_start",
                        reason=reason,
                        previous_session_file=previous_session_file,
                    )
                )
            except Exception:
                _log.exception(
                    "AgentSessionRuntime.session_start emit raised"
                )

        # P-358 — with_session AFTER rebind + session_start emit. Pi parity
        # ``:172-173``. Receives a fresh ReplacedSessionContext handle on
        # the NEW harness so the caller bypasses the OLD harness's stale
        # guard. Sprint 6h₅b W6 (P-364 W5 MAJOR) threads ``self`` so the
        # 6 ExtensionCommandContext methods (Pi ``:371`` extension) wire
        # through to this runtime's command surface (``new_session`` /
        # ``fork`` / ``switch_session``).
        if with_session is not None:
            ctx = self._harness.create_replaced_session_context(runtime=self)
            await with_session(ctx)

    # === Public replace APIs (Pi `:175-364`) — Sprint 6h₄c real bodies ========

    async def switch_session(
        self,
        path: str,
        *,
        options: dict | None = None,
        with_session: Callable[[ReplacedSessionContext], Awaitable[None]] | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``switchSession`` (``agent-session-runtime.ts:175-198``).

        Sprint 6h₅a W4 MEDIUM correction (W5 audit) — Pi order at
        ``:184-189`` is:

          1. ``previousSessionFile = this.session.sessionFile`` (line 184).
          2. ``newSession = await SessionManager.open(path)`` (line 185).
          3. ``await assertSessionCwdExists(newSession, fallbackCwd, ...)``
             (line 186 — Pi asserts BEFORE letting any extension cancel).
          4. ``if (await emitBeforeSwitch(...)) { return {cancelled: true}; }``
             (line 189).
          5. ``await finishSessionReplacement(newSession, "resume", ...)``.

        W2 reversed this — emitted the cancel hook FIRST then loaded /
        asserted. Pi parity requires the cwd assertion to surface even
        when an extension would have cancelled the swap, so the error
        is observable to the caller rather than swallowed by the
        cancel short-circuit. Sprint 6h₅a W6 lifts the load + assert
        BEFORE the cancel hook to match Pi.
        """

        from aelix_agent_core.session.session_cwd import assert_session_cwd_exists

        # P-342 — snapshot BEFORE teardown (Pi line 184).
        previous_session_file = (
            self.session.session_file if self.session is not None else None
        )

        # Pi parity: load metadata + open + assert cwd FIRST (Pi lines 185-186).
        metadata = await load_jsonl_session_metadata(self._fs, path)

        # #137 / ADR-0244 — take ownership BEFORE the read, not after it.
        # ``repo.open`` snapshots the file's leaf into PROCESS-LOCAL state
        # (``jsonl_storage.py:692`` reparents every later append onto it), so a
        # turn the other terminal appends between our read and the moment we
        # own the file is reparented away by our next append — which is #137
        # itself, reproduced by the guard meant to prevent it. Review measured
        # it end to end ("stored: shared, A-final, B-next; replay: shared,
        # B-next"), and it is precisely the handoff
        # ``DEFAULT_ACQUIRE_TIMEOUT`` exists to absorb: the other terminal
        # releases the lock *just after* writing its last turn. Locking first
        # costs nothing — the metadata read above is a header sniff — and it
        # needs no tail re-read, because after this line nobody else may
        # append. ASKING about a contended file still happens below, after the
        # cancel hook: taking a lock is silent, putting a question in front of
        # a user an extension was about to overrule is not.
        target_lock: SessionWriterLock | None = None
        contended = False
        if self._manages_writer_lock:
            target_lock = self._acquire_writer_lock(path)
            contended = target_lock is None

        # From here the lock is live, so every exit path has to account for it.
        try:
            new_session = await self._repo.open(metadata)

            # P-337 — Pi ``session-cwd.ts:1-59``. Run AFTER ``repo.open`` so
            # ``new_session.session_file`` is populated; pass
            # ``fallback_cwd=self.cwd`` for actionable diagnostic context.
            await assert_session_cwd_exists(
                new_session, fallback_cwd=self.cwd, fs=self._fs
            )

            # Pi parity: emit cancel hook SECOND (Pi line 189).
            if await self._emit_before_switch(
                reason="resume", target_session_file=path
            ):
                self._release_unadopted(target_lock)
                return RuntimeReplaceResult(cancelled=True)

            # The contended question is the only step here that can change
            # WHICH session we end up on — a "fork and continue" answer swaps
            # in a different file — so the target path is re-read from the
            # session we actually landed on. Handing the ORIGINAL path to
            # ``_finish_session_replacement`` told every ``session_shutdown``
            # handler this process had switched to the file it deliberately
            # did not switch to, the one the other terminal still owns.
            target_session_file = path
            if contended:
                resolved, target_lock = await self._resolve_contended_target(
                    path, metadata, new_session
                )
                if resolved is None:
                    return RuntimeReplaceResult(cancelled=True)
                new_session = resolved
                target_session_file = new_session.session_file or path

            await self._finish_session_replacement(
                new_session,
                reason="resume",
                previous_session_file=previous_session_file,
                target_session_file=target_session_file,
                with_session=with_session,
                writer_lock=target_lock,
            )
        except BaseException:
            # The swap never adopted the lock, so this process is still on the
            # old session and the target's kernel lock has no owner left.
            self._release_unadopted(target_lock)
            raise
        return RuntimeReplaceResult(cancelled=False)

    async def new_session(
        self,
        *,
        parent_session: str | None = None,
        setup: Callable[[Any], Awaitable[None]] | None = None,
        with_session: Callable[[ReplacedSessionContext], Awaitable[None]] | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``newSession`` (``agent-session-runtime.ts:200-232``).

        Sprint 6h₄c (ADR-0079, P-325 / P-330) — real body. Replaces the
        Sprint 6d stub in ``rpc_mode.py`` which rejected
        ``parent_session`` with an :class:`RpcErrorResponse`. Pi waveform:
          1. ``emit_before_switch()`` → bail if cancelled.
          2. ``repo.create(JsonlSessionCreateOptions(cwd=current_cwd,
             parent_session_path=parent_session))`` builds a fresh session
             under the current cwd, lineage-linked to ``parent_session``
             if supplied (Pi parity ``:213-215``).
          3. ``_finish_session_replacement(new_session)``.
          4. Return ``RuntimeReplaceResult(cancelled=False)``.

        Sprint 6h₅b (Phase 4.15, ADR-0083, P-358/P-359) — Pi's optional
        ``setup`` (``:226-229``) and ``with_session`` (``:172-173``)
        2-stage callbacks land here. ``setup`` runs AFTER ``_apply``
        BEFORE rebind so the caller can mutate the NEW session before
        the wire layer captures the new harness; ``with_session`` runs
        AFTER rebind + ``session_start`` emit and receives the
        :class:`ReplacedSessionContext` handle from
        :meth:`AgentHarness.create_replaced_session_context`.

        Aelix-additive simplification: Pi takes an options dict
        (``{parentSession?, setup?, withSession?}``); Aelix exposes the
        three as keyword-only parameters for tighter pyright narrowing.
        """

        if await self._emit_before_switch(
            reason="new", target_session_file=None
        ):
            return RuntimeReplaceResult(cancelled=True)
        cwd = self.cwd
        if cwd is None:
            raise RuntimeError(
                "new_session requires the current harness session to have a cwd"
            )

        # P-342 — snapshot BEFORE teardown.
        previous_session_file = (
            self.session.session_file if self.session is not None else None
        )

        new_session = await self._repo.create(
            JsonlSessionCreateOptions(
                cwd=cwd, parent_session_path=parent_session
            )
        )
        # #137 — a file that has just been published cannot be contended, so
        # this never prompts; what it DOES do is move ownership, so the
        # session this terminal just walked away from becomes openable by
        # another one.
        new_lock = self._writer_lock_for_new_file(new_session)
        try:
            await self._finish_session_replacement(
                new_session,
                reason="new",
                previous_session_file=previous_session_file,
                target_session_file=None,
                setup=setup,
                with_session=with_session,
                writer_lock=new_lock,
            )
        except BaseException:
            self._release_unadopted(new_lock)
            raise
        return RuntimeReplaceResult(cancelled=False)

    async def reload(self) -> RuntimeReplaceResult:
        """Issue #24 (ADR-pending) — full hot-reload round-trip via the P-302
        factory rebuild. The moat keystone (#53): "agent writes
        ``.aelix/extensions/foo.py`` -> ``/reload`` -> ``/foo`` works, no restart".

        pi parity: ``AgentSession.reload`` (``agent-session.ts:2382-2413``). The
        harness CANNOT rebuild itself — it holds no ``_create_harness`` reference
        (its ``runtime`` property is the ``_ExtensionRuntime`` bridge, not this
        :class:`AgentSessionRuntime`). So reload is a runtime-level sibling of
        :meth:`new_session` / :meth:`fork` that re-runs the factory over the SAME
        :class:`Session` — :meth:`_apply` fuses pi's step 6 (``_resourceLoader.reload``
        re-discovers on-disk extensions) and step 7 (``_buildRuntime`` rebuilds the
        runtime + tool registry + HookBus) into one await, so a newly-written
        extension file is picked up and its handlers/commands/tools go live with NO
        process restart.

        Order (pi ``:2382-2413``), reusing :meth:`_teardown_current` / :meth:`_apply`:
          1. ``wait_for_idle`` — no mid-turn swap.
          2. snapshot ``previous_flag_values`` + the LIVE active-tool filter + the
             shared SettingsManager from the OLD runner BEFORE teardown.
          3. ``_teardown_current("reload")`` FIRST (pi :2385) — emits
             ``session_shutdown(reload)`` (handlers still see OLD settings),
             invalidates the OLD runner (captured ctx goes stale), disposes the OLD
             harness.
          4. ``settings_manager.reload()`` AFTER the shutdown emit, BEFORE the
             rebuild (pi :2386). aelix does NOT ``reset_api_providers()`` (clear-only
             with no re-register would brick streaming — see the step-4 note).
          5. ``_apply(session, reload_seed=ReloadSeed(flag_values=...))`` — factory
             re-discovers + rebuilds (pi :2391+:2393); the seed pre-seeds the fresh
             runtime's ``flag_values`` BEFORE each ``setup()`` re-runs (#24-FU).
          6. ACTIVE-TOOL ROUND-TRIP — restore the pre-teardown active-tool filter,
             unioned with the rebuilt extension tools (pi ``reload`` passes
             ``includeAllExtensionTools: true``), intersected with the fresh registry
             so a removed extension's tools drop out cleanly (#24-FU).
          7. FLAG ROUND-TRIP (belt-and-braces) — the ReloadSeed already made the
             end-state correct (setup() read the restored value, closing the earlier
             aelix divergence where it read the DEFAULT — ADR-0177); this overwrite is
             kept as defense for a factory that ignores the seed.
          8. ``_rebind_session(harness, "reload")`` — swap subscribers / UI / command
             context onto the new harness (the TUI skips its transcript reset on reload).
          9. emit ``session_start(reload)`` on the NEW runner (gated on handlers).
         10. ``reload_resources()`` (= pi ``extendResourcesFromExtensions("reload")``).

        Reuses the SAME ``Session`` (no ``repo.create``/``fork``), so message history
        and lineage are preserved. Returns :class:`RuntimeReplaceResult` for symmetry
        with the other replace APIs; reload never cancels (no before-switch veto).
        """

        from aelix_agent_core.harness.hooks import SessionStartHookEvent

        session = self._harness.session
        if session is None:
            raise RuntimeError(
                "reload requires the current harness to have a session"
            )

        # 1. No mid-turn swap. ``dispose`` (step 4) also aborts+drains a live
        #    turn, but assert idle first so reload is a clean, serialized op.
        await self._harness.wait_for_idle()

        # 2. Snapshot the OLD runtime's flag values + the shared SettingsManager
        #    BEFORE teardown. ``get_flag_values`` returns a shallow copy so it
        #    survives the subsequent dispose. pi :2384.
        previous_flag_values = self._harness.extension_runner.get_flag_values()
        # Snapshot the LIVE active-tool filter BEFORE teardown so a user's runtime
        # tool selection survives the rebuild (#24-FU). ``None`` => "all tools
        # active"; a list => an explicit filter. Copied because ``state`` is a
        # live reference disposed in step 3.
        active_before = self._harness.state.active_tool_names
        active_before = list(active_before) if active_before is not None else None
        settings_manager = self._harness.settings_manager

        # 3. Teardown FIRST (pi :2385): emit session_shutdown(reload) — so its
        #    handlers still observe the OLD settings — then invalidate the old runner
        #    and dispose the OLD harness.
        await self._teardown_current("reload", None)

        # 4. Reload settings AFTER the shutdown emit, BEFORE the rebuild (pi :2386),
        #    so the fresh harness re-resolves its model against the new settings.
        #    NOTE (adversarial-review HIGH): aelix deliberately does NOT call
        #    ``reset_api_providers()`` here. That helper is CLEAR-ONLY — it empties
        #    the process-global ``_PROVIDERS`` streaming-dispatch table with NO
        #    re-register — and the factory rebuild never re-runs
        #    ``register_providers()``, so clearing it would brick ALL model access
        #    until a process restart (defeating the #53 moat). ``_PROVIDERS`` is a
        #    stateless api→adapter dispatch table, unchanged across a reload, and
        #    /new /fork /resume rebuild the harness without touching it either;
        #    credential/setting changes are picked up via ``settings_manager.reload()``
        #    + the rebuilt harness's per-stream ``get_api_key_and_headers`` resolve.
        if settings_manager is not None:
            await settings_manager.reload()

        # 5. Rebuild via the factory over the SAME session — re-discovers on-disk
        #    extensions, fresh _ExtensionRuntime + HookBus + tool registry. The
        #    ReloadSeed (#24-FU) pre-seeds the fresh runtime's ``flag_values``
        #    BEFORE each extension's ``setup()`` re-runs, so a ``setup()`` that
        #    branches on a flag reads the user's restored value — mirroring pi
        #    ``_buildRuntime`` which seeds ``runtime.flagValues`` before the new
        #    ``ExtensionRunner`` (register_flag's ``name not in flag_values`` guard
        #    then skips the default).
        await self._apply(
            session, reload_seed=ReloadSeed(flag_values=previous_flag_values)
        )

        # 6. ACTIVE-TOOL ROUND-TRIP (#24-FU). Restore the user's pre-reload active
        #    filter over the REBUILT registry, unioned with the current extension
        #    tools — pi ``reload`` passes ``includeAllExtensionTools: true`` so a
        #    just-written extension's tool is usable even under an explicit
        #    ``--tools`` filter (the #53 moat). Names are intersected with the fresh
        #    registry so a REMOVED extension's tools drop out cleanly (aelix
        #    ``set_active_tools`` validates names and would otherwise raise). A
        #    ``None`` snapshot ("all tools were active") is preserved when the
        #    rebuild is already unfiltered, else expanded to the full current set.
        rebuilt = self._harness
        if active_before is None:
            if rebuilt.state.active_tool_names is not None:
                await rebuilt.set_active_tools(
                    [t.name for t in rebuilt.state.tools]
                )
        else:
            current_set = {t.name for t in rebuilt.state.tools}
            ext_names = [
                name
                for ext in rebuilt.extension_runner.extensions
                for name in ext.tools
            ]
            # (active_before ∩ current) ∪ (ext ∩ current), order-preserving
            # (kept-first, ext-second) and fully de-duplicated with ONE running
            # ``seen`` — mirrors pi ``_refreshToolRegistry``'s
            # ``[...activeToolNames].filter(...)`` + push-ext + ``new Set(...)``.
            # Intersecting BOTH lists with the rebuilt registry keeps
            # ``set_active_tools`` from raising on a name absent from ``state.tools``
            # (a removed-ext tool in the snapshot, or an ext tool shadowed by a
            # same-named app tool that now owns the registry entry).
            target_active: list[str] = []
            seen: set[str] = set()
            for name in (*active_before, *ext_names):
                if name in current_set and name not in seen:
                    seen.add(name)
                    target_active.append(name)
            await rebuilt.set_active_tools(target_active)

        # 7. FLAG ROUND-TRIP (belt-and-braces). The ReloadSeed already pre-seeded
        #    the fresh runtime's flag_values before ``setup()`` re-ran (#24-FU), so
        #    the end-state is already correct; this post-``_apply`` overwrite is
        #    kept as defense for any factory that ignores ``reload_seed``. Runs
        #    AFTER ``_apply`` and BEFORE the session_start emit so a handler
        #    reacting to session_start/reload reads the restored state.
        new_runner = self._harness.extension_runner
        for name, value in previous_flag_values.items():
            new_runner.set_flag_value(name, value)

        # 8. Swap subscribers / UI / command context onto the NEW harness. The TUI
        #    rebind skips its session-swap-only transcript + /stats resets on "reload".
        if self._rebind_session is not None:
            await self._rebind_session(self._harness, "reload")

        # 9. session_start(reload) on the NEW runner (gated on handlers; the OLD bus
        #    was disposed in step 4). pi :2407.
        if new_runner.has_handlers("session_start"):
            try:
                await new_runner.emit(
                    SessionStartHookEvent(type="session_start", reason="reload")
                )
            except Exception:
                _log.exception(
                    "AgentSessionRuntime.reload session_start emit raised"
                )

        # 10. resources re-discover (= pi ``extendResourcesFromExtensions("reload")``,
        #    :2411). Re-emits the resources_discover hook on the rebuilt harness.
        await self._harness.reload_resources()
        return RuntimeReplaceResult(cancelled=False)

    async def fork(
        self,
        entry_id: str,
        *,
        position: ForkPosition = "before",
        with_session: Callable[[ReplacedSessionContext], Awaitable[None]] | None = None,
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
               user message; ``target_leaf_id =
               fork_at_leaf(selected_entry.parent_id)``,
               ``selected_text = _extract_user_message_text(...)``.
               The first entry in a file has no parent, and a bare ``None``
               there reaches :func:`get_entries_to_fork` as "copy the whole
               session" — so forking before the first user message used to
               reproduce the session instead of starting an empty one
               (#300). :func:`fork_at_leaf` maps that ``None`` to
               :data:`FORK_FROM_ROOT`, which is the empty branch.
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

        if await self._emit_before_fork(
            entry_id=entry_id, position=position
        ):
            return RuntimeReplaceResult(cancelled=True)

        if self.session is None:
            raise RuntimeError("fork requires an active session")

        selected_entry = await self.session.get_entry(entry_id)
        if selected_entry is None:
            raise ValueError("Invalid entry ID for forking")

        selected_text: str | None = None
        if position == "at":
            target_leaf_id: ForkEntryId = selected_entry.id
        else:
            # position == "before"
            if (
                selected_entry.type != "message"
                or selected_entry.message.role != "user"  # type: ignore[union-attr]
            ):
                raise ValueError("Invalid entry ID for forking")
            # `fork_at_leaf`, not the raw `parent_id`: the first entry in a
            # file has none, and `entry_id=None` means "copy the whole
            # source session" downstream (#300).
            target_leaf_id = fork_at_leaf(selected_entry.parent_id)
            selected_text = _extract_user_message_text(
                selected_entry.message.content  # type: ignore[union-attr]
            )

        # P-342 — snapshot BEFORE teardown / get_metadata so the value
        # comes from the OLD session.
        previous_session_file = self.session.session_file

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
        # #137 — same as ``new_session``: the fork is a fresh file, so this
        # cannot contend, but the lock must follow us onto it and off the
        # file we forked away from.
        new_lock = self._writer_lock_for_new_file(new_session)
        try:
            await self._finish_session_replacement(
                new_session,
                reason="fork",
                previous_session_file=previous_session_file,
                target_session_file=None,  # Pi fork has no targetSessionFile
                with_session=with_session,
                writer_lock=new_lock,
            )
        except BaseException:
            self._release_unadopted(new_lock)
            raise
        return RuntimeReplaceResult(
            cancelled=False, selected_text=selected_text
        )

    async def import_from_jsonl(
        self,
        path: str,
        *,
        cwd: str | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``importFromJsonl`` (``agent-session-runtime.ts:329-364``).

        Sprint 6h₅b (Phase 4.15, ADR-0083, P-360) — real body replaces
        the Sprint 6h₄c stub. Pi waveform:

          1. Resolve the caller-supplied ``path`` to an absolute path.
          2. Raise :class:`SessionImportFileNotFoundError` if the file
             does not exist.
          3. Compute the destination path under the canonical sessions
             root for the effective cwd (``cwd`` override or the current
             session's cwd).
          4. Emit ``session_before_switch`` (``reason="resume"``); bail
             with ``cancelled=True`` if an extension cancels.
          5. Snapshot the previous session_file.
          6. Copy the source JSONL to the destination when the two paths
             differ (same-path → skip the copy).
          7. Load metadata from the destination; apply ``cwd`` override
             via :func:`dataclasses.replace` so the metadata's
             ``cwd`` field reflects the caller's intent.
          8. Open the new :class:`Session` and assert its cwd exists on
             disk (Pi ``:352``).
          9. Hand off to :meth:`_finish_session_replacement` with
             ``reason="resume"``. **Pi confirms — no ``with_session``
             plumbing here** (Pi signature for ``importFromJsonl`` is
             ``(path, cwd?)``, no callbacks).
        """

        from aelix_agent_core.session.session_cwd import assert_session_cwd_exists

        # Step 1 — resolve path.
        resolved_path = await self._fs.absolute_path(path)
        # Step 2 — existence probe.
        if not await self._fs.exists(resolved_path):
            raise SessionImportFileNotFoundError(resolved_path)

        # Step 3 — destination under sessions root.
        current_cwd = cwd or self.cwd
        if current_cwd is None:
            raise RuntimeError(
                "import_from_jsonl requires a cwd (either explicit or via "
                "the current harness session)"
            )
        session_dir = await self._repo._get_session_dir(current_cwd)
        await self._fs.create_dir(session_dir, recursive=True)
        destination_path = await self._fs.join_path(
            [session_dir, os.path.basename(resolved_path)]
        )

        # Step 4 — cancel hook.
        if await self._emit_before_switch(
            reason="resume", target_session_file=destination_path
        ):
            return RuntimeReplaceResult(cancelled=True)

        # Step 5 — snapshot previous file.
        previous_session_file = (
            self.session.session_file if self.session is not None else None
        )

        # #137 / ADR-0244 — BEFORE the copy, not after. The destination is
        # ``<sessions root>/<encoded cwd>/<source basename>``, so re-importing
        # a file that has already been imported here targets a path another
        # terminal may be writing to right now — and step 6's ``copy_file``
        # replaces its destination wholesale (ADR-0242: staged in a temp, then
        # renamed over). Resolving ownership afterwards would mean the other
        # terminal's session is already gone by the time we ask.
        #
        # Deliberately no "fork and continue" here: the answer to an owned
        # destination is to leave it alone, and an import already produces a
        # new file whenever the basename is free.
        import_lock: SessionWriterLock | None = None
        if self._manages_writer_lock:
            import_lock = self._acquire_writer_lock(destination_path)
            if import_lock is None:
                raise SessionError(
                    "storage",
                    "cannot import over a session that is open in another "
                    f"terminal: {destination_path}",
                )

        # From here the lock is live and every exit path has to account for it
        # — five awaits below can raise (``copy_file``, the metadata load,
        # ``repo.open``, the cwd assertion, the replacement itself) and review
        # found each of them dropping it on the floor: neither released nor
        # stored, so the kernel held it for the life of the process with no
        # object left to let go. A retried ``/import`` then hit its own
        # orphan and was told the destination was "open in another terminal".
        try:
            # Step 6 — copy when paths differ.
            if resolved_path != destination_path:
                await self._fs.copy_file(resolved_path, destination_path)

            # Step 7 — load metadata + cwd override.
            metadata = await load_jsonl_session_metadata(
                self._fs, destination_path
            )
            if cwd is not None:
                metadata = replace(metadata, cwd=cwd)

            # Step 8 — open + assert cwd exists. Pi parity:
            # ``SessionManager.open(path, dir, cwdOverride)`` threads the
            # override into the loaded session's ``cwd`` field. Aelix routes
            # the override via the repo-seam ``cwd_override`` keyword (Sprint
            # 6h₅b W6 P-367 W5 MINOR fix) instead of mutating
            # ``storage._metadata`` from outside the repo — the writeback
            # now lives on the single owner (:meth:`JsonlSessionRepo.open`)
            # so the private-attribute touch stays encapsulated.
            new_session = await self._repo.open(
                metadata, cwd_override=cwd if cwd is not None else None
            )
            await assert_session_cwd_exists(
                new_session, fallback_cwd=current_cwd, fs=self._fs
            )

            # Step 9 — finish replacement (Pi: no with_session for import).
            await self._finish_session_replacement(
                new_session,
                reason="resume",
                previous_session_file=previous_session_file,
                target_session_file=destination_path,
                writer_lock=import_lock,
            )
        except BaseException:
            self._release_unadopted(import_lock)
            raise
        return RuntimeReplaceResult(cancelled=False)

    # === Dispose (Pi `:366-373`) ===============================================

    async def dispose(self) -> None:
        """Pi parity: ``dispose`` (``agent-session-runtime.ts:366-373``).

        Sprint 6h₅a (Phase 4.14, ADR-0081, P-341) — adds the missing
        ``session_shutdown`` emit with ``reason="quit"``. Sprint 6h₅a W5
        P-355 BLOCKING FIX — order corrected to **EMIT → INVALIDATE →
        DISPOSE**, matching Pi ``agent-session-runtime.ts:366-373``
        verbatim:

        .. code-block:: typescript

           async dispose(): Promise<void> {
               await emitSessionShutdownEvent(this.session.extensionRunner, {
                   type: "session_shutdown", reason: "quit",
               });
               this.beforeSessionInvalidate?.();
               this.session.dispose();
           }

        W2 originally implemented INVALIDATE → EMIT → DISPOSE based on
        a spec §J misread of Pi ``:366-373``; the supposed "intentional
        asymmetry" did not exist in Pi. ``dispose`` and
        :meth:`_teardown_current` now use the **same** order (EMIT FIRST
        so extensions can read live harness state before invalidate).

        **Race avoidance:** the ``extension_runner`` reference is
        captured at the TOP of the method BEFORE ``harness.dispose()``
        is awaited (dispose tears down the HookBus → bridge becomes a
        no-op after).
        """

        # Capture runner BEFORE invalidate/dispose — see P-340 race note.
        runner = self._harness.extension_runner

        # EMIT FIRST (Pi line 367-370).
        try:
            await _emit_session_shutdown_event(runner, "quit", None)
        except Exception:
            _log.exception("AgentSessionRuntime.session_shutdown emit raised")

        # Sprint 6h₅b (Phase 4.15, ADR-0083, P-363) — invalidate the runner
        # between EMIT and ``before_session_invalidate`` (same insertion
        # point as :meth:`_teardown_current`). Pi parity ``runner.ts:466-473``
        # — handlers running AFTER this point see
        # :class:`ExtensionError("stale")` with the Pi verbatim message.
        try:
            runner.invalidate(PI_STALENESS_MESSAGE)
        except Exception:
            _log.exception("AgentSessionRuntime.runner.invalidate raised")

        # INVALIDATE SECOND (Pi line 371).
        if self._before_session_invalidate is not None:
            try:
                self._before_session_invalidate()
            except Exception:
                _log.exception(
                    "AgentSessionRuntime.before_session_invalidate raised"
                )

        # DISPOSE THIRD (Pi line 372).
        await self._harness.dispose()

        # #137 / ADR-0244 — LAST, and after ``harness.dispose()``: an
        # extension's ``session_shutdown`` handler is allowed to write a final
        # entry, and it must still be writing to a file this process owns.
        # The kernel would drop the lock anyway when the process exits; this
        # is what makes `/quit` release it while the process lives on (the
        # TUI returns to ``entry.py``, which has more to do).
        self._install_writer_lock(None)


async def create_agent_session_runtime(
    harness: AgentHarness,
    create_harness: HarnessFactory,
    *,
    repo: JsonlSessionRepo,
    fs: FileSystem,
    diagnostics: list[AgentSessionRuntimeDiagnostic] | None = None,
    model_fallback_message: str | None = None,
    session_start_event: Any | None = None,
) -> AgentSessionRuntime:
    """Pi parity: ``createAgentSessionRuntime`` (``agent-session-runtime.ts:382-400``).

    Sprint 6h₅c (ADR-0085, P-370 + P-371). Module-level async factory that
    Pi calls at the runtime construction site. Two Pi invariants are
    materialised here:

      - **P-370 — Pi line ``:391``** — when the harness already has a
        :class:`Session` bound, :func:`assert_session_cwd_exists` runs
        BEFORE the runtime is constructed. The assertion raises
        :class:`MissingSessionCwdError` if the stored cwd is missing
        from disk, which is the Pi "factory bootstrap" call site
        (deferred from Sprint 6h₅a per ADR-0081 §carry-forward).
      - **P-371 — Pi lines ``:326`` + ``:2050``** — Pi emits
        :class:`SessionStartHookEvent(reason="startup")` once on
        bootstrap. Aelix mirrors via the ``session_start_event``
        kwarg: when :data:`None` (the Pi ``??`` default), the factory
        constructs the default startup payload. Callers needing a
        different reason (e.g. ``"reload"``, deferred to Sprint 6h₅d
        for the ``reload()`` path) can pre-build the event and pass
        it explicitly. The emit is gated on
        :meth:`ExtensionRunner.has_handlers` (Pi ``runner.ts:178-180``
        short-circuit) so the payload is never constructed when no
        extension cares.

    Failure semantics: a raising ``session_start`` handler is logged
    but does NOT propagate — bootstrap MUST complete even when an
    extension misbehaves (mirrors the
    :meth:`AgentSessionRuntime._finish_session_replacement`
    P-343 emit policy).
    """

    # P-370 — Pi line :391. Skip when no session is bound (factory may
    # be invoked against an in-memory harness in tests).
    #
    # Sprint 6h₅d §C (P-375): imports for ``SessionStartHookEvent`` +
    # ``assert_session_cwd_exists`` are hoisted to module top-level so
    # tests can monkeypatch via ``monkeypatch.setattr`` on a single
    # binding site (``runtime.agent_session_runtime``) without relying on
    # the prior function-local re-resolution of ``session.session_cwd``.
    #
    # Sprint 6h₅d §E (P-384 / MINOR-3): read through
    # :attr:`AgentHarness.session` instead of the private attribute.
    harness_session = harness.session
    if harness_session is not None:
        await assert_session_cwd_exists(
            harness_session, fallback_cwd=None, fs=fs
        )

    runtime = AgentSessionRuntime(
        harness,
        create_harness,
        repo=repo,
        fs=fs,
        diagnostics=diagnostics,
        model_fallback_message=model_fallback_message,
    )

    # P-371 — Pi :326 + :2050. The Pi `??` default lives in the
    # ``session_start_event=None`` sentinel.
    event = session_start_event or SessionStartHookEvent(
        type="session_start",
        reason="startup",
        previous_session_file=None,
    )
    runner = harness.extension_runner
    if runner.has_handlers("session_start"):
        try:
            await runner.emit(event)
        except Exception:
            _log.exception(
                "create_agent_session_runtime.session_start emit raised"
            )

    return runtime


__all__ = ["AgentSessionRuntime", "create_agent_session_runtime"]
