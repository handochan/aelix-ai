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

    :param max_line_bytes: OPTIONAL per-line budget, aelix-original. ``None``
        (the default) means unbounded, which is Pi's behaviour and what the
        SERVER's stdin intake must keep — see the class note below.
    """

    #: THE BUDGET IS PER-READER, AND THAT IS THE WHOLE POINT.
    #:
    #: This module serves BOTH directions of the wire: ``rpc_client`` reads a
    #: child's stdout with it, and ``rpc_mode`` reads the SERVER's stdin with
    #: it. A budget wired in unconditionally would therefore also silently
    #: swallow inbound *commands* — measured, a 5000-char command at a 2048
    #: budget vanished with no signal at all, and the server emits no error
    #: response for a line it never saw, so the client's ``_send`` future never
    #: resolves and the caller eats the full 30 s send timeout.
    #:
    #: So it is an opt-in constructor argument with a ``None`` default, and the
    #: server (``rpc_mode``'s ``JsonlLineReader(_on_line)``) does not opt in.
    #: A future budget on the intake direction must be an EXPLICIT over-budget
    #: error response, never a silent drop.

    def __init__(
        self,
        on_line: Callable[[str], None],
        *,
        max_line_bytes: int | None = None,
    ) -> None:
        self._on_line = on_line
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._max = max_line_bytes
        self._dropped = 0
        self._skipping = False

    @property
    def dropped_lines(self) -> int:
        """How many oversize lines were discarded. Always 0 when unbounded.

        Exposed as a counter rather than swallowed, because a dropped line is
        the one failure mode of a framing budget and it must have a route home:
        ``RpcClient`` republishes it and the delegation envelope carries it.
        """

        return self._dropped

    def _emit(self, line: str) -> None:
        # Pi parity: ``line.endsWith("\\r") ? line.slice(0, -1) : line``.
        if line.endswith("\r"):
            line = line[:-1]
        self._on_line(line)

    def _over_budget(self) -> bool:
        """Whether the un-terminated tail has outgrown the budget.

        MEASURED ON THE DECODED BUFFER, i.e. in characters, so a line made of
        multi-byte code points can reach up to 4x ``max_line_bytes`` before it
        trips. That is deliberate: the invariant this budget exists to hold is
        BOUNDEDNESS of parent memory against a child that never writes a
        newline, and a 4x slack on an 8 MiB ceiling still bounds it. Counting
        exact bytes would mean re-encoding the tail on every 4 KiB chunk.
        """

        return self._max is not None and len(self._buffer) > self._max

    def feed(self, chunk: bytes | str) -> None:
        """Pi parity: ``onData(chunk)`` (``jsonl.ts:28-39``)."""

        if isinstance(chunk, bytes):
            self._buffer += self._decoder.decode(chunk)
        else:
            self._buffer += chunk
        while True:
            idx = self._buffer.find("\n")
            if idx == -1:
                # No terminator yet. Drop the tail once it outgrows the budget
                # and RESYNC — everything up to the next newline belongs to the
                # line we just gave up on, so it must be discarded too or the
                # remainder would be emitted as a corrupt record.
                if self._over_budget():
                    if not self._skipping:
                        self._skipping = True
                        self._dropped += 1
                    self._buffer = ""
                return
            line = self._buffer[:idx]
            self._buffer = self._buffer[idx + 1 :]
            if self._skipping:
                # The terminator that ends the line we abandoned. Resync here.
                self._skipping = False
                continue
            if self._max is not None and len(line) > self._max:
                # A COMPLETE but oversize record. This leg is not redundant
                # with the un-terminated one above: a line that arrives with
                # its terminator inside the same chunk never reaches that
                # branch at all, so without this the budget only fired on
                # lines longer than the read chunk and a caller that set a
                # small budget silently got no enforcement.
                self._dropped += 1
                continue
            self._emit(line)

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
        if self._skipping:
            # A stream that ENDED mid-skip: the abandoned line never got its
            # terminator, so the residue is the tail of a record we already
            # counted as dropped and must not be emitted as if it were whole.
            self._buffer = ""
            self._skipping = False
            return
        if self._over_budget():
            self._dropped += 1
            self._buffer = ""
            return
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""


async def pump_jsonl_lines(
    stream: asyncio.StreamReader,
    reader: JsonlLineReader,
) -> None:
    """Drain ``stream`` into a reader the CALLER owns.

    The only difference from :func:`attach_jsonl_line_reader` is who holds the
    :class:`JsonlLineReader`, and that matters for exactly one reason: a caller
    that passed a ``max_line_bytes`` budget needs to read
    :attr:`JsonlLineReader.dropped_lines` *while the pump is still running*,
    which it cannot do when the reader is a local inside the pump.

    ``read``, never ``readline``: ``read(n)`` has no separator to fail to find,
    so it cannot raise ``StreamReader``'s limit error and cannot wedge the
    stream. (Measured: 200 001 bytes drained clean at the default 65536 limit,
    while ``readline()`` on the same stream raised.)
    """

    while True:
        chunk = await stream.read(4096)
        if not chunk:
            reader.end()
            return
        reader.feed(chunk)


async def attach_jsonl_line_reader(
    stream: asyncio.StreamReader,
    on_line: Callable[[str], None],
    *,
    max_line_bytes: int | None = None,
) -> None:
    """Pi parity: ``attachJsonlLineReader`` (async variant for RpcClient).

    Drains an :class:`asyncio.StreamReader` and dispatches each LF-framed
    record to ``on_line``. Returns when the stream reaches EOF (Pi's
    ``end`` event). Use ``asyncio.create_task(...)`` to run alongside
    other coroutines.
    """

    await pump_jsonl_lines(stream, JsonlLineReader(on_line, max_line_bytes=max_line_bytes))


__all__ = [
    "JsonlLineReader",
    "attach_jsonl_line_reader",
    "pump_jsonl_lines",
    "serialize_json_line",
]
