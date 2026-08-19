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
    scope = text.split("## Scope", 1)[1].split("\n## ", 1)[0]

    # PINNED ON THE CLAIM, NOT ON THE WORD. The first spelling of this test
    # asserted `"yolo" in scope`, and a sabotage that deleted the whole bullet
    # left it GREEN — `yolo` appears elsewhere in the section. What has to be
    # there is the SENTENCE: a delegated child starts unconfirmed, and the
    # `.aelix/` refusal that binds the other write-capable postures does not
    # bind this one.
    bullet = [
        para
        for para in scope.split("\n- **")
        if "yolo" in para.lower() and "subagent" in para.lower()
    ]
    assert bullet, "the yolo delegation trade is not in the Scope list at all"
    claim = bullet[0]
    assert ".aelix/" in claim, (
        "the Scope entry no longer says a yolo child escapes the .aelix/ write "
        "refusal — which is the sharpest edge of the #196 trade and the reason "
        "the entry exists"
    )
    assert "without a confirmation" in claim or "no confirmation" in claim


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


def test_the_model_is_not_told_a_delegated_agent_is_always_read_only() -> None:
    """The `agent` tool's own description — the text the MODEL reads.

    It said "A delegated agent is READ-ONLY unless the user explicitly approves
    more at a prompt". Under a YOLO parent nobody is prompted and the child is
    not read-only, so that sentence became false for the audience most likely to
    act on it: a model that believes its children cannot write will not reason
    about what they might do.

    Gated because a sabotage put the old sentence back and 1554 tests stayed
    green. Asserted on the CLAIM — the description must not promise that a
    prompt is the only way a child gets authority.
    """

    from aelix_agents.tool import _DESCRIPTION_HEAD

    text = _DESCRIPTION_HEAD
    assert "READ-ONLY unless the user explicitly approves more at a prompt" not in text, (
        "the agent tool tells the model a prompt is the only route to authority; "
        "under yolo there is no prompt and the child can write"
    )
    assert "READ-ONLY" in text, "the read-only default is still the default"
    assert "posture" in text, (
        "the description no longer names the parent's posture as the other way "
        "a child gets authority"
    )


def test_the_beta_changelog_entry_names_the_yolo_exception() -> None:
    """The `0.1.0-beta.1` section is what a beta user reads about consent.

    It states, correctly, that the dialog appears only when write authority is
    at stake — and that sentence needs the exception beside it, because for a
    YOLO user the dialog does not appear at all. Corrected in place rather than
    superseded: that section has not shipped, which is the convention `92b3f35`
    set.
    """

    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    beta = text.split("## [0.1.0-beta.1]", 1)[1]
    anchor = "The delegation consent dialog now appears only when write authority is"
    assert anchor in beta, "the entry this exception belongs to has moved or gone"
    entry = beta.split(anchor, 1)[1].split("\n- ", 1)[0]
    assert "yolo" in entry, (
        "the beta.1 consent entry no longer names the yolo exception, so it "
        "promises a dialog that a yolo user never sees"
    )
    assert "#196" in entry
