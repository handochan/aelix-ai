"""The RPC output writer: frames leave the event loop through one writer thread (#852, ADR-0317).

``run_rpc_mode`` used to write each frame with a blocking ``write`` and ``flush`` on the
loop thread, so a host that stopped reading stalled the whole native (its turn, its
children's pumps, every handler) until it read again (ADR-0307 K8, measured in #852).
Now the loop encodes a frame and enqueues it; one daemon thread writes the frames whole,
in the order they were enqueued.

Back-pressure follows pi's (``output-guard.ts`` ``waitForRawStdoutBackpressure``), with a
mark: while more than :data:`_HIGH_WATER_BYTES` are queued, the agent-event listener and
each command's reply wait (:meth:`ThreadedFrameWriter.wait_for_backpressure`), and
:class:`InputGate` holds the host's input: the lines are still read (until a mark's worth
of them is held) but not parsed, except one after each drain to the mark, so no other new
reply or parse error is made. Under the mark a line is parsed when the queue and the
replies still owed by the commands let in before it leave room. The other producers (an
extension's dialog, a child's ``aelix_child`` frame, a notice) enqueue regardless. Nothing
is dropped while the mode runs; frames are lost only after a closed pipe or a broken
stream, and past the exit's drain bound (ADR-0317).

A sink handed in by the caller (the tests, the WebSocket host) keeps the old inline
contract: :class:`InlineFrameWriter` calls it on the producer's thread and never waits.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import errno
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

# Module-private: bounds of one process's own stdout, not extension-band policy
# constants (tests/agents/test_p2_band_boundaries.py). All are the lead's calls (ADR-0317).
_HIGH_WATER_BYTES = 1 << 20
"""Above this many queued bytes, a producer that can wait does, and the input is held (1 MiB)."""
_DRAIN_SECONDS = 5.0
"""How long the mode's exit waits for the queued frames to reach the host."""
_RETRY_SECONDS = 0.01
"""A non-blocking stdout that is full is tried again after this long (pi's 10 ms)."""
_STDERR_WAIT_SECONDS = 0.2
"""How long the exit waits for its one stderr line before it goes on without it."""
_SETTLE_SECONDS = 0.05
"""How long a command let in counts as owing its reply: the longest the input waits for it."""
_REPLY_RESERVE_BYTES = 16 * 1024
"""The room a command let in takes until its reply is enqueued: 64 of them fill an empty queue's."""

WriteSome = Callable[[memoryview], "int | None"]
"""Writes some of a frame and says how many bytes (``None`` or 0: nothing, try later)."""


def pipe_closed(exc: BaseException) -> bool:
    """Whether a write failed because the host closed its end of stdout (#57).

    On Windows a write to a pipe whose reader has gone fails ``EINVAL`` rather than
    ``EPIPE`` (CPython's ``subprocess`` says so at its own ``stdin.write``: bpo-19612,
    bpo-30418), so there both spell "the host is gone".
    """

    if isinstance(exc, BrokenPipeError):
        return True
    return sys.platform == "win32" and isinstance(exc, OSError) and exc.errno == errno.EINVAL


def _retry_later(exc: OSError) -> bool:
    """A full stdout that will take bytes later: wait :data:`_RETRY_SECONDS` and write again.

    pi retries ``EAGAIN``, ``EWOULDBLOCK`` and ``ENOBUFS``. On Windows the C runtime reports
    a full non-blocking pipe as ``ENOSPC``, which CPython 3.12 turns into ``EAGAIN`` and 3.11
    does not, so there ``ENOSPC`` waits too; on POSIX it is a full disk, and the stream is
    broken.
    """

    if isinstance(exc, BlockingIOError) or exc.errno == errno.ENOBUFS:
        return True
    return sys.platform == "win32" and exc.errno == errno.ENOSPC


def tell_stderr(text: str, *, wait: float = 0.0) -> None:
    """Write one diagnostic line to stderr without blocking the caller past ``wait`` seconds.

    A host that reads neither stdout nor stderr must not hold the exit (Codex C3): the line
    goes out from a daemon thread with ``os.write``, which holds no Python lock, and is lost
    if stderr cannot take it before the interpreter exits. A stderr with no descriptor
    (pytest's capture) is written directly; it cannot block.
    """

    stream = sys.stderr
    try:
        fd = stream.fileno()
    except (AttributeError, ValueError, OSError):
        with contextlib.suppress(Exception):
            stream.write(text)
            stream.flush()
        return
    data = text.encode("utf-8", errors="replace")

    def write() -> None:
        view = memoryview(data)
        with contextlib.suppress(OSError):
            while view:
                view = view[os.write(fd, view) :]

    thread = threading.Thread(target=write, name="aelix-rpc-stderr", daemon=True)
    with contextlib.suppress(RuntimeError):  # no thread can start: the interpreter is ending
        thread.start()
        if wait:
            thread.join(wait)


