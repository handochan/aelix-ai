"""#852 (ADR-0317): RPC output leaves the event loop through one writer thread.

At the base every frame was written with a blocking ``write`` and ``flush`` on the loop
thread, so a host that stopped reading stalled the whole native until it read again: the
issue measured a 2.969 s loop stall for a 3.0 s pause (ADR-0307 K8). Here the real stdout
is a real pipe (``sys.__stdout__`` replaced by a stream over its write end), so the tests
drive the descriptor path ``aelix --mode rpc`` takes.

The tests run on Windows too unless marked: the pipes are ``os.pipe()``, the readers are
threads, and only the stderr-full and terminal children need POSIX (``os.set_blocking``
on a pipe, ``pty``). A writer thread that dies with a traceback fails the test it runs in.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import io
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import JsonlSessionCreateOptions, JsonlSessionRepo, LocalFileSystem
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    TextDeltaEvent,
)
from aelix_coding_agent.rpc import _stdout_writer, rpc_mode
from aelix_coding_agent.rpc._jsonl import serialize_json_line
from aelix_coding_agent.rpc._stdout_writer import (
    InlineFrameWriter,
    ThreadedFrameWriter,
    pipe_closed,
    real_stdout_sink,
    stream_sink,
)
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

from tests.env_sandbox import child_env

pytestmark = pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")

_MIB = 1 << 20


# === Doubles =================================================================


class _Gate:
    """A sink that blocks while closed, like a host that does not read; records what it wrote."""

    def __init__(self, *, open_: bool = False) -> None:
        self.opened = threading.Event()
        if open_:
            self.opened.set()
        self.written: list[bytes] = []
        self.raise_next: BaseException | None = None

    def __call__(self, data: memoryview) -> int:
        self.opened.wait(30)
        if self.raise_next is not None:
            exc, self.raise_next = self.raise_next, None
            raise exc
        self.written.append(bytes(data))
        return len(data)


class _Steps:
    """A sink that takes one frame per permit, so a test decides when each frame is written."""

    def __init__(self) -> None:
        self.permits = threading.Semaphore(0)
        self.written: list[bytes] = []
        self.calls = 0

    def __call__(self, data: memoryview) -> int:
        self.calls += 1
        assert self.permits.acquire(timeout=30), "no permit within 30 s"
        self.written.append(bytes(data))
        return len(data)

    def release(self, n: int = 1) -> None:
        for _ in range(n):
            self.permits.release()


class _Fd:
    """A stream over a raw descriptor, with no buffered layer to write through."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    @property
    def buffer(self) -> Any:
        pytest.fail("the buffered stream was written")

    def fileno(self) -> int:
        return self._fd

    def flush(self) -> None:
        return None


class _PipeHost:
    """A real pipe whose write end stands in for the process's stdout.

    A thread reads it to EOF while ``reading`` is set; clearing it is a host that stops
    reading (after the read it is in, which takes at most one chunk). ``stream`` is what
    ``sys.__stdout__`` becomes: a text stream with a ``.buffer`` and a ``fileno()``, the
    shape the base's writer used.
    """

    def __init__(self) -> None:
        self.read_fd, self.write_fd = os.pipe()
        self.stream = open(self.write_fd, "w", encoding="utf-8", closefd=False)  # noqa: SIM115
        self.data = bytearray()
        self.reading = threading.Event()
        self.slow = False  # 4 KiB every 5 ms: slower than any test's producer
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        def run() -> None:
            while self.reading.wait(60) and (
                chunk := os.read(self.read_fd, 4096 if self.slow else 65536)
            ):
                self.data.extend(chunk)
                if self.slow:
                    time.sleep(0.005)

        self.reading.set()
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    async def ready(self, stdin: asyncio.StreamReader) -> None:
        """One reply read: the mode is up (an in-process start takes seconds the first time)."""

        _send(stdin, {"type": "get_state", "id": "ready"})
        await _until(lambda: b'"id": "ready"' in self.data, 60, "the mode did not start")

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in bytes(self.data).split(b"\n") if line.strip()]

    def close(self) -> None:
        self.reading.set()
        with contextlib.suppress(OSError):
            self.stream.close()
        with contextlib.suppress(OSError):
            os.close(self.write_fd)
        if self._thread is not None:
            self._thread.join(10)
        with contextlib.suppress(OSError):
            os.close(self.read_fd)


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> Iterator[_PipeHost]:
    pipe = _PipeHost()
    monkeypatch.setattr(sys, "__stdout__", pipe.stream)
    try:
        yield pipe
    finally:
        pipe.close()


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every frame writer ``run_rpc_mode`` opens, in order."""

    writers: list[Any] = []
    real_open = _stdout_writer.open_frame_writer

    def spy(*args: Any) -> Any:
        writer = real_open(*args)
        writers.append(writer)
        return writer

    monkeypatch.setattr(_stdout_writer, "open_frame_writer", spy)
    return writers


def _harness(stream_fn: Any = None) -> AgentHarness:
    async def quiet(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    return AgentHarness(
        AgentHarnessOptions(model=Model(id="mock", provider="mock"), stream_fn=stream_fn or quiet)
    )


def _long_turn(counter: list[int] | None = None) -> Any:
    """80 text deltas of 20 kB with no wait between them: a turn that fills the queue at once."""

    async def stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        for _ in range(80):
            if counter is not None:
                counter[0] += 1
            yield TextDeltaEvent(delta="x" * 20_000)
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        )

    return stream


class _Ticker:
    """How late the loop runs a 20 ms sleep: the loop latency the issue's ``hb.py`` logged."""

    def __init__(self) -> None:
        self.late: list[float] = []
        self._started = time.monotonic()
        self._task = asyncio.ensure_future(self._tick())

    async def _tick(self) -> None:
        while True:
            self._started = time.monotonic()
            await asyncio.sleep(0.02)
            self.late.append(time.monotonic() - self._started - 0.02)

    async def stop(self) -> float:
        """The worst lateness, the sleep still pending included (a blocked loop never ran it)."""

        self.late.append(time.monotonic() - self._started - 0.02)
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        return max(self.late)


async def _until(predicate: Any, timeout: float, what: str) -> None:
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, what
        await asyncio.sleep(0.02)


async def _settled(writer: ThreadedFrameWriter) -> None:
    """Until the queue stays the same for 0.3 s: the pipe is full and nothing drains."""

    last, end = -1, time.monotonic() + 10
    while (queued := writer.queued_bytes) != last:
        assert time.monotonic() < end, "the queue did not settle"
        last = queued
        await asyncio.sleep(0.3)


def _send(stdin: asyncio.StreamReader, command: dict[str, Any]) -> None:
    stdin.feed_data(serialize_json_line(command).encode("utf-8"))


def _collect(read_fd: int) -> bytes:
    received = bytearray()
    while chunk := os.read(read_fd, 65536):
        received.extend(chunk)
    os.close(read_fd)
    return bytes(received)


# === The issue's measurement =================================================


