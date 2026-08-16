"""The rpc delegation channel — one child per task, driven over the JSONL wire.

The second implementation of the channel seam beside
:class:`~aelix_agents.print_channel.PrintChannel`, and like it, THIS FILE
SPAWNS. Every process-touching statement lives here rather than in
product-core, because ADR-0197's 3-band rule gives product-core the CONTRACT
and this bundled extension the policy; ``tests/agents/test_p2_band_boundaries``
is the machine gate.

ONE CHILD PER TASK. REUSE IS NOT A TUNING CHOICE, IT IS IMPOSSIBLE
------------------------------------------------------------------

The obvious reading of "a long-lived rpc channel" is one child answering many
delegations. That was measured and it cannot be built:

* **The 29-command rpc surface cannot re-target a live child.** There is no
  ``set_permission_mode``, no ``set_system_prompt``, no ``set_tools``, no
  ``set_cwd``. Every :class:`~aelix_agents.print_channel.SpawnPlan` field that
  varies per delegation — the resolved profile (system prompt, tools), the
  consent-resolved posture, the contained cwd — is startup-only.

  That is a SECURITY consequence, not a fidelity one. ADR-0199's
  ``permission_floor`` exists so a batch's later waves honour a posture the
  human tightened after wave 1 started; on a reused child there is no command
  to tighten with, so the floor would be silently inert while
  ``SubagentResult.permission_mode`` reported a posture the child was not
  running under.

* **The runtime deregisters a child that outlives its delegation.**
  ``_SubagentRuntimeImpl._run`` pops the registry row in a ``finally``, and
  ``stop_all`` iterates that registry. Measured against the real runtime, a
  child that survived ``run()`` was invisible to ``list`` / ``status`` / ``stop``
  / ``stop_all`` and to session teardown — verbatim the orphan ADR-0197 forbids
  and that ``stop_all``'s own docstring says P3 already fixed once.

* **``set_model`` — the one mutable knob, and the last argument for reuse —
  does not work on a spawned child at all.** ``cli/entry.py``'s rpc branch
  passes no model registry, so the child answers ``set_model`` /
  ``cycle_model`` / ``get_available_models`` with "no registry configured".
  The model has to arrive on the argv — from the profile's ``--model`` /
  ``--provider``, or, when the profile declares neither, forwarded from the
  parent's own effective model (see :func:`build_rpc_child_argv`).

So what does this channel buy over :class:`PrintChannel`, which is simpler on
every axis? A live, bidirectional command channel to a running turn: ``abort``
that the child ACKNOWLEDGES rather than a signal, ``steer`` and ``follow_up``
at turn boundaries, and a correlated request/response surface for anything a
future chain mode needs. On a strict one-shot path it buys none of that, and
the honest summary is that it costs the containment work below to reach parity
with a channel that already had it.

THE TASK GOES OVER THE WIRE, NOT ON THE ARGV
--------------------------------------------

``profile_to_argv(oneshot=False)`` SILENTLY DROPS its ``task`` argument — the
trailing ``Task: …`` positional is appended only on the oneshot branch. That is
correct for rpc (the child is a server, not a one-shot), but it means the task
must be delivered as a ``prompt`` command and that a future author "fixing" the
argv would change nothing. It also means this channel must append
``--no-session`` itself, which the oneshot prefix supplies and the rpc prefix
does not: without it the child writes a real session file into the user's
history, and P3 allows twelve delegations per prompt.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.agents.resolver import profile_to_argv
from aelix_coding_agent.rpc.rpc_client import (
    RpcClient,
    RpcClientError,
    RpcClientOptions,
    RpcServerExited,
)

from aelix_agents.envelope import DEFAULT_OUTPUT_CAP, build_result
from aelix_agents.print_channel import (
    DEFAULT_TIMEOUT_MS,
    POST_EXIT_DRAIN_SECONDS,
    STDERR_RING_BYTES,
    STREAM_LIMIT_BYTES,
    RunningChild,
    SpawnPlan,
    abort_child,
    apply_cost_fallback,
    build_child_env,
    narrow_context_files,
    narrow_tools,
)
from aelix_agents.prompt_file import remove_prompt_dir, write_prompt_file
from aelix_agents.reaper import (
    DEFAULT_GRACE_SECONDS,
    descendant_pids,
    kill_tree,
    pdeathsig,
    reap,
)
from aelix_agents.stream import MAX_LINE_BYTES, _StreamState, reduce_event
from aelix_agents.trust import child_trust_argv

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.builtin.permission_mode import PermissionMode
    from aelix_coding_agent.subagent_contract import SubagentOutcome, SubagentResult

_TERMINAL_STATES = frozenset({"done", "error", "stopped"})
"""The :data:`~aelix_coding_agent.subagent_contract.SubagentState` values after
which this channel must publish no further progress snapshot.

