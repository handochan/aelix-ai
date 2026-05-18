"""Sprint 6b (Phase 4.2, §D) — ``sanitize_surrogates`` tests.

Pi parity: ``utils/sanitize-unicode.ts`` (SHA 734e08e). Strips lone
surrogate code points; valid BMP + non-BMP characters survive.
"""

from __future__ import annotations

from aelix_ai.providers._sanitize_unicode import sanitize_surrogates


def test_empty_string_returned_as_empty() -> None:
    assert sanitize_surrogates("") == ""


def test_ascii_only_preserved() -> None:
    assert sanitize_surrogates("hello world") == "hello world"


def test_valid_emoji_preserved() -> None:
    # Properly paired surrogates render as single Python scalars; they
    # are not in the U+D800..U+DFFF range and must survive.
    text = "Hello \U0001f648 World"
    assert sanitize_surrogates(text) == text


def test_lone_high_surrogate_stripped() -> None:
    bad = "Text \ud83d here"
    assert sanitize_surrogates(bad) == "Text  here"


def test_lone_low_surrogate_stripped() -> None:
    bad = "Text \udc00 here"
    assert sanitize_surrogates(bad) == "Text  here"


def test_full_surrogate_range_stripped() -> None:
    bad = "".join(chr(i) for i in (0xD800, 0xDABC, 0xDFFF))
    assert sanitize_surrogates(bad) == ""


def test_bmp_around_surrogate_block_preserved() -> None:
    # The scalars adjacent to the surrogate block (U+D7FF and U+E000)
    # are valid and must be kept.
    text = "퟿"
    assert sanitize_surrogates(text) == "퟿"


def test_mixed_content_only_surrogates_stripped() -> None:
    text = "abc\ud800123\udfffxyz"
    assert sanitize_surrogates(text) == "abc123xyz"
