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
from aelix_coding_agent.tools._truncate import TruncationInfo
from aelix_coding_agent.tools.bash import BashToolDetails
from aelix_coding_agent.tui.render import EventRenderer, _tool_header, _truncate_lines
from rich.cells import cell_len


def _renderer() -> tuple[EventRenderer, list[Any], list[str]]:
    commits: list[Any] = []
    tails: list[str] = []
    return EventRenderer(commit=commits.append, set_tail=tails.append, width=80), commits, tails


def _plain(renderable: Any) -> str:
    if hasattr(renderable, "renderables"):  # rich Group → flatten its rows
        return "".join(_plain(child) for child in renderable.renderables)
    return renderable.plain if hasattr(renderable, "plain") else str(renderable)


def _committed_text(commits: list[Any]) -> str:
    return "".join(_plain(c) for c in commits)


def _row_styles(group: Any) -> list[str]:
    return [str(getattr(row, "style", "")) for row in group.renderables]


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
    r.hide_thinking = False  # test the full-visible render path (ADR-0115)
    r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta="let me ")))
    r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta="think")))
    assert "think" not in _committed_text(commits)
    r.on_agent_event(_msg_update(ThinkingEndEvent(content="let me think")))
    assert "let me think" in _committed_text(commits)


def test_thinking_end_empty_is_silent() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(_msg_update(ThinkingEndEvent(content="   ")))
    assert commits == []


def test_thinking_visible_by_default() -> None:
    # Issue #50 reconcile: the renderer's hardcoded default is now VISIBLE
    # (matching pi's ``hideThinkingBlock`` default of False), so headless /
    # no-settings contexts agree with the run_tui startup seed.
    r, _commits, _t = _renderer()
    assert r.hide_thinking is False


def test_thinking_collapsed_when_hidden_with_expand() -> None:
    # Sprint 6h₁₅ (ADR-0123): when HIDDEN, thinking collapses to a "Thinking…"
    # placeholder + /expand id; the full reasoning is recoverable, not shown.
    r, commits, _t = _renderer()
    r.hide_thinking = True  # issue #50: hidden is no longer the default
    r.on_agent_event(_msg_update(ThinkingEndEvent(content="secret chain of thought")))
    out = _committed_text(commits)
    assert "Thinking" in out and "/expand 1" in out
    assert "secret chain of thought" not in out  # collapsed, not inlined
    assert r.get_expanded(1) == "secret chain of thought"  # recoverable


def test_thinking_visible_when_toggled_off() -> None:
    # Ctrl+T flips hide_thinking → subsequent thinking renders in full.
    r, commits, _t = _renderer()
    r.hide_thinking = False
    r.on_agent_event(_msg_update(ThinkingEndEvent(content="open reasoning")))
    out = _committed_text(commits)
    assert "open reasoning" in out
    assert "/expand" not in out  # full render, no collapse placeholder


def test_thinking_renders_before_text_not_after() -> None:
    """ADR-0115 regression: the adapter emits thinking_end at end-of-stream
    (after the text already streamed), but reasoning must render ABOVE the
    answer, exactly once."""
    r, commits, _t = _renderer()
    r.hide_thinking = False  # full-visible render path (ADR-0115 ordering)
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta="reason ")))
    r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta="here")))
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="the answer")))
    r.on_agent_event(_msg_update(TextEndEvent(content="the answer")))
    # adapter's end-of-stream ordering: thinking_end fires AFTER text_end
    r.on_agent_event(_msg_update(ThinkingEndEvent(content="reason here")))
    text = _committed_text(commits)
    assert "reason here" in text and "the answer" in text
    assert text.index("reason here") < text.index("the answer")
    assert text.count("reason here") == 1  # late thinking_end does not re-print


def test_thinking_renders_before_tool_card() -> None:
    """Reasoning that preceded a tool call renders above its card, once."""
    r, commits, _t = _renderer()
    r.hide_thinking = False  # full-visible render path (ADR-0115 ordering)
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta="plan it")))
    r.on_agent_event(
        ToolExecutionStartEvent(tool_call_id="t1", tool_name="read", args={"path": "x.py"})
    )
    r.on_agent_event(_msg_update(ThinkingEndEvent(content="plan it")))
    text = _committed_text(commits)
    assert "plan it" in text and "read" in text
    assert text.index("plan it") < text.index("read")
    assert text.count("plan it") == 1


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