A THIRD copy beside ``runtime._TERMINAL_STATES`` (``runtime.py:143``) and
``progress._TERMINAL_STATES`` (``progress.py:96``), for the reason runtime's own
copy already gives: the row lifecycle is READ here — ``_eager_abort`` writes
``row.state`` and :meth:`RpcChannel._listener` gates on it — and neither the
renderer nor this channel may become a dependency of the module that owns it.
``test_the_three_copies_of_the_terminal_set_agree`` asserts all three equal, so
the copy cannot drift.
"""

STDIN_EOF_EXIT_SECONDS = 5.0
"""How long the child gets to exit on its own after its stdin is closed.

``run_rpc_mode`` races a shutdown event against stdin EOF, so closing the
command channel is a request to finish — no signal, no grace window, and the
child unwinds its own extensions and session. Only a child that does not take
the hint is signalled. Generous enough for a real teardown, bounded because one
child per task means the child MUST die before the delegation is done.
"""


@dataclass(frozen=True)
class _TurnOutcome:
    """How the turn ended, before the envelope decides what that means."""

    outcome: SubagentOutcome
    error: str | None = None


def build_rpc_child_argv(
    child_profile: AgentProfile,
    *,
    prompt_path: str,
    task: str,
    permission_mode: PermissionMode,
    child_cwd: str,
    parent_cwd: str,
    parent_model: Any | None = None,
) -> list[str]:
    """The rpc child's exact command line.

    Deliberately the SAME signature as
    :func:`~aelix_agents.print_channel.build_child_argv` so the two are
    interchangeable through the ``argv_builder`` seam — and ``task`` is
    therefore accepted and **ignored**. It is not a leak: an rpc child receives
    its task as a ``prompt`` command over the wire, and ``profile_to_argv``
    drops the argument on this branch anyway. Do not "fix" it by appending the
    task; that would put the user's prompt on a command line for no effect.

    ``-m aelix_coding_agent`` and nothing else. Specifically NOT ``-m aelix``,
    which is the umbrella package's mock-echo demo, and NOT the ``aelix``
    console script, which in a worktree resolves to the other tree's editable
    install.

    ``parent_model`` is forwarded exactly as the print channel forwards it (see
    :func:`~aelix_agents.print_channel.build_child_argv`), and the module
    docstring's warning that "the model has to come from the profile's
    ``--model`` / ``--provider``" is no longer the whole truth: it may also come
    from the parent.
    """

    del task  # delivered over the wire; see the docstring
    return [
        sys.executable,
        "-m",
        "aelix_coding_agent",
        *profile_to_argv(
            child_profile,
            prompt_path=prompt_path,
            oneshot=False,
            parent_model=parent_model,
        ),
        # The rpc prefix omits this and the oneshot prefix supplies it. Without
        # it every delegated child writes a session file the user never started
        # and then finds in their /resume picker.
        "--no-session",
        "--permission-mode",
        permission_mode.value,
        *child_trust_argv(Path(child_cwd), Path(parent_cwd)),
        # Belt-and-braces with the depth env var: the var stops the extension
        # loading, the flag stops the settings gate turning it back on.
        "--no-agents",
    ]


def build_rpc_child_env(
    profile: AgentProfile, *, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The rpc child's environment: the shared recipe, minus the stdin timeout.

    ``AELIX_STDIN_TIMEOUT`` is aimed at ``cli/entry.py``'s piped-stdin reader,
    which only runs for print/json — so it is inert here today. It is REMOVED
    rather than left at ``"1"`` because the two ways of being wrong are not
    symmetric: on this path stdin is the TRANSPORT, so if that flag ever became
    mode-agnostic, a one-second stdin timeout would tear down the command
    channel a second after the child booted. Deleting it makes the child
    inherit whatever the parent had, which is inert in exactly the same way and
    harmless in the failure case.
    """

    env = build_child_env(profile, base=base)
    env.pop("AELIX_STDIN_TIMEOUT", None)
    return env


