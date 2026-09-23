"""#260: what ``_PipeReader`` can prove to a drain, and ``run_contained``'s drain asking it.

Two halves. The UNIT half drives a reader over a real ``os.pipe()`` with no
process at all and pins the two proofs the drains now need before they may end
on their idle rule or a soft cap:

* :meth:`_PipeReader.drained` — the reader holds nothing and the pipe is empty
  now. Its phase is EVEN only while it holds nothing it has read, it is ODD
  while a chunk is in its hands, and it stays odd once the reader has ended.
  On the blocking branch "empty" is the reader's own last look, so a look that
  saw bytes proves nothing.
* :meth:`_PipeReader.caught_up` — the reader has handed on everything its first
  look dated AFTER :meth:`_PipeReader.pin_after`'s instant saw: the proof for a
  pipe that never empties. The LATEST instant asked for: since round 6 a later
  one voids a pin taken before it.

Each is run on both branches: ``native`` (on POSIX the non-blocking fd and
``poll``) and ``win32`` (the blocking ``read1`` with a look before every read,
forced through ``platform="win32"``). A stream with no fd — every scripted
double in ``test_pipe_reader_callbacks.py`` — keeps its old reads and proves
nothing. The cases that pin also run on two clocks (:func:`clock`):
this host's, and a coarse one whose tick ends only when the case moves it —
windows-latest's clock under CPython 3.11 and 3.12, made deterministic.

The SEAM half is ``run_contained``'s drain (#221's site of the same defect):
the reader is held past the grace, from the start of the run until
:data:`HOLD` after the root's own exit, and the root's 1000 bytes must still
come back. Measured on the base tree ``02f98560``: every native case here
returned ``stdout=b''`` 0.1 s after the exit, ``returncode == 0``. A reader
held until the call returns instead pins where the waiting stops: at the
hard cap, past the caller's deadline, and silently — and, since round 4, one
grace past a late look at that cap, once. And a pipe a helper keeps full
still ends at the caller's deadline, on the second proof, not at that cap.

The seam machinery in the middle is shared: ``tests/tools/test_bash_drain_asks_the_pipe.py``
holds the bash tool's reader with the same gate and the same holds.
"""

from __future__ import annotations

import errno
import io
import os
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, Any, cast

import pytest
from aelix_ai.utils import _process_tree
from aelix_ai.utils._process_tree import (
    _DRAIN_POLL_SECONDS,
    _READ_CHUNK_BYTES,
    DRAIN_CAP_SECONDS,
    EXIT_DRAIN_SECONDS,
    _PipeReader,
    _ReadState,
    run_contained,
)

from tests.process_tree.test_process_tree_real_processes import (
    strays as _strays_fixture,
)
from tests.process_tree.test_run_contained_real_processes import MARK, _registrar

#: ``test_run_contained_real_processes.py``'s cleanup fixture, via the file
#: that owns it, re-exported the way every real-process file here does.
strays = _strays_fixture

#: An anti-hang bound on every wait in here, never a verdict.
BOUND = 15.0

BRANCHES = ["native", "win32"]


def _platform(branch: str) -> str | None:
    return "win32" if branch == "win32" else None


# === the unit half: a reader over a real pipe, no process ===================


class _Chunks:
    """An ``on_chunk`` that can hold the reader INSIDE the hand-over.

    ``inside`` is set as a chunk arrives, before the hold, so a case knows the
    reader is odd; ``release`` lets it go on. The first ``hold`` chunks are
    held, each until released, and none after them.
    """

    def __init__(self, *, hold: int) -> None:
        self.got: list[bytes] = []
        self.inside = threading.Event()
        self.release = threading.Event()
        self._holds = hold

    def __call__(self, chunk: bytes) -> None:
        self.inside.set()
        if self._holds > 0:
            self._holds -= 1
            self.release.wait(BOUND)
        self.got.append(chunk)

    def release_and_hold_the_next(self) -> None:
        """Let the held chunk go and catch the next one, with no window between.

        The new events go in BEFORE the old release is set: the reader reaches
        its next hand-over only after this one returns, so it can only find
        them, never wait on a release that is already set or miss ``inside``.
        """

        held = self.release
        self.release = threading.Event()
        self.inside.clear()
        held.set()

    def let_go(self) -> None:
        """Hold nothing more and release what is held — every case's ``finally``."""

        self._holds = 0
        self.release.set()


def _reader_over_a_pipe(
    on_chunk: Callable[[bytes], None] | None, *, branch: str
) -> tuple[_PipeReader, int]:
    """An UNSTARTED reader over a fresh pipe, and the pipe's write end."""

    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    reader = _PipeReader(
        stream,
        _ReadState(last_chunk_at=time.monotonic()),
        on_chunk=on_chunk,
        platform=_platform(branch),
    )
    return reader, write_fd


def _until(predicate: Callable[[], bool], what: str) -> None:
    """Poll ``predicate`` until it holds, failing BY NAME at the anti-hang bound."""

    deadline = time.monotonic() + BOUND
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail(f"{what}: not within {BOUND}s")
        time.sleep(0.002)


def _end(reader: _PipeReader, write_fd: int) -> None:
    """Close the write end and join, so no daemon reader outlives its case."""

    os.close(write_fd)
    reader.join(BOUND)
    assert not reader.is_alive(), "the reader outlived its pipe's EOF"


class _HostClock:
    """This host's ``time.monotonic()``, as a case reads a pin's instant and waits on it.

    Coarse on windows-latest under CPython 3.11 and 3.12: ``GetTickCount64()``,
    "a resolution of 15.6 milliseconds" (CPython's 3.13 What's New, gh-88494),
    so there a look and an instant taken microseconds apart are often EQUAL —
    which is how CI run 35952321924 caught the pin's ``>=``. Each wait says how
    long this host's clock took to move, and what it is.
    """

    def read(self) -> float:
        return time.monotonic()

    def move_past(self, instant: float) -> None:
        """Return once a reading is later than ``instant``, so the next look is dated after it."""

        started = time.perf_counter()
        _until(lambda: time.monotonic() > instant, "the clock never moved past the pin's instant")
        info = time.get_clock_info("monotonic")
        warnings.warn(
            f"#260 host clock {info.implementation}, resolution {info.resolution}: moved past "
            f"the pin's instant in {(time.perf_counter() - started) * 1000:.3f} ms",
            stacklevel=2,
        )


class _CoarseClock:
    """``_process_tree``'s ``time``, with a tick that ends only when the case moves it.

    Windows' clock made deterministic: EVERY reading inside a tick is the same,
    so a look and a pin instant taken microseconds apart are equal every time,
    not only when no tick boundary falls between them. The reader dates its
    looks by this clock and the case reads its instants off it, so the two
    sides of every comparison share one clock, which never goes back. Anything
    else ``time`` offers passes through. It cannot go quietly unused: a reader
    that dated its looks by any other clock would date them after every
    instant here, and the blocking ``coarse`` ids would fail on their first
    assertion.
    """

    #: One of Windows' ticks, as a float the arithmetic keeps exact (2 ** -6).
    TICK = 0.015625

    def __init__(self) -> None:
        self.now = time.monotonic()

    def monotonic(self) -> float:
        return self.now

    def read(self) -> float:
        return self.now

    def move_past(self, instant: float) -> None:
        while self.now <= instant:
            self.now += self.TICK

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@pytest.fixture(params=["host", "coarse"])
def clock(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_HostClock | _CoarseClock]:
    """The clock a pinning case runs on: this host's, or :class:`_CoarseClock`.

    ``coarse`` is patched in as ``_process_tree``'s ``time``, which is where
    the reader reads the date of every look.
    """

    if request.param == "host":
        yield _HostClock()
        return
    coarse = _CoarseClock()
    monkeypatch.setattr(_process_tree, "time", coarse)
    yield coarse


