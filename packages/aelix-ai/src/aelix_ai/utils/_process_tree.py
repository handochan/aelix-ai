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
success-path behaviour on every platform, and ``False`` for every
:func:`run_contained` site — an extension's ``exec``, the catalog ``git
clone``, the ``fd`` tree scan (#221): a command that exits 0 after
backgrounding a helper keeps the helper, exactly as it does today.

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
import threading
import time
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any, Protocol, cast

__all__ = [
    "AbortHandle",
    "ProcessTree",
    "_retained_handle",
    "containment_spawn_kwargs",
    "kill_process_tree",
    "run_contained",
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

    ALL THREE STDIO ARE ``DEVNULL``, and that is the #221 bug applied to this
    function rather than a tidy-up (#221 review win-leg/WIN-3). It used to pass
    ``capture_output=True`` and throw the result away, which is exactly the
    shape #221 exists to end: on win32 CPython's ``run()`` follows its kill with
    an UNTIMED ``communicate()`` to join the reader threads, so a ``taskkill``
    that hit the 5 s timeout would then block on pipe EOF with no bound at all —
    and this runs synchronously on the event-loop thread since #220. With no
    pipe there is nothing for that join to wait on. It is the same reasoning
    ``util/tools_manager.py``'s version probe already relies on.
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
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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


# ---------------------------------------------------------------------------
# one bounded synchronous run, contained (#221)
# ---------------------------------------------------------------------------

#: How long the pipes must fall idle AFTER the root exited before the drain
#: gives up on them. Pi's ``EXIT_STDIO_GRACE_MS = 100``
#: (``utils/child-process.ts::waitForChildProcess``, the 2026-06-15 fix for
#: Pi #5303/#5753). The timer is armed at the EXIT and re-armed on every chunk
#: that arrives after it; measuring from the last chunk alone returned
#: instantly whenever the root had been quiet for a grace before exiting and
#: binned the whole post-exit tail — measured ``stdout=b'EARLY\n'`` with five
#: late chunks lost, against Pi's ``b'EARLY\nLATE0..4\n'`` (#221 review
#: PI-1/WIN-1/TP1/POSIX-7).
EXIT_DRAIN_SECONDS = 0.1
#: The absolute bound on the post-exit drain, from the exit instant. An
#: Aelix-only divergence: Pi drains without one. It exists because
#: ``api.exec``'s DEFAULT is ``timeout_ms=None``, under which the idle rule is
#: the only other bound and a descendant that never falls idle defeats it —
#: measured with a helper writing continuously: 10 MB buffered at 9 s and still
#: climbing, the call never returning (#221 review DOC-1: the ``ru_maxrss``
#: figure that used to stand here carried no unit and is dropped rather than
#: restated). The failure mode it buys is stated in :func:`run_contained`'s
#: docstring.
#:
#: WHAT IT BOUNDS IS THE CALLER'S WAIT, NOT THE READER'S LIFE (#221 review
#: site-exec-1). It used to be written here as the bound on "a chatty holder",
#: which it is not: the holder goes on writing and the leaked daemon goes on
#: reading afterwards, and until :meth:`_PipeReader.detach` existed it also went
#: on RETAINING — measured after a ``returncode == 0`` return, 10 MB grew to
#: 25 MB over the next 6 s with nobody left to read it. What the cap ends is the
#: call, at 2 s past the exit; what ends the accumulation is the ``detach`` in
#: :func:`run_contained`'s ``finally``, after which the reader discards.
DRAIN_CAP_SECONDS = 2.0
#: How long the root is given to die after the timeout ladder's kill. Matches
#: ``oauth/_resolve_config.py``'s bound, and for its reason: on win32 with no
#: job (a failed attach) and no resolvable ``taskkill.exe`` ``hard_kill`` is a
#: silent no-op (#202 review win-leg/F3), so a kill that could not kill must
#: cost a bounded wait rather than the calling thread.
REAP_GRACE_SECONDS = 5.0
#: The bound on the post-kill drain. Joining the readers for the remainder of
#: ``REAP_GRACE_SECONDS`` instead cost a flat 5 s whenever a pipe holder
#: outside the tree survived — measured ``6.011 s`` for a ``timeout=1.0`` run —
#: and bought zero bytes, because everything the kill released is readable at
#: once (#221 review TP4/PI-2). Measured again with the idle rule and an
#: escaped holder: ~1.1 s.
KILL_DRAIN_SECONDS = 1.0
#: The bound on the interrupt ladder's reap. CPython's own number for the same
#: situation: ``Popen._sigint_wait_secs = 0.25``. Short on purpose — a ^C must
#: not feel like a five-second hang.
INTERRUPT_REAP_SECONDS = 0.25

#: ``BufferedReader.read1(n)`` returns what is AVAILABLE rather than blocking
#: for ``n`` bytes or EOF — measured on py3.12.13/darwin: 10 B at 0.01 s, the
#: next 10 B at 0.52 s, ``b""`` at 1.02 s. That is what makes an idle timer
#: mean anything; a plain ``read(65536)`` would observe nothing until EOF.
_READ_CHUNK_BYTES = 65536
#: The drain's poll quantum. Small enough that a 0.1 s grace is not measurably
#: overshot, large enough not to spin.
_DRAIN_POLL_SECONDS = 0.005


@dataclass
class _ReadState:
    """The two instants the drain's idle rule is measured from.

    Shared by both readers and by :func:`run_contained`. ``last_chunk_at``
    is written ONLY by the reader threads (one attribute store, atomic under
    the GIL — there is no invariant spanning two fields to protect, so there
    is no lock); ``exited_at`` ONLY by :func:`run_contained`, once when the
    root exits and once more at the reap on the timeout path.
    """

    #: When a reader last appended a chunk. Initialised to the spawn instant so
    #: a root that never writes still has an origin.
    last_chunk_at: float
    #: When the root exited (or was reaped after the kill). ``None`` until then.
    exited_at: float | None = None


class _PipeReader(threading.Thread):
    """One daemon thread draining one pipe into a list of chunks.

    WHY THREADS AND NOT ``select``. Windows anonymous pipes cannot be
    ``select``ed and have no non-blocking mode, which is why CPython's own
    ``communicate()`` uses reader threads there; one shape on both platforms is
    also the only shape this box can test. It is CPython's arrangement with two
    differences: the chunks are timestamped (so the drain can ask "have the
    pipes fallen idle?" rather than only "is it EOF?"), and nothing ever joins
    these threads — see :func:`run_contained`'s leaked-reader paragraph.

    DAEMON, and that is load-bearing rather than defensive: a holder outside the
    tree (a ``setsid`` descendant on POSIX, a job escapee on win32) never closes
    the write end, so this thread can be blocked in ``read1`` forever and must
    not keep the interpreter alive.

    AND IT STOPS RETAINING WHEN THE CALL ENDS (#221 review site-exec-1). A
    daemon that outlives the call it was started for used to go on appending to
    ``chunks`` forever: measured after a ``returncode == 0`` return with a pipe
    holder still alive, 10 MB retained growing to 25 MB over the next 6 s, and
    +2 threads +2 fds per call, STACKING across calls. :meth:`detach` is what
    ends that half without ending the read — see its docstring for why the read
    itself must go on.
    """

    def __init__(self, stream: IO[bytes], state: _ReadState) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._state = state
        #: Everything read so far. Appended by this thread, read by the caller;
        #: ``list.append`` is atomic under the GIL and the caller copies before
        #: joining, so a chunk in flight is either wholly in or wholly out.
        self.chunks: list[bytes] = []
        #: ``True`` once the pipe reported EOF — every writer closed its end.
        self.eof = False
        #: Set by :meth:`detach`, read by :meth:`run`. One attribute store from
        #: the caller's thread and one load per chunk from this one, with no
        #: invariant spanning two fields — the same GIL argument
        #: :class:`_ReadState` makes, so no lock (#221 review site-exec-1).
        self._detached = False

    def run(self) -> None:
        stream = self._stream
        # ``Popen``'s pipes are ``BufferedReader``s and have ``read1``; the
        # fallback is for a stream that does not (a test double, a raw file
        # object) and costs only the idle timer's resolution, never
        # correctness.
        read1 = cast("Callable[[int], bytes] | None", getattr(stream, "read1", None))
        read = read1 if read1 is not None else stream.read
        try:
            while True:
                chunk = read(_READ_CHUNK_BYTES)
                if not chunk:
                    self.eof = True
                    return
                if self._detached:
                    # READ ON, KEEP NOTHING (#221 review site-exec-1). Not a
                    # ``return``: the holder must never be stalled on a full
                    # 64 KiB pipe by a call that has already gone home. The
                    # idle timer is not stamped either — the ``_ReadState``
                    # this shares belongs to a call that has returned.
                    continue
                self.chunks.append(chunk)
                self._state.last_chunk_at = time.monotonic()
        except (OSError, ValueError):
            # Ends SILENTLY (#221 review WIN-7). A closed or broken pipe here is
            # the normal end of a run that was killed, and this thread has no
            # caller to raise into — it is never joined.
            pass
        finally:
            # The thread owns the close: the caller may have returned already,
            # and closing under a blocked ``read1`` from another thread is not
            # portable.
            with contextlib.suppress(Exception):
                stream.close()

    def detach(self) -> None:
        """Keep draining this pipe, but stop RETAINING what comes out of it.

        Called from :func:`run_contained`'s ``finally`` on every reader it
        started, and NOWHERE ELSE — the payload (``CompletedProcess`` or the
        ``TimeoutExpired``) is built by :func:`_collected` before that
        ``finally`` runs, and a clear from inside :func:`_drain` would race it
        and, on the timeout path, deterministically empty the output
        ``test_an_exited_root_with_a_pipe_holder_is_a_success_and_keeps_the_tail``
        preserves (#221 review site-exec-1).

        WHY THE READ GOES ON. The thread cannot be unblocked — that is this
        module's stated leak, and ``CancelSynchronousIo`` is the win32-only
        half (#221 §I) — and a reader that stopped reading would leave a live
        holder wedged on a full pipe buffer the moment it wrote 64 KiB. So the
        read continues and the bytes are dropped: the thread and the fd still
        live until the holder closes its end, but memory no longer accumulates.

        RETAINED AFTERWARDS IS AT MOST ONE IN-FLIGHT CHUNK
        (:data:`_READ_CHUNK_BYTES`): the flag is set before the list is
        replaced, so the only chunk that can still land is one whose ``if
        self._detached`` test was already past when this ran. Idempotent.
        """

        self._detached = True
        self.chunks = []


def _start_reader(stream: IO[bytes] | None, state: _ReadState) -> _PipeReader | None:
    """Start a reader on ``stream``, or return ``None`` when there is none."""

    if stream is None:  # pragma: no cover — both streams are always PIPE here
        return None
    reader = _PipeReader(stream, state)
    reader.start()
    return reader


def _collected(reader: _PipeReader | None) -> bytes:
    """Everything ``reader`` has read so far. Never ``None`` — ``b""`` instead.

    ``TimeoutExpired.stdout``/``.stderr`` are part of this helper's contract and
    callers decode them unconditionally, so a missing stream must read as empty
    output rather than as ``None``. ``subprocess.run`` is the OTHER WAY ROUND
    from what this docstring used to claim (#221 review HC3): its POSIX timeout
    branch is the one that can leave them ``None`` — ``_check_timeout`` passes
    ``output=b"".join(seq) if seq else None``, so a child that printed nothing
    yields ``stdout=None stderr=None`` (measured, py3.12.13/darwin) — while its
    win32 branch re-``communicate()``s after the kill and always attaches bytes
    for a PIPEd stream (source read; not measurable on this host). So ``b""``
    here is a deliberate DIVERGENCE from CPython on POSIX, which is why the
    contract is stated at all.
    """

    if reader is None:  # pragma: no cover — both streams are always PIPE here
        return b""
    return b"".join(list(reader.chunks))


def _drain(
    readers: Sequence[_PipeReader],
    state: _ReadState,
    *,
    exit_drain: float,
    drain_until: float,
) -> None:
    """Read on past the root's death until one of three things is true.

    Ends on whichever comes FIRST (§A.2.6 of the #221 spec):

    (a) every reader has seen EOF, or is no longer alive — the ordinary end,
        and the only one that proves nothing was lost. The liveness half is
        cost, not correctness (#221 review HC5): a reader that ended through
        its ``except (OSError, ValueError)`` leg never sets ``eof``, and
        without it such a reader cost a full ``exit_drain`` to a thread that
        was already dead (measured 0.102 s against 0.000 s for an EOF'd
        control). ``eof`` keeps its documented meaning — "the pipe reported
        EOF" — rather than being overloaded to mean "this thread is done";
    (b) the pipes have been idle for ``exit_drain`` measured from
        ``max(last_chunk_at, exited_at)`` — Pi's rule, with the timer armed at
        the exit and re-armed by every chunk that arrives after it;
    (c) ``drain_until`` — the caller's absolute cap.

    A reader still blocked when this returns is LEFT ALONE. It is a daemon
    thread and it ends when the last holder of the write end closes it; there
    is no portable way to unblock it (``CancelSynchronousIo`` on win32 is the
    win32-only half and is not adopted — #221 §I).
    """

    while True:
        if all(reader.eof or not reader.is_alive() for reader in readers):
            return
        now = time.monotonic()
        if now >= drain_until:
            return
        armed_at = state.last_chunk_at
        exited_at = state.exited_at
        if exited_at is not None and exited_at > armed_at:
            armed_at = exited_at
        if now - armed_at >= exit_drain:
            return
        time.sleep(_DRAIN_POLL_SECONDS)


def _end_the_tree(tree: ProcessTree, proc: subprocess.Popen[bytes], *, reap: float) -> None:
    """``hard_kill`` the tree, belt the root, then wait ``reap`` seconds for it.

    THE BELT IS NOT REDUNDANT, and its safety is per-platform (#221 review
    WIN-5). ``hard_kill`` does not touch ``Popen`` state, so this is always
    entered with ``returncode is None`` even when the root is already dead
    (measured). POSIX — ``Popen.kill`` is ``send_signal(SIGKILL)``, which polls
    first (bpo-38630): the poll reaps the zombie and no second signal is sent,
    so a recycled pid cannot be hit. win32 — ``Popen.kill`` IS ``terminate``,
    does NOT poll, and calls ``TerminateProcess`` on the RETAINED handle
    (``class Handle(int)``, ``__del__ = Close``), so the number cannot have been
    recycled while we hold it; on an already-exited process the call raises
    ``PermissionError``, which ``Lib/subprocess.py:1674-1681`` converts into a
    ``returncode``. Both live inside ``suppress(OSError)``. And it is the ONLY
    thing that ends the root on a win32 host where the attach failed and
    ``taskkill.exe`` does not resolve, where ``hard_kill`` is a silent no-op
    (#202 review win-leg/F3).

    The wait is bounded because ``hard_kill`` can be that no-op: a root that
    outlives ``reap`` is a live ORPHAN — not a zombie — filed in
    ``subprocess._active`` and never collected, the residue
    ``oauth/_resolve_config.py`` already accepts (#221 review POSIX-6).
    """

    tree.hard_kill()
    with contextlib.suppress(OSError):
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=reap)


class AbortHandle:
    """A caller's grip on a :func:`run_contained` call blocked on ANOTHER thread.

    THE HOLE IT FILLS (#221 review SITE-1), measured. The interrupt ladder in
    :func:`run_contained` is reached by an exception raised INSIDE the call, and
    ``ExtensionAPI.exec`` awaits the helper on an ``asyncio.to_thread`` worker
    where no terminal signal ever lands — CPython delivers signals to the main
    thread only. Meanwhile the spawn's own session takes the child out of the
    terminal's foreground group, so the tty's ^C no longer reaches it either.
    The two together are a regression THIS change introduced: on ``main`` a ^C
    on ``aelix -p`` ended the command in ``0.02 s``; with the contained spawn
    and no handle the interrupted process waited out the command's whole
    remaining life — measured ``28.58 s`` of a 30 s child — because
    ``Runner.close`` joins the default executor for 300 s and then
    ``concurrent.futures.thread._python_exit`` joins it again with NO bound.

    WHAT IT DOES, AND WHAT IT DELIBERATELY DOES NOT. ``abort()`` sends the same
    two rungs the ladders send — ``tree.hard_kill()`` then the ``proc.kill()``
    belt — and does NOT wait for anything. That is the whole design: the kill is
    what lets the worker's blocked ``proc.wait(timeout=)`` return, after which
    the call's ordinary exit path drains under its idle rule and returns a
    ``CompletedProcess`` carrying the kill's returncode (``-9`` on POSIX, ``1``
    on win32) to a caller that has already stopped listening. No new thread, no
    polling, and no second code path through the helper.

    THE RACE IS CLOSED AT THE ATTACH, not by asking the caller to be late. A
    handle passed to a call that has not attached yet only marks itself
    aborted; :func:`run_contained` hands the tree over under this object's lock
    immediately after ``ProcessTree.attach``, and a mark already set fires the
    kill THERE. ``_finish`` (in the call's ``finally``) makes a late ``abort()``
    a no-op rather than a kill aimed at a pid that has been reaped.
    """

    def __init__(self) -> None:
        #: One lock over all four fields: "attached and not finished" is the
        #: invariant that decides whether a kill is sent, and it spans them.
        self._lock = threading.Lock()
        self._tree: ProcessTree | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._aborted = False
        self._finished = False

    @property
    def aborted(self) -> bool:
        """Whether :meth:`abort` has been called, whenever it was called."""

        with self._lock:
            return self._aborted

    def abort(self) -> bool:
        """End the run's tree now. Thread-safe, never raises, never waits.

        Returns ``True`` when the kill was actually sent, ``False`` when there
        was nothing to send it to — either the call has not attached yet (the
        mark is kept and the attach kills at once) or it has already finished.
        """

        with self._lock:
            self._aborted = True
            if self._finished or self._tree is None or self._proc is None:
                return False
            self._kill()
            return True

    def _attach(self, tree: ProcessTree, proc: subprocess.Popen[bytes]) -> None:
        """Take custody of a live run, killing it at once if ``abort`` beat us."""

        with self._lock:
            self._tree = tree
            self._proc = proc
            if self._aborted and not self._finished:
                self._kill()

    def _finish(self) -> None:
        """The run is over: drop the references and disarm a late ``abort``."""

        with self._lock:
            self._finished = True
            self._tree = None
            self._proc = None

    def _kill(self) -> None:
        """The ladder's two rungs, no reap. The caller holds the lock.

        Holding the lock across ``hard_kill`` costs a win32 ``taskkill``'s
        <= 5 s to a concurrent ``_finish``, which is the run's own thread and
        has already had its ``wait`` released by this kill; nothing waits on
        this object from a third direction.
        """

        tree, proc = self._tree, self._proc
        if tree is None or proc is None:  # pragma: no cover — guarded by callers
            return
        tree.hard_kill()
        with contextlib.suppress(OSError):
            proc.kill()


def run_contained(
    argv: Sequence[str],
    *,
    timeout: float | None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    api: _Win32Api | None = None,
    abort: AbortHandle | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """``subprocess.run(argv, capture_output=True, timeout=…)``, but contained.

    THE TWO BUGS THIS EXISTS FOR (#221), both measured on ``main`` dff6b62.

    1. **The timeout kills the root and nothing else.** CPython's ``run()`` does
       ``process.kill()`` on ``TimeoutExpired`` — the root pid alone. Every one
       of the three sites that used it leaked a non-``setsid`` grandchild on
       POSIX (``survivors=1`` at each, and the real ``ExtensionAPI.exec`` under
       a real model left its sleeper at ``ppid 1`` in *aelix's own* process
       group). And on Windows — where the reader threads own the output and
       must be joined to attach it to the exception — ``run()`` follows the kill
       with an UNTIMED ``process.communicate()``, so a descendant that inherited
       the pipes and outlived the root keeps the write end open and that join
       NEVER RETURNS. ``tests/process_tree/test_run_contained_real_processes.py``
       's first case is the one that would hang the windows leg on ``main``.
    2. **A root that exits 0 is reported as a timeout.** ``communicate()`` waits
       for pipe EOF, not for the root, so a command that printed ``done`` and
       exited 0 while a helper it backgrounded held stdout was raised as
       ``TimeoutExpired`` after the WHOLE timeout — measured ``out=b'done\\n'``
       at 3.32 s under a 1 s bound, and through the real exec surface as
       ``code=124 killed=True stdout='done\\n'``. Pi fixed the same shape on
       2026-06-15 (#5303/#5753, ``utils/child-process.ts``).

    THE SHAPE. ``stdin=DEVNULL``, both other streams piped, bytes out,
    ``check=False``, ``TimeoutExpired`` raised with whatever output arrived
    (``bytes``, never ``None``), spawn errors propagated unchanged. The root's
    lifetime is bounded by ``proc.wait(timeout=timeout)`` — *not* pipe EOF,
    which is bug 2 — and the pipes are drained afterwards by two daemon
    threads under an idle timer ARMED AT THE EXIT and re-armed per chunk
    (Pi's ``onExit -> armIdleTimer()``; measuring from the last chunk alone
    binned the whole post-exit tail and, under CPU contention, truncated a
    root's OWN output 10 runs in 60). The drain also ends at
    :data:`DRAIN_CAP_SECONDS` after the exit, and at the original deadline when
    a ``timeout`` was given — floored at one grace past the exit, so a root that
    exits a millisecond before the deadline still keeps its own tail. The cap is
    an Aelix-only divergence from Pi and its failure mode is stated: a
    DESCENDANT still writing at the cap has its output cut with the root's
    ``returncode == 0``. Never the root's own output, which is at most a pipe
    buffer at exit and is drained in milliseconds.

    HARD ONLY, NO SOFT LEG. There is no grace stage to escalate from and the
    producer is by definition not answering (``oauth/_resolve_config.py``'s
    reasoning, verbatim). Two more reasons, both measured: hard-only is what
    these sites do TODAY (``run()`` sends ``kill()`` with no SIGTERM leg, so a
    soft leg would be new behaviour to justify rather than behaviour to
    preserve), and Pi's soft leg is not a bound worth copying — ``execCommand``
    schedules ``if (!proc.killed) proc.kill("SIGKILL")`` on a 5 s timer, and
    Node sets ``killed`` when the signal is SENT, so the escalation never fires:
    a ``trap '' TERM`` child ran its full 40 s under a 1 s Pi timeout, while a
    SIGTERM-honouring one resolved at 1005 ms. We take one kill path and a
    latency bounded by us (:data:`REAP_GRACE_SECONDS` + :data:`KILL_DRAIN_SECONDS`)
    rather than by the command. Divergence from Pi needs no ADR (ADR-0235).

    THE WIN32 BOUND, STATED RATHER THAN IMPLIED (#221 review DOC-4/HC4). BOTH
    ladders below start with :meth:`ProcessTree.hard_kill`, which on win32 runs
    ``taskkill /T /F`` FIRST and UNCONDITIONALLY, synchronously, and
    :func:`_taskkill_tree` is itself a ``subprocess.run(timeout=5)``. So the
    win32 timeout path is ``timeout + <= 5 s + REAP_GRACE_SECONDS +
    KILL_DRAIN_SECONDS``, not ``timeout + 6 s``, and the interrupt leg's bound
    there is ``<= 5 s + 0.25 s`` rather than 0.25 s. The extra term is still
    OUR ``timeout=``, not the command's, which is what the paragraph above
    claims; #220's windows leg measured that rung at 0.031 s, 160x inside its
    own bound, and the windows leg's ``warnings.warn`` is where the number for
    this helper comes from. One residue is accepted and named rather than
    guarded (#221 review HC7, **[U]**): a SECOND interrupt landing INSIDE that
    ``taskkill`` propagates out of ``hard_kill`` — it is wrapped in
    ``suppress(OSError)`` only — leaving ``terminate_job`` and the belt unrun
    for that call, with ``close()`` (``kill_on_close=False``) ending nothing.
    Reaching it needs the user to hold ^C down through the ladder.

    THE INTERRUPT LEG IS MANDATORY, NOT COSMETIC. ``subprocess.run`` kills the
    root on ANY exception (``except: process.kill()``) and ``Popen.__exit__``
    states its assumption in a comment — "In the case of a KeyboardInterrupt we
    assume the SIGINT was also already sent to our child processes". The spawn
    below makes that assumption FALSE: ``start_new_session=True`` takes the
    child out of the terminal's foreground process group, and
    ``CREATE_NEW_PROCESS_GROUP`` "disables CTRL+C signals for all processes
    within the new process group". Measured under a real pty: ``^C`` -> parent
    ``KeyboardInterrupt``, contained child ALIVE. So any ``BaseException`` raised
    anywhere AFTER THE SPAWN runs the ladder at tree granularity, bounded at
    :data:`INTERRUPT_REAP_SECONDS`, before it propagates; no output is attached.
    Not merely out of the wait or the drain, which is where the cover used to
    end (#221 review posix-runner-1): the ``abort`` hand-over and both
    ``_start_reader`` calls are inside it too, because a ``KeyboardInterrupt``
    armed to land in that window leaked the whole ``setsid`` tree 12 times out
    of 12, against 0 out of 6 for the same signal a few microseconds later.
    Before ``ProcessTree.attach`` RETURNS there is no tree to end, so that one
    call carries a belt of its own instead — ``proc.kill()`` and a bounded
    ``proc.wait`` on the root, then the exception propagates unchanged.

    WHERE THAT LEG ACTUALLY FIRES, MEASURED (#221 review HC2). At ``aelix
    extension discover --refresh``, whose 60 s clone runs the wait on the MAIN
    thread — and there it buys something concrete: the tree is dead before
    ``_git_clone_bytes``'s ``finally: rmtree`` deletes the directory out from
    under a live ``git``. It does NOT fire at ``ExtensionAPI.exec``: that call
    awaits this helper on an ``asyncio.to_thread`` WORKER and CPython raises
    ``KeyboardInterrupt`` on the main thread only, so a terminal ^C never
    enters this frame there — measured, two ^C, ladder calls ``[]``. Only a
    ``BaseException`` raised INSIDE the worker reaches it. That site is closed
    by :class:`AbortHandle` instead, below.

    A SESSION OF ITS OWN, ALWAYS — AND WHAT THAT GIVES UP. The spawn is
    ``containment_spawn_kwargs(new_session=True)``, so the child has NO
    CONTROLLING TERMINAL. The alternative, ``process_group=0``, keeps the
    session and was measured to be worse than useless here: under a real pty a
    ``git clone`` over ssh with an unknown host key left ``git``, ``ssh``,
    ``sshd-session`` and ``sshd-auth`` all STOPPED (``ps stat T`` — ssh's
    ``read_passphrase`` calls ``tcsetattr``, which raises ``SIGTTOU``
    group-wide) with no prompt ever printed, for the full 60 s. With ``setsid``
    every tty read fails AT ONCE with the tool's own message — ``fatal: could
    not read Username for '…'`` (rc 128, measured 0.08 s on darwin against a
    local server answering 401; the tail after the colon is the platform's
    ``strerror(ENXIO)`` — ``Device not configured`` on darwin, ``No such device
    or address`` on Linux, so it is not quoted here — #221 review DOC-2),
    ``Host key verification failed.`` (rc 128, 0.62 s) — which the callers
    already surface. The cost, stated: these commands are non-interactive ON
    POSIX. On win32 there is no session to take away — ``containment_spawn_kwargs``
    returns ``CREATE_NEW_PROCESS_GROUP`` and the job is the containment — so the
    child keeps the console and a program that reads ``CONIN$`` directly (git
    does; our stdin is ``NUL``) can still prompt there, and an unanswered prompt
    costs the full timeout (post-merge review adversary-1; the same clause is on
    every user-facing surface that states the property). The
    reversal path is not ``process_group=0`` but "no POSIX group at all", and
    its measured cost is the leak this helper exists for — ``git remote-http``,
    blocked in libcurl on a hung server, outlives a root-only SIGKILL of
    ``git`` by 3 s+ (``ppid 1``, still in our group). ADR-0238's #221 amendment
    records both.

    WHAT IS LEAKED, DELIBERATELY — AND WHAT IS NOT. A reader whose pipe never
    reaches EOF is left running: one thread per event, daemon, blocked in
    ``read1`` until the holder closes the write end. CPython's own
    ``communicate()`` threads are daemons too; Pi ``destroy()``s the streams,
    which cancels Node's pending read, and Python has no portable equivalent.
    The cases are a ``setsid`` descendant on POSIX (outside the group — the
    asymmetry this module's docstring states), a job escapee on win32, and on
    the SUCCESS path a helper the command deliberately backgrounded, which
    ``kill_on_close=False`` and a releasing ``close()`` are there to preserve.

    WHAT IS NOT LEAKED IS MEMORY (#221 review site-exec-1). ``finally`` calls
    :meth:`_PipeReader.detach` on every reader it started, and a detached reader
    keeps READING — a holder must never be stalled on a full 64 KiB pipe by a
    call that has gone home — while DISCARDING. The thread and its fd still live
    until the holder closes its end; the bytes stop accumulating, bounded at one
    in-flight chunk (:data:`_READ_CHUNK_BYTES`). Before it, a ``returncode == 0``
    return with a live holder went on appending forever: measured 10 MB at the
    return and 25 MB 6 s later, +2 threads and +2 fds per call, stacking. It is
    deliberately NOT done in :func:`_drain`, which runs BEFORE the payload is
    collected on both the exit and the timeout path and would empty the very
    tail this helper exists to keep. :data:`DRAIN_CAP_SECONDS` is the bound on
    the CALLER's wait, not on any of this.

    A CALLER ON ANOTHER THREAD. ``abort`` takes an :class:`AbortHandle`, which
    is how a caller that is NOT the thread blocked here ends the run: the
    interrupt leg above cannot help ``ExtensionAPI.exec``, whose ``await`` is on
    an ``asyncio.to_thread`` worker that no signal reaches. ``handle.abort()``
    sends this ladder's two rungs and returns immediately; the blocked
    ``proc.wait`` then returns because the root is dead, and this call takes its
    ORDINARY exit path — drain, ``CompletedProcess`` with the kill's returncode
    — for a caller that has already unwound. Passing no handle changes nothing.

    ``platform`` and ``api`` are the injection seams of this module; production
    passes neither.
    """

    command = list(argv)
    # The deadline's origin, taken immediately before the spawn so that the
    # bound the drain caps against is the same one ``proc.wait(timeout=)``
    # enforced (#221 review CS5).
    start = time.monotonic()
    # The cast is about the CHECKER, and it is ``_resolve_config.py``'s:
    # unpacking a ``dict[str, Any]`` costs pyright the text/bytes overload
    # discrimination and it settles on ``Popen[str]``. At runtime ``PIPE`` with
    # no ``text``/``encoding`` is bytes, which is what the cast asserts. A spawn
    # failure propagates unchanged — no tree exists yet and ``close`` is not
    # reached; the call sites already translate ``FileNotFoundError``.
    proc = cast(
        "subprocess.Popen[bytes]",
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=None if env is None else dict(env),
            **containment_spawn_kwargs(new_session=True, platform=platform),
        ),
    )
    # Seeded BEFORE anything that can raise, so the ``finally`` below and every
    # ``_collected`` call stay well defined however early the region under it
    # fails (#221 review posix-runner-1). ``readers`` is appended to as each
    # thread STARTS rather than built at the end, for the same reason: a
    # ``_start_reader`` that raises must still leave its predecessor in the
    # list the ``finally`` detaches.
    readers: list[_PipeReader] = []
    out_reader: _PipeReader | None = None
    err_reader: _PipeReader | None = None
    # Attached BEFORE any read or wait: on win32 only descendants created after
    # the job assignment inherit membership, so every microsecond here is
    # assignment window. ``kill_on_close=False`` — see the module docstring. A
    # win32 attach that fails degrades inside ``attach`` (``contained=False``,
    # taskkill-only) and never raises; a POSIX attach cannot fail, and the
    # ``setsid`` -> ``getpgid`` race does not open (measured: 400/400 spawns
    # attached ``contained=True``).
    try:
        tree = ProcessTree.attach(
            proc.pid,
            kill_on_close=False,
            handle=_retained_handle(proc),
            platform=platform,
            api=api,
        )
    except BaseException:
        # THE BELT IS ALL THERE IS BEFORE A TREE EXISTS (#221 review
        # posix-runner-1). A ^C that lands in this window used to leave the
        # whole ``setsid`` tree behind — measured with a ``SIGALRM``-raised
        # ``KeyboardInterrupt`` armed 40 us before the attach: 12/12 leaked,
        # against 0/6 for the same signal inside the covered region below.
        # This handler cannot use ``_end_the_tree``: ``tree`` is exactly what
        # is unbound here, which is also why the attach is NOT inside the try
        # whose ``finally`` closes it.
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=INTERRUPT_REAP_SECONDS)
        raise
    try:
        try:
            if abort is not None:
                # Under the handle's own lock, and BEFORE the readers and the
                # wait: a handle whose ``abort()`` already ran (the cancellation
                # beat the spawn) fires the kill here rather than being lost.
                # See :class:`AbortHandle`.
                abort._attach(tree, proc)
            state = _ReadState(last_chunk_at=start)
            out_reader = _start_reader(proc.stdout, state)
            if out_reader is not None:
                readers.append(out_reader)
            err_reader = _start_reader(proc.stderr, state)
            if err_reader is not None:
                readers.append(err_reader)
        except BaseException:
            # THE SAME LADDER THE WAIT AND THE DRAIN GET, over the setup that
            # used to sit outside every kill leg (#221 review posix-runner-1).
            # Anything raised between the attach and the first ``wait`` —
            # ``abort._attach``'s kill, a ``_start_reader`` that cannot start a
            # thread, a signal that lands in the gap — now ends the tree before
            # it propagates, and the ``finally`` below still closes it.
            _end_the_tree(tree, proc, reap=INTERRUPT_REAP_SECONDS)
            raise
        # WHAT THIS ``try`` NOW HOLDS, AND WHY THAT IS THE POINT (#221 review
        # posix-runner-1). ``except subprocess.TimeoutExpired`` means "the
        # COMMAND ran out of time", so the block under it must contain nothing
        # else that can raise one. The setup above is a separate ``try`` for
        # exactly that reason: ``abort._attach`` can fire a win32 ``hard_kill``,
        # and a ``TimeoutExpired`` from THAT reported as the command's timeout
        # would attach the wrong payload to the wrong failure. (It cannot
        # actually escape today — ``_taskkill_tree`` catches
        # ``subprocess.SubprocessError``, which ``TimeoutExpired`` is, and
        # ``hard_kill`` adds ``suppress(OSError)`` on top — but the ``api`` seam
        # makes that a caller's guarantee rather than this module's, and the
        # split costs nothing.) The two handlers below are SIBLINGS, so the
        # ``TimeoutExpired`` re-raised from the first is not caught by the
        # second and the timeout path runs exactly one ladder.
        try:
            # Bounds the ROOT's lifetime, not pipe EOF — bug 2 above.
            # ``timeout=None`` waits without bound, as ``run(timeout=None)`` does.
            returncode = proc.wait(timeout=timeout)
            state.exited_at = time.monotonic()
            drain_until = state.exited_at + DRAIN_CAP_SECONDS
            if timeout is not None:
                # The original deadline caps the drain, floored at one grace
                # after the exit so a root that exits at ``deadline - 1 ms``
                # still keeps its own tail (#221 review POSIX-2/CS8).
                drain_until = min(
                    drain_until, max(start + timeout, state.exited_at + EXIT_DRAIN_SECONDS)
                )
            _drain(readers, state, exit_drain=EXIT_DRAIN_SECONDS, drain_until=drain_until)
        except subprocess.TimeoutExpired as expired:
            _end_the_tree(tree, proc, reap=REAP_GRACE_SECONDS)
            # The post-kill drain is armed HERE, at the reap: everything the
            # kill released is readable at once, and a holder OUTSIDE the tree
            # will never EOF, so this is the exit drain's idle rule under a
            # tighter cap (#221 review TP4/PI-2).
            state.exited_at = time.monotonic()
            _drain(
                readers,
                state,
                exit_drain=EXIT_DRAIN_SECONDS,
                drain_until=state.exited_at + KILL_DRAIN_SECONDS,
            )
            # ``expired.timeout`` IS the ``timeout`` argument — carried through
            # rather than re-read from the parameter because ``wait(timeout=
            # None)`` cannot raise this, a fact pyright cannot narrow through
            # ``Popen.wait`` (typeshed types the attribute ``float``, and the
            # parameter here is ``float | None``).
            raise subprocess.TimeoutExpired(
                command,
                expired.timeout,
                output=_collected(out_reader),
                stderr=_collected(err_reader),
            ) from None
        except BaseException:
            # CPython's ``except: process.kill()`` leg, at tree granularity and
            # bounded short. Nothing is drained and no output is attached: the
            # caller is unwinding.
            _end_the_tree(tree, proc, reap=INTERRUPT_REAP_SECONDS)
            raise
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=_collected(out_reader),
            stderr=_collected(err_reader),
        )
    finally:
        # FIRST, and on every path (#221 review site-exec-1). The payload above
        # is already built — ``_collected`` runs before this ``finally`` on the
        # success path, in the ``TimeoutExpired``'s constructor on the timeout
        # path, and not at all on the interrupt path — so nothing here can take
        # bytes away from a caller. A reader whose pipe never reaches EOF is
        # still left running (the leak this module states); what it no longer
        # does is accumulate.
        for reader in readers:
            reader.detach()
        # Disarmed BEFORE the release, so a late ``abort()`` cannot aim the
        # ladder at a pid this call has already stopped owning.
        if abort is not None:
            abort._finish()
        # Reached on every path that attached. POSIX: signals nothing. win32:
        # ``CloseHandle(job)``, which ends nothing at ``kill_on_close=False``.
        tree.close()
