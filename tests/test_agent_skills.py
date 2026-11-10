"""The project's agent skills: one canonical tree, a gated mirror, and what each skill must be (#733, ADR-0303).

``.agents/skills/<name>/`` is canonical: aelix reads it in a trusted project
(#509, ADR-0281) and so do other Agent Skills tools. ``.claude/skills/<name>/``
is a byte-identical committed mirror for Claude Code, kept by
``scripts/sync_agent_skills.py`` the way ADR-0218 keeps the bundled guides. The
same shape as ``tests/test_docs_bundle_sync.py``: compare bytes in both
directions, name the repair command in the failure.

Beyond the mirror, every skill must:

* have a ``SKILL.md`` whose frontmatter ``name`` is its directory name, matches
  ``^[a-z0-9]+(-[a-z0-9]+)*$`` and is at most 64 characters, with a non-empty
  ``description`` of at most 1024 (the Agent Skills limits, which aelix's loader
  enforces too);
* load in aelix's own loader, ``load_skills(dir, agents=True)``, which is what
  ``core/skill_selection.py`` calls for an ``.agents/skills`` source, with no
  diagnostic at all;
* hold no home-directory path, no OpenRouter key and no path-and-line citation.
  The citation gate does not scan these trees (it gates ``packages/``, ``tests/``,
  ``docs/guides/`` and the root ``*.md``), so a line number written here would
  rot with nothing to say so; name the construct instead.

``.claude/agents/*.md`` (the subagents) get the name, path and key checks too.

Every check is also driven over a planted tree in ``tmp_path``, so a check that
stopped finding anything fails here rather than passing over the real tree.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness._frontmatter import parse_frontmatter
from aelix_agent_core.harness.skills import load_skills

REPO = Path(__file__).resolve().parents[1]
CANONICAL = REPO / ".agents" / "skills"
MIRROR = REPO / ".claude" / "skills"
AGENTS = REPO / ".claude" / "agents"

_RESYNC = "uv run python scripts/sync_agent_skills.py"
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_HOME_PATH = re.compile(r"/Users/|/home/|[A-Za-z]:\\+Users\\")
_KEY = "sk-or-"
_LINE_CITATION = re.compile(r"[\w./-]+\.(?:py|md|toml|json):\d+")


def _sync_script() -> Any:
    path = REPO / "scripts" / "sync_agent_skills.py"
    spec = importlib.util.spec_from_file_location("_sync_agent_skills", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skill_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def skill_problems(skill_dir: Path) -> list[str]:
    """What is wrong with one skill directory's ``SKILL.md``; empty when nothing is."""

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"{skill_dir.name}: no SKILL.md"]
    meta, _body, error = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    if meta is None:
        return [f"{skill_dir.name}: frontmatter does not parse: {error}"]
    problems = []
    name = meta.get("name")
    if name != skill_dir.name:
        problems.append(f"{skill_dir.name}: frontmatter name {name!r} is not the directory name")
    if not isinstance(name, str) or not _NAME.match(name) or len(name) > 64:
        problems.append(f"{skill_dir.name}: name {name!r} is not ^[a-z0-9]+(-[a-z0-9]+)*$ within 64 characters")
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append(f"{skill_dir.name}: no description")
    elif len(description) > 1024:
        problems.append(f"{skill_dir.name}: description is {len(description)} characters, over 1024")
    return problems


def agent_problems(agent_md: Path) -> list[str]:
    """What is wrong with one subagent file's frontmatter; empty when nothing is."""

    meta, _body, error = parse_frontmatter(agent_md.read_text(encoding="utf-8"))
    if not meta:
        return [f"{agent_md.name}: no frontmatter ({error})"]
    problems = []
    name = meta.get("name")
    if name != agent_md.stem:
        problems.append(f"{agent_md.name}: frontmatter name {name!r} is not the file name")
    if not isinstance(name, str) or not _NAME.match(name) or len(name) > 64:
        problems.append(f"{agent_md.name}: name {name!r} is not ^[a-z0-9]+(-[a-z0-9]+)*$ within 64 characters")
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append(f"{agent_md.name}: no description")
    return problems


