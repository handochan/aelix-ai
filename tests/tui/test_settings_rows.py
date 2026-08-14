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
    # the Issue #66 ``tool_card_max_lines`` row + the hide_compaction_summary row
    # + the ADR-0197 (P2) ``features_agents`` delegation row + the issue #166
    # ``render_max_width`` ceiling.
    assert "code_block_indent" not in keys
    assert "tool_card_max_lines" in keys
    assert "features_agents" in keys
    assert "render_max_width" in keys
    assert len(rows) == 21
    # Live-effect rows come first (roadmap appendix O ordering).
    assert keys[:7] == [
        "theme",
        "default_model",
        "steering_mode",
        "follow_up_mode",
        "thinking_level",
        "hide_thinking_block",
        "hide_compaction_summary",
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


async def test_bool_hide_compaction_summary_is_live() -> None:
    # Aelix-original DISPLAY gate: the row is live (the shell mirrors the flag onto
    # renderer.hide_compaction_summary, which render.py reads per commit).
    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    row = rows["hide_compaction_summary"]
    assert row.live is True
    before = sm.get_hide_compaction_summary()
    res = apply_setting(row, sm)
    assert sm.get_hide_compaction_summary() == (not before)
    assert res.live == ("hide_compaction_summary", not before)


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


async def test_tool_card_max_lines_is_live() -> None:
    # Issue: the row is now LIVE (render.py reads renderer.tool_card_max_lines
    # per card, so the shell mirrors the persisted value onto the renderer and the
    # new cap takes effect on the next render — not only next launch). The apply
    # result must carry the (key, clamped-value) mirror payload the shell consumes.
    sm = SettingsManager.in_memory({})
    row = _rows(sm)["tool_card_max_lines"]
    assert row.live is True
    res = apply_setting(row, sm, int_value=30)
    assert res.kind == "ok"
    assert res.live == ("tool_card_max_lines", "30")  # clamped value, re-read as str


# The eleven rows re-measured 2026-07-31 (#111 B-11) to have ZERO production
# consumers: the value round-trips to settings.json and nothing ever reads it
# back, this launch or any other. NOT a synonym for "persist-only" —
# ``features_agents`` is persist-only too but IS consumed, by
# ``cli/entry.py::_build_harness_options``, so it is absent from this set.
INERT_ROWS = {
    "autocomplete_max_visible",
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


async def test_every_persist_only_row_has_a_live_none() -> None:
    # Honesty guard: the persist-only rows must NOT carry a live mirror payload.
    # ``tool_card_max_lines`` is deliberately EXCLUDED — it is a live row (see
    # test_tool_card_max_lines_is_live).
    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    for key in INERT_ROWS:
        row = rows[key]
        assert row.live is False, key
        if row.kind == "bool" or row.kind == "enum":
            res = apply_setting(row, sm)
            assert res.live is None, key


async def test_inert_rows_do_not_promise_that_they_apply_next_launch() -> None:
    """#111 B-11 — the help text of a row nobody reads must not imply it works.

    These eleven all used to end "Persisted; applies next launch", which reads
    as "restart and it takes effect". Nothing reads them at any launch, so that
    was a promise the build cannot keep.

    WHEN YOU WIRE ONE UP, delete it from ``INERT_ROWS`` in the same commit and
    give it honest help text. A row that works but claims to be inert is the
    same defect pointing the other way.
    """
    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    for key in INERT_ROWS:
        help_text = rows[key].help
        assert "applies next launch" not in help_text, key
        assert "not yet wired" in help_text, key


#: Rows that live in the #111 "persist-only" block but DO have live consumers.
#: Kept next to the guard that uses it so adding a wired row to that block
#: without extending the guard is a visible omission rather than a silent one —
#: ``render_max_width`` was added to the source comment and to this list in the
#: same commit precisely because the guard had been iterating only the original
#: two and would not have covered it.
WIRED_PERSIST_BLOCK_ROWS = ("features_agents", "tool_card_max_lines", "render_max_width")


async def test_wired_persist_only_rows_are_not_labelled_inert() -> None:
    """The inversion guard. These rows sit in the same block but have real
    consumers, so a blanket rewrite of the section would falsely demote them."""
    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    for key in WIRED_PERSIST_BLOCK_ROWS:
        assert key not in INERT_ROWS, key
        assert "not yet wired" not in rows[key].help, key


async def test_the_wired_list_matches_the_source_comment() -> None:
    """The comment above the block and the guard's list must not drift apart.

    Both are hand-maintained, and the pairing is the only thing that makes the
    guard trustworthy: a row named as an exception in prose but missing from the
    list is exactly the gap this commit found.
    """

    from pathlib import Path

    import aelix_coding_agent.tui.settings_rows as rows_mod

    source = Path(rows_mod.__file__).read_text(encoding="utf-8")
    block = source.split("genuinely wired and whose", 1)[1].split("WHEN YOU WIRE", 1)[0]
    named = {line.split("``")[1] for line in block.splitlines() if "``" in line}
    assert named == set(WIRED_PERSIST_BLOCK_ROWS), (
        f"comment names {sorted(named)}, guard iterates {sorted(WIRED_PERSIST_BLOCK_ROWS)}"
    )


async def test_skill_commands_help_does_not_claim_skills_are_unconsumed() -> None:
    """Skills are NOT inert — only this flag is.

    ``load_skills`` runs at startup and ``harness.set_skills`` publishes the
    result, which ``/skills``, the startup banner and rpc_mode's command list
    all read. Help text saying "the skills carrier has no consumer" would tell
    users ``--skill`` and ``.aelix/skills`` do nothing. What is genuinely
    missing is the ``/skill:<name>`` surface (#115).
    """
    sm = SettingsManager.in_memory({})
    help_text = _rows(sm)["enable_skill_commands"].help
    assert "carrier" not in help_text
    assert "/skill:<name>" in help_text
    assert "#115" in help_text


# === The false success (A-3) ==================================================


async def test_toggling_delegation_says_it_needs_a_restart() -> None:
    """THE CONFIRMATION IS WHERE THE LIE WAS.

    ``features_agents`` is persist-only: ``_agents_delegation_enabled`` reads it
    once per process while the harness is built, and ``/reload`` re-runs that
    same factory over a closure variable already frozen at ``None``. So the
    measured user journey was: ``/agents run`` refuses and points at
    ``/settings`` → the toggle reports ``agent delegation → on`` → the same
    command refuses again, with nothing anywhere saying why.

    The row's HELP text already said "applies next launch". Help is in the
    detail panel; the confirmation is what the user reads after acting.
    """

    sm = SettingsManager.in_memory({})
    row = _rows(sm)["features_agents"]

    result = apply_setting(row, sm)

    assert result.kind == "ok"
    assert "restart" in result.message.lower(), result.message
    # Still says what it did — the note is an addition, not a replacement.
    assert "agent delegation" in result.message.lower()
    assert "on" in result.message.lower()


async def test_a_row_without_apply_note_gains_no_suffix() -> None:
    """The note is row-scoped, so a live row's confirmation is untouched."""

    sm = SettingsManager.in_memory({})
    rows = _rows(sm)
    live_row = next(r for r in rows.values() if r.kind == "bool" and r.live)

    assert live_row.apply_note is None
    assert "(" not in apply_setting(live_row, sm).message


async def test_render_max_width_row_is_a_live_bounded_int() -> None:
    """Issue #166 — the ceiling is settable, live, and reads "default" unset."""

    sm = SettingsManager.in_memory({})
    row = _rows(sm)["render_max_width"]
    assert row.kind == "int"
    assert row.live is True
    assert row.int_range == (60, 240)
    # Unset shows "default" rather than a number, because unset stores no number
    # — the built-in ceiling lives in exactly one place (tui/width.py).
    assert row.read(sm) == "default"
    sm.set_render_max_width(90)
    assert row.read(sm) == "90"


async def test_render_max_width_clamps_on_write_and_on_read() -> None:
    sm = SettingsManager.in_memory({})
    sm.set_render_max_width(10_000)
    assert sm.get_render_max_width() == 240
    sm.set_render_max_width(1)
    assert sm.get_render_max_width() == 60
    # A hand-edited settings.json bypasses the setter, so the getter clamps too.
    # Loading raw JSON is the real shape of that scenario — writing
    # ``_global_settings`` directly does not, because the getter reads the merged
    # view and a direct poke never recomputes it.
    hand_edited = SettingsManager.in_memory({"renderMaxWidth": 9999})
    assert hand_edited.get_render_max_width() == 240
    too_small = SettingsManager.in_memory({"renderMaxWidth": 2})
    assert too_small.get_render_max_width() == 60
