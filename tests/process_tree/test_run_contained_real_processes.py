"""Real children for ``run_contained`` (#221). Every LATENCY claim lives here.

``test_run_contained.py`` pins dispatch and order against a fake ``Popen`` on
every runner. This file spawns actual processes, so each case measures what the
host really does — the POSIX arms run on ubuntu and macOS, the win32 arms only
on ``windows-latest``. That is integration coverage supplied by the gating
windows leg and NOT a way around the no-``skipif`` rule: every case below runs
everywhere, and where the ANSWER is genuinely platform-specific (a ``setsid``
grandchild is outside a process group and inside a job) the case asserts both
arms and documents the asymmetry.

THE ONE THAT WOULD HANG ``main``.
:func:`test_timeout_ends_a_pipe_holding_grandchild_and_returns_in_bound` is the
shape #221 exists for: on Windows CPython's ``run()`` follows its kill with an
UNTIMED ``communicate()`` to join the reader threads, and a descendant that
inherited the pipes keeps the write end open, so that join never returns. It is
not demonstrated as a red test on ``main`` — it would hang the leg for
GitHub's 360-minute default — and its ``_run_bounded`` bound plus its
``warnings.warn`` of the elapsed are the guard and the measurement instead.

EVERY ROOT ANNOUNCES ITS TREE BEFORE IT SLEEPS. A case that asserted "the
grandchild died" over a grandchild that was never running would be green while
measuring nothing (#203's lesson). So each root writes ``"<root pid>
<grandchild pid>"`` to a marker file — a temp file and ``os.replace``, so a
half-written line can never be read — and a :class:`_Registrar` started BEFORE
the bounded call polls that file on its own daemon thread and appends both pids
to ``strays`` the moment it lands. In flight, not afterwards: the failure
``_run_bounded`` exists to produce is a call that never returns, and registering
after it would leak exactly the tree the watchdog just caught — measured under a
mutant, a 60 s root left at ``ppid 1``, unregistered and unreaped (#221 review
T4).

Liveness is ``tests/process_probe`` and never ``os.kill(pid, 0)``: on Windows
signal 0 falls through CPython's ``os_kill_impl`` to ``TerminateProcess``, so
the "probe" would cause the death it claims to observe (#203).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pytest
from aelix_ai.utils._process_tree import (
    EXIT_DRAIN_SECONDS,
    INTERRUPT_REAP_SECONDS,
    KILL_DRAIN_SECONDS,
    REAP_GRACE_SECONDS,
    AbortHandle,
    run_contained,
)

from tests.process_probe import STATE_ALIVE, probe_state
from tests.process_tree.test_process_tree_real_processes import (
    DEADLINE,
    _await_dead,
    _reap,
)
from tests.process_tree.test_process_tree_real_processes import (
    strays as _strays_fixture,
)
from tests.process_tree.test_run_contained import _run_bounded

_T = TypeVar("_T")

#: The sibling file's cleanup fixture, RE-EXPORTED rather than re-written: one
#: definition of "pids this case is responsible for" and one root-only reaper
#: (a cleanup that reached for a process GROUP would be reaching for the very
#: mechanism under test). Bound through an assignment because a parameter named
#: ``strays`` would otherwise read to ruff as a redefinition of the import.
strays = _strays_fixture

#: In the argv of every sleeper these cases spawn, so a leak is greppable
#: (``pgrep -fl aelix221``) rather than anonymous.
MARK = "aelix221"

#: Seconds a root that is meant to outlive its timeout sleeps for.
OUTLIVE = 60.0

#: A root that announces its tree and then holds still.
#:
#: ``argv``: marker path, shape, how long the root holds, how long the
#: grandchild sleeps. ``inherit`` leaves the grandchild holding the root's
#: stdout/stderr PIPES — the shape that keeps ``communicate()`` waiting forever
#: on Windows; ``own-session`` puts it outside our process group (and still
#: inside a job); ``no-pipe`` gives it ``DEVNULL`` so it holds nothing and is a
#: deliberate outliver rather than a reason the call cannot return.
ROOT_SOURCE = """\
import os
import subprocess
import sys
import time

marker, shape, hold, nap = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
extra = {}
if shape == "own-session":
    from aelix_ai.utils._process_tree import containment_spawn_kwargs

    extra = containment_spawn_kwargs(new_session=True)
