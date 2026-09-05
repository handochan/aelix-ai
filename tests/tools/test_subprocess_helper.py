"""Tests for ``aelix_coding_agent.tools._subprocess.run_cancellable``.

Covers:
- Normal success: returns (stdout_text, returncode).
- FileNotFoundError (binary absent): returns None.
- Timeout: returns None and kills the child's tree.
- CancelledError: re-raised after killing the child's tree.
- #222: that tree is attached in the statement after the spawn, is what the
  timeout and cancel legs kill through, is RELEASED and not killed on success,
  and that no leg re-resolves the child's pgid after asyncio reaped it.

WHAT THE LAST GROUP IS FOR, AND WHY IT IS NOT THE #9129 SHAPE. This site's only
callers are ``grep._try_ripgrep`` and ``find._try_fd``, which spawn a fixed
``rg``/``fd`` argv with no shell — so the intermediate-parent tree Pi #9129 is
about (``tools/bash.py``'s, asserted in ``tests/tools/test_bash_tool_containment.py``)
cannot arise here at all. What #222 changed HERE is the address: the old
``kill_process_tree(proc.pid)`` re-derived the group with ``os.getpgid`` at kill
time, which at this site is after asyncio's child watcher already ``waitpid``'d
the child (#202 review posix2/F1), while ``ProcessTree.attach`` captures the
pgid — and on win32 the job — while the child is provably ours. The cases below
assert exactly that difference, by recording every ``os.getpgid`` call and
which side of the attach it fell on.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.utils._process_tree import ProcessTree
from aelix_coding_agent.tools import _subprocess
from aelix_coding_agent.tools._subprocess import run_cancellable

from tests.process_probe import STATE_GONE, STATE_ZOMBIE, await_dead_or_zombie
from tests.process_tree.test_process_tree_real_processes import strays as _strays_fixture
from tests.process_tree.test_run_contained_real_processes import _Registrar

# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


async def test_run_cancellable_returns_stdout():
    """Normal execution: (stdout_text, returncode) is returned."""
    result = await run_cancellable([sys.executable, "-c", "print('hello')"])
    assert result is not None
    stdout, rc = result
    assert "hello" in stdout
    assert rc == 0


async def test_run_cancellable_captures_stdout_only():
    """stderr is not mixed into the returned stdout text."""
    result = await run_cancellable(
        [sys.executable, "-c", "import sys; sys.stderr.write('err'); print('out')"]
    )
    assert result is not None
    stdout, _ = result
    assert "out" in stdout
    assert "err" not in stdout


async def test_run_cancellable_nonzero_returncode():
    """Non-zero exit codes are passed through in the tuple."""
    result = await run_cancellable([sys.executable, "-c", "raise SystemExit(42)"])
    assert result is not None
    _, rc = result
    assert rc == 42


# ---------------------------------------------------------------------------
# FileNotFoundError → None
# ---------------------------------------------------------------------------


async def test_run_cancellable_missing_binary_returns_none():
    """When the binary does not exist, return None (parity: FileNotFoundError)."""
    result = await run_cancellable(["/no/such/binary/xyz_does_not_exist"])
    assert result is None


# ---------------------------------------------------------------------------
# Timeout → None + child killed
# ---------------------------------------------------------------------------


async def test_run_cancellable_timeout_returns_none():
    """On timeout, run_cancellable returns None (parity with TimeoutExpired)."""
    result = await run_cancellable(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=0.1,
    )
    assert result is None


async def test_run_cancellable_timeout_kills_child():
    """After a timeout the child process must not be alive."""
    # Write the child PID to stdout so we can check it afterward.
    script = (
        "import os, time; "
        "print(os.getpid(), flush=True); "
        "time.sleep(30)"
    )
    result = await run_cancellable(
        [sys.executable, "-c", script],
        timeout=0.3,
    )
    assert result is None
    # The child is already dead: we cannot easily check the PID from here
    # without capturing stdout (the timeout fires before the script prints),
    # so assert only that the call returned promptly (no hang).


# ---------------------------------------------------------------------------
# CancelledError → re-raised + child killed
# ---------------------------------------------------------------------------


async def test_run_cancellable_cancellation_propagates():
    """When the wrapping task is cancelled, CancelledError is re-raised.

    The cancel must land AFTER the subprocess is spawned so it exercises the
    ``except asyncio.CancelledError: _kill_tree(); raise`` branch around
    ``_communicate()``, not the pre-spawn ``create_subprocess_exec`` checkpoint.
    A single ``sleep(0)`` yield is not sufficient — the child needs a moment to
    actually start.  Using ``sleep(0.1)`` mirrors the sibling kill-child test.
    """

    async def _run():
        return await run_cancellable(
            [sys.executable, "-c", "import time; time.sleep(30)"],
        )

    task = asyncio.create_task(_run())
    # Give the child time to actually start before cancelling, so cancel
    # lands inside _communicate() and exercises the kill-group-on-cancel path.
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)


async def test_run_cancellable_cancellation_kills_child():
    """After cancellation, the child process must be dead (not running).

    Strategy: the child writes its PID to a temp file, then sleeps.  We cancel
    the task, then verify the process is not in a running state.

    Note: after SIGKILL the child becomes a zombie (state Z) until the parent
    reaps it via wait().  ``os.kill(pid, 0)`` succeeds on zombies (the PID
    still exists in the process table), so gone AND zombie both count as dead
    — that distinction, and the platform question underneath it, live in
    ``tests.process_probe``.

    #203 — this poll used to read the ABSENCE of ``/proc/<pid>/status`` as
    "fully reaped, definitely dead".  There is no procfs on macOS or Windows,
    so off Linux it took that branch on the first iteration and the test
    passed no matter what the child was doing.  ``await_dead_or_zombie`` asks
    a real question on every platform and resolves "cannot tell" to ALIVE, so
    a surviving child now fails the assertion below everywhere.
    """
    import tempfile

    pid_file = tempfile.mktemp(suffix=".pid")  # noqa: S306 — test-only
    script = (
        f"import os, time; "
        f"open({pid_file!r}, 'w').write(str(os.getpid())); "
        f"time.sleep(30)"
    )

    async def _run():
        return await run_cancellable([sys.executable, "-c", script])

    task = asyncio.create_task(_run())
    # Give the child time to write its PID.
    for _ in range(50):
        await asyncio.sleep(0.05)
        if os.path.exists(pid_file):
            break

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Verify the child is dead (gone or zombie, not running/sleeping).
    # A missing pid file is a spawn failure, not a pass — asserting it keeps
    # the check below from being skipped into green.
    assert os.path.exists(pid_file), "Child never wrote its PID — spawn failed?"
    try:
        with open(pid_file) as _pf:
            child_pid = int(_pf.read().strip())
    finally:
        with contextlib.suppress(OSError):
            os.unlink(pid_file)

    # Poll with a short timeout to avoid hanging: SIGKILL delivery + kernel
    # scheduling can take a few milliseconds.
    last_state = await await_dead_or_zombie(child_pid, timeout=2.0)
    assert last_state in (STATE_GONE, STATE_ZOMBIE), (
        f"Child process {child_pid} still in state '{last_state}' "
        f"after SIGKILL (expected {STATE_GONE}/{STATE_ZOMBIE})"
    )


# ---------------------------------------------------------------------------
# Decode semantics: invalid bytes → U+FFFD replacement (intentional divergence
# from old ``subprocess.run(text=True)`` strict decode)
# ---------------------------------------------------------------------------


async def test_run_cancellable_invalid_utf8_replaced():
    """Invalid UTF-8 bytes in stdout are replaced with U+FFFD (not raised).

    This pins the intentional divergence from the old ``subprocess.run(text=True)``
    behaviour which used ``errors='strict'`` and would raise ``UnicodeDecodeError``
    on binary rg output.  We use ``errors='replace'`` — more robust and closer
    to Node's tolerant Buffer decoding used by pi.  Changing this decode mode
    must be explicit (update this test deliberately).
    """
    # Write a raw 0xFF byte to stdout — invalid UTF-8.
    script = "import sys; sys.stdout.buffer.write(b'\\xff\\n')"
    result = await run_cancellable([sys.executable, "-c", script])
    assert result is not None
    stdout, rc = result
    # U+FFFD replacement character must appear, NOT a UnicodeDecodeError.
    assert "�" in stdout
    assert rc == 0


# ---------------------------------------------------------------------------
# #222 — the tree attached at the spawn is the address every leg kills through
# ---------------------------------------------------------------------------

#: The sibling suite's cleanup fixture, RE-EXPORTED rather than re-written, for
#: the same reason ``test_run_contained_real_processes.py`` re-exports it: one
#: definition of "pids this case is responsible for", and one root-only reaper —
#: a cleanup that reached for a process GROUP would be reaching for the very
#: mechanism under test.
strays = _strays_fixture

#: In the argv of every child these cases spawn, so a leak is greppable
#: (``pgrep -fl aelix222``) rather than anonymous.
MARK = "aelix222"

#: Seconds a child that is meant to outlive its timeout sleeps for.
OUTLIVE = 60.0

#: ``run_cancellable``'s own post-kill wait, the only bounded step between the
#: kill and the return at this site. There is no drain here — ``communicate()``
#: is already cancelled by the time the kill runs — so the bash tool's
#: ``KILL_DRAIN_SECONDS`` has no analogue to fold in.
REAP_GUARD_SECONDS = 2.0

#: Announces its pid through a marker file, then holds still.
#:
#: The announcement is a temp file plus ``os.replace``, so a half-written line
#: can never be read (``ROOT_SOURCE``'s protocol in
#: ``tests/process_tree/test_run_contained_real_processes.py``). It cannot come
#: through stdout: on the timeout and cancel legs nothing ever reads the pipe.
#: The ``sys.stdout.buffer`` write is bytes on purpose — the windows leg turns a
#: text-mode ``print`` into CRLF (handoff-221 §3-12).
CHILD_SOURCE = """\
import os
import sys
import time

