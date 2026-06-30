"""Fast deterministic string-shortening hash — pi parity.

Pi parity: ``packages/ai/src/utils/hash.ts`` (``shortHash``) at SHA
``927e98068cda276bf9188f4774fb927c89823388``.

The OpenAI Responses adapter uses this hash for two byte-sensitive
purposes that MUST stay identical to pi across turns:

- generating stable synthetic ids for *foreign* tool-calls (ids that
  did not originate from OpenAI), and
- truncating message ids longer than 64 characters,

so that cross-turn reasoning/function-call pairing resolves to the same
slot on both the TypeScript and Python runtimes. Any divergence here
silently breaks encrypted-reasoning roundtrips.

The reference is a JavaScript ``Math.imul`` 32-bit mixing hash whose
output is the base-36 concatenation of two unsigned 32-bit words. We
reproduce it exactly, including:

- iteration over UTF-16 **code units** (matching ``String.length`` /
  ``charCodeAt``), so non-BMP code points contribute as surrogate
  pairs just like in V8;
- ``Math.imul`` semantics via low-32-bit multiplication;
- unsigned ``>>>`` shifts and ``>>> 0`` coercion via masking;
- lowercase ``Number.prototype.toString(36)`` formatting.

Golden vectors are cross-checked against Node in
``tests/providers/test_short_hash.py``.
"""

from __future__ import annotations

_U32 = 0xFFFFFFFF
_BASE36_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def _imul(a: int, b: int) -> int:
    """Emulate JS ``Math.imul`` — the low 32 bits of ``a * b``."""
    return (a * b) & _U32


def _utf16_code_units(s: str) -> list[int]:
    """Yield UTF-16 code units, matching JS ``charCodeAt`` iteration.

    ``str.length`` / ``charCodeAt`` walk UTF-16 code units, so a non-BMP
    code point (e.g. an emoji) is two surrogate units. Encoding to
    little-endian UTF-16 and reading 2-byte words reproduces that order
    exactly.
    """
    raw = s.encode("utf-16-le")
    return [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]


def _to_base36(n: int) -> str:
    """Lowercase base-36, matching JS ``Number.prototype.toString(36)``."""
    if n == 0:
        return "0"
    out = ""
    while n:
        out = _BASE36_DIGITS[n % 36] + out
        n //= 36
    return out


def short_hash(s: str) -> str:
    """Return pi's deterministic short hash of ``s`` (base-36 string)."""
    h1 = 0xDEADBEEF
    h2 = 0x41C6CE57
    for ch in _utf16_code_units(s):
        h1 = _imul(h1 ^ ch, 2654435761)
        h2 = _imul(h2 ^ ch, 1597334677)
    h1 = (_imul(h1 ^ (h1 >> 16), 2246822507) ^ _imul(h2 ^ (h2 >> 13), 3266489909)) & _U32
    h2 = (_imul(h2 ^ (h2 >> 16), 2246822507) ^ _imul(h1 ^ (h1 >> 13), 3266489909)) & _U32
    return _to_base36(h2) + _to_base36(h1)