class RpcChannel:
    """One delegation, start to envelope, over a JSONL command channel.

    Stateless between runs — all per-run state lives on the
    :class:`RunningChild` row the caller owns — so a single instance is shared
    by the whole session, exactly like :class:`PrintChannel`. That property is
    what makes it safe as the session-wide singleton the extension holds, and
    it is only true BECAUSE of one child per task: a channel holding a live
    client would be shared by up to ``MAX_LIVE_CHILDREN`` concurrent
    delegations.

    :param grace: SIGTERM → SIGKILL window handed to the reaper.
    :param model_registry: read LIVE (a callable), because the registry is
        rebound on ``/reload`` and a captured one goes stale.
    :param argv_builder: renders a plan into a command line. Injectable so the
        subprocess tests can drive the real machinery against a purpose-built
        stub child.
    :param env_builder: renders a profile into an environment.
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
        self._argv_builder = argv_builder or build_rpc_child_argv
        self._env_builder = env_builder or build_rpc_child_env

    async def run(
        self,
        plan: SpawnPlan,
        *,
        child: RunningChild | None = None,
        on_stream: Callable[[_StreamState], None] | None = None,
    ) -> SubagentResult:
        """Spawn, drive one turn, shut down, and return an envelope.

        NEVER raises except on cancellation, which is the one thing that
        propagates: it means the parent's turn is being torn down, so there is
        nobody left to render a result. The child is killed EAGERLY first — a
        second interrupt means "stop waiting", not "restart the clock".
        """

        started = time.monotonic()
        row = child if child is not None else RunningChild(
            id=plan.id, profile=plan.resolved.name
        )
        profile = plan.resolved.profile
        narrowing = narrow_tools(profile, plan.parent_tools)
        # The parent's ``-nc``, clamped onto the child exactly as the print
        # channel clamps it — same function, so the two channels cannot drift.
        child_profile = narrow_context_files(
            narrowing.profile, plan.parent_context_files
        )
        state = row.stream
        output_cap = (
            plan.output_cap if plan.output_cap is not None else profile.output_cap
        )
        timeout_ms = (
            plan.timeout_ms
            if plan.timeout_ms is not None
            else (profile.timeout_ms or DEFAULT_TIMEOUT_MS)
        )
        # Captured as the run goes, because the client drops its process on
        # ``stop`` and the envelope is built after teardown.
        captured: dict[str, Any] = {"stderr": "", "dropped": 0, "exit_code": None}

        def _envelope(
            *, outcome: SubagentOutcome, error: str | None = None
        ) -> SubagentResult:
            apply_cost_fallback(
                state, self._model_registry() if self._model_registry else None
            )
            state.dropped_lines = int(captured["dropped"])
            return build_result(
                id=plan.id,
                profile=plan.resolved.name,
                state=state,
                outcome=outcome,
                exit_code=captured["exit_code"],
                stderr_tail=str(captured["stderr"]),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                output_cap=output_cap if output_cap is not None else DEFAULT_OUTPUT_CAP,
                permission_mode=plan.permission_mode.value,
                dropped_tools=narrowing.dropped,
                error=error,
            )

        def _listener(payload: dict[str, Any]) -> None:
            # ONE SUBSCRIPTION, TWO CONSUMERS WITH DIFFERENT LIFETIMES, so each
            # is gated on its own condition. Moving the single ``unsubscribe()``
            # instead moves BOTH, and they do not want the same window: the
            # accumulator has to keep folding through the teardown drain, while
            # the progress tap has to stop the moment the row goes terminal.
            #
            # BOTH halves are wrapped, and neither is paranoia. ``reduce_event``
            # documents "NEVER raises" and once did not — a line carrying
            # ``"usage": {"input": Infinity}`` reached ``int(inf)``. And a
            # broken progress subscriber must not be able to fail a delegation
            # that is otherwise succeeding.
            #
            # THE ACCUMULATOR STOPS AT THE STREAM'S TERMINATOR. ``agent_end`` is
            # the event ``prompt_and_wait`` itself waits on
            # (``rpc_client.py:868-871``), so a line after it is by definition
            # not this turn's data — and folding one in is last-write-wins on
            # ``summary``, ``stop_reason`` and ``error_message``, plus an
            # unconditional ``turns += 1`` and a ``tokens`` LEVEL overwrite.
            # ``build_result``'s ``state.stop_reason in ("error", "aborted")``
            # disjunct (``envelope.py:308-312``) is NOT gated on the caller's
            # outcome, so one late line flips a finished, exit-0 delegation to
            # ``ok=False``. Measured on a child that finishes cleanly and then
            # writes one more ``message_end`` after its stdin EOF:
            # ``ok=False summary='shutdown hook noise' turns=2 tokens=1``,
            # against ``ok=True summary='the real answer' turns=1 tokens=10``.
            #
            # GATED ON THE STREAM, NOT ON THE CALLER'S OUTCOME, and the
            # difference is the whole of fix ①: a child that timed out, was
            # cancelled, was aborted or died mid-turn never emitted
            # ``agent_end``, so ``saw_agent_end`` is ``False`` on every one of
            # those paths and the teardown drain still folds everything. The
            # caller-outcome variant — unsubscribing when ``_run_turn``
            # returns — was measured and rejected: it still folded 1 145
            # post-terminator lines, because ``agent_end`` resolves the waiter's
            # future INSIDE this fan-out while the stdout pump keeps delivering.
            #
            # ``saw_agent_end`` WAS A DIAGNOSTIC FLAG AND IS NOW A GATE. If a
            # future kernel change makes ``agent_end`` non-final, this
            # accumulator goes deaf after turn 1 — but ``prompt_and_wait``
            # breaks in the same way at the same moment (its own docstring: no
            # correlation id, not safe for two overlapping turns), so the gate
            # fails together with the client's wait rather than behind it. That
            # coupling is the right one, and it did not exist before.
            #
            # THE ONE THING THE GATE SUBTRACTS, recorded so it is not
            # rediscovered: if a drain ever delivered ``agent_end`` BEFORE a
            # partial ``message_end``, that partial is now dropped and the
            # envelope falls back to ``(no output)`` — the exact pre-fix-①
            # symptom, on a stream that arrived in the wrong order. Measured as
            # reachable only from a synthetic child: the harness emits
            # ``MessageEndEvent`` and THEN ``AgentEndEvent``
            # (``harness/core.py:4589-4591``, same at ``:4548``), so on any real
            # child the terminator is last and the partial is always already
            # folded. It is a property of the kernel's emission order, not of
            # this gate, which is why the gate is still the right shape.
            if not state.saw_agent_end:
                with contextlib.suppress(Exception):
                    reduce_event(state, payload)
            # THE TAP STOPS AT A TERMINAL ROW. ``_eager_abort`` makes
            # ``row.state`` terminal BEFORE the ``finally`` drains, and
            # ``runtime._publish`` stamps that state onto every snapshot — so a
            # tap left live through teardown publishes a TERMINAL snapshot per
            # drained line, and ``SubagentProgressBridge`` pops its ``_tools``
            # entry on each one, making the next line ``first=True`` again.
            # Measured on one cancelled delegation: 2 325
            # ``subagent_start``/``subagent_end`` pairs for a single child, on
            # the channels a dashboard subscribes to. ``PrintChannel`` holds the
            # same invariant by cancelling its pumps next to its own
            # ``_eager_abort`` (``print_channel.py:1102-1109``); this channel
            # cannot, because the accumulator above still has to read.
            # ``runtime._run``'s ``finally`` publishes the ONE terminal snapshot
            # itself, so this channel's contract is: non-terminal snapshots only.
            #
            # NOT gated on the terminator too, on purpose: on the timeout path
            # ``saw_agent_end`` flips mid-drain and progress must keep flowing
            # there — the drain IS the payload.
            #
            # Two consequences, both deliberate and both narrow. Progress goes
            # QUIET through a cancelled or aborted teardown; the runtime's
            # terminal publish lands immediately after, so the user sees "gone",
            # not "frozen". And the tap still fires through an OK-path teardown,
            # where ``row.state`` is still ``"running"`` — on a ``state`` the
            # accumulator above no longer updates. Harmless: those snapshots are
            # non-terminal and the bridge dedups by row text.
            if on_stream is not None and row.state not in _TERMINAL_STATES:
                with contextlib.suppress(Exception):
                    on_stream(state)

        # ``None`` until there is a client to subscribe to, so the ``finally``
        # below needs no knowledge of which paths got that far.
        unsubscribe: Callable[[], None] | None = None

        row.prompt = write_prompt_file(plan.resolved.name, profile.body)
        try:
            if row.stopped:
                # ``stop`` landed while we were being set up. The registry row
                # is published as soon as the spawn is accepted, so this window
                # is real, and it is the difference between "stopped" and
                # "stopped, and also ran for its full timeout".
                row.state = "stopped"
                return _envelope(outcome="aborted")

            client = RpcClient(
                RpcClientOptions(
                    argv=self._argv_builder(
                        child_profile,
                        prompt_path=str(row.prompt.path),
                        task=plan.task,
                        permission_mode=plan.permission_mode,
                        child_cwd=plan.cwd,
                        parent_cwd=plan.parent_cwd,
                        parent_model=(
                            self._parent_model() if self._parent_model else None
                        ),
                    ),
                    cwd=plan.cwd,
                    env_base=self._env_builder(profile),
                    # The parent-death signal. It lives in this band, which is
                    # why product-core exposes a seam instead of a default.
                    preexec_fn=pdeathsig,
                    reader_limit=STREAM_LIMIT_BYTES,
                    # The same 64 KiB window the print channel keeps. The
                    # client's own 10 MiB default would be handed to the
                    # envelope's UNCAPPED details field.
                    stderr_max_bytes=STDERR_RING_BYTES,
                    stdout_line_max_bytes=MAX_LINE_BYTES,
                    # The whole delegation budget, because a cold real child
                    # costs ~25 s to reach its read loop and the 30 s default
                    # would make that a coin flip.
                    send_timeout_ms=timeout_ms,
                    # pi DISCARDS a `success: false` on prompt/steer/abort, and
                    # aelix ports that faithfully. Nobody is watching this
                    # child, so a discarded refusal — the busy preflight's exact
                    # shape — would become a full-budget wait for a terminator
                    # that is never coming, reported as a timeout.
                    raise_on_command_error=True,
                )
            )

            # SUBSCRIBED BEFORE THE PROCESS EXISTS, and that ordering is the
            # whole point. ``start`` spawns the child, creates the stdout pump
            # and only THEN waits out its 100 ms grace, so everything the child
            # emitted inside that window used to be broadcast to an empty
            # listener list. Measured against a ``/bin/sh`` child that speaks
            # and then exits 9 inside the grace: subscribed here, the envelope
            # reports the child's own ``rate limited`` and its 33 tokens;
            # subscribed after ``start``, the same run reports the stderr
            # traceback and zero usage. That is what made "the child said
            # nothing" and "we were not listening yet" the same observation,
            # with no counter anywhere that could tell them apart.
            #
            # It buys what the pump managed to READ, not everything the child
            # wrote: ``start``'s failure path cancels the pump without draining
            # it, so bytes still in the pipe are still lost.
            #
            # The summary shift is a promotion, not a substitution. The stderr
            # rung of ``_select_summary`` is gated on ``not ok`` and this path is
            # always ``ok=False``, so a traceback can only ever be displaced by
            # ``state.error_message`` — the child's own diagnosis of WHY the
            # delegation failed — and the traceback still travels in ``details``
            # and in ``error``.
            unsubscribe = client.on_event(_listener)

            try:
                await client.start()
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 — a failed spawn is a RESULT
                # Covers exec failures AND the child that died inside the
                # startup grace, which is where a bad ``--tools`` name lands:
                # measured, an unknown tool name is an uncaught traceback in
                # rpc mode, so the child dies before the transport exists and
                # its stderr is the only diagnosis there will ever be.
                captured["stderr"] = _stderr_of(exc, client)
                # The counter never needed a subscriber: ``JsonlLineReader``
                # increments ``self._dropped`` inside ``feed()``
                # (``_jsonl.py:126-146``) before ``_emit`` is reached, so a
                # dropped line is counted whether or not anyone is listening,
                # and identically before this commit. What was missing is that
                # this early return never READ it — ``captured["dropped"]`` was
                # assigned only in ``_drive``'s ``finally``, which a failed
                # ``start`` never reaches, so the envelope reported
                # ``dropped_lines: 0`` for a child whose very first line blew the
                # framing budget.
                #
                # THEREFORE INDEPENDENT of the subscribe ordering above: if that
                # is ever revisited, this line does NOT go with it.
                captured["dropped"] = client.dropped_lines
                captured["exit_code"] = getattr(exc, "returncode", None)
                row.state = "error"
                return _envelope(outcome="error", error=str(exc))

            row.proc = client.process
            row.state = "running"
            if row.stopped:
                # The other half of the race: a stop that landed while the
                # spawn was in flight saw ``proc is None`` and had nothing to
                # signal. Now it does.
                await abort_child(row, grace=self._grace)

            return await self._drive(
                client,
                plan,
                row,
                timeout_ms=timeout_ms,
                started=started,
                captured=captured,
                envelope=_envelope,
                unsubscribe=unsubscribe,
            )
        finally:
            # A BACKSTOP, not the real unsubscribe: ``_drive`` still does that
            # at the one moment that matters, after ``_shutdown`` has drained
            # the child's last bytes. This covers the early returns above and
            # the cancellation path without anyone having to enumerate them
            # correctly, and it is safe to double-call because ``on_event``'s
            # closure suppresses the ``ValueError``.
            if unsubscribe is not None:
                unsubscribe()
            # Synchronous, idempotent, never raises — so it always completes
            # once entered, including on the cancellation path.
            remove_prompt_dir(row.prompt)
            row.prompt = None

    async def _drive(
        self,
        client: RpcClient,
        plan: SpawnPlan,
        row: RunningChild,
        *,
        timeout_ms: int,
        started: float,
        captured: dict[str, Any],
        envelope: Callable[..., SubagentResult],
        unsubscribe: Callable[[], None],
    ) -> SubagentResult:
        """Prompt, wait for the terminator, shut the child down.

        The reducer is subscribed by the caller, before the child exists. What
        this method owns is the OTHER end of that subscription, and where it
        goes is a correctness question rather than a cleanup detail — see the
        ``finally``.
        """

        try:
            result = await self._run_turn(
                client, plan, row, timeout_ms=timeout_ms, started=started
            )
        except asyncio.CancelledError:
            self._eager_abort(row)
            raise
        except BaseException as exc:  # noqa: BLE001
            # LAST LINE OF DEFENCE. ``run`` documents "never raises except on
            # cancellation" and the runtime depends on it: its call site has no
            # ``except``, so anything escaping here reaches the harness's tool
            # dispatcher on the single-task door. Whatever any future reducer,
            # waiter or teardown does, a delegation ends as an ENVELOPE and the
            # process tree ends dead.
            self._eager_abort(row)
            result = _TurnOutcome(outcome="error", error=str(exc))
        finally:
            # THE REDUCER STAYS LIVE THROUGH TEARDOWN, and the ordering here is
            # the entire fix. ``_shutdown`` closes stdin, waits out the child's
            # exit and then spends ``POST_EXIT_DRAIN_SECONDS`` letting the pumps
            # reach EOF — and every line that arrives in that window is parsed
            # and correlated by ``RpcClient._handle_stdout_line`` regardless of
            # who is listening. Unsubscribing first did not stop the parsing; it
            # only guaranteed the result was thrown away at the ``for listener
            # in …`` line, with up to 7 s of the child's last words going into
            # a void.
            #
            # THE INTERVENTION PATH IS THE KILL PATH, so this is precisely the
            # material an orchestrator interrupted the child to collect.
            # Measured on a timed-out delegation whose partial answer was still
            # in flight when the parent gave up: ``summary='(no output)'`` with
            # zero tokens and zero turns, against the child's real partial
            # answer with its full usage block. §(j) of ADR-0197 already
            # promises that partial, and ``_select_summary``'s docstring already
            # explains how it is meant to arrive; this is what makes it true.
            #
            # A REAL ``run_rpc_mode`` CHILD WRITES NOTHING AFTER ITS STDIN EOF —
            # measured, it drops its own event pipe before it aborts the turn —
            # so what this recovers is not a "closing report" the child composes
            # on its way out. It is everything the child had ALREADY written
            # that the parent had not yet reduced: bytes in the pipe when the
            # deadline fired, and a turn that finished inside the window.
            #
            # WHAT STAYS LIVE HERE IS THE ACCUMULATOR, NOT THE PROGRESS TAP.
            # One subscription can only mean that because ``_listener`` gates
            # its two halves separately — do not re-couple them by moving this
            # ``unsubscribe()`` again. See its comments for the two measurements
            # that forced the split: a post-terminator line failing a finished
            # turn, and a terminal-state snapshot per drained line storming the
            # progress bus.
            try:
                with contextlib.suppress(Exception):
                    captured["exit_code"] = await self._shutdown(client, row)
                captured["stderr"] = client.get_stderr()
                captured["dropped"] = client.dropped_lines
            finally:
                # NESTED, because ``contextlib.suppress(Exception)`` above does
                # not catch ``CancelledError`` — it is a ``BaseException`` — and
                # a teardown cancelled mid-``_shutdown`` would otherwise skip
                # this on its way out.
                unsubscribe()

        if row.stopped:
            # We killed it, which is a different fact from it crashing.
            row.state = "stopped"
            return envelope(outcome="aborted")
        if result.outcome == "ok":
            # A PROPOSAL, not a verdict: ``build_result`` tightens it when the
            # exit code or the stream disagrees. Never trust the exit status
            # alone — a child whose model errored exits 0 with empty stderr
            # while the stream carries ``stop_reason: "error"``.
            row.state = "done" if captured["exit_code"] == 0 else "error"
        else:
            row.state = "error"
        return envelope(outcome=result.outcome, error=result.error)

    async def _run_turn(
        self,
        client: RpcClient,
        plan: SpawnPlan,
        row: RunningChild,
        *,
        timeout_ms: int,
        started: float,
    ) -> _TurnOutcome:
        """Hand the child its task and wait for the turn to terminate.

        The budget is the WHOLE delegation's, so what is left for the turn is
        what the spawn did not already spend. A real child's boot is the
        dominant term (~25 s cold, ~18.6 s of it import time), which is why the
        remaining budget is recomputed rather than passed through.
        """

        remaining_ms = timeout_ms - int((time.monotonic() - started) * 1000)
        if remaining_ms <= 0:
            return _TurnOutcome(outcome="timeout")
        try:
            # ``collect=False`` because the return value is discarded, and that
            # is the reason the flag exists rather than a use of it. This
            # channel's own reducer is already subscribed and holds the only
            # state anyone reads, so the client's second copy was an unbounded
            # list nobody looked at — measured at 25.6 MB of peak parent memory
            # for one turn of 5 000 ``message_update`` deltas of 4 KB, against
            # 0.36 MB without it, and there is no ceiling on either factor.
            await client.prompt_and_wait(
                plan.task, timeout_ms=remaining_ms, collect=False
            )
        except TimeoutError:
            return _TurnOutcome(outcome="timeout")
        except RpcServerExited as exc:
            # The child died mid-turn. Its exit status is read during teardown;
            # ``build_result`` turns a non-zero one into a failure on its own,
            # so this only has to supply the diagnosis.
            return _TurnOutcome(outcome="error", error=str(exc))
        except RpcClientError as exc:
            # The SERVER refused the command — a busy turn, an unsupported
            # verb. Correlated by request id, so unlike the terminator this
            # answer provably belongs to us.
            return _TurnOutcome(outcome="error", error=str(exc))
        return _TurnOutcome(outcome="ok")

    async def _shutdown(self, client: RpcClient, row: RunningChild) -> int | None:
        """The child MUST die: one child per task. Ask first, then insist.

        Closing stdin is a real shutdown request rather than a signal —
        ``run_rpc_mode`` races stdin EOF against its shutdown event — so a
        cooperative child unwinds its own extensions and session file. Only a
        child that ignores it reaches the reaper.
        """

        proc = client.process
        if proc is None:
            # Never started, or already torn down.
            return client.returncode

        await client.close_stdin()
        exit_code = await client.wait_for_exit(STDIN_EOF_EXIT_SECONDS)
        if exit_code is None:
            exit_code = await self._reap(proc, row)
        # The pumps still hold whatever the child wrote on its way out. Bounded,
        # because a pump blocked on a pipe some survivor still holds must not
        # turn a finished delegation into a hang.
        await client.drain(POST_EXIT_DRAIN_SECONDS)
        await client.stop()
        return exit_code

    async def _reap(self, proc: Any, row: RunningChild) -> int:
        """Run the reaper DETACHED and await it shielded.

        Detached so a cancellation of THIS task cannot cancel the kill;
        registry-owned so ``stop_all`` still joins it. The descendant walk is
        taken here, while the child is provably still alive, which is what
        makes a ``PPid`` index usable at all.
        """

        task = row.reaper_task
        if task is None or task.done():
            task = asyncio.ensure_future(
                reap(
                    proc,
                    grace=self._grace,
                    eager_kill=row.eager_kill,
                    descendants=descendant_pids(proc.pid),
                )
            )
            row.reaper_task = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._eager_abort(row)
            raise

    @staticmethod
    def _eager_abort(row: RunningChild) -> None:
        """Second interrupt: stop waiting, start killing."""

        row.eager_kill = True
        row.state = "stopped"
        proc = row.proc
        if proc is not None:
            kill_tree(proc, descendant_pids(proc.pid))


def _stderr_of(exc: BaseException, client: RpcClient) -> str:
    """The child's stderr, preferring what the exception already carried."""

    carried = getattr(exc, "stderr", None)
    if isinstance(carried, str) and carried:
        return carried
    with contextlib.suppress(Exception):
        return client.get_stderr()
    return ""


__all__ = [
    "STDIN_EOF_EXIT_SECONDS",
    "RpcChannel",
    "build_rpc_child_argv",
    "build_rpc_child_env",
]