def test_tool_end_diff_is_colorized() -> None:
    r, commits, _t = _renderer()
    diff = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n"
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text=diff)]),
            tool_name="edit",
        )
    )
    styles = _row_styles(commits[0])
    assert "green" in styles and "red" in styles


def test_tool_end_edit_diff_from_details_is_colorized() -> None:
    # Pi parity (ADR-0138): edit returns a SUCCESS MESSAGE as content + the diff
    # in details (line-numbered +/- format, NOT @@). The card must surface and
    # colorize the diff read from details, not flatten to a dim success line.
    from types import SimpleNamespace

    r, commits, _t = _renderer()
    diff = " 1 ctx\n-2 old line\n+2 new line\n 3 ctx"
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(
                content=[
                    TextContent(text="Successfully replaced 1 block(s) in f.txt.")
                ],
                details=SimpleNamespace(diff=diff, first_changed_line=2),  # type: ignore[arg-type]
            ),
            tool_name="edit",
        )
    )
    out = _committed_text(commits)
    styles = _row_styles(commits[0])
    assert "Successfully replaced" in out and "new line" in out
    assert "green" in styles and "red" in styles


def test_tool_end_diff_shaped_bash_keeps_exit_footer() -> None:
    from types import SimpleNamespace

    r, commits, _t = _renderer()
    diff = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n"
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(
                content=[TextContent(text=diff)],
                details=SimpleNamespace(exit_code=1),  # type: ignore[arg-type]
            ),
            tool_name="bash",
        )
    )
    out = _committed_text(commits)
    assert "+new" in out and "exit 1" in out


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
    assert "red" in _row_styles(commits[-1])


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
    # Default card (a Group, not a Table) when the tool_name does not match.
    assert "other output" in _committed_text(commits)
    assert commits[-1].__class__.__name__ == "Group"


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
    assert commits[-1].__class__.__name__ == "Group"


# === §A — _truncate_lines (pure) =============================================


def test_truncate_lines_under_max_keeps_all() -> None:
    kept, hidden = _truncate_lines("a\nb\nc", max_lines=12)
    assert kept == ["a", "b", "c"]
    assert hidden == 0


def test_truncate_lines_over_max_reports_hidden() -> None:
    text = "\n".join(str(i) for i in range(20))
    kept, hidden = _truncate_lines(text, max_lines=12)
    assert len(kept) == 12
    assert hidden == 8


def test_truncate_lines_caps_long_line() -> None:
    kept, hidden = _truncate_lines("x" * 200, max_lines=12, max_line_width=78)
    assert len(kept[0]) == 78
    assert kept[0].endswith("…")
    assert hidden == 0


# === §B — _tool_header (pure) ================================================


def test_tool_header_read_shows_path() -> None:
    assert _tool_header("read", {"path": "src/a.py"}) == "src/a.py"


def test_tool_header_read_shows_line_range() -> None:
    assert _tool_header("read", {"path": "a.py", "offset": 10, "limit": 5}) == "a.py:10-15"


def test_tool_header_write_shows_path() -> None:
    assert _tool_header("write", {"path": "out.txt", "content": "x" * 500}) == "out.txt"


def test_tool_header_edit_shows_path() -> None:
    assert _tool_header("edit", {"path": "e.py", "edits": [1, 2, 3]}) == "e.py"


def test_tool_header_bash_shows_command() -> None:
    assert _tool_header("bash", {"command": "ls -la"}) == "ls -la"


def test_tool_header_generic_falls_back_to_compact_args() -> None:
    out = _tool_header("grep", {"pattern": "foo"})
    assert "pattern" in out and "foo" in out


def test_tool_header_read_nonnumeric_offset_degrades_to_path() -> None:
    # Args come from unvalidated model JSON — a non-numeric offset/limit must
    # degrade to the bare path, not raise inside the start-header render.
    assert _tool_header("read", {"path": "a.py", "offset": "start"}) == "a.py"
    assert _tool_header("read", {"path": "a.py", "limit": []}) == "a.py"


def test_truncate_lines_cjk_width_counts_cells() -> None:
    # Korean chars are 2 terminal cells; a 50-char Hangul line is ~100 cells and
    # must be truncated against the cell-based cap (a len()-based cap would miss it).
    kept, _hidden = _truncate_lines("가" * 50, max_lines=12)
    assert kept[0].endswith("…")
    assert cell_len(kept[0]) <= 76


# === §A — _render_tool_end card styling ======================================


def test_tool_end_short_result_no_more_lines_footer() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="line one\nline two")]),
            tool_name="read",
        )
    )
    out = _committed_text(commits)
    assert "line one" in out and "line two" in out
    assert "more lines" not in out


