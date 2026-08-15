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
    TurnEndEvent,
    TurnStartEvent,
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


def test_tool_end_normal_cap_defaults_to_twelve() -> None:
    # Issue #66 — a fresh renderer caps normal output at 12 lines (default).
    r, commits, _t = _renderer()
    assert r.tool_card_max_lines == 12
    body = "\n".join(f"line {i}" for i in range(20))
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text=body)]),
            tool_name="read",
        )
    )
    out = _committed_text(commits)
    assert "line 11" in out and "line 12" not in out
    assert "(+8 more lines" in out


def test_tool_end_normal_cap_honours_configured_value() -> None:
    # Issue #66 — the configurable cap governs ONLY the normal-output card path.
    r, commits, _t = _renderer()
    r.tool_card_max_lines = 5
    body = "\n".join(f"line {i}" for i in range(20))
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text=body)]),
            tool_name="read",
        )
    )
    out = _committed_text(commits)
    assert "line 4" in out and "line 5" not in out
    assert "(+15 more lines" in out


def test_tool_end_error_cap_unaffected_by_configured_normal_cap() -> None:
    # Issue #66 owner decision — the separate 40-line error/diff cap stays 40
    # regardless of the configured NORMAL cap.
    r, commits, _t = _renderer()
    r.tool_card_max_lines = 5
    body = "\n".join(f"line {i}" for i in range(30))  # >5 but <40
    r.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="t1",
            result=ToolResult(content=[TextContent(text=body)]),
            tool_name="bash",
            is_error=True,
        )
    )
    out = _committed_text(commits)
    assert "line 29" in out  # full 30 lines kept under the untouched error cap
    assert "more lines" not in out


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


# === Aelix-original — compaction-summary DISPLAY gate ======================


def test_replay_hides_compaction_summary_when_gated() -> None:
    # When the gate is on, a persisted compaction-summary user message collapses
    # to a one-line marker; the summary body is NOT rendered (it stays in context).
    from aelix_agent_core.session.context import COMPACTION_SUMMARY_PREFIX
    from aelix_ai.messages import TextContent, UserMessage

    r, commits, _t = _renderer()
    r.hide_compaction_summary = True
    r.replay(
        [
            UserMessage(
                content=[TextContent(text=COMPACTION_SUMMARY_PREFIX + "SECRET_SUMMARY_BODY")]
            )
        ]
    )
    out = _committed_text(commits)
    assert "summary hidden" in out  # collapsed marker
    assert "SECRET_SUMMARY_BODY" not in out  # body suppressed from the transcript


def test_replay_shows_compaction_summary_by_default() -> None:
    # Default (gate off) — the summary renders normally (prior behavior).
    from aelix_agent_core.session.context import COMPACTION_SUMMARY_PREFIX
    from aelix_ai.messages import TextContent, UserMessage

    r, commits, _t = _renderer()
    assert r.hide_compaction_summary is False  # default visible
    r.replay(
        [
            UserMessage(
                content=[TextContent(text=COMPACTION_SUMMARY_PREFIX + "SECRET_SUMMARY_BODY")]
            )
        ]
    )
    out = _committed_text(commits)
    assert "SECRET_SUMMARY_BODY" in out  # body rendered
    assert "summary hidden" not in out


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


# === Issue #62 (ADR-0183) — custom-message replay ==========================


def _display_custom(*, display: bool = True, content: object = "deploy green") -> Any:
    from aelix_agent_core.session.context import create_display_custom_message

    return create_display_custom_message(
        "status", content, display, None, "2026-07-04T00:00:00Z"
    )


def test_replay_custom_default_renders_label_and_content() -> None:
    r, commits, _t = _renderer()
    r.replay([_display_custom()])
    out = _committed_text(commits)
    assert "[status]" in out  # bold custom_type label (default rendering)
    assert "deploy green" in out
    assert "»" not in out  # NOT a user echo (the pre-#62 flattened behavior)


def test_replay_custom_display_false_renders_nothing() -> None:
    # pi interactive-mode.ts:3029-3037 — the display gate fires BEFORE any
    # renderer lookup; the message stays in the LLM context but never renders.
    r, commits, _t = _renderer()
    r.replay([_display_custom(display=False)])
    assert commits == []


