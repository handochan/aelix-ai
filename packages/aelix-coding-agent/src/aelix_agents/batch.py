"""The batch executor — ``mode="parallel"`` and ``mode="chain"`` (ADR-0199 §3.3/§3.5).

THE TOPOLOGY LIVES HERE AND NOWHERE ELSE. ``_SubagentRuntimeImpl.spawn_granted``
is called ONCE PER MEMBER with ``mode="single"``: one spawn is one child. That is
why the seam still raises on a non-``single`` mode (``runtime._UNSUPPORTED_MODE``)
even though this phase ships parallel and chain — passing a topology to the seam
is a programming error, not a request, and a P4 runtime author must find that out
immediately rather than by watching one child run where eight were asked for.

FOUR BOUNDS, AND THEY ARE FOUR DIFFERENT THINGS. Getting these confused is how
this file grows a defect that only shows up under load:

* :data:`MAX_CONCURRENCY` — how many members of THIS batch may be inside
  ``spawn_granted`` at once. It WAITS. It exists so a batch never presents more
  members to ``_admit_live`` than the registry has room for, which is what makes
  ``_TOO_MANY_LIVE`` unreachable from inside a batch with an otherwise-idle
  session (S5).
* ``runtime.MAX_LIVE_CHILDREN`` — the WHOLE-SESSION registry ceiling. It
  REFUSES, and it must keep refusing: callers outside this batch (a
  ``/agents run``, a second host turn) legitimately hold rows. A member that
  trips it gets its own envelope and the batch continues.
* ``runtime.MAX_DELEGATIONS_PER_PROMPT`` — how many CHILDREN one user prompt may
  start (S6). Charged per child, inside ``_run``'s await-free window, and only
  once a row actually exists.
* :data:`MAX_BATCH_WALL_MS` — the aggregate wall-clock ceiling for ONE
  ``agent()`` call. Enforced by DERIVING each member's ``timeout_ms`` from what
  is left of the batch budget at the moment its clock starts — never by
  ``asyncio.wait_for`` around the batch, which would cancel the inner task and
  destroy every COMPLETED member's envelope, contradicting ``envelope.py:8-12``
  ("THE ENVELOPE ALWAYS RETURNS, NEVER RAISES").

CANCELLATION IS THE ONE THING THAT PROPAGATES. ``ctx.signal`` is dead — it is
always ``None``: ``AgentHarness`` calls ``agent_loop(...)`` with no ``signal=``
argument (``harness/core.py:4497-4503``), ``agent_loop``'s parameter defaults to
``None`` (``loop.py:107``) and is threaded unchanged into
``ToolExecutionContext(signal=signal)`` (``loop.py:626-628``). Abort is
``turn_task.cancel()`` (``core.py:1516-1518``). So ``CancelledError`` is the ONLY
channel by which a Ctrl+C reaches a child, and every rule below about it is load
bearing rather than defensive.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.subagent_contract import SubagentResult

from aelix_agents.aggregate import MemberOutcome
from aelix_agents.chain import TaskTooLarge, check_task_size, render_step
from aelix_agents.posture import child_permission_mode, posture_rank
from aelix_agents.print_channel import DEFAULT_TIMEOUT_MS
from aelix_agents.runtime import MAX_LIVE_CHILDREN
from aelix_agents.runtime import _new_id as _new_spawn_id
from aelix_agents.tool import MAX_TIMEOUT_MS, MIN_TIMEOUT_MS

if TYPE_CHECKING:
    from collections.abc import Callable

    from aelix_coding_agent.builtin.permission_mode import PermissionMode
    from aelix_coding_agent.subagent_contract import (
        ResolvedProfile,
        SubagentMode,
        SubagentProgress,
    )

    from aelix_agents.consent import SpawnGrant
    from aelix_agents.tool import AgentCall

MAX_CONCURRENCY = 4
"""Members of ONE batch that may be inside ``spawn_granted`` at the same time.

Ratified in the spec, and CONSTRAINED rather than chosen: it must be
``<= runtime.MAX_LIVE_CHILDREN``, or ``_admit_live`` starts refusing members from
INSIDE the batch — which is exactly the partial-success envelope set that S5 and
S7 exist to make unreachable. The constraint is enforced at import (below), not
left as a comment, because the two numbers live in different modules and a future
author raising one will not be looking at the other.

