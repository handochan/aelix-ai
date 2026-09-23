"""#260/#261: the bash tool's drain ends on a proof from the pipe, not on the clock.

THE DEFECT. ``exec``'s drain after the root's exit ended on EOF, on a dead
reader, or on the wall clock — the 0.1 s idle grace armed AT THE EXIT on the
``proc.wait`` worker, or the cap. It never asked the pipe. So a reader thread
starved across the exit — no GIL, no CPU — met a grace the drain had never
waited for and found already spent: the drain broke at once, and the
``detach()`` / ``delivering = False`` after it dropped whatever the reader had
not handed on yet. That is the command's OWN output, written before it exited,
returned with ``exit_code=0`` and nothing said. ``truncate_tail`` keeps the
tail, so the model loses exactly what it reads: the failing assertion, the
error summary. CI lost 7,168-57,344 B of
``test_bash_tool_containment.py::test_every_byte_is_delivered_under_a_loaded_loop``
in the six ubuntu runs recorded on #260 and #261.

THE SEAMS. Each case holds the reader thread at one point, from the start of
the run until :data:`HOLD` after the root's own exit (a ``Popen.wait`` spy
opens the gate), which is three graces: the old drain's idle rule always fires
inside the hold, so before #260 every native case here came back with none of
the command's 1000 bytes — a red that does not depend on timing luck. (The
pinned residual below holds it until ``exec`` has returned instead.)

* ``late`` — before the reader's first read: the bytes are still IN THE PIPE.
* ``chunk`` — inside ``on_chunk``: read, not yet posted to the loop.
* ``preread`` — at the first read that would take the command's bytes, before
  it takes them (the blocking branch has already taken its look by then).
* ``inread`` — INSIDE the read, after the bytes have left the pipe.

Each runs with no helper (the pipe reaches EOF: ``eof``) and with a silent
helper that keeps the pipe open (``holder``), where no EOF ever comes and the
drain has to end on the pipe itself — #232's shape, whose return must stay near
one grace past the hold and never fall back to the 2.0 s cap.

TWO BRANCHES, BOTH ASSERTED — no skipif. ``native`` is the reader this host
runs: on POSIX it reads its own non-blocking fd and waits in ``poll``, so its
phase is odd before every read and :meth:`_PipeReader.drained` is exact.
``win32`` forces the branch Windows runs (``platform="win32"``): a blocking
``read1`` preceded by the reader's own look, because the asking thread must not
query a pipe another thread is blocked reading. On ``windows-latest`` the two
are the same branch. The blocking branch has a stated residual — bytes that
arrive after the reader's last empty look and are still in the pipe or inside
its read when the drain asks, with the reader starved for a whole grace — and
``preread``/``inread`` put the hold in exactly that spot, so there the loss is
PINNED (``b""``, silently): closing the residual later flips a visible pin
instead of passing unnoticed. Everywhere else every byte arrives — including
when the blocking reader's look SAW the bytes before it was held, since a look
that saw bytes proves nothing.

The rest pin the drain's other ends and its loop: the final yield, the hard
cap (reported), the kill leg's cap, the second proof on a pipe that never
empties, the parked grace between refused proofs, where the notice goes, and —
since round 4 — a loop that reaches the drain late: the pin already taken at
the exit stamp, and one more grace for a late look at the hard cap. Since round
6, a kill whose ladder ends the root before the rest of its tree: the pin the
root's reap asked for proves nothing once the kill stamp follows it.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import pytest
from aelix_ai.tools import ToolExecutionContext
from aelix_ai.utils import _process_tree
from aelix_ai.utils._process_tree import (
    DRAIN_CAP_SECONDS,
    EXIT_DRAIN_SECONDS,
    KILL_DRAIN_SECONDS,
    _PipeReader,
)
from aelix_coding_agent.tools import bash as bash_module
from aelix_coding_agent.tools.bash import (
    BashToolDetails,
    ExecExitResult,
    create_bash_tool,
    create_local_bash_operations,
)

from tests.process_tree.test_run_contained_real_processes import _registrar
from tests.process_tree.test_the_drain_asks_the_pipe import (
    AWAY_AGAIN,
    BOUND,
    FULL_PIPE_ROOT_TEMPLATE,
    HOLD,
    LATE,
    PAYLOAD,
    ROOT_TEMPLATE,
    DrainLog,
    Gate,
    _is_taskkill,
    held_reader,
    hold_the_fd_read,
    slow_the_reader,
    takes_the_blocking_branch,
    the_deadline_ended_it_on_the_second_proof,
)
from tests.tools.test_bash_tool_containment import (
    _DEADLINE_ARM_SECONDS,
    MARK,
    _await_pids,
    _bounded,
    _command,
    _exec_task,
    _script,
)
from tests.tools.test_bash_tool_containment import (
    strays as _strays_fixture,
)

#: The containment file's cleanup fixture, re-exported the way that file
#: re-exports its own sibling's: one definition of "pids this case owns".
strays = _strays_fixture


def _root_command(
    tmp_path: Path, marker: Path, *, holder: bool, looked: Path | None, code: int
) -> str:
    """The seam root (``ROOT_TEMPLATE``, this file's own :data:`MARK`) as a shell command."""

    root = _script(tmp_path, "root_260.py", ROOT_TEMPLATE.replace("@MARK@", MARK))
    shape = "holder" if holder else "none"
    return _command(root, str(marker), shape, str(looked or "-"), str(code))


@pytest.mark.parametrize("branch", ["native", "win32"])
@pytest.mark.parametrize("holder", [False, True], ids=["eof", "holder"])
@pytest.mark.parametrize("where", ["late", "chunk", "preread", "inread"])
async def test_the_drain_waits_for_bytes_the_reader_has_not_handed_on(
    tmp_path: Path,
    strays: list[int],
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    holder: bool,
    branch: str,
) -> None:
    """The command's own 1000 bytes arrive though the reader was held past the grace.

    RED BEFORE #260 on every ``native`` id, measured on the base tree
    (``02f98560``): ``0 != 1000`` bytes, ``exit_code=0``, the call back 0.1 s
    after the exit — the drain ended on its idle rule with the reader still
    held. The ``win32`` ids cannot run there at all: the branch they force is
    part of the fix.

    GREEN NOW because the idle rule ends the drain only with a proof, and a
    held reader offers none: its phase is odd (``chunk``, POSIX ``preread`` and
    ``inread``), or the pipe is not empty (``late``), or its only look is
    missing or not empty. The ``holder`` ids also pin how the drain ends once
    the reader is free: on the pipe (:meth:`_PipeReader.drained` — the reader is
    parked over an empty pipe) about one grace after the hold, not at the 2.0 s
    cap, which is #232's price kept.

    THE PINNED RESIDUAL: the blocking branch's ``preread`` and ``inread``. Its
    reader looked at an empty pipe and was then held — before its read took
    the bytes, or inside it — so its word, "even, and my look saw 0", is stale,
    and nothing on that branch may ask the pipe instead. The drain believes it,
    ends one grace after the exit, and the bytes are lost with no signal: the
    reader was starved for a whole grace right after an empty look, which is
    the residual the class docstring states. It needs an overlapped read end
    to close; when it closes, these ids flip.

    DETERMINISTIC SINCE ROUND 4 (the cross-review's S1). The residual ends
    WITHOUT the reader, so its reader is let go only after ``exec`` returns
    (:class:`Gate`, ``hold=None``): on the 0.3 s timer the other ids need, a
    runner that stopped the process across the drain's verdict let the reader
    deliver first, and these ids failed as if the residual had closed. Were
    it to close, the drain could prove nothing while the reader is held and
    would end at the hard cap, flagged — which is what flips them now. And the
    root writes only once the reader has looked (:data:`ROOT_TEMPLATE`'s
    ``looked``), not after a 0.2 s sleep a starved reader could outlast.
    """

    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays) if holder else None
    blocking = takes_the_blocking_branch(branch)
    residual = blocking and where in ("preread", "inread")
    gate = Gate(None if residual else HOLD)
    gate.arm_from_the_exit(monkeypatch)
    seen: list[_PipeReader] = []
    fds: set[int] = set()
    platform = "win32" if branch == "win32" else None
    looked = tmp_path / "looked" if where in ("preread", "inread") else None
    monkeypatch.setattr(
        bash_module,
        "_PipeReader",
        held_reader(where, gate, platform=platform, seen=seen, fds=fds, looked=looked),
    )
    if where in ("preread", "inread"):
        hold_the_fd_read(monkeypatch, gate, where, fds)
    chunks: list[bytes] = []
    command = _root_command(tmp_path, marker, holder=holder, looked=looked, code=0)
    task = _exec_task(command, tmp_path, chunks, timeout=30.0)
    started = time.monotonic()
    try:
        result = await _bounded(task, BOUND + 5.0, f"seam {where}")
    finally:
        returned = time.monotonic()
        gate.event.set()
        if registrar is not None:
            registrar.settle()
        after_exit = returned - (gate.exited_at or started)
        warnings.warn(
            f"#260 seam={where} holder={holder} branch={branch}: exec returned "
            f"{after_exit:.3f}s after the exit, {len(b''.join(chunks))}/{len(PAYLOAD)} B "
            f"on {sys.platform}",
            stacklevel=1,
        )

    assert seen, "the held reader was never built — the case measured nothing"
    assert gate.exited_at is not None, "the root's exit was never seen — the case measured nothing"
    assert result.exit_code == 0
    assert result.timed_out is False
    delivered = b"".join(chunks)
    if residual:
        assert delivered == b"" and result.output_unconfirmed is False, (
            f"the blocking branch's residual closed — the drain returned {after_exit:.3f}s after "
            f"the exit with {len(delivered)} B, output_unconfirmed={result.output_unconfirmed}: "
            f"the reader's stale empty look is no longer trusted, or the pipe is being asked "
            f"another way; update this pin and the ADR-0238 #260 amendment's win32 paragraph"
        )
    else:
        assert delivered == PAYLOAD, (
            f"the drain ended {after_exit:.3f}s after the exit with "
            f"{len(delivered)}/{len(PAYLOAD)} bytes of the command's own output"
        )
        if holder:
            # The hold is 0.3 s; the reader then posts, parks over an empty
            # pipe, and the next idle end has its proof. The ceiling is far
            # under the 2.0 s cap that a drain unable to prove the parked state
            # falls back to.
            assert after_exit < 1.0, (
                f"returned {after_exit:.3f}s after the exit — a parked reader over an empty "
                f"pipe did not end the drain, which is #232's return gone back to the cap"
            )
    # Last, so a tree without these fails on the bytes first. The reader took
    # the branch it was asked for; and every end here had a proof — or, on the
    # pinned residual, believed it had one.
    assert (seen[0]._poll_fd is None) is blocking
    assert result.output_unconfirmed is False


