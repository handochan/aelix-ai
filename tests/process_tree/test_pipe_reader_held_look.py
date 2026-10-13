"""#381: on win32 the blocking branch looks at its pipe holding the GIL, and only looks.

``_PipeReader``'s blocking branch, the one win32 runs (forced here through
``platform="win32"``), looks at its pipe before every read. Until #381 both the
look (``_winapi.PeekNamedPipe``) and the read (``read1``) released the GIL, and
behind a busy loop thread each release cost the reader a whole GIL re-take,
6-10 ms on ``windows-latest``: 2 MiB of output came back in 9-12 s on
py3.11/3.12 against 0.3-0.5 s with the loop idle
(``test_bash_tool_containment.py``'s
``test_every_byte_is_delivered_under_a_loaded_loop``, which sat at 10-17 s of
its 20 s budget). Now a ``sole_reader``'s look is ``PeekNamedPipe`` on its own
duplicate of the pipe handle, called WITHOUT releasing the GIL
(``_HeldPipe``), and the read after a look that saw bytes asks ``read1`` for no
more than it saw. The read still releases the GIL: bytes a look counted can
leave the pipe (a writer that dies with a write pending takes it back), and a
read that held the GIL would then stop every thread (#381 review, round 2).

Pinned here, on every host, through a recording stand-in for the foreign calls
(the real ones on win32; FIONREAD and ``os.dup`` off it) under the real reader:
every look IS the held pipe's peek, on the reader's own handle; the read asks
for what the look saw, at most a chunk, and never for 0; a stream with a
``read1`` of its own, a reader that is not the sole one, and a
``BufferedReader`` over another raw get no held pipe; a stream closed and its
handle value reused by a pipe another thread is blocked reading cannot reach
the look; a close waits for a look in flight, makes the next look fail, and
happens once (after the loop, or by the collector); both product sites say
they are the sole reader; and a look that counted bytes the pipe no longer
holds leaves only the reader waiting (in a child process, which a GIL-held
wait could not be timed out from inside). On win32 only: the look is the very
prototype carrying ``FUNCFLAG_PYTHONAPI``, an ``os.pipe`` and a real ``Popen``
stdout get one, its handle is not inheritable, a message-mode pipe is read
whole, and the shape the round-2 review froze (a writer killed with its write
pending while another process holds the write end) leaves the process live.
The time is not pinned here: it is a distribution on ``windows-latest``,
quoted in the commit that made this change.
"""

from __future__ import annotations

import gc
import io
import os
import subprocess
import sys
import threading
import time
import warnings
from typing import IO, Any, cast

import pytest
from aelix_ai.utils import _process_tree
from aelix_ai.utils._process_tree import _READ_CHUNK_BYTES, _PipeReader, _ReadState

#: An anti-hang bound on every wait in here, never a verdict.
BOUND = 15.0

#: Less than any pipe buffer on the three hosts (4 KiB on win32's ``os.pipe``),
#: so one write lands whole and a look taken after it counts all of it.
#: Printable bytes only: no LF to become CRLF and no Ctrl-Z to read as EOF
#: should an fd ever be in text mode on win32.
PAYLOAD = (bytes(range(0x20, 0x7F)) * 33)[:3072]


class _Delivered:
    """An ``on_chunk`` that keeps what it is handed, and says when it has enough."""

    def __init__(self, want: int) -> None:
        self.got: list[bytes] = []
        self._want = want
        self.enough = threading.Event()

    def __call__(self, chunk: bytes) -> None:
        self.got.append(chunk)
        if sum(map(len, self.got)) >= self._want:
            self.enough.set()


