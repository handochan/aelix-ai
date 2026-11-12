"""#360 — a ``start()`` that does not return takes the child it spawned with it.

THE DEFECT. :meth:`RpcClient.start` spawns the child, then waits out a 100 ms
grace to see whether it dies at once. A cancel that landed in that grace raised
out of ``start`` and left the child running: the caller had no handle yet
(``RpcChannel`` publishes the process on its row only after ``start`` returns),
so nothing would ever stop it. Measured on the base, macOS and Linux: the child
and its grandchild alive after the ``CancelledError``, ``client.process`` still
set, and a child that never reads its stdin still running with PPID 1 after the
parent had exited.

THE CONTRACT these pin, once the fork has happened: every way ``start`` ends
other than returning leaves no child, no member of its process group, and the
client released (tasks, transport, tree) — whether the cancel lands in the
spawn, in the grace or in the abandon itself, whether the root has already
exited, whether the kill raises. While ``start`` runs, a second ``start`` and
every command are refused, and ``stop`` asks ``start`` to give up.

WHERE THE CANCEL LANDS IS NOT LEFT TO A CLOCK. Three seams, one per question:

* the GRACE MADE LONG (:class:`_LongGrace`), so the cancel can only land inside
  it, once the child has written the pids of its tree — the case for "and
  everything under it";
* the product's own 100 ms grace, the cancel sent from the exit watcher's FIRST
  step (:class:`_CancelInTheGrace`). ``start`` creates that task and then
  suspends in the grace, so the step runs while ``start`` waits there;
* the SPAWN HELD OPEN after its fork (:func:`_hold_the_spawn`, once CPython's
  call has returned; and CPython's own connection of the child's stdout held,
  in ``test_a_cancel_while_cpython_connects_the_pipes_takes_the_child_and_its_tree``).

Every death is read by pid (:mod:`tests.process_probe`), never by
``returncode`` alone: ``returncode`` is the root, and the tree is the point.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import gc
import os
import signal
import sys
import textwrap
import time
import warnings
from pathlib import Path
from typing import Any, cast

import pytest
from aelix_ai.utils import _process_identity, _process_tree
from aelix_ai.utils._process_tree import ProcessTree, _Win32Api, kill_process_tree
from aelix_coding_agent.rpc import rpc_client as rpc_client_module
from aelix_coding_agent.rpc.rpc_client import (
    RpcClient,
    RpcClientError,
    RpcClientOptions,
    RpcServerExited,
)

from tests.event_waits import wait_until, within
from tests.process_probe import STATE_GONE, STATE_ZOMBIE, await_dead_or_zombie

_DEAD = (STATE_GONE, STATE_ZOMBIE)

#: Writes its own pid and a grandchild's, atomically, then idles with its stdin
#: unread — a child that never takes the stdin-EOF hint, the shape that
#: outlived the parent on the base. The grandchild is a ``Popen`` (not
#: ``os.fork``, which win32 lacks) and does NOT ``setsid``: it is in the child's
#: group on POSIX and in its job on win32, which is what the abandon reaches.
#: A second argument ``exit`` makes the root exit 7 instead, its grandchild left.
_TREE_CHILD = textwrap.dedent(
    """
    import os, subprocess, sys, time
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    tmp = sys.argv[1] + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"{os.getpid()} {grandchild.pid}\\n")
    os.replace(tmp, sys.argv[1])
    if sys.argv[2:] == ["exit"]:
        sys.exit(7)
    time.sleep(300)
    """
)

#: :data:`_TREE_CHILD` deaf to ``SIGTERM``, and its grandchild with it (an
#: ignored signal stays ignored across ``Popen``): only a hard kill ends either.
_TERM_DEAF_TREE_CHILD = (
    "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n" + _TREE_CHILD
)

#: A child with nothing under it, for the product's own 100 ms grace.
_IDLE_CHILD = "import time; time.sleep(300)"

#: Answers every command it reads, so a command that reaches it succeeds.
_ECHO_CHILD = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        request = json.loads(line)
        print(json.dumps({"type": "response", "id": request["id"],
                          "command": request["type"], "success": True, "data": {}}), flush=True)
    """
)


class _LongGrace(RpcClient):
    """A grace nothing but the child's death or a cancel can end."""

    STARTUP_GRACE_MS = 600_000


class _CancelInTheGrace(RpcClient):
    """Cancels :attr:`target` from the exit watcher's first step.

    ``start`` creates the watcher task as the last thing before the grace's
    ``wait_for``, so the watcher's first step runs while ``start`` is suspended
    in the grace, on 3.11 (where ``wait_for`` wraps the wait in a task) as on
    3.12+.
    """

    def __init__(self, options: RpcClientOptions) -> None:
        super().__init__(options)
        self.target: asyncio.Task[Any] | None = None
        self.pid: int | None = None
        self.fired = False

    async def _watch_for_exit(self) -> None:
        if not self.fired:
            self.fired = True
            assert self.process is not None
            self.pid = self.process.pid
            assert self.target is not None
            self.target.cancel()
        await super()._watch_for_exit()


class _DeafWatcher(_LongGrace):
    """No exit watcher, so the grace cannot end on the child's death."""

    async def _watch_for_exit(self) -> None:
        await asyncio.Event().wait()


def _pids(path: Path) -> list[int] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    parts = text.split()
    if not text.endswith("\n") or len(parts) != 2:
        return None
    return [int(part) for part in parts]


def _really_kill(pid: int) -> None:
    """Best effort. ``SIGTERM`` is ``TerminateProcess`` on win32."""

    with contextlib.suppress(OSError):
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


async def _states(pids: list[int]) -> list[str]:
    return [await await_dead_or_zombie(pid, timeout=5.0) for pid in pids]


def _tree_argv(pid_file: Path, *extra: str, child: str = _TREE_CHILD) -> list[str]:
    return [sys.executable, "-c", child, str(pid_file), *extra]


def _transport_closed(proc: asyncio.subprocess.Process) -> bool:
    transport = getattr(proc, "_transport", None)
    return transport is not None and transport.is_closing()


def _tasks(client: RpcClient) -> tuple[Any, Any, Any]:
    return (client._stdout_reader_task, client._stderr_reader_task, client._exit_watcher_task)


def _released(client: RpcClient) -> bool:
    return client.process is None and client.tree is None and _tasks(client) == (None, None, None)


async def _start_with_its_tree(
    tmp_path: Path, cls: type[RpcClient] = _LongGrace, *extra: str, child: str = _TREE_CHILD
) -> tuple[RpcClient, asyncio.Task[None], list[int]]:
    pid_file = tmp_path / "tree.pids"
    client = cls(RpcClientOptions(argv=_tree_argv(pid_file, *extra, child=child)))
    start = asyncio.ensure_future(client.start())
    await wait_until(lambda: _pids(pid_file) is not None, what="the child to write its tree's pids")
    pids = _pids(pid_file)
    assert pids is not None
    assert not start.done(), "start() returned before the cancel: the grace is not what was hit"
    return client, start, pids


