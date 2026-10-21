"""Prompt-size estimation and the context clamp shared by the output-cap guards.

Extracted from ``openai_completions`` (Wave 1, #144-adjacent) so the
``anthropic-messages`` adapter can reuse the *same* estimator rather than grow a
second one that drifts. Both callers use :func:`estimate_payload_tokens` for one
purpose: decide whether a model-default output cap is compatible with the
model's context window. Its bias is deliberately conservative (over-count rather
than under-count): under-counting is what produces a provider 400, while
over-counting only trims a cap the provider would have refused anyway.

#563 adds :func:`clamp_max_tokens_to_context`, pi's ``clampMaxTokensToContext``
(``packages/ai/src/api/simple-options.ts:18-22`` at ``fdce48eb4``, introduced by
pi ``09f105957`` for pi #6061): every output cap an adapter sends is cut to
``context_window - estimated context - 4096``, at least 1. It reads the context
through :func:`estimate_context_tokens`, pi's ``estimateContextTokens``
(``packages/ai/src/utils/estimate.ts``): the last answer's reported usage plus an
estimate of what came after it. That estimate counts text as pi does, 3.5
characters a token (:func:`estimate_context_text_tokens`; a non-ASCII character is
a whole token, so it never counts less than pi), and an image as pi's 4800
characters, 1372 tokens, rather than its base64 length, because the clamp applies
to every request and :func:`estimate_payload_tokens` would charge one screenshot
hundreds of thousands of tokens and cut every later answer to 1. The guard of the
model's own cap in ``openai_completions`` keeps :func:`estimate_text_tokens`' 4
characters for the payload and takes the larger of the two.
"""

from __future__ import annotations

import dataclasses
import json
import math
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


# === #563: the output cap fits what the context window still holds ===========

CONTEXT_SAFETY_TOKENS = 4096
"""Room kept free between the estimated context and the window (pi's constant).

pi ``CONTEXT_SAFETY_TOKENS`` (``simple-options.ts:15``). It absorbs the estimate's
error: the part of the context after the last reported usage is a character
count, and a provider's tokenizer can read it as more.
"""

MIN_MAX_TOKENS = 1
"""The smallest cap the clamp returns (pi ``MIN_MAX_TOKENS``).

At 1 the request still goes out: a prompt that already overflows the window gets
the provider's own overflow error, which overflow recovery reads, rather than a
request aelix refused to build.
"""

CONTEXT_CHARS_PER_TOKEN = 3.5
"""Characters a token in :func:`estimate_context_tokens` (pi ``CHARS_PER_TOKEN``).

pi's ``estimate.ts`` divides by 3.5. Review round 2 of #563: this estimate used
the module's 4, so on the same context it asked for a larger cap than pi's and the
request could pass the window where pi's fits (an anchor of 20000 and 150000
characters after it, window 90000: 57500 here against pi's 62858, a cap of 28404
against 23046, and 62858 + 28404 > 90000).
"""

ESTIMATED_IMAGE_TOKENS = math.ceil(4800 / CONTEXT_CHARS_PER_TOKEN)
"""What one image counts as in :func:`estimate_context_tokens`: 1372.

pi charges an image 4800 characters (``ESTIMATED_IMAGE_CHARS``) at 3.5 characters
a token. The compaction estimate in
``aelix_agent_core.session.compaction.estimate_tokens`` keeps its own 1200.
"""

_SECONDS_EPOCH_LIMIT = 1e11
"""A timestamp below this is in seconds, at or above it in milliseconds.

aelix stamps the messages a turn produces with ``time.time()`` (seconds,
``aelix_agent_core/loop.py``) and a session rebuilt after a compaction gives the
summary message its entry's time in milliseconds
(``aelix_agent_core/session/context.py::_iso_to_unix_ms``). 1e11 seconds is
year 5138 and 1e11 milliseconds is 1973, so the two ranges cannot meet.
"""


def _as_ms(timestamp: Any) -> float | None:
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None
    value = float(timestamp)
    return value * 1000.0 if value < _SECONDS_EPOCH_LIMIT else value


