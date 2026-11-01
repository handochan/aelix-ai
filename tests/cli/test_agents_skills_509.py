"""#509 (ADR-0281) — ``~/.agents/skills`` and the project's ``.agents/skills``.

pi reads both (``package-manager.ts`` ``collectAncestorAgentsSkillDirs``,
``addAutoDiscoveredResources``); aelix read neither, so a user whose skills were
written for another agent had none. What is pinned here:

* the walk: ``<dir>/.agents/skills`` from the cwd up to the git root (a ``.git``
  file counts), or the filesystem root outside a repository, nearest first;
* the user's own ``~/.agents/skills`` is GLOBAL, read once, never gated — also
  when the walk from a folder under the home directory reaches it;
* trust covers what the scan reads: the gate counts the same list, and an
  untrusted run reads none of it — the #115 hole for ``.aelix/skills``;
* precedence: ``--skill`` > project (``.aelix`` before ``.agents``, nearer before
  farther) > global (``~/.aelix/agent/skills`` before ``~/.agents/skills``) >
  bundled, the losers shadowed;
* the trust prompt and ``/trust`` name the directories, flattened;
* the ``/skill`` panel's groups, shadowed rows and untrusted rows;
* the loader's Agent Skills rule: root ``.md`` files are not skills, a grouped
  ``.md`` with a description is.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from aelix_agent_core.harness.skills import load_skills
from aelix_agents.trust import child_trust_argv
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import _load_skill_catalog, _resolve_skill_tiers
from aelix_coding_agent.cli.project_trust import (
    _one_row,
    agents_skill_dirs_phrase,
    format_project_trust_prompt,
    has_trust_requiring_project_resources,
    resolve_project_trusted,
    trust_selector_title,
)
from aelix_coding_agent.core.skill_dirs import (
    git_root,
    project_agents_skill_dirs,
    trust_requiring_agents_skill_dirs,
    user_agents_skills_dir,
)
from aelix_coding_agent.core.skill_selection import SkillCatalog, SkillKind

from tests.env_sandbox import sandbox_home

pytestmark = pytest.mark.usefixtures("own_agent_dir")

_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks and modes")
_UP = f"..{os.sep}"
_AELIX = os.path.join(".aelix", "skills")
_AGENTS = os.path.join(".agents", "skills")


def _skill(root: Path, name: str, description: str | None = None) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description or f'MARK-{name}'}\n---\nbody\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch home directory, so ``~/.agents/skills`` is the test's own."""

    path = sandbox_home(monkeypatch, tmp_path / "home")
    path.mkdir()
    return path


@pytest.fixture
def repo(tmp_path: Path, home: Path) -> Path:
    """``repo/.git``, ``.agents/skills`` at the root and at ``pkg``, cwd ``pkg/app``.

    A ``.agents/skills`` ABOVE the repository too, which the walk must not
    reach, and a ``shared`` skill in every directory, ``.aelix/skills`` included.
    """

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    _skill(root / ".agents" / "skills", "root-skill")
    _skill(root / ".agents" / "skills", "shared", "from the repo root")
    _skill(root / "pkg" / ".agents" / "skills", "mid-skill")
    _skill(root / "pkg" / ".agents" / "skills", "shared", "from pkg")
    cwd = root / "pkg" / "app"
    _skill(cwd / ".aelix" / "skills", "shared", "from .aelix")
    _skill(tmp_path / ".agents" / "skills", "above-the-repo")
    _skill(home / ".agents" / "skills", "home-skill")
    _skill(home / ".agents" / "skills", "shared", "from ~/.agents")
    return cwd


def _real(path: Path) -> str:
    return os.path.realpath(path)


# --- the walk ---------------------------------------------------------------


def test_the_walk_stops_at_the_git_root_nearest_first(repo: Path) -> None:
    root = repo.parent.parent
    assert git_root(repo) == root
    assert project_agents_skill_dirs(repo) == [
        root / "pkg" / ".agents" / "skills",
        root / ".agents" / "skills",
    ]


