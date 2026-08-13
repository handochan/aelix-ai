"""#158 — the profiles that ship in the wheel must actually be launchable.

`agents/builtin/explorer.md` declared ``tools: [read, grep, glob, list]``. There
is no ``glob`` tool and no ``list`` tool — the real names are ``find`` and
``ls`` — so ``--agent explorer`` failed at every launch:

    $ aelix --print --approve ... --agent explorer "hi"
    Agent profile: explorer (bundled: .../agents/builtin/explorer.md)
    Error: set_active_tools: unknown tool name(s): ['glob', 'list']
    EXIT=1

Nothing checked the shipped profiles against the shipped tools, which is why a
profile that could never run was packaged and released. That gap is the actual
defect; the two names are just its first instance.

These tests are deliberately about the BUNDLED tier only. A user's own profile
may legitimately name an extension tool that this build has no adapter for —
that case is annotated in ``/agents list`` and refused at ``use``, by design
(#155). A profile aelix itself ships has no such excuse.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from aelix_coding_agent.agents.discovery import builtin_agents_dir
from aelix_coding_agent.agents.profile import parse_profile
from aelix_coding_agent.tools import ALL_TOOL_NAMES


def _bundled() -> list[Path]:
    return sorted(builtin_agents_dir().glob("*.md"))


def test_there_are_bundled_profiles_to_check() -> None:
    """Guard the guard: if discovery silently returned nothing, every test below
    would pass vacuously — which is exactly how the broken profile survived."""

    assert _bundled(), f"no bundled profiles found in {builtin_agents_dir()}"


@pytest.mark.parametrize("path", _bundled(), ids=lambda p: p.stem)
def test_a_bundled_profile_parses(path: Path) -> None:
    result = parse_profile(
        path.read_text(encoding="utf-8"), file_path=str(path), scope="bundled"
    )
    assert result.profile is not None, f"{path.name} failed to parse: {result.diagnostics}"
    assert not [d for d in result.diagnostics if getattr(d, "type", "") == "error"]


@pytest.mark.parametrize("path", _bundled(), ids=lambda p: p.stem)
def test_a_bundled_profile_names_only_real_tools(path: Path) -> None:
    """THE #158 PIN — red against the shipped `explorer.md`.

    Asserted against ``ALL_TOOL_NAMES`` rather than a live registry on purpose:
    a bundled profile must work in a bare build with no extensions loaded, which
    is precisely the configuration a new user has.
    """

    profile = parse_profile(
        path.read_text(encoding="utf-8"), file_path=str(path), scope="bundled"
    ).profile
    assert profile is not None
    if profile.tools is None:
        return  # inherits the ambient set — nothing to check
    unknown = [name for name in profile.tools if name not in ALL_TOOL_NAMES]
    assert not unknown, (
        f"{path.name} declares tools this build has none of: {unknown}. "
        f"Valid built-ins: {sorted(ALL_TOOL_NAMES)}"
    )


@pytest.mark.parametrize("path", _bundled(), ids=lambda p: p.stem)
def test_a_bundled_profile_does_not_name_a_fictional_tool_in_its_prose(
    path: Path,
) -> None:
    """The softer half of the same defect, and it shipped in TWO files.

    ``explorer.md`` and ``general-purpose.md`` both told the model to use
    ``grep``/``glob``. Instructing a model to call a tool that does not exist is
    the same error as declaring it, minus the crash — it just wastes a turn
    instead of failing loudly. Only backticked words are considered, so ordinary
    prose ("a list of files") is not flagged.
    """

    body = path.read_text(encoding="utf-8")
    # A small deny-list rather than "every backticked word must be a tool":
    # the prose legitimately backticks field names, paths and settings.
    fictional = {"glob", "list", "bash_tool", "search", "edit_file"} - ALL_TOOL_NAMES
    hits = sorted(
        {
            word
            for word in re.findall(r"`([a-z_]+)`", body)
            if word in fictional
        }
    )
    assert not hits, (
        f"{path.name} tells the model to use tools that do not exist: {hits}"
    )
