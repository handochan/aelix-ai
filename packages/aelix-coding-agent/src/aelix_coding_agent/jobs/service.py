"""The job service: one per process, owning every background job its sessions start (#722, ADR-0293).

**What it owns.** For every job: its state, its runner's task, the processes and
directories it registered, its admission slot, its facts in the owning session,
its notice, and the liveness of all of these. Clients (delegation, background
``bash``, an extension's kind) own only what their runner does and how their
records read (:mod:`aelix_coding_agent.jobs.contract`). Nothing here names a
kind: labels, settings keys and hints are the strings a client registered.

**States.** ``starting`` → ``running`` → (``stopping``) → ``done`` | ``failed``
| ``stopped``; ``lost`` and ``recovered`` only from a restart — ``recovered`` is
what a kind read of a job's end that nothing confirmed, recorded once every
process the job recorded is confirmed gone (#611 S1c); ``unresolved`` when the
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
What a kind can only READ of a job's end — no confirmed end backs it — is
recorded ``recovered``, never ``done``, and only once every process the job
recorded is confirmed gone; a kind's own ``unresolved`` keeps its reason, and
OS evidence never makes it ``lost`` (#611 S1c, ADR-0295). Nothing found at
start-up is ever signalled. One that stays ``unresolved`` holds a slot of its
kind while a recorded process may live, leaves the model a restored notice,
and — when OS evidence decided it — is classified again by the liveness loop
(Codex #11), which asks its kind again once the evidence says gone.

**Ownership** (Codex #5). Every reading and control path answers only for the
current session's jobs: an id of another session's job is not found.

**Attention** (#123 D1, the lead's design). A running job may post ONE notice
through :meth:`JobContext.attention` — a kind's "this job may need you" — with
id ``<job>:attention`` and ``details.status == "running"``. It is kept in a
record of its own (:class:`_Attention`), never in the job's ``notice``,
``notice_state``, ``owed`` or ``placed``: it is not a result, so nothing that
counts results or delivery counts it. The TUI's wake does
(:meth:`JobService.has_live_pending`). A rebind posts it again only while the
job still runs, and drops it otherwise. No fact is written for it.

**Retention** (#769, the lead's policy). A session's ended jobs are kept whole
up to :data:`SETTLED_KEPT`; past it the oldest that owes nothing — ended, its
outcome written, every notice of it placed or never owed — of a kind that
declares ``reads_persisted`` gives up its runner, context, task and evidence,
and keeps only what its session's records say of it, read back from them (its
facts, and its kind's records as its kind decodes them, as a restart would),
once its snapshot, its rows and its first page read the same from them.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from aelix_agent_core.harness.inbox import Notice, PromptNoticesResult
from aelix_ai.utils._process_identity import process_started

from aelix_coding_agent.jobs import evidence as ev
from aelix_coding_agent.jobs import notice as nt
from aelix_coding_agent.jobs.admission import Admission, AdmissionRefused, Reservation
from aelix_coding_agent.jobs.contract import (
    JOB_MARKER_ENV,
    JOBS_PROTOCOL_ENV,
    JOBS_PROTOCOL_VERSION,
    TERMINAL_STATES,
    ClientOutcome,
    HelperCleanup,
    JobControlAdapter,
    JobEndedControlAdapter,
    JobEndedMessageAdapter,
    JobIdAdapter,
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
from aelix_coding_agent.jobs.detach import DetachOffers
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
#: How many ended jobs of one session the service keeps whole (#769); past it
#: the oldest that owes nothing is kept only as its records read.
SETTLED_KEPT = 64

_OUTBOX_BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)
_GENERIC_NOTICE = "The job ended with status {status}; its client could not render a result."


SESSION_ENDED_FIRST = "the session ended before the job started"
"""A start refused because its owner ended, or the service shut down, while it was being made (#612)."""


class JobStartRefused(Exception):
    """A job could not start: no slot, no such kind, or its ``start`` fact could not be written."""


class JobLaunchFailed(Exception):
    """The runner reported that its launch failed. Nothing is notified for it."""


CONTROL_WORDS: Mapping[str, str] = {"steer": "a message", "wrap_up": "a wrap-up"}
"""What a refusal calls the two control verbs the ``/jobs`` surfaces send (#123 fix
round 1, V7): a human reads "a wrap-up", never ``wrap_up``. A verb with no entry is
shown as the kind wrote it."""


def control_refusal(caps: KindCapabilities, verb: str) -> str:
    """The sentence for ``verb`` on a kind that does not declare it, then the kind's ``control_hint``.

    One place for it: :meth:`JobService.control` answers with it, and the
    ``/jobs`` panel's ``m`` shows it as its note in place of a ``/jobs steer``
    line the job could not take.
    """

    hint = caps.control_hint
    return f"this kind of job does not take {CONTROL_WORDS.get(verb, verb)}" + (f"; {hint}" if hint else "")


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
    # What the kind handed ``start`` as ``client_start`` (#611 S2d), as recorded.
    client_start: Mapping[str, Any] = field(default_factory=dict)
    # The barrier confirmed every registered process gone (#749:
    # ``JobSnapshot.processes_gone``).
    confirmed: bool = False
    # Its place in start order, which breaks a tie of ``created`` (#769).
    seq: int = 0
    # Every notice of it waiting in the current harness's inbox, by id — its
    # ``unresolved`` one as well as its final one (#769: none may wait when it
    # is let go).
    owed: set[str] = field(default_factory=set)
    # Every notice of it placed, by id: its current one is delivered once it is
    # among them and nothing it owes waits, in whatever order they were placed.
    placed: set[str] = field(default_factory=set)
    # Its one attention notice (#123 D1), apart from ``notice``, ``owed`` and
    # ``placed``: never its result.
    attention: _Attention | None = None
    # ``_complete`` made it terminal, or ``_become_unresolved`` unresolved, and
    # has not returned: its records and facts are still being written, its
    # notice not yet posted (``unsettled`` holds ``-p`` through it; #123 fix r2).
    completing: bool = False

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
    # The run's time its outcome fact recorded (``elapsed_ms``), ``None`` when it
    # recorded none: ``0`` is what an outcome written with no measured run holds.
    elapsed: float | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    # An ``unresolved`` restored job is an obligation (Codex #11): it holds a
    # slot of its kind until it is settled, and while ``recheck`` is set the
    # liveness loop classifies it again — report-only — and once its processes
    # are gone asks its kind again (``_recheck_restored``): ``recovered``, the
    # kind's own ``unresolved``, or ``lost`` when the kind reads nothing or is
    # not registered.
    session: Any = None
    reservation: Reservation | None = None
    recheck: bool = False
    argv_contains: tuple[str, ...] = ()
    # The kind's own ``unresolved`` reason (#611 S1c): a recheck's OS evidence
    # never replaces it.
    kind_reason: bool = False
    # The start fact's ``client`` (#611 S2d, Codex #12): the kind's own launch
    # data, read back as recorded rather than derived again in this process.
    client_start: Mapping[str, Any] = field(default_factory=dict)
    # The last OS evidence said every recorded process is gone (#749:
    # ``JobSnapshot.processes_gone``); ``False`` too while nothing was read.
    gone: bool = False
    # Failed reads of its records since its processes were found gone.
    read_failures: int = 0


@dataclass(frozen=True)
class _Settled:
    """An ended job this process ran, once it is no longer kept whole (#769).

    Read back from its session's records — its ``start`` and ``outcome`` facts,
    whether its notices were placed, and its kind's records as its kind decodes
    them — with the two small values no record holds, its ``elapsed`` and last
    ``progress``, kept as they were. No runner, context, task, evidence or
    registration: its kind is held by name, version and label, so a ``/reload``
    leaves no old generation alive through it.
    """

    job_id: str
    seq: int
    kind: str
    version: int
    label: str
    title: str
    owner: str
    created: float
    state: JobState
    brief: str
    elapsed: float | None
    progress: Mapping[str, Any] | None
    notice_state: Literal["none", "delivered"]
    payload: Mapping[str, Any]
    client_start: Mapping[str, Any]


@dataclass
class _ExtensionNotice:
    """A notice an extension posted (``ExtensionAPI.post_notice``), kept until it is placed."""

    owner: str | None
    notice: Notice
    notice_state: Literal["pending", "delivered"] = "pending"


@dataclass
class _Attention:
    """A running job's one attention notice (#123 D1), kept apart from its final one.

    ``pending`` while posted (or waiting for its session's :meth:`JobService.bind`)
    and not placed; ``delivered`` once placed; ``dropped`` when a rebind found
    its job no longer running, so it was posted nowhere again.
    """

    owner: str
    notice: Notice
    state: Literal["pending", "delivered", "dropped"] = "pending"


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

    def register_process(
        self, proc: Any, *, pgid: int | None = None, tree: Any = None
    ) -> ev.ProcessRecord:
        """Register ``proc``; the record the barrier will read is returned.

        The SAME record, not a copy (#611 S1c): a client that has to kill
        before its ``run`` returns — an eager path — signals through
        :mod:`~aelix_coding_agent.jobs.evidence` on it, so the evidence those
        legs collect is the evidence the barrier confirms against.
        """

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
        return record

    def register_directory(self, path: str) -> None:
        if self._sealed:
            raise RuntimeError("register_directory after run returned: the job is sealed")
        self._job.directories.append(str(path))

    @property
    def stop_latched(self) -> bool:
        return self._job.stop_latched

    def progress(self, snapshot: Mapping[str, Any] | None = None) -> None:
        self._service._activity(self._job, snapshot)

    def attention(self, body: NoticeBody, *, brief: str = "") -> bool:
        """:meth:`JobContext.attention`: once per job, only while it runs (#123 D1)."""

        return self._service._attention(self._job, body, brief)

    def env_marker(self) -> dict[str, str]:
        return {JOB_MARKER_ENV: f"{self.job_id}:{self._nonce}"}

    def protocol_env(self) -> dict[str, str]:
        return {JOBS_PROTOCOL_ENV: JOBS_PROTOCOL_VERSION}

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
        settled_kept: int = SETTLED_KEPT,
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
        self._settled_kept = max(0, int(settled_kept))
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
        #: What a bare ``/jobs`` opens when a host has a panel (the TUI's, #776);
        #: ``None`` prints the table.
        self.panel_hook: Callable[[], Awaitable[str | None]] | None = None
        self._changed: asyncio.Event | None = None
        self._liveness: asyncio.Task[None] | None = None
        self._settled: dict[str, _Settled] = {}
        self._started = 0
        self._trimming: asyncio.Task[None] | None = None
        self._trim_again = False
        self._closed = False
        #: #612 (contract r5 §6.4; Codex r4-2 7): a counter :meth:`owner_ended` raises,
        #: and the value it raised to for each owner it ended. Never emptied: a start
        #: that read an older value refuses for an owner ended since, however late it
        #: arrives, and a ``/reload`` of the same owner raises it again.
        self._end_epoch = 0
        self._ended_at: dict[str, int] = {}
        #: The epoch when the current harness was bound: Ctrl+B's adoption door
        #: refuses an owner ended since (:meth:`prepare_adopted`).
        self._bound_epoch = 0
        #: The running foreground calls Ctrl+B could move into a job (#775).
        self.detach = DetachOffers()
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
        ``/fork``, ``/reload``; the CLI binds a rebuilt harness before its
        ``session_start`` handlers run (#612 fix round 2), so a job one of them
        starts is the new session's. Notices of this owner's jobs that were posted
        to an earlier harness and not placed are posted again; the session's
        facts are folded once per owner and per process, and what they owe is
        posted as *restored* (a TUI does not start a turn for those).
        """

        self._harness = harness
        self._bound_epoch = self._end_epoch
        session = getattr(harness, "session", None)
        self._session = session
        self._owner = await _owner_of(session)
        for job in self._jobs.values():
            # What waited in the harness this one replaces is not waiting
            # anywhere now; what is posted again below waits here (#769).
            job.owed.clear()
            if (
                job.owner == self._owner
                and job.notice is not None
                and job.notice_state == "pending"
            ):
                self._post(job.notice, job_ref=job)
            self._repost_attention(job)
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

    def _binding_ended(self) -> bool:
        """The bound session's owner ended since the bind: its jobs were stopped, and no new bind came yet."""

        return self._ended_at.get(self._owner or "", 0) > self._bound_epoch

    async def owner_ended(self, owner: str | None, reason: StopReason) -> None:
        """Stop every live job of ``owner`` and wait for the barriers up to :data:`CLEANUP_BOUND`.

        First, synchronously, the owner's ended epoch is raised (#612, contract r5
        §6.4): a :meth:`start` already past its entry for this owner — parked in its
        fact append — refuses at registration instead of registering a job after the
        snapshot below was taken (Codex r4-2 7 measured one registered at the base).
        """

        if owner is None:
            return
        self._end_epoch += 1
        self._ended_at[owner] = self._end_epoch
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
        await _cancel_quietly(self._liveness)
        await _cancel_quietly(self._trimming)
        self._liveness = self._trimming = None

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
                noun=adapter.capabilities.noun,
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
        client_start: Mapping[str, Any] | None = None,
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

        ``client_start`` is the kind's own launch data (#611 S2d, Codex #12),
        opaque here: it is written into the confirmed ``start`` fact as
        ``client`` and handed back on every snapshot of the job
        (``JobSnapshot.client_start``), a restart's included, and to the
        kind's ``restored_note``.

        A start whose owner ended after it began (:meth:`owner_ended`), or that
        the service's :meth:`shutdown` overtook, is refused at registration with
        its outcome fact ``failed`` (#612, contract r5 §6.4): nothing awaits
        between that check and the job's insertion. A start for the bound
        session once its owner ended — a ``session_shutdown`` hook that runs
        after this service's own — is refused at entry, before anything is
        written, until the next :meth:`bind` (#612 fix round 1, Codex C-H1).
        """

        epoch = self._end_epoch
        if self._closed:
            raise JobStartRefused("the job service is shutting down")
        if (session is None or session is self._session) and self._binding_ended():
            raise JobStartRefused(SESSION_ENDED_FIRST)
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
                    adapter.name,
                    caps.default_bound,
                    settings_key=adapter.settings_key,
                    noun=caps.noun,
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
                        **({"client": dict(client_start)} if client_start is not None else {}),
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
            if self._closed or self._ended_at.get(owner, 0) > epoch:
                await self._write_quietly(
                    target,
                    make_fact(
                        "outcome",
                        job_id,
                        status="failed",
                        brief=SESSION_ENDED_FIRST,
                        elapsed_ms=0,
                        client_records="missing",
                    ),
                )
                raise JobStartRefused(SESSION_ENDED_FIRST)
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
                client_start=dict(client_start or {}),
            )
            self._started += 1
            job.seq = self._started
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

    # === adoption: a running foreground call moved into a job (#775, ADR-0298) ===

    def prepare_adopted(
        self,
        kind: str,
        *,
        title: str,
        runner: JobRunner,
        session: Any,
        client_start: Mapping[str, Any],
        process: ev.ProcessRecord,
        ran_for: float = 0.0,
        started_wall: float | None = None,
    ) -> _Job:
        """A job for a call Ctrl+B is moving, built and NOT yet inserted; nothing changes here.

        Refused (:class:`JobStartRefused`) when the service is closing, the
        kind is unknown or not ``detachable``, or ``session`` is not the one
        the service serves now. The job is ``running`` from the start: its
        process exists and was recorded at its spawn (``process``, never sampled
        again). Its ``start`` and ``process`` facts are written by its own task
        (:meth:`run_adopted`), and its slot is handed over last
        (:meth:`give_slot`).

        ``ran_for`` is how many seconds the call had run in the foreground (#821).
        The job's clock and its ``created`` start that long before the move, so
        its attention and finished notices, its snapshot's ``elapsed`` (the kind's
        status rows, ``/jobs``) and ``/jobs <id>``'s "started" count the run from
        its start; they counted from the move, and a run moved while stuck read
        "is still running after 0s". Its last activity is still the move
        (:meth:`adopt`): what it did before was watched by the foreground call.
        ``started_wall`` is the wall clock the call's start read, its ``created``
        when given: ``ran_for`` back from the move's wall time is the start only
        while the wall clock did not change in between (Codex review of #821, L11).
        """

        if self._closed or self._binding_ended():
            # #612 (contract r5 §6.4): or the session this harness serves ended
            # since it was bound, and its jobs were already stopped.
            raise JobStartRefused(
                "the job service is shutting down" if self._closed else SESSION_ENDED_FIRST
            )
        registration = self.registry.get(kind)
        if registration is None or not registration.adapter.capabilities.detachable:
            raise JobStartRefused(f"no detachable job kind is registered under {kind!r}")
        if session is None or session is not self._session or self._owner is None:
            raise JobStartRefused("the call's session is not the one the job service serves")
        ran = max(0.0, ran_for)
        job = _Job(
            job_id=uuid.uuid4().hex[:16],
            registration=registration,
            title=title,
            owner=self._owner,
            session=session,
            notify=registration.adapter.capabilities.notify,
            created=started_wall if started_wall is not None else self._wall() - ran,
            started_at=self._clock() - ran,
            # Spent until :meth:`give_slot` moves the call's slot in.
            reservation=Reservation(self.admission, registration.name, 0),
            runner=runner,
            client_start=dict(client_start),
            state="running",
        )
        job.processes.append(process)
        job.ctx = _Context(self, job)
        return job

    def adopt(self, job: _Job, task: asyncio.Task[Any]) -> None:
        """Publish a prepared job, its task created (gated) by the commit. Synchronous; undone by :meth:`unadopt`.

        SILENT: no listener runs here. A listener is outside code, and one run
        while the commit is half done could change what the commit checked
        (Codex r1 3); the commit calls :meth:`announce_adopted` once it is whole.
        """

        job.task = task
        job.last_activity = self._clock()  # the move, not the run's start (#821)
        self._started += 1
        job.seq = self._started
        self._jobs[job.job_id] = job

    def unadopt(self, job: _Job) -> None:
        """Undo :meth:`adopt`, silently, its place in start order given back."""

        if self._jobs.get(job.job_id) is job:
            del self._jobs[job.job_id]
            # Its place in start order goes back when no job started after it.
            self._started = job.seq - 1 if self._started == job.seq else self._started

    def announce_adopted(self, job: _Job) -> None:
        """The commit that adopted ``job`` is whole: its listeners hear of it now."""

        self._touch()

    def give_slot(self, job: _Job, reservation: Reservation) -> None:
        """The call's one slot moves into the job, the count unchanged (``Reservation.split``). The commit's last step."""

        job.reservation = reservation.split(1)

    async def run_adopted(self, job: _Job) -> None:
        """The adopted job's task: its facts first, then the run and its barrier as any job's.

        The ``start`` fact carries the client's linkage (``client``); written
        quietly, as a fact task ``_finalize`` waits for, so a start that never
        lands leaves a restart reading the run as a foreground one (a stated
        limit, ADR-0298).
        """

        job.fact_tasks.append(asyncio.ensure_future(self._adopted_facts(job)))
        self._ensure_liveness()
        await self._drive(job)

    async def _adopted_facts(self, job: _Job) -> None:
        await self._write_quietly(
            job.session,
            make_fact(
                "start",
                job.job_id,
                kind=job.registration.name,
                kind_version=int(job.adapter.version),
                owner=job.owner,
                mode="background",
                notify=job.notify,
                title=job.title,
                created=job.created,
                settings_key=job.adapter.settings_key,
                supervisor=ev.supervisor_evidence(),
                client=dict(job.client_start),
            ),
        )
        for record in list(job.processes):
            job.process_seq += 1
            await self._write_quietly(
                job.session,
                make_fact("process", job.job_id, seq=job.process_seq, evidence=record.evidence()),
            )

    def rescue_adopted(self, job: _Job, why: str) -> None:
        """The adopted job's first task ended before its runner took the run: stop it through :meth:`_drive`.

        That task was cancelled before its first step (Codex r3 M2), or a
        start step raised before the runner drove the run (Codex r1 2). A new
        task runs :meth:`_drive_rescued`, whose runner throws the cancel into
        the moved run; ``_drive`` takes it as the service's own stop, so the
        job goes through its barrier and ends ``stopped`` with its slot freed.
        Raises when even that task cannot be created (a stated limit).
        """

        job.stop_latched = True
        if job.stop_requested_by_service is None:
            job.stop_requested_by_service = why
            job.service_outcome = ClientOutcome("stopped", why)
        job.task = asyncio.get_running_loop().create_task(
            self._drive_rescued(job), name=f"aelix-job-{job.job_id}-rescue"
        )

    async def _drive_rescued(self, job: _Job) -> None:
        """The rescue's task: the start steps that are missing, best effort, then :meth:`_drive` regardless.

        A start step that failed once is not allowed to strand the run again:
        its facts and the liveness loop are tried, a failure is logged, and the
        barrier still runs.
        """

        try:
            if not job.fact_tasks:
                job.fact_tasks.append(asyncio.ensure_future(self._adopted_facts(job)))
            self._ensure_liveness()
        except Exception:  # noqa: BLE001 — the run must still be stopped and its slot freed
            _log.warning("job %s: a start step failed again in its rescue", job.job_id, exc_info=True)
        await self._drive(job)

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
        job.confirmed = confirmed
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
        job.completing = True  # its notice is not posted yet (#123 fix r2)
        job.state = "unresolved"
        try:
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
        finally:
            job.completing = False
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
        job.completing = True
        job.state = status  # type: ignore[assignment]
        try:
            await self._complete_terminal(
                job, status, brief, outcome, client_error, helpers, launch_failed
            )
        finally:
            job.completing = False
            self._touch()

    async def _complete_terminal(
        self,
        job: _Job,
        status: str,
        brief: str,
        outcome: ClientOutcome | None,
        client_error: str | None,
        helpers: HelperCleanup,
        launch_failed: bool,
    ) -> None:
        """The rest of :meth:`_complete` once the job reads terminal: its records, its facts, its notice."""

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
        else:
            self._trim_later()
        self._touch()

    # === control (#123, ADR-0306 §3.1) ===========================================

    async def control(self, job_id: str, verb: str, payload: Mapping[str, Any]) -> str:
        """Hand ``verb`` to the kind's control adapter for a running job of this session.

        Generic: the service names no kind and reads no payload — it checks the
        job is this session's and still running, that the kind declares ``verb``
        in ``KindCapabilities.controls``, and calls the adapter's ``control``.
        The answer is the adapter's sentence, which the surfaces print. A kind
        that declares nothing is refused with its ``control_hint``
        (ADR-0293's control-adapter clause), in :func:`control_refusal`'s
        words. :class:`JobNotFound` for another session's id, as ``stop``
        raises it.
        """

        job = self._jobs.get(job_id)
        if job is None or job.owner != self._owner:
            if job is None and (ended := self.snapshot(job_id)) is not None:
                return self._ended_control(ended, verb) or (
                    f"{job_id[:6]} has ended: nothing runs to take {CONTROL_WORDS.get(verb, verb)}"
                )
            raise JobNotFound(job_id)
        if job.state in TERMINAL_STATES:
            return self._ended_control(self._snap(job), verb) or f"{job_id[:6]} already ended: {job.state}"
        caps = job.caps
        if verb not in caps.controls:
            return control_refusal(caps, verb)
        adapter = job.adapter
        if not isinstance(adapter, JobControlAdapter):
            return f"the {adapter.name} kind declares {verb} but has no control adapter"
        try:
            answer = await adapter.control(job_id, verb, payload)
        except Exception as exc:  # noqa: BLE001 — one sentence, never a traceback on a surface
            _log.debug("control %s on job %s raised", verb, job_id, exc_info=True)
            return f"{verb} failed: {type(exc).__name__}"
        return str(answer)

    def ended_message(self, job_id: str) -> str | None:
        """The kind's sentence for the panel's ``m`` on an ended job, typed no text (#848), or ``None``.

        ``None`` for a job that is live, unknown, another session's, or of a kind
        without :class:`JobEndedMessageAdapter`; the caller then asks ``control``
        for a steer, as it did before.
        """

        snap = self.snapshot(job_id)
        if snap is None or snap.state not in TERMINAL_STATES:
            return None
        registration = self.registry.get(snap.kind)
        adapter = registration.adapter if registration is not None else None
        if not isinstance(adapter, JobEndedMessageAdapter):
            return None
        try:
            return adapter.ended_message(snap)
        except Exception:  # noqa: BLE001 — never a traceback on a surface
            _log.debug("ended_message on job %s raised", job_id, exc_info=True)
            return None

    def _ended_control(self, job: JobSnapshot, verb: str) -> str | None:
        """The kind's sentence for ``verb`` on an ended ``job``, or ``None`` (:class:`JobEndedControlAdapter`).

        Asked only of a kind that declares ``verb``; one that raises answers
        nothing, and the service's own sentence stands.
        """

        registration = self.registry.get(job.kind)
        adapter = registration.adapter if registration is not None else None
        if not isinstance(adapter, JobEndedControlAdapter) or verb not in adapter.capabilities.controls:
            return None
        try:
            answer = adapter.ended_control(job, verb)
        except Exception:  # noqa: BLE001 — never a traceback on a surface
            _log.debug("ended_control %s on job %s raised", verb, job.job_id, exc_info=True)
            return None
        return answer if isinstance(answer, str) and answer else None

    # === stopping ==============================================================

    def request_stop(self, job_id: str, reason: StopReason = "user") -> bool:
        """Ask a job of the current session to stop. :class:`JobNotFound` for another session's id."""

        job = self._jobs.get(job_id)
        if job is None or job.owner != self._owner:
            return self._ended_or_not_found(job_id, job)
        return self._request_stop(job, reason)

    def _ended_or_not_found(self, job_id: str, job: _Job | None) -> bool:
        """``False`` for an ended job no longer kept whole, as one kept whole answers (#769)."""

        if job is None and self._settled_snap(job_id) is not None:
            return False
        raise JobNotFound(job_id)

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
            # A restored job, or an ended one no longer kept whole (#769), is
            # answered with its snapshot: nothing of it runs here.
            if job is None and (kept := self.snapshot(job_id)) is not None:
                return kept
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
        job.confirmed = True
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
        attention_ref: _Attention | None = None,
    ) -> bool:
        """Post ``notice`` to the current harness when its owner is the current one; whether it went.

        A notice of another session waits for its own (:meth:`bind` posts it
        again when that session is current), so a result never reaches a
        session that did not start its job (verify V15). An attention notice
        (``attention_ref``) marks only its own record placed: the job owes
        nothing for it (#123 D1, I1).
        """

        ref = job_ref or restored_ref or extension_ref or attention_ref
        owner = ref.owner if ref is not None else None
        harness = self._harness
        if harness is None or owner != self._owner or getattr(harness, "disposed", False):
            return False

        def placed(placed_notice: Notice, _entry_id: str) -> None:
            if job_ref is not None:
                self._job_notice_placed(job_ref, placed_notice)
            if restored_ref is not None:
                restored_ref.notice_state = "delivered"
            if extension_ref is not None:
                extension_ref.notice_state = "delivered"
            if attention_ref is not None:
                attention_ref.state = "delivered"
            for listener in list(self._placed_listeners):
                try:
                    listener(placed_notice)
                except Exception:  # noqa: BLE001
                    _log.debug("placed listener raised", exc_info=True)
            self._touch()

        return _owing(job_ref, notice, harness.post_notice(notice, on_placed=placed))

    def _attention(self, job: _Job, body: NoticeBody, brief: str) -> bool:
        """:meth:`JobContext.attention`: frame, record and post the job's one attention notice (#123 D1).

        ``False`` and nothing posted once the job raised one (whatever became
        of it, I5), for a job that does not notify (``job.notify`` already
        holds its kind's ``notify``: :meth:`start` and :meth:`prepare_adopted`
        fold it in), and unless :func:`_may_attend`. ``True`` also when its
        session is not the current one: the record waits for :meth:`bind`.
        ``False``, the record ``dropped``, when the current session's inbox
        refused it (an id it already knows; fix round 2, C-N).
        """

        if job.attention is not None or not job.notify or not _may_attend(job):
            return False
        adapter = self._adapter_now(job.registration)
        notice = Notice(
            notice_id=nt.attention_notice_id(job.job_id),
            custom_type=NOTICE_CUSTOM_TYPE,
            body=nt.frame(
                job_id=job.job_id,
                kind=adapter.name,
                label=adapter.label,
                title=job.title,
                status="running",
                elapsed=self._clock() - job.started_at,
                body=body.body,
                next_action=body.next_action,
                result_hint=False,
            ),
            details={"v": 1, "job_id": job.job_id, "status": "running"},
            brief=nt.cut_brief(
                f'{adapter.label} "{job.title}" still running' + (f": {brief}" if brief else "")
            ),
        )
        # The record first: the inbox's listeners (the TUI's wake) run inside the post.
        job.attention = _Attention(owner=job.owner, notice=notice)
        posted = self._post(notice, attention_ref=job.attention)
        harness = self._harness
        refused = not posted and job.owner == self._owner and harness is not None
        if refused and not getattr(harness, "disposed", False):
            job.attention.state = "dropped"
        self._touch()
        return job.attention.state != "dropped"

    def _repost_attention(self, job: _Job) -> None:
        """:meth:`bind`: a pending attention notice goes again while its job runs; else it is dropped (I4).

        :meth:`_post` keeps one of another session's job waiting for its own.
        """

        attention = job.attention
        if attention is None or attention.state != "pending":
            return
        if _may_attend(job):
            self._post(attention.notice, attention_ref=attention)
        else:
            attention.state = "dropped"

    def _job_notice_placed(self, job: _Job, placed: Notice) -> None:
        """A notice of ``job`` was placed: it owes it no more (#769).

        Delivered once nothing it posted waits — a notice the harness held out
        of every drain (a placement conflict) before this placement waits
        nowhere — and its current notice, if it has one, was placed, whichever
        was placed last. Only a placement runs this: a notice held after the
        job's last placement leaves it ``pending`` until a rebind posts its
        final notice again (rare: a conflict needs an ``nt-`` id collision or a
        foreign entry; ADR-0293's note).
        """

        job.owed.discard(placed.notice_id)
        job.placed.add(placed.notice_id)
        job.owed -= self._held()
        if _delivered(job):
            job.notice_state = "delivered"
        self._trim_later()

    def _held(self) -> set[str]:
        """The notices the current harness holds out of every drain."""

        # A host's own harness may have no inbox to hold notices in.
        held = getattr(self._harness, "held_notices", None)
        ids: Any = held() if callable(held) else ()
        return set(ids)

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

        Live: a job this process ran — its result, or its attention notice
        (#123 D1, I3) — or an extension's ``post_notice`` (verify S3). A
        restored result is not live: it waits for ``/jobs send`` or the user's
        next message (owner OQ7).
        """

        return self.extension_waiting() > 0 or any(
            j.owner == self._owner
            and ((j.notice_state == "pending" and j.notice is not None) or _attention_waits(j))
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
            if folded.job_id in self._jobs or folded.job_id in self._settled:
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
            client_start=_client_start_of(start),
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
            # ``recovered`` stays ``recovered`` (#611 S1c, ADR-0295): what a
            # restart read of a job no confirmed end backs is never ``done``.
            restored.state = _terminal(outcome.get("status"))
            restored.brief = str(outcome.get("brief", ""))
            restored.reason = None
            restored.elapsed = _recorded_elapsed(outcome)
            self._settle_restored(restored)
            if registration is not None:
                # The kind's reading of its outcome, joined with its own records
                # (Codex #12), for ``/jobs result``; a failure keeps the stored
                # brief and body (ADR-0293's table).
                try:
                    decoded = registration.adapter.decode(
                        {
                            "fact": dict(outcome),
                            "records": list(client),
                            "client_start": dict(restored.client_start),
                        }
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
        if recovered is not None and recovered.status not in ("unresolved", "recovered"):
            # The kind's confirmed records say how it ended.
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
        restored.argv_contains = argv_contains
        restored.gone = state == "lost"
        if recovered is not None and recovered.status == "unresolved":
            # THE KIND'S OWN UNCERTAINTY STANDS (#611 S1c, ADR-0295): its reason
            # is kept and OS evidence never turns it into ``lost``. The slot is
            # held while a recorded process may live and given back once every
            # one is confirmed gone; the job stays ``unresolved``.
            restored.kind_reason = True
            await self._restore_unresolved(
                restored, registration, recovered.reason or "unresolved", recheck=state != "lost"
            )
            if state == "lost":
                self._settle_restored(restored)
            return
        if recovered is not None and state == "lost":
            # ``recovered``: a reading of what the job left, recorded only once
            # every process it recorded is confirmed gone.
            await self._record_restored(
                session, restored, registration, "recovered", recovered.brief, recovered.payload
            )
        elif state == "lost":
            await self._record_restored(session, restored, registration, "lost", why, {})
        else:
            # A recorded process lives, or cannot be told gone: the job keeps its
            # supervision and its slot whatever its kind could read — a reading
            # is never an end while its process may still run (#611 S1c). The
            # liveness loop asks the kind again once the evidence says gone.
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
        # Once its processes were confirmed gone it holds no slot, and a read or
        # an adapter that failed is asked again on the next pass (#749).
        restored.recheck = recheck or (restored.gone and reason in _RETRIED_ONCE_GONE)
        if restored.reservation is None and not restored.gone:
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
                        body=_restored_unresolved_body(
                            reason, recheck=recheck, own=restored.kind_reason
                        )
                        + self._restored_note(restored, registration),
                    ),
                    details={"v": 1, "job_id": restored.job_id, "status": "unresolved"},
                    brief=nt.cut_brief(f'{restored.label} "{restored.title}" unresolved: {reason}'),
                )
                restored.notice_state = "pending"
                self._post(restored.notice, restored_ref=restored)
        self._ensure_liveness()

    def _restored_note(self, restored: _Restored, registration: Registration | None) -> str:
        """The kind's line for a restored ``lost`` or ``unresolved`` notice, after a newline; ``""`` without one (#611 S2d).

        Asked of the kind with the job's ``start`` fact (``restored_note``, a
        required adapter method); a raise or an answer that is no text adds
        nothing, as a failed ``finalize_at_placement`` does (ADR-0293 §3).
        """

        folded = restored.folded
        if registration is None or folded is None:
            return ""
        try:
            line = registration.adapter.restored_note(dict(folded.start or {}))
        except Exception:  # noqa: BLE001 — no extra line
            _log.debug("restored_note of %s raised", restored.job_id, exc_info=True)
            return ""
        return f"\n{line}" if isinstance(line, str) and line else ""

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
        restored.gone = state == "lost"
        if state == "lost":
            registration = self.registry.get(restored.kind)
            if registration is not None:
                # Its processes are gone now: the kind is asked again, as at the
                # restart (#611 S1c) — what it can read of the job's end is
                # recorded ``recovered``, its own uncertainty stays ``unresolved``,
                # and only nothing at all makes it ``lost``.
                await self._ask_again(restored, folded)
                return
            await self._record_restored(restored.session, restored, registration, "lost", why, {})
        elif why != restored.reason and not restored.kind_reason:
            restored.reason = why

    async def _ask_again(self, restored: _Restored, folded: FoldedJob) -> None:
        """Ask the kind of a restored job whose processes are gone, from its session's records now.

        A read of the records that fails leaves the job ``unresolved`` and
        checked again, so the next pass reads them again; and since no process
        of it is left, its slot goes back now rather than when aelix exits
        (#749: it was held for the rest of the process).
        """

        try:
            entries = await restored.session.get_entries()
        except Exception:  # noqa: BLE001 — read again on the next pass
            restored.read_failures += 1
            # Once at WARNING, then at DEBUG on every later pass (#749).
            level = logging.WARNING if restored.read_failures == 1 else logging.DEBUG
            _log.log(level, "could not read the records of job %s; read again later", restored.job_id)
            if restored.reservation is not None:
                restored.reservation.release()
                restored.reservation = None
            if not restored.kind_reason:
                restored.reason = "unreadable"
            return
        await self._reconcile_one(restored.session, folded, entries=entries)

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
            if status == "lost":
                text += self._restored_note(restored, registration)
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
        restored.state = _terminal(status)
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

    # === retention (#769) ===========================================================

    def _trim_later(self) -> None:
        """A job ended or its notice was placed: trim the sessions past :data:`SETTLED_KEPT`, soon."""

        if self._closed:
            return
        if self._trimming is not None and not self._trimming.done():
            self._trim_again = True
            return
        with contextlib.suppress(RuntimeError):
            self._trimming = asyncio.get_running_loop().create_task(
                self._trim(), name="aelix-jobs-trim"
            )
            self._trimming.add_done_callback(_log_task_failure)

    async def _trim(self) -> None:
        self._trim_again = True
        while self._trim_again and not self._closed:
            self._trim_again = False
            owners = {j.owner for j in self._jobs.values() if j.state in TERMINAL_STATES}
            for owner in owners:
                await self._trim_owner(owner)

    async def _trim_owner(self, owner: str) -> None:
        """Let go of ``owner``'s oldest ended jobs that owe nothing, down to the bound."""

        ended = [j for j in self._jobs.values() if j.owner == owner and j.state in TERMINAL_STATES]
        excess = len(ended) - self._settled_kept
        for job in ended:  # the map keeps start order: oldest first
            if excess <= 0:
                return
            # Only a kind whose ended jobs read from what they persisted (#769).
            if _owes_nothing(job) and job.caps.reads_persisted and await self._let_go(job):
                excess -= 1

    async def _let_go(self, job: _Job) -> bool:
        """Keep ``job`` only as its records read; ``False`` when they cannot be read back as it reads now."""

        kept = await self._read_back(job)
        # Nothing else takes a job out of the map, one trim runs at a time, and
        # owing nothing does not change once true: nothing to look at again.
        if kept is None or not self._reads_alike(job, kept):
            return False
        del self._jobs[job.job_id]
        self._settled[job.job_id] = kept
        self._touch()
        return True

    def _reads_alike(self, job: _Job, kept: _Settled) -> bool:
        """A check, for a kind that declared ``reads_persisted``, that ``kept`` reads as ``job`` does (#769).

        The snapshot field for field but its ``payload`` — a kind's live payload
        and its ``decode`` differ by design — then what the kind makes of each:
        its rows at width 1000 and the first 16 KiB page of its output. Later
        pages are not compared: the kind declared that they come from what it
        persisted. Any difference keeps the job whole.
        """

        live, rebuilt = self._snap(job), self._snap_settled(kept)
        if dataclasses.replace(live, payload={}) != dataclasses.replace(rebuilt, payload={}):
            return False
        for width in _COMPARED_WIDTHS:
            if self.render_rows(live, width) != self.render_rows(rebuilt, width):
                return False
        return all(
            self._output_of(live, 0, limit) == self._output_of(rebuilt, 0, limit)
            for limit in _COMPARED_PAGES
        )

    async def _read_back(self, job: _Job) -> _Settled | None:
        """What ``job``'s session's records say of it, as a restart reads them; ``None`` if they cannot.

        Its ``start`` and ``outcome`` facts, whether its notice's entry is in
        the file, and its kind's ``decode`` of the outcome joined with its own
        records (ADR-0293's restart reading). A read that fails, a fact that is
        missing or a ``decode`` that raises keeps the job whole: it is tried
        again at the next trim. So does an outcome whose kind's records did not
        all land (``client_records: missing``): its kind could not read it back
        as it reads it now.
        """

        try:
            entries = list(await job.session.get_entries())
            folded = fold(entries).get(job.job_id)
            outcome = folded.outcome if folded is not None else None
            if folded is None or folded.start is None or outcome is None:
                return None
            if outcome.get("client_records") != "ok":
                return None
            decoded = self._adapter_now(job.registration).decode(
                {
                    "fact": dict(outcome),
                    "records": client_records_of(entries, job.job_id),
                    "client_start": dict(job.client_start),
                }
            )
            notice_ids = _notice_ids(folded)
            placed = await self._all_placed(job.session, notice_ids)
        except Exception:  # noqa: BLE001 — kept whole, tried again later
            _log.debug("job %s: its records could not be read back", job.job_id, exc_info=True)
            return None
        if placed is None:
            return None
        start = folded.start
        adapter = self._adapter_now(job.registration)
        return _Settled(
            job_id=job.job_id,
            seq=job.seq,
            kind=job.registration.name,
            version=job.registration.version,
            label=adapter.label,
            title=str(start.get("title", "")),
            owner=folded.owner,
            created=float(start.get("created", 0.0) or 0.0),
            state=_terminal(outcome.get("status")),
            brief=str(outcome.get("brief", "")),
            elapsed=job.elapsed,
            progress=None if job.progress is None else dict(job.progress),
            notice_state="delivered" if notice_ids else "none",
            payload=dict(decoded.payload),
            client_start=_client_start_of(start),
        )

    async def _all_placed(self, session: Any, notice_ids: list[str]) -> bool | None:
        """``True`` when every one of ``notice_ids`` is placed in ``session``'s file; ``None`` otherwise."""

        for notice_id in notice_ids:
            if not await delivered(session, notice_id):
                return None
        return True

    def _settled_snap(self, job_id: str, *, any_owner: bool = False) -> JobSnapshot | None:
        kept = self._settled.get(job_id)
        if kept is None or not (any_owner or kept.owner == self._owner):
            return None
        return self._snap_settled(kept)

    def _snap_settled(self, kept: _Settled) -> JobSnapshot:
        """What ``_snap`` gave the job, from what was kept of it; its label the current one's."""

        current = self.registry.get(kept.kind)
        label = current.adapter.label if current is not None and current.version >= kept.version else kept.label
        return _snap_of(kept, label)

    # === reading ==================================================================

    def jobs_named(self, ident: str) -> list[str]:
        """The current session's jobs that a kind's own id ``ident`` names (#827).

        Each registered kind that is a :class:`JobIdAdapter` is asked in name
        order, with its own jobs of the current session, newest first; an
        answer counts only when it is one of them (Codex review of #827, H2: an
        adapter could name a job of another kind). A kind that raises answers
        nothing. One id per job, so more than one means two kinds answered with
        different jobs, which :func:`~aelix_coding_agent.jobs.commands.find`
        refuses. The core reads no id: the kind does.
        """

        snaps = self.snapshots()
        named: list[str] = []
        for name in self.registry.names():
            registration = self.registry.get(name)
            adapter = registration.adapter if registration is not None else None
            if not isinstance(adapter, JobIdAdapter):
                continue
            own = sorted((s for s in snaps if s.kind == name), key=lambda s: s.created, reverse=True)
            if not own:
                continue
            try:
                job_id = adapter.job_for(ident, own)
            except Exception:  # noqa: BLE001 — a kind's lookup never breaks a surface
                _log.debug("job_for(%r) raised for kind %s", ident, name, exc_info=True)
                continue
            if job_id in {s.job_id for s in own} and job_id not in named:
                named.append(job_id)
        return named

    def snapshot(self, job_id: str, *, any_owner: bool = False) -> JobSnapshot | None:
        """One job of the current session by its exact id; ``None`` for an unknown id or another session's.

        Every surface reads through this (``/jobs``, the ``job`` tools, the
        extension facade), so an id seen in another session reads nothing here
        (Codex #5). ``any_owner`` is the service's own and its tests' view.
        """

        job = self._jobs.get(job_id)
        if job is not None:
            return self._snap(job) if any_owner or job.owner == self._owner else None
        return self._settled_snap(job_id, any_owner=any_owner) or self._restored_snap(
            job_id, any_owner=any_owner
        )

    def _restored_snap(self, job_id: str, *, any_owner: bool) -> JobSnapshot | None:
        restored = self._restored.get(job_id)
        if restored is None or not (any_owner or restored.owner == self._owner):
            return None
        return self._snap_restored(restored)

    def snapshots(self, owner: str | None = None) -> list[JobSnapshot]:
        """This owner's jobs (the current one by default), live first, oldest first."""

        who = self._owner if owner is None else owner
        # An ended job no longer kept whole sorts where it did: by start time,
        # then start order (#769).
        live = [(j.created, j.seq, self._snap(j)) for j in self._jobs.values() if j.owner == who]
        live += [(k.created, k.seq, self._snap_settled(k)) for k in self._settled.values() if k.owner == who]
        old = [self._snap_restored(r) for r in self._restored.values() if r.owner == who]
        ordered = [snap for _created, _seq, snap in sorted(live, key=lambda t: t[:2])]
        return ordered + sorted(old, key=lambda s: s.created)

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
            rechecking=job.state == "unresolved" and job.unresolved_reason != "settle_error",
            processes_gone=job.confirmed,
            client_start=dict(job.client_start),
            attention_waiting=not terminal and _attention_waits(job),
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
            elapsed=restored.elapsed,
            last_activity_ago=None,
            quiet=False,
            result_waiting=restored.notice_state == "pending",
            notice_state=restored.notice_state,
            brief=restored.brief,
            unresolved_reason=restored.reason,
            payload=dict(restored.payload),
            rechecking=restored.state == "unresolved" and restored.recheck,
            processes_gone=restored.gone or restored.state in TERMINAL_STATES,
            client_start=dict(restored.client_start),
        )

    def unsettled(self, owner: str | None = None) -> bool:
        """Whether ``-p`` must wait: a job of a kind that blocks exit that is live, or ended with its notice not yet posted.

        A job leaves this only once its notice is posted (or it has none), so a
        drain that checks jobs before the inbox cannot miss a result (ADR-0293).
        That includes the writes between the terminal (or ``unresolved``) state
        and the notice, in ``_complete`` and ``_become_unresolved``
        (``completing``, #123 fix r2).
        """

        who = self._owner if owner is None else owner
        for job in self._jobs.values():
            if job.owner != who or not job.caps.blocks_print_exit:
                continue
            if job.state not in TERMINAL_STATES and job.state != "unresolved":
                return True
            if job.outbox is not None or job.completing:
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
        return self._output_of(snap, offset, limit)

    def _output_of(self, snap: JobSnapshot, offset: int, limit: int) -> OutputSlice | str:
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
            return [snap.title]  # the surfaces show the kind and label themselves (#776)
        try:
            rows = registration.adapter.render_rows(snap, width)
            if isinstance(rows, list) and all(isinstance(r, str) for r in rows):
                return rows
        except Exception:  # noqa: BLE001 — one generic row
            _log.debug("render_rows raised", exc_info=True)
        return [snap.title]

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

    def changed(self, *, check: Callable[[], None] | None = None) -> Coroutine[Any, Any, None]:
        """Capture the current signal now and validate a scoped waiter around its wait."""

        if self._changed is None:
            self._changed = asyncio.Event()
        event = self._changed

        async def wait() -> None:
            if check is not None:
                check()
            await event.wait()
            if check is not None:
                check()

        return wait()

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


def _may_attend(job: _Job) -> bool:
    """Whether ``job`` may still say it needs its session (#123 D1): running, no stop latched, its ``run`` not returned.

    The state alone does not say it: a stop the liveness loop latches leaves
    ``running`` as it was, and a job whose run returned reads ``running``
    through its barrier.
    """

    return (
        job.state == "running"
        and not job.stop_latched
        and not (job.ctx is not None and job.ctx._sealed)
    )


def _attention_waits(job: _Job) -> bool:
    """Its attention notice is posted (or waits for its session's bind) and not placed."""

    return job.attention is not None and job.attention.state == "pending"


def _delivered(job: _Job) -> bool:
    """Nothing of ``job`` waits, and its current notice, if any, was placed."""

    return not job.owed and (job.notice is None or job.notice.notice_id in job.placed)


def _owing(job: _Job | None, notice: Notice, posted: bool) -> bool:
    """``posted``, noting that ``job`` now owes ``notice`` until it is placed (#769)."""

    if posted and job is not None:
        job.owed.add(notice.notice_id)
    return posted


def _owes_nothing(job: _Job) -> bool:
    """An ended job whose outcome is written, whose notice is placed or was never owed, and whose
    tasks are over: it may be kept only as its records read (#769)."""

    return (
        job.state in TERMINAL_STATES
        and job.outbox is None
        and (job.notice is None or job.notice_state == "delivered")
        and (job.task is None or job.task.done())
        and (job.escalation is None or job.escalation.done())
        and not job.owed
    )


def _notice_ids(folded: FoldedJob) -> list[str]:
    """The notices a job's facts say must have been placed: its final one, which tells its end
    and stands for an ``unresolved`` one before it; without one, its ``unresolved`` ones (#769)."""

    final = (folded.outcome or {}).get("notice")
    records = [final] if isinstance(final, Mapping) else [s.get("notice") for s in folded.supervision]
    return list(dict.fromkeys(str(r.get("id")) for r in records if isinstance(r, Mapping)))


#: The row width and the page size whose reads must match before a job is let
#: go: rows as wide as any terminal shows them, and the first page
#: ``job(action="result")`` reads (#769).
_COMPARED_WIDTHS = (1000,)
_COMPARED_PAGES = (16 * 1024,)


def _snap_of(kept: _Settled, label: str) -> JobSnapshot:
    """The snapshot of an ended job read back from its records: what ``_snap`` gave it (#769)."""

    return JobSnapshot(
        job_id=kept.job_id,
        kind=kept.kind,
        label=label,
        title=kept.title,
        state=kept.state,
        owner=kept.owner,
        created=kept.created,
        elapsed=kept.elapsed,
        last_activity_ago=None,
        quiet=False,
        result_waiting=False,
        notice_state=kept.notice_state,
        brief=kept.brief,
        progress=kept.progress,
        payload=dict(kept.payload),
        processes_gone=True,
        client_start=dict(kept.client_start),
    )


def _terminal(status: Any) -> JobState:
    return status if status in TERMINAL_STATES else "unresolved"  # type: ignore[return-value]


def _recorded_elapsed(outcome: Mapping[str, Any]) -> float | None:
    """An ended job's run time as its outcome fact recorded it, for a restart's snapshot.

    A restart showed ``elapsed —`` for every job it read back, though the fact
    holds the run's ``elapsed_ms`` (Codex review of #821, M5). ``0`` and a value
    that is not a number stay unknown: the outcomes written with no run behind
    them (a launch the kind refused, a job a restart could not account for)
    record ``0``.
    """

    value = outcome.get("elapsed_ms")
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return value / 1000


def _client_start_of(start: Mapping[str, Any]) -> dict[str, Any]:
    """The ``client`` a ``start`` fact recorded (#611 S2d); empty when it has none or it is not a mapping."""

    client = start.get("client")
    return dict(client) if isinstance(client, Mapping) else {}


def _notice_record(notice: Notice | None) -> dict[str, Any]:
    assert notice is not None
    return {"id": notice.notice_id, "body": notice.body, "brief": notice.brief}


_HELPERS_UNVERIFIED = (
    "(Processes this job started outside its own tree were not confirmed stopped.)"
)


#: A restored job's reasons that are asked again on every pass once its
#: processes are confirmed gone: a read or an adapter call that failed (#749).
_RETRIED_ONCE_GONE = frozenset({"unreadable", "recover_error", "relation_error"})

_UNTIL_GONE = "it checks the job's processes until they are gone, and promises no later notice;"


def _restored_unresolved_body(reason: str, *, recheck: bool, own: bool = False) -> str:
    """Only what is true: a job the liveness loop does not recheck is not said to be checked (#611 S1c).

    Posted once, so it says only what stays true (#749): a kind's OWN
    ``unresolved`` (``own``) is rechecked only until its processes are gone,
    and its kind may still not tell how it ended then, so no later notice is
    promised for it.
    """

    then = (
        _UNTIL_GONE
        if recheck and own
        else "it keeps checking, and"
        if recheck
        else "it is not watching this job;"
    )
    return (
        "aelix restarted and found this job started and not ended; it could not tell "
        f"how it ended ({reason}). aelix stops nothing it finds at a restart; {then} "
        "/jobs shows its state."
    )


_UNRESOLVED_BODIES = {
    "cleanup_error": (
        "The job's run ended, but its cleanup of what it started outside its own "
        "process tree failed: those processes were not confirmed stopped. aelix "
        "retries the cleanup; /jobs shows its state. Recorded pids: {pids}."
    ),
    # The liveness loop skips it (``tick``): nothing checks it again (#749).
    "settle_error": (
        "The job's client failed to settle its run (settle_error), so aelix cannot "
        "tell how it ended, and it does not check this job again; /jobs shows its "
        "state. Recorded pids: {pids}."
    ),
}


def _unresolved_body(job: _Job, reason: str) -> str:
    pids = ", ".join(str(r.pid) for r in job.processes) or "none recorded"
    body = _UNRESOLVED_BODIES.get(reason)
    if body is not None:
        return body.format(pids=pids)
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


async def _cancel_quietly(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


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
    "SESSION_ENDED_FIRST",
    "SETTLED_KEPT",
    "JobLaunchFailed",
    "JobNotFound",
    "JobService",
    "JobStartRefused",
]
