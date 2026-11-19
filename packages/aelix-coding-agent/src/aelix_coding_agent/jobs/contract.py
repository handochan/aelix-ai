"""What a kind of background work supplies, and what the job service hands it (#722, ADR-0293).

A **kind** is a client of the job service: delegation, background ``bash``, or
an extension's own work. It registers two things under a name and a version:

* a :class:`JobKindAdapter` — the kind's view of its records: how to read its
  outcome, render its notice and its rows, page its output, recover a job a
  restart found unfinished, and which evidence relates a process to it;
* a runner factory — one :class:`JobRunner` per job, which does the work.

The service owns everything else: the state machine, the cleanup barrier, the
records, the notice frame, admission, liveness and the surfaces. Nothing here or
in the service names a kind; a kind's label, settings key and control hint are
opaque strings its client supplies (ADR-0008 as amended by ADR-0293).

**Runner rules** (ADR-0293 §runner):

* A runner keeps only what it was handed at start: its context, the processes
  and directories it registered, and its own immutable data. It never calls an
  ``ExtensionAPI`` or an extension context after :meth:`JobRunner.run` started —
  a ``/reload`` invalidates those while the job may still be finishing.
* :meth:`JobContext.register_process` is sealed once ``run`` returns, and a
  registration after the stop latch is stopped at once.
* ``run`` calls :meth:`JobContext.launched` or :meth:`JobContext.launch_failed`
  before the service's ``start`` returns: a ``start`` waits for one of them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    import asyncio

JobState = Literal[
    "starting",
    "running",
    "stopping",
    "done",
    "failed",
    "stopped",
    "lost",
    "recovered",
    "unresolved",
]
"""``unresolved`` is not terminal: the service keeps re-checking it.

