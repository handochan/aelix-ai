"""#390: descriptor markup stays visible and never creates terminal hyperlinks.

Exercise terminal bytes through live events, explicitly named replay and the
shared builder. A missing tool name deliberately retains the default card.
"""

from __future__ import annotations

import io
import json
import re
from typing import Any

import pytest
from aelix_agent_core.contracts import DescriptorEnvelope, ToolRendererDescPayload
from aelix_agent_core.session.entries import MessageEntry, entry_from_json, entry_to_json
from aelix_agent_core.types import ToolExecutionEndEvent
from aelix_ai.messages import TextContent, ToolResultMessage
from aelix_ai.tools import ToolResult
from aelix_coding_agent.tui import render
from aelix_coding_agent.tui.descriptors import DescriptorRegistry, DescriptorRenderer
from aelix_coding_agent.tui.render import EventRenderer
from rich.console import Console

_SGR = re.compile(r"\x1b\[[0-9;]*m")
_WIDE = "BODY " + "x" * 180
_POSITIONS = [
    "table-title",
    "form-title",
    "grid-title",
    "text-title",
    "empty-grid-title",
    "table-header",
    "table-fallback-header",
    "table-dict-cell",
    "table-scalar-cell",
    "form-label",
    "form-value",
    "grid-item",
    "text-body",
]
_TARGETS = [
    "javascript:alert(1)",
    "data:text/html,PWN",
    "custom:target",
    "http://example.test/path",
    "https://example.test/path",
    "file:///tmp/fixture",
    "mailto:user@example.test",
    "https://example.test/a\u200eb",
    "https://example.test/a\u200fb",
    "https://example.test/a\u061cb",
]
_MARKUP = [
    "[bold]LABEL[/bold]",
    "[/]LABEL",
    "[bold]LABEL",
    "[unknown]LABEL[/unknown]",
    "[link=https://example.test][bold]LABEL[/bold][/link]",
    "한글 👩‍💻 [red]LABEL[/red]",
]


def _bytes(renderable: Any) -> str:
    buf = io.StringIO()
    Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        no_color=False,
        width=240,
        height=40,
        legacy_windows=False,
    ).print(renderable)
    return buf.getvalue()


def _assert_literal(out: str, expected: str) -> None:
    assert "\x1b]8;" not in out, repr(out)
    assert expected in _SGR.sub("", out), repr(out)


def _fixture(position: str, text: str) -> tuple[DescriptorEnvelope, str, list[Any]]:
    view = position.split("-", 1)[0]
    payload: dict[str, Any] = {"title": "TITLE"}
    rows: list[Any] = [{"k": _WIDE}]
    if position.endswith("title"):
        view = "grid" if position == "empty-grid-title" else view
        payload["title"] = text
        rows = [] if position == "empty-grid-title" else [_WIDE]
        if view in ("table", "form"):
            rows = [{"k": _WIDE}]
    if view == "table":
        payload["columns"] = [{"key": "k", "header": _WIDE}]
    if position == "table-header":
        payload["columns"] = [{"key": "k", "header": text}]
    elif position == "table-fallback-header":
        payload["columns"] = [{"key": text}]
        rows = [{text: _WIDE}]
    elif position == "table-dict-cell":
        rows = [{"k": text}]
    elif position == "table-scalar-cell":
        rows = [text]
    elif position == "form-label":
        rows = [{text: _WIDE}]
    elif position == "form-value":
        rows = [{_WIDE: text}]
    elif position in ("grid-item", "text-body"):
        rows = [text]
    envelope = DescriptorEnvelope(
        kind="tool-renderer-desc",
        namespace="fixture",
        id="literal",
        payload=ToolRendererDescPayload(tool_name="probe", view=view, **payload),
    )
    body = str(rows[0]) if view == "text" else json.dumps(rows, ensure_ascii=False)
    return envelope, body, rows


