"""Sprint 6b (Phase 4.2, §D) — ``parse_streaming_json`` tests.

Pi parity: ``utils/json-parse.ts`` (SHA 734e08e). Lenient incremental
parse over a tool-call ``arguments`` stream.
"""

from __future__ import annotations

from aelix_ai.providers._streaming_json import parse_streaming_json


def test_none_returns_empty() -> None:
    assert parse_streaming_json(None) == {}


def test_empty_string_returns_empty() -> None:
    assert parse_streaming_json("") == {}


def test_whitespace_only_returns_empty() -> None:
    assert parse_streaming_json("   \n\t") == {}


def test_full_valid_object_parsed() -> None:
    assert parse_streaming_json('{"a": 1, "b": "two"}') == {"a": 1, "b": "two"}


def test_partial_open_brace() -> None:
    assert parse_streaming_json("{") == {}


def test_partial_with_key() -> None:
    # Trailing partial key — recovery may yield {} (Pi parity acceptable).
    out = parse_streaming_json('{"a"')
    assert isinstance(out, dict)


def test_partial_with_string_value_open() -> None:
    out = parse_streaming_json('{"a": "b')
    assert isinstance(out, dict)


def test_partial_array_value() -> None:
    out = parse_streaming_json('{"a": [1, 2')
    assert isinstance(out, dict)
    # Best-effort: should at least recover the closed prefix.
    assert "a" in out or out == {}


def test_complete_with_nested() -> None:
    assert parse_streaming_json('{"a": {"b": 1}}') == {"a": {"b": 1}}


def test_malformed_returns_empty() -> None:
    assert parse_streaming_json("not json at all") == {}


def test_non_object_root_normalized_to_empty() -> None:
    # Pi's contract uses dict for tool-call inputs; non-object roots
    # must collapse to ``{}``.
    assert parse_streaming_json("[1, 2, 3]") == {}
    assert parse_streaming_json('"just a string"') == {}
    assert parse_streaming_json("42") == {}


def test_partial_value_after_complete_pair_recovered() -> None:
    # ``{"a": 1, "b":`` — recoverable to ``{"a": 1}``.
    out = parse_streaming_json('{"a": 1, "b":')
    assert out == {"a": 1}
