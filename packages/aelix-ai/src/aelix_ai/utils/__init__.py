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

__all__ = [
    "Controls",
    "contains_steering_chars",
    "get_overflow_patterns",
    "is_context_overflow",
    "safe_for_terminal",
]