class _RecordingApi:
    """A ``_HeldPipeApi`` that records every call, so a test can see WHICH calls the reader made.

    On win32 every call goes to the real set (:func:`_process_tree._held_pipe_api`:
    the GIL-held ``PeekNamedPipe``, a real ``DuplicateHandle``). Off win32 the
    same calls are stood in for on the fd itself (FIONREAD, ``os.dup``,
    ``os.close``) with the fd as the "handle". Either way the reader under test
    is the real one; only the foreign calls are seen. ``inflate`` is added to
    every count a look answers (a look that counted bytes which are not, or no
    longer, in the pipe); with a ``gate`` every look first waits for it (in the
    stand-in, so a test can hold a look in flight).
    """

    def __init__(self, *, inflate: int = 0, gate: threading.Event | None = None) -> None:
        self.real = _process_tree._held_pipe_api() if sys.platform == "win32" else None
        if sys.platform == "win32":
            assert self.real is not None, "the held-pipe api could not be built on win32"
        self.inflate = inflate
        self.gate = gate
        self.in_peek = threading.Event()
        self.error = 0
        #: ``(handle, answer)`` per look; ``answer`` is the count, or -1.
        self.calls: list[tuple[int, int]] = []
        self.duplicated: list[tuple[int, int]] = []
        self.closed: list[int] = []

    def api(self) -> _process_tree._HeldPipeApi:
        return _process_tree._HeldPipeApi(
            peek=self.peek,
            last_error=self.last_error,
            osfhandle=self.osfhandle,
            duplicate=self.duplicate,
            close=self.close,
        )

    def peek(self, handle: int, buf: Any, size: int, read: Any, avail: Any, left: Any) -> int:
        if self.gate is not None:
            self.in_peek.set()
            assert self.gate.wait(BOUND), "the gate was never opened"
        if self.real is not None:
            ok = self.real.peek(handle, buf, size, read, avail, left)
            self.error = 0 if ok else self.real.last_error()
        elif sys.platform == "win32":  # pragma: no cover — the real set is always there
            raise AssertionError("no real held-pipe api on win32")
        else:
            import fcntl
            import termios

            try:
                answer = fcntl.ioctl(handle, termios.FIONREAD, bytes(4))
            except OSError as exc:
                self.error = exc.errno or 1
                ok = 0
            else:
                avail._obj.value = int.from_bytes(answer, sys.byteorder, signed=True)
                ok = 1
        if ok:
            avail._obj.value += self.inflate
        self.calls.append((handle, int(avail._obj.value) if ok else -1))
        return ok

    def last_error(self) -> int:
        return self.error

    def osfhandle(self, fd: int) -> int:
        return self.real.osfhandle(fd) if self.real is not None else fd

    def duplicate(self, handle: int) -> int:
        own = self.real.duplicate(handle) if self.real is not None else os.dup(handle)
        self.duplicated.append((handle, own))
        return own

    def close(self, handle: int) -> None:
        self.closed.append(handle)
        if self.real is not None:
            self.real.close(handle)
        else:
            os.close(handle)