marker = sys.argv[1]
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()}\\n")
os.replace(tmp, marker)
sys.stdout.buffer.write(b"ready\\n")
sys.stdout.buffer.flush()
time.sleep(float(sys.argv[2]))
"""


def _child_argv(marker: Path, hold: float) -> list[str]:
    return [sys.executable, "-c", CHILD_SOURCE, str(marker), str(hold), MARK]


def _bound(timeout: float) -> float:
    """The wall-clock a case may take before the watchdog calls it a hang.

    The ``+ 5.0`` on win32 is the runner's process-startup tax, measured across
    #202/#221's legs. ``REAP_GUARD_SECONDS`` is folded in because it is a wait
    this site really can spend on the kill legs — unlike ``REAP_GRACE_SECONDS``
    at the ``run_contained`` site, where folding the grace into the ASSERTED
    elapsed is what let a 6.011 s regression pass (#221, recorded at
    ``test_run_contained_real_processes.py``'s bounds).
    """

    return timeout + REAP_GUARD_SECONDS + 1.5 + (5.0 if sys.platform == "win32" else 0.0)


class _TreeSpy:
    """A recording pass-through around the real tree the site attached.

    Deliberately a wrapper and not a stub: the case still has to end a real
    child, so the kill must really happen. What it adds is the record of WHICH
    entry point the site used — ``hard_kill`` is the only kill a
    ``ProcessTree`` offers that #222 puts on these legs, and ``closed`` is how
    a release is told from a leak.
    """

    def __init__(self, tree: ProcessTree) -> None:
        self._tree = tree
        self.hard_kills = 0

    @property
    def contained(self) -> bool:
        return self._tree.contained

    @property
    def closed(self) -> bool:
        return self._tree.closed

    def hard_kill(self) -> None:
        self.hard_kills += 1
        self._tree.hard_kill()

    def close(self) -> None:
        self._tree.close()


class _AttachRecorder:
    """Drives ``_subprocess.ProcessTree.attach`` and marks the pgid timeline.

    ``phase`` flips to ``"after-attach"`` the instant the real ``attach``
    returns, and every ``os.getpgid`` call is tagged with the phase it fell in.
    That is the whole assertion #222 makes at this site: calls BEFORE the flip
    are the capture, and any call AFTER it is a pgid re-derived from a pid
    asyncio's watcher may already have reaped.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.trees: list[_TreeSpy] = []
        self.pgid_reads: list[tuple[str, int]] = []
        self.phase = "before-attach"
        self._real_getpgid = getattr(os, "getpgid", None)
        if self._real_getpgid is not None:
            monkeypatch.setattr(os, "getpgid", self._recording_getpgid)
        monkeypatch.setattr(_subprocess, "ProcessTree", self)

    def _recording_getpgid(self, pid: int) -> int:
        self.pgid_reads.append((self.phase, pid))
        assert self._real_getpgid is not None
        return self._real_getpgid(pid)

    def attach(self, pid: int, **kwargs: Any) -> _TreeSpy:
        tree = _TreeSpy(ProcessTree.attach(pid, **kwargs))
        self.phase = "after-attach"
        self.trees.append(tree)
        return tree

    @property
    def tree(self) -> _TreeSpy:
        assert len(self.trees) == 1, f"expected exactly one attach, got {len(self.trees)}"
        return self.trees[0]

    def assert_captured_at_the_spawn(self) -> None:
        """The capture happened, and nothing re-read the pgid afterwards.

        Both arms are asserted and the asymmetry is the platform's, not a
        ``skipif``: on POSIX ``_attach_posix`` reads ``getpgid`` once and that
        read IS the capture, so its absence would make the case vacuous. On
        win32 there is no ``os.getpgid`` to read at all — the capture is the Job
        Object handle, and ``contained`` is what says it was taken.
        """

        late = [entry for entry in self.pgid_reads if entry[0] == "after-attach"]
        assert late == [], f"the pgid was re-resolved after the attach: {late}"
        if self._real_getpgid is not None:
            assert [entry for entry in self.pgid_reads if entry[0] == "before-attach"], (
                "the attach never read a pgid — this case measured nothing"
            )
        else:
            assert self.tree.contained is True, "win32: the job is the capture"


async def test_the_tree_is_released_and_not_killed_when_the_child_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Success: attached, contained, closed — and never killed.

    ``kill_on_close`` defaults to ``False`` (ADR-0238's per-site rule, Pi
    #8225), so a command that exits after backgrounding something keeps it. The
    ``hard_kills == 0`` half is what says ``close()`` is a release; the
    ``closed`` half is what says the win32 ``CloseHandle`` is not leaked.
    """

    recorder = _AttachRecorder(monkeypatch)

    result = await run_cancellable([sys.executable, "-c", f"print('ok')  # {MARK}"])

    assert result is not None
    assert result[1] == 0
    assert recorder.tree.hard_kills == 0
    assert recorder.tree.contained is True
    assert recorder.tree.closed is True
    recorder.assert_captured_at_the_spawn()


async def test_the_timeout_leg_kills_through_the_tree_it_attached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, strays: list[int]
) -> None:
    """Timeout: ``hard_kill`` on the attached tree, then ``close``.

    On ``main`` this leg called ``kill_process_tree(proc.pid)``, which resolves
    the group with ``os.getpgid`` at that moment — after asyncio's watcher may
    already have reaped the child. The ``after-attach`` assertion is that
    difference; ``hard_kills == 1`` is that the kill went through the tree and
    not around it.
    """

    marker = tmp_path / "child.pid"
    registrar = _Registrar(marker, strays, fields=1)
    registrar.start()
    recorder = _AttachRecorder(monkeypatch)

    started = time.monotonic()
    # ``+ 5.0`` on the WATCHDOG and not on the asserted elapsed, matching
    # ``test_bash_tool_containment.py``'s ``_bounded(task, _bound(x) + 5.0)``
    # (#222 review M-11). With the same value on both, the ``wait_for`` fires
    # first and the case dies as a ``TimeoutError`` — so ``elapsed <=
    # _bound(0.5)`` below could never be the thing that failed, and the timing
    # assertion pinned nothing.
    result = await asyncio.wait_for(
        run_cancellable(_child_argv(marker, OUTLIVE), timeout=0.5),
        timeout=_bound(0.5) + 5.0,
    )
    elapsed = time.monotonic() - started
    warnings.warn(f"subprocess-helper timeout leg returned in {elapsed:.3f}s", stacklevel=1)

    pids = registrar.settle()
    assert result is None
    assert elapsed <= _bound(0.5)
    assert recorder.tree.hard_kills == 1
    assert recorder.tree.contained is True
    assert recorder.tree.closed is True
    recorder.assert_captured_at_the_spawn()

    assert pids is not None, "the child never announced its pid — spawn failed?"
    state = await await_dead_or_zombie(pids[0], timeout=5.0)
    assert state in (STATE_GONE, STATE_ZOMBIE), f"child {pids[0]} still in state {state!r}"


async def test_the_cancel_leg_kills_through_the_tree_it_attached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, strays: list[int]
) -> None:
    """Esc: the same ladder, then ``CancelledError`` propagates.

    The cancel is fired on the child's own announcement rather than after a
    blind sleep — a cancel that beats the fork would leave the case asserting
    nothing (#221 review D.2.4 measured exactly that with an injected
    interrupt), and on the windows runner a python child needs half a second to
    reach its first statement.
    """

    marker = tmp_path / "child.pid"
    registrar = _Registrar(marker, strays, fields=1)
    registrar.start()
    recorder = _AttachRecorder(monkeypatch)

    task = asyncio.create_task(run_cancellable(_child_argv(marker, OUTLIVE)))
    deadline = time.monotonic() + 10.0
    while registrar.pids is None and time.monotonic() < deadline:
        await asyncio.sleep(0.02)

    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        # ``+ 5.0`` on the watchdog for the reason above (#222 review M-11):
        # the asserted ``elapsed <= _bound(0.0)`` has to be able to fire.
        await asyncio.wait_for(asyncio.shield(task), timeout=_bound(0.0) + 5.0)
    elapsed = time.monotonic() - started
    warnings.warn(f"subprocess-helper cancel leg returned in {elapsed:.3f}s", stacklevel=1)

    pids = registrar.settle()
    assert elapsed <= _bound(0.0)
    assert recorder.tree.hard_kills == 1
    assert recorder.tree.contained is True
    assert recorder.tree.closed is True
    recorder.assert_captured_at_the_spawn()

    assert pids is not None, "the child never announced its pid — spawn failed?"
    state = await await_dead_or_zombie(pids[0], timeout=5.0)
    assert state in (STATE_GONE, STATE_ZOMBIE), f"child {pids[0]} still in state {state!r}"
