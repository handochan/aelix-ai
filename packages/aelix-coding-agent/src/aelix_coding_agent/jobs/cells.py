"""Terminal cells for the job surfaces, without the TUI extra (#776, ADR-0299).

A kind's rows and the ``/jobs`` table are cut by the cells a terminal gives a
string, not by its length: a CJK character takes two, a combining mark none, an
emoji with U+FE0F (``❤️``, ``1️⃣``) two. Product-core cannot import ``rich`` (the
TUI extra), so this is the standard library's reading of what
``rich.cells.cell_len`` reads, and where the two differ it counts MORE: U+FE0F
widens any narrow character, a character this Python's Unicode does not know yet
counts two, a control character counts one, and each side of a zero width joiner is
counted on its own. Measured
against ``rich`` 13.7.1 to 14.3.4 — the ``[tui]`` extra's range — on the corpus of
``tests/jobs/test_jobs_table_776.py``, it is never fewer, so a cut row does not run
past its width for those; outside that range and that corpus it is not measured.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator

_ZWJ = "\u200d"
_VS16 = "\ufe0f"

#: Format characters that take no cell. Every other ``Cf`` counts one here; ``rich`` gives 33 of
#: them none (U+061C, U+180E, U+FFF9–U+FFFB, U+13430…), so those count more here.
_ZERO_FORMAT = frozenset(
    [*range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x2070), 0xFEFF, *range(0xE0000, 0xE0080)]
)

#: Code points that join the character before them and take no cell, as ``rich`` reads them: the
#: emoji skin tones and the Hangul vowel and final jamo of a decomposed syllable.
_JOINED = ((0x1F3FB, 0x1F3FF), (0x1160, 0x11FF), (0xD7B0, 0xD7FF))

#: Marks Unicode 16 added: Python 3.14 reads them as marks, ``rich`` before 14.3 gives them a cell.
_NEW_MARKS = (
    (0x0897, 0x0897), (0x10D69, 0x10D6D), (0x10EFC, 0x10EFC), (0x113BB, 0x113C0), (0x113CE, 0x113CE),
    (0x113D0, 0x113D0), (0x113D2, 0x113D2), (0x113E1, 0x113E2), (0x11F5A, 0x11F5A), (0x1611E, 0x16129),
    (0x1612D, 0x1612F), (0x1E5EE, 0x1E5EF),
)

#: Symbols newer Unicode made wide whose ``east_asian_width`` this Python may still read narrow.
_WIDE_SYMBOLS = ((0x2630, 0x2637), (0x268A, 0x268F), (0x4DC0, 0x4DFF), (0x1D300, 0x1D376))


def _control(code: int) -> bool:
    return code < 0x20 or 0x7F <= code < 0xA0


def char_cells(ch: str) -> int:
    """One code point's cells, alone: 0 for a mark or a zero-width format character. A control
    character counts one: ``rich`` 14.3.0 gives it one, its other versions none (fix round 2)."""

    code = ord(ch)
    category = unicodedata.category(ch)
    if category in ("Mn", "Me") and _within(code, _NEW_MARKS):
        return 1
    if category in ("Mn", "Me") or (category == "Cf" and code in _ZERO_FORMAT) or _within(code, _JOINED):
        return 0
    if category == "Cn" or unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2  # unassigned here: most likely a newer Unicode's emoji, so counted wide
    return 2 if _within(code, _WIDE_SYMBOLS) else 1


def _within(code: int, spans: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= code <= high for low, high in spans)


def cells(text: str) -> int:
    """The cells ``text`` takes on a terminal."""

    if text.isascii():
        return len(text)
    return max((total for _end, total in _boundaries(text)), default=0)


def _boundaries(text: str) -> Iterator[tuple[int, int]]:
    """The end of each piece a cut must not split, with the cells of ``text`` up to it.

    A piece is a character with the marks, U+FE0F and zero width joined characters
    after it. Each side of a zero width joiner is counted on its own (#776 fix round 2,
    리드 판단): ``rich`` before 14.3 counts ``👨‍👩‍👧`` as six cells and 14.3 as two, and
    this reads six, so a cut fits under either. U+FE0F widens the narrow character
    before it, as ``rich`` 14.3 reads it (more than earlier ``rich``, which gives
    U+FE0F nothing)."""

    total = 0
    last = 0  # the width of the last character measured, until a U+FE0F uses it
    for index, ch in enumerate(text):
        if ch == _VS16:
            total += 1 if last == 1 else 0
            last = 0
        elif width := char_cells(ch):
            total += width
            last = width
        after = index + 1
        if after == len(text) or not (ch == _ZWJ or text[after] == _ZWJ or _joins(text[after])):
            yield after, total


def _joins(ch: str) -> bool:
    """Whether ``ch`` belongs to the character before it: U+FE0F, a mark, a zero-width format character."""

    return ch == _VS16 or (char_cells(ch) == 0 and not _control(ord(ch)))


def clusters(text: str) -> list[str]:
    """``text`` in the pieces a cut must not split: a character with the marks, U+FE0F and
    zero width joined characters after it."""

    out: list[str] = []
    start = 0
    for end, _total in _boundaries(text):
        out.append(text[start:end])
        start = end
    return out


def cut(text: str, width: int) -> str:
    """``text`` in at most ``width`` cells, ending in ``…`` when it was cut.

    One pass that stops once the text is past ``width`` (fix round 2, verify N4: a long
    text is not split or measured to its end)."""

    if width <= 0:
        return ""
    fits = 0  # where the last piece ends that leaves a cell for the ``…``
    for end, total in _boundaries(text):
        if total > width:
            return text[:fits] + "…"
        if total < width:
            fits = end
    return text


def pad(text: str, width: int) -> str:
    """``text`` cut to ``width`` cells and padded with spaces to exactly that many."""

    shown = cut(text, width)
    return shown + " " * max(0, width - cells(shown))


__all__ = ["cells", "char_cells", "clusters", "cut", "pad"]
