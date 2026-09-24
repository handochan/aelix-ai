"""Real children for the bash tool's ``exec`` (#222). The site-level assertions.

``test_bash_tool.py`` and ``test_abort_signal.py`` drive this surface with short
commands and fakes; this file spawns actual trees through the actual resolved
shell, so each case measures what the host really does — the POSIX arms on
ubuntu and macOS, the win32 arms only on ``windows-latest``. That is integration
coverage supplied by the gating windows leg and NOT a way around the
no-``skipif`` rule: every case below runs everywhere, and where the ANSWER is
genuinely platform-specific (a ``setsid`` holder is outside a process group and
inside a job) the case asserts both arms and documents the asymmetry.

THE ONE THE ISSUE EXISTS FOR.
:func:`test_the_timeout_ends_a_tree_whose_middle_parent_already_exited` is Pi
#9129's shape: the intermediate parent is gone by the time the kill runs, so
``taskkill /T`` — which follows LIVE parent links only — reports success and the
leaf runs on holding the pipe. On win32 that is not a leak but a HANG, because
``exec`` reads stdout to EOF; ADR-0238 assigns exactly that to #222. It is not
demonstrated as a red test on ``main`` — it would hang the leg — and the bound
plus the ``warnings.warn`` of the elapsed are the guard and the measurement
instead.

CONVENTIONS, shared with ``tests/process_tree/test_run_contained_real_processes.py``
and taken from it deliberately (one definition of "a real-process case in this
repo"): children write bytes through ``sys.stdout.buffer`` (the text layer
writes ``os.linesep`` and CRLF reddened the first #221 windows leg); child
scripts are FILES under ``tmp_path`` invoked as ``& "<python>" "<script>"`` on
win32 and through ``shlex.quote`` on POSIX (``test_abort_signal.py``'s
technique — the resolved shell is pwsh on the windows leg, which has no ``sh``
quoting); every root that spawns something able to outlive the call announces
the whole tree through an ``os.replace`` marker that a :class:`_Registrar`
started BEFORE the call polls on its own thread, so a call that never returns
still leaves no 60 s stray; every timing case warns its elapsed so the windows
log carries numbers under ``-q``.

Liveness is ``tests/process_probe`` and never ``os.kill(pid, 0)``: on Windows
signal 0 falls through CPython's ``os_kill_impl`` to ``TerminateProcess``, so
the "probe" would cause the death it claims to observe (#203).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import shlex
import subprocess
import sys
import threading
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import pytest
from aelix_ai.utils._process_tree import (
    DRAIN_CAP_SECONDS,
    EXIT_DRAIN_SECONDS,
    KILL_DRAIN_SECONDS,
    ProcessTree,
    _PipeReader,
)
from aelix_coding_agent.tools import bash as bash_module
from aelix_coding_agent.tools._abort import AbortSignal
from aelix_coding_agent.tools.bash import (
    ExecExitResult,
    _resolve_shell,
    create_local_bash_operations,
)
from aelix_coding_agent.util.shell_env import get_shell_env

from tests.process_probe import STATE_ALIVE, probe_state
from tests.process_tree.test_process_tree_real_processes import DEADLINE, _await_dead, _reap
from tests.process_tree.test_run_contained_real_processes import (
    OUTLIVE,
    _registrar,
    _with_planted_stdin,
)
from tests.process_tree.test_run_contained_real_processes import (
    strays as _strays_fixture,
)

_T = TypeVar("_T")

#: The sibling file's cleanup fixture, RE-EXPORTED rather than re-written: one
#: definition of "pids this case is responsible for" and one root-only reaper (a
#: cleanup that reached for a process GROUP would be reaching for the very
#: mechanism under test). Bound through an assignment because a parameter named
#: ``strays`` would otherwise read to ruff as a redefinition of the import.
strays = _strays_fixture

#: In the argv of every process these cases spawn, so a leak is greppable
#: (``pgrep -fl aelix222``) rather than anonymous. The sibling's is ``aelix221``;
#: a shared token would make the two files' strays indistinguishable.
MARK = "aelix222"

#: Slack over a stated bound, for shell startup and the runner's load. The
#: formula is #221's and the exclusion is the load-bearing half: ``bound =
#: timeout + KILL_DRAIN_SECONDS + 1.5``, NEVER ``+ REAP_GRACE_SECONDS`` —
#: folding the reap grace in is what let a 6.011 s regression pass its own
#: assertion (``test_run_contained_real_processes.py:461-465``).
SLACK = 1.5

#: Added to every bound on win32: ``hard_kill`` shells out to ``taskkill.exe
#: /T /F`` first and unconditionally under its own ``timeout=5``, and pwsh's own
#: startup is 0.5-0.57 s on the runner against bash's ~0.01 s here.
WIN32_SLACK = 5.0


def _bound(base: float) -> float:
    return base + KILL_DRAIN_SECONDS + SLACK + (WIN32_SLACK if sys.platform == "win32" else 0.0)


# === the children ===========================================================

#: A root that hands its pipe down two levels and then holds still.
#:
#: ``argv``: marker, the middle script, how long the leaf sleeps, how long the
#: root holds. The middle is spawned with ``stdin=DEVNULL`` and NOTHING ELSE on
#: purpose: passing any one stdio is what makes CPython skip its all-``None``
#: early return on win32 and mark the inherited stdout/stderr handles
#: inheritable, which is what puts the leaf on our pipe there (#222 critique
#: WIN32-4/WIN32-5). ``os.setsid`` is not used anywhere here — the whole point
#: is a leaf INSIDE the group whose PARENT LINK is dead.
ROOT_OF_A_TREE = """\
import os
import subprocess
import sys
import time

marker, middle, nap, hold = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
subprocess.Popen(
    [sys.executable, middle, marker, str(os.getpid()), nap, "@MARK@"],
    stdin=subprocess.DEVNULL,
)
time.sleep(float(hold))
""".replace("@MARK@", MARK)

#: Spawns the leaf, announces ``root middle leaf``, and EXITS — which is the
#: shape: by the time the kill runs the leaf's parent link is dead.
MIDDLE_THAT_EXITS = """\
import os
import subprocess
import sys