@pytest.mark.parametrize("branch", BRANCHES)
def test_the_phase_is_even_only_while_the_reader_holds_nothing(branch: str) -> None:
    """Even while parked, odd while a chunk is in its hands, odd for good after the end.

    The chunk is held inside ``on_chunk`` — read, not yet handed on — which is
    the window whose bytes a drain that trusted the clock used to drop; the
    reader must not be :meth:`~_PipeReader.drained` there. Parked again over an
    empty pipe it is, which is the proof #232's backgrounded-helper return rests
    on. After EOF it never is again: an ended reader goes odd BEFORE it closes
    the fd, so an asker racing the close discards what it read.
    """

    chunks = _Chunks(hold=1)
    reader, write_fd = _reader_over_a_pipe(chunks, branch=branch)
    reader.start()
    try:
        _until(reader.drained, "an idle reader over an empty pipe was never drained")
        assert reader._mark[0] % 2 == 0

        os.write(write_fd, b"x" * 10)
        assert chunks.inside.wait(BOUND), "the reader never took the chunk"
        assert reader._mark[0] % 2 == 1
        assert reader.drained() is False
        assert reader.handed_on == 0

        chunks.release.set()
        _until(lambda: reader.handed_on == 10, "the held chunk was never handed on")
        _until(reader.drained, "the reader never parked again over the empty pipe")
    finally:
        chunks.let_go()
        _end(reader, write_fd)

    assert chunks.got == [b"x" * 10]
    assert reader.eof is True
    assert reader._mark[0] % 2 == 1
    assert reader.drained() is False


@pytest.mark.parametrize("branch", BRANCHES)
def test_bytes_the_reader_has_not_taken_are_never_drained(branch: str) -> None:
    """Bytes still IN THE PIPE are not "drained", whatever the phase says.

    The reader is built and not started, so its phase is the initial even one
    and it has taken nothing — the shape #260's local reproduction measured at
    the break (``FIONREAD`` 26624 with the reader alive; what the CI runs hit is
    not measured). POSIX asks the pipe from the asking thread, so an empty pipe
    is drained and five bytes are not. The blocking branch may not ask the pipe
    from here at all, and a reader that has never looked has not said the pipe
    was empty: not drained, either way.
    """

    reader, write_fd = _reader_over_a_pipe(None, branch=branch)
    try:
        assert reader.drained() is (branch == "native" and sys.platform != "win32")
        os.write(write_fd, b"abcde")
        assert reader.drained() is False
        assert reader.caught_up() is False
    finally:
        os.close(write_fd)
        reader._stream.close()


@pytest.mark.parametrize("branch", BRANCHES)
def test_the_asking_thread_trusts_its_own_look_only_across_a_reader_that_stood_still(
    branch: str,
) -> None:
    """What :meth:`~_PipeReader.drained` may take from asking the pipe itself.

    POSIX (native): the asker reads the mark, then the pipe (``FIONREAD``). An
    empty answer proves nothing if the reader took bytes in between — they
    left the pipe for the reader's hands. Here the reader takes 10 bytes
    INSIDE the asker's probe and is held in their hand-over: the pipe answers
    0 and the reader is odd again, and ``drained`` must see that the mark MOVED
    across its probe and say no. That identity check is half of what makes the
    POSIX proof exact, and until this case nothing pinned it (review R1: with
    ``return pending == 0`` every other case stayed green).

    The blocking branch (forced here, the native one on win32): the asker must
    never query a pipe another thread is blocked reading (the class
    docstring), so ``drained`` does not call the probe from here at all — it
    answers the reader's own last look, which saw the pipe empty.
    """

    chunks = _Chunks(hold=1)
    reader, write_fd = _reader_over_a_pipe(chunks, branch=branch)
    real_probe = reader._probe
    assert real_probe is not None
    asker = threading.current_thread()
    asked: list[int] = []

    def probe() -> int:
        if threading.current_thread() is not asker:
            return real_probe()
        # Between the asker's mark and this answer: the reader wakes, takes the
        # 10 bytes and is held with them — the pipe is empty again.
        os.write(write_fd, b"x" * 10)
        assert chunks.inside.wait(BOUND), "the reader never took the bytes"
        asked.append(real_probe())
        return asked[-1]

    blocking = takes_the_blocking_branch(branch)
    reader.start()
    try:
        _until(reader.drained, "an idle reader over an empty pipe was never drained")
        reader._probe = probe
        assert reader.drained() is blocking
        assert asked == ([] if blocking else [0])
    finally:
        chunks.let_go()
        _end(reader, write_fd)
    assert b"".join(chunks.got) == (b"" if blocking else b"x" * 10)


@pytest.mark.parametrize("branch", BRANCHES)
def test_a_pin_is_caught_up_once_everything_its_look_saw_is_handed_on(
    branch: str, clock: _HostClock | _CoarseClock
) -> None:
    """The proof for a pipe a helper keeps full, where "drained" never comes.

    1000 bytes are read and held inside ``on_chunk``; 500 more wait in the pipe;
    then the drain pins. The reader has not looked since the pin's instant, so
    nothing is pinned yet and nothing is proven. Released, it hands on the
    1000, looks — holding nothing — and sees 500 pending: the pin is 1500. Then
    it reads those 500 and is HELD AGAIN, inside their hand-over: pinned, with
    only the first read's bytes handed on, it is not caught up and nothing is
    proven. That is the comparison :meth:`~_PipeReader.caught_up` makes, and a
    pin alone would pass it: with ``return pinned is not None`` (review R6) this
    case used to stay green, because the 500 were handed on microseconds after
    the look that pinned them and the not-yet state was never observed. Only
    once they are handed on is it caught up. Later bytes change neither,
    because the pin covers what was written before the look, which is all a
    drain asks about.

    ON A COARSE CLOCK the blocking branch's look before its first read — taken
    before any byte was written — is dated in the instant's own tick, and it
    must still pin nothing: dated AT the instant is not dated after it. With
    ``>=`` it pinned 0 there, caught up at once with all 1500 B undelivered —
    ``assert 0 is None`` on both windows-latest legs of CI run 35952321924, and
    on every host in the ``coarse`` ids. So before it lets the reader go, the
    case waits until the clock has moved past the instant, as a drain's own
    looks come ticks after the exit it pins at.
    """

    chunks = _Chunks(hold=2)
    reader, write_fd = _reader_over_a_pipe(chunks, branch=branch)
    reader.start()
    try:
        os.write(write_fd, b"a" * 1000)
        assert chunks.inside.wait(BOUND), "the reader never took the first chunk"
        os.write(write_fd, b"b" * 500)
        instant = clock.read()
        reader.pin_after(instant)
        assert reader.pinned is None, (
            "a look dated no later than the pin's instant pinned — it came before the 1500 B "
            "were written, so the pin reads as caught up with none of them handed on"
        )
        assert reader.caught_up() is False
        assert reader.proven() is False

        clock.move_past(instant)
        chunks.release_and_hold_the_next()
        assert chunks.inside.wait(BOUND), "the reader never took what its look saw"
        # The look that pinned came before this read, so the pin is in place.
        # ``len(chunks.got[0])`` rather than 1000: one read took the first
        # write on every host measured, and the case must not rest on it.
        assert reader.pinned == 1500
        assert reader.handed_on == len(chunks.got[0]) < 1500
        assert reader.caught_up() is False
        assert reader.proven() is False

        chunks.release.set()
        _until(reader.caught_up, "the reader never caught up with its pin")
        assert reader.pinned == 1500
        assert reader.handed_on == 1500

        os.write(write_fd, b"c" * 7)
        _until(lambda: reader.handed_on == 1507, "the later bytes were never handed on")
        assert reader.pinned == 1500
        assert reader.caught_up() is True
    finally:
        chunks.let_go()
        _end(reader, write_fd)

    assert b"".join(chunks.got) == b"a" * 1000 + b"b" * 500 + b"c" * 7


