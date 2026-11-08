"""The footer's jobs segment: always on while the session has a job to watch (#722, ADR-0293 §monitoring).

Shown whenever the current session has a job that is not terminal, one that is
``unresolved``, or a result the model has not been given yet:

``⚙ 2 running · 1 result waiting`` · ``⚙ 1 running (quiet 12m)`` ·
``⚙ 1 unresolved`` · ``⚙ 3 results waiting``

At a narrow width it first drops the quiet detail, then shortens to
``⚙ 2 · 1 waiting``. A product-core footer segment, not an extension status
row, so a yolo parent's narrow status area cannot cut it off (ADR-0278).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

from aelix_coding_agent.jobs.contract import JobSnapshot

GLYPH = "⚙"
_LIVE = ("starting", "running", "stopping")


def _cells(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _minutes(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h{minutes % 60:02d}m"


def indicator_text(
    snapshots: Iterable[JobSnapshot], width: int | None = None, *, extra_waiting: int = 0
) -> str | None:
    """The segment for ``snapshots``, or ``None`` when there is nothing to show.

    ``extra_waiting`` counts results that are not a job's — notices an
    extension posted (``ExtensionAPI.post_notice``) that the model has not
    been given yet (verify S3).
    """

    snaps = list(snapshots)
    running = [s for s in snaps if s.state in _LIVE]
    unresolved = [s for s in snaps if s.state == "unresolved"]
    waiting = sum(1 for s in snaps if s.result_waiting) + max(0, extra_waiting)
    if not (running or unresolved or waiting):
        return None
    quiet = [s.last_activity_ago or 0.0 for s in running if s.quiet]

    def build(*, detail: bool, short: bool) -> str:
        parts: list[str] = []
        if running:
            text = f"{len(running)}" if short else f"{len(running)} running"
            if detail and quiet:
                text += f" (quiet {_minutes(max(quiet))})"
            parts.append(text)
        if unresolved:
            parts.append(f"{len(unresolved)} unresolved")
        if waiting:
            if short:
                parts.append(f"{waiting} waiting")
            else:
                noun = "result" if waiting == 1 else "results"
                parts.append(f"{waiting} {noun} waiting")
        return f"{GLYPH} " + " · ".join(parts)

    for detail, short in ((True, False), (False, False), (False, True)):
        text = build(detail=detail, short=short)
        if width is None or _cells(text) <= width:
            return text
    return build(detail=False, short=True)


__all__ = ["GLYPH", "indicator_text"]
