"""``_PipeReader``'s callback shape (#222 §B). Fake streams, no processes.

WHY THIS FILE EXISTS SEPARATELY. #221 gave the reader one consumer — the list
``run_contained`` joins at the end of a synchronous call. #222 gives it a
second: ``tools/bash.py``'s ``exec`` streams to a model AS the bytes arrive, on
an event loop, so it hands the reader an ``on_chunk``/``on_eof`` pair instead of
collecting ``chunks``. The two properties that shape depends on are cheap to
state and expensive to discover from a real child: the callback reader RETAINS
NOTHING (holding the bytes twice was measured at 535 MB against 285 MB peak RSS
on 200 MB of output — #222 critique SCOPE-6), and ``on_eof`` fires on EVERY way
out of :meth:`_PipeReader.run`, because the bash site's bounded drain waits on
the event that callback sets and a reader that ended through its ``except`` leg
never sets ``eof`` (#221 review HC5).

EVERYTHING HERE IS A FAKE STREAM, deliberately. The pipe half is already
covered against real fds and real children in ``test_run_contained.py`` and the
two ``*_real_processes.py`` files; what is left to pin is the reader's own
control flow, and a scripted ``read1`` reaches the ``except (OSError,
ValueError)`` leg and the third exit — an exception that leg does NOT catch —
which no real pipe can be talked into on demand. It also makes the file
platform-free and sub-second.

EVERY READER IS JOINED. These are daemon threads: one left running would
survive into every later case in the session, and the ``read1`` script is what
guarantees each one ends.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import IO, cast

import pytest
from aelix_ai.utils._process_tree import _READ_CHUNK_BYTES, _PipeReader, _ReadState

#: How long a case waits for a reader that its script has already ended. Long
#: enough that a loaded runner is not the reason a case goes red, short enough
#: that a reader which never ends fails the case instead of the leg.
JOIN_BOUND = 5.0


class _ScriptedStream:
    """``read1`` answers taken from a script. Not a pipe, not an fd.

    Each entry is either the bytes that call hands back or the exception it
    raises; ``b""`` and running off the end are both EOF. ``gate_before`` makes
    one call block until :attr:`gate` is set, which is how the detach case
    stops the reader between two chunks without a sleep.
    """

    def __init__(
        self,
        script: Sequence[bytes | BaseException],
        *,
        gate_before: int | None = None,
    ) -> None:
        self._script = list(script)
        self._index = 0
        self._gate_before = gate_before
        #: Released by the case; waited on by the reader thread.
        self.gate = threading.Event()
        #: The reader thread owns the close — asserted, because a caller that
        #: closed instead would be closing under another thread's ``read1``.
        self.closed = False
        #: The ``n`` of every ``read1(n)``.
        self.sizes: list[int] = []

    def read1(self, size: int) -> bytes:
        self.sizes.append(size)
        if self._gate_before is not None and self._index == self._gate_before:
            assert self.gate.wait(JOIN_BOUND), "the case never released the reader's gate"
        if self._index >= len(self._script):
            return b""
        item = self._script[self._index]
        self._index += 1
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        self.closed = True


def _reader(
    stream: _ScriptedStream,
    state: _ReadState,
    *,
    on_chunk: Callable[[bytes], None] | None = None,
    on_eof: Callable[[], None] | None = None,
) -> _PipeReader:
    """A started reader over ``stream``.

    The cast is about the CHECKER only: :class:`_PipeReader` asks for
    ``IO[bytes]`` and reads exactly two names off it, ``read1`` and ``close``,
    which is the whole protocol a scripted stream needs to have.
    """

    reader = _PipeReader(cast("IO[bytes]", stream), state, on_chunk=on_chunk, on_eof=on_eof)
    reader.start()
    return reader


def _join(reader: _PipeReader) -> None:
    """Join the reader, failing by name rather than leaking a daemon thread."""

    reader.join(JOIN_BOUND)
    if reader.is_alive():
        pytest.fail(f"the reader was still running {JOIN_BOUND}s after its script ended")


def test_on_chunk_gets_every_chunk_in_order_and_the_reader_retains_nothing() -> None:
    """The retention invariant: with a callback, ``chunks`` stays EMPTY.

    The callback IS the consumer (#222 §B). Appending as well would hold every
    byte twice for the life of the call — measured on 200 MB of output at
    535 MB peak RSS against 285 MB today (#222 critique SCOPE-6) — and this is
    the assertion that stops a later reader from "repairing" the missing
    ``append``. The idle stamp is asserted in the same case because the bash
    site's post-kill drain re-arms its idle window from ``last_chunk_at``: a
    stamp that only happened on the append branch would leave that drain
    measuring from the exit alone.
    """

    log: list[object] = []
    stream = _ScriptedStream([b"one", b"two", b"three"])
    started = time.monotonic()
    state = _ReadState(last_chunk_at=started)

    reader = _reader(stream, state, on_chunk=log.append, on_eof=lambda: log.append("eof"))
    _join(reader)

    assert log == [b"one", b"two", b"three", "eof"]
    assert reader.chunks == []
    assert reader.eof is True
    assert state.last_chunk_at >= started
    assert stream.closed is True
    assert stream.sizes == [_READ_CHUNK_BYTES] * 4


def test_on_eof_fires_exactly_once_on_the_eof_path() -> None:
    """Once, and after the last chunk — the two halves the drain relies on.

    The bash site's drain is ``await eof.wait()`` on the success path and a
    re-armed ``wait_for`` on the kill path, so a second firing would be a
    second ``Event.set`` (harmless) but a firing BEFORE the last chunk callback
    would let ``exec`` return with bytes still queued behind it — the delivery
    invariant of #222 §A.2-3, whose whole point is that both callbacks travel
    the same FIFO.
    """

    calls: list[float] = []
    chunks: list[bytes] = []
    stream = _ScriptedStream([b"a", b"b"])

    def on_eof() -> None:
        calls.append(time.monotonic())

    reader = _reader(
        stream,
        _ReadState(last_chunk_at=time.monotonic()),
        on_chunk=chunks.append,
        on_eof=on_eof,
    )
    _join(reader)

    assert chunks == [b"a", b"b"]
    assert len(calls) == 1


@pytest.mark.parametrize("error", [OSError("broken pipe"), ValueError("closed file")])
def test_on_eof_fires_exactly_once_when_the_read_raises(error: BaseException) -> None:
    """The ``except (OSError, ValueError)`` leg — where ``eof`` is never set.

    That leg is the normal end of a run that was killed (#221 review WIN-7),
    and it leaves ``reader.eof`` ``False`` forever. #221's synchronous
    :func:`_drain` covers it with a ``not reader.is_alive()`` clause it added
    after measuring 0.102 s of pointless idle wait against 0.000 s (#221 review
    HC5); the bash site's drain is an event wait, so the equivalent cover is
    ``on_eof`` firing from here too. Both exception types are asserted because
    both are in the caught set and a mutant that fired only on the EOF
    ``return`` would be green on neither.
    """

    log: list[str] = []
    stream = _ScriptedStream([b"a", error])

    reader = _reader(
        stream,
        _ReadState(last_chunk_at=time.monotonic()),
        on_chunk=lambda chunk: log.append(chunk.decode()),
        on_eof=lambda: log.append("eof"),
    )
    _join(reader)

    assert log == ["a", "eof"]
    assert reader.eof is False
    assert stream.closed is True


def test_on_eof_fires_from_the_finally_when_the_except_leg_does_not_catch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third exit: an exception the leg does not catch still fires ``on_eof``.

    ``RuntimeError`` is the one that matters in production rather than a
    hypothetical: ``loop.call_soon_threadsafe`` on a closed loop raises it
    (#222 critique TESTS-1), so the callback bodies themselves can be the
    source. The site wraps its own bodies; what THIS pins is the primitive's
    half — the drain's event is set from the ``finally``, so a reader that dies
    this way ends the caller's wait instead of leaving it to time out on the
    cap. ``threading.excepthook`` is captured rather than left to print,
    because the recorded exception is also the proof that the case really took
    the uncaught path and not the ``except`` leg above it.
    """

    hooked: list[type[BaseException] | None] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: hooked.append(args.exc_type))

    log: list[str] = []
    stream = _ScriptedStream([b"a", RuntimeError("the loop is closed")])

    reader = _reader(
        stream,
        _ReadState(last_chunk_at=time.monotonic()),
        on_chunk=lambda chunk: log.append(chunk.decode()),
        on_eof=lambda: log.append("eof"),
    )
    _join(reader)

    assert hooked == [RuntimeError]
    assert log == ["a", "eof"]
    assert reader.eof is False
    assert stream.closed is True


def test_detach_stops_the_callback_and_the_reader_reads_on() -> None:
    """No delivery after ``detach()`` — the guard stays ABOVE the callback.

    ``detach`` is the bash site's innermost ``finally`` (#222 §A.2-7), and what
    it must end there is DELIVERY, not the read: the holder of the pipe must
    never be wedged on a full buffer by a call that has gone home. So the
    reader is asserted to have consumed the rest of the script and reached its
    ordinary EOF while delivering nothing more.

    The bound the primitive promises is "at most one in-flight chunk" — a
    delivery whose ``if self._detached`` test was already past can still run.
    The gate is what makes this case deterministic instead of merely likely:
    the reader is parked INSIDE ``read1`` when ``detach`` runs, so the next
    test cannot already be past.
    """

    delivered: list[bytes] = []
    first = threading.Event()

    def on_chunk(chunk: bytes) -> None:
        delivered.append(chunk)
        first.set()

    log: list[str] = []
    stream = _ScriptedStream([b"one", b"two", b"three"], gate_before=1)
    reader = _reader(
        stream,
        _ReadState(last_chunk_at=time.monotonic()),
        on_chunk=on_chunk,
        on_eof=lambda: log.append("eof"),
    )

    assert first.wait(JOIN_BOUND), "the reader never delivered its first chunk"
    reader.detach()
    stream.gate.set()
    _join(reader)

    assert delivered == [b"one"]
    assert reader.chunks == []
    assert reader.eof is True
    # The point of "read on, keep nothing": the script was consumed to its end.
    assert stream.sizes == [_READ_CHUNK_BYTES] * 4
    assert log == ["eof"]


def test_a_reader_without_callbacks_is_exactly_what_it_was() -> None:
    """No callbacks, no change — ``run_contained``'s readers pass none.

    #222 widened the constructor and nothing else: with both callbacks left at
    their default the chunks are retained, the idle timer is stamped, ``eof``
    is set and the thread closes the stream, which is the whole contract
    :func:`run_contained` and :func:`_collected` are built on. The constructor
    is called here with the positional pair ALONE — not through :func:`_reader`,
    which always passes the two new keywords — so this case is green on
    ``main`` as well as here (verified: 7 failed on ``main``'s checkout with the
    helper, 6 with this call). That is the point of it.
    """

    stream = _ScriptedStream([b"one", b"two"])
    started = time.monotonic()
    state = _ReadState(last_chunk_at=started)

    reader = _PipeReader(cast("IO[bytes]", stream), state)
    reader.start()
    _join(reader)

    assert reader.chunks == [b"one", b"two"]
    assert reader.eof is True
    assert state.last_chunk_at >= started
    assert stream.closed is True