if shape == "no-pipe":
    extra["stdout"] = subprocess.DEVNULL
    extra["stderr"] = subprocess.DEVNULL
child = subprocess.Popen(
    [sys.executable, "-c", "import sys, time; time.sleep(float(sys.argv[1]))", nap, "@MARK@"],
    stdin=subprocess.DEVNULL,
    **extra,
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {child.pid}\\n")
os.replace(tmp, marker)
time.sleep(hold)
""".replace("@MARK@", MARK)

#: The success shape #221's second bug is about, with the discriminating tail.
#:
#: The root prints ``done`` at once and then holds still for half a second, so
#: by the time it exits the last chunk is 0.5 s old — a drain whose idle timer
#: is measured from the LAST CHUNK rather than armed at the EXIT returns
#: immediately here and loses everything after it. The holder waits for the
#: root's own "I am leaving" flag and then writes ``late`` 50 ms later, so that
#: byte is STRICTLY after the exit on every platform (``os.getppid`` does not
#: change on Windows when the parent dies, so the flag file is the portable
#: happens-after, not the ppid).
ROOT_WITH_A_LATE_TAIL = """\
import os
import subprocess
import sys
import time

marker, leaving = sys.argv[1], sys.argv[2]
child = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import os, sys, time\\n"
        "while not os.path.exists(sys.argv[1]):\\n"
        "    time.sleep(0.01)\\n"
        "time.sleep(0.05)\\n"
        "sys.stdout.buffer.write(b'late\\\\n')\\n"
        "sys.stdout.buffer.flush()\\n"
        "time.sleep(30)\\n",
        leaving,
        "@MARK@",
    ],
    stdin=subprocess.DEVNULL,
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {child.pid}\\n")
os.replace(tmp, marker)
sys.stdout.buffer.write(b"done\\n")
sys.stdout.buffer.flush()
time.sleep(0.5)
with open(leaving, "w", encoding="utf-8") as handle:
    handle.write("gone")
""".replace("@MARK@", MARK)


#: Reports the child's SESSION, then opens the controlling terminal and reports
#: the errno BY NAME.
#:
#: The session line is the discriminator (#221 review MUT-2/T2): the ENXIO is
#: NOT one, because no CI leg here owns a controlling terminal — measured, with
#: ``process_group=0`` substituted for ``start_new_session=True`` the errno half
#: still passes in a tty-less run and fails only under a real pty. ``sid`` is
#: the half that does not depend on the parent owning a tty.
#:
#: ``OSError``'s own message is the platform's ``strerror`` ("Device not
#: configured" on darwin, "No such device or address" on Linux), which is not a
#: stable thing to assert on; the symbolic name is, and it is the one the
#: decision is stated in — with no controlling terminal the open fails at once
#: with ``ENXIO`` where the tty driver answers and ``ENOENT`` where ``/dev/tty``
#: is not even present. What must NOT happen is the third outcome: the child
#: STOPPED on ``SIGTTIN``.
NO_TTY_SOURCE = """\
import errno
import os
import sys

sys.stderr.write(f"sid={os.getsid(0)} pid={os.getpid()} ")
try:
    os.open("/dev/tty", os.O_RDONLY)
except OSError as exc:
    sys.stderr.write(errno.errorcode.get(exc.errno, str(exc.errno)))
    raise SystemExit(1) from None
