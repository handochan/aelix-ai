"""ExtensionCommandContext (Sprint 5b §D, ADR-0042; P0 #7 item 4).

Pi parity ``ExtensionCommandContext`` (``types.ts:333-364``) extends
:class:`ExtensionContext` with 6 lifecycle methods exposed to slash-command
handlers: ``wait_for_idle`` / ``new_session`` / ``fork`` / ``navigate_tree`` /
``switch_session`` / ``reload``.

Sprint 5b landed 4 of 6 (``wait_for_idle`` / ``fork`` / ``navigate_tree`` /
``reload``); ``new_session`` / ``switch_session`` raised a "deferred" error.

P0 #7 item 4 closes the gap: ``new_session`` / ``switch_session`` now delegate
to :class:`AgentSessionRuntime` (Pi ``runner.ts:636-668`` overlays delegating
to ``newSessionHandler`` / ``switchSessionHandler`` / ``forkHandler``) when an
:class:`AgentSessionRuntime` is bound; ``fork`` is realigned to Pi's
``fork(entryId: string, options)`` signature (Pi ``types.ts:341-344``) and
delegates to :meth:`AgentSessionRuntime.fork` when bound, keeping
:meth:`JsonlSessionRepo.fork` as the unattached fallback.

The optional ``with_session`` callback flows into the runtime, which already
produces the :class:`ReplacedSessionContext` handle via
``finishSessionReplacement`` → ``create_replaced_session_context``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.extensions.api import (
    ExtensionContext,
    ExtensionError,
    _ExtensionRuntime,
)

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import (
        AgentHarness,
        NavigateTreeOptions,
        NavigateTreeResult,
    )
    from aelix_agent_core.runtime._types import (
        ReplacedSessionContext,
        RuntimeReplaceResult,
    )
    from aelix_agent_core.runtime.agent_session_runtime import (
        AgentSessionRuntime,
    )
    from aelix_agent_core.session import Session
    from aelix_agent_core.session.jsonl_repo import (
        JsonlSessionMetadata,
        JsonlSessionRepo,
    )
    from aelix_agent_core.session.repo_utils import ForkPosition


class ExtensionCommandContext(ExtensionContext):
    """Pi parity ``ExtensionCommandContext`` (Sprint 5b §D, ADR-0042; P0 #7 item 4).

    Constructed by the CLI command dispatcher; carries the same fields as a
    plain :class:`ExtensionContext` plus 6 lifecycle methods. ``new_session`` /
    ``switch_session`` / ``fork`` delegate to a bound
    :class:`AgentSessionRuntime` (Pi ``runner.ts:636-668``); ``fork`` falls back
    to :meth:`JsonlSessionRepo.fork` when no runtime is bound.
    """

    def __init__(
        self,
        runtime: _ExtensionRuntime,
        *,
        harness: AgentHarness,
        repo: JsonlSessionRepo | None = None,
        session_runtime: AgentSessionRuntime | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(runtime, **kwargs)
        object.__setattr__(self, "_harness", harness)
        object.__setattr__(self, "_repo", repo)
        # Distinct slot from ``_runtime`` (the ``_ExtensionRuntime``); this
        # holds the AgentSessionRuntime command surface (Pi
        # ``newSessionHandler`` / ``forkHandler`` / ``switchSessionHandler``).
        object.__setattr__(self, "_runtime_session", session_runtime)

    async def wait_for_idle(self) -> None:
        """Pi parity ``waitForIdle`` — block until phase returns to idle."""

        await object.__getattribute__(self, "_harness").wait_for_idle()

    async def fork(
        self,
        entry_id: str | JsonlSessionMetadata,
        options: Any | None = None,
    ) -> RuntimeReplaceResult | Session:
        """Pi parity ``fork`` (``types.ts:341-344``).

        Pi shape: ``fork(entryId: string, options?: {position?, withSession?})``.

        When an :class:`AgentSessionRuntime` is bound (``session_runtime``),
        delegate to :meth:`AgentSessionRuntime.fork` with the realigned
        ``(entry_id, position, with_session)`` shape — this is the Pi-faithful
        ``forkHandler`` path (Pi ``runner.ts:637``).

        When no runtime is bound, fall back to the legacy
        :meth:`JsonlSessionRepo.fork` path (``source: JsonlSessionMetadata``,
        ``options: ForkOptions``) for callers that drive the repo directly.
        """

        session_runtime = object.__getattribute__(self, "_runtime_session")
        if session_runtime is not None:
            position: ForkPosition = "before"
            with_session: (
                Callable[[ReplacedSessionContext], Awaitable[None]] | None
            ) = None
            if options is not None:
                position = _opt(options, "position", "before")
                with_session = _opt(options, "with_session", None)
            return await session_runtime.fork(
                entry_id,  # type: ignore[arg-type]
                position=position,
                with_session=with_session,
            )

        repo = object.__getattribute__(self, "_repo")
        if repo is None:
            raise ExtensionError(
                "invalid_state",
                "ExtensionCommandContext.fork() requires either an "
                "AgentSessionRuntime binding or a JsonlSessionRepo binding.",
            )
        return await repo.fork(entry_id, options)  # type: ignore[arg-type]

    async def navigate_tree(
        self,
        target_id: str | None,
        options: NavigateTreeOptions | None = None,
    ) -> NavigateTreeResult:
        """Pi parity ``navigateTree`` — delegates to harness."""

        return await object.__getattribute__(self, "_harness").navigate_tree(
            target_id, options
        )

    async def reload(self) -> None:
        """Pi parity ``reload`` — hot-reload the harness.

        When an :class:`AgentSessionRuntime` is bound (``session_runtime``),
        delegate to :meth:`AgentSessionRuntime.reload` — the full P-302 factory
        rebuild (re-discover extensions/resources + rebuild the harness over the
        SAME session), matching Pi ``reload`` which re-runs ``_buildRuntime``
        (Issue #24 / ADR-0177). This is the same runtime-delegation the sibling
        lifecycle methods (:meth:`fork` / :meth:`new_session` /
        :meth:`switch_session`) use. The :class:`RuntimeReplaceResult` is
        discarded — Pi ``reload`` returns ``void``.

        When no runtime is bound (e.g. the minimal REPL, or RPC/print surfaces
        that do not yet construct a command-dispatch runtime), fall back to the
        cheaper :meth:`AgentHarness.reload_resources` (the ``resources_discover``
        chain only, no rebuild).

        NOTE: the runtime path disposes the harness this context was created
        from (staleness). The reload completes before this handler returns, but
        any *further* use of this ``ctx`` (or the ``_harness`` slot) after
        ``reload()`` hits the invalidated runtime and raises ``stale`` — this is
        the same tear-down the other lifecycle methods incur.
        """

        session_runtime = object.__getattribute__(self, "_runtime_session")
        if session_runtime is not None:
            await session_runtime.reload()
            return
        await object.__getattribute__(self, "_harness").reload_resources()

    async def new_session(
        self, options: Any | None = None
    ) -> RuntimeReplaceResult:
        """Pi parity ``newSession`` (``runner.ts:636``; ``types.ts:336-340``).

        Pi shape: ``newSession(options?: {parentSession?, setup?, withSession?})``.
        Delegates to :meth:`AgentSessionRuntime.new_session` when a runtime is
        bound; raises a clear error otherwise.
        """

        session_runtime = object.__getattribute__(self, "_runtime_session")
        if session_runtime is None:
            raise ExtensionError(
                "invalid_state",
                "ExtensionCommandContext.new_session() requires an "
                "AgentSessionRuntime binding.",
            )
        parent_session: str | None = None
        setup: Callable[[Any], Awaitable[None]] | None = None
        with_session: (
            Callable[[ReplacedSessionContext], Awaitable[None]] | None
        ) = None
        if options is not None:
            parent_session = _opt(options, "parent_session", None)
            setup = _opt(options, "setup", None)
            with_session = _opt(options, "with_session", None)
        return await session_runtime.new_session(
            parent_session=parent_session,
            setup=setup,
            with_session=with_session,
        )

    async def switch_session(
        self, target: str, options: Any | None = None
    ) -> RuntimeReplaceResult:
        """Pi parity ``switchSession`` (``runner.ts:638``; ``types.ts:349-352``).

        Pi shape: ``switchSession(sessionPath: string, options?: {withSession?})``.
        Delegates to :meth:`AgentSessionRuntime.switch_session` when a runtime
        is bound; raises a clear error otherwise.
        """

        session_runtime = object.__getattribute__(self, "_runtime_session")
        if session_runtime is None:
            raise ExtensionError(
                "invalid_state",
                "ExtensionCommandContext.switch_session() requires an "
                "AgentSessionRuntime binding.",
            )
        with_session: (
            Callable[[ReplacedSessionContext], Awaitable[None]] | None
        ) = None
        if options is not None:
            with_session = _opt(options, "with_session", None)
        return await session_runtime.switch_session(
            target, with_session=with_session
        )


def _opt(options: Any, key: str, default: Any) -> Any:
    """Read an option key from a Mapping or an attribute-style object.

    Pi passes options as a plain object (``options?.withSession``); Aelix
    callers may pass a ``dict`` or a dataclass/namespace, so accept both.
    """

    if options is None:
        return default
    if isinstance(options, dict):
        return options.get(key, default)
    return getattr(options, key, default)


__all__ = ["ExtensionCommandContext"]
