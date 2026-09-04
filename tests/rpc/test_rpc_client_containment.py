"""Lane P — containment, the injection seams, and child-death detection.

Every test here pins an AELIX-ORIGINAL behaviour, not a pi port. pi's
``rpc-client.ts`` (515 lines at the ``734e08e`` pin) has no child-exit
observation, no containment kwargs and no argv/env seams; the sole liveness
check it has is the 100 ms startup grace, which aelix already ported. The
module docstring on ``rpc/rpc_client.py`` records why each divergence exists.

WHY THESE ARE TIMING ASSERTIONS AND NOT ``pytest.raises`` ALONE. The defect
being fixed did not change WHETHER a dead child failed the caller — it changed
HOW LONG that took. Measured before the fix: a child exiting at 400 ms left
``prompt_and_wait(timeout_ms=5000)`` blocked for 5.02 s and then raised a bare
``TimeoutError()`` with empty args. A test asserting only that *something*
raised would have passed against the broken code, which is the whole failure
mode this sprint keeps finding. So each one asserts the elapsed time against
the budget it was given.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import os
import signal
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.utils._process_tree import (
    ProcessTree,
    _retained_handle,
    kill_process_tree,
)
from aelix_coding_agent.rpc import rpc_client as rpc_client_module
from aelix_coding_agent.rpc._jsonl import JsonlLineReader
from aelix_coding_agent.rpc.rpc_client import (
    RpcClient,
    RpcClientOptions,
    RpcServerExited,
)

from tests.process_probe import (
    STATE_GONE,
    STATE_ZOMBIE,
    await_dead_or_zombie,
    is_dead_or_zombie,
)

# A stub that boots, announces itself on stderr, then exits with a chosen code
# after a chosen delay. ``os._exit`` so nothing flushes or cleans up — the real
# failures this models (an OOM kill, a bad ``--tools`` name, a missing key) are
# just as abrupt.
_DYING_STUB = textwrap.dedent(
    """
    import os, sys, threading, time

    sys.stderr.write("stub booting\\n")
    sys.stderr.flush()

    def _die():
        time.sleep(float(os.environ["STUB_EXIT_DELAY"]))
        sys.stderr.write("stub dying now\\n")
        sys.stderr.flush()
        os._exit(int(os.environ["STUB_EXIT_CODE"]))

    threading.Thread(target=_die, daemon=True).start()
    for line in sys.stdin:
        pass
    time.sleep(30)
    """
)

# A stub that spawns a grandchild INHERITING stdout/stderr and then idles. This
# is the only shape that reproduced the ``stop()`` stall: measured, a live child
# plus a live pipe-holder made ``stop()`` pay its whole SIGTERM grace even
# though ``returncode`` was already set, because ``proc.wait()`` does not
# resolve until every pipe disconnects. Without the grandchild all four
# variations return in 0.00 s and the bug is invisible.
#
# THE GRANDCHILD IS A ``subprocess.Popen``, NOT ``os.fork()`` (#207). The
# property under test — "a descendant still holding fd 1/2 keeps
# ``proc.wait()`` from resolving" — is about INHERITED PIPE HANDLES, not about
# how the descendant came to exist, and ``os.fork`` does not exist on Windows
# at all (measured there: ``RpcServerExited ... AttributeError: module 'os' has
# no attribute 'fork'``, raised out of ``client.start()``, so the stall path
# this file exists to pin was never even entered).
#
# THE ``stdin=0, stdout=1, stderr=2`` IS LOAD-BEARING, do not drop it back to a
# bare ``Popen([...])``. On POSIX the two spell the same thing, but on win32
# they do not, and the bare form would make this test pass VACUOUSLY there.
# Read out of CPython's ``subprocess`` win32 arm: ``_get_handles`` opens with
# ``if stdin is None and stdout is None and stderr is None: return (-1,) * 6``,
# so ``_execute_child`` computes ``use_std_handles = -1 not in (...)`` -> False,
# never sets ``STARTF_USESTDHANDLES``, never builds a ``handle_list``, leaves
# ``close_fds`` True, and calls ``CreateProcess(..., int(not close_fds), ...)``
# — i.e. ``bInheritHandles=FALSE``. The client hands the child ``stdout=PIPE``/
# ``stderr=PIPE``, so those are pipe handles rather than console handles and a
# ``bInheritHandles=FALSE`` grandchild receives none of them: no holder, no
# stall, nothing asserted. Passing the fds as ints instead takes the
# ``isinstance(x, int)`` -> ``msvcrt.get_osfhandle`` -> ``_make_inheritable``
# path, which DOES set ``STARTF_USESTDHANDLES`` and a ``handle_list``. (That
# win32 chain is read from the CPython source, not measured here; the windows
# leg is what will confirm it.)
#
# Measured on darwin, the two forms are interchangeable — ``lsof`` shows the
# grandchild holding fds 0/1/2 as PIPEs either way, and both reproduce the bug
# identically: stubbing the polling ``_await_exit`` back out to
# ``await proc.wait()`` makes ``stop()`` pay the full grace (1.002 s and
# 1.001 s against a 1.000 s grace) while the real product returns in 0.052 s.
# The control matters as much as the fixture: with NO grandchild the regressed
# ``stop()`` still returns in 0.001 s, which is what proves the grandchild is
# the precondition and this test is not passing for free.
#
# IT REPORTS THE PID IT SPAWNED, like ``_ORPHANING_STUB`` below, and every case
# that runs it reaps that pid on the way out. It did not before, and the cost
# was measured: a bare ``pytest -q tests/rpc`` left three orphaned
# ``python -c "import time; time.sleep(30)"`` with ``ppid=1`` for half a minute,
# on ``main`` and on this branch alike (review posix2/F7). The leak is not a
# product bug — ``stop()``'s soft ``SIGTERM`` goes to the ROOT, so a cooperative
# child's leftovers are deliberately not signalled on POSIX (ADR-0238) — which
# is exactly why the CLEANUP has to be the test's own job.
_PIPE_HOLDER_STUB = textwrap.dedent(
    """
    import json, subprocess, sys, time

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=0, stdout=1, stderr=2,
    )
    sys.stdout.write(json.dumps({"type": "grandchild", "pid": child.pid}) + "\\n")
    sys.stdout.flush()

    sys.stderr.write("holder ready\\n")
    sys.stderr.flush()
    for line in sys.stdin:
        pass
    time.sleep(30)
    """
)

# No grandchild at all: the two spy cases below are about what the CLIENT did at
# spawn time, and a pipe-holder would only add a sleeper to clean up.
_IDLE_STUB = textwrap.dedent(
    """
    import sys, time
    sys.stderr.write("idle ready\\n")
    sys.stderr.flush()
    for line in sys.stdin:
        pass
    time.sleep(30)
    """
)

# A stub that survives the soft signal AND leaves a grandchild behind. The
# grandchild takes no group of its own, so on POSIX it sits in the child's
# process group and on win32 it inherits the job — i.e. it is reachable by
# exactly the two mechanisms #202 added and by neither ``proc.kill()`` nor
# ``TerminateProcess``.
#
# DEVNULL on all three streams, unlike ``_PIPE_HOLDER_STUB`` above: this case is
# about the KILL reaching a descendant, and a pipe-holding grandchild would also
# stall the client's stderr pump after the child is gone, which is a different
# defect and would only muddy the timing.
_ORPHANING_STUB = textwrap.dedent(
    """
    import json, signal, subprocess, sys, time

    def seen(*_):
        sys.stderr.write("signal seen\\n")
        sys.stderr.flush()

    for name in ("SIGTERM", "SIGBREAK"):
        s = getattr(signal, name, None)
        if s is not None:
            signal.signal(s, seen)

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sys.stdout.write(json.dumps({"type": "grandchild", "pid": child.pid}) + "\\n")
    sys.stdout.flush()

    while True:
        time.sleep(0.05)
    """
)

_ENV_REPORT_STUB = textwrap.dedent(
    """
    import json, os, sys

    sys.stdout.write(json.dumps({
        "type": "env_report",
        "has_probe": "AELIX_PROBE_KEY" in os.environ,
        "added": os.environ.get("AELIX_ADDED_KEY"),
    }) + "\\n")
    sys.stdout.flush()
    for line in sys.stdin:
        pass
    """
)


def _dying_client(*, code: int, delay: float) -> RpcClient:
    env = dict(os.environ)
    env["STUB_EXIT_CODE"] = str(code)
    env["STUB_EXIT_DELAY"] = str(delay)
    return RpcClient(
        RpcClientOptions(argv=[sys.executable, "-c", _DYING_STUB], env_base=env)
    )


def _taskkill_argv0s() -> tuple[str, str]:
    """The two argv[0]s to try on win32, in the product resolver's order.

    ``System32`` is not always on ``PATH`` and a bare ``taskkill`` then fails
    ``ENOENT`` (Pi #6596/#8560, which is why ``_taskkill_tree`` resolves the
    path at all). A cleanup helper is the worst place to inherit that: its
    ``suppress`` would make the miss invisible and the sleeper would outlive the
    leg anyway (review win-leg/F8).
    """

    root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
    return os.path.join(root, "System32", "taskkill.exe"), "taskkill"


def _reap(pid: int) -> None:
    """Kill ``pid`` if it is still there. Cleanup only — never an assertion.

    Deliberately ROOT-ONLY: a test that leaks a pid must not have its cleanup
    reach for a process GROUP or a job, which is the mechanism under test in
    this file.
    """

    with contextlib.suppress(Exception):
        if pid <= 0 or is_dead_or_zombie(pid):
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
    """Pids the case is responsible for, killed on the way out either way.

    A fixture rather than a ``finally`` because the pid becomes known before the
    first assertion, and a case that fails there has to clean up too.
    """

    pids: list[int] = []
    yield pids
    for pid in pids:
        _reap(pid)


async def _await_reported_grandchild(client: RpcClient, seen: list[dict]) -> int:
    """Block until a stub reports the pid it spawned, and return it."""

    for _ in range(200):
        if seen:
            break
        await asyncio.sleep(0.05)
    assert seen and seen[0].get("type") == "grandchild", (
        f"the stub never reported its grandchild; stderr: {client.get_stderr()!r}"
    )
    return int(seen[0]["pid"])


def _pipe_holder_client(seen: list[dict], **options: Any) -> RpcClient:
    """A client on ``_PIPE_HOLDER_STUB`` whose grandchild report is captured."""

    client = RpcClient(
        RpcClientOptions(argv=[sys.executable, "-c", _PIPE_HOLDER_STUB], **options)
    )
    client.on_event(seen.append)
    return client


# === Child-death detection ==================================================


async def test_a_child_that_dies_mid_wait_fails_fast_not_at_the_budget() -> None:
    """The slow leg: the caller is already waiting when the child dies."""

    client = _dying_client(code=7, delay=0.4)
    await client.start()
    try:
        started = time.monotonic()
        with pytest.raises(RpcServerExited) as excinfo:
            await client.prompt_and_wait("anything", timeout_ms=5000)
        elapsed = time.monotonic() - started
        # The budget was 5 s and the child died at 0.4 s. Anything approaching
        # the budget means the death went unobserved and the timeout fired.
        assert elapsed < 2.0, f"waited {elapsed:.2f}s for a child that died at 0.4s"
        assert excinfo.value.returncode == 7
        # The stderr MUST ride along: an rpc child that dies during startup
        # writes its traceback here and nothing at all to stdout, so this is the
        # only thing that makes the failure diagnosable.
        assert "stub dying now" in excinfo.value.stderr
    finally:
        await client.stop()


async def test_a_child_that_is_already_dead_fails_with_the_same_error() -> None:
    """The fast leg, normalised.

    Before the fix these two legs disagreed by a factor of 5000 AND by type:
    a command issued to an already-dead child raised ``RuntimeError`` in 0.00 s
    off the broken stdin pipe, while one issued moments earlier blocked the full
    budget and raised ``TimeoutError``. One fact about the world, two failures
    a caller had to handle separately — and would only discover separately.
    """

    client = _dying_client(code=3, delay=0.1)
    await client.start()
    try:
        for _ in range(100):
            if client.returncode is not None:
                break
            await asyncio.sleep(0.05)
        assert client.returncode == 3

        started = time.monotonic()
        with pytest.raises(RpcServerExited):
            await client.prompt_and_wait("anything", timeout_ms=5000)
        assert time.monotonic() - started < 2.0
    finally:
        await client.stop()


async def test_start_raises_a_typed_error_when_the_child_dies_in_the_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pi's one-shot grace, now carrying the diagnosis instead of a bare string.

    WHAT IS UNDER TEST is that a death OBSERVED inside the window becomes a
    typed error carrying the child's stderr — not that this particular child
    fits inside 100 ms. The window's own size is pinned by
    :data:`RpcClient.STARTUP_GRACE_MS`'s default and by the callers that consume
    it, so widening it here trades away nothing.

    THE RACE IS NOT GONE, it is only no longer in the test's way.
    ``_watch_for_exit`` POLLS ``returncode`` every ``EXIT_POLL_SECONDS``
    (50 ms), so detection is quantised to {50, 100, …} ms against a 100 ms
    ``wait_for`` — a window exactly one poll tick wide. A child dying at 51 ms
    is pushed to the 100 ms tick and ties the timeout. Measured on a 2-core box:
    idle 52-55 ms (12/12 inside), 2 hogs bimodal at 55/105 ms (6/12 outside),
    6 hogs 103-237 ms (12/12 outside) — an ordinarily busy CI runner, not a
    contrived one. The production one-tick race survives this change untouched;
    the root fix is to race the exit event instead of polling it, deliberately
    out of scope for a test-hygiene repair.

    The ``finally`` is the second half. On the failure path there was no
    teardown at all, so a red run leaked this client's pump and watcher tasks
    into every test after it in the file.
    """

    monkeypatch.setattr(RpcClient, "STARTUP_GRACE_MS", 2_000)
    client = _dying_client(code=9, delay=0.0)
    try:
        with pytest.raises(RpcServerExited) as excinfo:
            await client.start()
        assert excinfo.value.returncode == 9
        assert "stub booting" in excinfo.value.stderr
        # ``RpcServerExited`` is a ``RuntimeError`` subclass precisely so the
        # shape this site used to raise still matches for any existing caller.
        assert isinstance(excinfo.value, RuntimeError)
        assert client.process is None
    finally:
        await client.stop()


