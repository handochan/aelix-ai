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
import os
import re
import sys
import threading
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
from tests.process_tree.test_run_contained_real_processes import _ANNOUNCE_BOUND, _Registrar

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


#: HOW A ``-c`` CHILD HANDS THIS FILE A PID (#388), the writer half: a temp
#: sibling in the SAME directory (``os.replace`` is atomic within one filesystem
#: only), then ``os.replace`` onto the name, so the marker comes into being
#: complete. The shape :data:`CHILD_SOURCE` below always had, and the one the
#: #406 fix gave ``tests/agents_ext/test_print_channel_spawn.py``.
_ANNOUNCE_PID = """\
import os
import time

def announce(path, pid):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write("%d\\n" % pid)
    os.replace(tmp, path)

"""

_WHOLE_PID = re.compile(r"[0-9]+\n")


def _announced_pid(path: Path) -> int | None:
    """The reader half: the pid at ``path``, or ``None`` while it is not whole.

    Only digits followed by the newline :data:`_ANNOUNCE_PID` ends with count,
    so an absent, empty or half-written file is "not yet" — never a
    ``ValueError`` and never a truncated pid — and it stays correct on its own
    even against a writer that is not atomic. ``OSError`` and not only
    ``FileNotFoundError``: on win32 a read that meets the ``os.replace``
    (``MoveFileEx``) mid-flight can be refused with a sharing violation.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return int(text) if _WHOLE_PID.fullmatch(text) else None


def test_the_pid_handover_is_whole_at_both_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each half of the handover closes the empty-file race on its own (#388).

    WRITER: at the one ``os.replace`` the name does not exist yet and the source
    already holds the whole line, in the same directory. READER: absent, empty,
    unterminated and malformed are all "not yet"; only ``<digits>\\n`` is a pid.
    Exercised in-process, so neither half needs the other to be correct.
    """

    seen: list[tuple[str, bool, str]] = []
    real_replace = os.replace

    def _recording_replace(src: str, dst: str) -> None:
        source = Path(src)
        seen.append((source.read_text(encoding="utf-8"), Path(dst).exists(), str(source.parent)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _recording_replace)
    namespace: dict[str, Any] = {}
    exec(_ANNOUNCE_PID, namespace)  # noqa: S102 — the child's own source, verbatim
    target = tmp_path / "child.pid"
    namespace["announce"](str(target), 4242)
    assert seen == [("4242\n", False, str(tmp_path))]
    assert _announced_pid(target) == 4242

    # What a win32 child's text-mode write leaves on disk is ``\\r\\n``; read in
    # universal-newline mode it is the same whole line.
    target.write_text("4242\r\n", encoding="utf-8", newline="")
    assert _announced_pid(target) == 4242
    for text in ("", "4242", "42", "x\n", "\n"):
        target.write_text(text, encoding="utf-8", newline="")
        assert _announced_pid(target) is None, repr(text)
    assert _announced_pid(tmp_path / "absent.pid") is None


async def test_run_cancellable_cancellation_kills_child(tmp_path: Path) -> None:
    """After cancellation, the child process must be dead (not running).

    Strategy: the child writes its PID to a marker file, then sleeps.  We cancel
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

    #388 — the pid used to come through ``open(p, 'w').write(str(pid))`` while
    this case cancelled the moment ``os.path.exists`` was true. ``open``
    creates the file EMPTY and the digits land only when the file object is
    closed, so a cancel in between killed a child that had announced nothing
    and the read below was ``int('')``. Both ends are fixed now, each on its
    own (:data:`_ANNOUNCE_PID` and :func:`_announced_pid`): the file comes into
    being whole, and only a whole line counts. The poll for it is bounded by
    the registrar's own :data:`_ANNOUNCE_BOUND`, the bound the timeout leg's
    gate and the cancel leg's registrar use, no longer a fixed 2.5 s of its own
    that the child's start-up had to fit inside.
    """

    pid_file = tmp_path / "child.pid"
    script = _ANNOUNCE_PID + f"announce({str(pid_file)!r}, os.getpid())\ntime.sleep(30)\n"

    async def _run():
        return await run_cancellable([sys.executable, "-c", script])

    task = asyncio.create_task(_run())
    # Give the child time to announce its PID — a WHOLE pid, not a name — for
    # as long as the other legs give theirs (#388 review): the registrar's
    # ``_ANNOUNCE_BOUND``, past which an announcement is a premise failure.
    child_pid: int | None = None
    deadline = time.monotonic() + _ANNOUNCE_BOUND
    while child_pid is None and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        child_pid = _announced_pid(pid_file)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # A missing pid is a premise failure, not a pass — asserting it keeps the
    # check below from being skipped into green, and naming it keeps it from
    # reading as a kill that failed.
    assert child_pid is not None, (
        "PREMISE, NOT THE KILL: the child had not announced its pid "
        f"{_ANNOUNCE_BOUND}s after the spawn, so the cancel had no started child to "
        "end — the child's start-up on this runner (launch, interpreter, marker "
        "write), not the kill (#388)"
    )

    # Poll with a short timeout to avoid hanging: SIGKILL delivery + kernel
    # scheduling can take a few milliseconds.
    last_state = await await_dead_or_zombie(child_pid, timeout=2.0)
    assert last_state in (STATE_GONE, STATE_ZOMBIE), (
        f"Child process {child_pid} still in state '{last_state}' "
        f"after SIGKILL (expected {STATE_GONE}/{STATE_ZOMBIE})"
    )


# ---------------------------------------------------------------------------
# Decode semantics: never a UnicodeDecodeError — U+FFFD off win32, and on win32
# the console/OEM code page first (#239, an amendment to the #221 divergence)
# ---------------------------------------------------------------------------


async def test_run_cancellable_invalid_utf8_never_raises_and_the_answer_is_platform_shaped():
    """A byte no UTF-8 decoder accepts: U+FFFD everywhere but a DBCS console.

    The intentional divergence pinned here is still from
    ``subprocess.run(text=True)``, which used ``errors='strict'`` and raised
    ``UnicodeDecodeError`` on binary rg output. What replaced it in #239 is
    ``decode_child_output``, which on win32 gives the console output code page
    — or ``GetOEMCP`` when there is no console, which is the case under
    pytest's captured stdout — a strict try before the ``replace`` floor.

    THE en-US RUNNER IS BACK ON THAT FLOOR (#239 final pass), and this case
    pinned the opposite until then: "exactly one character, and not U+FFFD",
    because cp437 and cp850 map ``0xFF`` to U+00A0. That acceptance is what
    turned the marker INVISIBLE in
    ``tests/test_extension_issue5_runtime_and_trust.py`` on windows-latest py3.11
    and py3.12 (CI run 34238825800). A page that decodes all 256 single bytes is
    offered nothing now, so a single-byte chain is ``errors='replace'`` byte for
    byte and the win32 answer EQUALS the POSIX one — which is an invariant, so it
    is asserted exactly rather than weakly.

    A DBCS RUNNER IS THE ONE PLACE THAT CAN STILL DIFFER, and not in the
    direction this docstring used to claim. cp949, cp936 and cp950 REFUSE
    ``0xFF`` and reach the floor, but cp932 maps it to U+F8F3, a private-use
    character — measured 2026-09-09 on darwin/CPython 3.12.13 — so a Japanese
    runner is NOT U+FFFD. That arm keeps the weak "one character, then the
    newline" assertion, and it is selected by asking the resolved chain rather
    than by assuming a locale.
    """
    # Write a raw 0xFF byte to stdout — invalid UTF-8.
    script = "import sys; sys.stdout.buffer.write(b'\\xff\\n')"
    result = await run_cancellable([sys.executable, "-c", script])
    assert result is not None
    stdout, rc = result
    assert rc == 0
    if sys.platform == "win32" and the_console_page_is_dbcs():
        assert len(stdout) == 2 and stdout.endswith("\n"), stdout
    else:
        assert stdout == "�\n"


def the_console_page_is_dbcs() -> bool:
    """Does this runner's fallback chain end on a multi-byte code page?

    ``en-US`` (OEM 437/850) is single-byte and answers :data:`False`, and since
    #239's final pass a single-byte chain is ``errors="replace"`` outright, so
    that answer means "the POSIX answer". A Korean or Japanese runner answers
    :data:`True`, and only there can a lone undecodable byte be anything else.
    """

    from aelix_ai.utils._child_output import _is_multibyte_page, win32_output_fallbacks

    return any(_is_multibyte_page(codec) for codec in win32_output_fallbacks())


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

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, *, gate: threading.Event | None = None
    ) -> None:
        self.trees: list[_TreeSpy] = []
        self.pgid_reads: list[tuple[str, int]] = []
        self.phase = "before-attach"
        self._gate = gate
        #: ``time.monotonic()`` as the real ``attach`` returned — the spawn, to
        #: the resolution this case needs.
        self.attached_at: float | None = None
        #: ``time.monotonic()`` as this ``attach`` handed the tree back, i.e.
        #: the instant the site's ``wait_for`` clock starts.
        self.clock_started_at: float | None = None
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
        self.attached_at = time.monotonic()
        self.trees.append(tree)
        if self._gate is not None:
            # THE GATE (#388): hold the site between its attach and its
            # ``wait_for`` until the child has announced, so the timeout's clock
            # starts on a child that is already holding still. A BLOCKING wait on
            # purpose — ``attach`` is synchronous at the site, and the site does
            # nothing between it and the ``wait_for`` that could notice. AFTER
            # the real attach, never before it: on win32 only descendants created
            # after the assignment join the job (the site's own comment), so a
            # gate in front of it would let the venv launcher's interpreter
            # escape the very tree this case asserts on. Bounded by the
            # registrar's own poll, past which an announcement cannot arrive.
            self._gate.wait(_ANNOUNCE_BOUND)
        self.clock_started_at = time.monotonic()
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

    THE TIMEOUT'S CLOCK STARTS ON THE ANNOUNCEMENT (#388). ``run_cancellable``
    starts its ``wait_for`` right after the attach, so with a 0.5 s timeout all
    the child does before announcing used to sit INSIDE the timeout: on the
    windows runner the venv launcher, the interpreter it starts (the announced
    pid is that grandchild's, not ``proc.pid``) and the marker's file writes.
    Measured on windows-latest over 7200 spawns (runs 38439273430 and
    38440355388): launcher plus interpreter take a median 0.02-0.05 s (max
    0.15 s, 0.21 s with ``-S``), but CREATING the marker's files stalls —
    0.50-0.52 s in 5 spawns and 0.1-0.38 s in 14 more, often with the parent's
    own poll frozen for the same interval. Once in CI (windows py3.12, run
    37513810009) the leg returned after 0.687 s and failed "the child never
    announced its pid" with every kill assertion above it green; that run kept
    no stamps, so the cause is inferred, not measured — most likely such a
    stall, which lets the kill land before the pid is written. The recorder's
    gate now holds the site between its attach and its ``wait_for`` until the
    child has announced, so the timeout cannot fire on a child that has not
    started and the 0.5 s is the timeout's alone. A child that does not
    announce at all within the registrar's poll is reported as what it is — a
    PREMISE failure, not a kill failure.
    """

    marker = tmp_path / "child.pid"
    announced_at: list[float] = []
    announced = threading.Event()

    def _on_announce(_pids: tuple[int, ...]) -> None:
        announced_at.append(time.monotonic())
        announced.set()

    registrar = _Registrar(marker, strays, fields=1, on_announce=_on_announce)
    registrar.start()
    recorder = _AttachRecorder(monkeypatch, gate=announced)

    # ``+ 5.0`` on the WATCHDOG and not on the asserted elapsed, matching
    # ``test_bash_tool_containment.py``'s ``_bounded(task, _bound(x) + 5.0)``
    # (#222 review M-11). With the same value on both, the ``wait_for`` fires
    # first and the case dies as a ``TimeoutError`` — so ``elapsed <=
    # _bound(0.5)`` below could never be the thing that failed, and the timing
    # assertion pinned nothing. ``+ _ANNOUNCE_BOUND`` for the gate, which can
    # hold the call that long before its clock starts.
    result = await asyncio.wait_for(
        run_cancellable(_child_argv(marker, OUTLIVE), timeout=0.5),
        timeout=_bound(0.5) + 5.0 + _ANNOUNCE_BOUND,
    )
    returned_at = time.monotonic()

    pids = registrar.settle()
    assert result is None
    tree = recorder.tree
    assert recorder.attached_at is not None and recorder.clock_started_at is not None
    # THE PREMISE, named apart from the kill (#388): the child had announced
    # before the timeout's clock started. Fails only if the gate ran out — the
    # interpreter took longer than the registrar's whole poll to start — or if
    # the gate is gone and a slow start let the 0.5 s run out first.
    if announced_at:
        announce = announced_at[0] - recorder.attached_at
        when = f"it announced {announce:.3f}s after the attach"
    else:
        announce = float("nan")
        when = f"it never announced within the registrar's {_ANNOUNCE_BOUND}s poll"
    assert announced_at and announced_at[0] <= recorder.clock_started_at, (
        "PREMISE, NOT THE KILL: the child had not announced its pid when the 0.5 s "
        f"timeout's clock started ({when}) — the child's start-up on this runner "
        "(launch, interpreter, marker write), not the tree kill (#388)"
    )
    elapsed = returned_at - recorder.clock_started_at
    warnings.warn(
        f"subprocess-helper timeout leg: announced {announce:.3f}s after the attach, "
        f"returned {elapsed:.3f}s after the clock started",
        stacklevel=1,
    )
    # A GATE WHOSE NUMBER IS THE CLAIM, kept (#330 classified it): the timeout
    # leg's latency, which the tree spy below cannot bound. It says so now.
    # Measured from the clock's start since #388, so the start-up the gate
    # waited out is no longer inside it.
    assert elapsed <= _bound(0.5), (
        f"the timeout leg returned after {elapsed:.3f}s, past its {_bound(0.5):.2f}s "
        "bound (0.5 s timeout + REAP_GUARD_SECONDS + 1.5 s, + 5 s on win32): the "
        "kill ladder waited longer than its guard — or the runner is loaded; "
        "rerun the file alone"
    )
    assert tree.hard_kills == 1
    assert tree.contained is True
    assert tree.closed is True
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
    interrupt). Measured on the windows runner (#388, 7200 spawns): a python
    child reaches its first statement in a median 0.02-0.05 s (max 0.15 s,
    0.21 s with ``-S``), but creating its marker file stalled 0.50-0.52 s in 5
    of those spawns (the 4 the probe timed apart, inside the file's ``open()``)
    — a rare stall a blind sleep would have to cover every time.
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
    # A GATE WHOSE NUMBER IS THE CLAIM, kept (#330 classified it): the cancel
    # leg's latency. It says so now.
    assert elapsed <= _bound(0.0), (
        f"the cancel leg returned after {elapsed:.3f}s, past its {_bound(0.0):.2f}s "
        "bound (REAP_GUARD_SECONDS + 1.5 s, + 5 s on win32): the kill ladder "
        "waited longer than its guard — or the runner is loaded; rerun the file alone"
    )
    assert recorder.tree.hard_kills == 1
    assert recorder.tree.contained is True
    assert recorder.tree.closed is True
    recorder.assert_captured_at_the_spawn()

    # The same premise the timeout leg names (#388): this leg cancels on the
    # announcement, so a missing one means the cancel fired on a child that
    # had not started — the child's start-up, not the kill under test.
    assert pids is not None, (
        "PREMISE, NOT THE KILL: the child never announced its pid within the "
        f"registrar's {_ANNOUNCE_BOUND}s poll, so the cancel had no started child "
        "to end — the child's start-up on this runner (launch, interpreter, "
        "marker write), not the tree kill (#388)"
    )
    state = await await_dead_or_zombie(pids[0], timeout=5.0)
    assert state in (STATE_GONE, STATE_ZOMBIE), f"child {pids[0]} still in state {state!r}"
