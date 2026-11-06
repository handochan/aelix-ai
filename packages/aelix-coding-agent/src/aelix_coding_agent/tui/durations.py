"""How the TUI writes a length of time (#702).

One place for the three shapes the TUI uses, so ``/stats`` and the line a turn
leaves cannot drift apart:

* :func:`wall_duration` — a span of minutes or hours: ``42s`` / ``3m 05s`` /
  ``1h 02m`` (``/stats``'s active time).
* :func:`call_duration` — one call's latency: ``840ms`` / ``1.2s`` / ``45s``
  (``/stats``'s per-tool latency).
* :func:`turn_duration` — a turn, from a fraction of a second to hours:
  ``840ms`` / ``4.2s`` / ``42s`` / ``1m 12s`` / ``1h 03m``. Under a second it
  reads in milliseconds and below ten seconds with one decimal, as
  ``call_duration`` does (a reply that came at once read ``0.0s``, verify V10);
  a minute and up reads as ``wall_duration``.

Each takes anything ``float()`` takes; a value it cannot read is ``0s``
(``—`` for ``call_duration``, whose ``None`` means "not timed").
"""

from __future__ import annotations

from typing import Any


def _seconds(value: Any) -> float | None:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def wall_duration(seconds: Any) -> str:
    """Human wall-clock duration (``42s`` / ``3m 05s`` / ``1h 02m``)."""

    s = _seconds(seconds)
    if s is None:
        return "0s"
    total = int(s)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60:02d}s"
    return f"{total // 3600}h {(total % 3600) // 60:02d}m"


def call_duration(seconds: Any) -> str:
    """Per-call latency: ``—`` (None/bad) / ``840ms`` / ``1.2s`` / ``45s``.

    Sub-second values read in milliseconds (tool calls are often <1s); ``[1s,10s)``
    keeps one decimal; ≥10s rounds to whole seconds. Distinct from
    :func:`wall_duration` (which formats long minute/hour spans for *session* duration).
    """

    if seconds is None:
        return "—"
    s = _seconds(seconds)
    if s is None:
        return "—"
    if s < 1.0:
        return f"{round(s * 1000)}ms"
    if s < 10.0:
        return f"{s:.1f}s"
    return f"{round(s)}s"


def turn_duration(seconds: Any) -> str:
    """A turn's length: ``840ms`` / ``4.2s`` / ``42s`` / ``1m 12s`` / ``1h 03m``."""

    s = _seconds(seconds)
    if s is None:
        return "0s"
    if round(s * 1000) < 1000:
        return f"{round(s * 1000)}ms"
    if round(s, 1) < 10.0:
        return f"{s:.1f}s"
    total = round(s)
    if total < 60:
        return f"{total}s"
    return wall_duration(total)


__all__ = ["call_duration", "turn_duration", "wall_duration"]
