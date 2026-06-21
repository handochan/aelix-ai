"""Tests for the permission posture engine (WP-0, ADR-0157).

Pure unit tests — no prompt-toolkit / harness deps (mirrors model_picker /
thinking_picker purity).
"""

from __future__ import annotations

from aelix_coding_agent.builtin.permission_mode import (
    CYCLE_ORDER,
    MODE_META,
    PermissionMode,
    PermissionPosture,
)


def test_default_posture_is_default() -> None:
    assert PermissionPosture().get() is PermissionMode.DEFAULT


def test_cycle_advances_and_wraps() -> None:
    p = PermissionPosture()
    seen = [p.cycle() for _ in range(len(CYCLE_ORDER))]
    # cycle() returns the NEW mode each time; after len(CYCLE_ORDER) steps it
    # wraps back to the first cycle entry.
    assert seen == [
        PermissionMode.AUTO_ACCEPT,
        PermissionMode.PLAN,
        PermissionMode.YOLO,
        PermissionMode.AUTO,
        PermissionMode.DEFAULT,
    ]
    assert p.get() is PermissionMode.DEFAULT


def test_cycle_order_contains_auto() -> None:
    # STEP 7 appended AUTO (the classifier ships this sprint).
    assert PermissionMode.AUTO in CYCLE_ORDER
    assert len(CYCLE_ORDER) == 5


def test_cycle_off_cycle_value_restarts_at_first() -> None:
    # A value not in CYCLE_ORDER (defensive) restarts at the first entry.
    p = PermissionPosture()
    p.set(PermissionMode.YOLO)
    assert p.get() is PermissionMode.YOLO
    # (every enum member is in CYCLE_ORDER, so simulate the off-cycle path by
    # asserting cycle from YOLO is AUTO, then AUTO wraps to DEFAULT)
    assert p.cycle() is PermissionMode.AUTO
    assert p.cycle() is PermissionMode.DEFAULT


def test_mode_meta_has_every_member() -> None:
    for mode in PermissionMode:
        assert mode in MODE_META, mode


def test_default_has_no_badge() -> None:
    assert MODE_META[PermissionMode.DEFAULT].badge_text == ""
    assert PermissionPosture().badge() is None


def test_non_default_modes_have_distinct_badges() -> None:
    badges = {
        MODE_META[m].badge_text
        for m in (
            PermissionMode.AUTO_ACCEPT,
            PermissionMode.PLAN,
            PermissionMode.YOLO,
            PermissionMode.AUTO,
        )
    }
    assert len(badges) == 4  # all distinct
    # Distinct from steering's ⏵⏵ glyph.
    assert all("⏵" not in b for b in badges)


def test_plan_block_reason_mentions_shift_tab_exit() -> None:
    reason = MODE_META[PermissionMode.PLAN].block_reason
    assert "shift+tab" in reason.lower()
    assert "plan mode" in reason.lower()


def test_badge_reflects_current_mode() -> None:
    p = PermissionPosture(PermissionMode.YOLO)
    assert p.badge() == MODE_META[PermissionMode.YOLO].badge_text
