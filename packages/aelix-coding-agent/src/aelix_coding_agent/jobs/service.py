"""The job service: one per process, owning every background job its sessions start (#722, ADR-0293).

**What it owns.** For every job: its state, its runner's task, the processes and
directories it registered, its admission slot, its facts in the owning session,
its notice, and the liveness of all of these. Clients (delegation, background
``bash``, an extension's kind) own only what their runner does and how their
records read (:mod:`aelix_coding_agent.jobs.contract`). Nothing here names a
kind: labels, settings keys and hints are the strings a client registered.

**States.** ``starting`` → ``running`` → (``stopping``) → ``done`` | ``failed``
| ``stopped``; ``lost`` only from a restart's evidence; ``unresolved`` when the
barrier could not confirm the processes gone, a runner's ``settle`` or
``helper_cleanup`` failed, or a restart cannot tell. A terminal state is
recorded only after the barrier passed: the runner settled, every registered
root reaped, every group empty, every walked descendant gone, every
service-owned tree's members waited on. A stop the runner does not honour is
the service's to finish: its own kill legs after the stop grace, then a cancel
of ``run`` that still goes through the barrier (Codex #6).

**Write order at the end** (ADR-0293 §records): the client's records, then
``cleanup``, then ``outcome`` with its notice, then the notice is posted. The
slot is released once cleanup is confirmed — not when the notice goes, and not
only when the records land. An outcome that cannot be written is retried by
the liveness loop and its notice waits for it.

**Ownership.** A job belongs to the session that started it. ``/new``,
``/resume``, ``/fork`` and quitting stop the jobs of the session being left;
``/reload`` stops every job of the session (owner decision 4). A stop waits for
the barriers, in parallel, up to :data:`CLEANUP_BOUND`; what is still not
confirmed is shown ``unresolved`` and finishes in the background, its records
going to the session it was captured with. The service outlives every harness
build: it is built once by the CLI (``activation.install_service``).

**Monitoring** (owner, 2026-11-07: "코어 메인세션에서 백그라운드 잡을 모니터링하는게
혹은 확인하는 게 더 중요"): a liveness loop every :data:`LIVENESS_INTERVAL` while
any job is not terminal. A root that is gone while its runner has not returned
for :data:`EXIT_SETTLE_GRACE` is stopped, cancelled and barriered into
``failed`` (or ``unresolved``); a job with no activity for
:data:`QUIET_AFTER` is flagged ``quiet`` — never stopped, never notified (#604:
no timeout).

**Restart** (owner decision 4): report-only. :meth:`JobService.bind` folds the
session's facts once per owner and posts what is undelivered; a job started
and not ended is recovered by its kind from confirmed client records, or
classified from OS evidence (:func:`~aelix_coding_agent.jobs.evidence.classify_restart`).
Nothing found at start-up is ever signalled. One that stays ``unresolved`` holds
a slot of its kind, leaves the model a restored notice, and — when OS evidence
decided it — is classified again by the liveness loop (Codex #11).

**Ownership** (Codex #5). Every reading and control path answers only for the
current session's jobs: an id of another session's job is not found.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from aelix_agent_core.harness.inbox import Notice, PromptNoticesResult
from aelix_ai.utils._process_identity import process_started

from aelix_coding_agent.jobs import evidence as ev
from aelix_coding_agent.jobs import notice as nt
from aelix_coding_agent.jobs.admission import Admission, AdmissionRefused, Reservation
from aelix_coding_agent.jobs.contract import (
    TERMINAL_STATES,
    ClientOutcome,
    HelperCleanup,
    JobKindAdapter,
    JobRunner,
    JobSnapshot,
    JobState,
    KindCapabilities,
    NoticeBody,
    OutputSlice,
    Recovered,
    RunnerFactory,
    StopReason,
)
from aelix_coding_agent.jobs.records import (
    NOTICE_CUSTOM_TYPE,
    FoldedJob,
    RecordWriteFailed,
    append_fact,
    append_record,
    client_records_of,
    delivered,
    fold,
    make_fact,
)
from aelix_coding_agent.jobs.registry import KindRegistry, Registration

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness

_log = logging.getLogger(__name__)

#: How long an owner's end or a ``/reload`` waits for its jobs' barriers (in parallel).
CLEANUP_BOUND = 10.0
#: How often the liveness loop looks, while any job is not terminal.
LIVENESS_INTERVAL = 5.0
#: How long a job whose root is gone may take to report before it is stopped and
#: barriered (above delegation's 30 s post-EOF bound, its drain and its grace).
EXIT_SETTLE_GRACE = 45.0
#: How long without activity before a job is flagged quiet (a flag only).
QUIET_AFTER = 600.0

_OUTBOX_BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)
_GENERIC_NOTICE = "The job ended with status {status}; its client could not render a result."


class JobStartRefused(Exception):
    """A job could not start: no slot, no such kind, or its ``start`` fact could not be written."""


class JobLaunchFailed(Exception):
    """The runner reported that its launch failed. Nothing is notified for it."""


class JobNotFound(Exception):
    pass


@dataclass
class _Job:
    job_id: str
    registration: Registration
    title: str
    owner: str
    session: Any
    notify: bool
    created: float
    started_at: float
    reservation: Reservation
    runner: JobRunner
    ctx: _Context | None = None
    task: asyncio.Task[None] | None = None
    state: JobState = "starting"
    processes: list[ev.ProcessRecord] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    stop_latched: bool = False
    stop_reason: StopReason | None = None
    stop_requested_by_service: str | None = None
    last_activity: float = 0.0
    quiet: bool = False
    progress: Mapping[str, Any] | None = None
    root_gone_at: float | None = None
    outcome: ClientOutcome | None = None
    status: str | None = None
    brief: str = ""
    elapsed: float | None = None
    unresolved_reason: str | None = None
    notice: Notice | None = None
    notice_state: Literal["none", "pending", "delivered"] = "none"
    notice_restored: bool = False
    outbox: dict[str, Any] | None = None
    outbox_attempt: int = 0
    outbox_due: float = 0.0
    fact_tasks: list[asyncio.Task[None]] = field(default_factory=list)
    launch: asyncio.Future[str | None] | None = None
    launch_error: str | None = None
    process_seq: int = 0
    finalized: bool = False
    # ``run`` has returned: the job is in its barrier, which the liveness loop
    # leaves to its own bounds (it only rescues a runner that never reports).
    run_returned: bool = False
    # What ``run`` ended with, kept while the job is ``unresolved``: a later
    # confirmation completes it with this, not with a made-up failure.
    run_outcome: ClientOutcome | None = None
    run_error: str | None = None
    launch_failed: bool = False
    # When the service cancels the runner itself, what the run counts as.
    service_outcome: ClientOutcome | None = None
    # The service's own escalation of a stop the runner did not honour (Codex #6).
    escalation: asyncio.Task[None] | None = None
    # A failed ``helper_cleanup`` is retried on this schedule (Codex #7).
    cleanup_attempt: int = 0
    cleanup_due: float = 0.0

    @property
    def adapter(self) -> JobKindAdapter:
        return self.registration.adapter

    @property
    def caps(self) -> KindCapabilities:
        return self.registration.adapter.capabilities


@dataclass
class _Restored:
    """A job this process did not run, as the owning session's facts tell it."""

    job_id: str
    kind: str
    label: str
    title: str
    owner: str
    created: float
    state: JobState
    brief: str = ""
    reason: str | None = None
    notice: Notice | None = None
    notice_state: Literal["none", "pending", "delivered"] = "none"
    folded: FoldedJob | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    # An ``unresolved`` restored job is an obligation (Codex #11): it holds a
    # slot of its kind until it is settled, and when its state came from OS
    # evidence the liveness loop classifies it again — report-only — so a
    # process that has since gone makes it ``lost``.
    session: Any = None
    reservation: Reservation | None = None
    recheck: bool = False
    argv_contains: tuple[str, ...] = ()


