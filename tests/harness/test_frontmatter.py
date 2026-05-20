"""Pi parity: shared YAML frontmatter parser.

Sprint 6h₁ W6 (W4 m4) regression — both ``prompt_templates`` and
``skills`` loaders now share :func:`aelix_agent_core.harness._frontmatter.parse_frontmatter`.
This file covers the helper directly.
"""

from __future__ import annotations

from aelix_agent_core.harness._frontmatter import parse_frontmatter


def test_no_leading_delimiter_returns_full_body() -> None:
    """Pi parity: missing leading ``---`` → no frontmatter, body is full text."""

    frontmatter, body, err = parse_frontmatter("plain body line")
    assert frontmatter == {}
    assert body == "plain body line"
    assert err is None


def test_no_closing_delimiter_returns_full_body() -> None:
    """Pi parity: leading ``---`` without closing ``\\n---`` → no frontmatter."""

    frontmatter, body, err = parse_frontmatter("---\nstill no end here\n")
    assert frontmatter == {}
    # Body keeps the leading ``---`` because no closing was found.
    assert body.startswith("---")
    assert err is None


def test_crlf_normalised_before_parse() -> None:
    """Pi parity: ``\\r\\n`` line endings normalised to ``\\n``."""

    frontmatter, body, err = parse_frontmatter(
        "---\r\nname: x\r\n---\r\nbody line"
    )
    assert frontmatter == {"name": "x"}
    assert body == "body line"
    assert err is None


def test_dict_frontmatter_round_trip() -> None:
    """Pi parity: typical YAML frontmatter parses into a plain dict."""

    frontmatter, body, err = parse_frontmatter(
        "---\nname: my-skill\ndescription: hi\n---\nbody"
    )
    assert frontmatter == {"name": "my-skill", "description": "hi"}
    assert body == "body"
    assert err is None


def test_empty_frontmatter_yields_empty_dict() -> None:
    """Pi parity: ``---\\n---`` with empty middle → empty dict."""

    frontmatter, body, err = parse_frontmatter("---\n---\nbody")
    assert frontmatter == {}
    assert body == "body"
    assert err is None


def test_non_dict_frontmatter_collapses_to_empty_dict() -> None:
    """Pi parity: scalar/list YAML at the top level collapses to ``{}``."""

    frontmatter, body, err = parse_frontmatter("---\njust a string\n---\nbody")
    assert frontmatter == {}
    assert body == "body"
    assert err is None


def test_yaml_parse_failure_returns_error_message() -> None:
    """Sprint 6h₁ W6 (P-233): YAML errors surface as the 3rd tuple element."""

    frontmatter, _body, err = parse_frontmatter(
        "---\n: this is :: not valid yaml: [oops\n---\nbody"
    )
    assert frontmatter is None
    assert err is not None
    assert err  # non-empty diagnostic
