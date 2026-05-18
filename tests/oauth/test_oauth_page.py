"""Sprint 6c · Phase 4.3 — OAuth landing page tests."""

from __future__ import annotations

from aelix_ai.oauth._oauth_page import (
    _escape_html,
    oauth_error_html,
    oauth_success_html,
)


def test_success_html_contains_message() -> None:
    html = oauth_success_html("All done!")
    assert "All done!" in html
    assert "<!doctype html>" in html
    assert "Authentication successful" in html


def test_error_html_contains_message_and_details() -> None:
    html = oauth_error_html("Bad happened", "code=42")
    assert "Bad happened" in html
    assert "code=42" in html
    assert "Authentication failed" in html


def test_error_html_no_details_omits_details_block() -> None:
    html = oauth_error_html("Bad happened")
    assert "Bad happened" in html
    assert 'class="details"' not in html


def test_escape_html_handles_special_chars() -> None:
    """Pi parity: ``oauth-page.ts:3-10`` ``escapeHtml``."""

    assert _escape_html("<script>") == "&lt;script&gt;"
    assert _escape_html("a&b") == "a&amp;b"
    assert _escape_html('"x"') == "&quot;x&quot;"
    assert _escape_html("'x'") == "&#39;x&#39;"