def test_a_worktree_whose_git_is_a_file_is_a_root(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "wt"
    root.mkdir()
    (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n", encoding="utf-8")
    _skill(root / ".agents" / "skills", "in-tree")
    _skill(tmp_path / ".agents" / "skills", "outside")
    assert project_agents_skill_dirs(root / "sub") == [root / ".agents" / "skills"]


def test_outside_a_repository_the_walk_reaches_the_filesystem_root(
    tmp_path: Path, home: Path
) -> None:
    if git_root(tmp_path) is not None:
        pytest.skip("the temporary directory is inside a git repository here")
    outer = tmp_path / "outer"
    _skill(outer / ".agents" / "skills", "far")
    _skill(outer / "a" / ".agents" / "skills", "near")
    cwd = outer / "a" / "b"
    cwd.mkdir(parents=True)
    dirs = project_agents_skill_dirs(cwd)
    assert dirs[:2] == [outer / "a" / ".agents" / "skills", outer / ".agents" / "skills"]


def test_an_empty_agents_directory_is_read_but_does_not_ask(tmp_path: Path, home: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".agents" / "skills").mkdir(parents=True)
    assert project_agents_skill_dirs(tmp_path) == [tmp_path / ".agents" / "skills"]
    assert trust_requiring_agents_skill_dirs(tmp_path) == []
    assert has_trust_requiring_project_resources(tmp_path) is False


# --- the user's own directory ------------------------------------------------


def test_the_users_directory_is_global_once_and_never_asks(home: Path) -> None:
    """A folder under the home directory outside a repository reaches ``~/.agents/skills``.

    pi drops it from both the scan and the trust check (``pathsEqual``). Read
    as a project directory it would make every such folder ask, and the
    skills would be read twice.
    """

    if git_root(home) is not None:
        pytest.skip("the temporary directory is inside a git repository here")
    _skill(home / ".agents" / "skills", "mine")
    cwd = home / "notes" / "today"
    cwd.mkdir(parents=True)
    assert user_agents_skills_dir() == home / ".agents" / "skills"
    assert home / ".agents" / "skills" not in project_agents_skill_dirs(cwd)
    assert has_trust_requiring_project_resources(cwd) is False
    for trusted in (True, False):
        tiers = [(s.tier, s.directory) for s in _resolve_skill_tiers(Args(), str(cwd), trusted)]
        assert tiers.count(("global", str(home / ".agents" / "skills"))) == 1
        assert not [d for t, d in tiers if t == "project" and ".agents" in d]
        catalog = _load_skill_catalog(Args(), str(cwd), trusted, None)
        mine = [s for s in catalog.discovered if s.name == "mine"]
        assert len(mine) == 1
        assert catalog.origins[mine[0].file_path].tier == "global"


async def test_the_users_directory_alone_trusts_the_folder_without_asking(home: Path) -> None:
    if git_root(home) is not None:
        pytest.skip("the temporary directory is inside a git repository here")
    _skill(home / ".agents" / "skills", "mine")
    asked: list[Path] = []

    async def prompt(cwd: Path) -> None:
        asked.append(cwd)

    assert await resolve_project_trusted(home, override=None, has_ui=True, prompt=prompt) is True
    assert asked == []


@_POSIX_ONLY
def test_a_projects_symlink_to_the_users_directory_is_the_users_directory(
    tmp_path: Path, home: Path
) -> None:
    _skill(home / ".agents" / "skills", "mine")
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".agents").mkdir()
    (root / ".agents" / "skills").symlink_to(home / ".agents" / "skills")
    assert project_agents_skill_dirs(root) == []
    assert has_trust_requiring_project_resources(root) is False


@_POSIX_ONLY
def test_a_symlinked_agents_directory_elsewhere_is_the_projects(tmp_path: Path, home: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    _skill(elsewhere / "skills", "linked")
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".agents").symlink_to(elsewhere)
    assert trust_requiring_agents_skill_dirs(root) == [root / ".agents" / "skills"]
    assert has_trust_requiring_project_resources(root) is True
    names = {s.name for s in _load_skill_catalog(Args(), str(root), True, None).discovered}
    assert "linked" in names
    names = {s.name for s in _load_skill_catalog(Args(), str(root), False, None).discovered}
    assert "linked" not in names


@_POSIX_ONLY
@pytest.mark.skipif(sys.platform != "win32" and os.geteuid() == 0, reason="root reads anything")
def test_an_unreadable_agents_directory_neither_asks_nor_loads(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    skills = root / ".agents" / "skills"
    _skill(skills, "hidden")
    skills.chmod(0)
    try:
        assert has_trust_requiring_project_resources(root) is False
        catalog = _load_skill_catalog(Args(), str(root), True, None)
        assert "hidden" not in {s.name for s in catalog.discovered}
    finally:
        skills.chmod(0o755)


# --- trust covers the scan -----------------------------------------------------


def test_trust_covers_every_agents_directory_the_scan_reads(repo: Path) -> None:
    trusted = [
        s.directory
        for s in _resolve_skill_tiers(Args(), str(repo), True)
        if s.tier == "project" and s.agents
    ]
    asks = [str(d) for d in trust_requiring_agents_skill_dirs(repo)]
    # Every directory the scan reads that could load a skill makes the folder
    # ask, and the gate counts nothing the scan does not read.
    assert trusted
    assert [d for d in trusted if any(Path(d).iterdir())] == asks
    assert has_trust_requiring_project_resources(repo) is True
    untrusted = _resolve_skill_tiers(Args(), str(repo), False)
    assert not [s for s in untrusted if s.tier == "project"]


async def test_an_agents_only_repo_asks_and_is_denied_headless(tmp_path: Path, home: Path) -> None:
    """The #115 shape: the only skills are in an ANCESTOR's ``.agents/skills``."""

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    _skill(root / ".agents" / "skills", "planted")
    cwd = root / "src"
    cwd.mkdir()
    assert has_trust_requiring_project_resources(cwd) is True
    assert await resolve_project_trusted(cwd, override=None, has_ui=False) is False
    catalog = _load_skill_catalog(Args(), str(cwd), False, None)
    assert "planted" not in {s.name for s in catalog.discovered}


def test_a_delegated_child_in_the_same_folder_runs_without_trust(
    tmp_path: Path, home: Path
) -> None:
    """``aelix_agents`` decides ``--no-approve`` by the same predicate (ADR-0279 D14)."""

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    _skill(root / ".agents" / "skills", "planted")
    cwd = root / "src"
    cwd.mkdir()
    assert child_trust_argv(cwd, cwd) == ["--no-approve"]


def test_no_skills_reads_no_agents_directory(repo: Path) -> None:
    tiers = _resolve_skill_tiers(Args(no_skills=True), str(repo), True)
    assert not [s for s in tiers if s.agents]


# --- precedence (ADR-0279 D9, pi order inside a tier) ---------------------------


def test_same_name_precedence_and_shadowed_rows(repo: Path, home: Path) -> None:
    catalog = _load_skill_catalog(Args(), str(repo), True, None)
    shared = next(s for s in catalog.discovered if s.name == "shared")
    assert shared.description == "from .aelix"
    losers = [x.skill.description for x in catalog.shadowed if x.skill.name == "shared"]
    assert losers == ["from pkg", "from the repo root", "from ~/.agents"]
    by_name = {s.name: catalog.origins[s.file_path] for s in catalog.discovered}
    assert by_name["mid-skill"].tier == "project" and by_name["mid-skill"].agents
    assert by_name["home-skill"].tier == "global" and by_name["home-skill"].agents
    assert "above-the-repo" not in by_name


def test_a_nearer_agents_skill_beats_a_farther_one_and_both_beat_global(
    tmp_path: Path, home: Path
) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    _skill(root / ".agents" / "skills", "x", "far")
    _skill(root / "a" / ".agents" / "skills", "x", "near")
    _skill(home / ".agents" / "skills", "x", "home")
    cwd = root / "a"
    catalog = _load_skill_catalog(Args(), str(cwd), True, None)
    assert next(s for s in catalog.discovered if s.name == "x").description == "near"
    catalog = _load_skill_catalog(Args(), str(cwd), False, None)
    assert next(s for s in catalog.discovered if s.name == "x").description == "home"


def test_aelix_global_beats_the_users_agents_directory(
    home: Path, own_agent_dir: Path, tmp_path: Path
) -> None:
    _skill(own_agent_dir / "skills", "y", "aelix global")
    _skill(home / ".agents" / "skills", "y", "agents global")
    catalog = _load_skill_catalog(Args(), str(tmp_path), False, None)
    assert next(s for s in catalog.discovered if s.name == "y").description == "aelix global"
    assert [x.skill.description for x in catalog.shadowed if x.skill.name == "y"] == [
        "agents global"
    ]


# --- the trust prompt and /trust ------------------------------------------------


def test_the_trust_prompt_names_each_directory(repo: Path) -> None:
    lines = format_project_trust_prompt(repo).split("\n")
    assert lines[-1] == (
        "Trusting also loads the skills in .agents/skills here and in the folders above, "
        "up to the repository root; found in "
        + f"{_UP}.agents{os.sep}skills and {_UP * 2}.agents{os.sep}skills."
    )
    # The #380 paragraph is still the fourth line, unchanged, and the path above
    # the paragraph is what the relative paths are read against.
    assert lines[1] == str(repo)
    assert lines[3].startswith("This allows Aelix to load .aelix extensions")
    assert lines[4] == ""


def test_outside_a_repository_the_prompt_says_every_folder_above(
    tmp_path: Path, home: Path
) -> None:
    if git_root(tmp_path) is not None:
        pytest.skip("the temporary directory is inside a git repository here")
    body = format_project_trust_prompt(tmp_path, agents_skill_dirs=[])
    assert body.split("\n")[-1] == (
        "Trusting also loads the skills in .agents/skills here and in every folder above."
    )


def test_the_phrase_names_no_folder_the_repository_chose(tmp_path: Path) -> None:
    """Relative to the cwd: ``..`` only, however the folders above are named."""

    evil = tmp_path / "evil\x1b[8mx" if sys.platform != "win32" else tmp_path / "evil"
    cwd = evil / "a" / "b"
    dirs = [
        cwd / ".agents" / "skills",
        evil / "a" / ".agents" / "skills",
        evil / ".agents" / "skills",
    ]
    phrase = agents_skill_dirs_phrase(cwd, dirs)
    sep = os.sep
    assert phrase == (
        f".agents{sep}skills, ..{sep}.agents{sep}skills and ..{sep}..{sep}.agents{sep}skills"
    )


def test_a_directory_that_is_not_a_parent_is_still_flattened() -> None:
    evil = Path("/r/a\x1b[8mb\nTrust is safe/.agents/skills")
    phrase = agents_skill_dirs_phrase(Path("/r/x"), [evil])
    assert "\x1b" not in phrase and "\n" not in phrase
    assert phrase.endswith("^[[8mb^JTrust is safe/.agents/skills".replace("/", os.sep))


def test_one_row_is_the_panels_flattening() -> None:
    pytest.importorskip("prompt_toolkit")
    from aelix_coding_agent.tui.selection_panel import flat

    for text in ("a\x1bb", "a\nb\tc", "x\u202ey", "\x7f", "\x9bz", "plain é 漢", "a\u200bb"):
        assert _one_row(text) == flat(text), repr(text)


def test_three_directories_are_named_then_a_count() -> None:
    cwd = Path("/r/a/a/a/a")
    dirs = [Path("/r", *(["a"] * (4 - i)), ".agents", "skills") for i in range(5)]
    sep = os.sep
    up = f"..{sep}"
    rows = trust_selector_title(cwd, None, session_trusted=True, agents_skill_dirs=dirs).split("\n")
    assert rows == [
        "Project trust",
        str(cwd),
        "Saved decision: none",
        "Current session: trusted",
        f"Skills found in: .agents{sep}skills, {up}.agents{sep}skills, {up * 2}.agents{sep}skills"
        " and 2 more (/skill lists them)",
    ]
    body = format_project_trust_prompt(cwd, agents_skill_dirs=dirs)
    assert body.endswith(f"{up * 2}.agents{sep}skills and 2 more (/skill lists them).")


def test_trust_adds_nothing_where_there_is_no_agents_directory(tmp_path: Path, home: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert trust_selector_title(tmp_path, None, session_trusted=False).split("\n") == [
        "Project trust",
        str(tmp_path),
        "Saved decision: none",
        "Current session: untrusted",
    ]


def test_trust_names_the_directories_of_a_real_tree(repo: Path) -> None:
    rows = trust_selector_title(repo, None, session_trusted=False).split("\n")
    sep = os.sep
    assert rows[-1] == (
        f"Skills found in: ..{sep}.agents{sep}skills and ..{sep}..{sep}.agents{sep}skills"
    )


# --- the /skill panel -------------------------------------------------------------


def _kind(cwd: Path, trusted: bool) -> SkillKind:
    holder = {"result": _load_skill_catalog(Args(), str(cwd), trusted, None)}
    return SkillKind(
        holder=holder,
        harness=lambda: None,
        cwd=str(cwd),
        project_trusted=trusted,
        project_skills_dir=str(cwd / ".aelix" / "skills"),
    )


def test_panel_groups_one_per_directory_each_drawn_once(repo: Path) -> None:
    items = _kind(repo, True).items()
    groups = [i.group for i in items]
    # Every group's rows are contiguous: one header each.
    seen: list[str] = []
    for group in groups:
        if not seen or seen[-1] != group:
            assert group not in seen, groups
            seen.append(group)
    labels = [i.group_label for i in items]
    root = repo.parent.parent
    assert labels[0] == f"PROJECT  {_AELIX}/"
    project = [label for label in dict.fromkeys(labels) if label.startswith("PROJECT")]
    assert project == [
        f"PROJECT  {_AELIX}/",
        f"PROJECT  {_UP}{_AGENTS}/",
        f"PROJECT  {_UP * 2}{_AGENTS}/",
    ]
    assert f"GLOBAL  ~{os.sep}.agents{os.sep}skills/" in labels
    shadowed = [i for i in items if i.label == "shared" and not i.invocation]
    assert [i.source_base for i in shadowed] == [
        str(root / "pkg" / ".agents" / "skills"),
        str(root / ".agents" / "skills"),
        str(Path.home() / ".agents" / "skills"),
    ]
    locks = _kind(repo, True).locks()
    assert all(locks[i.key].reason.startswith("shadowed by the project skill") for i in shadowed)


def test_an_untrusted_panel_counts_each_directory_and_reads_none(repo: Path) -> None:
    kind = _kind(repo, False)
    items = kind.items()
    untrusted = [i for i in items if i.key.startswith("untrusted:")]
    assert [(i.group_label, i.label) for i in untrusted] == [
        (f"PROJECT  {_AELIX}/", "1 skill not loaded"),
        (f"PROJECT  {_UP}{_AGENTS}/", "2 skills not loaded"),
        (f"PROJECT  {_UP * 2}{_AGENTS}/", "2 skills not loaded"),
    ]
    locks = kind.locks()
    assert all(locks[i.key].value is False for i in untrusted)
    names = {i.label for i in items if not i.key.startswith("untrusted:")}
    assert "mid-skill" not in names and "root-skill" not in names
    assert "home-skill" in names


# --- the Agent Skills loader rule (pi ``collectSkillEntries`` mode "agents") --------


def test_agents_layout_ignores_root_markdown_and_finds_grouped_skills(tmp_path: Path) -> None:
    root = tmp_path / ".agents" / "skills"
    root.mkdir(parents=True)
    (root / "README.md").write_text("# Shared skills\n", encoding="utf-8")
    (root / "root-file.md").write_text(
        "---\nname: root-file\ndescription: a root file\n---\n", encoding="utf-8"
    )
    _skill(root, "nested-skill")
    group = root / "third-party"
    (group / "vendor" / "pack").mkdir(parents=True)
    (group / "child-skill.md").write_text(
        "---\nname: child-skill\ndescription: grouped\n---\nbody\n", encoding="utf-8"
    )
    (group / "notes.md").write_text("just notes, no frontmatter\n", encoding="utf-8")
    (group / "vendor" / "pack" / "deep-skill.md").write_text(
        "---\nname: deep-skill\ndescription: deeper\n---\n", encoding="utf-8"
    )
    result = load_skills(root, agents=True)
    assert sorted(s.name for s in result.skills) == ["child-skill", "deep-skill", "nested-skill"]
    assert result.diagnostics == []
    # The same directory under aelix's own rule takes the root files.
    plain = {s.name for s in load_skills(root).skills}
    assert "root-file" in plain and "child-skill" not in plain


def test_the_catalog_reads_both_agents_directories_by_their_rule(
    tmp_path: Path, home: Path
) -> None:
    """``SkillSource.agents`` reaches the loader for the project walk and ``~/.agents/skills``."""

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    for skills in (root / ".agents" / "skills", home / ".agents" / "skills"):
        skills.mkdir(parents=True)
        (skills / "README.md").write_text("# Shared with other agents\n", encoding="utf-8")
        (skills / f"loose-{skills.parent.parent.name}.md").write_text(
            "---\nname: loose\ndescription: a root file\n---\n", encoding="utf-8"
        )
    catalog = _load_skill_catalog(Args(), str(root), True, None)
    assert catalog.diagnostics == []
    assert "loose" not in {s.name for s in catalog.discovered}


# --- review round 1 (#509): file identity, scan modes, decoding, bounds, counts ---------


def _case_alias(path: Path) -> Path | None:
    """*path*'s last component in the other case, when this volume folds case; else ``None``."""

    alias = path.with_name(path.name.swapcase())
    return alias if alias.exists() else None


def test_a_home_spelled_in_another_case_is_still_the_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3: macOS keeps the case it is given, so ``realpath`` was no key (pi #10488's shape).

    ``HOME=…/home`` for a folder created as ``…/Home``, on the default
    case-insensitive volume: the user's own ``~/.agents/skills`` was a project
    directory — the folder asked, and a trusted run read it twice with the
    project copy winning.
    """

    real = tmp_path / "Home"
    real.mkdir()
    alias = _case_alias(real)
    if alias is None:
        pytest.skip("this volume is case-sensitive")
    sandbox_home(monkeypatch, alias)
    _skill(real / ".agents" / "skills", "mine")
    cwd = real / "notes"
    cwd.mkdir()
    if git_root(cwd) is not None:
        pytest.skip("the temporary directory is inside a git repository here")
    assert project_agents_skill_dirs(cwd) == []
    assert has_trust_requiring_project_resources(cwd) is False
    catalog = _load_skill_catalog(Args(), str(cwd), True, None)
    mine = [s for s in catalog.discovered if s.name == "mine"]
    assert [catalog.origins[s.file_path].tier for s in mine] == ["global"]
    assert not [x for x in catalog.shadowed if x.skill.name == "mine"]


@_POSIX_ONLY
def test_a_symlink_to_the_users_directory_in_another_case_is_the_users(
    tmp_path: Path, home: Path
) -> None:
    _skill(home / ".agents" / "skills", "mine")
    alias = _case_alias(home)
    if alias is None:
        pytest.skip("this volume is case-sensitive")
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".agents").mkdir()
    (root / ".agents" / "skills").symlink_to(alias / ".agents" / "skills")
    assert project_agents_skill_dirs(root) == []
    assert has_trust_requiring_project_resources(root) is False


def test_path_key_names_the_object_and_falls_back_to_the_string(tmp_path: Path) -> None:
    from aelix_coding_agent.core.skill_dirs import path_key

    (tmp_path / "a").mkdir()
    assert path_key(tmp_path / "a") == path_key(tmp_path / "a" / ".." / "a")
    assert path_key(tmp_path / "a") != path_key(tmp_path)
    missing = path_key(tmp_path / "missing")
    assert isinstance(missing, str) and missing == os.path.normcase(
        os.path.realpath(tmp_path / "missing")
    )
    assert isinstance(path_key("bad\0path"), str)


def _modes_tree(tmp_path: Path) -> Path:
    """A ``.agents/skills`` holding one skill only aelix's rule finds and one only pi's does."""

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    skills = root / ".agents" / "skills"
    skills.mkdir(parents=True)
    (skills / "custom.md").write_text(
        "---\nname: root-custom\ndescription: a root file\n---\n", encoding="utf-8"
    )
    (skills / "group").mkdir()
    (skills / "group" / "extra.md").write_text(
        "---\nname: group-extra\ndescription: grouped\n---\n", encoding="utf-8"
    )
    _skill(skills, "both")
    return root


def test_a_settings_path_to_an_agents_directory_keeps_its_root_files(tmp_path: Path) -> None:
    """F2: one directory, two rules — the scan of one no longer erases the other's.

    A settings ``skills`` path names a directory aelix reads by its own rule
    (root ``.md`` files are skills); the same directory found as
    ``.agents/skills`` is read by pi's (grouped ``.md`` files are). Keyed by
    the directory alone, the automatic scan ran first and the settings entry's
    ``root-custom`` was lost.
    """

    from aelix_ai.settings import SettingsManager

    root = _modes_tree(tmp_path)
    settings = SettingsManager.in_memory(
        {"skills": [str(root / ".agents" / "skills")]}, project_trusted=True
    )
    catalog = _load_skill_catalog(Args(), str(root), True, settings)
    names = [s.name for s in catalog.discovered]
    assert {"root-custom", "group-extra", "both"} <= set(names)
    assert names.count("both") == 1 and not catalog.shadowed


def test_a_skill_flag_on_an_agents_directory_keeps_its_grouped_files(tmp_path: Path) -> None:
    """F2, the other way: ``--skill`` (aelix's rule, first) erased the automatic scan."""

    root = _modes_tree(tmp_path)
    args = Args(skills=[str(root / ".agents" / "skills")])
    catalog = _load_skill_catalog(args, str(root), True, None)
    names = [s.name for s in catalog.discovered]
    assert {"root-custom", "group-extra", "both"} <= set(names)
    assert names.count("both") == 1 and not catalog.shadowed
    assert catalog.origins[
        next(s for s in catalog.discovered if s.name == "both").file_path
    ].tier == ("cli")


def test_a_grouped_file_that_is_not_utf8_and_no_skill_is_skipped_silently(tmp_path: Path) -> None:
    """F4: it raised ``UnicodeDecodeError`` out of ``load_skills`` and lost every skill."""

    root = tmp_path / ".agents" / "skills"
    _skill(root, "good")
    (root / "group").mkdir()
    (root / "group" / "notes.md").write_bytes(b"legacy documentation: \xff\xfe\n")
    result = load_skills(root, agents=True)
    assert [s.name for s in result.skills] == ["good"]
    assert result.diagnostics == []


def test_a_skill_that_is_not_utf8_is_skipped_with_one_diagnostic(tmp_path: Path) -> None:
    root = tmp_path / ".agents" / "skills"
    _skill(root, "good")
    (root / "group").mkdir()
    (root / "group" / "declared.md").write_bytes(
        b"---\nname: declared\ndescription: caf\xe9\n---\nbody\n"
    )
    (root / "broken").mkdir()
    (root / "broken" / "SKILL.md").write_bytes(b"---\nname: broken\ndescription: \xff\n---\n")
    result = load_skills(root, agents=True)
    assert [s.name for s in result.skills] == ["good"]
    assert sorted((d.code, Path(d.path).name) for d in result.diagnostics) == [
        ("read_failed", "SKILL.md"),
        ("read_failed", "declared.md"),
    ]
    assert all("not UTF-8" in d.message for d in result.diagnostics)


def test_an_ignore_file_that_is_not_utf8_still_applies(tmp_path: Path) -> None:
    root = tmp_path / ".agents" / "skills"
    _skill(root, "good")
    _skill(root, "ignored")
    (root / ".gitignore").write_bytes(b"# caf\xe9\nignored/\n")
    result = load_skills(root, agents=True)
    assert [s.name for s in result.skills] == ["good"]
    assert result.diagnostics == []


def test_the_phrase_counts_the_levels_past_three() -> None:
    """F5: 65 levels wrote ``../`` 65 times, five rows at 80 columns."""

    sep = os.sep
    deep = Path("/r").joinpath(*(["d"] * 65))
    near = Path("/r").joinpath(*(["d"] * 3))
    assert agents_skill_dirs_phrase(deep, [Path("/r/.agents/skills")]) == (
        f"..{sep}×65{sep}.agents{sep}skills"
    )
    assert agents_skill_dirs_phrase(near, [Path("/r/.agents/skills")]) == (
        f"..{sep}..{sep}..{sep}.agents{sep}skills"
    )
    assert agents_skill_dirs_phrase(near, [Path("/r/d/.agents/skills")]) == (
        f"..{sep}..{sep}.agents{sep}skills"
    )


def test_a_deep_cwd_keeps_every_option_of_the_question_on_an_80x24_screen() -> None:
    """F5 measured: the rows ``_prompt_one_shot_select`` draws at 80 columns.

    The cwd 65 short levels below the root holding the skills; the
    ``Trust parent folder (…)`` label and the cwd line still grow with the
    path (#380's own rows), the sentence no longer does.
    """

    pytest.importorskip("prompt_toolkit")
    from aelix_coding_agent.cli.entry import _one_shot_rows
    from aelix_coding_agent.cli.project_trust import project_trust_options

    cwd = Path("/r").joinpath(*(["d"] * 65))
    body = format_project_trust_prompt(cwd, agents_skill_dirs=[Path("/r/.agents/skills")])
    options = project_trust_options(cwd)
    rows = _one_shot_rows(body, options, len(options) - 1, 80).split("\n")
    last = max(i for i, row in enumerate(rows) if row.startswith("→ "))
    assert rows[last].endswith(options[-1])
    assert len(rows) <= 24, rows
    sentence = [r for r in rows if "×65" in r or r.startswith("Trusting also")]
    assert len(sentence) <= 2, sentence


def _long_tree(tmp_path: Path) -> Path:
    """Five ``.agents/skills`` on one walk under a cwd far wider than 80 columns."""

    root = tmp_path / ("a-repository-with-a-rather-long-directory-name-" + "x" * 40)
    (root / ".git").mkdir(parents=True)
    current = root
    for i, part in enumerate(["packages", "frontend-application", "src", "components"]):
        _skill(current / ".agents" / "skills", f"lvl{i}")
        current = current / part
    _skill(current / ".agents" / "skills", "here")
    return current


@pytest.mark.parametrize("trusted", [False, True])
def test_every_parent_header_is_told_apart_at_80_columns(
    tmp_path: Path, home: Path, trusted: bool
) -> None:
    """V1: by full path, cut from the right, every parent's header read the same."""

    pytest.importorskip("prompt_toolkit")
    import re

    from aelix_coding_agent.core.resource_selection import ResourceSelection
    from aelix_coding_agent.tui.selection_panel import (
        SelectionModel,
        cells,
        plan_layout,
        render_panel,
    )

    cwd = _long_tree(tmp_path)
    assert len(str(cwd)) > 160
    kind = _kind(cwd, trusted)
    snapshot = ResourceSelection(
        None, global_base=str(home), project_base=str(cwd / ".aelix"), project_trusted=trusted
    ).snapshot(kind)
    model = SelectionModel(snapshot, "Skills", ("skill", "skills"))
    layout = plan_layout(model, 80, 40)
    rows = [re.sub(r"\x1b\[[0-9;]*m", "", r) for r in render_panel(model, layout)]
    assert all(cells(r) <= 80 for r in rows)
    headers = [r.strip() for r in rows if r.startswith(" PROJECT")]
    sep = os.sep
    assert headers[-5:] == [
        f"PROJECT  .agents{sep}skills/",
        f"PROJECT  ..{sep}.agents{sep}skills/",
        f"PROJECT  ..{sep}..{sep}.agents{sep}skills/",
        f"PROJECT  ..{sep}..{sep}..{sep}.agents{sep}skills/",
        f"PROJECT  ..{sep}×4{sep}.agents{sep}skills/",
    ]


def test_the_users_directory_keeps_its_tilde_under_home(home: Path) -> None:
    """The GLOBAL header stays ``~/.agents/skills/`` when the cwd is under the home directory."""

    if git_root(home) is not None:
        pytest.skip("the temporary directory is inside a git repository here")
    _skill(home / ".agents" / "skills", "mine")
    cwd = home / "notes"
    cwd.mkdir()
    labels = {i.group_label for i in _kind(cwd, True).items()}
    assert f"GLOBAL  ~{os.sep}.agents{os.sep}skills/" in labels


def test_the_count_takes_the_loaders_listing_rule(tmp_path: Path) -> None:
    """F6: ``node_modules`` was counted; a root ``SKILL.md`` is the loader's one skill."""

    from aelix_coding_agent.core.skill_selection import count_untrusted_skills

    agents = tmp_path / ".agents" / "skills"
    _skill(agents, "node_modules")  # node_modules/SKILL.md, which the loader never reads
    _skill(agents, ".hidden")
    _skill(agents, "real")
    (agents / "README.md").write_text("# shared\n", encoding="utf-8")
    assert count_untrusted_skills(str(agents), agents=True) == 1
    assert len(load_skills(agents, agents=True).skills) == 1

    aelix = tmp_path / ".aelix" / "skills"
    _skill(aelix, "sub")
    (aelix / "SKILL.md").write_text("---\nname: skills\ndescription: root\n---\n", encoding="utf-8")
    (aelix / "loose.md").write_text("---\nname: loose\ndescription: x\n---\n", encoding="utf-8")
    assert count_untrusted_skills(str(aelix)) == 1 == len(load_skills(aelix).skills)


@pytest.mark.parametrize(
    ("entries", "asks"),
    [
        (["README.md"], False),
        (["notes.txt", "README.md"], False),
        (["node_modules/"], False),
        ([".hidden/"], False),
        (["SKILL.md"], True),
        (["group/"], True),
        (["x/SKILL.md"], True),
    ],
)
def test_a_folder_asks_only_for_what_the_loader_could_read(
    tmp_path: Path, home: Path, entries: list[str], asks: bool
) -> None:
    """V5: a ``README.md``-only ``.agents/skills`` asked, and loaded nothing when trusted.

    An empty subfolder still asks: nothing below the first level is listed,
    and what this check missed would load unasked (the #115 hole).
    """

    (tmp_path / ".git").mkdir()
    skills = tmp_path / ".agents" / "skills"
    skills.mkdir(parents=True)
    for entry in entries:
        target = skills / entry
        if entry.endswith("/"):
            target.mkdir(parents=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("---\nname: x\ndescription: x\n---\n", encoding="utf-8")
    assert has_trust_requiring_project_resources(tmp_path) is asks
    assert bool(trust_requiring_agents_skill_dirs(tmp_path)) is asks


async def test_status_names_agents_skills_where_it_is_not_trusted(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """V4: an agents-only repo printed "NOT trusted — .aelix/ resources here are skipped"."""

    from aelix_coding_agent.cli.status import run_status_command

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    _skill(root / ".agents" / "skills", "planted")
    monkeypatch.chdir(root)
    assert await run_status_command(["--no-extensions"]) == 0
    line = next(r for r in capsys.readouterr().out.splitlines() if "project trust" in r)
    assert line.endswith("NOT trusted — .aelix/ resources and .agents/skills here are skipped")


# --- review round 2 (#509): one warning per file, the inode-less fallback -------------


def _warned(skills: Path) -> None:
    """A ``SKILL.md`` that is not UTF-8 and one named unlike its folder: one warning each."""

    _skill(skills, "good")
    (skills / "bad").mkdir()
    (skills / "bad" / "SKILL.md").write_bytes(b"---\nname: bad\ndescription: caf\xe9\n---\nbody\n")
    (skills / "mismatch").mkdir()
    (skills / "mismatch" / "SKILL.md").write_text(
        "---\nname: other-name\ndescription: d\n---\nbody\n", encoding="utf-8"
    )


def _warnings(catalog: SkillCatalog) -> list[tuple[str, str]]:
    return sorted((d.code, Path(d.path).parent.name) for d in catalog.diagnostics)


def test_a_settings_path_to_the_users_directory_warns_once(tmp_path: Path, home: Path) -> None:
    """R1: F2 scans one directory by both rules, and every warning was printed twice.

    A global settings ``skills`` path to ``~/.agents/skills`` (the migration
    case) is read by aelix's rule and found again by the Agent Skills rule;
    both read a ``SKILL.md`` alike.
    """

    from aelix_ai.settings import SettingsManager

    _warned(home / ".agents" / "skills")
    cwd = tmp_path / "work"
    (cwd / ".git").mkdir(parents=True)
    settings = SettingsManager.in_memory({"skills": ["~/.agents/skills"]})
    catalog = _load_skill_catalog(Args(), str(cwd), False, settings)
    assert "good" in {s.name for s in catalog.discovered}
    assert _warnings(catalog) == [("invalid_metadata", "mismatch"), ("read_failed", "bad")]


def test_a_skill_flag_on_a_trusted_agents_directory_warns_once(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    _warned(root / ".agents" / "skills")
    args = Args(skills=[str(root / ".agents" / "skills")])
    catalog = _load_skill_catalog(args, str(root), True, None)
    assert _warnings(catalog) == [("invalid_metadata", "mismatch"), ("read_failed", "bad")]


def test_path_key_without_an_inode_still_tells_the_users_directory_apart(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R6: a filesystem that reports ``st_ino == 0`` keys by the resolved string.

    Two spellings of one directory stay equal, the user's own
    ``~/.agents/skills`` reached through a project's symlink is still not a
    project directory, and an unrelated repository's still is.
    """

    import aelix_coding_agent.core.skill_dirs as skill_dirs

    users = home / ".agents" / "skills"
    _skill(users, "mine")
    linked = tmp_path / "linked"
    (linked / ".git").mkdir(parents=True)
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    _skill(other / ".agents" / "skills", "theirs")
    real_stat = os.stat

    def no_inode(path: object, *args: object, **kwargs: object) -> os.stat_result:
        st = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        return os.stat_result((st.st_mode, 0, *tuple(st)[2:]))

    can_link = sys.platform != "win32"
    if can_link:
        (linked / ".agents").mkdir()
        (linked / ".agents" / "skills").symlink_to(users)
    monkeypatch.setattr(skill_dirs.os, "stat", no_inode)
    key = skill_dirs.path_key(users)
    assert isinstance(key, str) and key == os.path.normcase(os.path.realpath(users))
    assert key == skill_dirs.path_key(users / ".." / "skills")
    assert key != skill_dirs.path_key(other / ".agents" / "skills")
    if can_link:
        assert project_agents_skill_dirs(linked) == []
    assert [_real(p) for p in project_agents_skill_dirs(other)] == [
        _real(other / ".agents" / "skills")
    ]
