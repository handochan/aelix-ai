"""The batch executor — ``mode="parallel"`` and ``mode="chain"`` (ADR-0199 §3.3/§3.5).

THE TOPOLOGY LIVES HERE AND NOWHERE ELSE. ``_SubagentRuntimeImpl.spawn_granted``
is called ONCE PER MEMBER with ``mode="single"``: one spawn is one child. That is
why the seam still raises on a non-``single`` mode (``runtime._UNSUPPORTED_MODE``)
even though this phase ships parallel and chain — passing a topology to the seam
is a programming error, not a request, and a P4 runtime author must find that out
immediately rather than by watching one child run where eight were asked for.

THREE BOUNDS, AND THEY ARE THREE DIFFERENT THINGS. Getting these confused is how
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

THERE IS NO WALL-CLOCK BOUND (#604, ADR-0276). ``MAX_BATCH_WALL_MS`` — 30
minutes for the whole call, enforced by deriving each member's ``timeout_ms``
from what was left of it — is gone with every other delegation time limit: it
cut off chains that were still working. A batch runs until its members finish
or a human ends it, and cancellation (below) is how a human does.

CANCELLATION IS THE ONE THING THAT PROPAGATES. ``ctx.signal`` is dead — it is
always ``None``: ``AgentHarness`` calls ``agent_loop(...)`` with no ``signal=``
argument (``harness/core.py:5427-5433``), ``agent_loop``'s parameter defaults to
``None`` (``loop.py:107``) and is threaded unchanged into
``ToolExecutionContext(signal=signal)`` (``loop.py:638-640``). Abort is
``turn_task.cancel()`` (``core.py:1843-1845``). So ``CancelledError`` is the ONLY
channel by which a Ctrl+C reaches a child, and every rule below about it is load
bearing rather than defensive.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.subagent_contract import SubagentResult

from aelix_agents.aggregate import MemberOutcome
from aelix_agents.chain import TaskTooLarge, check_task_size, render_step
from aelix_agents.posture import child_permission_mode, posture_rank
from aelix_agents.runtime import MAX_LIVE_CHILDREN, SpawnRecordMeta
from aelix_agents.runtime import _new_id as _new_spawn_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from aelix_coding_agent.builtin.permission_mode import PermissionMode
    from aelix_coding_agent.subagent_contract import (
        ResolvedProfile,
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
a ninth live child in a session that already holds four is a resource refusal.

WHAT BOUNDS HOW LONG THE USER WAITS is the human, not a clock (#604, ADR-0276).
While a delegation runs, Enter is still routed to ``steer()`` as a MESSAGE
(``chrome.py:784-790``), so the way out is the interrupt: Esc or Ctrl+C cancel
the turn, which cancels this batch and kills every live member
(``print_channel._eager_abort``); SIGTERM / SIGHUP abort the turn the same way
(``tui/shell.py``), and ``-p`` turns SIGINT into the same cancellation. The
30-minute ``MAX_BATCH_WALL_MS`` used to be documented here as "the only bound on
how long the user is locked out"; it was a bound on the work, and it is gone."""

