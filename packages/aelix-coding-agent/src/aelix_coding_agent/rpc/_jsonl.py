"""Pi parity: ``packages/coding-agent/src/modes/rpc/jsonl.ts`` (58 LOC).

Strict JSONL framing for the RPC protocol.

LF-only framing is binding: payload strings MAY contain U+2028/U+2029
(valid inside JSON), so a Node ``readline`` (or any splitter that treats
those code points as line separators) will corrupt records. This module
splits on ``\\n`` only and strips a trailing CR for CRLF tolerance.
"""

from __future__ import annotations

import asyncio
import codecs
import json
from collections.abc import Callable


def serialize_json_line(value: object) -> str:
    """Pi parity: ``jsonl.ts:10-12`` (``serializeJsonLine``).

    LF-only framing. Payload strings MAY contain U+2028/U+2029 (valid
    inside JSON); clients MUST split records on ``\\n`` only.

    Aelix uses ``ensure_ascii=False`` so Pi-emitted Korean/Hangul (and any
    non-ASCII string content) round-trips identically. Pi's
    ``JSON.stringify`` is ASCII-safe by default but the JSON spec does not
    require escaping non-ASCII; the LF-only invariant is what matters.
    """

    return json.dumps(value, ensure_ascii=False) + "\n"


class JsonlLineReader:
    """Pi parity: ``jsonl.ts:21-58`` (``attachJsonlLineReader``).

    Streaming line reader that:

    - Decodes UTF-8 incrementally (multi-byte chunk-boundary safe — Pi
      uses ``StringDecoder("utf8")``; Aelix uses the equivalent
      ``codecs.getincrementaldecoder("utf-8")()``)
    - Splits on LF (``\\n``) only — NOT U+2028/U+2029
    - Strips trailing CR (CRLF tolerance) — Pi ``line.endsWith("\\r")``
    - Emits any non-empty buffer at end-of-stream — Pi ``onEnd``

    The reader is "fed" — callers push bytes (or str chunks) via
    :meth:`feed` and flush the trailing tail via :meth:`end`.
    """

    def __init__(self, on_line: Callable[[str], None]) -> None:
        self._on_line = on_line
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""

    def _emit(self, line: str) -> None:
        # Pi parity: ``line.endsWith("\\r") ? line.slice(0, -1) : line``.
        if line.endswith("\r"):
            line = line[:-1]
        self._on_line(line)

    def feed(self, chunk: bytes | str) -> None:
        """Pi parity: ``onData(chunk)`` (``jsonl.ts:28-39``)."""

        if isinstance(chunk, bytes):
            self._buffer += self._decoder.decode(chunk)
        else:
            self._buffer += chunk
        while True:
            idx = self._buffer.find("\n")
            if idx == -1:
                return
            self._emit(self._buffer[:idx])
            self._buffer = self._buffer[idx + 1 :]

    def end(self) -> None:
        """Pi parity: ``onEnd()`` (``jsonl.ts:41-46``).

        Flushes the incremental decoder, emits the residual buffer (if
        non-empty) as a final line, and resets internal state.
        """

        # ``decode(b"", final=True)`` flushes any pending partial multi-byte
        # sequence; matches Pi's ``decoder.end()``.
        tail = self._decoder.decode(b"", final=True)
        if tail:
            self._buffer += tail
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""


async def attach_jsonl_line_reader(
    stream: asyncio.StreamReader,
    on_line: Callable[[str], None],
) -> None:
    """Pi parity: ``attachJsonlLineReader`` (async variant for RpcClient).

    Drains an :class:`asyncio.StreamReader` and dispatches each LF-framed
    record to ``on_line``. Returns when the stream reaches EOF (Pi's
    ``end`` event). Use ``asyncio.create_task(...)`` to run alongside
    other coroutines.
    """

    reader = JsonlLineReader(on_line)
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            reader.end()
            return
        reader.feed(chunk)


__all__ = [
    "JsonlLineReader",
    "attach_jsonl_line_reader",
    "serialize_json_line",
]
