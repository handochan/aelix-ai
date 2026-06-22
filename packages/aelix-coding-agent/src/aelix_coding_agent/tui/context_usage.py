"""Sprint WP-8 (Feature 4) — the ``/context`` per-category composition ESTIMATOR.

This module is the PURE, dependency-light estimator half of the ``/context``
enrichment. It computes a heuristic per-category token breakdown of the live
context window so ``_context_handler`` can append an *estimated composition*
section beneath the existing measured Used / Free / Autocompact-buffer table.

Honesty is the whole point of the section: every number here is an ESTIMATE
(``ceil(len/4)`` characters→tokens, the well-known rough heuristic), so the
caller labels the section ``Estimated composition (≈, may not sum to the
measured total)`` and keeps the MEASURED total from
``SessionStats.context_usage`` authoritative. The estimate may diverge from the
provider's real tokenizer and the categories may not sum to the measured total —
that is expected and disclosed.

Design notes:

* PURE + no prompt-toolkit / Rich import — fully unit-testable in isolation and
  cheap to call. The handler (integration, NOT this module) gathers the live
  sources (system prompt / tool schemas / messages / memory text), each guarded,
  and renders the returned lines.
* :func:`estimate_categories` OMITS any category whose source is ``None`` or the
  empty string (no source → no row), so an unreachable source never fabricates a
  0-token line. Only sources that estimate to 0 tokens are dropped; a
  whitespace-only source is NOT empty and ``ceil(len/4)`` rounds it UP to ``>=1``
  token, so it still produces a (small) row — the omit rule keys off the token
  estimate, not a ``strip()``.
* :func:`build_category_lines` renders a tiny proportional bar plus
  ``Nk tokens (X%)``. The percentage is clamped to ``[0, 100]`` and the bar can
  never overflow the configured cell width, so a category larger than ``window``
  (or a zero/negative window) degrades gracefully instead of overrunning.

Reuses :func:`aelix_coding_agent.cli.list_models.format_token_count` when it is
importable (import-guarded) so ``Nk`` rendering matches the rest of the CLI;
falls back to a local formatter otherwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Category",
    "build_category_lines",
    "estimate_categories",
    "estimate_tokens",
]


def estimate_tokens(text: str | None) -> int:
    """Heuristic token estimate for ``text`` — ``ceil(len / 4)`` (labelled est.).

    The ~4-characters-per-token rule of thumb. Monotonic non-decreasing in the
    text length: a longer string never estimates fewer tokens. ``None`` / empty /
    non-``str`` input → ``0`` (no source, no tokens).
    """

    if not isinstance(text, str) or not text:
        return 0
    return math.ceil(len(text) / 4)


@dataclass(frozen=True)
class Category:
    """One estimated context category: a display ``name`` + its ``tokens``."""

    name: str
    tokens: int


def _schemas_text(tool_schemas: Any) -> str:
    """Flatten an arbitrary tool-schema collection to a single text blob.

    Tool schemas arrive as a list of dict-or-object descriptors; their exact
    shape is provider-dependent, so we just stringify each element and join. The
    estimate only needs an approximate character count, so a coarse ``str(...)``
    of each schema is sufficient (and never raises on an odd shape).
    """

    if not tool_schemas:
        return ""
    if isinstance(tool_schemas, str):
        return tool_schemas
    try:
        return "\n".join(str(s) for s in tool_schemas)
    except TypeError:
        # Not iterable — fall back to a flat stringification.
        return str(tool_schemas)


def _messages_text(messages: Any) -> str:
    """Flatten a message list to a single text blob for estimation.

    Messages may be dicts (``{"role":..., "content":...}``) or objects with a
    ``content`` attribute; content may itself be a list of parts. We extract a
    best-effort text view of each message — anything we can't read degrades to
    ``str(...)`` so the estimate stays robust to unknown shapes.
    """

    if not messages:
        return ""
    if isinstance(messages, str):
        return messages
    try:
        parts = [_message_text(m) for m in messages]
    except TypeError:
        return str(messages)
    return "\n".join(p for p in parts if p)


def _message_text(message: Any) -> str:
    """Best-effort text extraction for a single message (dict or object)."""

    content: Any
    if isinstance(message, dict):
        content = message.get("content", message)
    else:
        content = getattr(message, "content", message)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                # Common content-part shape: {"type":"text","text":"..."}.
                text = part.get("text")
                chunks.append(text if isinstance(text, str) else str(part))
            else:
                text = getattr(part, "text", None)
                chunks.append(text if isinstance(text, str) else str(part))
        return "\n".join(chunks)
    return str(content)


def estimate_categories(
    *,
    system_prompt: str | None,
    tool_schemas: list | None,
    messages: list | None,
    memory_text: str | None,
) -> list[Category]:
    """Build the estimated per-category breakdown (PURE).

    Categories, in display order: **System prompt**, **Built-in tools**,
    **Memory files**, **Messages**. A category whose source is ``None`` / empty
    (or estimates to 0 tokens) is OMITTED — only categories with a reachable,
    non-trivial source appear. Returns ``[]`` when every source is absent.

    (The spec leaves room for a Skills category once a source exists; there is no
    skills source threaded today, so it is intentionally not produced here.)
    """

    candidates: list[Category] = [
        Category("System prompt", estimate_tokens(system_prompt)),
        Category("Built-in tools", estimate_tokens(_schemas_text(tool_schemas))),
        Category("Memory files", estimate_tokens(memory_text)),
        Category("Messages", estimate_tokens(_messages_text(messages))),
    ]
    return [c for c in candidates if c.tokens > 0]


def _fmt_tokens(count: int) -> str:
    """``Nk`` formatter — reuse the CLI's ``format_token_count`` if importable."""

    try:
        from aelix_coding_agent.cli.list_models import (  # noqa: PLC0415
            format_token_count,
        )

        return format_token_count(count)
    except Exception:  # noqa: BLE001 — formatting must never break the section
        # Local fallback mirroring the CLI's K/M thresholds.
        if count >= 1_000_000:
            millions = count / 1_000_000
            return f"{int(millions)}M" if millions % 1 == 0 else f"{millions:.1f}M"
        if count >= 1_000:
            thousands = count / 1_000
            return f"{int(thousands)}K" if thousands % 1 == 0 else f"{thousands:.1f}K"
        return str(count)


