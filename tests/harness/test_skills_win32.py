r"""The skills loader on a Windows path, driven from any platform.

Everything the loader knows about a skill's identity it reads off the path: the
name is its containing directory's name, and the wire line that tells the model
where a skill's relative references resolve is that path's directory part. Both
went through helpers that split on a literal ``/``, so on Windows the directory
part of every skill was ``/``, the name derived from it was the empty string,
and ``_validate_name`` rejected the whole catalogue — packaged, project and user
tiers alike — with `name "extending-aelix" does not match parent directory ""`.
The third helper, the one that produces the path an ignore rule is matched
against, returned the full absolute path instead of a relative one, so
``.gitignore`` / ``.ignore`` / ``.fdignore`` were read, prefixed, and then
matched by nothing.

None of that needs a Windows runner. The failure is driven by the SEPARATOR in
the path, not by ``sys.platform``, so a Windows-shaped string — and
:class:`~pathlib.PureWindowsPath`, which does exactly the path algebra a Windows
:class:`~pathlib.Path` does — reproduces it on Linux and macOS. That is the
injection style ``tests/tools/test_bash_shell_win32.py`` and
``tests/cli/test_stdio_encoding_win32.py`` use, and it is why these cases carry
no ``skipif``: a test that only runs on the platform we cannot run on is not a
regression guard.

The bug being pinned, measured on darwin before the fix against the path a
Windows install hands the loader,
``C:\Users\me\AppData\Roaming\aelix\skills\extending-aelix\SKILL.md``:

    _dirname(p)                          -> '/'
    _basename(_dirname(p))               -> ''
    _validate_name('extending-aelix', _) -> ['name "extending-aelix" does not
                                             match parent directory ""']
    format_skill_invocation(skill)       -> 'References are relative to /.'
    _relative_path(root, skill_dir)      -> 'C:\\sk\\extending-aelix'
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

import pathspec
import pytest
from aelix_agent_core.harness.skills import (
    Skill,
    _basename,
    _dirname,
    _relative_path,
    _validate_name,
    format_skill_invocation,
)

_WIN_SKILL_MD = r"C:\Users\me\AppData\Roaming\aelix\skills\extending-aelix\SKILL.md"
_WIN_SKILL_DIR = r"C:\Users\me\AppData\Roaming\aelix\skills\extending-aelix"

# The four shapes a SKILL.md path actually arrives in. The mixed one is not
# exotic: Windows accepts ``/`` everywhere, so a config value or a CLI argument
# typed with forward slashes is joined onto native separators by pathlib and
# reaches the loader half and half.
_PATHS = [
    pytest.param(
        "/home/me/.aelix/agent/skills/extending-aelix/SKILL.md",
        "/home/me/.aelix/agent/skills/extending-aelix",
        id="posix",
    ),
    pytest.param(_WIN_SKILL_MD, _WIN_SKILL_DIR, id="windows"),
    pytest.param(
        r"C:/Users/me/aelix\skills\extending-aelix\SKILL.md",
        r"C:/Users/me/aelix\skills\extending-aelix",
        id="mixed-separators",
    ),
    pytest.param(
        r"\\fileserver\profiles\me\aelix\skills\extending-aelix\SKILL.md",
        r"\\fileserver\profiles\me\aelix\skills\extending-aelix",
        id="unc-share",
    ),
]


@pytest.mark.parametrize(("skill_md", "skill_dir"), _PATHS)
def test_the_skill_directory_is_read_off_either_separator(
    skill_md: str, skill_dir: str
) -> None:
    """A native path keeps its own separators on the way out: the value is
    shown to the model as somewhere to resolve references from, so it has to
    stay openable on the machine it names."""

    assert _dirname(skill_md) == skill_dir
    assert _basename(skill_md) == "SKILL.md"


@pytest.mark.parametrize(("skill_md", "skill_dir"), _PATHS)
def test_the_skill_name_comes_off_the_parent_directory(
    skill_md: str, skill_dir: str
) -> None:
    """The whole blast radius, in the composition ``_load_skill_from_file``
    itself runs.

    That value is both the fallback name for a SKILL.md whose frontmatter
    declares none and the name every declared one is validated against, so when
    it degrades to the empty string the loader does not merely mislabel a skill
    — it emits ``invalid_metadata`` for every skill on the machine.
    """

    parent_dir_name = _basename(_dirname(skill_md))

    assert parent_dir_name == _basename(skill_dir) == "extending-aelix"
    assert _validate_name("extending-aelix", parent_dir_name) == []


def test_the_wire_location_line_names_the_skills_own_directory() -> None:
    """``format_skill_invocation`` is the public surface of ``_dirname``.

    Its second line tells the model where the skill's relative references
    resolve; on Windows it said ``/``, which is not the skill's directory on
    any machine.
    """

    skill = Skill(
        name="extending-aelix",
        description="d",
        content="C",
        file_path=_WIN_SKILL_MD,
    )

    assert (
        f"References are relative to {_WIN_SKILL_DIR}."
        in format_skill_invocation(skill)
    )


def test_an_ignore_rule_still_matches_under_a_windows_root() -> None:
    """The ignore path is LOGICAL, and therefore POSIX on every platform.

    ``pathspec``'s ``gitwildmatch`` patterns are ``/``-separated wherever they
    run, and ``_add_ignore_rules`` prefixes a nested ignore file's patterns the
    same way, so the string matched against them has to be a relative POSIX
    path even when both ends of the subtraction are Windows paths.
    """

    root = PureWindowsPath(r"C:\Users\me\aelix\skills")
    skill_dir = root / "extending-aelix"

    assert _relative_path(root, skill_dir) == "extending-aelix"
    assert _relative_path(root, skill_dir / "SKILL.md") == "extending-aelix/SKILL.md"
    # Pi parity, unchanged: the root of the scan relativises to the empty
    # string, which ``_add_ignore_rules`` reads as "no prefix", and a POSIX
    # root keeps answering exactly what it answered before.
    assert _relative_path(root, root) == ""
    posix_root = PurePosixPath("/home/me/.aelix/agent/skills")
    assert _relative_path(posix_root, posix_root / "extending-aelix") == (
        "extending-aelix"
    )

    matcher = pathspec.PathSpec.from_lines("gitwildmatch", ["extending-aelix/"])
    assert matcher.match_file(f"{_relative_path(root, skill_dir)}/")
