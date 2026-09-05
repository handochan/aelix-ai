"""The JSON one-shot delegation channel — ADR-0197 §(j)/(k).

argv, env, cwd, stdio, both pumps, the timeout and the envelope hand-off.
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
   ``modes/print_mode.py:301-302`` prints every caught exception there, the SIGTERM
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
error, timeout, kill, spawn failure and cancellation — because the ``finally``
that removes it is synchronous (nothing to interrupt) and because the directory
is recorded on the :class:`RunningChild` registry row before the process starts,
so ``stop`` / ``stop_all`` / the extension teardown can remove it even when the
owning task was cancelled out of its own ``finally``.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import aelix_coding_agent
from aelix_ai.utils._process_tree import (
    ProcessTree,
    _retained_handle,
    containment_spawn_kwargs,
)
from aelix_coding_agent.agents.resolver import profile_to_argv
from aelix_coding_agent.subagent_contract import (
    DEPTH_ENV_VAR,
    MAX_SUBAGENT_DEPTH,
    SubagentState,
    subagent_depth,
)
from aelix_coding_agent.tools import ALL_TOOL_NAMES

from aelix_agents.consent import contains_control_chars
from aelix_agents.envelope import DEFAULT_OUTPUT_CAP, build_result
from aelix_agents.prompt_file import PromptFile, remove_prompt_dir, write_prompt_file
from aelix_agents.reaper import (
    DEFAULT_GRACE_SECONDS,
    descendant_pids,
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
    from aelix_coding_agent.builtin.permission_mode import PermissionMode
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

DEFAULT_TIMEOUT_MS = 600_000
"""Wall clock for one delegation, when neither the caller nor the profile names
one. Aelix-original: pi has no timeout of any kind, so a wedged child there is
a wedged parent forever."""

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

POST_EOF_EXIT_GRACE_SECONDS = 2.0
"""Minimum time :meth:`PrintChannel._stream_and_reap` gives ``returncode`` to
appear AFTER both pipes have reached EOF (P2 review, MEDIUM #3).

Both pumps at EOF means every writer closed fd 1 and fd 2 — the child is
exiting. But pipe EOF happens at ``exit()`` while ``proc.returncode`` is only
set when the loop's child watcher delivers the status, milliseconds to tens of
milliseconds later. Charging that gap against the caller's deadline made any
deadline landing inside it report a COMPLETE run as ``status="timeout",
ok=False`` with the right answer sitting in ``summary``. Measured on a child that
answers and exits at ~679 ms, sweeping ``timeout_ms`` across the band above it:
``686 ms -> timeout / ok=False / summary='the complete answer'``.

So the exit wait after EOF gets ``max(remaining budget, this)`` rather than the
remaining budget alone. It is a floor, not an extension: a child that closed its
stdio and then wedged still times out — two seconds later, and correctly."""

EXIT_POLL_SECONDS = 0.05
"""How often :func:`_wait_for_exit` reads ``proc.returncode``.

An attribute read, not a syscall — ``asyncio``'s child watcher sets the field
from its own callback. 20 wakeups a second while a delegation is in flight, and
none at all otherwise."""

_PROC_DIR = Path("/proc")
"""Same source :mod:`aelix_agents.reaper` reads, different index: that module
walks ``PPid`` to find a live child's descendants, :func:`pipe_holder_pids`
walks ``fd`` to find whoever holds a DEAD child's stdio."""


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
    timeout_ms: int | None = None
    output_cap: int | None = None


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
        return bytes(self._buf).decode("utf-8", errors="replace")


def narrow_tools(
    profile: AgentProfile, parent_grant: Iterable[str] | None
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
    (``agents/profile.py:167-177`` documents the inversion).
    """

    grant = set(parent_grant) if parent_grant is not None else set(ALL_TOOL_NAMES)
    requested = set(profile.tools) if profile.tools is not None else set(grant)
    allowed = (requested & grant & set(ALL_TOOL_NAMES)) - {AGENT_TOOL_NAME}
    dropped = tuple(sorted(requested - allowed))
    return ToolNarrowing(
        profile=dataclasses.replace(profile, tools=tuple(sorted(allowed))),
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
    becomes ``--no-context-files`` (``resolver.py:274-275``), and that emission
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
    (``tui/context.py:140-218``). A 150-byte directory name was demonstrated to
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


def build_child_argv(
    child_profile: AgentProfile,
    *,
    prompt_path: str,
    task: str,
    permission_mode: PermissionMode,
    child_cwd: str,
    parent_cwd: str,
    parent_model: Any | None = None,
) -> list[str]:
    """The child's exact command line — §(l).

    ``[sys.executable, "-m", "aelix_coding_agent", …]`` and nothing else.
    Specifically NOT ``-m aelix``, which ``rpc/rpc_client.py:1091`` does and which
    is a live bug (``aelix`` is the umbrella meta-package demo), and NOT the
    ``aelix`` console script, which in a worktree resolves to the OTHER tree's
    editable install.

    ``profile_to_argv`` supplies ``--mode json -p --no-session`` and appends
    ``f"Task: {task}"``. THE ``"Task: "`` PREFIX IS LOAD-BEARING and must not be
    stripped: ``args.py`` swallows an unrecognised ``--`` token into
    ``parsed.unknown_flags`` with NO diagnostic, so a bare task beginning with
    ``--`` would silently become a flag and the child would run with an empty
    prompt.

    ``child_trust_argv`` may legitimately return ``[]`` (§(g) clause 1) — the
    same call the ``/agents show`` dry-run renders, so the dry run stays the
    truth. ``--no-agents`` is belt-and-braces with :data:`DEPTH_ENV_VAR`: the
    env var stops the extension loading, the flag stops the settings gate
    turning it back on.

    ``parent_model`` is the parent's LIVE ``ExtensionContext.model``, forwarded
    only when the profile declares no model of its own (``resolver``'s emission
    table owns that rule). Without it a parent launched with ``--model`` on argv
    spawns a child with no model at all: the flag is run scope, the bundled
    profiles declare none, and nothing in between persists it.

    ``--no-context-files`` does NOT appear here even though the parent's ``-nc``
    is inherited (#121): it rides in on ``child_profile.context_files``, which
    :func:`narrow_context_files` clamps before the argv is built, so
    ``resolver.profile_to_flags`` stays the one place that emits it.
    """

    return [
        sys.executable,
        "-m",
        "aelix_coding_agent",
        *profile_to_argv(
            child_profile,
            prompt_path=prompt_path,
            oneshot=True,
            task=task,
            parent_model=parent_model,
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

    Inherited wholesale and then amended in four places. What is NOT touched is
    as deliberate as what is: ``AELIX_CODING_AGENT_DIR``, ``AELIX_SETTINGS_PATH``,
    ``AELIX_AUTH_PATH``, ``XDG_CONFIG_HOME``, ``PI_OFFLINE`` and every provider
    API key all pass through, because a child that cannot authenticate is a
    child that cannot do the work it was delegated.
    """

    env = dict(os.environ if base is None else base)

    # The depth gate. INERT at MAX_SUBAGENT_DEPTH == 1 — both branches yield 1,
    # and ``agents/profile.py:220`` defaults ``role`` to ``"leaf"`` — but wired
    # now so P3 only has to raise the cap. Computed from the INHERITED value
    # before the key is overwritten.
    depth = (
        MAX_SUBAGENT_DEPTH
        if profile.role == "leaf"
        else subagent_depth(env) + 1
    )
    env[DEPTH_ENV_VAR] = str(depth)

    # Belt-and-braces with ``stdin=DEVNULL``. An INHERITED ``"0"`` means "wait
    # forever" (``cli/entry.py:309-318``), and a child that waits forever on a
    # stdin nobody will ever write to is a delegation that only ends at the
    # timeout.
    env["AELIX_STDIN_TIMEOUT"] = "1"

    # Otherwise every child fans out its own copy of every configured MCP
    # server — N delegations become N × M processes, none of which the parent
    # tracks.
    env.pop("AELIX_MCP_CONFIG", None)

    # Closes the worktree/venv trap deterministically: the parent may be running
    # from a source tree that is on ITS ``sys.path`` only because of how it was
    # launched, and ``sys.executable -m aelix_coding_agent`` in a fresh process
    # would then fail to import at all.
    root = str(Path(aelix_coding_agent.__file__).resolve().parents[1])
    existing = env.get("PYTHONPATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if root not in parts:
        env["PYTHONPATH"] = os.pathsep.join([root, *parts])

    return env


def apply_cost_fallback(state: _StreamState, registry: Any | None) -> None:
    """Fill :attr:`_StreamState.cost` when the child's adapter reported none.

    ADR-0198 §D2 rule 9. ``usage["cost"]["total"]`` is already summed by
    :func:`~aelix_agents.stream.reduce_line`, but openrouter and
    openai-completions emit no ``cost`` key at all, so this fallback is the
    COMMON path rather than the exotic one. It lives here and not in
    ``stream.py`` because it needs a model-registry lookup — disk I/O, which a
    pure reducer may not do.

    Best-effort by construction: a delegation must never fail because a price
    could not be looked up.
    """

    if state.cost or registry is None or not state.provider or not state.model:
        return
    try:
        from aelix_ai.models import calculate_cost
        from aelix_ai.streaming import Usage

        model = registry.find(state.provider, state.model)
        if model is None:
            return
        usage = Usage(
            input=state.input,
            output=state.output,
            cache_read=state.cache_read,
            cache_write=state.cache_write,
        )
        state.cost = float(calculate_cost(model, usage).total)
    except Exception:  # noqa: BLE001 — a price is never worth failing a run over
        return


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

    The pump gather is awaited only through ``asyncio.shield``, so on the abort
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
    attribute read (see :data:`EXIT_POLL_SECONDS`).
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

    if not links or not _PROC_DIR.is_dir():
        return []
    me = os.getpid()
    holders: list[int] = []
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
        for fd in fds:
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if link in links:
                holders.append(pid)
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
            )
        )
        child.reaper_task = task
    # ``Exception`` only: a ``CancelledError`` here means the CALLER is being
    # torn down, and it must propagate — the reaper itself is shielded and
    # keeps going regardless.
    with contextlib.suppress(Exception):
        await asyncio.shield(task)


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
        narrowing = narrow_tools(profile, plan.parent_tools)
        # Both narrowings, then the argv. ``narrow_tools`` owns ``dropped``
        # (the envelope reports it); this one has nothing to report because a
        # parent's ``-nc`` is not a request the child made and lost.
        child_profile = narrow_context_files(
            narrowing.profile, plan.parent_context_files
        )
        state = row.stream
        assembler = LineAssembler()
        ring = StderrRing()
        output_cap = (
            plan.output_cap if plan.output_cap is not None else profile.output_cap
        )
        timeout_ms = (
            plan.timeout_ms
            if plan.timeout_ms is not None
            else (profile.timeout_ms or DEFAULT_TIMEOUT_MS)
        )

        def _envelope(
            *,
            outcome: SubagentOutcome,
            exit_code: int | None,
            error: str | None = None,
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
                dropped_tools=narrowing.dropped,
                error=error,
            )

        # Written BEFORE the process starts and recorded on the row in the same
        # breath, so ``stop`` / ``stop_all`` / the extension teardown can remove
        # it even if this task never reaches its own ``finally`` (§(h)).
        row.prompt = write_prompt_file(plan.resolved.name, profile.body)
        try:
            if row.stopped:
                # ``stop`` / ``stop_all`` landed while we were being set up. The
                # registry row is published as soon as the spawn is accepted, so
                # this window is real and it is the difference between "stopped"
                # and "stopped, and also ran for its full timeout".
                row.state = "stopped"
                return _envelope(outcome="aborted", exit_code=None)
            argv = self._argv_builder(
                child_profile,
                prompt_path=str(row.prompt.path),
                task=plan.task,
                permission_mode=plan.permission_mode,
                child_cwd=plan.cwd,
                parent_cwd=plan.parent_cwd,
                parent_model=(
                    self._parent_model() if self._parent_model else None
                ),
            )
            env = self._env_builder(profile)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=plan.cwd,
                    env=env,
                    # MANDATORY. An inherited stdin costs +30 s per delegation
                    # (``_read_piped_stdin``, ``cli/entry.py:307-362``) and any
                    # bytes that do arrive are PREPENDED to the task message.
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    # Explicit, and load-bearing — see the module docstring (B3).
                    limit=STREAM_LIMIT_BYTES,
                    # ``{"start_new_session": True}`` here — without it the
                    # child joins the PARENT's process group, so one Ctrl+C
                    # SIGINTs every subagent at once with no envelope, and
                    # neither parent (``tui/shell.py:1874-1891``) nor child
                    # (``modes/print_mode.py:131-190``) installs a SIGINT
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
            row.state = "running"
            if row.stopped:
                # The other half of the race: a stop that landed while
                # ``create_subprocess_exec`` was in flight saw ``proc is None``
                # and had nothing to signal. Now it does.
                await abort_child(row, grace=self._grace)
            return await self._stream_and_reap(
                proc,
                row,
                state=state,
                assembler=assembler,
                ring=ring,
                on_stream=on_stream,
                timeout_ms=timeout_ms,
                envelope=_envelope,
            )
        finally:
            # Synchronous, idempotent, and never raises — so it always completes
            # once entered, including on the cancellation path.
            remove_prompt_dir(row.prompt)
            row.prompt = None
            # THE SINGLE OWNER of a print row's tree (#220 §A.6 rule 2), and the
            # close is deliberately not unconditional here.
            #
            # ``close()`` DISARMS both legs — ``soft_kill`` returns ``False`` and
            # ``hard_kill`` is a no-op once ``_closed`` — so closing it before the
            # reaper's escalation would silently delete the kill on BOTH
            # platforms now that the POSIX escalation goes through the tree too
            # (``reap``'s Q1 group kill). The reaper is DETACHED by design
            # (finding B1) and may still be inside its grace when this ``finally``
            # runs, so the close waits for it.
            #
            # ``row.tree is None`` is not a nicety either: the abort-before-spawn
            # path and the spawn-failure path both reach here with no tree.
            tree = row.tree
            if tree is not None and row.tree_owned:
                task = row.reaper_task
                if task is None or task.done():
                    tree.close()
                    # NULLING IS THE MECHANISM, not tidiness: it is what makes a
                    # later ``abort_child`` → ``reap(..., tree=row.tree)`` get
                    # ``None`` and fall back to the signal legs instead of
                    # dispatching into a disarmed object. Mirrors the reference
                    # adoption (``rpc/rpc_client.py``'s ``stop()``).
                    row.tree = None
                else:
                    task.add_done_callback(
                        lambda _: (tree.close(), setattr(row, "tree", None))
                    )

    async def _stream_and_reap(
        self,
        proc: Any,
        row: RunningChild,
        *,
        state: _StreamState,
        assembler: LineAssembler,
        ring: StderrRing,
        on_stream: Callable[[_StreamState], None] | None,
        timeout_ms: int,
        envelope: Callable[..., SubagentResult],
    ) -> SubagentResult:
        """Both pumps, the deadline, and the kill legs.

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
        already set, so racing it against the pumps would race pipe EOF against
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
        # The gather is repeatedly SHIELDED and can end up cancelled on the abort
        # path, where nothing awaits it again. Retrieving the outcome here keeps
        # asyncio from logging "exception was never retrieved" over a shutdown
        # that went exactly as designed.
        pumps.add_done_callback(_swallow)
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        timed_out = False
        exit_code: int | None = None
        exited = asyncio.ensure_future(_wait_for_exit(proc))
        exited.add_done_callback(_swallow)
        try:
            # Shielded so the deadline cancels the WAIT, not the pumps: after
            # the kill leg the pipes still have to drain to EOF or the child's
            # last words — including the partial summary §(j) promises on a
            # timeout — are thrown away.
            pumps_wait = asyncio.ensure_future(asyncio.shield(pumps))
            pumps_wait.add_done_callback(_swallow)
            try:
                await asyncio.wait(
                    {pumps_wait, exited},
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=max(deadline - time.monotonic(), 0.0),
                )
            finally:
                if not pumps_wait.done():
                    pumps_wait.cancel()

            if pumps.done():
                # Both pipes reached EOF — every writer closed them, so the
                # child is exiting. Still bounded (a child that closed its stdio
                # and then wedged must not hold the parent open), but with a
                # FLOOR under the remaining budget: the gap between pipe EOF and
                # ``returncode`` being set is the loop's child watcher, not the
                # child, and charging it to the caller's deadline reported
                # finished runs as timeouts (MEDIUM #3, see
                # :data:`POST_EOF_EXIT_GRACE_SECONDS`).
                try:
                    exit_code = int(
                        await asyncio.wait_for(
                            asyncio.shield(exited),
                            max(
                                deadline - time.monotonic(),
                                POST_EOF_EXIT_GRACE_SECONDS,
                            ),
                        )
                    )
                except TimeoutError:
                    timed_out = True
                    exit_code = await self._reap(proc, row)
            elif exited.done():
                # THE CHILD IS GONE but the pipes are not. Its answer is already
                # in ``state``; the only open question is who is squatting.
                exit_code = exited.result()
                await self._drain_after_exit(proc, pumps, row.tree)
            else:
                # Neither: the deadline fired first.
                timed_out = True
                exit_code = await self._reap(proc, row)
        except asyncio.CancelledError:
            self._eager_abort(proc, row)
            # The pumps must not outlive an aborted run: they hold the child's
            # pipes and nobody is left to await them. NOT done in a ``finally`` —
            # on the timeout path the pumps are still draining the dying child's
            # last words, which is exactly the partial summary §(j) promises.
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

        if timed_out and not pumps.done():
            # Drain whatever the kill legs left in the pipes — the dying child's
            # last words ARE the partial summary §(j) promises. Bounded, because
            # a pump wedged on a pipe some survivor still holds must not convert
            # a timeout into a hang, and then cancelled so nothing lingers.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    asyncio.shield(pumps), POST_EXIT_DRAIN_SECONDS
                )
            pumps.cancel()

        if row.stopped:
            row.state = "stopped"
            return envelope(outcome="aborted", exit_code=exit_code)
        if timed_out:
            row.state = "error"
            return envelope(outcome="timeout", exit_code=exit_code)
        # ``outcome="ok"`` is a PROPOSAL, not a verdict: ``build_result``
        # tightens it when the exit code or the stream disagrees (§(j)). Never
        # trust the return code alone — a JSON-mode child whose model errored
        # exits 0 with empty stderr while the stream carries
        # ``stop_reason: "error"``.
        row.state = "done" if exit_code == 0 else "error"
        return envelope(outcome="ok", exit_code=exit_code)

    @staticmethod
    async def _drain_after_exit(
        proc: Any, pumps: asyncio.Future[Any], tree: ProcessTree | None
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
        """

        if pumps.done():
            return
        links = _child_pipe_links(proc)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(pumps), POST_EXIT_DRAIN_SECONDS)
        if pumps.done():
            return
        kill_tree(proc, pipe_holder_pids(links), tree=tree)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(pumps), POST_EXIT_DRAIN_SECONDS)
        # Cancelled unconditionally: a pump still blocked on a pipe some
        # unkillable holder owns would otherwise outlive the delegation.
        pumps.cancel()

    async def _reap(self, proc: Any, row: RunningChild) -> int:
        """Run the reaper DETACHED and await it shielded (finding B1).

        Detached so a cancellation of THIS task cannot cancel the kill;
        registry-owned so ``stop_all`` still joins it; and re-cancellation
        escalates to SIGKILL immediately rather than waiting out a grace whose
        only purpose was to let a cooperative child clean up.

        The descendant walk is taken HERE, while the child is provably still
        alive — every leg that reaches this method has a live child, which is
        what makes ``PPid`` a usable index. The dead-child case never comes here;
        it goes to :meth:`_drain_after_exit`.
        """

        task = row.reaper_task
        if task is None or task.done():
            task = asyncio.ensure_future(
                reap(
                    proc,
                    grace=self._grace,
                    eager_kill=row.eager_kill,
                    descendants=descendant_pids(proc.pid),
                    tree=row.tree,
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
        row.state = "stopped"
        kill_tree(proc, descendant_pids(proc.pid), tree=row.tree)
        # Q1 ON THE CANCEL PATH (#220 review round 2, posix2/P2). ``kill_tree``
        # deliberately runs no POSIX group kill of its own, so on macOS — where
        # ``descendant_pids`` is ``[]`` — the line above is a root-only
        # ``SIGKILL`` and a non-``setsid`` grandchild survives it. MEASURED on
        # Darwin: a delegation cancelled before its deadline (``reaper_task is
        # None``) left the grandchild alive, while the timeout path killed it.
        # Ctrl+C is ``turn_task.cancel()`` (``batch.py``), so that is the path a
        # human actually drives.
        #
        # ONLY when nothing else will escalate. If a reaper task exists and is
        # still running it owns the escalation and runs Q1 itself after its
        # grace; duplicating it here would kill the tree BEFORE that grace
        # elapses, which is the window ``run()``'s close rule (§A.6) exists to
        # observe and
        # ``test_the_tree_is_closed_after_the_reapers_escalation_never_before``
        # exists to measure. The condition is the same one ``abort_child`` and
        # :meth:`_reap` use to decide whether to BUILD a reaper.
        task = row.reaper_task
        if task is None or task.done():
            kill_group_if_live(proc, row.tree)


__all__ = [
    "AGENT_TOOL_NAME",
    "DEFAULT_TIMEOUT_MS",
    "EXIT_POLL_SECONDS",
    "POST_EOF_EXIT_GRACE_SECONDS",
    "POST_EXIT_DRAIN_SECONDS",
    "READ_CHUNK_BYTES",
    "STDERR_RING_BYTES",
    "STREAM_LIMIT_BYTES",
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
    "narrow_context_files",
    "narrow_tools",
    "pipe_holder_pids",
    "resolve_child_cwd",
]