async def test_a_host_that_stops_reading_for_3_s_does_not_stall_the_loop(
    host: _PipeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's ``stall`` mode in-process: 400 replies, a host that reads only after 3 s.

    Fails on the base: the loop blocks in ``buffer.write`` until the host reads, so the
    20 ms sleep is answered about 3 s late (the base run measured it; see ADR-0317).
    """

    waits = 0
    real_wait = ThreadedFrameWriter.wait_for_backpressure

    async def counted(self: ThreadedFrameWriter) -> None:
        nonlocal waits
        waits += 1
        await real_wait(self)

    monkeypatch.setattr(ThreadedFrameWriter, "wait_for_backpressure", counted)
    host.start()
    stdin = asyncio.StreamReader()
    task = asyncio.ensure_future(
        run_rpc_mode(_harness(), stdin=stdin, install_signal_handlers=False)
    )
    await host.ready(stdin)
    host.reading.clear()
    ticker = _Ticker()
    for i in range(400):
        _send(stdin, {"type": "get_state", "id": f"r{i}"})
    resume = threading.Timer(3.0, host.reading.set)
    resume.start()
    await _until(
        lambda: host.data.count(b'"type": "response"') >= 400, 30, "the 400 replies did not arrive"
    )
    worst = await ticker.stop()
    resume.join()
    stdin.feed_eof()
    await asyncio.wait_for(task, 15)

    ids = [line["id"] for line in host.lines() if line.get("type") == "response"][1:]
    assert ids == [f"r{i}" for i in range(400)], "every reply arrives, in order"
    assert len(host.data) > 192 * 1024, "the replies were more than a pipe holds"
    assert worst < 0.5, f"the loop ran a 20 ms sleep {worst:.3f} s late while the host did not read"
    assert waits >= 401, "each reply's task waits at the writer, as pi's does after a reply"


async def test_the_mode_tells_the_input_of_each_command_and_of_its_reply(
    host: _PipeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_rpc_mode`` tells the input gate of the command a line started, and again from
    that command's task once its reply is enqueued; a malformed line starts no command."""

    events: list[tuple[str, Any]] = []
    gate = _stdout_writer.InputGate
    real_started, real_replied, real_write = gate.started, gate.replied, ThreadedFrameWriter.write

    def started(self: Any, command: Any) -> None:
        events.append(("started", command))
        real_started(self, command)

    async def replied(self: Any) -> None:
        events.append(("replied", asyncio.current_task()))
        await real_replied(self)

    def write(self: ThreadedFrameWriter, data: bytes) -> None:
        frame = json.loads(data)
        events.append(("enqueued", frame.get("id") or frame.get("command")))
        real_write(self, data)

    monkeypatch.setattr(gate, "started", started)
    monkeypatch.setattr(gate, "replied", replied)
    monkeypatch.setattr(ThreadedFrameWriter, "write", write)
    host.start()
    stdin = asyncio.StreamReader()
    task = asyncio.ensure_future(
        run_rpc_mode(_harness(), stdin=stdin, install_signal_handlers=False)
    )
    await host.ready(stdin)
    stdin.feed_data(b"!\n")
    await _until(lambda: b'"parse"' in host.data, 10, "the parse error did not arrive")
    _send(stdin, {"type": "get_state", "id": "second"})
    await _until(lambda: b'"id": "second"' in host.data, 10, "the reply did not arrive")
    stdin.feed_eof()
    await asyncio.wait_for(task, 15)

    assert [(kind, what) for kind, what in events if kind == "enqueued"] == [
        ("enqueued", "ready"),
        ("enqueued", "parse"),
        ("enqueued", "second"),
    ]
    kinds = [kind for kind, _ in events]
    assert kinds == ["started", "enqueued", "replied", "enqueued", "started", "enqueued", "replied"]
    assert events[0][1] is events[2][1], "the reply was told from its own command's task"
    assert events[4][1] is events[6][1] and events[4][1] is not events[0][1]


# === The policy: never drop, wait at the mark ================================


def test_the_mark_is_1_mib_and_the_exit_waits_at_most_5_s() -> None:
    """The lead's two numbers, and the input's two (ADR-0317)."""

    assert _stdout_writer._HIGH_WATER_BYTES == _MIB
    assert _stdout_writer._DRAIN_SECONDS == 5.0
    assert _stdout_writer._SETTLE_SECONDS == 0.05
    assert _stdout_writer._REPLY_RESERVE_BYTES == 16 * 1024


async def test_a_producer_waits_above_the_mark_and_resumes_below_it() -> None:
    """At the mark a wait returns; one byte over it waits until the host reads."""

    gate = _Gate()
    closed: list[bool] = []
    writer = ThreadedFrameWriter(gate, lambda: closed.append(True))
    try:
        writer.write(b"a" * _MIB)  # being written: it counts, and it is blocked
        await asyncio.wait_for(writer.wait_for_backpressure(), 1)  # == the mark: no wait
        assert writer.input_allowance() == 1

        writer.write(b"b")
        waiting = asyncio.ensure_future(writer.wait_for_backpressure())
        await asyncio.sleep(0.3)
        assert not waiting.done(), "one byte over the mark, a producer waits"
        assert writer.input_allowance() == 0
        assert writer.queued_bytes == _MIB + 1

        gate.opened.set()
        await asyncio.wait_for(waiting, 5)
        assert await writer.aclose(5) is True
        assert gate.written == [b"a" * _MIB, b"b"], "nothing was dropped, nothing reordered"
        assert closed == []
    finally:
        gate.opened.set()


async def test_a_waiter_wakes_when_the_queue_falls_to_exactly_the_mark() -> None:
    """The wake boundary: a frame written that leaves exactly the mark queued wakes the waiter."""

    steps = _Steps()
    writer = ThreadedFrameWriter(steps, lambda: None, high_water=10)
    try:
        writer.write(b"a")  # written first, under the test's permit
        writer.write(b"b" * 10)  # then exactly the mark is left
        waiting = asyncio.ensure_future(writer.wait_for_backpressure())
        await asyncio.sleep(0.1)
        assert not waiting.done()
        steps.release()
        await asyncio.wait_for(waiting, 5)  # "b" is still blocked: 10 bytes queued
        assert steps.written == [b"a"] and writer.queued_bytes == 10
        steps.release()
        assert await writer.aclose(5) is True
    finally:
        steps.release(5)


async def test_the_agent_event_listener_follows_the_live_harness() -> None:
    """``follow`` holds the harness's events at the mark and moves to a rebound harness."""

    class _Harness:
        def __init__(self) -> None:
            self.listeners: list[Any] = []

        def subscribe(self, listener: Any) -> Any:
            self.listeners.append(listener)
            return lambda: self.listeners.remove(listener)

    gate = _Gate()
    writer = ThreadedFrameWriter(gate, lambda: None, high_water=10)
    old, new = _Harness(), _Harness()
    try:
        writer.follow(old)
        writer.follow(new)
        assert old.listeners == [] and len(new.listeners) == 1

        writer.write(b"x" * 11)
        held = asyncio.ensure_future(new.listeners[0](object()))
        await asyncio.sleep(0.3)
        assert not held.done(), "the harness's turn waits while the host is behind"
        gate.opened.set()
        await asyncio.wait_for(held, 5)
        await writer.aclose(5)
        assert new.listeners == [], "the exit ends the hold"
    finally:
        gate.opened.set()


async def test_a_turn_is_held_at_the_mark_and_every_frame_arrives_whole_in_order(
    host: _PipeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real turn over the real pipe while the host does not read.

    80 text deltas of 20 kB (each ``message_update`` frame is about 20.5 kB): with the
    host stalled, the turn stops near the 1 MiB mark instead of streaming all 80, the
    loop stays free and still takes ``get_state`` lines, and once the host reads, the
    frames it receives are exactly the frames enqueued, whole and in that order.
    """

    consumed = [0]
    enqueued: list[bytes] = []
    real_write = ThreadedFrameWriter.write

    def spy(self: ThreadedFrameWriter, data: bytes) -> None:
        enqueued.append(data)
        real_write(self, data)

    monkeypatch.setattr(ThreadedFrameWriter, "write", spy)
    host.start()
    stdin = asyncio.StreamReader()
    task = asyncio.ensure_future(
        run_rpc_mode(_harness(_long_turn(consumed)), stdin=stdin, install_signal_handlers=False)
    )
    await host.ready(stdin)
    host.reading.clear()
    ticker = _Ticker()
    snapshot: list[int] = []

    def resume() -> None:
        # Off the loop, so a loop blocked in a write (the base) is measured, not waited on.
        snapshot.append(consumed[0])
        host.reading.set()

    timer = threading.Timer(1.75, resume)
    _send(stdin, {"type": "prompt", "id": "p", "message": "go"})
    timer.start()
    for i in range(5):
        await asyncio.sleep(0.25)
        _send(stdin, {"type": "get_state", "id": f"s{i}"})
    await _until(lambda: bool(snapshot), 30, "the host did not resume")
    worst = await ticker.stop()
    timer.join()

    await _until(lambda: b'"agent_end"' in host.data, 30, "the turn did not end")
    await _until(lambda: host.data.count(b'"get_state"') >= 6, 10, "a reply did not arrive")
    stdin.feed_eof()
    await asyncio.wait_for(task, 15)
    total = sum(map(len, enqueued))
    await _until(lambda: len(host.data) >= total, 10, "the host did not receive every byte")

    assert worst < 0.5, f"the loop ran {worst:.3f} s late"
    assert snapshot[0] < 80, (
        f"the turn streamed {snapshot[0]} of 80 deltas while the host was not reading"
    )
    assert consumed[0] == 80, "the turn finished once the host read"
    received = [line + b"\n" for line in bytes(host.data).split(b"\n") if line]
    assert received == enqueued, "the host received the enqueued frames, whole and in order"


# === The input waits too =====================================================


async def test_input_is_not_parsed_while_the_host_is_behind(
    host: _PipeHost, opened: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex's queue probe: 6,000 ``get_state`` lines and 1,000 malformed ones, the host not reading.

    While more than the mark is queued the lines are held, and once the host reads again
    each held line's reply counts before the next line is judged, so the queue stays at
    the mark through the hold and through the resume. The peak is read at every enqueue,
    in both phases: the round 2 verify measured 3,708,712 bytes just after the host read,
    where every held line had been parsed in one pass, and about 55 kB over the mark
    during the hold. Every reply and every parse error arrives, in order.
    """

    peaks = {"held": 0, "resumed": 0}
    phase = "held"
    real_write = ThreadedFrameWriter.write

    def spy(self: ThreadedFrameWriter, data: bytes) -> None:
        real_write(self, data)
        peaks[phase] = max(peaks[phase], self.queued_bytes)

    monkeypatch.setattr(ThreadedFrameWriter, "write", spy)
    host.start()
    stdin = asyncio.StreamReader()
    task = asyncio.ensure_future(
        run_rpc_mode(_harness(), stdin=stdin, install_signal_handlers=False)
    )
    await host.ready(stdin)
    writer = opened[0]
    host.reading.clear()
    for i in range(6000):
        _send(stdin, {"type": "get_state", "id": f"s{i}"})
    stdin.feed_data(b"!\n" * 1000)
    await _until(lambda: writer.queued_bytes > _MIB, 30, "the queue did not reach the mark")
    await _settled(writer)
    held = writer.queued_bytes
    phase = "resumed"
    host.reading.set()
    await _until(lambda: host.data.count(b"\n") >= 7001, 30, "the replies did not all arrive")
    stdin.feed_eof()
    await asyncio.wait_for(task, 15)

    lines = host.lines()[1:]
    ids = [line["id"] for line in lines if line.get("command") == "get_state"]
    assert ids == [f"s{i}" for i in range(6000)], "every reply, in order"
    assert sum(line.get("command") == "parse" for line in lines) == 1000
    assert held > _MIB, "the host was behind"
    assert max(peaks.values()) < _MIB + 32 * 1024, f"the queue's peaks, in bytes: {peaks}"


async def test_a_line_gets_in_after_each_drain_even_while_a_turn_refills_the_queue(
    host: _PipeHost, opened: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``get_state`` written while a turn is held is not parsed until the host reads; then it
    is answered before the turn ends, though the turn refills the queue past the mark at once
    (the host reads slowly), because one line goes through after each drain to the mark."""

    enqueued: list[bytes] = []
    real_write = ThreadedFrameWriter.write

    def spy(self: ThreadedFrameWriter, data: bytes) -> None:
        enqueued.append(data)
        real_write(self, data)

    monkeypatch.setattr(ThreadedFrameWriter, "write", spy)
    host.start()
    stdin = asyncio.StreamReader()
    task = asyncio.ensure_future(
        run_rpc_mode(_harness(_long_turn()), stdin=stdin, install_signal_handlers=False)
    )
    await host.ready(stdin)
    writer = opened[0]
    host.reading.clear()
    _send(stdin, {"type": "prompt", "id": "p", "message": "go"})
    await _until(lambda: writer.queued_bytes > _MIB, 10, "the turn did not reach the mark")
    await _settled(writer)  # a macOS pipe grows its buffer once, which is a drain
    _send(stdin, {"type": "get_state", "id": "mid"})
    await asyncio.sleep(0.5)
    assert not any(b'"id": "mid"' in frame for frame in enqueued), (
        "parsed while the host was behind"
    )
    host.slow = True
    host.reading.set()
    await _until(lambda: b'"agent_end"' in host.data, 30, "the turn did not end")
    await _until(lambda: b'"id": "mid"' in host.data, 10, "the reply did not arrive")
    stdin.feed_eof()
    await asyncio.wait_for(task, 15)

    order = [
        line.get("id") or line["type"]
        for line in host.lines()
        if line.get("id") == "mid" or line["type"] == "agent_end"
    ]
    assert order == ["mid", "agent_end"], order


async def test_the_input_after_an_eof_is_parsed_at_once() -> None:
    """Lines held behind the mark are parsed when the input ends, so the mode handles every
    command the host wrote and reaches its exit (the drain bound then applies)."""

    gate = _Gate()
    lines: list[str] = []
    writer = ThreadedFrameWriter(gate, lambda: None, high_water=10)
    input_gate = _stdout_writer.InputGate(writer, lines.append)
    from aelix_coding_agent.rpc._jsonl import JsonlLineReader

    reader = JsonlLineReader(input_gate.take)
    stdin = asyncio.StreamReader()
    try:
        writer.write(b"x" * 11)  # the host is behind
        pump = asyncio.ensure_future(input_gate.pump(stdin, reader))
        stdin.feed_data(b"one\ntwo\n")
        await asyncio.sleep(0.2)
        assert lines == [] and input_gate.held_lines == 2
        stdin.feed_data(b"three")
        stdin.feed_eof()
        await asyncio.wait_for(pump, 5)
        assert lines == ["one", "two", "three"]
    finally:
        gate.opened.set()
        await writer.aclose(5)


class _Behind:
    """A writer double for :class:`InputGate`: behind until ``room``; ``drain()`` is one wake."""

    def __init__(self) -> None:
        self.room = False
        self._drained = asyncio.Event()

    def input_allowance(self) -> int:
        return sys.maxsize if self.room else 0

    def write(self, data: bytes) -> None:
        return None

    async def wait_for_backpressure(self) -> None:
        return None

    def drain(self) -> None:
        self._drained.set()

    async def wait_for_drain(self) -> bool:
        if self.room:
            return True
        await self._drained.wait()
        self._drained.clear()
        return False


async def test_one_held_line_goes_through_per_drain() -> None:
    """After a drain to the mark one line is parsed even if the queue is past the mark again,
    so a turn that refills the queue at once cannot starve the input, and only one."""

    from aelix_coding_agent.rpc._jsonl import JsonlLineReader

    parsed: list[str] = []
    writer = _Behind()
    input_gate = _stdout_writer.InputGate(writer, parsed.append)  # type: ignore[arg-type]
    stdin = asyncio.StreamReader()
    pump = asyncio.ensure_future(input_gate.pump(stdin, JsonlLineReader(input_gate.take)))
    try:
        stdin.feed_data(b"one\ntwo\nthree\n")
        await asyncio.sleep(0.1)
        assert parsed == [] and input_gate.held_lines == 3
        for expected in (["one"], ["one", "two"]):
            writer.drain()
            await _until(lambda e=expected: parsed == e, 5, f"not {expected}")
            await asyncio.sleep(0.1)
            assert parsed == expected, "more than one line per drain"
        writer.room = True
        writer.drain()
        await _until(lambda: parsed == ["one", "two", "three"], 5, "the last line")
    finally:
        stdin.feed_eof()
        await asyncio.wait_for(pump, 5)


async def test_a_second_hold_reads_on_after_the_first_hold_was_parsed() -> None:
    """The held-input count goes down as lines are parsed: after more than a mark of input
    has passed, a new hold still reads until a mark of it is held again."""

    from aelix_coding_agent.rpc._jsonl import JsonlLineReader

    parsed: list[str] = []
    writer = _Behind()
    input_gate = _stdout_writer.InputGate(writer, parsed.append)  # type: ignore[arg-type]
    stdin = asyncio.StreamReader()
    pump = asyncio.ensure_future(input_gate.pump(stdin, JsonlLineReader(input_gate.take)))
    try:
        writer.room = True
        stdin.feed_data(b"".join(f"{i:039d}\n".encode() for i in range(40_000)))  # 1.6 MB
        await _until(lambda: len(parsed) == 40_000, 10, "the first input was not parsed")
        writer.room = False
        stdin.feed_data(b"held 1\nheld 2\n")
        await _until(lambda: input_gate.held_lines == 2, 5, "the second input was not read")
        stdin.feed_eof()
        await asyncio.wait_for(pump, 5)
        assert parsed[-2:] == ["held 1", "held 2"]
    finally:
        if not pump.done():
            stdin.feed_eof()
            await asyncio.wait_for(pump, 5)


class _Commands:
    """``run_rpc_mode``'s ``_on_line`` for an :class:`InputGate` alone: one task per line.

    A line's task takes three loop turns, enqueues ``reply``, tells the gate, waits at the
    mark and ends, as a command's does. A ``slow…`` line's task does not answer before
    ``release`` is set, and a ``dies…`` line's task ends without a reply.
    """

    def __init__(self, writer: Any, reply: bytes = b"r") -> None:
        self.parsed: list[str] = []
        self.release = asyncio.Event()
        self.gate = _stdout_writer.InputGate(writer, self._on_line)
        self._writer = writer
        self._reply = reply
        self._tasks: set[asyncio.Future[None]] = set()

    def _on_line(self, line: str) -> None:
        self.parsed.append(line)
        task = asyncio.ensure_future(self._run(line))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self.gate.started(task)

    async def _run(self, line: str) -> None:
        if line.startswith("slow"):
            await self.release.wait()
        for _ in range(3):
            await asyncio.sleep(0)
        if not line.startswith("dies"):
            self._writer.write(self._reply)
            await self.gate.replied()

    def pump(self, stdin: Any) -> asyncio.Future[None]:
        from aelix_coding_agent.rpc._jsonl import JsonlLineReader

        return asyncio.ensure_future(self.gate.pump(stdin, JsonlLineReader(self.gate.take)))

    async def end(self, stdin: asyncio.StreamReader, pump: asyncio.Future[None]) -> None:
        self.release.set()
        if not pump.done():
            stdin.feed_eof()
            await asyncio.wait_for(pump, 5)
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5)


async def _within_turns(predicate: Any, what: str, turns: int = 50) -> None:
    """``predicate`` holds within ``turns`` passes of the loop: nothing was waited for."""

    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate(), what


async def test_the_room_left_says_how_many_commands_may_owe_a_reply() -> None:
    """One per 16 KiB of room and never fewer than one at or under the mark; none above it."""

    gate = _Gate()
    writer = ThreadedFrameWriter(gate, lambda: None)
    try:
        assert writer.input_allowance() == 64
        for size, allowance in (
            (_MIB // 2, 32),
            (_MIB // 2 - 32 * 1024, 2),
            (16 * 1024 - 1, 1),  # 16 KiB and a byte of room: still one
            (1, 1),
            (16 * 1024, 1),  # at the mark
            (1, 0),  # one byte over
        ):
            writer.write(b"x" * size)  # the frame being written counts, and it is blocked
            assert writer.input_allowance() == allowance, (writer.queued_bytes, allowance)
    finally:
        gate.opened.set()
        await writer.aclose(5)
    assert InlineFrameWriter(lambda data: None, lambda: None).input_allowance() > 1 << 40


async def test_at_the_resume_a_held_line_waits_for_the_reply_of_the_line_before_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round 2 verify's F1: at the first drain to the mark every held line was parsed in
    one pass, because a reply is enqueued by its command's task some loop turns after its
    line (12,520,050 bytes queued from 20,000 ``get_state`` lines). Now the command a line
    started counts until it ends, so the lines go in one at a time as their replies fit."""

    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 30.0)  # no line goes in on a timer
    steps = _Steps()
    writer = ThreadedFrameWriter(steps, lambda: None, high_water=100)
    commands = _Commands(writer, reply=b"r" * 60)
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)
    try:
        writer.write(b"a" * 150)  # the host is behind
        stdin.feed_data(b"1\n2\n3\n4\n5\n")
        await _until(lambda: commands.gate.held_lines == 5, 5, "the lines were not read")
        await asyncio.sleep(0.1)
        assert commands.parsed == [], "parsed above the mark"
        for expected in (["1", "2"], ["1", "2", "3"], ["1", "2", "3", "4"]):
            # One frame reaches the host. The first time the queue is empty: "1" goes in, its
            # 60-byte reply fits, "2" goes in and its reply passes the mark. After that each
            # frame written lets the waiting reply's command end and one more line in.
            steps.release()
            await _until(lambda e=expected: commands.parsed == e, 5, f"not {expected}")
            await asyncio.sleep(0.2)
            assert commands.parsed == expected, "a line went in before the reply before it counted"
        steps.release(10)
        await _until(lambda: len(commands.parsed) == 5, 5, "the last line")
    finally:
        steps.release(10)
        await commands.end(stdin, pump)
        await writer.aclose(5)


async def test_with_an_empty_queue_64_commands_may_owe_a_reply_and_the_65th_line_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 30.0)
    writer = ThreadedFrameWriter(_Gate(open_=True), lambda: None)
    commands = _Commands(writer)
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)
    try:
        stdin.feed_data(b"".join(f"slow{i}\n".encode() for i in range(70)))
        await _within_turns(lambda: len(commands.parsed) == 64, "64 lines did not go in at once")
        await asyncio.sleep(0.2)
        assert len(commands.parsed) == 64, "the 65th line did not wait for a reply"
        commands.release.set()  # they answer: the rest goes in
        await _until(lambda: len(commands.parsed) == 70, 5, "the rest did not go in")
        assert commands.parsed == [f"slow{i}" for i in range(70)]
    finally:
        await commands.end(stdin, pump)
        await writer.aclose(5)