async def test_a_blocking_look_that_saw_the_bytes_does_not_end_the_drain(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forced-win32 branch, end to end: a look that SAW the command's bytes is no proof.

    The residual ids above are one order — the reader looked at an EMPTY pipe,
    then the bytes came, then it starved. This is the other: the reader waits
    until the command's 1000 bytes are all in the pipe, looks (1000 pending,
    holding nothing: an EVEN mark) and is held at the ``read1`` that would take
    them until :data:`HOLD` after the exit. "Drained" there is "even, and that
    look saw 0"; this look saw 1000, so the drain has no proof and waits, and
    every byte arrives. With the clause's 0 half gone (``mark[3] >= 0``) the
    drain believes the look and drops all 1000 bytes one grace after the exit,
    ``exit_code=0`` and nothing said — an edit under which every case these two
    files had before this one stayed green (round-2 review). The root
    backgrounds a silent holder so the pipe is never broken under a look (on
    Windows a look at a broken pipe fails).
    """

    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays)
    gate = Gate(HOLD)
    gate.arm_from_the_exit(monkeypatch)
    seen: list[_PipeReader] = []
    ready: list[int] = []
    held = held_reader("preread", gate, platform="win32", seen=seen, fds=set())

    class LooksAtTheBytesFirst(held):
        def run(self) -> None:
            # Until the pipe holds the whole payload, asked on THIS thread
            # before it reads anything — so the look ``run`` takes next sees
            # all of it. Nothing else reads this pipe, so it can only grow.
            probe = self._probe
            deadline = time.monotonic() + BOUND
            while probe is not None and time.monotonic() < deadline:
                try:
                    pending = probe()
                except (OSError, ValueError):
                    break
                if pending >= len(PAYLOAD):
                    ready.append(pending)
                    break
                time.sleep(0.001)
            super().run()

    monkeypatch.setattr(bash_module, "_PipeReader", LooksAtTheBytesFirst)
    chunks: list[bytes] = []
    command = _root_command(tmp_path, marker, holder=True, looked=None, code=0)
    task = _exec_task(command, tmp_path, chunks, timeout=30.0)
    started = time.monotonic()
    try:
        result = await _bounded(task, BOUND + 5.0, "a blocking look that saw the bytes")
    finally:
        returned = time.monotonic()
        gate.event.set()
        registrar.settle()
        after_exit = returned - (gate.exited_at or started)
        warnings.warn(
            f"#260 a look that saw the bytes: exec returned {after_exit:.3f}s after the exit, "
            f"{len(b''.join(chunks))}/{len(PAYLOAD)} B, the pipe held {ready} B before the "
            f"reader's first look, on {sys.platform}",
            stacklevel=1,
        )

    assert seen, "the held reader was never built — the case measured nothing"
    assert gate.exited_at is not None, "the root's exit was never seen — the case measured nothing"
    assert ready == [len(PAYLOAD)], "the reader never saw the whole payload in the pipe"
    assert seen[0]._poll_fd is None, "the forced-win32 reader did not take the blocking branch"
    assert result.exit_code == 0
    delivered = b"".join(chunks)
    assert delivered == PAYLOAD, (
        f"the drain ended {after_exit:.3f}s after the exit with {len(delivered)}/{len(PAYLOAD)} "
        f"bytes — it took a look that SAW them for a proof that the pipe was empty"
    )
    assert after_exit < 1.0, (
        f"returned {after_exit:.3f}s after the exit — the parked reader over an empty pipe did "
        f"not end the drain once it had the bytes"
    )
    assert result.output_unconfirmed is False


class _HeldClock:
    """``time`` for ``tools/bash.py`` and ``_process_tree``, able to hold one reading.

    Held, every reading is the held value: one tick of a coarse clock, lasting
    as long as the case needs. Released, it is this host's clock again, which
    is already later than the held value, so it never goes back. Anything else
    ``time`` offers passes through, and asyncio keeps its own clock.
    """

    def __init__(self) -> None:
        self.held: float | None = None

    def monotonic(self) -> float:
        held = self.held
        return time.monotonic() if held is None else held

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


#: What the tie root writes before its reader's look.
FIRST = b"first\n"

#: Writes :data:`FIRST`, waits until the file ``argv[1]`` exists — the reader
#: creates it once it has looked at the empty pipe after that line — then writes
#: ``PAYLOAD``, waits until the file ``argv[2]`` exists — the reader creates it
#: once it holds the last of those bytes inside their hand-over — and exits at
#: once.
TIE_ROOT = """\
import os
import sys
import time

looked, holding = sys.argv[1], sys.argv[2]
out = sys.stdout.buffer
out.write(b"first\\n")
out.flush()
deadline = time.monotonic() + 15.0
while not os.path.exists(looked) and time.monotonic() < deadline:
    time.sleep(0.001)
out.write(b"".join(b"line %02d " % i + b"." * 41 + b"\\n" for i in range(20)))
out.flush()
while not os.path.exists(holding) and time.monotonic() < deadline:
    time.sleep(0.001)
