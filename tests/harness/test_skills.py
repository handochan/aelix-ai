"""Pi parity: ``packages/agent/src/harness/skills.ts``.

Sprint 6h₁ §G unit coverage. Pi reference: SHA ``734e08e``.
"""

from __future__ import annotations

from pathlib import Path

from aelix_agent_core.harness.skills import (
    Skill,
    SkillDiagnostic,
    format_skill_invocation,
    load_skills,
)

# === load_skills — SKILL.md discovery =========================================


def test_load_recursive_skill_md(tmp_path: Path) -> None:
    """Pi parity: ``loadSkillsFromDirInternal`` recursively finds SKILL.md."""

    sub = tmp_path / "my-skill"
    sub.mkdir()
    (sub / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: desc\n---\nbody"
    )
    result = load_skills([tmp_path])
    assert len(result.skills) == 1
    skill = result.skills[0]
    assert skill.name == "my-skill"
    assert skill.description == "desc"
    assert skill.content == "body"
    assert skill.file_path.endswith("SKILL.md")
    assert skill.disable_model_invocation is False
    assert result.diagnostics == []


def test_skill_md_first_hit_stops_recursion(tmp_path: Path) -> None:
    """Pi parity: ``return`` after first SKILL.md — no descent inside."""

    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "SKILL.md").write_text(
        "---\nname: outer\ndescription: outer skill\n---\nbody"
    )
    inner = outer / "inner-skill"
    inner.mkdir()
    (inner / "SKILL.md").write_text(
        "---\nname: inner-skill\ndescription: should not appear\n---\nbody"
    )
    result = load_skills([tmp_path])
    names = {s.name for s in result.skills}
    assert "outer" in names
    assert "inner-skill" not in names


def test_load_root_md_files(tmp_path: Path) -> None:
    """Pi parity: ``includeRootFiles=True`` at the top level only."""

    # Root .md file with frontmatter (no parent dir match needed because
    # the name matches the parent dir at the file level only when no
    # frontmatter ``name`` is provided. We supply a matching ``name``.)
    parent = tmp_path / "skills-root"
    parent.mkdir()
    md = parent / "root.md"
    md.write_text(
        # name validation requires name == parent dir name (Pi parity).
        # Root .md uses the parent directory, which is ``skills-root``.
        # Supply matching name explicitly.
        "---\nname: skills-root\ndescription: root level\n---\nbody"
    )
    result = load_skills([parent])
    names = {s.name for s in result.skills}
    assert "skills-root" in names


def test_missing_dir_silent(tmp_path: Path) -> None:
    """Pi parity: ``not_found`` → silent skip."""

    result = load_skills([tmp_path / "absent"])
    assert result.skills == []
    assert result.diagnostics == []


# === Ignore file honoring =====================================================


def test_gitignore_excludes_skill(tmp_path: Path) -> None:
    """Pi parity: ``.gitignore`` honoured during scan."""

    (tmp_path / ".gitignore").write_text("ignored-skill/\n")
    ignored = tmp_path / "ignored-skill"
    ignored.mkdir()
    (ignored / "SKILL.md").write_text(
        "---\nname: ignored-skill\ndescription: hidden\n---\nbody"
    )
    visible = tmp_path / "visible-skill"
    visible.mkdir()
    (visible / "SKILL.md").write_text(
        "---\nname: visible-skill\ndescription: ok\n---\nbody"
    )
    result = load_skills([tmp_path])
    names = {s.name for s in result.skills}
    assert "visible-skill" in names
    assert "ignored-skill" not in names


def test_ignore_file_honored(tmp_path: Path) -> None:
    """Pi parity: ``.ignore`` honoured alongside ``.gitignore``."""

    (tmp_path / ".ignore").write_text("hidden-skill/\n")
    hidden = tmp_path / "hidden-skill"
    hidden.mkdir()
    (hidden / "SKILL.md").write_text(
        "---\nname: hidden-skill\ndescription: hidden\n---\nbody"
    )
    result = load_skills([tmp_path])
    assert {s.name for s in result.skills} == set()