``recovered`` is a restart's reading of a job its kind could read an end of
only from what the job left, not from a confirmed end (#611 S1c, ADR-0295):
terminal, and never shown as ``done``. A restart records it only once every
process the job recorded is confirmed gone."""

TERMINAL_STATES: frozenset[str] = frozenset({"done", "failed", "stopped", "lost", "recovered"})

StopReason = Literal["user", "model", "owner_end", "reload", "liveness", "shutdown", "signal"]

#: The variable a parent sets in the environment of a process it runs as a job
#: when it wants that process's versioned end-of-run line (#611 S1c, ADR-0295),
#: and the one version there is. :meth:`JobContext.protocol_env` hands it out.
JOBS_PROTOCOL_ENV = "AELIX_JOBS_PROTOCOL"
JOBS_PROTOCOL_VERSION = "1"
#: The ``--mode json`` line such a process writes after its print run drained,
#: before it exits (``modes/print_mode.py``). Only when it was asked for, and
#: only while background jobs are on in that process too (Codex #1).
PRINT_DONE_EVENT = "aelix_print_done"
#: The job's marker in the environment of a process it owns
#: (:meth:`JobContext.env_marker`).
JOB_MARKER_ENV = "AELIX_JOB"
#: Every variable the job service hands only to a process it owns. A client
#: removes them from the environment of every other child it starts, so an
#: inherited request never reaches a run nobody asked (Codex #1).
JOB_INTERNAL_ENV: tuple[str, ...] = (JOBS_PROTOCOL_ENV, JOB_MARKER_ENV)


@dataclass(frozen=True)
class KindCapabilities:
    """What a kind declares about its jobs. The service reads nothing else about it.

    ``default_bound`` is the kind's concurrency bound when the user set none
    (``backgroundJobs.limits.<settings_key>``); the service clamps every bound
    to its hard maximum. ``blocks_print_exit`` makes ``-p`` wait for its jobs.
    ``notify`` makes an ended job leave a notice for the model. ``normal_exit``
    says what happens to members still in the job's process tree when its root
    exits on its own: ``stop_descendants`` stops and confirms them inside the
    barrier, ``release`` leaves them. ``activity="none"`` turns the quiet flag
    off for a kind that reports no progress. ``noun`` is what an admission
    refusal counts its running jobs as — ``(one, many)``, such as
    ``("delegated agent", "delegated agents")`` — and ``None`` says "background
    jobs of this kind" (#611 S1c, ADR-0295). ``reads_persisted`` says that an
    ended job of the kind reads — its rows and every page of its output — only
    from what it persisted: its records, as ``decode`` reads them, and the files
    they name. Only such a kind's ended jobs may be kept as their records read
    once a session holds more than the service's bound; every other kind's stay
    whole (#769). Delegation declares it with one caveat: its rows also read its
    client's own ``DelegationJobs.info``, which is in memory and never pruned.
    """

    background: bool = True
    detachable: bool = False
    output: bool = True
    job_tool_control: bool = True
    control_hint: str | None = None
    counts_foreground: bool = False
    default_bound: int = 4
    blocks_print_exit: bool = True
    notify: bool = True
    normal_exit: Literal["stop_descendants", "release"] = "stop_descendants"
    activity: Literal["progress", "none"] = "progress"
    on_owner_end: Literal["stop"] = "stop"
    on_reload: Literal["stop"] = "stop"
    noun: tuple[str, str] | None = None
    reads_persisted: bool = False
    controls: tuple[str, ...] = ()
    """The control verbs the kind's adapter takes through ``JobService.control``
    (#123, ADR-0306 §3.1): opaque strings the kind supplies, such as ``steer``
    and ``wrap_up``. A verb not listed is refused with ``control_hint`` when
    the kind has one (``service.control_refusal``), so ``/jobs steer`` on a
    background command says what stops it. ``()``, the default, declares none
    (ADR-0293's control-adapter clause)."""


@dataclass(frozen=True)
class ClientOutcome:
    """How a run ended, as its runner reports it. ``payload`` is the kind's own, opaque here."""

    status: Literal["done", "failed", "stopped"]
    brief: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NoticeBody:
    """What a kind renders for its notice: the body the service escapes and frames, and a next step."""

    body: str
    next_action: str | None = None


@dataclass(frozen=True)
class OutputSlice:
    """One page of a job's output, as its kind reads it.

    ``next_offset`` is where the next page starts, opaque to the service and the
    surfaces (#611 S2d, Codex #13): a kind that pages by something other than
    characters — bytes of a file — says so here, and ``job(action="result")``
    and ``/jobs result`` pass it back as ``offset``. ``None`` keeps the reading
    they always made, ``offset + len(text)``.
    """

    text: str
    offset: int
    total: int | None = None
    more: bool = False
    next_offset: int | None = None

    @property
    def continue_at(self) -> int:
        """The ``offset`` the next page is asked with: ``next_offset``, else ``offset + len(text)``."""

        return self.next_offset if self.next_offset is not None else self.offset + len(self.text)


@dataclass(frozen=True)
class Recovered:
    """A restart's answer for a job it found started and not ended."""

    status: Literal["done", "failed", "stopped", "lost", "unresolved", "recovered"]
    brief: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    reason: str | None = None


@dataclass(frozen=True)
class RelationSpec:
    """What relates a live process to a job, beyond its recorded pid and start time.

    ``argv_contains`` is a list of strings every one of which must appear in the
    process's argv for it to count as this job's (the kind derives them). Empty
    means the recorded identity alone decides.
    """

    argv_contains: tuple[str, ...] = ()


@dataclass(frozen=True)
class HelperCleanup:
    """Whether what a job's processes started outside its tree was confirmed stopped."""

    state: Literal["confirmed", "unverified", "none"]
    reason: str | None = None


@dataclass(frozen=True)
class JobSnapshot:
    """One job as the surfaces see it. ``progress`` is what the runner last reported."""

    job_id: str
    kind: str
    label: str
    title: str
    state: JobState
    owner: str
    created: float
    elapsed: float | None
    last_activity_ago: float | None
    quiet: bool
    result_waiting: bool
    notice_state: Literal["none", "pending", "delivered"]
    brief: str = ""
    progress: Mapping[str, Any] | None = None
    unresolved_reason: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    #: Whether the liveness loop checks an ``unresolved`` job again: ``False``
    #: when it will not — a restored job whose kind's own ``unresolved`` has its
    #: processes confirmed gone, for one, and a job this process ran whose
    #: runner's ``settle`` failed (``settle_error``); a kind's registration still
    #: re-reads ``adapter_missing`` and ``adapter_too_old`` (#611 S1c, #749).
    rechecking: bool = False
    #: Whether aelix has confirmed every process the job recorded gone: its
    #: barrier passed, or a restart's evidence said so. ``False`` while one may
    #: live, and when nothing was checked — a restored ``unresolved`` job whose
    #: records or adapter failed before its evidence was read (#749).
    processes_gone: bool = False
    #: What the kind handed :meth:`~aelix_coding_agent.jobs.service.JobService.start`
    #: as ``client_start``, as the job's ``start`` fact holds it — the same for a
    #: job this process runs and for one a restart found (#611 S2d, Codex #12).
    #: Opaque here: a kind reads back its own launch data, such as the absolute
    #: path of an output file, without deriving it again after a restart.
    client_start: Mapping[str, Any] = field(default_factory=dict)
    #: Whether the job, not ended, has its one attention notice
    #: (:meth:`JobContext.attention`) posted and not yet placed (#123 D1). Never
    #: a result: ``result_waiting`` and ``notice_state`` do not count it.
    attention_waiting: bool = False


@runtime_checkable
class JobControlAdapter(Protocol):
    """The control half a kind adds when its ``capabilities.controls`` names a verb (#123, ADR-0306 §3.1).

    A separate protocol so a kind that declares no controls (``bash``) needs no
    method; the service checks ``isinstance`` against it only after the verb was
    declared. ``control`` takes a running job of the kind, one declared verb and
    the verb's own payload (``text``, ``mode``, ``origin``), and answers one
    sentence the surface prints.
    """

    async def control(self, job_id: str, verb: str, payload: Mapping[str, Any]) -> str: ...


@runtime_checkable
class JobEndedControlAdapter(Protocol):
    """What a kind says when a verb it declares reaches one of its jobs that has ended (#822).

    ``ended_control`` takes the ended job and the verb, and answers the sentence
    the surface prints, or ``None`` for the service's own ("<id> already ended:
    <state>"). A kind whose own door refuses such a job with a way on (a
    background agent: ``action="resume"``) says it here too, so ``/jobs`` and the
    panel say what the model is told (Codex review of #822, M6: ``/jobs wrap`` on
    a finished agent said only "already ended: done").
    """

    def ended_control(self, job: JobSnapshot, verb: str) -> str | None: ...


@runtime_checkable
class JobIdAdapter(Protocol):
    """The half a kind adds when its jobs are also named by ids of its own (#827).

    ``job_for`` answers the id of the job that ``ident`` names among ``jobs``, or
    ``None``. ``jobs`` are the kind's own jobs of the current session, newest
    first, a restart's included; their ``payload`` and ``client_start`` are the
    kind's to read. The ``job`` and ``job_stop`` tools and ``/jobs`` find a job by
    its job id or a unique prefix of one; one of the kind's ids reached them as
    "no job … in this session" though the kind's own surfaces take both (a
    background agent's answer names its agent id and its job id).

    The service asks only when neither an exact job id nor a unique prefix of one
    matched (Codex review of #827, H2: a kind's answer had redirected a prefix
    that named another job), keeps an answer only when it is one of ``jobs``, and
    refuses an ``ident`` two kinds answer with different jobs. It reads no id itself.
    """

    def job_for(self, ident: str, jobs: Sequence[JobSnapshot]) -> str | None: ...


@runtime_checkable
class JobKindAdapter(Protocol):
    """A kind's view of its jobs. Every method may raise; ADR-0293's table says what the service does then."""

    name: str
    version: int
    label: str
    capabilities: KindCapabilities
    settings_key: str

    def decode(self, client: Mapping[str, Any]) -> ClientOutcome:
        """A restart's reading of an ended job: ``client`` is ``{"fact": <the outcome fact>,
        "records": <the job's own records, each with its custom_type>, "client_start": <the
        start fact's client>}`` (Codex #12; ``client_start`` since #775)."""
        ...

    def render_notice(self, outcome: ClientOutcome) -> NoticeBody: ...

    async def finalize_at_placement(
        self, outcome: ClientOutcome, branch: list[Any]
    ) -> str | None: ...

    def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]: ...

    def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None: ...

    async def recover(
        self, facts: Mapping[str, Any], client_records: list[Mapping[str, Any]]
    ) -> Recovered | None: ...

    def relation(self, facts: Mapping[str, Any]) -> RelationSpec: ...

    def restored_note(self, facts: Mapping[str, Any]) -> str | None:
        """One line a restart adds to a ``lost`` or ``unresolved`` notice of this kind, or ``None`` (#611 S2d).

        ``facts`` is the job's ``start`` fact; its ``client`` is what the kind
        handed ``start`` as ``client_start``. Required, so the service never
        has to ask an adapter what it has (Codex #12): a kind with nothing to add
        returns ``None``.
        """
        ...


class JobContext(Protocol):
    """What a runner is handed at start. The service's ``_Context`` is the implementation."""

    job_id: str
    owner: str
    mode: Literal["background"]

    def launched(self) -> None: ...

    def launch_failed(self, error: BaseException | str) -> None: ...

    def register_process(
        self,
        proc: asyncio.subprocess.Process | int,
        *,
        pgid: int | None = None,
        tree: Any = None,
    ) -> Any:
        """The record the barrier reads; a client kills through it, never around it (#611 S1c)."""
        ...

    def register_directory(self, path: str) -> None: ...

    @property
    def stop_latched(self) -> bool: ...

    def progress(self, snapshot: Mapping[str, Any] | None = None) -> None: ...

    def attention(self, body: NoticeBody, *, brief: str = "") -> bool:
        """Tell this job's session, once, that the running job may need it; whether it was taken (#123 D1).

        One framed notice per job, ``details.status == "running"``, its id
        ``<job>:attention``: refused (``False``, nothing posted) once one was
        taken, for a job that does not notify, and unless the job is running —
        not starting, not asked to stop, its ``run`` not returned. ``brief`` is
        the kind's short reason for the one-line summary. It is not the job's
        result and nothing that counts results counts it; no fact is written,
        so a restart restores nothing of it.
        """
        ...

    def env_marker(self) -> dict[str, str]: ...

    def protocol_env(self) -> dict[str, str]: ...


@runtime_checkable
class JobRunner(Protocol):
    """One job's work. Every method may raise; cancellation is always re-raised."""

    async def run(self, ctx: JobContext) -> ClientOutcome: ...

    def request_stop(self, reason: StopReason) -> None: ...

    async def settle(self) -> None: ...

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup: ...

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]: ...


RunnerFactory = Callable[[Mapping[str, Any]], JobRunner]


__all__ = [
    "JOBS_PROTOCOL_ENV",
    "JOB_INTERNAL_ENV",
    "JOB_MARKER_ENV",
    "JOBS_PROTOCOL_VERSION",
    "PRINT_DONE_EVENT",
    "TERMINAL_STATES",
    "ClientOutcome",
    "HelperCleanup",
    "JobContext",
    "JobControlAdapter",
    "JobEndedControlAdapter",
    "JobIdAdapter",
    "JobKindAdapter",
    "JobRunner",
    "JobSnapshot",
    "JobState",
    "KindCapabilities",
    "NoticeBody",
    "OutputSlice",
    "Recovered",
    "RelationSpec",
    "RunnerFactory",
    "StopReason",
]
