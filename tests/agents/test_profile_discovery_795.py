"""#795, #796 (ADR-0304 D4, D5, D10) — what discovery adds for the agents panel and the door.

* the profiles that LOSE a name collision are kept in ``shadowed``, each with
  the profile that finally holds the name; the old warnings are unchanged;
* an ``agents:`` name no profile has is a WARNING on the declaring file, never
  an error, so ``--agent`` still resolves that profile;
* ``complete_profile`` is ``resolve_profile``'s ``missing_path`` escalation on
  its own, with today's text;
* ``count_untrusted_profiles`` counts what ``_scan_dir`` would read from a
  directory listing, and opens nothing.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest
from aelix_coding_agent.agents import discovery
from aelix_coding_agent.agents.discovery import (
    ProfileDiscoveryResult,
    ProfileError,
    complete_profile,
    count_untrusted_profiles,
    discover_profiles,
    project_agents_dir,
    resolve_profile,
    user_agents_dir,
)

_BODY = "You are the agent."


def _write(path: Path, name: str, *, extra: str = "", description: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = f"{extra}\n" if extra else ""
    desc = description if description is not None else f"{name} agent"
    path.write_text(
        f"---\nname: {name}\ndescription: {desc}\n{suffix}---\n\n{_BODY}\n", encoding="utf-8"
    )
    return path


@pytest.fixture()
def bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "bundled"
    folder.mkdir()
    monkeypatch.setattr(discovery, "builtin_agents_dir", lambda: folder)
    return folder


@pytest.fixture()
def dirs(tmp_path: Path, bundled: Path) -> tuple[Path, Path]:
    cwd = tmp_path / "project"
    cwd.mkdir()
    agent_dir = tmp_path / "agent"
    (agent_dir / "agents").mkdir(parents=True)
    return cwd, agent_dir


def test_a_profile_that_loses_a_collision_is_kept_with_its_winner(
    dirs: tuple[Path, Path], bundled: Path
) -> None:
    cwd, agent_dir = dirs
    shipped = _write(bundled / "explorer.md", "explorer")
    mine = _write(user_agents_dir(str(agent_dir)) / "explorer.md", "explorer")
    result = discover_profiles(cwd, project_trusted=False, agent_dir=str(agent_dir))
    assert [p.file_path for p in result.profiles] == [str(mine)]
    assert [(s.profile.file_path, s.winner.file_path) for s in result.shadowed] == [
        (str(shipped), str(mine))
    ]
    # The warning is today's, unchanged.
    assert [d.message for d in result.diagnostics] == [
        f"user agent profile 'explorer' ({mine}) overrides the bundled profile at {shipped}"
    ]


def test_a_same_scope_duplicate_and_a_third_tier_name_the_final_winner(
    dirs: tuple[Path, Path], bundled: Path
) -> None:
    cwd, agent_dir = dirs
    users = user_agents_dir(str(agent_dir))
    shipped = _write(bundled / "explorer.md", "explorer")
    first = _write(users / "a-explorer.md", "explorer")
    second = _write(users / "b-explorer.md", "explorer")
    project = _write(project_agents_dir(cwd) / "explorer.md", "explorer")
    result = discover_profiles(cwd, project_trusted=True, agent_dir=str(agent_dir))
    assert [p.file_path for p in result.profiles] == [str(project)]
    assert [(s.profile.file_path, s.winner.file_path) for s in result.shadowed] == [
        (str(shipped), str(project)),
        (str(first), str(project)),
        (str(second), str(project)),
    ]


def test_an_agents_name_no_profile_has_is_a_warning_on_its_file(
    dirs: tuple[Path, Path],
) -> None:
    cwd, agent_dir = dirs
    users = user_agents_dir(str(agent_dir))
    orchestrator = _write(
        users / "orchestrator.md", "orchestrator", extra="agents: [explorer, reviewr]"
    )
    _write(users / "explorer.md", "explorer")
    result = discover_profiles(cwd, project_trusted=False, agent_dir=str(agent_dir))
    unknown = [d for d in result.diagnostics if "agents: names" in d.message]
    assert len(unknown) == 1
    diag = unknown[0]
    assert (diag.code, diag.type, diag.path) == ("invalid_metadata", "warning", str(orchestrator))
    assert diag.message == "agents: names 'reviewr', which no agent profile found here has"
    # Not escalated: --agent still takes the identity.
    resolved = resolve_profile(
        "orchestrator", cwd=cwd, project_trusted=False, agent_dir=str(agent_dir)
    )
    assert resolved.agents == ("explorer", "reviewr")


def test_a_malformed_agents_entry_keeps_the_profile_out_with_its_error(
    dirs: tuple[Path, Path],
) -> None:
    cwd, agent_dir = dirs
    users = user_agents_dir(str(agent_dir))
    bad = _write(users / "orchestrator.md", "orchestrator", extra="agents: [Bad_Name]")
    result = discover_profiles(cwd, project_trusted=False, agent_dir=str(agent_dir))
    assert [p.name for p in result.profiles] == []
    errors = [d for d in result.diagnostics if d.type == "error"]
    assert [d.path for d in errors] == [str(bad)]
    assert errors[0].message.startswith("agents: entry 'Bad_Name' is not a profile name")


def test_the_human_doors_unknown_name_text_is_unchanged(dirs: tuple[Path, Path]) -> None:
    cwd, agent_dir = dirs
    _write(user_agents_dir(str(agent_dir)) / "scout.md", "scout")
    _write(project_agents_dir(cwd) / "local.md", "local")
    with pytest.raises(ProfileError) as unknown:
        resolve_profile("nope", cwd=cwd, project_trusted=False, agent_dir=str(agent_dir))
    assert str(unknown.value) == "unknown agent profile 'nope'; available: scout"
    with pytest.raises(ProfileError) as peeked:
        resolve_profile("local", cwd=cwd, project_trusted=False, agent_dir=str(agent_dir))
    assert "exists only in" in str(peeked.value) and "--approve" in str(peeked.value)


def test_complete_profile_raises_todays_missing_path_text(dirs: tuple[Path, Path]) -> None:
    cwd, agent_dir = dirs
    users = user_agents_dir(str(agent_dir))
    path = _write(users / "scout.md", "scout", extra="skills: [./gone]")
    result = discover_profiles(cwd, project_trusted=False, agent_dir=str(agent_dir))
    (profile,) = result.profiles
    with pytest.raises(ProfileError) as completed:
        complete_profile(profile, result)
    with pytest.raises(ProfileError) as resolved:
        resolve_profile("scout", cwd=cwd, project_trusted=False, agent_dir=str(agent_dir))
    assert str(completed.value) == str(resolved.value)
    assert str(completed.value).startswith(
        f"agent profile 'scout' ({path}) references paths that do not exist: skills path"
    )


def test_complete_profile_reads_only_the_profiles_own_diagnostics(
    dirs: tuple[Path, Path],
) -> None:
    cwd, agent_dir = dirs
    users = user_agents_dir(str(agent_dir))
    _write(users / "scout.md", "scout")
    result = discover_profiles(cwd, project_trusted=False, agent_dir=str(agent_dir))
    (profile,) = result.profiles
    other = discovery.ProfileDiagnostic(
        code="missing_path",
        message="skills path does not exist: x",
        path="/elsewhere.md",
        type="warning",
    )
    noisy = ProfileDiscoveryResult(result.profiles, [*result.diagnostics, other])
    assert complete_profile(profile, noisy) is profile


def test_count_untrusted_profiles_counts_what_trusting_would_read_and_opens_nothing(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd, agent_dir = dirs
    folder = project_agents_dir(cwd)
    _write(folder / "good.md", "good")
    (folder / "broken.md").write_text("---\nname: Broken\n---\n\nbody\n", encoding="utf-8")
    (folder / ".hidden.md").write_text("x", encoding="utf-8")
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    (folder / "sub.md").mkdir()
    opened: list[object] = []
    real_open = builtins.open
    real_read = Path.read_text

    def _spy_open(file: object, *args: object, **kwargs: object) -> object:
        opened.append(file)
        return real_open(file, *args, **kwargs)  # type: ignore[call-overload]

    def _spy_read(self: Path, *args: object, **kwargs: object) -> str:
        opened.append(self)
        return real_read(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", _spy_open)
    monkeypatch.setattr(Path, "read_text", _spy_read)
    count = count_untrusted_profiles(folder)
    monkeypatch.undo()
    assert opened == []
    trusted = discover_profiles(cwd, project_trusted=True, agent_dir=str(agent_dir))
    loaded = [p for p in trusted.profiles if p.scope == "project"]
    failed = {d.path for d in trusted.diagnostics if d.type == "error"}
    assert count == 2 == len(loaded) + len(failed)


def test_count_untrusted_profiles_of_a_missing_folder_is_zero(tmp_path: Path) -> None:
    assert count_untrusted_profiles(tmp_path / "nope") == 0
