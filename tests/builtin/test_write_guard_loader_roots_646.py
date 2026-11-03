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
from aelix_ai.tools import ToolExecutionContext
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


# ============================================================
# The path the tool opens (fix round 1, verify V1 and V2)
# ============================================================


class _Recorder:
    """The local write/edit operations, with the path each one was handed."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    async def mkdir(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    async def write_file(self, path: str, data: bytes) -> None:
        self.paths.append(path)
        Path(path).write_bytes(data)

    async def read_file(self, path: str) -> bytes:
        self.paths.append(path)
        return Path(path).read_bytes()

    async def access(self, path: str) -> bool:
        return os.access(path, os.R_OK | os.W_OK)


_TARGET_CORPUS = [
    "plain.txt",
    "@at.txt",
    "@sub/at2.txt",
    "~/home-file.txt",
    "@~/at-home.txt",
    "~",  # a folder: neither tool can write it, the gate still names it
    "@ABS/outside.txt",
    "ABS/outside2.txt",
    "cafe\u0301.txt",  # NFD
    "nb\u00a0sp.txt",
    "ideo\u3000sp.txt",
    "trailing/",
    "sub/../dotdot.txt",
    "sub/./dot.txt",
    "~other/tilde-user.txt",
]


@pytest.mark.parametrize("raw", _TARGET_CORPUS)
async def test_the_gate_judges_the_file_the_write_and_edit_tools_open(
    repo: Path, home: Path, tmp_path: Path, raw: str
) -> None:
    """The gate's target IS the tools' target, for every spelling the tools rewrite.

    Measured on ``98b9ea46``: the gate expanded with ``expanduser`` and joined
    the rest itself, the tools strip a leading ``@``, normalize to NFC and map
    unicode spaces (``tools/_path_utils.py`` ``expand_path``), so ``@x`` was
    judged as ``<cwd>/@x`` and written to ``<cwd>/x``.
    """

    from aelix_coding_agent.tools.edit import create_edit_tool

    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "sub").mkdir()
    path = raw.replace("ABS", str(outside))
    if path == "~":
        _cwd, lexical, real = permission._write_spellings(path, str(repo), permission._Budget())
        assert lexical == os.path.normpath(home)
        assert real == os.path.realpath(home)
        return

    _cwd, lexical, real = permission._write_spellings(path, str(repo), permission._Budget())
    recorder = _Recorder()
    write = create_write_tool(str(repo), {"operations": recorder})
    assert write.execute is not None
    result = await write.execute({"path": path, "content": "old"}, ToolExecutionContext(tool_call_id="w"))
    assert not result.is_error, result.content
    written = recorder.paths[-1]
    assert os.path.normpath(written) == lexical
    assert os.path.realpath(written) == real
    assert os.path.samefile(written, real)

    edit = create_edit_tool(str(repo), {"operations": recorder})
    assert edit.execute is not None
    result = await edit.execute(
        {"path": path, "edits": [{"oldText": "old", "newText": "new"}]},
        ToolExecutionContext(tool_call_id="e"),
    )
    assert not result.is_error, result.content
    assert os.path.realpath(recorder.paths[-1]) == real
    assert Path(real).read_text() == "new"


def test_a_path_the_tools_strip_an_at_from_is_judged_where_it_lands(
    repo: Path, home: Path, tmp_path: Path
) -> None:
    """Verify V1: every one of these was auto-accepted and written on ``98b9ea46``."""

    outside = tmp_path / "outside"
    outside.mkdir()

    assert _is_auto_allowable_write("@.aelix/extensions/at.py", str(repo)) is False
    assert _is_auto_allowable_write("@.agents/skills/v-at/SKILL.md", str(repo)) is False
    assert _is_auto_allowable_write(f"@{outside}/at-abs.txt", str(repo)) is False
    assert _is_auto_allowable_write("@~/Library/LaunchAgents/at-home.plist", str(repo)) is False
    assert _is_auto_allowable_write("@.ENV", str(repo)) is False
    # Still the project, and still allowed.
    assert _is_auto_allowable_write("@lib/z.py", str(repo)) is True
    assert _is_auto_allowable_write(f"@{repo}/lib/z.py", str(repo)) is True


@_POSIX_LINKS
@pytest.mark.parametrize(
    ("link", "points_at", "write"),
    [
        ("lnk", ".agents/skills/existing", "lnk/../v-dd/SKILL.md"),
        ("lnk2", ".aelix/extensions/sub", "lnk2/../dd.py"),
        ("lnk3", ".aelix/extensions/sub", "lnk3/../../settings.json"),
    ],
)
def test_a_dot_dot_after_a_link_is_judged_in_the_links_target(
    repo: Path, link: str, points_at: str, write: str
) -> None:
    """Verify V2: ``realpath(normpath(p))`` dropped the link before applying ``..``."""

    (repo / points_at).mkdir(parents=True)
    (repo / link).symlink_to(points_at)

    assert _is_auto_allowable_write(write, str(repo)) is False


@_POSIX_LINKS
def test_a_dot_dot_after_an_ordinary_link_stays_auto_allowable(repo: Path) -> None:
    (repo / "lib" / "sub").mkdir(parents=True)
    (repo / "lnk").symlink_to("lib/sub")

    assert _is_auto_allowable_write("lnk/../ok.py", str(repo)) is True


@_POSIX_LINKS
def test_the_bounded_resolver_agrees_with_realpath(tmp_path: Path) -> None:
    """``_realpath`` is ``os.path.realpath`` with a count, on every layout but a loop."""

    root = tmp_path / "r"
    (root / "a" / "b").mkdir(parents=True)
    (root / "rel").symlink_to("a/b")
    (root / "abs").symlink_to(root / "a")
    (root / "a" / "up").symlink_to("..")
    (root / "chain1").symlink_to("chain2")
    (root / "chain2").symlink_to("rel")
    (root / "dangling").symlink_to("nowhere/deeper")
    (root / "a" / "b" / "back").symlink_to("../../abs/b")
    spellings = [
        "rel/x",
        "rel/../x",
        "abs/b/../b/y",
        "a/up/a/up/rel",
        "chain1/../../r/chain1/z",
        "dangling/q",
        "dangling/../q",
        "missing/../rel/x",
        "a/b/back/back/back",
        "a//b/./x",
        "",
    ]
    for spelling in spellings:
        full = f"{root}/{spelling}"
        assert permission._realpath(full, permission._Budget()) == os.path.realpath(full), spelling


@_POSIX_LINKS
def test_a_chain_past_the_link_limit_asks(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify V4: ``realpath`` follows any number of links; the gate gives up and asks."""

    previous = "src"
    for n in range(permission._LINKS_PER_PATH + 1):
        (repo / f"l{n}").symlink_to(previous)
        previous = f"l{n}"

    assert _is_auto_allowable_write(f"{previous}/x.py", str(repo)) is False
    assert _is_auto_allowable_write("l10/x.py", str(repo)) is True
    with pytest.raises(OSError, match="links"):
        permission._realpath(f"{repo}/{previous}/x.py", permission._Budget())

    monkeypatch.setattr(permission, "_DECISION_BUDGET", 5)
    assert _is_auto_allowable_write("l10/x.py", str(repo)) is False


