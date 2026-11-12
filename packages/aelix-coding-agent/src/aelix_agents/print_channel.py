"""The JSON one-shot delegation channel — ADR-0197 §(j)/(k).

argv, env, cwd, stdio, both pumps, the kill legs and the envelope hand-off.
There is no time limit on a delegation (#604, ADR-0276): the bounds below are on
waits that follow the child's own exit, never on the work.
Product-core contains no line of this: the 3-band rule (ADR-0008, amended by
ADR-0197) gives product-core the CONTRACT and this bundled extension every
process-touching statement, and ``tests/agents/test_p2_band_boundaries.py``
is the machine gate.

This file used to open by calling itself THE ONLY FILE IN AELIX THAT SPAWNS A
SUBAGENT. That stopped being true when :mod:`aelix_agents.rpc_channel` landed —
it is the second implementation of :class:`SubagentChannel`, it spawns too, and
it is in this band for the same reason. What remains true, and is the claim
that mattered, is that BOTH spawners live in this extension and product-core
contains no line of either.

Three defects this module exists to not have. All three were reproduced by
execution against this codebase before the design was settled; none of them is
stylistic.

1. **BOTH PIPES ARE PUMPED CONCURRENTLY, for the child's whole lifetime**
   (finding B2). The obvious schedule — drain stdout to EOF, then read stderr —
   deadlocks. Measured with a real child writing 1 MiB to stderr between two
   stdout lines: the probe hung for a full two-minute tool timeout, and even
   ``proc.kill(); await proc.wait()`` after an 8 s ``wait_for`` did not unwedge
   it, because the child was blocked in ``write(2)`` on a full stderr pipe and
   never reached a signal handler. A real aelix child writes plenty to stderr:
   ``modes/print_mode.py:518-519`` prints every caught exception there, the SIGTERM
   path emits a multi-line traceback, and provider SDK / httpx logging plus any
   extension ``print(..., file=sys.stderr)`` land in the same pipe. stderr goes
   into a BOUNDED ring (:class:`StderrRing`) because a chatty child must not be
   able to balloon parent memory either.

2. **``readline()`` IS NEVER USED** (finding B3). ``create_subprocess_exec``'s
   ``StreamReader`` defaults to ``limit=65536`` and ``readline()`` RAISES past
   it — and the oversize bytes stay in the buffer, so every SUBSEQUENT
   ``readline()`` raises too and the terminating ``agent_end`` is lost. Executed
   against a real 200 000-byte line::

       ValueError: Separator is not found, and chunk exceed the limit
       ValueError: Separator is found, but chunk is longer than limit

   200 KB is routine: one ``read`` of a large source file serialises into a
   ~207 KB ``message_end``, and ``message_end`` is the SOLE source of the
   summary and the usage. So the reader is opened with an explicit 8 MiB
   ``limit=``, the pump reads fixed-size chunks, and
   :class:`~aelix_agents.stream.LineAssembler` reassembles them — the only shape
   in which "drop the oversize line and resync" is implementable at all.

3. **THE REAPER SURVIVES CANCELLATION** (finding B1). See
   :mod:`aelix_agents.reaper`; the call site here runs it DETACHED and awaits it
   under ``asyncio.shield``, escalating eagerly when the awaiter is cancelled a
   second time.

And one artefact: the 0600 prompt file is unlinked on EVERY exit path — normal,
error, kill, spawn failure and cancellation — because the ``finally``
that removes it is synchronous (nothing to interrupt) and because the directory
is recorded on the :class:`RunningChild` registry row before the process starts,
so ``stop`` / ``stop_all`` / the extension teardown can remove it even when the
owning task was cancelled out of its own ``finally``. Since #719 the directory
of a run that got a process goes with the run's ONE cleanup task
(:func:`ensure_cleanup`), after its sweep read the launch ledger that may be kept
there; the file itself still goes in the ``finally``.

#719, #750 (ADR-0296): every POSIX delegated child is given a launch ledger and a
run token (:func:`_helper_tracking`); the run's cleanup task awaits the killed
child, stops what the ledger and the token name (:func:`sweep_helpers`), and is
joined by ``run``, ``stop``, ``stop_all`` and the CLI's last drain.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import math
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import aelix_coding_agent
from aelix_ai.utils import _process_identity
from aelix_ai.utils._child_output import decode_child_output
from aelix_ai.utils._process_tree import (
    ProcessTree,
    _retained_handle,
    containment_spawn_kwargs,
    wait_released,
)
from aelix_coding_agent.agents.resolver import (
    ModelPin,
    child_model_pin,
    inherits_parent_model,
    profile_to_argv,
)
from aelix_coding_agent.agents.sendable import unencodable_refusal
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.jobs.contract import JOB_INTERNAL_ENV
from aelix_coding_agent.subagent_contract import (
    DEPTH_ENV_VAR,
    MAX_SUBAGENT_DEPTH,
    SubagentState,
    subagent_depth,
)
from aelix_coding_agent.tools import ALL_TOOL_NAMES, launch_ledger

from aelix_agents.child_session import RESUME_CWD_GONE, ChildLimits, ResumeRefused
from aelix_agents.consent import contains_control_chars
from aelix_agents.envelope import DEFAULT_OUTPUT_CAP, build_result
from aelix_agents.posture import resume_clamp
from aelix_agents.prompt_file import PromptFile, remove_prompt_dir, write_prompt_file
from aelix_agents.reaper import (
    DEFAULT_GRACE_SECONDS,
    HeldWalk,
    WalkEntry,
    descendant_identities,
    hold_walk,
    kill_group_if_live,
    kill_tree,
    pdeathsig_preexec,
    reap,
)
from aelix_agents.stream import LineAssembler, _StreamState, reduce_line
from aelix_agents.trust import child_trust_argv

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.subagent_contract import (
        ResolvedProfile,
        SubagentOutcome,
        SubagentResult,
    )

logger = logging.getLogger(__name__)
"""Only one thing is logged from this module: a ``ProcessTree.attach`` that
failed. Everything else a delegation can get wrong is already a field on the
envelope, and an envelope is what a caller reads; a degraded containment is the
one failure with no envelope of its own, because the delegation itself succeeds
(§B.2)."""

AGENT_TOOL_NAME = "agent"
"""The delegation tool's name, declared HERE rather than in ``tool.py``.

``print_channel`` must subtract it from every child's grant (the second,
independent anti-nesting layer beside the depth env var), and it must be able
to do so without importing the tool module — which would invert the dependency
and drag the roster/description machinery into the spawn path."""

STREAM_LIMIT_BYTES = 8 * 1024 * 1024
"""``StreamReader`` buffer ceiling, passed EXPLICITLY to
``create_subprocess_exec``. It must exceed
:data:`~aelix_agents.stream.MAX_LINE_BYTES` so the per-line budget — which drops
and counts — is what fires on a pathological line, never the reader's own
``ValueError`` (finding B3)."""

STDERR_RING_BYTES = 64 * 1024
"""How much stderr is kept. The fallback chain only ever shows a TAIL, and the
tail is where the cause is: a Python traceback ends with the exception, and the
child's own error messages are the last thing written before it dies."""

READ_CHUNK_BYTES = 65536

POST_EXIT_DRAIN_SECONDS = 2.0
"""How long the pumps get to finish AFTER the child has exited.

Completion is raced against the child's EXIT rather than gated on pipe EOF
(P2 review, HIGH #4), so this is the whole budget a descendant that inherited
the child's stdout/stderr can cost us. Generous enough that an ordinary
kernel-buffered tail always lands, small enough that a pipe-holder converts a
finished delegation into a two-second delay instead of a ten-minute one."""

POST_EOF_EXIT_BOUND_SECONDS = 30.0
"""How long :meth:`PrintChannel._stream_and_reap` waits for ``returncode`` AFTER
both pipes have reached EOF, before it reaps the child (#604, ADR-0276 D4).

Both pumps at EOF means every writer closed fd 1 and fd 2 — normally the child is
exiting, and the gap to ``proc.returncode`` is the loop's child watcher,
milliseconds to tens of milliseconds. The child's own MCP and extension teardown
runs BEFORE that EOF (a child closes its stdio when its process ends), so it is
spent inside the main wait, which has no bound. What is left for this wait is a
child that closed its stdio and then wedged, and that must not hold the parent
open forever.

IT USED TO BORROW THE DELEGATION'S DEADLINE: ``max(remaining budget, 2 s)``, the
2 s being ``POST_EOF_EXIT_GRACE_SECONDS``. That floor existed because charging
the watcher's gap to the deadline reported COMPLETE runs as timeouts (P2 review,
MEDIUM #3; measured on a child answering at ~679 ms: ``686 ms -> timeout /
ok=False / summary='the complete answer'``). With no deadline left the wait needs
a bound of its own. 30 s is far above any watcher latency and short enough that
a wedged child costs half a minute rather than the session.

A child reaped here is reported with ``lingered=True``
(``envelope.build_result``): a stream that had already reached its terminator
stays ``ok`` with a note, and anything less is ``error``."""

EXIT_POLL_SECONDS = 0.05
"""How often :func:`_wait_for_exit` reads ``proc.returncode``.

An attribute read, not a syscall — ``asyncio``'s child watcher sets the field
from its own callback. 20 wakeups a second while a delegation is in flight, and
none at all otherwise."""

_PROC_DIR = Path("/proc")
"""Same source :mod:`aelix_agents.reaper` reads on Linux, different index: that
module walks ``PPid`` to find a live child's descendants (libproc on macOS, #691),
:func:`pipe_holder_pids` walks ``fd`` to find whoever holds a DEAD child's stdio."""

_RESUME_NEEDS_LIMITS = (
    "a resume plan carries no recorded limits, and a resumed agent is never "
    "launched with limits recomputed from the live parent (#610)"
)
"""The print channel's own belt (#610 D8). ``runtime._run`` always puts the
intersected limits on a resume plan; one that names a child to resume and
carries none would otherwise be launched with what a NEW delegation would get
now, which the first run does not bound at all."""


class CleanupOwner(Protocol):
    """Who ends a SERVICE-OWNED row's processes, tree and prompt directory (#611 S1c, ADR-0295).

    A background delegation is a job of its session (ADR-0293): the job
    service's barrier, not this channel, closes what the child left. So a row
    that carries an owner hands the owner, as soon as each exists, the prompt
    directory (:meth:`on_directory` — removed by the service after the barrier
    and the cleanup duty, never by ``run``'s ``finally``) and the process with
    its tree (:meth:`on_process` — the tree is closed only after the barrier
    confirmed its members gone); and every kill this module or the reaper would
    have performed itself goes through the owner instead (:meth:`stop_now`,
    :meth:`stop`), which collects the evidence the barrier confirms against
    BEFORE it signals anything (ADR-0293 §5). A row with no owner — every
    foreground delegation, every legacy path — is unchanged.

    Implemented in :mod:`aelix_agents.jobs_client`. Every method is total: it
    never raises into the channel.
    """

    def child_env(self) -> dict[str, str]:
        """What the owner adds to the child's environment (the protocol request)."""
        ...

    def on_directory(self, path: str) -> None: ...

    def on_process(self, row: RunningChild, proc: Any, *, session_path: str | None = None) -> None:
        """The process exists, ``row.tree`` attached: it is the job's now. ``session_path``
        is the child's session file, or ``None`` for a ``--no-session`` child."""
        ...

    def stop_now(self) -> None:
        """The hard leg at once, evidence first. Synchronous: it runs in ``except CancelledError``."""
        ...

    async def stop(self, grace: float) -> None:
        """Soft, ``grace``, then hard — evidence collected before the first signal."""
        ...


