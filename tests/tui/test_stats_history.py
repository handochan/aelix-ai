"""Unit tests for the cross-session /stats history store (WP-8 D3, ADR-0168).

The store is driven over an explicit tmp path + a scripted clock, so no agent-dir
/ wall-clock dependency is needed.
"""

from __future__ import annotations

from pathlib import Path

from aelix_coding_agent.tui.stats_history import StatsHistoryRecord, StatsHistoryStore


class _Clock:
    """Deterministic wall clock — each call returns the next scripted tick."""

    def __init__(self, ticks: list[float]) -> None:
        self._ticks = list(ticks)
        self._last = 0.0

    def __call__(self) -> float:
        if self._ticks:
            self._last = self._ticks.pop(0)
        return self._last


def _store(tmp_path: Path, ticks: list[float] | None = None) -> StatsHistoryStore:
    clock = _Clock(ticks or [100.0, 200.0, 300.0, 400.0])
    return StatsHistoryStore(tmp_path / "stats-history.jsonl", clock=clock)


def _fields(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "session_id": "s1",
        "cwd": "/work/proj",
        "model": "openai/gpt-4o",
        "turns": 1,
        "tool_calls": 2,
        "tool_failures": 0,
        "input": 100,
        "output": 40,
        "cache_read": 10,
        "cost": 0.01,
        "tool_seconds": 1.5,
    }
    base.update(over)
    return base


# -- append / load round-trip ----------------------------------------------


def test_append_then_load_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_fields(turns=1))
    store.append(_fields(turns=2, input=500))
    records = store.load()
    assert len(records) == 2
    assert all(isinstance(r, StatsHistoryRecord) for r in records)
    assert records[0].turns == 1 and records[1].turns == 2
    assert records[1].input == 500


def test_append_stamps_ts_from_clock(tmp_path: Path) -> None:
    store = _store(tmp_path, ticks=[111.0, 222.0])
    store.append(_fields())
    store.append(_fields())
    records = store.load()
    assert [r.ts for r in records] == [111.0, 222.0]


def test_append_keeps_explicit_ts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_fields(ts=999.0))
    assert store.load()[0].ts == 999.0


def test_record_tokens_property(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_fields(input=300, output=120, cache_read=70))
    # tokens = input + output (throughput; excludes cache reads).
    assert store.load()[0].tokens == 420


# -- tolerant load ----------------------------------------------------------


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert StatsHistoryStore(tmp_path / "nope.jsonl").load() == []


def test_load_skips_corrupt_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "stats-history.jsonl"
    path.write_text(
        '{"session_id": "a", "input": 5}\n'
        "not json at all\n"
        "\n"
        "[1, 2, 3]\n"  # valid json but not a dict → skipped
        '{"session_id": "b", "input": 9}\n',
        encoding="utf-8",
    )
    records = StatsHistoryStore(path).load()
    assert [r.session_id for r in records] == ["a", "b"]
    assert [r.input for r in records] == [5, 9]


def test_load_coerces_bad_field_type_by_skipping_row(tmp_path: Path) -> None:
    path = tmp_path / "stats-history.jsonl"
    # input is a non-numeric string → int(...) raises → row skipped, not the read.
    path.write_text(
        '{"session_id": "a", "input": "abc"}\n{"session_id": "b", "input": 7}\n',
        encoding="utf-8",
    )
    records = StatsHistoryStore(path).load()
    assert [r.session_id for r in records] == ["b"]


def test_load_limit_keeps_last_n(tmp_path: Path) -> None:
    store = _store(tmp_path, ticks=[float(i) for i in range(10)])
    for i in range(6):
        store.append(_fields(turns=i))
    last_two = store.load(limit=2)
    assert [r.turns for r in last_two] == [4, 5]


# -- prune ------------------------------------------------------------------


def test_prune_keeps_last_n(tmp_path: Path) -> None:
    store = _store(tmp_path, ticks=[float(i) for i in range(10)])
    for i in range(5):
        store.append(_fields(turns=i))
    store.prune(2)
    records = store.load()
    assert [r.turns for r in records] == [3, 4]


def test_prune_noop_when_within_keep(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_fields(turns=1))
    store.append(_fields(turns=2))
    store.prune(10)  # already within keep → unchanged
    assert [r.turns for r in store.load()] == [1, 2]


def test_prune_missing_file_is_noop(tmp_path: Path) -> None:
    store = StatsHistoryStore(tmp_path / "nope.jsonl")
    store.prune(10)  # must not raise
    assert store.load() == []


def test_prune_zero_truncates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_fields())
    store.prune(0)
    assert store.load() == []


# -- never raises on the hot path ------------------------------------------


def test_append_never_raises_on_unwritable_path(tmp_path: Path) -> None:
    # Point at a path whose parent is a FILE (mkdir will fail) — append swallows.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    store = StatsHistoryStore(blocker / "sub" / "stats-history.jsonl")
    store.append(_fields())  # must not raise
    assert store.load() == []  # and nothing was written
