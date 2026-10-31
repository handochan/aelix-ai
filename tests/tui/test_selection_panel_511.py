"""#511 (ADR-0279 D11) — the selection panel's model and its rendering, with no terminal.

The live checks (pty + pyte at 50x24, 80x24, 120x40, 200x50) are in the
issue's verification record; these pin what a frame must always be: exactly the
height it opened with, no row wider than the width it was drawn at, no control
character from a file reaching the terminal.
"""

from __future__ import annotations

import re

import pytest
from aelix_coding_agent.core.resource_selection import (
    ApplyResult,
    ItemState,
    Lock,
    ResourceItem,
    SaveResult,
    ScopeStatus,
    Snapshot,
)
from aelix_coding_agent.tui.selection_panel import (
    SelectionModel,
    cells,
    fit,
    plan_layout,
    render_panel,
    summary,
    wrap,
)

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _item(name: str, group: str = "global", description: str = "Does a thing.") -> ResourceItem:
    label = {"project": "PROJECT  .aelix/skills/", "global": "GLOBAL  ~/.aelix/agent/skills/"}.get(
        group, group.upper()
    )
    return ResourceItem(
        kind="skill",
        key=name,
        label=name,
        invocation=name,
        identities=(name,),
        description=description,
        group=group,
        group_label=label,
        path=f"/x/{name}/SKILL.md",
        scope=group,
        location=f"~/x/{name}",
    )


def _snapshot(
    states: list[ItemState], *, project_writable: bool = True, notices: tuple[str, ...] = ()
) -> Snapshot:
    return Snapshot(
        "skill",
        tuple(states),
        {
            "global": ScopeStatus("global", "/g/settings.json", True),
            "project": ScopeStatus(
                "project",
                "/p/.aelix/settings.json",
                project_writable,
                None if project_writable else "this folder is not trusted (/trust to decide)",
            ),
        },
        notices=notices,
    )


def _basic() -> SelectionModel:
    states = [
        ItemState(_item("conventions", "project"), True, True, "project"),
        ItemState(_item("pdf-tools"), True, True, None),
        ItemState(_item("deploy"), False, True, "global"),
        ItemState(_item("forced", "cli"), True, True, None, Lock(True, "forced on by --skill")),
    ]
    return SelectionModel(_snapshot(states), "Skills", ("skill", "skills"))


# --- the model --------------------------------------------------------------


def test_toggle_twice_leaves_no_edit() -> None:
    model = _basic()
    model.cursor = 1
    assert model.toggle() is None
    assert model.edits == {"pdf-tools": False}
    assert model.dirty
    model.toggle()
    assert model.edits == {} and not model.dirty


def test_a_locked_row_refuses_with_its_reason() -> None:
    model = _basic()
    model.cursor = 3
    refusal = model.toggle()
    assert refusal == "forced is locked: forced on by --skill"
    assert model.edits == {}
    assert model.message == ("warn", refusal)


def test_the_scope_control_moves_edits_and_refuses_an_unwritable_scope() -> None:
    model = _basic()
    model.cursor = 1
    model.toggle()
    assert model.set_target("project") is None
    # One file per Enter (C20): the edit goes where the control now points.
    assert model.edits == {"pdf-tools": False} and model.target == "project"
    untrusted = SelectionModel(
        _snapshot(list(model.states), project_writable=False), "Skills", ("skill", "skills")
    )
    refusal = untrusted.set_target("project")
    assert refusal is not None and "not trusted" in refusal
    assert untrusted.target == "global"


def test_search_filters_on_name_and_description_and_resets_the_cursor() -> None:
    model = _basic()
    model.cursor = 2
    model.type("pdf")
    assert [s.item.key for s in model.visible()] == ["pdf-tools"] and model.cursor == 0
    model.backspace()
    model.backspace()
    model.backspace()
    assert len(model.visible()) == 4


def test_paging_and_home_end() -> None:
    states = [ItemState(_item(f"s{i:03d}"), True, True, None) for i in range(120)]
    model = SelectionModel(_snapshot(states), "Skills", ("skill", "skills"))
    model.jump(10)
    assert model.cursor == 10
    model.jump(-50)
    assert model.cursor == 0
    model.end()
    assert model.cursor == 119
    model.move(1)
    assert model.cursor == 0  # ↑/↓ wrap


# --- rendering --------------------------------------------------------------


def _plain(rows: list[str]) -> list[str]:
    return [_SGR.sub("", r) for r in rows]


