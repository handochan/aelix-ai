"""#849 (ADR-0307 D3, K4): a kept seal that a save/restore hold puts back is lifted too.

An interrupted swap or quit whose settle outlives its join (an ``abort`` handler that
swallows the settle's cancel) keeps its seal until the settle ends; ``_unseal_when_settled``
then lifts it, but only while the gate is still that seal, so a hold set meanwhile is kept
(``tests/runtime/test_swap_settles_first_800.py::test_a_hold_set_while_the_settle_lives_is_kept_when_it_ends``).
``LateRouteHold.apply`` (``/agents use``, a late-route settle) saves the gate, holds the
turns, awaits, and restores what it saved. When the settle ends inside that await, the
callback saw the hold and did nothing, and ``apply`` restored the seal: on ``0436193c``
the harness then refused every prompt with "the session is being replaced" until the
next swap (#800 verify round 3 F1; the issue's probe S4).

Now a seal whose settle has ended is spent: putting it back puts back the gate it
replaced. Each test here fails on ``0436193c`` unless it says it is a guard.
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness import core as harness_core
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    TurnSeal,
)
from aelix_agent_core.runtime import agent_session_runtime as asr
from aelix_agent_core.runtime.agent_session_runtime import create_agent_session_runtime
from aelix_agent_core.session import LocalFileSystem
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, Model
from aelix_coding_agent.cli.runtime_bootstrap import LateRouteHold

from tests.agents_ext.test_child_session_records import _parent
from tests.agents_ext.test_stopped_child_reports_604 import _message_text
from tests.runtime.test_swap_settles_first_800 import _finish, _swallowing_host


async def _kept_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, AgentHarness, dict[str, Any], list[asyncio.Future[Any]]]:
    """A harness left installed by a ``new_session`` cancelled while the settle's first
    ``abort`` hook parks: the seal is kept, the settle alive until ``state["release"]``."""

    host, harness, _model, state = await _swallowing_host(tmp_path, monkeypatch)
    first = asyncio.ensure_future(harness.prompt("first"))
    await asyncio.wait_for(state["live"][0].wait(), 10)
    op = asyncio.ensure_future(host.new_session())
    await asyncio.wait_for(state["first_abort"].wait(), 10)
    op.cancel()
    await asyncio.gather(op, return_exceptions=True)
    await asyncio.wait_for(first, 10)
    assert host.harness is harness and isinstance(harness.turns_held, TurnSeal)
    return host, harness, state, [first, op]


async def _settle_ends(state: dict[str, Any]) -> None:
    state["release"].set()
    await asyncio.wait_for(asyncio.gather(*list(asr._SETTLES), return_exceptions=True), 10)
    await asyncio.sleep(0.02)


async def _runs(harness: AgentHarness, state: dict[str, Any]) -> None:
    """The next prompt reaches the model, is not aborted by the old settle, and answers."""

    turn = asyncio.ensure_future(harness.prompt("next"))
    try:
        await asyncio.wait_for(state["live"][1].wait(), 5)
        done, _ = await asyncio.wait({turn}, timeout=0.3)
        assert turn not in done, "the old settle aborted the next prompt"
        state["finish"].set()
        await asyncio.wait_for(turn, 10)
        assert _message_text(harness.state.messages[-1]) == "completed"
    finally:
        await _finish(turn)


async def test_a_late_route_hold_spanning_the_settles_end_does_not_put_the_seal_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's probe S4: the real ``LateRouteHold.apply``, unbound, whose ``_put_on``
    ends the settle and waits. On ``0436193c``: ``gate_after`` is the seal and the next
    prompt is refused "the session is being replaced"."""

    host, harness, state, tasks = await _kept_seal(tmp_path, monkeypatch)
    try:

        async def put_on(_harness: Any) -> None:
            await _settle_ends(state)

        await LateRouteHold.apply(types.SimpleNamespace(reason="probe", _put_on=put_on), harness)
        await asyncio.sleep(0.05)
        assert len(asr._SETTLES) == 0
        assert harness.turns_held is None
        await _runs(harness, state)
    finally:
        state["finish"].set()
        state["release"].set()
        await _finish(*tasks)
        await host.dispose()


