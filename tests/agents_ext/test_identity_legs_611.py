"""The delegation stop legs signal only what is still what they walked (#611 S2a, #758).

Real processes. A stand-in delegated child, a session leader that ignores
SIGTERM as a wedged child does, runs two things a ``bash`` tool leaves under a
child: ``G``, a command in a session of its own, and a shell ``S`` that leads
its group, waits on a foreground ``sleep`` (and a second one after it, so a
killed foreground command does not end ``S``) and has put ``B`` in the
background with ``(cmd &)`` (PPID 1, in ``S``'s group, so only ``S``'s group
reaches it).

A number reused between the walk and the kill is simulated the way #723's
cases simulate it: the start-time reader is made to answer another time for
one walked pid (``_process_identity.process_started``, which exists on the
base too), so the number "now names another process". On ``3cab5d16`` every
door SIGKILLed that pid and its group anyway: the walk carried no start times
and nothing was compared before a signal.

Doors: ``eager`` is ``PrintChannel._eager_abort`` (Esc, Esc Esc, a signal to
``-p``), ``stop`` is ``abort_child`` → ``reap(eager_kill=True)`` (``stop``,
``stop_all``, the teardown), ``grace`` is ``PrintChannel._reap``'s SIGTERM, a
grace the child ignores, and the escalation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import print_channel as pc
from aelix_agents import reaper as rp
from aelix_agents.print_channel import PrintChannel, RunningChild
from aelix_ai.utils import _process_identity

from tests.event_waits import wait_until

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX walks and groups; the Job is the tree on win32"
)

_SIGKILL = getattr(signal, "SIGKILL", 9)

_CHILD = textwrap.dedent(
    """
    import json, os, signal, subprocess, sys, time
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    out = sys.argv[1]
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    g = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True, **quiet
    )
    script = '(sleep 600 & echo $! > "$1.tmp" && mv "$1.tmp" "$1"); sleep 600; sleep 600'
    s = subprocess.Popen(["/bin/sh", "-c", script, "sh", out + ".bg"], start_new_session=True, **quiet)
    while not os.path.exists(out + ".bg"):
        time.sleep(0.01)
    with open(out + ".bg", encoding="utf-8") as handle:
        b = int(handle.read())
    with open(out + ".tmp", "w", encoding="utf-8") as handle:
        json.dump({"g": g.pid, "s": s.pid, "b": b}, handle)
    os.replace(out + ".tmp", out)
    time.sleep(600)
    """
)


class _Tree:
    def __init__(self, proc: Any, pids: dict[str, int]) -> None:
        self.proc = proc
        self.g, self.s, self.b = pids["g"], pids["s"], pids["b"]

    def everyone(self) -> list[int]:
        return [self.proc.pid, self.g, self.s, self.b]


@pytest.fixture
async def tree(tmp_path: Path) -> AsyncIterator[_Tree]:
    out = tmp_path / "pids.json"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _CHILD, str(out), start_new_session=True
    )
    made: _Tree | None = None
    try:
        await wait_until(out.exists, timeout=30, what="the stand-in child to announce its tree")
        made = _Tree(proc, json.loads(out.read_text(encoding="utf-8")))
        await wait_until(
            lambda: len(rp.descendant_pids(proc.pid)) >= 3,
            timeout=30,
            what="the shell's foreground sleep to start",
        )
        yield made
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(proc.pid, _SIGKILL)
        if made is not None:
            for pid in (made.g, made.b):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, _SIGKILL)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(made.s, _SIGKILL)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 10)


def _shift(monkeypatch: pytest.MonkeyPatch, pid: int) -> None:
    """``pid``'s number now reads as a process that started 1000 s earlier."""

    real = _process_identity.process_started

    def started(target: int) -> float | None:
        now = real(target)
        return now - 1000.0 if target == pid and now is not None else now

    monkeypatch.setattr(_process_identity, "process_started", started)


async def _door(door: str, tree: _Tree) -> None:
    row = RunningChild(id=f"sub-identity-{door}", profile="scout")
    row.proc = tree.proc
    if door == "eager":
        PrintChannel._eager_abort(tree.proc, row)
    elif door == "stop":
        await asyncio.wait_for(pc.abort_child(row, grace=0.3), 30)
    else:
        await asyncio.wait_for(PrintChannel(grace=0.3)._reap(tree.proc, row), 30)
    await asyncio.wait_for(tree.proc.wait(), 30)