def test_replay_custom_hook_renderable_wins() -> None:
    from rich.text import Text as _Text

    r, commits, _t = _renderer()
    r.render_custom_message = lambda msg: _Text("HOOKED:" + msg.custom_type)
    r.replay([_display_custom()])
    out = _committed_text(commits)
    assert "HOOKED:status" in out
    assert "[status]" not in out  # default rendering suppressed


def test_replay_custom_hook_none_falls_back_to_default() -> None:
    r, commits, _t = _renderer()
    r.render_custom_message = lambda msg: None
    r.replay([_display_custom()])
    assert "[status]" in _committed_text(commits)


def test_replay_custom_hook_raise_falls_back_silently() -> None:
    # pi custom-message.ts:68-70 — renderer exceptions are swallowed and the
    # default rendering is used; replay must never break on a bad extension.
    def _boom(msg: Any) -> Any:
        raise RuntimeError("bad renderer")

    r, commits, _t = _renderer()
    r.render_custom_message = _boom
    r.replay([_display_custom()])
    assert "[status]" in _committed_text(commits)


def test_replay_custom_list_content_flattened() -> None:
    from aelix_ai.messages import TextContent as _TC

    r, commits, _t = _renderer()
    r.replay([_display_custom(content=[_TC(text="part one")])])
    assert "part one" in _committed_text(commits)


def test_replay_custom_hook_returns_non_component_falls_back() -> None:
    # A renderer whose return has no .render() would AttributeError in the
    # shell closure; the render-layer hook contract is object|None, so a bad
    # shape must degrade to default rendering, never crash replay.
    r, commits, _t = _renderer()

    def _bad(msg: Any) -> Any:
        return object()  # not a Rich renderable; committing it must be safe

    # The shell closure is what converts a Component→renderable; at the render
    # layer the hook returns a renderable-or-None. Simulate the closure raising
    # on a bad shape by having the hook itself raise (closure catches → None).
    def _raises(msg: Any) -> Any:
        raise AttributeError("'object' has no attribute 'render'")

    r.render_custom_message = _raises
    r.replay([_display_custom()])
    assert "[status]" in _committed_text(commits)  # default fallback rendered


def test_component_to_text_snapshots_multiline_ansi() -> None:
    # Issue #62 review (MEDIUM): the Component→render(width)→Text.from_ansi
    # conversion used by the shell closure was never asserted. Lock it: a
    # two-line component yields both lines joined in the Rich Text.
    from aelix_coding_agent.tui.render import component_to_text

    class _Comp:
        def render(self, width: int) -> list[str]:
            assert width == 80
            return ["line-a", "line-b"]

    text = component_to_text(_Comp(), 80)
    assert text.plain == "line-a\nline-b"


def test_replay_custom_wire_dict_content_renders() -> None:
    # Issue #62 review (LOW, live-reproduced): custom content off the JSONL
    # wire is raw dicts (entry_from_json stores it verbatim), so the default
    # render must read dict blocks — not only TextContent objects.
    from aelix_agent_core.session.context import create_display_custom_message

    msg = create_display_custom_message(
        "status", [{"type": "text", "text": "wire-dict-line"}], True, None,
        "2026-07-04T00:00:00Z",
    )
    r, commits, _t = _renderer()
    r.replay([msg])
    assert "wire-dict-line" in _committed_text(commits)


# === Issue #133 item 2 — an interrupted turn must leave a trace =============
#
# The harness aborts by CANCELLING the turn task; its close-out
# (harness/core.py:4370-4383) deliberately emits only turn_end + agent_end, with
# no message_start/message_end pair, so ``_render_message_error`` (message_end
# only) never ran and the interrupt reached scrollback as nothing at all.
# pi prints "Operation aborted" from its message_end handler
# (interactive-mode.ts:2752-2757); aelix prints the same words from turn_end
# because that is the only event its abort path produces.


def _abort_turn_end() -> TurnEndEvent:
    return TurnEndEvent(
        message=AssistantMessage(content=[], stop_reason="aborted"), tool_results=[]
    )


def _notice_lines(commits: list[Any]) -> list[str]:
    return [_plain(c).strip() for c in commits if "Operation aborted" in _plain(c)]


def _outcome_lines(commits: list[Any]) -> list[str]:
    return [_plain(c).strip() for c in commits if "✖" in _plain(c)]


