"""#449 — a real RPC ``bash`` call awaited under a bound that names its slow phase.

WHAT THE 5 s USED TO BE. ``test_rpc_mode_bash_abort.py`` awaited every
``_handle_bash`` under ``asyncio.wait_for(..., timeout=5.0)``, and once
(windows-latest, py3.11, job 114543303754) ``echo hello`` did not return inside
it: the traceback stopped in ``_wait``'s ``asyncio.to_thread(_wait_and_stamp)``,
i.e. ``proc.wait()`` on the shell had not returned. Nothing in that log could say
whether the shell started late, ran late or was reaped late.

WHERE THE TIME GOES, MEASURED (run 38439411854, windows-latest, py3.11/3.12/3.13
x {idle, three busy loops}, 500 rounds of each arm per job, every phase stamped
with one FILETIME clock shared with the child and the kernel). The shell there
is ``pwsh`` 7.6.6 with no profile file and ``POWERSHELL_UPDATECHECK=Off``. Per
product call, max over the 3000: the spawn 11 ms, the executor picking up
``proc.wait`` 138 ms (cpu3; 25 ms idle), the loop resuming after the exit 12 ms,
the post-exit drain 0.3 ms. Everything else is the shell itself: process
creation to its first statement 250-450 ms median (p99 up to 1.23 s under
load), and its run-and-exit ~100-160 ms. The tail is the runner, not the
product: in the SAME job a bare ``Popen([pwsh, "-NoProfile", "-NonInteractive",
"-Command", ...])`` with no asyncio at all spent 5.97 s between process creation
and its first statement (py3.13 idle, round 108, where the other control took
2.59 s and the product call 0.70 s; round 107's product call 0.98 s and every
arm of round 109 0.67-0.78 s, against ~0.4 s typical); a product call once saw
the kernel's exit time 2.18 s before ``WaitForSingleObject`` returned on the
worker (py3.13 idle, round 251, the same round's control 1.26 s slow to start).
Product calls overall: median 0.38-0.65 s, max 2.70 s.

SO THE ORDINARY CALL'S BOUND IS A HANG GUARD, and :data:`HANG_GUARD_SECONDS` is
sized from that: 30 s is ~4.5x the worst stall measured in a process this code does
not own and ~11x the slowest product call, and still far under
``faulthandler_timeout`` (240 s, ``pyproject.toml``) and the windows job's 45
minutes. The ``echo`` cases assert their result and the signal registry, nothing
about time.

THE ABORT IS DIFFERENT: there the time IS the property (#449 review round 2).
``cancelled`` comes from ``sig.aborted``, so it stays True when the kill does
nothing, and an abort that kills nothing leaves ``sleep 30`` to return on its
own ~30 s later — inside a 30 s guard. So an aborted case waits for the shell's
first output first (:meth:`BashPhases.shell_started`, under the hang guard: a
PowerShell start-up stall is spent there), aborts a ``sleep 30`` that is
running, and then gives the call :data:`ABORT_ENDS_CALL_SECONDS` FROM THE ABORT.

WHAT IS LEFT FOR A STALL TO SAY. :class:`BashPhases` stamps every step of the
call — the spawn, the job attach and reader start, the executor picking up
``proc.wait``, the shell's first output, the child's own creation and exit
times from ``GetProcessTimes`` (win32 only), ``proc.wait`` returning, EOF, the
kill ladder's start and end and the kill's mark (after an abort, a cancel or a
timeout), the loop entering the drain, the return — and :meth:`BashPhases.guard` reports the gaps between them: as a
``UserWarning`` when the call took longer than :data:`SLOW_CALL_SECONDS` (above
every product call measured but the one 2.70 s stall), and in the failure message
when its bound expires. Each report names the slowest gap, so the next stall
says whether it was the shell, the kill, the reap or this process.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import types
import warnings
from collections.abc import Awaitable
from typing import Any, TypeVar

import aelix_coding_agent.tools.bash as bash_mod
import pytest

from tests.event_waits import wait_until, within

_T = TypeVar("_T")

#: The bound on one RPC bash call in these cases, and on an aborted case's wait
#: for its shell to start. A hang guard, not a budget: see the module docstring
#: for the distribution it is sized from.
HANG_GUARD_SECONDS = 30.0

#: From the abort to the call's return, for a ``sleep 30`` that was running when
#: the abort landed: a VERDICT bound, not a hang guard. An abort that kills
#: nothing returns no sooner than ~29.8 s after it (darwin, under the review's
#: sabotage: 29.86 s), twice this. What a working one costs, measured on
#: windows-latest (run 38453296707, 900 calls): median 0.063 s, p99 0.381 s, max
#: 0.547 s. There is NO ceiling by construction (#449 review round 3): the
#: ladder's bounded parts on win32 add up to 6.35 s (``taskkill.exe``'s run under
#: ``_process_tree._taskkill_tree``'s 5 s ``timeout=``, the 0.25 s reap
#: ``INTERRUPT_REAP_SECONDS``, the 1.0 s ``KILL_DRAIN_SECONDS``, one 0.1 s late
#: grace), but three parts have no bound at all - ``taskkill.exe``'s own
#: ``CreateProcess``, which runs before that timer starts; ``subprocess.run``'s
#: kill and untimed wait of a ``taskkill`` that hit its 5 s; and the worker's
#: ``proc.wait`` returning after the kill - the same return run 38439411854
#: once saw come 2.18 s after an ordinary exit (round 251). So 15 s is sized to
#: cover the bounded 6.35 s plus one stall as long as the worst this runner was
#: measured to give a process (5.97 s, a shell's start): 12.32 s, under this,
#: which is itself half of a non-killing abort's ~30 s.
ABORT_ENDS_CALL_SECONDS = 15.0

#: A call slower than this is reported phase by phase as a ``UserWarning``.
SLOW_CALL_SECONDS = 2.0

#: What the gap ENDING at each stamp covers, for the report's "slowest phase".
#: A gap is named by its two stamps, and the start alone can mislead: on win32
#: the kernel creates the child before ``Popen`` returns, so the shell's own
#: start-up is the gap from ``wait entered`` (not ``child created``) to its
#: first output in an ordinary call (measured on windows-latest, run
#: 38445492488: ``+0.30-0.36s first output``, every other gap 0.04 s or less).
_ENDS = {
    "spawn": "resolving the shell and its env",
    "child created": "CreateProcess",
    "spawned": "the rest of Popen",
    "reader started": "the job attach and the reader thread's start",
    "wait entered": "the executor picking up proc.wait",
    "first output": "the shell starting and running the command to its first output",
    "child exited": "the shell finishing and exiting",
    "eof": "the pipe's last writer closing",
    "wait returned": "proc.wait returning (the child running, or its reap)",
    "kill started": "the abort, cancel or timeout reaching the kill ladder",
    "kill ended": "the kill ladder",
    "kill marked": "_mark_the_kill stamping the kill instant after the ladder",
    "drain entered": "the loop resuming after proc.wait (after a kill: and the watcher's disarm)",
    "returned": "the post-exit drain and the return",
    "abort": "the case's own wait before it aborts",
    "(guard expired)": "the phase in flight when the bound expired",
}

#: What a gap INSIDE the kill ladder covers, whatever stamp ends it (#449 review
#: round 3). ``_end_the_tree`` is bracketed by "kill started" and "kill ended",
#: and what the kill releases is stamped inside it - EOF, on win32 the kernel's
#: exit time, the worker's ``proc.wait`` - so a stall in the ladder ends at one
#: of THOSE, whose own glosses ("the pipe's last writer closing", "the shell
#: finishing and exiting") would name the shell for what was the kill: on win32
#: ``hard_kill`` spawns ``taskkill.exe``, the same process-start stall class as
#: #449's.
_IN_THE_LADDER = (
    "inside the kill ladder: hard_kill (on win32 a taskkill.exe spawn and run, "
    "and the job), proc.kill, the reap"
)

#: Which product caller of ``_PipeReader.pin_after`` stamps what. Both call it on
#: the loop thread, so the thread cannot tell them apart, and on the abort,
#: cancel and timeout legs ``_mark_the_kill`` calls it BEFORE the drain does:
#: labelled by thread alone, a stall in an aborted case's kill would read as the
#: drain's (#449 review round 2). The worker's own call, in ``_wait_and_stamp``,
#: is "wait returned"'s instant and is not stamped again.
_PIN_CALLERS = {"_mark_the_kill": "kill marked", "_drain_after_the_exit": "drain entered"}

_FT_EPOCH_NS = 11_644_473_600 * 1_000_000_000

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.GetSystemTimePreciseAsFileTime.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    _k32.GetSystemTimePreciseAsFileTime.restype = None
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    _k32.GetProcessTimes.restype = wintypes.BOOL

    def _now_ns() -> int:
        """Wall clock in ns since 1601, the timebase ``GetProcessTimes`` answers in."""

        value = ctypes.c_ulonglong()
        _k32.GetSystemTimePreciseAsFileTime(ctypes.byref(value))
        return value.value * 100

    def _child_times(proc: Any) -> tuple[int | None, int | None]:
        handle = getattr(proc, "_handle", None)
        if handle is None:
            return None, None
        created, exited, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
        ok = _k32.GetProcessTimes(
            int(handle),
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None, None
        # A child still running has no exit time yet (zero).
        return created.value * 100 or None, exited.value * 100 or None

else:

    def _now_ns() -> int:
        return time.time_ns() + _FT_EPOCH_NS

    def _child_times(proc: Any) -> tuple[int | None, int | None]:
        return None, None


class BashPhases:
    """Stamps of one ``_LocalBashOperations.exec`` call, and the guard that reads them.

    Installed by :meth:`install` on the bash module only: ``Popen`` through a
    module-level view of :mod:`subprocess` (so a ``taskkill`` the kill ladder
    spawns from ``_process_tree`` is not mistaken for the shell) and the module's
    ``_PipeReader``, and the module's ``_end_the_tree`` (the kill ladder). Only
    the FIRST spawn of the case is stamped.

    ``delays`` injects a stall at a named stamp, before it is taken — for the
    cases that pin the report itself, never for a product case.
    """

    def __init__(self) -> None:
        self.stamps: dict[str, int] = {}
        self.delays: dict[str, float] = {}
        self._proc: Any = None
        self._loop_thread = threading.get_ident()

    # -- stamping ---------------------------------------------------------

    def stamp(self, name: str) -> None:
        delay = self.delays.get(name)
        if delay:
            time.sleep(delay)
        self.stamps.setdefault(name, _now_ns())

    def install(self, monkeypatch: pytest.MonkeyPatch) -> BashPhases:
        phases = self
        real_popen = subprocess.Popen
        real_reader = bash_mod._PipeReader
        real_end_the_tree = bash_mod._end_the_tree

        class _StampedPopen(real_popen):  # type: ignore[misc,valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                first = phases._proc is None
                if first:
                    phases.stamp("spawn")
                super().__init__(*args, **kwargs)
                if not first:
                    return
                phases._proc = self
                phases.stamp("spawned")
                real_wait = self.wait

                def wait(timeout: float | None = None) -> int:
                    on_worker = threading.get_ident() != phases._loop_thread
                    if on_worker:
                        phases.stamp("wait entered")
                    try:
                        return real_wait(timeout)
                    finally:
                        if on_worker:
                            phases.stamp("wait returned")

                self.wait = wait  # type: ignore[method-assign]

        class _StampedReader(real_reader):  # type: ignore[misc,valid-type]
            def __init__(
                self,
                stream: Any,
                state: Any,
                *,
                on_chunk: Any = None,
                on_eof: Any = None,
                **kwargs: Any,
            ) -> None:
                def chunk(data: bytes) -> None:
                    phases.stamp("first output")
                    on_chunk(data)

                def eof() -> None:
                    phases.stamp("eof")
                    on_eof()

                super().__init__(
                    stream,
                    state,
                    on_chunk=chunk if on_chunk is not None else None,
                    on_eof=eof if on_eof is not None else None,
                    **kwargs,
                )

            def start(self) -> None:
                phases.stamp("reader started")
                super().start()

            def pin_after(self, instant: float) -> None:
                # Named by its CALLER (see ``_PIN_CALLERS``): the kill's mark
                # and the drain's first statement are both on the loop thread.
                name = _PIN_CALLERS.get(sys._getframe(1).f_code.co_name)
                if name is not None:
                    phases.stamp(name)
                super().pin_after(instant)

        def _stamped_end_the_tree(*args: Any, **kwargs: Any) -> None:
            # The kill ladder of all three legs (abort watcher, timeout,
            # cancel), read through the module global at call time, so a
            # stall inside it is its own phase (#449 review round 3).
            phases.stamp("kill started")
            try:
                real_end_the_tree(*args, **kwargs)
            finally:
                phases.stamp("kill ended")

        view = types.SimpleNamespace(**vars(subprocess))
        view.Popen = _StampedPopen
        monkeypatch.setattr(bash_mod, "subprocess", view)
        monkeypatch.setattr(bash_mod, "_PipeReader", _StampedReader)
        monkeypatch.setattr(bash_mod, "_end_the_tree", _stamped_end_the_tree)
        return self

    # -- reporting --------------------------------------------------------

    def _ordered(self, *, now: int | None) -> list[tuple[str, int]]:
        stamps = dict(self.stamps)
        if self._proc is not None:
            created, exited = _child_times(self._proc)
            if created is not None:
                stamps["child created"] = created
            if exited is not None:
                stamps["child exited"] = exited
        if now is not None:
            stamps["(guard expired)"] = now
        return sorted(stamps.items(), key=lambda item: item[1])

    def gaps(self, *, now: int | None = None) -> list[tuple[str, str, int, str]]:
        """``(previous stamp, stamp, gap in ns, what the gap covers)``, in time order.

        A gap is glossed by the stamp that ends it (:data:`_ENDS`), except one
        that lies inside the kill ladder, which is glossed as the ladder
        whatever stamp ends it (:data:`_IN_THE_LADDER`).
        """

        ordered = self._ordered(now=now)
        stamps = dict(ordered)
        ladder_from = stamps.get("kill started")
        ladder_to = stamps.get("kill ended")
        gaps: list[tuple[str, str, int, str]] = []
        for (prev, t_prev), (name, t) in zip(ordered, ordered[1:], strict=False):
            inside = (
                ladder_from is not None
                and t_prev >= ladder_from
                and (ladder_to is None or t <= ladder_to)
            )
            meaning = _IN_THE_LADDER if inside else _ENDS.get(name, name)
            gaps.append((prev, name, t - t_prev, meaning))
        return gaps

    def report(self, *, now: int | None = None) -> tuple[str, str | None]:
        """Every stamp in time order with the gap before it, and the slowest gap."""

        ordered = self._ordered(now=now)
        if not ordered:
            return "no stamps (the call never spawned)", None
        parts = [ordered[0][0]]
        slowest: tuple[int, str] | None = None
        for prev, name, gap, meaning in self.gaps(now=now):
            parts.append(f"+{gap / 1e9:.3f}s {name}")
            if slowest is None or gap > slowest[0]:
                slowest = (gap, f"{prev} -> {name} ({gap / 1e9:.3f}s: {meaning})")
        return " | ".join(parts), slowest[1] if slowest else None

    def _describe(self, *, now: int | None = None) -> str:
        timeline, slowest = self.report(now=now)
        return f"slowest phase {slowest}; timeline: {timeline}"

    async def shell_started(
        self,
        what: str = "waiting for the shell's first output before the abort",
        *,
        slow: float = SLOW_CALL_SECONDS,
    ) -> None:
        """Wait, under the hang guard, until the shell has written its first output.

        For the aborted cases: the abort then lands on a command that is
        RUNNING, whatever the shell's start-up cost — the ``started`` event
        ``tests/tools/test_abort_signal.py``'s abort and cancel cases wait for
        (#330), read here from the reader's own stamp. A start slower than
        ``slow`` is reported phase by phase as well: it is
        spent before the abort's own bound starts, and would otherwise say
        nothing.
        """

        waited = await wait_until(
            lambda: "first output" in self.stamps,
            timeout=HANG_GUARD_SECONDS,
            what=what,
            detail=self._describe,
            interval=0.01,
        )
        if waited > slow:
            warnings.warn(
                f"#449 {what} on {sys.platform} took {waited:.3f}s (slow above "
                f"{slow}s, hang guard {HANG_GUARD_SECONDS}s); "
                f"{self._describe()}",
                stacklevel=2,
            )

    async def guard(
        self,
        awaitable: Awaitable[_T],
        what: str,
        *,
        bound: float = HANG_GUARD_SECONDS,
        slow: float = SLOW_CALL_SECONDS,
        kind: str = "hang guard",
    ) -> _T:
        """Await ``awaitable`` under ``bound``; name the slow phase if there is one.

        The bound is :func:`tests.event_waits.within`'s (#330, ADR-0225), so a
        ``TimeoutError`` the CALL raises comes out as itself, and only the
        bound's own expiry fails as "did not finish within", with this call's
        timeline appended. Its ``(guard expired)`` stamp is the expiry instant;
        what the cancellation that follows did (the kill it runs) is stamped
        after it.
        """

        started_ns = _now_ns()
        self.stamps.setdefault("call", started_ns)
        expires_ns = started_ns + round(bound * 1e9)
        started = time.monotonic()
        result = await within(
            awaitable,
            bound=bound,
            what=what,
            kind=kind,
            detail=lambda: self._describe(now=expires_ns),
        )
        elapsed = time.monotonic() - started
        self.stamps.setdefault("returned", _now_ns())
        if elapsed > slow:
            timeline, slowest = self.report()
            warnings.warn(
                f"#449 {what} on {sys.platform} took {elapsed:.3f}s (slow above {slow}s, "
                f"{kind} {bound}s); slowest phase {slowest}; timeline: {timeline}",
                stacklevel=2,
            )
        return result