@dataclass
class RunningChild:
    """One live delegation, as the registry sees it.

    Mutable and shared: :meth:`PrintChannel.run` writes through it while
    :class:`~aelix_agents.runtime._SubagentRuntimeImpl` reads it for ``list`` /
    ``status`` and reaches into it for ``stop`` / ``stop_all``. Owning the row
    here (rather than in ``runtime.py``) keeps the one file that has a
    ``Process`` object as the one file that defines what may be done with it.
    """

    id: str
    profile: str
    state: SubagentState = "starting"
    started_at: float = field(default_factory=time.monotonic)
    proc: Any | None = None
    prompt: PromptFile | None = None
    """The 0700 temp dir, recorded BEFORE the process starts so a cancelled
    owner still leaves something the teardown paths can remove."""
    reaper_task: asyncio.Task[int] | None = None
    """Registry-owned so ``stop_all`` can JOIN it — the reaper is detached on
    purpose (finding B1) and would otherwise outlive session teardown, which
    ADR-0197 forbids."""
    eager_kill: bool = False
    escalate: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
    """Set beside :attr:`eager_kill` by ``_eager_abort`` and :func:`abort_child`
    (#691). ``eager_kill`` is read once, when a reaper is BUILT; a reaper already
    in its grace waits on this instead, so a second cancel or a ``stop`` that
    reuses that reaper escalates it at once rather than when its grace ends
    (finding B1)."""
    tree: ProcessTree | None = None
    """Attached right after the spawn, before the first ``await``. Shared with
    ``rpc_channel``, which borrows the client's tree instead of attaching one."""
    tree_owned: bool = False
    """Whether THIS row's owner may close :attr:`tree` (#220 §A.6). ``False`` for
    an rpc row: ``RpcClient.stop()`` owns that one, and closing it from here
    would disarm both kill legs while the client still owes a soft→grace→hard
    sequence.

    A DECLARATION OF OWNERSHIP, and today no code path can exercise it as
    ``False`` where it matters: the only closer is ``PrintChannel.run``'s
    ``finally``, which only ever sees rows ``PrintChannel.run`` itself created,
    and those set this and :attr:`tree` together. Deleting the ``and
    row.tree_owned`` half of that guard leaves the whole suite green (measured,
    #220 review round 2, MUT-5). It is kept as the cheap half of a contract the
    field exists to state — a second channel that ever borrows a tree onto a
    print row would need it — not because a current caller depends on it."""
    stopped: bool = False
    """Set by ``stop`` / ``stop_all``. It is what turns the resulting envelope
    from ``error`` into ``aborted``: the process died because we killed it."""
    stream: _StreamState = field(default_factory=_StreamState)
    session_id: str | None = None
    """The child SESSION this row's process runs in, once there is one (#610
    D12): set at the registry insert for a resume, and as soon as the file is
    allocated for a new delegation. ``runtime.resume_refusal`` refuses to resume
    a session a live row already runs — the one in-process double-resume guard;
    the child's own writer lock is the cross-process one."""
    owner: CleanupOwner | None = field(default=None, repr=False, compare=False)
    """The job service's side of a SERVICE-OWNED row (#611 S1c), else ``None``.

    Set by ``runtime._run`` before the row is published, for a background
    delegation only. See :class:`CleanupOwner` for what it changes: the
    prompt directory and the tree outlive :meth:`PrintChannel.run`, and every
    kill goes through it."""
    tracking: HelperTracking | None = field(default=None, repr=False, compare=False)
    """The run's launch ledger and marker token (#719), set by
    :meth:`PrintChannel.run` before the spawn on POSIX; ``None`` on win32, where
    the delegation's Job is what stops the helpers."""
    channel_cleans: bool = False
    """Whether :meth:`PrintChannel.run` spawned this row's process and so owes it
    ONE cleanup task (#719, #750): an rpc row, or a print row that never got a
    process, does not."""
    cleanup: CleanupDuty | None = field(default=None, repr=False, compare=False)
    """That task, once the run's first end created it (:func:`ensure_cleanup`)."""
    cleanups: dict[str, CleanupDuty] | None = field(default=None, repr=False, compare=False)
    """The runtime's registry the task is published to, in the same synchronous
    step that creates it (r3 §3.4, Codex N8). ``None`` for a row built without a
    runtime (the channel tests); the task is then held by this module."""


@dataclass(frozen=True)
class SpawnPlan:
    """Everything decided BEFORE a process exists.

    Frozen on purpose: consent (§(i)), the posture clamp (§(e)), profile
    resolution (§(f)) and cwd containment (§(l)) have all already happened by
    the time one of these is built, and none of them may be re-decided by the
    spawn path. In particular :attr:`permission_mode` is the CONSENT-RESOLVED
    posture — ``child_permission_mode(...)`` unless a human raised it at the
    dialog — never a raw clamp this module recomputes.
    """

    id: str
    resolved: ResolvedProfile
    task: str
    cwd: str
    parent_cwd: str
    permission_mode: PermissionMode
    parent_tools: tuple[str, ...] | None = None
    """The parent's LIVE grant (``ExtensionContext.get_active_tools``). ``None``
    means "the parent holds every built-in", which is what the harness's own
    ``None`` sentinel means before it is materialised."""
    parent_context_files: bool = True
    """Does the PARENT load auto-discovered ``AGENTS.md`` project context?

    ``False`` is the parent's ``--no-context-files`` / ``-nc``, read live off
    :attr:`~aelix_agents.runtime.SubagentHost.context_files`. Consumed by
    :func:`narrow_context_files`, which is where the reasoning lives.

    Defaults to ``True`` — every child before this field existed loaded context
    files unconditionally, measured: ``build_child_argv`` and
    ``build_rpc_child_argv`` emitted no ``--no-context-files`` for any parent
    state, and ``no_context_files`` appeared nowhere in this package."""
    parent_thinking: str | None = None
    """The parent's EFFECTIVE thinking level when the spawn was admitted (#354).

    Read off :attr:`~aelix_agents.runtime.SubagentHost.thinking` — the runtime
    host's live harness, not a hook snapshot — and already validated against
    ``VALID_THINKING_LEVELS`` by ``runtime._parent_thinking``. Consumed by
    :func:`inherit_thinking`, which is where the precedence lives.

    ``None`` means "no evidence" and keeps the child's argv exactly as it was
    before this field existed: no ``--thinking`` unless the profile declares
    one. That was every child before #354 — on a reasoning-mandatory model it
    started at ``off`` and its first request died with ``400 Reasoning is
    mandatory for this endpoint and cannot be disabled``."""
    parent_exclude_tools: tuple[str, ...] = ()
    """The parent's ``--exclude-tools`` patterns when the spawn was admitted
    (#510, ADR-0283), read off :attr:`~aelix_agents.runtime.SubagentHost
    .exclude_tools`. Both channels hand them to :func:`narrow_tools`, so the
    child removes the same tools from its registry — the print channel through
    :attr:`limits`, the rpc channel, which never reads ``limits``, from here
    (Codex F07)."""
    output_cap: int | None = None
    session_path: str | None = None
    """The ABSOLUTE path of the session file the parent allocated for this child
    (#199, ADR-0243), or ``None`` for a child that runs ``--no-session``.

    Filled by ``runtime._run`` with ``dataclasses.replace`` once the file exists
    — after the plan was built and published, because allocation is a write and
    the admission block before it must stay await-free. Both channels turn it
    into the child's session flag, and hand it to the envelope builder so a
    truncation marker only ever claims the rest is recorded when it is.

    For a resume (#610) it is the existing file the child is resumed on."""
    limits: ChildLimits | None = None
    """What bounds this child, decided ONCE before the spawn was admitted (#610
    D8): :func:`fresh_limits` for a new delegation, :func:`resume_limits` for a
    resume. :class:`PrintChannel` builds the argv's tools, context files,
    thinking and model flags from this and from nothing else, so the receipt
    that records it and the argv cannot disagree — the window between them, a
    ``/model`` landing after the receipt and before the argv, is closed.

    ``None`` — a plan built by hand, as the channel tests build them — makes the
    channel compute it with :func:`fresh_limits`, the same function."""
    dropped_tools: tuple[str, ...] = ()
    """The tools the child asked for and did not get, reported on the envelope —
    :func:`fresh_limits`' ``dropped``, or for a resume the recorded tools the
    intersection took away. Read only with :attr:`limits`."""
    resumes: str | None = None
    """The first run's spawn key when this plan RESUMES that child (#610), else
    ``None``. A channel that cannot honour recorded limits refuses such a plan
    (``rpc_channel``)."""


@dataclass(frozen=True)
class ToolNarrowing:
    """The child's tool set, and what it cost to get there."""

    profile: AgentProfile
    dropped: tuple[str, ...]


class SubagentChannel(Protocol):
    """What the runtime actually requires of a channel.

    Measured, not designed: an AST walk over ``runtime.py`` and ``extension.py``
    finds exactly ONE attribute read on the channel object — ``run``. The test
    suite already injects bare duck-typed stand-ins that satisfy it, so this
    Protocol is a zero-behaviour-change replacement for two concrete
    :class:`PrintChannel` annotations, not a new constraint.

    It lives HERE, beside :class:`SpawnPlan` and :class:`RunningChild`, so that
    naming the seam does not drag a particular implementation into the
    runtime's import graph.

    THE CONTRACT IS WIDER THAN THE SIGNATURE, and the rest of it was previously
    written down nowhere:

    * **``run`` must not raise except on cancellation.** The runtime's call site
      is ``try/finally`` with no ``except``, so on the single-task door an
      exception propagates into the harness's tool dispatcher.
    * **``run`` must leave ``child.state`` terminal** (``done`` / ``error`` /
      ``stopped``) before returning; the runtime only promotes a non-terminal
      state to ``error`` as a backstop.
    * **``run`` may mutate ``child.stream`` in place** — that is the object the
      progress taps publish from.
    * **``child.proc`` must hold a real process** if the channel wants ``stop``,
      ``stop_all`` and session teardown to reach its child, because all three
      bypass the channel entirely and hand ``child.proc`` to the reaper.

    ONE OPTIONAL ATTRIBUTE BESIDE ``run`` (#610 F8): ``resumes_agents``. A
    channel that launches a resume plan from the plan's recorded ``limits``
    says so with a true class attribute (:class:`PrintChannel`); the runtime
    reads it with ``getattr(..., False)`` before a resume is admitted
    (``runtime.resume_refusal``), so a channel that says nothing — a stand-in,
    :class:`~aelix_agents.rpc_channel.RpcChannel` (which says ``False``) — is
    refused a resume before the budget is charged or a record written. Not a
    Protocol member: a fresh delegation does not read it, and every channel
    that only runs those stays as it was.
    """

    async def run(
        self,
        plan: SpawnPlan,
        *,
        child: RunningChild | None = ...,
        on_stream: Callable[[_StreamState], None] | None = ...,
    ) -> SubagentResult: ...


class StderrRing:
    """The last :data:`STDERR_RING_BYTES` of the child's stderr.

    A ``bytearray`` window rather than a ``deque`` of lines: the trim has to be
    byte-exact so a child that writes one 100 MiB line is bounded too, and
    decoding is deferred to :meth:`text` so a multi-byte code point cut by the
    trim degrades to a replacement character instead of raising.
    """

    __slots__ = ("_buf", "_max", "_total")

    def __init__(self, *, max_bytes: int = STDERR_RING_BYTES) -> None:
        self._max = max_bytes
        self._buf = bytearray()
        self._total = 0

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._total += len(chunk)
        self._buf.extend(chunk)
        overflow = len(self._buf) - self._max
        if overflow > 0:
            del self._buf[:overflow]

    @property
    def total_bytes(self) -> int:
        """Everything the child wrote, including what the ring dropped."""

        return self._total

    def text(self) -> str:
        # BOTH ends (#239): ``del self._buf[:overflow]`` above is byte-exact, so
        # this window's head is routinely a cut multi-byte sequence, and the
        # tail is whatever a dying child got out. Without the head claim the
        # seam fails UTF-8 strict and a whole valid tail is offered to a code
        # page. This is one of the two sites that really has a cut head — the
        # exec sites' buffers are append-only and claim the tail alone.
        return decode_child_output(bytes(self._buf), ragged_head=True, ragged_tail=True)


def narrow_tools(
    profile: AgentProfile,
    parent_grant: Iterable[str] | None,
    parent_exclude: Iterable[str] = (),
) -> ToolNarrowing:
    """Intersect the profile's request with the parent's LIVE grant — §(l).

    Structural, not advisory: the child is launched with the result, so it
    cannot exceed the parent's authority even if its profile says otherwise and
    even if its model asks nicely.

    Three clauses, each closing a different hole:

    * ``& parent_grant`` — the whole point. A profile naming ``bash`` under a
      parent whose ``--tools`` excluded it gets no ``bash``.
    * ``& ALL_TOOL_NAMES`` — a profile may not name an extension or MCP tool the
      CHILD cannot build; the harness's tool validator would kill the child at
      startup with a message the parent would report as a delegation failure.
    * ``- {"agent"}`` — the second, independent anti-nesting layer. The depth env
      var (:data:`DEPTH_ENV_VAR`) already stops the extension loading, and
      ``bind_subagents`` refuses at depth; this makes it true of the argv too.

    ``tools=()`` is preserved, NOT collapsed: ``profile_to_argv`` emits
    ``--no-tools`` for it, whereas ``--tools ''`` parses to ``[]`` which
    ``_resolve_active_tools`` reads as falsy → ``None`` → EVERY tool active
    (``agents/profile.py:172-182`` documents the inversion).

    ``parent_exclude`` (#510) — the parent's ``--exclude-tools`` patterns, put
    on the child's profile as they are (owner decision 6), so the child's own
    registry lacks them too: ``--tools`` (a lock since #510) already keeps a
    withheld built-in off, and this keeps it out of the registry, MCP tools
    included.
    """

    grant = set(parent_grant) if parent_grant is not None else set(ALL_TOOL_NAMES)
    requested = set(profile.tools) if profile.tools is not None else set(grant)
    allowed = (requested & grant & set(ALL_TOOL_NAMES)) - {AGENT_TOOL_NAME}
    dropped = tuple(sorted(requested - allowed))
    return ToolNarrowing(
        profile=dataclasses.replace(
            profile,
            tools=tuple(sorted(allowed)),
            exclude_tools=tuple(dict.fromkeys(parent_exclude)),
        ),
        dropped=dropped,
    )


def narrow_context_files(
    profile: AgentProfile, parent_context_files: bool
) -> AgentProfile:
    """Clamp the child's ``AGENTS.md`` context to the parent's — the ``-nc`` inherit.

    Structural, exactly like :func:`narrow_tools`: the child is LAUNCHED with
    the result, so a parent started with ``--no-context-files`` / ``-nc`` cannot
    be overruled by the child's profile or by the child's model.

    ONE DIRECTION ONLY. ``parent_context_files=True`` returns the profile
    untouched, so a profile's own ``context_files: false`` still wins — a parent
    that loads project context does not force it onto a child that declined.

    THE BUG THIS CLOSES. Before it, ``no_context_files`` appeared nowhere in
    this package and both argv builders emitted no ``--no-context-files`` for
    any parent state (measured, both channels). So a user who typed ``-nc``
    precisely because they did not want a cloned repo's ``AGENTS.md`` steering
    the agent got exactly that the moment the agent delegated — silently, and
    from a child they never saw the command line of.

    WHY HERE RATHER THAN AS A FLAG APPENDED IN :func:`build_child_argv`:
    ``resolver.profile_to_flags`` already owns the single place a profile
    becomes ``--no-context-files`` (``resolver.py:560-561``), and that emission
    table is what keeps the argv channel and the in-process overlay from
    drifting. A second emission site would also put the flag on the argv TWICE
    whenever the profile itself declared ``context_files: false``.

    WHY NOT ALONGSIDE :func:`~aelix_agents.trust.child_trust_argv`: that
    function is the project-TRUST mechanism, and #121 decided context-file
    injection is trust-INDEPENDENT (pi ``docs/security.md:27``; pi added
    context files to its own trust manager at ``89a9220`` and removed them
    again four days later at ``5cb4f59``). Hanging ``-nc`` off it would
    re-couple exactly what that decision separated, and it would derive the
    answer from the child's cwd — which is not where the user's flag lives.
    This is parent-AUTHORITY inheritance, the same shape as ``parent_tools``
    and ``parent_model``, and it belongs on that path.

    NOT reflected in the ``/agents show`` dry run, which renders the UN-narrowed
    profile. Pre-existing and shared with :func:`narrow_tools` — the dry run has
    no live parent grant to intersect against either — not something introduced
    here.
    """

    if parent_context_files:
        return profile
    return dataclasses.replace(profile, context_files=False)