def test_turn_end_aborted_commits_notice_after_partial_text() -> None:
    # ORDER and COUNT both matter: the notice has to terminate the partial answer
    # above it, so asserting only "some commit contains the string" would pass for
    # an implementation that prints it first, or twice.
    r, commits, _tails = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    for delta in ("Hello ", "world"):
        r.on_agent_event(_msg_update(TextDeltaEvent(delta=delta)))
    r.on_agent_event(_abort_turn_end())

    assert len(commits) == 2, [_plain(c) for c in commits]
    assert "Hello world" in _plain(commits[0])
    assert _plain(commits[1]).strip() == "✖ Operation aborted"
    # Yellow, NOT the bold red of a genuine failure — the point of the line is
    # that the user can tell "I stopped it" from "it crashed".
    assert str(commits[1].style) == "yellow"


def test_turn_end_aborted_during_tool_execution_still_notifies() -> None:
    # Aborting while a tool runs produces the same lone turn_end(aborted) with no
    # streamed text. Measured shape: the tool card commits, then the notice — so
    # the notice is the LAST commit and appears exactly once.
    r, commits, _tails = _renderer()
    r.on_agent_event(
        ToolExecutionStartEvent(
            tool_call_id="t1", tool_name="bash", args={"command": "sleep 100"}
        )
    )
    r.on_agent_event(_abort_turn_end())

    assert _notice_lines(commits) == ["✖ Operation aborted"]
    assert _plain(commits[-1]).strip() == "✖ Operation aborted"


def test_turn_end_normal_completion_commits_no_notice() -> None:
    r, commits, _tails = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_msg_update(TextDeltaEvent(delta="done")))
    r.on_agent_event(
        TurnEndEvent(
            message=AssistantMessage(content=[], stop_reason="end_turn"),
            tool_results=[],
        )
    )

    assert _notice_lines(commits) == []
    assert _outcome_lines(commits) == []


def test_message_end_and_turn_end_end_turn_commit_no_outcome_line() -> None:
    r, commits, _tails = _renderer()
    end = AssistantMessage(content=[], stop_reason="end_turn")
    r.on_agent_event(MessageEndEvent(message=end))
    r.on_agent_event(TurnEndEvent(message=end, tool_results=[]))

    assert _outcome_lines(commits) == []


def test_provider_error_commits_one_line_and_no_abort_notice() -> None:
    # A provider failure already prints via _render_message_error. turn_end then
    # arrives carrying the SAME message with stop_reason "error" — which must not
    # add a second line, and must never say "aborted".
    r, commits, _tails = _renderer()
    failed = AssistantMessage(
        content=[], stop_reason="error", error_message="rate limit"
    )
    r.on_agent_event(MessageEndEvent(message=failed))
    r.on_agent_event(TurnEndEvent(message=failed, tool_results=[]))

    assert _outcome_lines(commits) == ["✖ rate limit"]
    assert _notice_lines(commits) == []


def test_stream_level_abort_does_not_double_print() -> None:
    # Latent-regression guard. ``AssistantErrorEvent`` is typed
    # ``reason: Literal["aborted", "error"]``, so an adapter that starts honouring
    # a signal WOULD emit message_end(aborted) followed by turn_end carrying the
    # SAME object (measured against the real loop). Both _render_message_error and
    # _render_turn_abort match "aborted", so without the identity guard that prints
    # two outcome lines for one abort.
    r, commits, _tails = _renderer()
    aborted = AssistantMessage(content=[], stop_reason="aborted")
    r.on_agent_event(MessageEndEvent(message=aborted))
    r.on_agent_event(TurnEndEvent(message=aborted, tool_results=[]))

    assert _outcome_lines(commits) == ["✖ request aborted"]


def test_turn_end_without_a_message_attribute_is_tolerated() -> None:
    # The renderer is a plain broadcast subscriber and is fed bare
    # ``SimpleNamespace(type="turn_end")`` stubs elsewhere in the TUI tests
    # (test_run_tui_smoke.py's refresh-generation tests). The turn_end branch
    # never read ``event.message`` before this change, so reading it
    # unconditionally raised AttributeError inside the subscriber.
    from types import SimpleNamespace

    r, commits, _tails = _renderer()
    r.on_agent_event(SimpleNamespace(type="turn_end"))  # must not raise

    assert _outcome_lines(commits) == []


