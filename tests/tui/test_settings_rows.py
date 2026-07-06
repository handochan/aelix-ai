"""ImplConsumers (ADR-0161) — unit tests for the expanded /settings rows.

Drives :func:`build_settings_rows` + :func:`apply_setting` against a seeded
in-memory :class:`SettingsManager` (synchronous read-back is reliable; the disk
write is fire-and-forget). Covers: every row builds + reads its current value;
enum cycles wrap; bools flip; ints clamp via the setter; action rows delegate; the
live/persist split (live rows carry a ``(key, value)`` mirror payload, persist-only
rows do not); and persistence (re-read reflects the change).
"""

from __future__ import annotations

from aelix_ai.settings import SettingsManager
from aelix_coding_agent.tui.settings_rows import (
    ApplyResult,
    SettingsRow,
    apply_setting,
    build_settings_rows,
)


def _rows(sm: SettingsManager) -> dict[str, SettingsRow]:
    return {r.key: r for r in build_settings_rows(sm)}


async def test_build_rows_count_and_keys() -> None:
    sm = SettingsManager.in_memory({})
    rows = build_settings_rows(sm)
    keys = [r.key for r in rows]
    # The planned settable rows (code-block-indent SKIPPED — no setter); +1 for
    # the Issue #66 ``tool_card_max_lines`` row.
    assert "code_block_indent" not in keys
    assert "tool_card_max_lines" in keys
    assert len(rows) == 18
    # Live-effect rows come first (roadmap appendix O ordering).
    assert keys[:6] == [
        "theme",
        "default_model",
        "steering_mode",
        "follow_up_mode",
        "thinking_level",
        "hide_thinking_block",
    ]


async def test_rows_read_current_values_for_seeded_manager() -> None:
    sm = SettingsManager.in_memory(
        {
            "theme": "dark",
            "steeringMode": "all",
            "quietStartup": True,
            "autocompleteMaxVisible": 12,
            "treeFilterMode": "no-tools",
        }
    )
    rows = _rows(sm)
    assert rows["theme"].read(sm) == "dark"
    assert rows["steering_mode"].read(sm) == "all"
    assert rows["quiet_startup"].read(sm) == "on"
    assert rows["autocomplete_max_visible"].read(sm) == "12"
    assert rows["tree_filter_mode"].read(sm) == "no-tools"
    # An unset bool defaults to off; hide-thinking reads visible/hidden.
    assert rows["block_images"].read(sm) == "off"
    assert rows["hide_thinking_block"].read(sm) in ("hidden", "visible")


async def test_bool_flip_persists_and_marks_persist_only() -> None:
    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    res = apply_setting(rows["quiet_startup"], sm)
    assert isinstance(res, ApplyResult) and res.kind == "ok"
    assert sm.get_quiet_startup() is True
    assert res.live is None  # persist-only — no live mirror payload


async def test_bool_hide_thinking_is_live() -> None:
    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    before = sm.get_hide_thinking_block()
    res = apply_setting(rows["hide_thinking_block"], sm)
    assert sm.get_hide_thinking_block() == (not before)
    assert res.live == ("hide_thinking_block", not before)


async def test_enum_steering_cycles_and_wraps_and_is_live() -> None:
    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    assert rows["steering_mode"].read(sm) == "one-at-a-time"
    res = apply_setting(rows["steering_mode"], sm)
    assert sm.get_steering_mode() == "all"
    assert res.live == ("steering_mode", "all")
    # Wrap back.
    apply_setting(_rows(sm)["steering_mode"], sm)
    assert sm.get_steering_mode() == "one-at-a-time"


async def test_enum_tree_filter_wraps_through_all_five() -> None:
    sm = SettingsManager.in_memory({})
    start = sm.get_tree_filter_mode()
    seen = [start]
    for _ in range(5):
        apply_setting(_rows(sm)["tree_filter_mode"], sm)
        seen.append(sm.get_tree_filter_mode())
    # Five distinct values then a wrap back to the start.
    assert len(set(seen[:-1])) == 5
    assert seen[0] == seen[-1]