def stream_sink(stream: Any) -> Callable[[bytes], None]:
    """Write through ``stream``'s buffer, or its text layer when it has none.

    The pre-#852 writer, kept for a stream with no descriptor (pytest's capture).
    """

    def write(data: bytes) -> None:
        try:
            stream.buffer.write(data)
            stream.buffer.flush()
        except BrokenPipeError:
            # Issue #57: the JSONL consumer vanished — re-raise so the caller can
            # shut down, not through the text fallback, which raised the same error.
            raise
        except (AttributeError, ValueError, OSError) as exc:
            if pipe_closed(exc):
                raise BrokenPipeError(errno.EPIPE, str(exc)) from exc
            # ``stream`` may not expose ``.buffer`` (e.g. captured by pytest).
            # NOT ``decode_child_output``: these are bytes WE encoded, and never a
            # child's (#239).
            stream.write(data.decode("utf-8", errors="replace"))
            stream.flush()

    return write


def real_stdout_sink(stream: Any = None) -> WriteSome:
    """How the writer thread writes the process's real stdout, captured before the redirect.

    ``os.write`` on the descriptor, a pipe or a terminal, never ``stream.buffer.write``: a
    thread blocked inside the buffered writer holds its lock, and the interpreter's exit
    then waits on that lock (measured on 3.12: no exit within 15 s on a pipe; on a terminal
    that is not read, CPython's ``_enter_buffered_busy`` fatal error and status -6), so no
    drain bound could hold. ``os.write`` holds no Python lock. A Windows console is written
    through its raw console object, which takes UTF-8 (``os.write`` would hand the bytes to
    the console's code page), may write short, and holds no lock either. A stream with no
    descriptor keeps the base's buffered path.

    The sink writes some of a frame and says how much; the writer continues a short write.
    """

    if stream is None:
        stream = sys.__stdout__ if sys.__stdout__ is not None else sys.stdout
    try:
        fd = stream.fileno()
        terminal = os.isatty(fd)
    except (AttributeError, ValueError, OSError):
        whole = stream_sink(stream)

        def write_whole(view: memoryview) -> int:
            whole(bytes(view))
            return len(view)

        return write_whole
    with contextlib.suppress(Exception):
        stream.flush()  # anything written before the mode began goes out first
    if terminal and sys.platform == "win32":
        # The buffered layer's raw console, or the layer itself under ``-u``, where
        # ``buffer`` is unbuffered (``PYTHONUNBUFFERED``).
        buffer = getattr(stream, "buffer", None)
        console = getattr(buffer, "raw", buffer)
        if console is not None:
            return console.write

    def write_fd(view: memoryview) -> int:
        return os.write(fd, view)

    return write_fd


class InlineFrameWriter:
    """An injected sink, called on the producer's thread as before #852.

    A ``BrokenPipeError`` closes it, starts the shutdown and is re-raised to that
    producer; later frames are dropped (#57).
    """

    failure: BaseException | None = None

    def __init__(self, write: Callable[[bytes], None], close: Callable[[], None]) -> None:
        self._write = write
        self._close = close
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        try:
            self._write(data)
        except BrokenPipeError:
            self._closed = True
            self._close()
            raise

    def input_allowance(self) -> int:
        return sys.maxsize  # never behind: every line read is parsed at once, as at the base

    def raise_if_gone(self) -> None:
        return None

    async def wait_for_backpressure(self) -> None:
        return None

    async def wait_for_drain(self) -> bool:
        return True

    def follow(self, harness: Any) -> None:
        return None

    def release_producers(self) -> None:
        return None

    async def aclose(self, timeout: float | None = None) -> bool:
        return True


