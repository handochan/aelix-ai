"""Strip lone Unicode surrogates from text — Sprint 6b (ADR-0047 §D).

Pi parity: ``packages/ai/src/utils/sanitize-unicode.ts`` (SHA 734e08e; the
same regex at ``c11a73bfb``).

Unpaired surrogate code points (U+D800..U+DFFF without a matching partner)
cause JSON serialization errors when a wire format encodes a payload: the
OpenAI and Anthropic SDKs serialize a body with ``ensure_ascii=False`` and
encode it as UTF-8, which raises ``UnicodeEncodeError`` before the request
leaves (#791). The Pi helper strips them; valid emoji and other non-BMP
characters survive because their surrogates appear in legal high+low pairs.

Python's ``str`` is a sequence of code points rather than UTF-16 code units,
so a character outside the Basic Multilingual Plane is normally ONE code point
and never matches. A string can still hold a high surrogate directly followed
by a low one as two code points: two halves that arrived as separate escapes
(``json.loads`` turns a lone ``\\uD83D`` escape into a lone surrogate, and two
stream deltas can carry one half each). Pi's regex keeps such a pair, because
in JavaScript the pair is the character, so this port keeps it too and joins
it into the one code point it encodes: left as two code points it would still
fail the UTF-8 encode (#791).
"""

from __future__ import annotations

import re

#: Any code point in the surrogate block. The patterns are escapes in raw
#: strings, so this module's own constants hold no surrogate.
_ANY_SURROGATE = re.compile(r"[\ud800-\udfff]")

#: Pi's pattern: a high surrogate that no low one follows, or a low surrogate
#: that no high one precedes.
_UNPAIRED_SURROGATE = re.compile(
    r"[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]"
)


def sanitize_surrogates(text: str) -> str:
    """Remove lone Unicode surrogates from ``text``.

    Pi parity: ``sanitizeSurrogates`` (``utils/sanitize-unicode.ts:21``).

    Args:
        text: input string (may contain stray surrogate code points).

    Returns:
        ``text`` itself when it holds no surrogate. Otherwise a copy with
        every unpaired surrogate removed and every high+low pair joined into
        the one character it encodes, so no code point in ``U+D800..U+DFFF``
        is left. Valid emoji and characters outside the Basic Multilingual
        Plane are preserved.
    """

    if _ANY_SURROGATE.search(text) is None:
        return text
    paired = _UNPAIRED_SURROGATE.sub("", text)
    # Every surrogate left is a high half directly followed by its low half,
    # so a strict UTF-16 round trip joins each pair and cannot fail.
    return paired.encode("utf-16-le", "surrogatepass").decode("utf-16-le")


__all__ = ["sanitize_surrogates"]