def _job_members(tree: ProcessTree) -> str:
    """#772: what a win32 tree's job holds now, as text for a failure's message.

    ``JobObjectBasicProcessIdList`` lists the members of the job and of every
    job nested in it. A pid this test read from the child that is missing here
    was never in the job, so no ``TerminateJobObject`` reaches it. It only
    reports: the verdict stays :mod:`tests.process_probe`'s.
    """

    if sys.platform != "win32":
        return "no job off win32"
    job = tree.job
    if job is None:
        return f"no job (contained={tree.contained})"
    try:
        return f"job members {sorted(_process_tree._KernelApi().job_member_pids(job))}"
    except OSError as exc:
        return f"job members unreadable: {exc}"


#: ``PROCESS_QUERY_LIMITED_INFORMATION``, and the exit code a running process reports.
_QUERY_LIMITED = 0x1000
_STILL_ACTIVE = 259


def _win32_kernel32() -> Any:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    k32.QueryFullProcessImageNameW.restype = ctypes.c_int
    k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    k32.GetExitCodeProcess.restype = ctypes.c_int
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.restype = ctypes.c_int
    return k32


def _describe(pid: int) -> str:
    """#772 diagnostic, win32: the image (last two path parts) and exit state of whatever holds ``pid`` now."""

    if sys.platform != "win32":
        return "-"
    k32 = _win32_kernel32()
    handle = k32.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        return f"no process (OpenProcess error {ctypes.get_last_error()})"  # pyright: ignore[reportAttributeAccessIssue]
    try:
        size = ctypes.c_uint32(1024)
        buf = ctypes.create_unicode_buffer(1024)
        ok = k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        image = "\\".join(buf.value.split("\\")[-2:]) if ok else "image ?"
        code = ctypes.c_uint32()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return f"{image} exit ?"
        state = "running" if code.value == _STILL_ACTIVE else f"exited {code.value}"
        return f"{image} {state} started {_process_identity.process_started(pid)}"
    finally:
        k32.CloseHandle(handle)


def _in_job_now(pid: int, tree: ProcessTree) -> str:
    job = tree.job
    if sys.platform != "win32" or job is None:
        return "job closed"
    api = _process_tree._KernelApi()
    try:
        handle = api.open_member(pid)
    except OSError as exc:
        return f"unopenable ({exc})"
    try:
        return "in job" if api.in_job(handle, job) else "NOT in job"
    except OSError as exc:
        return f"IsProcessInJob failed ({exc})"
    finally:
        api.close_process(handle)


def _described_members(tree: ProcessTree) -> str:
    """The job's members at this moment, each with :func:`_describe`'s image, state and creation time."""

    job = tree.job
    if sys.platform != "win32" or job is None:
        return _job_members(tree)
    try:
        members = sorted(_process_tree._KernelApi().job_member_pids(job))
    except OSError as exc:
        return f"job members unreadable: {exc}"
    return "job members [" + "; ".join(f"{pid} {_describe(pid)}" for pid in members) + "]"


def _same_or_reused(pid: int, born: float | None) -> str:
    """Whether ``pid`` is still the process created at ``born`` (its creation time at the pid file)."""

    now = _process_identity.process_started(pid)
    if born is None or now is None:
        return f"creation time at the pid file {born}, now {now}: cannot tell"
    if now == born:
        return f"creation time {born} unchanged: the same process"
    return f"creation time {born} at the pid file, {now} now: the pid was REUSED"


class _JobWitness:
    """#772 diagnostic: what each win32 kill met, kept as notes and warned even when the test passes.

    Before every ``hard_kill``: the job's members and ``ActiveProcesses``; the
    ``taskkill`` it ran and how long; whether ``TerminateJobObject`` raised;
    ``ActiveProcesses`` right after. Off win32 it records the kill only.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.seen: list[str] = []
        #: Each pid's creation time as read at the pid file.
        self.born: dict[int, float | None] = {}
        real = ProcessTree.hard_kill

        def _witnessed(tree: ProcessTree) -> None:
            before = f"{_described_members(tree)} active={tree.active_processes()}"
            began = time.monotonic()
            real(tree)
            self.seen.append(
                f"hard_kill {time.monotonic() - began:.3f}s, before: {before}, "
                f"after: active={tree.active_processes()}"
            )

        monkeypatch.setattr(ProcessTree, "hard_kill", _witnessed)
        if sys.platform != "win32":
            return
        real_taskkill = _process_tree._KernelApi.taskkill
        real_terminate = _process_tree._KernelApi.terminate_job

        def _taskkill(api: Any, pid: int) -> None:
            began = time.monotonic()
            try:
                real_taskkill(api, pid)
            finally:
                self.seen.append(f"taskkill {pid} {time.monotonic() - began:.3f}s")

        def _terminate(api: Any, job: int) -> None:
            try:
                real_terminate(api, job)
            except OSError as exc:
                self.seen.append(f"TerminateJobObject raised {exc}")
                raise
            self.seen.append("TerminateJobObject ok")

        monkeypatch.setattr(_process_tree._KernelApi, "taskkill", _taskkill)
        monkeypatch.setattr(_process_tree._KernelApi, "terminate_job", _terminate)

    def snapshot(self, client: RpcClient, pids: list[int]) -> None:
        """At the pid file: each process's image and state, and whether the job holds it."""

        self.born.update((pid, _process_identity.process_started(pid)) for pid in pids)
        if sys.platform != "win32":
            return
        proc, tree = client.process, client.tree
        root = proc.pid if proc is not None else None
        held = [f"root {root}: {_describe(root)}"] if root is not None else []
        for pid in pids:
            member = _in_job_now(pid, tree) if tree is not None else "no tree"
            held.append(f"{pid}: {_describe(pid)}, {member}")
        self.seen.append("at the pid file: " + "; ".join(held))


async def _assert_the_tree_died(pids: list[int], root: int, witness: _JobWitness) -> None:
    """:func:`_states`' 5 s poll per pid; on a survivor, 10 s more of it and what the job held.

    #772 (windows-latest py3.13, 5 of 12 runs): the grandchild still ``alive``
    after that 5 s poll. The extra poll says whether it died late or never; the
    witness says whether it was in the job when the abandon killed it. On win32
    the witness's notes are warned on success too (the diagnostic round).
    """

    at_raise = [f"{pid}: {_describe(pid)}" for pid in pids]
    states = await _states(pids)
    if all(state in _DEAD for state in states):
        if sys.platform == "win32":
            warnings.warn(
                f"#772 evidence, passed: {(pids, states)} root {root}; {witness.seen}; "
                f"at the raise: {at_raise}",
                stacklevel=1,
            )
        return
    loop = asyncio.get_running_loop()
    started = loop.time()
    later: list[str] = []
    for pid, state in zip(pids, states, strict=True):
        if state in _DEAD:
            continue
        state = await await_dead_or_zombie(pid, timeout=10.0)
        took = loop.time() - started
        later.append(
            f"{pid} {state} {took:.2f}s on, now {_describe(pid)}, "
            f"{_same_or_reused(pid, witness.born.get(pid))}"
        )
    pytest.fail(
        f"{(pids, states)}: root {root}; {witness.seen or ['no hard_kill']}; "
        f"at the raise: {at_raise}; after a further poll: {later}"
    )


