"""Dispatch tests for ``aelix_ai.utils._process_tree`` (#202). No real processes.

These are the doctrine-compliant half of #202's coverage: every platform arm
runs on EVERY runner, because the platform is injected (``platform=``) and every
win32 side effect goes through the :class:`_Win32Api` seam rather than through
the operating system. ``tests/process_tree/test_process_tree_real_processes.py``
is the other half — real children, and the win32 legs of it only ever run on
``windows-latest``.

NOTHING HERE MAY SIGNAL ANYTHING. ``os.kill``, ``os.killpg`` and
``subprocess.run`` are replaced with recorders for every case by the autouse
:func:`spies` fixture, so a dispatch bug shows up as a recorded call rather than
as a SIGKILL at whatever pid 4321 happens to be on the runner. It also lends
``os.killpg`` and ``signal.SIGKILL``, which a Windows interpreter does not have
at all — the POSIX arms cannot even be CALLED there without them, and lending
asserts the arm's shape, which is all these cases ever claim about it.
"""

from __future__ import annotations

import gc
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from aelix_ai.utils._process_tree import (
    CREATE_NEW_PROCESS_GROUP,
    CTRL_BREAK_EVENT,
    ProcessTree,
    _KernelApi,
    _Win32Api,
    containment_spawn_kwargs,
    kill_process_tree,
)

#: The same value production and these assertions see. On POSIX it IS
#: ``signal.SIGKILL``; on Windows it is the number POSIX uses and nothing here
#: does anything with it but compare it.
SIGKILL = getattr(signal, "SIGKILL", 9)
SIGTERM = signal.SIGTERM

PID = 4321
HANDLE = 0xBEEF


@dataclass
class Spies:
    """Every signal and shell-out the module could emit, recorded instead."""

    kills: list[tuple[int, int]] = field(default_factory=list)
    killpgs: list[tuple[int, int]] = field(default_factory=list)
    runs: list[list[str]] = field(default_factory=list)

    @property
    def quiet(self) -> bool:
        return not (self.kills or self.killpgs or self.runs)


@pytest.fixture(autouse=True)
def spies(monkeypatch: pytest.MonkeyPatch) -> Spies:
    recorded = Spies()
    monkeypatch.setattr(os, "kill", lambda pid, sig: recorded.kills.append((pid, sig)))
    monkeypatch.setattr(
        os, "killpg", lambda pgid, sig: recorded.killpgs.append((pgid, sig)), raising=False
    )
    monkeypatch.setattr(signal, "SIGKILL", SIGKILL, raising=False)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        # ``check=False`` is load-bearing: taskkill exits non-zero on a pid that
        # is already gone, which is the goal state. ``timeout`` is load-bearing
        # too: the escalation is synchronous and every win32 caller of it is a
        # coroutine, so an un-timed spawn blocks the event loop for as long as
        # ``taskkill.exe`` hangs (review win-leg/F6).
        assert kwargs.get("check") is False
        assert kwargs.get("timeout") == 5
        # All three stdio are DEVNULL, and ``capture_output`` is GONE (#221
        # review WIN-3). ``_taskkill_tree`` used to capture output it then threw
        # away, which is #221's own shape applied to the escalation itself: on
        # win32 CPython follows a timed-out kill with an UNTIMED
        # ``communicate()`` to join the reader threads, so a ``taskkill`` that
        # hit its 5 s bound would go on to block with no bound at all —
        # synchronously, on the event-loop thread since #220. With no pipe there
        # is nothing for that join to wait on.
        assert kwargs.get("stdin") is subprocess.DEVNULL
        assert kwargs.get("stdout") is subprocess.DEVNULL
        assert kwargs.get("stderr") is subprocess.DEVNULL
        assert "capture_output" not in kwargs
        recorded.runs.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return recorded


class FakeApi:
    """:class:`_Win32Api` that records instead of touching kernel32.

    Keyword arguments name a method and give the exception it should raise after
    recording, so the "never raises" and "attach failed" shapes are one class.
    """

    JOB = 0x51D3

    def __init__(self, **errors: BaseException) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._errors = errors

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, *args))
        error = self._errors.get(name)
        if error is not None:
            raise error

    @property
    def names(self) -> list[str]:
        return [call[0] for call in self.calls]

    def create_job(self, *, kill_on_close: bool) -> int:
        self._record("create_job", kill_on_close)
        return self.JOB

    def assign(self, job: int, pid: int, handle: int | None) -> None:
        self._record("assign", job, pid, handle)

    def terminate_job(self, job: int) -> None:
        self._record("terminate_job", job)

    def close_handle(self, job: int) -> None:
        self._record("close_handle", job)

    def ctrl_break(self, pid: int) -> None:
        self._record("ctrl_break", pid)

    def taskkill(self, pid: int) -> None:
        self._record("taskkill", pid)


