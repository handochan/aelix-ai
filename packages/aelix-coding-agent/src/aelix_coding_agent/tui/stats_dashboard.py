"""Pure formatters + DI flow for the ``/stats`` usage dashboard (WP-8, Feature 2).

The harness :class:`aelix_agent_core.harness._session_stats.SessionStats`
(``await get_session_stats()``) carries aggregate counts/tokens/cost; the
TUI-side :class:`aelix_coding_agent.tui.activity_tracker.SessionActivityTracker`
(its :class:`ActivitySnapshot`) adds the per-tool success/failure split, the
per-model breakdown, the turn count, and wall-clock timing the harness does NOT
retain. This module turns those two read-only inputs into the per-session tab
bodies, adds a persisted cross-session **History** tab (WP-8 D3, fed by
:class:`aelix_coding_agent.tui.stats_history.StatsHistoryStore`), and drives the
framed tabbed viewer.

Design (mirrors :mod:`aelix_coding_agent.tui.model_picker` /
:mod:`aelix_coding_agent.tui.mcp_viewer`):

- The three ``build_*_tab`` functions are side-effect-free and dependency-light:
  they take duck-typed ``stats`` / ``snapshot`` objects and return ``list[str]``
  (the render signature the shared ``ctx.tabbed`` primitive expects per tab), so
  the FORMATTING is unit-testable without standing up the prompt-toolkit modal.
- :func:`run_stats` is module-level + dependency-injected (``stats_getter`` /
  ``snapshot`` / ``tabbed`` / ``commit`` callables) so the whole flow is testable
  too; ``shell.py`` wires the live harness getter + tracker snapshot +
  ``AelixTUIContext.tabbed`` + output-committer into it.
- **Degrade, never crash.** A ``stats_getter`` failure commits a red line and
  returns — the tracker-only tabs are NOT shown half-built, the REPL survives.
- **Honesty.** The per-session tabs are live-only; the cross-session heatmap /
  token trend / project table live on the persisted History tab (each per-session
  tab footer points there). Empty history degrades to an honest one-liner rather
  than a fabricated chart.

The dim footer escape codes mirror ``context.py`` (``_PICK_DIM`` / ``_PICK_RST``)
so a note renders dim inside the framed modal; they are duplicated here as small
local constants rather than importing ``context.py`` privates (this module stays
a leaf consumer that never reaches into a shared file).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Dim / reset escapes — mirror context.py:71/73 so a footer note renders dim
# inside the framed tabbed modal (duplicated, not imported, to keep this a leaf).
_DIM = "\x1b[2m"
_RST = "\x1b[0m"

# Width of the small inline composition / leaderboard bars.
_BAR_WIDTH = 16

# Pointer (the per-session tabs are live-only; the cross-session views are now
# persisted and live on the History tab — Sprint 6h₃₃ / D3).
_HISTORY_TAB_NOTE = "Cross-session heatmap · token trend · project table → the History tab."

# Intensity ramp for the activity-by-hour heatmap (9 levels, light → dark).
_HEAT_RAMP = " ▁▂▃▄▅▆▇█"


def _dim(text: str) -> str:
    """Wrap ``text`` in the dim/reset escapes used by the picker frame."""

    return f"{_DIM}{text}{_RST}"


def _num(value: Any) -> str:
    """Thousands-separated integer string; degrades to ``0`` on a bad value."""

    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _pct(fraction: float | None) -> str:
    """Render a ``[0,1]`` fraction as a whole-percent string (``—`` when None)."""

    if fraction is None:
        return "—"
    try:
        return f"{round(fraction * 100)}%"
    except (TypeError, ValueError):
        return "—"


def _ratio_pct(numerator: Any, denominator: Any) -> str:
    """``numerator/denominator`` as a percent string (``—`` when denom is 0)."""

    try:
        denom = int(denominator)
        if denom <= 0:
            return "—"
        return f"{round(int(numerator) / denom * 100)}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "—"


def _bar(fraction: float, width: int = _BAR_WIDTH) -> str:
    """A small block bar for ``fraction`` clamped to ``[0,1]`` over ``width``."""

    try:
        frac = max(0.0, min(1.0, float(fraction)))
    except (TypeError, ValueError):
        frac = 0.0
    filled = round(frac * width)
    return "█" * filled + "░" * (width - filled)


def _wall(seconds: Any) -> str:
    """Human wall-clock duration (``42s`` / ``3m 05s`` / ``1h 02m``)."""

    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return "0s"
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60:02d}s"
    return f"{total // 3600}h {(total % 3600) // 60:02d}m"


def _dur(seconds: Any) -> str:
    """Per-call latency: ``—`` (None/bad) / ``840ms`` / ``1.2s`` / ``45s``.

    Sub-second values read in milliseconds (tool calls are often <1s); ``[1s,10s)``
    keeps one decimal; ≥10s rounds to whole seconds. Distinct from :func:`_wall`
    (which formats long minute/hour spans for *session* duration).
    """

    if seconds is None:
        return "—"
    try:
        s = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "—"
    if s < 1.0:
        return f"{round(s * 1000)}ms"
    if s < 10.0:
        return f"{s:.1f}s"
    return f"{round(s)}s"


def _avg_tool_seconds(per_tool: Any) -> float | None:
    """Mean latency across all *timed* tool calls in a per-tool list (duck-typed).

    Reads ``timed_calls`` / ``total_duration`` off each duck-typed stat so it
    works for a real :class:`ToolStat` AND a sparse ``SimpleNamespace`` fixture.
    None when nothing was timed (no paired start/end) — never a misleading ``0``.
    """

    rows = list(per_tool or [])
    timed = sum(int(getattr(t, "timed_calls", 0) or 0) for t in rows)
    if timed <= 0:
        return None
    total = sum(float(getattr(t, "total_duration", 0.0) or 0.0) for t in rows)
    return total / timed


def build_session_tab(stats: Any, snapshot: Any) -> list[str]:
    """Session-summary tab: tool calls (ok/fail), success %, tokens, cost, etc.

    ``stats`` is a duck-typed :class:`SessionStats` (``tokens.{input,output,
    cache_read,cache_write,total}`` / ``cost`` / message counts); ``snapshot`` is
    a duck-typed :class:`ActivitySnapshot` (``tool_calls`` / ``tool_failures`` /
    ``success_rate`` / ``wall_seconds`` / ``turns``). Every field is read through
    :func:`getattr` so a sparse object never breaks rendering.
    """

    tokens = getattr(stats, "tokens", None)
    tok_in = getattr(tokens, "input", 0)
    tok_out = getattr(tokens, "output", 0)
    cache_read = getattr(tokens, "cache_read", 0)
    cache_write = getattr(tokens, "cache_write", 0)

    calls = getattr(snapshot, "tool_calls", 0) or 0
    failures = getattr(snapshot, "tool_failures", 0) or 0
    ok = max(0, calls - failures)
    success = _pct(getattr(snapshot, "success_rate", None))

    cost = getattr(stats, "cost", 0.0) or 0.0
    try:
        cost_str = f"${float(cost):.4f}"
    except (TypeError, ValueError):
        cost_str = "$0.0000"

    avg_latency = _dur(_avg_tool_seconds(getattr(snapshot, "per_tool", [])))

    lines = [
        f"Tool calls    {_num(calls)}  (✓ {_num(ok)}  ✗ {_num(failures)})",
        f"Success rate  {success}",
        f"Tool latency  {avg_latency}",
        "",
        f"Tokens in     {_num(tok_in)}",
        f"Tokens out    {_num(tok_out)}",
        f"Cache read    {_num(cache_read)}",
        f"Cache write   {_num(cache_write)}",
        f"Cost          {cost_str}",
        "",
        f"Messages      {_num(getattr(stats, 'total_messages', 0))}"
        f"  (you {_num(getattr(stats, 'user_messages', 0))} ·"
        f" assistant {_num(getattr(stats, 'assistant_messages', 0))})",
        f"Turns         {_num(getattr(snapshot, 'turns', 0))}",
        # "Active time" (not "Wall time"): ``wall_seconds`` spans the FIRST event
        # to the LAST event, so it reflects time spent with the agent active and
        # does NOT advance while the session sits idle waiting on the user.
        f"Active time   {_wall(getattr(snapshot, 'wall_seconds', 0.0))}",
        "",
        _dim(_HISTORY_TAB_NOTE),
    ]
    return lines


def build_activity_tab(snapshot: Any) -> list[str]:
    """Activity tab: turns + a per-model table (requests / in / out / cache).

    ``snapshot.per_model`` is a list of duck-typed :class:`ModelStat`
    (``model`` / ``requests`` / ``input`` / ``output`` / ``cache_read``) already
    sorted busiest-first by the tracker.
    """

    per_model = list(getattr(snapshot, "per_model", []) or [])
    lines = [
        f"Turns         {_num(getattr(snapshot, 'turns', 0))}",
        # See build_session_tab: span first→last event, so "Active time".
        f"Active time   {_wall(getattr(snapshot, 'wall_seconds', 0.0))}",
        "",
        "Per-model usage",
    ]
    if not per_model:
        lines.append("  (no model requests recorded yet)")
    else:
        lines.append(
            f"  {'model':<28}{'reqs':>6}{'in':>10}{'out':>10}{'cache':>10}"
        )
        for stat in per_model:
            model = str(getattr(stat, "model", "?") or "?")
            if len(model) > 28:
                model = model[:27] + "…"
            lines.append(
                f"  {model:<28}"
                f"{_num(getattr(stat, 'requests', 0)):>6}"
                f"{_num(getattr(stat, 'input', 0)):>10}"
                f"{_num(getattr(stat, 'output', 0)):>10}"
                f"{_num(getattr(stat, 'cache_read', 0)):>10}"
            )
    lines.extend(["", _dim(_HISTORY_TAB_NOTE)])
    return lines


def build_efficiency_tab(snapshot: Any) -> list[str]:
    """Efficiency tab: cache-hit %, tool success %, per-tool leaderboard.

    Cache-hit % = ``cache_read / (cache_read + input)`` summed across models. This
    treats ``input`` as the UNCACHED prompt tokens (the Anthropic convention,
    where cache reads are reported separately from ``input``). Providers that
    report a GROSS ``input`` already INCLUDING the cached tokens (e.g. OpenAI)
    make this an under-estimate — the tab footer states the assumption rather
    than silently implying a single canonical number. Tool success % = the
    snapshot's ``success_rate``; the leaderboard lists each tool's calls /
    failures with a small success bar, busiest-first (the tracker pre-sorts
    ``per_tool``).
    """

    per_model = list(getattr(snapshot, "per_model", []) or [])
    cache_read = sum(int(getattr(m, "cache_read", 0) or 0) for m in per_model)
    fresh_in = sum(int(getattr(m, "input", 0) or 0) for m in per_model)
    cache_hit = _ratio_pct(cache_read, cache_read + fresh_in)

    success = _pct(getattr(snapshot, "success_rate", None))

    per_tool = list(getattr(snapshot, "per_tool", []) or [])
    avg_latency = _dur(_avg_tool_seconds(per_tool))

    lines = [
        f"Cache-hit rate   {cache_hit}",
        _dim("  (assumes uncached input; gross-input providers under-report)"),
        f"Tool success     {success}",
        f"Tool latency     {avg_latency}",
        "",
        "Tool leaderboard",
    ]
    if not per_tool:
        lines.append("  (no tool calls recorded yet)")
    else:
        for stat in per_tool:
            name = str(getattr(stat, "name", "?") or "?")
            calls = int(getattr(stat, "calls", 0) or 0)
            failures = int(getattr(stat, "failures", 0) or 0)
            ok = max(0, calls - failures)
            frac = (ok / calls) if calls > 0 else 0.0
            # Per-tool average latency (``—`` when the tool had no timed calls).
            row_latency = _dur(_avg_tool_seconds([stat]))
            lines.append(
                f"  {name:<18}{_bar(frac)}  "
                f"{_num(calls)} calls · {_num(failures)} fail · {row_latency}"
            )
    lines.extend(["", _dim(_HISTORY_TAB_NOTE)])
    return lines


# === History tab (WP-8 D3, Sprint 6h₃₃) — cross-session, persisted ===========


def _rec_tokens(record: Any) -> int:
    """Throughput tokens (fresh input + output) off a duck-typed history record."""

    return int(getattr(record, "input", 0) or 0) + int(getattr(record, "output", 0) or 0)


def _project_name(cwd: Any) -> str:
    """Short project label = the final path component of ``cwd`` (else ``(unknown)``)."""

    text = str(cwd or "").strip()
    if not text:
        return "(unknown)"
    return Path(text).name or text


def _local_hour(ts: float) -> int:
    """Hour-of-day (0–23) in local time, for the heatmap bucketing."""

    return time.localtime(ts).tm_hour


def _short_when(ts: Any) -> str:
    """Compact local ``MM-DD HH:MM`` stamp for a history row (``—`` on a bad ts)."""

    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "—"


def _latest_per_session(records: list[Any]) -> list[Any]:
    """Collapse the cumulative per-turn rows to the LATEST row per ``session_id``.

    Rows are cumulative, so the highest-``ts`` row for a session is its final
    state. Rows with no ``session_id`` are kept individually (each its own
    "session") so a legacy/odd row still contributes to the project table.
    """

    latest: dict[str, Any] = {}
    for record in records:
        sid = str(getattr(record, "session_id", "") or "")
        key = sid or f"@{id(record)}"
        prev = latest.get(key)
        if prev is None or float(getattr(record, "ts", 0.0) or 0.0) >= float(
            getattr(prev, "ts", 0.0) or 0.0
        ):
            latest[key] = record
    return list(latest.values())


def _compact_tokens(n: int) -> str:
    """Token count bounded to a narrow column: ``987.7M`` / ``12.3k`` / ``3,900``."""

    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}k"
    return _num(n)


def _compact_cost(c: Any) -> str:
    """USD bounded to a narrow column: ``$1.2k`` / ``$150.00`` / ``$0.0400``."""

    try:
        v = max(0.0, float(c))
    except (TypeError, ValueError):
        return "$0.0000"
    if v >= 1000:
        return f"${v / 1000:.1f}k"
    if v >= 100:
        return f"${v:.2f}"
    return f"${v:.4f}"


def _project_table_lines(sessions: list[Any]) -> list[str]:
    """Per-project aggregate (sessions / tokens / cost / tool calls), busiest first.

    A CROSS-SESSION cumulative aggregate, so token totals and costs can grow large.
    Columns are abbreviated (``987.7M`` / ``$1.2k``) AND separated by a literal
    space so the worst case can't fuse into a digit-wall (6h₃₃ review, ux LOW-1).
    """

    agg: dict[str, dict[str, float]] = {}
    for s in sessions:
        proj = _project_name(getattr(s, "cwd", ""))
        a = agg.setdefault(proj, {"sessions": 0, "tokens": 0, "cost": 0.0, "calls": 0})
        a["sessions"] += 1
        a["tokens"] += _rec_tokens(s)
        a["cost"] += float(getattr(s, "cost", 0.0) or 0.0)
        a["calls"] += int(getattr(s, "tool_calls", 0) or 0)
    if not agg:
        return ["  (no projects recorded yet)"]
    rows = sorted(agg.items(), key=lambda kv: (-kv[1]["tokens"], kv[0]))
    out = [f"  {'project':<20} {'sess':>5} {'tokens':>9} {'cost':>9} {'tools':>7}"]
    for proj, a in rows[:10]:
        label = proj if len(proj) <= 20 else proj[:19] + "…"
        out.append(
            f"  {label:<20}"
            f" {_num(a['sessions']):>5}"
            f" {_compact_tokens(int(a['tokens'])):>9}"
            f" {_compact_cost(a['cost']):>9}"
            f" {_num(a['calls']):>7}"
        )
    if len(rows) > 10:
        out.append(_dim(f"  … {len(rows) - 10} more project(s) (top 10 by tokens shown)"))
    return out


def _token_trend_lines(sessions: list[Any], *, rows: int = 8, width: int = 20) -> list[str]:
    """Recent sessions' throughput tokens as small bars (oldest → newest)."""

    ordered = sorted(sessions, key=lambda s: float(getattr(s, "ts", 0.0) or 0.0))[-rows:]
    if not ordered:
        return ["  (no sessions recorded yet)"]
    peak = max((_rec_tokens(s) for s in ordered), default=0)
    out: list[str] = []
    for s in ordered:
        tok = _rec_tokens(s)
        frac = (tok / peak) if peak > 0 else 0.0
        out.append(f"  {_short_when(getattr(s, 'ts', 0.0))}  {_bar(frac, width)}  {_num(tok)}")
    return out


