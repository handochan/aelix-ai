"""#15 (Gemini adapters) — ``ToolCallContent.thought_signature`` field.

Step 1 foundational coverage: the additive frozen-dataclass field that will
hold Gemini's multi-turn ``thoughtSignature`` (base64) for tool-call thinking
continuity. Mirrors :attr:`TextContent.text_signature` /
:attr:`ThinkingContent.thinking_signature` — default ``""`` so every existing
caller / snapshot / parity fixture is unaffected.
"""

from __future__ import annotations

import dataclasses

import pytest
from aelix_ai.messages import ToolCallContent


def test_default_thought_signature_is_empty_string() -> None:
    """A bare ToolCallContent defaults thought_signature to ``""``."""
    block = ToolCallContent(tool_call_id="call_1", tool_name="read", input={})
    assert block.thought_signature == ""


def test_thought_signature_round_trips() -> None:
    """An explicit base64 signature is preserved verbatim."""
    block = ToolCallContent(
        tool_call_id="call_1",
        tool_name="read",
        input={"path": "x"},
        thought_signature="Zm9vYmFy",
    )
    assert block.thought_signature == "Zm9vYmFy"


def test_thought_signature_field_present_with_string_default() -> None:
    """Field-presence: the dataclass declares thought_signature: str = ""."""
    fields = {f.name: f for f in dataclasses.fields(ToolCallContent)}
    assert "thought_signature" in fields
    assert fields["thought_signature"].type in (str, "str")
    assert fields["thought_signature"].default == ""


def test_thought_signature_is_frozen() -> None:
    """The field inherits the frozen-dataclass immutability guarantee."""
    block = ToolCallContent(tool_call_id="call_1", tool_name="read")
    with pytest.raises(dataclasses.FrozenInstanceError):
        block.thought_signature = "mutated"  # type: ignore[misc]


def test_replace_preserves_thought_signature() -> None:
    """``dataclasses.replace`` carries the signature unless overridden."""
    block = ToolCallContent(tool_name="read", thought_signature="sig")
    same = dataclasses.replace(block, tool_call_id="call_9")
    assert same.thought_signature == "sig"
    cleared = dataclasses.replace(block, thought_signature="")
    assert cleared.thought_signature == ""