def _api(**errors: BaseException) -> _Win32Api:
    return cast(_Win32Api, FakeApi(**errors))


def _leads_its_own_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "getpgid", lambda pid: pid, raising=False)


# === the spawn kwargs =======================================================


def test_containment_spawn_kwargs_per_platform_and_session() -> None:
    assert containment_spawn_kwargs(new_session=True, platform="linux") == {
        "start_new_session": True
    }
    # The default is NOT setsid: ``!command`` runs credential helpers that open
    # /dev/tty, and setsid drops the controlling terminal.
    assert containment_spawn_kwargs(platform="linux") == {"process_group": 0}
    assert containment_spawn_kwargs(platform="darwin") == {"process_group": 0}
    for new_session in (False, True):
        assert containment_spawn_kwargs(new_session=new_session, platform="win32") == {
            "creationflags": CREATE_NEW_PROCESS_GROUP
        }


def test_the_literals_match_the_stdlib_names_where_they_exist() -> None:
    """The module spells two win32 constants as numbers; they must be the right ones.

    The names cannot be spelled in product code — they do not exist off Windows
    and the type gate analyses every file as Linux as well — so this is the only
    place the numbers are checked against the interpreter. On the windows leg
    ``getattr`` finds the real thing and this is a real comparison; elsewhere it
    lends the value and the case still asserts the module agrees with itself.
    """

    assert getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x0000_0200) == CREATE_NEW_PROCESS_GROUP
    assert getattr(signal, "CTRL_BREAK_EVENT", 1) == CTRL_BREAK_EVENT


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("pid", [0, -1])
def test_a_non_positive_pid_is_refused_everywhere(platform: str, pid: int, spies: Spies) -> None:
    """0 is not "no process" on Windows — it is every process on our console.

    ``GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, 0)`` broadcasts to the whole
    console group, this interpreter included, so a pid that arrived as 0 or -1
    has to raise rather than be attempted.
    """

    with pytest.raises(ValueError):
        ProcessTree.attach(pid, platform=platform, api=_api())
    with pytest.raises(ValueError):
        kill_process_tree(pid, platform=platform)
    assert spies.quiet


# === the win32 arm ==========================================================


def test_win32_attach_creates_a_job_and_assigns_the_handle(spies: Spies) -> None:
    api = FakeApi()

    tree = ProcessTree.attach(PID, handle=HANDLE, platform="win32", api=cast(_Win32Api, api))

    assert (tree.contained, tree.job) == (True, FakeApi.JOB)
    assert api.calls == [("create_job", False), ("assign", FakeApi.JOB, PID, HANDLE)]

    tree.hard_kill()
    assert api.names[-2:] == ["taskkill", "terminate_job"]

    tree.close()
    assert api.names[-1] == "close_handle"
    assert tree.job is None

    settled = list(api.calls)
    tree.close()
    assert api.calls == settled  # idempotent: the handle is released once
    assert spies.quiet


def test_win32_attach_without_a_handle_assigns_by_pid() -> None:
    """No retained handle -> the api derives one from the pid with OpenProcess.

    ``tree`` is bound rather than discarded on purpose: an unreferenced tree is
    collected mid-test and its finalizer appends a ``close_handle``, which is
    the behaviour the next case is about.
    """

    api = FakeApi()

    tree = ProcessTree.attach(PID, platform="win32", api=cast(_Win32Api, api))

    assert tree.contained is True
    assert api.calls == [("create_job", False), ("assign", FakeApi.JOB, PID, None)]


def test_win32_an_abandoned_tree_still_releases_its_handle() -> None:
    """``CreateJobObjectW(NULL, NULL)`` hands back the only handle there will be.

    The stdlib passes a child only the handles listed in
    ``PROC_THREAD_ATTRIBUTE_HANDLE_LIST``, so nothing else holds a reference and
    a caller that drops the tree without ``close()`` would leak a kernel object
    for the life of the process. ``weakref.finalize`` is the backstop.
    """

    api = FakeApi()

    ProcessTree.attach(PID, handle=HANDLE, platform="win32", api=cast(_Win32Api, api))
    gc.collect()

    assert api.names[-1] == "close_handle"


