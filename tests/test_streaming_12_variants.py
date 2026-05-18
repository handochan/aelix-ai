"""Sprint 6a (Phase 4.1, ADR-0037) — 12 ``AssistantMessageEvent`` variants.

Verifies the union mirrors Pi ``types.ts:347-359`` and each variant
exposes the Pi-named fields. The legacy :class:`AssistantEndEvent` is
exercised via subclass identity so existing test mocks keep working.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_ai.messages import AssistantMessage, ToolCallContent
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

_FIXTURE = (
    Path(__file__).parent
    / "pi_parity"
    / "fixtures"
    / "pi_assistant_message_events_734e08e.json"
)


def test_fixture_pi_sha_pin() -> None:
    fixture = json.loads(_FIXTURE.read_text())
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_pi_12_variants_present_in_union() -> None:
    """All 12 Pi variant names map to Aelix dataclasses."""

    fixture = json.loads(_FIXTURE.read_text())
    pi_names = [v["type"] for v in fixture["variants"]]
    assert len(pi_names) == 12

    aelix_types = {
        "start": AssistantStartEvent,
        "text_start": TextStartEvent,
        "text_delta": TextDeltaEvent,
        "text_end": TextEndEvent,
        "thinking_start": ThinkingStartEvent,
        "thinking_delta": ThinkingDeltaEvent,
        "thinking_end": ThinkingEndEvent,
        "toolcall_start": ToolCallStartEvent,
        "toolcall_delta": ToolCallDeltaEvent,
        "toolcall_end": ToolCallEndEvent,
        "done": AssistantDoneEvent,
        "error": AssistantErrorEvent,
    }
    for name in pi_names:
        cls = aelix_types[name]
        # Every variant exposes its ``type`` literal exactly matching Pi.
        instance = cls()  # type: ignore[call-arg]
        assert instance.type == name, (
            f"{cls.__name__} type literal {instance.type!r} != Pi {name!r}"
        )


def test_p39d_toolcall_delta_spelling_fixed() -> None:
    """P-39d SILENT DRIFT FIX: spelling is ``toolcall_delta`` (no underscore)."""

    evt = ToolCallDeltaEvent(delta="foo")
    assert evt.type == "toolcall_delta"
    # Old spelling MUST NOT appear anywhere.
    assert evt.type != "tool_call_delta"


def test_toolcall_delta_input_delta_alias_preserved() -> None:
    """Legacy ``input_delta`` accessor still works (deprecated property)."""

    evt = ToolCallDeltaEvent(delta="abc")
    assert evt.input_delta == "abc"


def test_assistant_end_event_legacy_alias_back_compat() -> None:
    """:class:`AssistantEndEvent` is a deprecated subclass of :class:`AssistantDoneEvent`.

    Existing test mocks that ``yield AssistantEndEvent(message=...)``
    keep working. The loop consumer accepts both ``"end"`` and ``"done"``
    as terminal-success markers.
    """

    msg = AssistantMessage(stop_reason="stop")
    legacy = AssistantEndEvent(message=msg)
    assert legacy.type == "end"
    assert isinstance(legacy, AssistantDoneEvent)
    assert legacy.message is msg


def test_text_delta_backfilled_fields() -> None:
    """TextDeltaEvent backfill — ``content_index`` + ``partial`` defaults work."""

    msg = AssistantMessage(content=[])
    evt = TextDeltaEvent(delta="hi", content_index=2, partial=msg)
    assert evt.content_index == 2
    assert evt.partial is msg


def test_toolcall_end_carries_tool_call_block() -> None:
    tc = ToolCallContent(tool_call_id="t1", tool_name="echo", input={"x": 1})
    msg = AssistantMessage(content=[tc])
    evt = ToolCallEndEvent(content_index=0, tool_call=tc, partial=msg)
    assert evt.tool_call.tool_call_id == "t1"
    assert evt.partial.content[0] is tc


def test_assistant_error_event_reason_enum() -> None:
    """:class:`AssistantErrorEvent` exposes Pi's two failure reasons."""

    for reason in ("aborted", "error"):
        evt = AssistantErrorEvent(reason=reason)  # type: ignore[arg-type]
        assert evt.reason == reason


def test_assistant_done_event_reason_enum() -> None:
    """:class:`AssistantDoneEvent` exposes Pi's three success reasons."""

    for reason in ("stop", "length", "toolUse"):
        evt = AssistantDoneEvent(reason=reason)  # type: ignore[arg-type]
        assert evt.reason == reason


def test_thinking_events_field_shape() -> None:
    msg = AssistantMessage(content=[])
    s = ThinkingStartEvent(content_index=0, partial=msg)
    d = ThinkingDeltaEvent(delta="reasoning", content_index=0, partial=msg)
    e = ThinkingEndEvent(content_index=0, content="reasoning", partial=msg)
    assert s.type == "thinking_start"
    assert d.type == "thinking_delta"
    assert e.type == "thinking_end"
    assert d.delta == "reasoning"
    assert e.content == "reasoning"


def test_text_start_and_text_end_field_shape() -> None:
    msg = AssistantMessage(content=[])
    s = TextStartEvent(content_index=0, partial=msg)
    e = TextEndEvent(content_index=0, content="hello", partial=msg)
    assert s.type == "text_start"
    assert e.type == "text_end"
    assert e.content == "hello"


def test_toolcall_start_field_shape() -> None:
    msg = AssistantMessage(content=[])
    s = ToolCallStartEvent(content_index=1, partial=msg)
    assert s.type == "toolcall_start"
    assert s.content_index == 1


def test_assistant_start_field_shape() -> None:
    """``AssistantStartEvent`` ships an empty ``AssistantMessage`` by default."""

    s = AssistantStartEvent()
    assert s.type == "start"
    assert isinstance(s.partial, AssistantMessage)


@pytest.mark.parametrize(
    "evt_cls,expected_type",
    [
        (AssistantStartEvent, "start"),
        (TextStartEvent, "text_start"),
        (TextDeltaEvent, "text_delta"),
        (TextEndEvent, "text_end"),
        (ThinkingStartEvent, "thinking_start"),
        (ThinkingDeltaEvent, "thinking_delta"),
        (ThinkingEndEvent, "thinking_end"),
        (ToolCallStartEvent, "toolcall_start"),
        (ToolCallDeltaEvent, "toolcall_delta"),
        (ToolCallEndEvent, "toolcall_end"),
        (AssistantDoneEvent, "done"),
        (AssistantErrorEvent, "error"),
    ],
)
def test_type_literal_each_variant(evt_cls: type, expected_type: str) -> None:
    instance = evt_cls()  # type: ignore[call-arg]
    assert instance.type == expected_type