def test_abort_notice_still_fires_on_the_turn_after_a_reported_error() -> None:
    # The dedupe guard must not go stale: a turn that errors, then a LATER turn
    # that is aborted, must still print the notice.
    r, commits, _tails = _renderer()
    failed = AssistantMessage(content=[], stop_reason="error", error_message="boom")
    r.on_agent_event(MessageEndEvent(message=failed))
    r.on_agent_event(TurnEndEvent(message=failed, tool_results=[]))
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_abort_turn_end())

    assert _notice_lines(commits) == ["✖ Operation aborted"]


async def test_message_end_replacement_does_not_double_print() -> None:
    """A message_end-replacing extension must not defeat the dedupe guard.

    Regression test for the review's LOW on the ORIGINAL object-identity guard.
    loop.py:396-405 identity-swaps the assistant message when a message_end
    handler returns a replacement, so ``_render_message_error`` sees the ORIGINAL
    object while the ``turn_end`` that follows carries the REPLACEMENT. An
    ``is`` comparison misses and BOTH outcome lines print for one abort.

    This drives the REAL ``agent_loop`` (not a hand-rolled event list) so the
    replacement swap is performed by production code.
    """
    from collections.abc import AsyncIterator
    from dataclasses import replace as dc_replace

    from aelix_agent_core import (
        AgentContext,
        AgentLoopConfig,
        agent_loop,
        default_convert_to_llm,
    )
    from aelix_ai.messages import UserMessage
    from aelix_ai.streaming import (
        AssistantMessageEvent,
        Context,
        Model,
        SimpleStreamOptions,
    )

    r, commits, _tails = _renderer()

    async def stream_fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantErrorEvent(
            error=AssistantMessage(content=[], stop_reason="aborted"),
            reason="aborted",
        )

    seen: list[tuple[str, int]] = []

    async def emit(event: Any) -> Any:
        r.on_agent_event(event)
        message = getattr(event, "message", None)
        if event.type in ("message_end", "turn_end"):
            seen.append((event.type, id(message)))
        # The extension: return a REPLACEMENT at message_end. loop.py then swaps
        # it in, so turn_end carries a different object than message_end did.
        if event.type == "message_end" and isinstance(message, AssistantMessage):
            return dc_replace(message)
        return None

    await agent_loop(
        [UserMessage(content=[TextContent(text="go")])],
        AgentContext(),
        AgentLoopConfig(
            model=Model(id="mock", provider="mock"),
            convert_to_llm=default_convert_to_llm,
        ),
        emit=emit,
        stream_fn=stream_fn,
    )

    # Precondition: the replacement really did happen, i.e. message_end and
    # turn_end carried DIFFERENT objects. Without this the test could pass
    # trivially against a guard that never faced the swap at all.
    assistant_ends = [i for t, i in seen if t == "message_end"]
    turn_ends = [i for t, i in seen if t == "turn_end"]
    assert turn_ends and assistant_ends[-1] != turn_ends[-1], seen

    # Exactly ONE outcome line for one abort.
    assert _outcome_lines(commits) == ["✖ request aborted"]


def test_abort_notice_survives_an_aborted_retry_attempt() -> None:
    """A bool cleared ONLY at message_start would go stale on the retry path.

    Measured against the real harness (provider error -> auto-retry -> abort the
    in-flight retry request): the emitted sequence is

        message_end("error") -> turn_end("error") -> agent_end ->
        auto_retry_start -> agent_start -> turn_start -> turn_end("aborted")

    with NO message_start after the failed attempt, so a flag left set by that
    attempt would SUPPRESS the notice.

    Scope, honestly stated: that sequence was reached by calling the programmatic
    ``AgentHarness.abort()`` while a retry attempt was in flight. From the TUI
    keyboard it is currently NOT reachable — interrupting an in-flight auto-retry
    does not abort at all today (measured live on this build and on the parent
    commit alike; a separate pre-existing gap). This test therefore pins the
    renderer contract, not a currently user-reachable keyboard bug.
    """
    r, commits, _tails = _renderer()
    failed = AssistantMessage(content=[], stop_reason="error", error_message="rate limit")
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(MessageEndEvent(message=failed))
    r.on_agent_event(TurnEndEvent(message=failed, tool_results=[]))
    r.on_agent_event(AgentEndEvent(messages=[]))
    # The retry attempt: turn_start, then the abort close-out. No message_start.
    r.on_agent_event(TurnStartEvent())
    r.on_agent_event(_abort_turn_end())

    assert _outcome_lines(commits) == ["✖ rate limit", "✖ Operation aborted"]


# === issue #166: the card/diff cap and the cell-vs-codepoint unit ============


