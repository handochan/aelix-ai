"""Pi parity: ``packages/coding-agent/src/modes/rpc/jsonl.ts`` regressions.

Covers the LF-only invariant (P-106 in
``.omc/specs/sprint-6d-phase-4-4-spec.md``), CR strip, multi-byte UTF-8
chunk-boundary safety, and end-of-stream tail emission.
"""

from __future__ import annotations

import json

from aelix_coding_agent.rpc._jsonl import (
    JsonlLineReader,
    serialize_json_line,
)


def test_serialize_round_trip_simple_dict() -> None:
    """serialize → split-and-parse → same dict."""

    payload = {"x": 1, "y": "two"}
    line = serialize_json_line(payload)
    assert line.endswith("\n")
    body = line[:-1]
    assert json.loads(body) == payload


def test_serialize_preserves_u2028_inside_string() -> None:
    """U+2028 / U+2029 are valid inside JSON strings (P-106).

    A JSON string containing a literal line-separator code point must
    round-trip without record corruption. Pi's serializer uses
    ``JSON.stringify`` (which by default leaves U+2028 unescaped); Aelix
    matches with ``json.dumps(..., ensure_ascii=False)``.
    """

    payload = {"text": "before after end"}
    line = serialize_json_line(payload)
    # Exactly one LF (the framing one) — no extra split markers introduced.
    assert line.count("\n") == 1
    decoded = json.loads(line.rstrip("\n"))
    assert decoded == payload
    assert " " in decoded["text"]


def test_reader_strips_cr_before_emit() -> None:
    """``hi\\r\\n`` → emit ``hi`` (CRLF tolerance, Pi ``line.endsWith("\\r")``)."""

    received: list[str] = []
    reader = JsonlLineReader(received.append)
    reader.feed("hi\r\n")
    assert received == ["hi"]


def test_reader_splits_only_on_lf_not_u2028() -> None:
    """LF-only framing — U+2028 inside a JSON string is NOT a record boundary."""

    record = '{"text": "a b"}'
    received: list[str] = []
    reader = JsonlLineReader(received.append)
    reader.feed(record + "\n")
    assert len(received) == 1
    assert json.loads(received[0]) == {"text": "a b"}


def test_reader_handles_multi_byte_split_across_chunks() -> None:
    """4-byte UTF-8 (emoji) split across feed boundaries decodes intact."""

    # "abc😀\n" — emoji = 4 bytes (F0 9F 98 80)
    raw = "abc\U0001f600\n".encode()
    received: list[str] = []
    reader = JsonlLineReader(received.append)
    # Split inside the emoji bytes (after byte 2 of 4).
    reader.feed(raw[: 3 + 2])  # 'abc' + first 2 bytes of emoji
    assert received == []
    reader.feed(raw[3 + 2 :])  # remaining 2 bytes + '\n'
    assert received == ["abc\U0001f600"]


def test_reader_emits_trailing_tail_on_end() -> None:
    """Buffer non-empty at end-of-stream → final line emitted (Pi ``onEnd``)."""

    received: list[str] = []
    reader = JsonlLineReader(received.append)
    reader.feed("partial")
    assert received == []
    reader.end()
    assert received == ["partial"]


def test_reader_end_with_empty_buffer_emits_nothing() -> None:
    received: list[str] = []
    reader = JsonlLineReader(received.append)
    reader.feed("complete\n")
    reader.end()
    assert received == ["complete"]


def test_reader_handles_multiple_records_in_one_feed() -> None:
    received: list[str] = []
    reader = JsonlLineReader(received.append)
    reader.feed("one\ntwo\nthree\n")
    assert received == ["one", "two", "three"]


def test_reader_handles_lf_inside_json_string_escaped() -> None:
    """``\\n`` inside a JSON string is escaped on the wire so it does NOT
    cause a split. The literal escape sequence is two characters
    ``\\`` ``n`` which is NOT a framing LF.
    """

    payload = {"text": "a\nb"}  # contains a literal LF inside the string
    line = serialize_json_line(payload)
    # The serializer escaped the LF as ``\n`` (two chars), so the only
    # raw LF in the buffer is the framing one at the end.
    assert line.count("\n") == 1
    received: list[str] = []
    reader = JsonlLineReader(received.append)
    reader.feed(line)
    assert received == [line[:-1]]
    assert json.loads(received[0]) == payload


def test_reader_handles_empty_lines() -> None:
    """Empty record between LFs emits empty string (Pi parity — caller filters)."""

    received: list[str] = []
    reader = JsonlLineReader(received.append)
    reader.feed("\n\n")
    assert received == ["", ""]
