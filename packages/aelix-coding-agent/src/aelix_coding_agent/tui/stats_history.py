"""Sprint 6h₃₃ (ADR-0168, WP-8 D3) — cross-session ``/stats`` history store.

The TUI-side activity tracker (:mod:`aelix_coding_agent.tui.activity_tracker`)
accumulates the LIVE session only; on a session swap it resets, and on exit it is
lost. This append-only JSONL store persists a cumulative snapshot row per turn
under ``get_agent_dir()/stats-history.jsonl`` so the ``/stats`` **History** tab can
render the cross-session views the single-session tracker cannot: a per-project
table, a per-session token trend, and an activity-by-hour heatmap.

Design (mirrors :mod:`aelix_coding_agent.tui.statusline_store` /
:class:`aelix_coding_agent.cli.project_trust.ProjectTrustStore` posture):

- **Never raises on the hot path.** :meth:`append` is best-effort — a write
  failure is swallowed (persistence must never crash the REPL turn loop).
- **Injected wall clock.** ``__init__`` takes ``clock=time.time`` so the recorded
  timestamps are unit-testable (the activity tracker's monotonic clock is for
  *durations*; history rows need wall-clock for a time-of-day heatmap).
- **Bounded.** :meth:`prune` atomically rewrites the file keeping the last
  ``keep`` rows; the caller prunes once at startup so the file can't grow without
  bound across sessions.
- **Tolerant reads.** :meth:`load` skips corrupt / wrong-shape lines (a
  half-written final line from a crash, a hand-edit) rather than failing the
  whole read.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.cli.config import get_agent_dir

if TYPE_CHECKING:
    from collections.abc import Callable

_FILENAME = "stats-history.jsonl"


@dataclass(frozen=True)
class StatsHistoryRecord:
    """One persisted cumulative snapshot of a session at a point in time.

    Rows are CUMULATIVE per session (each turn appends the session's running
    totals), so the latest row for a ``session_id`` is that session's final
    state, and the row sequence is a per-turn time series. ``ts`` is wall-clock
    epoch seconds (for the heatmap's hour-of-day bucketing).
    """

    ts: float
    session_id: str
    cwd: str
    model: str
    turns: int
    tool_calls: int
    tool_failures: int
    input: int
    output: int
    cache_read: int
    cost: float
    tool_seconds: float

    @property
    def tokens(self) -> int:
        """Throughput tokens (fresh input + output; excludes cache reads)."""

        return self.input + self.output


def _coerce(raw: dict[str, Any]) -> StatsHistoryRecord | None:
    """Build a record from a parsed JSON dict, tolerating missing/odd fields.

    Returns ``None`` (caller skips the line) if a present field has a type that
    can't be coerced — a corrupt row must not abort the whole read.
    """

    try:
        return StatsHistoryRecord(
            ts=float(raw.get("ts", 0.0) or 0.0),
            session_id=str(raw.get("session_id", "") or ""),
            cwd=str(raw.get("cwd", "") or ""),
            model=str(raw.get("model", "") or ""),
            turns=int(raw.get("turns", 0) or 0),
            tool_calls=int(raw.get("tool_calls", 0) or 0),
            tool_failures=int(raw.get("tool_failures", 0) or 0),
            input=int(raw.get("input", 0) or 0),
            output=int(raw.get("output", 0) or 0),
            cache_read=int(raw.get("cache_read", 0) or 0),
            cost=float(raw.get("cost", 0.0) or 0.0),
            tool_seconds=float(raw.get("tool_seconds", 0.0) or 0.0),
        )
    except (TypeError, ValueError):
        return None


class StatsHistoryStore:
    """Append-only JSONL store for :class:`StatsHistoryRecord`.

    :param path: the JSONL file path. Defaults to
        ``get_agent_dir()/stats-history.jsonl``.
    :param clock: wall-clock source (``time.time``) used to stamp ``ts`` when a
        record dict omits it. Injected for deterministic tests.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path) if path is not None else Path(get_agent_dir()) / _FILENAME
        self._clock = clock

    @property
    def path(self) -> Path:
        return self._path

    def append(self, fields: dict[str, Any]) -> None:
        """Append one record (a metric dict); stamps ``ts`` if absent. NEVER raises.

        The caller passes the metric fields (``session_id`` / ``cwd`` / token
        counts / …); the store stamps the wall-clock ``ts``. A directory-create
        or write failure is swallowed — losing a history row is acceptable, but
        crashing the turn loop is not.
        """

        try:
            data = dict(fields)
            data.setdefault("ts", self._clock())
            line = json.dumps(data, sort_keys=True)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001 — persistence must never crash the REPL
            return

    def load(self, *, limit: int | None = None) -> list[StatsHistoryRecord]:
        """Return parsed records oldest→newest; skips corrupt lines. NEVER raises.

        ``limit`` keeps only the last N records (the dashboard caps how much
        history it reads). A missing file → ``[]``.
        """

        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return []
        records: list[StatsHistoryRecord] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            record = _coerce(raw)
            if record is not None:
                records.append(record)
        if limit is not None and limit >= 0 and len(records) > limit:
            records = records[-limit:]
        return records

    def prune(self, keep: int) -> None:
        """Atomically rewrite the file keeping only the last ``keep`` lines.

        Best-effort + never raises. A no-op when the file is missing or already
        within ``keep`` lines. ``keep <= 0`` truncates to empty. Mirrors the
        ``statusline_store`` temp-then-``os.replace`` write so a crash mid-prune
        leaves the original intact.
        """

        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            lines = [ln for ln in text.splitlines() if ln.strip()]
            if len(lines) <= keep and keep > 0:
                return
            kept = lines[-keep:] if keep > 0 else []
            payload = "".join(f"{ln}\n" for ln in kept)
            tmp = self._path.with_name(f"{self._path.name}.tmp.{os.getpid()}")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._path)
        except Exception:  # noqa: BLE001 — pruning is housekeeping; never crash
            return


__all__ = ["StatsHistoryRecord", "StatsHistoryStore"]