@pytest.mark.parametrize(("width", "rows"), [(50, 24), (80, 24), (120, 40), (200, 50)])
def test_every_frame_is_the_same_height_and_never_wider_than_the_width(
    width: int, rows: int
) -> None:
    hostile = "evil\x1b]0;PWNED\x07\x1b[31m red\nnext"
    states = [
        ItemState(
            _item(
                f"skill-{i:03d}", "global" if i % 3 else "project", f"Description number {i} " * 6
            ),
            i % 4 != 0,
            True,
            None,
        )
        for i in range(120)
    ]
    states.append(
        ItemState(
            _item("한글-스킬-이름이-정말-아주-긴-스킬입니다", "global", "한국어 설명 " * 30),
            True,
            True,
            None,
        )
    )
    states.append(ItemState(_item("hostile", "global", hostile), True, True, None))
    model = SelectionModel(
        _snapshot(states, notices=("The read tool is off.",)), "Skills", ("skill", "skills")
    )
    layout = plan_layout(model, width, rows - 7)
    heights = set()
    for step in range(130):
        frame = render_panel(model, layout)
        heights.add(len(frame))
        for row in _plain(frame):
            assert "\x1b" not in row and "\x07" not in row, row
            assert cells(row) <= width, (width, cells(row), row)
        if step == 40:
            model.type("skill-01")
        if step == 60:
            model.type("x")  # no match
        if step == 70:
            model.search = ""
        model.move(1)
    assert heights == {layout.height}
    assert layout.height == rows - 7


def test_the_cursor_group_header_stays_pinned_and_the_counter_shows() -> None:
    states = [ItemState(_item(f"s{i:03d}"), True, True, None) for i in range(120)]
    model = SelectionModel(_snapshot(states), "Skills", ("skill", "skills"))
    layout = plan_layout(model, 80, 17)
    model.cursor = 60
    frame = _plain(render_panel(model, layout))
    assert frame[2].startswith(" GLOBAL")
    assert "(61/120)" in frame[1]
    assert any("▸ [✓] s060" in row for row in frame)


def test_narrow_drops_the_description_column_and_shortens_the_scope_control() -> None:
    model = _basic()
    frame = _plain(render_panel(model, plan_layout(model, 50, 17)))
    assert "[Global] Project" in frame[0]
    assert all("Does a thing." not in row for row in frame[2:8])
    wide = _plain(render_panel(model, plan_layout(model, 80, 17)))
    assert "Save to [Global]  This project" in wide[0]
    assert any("Does a thing." in row for row in wide[2:8])


def test_status_column_and_locked_glyphs() -> None:
    model = _basic()
    model.cursor = 1
    model.toggle()
    frame = _plain(render_panel(model, plan_layout(model, 80, 17)))
    text = "\n".join(frame)
    assert "( ✓)" not in text and "(✓) forced" in text
    assert "* unsaved" in text
    assert "off · pending" in text  # deploy: saved off, applied on
    assert "project" in text


def test_cut_and_wrap_are_cluster_aware() -> None:
    assert fit("한국어설명", 5) == "한국…"
    assert cells(fit("한국어설명", 5)) <= 5
    assert wrap("one two three four", 9, 2) == ["one two", "three …"]
    assert all(cells(r) <= 6 for r in wrap("가나다라마바사아자차", 6, 3))


def test_summary_wraps_under_its_indent() -> None:
    note = "The model gets the new list from your next message; skill text it already read stays."
    text = summary(
        "Skills",
        SaveResult(True, None, ("/home/u/.aelix/agent/settings.json",), (("pdf-tools", False),)),
        ApplyResult(True, turned_off=("pdf-tools", "deploy")),
        effect_note=note,
        width=60,
    ).plain.split("\n")
    assert text[0] == "✓ Skills saved to /home/u/.aelix/agent/settings.json"
    assert text[1] == "    off  pdf-tools"
    assert text[2] == "  Also applied from settings: deploy off (saved elsewhere)."
    assert all(row.startswith("  ") and cells(row) <= 60 for row in text[3:])


