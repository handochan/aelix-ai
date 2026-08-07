"""Track S3 — picker frames are sized in terminal COLUMNS, not codepoints.

``_picker_frame`` draws its dividers ``content_width`` cells wide. Measuring
content with ``len()`` counted a Hangul syllable as 1 while the terminal
gives it 2, so every CJK row in ``/context``, ``/model``, ``/extension`` and
the stats picker under-spanned its frame by roughly half.
"""

from __future__ import annotations

from aelix_coding_agent.tui.context import _visible_len


def test_hangul_counts_two_columns_per_syllable() -> None:
    assert _visible_len("한국어") == 6


def test_ascii_is_unchanged() -> None:
    assert _visible_len("abc") == 3


def test_mixed_cjk_and_ascii() -> None:
    # 10 Hangul syllables (20 cells) + 2 spaces.
    assert _visible_len("한국어 확장 설명입니다") == 22


def test_ansi_wrapped_hangul_measures_visible_width_only() -> None:
    """ANSI stripping and width measurement have to compose."""

    assert _visible_len("\x1b[2m한국어\x1b[0m") == 6
    assert _visible_len("\x1b[1;36m한국어 확장\x1b[0m") == 11


def test_ansi_wrapped_ascii_still_ignores_escapes() -> None:
    assert _visible_len("\x1b[1mabc\x1b[0m") == 3


def test_empty_string() -> None:
    assert _visible_len("") == 0


def test_frame_divider_spans_a_hangul_row() -> None:
    """The end-to-end property: the divider is at least as wide as the row.

    This is the visible symptom — a divider shorter than the text it is
    supposed to underline.
    """

    from aelix_coding_agent.tui.context import _picker_frame

    row = "한국어 확장 설명입니다"
    frame = _picker_frame("제목", [row], "힌트", _visible_len(row))
    divider = next(
        line for line in frame.value.split("\n") if "─" in line
    )
    assert _visible_len(divider) >= _visible_len(row)