# === Containment ============================================================


async def test_the_child_gets_its_own_process_group(strays: list[int]) -> None:
    """Without this one Ctrl+C SIGINTs every rpc child at once.

    Neither an interactive parent nor an rpc child installs a SIGINT handler,
    so nothing would convert that into a response or an envelope.

    The ``skipif`` this used to carry is gone (#202): ``start_new_session`` was
    silently IGNORED on win32 — CPython's ``_execute_child`` names the parameter
    ``unused_start_new_session`` — so there was nothing there to assert. There
    is now, and it is a stronger claim than the POSIX one: a Job Object holds
    every descendant, a process group only the ones that did not ``setsid``.
    Both arms also assert ``contained``, which is what guards the one thing
    ``IsProcessInJob`` alone cannot rule out — the runner already being inside a
    job of its own, whose TRUE would mask a failed assignment.
    """

    seen: list[dict] = []
    client = _pipe_holder_client(seen)
    await client.start()
    try:
        strays.append(await _await_reported_grandchild(client, seen))
        proc = client.process
        assert proc is not None
        tree = client._tree
        assert tree is not None
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
            handle = _retained_handle(proc)
            assert handle is not None
            assert kernel32.IsProcessInJob(handle, tree.job, ctypes.byref(member)) != 0
            assert member.value == 1
        else:
            assert tree.job is None
            assert os.getpgid(proc.pid) != os.getpgid(0)
            assert os.getpgid(proc.pid) == proc.pid
    finally:
        await client.stop()


