"""Dispatch and ORDER for ``run_contained`` (#221). Fake ``Popen``, real pipes.

This is the half that runs identically on every runner: the platform is
injected (``platform=``), every win32 side effect goes through the
:class:`_Win32Api` seam, and the child is a fake whose only real parts are two
``os.pipe()``s — so the drain, the idle timer and the caps are exercised against
real file descriptors while nothing is ever spawned or signalled.
``test_run_contained_real_processes.py`` is the other half, and every LATENCY
claim lives there; what is claimed here is dispatch, order, and the arithmetic
of the bounds.

NOTHING HERE MAY SIGNAL ANYTHING. ``os.kill``, ``os.killpg`` and
``subprocess.run`` are recorders for every case (the sibling file's pattern), so
a dispatch bug shows up as a recorded call rather than as a SIGKILL at whatever
pid 4321 happens to be on the runner.

WHY EVERY CALL GOES THROUGH :func:`_run_bounded`. There is no ``pytest-timeout``
in this environment and no ``timeout-minutes`` on the CI jobs, so a case whose
termination depends on a bound INSIDE ``run_contained`` — which is most of them —
would burn GitHub's 360-minute default if that bound were deleted. Each such
call therefore runs on a daemon thread that is joined for a stated bound and
fails by name. Daemon is load-bearing: with ``daemon=False`` a helper still
blocked in a hung call held the interpreter open past the red assertion
(measured 6 s wall against 4 s with it).
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

import pytest
from aelix_ai.utils import _process_tree
from aelix_ai.utils._process_tree import (
    _READ_CHUNK_BYTES,
    CREATE_NEW_PROCESS_GROUP,
    DRAIN_CAP_SECONDS,
    EXIT_DRAIN_SECONDS,
    INTERRUPT_REAP_SECONDS,
    KILL_DRAIN_SECONDS,
    REAP_GRACE_SECONDS,
    AbortHandle,
    ProcessTree,
    _PipeReader,
    _ReadState,
    _Win32Api,
    run_contained,
)

from tests.process_tree.test_process_tree_api import (
    HANDLE,
    PID,
    SIGKILL,
    FakeApi,
    _leads_its_own_group,
)

_T = TypeVar("_T")

#: The argv every case "spawns". It is never executed — the fake replaces
#: ``Popen`` — but it is the value ``.args`` and ``TimeoutExpired.cmd`` are
#: asserted against.
ARGV = ["a-command", "--flag"]

#: The names that belong to a KILL LADDER, in the shared order log. The attach
#: and release calls (``create_job``, ``assign``, ``close_handle``) share that
#: log and are filtered out here, because what the ladder cases claim is the
#: order of the kill, not that an attach happened first — which
#: :func:`test_attach_happens_before_any_wait` claims on its own.
_LADDER_NAMES = ("kill", "killpg", "taskkill", "terminate_job", "root")


def _run_bounded(fn: Callable[[], _T], bound: float, what: str) -> _T:
    """Run ``fn`` on a daemon thread, joined for ``bound`` seconds.

    Returns what ``fn`` returned and re-raises what it raised, so a case reads
    like a direct call; a call that has not come back within ``bound`` fails
    NAMING the mutant instead of hanging the leg. See the module docstring.
    """

    result: list[_T] = []
    error: list[BaseException] = []

    def _target() -> None:
        try:
            result.append(fn())
        except BaseException as exc:  # noqa: BLE001 — re-raised in the caller
            error.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(bound)
    if thread.is_alive():
        pytest.fail(f"{what}: run_contained did not return within {bound}s")
    if error:
        raise error[0]
    return result[0]


def _write_all(fd: int, payload: bytes) -> int:
    """``os.write`` every byte of ``payload``, returning how many went in.

    Bounded by ``len(payload)`` rather than a ``while True``: a pipe write is
    allowed to be partial, and the thing a case using this asserts is that the
    whole payload got THROUGH — which only means anything if the loop cannot
    itself be the reason it did not.
    """

    written = 0
    while written < len(payload):
        written += os.write(fd, payload[written : written + _READ_CHUNK_BYTES])
    return written


def _recording_readers(monkeypatch: pytest.MonkeyPatch) -> list[_PipeReader]:
    """Capture every reader ``run_contained`` STARTS, in order.

    Through ``_start_reader`` rather than through ``_PipeReader`` because what
    the cases below need is the ones that actually started — a constructor spy
    would also hand back a reader whose ``start`` raised (which is exactly
    :func:`test_a_reader_that_cannot_start_still_ends_the_tree`'s shape).
    """

    started: list[_PipeReader] = []
    real = _process_tree._start_reader

    def start_reader(stream: Any, state: _ReadState) -> _PipeReader | None:
        reader = cast("_PipeReader | None", real(stream, state))
        if reader is not None:
            started.append(reader)
        return reader

    monkeypatch.setattr(_process_tree, "_start_reader", start_reader)
    return started


# === the seams ==============================================================


@dataclass
class Spies:
    """Every signal and shell-out the module could emit, recorded instead.

    ``order`` is the one list the ladders are asserted against: a ladder that
    sends the right calls in the wrong order (the belt before the tree, the job
    before ``taskkill``) is a real defect and separate per-name lists cannot
    see it.
    """

    kills: list[tuple[int, int]] = field(default_factory=list)
    killpgs: list[tuple[int, int]] = field(default_factory=list)
    runs: list[list[str]] = field(default_factory=list)
    order: list[str] = field(default_factory=list)

    @property
    def ladder(self) -> list[str]:
        return [name for name in self.order if name in _LADDER_NAMES]

    @property
    def targets(self) -> set[int]:
        """Every pid or pgid this case addressed."""

        return {pid for pid, _ in self.kills} | {pgid for pgid, _ in self.killpgs}


@pytest.fixture(autouse=True)
def spies(monkeypatch: pytest.MonkeyPatch) -> Spies:
    recorded = Spies()

    def fake_kill(pid: int, sig: int) -> None:
        recorded.order.append("kill")
        recorded.kills.append((pid, sig))

    def fake_killpg(pgid: int, sig: int) -> None:
        recorded.order.append("killpg")
        recorded.killpgs.append((pgid, sig))

    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(signal, "SIGKILL", SIGKILL, raising=False)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        # ``_taskkill_tree``'s stdio is DEVNULL and NOT ``capture_output`` since
        # #221 (review WIN-3): a captured-and-discarded pipe is the very shape
        # this issue exists to end, because on win32 CPython follows a timed-out
        # kill with an UNTIMED ``communicate()`` to join the reader threads.
        assert kwargs.get("stdin") is subprocess.DEVNULL
        assert kwargs.get("stdout") is subprocess.DEVNULL
        assert kwargs.get("stderr") is subprocess.DEVNULL
        assert "capture_output" not in kwargs
        assert kwargs.get("check") is False
        assert kwargs.get("timeout") == 5
        recorded.order.append("subprocess.run")
        recorded.runs.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    # TP6: the POSIX attach asks ``getpgid`` whether the child leads its own
    # group. Without this it reads the RUNNER's answer for pid 4321 and the
    # ``contained`` arm is decided by whatever else happens to be running.
    _leads_its_own_group(monkeypatch)
    return recorded


class OrderedApi(FakeApi):
    """:class:`FakeApi` that also stamps the shared order log.

    Subclassed rather than re-written so the win32 arm's recording semantics
    stay the sibling file's — including that a method raises AFTER it records.
    """

    def __init__(self, order: list[str], **errors: BaseException) -> None:
        super().__init__(**errors)
        self._order = order

    def _record(self, name: str, *args: Any) -> None:
        self._order.append(name)
        super()._record(name, *args)


class FakePopen:
    """The child ``run_contained`` believes it spawned.

    Real ``os.pipe()``s, because the drain is the thing under test and it reads
    file descriptors; everything else is a switch the case throws. ``wait``
    blocks on a :class:`threading.Event` and raises ``TimeoutExpired`` exactly
    as CPython's does, so the timeout ladder is entered through its real door.
    """

    def __init__(self, owner: Spawner, argv: list[str]) -> None:
        self._owner = owner
        self.args = argv
        self.pid = PID
        self.returncode: int | None = None
        self.exited = threading.Event()
        self.kills = 0
        stdout_r, self.stdout_w = os.pipe()
        stderr_r, self.stderr_w = os.pipe()
        self.stdout = os.fdopen(stdout_r, "rb")
        self.stderr = os.fdopen(stderr_r, "rb")
        # PER END, not one flag for the pair: the drain's clause (a) is "BOTH
        # readers", and the only way to tell that from "either reader" is a case
        # that EOFs one pipe while a holder keeps the other
        # (:func:`test_the_drain_waits_for_both_eofs`).
        self._stdout_open = True
        self._stderr_open = True
        if owner.platform == "win32":
            # ``_retained_handle`` reads exactly this attribute, and a POSIX
            # ``Popen`` does not have it (measured) — so the arms differ here.
            self._handle = HANDLE

    # -- the ``Popen`` surface ``run_contained`` uses ------------------------

    def wait(self, timeout: float | None = None) -> int:
        if self._owner.on_wait_enter is not None:
            enter, self._owner.on_wait_enter = self._owner.on_wait_enter, None
            enter()
        if self._owner.wait_error is not None:
            error, self._owner.wait_error = self._owner.wait_error, None
            raise error
        if not self.exited.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)
        if self._owner.on_wait_return is not None:
            leave, self._owner.on_wait_return = self._owner.on_wait_return, None
            leave(self)
        return cast(int, self.returncode)

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.kills += 1
        self._owner.order.append("root")
        if self._owner.on_kill is not None:
            # Fired ONCE, and before anything the kill releases is closed: this
            # is the seam the post-kill drain's cases write their tail through.
            hook, self._owner.on_kill = self._owner.on_kill, None
            hook(self)
        if self._owner.wedged:
            return
        self.returncode = -9
        # A real SIGKILL releases the pipes the root held, and everything it
        # released is readable at once; a case that keeps a holder alive past
        # the kill says so by clearing ``close_on_kill``.
        if self._owner.close_on_kill:
            self.close_writes()
        self.exited.set()

    # -- the switches a case throws -----------------------------------------

    def exit(self, code: int = 0) -> None:
        """The root exits WITHOUT closing the pipes — a holder still has them."""

        self.returncode = code
        self.exited.set()

    def finish(self, code: int = 0) -> None:
        """The root exits and every writer is gone, so both pipes reach EOF."""

        self.close_writes()
        self.exit(code)

    def close_writes(self) -> None:
        self.close_stdout_write()
        self.close_stderr_write()

    def close_stdout_write(self) -> None:
        if not self._stdout_open:
            return
        self._stdout_open = False
        with contextlib.suppress(OSError):
            os.close(self.stdout_w)

    def close_stderr_write(self) -> None:
        """Close ONLY stderr's write end: one pipe at EOF, the other still held.

        The flag is per END rather than per object because the teardown closes
        whatever is still open, and a blanket ``suppress(OSError)`` over a
        second close of the same number would hide the fd having been RECYCLED
        in between and shut an unrelated descriptor.
        """

        if not self._stderr_open:
            return
        self._stderr_open = False
        with contextlib.suppress(OSError):
            os.close(self.stderr_w)


class Spawner:
    """Installs the fake ``Popen`` and keeps every child it handed out."""

    def __init__(self, platform: str, order: list[str]) -> None:
        self.platform = platform
        self.order = order
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.procs: list[FakePopen] = []
        self.spawn_error: BaseException | None = None
        self.wait_error: BaseException | None = None
        self.on_wait_enter: Callable[[], None] | None = None
        self.on_wait_return: Callable[[FakePopen], None] | None = None
        #: Fired from :meth:`FakePopen.kill`, once. What a real SIGKILL does to
        #: a pipe the root was holding is release it, and this is how a case
        #: says what became readable at that instant.
        self.on_kill: Callable[[FakePopen], None] | None = None
        self.wedged = False
        self.close_on_kill = True

    def __call__(self, argv: list[str], **kwargs: Any) -> FakePopen:
        if self.spawn_error is not None:
            raise self.spawn_error
        self.calls.append((list(argv), dict(kwargs)))
        proc = FakePopen(self, list(argv))
        self.procs.append(proc)
        return proc

    @property
    def proc(self) -> FakePopen:
        return self.procs[0]


@pytest.fixture
def spawner(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, spies: Spies
) -> Iterator[Spawner]:
    """The fake ``Popen``, plus the teardown that keeps a case from leaking.

    TP12: a case that leaves a write end open leaves a reader blocked in
    ``read1`` — a DAEMON thread, so it would survive into every later case and
    hold a pipe. Teardown supplies the EOF those readers are waiting for and
    then joins them, which also asserts the reader really does end on EOF.
    """

    platform = cast(str, getattr(request, "param", "linux"))
    made = Spawner(platform, spies.order)
    monkeypatch.setattr(subprocess, "Popen", made)
    yield made
    for proc in made.procs:
        proc.close_writes()
        proc.exited.set()
    deadline = time.monotonic() + 5.0
    for thread in threading.enumerate():
        if isinstance(thread, _PipeReader):
            thread.join(max(0.0, deadline - time.monotonic()))
            assert not thread.is_alive(), "a reader outlived its pipe's EOF"


@dataclass
class Trees:
    """Every :class:`ProcessTree` the call attached, and every ``close`` on one."""

    attached: list[ProcessTree] = field(default_factory=list)
    attach_kwargs: list[dict[str, Any]] = field(default_factory=list)
    closes: list[int] = field(default_factory=list)


@pytest.fixture
def trees(monkeypatch: pytest.MonkeyPatch) -> Trees:
    """RETAIN every tree (TP7) and count ``close`` calls on the METHOD.

    ``ProcessTree.close`` is idempotent, so "``close_handle`` was called once"
    cannot tell a single call from a double one — only a call-count spy on the
    method can, and the ``weakref.finalize`` would happily supply an omitted
    call later, at a GC moment, and hide it.
    """

    record = Trees()
    real_attach = ProcessTree.attach.__func__  # type: ignore[attr-defined]
    real_close = ProcessTree.close

    def attach(cls: type[ProcessTree], pid: int, **kwargs: Any) -> ProcessTree:
        tree = cast(ProcessTree, real_attach(cls, pid, **kwargs))
        record.attached.append(tree)
        record.attach_kwargs.append(dict(kwargs))
        return tree

    def close(self: ProcessTree) -> None:
        record.closes.append(self.pid)
        real_close(self)

    monkeypatch.setattr(ProcessTree, "attach", classmethod(attach))
    monkeypatch.setattr(ProcessTree, "close", close)
    return record


def _call(
    spawner: Spawner,
    spies: Spies,
    *,
    timeout: float | None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    api: _Win32Api | None = None,
    abort: AbortHandle | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """``run_contained`` with this case's platform and, on win32, an api.

    Attaching with ``platform="win32"`` and no api off Windows RAISES rather
    than quietly emitting ``os.kill(pid, 1)`` — ``CTRL_BREAK_EVENT`` and POSIX
    ``SIGHUP`` are both 1 — so the win32 arms always carry one.
    """

    if api is None and spawner.platform == "win32":
        api = cast(_Win32Api, OrderedApi(spies.order))
    return run_contained(
        ARGV, timeout=timeout, cwd=cwd, env=env, platform=spawner.platform, api=api, abort=abort
    )


# === C.1.1 — the spawn ======================================================


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_spawn_kwargs_and_stdio(spawner: Spawner, spies: Spies, trees: Trees) -> None:
    """C.1.1 exactly: these kwargs and NOTHING else, on both platforms.

    ``stdin=DEVNULL`` is Pi's ``execCommand`` contract (``stdio: ["ignore",
    "pipe", "pipe"]``) and it is a CHANGE: today's ``api.exec`` inherits the
    TUI's stdin. ``text`` must be absent because the helper returns bytes and
    each site owns its own decoding; ``shell`` because the argv is the
    extension's.
    """

    caller_env = {"AELIX_PROBE": "1"}
    spawner.on_wait_enter = lambda: spawner.proc.finish(0)

    _run_bounded(
        lambda: _call(spawner, spies, timeout=5.0, cwd="/somewhere", env=caller_env),
        5.0,
        "spawn kwargs",
    )

    argv, kwargs = spawner.calls[0]
    assert argv == ARGV
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    assert kwargs["cwd"] == "/somewhere"
    # A COPY: a caller that mutates its dict afterwards must not reach the child.
    assert kwargs["env"] == caller_env
    assert kwargs["env"] is not caller_env
    if spawner.platform == "win32":
        assert kwargs["creationflags"] == CREATE_NEW_PROCESS_GROUP
        assert set(kwargs) == {"stdin", "stdout", "stderr", "cwd", "env", "creationflags"}
    else:
        assert kwargs["start_new_session"] is True
        assert set(kwargs) == {"stdin", "stdout", "stderr", "cwd", "env", "start_new_session"}
    # Named individually as well: the set assertion above would still pass if
    # one of these had been swapped IN for a kwarg that is meant to be there.
    for absent in ("text", "shell", "capture_output", "process_group", "encoding"):
        assert absent not in kwargs


def test_env_none_is_passed_through_as_none(spawner: Spawner, spies: Spies, trees: Trees) -> None:
    """No ``env`` means INHERIT — the git clone and the ``fd`` scan rely on it."""

    spawner.on_wait_enter = lambda: spawner.proc.finish(0)

    _run_bounded(lambda: _call(spawner, spies, timeout=5.0), 5.0, "env=None")

    assert spawner.calls[0][1]["env"] is None
    assert spawner.calls[0][1]["cwd"] is None


# === C.1.2 — the attach =====================================================


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_attach_happens_before_any_wait(spawner: Spawner, spies: Spies, trees: Trees) -> None:
    """On win32 only descendants created AFTER the assignment inherit the job.

    So the attach has to be the first thing after the spawn returns, and every
    instruction in between is assignment window. The fake's ``wait`` asks how
    many trees existed by the time it was entered.
    """

    seen: list[int] = []

    def _enter() -> None:
        seen.append(len(trees.attached))
        spawner.proc.finish(0)

    spawner.on_wait_enter = _enter

    _run_bounded(lambda: _call(spawner, spies, timeout=5.0), 5.0, "attach order")

    assert seen == [1]
    assert trees.attach_kwargs[0]["kill_on_close"] is False
    # POSIX ``Popen`` has no ``_handle`` (measured), so ``None`` there is
    # ``_retained_handle``'s documented contract and not a miss.
    assert trees.attach_kwargs[0]["handle"] == (HANDLE if spawner.platform == "win32" else None)


# === C.1.4 — the exit path ==================================================


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_success_returns_output_and_closes(spawner: Spawner, spies: Spies, trees: Trees) -> None:
    """The ordinary end: both pipes reached EOF, nothing was killed, tree closed."""

    def _enter() -> None:
        os.write(spawner.proc.stdout_w, b"out-bytes")
        os.write(spawner.proc.stderr_w, b"err-bytes")
        spawner.proc.finish(7)

    spawner.on_wait_enter = _enter

    result = _run_bounded(lambda: _call(spawner, spies, timeout=5.0), 5.0, "success")

    assert result.args == ARGV
    assert (result.returncode, result.stdout, result.stderr) == (7, b"out-bytes", b"err-bytes")
    assert spies.ladder == []
    assert spawner.proc.kills == 0
    assert trees.attached[0].closed is True
    assert trees.closes == [PID]
    if spawner.platform == "win32":
        api = cast(OrderedApi, trees.attached[0]._api)
        assert api.names.count("close_handle") == 1
        assert "terminate_job" not in api.names


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_an_exited_root_with_a_quiet_pipe_holder_returns_after_the_idle_drain(
    spawner: Spawner, spies: Spies, trees: Trees
) -> None:
    """Bug 2 of #221, and Pi #5303/#5753: pipe EOF is not the root's lifetime.

    The root is quiet for three graces before it exits, so a timer measured
    from the LAST CHUNK is already expired at the exit and the whole post-exit
    tail is binned — measured ``stdout=b'EARLY\\n'`` against Pi's
    ``b'EARLY\\nLATE0..4\\n'``. Arming at the exit is what keeps ``tail``.

    The write ends stay OPEN on purpose: this is the shape where ``main``
    waited out the whole timeout and then reported ``TimeoutExpired`` over a
    root that had already exited 0.
    """

    exited_at: list[float] = []

    def _enter() -> None:
        time.sleep(3 * EXIT_DRAIN_SECONDS)
        exited_at.append(time.monotonic())
        spawner.proc.exit(0)

    spawner.on_wait_enter = _enter
    spawner.on_wait_return = lambda proc: os.write(proc.stdout_w, b"tail\n")

    result = _run_bounded(lambda: _call(spawner, spies, timeout=None), 5.0, "idle drain")
    elapsed = time.monotonic() - exited_at[0]

    assert result.stdout == b"tail\n"
    assert result.returncode == 0
    assert EXIT_DRAIN_SECONDS <= elapsed < EXIT_DRAIN_SECONDS + 0.5
    assert spies.ladder == []
    assert spawner.proc.kills == 0
    assert trees.closes == [PID]


def test_a_left_behind_reader_keeps_draining_and_stops_retaining(
    spawner: Spawner, spies: Spies, trees: Trees, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#221 review site-exec-1: the leak is the THREAD, and must not be the heap.

    The shape above returns while a holder still has the write end, so both
    daemon readers stay blocked in ``read1`` — that is the leak this module
    states and accepts. What it must NOT do is go on appending into a
    ``chunks`` list nobody will ever read again: measured against a holder
    writing after a ``returncode == 0`` return, 10 MB retained at the return
    and 25 MB six seconds later, +2 threads and +2 fds per call, STACKING.

    Both halves of the fix are asserted here because either one alone is
    wrong. Stopping the READ would wedge the holder on a full 64 KiB pipe, so
    1 MiB — sixteen chunks, far past any pipe buffer on any platform — must go
    in without blocking; and stopping the RETENTION is what the byte count
    claims, bounded at one in-flight chunk because ``detach`` sets its flag
    before it clears the list. The write is bounded on its own thread for the
    file's usual reason: under the mutant that stops reading it never returns.

    Then the holder closes, and the reader ends on EOF like any other — a
    detached reader is still a reader, not a wedged thread.
    """

    started = _recording_readers(monkeypatch)
    spawner.on_wait_enter = lambda: spawner.proc.exit(0)

    result = _run_bounded(lambda: _call(spawner, spies, timeout=None), 5.0, "a left-behind reader")

    assert result.returncode == 0
    assert len(started) == 2, "no reader was captured — the case measured nothing"
    out_reader = started[0]
    assert out_reader.is_alive(), (
        "the holder still has the write end, so the reader must still be blocked in read1 — "
        "the case measured nothing"
    )

    payload = b"x" * (16 * _READ_CHUNK_BYTES)
    written = _run_bounded(
        lambda: _write_all(spawner.proc.stdout_w, payload), 5.0, "1 MiB into a held pipe"
    )

    assert written == len(payload)
    retained = sum(len(chunk) for chunk in out_reader.chunks)
    assert retained <= _READ_CHUNK_BYTES, (
        f"{retained} bytes retained after the call returned; at most one in-flight chunk "
        f"({_READ_CHUNK_BYTES}) may be"
    )

    spawner.proc.close_stdout_write()
    out_reader.join(5.0)
    assert not out_reader.is_alive(), "a detached reader must still end when its pipe reaches EOF"
    assert out_reader.eof is True


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_the_drain_waits_for_both_eofs(
    spawner: Spawner, spies: Spies, trees: Trees, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§A.2.6(a): clause (a) is BOTH readers at EOF, never either one.

    Every other holder case in this file and in D.2 has the holder on BOTH
    pipes, so ``all`` and ``any`` are indistinguishable there — measured, the
    ``any`` mutant survived the whole 272-case set (#221 review MUT-1/M16).
    Here stderr reaches EOF at the exit while a holder keeps stdout: under
    ``any`` the drain returns before the tail is written and the output is
    silently truncated under ``returncode == 0`` — measured through a real
    grandchild as ``b'HEADTAIL'`` -> ``b'HEAD'``.

    The grace is widened to 0.5 s and the tail written at half of it for the
    same reason T8 widened two other bounds: what separates the two answers is
    ~0 s against 0.25 s, and the margin should be scheduling delay's, not the
    assertion's.
    """

    monkeypatch.setattr(_process_tree, "EXIT_DRAIN_SECONDS", 0.5)
    written = threading.Event()

    def _enter() -> None:
        proc = spawner.proc
        proc.close_stderr_write()

        def _late() -> None:
            time.sleep(0.25)
            # Suppressed: on the red path the call has already returned and the
            # teardown may have closed this end, and this thread has no caller.
            with contextlib.suppress(OSError):
                os.write(proc.stdout_w, b"tail\n")
            written.set()

        threading.Thread(target=_late, daemon=True).start()
        proc.exit(0)

    spawner.on_wait_enter = _enter

    result = _run_bounded(lambda: _call(spawner, spies, timeout=5.0), 5.0, "both eofs")

    assert written.wait(2.0), "the tail was never written — the case measured nothing"
    assert b"tail\n" in result.stdout
    assert result.stderr == b""
    assert result.returncode == 0
    assert spawner.proc.kills == 0


def test_an_actively_writing_holder_is_cut_at_the_cap(
    spawner: Spawner, spies: Spies, trees: Trees
) -> None:
    """The absolute cap, which is Aelix's divergence from Pi (Pi has none).

    ``timeout=None`` is ``api.exec``'s DEFAULT, so with no cap the idle rule is
    the only bound left and a descendant that never falls idle defeats it —
    measured 10 MB buffered at 9 s and still climbing, the call never returning
    (the ``ru_maxrss`` figure that used to stand here carried no unit and is
    dropped rather than restated — #221 review DOC-1). The cost is stated
    rather than hidden: a still-writing DESCENDANT has its output cut, with the
    ROOT's own ``returncode`` intact.
    """

    exited_at: list[float] = []
    stop = threading.Event()

    def _writer() -> None:
        while not stop.wait(EXIT_DRAIN_SECONDS / 2):
            try:
                os.write(spawner.proc.stdout_w, b"chunk")
            except OSError:
                return

    def _enter() -> None:
        exited_at.append(time.monotonic())
        spawner.proc.exit(5)
        threading.Thread(target=_writer, daemon=True).start()

    spawner.on_wait_enter = _enter

    try:
        result = _run_bounded(lambda: _call(spawner, spies, timeout=None), 6.0, "drain cap")
    finally:
        stop.set()
    elapsed = time.monotonic() - exited_at[0]

    assert abs(elapsed - DRAIN_CAP_SECONDS) < 0.3
    assert result.returncode == 5
    assert b"chunk" in result.stdout
    assert spawner.proc.kills == 0


def test_the_deadline_caps_the_drain_but_keeps_one_grace(
    spawner: Spawner, spies: Spies, trees: Trees
) -> None:
    """The original deadline caps the drain — floored at one grace after exit.

    Without the floor a root that exits at ``deadline - 1 ms`` would have its
    OWN tail cut; with it, the drain is never shorter than the idle grace.
    """

    timeout = 0.5
    exited_at: list[float] = []
    stop = threading.Event()

    def _writer() -> None:
        while not stop.wait(EXIT_DRAIN_SECONDS / 2):
            try:
                os.write(spawner.proc.stdout_w, b"chunk")
            except OSError:
                return

    def _enter() -> None:
        time.sleep(0.49)
        exited_at.append(time.monotonic())
        spawner.proc.exit(0)
        threading.Thread(target=_writer, daemon=True).start()

    spawner.on_wait_enter = _enter
    started = time.monotonic()

    try:
        result = _run_bounded(lambda: _call(spawner, spies, timeout=timeout), 5.0, "deadline cap")
    finally:
        stop.set()
    returned_at = time.monotonic()

    assert returned_at - exited_at[0] >= EXIT_DRAIN_SECONDS
    # 0.6, not 0.3 (#221 review T8): the run measures 0.60 s five times out of
    # five against a 0.5 s timeout, and what it is exposed to is two thread
    # hand-offs of scheduling delay on a 2-core runner, not CPU. The claim this
    # case makes is the LOWER bound on the line above — the grace floor —
    # which the widening does not touch.
    assert returned_at - started <= timeout + EXIT_DRAIN_SECONDS + 0.6
    assert result.returncode == 0


# === C.1.3 — the timeout ladder =============================================


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_timeout_kills_the_tree_then_the_root_then_bounds_the_reap(
    spawner: Spawner, spies: Spies, trees: Trees
) -> None:
    """The tree FIRST, the belt second — and the belt is not redundant.

    ``hard_kill`` does not touch ``Popen`` state, so the belt is always entered
    with ``returncode is None`` even when the root is already dead (measured).
    On win32 ``taskkill /T`` runs before ``TerminateJobObject`` so ``/T`` can
    walk to an assignment-window escapee through its still-live parent link.
    """

    spawner.on_wait_enter = lambda: os.write(spawner.proc.stdout_w, b"partial")

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run_bounded(lambda: _call(spawner, spies, timeout=0.1), 5.0, "timeout ladder")

    if spawner.platform == "win32":
        assert spies.ladder == ["taskkill", "terminate_job", "root"]
    else:
        assert spies.ladder == ["killpg", "root"]
        assert spies.killpgs == [(PID, SIGKILL)]
    # The module's one shell-out is ``taskkill``, and on the win32 arm it goes
    # through the api seam — so ``subprocess.run`` is never reached either way.
    assert spies.runs == []
    assert spawner.proc.kills == 1
    assert caught.value.cmd == ARGV
    assert caught.value.timeout == 0.1
    assert caught.value.stdout == b"partial"
    # ``b""``, never ``None``: every caller decodes both streams unconditionally.
    assert caught.value.stderr == b""
    assert trees.closes == [PID]


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_the_post_kill_drain_is_armed_at_the_reap(
    spawner: Spawner, spies: Spies, trees: Trees, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§A.3.1's stamp: the tail the KILL released is not binned.

    The root is silent from the spawn, so ``last_chunk_at`` is the spawn instant
    and is three graces stale by the time the kill lands; the bytes become
    readable only AT the reap, and the holder that has them is outside the tree,
    so no EOF ever ends the drain. Deleting either the post-kill ``_drain`` or
    the ``exited_at`` stamp that arms it left the whole 272-case set green
    (#221 review T3/M9 and MUT-4/E4) — the existing timeout cases either see the
    holder EOF at the kill or assert ``stdout == b""``.

    The release is written from a THREAD ~30 ms after the kill rather than
    synchronously inside it, and the grace is widened to 0.5 s: a synchronous
    write is a race the reader wins about half the time, so it does not fail
    when the drain is deleted (measured 4/10 and 8/10 detection by two
    reviewers independently). Nothing here joins the readers.
    """

    monkeypatch.setattr(_process_tree, "EXIT_DRAIN_SECONDS", 0.5)
    # The holder outside the tree keeps the write end: EOF is never the reason
    # the drain ends here, the idle rule is.
    spawner.close_on_kill = False
    released = threading.Event()

    def _on_kill(proc: FakePopen) -> None:
        def _late() -> None:
            time.sleep(0.03)
            with contextlib.suppress(OSError):
                os.write(proc.stdout_w, b"released")
            released.set()

        threading.Thread(target=_late, daemon=True).start()

    spawner.on_kill = _on_kill

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run_bounded(
            # ``timeout`` is 3x the widened grace so ``last_chunk_at`` cannot
            # stand in for the reap stamp.
            lambda: _call(spawner, spies, timeout=1.5),
            1.5 + REAP_GRACE_SECONDS + KILL_DRAIN_SECONDS + 1.5,
            "post-kill drain armed at the reap",
        )

    assert released.wait(2.0), "the kill released nothing — the case measured nothing"
    assert b"released" in caught.value.stdout
    assert spawner.proc.kills == 1


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_a_chatty_holder_past_the_kill_is_cut_at_kill_drain(
    spawner: Spawner, spies: Spies, trees: Trees, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one place :data:`KILL_DRAIN_SECONDS` is observable at all.

    A SILENT escaped holder — which is what every other timeout case has — ends
    the post-kill drain on the 0.1 s idle rule, so the cap is never reached and
    substituting :data:`REAP_GRACE_SECONDS` for it changes nothing: measured,
    that mutant survived the whole set and the leaked-reader case reported the
    same 1.111 s under it as at HEAD (#221 review MUT-3(b)/M10). A holder that
    never falls idle is the shape where the two numbers differ.

    The constants are shrunk so what is measured is the ARITHMETIC (which cap
    ends the drain) rather than the production latency, which D.2 owns. Shrunk,
    they are 0.3 s apart, so the window below is stated as a FLOOR and a
    ceiling rather than as ``abs(elapsed - 0.4) < 0.3``: the symmetric form has
    exactly the separation as its slack and the substitution's measured
    ``0.105 s`` clears it by 5 ms — rebuilt as a mutant here and measured, 2
    passed. The floor is the whole claim; the 0.3 above is scheduling's.

    The grace is widened to 0.2 s and the chatter writes every 0.05 s for the
    same reason T3's case widened its own: what must end this drain is the CAP,
    and the idle rule re-arms only when the READER thread is scheduled. At the
    0.1 s default with a chunk every 0.05 s, two missed wake-ups end the drain
    at 0.105 s — which is the mutant's own signature, so the case went red at
    HEAD on a loaded host (measured once in ~12). Four wake-ups have to be
    missed now.
    """

    monkeypatch.setattr(_process_tree, "EXIT_DRAIN_SECONDS", 0.2)
    monkeypatch.setattr(_process_tree, "REAP_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(_process_tree, "KILL_DRAIN_SECONDS", 0.4)
    spawner.close_on_kill = False
    killed_at: list[float] = []
    stop = threading.Event()

    def _chatter(proc: FakePopen) -> None:
        while not stop.is_set():
            try:
                os.write(proc.stdout_w, b"chunk")
            except OSError:
                return
            # A quarter of the widened grace, spelled as the number it is: the
            # module-level import here is still the unpatched 0.1.
            stop.wait(0.05)

    def _on_kill(proc: FakePopen) -> None:
        killed_at.append(time.monotonic())
        threading.Thread(target=_chatter, args=(proc,), daemon=True).start()

    spawner.on_kill = _on_kill

    try:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            _run_bounded(
                lambda: _call(spawner, spies, timeout=0.1), 0.1 + 0.1 + 0.4 + 1.5, "kill drain cap"
            )
        raised_at = time.monotonic()
    finally:
        stop.set()

    assert killed_at, "the kill never landed — the case measured nothing"
    after_the_kill = raised_at - killed_at[0]
    # 0.4 (the cap), not the reap grace substituted for it (measured 0.104 s
    # under that mutant), not revision 1's join of the readers for the reap
    # grace (0.108 s), and not the 0.05 the idle rule would give if the chatter
    # were not re-arming it. The floor is what discriminates all three; the
    # ceiling is scheduling's slack.
    assert after_the_kill >= 0.4 - 0.1, f"the drain ended at {after_the_kill:.3f}s, before the cap"
    assert after_the_kill < 0.4 + 0.3, f"the drain ran {after_the_kill:.3f}s, past the cap"
    assert b"chunk" in caught.value.stdout


def test_timeout_with_a_wedged_root_costs_reap_grace_not_forever(
    spawner: Spawner, spies: Spies, trees: Trees, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kill that could not kill costs a bounded wait, not the calling thread.

    That is reachable on win32: with no job (a failed attach) and no resolvable
    ``taskkill.exe``, ``hard_kill`` is a silent no-op (#202 review win-leg/F3).
    The constants are shrunk here so the case measures the ARITHMETIC — reap
    grace, then kill drain, then the raise — rather than the production
    numbers, which D.2 measures against real children.
    """

    monkeypatch.setattr(_process_tree, "REAP_GRACE_SECONDS", 0.3)
    monkeypatch.setattr(_process_tree, "KILL_DRAIN_SECONDS", 0.2)
    spawner.wedged = True
    spawner.close_on_kill = False
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        # ``+ 1.5``, not ``+ 0.5`` (#221 review T8): measured 0.51 s five times
        # out of five, and this bound is a watchdog against a HANG, not the
        # case's claim — the claim is the ``elapsed >= 0.1 + 0.3`` below.
        _run_bounded(lambda: _call(spawner, spies, timeout=0.1), 0.1 + 0.3 + 0.2 + 1.5, "wedged")
    elapsed = time.monotonic() - started

    assert elapsed >= 0.1 + 0.3
    assert caught.value.stdout == b""
    assert caught.value.stderr == b""
    assert trees.closes == [PID]


@pytest.mark.parametrize("spawner", ["win32"], indirect=True)
def test_a_failed_win32_attach_still_kills_the_root(
    spawner: Spawner, spies: Spies, trees: Trees
) -> None:
    """A refused ``AssignProcessToJobObject`` degrades; it never raises.

    With no job there is no ``TerminateJobObject`` to run, so what is left is
    ``taskkill /T`` and the belt — which is the case the belt exists for.
    """

    api = OrderedApi(spies.order, assign=OSError("refused"))

    with pytest.raises(subprocess.TimeoutExpired):
        _run_bounded(
            lambda: _call(spawner, spies, timeout=0.1, api=cast(_Win32Api, api)),
            5.0,
            "failed attach",
        )

    assert trees.attached[0].contained is False
    assert spies.ladder == ["taskkill", "root"]
    assert "terminate_job" not in api.names
    assert spawner.proc.kills == 1


def test_a_child_in_our_own_group_is_killed_root_only(
    spawner: Spawner, spies: Spies, trees: Trees, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TP6's opposite arm: not our group, so never ``killpg``.

    ``getpgid(pid) != pid`` means the spawn kwargs did not take and the child is
    sitting in OUR group — which holds this interpreter and every sibling it
    spawned. A group kill there is the "cleanup" SIGKILLing the process
    performing it (measured, #202 review posix2/F5).
    """

    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1000, raising=False)

    with pytest.raises(subprocess.TimeoutExpired):
        _run_bounded(lambda: _call(spawner, spies, timeout=0.1), 5.0, "not our group")

    assert trees.attached[0].contained is False
    assert spies.killpgs == []
    assert spies.kills == [(PID, SIGKILL)]


# === C.1.6 / C.1.8 — spawn failure, no bound, and the interrupt ladder ======


def test_spawn_failure_propagates_and_attaches_nothing(
    spawner: Spawner, spies: Spies, trees: Trees
) -> None:
    """No tree exists, so there is nothing to close and nothing to kill.

    All three sites already translate this exception (``127`` at ``api.exec``,
    a ``CatalogError`` at the clone, ``None`` at ``fd``), so the helper must
    not dress it up.
    """

    spawner.spawn_error = FileNotFoundError(2, "No such file or directory")

    with pytest.raises(FileNotFoundError):
        _run_bounded(lambda: _call(spawner, spies, timeout=1.0), 5.0, "spawn failure")

    assert trees.attached == []
    assert trees.closes == []
    assert spies.ladder == []


def test_timeout_none_waits_for_exit(spawner: Spawner, spies: Spies, trees: Trees) -> None:
    """``timeout=None`` — ``api.exec``'s default — waits without bound, as ``run`` does."""

    spawner.on_wait_enter = lambda: threading.Timer(0.2, spawner.proc.finish).start()
    started = time.monotonic()

    result = _run_bounded(lambda: _call(spawner, spies, timeout=None), 5.0, "timeout=None")

    assert result.returncode == 0
    assert time.monotonic() - started >= 0.2
    assert spawner.proc.kills == 0


class _EofExplodes(_PipeReader):
    """A reader whose EOF question raises — the drain's own exception path.

    The drain asks every reader ``eof`` on each pass, so this is where a
    ``BaseException`` out of the DRAIN (rather than out of the wait) enters
    ``run_contained``. Everything else about the reader is the real one's.
    """

    def __init__(self, stream: Any, state: _ReadState) -> None:
        self._eof = False
        super().__init__(stream, state)

    @property
    def eof(self) -> bool:
        raise KeyboardInterrupt

    @eof.setter
    def eof(self, value: bool) -> None:
        self._eof = value


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
@pytest.mark.parametrize("source", ["wait", "drain"])
def test_an_interrupt_ends_the_tree_before_it_propagates(
    spawner: Spawner,
    spies: Spies,
    trees: Trees,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    """CPython's ``except: process.kill()`` leg, at tree granularity.

    Load-bearing rather than cosmetic: ``Popen.__exit__`` states its assumption
    in a comment — "In the case of a KeyboardInterrupt we assume the SIGINT was
    also already sent to our child processes" — and this helper's own spawn
    makes it FALSE. ``start_new_session=True`` takes the child out of the
    terminal's foreground group, and ``CREATE_NEW_PROCESS_GROUP`` "disables
    CTRL+C signals for all processes within the new process group". Measured
    under a real pty: ``^C`` -> parent ``KeyboardInterrupt``, child ALIVE.

    No output is attached and nothing is drained: the caller is unwinding.
    """

    if source == "wait":
        spawner.wait_error = KeyboardInterrupt()
    else:
        monkeypatch.setattr(_process_tree, "_PipeReader", _EofExplodes)
        spawner.on_wait_enter = lambda: spawner.proc.exit(0)
    started = time.monotonic()

    with pytest.raises(KeyboardInterrupt):
        _run_bounded(lambda: _call(spawner, spies, timeout=30.0), 5.0, f"interrupt via {source}")
    elapsed = time.monotonic() - started

    if spawner.platform == "win32":
        assert spies.ladder == ["taskkill", "terminate_job", "root"]
    else:
        assert spies.ladder == ["killpg", "root"]
        assert spies.killpgs == [(PID, SIGKILL)]
    assert spawner.proc.kills == 1
    assert trees.closes == [PID]
    assert elapsed < INTERRUPT_REAP_SECONDS + 0.5


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_a_reader_that_cannot_start_still_ends_the_tree(
    spawner: Spawner, spies: Spies, trees: Trees, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#221 review posix-runner-1: the SETUP is inside a kill leg now too.

    The attach, the ``abort`` hand-over and the two ``_start_reader`` calls
    used to sit between the spawn and every ``except``, so anything raised
    there propagated with the whole contained tree still running. This is the
    reachable half of that window: a second reader whose thread cannot start.
    The first one is already running and the child is already spawned, so a
    bare ``raise`` leaks both.

    ``close_on_kill`` is cleared so the write ends survive the ladder, which is
    what lets the last two assertions ask whether the surviving reader was
    DETACHED — it is in ``readers`` because that list is appended to as each
    thread starts, not built after both have.
    """

    started: list[_PipeReader] = []

    class _SecondStartExplodes(_PipeReader):
        """Starts once, then refuses — ``threading`` raises exactly this."""

        def start(self) -> None:
            if started:
                raise RuntimeError("can't start new thread")
            started.append(self)
            super().start()

    monkeypatch.setattr(_process_tree, "_PipeReader", _SecondStartExplodes)
    spawner.close_on_kill = False

    with pytest.raises(RuntimeError):
        _run_bounded(lambda: _call(spawner, spies, timeout=30.0), 5.0, "a reader that cannot start")

    if spawner.platform == "win32":
        assert spies.ladder == ["taskkill", "terminate_job", "root"]
    else:
        assert spies.ladder == ["killpg", "root"]
        assert spies.killpgs == [(PID, SIGKILL)]
    assert spawner.proc.kills == 1
    assert trees.closes == [PID]

    assert len(started) == 1, "the first reader never started — the case measured nothing"
    os.write(spawner.proc.stdout_w, b"after-the-fall")
    spawner.proc.close_stdout_write()
    started[0].join(5.0)
    assert not started[0].is_alive()
    assert started[0].chunks == [], "the surviving reader was left retaining"


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_an_attach_that_raises_still_belts_the_root(
    spawner: Spawner, spies: Spies, trees: Trees, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before ``attach`` returns there is no tree, so the belt is all there is.

    ``ProcessTree.attach`` is documented not to raise — a win32 failure
    degrades inside it, a POSIX attach cannot fail — but "documented not to"
    is not "cannot", and the child is ALREADY SPAWNED at this point. So the
    call gets a ``try`` of its own whose handler is root-only:
    ``proc.kill()`` and a bounded ``proc.wait``, then the exception propagates
    unchanged. It deliberately does not reach for ``_end_the_tree``, because
    the ``tree`` that would be its first argument is the thing that did not
    happen (#221 review posix-runner-1).

    So the ladder here is ONE rung on both platforms — no ``killpg``, no
    ``taskkill``, no job — and nothing is closed, because nothing was opened.
    """

    def attach(pid: int, **kwargs: Any) -> ProcessTree:
        raise RuntimeError("attach blew up")

    monkeypatch.setattr(ProcessTree, "attach", attach)

    with pytest.raises(RuntimeError):
        _run_bounded(lambda: _call(spawner, spies, timeout=30.0), 5.0, "an attach that raises")

    assert spies.ladder == ["root"]
    assert spawner.proc.kills == 1
    # The belt's bounded ``wait`` reaped it: the root is not left running while
    # the exception unwinds.
    assert spawner.proc.poll() == -9
    assert spies.runs == []
    assert trees.attached == []
    assert trees.closes == []


# === C.1.7 ==================================================================


@pytest.mark.parametrize("shape", ["success", "timeout", "interrupt"])
def test_never_signals_a_pid_it_did_not_spawn(
    spawner: Spawner, spies: Spies, trees: Trees, shape: str
) -> None:
    """Every kill goes through the tree or ``proc.kill()``, and only at ``PID``.

    ``subprocess.run`` must not be reached at all on POSIX: the module's one
    shell-out is ``taskkill``, which is the win32 arm.
    """

    if shape == "success":
        spawner.on_wait_enter = lambda: spawner.proc.finish(0)
        _run_bounded(lambda: _call(spawner, spies, timeout=5.0), 5.0, shape)
    elif shape == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            _run_bounded(lambda: _call(spawner, spies, timeout=0.1), 5.0, shape)
    else:
        spawner.wait_error = KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            _run_bounded(lambda: _call(spawner, spies, timeout=5.0), 5.0, shape)

    assert spies.targets <= {PID}
    assert spies.runs == []


# === §K.1 — AbortHandle =====================================================


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_abort_from_another_thread_ends_the_tree_and_returns(
    spawner: Spawner, spies: Spies, trees: Trees
) -> None:
    """The leg the interrupt ladder cannot supply: a caller on ANOTHER thread.

    ``ExtensionAPI.exec`` awaits this helper on an ``asyncio.to_thread`` worker,
    and CPython delivers signals to the main thread only, so no exception ever
    enters the call and the ladder above never runs there — measured, ``^C``
    ended the command in 0.02 s on ``main`` and waited out its whole remaining
    28.58 s here (#221 review SITE-1). ``abort()`` sends the same two rungs from
    outside and does NOT wait: what it buys is that the blocked ``wait``
    RETURNS, after which this call takes its ordinary exit path.
    """

    handle = AbortHandle()
    aborted_at: list[float] = []
    sent: list[bool] = []

    def _abort() -> None:
        aborted_at.append(time.monotonic())
        sent.append(handle.abort())

    def _enter() -> None:
        timer = threading.Timer(0.1, _abort)
        timer.daemon = True
        timer.start()

    spawner.on_wait_enter = _enter

    result = _run_bounded(
        lambda: _call(spawner, spies, timeout=30.0, abort=handle), 5.0, "abort from another thread"
    )

    assert sent == [True], "the abort found nothing attached — the case measured nothing"
    if spawner.platform == "win32":
        assert spies.ladder == ["taskkill", "terminate_job", "root"]
    else:
        assert spies.ladder == ["killpg", "root"]
        assert spies.killpgs == [(PID, SIGKILL)]
    assert spawner.proc.kills == 1
    # A ``CompletedProcess``, not an exception: the caller has already unwound
    # and there is nobody to raise into, so the run just ends as a killed run.
    assert result.returncode == -9
    assert time.monotonic() - aborted_at[0] < INTERRUPT_REAP_SECONDS + 0.5
    assert trees.attached[0].closed is True
    assert trees.closes == [PID]


@pytest.mark.parametrize("spawner", ["linux", "win32"], indirect=True)
def test_abort_before_attach_kills_at_attach(
    spawner: Spawner, spies: Spies, trees: Trees, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race a cancelled turn really has: ``abort()`` beats the spawn.

    ``asyncio.to_thread`` hands the work to an executor, so a turn cancelled in
    the same millisecond it started can reach the handle before the child
    exists. The mark is kept and :meth:`AbortHandle._attach` fires the kill —
    otherwise the abort is simply lost and the command runs to its timeout,
    which for ``api.exec``'s default (``timeout_ms=None``) is never.

    Driven from the spied ``ProcessTree.attach``, the one instant that is after
    the spawn and before ``run_contained`` hands the tree over.
    """

    handle = AbortHandle()
    early: list[bool] = []
    spied_attach = ProcessTree.attach

    def attach(pid: int, **kwargs: Any) -> ProcessTree:
        tree = cast(ProcessTree, spied_attach(pid, **kwargs))
        early.append(handle.abort())
        return tree

    monkeypatch.setattr(ProcessTree, "attach", attach)

    result = _run_bounded(
        lambda: _call(spawner, spies, timeout=30.0, abort=handle), 5.0, "abort before attach"
    )

    # ``False``: there was nothing to kill YET, so the call only marked.
    assert early == [False]
    assert handle.aborted is True
    if spawner.platform == "win32":
        assert spies.ladder == ["taskkill", "terminate_job", "root"]
    else:
        assert spies.ladder == ["killpg", "root"]
    assert spawner.proc.kills == 1
    assert result.returncode == -9
    assert trees.closes == [PID]


def test_a_late_abort_after_the_run_finished_is_a_no_op(
    spawner: Spawner, spies: Spies, trees: Trees
) -> None:
    """``_finish`` in the call's ``finally`` is why this cannot signal a stranger.

    The pid the handle held has been reaped by the time a slow canceller gets
    to it, and on POSIX that number is free for somebody else — the same hazard
    ``ProcessTree.close``'s docstring states for the group.
    """

    handle = AbortHandle()
    spawner.on_wait_enter = lambda: spawner.proc.finish(0)

    result = _run_bounded(
        lambda: _call(spawner, spies, timeout=5.0, abort=handle), 5.0, "late abort"
    )

    assert result.returncode == 0
    assert handle.abort() is False
    assert handle.aborted is True
    assert spies.ladder == []
    assert spawner.proc.kills == 0