def test_card_line_width_only_ever_widens_from_the_historical_cap() -> None:
    """Cards may exceed the terminal; the approval dialog may not.

    A card row is committed as a bare Rich ``Text`` to the ADAPTIVE scrollback
    console, which WRAPS it — overflowing costs a second screen row and loses
    nothing. The approval dialog is a prompt-toolkit float with
    ``wrap_lines=False``, where overflowing CLIPS. Deriving this cap from the
    terminal in both directions therefore made narrow terminals strictly worse:
    measured on a 60-column terminal, a 63-cell tool-output line survived at the
    old fixed 76 and was cut to ``...T…`` at ``60 - 4 = 56`` — output DELETED
    that main displayed.
    """

    from aelix_coding_agent.tui.render import EventRenderer

    def cap(width: int) -> int:
        r = EventRenderer(commit=lambda _c: None, set_tail=lambda _t: None, width=width)
        return r._card_line_width()  # noqa: SLF001 — the unit under test

    # Narrow terminals keep main's behaviour exactly.
    assert cap(40) == 76
    assert cap(60) == 76
    assert cap(80) == 76
    # Wide ones gain room, minus the "│ " gutter plus slack.
    assert cap(120) == 116
    assert cap(200) == 196


def test_a_narrow_terminal_does_not_delete_tool_output() -> None:
    """The regression above, asserted on the rendered card body."""

    from aelix_coding_agent.tui.render import EventRenderer, _truncate_lines

    line = "y" * 54 + "TAIL_KEEP"  # 63 cells: fits 76, exceeds 56
    r = EventRenderer(commit=lambda _c: None, set_tail=lambda _t: None, width=60)
    kept, _hidden = _truncate_lines(line, max_lines=5, max_line_width=r._card_line_width())  # noqa: SLF001
    assert "TAIL_KEEP" in kept[0]


def test_card_cap_follows_a_callable_width() -> None:
    """A live reader must reach the card cap too, not only the streamed text."""

    from aelix_coding_agent.tui.render import EventRenderer

    live = {"cols": 200}
    r = EventRenderer(
        commit=lambda _c: None, set_tail=lambda _t: None, width=lambda: live["cols"]
    )
    assert r._card_line_width() == 196  # noqa: SLF001
    live["cols"] = 60
    assert r._card_line_width() == 76  # noqa: SLF001


def test_tool_header_cap_is_measured_in_cells_not_codepoints() -> None:
    """A Hangul header must not eat three scrollback rows where ASCII takes one.

    Before issue #166 these caps used ``len()`` while ``_truncate_lines`` next
    door used ``cell_len``. 50 Hangul codepoints pass an 80-CODEPOINT check at
    100 cells; the adaptive console soft-wrapped the overflow, which is why the
    mismatch survived unnoticed.
    """

    from aelix_coding_agent.tui.render import _HEADER_MAX_CELLS, _cap_cells

    hangul = "가" * 50  # 50 codepoints, 100 cells
    assert len(hangul) < _HEADER_MAX_CELLS  # the OLD check would have passed it
    assert cell_len(hangul) > _HEADER_MAX_CELLS  # ...while it is really 100 cells

    capped = _cap_cells(hangul, _HEADER_MAX_CELLS)
    assert cell_len(capped) <= _HEADER_MAX_CELLS
    assert capped.endswith("…")


def test_cap_cells_leaves_short_ascii_untouched() -> None:
    """The unit change must not alter behaviour for the common case."""

    from aelix_coding_agent.tui.render import _HEADER_MAX_CELLS, _cap_cells

    short = "read(path='/tmp/x.py')"
    assert _cap_cells(short, _HEADER_MAX_CELLS) == short
    exact = "z" * _HEADER_MAX_CELLS
    assert _cap_cells(exact, _HEADER_MAX_CELLS) == exact
    over = "z" * (_HEADER_MAX_CELLS + 1)
    assert _cap_cells(over, _HEADER_MAX_CELLS).endswith("…")
    assert cell_len(_cap_cells(over, _HEADER_MAX_CELLS)) <= _HEADER_MAX_CELLS


# === issue #164: replay renders what the live path renders ==================


_MD_BODY = (
    "**Set up authentication** first.\n\n"
    "Run `aelix /login` and pick a provider.\n\n"
    "- item one\n- item two\n"
)