os._exit(0)
"""


async def test_a_look_dated_in_the_exits_own_tick_does_not_end_the_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a coarse clock the look before a command's last write is still no proof.

    CI run 35952321924 caught the unit form of this on windows-latest, whose
    ``time.monotonic()`` under CPython 3.11 and 3.12 gives one reading to a
    whole tick (~15.6 ms, per CPython's 3.13 What's New). This is what it cost
    end to end, made deterministic. The root writes a first line; the reader
    hands it on, LOOKS at the empty pipe and parks; only then does the root
    write its last 1000 B, and a held clock dates that look and the exit stamp
    in one tick. The reader takes the 1000 B and is held inside their
    hand-over — starved — and the root exits only once it is. With ``>=`` the
    drain's pin took that look: 6 B pinned, caught up at once, so the idle rule
    ended the drain a grace after the exit with the command's last 1000 B still
    in the reader's hands, ``exit_code 0``, nothing said — 6 of 1006 B. A look
    dated AT the exit may have come before it, and this one did. With ``>`` it
    pins nothing, the drain's first ask is refused, it waits for the reader,
    and all 1006 B arrive.

    The forced-win32 branch, the only one that looks before a pin is asked for.
    The clock is held from that look until the pin is first asked for, in
    ``tools/bash.py`` (the exit stamp) and ``_process_tree`` (the look's date).
    Since round 4 that first ask comes from the exit stamp itself, on the
    ``proc.wait`` worker, and the drain asks again; the reader, held inside the
    hand-over, looks at nothing in between, so both asks see the same tie.

    NO TIMER DECIDES IT (round 5). The reader is let go by the drain's own
    first ask for a proof, the verdict taken while it held the bytes — and the
    root exits only once the reader holds them, so that ask always finds them
    in its hands. Until round 5 a timer freed the reader :data:`HOLD` after the
    exit, and the guard below needed the drain's repeat ``pin_after`` to beat
    it: a runner that stopped the whole process right after the exit let the
    timer fire first, the reader looked again, and the case measured nothing
    ("not one tick").
    """

    clock = _HeldClock()
    monkeypatch.setattr(bash_module, "time", clock)
    monkeypatch.setattr(_process_tree, "time", clock)
    # Opened by the drain's first ask, never by a clock.
    gate = threading.Event()
    looked = tmp_path / "looked"
    holding = tmp_path / "holding"
    at_the_pin: list[tuple[float, float]] = []
    #: Every ``proven()`` answer the drain got: (answer, handed_on at it).
    asks: list[tuple[bool, int]] = []
    seen: list[_PipeReader] = []

    class LooksBeforeTheLastWrite(_PipeReader):
        def __init__(self, stream: Any, state: Any, **kwargs: Any) -> None:
            on_chunk = kwargs["on_chunk"]

            def held(chunk: bytes) -> None:
                if b"line 19" in chunk:
                    holding.write_text("holding", encoding="utf-8")
                    gate.wait(BOUND)
                on_chunk(chunk)

            kwargs["on_chunk"] = held
            kwargs["platform"] = "win32"
            super().__init__(stream, state, **kwargs)
            seen.append(self)

        def _look(self, *, always: bool) -> None:
            super()._look(always=always)
            if self.handed_on == len(FIRST) and self._mark[3] == 0 and not looked.exists():
                clock.held = self._mark[1]
                looked.write_text("looked", encoding="utf-8")

        def pin_after(self, instant: float) -> None:
            at_the_pin.append((self._mark[1], instant))
            super().pin_after(instant)
            clock.held = None

        def proven(self) -> bool:
            answer = super().proven()
            asks.append((answer, self.handed_on))
            gate.set()
            return answer

    monkeypatch.setattr(bash_module, "_PipeReader", LooksBeforeTheLastWrite)
    chunks: list[bytes] = []
    root = _script(tmp_path, "tie_root.py", TIE_ROOT)
    task = _exec_task(_command(root, str(looked), str(holding)), tmp_path, chunks, timeout=30.0)
    try:
        result = await _bounded(task, BOUND + 5.0, "a look in the exit's own tick")
    finally:
        clock.held = None
        gate.set()
    delivered = b"".join(chunks)
    warnings.warn(
        f"#260 a look in the exit's own tick: {len(delivered)}/{len(FIRST) + len(PAYLOAD)} B, "
        f"(latest look, pin instant) {at_the_pin}, asks (answer, handed_on) {asks} on "
        f"{sys.platform}",
        stacklevel=1,
    )

    assert seen, "the reader was never built — the case measured nothing"
    assert looked.exists(), "the reader never looked at the empty pipe after the first line"
    assert holding.exists(), "the reader never held the command's last bytes"
    assert at_the_pin and all(look == instant for look, instant in at_the_pin), (
        f"the look and the exit stamp were not one tick {at_the_pin} — the case measured nothing"
    )
    assert asks, "the drain never asked for a proof — it ended on the clock alone"
    first_answer, handed_on_at_it = asks[0]
    assert handed_on_at_it < len(FIRST) + len(PAYLOAD), (
        "the reader had handed everything on before the drain's first ask — the case measured "
        "nothing"
    )
    assert first_answer is False, (
        f"the drain's first ask, with the command's last bytes in the reader's hands "
        f"({handed_on_at_it} B handed on), answered yes — it took a look dated in the exit's "
        f"own tick, taken before the command's last write, for a proof"
    )
    assert result.exit_code == 0
    assert delivered == FIRST + PAYLOAD, (
        f"{len(delivered)}/{len(FIRST) + len(PAYLOAD)} B — the drain ended before the reader "
        f"it had asked could hand the command's last bytes on"
    )
    assert result.output_unconfirmed is False


