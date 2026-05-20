"""Pi parity: ``packages/agent/src/harness/skills.ts`` (SHA 734e08e).

Loads skills from disk via recursive SKILL.md scan + root .md files,
honors ``.gitignore`` / ``.ignore`` / ``.fdignore`` rules, validates
frontmatter.

Pi reference: ``skills.ts:1-375``. The Aelix port mirrors Pi
line-by-line:

- :func:`load_skills` (Pi ``loadSkills``, lines 49-75)
- ``_load_skills_from_dir_internal`` (Pi ``loadSkillsFromDirInternal``,
  lines 103-175)
- ``_add_ignore_rules`` (Pi ``addIgnoreRules``, lines 177-213)
- ``_load_skill_from_file`` (Pi ``loadSkillFromFile``, lines 233-279)
- ``_validate_name`` / ``_validate_description`` (Pi lines 281-301)
- :func:`format_skill_invocation` (Pi ``formatSkillInvocation``,
  lines 38-41)

Aelix divergences from Pi (intentional, justified):

- No ``ExecutionEnv`` abstraction — direct :mod:`pathlib`.
- ``loadSourcedSkills`` (Pi lines 83-101) deferred — Sprint 6h₁ ships
  only the bare loader.
- Pi uses the npm ``ignore`` package; Aelix uses
  :mod:`pathspec` (``gitwildmatch`` flavour) which mirrors the
  ``.gitignore`` semantics Pi relies on.
- ``parseFrontmatter`` is shared with the prompt-templates loader via
  :mod:`._frontmatter` (Sprint 6h₁ W6 — W4 m4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pathspec

from aelix_agent_core.harness._frontmatter import parse_frontmatter

# Pi parity: ``skills.ts:5-7``.
_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024
_IGNORE_FILE_NAMES = (".gitignore", ".ignore", ".fdignore")
# Pi parity: ``skills.ts:285`` — lowercase a-z, digits, hyphens only.
_NAME_REGEX = re.compile(r"^[a-z0-9-]+$")


SkillDiagnosticCode = Literal[
    "file_info_failed",
    "list_failed",
    "read_failed",
    "parse_failed",
    "invalid_metadata",
]


@dataclass(frozen=True)
class Skill:
    """Pi parity: ``Skill`` interface (``types.ts``).

    Shape:
      - ``name`` (must match parent directory; lowercase + digits +
        hyphens; ≤ 64 chars; no leading/trailing/consecutive hyphens).
      - ``description`` (frontmatter, required non-empty; ≤ 1024 chars).
      - ``content`` (markdown body).
      - ``file_path`` (absolute path to the SKILL.md / root .md file).
      - ``disable_model_invocation`` (optional, frontmatter
        ``disable-model-invocation``).
    """

    name: str
    description: str
    content: str
    file_path: str
    disable_model_invocation: bool = False


@dataclass(frozen=True)
class SkillDiagnostic:
    """Pi parity: ``SkillDiagnostic`` (``skills.ts:19-28``)."""

    code: SkillDiagnosticCode
    message: str
    path: str
    type: Literal["warning"] = "warning"


@dataclass(frozen=True)
class LoadSkillsResult:
    """Pi parity: ``{skills, diagnostics}`` return type."""

    skills: list[Skill] = field(default_factory=list)
    diagnostics: list[SkillDiagnostic] = field(default_factory=list)


# Pi parity: ``skills.ts:38-41`` — ``formatSkillInvocation``.
def format_skill_invocation(
    skill: Skill, additional_instructions: str | None = None
) -> str:
    """Pi parity: ``formatSkillInvocation(skill, additionalInstructions?)``.

    Wire shape (Pi byte-for-byte):
        ``<skill name="{name}" location="{file_path}">``\\n
        ``References are relative to {dirname}.``\\n\\n
        ``{content}``\\n
        ``</skill>``

    When ``additional_instructions`` is supplied, append ``\\n\\n`` then
    the instructions.
    """

    file_path = skill.file_path
    dirname = _dirname(file_path)
    skill_block = (
        f'<skill name="{skill.name}" location="{file_path}">\n'
        f"References are relative to {dirname}.\n\n"
        f"{skill.content}\n"
        f"</skill>"
    )
    if additional_instructions:
        return f"{skill_block}\n\n{additional_instructions}"
    return skill_block


# Pi parity: ``skills.ts:49-75`` — ``loadSkills``.
def load_skills(dirs: str | Path | list[str | Path]) -> LoadSkillsResult:
    """Pi parity: ``loadSkills(env, dirs)``.

    For each input directory:
      - Recursive scan for ``SKILL.md``. The first ``SKILL.md`` found in
        a directory wins; that directory is NOT descended further.
      - For the root directory only, also load direct ``.md`` children
        as root skills (Pi ``includeRootFiles=true``).
      - Honor ``.gitignore``, ``.ignore``, ``.fdignore`` with
        relative-path prefixing.
      - Skip hidden entries (``.``-prefixed) and ``node_modules``.

    Missing directories are silently skipped (Pi: ``not_found`` is not
    a diagnostic).
    """

    skills: list[Skill] = []
    diagnostics: list[SkillDiagnostic] = []

    normalized: list[Path] = []
    if isinstance(dirs, (str, Path)):
        normalized.append(Path(dirs))
    else:
        normalized.extend(Path(d) for d in dirs)

    for root_dir in normalized:
        # Pi parity (``skills.ts:56-68``): ``fileInfo`` → not_found silent,
        # other errors emit ``file_info_failed``.
        try:
            exists = root_dir.exists()
        except OSError as exc:
            diagnostics.append(
                SkillDiagnostic(
                    code="file_info_failed",
                    message=str(exc),
                    path=str(root_dir),
                )
            )
            continue
        if not exists:
            continue
        if not root_dir.is_dir():
            continue

        # Pi parity (``skills.ts:70``): start the recursive walk with an
        # empty pattern list. The list is built up as ``addIgnoreRules``
        # encounters .gitignore / .ignore / .fdignore at each depth.
        # ``include_root_files=True`` only at the top level.
        _load_skills_from_dir_internal(
            root_dir,
            include_root_files=True,
            patterns=[],
            root_dir=root_dir,
            skills=skills,
            diagnostics=diagnostics,
        )

    return LoadSkillsResult(skills=skills, diagnostics=diagnostics)


# Pi parity: ``skills.ts:103-175`` — ``loadSkillsFromDirInternal``.
def _load_skills_from_dir_internal(
    directory: Path,
    *,
    include_root_files: bool,
    patterns: list[str],
    root_dir: Path,
    skills: list[Skill],
    diagnostics: list[SkillDiagnostic],
) -> None:
    """Recursive helper for :func:`load_skills`.

    ``patterns`` carries the cumulative ignore patterns accumulated by
    ancestor directories. Each level builds a fresh
    :class:`pathspec.PathSpec` from the cumulative list — Pi's
    ``ignore`` package mutates in place; Aelix passes the immutable
    list down each recursion.
    """

    # Pi parity (``skills.ts:113-126``): existence + kind resolution.
    if not directory.exists():
        return
    if not directory.is_dir():
        return

    # Pi parity (``skills.ts:128``): ingest .gitignore / .ignore /
    # .fdignore before scanning entries. Returns the patterns list
    # extended with whatever ignore files exist at this depth.
    patterns = _add_ignore_rules(
        directory, patterns, root_dir=root_dir, diagnostics=diagnostics
    )
    matcher = pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        # Pi parity (``skills.ts:131-134``): ``list_failed``.
        diagnostics.append(
            SkillDiagnostic(
                code="list_failed",
                message=str(exc),
                path=str(directory),
            )
        )
        return

    # Pi parity (``skills.ts:137-149``): first pass — look for SKILL.md.
    # If found and not ignored, load it and STOP recursing into this
    # directory (``return``).
    for entry in entries:
        if entry.name != "SKILL.md":
            continue
        if not entry.is_file():
            continue
        rel_path = _relative_path(root_dir, entry)
        if matcher.match_file(rel_path):
            continue
        _load_skill_from_file(entry, skills, diagnostics)
        return

    # Pi parity (``skills.ts:151-172``): second pass — sorted entries,
    # skip hidden + node_modules, recurse into subdirs (always with
    # include_root_files=False), and at the root level only also load
    # direct .md siblings.
    sorted_entries = sorted(entries, key=lambda p: p.name)
    for entry in sorted_entries:
        if entry.name.startswith(".") or entry.name == "node_modules":
            continue
        rel_path = _relative_path(root_dir, entry)
        # Pi parity (``skills.ts:158``): trailing slash so directory
        # patterns (``foo/``) match.
        ignore_path = f"{rel_path}/" if entry.is_dir() else rel_path
        if matcher.match_file(ignore_path):
            continue

        if entry.is_dir():
            # Pi parity (``skills.ts:161-165``): recursive descent with
            # ``includeRootFiles=false``.
            _load_skills_from_dir_internal(
                entry,
                include_root_files=False,
                patterns=patterns,
                root_dir=root_dir,
                skills=skills,
                diagnostics=diagnostics,
            )
            continue

        # Pi parity (``skills.ts:168-171``): root-level .md files only.
        if not entry.is_file():
            continue
        if not include_root_files:
            continue
        if not entry.name.endswith(".md"):
            continue
        _load_skill_from_file(entry, skills, diagnostics)


# Pi parity: ``skills.ts:177-213`` — ``addIgnoreRules``.
def _add_ignore_rules(
    directory: Path,
    patterns: list[str],
    *,
    root_dir: Path,
    diagnostics: list[SkillDiagnostic],
) -> list[str]:
    """Append ``.gitignore`` / ``.ignore`` / ``.fdignore`` patterns.

    Pi parity priority (insertion order): ``.gitignore`` → ``.ignore``
    → ``.fdignore``. Patterns are relative-prefixed so a nested
    ``.gitignore`` applies to its subtree only.

    Returns a NEW list (existing patterns + newly read ones); callers
    pass the returned list down the recursion so sibling subtrees see
    the inherited ignore rules but not each other's overrides.
    """

    relative_dir = _relative_path(root_dir, directory)
    prefix = f"{relative_dir}/" if relative_dir else ""

    collected: list[str] = []
    for filename in _IGNORE_FILE_NAMES:
        ignore_path = directory / filename
        try:
            exists = ignore_path.exists()
        except OSError as exc:
            diagnostics.append(
                SkillDiagnostic(
                    code="file_info_failed",
                    message=str(exc),
                    path=str(ignore_path),
                )
            )
            continue
        if not exists:
            continue
        if not ignore_path.is_file():
            continue
        try:
            content = ignore_path.read_text(encoding="utf-8")
        except OSError as exc:
            diagnostics.append(
                SkillDiagnostic(
                    code="read_failed",
                    message=str(exc),
                    path=str(ignore_path),
                )
            )
            continue
        for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            prefixed = _prefix_ignore_pattern(line, prefix)
            if prefixed is not None:
                collected.append(prefixed)

    if not collected:
        return patterns
    return [*patterns, *collected]


# Pi parity: ``skills.ts:215-231`` — ``prefixIgnorePattern``.
def _prefix_ignore_pattern(line: str, prefix: str) -> str | None:
    """Return the prefixed pattern, or ``None`` to drop the line."""

    trimmed = line.strip()
    if not trimmed:
        return None
    # Pi parity: comment lines (``#`` prefix) are dropped unless escaped
    # (``\\#``).
    if trimmed.startswith("#") and not trimmed.startswith("\\#"):
        return None

    pattern = line
    negated = False
    if pattern.startswith("!"):
        negated = True
        pattern = pattern[1:]
    elif pattern.startswith("\\!"):
        pattern = pattern[1:]
    if pattern.startswith("/"):
        pattern = pattern[1:]
    prefixed = f"{prefix}{pattern}" if prefix else pattern
    return f"!{prefixed}" if negated else prefixed


# Pi parity: ``skills.ts:233-279`` — ``loadSkillFromFile``.
def _load_skill_from_file(
    file_path: Path,
    skills: list[Skill],
    diagnostics: list[SkillDiagnostic],
) -> None:
    """Read + validate a SKILL.md (or root .md) file."""

    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        # Pi parity (``skills.ts:240``): ``read_failed``.
        diagnostics.append(
            SkillDiagnostic(
                code="read_failed",
                message=str(exc),
                path=str(file_path),
            )
        )
        return

    frontmatter, body, parse_error = parse_frontmatter(raw)
    if frontmatter is None:
        # Pi parity (``skills.ts:246``): ``parse_failed``.
        # Sprint 6h₁ W6 (P-233): surface the YAML error so the diagnostic
        # is actionable instead of generic.
        suffix = f": {parse_error}" if parse_error else ""
        diagnostics.append(
            SkillDiagnostic(
                code="parse_failed",
                message=f"failed to parse YAML frontmatter{suffix}",
                path=str(file_path),
            )
        )
        return

    # Pi parity (``skills.ts:251-253``): parent directory name + raw
    # description.
    skill_dir = _dirname(str(file_path))
    parent_dir_name = _basename(skill_dir)
    raw_description = frontmatter.get("description")
    description = raw_description if isinstance(raw_description, str) else None

    # Pi parity (``skills.ts:255-257``): description validation
    # diagnostics first.
    for err in _validate_description(description):
        diagnostics.append(
            SkillDiagnostic(
                code="invalid_metadata",
                message=err,
                path=str(file_path),
            )
        )

    # Pi parity (``skills.ts:259-263``): name from frontmatter or parent
    # dir name; validate against parent dir name + regex + length +
    # hyphen rules.
    raw_name = frontmatter.get("name")
    frontmatter_name = raw_name if isinstance(raw_name, str) else None
    name = frontmatter_name or parent_dir_name
    for err in _validate_name(name, parent_dir_name):
        diagnostics.append(
            SkillDiagnostic(
                code="invalid_metadata",
                message=err,
                path=str(file_path),
            )
        )

    # Pi parity (``skills.ts:265-267``): drop the skill entirely when
    # description is missing or whitespace-only.
    if not description or description.strip() == "":
        return

    # Pi parity (``skills.ts:275``): ``frontmatter["disable-model-invocation"]
    # === true`` (strict ``True``, not truthy).
    disable_raw = frontmatter.get("disable-model-invocation")
    disable_model_invocation = disable_raw is True

    skills.append(
        Skill(
            name=name,
            description=description,
            content=body,
            file_path=str(file_path),
            disable_model_invocation=disable_model_invocation,
        )
    )


# Pi parity: ``skills.ts:281-291`` — ``validateName``.
def _validate_name(name: str, parent_dir_name: str) -> list[str]:
    """Return validation errors for ``name``. Empty list means valid."""

    errors: list[str] = []
    if name != parent_dir_name:
        errors.append(
            f'name "{name}" does not match parent directory "{parent_dir_name}"'
        )
    if len(name) > _MAX_NAME_LENGTH:
        errors.append(
            f"name exceeds {_MAX_NAME_LENGTH} characters ({len(name)})"
        )
    if not _NAME_REGEX.match(name):
        errors.append(
            "name contains invalid characters (must be lowercase a-z, 0-9, hyphens only)"
        )
    if name.startswith("-") or name.endswith("-"):
        errors.append("name must not start or end with a hyphen")
    if "--" in name:
        errors.append("name must not contain consecutive hyphens")
    return errors


# Pi parity: ``skills.ts:293-301`` — ``validateDescription``.
def _validate_description(description: str | None) -> list[str]:
    """Return validation errors for ``description``."""

    errors: list[str] = []
    if description is None or description.strip() == "":
        errors.append("description is required")
    elif len(description) > _MAX_DESCRIPTION_LENGTH:
        errors.append(
            f"description exceeds {_MAX_DESCRIPTION_LENGTH} characters "
            f"({len(description)})"
        )
    return errors


# === Path helpers — Pi env-path equivalents using stdlib ====================


def _dirname(path: str) -> str:
    """Pi parity: ``dirnameEnvPath`` — strip trailing slashes, drop the
    last segment."""

    normalized = path.rstrip("/")
    if "/" not in normalized:
        return "/"
    slash_index = normalized.rfind("/")
    if slash_index <= 0:
        return "/"
    return normalized[:slash_index]


def _basename(path: str) -> str:
    """Pi parity: ``basenameEnvPath`` — strip trailing slashes, take the
    last segment."""

    normalized = path.rstrip("/")
    slash_index = normalized.rfind("/")
    if slash_index == -1:
        return normalized
    return normalized[slash_index + 1 :]


def _relative_path(root: Path, target: Path) -> str:
    """Pi parity: ``relativeEnvPath`` — relative path string from
    ``root`` to ``target`` using forward slashes; empty when target ==
    root."""

    root_str = str(root).rstrip("/")
    target_str = str(target).rstrip("/")
    if target_str == root_str:
        return ""
    if target_str.startswith(f"{root_str}/"):
        return target_str[len(root_str) + 1 :]
    return target_str.lstrip("/")


__all__ = [
    "LoadSkillsResult",
    "Skill",
    "SkillDiagnostic",
    "SkillDiagnosticCode",
    "format_skill_invocation",
    "load_skills",
]
