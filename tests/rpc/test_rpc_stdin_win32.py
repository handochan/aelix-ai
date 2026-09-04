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
import os
import sys

import pytest
from aelix_coding_agent.rpc.rpc_mode import (
    _open_stdin_reader,
    _thread_pumped_stdin_reader,
)


class _FakeStdin:
    """A ``sys.stdin`` stand-in whose ``fileno()`` is a real pipe (#202).

    The pump takes a DESCRIPTOR, never a stream object: reading a
    ``BufferedReader`` on the daemon thread held its lock through interpreter
    shutdown and killed the first rpc child ever allowed to exit cooperatively
    on ``windows-latest`` (``Fatal Python error: _enter_buffered_busy: could not
    acquire lock for <_io.BufferedReader name='<stdin>'> at interpreter
    shutdown, possibly due to daemon threads``). So the double is a pipe as
    well, with ``data`` already written and the write end closed, which is what
    makes every test here drive the exact code path a real stdin takes.
    """

    def __init__(self, data: bytes) -> None:
        self._read_fd, write_fd = os.pipe()
        os.write(write_fd, data)
        os.close(write_fd)

    def fileno(self) -> int:
        return self._read_fd

    def close(self) -> None:
        os.close(self._read_fd)


# === the thread pump actually yields lines ==================================


async def test_thread_pump_yields_jsonl_lines() -> None:
    stdin = _FakeStdin(b'{"id":"1"}\n{"id":"2"}\n')
    try:
        reader, thread = _thread_pumped_stdin_reader(stdin.fileno())

        assert await asyncio.wait_for(reader.readline(), 5) == b'{"id":"1"}\n'
        assert await asyncio.wait_for(reader.readline(), 5) == b'{"id":"2"}\n'
        # EOF, so the dispatch loop's stdin_eof path still fires.
        assert await asyncio.wait_for(reader.readline(), 5) == b""
        assert reader.at_eof()
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        stdin.close()


async def test_thread_pump_delivers_a_line_before_eof() -> None:
    """A short line is readable while the pipe is still open.

    A ``BufferedReader.read(n)`` on a pipe blocks until the buffer fills or the
    peer closes, which would stall the wire until 4 KiB of commands piled up.
    ``os.read`` returns as soon as ANY bytes are there, so a single short line
    arrives with the write end still open — measured here rather than assumed.
    """

    read_fd, write_fd = os.pipe()
    try:
        reader, thread = _thread_pumped_stdin_reader(read_fd)

        os.write(write_fd, b'{"id":"1"}\n')
        assert await asyncio.wait_for(reader.readline(), 5) == b'{"id":"1"}\n'
        assert not reader.at_eof()
        os.close(write_fd)
        assert await asyncio.wait_for(reader.readline(), 5) == b""
        thread.join(timeout=5)
    finally:
        os.close(read_fd)


async def test_thread_pump_treats_a_broken_stream_as_eof() -> None:
    """A stdin that errors must end the reader, not hang the RPC loop.

    A descriptor that is already closed makes ``os.read`` raise ``EBADF`` on
    the pump's first call — the same ``OSError`` shape a vanished stdin gives.
    """

    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    os.close(read_fd)

    reader, thread = _thread_pumped_stdin_reader(read_fd)

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
    stdin = _FakeStdin(b'{"id":"1"}\n')
    monkeypatch.setattr(sys, "stdin", stdin)
    try:
        reader, thread = await _open_stdin_reader(platform="win32")

        assert thread is not None  # the pump thread is the win32 discriminator
        assert await asyncio.wait_for(reader.readline(), 5) == b'{"id":"1"}\n'
        thread.join(timeout=5)
    finally:
        stdin.close()


# The only test in this file that fails on windows-latest, and it fails because
# it forces the POSIX arm onto a REAL ProactorEventLoop with a REAL
# ``os.pipe()``: IOCP cannot register a non-overlapped anonymous pipe handle
# (``OSError [WinError 6] The handle is invalid``, and CPython 3.11's asyncio
# then raises ``AttributeError: _empty_waiter`` on the way out). Skipping costs
# no coverage: ``test_posix_arm_would_fail_on_windows`` below pins the same
# property with a mocked ``connect_read_pipe`` and PASSES on windows, and
# ``_open_stdin_reader`` falls back to the real ``sys.platform``, so no actual
# Windows user reaches this arm.
@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "IOCP cannot register a non-overlapped anonymous pipe handle; the "
        "sibling test_posix_arm_would_fail_on_windows pins the property with a "
        "mock and passes there"
    ),
)
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
