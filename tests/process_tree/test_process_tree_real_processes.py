"""Real children for ``aelix_ai.utils._process_tree`` (#202). Integration coverage.

``test_process_tree_api.py`` pins the DISPATCH on every runner through the
injected ``platform=`` and the fake ``_Win32Api``. This file spawns actual
processes, so each case measures whatever the host really does — the POSIX arms
here run on ubuntu and macOS, the win32 arms only on ``windows-latest``. That is
integration coverage supplied by the gating windows leg, NOT a way around the
no-``skipif`` rule: every case below runs everywhere, and where the ANSWER is
genuinely platform-specific (a job holds a ``setsid`` grandchild, a process group
does not) the case asserts both arms and documents the asymmetry.

Liveness is ``tests/process_probe`` and never ``os.kill(pid, 0)`` — on Windows
signal 0 falls through CPython's ``os_kill_impl`` to ``TerminateProcess``, so the
"probe" would cause the death it claims to observe (#203).

Every wait bound here is :data:`DEADLINE` seconds. Signal delivery is scheduled
by the kernel, and CI runners are slow enough that a tight bound measures the
runner rather than the kill.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
from aelix_ai.utils._process_tree import (
    ProcessTree,
    _retained_handle,
    containment_spawn_kwargs,
    kill_process_tree,
)

from tests.process_probe import STATE_ALIVE, is_dead_or_zombie, probe_state

#: Seconds a kill is given before the case fails.
DEADLINE = 5.0

#: A child that outlives any of these cases unless something kills it.
SLEEPER = "import time; time.sleep(60)"

#: Spawns one grandchild and reports its pid, then exits.
#:
#: The read on stdin is what makes the win32 assignment window irrelevant here:
#: the parent attaches the job BEFORE it writes the byte, so the grandchild is
#: created strictly afterwards and inherits membership by construction.
#: ``argv[1] == "own-session"`` gives the grandchild a session/group of its own,
#: which is the shape a process group cannot reach and a job can.
GRANDCHILD_SOURCE = """\
import subprocess
import sys

from aelix_ai.utils._process_tree import containment_spawn_kwargs

sys.stdin.buffer.read(1)
extra = containment_spawn_kwargs(new_session=True) if sys.argv[1] == "own-session" else {}
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    **extra,
)
sys.stdout.write(f"{child.pid}\\n")
sys.stdout.flush()
"""

#: Same, but the leader leaves without waiting and without flushing atexit, so
#: on POSIX it is a ZOMBIE whose group still holds a running member.
ZOMBIE_LEADER_SOURCE = """\
import os
import subprocess
import sys

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
sys.stdout.write(f"{child.pid}\\n")
sys.stdout.flush()
os._exit(0)
"""

#: Answers the soft signal on whichever of the two names this platform has, so
#: a pass proves the signal was DELIVERED rather than that a grace elapsed.
COOPERATIVE_SOURCE = """\
import signal
import sys
import time


def seen(*_):
    sys.stderr.write("signalled\\n")
    sys.stderr.flush()
    sys.exit(3)


for name in ("SIGTERM", "SIGBREAK"):
    number = getattr(signal, name, None)
    if number is not None:
        signal.signal(number, seen)
sys.stdout.write("armed\\n")
sys.stdout.flush()
while True:
    time.sleep(0.05)
