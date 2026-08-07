"""Windows-asserting tests for the RPC stdin reader (#107).

``loop.connect_read_pipe`` raises ``NotImplementedError`` on Windows'
ProactorEventLoop, so ``--mode rpc`` died at startup before it could read a
single command. These tests RUN on Linux: the win32 arm is selected by
injecting ``platform="win32"``, and one of them makes ``connect_read_pipe``
raise exactly as Windows does, so the claim "the win32 arm does not go near it"
is measured rather than asserted.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import threading

import pytest
from aelix_coding_agent.rpc.rpc_mode import (
    _open_stdin_reader,
    _thread_pumped_stdin_reader,
)


class _FakeStdin:
    """A ``sys.stdin`` stand-in exposing the binary ``.buffer`` the pump reads."""

    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


# === the thread pump actually yields lines ==================================


async def test_thread_pump_yields_jsonl_lines() -> None:
    stream = io.BytesIO(b'{"id":"1"}\n{"id":"2"}\n')

    reader, thread = _thread_pumped_stdin_reader(stream)

    assert await asyncio.wait_for(reader.readline(), 5) == b'{"id":"1"}\n'
    assert await asyncio.wait_for(reader.readline(), 5) == b'{"id":"2"}\n'
    # EOF, so the dispatch loop's stdin_eof path still fires.
    assert await asyncio.wait_for(reader.readline(), 5) == b""
    assert reader.at_eof()
    thread.join(timeout=5)
    assert not thread.is_alive()


async def test_thread_pump_delivers_a_line_before_eof() -> None:
    """Line-at-a-time, not 4 KiB-at-a-time.

    A fixed-size ``read(4096)`` on a pipe blocks until the buffer fills or the
    peer closes, which would stall the wire until 4 KiB of commands piled up.
    A single short line must be readable while the stream is still open.
    """

    class _OneLineThenBlockForever(io.RawIOBase):
        def __init__(self) -> None:
            self._sent = False
            self.released = threading.Event()

        def readline(self, _size: int = -1) -> bytes:  # type: ignore[override]
            if not self._sent:
                self._sent = True
                return b'{"id":"1"}\n'
            self.released.wait(5)
            return b""

    stream = _OneLineThenBlockForever()
    reader, thread = _thread_pumped_stdin_reader(stream)

    assert await asyncio.wait_for(reader.readline(), 5) == b'{"id":"1"}\n'
    stream.released.set()
    thread.join(timeout=5)


async def test_thread_pump_treats_a_broken_stream_as_eof() -> None:
    """A stdin that errors must end the reader, not hang the RPC loop."""

    class _Broken(io.RawIOBase):
        def readline(self, _size: int = -1) -> bytes:  # type: ignore[override]
            raise OSError("stdin went away")

    reader, thread = _thread_pumped_stdin_reader(_Broken())

    assert await asyncio.wait_for(reader.readline(), 5) == b""
    thread.join(timeout=5)


# === arm selection ==========================================================


async def test_win32_uses_the_thread_pump_not_connect_read_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce the Windows failure and prove the win32 arm avoids it."""

    loop = asyncio.get_running_loop()

    async def windows_connect_read_pipe(*_a: object, **_k: object) -> None:
        raise NotImplementedError("ProactorEventLoop has no connect_read_pipe")

    monkeypatch.setattr(loop, "connect_read_pipe", windows_connect_read_pipe)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b'{"id":"1"}\n'))

    reader, thread = await _open_stdin_reader(platform="win32")

    assert thread is not None  # the pump thread is the win32 discriminator
    assert await asyncio.wait_for(reader.readline(), 5) == b'{"id":"1"}\n'
    thread.join(timeout=5)


async def test_posix_still_uses_connect_read_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSIX is unchanged: asyncio owns the transport and there is no thread."""

    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(read_fd, "rb", buffering=0) as pipe_in:
            monkeypatch.setattr(sys, "stdin", pipe_in)

            reader, thread = await _open_stdin_reader(platform="linux")

            assert thread is None
            os.write(write_fd, b'{"id":"1"}\n')
            assert await asyncio.wait_for(reader.readline(), 5) == b'{"id":"1"}\n'
    finally:
        os.close(write_fd)


async def test_posix_arm_would_fail_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the bug: the POSIX arm is exactly what broke ``--mode rpc``."""

    loop = asyncio.get_running_loop()

    async def windows_connect_read_pipe(*_a: object, **_k: object) -> None:
        raise NotImplementedError("ProactorEventLoop has no connect_read_pipe")

    monkeypatch.setattr(loop, "connect_read_pipe", windows_connect_read_pipe)

    with pytest.raises(NotImplementedError):
        await _open_stdin_reader(platform="linux")