def _zombie(pid: int) -> bool:
    """``ps`` says ``Z``: exited, not reaped. Read with ``ps`` so the base can run it too."""

    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return state.stdout.strip().startswith("Z")


def _running(pid: int) -> bool:
    """Alive and not a zombie, from ONE ``ps`` read.

    Not ``pid_is_live`` then ``ps``: a zombie its reaper (init, docker's
    ``--init``) takes between the two reads looked alive to the first and
    absent to the second, and read as running (#758 fix round 1, verify F1).
    No ``ps`` row is gone.
    """

    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


async def _settled(pid: int) -> bool:
    """Whether ``pid`` still runs half a second after the door returned."""

    await asyncio.sleep(0.5)
    return _running(pid)


_DOORS = pytest.mark.parametrize("door", ["eager", "stop", "grace"])


@_DOORS
async def test_a_walked_pid_whose_stamp_changed_gets_no_signal(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, door: str
) -> None:
    """``G`` was walked; its number now reads as another process. It is not signalled.

    The rest of the tree still dies: the child (its own ``Process``) and ``S``
    with its group. Fails on ``3cab5d16`` on every door: ``G`` is SIGKILLed.
    """

    _shift(monkeypatch, tree.g)

    await _door(door, tree)

    assert await _settled(tree.g), "a number that reads as another process was signalled"
    await wait_until(lambda: not _running(tree.b), timeout=10, what="B to die with S's group")


@_DOORS
async def test_a_walked_leaders_group_is_signalled_before_the_leader_and_only_while_it_verifies(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, door: str
) -> None:
    """``S``'s number reads as another process: neither it nor its group is signalled.

    ``B`` is reachable only through ``S``'s group, so it survives exactly when
    the group is withheld. Fails on ``3cab5d16``: ``killpg(S)`` took ``B``.
    """

    _shift(monkeypatch, tree.s)

    await _door(door, tree)

    assert await _settled(tree.b), "the group of a leader that did not verify was signalled"
    assert _running(tree.s), "a leader that did not verify was signalled"
    await wait_until(lambda: not _running(tree.g), timeout=10, what="G to die")


def _record_sends(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, int]]:
    """Every ``kill``, ``killpg`` and pidfd send, in order, each passed through."""

    sends: list[tuple[str, int, int]] = []
    fds: dict[int, int] = {}
    real_kill, real_killpg = os.kill, os.killpg

    def kill(pid: int, sig: int) -> None:
        sends.append(("kill", pid, sig))
        real_kill(pid, sig)

    def killpg(pgid: int, sig: int) -> None:
        sends.append(("killpg", pgid, sig))
        real_killpg(pgid, sig)

    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(os, "killpg", killpg)
    opener = getattr(os, "pidfd_open", None)
    sender = getattr(signal, "pidfd_send_signal", None)
    if opener is not None and sender is not None:

        def pidfd_open(pid: int, *args: Any) -> int:
            fd = opener(pid, *args)
            fds[fd] = pid
            return fd

        def pidfd_send_signal(fd: int, sig: int, *args: Any) -> None:
            if sig:
                sends.append(("pidfd", fds.get(fd, -1), sig))
            sender(fd, sig, *args)

        monkeypatch.setattr(os, "pidfd_open", pidfd_open)
        monkeypatch.setattr(signal, "pidfd_send_signal", pidfd_send_signal)
    return sends


@_DOORS
async def test_a_walked_leaders_group_goes_before_the_leader(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, door: str
) -> None:
    """With nothing reused, ``S``'s group is signalled first and ``S`` after it (Codex N5).

    Killing the leader first can let its parent reap it and free the number
    the later ``killpg`` names. Fails on ``3cab5d16``: the group came after
    every pid.
    """

    sends = _record_sends(monkeypatch)

    await _door(door, tree)

    kills = [(how, pid) for how, pid, sig in sends if sig == _SIGKILL]
    assert ("killpg", tree.s) in kills, sends
    to_s = [i for i, (how, pid) in enumerate(kills) if pid == tree.s and how != "killpg"]
    assert kills.index(("killpg", tree.s)) < min(to_s, default=len(kills)), sends
    await wait_until(lambda: not _running(tree.b), timeout=10, what="B to die")