async def test_stop_ends_a_descendant_the_child_left_behind() -> None:
    """The headline of #202, and it FAILS ON MAIN on every leg.

    ``stop()`` used to be ``proc.terminate()`` then ``proc.kill()`` — both aimed
    at the root and at nothing else — so a delegation aborted mid-turn left every
    descendant of its child running. Measured with ``main`` 39549b9's ``stop()``
    body re-imposed over this one: the grandchild is still ``alive`` when the
    5 s poll below gives up.

    The escalation path is deliberately the subject rather than the success
    path: the child survives the soft signal, so what reaches the grandchild is
    the group kill on POSIX and the job on win32, which is the one shape both
    platforms share. ``close()`` is a release on POSIX and kills nothing there.
    """

    seen: list[dict] = []
    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", _ORPHANING_STUB]))
    client.on_event(seen.append)
    await client.start()
    grandchild = 0
    try:
        for _ in range(200):
            if seen:
                break
            await asyncio.sleep(0.05)
        assert seen and seen[0].get("type") == "grandchild", (
            f"the stub never reported its grandchild; stderr: {client.get_stderr()!r}"
        )
        grandchild = int(seen[0]["pid"])
        # The premise. A grandchild that was already dead would make the
        # assertion below pass for free.
        assert not is_dead_or_zombie(grandchild)

        await client.stop()

        state = await await_dead_or_zombie(grandchild, timeout=5.0)
        assert state in (STATE_GONE, STATE_ZOMBIE), (
            f"the grandchild is {state} five seconds after stop() — the kill "
            "reached the child and stopped there, which is the #202 defect"
        )
    finally:
        # Never leave a 60 s sleeper behind when the assertion above failed.
        if grandchild > 0 and not is_dead_or_zombie(grandchild):
            kill_process_tree(grandchild)