def test_tool_end_long_result_truncated_with_footer() -> None:
    r, commits, _t = _renderer()
    body = "\n".join(f"line {i}" for i in range(40))
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text=body)]),
            tool_name="read",
        )
    )
    out = _committed_text(commits)
    assert "line 0" in out and "line 11" in out
    assert "line 12" not in out
    # ADR-0121 — a truncated card carries a ``/expand N`` hint (first id = 1) so
    # the elided body can be recovered.
    assert "(+28 more lines · /expand 1)" in out
    assert r.get_expanded(1) == body


def test_tool_end_error_uses_higher_cap_to_preserve_traceback() -> None:
    # Errors get a 40-line cap (vs 12) so a traceback's diagnostic tail survives.
    r, commits, _t = _renderer()
    body = "\n".join(f"line {i}" for i in range(30))  # >12 but <40
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text=body)]),
            tool_name="bash",
            is_error=True,
        )
    )
    out = _committed_text(commits)
    assert "line 29" in out  # full 30 lines kept under the error cap
    assert "more lines" not in out


def test_tool_end_card_rows_are_dim_by_default() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="ok")]),
            tool_name="read",
        )
    )
    assert "dim" in _row_styles(commits[-1])


def test_tool_end_bash_nonzero_exit_surfaced() -> None:
    r, commits, _t = _renderer()
    details = BashToolDetails(exit_code=2, truncation=TruncationInfo())
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(
                content=[TextContent(text="boom")], details=details, is_error=True
            ),
            tool_name="bash",
            is_error=True,
        )
    )
    out = _committed_text(commits)
    assert "exit 2" in out


def test_tool_end_bash_zero_exit_not_surfaced() -> None:
    r, commits, _t = _renderer()
    details = BashToolDetails(exit_code=0, truncation=TruncationInfo())
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="done")], details=details),
            tool_name="bash",
        )
    )
    assert "exit" not in _committed_text(commits)


def test_tool_end_matching_descriptor_skips_truncation() -> None:
    r, commits, _t = _renderer()
    env = _env("grep", view="text")
    _wire(r, env)
    body = "\n".join(f"row {i}" for i in range(40))
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text=body)]),
            tool_name="grep",
        )
    )
    # Descriptor precedence: a Panel custom view, NOT a truncated Group card.
    assert commits[-1].__class__.__name__ == "Panel"
    assert "more lines" not in _committed_text(commits)


# === Sprint 6h₁₄a (ADR-0121) — /expand store ==============================


def test_expand_store_is_bounded_and_evicts_oldest() -> None:
    r, _c, _t = _renderer()
    r._expand_max = 3
    ids = [r._store_expandable(f"body {i}") for i in range(5)]
    # Only the last 3 survive; the first two ids were evicted (oldest-first).
    assert r.get_expanded(ids[0]) is None
    assert r.get_expanded(ids[1]) is None
    assert r.get_expanded(ids[2]) == "body 2"
    assert r.get_expanded(ids[4]) == "body 4"


def test_reset_expand_store_drops_ids_and_seq() -> None:
    # W-review 6h₁₅ MEDIUM: a session swap must reset the store so post-swap
    # /expand N can't surface the prior session's body.
    r, _c, _t = _renderer()
    n1 = r._store_expandable("session A body")
    assert r.get_expanded(n1) == "session A body"
    r.reset_expand_store()
    assert r.get_expanded(n1) is None
    # ids restart from 1 (no stale-id confusion with the new session).
    assert r._store_expandable("session B body") == 1


def test_non_truncated_card_gets_no_expand_id() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text="short output")]),
            tool_name="read",
        )
    )
    # A short (non-truncated) card stores nothing and shows no /expand hint.
    assert "/expand" not in _committed_text(commits)
    assert r.get_expanded(1) is None


def test_truncated_diff_carries_expand_hint() -> None:
    r, commits, _t = _renderer()
    diff = "@@ -1,2 +1,2 @@\n" + "\n".join(f"+added line {i}" for i in range(60))
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text=diff)]),
            tool_name="bash",
        )
    )
    out = _committed_text(commits)
    assert "/expand 1" in out
    assert r.get_expanded(1) == diff.rstrip()


# === Sprint 6h₁₄b (ADR-0122) — transcript replay (/resume) =================