def _heatmap_lines(records: list[Any], hour_of: Callable[[float], int]) -> list[str]:
    """Activity (≈ turns, one row each) in 4 six-hour blocks, light → dark."""

    counts = [0] * 24
    for record in records:
        ts = getattr(record, "ts", None)
        if ts is None:
            continue
        try:
            hour = int(hour_of(float(ts))) % 24
        except (TypeError, ValueError, OSError):
            continue
        counts[hour] += 1
    peak = max(counts)
    if peak <= 0:
        return ["  (no activity recorded yet)"]
    last = len(_HEAT_RAMP) - 1

    def cell(count: int) -> str:
        return _HEAT_RAMP[round(count / peak * last)]

    out: list[str] = []
    for start in (0, 6, 12, 18):
        block = "".join(cell(counts[start + i]) for i in range(6))
        out.append(f"  {start:02d}–{start + 5:02d}  {block}")
    return out


def build_history_tab(
    records: list[Any], *, hour_of: Callable[[float], int] = _local_hour
) -> list[str]:
    """Cross-session History tab: per-project table + token trend + hour heatmap.

    ``records`` is the duck-typed :class:`StatsHistoryRecord` list from
    :class:`aelix_coding_agent.tui.stats_history.StatsHistoryStore` (cumulative,
    one row per turn). ``hour_of`` is injected so the heatmap's local-hour
    bucketing is deterministic under test. Empty history degrades to an honest
    one-liner (never a fabricated chart).
    """

    rows = list(records or [])
    if not rows:
        return [
            "No cross-session history yet.",
            "",
            _dim("History accrues one row per turn in stats-history.jsonl (agent dir)."),
        ]
    sessions = _latest_per_session(rows)
    projects = {_project_name(getattr(s, "cwd", "")) for s in sessions}
    return [
        f"Sessions      {_num(len(sessions))}",
        f"Projects      {_num(len(projects))}",
        "",
        "Per-project",
        *_project_table_lines(sessions),
        "",
        "Token trend (recent sessions, oldest → newest)",
        *_token_trend_lines(sessions),
        "",
        "Activity by hour (local, busier = darker)",
        *_heatmap_lines(rows, hour_of),
    ]