def _recording(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> _RecordingApi:
    """Plant a :class:`_RecordingApi` as the process's held-pipe api, on every host.

    ``raising=True`` (the default) is load-bearing: on a tree with no
    ``_held_pipe_api`` this cannot be planted.
    """

    recording = _RecordingApi(**kwargs)
    monkeypatch.setattr(_process_tree, "_held_pipe_api", recording.api)
    return recording


def _own_handle(recording: _RecordingApi) -> int:
    assert len(recording.duplicated) == 1, recording.duplicated
    return recording.duplicated[0][1]


def _spy_read1(stream: Any) -> list[int]:
    """Record the size of every ``read1`` the reader asks ``stream`` for.

    An instance attribute, so ``type(stream)`` is unchanged and the held-pipe
    gate still sees a plain ``BufferedReader``. Planted before ``start``: the
    reader looks ``read1`` up once, when it starts.
    """

    asked: list[int] = []
    real = stream.read1

    def read1(size: int = -1) -> bytes:
        asked.append(size)
        return real(size)

    stream.read1 = read1
    return asked


def _sole(stream: Any, **kwargs: Any) -> _PipeReader:
    """A forced-blocking-branch reader whose caller has said it is the stream's only reader."""

    return _PipeReader(
        stream,
        _ReadState(last_chunk_at=time.monotonic()),
        platform="win32",
        sole_reader=True,
        **kwargs,
    )


def _finish(
    reader: _PipeReader, write_fd: int, delivered: _Delivered, expected: bytes = PAYLOAD
) -> None:
    """Wait for ``expected``, close the write end, and see the reader end on the real EOF."""

    try:
        assert delivered.enough.wait(BOUND), (
            f"the reader handed on {sum(map(len, delivered.got))} of {len(PAYLOAD)} bytes"
        )
    finally:
        os.close(write_fd)
        reader.join(BOUND)
    assert not reader.is_alive(), "the reader outlived its pipe's EOF"
    assert reader.eof is True
    assert b"".join(delivered.got) == expected


def test_a_sole_readers_look_is_the_held_peek_and_its_read_asks_for_what_it_saw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every look is the held ``PeekNamedPipe`` on the reader's OWN handle; each read asks for
    exactly what the look before it counted.

    This is the wiring #381 is: red with the look back on
    ``_winapi.PeekNamedPipe`` or the probe not swapped (no looks recorded; off
    win32 the first falls back to nothing), red with the read asking a whole
    chunk again, and red with the handle not closed after the loop.
    """

    recording = _recording(monkeypatch)
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    asked = _spy_read1(stream)
    delivered = _Delivered(len(PAYLOAD))
    reader = _sole(stream, on_chunk=delivered)
    assert reader._held is not None, "a sole reader's BufferedReader(FileIO) gets one"
    assert reader._probe == reader._held.peek
    own = _own_handle(recording)
    os.write(write_fd, PAYLOAD)
    reader.start()
    _finish(reader, write_fd, delivered)

    assert recording.calls and recording.calls[0] == (own, len(PAYLOAD)), recording.calls[:3]
    assert {handle for handle, _answer in recording.calls} == {own}
    assert asked and asked[0] == len(PAYLOAD), f"the look saw {len(PAYLOAD)}; read1 asked {asked}"
    assert len(asked) == len(recording.calls), "every read comes after a look of its own"
    assert recording.closed == [own], "the reader closes its own handle once, after its loop"


def test_the_read_asks_for_at_most_a_chunk_whatever_the_look_saw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A look can count far more than a chunk (on win32 a pending multi-MiB write is counted
    whole); the read still asks for :data:`_READ_CHUNK_BYTES` at most. Red with the cap gone."""

    _recording(monkeypatch, inflate=400_000)
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    asked = _spy_read1(stream)
    delivered = _Delivered(len(PAYLOAD))
    reader = _sole(stream, on_chunk=delivered)
    os.write(write_fd, PAYLOAD)
    reader.start()
    _finish(reader, write_fd, delivered)

    assert asked and set(asked) == {_READ_CHUNK_BYTES}, asked


def test_a_look_that_saw_nothing_never_asks_read1_for_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a look that saw 0 the read asks for a whole chunk and waits for the command.

    ``read1(0)`` answers ``b""`` at once, which the read loop takes for EOF: the
    reader would end on its first empty look, before the command wrote a byte,
    and everything after would be lost. Red with ``seen > 0`` loosened to
    ``seen >= 0``.
    """

    recording = _recording(monkeypatch)
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    asked = _spy_read1(stream)
    delivered = _Delivered(len(PAYLOAD))
    reader = _sole(stream, on_chunk=delivered)
    reader.start()
    deadline = time.monotonic() + BOUND
    while not recording.calls:
        assert time.monotonic() < deadline, "the reader never looked"
        time.sleep(0.005)
    assert recording.calls[0][1] == 0, recording.calls
    os.write(write_fd, PAYLOAD)
    _finish(reader, write_fd, delivered)

    assert asked[0] == _READ_CHUNK_BYTES, asked
    assert 0 not in asked, asked


class _OwnRead1:
    """A real pipe behind a ``read1`` of its own — the #260 holds' shape."""

    def __init__(self, inner: io.BufferedReader) -> None:
        self._inner = inner
        self.read1_calls = 0

    def fileno(self) -> int:
        return self._inner.fileno()

    def read1(self, size: int) -> bytes:
        self.read1_calls += 1
        return self._inner.read1(size)

    def close(self) -> None:
        self._inner.close()


class _SubclassedRead1(io.BufferedReader):
    """A ``BufferedReader`` SUBCLASS over a plain ``FileIO``, with a ``read1`` of its own."""

    read1_calls = 0

    def read1(self, size: int = -1) -> bytes:
        self.read1_calls += 1
        return super().read1(size)


@pytest.mark.parametrize("shape", ["wrapper", "subclass"])
def test_a_stream_with_a_read1_of_its_own_gets_no_held_pipe(
    shape: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a plain ``BufferedReader`` over a plain ``FileIO`` gets the held look.

    Two shapes: a wrapper (no ``raw`` at all) and a subclass whose ``raw`` IS a
    plain ``FileIO``, so only the exact-type gate can refuse it. Such a stream's
    ``read1`` is its own business: it could read on another thread, or keep
    bytes the look cannot see. And the #260 ``preread``/``inread`` holds are
    streams of exactly this shape: with the gate gone their look would be held
    and their reads bounded, and the residual they pin would move. What this
    pins is the exact-type half of the gate: the subclass id is red with that
    half removed or loosened to ``isinstance``, and the wrapper id, which the
    raw-type half refuses as well, only with the whole gate gone (measured on
    darwin, #381 review round 4). With the raw-type half removed both ids stay
    green; that half is pinned by
    ``test_a_buffered_reader_over_another_raw_gets_no_held_pipe``.
    """

    recording = _recording(monkeypatch)
    read_fd, write_fd = os.pipe()
    stream: _OwnRead1 | _SubclassedRead1 = (
        _OwnRead1(os.fdopen(read_fd, "rb"))
        if shape == "wrapper"
        else _SubclassedRead1(io.FileIO(read_fd, "rb"))
    )
    delivered = _Delivered(len(PAYLOAD))
    reader = _PipeReader(
        cast("IO[bytes]", stream),
        _ReadState(last_chunk_at=time.monotonic()),
        on_chunk=delivered,
        platform="win32",
        sole_reader=True,
    )
    assert reader._held is None, "a stream with its own read1 gets no held pipe"
    os.write(write_fd, PAYLOAD)
    reader.start()
    _finish(reader, write_fd, delivered)
    assert recording.duplicated == [] and recording.calls == []
    assert stream.read1_calls >= 2, "the payload and the EOF both come through its read1"


def test_only_a_sole_reader_gets_a_held_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller that has not said it is the stream's only reader keeps the GIL-releasing look.

    Another thread's read pending on the same pipe makes a look wait for it
    (measured on ``windows-latest``: 1.5 s behind a 1.5 s read, on the
    original handle and on a duplicate), and a held look would wait with every
    thread stopped. No type check can see another reader, so the held path is
    the caller's to ask for. Red with ``sole_reader`` ignored.
    """

    recording = _recording(monkeypatch)
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    try:
        reader = _PipeReader(stream, _ReadState(last_chunk_at=time.monotonic()), platform="win32")
        assert reader._held is None and reader._release_held is None
        assert recording.duplicated == [] and recording.calls == []
    finally:
        stream.close()
        os.close(write_fd)


class _UpperRaw(io.RawIOBase):
    """A raw reader that changes what it reads: a ``BufferedReader`` over it is not a plain pipe."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def readable(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._fd

    def readinto(self, buffer: Any) -> int:
        data = os.read(self._fd, len(buffer)).upper()
        buffer[: len(data)] = data
        return len(data)

    def close(self) -> None:
        if not self.closed:
            os.close(self._fd)
        super().close()


def test_a_buffered_reader_over_another_raw_gets_no_held_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``BufferedReader(FileIO)``: under any other raw, ``read1`` is that raw's business."""

    recording = _recording(monkeypatch)
    read_fd, write_fd = os.pipe()
    delivered = _Delivered(len(PAYLOAD))
    reader = _sole(cast("IO[bytes]", io.BufferedReader(_UpperRaw(read_fd))), on_chunk=delivered)
    assert reader._held is None
    os.write(write_fd, PAYLOAD)
    reader.start()
    _finish(reader, write_fd, delivered, PAYLOAD.upper())
    assert recording.duplicated == []


def test_a_close_waits_for_a_look_in_flight_and_the_next_look_makes_no_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The close takes the lock the look holds: it cannot close the handle under a look, and
    once it has begun no look reaches the handle. A second close is nothing.

    A handle closed under a held look could be handed out again to a pipe some
    other thread is blocked reading, and the look would then wait with every
    thread stopped. Red with the lock removed (the close lands while the look
    is in flight), and red with the closed check removed from the look.
    """

    gate = threading.Event()
    recording = _recording(monkeypatch, gate=gate)
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    try:
        reader = _sole(stream)
        held = reader._held
        assert held is not None and reader._release_held is not None
        own = _own_handle(recording)
        os.write(write_fd, PAYLOAD)
        answers: list[int] = []
        looker = threading.Thread(target=lambda: answers.append(held.peek()), daemon=True)
        looker.start()
        assert recording.in_peek.wait(BOUND), "the look never started"
        closer = threading.Thread(target=held.close, daemon=True)
        closer.start()
        closer.join(0.3)
        assert closer.is_alive() and recording.closed == [], (
            f"the handle was closed under a look in flight: {recording.closed}"
        )
        gate.set()
        looker.join(BOUND)
        closer.join(BOUND)
        assert answers == [len(PAYLOAD)]
        assert recording.closed == [own]

        made = len(recording.calls)
        with pytest.raises(ValueError):
            held.peek()
        assert len(recording.calls) == made, recording.calls[made:]
        reader._release_held()
        held.close()
        assert recording.closed == [own], "closed once, whoever asks again"
    finally:
        gate.set()
        stream.close()
        os.close(write_fd)


def test_a_reader_that_never_ran_still_closes_its_own_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The duplicate is made before ``start``; a reader dropped unstarted releases it when collected."""

    recording = _recording(monkeypatch)
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    try:
        reader = _sole(stream)
        own = _own_handle(recording)
        assert recording.closed == []
        del reader
        gc.collect()
        assert recording.closed == [own]
    finally:
        stream.close()
        os.close(write_fd)


class _SaysWhetherSole(_PipeReader):
    """A real reader that records the ``sole_reader`` its call site passed."""

    said: list[bool] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).said.append(bool(kwargs.get("sole_reader", False)))
        super().__init__(*args, **kwargs)


async def test_both_product_sites_say_they_are_the_sole_reader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The bash tool and ``run_contained`` hand their fresh ``Popen`` pipes over as ``sole_reader``.

    That word is what turns the held look on; a site that dropped it would put
    #381 back on win32 with every test of the reader itself still green. Red
    with ``sole_reader=True`` removed from either site.
    """

    from aelix_ai.tools import ToolExecutionContext
    from aelix_coding_agent.tools import bash as bash_module

    _SaysWhetherSole.said = []
    monkeypatch.setattr(_process_tree, "_PipeReader", _SaysWhetherSole)
    done = _process_tree.run_contained([sys.executable, "-c", "print('hi')"], timeout=BOUND)
    assert done.returncode == 0 and done.stdout.strip() == b"hi"
    assert _SaysWhetherSole.said == [True, True], "run_contained: stdout and stderr"

    _SaysWhetherSole.said = []
    monkeypatch.setattr(bash_module, "_PipeReader", _SaysWhetherSole)
    tool = bash_module.create_bash_tool(str(tmp_path))
    assert tool.execute is not None
    await tool.execute(
        {"command": "echo hi", "timeout": BOUND}, ToolExecutionContext(tool_call_id="381")
    )
    assert _SaysWhetherSole.said == [True], "the bash tool's one reader"


# -- in a child process: a GIL-held wait cannot be timed out from inside --------


def _ticker(seconds: float) -> float:
    """Tick on this thread for ``seconds``; the longest gap between two ticks."""

    worst = 0.0
    last = time.monotonic()
    end = last + seconds
    while last < end:
        time.sleep(0.005)
        now = time.monotonic()
        worst = max(worst, now - last)
        last = now
    return worst


def _scenario_reuse() -> None:
    """Close the stream, hand its handle value to a pipe another thread is blocked reading,
    then look. Prints ``REUSED`` and ``RESULT`` lines."""

    recording = _RecordingApi()
    _process_tree._held_pipe_api = recording.api  # type: ignore[assignment]  # a child process
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    spare_read, _spare_write = os.pipe()  # an empty pipe whose writer stays open
    reader = _sole(stream)
    held = reader._held
    assert held is not None
    os.write(write_fd, PAYLOAD)
    freed = recording.osfhandle(read_fd)
    stream.close()  # what another thread could do between two looks
    reused: Any = None
    if sys.platform == "win32":  # pragma: no cover — the freed HANDLE value, handed out again
        import _winapi  # pyright: ignore[reportMissingModuleSource]

        me = _winapi.GetCurrentProcess()
        spare = recording.osfhandle(spare_read)
        for _ in range(256):
            candidate = _winapi.DuplicateHandle(
                me, spare, me, 0, False, _winapi.DUPLICATE_SAME_ACCESS
            )
            if candidate == freed:
                reused = candidate
                break

        def blocked_read() -> None:
            _winapi.ReadFile(reused, 1)

    else:  # the freed fd number, handed out again
        candidate = os.dup(spare_read)
        reused = candidate if candidate == freed else None

        def blocked_read() -> None:
            os.read(reused, 1)

    print(f"REUSED {reused is not None}", flush=True)
    if reused is not None:
        threading.Thread(target=blocked_read, daemon=True).start()
        time.sleep(0.3)  # the other thread's read is pending on the reused handle
    seen = held.peek()
    print(f"RESULT {seen}", flush=True)
    os._exit(0)


def _scenario_phantom() -> None:
    """A look counts bytes the pipe does not hold, with a writer still open: the read waits,
    and only the reader waits. Prints ``GAP`` and ``RESULT`` lines."""

    recording = _RecordingApi(inflate=len(PAYLOAD))
    _process_tree._held_pipe_api = recording.api  # type: ignore[assignment]  # a child process
    read_fd, write_fd = os.pipe()
    delivered = _Delivered(len(PAYLOAD))
    reader = _sole(os.fdopen(read_fd, "rb"), on_chunk=delivered)
    assert reader._held is not None
    reader.start()
    deadline = time.monotonic() + BOUND
    while not recording.calls:
        assert time.monotonic() < deadline, "the reader never looked"
        time.sleep(0.005)
    print(f"GAP {_ticker(1.0):.3f} alive={reader.is_alive()}", flush=True)
    os.write(write_fd, PAYLOAD)
    assert delivered.enough.wait(BOUND)
    os.close(write_fd)
    reader.join(BOUND)
    print(f"RESULT {b''.join(delivered.got) == PAYLOAD} eof={reader.eof}", flush=True)
    os._exit(0)


def _scenario_killed_writer() -> None:  # pragma: no cover — win32 only
    """The shape round 2's review froze: the look counts a writer's PENDING write, the writer
    is killed (its write cancelled, its bytes gone), and another process holds the write end.
    Prints ``LOOKED``, ``GAP`` and ``RESULT`` lines."""

    read_fd, write_fd = os.pipe()

    def spawn(code: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=write_fd,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    writer = spawn("import os, time; os.write(1, b'x' * 1048576); time.sleep(60)")
    holder = spawn("import time; time.sleep(60)")
    os.close(write_fd)
    time.sleep(0.8)  # the writer's one 1 MiB write is pending on a 4 KiB pipe
    delivered = _Delivered(1)
    reader = _sole(os.fdopen(read_fd, "rb"), on_chunk=delivered)
    assert reader._held is not None, "no held pipe on an os.pipe"
    look = reader._probe
    assert look is not None
    looked: list[int] = []

    def look_then_kill() -> int:
        seen = look()
        if not looked:
            looked.append(seen)
            writer.kill()
            writer.wait()
            time.sleep(0.2)
        return seen

    reader._probe = look_then_kill
    reader.start()
    deadline = time.monotonic() + BOUND
    while not looked:
        assert time.monotonic() < deadline, "the reader never looked"
        time.sleep(0.005)
    print(f"LOOKED {looked[0]}", flush=True)
    print(f"GAP {_ticker(2.0):.3f} alive={reader.is_alive()}", flush=True)
    holder.kill()
    holder.wait()
    reader.join(BOUND)
    print(f"RESULT ended={not reader.is_alive()} got={sum(map(len, delivered.got))}", flush=True)
    os._exit(0)


def _run_scenario(name: str) -> str:
    try:
        done = subprocess.run(
            [sys.executable, __file__, f"--scenario={name}"],
            capture_output=True,
            text=True,
            timeout=BOUND * 2,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        pytest.fail(f"the child FROZE and was killed at {BOUND * 2:.0f} s: {out!r}")
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def _gap(out: str) -> float:
    line = next(line for line in out.splitlines() if line.startswith("GAP "))
    return float(line.split()[1])


def test_a_stream_closed_and_its_handle_reused_cannot_reach_the_look() -> None:
    """The stream is closed and its handle value (fd number off win32) goes at once to a pipe
    another thread is blocked reading; the look still answers for its own pipe, at once.

    The look uses the reader's own DUPLICATE, which the stream's close does not
    touch. A look on the reused value would ask the wrong pipe and, on win32,
    wait behind that pipe's pending read holding the GIL: the child would
    freeze. Red with the duplicate dropped (a freeze on win32, the other pipe's
    0 off it).
    """

    out = _run_scenario("reuse")
    assert f"RESULT {len(PAYLOAD)}" in out, out
    if "REUSED True" not in out:  # pragma: no cover — Windows promises no order
        warnings.warn(
            f"#381 reuse case: the freed handle was not handed out again ({out!r})", stacklevel=1
        )


def test_a_look_that_counted_bytes_the_pipe_does_not_hold_leaves_only_the_reader_waiting() -> None:
    """The read after a look is ``read1``, with the GIL released: if the bytes the look counted
    are not there (on win32, a writer that died with its write pending took them back), the
    reader waits for the next write and every other thread runs on.

    Modelled on every host by a look that adds :data:`PAYLOAD`'s length to its
    count while the pipe is empty and its writer open. Red with a read that
    holds the GIL (round 2's take): the child's main thread stops and it is
    killed at the bound.
    """

    out = _run_scenario("phantom")
    assert _gap(out) < 0.5, out
    assert "alive=True" in out, f"the reader should still be waiting: {out}"
    assert "RESULT True eof=True" in out, out


# -- win32: the real calls -------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="the GIL-held kernel32 exists on win32 only")
def test_win32_the_look_is_the_gil_held_peek_on_a_handle_no_child_inherits() -> None:
    """The look IS the GIL-held ``PeekNamedPipe``, on an ``os.pipe`` and on a real ``Popen``
    stdout as the bash tool and ``run_contained`` build them, and its handle is not inheritable.

    Identity, not a copy: the api's ``peek`` is the very prototype that carries
    ``FUNCFLAG_PYTHONAPI``, and the reader's look is its held pipe's ``peek``.
    An inheritable duplicate would let a child spawned meanwhile share the
    file object, and that child's read would make the look wait.
    """

    assert sys.platform == "win32"
    import _ctypes

    api = _process_tree._held_pipe_api()
    assert api is not None
    flags = cast("Any", api.peek)._flags_
    assert flags & _ctypes.FUNCFLAG_PYTHONAPI, "PeekNamedPipe releases the GIL"

    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 3000)"], stdout=subprocess.PIPE
    )
    try:
        assert proc.stdout is not None
        for stream in (os.fdopen(read_fd, "rb"), proc.stdout):
            delivered = _Delivered(3000)
            reader = _PipeReader(
                cast("IO[bytes]", stream),
                _ReadState(last_chunk_at=time.monotonic()),
                on_chunk=delivered,
                sole_reader=True,
            )
            held = reader._held
            assert held is not None, f"no held pipe on {stream!r}"
            assert held._api is api
            assert reader._probe == held.peek
            assert held._handle is not None
            assert not os.get_handle_inheritable(held._handle)
            if stream is proc.stdout:
                reader.start()
                assert delivered.enough.wait(BOUND)
                reader.join(BOUND)
                assert b"".join(delivered.got) == b"x" * 3000 and reader.eof is True
            else:
                assert reader._release_held is not None
                reader._release_held()
                stream.close()
    finally:
        os.close(write_fd)
        proc.wait(BOUND)


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are win32's")
def test_win32_a_message_mode_pipe_is_read_whole() -> None:
    """A message-mode pipe gets the held look too, and its messages come through whole.

    The look waits for neither mode; a read of part of a message fails with
    ``ERROR_MORE_DATA``, and for a first message of 64 KiB or less the bounded
    read never asks for less than that message, since the look counts every
    message in the pipe. A larger first message gets the 64 KiB read the base
    tree made too, and no product site makes a message-mode pipe. The
    messages here are 100 and 50 bytes.
    """

    assert sys.platform == "win32"
    import _winapi  # pyright: ignore[reportMissingModuleSource]
    import msvcrt  # pyright: ignore[reportMissingModuleSource]

    name = rf"\\.\pipe\aelix-381-{os.getpid()}-{time.monotonic_ns()}"
    server = _winapi.CreateNamedPipe(
        name,
        _winapi.PIPE_ACCESS_INBOUND,
        _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT,
        1,
        0,
        65536,
        0,
        _winapi.NULL,
    )
    client = _winapi.CreateFile(
        name, _winapi.GENERIC_WRITE, 0, _winapi.NULL, _winapi.OPEN_EXISTING, 0, _winapi.NULL
    )
    stream = os.fdopen(msvcrt.open_osfhandle(server, os.O_RDONLY), "rb")
    delivered = _Delivered(150)
    reader = _PipeReader(
        stream, _ReadState(last_chunk_at=time.monotonic()), on_chunk=delivered, sole_reader=True
    )
    assert reader._held is not None
    _winapi.WriteFile(client, PAYLOAD[:100])
    _winapi.WriteFile(client, PAYLOAD[100:150])
    reader.start()
    try:
        assert delivered.enough.wait(BOUND), delivered.got
    finally:
        _winapi.CloseHandle(client)
        reader.join(BOUND)
    assert b"".join(delivered.got) == PAYLOAD[:150]
    assert not reader.is_alive()


@pytest.mark.skipif(sys.platform != "win32", reason="a cancelled pending write is win32's")
def test_win32_a_writer_killed_with_its_write_pending_leaves_the_process_live() -> None:
    """The round-2 freeze, through the reader: the look counts a pending 1 MiB write, the
    writer is killed (its bytes leave the pipe), another process holds the write end.

    The reader's read then waits for a write that will not come until the
    holder goes, and only the reader waits: the child's main thread ticks on.
    Red on round 2's tree, where the read of what the look counted held the
    GIL (measured: the whole process froze until killed).
    """

    out = _run_scenario("killed-writer")
    looked = int(next(line for line in out.splitlines() if line.startswith("LOOKED ")).split()[1])
    assert looked > 4096, f"the look did not count the pending write: {out}"
    assert _gap(out) < 0.5, out
    assert "RESULT ended=True" in out, out


if __name__ == "__main__" and len(sys.argv) == 2 and sys.argv[1].startswith("--scenario="):
    {
        "reuse": _scenario_reuse,
        "phantom": _scenario_phantom,
        "killed-writer": _scenario_killed_writer,
    }[sys.argv[1].split("=", 1)[1]]()