It WAITS rather than refusing. That is the whole difference between this bound
and the registry ceiling: a batch of eight is a legal call, so its members queue;
a ninth live child in a session that already holds four is a resource refusal."""

MAX_BATCH_WALL_MS = 1_800_000
"""The aggregate wall-clock ceiling for ONE ``agent()`` call — 30 minutes.

Chosen so the honest batch is never cut short and the pathological one is: with
:data:`MAX_CONCURRENCY` = 4 an eight-task parallel batch at the 600 s per-child
default runs in two waves = 1 200 s = 20 min and completes untouched, while an
eight-step CHAIN at the same default would be 80 minutes — verbatim the case S12
names. Deliberately equal to ``tool.MAX_TIMEOUT_MS``, so a model cannot buy more
wall clock by splitting a job across tasks or by merging tasks into one.

It is the only bound on how long the user is locked out, because S11 ships no
mid-turn stop overlay: while a delegation runs, Enter is routed to ``steer()`` as
a MESSAGE (``chrome.py:784-790``) and the queue drains only after the turn.

IT IS A CEILING ON THE MEMBERS' TIMEOUTS, NOT ON THE CALL'S WALL CLOCK. A member
that hits its deadline then runs its kill legs — ``reap(grace=5.0)``
(``reaper.py:80``) plus the bounded post-kill drain ``POST_EXIT_DRAIN_SECONDS =
2.0`` (``print_channel.py:135``). :data:`KILL_LEG_RESERVE_MS` is subtracted so the
ceiling is honoured rather than approximately honoured; the honest outer bound is
this number plus at most one kill leg for whatever was in flight when it fired."""

KILL_LEG_RESERVE_MS = 7_000
"""Grace (5 s) + post-kill drain (2 s), reserved out of the remaining budget.

