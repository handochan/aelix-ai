"""Strip lone Unicode surrogates from text — Sprint 6b (ADR-0047 §D).

Pi parity: ``packages/ai/src/utils/sanitize-unicode.ts`` (SHA 734e08e).

Unpaired surrogate code points (U+D800..U+DFFF without a matching partner)
cause JSON serialization errors when the OpenAI / OpenRouter wire format
encodes a payload. The Pi helper strips them; valid emoji and other
non-BMP characters survive because their surrogates appear in legal
high+low pairs.

Python's ``str`` is a sequence of Unicode scalars rather than UTF-16 code
units, so we iterate over the underlying code points and drop any that
fall inside the surrogate range. This is byte-equivalent to Pi's regex
replacement for any string that started life as valid UTF-8 / UTF-16 —
the only inputs that survive are scalars that were never legal anyway.
"""

from __future__ import annotations


def sanitize_surrogates(text: str) -> str:
    """Remove lone Unicode surrogates from ``text``.

    Pi parity: ``sanitizeSurrogates`` (``utils/sanitize-unicode.ts:21``).

    Args:
        text: input string (may contain stray surrogate code points).

    Returns:
        A copy of ``text`` with every scalar in ``U+D800..U+DFFF``
        removed. Valid emoji and characters outside the Basic
        Multilingual Plane are preserved because Python strings store
        them as single non-surrogate scalars.
    """

    return "".join(c for c in text if not 0xD800 <= ord(c) <= 0xDFFF)


__all__ = ["sanitize_surrogates"]