async def test_a_cancel_inside_the_grace_takes_the_child_and_its_tree(tmp_path: Path) -> None:
    """Red on the base: both pids alive after the ``CancelledError``."""

    client, start, pids = await _start_with_its_tree(tmp_path)
    proc = client.process
    assert proc is not None
    try:
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the cancelled start()")

        states = await _states(pids)
        assert all(state in _DEAD for state in states), (
            f"the child and its grandchild {pids} outlived a cancel inside start()'s "
            f"grace: {states}"
        )
        # Released, so the caller is not handed a dead child to stop later and
        # the client can be started again — the transport included, or asyncio
        # prints "Event loop is closed" for it at interpreter exit.
        assert _released(client)
        assert _transport_closed(proc)
    finally:
        for pid in pids:
            _really_kill(pid)


async def test_a_cancel_inside_the_products_own_grace_takes_the_child() -> None:
    """The 100 ms window as shipped, the cancel landing in it with no clock."""

    client = _CancelInTheGrace(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    assert client.STARTUP_GRACE_MS == 100
    start = asyncio.ensure_future(client.start())
    client.target = start
    try:
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the cancelled start()")

        assert client.fired, "the seam never ran: the cancel did not land in the grace"
        assert client.pid is not None
        state = await await_dead_or_zombie(client.pid, timeout=5.0)
        assert state in _DEAD, f"the child {client.pid} outlived the cancelled start(): {state}"
        assert client.process is None
    finally:
        if client.pid is not None:
            _really_kill(client.pid)


async def test_a_second_cancel_while_the_start_is_abandoned_still_kills_and_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kill goes out before the abandon's first ``await``; the release is a ``finally``.

    The second cancel is sent from inside the abandon's wait for the exit, the
    one ``await`` between the kill and the release.
    """

    client, start, pids = await _start_with_its_tree(tmp_path)
    original = client.wait_for_exit
    seen: list[str] = []

    async def _cancelled_again(timeout: float | None = None) -> int | None:
        seen.append("second cancel")
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        return await original(timeout)

    monkeypatch.setattr(client, "wait_for_exit", _cancelled_again)
    try:
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the twice-cancelled start()")

        assert seen == ["second cancel"], "the abandon's wait was never reached"
        states = await _states(pids)
        assert all(state in _DEAD for state in states), (pids, states)
        assert _released(client), "the second cancel skipped the release"
    finally:
        for pid in pids:
            _really_kill(pid)


async def test_a_cancel_on_every_turn_of_the_abandon_cannot_skip_the_kill(tmp_path: Path) -> None:
    """The kill is sent before the abandon first suspends (verify S2).

    A cancel is sent on each of the next loop turns after the first, so
    whichever turn the abandon first suspends in, one lands there: on 3.12+ the
    very next one, on 3.11 one later, since ``wait_for`` itself suspends while
    it cancels its inner wait. An ``await`` placed before the kill turns this
    red with both pids alive; nothing else here does.
    """

    client, start, pids = await _start_with_its_tree(tmp_path)

    async def _cancel_every_turn() -> int:
        sent = 0
        for _ in range(40):
            if start.done():
                break
            start.cancel()
            sent += 1
            await asyncio.sleep(0)
        return sent

    try:
        start.cancel()
        storm = asyncio.ensure_future(_cancel_every_turn())
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the start() cancelled on every turn")
        assert await storm >= 1

        states = await _states(pids)
        assert all(state in _DEAD for state in states), (pids, states)
        assert _released(client)
    finally:
        for pid in pids:
            _really_kill(pid)


def _hold_the_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[asyncio.subprocess.Process], asyncio.Event]:
    """Hold ``create_subprocess_exec`` open after its fork until the gate is set.

    A spawn ``start`` is still waiting on — the child forked, the call not yet
    returned to it — without depending on how many turns that takes. CPython's
    own pipe connection is over by then; the test that holds that step is
    ``test_a_cancel_while_cpython_connects_the_pipes_takes_the_child_and_its_tree``.
    Unshielded, the cancel is thrown into this hold and the process it already
    has is never handed back.
    """

    real_spawn = asyncio.create_subprocess_exec
    forked: list[asyncio.subprocess.Process] = []
    gate = asyncio.Event()

    async def _held(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        proc = await real_spawn(*args, **kwargs)
        forked.append(proc)
        await gate.wait()
        return proc

    monkeypatch.setattr(rpc_client_module.asyncio, "create_subprocess_exec", _held)
    return forked, gate


async def test_a_cancel_inside_the_spawn_takes_the_child_and_its_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review 2: the spawn is shielded, waited out, and what it forked ended.

    Red on the base: the cancel ended ``start`` with the child and its
    grandchild alive and no process on the client, since the spawn never
    returned one. A second cancel during that wait is absorbed — ``start``
    raises its cancel once the child is gone.
    """

    pid_file = tmp_path / "tree.pids"
    forked, gate = _hold_the_spawn(monkeypatch)
    client = _LongGrace(RpcClientOptions(argv=_tree_argv(pid_file)))
    start = asyncio.ensure_future(client.start())
    pids: list[int] = []
    try:
        await wait_until(lambda: forked and _pids(pid_file) is not None, what="the fork and its tree")
        pids = _pids(pid_file) or []
        start.cancel()
        await asyncio.sleep(0)
        start.cancel()
        await asyncio.sleep(0)
        assert not start.done(), "start() ended before the spawn it was cancelled in"
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the start() cancelled inside its spawn")

        states = await _states(pids)
        assert all(state in _DEAD for state in states), (
            f"the child and its grandchild {pids} outlived a cancel inside the spawn: {states}"
        )
        assert _released(client)
        assert _transport_closed(forked[0])
    finally:
        gate.set()
        for pid in pids:
            _really_kill(pid)


async def test_a_spawn_that_outlives_the_bound_is_ended_when_it_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound on that wait, and what takes over at it.

    ``start`` stops waiting at :attr:`RpcClient.SPAWN_SETTLE_SECONDS` and
    raises with the spawn still unfinished — the one exit where the child
    outlives ``start``, until the spawn lands. It is then ended by the
    spawn's own done-callback. Red on the base: nothing ever ends it.
    """

    class _ShortSettle(_LongGrace):
        SPAWN_SETTLE_SECONDS = 0.05

    pid_file = tmp_path / "tree.pids"
    forked, gate = _hold_the_spawn(monkeypatch)
    client = _ShortSettle(RpcClientOptions(argv=_tree_argv(pid_file)))
    start = asyncio.ensure_future(client.start())
    pids: list[int] = []
    try:
        await wait_until(lambda: forked and _pids(pid_file) is not None, what="the fork and its tree")
        pids = _pids(pid_file) or []
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the start() whose spawn outlived the bound")
        assert not gate.is_set()

        gate.set()
        states = await _states(pids)
        assert all(state in _DEAD for state in states), (
            f"the late spawn's child and grandchild {pids} were never ended: {states}"
        )
        await wait_until(lambda: _transport_closed(forked[0]), what="the late spawn's transport")
        assert _released(client)
    finally:
        gate.set()
        for pid in pids:
            _really_kill(pid)


async def test_a_late_spawn_whose_root_was_already_reaped_is_sent_no_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify round 2, S-A: the done-callback's pid-only kill keeps the guard.

    The spawn outlives the bound and its child exits, and is reaped, before the
    spawn lands, so by then the pid can be another process's. Red without the
    guard: the callback still handed that pid to ``kill_process_tree``.
    """

    class _ShortSettle(_LongGrace):
        SPAWN_SETTLE_SECONDS = 0.05

    pid_kills: list[int] = []
    # Recorded, never sent: when red, the pid is already free.
    monkeypatch.setattr(rpc_client_module, "kill_process_tree", pid_kills.append)
    forked, gate = _hold_the_spawn(monkeypatch)
    client = _ShortSettle(RpcClientOptions(argv=[sys.executable, "-c", "raise SystemExit(0)"]))
    start = asyncio.ensure_future(client.start())
    try:
        await wait_until(lambda: forked, what="the fork")
        (spawn,) = [
            task for task in asyncio.all_tasks() if getattr(task.get_coro(), "__name__", "") == "_held"
        ]
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the start() whose spawn outlived the bound")
        late = forked[0]
        await wait_until(lambda: late.returncode is not None, what="the late spawn's root reaped")

        # Added after the bound's, so it runs once the product's callback has:
        # the transport alone cannot say so, since the protocol closes it as
        # soon as the child and its pipes are gone.
        landed = asyncio.Event()
        spawn.add_done_callback(lambda _spawn: landed.set())
        gate.set()
        await within(landed.wait(), bound=20.0, what="the late spawn's done-callbacks")
        assert pid_kills == []
        assert _transport_closed(late)
        assert _released(client)
    finally:
        gate.set()


async def test_a_cancel_while_cpython_connects_the_pipes_takes_the_child_and_its_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review 2's own seam, inside ``create_subprocess_exec`` (verify round 2 nit 4).

    :func:`_hold_the_spawn` holds the call once it has returned; this holds it
    where CPython is still connecting the child's stdout, the step in which an
    unshielded cancel runs CPython's cleanup (``Popen.kill``, the root only).
    Red on the base: ``start`` did not end, waiting in that cleanup for pipes the
    grandchild still held.
    """

    loop = asyncio.get_running_loop()
    real_connect = loop.connect_read_pipe
    gate = asyncio.Event()
    connecting: list[bool] = []

    async def _held_connect(*args: Any, **kwargs: Any) -> Any:
        connecting.append(True)
        await gate.wait()
        return await real_connect(*args, **kwargs)

    monkeypatch.setattr(loop, "connect_read_pipe", _held_connect)
    pid_file = tmp_path / "tree.pids"
    client = _LongGrace(RpcClientOptions(argv=_tree_argv(pid_file)))
    start = asyncio.ensure_future(client.start())
    pids: list[int] = []
    try:
        await wait_until(
            lambda: connecting and _pids(pid_file) is not None,
            what="the pipe connection and the tree",
        )
        pids = _pids(pid_file) or []
        start.cancel()
        await asyncio.sleep(0)
        assert not start.done(), "start() ended before the spawn it was cancelled in"
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the start() cancelled while its pipes connected")

        states = await _states(pids)
        assert all(state in _DEAD for state in states), (
            f"the child and its grandchild {pids} outlived a cancel in the pipe connection: "
            f"{states}"
        )
        assert _released(client)
    finally:
        gate.set()
        for pid in pids:
            _really_kill(pid)


async def test_a_cancel_inside_a_spawn_that_then_fails_stays_a_cancel(tmp_path: Path) -> None:
    """The spawn's own error does not replace the cancel it was waited out for.

    The cancel lands while ``start`` waits on the shielded spawn, before the
    spawn's first step, and that step then fails to execute the program
    (verify round 2 nit 1, N18). Red when the settle re-raises the spawn's
    result: ``start`` raised ``FileNotFoundError``. On the base the spawn was
    not a task, so it had already failed before the cancel could be sent.
    """

    client = RpcClient(RpcClientOptions(argv=[str(tmp_path / "no-such-program")]))
    start = asyncio.ensure_future(client.start())
    await asyncio.sleep(0)
    assert not start.done(), "start() did not reach its wait on the spawn"
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await within(start, bound=20.0, what="the cancelled start() whose spawn failed")
    assert _released(client)
    assert not client._starting


async def test_a_late_attach_that_raises_is_reported_and_its_child_still_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The attach after a cancelled spawn, refused (verify round 2 nit 1, N5).

    It takes the pid-only kill, as a refused attach on the normal path does,
    and the refusal rides on the cancel as a note and reaches the loop's
    exception handler. Red without the report: nothing said the child was
    ended without a tree.
    """

    def _refuse(pid: int, **_kwargs: Any) -> ProcessTree:
        raise OSError("late attach refused")

    loop = asyncio.get_running_loop()
    reported: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    forked, gate = _hold_the_spawn(monkeypatch)
    monkeypatch.setattr(rpc_client_module.ProcessTree, "attach", _refuse)
    client = _LongGrace(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    start = asyncio.ensure_future(client.start())
    try:
        await wait_until(lambda: forked, what="the fork")
        start.cancel()
        await asyncio.sleep(0)
        gate.set()
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await within(start, bound=20.0, what="the start() whose late attach raised")

        notes = getattr(excinfo.value, "__notes__", [])
        assert any("late attach refused" in note for note in notes), notes
        assert [str(context.get("exception")) for context in reported] == ["late attach refused"]
        assert await await_dead_or_zombie(forked[0].pid, timeout=5.0) in _DEAD
        assert _released(client)
    finally:
        loop.set_exception_handler(previous)
        gate.set()
        for proc in forked:
            _really_kill(proc.pid)


async def test_an_attach_that_raises_takes_the_child_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not only a cancel: anything that escapes after the spawn.

    ``ProcessTree.attach`` is the first statement after the spawn. It cannot
    raise on POSIX, and on win32 only if ``kernel32`` cannot be loaded, but it
    is the one call there with no tree behind it yet, so it takes the pid-only
    kill. Red on the base: the error escaped and the child ran on.
    """

    spawned: list[int] = []
    pid_kills: list[int] = []

    def _refuse(pid: int, **_kwargs: Any) -> ProcessTree:
        spawned.append(pid)
        raise OSError("attach refused")

    def _pid_kill(pid: int) -> None:
        pid_kills.append(pid)
        kill_process_tree(pid)

    monkeypatch.setattr(rpc_client_module.ProcessTree, "attach", _refuse)
    monkeypatch.setattr(rpc_client_module, "kill_process_tree", _pid_kill)
    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    try:
        with pytest.raises(OSError, match="attach refused"):
            await within(client.start(), bound=20.0, what="the start() whose attach raised")

        (pid,) = spawned
        state = await await_dead_or_zombie(pid, timeout=5.0)
        assert state in _DEAD, f"the child {pid} outlived a start() whose attach raised: {state}"
        # The kill was the client's, not asyncio's: closing the transport also
        # ends the root (``Popen.kill``), but only the root and only after the
        # 5 s wait.
        assert pid_kills == [pid]
        assert client.process is None
    finally:
        for pid in spawned:
            kill_process_tree(pid)


async def test_a_root_already_dead_when_the_cancel_lands_still_has_its_group_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review 1: the root exited in the grace, its grandchild did not.

    The exit watcher is held so the grace cannot end on the death, and the
    cancel is sent once ``returncode`` is set. Red on the base: the abandon
    read the root's status, skipped the kill, and the grandchild ran on in the
    root's group with PPID 1.
    """

    witness = _JobWitness(monkeypatch)
    client, start, pids = await _start_with_its_tree(tmp_path, _DeafWatcher, "exit")
    witness.snapshot(client, pids)
    proc = client.process
    assert proc is not None
    try:
        await wait_until(lambda: client.returncode == 7, what="the root's own exit status")
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the cancelled start()")

        await _assert_the_tree_died(pids, proc.pid, witness)
        assert _released(client)
        assert _transport_closed(proc)
    finally:
        for pid in pids:
            _really_kill(pid)


async def test_a_premature_exit_ends_the_group_and_closes_the_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review 1 and 7, verify nit 2: the ``RpcServerExited`` path, no cancel.

    Red on the base twice over: the grandchild ran on, and the transport stayed
    open (``ResourceWarning: unclosed transport`` and "Event loop is closed" at
    teardown, macOS and Linux).
    """

    witness = _JobWitness(monkeypatch)
    client, start, pids = await _start_with_its_tree(tmp_path, _LongGrace, "exit")
    witness.snapshot(client, pids)
    proc = client.process
    assert proc is not None
    try:
        with pytest.raises(RpcServerExited, match="code 7") as excinfo:
            await within(start, bound=20.0, what="the start() whose child exited at once")
        assert excinfo.value.returncode == 7

        await _assert_the_tree_died(pids, proc.pid, witness)
        assert _released(client)
        assert _transport_closed(proc)
    finally:
        for pid in pids:
            _really_kill(pid)


#: Premature exits per run of the #772 evidence case.
_EVIDENCE_RUNS = 20


@pytest.mark.skipif(sys.platform != "win32", reason="#772 diagnostic: a real Windows Job")
async def test_on_win32_772_evidence_of_repeated_premature_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#772 diagnostic round 2: the premature-exit case, repeated, reported whether or not it fails.

    Even runs hold a handle to the grandchild from the pid file on, so its pid
    cannot be reused and every reading is of that process; odd runs hold none,
    as the case itself does. A survivor does not fail this case: the warning is
    the result. Only a grandchild still the process the pid file named is killed
    at the end of a run.
    """

    witness = _JobWitness(monkeypatch)
    api = _process_tree._KernelApi()
    loop = asyncio.get_running_loop()
    rows: list[str] = []
    late = 0
    for run in range(_EVIDENCE_RUNS):
        witness.seen.clear()
        run_dir = tmp_path / f"run-{run}"
        run_dir.mkdir()
        # Not ``_start_with_its_tree``: its "start() has not returned yet" check
        # is the case's premise, and here a run must not end the evidence.
        pid_file = run_dir / "tree.pids"
        client = _LongGrace(RpcClientOptions(argv=_tree_argv(pid_file, "exit")))
        start = asyncio.ensure_future(client.start())
        await wait_until(lambda f=pid_file: _pids(f) is not None, what="the child's tree")
        pids = _pids(pid_file) or []
        grandchild = pids[1]
        stamp = _process_identity.process_started(grandchild)
        witness.snapshot(client, pids)
        held: int | None = None
        if run % 2 == 0:
            with contextlib.suppress(OSError):
                held = api.open_member(grandchild)
        try:
            with pytest.raises(RpcServerExited):
                await within(start, bound=20.0, what="the start() whose child exited at once")
            raised = loop.time()
            at_raise = _describe(grandchild)
            state = await await_dead_or_zombie(grandchild, timeout=10.0)
            took = loop.time() - raised
            if state not in _DEAD or took > 5.0:
                late += 1
            rows.append(
                f"run {run} held={held is not None} {pids}: grandchild {state} after {took:.2f}s, "
                f"at the raise {at_raise}, now {_describe(grandchild)}, "
                f"{_same_or_reused(grandchild, stamp)}; {witness.seen}"
            )
        finally:
            if held is not None or _process_identity.same_process(grandchild, stamp) is True:
                kill_process_tree(grandchild)
            if held is not None:
                api.close_process(held)
    warnings.warn(
        f"#772 evidence: {late} of {_EVIDENCE_RUNS} grandchildren alive past 5 s | " + " | ".join(rows),
        stacklevel=1,
    )


async def test_a_cancel_swallowed_while_a_failed_start_is_abandoned_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify nit 7: the abandon of a NON-cancel failure must not lose a cancel.

    ``_teardown_tasks`` joins with ``suppress(BaseException)`` (#357's shape),
    so a cancel landing there on the premature-exit path was eaten and
    ``start`` raised ``RpcServerExited`` as if nothing had happened. The cancel
    is sent from the teardown's first step. Red on the base for that reason.
    """

    client = _LongGrace(RpcClientOptions(argv=[sys.executable, "-c", "raise SystemExit(3)"]))
    original = client._teardown_tasks

    async def _cancelled_in_teardown() -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        await original()

    monkeypatch.setattr(client, "_teardown_tasks", _cancelled_in_teardown)
    start = asyncio.ensure_future(client.start())
    with pytest.raises(asyncio.CancelledError):
        await within(start, bound=20.0, what="the start() cancelled in its abandon")
    assert _released(client)


async def test_a_kill_that_raises_still_releases_and_keeps_both_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review 8: a kill refused with something other than ``OSError``.

    The cancel stays what ``start`` raises, the refusal rides on it as a note
    and reaches the loop's exception handler, and the release still runs, with
    no 5 s wait for an exit nothing was sent to cause — closing the transport
    ends the root through asyncio's own ``Popen.kill``.
    Red on the base: the ``RuntimeError`` replaced the cancel, and the child,
    the transport and all three tasks were left.
    """

    def _refused(self: ProcessTree) -> None:
        raise RuntimeError("kill refused by the host")

    monkeypatch.setattr(ProcessTree, "hard_kill", _refused)
    loop = asyncio.get_running_loop()
    reported: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    client = _CancelInTheGrace(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    waits: list[float | None] = []
    original = client.wait_for_exit

    async def _recorded(timeout: float | None = None) -> int | None:
        waits.append(timeout)
        return await original(timeout)

    monkeypatch.setattr(client, "wait_for_exit", _recorded)
    start = asyncio.ensure_future(client.start())
    client.target = start
    try:
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await within(start, bound=20.0, what="the start() whose kill raised")

        assert any("kill refused by the host" in note for note in excinfo.value.__notes__)
        assert [str(context.get("exception")) for context in reported] == ["kill refused by the host"]
        assert _released(client)
        # Nothing was sent, so nothing was waited for: the release followed at once.
        assert waits == []
        assert client.pid is not None
        assert await await_dead_or_zombie(client.pid, timeout=5.0) in _DEAD
    finally:
        loop.set_exception_handler(previous)
        if client.pid is not None:
            _really_kill(client.pid)


@pytest.mark.parametrize("refused", ["_watch_for_exit", "create_subprocess_exec"])
async def test_a_refused_task_leaks_no_coroutine(refused: str) -> None:
    """Codex review 10: a host task factory refusing one of ``start``'s tasks.

    The coroutine handed to ``create_task`` is nobody's once the factory
    raises; red on the base with "coroutine 'RpcClient._watch_for_exit' was
    never awaited" at its collection. The child is ended either way, and a
    refused spawn forks nothing and leaves the client startable. Not a
    ``RuntimeError``: ``asyncio.ensure_future`` closes the coroutine itself for
    that one type only.
    """

    class _Refused(Exception):
        pass

    loop = asyncio.get_running_loop()
    previous = loop.get_task_factory()
    spawned: list[int] = []

    def _factory(loop: asyncio.AbstractEventLoop, coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
        if getattr(coro, "__qualname__", "").endswith(refused):
            if client.process is not None:
                spawned.append(client.process.pid)
            raise _Refused("task factory refused")
        return asyncio.Task(coro, loop=loop, **kwargs)

    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    loop.set_task_factory(_factory)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(_Refused, match="task factory refused"):
                await client.start()
            gc.collect()
        assert [str(w.message) for w in caught if "never awaited" in str(w.message)] == []
        assert _released(client)
        assert not client._starting
        if refused == "_watch_for_exit":
            (pid,) = spawned
            assert await await_dead_or_zombie(pid, timeout=5.0) in _DEAD
        else:
            assert spawned == []
    finally:
        loop.set_task_factory(previous)
        if client.process is not None:
            # Only when red: a ``start`` that returned holds its child.
            spawned.append(client.process.pid)
        for pid in spawned:
            _really_kill(pid)


async def test_two_concurrent_starts_spawn_one_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex review 3: the second ``start`` in the same turn is refused.

    Red on the base: both passed the ``_proc is None`` check before either had
    spawned, both returned, and one ``stop`` left the other child alive with
    its reader tasks.
    """

    real_spawn = asyncio.create_subprocess_exec
    spawned: list[asyncio.subprocess.Process] = []

    async def _recorded(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        proc = await real_spawn(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(rpc_client_module.asyncio, "create_subprocess_exec", _recorded)
    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    try:
        first, second = await asyncio.gather(client.start(), client.start(), return_exceptions=True)
        assert first is None
        assert isinstance(second, RuntimeError) and "already started" in str(second)
        assert len(spawned) == 1
        await client.stop()
        assert await await_dead_or_zombie(spawned[0].pid, timeout=5.0) in _DEAD
        assert _released(client)
    finally:
        for proc in spawned:
            _really_kill(proc.pid)


async def test_a_command_sent_before_start_returned_is_refused_and_never_written() -> None:
    """``_send`` refuses every command until ``start`` returns (Codex review 3, verify S3).

    Until then the child is ``start``'s and may still be ended with whatever
    it was told. Red on the base: the child received the command inside the
    grace and answered it.
    """

    client = _LongGrace(RpcClientOptions(argv=[sys.executable, "-c", _ECHO_CHILD]))
    start = asyncio.ensure_future(client.start())
    await wait_until(lambda: client._exit_watcher_task is not None, what="start() in its grace")
    proc = client.process
    assert proc is not None
    try:
        with pytest.raises(RuntimeError, match=r"start\(\) has not returned"):
            await within(client.get_state(), bound=20.0, what="a command sent inside the grace")
        assert client._pending_requests == {}
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the cancelled start()")
    finally:
        _really_kill(proc.pid)


async def test_a_stop_during_start_ends_it_and_returns_once_it_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``stop`` while ``start`` runs is a request, and ``start`` ends the tree.

    The request sends the HARD kill: the child ignores ``SIGTERM``, so a soft
    one would leave it alive and ``start`` in its 600 s grace. And ``stop``
    returns only once ``start`` has ended: the abandon is held at its wait, and
    ``stop`` is still waiting there (verify round 2, S-B: neither was pinned).
    Red on the base, which has no abandon for ``start`` to reach: ``stop`` ran
    its own teardown of the child.
    """

    client, start, pids = await _start_with_its_tree(tmp_path, child=_TERM_DEAF_TREE_CHILD)
    original = client.wait_for_exit
    abandoning = asyncio.Event()
    gate = asyncio.Event()

    async def _held(timeout: float | None = None) -> int | None:
        abandoning.set()
        await gate.wait()
        return await original(timeout)

    monkeypatch.setattr(client, "wait_for_exit", _held)
    try:
        stopper = asyncio.ensure_future(client.stop())
        root_state = await await_dead_or_zombie(pids[0], timeout=5.0)
        assert root_state in _DEAD, f"a child deaf to SIGTERM outlived the stop() request: {root_state}"
        await within(abandoning.wait(), bound=20.0, what="start() abandoning on the stop's request")
        for _ in range(5):
            await asyncio.sleep(0)
        assert not stopper.done(), "stop() returned before the start() it asked to give up had ended"

        gate.set()
        with pytest.raises(RpcClientError, match=r"stopped before start\(\) returned"):
            await within(start, bound=20.0, what="the start() a stop() asked to give up")
        for _ in range(5):
            if stopper.done():
                break
            await asyncio.sleep(0)
        assert stopper.done(), "stop() outlived the start() it was waiting for"
        await stopper

        states = await _states(pids)
        assert all(state in _DEAD for state in states), (pids, states)
        assert _released(client)
    finally:
        gate.set()
        for pid in pids:
            _really_kill(pid)


async def test_a_stop_during_the_spawn_ends_the_child_without_waiting_out_the_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``stop`` that lands before there is a tree to kill (verify round 2, S-B).

    The request is all it can send; ``start`` reads it once the spawn lands,
    skips the grace (600 s here) and ends the tree. Red when the grace is
    waited anyway: the child and its grandchild ran on inside it.
    """

    pid_file = tmp_path / "tree.pids"
    forked, gate = _hold_the_spawn(monkeypatch)
    client = _LongGrace(RpcClientOptions(argv=_tree_argv(pid_file)))
    start = asyncio.ensure_future(client.start())
    pids: list[int] = []
    try:
        await wait_until(lambda: forked and _pids(pid_file) is not None, what="the fork and its tree")
        pids = _pids(pid_file) or []
        assert client.tree is None
        stopper = asyncio.ensure_future(client.stop())
        for _ in range(5):
            await asyncio.sleep(0)
        assert not stopper.done(), "stop() returned before the start() it asked to give up had ended"

        gate.set()
        states = await _states(pids)
        assert all(state in _DEAD for state in states), (
            f"the child and its grandchild {pids} outlived a stop() issued during the spawn: "
            f"{states}"
        )
        with pytest.raises(RpcClientError, match=r"stopped before start\(\) returned"):
            await within(start, bound=20.0, what="the start() a stop() asked to give up")
        await within(stopper, bound=20.0, what="the stop() issued during the spawn")
        assert _released(client)
        assert _transport_closed(forked[0])
    finally:
        gate.set()
        for pid in pids:
            _really_kill(pid)


async def test_a_stop_racing_a_twice_cancelled_start_leaves_a_restart_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify S1 and Codex review 4, the same race seen from two ends.

    ``start`` is cancelled, a ``stop`` is issued, a second cancel lands in the
    abandon's wait (before the exit watcher could see the death), and the
    client is started again at once. Red on the base: ``stop`` waited for an
    exit event nobody would set, sat out both its bounds (6.00 s), and then
    tore down the RESTARTED child's tasks and cleared its fields.
    """

    client = _LongGrace(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    start = asyncio.ensure_future(client.start())
    await wait_until(lambda: client._exit_watcher_task is not None, what="start() in its grace")
    first = client.process
    assert first is not None
    original = client.wait_for_exit
    armed = [True]

    async def _cancelled_again(timeout: float | None = None) -> int | None:
        if armed[0]:
            armed[0] = False
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
        return await original(timeout)

    monkeypatch.setattr(client, "wait_for_exit", _cancelled_again)
    stopper: asyncio.Future[None] | None = None
    second: asyncio.subprocess.Process | None = None
    try:
        start.cancel()
        stopper = asyncio.ensure_future(client.stop())
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the twice-cancelled start()")
        assert not armed[0], "the second cancel never landed in the abandon"

        client.STARTUP_GRACE_MS = 100
        await within(client.start(), bound=20.0, what="the restart")
        second = client.process
        assert second is not None and second is not first
        restarted_tasks = _tasks(client)
        for _ in range(5):
            if stopper.done():
                break
            await asyncio.sleep(0)
        assert stopper.done(), "stop() outlived the start() it was waiting for"
        await stopper

        assert client.process is second
        assert second.returncode is None
        assert not _transport_closed(second)
        assert _tasks(client) == restarted_tasks
        assert not any(task.done() for task in restarted_tasks)
        await client.stop()
        assert await await_dead_or_zombie(second.pid, timeout=5.0) in _DEAD
    finally:
        if stopper is not None:
            stopper.cancel()
        _really_kill(first.pid)
        if second is not None:
            _really_kill(second.pid)


async def test_a_late_release_of_a_stopped_child_leaves_the_restarted_one_alone() -> None:
    """``_release`` frees the child it is handed and no newer one (Codex review 4).

    Two releasers of one child can overlap — two ``stop`` calls — and the late
    one may run after a ``start`` has put the next child in place. Red on the
    base: it cancelled the new child's tasks and cleared its process and tree.
    """

    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    await client.start()
    first, first_tree = client.process, client.tree
    assert first is not None
    await client.stop()
    await client.start()
    second, second_tree = client.process, client.tree
    restarted_tasks = _tasks(client)
    assert second is not None
    try:
        await client._release(first, first_tree)

        assert client.process is second
        assert client.tree is second_tree
        assert _tasks(client) == restarted_tasks
        assert not any(task is None or task.done() for task in restarted_tasks)
        assert not _transport_closed(second)
    finally:
        await client.stop()
        _really_kill(second.pid)


async def test_a_teardown_suspended_across_a_restart_clears_only_the_tasks_it_joined() -> None:
    """The same rule one level down: ``_teardown_tasks`` suspends in its joins.

    White-box, because the shape is narrow — two overlapping releasers of one
    child, the first done and a ``start`` already in while the second still
    joins — so the fields are re-filled by hand at the join, the point at
    which a restart would have filled them.
    """

    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    await client.start()
    proc = client.process
    assert proc is not None
    newer: list[asyncio.Task[None]] = []
    try:
        teardown = asyncio.ensure_future(client._teardown_tasks())
        await asyncio.sleep(0)
        assert not teardown.done(), "the teardown did not suspend in its first join"
        newer = [asyncio.ensure_future(asyncio.sleep(60)) for _ in range(3)]
        client._stdout_reader_task, client._stderr_reader_task, client._exit_watcher_task = newer
        await within(teardown, bound=20.0, what="the suspended teardown")

        assert _tasks(client) == tuple(newer)
    finally:
        # Not ``stop()``: the real exit watcher is gone, so its waits would run
        # to their bounds. The status is awaited before the release so asyncio
        # has copied it into the ``Popen``: on Linux the loop could close
        # between the reap and that copy, and the ``Popen`` then warned
        # "subprocess N is still running" in a later test (verify round 2 nit 2).
        _really_kill(proc.pid)
        await wait_until(lambda: proc.returncode is not None, what="the child's exit status")
        await client._release(proc, client.tree)


async def test_a_command_blocked_on_a_full_pipe_gets_the_stops_reason() -> None:
    """Codex review 9: a ``stop`` fails the request but does not wake its ``drain``.

    The child reads nothing, so a 2 MB prompt blocks in ``drain``; ``stop``
    fails it with "RPC server stopped", and the kill then breaks the pipe.
    Red on the base: the caller got the broken pipe as ``RpcServerExited``,
    and asyncio logged the stop's error as "Future exception was never
    retrieved".
    """

    loop = asyncio.get_running_loop()
    reported: list[str] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(str(context.get("message"))))
    client = RpcClient(
        RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD], send_timeout_ms=600_000)
    )
    await client.start()
    proc = client.process
    assert proc is not None
    try:
        command = asyncio.ensure_future(client.prompt("x" * 2_000_000))
        await wait_until(lambda: client._pending_requests, what="the prompt to be in flight")
        await within(client.stop(), bound=20.0, what="stop() under a blocked write")
        with pytest.raises(RpcClientError, match="RPC server stopped"):
            await within(command, bound=20.0, what="the blocked prompt")
        del command
        gc.collect()
        await asyncio.sleep(0)
        assert [message for message in reported if "never retrieved" in message] == []
    finally:
        loop.set_exception_handler(previous)
        _really_kill(proc.pid)


async def test_a_command_cancelled_on_a_full_pipe_leaves_no_request_behind() -> None:
    """A command cancelled in ``drain`` is forgotten (verify round 2 nit 1, N12).

    Red when the write's failure leaves the request in the map, as the base did
    for anything but a broken pipe: the ``stop`` that follows fails that orphan
    with "RPC server stopped", nobody is left to read it, and asyncio logs
    "Future exception was never retrieved".
    """

    loop = asyncio.get_running_loop()
    reported: list[str] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(str(context.get("message"))))
    client = RpcClient(
        RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD], send_timeout_ms=600_000)
    )
    await client.start()
    proc = client.process
    assert proc is not None
    try:
        command = asyncio.ensure_future(client.prompt("x" * 2_000_000))
        await wait_until(lambda: client._pending_requests, what="the prompt to be in flight")
        command.cancel()
        with pytest.raises(asyncio.CancelledError):
            await within(command, bound=20.0, what="the cancelled prompt")
        assert client._pending_requests == {}

        await within(client.stop(), bound=20.0, what="stop() after the cancelled prompt")
        del command
        gc.collect()
        await asyncio.sleep(0)
        assert [message for message in reported if "never retrieved" in message] == []
    finally:
        loop.set_exception_handler(previous)
        _really_kill(proc.pid)