def loader_problems(root: Path) -> list[str]:
    """aelix's own loader over *root*: every diagnostic, and every skill it did not load."""

    result = load_skills(root, agents=True)
    problems = [f"{d.code}: {d.message} ({Path(d.path).name})" for d in result.diagnostics]
    loaded = {skill.name for skill in result.skills}
    problems += [f"{d.name}: not loaded" for d in _skill_dirs(root) if d.name not in loaded]
    return problems


def text_problems(path: Path, shown_as: str) -> list[str]:
    """A home path, a key or a path-and-line citation in one file."""

    text = path.read_text(encoding="utf-8", errors="replace")
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        if _HOME_PATH.search(line):
            problems.append(f"{shown_as} line {number}: a home-directory path")
        if _KEY in line:
            problems.append(f"{shown_as} line {number}: an OpenRouter key prefix")
        if _LINE_CITATION.search(line):
            problems.append(f"{shown_as} line {number}: a path-and-line citation nothing gates")
    return problems


def _committable_files() -> list[Path]:
    """Every file under the skill and agent trees that is tracked or could be added (not ignored)."""

    proc = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            ".agents",
            ".claude/skills",
            ".claude/agents",
            ".claude/settings.json",
        ],
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip("not a git checkout")
    names = sorted({n for n in proc.stdout.decode("utf-8", "replace").split("\0") if n})
    return [REPO / n for n in names if (REPO / n).is_file()]


# --- the real trees ---------------------------------------------------------


def test_the_trees_are_not_empty() -> None:
    """Guards the guards: with no skill and no agent every check below passes over nothing."""

    assert _skill_dirs(CANONICAL), f"no skills under {CANONICAL.relative_to(REPO)}"
    assert sorted(AGENTS.glob("*.md")) if AGENTS.is_dir() else [], (
        f"no agents under {AGENTS.relative_to(REPO)}"
    )


def test_the_mirror_is_byte_identical_in_both_directions() -> None:
    found = _sync_script().drift(CANONICAL, MIRROR)
    assert not found, (
        f".claude/skills/ has drifted from .agents/skills/:\n{found.report()}\n"
        f"Claude Code reads the mirror, so it would run a different skill than aelix. Fix: {_RESYNC}"
    )


def test_every_skill_has_a_skill_md_with_valid_frontmatter() -> None:
    problems = [p for d in _skill_dirs(CANONICAL) for p in skill_problems(d)]
    assert problems == [], "\n".join(problems)


def test_aelix_loads_every_skill_with_no_diagnostics() -> None:
    problems = loader_problems(CANONICAL)
    assert problems == [], "\n".join(problems)


def test_every_agent_has_valid_frontmatter() -> None:
    agents = sorted(AGENTS.glob("*.md")) if AGENTS.is_dir() else []
    problems = [p for a in agents for p in agent_problems(a)]
    assert problems == [], "\n".join(problems)


def test_no_file_in_the_trees_holds_a_home_path_a_key_or_a_line_citation() -> None:
    problems = [
        p for path in _committable_files() for p in text_problems(path, path.relative_to(REPO).as_posix())
    ]
    assert problems == [], "\n".join(problems)


def test_git_may_track_the_skill_trees_and_still_ignores_the_rest_of_claude() -> None:
    """The ``.gitignore`` negations: without them ``git add`` refuses the mirror and the agents.

    ``.claude/`` is otherwise developer state (worktrees, personal settings) and
    must stay ignored (#143).
    """

    probes = {
        ".agents/skills/x/SKILL.md": False,
        ".claude/skills/x/SKILL.md": False,
        ".claude/agents/x.md": False,
        ".claude/settings.json": False,
        ".claude/settings.local.json": True,
        ".claude/worktrees/x/pyproject.toml": True,
    }
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=REPO,
        input="\0".join(probes).encode() + b"\0",
        capture_output=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        pytest.skip(f"git check-ignore unavailable: {proc.stderr!r}")
    ignored = {n for n in proc.stdout.decode().split("\0") if n}
    assert ignored == {p for p, want in probes.items() if want}


