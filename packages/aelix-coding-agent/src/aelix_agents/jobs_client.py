"""Delegation as a client of the job service: ``agent(action="spawn")`` and its siblings (#611 S1c, ADR-0295).

WHAT THIS MODULE IS. ADR-0293 made background work a job its session owns: the
job service (``aelix_coding_agent/jobs``) runs it, the kernel delivers its
notice once, and a CLIENT says what the work is. This is the first client. It
registers the ``agent`` kind — :class:`DelegationKindAdapter` (how a
delegation's job reads, renders and recovers) and :class:`DelegationRunner`
(one background child, start to envelope) — and it answers the five background
actions of the ``agent`` tool. Everything is behind the job service's one gate
(``jobs/activation.py``): while it is off nothing here is constructed, the tool
offers none of these actions, and every delegation runs as it did before #611.

THE RUN IS TODAY'S RUN. A background child is ``runtime._run`` → the print
channel → the reaper, exactly as a foreground ``agent()`` child is — the same
consent, posture, argv, env, records and envelope — with three differences,
each the job service's ownership (ADR-0293 §4, contract §2.3):

* its row carries an OWNER (:class:`_Ownership`, ``RunningChild.owner``): the
  process, its tree and its prompt directory are handed to the job as soon as
  each exists, the tree is closed and the directory removed only after the
  service's barrier confirmed the processes gone, and every kill the channel or
  the reaper would have made goes through the job's evidence legs, which
  collect before they signal (``jobs/evidence.py``);
* its records are CONFIRMED and carry ``job_id``: the start and pending are
  placed once each before the launch (a start that cannot be recorded refuses
  it), and the settle and final are written by the service, confirmed, ahead
  of the job's own ``cleanup`` and ``outcome`` facts;
* it asks the child for the versioned ``aelix_print_done`` line
  (``AELIX_JOBS_PROTOCOL=1``), and a run that lingered after its last run
  closed counts as finished only with it (ADR-0276 D4 as amended).

THE RUNNER KEEPS ONLY WHAT IT WAS HANDED (ADR-0293 §3). A ``/reload``
invalidates the ``ExtensionAPI`` and every context while a job may still be
finishing, so everything the run reads — the parent's directory, tools,
exclusions, context-file flag, thinking level, model, model registry and
session — is read ONCE, in the tool call, into a :class:`_Launch`, and the run
gets a runtime of its own over a host that answers only those values
(:func:`frozen_host`). Its progress goes to the job (``ctx.progress``), never
to the extension's statusline bridge.

THE ACTIONS (owner, 2026-11-07, and #604's one tool with an ``action``):

* ``spawn`` — ONE task; returns once the child launched (a launch failure is
  the call's own error, and no notice follows it);
* ``wait`` — :data:`~aelix_agents.tool.WAIT_MIN_SECONDS` to
  :data:`~aelix_agents.tool.WAIT_MAX_SECONDS`, default
  :data:`~aelix_agents.tool.WAIT_DEFAULT_SECONDS`; never stops anything, never
  returns the result (the notice is the one delivery); the third empty wait in
  a row in one prompt is refused;
* ``status`` / ``stop`` — through the service, for this session's agents only;
* ``result`` — the whole result again, paged; after a restart, from the
  parent's settle and the child's own file under the recovery rules below.

CONSERVATIVE RECOVERY (contract §2.4). A child's session file records no run
boundaries, so a restart never calls a job ``done`` without the parent's own
confirmed settle for it, and reads an answer from the child's file only when
the run is the LATEST run of that file — no later start for the same child
session anywhere in the parent's records — and the file's writer lock is free.
Otherwise the job is ``unresolved(cannot_bind_run)``, or left to the service's
OS evidence. What it reads is ``recovered`` on every surface — never ``done`` —
and the service records it only once the job's recorded processes are
confirmed gone (ADR-0295 §8).

HELPERS (owner, 2026-11-07; #719, ADR-0296). A delegated child's detached
helpers are stopped with it. On POSIX the cleanup is the same sweep a foreground
run's cleanup task makes (``print_channel.sweep_helpers``): the launch ledger's
groups and this run's marker hits, each with authority, then confirmed; its
answer is ``confirmed`` only when every target aelix identified is confirmed
gone, no launch was in flight, the ledger is whole and armed, the agent ran its
own exit cleanup (or aelix's stop legs ended it) and, on Linux, it was a
subreaper — otherwise ``unverified`` with why. A win32 child whose Job the
barrier confirmed empty answers ``confirmed``.

RPC AND ACP refuse the background actions until #612: the tool there has the
schema it had before #611 (``extension.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import re
import sys
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from aelix_coding_agent.jobs import evidence as ev
from aelix_coding_agent.jobs.contract import (
    TERMINAL_STATES,
    ClientOutcome,
    HelperCleanup,
    JobSnapshot,
    KindCapabilities,
    NoticeBody,
    OutputSlice,
    Recovered,
    RelationSpec,
)
from aelix_coding_agent.jobs.notice import escape_body, format_elapsed
from aelix_coding_agent.jobs.records import append_record

from aelix_agents.child_session import (
    CANNOT_BIND_RUN,
    CHILD_SESSION_TYPE,
    RESUME_FIRST_RUN_MISSING,
    RESUME_UNKNOWN_ID,
    RecoveredAnswer,
    _session_file_of,
    place_children,
    recover_run_answer,
    resume_lineage_refusal,
)
from aelix_agents.envelope import REASON_STOPPED, child_line
from aelix_agents.print_channel import _parent_stopped, sweep_helpers
from aelix_agents.result_file import (
    RESULT_RECORD_TYPE,
    ResultFile,
    Unreadable,
    read_result,
    write_result,
)
from aelix_agents.runtime import (
    MAX_LIVE_CHILDREN,
    CallEnvelopes,
    SpawnRecordMeta,
    SubagentHost,
    _lock_refusal,
    _SubagentRuntimeImpl,
)
from aelix_agents.tool import (
    WAIT_DEFAULT_SECONDS,
    WAIT_MAX_SECONDS,
    WAIT_MIN_SECONDS,
    render_subagent_result,
)

if TYPE_CHECKING:
    from aelix_coding_agent.builtin.permission_mode import PermissionMode
    from aelix_coding_agent.subagent_contract import (
        ResolvedProfile,
        SubagentProgress,
        SubagentResult,
    )

    from aelix_agents.consent import SpawnGrant
    from aelix_agents.print_channel import RunningChild, SubagentChannel

logger = logging.getLogger(__name__)

KIND = "agent"
"""The kind's name, registered by this client — opaque to the service."""
KIND_VERSION = 1
LABEL = "agent"
SETTINGS_KEY = "agents"
"""``backgroundJobs.limits.agents`` (ADR-0293 §9, owner OQ6)."""

