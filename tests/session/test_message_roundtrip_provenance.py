"""A persisted message must come back carrying everything it was written with.

``_message_to_dict`` is ``dataclasses.asdict``, so every field reaches the JSONL.
``_message_from_dict`` rebuilds field-by-field and silently dropped several of
them, so the values were on disk but never survived a reload:

- ``AssistantMessage.provider`` / ``.model`` / ``.api`` — ADR-0190 stamps this
  provenance so a reload can tell a same-model turn from a cross-model one. It
  is also the only thing that says which price list a turn should be billed at,
  so a resumed session could not be costed.
- ``AssistantMessage.response_id`` — the OpenAI Responses adapter chains turns
  through ``previous_response_id``; a dropped id breaks that continuity.
- ``ToolResultMessage.tool_name`` — adapters that must wrap a tool result with
  its function name (Moonshot, Together, Cloudflare) read it.
"""

from __future__ import annotations

from aelix_agent_core.session.entries import (
    MessageEntry,
    entry_from_json,
    entry_to_json,
)
from aelix_ai.messages import AssistantMessage, TextContent, ToolResultMessage

_TS = "2026-08-07T10:00:00.000Z"


def _roundtrip(message: object) -> object:
    entry = MessageEntry(id="1", parent_id=None, timestamp=_TS, message=message)  # type: ignore[arg-type]
    restored = entry_from_json(entry_to_json(entry))
    return restored.message  # type: ignore[attr-defined]


def test_assistant_provenance_survives_a_roundtrip() -> None:
    original = AssistantMessage(
        content=[TextContent(text="hi")],
        usage={"input": 10, "output": 5},
        api="anthropic-messages",
        provider="anthropic",
        model="claude-haiku-4-5",
    )
    restored = _roundtrip(original)
    assert restored.api == "anthropic-messages"
    assert restored.provider == "anthropic"
    assert restored.model == "claude-haiku-4-5"


def test_assistant_response_id_survives_a_roundtrip() -> None:
    original = AssistantMessage(
        content=[TextContent(text="hi")], response_id="resp_abc123"
    )
    assert _roundtrip(original).response_id == "resp_abc123"


def test_assistant_usage_and_stop_reason_still_survive() -> None:
    """The fields that already round-tripped must keep doing so."""

    original = AssistantMessage(
        content=[TextContent(text="hi")],
        usage={"input": 10, "output": 5},
        stop_reason="end_turn",
        error_message=None,
    )
    restored = _roundtrip(original)
    assert restored.usage == {"input": 10, "output": 5}
    assert restored.stop_reason == "end_turn"


def test_tool_result_tool_name_survives_a_roundtrip() -> None:
    original = ToolResultMessage(
        tool_call_id="call_1",
        content=[TextContent(text="done")],
        tool_name="read_file",
    )
    restored = _roundtrip(original)
    assert restored.tool_call_id == "call_1"
    assert restored.tool_name == "read_file"


def test_a_resumed_assistant_message_can_still_be_priced() -> None:
    """The end the provenance serves: a reloaded turn is costable."""

    from aelix_agent_core.harness._session_stats import aggregate_session_stats

    original = AssistantMessage(
        content=[TextContent(text="hi")],
        usage={"input": 4000, "output": 369},
        provider="anthropic",
        model="claude-haiku-4-5",
    )
    stats = aggregate_session_stats("s", [_roundtrip(original)])  # type: ignore[list-item]
    assert stats.cost > 0.0
    assert stats.cost_known is True
