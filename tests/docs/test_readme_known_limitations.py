"""#111 — the README's "Known limitations" section, pinned to the code.

A limitations section is the one part of a README that goes stale by being
FIXED. This file exists so that fixing one of the limitations turns a test red
and the prose gets updated in the same commit, rather than the README quietly
continuing to describe a defect that is gone — or, as happened here, continuing
to promise a guarantee that never held.

WHAT WAS WRONG. `README.md` said, of headless mode:

    Two guarantees survive: `GuardrailExtension` still hard-denies its
    patterns, and `--permission-mode plan` blocks every mutating tool on the
    headless path too.

`README.ko.md` said the same. Both clauses are false, and #188 only found the
second one. Both subsystems identify "mutating" by a fixed frozenset of BARE
tool names, and every tool that does not come from the built-in set registers
under a different one — MCP prefixes with its server (`adapter.py`:
``qualified = f"{name_prefix}__{tool.name}"``), so `write_file` arrives as
`fs__write_file` and matches nothing.

The tests below assert the DEFECT, deliberately. When #188 is fixed they fail,
which is the signal to rewrite both READMEs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
READMES = ("README.md", "README.ko.md")


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


# === the claim, in code ====================================================


def test_both_safety_nets_match_bare_tool_names_only() -> None:
    """The measurement the README wording is derived from.

    Not a paraphrase of #188: #188 asserts in its own Scope section that "the
    floor still holds — GuardrailExtension runs first, so catastrophic patterns
    are still hard-denied". That is refuted here. Every guardrail rule is scoped
    to the same bare-name sets the permission check uses, so on the MCP path
    NEITHER net is in the way.
    """

    from aelix_coding_agent.builtin.guardrail import _BASH_TOOLS, _WRITE_TOOLS
    from aelix_coding_agent.builtin.permission import _MUTATING

    assert _MUTATING == _BASH_TOOLS | _WRITE_TOOLS
    # Bare names, all of them. A qualified/prefixed spelling anywhere in here
    # would mean the sets had grown a namespace concept and this whole section
    # needs rereading.
    assert all("__" not in name for name in _MUTATING)
    for prefixed in ("fs__write_file", "filesystem__edit_file", "shell__execute_command"):
        assert prefixed not in _MUTATING


def test_no_guardrail_rule_applies_to_every_tool() -> None:
    """`applies_to_tools=None` would mean "any tool" — and there is none.

    This is the assertion that makes the first clause of the old README
    sentence false rather than merely imprecise. One rule with `None` here and
    "GuardrailExtension still hard-denies its patterns" would be defensible.
    """

    from aelix_coding_agent.builtin.guardrail import GuardrailExtension

    ext = GuardrailExtension()
    rules = ext._active_rules()  # noqa: SLF001 — the gate is about internals
    assert rules, "no rules at all — the fixture, not the product, is broken"
    assert all(rule.applies_to_tools is not None for rule in rules)


# === the claim, in prose ===================================================


@pytest.mark.parametrize("name", READMES)
def test_the_false_guarantee_sentence_is_gone(name: str) -> None:
    """Both languages. The Korean twin carried the identical claim and would
    have been missed by an English-only check."""

    text = _read(name)
    for false_claim in (
        "blocks every mutating tool on the headless path too",
        "헤드리스 경로에서도 모든 변경 툴을 차단합니다",
        "Two guarantees survive",
        "두 가지 보장은 남습니다",
    ):
        assert false_claim not in text, f"{name} still claims: {false_claim}"


@pytest.mark.parametrize("name", READMES)
def test_the_limitations_section_names_every_open_issue_it_describes(
    name: str,
) -> None:
    """A limitation the reader cannot look up is a limitation they cannot track.

    #137 and #138 were absent entirely; #188 was the defect the section
    described without ever naming.
    """

    text = _read(name)
    for issue in (188, 137, 138, 14, 110):
        assert f"issues/{issue}" in text, f"{name} does not link #{issue}"


@pytest.mark.parametrize("name", READMES)
def test_the_limitation_count_matches_the_bullets(name: str) -> None:
    """The intro says how many there are; miscounting it is the cheapest
    possible way for this section to start lying again."""

    text = _read(name)
    if name == "README.md":
        section = text.split("## Known limitations (beta)", 1)[1]
        assert "Five things worth knowing" in section
    else:
        section = text.split("## 알려진 한계 (베타)", 1)[1]
        assert "다섯 가지입니다" in section
    section = section.split("\n## ", 1)[0]
    # Bullets are `**Bold lead-in.**` at the start of a line.
    bullets = [line for line in section.splitlines() if line.startswith("**")]
    assert len(bullets) == 5, [b[:40] for b in bullets]
