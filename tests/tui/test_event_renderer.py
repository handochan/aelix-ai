"""Sprint 6h₁₀b (ADR-0105) — EventRenderer (sink-based) dispatch tests.

Uses the real harness/streaming event dataclasses; ``commit`` captures finished
Rich renderables and ``set_tail`` captures the live-window strings.
"""

from __future__ import annotations

from typing import Any

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


def _renderer() -> tuple[EventRenderer, list[Any], list[str]]:
    commits: list[Any] = []
    tails: list[str] = []
    return EventRenderer(commit=commits.append, set_tail=tails.append, width=80), commits, tails


def _plain(renderable: Any) -> str:
    return renderable.plain if hasattr(renderable, "plain") else str(renderable)


def _committed_text(commits: list[Any]) -> str:
    return "".join(_plain(c) for c in commits)


def _msg_update(stream_event: Any) -> MessageUpdateEvent:
    return MessageUpdateEvent(message=AssistantMessage(), assistant_message_event=stream_event)


def test_text_delta_accumulates_and_finalizes() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="Hello, ")))
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="world!")))
    r.on_agent_event(_msg_update(TextEndEvent(content="Hello, world!")))
    assert "Hello, world!" in _committed_text(commits)


def test_text_streams_to_tail_before_finalize() -> None:
    r, _c, tails = _renderer()
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="in progress")))
    assert tails and "in progress" in tails[-1]


def test_text_end_content_overrides_accumulation() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="partial")))
    r.on_agent_event(_msg_update(TextEndEvent(content="final canonical text")))
    assert "final canonical text" in _committed_text(commits)


def test_thinking_rendered_on_end() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta="let me ")))
    r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta="think")))
    assert "think" not in _committed_text(commits)
    r.on_agent_event(_msg_update(ThinkingEndEvent(content="let me think")))
    assert "let me think" in _committed_text(commits)


def test_thinking_end_empty_is_silent() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(_msg_update(ThinkingEndEvent(content="   ")))
    assert commits == []


def test_tool_start_renders_header_with_args() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        ToolExecutionStartEvent(tool_call_id="t1", tool_name="read_file", args={"path": "a.py"})
    )
    out = _committed_text(commits)
    assert "read_file" in out and "path" in out


def test_tool_start_truncates_long_args() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        ToolExecutionStartEvent(tool_call_id="t", tool_name="w", args={"data": "x" * 200})
    )
    assert "…" in _committed_text(commits)


def test_tool_end_renders_result() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="file contents here")]),
            tool_name="read_file",
        )
    )
    assert "file contents here" in _committed_text(commits)


def test_tool_end_error_uses_red_style() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="boom")], is_error=True),
            tool_name="bash",
            is_error=True,
        )
    )
    assert "boom" in _committed_text(commits)
    assert str(getattr(commits[-1], "style", "")) == "red"


def test_tool_end_empty_result_is_silent() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(tool_call_id="t1", result=ToolResult(content=[]), tool_name="noop")
    )
    assert commits == []


def test_streaming_error_event_renders() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        _msg_update(AssistantErrorEvent(reason="error", error_message="provider exploded"))
    )
    assert "provider exploded" in _committed_text(commits)


def test_message_end_error_renders() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        MessageEndEvent(
            message=AssistantMessage(stop_reason="error", error_message="rate limit")
        )
    )
    assert "rate limit" in _committed_text(commits)


def test_message_end_success_no_error() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(MessageEndEvent(message=AssistantMessage(stop_reason="stop")))
    assert "✖" not in _committed_text(commits)


def test_done_event_finalizes_open_text() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="partial answer")))
    r.on_agent_event(_msg_update(AssistantDoneEvent(reason="stop")))
    assert "partial answer" in _committed_text(commits)


def test_end_event_treated_as_done() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="ending text")))
    r.on_agent_event(_msg_update(AssistantEndEvent()))
    assert "ending text" in _committed_text(commits)


def test_toolcall_stream_events_are_noop() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(_msg_update(ToolCallStartEvent()))
    r.on_agent_event(_msg_update(ToolCallDeltaEvent(delta='{"x":1}')))
    r.on_agent_event(_msg_update(ToolCallEndEvent()))
    assert commits == []


def test_unknown_event_type_is_noop() -> None:
    r, commits, _t = _renderer()

    class _Future:
        type = "some_future_event"

    r.on_agent_event(_Future())  # type: ignore[arg-type]
    assert commits == []


def test_agent_end_is_noop() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(AgentEndEvent(messages=[]))
    assert commits == []


def test_message_start_resets_between_messages() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="first message")))
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))  # finalizes first
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="second")))
    r.on_agent_event(_msg_update(TextEndEvent(content="second")))
    out = _committed_text(commits)
    assert "first message" in out and "second" in out


# === §B — live tool-result interception ======================================


def _env(tool_name: str, view: str = "table", **payload: Any) -> Any:
    from aelix_agent_core.contracts.descriptor import DescriptorEnvelope

    body = {"kind": "tool-renderer-desc", "tool_name": tool_name, "view": view, **payload}
    return DescriptorEnvelope(
        kind="tool-renderer-desc", namespace="ext", id="t", payload=body  # type: ignore[arg-type]
    )


def _descriptor_renderer() -> Any:
    from aelix_coding_agent.tui.descriptors import DescriptorRegistry, DescriptorRenderer

    class _FakeChrome:
        def set_widget(self, *a: Any, **k: Any) -> None: ...

    return DescriptorRenderer(_FakeChrome(), object(), DescriptorRegistry())  # type: ignore[arg-type]


def _wire(r: EventRenderer, env: Any) -> None:
    dr = _descriptor_renderer()
    r.descriptor_renderer = dr
    r.get_tool_renderer_desc = lambda name: env if name == env.payload.tool_name else None


def test_tool_end_matching_descriptor_renders_custom_table() -> None:
    r, commits, _t = _renderer()
    env = _env("grep", view="table", columns=[{"key": "file", "header": "File"}])
    _wire(r, env)
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text='[{"file": "a.py"}]')]),
            tool_name="grep",
        )
    )
    # The custom view is a Rich Table — NOT a plain Text dump.
    assert commits and commits[-1].__class__.__name__ == "Table"


def test_tool_end_text_view_descriptor_renders_panel() -> None:
    r, commits, _t = _renderer()
    env = _env("echo", view="text")
    _wire(r, env)
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="raw line")]),
            tool_name="echo",
        )
    )
    assert commits and commits[-1].__class__.__name__ == "Panel"


def test_tool_end_no_matching_descriptor_uses_default() -> None:
    r, commits, _t = _renderer()
    env = _env("grep", view="table")
    _wire(r, env)
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="other output")]),
            tool_name="read_file",  # no descriptor for this tool
        )
    )
    # Default Text dump (not a Table) when the tool_name does not match.
    assert "other output" in _committed_text(commits)
    assert commits[-1].__class__.__name__ == "Text"


def test_tool_end_no_lookup_wired_uses_default() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="plain")]),
            tool_name="grep",
        )
    )
    assert "plain" in _committed_text(commits)
    assert commits[-1].__class__.__name__ == "Text"