"""


def _taskkill_argv0s() -> tuple[str, str]:
    """The two argv[0]s ``_taskkill_tree`` tries, in its order, for the cleanup.

    ``System32`` is not always on ``PATH``, and a bare ``taskkill`` then fails
    ``ENOENT`` — Pi #6596/#8560, which is why the product resolver exists. A
    cleanup helper is the worst place to inherit that bug: each ``_reap`` is
    what stops a 60 s sleeper leaking out of a failed assertion into the rest of
    the leg, and its ``suppress(Exception)`` would make the miss invisible
    (review win-leg/F8). The product function is not called instead because its
    win32 arm is ``/T`` (a tree) and its POSIX arm is ``killpg`` — the two
    mechanisms under test here.
    """

    root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
    return os.path.join(root, "System32", "taskkill.exe"), "taskkill"


def _reap(pid: int) -> None:
    """Kill ``pid`` if it is still there. Cleanup only — never an assertion.

    Deliberately root-only: a test that leaks a pid must not have its cleanup
    reach for a process GROUP, which is the very thing under test.
    """

    with contextlib.suppress(Exception):
        if is_dead_or_zombie(pid):
            return
        if sys.platform == "win32":
            for argv0 in _taskkill_argv0s():
                try:
                    subprocess.run(
                        [argv0, "/F", "/PID", str(pid)],
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
                except FileNotFoundError:
                    continue
                return
        else:
            os.kill(pid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]


@pytest.fixture
def strays() -> Iterator[list[int]]:
    """Pids the case is responsible for, killed on the way out either way."""

    pids: list[int] = []
    yield pids
    for pid in pids:
        _reap(pid)


def _await_dead(pid: int, *, timeout: float = DEADLINE) -> str:
    """Poll until ``pid`` is dead, returning the LAST OBSERVED state.

    The return value is the assertion subject, so a case that fails says which
    state it was still seeing rather than just ``False``.
    """

    deadline = time.monotonic() + timeout
    state = probe_state(pid)
    while state == STATE_ALIVE and time.monotonic() < deadline:
        time.sleep(0.05)
        state = probe_state(pid)
    return state


def _spawn_parent_of_a_grandchild(
    strays: list[int], *, own_session: bool, kill_on_close: bool = False
) -> tuple[subprocess.Popen[str], ProcessTree, int]:
    """Spawn the tree, attach it, release the child, and return the grandchild pid."""

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            GRANDCHILD_SOURCE,
            "own-session" if own_session else "inherit",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        **containment_spawn_kwargs(),
    )
    strays.append(proc.pid)
    # Attach IMMEDIATELY, before anything is allowed to run: on win32 only
    # descendants created after the assignment inherit membership.
    tree = ProcessTree.attach(proc.pid, kill_on_close=kill_on_close, handle=_retained_handle(proc))
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write("g")
    proc.stdin.flush()
    grandchild = int(proc.stdout.readline().strip())
    strays.append(grandchild)
    # Vacuity guard (#203's lesson): a grandchild that was never running would
    # make every "it died" assertion below pass while measuring nothing.
    assert probe_state(grandchild) == STATE_ALIVE
    return proc, tree, grandchild


def test_a_descendant_whose_parent_already_exited_still_dies(strays: list[int]) -> None:
    """Pi #9129's shape: the link ``taskkill /T`` walks is already broken.

    MSYS bash runs each pipeline stage through a short-lived subshell, so by the
    time a kill runs the leaves have a dead parent, ``taskkill`` kills the bash
    layers and exits 0, and the leaves run on (the reporter measured 70 minutes).
    Both containers here survive the parent: a POSIX process group keeps its id
    while any member lives, and a Job Object holds members outright.
    """

    proc, tree, grandchild = _spawn_parent_of_a_grandchild(strays, own_session=False)

    assert proc.wait(timeout=DEADLINE) is not None
    assert is_dead_or_zombie(proc.pid), "the case needs the parent gone before the kill"

    tree.hard_kill()

    assert _await_dead(grandchild) != STATE_ALIVE
    tree.close()


def test_a_setsid_descendant_is_reached_by_the_job_and_not_by_the_group(
    strays: list[int],
) -> None:
    """The POSIX/Windows asymmetry, asserted rather than assumed.

    A grandchild that made itself a session leader has left our process group,
    so ``killpg`` cannot reach it — which is by design: the walk that does reach
    it is ``aelix_agents/reaper.py``'s (ADR-0197 I2), not this module's. A job
    has no such hole; membership is inherited and we never set ``BREAKAWAY_OK``.
    Windows containment is therefore STRICTLY STRONGER here.
    """

    proc, tree, grandchild = _spawn_parent_of_a_grandchild(strays, own_session=True)

    assert proc.wait(timeout=DEADLINE) is not None

    tree.hard_kill()

    if sys.platform == "win32":
        assert _await_dead(grandchild) != STATE_ALIVE
    else:
        # Give the kill the same window the win32 arm gets before claiming the
        # survival is real rather than merely not-yet-observed.
        time.sleep(0.5)
        assert probe_state(grandchild) == STATE_ALIVE
    tree.close()


def test_kill_on_close_ends_leftovers_only_where_it_was_asked_for(
    strays: list[int],
) -> None:
    """``close()`` is a release everywhere, and a kill only on win32 when asked.

    Revision 1 of this design had ``close()`` signal on POSIX too, and that
    killed helpers a hook had deliberately backgrounded on the SUCCESS path. So
    POSIX never signals here, and on win32 ``KILL_ON_JOB_CLOSE`` is a per-site
    decision: the rpc child asks for it ("one child per task"), the hook and
    ``!command`` sites do not.
    """

    proc, tree, grandchild = _spawn_parent_of_a_grandchild(
        strays, own_session=False, kill_on_close=True
    )

    assert proc.wait(timeout=DEADLINE) is not None

    tree.close()

    if sys.platform == "win32":
        assert _await_dead(grandchild) != STATE_ALIVE
    else:
        time.sleep(0.5)
        assert probe_state(grandchild) == STATE_ALIVE


def test_kill_process_tree_ends_a_live_child_for_real(strays: list[int]) -> None:
    """The pid-only path, executed rather than stubbed.

    #105's win32 arm has been shipped for months with ``taskkill`` mocked in
    every case, so the shell-out itself had never run anywhere. On the windows
    leg this is the first real ``taskkill.exe``.
    """

    proc = subprocess.Popen(
        [sys.executable, "-c", SLEEPER], **containment_spawn_kwargs(new_session=True)
    )
    strays.append(proc.pid)

    kill_process_tree(proc.pid)

    assert proc.wait(timeout=DEADLINE) is not None


def test_kill_process_tree_reaches_the_group_of_a_zombie_leader(
    strays: list[int],
) -> None:
    """Unreaped leader, live descendant — the shape the ONE caller left is in.

    Since #222 that caller is ``rpc_client.stop()``'s degradation, for a client
    whose ``attach`` itself raised: an asyncio spawn site, where
    ``returncode is None`` does not prove the pid is unreaped (the primitive's
    PID/PGID paragraph), so it can arrive here either side of the reap. The two
    tool sites that used to call — ``bash.py``'s ``exec`` and
    ``run_cancellable`` — hold a :class:`ProcessTree` now and never reach this
    function. What holds either way is that the fallback addresses only the
    group number the spawn asked for — pinned while any member lives, ``ESRCH``
    when the group is empty. On Linux ``getpgid`` still resolves through a
    zombie; on Darwin it raises ``ProcessLookupError`` (measured), which is why
    the pid is used as the pgid instead of giving up. On win32 there is no
    group and ``taskkill /T`` needs a live root, so the descendant survives —
    which is exactly why that degradation is a degradation, and what a tree
    buys at a site that has one. This case pins the pid-only path only; the
    site-level assertion for the tool children is
    ``tests/tools/test_bash_tool_containment.py``'s timeout-with-an-exited-
    intermediate-parent case.
    """

    proc = subprocess.Popen(
        [sys.executable, "-c", ZOMBIE_LEADER_SOURCE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        **containment_spawn_kwargs(new_session=True),
    )
    strays.append(proc.pid)
    assert proc.stdout is not None
    grandchild = int(proc.stdout.readline().strip())
    strays.append(grandchild)
    assert probe_state(grandchild) == STATE_ALIVE
    # Deliberately no ``proc.wait()``: the leader must still be unreaped.
    assert _await_dead(proc.pid) != STATE_ALIVE

    kill_process_tree(proc.pid)

    if sys.platform == "win32":
        time.sleep(0.5)
        assert probe_state(grandchild) == STATE_ALIVE
    else:
        assert _await_dead(grandchild) != STATE_ALIVE
    proc.wait(timeout=DEADLINE)


def test_soft_kill_is_delivered_and_survivable(strays: list[int]) -> None:
    """The soft stage has to be a real, catchable signal on both platforms.

    On POSIX that is SIGTERM. On Windows it is ``CTRL_BREAK_EVENT``, the one
    value CPython routes to ``GenerateConsoleCtrlEvent`` instead of
    ``TerminateProcess`` — every other signal there is uncatchable, which is why
    #207 measured a grace of 0.047 s. It needs a shared console and a
    ``CREATE_NEW_PROCESS_GROUP`` child. IF THIS FAILS ON THE WINDOWS LEG the
    runner has no console and ADR-0238's cooperative claim must be narrowed,
    with the measured reason, before merge.
    """

    proc = subprocess.Popen(
        [sys.executable, "-c", COOPERATIVE_SOURCE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **containment_spawn_kwargs(),
    )
    strays.append(proc.pid)
    tree = ProcessTree.attach(proc.pid, handle=_retained_handle(proc))
    assert proc.stdout is not None
    assert proc.stderr is not None
    assert proc.stdout.readline().strip() == "armed"

    # The return value separates the two ways this case can go red on the
    # windows leg: False is "the runner has no console, nothing was sent" (the
    # narrowing trigger below), True followed by a timeout would be "it was sent
    # and the child did not answer".
    assert tree.soft_kill() is True, "the soft signal was never sent"

    assert proc.wait(timeout=DEADLINE) == 3
    assert "signalled" in proc.stderr.read()
    tree.close()


def test_the_child_is_contained(strays: list[int]) -> None:
    """``contained`` is not a hope: ask the operating system on both arms.

    The win32 arm also guards the one thing D.6 admits is untestable — the
    runner may already be inside a job, and a nested TRUE could mask a failed
    assignment — by asserting ``contained`` as well, which is false whenever
    ``AssignProcessToJobObject`` raised.
    """

    proc = subprocess.Popen([sys.executable, "-c", SLEEPER], **containment_spawn_kwargs())
    strays.append(proc.pid)
    handle = _retained_handle(proc)
    tree = ProcessTree.attach(proc.pid, handle=handle)

    assert tree.contained is True

    if sys.platform == "win32":
        assert tree.job is not None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.IsProcessInJob.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        kernel32.IsProcessInJob.restype = ctypes.c_int
        member = ctypes.c_int()
        assert kernel32.IsProcessInJob(handle, tree.job, ctypes.byref(member)) != 0
        assert member.value == 1
    else:
        assert tree.job is None
        assert os.getpgid(proc.pid) == proc.pid

    tree.hard_kill()
    assert proc.wait(timeout=DEADLINE) is not None
    tree.close()


#: The rpc child's exact shape: a Python-level handler installed with
#: ``signal.signal`` that only schedules ``Event.set`` on the loop, and a main
#: thread parked in the event loop with nothing else to do. On Windows that is
#: the proactor loop blocked in ``GetQueuedCompletionStatus``; what is supposed
#: to wake it is the wakeup fd ``BaseProactorEventLoop.__init__`` registers.
EVENT_LOOP_SOURCE = """\
import asyncio
import signal
import sys


