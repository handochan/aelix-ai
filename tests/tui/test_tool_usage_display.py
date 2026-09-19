"""#199 / ADR-0243 — the one line ``/cost`` and the ``/stats`` session tab add for
usage that tools reported (``SessionStats.tool_usage``).

The totals above that line already include it (the kernel folds the
``aelix.usage`` records into ``tokens``/``cost``), so the line is a breakdown:
it appears only when a tool reported a run, it carries its own cost through
:func:`format_session_cost` (so a pending or unpriced run reads ``≥ $X`` /
``n/a``, never a confident figure), and nothing adds it to anything.

Rendering is pinned on the produced TEXT here; rule 9's live TUI check is the
lead's, after integration.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness._session_stats import (
    USAGE_RECORD_TYPE,
    SessionStatsTokens,
    ToolUsage,
    aggregate_session_stats,
)
from aelix_coding_agent.tui.commands import BUILTIN_COMMANDS, CommandContext, match_command
from aelix_coding_agent.tui.stats_dashboard import (
    TOOL_USAGE_LABEL,
    UNPRICED_COST,
    build_session_tab,
    format_tool_usage,
)
from rich.console import Console

# -- fixtures ------------------------------------------------------------------


def _usage(
    *,
    tokens_in: int = 12_300,
    tokens_out: int = 1_100,
    cost: float = 0.011,
    cost_known: bool = True,
    runs: int = 2,
    pending: int = 0,
) -> ToolUsage:
    return ToolUsage(
        tokens=SessionStatsTokens(
            input=tokens_in, output=tokens_out, total=tokens_in + tokens_out
        ),
        cost=cost,
        cost_known=cost_known,
        runs=runs,
        pending=pending,
    )


def _stats(tool_usage: ToolUsage | None = None, **over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "session_id": "s1",
        "user_messages": 4,
        "assistant_messages": 6,
        "total_messages": 19,
        "tokens": SimpleNamespace(
            input=24_300, output=4_500, cache_read=8_000, cache_write=500, total=37_300
        ),
        "cost": 0.0531,
        "cost_known": True,
    }
    if tool_usage is not None:
        base["tool_usage"] = tool_usage
    base.update(over)
    return SimpleNamespace(**base)


def _render(renderable: object, width: int = 100) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True).print(renderable)
    return buffer.getvalue()


async def _run_cost(harness: object, *, width: int = 100) -> str:
    committed: list[object] = []
    command = match_command("/cost", BUILTIN_COMMANDS)
    assert command is not None and command.handler is not None
    ctx = CommandContext(
        chrome=object(),  # type: ignore[arg-type]
        harness=harness,  # type: ignore[arg-type]
        commit=committed.append,
        cwd="/w",
        commands=list(BUILTIN_COMMANDS),
    )
    await command.handler(ctx, "")
    return "".join(_render(c, width=width) for c in committed)


class _StatsHarness:
    def __init__(self, stats: object) -> None:
        self._stats = stats

    async def get_session_stats(self) -> object:
        return self._stats


# === format_tool_usage =========================================================


def test_the_line_reads_as_the_design_example() -> None:
    assert format_tool_usage(_stats(_usage())) == "12.3k in / 1.1k out · $0.0110 (2 runs)"
    assert TOOL_USAGE_LABEL == "Tools & delegated agents"


def test_a_pending_run_is_counted_and_the_cost_reads_as_a_floor() -> None:
    line = format_tool_usage(_stats(_usage(cost_known=False, pending=1)))
    assert line == "12.3k in / 1.1k out · ≥ $0.0110 (2 runs, 1 pending)"


def test_nothing_settled_yet_reads_as_no_price_not_as_free() -> None:
    line = format_tool_usage(
        _stats(_usage(tokens_in=0, tokens_out=0, cost=0.0, cost_known=False, runs=1, pending=1))
    )
    assert line == f"0 in / 0 out · {UNPRICED_COST} (1 run, 1 pending)"
    assert "$0.0000" not in line


def test_an_unpriced_settled_run_never_reads_as_a_zero_bill() -> None:
    line = format_tool_usage(_stats(_usage(cost=0.0, cost_known=False, runs=1)))
    assert line == f"12.3k in / 1.1k out · {UNPRICED_COST} (1 run)"


@pytest.mark.parametrize(
    ("count", "spelled"),
    [(0, "0"), (999, "999"), (1_000, "1.0k"), (12_345, "12.3k"), (1_234_567, "1.2M")],
)
def test_token_counts_are_compact(count: int, spelled: str) -> None:
    line = format_tool_usage(_stats(_usage(tokens_in=count, tokens_out=count)))
    assert line is not None
    assert line.startswith(f"{spelled} in / {spelled} out · ")


def test_no_line_without_a_reported_run() -> None:
    assert format_tool_usage(_stats()) is None  # an embedder's stats: no attribute
    assert format_tool_usage(_stats(ToolUsage())) is None  # nothing reported
    assert format_tool_usage(SimpleNamespace()) is None  # a sparse fake


# === /stats — the session tab ===================================================


def test_the_session_tab_adds_one_line_right_under_the_cost() -> None:
    lines = build_session_tab(_stats(_usage()), None)
    cost_at = next(i for i, ln in enumerate(lines) if ln.startswith("Cost "))
    assert lines[cost_at + 1] == (
        "Tools & delegated agents: 12.3k in / 1.1k out · $0.0110 (2 runs)"
    )
    assert sum(TOOL_USAGE_LABEL in ln for ln in lines) == 1
    # The session cost above is the merged total, untouched by the breakdown.
    assert lines[cost_at] == "Cost          $0.0531"


def test_the_session_tab_is_unchanged_when_no_tool_reported() -> None:
    plain = build_session_tab(_stats(), None)
    empty = build_session_tab(_stats(ToolUsage()), None)
    assert plain == empty
    assert not any(TOOL_USAGE_LABEL in ln for ln in plain)
    with_line = build_session_tab(_stats(_usage()), None)
    assert len(with_line) == len(plain) + 1


def test_the_session_tab_shows_the_pending_count() -> None:
    lines = build_session_tab(_stats(_usage(cost_known=False, pending=1), cost_known=False), None)
    tool_lines = [ln for ln in lines if ln.startswith(TOOL_USAGE_LABEL)]
    assert tool_lines == [
        "Tools & delegated agents: 12.3k in / 1.1k out · ≥ $0.0110 (2 runs, 1 pending)"
    ]


# === /cost ========================================================================


async def test_cost_adds_one_row_after_the_cost_row() -> None:
    out = await _run_cost(_StatsHarness(_stats(_usage())))
    rows = [ln.strip("│ ").rstrip() for ln in out.splitlines()]
    cost_at = next(i for i, ln in enumerate(rows) if ln.startswith("cost (USD)"))
    # Whitespace-normalised: the grid pads the label column to its widest label.
    assert " ".join(rows[cost_at + 1].split()) == (
        "tools & agents 12.3k in / 1.1k out · 0.0110 (2 runs)"
    )
    assert out.count("tools & agents") == 1
    # Every existing row is still there, with its own figure.
    for label, value in (
        ("messages", "19"),
        ("input tokens", "24300"),
        ("output tokens", "4500"),
        ("total tokens", "37300"),
        ("cost (USD)", "0.0531"),
    ):
        assert any(r.split() == [*label.split(), value] for r in rows), (label, rows)


async def test_cost_has_no_extra_row_when_no_tool_reported() -> None:
    out = await _run_cost(_StatsHarness(_stats()))
    assert "tools & agents" not in out
    assert "0.0531" in out


async def test_cost_shows_the_pending_run_and_a_floor() -> None:
    out = await _run_cost(
        _StatsHarness(_stats(_usage(cost_known=False, pending=1), cost_known=False))
    )
    assert "≥ 0.0110 (2 runs, 1 pending)" in out
    assert "≥ 0.0531" in out  # the session cost is a floor too


async def test_the_widest_cost_row_fits_an_80_column_terminal() -> None:
    """A representative wide row — twelve runs, all pending, six-figure token
    counts and a floor at once — must not wrap inside the ``/cost`` panel. Not
    a bound on every value: bigger counts can still wrap (Codex review nit)."""

    out = await _run_cost(
        _StatsHarness(
            _stats(
                _usage(cost_known=False, pending=12, runs=12, tokens_in=987_654, tokens_out=987_654),
                cost_known=False,
            )
        ),
        width=80,
    )
    rows = [ln for ln in out.splitlines() if "tools & agents" in ln]
    assert len(rows) == 1, out
    # The whole value on the label's own line: nothing wrapped onto the next one.
    assert "987.7k in / 987.7k out · ≥ 0.0110 (12 runs, 12 pending)" in rows[0], out
    assert all(len(line) <= 80 for line in out.splitlines()), out


# === the real pipeline: kernel fold → display ======================================


def _records() -> list[dict[str, Any]]:
    return [
        {"v": 1, "key": "sub-000000000001", "state": "pending"},
        {
            "v": 1,
            "key": "sub-000000000001",
            "state": "final",
            "usage": {
                "input": 12_000,
                "output": 900,
                "cache_read": 0,
                "cache_write": 0,
                "cost": 0.0081,
            },
            "cost_known": True,
        },
        {"v": 1, "key": "sub-000000000002", "state": "pending"},
        {
            "v": 1,
            "key": "sub-000000000002",
            "state": "final",
            "usage": {
                "input": 300,
                "output": 200,
                "cache_read": 0,
                "cache_write": 0,
                "cost": 0.0029,
            },
            "cost_known": True,
        },
    ]


def test_real_stats_render_the_merged_total_and_the_breakdown() -> None:
    stats = aggregate_session_stats("s", [], usage_records=_records())
    lines = build_session_tab(stats, None)
    assert "Tokens in     12,300" in lines
    assert "Cost          $0.0110" in lines
    assert "Tools & delegated agents: 12.3k in / 1.1k out · $0.0110 (2 runs)" in lines


async def test_cost_through_a_real_harness_session() -> None:
    """``/cost`` → ``AgentHarness.get_session_stats`` → the branch's records."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.session import MemorySessionStorage, Session

    session = Session(MemorySessionStorage())
    for record in _records():
        await session.append_custom_entry(USAGE_RECORD_TYPE, record)
    # A third run that never settled: its pending line is the last word on it.
    await session.append_custom_entry(
        USAGE_RECORD_TYPE, {"v": 1, "key": "sub-000000000003", "state": "pending"}
    )

    async def _no_stream(*_a: Any, **_k: Any) -> Any:  # never called: no prompt runs
        raise AssertionError("no turn runs in this test")

    harness = AgentHarness(AgentHarnessOptions(stream_fn=_no_stream, session=session))
    try:
        out = await _run_cost(harness)
    finally:
        await harness.dispose()
    assert "tools & agents" in out
    assert "12.3k in / 1.1k out · ≥ 0.0110 (3 runs, 1 pending)" in out
    assert "≥ 0.0110" in out  # the third run never settled: the total is a floor
