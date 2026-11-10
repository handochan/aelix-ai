"""Signal what a walk named only while it is still what was walked (#611 S2a, ADR-0291).

A walk names processes by number, and a number is handed on once its process
has been reaped. The delegation stop legs (``aelix_agents.reaper``: Esc, Esc
Esc, a signal to ``-p``, ``stop``, ``stop_all``, the escalation after a grace,
the success drain) used to SIGKILL the walk's numbers and the groups they led
as they were read, so a number that went to another process between the walk
and the kill took that SIGKILL (Codex #4 on the #611 S2 contract), and so did
the group of a walked leader that died first (Codex N5).

:class:`HeldWalk` holds a walk's entries (:class:`~aelix_ai.utils._process_walk.WalkEntry`,
each with the start time read with its parent) and the groups the walked
processes led, read with it. Before every signal:

* **a pid** — on Linux, through a pidfd opened for it right before its first
  signal and kept only if the pid still had the walk's start time
  (:func:`~aelix_ai.utils._process_identity.acquire_pidfd`); a pidfd reaches
  that process or nothing. Where no pidfd could be opened (macOS, Windows, an
  old kernel, ``EMFILE``) its start time is read again right before the signal
  and must equal the walk's. A start time the walk could not read means no
  signal.
* **a group** — only while its leader still verifies (pidfd live, or the start
  time again), and BEFORE that leader's own kill, so the leader's death cannot
  free the number first. A group whose leader is dead or unverified gets
  nothing on these legs.

Order: groups deepest first, then pids deepest first, each group before its
leader's own pid. Deepest first because a deeper leader can depend on a
shallower one (a pipe whose writer is its ancestor): killing the ancestor's
group first can end that leader before its own group is read, and its check then
withholds the group that still holds an orphaned member (Codex #1 on #758's first
round; ``test_identity_legs_611.py::test_a_leader_that_dies_with_its_ancestors_group_still_has_its_group_signalled``).
The root is the caller's (it is our own child and is signalled through its
``Process``).

What stays (ADR-0291's 2026-11-10 note): a process forked between the walk and
the kill is missed (R1); on macOS the start-time check and the ``kill`` are
two calls, and on Linux so are a leader's check and its ``killpg`` (R2); a
walked parent whose number was reused between its read and its children's
makes a stranger's children look walked (R3).
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import weakref
from collections.abc import Iterable

from aelix_ai.utils import _process_identity
from aelix_ai.utils._process_walk import WalkEntry


def _close_all(fds: dict[int, int]) -> None:
    for fd in list(fds.values()):
        with contextlib.suppress(OSError):
            os.close(fd)
    fds.clear()


class HeldWalk:
    """A walk's entries and the groups they led, with what verifies each one.

    Built right after the walk, before any signal, with the start times the walk
    read. A Linux pidfd is acquired for an entry when the leg first needs it —
    right before that entry's first signal — and kept only if the pid then still
    has the walk's start time; from then on it names that process or nothing.
    Acquiring there rather than all at once after the walk keeps the window in
    which a walked process can fork unseen (R1) as short as the base's: measured
    with M5's fork loop in Linux docker, acquiring every pidfd up front left 2–7
    forks per stop where the base left 1–2. What that costs: a number handed on
    between the walk and the acquisition to a process that started in the same
    clock tick as the walked one (10 ms on Linux) would pass — a reuse that
    fast needs a pid space turned over within one tick. :meth:`close` releases
    the pidfds; a finalizer does too, for one dropped unclosed.
    """

    def __init__(self, entries: Iterable[WalkEntry] = (), groups: Iterable[int] = ()) -> None:
        self.entries: tuple[WalkEntry, ...] = tuple(entries)
        walked = {entry.pid for entry in self.entries}
        #: Only a walked pid's group: the groups belong to the walk that read them.
        self.groups: tuple[int, ...] = tuple(dict.fromkeys(g for g in groups if g in walked))
        self._fds: dict[int, int] = {}
        self._strangers: set[int] = set()
        self._acquired: set[int] = set()
        self._finalizer = weakref.finalize(self, _close_all, self._fds)

    def _acquire(self, entry: WalkEntry) -> None:
        if entry.pid in self._acquired:
            return
        self._acquired.add(entry.pid)
        answer, fd = _process_identity.acquire_pidfd(entry.pid, entry.stamp)
        if answer == "held" and fd is not None:
            self._fds[entry.pid] = fd
        elif answer == "stranger":
            self._strangers.add(entry.pid)

    @property
    def pids(self) -> list[int]:
        return [entry.pid for entry in self.entries]

    def verifies(self, entry: WalkEntry) -> bool:
        """Whether ``entry``'s number still names the process the walk read."""

        self._acquire(entry)
        if entry.pid in self._strangers:
            return False
        fd = self._fds.get(entry.pid)
        if fd is not None:
            return _process_identity.pidfd_live(fd)
        if entry.stamp is None:
            return False
        return _process_identity.process_started(entry.pid) == entry.stamp

    def signal_pid(self, entry: WalkEntry, sig: int) -> None:
        """``sig`` to ``entry``'s process, through its pidfd or after a stamp check. Never raises."""

        self._acquire(entry)
        if entry.pid in self._strangers:
            return
        fd = self._fds.get(entry.pid)
        if fd is not None:
            _process_identity.pidfd_signal(fd, sig)
            return
        if not self.verifies(entry):
            return
        with contextlib.suppress(OSError):
            os.kill(entry.pid, sig)

    def kill(self, sig: int, *, groups: bool = True) -> None:
        """Each verified leader's group (SIGKILL), then each pid, both deepest first. Never raises.

        ``groups`` is False on win32, where there are none, and for a caller
        whose policy is no groups (the success drain).
        """

        if groups and sys.platform != "win32":
            leaders = set(self.groups)
            # Deepest first, as the walk is ordered (the module docstring).
            for entry in self.entries:
                if entry.pid in leaders and self.verifies(entry):
                    with contextlib.suppress(OSError):
                        os.killpg(entry.pid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
        for entry in self.entries:
            self.signal_pid(entry, sig)

    def close(self) -> None:
        """Release the pidfds. Idempotent."""

        self._finalizer()


__all__ = ["HeldWalk"]