# ============================================================
# Links inside a root (fix round 1, Codex PC1)
# ============================================================


@_POSIX_LINKS
@pytest.mark.parametrize(
    ("link", "points_at", "write"),
    [
        (".agents/skills/x", "../../docs/x", "docs/x/SKILL.md"),
        (".aelix/skills/y", "../../lib/y", "lib/y/SKILL.md"),
        (".agents/skills/deep/inner", "../../../far", "far/z/SKILL.md"),
        (".agents/skills/solo/SKILL.md", "../../../notes/solo.md", "notes/solo.md"),
        (".aelix/extensions/e.py", "../../src/e.py", "src/e.py"),
        (".aelix/extensions/pkg/helper.py", "../../../src/helper.py", "src/helper.py"),
        (".aelix/settings.json", "../conf/settings.json", "conf/settings.json"),
    ],
)
def test_a_link_inside_a_root_guards_what_it_points_at(
    repo: Path, link: str, points_at: str, write: str
) -> None:
    """Measured on ``98b9ea46``: each write was auto-accepted, and the load read it through the link."""

    (repo / link).parent.mkdir(parents=True, exist_ok=True)
    (repo / link).symlink_to(points_at)

    assert _is_auto_allowable_write(write, str(repo)) is False
    # The folder next to the target is not read through anything.
    assert _is_auto_allowable_write("other/x.md", str(repo)) is True