# --- the same checks over planted trees -------------------------------------


def _write(path: Path, text: str) -> None:
    # Bytes, not write_text: on Windows text mode turns "\n" into "\r\n", and the
    # sync tests compare the mirror's bytes with the LF literal they wrote (#733's
    # first CI run failed 3 tests on every windows-latest leg that way).
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _skill_md(name: str, description: str = "Does one thing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


def test_drift_sees_a_missing_a_changed_and_an_extra_file(tmp_path: Path) -> None:
    canonical, mirror = tmp_path / "canonical", tmp_path / "mirror"
    _write(canonical / "one" / "SKILL.md", _skill_md("one"))
    _write(canonical / "one" / "references" / "notes.md", "notes\n")
    _write(mirror / "one" / "SKILL.md", _skill_md("one", "Does one THING."))  # same length, other bytes
    _write(mirror / "gone" / "SKILL.md", _skill_md("gone"))
    _write(mirror / "one" / ".DS_Store", "finder\n")  # debris is not drift

    found = _sync_script().drift(canonical, mirror)
    assert found.missing == ["one/references/notes.md"]
    assert found.differs == ["one/SKILL.md"]
    assert found.extra == ["gone/SKILL.md"]


def test_sync_writes_the_mirror_removes_stale_files_and_check_agrees(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    module = _sync_script()
    canonical, mirror = tmp_path / ".agents" / "skills", tmp_path / ".claude" / "skills"
    monkeypatch.setattr(module, "CANONICAL", canonical)
    monkeypatch.setattr(module, "MIRROR", mirror)
    _write(canonical / "one" / "SKILL.md", _skill_md("one"))
    _write(canonical / "one" / "scripts" / "run.sh", "echo hi\n")
    _write(mirror / "renamed" / "SKILL.md", _skill_md("renamed"))

    assert module.main(["--check"]) == 1
    err = capsys.readouterr().err
    assert "missing from the mirror: one/SKILL.md" in err and "in the mirror only: renamed/SKILL.md" in err
    assert _RESYNC in err

    assert module.main([]) == 0
    assert (mirror / "one" / "scripts" / "run.sh").read_bytes() == b"echo hi\n"
    assert not (mirror / "renamed").exists(), "a stale skill directory must go with its files"
    assert not module.drift(canonical, mirror)
    assert module.main(["--check"]) == 0

    (mirror / "one" / "SKILL.md").write_text(_skill_md("one", "Edited in the mirror."), encoding="utf-8")
    assert module.main(["--check"]) == 1
    assert "differs from the canonical copy: one/SKILL.md" in capsys.readouterr().err


@pytest.mark.parametrize("canonical_is_a_file", [True, False])
def test_sync_reshapes_a_path_that_changed_between_file_and_directory(
    tmp_path: Path, monkeypatch: Any, capsys: Any, canonical_is_a_file: bool
) -> None:
    """Codex 3: sync used to copy before deleting, and stopped on ``IsADirectoryError`` or ``FileExistsError``."""

    module = _sync_script()
    canonical, mirror = tmp_path / ".agents" / "skills", tmp_path / ".claude" / "skills"
    monkeypatch.setattr(module, "CANONICAL", canonical)
    monkeypatch.setattr(module, "MIRROR", mirror)
    _write(canonical / "one" / "SKILL.md", _skill_md("one"))
    _write(mirror / "one" / "SKILL.md", _skill_md("one"))
    topic_file, topic_dir = "one/references/topic", "one/references/topic/old.md"
    new_side, old_side = (topic_file, topic_dir) if canonical_is_a_file else (topic_dir, topic_file)
    _write(canonical / new_side, "new shape\n")
    _write(mirror / old_side, "old shape\n")
    if canonical_is_a_file:
        _write(mirror / "one" / "references" / "topic" / ".DS_Store", "debris\n")

    assert module.main(["--check"]) == 1
    capsys.readouterr()
    assert module.main([]) == 0, capsys.readouterr().err
    assert (mirror / new_side).read_bytes() == b"new shape\n"
    assert not module.drift(canonical, mirror)
    assert module.main(["--check"]) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlink_in_a_tree_is_refused(tmp_path: Path) -> None:
    module = _sync_script()
    canonical = tmp_path / "canonical"
    _write(canonical / "one" / "SKILL.md", _skill_md("one"))
    (canonical / "one" / "link.md").symlink_to(canonical / "one" / "SKILL.md")
    with pytest.raises(module.SkillTreeError):
        module.drift(canonical, tmp_path / "mirror")


@pytest.mark.parametrize(
    ("directory", "content", "expected"),
    [
        ("good-skill", _skill_md("good-skill"), []),
        ("one", _skill_md("other"), ["is not the directory name"]),
        ("Bad_Name", _skill_md("Bad_Name"), ["is not ^[a-z0-9]"]),
        ("a--b", _skill_md("a--b"), ["is not ^[a-z0-9]"]),
        ("-lead", "---\nname: '-lead'\ndescription: x\n---\n", ["is not ^[a-z0-9]"]),
        ("x" * 65, _skill_md("x" * 65), ["within 64 characters"]),
        ("quiet", "---\nname: quiet\n---\n", ["no description"]),
        ("wordy", _skill_md("wordy", "w" * 1025), ["over 1024"]),
        ("bare", "# no frontmatter\n", ["is not the directory name", "no description"]),
    ],
)
def test_the_frontmatter_check_catches_each_rule(
    tmp_path: Path, directory: str, content: str, expected: list[str]
) -> None:
    _write(tmp_path / directory / "SKILL.md", content)
    problems = skill_problems(tmp_path / directory)
    assert len(problems) >= len(expected)
    for fragment in expected:
        assert any(fragment in p for p in problems), problems
    if not expected:
        assert problems == []


def test_the_loader_check_passes_a_good_skill_and_names_a_broken_one(tmp_path: Path) -> None:
    _write(tmp_path / "good" / "skills" / "fine" / "SKILL.md", _skill_md("fine"))
    assert loader_problems(tmp_path / "good" / "skills") == []

    _write(tmp_path / "bad" / "skills" / "fine" / "SKILL.md", _skill_md("fine"))
    _write(tmp_path / "bad" / "skills" / "broken" / "SKILL.md", "---\nname: broken\n---\n")
    _write(tmp_path / "bad" / "skills" / "elsewhere" / "SKILL.md", _skill_md("not-elsewhere"))
    problems = loader_problems(tmp_path / "bad" / "skills")
    assert any("broken: not loaded" in p for p in problems), problems
    assert any(p.startswith("invalid_metadata") for p in problems), problems
    assert not any("fine" in p for p in problems), problems


def test_the_agent_check_catches_a_bad_name_and_a_missing_description(tmp_path: Path) -> None:
    good = tmp_path / "aelix-checker.md"
    _write(good, "---\nname: aelix-checker\ndescription: Checks.\n---\nBody.\n")
    assert agent_problems(good) == []
    renamed = tmp_path / "aelix-other.md"
    _write(renamed, "---\nname: aelix-checker\ndescription: Checks.\n---\n")
    assert any("not the file name" in p for p in agent_problems(renamed))
    silent = tmp_path / "silent.md"
    _write(silent, "---\nname: silent\n---\n")
    assert any("no description" in p for p in agent_problems(silent))
    bare = tmp_path / "bare.md"
    _write(bare, "no frontmatter\n")
    assert agent_problems(bare)


@pytest.mark.parametrize(
    ("line", "what"),
    [
        ("see /Users/someone/dev/repo", "home-directory"),
        ("see /home/someone/repo", "home-directory"),
        ("see C:\\Users\\someone\\repo", "home-directory"),
        ("OPENROUTER_API_KEY=sk-or-v1-0000", "OpenRouter key"),
        # Built at run time: written out, the citation gate would read them as real citations.
        ("as harness/core" + ".py:" + "120 shows", "citation"),
        ("the table in README" + ".md:" + "5", "citation"),
    ],
)
def test_the_text_check_catches_each_pattern(tmp_path: Path, line: str, what: str) -> None:
    path = tmp_path / "SKILL.md"
    _write(path, f"fine line\n{line}\n")
    problems = text_problems(path, "SKILL.md")
    assert len(problems) == 1 and what in problems[0] and "line 2" in problems[0], problems


def test_the_text_check_passes_ordinary_prose(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    _write(
        path,
        "Run `uv run python scripts/preflight.py --plan` from the repository root.\n"
        "Use a scratch HOME under your scratch directory, never `~/.aelix`.\n"
        "See `harness/core.py` (`AgentHarness.prompt`), and ADR-0303.\n",
    )
    assert text_problems(path, "SKILL.md") == []


# --- the skills' own scripts (#733 fix round 1, F10) ------------------------------

_SKILL_SCRIPTS = sorted(CANONICAL.glob("*/scripts/*.py"))


def test_the_skill_scripts_pass_pyright_on_windows(tmp_path: Path) -> None:
    """``pyright --pythonplatform Windows`` over every skill script, as CI's Windows type gate would.

    The project's pyright ``exclude`` has ``**/.*``, which drops a file under
    ``.agents/`` even when it is named on the command line (it analysed 1 of 2
    named files, and reported 0 errors for the 6 the excluded one had), so the
    copies are checked from ``tmp_path``, where no project config reaches.
    """

    if importlib.util.find_spec("pyright") is None:
        pytest.skip("pyright is not installed in this environment")
    assert _SKILL_SCRIPTS, "no skill scripts found; the glob is broken"
    copies = []
    for number, script in enumerate(_SKILL_SCRIPTS):
        copy = tmp_path / f"{number}_{script.name}"
        copy.write_bytes(script.read_bytes())
        copies.append(copy.name)
    proc = subprocess.run(
        [sys.executable, "-m", "pyright", "--pythonplatform", "Windows", "--pythonpath", sys.executable, "--outputjson", *copies],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        # Measured in a python:3.12-slim container with no node: the pyright
        # wrapper's nodeenv install failed and stdout held no report. CI's legs
        # run scripts/check_types.py on the same pyright first, which fails there
        # instead of skipping.
        pytest.skip(f"pyright could not run here (exit {proc.returncode}): {proc.stderr.strip()[-300:]}")
    errors = [
        f"{Path(d['file']).name} line {d['range']['start']['line'] + 1}: {d['message'].splitlines()[0]}"
        for d in report["generalDiagnostics"]
        if d["severity"] == "error"
    ]
    assert report["summary"]["filesAnalyzed"] == len(copies), report["summary"]
    assert errors == [], "\n".join(errors)


def _skill_script(name: str) -> Path:
    found = [p for p in _SKILL_SCRIPTS if p.name == name]
    assert len(found) == 1, found
    return found[0]


def test_tui_capture_answers_help_and_refuses_windows_by_name(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_skill_script("tui_capture.py")), "--help"],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if sys.platform == "win32":
        assert proc.returncode != 0 and "needs a POSIX pty" in proc.stderr, proc.stderr
    else:
        assert proc.returncode == 0, proc.stderr
        assert "--scratch" in proc.stdout and "--step" in proc.stdout


def test_mutants_kills_a_mutant_names_a_survivor_and_restores_the_file(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    _write(tree / "mod.py", "LIMIT = 3\n# a comment no test reads\n")
    original = (tree / "mod.py").read_bytes()
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            [
                {"id": "M1", "file": "mod.py", "old": "LIMIT = 3", "new": "LIMIT = 4"},
                {"id": "M2", "file": "mod.py", "old": "a comment", "new": "the comment"},
            ]
        ),
        encoding="utf-8",
    )
    log = tmp_path / "log.json"
    command = [sys.executable, "-c", "import mod; assert mod.LIMIT == 3"]
    proc = subprocess.run(
        [sys.executable, str(_skill_script("mutants.py")), "--spec", str(spec), "--tree", str(tree), "--log", str(log), "--", *command],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr  # one survivor
    assert "killed 1/2; survived: M2" in proc.stdout
    assert [r["verdict"] for r in json.loads(log.read_text(encoding="utf-8"))] == ["killed", "SURVIVED"]
    assert (tree / "mod.py").read_bytes() == original
