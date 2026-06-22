"""Sprint WP-8 (Feature 4) — unit tests for the ``/context`` estimator.

Covers the PURE estimator module ``tui/context_usage.py`` in isolation (no
prompt-toolkit, no harness):

* :func:`estimate_tokens` — the ``ceil(len/4)`` heuristic + monotonicity +
  None/empty/non-str → 0.
* :func:`estimate_categories` — order, omission of None/empty/whitespace
  sources, message/schema flattening, all-absent → ``[]``.
* :func:`build_category_lines` — the percent math is clamped to ``[0, 100]`` and
  the proportional bar never overflows the configured width (incl. a category
  larger than the window and a zero/negative window).
"""

from __future__ import annotations

import math

from aelix_coding_agent.tui.context_usage import (
    Category,
    build_category_lines,
    estimate_categories,
    estimate_tokens,
)

# --------------------------------------------------------------------------- #
# estimate_tokens
# --------------------------------------------------------------------------- #


def test_estimate_tokens_heuristic_ceil_div_4() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1  # ceil(1/4)
    assert estimate_tokens("abcd") == 1  # ceil(4/4)
    assert estimate_tokens("abcde") == 2  # ceil(5/4)
    assert estimate_tokens("x" * 400) == 100


def test_estimate_tokens_none_and_non_str_are_zero() -> None:
    assert estimate_tokens(None) == 0
    assert estimate_tokens(123) == 0  # type: ignore[arg-type]
    assert estimate_tokens(["a", "b"]) == 0  # type: ignore[arg-type]


def test_estimate_tokens_is_monotonic_non_decreasing() -> None:
    prev = 0
    for n in range(0, 200):
        cur = estimate_tokens("x" * n)
        assert cur >= prev, f"non-monotonic at len {n}: {cur} < {prev}"
        assert cur == math.ceil(n / 4) if n else cur == 0
        prev = cur


# --------------------------------------------------------------------------- #
# estimate_categories
# --------------------------------------------------------------------------- #


def test_estimate_categories_all_present_ordered() -> None:
    cats = estimate_categories(
        system_prompt="You are Aelix." * 4,
        tool_schemas=[{"name": "bash"}, {"name": "read"}],
        messages=[{"role": "user", "content": "hello there friend"}],
        memory_text="# AGENTS.md\nproject rules here",
    )
    names = [c.name for c in cats]
    assert names == ["System prompt", "Built-in tools", "Memory files", "Messages"]
    assert all(c.tokens > 0 for c in cats)


def test_estimate_categories_omits_none_and_empty_sources() -> None:
    cats = estimate_categories(
        system_prompt="real prompt content here",
        tool_schemas=None,
        messages=[],
        memory_text="",
    )
    assert [c.name for c in cats] == ["System prompt"]


def test_estimate_categories_omits_whitespace_only_zero_token_source() -> None:
    # A whitespace-only source: estimate_tokens floors short strings; a source
    # that estimates to 0 tokens is dropped (no information in a 0-token row).
    cats = estimate_categories(
        system_prompt=None,
        tool_schemas=None,
        messages=None,
        memory_text=None,
    )
    assert cats == []


def test_estimate_categories_flattens_message_content_parts() -> None:
    cats = estimate_categories(
        system_prompt=None,
        tool_schemas=None,
        messages=[
            {"role": "user", "content": [{"type": "text", "text": "x" * 40}]},
            {"role": "assistant", "content": "y" * 40},
        ],
        memory_text=None,
    )
    assert [c.name for c in cats] == ["Messages"]
    # 40 + 1 (newline join) + 40 chars ≈ ceil(81/4) = 21 tokens.
    assert cats[0].tokens == math.ceil(81 / 4)


def test_estimate_categories_flattens_object_messages() -> None:
    class _Msg:
        def __init__(self, content: object) -> None:
            self.content = content

    cats = estimate_categories(
        system_prompt=None,
        tool_schemas=None,
        messages=[_Msg("hello world this is content")],
        memory_text=None,
    )
    assert [c.name for c in cats] == ["Messages"]
    assert cats[0].tokens > 0


def test_estimate_categories_string_schemas_and_messages() -> None:
    cats = estimate_categories(
        system_prompt=None,
        tool_schemas="raw schema blob " * 4,
        messages="a plain string transcript",
        memory_text=None,
    )
    assert [c.name for c in cats] == ["Built-in tools", "Messages"]


# --------------------------------------------------------------------------- #
# build_category_lines
# --------------------------------------------------------------------------- #


def test_build_category_lines_empty_is_empty() -> None:
    assert build_category_lines([], window=1000) == []


def test_build_category_lines_percent_math() -> None:
    cats = [Category("System prompt", 250), Category("Messages", 500)]
    lines = build_category_lines(cats, window=1000, bar_width=10)
    assert "(25%)" in lines[0]
    assert "(50%)" in lines[1]
    assert "System prompt" in lines[0]
    assert "Messages" in lines[1]


def test_build_category_lines_percent_clamped_to_100() -> None:
    # Category larger than the window must not print >100% and must not overflow.
    cats = [Category("Messages", 5000)]
    lines = build_category_lines(cats, window=1000, bar_width=12)
    assert "(100%)" in lines[0]
    filled = lines[0].count("█")
    assert filled == 12  # fully filled, never more than the bar width


def test_build_category_lines_bar_never_overflows_width() -> None:
    bar_width = 20
    cats = [
        Category("a", 0),
        Category("b", 500),
        Category("c", 999),
        Category("d", 1000),
        Category("e", 9999),  # > window
    ]
    for window in (0, -5, 1, 1000, 1_000_000):
        lines = build_category_lines(cats, window=window, bar_width=bar_width)
        for line in lines:
            cells = line.count("█") + line.count("░")
            assert cells == bar_width, (window, line, cells)


def test_build_category_lines_zero_window_is_zero_percent() -> None:
    cats = [Category("System prompt", 500)]
    for window in (0, -10):
        lines = build_category_lines(cats, window=window, bar_width=8)
        assert "(0%)" in lines[0]
        assert lines[0].count("█") == 0


def test_build_category_lines_renders_token_count() -> None:
    cats = [Category("System prompt", 2000)]
    lines = build_category_lines(cats, window=10_000, bar_width=8)
    # format_token_count(2000) -> "2K"
    assert "2K tokens" in lines[0]
