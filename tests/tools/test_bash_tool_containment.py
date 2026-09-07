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
from pathlib import Path
from typing import Any, TypeVar

import pytest
from aelix_ai.utils._process_tree import (
    DRAIN_CAP_SECONDS,
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
from tests.process_tree.test_process_tree_real_processes import DEADLINE, _await_dead
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


@pytest.mark.parametrize("timeout", [None, 1.0], ids=["no-deadline", "deadline-1s"])
async def test_a_holder_that_never_falls_idle_hits_the_drain_cap(
    tmp_path: Path, strays: list[int], timeout: float | None
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

    The pins are the FLOOR and the truncation. The floor is what a regression
    trips: with ``_wait`` stamping ``state.exited_at`` for the ordinary exit
    instead of ``_root_exited_at``, ``_drain_to_the_end`` takes the KILL
    branch's 1.0 s cap and the call comes back at 1.064 s against this 2.0 s
    floor. The ceiling folds :data:`KILL_DRAIN_SECONDS` in, so it is a sanity
    bound rather than a discriminator (4.5 s POSIX / 9.5 s win32); a second
    formula for the drains that kill nothing is not worth a divergence from
    this file's one helper.

    The truncation is asserted on the holder's OWN bytes because this root
    writes none of its own: the holder emits ~1200 ticks over ``OUTLIVE`` and
    the call must come back having seen a small fraction of them (measured 38
    on the ``None`` arm, 17-18 on the deadline arm).

    RED on ``main``: neither arm terminates there — the drain waits for a
    pipe EOF that is 60 s away — so :func:`_bounded` is what fails (9.501 s,
    176 ticks). A temporary copy whose holder lives 5 s measured 5.100 s and
    94 ticks on ``main`` against 2.067 s and 38 here.
    """

    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays)
    chunks: list[bytes] = []
    # ``hold`` is the ROOT's own sleep, so ``hold=0.0`` is what makes the root
    # exit at once and arms the drain immediately; ``tick`` is the HOLDER's,
    # and 50 ms against a 100 ms grace is what keeps the window open.
    command = _holder_command(tmp_path, marker, tick=0.05, hold=0.0)
    task = _exec_task(command, tmp_path, chunks, timeout=timeout)
    started = time.monotonic()

    try:
        result = await _bounded(
            task, _bound(DRAIN_CAP_SECONDS) + 5.0, "a holder that never falls idle"
        )
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()
        warnings.warn(
            f"bash exec drain cap: timeout={timeout} elapsed={elapsed:.3f}s "
            f"ticks={b''.join(chunks).count(b'tick')} on {sys.platform}",
            stacklevel=1,
        )

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    assert result.exit_code == 0
    assert result.timed_out is False
    if timeout is None:
        assert DRAIN_CAP_SECONDS <= elapsed <= _bound(DRAIN_CAP_SECONDS)
    else:
        assert timeout <= elapsed < DRAIN_CAP_SECONDS, (
            f"elapsed={elapsed:.3f}s reached the flat cap — the caller's own deadline of "
            f"{timeout}s did not bound the exit drain"
        )
    ticks = b"".join(chunks).count(b"tick\n")
    assert 0 < ticks < 200, (
        f"the drain delivered {ticks} ticks — the holder writes ~1200 over its life, so a "
        f"count outside this range means the cap did not cut anything"
    )

# === 8b: an abort that lands in the exit-path drain =========================


async def test_an_abort_during_the_exit_path_drain_keeps_the_helper(
    tmp_path: Path, strays: list[int]
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
    repair and ``assert not task.done()`` is what turns "the abort landed inside
    the drain" from an assumption into an assertion.

    The elapsed pin is the discriminator, not the byte pin. Without it the case
    passes at 1.023 s with ``_wait`` stamping ``state.exited_at`` for the
    ordinary exit instead of ``_root_exited_at`` — which routes the drain into
    the KILL branch's 1.0 s cap — exactly as it passes at 2.025 s under the
    rule (measured). The FLOOR is
    :data:`DRAIN_CAP_SECONDS` and is platform-independent; the ceiling folds
    :data:`KILL_DRAIN_SECONDS` in and is a sanity bound (4.5 s POSIX / 9.5 s
    win32). ``hold`` is 5.0 rather than ``OUTLIVE`` so ``_bounded`` stays a real
    guard rather than a 70 s one: the design's refuter lane measured that at
    ``OUTLIVE`` a dropped cap runs the case ~60 s and then reddens on the helper
    pin with a message that is false about what happened.

    #230 answered whether an abort in this window SHOULD do anything — it kills
    nothing — at ``run_contained``; this case pins that #222 had already put
    this site there, rather than answering it by accident.
    """

    marker = tmp_path / "pids.txt"
    hold = 5.0
    tick = 0.05
    root = _script(
        tmp_path, "root_and_tail.py", ROOT_THAT_BACKGROUNDS_A_HELPER_BEHIND_A_TAIL
    )
    registrar = _registrar(marker, strays, fields=3)
    signal = AbortSignal()
    chunks: list[bytes] = []
    command = _command(root, str(marker), str(OUTLIVE), str(hold), str(tick))
    task = _exec_task(command, tmp_path, chunks, signal=signal, timeout=None)
    started = time.monotonic()

    try:
        await _await_pids(registrar, "an abort during the exit-path drain")
        # The root announces and then exits AT ONCE, so this settle lands the
        # abort inside ``_drain_after_the_exit`` — which the ticking tail holds
        # open to the cap — rather than inside ``_wait``.
        await asyncio.sleep(0.3)
        assert not task.done(), (
            "exec had already returned when the abort fired — the case measured nothing"
        )
        signal.abort()
        result = await _bounded(task, hold + 10.0, "an abort during the exit-path drain")
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()
        warnings.warn(
            f"bash exec abort in the exit drain: {elapsed:.3f}s on {sys.platform}",
            stacklevel=1,
        )

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    _root_pid, helper, _tail = pids
    output = b"".join(chunks)
    assert result.exit_code == 0
    assert result.timed_out is False
    assert output.startswith(b"ROOT\n")
    assert b"TAIL\n" in output
    # ``hold / tick`` is 100 — MORE than the tail writes in its whole life, so
    # the old bound could never fire. The cap admits about
    # ``DRAIN_CAP_SECONDS / tick`` (40 measured); 1.5× of that is under the
    # 100 an uncapped drain would deliver, so this is a real second killer for
    # mutation (b) beside the elapsed floor.
    tails = output.count(b"TAIL\n")
    assert tails <= DRAIN_CAP_SECONDS / tick * 1.5, (
        f"the drain delivered {tails} tail lines — the cap admits about "
        f"{DRAIN_CAP_SECONDS / tick:.0f} and the tail's whole life is {hold / tick:.0f}, "
        f"so a count this high means the cap cut nothing"
    )
    assert DRAIN_CAP_SECONDS <= elapsed <= _bound(DRAIN_CAP_SECONDS)
    assert probe_state(helper) == STATE_ALIVE, (
        f"the abort at 0.3s killed the backgrounded helper {helper} — the tree the command "
        f"exited 0 to leave behind"
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
    ``_drain_past_the_kill`` reddened exactly 7 and excluded it. What keeps the
    case green is the shape of its own command: :data:`CHATTY` backgrounds
    nothing, so the pipe EOFs at the root's own exit and the drain ends on
    ``eof`` rather than on the idle timer or the cap — the FIFO argument above
    still carries the delivery. Deleting the yield reddens nothing here either:
    40 rounds of this case and 5 rounds of the whole file, 0 failures. Where it
    IS load-bearing is the leg this case does not take, a drain that ends on the
    timeout or the cap and resumes with chunk callbacks still queued behind
    it.
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
    """

    marker = tmp_path / "pids.txt"
    root = _script(tmp_path, "root_with_a_chatty_holder.py", ROOT_WITH_AN_ESCAPED_HOLDER)
    holder_script = _script(tmp_path, "holder.py", HOLDER)
    # ``hold=0.0``: the root announces and exits AT ONCE, so the call is on the
    # unbounded exit-path drain when the cancel lands 0.5 s later. The holder
    # writes for 2.0 s and then closes the pipe, which is what fires the EOF
    # callback into the loop ``asyncio.run`` has by then closed.
    command = _command(root, str(marker), holder_script, "2.0", "0.05", "0.0")
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

    crashes: list[Any] = []
    saved_hook = threading.excepthook
    threading.excepthook = crashes.append
    registrar = _registrar(marker, strays)
    chunks: list[bytes] = []

    async def _go() -> None:
        task = _exec_task(command, tmp_path, chunks, timeout=None)
        # On the ANNOUNCEMENT and not on a wall clock, the pattern cases 2/3
        # already use: the root has to have forked before the cancel, and a
        # blind 0.5 s is under pwsh's own 0.5-0.57 s startup on the runner —
        # emulating that startup, this case died on "the root never announced
        # its tree". The settle is for the root's own exit, which follows the
        # announcement by microseconds and is what puts the call on the
        # exit-path drain the cancel has to land in.
        await _await_pids(registrar, "a cancel during the drain")
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await _bounded(task, _bound(0.0) + 5.0, "a cancel during the drain")

    try:
        asyncio.run(_go())
        pids = registrar.settle()
        assert pids is not None, "the root never announced its tree — the case measured nothing"
        # Outlive the holder: its EOF callback is what reaches the closed loop.
        assert _await_dead(pids[1]) != STATE_ALIVE
        time.sleep(0.2)
    finally:
        threading.excepthook = saved_hook

    assert detached, "the reader was never detached — the retention of #221 site-exec-1 is back"
    assert trees and trees[0].closed is True
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
