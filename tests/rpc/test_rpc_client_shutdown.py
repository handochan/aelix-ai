"""Pi parity: RpcClient shutdown — soft signal → 1 s grace → hard kill.

Verifies the constants on the class, the stderr capture, and the escalation
behaviour. Two stubs: one that SURVIVES the soft signal so the escalation runs,
one that answers it and exits so the cooperative path runs.

WHY THE STUBS INSTALL A HANDLER AND NOT ``SIG_IGN`` (#202, #207). Both cases
here used to be skipped on win32 because ``Popen.terminate()`` was
``TerminateProcess`` — uncatchable, so ``SIG_IGN`` bought the child nothing and
no grace ever elapsed (measured on windows-latest: 0.062 s against a 0.300 s
grace). :meth:`RpcClient.stop` now sends ``CTRL_BREAK_EVENT`` there, which IS
catchable, and CPython delivers it as ``SIGBREAK``. A real handler is what makes
the pass mean something on either platform: ``elapsed >= grace`` alone would
also pass if the soft signal were never delivered at all and the grace simply
ran out, so the stub writes a breadcrumb from inside the handler and the tests
assert BOTH. Delivered, survived, escalated — and the third one is asserted by
the ``hard_kill`` entry in the ladder's record, not inferred from the clock. With the escalation deleted
the grace is still paid and the breadcrumb is still written, and ``stop()``'s
trailing ``transport.close()`` then kills the root on its own (CPython
``asyncio/base_subprocess.py`` calls ``Popen.kill()`` there), so both of the
older assertions hold with the subject of the test removed (review MUT-1).

NONE OF THE LADDER IS READ OFF THE CLOCK ANY MORE (#330). "The grace was
waited out" was ``elapsed >= grace`` with no margin — the #313 shape: on
windows-latest ``time.monotonic`` steps 15.625 ms and asyncio may fire the
grace's timer a tick early, so a correct run can read 0.297 s for 0.3 s. "The
grace was skipped" and "the child took the hint" were ``elapsed < grace``, a bet
against the runner's load. Each is now the ladder's own record
(``tests/rpc/_stop_events.py``): which bound each ``_await_exit`` actually armed
its ``wait_for`` with, whether that bound fired or the child's exit landed
inside it, and where the ``hard_kill`` fell.

THE OBSERVABLE WINDOWS FACT, stated as what is checkable. A child with no
Python-level ``SIGBREAK`` disposition is terminated by the console event itself
— the CRT's console handler returns FALSE for ``SIG_DFL`` and the OS default
handler exits the process — so a breadcrumb written from INSIDE the handler is
evidence that a handler ran, not merely that an event was sent. Which layer
registers that console handler is deliberately not claimed here: it is the
UCRT's, installed as a side effect of ``signal()``, and nothing in this repo
cites a CPython ``SetConsoleCtrlHandler`` call site (review win-leg/F9).
"""

from __future__ import annotations

import sys
import textwrap

import pytest
from aelix_coding_agent.rpc.rpc_client import RpcClient, RpcClientOptions

from tests.event_waits import wait_until
from tests.rpc._stop_events import EXITED, REAP_BOUND, grace_of, record_stop, settled


def test_rpc_client_default_constants_match_pi() -> None:
    """Pi parity invariants (rpc-client.ts:79, :107, :262, :332)."""

    assert RpcClient.DEFAULT_SEND_TIMEOUT_MS == 30_000
    assert RpcClient.DEFAULT_WAIT_FOR_IDLE_MS == 60_000
    assert RpcClient.STARTUP_GRACE_MS == 100
    assert RpcClient.SHUTDOWN_SIGTERM_TIMEOUT_MS == 1_000


# Stub server: answers the soft signal with a breadcrumb and KEEPS RUNNING, so
# the hard kill is what ends it.
#
# THREE breadcrumbs, and the "stub ready" one is the whole fix for a 40% flake.
# The stub is not armed until ``signal.signal`` has run, and the 100 ms startup
# grace is not a readiness signal — measured over 10 runs, the child had not
# reached that line by the time ``stop()`` fired in 2 of them, so it died on the
# FIRST SIGTERM and the escalation the test claims to exercise never ran. The
# failing runs returned from ``stop()`` in ~110 ms; the passing ones took
# ~415 ms. Waiting for ``stub ready`` is what makes the test about the
# escalation instead of about the scheduler.
#
# ``SIGBREAK`` is installed wherever it exists because that is what the Windows
# soft kill arrives as; ``getattr`` because the name is absent on POSIX. Both
# are installed rather than branching on the platform: ``SIGTERM`` is defined on
# win32 too, it is merely undeliverable there, so installing both keeps the stub
# platform-free and lets whichever one arrives write the breadcrumb.
#
# The 0.05 s sleep, not 0.5 s: on Windows a console control event is delivered
# on a separate thread and the Python handler runs when the MAIN thread next
# reaches a bytecode boundary, so the sleep chunk is an upper bound on the
# breadcrumb's latency and it has to fit inside the 0.300 s grace below.
_SOFT_KILL_BREADCRUMB = "signal seen"
_SOFT_KILL_SURVIVOR_STUB = textwrap.dedent(
    """
    import signal
    import sys
    import time

    sys.stderr.write("stub starting\\n")
    sys.stderr.flush()

    def seen(*_):
        sys.stderr.write("signal seen\\n")
        sys.stderr.flush()

    for name in ("SIGTERM", "SIGBREAK"):
        s = getattr(signal, name, None)
        if s is not None:
            signal.signal(s, seen)

    sys.stderr.write("stub ready\\n")
    sys.stderr.flush()

    while True:
        time.sleep(0.05)
    """
)