def test_a_terminal_that_shrinks_under_the_panel_shrinks_its_height_not_its_hint() -> None:
    """C22: the height is fixed at open, except that it follows a smaller cap down."""

    from aelix_coding_agent.tui.selection_panel import SelectionList
    from prompt_toolkit.key_binding import KeyBindings

    states = [ItemState(_item(f"s{i:03d}"), True, True, None) for i in range(40)]
    model = SelectionModel(_snapshot(states), "Skills", ("skill", "skills"))
    layout = plan_layout(model, 80, 17)
    cap = {"rows": 40}
    control = SelectionList(model, layout, lambda: 80, KeyBindings(), cap=lambda: cap["rows"])
    assert control.current_layout().height == 17  # a larger cap never grows it
    cap["rows"] = 10
    small = control.current_layout()
    assert small.height == 10
    frame = _plain(render_panel(model, small))
    assert len(frame) == 10
    assert frame[-1].startswith(" ↑↓ move")  # the hint survives; the list gave up rows
    cap["rows"] = 4
    tiny = _plain(render_panel(model, control.current_layout()))
    assert len(tiny) == 4 and "too small" in tiny[1]


# --- round 1 (Codex X7, X8; verifier V2, V5, V7) ------------------------------------


def test_state_and_refusal_lines_flatten_control_characters() -> None:
    """X7: a lock reason carries a path; a newline or U+202E in it must not reach the terminal."""

    hostile = "shadowed by the global skill (/x/evil\n‮dir\x1b[31m/SKILL.md)"
    states = [ItemState(_item("dup"), False, False, None, Lock(False, hostile))]
    model = SelectionModel(_snapshot(states), "Skills", ("skill", "skills"))
    for frame in (render_panel(model, plan_layout(model, 80, 17)),):
        joined = "".join(_SGR.sub("", row) for row in frame)
        assert "\n" not in joined and "‮" not in joined and "\x1b" not in joined
    model.toggle()  # the refusal quotes the same reason
    assert model.message is not None
    rows = [_SGR.sub("", r) for r in render_panel(model, plan_layout(model, 80, 17))]
    assert all("‮" not in r and "\x1b" not in r for r in rows)
    assert any("<U+202E>" in r for r in rows)


def test_a_one_row_list_shows_the_row_space_toggles() -> None:
    """X8: the single row went to the group header, so Space changed a row nobody saw."""

    states = [ItemState(_item(f"s{i}"), True, True, None) for i in range(5)]
    model = SelectionModel(_snapshot(states), "Skills", ("skill", "skills"))
    layout = plan_layout(model, 80, 9)
    assert layout.viewport == 1
    for cursor in range(5):
        model.cursor = cursor
        rows = [_SGR.sub("", r) for r in render_panel(model, layout)]
        assert any(f"▸ [✓] s{cursor}" in r for r in rows)


def test_moving_away_clears_a_refusal_but_not_the_create_question() -> None:
    """V2: jump, home, end, type and backspace kept a refusal about another row."""

    for step in ("jump", "home", "end", "type", "backspace"):
        model = _basic()
        model.cursor = 3
        model.toggle()  # locked: a refusal
        assert model.message is not None
        model.search = "x" if step == "backspace" else ""
        if step == "jump":
            model.jump(-1)
        elif step == "home":
            model.home()
        elif step == "end":
            model.end()
        elif step == "type":
            model.type("f")
        else:
            model.backspace()
        assert model.message is None, step
    model = _basic()
    model.confirm_create = True
    model.message = ("warn", "This creates .aelix/settings.json")
    model.home()
    assert model.message is not None


def test_rows_fit_at_twenty_and_twenty_one_columns() -> None:
    """V5: a 13-cell status column pushed narrow rows past the width."""

    states = [
        ItemState(_item("deploy"), False, True, "global"),  # off · pending
        ItemState(_item("forced", "cli"), True, True, None, Lock(True, "x")),
        ItemState(_item("a-long-skill-name"), True, True, None),
    ]
    model = SelectionModel(_snapshot(states), "Skills", ("skill", "skills"))
    for width in (20, 21, 22):
        for row in render_panel(model, plan_layout(model, width, 17)):
            assert cells(_SGR.sub("", row)) <= width, (width, row)


def test_a_shadowed_row_does_not_show_its_winners_saved_value() -> None:
    """V7: "Saved on · Locked: shadowed" read as if the shadowed copy were on."""

    item = ResourceItem(
        kind="skill",
        key="dup@/b/dup/SKILL.md",
        label="dup",
        invocation="",
        identities=("dup",),
        group="bundled",
        group_label="BUNDLED",
        path="/b/dup/SKILL.md",
        scope="bundled",
    )
    states = [ItemState(item, True, False, None, Lock(False, "shadowed by the global skill"))]
    model = SelectionModel(_snapshot(states), "Skills", ("skill", "skills"))
    rows = [_SGR.sub("", r) for r in render_panel(model, plan_layout(model, 80, 17))]
    assert any("Not loaded: shadowed by the global skill" in r for r in rows)
    assert not any("Saved on" in r for r in rows)