async def run_stats(
    *,
    stats_getter: Callable[[], Awaitable[Any]],
    snapshot: Any,
    tabbed: Callable[..., Awaitable[None]],
    commit: Callable[[object], None],
    history_getter: Callable[[], list[Any]] | None = None,
) -> None:
    """Drive the ``/stats`` dashboard end-to-end (WP-8, Feature 2).

    Module-level + dependency-injected (``stats_getter`` async accessor +
    pre-captured ``snapshot`` + ``tabbed`` modal + ``commit`` callables) so the
    whole flow is unit-testable without the prompt-toolkit app. ``shell.py`` wires
    the live ``harness.get_session_stats`` + ``tracker.snapshot()`` +
    ``AelixTUIContext.tabbed`` + output-committer into it.

    ``stats_getter`` is awaited and GUARDED: a failure commits a red line and
    returns (no half-built modal). On success the three tabs are opened via the
    shared tabbed viewer; each ``build_*_tab`` is bound LATE (the modal calls it
    per tab switch) so a single formatter raising is contained by ``tabbed``'s
    own per-render guard, never the REPL.
    """

    from rich.text import Text  # local import keeps this module import-light

    try:
        stats = await stats_getter()
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ stats unavailable: {exc}", style="bold red"))
        return

    tabs: list[tuple[str, Any]] = [
        ("Session", lambda: build_session_tab(stats, snapshot)),
        ("Activity", lambda: build_activity_tab(snapshot)),
        ("Efficiency", lambda: build_efficiency_tab(snapshot)),
    ]
    # The History tab is added ONLY when a history source is wired (D3). It is
    # bound LATE like the others, and the load itself is guarded so a corrupt
    # store degrades to the empty-history one-liner rather than failing the modal.
    if history_getter is not None:

        def _history() -> list[str]:
            try:
                records = history_getter()
            except Exception:  # noqa: BLE001 — a bad store never breaks the tab
                records = []
            return build_history_tab(records)

        tabs.append(("History", _history))

    try:
        await tabbed("Usage statistics", tabs)
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ stats viewer failed: {exc}", style="bold red"))
        return


__all__ = [
    "build_activity_tab",
    "build_efficiency_tab",
    "build_history_tab",
    "build_session_tab",
    "run_stats",
]
