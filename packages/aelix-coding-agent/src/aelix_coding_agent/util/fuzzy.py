"""Pi parity: ``tui/src/fuzzy.ts`` (137 LOC TS, stdlib-only).

Sprint 6h₇a (Phase 5a-iii-α, ADR-0090, P-414). Stdlib-only port —
no ``fuzzywuzzy``, no ``difflib``. Scoring constants are LOAD-BEARING
and mirror Pi exactly:

- Exact match: ``-100``
- Word-boundary char (`` ``, ``-``, ``_``, ``.``, ``/``, ``:``)
  preceding match: ``-10``
- Consecutive match: ``-5 × consecutive_count``
- Gap between matches: ``+2 × gap``
- Position (i-th matched char): ``+0.1 × i``
- Alphanumeric-swap fallback (e.g., ``codex52`` → ``5.2codex``): ``+5``
  penalty on the swapped-match score

Lower score = better match.

Pi citation at SHA ``734e08edf82ff315bc3d96472a6ebfa69a1d8016``:
``packages/tui/src/fuzzy.ts:1-137``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")

# Pi parity: ``/[\s\-_./:]/`` regex literal (``fuzzy.ts:36``). The Pi
# ``\s`` escape matches all ASCII whitespace, so we include `\t\n\r`
# alongside the literal characters enumerated in the spec table for
# byte-for-byte semantic parity. The spec table lists the six common
# characters; ``\t\n\r`` arrive via ``\s`` and are intentional, not
# a divergence.
_WORD_BOUNDARY_CHARS: frozenset[str] = frozenset(" \t\n\r-_./:")

# Pi parity: ``/^(?<letters>[a-z]+)(?<digits>[0-9]+)$/`` (``fuzzy.ts:80``).
_ALPHA_NUMERIC_RE = re.compile(r"^([a-z]+)([0-9]+)$")

# Pi parity: ``/^(?<digits>[0-9]+)(?<letters>[a-z]+)$/`` (``fuzzy.ts:81``).
_NUMERIC_ALPHA_RE = re.compile(r"^([0-9]+)([a-z]+)$")


@dataclass(frozen=True)
class FuzzyMatch:
    """Pi parity: ``FuzzyMatch`` interface (``fuzzy.ts:7-10``).

    Aelix exposes ``matched`` (Python ``bool`` convention) where Pi uses
    ``matches``. ``score`` carries the accumulated weighted score —
    lower is a better match.

    Aelix-additive: ``indices`` carries the matched-char positions into
    ``text`` for downstream highlight rendering. Pi does not expose
    indices (its TUI renderer matches on the fly), but exposing them is
    a forward-compatible no-op for the current Aelix consumer
    (``cli/list_models.py``) and saves a future re-port if highlighting
    lands in Phase 5b TUI. Empty when ``matched`` is :data:`False`.
    """

    matched: bool
    score: float
    indices: list[int]


def _match_query(query: str, text_lower: str) -> FuzzyMatch:
    """Pi parity: ``matchQuery`` inner helper (``fuzzy.ts:21-71``)."""

    if len(query) == 0:
        return FuzzyMatch(matched=True, score=0.0, indices=[])

    if len(query) > len(text_lower):
        return FuzzyMatch(matched=False, score=0.0, indices=[])

    query_index = 0
    score = 0.0
    last_match_index = -1
    consecutive_matches = 0
    indices: list[int] = []

    i = 0
    while i < len(text_lower) and query_index < len(query):
        if text_lower[i] == query[query_index]:
            # Pi parity: word-boundary test at i==0 OR previous char in
            # the ``[\s\-_./:]`` set (``fuzzy.ts:36``).
            is_word_boundary = i == 0 or text_lower[i - 1] in _WORD_BOUNDARY_CHARS

            # Reward consecutive matches.
            if last_match_index == i - 1:
                consecutive_matches += 1
                score -= consecutive_matches * 5
            else:
                consecutive_matches = 0
                # Penalize gaps.
                if last_match_index >= 0:
                    score += (i - last_match_index - 1) * 2

            # Reward word boundary matches.
            if is_word_boundary:
                score -= 10

            # Slight penalty for later matches.
            score += i * 0.1

            last_match_index = i
            indices.append(i)
            query_index += 1
        i += 1

    if query_index < len(query):
        return FuzzyMatch(matched=False, score=0.0, indices=[])

    # Pi parity: exact-match bonus when query == text (``fuzzy.ts:67``).
    if query == text_lower:
        score -= 100

    return FuzzyMatch(matched=True, score=score, indices=indices)


def fuzzy_match(query: str, text: str) -> FuzzyMatch:
    """Pi parity: ``fuzzyMatch`` (``fuzzy.ts:12-99``).

    Returns a :class:`FuzzyMatch` reporting whether ``query`` matches
    ``text`` in-order (not necessarily consecutively) and the weighted
    score (lower = better). Case-insensitive.

    When the primary match fails AND ``query`` is purely
    ``[a-z]+[0-9]+`` or ``[0-9]+[a-z]+``, retries with the
    alpha/numeric halves swapped (``codex52`` → ``52codex``) and adds a
    ``+5`` penalty to the swapped score — enables matches like
    ``codex52`` against ``gpt-5.2-codex`` (Pi `fuzzy.ts:79-99`).
    """

    query_lower = query.lower()
    text_lower = text.lower()

    primary = _match_query(query_lower, text_lower)
    if primary.matched:
        return primary

    # Pi parity: alpha/numeric swap fallback (``fuzzy.ts:79-99``).
    alpha_num = _ALPHA_NUMERIC_RE.match(query_lower)
    num_alpha = _NUMERIC_ALPHA_RE.match(query_lower)
    if alpha_num is not None:
        swapped_query = f"{alpha_num.group(2)}{alpha_num.group(1)}"
    elif num_alpha is not None:
        swapped_query = f"{num_alpha.group(2)}{num_alpha.group(1)}"
    else:
        swapped_query = ""

    if not swapped_query:
        return primary

    swapped = _match_query(swapped_query, text_lower)
    if not swapped.matched:
        return primary

    # Pi parity: +5 penalty on swapped score (``fuzzy.ts:98``).
    return FuzzyMatch(
        matched=True,
        score=swapped.score + 5,
        indices=swapped.indices,
    )


def fuzzy_filter(
    items: list[T],
    query: str,
    get_text: Callable[[T], str],
) -> list[T]:
    """Pi parity: ``fuzzyFilter`` (``fuzzy.ts:105-137``).

    Filter and sort ``items`` by fuzzy-match quality (best matches
    first). Whitespace-splits ``query`` into tokens; every token must
    :func:`fuzzy_match` ``get_text(item)`` (AND semantics). Final score
    is the sum of per-token scores; results sort ascending (lower is
    better). Empty / whitespace-only query returns ``items`` unchanged.
    """

    if not query.strip():
        return list(items)

    tokens = [t for t in query.strip().split() if t]
    if not tokens:
        return list(items)

    scored: list[tuple[T, float]] = []
    for item in items:
        text = get_text(item)
        total_score = 0.0
        all_match = True
        for token in tokens:
            match = fuzzy_match(token, text)
            if match.matched:
                total_score += match.score
            else:
                all_match = False
                break
        if all_match:
            scored.append((item, total_score))

    scored.sort(key=lambda pair: pair[1])
    return [pair[0] for pair in scored]


__all__ = [
    "FuzzyMatch",
    "fuzzy_filter",
    "fuzzy_match",
]
