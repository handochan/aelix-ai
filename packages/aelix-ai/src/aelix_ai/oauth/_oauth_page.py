"""OAuth landing-page HTML — Sprint 6c · Phase 4.3 · §D.

Pi parity: ``packages/ai/src/utils/oauth/oauth-page.ts`` (SHA 734e08e).

Aelix mirrors the Pi HTML shell (dark theme + system fonts) so the
local callback page looks identical across the Pi / Aelix CLIs. The
spec (§D) allows visual divergence — no parity test asserts the
exact HTML body — only the call sites matter (200 OK with text/html
for success, 400 with text/html for error).
"""

from __future__ import annotations

_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800" '
    'aria-hidden="true"><path fill="#fff" fill-rule="evenodd" '
    'd="M165.29 165.29 H517.36 V400 H400 V517.36 H282.65 V634.72 '
    'H165.29 Z M282.65 282.65 V400 H400 V282.65 Z"/>'
    '<path fill="#fff" d="M517.36 400 H634.72 V634.72 H517.36 Z"/></svg>'
)


def _escape_html(value: str) -> str:
    """Pi parity: ``oauth-page.ts:3-10`` ``escapeHtml``."""

    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _render_page(
    *, title: str, heading: str, message: str, details: str | None = None
) -> str:
    """Pi parity: ``oauth-page.ts:12-92`` ``renderPage``."""

    title_h = _escape_html(title)
    heading_h = _escape_html(heading)
    message_h = _escape_html(message)
    details_h = _escape_html(details) if details else None
    details_block = (
        f'<div class="details">{details_h}</div>' if details_h else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title_h}</title>
  <style>
    :root {{
      --text: #fafafa;
      --text-dim: #a1a1aa;
      --page-bg: #09090b;
    }}
    * {{ box-sizing: border-box; }}
    html {{ color-scheme: dark; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: var(--page-bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
      text-align: center;
    }}
    main {{
      width: 100%;
      max-width: 560px;
    }}
    .logo {{
      width: 72px;
      height: 72px;
      display: block;
      margin: 0 auto 24px;
    }}
    h1 {{ margin: 0 0 10px; font-size: 28px; font-weight: 650; }}
    p {{ margin: 0; line-height: 1.7; color: var(--text-dim); font-size: 15px; }}
    .details {{
      margin-top: 16px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 13px;
      color: var(--text-dim);
      white-space: pre-wrap;
      word-break: break-word;
    }}
  </style>
</head>
<body>
  <main>
    <div class="logo">{_LOGO_SVG}</div>
    <h1>{heading_h}</h1>
    <p>{message_h}</p>
    {details_block}
  </main>
</body>
</html>"""


def oauth_success_html(message: str) -> str:
    """Pi parity: ``oauth-page.ts:94-100`` ``oauthSuccessHtml``."""

    return _render_page(
        title="Authentication successful",
        heading="Authentication successful",
        message=message,
    )


def oauth_error_html(message: str, details: str = "") -> str:
    """Pi parity: ``oauth-page.ts:102-109`` ``oauthErrorHtml``."""

    return _render_page(
        title="Authentication failed",
        heading="Authentication failed",
        message=message,
        details=details or None,
    )


__all__ = ["oauth_error_html", "oauth_success_html"]
