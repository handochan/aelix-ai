"""Sprint 6h₁₀a (ADR-0104) — StreamRenderer unit tests.

Deterministic: a fake clock drives the throttle so there is no ``time.sleep``;
output is captured to a ``StringIO``-backed Rich Console (no real terminal).
"""

from __future__ import annotations

import io

from aelix_coding_agent.tui.stream import StreamRenderer
from rich.console import Console


class FakeClock:
    """Controllable monotonic clock.

    Each call returns the current time, then auto-advances by ``auto`` so a
    render bracketed by two calls measures ``auto`` seconds of "render time".
    """

    def __init__(self, start: float = 0.0, auto: float = 0.0) -> None:
        self.t = start
        self.auto = auto

    def __call__(self) -> float:
        value = self.t
        self.t += self.auto
        return value

    def advance(self, dt: float) -> None:
        self.t += dt


def _console(width: int = 40) -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=True, width=width), buf


def test_update_empty_before_content_is_noop() -> None:
    console, buf = _console()
    sr = StreamRenderer(console)
    sr.update("")
    assert buf.getvalue() == ""
    assert sr._live is None
    assert not sr.stopped


def test_bare_final_with_no_content_closes_without_live() -> None:
    console, buf = _console()
    sr = StreamRenderer(console)
    sr.update("", final=True)
    assert sr.stopped is True
    assert sr._live is None
    assert buf.getvalue() == ""


def test_first_update_always_renders() -> None:
    clock = FakeClock(auto=0.0)
    console, buf = _console()
    sr = StreamRenderer(console, time_fn=clock)
    sr.update("hello")
    # Live started; trailing window holds the line (nothing committed yet since
    # 1 line <= live_window default 6).
    assert sr._live is not None
    assert "hello" in buf.getvalue()


def test_throttle_skips_updates_within_min_delay() -> None:
    clock = FakeClock(auto=0.0)
    console, buf = _console()
    sr = StreamRenderer(console, min_delay=0.05, time_fn=clock)

    sr.update("a")  # first render at t=0; _when=0
    first = buf.getvalue()

    # Second update with the clock unchanged → (0 - 0) < 0.05 → skipped.
    sr.update("ab")
    assert buf.getvalue() == first  # no change

    # Advance past min_delay → renders.
    clock.advance(0.1)
    sr.update("abc")
    assert buf.getvalue() != first


def test_final_bypasses_throttle() -> None:
    clock = FakeClock(auto=0.0)
    console, buf = _console()
    sr = StreamRenderer(console, min_delay=0.05, time_fn=clock)
    sr.update("a")
    # No clock advance, but final must still render + close.
    sr.update("ab", final=True)
    assert sr.stopped is True
    assert "ab" in buf.getvalue()


def test_adaptive_min_delay_clamps_to_max() -> None:
    # auto=0.3 → render bracket measures 0.3s → 0.3*10 = 3.0 → clamp to max 2.0.
    clock = FakeClock(auto=0.3)
    console, _ = _console()
    sr = StreamRenderer(console, min_delay=0.05, max_delay=2.0, time_fn=clock)
    sr.update("hello world")
    assert sr.min_delay == 2.0


def test_adaptive_min_delay_clamps_to_floor() -> None:
    # auto=0.001 → render bracket 0.001s → 0.01 < floor 0.05 → clamp to floor.
    clock = FakeClock(auto=0.001)
    console, _ = _console()
    sr = StreamRenderer(console, min_delay=0.05, max_delay=2.0, time_fn=clock)
    sr.update("hi")
    assert sr.min_delay == 0.05


def test_stable_lines_committed_at_live_window() -> None:
    clock = FakeClock(auto=0.0)
    console, buf = _console()
    sr = StreamRenderer(console, live_window=2, time_fn=clock)
    # 5 short lines, width 40 so no wrapping.
    text = "l0\nl1\nl2\nl3\nl4"
    sr.update(text)
    # 5 lines - live_window 2 → 3 stable lines committed.
    assert len(sr._printed) == 3
    out = buf.getvalue()
    assert "l0" in out and "l1" in out and "l2" in out


def test_final_flushes_all_lines_and_stops() -> None:
    clock = FakeClock(auto=0.0)
    console, buf = _console()
    sr = StreamRenderer(console, live_window=2, time_fn=clock)
    text = "l0\nl1\nl2\nl3\nl4"
    sr.update(text)
    sr.update(text, final=True)
    assert sr.stopped is True
    assert sr._live is None
    out = buf.getvalue()
    for line in ("l0", "l1", "l2", "l3", "l4"):
        assert line in out


def test_double_final_is_noop() -> None:
    clock = FakeClock(auto=0.0)
    console, buf = _console()
    sr = StreamRenderer(console, time_fn=clock)
    sr.update("done", final=True)
    snapshot = buf.getvalue()
    # Second final must not raise and must not change output.
    sr.update("ignored", final=True)
    assert buf.getvalue() == snapshot


def test_post_final_update_is_noop() -> None:
    clock = FakeClock(auto=0.0)
    console, buf = _console()
    sr = StreamRenderer(console, time_fn=clock)
    sr.update("hello", final=True)
    snapshot = buf.getvalue()
    sr.update("more text")
    assert buf.getvalue() == snapshot
    assert sr._live is None


def test_long_line_wraps_at_console_width() -> None:
    # A single no-space line wider than the console folds into multiple lines.
    clock = FakeClock(auto=0.0)
    console, _ = _console(width=20)
    sr = StreamRenderer(console, live_window=1, time_fn=clock)
    sr.update("x" * 60)
    # One *unwrapped* line minus live_window(1) commits 0; ≥1 committed proves
    # the text wrapped into multiple lines at the snapshotted console width.
    assert len(sr._printed) >= 1


def test_width_snapshotted_at_stream_start() -> None:
    clock = FakeClock(auto=0.0)
    console, _ = _console(width=40)
    sr = StreamRenderer(console, time_fn=clock)
    sr.update("first")
    assert sr._width == 40


def test_width_change_mid_stream_does_not_corrupt_committed_rows() -> None:
    # Regression (MEDIUM-2): a terminal resize mid-stream must not re-wrap
    # already-committed scrollback rows. The snapshot width is held for the
    # stream's lifetime, so the positional ``_printed`` index stays aligned.
    clock = FakeClock(auto=0.0)
    console, _ = _console(width=40)
    sr = StreamRenderer(console, live_window=1, time_fn=clock)
    sr.update("a" * 90)  # wraps at width 40
    committed_before = list(sr._printed)
    assert committed_before  # something was committed

    console.width = 120  # simulate a terminal resize
    sr.update("a" * 90 + "bbbb")
    # Snapshot held; previously-committed rows are byte-identical (no re-wrap).
    assert sr._width == 40
    assert sr._printed[: len(committed_before)] == committed_before