async def main():
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for name in ("SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            signal.signal(number, lambda *_: loop.call_soon_threadsafe(stop.set))
    sys.stdout.write("armed\\n")
    sys.stdout.flush()
    await stop.wait()
    sys.stderr.write("woke\\n")
    sys.stderr.flush()
    return 5


sys.exit(asyncio.run(main()))
"""


def test_soft_kill_wakes_a_child_parked_in_its_event_loop(strays: list[int]) -> None:
    """The stub in ``test_soft_kill_is_delivered_and_survivable`` sleeps in
    50 ms slices, so it checks for signals twenty times a second whatever the
    platform does. The rpc child does not: it sits in ``asyncio`` with nothing
    scheduled, and on Windows a Python-level ``SIGBREAK`` handler only runs when
    the main thread next executes bytecode — which a proactor loop parked in
    ``GetQueuedCompletionStatus`` does only if the signal's wakeup byte lands on
    the loop's self-socket. This is that mechanism, alone, with a real child.
    Exit 5 is the handler's doing; a hard kill cannot produce it.
    """

    proc = subprocess.Popen(
        [sys.executable, "-c", EVENT_LOOP_SOURCE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **containment_spawn_kwargs(new_session=True),
    )
    strays.append(proc.pid)
    tree = ProcessTree.attach(proc.pid, handle=_retained_handle(proc))
    assert proc.stdout is not None
    assert proc.stderr is not None
    assert proc.stdout.readline().strip() == "armed"

    assert tree.soft_kill() is True, "the soft signal was never sent"

    started = time.monotonic()
    code = proc.wait(timeout=DEADLINE)
    elapsed = time.monotonic() - started
    assert code == 5, (
        f"the child exited {code} after {elapsed:.3f}s: the handler never ran, so "
        "the loop was not woken by the signal. stderr: "
        f"{proc.stderr.read()!r}"
    )
    assert "woke" in proc.stderr.read()
    tree.close()
