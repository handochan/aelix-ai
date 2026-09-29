"""#351 — a cancellation that lands in the terminator's cleanup turn propagates, at every call site.

Four callers go through ``RpcClient._await_terminator``: ``wait_for_idle``,
``collect_events``, ``prompt_and_wait`` (its own ``agent_end`` wait) and
``_send`` — every command, including the ``prompt`` that ``prompt_and_wait``
sends first. The helper's ``finally`` joined its ``death`` task with
``suppress(BaseException): await death``, and a caller cancellation landing
while the caller was suspended in that join was handed to death and swallowed:
the call returned normally with ``cancelling()`` still 1 (measured on fbead6e0,
every call-site case below: ``RETURNED NORMALLY``; the timeout-leg case raised
its own ``TimeoutError`` instead, third paragraph). The production turn was
``_send``'s terminator for ``prompt`` — the loop turn in which the child's
answer to ``prompt`` resolves.

The cancel is aimed by :mod:`tests.rpc._terminator_seam`, not by a clock: it is
issued from inside death's own cancellation step, while the caller is in the
cleanup. ``landed is True`` makes each case non-vacuous; the listener and
pending-request asserts pin that the cancelled call still cleaned up after
itself.

The TIMEOUT leg has the same cleanup, so the same cancel was lost there too,
but it did not look like a normal return: the helper fell through to its own
``TimeoutError`` with ``cancelling()`` still 1, so a cancelled call was
reported as a timeout — and an rpc delegation's ``_run_turn`` turned that into
a ``timeout`` envelope. ``wait_for_idle-timeout`` pins it: a 50 ms budget and
no ``prompt``, so nothing but the product's own timer ends the wait, and the
seam fires whenever that timer does (measured on fbead6e0: ``TimeoutError``).

Codex's cross-review of febe8ba5 added the rest of the file. Two wrong joins
passed every cancel case — ``await asyncio.sleep(0)``, and ``await death``
re-raising whenever ``cancelling() > 0`` — so two cases pin what the join does
when nobody cancels the caller: it waits for death to FINISH (a death that
needs three more turns, :class:`tests.rpc._terminator_seam.DeathThatTakesTurns`)
and it never mistakes death's cancellation for the caller's (a caller carrying
an earlier, already-handled cancel). The last case pins which leg wins when an
``agent_end`` is read just after the child's death was observed — the margin
the join's turns give a completed turn, kept on purpose (ADR-0201's #351
amendment).
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Coroutine
from typing import Any

import pytest
from aelix_coding_agent.rpc.rpc_client import RpcClient, RpcClientOptions, RpcServerExited

from tests.event_waits import wait_until, within
from tests.rpc._terminator_seam import install, install_slow_death
from tests.rpc.test_rpc_client_lifecycle import _STUB_SERVER


class _StubClient(RpcClient):
    """The lifecycle stub: answers every command, emits ``agent_end`` after ``prompt``."""

    def __init__(self) -> None:
        super().__init__(RpcClientOptions(argv=[sys.executable, "-c", _STUB_SERVER]))


# case id -> (what it is, which of the target's terminators to aim at, whether
# the TEST task must send the ``prompt`` that resolves the wait).
# ``wait_for_idle`` and ``collect_events`` send nothing themselves, so their
# ``agent_end`` comes from a ``prompt`` the test sends once the target is inside
# the terminator.
_CASES: dict[str, tuple[str, int, bool]] = {
    "wait_for_idle": ("wait_for_idle", 1, True),
    "collect_events": ("collect_events", 1, True),
    "prompt_and_wait-agent_end": ("prompt_and_wait's own agent_end wait", 2, False),
    "prompt_and_wait-prompt_answer": (
        "prompt_and_wait's _send, waiting for the prompt answer (the production turn)",
        1,
        False,
    ),
    "send-get_state": ("_send for a plain command (get_state)", 1, False),
    "wait_for_idle-timeout": (
        "wait_for_idle's timeout leg (its 50 ms budget expiring, no agent_end)",
        1,
        False,
    ),
}


def _call(client: RpcClient, case: str) -> Awaitable[Any]:
    if case == "wait_for_idle":
        return client.wait_for_idle(timeout_ms=600_000)
    if case == "wait_for_idle-timeout":
        # No prompt is ever sent, so the stub never emits agent_end: only the
        # product's own timer ends this wait, and the seam fires in its cleanup.
        return client.wait_for_idle(timeout_ms=50)
    if case == "collect_events":
        return client.collect_events(timeout_ms=600_000)
    if case.startswith("prompt_and_wait"):
        return client.prompt_and_wait("go", timeout_ms=600_000)
    return client.get_state()


@pytest.mark.parametrize("case", list(_CASES))
async def test_a_cancel_in_the_terminators_cleanup_turn_propagates(case: str) -> None:
    """The caller's cancellation beats a ``done`` that already resolved.

    Exactly as it already did one loop turn earlier (the wait's waiter
    resolved, the caller not yet resumed), where ``Task.cancel()`` sets
    ``_must_cancel`` and the ``CancelledError`` comes out of the wait. With a
    600 s budget on every wait, a swallowed cancel reads as the call returning
    normally in ~0.1 s — fast and named, never a hang.
    """

    what, fire_on, test_sends_the_prompt = _CASES[case]
    client = _StubClient()
    await client.start()
    try:
        seam = install(client, fire_on=fire_on)
        task = asyncio.ensure_future(_call(client, case))
        seam.target = task  # before the task's first step: it has not run yet
        if test_sends_the_prompt:
            await wait_until(lambda: seam.deaths == 1, what=f"{what} to enter the terminator")
            # The stub answers, then emits agent_end. Bounded: this ``_send``
            # runs its own terminator in the TEST task.
            await within(client.prompt("go"), bound=20.0, what="the test's own prompt")
        lost: str | None = None
        try:
            await within(
                task,
                bound=20.0,
                what=f"{what} after a cancel in the terminator's cleanup turn",
            )
            lost = "returned normally"
        except asyncio.CancelledError:
            pass
        except TimeoutError as exc:
            # The product's own timeout, not ``within``'s bound (that one
            # raises AssertionError). Only the timeout leg can get here.
            lost = f"raised its own TimeoutError ({str(exc)[:40]!r}…)"
        # ``landed`` first: a cancel that never reached the cleanup (a helper
        # that no longer suspends after ``death.cancel()``, say) is a broken
        # seam or a moved window, not a swallow, and must not read as one.
        assert seam.landed is True, (
            f"the seam's cancel never landed inside {what}'s terminator "
            f"(landed={seam.landed}, deaths={seam.deaths}, the call {lost or 'was cancelled'})"
        )
        if lost is not None:
            pytest.fail(
                f"{what} {lost} after a cancel landed in its terminator's cleanup "
                f"turn (landed={seam.landed}, cancelling={task.cancelling()}) — the "
                "cancellation was swallowed (#351)"
            )
        assert client._event_listeners == [], "a cancelled wait left its listener subscribed"
        assert client._pending_requests == {}, "a cancelled command left its request pending"
    finally:
        await client.stop()


# Codex's cross-review of febe8ba5. The cancel cases above pin that the
# cleanup suspends and lets a caller cancel out; two wrong joins pass them all
# ("8 passed" each on Codex's mutant plugins), so the two cases below pin what
# the join must ALSO do when nobody cancels the caller.
#
# case id -> (what it is, whether the TEST task must send the ``prompt`` that
# resolves the wait, the exception the call itself raises or ``None``).
_NO_CANCEL_CASES: dict[str, tuple[str, bool, type[BaseException] | None]] = {
    "wait_for_idle": ("wait_for_idle (the done leg)", True, None),
    "prompt_and_wait": ("prompt_and_wait (two terminators, both on the done leg)", False, None),
    "send-get_state": ("_send for get_state (the done leg)", False, None),
    "wait_for_idle-timeout": ("wait_for_idle's timeout leg", False, TimeoutError),
}


async def _run_to_the_end(awaitable: Awaitable[Any]) -> tuple[str, BaseException | None]:
    """Await *awaitable* in THIS task; report how it ended instead of raising."""

    try:
        await awaitable
    except BaseException as exc:  # noqa: BLE001 — the verdict is the caller's
        return type(exc).__name__, exc
    return "returned", None


@pytest.mark.parametrize("case", list(_NO_CANCEL_CASES))
async def test_the_cleanup_waits_for_death_to_finish_before_the_call_returns(case: str) -> None:
    """C4a: no death task outlives the call that built it.

    :class:`tests.rpc._terminator_seam.DeathThatTakesTurns` makes death need
    three more loop turns once cancelled. The pending-death snapshot is taken
    in the CALLER's own step, the moment the call returns or raises — awaiting
    the task from the test would hand death those turns for free. A join that
    only suspends one turn (Codex's ``await asyncio.sleep(0)`` mutant, which
    passed every cancel case above) returns with death still cancelling and
    fails here; ``asyncio.wait([death])`` and the old ``await death`` both wait
    it out.
    """

    what, test_sends_the_prompt, raises = _NO_CANCEL_CASES[case]
    client = _StubClient()
    await client.start()
    try:
        seam = install_slow_death(client, turns=3)
        pending_at_return: list[asyncio.Task[Any]] = []

        async def _call_then_snapshot() -> tuple[str, BaseException | None]:
            outcome = await _run_to_the_end(_call(client, case))
            pending_at_return.extend(d for d in seam.deaths if not d.done())
            return outcome

        task = asyncio.ensure_future(_call_then_snapshot())
        seam.target = task
        if test_sends_the_prompt:
            await wait_until(lambda: len(seam.deaths) == 1, what=f"{what} to enter the terminator")
            await within(client.prompt("go"), bound=20.0, what="the test's own prompt")
        name, exc = await within(task, bound=20.0, what=what)

        assert seam.deaths, f"{what} built no death task through the seam — it tests nothing"
        if raises is None:
            assert exc is None, f"{what} raised {name} ({exc!r}) instead of returning"
        else:
            assert isinstance(exc, raises), f"{what} ended {name} ({exc!r}), not {raises.__name__}"
        assert not pending_at_return, (
            f"{what} came back with {len(pending_at_return)} of its {len(seam.deaths)} "
            "death task(s) still cancelling — the cleanup must wait for death to finish, "
            "not just yield a turn after cancelling it (Codex C4a on febe8ba5)"
        )
    finally:
        await client.stop()


@pytest.mark.parametrize("case", list(_NO_CANCEL_CASES))
async def test_an_earlier_handled_cancellation_does_not_resurface_in_a_later_call(
    case: str,
) -> None:
    """C4b: the join tells death's cancellation from the caller's by WHERE it lands, not by a counter.

    The caller is cancelled once, handles the ``CancelledError`` and carries on
    without ``uncancel()`` — so ``cancelling()`` is 1 on entry to a call nobody
    is cancelling. That call must end exactly as it would otherwise: return,
    or raise its own ``TimeoutError`` on the timeout leg. A join that re-raises
    death's ``CancelledError`` whenever ``current_task().cancelling() > 0``
    (Codex's conditional mutant, which passed every cancel case above) turns
    each of these into a spurious ``CancelledError``; ``asyncio.wait([death])``
    never sees death's cancellation as the caller's at all.
    """

    what, test_sends_the_prompt, raises = _NO_CANCEL_CASES[case]
    client = _StubClient()
    await client.start()
    try:
        cancelling_on_entry: list[int] = []

        async def _after_a_handled_cancel() -> tuple[str, BaseException | None]:
            me = asyncio.current_task()
            assert me is not None
            me.cancel()
            # Handled, and deliberately NOT uncancel()ed.
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(0)
            cancelling_on_entry.append(me.cancelling())
            return await _run_to_the_end(_call(client, case))

        task = asyncio.ensure_future(_after_a_handled_cancel())
        if test_sends_the_prompt:
            await wait_until(lambda: len(client._event_listeners) == 1, what=f"{what} to subscribe")
            await within(client.prompt("go"), bound=20.0, what="the test's own prompt")
        name, exc = await within(task, bound=20.0, what=what)

        assert cancelling_on_entry == [1], (
            f"precondition: the caller entered {what} with cancelling()={cancelling_on_entry}"
        )
        if raises is None:
            assert exc is None, (
                f"{what} raised {name} ({exc!r}) for a caller carrying an earlier, "
                "already-handled cancellation — nothing cancelled this call (Codex C4b)"
            )
        else:
            assert isinstance(exc, raises), (
                f"{what} ended {name} ({exc!r}), not {raises.__name__}, for a caller "
                "carrying an earlier, already-handled cancellation (Codex C4b)"
            )
        assert client._event_listeners == [], "the call left its listener subscribed"
        assert client._pending_requests == {}, "the call left its request pending"
    finally:
        await client.stop()


class _ExitWithEntry(asyncio.Event):
    """An ``_exited`` stand-in that says when the terminator built its death task."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()

    def wait(self) -> Coroutine[Any, Any, bool]:  # type: ignore[override]
        self.entered.set()
        return super().wait()


# offset -> what ``wait_for_idle`` answers when the child's ``agent_end`` is
# handled ``offset`` loop callbacks after ``_exited`` is set (CPython 3.11.15
# and 3.12.13 alike, CI's two; the counts are asyncio's, so a newer CPython
# that schedules ``asyncio.wait``'s callbacks differently moves them too).
_LATE_AGENT_END: dict[int, str] = {
    0: "returns",
    1: "returns",
    # fbead6e0 answered RpcServerExited from here: its ``await death`` did not
    # suspend on the death leg; ``asyncio.wait([death])`` holds it two callbacks.
    2: "returns",
    3: "returns",
    4: "RpcServerExited",
    5: "RpcServerExited",
}


@pytest.mark.parametrize("offset", list(_LATE_AGENT_END))
async def test_an_agent_end_handled_before_the_helper_returns_beats_the_observed_death(
    offset: int,
) -> None:
    """Codex's C2 on febe8ba5, decided (a): the join's turns on the death leg are a margin for ``done``.

    The helper decides AFTER its join, and ``done`` wins if it resolved by
    then — "a completed turn is a completed turn". The join now holds the caller
    two loop callbacks on the death leg, where fbead6e0's ``await death`` did not suspend at all,
    so an ``agent_end`` handled up to three callbacks after ``_exited`` is set
    now returns where fbead6e0 raised ``RpcServerExited`` from two on (Codex's
    ``probe_late_event.py``: ``parent offset=2 outcome=RpcServerExited`` /
    ``head offset=2 outcome=RETURN``, same at 3).

    Kept on purpose. An ``agent_end`` the parent reads after it saw the exit
    was written BEFORE the child exited — a dead process writes nothing — so
    returning is the truer answer, and it is the rule the helper already
    applied in the same tick. It also changes nothing measured in production:
    a real child that writes ``agent_end`` and exits at once never had the line
    unread when ``_exited`` was set, 0 of 300 runs each for fbead6e0's helper
    and this one, with and without a 1.6 MB flood ahead of it and with the
    exit poll at the product's 50 ms or every loop turn.

    Pinned at both edges so a change to the margin is a decision, not a side
    effect. No child: a bare client whose ``_exited`` and stdout line are
    driven by hand, so only callbacks — no clock — decide the order.
    """

    expected = _LATE_AGENT_END[offset]
    client = RpcClient(RpcClientOptions())  # never started: no child, pump or watcher
    exit_event = _ExitWithEntry()
    client._exited = exit_event
    task = asyncio.ensure_future(client.wait_for_idle(timeout_ms=600_000))
    await within(exit_event.entered.wait(), bound=20.0, what="wait_for_idle's terminator")

    loop = asyncio.get_running_loop()
    exit_event.set()

    def _deliver(remaining: int) -> None:
        if remaining:
            loop.call_soon(_deliver, remaining - 1)
        else:
            client._handle_stdout_line('{"type": "agent_end", "messages": []}')

    loop.call_soon(_deliver, offset)
    try:
        await within(task, bound=20.0, what="wait_for_idle after the child's death")
        outcome = "returns"
    except RpcServerExited:
        outcome = "RpcServerExited"
    assert outcome == expected, (
        f"an agent_end handled {offset} callback(s) after the child's death was observed: "
        f"wait_for_idle {outcome}, the pinned answer is {expected} — the death leg's "
        "margin for a completed turn moved; if that is intended, say so in ADR-0201's "
        "#351 amendment and the helper's docstring and re-pin (Codex C2 on febe8ba5)"
    )
    assert client._event_listeners == [], "wait_for_idle left its listener subscribed"