async def test_a_chunk_posted_while_the_drain_proves_is_still_delivered(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drain's last ``sleep(0)`` is half of every proven end.

    The reader counts a chunk as handed on once it is POSTED to the loop
    (``call_soon_threadsafe``), so a proof taken on the loop thread says
    "queued", not "delivered". Here the reader is held inside its hand-over
    until the drain first asks for a proof; that call lets it go and waits
    until it has posted and counted the command's 1000 bytes and pinned its
    first look after the exit — a real proof, with the chunk's callback queued
    BEHIND this very step of the drain. Only the yield after the break runs it
    before ``detach`` and ``delivering = False``. Without it (review R3) the
    bytes are dropped with ``exit_code=0`` — "0/1000 B delivered" — and until
    this case every other test stayed green.

    The root backgrounds a silent holder so the pipe stays open: the look that
    pins must see an open pipe on every host (on Windows a look at a broken
    pipe fails, and pins nothing).
    """

    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays)
    gate = threading.Event()

    class PostsDuringTheProof(_PipeReader):
        def __init__(self, stream: Any, state: Any, **kwargs: Any) -> None:
            on_chunk = kwargs["on_chunk"]

            def held(chunk: bytes) -> None:
                gate.wait(BOUND)
                on_chunk(chunk)

            kwargs["on_chunk"] = held
            super().__init__(stream, state, **kwargs)

        def proven(self) -> bool:
            if not gate.is_set():
                gate.set()
                deadline = time.monotonic() + BOUND
                while not (self.handed_on >= len(PAYLOAD) and self.caught_up()):
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.001)
            return super().proven()

    monkeypatch.setattr(bash_module, "_PipeReader", PostsDuringTheProof)
    chunks: list[bytes] = []
    command = _root_command(tmp_path, marker, holder=True, looked=None, code=0)
    try:
        result = await _bounded(
            _exec_task(command, tmp_path, chunks, timeout=30.0),
            BOUND + 5.0,
            "a chunk posted during the proof",
        )
    finally:
        gate.set()
        registrar.settle()

    assert result.exit_code == 0
    delivered = b"".join(chunks)
    assert delivered == PAYLOAD, (
        f"{len(delivered)}/{len(PAYLOAD)} B delivered — the chunk the reader posted while the "
        f"drain was proving was still queued when the drain returned"
    )
    assert result.output_unconfirmed is False


#: How many refused proofs the parked-grace case lets the drain collect before
#: it lets the reader go.
REFUSALS = 3


async def test_a_refused_proof_parks_the_loop_for_a_whole_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a proof the drain waits ONE MORE GRACE with the loop parked — it does not spin.

    The issue's first remedy, and the half of it no byte count can see. After a
    refused proof the drain waits a whole grace on ``eof`` before it asks
    again, and the loop is parked in that wait, which is what leaves the GIL to
    a starved reader. A drain that asked again at once would spin on the loop
    thread until the proof or the hard cap and still lose no bytes — with
    ``end_at = now - EXIT_DRAIN_SECONDS`` in place of ``+`` this case's reader
    still delivered all 1000, and every other case in both files stayed green
    (round-2 review).

    What sees it is the drain's own asks. The reader is held before its first
    read, so every ask is refused, and it is let go by the drain's
    :data:`REFUSALS`-th refusal — a release driven by the asks, not by a clock,
    so the case always observes that many. Parked, they are a grace apart;
    spinning, they came well under a millisecond apart. The bound is half a
    grace rather than a whole one, so that neither a timer that fires a clock
    tick early nor a coarse clock can fail a drain that did park.
    """

    seen: list[_PipeReader] = []
    # Never armed from the exit: only the drain's refusals open it.
    gate = Gate(None)
    asks: list[tuple[float, bool]] = []
    held = held_reader("late", gate, platform=None, seen=seen, fds=set())

    class CountsTheAsks(held):
        def proven(self) -> bool:
            answer = super().proven()
            asks.append((time.monotonic(), answer))
            if sum(1 for _at, proven in asks if not proven) >= REFUSALS:
                gate.event.set()
            return answer

    monkeypatch.setattr(bash_module, "_PipeReader", CountsTheAsks)
    chunks: list[bytes] = []
    command = _root_command(tmp_path, tmp_path / "pids.txt", holder=False, looked=None, code=0)
    try:
        result = await _bounded(
            _exec_task(command, tmp_path, chunks, timeout=30.0),
            BOUND + 5.0,
            "a refused proof",
        )
    finally:
        gate.event.set()
    refused = [at for at, proven in asks if not proven][:REFUSALS]
    gaps = [later - earlier for earlier, later in pairwise(refused)]
    warnings.warn(
        f"#260 parked grace: {len(asks)} asks, the first {len(refused)} refusals "
        f"{[round(gap * 1000, 3) for gap in gaps]} ms apart, "
        f"{len(b''.join(chunks))}/{len(PAYLOAD)} B on {sys.platform}",
        stacklevel=1,
    )

    assert seen, "the held reader was never built — the case measured nothing"
    assert result.exit_code == 0
    assert b"".join(chunks) == PAYLOAD
    assert len(refused) == REFUSALS, "the drain never asked for a proof while the reader was held"
    assert min(gaps) >= EXIT_DRAIN_SECONDS / 2, (
        f"refused proofs {[round(gap * 1000, 3) for gap in gaps]} ms apart — the drain asked "
        f"again without parking the loop for a grace in between"
    )
    assert result.output_unconfirmed is False


async def test_a_reader_held_past_the_hard_cap_is_reported_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one end that can still lose the command's bytes on POSIX says so (ADR-0238's question).

    (On win32 the blocking branch's residual is a second, and silent — the
    pinned ``preread``/``inread`` ids above.) The reader is held before its
    first read until the tool returns (:class:`Gate`, ``hold=None`` — until
    round 4 a timer at 2.6 s, which a runner stopped across the cap could let
    fire first). With no proof ever possible, the drain ends at the hard cap,
    :data:`DRAIN_CAP_SECONDS` past the exit, ``exec`` sets
    ``output_unconfirmed``, and the tool hands the model the notice where its
    body would be. It is the notice ALONE here: the command's 1000 bytes were
    still in the pipe, and ``(no output)`` would have been a claim the drain
    could not make.

    RED BEFORE #260, measured on the base tree: the call came back 0.1 s after
    the exit with ``(no output)``, ``is_error=False`` and no field to say
    otherwise — the command printed 1000 bytes.
    """

    gate = Gate(None)
    gate.arm_from_the_exit(monkeypatch)
    seen: list[_PipeReader] = []
    monkeypatch.setattr(
        bash_module, "_PipeReader", held_reader("late", gate, platform=None, seen=seen, fds=set())
    )
    tool = create_bash_tool(str(tmp_path))
    command = _root_command(tmp_path, tmp_path / "pids.txt", holder=False, looked=None, code=0)
    try:
        result = await tool.execute(
            {"command": command, "timeout": 30}, ToolExecutionContext(tool_call_id="t260")
        )
    finally:
        returned = time.monotonic()
        gate.event.set()
    assert gate.exited_at is not None, "the root's exit was never seen — the case measured nothing"
    after_exit = returned - gate.exited_at
    warnings.warn(
        f"#260 hard cap: the tool returned {after_exit:.3f}s after the exit on {sys.platform}",
        stacklevel=1,
    )

    assert result.content[0].text == (
        "[Output may be incomplete: reading stopped 2s after the command ended, before its "
        "last output could be confirmed. Re-run it, or redirect its output to a file, if the "
        "end matters.]"
    )
    assert result.is_error is False
    assert isinstance(result.details, BashToolDetails)
    assert result.details.exit_code == 0
    assert result.details.output_unconfirmed is True
    assert DRAIN_CAP_SECONDS <= after_exit < DRAIN_CAP_SECONDS + 0.5


async def test_a_kill_legs_hard_cap_is_the_kill_drain_and_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The timeout leg: hard cap ``killed_at + KILL_DRAIN_SECONDS``, then the status line.

    The root writes its 1000 bytes and sleeps past the timeout; the reader is
    held before its first read until the tool returns (:class:`Gate`,
    ``hold=None`` — until round 4 a timer at 1.6 s after the reap, which a
    runner stopped across the cap could let fire first). The drain ends at the
    1.0 s kill cap — the kill legs have no softer one — so the
    notice says ``1s`` and sits between the (empty) body and the status line
    the model is told to act on. A kill leg handed the success path's 2.0 s by
    mistake returns a second later, which the upper bound catches: nothing
    else in the suite pins which cap each leg passes.

    RED BEFORE #260, measured on the base tree: back 0.1 s after the reap,
    with the bare status line and nothing about the 1000 bytes.
    """

    # 2.0 s on win32 for the containment file's reason: pwsh starts in 0.5-0.7 s
    # there, and the root must have written before the kill.
    timeout = 2.0 if sys.platform == "win32" else 1.0
    gate = Gate(None)
    gate.arm_from_the_exit(monkeypatch)
    seen: list[_PipeReader] = []
    monkeypatch.setattr(
        bash_module, "_PipeReader", held_reader("late", gate, platform=None, seen=seen, fds=set())
    )
    sleeper = _script(
        tmp_path,
        "writes_then_sleeps.py",
        "import sys, time\n"
        "sys.stdout.buffer.write(b'x' * 999 + b'\\n')\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(30)\n",
    )
    tool = create_bash_tool(str(tmp_path))
    try:
        result = await tool.execute(
            {"command": _command(sleeper), "timeout": timeout},
            ToolExecutionContext(tool_call_id="t260k"),
        )
    finally:
        returned = time.monotonic()
        gate.event.set()
    assert gate.exited_at is not None, "the reap was never seen — the case measured nothing"
    after_kill = returned - gate.exited_at
    warnings.warn(
        f"#260 kill-leg hard cap: the tool returned {after_kill:.3f}s after the reap on "
        f"{sys.platform}",
        stacklevel=1,
    )

    text = result.content[0].text
    assert text.startswith(
        "[Output may be incomplete: reading stopped 1s after the command ended, before its "
        "last output could be confirmed. Re-run it, or redirect its output to a file, if the "
        "end matters.]\n\nCommand timed out after "
    ), text
    assert result.is_error is True
    assert isinstance(result.details, BashToolDetails)
    assert result.details.exit_code is None
    assert result.details.output_unconfirmed is True
    assert KILL_DRAIN_SECONDS <= after_kill < KILL_DRAIN_SECONDS + 0.5