NOTICE_BODY_MAX_BYTES = 12 * 1024
"""The most of a result a notice carries, in UTF-8 bytes (#611 S1c).

Below the service's 16 KiB frame (``jobs/notice.NOTICE_MAX_BYTES``) by the
frame's own capped parts — its header, the next step, the placement line — so
the frame never has to cut the body itself. A longer result is cut here, at a
character boundary, and the cut names ``agent(action="result")`` and the
child's session file, where the rest is (owner, 2026-11-07: the 16 KiB framed
cap points there)."""

RESULT_PAGE_BYTES = 16 * 1024
"""One page of ``agent(action="result")``, in UTF-8 bytes."""

RECOVERED_CAVEAT = (
    "Its last answer, read from its session file after aelix restarted; aelix could "
    "not confirm it finished."
)
"""What leads every reading of a ``recovered`` agent — its notice, ``status``'s
brief, ``result`` and ``/jobs result`` (contract §2.4: never ``done`` without the
parent's confirmed settle)."""

_STATUS_OF: dict[str, Literal["done", "failed", "stopped"]] = {
    "ok": "done",
    "aborted": "stopped",
    "cancelled": "stopped",
    "declined": "failed",
    "error": "failed",
    "timeout": "failed",
}
"""An envelope's status as a job's outcome. ``aborted`` is a stop (``stop``,
``/jobs stop``, an owner's end, a reload); everything else that is not ``ok``
failed."""

_STOPPED_BY: dict[str, str] = {
    "model": 'stopped by you (agent(action="stop"))',
    "user": "stopped by the user (/jobs stop)",
    "owner_end": "stopped when its session ended (/new, /resume, /fork or quit)",
    "reload": "stopped by /reload",
    "shutdown": "stopped when aelix exited",
    "signal": "stopped by an interrupt",
    "liveness": "stopped by aelix's liveness check",
}
"""Why a BACKGROUND agent's run was stopped, by the job's stop reason (verify N4).

The envelope's own :data:`~aelix_agents.envelope.REASON_STOPPED` names the
foreground ways — ``stop``, ``/new`` … — which are not how a job is stopped."""

_HELPERS_UNTRACKED = "the agent's run had no launch ledger to sweep"
_HELPERS_NO_JOB = "the agent ran without a Windows Job to confirm its helpers against"
_HELPERS_UNCONFIRMED = "its processes are not yet confirmed gone"

CAPABILITIES = KindCapabilities(
    background=True,
    # Ctrl+B is #611 S3.
    detachable=False,
    output=True,
    # ``job_stop`` and the ``job`` tool's reads cover every kind, but stopping
    # and waiting for an agent is this tool's (#604: one ``agent`` tool).
    job_tool_control=False,
    control_hint='use agent(action="status"|"wait"|"stop"|"result", id=…)',
    counts_foreground=True,
    default_bound=MAX_LIVE_CHILDREN,
    blocks_print_exit=True,
    notify=True,
    # A delegated child's helpers are stopped with it (owner, 2026-11-07).
    normal_exit="stop_descendants",
    activity="progress",
    # Every live delegation counts, foreground ones too: the refusal a
    # foreground or batch call reads names them as the model knows them (verify S3).
    noun=("delegated agent", "delegated agents"),
    # An ended agent reads from its result file (#769); one whose run wrote none
    # reads otherwise once read back, and the job service keeps it whole.
    reads_persisted=True,
)


# === What a spawn freezes ======================================================


@dataclass(frozen=True)
class _Launch:
    """Everything a background run reads, read ONCE in the ``spawn`` call (ADR-0293 §3).

    The runner keeps this and nothing that reaches an ``ExtensionAPI``: the
    host it runs on answers these values and no others (:func:`frozen_host`),
    so a ``/reload`` that invalidates the extension while the child is still
    finishing changes nothing the run reads.
    """

    grant: SpawnGrant
    resolved: ResolvedProfile
    task: str
    cwd: str
    tool_call_id: str
    host: SubagentHost
    channel: SubagentChannel | None
    stop_grace: float = ev.STOP_GRACE


def frozen_host(
    *,
    cwd: str,
    posture: PermissionMode,
    active_tools: list[str] | None,
    exclude_tools: Sequence[str],
    context_files: bool,
    model_registry: Any,
    model: Any,
    thinking: str | None,
    session: Any,
) -> SubagentHost:
    """A :class:`SubagentHost` that answers values read once, and reaches nothing live.

    No ``on_progress``, no ``on_disclosure`` (both reach the extension's
    statusline bridge), no ``reserve`` (the job holds its slot itself), and no
    consent context: a background run asks nothing of anyone.
    """

    tools = None if active_tools is None else list(active_tools)
    excluded = tuple(exclude_tools)
    return SubagentHost(
        cwd=lambda: cwd,
        posture=lambda: posture,
        active_tools=lambda: None if tools is None else list(tools),
        exclude_tools=lambda: excluded,
        context_files=lambda: context_files,
        model_registry=lambda: model_registry,
        model=lambda: model,
        thinking=lambda: thinking,
        session=lambda: session,
    )


def frozen_channel(channel: SubagentChannel | None, *, model_registry: Any, model: Any) -> Any:
    """The channel a background run uses: the session's, with nothing live left in it.

    A :class:`~aelix_agents.print_channel.PrintChannel` reads its model
    registry and the parent's model through getters into the extension; the
    copy answers the values the spawn read. Any other channel — the recording
    and stub channels the tests inject — is used as it is.
    """

    from aelix_agents.print_channel import PrintChannel

    if isinstance(channel, PrintChannel):
        return channel.frozen(model_registry=model_registry, parent_model=model)
    return channel


# === The job's side of the row =================================================