async def test_a_command_sent_after_stop_began_is_refused() -> None:
    """Once ``stop`` has failed every pending request, a new one is refused too.

    Red on the base: it was written to the dying child and came back as that
    child's exit.
    """

    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    await client.start()
    proc = client.process
    assert proc is not None
    stopper = asyncio.ensure_future(client.stop())
    try:
        await asyncio.sleep(0)
        assert not stopper.done()
        with pytest.raises(RpcClientError, match="RPC server stopped"):
            await within(client.get_state(), bound=20.0, what="a command sent during stop()")
        await within(stopper, bound=20.0, what="the stop()")
    finally:
        stopper.cancel()
        _really_kill(proc.pid)


class _KillingApi:
    """A win32 api that records, and makes ``taskkill`` really end the root.

    The real ``taskkill /T`` and ``TerminateJobObject`` cannot run off Windows,
    and the order of the calls is the claim: no ``ctrl_break`` (no soft stage),
    the tree's hard kill, then the release. ``assign_fails`` is the job the
    runner refused, which ``attach`` swallows into an UNCONTAINED tree.
    """

    JOB = 0x360

    def __init__(self, *, assign_fails: bool = False) -> None:
        self.calls: list[str] = []
        self.assign_fails = assign_fails

    def create_job(self, *, kill_on_close: bool) -> int:
        self.calls.append(f"create_job(kill_on_close={kill_on_close})")
        return self.JOB

    def assign(self, job: int, pid: int, handle: int | None) -> None:
        self.calls.append("assign")
        if self.assign_fails:
            raise OSError("assignment refused")

    def terminate_job(self, job: int) -> None:
        self.calls.append("terminate_job")

    def close_handle(self, job: int) -> None:
        self.calls.append("close_handle")

    def ctrl_break(self, pid: int) -> None:
        self.calls.append("ctrl_break")

    def taskkill(self, pid: int) -> None:
        self.calls.append("taskkill")
        _really_kill(pid)


