"""Sprint 6h₆ (Phase 5a-i, ADR-0089) — ``cli/initial_message.py`` tests.

Covers composition order, the .shift() mutation regression (P-388),
missing-parts shapes, and the empty-result None return.
"""

from __future__ import annotations

from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.initial_message import (
    InitialMessage,
    build_initial_message,
)

# === Composition order =======================================================


def test_composition_stdin_only() -> None:
    parsed = Args()
    result = build_initial_message(parsed, stdin_content="STDIN")
    assert result.initial_message == "STDIN"
    assert result.initial_images is None


def test_composition_file_only() -> None:
    parsed = Args()
    result = build_initial_message(parsed, file_text="FILE")
    assert result.initial_message == "FILE"


def test_composition_message_only() -> None:
    parsed = Args(messages=["MSG"])
    result = build_initial_message(parsed)
    assert result.initial_message == "MSG"


def test_composition_full_order_no_separator() -> None:
    """Pi parity: ``stdin + fileText + firstMessage`` with no separator."""

    parsed = Args(messages=["MSG"])
    result = build_initial_message(
        parsed, file_text="FILE", stdin_content="STDIN"
    )
    assert result.initial_message == "STDINFILEMSG"


def test_composition_stdin_plus_file() -> None:
    parsed = Args()
    result = build_initial_message(
        parsed, file_text="FILE", stdin_content="STDIN"
    )
    assert result.initial_message == "STDINFILE"


def test_composition_stdin_plus_message() -> None:
    parsed = Args(messages=["MSG"])
    result = build_initial_message(parsed, stdin_content="STDIN")
    assert result.initial_message == "STDINMSG"


def test_composition_file_plus_message() -> None:
    parsed = Args(messages=["MSG"])
    result = build_initial_message(parsed, file_text="FILE")
    assert result.initial_message == "FILEMSG"


# === .shift() mutation regression — Pi parity (P-388) =======================


def test_shift_mutation_pops_first_message() -> None:
    """REGRESSION TEST: Pi ``.shift()`` mutates ``parsed.messages``."""

    parsed = Args(messages=["first", "second", "third"])
    build_initial_message(parsed)
    # SIDE EFFECT — first message MUST be removed from the list.
    assert parsed.messages == ["second", "third"]


def test_shift_mutation_single_message_empties_list() -> None:
    parsed = Args(messages=["only"])
    build_initial_message(parsed)
    assert parsed.messages == []


def test_shift_mutation_no_messages_no_side_effect() -> None:
    parsed = Args(messages=[])
    build_initial_message(parsed, file_text="FILE")
    assert parsed.messages == []


def test_shift_runs_even_with_stdin_and_file() -> None:
    """Pi parity: ``.shift()`` runs regardless of stdin/file presence."""

    parsed = Args(messages=["a", "b"])
    build_initial_message(
        parsed, file_text="F", stdin_content="S"
    )
    assert parsed.messages == ["b"]


# === Empty results = None ====================================================


def test_no_inputs_returns_none() -> None:
    parsed = Args()
    result = build_initial_message(parsed)
    assert result.initial_message is None
    assert result.initial_images is None


def test_empty_stdin_string_still_counts() -> None:
    """Pi parity: empty stdin string is still appended (Pi checks
    ``stdinContent !== undefined``, not truthiness)."""

    parsed = Args()
    result = build_initial_message(parsed, stdin_content="")
    # Empty string counts — parts = [""], join = ""
    # ``"".join([""])`` is ``""`` which is falsy but not None.
    # Per spec: ``parts.length > 0`` → ``"".join([""]) == ""`` is returned.
    assert result.initial_message == ""


def test_falsy_file_text_skipped() -> None:
    """``file_text`` only joins when truthy (Pi ``if (fileText)``)."""

    parsed = Args()
    result = build_initial_message(parsed, file_text="")
    assert result.initial_message is None


# === Images ==================================================================


def test_images_passthrough() -> None:
    parsed = Args()
    images: list[object] = ["img1", "img2"]
    result = build_initial_message(parsed, file_images=images)
    assert result.initial_images == ["img1", "img2"]


def test_empty_images_returns_none() -> None:
    parsed = Args()
    result = build_initial_message(parsed, file_images=[])
    assert result.initial_images is None


# === Dataclass smoke =========================================================


def test_initial_message_dataclass_defaults() -> None:
    im = InitialMessage()
    assert im.initial_message is None
    assert im.initial_images is None
