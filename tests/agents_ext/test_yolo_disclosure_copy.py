"""The sentences that PROMISE what #196 changed, gated so they cannot revert.

A sabotage round found this hole: reverting `MODE_META[YOLO].description` to
its pre-#196 wording — the string a user reads AT THE MOMENT they press
shift+tab into `yolo` — left 1788 tests green. Copy is not usually worth a gate,
but this copy is now load-bearing in two directions at once: it is the only
place a user is told, before choosing the posture, that the posture also covers
delegation, and SECURITY.md's Scope list makes the same promise about a child
that can write to `.aelix/`.

The assertions are deliberately about the CLAIM, not the phrasing: each checks
that the sentence still says the thing, not that it says it in today's words.
"""

from __future__ import annotations

from pathlib import Path

from aelix_coding_agent.builtin.permission_mode import MODE_META, PermissionMode

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_yolo_mode_description_says_it_covers_delegation() -> None:
    """The toast a user sees at the moment they choose the posture.

    Shown by ``tui/shell.py`` on the shift+tab cycle, so it is the last thing
    read before the decision rather than documentation read after it.
    """

    text = MODE_META[PermissionMode.YOLO].description.lower()
    assert "subagent" in text or "delegat" in text, (
        f"MODE_META[YOLO] no longer tells the user that yolo covers "
        f"delegation, which is the one thing #196 changed about it: {text!r}"
    )
    # The half that did NOT change, kept beside it so a rewrite cannot quietly
    # drop the floor while updating the prompt half.
    assert "guardrail" in text


def test_security_md_scope_names_the_yolo_delegation_trade() -> None:
    """SECURITY.md's Scope list is where "working as designed" is enumerated.

    The specific fact that belongs there and nowhere else: a `yolo` child is not
    subject to the `.aelix/` write refusal that binds `auto-accept-edits` and
    `auto` children, so it can author the parent's next profile or extension.
    """

    text = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "`yolo`" in text
    assert ".aelix/" in text
    scope = text.split("## Scope", 1)[1]
    assert "yolo" in scope.lower(), "the yolo trade is not in the Scope list"


def test_the_agent_profiles_guide_still_states_the_exception() -> None:
    """`You are still the one who answers` was true of every posture and is not.

    Asserted on BOTH copies, because the wheel's is the one an installed user
    reads and the two have drifted before.
    """

    for path in (
        REPO_ROOT / "docs" / "guides" / "agent-profiles.md",
        REPO_ROOT
        / "packages"
        / "aelix-coding-agent"
        / "src"
        / "aelix_coding_agent"
        / "docs"
        / "agent-profiles.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert "You are still the one who answers" in text, path
        assert "with one\nexception" in text, (
            f"{path} still claims you always answer, with no exception noted"
        )
        assert "told, not asked" in text, path
