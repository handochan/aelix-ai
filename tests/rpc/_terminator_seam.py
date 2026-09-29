"""#351 — land a cancellation in ``RpcClient._await_terminator``'s cleanup turn, with no clock.

``_await_terminator`` races the thing the caller waits for (``done``) against the
child's death: it builds one ``death`` task per call from ``self._exited.wait()``,
waits for whichever comes first, and cancels and joins ``death`` in its
``finally``. While the caller is suspended in that join, a ``Task.cancel()`` of
the caller is handed to whatever the caller is suspended on — ``death``, already
cancelling — and not to the caller itself. The helper used to join with
``suppress(BaseException): await death``, so the caller's cancellation came
back as death's ``CancelledError`` inside the ``suppress`` and was lost: the
helper saw ``done.done()`` and returned normally, with the caller's
``cancelling()`` still raised (#351). One loop turn earlier the same cancel
propagated, which is why only a seam can aim at this turn.

:class:`CancelInTheCleanupTurn` is that seam. It stands in for ``_exited``, and
its ``wait()`` is a PLAIN function returning a coroutine: ``ensure_future``
evaluates its argument in the CALLER'S step, so ``asyncio.current_task()`` there
is the task that entered the terminator. For its ``target`` only, it counts the
death tasks built (``deaths``), and on the ``fire_on``-th it returns a wrapper
that, when death is cancelled, cancels the target FROM INSIDE DEATH'S OWN
CANCELLATION STEP. At that moment the target is suspended in the helper's
cleanup — exactly the turn the old join swallowed. No sleep, no timer and no
load decides where the cancel lands.

``landed`` is what ``Task.cancel()`` answered: ``True`` means the target was not
done yet. Asserting it keeps a case from passing vacuously — if a refactor ever
stops building a death task per call through ``self._exited.wait()`` (one shared
exit future, say), the seam never fires and ``landed`` stays ``None``, which
fails loudly instead of passing. It also pins that the cleanup still SUSPENDS
at least once after ``death.cancel()``: a helper with no suspension there returns
before death's cancellation step runs, so a call that ends with the helper is
done by then and ``landed`` reads ``False`` (measured on the design prototype: 4
of its 5 call-site cases). It does NOT pin that the suspension is a wait for
death — ``await asyncio.sleep(0)`` in place of the join passes every case,
because death's cancellation step runs in that one turn (that mutant: ``6 passed``
on the cancel cases; dropping the join outright: 5 of 6 red on
``landed=False``). :class:`DeathThatTakesTurns` below pins that part.

Install it AFTER ``start()``: ``start()`` replaces ``_exited`` with a fresh
event. ``_watch_for_exit`` sets whatever ``self._exited`` is at exit time, so
the child-death leg still works through the seam, and every other task's call
(``stop()``'s ``_await_exit``, a helper task's own ``_send``) gets the plain
``Event.wait()``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from aelix_coding_agent.rpc.rpc_client import RpcClient


class CancelInTheCleanupTurn(asyncio.Event):
    """An ``_exited`` stand-in that cancels ``target`` inside its terminator's cleanup."""

    def __init__(self, *, fire_on: int = 1) -> None:
        super().__init__()
        self.target: asyncio.Task[Any] | None = None
        self.fire_on = fire_on
        self.deaths = 0  # death tasks the terminator built for ``target`` so far
        self.landed: bool | None = None  # ``target.cancel()``'s answer when it fired

    def wait(self) -> Coroutine[Any, Any, bool]:  # type: ignore[override]
        target = self.target
        inner = super().wait()
        if target is None or asyncio.current_task() is not target:
            return inner
        self.deaths += 1
        if self.deaths != self.fire_on:
            return inner

        async def _death() -> bool:
            try:
                return await inner
            except asyncio.CancelledError:
                # The terminator's ``death.cancel()`` is being delivered; the
                # caller is suspended in the join that follows it.
                self.landed = target.cancel()
                raise

        return _death()


def install(client: RpcClient, *, fire_on: int) -> CancelInTheCleanupTurn:
    """Swap the seam in for ``client._exited``. Call it after ``start()``."""

    seam = CancelInTheCleanupTurn(fire_on=fire_on)
    if client._exited.is_set():
        seam.set()
    client._exited = seam
    return seam


class DeathThatTakesTurns(asyncio.Event):
    """An ``_exited`` stand-in whose death task, once cancelled, needs more turns to finish.

    Codex's cross-review of febe8ba5 (C4a): the cancel seam above only pins that
    the cleanup SUSPENDS after ``death.cancel()`` — ``await asyncio.sleep(0)`` in
    place of the join passed every case, because the real ``Event.wait()`` death
    finishes cancelling in that one turn. This stand-in makes death take
    ``turns`` more loop turns after its cancellation is delivered (it catches the
    ``CancelledError``, yields ``turns`` times, then re-raises it), so a cleanup
    that returns before death has FINISHED shows as a death task still pending
    when the call returns — the contract the join exists for: no death task
    outlives the call that built it.

    ``deaths`` holds every death task built for ``target`` (recorded in death's
    own first step, so it is the task ``ensure_future`` made). Install it AFTER
    ``start()``, like :class:`CancelInTheCleanupTurn`.
    """

    def __init__(self, *, turns: int = 3) -> None:
        super().__init__()
        self.target: asyncio.Task[Any] | None = None
        self.turns = turns
        self.deaths: list[asyncio.Task[Any]] = []

    def wait(self) -> Coroutine[Any, Any, bool]:  # type: ignore[override]
        target = self.target
        inner = super().wait()
        if target is None or asyncio.current_task() is not target:
            return inner

        async def _death() -> bool:
            me = asyncio.current_task()
            assert me is not None
            self.deaths.append(me)
            try:
                return await inner
            except asyncio.CancelledError:
                for _ in range(self.turns):
                    await asyncio.sleep(0)
                raise

        return _death()


def install_slow_death(client: RpcClient, *, turns: int = 3) -> DeathThatTakesTurns:
    """Swap :class:`DeathThatTakesTurns` in for ``client._exited``. Call it after ``start()``."""

    seam = DeathThatTakesTurns(turns=turns)
    if client._exited.is_set():
        seam.set()
    client._exited = seam
    return seam