# The same stub, except the handler takes the hint. ``sys.exit(3)`` from inside
# a handler raises ``SystemExit`` in the main thread, so the exit status is the
# assertion subject: 3 can only come from the handler having run.
_COOPERATIVE_STUB = textwrap.dedent(
    """
    import signal
    import sys
    import time

    def bye(*_):
        sys.stderr.write("signal seen\\n")
        sys.stderr.flush()
        sys.exit(3)

    for name in ("SIGTERM", "SIGBREAK"):
        s = getattr(signal, name, None)
        if s is not None:
            signal.signal(s, bye)

    sys.stderr.write("stub ready\\n")
    sys.stderr.flush()

    while True:
        time.sleep(0.05)
    """
)


async def _await_breadcrumb(client: RpcClient, needle: str) -> None:
    """Block until ``needle`` shows up on the child's stderr.

    An anti-hang bound (10 s, as before: 200 polls of 50 ms); a miss names the
    breadcrumb, the bound, the wall clock spent and the stderr it did see.
    """

    await wait_until(
        lambda: needle in client.get_stderr(),
        timeout=10.0,
        what=f"the stub to write {needle!r} on stderr",
        detail=lambda: f"stderr so far: {client.get_stderr()!r}",
    )


class _SoftKillSurvivorClient(RpcClient):
    """Reduce the soft-kill grace to keep the regression fast."""

    SHUTDOWN_SIGTERM_TIMEOUT_MS = 300

    def __init__(self) -> None:
        super().__init__(RpcClientOptions())

    def _build_argv(self) -> list[str]:
        return [sys.executable, "-c", _SOFT_KILL_SURVIVOR_STUB]


