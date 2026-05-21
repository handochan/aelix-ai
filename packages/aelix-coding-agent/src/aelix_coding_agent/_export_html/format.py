"""Pi parity: ``packages/coding-agent/src/core/export-html/format.ts`` —
HTML renderer for a session message list.

Sprint 6h₅c (Phase 4.16, ADR-0085, P-372 + P-373). Renders ``messages``
through markdown-it-py + Pygments to produce a syntactically valid HTML5
document with the dark theme defined in :mod:`._template`.

Public surface:

  - :func:`export_html` — same Pi wire contract as the Sprint 6h₃
    minimal renderer (``output_path`` default + return shape).

Pi parity divergences:

  - Markdown is rendered through :mod:`markdown_it` (commonmark + table
    + breaks) instead of Pi's vendored ``marked.min.js``. Different
    library, same semantic HTML.
  - Code fences are syntax-highlighted through :mod:`pygments` instead
    of Pi's vendored ``highlight.min.js`` — class names differ
    (Pygments ``.k`` vs hljs ``.hljs-keyword``).

Security: every user-controlled string flows through
:func:`html.escape` (markdown rendering uses ``html: False`` so raw HTML
in source text is escaped, not interpreted). The base64 ``data`` URI for
images is escaped to prevent attribute injection.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from aelix_coding_agent._export_html.template import _HTML_TEMPLATE, _THEME_CSS

# Pygments formatter — ``nowrap=True`` so we control the outer
# ``<pre class="pyg">`` wrapper from :func:`_highlight`. ``cssclass="pyg"``
# matches the prefix used by :data:`_THEME_CSS` via ``get_style_defs(".pyg")``.
_HTML_FMT = HtmlFormatter(cssclass="pyg", nowrap=True)


def _highlight(code: str, lang: str, attrs: Any) -> str:
    """Pi parity: ``marked``'s ``highlight`` hook.

    Resolves a Pygments lexer for ``lang``; falls back to
    :class:`TextLexer` when the language is unknown or empty so the code
    block still renders (Pi parity: ``hljs.getLanguage`` returning
    :data:`None` triggers the auto-detection fallback). Returns the
    fully-wrapped ``<pre class="pyg"><code>...</code></pre>`` string
    that markdown-it splices in verbatim.
    """

    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    return (
        f'<pre class="pyg"><code>{highlight(code, lexer, _HTML_FMT)}</code></pre>'
    )


# Markdown renderer — `commonmark` preset + table plugin. ``html: False``
# prevents raw HTML injection from source text; ``breaks: True`` mirrors
# Pi's GitHub-flavored hard-break rendering.
_MD = MarkdownIt(
    "commonmark",
    {"breaks": True, "html": False, "highlight": _highlight},
).enable("table")


def _render_text_markdown(text: str) -> str:
    """Render ``text`` through markdown-it.

    Pi parity: the renderer also applies syntax highlighting through the
    :func:`_highlight` hook above; plain text without fences renders as a
    paragraph.
    """

    return _MD.render(text or "")


def export_html(
    messages: list[Message],
    output_path: str | None = None,
    *,
    title: str = "Aelix Session",
    session_basename: str | None = None,
) -> str:
    """Pi parity: ``session.exportToHtml(outputPath?)``.

    Render ``messages`` into a syntactically valid HTML5 document with
    full visual fidelity (Pygments syntax highlighting + markdown
    rendering + inline image data URIs + dark theme). Sprint 6h₅c
    (Phase 4.16, ADR-0085, P-372) replaces the Sprint 6h₃ minimal
    renderer; the Pi wire contract (``output_path=None`` →
    ``aelix-session-<basename>.html`` cwd-relative default; return the
    resolved absolute path) is unchanged.

    Pi parity: ``export-html.ts:242-248`` — Pi raises on in-memory or
    empty sessions. When called from the harness, the harness owns the
    precondition checks; this function is the pure renderer + writer.

    Sprint 6h₅c additive: :class:`ImageContent` blocks render as
    ``<img src="data:{mime};base64,{data}" class="message-image" />``
    (Pi parity: ``template.js:909`` — P-373). The ``tool-image`` class
    variant is appended when the image is inside a
    :class:`ToolResultMessage` so the max-height CSS rule applies.
    """

    body_sections: list[str] = []
    for msg in messages:
        body_sections.append(_render_message(msg))
    body = "\n".join(body_sections)
    doc = _HTML_TEMPLATE.format(
        title=html.escape(title), css=_THEME_CSS, messages=body
    )

    if output_path is None:
        # Pi parity: ``export-html.ts:273-277`` — relative cwd default of
        # the form ``aelix-session-<basename>.html`` when session_file
        # exists. (Aelix substitutes "aelix" for Pi's APP_NAME.)
        basename = session_basename or "untitled"
        output_path = f"aelix-session-{basename}.html"
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return str(path.resolve())


def _render_message(msg: Message) -> str:
    """Render a single :class:`Message` as an HTML ``<section>``.

    Pi parity: per-role section + ``<div class="role">`` header. Unknown
    message types degrade to an HTML comment so the document stays
    syntactically valid.
    """

    if isinstance(msg, UserMessage):
        body = "\n".join(
            _render_content_block(b, is_tool_result=False)
            for b in (msg.content or [])
        )
        return (
            f'<section class="user"><div class="role">user</div>{body}</section>'
        )
    if isinstance(msg, AssistantMessage):
        body = "\n".join(
            _render_content_block(b, is_tool_result=False)
            for b in (msg.content or [])
        )
        return (
            f'<section class="assistant">'
            f'<div class="role">assistant</div>'
            f'{body}</section>'
        )
    if isinstance(msg, ToolResultMessage):
        body = "\n".join(
            _render_content_block(b, is_tool_result=True)
            for b in (msg.content or [])
        )
        tool_id = html.escape(getattr(msg, "tool_call_id", "") or "")
        return (
            f'<section class="tool_result">'
            f'<div class="role">tool_result <code>{tool_id}</code></div>'
            f'{body}</section>'
        )
    type_name = type(msg).__name__
    return f"<!-- unknown message type: {html.escape(type_name)} -->"


def _render_content_block(block: Any, *, is_tool_result: bool = False) -> str:
    """Render a content block (text / tool_call / thinking / image).

    Pi parity: text blocks flow through markdown-it (Pi
    ``marked.parse``); :class:`ToolCallContent` becomes a JSON-pretty
    ``<pre>`` block. :class:`ImageContent` renders as an inline
    base64-data-URI ``<img>`` (Sprint 6h₅c P-373). The ``is_tool_result``
    flag selects the ``tool-image`` CSS class variant per Pi
    ``template.js:909``.
    """

    if isinstance(block, TextContent):
        # markdown-it returns a full HTML fragment including the wrapping
        # ``<p>`` element; trust the renderer output verbatim.
        return _render_text_markdown(block.text or "")
    if isinstance(block, ToolCallContent):
        args = html.escape(
            json.dumps(block.input or {}, indent=2, ensure_ascii=False)
        )
        name = html.escape(block.tool_name or "")
        return f"<pre><code>tool_call: {name}\n{args}</code></pre>"
    if isinstance(block, ThinkingContent):
        thinking = html.escape(block.thinking or "")
        return f'<p class="thinking"><em>{thinking}</em></p>'
    if isinstance(block, ImageContent):
        # Pi parity: ``template.js:909`` — inline base64 data URI.
        # Sprint 6h₅c W4/W5 P-377 BLOCKING FIX — Pi uses
        # ``class="tool-image"`` ONLY (NOT ``"message-image tool-image"``)
        # for tool-result images; the ``message-image`` class belongs to
        # the non-tool-result variant. Strict literal Pi parity.
        mime = html.escape(block.mime_type or "image/png")
        data = html.escape(block.data or "")
        classes = "tool-image" if is_tool_result else "message-image"
        return f'<img src="data:{mime};base64,{data}" class="{classes}" />'
    type_name = type(block).__name__
    return f"<!-- unrendered block: {html.escape(type_name)} -->"


__all__ = ["export_html"]