def test_fdignore_file_honored(tmp_path: Path) -> None:
    """Pi parity: ``.fdignore`` honoured alongside the other ignore files."""

    (tmp_path / ".fdignore").write_text("blocked-skill/\n")
    blocked = tmp_path / "blocked-skill"
    blocked.mkdir()
    (blocked / "SKILL.md").write_text(
        "---\nname: blocked-skill\ndescription: blocked\n---\nbody"
    )
    result = load_skills([tmp_path])
    assert {s.name for s in result.skills} == set()


def test_hidden_dirs_skipped(tmp_path: Path) -> None:
    """Pi parity: entries starting with ``.`` are skipped during recursion."""

    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "SKILL.md").write_text(
        "---\nname: hidden\ndescription: x\n---\nbody"
    )
    result = load_skills([tmp_path])
    assert result.skills == []


def test_node_modules_skipped(tmp_path: Path) -> None:
    """Pi parity: ``node_modules`` is hard-coded skip."""

    nm = tmp_path / "node_modules"
    nm.mkdir()
    inner = nm / "pkg-skill"
    inner.mkdir()
    (inner / "SKILL.md").write_text(
        "---\nname: pkg-skill\ndescription: x\n---\nbody"
    )
    result = load_skills([tmp_path])
    assert result.skills == []


# === Name validation ==========================================================


def test_invalid_name_uppercase_rejected(tmp_path: Path) -> None:
    """Pi parity: name regex ``^[a-z0-9-]+$`` — uppercase rejected."""

    bad = tmp_path / "BadCase"
    bad.mkdir()
    (bad / "SKILL.md").write_text(
        "---\nname: BadCase\ndescription: x\n---\nbody"
    )
    result = load_skills([tmp_path])
    # Skill still returned (Pi behaviour — only missing description drops it),
    # but a diagnostic is emitted.
    codes = [d.code for d in result.diagnostics]
    assert "invalid_metadata" in codes


def test_invalid_name_mismatch_with_parent(tmp_path: Path) -> None:
    """Pi parity: name must match parent directory."""

    parent = tmp_path / "right-name"
    parent.mkdir()
    (parent / "SKILL.md").write_text(
        "---\nname: wrong-name\ndescription: x\n---\nbody"
    )
    result = load_skills([tmp_path])
    msgs = [d.message for d in result.diagnostics if d.code == "invalid_metadata"]
    assert any("does not match parent" in m for m in msgs)


def test_invalid_name_leading_hyphen(tmp_path: Path) -> None:
    """Pi parity: leading hyphen rejected."""

    parent = tmp_path / "-bad"
    parent.mkdir()
    (parent / "SKILL.md").write_text(
        "---\nname: -bad\ndescription: x\n---\nbody"
    )
    result = load_skills([tmp_path])
    msgs = [d.message for d in result.diagnostics if d.code == "invalid_metadata"]
    assert any("start or end with a hyphen" in m for m in msgs)


def test_invalid_name_consecutive_hyphens(tmp_path: Path) -> None:
    """Pi parity: ``--`` rejected."""

    parent = tmp_path / "bad--name"
    parent.mkdir()
    (parent / "SKILL.md").write_text(
        "---\nname: bad--name\ndescription: x\n---\nbody"
    )
    result = load_skills([tmp_path])
    msgs = [d.message for d in result.diagnostics if d.code == "invalid_metadata"]
    assert any("consecutive hyphens" in m for m in msgs)


def test_invalid_name_too_long(tmp_path: Path) -> None:
    """Pi parity: name > 64 chars rejected."""

    long_name = "a" * 65
    parent = tmp_path / long_name
    parent.mkdir()
    (parent / "SKILL.md").write_text(
        f"---\nname: {long_name}\ndescription: x\n---\nbody"
    )
    result = load_skills([tmp_path])
    msgs = [d.message for d in result.diagnostics if d.code == "invalid_metadata"]
    assert any("exceeds 64" in m for m in msgs)


# === Description validation ===================================================