def _null_stamp(monkeypatch: pytest.MonkeyPatch, pid: int) -> None:
    """The walk could not read ``pid``'s start time."""

    real: Callable[[int], list[Any]] | None = getattr(rp, "descendant_identities", None)

    def walk(root: int) -> list[Any]:
        assert real is not None
        return [entry._replace(stamp=None) if entry.pid == pid else entry for entry in real(root)]

    # ``raising=False``: on ``3cab5d16`` neither module had the name, and nothing calls it there.
    monkeypatch.setattr(pc, "descendant_identities", walk, raising=False)
    monkeypatch.setattr(rp, "descendant_identities", walk, raising=False)


@_DOORS
async def test_a_live_leader_with_an_unknown_stamp_is_never_group_killed(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, door: str
) -> None:
    """``S`` lives, but the walk has no start time for it: no group, no pid signal.

    Fails on ``3cab5d16``, which had no start times at all: ``killpg(S)`` took ``B``.
    """

    _null_stamp(monkeypatch, tree.s)

    await _door(door, tree)

    assert await _settled(tree.b), "the group of a leader of unknown identity was signalled"
    assert _running(tree.s)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="pidfds are Linux")
async def test_linux_signals_go_through_a_pidfd(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Linux a walked pid's SIGKILL goes through a pidfd, opened right before its first signal.

    Fails on ``3cab5d16``: ``os.kill(G, SIGKILL)``.
    """

    sends = _record_sends(monkeypatch)

    await _door("eager", tree)

    assert ("pidfd", tree.g, _SIGKILL) in sends, sends
    assert ("kill", tree.g, _SIGKILL) not in sends, sends
    await wait_until(lambda: not _running(tree.g), timeout=10, what="G to die")


class _Dead:
    pid = 2**22 - 3
    returncode = 0


async def test_the_success_drain_still_signals_no_group(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_drain_after_exit`` (a delegation that SUCCEEDED) signals the holder it named and no group.

    ``S`` stands in for a process still holding a finished child's stdout. It is
    signalled; ``B``, in its group, is not. Green on ``3cab5d16`` and after
    (#611 S2a gives this caller the per-pid check only; ``reaper.kill_tree``).
    """

    stamp = _process_identity.process_started(tree.s)
    entry = rp.WalkEntry(tree.s, stamp, None) if hasattr(rp, "WalkEntry") else None
    monkeypatch.setattr(pc, "_child_pipe_links", lambda _proc: {"pipe:[1]"})
    monkeypatch.setattr(pc, "pipe_holder_pids", lambda _links: [tree.s])
    monkeypatch.setattr(pc, "pipe_holders", lambda _links: [entry], raising=False)
    monkeypatch.setattr(pc, "POST_EXIT_DRAIN_SECONDS", 0.05)
    sends = _record_sends(monkeypatch)
    pumps: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    await PrintChannel._drain_after_exit(_Dead(), pumps, None)

    assert [s for s in sends if s[0] == "killpg"] == [], sends
    await wait_until(lambda: not _running(tree.s), timeout=10, what="S to die")
    assert await _settled(tree.b), "the success drain signalled a group"


class _Libproc:
    """``proc_listpids(PROC_PPID_ONLY, parent, …)`` answered from a table, in bytes."""

    def __init__(self, children: dict[int, list[int]]) -> None:
        self.children = children

    def proc_listpids(self, _kind: int, parent: int, buffer: Any, _size: int) -> int:
        kids = self.children.get(parent, [])
        for i, kid in enumerate(kids):
            buffer[i] = kid
        return len(kids) * 4


def test_the_macos_walk_keeps_a_child_only_while_its_own_read_names_the_parent() -> None:
    """R3, narrowed (#611 S2a, Codex N3): each child's parent and start time come from one read.

    The kernel lists 21 and 22 as 20's children, but 22's own read now names
    another parent (its number was handed on between the two reads), so 22 and
    everything under it are dropped; 21 keeps the start time of its read.
    """

    from aelix_ai.utils import _process_walk

    reads = {21: (20, 2.0), 22: (99, 3.0), 23: (22, 4.0)}
    entries = _process_walk.descendant_identities(
        20,
        proc=Path("/nonexistent-proc"),
        is_darwin=lambda: True,
        libproc=lambda: _Libproc({20: [21, 22], 22: [23]}),
        identity=reads.get,
    )
    assert entries == [rp.WalkEntry(21, 2.0, 20)]


async def test_reap_closes_the_walk_it_is_handed() -> None:
    """``reap`` owns the walk handed to it, however it ends: here, a child already reaped.

    On Linux a walk holds pidfds; one left open per stop is an fd leak.
    """

    walk = rp.HeldWalk([rp.WalkEntry(os.getpid(), _process_identity.process_started(os.getpid()), None)])
    walk.verifies(walk.entries[0])  # acquires, where pidfds exist

    class _Reaped:
        pid = 2**22 - 5
        returncode = 0

    assert await rp.reap(_Reaped(), descendants=walk) == 0
    assert not walk._finalizer.alive


def test_kill_tree_closes_the_walk_it_built(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handed walk entries, ``kill_tree`` holds them itself and closes that hold; a held walk stays the caller's."""

    closed: list[object] = []
    real = rp.HeldWalk.close

    def close(self: Any) -> None:
        closed.append(self)
        real(self)

    monkeypatch.setattr(rp.HeldWalk, "close", close)
    entry = rp.WalkEntry(2**22 - 7, None, None)
    rp.kill_tree(_Dead(), [entry])
    assert len(closed) == 1
    held = rp.HeldWalk([entry])
    rp.kill_tree(_Dead(), held)
    assert len(closed) == 1, "a walk the caller holds is the caller's to close"
    held.close()



# === #758 fix round 1, item 1: groups deepest first (Codex #1) ================

_CHAIN = textwrap.dedent(
    """
    import json, os, signal, subprocess, sys, time
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    out, role = sys.argv[1], sys.argv[2]
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if role == "H":
        # H: leads its group; leaves X there with PPID 1; ends when G's pipe closes.
        readfd = int(sys.argv[3])
        r, w = os.pipe()
        mid = os.fork()
        if mid == 0:
            os.close(r)
            x = os.fork()
            if x == 0:
                os.close(w)
                signal.signal(signal.SIGHUP, signal.SIG_IGN)
                time.sleep(600)
                os._exit(0)
            os.write(w, str(x).encode())
            os._exit(0)
        os.close(w)
        x = int(os.read(r, 100))
        os.waitpid(mid, 0)
        with open(out + ".tmp", "w") as h:
            json.dump({"g": os.getppid(), "h": os.getpid(), "x": x}, h)
        os.replace(out + ".tmp", out)
        os.read(readfd, 1)
        os._exit(0)
    if role == "G":
        r, w = os.pipe()
        subprocess.Popen([sys.executable, "-c", sys.argv[3], out, "H", str(r)],
                         start_new_session=True, pass_fds=(r,), **quiet)
        os.close(r)
        time.sleep(600)
    subprocess.Popen([sys.executable, "-c", sys.argv[3], out, "G", sys.argv[3]],
                     start_new_session=True, **quiet)
    time.sleep(600)
    """
)


@pytest.mark.parametrize("door", ["eager", "stop"])
async def test_a_leader_that_dies_with_its_ancestors_group_still_has_its_group_signalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, door: str
) -> None:
    """``C → G → H``, each leading a group; ``X`` sits orphaned in ``H``'s group; ``H``
    reads a pipe whose writer is ``G``, so ``H`` ends when ``G``'s group is killed.

    The interleaving is forced: after ``killpg(G)`` the stop waits until ``H`` is
    gone. Groups deepest first reach ``H``'s group while ``H`` still verifies.
    Fails on ``f3e9b814`` (shallowest first: ``G``'s group, then ``H`` no longer
    verifies and its group is withheld, ``X`` left running); passes on ``3cab5d16``,
    where every group went after every pid, unchecked.
    """

    out = tmp_path / "chain.json"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _CHAIN, str(out), "C", _CHAIN, start_new_session=True
    )
    x = None
    leaders: list[int] = []
    try:
        await wait_until(out.exists, timeout=30, what="H to announce X")
        pids = json.loads(out.read_text(encoding="utf-8"))
        g, h, x = pids["g"], pids["h"], pids["x"]
        leaders.extend([g, h])
        real_killpg = os.killpg

        def killpg(group: int, sig: int) -> None:
            real_killpg(group, sig)
            if group == g:
                deadline = time.monotonic() + 10
                while _running(h) and time.monotonic() < deadline:
                    time.sleep(0.005)

        monkeypatch.setattr(os, "killpg", killpg)
        row = RunningChild(id=f"sub-chain-{door}", profile="scout")
        row.proc = proc
        if door == "eager":
            PrintChannel._eager_abort(proc, row)
        else:
            await asyncio.wait_for(pc.abort_child(row, grace=0.3), 30)
        await asyncio.wait_for(proc.wait(), 30)
        await wait_until(lambda: not _running(x), timeout=10, what="X, in H's group, to die")
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(proc.pid, _SIGKILL)
        if x is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(x, _SIGKILL)
        for leader in leaders:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(leader, _SIGKILL)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 10)