async def test_a_host_that_reads_pays_no_wait_after_a_slow_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round 2 verify's F2: a ``get_state`` written 5 ms after a ``bash`` ``sleep 0.6``
    waited 45 ms, because the input waited for every command its last lines had started.
    With room in the queue the lines after a slow command go in as they are read."""

    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 30.0)  # any wait would show
    writer = ThreadedFrameWriter(_Gate(open_=True), lambda: None)
    commands = _Commands(writer)
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)
    try:
        stdin.feed_data(b"slow\n")
        await _within_turns(lambda: commands.parsed == ["slow"], "the slow command did not start")
        for i in range(5):
            stdin.feed_data(f"{i}\n".encode())
            await _within_turns(
                lambda n=i: len(commands.parsed) == n + 2, f"line {i} waited for the slow command"
            )
    finally:
        await commands.end(stdin, pump)
        await writer.aclose(5)


async def test_near_the_mark_a_command_that_does_not_answer_is_waited_for_its_time_and_no_longer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wait is bounded, so an ``abort_bash`` behind a ``bash`` still gets in: the time runs
    from the slow command's own line, and once it is up that command is not waited for again."""

    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 1.0)
    writer = ThreadedFrameWriter(_Gate(open_=True), lambda: None, high_water=100)  # one may owe
    commands = _Commands(writer, reply=b"")
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)
    try:
        stdin.feed_data(b"slow\n")
        await _within_turns(lambda: commands.parsed == ["slow"], "the slow command did not start")
        started = time.monotonic()
        await asyncio.sleep(0.8)
        stdin.feed_data(b"next\n")
        await _until(lambda: commands.parsed == ["slow", "next"], 5, "the next line never went in")
        took = time.monotonic() - started
        assert 0.9 <= took < 1.5, f"the line behind the slow command went in after {took:.3f} s"
        await asyncio.sleep(0.05)  # "next" has answered
        stdin.feed_data(b"last\n")
        await _within_turns(
            lambda: commands.parsed == ["slow", "next", "last"],
            "the slow command was waited for a second time",
        )
    finally:
        await commands.end(stdin, pump)
        await writer.aclose(5)