async def test_a_pipe_that_never_empties_ends_at_the_deadline_on_the_second_proof(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deadline still bounds the call when the pipe is never empty — on the pinned look.

    The reader rests 20 ms after every read and the helper writes 64 KiB blocks
    for longer than the whole drain can last, so the helper is always blocked on
    a FULL pipe: whenever the drain asks, the reader is mid-rest (odd) or the
    pipe holds bytes, and "drained" never holds — deterministically, not by
    luck of scheduling. What ends the drain at the deadline is
    :meth:`_PipeReader.caught_up`: the reader's first look after the exit pinned
    ``handed_on + pending``, and it has handed that much on since, so everything
    the ROOT wrote before it exited is in — its own line arrives first — and the
    helper's blocks after the pin are the cut #232 accepts.

    THE VERDICT IS THE DRAIN'S OWN SEQUENCE (round 5; :class:`DrainLog`,
    :func:`the_deadline_ended_it_on_the_second_proof`): at every look it takes
    at or after its deadline it asks the reader for a proof, and it ends on
    the first yes — the second proof, with the pipe still open — silently.
    Until round 5 the case asserted ``timeout <= elapsed < timeout + 0.5`` on
    POSIX, and the floor was a race: the reader's 20 ms rests had to keep
    beating the 0.1 s idle timer. A runner that stopped the whole process for
    longer than a grace let the idle rule end the drain early — legitimately,
    on the same caught-up pin, the root's line in and the helper cut — and the
    case failed its floor, under a message that said the drain had run to its
    hard cap. That end passes now, because the proof says so; a stall that
    holds the loop past the deadline moves the looks, not which of them ask.
    The sequence holds on every host, win32 included.

    What goes red: without the pin, or with one that is never caught up, every
    ask at the deadline is refused and the drain runs to the 2.0 s hard cap,
    where it asks once more, is refused, and reports an unconfirmed end for a
    command whose own output was complete — a false notice on every command
    that backgrounds something busy. A drain that ends on the clock alone, or
    that sits past its deadline without asking, fails on the sequence too.
    Measured with the drain's ``pin_after`` call deleted (round 2, before the
    exit stamp asked for the pin as well): against a fast reader that deletion
    still came back in time in 1 of 2 runs, because a fast reader sometimes
    empties the pipe and "drained" rescues it; with the reader slowed it failed
    4 of 4.

    Nothing is retained (``on_data`` counts) and nothing spins: the slowed
    reader keeps the helper blocked until ``strays`` ends it.
    """

    timeout = _DEADLINE_ARM_SECONDS
    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays)
    log = DrainLog()
    monkeypatch.setattr(bash_module, "time", log)
    monkeypatch.setattr(
        bash_module, "_exit_drain_cap", log.exit_drain_cap(bash_module._exit_drain_cap)
    )
    built = slow_the_reader(monkeypatch, bash_module, log=log)
    root = _script(tmp_path, "full_pipe_root.py", FULL_PIPE_ROOT_TEMPLATE.replace("@MARK@", MARK))
    first: list[bytes] = []
    total = [0]

    def on_data(chunk: bytes) -> None:
        if not first:
            first.append(chunk[:16])
        total[0] += len(chunk)

    ops = create_local_bash_operations()
    task = asyncio.ensure_future(
        ops.exec(
            _command(root, str(marker), str(timeout + DRAIN_CAP_SECONDS + 3.0)),
            str(tmp_path),
            on_data=on_data,
            timeout=timeout,
        )
    )
    started = time.monotonic()
    try:
        result = await _bounded(
            task, timeout + DRAIN_CAP_SECONDS + 5.0, "a pipe that never empties"
        )
    finally:
        elapsed = time.monotonic() - started
        registrar.settle()
        warnings.warn(
            f"#260 full pipe: timeout={timeout} elapsed={elapsed:.3f}s "
            f"delivered={total[0]} B on {sys.platform}",
            stacklevel=1,
        )

    reader = built[0] if built else None
    assert reader is not None, "the slowed reader was never built — the case measured nothing"
    assert result.exit_code == 0
    assert first and first[0].startswith(b"ROOT-DONE\n"), f"the root's own line: {first!r}"
    the_deadline_ended_it_on_the_second_proof(log, reader, what="full pipe")
    assert result.output_unconfirmed is False, (
        "the drain ran to its hard cap and called a complete output unconfirmed — the pinned "
        "look did not end it at the deadline"
    )


#: How long the late-loop case's reader rests after every read that took
#: bytes: longer than a grace, so a pin first asked for when the drain starts
#: cannot be caught up inside the one grace a late look buys.
SLOWER_THAN_A_GRACE = 0.25


def _away_until_caught_up(
    since: float, built: list[_PipeReader], back: list[float]
) -> Callable[[], None]:
    """A loop callback: away until :data:`LATE` past ``since`` AND the reader has caught up.

    Caught up is: it has handed on everything its pin covers, or it has
    ended. The two late-loop cases pin that a pin asked for at the stamp is the
    reader's to take while the loop is away, so a drain that starts late
    finds it caught up. Until round 5 the loop was away for :data:`LATE`
    exactly, a timer the reader had to beat: a runner that stopped the whole
    process for most of it left the reader short of its pin when the loop came
    back, and the case failed with the product right. So the loop stays away
    until the reader HAS caught up, judged here from the reader's own fields,
    not from :meth:`_PipeReader.caught_up`. That cannot hide what the cases
    pin: with no pin asked for at the stamp there is nothing to catch up with
    while the loop is away, so it comes back only at the anti-hang bound, and
    the drain, asking only then, cannot catch up within its one late grace.

    The wait is on clock READINGS, not a timer, and ``back`` gets the reading
    at which it let the loop go (round 6), so "the loop came back past the
    cap" is a comparison of two readings the callback itself ordered — it
    holds on any clock, a coarse one included.
    """

    def away() -> None:
        deadline = since + BOUND
        while time.monotonic() < deadline:
            reader = built[0] if built else None
            pinned = None if reader is None else reader.pinned
            caught_up = reader is not None and (
                not reader.is_alive() or (pinned is not None and reader.handed_on >= pinned)
            )
            if caught_up and time.monotonic() >= since + LATE:
                break
            time.sleep(0.005)
        back.append(time.monotonic())

    return away


async def test_a_loop_that_reaches_the_drain_late_finds_the_readers_proof_waiting(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pin is asked for at the exit stamp, so a late loop does not flag a complete output.

    Round 4, the cross-review's S2 (a). The drain used to ask for its pin only
    when the loop got to it; a loop kept away past the hard cap then asked,
    and judged, in the same instant, so a reader that had been reading the
    whole time behind a helper that never lets the pipe empty could not have
    caught up with a pin microseconds old — the command's output was complete
    and the result said otherwise (cross-review probe, 2.5 s away:
    ``output_unconfirmed=True`` with the root's line delivered).
    ``_wait_and_stamp`` now asks for the pin itself, on the worker, one
    statement after the stamp: the reader takes it while the loop is away.

    The full-pipe root, its reader resting :data:`SLOWER_THAN_A_GRACE` after
    every read, and the loop blocked from the exit for :data:`LATE` and until
    the reader has caught up with its pin (:func:`_away_until_caught_up`,
    round 5). Stamped at the exit, the pin is caught up within two reads, long
    before the loop is back, and the drain's first look ends it on that
    proof. Asked only by the drain, it would need a look and a read — more
    than the one grace the late look buys — and the end would be flagged.
    """

    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays)
    built = slow_the_reader(monkeypatch, bash_module, pause=SLOWER_THAN_A_GRACE)
    loop = asyncio.get_running_loop()
    real_wait = subprocess.Popen.wait
    stamped: list[float] = []
    back: list[float] = []

    def keeps_the_loop_away(proc: subprocess.Popen[Any], timeout: float | None = None) -> int:
        code = real_wait(proc, timeout=timeout)
        if not stamped:
            stamped.append(time.monotonic())
            loop.call_soon_threadsafe(_away_until_caught_up(stamped[0], built, back))
        return code

    monkeypatch.setattr(subprocess.Popen, "wait", keeps_the_loop_away)
    root = _script(tmp_path, "full_pipe_root.py", FULL_PIPE_ROOT_TEMPLATE.replace("@MARK@", MARK))
    first: list[bytes] = []
    total = [0]

    def on_data(chunk: bytes) -> None:
        if not first:
            first.append(chunk[:16])
        total[0] += len(chunk)

    task = asyncio.ensure_future(
        create_local_bash_operations().exec(
            _command(root, str(marker), str(BOUND + LATE + DRAIN_CAP_SECONDS + 10.0)),
            str(tmp_path),
            on_data=on_data,
            timeout=30.0,
        )
    )
    try:
        result = await _bounded(task, BOUND + LATE + DRAIN_CAP_SECONDS + 5.0, "a loop kept away")
    finally:
        returned = time.monotonic()
        registrar.settle()
    reader = built[0] if built else None
    assert stamped, "the root's exit was never seen — the case measured nothing"
    after_exit = returned - stamped[0]
    warnings.warn(
        f"#260 late loop: exec returned {after_exit:.3f}s after the exit, delivered={total[0]} B, "
        f"pinned={getattr(reader, 'pinned', None)} handed_on={getattr(reader, 'handed_on', None)} "
        f"on {sys.platform}",
        stacklevel=1,
    )

    assert reader is not None, "the slowed reader was never built — the case measured nothing"
    # What the verdict needs: the loop let go past the hard cap. Until round 6
    # this read ``after_exit >= LATE``, the call's return against the stamp —
    # which the callback's own condition makes true only up to the float
    # rounding of ``stamped[0] + LATE``, and which is not when the loop came
    # back. ``back`` is: the callback's reading as it let the loop go.
    assert back and back[0] >= stamped[0] + DRAIN_CAP_SECONDS, (
        f"the loop came back {(back[0] - stamped[0]) if back else None}s after the exit — "
        f"not past the hard cap, so the drain did not start late: the case measured nothing"
    )
    assert result.exit_code == 0
    assert first and first[0].startswith(b"ROOT-DONE\n"), f"the root's own line: {first!r}"
    assert result.output_unconfirmed is False, (
        "a complete output came back flagged — the pin was not taken while the loop was away"
    )


#: The kill-leg twin of ``FULL_PIPE_ROOT_TEMPLATE``: the root writes its own
#: line, backgrounds the same 64 KiB-block helper — in a session of its own, so
#: on POSIX it is outside the tree the timeout leg kills and keeps the pipe full
#: past the kill — announces both pids, and then sleeps past any deadline
#: (argv: marker, life). On win32 the job takes the helper too, and the pipe
#: reaches EOF at the kill.
KILLED_ROOT_TEMPLATE = """\
import os
import subprocess
import sys
import time

marker, life = sys.argv[1], sys.argv[2]
sys.stdout.buffer.write(b"ROOT-DONE\\n")
sys.stdout.buffer.flush()
helper = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import sys, time\\n"
        "end = time.monotonic() + float(sys.argv[1])\\n"
        "block = b'y' * 65535 + b'\\\\n'\\n"
        "while time.monotonic() < end:\\n"
        "    sys.stdout.buffer.write(block)\\n"
        "    sys.stdout.buffer.flush()\\n",
        life,
        "@MARK@",
    ],
    stdin=subprocess.DEVNULL,
    start_new_session=os.name != "nt",
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {helper.pid}\\n")
os.replace(tmp, marker)
time.sleep(30)
"""


