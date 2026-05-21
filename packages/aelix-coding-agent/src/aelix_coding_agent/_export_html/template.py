"""Pi parity: ``packages/coding-agent/src/core/export-html/template.css`` +
``template.html`` — curated subset.

Sprint 6h₅c (Phase 4.16, ADR-0085, P-372 + P-373). Ships:

  - :data:`_THEME_CSS` — dark theme + role sections + markdown
    rendering + Pygments token classes + image rendering.
  - :data:`_HTML_TEMPLATE` — top-level HTML5 skeleton with
    placeholders ``{title}`` and ``{messages}``.

Pi parity divergences (synthesised per ADR-0085 §L Consensus Addendum):

  - Pi vendors ``highlight.min.js`` + ``marked.min.js`` (~3 MB browser
    JS). Aelix ships :mod:`pygments` + :mod:`markdown_it` for pure
    server-side rendering — semantically equivalent output, different
    class names.
  - Single fixed dark theme. Pi's luminance-based color-derivation is
    deferred to Sprint 6h₅d carry-forward.
"""

from __future__ import annotations

from pygments.formatters.html import HtmlFormatter

# Pi parity: Pygments stylesheet generation is the equivalent of Pi's
# ``hljs.css`` bundle — class names differ (``.k``, ``.s`` etc instead
# of ``.hljs-keyword``) but token coverage is equivalent. Resolved at
# module import so the constant string captures the styles once.
_PYGMENTS_CSS = HtmlFormatter(cssclass="pyg").get_style_defs(".pyg")


# === Curated dark-theme CSS (~250 LOC) =====================================
# Pi source: ``coding-agent/src/core/export-html/template.css`` SHA
# ``734e08e``. Aelix curates the visually-load-bearing subset:
#
#   - body / layout                  (Pi :1-50)
#   - role headers + section bg      (Pi :120-150 / :280-380)
#   - markdown (h1-h6, p, ul/ol, bq) (Pi :430-510)
#   - pre / code + Pygments tokens   (Pi :520-720) [via _PYGMENTS_CSS]
#   - .message-image, .tool-image    (Pi :909-930)
#
# The grouping comment headers preserve traceability to Pi line ranges
# so a future Sprint 6h₅d port can extend without re-deriving the map.

# Sprint 6h₅d §D (MINOR-1 carry-forward from ADR-0086): the original
# Sprint 6h₅c f-string interpolated ``{_PYGMENTS_CSS}`` directly into a
# 196-line CSS block which required brace-doubling on every literal CSS
# rule. Split into ``_BASE_THEME_CSS`` (everything before the Pygments
# interpolation) + ``_IMAGE_CSS`` (everything after) and concatenate so
# all CSS literals can use single braces.
_BASE_THEME_CSS = """
/* ===== body / layout (Pi :1-50) ===== */
:root {
    --bg: #1e1e1e;
    --fg: #d4d4d4;
    --section-user: #25324b;
    --section-assistant: #2a2a2a;
    --section-tool: #3a3018;
    --role: #569cd6;
    --border: #3a3a3a;
    --code-bg: #1a1a1a;
    --link: #4fc3f7;
}

* {
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    background: var(--bg);
    color: var(--fg);
    max-width: 920px;
    margin: 2em auto;
    padding: 1em 2em;
}

h1 {
    font-size: 1.6em;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.5em;
    margin-bottom: 1em;
}

a {
    color: var(--link);
    text-decoration: none;
}

a:hover {
    text-decoration: underline;
}

/* ===== role sections (Pi :280-380) ===== */
section {
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.8em 1em;
    margin: 1em 0;
    word-wrap: break-word;
}

section.user {
    background: var(--section-user);
    border-color: #3a4f6f;
}

section.assistant {
    background: var(--section-assistant);
    border-color: var(--border);
}

section.tool_result {
    background: var(--section-tool);
    border-color: #4a3e23;
}

/* ===== role header (Pi :120-150) ===== */
.role {
    font-weight: 600;
    color: var(--role);
    margin-bottom: 0.5em;
    text-transform: uppercase;
    font-size: 0.85em;
    letter-spacing: 0.05em;
}

.role code {
    background: var(--code-bg);
    color: var(--fg);
    padding: 0 0.3em;
    border-radius: 3px;
    font-size: 0.9em;
    text-transform: none;
}

/* ===== thinking blocks ===== */
.thinking {
    color: #888;
    font-style: italic;
    padding: 0.4em 0.8em;
    border-left: 2px solid #555;
    margin: 0.5em 0;
}

/* ===== markdown elements (Pi :430-510) ===== */
section h1, section h2, section h3, section h4, section h5, section h6 {
    margin-top: 1em;
    margin-bottom: 0.5em;
    color: #ffffff;
    line-height: 1.3;
}

section h1 { font-size: 1.4em; }
section h2 { font-size: 1.25em; }
section h3 { font-size: 1.1em; }
section h4 { font-size: 1em; }

section p {
    margin: 0.5em 0;
}

section ul, section ol {
    padding-left: 1.5em;
    margin: 0.5em 0;
}

section li {
    margin: 0.2em 0;
}

section blockquote {
    margin: 0.5em 0;
    padding: 0.3em 1em;
    border-left: 3px solid #555;
    color: #aaa;
}

section table {
    border-collapse: collapse;
    margin: 0.5em 0;
}

section th, section td {
    border: 1px solid var(--border);
    padding: 0.3em 0.6em;
    text-align: left;
}

section th {
    background: var(--code-bg);
}

/* ===== inline code ===== */
section code {
    background: var(--code-bg);
    color: #ce9178;
    padding: 0.1em 0.4em;
    border-radius: 3px;
    font-family: "SF Mono", Menlo, Monaco, "Courier New", monospace;
    font-size: 0.9em;
}

/* ===== pre / fenced code (Pi :520-720) ===== */
pre {
    background: var(--code-bg);
    color: var(--fg);
    padding: 0.8em 1em;
    border-radius: 4px;
    overflow-x: auto;
    margin: 0.5em 0;
    font-family: "SF Mono", Menlo, Monaco, "Courier New", monospace;
    font-size: 0.9em;
    line-height: 1.45;
}

pre code {
    background: transparent;
    color: inherit;
    padding: 0;
    border-radius: 0;
    font-size: inherit;
}

pre.pyg {
    background: var(--code-bg);
}

/* ===== Pygments token classes (Pi :520-720 equivalent) ===== */
"""


_IMAGE_CSS = """
/* ===== images (Pi :909-930 — P-373) ===== */
.message-image {
    max-width: 100%;
    max-height: 400px;
    border-radius: 4px;
    display: block;
    margin: 0.5em 0;
}

.tool-image {
    max-height: 500px;
}
"""


_THEME_CSS = _BASE_THEME_CSS + "\n" + _PYGMENTS_CSS + "\n" + _IMAGE_CSS


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
<h1>{title}</h1>
{messages}
</body>
</html>
"""


__all__ = ["_HTML_TEMPLATE", "_THEME_CSS"]