@_POSIX_LINKS
def test_a_link_inside_a_link_target_is_followed_too(repo: Path) -> None:
    (repo / ".agents" / "skills").mkdir(parents=True)
    (repo / "docs" / "x").mkdir(parents=True)
    (repo / ".agents" / "skills" / "x").symlink_to("../../docs/x")
    (repo / "docs" / "x" / "next").symlink_to("../../far")

    assert _is_auto_allowable_write("far/y/SKILL.md", str(repo)) is False


@_POSIX_LINKS
def test_a_link_in_a_folder_the_load_does_not_list_is_not_a_root(repo: Path) -> None:
    """A config dir's own entries and its loaded families only: ``.aelix/notes`` is not read."""

    (repo / ".aelix" / "notes").mkdir(parents=True)
    (repo / ".aelix" / "notes" / "x").symlink_to("../../docs")
    (repo / ".agents" / "skills" / ".hidden").mkdir(parents=True)
    (repo / ".agents" / "skills" / ".hidden" / "y").symlink_to("../../../lib")

    assert _is_auto_allowable_write("docs/a.md", str(repo)) is True
    assert _is_auto_allowable_write("lib/b.py", str(repo)) is True


def test_a_large_folder_beside_the_loaded_ones_costs_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cwd of ``~``: ``~/.aelix/sessions`` holds thousands of files and is never listed."""

    sessions = home / ".aelix" / "sessions"
    sessions.mkdir(parents=True)
    for n in range(200):
        (sessions / f"s{n}.jsonl").touch()
    monkeypatch.setattr(permission, "_DECISION_BUDGET", 150)

    assert _is_auto_allowable_write("notes.txt", str(home)) is True


def test_a_root_too_large_to_list_asks(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skills = repo / ".agents" / "skills"
    skills.mkdir(parents=True)
    for n in range(20):
        (skills / f"s{n}").mkdir()

    assert _is_auto_allowable_write("src/x.py", str(repo)) is True
    monkeypatch.setattr(permission, "_DECISION_BUDGET", 15)
    assert _is_auto_allowable_write("src/x.py", str(repo)) is False


# ============================================================
# A hostile project file (fix round 1, verify V4 and V5)
# ============================================================


def test_a_project_list_past_the_cap_asks(repo: Path) -> None:
    (repo / ".aelix").mkdir()
    settings = repo / ".aelix" / "settings.json"
    limit = permission._PROJECT_LIST_LIMIT
    settings.write_text(json.dumps({"skills": [f"../d{n}" for n in range(limit)]}))
    assert _is_auto_allowable_write("src/x.py", str(repo)) is True

    settings.write_text(json.dumps({"prompts": [f"../d{n}" for n in range(limit + 1)]}))
    assert _is_auto_allowable_write("src/x.py", str(repo)) is False


class _Looked:
    """Every path ``stat`` / ``lstat`` / ``scandir`` / ``readlink`` / ``open`` was handed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paths: list[str] = []
        for name in ("stat", "lstat", "scandir", "readlink", "open"):
            real = getattr(os, name)

            def spy(path: Any, *args: Any, _real: Any = real, **kwargs: Any) -> Any:
                self.paths.append(os.fspath(path) if not isinstance(path, int) else "")
                return _real(path, *args, **kwargs)

            monkeypatch.setattr(os, name, spy)

    def touched(self, word: str) -> list[str]:
        return [p for p in self.paths if word in p]


