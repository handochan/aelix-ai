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
    # ``render_max_width`` ceiling + the ``check_for_updates`` switch.
    assert "code_block_indent" not in keys
    assert "tool_card_max_lines" in keys
    assert "features_agents" in keys
    assert "render_max_width" in keys
    assert "check_for_updates" in keys
    # #238 — the ``@``-menu gitignore toggle, appended at the END of the LIVE
    # block (index 7), so ``keys[:7]`` below is unchanged by it.
    assert "respect_gitignore" in keys
    assert len(rows) == 23
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


# The TEN rows re-measured 2026-08-18 (#84, was eleven under #111 B-11) to have
# ZERO production consumers: the value round-trips to settings.json and nothing
# ever reads it back, this launch or any other. NOT a synonym for
# "persist-only" — ``features_agents`` is persist-only too but IS consumed, by
# ``cli/entry.py::_build_harness_options``, so it is absent from this set.
#
# ``enable_skill_commands`` LEFT this set under #84: #115 wired it on
# 2026-08-12 (``tui/shell.py`` -> ``expand_resource_command``) and did not
# revert its copy, so for twelve days the row worked while its help text said
# it did not — and ``test_inert_rows_do_not_promise_that_they_apply_next_launch``
# below asserted the false sentence, pinning the lie green. That is why
# ``test_the_inert_and_wired_lists_are_measured_not_declared`` now derives both
# sets from the source instead of trusting these literals.
INERT_ROWS = {
    "autocomplete_max_visible",
    "show_hardware_cursor",
    "editor_padding_x",
    "quiet_startup",
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

    These all used to end "Persisted; applies next launch", which reads
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
WIRED_PERSIST_BLOCK_ROWS = (
    "features_agents",
    "tool_card_max_lines",
    "render_max_width",
    "enable_skill_commands",
    "check_for_updates",
)


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
    """Skills are NOT inert — only this flag's SURFACE is switchable.

    ``load_skills`` runs at startup and ``harness.set_skills`` publishes the
    result, which ``/skills``, the startup banner and rpc_mode's command list
    all read — whatever this flag says. Help text implying otherwise would tell
    users ``--skill`` and ``.aelix/skills`` do nothing.

    UPDATED under #84. This test used to require the literal ``#115`` in the
    copy, because the copy's job was to point at a surface that did not exist
    yet. #115 then BUILT that surface (871a6be) without touching either the
    copy or this assertion, so the pair went on requiring a sentence that had
    become false. The issue reference is gone; what is asserted now is the part
    that stays true whichever way the flag points.
    """
    sm = SettingsManager.in_memory({})
    help_text = _rows(sm)["enable_skill_commands"].help
    assert "carrier" not in help_text
    assert "/skill:<name>" in help_text
    assert "skills still load" in help_text
    assert "does not exist" not in help_text
    assert "not yet" not in help_text


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


# === #84 — stop hand-maintaining the honesty claim ==========================


#: getter name for every row in the persist-only block, wired or not. The two
#: sets above are hand-written claims ABOUT these; the test below checks the
#: claims against the source, which is the only reason to trust either list.
_PERSIST_BLOCK_GETTERS = {
    "autocomplete_max_visible": "get_autocomplete_max_visible",
    "show_hardware_cursor": "get_show_hardware_cursor",
    "editor_padding_x": "get_editor_padding_x",
    "quiet_startup": "get_quiet_startup",
    "enable_skill_commands": "get_enable_skill_commands",
    "double_escape_action": "get_double_escape_action",
    "tree_filter_mode": "get_tree_filter_mode",
    "image_auto_resize": "get_image_auto_resize",
    "block_images": "get_block_images",
    "show_terminal_progress": "get_show_terminal_progress",
    "clear_on_shrink": "get_clear_on_shrink",
    "features_agents": "get_features_agents",
    "tool_card_max_lines": "get_tool_card_max_lines",
    "render_max_width": "get_render_max_width",
    "check_for_updates": "get_check_for_updates",
}

#: Where a getter is DEFINED rather than consumed. Excluded so the definition
#: does not read as its own consumer — the mistake that would make every row
#: look wired and the whole gate vacuous.
_NOT_A_CONSUMER = ("aelix_ai/settings/", "tui/settings_rows.py")


def _getter_call_sites(wanted: set[str] | None = None) -> dict[str, list[str]]:
    """Every production ``…get_x(…)`` CALL, by getter name.

    AST rather than a substring search, for the reason this repo keeps
    rediscovering: ``tui/shell.py``'s docstring names
    ``get_enable_skill_commands()`` in prose, and a grep counts that as a
    consumer. A row that is inert but mentioned in a comment would then be
    reported as wired — the exact inversion #84 exists to stop.

    ``wanted`` defaults to the persist-block getters, so the two existing
    callers are unchanged; #238 passes its own name in. NECESSARY BUT NOT
    SUFFICIENT as a wiring proof, and measured so: with the getter called in
    ``run_tui`` and the resulting keyword deleted at all three completer call
    sites — the row dead for every user — this scan still reports one site.
    ``tests/tui/test_completer_wiring.py`` is what actually pins the forwarding.
    """

    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    wanted = set(_PERSIST_BLOCK_GETTERS.values()) if wanted is None else set(wanted)
    found: dict[str, list[str]] = {name: [] for name in wanted}
    files = [
        p
        for p in [*repo.glob("packages/*/src/**/*.py"), *repo.glob("src/**/*.py")]
        if not any(part in p.as_posix() for part in _NOT_A_CONSUMER)
    ]
    assert len(files) > 150, f"only {len(files)} production files — bad glob"
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (
                fn.attr
                if isinstance(fn, ast.Attribute)
                else fn.id
                if isinstance(fn, ast.Name)
                else None
            )
            if name in wanted:
                found[name].append(
                    f"{path.relative_to(repo).as_posix()}:{node.lineno}"
                )
    return found


async def test_the_inert_and_wired_lists_are_measured_not_declared() -> None:
    """The two literal sets above must match what the source actually does.

    #115 wired a row and left it declared inert; nothing failed, because both
    the declaration and the assertion about it were hand-written from the same
    stale belief. This derives the answer instead.
    """

    sites = _getter_call_sites()
    measured_wired = {
        key
        for key, getter in _PERSIST_BLOCK_GETTERS.items()
        if sites[getter]
    }
    measured_inert = set(_PERSIST_BLOCK_GETTERS) - measured_wired

    assert measured_wired == set(WIRED_PERSIST_BLOCK_ROWS), (
        "WIRED_PERSIST_BLOCK_ROWS disagrees with the source. Newly wired: "
        f"{sorted(measured_wired - set(WIRED_PERSIST_BLOCK_ROWS))}; no longer "
        f"wired: {sorted(set(WIRED_PERSIST_BLOCK_ROWS) - measured_wired)}. "
        "Move the row between the two sets AND fix its help text in the same "
        "commit — see the block comment in tui/settings_rows.py."
    )
    assert measured_inert == INERT_ROWS, (
        f"INERT_ROWS disagrees with the source: {measured_inert ^ INERT_ROWS}"
    )


async def test_the_call_site_scanner_can_tell_a_call_from_a_mention() -> None:
    """Positive control for the scanner, both directions.

    A zero from ``_getter_call_sites`` is only evidence if a non-zero is
    reachable — and the docstring case is not hypothetical, it is live in
    ``tui/shell.py`` today.
    """

    sites = _getter_call_sites()
    # It finds real calls...
    assert sites["get_features_agents"], "scanner found no call it should find"
    # ...and it finds ONLY the call in the file that also mentions the name in
    # prose. shell.py:3550 is a docstring; a substring scan would report 2.
    skill_sites = sites["get_enable_skill_commands"]
    assert len(skill_sites) == 1, skill_sites
    assert "tui/shell.py" in skill_sites[0]


async def test_the_wired_row_says_something_true_about_being_off() -> None:
    """#84's actual beta deliverable, from the reader's side.

    The old copy promised "no /skill:<name> surface exists yet", which stopped
    being true when #115 shipped. Assert the new copy instead of merely
    asserting the old one is gone, so a future blanket rewrite has to keep
    meaning something.
    """

    sm = SettingsManager.in_memory({})
    row = _rows(sm)["enable_skill_commands"]
    assert "not yet wired" not in row.help
    assert "skills still load" in row.help
    assert row.live is False
    assert row.apply_note == "takes effect after you restart aelix"


# === Issue #238 — the @-menu gitignore toggle ==============================


async def test_the_gitignore_row_is_live_and_fits_the_picker() -> None:
    """⑦ The row exists, is dispatchable, is LIVE, and its help is READABLE.

    Three defects in one case. (a) A ``kind="bool"`` row whose key is missing
    from ``_BOOL_GETTERS``/``_BOOL_SETTERS`` raises ``KeyError`` inside
    ``_row_bool``, which ``apply_setting`` swallows into a red line — a silently
    dead row, live on ``main`` today for ``check_for_updates``. So the toggle is
    DRIVEN and the getter re-read, not merely looked up in the two tables.
    (b) ``live=True`` is the promise the PULL wiring keeps. (c) ``_open_settings``
    hands ``select`` ONE unwrapped string as the detail; ``select``'s Window is
    ``wrap_lines=False`` and ``_picker_frame`` clamps only its RULES to
    ``_PICK_MAX_WIDTH``, so a longer help renders to exactly the pane width, cut
    mid-word (measured: a 272-cell help → 200 rendered cells at ``tmux -x 200``).
    14 of the 22 rows on ``main`` already exceed it; this one must not.
    """

    from aelix_coding_agent.tui.context import _PICK_MAX_WIDTH, _visible_len

    sm = SettingsManager.in_memory({})
    row = _rows(sm)["respect_gitignore"]
    assert row.kind == "bool"
    assert row.live is True
    # No apply_note: the change IS in effect when the confirmation is drawn.
    assert row.apply_note is None
    assert row.read(sm) == "on"  # default ON, through the real getter

    result = apply_setting(row, sm)
    assert result.kind == "ok", result.message
    assert result.live == ("respect_gitignore", False)
    assert sm.get_respect_gitignore() is False
    assert _rows(sm)["respect_gitignore"].read(sm) == "off"

    # The help says WHAT off does, in the phrasing #238 measured to be true —
    # "the ignore files hide", never "files git ignores" (``--no-ignore`` lifts
    # ``.ignore``/``.fdignore``/parent-directory rules too, which git does not
    # own) — and carries the fd CONDITION, without which the live claim is false
    # for offline / Termux / never-ran-``find`` users.
    assert "Off →" in row.help
    assert "ignore files hide" in row.help
    assert "needs fd" in row.help
    assert _visible_len(row.help) <= _PICK_MAX_WIDTH, (
        f"help is {_visible_len(row.help)} cells; the detail panel does not wrap "
        f"and is cut at the pane width (_PICK_MAX_WIDTH={_PICK_MAX_WIDTH})"
    )


async def test_the_gitignore_row_has_a_live_consumer() -> None:
    """⑨ The #84 scan: the getter is CALLED in production, in the shell.

    NECESSARY, NOT SUFFICIENT — measured: with the keyword deleted at all three
    completer call sites the row is dead for every user and this case stays
    green, because ``run_tui``'s nested ``def`` still contains an ``ast.Call``.
    ``tests/tui/test_completer_wiring.py`` (⑫-⑮) is the actual guard.
    """

    sites = _getter_call_sites({"get_respect_gitignore"})["get_respect_gitignore"]
    assert sites, "no production consumer reads get_respect_gitignore (#84 shape)"
    assert any("tui/shell.py" in s for s in sites), sites


def _live_rows_from_source() -> dict[str, str]:
    """``{key: kind}`` for every ``live=True`` row, read out of the source."""

    import ast
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "packages/aelix-coding-agent/src/aelix_coding_agent/tui/settings_rows.py"
    )
    tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "SettingsRow"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        live = kw.get("live")
        if not (isinstance(live, ast.Constant) and live.value is True):
            continue
        key = kw["key"]
        kind = kw["kind"]
        assert isinstance(key, ast.Constant) and isinstance(kind, ast.Constant)
        out[str(key.value)] = str(kind.value)
    assert out, "found no live=True rows — the SettingsRow scan is broken"
    return out


