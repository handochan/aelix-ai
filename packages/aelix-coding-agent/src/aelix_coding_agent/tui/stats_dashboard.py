"""Pure formatters + DI flow for the ``/stats`` usage dashboard (WP-8, Feature 2).

The harness :class:`aelix_agent_core.harness._session_stats.SessionStats`
(``await get_session_stats()``) carries aggregate counts/tokens/cost; the
TUI-side :class:`aelix_coding_agent.tui.activity_tracker.SessionActivityTracker`
(its :class:`ActivitySnapshot`) adds the per-tool success/failure split, the
per-model breakdown, the turn count, and wall-clock timing the harness does NOT
retain. This module turns those two read-only inputs into three tab bodies and
drives the framed tabbed viewer.

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
- **Honesty.** Cross-session heatmaps / trend lines are NOT available (no history
  is retained) — every tab states the gap on a dim footer line rather than
  silently implying the data exists.

The dim footer escape codes mirror ``context.py`` (``_PICK_DIM`` / ``_PICK_RST``)
so a note renders dim inside the framed modal; they are duplicated here as small
local constants rather than importing ``context.py`` privates (this module stays
a leaf consumer that never reaches into a shared file).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Dim / reset escapes — mirror context.py:71/73 so a footer note renders dim
# inside the framed tabbed modal (duplicated, not imported, to keep this a leaf).
_DIM = "\x1b[2m"
_RST = "\x1b[0m"

# Width of the small inline composition / leaderboard bars.
_BAR_WIDTH = 16

# Honest disclaimer: the data this dashboard CANNOT show (no history retained).
_NO_HISTORY_NOTE = (
    "Cross-session heatmap / trend omitted — only the live session is tracked "
    "(no history retained)."
)


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

    lines = [
        f"Tool calls    {_num(calls)}  (✓ {_num(ok)}  ✗ {_num(failures)})",
        f"Success rate  {success}",
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
        _dim(_NO_HISTORY_NOTE),
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
    lines.extend(["", _dim(_NO_HISTORY_NOTE)])
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

    lines = [
        f"Cache-hit rate   {cache_hit}",
        _dim("  (assumes uncached input; gross-input providers under-report)"),
        f"Tool success     {success}",
        "",
        "Tool leaderboard",
    ]
    per_tool = list(getattr(snapshot, "per_tool", []) or [])
    if not per_tool:
        lines.append("  (no tool calls recorded yet)")
    else:
        for stat in per_tool:
            name = str(getattr(stat, "name", "?") or "?")
            calls = int(getattr(stat, "calls", 0) or 0)
            failures = int(getattr(stat, "failures", 0) or 0)
            ok = max(0, calls - failures)
            frac = (ok / calls) if calls > 0 else 0.0
            lines.append(
                f"  {name:<18}{_bar(frac)}  "
                f"{_num(calls)} calls · {_num(failures)} fail"
            )
    lines.extend(["", _dim(_NO_HISTORY_NOTE)])
    return lines


async def run_stats(
    *,
    stats_getter: Callable[[], Awaitable[Any]],
    snapshot: Any,
    tabbed: Callable[..., Awaitable[None]],
    commit: Callable[[object], None],
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

    try:
        await tabbed(
            "Usage statistics",
            [
                ("Session", lambda: build_session_tab(stats, snapshot)),
                ("Activity", lambda: build_activity_tab(snapshot)),
                ("Efficiency", lambda: build_efficiency_tab(snapshot)),
            ],
        )
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ stats viewer failed: {exc}", style="bold red"))
        return


__all__ = [
    "build_activity_tab",
    "build_efficiency_tab",
    "build_session_tab",
    "run_stats",
]