@pytest.mark.parametrize("kill_on_close", [False, True])
def test_win32_kill_on_close_is_passed_to_create_job(kill_on_close: bool) -> None:
    """The per-site decision: only the rpc child asks for KILL_ON_JOB_CLOSE."""

    api = FakeApi()

    tree = ProcessTree.attach(
        PID, kill_on_close=kill_on_close, platform="win32", api=cast(_Win32Api, api)
    )

    assert tree.contained is True
    assert api.calls[0] == ("create_job", kill_on_close)


@pytest.mark.parametrize("failing", ["create_job", "assign"])
def test_win32_attach_failure_falls_back_to_taskkill(failing: str, spies: Spies) -> None:
    """A refused job must not raise into a spawn — it degrades to taskkill."""

    api = FakeApi(**{failing: OSError("the runner refused")})

    tree = ProcessTree.attach(PID, handle=HANDLE, platform="win32", api=cast(_Win32Api, api))

    assert (tree.contained, tree.job) == (False, None)
    # A job that was created before the failure is released immediately; one
    # that was never created has nothing to release.
    assert ("close_handle" in api.names) is (failing == "assign")

    settled = list(api.calls)
    tree.hard_kill()
    assert api.names[len(settled) :] == ["taskkill"]  # no terminate_job: no job

    settled = list(api.calls)
    tree.close()
    assert api.calls == settled
    assert spies.quiet


def test_win32_terminate_failure_still_ran_taskkill_first() -> None:
    """Order is the belt for the assignment window (spec B.4), so it is pinned.

    ``taskkill /T`` runs while the root is still alive, so it can walk to a
    descendant that was spawned between CreateProcess and the assignment; the
    job runs second and catches everything else. A failing TerminateJobObject
    must not undo the first half, nor propagate.
    """

    api = FakeApi(terminate_job=OSError("job already gone"))

    tree = ProcessTree.attach(PID, handle=HANDLE, platform="win32", api=cast(_Win32Api, api))
    tree.hard_kill()

    assert api.names[-2:] == ["taskkill", "terminate_job"]


def test_win32_soft_kill_sends_ctrl_break_to_the_group(spies: Spies) -> None:
    api = FakeApi()
    tree = ProcessTree.attach(PID, handle=HANDLE, platform="win32", api=cast(_Win32Api, api))

    assert tree.soft_kill() is True
    # A win32 console event is group-wide by construction, so ``whole_group``
    # is accepted and ignored rather than being a second code path.
    assert tree.soft_kill(whole_group=True) is True

    assert api.names[-2:] == ["ctrl_break", "ctrl_break"]
    assert api.calls[-1] == ("ctrl_break", PID)
    assert spies.quiet


def test_win32_soft_kill_reports_a_console_event_it_could_not_send(spies: Spies) -> None:
    """``GenerateConsoleCtrlEvent`` fails when the process has no console.

    ``os.kill(pid, CTRL_BREAK_EVENT)`` then raises, and before ``soft_kill``
    returned anything that failure was indistinguishable from a child ignoring
    the signal: the caller paid its whole grace for an event nobody received
    (review win-leg/F2, which enumerated six tests that would have gone red on a
    console-less runner with no way to say why). ``False`` means "nothing was
    sent, there is nothing to wait for" — the caller escalates instead.
    """

    quiet = FakeApi(ctrl_break=OSError("no console to send it on"))
    tree = ProcessTree.attach(PID, handle=HANDLE, platform="win32", api=cast(_Win32Api, quiet))

    assert tree.soft_kill() is False  # must not raise, either
    assert quiet.names[-1] == "ctrl_break"
    assert spies.quiet


def test_win32_never_touches_killpg_or_sigkill(monkeypatch: pytest.MonkeyPatch) -> None:
    """The #105 crash, re-asserted for the new surface.

    Deleting the three names reproduces a Windows interpreter closely enough to
    prove no win32 path references any of them — including the new ``attach``,
    ``soft_kill``, ``hard_kill`` and ``close``.
    """

    monkeypatch.delattr(os, "killpg", raising=False)
    monkeypatch.delattr(os, "getpgid", raising=False)
    monkeypatch.delattr(signal, "SIGKILL", raising=False)
    api = FakeApi()

    tree = ProcessTree.attach(
        PID, kill_on_close=True, handle=HANDLE, platform="win32", api=cast(_Win32Api, api)
    )
    tree.soft_kill()
    tree.soft_kill(whole_group=True)
    tree.hard_kill()
    tree.close()
    kill_process_tree(PID, platform="win32")