async def test_a_kill_leg_whose_drain_starts_late_finds_the_readers_proof_waiting(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kill stamp asks for the pin too, so the drain after it can start late.

    Round 4, the cross-review's S2 (a) on the kill legs. They stamp on the
    loop thread, and with an abort signal — RPC ``bash`` — the timeout leg
    awaits the watcher's end before its drain asks for anything, which yields
    the loop to whatever else is queued. Here that is a callback that keeps
    the loop away for :data:`LATE` and until the reader has caught up with its
    pin (:func:`_away_until_caught_up`, round 5), queued at the reap. The
    helper, outside the tree on POSIX, keeps the pipe full past the kill, and
    the reader rests :data:`SLOWER_THAN_A_GRACE` after every read. Asked for
    at the kill stamp, the pin is caught up while the loop is away and the
    late drain ends on it, unflagged; asked for only by the drain, it would
    need a look and a read — more than the one grace the late look buys — and
    a complete output would come back flagged. On win32 the job kills the
    helper with the tree, the pipe reaches EOF, and the case is not a
    discriminator there.
    """

    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays)
    built = slow_the_reader(monkeypatch, bash_module, pause=SLOWER_THAN_A_GRACE)
    loop = asyncio.get_running_loop()
    real_wait = subprocess.Popen.wait
    reaped: list[float] = []
    back: list[float] = []

    def keeps_the_loop_away(proc: subprocess.Popen[Any], timeout: float | None = None) -> int:
        code = real_wait(proc, timeout=timeout)
        if not reaped and not _is_taskkill(proc):
            reaped.append(time.monotonic())
            loop.call_soon_threadsafe(_away_until_caught_up(reaped[0], built, back))
        return code

    monkeypatch.setattr(subprocess.Popen, "wait", keeps_the_loop_away)
    root = _script(tmp_path, "killed_root.py", KILLED_ROOT_TEMPLATE.replace("@MARK@", MARK))
    first: list[bytes] = []
    total = [0]

    def on_data(chunk: bytes) -> None:
        if not first:
            first.append(chunk[:16])
        total[0] += len(chunk)

    # 2.0 s on win32 for the containment file's reason: pwsh starts in 0.5-0.7 s
    # there, and the root must have written before the kill.
    timeout = 2.0 if sys.platform == "win32" else 1.0
    never = asyncio.Event()
    task = asyncio.ensure_future(
        create_local_bash_operations().exec(
            _command(root, str(marker), str(BOUND + timeout + LATE + DRAIN_CAP_SECONDS + 10.0)),
            str(tmp_path),
            on_data=on_data,
            signal=never,
            timeout=timeout,
        )
    )
    try:
        result = await _bounded(
            task, BOUND + timeout + LATE + DRAIN_CAP_SECONDS + 5.0, "a late kill drain"
        )
    finally:
        returned = time.monotonic()
        registrar.settle()
    reader = built[0] if built else None
    assert reaped, "the reap was never seen — the case measured nothing"
    after_reap = returned - reaped[0]
    warnings.warn(
        f"#260 late kill drain: exec returned {after_reap:.3f}s after the reap, "
        f"delivered={total[0]} B, pinned={getattr(reader, 'pinned', None)} "
        f"handed_on={getattr(reader, 'handed_on', None)} on {sys.platform}",
        stacklevel=1,
    )

    assert reader is not None, "the slowed reader was never built — the case measured nothing"
    # The late-loop case's reading, for the kill cap (round 6): when the
    # callback let the loop go, not when the call came back.
    assert back and back[0] >= reaped[0] + KILL_DRAIN_SECONDS, (
        f"the loop came back {(back[0] - reaped[0]) if back else None}s after the reap — "
        f"not past the kill cap, so the drain did not start late: the case measured nothing"
    )
    assert result.exit_code is None
    assert result.timed_out is True
    assert first and first[0].startswith(b"ROOT-DONE\n"), f"the root's own line: {first!r}"
    assert result.output_unconfirmed is False, (
        "a complete output came back flagged — the pin was not taken at the kill stamp"
    )


#: A tree MEMBER that is not the root. It writes 100 B once the file ``argv[1]``
#: exists and its last 1000 B — marked ``LAST`` — once ``argv[2]`` does, then
#: waits for the ladder to end it.
KILL_ORDER_MEMBER = """\
import os
import sys
import time

first, last = sys.argv[1], sys.argv[2]
deadline = time.monotonic() + 30.0
while not os.path.exists(first) and time.monotonic() < deadline:
    time.sleep(0.001)
sys.stdout.buffer.write(b"A" * 99 + b"\\n")
sys.stdout.buffer.flush()
while not os.path.exists(last) and time.monotonic() < deadline:
    time.sleep(0.001)
sys.stdout.buffer.write(b"LAST" + b"B" * 995 + b"\\n")
sys.stdout.buffer.flush()
time.sleep(30)
"""

#: Starts :data:`KILL_ORDER_MEMBER` on the inherited pipe — a plain child, so in
#: the root's process group on POSIX and in the job on win32, where the ladder
#: reaches it and a kill of the root alone does not — announces both pids, and
#: waits to be killed (argv: marker, the member's script, its two files).
#: ``stdin=DEVNULL`` and nothing else, for the win32 inheritance rule.
KILL_ORDER_ROOT = """\
import os
import subprocess
import sys
import time

marker, member, first, last = sys.argv[1:5]
child = subprocess.Popen(
    [sys.executable, member, first, last, "@MARK@"],
    stdin=subprocess.DEVNULL,
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {child.pid}\\n")
os.replace(tmp, marker)
time.sleep(30)
"""


def _within(predicate: Callable[[], bool]) -> bool:
    """Poll ``predicate`` up to :data:`BOUND`; never raise — this runs inside ``exec``."""

    deadline = time.monotonic() + BOUND
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.001)
    return True


@pytest.mark.parametrize("branch", ["native", "win32"])
async def test_a_kill_that_reaps_the_root_first_is_proven_only_after_the_whole_ladder(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch, branch: str
) -> None:
    """An abort whose ladder ends the ROOT before the rest of its tree proves nothing until the end.

    Round 6, the second cross-review's N1. On an abort or a cancel the
    ``proc.wait`` worker is still waiting when the ladder runs, so it sees the
    ROOT reaped, stamps it and asks for its pin — from inside the ladder. On
    win32 the ladder is ``taskkill /T /F``, which can end the root first, and
    then ``TerminateJobObject``, and a job member the walk cannot reach writes
    on between the two. The kill stamp that follows the ladder asks again,
    later. Round 4's ``pin_after`` kept a pin already taken, so the reap's pin
    — from a look before the member's last write — proved the drain done with
    those bytes in the reader's hands: with this order modelled on darwin (the
    root SIGKILLed, 50 ms, then the real ladder) and the reader starved once it
    had caught up, the member's last 711-774 B were lost in 10 of 10 rounds,
    none flagged. Now the later instant wins.

    That order, made deterministic. The ladder is replaced by one that kills
    the root alone, waits for the worker's pin (a spy on ``pin_after``) and for
    the reader to have pinned the member's first 100 B after it and caught up,
    lets the member write its last 1000 B, waits until the reader holds them
    inside their hand-over, and only then runs the real ladder. The reader is
    let go by the drain's first ask for a proof. That ask must be refused —
    with the kill stamp's pin not yet taken nothing proves what the tree wrote
    before the ladder ended — and all 1100 B arrive, unflagged. With round 4's
    rule that ask answered yes on the reap's pin and the drain ended there.

    Both branches: the hold is inside ``on_chunk``, where the phase is odd on
    either, so ``drained`` cannot answer for the pin and the blocking branch's
    stale-look residual is not in play. The order is modelled, not measured on
    Windows.
    """

    loop_thread = threading.current_thread()
    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays)
    first = tmp_path / "first"
    last = tmp_path / "last"
    #: Every ``pin_after``: the instant, and whether it came off the loop thread.
    pins: list[tuple[float, bool]] = []
    holding = threading.Event()
    release = threading.Event()
    #: Every ``proven()`` answer the drain got: (answer, handed_on at it).
    asks: list[tuple[bool, int]] = []
    seen: list[_PipeReader] = []
    #: How far the modelled ladder got before it ran the real one.
    steps: list[str] = []

    class HeldAtTheLastBytes(_PipeReader):
        def __init__(self, stream: Any, state: Any, **kwargs: Any) -> None:
            on_chunk = kwargs["on_chunk"]

            def held(chunk: bytes) -> None:
                if b"LAST" in chunk:
                    holding.set()
                    release.wait(BOUND)
                on_chunk(chunk)

            kwargs["on_chunk"] = held
            if branch == "win32":
                kwargs["platform"] = "win32"
            super().__init__(stream, state, **kwargs)
            seen.append(self)

        def pin_after(self, instant: float) -> None:
            pins.append((instant, threading.current_thread() is not loop_thread))
            super().pin_after(instant)

        def proven(self) -> bool:
            answer = super().proven()
            asks.append((answer, self.handed_on))
            release.set()
            return answer

    real_end_the_tree = bash_module._end_the_tree

    def ends_the_root_first(tree: Any, proc: subprocess.Popen[bytes], *, reap: float) -> None:
        # ``taskkill /T /F`` ends the root; the worker sees it reaped and pins.
        with contextlib.suppress(OSError):
            proc.kill()
        if _within(lambda: any(off_loop for _instant, off_loop in pins)):
            steps.append("reaped")
            reaped_at = next(instant for instant, off_loop in pins if off_loop)
            # So the reader's next look is dated after the reap on any clock.
            _within(lambda: time.monotonic() > reaped_at)
            first.write_text("go", encoding="utf-8")
            reader = seen[0] if seen else None

            def caught_up_after_the_reap() -> bool:
                pinned = None if reader is None else reader.pinned
                return (
                    reader is not None
                    and pinned is not None
                    and reader.handed_on >= 100
                    and reader.handed_on >= pinned
                )

            if _within(caught_up_after_the_reap):
                steps.append("caught up")
                # A member the walk did not reach writes its last bytes.
                last.write_text("go", encoding="utf-8")
                if holding.wait(BOUND):
                    steps.append("holding")
        # ``TerminateJobObject``: the real ladder ends the rest of the tree.
        real_end_the_tree(tree, proc, reap=reap)

    monkeypatch.setattr(bash_module, "_end_the_tree", ends_the_root_first)
    monkeypatch.setattr(bash_module, "_PipeReader", HeldAtTheLastBytes)
    member = _script(tmp_path, "member.py", KILL_ORDER_MEMBER)
    root = _script(tmp_path, "member_root.py", KILL_ORDER_ROOT.replace("@MARK@", MARK))
    abort = asyncio.Event()
    chunks: list[bytes] = []
    task = _exec_task(
        _command(root, str(marker), member, str(first), str(last)),
        tmp_path,
        chunks,
        signal=abort,
        timeout=30.0,
    )
    try:
        await _await_pids(registrar, "the root that reaps first")
        abort.set()
        result = await _bounded(task, 3 * BOUND + 10.0, "the root that reaps first")
    finally:
        release.set()
        registrar.settle()
    delivered = b"".join(chunks)
    warnings.warn(
        f"#260 the root reaped first ({branch}): steps {steps}, pins (instant, off the loop) "
        f"{pins}, asks (answer, handed_on) {asks}, {len(delivered)}/1100 B, "
        f"output_unconfirmed={result.output_unconfirmed} on {sys.platform}",
        stacklevel=1,
    )

    assert steps == ["reaped", "caught up", "holding"], (
        f"the modelled ladder stopped after {steps} — the case measured nothing"
    )
    reap_pins = [instant for instant, off_loop in pins if off_loop]
    kill_pins = [instant for instant, off_loop in pins if not off_loop]
    assert kill_pins and kill_pins[0] > reap_pins[0], (
        f"the kill stamp {kill_pins} came no later than the reap's {reap_pins} — the case "
        f"measured nothing"
    )
    assert asks, "the drain never asked for a proof — it ended on the clock alone"
    first_answer, handed_on_at_it = asks[0]
    assert handed_on_at_it == 100, (
        f"{handed_on_at_it} B handed on at the drain's first ask — the reader was not holding "
        f"the member's last bytes there: the case measured nothing"
    )
    assert first_answer is False, (
        "the drain's first ask, with the member's last 1000 B in the reader's hands, answered "
        "yes — it took the pin from a look after the ROOT's reap, before those bytes were "
        "written, for a proof that the whole tree's output was in"
    )
    assert result.exit_code is None
    assert delivered == b"A" * 99 + b"\n" + b"LAST" + b"B" * 995 + b"\n", (
        f"{len(delivered)}/1100 B — the drain ended before the reader could hand on the last "
        f"bytes the tree wrote before the ladder ended"
    )
    assert result.output_unconfirmed is False


@pytest.mark.parametrize("away", ["before_the_first_look", "while_parked", "twice"])
async def test_a_late_look_at_the_hard_cap_gives_the_reader_one_more_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, away: str
) -> None:
    """A drain kept away past its hard cap asks again a grace later before it gives up — once.

    Round 4, the cross-review's S2 (b). The hard cap is a clock reading, and a
    drain kept away past it — its loop, or the whole process, stopped — took
    the no-proof verdict at its first look back, in the instant the stall gave
    the reader back its CPU. Measured with the reader starved until the loop
    came back and the pin already taken at the stamp (the cross-review's probe,
    and one whose stall lands while the drain is parked): 2.5 s away before the
    drain's first look, 3 and 7 rounds of 20 (two runs) lost the command's
    1000 B, flagged, and 5 and 3 more flagged all of it; 2.5 s away while it
    was parked, 19 and 16 of 20 flagged a complete output. Now a look that
    comes more than a grace
    after the drain meant to look, within a grace of the hard cap or past it,
    moves the verdict one grace past that look, once per drain: 0 of 20 lost
    or flagged in either shape.

    The reader is held until the call returns, so no proof can come and the
    case pins the mechanism, deterministically: the drain is kept away for
    :data:`LATE` — from the exit stamp (``before_the_first_look``, ``twice``)
    or from its first refused proof (``while_parked``) — and after it is back
    it asks at least twice, half a grace apart or more, before the flagged
    end. Before, it asked once and ended. ``twice`` keeps it away again, for
    :data:`AWAY_AGAIN`, from its first ask after that: the grace is given
    once, so the drain comes back to its verdict and asks exactly twice — a
    grace given at every late look would have no bound on a loop that is
    always late.
    """

    loop = asyncio.get_running_loop()
    gate = Gate(None)
    gate.arm_from_the_exit(monkeypatch)
    away_from: list[float] = []
    #: When the loop got back from its LATE absence, read on the loop thread as
    #: the stall ends (round 6), so every ask the drain makes after it reads
    #: later — on any clock. Until round 6 this was ``away_from[0] + LATE``,
    #: computed: on a clock whose tick does not divide LATE the ask right after
    #: the stall could read a tick BEFORE it and drop out of ``late``, and
    #: ``twice`` then kept the loop away from the wrong ask.
    back: list[float] = []
    again: list[float] = []
    #: Every instant ``pin_after`` was asked for — the product's own stamps.
    pins: list[float] = []

    def stay_away() -> None:
        time.sleep(LATE)
        back.append(time.monotonic())

    if away in ("before_the_first_look", "twice"):
        gate_wait = subprocess.Popen.wait

        def keeps_the_loop_away(proc: subprocess.Popen[Any], timeout: float | None = None) -> int:
            code = gate_wait(proc, timeout=timeout)
            if not away_from:
                away_from.append(time.monotonic())
                loop.call_soon_threadsafe(stay_away)
            return code

        monkeypatch.setattr(subprocess.Popen, "wait", keeps_the_loop_away)
    seen: list[_PipeReader] = []
    held = held_reader("late", gate, platform=None, seen=seen, fds=set())

    class KeptAway(held):
        def pin_after(self, instant: float) -> None:
            pins.append(instant)
            super().pin_after(instant)

        def proven(self) -> bool:
            answer = super().proven()
            now = time.monotonic()
            if away == "while_parked" and not away_from:
                away_from.append(now)
                loop.call_soon(stay_away)
            elif away == "twice" and back and not again:
                again.append(now)
                loop.call_soon(time.sleep, AWAY_AGAIN)
            return answer

    monkeypatch.setattr(bash_module, "_PipeReader", KeptAway)
    chunks: list[bytes] = []
    command = _root_command(tmp_path, tmp_path / "pids.txt", holder=False, looked=None, code=0)
    try:
        result = await _bounded(
            _exec_task(command, tmp_path, chunks, timeout=30.0), BOUND + 5.0, "a late look"
        )
    finally:
        returned = time.monotonic()
        gate.event.set()
    assert gate.exited_at is not None, "the root's exit was never seen — the case measured nothing"
    assert away_from and back, "the loop was never kept away — the case measured nothing"
    asks = cast("Any", seen[0]).asks
    late = [at for at, _answer in asks if at >= back[0]]
    warnings.warn(
        f"#260 late look ({away}): back {back[0] - gate.exited_at:.3f}s after the exit, "
        f"asked {len(late)} times after, over {(late[-1] - late[0]) if late else 0:.3f}s, "
        f"returned {returned - back[0]:.3f}s after on {sys.platform}",
        stacklevel=1,
    )

    # What the verdict needs: the drain back past its hard cap, measured from
    # the stamp it is armed from (the first instant pinned) — so its first look
    # after the stall takes the late grace.
    assert pins and back[0] >= pins[0] + DRAIN_CAP_SECONDS, (
        f"the loop came back {back[0] - pins[0] if pins else None}s after the exit stamp — "
        f"before the hard cap: the case measured nothing"
    )
    assert result.exit_code == 0
    assert b"".join(chunks) == b""
    assert result.output_unconfirmed is True
    assert not any(answer for _at, answer in asks), "a held reader proved something"
    assert len(late) >= 2, (
        f"the drain asked {len(late)} time(s) after it came back — it gave up at its late look "
        f"without a grace for the reader"
    )
    assert late[-1] - late[0] >= EXIT_DRAIN_SECONDS / 2
    assert returned - back[0] >= EXIT_DRAIN_SECONDS / 2
    if away == "twice":
        assert again, "the loop was never kept away a second time — the case measured nothing"
        assert len(late) == 2, (
            f"the drain asked {len(late)} times after it came back — a second late look bought "
            f"a second grace"
        )


async def test_a_late_look_far_from_the_hard_cap_moves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drain that is late long before its hard cap keeps that cap — it neither gains nor loses.

    The other half of the late-look rule: it applies only within a grace of
    the hard cap or past it. The loop is kept away :data:`AWAY_AGAIN` from the
    exit stamp — late, but nowhere near the cap — and the reader is held
    before its first read until 1.0 s after the exit, which only a drain that
    kept its 2.0 s cap still waits for: all 1000 B arrive and nothing is
    flagged. Were the late look to set the cap to a grace past itself whatever
    the cap was, it would pull the cap IN, to about 0.4 s, and the drain would
    end there with nothing delivered, flagged.

    WHY THE DRAIN'S FIRST LOOK COMES AFTER THE STALL. The stall is queued on the
    loop from the ``proc.wait`` worker, inside the ``Popen.wait`` that
    ``_wait_and_stamp`` is still in: before that function stamps the exit and
    returns, so before the executor's done-callback queues the worker's result
    (``call_soon_threadsafe`` both, onto the loop's one FIFO). The loop runs
    the stall first; only then does the result reach ``exec``'s task, and the
    drain start. The case checks it rather than assume it: the drain's own
    first look (:class:`DrainLog`) reads no earlier than the stall's end.

    WHAT THE VERDICT NEEDS (round 6) is that first look LATE, by the drain's
    own rule: more than a grace after the stamp it is armed from, which is the
    first instant ``pin_after`` is asked for. Both are the drain's readings, so
    the comparison is its own and needs no slack for a coarse clock. Until
    round 6 the case asked instead that the first ask come a whole
    :data:`AWAY_AGAIN` after the worker's reading, two readings around a 0.3 s
    sleep: the windows-latest py3.12 leg of CI run 36000198338 read 0.296 s of
    it on its 15.6 ms clock and failed the case, with all 1000 B delivered and
    nothing flagged.
    """

    loop = asyncio.get_running_loop()
    gate = Gate(1.0)
    gate.arm_from_the_exit(monkeypatch)
    gate_wait = subprocess.Popen.wait
    away_from: list[float] = []
    back: list[float] = []
    pins: list[float] = []

    def stay_away() -> None:
        time.sleep(AWAY_AGAIN)
        back.append(time.monotonic())

    def keeps_the_loop_away(proc: subprocess.Popen[Any], timeout: float | None = None) -> int:
        code = gate_wait(proc, timeout=timeout)
        if not away_from:
            away_from.append(time.monotonic())
            loop.call_soon_threadsafe(stay_away)
        return code

    monkeypatch.setattr(subprocess.Popen, "wait", keeps_the_loop_away)
    log = DrainLog()
    monkeypatch.setattr(bash_module, "time", log)
    monkeypatch.setattr(
        bash_module, "_exit_drain_cap", log.exit_drain_cap(bash_module._exit_drain_cap)
    )
    seen: list[_PipeReader] = []
    held = held_reader("late", gate, platform=None, seen=seen, fds=set())

    class KnowsTheStamp(held):
        def pin_after(self, instant: float) -> None:
            pins.append(instant)
            super().pin_after(instant)

    monkeypatch.setattr(bash_module, "_PipeReader", KnowsTheStamp)
    chunks: list[bytes] = []
    command = _root_command(tmp_path, tmp_path / "pids.txt", holder=False, looked=None, code=0)
    try:
        result = await _bounded(
            _exec_task(command, tmp_path, chunks, timeout=30.0), BOUND + 5.0, "an early late look"
        )
    finally:
        returned = time.monotonic()
        gate.event.set()
    assert gate.exited_at is not None, "the root's exit was never seen — the case measured nothing"
    assert away_from and back, "the loop was never kept away — the case measured nothing"
    looks = [event[1] for event in log.events if event[0] == "look"]
    first_look = looks[0] if looks else None
    late_by = first_look - pins[0] if pins and first_look is not None else None
    warnings.warn(
        f"#260 early late look: the drain's first look {late_by}s after its exit stamp, "
        f"{(first_look - back[0]) if first_look is not None else None}s after the stall "
        f"ended; returned {returned - gate.exited_at:.3f}s after the exit, "
        f"{len(b''.join(chunks))}/{len(PAYLOAD)} B on {sys.platform}",
        stacklevel=1,
    )

    assert first_look is not None and first_look >= back[0], (
        "the drain looked before the loop came back — the FIFO this case rests on did not hold"
    )
    assert late_by is not None and late_by > EXIT_DRAIN_SECONDS, (
        f"the drain's first look came {late_by}s after its exit stamp — not more than a grace, "
        f"so it was not late: the case measured nothing"
    )
    assert result.exit_code == 0
    assert b"".join(chunks) == PAYLOAD, (
        f"{len(b''.join(chunks))}/{len(PAYLOAD)} B — a late look far from the hard cap moved it"
    )
    assert result.output_unconfirmed is False


class _UnconfirmedOps:
    """A :class:`BashOperations` that prints ``lines`` lines and reports an unproven end."""

    def __init__(self, lines: int, exit_code: int | None) -> None:
        self._lines = lines
        self._exit_code = exit_code

    async def exec(
        self,
        command: str,
        cwd: str,
        *,
        on_data: Any,
        signal: Any = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecExitResult:
        for index in range(self._lines):
            on_data(f"out {index}\n".encode())
        return ExecExitResult(exit_code=self._exit_code, output_unconfirmed=True)


async def test_the_notice_follows_the_truncation_and_precedes_the_status(tmp_path: Path) -> None:
    """Where the notice goes: after the kept tail and its notice, before the status.

    ``truncate_tail`` keeps the LAST lines, so a notice written into the output
    before the cut could itself be cut; appended afterwards it cannot. The
    status line stays last because it is what the model acts on. 2500 lines is
    past the 2000-line cap, so all three parts are present.

    And it names no number: the ``2s``/``1s`` the cases above read is the LOCAL
    drain's hard cap, and a custom ``BashOperations`` like this one had timing
    of its own that the tool cannot know (review).
    """

    tool = create_bash_tool(str(tmp_path), {"operations": _UnconfirmedOps(2500, 3)})
    result = await tool.execute({"command": "x"}, ToolExecutionContext(tool_call_id="t260n"))

    text = result.content[0].text
    body, _, rest = text.partition("\n\n[Showing lines 501-2500 of 2500. Full output: ")
    assert body.endswith("out 2499"), "the kept tail is not where the body should end"
    notice_at = rest.index(
        "]\n\n[Output may be incomplete: reading stopped after the command ended, before its "
        "last output could be confirmed. Re-run it, or redirect its output to a file, if the "
        "end matters.]"
    )
    assert rest.endswith("if the end matters.]\n\nCommand exited with code 3"), (
        "the status line is not last, or the notice is not right before it"
    )
    assert notice_at > 0
    assert result.is_error is True
    assert isinstance(result.details, BashToolDetails)
    assert result.details.output_unconfirmed is True


async def test_a_proven_end_says_nothing(tmp_path: Path) -> None:
    """The default is silence: an end with a proof — every routine one — adds no line.

    Pi says nothing at any end; Aelix says something only at the one end that
    can lose the command's own bytes on POSIX (win32's residual is silent: the
    ids pinned above). A ``BashOperations`` that predates the field constructs
    ``ExecExitResult`` without it and gets ``False``.
    """

    assert ExecExitResult(exit_code=0).output_unconfirmed is False

    class _Quiet:
        async def exec(self, command: str, cwd: str, **_kwargs: Any) -> ExecExitResult:
            _kwargs["on_data"](b"all of it\n")
            return ExecExitResult(exit_code=0)

    tool = create_bash_tool(str(tmp_path), {"operations": _Quiet()})
    result = await tool.execute({"command": "x"}, ToolExecutionContext(tool_call_id="t260q"))

    assert result.content[0].text == "all of it\n"
    assert isinstance(result.details, BashToolDetails)
    assert result.details.output_unconfirmed is False
