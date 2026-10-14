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
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import warnings
import weakref
from collections.abc import Callable
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

from tests.process_probe import STATE_ALIVE, STATE_GONE, STATE_ZOMBIE, probe_state
from tests.process_tree.test_process_tree_real_processes import DEADLINE, _await_dead, _reap
from tests.process_tree.test_run_contained_real_processes import (
    OUTLIVE,
    _registrar,
    _with_planted_stdin,
)
from tests.process_tree.test_run_contained_real_processes import (
    strays as _strays_fixture,
)
from tests.process_tree.test_the_drain_asks_the_pipe import (
    HardEnd,
    _looks_and_parks,
    the_drain_stopped_at_its_hard_end,
    the_hard_end,
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
    (``test_abort_signal.py:130-139``). :data:`MARK` is appended to every argv
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
#: asserted on the one that measured. 8a's attempts can fail their premise two
#: more ways, each said and run again within the same three: a drain the
#: runner held away from its own rule (#341, :func:`_judged_on_its_record`)
#: and, on its deadline arm, a root that had not exited by the caller's
#: deadline (#446, :func:`_why_the_root_timed_out` and
#: :func:`_the_timeout_leg_left`).
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
        #: ``("look", at, last_chunk_at)``, ``("park", seconds)`` (#341) and
        #: ``("ask", at, answer, pinned, handed_on, eof)``.
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

        class Parks:
            """``asyncio`` whose ``wait_for`` logs the drain's park — its ``due - now`` (#341)."""

            def wait_for(self, awaitable: Any, timeout: float | None) -> Any:
                if threading.current_thread() is record._thread:
                    record.events.append(("park", timeout))
                # The real coroutine, not awaited here: no frame, no loop turn.
                return asyncio.wait_for(awaitable, timeout)

            def __getattr__(self, name: str) -> Any:
                return getattr(asyncio, name)

        monkeypatch.setattr(bash_module, "time", self)
        monkeypatch.setattr(bash_module, "_exit_drain_cap", cap)
        monkeypatch.setattr(bash_module, "_PipeReader", Recorded)
        monkeypatch.setattr(bash_module, "asyncio", Parks())

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

    def hard_end(self) -> HardEnd:
        """The documented hard end replayed on this drain's looks and parks (#341).

        :data:`DRAIN_CAP_SECONDS` past the exit the drain was armed from — the
        exit path's hard cap, which on the ``timeout=None`` arms is its soft
        cap too — or one grace past a late look near it, once.
        """

        assert self.exited_at is not None, "the exit path's drain never ran — no hard end"
        return the_hard_end(self.exited_at, _looks_and_parks(self.events), cap=DRAIN_CAP_SECONDS)

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


def _premise_stalled(what: str, stall: str) -> str:
    """The line a run whose drain's record shows it held away from its rule leaves (#341)."""

    return (
        f"{what}: THE PREMISE FAILED, NOT THE CASE — the drain's record shows it was held away "
        f"from its own rule: {stall}. A runner that stops the process does that, and the drain "
        f"answers it correctly; this run's wall clock measured the runner, so the case runs "
        f"again"
    )


def _every_attempt_failed_its_premise(
    what: str,
    lines: list[str],
    measured: str = "the cap",
    *,
    stalls: bool = False,
    deadline: bool = False,
) -> str:
    if deadline:
        # #446: 8a's deadline arm, whose attempts can also meet a root still
        # running at the caller's deadline (:func:`_why_the_root_timed_out`).
        return (
            f"{what}: every one of {ATTEMPTS} attempts ended on the idle rule, had a record that "
            f"shows the drain held away from its rule, or met a root still running at the "
            f"caller's deadline, so the case never measured {measured}. Once is a stalled or "
            f"slow runner; every time is the idle rule's input broken (a reader that no longer "
            f"stamps ``last_chunk_at`` on a hand-over ends every drain there), a drain that waits "
            f"before it looks or holds its caller after its last look, a root that starts slower "
            f"than the arm on this host every time (size ``_DEADLINE_ARM_SECONDS`` again from a "
            f"measured distribution of the root's exit), or a host stalling every run — read the "
            f"drains: " + " | ".join(lines)
        )
    if not stalls:
        return (
            f"{what}: every one of {ATTEMPTS} attempts ended on the idle rule, so the case never "
            f"measured {measured}. Once is a stalled runner; every time is the idle rule's input "
            f"broken (a reader that no longer stamps ``last_chunk_at`` on a hand-over ends every "
            f"drain there) or a host stalling every run — read the silences: " + " | ".join(lines)
        )
    return (
        f"{what}: every one of {ATTEMPTS} attempts ended on the idle rule or had a record that "
        f"shows the drain held away from its rule, so the case never measured {measured}. Once "
        f"is a stalled runner; every time is the idle rule's input broken (a reader that no "
        f"longer stamps ``last_chunk_at`` on a hand-over ends every drain there), a drain that "
        f"waits before it looks or holds its caller after its last look, or a host stalling "
        f"every run — read the drains: " + " | ".join(lines)
    )


def _judged_on_its_record(record: _DrainRecord, *, returned: float, what: str) -> str | None:
    """8a's, 8b's and the late-tail case's verdict on the drain's own record (#341).

    The documented hard end, replayed on the drain's looks and parks
    (:meth:`_DrainRecord.hard_end`), on every attempt: it never looked at or
    past it and went on, never parked aiming past it — a proof may end it
    earlier, at its soft cap or on the idle rule, which :class:`_DrainEnd`
    pins. What comes back is why the attempt's wall clock measured the runner
    instead — a look late by more than a grace at or past the soft cap
    (:attr:`_DrainRecord.cap`) or near the hard one, or half a second from the
    drain's last look to the call's return
    (:func:`the_drain_stopped_at_its_hard_end`) — and the case runs again
    (:data:`ATTEMPTS`) rather than judge its ceiling on that attempt.
    """

    return the_drain_stopped_at_its_hard_end(
        record.hard_end(),
        returned=returned,
        exit_seen=None,
        proofless=False,
        what=what,
        soft_cap=record.cap,
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
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
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

    THE PREMISES ARE THE DRAIN'S TO JUDGE SINCE #341 (:class:`_DrainRecord`).
    The bytes and ``elapsed < 1.0`` are also a premise about the runner, the
    reverse of #325's: that the drain ends on its idle rule before the
    helper's ``LATE``, which the helper writes on ITS clock half a second
    after it starts. A process stopped across the root's exit stops the drain
    and the reader with it but not the helper, and comes back to a pipe with
    ``LATE`` already in: #325's lane saw the whole file under the serialised
    stall plugin fail this case 1-2 times in 3-6 runs, once on the bytes
    (``b'EARLY\\nLATE\\n' == b'EARLY\\n'``) and once on the literal
    (``1.0089 < 1.0``), while it passed 9/9 alone. So the drain's record comes
    first: it must have ended on its IDLE rule (proven, a grace of silence —
    the 2.0 s cap regression ends ``cap`` and fails as such, on every
    attempt), and the replayed hard end must hold on it
    (:func:`_judged_on_its_record`). An idle end with ``LATE`` delivered, a
    call slower than the literal, or a record that shows the drain held away
    from its rule is the PREMISE failing, not the case — said, with the
    drain's numbers, and the case runs again (:data:`ATTEMPTS`); the bytes and
    the literal are then asserted on the attempt that measured. Every attempt
    so is a grace that grew, an idle rule armed late, a slow launch path, a
    call held past its drain, or a host stalling every run, and fails. The
    literal stays because nothing on the drain's record sees the launch: a
    1.2 s wait before the spawn passes every check made from the drain's
    arming on (#341 review, measured).
    """

    what = "the exit-path drain returns without the tail"
    measured = "the tail being cut"
    premise_failures: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        marker = tmp_path / f"attempt-{attempt}" / "pids.txt"
        marker.parent.mkdir()
        root = _script(marker.parent, "root_late_tail.py", ROOT_WITH_A_LATE_TAIL)
        registrar = _registrar(marker, strays)
        record = _DrainRecord()
        record.install(monkeypatch)
        chunks: list[bytes] = []
        task = _exec_task(
            _command(root, str(marker), "0.5", str(OUTLIVE)), marker.parent, chunks, timeout=None
        )
        started = time.monotonic()

        try:
            result = await _bounded(task, _bound(0.0) + 5.0, what)
        finally:
            returned = time.monotonic()
            elapsed = returned - started
            pids = registrar.settle()
            end = record.end()
            warnings.warn(
                f"bash exec exit-path drain: elapsed={elapsed:.3f}s "
                f"chunks={b''.join(chunks)!r} end={_label(task, end)} "
                f"attempt={attempt}/{ATTEMPTS} on {sys.platform}",
                stacklevel=1,
            )
        if end.kind != "idle":
            break
        reasons: list[str] = []
        if b"LATE" in b"".join(chunks):
            reasons.append(
                "``LATE`` was delivered: the drain waits a grace of silence from the later of "
                "the exit and the last chunk it was handed, and the helper writes ``LATE`` on "
                "its own clock, so a process held across the root's exit meets it already in "
                "the pipe"
            )
        if elapsed >= 1.0:
            reasons.append(f"the call came back {elapsed:.3f}s after it was made, not inside 1.0s")
        stall = _judged_on_its_record(record, returned=returned, what=what)
        if stall is not None:
            reasons.append(stall)
        if not reasons:
            break
        premise_failures.append(
            f"attempt {attempt}: THE PREMISE FAILED, NOT THE CASE — the drain {end.summary}; "
            f"{' and '.join(reasons)}. That end is the idle rule working; this run measured "
            f"nothing about {measured}, so the case runs again"
        )
        warnings.warn(premise_failures[-1], stacklevel=1)
        # The helper holds the pipe for ``OUTLIVE``; it may not run on through
        # the next attempt.
        for pid in pids or ():
            _reap(pid)
    else:
        drains = " | ".join(premise_failures)
        pytest.fail(
            f"{what}: every one of {ATTEMPTS} attempts ended on the idle rule with ``LATE`` "
            f"delivered, a call slower than 1.0 s, or a drain held away from its rule, so the "
            f"case never measured {measured}. Once is a stalled runner; every time is a grace "
            f"that grew, an idle rule armed from a later instant than the exit, a launch path "
            f"that got slower, a call held past its drain, or a host stalling every run — read "
            f"the drains: {drains}"
        )

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    _root_pid, helper = pids
    assert result.exit_code == 0
    assert end.kind == "idle", (
        f"the exit-path drain {end.summary} — a root whose helper writes nothing for half a "
        f"second ends on the idle rule, a grace after the exit, and nowhere else "
        f"({elapsed:.3f}s from the call)"
    )
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
#: 2.08 s against 1.00 s and on win32 landed within the sanity ceiling the arm
#: had — so until #341 the DISCRIMINATION was the POSIX arm's alone. Since
#: #341 it is read off the drain on both platforms: the soft cap it was handed
#: is below the flat one.
#:
#: THE RULE FOR SIZING IT, AND WHY 2.0 S STAYS (#446). The arm measures only
#: when ``exit < timeout`` (the root exits on its own, so the exit path's drain
#: runs) and ``timeout < exit + DRAIN_CAP_SECONDS`` (the deadline, not the flat
#: cap, is the soft cap the drain is handed). Measured on windows-latest (run
#: 38509857375, 75 rounds per cell, py3.11/3.12/3.13, with no load and with
#: three or four busy loops on the four vCPUs), the root's exit after the call
#: ran from 0.528 s to 1.70 s with no load — 0 of 450 deadline-arm runs timed
#: out — and up to 2.16 s under three loops and 4.25 s under four, where 0-8 of
#: 150 runs per cell timed out (31 of 900). That spread is wider than the 2.0 s
#: window the two conditions leave, so no single value covers it: 2.0 s holds
#: the unloaded maximum with 0.3 s to spare and stays 0.53 s under the unloaded
#: minimum plus the cap, while anything that covered the loaded tail (over
#: 4.25 s) would hand an unloaded root the flat cap and the arm would measure
#: nothing. The loaded tail is the case's premise failing, which the case says
#: and runs again (:func:`_why_the_root_timed_out`), instead of a larger number.
_DEADLINE_ARM_SECONDS = 2.0 if sys.platform == "win32" else 1.0

#: How much EARLIER than the caller's deadline the site's timed wait may give
#: up, on this case's clock, and still be that deadline (#446). A correct site
#: never gives up early: the case reads ``started`` before ``exec`` takes its
#: own ``started_at``, and the worker calls ``proc.wait(timeout)`` later still.
#: But both readings are ``time.monotonic``, which steps 15.625 ms on win32 up
#: to py3.12, and a win32 timer can fire a tick early (ADR-0238's #330
#: amendment says so of asyncio's). Three ticks: a site that gives up earlier
#: than this is caught by :func:`_why_the_root_timed_out`; one that gives up
#: less than this early is not. The same three ticks are the clock grain
#: wherever else the record is placed against an instant: the window an alive
#: look must fall in, how far before the deadline a dead look must be to name
#: an exit the wait did not see, and the announcement's file time against the
#: kill (:func:`_the_timeout_leg_left`). The rule table pins it from both sides:
#: a give-up 0.04 s before the caller's deadline is the deadline, 0.06 s is not.
_EARLY_TIMEOUT_SLACK = 0.05

#: How much LATER than its own deadline the site's timed wait may give up and
#: still have given up AT it (#446 review round 2). The give-up is stamped on
#: the worker after ``proc.wait`` raises, so it carries the worker's resume:
#: 0.000-0.033 s past the wait's deadline in 38 timed-out attempts on
#: windows-latest under load (run 38538998280), and a worker resumed at most
#: 0.067 s late under four busy loops (run 38509857375). Half a second is seven
#: times that. A wait that gives up later does not honour the deadline it was
#: handed, and a wait handed another deadline than the caller's is caught
#: before this is asked (:func:`_why_the_root_timed_out`). The rule table pins
#: it from both sides (0.45 s past is the deadline, 0.55 s is not).
_LATE_TIMEOUT_SLACK = 0.5

#: How long before the deadline the site's timed wait was handed
#: :class:`_TimeoutWitness` looks at the root once more (#446). A root found
#: dead when the wait gave up is either an exit the wait did not see — a defect
#: — or an exit AT the deadline that the wait's own timeout beat: a dead heat,
#: measured twice in 13 misses on windows-latest (run 38509857375; pwsh's
#: kernel exit time, to its ~16 ms grain, 0.003 s before and 0.045 s after the
#: wait's deadline, and the worker resumed 0.020 s and 0.036 s past it). The
#: look tells them apart: alive this long before the deadline is the dead heat.
#: An alive look counts only when it BEGAN no earlier than this plus
#: :data:`_EARLY_TIMEOUT_SLACK` before the deadline (review round 2): an
#: earlier one — a look scheduled from the wrong end of the wait, or run at
#: once — leaves the exit anywhere after it, and the rule fails closed on it.
#: 0.25 s because the worker that resumes after a timed wait ran up to 0.067 s
#: late there, under four busy loops, and a look as late as that is still clear
#: of the deadline; the last quarter second before a deadline is the window the
#: record does not judge, and an exit the wait cannot see is missed on every
#: attempt, whose roots exit well before it (0.53-1.70 s against 2.0 s with no
#: load).
_LOOK_BEFORE_THE_DEADLINE = 0.25

#: How long after the timeout leg's kill went out a root that exits 0 may still
#: be reaped and count as the kill's (#446 review round 2). Returncode 0 alone
#: cannot say the kill missed the root: one already inside its own exit when
#: the kill landed can still report its own status. How long it ran on after
#: the kill had gone out can — a root the kill reached is reaped as soon as the
#: waiting thread wakes, and on windows-latest a wait woke at most 0.215 s after
#: pwsh's kernel exit (run 38509857375, every cell; 0.010-0.029 s at the
#: median). A root still running a quarter second after the kill, that then
#: exits 0 by itself, was never reached.
_A_KILL_TAKES = 0.25

#: How long after the timeout leg's kill went out a process it reached may
#: still look alive (#446 review round 2). The kill itself is a ``SIGKILL`` to
#: the group or a ``TerminateJobObject`` and lands in milliseconds — the reap of
#: a killed root came back 0.003-0.004 s after it on darwin — but termination is
#: the target's to finish, and under load it waits for a CPU like anything
#: else, and so does the thread that sees it: a wait on windows-latest saw
#: pwsh's kernel exit up to 0.215 s late (run 38509857375). Two seconds is ten
#: times that. What is still alive later than this was never reached: the
#: check looks once at the call's end and polls only up to this instant, so a
#: process the kill missed cannot slip through by exiting on its own while the
#: check waits for it.
_A_KILLED_PROCESS_ENDS_WITHIN = 2.0


class _TimeoutWitness:
    """The site's timed ``proc.wait``, the root around its deadline, and what the kill had to end (#446).

    Installed as the site's ``_retained_handle``, which ``exec`` calls once,
    right after the spawn and before anything waits: it records the root's pid
    and ``proc`` — and, on POSIX, the root's process group when the root leads
    its own (``setsid``) — and wraps THAT ``proc``'s ``wait`` for the call.
    Installed over ``ProcessTree.attach`` as well, which ``exec`` calls with the
    retained handle next: on win32 it keeps a handle of its own on the job the
    site put the root in (``DuplicateHandle``; the site closes its own when the
    call ends), so the job can still be asked what is in it after the call.

    Two waits are recorded. The site's first timed wait — the worker's
    ``proc.wait(timeout)``: when it began, the timeout it was handed and, if it
    raised ``TimeoutExpired``, when, with the root's state then
    (:func:`probe_state`, which reaps nothing). And, once that wait has given
    up, the next timed wait on the root — the timeout leg's reap in
    ``_end_the_tree``, which the site makes after its kill has gone out: the
    root's state just before it, when it began (also as wall time, to place the
    announcement's file time against), and when it came back. A thread started
    by :meth:`install` — before the call, so nothing is started between the
    spawn and the job's assignment — looks at the root once more
    :data:`_LOOK_BEFORE_THE_DEADLINE` before the first wait's deadline, unless
    the wait came back first, and records when the look began and ended.

    The wrappers call straight through and re-raise; what they add is time:
    :func:`probe_state` on the worker after the timed wait gives up and on the
    loop before the reap — on macOS each runs ``ps``, a few ms — and on win32 a
    ``DuplicateHandle`` after the job's assignment.

    WHY. A root that has not exited by the caller's deadline is timed out and
    its tree killed, and the call comes back ``exit_code=None, timed_out=True``
    without ever reaching the exit path's drain — correctly. On win32 that root
    is a pwsh start plus a python child, and on a loaded runner it missed the
    2.0 s deadline (job 114898865326, ``elapsed=2.266s ticks=1
    end=no-exit-drain``). Measured on windows-latest (run 38509857375: 13
    misses in 450 instrumented deadline-arm runs under load, 0 in 225 without):
    10 were start-up — the python root had not finished by the deadline, and 8
    of those had not got past ``aelix_ai``'s import; pwsh's creation-to-python
    was 0.27-0.33 s at the median unloaded, 1.0-1.1 s under four busy loops
    and up to 2.94 s, while pwsh's own CPU stayed 0.27-0.94 s in every cell, so
    it WAITED for a CPU rather than did more — 2 were pwsh's teardown after a
    python root that had ended (0.26-0.27 s against a 0.06-0.08 s unloaded
    median), and in 1 pwsh exited at the wait's deadline itself; in 2 of the
    13 the root was already gone when the worker resumed from the timeout.
    Seeing the exit was never the cause: pwsh's kernel exit to the wait's
    return was 0.010-0.029 s at the median and at most 0.215 s. That is the
    case's premise failing, not the product, but ``timed_out=True`` alone cannot
    say so: a site that hands ``proc.wait`` another deadline, whose wait does
    not see the root's exit, or whose kill misses the tree, reports exactly the
    same. This record is what tells them apart
    (:func:`_why_the_root_timed_out`, :func:`_the_timeout_leg_left`).
    """

    def __init__(self) -> None:
        #: ``proc.pid`` of the root the site waits on (pwsh on win32), once spawned.
        self.root: int | None = None
        #: The root's ``Popen``, for the returncode the timeout leg's reap left on it.
        self.proc: Any | None = None
        #: POSIX: the root's process group, when the root leads its own.
        self.group: int | None = None
        #: win32: our own handle on the job the site attached the root to.
        self.job: int | None = None
        #: When the site's first timed wait began, and the timeout it was handed.
        self.waited_at: float | None = None
        self.waited_for: float | None = None
        #: When that wait raised ``TimeoutExpired``; ``None`` if it never did.
        self.timed_out_at: float | None = None
        #: :func:`probe_state` of the root at that instant.
        self.root_then: str | None = None
        #: The look before the deadline: when it began, what it found, and the
        #: instant after it found it — so a root it found alive was alive at
        #: ``look_began``, and one it found dead had exited by ``looked_at``.
        #: ``None`` when the wait came back first, or the look never ran.
        self.look_began: float | None = None
        self.looked_at: float | None = None
        self.looked_saw: str | None = None
        #: The timeout leg's reap: the root's state just before it, when it
        #: began (monotonic and wall), and when it came back.
        self.root_at_reap: str | None = None
        self.reap_began: float | None = None
        self.reap_began_wall: float | None = None
        self.reap_back: float | None = None
        #: Wall time when :meth:`settle` ran, after the call came back.
        self.settled_wall: float | None = None
        self._job_closer: Callable[[], Any] | None = None
        self._armed = threading.Event()
        self._back = threading.Event()
        self._looker = threading.Thread(target=self._look, daemon=True)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        witness = self

        def retained(proc: Any) -> int | None:
            witness.root, witness.proc = proc.pid, proc
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    if os.getpgid(proc.pid) == proc.pid:
                        witness.group = proc.pid
            wait = proc.wait

            def timed(timeout: float | None = None) -> Any:
                first = timeout is not None and witness.waited_at is None
                reap = (
                    not first
                    and timeout is not None
                    and witness.timed_out_at is not None
                    and witness.reap_began is None
                )
                if first:
                    witness.waited_at, witness.waited_for = time.monotonic(), timeout
                    witness._armed.set()
                if reap:
                    witness.root_at_reap = probe_state(proc.pid)
                    witness.reap_began, witness.reap_began_wall = time.monotonic(), time.time()
                try:
                    return wait(timeout)
                except subprocess.TimeoutExpired:
                    if first:
                        witness.timed_out_at = time.monotonic()
                        witness.root_then = probe_state(proc.pid)
                    raise
                finally:
                    if first:
                        witness._back.set()
                    if reap:
                        witness.reap_back = time.monotonic()

            proc.wait = timed
            return _REAL_RETAINED_HANDLE(proc)

        def attach(pid: int, **kwargs: Any) -> Any:
            tree = _REAL_ATTACH(pid, **kwargs)
            if pid == witness.root:
                witness._keep_the_job(tree)
            return tree

        monkeypatch.setattr(bash_module, "_retained_handle", retained)
        monkeypatch.setattr(ProcessTree, "attach", staticmethod(attach))
        self._looker.start()

    def _keep_the_job(self, tree: Any) -> None:
        job = _a_handle_of_our_own_on(getattr(tree, "_job", None))
        if job is not None:
            self.job = job
            self._job_closer = weakref.finalize(self, _close_the_handle, job)

    def _look(self) -> None:
        # Bounded only so a witness nobody settles cannot outlive its case by
        # much; :meth:`settle` sets both events.
        if not self._armed.wait(OUTLIVE):
            return
        waited_at, waited_for, root = self.waited_at, self.waited_for, self.root
        if waited_at is None or waited_for is None or root is None:
            return
        due = waited_at + waited_for - _LOOK_BEFORE_THE_DEADLINE
        if self._back.wait(max(0.0, due - time.monotonic())):
            return
        began = time.monotonic()
        state = probe_state(root)
        self.look_began, self.looked_at, self.looked_saw = began, time.monotonic(), state

    def settle(self) -> None:
        """Stop the look (the call is back) and wait for it — before the record is read."""

        self.settled_wall = time.time()
        self._back.set()
        self._armed.set()
        self._looker.join(DEADLINE)

    def release(self) -> None:
        """Close our handle on the job, if we took one. Idempotent."""

        if self._job_closer is not None:
            self._job_closer()


#: The site's ``_retained_handle`` as imported, before any witness wraps it.
_REAL_RETAINED_HANDLE = bash_module._retained_handle
#: ``ProcessTree.attach`` as imported, before any witness wraps it.
_REAL_ATTACH = ProcessTree.attach

#: ``DUPLICATE_SAME_ACCESS`` and ``JobObjectBasicProcessIdList``.
_DUPLICATE_SAME_ACCESS = 0x2
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3


def _kernel32() -> Any:
    """``kernel32`` with the four signatures the witness's job handle needs (win32 only)."""

    import ctypes

    # ``WinDLL`` exists only on windows; every caller is behind a
    # ``sys.platform`` guard pyright cannot carry into this function.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]
    handle = ctypes.c_void_p
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = handle
    kernel32.DuplicateHandle.argtypes = [
        handle,
        handle,
        handle,
        ctypes.POINTER(handle),
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    kernel32.DuplicateHandle.restype = ctypes.c_int
    kernel32.QueryInformationJobObject.argtypes = [
        handle,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.QueryInformationJobObject.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [handle]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _a_handle_of_our_own_on(job: int | None) -> int | None:
    """win32: a duplicate of the site's job handle, ours to query after the call; else ``None``."""

    if sys.platform != "win32":
        return None
    if job is None:
        return None
    import ctypes

    kernel32 = _kernel32()
    me = kernel32.GetCurrentProcess()
    ours = ctypes.c_void_p()
    if not kernel32.DuplicateHandle(me, job, me, ctypes.byref(ours), 0, 0, _DUPLICATE_SAME_ACCESS):
        return None
    return ours.value


def _close_the_handle(handle: int) -> None:
    if sys.platform == "win32":
        _kernel32().CloseHandle(handle)


def _what_the_job_holds(job: int) -> list[int] | None:
    """win32: the pids still in ``job`` (``JobObjectBasicProcessIdList``); ``None`` if it cannot say."""

    if sys.platform != "win32":
        return None
    import ctypes

    class _Ids(ctypes.Structure):
        _fields_ = [
            ("assigned", ctypes.c_uint32),
            ("listed", ctypes.c_uint32),
            ("ids", ctypes.c_size_t * 64),
        ]

    ids = _Ids()
    if not _kernel32().QueryInformationJobObject(
        job, _JOB_OBJECT_BASIC_PROCESS_ID_LIST, ctypes.byref(ids), ctypes.sizeof(ids), None
    ):
        return None
    return [int(ids.ids[index]) for index in range(ids.listed)]


def _what_the_group_holds(group: int) -> list[int] | None:
    """POSIX: the pids in process group ``group`` that are not zombies; ``None`` if it cannot say.

    ``os.killpg(group, 0)`` first, which signals nothing and answers
    ``ProcessLookupError`` only when the group has no member at all. A member
    is there otherwise, alive or a zombie its parent has not reaped yet, and
    ``ps`` says which; with no ``ps`` this cannot tell, and says so rather than
    read the silence as empty (``tests/process_probe``'s rule).
    """

    if sys.platform == "win32":
        return None
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return []
    except PermissionError:
        pass
    ps = shutil.which("ps")
    if ps is None:
        return None
    try:
        listing = subprocess.run(
            [ps, "-A", "-o", "pid=,pgid=,stat="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    held = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == str(group) and not fields[2].startswith("Z"):
            held.append(int(fields[0]))
    return held


#: What :func:`_why_the_root_timed_out` names, one phrase per verdict, each in
#: exactly one of its answers — the rule table asserts the one it expects and
#: none of the others.
_VERDICTS = (
    "never raised",
    "was handed",
    "before the caller's",
    "past its own deadline",
    "does not see the root's exit",
    "the look never ran",
    "too early to place",
    "too late to place",
)


def _why_the_root_timed_out(
    witness: _TimeoutWitness, *, started: float, timeout: float
) -> str | None:
    """``None`` when a timed-out attempt is the PREMISE failing; otherwise the defect (#446).

    The deadline arm's premise is that the root exits inside the caller's
    deadline. Only the runner breaks it that way, and the record shows it: the
    site's own timed wait was handed the caller's timeout and gave up AT that
    deadline — no earlier than :data:`_EARLY_TIMEOUT_SLACK` before it on the
    case's clock, no later than :data:`_LATE_TIMEOUT_SLACK` past the wait's own
    — on a root that had not exited: still running when the wait gave up, or
    still running at a look that began inside the last
    :data:`_LOOK_BEFORE_THE_DEADLINE` (plus the clock grain) before the wait's
    deadline and gone by the time it gave up (a dead heat: the exit and the
    wait's timeout landed together). Every other way to reach
    ``timed_out=True`` here is the site's, and each one is named by its own
    phrase (:data:`_VERDICTS`):

    - the label without the wait: ``timed_out=True`` while the timed wait never
      raised — "never raised";
    - any other timeout handed to ``proc.wait`` than the caller's — short,
      long, or what is left of it (this case reads the wait's deadline as the
      caller's, so a site that hands it anything else must be read again) —
      "was handed";
    - a wait handed the caller's timeout that still gave up before the
      caller's deadline: counted from an earlier origin than the call, or not
      honouring the deadline it was handed — "before the caller's";
    - a wait that does not honour its deadline: it gave up later than that —
      "past its own deadline";
    - a wait that does not see the root's exit: the root was already dead
      (``zombie`` on POSIX, ``gone`` on win32) at a look that ended more than
      the clock grain before the wait's deadline — "does not see the root's
      exit";
    - and, failing closed, a root dead when the wait gave up that no look
      placed at the deadline: the look never ran — "the look never ran"; it saw
      the root alive but began too early to place the exit inside the last
      quarter second — "too early to place"; or it saw the root dead but ended
      too close to, or past, the deadline to place the exit before it — "too
      late to place".

    The kill that follows is checked separately (:func:`_the_timeout_leg_left`).
    """

    if witness.timed_out_at is None:
        return (
            "the call reported timed_out=True, but the site's timed ``proc.wait`` never raised "
            "``TimeoutExpired`` — the label did not come from the deadline"
        )
    gave_up = witness.timed_out_at - started
    if witness.waited_for != timeout:
        return (
            f"the site's timed wait was handed {witness.waited_for}s, not the caller's {timeout}s "
            f"— it gave up {gave_up:.3f}s after the call on a deadline the caller never set"
        )
    if gave_up < timeout - _EARLY_TIMEOUT_SLACK:
        return (
            f"the site's timed wait gave up {gave_up:.3f}s after the call, before the caller's "
            f"{timeout}s deadline, the very timeout it got — a wait counted from an earlier "
            f"origin than the call, or one that does not honour its deadline"
        )
    waited_at = witness.waited_at if witness.waited_at is not None else started
    deadline = waited_at + timeout
    late = witness.timed_out_at - deadline
    if late > _LATE_TIMEOUT_SLACK:
        return (
            f"the site's timed wait gave up {late:.3f}s past its own deadline ({gave_up:.3f}s "
            f"after the call) — a wait that does not honour the deadline it waited for"
        )
    if witness.root_then == STATE_ALIVE:
        return None
    if witness.look_began is None or witness.looked_at is None or witness.looked_saw is None:
        return (
            f"the root was already {witness.root_then} when the site's timed wait gave up "
            f"{gave_up:.3f}s after the call, and the look never ran — nothing on the record "
            f"places the root's exit at the deadline, so the case fails closed"
        )
    if witness.looked_saw == STATE_ALIVE:
        early = deadline - witness.look_began
        if early <= _LOOK_BEFORE_THE_DEADLINE + _EARLY_TIMEOUT_SLACK:
            return None
        return (
            f"the root was alive at a look that began {early:.3f}s before the wait's deadline and "
            f"{witness.root_then} when the wait gave up {gave_up:.3f}s after the call — a look "
            f"too early to place its exit inside the last {_LOOK_BEFORE_THE_DEADLINE}s, so the "
            f"case fails closed"
        )
    ahead = deadline - witness.looked_at
    if ahead > _EARLY_TIMEOUT_SLACK:
        return (
            f"the root was already {witness.looked_saw} {ahead:.3f}s before the wait's own "
            f"deadline, and the wait still gave up, {gave_up:.3f}s after the call — a wait that "
            f"does not see the root's exit"
        )
    return (
        f"the root was {witness.looked_saw} at a look that ended {-ahead:+.3f}s from the "
        f"wait's deadline, and when the wait gave up {gave_up:.3f}s after the call — a look too "
        f"late to place its exit before the deadline, so the case fails closed"
    )


def _the_timeout_leg_left(
    witness: _TimeoutWitness,
    pids: tuple[int, ...] | None,
    marker: Path,
    strays: list[int],
) -> tuple[list[str], list[str]]:
    """What the timeout leg's kill missed, each by name, and what it was checked for (#446).

    Asked only of an attempt :func:`_why_the_root_timed_out` found to be the
    premise, so the kill went out while something of the tree may still have
    been running. Four checks, each with its own message:

    - THE ROOT EXITED 0 ON ITS OWN after the kill had gone out: still running
      just before the timeout leg's reap, and reaped more than
      :data:`_A_KILL_TAKES` later with returncode 0 — a kill that never
      reached it (one that did leaves ``-SIGKILL`` on POSIX and 1 on win32). A
      root already dead before the reap exited before the kill could reach it,
      which says nothing about the kill.
    - THE TREE WAS ANNOUNCED AFTER THE KILL: the marker the python root writes
      once it has spawned the holder has a file time later than the reap began
      (wall clock, :data:`_EARLY_TIMEOUT_SLACK` of grain) — a process the kill
      missed went on running. A marker that appeared only after the registrar
      stopped polling counts too, and its pids are registered for cleanup.
    - AN ANNOUNCED PROCESS OUTLIVED THE KILL: the root, the python root, and on
      win32 the escaped holder (the job reaches it there; on POSIX it is
      OUTSIDE the group by construction,
      :func:`test_a_holder_outside_the_tree_does_not_hang_the_call`'s
      asymmetry), still alive :data:`_A_KILLED_PROCESS_ENDS_WITHIN` after the
      kill went out.
    - THE TREE STILL HELD A PROCESS: the root's process group on POSIX (when
      the root leads it), its job on win32 (when the site had one), still
      holding a process that is not a zombie that long after the kill —
      whether or not it ever announced, which is the python root the kill
      missed while it was still starting. Those pids are registered for
      cleanup.

    The last two look once when this is asked — after the call came back and
    the registrar settled — and poll only up to that instant past the kill, so
    a process the kill missed cannot pass by exiting on its own while the check
    waits for it. What none of this can see: a root that exited 0 within
    :data:`_A_KILL_TAKES` of the kill (the kill and the exit are one instant to
    the record), a process the kill missed that ended on its own before the
    check looked and never announced after the kill, and a process outside the
    root's group and job that never announced.
    """

    left: list[str] = []
    checked: list[str] = []
    try:
        code = None if witness.proc is None else witness.proc.returncode
        ran_on = (
            None
            if witness.reap_began is None or witness.reap_back is None
            else witness.reap_back - witness.reap_began
        )
        if (
            code == 0
            and witness.root_at_reap == STATE_ALIVE
            and ran_on is not None
            and ran_on > _A_KILL_TAKES
        ):
            left.append(
                f"the root {witness.root} was still running after the kill had gone out and "
                f"exited 0 on its own {ran_on:.3f}s later — a kill that never reached it"
            )
        elif code == 0 and witness.root_at_reap != STATE_ALIVE:
            checked.append("the root had exited 0 on its own before the kill went out")
        elif code == 0:
            checked.append(
                f"the root exited 0 within {_A_KILL_TAKES}s of the kill going out, one instant "
                f"with it to this record"
            )
        elif code is not None:
            checked.append(f"the root ended by the kill (returncode {code})")

        kill_wall = (
            witness.reap_began_wall if witness.reap_began_wall is not None else witness.settled_wall
        )
        try:
            written = marker.stat().st_mtime
        except OSError:
            written = None
        if pids is None and written is not None:
            with contextlib.suppress(OSError, ValueError):
                pids = tuple(int(pid) for pid in marker.read_text(encoding="utf-8").split())
                strays.extend(pids)
        if written is not None and kill_wall is not None:
            after = written - kill_wall
            if after > _EARLY_TIMEOUT_SLACK:
                left.append(
                    f"the python root announced its tree {after:.3f}s after the timeout leg's "
                    f"kill went out — a process the kill missed went on running"
                )
            else:
                checked.append(f"its tree announced {-after:.3f}s before the kill went out")
        else:
            checked.append("no tree announced")

        owed: dict[int, str] = {}
        if witness.root is not None:
            owed[witness.root] = "the root"
        if pids:
            owed.setdefault(pids[0], "the python root")
            if sys.platform == "win32" and len(pids) > 1:
                owed.setdefault(pids[1], "the holder, inside the job")
        if witness.group is not None:
            tree, held_by = "process group", witness.group
        elif witness.job is not None:
            tree, held_by = "job", witness.job
        else:
            tree, held_by = None, None

        def held() -> list[int] | None:
            if held_by is None:
                return []
            if tree == "job":
                return _what_the_job_holds(held_by)
            return _what_the_group_holds(held_by)

        kill_at = witness.reap_began if witness.reap_began is not None else time.monotonic()
        limit = kill_at + _A_KILLED_PROCESS_ENDS_WITHIN
        while True:
            alive = [pid for pid in owed if probe_state(pid) == STATE_ALIVE]
            still = held()
            if (not alive and still == []) or time.monotonic() >= limit:
                break
            time.sleep(0.05)
        looked = time.monotonic() - kill_at
        for pid in alive:
            left.append(f"{pid} ({owed[pid]}) was still alive {looked:.3f}s after the kill")
        if still is None:
            left.append(f"the root's {tree} still answers, and nothing here could list it")
        elif still:
            strays.extend(still)
            left.append(f"the root's {tree} still held {still} {looked:.3f}s after the kill")
        if not alive:
            checked.append(f"dead after it: {', '.join(owed.values())}")
        if still == [] and tree is not None:
            checked.append(f"nothing left in the root's {tree}")
        return left, checked
    finally:
        witness.release()


def _premise_missed_the_deadline(
    what: str,
    witness: _TimeoutWitness,
    *,
    started: float,
    timeout: float,
    announced: bool,
    checked: list[str],
) -> str:
    """The line an attempt whose root had not exited by the deadline leaves (#446)."""

    gave_up = (witness.timed_out_at or started) - started
    if witness.root_then == STATE_ALIVE:
        when = f"still running when the site's wait gave up, {gave_up:.3f}s after the call"
    else:
        waited_at = witness.waited_at if witness.waited_at is not None else started
        before = waited_at + timeout - (witness.look_began or waited_at)
        when = (
            f"still running at a look {before:.3f}s before the wait's deadline and "
            f"{witness.root_then} when the wait gave up {gave_up:.3f}s after the call — a dead "
            f"heat between the root's exit and the wait's timeout"
        )
    stage = (
        "after its python child had announced its tree"
        if announced
        else "before its python child announced its tree — a start-up the deadline came first for"
    )
    return (
        f"{what}: THE PREMISE FAILED, NOT THE CASE — the root had not exited by the caller's "
        f"{timeout}s deadline: {when}, {stage}. The deadline arm needs a root that exits inside "
        f"its deadline, and on win32 that root is a pwsh start plus a python child; the site "
        f"answered as a timeout does (``exit_code=None, timed_out=True``) and its kill was "
        f"checked ({'; '.join(checked)}), and the exit path's drain, which is what this case "
        f"measures, never ran, so the case runs again"
    )


def _the_call_came_back_at_or_past(started: float, timeout: float, returned: float) -> bool:
    """8a's floor as the product's own sum (#446), never as ``timeout <= returned - started``.

    The drain ended on a look at or past ``started_at + timeout`` and the case
    read ``started`` before ``exec`` took ``started_at``, so ``started +
    timeout <= returned`` holds exactly, while the subtraction can land an ulp
    under ``timeout``. On win32 up to py3.12 ``time.monotonic`` steps 15.625 ms,
    so ``started`` and ``started_at`` can be one reading and the look that ends
    the drain can read the soft cap itself; when ``started + timeout`` crosses a
    power of two it rounds down, and the look's reading minus the start is then
    one ulp short: ``assert 2.0 <= 1.9999999999999432`` on windows-latest
    py3.12 (run 38509857375, once in 675 deadline-arm runs).
    :func:`test_the_deadline_arms_floor_holds_on_a_rounding_tie` pins it.
    """

    return started + timeout <= returned


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
    discriminates on POSIX — ``_bound(1.0)`` is 3.5 s POSIX / 8.5 s win32 and
    the mutation lands under it, so that shape would be inert — and since #341
    the soft cap the drain was handed discriminates on both platforms (below).

    The pins are the CAP END and the truncation. The cap end is what a
    regression trips: with ``_wait`` stamping ``state.exited_at`` for the
    ordinary exit instead of ``_root_exited_at``, ``_drain_to_the_end`` takes
    the KILL branch's 1.0 s cap and the call comes back at 1.064 s — against
    the 2.0 s floor until #325, and as a drain that never computed the exit
    path's cap now (below). The ``None`` arm's ceiling folds
    :data:`KILL_DRAIN_SECONDS` in, so it is a sanity bound rather than a
    discriminator (4.5 s POSIX / 9.5 s win32); a second formula for the drains
    that kill nothing is not worth a divergence from this file's one helper.

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
    stay, after the record, as its consequence on the case's own clock.

    THE CEILINGS ARE JUDGED ON AN ATTEMPT WHOSE RECORD SHOWS NO STALL SINCE
    #341 (:func:`_judged_on_its_record`). Both are wall clocks from the call —
    ``elapsed <= _bound(DRAIN_CAP_SECONDS)`` on the ``None`` arm, and
    ``elapsed < DRAIN_CAP_SECONDS`` (POSIX) or a ``timeout + KILL_DRAIN_SECONDS
    + SLACK`` sanity bound (win32) on the deadline arm — and a process stopped
    across the cap, or across the deadline for as long as the deadline is
    short of the cap, comes back to a look the product is right to take late,
    and failed them (0.9 s for 1.2 s: ``elapsed=2.204s``; 1.9 s for 2.7 s:
    ``4.669 <= 4.5``). So every attempt's record is read first: the documented
    hard end, replayed on the drain's own looks and parks, holds (it never
    looked past it and went on, never parked aiming past it); and a look late
    by more than a grace at or past the soft cap or near the hard one, or half
    a second from the drain's last look to the return, is the premise failing
    — said, and run again, like an idle end. The ceilings stay, on the attempt
    that measured. The deadline arm also reads off the drain what its POSIX
    ceiling discriminates, on win32 too: the soft cap it was handed is below
    the flat one — the caller's deadline, not ``DRAIN_CAP_SECONDS`` past the
    exit.

    A ROOT THAT HAD NOT EXITED BY THE DEADLINE IS THE PREMISE FAILING SINCE
    #446, ON THE WITNESS'S RECORD AND NOWHERE ELSE. The deadline arm measures
    the exit path's drain, so it needs a root that exits inside its deadline;
    on win32 that root is a pwsh start plus a python child, and on a loaded
    windows runner it missed the 2.0 s deadline (job 114898865326, and 31 of
    900 loaded runs in run 38509857375 — 0 of 450 unloaded), took the timeout
    leg correctly and failed here at ``assert result.exit_code == 0``. Raising
    the deadline cannot cover that tail (:data:`_DEADLINE_ARM_SECONDS` says
    why), so such an attempt runs again — but only when
    :func:`_why_the_root_timed_out` reads it off the site's own timed wait
    (:class:`_TimeoutWitness`) as a wait handed the caller's timeout that gave
    up AT that deadline, from either side, on a root that had not exited, and
    only when :func:`_the_timeout_leg_left` finds that the timeout leg's kill
    reached the tree: the root did not run on to exit 0 by itself, nothing
    announced its tree after the kill went out, and within
    :data:`_A_KILLED_PROCESS_ENDS_WITHIN` of the kill every announced process
    it owes is dead and the root's process group (POSIX) or job (win32) holds
    nothing that is not a zombie. Each other shape fails the attempt it
    happens on, by its own name; every attempt timing out fails as the
    premise failing every time. :func:`test_8a_runs_again_after_a_root_that_had_not_exited_by_its_deadline`
    and :func:`test_8a_fails_a_timeout_leg_whose_kill_never_reached_the_root`
    drive that branch on every leg. The floor, on the attempt that measured,
    is the product's own sum (:func:`_the_call_came_back_at_or_past`), for the
    same reason as the soft cap.
    """

    await _the_drain_cap_case(tmp_path, strays, monkeypatch, timeout)


async def _the_drain_cap_case(
    tmp_path: Path,
    strays: list[int],
    monkeypatch: pytest.MonkeyPatch,
    timeout: float | None,
    *,
    first_root_holds: float = 0.0,
) -> list[str]:
    """8a's procedure; returns the premise lines the attempts it ran again left.

    ``first_root_holds`` is how long attempt 1's python root sleeps after it
    has announced its tree — ``0.0`` in 8a itself, and past the deadline in the
    rows that drive the deadline premise on every leg (#446 review round 2).
    """

    what = f"a holder that never falls idle (timeout={timeout})"
    premise_failures: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        marker = tmp_path / f"attempt-{attempt}" / "pids.txt"
        marker.parent.mkdir()
        registrar = _registrar(marker, strays)
        record = _DrainRecord()
        record.install(monkeypatch)
        witness = _TimeoutWitness()
        witness.install(monkeypatch)
        chunks: list[bytes] = []
        # ``hold`` is the ROOT's own sleep, so ``hold=0.0`` is what makes the
        # root exit at once and arms the drain immediately; ``tick`` is the
        # HOLDER's, and 50 ms against a 100 ms grace is what keeps the window
        # open — while the reader keeps up (:class:`_DrainRecord`).
        hold = first_root_holds if attempt == 1 else 0.0
        command = _holder_command(marker.parent, marker, tick=0.05, hold=hold)
        task = _exec_task(command, marker.parent, chunks, timeout=timeout)
        started = time.monotonic()

        try:
            result = await _bounded(task, _bound(DRAIN_CAP_SECONDS) + 5.0, what)
        finally:
            returned = time.monotonic()
            elapsed = returned - started
            witness.settle()
            pids = registrar.settle()
            end = record.end()
            warnings.warn(
                f"bash exec drain cap: timeout={timeout} elapsed={elapsed:.3f}s "
                f"ticks={b''.join(chunks).count(b'tick')} end={_label(task, end)} "
                f"attempt={attempt}/{ATTEMPTS} on {sys.platform}",
                stacklevel=1,
            )
        if end.kind == "idle":
            witness.release()
            premise_failures.append(_premise_failed(f"attempt {attempt}", end))
        elif (
            end.kind == "no-exit-drain"
            and timeout is not None
            and result.timed_out
            and result.exit_code is None
        ):
            # #446: the call took the timeout leg — the premise failing only
            # on the witness's record (the root had not exited by the
            # deadline), and only once that leg's kill is found to have reached
            # the tree. Anything else fails here, on this attempt, by name.
            defect = _why_the_root_timed_out(witness, started=started, timeout=timeout)
            checked: list[str] = []
            if defect is None:
                left, checked = _the_timeout_leg_left(witness, pids, marker, strays)
                if left:
                    defect = f"the timeout leg's kill missed: {'; '.join(left)}"
            witness.release()
            if defect is not None:
                pytest.fail(f"{what}, attempt {attempt}: {defect} ({end.summary})")
            premise_failures.append(
                _premise_missed_the_deadline(
                    f"attempt {attempt}",
                    witness,
                    started=started,
                    timeout=timeout,
                    announced=pids is not None,
                    checked=checked,
                )
            )
        elif end.kind != "cap":
            witness.release()
            break
        else:
            witness.release()
            stall = _judged_on_its_record(record, returned=returned, what=what)
            if stall is None:
                break
            premise_failures.append(_premise_stalled(f"attempt {attempt}", stall))
        warnings.warn(premise_failures[-1], stacklevel=1)
        # The escaped holder writes every 0.05 s for ``OUTLIVE`` into a reader
        # this process detached; it may not run on through the next attempt.
        for pid in pids or ():
            _reap(pid)
    else:
        pytest.fail(
            _every_attempt_failed_its_premise(
                what, premise_failures, stalls=True, deadline=timeout is not None
            )
        )

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
        # The drain's own soft cap, on both platforms: with ``timeout <=
        # DRAIN_CAP_SECONDS`` and the start before the exit, an
        # ``_exit_drain_cap`` that honours the deadline answers strictly less
        # than the flat cap, and one that drops it answers the flat cap —
        # compared as the product's own sum, so no subtraction rounds it.
        assert record.cap is not None and record.exited_at is not None
        flat = record.cap >= record.exited_at + DRAIN_CAP_SECONDS
        assert not flat, (
            f"the soft cap the drain was handed is {record.cap - record.exited_at:.3f}s past "
            f"the exit — the flat cap, not the caller's {timeout}s deadline ({end.summary})"
        )
        # POSIX: the deadline (1.0 s) is below the flat cap, so reaching the cap
        # means the term is gone. win32: the deadline (2.0 s) equals the cap
        # measured from a root that exits at ~0.5 s, so the ceiling there is a
        # sanity bound (deadline + one grace + the runner's slack), not the
        # discriminator — see :data:`_DEADLINE_ARM_SECONDS`.
        ceiling = (
            timeout + KILL_DRAIN_SECONDS + SLACK if sys.platform == "win32" else DRAIN_CAP_SECONDS
        )
        # The floor as the product's own sum (#446), never as ``timeout <=
        # elapsed`` — :func:`_the_call_came_back_at_or_past` says why.
        assert _the_call_came_back_at_or_past(started, timeout, returned), (
            f"elapsed={elapsed!r}s — the call came back before the caller's own deadline of "
            f"{timeout}s, which the drain was handed as its soft cap ({end.summary})"
        )
        assert elapsed < ceiling, (
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
    return premise_failures


#: How long past the deadline arm's deadline attempt 1's python root sleeps in
#: the two rows that drive 8a's deadline premise (#446 review round 2): long
#: enough to be running when the timeout leg's kill goes out on any runner, and
#: short enough that a kill which never reaches it sees it exit 0 by itself a
#: second after the deadline, well past :data:`_A_KILL_TAKES`.
_HELD_PAST_THE_DEADLINE = 1.0


async def test_8a_runs_again_after_a_root_that_had_not_exited_by_its_deadline(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """8a's deadline premise, driven on every leg (#446 review round 2).

    The real miss is rare and windows-only (a loaded runner, a pwsh start), so
    without this row the branch, the kill check and the retry ran only when a
    runner happened to be slow. Here attempt 1's python root announces its tree
    and then sleeps past the deadline: the site's timed wait gives up AT the
    deadline on a running root and the timeout leg kills the tree, which the
    case must say as its premise failing — with the kill checked — and then
    measure the cap on attempt 2. A case that trusted no timeout (the branch
    gone) fails here at ``assert result.exit_code == 0``.
    """

    lines = await _the_drain_cap_case(
        tmp_path,
        strays,
        monkeypatch,
        _DEADLINE_ARM_SECONDS,
        first_root_holds=_DEADLINE_ARM_SECONDS + _HELD_PAST_THE_DEADLINE,
    )
    assert lines, "attempt 1's root held past the deadline, and no attempt said so"
    first = lines[0]
    assert first.startswith("attempt 1: THE PREMISE FAILED"), first
    assert "had not exited by the caller's" in first, first
    assert "still running when the site's wait gave up" in first, first
    assert "its kill was checked (" in first, first


async def test_8a_fails_a_timeout_leg_whose_kill_never_reached_the_root(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same premise shape with a timeout leg that kills nothing fails 8a by name (#446 review round 2).

    The site's ``_end_the_tree`` is replaced, for this row only, by its reap
    alone: no tree kill and no belt, so attempt 1's held root runs on and exits
    0 by itself a second after the deadline. The rule alone reads that attempt
    as the premise (the wait gave up AT the deadline on a running root); what
    must fail it is :func:`_the_timeout_leg_left` — "exited 0 on its own", and
    on win32 also the escaped holder, inside the job, outliving it. A case that
    trusted any timeout, or dropped the kill check, passes here instead.
    """

    def ends_nothing(tree: ProcessTree, proc: subprocess.Popen[bytes], *, reap: float) -> None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=reap)

    monkeypatch.setattr(bash_module, "_end_the_tree", ends_nothing)
    with pytest.raises(pytest.fail.Exception) as failed:
        await _the_drain_cap_case(
            tmp_path,
            strays,
            monkeypatch,
            _DEADLINE_ARM_SECONDS,
            first_root_holds=_DEADLINE_ARM_SECONDS + _HELD_PAST_THE_DEADLINE,
        )
    message = str(failed.value)
    assert "attempt 1: the timeout leg's kill missed:" in message, message
    assert "exited 0 on its own" in message, message


@pytest.mark.parametrize(
    ("gave_up", "root_then", "look", "waited_for", "verdict"),
    [
        (0.016, STATE_ALIVE, None, None, None),
        (-0.07, STATE_ALIVE, None, None, None),
        (-0.09, STATE_ALIVE, None, None, "before the caller's"),
        (-0.5, STATE_ALIVE, None, None, "before the caller's"),
        (0.45, STATE_ALIVE, None, None, None),
        (0.55, STATE_ALIVE, None, None, "past its own deadline"),
        (3.0, STATE_ALIVE, None, None, "past its own deadline"),
        (0.016, STATE_ALIVE, None, 5.0, "was handed"),
        (0.016, STATE_ALIVE, None, -0.5, "was handed"),
        (0.016, STATE_ZOMBIE, (-0.25, -0.24, STATE_ALIVE), None, None),
        (0.036, STATE_GONE, (-0.18, -0.17, STATE_ALIVE), None, None),
        (0.016, STATE_ZOMBIE, (-0.29, -0.28, STATE_ALIVE), None, None),
        (0.016, STATE_ZOMBIE, (-0.31, -0.30, STATE_ALIVE), None, "too early to place"),
        (0.016, STATE_ZOMBIE, (-0.6, -0.59, STATE_ALIVE), None, "too early to place"),
        (0.016, STATE_ZOMBIE, (-1.0, -0.99, STATE_ALIVE), None, "too early to place"),
        (0.016, STATE_ZOMBIE, (-0.25, -0.24, STATE_ZOMBIE), None, "does not see the root's exit"),
        (0.016, STATE_GONE, (-0.25, -0.24, STATE_GONE), None, "does not see the root's exit"),
        (0.016, STATE_GONE, (-0.07, -0.06, STATE_GONE), None, "does not see the root's exit"),
        (0.016, STATE_GONE, (-0.05, -0.04, STATE_GONE), None, "too late to place"),
        (0.09, STATE_GONE, (0.06, 0.07, STATE_GONE), None, "too late to place"),
        (0.016, STATE_GONE, None, None, "the look never ran"),
        (0.016, STATE_ZOMBIE, None, None, "the look never ran"),
        (None, None, None, None, "never raised"),
    ],
    ids=[
        "alive-past-the-deadline",
        "alive-0.04s-before-the-callers-deadline",
        "gave-up-0.06s-before-the-callers-deadline",
        "gave-up-half-a-second-early",
        "alive-0.45s-past-the-waits-deadline",
        "gave-up-0.55s-past-the-waits-deadline",
        "gave-up-3s-late",
        "handed-the-reap-grace-on-top",
        "handed-half-a-second-less",
        "a-dead-heat-posix",
        "a-dead-heat-win32-late-look",
        "alive-at-a-look-0.29s-before",
        "alive-at-a-look-0.31s-before",
        "alive-at-a-look-0.6s-before",
        "alive-at-a-look-as-the-wait-began",
        "exited-unseen-posix",
        "exited-unseen-win32",
        "dead-at-a-look-that-ended-0.06s-before",
        "dead-at-a-look-that-ended-0.04s-before",
        "dead-at-a-look-past-the-deadline",
        "gone-and-never-looked-at",
        "a-zombie-never-looked-at",
        "a-label-without-the-wait",
    ],
)
def test_only_a_root_that_had_not_exited_by_the_deadline_is_8as_premise(
    gave_up: float | None,
    root_then: str | None,
    look: tuple[float, float, str] | None,
    waited_for: float | None,
    verdict: str | None,
) -> None:
    """:func:`_why_the_root_timed_out`, row by row, without a process (#446).

    8a retries a timed-out attempt only on this verdict, and on the windows leg
    the real one is rare (a loaded runner, a pwsh start), so the rule is pinned
    here where every row runs on every leg. Every instant is relative to the
    WAIT'S deadline: the site's wait began 0.03 s after the case's start, so
    the caller's deadline is 0.03 s before it. ``look`` is when the look began
    and ended and what it saw; ``waited_for``, when given, is added to the
    caller's timeout to make the one the wait was handed.

    The premise: a wait handed the caller's timeout that gave up AT that
    deadline — 0.04 s before the caller's is inside the clock's grain, 0.06 s
    is not; 0.45 s past the wait's own is inside :data:`_LATE_TIMEOUT_SLACK`,
    0.55 s is not — on a root still running then, or still running at a look
    that began inside the last :data:`_LOOK_BEFORE_THE_DEADLINE` plus the grain
    and dead when the wait gave up (the dead heat windows-latest measured twice
    in 13 misses; the win32 row's look runs 0.07 s late, as late as that
    runner's worker ever resumed). A look 0.29 s before is inside, 0.31 s is
    not. The site's defect, failing 8a at once: a wait handed another timeout,
    one that gave up early or late, a root already dead at a look that ended
    0.06 s before the deadline (0.04 s is the grain, and fails closed), a
    ``timed_out=True`` the wait never raised — and, failing closed, a root dead
    when the wait gave up that no look placed at the deadline: none ran, it saw
    the root alive too early, or it saw it dead too late. Each verdict is its
    own phrase (:data:`_VERDICTS`) and a row asserts the one it expects and
    none of the others, so a rule that dropped any one check, merged two of
    them, or moved a slack past its pair of rows turns a row red.
    """

    started, timeout = 100.0, _DEADLINE_ARM_SECONDS
    witness = _TimeoutWitness()
    witness.waited_at = started + 0.03
    witness.waited_for = timeout + (waited_for or 0.0)
    deadline = witness.waited_at + witness.waited_for
    if gave_up is not None:
        witness.timed_out_at = deadline + gave_up
        witness.root_then = root_then
    if look is not None:
        witness.look_began = deadline + look[0]
        witness.looked_at = deadline + look[1]
        witness.looked_saw = look[2]
    answer = _why_the_root_timed_out(witness, started=started, timeout=timeout)
    if verdict is None:
        assert answer is None, answer
        return
    assert answer is not None, f"the rule took this row as the premise; expected {verdict!r}"
    named = [phrase for phrase in _VERDICTS if phrase in answer]
    assert named == [verdict], answer


def test_the_witness_looks_a_quarter_second_before_the_waits_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """:class:`_TimeoutWitness`'s look runs where the rule expects it (#446 review round 2).

    The rule takes a look that saw the root alive as a dead heat only when it
    began inside the last :data:`_LOOK_BEFORE_THE_DEADLINE` plus the clock
    grain, so the look's own schedule is pinned here, with a stand-in for the
    site's ``proc``: a root that never exits (this process) and a timed wait
    that gives up at its deadline — held open, past it, until the witness has
    looked, so a starved look thread delays the row instead of failing it. A
    look scheduled from the wrong end of the wait (``waited_at + 0.25``) or run
    at once begins a second or three quarters of one before the deadline and
    fails here.
    """

    timeout = 1.0
    witness = _TimeoutWitness()
    witness.install(monkeypatch)

    class _NeverExits:
        pid = os.getpid()

        def wait(self, timeout: float | None = None) -> int:
            assert timeout is not None
            time.sleep(timeout)
            held_until = time.monotonic() + DEADLINE
            while witness.looked_at is None and time.monotonic() < held_until:
                time.sleep(0.01)
            raise subprocess.TimeoutExpired("the root", timeout)

    proc = _NeverExits()
    bash_module._retained_handle(proc)  # what ``exec`` does right after the spawn
    with pytest.raises(subprocess.TimeoutExpired):
        proc.wait(timeout)
    witness.settle()
    assert witness.waited_at is not None and witness.look_began is not None, (
        "the witness never looked before the wait's deadline"
    )
    early = witness.waited_at + timeout - witness.look_began
    assert early <= _LOOK_BEFORE_THE_DEADLINE + _EARLY_TIMEOUT_SLACK, (
        f"the look began {early:.3f}s before the wait's deadline, not inside the last "
        f"{_LOOK_BEFORE_THE_DEADLINE}s"
    )
    assert witness.looked_saw == STATE_ALIVE


def test_the_deadline_arms_floor_holds_on_a_rounding_tie() -> None:
    """8a's floor holds where the subtraction lands an ulp short (#446).

    ``started`` a whole-millisecond reading in ``[510, 512)`` and the drain's
    last look reading the soft cap itself, ``started + timeout``: the sum
    crosses 512 and rounds, so ``returned - started`` is
    ``1.9999999999999432`` — the windows-latest py3.12 failure — while the
    product's own sum holds exactly.
    """

    started, timeout = 510.002, 2.0
    returned = started + timeout
    assert returned - started < timeout, "not a rounding tie: the row measures nothing"
    assert _the_call_came_back_at_or_past(started, timeout, returned)


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

    THE CEILING IS JUDGED ON AN ATTEMPT WHOSE RECORD SHOWS NO STALL SINCE #341
    (:func:`_judged_on_its_record`), as 8a's are: every attempt's record holds
    the documented hard end replayed on the drain's own looks and parks, and a
    look late near the cap or half a second from the drain's last look to the
    return is the premise failing — said, and run again — so a process
    stopped across the cap no longer decides the sanity bound
    ``elapsed <= _bound(DRAIN_CAP_SECONDS)``, which stays on the attempt that
    measured.
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
            returned = time.monotonic()
            elapsed = returned - started
            pids = registrar.settle()
            end = record.end()
            warnings.warn(
                f"bash exec abort in the exit drain: {elapsed:.3f}s end={_label(task, end)} "
                f"attempt={attempt}/{ATTEMPTS} on {sys.platform}",
                stacklevel=1,
            )
        if end.kind == "idle":
            premise_failures.append(_premise_failed(f"attempt {attempt}", end))
        elif end.kind != "cap":
            break
        else:
            stall = _judged_on_its_record(record, returned=returned, what=what)
            if stall is None:
                break
            premise_failures.append(_premise_stalled(f"attempt {attempt}", stall))
        warnings.warn(premise_failures[-1], stacklevel=1)
        # The escaped helper and the tail still write into a reader this
        # process detached; neither may run on through the next attempt.
        for pid in pids or ():
            _reap(pid)
    else:
        pytest.fail(_every_attempt_failed_its_premise(what, premise_failures, stalls=True))

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

    ITS BUDGET, ``DEADLINE * 4`` = 20 s, IS THE COMMAND'S ``timeout`` (#381).
    One ``windows-latest`` py3.12 run hit it (``timed_out=True``) in a full
    suite. Measured there with nothing else running, the case took 10-17 s on
    py3.11/3.12 — not a stall: behind this case's ``_busy`` loop every GIL
    release in the reader thread cost it a 6-10 ms re-take, and the win32
    reader released it twice per ~5 KiB chunk, in its look and in ``read1``.
    The look now keeps the GIL and the read still releases it (``_PipeReader``'s
    class docstring; ``tests/process_tree/test_pipe_reader_held_look.py``), so
    on win32 the case still pays one re-take per chunk. Over three measure
    runs (38485362933, 38493087451, 38496608469: 600 rounds, no failure) the
    call medians with nothing else running ranged 4.61-7.29 s on py3.11,
    6.32-7.68 on py3.12 and 1.23-1.85 on py3.13, and 2.17-2.68 under three
    busy processes; the worst call took 10.76 s (py3.12, its exec 10.42 s),
    and 18 of the 240 no-load py3.11/3.12 execs took over 8 s. The base tree
    took 11.98-14.21 s at the median on py3.11/3.12, 1.6-2.6x as long (1.7x
    paired round by round on one machine). The ``_busy`` loop stays a
    ``sleep(0)`` spin on purpose: its GIL pressure is the condition that
    starved the reader in #260's six ubuntu losses. A loop that spins in 10 ms
    bursts and idles between them took 2.87, 3.15 and 0.64 s at the median
    (run 38485362933): that much of the time is the spin's pressure, and a
    "realistic" loader would buy it back by taking away the pressure this
    case exists to apply. The budget did not move either, so the worst call
    sat about 1.86x under it. That margin is the accepted risk: a rule written
    before the first run asked every median to stay 3x under the budget, at
    6.7 s or less, and four of the six no-load py3.11/3.12 medians did not
    (6.99-7.68 s). The time the exec took is in the warning below, so a
    ``-q`` log says how near it a run came.
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
    started = time.monotonic()
    try:
        result = await _bounded(task, DEADLINE * 4 + 5.0, "delivery completeness")
    finally:
        elapsed = time.monotonic() - started
        stop.set()
        await busy

    warnings.warn(
        f"delivery under a loaded loop: exec {elapsed:.3f}s of {DEADLINE * 4:.0f}s, "
        f"{len(chunks)} chunks, on {sys.platform}",
        stacklevel=1,
    )
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