class _Ownership:
    """:class:`~aelix_agents.print_channel.CleanupOwner` for one background run.

    It holds the job's context, and once the process exists the service's OWN
    record of it (``JobContext.register_process`` returns it): every kill here
    is :mod:`aelix_coding_agent.jobs.evidence`'s legs on that record, so the
    evidence they collect before the first signal — the walk, the groups, on
    win32 the Job's members — is what the barrier confirms against.
    """

    def __init__(
        self, ctx: Any, *, on_launch: Callable[[RunningChild], None] | None = None
    ) -> None:
        self._ctx = ctx
        self._on_launch = on_launch
        self.job_id: str = ctx.job_id
        self.record: Any = None
        self.tree: Any = None
        self.row: RunningChild | None = None
        self.launched = False
        self.session_path: str | None = None
        self.closing: list[tuple[str, dict[str, Any]]] | None = None
        #: Whether its tree held a win32 Job when the confirmed cleanup closed it.
        self.had_job = False
        self._tasks: set[asyncio.Future[Any]] = set()

    # ── CleanupOwner ─────────────────────────────────────────────

    def child_env(self) -> dict[str, str]:
        try:
            return {**self._ctx.protocol_env(), **self._ctx.env_marker()}
        except Exception:  # noqa: BLE001 — a request not made is a legacy run
            logger.debug("job %s: no protocol env", self.job_id, exc_info=True)
            return {}

    def on_directory(self, path: str) -> None:
        try:
            self._ctx.register_directory(path)
        except Exception:  # noqa: BLE001 — sealed: the run is over
            logger.debug("job %s: directory not registered", self.job_id, exc_info=True)

    def on_process(self, row: RunningChild, proc: Any, *, session_path: str | None = None) -> None:
        self.row = row
        self.tree = row.tree
        self.session_path = session_path
        # With no tree (an attach that degraded), the child's own process group —
        # it leads one, ``start_new_session`` — is the evidence on POSIX.
        pgid = proc.pid if row.tree is None and sys.platform != "win32" else None
        try:
            self.record = self._ctx.register_process(proc, pgid=pgid, tree=row.tree)
        except Exception:  # noqa: BLE001 — a job that cannot hold it stops it
            logger.warning(
                "job %s: its process could not be registered", self.job_id, exc_info=True
            )
            with contextlib.suppress(Exception):
                proc.kill()
        if not self.launched:
            self.launched = True
            if self._on_launch is not None:
                # The agent's id is known now — the receipt allocated its file
                # before the spawn — and is named before ``spawn`` returns it.
                with contextlib.suppress(Exception):
                    self._on_launch(row)
            self._ctx.launched()

    def stop_now(self) -> None:
        record = self.record
        if record is not None:
            with contextlib.suppress(Exception):
                ev.kill_now(record)

    async def stop(self, grace: float) -> None:
        record = self.record
        if record is not None:
            await ev.stop_legs([record], grace=grace)

    # ── records ──────────────────────────────────────────────────

    async def write_records(
        self, session: Any, records: Sequence[tuple[str, dict[str, Any]]]
    ) -> None:
        """The receipt's opening records, CONFIRMED and placed once, with the job's id.

        Raises ``RecordWriteFailed`` when one did not land: ``runtime._run``
        then ends before the launch, and the job's start is refused.
        """

        for custom_type, data in records:
            await append_record(session, custom_type, {**data, "job_id": self.job_id})

    def hand_records(self, records: Sequence[tuple[str, dict[str, Any]]]) -> None:
        """The settle and final ``_run`` built; the service writes them (:meth:`DelegationRunner.records`)."""

        self.closing = list(records)

    # ── the stop the job asked for ────────────────────────────────

    def request_stop(self, grace: float) -> None:
        """The job's evidence legs, soft first, once the process exists (the runner marks the row)."""

        if self.record is None:
            return
        task = asyncio.ensure_future(self.stop(grace))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def settle(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


# === The runner =================================================================


@dataclass
class AgentJob:
    """What this client keeps of one background agent, by its job id.

    ``agent_id`` is what ``spawn`` returned and what the model passes back:
    the child's session id when it has a session file, else its spawn key
    (``test_agent_id_session_or_key``). ``text`` is the whole rendered result
    once the run returned — held by the run's runner, and read only before the
    job has an outcome, when the snapshot's payload has none (#769). An ended
    job's result is the live payload's while the job service keeps it whole and
    its file's once it is let go; the runner, and the text with it, is freed at
    the next garbage collection (it sits in reference cycles).

    One entry per background agent this process ran, never removed: its ids,
    profile, session path and status — not its task, which its facts and the
    child's file hold.
    """

    job_id: str
    profile: str
    agent_id: str | None = None
    key: str | None = None
    session_id: str | None = None
    session_path: str | None = None
    status: str | None = None
    runner: weakref.ref[DelegationRunner] | None = None

    @property
    def text(self) -> str | None:
        runner = self.runner() if self.runner is not None else None
        return runner.text if runner is not None else None


class DelegationRunner:
    """One background child, from spawn to envelope (ADR-0293 :class:`~aelix_coding_agent.jobs.contract.JobRunner`)."""

    def __init__(self, launch: _Launch, jobs: DelegationJobs) -> None:
        self._launch = launch
        self._jobs = jobs
        self._own: _Ownership | None = None
        self._runtime: _SubagentRuntimeImpl | None = None
        self._ctx: Any = None
        self._stopped_by: str | None = None
        self.result: SubagentResult | None = None
        #: The whole rendered result once the run returned (``AgentJob.text``).
        self.text: str | None = None
        #: Its file, once written (#769); ``None`` when it could not be.
        self.result_file: ResultFile | None = None

    async def run(self, ctx: Any) -> ClientOutcome:
        launch = self._launch
        self._ctx = ctx
        entry = self._jobs.track(ctx.job_id, launch)
        entry.runner = weakref.ref(self)

        def named(row: RunningChild) -> None:
            entry.key = row.id
            entry.session_id = row.session_id
            entry.agent_id = row.session_id or row.id
            self._jobs.name(entry)

        own = _Ownership(ctx, on_launch=named)
        self._own = own
        # Its own runtime over the frozen host: its own registry, its own door —
        # the session's ``stop_all`` never reaches it; the job service stops it.
        runtime = _SubagentRuntimeImpl(host=launch.host, channel=launch.channel)
        self._runtime = runtime

        def tap(progress: SubagentProgress) -> None:
            self._on_progress(entry, progress)

        try:
            result = await runtime._run(
                launch.grant,
                launch.resolved,
                launch.task,
                child_cwd=launch.cwd,
                on_event=tap,
                # The per-prompt budget was charged by the call that spawned it.
                charge_budget=False,
                record=SpawnRecordMeta(
                    tool_call_id=launch.tool_call_id,
                    mode="single",
                    index=0,
                    envelopes=CallEnvelopes(),
                ),
                owner=own,
            )
        except Exception as exc:
            if not own.launched:
                # A start record that could not be confirmed, most likely: the
                # launch is refused and the call says why (ADR-0293 §7).
                ctx.launch_failed(f"the agent could not be started: {type(exc).__name__}: {exc}")
            raise
        stopped_by = _STOPPED_BY.get(self._stopped_by or "")
        if result.reason == REASON_STOPPED and stopped_by is not None:
            result = dataclasses.replace(result, reason=stopped_by)
        self.result = result
        entry.session_path = own.session_path
        if not own.launched:
            # ``_run`` refused, or the spawn failed: the start call's error, and
            # no notice (ADR-0293 §3).
            ctx.launch_failed(result.error or result.summary or "the agent did not start")
        text = _utf8_safe(_result_text(result))
        self.text = text
        # ``key`` is named at the launch (``named``): a run that never launched writes none.
        self.result_file = await _write_result(launch.host.session(), entry.key, text)
        status = _STATUS_OF.get(result.status, "failed")
        entry.status = status
        return ClientOutcome(
            status=status,
            brief=_utf8_safe(_brief(result)),
            payload={
                "text": text,
                "agent_id": entry.agent_id,
                "key": entry.key,
                "session_id": entry.session_id,
                "profile": launch.resolved.name,
                "envelope_status": result.status,
            },
        )

    def _on_progress(self, entry: AgentJob, progress: SubagentProgress) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        with contextlib.suppress(Exception):
            ctx.progress(
                {
                    "tool": None if progress.current_tool is None else _utf8_safe(progress.current_tool),
                    "tokens": progress.tokens,
                    "last_text": None if progress.last_text is None else _utf8_safe(progress.last_text),
                    "state": progress.state,
                }
            )

    def request_stop(self, reason: str) -> None:
        if self._stopped_by is None:
            self._stopped_by = reason
        own = self._own
        runtime = self._runtime
        if runtime is not None:
            # The envelope reads ``aborted``: the process ended because it was
            # stopped. Before the process exists this is the whole stop: the
            # channel re-reads ``stopped`` before and after the spawn.
            for row in list(runtime._children.values()):
                row.stopped = True
        if own is not None:
            own.request_stop(self._launch.stop_grace)

    async def settle(self) -> None:
        own = self._own
        if own is not None:
            await own.settle()

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        """Close the tree the barrier confirmed, and say what it could not confirm.

        The tree is closed HERE, after the barrier (contract §2.3): closing it
        earlier would have disarmed the legs the barrier still needed, and on
        win32 closing a job handle with members left would kill them unwaited.
        A barrier that did NOT confirm leaves it open (Codex #2): the job stays
        ``unresolved``, the service's recheck still needs its legs, and its
        cleanup is asked again once the barrier confirms. Whether a win32 Job
        existed is read before the close, which is what clears it, so a
        confirmed cleanup after an earlier failed one reads ``confirmed``.
        """

        own = self._own
        if own is None:
            return HelperCleanup("none")
        if not receipt.get("confirmed"):
            if own.record is None:
                return HelperCleanup("none")
            return HelperCleanup("unverified", _HELPERS_UNCONFIRMED)
        tree = own.tree
        if tree is not None and not getattr(tree, "closed", False):
            own.had_job = getattr(tree, "job", None) is not None
            with contextlib.suppress(Exception):
                tree.close()
        if own.record is None:
            return HelperCleanup("none")
        if sys.platform == "win32":
            if own.had_job:
                return HelperCleanup("confirmed")
            return HelperCleanup("unverified", _HELPERS_NO_JOB)
        return await _posix_helpers(own)

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        own = self._own
        records = list(own.closing or []) if own is not None else []
        written = self.result_file
        return records + ([(RESULT_RECORD_TYPE, written.record())] if written is not None else [])


def _children_dir(session: Any) -> str | None:
    """The directory a parent's child session files live in; ``None`` with no session file or outside a bucket."""

    path = _session_file_of(session) if session is not None else None
    placement = place_children(path) if path is not None else None
    return placement.directory if placement is not None else None


async def _write_result(session: Any, key: str | None, text: str) -> ResultFile | None:
    """The run's result file beside its child session (#769); ``None`` where there is none to write:
    a run that never launched (no key), or one with no child session directory."""

    directory = _children_dir(session) if key is not None else None
    if directory is None or key is None:
        return None
    return await asyncio.to_thread(write_result, directory, key, text)


def _read_result(session: Any, payload: Mapping[str, Any]) -> str | Unreadable | None:
    """The result a run wrote to its file, as ``payload["result"]`` records it; ``None`` when it recorded none."""

    file = ResultFile.from_record(payload.get("result"))
    if file is None:
        return None
    directory = _children_dir(session)
    if directory is None:
        return Unreadable("this conversation's session file is not where its agents' files are")
    return read_result(directory, file)


async def _posix_helpers(own: _Ownership) -> HelperCleanup:
    """The launch ledger's sweep for a background run, inside the service's ``cleanup_bound`` (#719).

    Reached only after the barrier confirmed the job's processes gone. The
    ledger is read before the service removes the run's directories, so one
    kept in the prompt directory is read too (r2 §3.1).
    """

    row = own.row
    tracking = row.tracking if row is not None else None
    if row is None or tracking is None:
        return HelperCleanup("unverified", _HELPERS_UNTRACKED)
    answer = await sweep_helpers(tracking, parent_stopped=_parent_stopped(row))
    if answer.confirmed:
        return HelperCleanup("confirmed")
    return HelperCleanup("unverified", answer.reason)


# === The kind's adapter ===========================================================


class DelegationKindAdapter:
    """How the job service reads, renders and recovers a background agent (ADR-0293 :class:`~aelix_coding_agent.jobs.contract.JobKindAdapter`)."""

    name = KIND
    version = KIND_VERSION
    label = LABEL
    settings_key = SETTINGS_KEY
    capabilities = CAPABILITIES

    def __init__(self, jobs: DelegationJobs, *, session: Callable[[], Any | None]) -> None:
        self._jobs = jobs
        self._session = session

    def decode(self, client: Mapping[str, Any]) -> ClientOutcome:
        """A restart's reading of an ENDED job: its own settle, never a made-up text."""

        fact = client.get("fact")
        fact = fact if isinstance(fact, Mapping) else {}
        records = client.get("records")
        records = records if isinstance(records, list) else []
        status = fact.get("status")
        if status == "recovered":
            # Read from the child's file at the last restart: no settle backs it,
            # so its identity comes from the start record and ``result`` reads the
            # file again under the same rules (Codex #6).
            payload = {**_payload_of(_start_of(records)), "recovered": True}
        else:
            payload = _settled_payload(records)
        return ClientOutcome(
            status=status if status in ("done", "failed", "stopped") else "failed",
            brief=str(fact.get("brief", "")),
            payload=payload,
        )

    def render_notice(self, outcome: ClientOutcome) -> NoticeBody:
        payload = outcome.payload
        text = payload.get("text") if isinstance(payload.get("text"), str) else None
        agent_id = payload.get("agent_id") or payload.get("session_id") or payload.get("key")
        where = _where(payload)
        if text is None:
            text = outcome.brief or f"The agent ended {outcome.status}; no result was kept."
        if payload.get("recovered"):
            text = f"{RECOVERED_CAVEAT}\n\n{text}"
        body = _cut_body(text, agent_id=agent_id, where=where)
        next_action = None
        if isinstance(agent_id, str) and agent_id:
            # THE UNCUT FOOTER (Codex #12): the frame never cuts the next step,
            # so both pointers are here whatever the body's cut kept. The header
            # names the job's id, the call that spawned it the agent's: both
            # resolve, and the model is told so (verify N1).
            next_action = (
                f'Its whole result: agent(action="result", id="{child_line(agent_id)}")'
                + (f", or {where}" if where else "")
                + ". The job id above names the same agent; either id works in agent()."
            )
        return NoticeBody(body=body, next_action=next_action)

    async def finalize_at_placement(self, outcome: ClientOutcome, branch: list[Any]) -> str | None:
        """The resume hint, as the branch now current can honour it (contract §2.3: branch-scoped hints).

        An agent can be resumed only from a branch that holds its first run's
        START record and a lineage that chains to it (``find_resume_target``).
        A ``/tree`` move while it ran may have left the start on another branch
        and only its settle on this one; the hint is then checked by the
        resume's own record rules (:func:`resume_lineage_refusal`, Codex #9)
        and says so instead of naming a call that would be refused.
        """

        payload = outcome.payload
        session_id = payload.get("session_id")
        key = payload.get("key")
        profile = payload.get("profile")
        if not isinstance(session_id, str) or not isinstance(key, str):
            return None
        refusal = resume_lineage_refusal(branch, session_id)
        if refusal in (RESUME_UNKNOWN_ID, RESUME_FIRST_RUN_MISSING):
            return (
                "This agent's record is not on the conversation's current branch, so "
                "it cannot be resumed from here."
            )
        if refusal is not None:
            return None
        name = child_line(profile) if isinstance(profile, str) else "<profile>"
        return (
            'To continue this agent: agent(action="resume", '
            f'id="{child_line(session_id)}", profile="{name}", task=<its next instruction>).'
        )

    def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
        entry = self._jobs.info.get(snapshot.job_id)
        progress = snapshot.progress or {}
        tool = progress.get("tool")
        head = f"{snapshot.label} {entry.profile}" if entry is not None else snapshot.label
        rows = [f"{head} · {snapshot.title}"[: max(1, width)]]
        if isinstance(tool, str) and tool and snapshot.state not in TERMINAL_STATES:
            rows.append(f"  running {tool}"[: max(1, width)])
        if entry is not None and entry.agent_id:
            rows.append(f"  id {entry.agent_id}"[: max(1, width)])
        return rows

    def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None:
        text = self._held_text(job)
        if isinstance(text, Unreadable):
            return OutputSlice(text=f"Its result cannot be read: {text}.", offset=0)
        if text is None:
            if job.state not in TERMINAL_STATES:
                if job.job_id not in self._jobs.info:
                    return OutputSlice(text=_restored_unfinished(job), offset=0)
                # The state-aware sentence ``result`` gives (#749).
                return OutputSlice(text=_not_ended(job, self._jobs), offset=0)
            agent_id = job.payload.get("session_id") or job.payload.get("key") or job.job_id
            where = _where(job.payload)
            return OutputSlice(
                text=(
                    "This agent's result is not held in memory after a restart. Read it "
                    f'with agent(action="result", id="{child_line(str(agent_id))}")'
                    + (f", or {where}" if where else "")
                    + "."
                ),
                offset=0,
            )
        if _is_recovered(job):
            text = f"{RECOVERED_CAVEAT}\n\n{text}"
        return page(text, offset=offset, limit=limit)

    def _held_text(self, job: JobSnapshot) -> str | Unreadable | None:
        """The whole result: the live payload's, the run's file (#769), or — before the job has
        an outcome, the window between its run's return and its end — its runner's.

        An ended job's live payload always holds its text, so an ended job read
        without one is one read back from its records: only its file can answer
        for it, which is what lets the job service tell a faithful read-back.
        """

        text = job.payload.get("text")
        if isinstance(text, str):
            return text
        written = _read_result(self._session(), job.payload)
        if written is not None or job.state in TERMINAL_STATES:
            return written
        entry = self._jobs.info.get(job.job_id)
        return entry.text if entry is not None else None

    async def recover(
        self, facts: Mapping[str, Any], client_records: list[Mapping[str, Any]]
    ) -> Recovered | None:
        """A restart's verdict on a job started and not ended (contract §2.3, §2.4).

        1. The parent's own CONFIRMED settle for this job (its client record)
           decides first: that is the only evidence that may say ``done``.
        2. Without one, the child's file — only for the LATEST run of that file,
           and only while nothing holds the file's writer lock — gives its last
           answer as ``recovered`` ("aelix could not confirm it finished").
           A run that is not the latest is ``unresolved(cannot_bind_run)``.
        3. Anything else — no start record, a held lock, no answer yet — is
           ``None``: the service classifies from OS evidence, report-only.
        """

        settle = _settle_of(list(client_records))
        if settle is not None:
            status = _STATUS_OF.get(str(settle.get("status")), "failed")
            return Recovered(
                status=status,
                brief=f"recorded {settle.get('status')} before aelix restarted",
                payload=_payload_of(settle),
            )
        start = _start_of(list(client_records))
        if start is None:
            return None
        session = self._session()
        key = start.get("key")
        if session is None or not isinstance(key, str):
            return None
        answer = await recover_run_answer(session, key)
        if answer == CANNOT_BIND_RUN:
            return Recovered(status="unresolved", reason=CANNOT_BIND_RUN)
        if not isinstance(answer, RecoveredAnswer):
            return None
        if _lock_refusal(answer.path) is not None:
            # Something holds the child's file: it may still be running.
            return None
        return Recovered(
            status="recovered",
            brief="its last answer, read from its session; aelix could not confirm it finished",
            payload={
                **_payload_of(start),
                "text": _utf8_safe(answer.text),
                "rel": answer.rel,
                "recovered": True,
            },
        )

    def relation(self, facts: Mapping[str, Any]) -> RelationSpec:
        # Every delegated child is ``python -m aelix_coding_agent …``.
        return RelationSpec(argv_contains=("aelix_coding_agent",))

    def restored_note(self, facts: Mapping[str, Any]) -> str | None:
        """Nothing to add (#611 S2d): a restored agent's notice already says what a restart can."""

        return None


# === The client's own state, one per process =======================================


class DelegationJobs:
    """This process's background agents, by job id and by agent id.

    Held by the ONE ``AgentsExtension`` of the process, so it outlives every
    harness build as the job service does; the adapter of each build and every
    runner share it. In memory only: after a restart the ids are found again
    through the parent's records (:meth:`resolve`).
    """

    def __init__(self) -> None:
        self.info: dict[str, AgentJob] = {}
        self._by_agent: dict[str, str] = {}
        #: Consecutive waits that ended with nothing settled, this prompt (D1).
        self.empty_waits = 0

    def track(self, job_id: str, launch: _Launch) -> AgentJob:
        entry = self.info.get(job_id)
        if entry is None:
            entry = AgentJob(job_id=job_id, profile=launch.resolved.name)
            self.info[job_id] = entry
        return entry

    def name(self, entry: AgentJob) -> None:
        if entry.agent_id:
            self._by_agent[entry.agent_id] = entry.job_id
        if entry.key:
            self._by_agent[entry.key] = entry.job_id

    def job_id_for(self, agent_id: str) -> str | None:
        if agent_id in self.info:
            return agent_id
        return self._by_agent.get(agent_id)

    async def resolve(self, service: Any, agent_id: str, *, session: Any = None) -> str | None:
        """The job id ``agent_id`` names in THIS session: a job id, an agent id, or a key.

        The service's snapshot first, for a job id; then this process's maps;
        after a restart, the parent's own records: the start line that carries
        a ``job_id`` and names this child session or key.
        Whatever is found must be a job the service shows for the current
        session (``snapshot`` answers only its own, Codex #5).
        """

        # The job id a notice's header names, first: after a restart this
        # process holds no map for it, and the service's own snapshot of the
        # current session is the authority (Codex #7).
        snap = service.snapshot(agent_id)
        if snap is not None and snap.kind == KIND:
            return agent_id
        found = self.job_id_for(agent_id)
        if found is None and session is not None:
            with contextlib.suppress(Exception):
                for entry in await session.get_entries():
                    data = getattr(entry, "data", None)
                    if (
                        getattr(entry, "custom_type", None) != CHILD_SESSION_TYPE
                        or not isinstance(data, dict)
                        or not isinstance(data.get("job_id"), str)
                    ):
                        continue
                    child = data.get("child")
                    names = {data.get("key")}
                    if isinstance(child, dict):
                        names.add(child.get("session_id"))
                    if agent_id in names:
                        found = data["job_id"]
                        break
        if found is None:
            return None
        snap = service.snapshot(found)
        return found if snap is not None and snap.kind == KIND else None


# === The background actions ========================================================


def wait_bounds(requested: float | None) -> tuple[float, str | None]:
    """The seconds a ``wait`` runs for, and the sentence that says it was clamped."""

    if requested is None:
        return WAIT_DEFAULT_SECONDS, None
    if requested < WAIT_MIN_SECONDS:
        return WAIT_MIN_SECONDS, f"(raised to {int(WAIT_MIN_SECONDS)} s, the minimum)"
    if requested > WAIT_MAX_SECONDS:
        return WAIT_MAX_SECONDS, f"(cut to {int(WAIT_MAX_SECONDS)} s, the maximum)"
    return float(requested), None


def _settled(snap: JobSnapshot) -> bool:
    """Done for a ``wait``: ended, or ``unresolved`` (which a wait cannot outlast)."""

    return snap.state == "unresolved" or snap.state in TERMINAL_STATES


_NOTICE_SETTLE_SECONDS = 2.0
"""How long a ``wait`` that saw an agent end gives its notice to be posted, so
the notice is placed right after the wait's own result (``_complete`` writes the
outcome fact between the two)."""


async def wait_for_agents(
    service: Any, jobs: DelegationJobs, job_ids: list[str], *, requested: float | None
) -> tuple[str, bool]:
    """``agent(action="wait")``: the status lines, and whether this wait was EMPTY.

    Never stops anything and never returns a result: the notice is the one
    delivery, posted before the waiter wakes, so it is placed right after this
    call's result. Built on ``asyncio.wait`` with a timeout, never
    ``wait_for`` (the py3.11 extra-turn trap). Esc cancels the wait, not the
    child: nothing here owns the job.
    """

    seconds, clamped = wait_bounds(requested)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds

    def snaps() -> list[JobSnapshot]:
        return [s for s in (service.snapshot(j) for j in job_ids) if s is not None]

    while True:
        current = snaps()
        if not current or any(_settled(s) for s in current):
            break
        left = deadline - loop.time()
        if left <= 0:
            break
        changed = asyncio.ensure_future(service.changed())
        try:
            await asyncio.wait({changed}, timeout=left)
        finally:
            changed.cancel()
    current = snaps()
    finished = [s for s in current if _settled(s)]
    settle_by = loop.time() + _NOTICE_SETTLE_SECONDS
    while any(s.state in TERMINAL_STATES and s.notice_state == "none" for s in finished):
        left = min(settle_by, deadline + _NOTICE_SETTLE_SECONDS) - loop.time()
        if left <= 0:
            break
        changed = asyncio.ensure_future(service.changed())
        try:
            await asyncio.wait({changed}, timeout=left)
        finally:
            changed.cancel()
        current = snaps()
        finished = [s for s in current if _settled(s)]
    lines = [_status_line(jobs, s) for s in current]
    if clamped:
        lines.insert(0, f"Waited {int(seconds)} s {clamped}.")
    ended = [s for s in finished if s.state in TERMINAL_STATES]
    if not finished:
        lines.append(
            "Still running. Its result arrives as a notice when it finishes; you do "
            "not need to wait again."
        )
    elif ended and all(s.notice_state == "delivered" for s in ended):
        lines.append(
            'Its result was already given to you as a notice; agent(action="result", '
            "id=…) reads it again."
        )
    elif ended:
        lines.append(
            "The result of an agent that finished arrives as a notice right after this "
            'call; agent(action="result", id=…) reads it again.'
        )
    if len(ended) < len(finished):
        # ``unresolved`` ends a wait (a wait cannot outlast it), and no notice
        # is promised for it here: aelix cannot tell how it ended.
        lines.append(
            "An unresolved agent is not waited on: aelix cannot confirm how it ended, "
            "and waiting does not change that. status shows what aelix knows of it."
        )
    return "\n".join(lines), not finished


def _status_line(jobs: DelegationJobs, snap: JobSnapshot) -> str:
    entry = jobs.info.get(snap.job_id)
    agent_id = entry.agent_id if entry is not None and entry.agent_id else snap.job_id
    profile = entry.profile if entry is not None else snap.label
    parts = [f"{child_line(profile)} (id {child_line(agent_id)}): {snap.state}"]
    if snap.state not in TERMINAL_STATES and snap.elapsed is not None:
        parts.append(f"for {format_elapsed(snap.elapsed)}")
        tool = (snap.progress or {}).get("tool")
        if isinstance(tool, str) and tool:
            parts.append(f"running {child_line(tool)}")
        if snap.quiet:
            parts.append("quiet")
    elif snap.state in TERMINAL_STATES:
        if snap.brief:
            parts.append(child_line(snap.brief))
        parts.append("result delivered" if snap.notice_state == "delivered" else "result waiting")
    elif snap.state == "unresolved" and snap.unresolved_reason:
        parts.append(child_line(snap.unresolved_reason))
    return " — ".join(parts)


def _restored_unfinished(snap: JobSnapshot) -> str:
    """What is true of an agent a restart found unfinished (verify S1).

    This process did not start it, so it holds none of its processes, stops
    nothing it found (owner, 2026-11-07: a restart never kills) and has no
    notice coming for it — only the liveness loop's recheck, which records what
    the agent's kind can read once its processes are gone. When no recheck
    follows (``rechecking`` false: the kind's own ``unresolved`` with its
    processes confirmed gone, verify-r2 SF2), neither a check nor a notice is
    promised.

    Whether it may still run is what the service confirmed of its processes
    (``processes_gone``), not whether it is rechecked: a job whose records or
    adapter failed was never checked against the OS (#749).
    """

    reason = f" ({child_line(snap.unresolved_reason)})" if snap.unresolved_reason else ""
    head = (
        f"It is {snap.state}{reason}: aelix restarted after this agent started and "
        "cannot tell how it ended. aelix stops nothing it finds at a restart"
    )
    running = ", and its processes are gone" if snap.processes_gone else ", so it may still be running"
    return f"{head}{running}{_restored_watch(snap)}"


def _restored_watch(snap: JobSnapshot) -> str:
    """Whether aelix checks a restored agent again, and what that can bring (#749)."""

    if not snap.rechecking:
        return "; it is not watching this agent: no notice is promised for it."
    if snap.unresolved_reason == CANNOT_BIND_RUN:
        # The kind's own reason: a later run of the same child file stays in
        # the parent's records, so asking again reads the same.
        checks = "" if snap.processes_gone else "; aelix checks its processes until they are gone"
        return (
            f"{checks}. A later run of this agent wrote to the same session file, so aelix "
            "cannot read this run's end from it: no notice is promised for it."
        )
    if snap.processes_gone:
        return (
            "; aelix reads its records again, and what it can read of its end comes as a "
            "notice with a later message."
        )
    return (
        "; aelix keeps checking, and if it finds the agent's processes gone, what it can "
        "read of its end comes as a notice with a later message."
    )


def _not_ended(snap: JobSnapshot, jobs: DelegationJobs) -> str:
    """``result`` of an agent that has not ended: why there is no result, and whether one comes."""

    if snap.job_id not in jobs.info:
        return _restored_unfinished(snap) + " There is no result to read now."
    if snap.state == "unresolved" and not snap.rechecking:
        return _unwatched(snap)
    return f"It is {snap.state}; its result arrives as a notice when it finishes."


def _unwatched(snap: JobSnapshot) -> str:
    """An agent this process ran that nothing checks again — its runner's settle failed (#749)."""

    reason = f" ({child_line(snap.unresolved_reason)})" if snap.unresolved_reason else ""
    return (
        f"It is unresolved{reason}: aelix cannot tell how it ended and does not check it "
        "again, so no notice of its end is coming."
    )


def _result_tail(snap: JobSnapshot, *, partial: bool = False) -> str:
    """Where the result of an ended agent is: given, or coming as a notice (verify S2)."""

    if snap.notice_state == "delivered":
        return (
            'Its result was already given to you as a notice; agent(action="result", '
            "id=…) reads it again."
        )
    what = "Its result — partial — comes back" if partial else "Its result comes back"
    return f'{what} as a notice; agent(action="result", id=…) reads it now.'


def stop_text(
    before: JobSnapshot | None, after: JobSnapshot, jobs: DelegationJobs
) -> str:
    """What ``agent(action="stop")`` says, from the agent's state before and after the stop.

    True to what the service did (verify S1, S2): an agent this process did not
    start — a restart found it — is not stopped, and a notice is promised for it
    only while the service still checks it (``rechecking``); one that had already
    ended says where its result is.
    """

    if after.job_id not in jobs.info and after.state not in TERMINAL_STATES:
        return "Not stopped. " + _restored_unfinished(after)
    if before is not None and before.state in TERMINAL_STATES:
        return f"Nothing to stop: it already ended ({after.state}). " + _result_tail(after)
    if after.state in TERMINAL_STATES:
        return f"It is {after.state}. " + _result_tail(after, partial=after.state == "stopped")
    return _stopping(after)


def _stopping(after: JobSnapshot) -> str:
    """A stop of an agent this process runs that has not ended yet."""

    if after.state == "unresolved" and not after.rechecking:
        return "Asked to stop. " + _unwatched(after)
    return (
        f"Asked to stop; it is {after.state}, and aelix keeps stopping it. Its result "
        "comes back as a notice once it has stopped."
    )


def status_text(service: Any, jobs: DelegationJobs, job_ids: list[str]) -> str:
    snaps = [s for s in (service.snapshot(j) for j in job_ids) if s is not None]
    if not snaps:
        return "You have no background agents in this session."
    return "\n".join(_status_line(jobs, s) for s in snaps)


def session_agents(service: Any) -> list[str]:
    """Every background agent of the current session, oldest first."""

    return [s.job_id for s in service.snapshots() if s.kind == KIND]


def page(text: str, *, offset: int, limit: int) -> OutputSlice:
    """``text`` from byte ``offset``, at most ``limit`` bytes, cut at character boundaries.

    Paged by bytes as ``bash``'s file is (ADR-0297 §4, #770): ``next_offset``
    is the byte after the page, so ``job(action="result")`` and ``/jobs
    result`` — which pass ``continue_at`` back — read every byte once. A page
    never ends inside a character, and one with bytes after its offset takes
    at least one character, all of it, when ``limit`` is smaller than that
    character. ``text`` is a ``str``, so its bytes are whole UTF-8: there is no
    invalid byte and no cut character at its end.
    """

    raw = text.encode("utf-8")
    start = max(0, min(int(offset), len(raw)))
    # Never start inside a character: step forward over continuation bytes.
    while start < len(raw) and (raw[start] & 0xC0) == 0x80:
        start += 1
    end = _page_end(raw, start, limit)
    chunk = raw[start:end].decode("utf-8", errors="ignore")
    return OutputSlice(
        text=chunk, offset=start, total=len(raw), more=end < len(raw), next_offset=end
    )


def _page_end(raw: bytes, start: int, limit: int) -> int:
    """Where a page of ``raw`` from ``start`` ends: at most ``limit`` bytes, never inside a
    character, and past one whole character when ``limit`` is smaller than it (#770)."""

    end = min(len(raw), start + max(1, int(limit)))
    while end < len(raw) and (raw[end] & 0xC0) == 0x80:
        end -= 1
    if end == start < len(raw):
        # A limit smaller than the first character: that character, whole.
        end += 1
        while end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end += 1
    return end


async def result_text(
    service: Any, jobs: DelegationJobs, job_id: str, *, offset: int, session: Any
) -> tuple[str, bool]:
    """``agent(action="result")``: one page of the whole result, or why there is none; and whether it failed.

    A read: it acknowledges nothing and replaces no notice. In memory while
    this process ran the job; after a restart, the parent's settle and the
    child's file under the same rules as the restart's own recovery. A
    ``recovered`` agent's result leads with :data:`RECOVERED_CAVEAT`, and after
    a second restart its file is read again under those rules (Codex #6).
    """

    snap = service.snapshot(job_id)
    if snap is None:
        return f"No agent with id {job_id} in this session.", True
    if snap.state not in TERMINAL_STATES:
        return _not_ended(snap, jobs), True
    recovered = _is_recovered(snap)
    text = await _whole_text(snap, session)
    if isinstance(text, Unreadable):
        # Never the child's last answer in place of a file that cannot be read.
        return f"Its result cannot be read: {text}.", True
    if text is None:
        key = snap.payload.get("key")
        if not isinstance(key, str) or session is None:
            return "No result was recorded for it.", True
        if not recovered and snap.payload.get("envelope_status") is None and not _has_settle(snap):
            return (
                "aelix has no confirmed end of this run, so it does not read an answer "
                "from the agent's file as its result.",
                True,
            )
        answer = await recover_run_answer(session, key)
        if not isinstance(answer, RecoveredAnswer):
            return f"Its result cannot be read: {_READ_REASONS.get(answer, answer)}.", True
        if recovered and _lock_refusal(answer.path) is not None:
            # The restart's own rule: a held writer lock may be a live writer.
            return (
                "Its result cannot be read: something holds its session file, so it "
                "may still be running.",
                True,
            )
        said = _utf8_safe(answer.text)
        text = (
            said
            if recovered
            else "Its last answer, read from its session file after a restart:\n\n" + said
        )
    if recovered:
        text = f"{RECOVERED_CAVEAT}\n\n{text}"
    sliced = page(text, offset=offset, limit=RESULT_PAGE_BYTES)
    tail = (
        f'\n\n… more: agent(action="result", id="{job_id}", offset={sliced.continue_at})'
        if sliced.more
        else ""
    )
    return sliced.text + tail, False


_READ_REASONS: dict[str, str] = {
    "no_session_file": (
        "this conversation is not saved in aelix's sessions directory, so the agent's "
        "session file cannot be found from it"
    ),
    "unreadable": "its records could not be read",
    "no_start_record": "this conversation holds no start record for it",
    "no_child_file": "it has no session file of its own",
    CANNOT_BIND_RUN: (
        "a later run of this agent wrote to the same session file, so its last answer "
        "there may be that run's"
    ),
    "damaged": "its session record is damaged",
    "no_answer": "its session file holds no answer from it",
}
"""Each code :func:`~aelix_agents.child_session.recover_run_answer` returns, as
``result`` says it (#749): the model reads a sentence, never the code. A refused
file comes back as a sentence already (``child_session._as_read``)."""


async def _whole_text(snap: JobSnapshot, session: Any) -> str | Unreadable | None:
    """The whole result ``result`` reads of an ended agent: the live payload's, or its run's file (#769).

    Not the runner's text: ``result`` reads only an ended job, whose live
    payload holds it, so a job let go reads its file at once, as
    ``job(action="result")`` does, whether or not its runner was freed yet.
    """

    text = snap.payload.get("text")
    if isinstance(text, str):
        return text
    return await asyncio.to_thread(_read_result, session, snap.payload)


def _has_settle(snap: JobSnapshot) -> bool:
    """Whether the job's payload came from the parent's own settle (``_payload_of``)."""

    return snap.payload.get("settle_status") is not None


def _is_recovered(snap: JobSnapshot) -> bool:
    """Whether a restart READ this agent's end from its file, with no confirmed end (contract §2.4)."""

    return snap.state == "recovered" or bool(snap.payload.get("recovered"))


# === Helpers ===========================================================================


_LONE_SURROGATE = re.compile(r"[\ud800-\udfff]")


def _utf8_safe(text: str) -> str:
    """``text`` with each lone surrogate replaced by U+FFFD: what a child says, as the job layer holds it (#782).

    A lone surrogate (``"\\ud83d"``, half an emoji) is a legal ``str`` that a
    child's JSON stream and its session file can carry, and no UTF-8 encoder
    writes it. A background agent's text is cut by UTF-8 bytes for the notice's
    brief and body and paged by bytes, which raised ``UnicodeEncodeError`` on it
    — its job ended with no outcome and no notice, and ``result`` raised — and
    its result file refused it. It is made whole once, where the child's text enters this
    client: the run's result and brief, its progress (the tool it runs, its last
    line), and an answer read back from the child's file after a restart.
    Replaced, not dropped, as ``sendable()``
    replaces it in a chain step's ``{previous}``, so the reader sees that a
    character was there. Every other character is left as it is.
    """

    return _LONE_SURROGATE.sub("\ufffd", text)


def _result_text(result: SubagentResult) -> str:
    rendered = render_subagent_result(result)
    return "\n".join(getattr(block, "text", "") for block in rendered.content)


def _brief(result: SubagentResult) -> str:
    first = (result.summary or result.error or "").strip().splitlines()
    return child_line(first[0][:160]) if first else result.status


def _where(payload: Mapping[str, Any]) -> str | None:
    """The child's session file, named by its id — never by its path (ADR-0243 §5: a path
    would put a home directory and a user name into the model's context)."""

    session_id = payload.get("session_id")
    if isinstance(session_id, str) and session_id:
        return f"its delegated session file (Session: {child_line(session_id)})"
    return None


def _escaped_size(text: str) -> int:
    """``text``'s UTF-8 size once the frame escaped it (``jobs/notice.escape_body``)."""

    return len(escape_body(text).encode("utf-8"))


def _cut_body(text: str, *, agent_id: Any, where: str | None) -> str:
    """``text``, or its head cut so that, ESCAPED as the frame will, it fits :data:`NOTICE_BODY_MAX_BYTES`.

    The budget is of the escaped bytes (Codex #12): escaping a forged tag or
    header lengthens it, and a body measured before that would be cut again by
    the frame — with its pointers.
    """

    if _escaped_size(text) <= NOTICE_BODY_MAX_BYTES:
        return text
    pointer = (
        f'agent(action="result", id="{child_line(agent_id)}")'
        if isinstance(agent_id, str) and agent_id
        else 'agent(action="result")'
    )
    if where:
        pointer += f", or {where}"
    marker_room = 512
    budget = NOTICE_BODY_MAX_BYTES - marker_room
    kept = _cut_bytes(text, budget)
    while (over := _escaped_size(kept) - budget) > 0:
        kept = _cut_bytes(kept, len(kept.encode("utf-8")) - over)
    left = len(text.encode("utf-8")) - len(kept.encode("utf-8"))
    return f"{kept}\n… (cut here; {left} more bytes — the whole result: {pointer})"


def _cut_bytes(text: str, limit: int) -> str:
    return text.encode("utf-8")[: max(0, limit)].decode("utf-8", errors="ignore")


def _records_of(
    records: list[Mapping[str, Any]], phase: str | None, custom_type: str = CHILD_SESSION_TYPE
) -> Mapping[str, Any] | None:
    for record in reversed(records):
        if record.get("custom_type") == custom_type and record.get("phase") == phase:
            return record
    return None


def _settle_of(records: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return _records_of(records, "settle")


def _start_of(records: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return _records_of(records, "start")


def _settled_payload(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The settle's payload, with the run's result file when it wrote one (#769)."""

    payload = _payload_of(_settle_of(records))
    written = _records_of(records, None, RESULT_RECORD_TYPE)
    if written is not None:
        payload["result"] = {k: written.get(k) for k in ("v", "key", "bytes", "sha256")}
    return payload


def _payload_of(record: Mapping[str, Any] | None) -> dict[str, Any]:
    if record is None:
        return {}
    child = record.get("child")
    child = child if isinstance(child, Mapping) else {}
    payload: dict[str, Any] = {
        "key": record.get("key"),
        "session_id": child.get("session_id"),
        "rel": child.get("rel"),
        "profile": record.get("profile"),
        "agent_id": child.get("session_id") or record.get("key"),
    }
    if record.get("phase") == "settle":
        payload["settle_status"] = record.get("status")
    return payload


__all__ = [
    "CAPABILITIES",
    "KIND",
    "NOTICE_BODY_MAX_BYTES",
    "RECOVERED_CAVEAT",
    "RESULT_PAGE_BYTES",
    "SETTINGS_KEY",
    "AgentJob",
    "DelegationJobs",
    "DelegationKindAdapter",
    "DelegationRunner",
    "frozen_channel",
    "frozen_host",
    "page",
    "result_text",
    "session_agents",
    "status_text",
    "stop_text",
    "wait_bounds",
    "wait_for_agents",
]