def _usage_tokens(usage: Any) -> int:
    """pi ``calculateContextTokens``: ``totalTokens``, else the four parts.

    Mirrors ``aelix_agent_core.session.compaction.calculate_context_tokens``,
    which this package cannot import (the dependency runs the other way).
    """

    if not isinstance(usage, dict):
        return 0

    def _int(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
                return int(value)
        return 0

    total = _int("total_tokens", "totalTokens")
    if total:
        return total
    return (
        _int("input_tokens", "input")
        + _int("output_tokens", "output")
        + _int("cache_read", "cacheRead")
        + _int("cache_write", "cacheWrite")
    )


def estimate_context_text_tokens(text: str) -> int:
    """pi ``estimateTextTokens`` for :func:`estimate_context_tokens`: 3.5 characters a token.

    ``ceil(ascii / 3.5)`` plus one token per non-ASCII character, which is never
    less than pi's ``ceil(len / 3.5)`` (#563 review round 2: the clamp must not ask
    for more than pi's estimate leaves room for).
    """

    if not text:
        return 0
    ascii_chars = len(text.encode("ascii", "ignore"))
    return -(-2 * ascii_chars // 7) + (len(text) - ascii_chars)


def _tools_tokens(tools: Any) -> int:
    """pi ``estimateToolsTokens``: the tools' JSON at 3.5 characters a token.

    A tool is its name, description and parameters (what an adapter sends), dumped
    with ``json.dumps``' spaced separators, so the text is never shorter than pi's
    ``JSON.stringify``. An object without those fields is walked for its strings.
    """

    if not tools:
        return 0
    total = 0
    for tool in tools if isinstance(tools, (list, tuple)) else [tools]:
        if isinstance(tool, dict):
            shape: Any = tool
        elif hasattr(tool, "name") and hasattr(tool, "parameters"):
            shape = {
                "name": getattr(tool, "name", ""),
                "description": getattr(tool, "description", ""),
                "parameters": getattr(tool, "parameters", None),
            }
        else:
            total += -(-estimate_payload_tokens(tool) * 8 // 7)
            continue
        try:
            text = json.dumps(shape, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(shape)
        total += estimate_context_text_tokens(text)
    return total


def estimate_message_tokens(message: Any) -> int:
    """pi ``estimateMessageTokens``: the text a message carries, images flat.

    Text, thinking and tool-call names and arguments are counted with
    :func:`estimate_context_text_tokens`; an image is :data:`ESTIMATED_IMAGE_TOKENS`
    whatever its size. Each block is rounded up on its own, where pi rounds the
    message once, so the count is never below pi's. Signatures, ids and base64 data are not text the model
    reads, so they are not counted (pi does the same).
    """

    from aelix_ai.messages import (
        ImageContent,
        TextContent,
        ThinkingContent,
        ToolCallContent,
    )

    content = getattr(message, "content", None)
    if isinstance(content, str):
        return estimate_context_text_tokens(content)
    if not isinstance(content, (list, tuple)):
        return 0
    total = 0
    for block in content:
        if isinstance(block, TextContent):
            total += estimate_context_text_tokens(block.text or "")
        elif isinstance(block, ImageContent):
            total += ESTIMATED_IMAGE_TOKENS
        elif isinstance(block, ThinkingContent):
            total += estimate_context_text_tokens(block.thinking or "")
        elif isinstance(block, ToolCallContent):
            try:
                arguments = json.dumps(block.input or {}, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                arguments = str(block.input)
            total += estimate_context_text_tokens(
                block.tool_name or ""
            ) + estimate_context_text_tokens(arguments)
        elif isinstance(block, str):
            total += estimate_context_text_tokens(block)
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                total += estimate_context_text_tokens(text)
    return total


def estimate_context_tokens(context: Any) -> int:
    """pi ``estimateContextTokens``: the context a request would carry, in tokens.

    The last assistant message that reported usage anchors the count - its
    ``total_tokens`` (input, cache and output: everything up to and including that
    answer, system prompt and tools among it) - and every message after it is
    estimated. With no such message, the system prompt, the tools and every
    message are estimated.

    An answer that ended in ``error`` or ``aborted``, or reported no usage, does
    not anchor (pi's rule, and the compaction estimate's). Nor does one with a
    message stamped later before it (pi: "A newer prefix message was inserted
    after this response (for example, a compaction summary), so its usage cannot
    describe the current prefix"): after a compaction the kept answers still carry
    the usage of the long conversation they answered, and anchoring on one would
    cut the next request's output cap to almost nothing. Timestamps in seconds and
    in milliseconds are compared as milliseconds (:data:`_SECONDS_EPOCH_LIMIT`); a
    message without one neither loses its anchor nor moves the bound.
    """

    messages = list(getattr(context, "messages", None) or [])
    latest_prefix_ms = float("-inf")
    anchor_index: int | None = None
    anchor_tokens = 0
    for index, message in enumerate(messages):
        stamped = _as_ms(getattr(message, "timestamp", None))
        if getattr(message, "role", None) == "assistant":
            applies = stamped is None or stamped >= latest_prefix_ms
            tokens = _usage_tokens(getattr(message, "usage", None))
            if (
                applies
                and getattr(message, "stop_reason", None) not in ("aborted", "error")
                and tokens > 0
            ):
                anchor_index = index
                anchor_tokens = tokens
        if stamped is not None and stamped > latest_prefix_ms:
            latest_prefix_ms = stamped
    if anchor_index is not None:
        return anchor_tokens + sum(
            estimate_message_tokens(message) for message in messages[anchor_index + 1 :]
        )
    system_prompt = getattr(context, "system_prompt", None)
    return (
        (estimate_context_text_tokens(system_prompt) if isinstance(system_prompt, str) else 0)
        + _tools_tokens(getattr(context, "tools", None))
        + sum(estimate_message_tokens(message) for message in messages)
    )


def context_output_room(model: Any, context: Any) -> int | None:
    """The largest output cap the window still holds, or ``None`` if unknown.

    ``context_window - estimate_context_tokens(context) - CONTEXT_SAFETY_TOKENS``,
    at least :data:`MIN_MAX_TOKENS`. ``None`` when the model declares no window
    (``context_window <= 0``), where no arithmetic is possible.
    """

    window = getattr(model, "context_window", 0) or 0
    if isinstance(window, bool) or not isinstance(window, (int, float)) or window <= 0:
        return None
    available = int(window) - estimate_context_tokens(context) - CONTEXT_SAFETY_TOKENS
    return max(MIN_MAX_TOKENS, available)


def clamp_max_tokens_to_context(model: Any, context: Any, max_tokens: int) -> int:
    """pi ``clampMaxTokensToContext``: ``max_tokens``, cut to what the window holds.

    ``min(max_tokens, max(1, context_window - estimate - 4096))``; with no known
    window, ``max(1, max_tokens)``. The adapters apply it to every output cap they
    send, the caller's (a compaction or branch summary's) as well as the model's:
    an endpoint that checks ``prompt + max_tokens <= context_window`` (MiniMax's
    Anthropic-compatible API in pi #6061; the OpenAI-style "you requested ... in
    the completion" endpoints whose errors ``utils/overflow.py`` reads) otherwise
    rejects a long conversation's request before auto-compaction's threshold is
    reached (#563).
    """

    room = context_output_room(model, context)
    if room is None:
        return max(MIN_MAX_TOKENS, max_tokens)
    return min(max_tokens, room)


__all__ = [
    "CONTEXT_SAFETY_TOKENS",
    "ESTIMATED_IMAGE_TOKENS",
    "MIN_MAX_TOKENS",
    "OUTPUT_CAP_MARGIN_TOKENS",
    "clamp_max_tokens_to_context",
    "context_output_room",
    "CONTEXT_CHARS_PER_TOKEN",
    "estimate_context_text_tokens",
    "estimate_context_tokens",
    "estimate_message_tokens",
    "estimate_payload_tokens",
    "estimate_text_tokens",
]
