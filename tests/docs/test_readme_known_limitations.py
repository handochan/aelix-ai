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

`README.ko.md` said the same. Both clauses were false, and #188 only found the
second one. Both subsystems identified "mutating" by a fixed frozenset of BARE
tool names, and every tool that does not come from the built-in set registers
under a different one — MCP prefixes with its server (`adapter.py`:
``qualified = f"{name_prefix}__{tool.name}"``), so `write_file` arrives as
`fs__write_file` and matched nothing.

THE TURN THIS FILE WAS WAITING FOR. Its first tests asserted that defect,
deliberately, so that fixing #188 would fail them. ADR-0253 fixed the
PERMISSION half — the gate now keys on the provenance of the tool object, so
anything Aelix did not build is mutating — and the READMEs were rewritten in the
same commit. The GUARDRAIL half is unchanged and is still a stated limitation;
:func:`test_no_guardrail_rule_applies_to_every_tool` keeps pinning it, and
:func:`test_the_guardrail_goes_by_bare_name_whatever_registered_the_tool` pins
what the READMEs now say about it (#188 round 1: the first rewrite said it
looked "only at the built-in" tools, and an extension's ``shell`` is checked).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
READMES = ("README.md", "README.ko.md")


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


# === the claim, in code ====================================================


async def test_plan_blocks_an_mcp_tool_on_the_headless_path() -> None:
    """The measurement behind "`--permission-mode plan` does hold there".

    The real gate at PLAN with no UI, handed an MCP tool built by the real
    adapter (``fs__write_file``) inside a turn context that also holds the
    built-ins — the shape the loop hands the hook. On ``aab1f210`` this
    returned ``None``: allowed.
    """

    import mcp.types as mcp_types
    from aelix_agent_core.harness.hooks import ToolCallHookEvent
    from aelix_agent_core.types import AgentContext
    from aelix_coding_agent.builtin.permission import PermissionExtension
    from aelix_coding_agent.builtin.permission_mode import (
        PermissionMode,
        PermissionPosture,
    )
    from aelix_coding_agent.mcp.adapter import mcp_tool_to_agent_tool
    from aelix_coding_agent.tools import create_all_tools

    mcp_tool = mcp_tool_to_agent_tool(
        object(),  # type: ignore[arg-type] — never called: the gate refuses first
        mcp_types.Tool(name="write_file", inputSchema={"type": "object"}),
        name_prefix="fs",
    )
    tools = [*create_all_tools("/proj").values(), mcp_tool]
    event = ToolCallHookEvent(
        tool_call_id="t1",
        tool_name="fs__write_file",
        args={"path": "a.txt", "content": "x"},
        context=AgentContext(tools=tools),
    )

    class _Headless:
        has_ui = False
        ui = None
        cwd = "/proj"

    perm = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.PLAN))
    result = await perm._on_tool_call(event, _Headless())  # type: ignore[arg-type]
    assert result is not None and result.block


def test_no_guardrail_rule_applies_to_every_tool() -> None:
    """`applies_to_tools=None` would mean "any tool" — and there is none.

    This is the assertion that made the first clause of the old README
    sentence false rather than merely imprecise, and it is what the current
    README still says: the hard-deny patterns go by a fixed set of bare tool
    names, so an MCP tool is not checked. One rule with `None` here and that
    sentence goes stale.
    """

    from aelix_coding_agent.builtin.guardrail import GuardrailExtension

    ext = GuardrailExtension()
    rules = ext._active_rules()  # noqa: SLF001 — the gate is about internals
    assert rules, "no rules at all — the fixture, not the product, is broken"
    assert all(rule.applies_to_tools is not None for rule in rules)


def test_the_guardrail_goes_by_bare_name_whatever_registered_the_tool() -> None:
    """What the READMEs say: by NAME, not by what built the tool.

    An extension tool named ``shell`` is checked (it is not aelix's, and the
    guardrail blocks its ``rm -rf`` anyway); an MCP tool is not, because the
    manager always names it ``<server>__<tool>``. Decision only: nothing runs.
    """

    from aelix_agent_core.harness.hooks import ToolCallHookEvent
    from aelix_coding_agent.builtin.guardrail import GuardrailExtension

    ext = GuardrailExtension()

    def _blocked(name: str, args: dict[str, str]) -> bool:
        event = ToolCallHookEvent(tool_call_id="t1", tool_name=name, args=args)
        result = ext._on_tool_call(event, None)  # type: ignore[arg-type]  # noqa: SLF001
        return result is not None and result.block

    assert _blocked("shell", {"command": "rm -rf fake-dir"})
    assert _blocked("write_file", {"path": ".env", "content": "x"})
    assert not _blocked("fs__shell", {"command": "rm -rf fake-dir"})
    assert not _blocked("fs__write_file", {"path": ".env", "content": "x"})

    for name, phrase in (
        ("README.md", "go by bare tool name"),
        ("README.ko.md", "이름만 보고"),
    ):
        text = _read(name)
        assert phrase in text, f"{name} no longer says the guardrail goes by name"
        assert "only look at the built-in shell" not in text
        assert "내장 셸·파일 툴만 봅니다" not in text


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
        # The pre-ADR-0253 wording: true on `aab1f210`, false after #188.
        "reaches neither `GuardrailExtension` nor the",
        "`--permission-mode plan` 차단에도 걸리지 않습니다",
    ):
        assert false_claim not in text, f"{name} still claims: {false_claim}"


@pytest.mark.parametrize("name", READMES)
def test_the_limitations_section_names_every_open_issue_it_describes(
    name: str,
) -> None:
    """A limitation the reader cannot look up is a limitation they cannot track.

    #137 and #138 were absent entirely; #188 was the defect the section
    described without ever naming. #260 joined the list for `v0.1.0-beta.2`:
    a silent tail loss is the one limitation a reader cannot notice unaided.
    """

    text = _read(name)
    for issue in (188, 137, 138, 14, 110, 260):
        assert f"issues/{issue}" in text, f"{name} does not link #{issue}"
    # The fix's record, so a reader can see what "plan holds" rests on.
    assert "decisions/0253-" in text, f"{name} does not link ADR-0253"


@pytest.mark.parametrize("name", READMES)
def test_the_limitation_count_matches_the_bullets(name: str) -> None:
    """The intro says how many there are; miscounting it is the cheapest
    possible way for this section to start lying again."""

    text = _read(name)
    if name == "README.md":
        section = text.split("## Known limitations (beta)", 1)[1]
        assert "Six things worth knowing" in section
    else:
        section = text.split("## 알려진 한계 (베타)", 1)[1]
        assert "여섯 가지입니다" in section
    section = section.split("\n## ", 1)[0]
    # Bullets are `**Bold lead-in.**` at the start of a line.
    bullets = [line for line in section.splitlines() if line.startswith("**")]
    assert len(bullets) == 6, [b[:40] for b in bullets]
