"""The Agent Skills directories: ``~/.agents/skills`` and the project's ``.agents/skills`` (#509).

pi reads both (``package-manager.ts`` ``collectAncestorAgentsSkillDirs`` and
``addAutoDiscoveredResources``), and other agents write skills there, so a user
who made skills for another tool had none in aelix. ADR-0281 decides:

* ``~/.agents/skills`` is a GLOBAL source with no trust gate, read right after
  ``~/.aelix/agent/skills`` (:func:`user_agents_skills_dir`);
* ``<dir>/.agents/skills`` is a PROJECT source for every ``<dir>`` from the cwd
  up to the git root — or the filesystem root outside a repository — nearest
  first, read only in a trusted project (:func:`project_agents_skill_dirs`);
* the trust gate counts exactly that list (:func:`trust_requiring_agents_skill_dirs`),
  so trust covers everything the scan reads: one walk serves both, and neither
  can grow without the other. pi's trust check walks every ancestor, past the
  git root its scan stops at (``trust-manager.ts`` ``hasTrustRequiringProjectResources``),
  which asks about folders it never reads.

The user's own ``~/.agents/skills`` is left out of the walk wherever it is met
— a folder under the home directory outside a repository reaches it — so it is
read once, as GLOBAL, and never makes the folder ask (pi filters it out of both
the scan and the trust check with ``pathsEqual``). It is compared by file
identity (:func:`path_key`), so a project's symlink to it, or a ``HOME`` spelled
in another case on a case-insensitive volume, is the user's directory too.

Stdlib only: ``cli/project_trust.py`` imports this at launch.
"""

from __future__ import annotations

import os
from pathlib import Path

AGENTS_DIR_NAME = ".agents"
"""The Agent Skills directory name, at the home directory and in a project."""

PARENTS_SPELLED = 3
"""How many ``..`` :func:`parent_relative` writes out before it counts them."""


def user_agents_skills_dir() -> Path:
    """``~/.agents/skills`` (pi ``join(getHomeDir(), ".agents", "skills")``).

    The home directory, not the agent directory: ``AELIX_CODING_AGENT_DIR``
    moves aelix's own files, not another tool's. A repository ``.env`` cannot
    move it (``HOME`` is on ``_DOTENV_LOCKED``, ADR-0203).
    """

    return Path.home() / AGENTS_DIR_NAME / "skills"


def path_key(path: str | os.PathLike[str]) -> tuple[int, int] | str:
    """What makes two spellings of one existing file or directory equal: its device and inode.

    The first key was ``normcase(realpath(path))``, and on macOS it is not one:
    ``normcase`` changes nothing on darwin and ``realpath`` keeps the case it is
    given, so a ``HOME`` spelled ``…/Home`` for ``…/home`` on the default
    case-insensitive volume — or a project's symlink to it so spelled — made the
    user's own ``~/.agents/skills`` a project directory: the folder asked, and a
    trusted run read it twice with the project copy winning (#509 review F3;
    pi #10488 is the same shape on Windows). ``os.stat`` names the object
    whatever the spelling. A path that cannot be stat'ed, or a filesystem that
    reports no inode, falls back to the string, which no stat key equals.
    """

    try:
        st = os.stat(path)
        if st.st_ino:
            return (st.st_dev, st.st_ino)
    except (OSError, ValueError):
        pass
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(path))


def git_root(start: str | os.PathLike[str]) -> Path | None:
    """The nearest directory from *start* up that holds ``.git`` (pi ``findGitRepoRoot``).

    Anything named ``.git`` counts, as pi's ``existsSync``: a worktree's or a
    submodule's ``.git`` is a file. ``None`` outside a repository.
    """

    current = Path(os.path.abspath(start))
    while True:
        if os.path.exists(current / ".git"):
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def agents_skill_walk(cwd: str | os.PathLike[str]) -> list[Path]:
    """``<dir>/.agents/skills`` for *cwd* and each parent, nearest first, whether or not it exists.

    pi ``collectAncestorAgentsSkillDirs``'s walk: it stops after the git root,
    or at the filesystem root outside a repository. :func:`project_agents_skill_dirs`
    keeps the directories the scan reads; the write gate takes every one,
    because a write can create the folder a link named ``.agents`` points at
    (#646).
    """

    start = Path(os.path.abspath(cwd))
    root = git_root(start)
    walk: list[Path] = []
    current = start
    while True:
        walk.append(current / AGENTS_DIR_NAME / "skills")
        if root is not None and current == root:
            return walk
        parent = current.parent
        if parent == current:
            return walk
        current = parent


