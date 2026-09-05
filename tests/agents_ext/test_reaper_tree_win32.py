"""The reaper's dispatch between the signal legs and the containment tree (#220).

#202 built ``aelix_ai.utils._process_tree.ProcessTree`` — a Job Object on
Windows, a process group here — and #220 hands it to :func:`reap` and
:func:`kill_tree`. These cases pin WHICH LEG RUNS, per platform and per tree
state. They are the reason the win32 arm cannot silently leak onto POSIX and the
reason a closed tree cannot silently swallow a kill.

NO REAL ``ProcessTree`` AND NO REAL SPAWN MAY APPEAR IN THIS FILE, and that is a
hard constraint rather than a preference. ``reaper.sys is sys``, so
``monkeypatch.setattr(reaper.sys, "platform", "win32")`` is PROCESS-GLOBAL:
under it ``containment_spawn_kwargs()`` yields ``creationflags=`` (which POSIX
``subprocess`` rejects outright) and ``ProcessTree.attach`` reaches
``_KernelApi()`` → ``AttributeError: module 'ctypes' has no attribute 'WinDLL'``
— both measured. The patch is safe here precisely because nothing real is inside
its blast radius: at runtime the reaper touches exactly three members of a tree
(``closed``, ``soft_kill()``, ``hard_kill()``), so :class:`_Tree` below is the
whole surface. And the patch is still REQUIRED, for the two fallback cases whose
arm runs ``reaper._kill_signal()``.

WHAT THE INJECTED PLATFORM DOES *NOT* PROVE. ``descendant_pids`` decides on
``/proc`` existing, not on ``sys.platform``, so on the Linux CI leg the walk is
still REAL under this patch. These cases pin that the tree arm returns BEFORE
consulting it; "``descendant_pids`` is ``[]`` on Windows" is asserted by the
windows leg, not here.

Every asynchronous case is wrapped in ``asyncio.wait_for(..., 5.0)``. The
waiters here are wedged deliberately — they resolve only when the product line
under test fires — so a deleted product line has to fail as a legible timeout
rather than hang the suite.
"""

from __future__ import annotations

import asyncio
import signal
import time
from typing import Any

import pytest
from aelix_agents import reaper
from aelix_agents.reaper import kill_tree, reap

# The POSIX case runs on the windows leg too, where this name does not exist —
# so it is lent, exactly as ``tests/agents_ext/test_reaper_kill_signal_win32.py``
# and ``tests/tools/test_process_tree_win32.py`` do. On POSIX it IS
# ``signal.SIGKILL`` and lending it back is a no-op.
SIGKILL = getattr(signal, "SIGKILL", 9)

CHILD_PID = 4321
DESCENDANT_PID = 99

GRACE = 0.2
"""Small on purpose. Two cases assert a timestamp against ``GRACE / 2``, so the
grace has to be long enough that "waited" and "did not wait" are separable on a
loaded CI runner and short enough that seven cases cost no wall time."""