# ONE SEMAPHORE PER RUNNING LOOP, and the precedent is ``consent.py:121-140``.
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
    its UI, for as long as the batch runs — which no clock bounds (#604)."""
    posture: Callable[[], PermissionMode]
    """The parent's LIVE posture getter (``extension._host_posture``), read once
    per member — never a value captured at call time. shift+tab stays bound while
    a turn runs (``chrome.py:967-970``), and wave 2 may start any time after
    wave 1 — there is no time limit on wave 1 (#604)."""
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
    ever offered under a ``PLAN`` or ``DEFAULT`` parent (``consent.py:560``
    requires ``AUTO_ACCEPT`` strictly looser than the clamp), and a ``DEFAULT``
    parent still has ``PLAN`` below it to shift+tab into. See
    :func:`_live_floor`."""
    on_event: Callable[[int, SubagentProgress], None] | None
    record: SpawnRecordMeta | None = None
    """The call's record template (#199) — ``tool_call_id`` and topology, no
    index. :func:`_member` binds each member's own index onto a copy, the same
    index ``on_event`` carries, so the parent's records name the task position
    the model addressed."""


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
    record: SpawnRecordMeta | None = None,
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
    is minted INSIDE ``_run`` (``runtime.py:1386``), and for members 5-8 not until
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
        on_event=on_event,
        record=record,
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
      second-Ctrl+C escalation at ``print_channel.py:1760-1762`` (``_reap``'s
      ``except CancelledError: self._eager_abort(proc, row); raise``).
    * No ``ensure_future`` without holding the handle and no ``shield``: a
      detached member is a child nobody can kill, and ``PrintChannel.run``
      documents that ``CancelledError`` is the ONE thing it propagates and that
      it kills the child eagerly before re-raising (``print_channel.py:1284-1292``,
      ``:944-951``).
    * Awaited HERE rather than returned: cancelling the task that owns this frame
      cancels the ``_GatheringFuture``, which is the only path that cancels the
      children — ``gather`` propagating a first exception does NOT.

    Which is exactly why :func:`_member` swallows every non-cancel exception into
    an envelope. ``gather(..., return_exceptions=False)`` re-raises the FIRST
    exception immediately and leaves its siblings RUNNING, DETACHED, holding real
    ``-m aelix_coding_agent`` processes with nothing left to reap them. That path
    is reachable, not theoretical: ``PrintChannel.run`` writes the prompt file
    OUTSIDE its own ``try`` (``print_channel.py:1367`` vs ``:1368``) and
    ``write_prompt_file`` does ``mkdtemp`` + ``os.open``
    (``prompt_file.py:130-132``), so a full ``/tmp``, an ``EMFILE`` or a yanked
    ``TMPDIR`` raises ``OSError`` straight out — and four concurrent children each
    writing a prompt directory is precisely the load that makes it fire.

    *Rejected: ``asyncio.TaskGroup``.* It also cancels siblings on the first
    exception, but it wraps the outcome in an ``ExceptionGroup``, which changes
    how ``CancelledError`` reaches the frame above and would need this whole
    argument re-derived for no gain.
    """

    coros = [_member(batch, index, task) for index, task in enumerate(batch.call.tasks)]
    # On a cancel, CPython's ``gather`` raises here as soon as the FIRST member
    # ends cancelled (its ``_done_callback`` sets the outer exception) while the
    # others may still be cleaning up — which is why the call's cancel report
    # builds those from their live streams (``runtime.CallEnvelopes``, #604).
    members = await asyncio.gather(*coros, return_exceptions=False)
    return list(members), 0


async def _run_chain(batch: _Batch) -> tuple[list[MemberOutcome], int]:
    """The tasks in order, each able to insert the previous one's summary.

    STOPS AT THE FIRST ``ok=False`` AND RETURNS THE PARTIAL CHAIN (§3.3).
    ``{previous}`` makes step *k+1* depend on step *k*'s summary, so continuing
    would feed a failure message — or ``"(no output)"`` (``envelope.py:34``) —
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
        outcome = await _member(batch, index, rendered)
        members.append(outcome)
        if not outcome.ok:
            break
        # ``summary``, verbatim, INCLUDING any truncation marker — never
        # ``details`` (§3.1, and the reasoning is in ``chain.py``'s docstring).
        #
        # ONLY AN ``ok`` STEP REACHES THIS LINE, which is why #604's failure
        # templates (ADR-0276 D11/D12: a reason, a partial answer, written files)
        # never enter ``{previous}`` and this hand-off needed no change: the
        # ``break`` above ends the chain at the first step that did not finish,
        # and the model reads that step's D12 block in the aggregate instead.
        previous = outcome.result.summary
        # …and the EVIDENCE behind it. ``summary`` is the child's conclusion
        # only; without this, step k+1 cannot tell what ground step k covered,
        # or that step k's tools all failed while it still reported ``ok``.
        # ELASTIC — ``render_step`` drops it rather than let it push the step
        # over the ceiling, because an oversize step is refused and a refusal
        # stops the whole remaining chain.
        previous_trail = outcome.result.tool_trail
    return members, len(tasks) - len(members)


async def _member(batch: _Batch, index: int, task: str) -> MemberOutcome:
    """One member: acquire a permit, spawn exactly one child.

    THE PERMIT IS HELD BY A CONTEXT MANAGER, never ``await sem.acquire()`` with a
    bare ``release()`` after the body. A ``CancelledError`` mid-batch would skip
    that release, and because :data:`_SEM_BY_LOOP` is module level the leak is not
    scoped to this batch: :data:`MAX_CONCURRENCY` becomes permanently smaller and
    after four leaks the NEXT ``agent()`` call in the session parks on
    ``acquire()`` forever, and nothing but the human's interrupt would end it —
    there is no clock on a delegation (#604).

    THE ACQUIRE IS THE ONLY ``await`` BEFORE ``spawn_granted``, AND IT IS OUTSIDE
    BOTH TOCTOU WINDOWS (S5 / dossier H12). ``_run``'s admission block —
    ``_admit_live()`` → budget check → ``+= 1`` → ``_new_id()`` → registry insert
    (``runtime.py:1374-1388``) — contains no ``await``, so asyncio cannot interleave
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
    # (``runtime.py:1429``) and every refusal that precedes the insert —
    # ``_admit_live``, the per-prompt budget, a resume refused at its limits
    # (#610), a non-consented grant — returns BEFORE it
    # (``runtime.py:1328-1385``). So "this tap fired at least once" is
    # exactly "a delegation was admitted", which is the fact
    # ``aggregate.MemberOutcome`` needs.
    #
    # Deliberately NOT string-matching ``_TOO_MANY_LIVE`` / ``_BUDGET_EXHAUSTED``
    # against the summary: that would couple this module to ``runtime.py``'s
    # message wording and would misclassify a child that happened to echo one.
    admitted = False

    def _tap(progress: SubagentProgress) -> None:
        nonlocal admitted
        # Set FIRST. ``_publish`` swallows a tap's exception (``runtime.py:1877-1878``),
        # so a raising subscriber must not be able to lose the observation.
        admitted = True
        if batch.on_event is not None:
            batch.on_event(index, progress)

    try:
        async with _semaphore():
            # A cancellation that lands while parked cancels HERE: no process
            # exists, nothing to reap, nothing to release beyond the permit the
            # context manager owns.
            #
            # NO CLOCK IS DERIVED HERE ANY MORE (#604, ADR-0276). A member used
            # to get ``min(requested, 30 min, what was left of the call's 30
            # minutes)`` and was refused below one second; a member now runs
            # until it finishes, exactly as a ``mode="single"`` child does.
            result = await batch.runtime.spawn_granted(
                batch.grant,
                batch.resolved,
                task,
                cwd=batch.cwd,
                permission_floor=_live_floor(batch),
                on_event=_tap,
                record=(
                    None
                    if batch.record is None
                    else dataclasses.replace(batch.record, index=index)
                ),
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
    (``extension.py:500-506``) but it is read exactly ONCE per call, inside
    ``_grant_for`` (``extension.py:1190``), and baked into ``grant.mode``, which
    becomes every member's ``SpawnPlan.permission_mode``
    (``runtime.py:1421``). Meanwhile shift+tab stays live during a running
    turn — its binding is gated only on ``Condition(lambda:
    self._input_has_focus() and not self.is_modal_open())``
    (``chrome.py:967-970``), and the input window holds focus while a turn runs;
    that is precisely how Enter reaches ``steer()``. Under P2 the resulting window
    was one child and milliseconds. Here it is as long as the batch runs — no
    clock bounds it since #604 — and S11 has deliberately removed every other
    mid-turn control short of interrupting the turn, so shift+tab is the only
    lever that leaves the batch running.

    IT IS A GATE, NOT AN UNCONDITIONAL RANK-MIN, and that much is a deliberate
    correction to the two lines §3.9 spells out. ``mode = min(grant.mode, live,
    key=posture_rank)`` evaluated with an UNCHANGED posture returns ``live``
    whenever the human WIDENED at the dialog — ``consent._may_widen`` only offers
    the rung when ``AUTO_ACCEPT`` is strictly looser than the clamp
    (``consent.py:560``), so ``grant.mode`` is strictly above ``live`` in exactly
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
    than the clamp (``consent.py:560``) and the clamp's reachable set is
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
    is rank-MINed by ``runtime._tighten`` (``runtime.py:1928-1939``) so no member
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
    this function exists for is the wave-1→wave-2 one, which nothing bounds.
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
    """Monotonic seconds, for :attr:`BatchOutcome.wall_ms` — a measurement, not a
    bound (there is none since #604). A module-level indirection so the arithmetic
    is testable under a fake clock without sleeping — the same shape
    ``runtime._now`` uses."""

    return time.monotonic()


__all__ = [
    "MAX_CONCURRENCY",
    "BatchOutcome",
    "run_batch",
]
