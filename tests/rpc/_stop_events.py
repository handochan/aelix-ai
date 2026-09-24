"""#330 — what ``RpcClient.stop()`` did, in the order it did it.

``stop()`` is soft → grace → hard (ADR-0238). The tests of it used to read that
ladder off the wall clock: ``elapsed >= grace`` for "the grace was waited out",
``elapsed < grace`` for "the grace was skipped" or "the child took the hint".
On windows-latest ``time.monotonic`` steps 15.625 ms and asyncio may fire a
timer one of those ticks early, so a floor with no margin is unsound there
(#313 measured 0.187 s for a 0.2 s timer), and a ceiling equal to the grace is a
bet against the runner's load.

The ladder has its own record, and this reads it: every ``_await_exit`` wait
``stop()`` armed — the bound the REAL ``asyncio.wait_for`` inside it was handed,
read through :func:`tests.event_waits.record_armed_waits`, not the argument
``stop()`` passed in — with how that wait ended, and every ``hard_kill`` it sent,
in completion order. "Waited the grace out, then escalated" is
``[("await_exit", grace, "timed out"), ("hard_kill",), ("await_exit", 5.0,
"exit landed")]`` on any runner at any load; a skipped grace has no grace entry
at all; a cooperative child is one grace entry whose exit landed and no kill —
or, when its exit and the grace's timer land in the same loop turn, one whose
bound fired with the exit already set (``"timed out, exit"``), which the cases
read through :func:`settled`.

WHY THE ARMED BOUND AND NOT THE ARGUMENT (#330 review). A wrapper that only
saw ``_await_exit(timeout)`` recorded what ``stop()`` ASKED for, so a grace that
ended early inside ``_await_exit`` (measured: ``timeout=timeout / 2`` there)
left the record identical and every case green — the one thing the old
``elapsed >= grace`` floor did catch. "Timed out" here is asyncio's own timer
firing at the bound it was armed with, which is exactly "the grace was waited
out", without reading a 15.625 ms-step clock to say so.

Instance attributes over the bound methods, so the class and every other client
in the run stay untouched; both wrappers delegate, so the child still ends the
way production ends it. The module-global ``asyncio`` swap is undone by the
case's ``monkeypatch``.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_coding_agent.rpc import rpc_client as rpc_client_module
from aelix_coding_agent.rpc.rpc_client import RpcClient

from tests.event_waits import armed_since, record_armed_waits

StopEvent = tuple[Any, ...]
"""``("await_exit", armed_bound, outcome)`` or ``("hard_kill",)``.

``outcome`` is ``"exit landed"`` (the wait returned and the child's exit is
set), ``"timed out"`` (the armed bound fired and the exit had not landed), or
a spelled-out anomaly such as ``"returned, no exit"`` — which is a grace that
ended early for some reason other than the child. ``armed_bound`` is the one
bound the real ``wait_for`` was armed with, or the list of every wait armed
inside that call when it was not exactly one.

``"timed out, exit"`` is the one anomaly that is NOT a defect by itself: the
armed bound fired and ``_exited`` was set in the same loop turn — the exit
watcher's poll landing in the turn the timer fires, which a child that answers
the soft signal right at the end of the grace (the real rpc child on a slow
windows leg) or a whole-process pause across the deadline can produce.
``_await_exit`` then answers ``True`` and ``stop()`` does not escalate, which is
the right answer. Read such a ladder through :func:`settled`.
"""

EXITED = "exited"
""":func:`settled`'s outcome for an ``_await_exit`` that answered ``True``."""


def settled(events: list[StopEvent]) -> list[StopEvent]:
    """*events* with both ``True`` answers of ``_await_exit`` read as :data:`EXITED`.

    ``"exit landed"`` and ``"timed out, exit"`` are the two outcomes of the race
    in :data:`StopEvent`: in both the child had exited when ``_await_exit``
    returned, ``_await_exit`` said so, and ``stop()``'s next step is the same.
    What a verdict about the ladder needs is the bound each wait was ARMED with
    (a grace cut short still reads short here), whether ``_await_exit`` answered
    ``True``, and where ``hard_kill`` fell — so a cooperative child is
    ``[("await_exit", grace, EXITED)]`` whichever side of the race its exit
    fell on, and an escalation is still a ``("hard_kill",)`` entry.

    NOT for the pipe-holder stall, whose defect IS a grace that fired over a
    child already dead: that case reads the raw record, where the race is out
    of reach (its child dies at the soft signal, a whole grace before the
    timer).
    """

    answered_true = ("exit landed", "timed out, exit")
    return [
        (e[0], e[1], EXITED) if e[0] == "await_exit" and e[2] in answered_true else e
        for e in events
    ]


def record_stop(client: RpcClient, monkeypatch: pytest.MonkeyPatch) -> list[StopEvent]:
    """Spy on the waits and kills of *client*'s next ``stop()``. Call after ``start()``."""

    armed = record_armed_waits(monkeypatch, rpc_client_module)
    events: list[StopEvent] = []
    real_await_exit = client._await_exit

    async def _await_exit(timeout: float) -> bool:
        mark = len(armed)
        landed = await real_await_exit(timeout)
        inside = armed_since(armed, mark)
        if len(inside) == 1:
            bound: Any = inside[0].timeout
            how = inside[0].outcome
        else:
            bound = [(w.timeout, w.outcome) for w in inside]
            how = "several waits" if inside else "no wait armed"
        if landed and how == "returned":
            outcome = "exit landed"
        elif not landed and how == "timed out":
            outcome = "timed out"
        else:
            outcome = f"{how}, {'exit' if landed else 'no exit'}"
        events.append(("await_exit", bound, outcome))
        return landed

    client._await_exit = _await_exit  # type: ignore[method-assign]

    tree = client._tree
    assert tree is not None, "record_stop() needs a started client: there is no tree to spy on"
    real_hard_kill = tree.hard_kill

    def _hard_kill() -> None:
        events.append(("hard_kill",))
        real_hard_kill()

    tree.hard_kill = _hard_kill  # type: ignore[method-assign]
    return events


def grace_of(client: RpcClient) -> float:
    """The soft-kill grace ``stop()`` arms, in the seconds ``_await_exit`` takes."""

    return client.SHUTDOWN_SIGTERM_TIMEOUT_MS / 1000.0


REAP_BOUND = 5.0
"""``stop()``'s bound on the final reap after the hard kill (W4 m9)."""