def test_a_look_already_taken_after_the_instant_pins_at_once(
    clock: _HostClock | _CoarseClock,
) -> None:
    """The blocking branch looks before every read, so the drain may find its pin made.

    That branch parks in ``read1`` right after a look, and a drain that pins
    afterwards would otherwise wait for a look that comes only with the next
    chunk. So :meth:`~_PipeReader.pin_after` takes the latest look when it is
    already late enough: here the reader hands on 10 bytes, looks (0 pending)
    and parks, and the pin is those 10 — caught up the moment it is asked.

    "Late enough" is dated AFTER the instant, never in its tick, so the case
    lets the clock move past the instant before it writes: the look that
    follows the hand-over is then later on any clock. Without that wait a
    coarse clock dates it AT the instant, and it pins nothing — correctly.
    """

    chunks = _Chunks(hold=0)
    reader, write_fd = _reader_over_a_pipe(chunks, branch="win32")
    reader.start()
    try:
        instant = clock.read()
        clock.move_past(instant)
        os.write(write_fd, b"y" * 10)
        _until(lambda: reader.handed_on == 10, "the chunk was never handed on")
        _until(lambda: reader._mark[1] > instant and reader._mark[0] % 2 == 0, "no second look")
        reader.pin_after(instant)
        assert reader.pinned == 10
        assert reader.caught_up() is True
    finally:
        _end(reader, write_fd)


@pytest.mark.parametrize("branch", BRANCHES)
def test_a_later_instant_voids_a_pin_taken_before_it(
    branch: str, clock: _HostClock | _CoarseClock
) -> None:
    """A pin counts only after the LATEST instant asked for (round 6, the second cross-review's N1).

    One kill leg at the bash tool stamps two instants. The ``proc.wait`` worker
    stamps the ROOT's reap, which an abort's or a cancel's ladder brings about
    from inside itself, and the kill stamp follows the whole ladder — on win32
    ``taskkill /T /F`` and then ``TerminateJobObject``, with a job member the
    walk cannot reach writing on between the two. Round 4's rule moved the
    instant and KEPT a pin already taken, so a pin from a look between the reap
    and the kill covered only what was written before the root died, and the
    drain could end on it with the member's last bytes in the reader's hands.

    Here the reader pins after a first instant — 100 B, caught up — and the
    same instant asked again keeps that pin (the exit leg's worker, then its
    drain). Then the reader takes 1000 B more and is held inside their
    hand-over: still caught up with that pin, and rightly, for what was written
    before the first instant. A second instant, read after the 1000 B were
    written, voids it — nothing is pinned, caught up or proven while they are
    in the reader's hands; round 4's rule answered caught up there. The first
    instant asked again after it, as a worker whose stamp lands last would,
    changes nothing: the instant only moves forward. Let go, the reader's next
    look pins all 1100 B, and it catches up.

    The blocking branch looks once before any pin, so the case waits for that
    look before it reads the first instant: dated no later than the instant, it
    pins nothing. ON A COARSE CLOCK the second instant is read in the tick of
    the look that pinned — equal, so not after it — and the strict test voids
    that pin too.
    """

    got: list[bytes] = []
    inside = threading.Event()
    release = threading.Event()

    def on_chunk(chunk: bytes) -> None:
        if chunk.startswith(b"b"):
            inside.set()
            release.wait(BOUND)
        got.append(chunk)

    reader, write_fd = _reader_over_a_pipe(on_chunk, branch=branch)
    reader.start()
    try:
        if takes_the_blocking_branch(branch):
            _until(lambda: reader._mark[3] == 0, "the reader never looked at the empty pipe")
        first = clock.read()
        reader.pin_after(first)
        clock.move_past(first)
        os.write(write_fd, b"a" * 100)
        _until(
            lambda: reader.handed_on == 100 and reader.caught_up(),
            "the reader never caught up with the first instant's pin",
        )
        assert reader.pinned == 100
        reader.pin_after(first)
        assert reader.pinned == 100, "the same instant asked again dropped the pin it had"

        os.write(write_fd, b"b" * 1000)
        assert inside.wait(BOUND), "the reader never took the 1000 B"
        assert reader.handed_on == 100
        assert reader.caught_up() is True

        second = clock.read()
        reader.pin_after(second)
        assert reader.pinned is None, (
            "a pin from a look no later than the later instant still counted — it covers "
            "nothing written between that look and the instant: here the 1000 B the reader "
            "holds"
        )
        assert reader.caught_up() is False
        assert reader.proven() is False

        reader.pin_after(first)
        assert reader.pinned is None, "an earlier instant asked last moved the instant back"
        assert reader.caught_up() is False

        clock.move_past(second)
        release.set()
        _until(reader.caught_up, "no look after the later instant pinned")
        assert reader.pinned == 1100
        assert reader.handed_on == 1100
    finally:
        release.set()
        _end(reader, write_fd)

    assert b"".join(got) == b"a" * 100 + b"b" * 1000


class _GatedRead:
    """A real pipe's ``read1`` whose FIRST call waits for ``gate``; ``fileno`` passes through."""

    def __init__(self, inner: Any, gate: threading.Event) -> None:
        self._inner = inner
        self._gate = gate
        self.parked = threading.Event()
        self._first = True

    def read1(self, size: int) -> bytes:
        if self._first:
            self._first = False
            self.parked.set()
            self._gate.wait(BOUND)
        return self._inner.read1(size)

    def fileno(self) -> int:
        return self._inner.fileno()

    def close(self) -> None:
        self._inner.close()


def test_a_look_is_dated_before_it_asks_the_pipe(clock: _HostClock | _CoarseClock) -> None:
    """A look that began before the pin's instant never pins, however late it answers.

    The blocking branch looks before every read, so a look can straddle the
    drain's :meth:`~_PipeReader.pin_after`: begun before the instant, answered
    after it. What it saw is the pipe BEFORE the instant — empty here, though
    100 bytes are written before the instant — so it must not pin: a pin of 0
    reads as caught up with those 100 bytes still in the pipe. Two things
    hold that, and until this case neither was pinned (review R2 and R5, every
    other case green under each): the look is dated BEFORE its probe (the
    critic's point — a date taken after the probe would be later than what it
    saw), and a look pins only when that date is LATER than the instant. Equal
    is not later: on a coarse clock this look is dated in the instant's own
    tick, and ``>=`` pinned 0 here — ``assert 0 is None`` on both windows-latest
    legs of CI run 35952321924, and on every host in the ``coarse`` id.

    The blocking branch on every host: POSIX looks only once a pin has been
    asked for (:meth:`~_PipeReader._look`), so its looks cannot predate one.
    ``drained`` answers yes here, and that is the stated win32 residual — the
    bytes arrived after the reader's last empty look — not this case's subject.
    """

    read_fd, write_fd = os.pipe()
    gate = threading.Event()
    stream = _GatedRead(os.fdopen(read_fd, "rb"), gate)
    reader = _PipeReader(cast("IO[bytes]", stream), _ReadState(time.monotonic()), platform="win32")
    real_probe = reader._probe
    assert real_probe is not None
    assert reader._poll_fd is None
    probed = threading.Event()
    answer = threading.Event()

    def slow_probe() -> int:
        seen = real_probe()  # the pipe as it is BEFORE the write below: empty
        probed.set()
        answer.wait(BOUND)
        return seen

    reader._probe = slow_probe
    reader.start()
    try:
        assert probed.wait(BOUND), "the reader never looked"
        os.write(write_fd, b"w" * 100)
        instant = clock.read()
        reader.pin_after(instant)
        answer.set()
        assert stream.parked.wait(BOUND), "the reader never reached its read"
        assert reader.pinned is None, (
            "a look begun before the pin's instant pinned what it saw — the empty pipe, with "
            "the 100 B written since still in it"
        )
        assert reader.caught_up() is False

        # The other half: the read takes the 100 bytes, and the look after it
        # — dated after the instant, once the clock has moved past it — pins
        # them. With the write end still open, so the look sees a live pipe on
        # every host (a Windows look at a broken pipe fails, and pins nothing).
        clock.move_past(instant)
        gate.set()
        _until(reader.caught_up, "a look dated after the instant never pinned")
        assert reader.pinned == 100
    finally:
        gate.set()
        answer.set()
        os.close(write_fd)
        reader.join(BOUND)
    assert reader.handed_on == 100