async def test_stop_does_not_wait_out_the_grace_when_a_descendant_holds_the_pipes(
    strays: list[int],
) -> None:
    """The measured stall, pinned.

    ``proc.wait()`` cannot answer "is the child gone" — CPython resolves its
    exit waiters only once every pipe is disconnected, so a grandchild holding
    fd 1/2 kept ``stop()`` blocked for the whole SIGTERM grace on a child that
    had already died. Polling ``returncode`` is the independent signal.

    The grandchild SURVIVES this ``stop()`` on POSIX, by design: the soft signal
    goes to the root and the child takes it, so the group is never signalled and
    ``close()`` is a release. That is the survivor the ``strays`` fixture ends
    (review posix2/F7) — it is cleanup, not an assertion.
    """

    seen: list[dict] = []
    client = _pipe_holder_client(seen)
    await client.start()
    strays.append(await _await_reported_grandchild(client, seen))
    for _ in range(100):
        if "holder ready" in client.get_stderr():
            break
        await asyncio.sleep(0.05)

    started = time.monotonic()
    await client.stop()
    elapsed = time.monotonic() - started
    grace = RpcClient.SHUTDOWN_SIGTERM_TIMEOUT_MS / 1000.0
    assert elapsed < grace, (
        f"stop() took {elapsed:.2f}s; the whole point is that it no longer "
        f"pays the {grace:.2f}s SIGTERM grace for a child that already died"
    )