def test_win32_without_an_injected_api_refuses_off_windows(
    monkeypatch: pytest.MonkeyPatch, spies: Spies
) -> None:
    """``platform="win32"`` on a POSIX host must raise, not emit SIGHUP.

    ``CTRL_BREAK_EVENT`` and ``SIGHUP`` are both 1, so a win32 arm that fell
    through to ``os.kill`` on a POSIX box would hang up on a real process. The
    real seam refuses to be constructed there, and that refusal is the guard.

    Driven through ``sys.platform`` rather than the runner's own so the case
    means the same thing on ``windows-latest``.
    """

    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(RuntimeError):
        ProcessTree.attach(PID, platform="win32")
    assert spies.quiet


@pytest.mark.parametrize("system_root", ["D:\\WinDir", None])
def test_win32_taskkill_is_resolved_from_systemroot_and_retries_the_bare_name(
    system_root: str | None, monkeypatch: pytest.MonkeyPatch, spies: Spies
) -> None:
    """Pi #6596: a bare ``taskkill`` is ENOENT when System32 is not on PATH.

    Pi fixed it in ``7af2d27d`` by joining ``%SystemRoot%``; ours does the same
    and keeps the bare name as the retry, so a wrong ``SystemRoot`` degrades to
    the old behaviour instead of to a silent no-kill. The body is platform-free,
    so it is driven directly (the class it lives on is windows-only).
    """

    if system_root is None:
        monkeypatch.delenv("SYSTEMROOT", raising=False)
        expected_root = r"C:\Windows"
    else:
        monkeypatch.setenv("SYSTEMROOT", system_root)
        expected_root = system_root
    resolved = os.path.join(expected_root, "System32", "taskkill.exe")

    _KernelApi.taskkill(cast(Any, None), PID)

    assert spies.runs == [[resolved, "/T", "/F", "/PID", str(PID)]]

    def missing(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        spies.runs.append(list(argv))
        if argv[0] == resolved:
            raise FileNotFoundError(argv[0])
        return subprocess.CompletedProcess(list(argv), 0)

    monkeypatch.setattr(subprocess, "run", missing)
    spies.runs.clear()

    _KernelApi.taskkill(cast(Any, None), PID)

    assert spies.runs == [
        [resolved, "/T", "/F", "/PID", str(PID)],
        ["taskkill", "/T", "/F", "/PID", str(PID)],
    ]


# === the POSIX arm ==========================================================


def test_posix_attach_verifies_group_leadership(
    monkeypatch: pytest.MonkeyPatch, spies: Spies
) -> None:
    _leads_its_own_group(monkeypatch)

    tree = ProcessTree.attach(PID, platform="linux")

    assert (tree.contained, tree.job) == (True, None)

    tree.hard_kill()
    assert (spies.killpgs, spies.kills) == ([(PID, SIGKILL)], [])

    # close() is a RELEASE on POSIX. Revision 1 signalled here and killed
    # helpers that hooks had deliberately backgrounded and exited 0 over.
    tree.close()
    assert (spies.killpgs, spies.kills, spies.runs) == ([(PID, SIGKILL)], [], [])


def test_posix_a_child_in_our_own_group_is_never_killpg_d(
    monkeypatch: pytest.MonkeyPatch, spies: Spies
) -> None:
    """The safety case: a caller that forgot the spawn kwargs must not kill US.

    A child that leads no group of its own is in the caller's group, which holds
    this interpreter and every sibling it spawned. ``contained`` false means both
    kills stay root-only, ``whole_group=True`` included.
    """

    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1000, raising=False)

    tree = ProcessTree.attach(PID, platform="linux")

    assert tree.contained is False

    tree.hard_kill()
    assert tree.soft_kill(whole_group=True) is True  # sent, just not to a group

    assert spies.kills == [(PID, SIGKILL), (PID, SIGTERM)]
    assert spies.killpgs == []


