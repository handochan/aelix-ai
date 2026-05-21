"""Sprint 6h₅c · Phase 4.16 — visual fidelity for the HTML exporter
(P-372 + P-373).

Pi parity: ``coding-agent/src/core/export-html/`` — verify Pygments
syntax highlighting + markdown-it rendering + inline base64 image
data URI shape.
"""

from __future__ import annotations

from pathlib import Path

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_coding_agent._export_html import export_html


def test_image_content_renders_as_base64_img_tag(tmp_path: Path) -> None:
    """Pi parity ``template.js:909`` — ``ImageContent`` becomes
    ``<img src="data:{mime};base64,{data}" class="message-image" />``.
    """

    msgs = [
        UserMessage(
            content=[
                ImageContent(
                    mime_type="image/png",
                    data="iVBORw0KGgoAAAA",
                )
            ]
        )
    ]
    out = tmp_path / "image.html"
    export_html(msgs, str(out), title="t", session_basename="b")
    body = out.read_text(encoding="utf-8")
    assert 'src="data:image/png;base64,iVBORw0KGgoAAAA"' in body
    assert 'class="message-image"' in body


def test_image_inside_tool_result_uses_tool_image_class(
    tmp_path: Path,
) -> None:
    """Pi parity P-377 (strict literal): tool-result image variant uses
    ``class="tool-image"`` ONLY — Pi `template.js:909` does NOT carry
    the ``message-image`` class on tool-result images.
    """

    msgs = [
        ToolResultMessage(
            tool_call_id="t-1",
            content=[ImageContent(mime_type="image/jpeg", data="abcd")],
        )
    ]
    out = tmp_path / "tool-image.html"
    export_html(msgs, str(out), title="t", session_basename="b")
    body = out.read_text(encoding="utf-8")
    # Pi strict literal parity: tool-result images carry ``tool-image``
    # ONLY, not the ``message-image tool-image`` combined string.
    assert 'class="tool-image"' in body
    assert 'class="message-image tool-image"' not in body
    assert 'src="data:image/jpeg;base64,abcd"' in body


def test_image_xss_safe(tmp_path: Path) -> None:
    """Hostile mime / data values must be escaped so attribute injection
    cannot break out of the ``src=`` quote.
    """

    msgs = [
        UserMessage(
            content=[
                ImageContent(
                    mime_type='image/png" onerror="alert(1)',
                    data='evil" onerror="bad()',
                )
            ]
        )
    ]
    out = tmp_path / "xss.html"
    export_html(msgs, str(out), title="t", session_basename="b")
    body = out.read_text(encoding="utf-8")
    # The raw ``onerror=`` attribute must NOT survive in unescaped form.
    assert 'onerror="alert(1)"' not in body
    assert 'onerror="bad()"' not in body
    # The escaped form is fine — quotes inside the URI are HTML-escaped.
    assert "&quot;" in body


def test_markdown_renders_to_paragraph(tmp_path: Path) -> None:
    """Plain text flows through markdown-it → wraps in ``<p>``."""

    msgs = [UserMessage(content=[TextContent(text="hello world")])]
    out = tmp_path / "md.html"
    export_html(msgs, str(out), title="t", session_basename="b")
    body = out.read_text(encoding="utf-8")
    assert "<p>hello world</p>" in body


def test_fenced_code_block_gets_pygments_classes(tmp_path: Path) -> None:
    """A fenced ```python``` block is highlighted by Pygments → at least
    one Pygments token class appears in the output.
    """

    code = "```python\ndef foo():\n    return 42\n```"
    msgs = [AssistantMessage(content=[TextContent(text=code)])]
    out = tmp_path / "code.html"
    export_html(msgs, str(out), title="t", session_basename="b")
    body = out.read_text(encoding="utf-8")
    # Pygments wraps the fenced block in our ``<pre class="pyg">`` envelope.
    assert 'class="pyg"' in body
    # And it tokenises ``def`` to a ``k`` (keyword) class.
    assert 'class="k"' in body


def test_unknown_language_falls_back_to_text_lexer(tmp_path: Path) -> None:
    """Pi parity: unknown language → ``TextLexer`` fallback so the block
    still renders inside ``<pre class="pyg">``.
    """

    code = "```not-a-real-language\nplain text\n```"
    msgs = [AssistantMessage(content=[TextContent(text=code)])]
    out = tmp_path / "unknown.html"
    export_html(msgs, str(out), title="t", session_basename="b")
    body = out.read_text(encoding="utf-8")
    assert 'class="pyg"' in body
    assert "plain text" in body


def test_theme_css_includes_pygments_styles(tmp_path: Path) -> None:
    """The exported HTML must include Pygments token-class style defs
    so the ``.k`` / ``.s`` / ``.c`` classes resolve at render time.
    """

    msgs = [UserMessage(content=[TextContent(text="anything")])]
    out = tmp_path / "css.html"
    export_html(msgs, str(out), title="t", session_basename="b")
    body = out.read_text(encoding="utf-8")
    # Pygments emits a ``.pyg .k`` (or similar) selector in get_style_defs.
    assert ".pyg" in body
    # The dark theme CSS variable is present.
    assert "--bg:" in body