"""


def _root_argv(marker: Path, shape: str, *, hold: float, nap: float = OUTLIVE) -> list[str]:
    return [sys.executable, "-c", ROOT_SOURCE, str(marker), shape, str(hold), str(nap)]


def _await_marker(marker: Path, strays: list[int], *, fields: int = 2) -> tuple[int, ...] | None:
    """Poll for the root's announcement, registering every pid in it as it lands.

    Returns ``None`` rather than failing so a caller can run this from a
    ``finally`` without masking the real assertion; the caller states the
    vacuity guard itself. ``fields`` is how many pids the root writes — two for
    the roots here (root + grandchild), one at the exec site, whose roots
    announce only themselves.
    """

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            raw = marker.read_text(encoding="utf-8").split()
        except OSError:
            raw = []
        if len(raw) == fields:
            pids = tuple(int(field) for field in raw)
            strays.extend(pids)
            return pids
        time.sleep(0.02)
    return None


class _Registrar(threading.Thread):
    """Polls a marker on its own daemon thread WHILE the call is in flight.

    That is the whole point of it (#221 review T4). Polling after the bounded
    call returns cannot register the tree of a call that never returned — which
    is the exact failure ``_run_bounded`` exists to produce, and which was
    measured to leave a 60 s root at ``ppid 1``, unregistered and unreaped.

    ``on_announce`` is the seam the abort case drives: the earliest instant at
    which the tree is known to EXIST, so a cancellation aimed at it cannot land
    before the root has forked (measured in D.2.4: injected at the top of the
    first ``wait``, the interrupt beat the fork and the marker never appeared).
    """

    def __init__(
        self,
        marker: Path,
        strays: list[int],
        *,
        fields: int = 2,
        on_announce: Callable[[tuple[int, ...]], None] | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self._marker = marker
        self._strays = strays
        self._fields = fields
        self._on_announce = on_announce
        #: The pids, once seen. ``None`` while the root has not announced.
        self.pids: tuple[int, ...] | None = None

    def run(self) -> None:
        pids = _await_marker(self._marker, self._strays, fields=self._fields)
        self.pids = pids
        if pids is not None and self._on_announce is not None:
            self._on_announce(pids)

    def settle(self) -> tuple[int, ...] | None:
        """Join the poll and return what it saw — ``None`` if it never landed.

        Shaped for a ``finally``: it never raises, so the case's own assertion
        is what a reader sees when something else went wrong first.
        """

        self.join(6.0)
        return self.pids


def _registrar(
    marker: Path,
    strays: list[int],
    *,
    fields: int = 2,
    on_announce: Callable[[tuple[int, ...]], None] | None = None,
) -> _Registrar:
    """Start a :class:`_Registrar`. Call it BEFORE the bounded call, always."""

    thread = _Registrar(marker, strays, fields=fields, on_announce=on_announce)
    thread.start()
    return thread


def _with_planted_stdin(call: Callable[[], _T]) -> _T:
    """Run ``call`` with fd 0 pointing at readable bytes instead of ``/dev/null``.

    WITHOUT THIS A STDIN CASE IS VACUOUS — measured (#221 review SITE-2).
    pytest's default ``--capture=fd`` does ``dup2(open(os.devnull), 0)`` for
    every test (``_pytest/capture.py``, ``FDCaptureBase.__init__``: ``if
    targetfd == 0``), so a child that merely INHERITED stdin also reads ``''``:
    the naive form passed unchanged against ``main``'s no-``stdin=`` shape at
    both stdin cases (mutated: 1 passed each). Planting readable bytes is what
    makes inheritance observable — with them, the mutant reads
    ``'SHOULD NOT BE READ\n'``.

    POSIX-only discrimination, stated rather than assumed: on win32 CPython's
    ``Popen._get_handles`` takes an inherited stdin from
    ``_winapi.GetStdHandle(STD_INPUT_HANDLE)``, not from fd 0, so the ``dup2``
    below does not reach a win32 child and the case stays green there without
    discriminating. The win32 arm's guard is the kwarg assertion in
    ``test_run_contained.py::test_spawn_kwargs_and_stdio``.
    """

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"SHOULD NOT BE READ\n")
    os.close(write_fd)
    saved = os.dup(0)
    try:
        os.dup2(read_fd, 0)
        os.close(read_fd)
        if sys.platform != "win32":
            # The self-check, so this can never go silently vacuous again.
            assert os.fstat(0).st_ino != os.stat(os.devnull).st_ino, (
                "fd 0 is /dev/null — the case cannot tell DEVNULL from inheritance"
            )
        return call()
    finally:
        os.dup2(saved, 0)
        os.close(saved)


# === the two shapes of #1 ===================================================


def test_timeout_ends_a_pipe_holding_grandchild_and_returns_in_bound(
    tmp_path: Path, strays: list[int]
) -> None:
    """The headline: the timeout takes the whole tree, and the call comes back.

    On POSIX ``main`` left the grandchild running (measured ``survivors=1`` at
    every one of the three sites, and at the real ``exec`` surface under a real
    model the sleeper survived at ``ppid 1`` in aelix's OWN process group). On
    Windows ``main`` never returned at all, because the grandchild holds the
    pipes CPython's post-kill ``communicate()`` is waiting to see EOF on — THIS
    IS THE CASE THAT WOULD HANG THE LEG THERE.
    """

    marker = tmp_path / "pids.txt"
    timeout = 1.0
    bound = timeout + REAP_GRACE_SECONDS + KILL_DRAIN_SECONDS + 5
    registrar = _registrar(marker, strays)
    started = time.monotonic()

    try:
        with pytest.raises(subprocess.TimeoutExpired):
            _run_bounded(
                lambda: run_contained(_root_argv(marker, "inherit", hold=OUTLIVE), timeout=timeout),
                bound,
                "timeout with a pipe-holding grandchild",
            )
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    root_pid, grandchild = pids
    # The messages carry the elapsed and the LAST observed state because this
    # case went red once in ~15 executions during the review and the traceback
    # was lost (#221 review MUT-5): a CI red must name which poll expired
    # rather than print ``assert 'alive' != 'alive'``.
    grandchild_state = _await_dead(grandchild)
    assert grandchild_state != STATE_ALIVE, (
        f"grandchild {grandchild} still {grandchild_state} after {DEADLINE}s of polling; "
        f"run_contained returned at {elapsed:.3f}s"
    )
    root_state = _await_dead(root_pid)
    assert root_state != STATE_ALIVE, (
        f"root {root_pid} still {root_state} after {DEADLINE}s of polling; "
        f"run_contained returned at {elapsed:.3f}s"
    )
    warnings.warn(
        f"run_contained timeout path: {elapsed:.3f}s on {sys.platform}",
        stacklevel=1,
    )


def test_an_exited_root_with_a_pipe_holder_is_a_success_and_keeps_the_tail(
    tmp_path: Path, strays: list[int]
) -> None:
    """Bug 2: a root that exited 0 is a SUCCESS, and its tail is not binned.

    Measured on ``main``: the same shape came back as ``TimeoutExpired`` after
    the whole timeout with ``out=b'done\\n'`` (3.32 s under a 1 s bound), and
    through the real ``exec`` surface as ``code=124 killed=True
    stdout='done\\n'``. Two mutants die here at once — deleting the drain
    (``late`` disappears) and stamping the idle timer only on chunks (the root
    was quiet for 0.5 s before exiting, so the timer is already expired at the
    exit and ``late`` disappears again).

    The holder is ALIVE afterwards on every platform: ``kill_on_close=False``
    and a releasing ``close()`` are what keep a helper a command deliberately
    backgrounded.

    Both writers go through ``sys.stdout.buffer`` on purpose: the text layer
    writes ``os.linesep``, and the windows leg of the first CI run returned
    ``b'done\\r\\nlate\\r\\n'`` against this exact-bytes assertion (run
    33959649661). The helper is a byte pipe; the sites own their own newline
    normalisation.
    """

    marker = tmp_path / "pids.txt"
    leaving = tmp_path / "leaving.flag"
    argv = [sys.executable, "-c", ROOT_WITH_A_LATE_TAIL, str(marker), str(leaving)]
    registrar = _registrar(marker, strays)
    started = time.monotonic()

    try:
        result = _run_bounded(
            lambda: run_contained(argv, timeout=None),
            0.5 + EXIT_DRAIN_SECONDS + 2.0 + 5,
            "an exited root with a pipe holder",
        )
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    _, holder = pids
    assert result.returncode == 0
    assert result.stdout == b"done\nlate\n"
    assert elapsed <= 0.5 + EXIT_DRAIN_SECONDS + 2.0
    assert probe_state(holder) == STATE_ALIVE
    warnings.warn(
        f"run_contained exited-root drain: {elapsed:.3f}s on {sys.platform}",
        stacklevel=1,
    )


# === the asymmetry, and the leaked-reader bound =============================


def test_a_setsid_grandchild_is_reached_by_the_job_and_not_by_the_group(
    tmp_path: Path, strays: list[int]
) -> None:
    """The asymmetry ADR-0238 states, measured at THIS helper.

    A grandchild that made itself a session leader has left our process group,
    so ``killpg`` cannot reach it — by design: the walk that does reach it is
    ``aelix_agents/reaper.py``'s (ADR-0197 I2). A job has no such hole.

    What matters here beyond the asymmetry is that the survivor does not cost
    the CALLER anything: it holds the pipes and will never close them, so the
    post-kill drain must end on its own idle bound. Under revision 1 (join the
    readers for the rest of ``reap_grace``) the same shape cost a flat
    ``6.011 s`` for a 1 s timeout and bought zero bytes; the bound asserted
    below EXCLUDES ``reap_grace`` for that reason, and revision 1 fails it
    (rebuilt as a mutant and measured: 6.010-6.024 s, green at the old 8.0,
    red at 3.5 — #221 review T1/MUT-3).

    The cap ITSELF is not observable here: this holder is silent, so the drain
    ends on the 0.1 s idle rule and substituting ``reap_grace`` for
    ``KILL_DRAIN_SECONDS`` changes the measured 1.111 s not at all.
    ``test_run_contained.py::test_a_chatty_holder_past_the_kill_is_cut_at_kill_drain``
    is where that number binds.
    """

    marker = tmp_path / "pids.txt"
    timeout = 1.0
    # NOT ``+ REAP_GRACE_SECONDS``. A SIGKILLed root is reaped in milliseconds
    # — this case measures 1.107-1.118 s end to end on darwin — so folding the
    # reap grace in made the bound 8.0 s, which is precisely what let revision
    # 1's 6.011 s through the assertion that claims to have replaced it.
    bound = timeout + KILL_DRAIN_SECONDS + 1.5
    if sys.platform == "win32":
        # ``hard_kill`` shells out to ``taskkill.exe /T /F`` FIRST and
        # unconditionally on every win32 timeout, under its own ``timeout=5``;
        # #220's leg measured that rung at 0.031 s, and the ``warnings.warn``
        # below is what reports this helper's real number from the leg.
        bound += 5.0
    registrar = _registrar(marker, strays)
    started = time.monotonic()

    try:
        with pytest.raises(subprocess.TimeoutExpired):
            _run_bounded(
                lambda: run_contained(
                    _root_argv(marker, "own-session", hold=OUTLIVE), timeout=timeout
                ),
                bound + 5,
                "a setsid grandchild",
            )
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    root_pid, grandchild = pids
    root_state = _await_dead(root_pid)
    assert root_state != STATE_ALIVE, (
        f"root {root_pid} still {root_state} after {DEADLINE}s; returned at {elapsed:.3f}s"
    )
    assert elapsed <= bound
    if sys.platform == "win32":
        assert _await_dead(grandchild) != STATE_ALIVE
    else:
        # Give the kill the same window the win32 arm gets before calling the
        # survival real rather than merely not-yet-observed.
        time.sleep(0.5)
        assert probe_state(grandchild) == STATE_ALIVE
    warnings.warn(
        f"run_contained leaked-reader bound: {elapsed:.3f}s on {sys.platform}",
        stacklevel=1,
    )


def test_a_deliberate_outliver_survives_success_and_dies_on_timeout(
    tmp_path: Path, strays: list[int]
) -> None:
    """§A.5's third bullet: the two paths differ, and that is the decision.

    A member still alive after the root exited is NOT a stray by definition —
    ``git credential-cache--daemon`` is started without ``setsid`` and without
    a wait, so it re-parents to init and stays in the clone's group (measured
    ``ppid 1``, ``pgid`` == the git child's). The timeout ``killpg`` reaches it,
    which is accepted and bounded; a SUCCESSFUL run's ``close()`` must not,
    which is what ``kill_on_close=False`` buys.

    The grandchild here holds no pipe, so nothing about the drain is involved:
    the only question asked is who the kill reaches.

    Both halves register through a :class:`_Registrar` started BEFORE the call
    and settled in a ``finally``, like every other case in this file (#221
    review T4). The timeout half is the one that made that mandatory: it is the
    only place here where the call can fail to return, and registering
    afterwards left a 60 s root at ``ppid 1``, unregistered and unreaped, when
    it did.

    THE TIMEOUT HALF'S GRANDCHILD SLEEPS ``OUTLIVE``, NOT 5 s (#221 review
    win32-arm-1). At ``nap=5.0`` it exited of old age inside ``_await_dead``'s
    own 5.0 s poll, so the half asserted nothing: rebuilt as a mutant with
    ``ProcessTree.hard_kill`` a no-op, it stayed GREEN at 5.09 s — 0.44 s of
    slack was all that separated "the ladder reached it" from "it finished
    sleeping". A 60 s nap leaves no such reading; the ``strays`` fixture reaps
    it if the kill really is gone. The success half keeps ``nap=5.0``: there
    the claim is that the survivor is STILL ALIVE, which a long nap can only
    make harder to fake, and it is reaped explicitly.
    """

    success_marker = tmp_path / "success.txt"
    success_registrar = _registrar(success_marker, strays)
    try:
        result = _run_bounded(
            lambda: run_contained(
                _root_argv(success_marker, "no-pipe", hold=0.0, nap=5.0), timeout=5.0
            ),
            10.0,
            "a deliberate outliver, success path",
        )
    finally:
        success_pids = success_registrar.settle()

    assert success_pids is not None, "the root never announced its tree — the case measured nothing"
    _, survivor = success_pids
    assert result.returncode == 0
    assert probe_state(survivor) == STATE_ALIVE
    _reap(survivor)

    timeout_marker = tmp_path / "timeout.txt"
    timeout_registrar = _registrar(timeout_marker, strays)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            _run_bounded(
                lambda: run_contained(
                    _root_argv(timeout_marker, "no-pipe", hold=OUTLIVE, nap=OUTLIVE),
                    timeout=0.5,
                ),
                0.5 + REAP_GRACE_SECONDS + KILL_DRAIN_SECONDS + 5,
                "a deliberate outliver, timeout path",
            )
    finally:
        timeout_pids = timeout_registrar.settle()

    assert timeout_pids is not None, "the root never announced its tree — the case measured nothing"
    _, doomed = timeout_pids
    assert _await_dead(doomed) != STATE_ALIVE


# === the interrupt leg, against a real tree =================================


def test_an_interrupt_ends_a_real_tree(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """^C does NOT reach these children, so the helper has to.

    ``start_new_session=True`` takes the child out of the terminal's foreground
    process group and ``CREATE_NEW_PROCESS_GROUP`` disables the console's
    Ctrl+C for it, which makes ``Popen.__exit__``'s stated assumption ("we
    assume the SIGINT was also already sent to our child processes") false.
    Measured under a real pty: ``^C`` -> parent ``KeyboardInterrupt``, contained
    child alive. The interrupt is injected at the wait rather than by signalling
    this interpreter, so the case is the same on every platform.
    """

    marker = tmp_path / "pids.txt"
    real_wait = subprocess.Popen.wait
    interrupted_at: list[float] = []

    def wait_once_interrupted(self: subprocess.Popen[bytes], timeout: float | None = None) -> int:
        if not interrupted_at:
            # The tree has to EXIST before the ladder runs, or the case measures
            # a kill with nothing under it: injected at the top of the very
            # first wait, the interrupt lands microseconds after the spawn and
            # before the root has forked its grandchild (measured — the marker
            # never appeared and the case failed its own vacuity guard).
            deadline = time.monotonic() + 5.0
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            interrupted_at.append(time.monotonic())
            raise KeyboardInterrupt
        return real_wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", wait_once_interrupted)

    registrar = _registrar(marker, strays)

    try:
        with pytest.raises(KeyboardInterrupt):
            _run_bounded(
                lambda: run_contained(_root_argv(marker, "inherit", hold=OUTLIVE), timeout=OUTLIVE),
                DEADLINE + 5,
                "an interrupt against a real tree",
            )
    finally:
        returned_at = time.monotonic()
        pids = registrar.settle()

    assert interrupted_at, "the injected interrupt never fired — the case measured nothing"
    assert pids is not None, "the root never announced its tree — the case measured nothing"
    root_pid, grandchild = pids
    assert _await_dead(root_pid) != STATE_ALIVE
    assert _await_dead(grandchild) != STATE_ALIVE
    # The ladder's own bound: ``hard_kill``, the belt, and a 0.25 s reap. There
    # is no drain and no output on this path, so nothing else is in the number.
    assert returned_at - interrupted_at[0] < INTERRUPT_REAP_SECONDS + 1.0


def test_abort_ends_a_real_tree_promptly(tmp_path: Path, strays: list[int]) -> None:
    """§K.1 against real processes: the leg ``ExtensionAPI.exec`` actually has.

    The case above injects its ``KeyboardInterrupt`` INSIDE the call, which is
    the one thing a terminal ^C cannot do to ``exec``: that ``await`` is on an
    ``asyncio.to_thread`` worker and CPython delivers signals to the main thread
    only (#221 review SITE-1, measured — two ^C, ladder calls ``[]``). So here
    the call runs on a helper thread and the abort comes from THIS one, which is
    the production shape.

    ``timeout=None`` is ``api.exec``'s default and is what makes the claim
    sharp: without the handle nothing bounds this call at all, and the measured
    cost on ``main``-plus-containment was the command's whole remaining life
    (``28.58 s`` of a 30 s child). The grandchild holds the pipes, so the return
    also exercises the ordinary drain — ``abort()`` adds no second exit path.

    The abort is fired 0.3 s after the ROOT ANNOUNCED rather than 0.3 s after
    the spawn: aimed by the clock alone it can land before the fork, and the
    case would then measure a kill with nothing under it (measured in
    :func:`test_an_interrupt_ends_a_real_tree` — the marker never appeared).
    """

    marker = tmp_path / "pids.txt"
    handle = AbortHandle()
    announced = threading.Event()
    registrar = _registrar(marker, strays, on_announce=lambda _pids: announced.set())
    returned: list[subprocess.CompletedProcess[bytes]] = []
    failed: list[BaseException] = []

    def _worker() -> None:
        try:
            returned.append(
                run_contained(
                    _root_argv(marker, "inherit", hold=OUTLIVE), timeout=None, abort=handle
                )
            )
        except BaseException as exc:  # noqa: BLE001 — re-raised in the caller
            failed.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    try:
        assert announced.wait(5.0), "the root never announced its tree — the case measured nothing"
        time.sleep(0.3)
        aborted_at = time.monotonic()
        sent = handle.abort()
        thread.join(2.0)
        joined_at = time.monotonic()
    finally:
        pids = registrar.settle()

    if failed:
        raise failed[0]
    assert sent is True, "the abort found nothing attached — the case measured nothing"
    assert not thread.is_alive(), (
        f"the call was still blocked {joined_at - aborted_at:.3f}s after the abort"
    )
    assert pids is not None
    root_pid, grandchild = pids
    root_state = _await_dead(root_pid)
    assert root_state != STATE_ALIVE, (
        f"root {root_pid} still {root_state} after {DEADLINE}s; abort returned at "
        f"{joined_at - aborted_at:.3f}s"
    )
    grandchild_state = _await_dead(grandchild)
    assert grandchild_state != STATE_ALIVE, (
        f"grandchild {grandchild} still {grandchild_state} after {DEADLINE}s; abort returned at "
        f"{joined_at - aborted_at:.3f}s"
    )
    # A ``CompletedProcess``, not an exception: nobody is left to raise into.
    # ``-9`` is the POSIX SIGKILL; on win32 ``taskkill /F`` gives the root ``1``.
    assert returned[0].returncode != 0
    if sys.platform != "win32":
        assert returned[0].returncode == -9


# === the plain contract =====================================================


def test_stdin_is_devnull(strays: list[int]) -> None:
    """Pi's ``execCommand`` spawns ``stdio: ["ignore", "pipe", "pipe"]``.

    Today's ``api.exec`` INHERITS the TUI's stdin, so a command that reads it
    competes with the terminal for the user's keystrokes. ``''`` is what a
    child reading ``/dev/null`` (``NUL`` on win32) sees.

    THE ASSERTION IS ONLY WORTH ANYTHING INSIDE
    :func:`_with_planted_stdin` (#221 review SITE-2). Under pytest's default
    ``--capture=fd`` this process's fd 0 already IS ``/dev/null``, so a child
    that merely inherited stdin reads ``''`` too and the naive form passed
    unchanged against ``main``'s no-``stdin=`` shape (mutated: 1 passed).
    With readable bytes planted there the mutant reads ``'SHOULD NOT BE
    READ\\n'`` and this case is red.
    """

    result = _with_planted_stdin(
        lambda: _run_bounded(
            lambda: run_contained(
                [sys.executable, "-c", "import sys; sys.stdout.write(repr(sys.stdin.read()))"],
                timeout=DEADLINE,
            ),
            DEADLINE + 5,
            "stdin is devnull",
        )
    )

    assert result.stdout == b"''"
    assert result.returncode == 0


def test_spawn_error_propagates(strays: list[int]) -> None:
    """No tree exists yet, so the exception is the stdlib's, unwrapped."""

    with pytest.raises(FileNotFoundError):
        _run_bounded(
            lambda: run_contained(
                [os.path.join(os.getcwd(), "no-such-binary-aelix221")], timeout=1.0
            ),
            DEADLINE,
            "spawn error",
        )


def test_success_matches_subprocess_run(strays: list[int]) -> None:
    """The shape the three sites are giving up, reproduced exactly.

    ``capture_output=True`` + ``check=False`` + a returncode; bytes, because
    each site owns its own decoding (utf-8/replace at ``exec`` and ``fd``, raw
    bytes at the clone).
    """

    source = "import sys; print('x'); sys.stderr.write('e'); sys.exit(3)"
    argv = [sys.executable, "-c", source]
    reference = subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=DEADLINE
    )
    result = _run_bounded(
        lambda: run_contained(argv, timeout=DEADLINE),
        DEADLINE + 5,
        "success matches subprocess.run",
    )

    # ``print`` writes through the text layer, i.e. ``os.linesep`` — CRLF on
    # win32 (the first windows leg returned ``b'x\r\n'`` against ``b"x\n"``,
    # run 33959649661). The helper is a byte pipe and must hand back exactly the
    # bytes ``subprocess.run`` hands back, so the reference IS ``subprocess.run``.
    assert result.stdout == reference.stdout == b"x" + os.linesep.encode()
    assert result.stderr == reference.stderr == b"e"
    assert result.returncode == reference.returncode == 3


def test_no_controlling_terminal(strays: list[int]) -> None:
    """The session decision, executed rather than reasoned about.

    ``process_group=0`` — the alternative that keeps the session — was measured
    to STOP the child instead: under a real pty a ``git clone`` over ssh with an
    unknown host key left ``git``, ``ssh``, ``sshd-session`` and ``sshd-auth``
    all in ``ps stat T`` with no prompt ever printed, for the full 60 s (ssh's
    ``read_passphrase`` calls ``tcsetattr``, which raises ``SIGTTOU``
    group-wide). With ``setsid`` the tty read fails AT ONCE with the tool's own
    message, which is what the callers surface. The cost — these commands are
    non-interactive — is stated in the guide and in ADR-0238's amendment.

    THE DISCRIMINATOR IS THE SESSION, NOT THE ERRNO (#221 review MUT-2/T2). No
    CI leg here owns a controlling terminal, so a child that merely INHERITED
    the runner's tty-less state gets the same ``ENXIO``: measured, with
    ``process_group=0`` substituted for ``start_new_session=True`` the errno
    half still passed in a tty-less run and failed only under a real pty. The
    session is what the substitution actually changes — ``process_group=0``
    keeps the parent's sid, ``setsid`` gives the child one of its own — so the
    POSIX arm asserts that, and keeps the errno half as "not stopped, not
    hung".

    On win32 there is no ``/dev/tty`` to open and the flag buys the child no
    console signals; the arm asserts only that a plain child still runs.
    """

    if sys.platform == "win32":
        argv = [sys.executable, "-c", "print('no tty here')"]
    else:
        argv = [sys.executable, "-c", NO_TTY_SOURCE]
    started = time.monotonic()

    result = _run_bounded(
        lambda: run_contained(argv, timeout=DEADLINE), DEADLINE + 5, "no controlling terminal"
    )
    elapsed = time.monotonic() - started

    if sys.platform == "win32":
        assert result.returncode == 0
        assert result.stdout.strip() == b"no tty here"
    else:
        reported = dict(
            field.split("=", 1)
            for field in result.stderr.decode("utf-8", "replace").split()
            if "=" in field
        )
        child_sid = int(reported["sid"])
        child_pid = int(reported["pid"])
        # A session LEADER: ``setsid`` makes sid == pid, which is exactly what
        # the ``process_group=0`` alternative does not do.
        assert child_sid == child_pid, f"child {child_pid} is in session {child_sid}, not its own"
        assert child_sid != os.getsid(0), (
            f"child kept the harness's session {child_sid} — the spawn did not setsid"
        )
        # Not stopped, not hung: the open FAILS, with the errno saying there is
        # no terminal — ENXIO where the driver answers, ENOENT where /dev/tty
        # is not even present.
        assert result.returncode != 0
        assert b"ENXIO" in result.stderr or b"ENOENT" in result.stderr
        assert elapsed < 5.0