def _outputs(envelope: DescriptorEnvelope, body: str) -> list[str]:
    outputs: list[str] = []
    for mode in ("live", "named-replay"):
        commits: list[Any] = []
        renderer = EventRenderer(commit=commits.append, set_tail=lambda _: None, width=240)
        renderer.get_tool_renderer_desc = lambda name: envelope if name == "probe" else None
        renderer.descriptor_renderer = DescriptorRenderer  # type: ignore[assignment]
        if mode == "live":
            renderer.on_agent_event(
                ToolExecutionEndEvent(
                    tool_call_id="call",
                    tool_name="probe",
                    result=ToolResult(content=[TextContent(text=body)]),
                    is_error=False,
                )
            )
        else:
            renderer.replay(
                [
                    ToolResultMessage(
                        tool_call_id="call", tool_name="probe", content=[TextContent(text=body)]
                    )
                ]
            )
        assert len(commits) == 1, (mode, commits)
        outputs.append(_bytes(commits[0]))
    return outputs


@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("target", _TARGETS)
def test_live_and_named_replay_links_are_visible_literals(position: str, target: str) -> None:
    text = f"[link={target}]LABEL[/link]"
    envelope, body, _ = _fixture(position, text)
    for out in _outputs(envelope, body):
        _assert_literal(out, text)
        if position == "table-fallback-header":
            assert "BODY" in out  # the raw markup-shaped lookup key still matches


@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("text", _MARKUP)
def test_live_and_named_replay_markup_preserves_content(position: str, text: str) -> None:
    envelope, body, _ = _fixture(position, text)
    for out in _outputs(envelope, body):
        _assert_literal(out, text)


@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("text", ["[link=custom:target]LABEL[/link]", "[/]LABEL"])
def test_shared_builder_defaults_draw_literal_descriptor_text(position: str, text: str) -> None:
    envelope, _, rows = _fixture(position, text)
    _assert_literal(_bytes(DescriptorRenderer.build_tool_renderable(envelope, rows)), text)


@pytest.mark.parametrize("view", ["table", "form", "grid", "text"])
@pytest.mark.parametrize("title", [None, ""])
@pytest.mark.parametrize("empty", [False, True])
def test_absent_and_empty_titles_render_without_fallback(
    view: str, title: str | None, empty: bool
) -> None:
    envelope, _, _ = _fixture(f"{view}-title", "unused")
    envelope.payload.title = title  # type: ignore[union-attr]
    rows = [] if empty else ([{"k": "CONTENT"}] if view in ("table", "form") else ["CONTENT"])
    body = "" if empty and view == "text" else ("CONTENT" if view == "text" else json.dumps(rows))
    # Inspect actual output; build errors otherwise silently become default cards.
    out = _bytes(DescriptorRenderer.build_tool_renderable(envelope, rows))
    assert "\x1b]8;" not in out
    if not empty:
        assert "CONTENT" in out
    for rendered in _outputs(envelope, body):
        if view == "table":
            assert "┏" in rendered, rendered
        elif view == "text":
            assert "╭" in rendered, rendered
        else:
            assert "│ " not in _SGR.sub("", rendered), rendered
        if not empty:
            assert "CONTENT" in rendered


@pytest.mark.parametrize("ensure_ascii", [True, False], ids=["escaped", "literal"])
def test_projection_preserves_raw_unicode_markup_keys_and_collisions(ensure_ascii: bool) -> None:
    key = "[bold]part\u200bno[/bold]"
    envelope, _, _ = _fixture("table-header", "SAFE HEADER")
    envelope.payload.columns = [{"key": key, "header": "SAFE HEADER"}]  # type: ignore[union-attr]
    envelope.payload.rows_path = "[bold]nested\u200b[/bold].rows"  # type: ignore[union-attr]
    data = {"[bold]nested\u200b[/bold]": {"rows": [{key: "FIRST"}]}}
    body = json.dumps(data, ensure_ascii=ensure_ascii)
    assert DescriptorRenderer.project_tool_result(envelope, body) == [{key: "FIRST"}]
    for out in _outputs(envelope, body):
        _assert_literal(out, "SAFE HEADER")
        assert "FIRST" in out

    envelope, _, _ = _fixture("form-value", "unused")
    rows = [{"[bold]FIELD\u200b[/bold]": "FIRST", "[bold]FIELD[/bold]": "SECOND"}]
    for out in _outputs(envelope, json.dumps(rows, ensure_ascii=ensure_ascii)):
        _assert_literal(out, "[bold]FIELD[/bold]")
        assert _SGR.sub("", out).count("[bold]FIELD[/bold]") == 2
        assert "FIRST" in out and "SECOND" in out

    envelope, _, _ = _fixture("text-body", "unused")
    envelope.payload.text_path = "[bold]nested\u200b[/bold].value"  # type: ignore[union-attr]
    text = "[link=custom:target]TEXT[/link]"
    body = json.dumps({"[bold]nested\u200b[/bold]": {"value": text}}, ensure_ascii=ensure_ascii)
    assert DescriptorRenderer.project_tool_result(envelope, body) == [text]
    for out in _outputs(envelope, body):
        _assert_literal(out, text)
        assert "nested" not in out


