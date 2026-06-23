"""Issue #12 — skill-directory resolution + runtime wiring (entry.py).

``_resolve_skill_dirs`` composes the directories ``load_skills`` scans:
explicit ``--skill`` paths, the global agent skills dir, and the project-local
``<cwd>/.aelix/skills`` (only when the project is trusted). End-to-end loading
+ ``harness.set_skills`` is exercised by the harness/skills loader tests; here
we pin the dir-composition policy that consumes the CLI flags.
"""

from __future__ import annotations

from pathlib import Path

from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.config import get_agent_dir
from aelix_coding_agent.cli.entry import _resolve_skill_dirs


def _global_dir() -> str:
    return str(Path(get_agent_dir()) / "skills")


def test_defaults_include_global_and_trusted_project_dir() -> None:
    dirs = [str(d) for d in _resolve_skill_dirs(Args(), "/work/proj", True)]
    assert _global_dir() in dirs
    assert str(Path("/work/proj") / ".aelix" / "skills") in dirs


def test_untrusted_project_drops_project_local_dir() -> None:
    dirs = [str(d) for d in _resolve_skill_dirs(Args(), "/work/proj", False)]
    assert _global_dir() in dirs
    # Project-local skills are a prompt-injection vector → gated behind trust.
    assert str(Path("/work/proj") / ".aelix" / "skills") not in dirs


def test_no_skills_drops_all_defaults_keeps_explicit() -> None:
    args = Args(skills=["/abs/custom-skill"], no_skills=True)
    dirs = [str(d) for d in _resolve_skill_dirs(args, "/work/proj", True)]
    assert dirs == ["/abs/custom-skill"]


def test_explicit_relative_skill_resolved_against_cwd() -> None:
    args = Args(skills=["local/skills"])
    dirs = [str(d) for d in _resolve_skill_dirs(args, "/work/proj", True)]
    assert str(Path("/work/proj") / "local" / "skills") in dirs


def test_explicit_skill_md_path_scans_its_parent() -> None:
    args = Args(skills=["/abs/my-skill/SKILL.md"], no_skills=True)
    dirs = [str(d) for d in _resolve_skill_dirs(args, "/work/proj", True)]
    assert dirs == ["/abs/my-skill"]