async def test_the_tree_is_attached_before_the_first_await(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordering invariant four docstrings assert and nothing checked.

    ``ProcessTree.attach`` has to run between ``create_subprocess_exec``
    returning and the first ``await`` after it, because on win32 only
    descendants created AFTER ``AssignProcessToJobObject`` inherit membership —
    a late attach silently contains a DIFFERENT tree. It is invisible on POSIX
    (the pgid is the same either way) and no windows-leg case observes ordering,
    so moving the attach past ``start()``'s 100 ms startup grace left the whole
    suite green (review MUT-5).

    THE PROXY FOR "NO AWAIT HAS HAPPENED" is that nothing ``start()`` schedules
    after the spawn exists yet: the stdout pump and the exit watcher are both
    created below the attach, so both being ``None`` at attach time means no
    suspension point came between.
    """

    real_attach = rpc_client_module.ProcessTree.attach
    at_attach: list[tuple[object, object]] = []

    def _spy(pid: int, **kwargs: Any) -> ProcessTree:
        at_attach.append((client._stdout_reader_task, client._exit_watcher_task))
        return real_attach(pid, **kwargs)

    monkeypatch.setattr(rpc_client_module.ProcessTree, "attach", _spy)

    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_STUB]))
    await client.start()
    try:
        assert at_attach, "ProcessTree.attach was never called by start()"
        reader_task, watcher_task = at_attach[0]
        assert reader_task is None and watcher_task is None, (
            "the tree was attached after a task was scheduled, so an await ran "
            f"between the spawn and the attach: {at_attach[0]!r}"
        )
    finally:
        await client.stop()


async def test_the_rpc_site_asks_for_kill_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``kill_on_close`` is a PER-SITE policy, and this is the site that wants it.

    The flag is pinned as a ``ProcessTree`` parameter by the primitive's own
    tests, but nothing pinned what a CALL SITE passes: flipping this one to
    ``False`` left the whole suite green on both legs (review MUT-6). It is
    load-bearing here because this client owns exactly one child per task — on
    win32 ``CloseHandle(job)`` is then what ends a leftover the cooperative
    ``stop()`` never signalled, and what makes the child die with its parent.
    Subprocess hooks and ``!command`` ask for the opposite and have their own
    pins.
    """

    real_attach = rpc_client_module.ProcessTree.attach
    seen_kwargs: dict[str, Any] = {}

    def _spy(pid: int, **kwargs: Any) -> ProcessTree:
        seen_kwargs.update(kwargs)
        return real_attach(pid, **kwargs)

    monkeypatch.setattr(rpc_client_module.ProcessTree, "attach", _spy)

    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", _IDLE_STUB]))
    await client.start()
    try:
        assert seen_kwargs.get("kill_on_close") is True, (
            "the rpc site stopped asking for kill_on_close; on win32 that is "
            f"the leftovers and the parent-death guarantee. kwargs: {seen_kwargs!r}"
        )
        # The handle seam rides along: passing the one the stdlib already
        # retains is what keeps ``assign`` from re-deriving one with
        # ``OpenProcess`` and being refused ``ACCESS_DENIED``. ``None`` here is
        # correct on POSIX and would be a real defect on win32.
        assert "handle" in seen_kwargs
    finally:
        await client.stop()


# === The injection seams ====================================================


async def test_argv_replaces_the_command_line_outright() -> None:
    """``argv`` wins over ``cli_path`` / ``provider`` / ``model`` / ``args``.

    A caller that renders its own command line has already decided all of them;
    appending would run flags nobody chose.
    """

    client = RpcClient(
        RpcClientOptions(
            argv=[sys.executable, "-c", _PIPE_HOLDER_STUB],
            cli_path="/ignored",
            provider="ignored",
            model="ignored",
            args=["--ignored"],
        )
    )
    assert client._build_argv() == [sys.executable, "-c", _PIPE_HOLDER_STUB]


async def test_env_base_can_DELETE_an_inherited_key() -> None:
    """``env`` alone can only add or overwrite — this is why ``env_base`` exists.

    The key that matters is ``AELIX_MCP_CONFIG``: without a way to strip it,
    every delegated child fans out its own copy of every configured MCP server.
    Passing ``None`` as a value is not a workaround — the key survives with a
    ``None`` value, which ``create_subprocess_exec`` rejects.
    """

    base = {k: v for k, v in os.environ.items() if k != "AELIX_PROBE_KEY"}
    client = RpcClient(
        RpcClientOptions(
            argv=[sys.executable, "-c", _ENV_REPORT_STUB],
            env_base=base,
            env={"AELIX_ADDED_KEY": "added"},
        )
    )
    seen: list[dict] = []
    client.on_event(seen.append)
    os.environ["AELIX_PROBE_KEY"] = "inherited"
    try:
        await client.start()
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.05)
        assert seen, "stub never reported its environment"
        assert seen[0]["has_probe"] is False, "the inherited key was not removed"
        assert seen[0]["added"] == "added"
    finally:
        os.environ.pop("AELIX_PROBE_KEY", None)
        await client.stop()


