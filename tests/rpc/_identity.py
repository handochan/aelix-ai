"""#772 — read a tracked pid by identity, not by number.

:func:`tests.process_probe.await_dead_or_zombie` asks whether a pid NUMBER is
held. Once the tracked process is dead the number is free, and a process
created later can hold it and read ``alive``: the shape of #772's failures
(``['gone', 'alive']`` 5 s after ``RpcClient.start`` raised, 5 of 12
windows-latest py3.13 runs on 2026-11-12). Pid reuse is not shown to be their
cause: diagnostic runs 43200282429 and 43209921126 never reproduced them. Real
reuse was forced on Linux only (verify r1, ``ns_last_pid``), and this read
answers :data:`STATE_REUSED` there, which :data:`ENDED` counts (15 of 15 runs,
verify r2).

:class:`Identities` records each pid's state and creation time
(:func:`~aelix_ai.utils._process_identity.process_started`) when the tracked
tree is first read. A pid alive later is another process only when that is
KNOWN: it was dead at the record, or both creation times were read and differ.
Anything unread stays the tracked process, so the read errs toward alive — and
so do its two blind spots: a number reused BEFORE :meth:`Identities.record` is
taken for the tracked process (verify r1 measured the record first in 40 of 40
macOS, 40 of 40 Linux and 88 of 88 windows round-3 runs), and a creation time
unread at the record lets :func:`kill_if_tracked` kill whatever holds the
number. A case that never records its tree is not caught either: its first read
of a live pid raises ``KeyError`` (a failure), and without reuse it passes as it
did before #772, so a missing record cannot make a leak pass (verify r2, R5/S1).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from aelix_ai.utils import _process_identity

from tests.process_probe import STATE_ALIVE, STATE_GONE, STATE_ZOMBIE, probe_state

#: The tracked process is gone and its number is held by another one.
STATE_REUSED = "reused"

#: Every answer that means the tracked process is no longer running.
ENDED = (STATE_GONE, STATE_ZOMBIE, STATE_REUSED)


class Identities:
    """Each tracked pid's state and creation time, as first read."""

    def __init__(self) -> None:
        self.at: dict[int, tuple[str, float | None]] = {}

    def record(self, pids: list[int]) -> None:
        for pid in pids:
            self.at[pid] = (probe_state(pid), _process_identity.process_started(pid))

    def born(self, pid: int) -> float | None:
        return self.at[pid][1]

    def reused(self, pid: int) -> bool:
        """Whether a live ``pid`` is KNOWN to be another process than the one recorded."""

        then, born = self.at[pid]
        if then != STATE_ALIVE:
            return True
        if born is None:
            return False
        now = _process_identity.process_started(pid)
        return now is not None and now != born


async def await_gone(pid: int, ids: Identities, *, timeout: float, interval: float = 0.05) -> str:
    """Poll until the tracked process is gone, a zombie or :data:`STATE_REUSED`, or ``timeout``.

    Returns the last answer, :data:`STATE_ALIVE` included, as
    :func:`~tests.process_probe.await_dead_or_zombie` does.
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        state = probe_state(pid)
        if state != STATE_ALIVE:
            return state
        if ids.reused(pid):
            return STATE_REUSED
        if loop.time() >= deadline:
            return state
        await asyncio.sleep(interval)


def kill_if_tracked(pid: int, ids: Identities, kill: Callable[[int], None]) -> None:
    """``kill(pid)`` unless ``pid`` is alive and known to be another process now."""

    if pid in ids.at and probe_state(pid) == STATE_ALIVE and ids.reused(pid):
        return
    kill(pid)


def same_or_reused(pid: int, ids: Identities) -> str:
    """The verdict a failure message gives: is ``pid`` still the recorded process?"""

    born = ids.born(pid)
    now = _process_identity.process_started(pid)
    if born is None or now is None:
        return f"creation time when recorded {born}, now {now}: cannot tell"
    if now == born:
        return f"creation time {born} unchanged: the same process"
    return f"creation time {born} when recorded, {now} now: the pid was REUSED"