def _apply_live_setting_branches() -> dict[str, bool]:
    """``{key: has_a_real_body}`` for every ``key == "…"`` branch in the shell."""

    import ast
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "packages/aelix-coding-agent/src/aelix_coding_agent/tui/shell.py"
    )
    tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
        and n.name == "_apply_live_setting"
    )
    out: dict[str, bool] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "key"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.comparators[0], ast.Constant)
        ):
            continue
        key = str(test.comparators[0].value)
        inert = all(
            isinstance(stmt, ast.Pass)
            or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
            for stmt in node.body
        )
        out[key] = not inert
    assert out, "found no key == '…' branches — the _apply_live_setting scan is broken"
    return out


def _docstring_key_list(marker: str) -> set[str]:
    """The comma-separated keys following ``marker`` in the module docstring."""

    import aelix_coding_agent.tui.settings_rows as rows_mod

    doc = rows_mod.__doc__ or ""
    lines = doc.splitlines()
    idx = next(
        (i for i, line in enumerate(lines) if line.strip().startswith(marker)), None
    )
    assert idx is not None, f"the module docstring carries no {marker!r} line"
    collected = [lines[idx].strip()[len(marker) :]]
    for line in lines[idx + 1 :]:
        if not line.strip():
            break
        collected.append(line.strip())
    return {part.strip() for part in " ".join(collected).split(",") if part.strip()}