async def test_a_seal_put_back_after_its_settle_ended_puts_back_the_gate_it_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any save/restore of the gate, not only ``apply``'s: a gate saved while the seal was
    kept and restored after the settle ended. The seal had replaced no gate, so none comes
    back and the next prompt runs. On ``0436193c`` the seal comes back."""

    host, harness, state, tasks = await _kept_seal(tmp_path, monkeypatch)
    try:
        saved = harness.turns_held
        assert isinstance(saved, TurnSeal)
        harness.hold_turns("a hold of its own")
        await _settle_ends(state)
        assert harness.turns_held == "a hold of its own"
        harness.hold_turns(saved)
        assert harness.turns_held is None
        await _runs(harness, state)
    finally:
        state["finish"].set()
        state["release"].set()
        await _finish(*tasks)
        await host.dispose()


async def test_a_seal_put_back_while_its_settle_lives_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard (passes on ``0436193c``): a save/restore that ends while the settle is still
    alive puts the live seal back, which refuses the next prompt with its reason, and is
    lifted when the settle ends (D3's kept seal is unchanged)."""

    host, harness, state, tasks = await _kept_seal(tmp_path, monkeypatch)
    try:
        saved = harness.turns_held
        harness.hold_turns("a hold of its own")
        harness.hold_turns(saved)
        assert harness.turns_held is saved
        with pytest.raises(AgentHarnessError) as refused:
            await harness.prompt("refused")
        assert refused.value.code == "invalid_state"
        assert asr.REPLACING_MESSAGE in str(refused.value)
        await _settle_ends(state)
        assert harness.turns_held is None
        await _runs(harness, state)
    finally:
        state["finish"].set()
        state["release"].set()
        await _finish(*tasks)
        await host.dispose()


async def test_a_spent_seal_puts_back_the_hold_it_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seal replaced a hold set while the turn ran ("held by the launch"): restored after
    its settle ended, the spent seal puts that hold back, not ``None``. On ``0436193c`` the
    seal comes back."""

    host, harness, _model, state = await _swallowing_host(tmp_path, monkeypatch)
    first = asyncio.ensure_future(harness.prompt("first"))
    op: asyncio.Future[Any] | None = None
    try:
        await asyncio.wait_for(state["live"][0].wait(), 10)
        harness.hold_turns("held by the launch")
        op = asyncio.ensure_future(host.new_session())
        await asyncio.wait_for(state["first_abort"].wait(), 10)
        op.cancel()
        await asyncio.gather(op, return_exceptions=True)
        await asyncio.wait_for(first, 10)
        saved = harness.turns_held
        assert isinstance(saved, TurnSeal)
        harness.hold_turns("a hold of its own")
        await _settle_ends(state)
        harness.hold_turns(saved)
        assert harness.turns_held == "held by the launch"
    finally:
        state["finish"].set()
        state["release"].set()
        await _finish(first, *([op] if op is not None else []))
        harness.hold_turns(None)
        await host.dispose()


def test_a_spent_seal_over_a_spent_seal_puts_back_the_gate_under_both() -> None:
    """``_unspent`` follows every spent seal down to the gate under them; a seal that is
    not spent stops it. Two swaps interrupted on a turn that ignores their cancels stack
    two seals (verify F9; ``test_two_interrupted_swaps_stack_their_seals_and_a_hold_across_both_puts_back_neither``)."""

    from aelix_agent_core.harness.core import _unspent

    inner, outer = TurnSeal("inner"), TurnSeal("outer")
    inner.spend("the gate under both")
    outer.spend(inner)
    assert _unspent(outer) == "the gate under both"
    live = TurnSeal("live")
    spent_over_live = TurnSeal("spent")
    spent_over_live.spend(live)
    assert _unspent(spent_over_live) is live
    assert _unspent(None) is None and _unspent("plain") == "plain"


def test_the_retired_gates_are_forgotten_once_no_gate_is_up() -> None:
    """Verify round 2, N3. A hold that ends under another gate is retired, so that a
    later restore of its gate puts back what it had saved; once no gate is up nothing
    can put it back (a hold or a seal restores only while its own gate is up) and the
    map is empty again, where in fix round 1 it kept one entry for every such hold
    for the life of the harness. It is kept while a gate is still up, and a hold
    retired after it was emptied is followed as before."""

    harness = AgentHarness(AgentHarnessOptions(model=Model(id="fixture", provider="fixture")))
    for under_reason in (None, "a gate under the hold"):
        harness.hold_turns(under_reason)
        under = harness.turns_held
        harness.hold_turns("a hold")
        hold = harness.turns_held
        assert hold is not None
        harness.hold_turns("a gate put up after it")
        later = harness.turns_held
        harness.release_turns(hold, under)  # the hold ends under the later gate: retired
        assert harness.turns_held is later
        assert list(harness._retired_gates or {}) == [id(hold)]
        harness.hold_turns(hold)  # whoever saved the hold's gate restores it
        assert harness.turns_held is under, "the ended hold's gate came back"
        assert bool(harness._retired_gates) is (under is not None), "kept only while a gate is up"
        harness.hold_turns(None)
        assert harness.turns_held is None and not harness._retired_gates


def _hold(harness: AgentHarness, reason: str, entered: asyncio.Event, release: asyncio.Event) -> Any:
    """The real ``LateRouteHold.apply``, unbound, whose ``_put_on`` waits for ``release``."""

    async def put_on(_harness: Any) -> None:
        entered.set()
        await release.wait()

    return LateRouteHold.apply(types.SimpleNamespace(reason=reason, _put_on=put_on), harness)


async def test_a_spent_seal_does_not_put_back_a_hold_that_has_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex C4. Hold A is up when a swap seals; the swap is interrupted (its settle kept
    alive); hold B saves the seal; A ends while B runs; the settle ends; B ends. B's
    hold stays up while it runs, and after both have ended no gate is left and the next
    prompt runs. In round 1 A's end took B's gate down to ``None`` and B's end put
    A's expired hold back (the spent seal stood for it): every prompt was refused."""

    host, harness, _model, state = await _swallowing_host(tmp_path, monkeypatch)
    a_in, a_release, b_in, b_release = (asyncio.Event() for _ in range(4))
    first = asyncio.ensure_future(harness.prompt("first"))
    tasks: list[asyncio.Future[Any]] = [first]
    try:
        await asyncio.wait_for(state["live"][0].wait(), 10)
        tasks.append(asyncio.ensure_future(_hold(harness, "A", a_in, a_release)))
        await asyncio.wait_for(a_in.wait(), 10)
        op = asyncio.ensure_future(host.new_session())
        tasks.append(op)
        await asyncio.wait_for(state["first_abort"].wait(), 10)
        assert isinstance(harness.turns_held, TurnSeal)
        op.cancel()
        await asyncio.gather(op, return_exceptions=True)
        await asyncio.wait_for(first, 10)
        tasks.append(asyncio.ensure_future(_hold(harness, "B", b_in, b_release)))
        await asyncio.wait_for(b_in.wait(), 10)
        gate_b = harness.turns_held
        a_release.set()
        await asyncio.wait_for(tasks[1], 10)
        assert harness.turns_held is gate_b, "A's end took down B's hold"
        await _settle_ends(state)
        b_release.set()
        await asyncio.wait_for(tasks[3], 10)
        assert harness.turns_held is None
        await _runs(harness, state)
    finally:
        for event in (a_release, b_release, state["finish"], state["release"]):
            event.set()
        await _finish(*tasks)
        await host.dispose()


