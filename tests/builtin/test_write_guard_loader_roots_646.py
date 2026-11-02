"""A write into a folder the loader reads is not auto-accepted, whatever the link is called (#646).

``_is_auto_allowable_write`` judged folder NAMES on the write's own path, in its
lexical and its resolved spelling. A link with another name that points INTO
``.agents`` or ``.aelix`` was refused, but a link NAMED ``.agents``,
``.agents/skills`` or ``.aelix`` that points at an ordinary folder was not seen
from the other side. Measured on ``758276ff`` through the real gate
(``auto-accept-edits`` and ``auto``, with and without a UI), the write tool and
the trusted catalog, every one of these was written and loaded::

    .agents/skills -> ../.claude/skills   .claude/skills/planted-a/SKILL.md   project skill
    .agents -> tooling                    tooling/skills/planted-d/SKILL.md   project skill
    .aelix -> config                      config/skills/planted-c/SKILL.md    project skill
    .aelix -> config                      config/extensions/x.py              next launch runs it
    ~/.agents/skills -> ~/dotfiles/skills skills/planted-f/SKILL.md           global skill, every project
    .aelix/settings.json "skills"         docs/skills/planted-e/SKILL.md      project skill

The owner's decision (2026-11-03) blocks the whole class: a write whose real
path is under the real path of a folder or file the loader reads asks, and a
delegated child without a UI is refused it. ``--skill`` paths are left out.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_agent_core.types import AgentContext
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.builtin import permission
from aelix_coding_agent.builtin.permission import (
    PermissionExtension,
    _is_auto_allowable_write,
    evaluate_tool_call,
    posture_decision,
)
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.config import ENV_AGENT_DIR
from aelix_coding_agent.cli.entry import _load_skill_catalog
from aelix_coding_agent.tools.write import create_write_tool

from tests.env_sandbox import sandbox_home

_POSIX_LINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch home whose ``~/.aelix/agent`` is the agent directory."""

    monkeypatch.delenv(ENV_AGENT_DIR, raising=False)
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)
    path = sandbox_home(monkeypatch, tmp_path / "home")
    path.mkdir()
    return path


@pytest.fixture
def repo(tmp_path: Path, home: Path) -> Path:
    path = tmp_path / "repo"
    (path / ".git").mkdir(parents=True)
    (path / "src").mkdir()
    return path


def _ctx(**fields: object) -> Any:
    """The two fields the gate reads from its ``ExtensionContext``."""

    return SimpleNamespace(**fields)


def _manager(cwd: Path, *, trusted: bool = True) -> SettingsManager:
    from aelix_coding_agent.cli.config import get_agent_dir

    return SettingsManager.create(
        cwd=str(cwd), agent_dir=Path(get_agent_dir()), project_trusted=trusted
    )


# ============================================================
# The links the issue measured
# ============================================================


@_POSIX_LINKS
def test_a_link_named_agents_skills_guards_its_target(repo: Path) -> None:
    (repo / ".claude" / "skills").mkdir(parents=True)
    (repo / ".agents").mkdir()
    (repo / ".agents" / "skills").symlink_to("../.claude/skills")

    assert _is_auto_allowable_write(".claude/skills/planted/SKILL.md", str(repo)) is False
    assert _is_auto_allowable_write(".claude/skills", str(repo)) is False
    # The sibling the link does not cover is ordinary.
    assert _is_auto_allowable_write(".claude/notes.md", str(repo)) is True


@_POSIX_LINKS
def test_a_link_named_agents_guards_a_skills_folder_that_does_not_exist_yet(repo: Path) -> None:
    """The write would CREATE ``tooling/skills``: the root is anchored where it stops existing."""

    (repo / "tooling").mkdir()
    (repo / ".agents").symlink_to("tooling")

    assert _is_auto_allowable_write("tooling/skills/planted/SKILL.md", str(repo)) is False
    assert _is_auto_allowable_write("tooling/README.md", str(repo)) is True


@_POSIX_LINKS
def test_a_dangling_agents_link_guards_the_folder_it_names(repo: Path) -> None:
    (repo / ".agents").symlink_to("tooling")

    assert _is_auto_allowable_write("tooling/skills/planted/SKILL.md", str(repo)) is False


@_POSIX_LINKS
def test_a_missing_part_of_a_root_is_compared_in_any_case(repo: Path) -> None:
    """On a case-insensitive volume ``Tooling/skills`` IS ``tooling/skills`` once made.

    The folded comparison asks on a case-sensitive one too, which is the safe side.
    """

    (repo / ".agents").symlink_to("tooling")

    assert _is_auto_allowable_write("Tooling/Skills/planted/SKILL.md", str(repo)) is False


