"""Pi parity: ``packages/coding-agent/src/core/export-html/`` minimal port.

Sprint 6h₃ (ADR-0073, P-270/P-279/P-281) ships a syntactically valid
HTML5 document with the Pi wire-shape contract:

- :func:`export_html` returns the resolved output path as a string.
- ``output_path=None`` → Pi-shape ``aelix-session-<basename>.html``
  cwd-relative default (Pi parity: ``export-html.ts:273-277``). The
  caller (harness ``export_to_html``) supplies ``session_basename``;
  in-memory sessions are pre-empted at the harness boundary per the
  Pi error parity contract (P-279).
- ``output_path=<path>`` → the document is written there; parent
  directories are created as needed; the returned path is the
  ``Path.resolve()``-ed absolute string.

Sprint 6h₃ deliberately ships a **minimal** renderer — the goal is
the Pi wire contract (``{path: string}``) and a recognisable HTML5
document, NOT visual fidelity. Pi's full ``coding-agent/src/core/
export-html/`` subsystem is a substantial port (CSS framework,
syntax highlighting, responsive layout, image rendering) that
defers to Sprint 6h₅+ per ADR-0074 carry-forward.

Pi parity: export-html.ts:242-248 — Pi raises on in-memory or empty
session. When called from the harness path, the harness owns the
precondition checks (this function is the pure renderer + writer).
Callers should validate first.

Security: every user-controlled string flows through
:func:`html.escape` even though the file is local-only — XSS surface
matters when a user opens an exported session in a browser.
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

_HTML_DOCUMENT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 800px; margin: 2em auto; padding: 1em; }}
section.user {{ background: #f0f4f8; padding: 1em; border-radius: 8px; margin: 1em 0; }}
section.assistant {{ background: #ffffff; border: 1px solid #e0e6ed; padding: 1em; border-radius: 8px; margin: 1em 0; }}
section.tool_result {{ background: #fdf6e3; padding: 1em; border-radius: 8px; margin: 1em 0; }}
pre {{ background: #2d2d2d; color: #ccc; padding: 0.5em; border-radius: 4px; overflow-x: auto; }}
.role {{ font-weight: bold; color: #586e75; margin-bottom: 0.5em; }}
</style>
</head>
<body>
<h1>{title}</h1>
{messages}
</body>
</html>
"""


def export_html(
    messages: list[Message],
    output_path: str | None = None,
    *,
    title: str = "Aelix Session",
    session_basename: str | None = None,
) -> str:
    """Pi parity: ``session.exportToHtml(outputPath?)``.

    Render ``messages`` into a syntactically valid HTML5 document and
    write it to ``output_path``. When ``output_path`` is :data:`None`
    the default is the Pi-shape ``aelix-session-<basename>.html``
    relative to the current working directory (Pi parity:
    ``export-html.ts:273-277``; Aelix substitutes ``"aelix"`` for Pi's
    ``APP_NAME``). The ``session_basename`` kwarg supplies the
    ``<basename>`` portion; callers pass the JSONL stem.

    Pi parity: export-html.ts:242-248 — Pi raises on in-memory or
    empty session. When called from the harness path, the harness
    owns the precondition checks (this function is the pure renderer
    + writer). Callers should validate first.

    Returns the resolved (absolute) path as a string so the RPC
    ``export_html`` handler can return ``{path: str}`` per Pi's wire
    shape.

    Sprint 6h₃ minimal renderer. Visual fidelity (CSS, syntax
    highlighting, responsive layout) deferred to Sprint 6h₅+ per
    ADR-0074.
    """

    body_sections: list[str] = []
    for msg in messages:
        body_sections.append(_render_message(msg))
    body = "\n".join(body_sections)
    doc = _HTML_DOCUMENT_TEMPLATE.format(
        title=html.escape(title), messages=body
    )

    if output_path is None:
        # Pi parity: export-html.ts:273-277 — relative cwd default of
        # the form `aelix-session-<basename>.html` when session_file
        # exists. (Aelix substitutes "aelix" for Pi's APP_NAME.)
        basename = session_basename or "untitled"
        output_path = f"aelix-session-{basename}.html"
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return str(path.resolve())


def _render_message(msg: Message) -> str:
    """Render a single :class:`Message` as an HTML ``<section>``.

    Pi parity: per-role section + ``<div class="role">`` header.
    Unknown message types degrade to an HTML comment so the document
    stays syntactically valid.
    """

    if isinstance(msg, UserMessage):
        body = "\n".join(_render_content_block(b) for b in (msg.content or []))
        return f'<section class="user"><div class="role">user</div>{body}</section>'
    if isinstance(msg, AssistantMessage):
        body = "\n".join(_render_content_block(b) for b in (msg.content or []))
        return (
            f'<section class="assistant">'
            f'<div class="role">assistant</div>'
            f'{body}</section>'
        )
    if isinstance(msg, ToolResultMessage):
        body = "\n".join(_render_content_block(b) for b in (msg.content or []))
        tool_id = html.escape(getattr(msg, "tool_call_id", "") or "")
        return (
            f'<section class="tool_result">'
            f'<div class="role">tool_result <code>{tool_id}</code></div>'
            f'{body}</section>'
        )
    type_name = type(msg).__name__
    return f"<!-- unknown message type: {html.escape(type_name)} -->"


def _render_content_block(block: Any) -> str:
    """Render a content block (text / tool_call / thinking / image).

    Pi parity: ``ToolCallContent`` becomes a ``<pre>`` block carrying
    JSON-formatted arguments. ``TextContent`` becomes a ``<p>``.
    Unknown blocks degrade to HTML comments.
    """

    if isinstance(block, TextContent):
        return f"<p>{html.escape(block.text or '')}</p>"
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
        # Pi visual fidelity for images is deferred to Sprint 6h₅
        # per ADR-0074; emit a placeholder comment so the document
        # still validates.
        mime = html.escape(block.mime_type or "image")
        return f"<!-- image: {mime} -->"
    type_name = type(block).__name__
    return f"<!-- unrendered block: {html.escape(type_name)} -->"


__all__ = ["export_html"]
