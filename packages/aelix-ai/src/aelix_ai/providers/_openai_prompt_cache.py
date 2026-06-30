"""OpenAI prompt-cache-key clamping — pi parity.

Pi parity: ``packages/ai/src/api/openai-prompt-cache.ts`` at SHA
``927e98068cda276bf9188f4774fb927c89823388``.

OpenAI rejects ``prompt_cache_key`` values longer than 64 characters.
pi clamps the key by **Unicode code point** (``Array.from`` in JS, which
iterates code points rather than UTF-16 units), so an over-long key is
truncated to its first 64 code points. We mirror that: Python string
indexing/slicing is already code-point based, so ``len(key)`` and
``key[:64]`` match ``Array.from(key).length`` / ``.slice(0, 64)``.
"""

from __future__ import annotations

OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH = 64


def clamp_openai_prompt_cache_key(key: str | None) -> str | None:
    """Clamp ``key`` to 64 code points, mirroring pi.

    Returns ``None`` unchanged. Keys at or below the limit are returned
    as-is; longer keys are truncated to their first 64 code points.
    """
    if key is None:
        return None
    if len(key) <= OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH:
        return key
    return key[:OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH]