def _bar(tokens: int, window: int, width: int) -> str:
    """A small proportional bar that NEVER overflows ``width`` cells.

    Filled cells are ``round(tokens / window * width)`` clamped to ``[0, width]``;
    the remainder is the empty track. A zero/negative window → an all-empty bar
    (no division by zero, no overflow).
    """

    if window <= 0 or width <= 0:
        return "░" * max(width, 0)
    filled = round(tokens / window * width)
    filled = max(0, min(filled, width))
    return "█" * filled + "░" * (width - filled)


def build_category_lines(
    categories: list[Category],
    window: int,
    *,
    bar_width: int = 16,
) -> list[str]:
    """Render ``categories`` as ``bar  name  Nk tokens (X%)`` lines (PURE).

    The percentage is ``tokens / window * 100`` CLAMPED to ``[0, 100]`` (so a
    category larger than the window, or a zero/negative window, can't print a
    nonsensical or overflowing value), and the bar is width-bounded by
    :func:`_bar`. Returns ``[]`` for no categories. Each line is a plain string;
    styling/labelling is the caller's job.
    """

    if not categories:
        return []
    name_width = max(len(c.name) for c in categories)
    lines: list[str] = []
    for cat in categories:
        pct = cat.tokens / window * 100.0 if window > 0 else 0.0
        pct = max(0.0, min(pct, 100.0))
        bar = _bar(cat.tokens, window, bar_width)
        lines.append(
            f"{bar}  {cat.name.ljust(name_width)}  "
            f"{_fmt_tokens(cat.tokens)} tokens ({pct:.0f}%)"
        )
    return lines
