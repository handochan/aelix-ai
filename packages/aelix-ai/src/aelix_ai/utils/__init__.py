"""Shared helpers for :mod:`aelix_ai` (Pi parity: ``packages/ai/src/utils``)."""

from __future__ import annotations

from aelix_ai.utils.overflow import (
    get_overflow_patterns,
    is_context_overflow,
)
from aelix_ai.utils.terminal_text import (
    Controls,
    contains_steering_chars,
    safe_for_terminal,
)

def format_cost(value: float, known: bool) -> str:
    """Format a session cost, marking a lower bound with ``≥``.

    When ``known`` is false the value is a floor (a message with no price,
    a post-compaction parent, or an in-flight/interrupted delegation), so it
    is shown as ``≥ $X.XX`` rather than a confirmed charge.
    """
    if known:
        return f"${value:.2f}"
    return f"≥ ${value:.2f}"

__all__ = [
    "Controls",
    "contains_steering_chars",
    "format_cost",
    "get_overflow_patterns",
    "is_context_overflow",
    "safe_for_terminal",
]