async def test_two_slow_commands_each_count_for_their_own_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each command's time runs from its own line: when the older one's is up, the younger
    one still counts, so a line behind both waits for the younger one's time too."""

    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 2.0)
    writer = ThreadedFrameWriter(  # room for two unanswered commands
        _Gate(open_=True), lambda: None, high_water=2 * _stdout_writer._REPLY_RESERVE_BYTES
    )
    commands = _Commands(writer, reply=b"")
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)
    try:
        started = time.monotonic()
        stdin.feed_data(b"slow a\n")
        await asyncio.sleep(1.0)
        stdin.feed_data(b"slow b\nslow c\nslow d\n")
        await _within_turns(lambda: len(commands.parsed) == 2, "the second command did not start")
        await _until(lambda: len(commands.parsed) >= 3, 10, "the third line never went in")
        took = time.monotonic() - started
        assert 1.9 <= took < 2.6, f"the third line went in after {took:.3f} s, not the first's 2 s"
        await asyncio.sleep(0.3)
        assert len(commands.parsed) == 3, "the younger command stopped counting with the older"
        await _until(lambda: len(commands.parsed) == 4, 10, "the fourth line never went in")
        took = time.monotonic() - started
        assert 2.9 <= took < 3.6, f"the fourth line went in after {took:.3f} s, not the second's"
    finally:
        await commands.end(stdin, pump)
        await writer.aclose(5)


async def test_more_input_does_not_let_a_waiting_line_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A line that waits for a reply is woken when more input is read; it goes on waiting."""

    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 30.0)
    writer = ThreadedFrameWriter(_Gate(open_=True), lambda: None, high_water=100)  # one may owe
    commands = _Commands(writer, reply=b"")
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)
    try:
        stdin.feed_data(b"slow 1\nslow 2\n")
        await _within_turns(lambda: commands.parsed == ["slow 1"], "the first line did not go in")
        stdin.feed_data(b"third\n")
        await _until(lambda: commands.gate.held_lines == 2, 5, "the third line was not read")
        await asyncio.sleep(0.1)
        assert commands.parsed == ["slow 1"], "a line went in on a read, with a reply still owed"
    finally:
        await commands.end(stdin, pump)
        await writer.aclose(5)


async def test_the_line_let_in_at_a_drain_waits_for_the_command_before_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One line per drain, and one command at a time: the line a drain lets in above the mark
    waits for the command let in before it, so held lines are answered in the order written
    (in the round 2 head a malformed line let in at the next drain was answered before the
    ``get_state`` ahead of it)."""

    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 30.0)
    writer = _Behind()
    commands = _Commands(writer)
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)
    try:
        stdin.feed_data(b"slow\nnext\n")
        await _until(lambda: commands.gate.held_lines == 2, 5, "the lines were not read")
        writer.drain()
        await _until(lambda: commands.parsed == ["slow"], 5, "the first drain let no line in")
        writer.drain()
        await asyncio.sleep(0.2)
        assert commands.parsed == ["slow"], "a line went in while the one before it was unanswered"
        commands.release.set()  # it answers: the drain's line goes in, though nothing drained since
        await _until(lambda: commands.parsed == ["slow", "next"], 5, "the drain's line was lost")
    finally:
        writer.room = True
        writer.drain()
        await commands.end(stdin, pump)


async def test_a_reply_that_waits_at_the_mark_counts_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command counts until its reply is enqueued, not until its task ends: a turn that
    refills the queue at every drain keeps each answered command's task waiting at the mark,
    and the next drain's line must not wait for it."""

    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 30.0)
    steps = _Steps()
    writer = ThreadedFrameWriter(steps, lambda: None, high_water=100)
    commands = _Commands(writer, reply=b"r" * 120)
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)

    async def turn() -> None:  # as the held agent listener: woken by a drain, it enqueues again
        while True:
            await writer.wait_for_drain()
            writer.write(b"t" * 150)

    try:
        writer.write(b"a" * 150)  # the host is behind
        stdin.feed_data(b"1\n2\n")
        await _until(lambda: commands.gate.held_lines == 2, 5, "the lines were not read")
        refill = asyncio.ensure_future(turn())
        await asyncio.sleep(0.05)
        steps.release()  # a drain: the turn refills the queue, "1" goes in and its reply waits
        await _until(lambda: commands.parsed == ["1"], 5, "the first drain let no line in")
        await _until(lambda: writer.queued_bytes == 270, 5, "the reply was not enqueued")
        steps.release(2)  # the turn's frame and the reply reach the host: the next drain
        await _until(
            lambda: commands.parsed == ["1", "2"], 5, "the answered command was waited for"
        )
    finally:
        refill.cancel()
        steps.release(20)
        await commands.end(stdin, pump)
        await writer.aclose(5)


async def test_a_command_that_ends_without_a_reply_counts_no_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 30.0)
    writer = ThreadedFrameWriter(_Gate(open_=True), lambda: None, high_water=100)  # one may owe
    commands = _Commands(writer, reply=b"")
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)
    try:
        stdin.feed_data(b"dies\nnext\n")
        await _within_turns(
            lambda: commands.parsed == ["dies", "next"], "the ended command counted"
        )
    finally:
        await commands.end(stdin, pump)
        await writer.aclose(5)


async def test_an_eof_behind_a_line_that_waits_for_a_reply_is_seen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The input is read on while a line waits for a reply, as while it waits for a drain: the
    host's EOF is seen, and every line left is parsed at once."""

    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 30.0)
    writer = ThreadedFrameWriter(_Gate(open_=True), lambda: None, high_water=100)
    commands = _Commands(writer, reply=b"")
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)
    try:
        stdin.feed_data(b"slow 1\nslow 2\n")
        await _within_turns(lambda: commands.parsed == ["slow 1"], "the first line did not go in")
        await asyncio.sleep(0.1)
        assert commands.parsed == ["slow 1"] and commands.gate.held_lines == 1
        stdin.feed_eof()
        await asyncio.wait_for(pump, 5)
        assert commands.parsed == ["slow 1", "slow 2"]
    finally:
        await commands.end(stdin, pump)
        await writer.aclose(5)