def test_a_blocking_look_that_saw_bytes_proves_nothing() -> None:
    """The blocking branch's "drained" is "even, AND that look saw 0" — this pins the 0.

    That branch's asker may not query the pipe, so what the reader's own last
    look SAW is the whole proof there. Here 100 bytes are in the pipe before
    the reader starts: its first look sees them, publishes an EVEN mark — it
    holds nothing yet — and the reader is held at the ``read1`` that would take
    them. Holding nothing over a pipe that is NOT empty is not drained, and
    with no pin asked for nothing else proves it either. With ``mark[3] >= 0``
    in place of ``== 0`` this answers yes — and every case these two files had
    before it stayed green under that edit (round-2 review), because no other
    look on this branch sees bytes that then stay in the pipe.
    ``tests/tools/test_bash_drain_asks_the_pipe.py`` shows what it costs end to
    end.
    """

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"q" * 100)
    gate = threading.Event()
    stream = _GatedRead(os.fdopen(read_fd, "rb"), gate)
    reader = _PipeReader(cast("IO[bytes]", stream), _ReadState(time.monotonic()), platform="win32")
    assert reader._poll_fd is None
    reader.start()
    try:
        assert stream.parked.wait(BOUND), "the reader never reached its first read"
        assert reader._mark[0] % 2 == 0
        assert reader._mark[3] == 100, "the look before the read did not see the 100 bytes"
        assert reader.drained() is False
        assert reader.proven() is False
    finally:
        gate.set()
        os.close(write_fd)
        reader.join(BOUND)
    assert reader.handed_on == 100


class _Scripted:
    """``read1`` answers from a script, one held at ``gate_at`` — no fd, no probe."""

    def __init__(self, script: list[bytes], *, gate_at: int | None = None) -> None:
        self._script = script
        self._gate_at = gate_at
        self.gate = threading.Event()
        self.parked = threading.Event()
        self.sizes: list[int] = []

    def read1(self, size: int) -> bytes:
        self.sizes.append(size)
        if self._gate_at is not None and len(self.sizes) - 1 == self._gate_at:
            self.parked.set()
            self.gate.wait(BOUND)
        return self._script.pop(0) if self._script else b""

    def close(self) -> None:
        pass


def test_a_stream_with_nothing_to_ask_reads_as_before_and_proves_nothing() -> None:
    """No fd, no probe: the pre-#260 reads exactly, and no proof at all.

    The scripted doubles ``test_pipe_reader_callbacks.py`` drives keep every
    ``read1(_READ_CHUNK_BYTES)`` they had. What they cannot have is a proof.
    Parked in ``read1`` the phase is EVEN — and would be just as even right
    after ``read1`` had taken bytes, before the reader could flip — so "even"
    alone would be the idle clock's answer again, the rule #260 removed. Until
    round 4 this case pinned it as intended (``drained() is True`` here); the
    cross-review's N2 turned it: a reader that cannot prove anything says so,
    and its drain ends on EOF, on its death or at the hard cap.
    """

    stream = _Scripted([b"one", b"two"], gate_at=2)
    got: list[bytes] = []
    reader = _PipeReader(
        cast("IO[bytes]", stream), _ReadState(time.monotonic()), on_chunk=got.append
    )
    assert reader._probe is None
    assert reader._poll_fd is None
    reader.start()
    try:
        assert stream.parked.wait(BOUND), "the reader never reached its third read"
        assert reader._mark[0] % 2 == 0, "parked in read1 with nothing in its hands"
        assert reader.drained() is False
        assert reader.proven() is False
    finally:
        stream.gate.set()
        reader.join(BOUND)
    assert got == [b"one", b"two"]
    assert stream.sizes == [_READ_CHUNK_BYTES] * 3
    assert reader.drained() is False


class _BreaksAfterOne:
    """A real pipe whose ``read1`` answers one chunk and then RAISES, as a broken pipe may."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.reads = 0

    def read1(self, size: int) -> bytes:
        self.reads += 1
        if self.reads == 1:
            return self._inner.read1(size)
        raise OSError(errno.EPIPE, "the pipe broke under the read")

    def fileno(self) -> int:
        return self._inner.fileno()

    def close(self) -> None:
        self._inner.close()


def test_a_reader_that_ends_on_an_error_is_odd_for_good() -> None:
    """Every way out of ``run`` leaves the phase odd — not only EOF.

    At EOF the read that returned nothing has already made the phase odd, so
    the EOF cases above cannot tell whether ``run``'s ``finally`` flips it. A
    read that RAISES on the blocking branch leaves the phase where the look
    before it put it — EVEN, over a look that saw the pipe empty — and only
    that ``finally`` makes the ended reader odd for good. Without the flip
    (review R4, every other case green) a reader that died on an error would
    read as drained for good. A real pipe, so the reader has a probe and its
    look counts: a stream with nothing to ask is never drained, whatever its
    phase (round 4 moved this case off such a stream for that reason).
    """

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"one")
    stream = _BreaksAfterOne(os.fdopen(read_fd, "rb"))
    reader = _PipeReader(cast("IO[bytes]", stream), _ReadState(time.monotonic()), platform="win32")
    assert reader._probe is not None
    assert reader._poll_fd is None
    reader.start()
    try:
        reader.join(BOUND)
    finally:
        os.close(write_fd)
    assert not reader.is_alive()
    assert stream.reads == 2
    assert reader.chunks == [b"one"]
    assert reader.eof is False, "the except leg, not EOF"
    assert reader._mark[3] == 0, "the look before the failed read did not see the pipe empty"
    assert reader._mark[0] % 2 == 1
    assert reader.drained() is False


def test_a_stream_that_is_not_a_pipe_still_reads() -> None:
    """Building the probe is never an exception out of the constructor.

    ``BytesIO.fileno`` raises ``io.UnsupportedOperation``; the reader is built
    without a probe and reads to EOF as it always did.
    """

    reader = _PipeReader(cast("IO[bytes]", io.BytesIO(b"plain")), _ReadState(time.monotonic()))
    assert reader._probe is None
    reader.start()
    reader.join(BOUND)
    assert reader.chunks == [b"plain"]
    assert reader.eof is True


# === the seam machinery, shared with tests/tools/test_bash_drain_asks_the_pipe.py ==

#: How long a seam holds the reader, from the root's exit: three graces, so the
#: pre-#260 drains' idle rule (0.1 s from the exit) always fired inside it.
HOLD = 0.3

#: Twenty 50-byte lines in one write: small enough for any pipe buffer, so the
#: whole payload is in the pipe or in the reader's hands the moment it is out.
PAYLOAD = b"".join(b"line %02d " % i + b"." * 41 + b"\n" for i in range(20))

#: ``argv``: marker, ``holder``/``none``, ``looked``, the exit code. ``holder``
#: backgrounds a silent helper that inherits the pipe (``stdin`` at ``DEVNULL``
#: and nothing else — the win32 inheritance rule the containment cases state)
#: and announces both pids BEFORE writing, so ``strays`` reaps it whatever the
#: case does. ``looked`` is ``-`` to write at once, or a file to wait for
#: first: :func:`held_reader`'s reader creates it right after its first look,
#: so the pipe was still EMPTY at that look — the order a Windows reader parked
#: in ``ReadFile`` sees, and the only one in which the blocking branch's
#: residual can be pinned. It used to be a 0.2 s sleep, which a reader thread
#: starved for longer — #260's own shape — turned into the other order (round
#: 4). ``@MARK@`` is each file's own.
ROOT_TEMPLATE = """\
import os
import subprocess
import sys
import time

marker, shape, looked, code = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
if shape == "holder":
    helper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", "@MARK@"],
        stdin=subprocess.DEVNULL,
    )
    tmp = marker + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()} {helper.pid}\\n")
    os.replace(tmp, marker)