class ThreadedFrameWriter:
    """One daemon thread writes the queued frames, whole and in order (#852, ADR-0317).

    :meth:`write` may be called from any thread and never blocks on the pipe. The thread
    starts with the first frame. A write that fails with anything but a full stream
    (``EAGAIN``, ``ENOBUFS``, Windows' ``ENOSPC``: wait and retry) or ``EINTR`` (retry) ends
    the stream: a closed pipe, or a stream broken mid-frame, where writing the next frame
    would append it to a partial one. Then the queue is dropped, every waiting producer
    wakes, the mode's shutdown starts (``close``), and :attr:`failure` holds the error.
    """

    def __init__(
        self,
        write: WriteSome,
        close: Callable[[], None],
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        high_water: int = _HIGH_WATER_BYTES,
    ) -> None:
        self._write = write
        self._close = close
        self._loop = loop or asyncio.get_running_loop()
        self._high_water = high_water
        self._lock = threading.Condition()
        self._frames: collections.deque[bytes] = collections.deque()
        self._queued = 0
        """Bytes enqueued that have not reached the stream (the written part of a frame excluded)."""
        self._closing = False  # the exit began: no new frame is taken
        self._closed = False  # the stream ended (``failure``): nothing more is written
        self._abandoned = False  # the drain bound passed: the thread stops after its write
        self._released = False
        self._waiting = False
        self._failure: BaseException | None = None
        self._waiters: list[asyncio.Future[None]] = []
        self._drained: asyncio.Future[None] | None = None
        self._unfollow: Callable[[], None] | None = None
        self._thread: threading.Thread | None = None

    @property
    def queued_bytes(self) -> int:
        with self._lock:
            return self._queued

    @property
    def failure(self) -> BaseException | None:
        """The error that ended the stream: a closed pipe or a broken stream; ``None`` while it is whole."""

        with self._lock:
            return self._failure

    def input_allowance(self) -> int:
        """How many commands may owe their reply when one more line of input is parsed.

        None above the mark. At or under it, one per :data:`_REPLY_RESERVE_BYTES` of the
        room left and never fewer than one: 64 with an empty queue, one within 16 KiB of
        the mark (a closed stream has emptied the queue).
        """

        with self._lock:
            room = self._high_water - self._queued
        return 0 if room < 0 else max(1, room // _REPLY_RESERVE_BYTES)

    def raise_if_gone(self) -> None:
        """Raise ``BrokenPipeError`` when the stream ended, so ``main_sync`` exits 141 (ADR-0180)."""

        failure = self.failure
        if failure is not None:
            raise BrokenPipeError(errno.EPIPE, "the RPC host's stdout is gone") from failure

    def write(self, data: bytes) -> None:
        """Enqueue one frame; refused once the stream ended or the exit began."""

        with self._lock:
            if self._closed or self._closing:
                return
            self._frames.append(data)
            self._queued += len(data)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._serve, name="aelix-rpc-stdout", daemon=True
                )
                self._thread.start()
            self._lock.notify()

    async def wait_for_backpressure(self) -> None:
        """Return once the queue is at or below the mark (a closed pipe empties it) or the exit began."""

        while not await self.wait_for_drain():
            pass

    async def wait_for_drain(self) -> bool:
        """``True`` at once while there is room; otherwise ``False`` at the queue's next drain to the mark.

        One wake, no second look: by the time the waiter runs, a producer woken before it
        may have filled the queue again (:class:`InputGate` lets one line in then).
        """

        with self._lock:
            if self._released or self._closed or self._queued <= self._high_water:
                return True
            self._waiting = True
        waiter = self._loop.create_future()
        self._waiters.append(waiter)
        try:
            await waiter
        finally:
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)
        return False

    def follow(self, harness: Any) -> None:
        """Hold ``harness``'s agent events at the mark, as pi's ``unsubscribeBackpressure`` listener does.

        The harness awaits an async listener, so its turn waits while the host is behind;
        the previous harness's listener ends here.
        """

        unfollow, self._unfollow = self._unfollow, None
        if unfollow is not None:
            unfollow()
        if harness is not None:
            self._unfollow = harness.subscribe(self._hold)

    async def _hold(self, _event: Any) -> None:
        await self.wait_for_backpressure()

    def release_producers(self) -> None:
        """The mode is ending: every wait returns now and later ones do not wait."""

        with self._lock:
            self._released = True
        self._wake()

    async def aclose(self, timeout: float | None = None) -> bool:
        """Write what is queued, waiting at most ``timeout`` (:data:`_DRAIN_SECONDS`).

        From here no new frame is taken. Returns whether every frame enqueued reached the
        stream whole: ``False`` after a closed pipe or a broken stream, and past the bound.
        Then the frames still queued are dropped and the one being written may end without
        its newline; a stderr line says how many frames did not reach the stream whole and
        how many bytes they hold. A write that has not returned counts in full: on a pipe
        that blocks, the kernel may hold part of that frame, and the writer cannot know how
        much. The thread writes nothing more than the rest of the ``os.write`` it is in.
        """

        self.release_producers()
        self.follow(None)
        drained = self._loop.create_future()
        with self._lock:
            self._closing = True
            if self._frames:  # a closed or broken stream emptied the queue
                self._drained = drained
            self._lock.notify_all()
        if self._drained is drained:
            bound = _DRAIN_SECONDS if timeout is None else timeout
            late = False
            try:
                await asyncio.wait({drained}, timeout=bound)
            finally:
                with self._lock:
                    late = not drained.done() and bool(self._frames)
                    if not drained.done():
                        self._abandoned = True
                    frames, size = len(self._frames), self._queued
            if late:
                tell_stderr(
                    f"[rpc] the host did not read for {bound:g} s at exit: "
                    f"{frames} frame(s), {size} bytes not written\n",
                    wait=_STDERR_WAIT_SECONDS,
                )
                return False
        return self.failure is None

    # === The writer thread ===============================================

    def _serve(self) -> None:
        """Write each queued frame, whole and in order, until the exit or the stream's end."""

        try:
            while True:
                with self._lock:
                    while not self._frames and not self._closing:
                        self._lock.wait()
                    if not self._frames:
                        return
                    data = self._frames[0]
                if not self._send(data):
                    return
                with self._lock:
                    self._frames.popleft()
                    drained = self._drained if not self._frames else None
                if drained is not None:
                    self._call_on_loop(_settle, drained)
        except BaseException as exc:  # noqa: BLE001 — never a dead thread with producers waiting
            self._fail(exc)

    def _send(self, data: bytes) -> bool:
        """Write one frame whole, continuing short writes; ``False`` once the stream ended or the exit gave up."""

        view = memoryview(data)
        while view:
            with self._lock:
                if self._closed or self._abandoned:
                    return False
            try:
                sent = self._write(view)
            except InterruptedError:
                continue  # EINTR: the same bytes again
            except OSError as exc:
                if not _retry_later(exc):
                    self._fail(exc)
                    return False
                sent = 0
            if not sent:  # full: nothing taken (``None`` from a non-blocking raw stream)
                time.sleep(_RETRY_SECONDS)
                continue
            view = view[sent:]
            with self._lock:
                self._queued -= sent
                wake = self._waiting and self._queued <= self._high_water
                if wake:
                    self._waiting = False
            if wake:
                self._call_on_loop(self._wake)
        return True

    def _fail(self, exc: BaseException) -> None:
        """The stream ended: drop the queue, wake every waiter, start the shutdown (#57)."""

        with self._lock:
            if self._failure is not None:
                return
            self._failure = exc
            self._closed = True
            self._frames.clear()
            self._queued = 0
            drained = self._drained
        self._call_on_loop(self._closed_on_loop, drained)
        if not pipe_closed(exc):
            tell_stderr(f"[rpc] stdout cannot be written ({exc!r}); the mode ends\n")

    def _call_on_loop(self, callback: Callable[..., None], *args: Any) -> None:
        with contextlib.suppress(RuntimeError):  # the loop has closed
            self._loop.call_soon_threadsafe(callback, *args)

    # === On the loop ======================================================

    def _wake(self) -> None:
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            _settle(waiter)

    def _closed_on_loop(self, drained: asyncio.Future[None] | None) -> None:
        self._wake()
        if drained is not None:
            _settle(drained)
        self._close()


