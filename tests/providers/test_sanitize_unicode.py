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


def test_full_surrogate_range_stripped_when_lone() -> None:
    bad = " ".join(chr(i) for i in (0xD800, 0xDABC, 0xDBFF, 0xDC00, 0xDFFF))
    assert sanitize_surrogates(bad) == "    "


def test_a_high_half_followed_by_a_low_half_is_kept_as_one_character() -> None:
    # #791. Pi's regex keeps U+DABC because U+DFFF follows it, and U+DFFF
    # because U+DABC precedes it: node on pi's pattern returns the one code
    # point U+BF3FF for this input. Before #791 this returned "".
    bad = "".join(chr(i) for i in (0xD800, 0xDABC, 0xDFFF))
    assert sanitize_surrogates(bad) == "\U000bf3ff"


def test_an_emoji_split_into_two_code_points_is_joined() -> None:
    # #791: two halves that arrived as separate escapes are the character.
    split = chr(0xD83D) + chr(0xDE48)
    assert len(split) == 2
    assert sanitize_surrogates(f"a{split}b") == "a\U0001f648b"


def test_text_without_a_surrogate_is_returned_as_it_is() -> None:
    text = "plain \U0001f648 text"
    assert sanitize_surrogates(text) is text


def _pi_reference(text: str) -> str:
    """Pi's regex, run on the UTF-16 code units JavaScript holds ``text`` as.

    ``/[\\uD800-\\uDBFF](?![\\uDC00-\\uDFFF])|(?<![\\uD800-\\uDBFF])[\\uDC00-\\uDFFF]/g``
    (``utils/sanitize-unicode.ts``, pi ``c11a73bfb``): drop a high unit that no
    low unit follows, and a low unit that no high unit precedes, both judged on
    the original sequence.
    """

    raw = text.encode("utf-16-le", "surrogatepass")
    units = [int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)]

    def high(u: int) -> bool:
        return 0xD800 <= u <= 0xDBFF

    def low(u: int) -> bool:
        return 0xDC00 <= u <= 0xDFFF

    kept = [
        u
        for i, u in enumerate(units)
        if not (high(u) and not (i + 1 < len(units) and low(units[i + 1])))
        and not (low(u) and not (i > 0 and high(units[i - 1])))
    ]
    return b"".join(u.to_bytes(2, "little") for u in kept).decode("utf-16-le")


def test_every_short_string_matches_pis_regex() -> None:
    # #791: every string of up to four pieces drawn from these, 1,555 in all.
    pieces = ["a", "\U0001f648", "\ud83d", "\ude48", "\udbff", "\udc00"]
    strings = [""]
    frontier = [""]
    for _ in range(4):
        frontier = [s + p for s in frontier for p in pieces]
        strings += frontier
    assert len(strings) == 1555
    mismatches = [s for s in strings if sanitize_surrogates(s) != _pi_reference(s)]
    assert mismatches == []


def test_bmp_around_surrogate_block_preserved() -> None:
    # The scalars adjacent to the surrogate block (U+D7FF and U+E000)
    # are valid and must be kept.
    text = "퟿"
    assert sanitize_surrogates(text) == "퟿"


def test_mixed_content_only_surrogates_stripped() -> None:
    text = "abc\ud800123\udfffxyz"
    assert sanitize_surrogates(text) == "abc123xyz"
