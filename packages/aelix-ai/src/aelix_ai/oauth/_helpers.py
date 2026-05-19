"""Shared OAuth-internal helpers — Sprint 6e W6 (P-157).

Extracted from the duplicate definitions in ``anthropic.py`` /
``openai_codex.py`` / ``github_copilot.py``. Each provider previously
held its own copy of ``_maybe_await``; this module is the single owner.

Pi parity: Pi's OAuth flows use top-level ``await`` plus
``Promise.resolve()`` for sync-or-async callback invocation. Aelix
mirrors that pattern with :func:`_maybe_await`: a function that awaits
a value only when it's a coroutine/awaitable, otherwise returns it
verbatim.
"""

from __future__ import annotations

import inspect
from typing import Any


async def maybe_await(value: Any) -> Any:
    """Await ``value`` only when it's a coroutine/awaitable.

    The Sprint 6a pattern from ``providers/anthropic.py`` —
    sync-or-async callbacks invoked through this helper return their
    final value regardless of whether the implementation is sync or
    async.
    """

    if inspect.isawaitable(value):
        return await value
    return value


__all__ = ["maybe_await"]
