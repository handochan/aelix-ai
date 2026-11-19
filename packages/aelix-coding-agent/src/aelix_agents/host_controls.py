"""The :class:`~aelix_coding_agent.subagent_contract.SubagentControls` a build binds (#612, ADR-0307).

What an RPC host reaches of a build's delegated children: the rows and frames
of its :class:`~aelix_agents.child_events.ChildEventHub`, and #123's doors —
``runtime.steer``, ``runtime.wrap_up``, ``runtime.stop`` on the runtime that
holds the row (the build's own, or a background run's), and a job row's stop
through the job service, which records who asked. One per build, set by the
agents extension as ``runtime.controls``; product core reads it through the
seam and never imports this module.

Answers are the wire's own words (``Delivery`` as a mapping, ``{"state": …}``);
a request the controls cannot serve raises :class:`ValueError`. A host's request
is a person's action in that host, so every door is asked with ``origin="user"``
(contract r5 §3.2, a proposal).

AN INTERVENTION IS ANSWERED BY ITS STAGE. Nothing here awaits before the door.
A steer's door writes its line before its own first ``await``
(``CommandWire.request``), so a caller that sees a cancel land inside
:meth:`HostControls.steer` knows the line may have been read. A wrap-up's door
first writes ``cancel`` to an open approval request and waits for its
acknowledgement (ADR-0308); a cancel landing there, before the final steer's
write began, comes out of :meth:`HostControls.wrap_up` as
:class:`~aelix_agents.command_wire.InterventionNotSent` (``not_sent`` true), the
wrap-up taken back (#612 fix round 1, V-F1); the approval ``cancel`` it wrote
first may have been read (fix round 2, F4). That is how the control plane
answers a request a session change interrupted (contract r5 §3.4).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from aelix_agents.child_approvals import withdraw_approval
from aelix_agents.child_events import TEXT_CHARS, cap, job_id_of, no_agent
from aelix_agents.runtime import RESUME_HINT, WRAP_UP_RESUME_HINT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aelix_agents.child_events import ChildEventHub
    from aelix_agents.print_channel import RunningChild

CHILD_STOP_ANSWER_BOUND = 10.0
"""How long ``children/stop`` waits for its door before it answers the state it
sees while the stop goes on (contract r5 §3.1, a proposal; ``jobs/service.py``'s
``CLEANUP_BOUND``). Not the steer inbox's 5 + 30 s, which a stop does not use."""

SESSION_CHANGED = "the session changed"
"""The refusal of an intervention on controls a later build retired (contract r5 §3.4)."""


def _wire(delivery: Any) -> dict[str, Any]:
    """A door's ``Delivery`` in the wire's words."""

    return {
        "outcome": delivery.outcome,
        "phase": delivery.phase,
        "verb": delivery.verb,
        "ended": bool(delivery.ended),
        "reason": cap(delivery.reason, TEXT_CHARS),
    }


def _refused(reason: str, *, verb: str) -> dict[str, Any]:
    return {"outcome": "refused", "phase": None, "verb": verb, "ended": False, "reason": reason}


class HostControls:
    """One build's children, as an RPC host reaches them. See the module docstring."""

    def __init__(
        self,
        hub: ChildEventHub,
        *,
        stop_job: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._hub = hub
        self._stop_job = stop_job
        self._closed = False
        self._doors: set[asyncio.Future[None]] = set()

    @property
    def epoch(self) -> str:
        return self._hub.epoch

    @property
    def closed(self) -> bool:
        return self._closed

    # === reads ==================================================================

    def list(self) -> list[dict[str, Any]]:
        return self._hub.rows()

    def events(self, id: str, after_seq: int | None) -> dict[str, Any]:  # noqa: A002 — the wire's name
        return self._hub.events(id, after_seq)

    def subscribe(self, callback: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        return self._hub.subscribe(callback)

    def close(self) -> None:
        """Retired at the rebind: every later intervention refuses; reads and the subscription go on."""

        self._closed = True

    # === the doors ==============================================================

    def _live(self, child_id: str) -> tuple[Any, RunningChild] | None:
        """The live row, ``None`` for one that ended; :class:`ValueError` once closed or for an unknown id."""

        if self._closed:
            raise ValueError(SESSION_CHANGED)
        found = self._hub.live(child_id)
        if found is None and not self._hub.knows(child_id):
            raise ValueError(no_agent(child_id))
        return found

    async def steer(self, id: str, text: str, mode: str) -> dict[str, Any]:  # noqa: A002
        found = self._live(id)
        if found is None:
            return _refused(RESUME_HINT, verb=mode)
        runtime, child = found
        return _wire(await runtime.steer(child.id, text, mode=mode, origin="user"))

    async def wrap_up(self, id: str) -> dict[str, Any]:  # noqa: A002
        found = self._live(id)
        if found is None:
            return _refused(WRAP_UP_RESUME_HINT, verb="wrap_up")
        runtime, child = found
        return _wire(await runtime.wrap_up(child.id, origin="user"))

    async def stop(self, id: str) -> dict[str, Any]:  # noqa: A002
        """Stop the child and answer the state the hub holds when the door returned, or at the bound.

        A foreground row's door is ``runtime.stop`` (it awaits the kill and joins
        the cleanup); a job row's is the job service's stop, asked by the user,
        which returns before the job's stop legs — so it usually answers
        ``running``. The door goes on past the bound, and past a cancel of this
        call; the terminal ``aelix_child`` frame is the end. An approval request
        open on the row is withdrawn before the door is asked (ADR-0308).
        """

        found = self._live(id)
        if found is not None:
            # #612 (ADR-0308; the lead's call after part A): a request the row has
            # open is withdrawn first — or, its answer's write begun, recorded as
            # that decision — before either door runs.
            withdraw_approval(found[1])
            door = asyncio.ensure_future(self._door(*found))
            self._doors.add(door)
            door.add_done_callback(self._door_done)
            await asyncio.wait({door}, timeout=CHILD_STOP_ANSWER_BOUND)
        return {"state": self._hub.state_of(id)}

    async def _door(self, runtime: Any, child: RunningChild) -> None:
        job_id = job_id_of(child)
        if job_id is not None and self._stop_job is not None:
            try:
                await self._stop_job(job_id)
                return
            except Exception:  # noqa: BLE001 — the row's own stop still reaches it
                pass
        await runtime.stop(child.id)

    def _door_done(self, door: asyncio.Future[None]) -> None:
        self._doors.discard(door)
        with contextlib.suppress(BaseException):
            door.exception()


__all__ = ["CHILD_STOP_ANSWER_BOUND", "SESSION_CHANGED", "HostControls"]