@_POSIX_LINKS
@pytest.mark.parametrize(
    "path",
    [
        "config/skills/planted/SKILL.md",
        "config/extensions/x.py",
        "config/settings.json",
        "config/agents/planted.md",
        "config/prompt-templates/planted.md",
        "config/mcp.json",
        "config",
    ],
)
def test_a_link_named_aelix_guards_all_of_its_target(repo: Path, path: str) -> None:
    (repo / "config").mkdir()
    (repo / ".aelix").symlink_to("config")

    assert _is_auto_allowable_write(path, str(repo)) is False


@_POSIX_LINKS
def test_a_users_agents_skills_link_into_the_cwd_guards_it(home: Path) -> None:
    """The dotfiles case: a GLOBAL skill every project loads, ``--no-approve`` or not."""

    dotfiles = home / "dotfiles"
    (dotfiles / "skills").mkdir(parents=True)
    (dotfiles / ".git").mkdir()
    (home / ".agents").mkdir()
    (home / ".agents" / "skills").symlink_to(dotfiles / "skills")

    assert _is_auto_allowable_write("skills/planted/SKILL.md", str(dotfiles)) is False
    # The rest of the dotfiles repo is ordinary — the prompt cost stops at the folder.
    assert _is_auto_allowable_write("zshrc.d/aliases.sh", str(dotfiles)) is True


@_POSIX_LINKS
def test_a_cwd_inside_a_read_folder_asks_for_every_write(home: Path) -> None:
    dotfiles = home / "dotfiles"
    (dotfiles / "skills" / "mine").mkdir(parents=True)
    (home / ".agents").mkdir()
    (home / ".agents" / "skills").symlink_to(dotfiles / "skills")

    assert _is_auto_allowable_write("SKILL.md", str(dotfiles / "skills" / "mine")) is False


@_POSIX_LINKS
def test_a_global_aelix_link_into_the_cwd_guards_the_agent_dir(home: Path) -> None:
    dotfiles = home / "dotfiles"
    (dotfiles / "aelix" / "agent").mkdir(parents=True)
    (home / ".aelix").symlink_to(dotfiles / "aelix")

    for path in (
        "aelix/agent/extensions/x.py",
        "aelix/agent/skills/planted/SKILL.md",
        "aelix/agent/settings.json",
        "aelix/agent/trust.json",
    ):
        assert _is_auto_allowable_write(path, str(dotfiles)) is False, path
    assert _is_auto_allowable_write("README.md", str(dotfiles)) is True


