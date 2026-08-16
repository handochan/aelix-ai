"""Sprint 6h₁₀b (ADR-0105) — StreamRenderer (sink-based) unit tests.

Deterministic: a fake clock drives the throttle; ``commit``/``set_tail`` capture
into lists (no Rich Live, no real terminal).
"""

from __future__ import annotations

from aelix_coding_agent.tui.stream import StreamRenderer, plain_lines


class FakeClock:
    def __init__(self, start: float = 0.0, auto: float = 0.0) -> None:
        self.t = start
        self.auto = auto

    def __call__(self) -> float:
        value = self.t
        self.t += self.auto
        return value

    def advance(self, dt: float) -> None:
        self.t += dt


def _renderer(
    *, width: int = 40, live_window: int = 6, min_delay: float = 1 / 20, clock: FakeClock
) -> tuple[StreamRenderer, list[str], list[str]]:
    commits: list[str] = []
    tails: list[str] = []
    sr = StreamRenderer(
        commit=commits.append,
        set_tail=tails.append,
        width=width,
        live_window=live_window,
        min_delay=min_delay,
        time_fn=clock,
    )
    return sr, commits, tails


def test_update_empty_before_content_is_noop() -> None:
    sr, commits, tails = _renderer(clock=FakeClock())
    sr.update("")
    assert commits == [] and tails == [] and not sr.stopped


def test_bare_final_with_no_content_closes() -> None:
    sr, commits, tails = _renderer(clock=FakeClock())
    sr.update("", final=True)
    assert sr.stopped is True and commits == []


def test_first_update_sets_tail_no_commit() -> None:
    sr, commits, tails = _renderer(clock=FakeClock())
    sr.update("hello")  # 1 line <= live_window → stays in the live tail
    assert commits == []
    assert tails and "hello" in tails[-1]


def test_throttle_skips_updates_within_min_delay() -> None:
    clock = FakeClock()
    sr, commits, tails = _renderer(min_delay=0.05, clock=clock)
    sr.update("a")
    n = len(tails)
    sr.update("ab")  # within min_delay → skipped
    assert len(tails) == n
    clock.advance(0.1)
    sr.update("abc")  # past min_delay → renders
    assert len(tails) == n + 1


def test_final_bypasses_throttle() -> None:
    clock = FakeClock()
    sr, commits, tails = _renderer(min_delay=0.05, clock=clock)
    sr.update("a")
    sr.update("ab", final=True)
    assert sr.stopped is True


def test_adaptive_min_delay_clamps_to_max() -> None:
    sr, _c, _t = _renderer(min_delay=0.05, clock=FakeClock(auto=0.3))
    sr.update("hello world")
    assert sr.min_delay == 2.0


def test_adaptive_min_delay_clamps_to_floor() -> None:
    sr, _c, _t = _renderer(min_delay=0.05, clock=FakeClock(auto=0.001))
    sr.update("hi")
    assert sr.min_delay == 0.05


def test_stable_lines_committed_at_window() -> None:
    sr, commits, tails = _renderer(live_window=2, clock=FakeClock())
    # A markdown list renders one line per item (single-newline text would be one
    # joined paragraph). 5 items − window 2 → the earliest items commit as stable.
    sr.update("- l0\n- l1\n- l2\n- l3\n- l4")
    assert commits  # stable prefix committed
    joined = "".join(commits)
    assert "l0" in joined and "l1" in joined  # earliest items are stable
    assert "l4" in "".join(tails)  # the last item stays in the live tail


def test_final_flushes_all_and_clears_tail() -> None:
    sr, commits, tails = _renderer(live_window=2, clock=FakeClock())
    text = "l0\nl1\nl2\nl3\nl4"
    sr.update(text)
    sr.update(text, final=True)
    assert sr.stopped is True
    joined = "".join(commits)
    for line in ("l0", "l1", "l2", "l3", "l4"):
        assert line in joined
    assert tails[-1] == ""  # tail cleared on final


def test_long_line_wraps_at_width() -> None:
    sr, commits, tails = _renderer(width=20, live_window=1, clock=FakeClock())
    sr.update("x" * 60)  # folds into multiple 20-col lines
    # One unwrapped line − window 1 = 0 committed; ≥1 committed proves wrapping.
    assert commits


def test_double_final_is_noop() -> None:
    sr, commits, _t = _renderer(clock=FakeClock())
    sr.update("done", final=True)
    snapshot = list(commits)
    sr.update("ignored", final=True)
    assert commits == snapshot


def test_post_final_update_is_noop() -> None:
    sr, commits, _t = _renderer(clock=FakeClock())
    sr.update("hello", final=True)
    snapshot = list(commits)
    sr.update("more")
    assert commits == snapshot


# === plain_lines (issue #170) ===============================================
#
# The reasoning counterpart to `markdown_lines`. It shipped with no tests at
# all, which left the one thing it exists for — reasoning is NOT markdown —
# undefended: swapping the renderer's call for `markdown_lines` kept the whole
# suite green.


def test_plain_lines_renders_nothing_for_empty_input() -> None:
    assert plain_lines("", 40) == []


def test_plain_lines_emits_the_requested_style() -> None:
    (line,) = plain_lines("reasoning", 40, style="dim italic")
    assert line.startswith("\x1b[2;3m"), repr(line)
    assert "reasoning" in line


def test_plain_lines_leaves_markdown_alone() -> None:
    """The whole reason this is not `markdown_lines`.

    Run through a Markdown renderer, ``**bold**`` becomes bold, ``# heading``
    becomes a heading and the backticks vanish — so reasoning stops reading as
    reasoning and starts reading as the answer.
    """

    source = "# not a heading\n**not bold** and `not code`"
    rendered = "".join(plain_lines(source, 60))
    for literal in ("# not a heading", "**not bold**", "`not code`"):
        assert literal in rendered, f"{literal!r} was restyled away"


def test_plain_lines_wraps_at_width() -> None:
    lines = plain_lines("x" * 60, 20)
    assert len(lines) == 3
    assert all(line.endswith("\n") for line in lines[:-1])