async def test_a_hold_that_ends_under_a_live_seal_leaves_the_seal_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex C5 (also on ``0436193c``). A hold up before an interrupted swap ends while the
    swap's settle still lives: the seal stays up and refuses a prompt with its reason, and
    when the settle ends the gate under the seal is the one the hold saved, ``None``: the
    next prompt runs and the old settle does not re-abort it. In round 1 and on
    ``0436193c`` the hold's end put ``None`` up over the live seal, the next prompt reached
    the model, and the settle's re-abort ended it."""

    host, harness, _model, state = await _swallowing_host(tmp_path, monkeypatch)
    hold_in, hold_release = asyncio.Event(), asyncio.Event()
    first = asyncio.ensure_future(harness.prompt("first"))
    tasks: list[asyncio.Future[Any]] = [first]
    try:
        await asyncio.wait_for(state["live"][0].wait(), 10)
        tasks.append(asyncio.ensure_future(_hold(harness, "A", hold_in, hold_release)))
        await asyncio.wait_for(hold_in.wait(), 10)
        op = asyncio.ensure_future(host.new_session())
        tasks.append(op)
        await asyncio.wait_for(state["first_abort"].wait(), 10)
        seal = harness.turns_held
        assert isinstance(seal, TurnSeal)
        op.cancel()
        await asyncio.gather(op, return_exceptions=True)
        await asyncio.wait_for(first, 10)
        assert len(asr._SETTLES) == 1, "the settle ended; nothing was measured"
        hold_release.set()
        await asyncio.wait_for(tasks[1], 10)
        assert harness.turns_held is seal, "the hold's end took the live seal down"
        with pytest.raises(AgentHarnessError) as refused:
            await harness.prompt("refused")
        assert asr.REPLACING_MESSAGE in str(refused.value)
        await _settle_ends(state)
        assert harness.turns_held is None
        await _runs(harness, state)
    finally:
        for event in (hold_release, state["finish"], state["release"]):
            event.set()
        await _finish(*tasks)
        await host.dispose()