def test_posix_a_zombie_leader_still_attaches_to_its_own_group(
    monkeypatch: pytest.MonkeyPatch, spies: Spies
) -> None:
    """Darwin raises ProcessLookupError for a zombie leader whose group is alive.

    Measured on the owner's own box, which is why "getpgid failed, so stay
    root-only" was false: it refused containment in exactly the case containment
    exists for. The caller passed the spawn kwargs, so the group is the pid.
    """

    def gone(_pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", gone, raising=False)

    tree = ProcessTree.attach(PID, platform="linux")

    assert tree.contained is True

    tree.hard_kill()
    assert (spies.killpgs, spies.kills) == ([(PID, SIGKILL)], [])


def test_posix_soft_kill_root_vs_group(monkeypatch: pytest.MonkeyPatch, spies: Spies) -> None:
    """The hook site asks for the group: a shell forwards nothing to its pipeline."""

    _leads_its_own_group(monkeypatch)
    tree = ProcessTree.attach(PID, platform="linux")

    assert tree.soft_kill() is True
    assert (spies.kills, spies.killpgs) == ([(PID, SIGTERM)], [])

    assert tree.soft_kill(whole_group=True) is True
    assert (spies.kills, spies.killpgs) == ([(PID, SIGTERM)], [(PID, SIGTERM)])


@pytest.mark.parametrize("whole_group", [False, True])
def test_posix_soft_kill_reports_a_signal_it_could_not_send(
    whole_group: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ESRCH`` is "there was nobody to signal", not "the signal is pending".

    Same contract as the win32 console arm: the caller learns that its grace has
    nothing to wait for and escalates rather than sleeping through it.
    """

    def gone(*_args: Any) -> None:
        raise ProcessLookupError

    _leads_its_own_group(monkeypatch)
    tree = ProcessTree.attach(PID, platform="linux")
    monkeypatch.setattr(os, "kill", gone)
    monkeypatch.setattr(os, "killpg", gone, raising=False)

    assert tree.soft_kill(whole_group=whole_group) is False


def test_posix_kill_process_tree_falls_back_to_the_pid_as_pgid(
    monkeypatch: pytest.MonkeyPatch, spies: Spies
) -> None:
    """Same Darwin zombie shape, on the pid-only entry point.

    ``bash.py``'s two call sites run before any reap, so there the zombie really
    is still pinning the number its group is named after;
    ``_subprocess.py::run_cancellable`` runs AFTER asyncio's watcher has reaped
    the child (measured, review posix2/F1). What bounds the fallback either way
    is that it addresses only the group number the spawn asked for — pinned
    while any member lives, ``ESRCH`` when the group is already empty.
    """

    def gone(_pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", gone, raising=False)

    kill_process_tree(PID, platform="linux")

    assert (spies.killpgs, spies.kills, spies.runs) == ([(PID, SIGKILL)], [], [])


# === lifecycle ==============================================================


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_every_method_is_a_no_op_after_close(
    platform: str, monkeypatch: pytest.MonkeyPatch, spies: Spies
) -> None:
    _leads_its_own_group(monkeypatch)
    api = FakeApi() if platform == "win32" else None
    tree = ProcessTree.attach(
        PID,
        handle=HANDLE,
        platform=platform,
        api=cast(_Win32Api, api) if api is not None else None,
    )

    tree.close()
    settled = list(api.calls) if api is not None else []

    # A closed tree sent nothing, so it reports nothing sent — the caller has no
    # grace to pay for a signal that was never emitted.
    assert tree.soft_kill() is False
    assert tree.soft_kill(whole_group=True) is False
    tree.hard_kill()
    tree.close()

    assert (list(api.calls) if api is not None else []) == settled
    assert spies.quiet


@pytest.mark.parametrize("error", [OSError("x"), PermissionError("x"), ProcessLookupError("x")])
def test_never_raises(error: OSError, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every method is called from an abort or timeout path.

    An exception there masks the cause the caller was already handling, so the
    contract is best-effort on both platforms and every underlying failure —
    the kernel32 seam's and the POSIX signals' — is swallowed.
    """

    holding = FakeApi(terminate_job=error, close_handle=error, ctrl_break=error, taskkill=error)
    tree = ProcessTree.attach(PID, handle=HANDLE, platform="win32", api=cast(_Win32Api, holding))
    assert tree.soft_kill() is False  # swallowed AND reported, not swallowed only
    tree.hard_kill()
    tree.close()

    refused = ProcessTree.attach(PID, platform="win32", api=_api(create_job=error))
    assert refused.contained is False
    # A refused JOB is not a refused console: the group the spawn's
    # CREATE_NEW_PROCESS_GROUP made still exists, so the soft signal is still
    # sendable and still reported as sent.
    assert refused.soft_kill() is True
    refused.hard_kill()
    refused.close()

    def raise_it(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    _leads_its_own_group(monkeypatch)
    monkeypatch.setattr(os, "kill", raise_it)
    monkeypatch.setattr(os, "killpg", raise_it, raising=False)

    posix = ProcessTree.attach(PID, platform="linux")
    assert posix.soft_kill() is False
    assert posix.soft_kill(whole_group=True) is False
    posix.hard_kill()
    posix.close()
    kill_process_tree(PID, platform="linux")

    monkeypatch.setattr(os, "getpgid", raise_it, raising=False)
    ProcessTree.attach(PID, platform="linux").hard_kill()
    kill_process_tree(PID, platform="linux")

    monkeypatch.setattr(subprocess, "run", raise_it)
    kill_process_tree(PID, platform="win32")