def _replay_one_text(width: Any, body: str = _MD_BODY) -> list[Any]:
    from aelix_ai.messages import AssistantMessage, TextContent

    commits: list[Any] = []
    r = EventRenderer(commit=commits.append, set_tail=lambda _t: None, width=width)
    r.replay([AssistantMessage(content=[TextContent(text=body)])])
    return commits


def test_replay_renders_markdown_not_source() -> None:
    """FAILS TODAY: replay committed ``Text(body)`` — the raw markdown SOURCE.

    Measured before the fix: literal ``**`` and literal backticks on screen, no
    bullets, no styling. This is what a user saw after every ``/resume``.
    """

    plain = _replay_one_text(100)[0].plain
    assert "**" not in plain
    assert "`" not in plain
    assert "•" in plain  # the list actually rendered


def test_replay_uses_the_callable_render_width() -> None:
    """FAILS TODAY: max rendered line was 39 cells regardless of the terminal.

    Production passes a CALLABLE (issue #166); only tests pass a bare int. A
    hardcoded 80 here would pass every existing test while silently undoing that
    work, so the width is asserted through the callable form.
    """

    plain = _replay_one_text(lambda: 100)[0].plain
    # Rich pads every markdown line to the full width, so this is exact rather
    # than a lower bound. Do not add an ``endswith`` assertion to these lines.
    assert max(len(line) for line in plain.split("\n")) == 100


def test_replay_text_matches_the_live_stream() -> None:
    """The two paths must agree, asserted on a SINGLE-delta body.

    Not a multi-chunk stream: ``_render_lines`` re-renders the whole accumulated
    text each tick and the stream freezes already-committed lines, so a body
    whose late content reflows earlier lines (a long table) legitimately differs.
    That is a property of the streaming window, not a replay defect.
    """

    from aelix_agent_core.types import MessageStartEvent
    from aelix_ai.messages import AssistantMessage
    from aelix_ai.streaming import TextDeltaEvent, TextEndEvent

    live: list[Any] = []
    r = EventRenderer(commit=live.append, set_tail=lambda _t: None, width=100)
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_msg_update(TextDeltaEvent(delta=_MD_BODY)))
    r.on_agent_event(_msg_update(TextEndEvent(content=_MD_BODY)))

    replayed = _replay_one_text(100)
    assert "".join(c.plain for c in live) == "".join(c.plain for c in replayed)


def test_replay_skips_a_body_that_renders_to_no_markdown_lines() -> None:
    """FAILS TODAY (1 stray commit), and would fail again on a naive fix.

    ``<!-- hidden -->`` is non-blank under ``.strip()`` but renders to zero
    lines. Guarding on the body instead of on the rendered output emits a blank
    line the live path never emits.
    """

    assert _replay_one_text(100, "<!-- hidden -->") == []


def test_replay_honours_hide_thinking_like_the_live_path() -> None:
    """FAILS TODAY: replay dumped the full reasoning regardless of the setting.

    ``hide_thinking`` is seeded from the persisted setting at startup, so a user
    with "Show thinking" OFF got every past turn's chain-of-thought back on
    every resume — and, after #165, on every ``--resume`` launch too.
    """

    from aelix_ai.messages import AssistantMessage, ThinkingContent

    commits: list[Any] = []
    r = EventRenderer(commit=commits.append, set_tail=lambda _t: None, width=80)
    r.hide_thinking = True
    r.replay([AssistantMessage(content=[ThinkingContent(thinking="secret chain")])])

    assert commits[0].plain == "💭 Thinking… (/expand 1)"
    assert r.get_expanded(1) == "secret chain"  # still recoverable, as when live


def test_replay_keeps_both_thinking_blocks_where_live_shows_one() -> None:
    """A deliberate NON-match, pinned so it is not "fixed" into a content loss.

    The live path renders only the FIRST thinking block of a message — a
    ``_thinking_flushed`` latch cleared by ``message_start``, which replay never
    emits. Here the LIVE side is the one dropping content, so replay is left
    showing both rather than matched down to it.
    """

    from aelix_ai.messages import AssistantMessage, ThinkingContent

    commits: list[Any] = []
    r = EventRenderer(commit=commits.append, set_tail=lambda _t: None, width=80)
    r.replay(
        [
            AssistantMessage(
                content=[ThinkingContent(thinking="A"), ThinkingContent(thinking="B")]
            )
        ]
    )
    assert [c.plain for c in commits] == ["A", "B"]