async def test_an_injected_sink_parses_every_line_as_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round 2 verify's L3: an injected sink (the WebSocket host, the tests) is never
    behind, so no line waits for a command, however many are running."""

    monkeypatch.setattr(_stdout_writer, "_SETTLE_SECONDS", 30.0)
    commands = _Commands(InlineFrameWriter(lambda data: None, lambda: None))
    stdin = asyncio.StreamReader()
    pump = commands.pump(stdin)
    try:
        stdin.feed_data(b"".join(f"slow{i}\n".encode() for i in range(70)))
        await _within_turns(lambda: len(commands.parsed) == 70, "a line waited on an injected sink")
        stdin.feed_data(b"next\n")
        await _within_turns(lambda: commands.parsed[-1] == "next", "the next line waited")
    finally:
        await commands.end(stdin, pump)


class _Input:
    """A host's input that counts the reads: 4,096 bytes a read until ``data`` runs out."""

    def __init__(self, data: bytes, *, fail: bool = False) -> None:
        self.data = data
        self.reads = 0
        self._fail = fail

    async def read(self, n: int) -> bytes:
        await asyncio.sleep(0)
        if not self.data and self._fail:
            raise OSError(errno.EIO, "I/O error")
        self.reads += 1
        chunk, self.data = self.data[:n], self.data[n:]
        return chunk


async def test_input_stops_being_read_once_a_mark_of_it_waits() -> None:
    """The host's own writes wait in its pipe once a mark's worth of lines is held, and the rest
    is read and parsed when the host catches up."""

    from aelix_coding_agent.rpc._jsonl import JsonlLineReader

    gate = _Gate()
    parsed: list[str] = []
    writer = ThreadedFrameWriter(gate, lambda: None, high_water=10)
    input_gate = _stdout_writer.InputGate(writer, parsed.append)
    lines = 40_000
    host: Any = _Input(b"".join(f"{i:039d}\n".encode() for i in range(lines)))  # 1.6 MB
    try:
        writer.write(b"x" * 11)  # the host is behind
        pump = asyncio.ensure_future(input_gate.pump(host, JsonlLineReader(input_gate.take)))
        await _until(lambda: host.reads * 4096 >= _MIB, 30, "a mark's worth was not read")
        await asyncio.sleep(0.3)
        held = host.reads
        assert parsed == [], "a line was parsed while the host was behind"
        assert held * 4096 < _MIB + 3 * 4096 and host.data, "reading went on past the mark"
        assert len(writer._waiters) == 1, "the waits of the reads before were left behind"
        gate.opened.set()
        await asyncio.wait_for(pump, 10)
        assert parsed == [f"{i:039d}" for i in range(lines)]
    finally:
        gate.opened.set()
        await writer.aclose(5)


def _failing_input() -> Any:
    return _Input(b"a\nb\n", fail=True)


async def test_a_failed_read_parses_the_held_lines_first() -> None:
    """As the base's reader had parsed each line before the read that failed."""

    from aelix_coding_agent.rpc._jsonl import JsonlLineReader

    gate = _Gate()
    parsed: list[str] = []
    writer = ThreadedFrameWriter(gate, lambda: None, high_water=10)
    input_gate = _stdout_writer.InputGate(writer, parsed.append)
    try:
        writer.write(b"x" * 11)
        with pytest.raises(OSError):
            await asyncio.wait_for(
                input_gate.pump(_failing_input(), JsonlLineReader(input_gate.take)),
                5,
            )
        assert parsed == ["a", "b"]
    finally:
        gate.opened.set()
        await writer.aclose(5)


# === A closed pipe and other write errors ====================================


async def test_a_closed_pipe_still_ends_the_mode(host: _PipeHost) -> None:
    """#57 kept: the host closes its end; the next frame's failed write starts the shutdown.

    No EOF is fed, so only the closed pipe can end the mode, and the mode then raises
    ``BrokenPipeError`` so ``main_sync`` exits 141 (ADR-0180), as the base's last flush
    did. On Windows this is the ``EINVAL`` a write to a pipe with no reader raises there.
    """

    os.close(host.read_fd)
    host.read_fd = os.open(os.devnull, os.O_RDONLY)  # what ``close`` closes instead
    stdin = asyncio.StreamReader()
    task = asyncio.ensure_future(
        run_rpc_mode(_harness(), stdin=stdin, install_signal_handlers=False)
    )
    _send(stdin, {"type": "get_state", "id": "s"})
    with pytest.raises(BrokenPipeError):
        await asyncio.wait_for(task, 15)