@pytest.mark.parametrize(
    ("assign_fails", "expected"),
    [
        (False, ["create_job(kill_on_close=True)", "assign", "taskkill", "terminate_job", "close_handle"]),
        # The fallback ADR-0201 states as a limit: no job, so ``taskkill /T``
        # alone, which follows live parent links only.
        (True, ["create_job(kill_on_close=True)", "assign", "close_handle", "taskkill"]),
    ],
    ids=["job", "job-refused"],
)
async def test_on_win32_the_abandon_kills_through_the_job_with_no_soft_stage(
    monkeypatch: pytest.MonkeyPatch, assign_fails: bool, expected: list[str]
) -> None:
    """The win32 arm, injected: ``taskkill`` then the job, then ``CloseHandle``."""

    api = _KillingApi(assign_fails=assign_fails)
    real_attach = ProcessTree.attach

    def _win32_tree(pid: int, **kwargs: Any) -> ProcessTree:
        return real_attach(pid, **kwargs, platform="win32", api=cast(_Win32Api, api))

    monkeypatch.setattr(rpc_client_module.ProcessTree, "attach", _win32_tree)
    client = _CancelInTheGrace(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_CHILD]))
    start = asyncio.ensure_future(client.start())
    client.target = start
    try:
        with pytest.raises(asyncio.CancelledError):
            await within(start, bound=20.0, what="the cancelled start()")

        assert api.calls == expected
        assert client.pid is not None
        assert await await_dead_or_zombie(client.pid, timeout=5.0) in _DEAD
        assert client.process is None
    finally:
        if client.pid is not None:
            _really_kill(client.pid)