Per REMAINING STEP in a chain, because a chain's kill legs are sequential; FLAT
for parallel, because there they overlap. See :func:`_kill_leg_reserve_ms`."""

_BATCH_BUDGET_EXHAUSTED = (
    "Delegation refused: this agent() call has used its whole wall-clock budget "
    f"({MAX_BATCH_WALL_MS // 60_000} minutes for the call, however many tasks it "
    "carries), so there was not enough time left to start this one. No process "
    "was created. Run the remaining tasks in a new call, or make them smaller."
)

# ONE SEMAPHORE PER RUNNING LOOP, and the precedent is ``consent.py:109-128``.
# ``asyncio.Semaphore`` binds itself to the first loop that CONTENDS on it (the
# ``_LoopBoundMixin`` fast path never touches ``_get_loop``) and raises "bound to
# a different event loop" forever after. A process has exactly one loop, so in
# production this dict holds exactly one entry; a pytest session builds a fresh
# loop per test, and without this a single contended test would poison every
# later one in the same process.
# Keyed by the LOOP OBJECT and typed as one (not ``object``) because the
# eviction below asks it ``is_closed()``.
_SEM_BY_LOOP: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}

# NOT an ``assert``: ``python -O`` strips those, and this is a safety
# constraint rather than a debugging aid. Raising at import is the right
# severity — a build in which a batch can out-run the registry ceiling must not
# start, because the failure it produces (partial-success envelope sets behind a
# consent dialog that showed all N) is exactly the shape S5/S7 forbid.
if MAX_CONCURRENCY > MAX_LIVE_CHILDREN:
    raise RuntimeError(
        f"MAX_CONCURRENCY ({MAX_CONCURRENCY}) exceeds "
        f"runtime.MAX_LIVE_CHILDREN ({MAX_LIVE_CHILDREN}): a batch would present "
        "more members to _admit_live than the registry can hold, and the surplus "
        "would come back as refusal envelopes for children the human already "
        "approved. Lower MAX_CONCURRENCY or raise the registry ceiling."
    )


@dataclass(frozen=True)
class BatchOutcome:
    """What one ``agent()`` call produced, in SUBMITTED order.

    :attr:`not_run` counts steps for which NO envelope exists at all — a chain
    stops at the first failure (§3.3), so its trailing steps were never
    submitted. That is a different fact from a member with ``started=False``,
    which DOES have an envelope explaining its refusal, and
    ``aggregate.render_batch_result`` renders and counts the two separately.

    :attr:`wall_ms` is the executor's own measured wall clock — not a sum and not
    a max of the members' ``elapsed_ms``. It is the only number that is true for
    both modes.
    """

    members: tuple[MemberOutcome, ...]
    not_run: int
    wall_ms: int


@dataclass(frozen=True)
class _Batch:
    """Everything a member needs that is the same for every member.

    Frozen and passed down rather than closed over: a member coroutine is created
    for index *k* and may not start until wave 2, so anything it reads must be
    either immutable or an explicitly LIVE getter (:attr:`posture`). A captured
    plain value that looked live is the defect §3.9 exists to close.
    """

    runtime: Any
    """The ``_SubagentRuntimeImpl``. Typed ``Any`` on purpose: this module drives
    the PRIVATE door (``spawn_granted``), which is deliberately absent from the
    ``SubagentRuntime`` Protocol (S2), so there is no public type to name."""
    grant: SpawnGrant
    resolved: ResolvedProfile
    call: AgentCall
    cwd: str
    has_ui: Callable[[], bool]
    """The host's LIVE UI predicate, read once per member — NEVER a value.

    A GETTER for exactly the reason :attr:`posture` is one, and the two are the
    two arguments of the SAME clamp call (:func:`_live_floor`). ``ctx.has_ui`` is
    time-varying (``runtime.py:33-35``, finding OC-7: ``False`` during
    ``harness.bootstrap()``, re-pointed on every ``/new`` / ``/fork`` /
    ``/resume``, ``False`` again on TUI exit), and it is the ONLY input that
    decides what ``approval_mode: "ask"`` clamps to — ``PLAN`` with no UI, the
    full inherit baseline with one (``posture.py:201-204``). A value captured at
    ``_execute`` would therefore keep the LOOSE clamp for a batch that outlives
    its UI, for up to :data:`MAX_BATCH_WALL_MS`."""
    posture: Callable[[], PermissionMode]
    """The parent's LIVE posture getter (``extension._host_posture``), read once
    per member — never a value captured at call time. shift+tab stays bound while
    a turn runs (``chrome.py:967-970``), and wave 2 may start half an hour after
    wave 1."""
    clamp_at_start: PermissionMode
    """The child clamp implied by the parent posture when the batch began. It is
    ONE of the two reference points the per-member live read is compared AGAINST
    — see :func:`_live_floor`."""
    posture_at_start: PermissionMode
    """The PARENT'S OWN posture when the batch began — the second reference point.

    Kept BESIDE :attr:`clamp_at_start` rather than derived from it because the
    clamp is lossy in exactly the direction that matters. ``child_permission_mode``
    reaches only three of the five ranks (``PLAN`` / ``AUTO_ACCEPT`` / ``YOLO``;
    ``DEFAULT`` is folded into ``PLAN`` at ``posture.py:231`` and ``AUTO`` into
    ``AUTO_ACCEPT`` at ``posture.py:54-60``), and in the WIDENED case it is
    always ``PLAN`` — the bottom of the lattice — so no comparison against it can
    ever detect a tightening. The raw posture does not saturate: widening is only
    ever offered under a ``PLAN`` or ``DEFAULT`` parent (``consent.py:527``
    requires ``AUTO_ACCEPT`` strictly looser than the clamp), and a ``DEFAULT``
    parent still has ``PLAN`` below it to shift+tab into. See
    :func:`_live_floor`."""
    deadline: float
    """Monotonic instant past which no member may be given any clock."""
    on_event: Callable[[int, SubagentProgress], None] | None


async def run_batch(
    *,
    runtime: Any,
    grant: SpawnGrant,
    resolved: ResolvedProfile,
    call: AgentCall,
    cwd: str,
    has_ui: Callable[[], bool],
    posture: Callable[[], PermissionMode],
    on_event: Callable[[int, SubagentProgress], None] | None = None,
) -> BatchOutcome:
    """Run one ``agent()`` call's whole batch. Returns; raises only on cancel.

    ``mode="single"`` never reaches here — it stays on ``spawn_granted`` +
    ``render_subagent_result`` byte-for-byte, which is what keeps P2's tests
    meaningful. Reaching here with ``"single"`` is a wiring bug in the caller and
    raises, for the same reason ``runtime._reject_unsupported`` raises.

    ``on_event`` carries the member's INDEX, bound at member creation, so a
    subscriber never has to infer which task a snapshot belongs to
    (``SubagentProgress`` carries no batch id — §3.6 explains why it stays that
    way). The index is available before the child's id exists, which is the
    property that makes the whole grouping design work: ``spawn_id = _new_id()``
    is minted INSIDE ``_run`` (``runtime.py:799``), and for members 5-8 not until
    wave 2.
    """

    started_at = _now()
    # ONE observation of the parent posture, used to build BOTH reference points.
    # Calling ``posture()`` twice would let a shift+tab land between the two and
    # produce a ``clamp_at_start`` that no single instant of the host ever
    # implied — which is the precise thing :func:`_live_floor` compares against.
    posture_at_start = posture()
    batch = _Batch(
        runtime=runtime,
        grant=grant,
        resolved=resolved,
        call=call,
        cwd=cwd,
        has_ui=has_ui,
        posture=posture,
        posture_at_start=posture_at_start,
        clamp_at_start=child_permission_mode(
            resolved.profile.approval_mode,
            posture_at_start,
            resolved.scope,
            # Both getters are read HERE, at the same instant, so the reference
            # point is one coherent observation of the host. Every later member
            # re-reads both (:func:`_live_floor`) and compares against it.
            has_ui=has_ui(),
        ),
        # CAPTURED ONCE, BEFORE MEMBER 1. Every member derives its own clock from
        # this instant, so waves compose with no extra machinery: a later wave
        # simply computes a smaller remainder.
        deadline=started_at + MAX_BATCH_WALL_MS / 1000,
        on_event=on_event,
    )

    if call.mode == "parallel":
        members, not_run = await _run_parallel(batch)
    elif call.mode == "chain":
        members, not_run = await _run_chain(batch)
    else:
        raise ValueError(
            f"run_batch received mode={call.mode!r}; it composes 'parallel' and "
            "'chain' only. mode='single' stays on spawn_granted + "
            "render_subagent_result."
        )
    return BatchOutcome(
        members=tuple(members),
        not_run=not_run,
        wall_ms=int((_now() - started_at) * 1000),
    )


async def _run_parallel(batch: _Batch) -> tuple[list[MemberOutcome], int]:
    """Every task at once, bounded by :data:`MAX_CONCURRENCY`. Nothing stops.

    Members are INDEPENDENT, so one failing member does not cancel its siblings —
    that asymmetry with :func:`_run_chain` is deliberate and is the whole
    difference between the two modes.

    A BARE ``asyncio.gather`` WITH ``return_exceptions=False``, AWAITED IN THIS
    FRAME. Every clause of that sentence was a review finding:

    * ``return_exceptions=True`` would capture a member's ``CancelledError`` as a
      RESULT, so this frame would not propagate — which bypasses the
      second-Ctrl+C escalation at ``print_channel.py:1214-1216`` (``_reap``'s
      ``except CancelledError: self._eager_abort(proc, row); raise``).
    * No ``ensure_future`` without holding the handle and no ``shield``: a
      detached member is a child nobody can kill, and ``PrintChannel.run``
      documents that ``CancelledError`` is the ONE thing it propagates and that
      it kills the child eagerly before re-raising (``print_channel.py:866-874``,
      ``:944-951``).
    * Awaited HERE rather than returned: cancelling the task that owns this frame
      cancels the ``_GatheringFuture``, which is the only path that cancels the
      children — ``gather`` propagating a first exception does NOT.

    Which is exactly why :func:`_member` swallows every non-cancel exception into
    an envelope. ``gather(..., return_exceptions=False)`` re-raises the FIRST
    exception immediately and leaves its siblings RUNNING, DETACHED, holding real
    ``-m aelix_coding_agent`` processes with nothing left to reap them. That path
    is reachable, not theoretical: ``PrintChannel.run`` writes the prompt file
    OUTSIDE its own ``try`` (``print_channel.py:930`` vs ``:931``) and
    ``write_prompt_file`` does ``mkdtemp`` + ``os.open``
    (``prompt_file.py:129-131``), so a full ``/tmp``, an ``EMFILE`` or a yanked
    ``TMPDIR`` raises ``OSError`` straight out — and four concurrent children each
    writing a prompt directory is precisely the load that makes it fire.

    *Rejected: ``asyncio.TaskGroup``.* It also cancels siblings on the first
    exception, but it wraps the outcome in an ``ExceptionGroup``, which changes
    how ``CancelledError`` reaches the frame above and would need this whole
    argument re-derived for no gain.
    """

    coros = [
        _member(batch, index, task, steps_left=1)
        for index, task in enumerate(batch.call.tasks)
    ]
    members = await asyncio.gather(*coros, return_exceptions=False)
    return list(members), 0


async def _run_chain(batch: _Batch) -> tuple[list[MemberOutcome], int]:
    """The tasks in order, each able to insert the previous one's summary.

    STOPS AT THE FIRST ``ok=False`` AND RETURNS THE PARTIAL CHAIN (§3.3).
    ``{previous}`` makes step *k+1* depend on step *k*'s summary, so continuing
    would feed a failure message — or ``"(no output)"`` (``envelope.py:32``) —
    into the next child AS IF IT WERE A RESULT. A ``status="declined"`` member
    stops it too: consent covers the whole batch (S4), so a decline is a decline
    of the batch.

    *Rejected: a ``continue_on_error`` schema parameter.* Under ``{previous}`` its
    only correct value is ``False``, and putting it on the schema invites the
    model to set it ``True``.

    The rendered step is size-checked AGAIN here, not only at parse time: a chain
    can grow past ``MAX_TASK_BYTES`` purely through substitution, and without this
    the oversize argv element reaches ``create_subprocess_exec`` and fails with an
    opaque ``OSError: [Errno 7]`` that the model reads as an unexplained spawn
    failure.

    No ``gather``, so cancellation needs no special handling: the ``await`` below
    is the only suspension point and ``CancelledError`` propagates out of it.
    """

    tasks = batch.call.tasks
    members: list[MemberOutcome] = []
    previous: str | None = None
    previous_trail: str | None = None
    for index, task in enumerate(tasks):
        try:
            rendered = render_step(task, previous, trail=previous_trail)
            check_task_size(rendered)
        except TaskTooLarge as exc:
            # NO CHILD EXISTS for this step, so it is "did not start" and not
            # "failed" — and it still stops the chain, because every later step
            # was going to be fed this one's summary.
            members.append(
                MemberOutcome.never_started(_refusal_envelope(batch.resolved, str(exc)))
            )
            break
        outcome = await _member(
            batch, index, rendered, steps_left=len(tasks) - index
        )
        members.append(outcome)
        if not outcome.ok:
            break
        # ``summary``, verbatim, INCLUDING any truncation marker — never
        # ``details`` (§3.1, and the reasoning is in ``chain.py``'s docstring).
        previous = outcome.result.summary
        # …and the EVIDENCE behind it. ``summary`` is the child's conclusion
        # only; without this, step k+1 cannot tell what ground step k covered,
        # or that step k's tools all failed while it still reported ``ok``.
        # ELASTIC — ``render_step`` drops it rather than let it push the step
        # over the ceiling, because an oversize step is refused and a refusal
        # stops the whole remaining chain.
        previous_trail = outcome.result.tool_trail
    return members, len(tasks) - len(members)


async def _member(
    batch: _Batch, index: int, task: str, *, steps_left: int
) -> MemberOutcome:
    """One member: acquire a permit, derive a clock, spawn exactly one child.

    THE PERMIT IS HELD BY A CONTEXT MANAGER, never ``await sem.acquire()`` with a
    bare ``release()`` after the body. A ``CancelledError`` mid-batch would skip
    that release, and because :data:`_SEM_BY_LOOP` is module level the leak is not
    scoped to this batch: :data:`MAX_CONCURRENCY` becomes permanently smaller and
    after four leaks the NEXT ``agent()`` call in the session parks on
    ``acquire()`` forever. S11 makes the REPL read-only while a turn runs and the
    batch deadline is only consulted AFTER the acquire, so nothing would ever end
    it.

    THE ACQUIRE IS THE ONLY ``await`` BEFORE ``spawn_granted``, AND IT IS OUTSIDE
    BOTH TOCTOU WINDOWS (S5 / dossier H12). ``_run``'s admission block —
    ``_admit_live()`` → budget check → ``+= 1`` → ``_new_id()`` → registry insert
    (``runtime.py:787-801``) — contains no ``await``, so asyncio cannot interleave
    two members inside it. Putting the acquire anywhere inside that block would
    split it and let two members both pass ``_admit_live`` before either
    registered. It is here, one frame above, where the only thing it orders is how
    many members reach the door at all. ``test_batch_executor`` pins the window
    await-free with an AST assertion.

    THE WHOLE BODY IS GUARDED. ``CancelledError`` is re-raised — it is the one
    thing that propagates — and every other ``BaseException`` becomes an envelope,
    because ``envelope.py:8-12`` already requires that of this layer and because
    ``gather``'s sibling-cancellation semantics (see :func:`_run_parallel`) depend
    on nothing else escaping.
    """

    # WHETHER A CHILD ROW EVER EXISTED, observed rather than inferred. ``_run``
    # publishes a first snapshot IMMEDIATELY after the registry insert
    # (``runtime.py:840``) and every refusal that precedes the insert —
    # ``_admit_live``, the per-prompt budget, a non-consented grant — returns
    # BEFORE it (``runtime.py:785-797``). So "this tap fired at least once" is
    # exactly "a delegation was admitted", which is the fact
    # ``aggregate.MemberOutcome`` needs.
    #
    # Deliberately NOT string-matching ``_TOO_MANY_LIVE`` / ``_BUDGET_EXHAUSTED``
    # against the summary: that would couple this module to ``runtime.py``'s
    # message wording and would misclassify a child that happened to echo one.
    admitted = False

    def _tap(progress: SubagentProgress) -> None:
        nonlocal admitted
        # Set FIRST. ``_publish`` swallows a tap's exception (``runtime.py:951-952``),
        # so a raising subscriber must not be able to lose the observation.
        admitted = True
        if batch.on_event is not None:
            batch.on_event(index, progress)

    try:
        async with _semaphore():
            # A cancellation that lands while parked cancels HERE: no process
            # exists, nothing to reap, nothing to release beyond the permit the
            # context manager owns.
            remaining_ms = int((batch.deadline - _now()) * 1000) - _kill_leg_reserve_ms(
                batch.call.mode, steps_left
            )
            if remaining_ms < MIN_TIMEOUT_MS:
                # An ENVELOPE, not a raise and not a silent drop: the batch is
                # never trimmed, so the model is told which tasks got no clock.
                return MemberOutcome.never_started(
                    _refusal_envelope(batch.resolved, _BATCH_BUDGET_EXHAUSTED)
                )
            # THE PROFILE'S OWN BUDGET IS THE DEFAULT, NOT ``DEFAULT_TIMEOUT_MS``
            # — this line mirrors ``print_channel.py:894-898`` exactly, and it
            # must, because the executor is what makes ``plan.timeout_ms`` non-
            # ``None``. Substituting the module default here would mean the
            # channel's own ``profile.timeout_ms`` fallback is UNREACHABLE for
            # every batch member, so an author who bounded their agent to one
            # minute in frontmatter (``agents/profile.py:398``) would silently
            # get ten — times up to eight children — while ``mode="single"``,
            # which passes ``pending.call.timeout_ms`` straight through
            # (``extension.py:1024``), still honoured it. Same profile, two modes,
            # two clocks.
            requested_ms = (
                batch.call.timeout_ms
                if batch.call.timeout_ms is not None
                else (batch.resolved.profile.timeout_ms or DEFAULT_TIMEOUT_MS)
            )
            effective_ms = min(requested_ms, MAX_TIMEOUT_MS, remaining_ms)
            result = await batch.runtime.spawn_granted(
                batch.grant,
                batch.resolved,
                task,
                cwd=batch.cwd,
                timeout_ms=effective_ms,
                permission_floor=_live_floor(batch),
                on_event=_tap,
            )
            return _classify(result, admitted=admitted)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 — the envelope always returns
        return _classify(
            _refusal_envelope(
                batch.resolved, f"delegation failed to start: {exc.__class__.__name__}: {exc}"
            ),
            admitted=admitted,
        )


def _classify(result: SubagentResult, *, admitted: bool) -> MemberOutcome:
    """Attach the one fact the envelope cannot carry: did a child ever exist.

    ``SubagentResult`` has no field for it and gaining one would be a
    product-core edit (S2). ``admitted`` is ``True`` iff ``_run`` got as far as
    registering a row, which is the last moment before a process is created and
    the first moment the per-prompt budget has been charged.
    """

    if admitted:
        return MemberOutcome.ran(result)
    return MemberOutcome.never_started(result)


def _live_floor(batch: _Batch) -> PermissionMode | None:
    """The §3.9 floor: ``None`` unless the PARENT TIGHTENED since the batch began.

    The problem this closes. ``_host_posture()`` is a live getter
    (``extension.py:392-398``) but it is read exactly ONCE per call, inside
    ``_grant_for`` (``extension.py:762``), and baked into ``grant.mode``, which
    becomes every member's ``SpawnPlan.permission_mode``
    (``runtime.py:825``). Meanwhile shift+tab stays live during a running
    turn — its binding is gated only on ``Condition(lambda:
    self._input_has_focus() and not self.is_modal_open())``
    (``chrome.py:967-970``), and the input window holds focus while a turn runs;
    that is precisely how Enter reaches ``steer()``. Under P2 the resulting window
    was one child and milliseconds. Here it is up to
    :data:`MAX_BATCH_WALL_MS`, and S11 has deliberately removed every other
    mid-turn control, so shift+tab is the only lever there is.

    IT IS A GATE, NOT AN UNCONDITIONAL RANK-MIN, and that much is a deliberate
    correction to the two lines §3.9 spells out. ``mode = min(grant.mode, live,
    key=posture_rank)`` evaluated with an UNCHANGED posture returns ``live``
    whenever the human WIDENED at the dialog — ``consent._may_widen`` only offers
    the rung when ``AUTO_ACCEPT`` is strictly looser than the clamp
    (``consent.py:527``), so ``grant.mode`` is strictly above ``live`` in exactly
    the widened case. The literal form would therefore revoke every widening the
    human explicitly granted, on every batch, with no posture change at all — and
    it would break §7 invariant 1's own stated proof that "with a steady posture,
    every member's ``SpawnPlan.permission_mode`` in an 8-task batch is identical
    and equals the single-spawn value".

    TWO SIGNALS, NOT ONE, AND THE SECOND ONE IS WHY THIS FUNCTION WORKS AT ALL.
    Gating on the CLAMP alone — the shipped first draft — was measured inert in
    precisely the case the bullets below advertise. ``child_permission_mode``
    reaches only three of the five ranks and, whenever ``_may_widen`` returns
    True, is provably ``PLAN``: the rung requires ``AUTO_ACCEPT`` strictly looser
    than the clamp (``consent.py:527``) and the clamp's reachable set is
    ``{PLAN, AUTO_ACCEPT, YOLO}`` (``DEFAULT`` is folded into ``PLAN`` at
    ``posture.py:231``), so the only value strictly below ``AUTO_ACCEPT`` is
    ``PLAN``. ``PLAN`` is rank 0. Nothing is strictly below rank 0, so
    ``rank(live) < rank(clamp_at_start)`` could never fire for a widened batch —
    measured as 0 hits over the whole (approval_mode × posture_at_start ×
    posture_now × has_ui) matrix. The human raised authority to
    ``auto-accept-edits``, pressed shift+tab back to plan mid-batch, and wave 2
    kept writing.

    The missing input was not a different comparison of the same two derived
    values; it was the PARENT'S OWN POSTURE, which does not saturate. Widening is
    only ever offered under a ``PLAN`` or ``DEFAULT`` parent, and a ``DEFAULT``
    parent still has ``PLAN`` beneath it to shift+tab into — so
    :attr:`_Batch.posture_at_start` is a reference point that a real tightening
    can actually fall below. Hence:

    * ``clamp_tightened`` — the original signal. It is the one that carries the
      ``has_ui`` half: ``has_ui`` is the only input deciding what
      ``approval_mode: "ask"`` clamps to (``posture.py:201-204``), and a batch
      that outlives its UI changes the CLAMP with the posture untouched.
    * ``parent_tightened`` — the new one, and the only one that can fire against
      a saturated clamp. shift+tab is the user saying "less authority now"; a
      per-spawn widening granted before that is not a licence to ignore it.

    EITHER signal admits the floor, which makes the change monotone in the safe
    direction: it can only ADD floors, never remove one, and the floor it returns
    is rank-MINed by ``runtime._tighten`` (``runtime.py:955-966``) so no member
    can ever be RAISED. Under a steady posture and a steady UI neither fires, so
    §7 invariant 1 is untouched.

    *Rejected: gating the widened case on ``rank(live) < rank(grant.mode)``.*
    Measured: with a STEADY ``default`` parent that yields ``floor=plan`` and
    revokes the widening — it is the unconditional rank-min again, wearing a
    condition.

    What ships, then, is the §3.9 prose exactly:

    * the human's answer stays a CEILING — nothing here can raise a member above
      ``grant.mode``, because the runtime rank-MINs whatever this returns;
    * the live parent stays a FLOOR that can only tighten;
    * tightening the parent revokes a widening for members that have not started;
    * loosening it does NOT raise wave 2 above ``grant.mode``.

    Both reference points are read at the top of :func:`run_batch` rather than at
    the hook, so a shift+tab in the hook→execute gap is missed. That gap is the
    one P2 already has for a single spawn and is milliseconds wide; the window
    this function exists for is the wave-1→wave-2 one, which is up to 30 minutes.
    """

    live_posture = batch.posture()
    live = child_permission_mode(
        batch.resolved.profile.approval_mode,
        live_posture,
        batch.resolved.scope,
        # LIVE, exactly like the posture beside it. ``has_ui`` is the only input
        # that decides what ``approval_mode: "ask"`` clamps to
        # (``posture.py:201-204``), so freezing it would let a batch that
        # outlives its UI keep the LOOSER of the two clamps — the same
        # stale-value defect §3.9 exists to close, for the other argument.
        has_ui=batch.has_ui(),
    )
    clamp_tightened = posture_rank(live) < posture_rank(batch.clamp_at_start)
    parent_tightened = posture_rank(live_posture) < posture_rank(batch.posture_at_start)
    if clamp_tightened or parent_tightened:
        # ``live`` and NOT ``live_posture``: what a member may run under is the
        # CHILD clamp, never the parent's raw posture — returning the latter
        # would hand a ``yolo`` parent's raw ``yolo`` to a child that
        # ``child_permission_mode`` had deliberately capped. It is also already
        # <= the live parent by construction (``posture.py:227``).
        return live
    return None


def _kill_leg_reserve_ms(mode: SubagentMode, steps_left: int) -> int:
    """Wall clock held back so a timing-out member's kill legs fit under the cap.

    A member that hits its deadline does not stop there: ``reap`` waits
    ``DEFAULT_GRACE_SECONDS = 5.0`` (``reaper.py:80``) and then drains for a
    bounded ``POST_EXIT_DRAIN_SECONDS = 2.0`` (``print_channel.py:135``).

    In PARALLEL those legs overlap, so one reserve covers the whole wave. In a
    CHAIN they are strictly sequential, so an eight-step chain that runs into the
    ceiling would overshoot by up to 8 × 7 s ≈ 56 s — hence one reserve per step
    still to run, INCLUDING this one, which is the step whose own kill leg is
    being paid for. Reserving rather than overshooting is why the ADR can state
    the ceiling as a number it actually means.
    """

    if mode == "chain":
        return KILL_LEG_RESERVE_MS * max(steps_left, 1)
    return KILL_LEG_RESERVE_MS


def _semaphore() -> asyncio.Semaphore:
    """The per-loop batch semaphore. See :data:`_SEM_BY_LOOP`."""

    loop = asyncio.get_running_loop()
    sem = _SEM_BY_LOOP.get(loop)
    if sem is None:
        # EVICT ONLY CLOSED LOOPS, NEVER "EVERYTHING BUT ME". A blanket
        # ``clear()`` here cannot tell a retired loop from a live one, and with
        # two loops alive in one process (a thread running its own
        # ``asyncio.run``) the second loop's first member would evict the FIRST
        # loop's semaphore WHILE ITS PERMITS ARE HELD — the first loop's next
        # member then mints a fresh 4-permit one and the process runs 8 children
        # against a bound of 4. That is the bound this module raises at import to
        # protect (see :data:`MAX_CONCURRENCY`), so it must not be droppable by a
        # housekeeping line. ``is_closed()`` is the only reliable "retired"
        # signal, and it is exact: pytest-asyncio closes each test's loop, which
        # is the case the eviction was written for.
        #
        # NOT a ``WeakKeyDictionary``: the value strongly references the key
        # (``_LoopBoundMixin`` pins the loop on the semaphore once contended), so
        # entries would never be collected and the weakness would be a lie.
        for retired in [
            other for other in _SEM_BY_LOOP if other is not loop and other.is_closed()
        ]:
            del _SEM_BY_LOOP[retired]
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        _SEM_BY_LOOP[loop] = sem
    return sem


def _refusal_envelope(resolved: ResolvedProfile, message: str) -> SubagentResult:
    """An envelope for a member that never got a process. Returned, never raised.

    Mirrors ``runtime._error_result`` deliberately — same ``status``, same
    ``summary``/``error`` pair — so a batch member refused HERE and one refused by
    the runtime read identically to the model.
    """

    return SubagentResult(
        id=_new_spawn_id(),
        profile=resolved.name,
        ok=False,
        status="error",
        summary=message,
        error=message,
    )


def _now() -> float:
    """Monotonic seconds. A module-level indirection so the deadline arithmetic
    is testable under a fake clock without sleeping — the same shape
    ``runtime._now`` uses."""

    return time.monotonic()


__all__ = [
    "KILL_LEG_RESERVE_MS",
    "MAX_BATCH_WALL_MS",
    "MAX_CONCURRENCY",
    "BatchOutcome",
    "run_batch",
]