@pytest.mark.skipif(os.name != "posix", reason="preexec_fn is POSIX-only")
async def test_preexec_fn_runs_in_the_child(
    tmp_path: Path, strays: list[int]
) -> None:
    """The seam that lets a caller install a parent-death signal.

    It has to be a seam rather than a default because the implementation lives
    in the bundled extension, and the import-direction gate (ADR-0197 §(a))
    forbids product-core from reaching into band 3 to get it.
    """

    marker = tmp_path / "preexec-ran"

    def _preexec() -> None:
        marker.write_text("ran")

    seen: list[dict] = []
    client = _pipe_holder_client(seen, preexec_fn=_preexec)
    await client.start()
    try:
        strays.append(await _await_reported_grandchild(client, seen))
        assert marker.exists(), "preexec_fn never ran in the forked child"
    finally:
        await client.stop()


async def test_stderr_cap_is_overridable_per_client() -> None:
    """A delegation wants a far smaller window than the 10 MiB default.

    The envelope's fallback chain only shows a TAIL, and it hands the raw
    capture to an UNCAPPED details field.
    """

    noisy = textwrap.dedent(
        """
        import sys, time
        sys.stderr.write("Z" * 5000 + "\\n")
        sys.stderr.write("TAIL-MARKER\\n")
        sys.stderr.flush()
        for line in sys.stdin:
            pass
        time.sleep(30)
        """
    )
    client = RpcClient(
        RpcClientOptions(argv=[sys.executable, "-c", noisy], stderr_max_bytes=256)
    )
    await client.start()
    try:
        for _ in range(100):
            if "TAIL-MARKER" in client.get_stderr():
                break
            await asyncio.sleep(0.05)
        captured = client.get_stderr()
        # Evicted from the FRONT, so the most-recent context is what survives.
        assert "TAIL-MARKER" in captured
        assert len(captured) <= 256
    finally:
        await client.stop()