def project_agents_skill_dirs(cwd: str | os.PathLike[str]) -> list[Path]:
    """Every ``.agents/skills`` directory a trusted project reads, nearest first.

    pi ``collectAncestorAgentsSkillDirs``: ``<dir>/.agents/skills`` for *cwd*
    and each parent, stopping after the git root, or at the filesystem root
    outside a repository (:func:`agents_skill_walk`). Only directories are
    returned (a missing one loads nothing), and never the user's own
    :func:`user_agents_skills_dir`. The walk is over *cwd*'s own spelling — the
    launch passes ``Path.cwd()``, whose parents are real directories — and
    never raises: a directory that cannot be checked is not one.
    """

    user = path_key(user_agents_skills_dir())
    return [
        candidate
        for candidate in agents_skill_walk(cwd)
        if os.path.isdir(candidate) and path_key(candidate) != user
    ]


def skipped_entry(name: str) -> bool:
    """An entry the skill loader never reads: a hidden one or ``node_modules``.

    ``harness/skills.py`` ``_load_skills_from_dir_internal`` (pi
    ``collectSkillEntries``). The trust check and the untrusted count list a
    directory without loading it, so they take the loader's rule from here.
    """

    return name.startswith(".") or name == "node_modules"


def _may_hold_skills(directory: Path) -> bool:
    """Whether a listing of the Agent Skills *directory* shows anything the loader could read.

    A ``SKILL.md`` in it, or a subdirectory the loader would descend (not
    :func:`skipped_entry`). Its other files are never skills there (ADR-0281
    D5), so a folder that holds only a ``README.md``, a hidden folder or
    ``node_modules`` loads nothing and does not ask (#509 verify V5, review F6).
    No file is opened and nothing below the first level is listed: a
    subdirectory asks even when empty, and an ignore file is not read — the
    safe side, since what this misses would be loaded unasked.
    """

    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name == "SKILL.md" and os.path.isfile(entry.path):
                    return True
                if skipped_entry(entry.name):
                    continue
                try:
                    if entry.is_dir():
                        return True
                except OSError:
                    return True  # cannot tell: ask
    except OSError:
        # Unreadable — the scan cannot read it either, so it loads nothing.
        return False
    return False


def trust_requiring_agents_skill_dirs(cwd: str | os.PathLike[str]) -> list[Path]:
    """The :func:`project_agents_skill_dirs` that make the folder ask: those that may hold a skill.

    The rule of every other directory family (``.aelix/skills`` and the rest,
    ``cli/project_trust.py``) is "an empty directory gates nothing"; here it is
    the loader's own listing rule (:func:`_may_hold_skills`). The trust prompt
    and ``/trust`` name these directories.
    """

    return [d for d in project_agents_skill_dirs(cwd) if _may_hold_skills(d)]


def parent_relative(path: str | os.PathLike[str], cwd: str | os.PathLike[str]) -> str | None:
    """*path* from *cwd* when it is in the ``.agents`` of *cwd* or a parent, else ``None``.

    ``.agents/skills``, ``../../.agents/skills/x/SKILL.md`` — and past
    :data:`PARENTS_SPELLED` levels the ``..`` are counted, ``../×65/.agents/skills``,
    so the text is bounded however deep *cwd* is. Written out, 65 levels pushed
    the 80x24 trust question's last option below the screen (#509 review F5),
    and full paths cut from the right drew every parent's ``/skill`` header the
    same (verify V1). It holds no folder name above ``.agents``: every part
    before it is ``..`` or the count. Another drive on Windows, or a folder
    beside the cwd's line of parents, is ``None``; the caller shows it its own way.
    """

    try:
        parts = os.path.relpath(path, cwd).split(os.sep)
    except ValueError:
        return None
    ups = 0
    while ups < len(parts) and parts[ups] == os.pardir:
        ups += 1
    rest = parts[ups:]
    if not rest or rest[0] != AGENTS_DIR_NAME:
        return None
    lead = [os.pardir] * ups if ups <= PARENTS_SPELLED else [f"{os.pardir}{os.sep}×{ups}"]
    return os.path.join(*lead, *rest)


__all__ = [
    "AGENTS_DIR_NAME",
    "PARENTS_SPELLED",
    "agents_skill_walk",
    "git_root",
    "parent_relative",
    "path_key",
    "project_agents_skill_dirs",
    "skipped_entry",
    "trust_requiring_agents_skill_dirs",
    "user_agents_skills_dir",
]