@dataclass
class _ExtensionNotice:
    """A notice an extension posted (``ExtensionAPI.post_notice``), kept until it is placed."""

    owner: str | None
    notice: Notice
    notice_state: Literal["pending", "delivered"] = "pending"


class _Context:
    """The :class:`~aelix_coding_agent.jobs.contract.JobContext` a runner is handed."""

    mode: Literal["background"] = "background"

    def __init__(self, service: JobService, job: _Job) -> None:
        self._service = service
        self._job = job
        self.job_id = job.job_id
        self.owner = job.owner
        self._sealed = False
        self._nonce = uuid.uuid4().hex[:8]

    def launched(self) -> None:
        self._service._launch_result(self._job, None)

    def launch_failed(self, error: BaseException | str) -> None:
        text = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        self._service._launch_result(self._job, text or "launch failed")

    def register_process(self, proc: Any, *, pgid: int | None = None, tree: Any = None) -> None:
        if self._sealed:
            raise RuntimeError("register_process after run returned: the job is sealed")
        pid = proc if isinstance(proc, int) else int(getattr(proc, "pid", 0) or 0)
        if pid <= 0:
            raise ValueError("register_process needs a pid")
        record = ev.ProcessRecord(
            pid=pid,
            started=process_started(pid),
            pgid=pgid,
            proc=None if isinstance(proc, int) else proc,
            tree=tree,
        )
        self._service._register_process(self._job, record)

    def register_directory(self, path: str) -> None:
        if self._sealed:
            raise RuntimeError("register_directory after run returned: the job is sealed")
        self._job.directories.append(str(path))

    @property
    def stop_latched(self) -> bool:
        return self._job.stop_latched

    def progress(self, snapshot: Mapping[str, Any] | None = None) -> None:
        self._service._activity(self._job, snapshot)

    def env_marker(self) -> dict[str, str]:
        return {"AELIX_JOB": f"{self.job_id}:{self._nonce}"}

    def protocol_env(self) -> dict[str, str]:
        return {"AELIX_JOBS_PROTOCOL": "1"}

    def _seal(self) -> None:
        self._sealed = True


