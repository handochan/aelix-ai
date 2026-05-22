"""Sprint 6h₇a (Phase 5a-iii-α, ADR-0090) — fuzzy match/filter tests.

Mirrors Pi ``packages/tui/test/fuzzy.test.ts`` (15 test cases) at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016`` — case-for-case parity so
any future drift surfaces here.
"""

from __future__ import annotations

from aelix_coding_agent.util.fuzzy import fuzzy_filter, fuzzy_match

# === fuzzy_match ============================================================


def test_empty_query_matches_everything_with_score_0() -> None:
    result = fuzzy_match("", "anything")
    assert result.matched is True
    assert result.score == 0


def test_query_longer_than_text_does_not_match() -> None:
    result = fuzzy_match("longquery", "short")
    assert result.matched is False


def test_exact_match_has_good_score() -> None:
    result = fuzzy_match("test", "test")
    assert result.matched is True
    # Should be negative due to consecutive bonuses + exact-match bonus.
    assert result.score < 0


def test_characters_must_appear_in_order() -> None:
    match_in_order = fuzzy_match("abc", "aXbXc")
    assert match_in_order.matched is True

    match_out_of_order = fuzzy_match("abc", "cba")
    assert match_out_of_order.matched is False


def test_case_insensitive_matching() -> None:
    assert fuzzy_match("ABC", "abc").matched is True
    assert fuzzy_match("abc", "ABC").matched is True


def test_consecutive_matches_score_better_than_scattered() -> None:
    consecutive = fuzzy_match("foo", "foobar")
    scattered = fuzzy_match("foo", "f_o_o_bar")

    assert consecutive.matched is True
    assert scattered.matched is True
    assert consecutive.score < scattered.score


def test_word_boundary_matches_score_better() -> None:
    at_boundary = fuzzy_match("fb", "foo-bar")
    not_at_boundary = fuzzy_match("fb", "afbx")

    assert at_boundary.matched is True
    assert not_at_boundary.matched is True
    assert at_boundary.score < not_at_boundary.score


def test_matches_swapped_alpha_numeric_tokens() -> None:
    # Pi parity: ``codex52`` swaps to ``52codex`` and matches inside
    # ``gpt-5.2-codex`` (digits "5" + "2" via gap + literal "codex").
    result = fuzzy_match("codex52", "gpt-5.2-codex")
    assert result.matched is True


# === fuzzy_filter ===========================================================


def test_empty_query_returns_all_items_unchanged() -> None:
    items = ["apple", "banana", "cherry"]
    result = fuzzy_filter(items, "", lambda x: x)
    assert result == items


def test_whitespace_only_query_returns_all_items_unchanged() -> None:
    items = ["apple", "banana", "cherry"]
    result = fuzzy_filter(items, "   ", lambda x: x)
    assert result == items


def test_filters_out_non_matching_items() -> None:
    items = ["apple", "banana", "cherry"]
    result = fuzzy_filter(items, "an", lambda x: x)
    assert "banana" in result
    assert "apple" not in result
    assert "cherry" not in result


def test_sorts_results_by_match_quality() -> None:
    items = ["a_p_p", "app", "application"]
    result = fuzzy_filter(items, "app", lambda x: x)
    # ``app`` wins: consecutive + exact match.
    assert result[0] == "app"


def test_prioritizes_exact_matches_over_longer_prefix_matches() -> None:
    items = ["clone", "cl"]
    result = fuzzy_filter(items, "cl", lambda x: x)
    assert result == ["cl", "clone"]


def test_works_with_custom_get_text() -> None:
    items = [
        {"name": "foo", "id": 1},
        {"name": "bar", "id": 2},
        {"name": "foobar", "id": 3},
    ]
    result = fuzzy_filter(items, "foo", lambda item: str(item["name"]))
    assert len(result) == 2
    names = [str(item["name"]) for item in result]
    assert "foo" in names
    assert "foobar" in names


def test_token_and_filtering_requires_all_tokens_to_match() -> None:
    # ``foo bar`` requires BOTH tokens to fuzzy-match the text.
    items = ["foo", "bar", "foobar"]
    result = fuzzy_filter(items, "foo bar", lambda x: x)
    # ``foo`` lacks ``bar``; ``bar`` lacks ``foo``; ``foobar`` has both.
    assert "foobar" in result
    assert "foo" not in result
    assert "bar" not in result


def test_indices_track_matched_positions() -> None:
    # ``abc`` against ``aXbXc`` matches at positions 0, 2, 4.
    result = fuzzy_match("abc", "aXbXc")
    assert result.matched is True
    assert result.indices == [0, 2, 4]


def test_empty_query_indices_empty() -> None:
    result = fuzzy_match("", "anything")
    assert result.indices == []
