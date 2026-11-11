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
from aelix_coding_agent.jobs.contract import JobKindAdapter, JobSnapshot, RunnerFactory, StopReason

if TYPE_CHECKING:
    from aelix_coding_agent.jobs.admission import Reservation
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

    def reserve(self, kind: str, wanted: int = 1) -> Reservation:
        """Up to ``wanted`` slots of ``kind``, at least one, before the client asks consent (#611 S1c).

        ``JobService.reserve``, for a kind this extension registered. Hand the
        reservation to each :meth:`start`; release what is left. Refused with
        ``JobStartRefused`` (the admission's sentence) when none is free.
        """

        from aelix_coding_agent.jobs.service import JobStartRefused

        self._check()
        if not self._mine(kind):
            raise JobStartRefused(f"job kind {kind!r} is not one this extension registered")
        return self._service.reserve(kind, wanted)

    async def start(
        self,
        kind: str,
        *,
        title: str,
        params: Mapping[str, Any] | None = None,
        notify: bool | None = None,
        reservation: Reservation | None = None,
        client_start: Mapping[str, Any] | None = None,
    ) -> str:
        """Start a job of ``kind`` for the current session; its id. See ``JobService.start``.

        ``reservation`` is one :meth:`reserve` returned (#611 S1c): the job's
        slot is taken out of it, so a consented start is never refused.
        ``client_start`` is the kind's own launch data, recorded in the job's
        ``start`` fact and handed back on its snapshots (#611 S2d).
        """

        from aelix_coding_agent.jobs.service import JobStartRefused

        self._check()
        if not self._mine(kind):
            raise JobStartRefused(f"job kind {kind!r} is not one this extension registered")
        return await self._service.start(
            kind,
            title=title,
            params=params,
            notify=notify,
            reservation=reservation,
            client_start=client_start,
        )

    def list(self) -> list[JobSnapshot]:
        self._check()
        return [s for s in self._service.snapshots() if self._mine(s.kind)]

    def status(self, job_id: str) -> JobSnapshot | None:
        self._check()
        snap = self._service.snapshot(job_id)
        return snap if snap is not None and self._mine(snap.kind) else None

    # The service's reading names, for a client that reads through the facade
    # what a host reads off the service (#611 S1c).
    snapshot = status
    snapshots = list

    async def changed(self) -> None:
        """Wait for the next change to any job (``JobService.changed``)."""

        self._check()
        await self._service.changed()

    async def stop(
        self, job_id: str, *, reason: StopReason = "user", wait: float | None = None
    ) -> JobSnapshot | None:
        self._check()
        snap = self._service.snapshot(job_id)
        if snap is None or not self._mine(snap.kind):
            return None
        return await self._service.stop(job_id, reason=reason, wait=wait)


__all__ = ["JobsFacade"]
