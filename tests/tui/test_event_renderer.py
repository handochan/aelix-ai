"""Sprint 6h₁₀a (ADR-0104) — EventRenderer dispatch tests.

Uses the real harness/streaming event dataclasses so the discriminated-union
field access is exercised exactly as in production. Output is captured to a
``StringIO``-backed Rich Console (no real terminal).
"""

from __future__ import annotations

import io

from aelix_agent_core.types import (
    AgentEndEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantEndEvent,
    AssistantErrorEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from aelix_ai.tools import ToolResult
from aelix_coding_agent.tui.render import EventRenderer
from rich.console import Console


def _renderer() -> tuple[EventRenderer, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    return EventRenderer(console), buf


def _msg_update(stream_event) -> MessageUpdateEvent:
    return MessageUpdateEvent(
        message=AssistantMessage(),
        assistant_message_event=stream_event,
    )


def test_text_delta_accumulates_and_finalizes() -> None:
    r, buf = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="Hello, ")))
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="world!")))
    r.on_agent_event(_msg_update(TextEndEvent(content="Hello, world!")))
    assert "Hello, world!" in buf.getvalue()


def test_text_end_content_overrides_accumulation() -> None:
    r, buf = _renderer()
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="partial")))
    r.on_agent_event(_msg_update(TextEndEvent(content="final canonical text")))
    assert "final canonical text" in buf.getvalue()


def test_thinking_rendered_dim_on_end() -> None:
    r, buf = _renderer()
    r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta="let me ")))
    r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta="think")))
    # Nothing printed until thinking_end (thin shell: no live thinking region).
    assert "think" not in buf.getvalue()
    r.on_agent_event(_msg_update(ThinkingEndEvent(content="let me think")))
    assert "let me think" in buf.getvalue()


def test_tool_start_renders_header_with_args() -> None:
    r, buf = _renderer()
    r.on_agent_event(
        ToolExecutionStartEvent(
            tool_call_id="t1", tool_name="read_file", args={"path": "a.py"}
        )
    )
    out = buf.getvalue()
    assert "read_file" in out
    assert "path" in out


def test_tool_end_renders_result() -> None:
    r, buf = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="file contents here")]),
            tool_name="read_file",
            is_error=False,
        )
    )
    assert "file contents here" in buf.getvalue()


def test_tool_end_error_renders() -> None:
    r, buf = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="boom")], is_error=True),
            tool_name="bash",
            is_error=True,
        )
    )
    assert "boom" in buf.getvalue()


def test_tool_end_empty_result_is_silent() -> None:
    r, buf = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[]),
            tool_name="noop",
        )
    )
    assert buf.getvalue() == ""


def test_error_event_renders_message() -> None:
    r, buf = _renderer()
    r.on_agent_event(
        _msg_update(
            AssistantErrorEvent(reason="error", error_message="provider exploded")
        )
    )
    assert "provider exploded" in buf.getvalue()


def test_error_event_without_message_uses_reason() -> None:
    r, buf = _renderer()
    r.on_agent_event(_msg_update(AssistantErrorEvent(reason="aborted")))
    assert "aborted" in buf.getvalue()


def test_toolcall_stream_events_are_noop() -> None:
    r, buf = _renderer()
    # Tool-arg streaming is intentionally not rendered (keyed off harness layer).
    r.on_agent_event(_msg_update(ToolCallStartEvent()))
    r.on_agent_event(_msg_update(ToolCallDeltaEvent(delta='{"x":1}')))
    r.on_agent_event(_msg_update(ToolCallEndEvent()))
    assert buf.getvalue() == ""


# === HIGH-1: terminal failures arrive as MessageEndEvent (loop.py:299-310) ===


def test_message_end_error_renders() -> None:
    r, buf = _renderer()
    r.on_agent_event(
        MessageEndEvent(
            message=AssistantMessage(
                stop_reason="error", error_message="provider exploded"
            )
        )
    )
    assert "provider exploded" in buf.getvalue()


def test_message_end_aborted_without_message_uses_reason() -> None:
    r, buf = _renderer()
    r.on_agent_event(
        MessageEndEvent(message=AssistantMessage(stop_reason="aborted"))
    )
    out = buf.getvalue()
    assert "aborted" in out and "✖" in out


def test_message_end_success_renders_no_error() -> None:
    r, buf = _renderer()
    r.on_agent_event(MessageEndEvent(message=AssistantMessage(stop_reason="stop")))
    assert "✖" not in buf.getvalue()


def test_message_end_error_after_streamed_text_shows_both() -> None:
    r, buf = _renderer()
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="partial output ")))
    r.on_agent_event(
        MessageEndEvent(
            message=AssistantMessage(stop_reason="error", error_message="rate limit")
        )
    )
    out = buf.getvalue()
    assert "partial output" in out  # streamed text finalized
    assert "rate limit" in out  # error surfaced


# === done / end finalization (spec §3.2) ===


def test_done_event_finalizes_open_text() -> None:
    r, buf = _renderer()
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="partial answer")))
    r.on_agent_event(_msg_update(AssistantDoneEvent(reason="stop")))
    assert "partial answer" in buf.getvalue()


def test_end_event_treated_as_done() -> None:
    r, buf = _renderer()
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="ending text")))
    r.on_agent_event(_msg_update(AssistantEndEvent()))
    assert "ending text" in buf.getvalue()


# === edge cases ===


def test_thinking_end_empty_content_is_silent() -> None:
    r, buf = _renderer()
    r.on_agent_event(_msg_update(ThinkingEndEvent(content="   ")))
    assert buf.getvalue() == ""


def test_tool_start_truncates_long_args() -> None:
    r, buf = _renderer()
    r.on_agent_event(
        ToolExecutionStartEvent(
            tool_call_id="t1", tool_name="write", args={"data": "x" * 200}
        )
    )
    assert "…" in buf.getvalue()


def test_tool_end_error_uses_red_ansi() -> None:
    r, buf = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="boom")], is_error=True),
            tool_name="bash",
            is_error=True,
        )
    )
    # force_terminal=True emits the ANSI red foreground code for error styling.
    assert "\x1b[31m" in buf.getvalue()


def test_unknown_harness_event_type_is_noop() -> None:
    r, buf = _renderer()

    class _Future:
        type = "some_future_event"

    r.on_agent_event(_Future())  # type: ignore[arg-type]
    assert buf.getvalue() == ""


def test_agent_end_event_is_noop() -> None:
    r, buf = _renderer()
    r.on_agent_event(AgentEndEvent(messages=[]))
    assert buf.getvalue() == ""


def test_message_start_resets_state_between_messages() -> None:
    r, buf = _renderer()
    # First message streams text but never sends text_end/done.
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="first message")))
    # Second message_start must finalize the first stream and start clean.
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="second")))
    r.on_agent_event(_msg_update(TextEndEvent(content="second")))
    out = buf.getvalue()
    assert "first message" in out  # finalized on the second message_start
    assert "second" in out