async def test_stop_escalates_to_the_hard_kill_when_the_soft_one_is_survived(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that answers the soft signal and lives is killed after the grace.

    Runs on every platform now (#202 closed #207 (1)): the soft stage is SIGTERM
    on POSIX and ``CTRL_BREAK_EVENT`` on win32, and both are catchable, so the
    same stub proves the same three things on both — delivered, survived, and
    ENDED BY ``hard_kill``, which is counted rather than inferred.
    """

    client = _SoftKillSurvivorClient()
    await client.start()
    # The stub is only armed once ``signal.signal`` has run. Signalling before
    # that measures the scheduler, not the escalation.
    await _await_breadcrumb(client, "stub ready")

    proc = client.process
    assert proc is not None
    events = record_stop(client, monkeypatch)

    await client.stop()

    # THE SUBJECT OF THIS TEST IS THE ESCALATION, and the record is what
    # observes it. Measured (review MUT-1): with ``tree.hard_kill()`` deleted
    # from ``stop()`` the grace is still paid in full and the breadcrumb is still
    # written, because the trailing ``transport.close()`` reaches
    # ``Popen.kill()`` and ends the root anyway — so only the kill's own entry
    # tells the two apart. The grace entry is what ``elapsed >= grace`` used to
    # stand for (#330): the grace was ARMED at its configured size and the
    # child's exit did NOT land inside it, i.e. the child survived the soft
    # signal and it was the hard kill, after the grace, that ended it.
    grace = grace_of(client)
    assert settled(events) == [
        ("await_exit", grace, "timed out"),
        ("hard_kill",),
        ("await_exit", REAP_BOUND, EXITED),
    ], (
        f"stop() did not wait out its {grace:.3f}s soft-kill grace and then escalate: "
        f"{events}. A grace entry whose exit LANDED is a child that died on the soft "
        "signal; a grace entry armed with less than the grace, or one that returned "
        "with no exit, is a grace that ended early; "
        "no grace entry is a grace that was skipped; no hard_kill is the escalation "
        "this test exists for (the child may still have died, via "
        "transport.close()'s own Popen.kill())"
    )
    assert proc.returncode is not None, "the child outlived stop()"
    # And the grace was a grace, not a silence: the child SAW the soft signal
    # and chose to stay. Without this the test also passes when nothing was
    # delivered and the timeout simply expired.
    assert _SOFT_KILL_BREADCRUMB in client.get_stderr(), (
        "the child never reported the soft signal; the grace elapsed but "
        f"nothing was delivered. stderr: {client.get_stderr()!r}"
    )
    assert "stub starting" in client.get_stderr()


async def test_a_soft_kill_that_was_never_sent_does_not_buy_a_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused soft signal escalates AT ONCE instead of waiting it out.

    ``ProcessTree.soft_kill`` returns whether the signal was actually SENT.
    ``False`` is the console-less win32 runner (``GenerateConsoleCtrlEvent``
    fails and the ``OSError`` is swallowed inside the primitive) and ``ESRCH``
    on POSIX — in both, nothing is on its way to the child, so the grace below
    would buy a full second of nothing. Before ``stop()`` read the bool, an
    undeliverable console event was indistinguishable from a child ignoring the
    signal and every win32 teardown paid its grace for it (review win-leg/F2).

    The refusal is INJECTED on the tree instance rather than by removing the
    console, because a POSIX box has no way to reproduce the win32 shape and
    this file takes no ``skipif``.
    """

    client = _SoftKillSurvivorClient()
    await client.start()
    await _await_breadcrumb(client, "stub ready")

    proc = client.process
    assert proc is not None
    tree = client._tree
    assert tree is not None
    tree.soft_kill = lambda *_a, **_k: False  # type: ignore[method-assign]
    events = record_stop(client, monkeypatch)

    await client.stop()

    # No grace entry at all: ``stop()`` never armed the grace, so it did not
    # pay it — which ``elapsed < grace`` could only say on an idle runner
    # (#330). Straight to the kill, then the bounded reap.
    grace = grace_of(client)
    assert settled(events) == [("hard_kill",), ("await_exit", REAP_BOUND, EXITED)], (
        f"stop() armed {events} after a soft kill that was never sent — a "
        f"{grace:.3f}s grace entry is a grace it had no reason to pay, since "
        "nothing was on its way to the child"
    )
    assert proc.returncode is not None, (
        "the child outlived stop(); skipping the grace must lead to the hard "
        "kill, not to skipping the kill"
    )
    # The stub writes the breadcrumb from inside its handler, so its ABSENCE is
    # the proof that the grace was skipped rather than merely short: no soft
    # signal was sent, and SIGKILL/TerminateJobObject cannot be caught.
    assert _SOFT_KILL_BREADCRUMB not in client.get_stderr(), (
        f"a soft signal reached the child after all: {client.get_stderr()!r}"
    )


async def test_a_cooperative_child_exits_under_the_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the same mechanism: the soft signal is enough.

    ``proc`` is captured BEFORE ``stop()`` because :attr:`RpcClient.returncode`
    reads through ``_proc``, which ``stop()`` drops — the exit status has to
    come off the ``Process`` object itself.
    """

    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", _COOPERATIVE_STUB]))
    await client.start()
    await _await_breadcrumb(client, "stub ready")
    proc = client.process
    assert proc is not None

    events = record_stop(client, monkeypatch)

    await client.stop()

    # One grace entry, armed in full, after which ``_await_exit`` answered that
    # the child had exited — and no kill: the escalation never ran (#330 — this
    # was ``elapsed < grace``, a bet against the runner's load). ``settled``
    # accepts both sides of the one race in that answer (the exit landing in
    # the loop turn the grace's timer fires: ``stop()`` still does not
    # escalate, rightly), so this is "did not escalate, and the child exited",
    # not "the exit beat the timer".
    grace = grace_of(client)
    assert settled(events) == [("await_exit", grace, EXITED)], (
        f"stop() recorded {events} for a child that exits on the soft signal — "
        f"expected one wait armed with the full {grace:.3f}s grace, the child "
        "exited when it ended, and no escalation after it"
    )
    # 3 is only reachable through the handler, so this is the delivery proof
    # that "the exit landed inside the grace" on its own is not.
    assert proc.returncode == 3, f"child exited {proc.returncode}, not via its handler"
    assert _SOFT_KILL_BREADCRUMB in client.get_stderr()


async def test_get_stderr_returns_captured_output() -> None:
    """``get_stderr()`` accumulates server stderr across the lifecycle."""

    client = _SoftKillSurvivorClient()
    await client.start()
    try:
        await _await_breadcrumb(client, "stub starting")
    finally:
        await client.stop()


async def test_stop_is_idempotent() -> None:
    """Calling stop() on an already-stopped client is a no-op."""

    client = _SoftKillSurvivorClient()
    await client.start()
    await client.stop()
    # Second stop should not raise.
    await client.stop()