@pytest.mark.parametrize(
    "entry",
    # A backslashed ``\\\\attacker\\share`` is relative to ``posixpath`` and so
    # cannot be measured here; :func:`test_what_counts_as_another_machine` has it.
    ["//attacker/share/skills", "//?/UNC/attacker/share", "//./pipe/attacker"],
)
def test_a_project_settings_path_on_another_machine_is_never_looked_at(
    repo: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """Verify V5 (Windows): ``stat`` of a UNC path sends the user's NTLM hash to its host.

    Measured with ``ntpath`` rules on a POSIX runner (``permission._NT``); the
    entry is refused before anything resolves it.
    """

    (repo / ".aelix").mkdir()
    (repo / ".aelix" / "settings.json").write_text(json.dumps({"skills": [entry]}))
    monkeypatch.setattr(permission, "_NT", True)
    looked = _Looked(monkeypatch)

    allowed = _is_auto_allowable_write("src/x.py", str(repo))
    monkeypatch.undo()

    assert allowed is False
    assert looked.touched("attacker") == []


def test_a_project_profile_path_on_another_machine_is_never_looked_at(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".aelix" / "agents").mkdir(parents=True)
    (repo / ".aelix" / "agents" / "p.md").write_text(
        "---\nname: p\ndescription: d\nskills: //attacker/share/s\n---\nbody\n"
    )
    monkeypatch.setattr(permission, "_NT", True)
    looked = _Looked(monkeypatch)

    allowed = _is_auto_allowable_write("src/x.py", str(repo))
    monkeypatch.undo()

    assert allowed is False
    assert looked.touched("attacker") == []


@_POSIX_LINKS
def test_a_link_in_a_root_to_another_machine_is_never_followed(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".agents" / "skills").mkdir(parents=True)
    (repo / ".agents" / "skills" / "x").symlink_to("//attacker/share/x")
    monkeypatch.setattr(permission, "_NT", True)
    looked = _Looked(monkeypatch)

    allowed = _is_auto_allowable_write("src/x.py", str(repo))
    monkeypatch.undo()

    assert allowed is False
    assert looked.touched("attacker") == []


@pytest.mark.parametrize(
    ("path", "cwd", "remote"),
    [
        ("\\\\attacker\\share\\x", "C:\\work", True),
        ("//attacker/share/x", "C:\\work", True),
        ("\\\\?\\C:\\x", "C:\\work", True),
        ("\\\\.\\pipe\\x", "C:\\work", True),
        ("\\\\?\\UNC\\attacker\\share\\x", "C:\\work", True),
        ("C:\\x", "C:\\work", False),
        ("D:\\x", "C:\\work", False),
        ("\\x", "C:\\work", False),
        ("relative\\x", "C:\\work", False),
        ("\\\\srv\\share\\skills", "\\\\srv\\share\\repo", False),
        ("//SRV/Share/skills", "\\\\srv\\share\\repo", False),
        ("\\\\other\\share\\skills", "\\\\srv\\share\\repo", True),
    ],
)
def test_what_counts_as_another_machine(
    monkeypatch: pytest.MonkeyPatch, path: str, cwd: str, remote: bool
) -> None:
    monkeypatch.setattr(permission, "_NT", True)

    assert permission._is_remote(path, cwd) is remote


def test_nothing_is_another_machine_off_windows() -> None:
    if permission._NT:
        pytest.skip("POSIX rule")
    assert permission._is_remote("//attacker/share/x", "/work") is False


# ============================================================
# A child in a subfolder (fix round 1, verify V3)
# ============================================================


@_POSIX_LINKS
def test_a_child_in_a_subfolder_guards_its_parents_config_dir(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify V3: ``agent(cwd='pkg')`` computed its roots from ``pkg`` alone."""

    (repo / "pkg" / "conf").mkdir(parents=True)
    (repo / ".aelix").symlink_to("pkg/conf")
    child_cwd = str(repo / "pkg")
    assert _is_auto_allowable_write("conf/extensions/sub2.py", child_cwd) is True

    env = permission.pass_loader_roots_to_child({}, str(repo))
    monkeypatch.setenv(permission.PARENT_ROOTS_ENV, env[permission.PARENT_ROOTS_ENV])

    assert _is_auto_allowable_write("conf/extensions/sub2.py", child_cwd) is False
    assert _is_auto_allowable_write("conf/skills/x/SKILL.md", child_cwd) is False
    assert _is_auto_allowable_write("lib/ok.py", child_cwd) is True


def test_a_child_in_a_subfolder_guards_its_parents_settings_paths(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".aelix").mkdir()
    (repo / ".aelix" / "settings.json").write_text(
        json.dumps({"skills": ["../docs/skills"], "prompts": ["../docs/prompts"]})
    )
    (repo / "docs").mkdir()
    child_cwd = str(repo / "docs")
    assert _is_auto_allowable_write("skills/v-sub/SKILL.md", child_cwd) is True

    env = permission.pass_loader_roots_to_child({}, str(repo))
    monkeypatch.setenv(permission.PARENT_ROOTS_ENV, env[permission.PARENT_ROOTS_ENV])

    assert _is_auto_allowable_write("skills/v-sub/SKILL.md", child_cwd) is False
    assert _is_auto_allowable_write("prompts/p.md", child_cwd) is False
    assert _is_auto_allowable_write("guide.md", child_cwd) is True


def test_the_record_is_kept_and_extended_for_a_grandchild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp = tmp_path / "servers.json"
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(mcp))
    first = permission.pass_loader_roots_to_child({}, str(tmp_path / "top"))
    monkeypatch.delenv("AELIX_MCP_CONFIG")
    second = permission.pass_loader_roots_to_child(dict(first), str(tmp_path / "top" / "mid"))

    assert permission._parent_record(second) == (
        [str(tmp_path / "top"), str(tmp_path / "top" / "mid")],
        [str(mcp)],
    )


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        "[]",
        '{"cwds": "/a"}',
        '{"cwds": ["relative"]}',
        '{"files": [1]}',
        json.dumps({"cwds": [f"/d{n}" for n in range(17)]}),
    ],
)
def test_a_record_aelix_did_not_write_asks_and_is_passed_on_as_it_is(
    repo: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(permission.PARENT_ROOTS_ENV, value)

    assert _is_auto_allowable_write("src/x.py", str(repo)) is False
    env = {permission.PARENT_ROOTS_ENV: value}
    assert permission.pass_loader_roots_to_child(env, str(repo)) == {permission.PARENT_ROOTS_ENV: value}


def test_a_project_env_file_cannot_write_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``AELIX_`` is a prefix a cwd ``.env`` cannot set; the record is not reopened by name."""

    from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv

    monkeypatch.delenv(permission.PARENT_ROOTS_ENV, raising=False)
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(f'{permission.PARENT_ROOTS_ENV}={{"cwds": ["/"]}}\nAELIX646_PROBE_API_KEY=k\n')
    load_dotenv(str(dotenv))
    leaked = os.environ.pop(permission.PARENT_ROOTS_ENV, None)
    # ``load_dotenv`` writes ``os.environ`` itself; the control proves it ran.
    control = os.environ.pop("AELIX646_PROBE_API_KEY", None)

    assert leaked is None
    assert control == "k"


def test_a_record_naming_another_machine_asks(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(permission, "_NT", True)
    monkeypatch.setenv(permission.PARENT_ROOTS_ENV, json.dumps({"cwds": ["//attacker/share"]}))

    assert _is_auto_allowable_write("src/x.py", str(repo)) is False


async def test_both_channels_hand_the_child_the_parents_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The print and rpc spawns, a first run and a resume alike, go through the same line."""

    from aelix_agents.print_channel import PrintChannel, RunningChild
    from aelix_agents.rpc_channel import RpcChannel

    from tests.agents_ext.test_rpc_channel import _plan, _print_stub, _rpc_stub

    body = (
        "def script(task):\n"
        "    start()\n"
        f"    say(os.environ.get({permission.PARENT_ROOTS_ENV!r}, 'missing'), input=1, output=1)\n"
        "    done()\n"
    )
    mcp = tmp_path / "servers.json"
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(mcp))
    (tmp_path / "sub").mkdir()
    plan = _plan(tmp_path, cwd=str(tmp_path / "sub"), parent_cwd=str(tmp_path))
    print_script = _print_stub(body)
    rpc_script = _rpc_stub(body)

    results = [
        await PrintChannel(
            argv_builder=lambda *a, **k: [sys.executable, "-c", print_script, "Task: t"]
        ).run(plan, child=RunningChild(id="sub-test", profile="scout")),
        await RpcChannel(argv_builder=lambda *a, **k: [sys.executable, "-c", rpc_script]).run(
            plan, child=RunningChild(id="sub-test", profile="scout")
        ),
    ]

    for result in results:
        assert result.ok is True, result
        assert json.loads(result.summary) == {"cwds": [str(tmp_path)], "files": [str(mcp)]}


# ============================================================
# More files the loader reads (fix round 1, verify V6, profile paths)
# ============================================================


def test_the_mcp_config_the_environment_names_is_guarded(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify V6: its servers run as commands at the next launch."""

    assert _is_auto_allowable_write("mcp.servers.json", str(repo)) is True
    monkeypatch.setenv("AELIX_MCP_CONFIG", "mcp.servers.json")
    monkeypatch.chdir(repo)

    assert _is_auto_allowable_write("mcp.servers.json", str(repo)) is False
    assert _is_auto_allowable_write("src/x.py", str(repo)) is True


def test_a_parents_mcp_config_is_guarded_in_its_child(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child's environment drops ``AELIX_MCP_CONFIG``; the record carries the file."""

    monkeypatch.setenv("AELIX_MCP_CONFIG", str(repo / "mcp.servers.json"))
    env = permission.pass_loader_roots_to_child({}, str(repo))
    monkeypatch.delenv("AELIX_MCP_CONFIG")
    monkeypatch.setenv(permission.PARENT_ROOTS_ENV, env[permission.PARENT_ROOTS_ENV])

    assert _is_auto_allowable_write("mcp.servers.json", str(repo)) is False


@pytest.mark.parametrize(
    "line",
    [
        "skills: [../../../work/team-skills]",
        "skills: WORK/team-skills",
        "skills: a, WORK/team-skills",
        "extensions: [WORK/team-skills/ext.py]",
    ],
)
def test_a_user_profiles_paths_are_guarded(home: Path, line: str) -> None:
    from aelix_coding_agent.cli.config import get_agent_dir

    work = home / "work"
    (work / "team-skills").mkdir(parents=True)
    agents = Path(get_agent_dir()) / "agents"
    agents.mkdir(parents=True)
    line = line.replace("WORK", str(work))
    (agents / "p.md").write_text(f"---\nname: p\ndescription: d\n{line}\n---\nbody\n")

    target = "team-skills/ext.py" if "extensions" in line else "team-skills/x/SKILL.md"
    assert _is_auto_allowable_write(target, str(work)) is False
    assert _is_auto_allowable_write("README.md", str(work)) is True


def test_a_project_profiles_skills_path_is_guarded_whatever_the_trust(repo: Path) -> None:
    (repo / ".aelix" / "agents").mkdir(parents=True)
    (repo / ".aelix" / "agents" / "p.md").write_text(
        "---\nname: p\ndescription: d\nskills: [../../docs/pskills]\n---\nbody\n"
    )

    assert _is_auto_allowable_write("docs/pskills/x/SKILL.md", str(repo)) is False
    assert _is_auto_allowable_write("docs/other.md", str(repo)) is True


def test_a_project_with_too_many_profiles_or_entries_asks(repo: Path) -> None:
    agents = repo / ".aelix" / "agents"
    agents.mkdir(parents=True)
    limit = permission._PROJECT_PROFILE_LIMIT
    for n in range(limit):
        (agents / f"p{n}.md").write_text(f"---\nname: p{n}\ndescription: d\n---\nb\n")
    assert _is_auto_allowable_write("src/x.py", str(repo)) is True

    (agents / "over.md").write_text("---\nname: over\ndescription: d\n---\nb\n")
    assert _is_auto_allowable_write("src/x.py", str(repo)) is False

    for path in agents.iterdir():
        path.unlink()
    entries = ", ".join(f"d{n}" for n in range(permission._PROJECT_LIST_LIMIT + 1))
    (agents / "wide.md").write_text(f"---\nname: wide\ndescription: d\nskills: [{entries}]\n---\nb\n")
    assert _is_auto_allowable_write("src/x.py", str(repo)) is False


def test_a_profile_that_does_not_parse_names_nothing(repo: Path) -> None:
    agents = repo / ".aelix" / "agents"
    agents.mkdir(parents=True)
    (agents / "deep.md").write_text("---\nskills: " + "[" * 5000 + "\n---\nb\n")
    (agents / "binary.md").write_bytes(b"---\nskills: \xff\xfe\n---\n")

    assert _is_auto_allowable_write("src/x.py", str(repo)) is True


@_POSIX_LINKS
@pytest.mark.parametrize("case", ["A", "B", "C"])
@pytest.mark.parametrize("mode", [PermissionMode.AUTO_ACCEPT, PermissionMode.AUTO])
async def test_verify_v13_a_link_inside_a_root_pointing_out_guards_its_target(
    repo: Path, case: str, mode: PermissionMode
) -> None:
    """Verify V13 (Codex PC1), the verifier's three cases, end to end.

    Measured on ``98b9ea46`` (``.omc/specs/646-verify-probes``' ``pc1.py``): a
    headless ``auto-accept-edits`` child was allowed each write, and the trusted
    catalog loaded A and C as project skills; B's target ran as the next
    launch's extension. The loader half is asserted here too, with the target
    written by the test itself, so each case is one the loader really reads.
    """

    from aelix_coding_agent.extensions.loader import _discover_in_dir

    if case == "A":
        (repo / ".agents" / "skills").mkdir(parents=True)
        (repo / "docs" / "x").mkdir(parents=True)
        (repo / ".agents" / "skills" / "x").symlink_to("../../docs/x")
        target, name = "docs/x/SKILL.md", "pc1a"
    elif case == "B":
        (repo / ".aelix" / "extensions").mkdir(parents=True)
        (repo / "vendor").mkdir()
        (repo / ".aelix" / "extensions" / "ext.py").symlink_to("../../vendor/ext.py")
        target, name = "vendor/ext.py", ""
    else:
        (repo / ".agents" / "skills").mkdir(parents=True)
        (repo / "pub").mkdir()
        (repo / ".agents" / "skills" / "y").symlink_to(str(repo / "pub"))
        target, name = "pub/SKILL.md", "pc1c"
    untrusted = _manager(repo, trusted=False)
    write = create_write_tool(str(repo))
    event = ToolCallHookEvent(
        tool_call_id="t",
        tool_name="write",
        args={"path": target, "content": "x"},
        context=AgentContext(tools=[write]),
    )

    child = PermissionExtension(
        posture=PermissionPosture(mode=mode), headless_default="block", settings=untrusted
    )
    verdict = await child._on_tool_call(event, _ctx(cwd=str(repo), has_ui=False))
    assert verdict is not None and verdict.block is True
    runner = _Runner()
    interactive = PermissionExtension(
        posture=PermissionPosture(mode=mode), approval_runner=runner, settings=untrusted
    )
    await interactive._on_tool_call(event, _ctx(cwd=str(repo), has_ui=True, ui=SimpleNamespace()))
    assert len(runner.requests) == 1
    assert evaluate_tool_call(mode, "write", event.args, str(repo), settings=untrusted) == "ask"

    # What the gate keeps from being written unasked IS read through the link.
    if case == "B":
        (repo / target).write_text("X = 1\n")
        found = [Path(p).resolve() for p in _discover_in_dir(repo / ".aelix" / "extensions")]  # type: ignore[arg-type]
        assert found == [(repo / target).resolve()]
    else:
        (repo / target).write_text(f"---\nname: {name}\ndescription: PC1\n---\nbody\n")
        names = {s.name for s in _load_skill_catalog(Args(), str(repo), True, _manager(repo)).skills}
        assert name in names