@pytest.mark.parametrize("outer_kept", [False, True], ids=["outer-lifted", "outer-kept"])
async def test_two_interrupted_swaps_stack_their_seals_and_a_hold_across_both_puts_back_neither(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outer_kept: bool
) -> None:
    """Verify F9 (the verifier's stacked case) and Codex's two-swap control. On a turn whose
    model ignores cancels, a swap is interrupted while its first ``abort`` hook parks (seal
    A kept), then a second swap seals over A (seal B) and is interrupted in an awaited
    ``session_shutdown`` handler past its bound. ``outer-lifted``: B's settle is stopped
    at once, so the gate goes back to A. ``outer-kept``: B's first ``abort`` hook parks
    too, so B stays up. A hold saves that gate and, inside, both settles end; restored
    after, it puts back neither seal (B stands for A, A for ``None``), and once the turn
    ends the next prompt answers. Passes in round 1; on ``0436193c`` the hold put a
    seal back and the next prompt was refused. Kills a ``_unspent`` that follows one
    spent seal only (``outer-kept``)."""

    monkeypatch.setattr(harness_core, "_REABORT_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(asr, "SWAP_SETTLE_SECONDS", 0.3)
    session, repo, _path = await _parent(tmp_path)
    started, model_release = asyncio.Event(), asyncio.Event()
    parked_aborts = 2 if outer_kept else 1
    hooks_in = [asyncio.Event() for _ in range(2)]
    hooks_release = asyncio.Event()
    calls = {"model": 0, "aborts": 0}

    async def stubborn(*_args: Any) -> Any:
        calls["model"] += 1
        if calls["model"] == 1:
            started.set()
            while not model_release.is_set():
                try:
                    await model_release.wait()
                except asyncio.CancelledError:
                    continue
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=f"answer {calls['model']}")], stop_reason="end_turn"
            )
        )

    model_id = Model(id="mock", provider="mock")
    harness = AgentHarness(AgentHarnessOptions(model=model_id, session=session, stream_fn=stubborn))

    async def abort_hook(*_args: Any) -> None:
        calls["aborts"] += 1
        index = calls["aborts"] - 1
        if index < parked_aborts:
            hooks_in[index].set()
            while not hooks_release.is_set():
                try:
                    await hooks_release.wait()
                except asyncio.CancelledError:
                    continue

    harness.hooks.on("abort", abort_hook)
    shutdown = asyncio.Event()

    async def park_shutdown(_event: Any, _ctx: Any) -> None:
        if not shutdown.is_set():
            shutdown.set()
            await asyncio.Event().wait()

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=model_id, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    turn = asyncio.ensure_future(harness.prompt("first"))
    tasks: list[asyncio.Future[Any]] = [turn]
    try:
        await asyncio.wait_for(started.wait(), 10)
        op1 = asyncio.ensure_future(host.new_session())
        tasks.append(op1)
        await asyncio.wait_for(hooks_in[0].wait(), 10)
        op1.cancel()
        await asyncio.gather(op1, return_exceptions=True)
        seal_a = harness.turns_held
        assert isinstance(seal_a, TurnSeal)
        harness.hooks.on("session_shutdown", park_shutdown)
        op2 = asyncio.ensure_future(host.new_session())
        tasks.append(op2)
        await asyncio.wait_for(shutdown.wait(), 10)
        seal_b = harness.turns_held
        assert isinstance(seal_b, TurnSeal) and seal_b is not seal_a, "no second seal"
        op2.cancel()
        await asyncio.gather(op2, return_exceptions=True)
        assert harness.turns_held is (seal_b if outer_kept else seal_a)

        async def both_settles_end(_harness: Any) -> None:
            hooks_release.set()
            await asyncio.wait_for(asyncio.gather(*list(asr._SETTLES), return_exceptions=True), 10)
            await asyncio.sleep(0.02)

        await LateRouteHold.apply(
            types.SimpleNamespace(reason="across both", _put_on=both_settles_end), harness
        )
        assert harness.turns_held is None, "the hold put a seal back"
        assert seal_a.spent and seal_b.spent and len(asr._SETTLES) == 0
        model_release.set()
        await asyncio.wait_for(turn, 10)
        await asyncio.wait_for(harness.prompt("second"), 10)
        assert _message_text(harness.state.messages[-1]) == "answer 2"
    finally:
        model_release.set()
        hooks_release.set()
        await _finish(*tasks)
        await asyncio.wait_for(asyncio.gather(*list(asr._SETTLES), return_exceptions=True), 10)
        await host.dispose()