# === The per-line framing budget ============================================
#
# The budget is a CONSTRUCTOR ARGUMENT with a ``None`` default, and these tests
# exist to keep it that way. ``_jsonl`` serves BOTH directions of the wire: the
# client reads a child's stdout with it and the SERVER reads its own stdin with
# it. A budget wired in unconditionally therefore also silently swallows
# inbound COMMANDS — for which the server emits no error response, so the
# client's request future never resolves and the caller eats the full 30 s send
# timeout. Measured, that is exactly what happened.


def test_the_line_budget_is_off_by_default() -> None:
    """The server's intake direction must stay unbounded."""

    got: list[str] = []
    reader = JsonlLineReader(got.append)
    reader.feed(b"y" * 500_000 + b"\n")
    assert len(got) == 1
    assert len(got[0]) == 500_000
    assert reader.dropped_lines == 0


def test_an_oversize_line_is_dropped_counted_and_resynced() -> None:
    """Including when it arrives WITH its terminator in one chunk.

    That leg is not redundant: a line whose newline is in the same chunk never
    reaches the un-terminated branch, so an earlier spelling of this budget
    enforced nothing at all for any line shorter than the read chunk.
    """

    got: list[str] = []
    reader = JsonlLineReader(got.append, max_line_bytes=64)
    reader.feed(b"x" * 500 + b'\n{"ok":1}\n')
    assert got == ['{"ok":1}'], "the good record after the oversize one was lost"
    assert reader.dropped_lines == 1


def test_an_oversize_line_split_across_chunks_is_also_dropped() -> None:
    got: list[str] = []
    reader = JsonlLineReader(got.append, max_line_bytes=64)
    for _ in range(10):
        reader.feed(b"x" * 50)
    reader.feed(b'\n{"ok":1}\n')
    assert got == ['{"ok":1}']
    assert reader.dropped_lines == 1


def test_a_stream_that_ends_mid_oversize_line_emits_nothing() -> None:
    """The residue is the tail of a record already counted as dropped."""

    got: list[str] = []
    reader = JsonlLineReader(got.append, max_line_bytes=64)
    reader.feed(b"z" * 500)
    reader.end()
    assert got == []
    assert reader.dropped_lines == 1


async def test_the_client_republishes_the_drop_counter_while_it_runs() -> None:
    """The counter needs a route home or the envelope can never carry it."""

    chatty = textwrap.dedent(
        """
        import sys, time
        sys.stdout.write("Q" * 5000 + "\\n")
        sys.stdout.write('{"type":"marker"}' + "\\n")
        sys.stdout.flush()
        for line in sys.stdin:
            pass
        time.sleep(30)
        """
    )
    seen: list[dict] = []
    client = RpcClient(
        RpcClientOptions(
            argv=[sys.executable, "-c", chatty], stdout_line_max_bytes=256
        )
    )
    client.on_event(seen.append)
    await client.start()
    try:
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.05)
        assert seen and seen[0].get("type") == "marker"
        assert client.dropped_lines == 1
    finally:
        await client.stop()
