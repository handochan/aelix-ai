"""``aelix_agents.trust.child_trust_argv`` — the two-clause rule (ADR-0197 §(g)).

Near-pure: no process, no asyncio. The only I/O is ``Path.resolve()`` and the
already-shipped ``has_trust_requiring_project_resources`` predicate, both driven
against ``tmp_path``.

Finding OC-4 settled this as CONDITIONAL rather than always-on. Both halves are
pinned here:

* clause 1 is the regression guard for the ``inherit_skills: true`` DEFAULT
  (``agents/profile.py:187``) — an always-on ``--no-approve`` would silently
  stop a skills-only repo's skills from loading, for zero security gain, and
  the child's Notice goes to stderr where a successful run never surfaces it;
* clause 2 is the escalation the flag exists for — a MODEL-CHOSEN cwd
  inheriting a monorepo root's persisted ``trust.json`` through the
  nearest-ancestor walk (``project_trust.py:703-710``, transitivity documented
  at ``:60-61``) and executing a vendored ``.aelix/extensions/*.py`` the parent
  itself never loaded.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aelix_agents.trust import child_trust_argv

_NO_APPROVE = ["--no-approve"]


def _aelix(root: Path) -> Path:
    path = root / ".aelix"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- clause 1: same cwd, nothing to gate → emit nothing ---------------------


def test_same_cwd_no_trust_resources_emits_nothing(tmp_path: Path) -> None:
    """THE CLAUSE-1 PIN — an empty directory has no authority to withhold.

    Step 2 of ``resolve_project_trusted`` (``project_trust.py:678-680``) would
    have returned ``True`` for the PARENT in this directory too, so denying the
    child buys nothing.

    This docstring used to carry a second argument — that forcing ``False``
    "would only strip ``.aelix/skills/``, which the trust gate never guarded".
    #115 retired it: skills now reach the model, the predicate counts them, and
    the case it described has moved to
    ``test_same_cwd_with_skills_only_now_emits_no_approve`` under clause 2.
    What is pinned here is only the genuinely empty case.
    """

    assert child_trust_argv(tmp_path, tmp_path) == []


def test_same_cwd_with_an_empty_aelix_dir_emits_nothing(tmp_path: Path) -> None:
    """An empty ``.aelix/`` loads nothing, so it gates nothing."""

    _aelix(tmp_path)
    assert child_trust_argv(tmp_path, tmp_path) == []


def test_same_cwd_with_an_empty_extensions_dir_emits_nothing(tmp_path: Path) -> None:
    """The predicate requires at least one ENTRY, not just the directory."""

    (_aelix(tmp_path) / "extensions").mkdir()
    assert child_trust_argv(tmp_path, tmp_path) == []


# --- clause 2: anything the gate would guard → --no-approve ----------------


def test_same_cwd_with_skills_only_now_emits_no_approve(tmp_path: Path) -> None:
    """#115 MOVED THIS TEST FROM CLAUSE 1, and the move IS the regression.

    It used to assert ``== []`` on the reasoning that
    ``has_trust_requiring_project_resources`` "deliberately" skipped
    ``skills/``, so withholding trust from a same-cwd child cost the
    ``inherit_skills: true`` default for zero security gain.

    That premise died with #115. A skill's name, description and path now go
    into the system prompt (``cli/skills_prompt``), so a ``skills/`` directory
    that arrived with a ``git clone`` writes instructions into the agent — the
    same class of thing as the ``agents/`` profiles two tests below, which have
    always been denied. The gain is no longer zero, so the exemption no longer
    applies and the child is denied like every other gated resource.

    Note the assertion is on ``child_trust_argv`` but the CHANGE was in
    ``project_trust.has_trust_requiring_project_resources``: this module reads
    that predicate rather than re-spelling the resource list, which is why one
    edit moved both.
    """

    skill = _aelix(tmp_path) / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# review\n", encoding="utf-8")
    assert child_trust_argv(tmp_path, tmp_path) == _NO_APPROVE


def test_same_cwd_with_prompt_templates_only_emits_no_approve(
    tmp_path: Path,
) -> None:
    """#115's sibling family: a template body becomes a USER TURN verbatim.

    ``/<name>`` sends the file's contents as the turn text, so a cloned
    ``.aelix/prompt-templates/`` is an injection surface on exactly the same
    footing as ``skills/``, and is gated by the same predicate clause.
    """

    templates = _aelix(tmp_path) / "prompt-templates"
    templates.mkdir()
    (templates / "review.md").write_text("review this\n", encoding="utf-8")
    assert child_trust_argv(tmp_path, tmp_path) == _NO_APPROVE


def test_same_cwd_with_project_extensions_emits_no_approve(tmp_path: Path) -> None:
    """Project extensions are CODE — the gate is live, so the child is denied."""

    extensions = _aelix(tmp_path) / "extensions"
    extensions.mkdir()
    (extensions / "x.py").write_text("", encoding="utf-8")
    assert child_trust_argv(tmp_path, tmp_path) == _NO_APPROVE


def test_same_cwd_with_project_agents_emits_no_approve(tmp_path: Path) -> None:
    """A project agent profile declares a whole-session IDENTITY (ADR-0196)."""

    agents = _aelix(tmp_path) / "agents"
    agents.mkdir()
    (agents / "x.md").write_text("---\nname: x\n---\nbody\n", encoding="utf-8")
    assert child_trust_argv(tmp_path, tmp_path) == _NO_APPROVE


def test_same_cwd_with_project_mcp_emits_no_approve(tmp_path: Path) -> None:
    """A project ``mcp.json`` launches arbitrary server subprocesses."""

    (_aelix(tmp_path) / "mcp.json").write_text("{}", encoding="utf-8")
    assert child_trust_argv(tmp_path, tmp_path) == _NO_APPROVE


def test_different_cwd_always_emits_no_approve(tmp_path: Path) -> None:
    """THE CLAUSE-2 PIN — the model-chosen-cwd escalation.

    A DIFFERENT cwd is always model-chosen. Even a bare subdirectory with no
    ``.aelix/`` of its own gets the flag, because the danger is not what is in
    the child's own directory — it is the nearest-ANCESTOR ``trust.json`` walk
    that would silently vouch for whatever a vendored subtree contains.
    """

    child = tmp_path / "vendor" / "sdk"
    child.mkdir(parents=True)
    assert child_trust_argv(child, tmp_path) == _NO_APPROVE


def test_the_monorepo_vendor_shape_is_denied(tmp_path: Path) -> None:
    """The exact measured escalation, reconstructed.

    The root has NO ``.aelix/`` at all, so nothing has ever executed there and
    the parent never loaded the vendored extension (the loader scans
    ``cwd/.aelix/extensions`` only). The model then picks ``cwd="vendor/sdk"``.
    Without the flag the child's step-4 ancestor walk finds the root's persisted
    ``True`` and ``telemetry.py`` runs.
    """

    vendored = tmp_path / "vendor" / "sdk"
    extensions = _aelix(vendored) / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "telemetry.py").write_text("", encoding="utf-8")
    assert child_trust_argv(vendored, tmp_path) == _NO_APPROVE
    # ... and the parent's own directory is still exempt, so this is a
    # narrowing of the child only, not a blanket regression.
    assert child_trust_argv(tmp_path, tmp_path) == []


def test_parent_subdirectory_of_child_is_still_different(tmp_path: Path) -> None:
    """Containment in either direction is irrelevant — only EQUALITY exempts."""

    child = tmp_path / "sub"
    child.mkdir()
    assert child_trust_argv(tmp_path, child) == _NO_APPROVE


# --- path identity ----------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_symlinked_same_cwd_is_treated_as_same(tmp_path: Path) -> None:
    """Both sides ``.resolve()``, so a symlinked path is the SAME directory.

    Without this, a perfectly ordinary setup (``/tmp`` symlinked, a worktree
    reached through a link) would silently take clause 2 and lose its skills.
    """

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    assert child_trust_argv(link, real) == []
    assert child_trust_argv(real, link) == []


def test_relative_and_absolute_forms_of_the_same_dir_agree(tmp_path: Path) -> None:
    """``resolve()`` also normalises ``..`` traversal."""

    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert child_trust_argv(sub, tmp_path / "a" / "b" / ".." / "b") == []


# --- fail-closed ------------------------------------------------------------


def test_nonexistent_child_cwd_emits_no_approve(tmp_path: Path) -> None:
    """A path that does not exist is not evidence of anything."""

    assert child_trust_argv(tmp_path / "gone", tmp_path) == _NO_APPROVE


def test_a_raising_predicate_fails_closed(tmp_path: Path, monkeypatch) -> None:
    """FAIL-CLOSED. Clause 1 is an EXEMPTION from a security default.

    It may only be granted on positive evidence, and an exception — a corrupt
    trust store, an unreadable directory, a ``resolve()`` that blows up — is the
    ABSENCE of evidence. Asserted through the real import site so a refactor
    that stops calling the predicate cannot pass.
    """

    import aelix_agents.trust as trust_module

    def _boom(_cwd):
        raise OSError("corrupt store")

    monkeypatch.setattr(
        trust_module, "has_trust_requiring_project_resources", _boom
    )
    assert child_trust_argv(tmp_path, tmp_path) == _NO_APPROVE


def test_a_raising_resolve_fails_closed(tmp_path: Path, monkeypatch) -> None:
    """Same rule for the path comparison itself."""

    def _boom(self, *_args, **_kwargs):
        raise OSError("ELOOP")

    monkeypatch.setattr(Path, "resolve", _boom)
    assert child_trust_argv(tmp_path, tmp_path) == _NO_APPROVE


def test_return_value_is_a_fresh_list(tmp_path: Path) -> None:
    """The result is spliced into argv, so a shared list would be a live bug."""

    first = child_trust_argv(tmp_path / "elsewhere", tmp_path)
    first.append("--mutated")
    assert child_trust_argv(tmp_path / "elsewhere", tmp_path) == _NO_APPROVE
