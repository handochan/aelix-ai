"""ExtensionCommandContext (Sprint 5b §D, ADR-0042).

Pi parity ``ExtensionCommandContext`` (``types.ts:333-364``) extends
:class:`ExtensionContext` with 6 lifecycle methods exposed to slash-command
handlers. Sprint 5b lands 4 of 6 (P-35): ``wait_for_idle`` / ``fork`` /
``navigate_tree`` / ``reload``. ``new_session`` / ``switch_session`` are
deferred to Phase 5 (CLI lifecycle).
"""

from __future__ import annotations

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
    from aelix_agent_core.session import Session
    from aelix_agent_core.session.jsonl_repo import (
        JsonlSessionMetadata,
        JsonlSessionRepo,
    )
    from aelix_agent_core.session.repo_utils import ForkOptions


class ExtensionCommandContext(ExtensionContext):
    """Pi parity ``ExtensionCommandContext`` (Sprint 5b §D, ADR-0042).

    Constructed by the CLI command dispatcher; carries the same fields as a
    plain :class:`ExtensionContext` plus 4 lifecycle methods (Sprint 5b lands
    4 of 6; ``new_session`` + ``switch_session`` raise until Phase 5 lands
    the ``SessionManager.replaceSession`` plumbing).
    """

    def __init__(
        self,
        runtime: _ExtensionRuntime,
        *,
        harness: AgentHarness,
        repo: JsonlSessionRepo | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(runtime, **kwargs)
        object.__setattr__(self, "_harness", harness)
        object.__setattr__(self, "_repo", repo)

    async def wait_for_idle(self) -> None:
        """Pi parity ``waitForIdle`` — block until phase returns to idle."""

        await object.__getattribute__(self, "_harness").wait_for_idle()

    async def fork(
        self,
        source: JsonlSessionMetadata,
        options: ForkOptions,
    ) -> Session:
        """Pi parity ``fork`` — delegates to :meth:`JsonlSessionRepo.fork`."""

        repo = object.__getattribute__(self, "_repo")
        if repo is None:
            raise ExtensionError(
                "invalid_state",
                "ExtensionCommandContext.fork() requires a JsonlSessionRepo binding.",
            )
        return await repo.fork(source, options)

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
        """Pi parity ``reload`` — re-fires the ``resources_discover`` chain."""

        await object.__getattribute__(self, "_harness").reload_resources()

    async def new_session(self, options: Any | None = None) -> None:
        """Pi parity ``newSession`` — deferred to Phase 5 CLI lifecycle."""

        raise ExtensionError(
            "invalid_state",
            "ExtensionCommandContext.new_session is deferred to Phase 5 "
            "(deferred to Sprint 6h₁₀b — see ADR-0100).",
        )

    async def switch_session(
        self, target: Any, options: Any | None = None
    ) -> None:
        """Pi parity ``switchSession`` — deferred to Phase 5 CLI lifecycle."""

        raise ExtensionError(
            "invalid_state",
            "ExtensionCommandContext.switch_session is deferred to Phase 5 "
            "(deferred to Sprint 6h₁₀b — see ADR-0100).",
        )


__all__ = ["ExtensionCommandContext"]