def inherit_thinking(profile: AgentProfile, parent_thinking: str | None) -> AgentProfile:
    """Give a child that inherits the parent's model the parent's level too (#354).

    THE BUG THIS CLOSES. A child's thinking level came from its profile's
    ``thinking:`` and nowhere else: neither argv builder emitted ``--thinking``
    for any parent state, and a headless child (``--mode json -p`` / ``--mode
    rpc``) did not seed ``defaultThinkingLevel`` either (#286, since fixed). So
    every child of a bundled profile — none of which declares ``thinking:`` —
    started at ``off``. On a model whose endpoint refuses to switch reasoning off
    (``openrouter/z-ai/glm-5.3-flash``, measured 2026-09-25) every delegation
    then died on its first request with ``400 Reasoning is mandatory for this
    endpoint and cannot be disabled``, while the parent, on the same model at
    ``high``, ran fine. The MODEL had been inherited since #304; the level had
    not.

    PRECEDENCE, top wins:

    1. the profile's own ``thinking:`` — an explicit statement about this child,
       and the only ``--thinking`` a child argv ever carried before;
    2. the parent's effective level (``parent_thinking``) — ONLY when the child
       also inherits the parent's model, i.e. the profile names neither
       ``model`` nor ``provider`` (:func:`~aelix_coding_agent.agents.resolver
       .inherits_parent_model`, the gate ``child_model_flags`` reads);
    3. nothing — ``None`` keeps the pre-#354 argv, so the child resolves its
       own level: the ``defaultThinkingLevel`` of its GLOBAL settings, clamped
       to its model, or the bottom rung ``off`` (clamped too) when that is unset
       (#286). The child is a ``--mode json -p`` launch, and every mode applies
       the setting since #286. Not a project's: :func:`~aelix_agents.trust
       .child_trust_argv` passes ``--no-approve`` whenever the project has a
       ``.aelix/settings.json``, so the child never loads that layer, and a
       ``defaultThinkingLevel`` there — which a parent in a trusted project runs
       at — does not reach it (a follow-up).

    The level travels WITH the model, as in pi's subagent example
    (``examples/extensions/subagent/index.ts``: ``inheritsDispatchConfig =
    !agent.model``, then ``--thinking ctx.thinkingLevel``). A level is a
    statement about a model — ``high`` on the parent's reasoning model says
    nothing about a profile's own non-reasoning or differently-priced one — so
    a profile that names its own model or provider gets no level from the
    parent. Without a ``thinking:`` of its own it runs at the global settings
    default clamped to its own model (#286), as a child of pi's subagent example
    runs at its own settings default (``createAgentSession``: per-model setting,
    then the default); a profile that wants another level states ``thinking:``.

    ``off`` is a level, not "unset": a parent at ``off`` hands its child
    ``--thinking off``, which pi does too (its ``"off"`` is a truthy string), and
    an explicit flag outranks the child's own settings default.

    WHY HERE, THE SAME WAY AS :func:`narrow_context_files`:
    ``resolver.profile_to_flags`` stays the ONE place a profile becomes
    ``--thinking``, so the argv channel and the in-process overlay keep one
    emission table and the flag can never appear twice. Both channels call this
    on the plan's value, so they cannot drift either.

    NOT reflected in the ``/agents show`` dry run, which renders the profile
    without a live parent — the same limit as ``narrow_tools`` and the
    inherited model.
    """

    if (
        profile.thinking is not None
        or parent_thinking is None
        or not inherits_parent_model(profile)
    ):
        return profile
    return dataclasses.replace(profile, thinking=parent_thinking)


def resolve_child_cwd(cwd: str | None, parent_cwd: str) -> str:
    """Contain the child's working directory inside the parent's — §(l).

    ``cwd`` is MODEL-CHOSEN on the ``agent`` tool door, so this is a security
    boundary and not a convenience: an out-of-tree directory is an ERROR, never
    a silent fallback to the parent's cwd, because a silent fallback would run
    the task somewhere the model did not ask for and nobody was told about.

    Composes with, and does not replace,
    :func:`~aelix_agents.trust.child_trust_argv`: this bounds WHERE a child may
    run, that bounds WHAT may execute once it gets there.

    IT ALSO REFUSES A CONTROL CHARACTER, AND THAT IS A SECOND, INDEPENDENT
    DEFENCE (F1 belt). POSIX permits every byte but ``/`` and NUL in a path
    component, so a model-chosen ``cwd`` may contain ``\\n`` and ``\\x1b`` — and
    the value this function returns is interpolated verbatim into the consent
    dialog, which ``ctx.ui.select`` both newline-splits into rows and ANSI-parses
    (``tui/context.py:150-228``). A 150-byte directory name was demonstrated to
    render a wholly forged, benign-looking dialog while the real permission row
    and the real tasks sat hidden behind SGR 8. ``consent.py``'s
    :func:`~aelix_agents.consent._sanitize_field` is the fix and is sufficient on
    its own; this is the belt, and it is also correct in its own right — a
    directory whose NAME is an escape sequence is not somewhere a delegated
    agent should be asked to run, whoever is going to render it later.
    """

    parent = Path(parent_cwd).resolve()
    if cwd is None:
        # The parent's OWN cwd, not a model-supplied string: nothing to validate
        # that the process is not already living with.
        return str(parent)
    # #627: the ``agent`` parser's sentence, for every door — an extension's
    # ``spawn(cwd=…)`` got the codec's or the OS's message instead. The OS's
    # RULE, though, not the text rule (``sendable.unencodable_character``):
    # ``resume_cwd`` brings a RECORDED cwd back through here, and on Linux a
    # directory whose name is not UTF-8 decodes to U+DC80-U+DCFF, which
    # ``os.fsencode`` takes back to its own bytes — refusing it refused every
    # resume under such a directory (review round 2). The model's own ``cwd``
    # has already met the text rule in the parser.
    refusal = unencodable_refusal(cwd, where="'cwd'", resend="the directory")
    if refusal is not None:
        raise ValueError(refusal)
    candidate = Path(cwd)
    if not candidate.is_absolute():
        candidate = Path(parent_cwd) / candidate
    resolved = candidate.resolve()
    # Checked on the RESOLVED path rather than on ``cwd``: a relative request is
    # joined to the parent's cwd above, and it is the resolved string that is
    # returned, stored on ``PendingSpawn`` and rendered.
    if contains_control_chars(str(resolved)):
        raise ValueError(
            f"cwd {cwd!r} contains a control character; a delegated agent may "
            "not be run in a directory whose name can steer the terminal."
        )
    if not resolved.is_relative_to(parent):
        raise ValueError(
            f"cwd {cwd!r} resolves outside the parent's working directory "
            f"({parent}); a delegated agent may only run inside it."
        )
    if not resolved.is_dir():
        raise ValueError(f"cwd {cwd!r} is not a directory")
    return str(resolved)


@dataclass(frozen=True)
class ComputedLimits:
    """One run's :class:`~aelix_agents.child_session.ChildLimits`, and what they cost."""

    limits: ChildLimits
    dropped: tuple[str, ...]
    """The tools the child asked for and does not get — the envelope's note."""


def profile_identity(source_path: object) -> str | None:
    """A profile's identity as a resume compares it: its file, as an absolute path.

    #610 D9 / F15. ``fresh_limits`` records it and ``runtime._same_identity``
    computes it again for the profile re-resolved at resume time, so both sides
    are spelled by this one function. Lexical (``os.path.abspath``), never
    symlink-resolved: discovery keeps a scanned profile's path as listed, and a
    resolved spelling would make every symlinked agents directory a different
    file. ``None`` for a profile with no file.
    """

    return os.path.abspath(str(source_path)) if source_path is not None else None


def fresh_limits(
    resolved: ResolvedProfile,
    *,
    granted_mode: PermissionMode,
    cwd: str,
    parent_tools: Iterable[str] | None,
    parent_context_files: bool,
    parent_thinking: str | None,
    parent_model: Any | None,
    model_registry: Any | None,
    parent_exclude_tools: Iterable[str] = (),
) -> ComputedLimits:
    """THE ONE COMPUTATION of what bounds a NEW delegation's child (#610 D8).

    The four decisions the channel used to make on its own, in the same
    order and by the same functions: :func:`narrow_tools`,
    :func:`narrow_context_files`, :func:`inherit_thinking`, and the model
    flags (``resolver.child_model_pin`` — :func:`~aelix_coding_agent.agents
    .resolver.child_model_flags`, read back as values). ``runtime._run`` calls
    it once per run, BEFORE the run is admitted, and hands the result to both
    the receipt and the plan; a plan built without it gets it from
    :meth:`PrintChannel.run`, through this same function.

    BEFORE ADMISSION, AND THAT IS EXCEPTION SAFETY (critique M4): the model
    pin reads the parent's live registry (``_pin_route``, ``resolve_route``),
    which can raise, and a raise between ``_run``'s registry insert and its
    ``try`` would leak the registry row — four of those and ``MAX_LIVE_CHILDREN``
    refuses every delegation for the rest of the session.

    ``granted_mode`` is recorded, not applied: the posture the child runs at is
    the plan's ``permission_mode``. Pure apart from what ``parent_model`` and
    ``model_registry`` do when read.
    """

    profile = resolved.profile
    narrowing = narrow_tools(profile, parent_tools, parent_exclude_tools)
    child_profile = inherit_thinking(
        narrow_context_files(narrowing.profile, parent_context_files),
        parent_thinking,
    )
    return ComputedLimits(
        limits=ChildLimits(
            granted_mode=PermissionMode(granted_mode),
            # Always a tuple here: ``narrow_tools`` never leaves ``None``.
            tools=tuple(child_profile.tools or ()),
            exclude_tools=child_profile.exclude_tools,
            context_files=child_profile.context_files,
            thinking=child_profile.thinking,
            model=child_model_pin(profile, parent_model, model_registry),
            cwd=cwd,
            # ``str`` whatever a host's ``ResolvedProfile`` carries: the record
            # is JSON, and an append that cannot serialise is swallowed — the
            # run would lose its start record, not its spawn. ABSOLUTE, as the
            # reader requires (#610 F15): a relative ``AELIX_CODING_AGENT_DIR``
            # gives a relative ``source_path``, and ``runtime._same_identity``
            # compares the same spelling (``profile_identity``).
            profile_source=profile_identity(resolved.source_path),
            profile_scope=str(resolved.scope),
        ),
        dropped=narrowing.dropped,
    )


def resume_cwd(recorded: str, parent_cwd: str) -> str:
    """The directory a resumed child runs in, or :class:`ResumeRefused`.

    #610 D9, F5. The recorded one, re-contained in the parent's directory now by
    :func:`resolve_child_cwd`'s rules — outside it, or gone, is refused — and
    still THAT directory. The recorded path is canonical: every door hands
    ``_run`` what :func:`resolve_child_cwd` returned, which is ``Path.resolve()``'s
    answer. So a path that now resolves to anything else (the directory renamed
    and a symlink to a sibling left under its old name) is refused rather than
    followed: containment alone is not identity, and the sibling is inside the
    parent too. One function for the hook's check (``runtime.resume_refusal``)
    and the launch (:func:`resume_limits`), so the two cannot disagree.
    """

    try:
        cwd = resolve_child_cwd(recorded, parent_cwd)
    except ValueError:
        raise ResumeRefused(RESUME_CWD_GONE) from None
    if cwd != recorded:
        raise ResumeRefused(RESUME_CWD_GONE)
    return cwd


def resume_limits(
    stored: ChildLimits, fresh: ChildLimits, *, parent_cwd: str
) -> ComputedLimits:
    """What bounds a RESUMED child: its first run's limits ∩ a new delegation's now.

    #610 D9. ``stored`` is the FIRST run's recorded limits — the ceiling, never
    the last resume's (a resume narrowed because the parent was briefly in
    ``plan`` must not narrow the agent for good). ``fresh`` is
    :func:`fresh_limits` for the parent as it is now, with the profile
    re-resolved by its recorded name. Field by field:

    * posture — :func:`~aelix_agents.posture.resume_clamp` of the recorded
      grant and the one given now;
    * tools — the recorded list, in its order, without the ones a new
      delegation would not get now; none left is ``--no-tools``;
    * exclusions (#510) — the recorded patterns and the parent's now, both:
      a resume never runs with a tool either run removed;
    * context files — on only if both say on;
    * thinking and the model — the recorded ones, PINNED rather than
      intersected: neither is an authority, and re-deriving the model from a
      parent whose ``/model`` moved would switch the resumed child's model
      (Codex #26718 / #36433);
    * cwd — :func:`resume_cwd`: the recorded directory, still that directory
      and still inside the parent's. Otherwise refused.

    ``dropped`` is the recorded tools the intersection took away (D9) — what
    the resumed child had and has no more, not what its profile asks for.
    Raises :class:`~aelix_agents.child_session.ResumeRefused` with
    ``RESUME_CWD_GONE`` and nothing else.
    """

    cwd = resume_cwd(stored.cwd, parent_cwd)
    allowed = set(fresh.tools)
    return ComputedLimits(
        limits=ChildLimits(
            granted_mode=resume_clamp(stored.granted_mode, fresh.granted_mode),
            tools=tuple(tool for tool in stored.tools if tool in allowed),
            exclude_tools=tuple(dict.fromkeys((*stored.exclude_tools, *fresh.exclude_tools))),
            context_files=stored.context_files and fresh.context_files,
            thinking=stored.thinking,
            model=stored.model,
            cwd=cwd,
            profile_source=stored.profile_source,
            profile_scope=stored.profile_scope,
        ),
        dropped=tuple(tool for tool in stored.tools if tool not in allowed),
    )


