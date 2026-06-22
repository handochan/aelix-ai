"""Unit tests for the /stats dashboard formatters + flow (WP-8, Feature 2).

The three ``build_*_tab`` formatters are driven over ``SimpleNamespace`` fixture
stats + snapshots (no harness / prompt-toolkit), and :func:`run_stats` is driven
with fake ``stats_getter`` / ``tabbed`` / ``commit`` callables.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from aelix_coding_agent.tui.stats_dashboard import (
    build_activity_tab,
    build_efficiency_tab,
    build_history_tab,
    build_session_tab,
    run_stats,
)

# Each per-session tab footer points at the (persisted) cross-session History tab.
_HISTORY_HINT = "History tab"


# -- fixtures ---------------------------------------------------------------


def _stats(**over: Any) -> SimpleNamespace:
    tokens = SimpleNamespace(
        input=over.pop("input", 12000),
        output=over.pop("output", 3400),
        cache_read=over.pop("cache_read", 8000),
        cache_write=over.pop("cache_write", 500),
        total=over.pop("total", 23900),
    )
    base = {
        "session_id": "s1",
        "user_messages": 4,
        "assistant_messages": 6,
        "tool_calls": 9,
        "tool_results": 9,
        "total_messages": 19,
        "tokens": tokens,
        "cost": 0.0421,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _model(model: str, **over: Any) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        requests=over.get("requests", 1),
        input=over.get("input", 0),
        output=over.get("output", 0),
        cache_read=over.get("cache_read", 0),
    )


def _tool(name: str, calls: int, failures: int, **over: Any) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        calls=calls,
        failures=failures,
        total_duration=over.get("total_duration", 0.0),
        timed_calls=over.get("timed_calls", 0),
    )


def _snapshot(**over: Any) -> SimpleNamespace:
    base = {
        "tool_calls": 9,
        "tool_failures": 2,
        "per_tool": [
            _tool("read", 5, 0),
            _tool("bash", 4, 2),
        ],
        "per_model": [
            _model("openai/gpt-4o", requests=3, input=12000, output=3400, cache_read=8000),
            _model("anthropic/claude", requests=1, input=2000, output=500, cache_read=0),
        ],
        "turns": 3,
        "wall_seconds": 185.0,
        # success_rate is a real property on ActivitySnapshot; fixtures supply a
        # concrete float so the formatter can be tested in isolation.
        "success_rate": (9 - 2) / 9,
    }
    base.update(over)
    return SimpleNamespace(**base)


# -- Session tab ------------------------------------------------------------


def test_session_tab_tool_calls_ok_fail_split() -> None:
    lines = build_session_tab(_stats(), _snapshot())
    body = "\n".join(lines)
    # 9 calls, 2 failures → 7 ok.
    assert "Tool calls    9  (✓ 7  ✗ 2)" in body


def test_session_tab_success_rate_percent() -> None:
    lines = build_session_tab(_stats(), _snapshot(success_rate=0.75))
    assert any("Success rate  75%" in ln for ln in lines)


def test_session_tab_tokens_cost_and_messages() -> None:
    lines = build_session_tab(_stats(), _snapshot())
    body = "\n".join(lines)
    assert "Tokens in     12,000" in body
    assert "Tokens out    3,400" in body
    assert "Cache read    8,000" in body
    assert "Cache write   500" in body
    assert "Cost          $0.0421" in body
    assert "Messages      19  (you 4 · assistant 6)" in body


def test_session_tab_wall_time_formatted() -> None:
    lines = build_session_tab(_stats(), _snapshot(wall_seconds=185.0))
    # 185s → 3m 05s. Labelled "Active time" (spans first→last event, not session
    # wall-clock — it does not advance while idle).
    assert any("Active time   3m 05s" in ln for ln in lines)


def test_session_tab_has_honest_no_history_footer() -> None:
    lines = build_session_tab(_stats(), _snapshot())
    assert any(_HISTORY_HINT in ln for ln in lines)
    # The note is rendered dim (mirrors the picker frame).
    assert any(_HISTORY_HINT in ln and "\x1b[2m" in ln for ln in lines)


def test_session_tab_success_rate_none_is_dash() -> None:
    lines = build_session_tab(_stats(), _snapshot(tool_calls=0, success_rate=None))
    assert any("Success rate  —" in ln for ln in lines)


def test_session_tab_shows_tool_latency() -> None:
    # WP-8 D4: 3.0s over 2 timed calls → 1.5s average.
    snap = _snapshot(per_tool=[_tool("bash", 2, 0, total_duration=3.0, timed_calls=2)])
    lines = build_session_tab(_stats(), snap)
    assert any("Tool latency  1.5s" in ln for ln in lines)


def test_session_tab_tool_latency_dash_when_untimed() -> None:
    lines = build_session_tab(_stats(), _snapshot())  # fixture tools are untimed
    assert any("Tool latency  —" in ln for ln in lines)


# -- Activity tab -----------------------------------------------------------


def test_activity_tab_lists_turns_and_per_model_table() -> None:
    lines = build_activity_tab(_snapshot())
    body = "\n".join(lines)
    assert "Turns         3" in body
    assert "Per-model usage" in body
    # Both models appear with their request counts + tokens.
    assert "openai/gpt-4o" in body
    assert "anthropic/claude" in body
    # gpt-4o row carries reqs=3 / in=12,000.
    gpt_row = next(ln for ln in lines if "openai/gpt-4o" in ln)
    assert "3" in gpt_row
    assert "12,000" in gpt_row


def test_activity_tab_empty_per_model_states_so() -> None:
    lines = build_activity_tab(_snapshot(per_model=[]))
    assert any("no model requests recorded" in ln for ln in lines)


def test_activity_tab_truncates_long_model_id() -> None:
    long_id = "very/long-provider-model-identifier-that-overflows-the-column"
    lines = build_activity_tab(_snapshot(per_model=[_model(long_id)]))
    row = next(ln for ln in lines if "…" in ln)
    assert "very/long-provider-model-id" in row


def test_activity_tab_has_honest_footer() -> None:
    lines = build_activity_tab(_snapshot())
    assert any(_HISTORY_HINT in ln for ln in lines)


# -- Efficiency tab ---------------------------------------------------------


def test_efficiency_tab_cache_hit_rate_math() -> None:
    # cache_read total = 8000; input total = 14000 → 8000/22000 ≈ 36%.
    lines = build_efficiency_tab(_snapshot())
    assert any("Cache-hit rate   36%" in ln for ln in lines)


def test_efficiency_tab_cache_hit_dash_when_no_tokens() -> None:
    lines = build_efficiency_tab(
        _snapshot(per_model=[_model("m", input=0, cache_read=0)])
    )
    assert any("Cache-hit rate   —" in ln for ln in lines)


def test_efficiency_tab_tool_success_percent() -> None:
    lines = build_efficiency_tab(_snapshot(success_rate=0.75))
    assert any("Tool success     75%" in ln for ln in lines)


def test_efficiency_tab_leaderboard_lists_tools_with_calls_and_fails() -> None:
    lines = build_efficiency_tab(_snapshot())
    body = "\n".join(lines)
    assert "Tool leaderboard" in body
    read_row = next(ln for ln in lines if ln.strip().startswith("read"))
    assert "5 calls · 0 fail" in read_row
    bash_row = next(ln for ln in lines if ln.strip().startswith("bash"))
    assert "4 calls · 2 fail" in bash_row
    # The success bar uses block glyphs.
    assert "█" in read_row


def test_efficiency_tab_empty_leaderboard_states_so() -> None:
    lines = build_efficiency_tab(_snapshot(per_tool=[]))
    assert any("no tool calls recorded" in ln for ln in lines)


def test_efficiency_tab_has_honest_footer() -> None:
    lines = build_efficiency_tab(_snapshot())
    assert any(_HISTORY_HINT in ln for ln in lines)


def test_efficiency_tab_summary_and_per_tool_latency() -> None:
    # WP-8 D4: 8.0s over 4 timed calls → 2.0s, shown both as the summary line
    # and on the per-tool leaderboard row.
    snap = _snapshot(per_tool=[_tool("bash", 4, 1, total_duration=8.0, timed_calls=4)])
    lines = build_efficiency_tab(snap)
    body = "\n".join(lines)
    assert "Tool latency     2.0s" in body
    bash_row = next(ln for ln in lines if ln.strip().startswith("bash"))
    assert "2.0s" in bash_row


def test_efficiency_tab_latency_dash_when_untimed() -> None:
    lines = build_efficiency_tab(_snapshot())  # fixture tools are untimed
    assert any("Tool latency     —" in ln for ln in lines)
    # Each untimed leaderboard row reports — for its latency column too.
    read_row = next(ln for ln in lines if ln.strip().startswith("read"))
    assert read_row.rstrip().endswith("—")


def test_efficiency_tab_sub_second_latency_is_ms() -> None:
    snap = _snapshot(per_tool=[_tool("read", 1, 0, total_duration=0.84, timed_calls=1)])
    lines = build_efficiency_tab(snap)
    assert any("840ms" in ln for ln in lines)


# -- robustness of the formatters ------------------------------------------


def test_formatters_degrade_on_sparse_objects() -> None:
    # Empty namespaces (every field absent) must not raise — getattr defaults.
    empty_stats = SimpleNamespace()
    empty_snap = SimpleNamespace()
    assert isinstance(build_session_tab(empty_stats, empty_snap), list)
    assert isinstance(build_activity_tab(empty_snap), list)
    assert isinstance(build_efficiency_tab(empty_snap), list)


def test_all_tabs_return_list_of_str() -> None:
    for tab in (
        build_session_tab(_stats(), _snapshot()),
        build_activity_tab(_snapshot()),
        build_efficiency_tab(_snapshot()),
    ):
        assert isinstance(tab, list)
        assert all(isinstance(ln, str) for ln in tab)


# -- run_stats flow ---------------------------------------------------------


def _plain(renderable: object) -> str:
    return getattr(renderable, "plain", str(renderable))


async def test_run_stats_opens_three_tabs() -> None:
    captured: dict[str, Any] = {}

    async def stats_getter() -> SimpleNamespace:
        return _stats()

    async def tabbed(title: str, tabs: list[tuple[str, Any]]) -> None:
        captured["title"] = title
        captured["tab_names"] = [name for name, _ in tabs]
        # Exercise each render callback (mirrors how the modal renders a tab).
        captured["rendered"] = {name: render() for name, render in tabs}

    await run_stats(
        stats_getter=stats_getter,
        snapshot=_snapshot(),
        tabbed=tabbed,
        commit=lambda _c: None,
    )

    assert captured["title"] == "Usage statistics"
    assert captured["tab_names"] == ["Session", "Activity", "Efficiency"]
    # Each tab rendered to a non-empty list of strings.
    for name in ("Session", "Activity", "Efficiency"):
        rendered = captured["rendered"][name]
        assert isinstance(rendered, list) and rendered
        assert all(isinstance(ln, str) for ln in rendered)


async def test_run_stats_degrades_when_getter_raises() -> None:
    committed: list[object] = []
    opened: list[int] = []

    async def stats_getter() -> SimpleNamespace:
        raise RuntimeError("boom")

    async def tabbed(title: str, tabs: list[tuple[str, Any]]) -> None:
        opened.append(1)  # must NOT be reached

    await run_stats(
        stats_getter=stats_getter,
        snapshot=_snapshot(),
        tabbed=tabbed,
        commit=committed.append,
    )
    # No modal opened; a red error line was committed.
    assert opened == []
    assert any("stats unavailable" in _plain(c) for c in committed)


async def test_run_stats_degrades_when_tabbed_raises() -> None:
    committed: list[object] = []

    async def stats_getter() -> SimpleNamespace:
        return _stats()

    async def tabbed(title: str, tabs: list[tuple[str, Any]]) -> None:
        raise RuntimeError("modal exploded")

    await run_stats(
        stats_getter=stats_getter,
        snapshot=_snapshot(),
        tabbed=tabbed,
        commit=committed.append,
    )
    assert any("stats viewer failed" in _plain(c) for c in committed)


# === History tab (WP-8 D3) =================================================


def _hrec(**over: Any) -> SimpleNamespace:
    base = {
        "ts": 1000.0,
        "session_id": "s1",
        "cwd": "/work/alpha",
        "model": "m",
        "turns": 1,
        "tool_calls": 2,
        "tool_failures": 0,
        "input": 1000,
        "output": 400,
        "cache_read": 100,
        "cost": 0.01,
        "tool_seconds": 1.0,
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_history_tab_empty_is_honest() -> None:
    body = "\n".join(build_history_tab([]))
    assert "No cross-session history yet." in body
    assert "stats-history.jsonl" in body


def test_history_tab_collapses_latest_per_session_for_project_table() -> None:
    # s1 has two CUMULATIVE rows; the latest (3,900 tokens) is what the project
    # table must reflect — NOT the sum of both rows.
    records = [
        _hrec(session_id="s1", cwd="/work/alpha", ts=1.0, input=1000, output=400, cost=0.01, tool_calls=3),
        _hrec(session_id="s1", cwd="/work/alpha", ts=2.0, input=3000, output=900, cost=0.04, tool_calls=7),
        _hrec(session_id="s2", cwd="/work/beta", ts=3.0, input=500, output=200, cost=0.005, tool_calls=2),
    ]
    lines = build_history_tab(records, hour_of=lambda ts: 0)
    body = "\n".join(lines)
    assert "Sessions      2" in body
    assert "Projects      2" in body
    alpha = next(ln for ln in lines if ln.strip().startswith("alpha"))
    assert "3,900" in alpha  # latest cumulative row, not 1,400 + 3,900
    assert "$0.0400" in alpha  # latest cost, not summed
    beta = next(ln for ln in lines if ln.strip().startswith("beta"))
    assert "700" in beta


def test_history_tab_project_table_sorted_busiest_first() -> None:
    records = [
        _hrec(session_id="a", cwd="/p/small", input=10, output=0),
        _hrec(session_id="b", cwd="/p/big", input=9000, output=0),
    ]
    lines = build_history_tab(records, hour_of=lambda ts: 0)
    rows = [ln for ln in lines if ln.strip().startswith(("small", "big"))]
    assert rows[0].strip().startswith("big")  # more tokens first


def test_history_tab_project_table_bounds_large_values() -> None:
    # 6h₃₃ review (ux LOW-1): a cross-session cumulative aggregate can reach 100M+
    # tokens and >$1000 cost; the columns must abbreviate AND keep a leading-space
    # gap so they never fuse into an unreadable digit-wall.
    records = [
        _hrec(session_id="s1", cwd="/work/huge", input=900_000_000, output=123_456_789, cost=1234.5678, tool_calls=54210),
    ]
    lines = build_history_tab(records, hour_of=lambda ts: 0)
    row = next(ln for ln in lines if ln.strip().startswith("huge"))
    assert "1023.5M" in row  # tokens abbreviated to millions
    assert "$1.2k" in row  # cost abbreviated to k-notation
    assert " $1.2k" in row  # a gap precedes the cost column (no fusion)
    assert " 54,210" in row  # tool calls stay separated too


def test_history_tab_project_table_discloses_top_10_cap() -> None:
    records = [
        _hrec(session_id=f"s{i}", cwd=f"/work/proj{i:02d}", input=1000 - i)
        for i in range(15)
    ]
    body = "\n".join(build_history_tab(records, hour_of=lambda ts: 0))
    assert "5 more project(s)" in body  # the cap is disclosed, not silent


def test_history_tab_token_trend_has_bars() -> None:
    records = [_hrec(session_id=f"s{i}", ts=float(i), input=100 * i, output=0) for i in range(1, 4)]
    body = "\n".join(build_history_tab(records, hour_of=lambda ts: 0))
    assert "Token trend" in body
    assert "█" in body  # the busiest session fills its bar


def test_history_tab_heatmap_buckets_by_hour() -> None:
    # Two events at hour 9, one at hour 15 → 06–11 block peaks, 12–17 is lighter.
    records = [_hrec(session_id="a", ts=1.0), _hrec(session_id="b", ts=2.0), _hrec(session_id="c", ts=3.0)]
    hours = {1.0: 9, 2.0: 9, 3.0: 15}
    lines = build_history_tab(records, hour_of=lambda ts: hours[ts])
    block_0611 = next(ln for ln in lines if ln.strip().startswith("06"))
    block_1217 = next(ln for ln in lines if ln.strip().startswith("12"))
    assert "█" in block_0611  # peak (2 events) → full block
    assert "█" not in block_1217  # 1 event → lighter than the peak


async def test_run_stats_adds_history_tab_when_getter_provided() -> None:
    captured: dict[str, Any] = {}

    async def stats_getter() -> SimpleNamespace:
        return _stats()

    async def tabbed(title: str, tabs: list[tuple[str, Any]]) -> None:
        captured["names"] = [n for n, _ in tabs]
        captured["rendered"] = {n: render() for n, render in tabs}

    await run_stats(
        stats_getter=stats_getter,
        snapshot=_snapshot(),
        tabbed=tabbed,
        commit=lambda _c: None,
        history_getter=lambda: [_hrec(session_id="s1")],
    )
    assert captured["names"] == ["Session", "Activity", "Efficiency", "History"]
    assert captured["rendered"]["History"]  # non-empty


async def test_run_stats_history_getter_failure_degrades_to_empty_tab() -> None:
    captured: dict[str, Any] = {}

    async def stats_getter() -> SimpleNamespace:
        return _stats()

    async def tabbed(title: str, tabs: list[tuple[str, Any]]) -> None:
        captured["rendered"] = {n: render() for n, render in tabs}

    def boom() -> list[Any]:
        raise RuntimeError("store exploded")

    await run_stats(
        stats_getter=stats_getter,
        snapshot=_snapshot(),
        tabbed=tabbed,
        commit=lambda _c: None,
        history_getter=boom,
    )
    # The History tab still renders (the empty one-liner), not a crash.
    body = "\n".join(captured["rendered"]["History"])
    assert "No cross-session history yet." in body