def test_a_real_aelix_rpc_exits_141_when_its_host_closes_stdout(tmp_path: Path) -> None:
    """ADR-0180's "stdout consumer gone": the real ``aelix --mode rpc`` exits 141.

    The base exited 141 because its buffered stdout still held the frame the closed pipe
    refused; the writer thread holds nothing there, so the previous head exited 0.
    """

    env = child_env(
        tmp_path / "home",
        AELIX_CODING_AGENT_DIR=str(tmp_path / "agent"),
        AELIX_SETTINGS_PATH=str(tmp_path / "settings.json"),
        PYTHONUNBUFFERED="1",
        NO_COLOR="1",
    )
    argv = [sys.executable, "-m", "aelix_coding_agent", "--mode", "rpc", "--no-session"]
    argv += ["--offline", "--no-extensions"]
    proc = subprocess.Popen(
        argv,
        cwd=str(tmp_path),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdin, stdout, stderr = proc.stdin, proc.stdout, proc.stderr
    assert stdin is not None and stdout is not None and stderr is not None
    err = bytearray()
    drain = threading.Thread(target=lambda: err.extend(stderr.read()), daemon=True)
    drain.start()
    try:
        stdin.write(b'{"type": "get_state", "id": "ready"}\n')
        stdin.flush()
        first: list[bytes] = []
        reader = threading.Thread(target=lambda: first.append(stdout.readline()), daemon=True)
        reader.start()
        reader.join(90)
        assert first and b'"ready"' in first[0], "the child did not answer within 90 s"
        stdout.close()
        stdin.write(b'{"type": "get_state", "id": "after"}\n')
        stdin.flush()
        try:
            returncode = proc.wait(60)
        except subprocess.TimeoutExpired:
            pytest.fail("the child did not exit within 60 s of its host closing stdout")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        with contextlib.suppress(OSError):
            stdin.close()
        drain.join(10)
    text = err.decode("utf-8", errors="replace")
    assert returncode == 141, text
    assert "Traceback" not in text, text


async def test_a_closed_pipe_releases_a_waiting_producer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = _Gate()
    closed: list[bool] = []
    writer = ThreadedFrameWriter(gate, lambda: closed.append(True), high_water=10)
    try:
        writer.write(b"x" * 11)
        waiting = asyncio.ensure_future(writer.wait_for_backpressure())
        await asyncio.sleep(0.2)
        assert not waiting.done()
        gate.raise_next = BrokenPipeError(errno.EPIPE, "the host went away")
        gate.opened.set()
        await asyncio.wait_for(waiting, 5)
        await _until(lambda: closed == [True], 5, "the shutdown did not start")
        writer.write(b"later")
        assert writer.queued_bytes == 0, "a closed writer drops what nobody will read"
        assert isinstance(writer.failure, BrokenPipeError)
        assert await writer.aclose(1) is False
        with pytest.raises(BrokenPipeError):
            writer.raise_if_gone()
        assert capsys.readouterr().err == "", "a closed pipe ends the mode quietly, as at the base"
    finally:
        gate.opened.set()


async def test_an_unexpected_error_in_the_thread_ends_the_stream_and_wakes_the_waiters() -> None:
    """Codex C7: the writer thread never dies with a producer left waiting (or a traceback)."""

    def sink(data: memoryview) -> int:
        raise RuntimeError("not an OSError")

    closed: list[bool] = []
    writer = ThreadedFrameWriter(sink, lambda: closed.append(True), high_water=1)
    with contextlib.redirect_stderr(io.StringIO()) as err:
        writer.write(b"frame\n")
        await asyncio.wait_for(writer.wait_for_backpressure(), 5)
        await _until(lambda: closed == [True], 5, "the shutdown did not start")
    assert isinstance(writer.failure, RuntimeError)
    assert "[rpc] stdout cannot be written (RuntimeError" in err.getvalue()


async def test_a_pipe_that_closes_during_the_exit_drain_ends_it_at_once() -> None:
    gate = _Gate()
    closed: list[bool] = []
    writer = ThreadedFrameWriter(gate, lambda: closed.append(True))
    writer.write(b"one\n")
    writer.write(b"two\n")
    closing = asyncio.ensure_future(writer.aclose(10))
    await asyncio.sleep(0.1)
    gate.raise_next = BrokenPipeError(errno.EPIPE, "the host went away")
    started = time.monotonic()
    gate.opened.set()
    assert await asyncio.wait_for(closing, 5) is False
    assert time.monotonic() - started < 2.0, "the drain waited for a host that is gone"
    assert gate.written == [] and closed == [True]


def _descriptor_writer(
    monkeypatch: pytest.MonkeyPatch, fake_write: Any
) -> tuple[ThreadedFrameWriter, int, list[bool]]:
    """A writer over a real pipe whose ``os.write`` calls go through ``fake_write(real, fd, view)``."""

    read_fd, write_fd = os.pipe()
    real = os.write

    def write(fd: int, data: Any) -> int:
        if fd != write_fd:
            return real(fd, data)
        return fake_write(real, fd, data)

    closed: list[bool] = []
    sink = real_stdout_sink(_Fd(write_fd))
    monkeypatch.setattr(_stdout_writer.os, "write", write)
    writer = ThreadedFrameWriter(sink, lambda: closed.append(True))
    writer.write_fd = write_fd  # type: ignore[attr-defined]
    return writer, read_fd, closed


async def test_a_broken_stream_is_never_written_again(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex C4: after five bytes of a frame and then ``EIO``, the next frame is not glued to the
    partial one; the stream is treated as a closed pipe (the shutdown starts, 141)."""

    calls = 0

    def fake(real: Any, fd: int, view: Any) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real(fd, bytes(view[:5]))
        raise OSError(errno.EIO, "Input/output error")

    writer, read_fd, closed = _descriptor_writer(monkeypatch, fake)
    writer.write(b'{"id": "bad"}\n')
    writer.write(b'{"id": "good"}\n')
    assert await writer.aclose(5) is False
    os.close(writer.write_fd)  # type: ignore[attr-defined]
    assert _collect(read_fd) == b'{"id"', "nothing was appended to the partial frame"
    await _until(lambda: closed == [True], 5, "the shutdown did not start")
    assert isinstance(writer.failure, OSError) and writer.failure.errno == errno.EIO
    assert capsys.readouterr().err.count("[rpc] stdout cannot be written") == 1


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(lambda: InterruptedError(errno.EINTR, "Interrupted system call"), id="eintr"),
        pytest.param(lambda: BlockingIOError(errno.EAGAIN, "Resource busy"), id="eagain"),
        pytest.param(lambda: OSError(errno.ENOBUFS, "No buffer space available"), id="enobufs"),
    ],
)
async def test_a_retryable_error_loses_no_frame(
    monkeypatch: pytest.MonkeyPatch, error: Any
) -> None:
    """``EINTR`` retries at once; a full stream (``EAGAIN``, ``ENOBUFS``) waits and retries."""

    calls = 0

    def fake(real: Any, fd: int, view: Any) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error()
        return real(fd, view)

    writer, read_fd, closed = _descriptor_writer(monkeypatch, fake)
    writer.write(b'{"id": "first"}\n')
    writer.write(b'{"id": "second"}\n')
    assert await writer.aclose(5) is True
    os.close(writer.write_fd)  # type: ignore[attr-defined]
    assert _collect(read_fd) == b'{"id": "first"}\n{"id": "second"}\n'
    assert closed == [] and writer.failure is None


async def test_enospc_waits_on_windows_and_breaks_the_stream_elsewhere(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Windows' C runtime reports a full non-blocking pipe as ``ENOSPC`` (3.11 does not turn it
    into ``EAGAIN``); on POSIX it is a full disk."""

    for platform, whole in (("win32", True), ("linux", False)):
        calls = 0

        def fake(real: Any, fd: int, view: Any) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(fd, view)

        monkeypatch.setattr(sys, "platform", platform)
        writer, read_fd, closed = _descriptor_writer(monkeypatch, fake)
        writer.write(b"frame\n")
        assert await writer.aclose(5) is whole, platform
        monkeypatch.undo()
        os.close(writer.write_fd)  # type: ignore[attr-defined]
        assert _collect(read_fd) == (b"frame\n" if whole else b""), platform
    capsys.readouterr()


async def test_a_write_that_takes_nothing_waits_instead_of_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero-byte return is a full stream: retried every 10 ms, not as fast as the CPU goes
    (339,942 calls in 50 ms at the previous head, Codex C8)."""

    zeros = 0
    until = time.monotonic() + 0.1

    def fake(real: Any, fd: int, view: Any) -> int:
        nonlocal zeros
        if time.monotonic() < until:
            zeros += 1
            return 0
        return real(fd, view)

    writer, read_fd, closed = _descriptor_writer(monkeypatch, fake)
    writer.write(b"frame\n")
    assert await writer.aclose(5) is True
    os.close(writer.write_fd)  # type: ignore[attr-defined]
    assert _collect(read_fd) == b"frame\n"
    assert zeros <= 20, f"{zeros} writes in 100 ms"


def test_einval_is_a_closed_pipe_on_windows_only(monkeypatch: pytest.MonkeyPatch) -> None:
    einval = OSError(errno.EINVAL, "Invalid argument")
    eio = OSError(errno.EIO, "I/O error")
    assert pipe_closed(BrokenPipeError(errno.EPIPE, "Broken pipe"))
    monkeypatch.setattr(sys, "platform", "win32")
    assert pipe_closed(einval)
    assert not pipe_closed(eio), "on Windows too, only a closed pipe's errors count"
    monkeypatch.setattr(sys, "platform", "linux")
    assert not pipe_closed(einval)
    assert not pipe_closed(eio)


# === The exit =================================================================


async def test_the_exit_gives_up_on_a_host_that_never_reads(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = _Gate()
    writer = ThreadedFrameWriter(gate, lambda: None, high_water=10)
    try:
        writer.write(b"x" * 11)
        writer.write(b"y")
        held = asyncio.ensure_future(writer.wait_for_backpressure())
        started = time.monotonic()
        assert await writer.aclose(0.5) is False
        took = time.monotonic() - started
        assert 0.4 <= took < 2.0, took
        assert held.done(), "the exit released the waiting producer"
        err = capsys.readouterr().err
        assert "the host did not read for 0.5 s at exit: 2 frame(s), 12 bytes not written" in err
    finally:
        gate.opened.set()


async def test_the_exit_waits_the_drain_bound_and_no_longer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``aclose()`` with no argument waits :data:`_DRAIN_SECONDS` (Codex C9: a doubled bound
    survived every test that set its own)."""

    monkeypatch.setattr(_stdout_writer, "_DRAIN_SECONDS", 0.4)
    gate = _Gate()
    writer = ThreadedFrameWriter(gate, lambda: None)
    try:
        writer.write(b"x\n")
        started = time.monotonic()
        assert await writer.aclose() is False
        took = time.monotonic() - started
        assert 0.35 <= took < 0.75, took
        assert "did not read for 0.4 s" in capsys.readouterr().err
    finally:
        gate.opened.set()


async def test_the_count_leaves_out_the_bytes_that_reached_the_host(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A frame cut short: its bytes that reached the host are not counted as not written."""

    stop = threading.Event()

    def fake(real: Any, fd: int, view: Any) -> int:
        if len(view) == 20:
            return real(fd, bytes(view[:5]))
        stop.wait(30)
        return 0

    writer, read_fd, _ = _descriptor_writer(monkeypatch, fake)
    try:
        writer.write(b"y" * 19 + b"\n")
        assert await writer.aclose(0.3) is False
        assert "1 frame(s), 15 bytes not written" in capsys.readouterr().err
    finally:
        stop.set()
        os.close(writer.write_fd)  # type: ignore[attr-defined]
        _collect(read_fd)


async def test_the_exit_writes_everything_a_reading_host_takes() -> None:
    """The drain settles when the last frame is written, not when any frame is (W35)."""

    steps = _Steps()
    writer = ThreadedFrameWriter(steps, lambda: None)
    try:
        for i in range(3):
            writer.write(f"{i}\n".encode())
        closing = asyncio.ensure_future(writer.aclose(5))
        await asyncio.sleep(0.05)  # the drain is waiting
        for written in (1, 2):
            steps.release()
            await _until(lambda n=written: len(steps.written) == n, 5, "a frame was not written")
            await asyncio.sleep(0.1)
            assert not closing.done(), f"the drain settled with {3 - written} frame(s) queued"
        steps.release()
        assert await asyncio.wait_for(closing, 5) is True
        assert steps.written == [b"0\n", b"1\n", b"2\n"]
    finally:
        steps.release(5)


async def test_a_woken_producer_looks_again_before_it_goes_on() -> None:
    """Two producers wait; the drain wakes both; the first refills the queue past the mark, so
    the second waits again instead of going on above the mark."""

    steps = _Steps()
    writer = ThreadedFrameWriter(steps, lambda: None, high_water=10)
    try:
        writer.write(b"x" * 11)

        async def refill() -> None:
            await writer.wait_for_backpressure()
            writer.write(b"y" * 11)

        first = asyncio.ensure_future(refill())
        await asyncio.sleep(0.05)
        second = asyncio.ensure_future(writer.wait_for_backpressure())
        await asyncio.sleep(0.05)
        steps.release()  # "x" written: both are woken; "first" runs first
        await asyncio.wait_for(first, 5)
        await asyncio.sleep(0.2)
        assert not second.done(), "the second producer went on above the mark"
        steps.release()
        await asyncio.wait_for(second, 5)
    finally:
        steps.release(5)
        await writer.aclose(5)


async def test_the_exit_gives_up_mid_frame_after_the_write_in_progress() -> None:
    """Past the bound the thread finishes the write call it is in and writes nothing more, not
    even the rest of that frame."""

    gate = threading.Event()
    written = bytearray()

    def five(data: memoryview) -> int:
        gate.wait(30)
        written.extend(bytes(data[:5]))
        return 5

    writer = ThreadedFrameWriter(five, lambda: None)
    writer.write(b"0123456789\n")
    with contextlib.redirect_stderr(io.StringIO()):
        assert await writer.aclose(0.05) is False
    gate.set()
    thread = writer._thread
    assert thread is not None
    await asyncio.to_thread(thread.join, 5)
    assert bytes(written) == b"01234"


async def test_the_stderr_line_waits_its_bound_and_no_longer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex C3: a stderr that takes nothing holds the exit for 0.2 s, not until it is read."""

    read_fd, write_fd = os.pipe()
    stderr = open(write_fd, "w", encoding="utf-8", closefd=False)  # noqa: SIM115
    release = threading.Event()
    real = os.write

    def blocked(fd: int, data: Any) -> int:
        if fd == write_fd:
            release.wait(30)
        return real(fd, data)

    gate = _Gate()
    writer = ThreadedFrameWriter(gate, lambda: None)
    try:
        writer.write(b"x\n")
        monkeypatch.setattr(sys, "stderr", stderr)
        monkeypatch.setattr(_stdout_writer.os, "write", blocked)
        started = time.monotonic()
        assert await writer.aclose(0.1) is False
        took = time.monotonic() - started
        monkeypatch.undo()
        assert 0.25 <= took < 0.8, took
        release.set()
        await _until(lambda: _readable(read_fd), 5, "the line was not written once stderr took it")
        assert b"did not read for 0.1 s at exit" in os.read(read_fd, 4096)
    finally:
        release.set()
        gate.opened.set()
        stderr.close()
        os.close(write_fd)
        os.close(read_fd)


def _readable(fd: int) -> bool:
    import select

    if sys.platform == "win32":  # pragma: no cover - a pipe is not selectable there
        return True
    return bool(select.select([fd], [], [], 0)[0])


async def test_a_write_after_the_exit_gave_up_is_refused() -> None:
    """Past the bound the queued frames are dropped and a later write is refused; only the
    frame in the write call at that moment can still go out (Codex C7)."""

    gate = _Gate()
    writer = ThreadedFrameWriter(gate, lambda: None)
    writer.write(b"in flight\n")
    writer.write(b"queued\n")
    with contextlib.redirect_stderr(io.StringIO()):
        assert await writer.aclose(0.05) is False
    writer.write(b"after\n")
    assert writer.queued_bytes == len(b"in flight\n") + len(b"queued\n"), "a late write was taken"
    gate.opened.set()
    thread = writer._thread
    assert thread is not None
    await asyncio.to_thread(thread.join, 5)
    assert not thread.is_alive()
    assert gate.written == [b"in flight\n"]


async def test_a_clean_close_tells_nothing_and_leaves_no_thread(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = _Gate(open_=True)
    closed: list[bool] = []
    writer = ThreadedFrameWriter(gate, lambda: closed.append(True))
    assert writer._thread is None, "no thread before the first frame"
    writer.write(b"one\n")
    await _until(lambda: writer.queued_bytes == 0, 5, "the frame was not written")
    started = time.monotonic()
    assert await writer.aclose(5) is True
    assert time.monotonic() - started < 1.0, "an empty queue waited for the drain bound"
    thread = writer._thread
    assert thread is not None
    await asyncio.to_thread(thread.join, 5)
    assert not thread.is_alive()
    assert closed == [] and writer.failure is None
    assert capsys.readouterr().err == ""


_CHILD = r"""
import asyncio, os, sys
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.streaming import Model
try:
    from aelix_coding_agent.rpc import _stdout_writer
    _stdout_writer._DRAIN_SECONDS = float(os.environ["RPC_TEST_DRAIN"])
except ImportError:  # the base, before #852
    pass
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

if os.environ.get("RPC_TEST_FILL_STDERR"):
    os.set_blocking(2, False)
    try:
        while True:
            os.write(2, b"s" * 4096)
    except BlockingIOError:
        pass
    os.set_blocking(2, True)
harness = AgentHarness(AgentHarnessOptions(model=Model(id="mock", provider="mock")))
asyncio.run(run_rpc_mode(harness, install_signal_handlers=False))
if not os.environ.get("RPC_TEST_FILL_STDERR"):
    print("run_rpc_mode returned", file=sys.stderr, flush=True)
"""


def _spawn_child(tmp_path: Path, stdout: Any, **extra: str) -> subprocess.Popen[bytes]:
    """The child, unbuffered unless ``PYTHONUNBUFFERED=""`` (a buffered stdout has the lock)."""

    extra.setdefault("PYTHONUNBUFFERED", "1")
    env = child_env(
        tmp_path / "home",
        AELIX_CODING_AGENT_DIR=str(tmp_path / "agent"),
        RPC_TEST_DRAIN="1",
        **{key: value for key, value in extra.items() if value},
    )
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        cwd=str(tmp_path),
        env=env,
        stdin=subprocess.PIPE,
        stdout=stdout,
        stderr=subprocess.PIPE,
    )


def _end_input_and_time_the_exit(proc: subprocess.Popen[bytes], wait: float) -> float:
    commands = b"".join(
        (json.dumps({"type": "get_state", "id": f"r{i}"}) + "\n").encode() for i in range(2000)
    )
    assert proc.stdin is not None
    proc.stdin.write(commands)
    proc.stdin.close()
    started = time.monotonic()
    try:
        proc.wait(wait)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the process did not exit within {wait:g} s of its stdin's end")
    return time.monotonic() - started


def test_a_real_process_exits_past_a_host_that_never_reads(tmp_path: Path) -> None:
    """The process, not just the coroutine: stdin ends, stdout is full and never read.

    2,000 replies (about 1.3 MB) pass the 1 MiB mark, so the input is held and reply
    tasks are waiting at the writer when the EOF comes: the EOF parses the rest and the
    shutdown releases every wait before it waits for the tasks.

    Fails on the base, where the loop is blocked in the write and never sees the EOF (no
    exit within 30 s; the wait here is 90 s for a loaded machine's start-up). Also fails if
    the thread wrote through ``sys.__stdout__.buffer``: a thread blocked inside the
    buffered writer holds its lock and the interpreter's exit waits on it (measured on
    3.12: no exit in 15 s).
    """

    proc = _spawn_child(tmp_path, subprocess.PIPE)
    try:
        _end_input_and_time_the_exit(proc, 90)
        assert proc.stderr is not None
        err = proc.stderr.read().decode("utf-8", errors="replace")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()

    assert proc.returncode == 0, err
    assert "Fatal Python error" not in err, err
    assert "[rpc] the host did not read for 1 s at exit" in err, err
    assert "run_rpc_mode returned" in err


@pytest.mark.skipif(sys.platform == "win32", reason="os.set_blocking on a pipe: POSIX (3.11)")
def test_a_full_stderr_does_not_hold_the_exit(tmp_path: Path) -> None:
    """Codex C3: stdout and stderr both full and unread; the "not written" line must not block.

    At the previous head the line was a blocking ``print`` on the loop, and the process
    was still alive 6.6 s after its stdin ended (a 5 s drain).
    """

    proc = _spawn_child(tmp_path, subprocess.PIPE, RPC_TEST_FILL_STDERR="1")
    try:
        took = _end_input_and_time_the_exit(proc, 30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
    assert proc.returncode == 0
    assert took < 1.0 + 10.0, took


@pytest.mark.skipif(sys.platform == "win32", reason="a pseudo-terminal: POSIX only")
def test_a_terminal_that_is_not_read_does_not_hold_the_exit(tmp_path: Path) -> None:
    """Codex C3: stdout a terminal nobody reads, buffered as a terminal's stdout is without ``-u``.
    The previous head's terminal path held the buffer's lock in the writer thread, and the exit
    died in ``_enter_buffered_busy`` (status -6)."""

    import pty
    import tty

    master, slave = pty.openpty()
    tty.setraw(slave)
    try:
        proc = _spawn_child(tmp_path, slave, PYTHONUNBUFFERED="")
        os.close(slave)
        slave = -1
        try:
            _end_input_and_time_the_exit(proc, 60)
            assert proc.stderr is not None
            err = proc.stderr.read().decode("utf-8", errors="replace")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stderr is not None:
                proc.stderr.close()
    finally:
        if slave != -1:
            os.close(slave)
        os.close(master)
    assert proc.returncode == 0, err
    assert "Fatal Python error" not in err, err
    assert "[rpc] the host did not read for 1 s at exit" in err, err


# === The sink ================================================================


async def test_the_sink_writes_the_descriptor_not_the_buffered_stream() -> None:
    """A thread blocked in ``buffer.write`` would hold the buffer's lock through the exit."""

    read_fd, write_fd = os.pipe()
    received = bytearray()

    def read() -> None:
        while chunk := os.read(read_fd, 65536):
            received.extend(chunk)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    frame = b"z" * 300_000 + b"\n"  # more than a pipe holds: short writes continue
    try:
        writer = ThreadedFrameWriter(real_stdout_sink(_Fd(write_fd)), lambda: None)
        writer.write(frame)
        writer.write(b"tail\n")
        assert await writer.aclose(10) is True
    finally:
        os.close(write_fd)
        reader.join(10)
        os.close(read_fd)
    assert bytes(received) == frame + b"tail\n"


def test_the_sink_flushes_what_the_stream_already_held() -> None:
    """Bytes buffered in ``sys.__stdout__`` before the mode began go out before its first frame."""

    read_fd, write_fd = os.pipe()
    stream = open(write_fd, "wb", closefd=False)  # noqa: SIM115
    try:
        stream.write(b"earlier\n")  # buffered, not yet written
        sink = real_stdout_sink(stream)
        assert sink(memoryview(b"frame\n")) == 6
    finally:
        stream.close()
        os.close(write_fd)
    assert _collect(read_fd) == b"earlier\nframe\n"


def test_a_posix_terminal_is_written_through_its_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal on POSIX takes UTF-8 from ``os.write``, which holds no lock (Codex C3)."""

    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(_stdout_writer.os, "isatty", lambda fd: True)
    monkeypatch.setattr(sys, "platform", "linux")
    sink = real_stdout_sink(_Fd(write_fd))
    sink(memoryview("한글\n".encode()))
    os.close(write_fd)
    assert _collect(read_fd) == "한글\n".encode()


@pytest.mark.parametrize("unbuffered", [True, False], ids=["-u", "buffered"])
async def test_a_windows_console_is_written_through_its_raw_console_and_short_writes_continue(
    monkeypatch: pytest.MonkeyPatch, unbuffered: bool
) -> None:
    """Codex C8: CPython's raw Windows console may take only part of a frame; the rest of the
    frame must follow it. Under ``-u`` the stream's ``buffer`` is the raw console itself.
    Without it (the round 2 verify's V17) ``buffer`` is a ``BufferedWriter`` whose ``raw`` is
    the console: a frame written through that buffer is not flushed, and a thread blocked in
    it holds the lock the interpreter's exit waits on, so the sink writes the raw console."""

    class _ShortConsole:
        def __init__(self) -> None:
            self.out = bytearray()

        def write(self, data: Any) -> int:
            part = bytes(data[:16383])
            self.out.extend(part)
            return len(part)

    class _Buffered:
        def __init__(self) -> None:
            self.raw = _ShortConsole()

        def write(self, data: Any) -> int:
            raise AssertionError("the console was written through its buffer")

    class _Console:
        def __init__(self) -> None:
            self.buffer: Any = _ShortConsole() if unbuffered else _Buffered()

        def fileno(self) -> int:
            return 1

        def flush(self) -> None:
            return None

    monkeypatch.setattr(_stdout_writer.os, "isatty", lambda fd: True)
    monkeypatch.setattr(sys, "platform", "win32")
    console = _Console()
    raw = console.buffer if unbuffered else console.buffer.raw
    first = b'{"large": "' + b"x" * 32753 + b'"}\n'
    second = '{"next": "한글"}\n'.encode()
    writer = ThreadedFrameWriter(real_stdout_sink(console), lambda: None)
    writer.write(first)
    writer.write(second)
    assert await writer.aclose(5) is True
    assert bytes(raw.out) == first + second


def test_a_stream_with_no_descriptor_is_written_through_the_stream() -> None:
    """pytest's capture has no ``fileno()``: the base's buffered path, flushed."""

    class _Buffer(io.BytesIO):
        flushed = 0

        def flush(self) -> None:
            self.flushed += 1
            super().flush()

    class _Captured:
        def __init__(self) -> None:
            self.buffer = _Buffer()

        def fileno(self) -> int:
            raise io.UnsupportedOperation("fileno")

    captured = _Captured()
    sink = real_stdout_sink(captured)
    assert sink(memoryview(b"frame\n")) == 6
    assert captured.buffer.getvalue() == b"frame\n"
    assert captured.buffer.flushed >= 1, "the buffered frame was flushed"


def test_the_stream_sink_reads_einval_as_a_closed_pipe_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream with no descriptor: a Windows pipe with no reader still starts the shutdown (#57)."""

    class _Buffer:
        def write(self, data: bytes) -> None:
            raise OSError(errno.EINVAL, "Invalid argument")

        def flush(self) -> None:
            return None

    class _Stream:
        buffer = _Buffer()

        def write(self, text: str) -> None:
            pytest.fail("a closed pipe went to the text fallback")

    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(BrokenPipeError):
        stream_sink(_Stream())(b"x\n")


def test_an_injected_sink_that_breaks_starts_the_shutdown_and_drops_later_frames() -> None:
    """The WebSocket host's inline path (#57), unchanged from the base's ``_CloseOnBrokenPipe``."""

    written: list[bytes] = []
    closed: list[bool] = []

    def sink(data: bytes) -> None:
        if data == b"broken\n":
            raise BrokenPipeError(errno.EPIPE, "gone")
        written.append(data)

    writer = InlineFrameWriter(sink, lambda: closed.append(True))
    writer.write(b"one\n")
    with pytest.raises(BrokenPipeError):
        writer.write(b"broken\n")
    writer.write(b"later\n")
    assert written == [b"one\n"] and closed == [True]


# === The mode's lifetime =====================================================


async def test_a_session_change_moves_the_hold_to_the_new_harness(
    host: _PipeHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``rebind_session`` follows the new harness, so its turn waits at the mark too."""

    followed: list[Any] = []
    real_follow = ThreadedFrameWriter.follow

    def spy(self: ThreadedFrameWriter, harness: Any) -> None:
        followed.append(harness)
        real_follow(self, harness)

    monkeypatch.setattr(ThreadedFrameWriter, "follow", spy)
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_path = (await target.get_metadata()).path
    model = Model(id="mock", provider="mock")
    old = AgentHarness(AgentHarnessOptions(model=model, session=source))
    new = AgentHarness(AgentHarnessOptions(model=model, session=target))

    async def factory(_session: Any, **_kwargs: Any) -> AgentHarness:
        return new

    runtime = AgentSessionRuntime(old, factory, repo=repo, fs=fs)
    host.start()
    stdin = asyncio.StreamReader()
    task = asyncio.ensure_future(
        run_rpc_mode(
            runtime.harness, runtime_host=runtime, stdin=stdin, install_signal_handlers=False
        )
    )
    await host.ready(stdin)
    await runtime.switch_session(target_path)
    assert followed[:2] == [old, new], followed
    held = [
        fn for fn in new._listeners if getattr(fn, "__func__", None) is ThreadedFrameWriter._hold
    ]
    assert len(held) == 1, "the new harness's events wait at the mark"
    stdin.feed_eof()
    await asyncio.wait_for(task, 15)
    assert followed[-1] is None, "the exit ends the hold"


async def test_a_cancelled_mode_releases_a_held_turn_and_drains_within_the_bound(
    host: _PipeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelled (Ctrl+C under ``asyncio.run``) while the host is behind and a turn is held.

    The release at the top of the teardown lets the held turn settle at once, and the exit
    waits only the drain bound (0.2 s here) for a host that does not read. Without that
    release the disposal's abort finds the turn's terminal events waiting at the mark, and
    ``abort_until_idle`` cuts each one only at its 2 s re-abort (about 4 s in all).
    """

    monkeypatch.setattr(_stdout_writer, "_DRAIN_SECONDS", 0.2)
    host.start()
    stdin = asyncio.StreamReader()
    task = asyncio.ensure_future(
        run_rpc_mode(_harness(_long_turn()), stdin=stdin, install_signal_handlers=False)
    )
    await host.ready(stdin)
    host.reading.clear()
    _send(stdin, {"type": "prompt", "id": "p", "message": "go"})
    await asyncio.sleep(1.0)  # the turn reaches the mark and waits there
    started = time.monotonic()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, 30)
    took = time.monotonic() - started
    assert took < 3, f"the cancelled mode took {took:.2f} s to end"


async def test_a_mode_that_fails_to_start_leaves_no_writer_thread(
    host: _PipeHost, monkeypatch: pytest.MonkeyPatch, opened: list[Any]
) -> None:
    """Codex C7: stdin cannot be opened; the writer's thread starts with the first frame only."""

    async def broken_stdin(**_kwargs: Any) -> Any:
        raise OSError(errno.EBADF, "Bad file descriptor")

    monkeypatch.setattr(rpc_mode, "_open_stdin_reader", broken_stdin)
    for _ in range(3):
        with pytest.raises(OSError):
            await run_rpc_mode(_harness(), install_signal_handlers=False)
    assert len(opened) == 3
    alive = [w for w in opened if getattr(w, "_thread", None) is not None and w._thread.is_alive()]
    assert alive == [], "a refused start left its writer's thread behind"


async def test_the_drain_runs_when_a_retire_raises(
    host: _PipeHost, monkeypatch: pytest.MonkeyPatch, opened: list[Any]
) -> None:
    """Codex C7: ``control.retire()`` raising does not skip the writer's close."""

    from aelix_coding_agent.rpc.control import RpcControlPlane

    def broken_retire(self: Any) -> None:
        raise RuntimeError("retire failed")

    monkeypatch.setattr(RpcControlPlane, "retire", broken_retire)
    host.start()
    stdin = asyncio.StreamReader()
    task = asyncio.ensure_future(
        run_rpc_mode(_harness(), stdin=stdin, install_signal_handlers=False)
    )
    await host.ready(stdin)
    stdin.feed_eof()
    with pytest.raises(RuntimeError, match="retire failed"):
        await asyncio.wait_for(task, 15)
    writer = opened[0]
    assert writer._closing, "the writer was closed"
    thread = writer._thread
    await asyncio.to_thread(thread.join, 5)
    assert not thread.is_alive()


async def test_the_stderr_line_is_out_before_the_shutdown_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI on windows-latest (py3.11, py3.14): the thread started the shutdown before it wrote
    its stderr line, so the two tests above, which read stderr once the shutdown began, found
    it empty. A stderr with no descriptor, as here, now has the line before the shutdown
    starts, however slow the line is; a real descriptor gets it from a thread, as before."""

    tell = _stdout_writer.tell_stderr

    def tell_slowly(text: str, *, wait: float = 0.0) -> None:
        time.sleep(0.3)
        tell(text, wait=wait)

    monkeypatch.setattr(_stdout_writer, "tell_stderr", tell_slowly)

    def sink(data: memoryview) -> int:
        raise RuntimeError("not an OSError")

    closed: list[bool] = []
    writer = ThreadedFrameWriter(sink, lambda: closed.append(True))
    with contextlib.redirect_stderr(io.StringIO()) as err:
        writer.write(b"frame\n")
        await _until(lambda: closed == [True], 5, "the shutdown did not start")
        line = err.getvalue()
    assert "[rpc] stdout cannot be written (RuntimeError" in line, "the shutdown started before the line"


async def test_an_error_that_cannot_be_printed_still_starts_the_shutdown() -> None:
    """Delta verify F2: an ``OSError`` whose ``repr`` raises still starts the shutdown. With the
    line made before the shutdown was scheduled and nothing around it, the thread left ``_fail``
    on the ``repr``'s error and the shutdown never started."""

    class _Unprintable(OSError):
        def __repr__(self) -> str:
            raise ValueError("no repr")

    def sink(data: memoryview) -> int:
        raise _Unprintable(errno.EIO, "Input/output error")

    closed: list[bool] = []
    writer = ThreadedFrameWriter(sink, lambda: closed.append(True))
    writer.write(b"frame\n")
    await _until(lambda: closed == [True], 5, "the shutdown did not start")
    assert isinstance(writer.failure, _Unprintable)