def test_missing_description_drops_skill(tmp_path: Path) -> None:
    """Pi parity: missing/empty description → skill dropped + diagnostic."""

    parent = tmp_path / "no-desc"
    parent.mkdir()
    (parent / "SKILL.md").write_text("---\nname: no-desc\n---\nbody")
    result = load_skills([tmp_path])
    assert result.skills == []
    codes = [d.code for d in result.diagnostics]
    assert "invalid_metadata" in codes


def test_whitespace_description_drops_skill(tmp_path: Path) -> None:
    """Pi parity: whitespace-only description treated as missing."""

    parent = tmp_path / "ws-desc"
    parent.mkdir()
    (parent / "SKILL.md").write_text(
        '---\nname: ws-desc\ndescription: "   "\n---\nbody'
    )
    result = load_skills([tmp_path])
    assert result.skills == []


# === disable-model-invocation flag ===========================================


def test_disable_model_invocation_true(tmp_path: Path) -> None:
    """Pi parity: ``disable-model-invocation: true`` strict bool."""

    parent = tmp_path / "manual"
    parent.mkdir()
    (parent / "SKILL.md").write_text(
        "---\nname: manual\ndescription: m\ndisable-model-invocation: true\n---\nbody"
    )
    result = load_skills([tmp_path])
    assert result.skills[0].disable_model_invocation is True


def test_disable_model_invocation_integer_one_is_false(tmp_path: Path) -> None:
    """Pi parity: only literal ``True`` flips the bit.

    Sprint 6h₁ W6 (W4 m2): the prior assertion ``in (True, False)`` was
    tautological. PyYAML maps ``1`` to the integer ``1`` (truthy but
    NOT ``is True``); Pi's ``frontmatter['disable-model-invocation'] === true``
    rejects this case. The Aelix port matches via the
    ``disable_raw is True`` guard in
    :func:`aelix_agent_core.harness.skills._load_skill_from_file`.
    """

    parent = tmp_path / "auto"
    parent.mkdir()
    (parent / "SKILL.md").write_text(
        "---\nname: auto\ndescription: a\ndisable-model-invocation: 1\n---\nbody"
    )
    result = load_skills([tmp_path])
    assert result.skills[0].disable_model_invocation is False


def test_disable_model_invocation_string_is_false(tmp_path: Path) -> None:
    parent = tmp_path / "auto2"
    parent.mkdir()
    (parent / "SKILL.md").write_text(
        '---\nname: auto2\ndescription: a\ndisable-model-invocation: "true"\n---\nbody'
    )
    result = load_skills([tmp_path])
    # The string ``"true"`` is NOT literally True — flag stays False.
    assert result.skills[0].disable_model_invocation is False


# === format_skill_invocation ==================================================


def test_format_invocation_basic() -> None:
    """Pi parity: wire shape ``<skill name=... location=...>...``."""

    skill = Skill(
        name="my-skill",
        description="d",
        content="C",
        file_path="/foo/my-skill/SKILL.md",
    )
    out = format_skill_invocation(skill)
    assert '<skill name="my-skill" location="/foo/my-skill/SKILL.md">' in out
    assert "References are relative to /foo/my-skill." in out
    assert "C" in out
    assert out.endswith("</skill>")


def test_format_invocation_with_additional_instructions() -> None:
    """Pi parity: additional instructions appended with ``\\n\\n``."""

    skill = Skill(
        name="x",
        description="d",
        content="body",
        file_path="/p/x/SKILL.md",
    )
    out = format_skill_invocation(skill, "EXTRA")
    assert out.endswith("\n\nEXTRA")
    # Skill block still present before the instructions.
    assert "</skill>\n\nEXTRA" in out


def test_diagnostic_type_is_warning(tmp_path: Path) -> None:
    """Closure check: every emitted diagnostic carries ``type="warning"``."""

    parent = tmp_path / "no-desc"
    parent.mkdir()
    (parent / "SKILL.md").write_text("---\nname: no-desc\n---\nbody")
    result = load_skills([tmp_path])
    for d in result.diagnostics:
        assert isinstance(d, SkillDiagnostic)
        assert d.type == "warning"