@pytest.mark.parametrize("named", [True, False], ids=["named-descriptor", "empty-default-card"])
def test_real_session_roundtrip_preserves_named_replay_and_empty_name_fallback(named: bool) -> None:
    envelope, body, _ = _fixture("table-dict-cell", "[link=custom:target]LABEL[/link]")
    original = ToolResultMessage(
        tool_call_id="call",
        tool_name="probe" if named else "",
        content=[TextContent(text=body)],
    )
    entry = MessageEntry(
        id="entry", parent_id=None, timestamp="2026-10-12T00:00:00Z", message=original
    )
    restored = entry_from_json(entry_to_json(entry))
    assert isinstance(restored, MessageEntry)
    assert restored.message.tool_name == original.tool_name  # type: ignore[union-attr]
    commits: list[Any] = []
    lookups: list[str] = []
    renderer = EventRenderer(commit=commits.append, set_tail=lambda _: None, width=240)

    def lookup(name: str) -> DescriptorEnvelope:
        lookups.append(name)
        return envelope

    renderer.get_tool_renderer_desc = lookup
    renderer.descriptor_renderer = DescriptorRenderer  # type: ignore[assignment]
    renderer.replay([restored.message])
    assert len(commits) == 1
    out = _bytes(commits[0])
    _assert_literal(out, "[link=custom:target]LABEL[/link]")
    if named:
        assert lookups == ["probe"]
        assert _WIDE in out  # descriptor column header proves this is not default JSON
    else:
        assert lookups == []
        assert "│ " in _SGR.sub("", out) and '[{"k":' in out


def test_descriptor_sanitizers_still_apply_after_projection_before_literal_display() -> None:
    title = "[bold]FIRST\nSECOND\x1b[2J[/bold]"
    envelope, _, _ = _fixture("table-title", title)
    body = json.dumps([{"k": "[bold]VALUE\x1b[2J\u202e\nNEXT[/bold] 👩‍💻"}])
    for out in _outputs(envelope, body):
        _assert_literal(out, render._header_text(title))
        assert "[bold]VALUE" in out and "NEXT[/bold] 👩‍💻" in out
        assert "\x1b[2J" not in out and "\u202e" not in out


def test_descriptor_identity_and_tool_matching_keep_raw_markup_and_unicode() -> None:
    name = "[bold]probe\u200b[/bold]"
    envelope, body, _ = _fixture("table-dict-cell", "[bold]CONTENT[/bold]")
    envelope.namespace = "[bold]space\u200b[/bold]"
    envelope.id = "[bold]id\u200b[/bold]"
    envelope.payload.tool_name = name  # type: ignore[union-attr]
    registry = DescriptorRegistry()
    registry.apply(envelope)
    assert registry.by_kind("tool-renderer-desc") == [envelope]
    assert registry.chrome_key(envelope) == "[bold]space\u200b[/bold]:[bold]id\u200b[/bold]"
    commits: list[Any] = []
    seen: list[str] = []
    renderer = EventRenderer(commit=commits.append, set_tail=lambda _: None, width=240)

    def lookup(tool_name: str) -> DescriptorEnvelope | None:
        seen.append(tool_name)
        return envelope if tool_name == name else None

    renderer.get_tool_renderer_desc = lookup
    renderer.descriptor_renderer = DescriptorRenderer  # type: ignore[assignment]
    renderer.on_agent_event(
        ToolExecutionEndEvent(
            tool_call_id="call",
            tool_name=name,
            result=ToolResult(content=[TextContent(text=body)]),
            is_error=False,
        )
    )
    assert seen == [name] and len(commits) == 1
    _assert_literal(_bytes(commits[0]), "[bold]CONTENT[/bold]")
