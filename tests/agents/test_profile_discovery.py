"""ADR-0196 — ``agents/discovery.py``: where profiles live, and who may load them.

The two rules under test that are SECURITY rules, not ergonomics:

* the project tier is inert until ``project_trusted`` (which only became a real
  gate once ``project_trust.py:174-199`` learned about ``.aelix/agents/``);
* scope is decided by resolved-path CONTAINMENT, so ``--agent-file`` cannot
  launder a project profile into the ungated ``explicit`` bucket.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_coding_agent.agents import discovery
from aelix_coding_agent.agents.discovery import (
    ProfileError,
    builtin_agents_dir,
    classify_scope,
    discover_profiles,
    load_profile_file,
    project_agents_dir,
    resolve_profile,
    user_agents_dir,
)

_BODY = "You are the agent."

_REAL_BUNDLED_DIR = builtin_agents_dir()
"""Captured at IMPORT time, before ``_empty_bundled_tier`` can patch it away.

One test in this file has to read what actually ships; every other test has to
not see it. Resolving the real path once, here, keeps both true without either
test knowing about the other's fixture."""


def _write(path: Path, name: str, *, extra: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = f"{extra}\n" if extra else ""
    path.write_text(
        f"---\nname: {name}\ndescription: {name} agent\n{suffix}---\n\n{_BODY}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _empty_bundled_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the BUNDLED tier at an empty directory for every test in this file.

    The bundled tier is anchored on ``__file__``, not on a fixture path, so it
    contributes its shipped profiles to every ``discover_profiles`` call —
    including the ones here that assert an EXACT profile list. Without this the
    file measures what aelix happens to ship today, and adding a second bundled
    profile later would turn nine unrelated assertions red for a reason none of
    them is about.

    Isolated rather than accommodated: these tests exist for the user/project
    tier rules (one of them a security rule), so the third tier is a variable to
    hold still. What the bundled tier itself does is pinned separately below,
    including the one assertion that DOES read the real shipped directory.
    """

    empty = tmp_path / "bundled-empty"
    empty.mkdir()
    monkeypatch.setattr(discovery, "builtin_agents_dir", lambda: empty)
    return empty


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    """``(cwd, agent_dir)`` — the project root and the user agent ROOT.

    ``agent_dir`` deliberately sits OUTSIDE ``cwd`` so the two tiers cannot be
    confused by containment.
    """

    cwd = tmp_path / "project"
    cwd.mkdir()
    agent_dir = tmp_path / "agent"
    (agent_dir / "agents").mkdir(parents=True)
    return cwd, agent_dir


def test_user_dir_scanned(dirs: tuple[Path, Path]) -> None:
    cwd, agent_dir = dirs
    _write(user_agents_dir(str(agent_dir)) / "scout.md", "scout")

    result = discover_profiles(cwd, project_trusted=True, agent_dir=str(agent_dir))

    assert [p.name for p in result.profiles] == ["scout"]
    assert result.profiles[0].scope == "user"
    assert result.diagnostics == []


def test_project_dir_scanned_when_trusted(dirs: tuple[Path, Path]) -> None:
    cwd, agent_dir = dirs
    _write(project_agents_dir(cwd) / "local.md", "local")

    result = discover_profiles(cwd, project_trusted=True, agent_dir=str(agent_dir))

    assert [p.name for p in result.profiles] == ["local"]
    assert result.profiles[0].scope == "project"


def test_project_profile_inert_when_untrusted(dirs: tuple[Path, Path]) -> None:
    """Absent from ``list()``, and ``resolve_profile`` says how to fix it."""

    cwd, agent_dir = dirs
    _write(project_agents_dir(cwd) / "local.md", "local")

    result = discover_profiles(cwd, project_trusted=False, agent_dir=str(agent_dir))
    assert result.profiles == []

    with pytest.raises(ProfileError) as exc:
        resolve_profile(
            "local", cwd=cwd, project_trusted=False, agent_dir=str(agent_dir)
        )
    assert "--approve" in str(exc.value)


def test_project_wins_name_collision_with_warning(dirs: tuple[Path, Path]) -> None:
    """Spec §2.2 (ratified). The warning naming BOTH paths is half the
    mitigation; the other half is the confirmation prompt in ``cli/entry.py``."""

    cwd, agent_dir = dirs
    user_path = _write(user_agents_dir(str(agent_dir)) / "scout.md", "scout")
    project_path = _write(project_agents_dir(cwd) / "scout.md", "scout")

    result = discover_profiles(cwd, project_trusted=True, agent_dir=str(agent_dir))

    assert len(result.profiles) == 1
    winner = result.profiles[0]
    assert winner.scope == "project"
    assert winner.file_path == str(project_path)

    collisions = [d for d in result.diagnostics if d.type == "warning"]
    assert len(collisions) == 1
    assert str(user_path) in collisions[0].message
    assert str(project_path) in collisions[0].message


def test_name_field_beats_filename_with_warning(dirs: tuple[Path, Path]) -> None:
    """Identity is the ``name`` FIELD; the mismatch is disclosed, not fatal."""

    cwd, agent_dir = dirs
    _write(user_agents_dir(str(agent_dir)) / "filename.md", "declared")

    result = discover_profiles(cwd, project_trusted=True, agent_dir=str(agent_dir))

    assert [p.name for p in result.profiles] == ["declared"]
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].type == "warning"
    assert "does not match filename" in result.diagnostics[0].message


def test_duplicate_name_in_scope_last_wins_with_warning(
    dirs: tuple[Path, Path],
) -> None:
    cwd, agent_dir = dirs
    first = _write(user_agents_dir(str(agent_dir)) / "a.md", "dup")
    second = _write(user_agents_dir(str(agent_dir)) / "b.md", "dup")

    result = discover_profiles(cwd, project_trusted=True, agent_dir=str(agent_dir))

    assert len(result.profiles) == 1
    # Directory order is sorted by filename, so ``b.md`` is last → it wins.
    assert result.profiles[0].file_path == str(second)
    shadow = [d for d in result.diagnostics if "shadows" in d.message]
    assert len(shadow) == 1
    assert str(first) in shadow[0].message


def test_missing_dirs_are_not_an_error(tmp_path: Path) -> None:
    """Mirrors ``load_skills`` (``skills.py:167-168``): absent → silent."""

    result = discover_profiles(
        tmp_path / "nope",
        project_trusted=True,
        agent_dir=str(tmp_path / "also-nope"),
    )

    assert result.profiles == []
    assert result.diagnostics == []


def test_dotfiles_and_subdirs_skipped(dirs: tuple[Path, Path]) -> None:
    """A profile is a single flat ``<name>.md`` (plan decision 2)."""

    cwd, agent_dir = dirs
    agents = user_agents_dir(str(agent_dir))
    _write(agents / "real.md", "real")
    _write(agents / ".hidden.md", "hidden")
    _write(agents / "nested" / "deep.md", "deep")
    (agents / "notes.txt").write_text("not a profile", encoding="utf-8")
    (agents / "dir-named.md").mkdir()

    result = discover_profiles(cwd, project_trusted=True, agent_dir=str(agent_dir))

    assert [p.name for p in result.profiles] == ["real"]
    assert result.diagnostics == []


def test_classify_scope_by_containment(dirs: tuple[Path, Path]) -> None:
    """THE LAUNDERING FIX.

    A path under ``cwd`` is ``"project"`` no matter that the user named it with
    ``--agent-file``; the user tier is checked FIRST so a user dir that happens
    to live under ``cwd`` is not mis-gated.
    """

    cwd, agent_dir = dirs
    project_path = _write(project_agents_dir(cwd) / "local.md", "local")
    user_path = _write(user_agents_dir(str(agent_dir)) / "scout.md", "scout")
    stray = _write(cwd.parent / "elsewhere" / "stray.md", "stray")

    assert classify_scope(project_path, cwd=cwd, agent_dir=agent_dir) == "project"
    assert classify_scope(user_path, cwd=cwd, agent_dir=agent_dir) == "user"
    assert classify_scope(stray, cwd=cwd, agent_dir=agent_dir) == "explicit"

    # ...and the same verdict through the ``--agent-file`` entry point, which is
    # what makes the prohibition on project ``extensions:`` unforgeable.
    loaded = load_profile_file(
        str(project_path), cwd=cwd, agent_dir=str(agent_dir)
    )
    assert loaded.profile is not None
    assert loaded.profile.scope == "project"

    with pytest.raises(ProfileError) as exc:
        resolve_profile(
            str(project_path),
            cwd=cwd,
            project_trusted=False,
            agent_dir=str(agent_dir),
            is_file=True,
        )
    assert "--approve" in str(exc.value)


def test_symlink_inside_cwd_still_classifies_project(
    dirs: tuple[Path, Path],
) -> None:
    """A symlink is a path the PROJECT can ship — it must not pick its own scope.

    Regression for the review's proven escalation. ``.aelix/agents/helper.md`` as
    a SYMLINK to anywhere outside ``cwd`` used to classify by its target, i.e.
    ``"explicit"``, and one ``aelix --agent-file .aelix/agents/helper.md`` then
    walked past all three gates at once: the ``project_trusted`` refusal here,
    the per-identity confirmation in ``cli/entry.py``, and — the one that matters
    — ``parse_profile``'s ban on ``extensions:`` at project scope, which is the
    cut that keeps an untrusted repo from naming a tier-3 extension path
    (ungated by BOTH discovery kill switches, ``extensions/loader.py:795-859``).

    Nothing here is written outside the fixture tree, and a git clone can carry
    symlinks, so the whole setup is something a repository can ship.
    """

    cwd, agent_dir = dirs
    outside = cwd.parent / "tooling"
    payload = outside / "payload.py"
    outside.mkdir(parents=True, exist_ok=True)
    payload.write_text("def setup(aelix):\n    pass\n", encoding="utf-8")
    target = _write(
        outside / "profiles" / "helper.md", "helper", extra=f"extensions: [{payload}]"
    )

    link_dir = project_agents_dir(cwd)
    link_dir.mkdir(parents=True, exist_ok=True)
    link = link_dir / "helper.md"
    link.symlink_to(target)

    # The control: a REGULAR file at the same spot has always classified project.
    control = _write(link_dir / "control.md", "control")
    assert classify_scope(control, cwd=cwd, agent_dir=agent_dir) == "project"
    assert (
        classify_scope(link.resolve(), cwd=cwd, agent_dir=agent_dir, spelled=link)
        == "project"
    )

    # Gate 3 — the ``extensions:`` prohibition — now fires, so the profile does
    # not exist at all and the extension path never reaches ``parsed.extensions``.
    loaded = load_profile_file(str(link), cwd=cwd, agent_dir=str(agent_dir))
    assert loaded.profile is None
    assert [d.code for d in loaded.diagnostics] == ["scope_forbidden"]

    # ...and gates 1 + 2: ``--agent-file`` on it is fatal, trusted or not.
    for trusted in (False, True):
        with pytest.raises(ProfileError):
            resolve_profile(
                str(link),
                cwd=cwd,
                project_trusted=trusted,
                agent_dir=str(agent_dir),
                is_file=True,
            )


def test_symlink_inside_cwd_without_extensions_is_trust_gated(
    dirs: tuple[Path, Path],
) -> None:
    """The same containment rule, isolated from the ``extensions:`` refusal.

    Without a second case the test above could pass on the parse error alone and
    say nothing about the SCOPE, which is what the trust gate and the
    confirmation prompt both key off.
    """

    cwd, agent_dir = dirs
    target = _write(cwd.parent / "tooling" / "profiles" / "helper.md", "helper")
    link_dir = project_agents_dir(cwd)
    link_dir.mkdir(parents=True, exist_ok=True)
    link = link_dir / "helper.md"
    link.symlink_to(target)

    loaded = load_profile_file(str(link), cwd=cwd, agent_dir=str(agent_dir))
    assert loaded.profile is not None
    assert loaded.profile.scope == "project"

    with pytest.raises(ProfileError) as exc:
        resolve_profile(
            str(link),
            cwd=cwd,
            project_trusted=False,
            agent_dir=str(agent_dir),
            is_file=True,
        )
    assert "--approve" in str(exc.value)


def test_symlink_into_the_user_tier_stays_user(dirs: tuple[Path, Path]) -> None:
    """User is checked FIRST, on the target — the bytes loaded are the user's own.

    The containment union must not over-reach: a link inside ``cwd`` that points
    INTO ``~/.aelix/agent/agents`` resolves to a file the user wrote, so gating it
    behind Project Trust would refuse the user their own profile.
    """

    cwd, agent_dir = dirs
    target = _write(user_agents_dir(str(agent_dir)) / "mine.md", "mine")
    link_dir = project_agents_dir(cwd)
    link_dir.mkdir(parents=True, exist_ok=True)
    link = link_dir / "mine.md"
    link.symlink_to(target)

    loaded = load_profile_file(str(link), cwd=cwd, agent_dir=str(agent_dir))
    assert loaded.profile is not None
    assert loaded.profile.scope == "user"


def test_resolve_profile_escalates_missing_skill_path_to_fatal(
    dirs: tuple[Path, Path],
) -> None:
    """A silently-absent skill is the same class of problem as the wrong
    identity — the render path warns, the run path refuses (plan decision 3)."""

    cwd, agent_dir = dirs
    _write(
        user_agents_dir(str(agent_dir)) / "scout.md",
        "scout",
        extra="skills: [nope]",
    )

    listed = discover_profiles(cwd, project_trusted=True, agent_dir=str(agent_dir))
    assert [p.name for p in listed.profiles] == ["scout"]
    assert [d.code for d in listed.diagnostics] == ["missing_path"]

    with pytest.raises(ProfileError) as exc:
        resolve_profile(
            "scout", cwd=cwd, project_trusted=True, agent_dir=str(agent_dir)
        )
    assert "do not exist" in str(exc.value)


def test_resolve_profile_unknown_name_lists_available(
    dirs: tuple[Path, Path],
) -> None:
    cwd, agent_dir = dirs
    _write(user_agents_dir(str(agent_dir)) / "scout.md", "scout")

    with pytest.raises(ProfileError) as exc:
        resolve_profile(
            "nope", cwd=cwd, project_trusted=True, agent_dir=str(agent_dir)
        )
    assert "scout" in str(exc.value)


# === The BUNDLED tier (the on-ramp) ==========================================
#
# ``profile`` is a REQUIRED parameter of the ``agent`` tool, so before this tier
# existed a default install had no legal value to pass and the tool's own
# description said so. These pin the two halves that make the on-ramp real: that
# something ships, and that shipping it takes nothing away from the user.


def test_a_profile_actually_ships_in_the_wheel() -> None:
    """The ONE assertion here that reads the real installed directory.

    Deliberately not parametrised on a name: which starter profile ships is a
    product decision that may change, and pinning the name here would make this
    file fail for a reason it is not about. What must never regress is that the
    directory is non-empty and that what is in it LOADS — a bundled profile that
    exists but raises is worse than none, because discovery reports it as a
    diagnostic on every single startup.
    """

    shipped = sorted(_REAL_BUNDLED_DIR.glob("*.md"))
    assert shipped, f"no bundled profile ships in {_REAL_BUNDLED_DIR}"
    for path in shipped:
        result = load_profile_file(str(path), cwd=_REAL_BUNDLED_DIR)
        errors = [d for d in result.diagnostics if d.type == "error"]
        assert not errors, f"{path} does not load: {errors}"
        assert result.profile is not None
        # The description is what the model reads in the ``agent`` tool's schema
        # to decide whether this profile fits the task. A bundled profile without
        # one is a name the model cannot choose between.
        assert result.profile.description, f"{path} must describe itself"


def test_bundled_general_purpose_is_a_full_worker_that_inherits_consent() -> None:
    """The ``general-purpose`` starter is the write-capable delegation default.

    Its shape is a deliberate product + security decision (owner, 2026-08-07):
    a full-toolset worker, but bounded so it cannot escalate the trust surface —
    * ``tools`` unset → the full default tool set (a real worker that can edit
      and run commands), NOT an allowlist;
    * ``role: leaf`` → it cannot itself delegate, so a default profile can never
      open an unbounded delegation chain;
    * ``approval_mode`` inherits → every edit and command it runs goes through
      the SAME consent the parent is under. It must never be ``auto`` — that
      would hand a child silent write/exec on a default install.
    Pin all three so a later edit that restricts tools, lets it orchestrate, or
    auto-approves it is caught here.
    """

    result = load_profile_file(
        str(_REAL_BUNDLED_DIR / "general-purpose.md"), cwd=_REAL_BUNDLED_DIR
    )
    assert not [d for d in result.diagnostics if d.type == "error"]
    profile = result.profile
    assert profile is not None
    assert profile.name == "general-purpose"
    assert profile.description
    assert profile.role == "leaf"
    assert profile.tools is None, "unset tools = full toolset; an allowlist would narrow it"
    assert profile.approval_mode in (None, "inherit"), (
        "a default write/exec worker must inherit consent, never auto-approve"
    )


def test_bundled_tier_is_discovered_on_a_default_install(
    dirs: tuple[Path, Path], _empty_bundled_tier: Path
) -> None:
    """Nothing on disk anywhere, and delegation still has a legal profile."""

    cwd, agent_dir = dirs
    _write(_empty_bundled_tier / "explorer.md", "explorer")

    result = discover_profiles(cwd, project_trusted=False, agent_dir=str(agent_dir))

    assert [p.name for p in result.profiles] == ["explorer"]
    assert result.profiles[0].scope == "bundled"
    assert result.diagnostics == []


def test_a_user_profile_of_the_same_name_beats_the_bundled_one(
    dirs: tuple[Path, Path], _empty_bundled_tier: Path
) -> None:
    """The property that keeps a bundled profile a starting point, not a cage.

    The tier order is bundled → user → project and the last writer wins, so a
    user who writes their own ``explorer`` replaces ours outright rather than
    colliding with something they cannot delete.
    """

    cwd, agent_dir = dirs
    _write(_empty_bundled_tier / "explorer.md", "explorer")
    mine = _write(user_agents_dir(str(agent_dir)) / "explorer.md", "explorer")

    result = discover_profiles(cwd, project_trusted=True, agent_dir=str(agent_dir))

    assert len(result.profiles) == 1
    winner = result.profiles[0]
    assert winner.scope == "user"
    assert winner.file_path == str(mine)

    # Disclosed rather than silent, and the message names the scopes it actually
    # shadowed — the collision text is computed from the profiles, so it reads
    # "user … overrides the bundled profile", not a hardcoded project/user pair.
    warnings = [d for d in result.diagnostics if d.type == "warning"]
    assert len(warnings) == 1
    assert "user" in warnings[0].message and "bundled" in warnings[0].message


def test_bundled_scope_is_classified_by_containment(
    _empty_bundled_tier: Path,
) -> None:
    """``--agent-file`` naming a bundled path must not read as ``project``.

    Scope is decided by resolved-path containment, and a user who runs aelix
    from inside site-packages (or from this source tree) would otherwise see the
    shipped profile trust-gated as project-local.
    """

    path = _write(_empty_bundled_tier / "explorer.md", "explorer")

    assert (
        classify_scope(
            path,
            cwd=_empty_bundled_tier.parent,
            agent_dir=_empty_bundled_tier.parent / "agent",
        )
        == "bundled"
    )
