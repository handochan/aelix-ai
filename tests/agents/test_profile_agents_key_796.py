"""#796 (ADR-0304 D4) — a profile's ``agents:`` frontmatter key.

Three-valued like ``tools:``: absent or null → ``None`` (every profile that is
on), ``[]`` or ``""`` → ``()`` (delegates to none), names → those names. Each
entry is a profile NAME by ``_validate_name``'s four rules; an entry that is
not one is an error and the profile does not load. Globs get their own message.
"""

from __future__ import annotations

import dataclasses

import pytest
from aelix_coding_agent.agents.profile import AgentProfile, parse_profile

_BODY = "You are the agent."


def _parse(agents_line: str | None, *, name: str = "orchestrator", scope: str = "user"):
    extra = f"{agents_line}\n" if agents_line is not None else ""
    content = f"---\nname: {name}\ndescription: d\n{extra}---\n\n{_BODY}\n"
    return parse_profile(content, file_path=f"/abs/{name}.md", scope=scope)  # type: ignore[arg-type]


def _agents(agents_line: str | None) -> tuple[str, ...] | None:
    result = _parse(agents_line)
    assert result.profile is not None, [d.message for d in result.diagnostics]
    return result.profile.agents


@pytest.mark.parametrize(
    ("line", "want"),
    [
        (None, None),
        ("agents:", None),
        ("agents: null", None),
        ("agents: []", ()),
        ('agents: ""', ()),
        ("agents: [explorer, reviewer]", ("explorer", "reviewer")),
        ("agents: explorer, reviewer", ("explorer", "reviewer")),
        ("agents: [' explorer ', '', reviewer]", ("explorer", "reviewer")),
        ("agents: [reviewer, explorer, reviewer]", ("reviewer", "explorer")),
        ("agents: [orchestrator]", ("orchestrator",)),
    ],
)
def test_the_three_values_and_the_list_forms(
    line: str | None, want: tuple[str, ...] | None
) -> None:
    assert _agents(line) == want


def test_the_key_is_known_so_it_draws_no_unknown_key_warning() -> None:
    result = _parse("agents: [explorer]")
    assert not [d for d in result.diagnostics if d.code == "unknown_field"]


@pytest.mark.parametrize(
    "entry",
    ["Explorer", "a_b", "-explorer", "explorer-", "ex--plorer", "x" * 65],
)
def test_an_entry_that_is_not_a_profile_name_fails_the_profile(entry: str) -> None:
    result = _parse(f"agents: ['{entry}']")
    assert result.profile is None
    messages = [d.message for d in result.diagnostics if d.type == "error"]
    assert any(m.startswith(f"agents: entry {entry!r} is not a profile name: ") for m in messages)


@pytest.mark.parametrize("entry", ["expl*", "a?", "[a]", "{a,b}", "!explorer"])
def test_a_glob_gets_its_own_message(entry: str) -> None:
    result = _parse(f"agents: ['{entry}']")
    assert result.profile is None
    errors = [d.message for d in result.diagnostics if d.type == "error"]
    assert errors == [
        f"agents: entries are profile names; {entry!r} is not one (globs are not supported)"
    ]


@pytest.mark.parametrize(
    "line", ["agents: 5", "agents: [[explorer]]", "agents: {explorer: true}", "agents: [5]"]
)
def test_a_non_string_value_fails_the_profile(line: str) -> None:
    result = _parse(line)
    assert result.profile is None
    assert any(d.message.startswith("agents") for d in result.diagnostics if d.type == "error")


def test_a_project_profile_may_carry_it() -> None:
    result = _parse("agents: [explorer]", scope="project")
    assert result.profile is not None
    assert result.profile.agents == ("explorer",)


def test_rpc_agents_show_carries_it_through_asdict() -> None:
    result = _parse("agents: [explorer]")
    assert result.profile is not None
    assert dataclasses.asdict(result.profile)["agents"] == ("explorer",)
    assert dataclasses.asdict(_parse(None).profile)["agents"] is None  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["agents", "features", "skills", "tools", "inherit", "none"])
def test_no_profile_name_is_reserved(name: str) -> None:
    result = _parse(None, name=name)
    assert result.profile is not None and result.profile.name == name


def test_none_and_inherit_in_the_list_are_names_and_nothing_more() -> None:
    assert _agents("agents: [none, inherit]") == ("none", "inherit")


def test_the_field_defaults_to_none_for_a_profile_built_in_code() -> None:
    profile = AgentProfile(name="x", description="d", body="b", file_path="/x.md", scope="user")
    assert profile.agents is None