def limited_profile(profile: AgentProfile, limits: ChildLimits) -> AgentProfile:
    """``profile`` carrying ``limits``' tools, context files and thinking level.

    What :func:`build_child_argv` renders. ``resolver.profile_to_flags`` stays
    the one place a profile becomes ``--tools`` / ``--no-context-files`` /
    ``--thinking``; the model flags come from ``limits.model`` alone, so the
    profile's own ``model`` / ``provider`` are not read. Everything else — the
    system prompt, skills, extensions, the output cap — is the profile's own,
    as it is now (#610 D9: a profile file's identity is its path).
    """

    return dataclasses.replace(
        profile,
        tools=limits.tools,
        exclude_tools=limits.exclude_tools,
        context_files=limits.context_files,
        thinking=limits.thinking,
    )


def build_child_argv(
    child_profile: AgentProfile,
    *,
    prompt_path: str,
    task: str,
    permission_mode: PermissionMode,
    child_cwd: str,
    parent_cwd: str,
    parent_model: Any | None = None,
    session_path: str | None = None,
    model_registry: Any | None = None,
    model_pin: ModelPin | None = None,
) -> list[str]:
    """The child's exact command line — §(l).

    ``[sys.executable, "-P", "-m", "aelix_coding_agent", …]`` and nothing else.
    ``-P`` (#641) keeps the child's cwd — the project — off ``sys.path``:
    ``python -m`` puts it at ``sys.path[0]``, so a project file named like an
    installed extension module (a global pack's ``[plugin.entry] python``, an
    entry point's module) was imported in its place, whatever the project's trust.
    Python 3.11 is the floor, so the flag is always there. Specifically NOT
    ``-m aelix``, which ``rpc/rpc_client.py:1431`` does and which
    is a live bug (``aelix`` is the umbrella meta-package demo), and NOT the
    ``aelix`` console script, which in a worktree resolves to the OTHER tree's
    editable install.

    ``profile_to_argv`` supplies ``--mode json -p``, the session flag and the
    trailing ``f"Task: {task}"``. THE ``"Task: "`` PREFIX IS LOAD-BEARING and
    must not be stripped: ``args.py`` swallows an unrecognised ``--`` token into
    ``parsed.unknown_flags`` with NO diagnostic, so a bare task beginning with
    ``--`` would silently become a flag and the child would run with an empty
    prompt.

    ``session_path`` (#199) is the child's own session file, allocated by the
    parent beside its own file (``child_session.py``) and passed as ``--session
    <abs path>``; ``None`` keeps ``--no-session``. Nothing forwards
    ``--session-dir``: the file is chosen here, so the child never picks a place
    in any sessions root a picker scans.

    ``child_trust_argv`` may legitimately return ``[]`` (§(g) clause 1).
    ``/agents show`` renders the profile's flags only (``profile_to_flags``):
    the session flag, ``--permission-mode``, the trust flags and ``--no-agents``
    are spawn-time state a dry run cannot know. ``--no-agents`` is
    belt-and-braces with :data:`DEPTH_ENV_VAR`: the env var stops the extension
    loading, the flag stops the settings gate turning it back on.

    ``parent_model`` is the parent's LIVE effective model (``SubagentHost.model``
    — the runtime host's current harness, NOT ``ExtensionContext.model``, which
    is a per-hook snapshot; #304), forwarded only when the profile declares no
    model of its own (``resolver``'s emission table owns that rule). Without it
    a parent launched with ``--model`` on argv spawns a child with no model at
    all: the flag is run scope, the bundled profiles declare none, and nothing
    in between persists it.

    ``--no-context-files`` does NOT appear here even though the parent's ``-nc``
    is inherited (#121): it rides in on ``child_profile.context_files``, which
    :func:`narrow_context_files` clamps before the argv is built, so
    ``resolver.profile_to_flags`` stays the one place that emits it.

    ``model_registry`` is the parent's LIVE registry (#344): a ``--model`` on a
    models.json or extension provider is emitted as ``--model <id> --provider
    <provider>`` (the child loads no extensions by default); one a credential
    decided is not — the child inherits it and the .env record (ADR-0250).
    Left to its own cascade it re-derived an extension route through OpenRouter.

    ``model_pin`` (#610 D8) is that decision ALREADY MADE — the run's
    ``ChildLimits.model`` — and when it is given it is the model flags, and
    ``parent_model`` / ``model_registry`` are not read. :class:`PrintChannel`
    always passes it; the two live inputs remain for callers that build an argv
    from a profile alone.
    """

    return [
        sys.executable,
        "-P",
        "-m",
        "aelix_coding_agent",
        *profile_to_argv(
            child_profile,
            prompt_path=prompt_path,
            oneshot=True,
            task=task,
            parent_model=parent_model,
            session_path=session_path,
            model_registry=model_registry,
            model_pin=model_pin,
        ),
        "--permission-mode",
        permission_mode.value,
        *child_trust_argv(Path(child_cwd), Path(parent_cwd)),
        "--no-agents",
    ]


