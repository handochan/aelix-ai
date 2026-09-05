"""Containment for a child this process spawned, and everything under it (#202).

THE BUG THIS EXISTS FOR. Every teardown in this repo used to signal the *root*
of a spawn and call the job done. Measured on ``main`` 39549b9: a timed-out
subprocess hook running ``sh -c "sleep 6 | cat"`` left ``sh``, ``sleep`` and
``cat`` running (the shell ``exec``s a single command, so ``sh -c "sleep 5"``
looked fine and hid it); an aborted rpc delegation left every descendant of the
child; and on Windows the root's ``TerminateProcess`` orphaned the command
``cmd.exe /c`` had launched, every time. ADR-0238.

MECHANISM, PER PLATFORM

POSIX — the tree is **the process group we asked the spawn to create**.
:func:`containment_spawn_kwargs` supplies either ``start_new_session=True``
(``setsid``: new session, new group, controlling terminal dropped) or
``process_group=0`` (Python >= 3.11; ``setpgid(0, 0)`` in the child: a new group
inside the SAME session, so the terminal survives). The second is what
``!command`` credential helpers need — ``gpg``/``pass``/pinentry open
``/dev/tty``, and under a real pty a ``setsid`` child answers
``sh: /dev/tty: Device not configured``. Killing is ``killpg`` on the pgid
captured at attach.

win32 — the tree is a **Job Object**. ``subprocess`` silently IGNORES
``start_new_session`` there (CPython's ``_execute_child`` names the parameter
``unused_start_new_session``), so there is no group to aim a kill at; a Windows
process group is a *console-event address*, not a kill unit. The child is
spawned with ``CREATE_NEW_PROCESS_GROUP`` so a soft ``CTRL_BREAK_EVENT`` has
somewhere to land, and is assigned to a job immediately after spawn. Children
of a job member inherit membership (we never set ``BREAKAWAY_OK``).

THE ASYMMETRY, STATED RATHER THAN PAPERED OVER. A job holds every descendant,
including one whose parent has already exited and one that made itself a
session leader. A process group holds neither of the first kind's cousins nor
**any descendant that called ``setsid`` itself** — which is every tool child
(``bash.py``, ``_subprocess.py``) and every MCP stdio server (the SDK spawns them
with ``start_new_session=True``). A hook shell and a ``!command`` shell are NOT
in that list: each is spawned with ``process_group=0`` — its own group inside the
SAME session, tty kept — so it is outside an rpc child's group as well, without
being a session leader. So Windows containment here is strictly STRONGER than
POSIX's. That is by design: the descendant *walk* which reaches a ``setsid``
grandchild is ``aelix_agents/reaper.py``'s job (ADR-0197 finding I2) and this
module does not re-adopt ``killpg`` as a substitute for it.

``close()`` IS A RELEASE, NOT A KILL. On POSIX it signals nothing, ever —
revision 1 sent ``killpg(SIGKILL)`` from it and that killed helpers a hook had
deliberately backgrounded and exited 0 over. On win32 it is ``CloseHandle(job)``,
which ends the remaining members only for a tree attached with
``kill_on_close=True``. That is a PER-SITE choice: ``True`` for the rpc child
("one child per task", and it already dies with the parent on Linux via
``pdeathsig``), ``True`` for the print-mode delegation child too (#220 — same
two reasons: one child per task, so releasing the tree IS being done with it,
and on Windows the closing handle is the only parent-death backstop there is),
``False`` for subprocess hooks and ``!command``, which keep today's
success-path behaviour on every platform.

THE ASSIGNMENT WINDOW. Between ``CreateProcess`` and ``AssignProcessToJobObject``
the child runs unheld. On the asyncio spawn the window spans the transport's
pipe-connection round trips — measured on POSIX at 0.3 ms idle, 1.05 ms median
and 1.65 ms max under load, against a shell that reaches its first ``fork`` at
~2 ms; unmeasured on the proactor loop. ``CREATE_SUSPENDED`` cannot close it
(``Popen`` closes the thread handle before returning). Belt and braces:
:meth:`ProcessTree.hard_kill` runs ``taskkill /T /F`` FIRST, while the root is
still alive, so ``/T`` can walk to an escapee through its live parent link, and
``TerminateJobObject`` second.

THE PID/PGID HAZARD, BOUNDED PER SITE. ``soft_kill`` is always guarded by the
caller's ``returncode is None`` with no ``await`` in between. ``hard_kill`` on
the escalation path is deliberately NOT guarded by root liveness — leader dead
and descendants alive is the exact shape the group kill exists for. The bound on
a stale number, stated as measured rather than as hoped: on the asyncio sites
(rpc, hooks, and since #220 the delegation reaper's escalation and
``PrintChannel._eager_abort``) ``returncode is None`` does NOT prove the pid is
unreaped.
asyncio's child watcher calls ``waitpid`` on its own thread and a LATER loop
callback copies the status into ``returncode`` — measured: a loop blocked for
1.5 s still read ``returncode is None`` for a pid the kernel had already
released. So the honest window there is (watcher reap -> loop callback latency)
plus the grace, not a 50 ms poll quantum. ``!command`` is the one site where the
literal claim holds: a synchronous ``Popen`` nothing has waited on, so the zombie
pins the number. What actually bounds all three is the GROUP, not the pid: a
non-empty group's id cannot be reused while any member lives (POSIX.1 §3.293;
Linux holds it through ``attach_pid(PIDTYPE_PGID)``, BSD likewise), and an empty
one has nothing to kill — ``killpg`` answers ``ESRCH``. The residual case, the
number recycled AND its new holder having made itself a group leader inside that
window, is accepted and stated in ADR-0238. On win32 with a job there is no pid
hazard at all: the handle names the job.

WHY ctypes AND NOT pywin32. The MCP SDK does the same thing through ``win32job``
(``mcp/os/win32/utilities.py``) and the constants below are cross-checked against
it. ``pywin32`` arrives in this environment only as a dependency of ``mcp``, and
``aelix-ai`` is the bottom of the import direction and must not take that
dependency (AGENTS.md §1). ``import ctypes`` at module top is safe everywhere;
``ctypes.WinDLL`` is reached lazily, inside :class:`_KernelApi`.

WHAT RUNS WHERE. The win32 dispatch is exercised from any host through the
injected ``platform=`` and the :class:`_Win32Api` seam. The kernel32 and
``taskkill.exe`` bodies run for real only on the ``windows-latest`` leg —
``tests/process_tree/test_process_tree_real_processes.py`` executes
``taskkill.exe`` there, which the ``tools/`` predecessor of this module (#105)
never did anywhere.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import subprocess
import sys
import weakref
from collections.abc import Callable
from typing import Any, Protocol

__all__ = [
    "ProcessTree",
    "_retained_handle",
    "containment_spawn_kwargs",
    "kill_process_tree",
]

#: ``subprocess.CREATE_NEW_PROCESS_GROUP``. Spelled as a literal because the
#: name exists only on Windows and product code is type-checked as Linux too.
CREATE_NEW_PROCESS_GROUP = 0x0000_0200
#: ``signal.CTRL_BREAK_EVENT``, same reason. The value collides with POSIX
#: ``SIGHUP``, which is why it is only ever passed to ``os.kill`` from
#: :class:`_KernelApi` — a class that cannot be constructed off Windows.
CTRL_BREAK_EVENT = 1

# kernel32 constants, cross-checked against the MCP SDK's win32job use.
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


def containment_spawn_kwargs(
    *, new_session: bool = False, platform: str | None = None
) -> dict[str, Any]:
    """The kwargs a spawn passes so its child is a tree this module can end.

    POSIX with ``new_session`` -> ``{"start_new_session": True}``: ``setsid``, a
    new session, and no controlling terminal. Without it -> ``{"process_group":
    0}``: ``setpgid(0, 0)``, a new group in the SAME session, so a credential
    helper behind ``!command`` can still open ``/dev/tty``. ``killpg`` reaches
    both identically.

    win32 -> ``{"creationflags": CREATE_NEW_PROCESS_GROUP}`` either way: the
    group is the address :meth:`ProcessTree.soft_kill`'s console event needs,
    and the containment proper is the job attached afterwards.

    A caller that already passes ``creationflags`` must OR them into these;
    none does today.
    """

    if _resolve_platform(platform) == "win32":
        return {"creationflags": CREATE_NEW_PROCESS_GROUP}
    if new_session:
        return {"start_new_session": True}
    return {"process_group": 0}


def _retained_handle(proc: Any) -> int | None:
    """The process handle the stdlib is ALREADY holding for ``proc``, or ``None``.

    ``AssignProcessToJobObject`` needs a handle, and deriving one from a pid with
    ``OpenProcess`` can be refused (``ACCESS_DENIED``) for a process we are
    perfectly entitled to contain. Both spawn shapes retain one: ``Popen._handle``
    on win32, and for an ``asyncio.subprocess.Process`` the same field on the
    ``Popen`` its transport keeps (``get_extra_info("subprocess")``).

    Returns ``None`` on POSIX, where neither object has the attribute — there is
    no handle to have, and the pgid is the address instead. Every failure is
    swallowed: this only ever *improves* an ``attach``.
    """

    with contextlib.suppress(Exception):
        handle = getattr(proc, "_handle", None)
        if handle is None:
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                handle = getattr(transport.get_extra_info("subprocess"), "_handle", None)
        if handle is not None:
            return int(handle)
    return None


def _resolve_platform(platform: str | None) -> str:
    """``platform`` or :data:`sys.platform`, read at CALL time.

    Read late on purpose: the tests drive both arms by patching ``sys.platform``
    on the module object, which a value captured at import would not see.
    """

    return platform if platform is not None else sys.platform


def _require_pid(pid: int) -> None:
    """A pid must be positive — on win32 a group id of 0 is a console broadcast.

    ``GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, 0)`` addresses *every* process
    sharing our console, the interpreter included. A caller that reaches here
    with 0 or -1 has a bug worth raising for, not a kill worth attempting.
    """

    if pid <= 0:
        raise ValueError(f"not a process id: {pid!r}")


# ---------------------------------------------------------------------------
# the win32 seam
# ---------------------------------------------------------------------------


class _Win32Api(Protocol):
    """Every win32 side effect this module can have, behind one injection point.

    Dispatch is then testable from a POSIX box without emitting a real signal —
    and, because :class:`_KernelApi` refuses to be constructed off Windows, an
    injected ``platform="win32"`` with no api raises instead of quietly sending
    ``SIGHUP`` (``CTRL_BREAK_EVENT`` and ``SIGHUP`` are both 1).
    """

    def create_job(self, *, kill_on_close: bool) -> int: ...

    def assign(self, job: int, pid: int, handle: int | None) -> None: ...

    def terminate_job(self, job: int) -> None: ...

    def close_handle(self, job: int) -> None: ...

    def ctrl_break(self, pid: int) -> None: ...

    def taskkill(self, pid: int) -> None: ...


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    """64 bytes on x64, ``LimitFlags`` at offset 16 (verified with ctypes)."""

    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    )


class _IO_COUNTERS(ctypes.Structure):
    """48 bytes. Present only because the extended struct embeds it."""

    _fields_ = (
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    )


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    """144 bytes on x64. ``SIZE_T`` and ``ULONG_PTR`` are both ``c_size_t``."""

    _fields_ = (
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


def _load_kernel32() -> tuple[Any, Callable[[], int]]:
    """The real ``kernel32`` with explicit signatures, plus ``get_last_error``.

    Both suppressions are about the CHECKER, not about doubt at runtime:
    ``WinDLL`` and ``get_last_error`` exist only on windows, and the type gate
    analyses this file as Linux as well. :class:`_KernelApi` refuses to exist
    elsewhere; pyright cannot narrow into a separate function, so it is told.

    Every ``restype`` is set because a ``HANDLE`` is pointer-sized and ctypes'
    default ``c_int`` would truncate it on Win64 (the idiom is
    ``tests/process_probe.py::_load_kernel32``'s).
    """

    kernel32 = ctypes.WinDLL(  # pyright: ignore[reportAttributeAccessIssue]
        "kernel32", use_last_error=True
    )
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateJobObject.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32, ctypes.get_last_error  # pyright: ignore[reportAttributeAccessIssue]


class _KernelApi:
    """:class:`_Win32Api` implemented against ``kernel32``.

    Constructible ONLY on Windows. That refusal is the safety property: it makes
    an injected ``platform="win32"`` on a POSIX host a ``RuntimeError`` rather
    than an ``os.kill(pid, 1)``, i.e. a ``SIGHUP`` at a real process.
    """

    # Declared, not merely assigned: under ``--pythonplatform Linux`` pyright
    # narrows the guard below to "always raises" and the assignments become
    # unreachable, so without these the whole class reads as attribute-less.
    _kernel32: Any
    _last_error: Callable[[], int]

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError(
                "_KernelApi is windows-only — inject an api to drive the win32 arm elsewhere"
            )
        self._kernel32, self._last_error = _load_kernel32()

    def create_job(self, *, kill_on_close: bool) -> int:
        # ``CreateJobObjectW(NULL, NULL)``: unnamed, and the handle it returns is
        # NON-inheritable. The stdlib passes only the handles listed in
        # ``PROC_THREAD_ATTRIBUTE_HANDLE_LIST`` to a child, so ours is the last
        # handle and ``close()`` really is the release.
        job = self._kernel32.CreateJobObjectW(None, None)
        if not job:
            raise self._last_os_error("CreateJobObjectW")
        if not kill_on_close:
            return int(job)
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = self._kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            error = self._last_os_error("SetInformationJobObject")
            with contextlib.suppress(OSError):
                self.close_handle(int(job))
            raise error
        return int(job)

    def assign(self, job: int, pid: int, handle: int | None) -> None:
        if handle is not None:
            if not self._kernel32.AssignProcessToJobObject(job, handle):
                raise self._last_os_error("AssignProcessToJobObject")
            return
        # Only reached when the caller had no retained handle. The mask is the
        # SDK's: the two rights the assignment itself needs, nothing broader,
        # so a process we may contain but not inspect is still assignable.
        opened = self._kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not opened:
            raise self._last_os_error("OpenProcess")
        try:
            if not self._kernel32.AssignProcessToJobObject(job, opened):
                raise self._last_os_error("AssignProcessToJobObject")
        finally:
            self._kernel32.CloseHandle(opened)

    def terminate_job(self, job: int) -> None:
        if not self._kernel32.TerminateJobObject(job, 1):
            raise self._last_os_error("TerminateJobObject")

    def close_handle(self, job: int) -> None:
        if not self._kernel32.CloseHandle(job):
            raise self._last_os_error("CloseHandle")

    def ctrl_break(self, pid: int) -> None:
        # The one signal value CPython's ``os_kill_impl`` routes to
        # ``GenerateConsoleCtrlEvent`` instead of ``TerminateProcess``, which is
        # what makes it the only *catchable* stop signal on Windows. It needs a
        # shared console and a ``CREATE_NEW_PROCESS_GROUP`` child; without them
        # it fails and the caller's grace elapses into ``hard_kill``.
        os.kill(pid, CTRL_BREAK_EVENT)

    def taskkill(self, pid: int) -> None:
        _taskkill_tree(pid)

    def _last_os_error(self, call: str) -> OSError:
        return OSError(f"{call} failed (GetLastError={self._last_error()})")


def _taskkill_tree(pid: int) -> None:
    """``taskkill /T /F /PID`` — the fallback for a tree with no job handle.

    ``/T`` is load-bearing: without it only the named process dies. It follows
    LIVE parent links only, which is why it is a fallback and not the mechanism
    (Pi #9129: MSYS bash runs each pipeline stage through a short-lived
    subshell, so the leaves have a dead parent by the time the kill runs;
    ``taskkill`` reports success and the leaves run on).

    argv[0] is resolved from ``%SystemRoot%`` because ``System32`` is not always
    on ``PATH`` and a bare name then fails ``ENOENT`` — Pi hit exactly this
    (#6596/#8560) and fixed it the same way in ``7af2d27d``. A wrong
    ``SystemRoot`` retries the bare name, so the worst case degrades to the old
    behaviour rather than to silence.

    THIS BLOCKS THE CALLER for the length of a process spawn, and the wait is
    the CALLER's — there is no thread and no deferral here. It used to be true
    that every win32 caller of :meth:`ProcessTree.hard_kill` was a coroutine
    (``RpcClient.stop``, the hook timeout ladder, the hook cancellation path),
    so the wait was merely the event loop's between two ``await`` points. #220 added
    SYNCHRONOUS callers on the second-Ctrl+C path — ``PrintChannel._eager_abort``
    and ``RpcChannel._eager_abort`` are ``@staticmethod``s called from inside
    ``except asyncio.CancelledError`` handlers — so this now runs a process
    spawn to completion on the event-loop thread. Accepted deliberately: the
    same cost is already paid on the loop by ``RpcClient.stop`` and the hook
    timeout ladder, and the alternative to a bounded stall is a leaked tree.
    It is bounded at 5 s per attempt by ``timeout=``: a ``taskkill.exe`` that
    never returns must cost a stalled escalation, not a frozen loop.
    ``TimeoutExpired`` is a :exc:`subprocess.SubprocessError` and not an
    :exc:`OSError`, so it is caught by name alongside it. The real duration is
    unmeasured — #220's windows leg is the first place a stopwatch around it
    costs nothing.
    """

    # Windows' environment block is case-insensitive and CPython upper-cases
    # every key in ``os.environ`` there, so this is the same variable Pi reads
    # as ``SystemRoot``; the uppercase spelling is the one that is also a real
    # lookup on the POSIX box the argv cases are driven from.
    system_root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
    for argv0 in (os.path.join(system_root, "System32", "taskkill.exe"), "taskkill"):
        try:
            # ``check=False``: taskkill exits non-zero when the pid is already
            # gone, which is the goal state, not an error.
            subprocess.run(
                [argv0, "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except FileNotFoundError:
            continue
        except (OSError, subprocess.SubprocessError):
            return
        return


def _release_job(api: _Win32Api, job: int) -> None:
    """``CloseHandle``, best effort. Also the ``weakref.finalize`` callback."""

    with contextlib.suppress(OSError):
        api.close_handle(job)


# ---------------------------------------------------------------------------
# the tree
# ---------------------------------------------------------------------------


class ProcessTree:
    """One child this process just spawned, and everything descended from it.

    ATTACH IMMEDIATELY after the spawn call returns, before any ``await``: on
    win32 the job is assigned at that moment and only descendants created AFTER
    the assignment inherit membership (§B.4 of the #202 spec; ADR-0238).

    The pid must be one THIS process just spawned, passing
    :func:`containment_spawn_kwargs`. Nothing else is safe to ``killpg``, and
    :meth:`attach` verifies it: a child that leads no group of its own is in
    *our* group, so it attaches with ``contained`` false and every kill stays
    root-only rather than reaching the caller's own siblings.
    """

    #: The child's pid, as returned by the spawn.
    pid: int
    #: POSIX: the child leads the group we asked for. win32: a job holds the tree.
    contained: bool

    def __init__(
        self,
        pid: int,
        *,
        contained: bool,
        pgid: int | None = None,
        job: int | None = None,
        api: _Win32Api | None = None,
    ) -> None:
        self.pid = pid
        self.contained = contained
        self._pgid = pgid
        self._job = job
        self._api = api
        # An api is present exactly on the win32 arm — :meth:`_attach_win32`
        # passes one even when the job failed, so a fallback tree still
        # dispatches to taskkill rather than to ``killpg``.
        self._win32 = api is not None
        self._closed = False
        self._finalizer: weakref.finalize | None = None
        if job is not None and api is not None:
            # An abandoned tree still releases its handle. Nothing HERE kills —
            # but KILL_ON_JOB_CLOSE decides that, and for a
            # ``kill_on_close=True`` tree (the rpc child and, since #220, the
            # delegation print child) this finalizer IS a kill, fired at a
            # nondeterministic GC moment. That is the accepted cost of the
            # backstop; the owner is expected to :meth:`close` deliberately.
            self._finalizer = weakref.finalize(self, _release_job, api, job)

    @property
    def job(self) -> int | None:
        """The win32 job handle while it is open; ``None`` elsewhere.

        Read-only, and ``None`` again after :meth:`close` — a closed handle is
        not a job, and handing one out would invite a use-after-close.
        """

        return self._job

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` has run. A closed tree signals NOTHING.

        Both kill legs early-return on it (:meth:`soft_kill` → ``False``,
        :meth:`hard_kill` → no-op), so a caller that dispatches on "a tree is
        present" rather than "a tree can still kill" silently does nothing at
        all. ``aelix_agents``' reaper reads this to decide between the tree legs
        and the signal legs (#220, ``reaper._usable``); it is read as an
        attribute there, so a stub that omits it fails loudly.
        """

        return self._closed

    @classmethod
    def attach(
        cls,
        pid: int,
        *,
        kill_on_close: bool = False,
        handle: int | None = None,
        platform: str | None = None,
        api: _Win32Api | None = None,
    ) -> ProcessTree:
        """Take ownership of the tree rooted at ``pid``.

        ``kill_on_close`` is the per-site decision described in the module
        docstring: it only ever means anything on win32, where it makes
        :meth:`close` end whatever is left. ``handle`` is the stdlib's retained
        process handle (:func:`_retained_handle`); without one the win32 arm
        derives one with ``OpenProcess``, which a hardened process can refuse.

        ``platform`` and ``api`` are injection seams for the tests. Attaching
        with ``platform="win32"`` and no ``api`` off Windows raises.
        """

        _require_pid(pid)
        if _resolve_platform(platform) == "win32":
            return cls._attach_win32(pid, kill_on_close=kill_on_close, handle=handle, api=api)
        return cls._attach_posix(pid)

    @classmethod
    def _attach_win32(
        cls,
        pid: int,
        *,
        kill_on_close: bool,
        handle: int | None,
        api: _Win32Api | None,
    ) -> ProcessTree:
        resolved: _Win32Api = api if api is not None else _KernelApi()
        job: int | None = None
        try:
            job = resolved.create_job(kill_on_close=kill_on_close)
            resolved.assign(job, pid, handle)
        except OSError:
            # Nested jobs have been legal since Windows 8, so a refusal here is
            # a measurement about the runner rather than a shape to plan for.
            # Either way a failed attach must not raise into a spawn: fall back
            # to the taskkill-only path and say so through ``contained``.
            if job is not None:
                _release_job(resolved, job)
            return cls(pid, contained=False, api=resolved)
        return cls(pid, contained=True, job=job, api=resolved)

    @classmethod
    def _attach_posix(cls, pid: int) -> ProcessTree:
        try:
            pgid = os.getpgid(pid)  # pyright: ignore[reportAttributeAccessIssue]
        except ProcessLookupError:
            # Darwin raises this for a ZOMBIE leader whose group is alive and
            # still holding descendants (measured on the owner's own box), so
            # "getpgid failed, therefore stay root-only" would have refused
            # containment in exactly the case containment is for. The caller
            # passed the spawn kwargs; the group it asked for is ``pid``.
            return cls(pid, contained=True, pgid=pid)
        except OSError:
            return cls(pid, contained=False)
        contained = pgid == pid
        return cls(pid, contained=contained, pgid=pid if contained else None)

    def soft_kill(self, *, whole_group: bool = False) -> bool:
        """Ask the tree to stop. Never raises. Returns whether it was SENT.

        POSIX: ``SIGTERM`` to the child, or to the group when ``whole_group``
        and contained — a shell forwards nothing to its pipeline, so the hook
        site asks for the group. win32: ``CTRL_BREAK_EVENT`` at the child's
        process group, which is group-wide by construction, so ``whole_group``
        is accepted and ignored.

        ``True`` means the send call returned: ``os.kill``/``os.killpg`` did not
        raise, or on win32 ``GenerateConsoleCtrlEvent`` reported success.
        ``False`` means it raised — no attached console on win32, ``ESRCH`` on
        POSIX — or that the tree is already closed. A CALLER THAT GETS ``False``
        HAS NOTHING TO WAIT FOR: no signal reached the tree, so the grace it
        would pay next buys nothing and it should go straight to
        :meth:`hard_kill`. Before this returned anything, an undeliverable
        console event on a runner with no console was indistinguishable from a
        child ignoring the signal, and every win32 teardown paid its full grace
        for it (review win-leg/F2).

        Callers guard this with ``returncode is None`` read immediately before,
        with no ``await`` in between (§B.2).
        """

        if self._closed:
            return False
        if self._win32:
            if self._api is None:  # pragma: no cover — set together in __init__
                return False
            try:
                self._api.ctrl_break(self.pid)
            except OSError:
                return False
            return True
        try:
            if whole_group and self.contained and self._pgid is not None:
                os.killpg(self._pgid, signal.SIGTERM)  # pyright: ignore[reportAttributeAccessIssue]
            else:
                os.kill(self.pid, signal.SIGTERM)
        except OSError:
            return False
        return True

    def hard_kill(self) -> None:
        """End the tree unconditionally. Never raises.

        Deliberately NOT guarded by root liveness: leader dead, descendants
        alive is the shape this exists for, and a guard would skip it.
        """

        if self._closed:
            return
        if self._win32:
            if self._api is None:  # pragma: no cover — set together in __init__
                return
            # taskkill FIRST, while the root is still alive so ``/T`` can walk
            # to anything that escaped the assignment window; the job second,
            # which is what actually holds a parentless or re-parented member.
            with contextlib.suppress(OSError):
                self._api.taskkill(self.pid)
            if self._job is not None:
                with contextlib.suppress(OSError):
                    self._api.terminate_job(self._job)
            return
        with contextlib.suppress(OSError):
            if self.contained and self._pgid is not None:
                os.killpg(self._pgid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
            else:
                # Not our group. See the class docstring: root only.
                os.kill(self.pid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]

    def close(self) -> None:
        """Release the tree. Idempotent, and a no-op on POSIX.

        POSIX signals NOTHING here. At the rpc caller the gap between the
        child's reap and this call is seconds (``rpc_channel._shutdown``: reap,
        then ``drain(2.0)``, then ``stop()``), which is precisely when an empty
        group's number is free for somebody else.

        win32: ``CloseHandle(job)``. That ends the remaining members only when
        the tree was attached with ``kill_on_close=True``.
        """

        if self._closed:
            return
        self._closed = True
        job, self._job = self._job, None
        finalizer, self._finalizer = self._finalizer, None
        if finalizer is not None:
            finalizer.detach()
        if job is not None and self._api is not None:
            _release_job(self._api, job)


def kill_process_tree(pid: int, *, platform: str | None = None) -> None:
    """Forcibly terminate ``pid`` and every process descended from it (#105).

    The pid-only entry point, kept for its two spawn-site callers
    ``tools/bash.py`` and ``tools/_subprocess.py``, which hold no
    :class:`ProcessTree` — and reached once more from ``rpc_client.stop()``, as
    the degradation for a client whose ``attach`` itself raised. Best-effort and
    never raises: an already-dead child is the goal state, not an error, and
    callers invoke this from abort/timeout paths where an exception would mask
    the original cause.

    POSIX: ``killpg`` on the child's group, and ONLY when that group is the
    child's own. ``getpgid(pid) != pid`` means the caller did not pass the spawn
    kwargs and the child is sitting in OUR group — which holds this interpreter
    and every sibling it spawned — so the kill degrades to a root-only
    ``os.kill``. That is the same rule :meth:`ProcessTree.attach` enforces
    through ``contained``, restated here because this entry point has no attach
    to have carried it (review posix2/F5, ADR-0238's "never ``killpg`` a group
    whose leader is not the pid we spawned").

    ``ProcessLookupError`` from ``getpgid`` falls back to ``killpg(pid, ...)``
    rather than giving up: Darwin raises it for a ZOMBIE leader whose group is
    alive and still holding descendants (measured). The callers differ in what
    that fallback is aimed at, and the difference is worth stating rather than
    averaging: ``bash.py``'s call sites run BEFORE any reap
    (``bash.py:300-330`` — ``wait(timeout)`` raised, or the abort watcher
    fired), so the zombie is genuinely still pinning the number, while
    ``_subprocess.py::run_cancellable`` runs AFTER asyncio's watcher has already
    reaped the child (measured), up to the caller's whole timeout later — 30 s
    for grep/find. What bounds the fallback anyway is that it only ever
    addresses the group number the spawn asked for: a non-empty group keeps its
    id while any member lives (POSIX.1 §3.293; Linux
    ``attach_pid(PIDTYPE_PGID)``), and an empty one has nothing to kill —
    ``killpg`` answers ``ESRCH``. The residual case is stated in ADR-0238.

    win32: ``taskkill.exe /T /F /PID`` through the same resolver as the job
    path. There is no job here and no handle to have had one.

    ``platform`` defaults to :data:`sys.platform`; it is injected so the win32
    arm can be exercised from a POSIX box and — since the windows leg exists —
    the POSIX arm from a Windows one, where the tests must lend the interpreter
    the three names it does not have.
    """

    _require_pid(pid)
    if _resolve_platform(platform) == "win32":
        _taskkill_tree(pid)
        return
    try:
        pgid = os.getpgid(pid)  # pyright: ignore[reportAttributeAccessIssue]
    except ProcessLookupError:
        # Darwin, zombie leader: the group is alive, the leader is not readable.
        # The number the spawn asked for is the pid.
        pgid = pid
    except OSError:
        return
    if pgid != pid:
        # NOT our group — the caller forgot the spawn kwargs, so this pid is in
        # the group that holds this interpreter. Root-only, or the "cleanup"
        # SIGKILLs the process performing it (measured in review posix2/F5).
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
        return
    with contextlib.suppress(OSError):
        # NOT reachable on Windows, and deliberately NOT guarded a second time.
        # The early return above is the only way here, and with no injected
        # ``platform`` — which is every production caller — it decides on
        # ``sys.platform``. pyright cannot narrow through that indirection, so
        # the windows type gate reports both POSIX-only names on this line; the
        # suppression is about the CHECKER. A runtime guard here would read to
        # the next person as a safety check and would be a lie:
        # ``test_win32_never_touches_killpg_or_sigkill`` deletes exactly these
        # names and passes on the windows runner itself.
        os.killpg(pgid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