class JobService:
    """Every background job of this process. See the module docstring."""

    def __init__(
        self,
        *,
        settings: Callable[[], Mapping[str, Any] | None] | None = None,
        warn: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        interval: float = LIVENESS_INTERVAL,
        exit_grace: float = EXIT_SETTLE_GRACE,
        quiet_after: float = QUIET_AFTER,
        cleanup_bound: float = CLEANUP_BOUND,
        stop_grace: float = ev.STOP_GRACE,
    ) -> None:
        self.registry = KindRegistry()
        self.admission = Admission(settings, warn=warn)
        self._clock = clock
        self._wall = wall
        self._interval = interval
        self._exit_grace = exit_grace
        self._quiet_after = quiet_after
        self._cleanup_bound = cleanup_bound
        self._stop_grace = stop_grace
        self._jobs: dict[str, _Job] = {}
        self._restored: dict[str, _Restored] = {}
        self._extension_notices: dict[str, _ExtensionNotice] = {}
        self._reconciled: set[str] = set()
        self._harness: AgentHarness | None = None
        self._session: Any = None
        self._owner: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._placed_listeners: list[Callable[[Notice], None]] = []
        #: What ``/jobs send`` calls when a host starts notice turns itself (the
        #: TUI's waker); ``None`` runs the turn here (:meth:`send_waiting`).
        self.send_hook: Callable[[], str] | None = None
        self._changed: asyncio.Event | None = None
        self._liveness: asyncio.Task[None] | None = None
        self._closed = False
        self.registry.add_listener(self._on_registration)

    # === binding ===========================================================

    @property
    def owner(self) -> str | None:
        return self._owner

    @property
    def harness(self) -> AgentHarness | None:
        return self._harness

    async def bind(self, harness: AgentHarness, *, reason: str = "startup") -> None:
        """The harness (and its session) is the current one now: post what this owner is owed.

        Called after every harness build — startup, ``/new``, ``/resume``,
        ``/fork``, ``/reload``. Notices of this owner's jobs that were posted
        to an earlier harness and not placed are posted again; the session's
        facts are folded once per owner and per process, and what they owe is
        posted as *restored* (a TUI does not start a turn for those).
        """

        self._harness = harness
        session = getattr(harness, "session", None)
        self._session = session
        self._owner = await _owner_of(session)
        for job in self._jobs.values():
            if (
                job.owner == self._owner
                and job.notice is not None
                and job.notice_state == "pending"
            ):
                self._post(job.notice, job_ref=job)
        for restored in self._restored.values():
            if (
                restored.owner == self._owner
                and restored.notice is not None
                and restored.notice_state == "pending"
            ):
                self._post(restored.notice, restored_ref=restored)
        for posted in self._extension_notices.values():
            if posted.owner == self._owner and posted.notice_state == "pending":
                self._post(posted.notice, extension_ref=posted)
        if self._owner is not None and self._owner not in self._reconciled and session is not None:
            self._reconciled.add(self._owner)
            await self.reconcile(session, self._owner)
        self._touch()

    async def owner_ended(self, owner: str | None, reason: StopReason) -> None:
        """Stop every live job of ``owner`` and wait for the barriers up to :data:`CLEANUP_BOUND`."""

        if owner is None:
            return
        jobs = [
            j for j in self._jobs.values() if j.owner == owner and j.state not in TERMINAL_STATES
        ]
        await self._stop_many(jobs, reason)

    async def shutdown(self) -> None:
        """Process exit: stop every job, wait the bound, cancel what is left."""

        self._closed = True
        jobs = [j for j in self._jobs.values() if j.state not in TERMINAL_STATES]
        await self._stop_many(jobs, "shutdown")
        left: list[asyncio.Task[None]] = []
        for job in self._jobs.values():
            if job.task is not None and not job.task.done():
                if not job.run_returned:
                    # Cancelled as the service's own stop, so the run still goes
                    # through its barrier (Codex #6): never a terminal state without it.
                    self._cancel_runner(job, "aelix is exiting", "stopped")
                left.append(job.task)
            if job.escalation is not None and not job.escalation.done():
                job.escalation.cancel()
        if left:
            await asyncio.wait(left, timeout=self._cleanup_bound)
            for task in left:
                if not task.done():
                    task.cancel()
        if self._liveness is not None:
            self._liveness.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._liveness
            self._liveness = None

    # === kinds ===============================================================

    def register_kind(
        self,
        adapter: JobKindAdapter,
        runner_factory: RunnerFactory,
        *,
        source: str = "",
        lifetime: Callable[[], None] | None = None,
    ) -> Registration:
        """Register ``adapter``'s kind for ``source`` (an extension's name; ``""`` is product-core).

        ``KindTaken`` when another source registered that name first.
        ``lifetime`` raises once the registering API's runtime has ended; the
        registration is then no longer offered for new jobs.
        """

        if not isinstance(adapter.capabilities, KindCapabilities):
            raise TypeError("a job kind's capabilities must be a KindCapabilities")
        return self.registry.register(adapter, runner_factory, source=source, lifetime=lifetime)

    def _on_registration(self, registration: Registration) -> None:
        """A kind registered again: re-evaluate the restored jobs it could not read."""

        stuck = [
            r
            for r in self._restored.values()
            if r.kind == registration.name and r.reason in ("adapter_missing", "adapter_too_old")
        ]
        if not stuck or self._session is None:
            return
        session, owner = self._session, self._owner

        async def again() -> None:
            for restored in stuck:
                if restored.folded is not None and restored.owner == owner:
                    await self._reconcile_one(session, restored.folded, entries=None)
            self._touch()

        with contextlib.suppress(RuntimeError):
            task = asyncio.get_running_loop().create_task(again())
            task.add_done_callback(_log_task_failure)

    # === starting =============================================================

    def reserve(self, kind: str, wanted: int = 1) -> Reservation:
        """Up to ``wanted`` slots of ``kind`` now, at least one, for a client that asks consent first.

        Hand the reservation to each :meth:`start` (``reservation=``), which
        takes its slot out of it; release what is left when the batch is done.
        :class:`JobStartRefused` when nothing is free or the kind is unknown.
        """

        registration = self.registry.get(kind)
        if registration is None:
            raise JobStartRefused(f"no job kind is registered under {kind!r}")
        adapter = registration.adapter
        try:
            return self.admission.reserve_up_to(
                adapter.name,
                adapter.capabilities.default_bound,
                wanted,
                settings_key=adapter.settings_key,
            )
        except AdmissionRefused as exc:
            raise JobStartRefused(str(exc)) from exc

    async def start(
        self,
        kind: str,
        *,
        title: str,
        params: Mapping[str, Any] | None = None,
        notify: bool | None = None,
        session: Any = None,
        reservation: Reservation | None = None,
    ) -> str:
        """Start a job of ``kind`` for the current session; its id once the runner reported its launch.

        Refused (:class:`JobStartRefused`) before anything runs when the kind
        is unknown, no slot is free, or the ``start`` fact cannot be confirmed.
        :class:`JobLaunchFailed` when the runner reported a failed launch: the
        job ends ``failed`` with no notice, and the error is the caller's to say.

        ``reservation`` is one the caller took before asking consent
        (:meth:`reserve`): the job's slot is taken out of it, so a consented
        start is never refused for want of a slot (Codex #16). Until the job
        exists and its finalizer owns the slot, every way out of this call — a
        refusal, a raise, a cancel — gives the slot back (Codex #15).
        """

        if self._closed:
            raise JobStartRefused("the job service is shutting down")
        registration = self.registry.get(kind)
        if registration is None:
            raise JobStartRefused(f"no job kind is registered under {kind!r}")
        adapter = registration.adapter
        caps = adapter.capabilities
        target = session if session is not None else self._session
        if target is None:
            raise JobStartRefused("there is no session to own the job")
        if reservation is not None:
            if (
                not reservation.belongs_to(self.admission)
                or reservation.key != adapter.name
                or reservation.released
            ):
                raise JobStartRefused("the reservation is not one for this kind, or it is spent")
            own = reservation.split(1)
        else:
            try:
                own = self.admission.reserve(
                    adapter.name, caps.default_bound, settings_key=adapter.settings_key
                )
            except AdmissionRefused as exc:
                raise JobStartRefused(str(exc)) from exc
        owned = False
        try:
            owner = await _owner_of(target) or ""
            job_id = uuid.uuid4().hex[:16]
            wants_notice = caps.notify if notify is None else (bool(notify) and caps.notify)
            created = self._wall()
            try:
                await append_fact(
                    target,
                    make_fact(
                        "start",
                        job_id,
                        kind=adapter.name,
                        kind_version=int(adapter.version),
                        owner=owner,
                        mode="background",
                        notify=wants_notice,
                        title=title,
                        created=created,
                        settings_key=adapter.settings_key,
                        supervisor=ev.supervisor_evidence(),
                    ),
                )
            except RecordWriteFailed as exc:
                raise JobStartRefused(f"the job's start could not be recorded: {exc}") from exc
            try:
                runner = registration.runner_factory(dict(params or {}))
            except Exception as exc:
                await self._write_quietly(
                    target,
                    make_fact(
                        "outcome",
                        job_id,
                        status="failed",
                        brief=f"client error: {type(exc).__name__}",
                        elapsed_ms=0,
                        client_records="missing",
                    ),
                )
                raise JobLaunchFailed(f"client error: {type(exc).__name__}: {exc}") from exc
            job = _Job(
                job_id=job_id,
                registration=registration,
                title=title,
                owner=owner,
                session=target,
                notify=wants_notice,
                created=created,
                started_at=self._clock(),
                reservation=own,
                runner=runner,
            )
            job.last_activity = job.started_at
            loop = asyncio.get_running_loop()
            job.launch = loop.create_future()
            job.ctx = _Context(self, job)
            self._jobs[job_id] = job
            job.task = loop.create_task(self._drive(job), name=f"aelix-job-{job_id}")
            # From here the job's finalizer owns the slot.
            owned = True
        finally:
            if not owned:
                own.release()
        self._ensure_liveness()
        self._touch()
        error = await asyncio.shield(job.launch)
        if error is not None:
            if job.task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(job.task)
            raise JobLaunchFailed(error)
        return job_id

    def _launch_result(self, job: _Job, error: str | None) -> None:
        if job.launch is None or job.launch.done():
            return
        if error is None:
            job.state = "running"
        else:
            job.launch_error = error
        job.launch.set_result(error)
        self._touch()

    def _register_process(self, job: _Job, record: ev.ProcessRecord) -> None:
        job.processes.append(record)
        if job.stop_latched:
            ev.kill_now(record)
        job.process_seq += 1
        fact = make_fact("process", job.job_id, seq=job.process_seq, evidence=record.evidence())
        with contextlib.suppress(RuntimeError):
            task = asyncio.get_running_loop().create_task(self._write_quietly(job.session, fact))
            job.fact_tasks.append(task)

    def _activity(self, job: _Job, snapshot: Mapping[str, Any] | None) -> None:
        job.last_activity = self._clock()
        if snapshot is not None:
            job.progress = dict(snapshot)
        if job.quiet:
            job.quiet = False
        self._touch()

    # === the run and its barrier ================================================

    async def _drive(self, job: _Job) -> None:
        ctx = job.ctx
        assert ctx is not None
        outcome: ClientOutcome | None = None
        client_error: str | None = None
        try:
            outcome = await job.runner.run(ctx)
            if not isinstance(outcome, ClientOutcome):
                outcome, client_error = None, "client error: run returned no outcome"
        except asyncio.CancelledError:
            if job.stop_requested_by_service is None:
                ctx._seal()
                self._launch_result(job, "the job was cancelled before it launched")
                raise
            if job.service_outcome is not None:
                outcome = job.service_outcome
            else:
                client_error = job.stop_requested_by_service
        except Exception as exc:  # noqa: BLE001 — a runner's raise is a failed outcome
            client_error = f"client error: {type(exc).__name__}"
            _log.warning("job %s: run raised", job.job_id, exc_info=True)
        ctx._seal()
        job.run_returned = True
        if job.launch is not None and not job.launch.done():
            self._launch_result(job, client_error or "the job ended before it reported its launch")
        await self._finalize(job, outcome, client_error)

    async def _finalize(
        self, job: _Job, outcome: ClientOutcome | None, client_error: str | None
    ) -> None:
        if job.fact_tasks:
            await asyncio.gather(*job.fact_tasks, return_exceptions=True)
        launch_failed = job.launch_error is not None
        policy = "stop" if job.stop_latched else job.caps.normal_exit
        if policy == "stop" and job.processes:
            # The kill legs first, so the runner settles pipes whose writers are
            # gone; the barrier then confirms what the legs did.
            await ev.stop_legs(job.processes, grace=self._stop_grace, clock=self._clock)
        settle_failed = False
        try:
            await asyncio.wait_for(job.runner.settle(), timeout=self._cleanup_bound)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a timeout too: never failed, stopped or lost
            settle_failed = True
            _log.warning("job %s: settle failed", job.job_id, exc_info=True)
        confirmed, why = await self._barrier(job, policy)
        helpers, helpers_failed = await self._helper_cleanup(job, policy, confirmed)
        job.elapsed = self._clock() - job.started_at
        job.run_outcome, job.run_error, job.launch_failed = outcome, client_error, launch_failed
        if settle_failed or not confirmed or helpers_failed:
            reason = (
                "settle_error"
                if settle_failed
                else (why or "not confirmed")
                if not confirmed
                else "cleanup_error"
            )
            await self._become_unresolved(job, reason, helpers)
            return
        await self._complete(job, outcome, client_error, helpers, launch_failed=launch_failed)

    async def _barrier(self, job: _Job, policy: str) -> tuple[bool, str | None]:
        if not job.processes:
            return True, None
        deadline = self._clock() + self._cleanup_bound
        if policy == "stop_descendants":
            # Roots first: a normal exit is the root's own; what is left in the
            # tree is stopped and confirmed inside the barrier (Codex r5 #2).
            ok, why = await ev.confirm_gone(
                job.processes, deadline, clock=self._clock, check_groups=False
            )
            if not ok:
                return ok, why
            ev.stop_descendants(job.processes)
        check_groups = policy != "release"
        return await ev.confirm_gone(
            job.processes, deadline, clock=self._clock, check_groups=check_groups
        )

    async def _helper_cleanup(
        self, job: _Job, policy: str, confirmed: bool
    ) -> tuple[HelperCleanup, bool]:
        """The runner's cleanup of what its processes started outside its tree; and whether it FAILED.

        A returned ``Unverified`` is the runner's honest answer (S1's POSIX
        limit): the job completes and its notice says so. A raise, a timeout or
        no answer is a cleanup that did not happen: the job stays
        ``unresolved(cleanup_error)``, holds its slot, and the duty is retried
        (contract §2.5, Codex #7).
        """

        receipt = {
            "job_id": job.job_id,
            "policy": policy,
            "confirmed": confirmed,
            "processes": [r.evidence() for r in job.processes],
        }
        try:
            result = await asyncio.wait_for(
                job.runner.helper_cleanup(receipt), timeout=self._cleanup_bound
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _log.warning("job %s: helper_cleanup failed", job.job_id, exc_info=True)
            return HelperCleanup("unverified", "helper_cleanup failed"), True
        if not isinstance(result, HelperCleanup):
            return HelperCleanup("unverified", "helper_cleanup returned no answer"), True
        return result, False

    async def _become_unresolved(
        self, job: _Job, reason: str, helpers: HelperCleanup | None
    ) -> None:
        job.state = "unresolved"
        job.unresolved_reason = reason
        if reason == "cleanup_error" and helpers is not None:
            await self._write_quietly(
                job.session,
                make_fact(
                    "cleanup",
                    job.job_id,
                    helpers=helpers.state,
                    **({"reason": helpers.reason} if helpers.reason else {}),
                ),
            )
            job.cleanup_due = self._clock() + _OUTBOX_BACKOFF[0]
        notice = None
        if job.notify and job.caps.notify:
            notice = self._frame_notice(
                job, "unresolved", f"{reason}", body=_unresolved_body(job, reason)
            )
        await self._write_quietly(
            job.session,
            make_fact(
                "supervision",
                job.job_id,
                state="unresolved",
                reason=reason,
                **({"notice": _notice_record(notice)} if notice is not None else {}),
            ),
        )
        if notice is not None and job.notice is None:
            job.notice = notice
            job.notice_state = "pending"
            self._post(notice, job_ref=job)
        self._ensure_liveness()
        self._touch()

    async def _complete(
        self,
        job: _Job,
        outcome: ClientOutcome | None,
        client_error: str | None,
        helpers: HelperCleanup,
        *,
        launch_failed: bool = False,
    ) -> None:
        if job.finalized:
            return
        job.finalized = True
        if client_error is not None or outcome is None:
            status = "failed"
            brief = client_error or "client error"
        else:
            status = outcome.status
            brief = outcome.brief
        job.status = status
        job.brief = brief
        job.outcome = outcome
        job.state = status  # type: ignore[assignment]
        job.unresolved_reason = None
        # Cleanup is confirmed: the slot goes back now, whatever the records do.
        job.reservation.release()
        client_ok = True
        if outcome is not None and not launch_failed:
            try:
                records = job.runner.records(outcome)
            except Exception:  # noqa: BLE001
                _log.warning("job %s: records() raised", job.job_id, exc_info=True)
                records, client_ok = [], False
            for custom_type, data in records or []:
                try:
                    await append_record(job.session, custom_type, {**data, "job_id": job.job_id})
                except RecordWriteFailed:
                    client_ok = False
        elif outcome is None:
            client_ok = False
        await self._write_quietly(
            job.session,
            make_fact(
                "cleanup",
                job.job_id,
                helpers=helpers.state,
                **({"reason": helpers.reason} if helpers.reason else {}),
            ),
        )
        notice = None
        if job.notify and job.caps.notify and not launch_failed:
            notice = self._frame_notice(job, status, brief, outcome=outcome, helpers=helpers)
        fact = make_fact(
            "outcome",
            job.job_id,
            status=status,
            brief=brief,
            elapsed_ms=int((job.elapsed or 0.0) * 1000),
            client_records="ok" if client_ok else "missing",
            **({"notice": _notice_record(notice)} if notice is not None else {}),
        )
        job.notice = notice
        try:
            await append_fact(job.session, fact)
        except RecordWriteFailed:
            _log.warning("job %s: outcome not written; retried by the liveness loop", job.job_id)
            job.outbox = fact
            job.outbox_attempt = 0
            job.outbox_due = self._clock() + _OUTBOX_BACKOFF[0]
            self._ensure_liveness()
            self._touch()
            return
        self._after_outcome(job)

    def _after_outcome(self, job: _Job) -> None:
        job.outbox = None
        for path in job.directories:
            shutil.rmtree(path, ignore_errors=True)
        if job.notice is not None:
            job.notice_state = "pending"
            self._post(job.notice, job_ref=job)
        self._touch()

    # === stopping ==============================================================

    def request_stop(self, job_id: str, reason: StopReason = "user") -> bool:
        """Ask a job of the current session to stop. :class:`JobNotFound` for another session's id."""

        job = self._jobs.get(job_id)
        if job is None or job.owner != self._owner:
            raise JobNotFound(job_id)
        return self._request_stop(job, reason)

    def _request_stop(self, job: _Job, reason: StopReason) -> bool:
        if job.state in TERMINAL_STATES:
            return False
        if job.state == "unresolved":
            # Its kill legs again, in the background (the liveness loop's work).
            with contextlib.suppress(RuntimeError):
                task = asyncio.get_running_loop().create_task(
                    self._recheck_unresolved(job, force=True)
                )
                task.add_done_callback(_log_task_failure)
            return True
        if not job.stop_latched:
            job.stop_latched = True
            job.stop_reason = reason
            if job.state == "running":
                job.state = "stopping"
            try:
                job.runner.request_stop(reason)
            except Exception:  # noqa: BLE001 — the service runs its own legs then
                _log.warning(
                    "job %s: request_stop raised; stopping it from its evidence",
                    job.job_id,
                    exc_info=True,
                )
                with contextlib.suppress(RuntimeError):
                    task = asyncio.get_running_loop().create_task(
                        ev.stop_legs(job.processes, grace=self._stop_grace, clock=self._clock)
                    )
                    task.add_done_callback(_log_task_failure)
            self._escalate_later(job)
            self._touch()
        return True

    def _escalate_later(self, job: _Job) -> None:
        if job.escalation is not None or job.run_returned:
            return
        with contextlib.suppress(RuntimeError):
            job.escalation = asyncio.get_running_loop().create_task(
                self._escalate(job), name=f"aelix-job-{job.job_id}-stop"
            )
            job.escalation.add_done_callback(_log_task_failure)

    async def _escalate(self, job: _Job) -> None:
        """A stop the runner did not honour within the grace is the service's to finish (Codex #6).

        After :data:`~aelix_coding_agent.jobs.evidence.STOP_GRACE` with ``run``
        still running, the service runs its own kill legs from the evidence it
        holds — whatever the runner does — and gives the runner the grace again
        to report; then it cancels ``run``. The cancelled run still goes through
        :meth:`_finalize`'s barrier, so the job ends ``stopped`` only once its
        processes are confirmed gone, and ``unresolved`` otherwise.
        """

        task = job.task
        if task is None:
            return
        done, _ = await asyncio.wait({task}, timeout=self._stop_grace)
        if done or job.run_returned:
            return
        await ev.stop_legs(job.processes, grace=self._stop_grace, clock=self._clock)
        done, _ = await asyncio.wait({task}, timeout=self._stop_grace)
        if done or job.run_returned:
            return
        self._cancel_runner(job, "its runner did not stop when asked", "stopped")

    def _cancel_runner(self, job: _Job, why: str, status: Literal["failed", "stopped"]) -> None:
        if job.task is None or job.task.done() or job.run_returned:
            return
        job.stop_latched = True
        if job.stop_requested_by_service is None:
            job.stop_requested_by_service = why
            if status == "stopped":
                job.service_outcome = ClientOutcome("stopped", why)
        job.task.cancel()

    async def stop(
        self, job_id: str, *, reason: StopReason = "user", wait: float | None = None
    ) -> JobSnapshot | None:
        """Ask a job of the current session to stop and wait for its barrier, up to ``wait`` seconds (default :data:`CLEANUP_BOUND`).

        An id of another session's job is :class:`JobNotFound`, as an unknown
        one is: holding an id is not authority over it (Codex #5).
        """

        job = self._jobs.get(job_id)
        if job is None or job.owner != self._owner:
            restored = self._restored.get(job_id)
            if job is None and restored is not None and restored.owner == self._owner:
                return self.snapshot(job_id)
            raise JobNotFound(job_id)
        self._request_stop(job, reason)
        if job.task is not None and not job.task.done():
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(
                    asyncio.shield(job.task), timeout=self._cleanup_bound if wait is None else wait
                )
        return self.snapshot(job_id)

    async def _stop_many(self, jobs: list[_Job], reason: StopReason) -> None:
        for job in jobs:
            self._request_stop(job, reason)
        tasks = [j.task for j in jobs if j.task is not None and not j.task.done()]
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=self._cleanup_bound)
            if pending:
                for job in jobs:
                    if job.task in pending and job.state not in TERMINAL_STATES:
                        job.state = "unresolved"
                        job.unresolved_reason = f"{reason}_timeout"
                self._ensure_liveness()
        self._touch()

    # === liveness ================================================================

    def _ensure_liveness(self) -> None:
        if self._liveness is not None and not self._liveness.done():
            return
        if not self._needs_liveness():
            return
        with contextlib.suppress(RuntimeError):
            self._liveness = asyncio.get_running_loop().create_task(
                self._liveness_loop(), name="aelix-jobs-liveness"
            )

    def _needs_liveness(self) -> bool:
        return any(
            j.state not in TERMINAL_STATES or j.outbox is not None for j in self._jobs.values()
        ) or any(r.state == "unresolved" and r.recheck for r in self._restored.values())

    async def _liveness_loop(self) -> None:
        while self._needs_liveness() and not self._closed:
            await asyncio.sleep(self._interval)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — the loop never dies of one job
                _log.warning("liveness tick raised", exc_info=True)

    async def tick(self) -> None:
        """One pass of the liveness loop (public for tests and for a host that drives it)."""

        now = self._clock()
        for job in list(self._jobs.values()):
            if job.state in ("starting", "running", "stopping"):
                if (
                    job.caps.activity == "progress"
                    and not job.quiet
                    and now - job.last_activity >= self._quiet_after
                ):
                    job.quiet = True
                self._check_root(job, now)
            elif job.state == "unresolved" and job.unresolved_reason != "settle_error":
                await self._recheck_unresolved(job)
            if job.outbox is not None and now >= job.outbox_due:
                await self._retry_outbox(job)
        for restored in list(self._restored.values()):
            if restored.state == "unresolved" and restored.recheck:
                await self._recheck_restored(restored)
        self._touch()

    def _check_root(self, job: _Job, now: float) -> None:
        if not job.processes or job.task is None or job.task.done() or job.run_returned:
            return
        if not all(r.root_gone() is True for r in job.processes):
            job.root_gone_at = None
            return
        if job.root_gone_at is None:
            job.root_gone_at = now
            return
        if now - job.root_gone_at < self._exit_grace or job.stop_requested_by_service is not None:
            return
        # The root is gone and the runner has not reported: latch, stop what is
        # left, cancel the runner, and let the barrier decide.
        job.stop_reason = "liveness"
        self._cancel_runner(job, "its process ended but its runner did not report", "failed")

    async def _recheck_unresolved(self, job: _Job, *, force: bool = False) -> None:
        """One more try at an ``unresolved`` job: its kill legs, its barrier, its cleanup duty.

        While its run has not returned, the service's legs run again from its
        evidence and the runner is escalated (Codex #6) — a ``/reload`` that
        timed out does not leave a live root to an uncooperative runner. Once
        confirmed gone, a failed ``helper_cleanup`` is retried on its schedule
        (Codex #7), and the job completes with what its run ended with.
        ``settle_error`` stays unresolved: never failed, stopped or lost.
        """

        if job.task is not None and not job.task.done():
            if not job.run_returned:
                await ev.stop_legs(job.processes, grace=0.0, clock=self._clock)
                self._escalate_later(job)
            return  # its own barrier is still running
        if job.unresolved_reason == "settle_error" and not force:
            return
        if job.unresolved_reason == "cleanup_error":
            if not force and self._clock() < job.cleanup_due:
                return
            helpers, failed = await self._helper_cleanup(job, "stop", True)
            if failed:
                job.cleanup_attempt += 1
                step = _OUTBOX_BACKOFF[min(job.cleanup_attempt, len(_OUTBOX_BACKOFF) - 1)]
                job.cleanup_due = self._clock() + step
                return
            await self._complete(
                job, job.run_outcome, job.run_error, helpers, launch_failed=job.launch_failed
            )
            return
        deadline = self._clock() + min(self._cleanup_bound, 2.0)
        await ev.stop_legs(job.processes, grace=0.0, clock=self._clock)
        ok, why = await ev.confirm_gone(job.processes, deadline, clock=self._clock)
        if not ok:
            job.unresolved_reason = job.unresolved_reason or why
            return
        if job.unresolved_reason == "settle_error":
            return  # never failed, stopped or lost: it stays unresolved
        helpers, failed = await self._helper_cleanup(job, "stop", True)
        if failed:
            await self._become_unresolved(job, "cleanup_error", helpers)
            return
        outcome, error = job.run_outcome, job.run_error
        if outcome is None and error is None:
            error = "stopped after it could not be confirmed"
        await self._complete(job, outcome, error, helpers, launch_failed=job.launch_failed)

    async def _retry_outbox(self, job: _Job) -> None:
        fact = job.outbox
        if fact is None:
            return
        try:
            await append_fact(job.session, fact)
        except RecordWriteFailed:
            job.outbox_attempt += 1
            if job.outbox_attempt >= len(_OUTBOX_BACKOFF):
                _log.warning(
                    "job %s: outcome still not written; its notice is not sent", job.job_id
                )
                job.outbox_due = self._clock() + _OUTBOX_BACKOFF[-1]
            else:
                job.outbox_due = self._clock() + _OUTBOX_BACKOFF[job.outbox_attempt]
            return
        self._after_outcome(job)

    # === notices =================================================================

    def _frame_notice(
        self,
        job: _Job,
        status: str,
        brief: str,
        *,
        outcome: ClientOutcome | None = None,
        helpers: HelperCleanup | None = None,
        body: str | None = None,
    ) -> Notice:
        adapter = self._adapter_now(job.registration)
        rendered: NoticeBody
        if body is not None:
            rendered = NoticeBody(body)
        elif outcome is None:
            rendered = NoticeBody(f"{brief}" if brief else _GENERIC_NOTICE.format(status=status))
        else:
            try:
                rendered = adapter.render_notice(outcome)
                if not isinstance(rendered, NoticeBody):
                    raise TypeError("render_notice returned no NoticeBody")
            except Exception:  # noqa: BLE001 — a generic body, never a lost notice
                _log.warning("job %s: render_notice failed", job.job_id, exc_info=True)
                rendered = NoticeBody(_GENERIC_NOTICE.format(status=status))
        text = rendered.body
        if helpers is not None and helpers.state == "unverified":
            text = f"{text}\n(Processes this job started outside its own tree were not confirmed stopped: {helpers.reason or 'unverified'}.)"
        notice_id = (
            nt.unresolved_notice_id(job.job_id)
            if status == "unresolved"
            else nt.final_notice_id(job.job_id)
        )
        framed = nt.frame(
            job_id=job.job_id,
            kind=adapter.name,
            label=adapter.label,
            title=job.title,
            status=status,
            elapsed=job.elapsed,
            body=text,
            next_action=rendered.next_action,
        )
        finalize = None
        if outcome is not None:
            finalize = self._placement_line(job.registration, outcome)
        return Notice(
            notice_id=notice_id,
            custom_type=NOTICE_CUSTOM_TYPE,
            body=framed,
            details={"v": 1, "job_id": job.job_id, "status": status},
            brief=nt.cut_brief(
                f'{adapter.label} "{job.title}" {status}' + (f": {brief}" if brief else "")
            ),
            finalize=finalize,
            finalize_max=nt.PLACEMENT_LINE_RESERVE if finalize is not None else 0,
        )

    def _adapter_now(self, registration: Registration) -> JobKindAdapter:
        current = self.registry.get(registration.name)
        if current is not None and current.version >= registration.version:
            return current.adapter
        return registration.adapter

    def _placement_line(
        self, registration: Registration, outcome: ClientOutcome
    ) -> Callable[[], Awaitable[str | None]]:
        async def line() -> str | None:
            adapter = self._adapter_now(registration)
            branch: list[Any] = []
            session = self._session
            if session is not None:
                with contextlib.suppress(Exception):
                    branch = list(await session.get_branch())
            try:
                return nt.cut_placement_line(await adapter.finalize_at_placement(outcome, branch))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — no extra line
                _log.warning("finalize_at_placement raised", exc_info=True)
                return None

        return line

    def _post(
        self,
        notice: Notice,
        *,
        job_ref: _Job | None = None,
        restored_ref: _Restored | None = None,
        extension_ref: _ExtensionNotice | None = None,
    ) -> bool:
        """Post ``notice`` to the current harness when its owner is the current one; whether it went.

        A notice of another session waits for its own (:meth:`bind` posts it
        again when that session is current), so a result never reaches a
        session that did not start its job (verify V15).
        """

        ref = job_ref or restored_ref or extension_ref
        owner = ref.owner if ref is not None else None
        harness = self._harness
        if harness is None or owner != self._owner or getattr(harness, "disposed", False):
            return False

        def placed(placed_notice: Notice, _entry_id: str) -> None:
            if job_ref is not None:
                job_ref.notice_state = "delivered"
            if restored_ref is not None:
                restored_ref.notice_state = "delivered"
            if extension_ref is not None:
                extension_ref.notice_state = "delivered"
            for listener in list(self._placed_listeners):
                try:
                    listener(placed_notice)
                except Exception:  # noqa: BLE001
                    _log.debug("placed listener raised", exc_info=True)
            self._touch()

        return harness.post_notice(notice, on_placed=placed)

    def post_extension_notice(
        self, source: str, notice_id: str, body: str, *, title: str = "", brief: str = ""
    ) -> bool:
        """``ExtensionAPI.post_notice``: a notice an extension leaves, framed like a job's.

        Its id is the extension's name and ``notice_id``, so two extensions
        cannot collide and one cannot pose as a job. ``False`` when there is no
        current harness or the id is already known there.
        """

        harness = self._harness
        if harness is None or getattr(harness, "disposed", False):
            return False
        key = nt.extension_notice_key(source, notice_id)
        if key in self._extension_notices:
            return False
        framed = nt.frame(
            job_id=key,
            kind=source or "extension",
            label="extension work",
            title=title or notice_id,
            status="done",
            elapsed=None,
            body=body,
            result_hint=False,
        )
        posted = _ExtensionNotice(
            owner=self._owner,
            notice=Notice(
                notice_id=key,
                custom_type=NOTICE_CUSTOM_TYPE,
                body=framed,
                details={"v": 1, "source": source},
                brief=nt.cut_brief(brief or title or notice_id),
            ),
        )
        if not self._post(posted.notice, extension_ref=posted):
            return False
        self._extension_notices[key] = posted
        self._touch()
        return True

    def extension_waiting(self) -> int:
        """Notices extensions posted for the current session that the model has not been given (verify S3)."""

        return sum(
            1
            for posted in self._extension_notices.values()
            if posted.owner == self._owner and posted.notice_state == "pending"
        )

    async def send_waiting(self) -> PromptNoticesResult:
        """``/jobs send``: a notice turn now, for what waits (restored results included)."""

        harness = self._harness
        if harness is None:
            return PromptNoticesResult(started=False, reason="nothing")
        return await harness.prompt_notices()

    def has_live_pending(self) -> bool:
        """Whether a live notice is posted and not yet placed (the TUI's wake).

        Live: a job this process ran, or an extension's ``post_notice`` (verify
        S3). A restored result is not live: it waits for ``/jobs send`` or the
        user's next message (owner OQ7).
        """

        return self.extension_waiting() > 0 or any(
            j.owner == self._owner and j.notice_state == "pending" and j.notice is not None
            for j in self._jobs.values()
        )

    # === restart: fold, recover, classify =======================================

    async def reconcile(self, session: Any, owner: str) -> None:
        """Fold ``session``'s facts and settle what this process did not run. Report-only."""

        try:
            entries = await session.get_entries()
        except Exception:  # noqa: BLE001
            _log.warning("could not read the session's job records", exc_info=True)
            return
        for folded in fold(entries).values():
            if folded.job_id in self._jobs:
                continue
            if folded.owner and folded.owner != owner:
                continue
            await self._reconcile_one(session, folded, entries=entries)
        self._touch()

    async def _reconcile_one(
        self, session: Any, folded: FoldedJob, *, entries: list[Any] | None
    ) -> None:
        start = folded.start or {}
        restored = self._restored.get(folded.job_id) or _Restored(
            job_id=folded.job_id,
            kind=folded.kind,
            label=folded.kind,
            title=str(start.get("title", "")),
            owner=folded.owner,
            created=float(start.get("created", 0.0) or 0.0),
            state="unresolved",
            folded=folded,
        )
        restored.session = session
        self._restored[folded.job_id] = restored
        if folded.unreadable:
            await self._restore_unresolved(restored, None, "unreadable")
            return
        registration, missing = self.registry.resolve(folded.kind, folded.kind_version)
        if registration is not None:
            restored.label = registration.adapter.label
        if entries is None:
            try:
                entries = await session.get_entries()
            except Exception:  # noqa: BLE001
                await self._restore_unresolved(restored, registration, "unreadable")
                return
        client = client_records_of(entries or [], folded.job_id)
        outcome = folded.outcome
        if outcome is not None:
            restored.state = _terminal(outcome.get("status"))
            if outcome.get("status") == "recovered":
                restored.state = "done"
            restored.brief = str(outcome.get("brief", ""))
            restored.reason = None
            self._settle_restored(restored)
            if registration is not None:
                # The kind's reading of its outcome, joined with its own records
                # (Codex #12), for ``/jobs result``; a failure keeps the stored
                # brief and body (ADR-0293's table).
                try:
                    decoded = registration.adapter.decode(
                        {"fact": dict(outcome), "records": list(client)}
                    )
                    restored.payload = dict(decoded.payload)
                except Exception:  # noqa: BLE001
                    _log.debug(
                        "decode of %s failed; the stored body stands", folded.job_id, exc_info=True
                    )
            notice = outcome.get("notice")
            if isinstance(notice, Mapping) and folded.notify:
                await self._restore_notice(session, restored, notice, outcome.get("status"))
            return
        if missing is not None or registration is None:
            await self._restore_unresolved(restored, None, missing or "adapter_missing")
            return
        adapter = registration.adapter
        recovered: Recovered | None = None
        try:
            recovered = await adapter.recover(dict(start), client)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            await self._restore_unresolved(restored, registration, "recover_error")
            return
        if recovered is not None and recovered.status not in ("unresolved",):
            status = recovered.status
            await self._record_restored(
                session, restored, registration, status, recovered.brief, recovered.payload
            )
            return
        try:
            relation = adapter.relation(dict(start))
            argv_contains = tuple(relation.argv_contains)
        except Exception:  # noqa: BLE001
            await self._restore_unresolved(restored, registration, "relation_error")
            return
        state, why = ev.classify_restart(
            list(folded.process_list()), start.get("supervisor"), argv_contains=argv_contains
        )
        if state == "lost":
            await self._record_restored(session, restored, registration, "lost", why, {})
        else:
            restored.argv_contains = argv_contains
            await self._restore_unresolved(restored, registration, why, recheck=True)

    async def _restore_unresolved(
        self,
        restored: _Restored,
        registration: Registration | None,
        reason: str,
        *,
        recheck: bool = False,
    ) -> None:
        """A restart's ``unresolved`` job: its slot held, its model notified, its evidence rechecked (Codex #11).

        Report-only, as every restart is: nothing is signalled. The slot is held
        under its kind (``Admission.hold``) until the job is settled, so a
        process still running from the last session counts against the bound.
        The notice — under the job's ``unresolved`` id, so a restart that finds
        it delivered posts nothing — is a restored one: it goes with the user's
        next message or ``/jobs send`` and never starts a TUI turn (owner OQ7).
        """

        restored.state, restored.reason = "unresolved", reason
        restored.recheck = recheck
        if restored.reservation is None:
            restored.reservation = self.admission.hold(restored.kind)
        folded = restored.folded
        notify = folded is not None and folded.notify
        if registration is not None:
            notify = notify and registration.adapter.capabilities.notify
        if notify and restored.notice is None and restored.session is not None:
            notice_id = nt.unresolved_notice_id(restored.job_id)
            if await delivered(restored.session, notice_id):
                restored.notice_state = "delivered"
            else:
                restored.notice = Notice(
                    notice_id=notice_id,
                    custom_type=NOTICE_CUSTOM_TYPE,
                    body=nt.frame(
                        job_id=restored.job_id,
                        kind=restored.kind,
                        label=restored.label,
                        title=restored.title,
                        status="unresolved",
                        elapsed=None,
                        body=_restored_unresolved_body(reason),
                    ),
                    details={"v": 1, "job_id": restored.job_id, "status": "unresolved"},
                    brief=nt.cut_brief(f'{restored.label} "{restored.title}" unresolved: {reason}'),
                )
                restored.notice_state = "pending"
                self._post(restored.notice, restored_ref=restored)
        self._ensure_liveness()

    def _settle_restored(self, restored: _Restored) -> None:
        restored.recheck = False
        if restored.reservation is not None:
            restored.reservation.release()
            restored.reservation = None

    async def _recheck_restored(self, restored: _Restored) -> None:
        """Classify a restored ``unresolved`` job again from OS evidence. Report-only: never a signal."""

        folded = restored.folded
        if folded is None or restored.session is None:
            return
        start = folded.start or {}
        state, why = ev.classify_restart(
            list(folded.process_list()),
            start.get("supervisor"),
            argv_contains=restored.argv_contains,
        )
        if state == "lost":
            registration = self.registry.get(restored.kind)
            await self._record_restored(restored.session, restored, registration, "lost", why, {})
        elif why != restored.reason:
            restored.reason = why

    async def _record_restored(
        self,
        session: Any,
        restored: _Restored,
        registration: Registration | None,
        status: str,
        brief: str,
        payload: Mapping[str, Any],
    ) -> None:
        adapter = registration.adapter if registration is not None else None
        notice = None
        folded = restored.folded
        notify = folded is not None and folded.notify
        if adapter is not None:
            notify = notify and adapter.capabilities.notify
        if notify:
            text, next_action = brief, None
            if adapter is not None and status in ("recovered", "done", "failed", "stopped"):
                try:
                    rendered = adapter.render_notice(
                        ClientOutcome(
                            status="done" if status == "recovered" else status,
                            brief=brief,
                            payload=payload,
                        )  # type: ignore[arg-type]
                    )
                    text = rendered.body
                    next_action = rendered.next_action
                except Exception:  # noqa: BLE001
                    text, next_action = _GENERIC_NOTICE.format(status=status), None
            cleanup = folded.cleanup if folded is not None else None
            if cleanup is None or cleanup.get("helpers") == "unverified":
                # Contract §2.3: no cleanup fact for a job that never ended, or an
                # unverified one — what it started outside its tree was not
                # confirmed stopped.
                text = f"{text}\n{_HELPERS_UNVERIFIED}"
            framed = nt.frame(
                job_id=restored.job_id,
                kind=restored.kind,
                label=restored.label,
                title=restored.title,
                status=status,
                elapsed=None,
                body=text,
                next_action=next_action,
            )
            notice = Notice(
                notice_id=nt.final_notice_id(restored.job_id),
                custom_type=NOTICE_CUSTOM_TYPE,
                body=framed,
                details={"v": 1, "job_id": restored.job_id, "status": status},
                brief=nt.cut_brief(f'{restored.label} "{restored.title}" {status}'),
            )
        fact = make_fact(
            "outcome",
            restored.job_id,
            status=status,
            brief=brief,
            elapsed_ms=0,
            client_records="missing",
            **({"notice": _notice_record(notice)} if notice is not None else {}),
        )
        try:
            await append_fact(session, fact)
        except RecordWriteFailed:
            restored.state, restored.reason = "unresolved", "record_failed"
            return
        restored.state = "done" if status == "recovered" else _terminal(status)
        restored.brief = brief
        restored.reason = None
        restored.payload = dict(payload)
        self._settle_restored(restored)
        if notice is not None:
            restored.notice = notice
            restored.notice_state = "pending"
            self._post(notice, restored_ref=restored)

    async def _restore_notice(
        self, session: Any, restored: _Restored, record: Mapping[str, Any], status: Any
    ) -> None:
        notice_id = record.get("id")
        body = record.get("body")
        if not isinstance(notice_id, str) or not isinstance(body, str):
            return
        if await delivered(session, notice_id):
            restored.notice_state = "delivered"
            return
        restored.notice = Notice(
            notice_id=notice_id,
            custom_type=NOTICE_CUSTOM_TYPE,
            body=body,
            details={"v": 1, "job_id": restored.job_id, "status": str(status)},
            brief=str(record.get("brief", "")),
        )
        restored.notice_state = "pending"
        self._post(restored.notice, restored_ref=restored)

    def restored_waiting(self) -> int:
        """Restored results of the current owner that are posted and not yet placed."""

        return sum(
            1
            for r in self._restored.values()
            if r.owner == self._owner and r.notice_state == "pending"
        )

    # === reading ==================================================================

    def snapshot(self, job_id: str, *, any_owner: bool = False) -> JobSnapshot | None:
        """One job of the current session by its exact id; ``None`` for an unknown id or another session's.

        Every surface reads through this (``/jobs``, the ``job`` tools, the
        extension facade), so an id seen in another session reads nothing here
        (Codex #5). ``any_owner`` is the service's own and its tests' view.
        """

        job = self._jobs.get(job_id)
        if job is not None:
            return self._snap(job) if any_owner or job.owner == self._owner else None
        restored = self._restored.get(job_id)
        if restored is None or not (any_owner or restored.owner == self._owner):
            return None
        return self._snap_restored(restored)

    def snapshots(self, owner: str | None = None) -> list[JobSnapshot]:
        """This owner's jobs (the current one by default), live first, oldest first."""

        who = self._owner if owner is None else owner
        live = [self._snap(j) for j in self._jobs.values() if j.owner == who]
        old = [self._snap_restored(r) for r in self._restored.values() if r.owner == who]
        return sorted(live, key=lambda s: s.created) + sorted(old, key=lambda s: s.created)

    def _snap(self, job: _Job) -> JobSnapshot:
        now = self._clock()
        terminal = job.state in TERMINAL_STATES
        return JobSnapshot(
            job_id=job.job_id,
            kind=job.adapter.name,
            label=self._adapter_now(job.registration).label,
            title=job.title,
            state=job.state,
            owner=job.owner,
            created=job.created,
            elapsed=job.elapsed if terminal else now - job.started_at,
            last_activity_ago=None if terminal else now - job.last_activity,
            quiet=job.quiet and not terminal,
            result_waiting=job.notice_state == "pending",
            notice_state=job.notice_state,
            brief=job.brief,
            progress=job.progress,
            unresolved_reason=job.unresolved_reason,
            payload=dict(job.outcome.payload) if job.outcome is not None else {},
        )

    def _snap_restored(self, restored: _Restored) -> JobSnapshot:
        return JobSnapshot(
            job_id=restored.job_id,
            kind=restored.kind,
            label=restored.label,
            title=restored.title,
            state=restored.state,
            owner=restored.owner,
            created=restored.created,
            elapsed=None,
            last_activity_ago=None,
            quiet=False,
            result_waiting=restored.notice_state == "pending",
            notice_state=restored.notice_state,
            brief=restored.brief,
            unresolved_reason=restored.reason,
            payload=dict(restored.payload),
        )

    def unsettled(self, owner: str | None = None) -> bool:
        """Whether ``-p`` must wait: a job of a kind that blocks exit that is live, or ended with its notice not yet posted.

        A job leaves this only once its notice is posted (or it has none), so a
        drain that checks jobs before the inbox cannot miss a result (ADR-0293).
        """

        who = self._owner if owner is None else owner
        for job in self._jobs.values():
            if job.owner != who or not job.caps.blocks_print_exit:
                continue
            if job.state not in TERMINAL_STATES and job.state != "unresolved":
                return True
            if job.outbox is not None:
                return True
            if (
                job.state in TERMINAL_STATES
                and job.notice is not None
                and job.notice_state == "none"
            ):
                return True
        return False

    def output(self, job_id: str, *, offset: int = 0, limit: int = 16 * 1024) -> OutputSlice | str:
        """The kind's output for ``job_id``, or a sentence saying why there is none."""

        snap = self.snapshot(job_id)
        if snap is None:
            return f"no job {job_id}"
        registration = self.registry.get(snap.kind)
        if registration is None:
            return f"the {snap.kind} kind is not registered in this session"
        if not registration.adapter.capabilities.output:
            return "this kind of job has no output to read"
        try:
            result = registration.adapter.output(snap, offset=offset, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return f"could not read the result: {type(exc).__name__}"
        if result is None:
            return snap.brief or "no output was recorded"
        return result

    def render_rows(self, snap: JobSnapshot, width: int) -> list[str]:
        registration = self.registry.get(snap.kind)
        if registration is None:
            return [f"{snap.kind} · {snap.title}"]
        try:
            rows = registration.adapter.render_rows(snap, width)
            if isinstance(rows, list) and all(isinstance(r, str) for r in rows):
                return rows
        except Exception:  # noqa: BLE001 — one generic row
            _log.debug("render_rows raised", exc_info=True)
        return [f"{snap.label} · {snap.title}"]

    # === change notification ========================================================

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def add_placed_listener(self, listener: Callable[[Notice], None]) -> Callable[[], None]:
        """Call ``listener(notice)`` when a job's notice is placed (a surface shows a line for it)."""

        self._placed_listeners.append(listener)

        def remove() -> None:
            if listener in self._placed_listeners:
                self._placed_listeners.remove(listener)

        return remove

    async def changed(self) -> None:
        if self._changed is None:
            self._changed = asyncio.Event()
        await self._changed.wait()

    def _touch(self) -> None:
        event, self._changed = self._changed, None
        if event is not None:
            event.set()
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001
                _log.debug("job listener raised", exc_info=True)

    async def _write_quietly(self, session: Any, fact: dict[str, Any]) -> None:
        try:
            await append_fact(session, fact)
        except RecordWriteFailed:
            _log.warning("job fact %s not written", fact.get("fact"))


def _terminal(status: Any) -> JobState:
    return status if status in TERMINAL_STATES else "unresolved"  # type: ignore[return-value]


def _notice_record(notice: Notice | None) -> dict[str, Any]:
    assert notice is not None
    return {"id": notice.notice_id, "body": notice.body, "brief": notice.brief}


_HELPERS_UNVERIFIED = (
    "(Processes this job started outside its own tree were not confirmed stopped.)"
)


def _restored_unresolved_body(reason: str) -> str:
    return (
        "aelix restarted and found this job started and not ended; it could not tell "
        f"how it ended ({reason}). aelix stops nothing it finds at a restart; it keeps "
        "checking, and /jobs shows its state."
    )


def _unresolved_body(job: _Job, reason: str) -> str:
    pids = ", ".join(str(r.pid) for r in job.processes) or "none recorded"
    if reason == "cleanup_error":
        return (
            "The job's run ended, but its cleanup of what it started outside its own "
            "process tree failed: those processes were not confirmed stopped. aelix "
            f"retries the cleanup; /jobs shows its state. Recorded pids: {pids}."
        )
    return (
        f"aelix could not confirm that this job's processes stopped ({reason}). "
        f"Recorded pids: {pids}. It keeps checking; /jobs shows its state."
    )


async def _owner_of(session: Any) -> str | None:
    if session is None:
        return None
    try:
        meta = await session.get_metadata()
    except Exception:  # noqa: BLE001
        return None
    return str(getattr(meta, "id", "") or "") or None


def _log_task_failure(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _log.warning("job service task failed: %r", exc)


__all__ = [
    "CLEANUP_BOUND",
    "EXIT_SETTLE_GRACE",
    "LIVENESS_INTERVAL",
    "QUIET_AFTER",
    "JobLaunchFailed",
    "JobNotFound",
    "JobService",
    "JobStartRefused",
]