class _Tree:
    """Recording stand-in for :class:`ProcessTree` — the three members ``reap``
    actually touches, and nothing else.

    ``closed`` is a plain attribute rather than a property because that is how
    the product reads it: ``reaper._usable`` does ``tree.closed``, deliberately
    NOT ``getattr(tree, "closed", False)``, so a stub that forgot to declare it
    would raise here instead of silently taking the POSIX arm.

    ``gate`` is the link back to the waiter: a real escalation is what makes a
    wedged child exit, so :meth:`hard_kill` opens it. Without that link every
    case below would still pass with ``hard_kill`` deleted — it would merely
    take the full timeout to do so.
    """

    def __init__(
        self,
        *,
        soft: bool = True,
        closed: bool = False,
        gate: asyncio.Event | None = None,
        events: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.closed = closed
        self._soft = soft
        self.gate = gate
        # SHARED with the ``os.kill`` spy when a case passes one in. Two
        # separate logs cannot express "the group kill came AFTER the walk",
        # which is the sentence Q1's whole rationale rests on — measured:
        # moving ``tree.hard_kill()`` to before ``kill_tree`` in ``reap`` left
        # every case in this file green (#220 review round 2, MUT-3).
        self.events = events
        self.t0 = time.monotonic()
        self.calls: list[tuple[str, float]] = []

    def _log(self, name: str) -> None:
        self.calls.append((name, time.monotonic() - self.t0))
        if self.events is not None:
            self.events.append((name,))

    def soft_kill(self) -> bool:
        self._log("soft_kill")
        return self._soft

    def hard_kill(self) -> None:
        self._log("hard_kill")
        if self.gate is not None:
            self.gate.set()

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def at(self, name: str) -> float:
        """When ``name`` was called, seconds since this stub was built.

        The stub's own clock, never ``reap``'s elapsed: against a wedged waiter
        the total elapsed is IDENTICAL for the correct code and for a mutant
        that skips the grace, because both end at the same escalation. Only the
        timestamp recorded inside the stub separates them (measured).
        """

        return next(when for got, when in self.calls if got == name)


class _Wedged:
    """A child that has not exited and will not exit on its own.

    NOT the sibling file's ``_Live``, whose ``wait()`` returns ``15``
    immediately: against that stub ``reap``'s non-eager leg resolves on the
    first tick and the escalation is NEVER reached (measured: ``rc 15 elapsed
    0.0 sent [(4321, SIGTERM)]``), so every grace-then-escalate case would pass
    vacuously. This one resolves only when the test's gate is opened — by
    :meth:`_Tree.hard_kill`, or by the ``os.kill`` spy seeing the descendant
    signal — which is what makes a deleted escalation a failure.
    """

    returncode = None
    pid = CHILD_PID

    def __init__(self) -> None:
        self.gate = asyncio.Event()

    def kill(self) -> None:  # pragma: no cover — reached only by a regression
        raise AssertionError("the reaper must never reap behind the loop's back")

    async def wait(self) -> int:
        await self.gate.wait()
        return 1


class _Dead:
    """A child that has already exited, for the synchronous :func:`kill_tree`.

    ``returncode`` is set, so ``_signal_child`` returns at its own guard and the
    only ``os.kill`` today's POSIX body would still make is the DESCENDANT one.
    That is what case 4 asserts the absence of.
    """

    returncode = 0
    pid = CHILD_PID

    async def wait(self) -> int:  # pragma: no cover — never awaited here
        return 0


def _simulate_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the module see Windows: ``sys.platform`` win32, no ``SIGKILL``.

    The deleted attribute is not decoration. The two fallback cases run
    ``_kill_signal()`` for real, and naming ``signal.SIGKILL`` there is the #103
    crash; ``raising=False`` says "ensure it is absent", not "assert it was
    present", so this file also works on the windows leg where it already is.
    """

    monkeypatch.setattr(reaper.sys, "platform", "win32", raising=True)
    monkeypatch.delattr(reaper.signal, "SIGKILL", raising=False)


def _simulate_posix(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    """Make the module see POSIX, on the windows leg too.

    The spec wrote this case as "no platform patch"; that reads correctly from a
    POSIX box and is wrong on ``windows-latest``, where an unpatched case would
    take the win32 arm and assert the opposite of what it is for. Injecting the
    platform is this repo's convention anyway (``tests/conftest.py``), and
    ``SIGKILL`` is lent back for the same reason the sibling file lends it.
    """

    monkeypatch.setattr(reaper.sys, "platform", platform, raising=True)
    monkeypatch.setattr(reaper.signal, "SIGKILL", SIGKILL, raising=False)


def _record_signals(
    monkeypatch: pytest.MonkeyPatch,
    *,
    opens: asyncio.Event | None = None,
    events: list[tuple[Any, ...]] | None = None,
) -> list[tuple[int, int]]:
    """Spy on ``os.kill`` — the pids and signals, in order.

    ``opens`` is set when the DESCENDANT is signalled, i.e. when the escalation
    has provably reached ``kill_tree``'s loop. It is how a case whose tree never
    fires (a closed tree, no tree at all) still lets its wedged child exit.

    ``events`` is the SHARED log a case passes to both this spy and its
    :class:`_Tree`, so signals and tree calls interleave in one sequence and an
    ordering claim becomes assertable (MUT-3).
    """

    sent: list[tuple[int, int]] = []

    def _kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        if events is not None:
            events.append(("kill", pid, sig))
        if opens is not None and pid == DESCENDANT_PID:
            opens.set()

    monkeypatch.setattr(reaper.os, "kill", _kill)
    return sent


async def test_win32_tree_soft_then_grace_then_hard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of #220: a Windows child gets a signal it can CATCH.

    ``os.kill`` on Windows is ``TerminateProcess`` for every value that is not a
    console control event, so today's leg 1 is already the uncatchable kill and
    the grace buys nothing. Through the tree, leg 1 is ``CTRL_BREAK_EVENT``, the
    child's ``SIGBREAK`` handler runs, and the grace is a real grace.
    """

    sent = _record_signals(monkeypatch)
    _simulate_win32(monkeypatch)
    proc = _Wedged()
    tree = _Tree(gate=proc.gate)

    rc = await asyncio.wait_for(reap(proc, grace=GRACE, tree=tree), 5.0)

    assert tree.names == ["soft_kill", "hard_kill"], tree.calls
    # The escalation waited out the grace rather than following the console
    # event immediately.
    #
    # ONE CLOCK RESOLUTION OF SLACK, and it is not padding. Before CPython 3.13
    # ``time.monotonic()`` on Windows is ``GetTickCount64``, whose
    # ``get_clock_info`` resolution is ~15.6 ms, and ``BaseEventLoop._run_once``
    # runs a scheduled handle as soon as ``when < now + _clock_resolution`` —
    # i.e. up to one resolution EARLY. Against a 0.2 s grace a tick-quantised
    # delta of 0.1875 is reachable there, so a bare ``>= GRACE`` is a coin flip
    # on the one leg this file exists for (#220 review round 2, W32-4). The
    # sibling cases assert ``< GRACE / 2`` and are unaffected — that is the
    # discriminating half, and it keeps a full tick of headroom either way.
    slack = asyncio.get_running_loop()._clock_resolution  # noqa: SLF001
    assert tree.at("hard_kill") >= GRACE - slack, (tree.calls, slack)
    # The waiter's value, not a constant: this is what pins the final shielded
    # ``await`` still being there.
    assert rc == 1
    # No pid was signalled on this arm — not the root, not a descendant.
    assert sent == []


async def test_win32_soft_kill_refused_skips_the_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``soft_kill() -> False`` means nothing was delivered, so nothing is owed.

    No console, ``ESRCH``, or a closed job: in each case the child never learned
    it was asked to stop, and waiting a grace for a request that was never made
    only delays the kill. ``RpcClient.stop()`` makes the same decision.
    """

    sent = _record_signals(monkeypatch)
    _simulate_win32(monkeypatch)
    proc = _Wedged()
    tree = _Tree(soft=False, gate=proc.gate)

    rc = await asyncio.wait_for(reap(proc, grace=GRACE, tree=tree), 5.0)

    assert tree.names == ["soft_kill", "hard_kill"], tree.calls
    # The stub's clock, never ``reap``'s elapsed — see :meth:`_Tree.at`.
    assert tree.at("hard_kill") < GRACE / 2, tree.calls
    assert rc == 1
    assert sent == []


async def test_win32_eager_kill_skips_the_soft_leg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q4: ``eager_kill=True`` on the win32 tree arm is HARD ONLY.

    ``abort_child`` always passes ``eager_kill=True``, so this is the common
    ``stop`` / ``stop_all`` path and not an edge. With a zero grace the console
    event is not merely useless — it starts the child's ``dispose()``, which
    ``taskkill`` + ``TerminateJobObject`` then truncate microseconds later — and
    it doubles the latency of the one leg a human is waiting on, because
    ``hard_kill`` blocks the loop on a ``taskkill.exe`` spawn.

    This is the case that goes red if the ``not eager_kill`` clause is deleted
    from leg 1's condition.
    """

    sent = _record_signals(monkeypatch)
    _simulate_win32(monkeypatch)
    proc = _Wedged()
    tree = _Tree(gate=proc.gate)

    rc = await asyncio.wait_for(reap(proc, grace=GRACE, eager_kill=True, tree=tree), 5.0)

    assert tree.names == ["hard_kill"], tree.calls
    # Q4's pin, stated as a count rather than left to the list comparison above:
    # "soft_kill was never called" is the decision, and it must survive someone
    # relaxing the ordering assertion.
    assert tree.names.count("soft_kill") == 0, tree.calls
    assert tree.at("hard_kill") < GRACE / 2, tree.calls
    assert rc == 1
    assert sent == []


def test_win32_kill_tree_uses_the_job_and_signals_no_pids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The job IS the descendant set, so the walk is not consulted at all.

    ``descendants`` is ignored on this arm — on Windows it is empty anyway,
    ``descendant_pids`` having no ``/proc`` to read — and the arm deliberately
    does not fall through to ``_signal_child``: ``TerminateProcess(handle, sig)``
    would race ``TerminateJobObject(job, 1)`` for the exit status the escalation
    tests read.
    """

    sent = _record_signals(monkeypatch)
    _simulate_win32(monkeypatch)
    tree = _Tree()

    kill_tree(_Dead(), [DESCENDANT_PID], tree=tree)

    assert tree.names == ["hard_kill"], tree.calls
    # Including pid 99: deleting the tree arm leaves the descendant loop, which
    # is what this ``[]`` catches.
    assert sent == []


async def test_win32_without_a_tree_is_todays_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tree=None`` is a supported input, and it must be byte-for-byte today.

    The print channel degrades to it when ``ProcessTree.attach`` fails, which is
    exactly the situation in which nothing else will end the child. Same
    sequence as ``test_reaper_kill_signal_win32.py``'s eager case.
    """

    proc = _Wedged()
    sent = _record_signals(monkeypatch, opens=proc.gate)
    _simulate_win32(monkeypatch)

    rc = await asyncio.wait_for(
        reap(proc, grace=GRACE, eager_kill=True, descendants=[DESCENDANT_PID], tree=None),
        5.0,
    )

    assert rc == 1
    assert sent == [
        (CHILD_PID, signal.SIGTERM),
        (DESCENDANT_PID, signal.SIGTERM),
        (CHILD_PID, signal.SIGTERM),
    ]


async def test_win32_closed_tree_falls_back_to_the_signal_legs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLOSED tree is not a usable tree, and the difference is total.

    Both legs of a closed tree early-return — ``soft_kill`` to ``False``,
    ``hard_kill`` to nothing — so dispatching to one signals NOTHING. Usually a
    closed tree is an already-dead tree (closing the last handle of a
    ``kill_on_close=True`` job terminates its members), but the
    ``contained=False, job=None`` fallback closes without killing anything, and
    that is the shape this guards.

    This is the case that goes red if ``reaper._usable`` is weakened to
    ``tree is not None``: the mutant takes the tree arm, sends no signal, and
    the wedged child's gate — opened by the ``os.kill`` spy — is never set.
    """

    proc = _Wedged()
    sent = _record_signals(monkeypatch, opens=proc.gate)
    _simulate_win32(monkeypatch)
    tree = _Tree(closed=True)

    rc = await asyncio.wait_for(
        reap(proc, grace=GRACE, descendants=[DESCENDANT_PID], tree=tree), 5.0
    )

    assert rc == 1
    assert tree.calls == []
    assert sent == [
        (CHILD_PID, signal.SIGTERM),
        (DESCENDANT_PID, signal.SIGTERM),
        (CHILD_PID, signal.SIGTERM),
    ]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
async def test_posix_tree_uses_the_signal_legs_and_then_the_group(
    platform: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSIX keeps both signal legs and adds ONE group kill, after the walk.

    This is the guard against a win32 fix leaking onto POSIX: leg 1 is still
    ``os.kill(pid, SIGTERM)`` and ``soft_kill`` is never called, so the child's
    own ``_signal_cleanup_and_exit`` still runs. The escalation is still the
    descendant walk. Only then does Q1's ``tree.hard_kill()`` —
    ``killpg(pgid, SIGKILL)`` — fire, addressing what the walk could not name:
    ``descendant_pids`` is ``[]`` on macOS, where a non-``setsid`` descendant
    measurably survives the root-only escalation.

    Three deletions each turn this red, and every one of them on an ASSERTION
    rather than on a timeout — this is the one case in the file whose gate is
    opened by BOTH the tree and the ``os.kill`` spy, so neither deletion can
    hang it, and the first drafting of this docstring claimed otherwise (#220
    review round 2, TEST-1): Q1's ``hard_kill`` line (``tree.names`` becomes
    ``[]``), the descendant loop (``(99, SIGKILL)`` disappears from ``sent``),
    and leg 1's ``_signal_child`` (the first tuple disappears).

    A FOURTH is the ORDER, which needs the shared ``events`` log to be visible at
    all: moving ``tree.hard_kill()`` to before ``kill_tree`` in ``reap`` left two
    separate logs identical and the whole file green (MUT-3). "After the walk" is
    the sentence Q1's rationale rests on, so it is asserted rather than narrated.
    """

    _simulate_posix(monkeypatch, platform)
    proc = _Wedged()
    events: list[tuple[Any, ...]] = []
    sent = _record_signals(monkeypatch, opens=proc.gate, events=events)
    tree = _Tree(gate=proc.gate, events=events)

    rc = await asyncio.wait_for(
        reap(proc, grace=GRACE, descendants=[DESCENDANT_PID], tree=tree), 5.0
    )

    assert rc == 1
    assert sent == [
        (CHILD_PID, signal.SIGTERM),
        (DESCENDANT_PID, SIGKILL),
        (CHILD_PID, SIGKILL),
    ]
    # Exactly once, and never the console event: on POSIX the tree is reached
    # ONLY through Q1. If Q1 is ever reverted, this assertion is the thing that
    # must MOVE — it is not a redundant belt on the walk.
    assert tree.names == ["hard_kill"], tree.calls
    # AND LAST. One log, so "the group only ever addresses what the walk could
    # not name" is a measurement rather than a comment.
    assert events == [
        ("kill", CHILD_PID, signal.SIGTERM),
        ("kill", DESCENDANT_PID, SIGKILL),
        ("kill", CHILD_PID, SIGKILL),
        ("hard_kill",),
    ], events
