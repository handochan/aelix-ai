"""Sprint 6h₃ (ADR-0073, P-270) — minimal HTML emitter unit tests.

Pi parity: :func:`aelix_coding_agent._export_html.export_html` produces
a syntactically valid HTML5 document with per-role sections. The tests
pin the wire contract (returned path, file contents) without locking
in Pi visual fidelity (deferred to Sprint 6h₅+ per ADR-0074).
"""

from __future__ import annotations

import os
from pathlib import Path

from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_coding_agent._export_html import export_html


def test_export_html_empty_messages_returns_path(tmp_path: Path) -> None:
    """Pi parity: ``[]`` messages → still produces a valid document."""

    out = tmp_path / "empty.html"
    path = export_html([], output_path=str(out))
    assert Path(path).exists()
    contents = Path(path).read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in contents
    assert "<html" in contents
    assert "</html>" in contents


def test_export_html_writes_to_supplied_path(tmp_path: Path) -> None:
    """Pi parity: when ``output_path`` is given, the file lands there."""

    out = tmp_path / "session.html"
    path = export_html([], output_path=str(out))
    # Pi returns the resolved absolute path.
    assert path == str(out.resolve())
    assert out.exists()


def test_export_html_creates_parent_dirs(tmp_path: Path) -> None:
    """Pi parity: parent directories are created when missing."""

    out = tmp_path / "nested" / "deeper" / "session.html"
    assert not out.parent.exists()
    path = export_html([], output_path=str(out))
    assert Path(path).exists()
    assert out.parent.exists()


def test_export_html_none_path_uses_pi_shape_default(tmp_path: Path) -> None:
    """Pi parity (P-281 W6): ``output_path=None`` → cwd-relative
    ``aelix-session-<basename>.html`` per ``export-html.ts:273-277``.
    """

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        path = export_html([], session_basename="abc123")
        p = Path(path)
        try:
            assert p.exists()
            assert p.name == "aelix-session-abc123.html"
            # Resolved absolute path lives under the cwd.
            assert p.parent == tmp_path.resolve()
        finally:
            if p.exists():
                os.unlink(p)
    finally:
        os.chdir(cwd)


def test_export_html_none_path_without_basename_uses_untitled(
    tmp_path: Path,
) -> None:
    """Pi parity (P-281 W6): missing ``session_basename`` falls back to
    ``aelix-session-untitled.html``.
    """

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        path = export_html([])
        p = Path(path)
        try:
            assert p.exists()
            assert p.name == "aelix-session-untitled.html"
        finally:
            if p.exists():
                os.unlink(p)
    finally:
        os.chdir(cwd)


def test_export_html_renders_user_assistant_tool_result(tmp_path: Path) -> None:
    """Pi parity: per-role sections with role-class CSS hooks."""

    msgs = [
        UserMessage(content=[TextContent(text="hello")]),
        AssistantMessage(content=[TextContent(text="hi back")]),
        ToolResultMessage(
            tool_call_id="t1", content=[TextContent(text="result")]
        ),
    ]
    out = tmp_path / "doc.html"
    path = export_html(msgs, output_path=str(out))
    body = Path(path).read_text(encoding="utf-8")
    assert 'section class="user"' in body
    assert 'section class="assistant"' in body
    assert 'section class="tool_result"' in body
    assert "hello" in body
    assert "hi back" in body
    assert "result" in body


def test_export_html_tool_call_renders_as_pre_block(tmp_path: Path) -> None:
    """Pi parity: ``ToolCallContent`` becomes a ``<pre>`` with JSON args."""

    msg = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="c1",
                tool_name="bash",
                input={"command": "echo hi"},
            )
        ]
    )
    out = tmp_path / "tc.html"
    path = export_html([msg], output_path=str(out))
    body = Path(path).read_text(encoding="utf-8")
    assert "<pre>" in body
    assert "tool_call: bash" in body
    # JSON args appear, properly indented + escaped.
    assert "&quot;command&quot;" in body
    assert "echo hi" in body


def test_export_html_escapes_user_text(tmp_path: Path) -> None:
    """Security: every user-controlled string flows through html.escape."""

    msg = UserMessage(
        content=[TextContent(text="<script>alert('xss')</script>")]
    )
    out = tmp_path / "xss.html"
    path = export_html([msg], output_path=str(out))
    body = Path(path).read_text(encoding="utf-8")
    assert "<script>alert" not in body
    assert "&lt;script&gt;alert" in body


def test_export_html_escapes_title(tmp_path: Path) -> None:
    """Security: the ``title`` kwarg also flows through html.escape."""

    out = tmp_path / "title.html"
    path = export_html(
        [], output_path=str(out), title="<b>hello</b>"
    )
    body = Path(path).read_text(encoding="utf-8")
    assert "<b>hello</b>" not in body
    assert "&lt;b&gt;hello&lt;/b&gt;" in body


def test_export_html_escapes_tool_call_arguments(tmp_path: Path) -> None:
    """Security: tool-call JSON arguments are html-escaped before emit."""

    msg = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="c1",
                tool_name="bash",
                input={"cmd": "<script>"},
            )
        ]
    )
    out = tmp_path / "tcxss.html"
    path = export_html([msg], output_path=str(out))
    body = Path(path).read_text(encoding="utf-8")
    assert "<script>" not in body  # raw payload must NOT appear unescaped
    assert "&lt;script&gt;" in body


def test_export_html_empty_content_does_not_crash(tmp_path: Path) -> None:
    """Defensive: messages with empty content lists are still rendered."""

    msgs = [
        UserMessage(content=[]),
        AssistantMessage(content=[]),
    ]
    out = tmp_path / "empty-content.html"
    path = export_html(msgs, output_path=str(out))
    body = Path(path).read_text(encoding="utf-8")
    assert 'section class="user"' in body
    assert 'section class="assistant"' in body


def test_export_html_returns_resolved_absolute_path(tmp_path: Path) -> None:
    """Pi parity: relative ``output_path`` resolves to absolute."""

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        path = export_html([], output_path="rel.html")
        assert Path(path).is_absolute()
        assert Path(path).exists()
    finally:
        os.chdir(cwd)