async def test_enum_double_escape_wraps_three() -> None:
    sm = SettingsManager.in_memory({})
    seen = [sm.get_double_escape_action()]
    for _ in range(3):
        apply_setting(_rows(sm)["double_escape_action"], sm)
        seen.append(sm.get_double_escape_action())
    assert seen[0] == seen[-1]
    assert set(seen) == {"fork", "tree", "none"}


async def test_int_clamps_high_and_low() -> None:
    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    res = apply_setting(rows["autocomplete_max_visible"], sm, int_value=99)
    assert sm.get_autocomplete_max_visible() == 20  # clamped to [3, 20]
    assert "20" in res.message
    apply_setting(_rows(sm)["autocomplete_max_visible"], sm, int_value=1)
    assert sm.get_autocomplete_max_visible() == 3
    apply_setting(_rows(sm)["editor_padding_x"], sm, int_value=99)
    assert sm.get_editor_padding_x() == 3  # clamped to [0, 3]


async def test_tool_card_max_lines_row_and_clamp() -> None:
    # Issue #66 — the row is present, reads its default (12), and the setter
    # clamps to [3, 40] via the apply path.
    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    row = rows["tool_card_max_lines"]
    assert row.kind == "int" and row.int_range == (3, 40)
    assert row.read(sm) == "12"  # default when unset
    res = apply_setting(row, sm, int_value=99)
    assert res.kind == "ok"
    assert sm.get_tool_card_max_lines() == 40  # clamped high
    assert "40" in res.message
    apply_setting(_rows(sm)["tool_card_max_lines"], sm, int_value=1)
    assert sm.get_tool_card_max_lines() == 3  # clamped low
    # A valid in-range value round-trips.
    apply_setting(_rows(sm)["tool_card_max_lines"], sm, int_value=20)
    assert sm.get_tool_card_max_lines() == 20


async def test_int_without_value_is_error() -> None:
    sm = SettingsManager.in_memory({})
    res = apply_setting(_rows(sm)["autocomplete_max_visible"], sm, int_value=None)
    assert res.kind == "error"


async def test_action_rows_delegate() -> None:
    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    for key in ("theme", "default_model", "thinking_level"):
        res = apply_setting(rows[key], sm)
        assert res.kind == "delegate"
        assert res.message == key


async def test_apply_never_raises_on_setter_failure() -> None:
    # A setter blowing up returns an error ApplyResult, not an exception.
    class _BoomSM:
        def get_quiet_startup(self) -> bool:
            return False

        def set_quiet_startup(self, value: bool) -> None:
            raise RuntimeError("disk full")

    sm = SettingsManager.in_memory({})
    row = _rows(sm)["quiet_startup"]
    # Build the row against the real SM (for read), apply against a boom setter.
    res = apply_setting(row, _BoomSM())  # type: ignore[arg-type]
    assert res.kind == "error"
    assert "disk full" in res.message


async def test_every_persist_only_row_has_a_live_none() -> None:
    # Honesty guard: the persist-only rows must NOT carry a live mirror payload
    # (their commit message says "applies next launch / when a consumer is wired").
    sm = SettingsManager.in_memory({})
    persist_only = {
        "autocomplete_max_visible",
        "tool_card_max_lines",
        "show_hardware_cursor",
        "editor_padding_x",
        "quiet_startup",
        "enable_skill_commands",
        "double_escape_action",
        "tree_filter_mode",
        "image_auto_resize",
        "block_images",
        "show_terminal_progress",
        "clear_on_shrink",
    }
    rows = _rows(sm)
    for key in persist_only:
        row = rows[key]
        assert row.live is False, key
        if row.kind == "bool" or row.kind == "enum":
            res = apply_setting(row, sm)
            assert res.live is None, key