def test_groups_and_pids_go_deepest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """The order itself: every verified leader's group deepest first, then every pid deepest first.

    Injected: three walked leaders, deepest first as the walk returns them, all
    still the processes walked. ``f3e9b814`` sent the groups shallowest first.
    """

    pids = (2**22 - 11, 2**22 - 12, 2**22 - 13)
    real = _process_identity.process_started
    monkeypatch.setattr(
        _process_identity, "process_started", lambda pid: 7.0 if pid in pids else real(pid)
    )
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    sends: list[tuple[str, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, _sig: sends.append(("kill", pid)))
    monkeypatch.setattr(os, "killpg", lambda pid, _sig: sends.append(("killpg", pid)))
    walk = rp.HeldWalk([rp.WalkEntry(pid, 7.0, None) for pid in pids], pids)

    walk.kill(_SIGKILL)

    assert sends == [("killpg", p) for p in pids] + [("kill", p) for p in pids]


async def test_the_walk_names_the_deepest_generation_first(tmp_path: Path) -> None:
    """``descendant_identities`` answers deepest first: ``H`` (``C``'s grandchild) before ``G``."""

    out = tmp_path / "chain.json"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _CHAIN, str(out), "C", _CHAIN, start_new_session=True
    )
    try:
        await wait_until(out.exists, timeout=30, what="the chain to start")
        h = json.loads(out.read_text(encoding="utf-8"))["h"]
        walked = [entry.pid for entry in rp.descendant_identities(proc.pid)]
        assert len(walked) == 2 and walked[0] == h, walked
    finally:
        for pid in rp.descendant_pids(proc.pid):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, _SIGKILL)
        with contextlib.suppress(ProcessLookupError):
            os.kill(proc.pid, _SIGKILL)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 10)


def test_one_read_names_a_processs_parent_and_start_time() -> None:
    """``process_identity`` answers this process's parent and the start time ``process_started`` reads (I13)."""

    found = _process_identity.process_identity(os.getpid())
    assert found == (os.getppid(), _process_identity.process_started(os.getpid()))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="pidfds are Linux")
def test_a_leader_reaped_after_its_pidfd_was_opened_does_not_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pidfd pins the walked leader: once it is reaped, the leader no longer verifies and its
    group number gets no ``killpg``, whatever holds that number by then (I10, S12)."""

    leader = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        stamp = _process_identity.process_started(leader.pid)
        walk = rp.HeldWalk([rp.WalkEntry(leader.pid, stamp, os.getpid())], [leader.pid])
        assert walk.verifies(walk.entries[0]), "the live leader verifies through its pidfd"
        leader.kill()
        leader.wait(5)
        sent: list[int] = []
        monkeypatch.setattr(os, "killpg", lambda pgid, _sig: sent.append(pgid))
        assert not walk.verifies(walk.entries[0])
        walk.kill(_SIGKILL)
        assert sent == []
        walk.close()
    finally:
        with contextlib.suppress(ProcessLookupError):
            leader.kill()
        leader.wait(5)