#: Spawns per run of the membership case: one escape in three spawns, the rate
#: #772's failures suggest, passes all eight about 4 times in 100.
_MEMBERSHIP_SPAWNS = 8


def _in_job(pid: int, job: int) -> bool:
    api = _process_tree._KernelApi()
    handle = api.open_member(pid)
    try:
        return api.in_job(handle, job)
    finally:
        api.close_process(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="a real Windows Job")
async def test_on_win32_what_the_child_starts_at_once_is_born_in_its_job(tmp_path: Path) -> None:
    """#772: the child's own child and grandchild are members of the job ``start`` attached.

    On the windows runner ``sys.executable`` is the venv launcher: the root
    starts the interpreter (the pid file's first pid) as soon as it runs, and
    membership of a job is inherited only by a process created after its
    parent was assigned. ``IsProcessInJob`` is asked while both live, the
    root's handle held by the transport, on each of :data:`_MEMBERSHIP_SPAWNS`
    spawns.
    """

    outside: list[tuple[int, int]] = []
    for spawn in range(_MEMBERSHIP_SPAWNS):
        pid_file = tmp_path / f"tree-{spawn}.pids"
        client = RpcClient(RpcClientOptions(argv=_tree_argv(pid_file)))
        await within(client.start(), bound=20.0, what="start()")
        pids: list[int] = []
        try:
            await wait_until(lambda f=pid_file: _pids(f) is not None, what="the child's tree")
            pids = _pids(pid_file) or []
            tree = client.tree
            assert tree is not None and tree.job is not None, "no job was attached"
            outside.extend((spawn, pid) for pid in pids if not _in_job(pid, tree.job))
        finally:
            await within(client.stop(), bound=20.0, what="stop()")
            for pid in pids:
                _really_kill(pid)
    assert outside == [], f"(spawn, pid) outside the job, of {_MEMBERSHIP_SPAWNS} spawns: {outside}"