def test_an_agent_dir_moved_into_the_cwd_is_guarded(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``AELIX_CODING_AGENT_DIR`` without a ``.aelix`` name anywhere in it."""

    monkeypatch.setenv(ENV_AGENT_DIR, str(repo / "agent-home"))

    assert _is_auto_allowable_write("agent-home/extensions/x.py", str(repo)) is False


@_POSIX_LINKS
def test_an_ancestor_agents_link_up_to_the_git_root_is_guarded(repo: Path) -> None:
    pkg = repo / "packages" / "app"
    pkg.mkdir(parents=True)
    (repo / "shared-skills").mkdir()
    (repo / ".agents").mkdir()
    (repo / ".agents" / "skills").symlink_to(repo / "shared-skills")

    assert _is_auto_allowable_write("../../shared-skills/x/SKILL.md", str(pkg)) is False


# ============================================================
# The settings paths (#511) — read through the manager the gate is given
# ============================================================


def test_a_project_settings_skills_path_is_guarded(repo: Path) -> None:
    (repo / ".aelix").mkdir()
    (repo / ".aelix" / "settings.json").write_text(
        json.dumps({"skills": ["../docs/skills"], "prompts": ["../docs/prompts", "!x*"]})
    )
    (repo / "docs" / "skills").mkdir(parents=True)

    for manager in (_manager(repo), None):
        assert _is_auto_allowable_write("docs/skills/x/SKILL.md", str(repo), manager) is False
        assert _is_auto_allowable_write("docs/prompts/p.md", str(repo), manager) is False
        assert _is_auto_allowable_write("docs/guide.md", str(repo), manager) is True


def test_an_untrusted_projects_settings_paths_are_guarded_for_its_child(repo: Path) -> None:
    """A delegated child runs ``--no-approve`` wherever this file exists, so its manager
    never opens it — and the TRUSTED parent loads the path on its next ``/reload``.
    The gate reads the two lists itself, whatever the trust.
    """

    (repo / ".aelix").mkdir()
    (repo / ".aelix" / "settings.json").write_text(json.dumps({"skills": ["../docs/skills"]}))
    manager = _manager(repo, trusted=False)
    assert manager.read_list("project", "skills") is None  # ADR-0252 §A, unchanged

    assert _is_auto_allowable_write("docs/skills/x/SKILL.md", str(repo), manager) is False


def test_the_old_custom_directories_form_is_read(repo: Path) -> None:
    (repo / ".aelix").mkdir()
    (repo / ".aelix" / "settings.json").write_text(
        json.dumps({"skills": {"customDirectories": ["../old-skills"]}})
    )

    assert _is_auto_allowable_write("old-skills/x/SKILL.md", str(repo)) is False


@pytest.mark.skipif(sys.platform == "win32", reason="FIFOs are POSIX")
def test_a_fifo_named_settings_json_does_not_block_and_names_nothing(repo: Path) -> None:
    assert sys.platform != "win32"
    (repo / ".aelix").mkdir()
    os.mkfifo(repo / ".aelix" / "settings.json")

    assert _is_auto_allowable_write("src/x.py", str(repo)) is True


def test_a_directory_named_settings_json_names_nothing(repo: Path) -> None:
    (repo / ".aelix" / "settings.json").mkdir(parents=True)

    assert _is_auto_allowable_write("src/x.py", str(repo)) is True


def test_a_project_settings_file_over_the_read_limit_asks(repo: Path) -> None:
    (repo / ".aelix").mkdir()
    padding = " " * (permission._PROJECT_SETTINGS_READ_LIMIT + 1)
    (repo / ".aelix" / "settings.json").write_text("{}" + padding)

    assert _is_auto_allowable_write("src/x.py", str(repo)) is False


def test_a_global_settings_skills_path_is_guarded(home: Path, repo: Path) -> None:
    agent = home / ".aelix" / "agent"
    agent.mkdir(parents=True)
    (agent / "settings.json").write_text(json.dumps({"skills": [str(repo / "team-skills")]}))

    assert _is_auto_allowable_write("team-skills/x/SKILL.md", str(repo), _manager(repo)) is False


def test_a_global_settings_file_moved_into_the_cwd_is_guarded(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(repo / "conf" / "aelix.json"))

    assert _is_auto_allowable_write("conf/aelix.json", str(repo), _manager(repo)) is False


def test_a_path_added_on_disk_after_launch_is_guarded(repo: Path) -> None:
    """Recomputed on every decision: ``/reload`` and the next launch read the disk."""

    manager = _manager(repo)
    assert _is_auto_allowable_write("later/x/SKILL.md", str(repo), manager) is True

    (repo / ".aelix").mkdir()
    (repo / ".aelix" / "settings.json").write_text(json.dumps({"skills": ["../later"]}))

    assert _is_auto_allowable_write("later/x/SKILL.md", str(repo), manager) is False


def test_a_settings_file_that_does_not_parse_names_nothing(repo: Path) -> None:
    (repo / ".aelix").mkdir()
    (repo / ".aelix" / "settings.json").write_text('{"skills": ["../docs/skills"')

    assert _is_auto_allowable_write("docs/skills/x/SKILL.md", str(repo), _manager(repo)) is True


def test_a_settings_file_that_cannot_be_read_asks(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(repo)

    def unreadable(scope: str, key: str) -> object:
        raise PermissionError(13, "Permission denied", "settings.json")

    monkeypatch.setattr(manager, "read_list", unreadable)

    assert _is_auto_allowable_write("src/x.py", str(repo), manager) is False


# ============================================================
# Controls, and "no proof"
# ============================================================


def test_an_ordinary_write_stays_auto_allowable(repo: Path) -> None:
    assert _is_auto_allowable_write("src/x.py", str(repo), _manager(repo)) is True


def test_a_skill_path_given_with_dash_dash_skill_stays_auto_allowable(repo: Path) -> None:
    """``--skill`` is what the user named for this run; the gate never takes it."""

    (repo / "myskills" / "x").mkdir(parents=True)
    (repo / "myskills" / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: mine\n---\nbody\n"
    )
    parsed = Args()
    parsed.skills = ["myskills"]
    names = {s.name for s in _load_skill_catalog(parsed, str(repo), True, _manager(repo)).skills}
    assert "x" in names  # the run reads it ...

    assert _is_auto_allowable_write("myskills/x/SKILL.md", str(repo), _manager(repo)) is True


def test_a_folder_that_cannot_be_looked_at_asks(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = permission._identity

    def guarded(path: str) -> object:
        if path.endswith(os.path.join(".agents", "skills")):
            raise PermissionError(13, "Permission denied", path)
        return real(path)

    monkeypatch.setattr(permission, "_identity", guarded)

    assert _is_auto_allowable_write("src/x.py", str(repo)) is False


def test_a_nul_in_the_path_asks_instead_of_raising(repo: Path) -> None:
    assert _is_auto_allowable_write("src/x\x00.py", str(repo)) is False


@_POSIX_LINKS
def test_a_re_pointed_link_is_judged_by_its_new_target(repo: Path) -> None:
    (repo / "a").mkdir()
    (repo / "b").mkdir()
    (repo / ".agents").mkdir()
    link = repo / ".agents" / "skills"
    link.symlink_to(repo / "a")
    assert _is_auto_allowable_write("a/x/SKILL.md", str(repo)) is False
    assert _is_auto_allowable_write("b/x/SKILL.md", str(repo)) is True

    link.unlink()
    link.symlink_to(repo / "b")

    assert _is_auto_allowable_write("a/x/SKILL.md", str(repo)) is True
    assert _is_auto_allowable_write("b/x/SKILL.md", str(repo)) is False


# ============================================================
# Every surface that shares the guard
# ============================================================


@_POSIX_LINKS
@pytest.mark.parametrize("mode", [PermissionMode.AUTO_ACCEPT, PermissionMode.AUTO])
def test_posture_and_rpc_evaluate_leave_it_to_an_approver(repo: Path, mode: PermissionMode) -> None:
    """``write`` and ``edit`` are both the ``write`` provenance; RPC ``permissions/evaluate`` agrees."""

    (repo / "tooling").mkdir()
    (repo / ".agents").symlink_to("tooling")
    args = {"path": "tooling/skills/x/SKILL.md", "content": "x"}

    assert posture_decision(mode, "write", args, lambda: str(repo), lambda a: "ask") is None
    assert evaluate_tool_call(mode, "write", args, str(repo), settings=_manager(repo)) == "ask"
    control = {"path": "src/x.py", "content": "x"}
    assert evaluate_tool_call(mode, "write", control, str(repo), settings=_manager(repo)) == "allow"


class _Runner:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def __call__(self, request: object) -> object:
        from aelix_coding_agent.tui.approval_dialog import ApprovalDecision

        self.requests.append(request)
        return ApprovalDecision.NO


@_POSIX_LINKS
@pytest.mark.parametrize("mode", [PermissionMode.AUTO_ACCEPT, PermissionMode.AUTO])
async def test_end_to_end_the_gate_asks_blocks_a_child_and_nothing_loads(
    repo: Path, mode: PermissionMode
) -> None:
    """Real gate, real write tool, trusted catalog — the issue's measurement, after."""

    (repo / "tooling").mkdir()
    (repo / ".agents").symlink_to("tooling")
    (repo / ".aelix").mkdir()
    (repo / ".aelix" / "settings.json").write_text(json.dumps({"skills": ["../docs/skills"]}))
    (repo / "docs" / "skills").mkdir(parents=True)
    manager = _manager(repo)
    write = create_write_tool(str(repo))
    context = AgentContext(tools=[write])

    for target in ("tooling/skills/planted/SKILL.md", "docs/skills/planted/SKILL.md"):
        args = {"path": target, "content": "---\nname: planted\ndescription: P\n---\nbody\n"}
        event = ToolCallHookEvent(tool_call_id="t", tool_name="write", args=args, context=context)
        child = PermissionExtension(
            posture=PermissionPosture(mode=mode),
            headless_default="block",
            settings=manager,
        )
        verdict = await child._on_tool_call(event, _ctx(cwd=str(repo), has_ui=False))
        assert verdict is not None and verdict.block is True, target

        runner = _Runner()
        interactive = PermissionExtension(
            posture=PermissionPosture(mode=mode),
            approval_runner=runner,
            settings=manager,
        )
        verdict = await interactive._on_tool_call(
            event, _ctx(cwd=str(repo), has_ui=True, ui=SimpleNamespace())
        )
        assert len(runner.requests) == 1, target
        assert verdict is not None and verdict.block is True

    names = {s.name for s in _load_skill_catalog(Args(), str(repo), True, manager).skills}
    assert "planted" not in names

    # The control is written unasked, as before.
    control = ToolCallHookEvent(
        tool_call_id="c",
        tool_name="write",
        args={"path": "src/x.py", "content": "x"},
        context=context,
    )
    child = PermissionExtension(
        posture=PermissionPosture(mode=mode), headless_default="block", settings=manager
    )
    assert await child._on_tool_call(control, _ctx(cwd=str(repo), has_ui=False)) is None
