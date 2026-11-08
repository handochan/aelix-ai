"""``ExtensionAPI.jobs``: what an extension sees of the job service (#722, ADR-0293).

An extension registers a kind in its ``setup`` (a ``/reload`` registers it
again — a new generation) and starts jobs of it for the current session. A
job's runner must not keep the ``ExtensionAPI`` or a context past its start:
the reload invalidates both while the job may still be finishing.

**Bound to its extension and its runtime** (Codex #18). Every call checks that
background jobs are on and that the runtime the facade came from is still
live, so a facade kept past a reload or a session change refuses instead of
acting for whoever is current. It registers kinds under its extension's name
(another extension cannot take one over), and it starts, lists, reads and
stops only jobs of the kinds its extension registered, of the current session.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.jobs.activation import require_active
from aelix_coding_agent.jobs.contract import JobKindAdapter, JobSnapshot, RunnerFactory

if TYPE_CHECKING:
    from aelix_coding_agent.jobs.registry import Registration
    from aelix_coding_agent.jobs.service import JobService


class JobsFacade:
    def __init__(
        self,
        service: JobService,
        *,
        source: str = "",
        lifetime: Callable[[], None] | None = None,
    ) -> None:
        self._service = service
        self.source = source
        self._lifetime = lifetime

    def _check(self) -> None:
        require_active()
        if self._lifetime is not None:
            self._lifetime()

    def _mine(self, kind: str) -> bool:
        return self._service.registry.owner_of(kind) == self.source

    def register_kind(self, adapter: JobKindAdapter, runner_factory: RunnerFactory) -> Registration:
        self._check()
        return self._service.register_kind(
            adapter, runner_factory, source=self.source, lifetime=self._lifetime
        )

    async def start(
        self,
        kind: str,
        *,
        title: str,
        params: Mapping[str, Any] | None = None,
        notify: bool | None = None,
    ) -> str:
        """Start a job of ``kind`` for the current session; its id. See ``JobService.start``."""

        from aelix_coding_agent.jobs.service import JobStartRefused

        self._check()
        if not self._mine(kind):
            raise JobStartRefused(f"job kind {kind!r} is not one this extension registered")
        return await self._service.start(kind, title=title, params=params, notify=notify)

    def list(self) -> list[JobSnapshot]:
        self._check()
        return [s for s in self._service.snapshots() if self._mine(s.kind)]

    def status(self, job_id: str) -> JobSnapshot | None:
        self._check()
        snap = self._service.snapshot(job_id)
        return snap if snap is not None and self._mine(snap.kind) else None

    async def stop(self, job_id: str) -> JobSnapshot | None:
        self._check()
        snap = self._service.snapshot(job_id)
        if snap is None or not self._mine(snap.kind):
            return None
        return await self._service.stop(job_id, reason="user")


__all__ = ["JobsFacade"]
