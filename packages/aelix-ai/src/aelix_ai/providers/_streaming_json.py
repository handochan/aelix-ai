"""Lenient incremental JSON parser — Sprint 6b (ADR-0047 §D).

Pi parity: ``packages/ai/src/utils/json-parse.ts`` (SHA 734e08e).

Pi streams ``tool_call.function.arguments`` as a UTF-8 chunk sequence
that *eventually* reconstructs a valid JSON object. The adapter calls
:func:`parse_streaming_json` after each delta so observers see the
best-effort parsed dict in :attr:`ToolCallContent.input` while the chunk
sequence is still arriving.

Pi delegates to the ``partial-json`` npm package with a thin
control-character repair pass. The Python port re-implements the same
contract with the stdlib :mod:`json` parser plus a manual
``close-the-braces`` heuristic — sufficient to satisfy the Phase 4.2
test matrix without pulling a third-party dep.

Contract:
    - ``None`` or whitespace-only input → ``{}``.
    - Valid complete JSON → :func:`json.loads` result.
    - Incomplete JSON (truncated object/array/string/number) → best-effort
      close + reparse. Returns ``{}`` if no recovery is possible.
"""

from __future__ import annotations

import json
from typing import Any


def _repair_partial(text: str) -> str:
    """Close open structures so the leftover is parseable as JSON.

    The implementation tries a sequence of progressively shorter
    candidate truncations of ``text``, closing any still-open
    ``{[`` brackets, until one parses. It is O(n²) in the worst case
    but adequate for the ~KB-scale tool-call ``arguments`` payloads we
    actually receive.
    """

    # Pre-scan once: locate the open-bracket stack so we know what to
    # append. We also build the byte-level mask of "are we inside a
    # string?" — useful for picking truncation points.
    n = len(text)
    in_string_at: list[bool] = [False] * (n + 1)
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            in_string_at[i] = True
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
        in_string_at[i + 1] = in_string

    # Candidate cut-points: end of string + every comma / bracket that's
    # NOT inside a string. We try them from longest to shortest until
    # one parses cleanly after closing the open brackets.
    cut_points: list[int] = [n]
    for i in range(n - 1, -1, -1):
        ch = text[i]
        if in_string_at[i + 1]:
            continue
        if ch in ",}]" or ch in "{[":
            cut_points.append(i + 1)

    for cut in cut_points:
        candidate = text[:cut].rstrip()
        # Strip dangling separators that prevent a clean close.
        while candidate.endswith((",", ":")):
            candidate = candidate[:-1].rstrip()
        if not candidate:
            continue
        stack_close: list[str] = []
        in_string = False
        escape = False
        for ch in candidate:
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    stack_close.append("}")
                elif ch == "[":
                    stack_close.append("]")
                elif (
                    ch in "}]"
                    and stack_close
                    and stack_close[-1] == ch
                ):
                    stack_close.pop()
        if in_string:
            # The cut sits mid-string — skip; we'll try the next one.
            continue
        repaired = candidate + "".join(reversed(stack_close))
        try:
            json.loads(repaired)
            return repaired
        except (json.JSONDecodeError, ValueError):
            continue
    return ""


def parse_streaming_json(partial: str | None) -> dict[str, Any]:
    """Best-effort parse an incremental JSON object payload.

    Pi parity: ``parseStreamingJson`` (``utils/json-parse.ts:104``).

    Args:
        partial: an in-progress JSON fragment from a streaming tool-call
            ``arguments`` field. ``None`` and empty/whitespace input
            return ``{}``.

    Returns:
        The parsed dict. If neither the input nor the repaired variant
        parse cleanly, returns ``{}`` so the caller's incremental view of
        :attr:`ToolCallContent.input` never crashes mid-stream.
    """

    if partial is None or not partial.strip():
        return {}

    # Fast path — the chunk is already a complete JSON object.
    try:
        result = json.loads(partial)
        if isinstance(result, dict):
            return result
        # Pi normalizes non-object roots to ``{}``; the agent loop only
        # ever stores tool-call inputs as a dict.
        return {}
    except (json.JSONDecodeError, ValueError):
        pass

    # Slow path — close open structures and re-parse. ``_repair_partial``
    # iterates candidate truncations until one parses; it returns the
    # empty string if no recovery is possible.
    repaired = _repair_partial(partial)
    if repaired:
        try:
            result = json.loads(repaired)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


__all__ = ["parse_streaming_json"]