marker, root_pid, nap, mark = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
child = subprocess.Popen(
    [sys.executable, "-c", "import sys, time; time.sleep(float(sys.argv[1]))", nap, mark],
    stdin=subprocess.DEVNULL,
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{root_pid} {os.getpid()} {child.pid}\\n")
os.replace(tmp, marker)
"""

#: A holder OUTSIDE the tree on POSIX and inside the job on win32.
#:
#: ``containment_spawn_kwargs(new_session=True)`` is imported in the child
#: rather than spelled as ``os.setsid`` so the two platforms are one line and
#: the win32 arm is a real spawn flag rather than a skip.
ROOT_WITH_AN_ESCAPED_HOLDER = """\
import os
import subprocess
import sys
import time

from aelix_ai.utils._process_tree import containment_spawn_kwargs

marker, holder, nap, tick, hold = sys.argv[1:6]
child = subprocess.Popen(
    [sys.executable, holder, nap, tick, "@MARK@"],
    stdin=subprocess.DEVNULL,
    **containment_spawn_kwargs(new_session=True),
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {child.pid}\\n")
os.replace(tmp, marker)
time.sleep(float(hold))
""".replace("@MARK@", MARK)

#: Holds the inherited pipe for ``nap`` seconds, writing every ``tick`` when
#: ``tick > 0``. Silent at ``tick == 0``, which is what makes the post-kill
#: drain end on its idle rule rather than on its cap.
HOLDER = """\
import sys
import time

nap, tick = float(sys.argv[1]), float(sys.argv[2])
deadline = time.monotonic() + nap
while time.monotonic() < deadline:
    if tick > 0:
        sys.stdout.buffer.write(b"tick\\n")
        sys.stdout.buffer.flush()
        time.sleep(tick)
    else:
        time.sleep(0.05)
"""

#: Exits 0 after backgrounding a helper with ALL THREE stdio at ``DEVNULL``.
#:
#: The third one is the case, not tidiness: ``exec`` spawns
#: ``stderr=subprocess.STDOUT``, so a helper with only stdout redirected still
#: holds fd 2 — the same pipe — and the critique measured exactly that shape
#: holding a real ``exec`` for 20.03 s instead of 0.02 s (WIN32-4).
ROOT_THAT_BACKGROUNDS_A_HELPER = """\
import os
import subprocess
import sys

marker, nap = sys.argv[1], sys.argv[2]
child = subprocess.Popen(
    [sys.executable, "-c", "import sys, time; time.sleep(float(sys.argv[1]))", nap, "@MARK@"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {child.pid}\\n")
os.replace(tmp, marker)
""".replace("@MARK@", MARK)

#: :data:`ROOT_THAT_BACKGROUNDS_A_HELPER` plus a second child that HOLDS the
#: pipe — which is the only reason the exit-path drain stays open long enough
#: for an abort to land inside it.
#:
#: The literal reuse the review asked for does not measure anything: with all
#: three stdio at ``DEVNULL`` the pipe EOFs at the ROOT's own exit, so the
#: exit-path drain is ~1 ms wide (measured 2026-09-06, darwin: ``exec`` returned
#: 0.0009 s after ``proc.wait``) and an abort 0.3 s later would arrive after
#: ``exec`` had already returned. So there are two children, with two jobs:
#: ``helper`` is the assertion (all three ``DEVNULL``, ``OUTLIVE``, and INSIDE
#: the tree — no ``setsid``, so both a ``killpg`` and a job kill reach it, which
#: is what makes the survival claim non-vacuous on both platforms), and ``tail``
#: only holds the inherited pipe for ``hold`` seconds and then closes it by
#: exiting. ``stdin=DEVNULL`` and nothing else on the tail, for
#: :data:`ROOT_OF_A_TREE`'s win32 handle-inheritance reason.
#:
#: ``argv``: marker, the helper's life, the tail's ``hold``, the tail's
#: ``tick``. The fourth is #232's, and it branches exactly as :data:`HOLDER`
#: does: at ``tick <= 0`` the tail is today's single write after
#: ``time.sleep(hold)``, and above it the tail writes every ``tick`` for
#: ``hold`` seconds. A chatty tail is the only way to hold the exit-path drain
#: open now that it ends on the idle rule — with the silent one ``exec`` is back
#: at ~0.1 s and an abort 0.3 s later measures nothing (measured 0.364 s,
#: ``task.done()`` already True). There is NO ``len(sys.argv)`` default:
#: :func:`_command` appends :data:`MARK` to every argv, so at a three-argument
#: call site ``sys.argv[4]`` is ``"aelix222"`` and ``float()`` raises. Both call
#: sites pass one.
ROOT_THAT_BACKGROUNDS_A_HELPER_BEHIND_A_TAIL = """\
import os
import subprocess
import sys

marker, nap, hold, tick = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
helper = subprocess.Popen(
    [sys.executable, "-c", "import sys, time; time.sleep(float(sys.argv[1]))", nap, "@MARK@"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
tail = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import sys, time\\n"
        "hold, tick = float(sys.argv[1]), float(sys.argv[2])\\n"
        "if tick <= 0:\\n"
        "    time.sleep(hold)\\n"
        "    sys.stdout.buffer.write(b'TAIL\\\\n')\\n"
        "    sys.stdout.buffer.flush()\\n"
        "else:\\n"
        "    deadline = time.monotonic() + hold\\n"
        "    while time.monotonic() < deadline:\\n"
        "        sys.stdout.buffer.write(b'TAIL\\\\n')\\n"
        "        sys.stdout.buffer.flush()\\n"
        "        time.sleep(tick)\\n",
        hold,
        tick,
        "@MARK@",
    ],
    stdin=subprocess.DEVNULL,
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {helper.pid} {tail.pid}\\n")
os.replace(tmp, marker)
sys.stdout.buffer.write(b"ROOT\\n")
sys.stdout.buffer.flush()
""".replace("@MARK@", MARK)

#: Writes ``EARLY`` and exits 0 while a helper on the inherited pipe writes
#: ``LATE`` ``nap`` seconds later and then HOLDS the pipe for ``hold`` more.
#:
#: ``argv``: marker, how long the helper waits before ``LATE``, how long it
#: holds afterwards. The third one is #232's: the success path now returns on
#: the idle rule, so the helper is still alive when ``exec`` comes back and
#: ``probe_state`` on it is what makes "the helper keeps running" an assertion
#: rather than a claim.
ROOT_WITH_A_LATE_TAIL = """\
import os
import subprocess
import sys

marker, nap, hold = sys.argv[1], sys.argv[2], sys.argv[3]
child = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import sys, time\\n"
        "time.sleep(float(sys.argv[1]))\\n"
        "sys.stdout.buffer.write(b'LATE\\\\n')\\n"
        "sys.stdout.buffer.flush()\\n"
        "time.sleep(float(sys.argv[2]))\\n",
        nap,
        hold,
        "@MARK@",
    ],
    stdin=subprocess.DEVNULL,
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {child.pid}\\n")
os.replace(tmp, marker)
sys.stdout.buffer.write(b"EARLY\\n")
sys.stdout.buffer.flush()
""".replace("@MARK@", MARK)

#: 2 MiB in 2048 separate flushed writes — many small deliveries rather than one
#: big one, because what the delivery case measures is the handoff per chunk.
CHATTY = """\
import sys

line = b"x" * 1023 + b"\\n"
for _ in range(2048):
    sys.stdout.buffer.write(line)
    sys.stdout.buffer.flush()
"""


def _script(tmp_path: Path, name: str, source: str) -> str:
    """Write a child script and return its path.

    A FILE, never ``python -c "…"``: C.2's roots have to be nested inside a
    ``pwsh -Command`` string on the windows leg, and a ``-c`` body with quotes
    and newlines in it does not survive that nesting (#222 critique TESTS-14).
    """

    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return str(path)


def _command(*args: str) -> str:
    """A command STRING running ``sys.executable`` with ``args``, per shell.

    pwsh needs the call operator to run a quoted path (``& "C:/…/python.exe"``)
    and bash needs ``shlex.quote``; both accept double-quoted arguments
    (``test_abort_signal.py:128-137``). :data:`MARK` is appended to every argv
    so that anything these cases leak is greppable.
    """

    tail = " ".join(f'"{arg}"' for arg in (*args, MARK))
    if sys.platform == "win32":
        return f'& "{sys.executable}" {tail}'
    return f"{shlex.quote(sys.executable)} {tail}"


# === the harness ============================================================


async def _bounded(task: asyncio.Task[_T], bound: float, what: str) -> _T:
    """Await ``task`` for ``bound`` seconds, failing BY NAME if it does not land.

    ``_run_bounded``'s job in the sibling file, for a coroutine: there is no
    ``pytest-timeout`` here and no ``timeout-minutes`` on the jobs, so a case
    whose termination depends on a bound INSIDE ``exec`` would burn GitHub's
    360-minute default if that bound were deleted. The shield is what makes the
    expiry a FAILURE rather than a cancellation of the thing under test; the
    cancel afterwards is cleanup, itself bounded, because on ``main`` the cancel
    leg drains to EOF and a held pipe hangs it too.
    """

    try:
        return await asyncio.wait_for(asyncio.shield(task), bound)
    except TimeoutError:
        task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(task), 5.0)
        pytest.fail(f"{what}: exec did not return within {bound}s")


async def _await_pids(registrar: Any, what: str) -> tuple[int, ...]:
    """Wait for the root's announcement from inside the event loop."""

    deadline = time.monotonic() + 10.0
    while registrar.pids is None and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    pids = registrar.pids
    assert pids is not None, (
        f"{what}: the root never announced its tree — the case measured nothing"
    )
    return pids


def _exec_task(
    command: str,
    cwd: Path,
    chunks: list[bytes],
    *,
    signal: Any | None = None,
    timeout: float | None = None,
) -> asyncio.Task[ExecExitResult]:
    ops = create_local_bash_operations()
    return asyncio.ensure_future(
        ops.exec(command, str(cwd), on_data=chunks.append, signal=signal, timeout=timeout)
    )


def _tree_command(tmp_path: Path, marker: Path, *, hold: float = OUTLIVE) -> str:
    root = _script(tmp_path, "root_of_a_tree.py", ROOT_OF_A_TREE)
    middle = _script(tmp_path, "middle.py", MIDDLE_THAT_EXITS)
    return _command(root, str(marker), middle, str(OUTLIVE), str(hold))


def _holder_command(
    tmp_path: Path, marker: Path, *, tick: float = 0.0, hold: float = OUTLIVE
) -> str:
    root = _script(tmp_path, "root_with_a_holder.py", ROOT_WITH_AN_ESCAPED_HOLDER)
    holder = _script(tmp_path, "holder.py", HOLDER)
    return _command(root, str(marker), holder, str(OUTLIVE), str(tick), str(hold))


# === the drain's own record (#325) ==========================================

#: How many times a case whose PREMISE is "the idle rule never fires" runs
#: before a failed premise is itself the failure. Three cases here hold the
#: exit-path drain open with a writer that ticks every 0.05 s against the 0.1 s
#: grace — 8a, 8b and 10 — and each of them can meet a drain that ended on its
#: idle rule legitimately (:class:`_DrainRecord`). One such end is a run that
#: measured nothing; every attempt ending that way is a regression of the idle
#: rule's input (a reader that never stamps ``last_chunk_at`` ends every drain
#: there) or a host that stalls every run, and the message says which numbers
#: to read. Not a skip: every attempt runs the whole case, and the pins are
#: asserted on the one that measured.
ATTEMPTS = 3


@dataclass(frozen=True)
class _DrainEnd:
    """How one exit-path drain ended, in its own terms, and the numbers behind it."""

    #: ``cap`` (its last look was at or past its soft cap — the deadline, or
    #: :data:`DRAIN_CAP_SECONDS` past the exit — every look it took there
    #: asked for a proof, none but the last was proven, and the last either
    #: WAS proven or was refused at the hard cap, where the drain ends proof
    #: or not and reports ``output_unconfirmed``; on the ``timeout=None`` arms
    #: the soft cap IS the hard one, so there that is the first look past it.
    #: However late a stall made that look, the shape is the same),
    #: ``past-cap`` (it looked past its soft cap without asking, or went on
    #: after a proof there: a drain that ignores its soft cap and runs to the
    #: hard one), ``idle`` (a proven end on the idle rule: the reader had
    #: handed nothing on for a grace), ``eof`` (the loop left without a proof —
    #: the pipe's EOF or the reader's death — including a drain whose last look
    #: was past its soft cap and refused short of the hard cap, which then met
    #: EOF before it looked again: the drain breaks on a refusal only at the
    #: hard cap, which a late look can move later but never earlier than
    #: :data:`DRAIN_CAP_SECONDS` past the exit, so a refusal before that
    #: instant did not end it),
    #: ``early`` (a proven end before the cap WITHOUT a grace of silence, which
    #: no rule of the drain produces), ``no-exit-drain`` (the ordinary exit's cap
    #: was never computed: the drain took a kill branch, or never ran).
    kind: str
    summary: str


class _DrainRecord:
    """The exit-path drain's own sequence, recorded where the drain decides (#325).

    ``tests/process_tree/test_the_drain_asks_the_pipe.py``'s :class:`DrainLog`,
    narrowed to what these cases judge and widened by three readings. Installed
    as the site's ``time`` and ``_exit_drain_cap`` and as its ``_PipeReader``
    (:meth:`install`), it starts when the site computes the soft cap — on the
    loop thread, right before the drain runs there — and records: the cap and
    the three arguments it was computed from (the root's exit instant, the
    start, the caller's ``timeout``); every clock reading the drain takes on
    that thread as a LOOK, with the reader's ``last_chunk_at`` read just before
    the drain reads it; and every answer ``proven()`` gives the drain, as an
    ASK. Readings on other threads (the ``proc.wait`` worker's exit stamp) are
    not the drain's and are not recorded.

    WHY (#325). ``test_an_abort_during_the_exit_path_drain_keeps_the_helper``
    failed once on windows-latest as ``assert 2.0 <= 1.375``: the drain ended
    before its cap, and the floor said nothing about why. The drain has more
    than one end and the case bet on one — that the tail's 0.05 s ticks keep
    the 0.1 s idle rule from ever firing. That bet is on the READER, not on the tail:
    ``last_chunk_at`` is stamped when the reader hands a chunk on, so a reader
    that hands nothing on for a grace — the tail paused, the reader starved,
    the whole process stopped by the runner — lets the idle rule end the drain,
    WITH a proof, on every platform: the reader's first look after the exit
    pinned what the command had written, it has handed that on long since
    (:meth:`_PipeReader.caught_up`), and the tail's later lines are a helper's,
    which that end is allowed to cut. Measured (#325 probe, darwin, 8b's
    command with the reader held from the abort until ``exec`` returned — on
    the native branch at its next even mark, on the forced-win32 branch right
    after its next look that saw an empty pipe): the drain ended 0.38-0.48 s
    after the exit with ``exit_code 0``, 3 of 3 on the native branch
    (``drained() False``, ``caught_up() True``) and 3 of 3 on the forced-win32
    one (both True); and the unchanged case failed exactly as
    CI did under a 0.3 s whole-process stall 50 ms after the exit, ``assert
    2.0 <= 0.393``. So the verdict is this record: a cap end is what each case
    pins, an idle end is a premise that failed and is said as such, and
    anything else fails with the drain's own numbers.
    """

    def __init__(self) -> None:
        #: ``("look", at, last_chunk_at)`` and ``("ask", at, answer, pinned, handed_on, eof)``.
        self.events: list[tuple[Any, ...]] = []
        self.cap: float | None = None
        self.exited_at: float | None = None
        self.started_at: float | None = None
        #: The ``timeout`` the site handed ``_exit_drain_cap``; :data:`_UNSET` until it did.
        self.timeout: float | None | object = _UNSET
        self._state: Any | None = None
        self._thread: threading.Thread | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        record = self

        def cap(exited_at: float, *, started_at: float, timeout: float | None) -> float:
            # The site's own function, captured at import: a retry installs a
            # fresh record over the last one, and reading ``bash_module`` here
            # would wrap the previous attempt's wrapper, which would then go on
            # recording into a record already classified.
            answer = _REAL_EXIT_DRAIN_CAP(exited_at, started_at=started_at, timeout=timeout)
            record.cap, record.exited_at = answer, exited_at
            record.started_at, record.timeout = started_at, timeout
            record._thread = threading.current_thread()
            return answer

        class Recorded(_PipeReader):
            def __init__(self, stream: Any, state: Any, **kwargs: Any) -> None:
                super().__init__(stream, state, **kwargs)
                record._state = state

            def proven(self) -> bool:
                answer = super().proven()
                record.events.append(
                    ("ask", time.monotonic(), answer, self.pinned, self.handed_on, self.eof)
                )
                return answer

        monkeypatch.setattr(bash_module, "time", self)
        monkeypatch.setattr(bash_module, "_exit_drain_cap", cap)
        monkeypatch.setattr(bash_module, "_PipeReader", Recorded)

    def monotonic(self) -> float:
        state = self._state
        # BEFORE the reading, so the drain reads a ``last_chunk_at`` at least
        # this fresh: a silence recorded here is never shorter than the one the
        # drain judged, and an idle end the drain took reads as one here.
        last_chunk_at = None if state is None else state.last_chunk_at
        now = time.monotonic()
        if threading.current_thread() is self._thread:
            self.events.append(("look", now, last_chunk_at))
        return now

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)

    def last_ask_proven(self) -> bool:
        """Whether the drain's last look asked and was proven — which it always breaks on.

        Every ``True`` from ``proven()`` ends the drain's loop at once (the idle
        rule's and the soft cap's ``break``, and the hard cap's), with no await
        between the answer and the ``break``, so a cancel that finds a drain
        whose last answer was ``True`` found it ENDED — in the one
        ``await asyncio.sleep(0)`` after the loop, before the detach and the
        close. A last answer ``False`` at the hard cap also ends the loop, but
        this record cannot tell it from a late look's re-armed hard cap
        without the drain's ``due``; case 10's cancel lands
        :data:`INTO_THE_DRAIN` into a 2.0 s cap, far from either.
        """

        looks = [index for index, event in enumerate(self.events) if event[0] == "look"]
        if not looks:
            return False
        asks = [event for event in self.events[looks[-1] + 1 :] if event[0] == "ask"]
        return bool(asks) and asks[-1][2] is True

    def end(self) -> _DrainEnd:
        """Classify the drain's last look and its last ask, in the drain's own arithmetic."""

        cap, exited_at = self.cap, self.exited_at
        if cap is None or exited_at is None:
            return _DrainEnd(
                "no-exit-drain",
                "never ran: the ordinary exit's drain cap was never computed, so the call took "
                "a kill branch's drain (``state.exited_at`` stamped for an ordinary exit sends "
                "it to the 1.0 s KILL_DRAIN_SECONDS cap) or reached no drain at all",
            )
        looks = [index for index, event in enumerate(self.events) if event[0] == "look"]
        if not looks:
            return _DrainEnd("eof", "the drain took no look — it found EOF at once")
        _look, at, last_chunk_at = self.events[looks[-1]]
        asks = [event for event in self.events[looks[-1] + 1 :] if event[0] == "ask"]
        armed = max(last_chunk_at if last_chunk_at is not None else exited_at, exited_at)
        silent = at - armed
        answer, pinned, handed_on, eof = asks[-1][2:] if asks else (None, None, None, None)
        summary = (
            f"its last look {at - exited_at:.3f}s after the root's exit (soft cap "
            f"{cap - exited_at:.3f}s past it, timeout={self.timeout}), the reader silent "
            f"{silent:.3f}s there against a {EXIT_DRAIN_SECONDS}s grace; {len(looks)} looks, "
            f"{sum(1 for event in self.events if event[0] == 'ask')} asks, the last answered "
            f"{answer} (pinned={pinned} handed_on={handed_on} eof={eof})"
        )
        if asks and at >= cap:
            # Every look at or past the soft cap asks (``end_at`` is never
            # later than ``cap``), and only the last may have been answered
            # yes; a stall moves that look, never this shape.
            past = [index for index in looks if self.events[index][1] >= cap]
            for position, index in enumerate(past):
                following = past[position + 1] if position + 1 < len(past) else len(self.events)
                answers = [
                    event[2] for event in self.events[index + 1 : following] if event[0] == "ask"
                ]
                last = position + 1 == len(past)
                if not answers or (not last and any(answers)):
                    return _DrainEnd(
                        "past-cap",
                        f"went on past its soft cap: look {position + 1} of {len(past)} there "
                        f"({self.events[index][1] - exited_at:.3f}s after the exit) "
                        f"{'asked nothing' if not answers else 'was proven and did not end it'}"
                        f"; {summary}",
                    )
            if not answer and at < exited_at + DRAIN_CAP_SECONDS:
                # Refused short of the hard cap: the drain does not break on a
                # refusal before that instant (``_DrainEnd``'s ``eof``), so it
                # left its loop on EOF or the reader's death before it looked
                # again — not at its cap.
                return _DrainEnd(
                    "eof",
                    f"was refused past its soft cap, short of the hard cap, and then left its "
                    f"loop without a proof (EOF, or the reader died): {summary}",
                )
            return _DrainEnd("cap", f"ended at its cap: {summary}")
        if asks and answer and at >= armed + EXIT_DRAIN_SECONDS:
            return _DrainEnd("idle", f"ended on its idle rule, proven: {summary}")
        if asks and answer:
            return _DrainEnd("early", f"ended on a proof before its cap and its grace: {summary}")
        return _DrainEnd(
            "eof", f"left its loop without a proof (EOF, or the reader died): {summary}"
        )


#: :attr:`_DrainRecord.timeout` before the site computed a cap at all.
_UNSET = object()

#: The site's ``_exit_drain_cap`` as imported, before any record wraps it.
_REAL_EXIT_DRAIN_CAP = bash_module._exit_drain_cap


#: How far into the exit-path drain 8b's abort and case 10's cancel land, once
#: the drain has STARTED (:func:`_into_the_exit_drain`). Not a bet on anything:
#: any instant after the start is inside the drain until it ends, and a short
#: settle only means the drain has taken a few looks by then — the shorter it
#: is, the less room an idle end has to land first.
INTO_THE_DRAIN = 0.1


async def _into_the_exit_drain(task: asyncio.Task[Any], record: _DrainRecord, what: str) -> bool:
    """Wait for the call to reach its exit-path drain, then settle into it.

    ON AN EVENT, NOT A TIMER (#325 review). Both 8b's abort and case 10's
    cancel have to land after the ROOT'S EXIT, and the announcement they used
    to count a blind settle from comes BEFORE it — on win32 the exit is pwsh's
    teardown. The diag run 36033034335 put that exit up to 0.256 s after the
    announcement on a loaded windows runner, against the 0.3 s settle 8b had:
    an abort ahead of the exit lands in ``_wait``, ends the tree, and the case
    died on a bare ``assert None == 0``. The event is the site computing the
    exit path's cap (:attr:`_DrainRecord.cap`): it happens on the loop thread,
    AFTER the watcher is disarmed and right before the drain runs, so once this
    coroutine sees it the call is inside that drain.

    Returns whether it got there: ``False`` when ``exec`` came back without
    computing the cap (a kill branch's drain, :class:`_DrainEnd`'s
    ``no-exit-drain``) or ended its drain inside the settle — the caller says
    which from the record. Fails, by name, if neither happens within an
    anti-hang bound.
    """

    bound = _bound(0.0) + 5.0
    waited_from = time.monotonic()
    while record.cap is None and not task.done():
        if time.monotonic() - waited_from > bound:
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(task), 5.0)
            pytest.fail(
                f"{what}: the root announced but the call reached no exit-path drain within "
                f"{bound}s (waited {time.monotonic() - waited_from:.3f}s) — the root never "
                f"exited, so the case measured nothing"
            )
        await asyncio.sleep(0.01)
    if record.cap is None:
        return False
    await asyncio.sleep(INTO_THE_DRAIN)
    return not task.done()


def _label(task: asyncio.Task[Any], end: _DrainEnd) -> str:
    """The ``end=`` a case's measurement twin prints: the record's, for a call that returned.

    A call :func:`_bounded` gave up on and cancelled has no end of its own —
    the record's last look would read as ``eof`` — so it is labelled as what
    it was.
    """

    if not task.done():
        return "unreturned"
    if task.cancelled():
        return "cancelled-by-the-bound"
    return end.kind


def _premise_failed(what: str, end: _DrainEnd, measured: str = "the cap") -> str:
    """The line a run whose drain ended on the idle rule leaves in the log."""

    return (
        f"{what}: THE PREMISE FAILED, NOT THE CASE — the drain {end.summary}. The idle rule "
        f"fires when the READER hands nothing on for a grace: the writer paused, or the reader "
        f"or the whole process was held (a stalled runner). That end is proven and allowed "
        f"(the tail is a helper's output); this run measured nothing about {measured}, so "
        f"the case runs again"
    )


def _every_attempt_failed_its_premise(
    what: str, lines: list[str], measured: str = "the cap"
) -> str:
    return (
        f"{what}: every one of {ATTEMPTS} attempts ended on the idle rule, so the case never "
        f"measured {measured}. Once is a stalled runner; every time is the idle rule's input "
        f"broken (a reader that no longer stamps ``last_chunk_at`` on a hand-over ends every "
        f"drain there) or a host stalling every run — read the silences: " + " | ".join(lines)
    )


# === 1-3: the tree whose middle parent is gone, on all three legs ============


async def test_the_timeout_ends_a_tree_whose_middle_parent_already_exited(
    tmp_path: Path, strays: list[int]
) -> None:
    """Pi #9129, at this site: the leaf is killed and ``exec`` comes back.

    The leaf's parent exited before the kill, so ``taskkill /T`` cannot reach it
    — the job can, which is the whole win32 half of #222. On POSIX ``main`` is
    green here because ``killpg`` reaches the leaf anyway; the discriminator on
    BOTH legs is the C.5(a) mutant (drop the ``hard_kill`` on the timeout leg),
    against which the critique measured the leaf still ``S`` while the tree
    ladder left it GONE.

    ``timeout=1.0`` is the sibling's number for the same chain and is green on
    both windows legs there: the runner's pwsh reaches the third pid in ~0.3 s.
    """

    marker = tmp_path / "pids.txt"
    timeout = 1.0
    registrar = _registrar(marker, strays, fields=3)
    chunks: list[bytes] = []
    task = _exec_task(_tree_command(tmp_path, marker), tmp_path, chunks, timeout=timeout)
    started = time.monotonic()

    try:
        result = await _bounded(task, _bound(timeout) + 5.0, "timeout with a dead middle parent")
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    root_pid, _middle, leaf = pids
    assert result.timed_out is True
    assert result.exit_code is None
    leaf_state = _await_dead(leaf)
    assert leaf_state != STATE_ALIVE, (
        f"leaf {leaf} still {leaf_state} after {DEADLINE}s of polling; exec returned at "
        f"{elapsed:.3f}s"
    )
    assert _await_dead(root_pid) != STATE_ALIVE
    assert elapsed <= _bound(timeout)
    warnings.warn(f"bash exec timeout leg: {elapsed:.3f}s on {sys.platform}", stacklevel=1)


async def test_the_abort_signal_ends_a_tree_whose_middle_parent_already_exited(
    tmp_path: Path, strays: list[int]
) -> None:
    """The same tree, ended by the abort watcher instead of by the timeout.

    The abort is fired once the root has ANNOUNCED, not after a blind 0.3 s:
    pwsh starts in 0.5-0.57 s on the runner, so a blind wait would fire before
    the tree existed and the case would measure a kill with nothing under it
    (#222 critique WIN32-6). The leaf is asserted ALIVE first for the same
    reason.
    """

    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays, fields=3)
    signal = AbortSignal()
    chunks: list[bytes] = []
    task = _exec_task(_tree_command(tmp_path, marker), tmp_path, chunks, signal=signal)
    # Seeded so the ``finally``'s arithmetic is defined even when the wait for
    # the announcement is what failed.
    aborted_at = time.monotonic()

    try:
        _root_pid, _middle, leaf = await _await_pids(registrar, "abort with a dead middle parent")
        assert probe_state(leaf) == STATE_ALIVE
        aborted_at = time.monotonic()
        signal.abort()
        result = await _bounded(task, _bound(0.0) + 5.0, "abort with a dead middle parent")
    finally:
        elapsed = time.monotonic() - aborted_at
        pids = registrar.settle()

    assert pids is not None
    root_pid, _middle, leaf = pids
    assert result.exit_code is None
    assert result.timed_out is False
    assert _await_dead(leaf) != STATE_ALIVE
    assert _await_dead(root_pid) != STATE_ALIVE
    assert elapsed <= _bound(0.0)
    warnings.warn(f"bash exec abort leg: {elapsed:.3f}s on {sys.platform}", stacklevel=1)


async def test_a_cancel_ends_a_tree_whose_middle_parent_already_exited(
    tmp_path: Path, strays: list[int]
) -> None:
    """The same tree, ended by the Esc path, with ``CancelledError`` preserved."""

    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays, fields=3)
    chunks: list[bytes] = []
    task = _exec_task(_tree_command(tmp_path, marker), tmp_path, chunks)
    # Seeded, as in the case above: the ``finally`` runs whatever failed.
    cancelled_at = time.monotonic()

    try:
        _root_pid, _middle, leaf = await _await_pids(registrar, "cancel with a dead middle parent")
        assert probe_state(leaf) == STATE_ALIVE
        cancelled_at = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await _bounded(task, _bound(0.0) + 5.0, "cancel with a dead middle parent")
    finally:
        elapsed = time.monotonic() - cancelled_at
        pids = registrar.settle()

    assert pids is not None
    root_pid, _middle, leaf = pids
    assert _await_dead(leaf) != STATE_ALIVE
    assert _await_dead(root_pid) != STATE_ALIVE
    assert elapsed <= _bound(0.0)
    warnings.warn(f"bash exec cancel leg: {elapsed:.3f}s on {sys.platform}", stacklevel=1)


# === 4: a holder outside the tree, on all three legs =========================


@pytest.mark.parametrize("leg", ["timeout", "abort", "cancel"])
async def test_a_holder_outside_the_tree_does_not_hang_the_call(
    tmp_path: Path, strays: list[int], leg: str
) -> None:
    """§1-3, the reason #222 is not inert on the owner's own platform.

    A ``setsid`` descendant keeps its copy of the pipe after the group kill, so
    a drain that reads to EOF waits for IT rather than for the command. Measured
    on ``main`` with an 8 s holder: ``timed_out=True`` but ``exec`` returned at
    8.02 s, and the abort and ``CancelledError`` legs at 8.02 / 8.03 s — all
    three, which is why all three are parametrised here and why the same-group
    tree of the three cases above cannot stand in for them (it returns at 0.6 s
    with no bound at all). This case's own holder outlives the run, so against
    ``main`` all three legs fail their bound rather than merely overshoot it
    (measured 2026-09-05: "exec did not return within 8.0/7.5/7.5 s").

    The asymmetry, asserted on both arms rather than skipped: on POSIX the
    holder SURVIVES (the reaper's walk is what reaches a session leader,
    ADR-0238) and the bound is what makes it free; on win32 the job holds it and
    it dies. The holder is silent, so the drain ends on the 0.1 s idle rule —
    the cap is what
    ``test_run_contained.py::test_a_chatty_holder_past_the_kill_is_cut_at_kill_drain``
    binds.
    """

    marker = tmp_path / "pids.txt"
    # 2.0 and not 0.5 on the timeout leg: the clock starts BEFORE the shell runs
    # a line (``exec`` hands ``timeout`` to ``proc.wait`` on the
    # ``[shell, flag, command]`` spawn), and the runner's pwsh needs 0.5-0.57 s
    # to start before the python chain under it begins at all. Reproduced by
    # emulating 0.5 s of shell startup (a wrapper that sleeps and then ``exec``s
    # the real shell): at ``timeout=0.5`` this leg failed with "the root never
    # announced its tree — the case measured nothing", i.e. the kill landed
    # before there was a tree to kill. The other two legs fire on the
    # announcement and so have no such budget.
    timeout = 2.0 if leg == "timeout" else None
    registrar = _registrar(marker, strays)
    signal = AbortSignal() if leg == "abort" else None
    chunks: list[bytes] = []
    command = _holder_command(tmp_path, marker)
    task = _exec_task(command, tmp_path, chunks, signal=signal, timeout=timeout)
    started = time.monotonic()

    try:
        if leg == "timeout":
            result = await _bounded(task, _bound(2.0) + 5.0, f"a {leg} with an escaped holder")
            assert result.timed_out is True
            assert result.exit_code is None
        else:
            _root_pid, holder = await _await_pids(registrar, f"a {leg} with an escaped holder")
            assert probe_state(holder) == STATE_ALIVE
            started = time.monotonic()
            if signal is not None:
                signal.abort()
                result = await _bounded(task, _bound(0.0) + 5.0, f"a {leg} with an escaped holder")
                assert result.exit_code is None
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await _bounded(task, _bound(0.0) + 5.0, f"a {leg} with an escaped holder")
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    root_pid, holder = pids
    assert _await_dead(root_pid) != STATE_ALIVE
    assert elapsed <= _bound(2.0 if leg == "timeout" else 0.0)
    if sys.platform == "win32":
        assert _await_dead(holder) != STATE_ALIVE
    else:
        # Give the kill the same window the win32 arm gets before calling the
        # survival real rather than merely not-yet-observed.
        time.sleep(0.5)
        assert probe_state(holder) == STATE_ALIVE
    warnings.warn(f"bash exec {leg} with a holder: {elapsed:.3f}s on {sys.platform}", stacklevel=1)


# === 5: what a successful command is allowed to leave behind =================


async def test_a_successful_command_keeps_the_helper_it_backgrounded(
    tmp_path: Path, strays: list[int]
) -> None:
    """``kill_on_close=False``: ``close()`` is a release, never a kill.

    Pi #8225's rule and ADR-0238's per-site decision — a command that exits 0
    after starting a daemon meant to outlive it keeps it. The helper takes all
    three stdio at ``DEVNULL`` (see :data:`ROOT_THAT_BACKGROUNDS_A_HELPER`), so
    what is measured here is the CLOSE and not the drain.
    """

    marker = tmp_path / "pids.txt"
    root = _script(tmp_path, "root_backgrounds.py", ROOT_THAT_BACKGROUNDS_A_HELPER)
    registrar = _registrar(marker, strays)
    chunks: list[bytes] = []
    task = _exec_task(_command(root, str(marker), str(OUTLIVE)), tmp_path, chunks)
    started = time.monotonic()

    try:
        result = await _bounded(task, _bound(0.0) + 5.0, "a backgrounded helper")
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    _root_pid, helper = pids
    assert result.exit_code == 0
    assert result.timed_out is False
    assert elapsed <= _bound(0.0)
    assert probe_state(helper) == STATE_ALIVE
    warnings.warn(f"bash exec backgrounded helper: {elapsed:.3f}s on {sys.platform}", stacklevel=1)


# === 6: stdin ===============================================================


def test_the_child_reads_devnull_and_not_aelixs_stdin(tmp_path: Path, strays: list[int]) -> None:
    """§A.5: ``stdin=DEVNULL``, Pi's ``stdio: ["ignore", "pipe", "pipe"]``.

    ``''`` is what a child reading ``/dev/null`` (``NUL`` on win32) sees. THE
    ASSERTION IS ONLY WORTH ANYTHING INSIDE ``_with_planted_stdin`` (#221 review
    SITE-2): under pytest's default ``--capture=fd`` this process's fd 0 already
    IS ``/dev/null``, so a child that merely INHERITED stdin reads ``''`` too and
    the naive form passes unchanged against ``main``. With readable bytes
    planted there the mutant reads ``'SHOULD NOT BE READ\\n'``.

    POSIX-only discrimination, stated rather than assumed: win32 takes an
    inherited stdin from ``GetStdHandle(STD_INPUT_HANDLE)``, not from fd 0, so
    the plant does not reach a win32 child and the arm there is a shape check
    whose real guard is ``test_bash_tool.py``'s kwarg assertion.

    A SYNC case on purpose: the plant is a process-wide ``dup2`` and
    ``_with_planted_stdin`` takes a synchronous callable, so the loop lives
    entirely inside the window in which fd 0 is planted.
    """

    reader = _script(
        tmp_path, "read_stdin.py", "import sys\nsys.stdout.write(repr(sys.stdin.read()))\n"
    )
    chunks: list[bytes] = []

    async def _go() -> ExecExitResult:
        task = _exec_task(_command(reader), tmp_path, chunks, timeout=DEADLINE)
        return await _bounded(task, DEADLINE + 5.0, "stdin is devnull")

    result = _with_planted_stdin(lambda: asyncio.run(_go()))

    assert result.exit_code == 0
    assert b"".join(chunks) == b"''"


# === 7: the tree is released, not killed ====================================


async def test_a_successful_run_attaches_first_and_releases_without_killing(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The attach happened, it happened FIRST, and nothing was killed.

    ``contained is True`` is here because a silently degraded attach (a win32
    ``OpenProcess`` refusal, a POSIX ``getpgid`` mismatch) reduces #222 to
    ``main``'s behaviour and would otherwise surface only as a mystery timeout
    (#222 critique TESTS-12).

    The ORDER is asserted rather than the WINDOW: a job assigned one loop turn
    late still catches every descendant here — the pwsh chain's own startup is
    0.27 s against one turn's microseconds — so "attach after the first await"
    is not a killable mutation and the recorded sequence is what replaces it
    (WIN32-11).
    """

    events: list[str] = []
    trees: list[ProcessTree] = []
    attach_pids: list[int] = []
    attach_kwargs: list[dict[str, Any]] = []
    real_attach = ProcessTree.attach
    real_hard_kill = ProcessTree.hard_kill
    real_wait = subprocess.Popen.wait

    def spy_attach(pid: int, **kwargs: Any) -> ProcessTree:
        events.append("attach")
        attach_pids.append(pid)
        attach_kwargs.append(dict(kwargs))
        tree = real_attach(pid, **kwargs)
        trees.append(tree)
        return tree

    def spy_hard_kill(self: ProcessTree) -> None:
        events.append("hard_kill")
        real_hard_kill(self)

    def spy_wait(self: subprocess.Popen[Any], timeout: float | None = None) -> int:
        events.append("wait")
        return real_wait(self, timeout=timeout)

    class SpyReader(_PipeReader):
        def start(self) -> None:
            events.append("reader-start")
            super().start()

    monkeypatch.setattr(bash_module, "ProcessTree", type("Spy", (), {"attach": spy_attach}))
    monkeypatch.setattr(bash_module, "_PipeReader", SpyReader)
    monkeypatch.setattr(ProcessTree, "hard_kill", spy_hard_kill)
    monkeypatch.setattr(subprocess.Popen, "wait", spy_wait)

    chunks: list[bytes] = []
    printer = _script(tmp_path, "printer.py", "import sys\nsys.stdout.buffer.write(b'ok\\n')\n")
    task = _exec_task(_command(printer), tmp_path, chunks, timeout=DEADLINE)
    result = await _bounded(task, DEADLINE + 5.0, "a successful run releases its tree")

    assert result.exit_code == 0
    assert b"".join(chunks) == b"ok\n"
    assert trees, "ProcessTree.attach was never called — the case measured nothing"
    tree = trees[0]
    assert tree.contained is True
    assert tree.closed is True
    assert "hard_kill" not in events
    assert events[0] == "attach"
    assert events.index("attach") < events.index("reader-start")
    assert events.index("attach") < events.index("wait")
    # M-18 (#230): the value the SITE really attaches with, read through
    # ``attach``'s own defaults rather than through the test's. The site passes
    # ``handle`` only — no ``kill_on_close`` at all — so
    # ``attach_kwargs[0].get("kill_on_close", False)`` would answer with this
    # case's own literal and stay green whatever ``attach``'s default became,
    # i.e. blind to exactly the regression this assertion exists for.
    # ``real_attach`` is captured above, BEFORE the ``bash_module.ProcessTree``
    # monkeypatch, so this reads the real signature.
    bound = inspect.signature(real_attach).bind(attach_pids[0], **attach_kwargs[0])
    bound.apply_defaults()
    assert bound.arguments["kill_on_close"] is False


# === 8: the exit-path drain, on the idle rule under a cap (#232) ============


async def test_a_successful_root_returns_without_waiting_for_its_holders_tail(
    tmp_path: Path, strays: list[int]
) -> None:
    """The owner's decision of 2026-09-06: the call comes back when the command does.

    The root exits 0 half a second before its helper writes ``LATE``, and the
    helper holds the pipe for the rest of its ``OUTLIVE`` life. Until #232
    ``exec`` waited for that pipe's EOF here, so both lines arrived and the call
    stayed open for the helper's whole life with NO ceiling of any kind —
    measured against a copy of ``7fa6796``: 4.050 s for a 4 s helper, 13.983 s
    for a call that had asked for 10 s, and 4.059 s for one that had asked for
    1 s and was told it had succeeded within its deadline. The drain is now
    ``run_contained``'s: idle :data:`EXIT_DRAIN_SECONDS` from the exit, capped
    at :data:`DRAIN_CAP_SECONDS` and at the caller's own deadline. ``LATE`` is
    written five graces late, so it is CUT — that is Pi's
    ``waitForChildProcess`` behaviour and the owner's choice, and #222 pinned
    the OPPOSITE on purpose (§H) so that this flip would be visible rather than
    silent. This case is that pin, inverted.

    The helper is asserted ALIVE on both arms. Nothing is killed on this leg at
    all: POSIX has no session to end here and win32's ``close()`` at
    ``kill_on_close=False`` ends nothing either.

    Bytes through ``sys.stdout.buffer`` on both writers: the text layer writes
    ``os.linesep`` and the windows leg of the first #221 CI run returned
    ``b'done\\r\\nlate\\r\\n'`` against this exact-bytes shape.

    ``elapsed < 1.0`` is a BARE LITERAL and not a :func:`_bound`-shaped ceiling,
    which was rejected on measurement: the gating windows legs run this case's
    launch chain in 0.265-0.343 s over 8 legs, so the case lands at ~0.33-0.45 s
    there against ≥ 0.55 s of headroom, whereas ``_bound(0.0)`` would be 7.5 s on
    win32 — wide enough for a regression that always waits the 2.0 s cap to pass
    its own clock check. The landed #221 sibling
    ``test_an_exited_root_with_a_pipe_holder_is_a_success_and_keeps_the_tail``
    asserts a bare literal too.

    RED on ``main``: with the helper holding the pipe for ``OUTLIVE`` the
    unbounded drain never returns, so the case dies in :func:`_bounded` rather
    than on the bytes — measured 7.55 s, warning
    ``elapsed=7.501s chunks=b'EARLY\\nLATE\\n'`` against 0.124 s and
    ``b'EARLY\\n'`` here. That is why the ``warnings.warn`` is inside the
    ``finally``: a RED run records both halves in one line.
    """

    marker = tmp_path / "pids.txt"
    root = _script(tmp_path, "root_late_tail.py", ROOT_WITH_A_LATE_TAIL)
    registrar = _registrar(marker, strays)
    chunks: list[bytes] = []
    task = _exec_task(
        _command(root, str(marker), "0.5", str(OUTLIVE)), tmp_path, chunks, timeout=None
    )
    started = time.monotonic()

    try:
        result = await _bounded(
            task, _bound(0.0) + 5.0, "the exit-path drain returns without the tail"
        )
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()
        warnings.warn(
            f"bash exec exit-path drain: elapsed={elapsed:.3f}s "
            f"chunks={b''.join(chunks)!r} on {sys.platform}",
            stacklevel=1,
        )

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    _root_pid, helper = pids
    assert result.exit_code == 0
    assert b"".join(chunks) == b"EARLY\n"
    assert elapsed < 1.0
    assert probe_state(helper) == STATE_ALIVE, (
        f"the successful call ended the helper {helper} it had backgrounded — this leg kills "
        f"nothing and the helper is meant to outlive the call"
    )


# === 8a: a holder that never falls idle, and the cap ========================


#: The deadline arm's ``timeout``. 1.0 s on POSIX, where the root exits in ~10 ms
#: and the deadline term is the only thing that can end the drain before the
#: 2.0 s cap. 2.0 s on win32: the root is a pwsh startup (0.5-0.7 s on the
#: runner, #222 handoff) plus a python child, and under load it did not exit
#: inside 1.0 s at all — ``main`` run 34171328513 (py3.12) timed the ROOT out
#: (``exit_code=None, timed_out=True``) where the same leg had passed twice
#: before. The term still bites at 2.0 s (it needs ``exit < timeout < exit +
#: DRAIN_CAP_SECONDS``, and the root exits well inside 2 s there); the mutant
#: that drops the term returns at ``exit + 2.0`` instead, which on POSIX is
#: 2.08 s against 1.00 s and on win32 lands within the sanity ceiling below —
#: so the DISCRIMINATION is the POSIX arm's, and the win32 arm pins the floor
#: and the root's own rc 0.
_DEADLINE_ARM_SECONDS = 2.0 if sys.platform == "win32" else 1.0


@pytest.mark.parametrize(
    "timeout", [None, _DEADLINE_ARM_SECONDS], ids=["no-deadline", "deadline-arm"]
)
async def test_a_holder_that_never_falls_idle_hits_the_drain_cap(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch, timeout: float | None
) -> None:
    """Why Pi's uncapped idle rule was NOT adopted (#232 §A), and the knob.

    The root exits at once and its escaped holder writes every 50 ms for its
    whole ``OUTLIVE`` life, so the idle timer is re-armed before it can ever
    expire — measured, an uncapped rule comes back only at the holder's own
    EOF, 5.076 s for a 5 s holder, and would not return at all for this one.

    The ``timeout=None`` arm is where :data:`DRAIN_CAP_SECONDS` is the ONLY
    bound: ``api.exec``'s default and the bash tool's ``default_timeout=0``
    escape hatch both reach ``exec`` with no deadline.

    The ``timeout=1.0`` arm is the DEADLINE term, and the only pin in the
    suite on the fact that THIS SITE hands ``_exit_drain_cap`` its deadline.
    ``tests/process_tree/test_run_contained.py`` pins that function as pure
    arithmetic, where the site's own arguments are not visible, so a site that
    passed it ``timeout=None`` was measured green across ``tests/tools``,
    ``tests/process_tree`` AND the full suite (10258 passed, no related
    failure) while coming back at 2.08 s where the CHANGELOG and ADR-0238 both
    promise 1.0 s. Measured 1.000-1.002 s adopted (4/4, 17-18 ticks) against
    2.076-2.087 s mutated (3/3). :data:`DRAIN_CAP_SECONDS` is the ceiling that
    discriminates — ``_bound(1.0)`` is 3.5 s POSIX / 8.5 s win32 and the
    mutation lands under it, so that shape would be inert.

    The pins are the CAP END and the truncation. The cap end is what a
    regression trips: with ``_wait`` stamping ``state.exited_at`` for the
    ordinary exit instead of ``_root_exited_at``, ``_drain_to_the_end`` takes
    the KILL branch's 1.0 s cap and the call comes back at 1.064 s — against
    the 2.0 s floor until #325, and as a drain that never computed the exit
    path's cap now (below). The ceiling folds :data:`KILL_DRAIN_SECONDS` in,
    so it is a sanity bound rather than a discriminator (4.5 s POSIX / 9.5 s
    win32); a second formula for the drains that kill nothing is not worth a
    divergence from this file's one helper.

    The truncation is asserted on the holder's OWN bytes because this root
    writes none of its own: the holder emits ~1200 ticks over ``OUTLIVE`` and
    the call must come back having seen a small fraction of them (measured 38
    on the ``None`` arm, 17-18 on the deadline arm).

    RED on ``main``: neither arm terminates there — the drain waits for a
    pipe EOF that is 60 s away — so :func:`_bounded` is what fails (9.501 s,
    176 ticks). A temporary copy whose holder lives 5 s measured 5.100 s and
    94 ticks on ``main`` against 2.067 s and 38 here.

    THE VERDICT IS THE DRAIN'S OWN RECORD SINCE #325 (:class:`_DrainRecord`).
    The case's premise is that the 0.05 s ticks keep the idle rule from ever
    firing, and a reader that hands nothing on for a grace breaks it on every
    platform, legitimately: under a 0.3 s whole-process stall 50 ms after the
    exit both arms failed their floors (``assert 2.0 <= 0.433``, ``1.0 <=
    0.450``). Such an end is said, with the drain's numbers, and the case runs
    again (:data:`ATTEMPTS`); what is pinned is that the drain ended AT ITS CAP
    — the flat one, or the deadline — which the ``_wait`` mutation above fails
    as ``no-exit-drain`` on both arms instead of on a floor. The deadline arm
    also reads the ``timeout`` the site handed ``_exit_drain_cap``, which is the
    site's deadline pinned by the drain rather than by a ceiling. The floors
    stay, after the record, as its consequence on the case's own clock; the
    ceilings are left to #341, which owns the upper bounds past a cap.
    """

    what = f"a holder that never falls idle (timeout={timeout})"
    premise_failures: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        marker = tmp_path / f"attempt-{attempt}" / "pids.txt"
        marker.parent.mkdir()
        registrar = _registrar(marker, strays)
        record = _DrainRecord()
        record.install(monkeypatch)
        chunks: list[bytes] = []
        # ``hold`` is the ROOT's own sleep, so ``hold=0.0`` is what makes the
        # root exit at once and arms the drain immediately; ``tick`` is the
        # HOLDER's, and 50 ms against a 100 ms grace is what keeps the window
        # open — while the reader keeps up (:class:`_DrainRecord`).
        command = _holder_command(marker.parent, marker, tick=0.05, hold=0.0)
        task = _exec_task(command, marker.parent, chunks, timeout=timeout)
        started = time.monotonic()

        try:
            result = await _bounded(task, _bound(DRAIN_CAP_SECONDS) + 5.0, what)
        finally:
            elapsed = time.monotonic() - started
            pids = registrar.settle()
            end = record.end()
            warnings.warn(
                f"bash exec drain cap: timeout={timeout} elapsed={elapsed:.3f}s "
                f"ticks={b''.join(chunks).count(b'tick')} end={_label(task, end)} "
                f"attempt={attempt}/{ATTEMPTS} on {sys.platform}",
                stacklevel=1,
            )
        if end.kind != "idle":
            break
        premise_failures.append(_premise_failed(f"attempt {attempt}", end))
        warnings.warn(premise_failures[-1], stacklevel=1)
        # The escaped holder writes every 0.05 s for ``OUTLIVE`` into a reader
        # this process detached; it may not run on through the next attempt.
        for pid in pids or ():
            _reap(pid)
    else:
        pytest.fail(_every_attempt_failed_its_premise(what, premise_failures))

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert end.kind == "cap", (
        f"the exit-path drain {end.summary} — a holder writing every 0.05 s is cut at the cap "
        f"and nowhere else ({elapsed:.3f}s from the call)"
    )
    if timeout is not None:
        assert record.timeout == timeout, (
            f"the site handed its exit drain timeout={record.timeout}, not the caller's "
            f"{timeout}s — the deadline term is gone ({end.summary})"
        )
    if timeout is None:
        assert DRAIN_CAP_SECONDS <= elapsed <= _bound(DRAIN_CAP_SECONDS)
    else:
        # POSIX: the deadline (1.0 s) is below the flat cap, so reaching the cap
        # means the term is gone. win32: the deadline (2.0 s) equals the cap
        # measured from a root that exits at ~0.5 s, so the ceiling there is a
        # sanity bound (deadline + one grace + the runner's slack), not the
        # discriminator — see :data:`_DEADLINE_ARM_SECONDS`.
        ceiling = (
            timeout + KILL_DRAIN_SECONDS + SLACK if sys.platform == "win32" else DRAIN_CAP_SECONDS
        )
        assert timeout <= elapsed < ceiling, (
            f"elapsed={elapsed:.3f}s — the caller's own deadline of {timeout}s did not bound "
            f"the exit drain (ceiling {ceiling:.1f}s)"
        )
    ticks = b"".join(chunks).count(b"tick\n")
    assert 0 < ticks < 200, (
        f"the drain delivered {ticks} ticks — the holder writes ~1200 over its life, so a "
        f"count outside this range means the cap did not cut anything"
    )
    # #260: a cap that cuts a HELPER is a proven end — the reader had handed on
    # everything its first post-exit look saw — so the result says nothing
    # about it. The notice is for the command's own bytes; on every command
    # that backgrounds something chatty it would be noise.
    assert result.output_unconfirmed is False

# === 8b: an abort that lands in the exit-path drain =========================


async def test_an_abort_during_the_exit_path_drain_keeps_the_helper(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The watcher is disarmed BEFORE the drain, so a late Esc kills nothing.

    The command SUCCEEDED — the root exited 0 after backgrounding a helper meant
    to outlive it — and the only thing still open is the pipe a second child
    holds. An abort arriving in that window must not be able to reach back
    through ``_end_the_tree`` and kill the tree the command has already been
    paid for: that is exactly the process ``kill_on_close=False`` exists to keep
    (Pi #8225, ADR-0238, §A.6), and on win32 ``hard_kill`` is
    ``taskkill /T /F`` + ``TerminateJobObject``, which reaches it even through a
    process group of its own.

    Measured on the branch before the teardown was reordered (2026-09-06,
    darwin): ``exit_code=None``, ``timed_out=False``, ``chunks == [b'ROOT\\n']``
    and the helper GONE — the abort at 0.3 s ended a tree whose command had
    exited 0 at 0.05 s and cut the tail off the output. With the watcher
    cancelled inside the drain's ``finally`` (the order ``main`` has) the abort
    has nothing left to fire into.

    THE TAIL IS CHATTY SINCE #232, and that is what keeps the case measuring
    anything. With the silent tail this case shipped with, the idle rule ends
    the drain at ~0.1 s and the abort at 0.3 s lands after ``exec`` has already
    returned (measured 0.364 s, ``task.done()`` True at the abort). A longer
    ``hold`` makes that tail QUIETER, not chattier, so the ``tick`` argv is the
    repair, and "the abort landed inside the drain" is an assertion: ``exec``
    has not returned when it fires.

    The CAP END is the discriminator, not the byte pin. Without it the case
    passes at 1.023 s with ``_wait`` stamping ``state.exited_at`` for the
    ordinary exit instead of ``_root_exited_at`` — which routes the drain into
    the KILL branch's 1.0 s cap — exactly as it passes at 2.025 s under the
    rule (measured). Until #325 that was an elapsed FLOOR of
    :data:`DRAIN_CAP_SECONDS`; the ceiling folds :data:`KILL_DRAIN_SECONDS` in
    and is a sanity bound (4.5 s POSIX / 9.5 s win32). ``hold`` is 5.0 rather
    than ``OUTLIVE`` so ``_bounded`` stays a real guard rather than a 70 s one:
    the design's refuter lane measured that at ``OUTLIVE`` a dropped cap runs
    the case ~60 s and then reddens on the helper pin with a message that is
    false about what happened.

    #325: THE FLOOR FAILED ON A PREMISE, NOT ON THE PRODUCT. windows-latest
    py3.11 (run 35767268004, before #260) came back ``assert 2.0 <= 1.375``
    with ``exit_code 0``: the drain had ended on its own before the cap, and
    the floor could not say how. The case's premise is that the tail's ticks
    keep the idle rule from firing, and it is a premise about the READER —
    :class:`_DrainRecord` has the mechanism and the measurements. So the
    verdict is the drain's record now: it must end at its cap (the mutation
    above ends it with no exit-path cap at all, ``no-exit-drain``; a dropped
    cap ends it on the tail's EOF, ``eof``); an idle end is reported as the
    premise it is and the case runs again (:data:`ATTEMPTS`); the floor stays,
    after the record, as its consequence on this case's clock. On the new
    drain the early end did not come back on CI: run 36033034335 repeated this
    case 20 times quiet and 20 times under ``os.cpu_count()`` CPU burners on
    each of the four legs, 0 early ends in 160 (80 on windows), every drain
    ended at the hard cap with a proof, and the tail's longest write gap on
    windows was 0.051 s quiet and 0.080 s loaded against the 0.1 s grace.

    THE ABORT WAITS FOR THE DRAIN'S START, not 0.3 s after the announcement
    (#325 review). The root announces and THEN exits — on win32 that exit is
    pwsh's teardown — and the same diag run landed the 0.3 s abort only 0.044 s
    after the root's exit at the least (win32, loaded): an abort ahead of the
    exit is the watcher's to act on, it ends the tree, and the case died on a
    bare ``assert None == 0`` (reproduced by holding the root 0.35 s past its
    announcement). :func:`_into_the_exit_drain` makes it an event: the site
    computing the exit path's cap, after the watcher is disarmed.

    #230 answered whether an abort in this window SHOULD do anything — it kills
    nothing — at ``run_contained``; this case pins that #222 had already put
    this site there, rather than answering it by accident.
    """

    what = "an abort during the exit-path drain"
    hold = 5.0
    tick = 0.05
    premise_failures: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        marker = tmp_path / f"attempt-{attempt}" / "pids.txt"
        marker.parent.mkdir()
        root = _script(
            marker.parent, "root_and_tail.py", ROOT_THAT_BACKGROUNDS_A_HELPER_BEHIND_A_TAIL
        )
        registrar = _registrar(marker, strays, fields=3)
        record = _DrainRecord()
        record.install(monkeypatch)
        signal = AbortSignal()
        chunks: list[bytes] = []
        command = _command(root, str(marker), str(OUTLIVE), str(hold), str(tick))
        task = _exec_task(command, marker.parent, chunks, signal=signal, timeout=None)
        started = time.monotonic()

        try:
            await _await_pids(registrar, what)
            # On the drain's START, not on a settle after the announcement: the
            # root exits after it announces, and an abort ahead of that exit
            # lands in ``_wait`` (:func:`_into_the_exit_drain`).
            in_the_drain = await _into_the_exit_drain(task, record, what)
            aborted_at = time.monotonic() - started
            signal.abort()
            result = await _bounded(task, hold + 10.0, what)
        finally:
            elapsed = time.monotonic() - started
            pids = registrar.settle()
            end = record.end()
            warnings.warn(
                f"bash exec abort in the exit drain: {elapsed:.3f}s end={_label(task, end)} "
                f"attempt={attempt}/{ATTEMPTS} on {sys.platform}",
                stacklevel=1,
            )
        if end.kind != "idle":
            break
        premise_failures.append(_premise_failed(f"attempt {attempt}", end))
        warnings.warn(premise_failures[-1], stacklevel=1)
        # The escaped helper and the tail still write into a reader this
        # process detached; neither may run on through the next attempt.
        for pid in pids or ():
            _reap(pid)
    else:
        pytest.fail(_every_attempt_failed_its_premise(what, premise_failures))

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    _root_pid, helper, _tail = pids
    # The drain's own end first: a call that took a kill branch never had an
    # exit-path drain for the abort to land in, and that is the regression to
    # name, not the premise below.
    assert end.kind == "cap", (
        f"the exit-path drain {end.summary} — a tail writing every {tick}s is cut at the cap "
        f"and nowhere else ({elapsed:.3f}s from the call, the abort at {aborted_at:.3f}s, "
        f"exit_code={result.exit_code})"
    )
    assert in_the_drain, (
        f"exec had already returned when the abort fired — the case measured nothing; the "
        f"drain {end.summary}"
    )
    output = b"".join(chunks)
    assert result.exit_code == 0
    assert result.timed_out is False
    assert output.startswith(b"ROOT\n")
    assert b"TAIL\n" in output
    # ``hold / tick`` is 100 — MORE than the tail writes in its whole life, so
    # the old bound could never fire. The cap admits about
    # ``DRAIN_CAP_SECONDS / tick`` (40 measured); 1.5× of that is under the
    # 100 an uncapped drain would deliver, so this is a real second killer for
    # mutation (b) beside the cap end.
    tails = output.count(b"TAIL\n")
    assert tails <= DRAIN_CAP_SECONDS / tick * 1.5, (
        f"the drain delivered {tails} tail lines — the cap admits about "
        f"{DRAIN_CAP_SECONDS / tick:.0f} and the tail's whole life is {hold / tick:.0f}, "
        f"so a count this high means the cap cut nothing"
    )
    assert DRAIN_CAP_SECONDS <= elapsed <= _bound(DRAIN_CAP_SECONDS)
    assert probe_state(helper) == STATE_ALIVE, (
        f"the abort at {aborted_at:.3f}s, {INTO_THE_DRAIN}s into the exit-path drain, killed "
        f"the backgrounded helper {helper} — the tree the command exited 0 to leave behind"
    )


# === 8c: a turn cancel that lands in the watcher teardown ===================


class _CancelsTheTurnFromInsideTheWatcher:
    """``test_abort_signal.py``'s hook, duplicated rather than imported (#234).

    This file imports no fakes today — every case above drives real children —
    and twelve lines of duplication keeps that boundary where it is. ``exec``
    starts the watcher on ``hasattr(signal, "wait")`` alone, and ``wait()`` here
    blocks on an event nobody sets, so the ONLY thing that wakes it is the
    teardown's own ``watcher_task.cancel()``: cancelling the task running
    ``exec`` from there lands the cancellation in the one window #234 exists
    for, deterministically.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.exec_task: asyncio.Task[ExecExitResult] | None = None
        self.cancelled_the_turn = False

    async def wait(self) -> None:
        try:
            await self._event.wait()
        except asyncio.CancelledError:
            assert self.exec_task is not None, "the case never handed over the exec task"
            self.cancelled_the_turn = True
            self.exec_task.cancel()
            raise


async def test_a_turn_cancel_in_the_watcher_teardown_keeps_the_helper_and_still_detaches(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0238's Consequences paragraph, against a real tree instead of a script.

    ``test_abort_signal.py``'s case for this window uses ``echo ok`` — no tree,
    no helper, no holder — so it pins the PROPAGATION and nothing about what
    the cancellation costs. This one is 8b's command with the cancel in place of
    the abort: the root exits 0 after backgrounding a helper meant to outlive it
    and a tail still holding the pipe, and the cancellation is delivered from
    inside the watcher while ``exec`` is suspended in the teardown.

    What must hold, all of it after the cancellation is already unwinding:

    * the helper the command exited 0 to leave behind is STILL ALIVE — this leg
      ends no tree of its own (the ``except asyncio.CancelledError`` that does
      is around ``_wait``, which had already returned), and that is the process
      ``kill_on_close=False`` exists to keep (Pi #8225; ADR-0238, "Decision" —
      ``close()`` is a release, not a kill);
    * the reader is detached and the tree closed, because the drain, the detach
      and the close are the three ``finally``s outside this one;
    * the tail the holder had not written yet is CUT, because since #232 the
      exit-path drain ends one :data:`EXIT_DRAIN_SECONDS` after the root's own
      exit — and the cancellation is still delivered, the helper still alive,
      the reader still detached, with nothing about that resting on the drain
      having been unbounded (measured 2.07 s before; 0.126 s after, 3/3).

    RED on ``main``: the teardown swallows the cancellation, so ``exec`` returns
    an ordinary ``exit_code=0`` and ``pytest.raises`` reports DID NOT RAISE.
    #230 owns whether an abort in this window should kill anything; this case
    pins only that #234 did not answer that by accident either way.
    """

    marker = tmp_path / "pids.txt"
    hold = 2.0
    root = _script(
        tmp_path, "root_and_tail.py", ROOT_THAT_BACKGROUNDS_A_HELPER_BEHIND_A_TAIL
    )
    detached: list[bool] = []
    trees: list[ProcessTree] = []
    real_detach = _PipeReader.detach
    real_attach = ProcessTree.attach

    def spy_detach(self: _PipeReader) -> None:
        detached.append(True)
        real_detach(self)

    def spy_attach(pid: int, **kwargs: Any) -> ProcessTree:
        tree = real_attach(pid, **kwargs)
        trees.append(tree)
        return tree

    monkeypatch.setattr(_PipeReader, "detach", spy_detach)
    monkeypatch.setattr(bash_module, "ProcessTree", type("Spy", (), {"attach": spy_attach}))

    registrar = _registrar(marker, strays, fields=3)
    signal = _CancelsTheTurnFromInsideTheWatcher()
    chunks: list[bytes] = []
    # ``tick=0.0`` is today's silent tail — one write after ``time.sleep(hold)``
    # — which #232's drain never waits for. 8b passes the chatty one.
    command = _command(root, str(marker), str(OUTLIVE), str(hold), "0.0")
    task = _exec_task(command, tmp_path, chunks, signal=signal, timeout=None)
    signal.exec_task = task
    started = time.monotonic()

    try:
        with pytest.raises(asyncio.CancelledError):
            await _bounded(task, hold + 10.0, "a turn cancel in the watcher teardown")
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    _root_pid, helper, _tail = pids
    assert signal.cancelled_the_turn, (
        "the teardown never cancelled the watcher — the case measured nothing"
    )
    assert task.cancelled(), "the watcher teardown swallowed the caller's cancellation"
    assert probe_state(helper) == STATE_ALIVE, (
        f"the cancellation in the teardown killed the backgrounded helper {helper} — the tree "
        f"the command exited 0 to leave behind"
    )
    assert detached, "the reader was never detached — the retention of #221 site-exec-1 is back"
    assert trees and trees[0].closed is True
    assert b"".join(chunks) == b"ROOT\n"
    warnings.warn(
        f"teardown cancel with a holder: {elapsed:.3f}s on {sys.platform}", stacklevel=1
    )


# === 9: delivery completeness ===============================================


async def test_every_byte_is_delivered_under_a_loaded_loop(
    tmp_path: Path, strays: list[int]
) -> None:
    """The tail must not be silently dropped — measured 4 ways in the critique.

    ``loop.call_soon_threadsafe(on_data, chunk)`` plus a POLLED ``reader.eof``
    loses the end of a command's output: 67-72/300, 8-14/120, 15/250 and 9/3000
    across four independent harnesses, because ``_run_once`` snapshots
    ``len(self._ready)`` and a callback enqueued after the snapshot runs only on
    the NEXT iteration — after the poll observed EOF and ``exec`` returned. The
    fix is that the chunks and the EOF go through the SAME FIFO, which makes
    "every chunk callback has run before the waiter resumes" a property of the
    queue rather than of timing.

    The reference is a plain ``subprocess.run`` of the same argv, so the
    comparison is CRLF-safe by construction.

    WHAT THIS CASE DID NOT KILL, MEASURED (2026-09-05, darwin, py3.12). #222's
    §C.5(l) expects it to redden a polled ``reader.eof`` in place of
    ``await eof.wait()``. Rebuilt as a mutant twice — polling at 5 ms and at
    ``sleep(0)`` — it stayed GREEN 40 rounds each, because a poll that AWAITS is
    itself at the back of the same FIFO: its wake is enqueued before the chunk
    callback it would have to overtake. The critique's own rate was 0.3-6 % per
    run, so 40 rounds is not enough to call the mutant dead either; what this
    case pins is the byte-for-byte contract, and the ordering underneath it is
    pinned in ``tests/process_tree/test_pipe_reader_callbacks.py``.

    §C.5(l2) — "drop the post-cut ``sleep(0)``" — is still not this case's to
    kill, but since #232 the reason has changed and the old one has inverted
    (#222 review M-10). The yield is the last line of
    ``_drain_after_the_exit``, which now serves the SUCCESS path as well as the
    three kill legs, and this case is one of its callers: measured 2026-09-07
    (darwin, py3.12), a raise injected at its top reddens **16 of this file's 17
    ids, this case among them**, where on ``main`` the same injection at
    ``_drain_past_the_kill`` reddened exactly 7 and excluded it. Deleting the
    yield reddens nothing here either: 40 rounds of this case and 5 rounds of
    the whole file, 0 failures. Where it IS load-bearing is a drain that ends on
    the timeout or the cap and resumes with chunk callbacks still queued behind
    it.

    WHAT THIS CASE ACTUALLY RESTS ON, corrected by #260. This docstring used to
    say that :data:`CHATTY` backgrounds nothing, "so the pipe EOFs at the root's
    own exit and the drain ends on ``eof`` rather than on the idle timer or the
    cap". The pipe does reach EOF at the exit — but the drain learns of that EOF
    only after the READER has read it, and until #260 a reader starved across
    the exit let the idle timer, armed at the exit, end the drain first. That is
    what failed here in six recorded ubuntu CI runs, in whole 1 KiB lines off
    the tail: 26,624 B (run 34238827475), 38,912 (34312006861), 7,168
    (35420451091), 24,576 (35447857370), 8,192 (35754728478) and 57,344
    (35887669985) — #260, #261. The drain now ends on the idle timer or the cap
    only with a proof that the reader holds nothing and the pipe is empty, or
    that it has handed on everything its first post-exit look saw
    (``_PipeReader.proven``); without one it waits, up to a hard cap it reports.
    That is exact on POSIX. On ``windows-latest``, where this case runs too, the
    proof is the reader's own last look, and a reader starved right after an
    empty look leaves it stale: the idle timer can still end the drain with
    bytes undelivered, silently — the stated win32 residual, pinned as a loss
    by the forced-win32 ``preread``/``inread`` ids of the file below — so a red
    here on that leg is not a refutation of #260. The FIFO argument carries the
    delivery when the drain ends on ``eof``; the proof, and the ``sleep(0)``
    after it (pinned there since #260 by
    ``test_a_chunk_posted_while_the_drain_proves_is_still_delivered``), carry
    it when it does not. The deterministic form of this failure — the reader
    held across the exit at four points, red on the base tree every run — is
    ``tests/tools/test_bash_drain_asks_the_pipe.py``; this case stays the
    natural-load half, byte for byte against ``subprocess.run``.
    """

    chatty = _script(tmp_path, "chatty.py", CHATTY)
    command = _command(chatty)
    shell = _resolve_shell(get_shell_env())
    reference = subprocess.run(  # noqa: S603
        [shell.path, shell.command_flag, command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=DEADLINE * 4,
    )
    assert len(reference.stdout) >= 2 * 1024 * 1024, "the reference did not produce 2 MiB"

    stop = asyncio.Event()

    async def _busy() -> None:
        # A loaded loop is the condition under which the losses were measured:
        # a callback queued behind this task's ticks is what the snapshot cuts.
        while not stop.is_set():
            sum(range(2000))
            await asyncio.sleep(0)

    busy = asyncio.ensure_future(_busy())
    chunks: list[bytes] = []
    task = _exec_task(command, tmp_path, chunks, timeout=DEADLINE * 4)
    try:
        result = await _bounded(task, DEADLINE * 4 + 5.0, "delivery completeness")
    finally:
        stop.set()
        await busy

    assert result.exit_code == 0
    assert len(b"".join(chunks)) == len(reference.stdout)
    assert b"".join(chunks) == reference.stdout


# === 10: a cancel that lands in the drain ===================================


def test_a_cancel_during_the_drain_still_detaches_and_closes(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One Esc is enough, and it must not cost the detach or the close.

    Measured in the critique with the design's nesting collapsed into one
    ``finally``: a single ``task.cancel()`` at t=0.5 s lands on the drain (the
    root has exited, a helper holds stdout) and ``detach`` never runs, against
    ``close``, which has its own ``finally`` and survives. The leak that reopens
    is #221 site-exec-1's: 14.9 GB retained against 0 MB after 2 s of a chatty
    holder.

    THE SECOND HALF IS THE READER THREAD AFTER THE LOOP IS GONE. This case runs
    ``asyncio.run``, so the loop is CLOSED when the helper finally exits and the
    reader fires its EOF callback into it — ``call_soon_threadsafe`` raises
    ``RuntimeError`` there, which ``_PipeReader.run``'s
    ``except (OSError, ValueError)`` does not catch and which
    ``threading.excepthook`` printed out of a TUI in the critique's measurement.
    Nothing may be recorded.

    #325: the cancel lands in a drain the holder's 0.05 s ticks hold open,
    which is the premise :class:`_DrainRecord` describes — a reader that
    hands nothing on for a grace lets the idle rule end the drain first, and
    the cancel then finds ``exec`` returned (``DID NOT RAISE``). Measured
    under this case's EARLIER timing — a blind cancel 0.2 s after the root's
    announcement — with the reader held from its first look 50 ms after the
    exit until 0.3 s after it: 3 of 3 returned ``exit_code=0`` before the
    cancel. Under the cancel it has now, :data:`INTO_THE_DRAIN` after the
    drain's start, a hold that short no longer ends the drain first (the #325
    verdict probe, the reader held 0.5 s once it has handed on 10 bytes:
    held once per attempt, ``PASSED``, ``end=cancelled-in-the-drain``); a
    reader that never stamps ``last_chunk_at`` still drives every attempt
    into this premise (#325 sabotage S-d). That end is said with the
    drain's numbers and the case runs again (:data:`ATTEMPTS`); one that is
    not the idle rule fails as it is — including a call that never computed
    the exit path's cap (``no-exit-drain``), which had no drain for the cancel
    to land in. The cancel waits for that drain's START, which the record sees
    (:func:`_into_the_exit_drain`), and lands :data:`INTO_THE_DRAIN` into it:
    the root's exit follows its announcement by as long as pwsh's teardown
    takes, and a cancel ahead of it measured the kill branch's drain while the
    log said the exit drain's.
    """

    what = "a cancel during the drain"
    measured = "a cancel inside the drain"
    detached: list[bool] = []
    trees: list[ProcessTree] = []
    real_detach = _PipeReader.detach
    real_attach = ProcessTree.attach

    def spy_detach(self: _PipeReader) -> None:
        detached.append(True)
        real_detach(self)

    def spy_attach(pid: int, **kwargs: Any) -> ProcessTree:
        tree = real_attach(pid, **kwargs)
        trees.append(tree)
        return tree

    monkeypatch.setattr(_PipeReader, "detach", spy_detach)
    monkeypatch.setattr(bash_module, "ProcessTree", type("Spy", (), {"attach": spy_attach}))

    async def _go(
        command: str,
        cwd: Path,
        registrar: Any,
        record: _DrainRecord,
        returned: list[ExecExitResult],
    ) -> None:
        task = _exec_task(command, cwd, [], timeout=None)
        # On EVENTS and not on a wall clock. The announcement, the pattern
        # cases 2/3 already use: the root has to have forked before the cancel,
        # and a blind 0.5 s is under pwsh's own 0.5-0.57 s startup on the
        # runner — emulating that startup, this case died on "the root never
        # announced its tree". Then the drain's START (#325 review): the root
        # exits AFTER it announces — on win32 that is pwsh's teardown — and a
        # cancel ahead of the exit lands in ``_wait`` and measures the kill
        # branch's drain while the log said the exit drain.
        await _await_pids(registrar, what)
        if not await _into_the_exit_drain(task, record, what):
            # The call came back before the cancel: the caller says how.
            returned.append(task.result())
            return
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await _bounded(task, _bound(0.0) + 5.0, what)

    crashes: list[Any] = []
    saved_hook = threading.excepthook
    threading.excepthook = crashes.append
    premise_failures: list[str] = []

    try:
        for attempt in range(1, ATTEMPTS + 1):
            # Per attempt: a premise-failed attempt's drain ended normally and
            # detached too, which would answer for the one that measured.
            detached.clear()
            marker = tmp_path / f"attempt-{attempt}" / "pids.txt"
            marker.parent.mkdir()
            root = _script(
                marker.parent, "root_with_a_chatty_holder.py", ROOT_WITH_AN_ESCAPED_HOLDER
            )
            holder_script = _script(marker.parent, "holder.py", HOLDER)
            # ``hold=0.0``: the root announces and exits at once (as far as its
            # shell lets it), so the call reaches the exit-path drain the cancel
            # waits for — held open by the holder's 0.05 s ticks while the
            # reader keeps up (:class:`_DrainRecord`). The holder writes for 2.0 s and then
            # closes the pipe, which is what fires the EOF callback into the
            # loop ``asyncio.run`` has by then closed.
            command = _command(root, str(marker), holder_script, "2.0", "0.05", "0.0")
            registrar = _registrar(marker, strays)
            record = _DrainRecord()
            record.install(monkeypatch)
            returned: list[ExecExitResult] = []
            asyncio.run(_go(command, marker.parent, registrar, record, returned))
            pids = registrar.settle()
            assert pids is not None, "the root never announced its tree — the case measured nothing"
            # Outlive the holder: its EOF callback is what reaches the closed loop.
            assert _await_dead(pids[1]) != STATE_ALIVE
            time.sleep(0.2)
            end = record.end()
            # A drain the cancel interrupted has no end of its own to classify;
            # that it WAS the exit drain is the record's to say — ``_go`` only
            # cancels once the site computed that drain's cap. A drain whose
            # last answer was a proof had already ENDED on it when the cancel
            # landed (in the one yield before the detach,
            # :meth:`_DrainRecord.last_ask_proven`): that is said as its own
            # end, and the verdicts below stay the call's.
            if returned or record.cap is None:
                label = end.kind
            elif record.last_ask_proven():
                label = f"{end.kind}-then-cancelled"
            else:
                label = "cancelled-in-the-drain"
            warnings.warn(
                f"bash exec cancel during the drain: end={label} "
                f"attempt={attempt}/{ATTEMPTS} on {sys.platform}",
                stacklevel=1,
            )
            if not returned:
                break
            assert end.kind == "idle", (
                f"exec had already returned when the cancel fired, and not on the idle rule "
                f"— the drain {end.summary}"
            )
            premise_failures.append(_premise_failed(f"attempt {attempt}", end, measured))
            warnings.warn(premise_failures[-1], stacklevel=1)
        else:
            pytest.fail(_every_attempt_failed_its_premise(what, premise_failures, measured))
    finally:
        threading.excepthook = saved_hook

    assert detached, "the reader was never detached — the retention of #221 site-exec-1 is back"
    assert trees and trees[-1].closed is True
    assert crashes == [], f"the reader thread raised into threading.excepthook: {crashes}"


# === 11: where on_data runs, and when it stops ==============================


async def test_on_data_runs_on_the_loop_thread_and_never_after_the_return(
    tmp_path: Path, strays: list[int]
) -> None:
    """The callback contract the three callers rely on.

    All three join ``chunks`` with NO await after ``exec`` returns
    (``create_bash_tool``'s ``execute``, ``cli/repl.py``, ``rpc_mode.py``), so a
    delivery from the reader thread would race a ``list.append`` against a
    ``b"".join`` and a delivery after the return would append to a list nobody
    reads again.

    The holder here goes on writing PAST the kill on POSIX (it is a ``setsid``
    escapee, so the group kill does not reach it) — which is what gives the
    "nothing after the return" half something to observe. On win32 the job ends
    the holder, so that half is vacuous there and the thread-identity half is
    what the leg asserts; the asymmetry is the same one case 4 documents.

    WHICH MUTANT IT ACTUALLY KILLS, MEASURED (2026-09-05, darwin). §C.5(k) —
    ``on_chunk=_deliver`` with no ``call_soon_threadsafe`` — is red here, on the
    ident set. §C.5(n) — the primitive's ``_detached`` guard no longer covering
    the delivery — stays GREEN, and that is a property of the site rather than a
    hole: ``exec`` clears ``delivering`` on the loop in the same ``finally`` as
    the ``detach``, so the two guards cover each other and no site-level case
    can see one of them move. The guard's own case is in
    ``tests/process_tree/test_pipe_reader_callbacks.py`` —
    ``test_detach_stops_the_callback_and_the_reader_reads_on``.
    """

    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays)
    idents: list[int] = []
    stamps: list[float] = []

    def on_data(chunk: bytes) -> None:
        idents.append(threading.get_ident())
        stamps.append(time.monotonic())

    ops = create_local_bash_operations()
    command = _holder_command(tmp_path, marker, tick=0.02)
    # 2.0 for case 4's reason: the timeout clock starts at the ``[shell, flag,
    # command]`` spawn, so at 0.5 the kill can land before the holder has
    # written its first tick and ``on_data never fired`` is what the case
    # reports. Emulating pwsh's 0.5-0.57 s startup it did exactly that.
    task = asyncio.ensure_future(ops.exec(command, str(tmp_path), on_data=on_data, timeout=2.0))

    try:
        result = await _bounded(task, _bound(2.0) + 5.0, "on_data placement")
    finally:
        returned_at = time.monotonic()
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    assert result.timed_out is True
    assert idents, "on_data never fired — the case measured nothing"
    assert set(idents) == {threading.get_ident()}, (
        f"on_data ran on {set(idents)}, not on the loop thread {threading.get_ident()}"
    )
    assert max(stamps) <= returned_at
    delivered_by_the_return = len(idents)
    # The holder is still writing on POSIX; a delivery now is one the caller
    # will never look at.
    await asyncio.sleep(0.5)
    assert len(idents) == delivered_by_the_return


# === 12: the belt around the attach =========================================


async def test_a_failing_attach_does_not_leave_the_child_running(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#221 R2's shape: nothing between the spawn and the first guarded statement.

    ``attach`` swallows ``OSError`` into ``contained=False`` on win32 and cannot
    raise on POSIX past ``_require_pid``, so this is a belt rather than a path —
    but a spawned child must never be left running by an exception in that
    window, and the same window holds the reader start and an ``assert``.

    "BEFORE THE FIRST ``await``" is the rule the attach obeys at this site, and
    it is not "in the statement after the spawn" (#222 review M-12): that is
    true of ``run_cancellable``, whereas ``exec`` runs six statements — the
    loop, the two channels, ``delivering``, ``_post`` and ``_deliver`` — between
    the ``Popen`` and the ``ProcessTree.attach``. All six are synchronous, which
    is what the win32 job-assignment window actually needs, and the ORDER rather
    than the window is what case 7 asserts.
    """

    spawned: list[subprocess.Popen[Any]] = []
    real_popen = subprocess.Popen

    def spy_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        strays.append(proc.pid)
        return proc

    def refuse(pid: int, **kwargs: Any) -> ProcessTree:
        raise OSError("attach refused")

    monkeypatch.setattr(subprocess, "Popen", spy_popen)
    monkeypatch.setattr(bash_module, "ProcessTree", type("Spy", (), {"attach": refuse}))

    # 15 s AND NOT 60, AND NOT AN ANNOUNCEMENT (#222 review M-4). What
    # ``spy_popen`` registers is the pid ``exec`` spawned, which on the windows
    # leg is pwsh and not python: pwsh does not exec-replace its child the way
    # bash does, so the belt's ``proc.kill()`` ends the shell and a python it had
    # already started would run on unregistered. The marker protocol cannot
    # reach it — measured 2026-09-06 on darwin, this child is killed inside the
    # attach window, ~1 ms after the spawn and long before the interpreter runs
    # a line, so the announcement NEVER lands (``announced=None``, marker
    # absent) and waiting for it cost the case a flat 5.05 s. A bounded sleep is
    # what is left: still 3x ``DEADLINE``, so the belt is what the assertion
    # below observes and not old age, and a win32 escapee is a 15 s stray rather
    # than a minute of one.
    sleeper = _script(tmp_path, "sleeper.py", "import time\ntime.sleep(15)\n")
    ops = create_local_bash_operations()
    chunks: list[bytes] = []

    with pytest.raises(OSError, match="attach refused"):
        await ops.exec(_command(sleeper), str(tmp_path), on_data=chunks.append)

    assert spawned, "nothing was spawned — the case measured nothing"
    state = _await_dead(spawned[0].pid)
    assert state != STATE_ALIVE, f"the root {spawned[0].pid} is still {state} after a failed attach"
