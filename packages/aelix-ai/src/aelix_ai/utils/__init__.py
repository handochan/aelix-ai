"""Shared helpers for :mod:`aelix_ai` (Pi parity: ``packages/ai/src/utils``)."""

from __future__ import annotations

from aelix_ai.utils.overflow import (
    get_overflow_patterns,
    is_context_overflow,
)

__all__ = [
    "get_overflow_patterns",
    "is_context_overflow",
]