class InputGate:
    """The host's input lines, held while the host is behind (#852, ADR-0317, the lead's call).

    A line is parsed (and its command started) when the writer's queue is at or under its
    mark and the commands let in before it leave room. A reply is enqueued by its command's
    task, some loop turns after the line, so a command that has not answered counts as
    :data:`_REPLY_RESERVE_BYTES` of the room (:meth:`ThreadedFrameWriter.input_allowance`):
    with an empty queue 64 may owe a reply, within 16 KiB of the mark one, so there each
    line's reply counts before the next line is judged. A command counts until its reply is
    enqueued (:meth:`replied`), its task ends, or :data:`_SETTLE_SECONDS` have passed since
    its line, which is the longest the input waits for one command (a ``bash`` or a
    ``compact`` answers when it is done).

    Above the mark no line is parsed, except one after each drain to the mark, even when a
    turn has filled the queue past it again, so the turn cannot starve the input. That line
    too waits for the command let in before it, so the lines held while the host is behind
    are answered one at a time. While a line waits, the bytes go on being read as long as
    less than a mark's worth of lines is held, so an EOF behind them is still seen; at the
    EOF, or a failed read, every line left is parsed at once. An injected sink is never
    behind: its lines are parsed as they are read, as at the base.
    """

    def __init__(
        self,
        writer: InlineFrameWriter | ThreadedFrameWriter,
        on_line: Callable[[str], None],
    ) -> None:
        self._writer = writer
        self._on_line = on_line
        self._lines: collections.deque[str] = collections.deque()
        self._backlog = 0
        # The commands let in that still count, oldest first, each with the time it stops
        # counting (a line that waits takes the oldest out once that time has come).
        self._owed: dict[asyncio.Future[Any], float] = {}
        self._news: asyncio.Future[None] | None = None

    @property
    def held_lines(self) -> int:
        return len(self._lines)

    def take(self, line: str) -> None:
        """The line reader's callback: hold the line until it may be parsed."""

        self._lines.append(line)
        self._backlog += len(line) + 1

    def started(self, command: asyncio.Future[Any]) -> None:
        """The line being parsed started ``command`` (its task): it owes its reply from now."""

        self._owed[command] = time.monotonic() + _SETTLE_SECONDS
        command.add_done_callback(self._answered)

    async def replied(self) -> None:
        """A command's task awaits this once its reply is enqueued: the reply counts for the
        input from here, and the task waits at the mark, as pi's does after each reply."""

        command = asyncio.current_task()
        if command is not None:
            self._answered(command)
        await self._writer.wait_for_backpressure()

    def _answered(self, command: asyncio.Future[Any]) -> None:
        if self._owed.pop(command, None) is not None:
            news = self._news
            if news is not None and not news.done():
                news.set_result(None)

    def _parse(self) -> None:
        line = self._lines.popleft()
        self._backlog -= len(line) + 1
        self._on_line(line)

    def _parse_all(self) -> None:
        while self._lines:
            self._parse()

    async def pump(self, stdin: asyncio.StreamReader, reader: Any) -> None:
        """Read ``stdin`` into ``reader`` (whose callback is :meth:`take`) until its end."""

        loop = asyncio.get_running_loop()
        read: asyncio.Future[bytes] | None = None
        drained = False  # the queue fell to the mark: one line is owed, whatever is queued now
        try:
            while True:
                if not self._lines:
                    chunk = await (read if read is not None else stdin.read(4096))
                    read = None
                else:
                    allowance = self._writer.input_allowance()
                    if drained and not allowance:
                        allowance = 1
                    if len(self._owed) < allowance:
                        drained = False
                        self._parse()
                        continue
                    # The next line waits: for a reply owed, at most until the oldest command's
                    # time is up, or, above the mark with no line owed, for a drain to the mark.
                    if read is None and self._backlog < _HIGH_WATER_BYTES:
                        read = asyncio.ensure_future(stdin.read(4096))
                    wake: asyncio.Future[Any]
                    oldest: asyncio.Future[Any] | None = None
                    patience: float | None = None
                    if allowance:
                        oldest = next(iter(self._owed))
                        patience = max(0.0, self._owed[oldest] - time.monotonic())
                        wake = self._news = loop.create_future()
                    else:
                        wake = asyncio.ensure_future(self._writer.wait_for_drain())
                    try:
                        done, _ = await asyncio.wait(
                            {wake} if read is None else {wake, read},
                            timeout=patience,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    finally:
                        self._news = None
                        wake.cancel()
                    if oldest is None:
                        drained = wake.done() and not wake.cancelled()
                    elif not done:  # its time is up, whatever the clock's step says
                        self._owed.pop(oldest, None)
                    if read is None or not read.done():
                        continue
                    chunk, read = read.result(), None
                if not chunk:
                    reader.end()
                    break
                reader.feed(chunk)
        except Exception:
            self._parse_all()  # the lines already read, as the base's reader parsed them
            raise
        finally:
            if read is not None:
                read.cancel()
        self._parse_all()


def _settle(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


def open_frame_writer(
    stdout_write: Callable[[bytes], None] | None, close: Callable[[], None]
) -> InlineFrameWriter | ThreadedFrameWriter:
    """The real stdout gets the writer thread; an injected sink stays inline (ADR-0317)."""

    if stdout_write is not None:
        return InlineFrameWriter(stdout_write, close)
    return ThreadedFrameWriter(real_stdout_sink(), close)


__all__ = [
    "InlineFrameWriter",
    "InputGate",
    "ThreadedFrameWriter",
    "open_frame_writer",
    "pipe_closed",
    "real_stdout_sink",
    "stream_sink",
    "tell_stderr",
]