def test_replay_renders_user_assistant_tool_transcript() -> None:
    from aelix_ai.messages import (
        AssistantMessage,
        TextContent,
        ThinkingContent,
        ToolCallContent,
        ToolResultMessage,
        UserMessage,
    )

    r, commits, _t = _renderer()
    long_body = "\n".join(f"out {i}" for i in range(30))
    messages = [
        UserMessage(content=[TextContent(text="read the file")]),
        AssistantMessage(
            content=[
                ThinkingContent(thinking="I should read it"),
                ToolCallContent(tool_call_id="t1", tool_name="read", input={"path": "/x.txt"}),
            ]
        ),
        ToolResultMessage(
            tool_call_id="t1", tool_name="read", content=[TextContent(text=long_body)]
        ),
        AssistantMessage(content=[TextContent(text="It has 30 lines.")]),
    ]
    r.replay(messages)
    out = _committed_text(commits)
    assert "» read the file" in out  # user echo
    assert "I should read it" in out  # thinking
    assert "● read(/x.txt)" in out  # tool-call header (Sprint 6h₃₂: ● marker)
    assert "out 0" in out and "out 11" in out  # truncated card body
    assert "out 12" not in out  # truncated at 12
    assert "/expand 1" in out  # truncated → expand hint
    assert "It has 30 lines." in out  # assistant answer
    assert r.get_expanded(1) == long_body  # full body recoverable via /expand


def test_replay_renders_terminal_error_line() -> None:
    from aelix_ai.messages import AssistantMessage, TextContent

    r, commits, _t = _renderer()
    r.replay(
        [
            AssistantMessage(
                content=[TextContent(text="partial")],
                stop_reason="error",
                error_message="provider exploded",
            )
        ]
    )
    out = _committed_text(commits)
    assert "partial" in out
    assert "✖ provider exploded" in out


def test_replay_empty_is_noop() -> None:
    r, commits, _t = _renderer()
    r.replay([])
    assert commits == []


# === Sprint 6h₂₅ (ADR-0153, WP-6) — shared user-echo helper ================


def _group_rows(group: Any) -> list[Any]:
    return list(getattr(group, "renderables", []))


def test_render_user_message_blank_line_chevron_and_cyan() -> None:
    from aelix_coding_agent.tui.render import render_user_message

    group = render_user_message("hello there")
    rows = _group_rows(group)
    # Sprint 6h₃₂ — blank lines ABOVE and BELOW fence the echo off (the single
    # leading blank of ADR-0153 was too subtle).
    assert len(rows) == 3
    assert rows[0].plain == ""  # leading blank
    assert rows[2].plain == ""  # trailing blank
    # The middle row keeps the ``» `` chevron and is styled bold cyan (stands out).
    assert rows[1].plain == "» hello there"
    assert "bold cyan" in str(rows[1].style)


def test_render_user_message_steer_and_follow_up_labels_same_visual() -> None:
    from aelix_coding_agent.tui.render import render_user_message

    steer = _group_rows(render_user_message("go left", kind="steer"))
    follow = _group_rows(render_user_message("then commit", kind="follow_up"))
    # Distinct labels...
    assert steer[1].plain == "Steering: go left"
    assert follow[1].plain == "Follow-up: then commit"
    # ...but the SAME visual language: leading blank line + bold cyan.
    for rows in (steer, follow):
        assert rows[0].plain == ""
        assert "bold cyan" in str(rows[1].style)


def test_render_user_message_unknown_kind_degrades_to_prompt() -> None:
    from aelix_coding_agent.tui.render import render_user_message

    rows = _group_rows(render_user_message("oops", kind="mystery"))
    assert rows[1].plain == "» oops"


# === Sprint 6h₃₂ — shared tool-call header helper ==========================


def test_render_tool_call_line_marker_bold_name_and_args() -> None:
    from aelix_coding_agent.tui.render import render_tool_call_line

    line = render_tool_call_line("bash", "git log --oneline")
    # The ● marker replaces the old ⚙ gear; the plain text carries name + args.
    assert line.plain == "● bash(git log --oneline)"
    # The marker and the tool NAME are bold (so the name reads first); the args
    # keep the plain cyan card weight (NOT bold).
    assert any(str(s.style) == "bold cyan" for s in line.spans)  # marker/name
    assert any(str(s.style) == "cyan" for s in line.spans)  # args, not bold


def test_render_tool_call_line_no_summary_omits_parens() -> None:
    from aelix_coding_agent.tui.render import render_tool_call_line

    line = render_tool_call_line("noop", "")
    assert line.plain == "● noop"