if looked != "-":
    deadline = time.monotonic() + 15.0
    while not os.path.exists(looked) and time.monotonic() < deadline:
        time.sleep(0.001)
sys.stdout.buffer.write(b"".join(b"line %02d " % i + b"." * 41 + b"\\n" for i in range(20)))
sys.stdout.buffer.flush()
sys.exit(code)
"""


def _is_taskkill(proc: subprocess.Popen[Any]) -> bool:
    """Is ``proc`` the ``taskkill.exe`` a win32 ``hard_kill`` runs, rather than the root?"""

    args = proc.args
    first = args[0] if isinstance(args, (list, tuple)) and args else args
    return str(first).replace("\\", "/").rsplit("/", 1)[-1].lower().startswith("taskkill")


class Gate:
    """Opens ``hold`` seconds after the root's exit, as ``Popen.wait`` saw it — or never.

    The spy stamps the FIRST wait that returns: the ordinary exit on the
    success path, the reap on a kill leg (a timed-out wait raises before it
    gets here). Except ``taskkill.exe``'s own wait: on win32 ``hard_kill``
    runs it through ``subprocess.run(timeout=5)`` BEFORE the root's reap, and
    its ``communicate`` calls the same class-wide ``Popen.wait`` — stamped, it
    would date a kill leg from ``taskkill``'s return instead of the reap
    (review; nothing here was measured on Windows).

    ``hold=None`` stamps and starts no timer: the gate opens only when the case
    opens it, after the call under test has RETURNED (round 4, the
    cross-review's S1). That is for every case whose verdict is that the drain
    ended WITHOUT the reader — the pinned residual and the hard caps. On a
    timer those raced it: a runner that stopped the process across the
    verdict opened the gate first, the reader delivered, and the case failed
    as if the residual had closed — measured with the whole pytest process
    stopped 0.3 s from 50 ms after the exit, the four forced-win32 residual
    ids failed 15 of 16 runs, and with a 0.8 s stop across the caps every
    hard-cap run failed. A case whose verdict is that the drain WAITED for
    the reader keeps the timer: the drain cannot end without the reader
    before its hard cap — 1.7 s past a :data:`HOLD` timer, 1.0 s past the
    1.0 s ones — and a stall that carries it past that cap is met by the late
    grace. Where a verdict needs the drain to have acted while the reader was
    still held, the drain's own asks let the reader go instead of a timer
    (the parked-grace case; since round 5 the exit-tick case, whose guard a
    stall let the timer beat).
    """

    def __init__(self, hold: float | None) -> None:
        self._hold = hold
        self.event = threading.Event()
        self.exited_at: float | None = None

    def arm_from_the_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_wait = subprocess.Popen.wait
        hold = self._hold

        def spy_wait(proc: subprocess.Popen[Any], timeout: float | None = None) -> int:
            code = real_wait(proc, timeout=timeout)
            if self.exited_at is None and not _is_taskkill(proc):
                self.exited_at = time.monotonic()
                if hold is not None:
                    timer = threading.Timer(hold, self.event.set)
                    timer.daemon = True
                    timer.start()
            return code

        monkeypatch.setattr(subprocess.Popen, "wait", spy_wait)

    def hold(self) -> None:
        """Park the calling reader thread until the gate opens.

        Never raises: this runs on the reader thread, where an assertion would
        only reach ``threading.excepthook``. The case's byte assertion is what
        reports a gate that never opened.
        """

        self.event.wait(BOUND)


class _HoldAround:
    """The blocking branch's two holds: before the first ``read1``, or after one.

    Only that branch calls ``read1`` — the POSIX reader reads its fd itself,
    and :func:`hold_the_fd_read` holds it there. ``fileno`` passes through, so
    the reader builds its probe on the real pipe.
    """

    def __init__(self, inner: Any, gate: Gate, where: str) -> None:
        self._inner = inner
        self._gate = gate
        self._where = where
        self._first = True

    def read1(self, size: int) -> bytes:
        if self._where == "preread" and self._first:
            self._first = False
            self._gate.hold()
        chunk = self._inner.read1(size)
        if self._where == "inread" and chunk:
            self._gate.hold()
        return chunk

    def fileno(self) -> int:
        return self._inner.fileno()

    def close(self) -> None:
        self._inner.close()


def _pending(fd: int) -> int:
    """FIONREAD — only ever called from the POSIX reader's read, so POSIX only."""

    import fcntl
    import termios

    answer = fcntl.ioctl(fd, termios.FIONREAD, bytes(4))  # pyright: ignore[reportAttributeAccessIssue]
    return int.from_bytes(answer, sys.byteorder, signed=True)


def hold_the_fd_read(
    monkeypatch: pytest.MonkeyPatch, gate: Gate, where: str, fds: set[int]
) -> None:
    """The same two holds for the POSIX reader, through ``_process_tree._read_fd``.

    ``preread`` holds the first read that WOULD take bytes — the reader's phase
    is already odd and the bytes are still in the pipe; a read that would find
    the pipe empty is let through, so the reader can wait in ``poll`` first.
    ``inread`` holds after a read that took bytes. ``raising=False`` so a tree
    without the seam still runs the case: its reader reads through ``read1``,
    where :class:`_HoldAround` holds it instead.
    """

    real_read = os.read
    held: list[bool] = []

    def read(fd: int, size: int) -> bytes:
        if fd in fds and where == "preread" and not held and _pending(fd) > 0:
            held.append(True)
            gate.hold()
        data = real_read(fd, size)
        if fd in fds and where == "inread" and data:
            gate.hold()
        return data

    monkeypatch.setattr(_process_tree, "_read_fd", read, raising=False)


def held_reader(
    where: str,
    gate: Gate,
    *,
    platform: str | None,
    seen: list[_PipeReader],
    fds: set[int],
    looked: Path | None = None,
) -> type[_PipeReader]:
    """A :class:`_PipeReader` held at ``where``, on ``platform``'s branch.

    ``late`` holds before the reader's first read, ``chunk`` inside its
    ``on_chunk`` (a reader without one is not held), ``preread``/``inread``
    around its read. ``platform`` is passed only when given, so the native
    reader of a tree without the seam still builds. ``looked``, when given, is
    created right after the FIRST reader built — stdout's, at both sites — has
    taken its first look (:data:`ROOT_TEMPLATE`'s ``looked``); on the blocking
    branch that look is a real one, over the pipe the root has not yet written.
    """

    class Held(_PipeReader):
        def __init__(self, stream: Any, state: Any, **kwargs: Any) -> None:
            fds.add(stream.fileno())
            if where in ("preread", "inread"):
                stream = _HoldAround(stream, gate, where)
            on_chunk = kwargs.get("on_chunk")
            if where == "chunk" and on_chunk is not None:

                def held_chunk(chunk: bytes) -> None:
                    gate.hold()
                    on_chunk(chunk)

                kwargs["on_chunk"] = held_chunk
            if platform is not None:
                kwargs["platform"] = platform
            super().__init__(stream, state, **kwargs)
            self.announce = looked if not seen else None
            #: Every answer :meth:`proven` gave a drain, and when, in order.
            self.asks: list[tuple[float, bool]] = []
            seen.append(self)

        def run(self) -> None:
            if where == "late":
                gate.hold()
            super().run()

        def proven(self) -> bool:
            answer = super().proven()
            self.asks.append((time.monotonic(), answer))
            return answer

        def _look(self, *, always: bool) -> None:
            super()._look(always=always)
            announce, self.announce = self.announce, None
            if announce is not None:
                announce.write_text("looked", encoding="utf-8")

    return Held


def takes_the_blocking_branch(branch: str) -> bool:
    """Is ``branch`` the one with the residual — asked for, or this host's own?

    Keyed on what was ASKED, not on what the reader did, so a POSIX reader that
    silently fell back to blocking reads fails the native ids instead of being
    graded against the residual it would then have.
    """

    return branch == "win32" or sys.platform == "win32"


#: A root that writes its own line FIRST and then backgrounds a helper that
#: writes 64 KiB blocks into the inherited pipe for ``life`` seconds (argv:
#: marker, life), announcing both pids for ``strays``. The helper is only as
#: fast as the pipe lets it be: against a reader slower than it, it spends its
#: life blocked on a full pipe. ``@MARK@`` is each file's own.
FULL_PIPE_ROOT_TEMPLATE = """\
import os
import subprocess
import sys

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
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {helper.pid}\\n")
os.replace(tmp, marker)
"""

#: How long a slowed reader rests after every read that took bytes.
SLOW_READ_PAUSE = 0.02


class DrainLog:
    """A drain's own sequence — each look it takes, each answer it gets — to judge it by.

    Patched in as the site's ``time`` (this object) and ``_exit_drain_cap``
    (:meth:`exit_drain_cap`), it starts on the thread that computes the soft
    cap, records that cap as :attr:`deadline`, and from then logs every clock
    reading on that thread as a LOOK. That thread is the drain's — the cap is
    computed right before the drain runs, on the thread it runs on — and a
    drain reads the clock once per turn of its loop, then decides. The readers
    :func:`slow_the_reader` builds with this log append every ``proven()``
    answer to the same list, from the same thread, so :attr:`events` is the
    drain's order exactly: each look, then the ask it made there, if any.
    Readings on other threads — a reader's, the ``proc.wait`` worker's — are
    not the drain's and are not logged; anything else ``time`` offers passes
    through.

    Why (round 5): a verdict on the wall clock races the drain against
    whatever a stall stops. A stall moves every look but does not change
    which of them ask, so a verdict on what the drain did, rather than when,
    does not turn red because the runner stalled.
    """

    def __init__(self) -> None:
        #: ``("look", at)`` and ``("ask", at, reader, answer, pinned, handed_on, eof)``.
        self.events: list[tuple[Any, ...]] = []
        #: The soft cap the drain was handed: the caller's deadline, as the drain sees it.
        self.deadline: float | None = None
        self._thread: threading.Thread | None = None

    def monotonic(self) -> float:
        now = time.monotonic()
        if threading.current_thread() is self._thread:
            self.events.append(("look", now))
        return now

    def exit_drain_cap(self, real: Callable[..., float]) -> Callable[..., float]:
        """``real`` (the site's ``_exit_drain_cap``), recording its answer and starting the log."""

        def cap(exited_at: float, *, started_at: float, timeout: float | None) -> float:
            answer = real(exited_at, started_at=started_at, timeout=timeout)
            self.deadline = answer
            self._thread = threading.current_thread()
            return answer

        return cap

    def ask(self, reader: _PipeReader, answer: bool) -> None:
        """Log ``reader``'s answer, with what the second proof compares, read just after it."""

        self.events.append(
            ("ask", time.monotonic(), reader, answer, reader.pinned, reader.handed_on, reader.eof)
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


def the_deadline_ended_it_on_the_second_proof(
    log: DrainLog, reader: _PipeReader, *, what: str
) -> None:
    """The full-pipe cases' verdict, on the drain's own sequence (:class:`DrainLog`).

    At every look the drain took at or after its deadline it asked ``reader``
    for a proof; its last act was an ask; ``reader``'s last answer was yes,
    on the SECOND proof — pinned, and everything the pin covers handed on —
    with the pipe not yet at EOF, so the helper was cut, not waited for; and
    that yes was the FIRST one, so the proof ended the drain the moment it
    came. Without the last clause a drain that is told yes at the deadline and
    carries on to its hard cap, where it asks again and is told yes again,
    passes every other clause (the round-5 verifier's sabotage Q1: 2.047-2.083
    s against a 1.0 s deadline, "12 asks, 0 refused"). An end before the
    deadline — the idle rule, after a stall kept the reader from reading for a
    grace — passes when it is that proof too. The sequence goes out as a
    warning first, ``what`` naming the case.
    """

    events = log.events
    deadline = log.deadline
    assert deadline is not None, "the drain never took its soft cap — the case measured nothing"
    asks = [event for event in events if event[0] == "ask" and event[2] is reader]
    looks = [event[1] for event in events if event[0] == "look"]
    last = asks[-1] if asks else None
    summary = (
        f"{len(looks)} looks, {sum(1 for at in looks if at >= deadline)} of them at or past the "
        f"deadline; {len(asks)} asks, {sum(1 for ask in asks if not ask[3])} refused; the last "
        f"{(last[1] - deadline) if last else 0:+.3f}s from the deadline, answered "
        f"{last[3] if last else None} with pinned={last[4] if last else None} "
        f"handed_on={last[5] if last else None} eof={last[6] if last else None}"
    )
    warnings.warn(f"#260 {what}: {summary} on {sys.platform}", stacklevel=2)
    for index, event in enumerate(events):
        if event[0] != "look" or event[1] < deadline:
            continue
        after = events[index + 1] if index + 1 < len(events) else None
        assert after is not None and after[0] == "ask" and after[2] is reader, (
            f"the drain looked {event[1] - deadline:.3f}s past its deadline and asked its reader "
            f"nothing there — the deadline did not end a drain whose pipe never empties, which "
            f"went on toward its hard cap, or ended at it without asking ({summary})"
        )
    assert last is not None, (
        f"the drain never asked its reader for a proof — it ended on the clock alone ({summary})"
    )
    assert events[-1][0] == "ask", (
        f"the drain's last act was a look, not an ask — it ended without a proof ({summary})"
    )
    _kind, _at, _reader, answer, pinned, handed_on, eof = last
    assert answer is True, (
        f"the drain's last answer from its reader was no — it ended without a proof, at its "
        f"hard cap or on EOF: the pinned look never caught up ({summary})"
    )
    assert pinned is not None and handed_on >= pinned, (
        f"the drain ended on a proof, but not on the second one ({summary})"
    )
    assert not any(ask[3] for ask in asks[:-1]), (
        f"the reader proved it before the drain's last ask and the drain carried on — a yes "
        f"must end the drain at once ({summary})"
    )
    assert eof is False, (
        f"the pipe was at EOF when the drain ended — the helper was not cut; the case measured "
        f"nothing ({summary})"
    )


class _SlowRead:
    """A stream whose ``read1`` rests after every read that took bytes (the blocking branch)."""

    def __init__(self, inner: Any, pause: float) -> None:
        self._inner = inner
        self._pause = pause

    def read1(self, size: int) -> bytes:
        chunk = self._inner.read1(size)
        if chunk:
            time.sleep(self._pause)
        return chunk

    def fileno(self) -> int:
        return self._inner.fileno()

    def close(self) -> None:
        self._inner.close()


def slow_the_reader(
    monkeypatch: pytest.MonkeyPatch,
    owner: Any,
    *,
    pause: float = SLOW_READ_PAUSE,
    log: DrainLog | None = None,
) -> list[_PipeReader]:
    """Make every reader ``owner`` builds slower than any helper, on either branch.

    ``owner`` is the module whose ``_PipeReader`` the site under test looks up —
    ``_process_tree`` for :func:`run_contained`, ``tools/bash.py`` for ``exec``
    — and the readers are returned in the order the site built them. The POSIX
    reader reads its fd through ``_process_tree._read_fd``; the blocking branch
    reads ``read1``. Both rest ``pause`` (:data:`SLOW_READ_PAUSE` unless given)
    after a read that took bytes — and keep resting after the call returns, so
    the helper stays blocked on its full pipe rather than spinning until
    ``strays`` ends it. With a ``log``, every ``proven()`` answer goes into it.
    """

    built: list[_PipeReader] = []
    fds: set[int] = set()
    real_read = os.read

    def read(fd: int, size: int) -> bytes:
        data = real_read(fd, size)
        if data and fd in fds:
            time.sleep(pause)
        return data

    class Slow(_PipeReader):
        def __init__(self, stream: Any, state: Any, **kwargs: Any) -> None:
            fds.add(stream.fileno())
            super().__init__(cast("IO[bytes]", _SlowRead(stream, pause)), state, **kwargs)
            built.append(self)

        def proven(self) -> bool:
            answer = super().proven()
            if log is not None:
                log.ask(self, answer)
            return answer

    monkeypatch.setattr(_process_tree, "_read_fd", read, raising=False)
    monkeypatch.setattr(owner, "_PipeReader", Slow)
    return built


# === the seam half: run_contained's drain ===================================


@pytest.mark.parametrize("holder", [False, True], ids=["eof", "holder"])
@pytest.mark.parametrize("where", ["late", "inread"])
def test_run_contained_waits_for_bytes_the_reader_has_not_handed_on(
    tmp_path: Path,
    strays: list[int],
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    holder: bool,
) -> None:
    """The root's own 1000 bytes come back though the reader was held past the grace.

    ``run_contained``'s drain polls, on the calling thread, so its reader's
    starvation is the same skew as the bash tool's: the idle rule met a grace
    already spent and returned — ``returncode == 0``, ``stdout=b''`` on the base
    tree, 0.1 s after the exit, for every id here. ``late`` holds the reader
    before its first read (the bytes are in the pipe), ``inread`` inside the
    read that took them.

    Native reader only: ``run_contained`` builds its readers through
    ``_start_reader`` and passes no platform. So on ``windows-latest`` this is
    the blocking branch, and ``inread`` there is the stated residual — pinned
    as ``b""``, exactly as the bash tool's forced-win32 ids pin it. ``holder``
    also pins the end on the pipe: one grace after the hold, not the 2.0 s cap.

    The residual's reader is let go only after the call returns (:class:`Gate`,
    ``hold=None``), so no stall can let it deliver first. What pins it is the
    PROOF the drain took from it — held, with the bytes in its hands, it said
    "proven" — and not the empty ``stdout`` alone, because a closed residual
    would end at this site's hard cap with ``b""`` too, silently.
    """

    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays) if holder else None
    residual = where == "inread" and takes_the_blocking_branch("native")
    gate = Gate(None if residual else HOLD)
    gate.arm_from_the_exit(monkeypatch)
    seen: list[_PipeReader] = []
    fds: set[int] = set()
    looked = tmp_path / "looked" if where == "inread" else None
    monkeypatch.setattr(
        _process_tree,
        "_PipeReader",
        held_reader(where, gate, platform=None, seen=seen, fds=fds, looked=looked),
    )
    if where == "inread":
        hold_the_fd_read(monkeypatch, gate, where, fds)
    root = tmp_path / "root_260.py"
    root.write_text(ROOT_TEMPLATE.replace("@MARK@", MARK), encoding="utf-8")
    shape = "holder" if holder else "none"
    argv = [sys.executable, str(root), str(marker), shape, str(looked or "-"), "0"]
    started = time.monotonic()
    try:
        done = run_contained(argv, timeout=30.0)
    finally:
        returned = time.monotonic()
        gate.event.set()
        if registrar is not None:
            registrar.settle()
    after_exit = returned - (gate.exited_at or started)
    warnings.warn(
        f"#260 run_contained seam={where} holder={holder}: returned {after_exit:.3f}s after "
        f"the exit, stdout {len(done.stdout)}/{len(PAYLOAD)} B on {sys.platform}",
        stacklevel=1,
    )

    assert seen, "the held reader was never built — the case measured nothing"
    assert gate.exited_at is not None, "the root's exit was never seen — the case measured nothing"
    assert done.returncode == 0
    if residual:
        answers = [answer for _at, answer in cast("Any", seen[0]).asks]
        assert done.stdout == b"" and True in answers, (
            f"the blocking branch's residual closed — the held stdout reader answered "
            f"{answers} and the drain returned {after_exit:.3f}s after the exit; update this "
            f"pin with the bash tool's"
        )
        return
    assert done.stdout == PAYLOAD, (
        f"the drain ended {after_exit:.3f}s after the exit with "
        f"{len(done.stdout)}/{len(PAYLOAD)} bytes of the root's own output"
    )
    if holder:
        assert after_exit < 1.0, (
            f"returned {after_exit:.3f}s after the exit — a parked reader over an empty pipe "
            f"did not end the drain, which is the idle rule gone back to the 2.0 s cap"
        )


def test_run_contained_holds_a_deadline_only_as_far_as_its_hard_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller's deadline is a SOFT cap now; ``DRAIN_CAP_SECONDS`` past the exit is the hard one.

    ``timeout=1.0`` puts the deadline a second after the start, and the root
    exits at once. The reader is held before its first read until the call
    returns (:class:`Gate`, ``hold=None``), so no proof can come: the drain
    must pass the deadline — cutting the root's own bytes there is exactly what
    #260 removed — and stop at the hard cap, 2.0 s past the exit, with the
    bytes still undelivered. That end is SILENT here (``returncode == 0``,
    ``stdout == b""``): a ``CompletedProcess`` has nowhere to say it, and the
    ADR records that as the one loss this site keeps on POSIX — on win32 the
    blocking branch's residual is a second, pinned by the ``inread`` ids above.
    The lower bound is what a call site that dropped ``hard_until=`` fails —
    its drain would end at the deadline, as before #260. It is also the price,
    stated: a starved reader can hold a ``timeout=1.0`` call to 2.0 s past the
    exit.
    """

    gate = Gate(None)
    gate.arm_from_the_exit(monkeypatch)
    seen: list[_PipeReader] = []
    monkeypatch.setattr(
        _process_tree, "_PipeReader", held_reader("late", gate, platform=None, seen=seen, fds=set())
    )
    root = tmp_path / "root_260.py"
    root.write_text(ROOT_TEMPLATE.replace("@MARK@", MARK), encoding="utf-8")
    argv = [sys.executable, str(root), str(tmp_path / "pids.txt"), "none", "-", "0"]
    try:
        done = run_contained(argv, timeout=1.0)
    finally:
        returned = time.monotonic()
        gate.event.set()
    assert gate.exited_at is not None, "the root's exit was never seen — the case measured nothing"
    after_exit = returned - gate.exited_at
    warnings.warn(
        f"#260 run_contained hard cap: returned {after_exit:.3f}s after the exit on {sys.platform}",
        stacklevel=1,
    )

    assert seen, "the held reader was never built — the case measured nothing"
    assert done.returncode == 0
    assert done.stdout == b""
    assert DRAIN_CAP_SECONDS <= after_exit < DRAIN_CAP_SECONDS + 0.5


#: How long the late-look cases keep a drain away past its hard cap.
LATE = DRAIN_CAP_SECONDS + 0.5

#: A shorter absence, but longer than a grace: late, and — from the exit —
#: nowhere near the cap.
AWAY_AGAIN = 3 * EXIT_DRAIN_SECONDS


class _KeepsTheDrainAway:
    """``_process_tree``'s ``time``, whose ``sleep`` keeps :func:`_drain` away.

    ``stalls`` maps a poll's number to how long that poll sleeps instead of
    :data:`_DRAIN_POLL_SECONDS`, on the calling thread: the drain comes back
    late, as a process stopped or descheduled across that poll does.
    ``back_at`` records when each of those polls returned. Anything else
    ``time`` offers passes through.
    """

    def __init__(self, stalls: dict[int, float]) -> None:
        self._stalls = stalls
        self.polls = 0
        self.back_at: list[float] = []

    def sleep(self, seconds: float) -> None:
        if seconds == _DRAIN_POLL_SECONDS and threading.current_thread() is threading.main_thread():
            self.polls += 1
            stall = self._stalls.get(self.polls)
            if stall is not None:
                time.sleep(stall)
                self.back_at.append(time.monotonic())
                return
        time.sleep(seconds)

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@pytest.mark.parametrize("away", ["past_the_cap", "twice", "early"])
def test_run_contained_gives_a_late_look_at_its_hard_cap_one_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, away: str
) -> None:
    """A drain kept away past its hard cap asks its readers for one grace more — once, and only there.

    Round 4 (the cross-review's S2, found at this site by measurement). The
    hard cap is a clock reading, and a drain kept away past it — its thread,
    or the whole process, stopped — used to return at its first look back, in
    the instant the stall gave the readers back their CPU. Measured with the
    readers starved until the stall ended and the drain kept away 2.5 s from
    its third poll: 20 of 20 rounds lost the root's 1000 B, silently; 0 of 20
    once a late look within a grace of the hard cap, or past it, moves the end
    one grace past that look — once per drain.

    ``past_the_cap`` and ``twice`` hold the readers until the call returns,
    so nothing can prove and the case pins the mechanism, deterministically:
    kept away :data:`LATE` from its third poll, the drain asks again for at
    least half a grace before it returns, where it used to return without
    asking at all. ``twice`` keeps it away again, :data:`AWAY_AGAIN`, from
    the poll after that: the grace is given once, so it comes back past the
    moved end and returns without asking. ``early`` keeps it away only
    :data:`AWAY_AGAIN` — late, far from the cap — and lets the readers go 1.0 s
    after the exit: the drain kept its 2.0 s end and waited for them. A late
    look that set the end a grace past itself whatever the end was would pull
    it IN, to about 0.4 s, and return with nothing.
    """

    stalls = {3: LATE, 4: AWAY_AGAIN} if away == "twice" else {3: LATE}
    if away == "early":
        stalls = {3: AWAY_AGAIN}
    gate = Gate(1.0 if away == "early" else None)
    gate.arm_from_the_exit(monkeypatch)
    seen: list[_PipeReader] = []
    monkeypatch.setattr(
        _process_tree, "_PipeReader", held_reader("late", gate, platform=None, seen=seen, fds=set())
    )
    clock = _KeepsTheDrainAway(stalls)
    monkeypatch.setattr(_process_tree, "time", clock)
    root = tmp_path / "root_260.py"
    root.write_text(ROOT_TEMPLATE.replace("@MARK@", MARK), encoding="utf-8")
    argv = [sys.executable, str(root), str(tmp_path / "pids.txt"), "none", "-", "0"]
    try:
        done = run_contained(argv, timeout=30.0)
    finally:
        returned = time.monotonic()
        gate.event.set()
    assert gate.exited_at is not None, "the root's exit was never seen — the case measured nothing"
    assert clock.back_at, "the drain was never kept away — the case measured nothing"
    back_at = clock.back_at[0]
    asks = [at for reader in seen for at, _answer in cast("Any", reader).asks]
    late = [at for at in asks if at >= back_at]
    warnings.warn(
        f"#260 run_contained late look ({away}): back {back_at - gate.exited_at:.3f}s after the "
        f"exit, {len(late)} asks after it over {(max(late) - back_at) if late else 0:.3f}s, "
        f"returned {returned - back_at:.3f}s after it, stdout {len(done.stdout)} B on "
        f"{sys.platform}",
        stacklevel=1,
    )

    assert done.returncode == 0
    if away == "early":
        assert done.stdout == PAYLOAD, (
            f"{len(done.stdout)}/{len(PAYLOAD)} B — a late look far from the hard cap moved it"
        )
        return
    assert back_at >= gate.exited_at + DRAIN_CAP_SECONDS, "the drain was not kept away past its cap"
    assert done.stdout == b""
    assert late, "the drain gave up at its late look without asking its readers again"
    if away == "twice":
        assert len(clock.back_at) == 2, (
            "the drain was not kept away a second time — the case measured nothing"
        )
        again = clock.back_at[1]
        # What the verdict needs of the second absence (round 6): that it
        # carried the drain past the end its late look moved, one grace after
        # that look. The look comes before the first ask after it, on this
        # thread and this clock, so an ``again`` a grace past that ask is past
        # the moved end by the drain's own readings — no tick of slack needed.
        # ``again - back_at >= AWAY_AGAIN`` asked instead for the whole sleep
        # between two readings: both windows-latest legs of CI run 36000198338
        # read 0.297 s of it on their 15.6 ms clock.
        assert again >= min(late) + EXIT_DRAIN_SECONDS, (
            f"the drain came back {again - min(late):.3f}s after its first ask past the late "
            f"look — not a grace, so not past the end that look moved: the case measured nothing"
        )
        assert not [at for at in asks if at >= again], (
            "the drain asked again after it came back a second time — a second late look "
            "bought a second grace"
        )
        return
    assert max(late) - back_at >= EXIT_DRAIN_SECONDS / 2
    assert returned - back_at >= EXIT_DRAIN_SECONDS / 2


def test_run_contained_ends_a_pipe_that_never_empties_at_its_deadline_on_the_pinned_look(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deadline still bounds a drain whose pipe never empties — on the second proof.

    ``test_bash_drain_asks_the_pipe.py``'s full-pipe case, at this site. The
    reader rests 20 ms after every read and the helper writes 64 KiB blocks for
    longer than the drain can last, so the helper is always blocked on a FULL
    pipe and "drained" never holds. What ends the drain at the caller's
    deadline is :meth:`_PipeReader.caught_up`: :func:`_drain` pins every reader
    at the exit, the first look after it records ``handed_on + pending``, and
    the reader has handed that much on long before the deadline — so the
    root's own line is in, and the helper's later blocks are the cut the
    deadline is for.

    The verdict is the drain's own sequence (:class:`DrainLog`,
    :func:`the_deadline_ended_it_on_the_second_proof`): at every poll at or
    after its deadline it asks the stdout reader for a proof, and it ends on
    the first yes, the second proof, with the pipe still open. Until round 5
    it was ``timeout <= elapsed < timeout + 0.5`` on POSIX, and the floor was a
    race: the reader's 20 ms rests had to keep beating the 0.1 s idle timer. A
    runner that stopped the whole process for longer than a grace let the idle
    rule end the drain early — legitimately, on the same caught-up pin — and
    the case failed. That end passes now, because the proof says so; a stall
    that holds the drain past its deadline moves its polls, not which of them
    ask. The sequence holds on every host, win32 included, where the deadline
    is 2.0 s (room for a slower spawn; nothing here was measured on Windows).

    What goes red: without the pin — ``_drain``'s ``pin_after`` loop deleted,
    which left ``test_run_contained_real_processes.py`` and every earlier case
    here green (round-2 review) — every ask at the deadline is refused and the
    call runs to the hard cap, 2.0 s past the exit, where the caller was
    promised its deadline: the hard end returns without asking, so the
    sequence ends on a look. So does a drain that ends on the clock alone, or
    that polls past its deadline without asking.
    """

    timeout = 2.0 if sys.platform == "win32" else 1.0
    marker = tmp_path / "pids.txt"
    registrar = _registrar(marker, strays)
    log = DrainLog()
    monkeypatch.setattr(_process_tree, "time", log)
    monkeypatch.setattr(
        _process_tree, "_exit_drain_cap", log.exit_drain_cap(_process_tree._exit_drain_cap)
    )
    built = slow_the_reader(monkeypatch, _process_tree, log=log)
    root = tmp_path / "full_pipe_root.py"
    root.write_text(FULL_PIPE_ROOT_TEMPLATE.replace("@MARK@", MARK), encoding="utf-8")
    argv = [sys.executable, str(root), str(marker), str(timeout + DRAIN_CAP_SECONDS + 3.0)]
    started = time.monotonic()
    try:
        done = run_contained(argv, timeout=timeout)
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()
    # ``run_contained`` starts stdout's reader first, then stderr's.
    out = built[0] if built else None
    warnings.warn(
        f"#260 run_contained full pipe: timeout={timeout} elapsed={elapsed:.3f}s "
        f"stdout={len(done.stdout)} B on {sys.platform}",
        stacklevel=1,
    )

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    assert out is not None, "the slowed reader was never built — the case measured nothing"
    assert done.returncode == 0
    assert done.stdout.startswith(b"ROOT-DONE\n"), f"the root's own line: {done.stdout[:16]!r}"
    the_deadline_ended_it_on_the_second_proof(log, out, what="run_contained full pipe")
