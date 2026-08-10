"""Deliberately-high prompt-size estimation, shared by the output-cap guards.

Extracted from ``openai_completions`` (Wave 1, #144-adjacent) so the
``anthropic-messages`` adapter can reuse the *same* estimator rather than grow a
second one that drifts. Both callers use it for one purpose: decide whether a
model-default output cap is compatible with the model's context window.

The bias is deliberately conservative (over-count rather than under-count):
under-counting is what produces a provider 400, while over-counting only trims a
cap the provider would have refused anyway.
"""

from __future__ import annotations

import dataclasses
from typing import Any

OUTPUT_CAP_MARGIN_TOKENS = 1024
"""Fixed slack added to the prompt estimate before a model-default cap is kept.

Absorbs the per-message/per-tool framing tokens the raw string walk does not see
(role wrappers, JSON punctuation the provider counts, BOS/EOS).
"""


def estimate_text_tokens(text: str) -> int:
    """Deliberately-high token estimate for one payload string.

    ASCII characters are charged at the repo-wide ``ceil(len / 4)`` heuristic
    (same shape as ``tui/context_usage.estimate_tokens``). Non-ASCII characters
    are charged at one token per character: CJK and emoji tokenize far denser
    than 4:1, and this estimate gates a *fail-closed* decision.

    The accounting is **per character, not per string**. The original Wave 1
    implementation branched on ``str.isascii()`` for the whole string, so a
    single em-dash in a 40 KB source file charged that entire file at 1
    token/char — a 4x over-estimate that could drop a perfectly usable cap. Here
    the two classes are counted separately and summed, which keeps the
    fail-closed bias exactly where it belongs (on the non-ASCII characters) and
    nowhere else.

    ``str.encode("ascii", "ignore")`` does the character classification at C
    speed; its length is precisely the number of ASCII characters.
    """

    if not text:
        return 0
    ascii_chars = len(text.encode("ascii", "ignore"))
    non_ascii_chars = len(text) - ascii_chars
    return -(-ascii_chars // 4) + non_ascii_chars


def estimate_payload_tokens(value: Any) -> int:
    """Estimate the prompt size of a request payload or context object.

    Walks strings, mappings, sequences **and dataclass instances**. The
    dataclass arm is what lets the ``anthropic-messages`` adapter pass its
    ``Context`` (``system_prompt`` / ``messages`` / ``tools``) directly: unlike
    the OpenAI-completions adapter, it has no already-built dict payload at the
    point the cap must be decided, because the cap is an *input* to
    ``build_params``. ``dataclasses.fields`` is used rather than ``asdict`` to
    avoid deep-copying the whole message history just to measure it.
    """

    total = 0
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            total += estimate_text_tokens(current)
        elif isinstance(current, dict):
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, (list, tuple, set, frozenset)):
            stack.extend(current)
        elif dataclasses.is_dataclass(current) and not isinstance(current, type):
            stack.extend(
                getattr(current, f.name, None) for f in dataclasses.fields(current)
            )
    return total


__all__ = [
    "OUTPUT_CAP_MARGIN_TOKENS",
    "estimate_payload_tokens",
    "estimate_text_tokens",
]