async def test_the_live_rows_docstring_matches_the_measured_mechanism() -> None:
    """⑯ The LIVE-row docstring's PUSH/PULL split is DERIVED, not declared.

    The old single-mechanism sentence ("the LIVE-effect rows … DUAL-WRITE …
    the shell owns the live half") named seven rows while nine were
    ``live=True`` — ``hide_compaction_summary`` and ``render_max_width`` had
    been missing for as long as nothing derived the list from source. #238 adds
    the first row for which that sentence is not merely stale but FALSE: it does
    not dual-write at all, because the shell never binds the completer to a name
    and so cannot mirror onto it. This classifies every live row from the source
    and requires the docstring to agree.
    """

    live_rows = _live_rows_from_source()
    branches = _apply_live_setting_branches()
    push: set[str] = set()
    pull: set[str] = set()
    for key, kind in live_rows.items():
        # ``action`` rows are PUSH by delegation: the shell runs the live flow.
        if kind == "action" or branches.get(key, False):
            push.add(key)
        else:
            pull.add(key)

    assert _docstring_key_list("PUSH keys:") == push, (
        f"docstring PUSH list {sorted(_docstring_key_list('PUSH keys:'))} vs "
        f"source {sorted(push)}"
    )
    assert _docstring_key_list("PULL keys:") == pull, (
        f"docstring PULL list {sorted(_docstring_key_list('PULL keys:'))} vs "
        f"source {sorted(pull)}"
    )
    # The universal claim on the field doc goes false for a PULL row unless it
    # names the second mechanism, whether or not the list above gains the key.
    field_doc = SettingsRow.__doc__ or ""
    assert "ONE OF TWO" in field_doc, "SettingsRow.live still claims a single mechanism"
    assert "respect_gitignore" in field_doc
