"""Unit tests for the session activity tracker (WP-8, Feature 2).

Scripted ``SimpleNamespace`` events + a fake clock drive the pure tracker, so no
harness / prompt-toolkit / wall-clock dependency is needed.
"""

from __future__ import annotations

from types import SimpleNamespace

from aelix_coding_agent.tui.activity_tracker import (
    ActivitySnapshot,
    ModelStat,
    SessionActivityTracker,
    ToolStat,
)


class _FakeClock:
    """Deterministic monotonic clock — each call returns the next scripted tick."""

    def __init__(self, ticks: list[float]) -> None:
        self._ticks = list(ticks)
        self._last = 0.0

    def __call__(self) -> float:
        if self._ticks:
            self._last = self._ticks.pop(0)
        return self._last


def _tool_start(name: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_execution_start", tool_name=name, args={})


def _tool_end(name: str, *, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_execution_end", tool_name=name, result="ok", is_error=is_error
    )


def _tool_start_id(name: str, tcid: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_execution_start", tool_name=name, tool_call_id=tcid, args={}
    )


def _tool_end_id(name: str, tcid: str, *, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_execution_end",
        tool_name=name,
        tool_call_id=tcid,
        result="ok",
        is_error=is_error,
    )


def _message_end(
    *, model: str | None = None, usage: object = None, role: str = "assistant"
) -> SimpleNamespace:
    # Real assistant responses carry ``role="assistant"`` (messages.py:118); the
    # tracker only counts those toward per-model usage (the loop also emits
    # message_end for user/tool-result messages, which must NOT inflate reqs).
    message = SimpleNamespace(model=model, usage=usage, role=role)
    return SimpleNamespace(type="message_end", message=message)


def _turn_end() -> SimpleNamespace:
    return SimpleNamespace(type="turn_end")


# -- tool counts / failures / success rate ---------------------------------


def test_per_tool_counts_and_failures() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(_tool_start("read"))
    tracker.on_event(_tool_end("read"))
    tracker.on_event(_tool_end("read"))
    tracker.on_event(_tool_end("bash", is_error=True))
    tracker.on_event(_tool_end("bash"))

    snap = tracker.snapshot()
    assert isinstance(snap, ActivitySnapshot)
    assert snap.tool_calls == 4
    assert snap.tool_failures == 1
    # Busiest first: read (2) before bash (2 too) → tie broken by name.
    by_name = {t.name: t for t in snap.per_tool}
    assert by_name["read"] == ToolStat(name="read", calls=2, failures=0)
    assert by_name["bash"] == ToolStat(name="bash", calls=2, failures=1)


def test_per_tool_sorted_busiest_first_then_name() -> None:
    tracker = SessionActivityTracker()
    for _ in range(3):
        tracker.on_event(_tool_end("grep"))
    tracker.on_event(_tool_end("edit"))
    tracker.on_event(_tool_end("apply"))

    names = [t.name for t in tracker.snapshot().per_tool]
    # grep (3 calls) first; remaining single-call tools tie → alphabetical.
    assert names == ["grep", "apply", "edit"]


def test_success_rate_none_before_any_tool_call() -> None:
    tracker = SessionActivityTracker()
    assert tracker.snapshot().success_rate is None
    # turn/message events alone must not produce a misleading 0%.
    tracker.on_event(_turn_end())
    tracker.on_event(_message_end(model="m"))
    assert tracker.snapshot().success_rate is None


def test_success_rate_math() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(_tool_end("a"))  # ok
    tracker.on_event(_tool_end("a"))  # ok
    tracker.on_event(_tool_end("a"))  # ok
    tracker.on_event(_tool_end("b", is_error=True))  # fail
    snap = tracker.snapshot()
    assert snap.success_rate == 0.75


def test_all_failures_success_rate_zero() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(_tool_end("x", is_error=True))
    tracker.on_event(_tool_end("x", is_error=True))
    assert tracker.snapshot().success_rate == 0.0


def test_tool_end_missing_name_falls_back() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(SimpleNamespace(type="tool_execution_end", is_error=False))
    snap = tracker.snapshot()
    assert snap.tool_calls == 1
    assert snap.per_tool[0].name == "(unknown)"


# -- per-tool latency (WP-8 D4): start↔end pairing by tool_call_id ----------


def test_per_tool_latency_paired_by_call_id() -> None:
    # Two bash calls: 0.5s then 2.0s. The fake clock pops one tick per event
    # (one _stamp per on_event), so 4 events consume exactly 4 ticks.
    clock = _FakeClock([100.0, 100.5, 200.0, 202.0])
    tracker = SessionActivityTracker(clock=clock)
    tracker.on_event(_tool_start_id("bash", "c1"))  # 100.0
    tracker.on_event(_tool_end_id("bash", "c1"))  # 100.5 → 0.5s
    tracker.on_event(_tool_start_id("bash", "c2"))  # 200.0
    tracker.on_event(_tool_end_id("bash", "c2"))  # 202.0 → 2.0s

    stat = tracker.snapshot().per_tool[0]
    assert stat.name == "bash"
    assert stat.calls == 2
    assert stat.timed_calls == 2
    assert stat.total_duration == 2.5
    assert stat.avg_duration == 1.25


def test_tool_end_without_start_is_counted_but_not_timed() -> None:
    # An end whose start was never seen (replay / missed start) still counts as a
    # call but contributes no latency — avg_duration stays None.
    tracker = SessionActivityTracker()
    tracker.on_event(_tool_end_id("read", "x1"))
    stat = tracker.snapshot().per_tool[0]
    assert stat.calls == 1
    assert stat.timed_calls == 0
    assert stat.avg_duration is None
    assert stat.total_duration == 0.0


def test_avg_tool_seconds_aggregates_timed_calls_only() -> None:
    clock = _FakeClock([0.0, 1.0, 10.0, 13.0])
    tracker = SessionActivityTracker(clock=clock)
    tracker.on_event(_tool_start_id("a", "1"))
    tracker.on_event(_tool_end_id("a", "1"))  # 1.0s
    tracker.on_event(_tool_start_id("b", "2"))
    tracker.on_event(_tool_end_id("b", "2"))  # 3.0s
    # (1.0 + 3.0) / 2 timed calls = 2.0s, independent of any untimed calls.
    assert tracker.snapshot().avg_tool_seconds == 2.0


def test_avg_tool_seconds_none_before_any_timed_call() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(_tool_end("read"))  # counted, but untimed (no start)
    assert tracker.snapshot().avg_tool_seconds is None


def test_reset_clears_pending_starts() -> None:
    # A start before reset must not pair against an end after reset (no stale
    # duration bleeding into the new session).
    clock = _FakeClock([5.0, 99.0])
    tracker = SessionActivityTracker(clock=clock)
    tracker.on_event(_tool_start_id("bash", "c1"))  # pending @5.0
    tracker.reset()
    tracker.on_event(_tool_end_id("bash", "c1"))  # @99.0, no matching start
    stat = tracker.snapshot().per_tool[0]
    assert stat.calls == 1
    assert stat.timed_calls == 0
    assert stat.avg_duration is None


# -- per-model token accumulation ------------------------------------------


def test_per_model_from_message_model_attr_with_usage_dataclass() -> None:
    tracker = SessionActivityTracker()
    usage = SimpleNamespace(input=100, output=40, cache_read=10, cache_write=5)
    tracker.on_event(_message_end(model="openai/gpt-4o", usage=usage))
    tracker.on_event(_message_end(model="openai/gpt-4o", usage=usage))

    per_model = tracker.snapshot().per_model
    assert per_model == [
        ModelStat(
            model="openai/gpt-4o",
            requests=2,
            input=200,
            output=80,
            cache_read=20,
        )
    ]


def test_per_model_usage_dict_shape() -> None:
    """`_read` must handle dict-shape usage payloads (provider passthrough)."""

    tracker = SessionActivityTracker()
    usage = {"input": 50, "output": 25, "cache_read": 7, "cache_write": 3}
    tracker.on_event(_message_end(model="anthropic/claude", usage=usage))

    stat = tracker.snapshot().per_model[0]
    assert (stat.input, stat.output, stat.cache_read) == (50, 25, 7)


def test_per_model_falls_back_to_model_provider() -> None:
    tracker = SessionActivityTracker(model_provider=lambda: "fallback/model")
    tracker.on_event(_message_end(model=None, usage={"input": 1, "output": 1}))
    stat = tracker.snapshot().per_model[0]
    assert stat.model == "fallback/model"
    assert stat.requests == 1


def test_per_model_unknown_when_no_model_and_no_provider() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(_message_end(model=None, usage=None))
    assert tracker.snapshot().per_model[0].model == "(unknown)"


def test_message_end_without_message_is_ignored() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(SimpleNamespace(type="message_end", message=None))
    assert tracker.snapshot().per_model == []


def test_user_and_tool_result_message_end_do_not_inflate_per_model() -> None:
    # The loop emits message_end for the user prompt + every tool-result message
    # too (loop.py:84). Those carry no ``.model`` / ``.usage`` and must NOT be
    # counted toward per-model requests (nor mis-attributed to the current model
    # via the provider fallback), or the leaderboard reqs column would over-count
    # by (1 user + N tool-results) per turn.
    tracker = SessionActivityTracker(model_provider=lambda: "current/model")
    tracker.on_event(_message_end(model=None, usage=None, role="user"))
    tracker.on_event(_message_end(model=None, usage=None, role="toolResult"))
    tracker.on_event(_message_end(model=None, usage=None, role="toolResult"))
    assert tracker.snapshot().per_model == []
    # A real assistant turn still counts (and resolves via the provider fallback).
    tracker.on_event(_message_end(model=None, usage={"input": 5}))
    per_model = tracker.snapshot().per_model
    assert len(per_model) == 1
    assert per_model[0].model == "current/model"
    assert per_model[0].requests == 1


def test_per_model_sorted_busiest_first() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(_message_end(model="a", usage=None))
    tracker.on_event(_message_end(model="b", usage=None))
    tracker.on_event(_message_end(model="b", usage=None))
    models = [m.model for m in tracker.snapshot().per_model]
    assert models == ["b", "a"]


# -- turns + wall time ------------------------------------------------------


def test_turn_count() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(_turn_end())
    tracker.on_event(_turn_end())
    tracker.on_event(_turn_end())
    assert tracker.snapshot().turns == 3


def test_wall_time_first_to_last_event() -> None:
    clock = _FakeClock([100.0, 101.5, 103.0, 105.25])
    tracker = SessionActivityTracker(clock=clock)
    tracker.on_event(_tool_start("read"))  # 100.0 (first)
    tracker.on_event(_tool_end("read"))  # 101.5
    tracker.on_event(_turn_end())  # 103.0
    tracker.on_event(_message_end(model="m"))  # 105.25 (last)
    assert tracker.snapshot().wall_seconds == 105.25 - 100.0


def test_wall_time_zero_before_any_event() -> None:
    tracker = SessionActivityTracker(clock=_FakeClock([5.0]))
    assert tracker.snapshot().wall_seconds == 0.0


def test_wall_time_single_event_is_zero() -> None:
    tracker = SessionActivityTracker(clock=_FakeClock([42.0]))
    tracker.on_event(_turn_end())
    assert tracker.snapshot().wall_seconds == 0.0


# -- reset / robustness -----------------------------------------------------


def test_reset_clears_all_state() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(_tool_end("read"))
    tracker.on_event(_message_end(model="m", usage={"input": 9}))
    tracker.on_event(_turn_end())

    tracker.reset()
    snap = tracker.snapshot()
    assert snap.tool_calls == 0
    assert snap.tool_failures == 0
    assert snap.per_tool == []
    assert snap.per_model == []
    assert snap.turns == 0
    assert snap.wall_seconds == 0.0
    assert snap.success_rate is None


def test_unknown_event_type_is_noop_but_stamps_clock() -> None:
    clock = _FakeClock([10.0, 12.0])
    tracker = SessionActivityTracker(clock=clock)
    tracker.on_event(SimpleNamespace(type="agent_thinking"))
    tracker.on_event(SimpleNamespace(type="mystery_event"))
    snap = tracker.snapshot()
    assert snap.tool_calls == 0
    assert snap.turns == 0
    # Even unknown events advance the wall-clock window.
    assert snap.wall_seconds == 2.0


def test_event_without_type_does_not_crash() -> None:
    tracker = SessionActivityTracker()
    tracker.on_event(SimpleNamespace())  # no .type
    tracker.on_event(object())  # no attributes at all
    # Degrades silently; clock still stamped, no counts.
    assert tracker.snapshot().tool_calls == 0


def test_malformed_message_end_does_not_crash() -> None:
    tracker = SessionActivityTracker()
    # usage attr access on a model_provider that raises must be swallowed.
    def _boom() -> str:
        raise RuntimeError("provider exploded")

    tracker = SessionActivityTracker(model_provider=_boom)
    tracker.on_event(_message_end(model=None, usage={"input": 5}))
    stat = tracker.snapshot().per_model[0]
    # Provider raised → falls through to "(unknown)" rather than crashing.
    assert stat.model == "(unknown)"
    assert stat.input == 5