def build_child_env(
    profile: AgentProfile, *, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The child's environment — §(l).

    Inherited wholesale and then amended in six places. What is NOT touched is
    as deliberate as what is: ``AELIX_CODING_AGENT_DIR``, ``AELIX_SETTINGS_PATH``,
    ``AELIX_AUTH_PATH``, ``XDG_CONFIG_HOME``, ``PI_OFFLINE`` and every provider
    API key all pass through, because a child that cannot authenticate is a
    child that cannot do the work it was delegated.
    """

    env = dict(os.environ if base is None else base)

    # The depth gate. INERT at MAX_SUBAGENT_DEPTH == 1 — both branches yield 1,
    # and ``agents/profile.py:232`` defaults ``role`` to ``"leaf"`` — but wired
    # now so P3 only has to raise the cap. Computed from the INHERITED value
    # before the key is overwritten.
    depth = (
        MAX_SUBAGENT_DEPTH
        if profile.role == "leaf"
        else subagent_depth(env) + 1
    )
    env[DEPTH_ENV_VAR] = str(depth)

    # Belt-and-braces with ``stdin=DEVNULL``. An INHERITED ``"0"`` means "wait
    # forever" (``cli/entry.py:372-381``), and a child that waits forever on a
    # stdin nobody will ever write to is a delegation that never ends on its
    # own — there is no time limit to end it (#604).
    env["AELIX_STDIN_TIMEOUT"] = "1"

    # Otherwise every child fans out its own copy of every configured MCP
    # server — N delegations become N × M processes, none of which the parent
    # tracks.
    env.pop("AELIX_MCP_CONFIG", None)
    # ...and what this launch's mcp.json turned off or claimed goes along by
    # NAME, so the child — which reads its own file, usually untrusted — never
    # starts a server the parent's config kept off (#512, ADR-0282 D9). Names
    # only: the child can use them to suppress and for nothing else.
    from aelix_coding_agent.core.mcp_servers import pass_mcp_names_to_child

    pass_mcp_names_to_child(env)
    # ...and so does what this launch's ``extensions`` settings turned off (#25
    # verify V5): a child with ``inherit_extensions: true`` reads its own
    # settings, usually untrusted, and must not import an extension its parent
    # kept off. Suppress only, as the MCP names above.
    from aelix_coding_agent.core.extension_selection import pass_extension_off_to_child

    pass_extension_off_to_child(env)

    # Closes the worktree/venv trap deterministically: the parent may be running
    # from a source tree that is on ITS ``sys.path`` only because of how it was
    # launched, and ``sys.executable -m aelix_coding_agent`` in a fresh process
    # would then fail to import at all.
    root = str(Path(aelix_coding_agent.__file__).resolve().parents[1])
    existing = env.get("PYTHONPATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if root not in parts:
        parts.insert(0, root)
    # Rebuilt from ``parts`` EVERY time, also when the root was already listed:
    # an empty segment (``'<root>:'``) is the cwd — the project — on the child's
    # ``sys.path`` even under ``-P``, and a project ``sitecustomize.py`` then ran
    # at the child's startup (#641 verify V4).
    env["PYTHONPATH"] = os.pathsep.join(parts)

    return env


def apply_cost_fallback(state: _StreamState, registry: Any | None) -> bool:
    """Fill :attr:`_StreamState.cost` when the child's adapter reported none.

    ADR-0198 §D2 rule 9. ``usage["cost"]["total"]`` is already summed by
    :func:`~aelix_agents.stream.reduce_line`, but openrouter and
    openai-completions emit no ``cost`` key at all, so this fallback is the
    COMMON path rather than the exotic one. It lives here and not in
    ``stream.py`` because it needs a model-registry lookup — disk I/O, which a
    pure reducer may not do.

    RETURNS WHETHER IT PRICED THE RUN (#199, A.3b), and records the same fact on
    :attr:`_StreamState.cost_priced`. It used to return ``None`` on every path,
    so "the registry had no such model" and "the registry priced it at $0.00" (a
    ``:free`` model) both left ``cost == 0.0`` and nobody could tell a free run
    from an unpriced one — which is exactly what a recorded ``cost_known`` has
    to tell apart. A run that already carries a cost is not re-priced, and
    answers ``False``: the fallback did nothing.

    Synchronous and total: ``runtime._run`` calls it once more on a cancelled or
    errored run, which never reached an envelope, so its spend is priced too.

    Best-effort by construction: a delegation must never fail because a price
    could not be looked up.
    """

    if state.cost or registry is None or not state.provider or not state.model:
        return False
    try:
        from aelix_ai.models import calculate_cost
        from aelix_ai.streaming import Usage

        model = registry.find(state.provider, state.model)
        if model is None:
            return False
        usage = Usage(
            input=state.input,
            output=state.output,
            cache_read=state.cache_read,
            cache_write=state.cache_write,
        )
        price = float(calculate_cost(model, usage).total)
    except Exception:  # noqa: BLE001 — a price is never worth failing a run over
        return False
    if not math.isfinite(price) or price < 0:
        return False
    state.cost = price
    state.cost_priced = True
    return True


async def _pump_stdout(
    reader: Any,
    assembler: LineAssembler,
    state: _StreamState,
    on_line: Callable[[_StreamState], None] | None,
) -> None:
    """Chunk → lines → reduced state, for the child's whole lifetime.

    ``reader.read(n)``, NEVER ``reader.readline()`` — see the module docstring
    (finding B3). ``read`` has no separator to fail to find, so it cannot raise
    the limit error and cannot wedge the stream.

    ``on_line`` is the progress tap. Its exceptions are swallowed: a broken
    subscriber must not be able to abort a delegation that is otherwise
    succeeding, and the alternative (a raise inside the pump) would leave the
    stderr pump running against a stdout reader that stopped — the very
    deadlock shape this design exists to avoid.

    ``reduce_line`` IS WRAPPED THE SAME WAY, and that is not belt-and-braces
    paranoia (P2 review, HIGH #3). ``reduce_line`` documents "NEVER raises" and
    once did not: one child stdout line carrying ``"usage": {"input": Infinity}``
    — legal JSON that CPython both emits and accepts — reached ``int(inf)``,
    raised ``OverflowError`` out of this pump, escaped the gather and the
    ``wait_for`` (which catches only ``TimeoutError``), and left ``run`` with no
    reaper ever started and the registry row already popped: an un-reapable
    session leader holding the parent's API keys. The reducer is fixed AND the
    pump refuses to die for a line, because "a malformed line kills the process
    tree" must not be one refactor away from being true again.
    """

    def _reduce(line: str) -> None:
        # Contract violation, not flow control — hence swallow-and-continue
        # rather than a counter: a line the reducer cannot fold is worth exactly
        # as much as a line it silently skips, and the run's other lines are
        # worth the whole delegation.
        with contextlib.suppress(Exception):
            reduce_line(state, line)
        if on_line is not None:
            with contextlib.suppress(Exception):
                on_line(state)

    while True:
        chunk = await reader.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        for line in assembler.feed(chunk):
            _reduce(line)
    # A child that dies mid-line (SIGKILL, a crash) leaves bytes with no
    # terminator; the last thing a dying child wrote is often the most
    # informative line in the stream.
    for line in assembler.flush():
        _reduce(line)


def _swallow(future: asyncio.Future[Any]) -> None:
    """Retrieve a future's outcome so asyncio stops complaining about it.

    The pump gather is waited on only through ``asyncio.wait``, so on the abort
    path it finishes cancelled with nobody holding a reference. Without this
    callback asyncio logs a "exception was never retrieved" traceback for a
    shutdown that went exactly as designed, and that noise is indistinguishable
    from a real one.
    """

    with contextlib.suppress(BaseException):
        future.exception()


async def _pump_stderr(reader: Any, ring: StderrRing) -> None:
    """Drain stderr into the bounded ring, concurrently with stdout (B2)."""

    while True:
        chunk = await reader.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        ring.feed(chunk)


async def _wait_for_exit(proc: Any, *, poll: float = EXIT_POLL_SECONDS) -> int:
    """Resolve when the CHILD is gone — not when its pipes close.

    ``asyncio.subprocess.Process.wait()`` cannot answer this question, and that
    is the whole reason completion was mis-gated (P2 review, HIGH #4). Measured:
    a child that forks a stdio-inheriting grandchild and then exits 0 leaves
    ``proc.returncode == 0`` immediately — the child watcher sets it from
    ``_process_exited`` — while ``await proc.wait()`` does NOT resolve, because
    ``BaseSubprocessTransport._try_finish`` only wakes the exit waiters once
    every pipe is ``disconnected``::

        proc.wait() DID NOT RESOLVE in 5s; returncode=0

    So racing ``proc.wait()`` against the pumps would race pipe EOF against
    pipe EOF. ``returncode`` is the independent signal, and polling it is an
    attribute read (see :data:`EXIT_POLL_SECONDS`). That measurement is CPython
    up to 3.13.14: from 3.13.15 (gh-119710) ``proc.wait()`` resolves at the exit
    too, and this poll answers the same on both (#192). The reaper's grace needs
    the OLD meaning and gets it from ``wait_released``.
    """

    while getattr(proc, "returncode", None) is None:
        await asyncio.sleep(poll)
    return int(proc.returncode)


def _child_pipe_links(proc: Any) -> set[str]:
    """``pipe:[<inode>]`` — how ``/proc/<pid>/fd`` spells the child's stdio.

    Both ends of a pipe share one inode, so any process still holding the WRITE
    end shows the same link the parent's READ end has. That makes "who is
    keeping this delegation's stdout open" an exact question with an exact
    answer, taken at the moment it is asked — no snapshot to go stale and no pid
    to be recycled underneath us (which is the hazard ``reaper.py``'s module
    docstring already has to reason about).

    ``proc._transport`` is private; every step is guarded and an empty result
    simply means the caller falls back to killing nothing, which is what it did
    before this existed.
    """

    transport = getattr(proc, "_transport", None)
    if transport is None:
        return set()
    links: set[str] = set()
    for fd in (1, 2):
        try:
            pipe = transport.get_pipe_transport(fd).get_extra_info("pipe")
            links.add(f"pipe:[{os.fstat(pipe.fileno()).st_ino}]")
        except Exception:  # noqa: BLE001, PERF203 — best effort, per fd
            continue
    return links


def pipe_holder_pids(links: set[str]) -> list[int]:
    """Every process other than us holding one of ``links`` open.

    The counterpart to :func:`~aelix_agents.reaper.descendant_pids`, for the one
    case that walk cannot serve: the child has already exited, so everything it
    left behind has been reparented and no ``PPid`` chain leads back to it — yet
    something is still holding fd 1 or fd 2, which is precisely why we are
    asking. ``mcp/client.py:232`` calls ``stdio_client`` with no ``errlog`` and
    the SDK defaults to ``sys.stderr``, so every stdio MCP server a child
    launches is one of these.

    Best-effort by construction: no ``/proc`` (macOS, Windows) → ``[]``, and a
    pid belonging to another user raises ``PermissionError`` on its ``fd``
    directory and is skipped — it cannot be holding our pipe anyway.
    """

    return [holder.pid for holder in pipe_holders(links)]


def pipe_holders(links: set[str]) -> list[WalkEntry]:
    """:func:`pipe_holder_pids`, each with the start time read when it was found (#611 S2a).

    ``kill_tree`` signals a holder only while its number still has that start
    time, so a squatter that exits and hands its number on before the kill is
    not replaced by the process that took it. The time is read after the ``fd``
    directory opened and before its links are read; a holder whose time cannot
    be read is named with none, and is not signalled.
    """

    if not links or not _PROC_DIR.is_dir():
        return []
    me = os.getpid()
    holders: list[WalkEntry] = []
    try:
        entries = list(_PROC_DIR.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:
            # Exited between the listing and the read, or somebody else's.
            continue
        stamp = _process_identity.process_started(pid)
        for fd in fds:
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if link in links:
                holders.append(WalkEntry(pid, stamp, None))
                break
    return holders


async def abort_child(
    child: RunningChild, *, grace: float = DEFAULT_GRACE_SECONDS
) -> None:
    """Kill ``child`` now — the ``stop`` / ``stop_all`` / teardown entry point.

    Marks the row so the owning :meth:`PrintChannel.run` reports ``aborted``
    rather than ``error`` (the process died because we killed it, which is a
    different fact from the process crashing), then joins the reaper under
    ``asyncio.shield`` so a cancelled ``stop`` still leaves the kill running.

    Idempotent and safe before the process exists: a spawn that was declined or
    that died in ``create_subprocess_exec`` still has a temp directory to
    remove.
    """

    child.stopped = True
    child.eager_kill = True
    # A reaper ``_reap`` already started is in its grace and is reused below;
    # this is what makes it stop waiting (#691).
    child.escalate.set()
    proc = child.proc
    if proc is None:
        # A stop that arrives BEFORE the process exists (the registry row is
        # published as soon as the spawn is accepted, which is strictly earlier).
        # ``stopped`` is the whole mechanism: ``PrintChannel.run`` re-reads it
        # immediately before AND immediately after ``create_subprocess_exec``, so
        # the child is either never started or killed the instant it is.
        #
        # The temp prompt directory is deliberately NOT removed here: the owning
        # ``run`` may be between ``write_prompt_file`` and the spawn, and yanking
        # the file out from under a child that is about to read it turns a clean
        # abort into a confusing startup failure. Its three owners are the run's
        # own ``finally``, ``stop_all``'s sweep, and the extension teardown.
        child.state = "stopped"
        return
    task = child.reaper_task
    if task is None or task.done():
        task = asyncio.ensure_future(
            reap(
                proc,
                grace=grace,
                eager_kill=True,
                descendants=None,
                # The row, not a captured client: this is the door ``stop`` /
                # ``stop_all`` / the extension teardown all use, and it is also
                # the door that CREATES the reaper task ``_reap`` later reuses —
                # so a tree passed only at ``_reap`` would be discarded on the
                # one path a human drives. ``None`` after ``run``'s ``finally``
                # closed it, which is exactly the fallback ``reap`` wants.
                tree=child.tree,
                # A service-owned row's kill is its owner's (#611 S1c): ``reap``
                # then signals nothing itself and waits for the process.
                owner=child.owner,
            )
        )
        child.reaper_task = task
    # A stop is one of the run's ends (#719, #750): its cleanup task exists and
    # is in the runtime's registry from here, before any ``await``, so a
    # ``stop_all`` that pops this row still has the task to join (Codex N8).
    ensure_cleanup(child, grace=grace)
    # ``Exception`` only: a ``CancelledError`` here means the CALLER is being
    # torn down, and it must propagate — the reaper itself is shielded and
    # keeps going regardless.
    with contextlib.suppress(Exception):
        await asyncio.shield(task)


# === #719, #750: what a delegated agent's bash left, and one cleanup per run ===


@dataclass
class HelperTracking:
    """One delegated run's launch ledger and marker token (#719, ADR-0296).

    ``ledger`` is the file the agent writes, or ``None`` when it could not be
    created (the run is then untracked and its sweep reads ``unverified``).
    ``beside_session``: the file sits beside the child session as
    ``<stem>/<run key>.launch-ledger``, kept with a ``swept`` line when the
    sweep is not confirmed; otherwise it is in the per-spawn prompt directory
    and is read before that directory is removed (r2 §3.1). ``token`` is never
    written anywhere but the child's environment.
    """

    token: str
    ledger: str | None
    beside_session: bool
    agent_stamp: float | None = None


def _helper_tracking(row: RunningChild, plan: SpawnPlan) -> HelperTracking | None:
    """THE #719 SWITCH: every POSIX delegated row is tracked; none on win32 (r3 §2, owner OQ-A).

    Ungated, foreground and background alike (owner, 2026-11-10: "게이트 없이
    적용 (권장)"). Creates the ledger before the spawn (``O_EXCL``, 0600, one
    ``open`` line) beside the child session, or in the prompt directory when
    there is none.
    """

    if sys.platform == "win32" or row.prompt is None:
        return None
    if plan.session_path is not None:
        path = os.path.join(
            os.path.dirname(plan.session_path), plan.id + launch_ledger.LEDGER_SUFFIX
        )
    else:
        path = str(row.prompt.directory / launch_ledger.FALLBACK_NAME)
    created = launch_ledger.create(path, run=plan.id)
    return HelperTracking(
        token=secrets.token_hex(16),
        ledger=path if created else None,
        beside_session=plan.session_path is not None,
    )


def _track_in_env(env: dict[str, str], tracking: HelperTracking | None) -> None:
    """The ledger's path and the run's token, into the child's environment.

    An inherited ``AELIX_LAUNCH_LEDGER`` is removed first: it is internal to the
    process it was handed to. The marker is appended to, never replaced.
    """

    env.pop(launch_ledger.LEDGER_ENV, None)
    if tracking is None:
        return
    if tracking.ledger is not None:
        env[launch_ledger.LEDGER_ENV] = tracking.ledger
    prior = env.get(launch_ledger.MARKER_ENV)
    env[launch_ledger.MARKER_ENV] = f"{prior},{tracking.token}" if prior else tracking.token


def _parent_stopped(row: RunningChild) -> bool:
    """Whether aelix's own stop legs killed the agent, walking it first (S2a).

    Then a missing ``exit`` line is not "the agent ended without its own
    cleanup": the leg walked the agent's descendants while it lived — on Linux,
    with the subreaper, the helpers it had adopted too.
    """

    return row.stopped or row.eager_kill or row.reaper_task is not None


def _reading_reasons(reading: launch_ledger.Reading | None, *, parent_stopped: bool) -> list[str]:
    """What a ledger says that keeps a sweep from ``confirmed`` (r3 §3.4)."""

    if reading is None:
        return [launch_ledger.DAMAGED]
    reasons: list[str] = []
    if reading.damaged:
        reasons.append(launch_ledger.DAMAGED)
    if reading.in_flight:
        reasons.append(launch_ledger.IN_FLIGHT)
    if reading.armed is None:
        reasons.append(launch_ledger.NOT_ARMED)
    elif sys.platform == "linux" and reading.armed.get("subreaper") is not True:
        reasons.append(launch_ledger.NOT_SUBREAPER)
    if reading.exit is None:
        if not parent_stopped:
            reasons.append(launch_ledger.NO_EXIT_CLEANUP)
    elif reading.exit.get("cleanup") != "confirmed":
        reasons.append(str(reading.exit.get("reason") or "the agent's own cleanup was unverified"))
    return reasons


def _sweep_now(
    tracking: HelperTracking, *, parent_stopped: bool
) -> tuple[launch_ledger.Sweep, list[str]]:
    """Read the ledger, kill its groups with authority, then this run's marker hits (r3 §3.5).

    Blocking — the ledger read and the scan of every process of this user —
    so :func:`sweep_helpers` runs it on a worker thread. Returns what was
    signalled and the reasons the reading itself gives.
    """

    reasons: list[str] = []
    done = launch_ledger.Sweep()
    if tracking.ledger is None:
        reasons.append(launch_ledger.NOT_CREATED)
    else:
        reading = launch_ledger.read(tracking.ledger)
        reasons.extend(_reading_reasons(reading, parent_stopped=parent_stopped))
        if reading is not None:
            done.merge(launch_ledger.sweep_groups(reading.launches.values(), reading.retired))
    exclude = launch_ledger.ancestry()
    hits = launch_ledger.marker_hits(tracking.token, since=tracking.agent_stamp, exclude=exclude)
    if hits is None:
        reasons.append(launch_ledger.SCAN_FAILED)
    done.merge(launch_ledger.kill_hits(hits, exclude=exclude))
    return done, reasons


@dataclass(frozen=True)
class HelperAnswer:
    """A sweep's verdict: ``confirmed``, or why not (at most 16 targets, then a count)."""

    confirmed: bool
    reason: str | None = None


async def sweep_helpers(tracking: HelperTracking, *, parent_stopped: bool) -> HelperAnswer:
    """Stop what the agent's ``bash`` left, confirm it gone, and keep the ledger only when unverified.

    The foreground cleanup task (:func:`ensure_cleanup`) and a background job's
    ``helper_cleanup`` (``jobs_client``) both end here. ``confirmed`` means every
    target aelix identified was signalled with authority and is confirmed gone,
    no launch was in flight, the ledger is whole and armed, the agent ran its
    own exit cleanup or aelix's stop legs ended it, and on Linux it was a
    subreaper. It does not cover ADR-0296's residuals. A scan of this user's
    processes that could not run is ``unverified`` and keeps the ledger (Codex 7).
    """

    done, reasons = await asyncio.to_thread(_sweep_now, tracking, parent_stopped=parent_stopped)
    reasons.extend(done.reasons)
    why = await launch_ledger.confirm_stopped(done)
    if why is not None:
        reasons.append(why)
    answer = HelperAnswer(
        confirmed=not reasons, reason=launch_ledger.summarize(reasons) if reasons else None
    )
    _settle_ledger(tracking, answer)
    return answer


def _settle_ledger(tracking: HelperTracking, answer: HelperAnswer) -> None:
    """Beside the session: deleted when confirmed, kept with a ``swept`` line when not (r2 §3.1).

    Report-only evidence for a restart; nothing ever kills from it. The
    prompt-directory copy goes with that directory.
    """

    path = tracking.ledger
    if path is None or not tracking.beside_session:
        return
    with contextlib.suppress(OSError):
        # A compaction the agent did not finish (it was killed between the
        # write and the replace) leaves this beside the session.
        os.unlink(path + launch_ledger.COMPACT_SUFFIX)
    if answer.confirmed:
        with contextlib.suppress(OSError):
            os.unlink(path)
        return
    launch_ledger.append(path, {"t": "swept", "confirmed": False, "reason": answer.reason})


@dataclass
class CleanupDuty:
    """One run's cleanup task, as its registry holds it (r3 §3.4).

    Published right before the spawn (:func:`open_cleanup`), so a spawn in
    flight — whose OS child may already exist while ``row.proc`` is still
    ``None`` — is already a duty every remover finds (Codex 3); its task waits
    for :attr:`ended`, set at the run's first end. ``step`` names where it is,
    for the one line a drain writes about a duty still running at its bound.
    """

    key: str
    step: str = "waiting for the run to end"
    task: asyncio.Task[None] | None = None
    ended: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)


CLEANUP_CONFIRM_SECONDS = launch_ledger.CONFIRM_SECONDS
"""How long the task waits for the child's pipes before the parent closes its own ends."""

_HELD_DUTIES: dict[str, CleanupDuty] = {}
"""Where a row built without a runtime keeps its task (the loop keeps only a weak one)."""


def open_cleanup(row: RunningChild, *, grace: float) -> CleanupDuty | None:
    """The row's ONE cleanup task, published BEFORE the spawn is awaited (Codex 3, N8).

    Created and added to the runtime's registry in one synchronous step — no
    ``await`` between them — so no remover can look at the registry in between,
    and a ``stop_all`` that meets the spawn in flight keeps the row's prompt
    directory and joins this task instead of returning past a live child. The
    task waits for the run's first end (:func:`ensure_cleanup`) before it
    touches anything. ``None`` for a service-owned row, whose barrier and
    ``helper_cleanup`` are the job service's.
    """

    if row.owner is not None:
        return None
    duty = row.cleanup
    if duty is not None:
        return duty
    duty = CleanupDuty(key=row.id)
    registry = row.cleanups if row.cleanups is not None else _HELD_DUTIES
    duty.task = asyncio.ensure_future(_clean_up(row, duty, grace))
    row.cleanup = duty
    registry[row.id] = duty

    def _done(_task: asyncio.Future[None]) -> None:
        if registry.get(duty.key) is duty:
            del registry[duty.key]

    duty.task.add_done_callback(_done)
    return duty


def ensure_cleanup(row: RunningChild, *, grace: float) -> CleanupDuty | None:
    """The run's first end — normal, an error, the cancel handler, or a stop — sets its task going (#750).

    Every later end gets the same task. ``None`` for a row this channel did not
    spawn (no process, an rpc row) or a service-owned one.
    """

    if row.owner is not None or row.proc is None or not row.channel_cleans:
        return None
    duty = open_cleanup(row, grace=grace)
    if duty is not None:
        duty.ended.set()
    return duty


async def join_cleanup(duty: CleanupDuty | None) -> None:
    """Await ``duty`` under ``asyncio.shield``: a cancelled awaiter never cancels it (Codex N9)."""

    if duty is None or duty.task is None:
        return
    with contextlib.suppress(Exception):
        await asyncio.shield(duty.task)


async def _bounded(awaitable: Any, timeout: float) -> bool:
    """``awaitable`` for at most ``timeout`` seconds; whether it finished. Cancelled, never raised, past it."""

    future = asyncio.ensure_future(awaitable)
    try:
        await asyncio.wait({future}, timeout=timeout)
    finally:
        if not future.done():
            future.cancel()
        future.add_done_callback(_swallow)
    return future.done() and not future.cancelled()


def _close_pipes(proc: Any) -> None:
    """The PARENT's read ends of the child's stdout and stderr, closed. Never raises.

    Only once the child is reaped, for a holder the sweep could not name (an
    ADR-0296 residual): its next write to that pipe gets ``EPIPE`` (or a
    ``SIGPIPE`` it does not handle), and the subprocess transport can close, so
    it is not left to a closed loop (#750, Codex 6). The holder itself is not
    signalled by aelix.
    """

    transport = getattr(proc, "_transport", None)
    if transport is None:
        return
    for fd in (1, 2):
        with contextlib.suppress(Exception):
            pipe = transport.get_pipe_transport(fd)
            if pipe is not None and not pipe.is_closing():
                pipe.close()


async def _release_pipes(proc: Any) -> None:
    """The killed child's transport released (#750): its pipes within the bound, else ours closed.

    Never drops the obligation: a child not yet reaped is waited for with no
    bound, and the drain reports a task still here at ``CLEANUP_BOUND``.
    """

    if await _bounded(wait_released(proc), CLEANUP_CONFIRM_SECONDS):
        return
    await _wait_for_exit(proc)
    _close_pipes(proc)
    await wait_released(proc)


async def _clean_up(row: RunningChild, duty: CleanupDuty, grace: float) -> None:
    """The task's body (r3 §3.4): the child's exit, the sweep, its pipes, the directory, the tree.

    0. wait for the run's first end (the task is published before the spawn);
       a run that got no process skips to step 4;
    1. await the child's exit, bounded by the grace — the killed child is
       awaited (#750), so its transport is not left to a closed loop;
    2. the ledger sweep and the marker scan (:func:`sweep_helpers`); an
       unverified answer is one DEBUG log line, never a terminal write (the
       TUI's frame, #546's rule);
    3. the child's pipes released (:func:`_release_pipes`);
    4. the prompt directory removed — after the sweep read a ledger kept there;
    5. the tree closed, after a reaper still escalating (#220 §A.6).

    Steps 4 and 5 run however 0–3 end — a sweep that raised is one DEBUG line,
    and a loop closing under the task still removes the directory and closes
    the tree.
    """

    try:
        await duty.ended.wait()
        proc = row.proc
        if proc is None:
            return
        duty.step = "waiting for the agent to exit"
        await _bounded(_wait_for_exit(proc), grace)
        tracking = row.tracking
        if tracking is not None:
            duty.step = "sweeping what its bash left"
            answer = await sweep_helpers(tracking, parent_stopped=_parent_stopped(row))
            if not answer.confirmed:
                logger.debug(
                    "delegated run %s: its helpers are not confirmed stopped: %s",
                    row.id,
                    answer.reason,
                )
        duty.step = "waiting for its pipes to close"
        await _release_pipes(proc)
    except Exception:  # noqa: BLE001 — the directory and the tree still go
        logger.debug("delegated run %s: its cleanup failed", row.id, exc_info=True)
    finally:
        duty.step = "removing its prompt directory"
        remove_prompt_dir(row.prompt)
        row.prompt = None
        _close_tree(row)


def _close_tree(row: RunningChild) -> None:
    """THE SINGLE OWNER of a print row's tree (#220 §A.6 rule 2), and the close is not unconditional.

    ``close()`` DISARMS both legs — ``soft_kill`` returns ``False`` and
    ``hard_kill`` is a no-op once ``_closed`` — so closing it before the
    reaper's escalation would silently delete the kill on BOTH platforms
    (``reap``'s Q1 group kill goes through the tree). The reaper is DETACHED by
    design (finding B1) and may still be inside its grace, so the close waits
    for it. ``row.tree is None``: an attach that degraded. NULLING IS THE
    MECHANISM: a later ``abort_child`` → ``reap(..., tree=row.tree)`` then gets
    ``None`` and falls back to the signal legs instead of dispatching into a
    disarmed object (``rpc/rpc_client.py``'s ``stop()`` does the same).
    """

    tree = row.tree
    if tree is None or not row.tree_owned:
        return
    task = row.reaper_task
    if task is None or task.done():
        tree.close()
        row.tree = None
    else:
        task.add_done_callback(lambda _: (tree.close(), setattr(row, "tree", None)))


def _end_of_run(row: RunningChild, grace: float) -> None:
    """``run``'s ``finally``: synchronous, total, and the same on every path out.

    A service-owned row's directory and tree are the job's (#611 S1c). A row
    that never got a process has nothing to sweep: its directory goes now, and
    an unused ledger beside its session with it. Otherwise the prompt FILE is
    unlinked now — the user's private agent prompt never outlives the run on
    any path — and the run's cleanup task, created here if no earlier end did,
    removes the directory once it has read what may be kept there.
    """

    if row.owner is not None:
        return
    if row.proc is None:
        remove_prompt_dir(row.prompt)
        row.prompt = None
        tracking = row.tracking
        if tracking is not None and tracking.ledger is not None and tracking.beside_session:
            with contextlib.suppress(OSError):
                os.unlink(tracking.ledger)
        if row.cleanup is not None:
            # A spawn that failed or was cancelled: the duty published before
            # it has nothing to sweep, and ends now.
            row.cleanup.ended.set()
        return
    if row.prompt is not None:
        with contextlib.suppress(OSError):
            row.prompt.path.unlink()
    ensure_cleanup(row, grace=grace)


class PrintChannel:
    """One delegation, start to envelope.

    Stateless between runs — every piece of per-run state lives on the
    :class:`RunningChild` row the caller owns — so a single instance is shared
    by the whole session.

    :param grace: SIGTERM → SIGKILL window handed to :func:`~aelix_agents.reaper.reap`.
    :param model_registry: read LIVE (a callable) for the cost fallback, because
        the registry is rebound on ``/reload`` and a captured one goes stale.
    :param parent_model: the parent's effective model, read LIVE for the same
        reason and one more — ``/model`` rebinds it mid-session, and a child
        spawned after that must inherit what the parent is using NOW.
    :param argv_builder: the seam that renders a plan into a command line.
        Defaults to :func:`build_child_argv`. Injectable so the subprocess
        tests can drive the real pump/reaper/envelope machinery against a
        purpose-built stub child (a 1 MiB stderr burst, a 300 KB line, a
        SIGTERM-ignoring process) — none of which a real aelix child can be
        made to produce on demand.
    """

    resumes_agents = True
    """It launches a resume plan from the plan's recorded ``limits`` and nothing
    else (#610 D8) — the attribute :class:`SubagentChannel` describes."""

    def __init__(
        self,
        *,
        grace: float = DEFAULT_GRACE_SECONDS,
        model_registry: Callable[[], Any | None] | None = None,
        parent_model: Callable[[], Any | None] | None = None,
        argv_builder: Callable[..., list[str]] | None = None,
        env_builder: Callable[..., dict[str, str]] | None = None,
    ) -> None:
        self._grace = grace
        self._model_registry = model_registry
        self._parent_model = parent_model
        self._argv_builder = argv_builder or build_child_argv
        self._env_builder = env_builder or build_child_env

    def frozen(self, *, model_registry: Any, parent_model: Any) -> PrintChannel:
        """This channel with its two live getters replaced by values read once (#611 S1c).

        For a background run, whose runner must reach nothing an ``/reload``
        invalidates (ADR-0293 §3): the cost fallback's registry and the parent's
        model are the ones the spawn read. Everything else — the grace, the argv
        and env builders a test injects — is this channel's.
        """

        return PrintChannel(
            grace=self._grace,
            model_registry=lambda: model_registry,
            parent_model=lambda: parent_model,
            argv_builder=self._argv_builder,
            env_builder=self._env_builder,
        )

    async def run(
        self,
        plan: SpawnPlan,
        *,
        child: RunningChild | None = None,
        on_stream: Callable[[_StreamState], None] | None = None,
    ) -> SubagentResult:
        """Spawn, stream, reap, and return an envelope. NEVER raises except on
        cancellation.

        A ``CancelledError`` is the ONE thing that propagates, and deliberately:
        it means the parent's turn is being torn down, so there is nobody left
        to render a result. Before re-raising, the child is killed EAGERLY —
        the human already pressed the key once, and a second press means "stop
        waiting", not "restart the 5 s clock" (finding B1).
        """

        started = time.monotonic()
        row = child if child is not None else RunningChild(
            id=plan.id, profile=plan.resolved.name
        )
        profile = plan.resolved.profile
        # THE ARGV IS BUILT FROM ``plan.limits`` AND NOTHING ELSE (#610 D8):
        # the tools, the context files, the thinking level and the model flags
        # ``_run`` decided — and recorded — before the run was admitted. A plan
        # without them (the channel tests build plans by hand) gets them from
        # the same function, from the plan's frozen parent state and this
        # channel's live getters, which is what this method used to do inline.
        if plan.limits is not None:
            limits, dropped = plan.limits, plan.dropped_tools
        else:
            computed = fresh_limits(
                plan.resolved,
                granted_mode=plan.permission_mode,
                cwd=plan.cwd,
                parent_tools=plan.parent_tools,
                parent_context_files=plan.parent_context_files,
                parent_thinking=plan.parent_thinking,
                parent_exclude_tools=plan.parent_exclude_tools,
                parent_model=self._parent_model() if self._parent_model else None,
                # #344 — the route a user-defined provider needs, see
                # ``build_child_argv``. Read LIVE: ``/reload`` rebinds it.
                model_registry=(self._model_registry() if self._model_registry else None),
            )
            limits, dropped = computed.limits, computed.dropped
        child_profile = limited_profile(profile, limits)
        state = row.stream
        assembler = LineAssembler()
        ring = StderrRing()
        output_cap = (
            plan.output_cap if plan.output_cap is not None else profile.output_cap
        )

        def _envelope(
            *,
            outcome: SubagentOutcome,
            exit_code: int | None,
            error: str | None = None,
            lingered: bool = False,
        ) -> SubagentResult:
            # Folded HERE and nowhere else: the drop happens at the byte layer,
            # so ``LineAssembler`` owns the counter and ``_StreamState`` — which
            # ``build_result`` reads — has no way to learn about it otherwise.
            apply_cost_fallback(
                state, self._model_registry() if self._model_registry else None
            )
            state.dropped_lines = assembler.dropped_lines
            return build_result(
                id=plan.id,
                profile=plan.resolved.name,
                state=state,
                outcome=outcome,
                exit_code=exit_code,
                stderr_tail=ring.text(),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                output_cap=output_cap if output_cap is not None else DEFAULT_OUTPUT_CAP,
                permission_mode=plan.permission_mode.value,
                dropped_tools=dropped,
                error=error,
                session_recorded=plan.session_path is not None,
                lingered=lingered,
                # Only a service-owned run asked for the versioned line.
                print_done_required=row.owner is not None,
            )

        if plan.resumes is not None and plan.limits is None:
            # A resume launched from limits computed just above would run at
            # what a new delegation gets NOW, unbounded by its first run.
            row.state = "error"
            return _envelope(outcome="error", exit_code=None, error=_RESUME_NEEDS_LIMITS)
        # Written BEFORE the process starts and recorded on the row in the same
        # breath, so ``stop`` / ``stop_all`` / the extension teardown can remove
        # it even if this task never reaches its own ``finally`` (§(h)).
        row.prompt = write_prompt_file(plan.resolved.name, profile.body)
        if row.owner is not None:
            # A service-owned row's directory is the job's: removed by the
            # service once its barrier and cleanup duty are done (#611 S1c,
            # ADR-0293 §cleanup obligations), never by this ``finally``.
            row.owner.on_directory(str(row.prompt.directory))
        try:
            if row.stopped:
                # ``stop`` / ``stop_all`` landed while we were being set up. The
                # registry row is published as soon as the spawn is accepted, so
                # this window is real and it is the difference between "stopped"
                # and "stopped, and also ran to completion".
                row.state = "stopped"
                return _envelope(outcome="aborted", exit_code=None)
            argv = self._argv_builder(
                child_profile,
                prompt_path=str(row.prompt.path),
                task=plan.task,
                permission_mode=plan.permission_mode,
                child_cwd=plan.cwd,
                parent_cwd=plan.parent_cwd,
                # The model flags as ``limits`` holds them — the parent's model
                # and registry are not read again at launch (#610 D8).
                model_pin=limits.model,
                session_path=plan.session_path,
            )
            from aelix_coding_agent.builtin.permission import pass_loader_roots_to_child
            env = pass_loader_roots_to_child(self._env_builder(profile), plan.parent_cwd)
            # The versioned ``print_done`` request and the job's marker
            # (#611 S1c) belong to a service-owned run only: one INHERITED from
            # whatever launched this aelix is removed, so no other child is
            # asked (Codex #1), and an owned row's own are set below.
            for name in JOB_INTERNAL_ENV:
                env.pop(name, None)
            if row.owner is not None:
                env.update(row.owner.child_env())
            # #719: the launch ledger and the run's marker (POSIX, every row).
            row.tracking = _helper_tracking(row, plan)
            _track_in_env(env, row.tracking)
            # The run's one cleanup duty, BEFORE the spawn is awaited: asyncio
            # starts the OS child inside that await, while ``row.proc`` is
            # still None (Codex 3 on #719).
            open_cleanup(row, grace=self._grace)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=plan.cwd,
                    env=env,
                    # MANDATORY. An inherited stdin costs +30 s per delegation
                    # (``_read_piped_stdin``, ``cli/entry.py:370-425``) and any
                    # bytes that do arrive are PREPENDED to the task message.
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    # Explicit, and load-bearing — see the module docstring (B3).
                    limit=STREAM_LIMIT_BYTES,
                    # ``{"start_new_session": True}`` here — without it the
                    # child joins the PARENT's process group, so one Ctrl+C
                    # SIGINTs every subagent at once with no envelope, and
                    # neither parent (``tui/shell.py:2548-2565``) nor child
                    # (``modes/print_mode.py:253-312``) installs a SIGINT
                    # handler to convert that into a result.
                    #
                    # ``{"creationflags": CREATE_NEW_PROCESS_GROUP}`` on Windows,
                    # and that is TWO fixes, not a port of one (#220). Windows
                    # accepts ``start_new_session`` and silently ignores it
                    # (CPython names the parameter ``unused_start_new_session``),
                    # so until now the child sat in the parent's console group:
                    # a ``CTRL_BREAK_EVENT`` could not be addressed to it without
                    # hitting the parent, AND a console Ctrl+C reached every
                    # subagent at once — the same harm the POSIX arm has always
                    # prevented. The new group closes both: it is the address
                    # ``ProcessTree.soft_kill``'s console event needs, and it
                    # DISABLES console Ctrl+C for the child and everything under
                    # it. The second half is documentation-derived, not measured:
                    # the windows leg does not drive a console Ctrl+C.
                    **containment_spawn_kwargs(new_session=True),
                    # ``None`` on Windows, where subprocess REJECTS the
                    # argument outright — unlike the containment kwargs above,
                    # which every platform accepts in the form it understands.
                    # #202 built the primitive (``aelix_ai.utils._process_tree``)
                    # and #220 adopted it here; this hook stays POSIX's alone,
                    # because Windows has no ``pdeathsig`` and the job's
                    # ``kill_on_close`` is its nearest equivalent (ADR-0238).
                    preexec_fn=pdeathsig_preexec(),  # noqa: PLW1509
                )
            except Exception as exc:  # noqa: BLE001 — a failed spawn is a RESULT
                row.state = "error"
                return _envelope(outcome="error", exit_code=None, error=str(exc))

            row.proc = proc
            # This run owes the process one cleanup task now (#750), and the
            # sweep reads the agent's start time against its marker hits.
            row.channel_cleans = True
            if row.tracking is not None:
                row.tracking.agent_stamp = _process_identity.process_started(proc.pid)
            try:
                # ITS OWN ``try``, deliberately OUTSIDE the spawn's. Inside it,
                # an attach failure would take the ``except`` above and return an
                # error envelope for a child that is RUNNING: ``_stream_and_reap``
                # is never entered, no reaper task is ever created, and
                # ``runtime.py`` then pops the row — the live-child-with-no-reaper
                # shape this file's last-line-of-defence handler exists to
                # prevent. DEGRADE, never abandon: ``tree=None`` is a supported
                # reaper input by construction (``reaper._usable``).
                #
                # Synchronous on both arms (``_attach_posix`` is one
                # ``os.getpgid``; ``_attach_win32`` is three ctypes calls), so it
                # adds no ``await`` between publishing ``row.proc`` and re-reading
                # ``row.stopped`` below — the race that ordering exists for is
                # unchanged. And it must sit BEFORE that re-read: ``abort_child``
                # reads ``child.tree``, so a stop that landed during the spawn
                # would otherwise abort with ``tree=None`` and lose win32
                # containment on exactly the child that was told to stop.
                row.tree = ProcessTree.attach(
                    proc.pid, kill_on_close=True, handle=_retained_handle(proc)
                )
                # ``kill_on_close=True`` because the print child meets both of
                # ADR-0238's stated criteria: ONE child per task, so releasing
                # the tree is the same event as being done with it; and on
                # Windows it is the analogue of the ``pdeathsig`` the line above
                # installs on Linux — a parent's death closes its last job
                # handle and ``KILL_ON_JOB_CLOSE`` ends the tree. The visible
                # consequence, stated rather than denied: on win32 a SUCCESSFUL
                # delegation now also ends a helper the child deliberately left
                # running. On POSIX ``close()`` still signals nothing.
                row.tree_owned = True
            except Exception as exc:  # noqa: BLE001
                row.tree = None
                row.tree_owned = False
                # Logged, not swallowed: on Windows a real attach failure
                # degrades containment to the root-only shape Pi #9129 proved
                # insufficient, and a delegation that still returns a perfectly
                # good envelope is the one failure with nowhere else to surface.
                logger.warning("process-tree attach failed for %s: %s", row.id, exc)
            if row.owner is not None:
                # The process and its tree are the job's now (#611 S1c): the
                # service's barrier waits on them and closes the tree. Before the
                # ``stopped`` re-read below, so a stop that landed during the
                # spawn kills through the owner, with the evidence collected.
                row.owner.on_process(row, proc, session_path=plan.session_path)
            row.state = "running"
            if row.stopped:
                # The other half of the race: a stop that landed while
                # ``create_subprocess_exec`` was in flight saw ``proc is None``
                # and had nothing to signal. Now it does.
                await abort_child(row, grace=self._grace)
            result = await self._stream_and_reap(
                proc,
                row,
                state=state,
                assembler=assembler,
                ring=ring,
                on_stream=on_stream,
                envelope=_envelope,
            )
            # A run that ENDS — normally, after a reap, or on an error the
            # pumps caught — returns its envelope once its cleanup is done:
            # the sweep of what its bash left (#719) and the killed child
            # awaited (#750). Shielded: a cancel here leaves it running in the
            # registry, and the ``finally`` below finds the same task. The
            # cancel path itself never waits here (D14).
            await join_cleanup(ensure_cleanup(row, grace=self._grace))
            return result
        finally:
            # SERVICE-OWNED (#611 S1c): the directory and the tree are the job's.
            # The service removes the one after its barrier and cleanup duty, and
            # the runner closes the other only once the barrier confirmed its
            # members gone — closing it here would disarm the legs the barrier
            # still needs (#220 §A.6). Every other row: :func:`_end_of_run`,
            # synchronous, idempotent and never raising, so it always completes
            # once entered, including on the cancellation path.
            _end_of_run(row, self._grace)

    async def _stream_and_reap(
        self,
        proc: Any,
        row: RunningChild,
        *,
        state: _StreamState,
        assembler: LineAssembler,
        ring: StderrRing,
        on_stream: Callable[[_StreamState], None] | None,
        envelope: Callable[..., SubagentResult],
    ) -> SubagentResult:
        """Both pumps, the post-EOF bound, and the kill legs.

        THERE IS NO DEADLINE (#604, ADR-0276). The main wait ends when the child
        does — its exit, or its pipes' EOF — or when a human ends it: ``stop`` /
        ``stop_all`` kill the child (so one of those follows), and Esc, a signal
        or ``/agents run``'s interrupt cancel this task. The only bounded wait
        left is the one after EOF (:data:`POST_EOF_EXIT_BOUND_SECONDS`).

        COMPLETION IS RACED AGAINST THE CHILD'S EXIT, NOT GATED ON PIPE EOF
        (P2 review, HIGH #4). An earlier shape awaited both pumps first and only
        then ``proc.wait()``, which silently made "the child finished" mean "no
        writer holds fd 1/2 any more". Anything the child leaves behind that
        inherited its stdio keeps those pipes open long after the child is dead —
        every stdio MCP server the child launches does, because
        ``mcp/client.py:232`` passes no ``errlog`` and the SDK defaults to
        ``sys.stderr``. Measured with a stub child that forks such a descendant,
        answers correctly and exits 0 in ~50 ms, at ``timeout_ms=3000``::

            status=timeout took=8.04s child='GONE' orphan='S (sleeping)' exit=0

        Three harms in one line: the parent blocked for the entire budget (600 s
        in production), the post-kill drain added its own 5 s on the same held
        pipe, and a correct answer was discarded as ``ok=False``.

        THE RACE IS AGAINST :func:`_wait_for_exit`, NOT ``proc.wait()``, and that
        distinction is the whole fix — measured, ``proc.wait()`` does not resolve
        until every pipe is disconnected even though ``proc.returncode`` is
        already set (CPython up to 3.13.14; gh-119710 changed that in 3.13.15),
        so racing it against the pumps would race pipe EOF against
        pipe EOF. :func:`_drain_after_exit` then finds the actual holder by pipe
        inode, which is the one question a ``PPid`` walk cannot answer once the
        child is gone.
        """

        pumps = asyncio.gather(
            asyncio.ensure_future(
                _pump_stdout(proc.stdout, assembler, state, on_stream)
            ),
            asyncio.ensure_future(_pump_stderr(proc.stderr, ring)),
        )
        # The gather can end up cancelled on the abort path, where nothing awaits
        # it again. Retrieving the outcome here keeps asyncio from logging
        # "exception was never retrieved" over a shutdown that went exactly as
        # designed.
        pumps.add_done_callback(_swallow)
        lingered = False
        exit_code: int | None = None
        exited = asyncio.ensure_future(_wait_for_exit(proc))
        exited.add_done_callback(_swallow)
        try:
            # ``asyncio.wait`` never cancels what it waits on, so ending the WAIT
            # never cancels the pumps: when the child exits first, the pipes
            # still have to drain to EOF or its last words — the partial summary
            # §(j) promises — are thrown away. NOT through ``asyncio.shield``:
            # from Python 3.14 a shield whose outer side ends first logs its
            # inner's exception (``tasks._log_on_exception``), and a cancelled
            # gather ends with ``set_exception(CancelledError())``, not
            # cancelled. Every stopped delegation then reached the loop's handler
            # as "CancelledError exception in shielded future" — in the TUI,
            # prompt_toolkit's "Unhandled exception in event loop … Press ENTER
            # to continue", which ate the next line typed (#604, CI py3.14).
            # NO ``timeout=`` (#604, ADR-0276) — see the docstring. A run still
            # going is a child still working, and the 10-minute default that
            # used to end this wait cut off children that were.
            await asyncio.wait({pumps, exited}, return_when=asyncio.FIRST_COMPLETED)

            if pumps.done():
                # Both pipes reached EOF — every writer closed them, so the
                # child is exiting. Still bounded (a child that closed its stdio
                # and then wedged must not hold the parent open), by its own
                # :data:`POST_EOF_EXIT_BOUND_SECONDS` now that there is no
                # deadline to borrow one from.
                try:
                    exit_code = int(
                        await asyncio.wait_for(asyncio.shield(exited), POST_EOF_EXIT_BOUND_SECONDS)
                    )
                except TimeoutError:
                    lingered = True
                    exit_code = await self._reap(proc, row)
            else:
                # THE CHILD IS GONE but the pipes are not — with no timeout on
                # the wait above, an unfinished pump means ``exited`` ended it.
                # Its answer is already in ``state``; the only open question is
                # who is squatting.
                exit_code = exited.result()
                await self._drain_after_exit(proc, pumps, row.tree, owner=row.owner)
        except asyncio.CancelledError:
            self._eager_abort(proc, row)
            # The pumps must not outlive an aborted run: they hold the child's
            # pipes and nobody is left to await them. Every path that RETURNS
            # below has already seen them finish or had ``_drain_after_exit``
            # cancel them, so this is the one place that has to.
            pumps.cancel()
            raise
        except Exception as exc:  # noqa: BLE001
            # LAST LINE OF DEFENCE (P2 review, HIGH #3). ``run`` documents "never
            # raises except on cancellation" and the pumps document the same;
            # both were once false, and the cost of that was a live child with no
            # reaper and no registry row — beyond the reach of ``stop`` /
            # ``stop_all`` / teardown for the rest of the process's life.
            # Whatever any future reducer, pump or waiter does, a delegation ends
            # as an ENVELOPE and the process tree ends dead.
            self._eager_abort(proc, row)
            pumps.cancel()
            with contextlib.suppress(Exception):
                exit_code = int(
                    await asyncio.wait_for(asyncio.shield(exited), self._grace)
                )
            row.state = "error"
            return envelope(outcome="error", exit_code=exit_code, error=str(exc))
        finally:
            exited.cancel()

        # No post-kill drain here any more (#604). It ran only when the deadline
        # fired with the pumps still open; the one kill left on this path — the
        # post-EOF reap — happens after both pumps have finished.

        if row.stopped:
            row.state = "stopped"
            return envelope(outcome="aborted", exit_code=exit_code)
        if lingered:
            # The exit code is the REAPER's, so it is not evidence about the
            # answer: ``build_result`` reads the stream to decide whether the
            # child had finished (ADR-0276 D4), and the row follows its verdict.
            result = envelope(outcome="ok", exit_code=exit_code, lingered=True)
            row.state = "done" if result.ok else "error"
            return result
        # ``outcome="ok"`` is a PROPOSAL, not a verdict: ``build_result``
        # tightens it when the exit code or the stream disagrees (§(j)). Never
        # trust the return code alone — until #410 a JSON-mode child whose model
        # errored exited 0 with empty stderr while the stream carried
        # ``stop_reason: "error"`` (it exits 1 since, ADR-0268; the stream is
        # still what ``build_result`` reads).
        row.state = "done" if exit_code == 0 else "error"
        return envelope(outcome="ok", exit_code=exit_code)

    @staticmethod
    async def _drain_after_exit(
        proc: Any,
        pumps: asyncio.Future[Any],
        tree: ProcessTree | None,
        *,
        owner: CleanupOwner | None = None,
    ) -> None:
        """Bounded drain once the child is dead — and evict whoever holds the pipes.

        Reaching here with pumps still running means a process OTHER than the
        child has fd 1 or fd 2 open (P2 review, HIGH #4). It gets
        :data:`POST_EXIT_DRAIN_SECONDS` to be merely slow; after that it is a
        squatter on a finished delegation's stdio and is killed.

        The holder is found by PIPE INODE (:func:`pipe_holder_pids`), not by a
        ``PPid`` walk: the child has already exited, so its descendants were
        reparented to init the moment it died and
        :func:`~aelix_agents.reaper.descendant_pids` returns nothing at all —
        that is exactly why the survivor used to be permanent.

        ``kill_tree`` is handed the real ``proc``: its ``returncode`` is set, so
        the child half is a no-op and only the squatters are signalled.

        ``tree`` (#220) changes this site on win32 ONLY, where the inode walk has
        nothing to read and the job is the only thing that can reach a squatter.
        POSIX is deliberately unchanged: ``kill_tree`` runs no group kill, and
        this is the caller that must not — it fires on the delegation SUCCESS
        path against a group whose leader has been reaped, which is revision 1
        of ``ProcessTree.close()`` again (it killed helpers a hook had
        deliberately backgrounded and exited 0 over).

        Two bounds on the win32 leg, so a reader does not have to re-derive them:

        * The kill is carried by ``TerminateJobObject``, not by ``taskkill /T``:
          ``/T`` follows LIVE parent links and the root is already dead here.
          #202 measured this exact shape and asserted the grandchild SURVIVES the
          walk (``tests/process_tree/test_process_tree_real_processes.py``). If
          ``_attach_win32`` fell back (``contained=False``, ``job is None``) this
          leg is a no-op on win32.
        * It is not a stale-pid kill. This branch is entered only while ``pumps``
          is unfinished, i.e. while a descendant still holds the child's stdio —
          so the transport still holds the ``Popen`` (``asyncio``'s subprocess
          transport nulls ``_proc`` only once every pipe disconnects, which is
          the negation of this branch's own precondition), and its open process
          handle pins the pid on Windows. Read from CPython, not measured on a
          Windows host.

        A SERVICE-OWNED row (``owner``, #611 S1c) is the job service's to
        clean: the squatters named by inode are signalled with no tree (so no
        job-wide kill empties the member list the barrier still has to read),
        then the owner's own hard leg, evidence first, reaches the child's group
        and — on win32 — its Job's members. A background delegation's helpers are
        stopped with it (owner, 2026-11-07); the barrier confirms it.
        """

        if pumps.done():
            return
        links = _child_pipe_links(proc)
        # ``asyncio.wait``, not ``wait_for(shield(pumps))``: it neither cancels
        # the pumps at the bound nor, on Python 3.14, leaves the shield's logger
        # on them for the ``cancel()`` below (see ``_stream_and_reap``).
        await asyncio.wait({pumps}, timeout=POST_EXIT_DRAIN_SECONDS)
        if pumps.done():
            return
        if owner is not None:
            # The owner's evidence first, then its hard leg; then the squatters
            # the inode walk named, which no walk of a dead root can.
            owner.stop_now()
            kill_tree(proc, pipe_holders(links), tree=None)
        else:
            kill_tree(proc, pipe_holders(links), tree=tree)
        await asyncio.wait({pumps}, timeout=POST_EXIT_DRAIN_SECONDS)
        # Cancelled unconditionally: a pump still blocked on a pipe some
        # unkillable holder owns would otherwise outlive the delegation.
        pumps.cancel()

    async def _reap(self, proc: Any, row: RunningChild) -> int:
        """Run the reaper DETACHED and await it shielded (finding B1).

        Detached so a cancellation of THIS task cannot cancel the kill;
        registry-owned so ``stop_all`` still joins it; and re-cancellation
        escalates to SIGKILL immediately rather than waiting out a grace whose
        only purpose was to let a cooperative child clean up.

        The descendant walk is taken HERE, with the groups its processes lead,
        while the child is provably still alive — every leg that reaches this
        method has a live child, which is what makes ``PPid`` a usable index.
        The dead-child case never comes here; it goes to
        :meth:`_drain_after_exit`. A child already reaped when this runs (the
        post-EOF bound racing its exit) is not walked, as in
        :meth:`_eager_abort` (#691): ``reap`` returns its status at once, and
        its pid may already be somebody else's.

        The reaper waits on ``row.escalate`` beside its grace, which is what
        makes the re-cancellation above eager for a reaper already running.
        """

        task = row.reaper_task
        if task is None or task.done():
            live = getattr(proc, "returncode", None) is None
            # Held for the reaper, which closes it (#611 S2a).
            walk = hold_walk(descendant_identities(proc.pid)) if live else HeldWalk()
            task = asyncio.ensure_future(
                reap(
                    proc,
                    grace=self._grace,
                    eager_kill=row.eager_kill,
                    descendants=walk,
                    tree=row.tree,
                    escalate=row.escalate,
                    # A service-owned row is stopped by its owner (#611 S1c).
                    owner=row.owner,
                )
            )
            row.reaper_task = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._eager_abort(proc, row)
            raise

    @staticmethod
    def _eager_abort(proc: Any, row: RunningChild) -> None:
        """Second Ctrl+C: stop waiting, start killing.

        Through the row's tree (#220), which on win32 makes this a
        ``taskkill /T /F`` plus ``TerminateJobObject`` where it used to be a
        root-only ``TerminateProcess``, and on POSIX supplies Q1's group kill
        when — and only when — no reaper is left to supply it (see below).
        STATED COST: this method is synchronous
        and is called from inside ``except asyncio.CancelledError`` handlers, so
        on win32 it now runs a ``subprocess.run(taskkill, timeout=5)`` on the
        event-loop thread. That is the same cost ``RpcClient.stop()`` and the
        hook timeout ladder already pay on the loop, and it is accepted for the
        same reason: the alternative to a bounded stall is a leaked tree. The
        two obvious remedies are worse — a ``terminate_job``-only path drops the
        ``/T`` walk, and awaiting a thread inside ``reap`` would punch a
        cancellation hole between the kill and the shielded final wait.
        """

        row.eager_kill = True
        # A reaper already in its grace read ``eager_kill`` when it started; the
        # event is what ends that grace now, with the snapshot it took before
        # its SIGTERM (#691). A root that died to that SIGTERM is not walked
        # below, so without it a ``setsid`` descendant waited out the grace.
        row.escalate.set()
        row.state = "stopped"
        if row.owner is not None:
            # SERVICE-OWNED (#611 S1c, ADR-0293 §5): the owner's hard leg, which
            # collects the evidence — the walk, the groups, on win32 the Job's
            # members — before its first signal, on the record the barrier
            # confirms against. ``kill_tree``'s tree arm would empty the Job's
            # member list first and leave the barrier nothing to wait on.
            row.owner.stop_now()
            return
        # THE WALK IS WHAT REACHES THE CHILD'S ``bash`` COMMAND (#691): this is
        # a SIGKILL with no grace, so the child's ``dispose()`` gets no time, and
        # the command leads a session of its own that the child's group kill
        # does not reach. Esc, Esc Esc and a signal to a headless parent come
        # here. Until #691 the walk was ``[]`` on macOS and that command
        # outlived every one of them. The groups the walked processes lead go
        # with it, read in the same breath, before any signal: what ``(cmd &)``
        # left in the shell's group has no parent chain back to the child.
        #
        # Only a child that has not been reaped is walked. A cancel that lands
        # inside ``_drain_after_exit`` reaches here with the child dead and
        # reaped: its descendants were reparented when it died, so the walk
        # could name nothing of its own — only the children of whoever got the
        # pid next (``reaper``'s PID-recycling hazard).
        #
        # Each walked pid is signalled only while it is still the process
        # walked, and each group only while its leader is, before that leader
        # (#611 S2a, ``kill_tree``).
        live = getattr(proc, "returncode", None) is None
        walk = hold_walk(descendant_identities(proc.pid)) if live else HeldWalk()
        try:
            kill_tree(proc, walk, tree=row.tree)
        finally:
            walk.close()
        # Q1 ON THE CANCEL PATH (#220 review round 2, posix2/P2). ``kill_tree``
        # deliberately runs no group kill of the CHILD's group, so the line
        # above reaches only what the walk named, the groups those processes
        # lead, and the root. MEASURED on Darwin before #691, when the walk
        # there was ``[]``: a delegation cancelled with no reaper running
        # (``reaper_task is None``) left a non-``setsid`` grandchild alive,
        # while the deadline path — gone since #604, and a reaper path like
        # ``stop`` — killed it. The walk names that grandchild now; the group
        # kill stays for a member the walk did not name.
        # Ctrl+C is ``turn_task.cancel()`` (``batch.py``), so that is the path a
        # human actually drives.
        #
        # ONLY when nothing else will escalate. If a reaper task exists and is
        # still running it owns the escalation: the event above ends its grace
        # and it runs Q1 itself, after the kill of its own snapshot. Duplicating
        # it here would kill the tree from outside that reaper while it is still
        # pending, which is the window ``run()``'s close rule (§A.6) exists to
        # observe and
        # ``test_the_tree_is_closed_after_the_reapers_escalation_never_before``
        # exists to measure. The condition is the same one ``abort_child`` and
        # :meth:`_reap` use to decide whether to BUILD a reaper.
        task = row.reaper_task
        if task is None or task.done():
            kill_group_if_live(proc, row.tree)


__all__ = [
    "AGENT_TOOL_NAME",
    "EXIT_POLL_SECONDS",
    "POST_EOF_EXIT_BOUND_SECONDS",
    "POST_EXIT_DRAIN_SECONDS",
    "READ_CHUNK_BYTES",
    "STDERR_RING_BYTES",
    "STREAM_LIMIT_BYTES",
    "ComputedLimits",
    "PrintChannel",
    "RunningChild",
    "SpawnPlan",
    "StderrRing",
    "SubagentChannel",
    "ToolNarrowing",
    "abort_child",
    "apply_cost_fallback",
    "build_child_argv",
    "build_child_env",
    "fresh_limits",
    "limited_profile",
    "narrow_context_files",
    "narrow_tools",
    "pipe_holder_pids",
    "pipe_holders",
    "profile_identity",
    "resolve_child_cwd",
    "resume_cwd",
    "resume_limits",
]
