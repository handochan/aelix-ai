"""``.aelix`` as a sensitive path component (ADR-0197 §(i), finding OC-5).

Before this entry ``_is_auto_allowable_write`` returned :data:`True` for
``.aelix/agents/evil.md``, ``.aelix/extensions/evil.py``, ``.aelix/mcp.json``
and ``.aelix/settings.json`` — those are EXACTLY the three resources the Project
Trust gate exists to guard (``cli/project_trust.py:112-177``) plus the user's
own configuration. An auto-accepting agent could therefore WRITE the project
identity / project extension that a LATER run then EXECUTES under an ancestor
``trust.json: true`` (``project_trust.py:550-557``; transitivity is documented
at ``:60-61``). ``--no-approve`` cannot touch that: it stops a child LOADING
such a file, never writing one.

This is a HARD PREREQUISITE for §(i)'s bounded widening — without it a measured
``auto-accept-edits`` child is a self-perpetuating escalation path — and a
deliberate, user-visible behaviour change for interactive AUTO_ACCEPT users
editing their own ``.aelix/**``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_coding_agent.builtin.permission import (
    _MUTATING,
    _SENSITIVE_DIR_COMPONENTS,
    PermissionExtension,
    _is_auto_allowable_write,
    _rule_key,
)
from aelix_coding_agent.builtin.permission_mode import (
    PermissionMode,
    PermissionPosture,
)

_CWD = "/proj"


class _RecordingUI:
    """Records ``select`` calls and denies."""

    def __init__(self) -> None:
        self.select_calls = 0

    async def select(
        self, title: str, options: list[str], opts: object = None
    ) -> str | None:
        self.select_calls += 1
        return "No"

    async def input(
        self, title: str, placeholder: str | None = None, opts: object = None
    ) -> str | None:  # pragma: no cover - not reached
        return None


class _FakeCtx:
    def __init__(self, *, has_ui: bool, ui: _RecordingUI, cwd: str = _CWD) -> None:
        self.has_ui = has_ui
        self.ui = ui
        self.cwd = cwd


# ============================================================
# The predicate
# ============================================================


@pytest.mark.parametrize(
    "path",
    [
        ".aelix/agents/x.md",
        ".aelix/extensions/x.py",
        ".aelix/mcp.json",
        ".aelix/settings.json",
        "sub/.aelix/agents/x.md",
        "/proj/.aelix/extensions/x.py",
        # Traversal back INTO the tree still normalises to a .aelix component.
        "src/../.aelix/agents/x.md",
    ],
)
def test_aelix_paths_are_not_auto_allowable(path: str) -> None:
    """Every ``.aelix`` write falls through to the prompt (or the child floor)."""

    assert _is_auto_allowable_write(path, _CWD) is False


@pytest.mark.parametrize(
    "path",
    [
        "src/app.py",
        "/proj/src/app.py",
        "README.md",
        # NOT a ``.aelix`` COMPONENT — only whole path segments are sensitive,
        # so a file merely named after it is ordinary.
        "docs/aelix.md",
        "docs/.aelixrc",
    ],
)
def test_non_aelix_in_cwd_still_auto_allowable(path: str) -> None:
    """The blast-radius guard: the new component must not make AUTO_ACCEPT useless."""

    assert _is_auto_allowable_write(path, _CWD) is True


def test_existing_sensitive_components_unchanged() -> None:
    """The OC-5 entry is ADDITIVE — the WP-0 #4 set survives intact."""

    assert {".ssh", ".gnupg", "cron.d", "cron.daily"} <= _SENSITIVE_DIR_COMPONENTS
    assert ".aelix" in _SENSITIVE_DIR_COMPONENTS


# ============================================================
# Symlinks — the checks must judge the TARGET (P2 review, HIGH #1)
# ============================================================
#
# ``_is_auto_allowable_write`` reasoned on ``normpath`` alone and never called
# ``realpath``, so BOTH of its rules — cwd containment and the sensitive-
# component set — were decided on a NAME rather than on a target. Git stores
# symlinks as mode 120000, so a repository can ship one; measured against the
# real gate at ``auto-accept-edits`` in a repo holding ``docs -> .aelix``::
#
#     .aelix/agents/evil.md   BLOCKED
#     docs/agents/evil.md     >>> ALLOWED <<<   -> <repo>/.aelix/agents/evil.md
#     keys/authorized_keys2   >>> ALLOWED <<<   -> <home>/.ssh/authorized_keys2
#     h/.bashrc_evil          >>> ALLOWED <<<   -> <home>/.bashrc_evil
#
# ADR-0197 §(i) names the ``.aelix`` entry as the HARD PREREQUISITE for bounded
# widening, so a widened child that could reach ``.aelix/agents/*.md`` through a
# symlink authored the parent's NEXT identity and self-perpetuated — reproduced
# end to end with a real spawned child before this fix.


@pytest.fixture
def symlinked_repo(tmp_path: Path) -> Path:
    """A repo whose checked-in symlinks point at sensitive places."""

    repo = tmp_path / "repo"
    (repo / ".aelix" / "agents").mkdir(parents=True)
    (repo / ".aelix" / "extensions").mkdir(parents=True)
    (repo / "src").mkdir()
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)

    (repo / "docs").symlink_to(".aelix")
    (repo / "keys").symlink_to(home / ".ssh")
    (repo / "h").symlink_to(home)
    return repo


@pytest.mark.parametrize(
    "path",
    [
        "docs/agents/evil.md",
        "docs/extensions/evil.py",
        "docs/settings.json",
        "docs/mcp.json",
    ],
)
def test_a_symlink_into_dot_aelix_is_not_auto_allowable(
    symlinked_repo: Path, path: str
) -> None:
    """The self-perpetuation cut. A NAME is not a target."""

    assert _is_auto_allowable_write(path, str(symlinked_repo)) is False


@pytest.mark.parametrize(
    "path",
    ["keys/authorized_keys2", "keys/config", "h/.bashrc_evil", "h/.profile_evil"],
)
def test_a_symlink_out_of_cwd_is_not_auto_allowable(
    symlinked_repo: Path, path: str
) -> None:
    """Containment is the OTHER rule the missing ``realpath`` voided.

    ``_is_auto_allowable_write``'s own docstring promises that writes to
    ``~/.ssh/authorized_keys`` and ``~/.bashrc`` are not silently accepted; a
    symlinked directory made both of them lexically in-tree.
    """

    assert _is_auto_allowable_write(path, str(symlinked_repo)) is False


def test_a_symlinked_cwd_does_not_break_ordinary_writes(symlinked_repo: Path) -> None:
    """Blast radius: BOTH sides are resolved, so a symlinked checkout still works.

    Resolving only the path would make every write inside a symlinked project
    root look out-of-tree, and AUTO_ACCEPT would prompt for everything.
    """

    alias = symlinked_repo.parent / "alias"
    alias.symlink_to(symlinked_repo)
    assert _is_auto_allowable_write("src/ordinary.py", str(alias)) is True


def test_an_innocent_symlink_inside_the_tree_is_still_auto_allowable(
    symlinked_repo: Path,
) -> None:
    """A symlink is not itself suspicious — only where it lands is."""

    (symlinked_repo / "src" / "vendored").symlink_to(symlinked_repo / "src")
    assert (
        _is_auto_allowable_write("src/vendored/ordinary.py", str(symlinked_repo)) is True
    )


def test_a_lexically_sensitive_name_is_refused_even_if_it_resolves_clean(
    symlinked_repo: Path,
) -> None:
    """BOTH spellings must pass — the TOCTOU half.

    The target usually does not exist yet, so ``realpath`` returns the lexical
    path and a child holding ``bash`` could plant the symlink between this check
    and the write. Judging the lexical form as well means a name that is already
    sensitive is refused whatever the filesystem currently says.
    """

    (symlinked_repo / "real").mkdir()
    (symlinked_repo / ".aelix" / "linked").symlink_to(symlinked_repo / "real")
    assert (
        _is_auto_allowable_write(".aelix/linked/notes.md", str(symlinked_repo)) is False
    )


def test_a_broken_symlink_is_refused_rather_than_followed(
    symlinked_repo: Path,
) -> None:
    """No evidence about where a write lands is not a licence to allow it."""

    (symlinked_repo / "dangling").symlink_to(symlinked_repo.parent / "nowhere" / ".ssh")
    assert (
        _is_auto_allowable_write("dangling/authorized_keys", str(symlinked_repo))
        is False
    )


async def test_a_headless_child_cannot_widen_into_dot_aelix_via_a_symlink(
    symlinked_repo: Path,
) -> None:
    """THE END-TO-END CUT, through the real gate a widened child gets.

    posture ``auto-accept-edits`` + no UI + ``headless_default="block"`` is
    exactly what ADR-0197 §(i)'s widening dialog hands a delegated child. Before
    the fix this returned ``None`` (allow) and the child's write landed in
    ``.aelix/agents/``, where the NEXT run discovers it as a project identity.
    """

    ui = _RecordingUI()
    perm = PermissionExtension(
        posture=PermissionPosture(mode=PermissionMode.AUTO_ACCEPT),
        headless_default="block",
    )
    event = ToolCallHookEvent(
        tool_call_id="t1", tool_name="write", args={"path": "docs/agents/pwned.md"}
    )
    result = await perm._on_tool_call(  # type: ignore[arg-type]
        event, _FakeCtx(has_ui=False, ui=ui, cwd=str(symlinked_repo))
    )

    assert result is not None
    assert result.block is True
    assert ui.select_calls == 0


# ============================================================
# The full ladder
# ============================================================


async def test_aelix_write_prompts_under_auto_accept() -> None:
    """Interactive AUTO_ACCEPT + ``.aelix/agents/x.md`` → the 4-option prompt.

    The deliberate, user-visible behaviour change (ADR-0197 §(i) / CHANGELOG):
    branch (f) declines, control reaches the prompt at ``permission.py:385-392``
    instead of returning ``None``.
    """

    ui = _RecordingUI()
    perm = PermissionExtension(
        posture=PermissionPosture(mode=PermissionMode.AUTO_ACCEPT)
    )
    event = ToolCallHookEvent(
        tool_call_id="t1", tool_name="write", args={"path": ".aelix/agents/x.md"}
    )
    result = await perm._on_tool_call(event, _FakeCtx(has_ui=True, ui=ui))  # type: ignore[arg-type]
    assert ui.select_calls == 1
    assert result is not None and result.block is True


async def test_ordinary_write_under_auto_accept_is_still_silent() -> None:
    """The complement — an in-cwd, non-sensitive write still never prompts."""

    ui = _RecordingUI()
    perm = PermissionExtension(
        posture=PermissionPosture(mode=PermissionMode.AUTO_ACCEPT)
    )
    event = ToolCallHookEvent(
        tool_call_id="t1", tool_name="write", args={"path": "src/app.py"}
    )
    assert await perm._on_tool_call(event, _FakeCtx(has_ui=True, ui=ui)) is None  # type: ignore[arg-type]
    assert ui.select_calls == 0


# ============================================================
# Why delegation consent is NOT built out of this ladder
# ============================================================


def test_agent_is_not_in_mutating() -> None:
    """Pins ADR-0197 §(i)'s rejected alternative, with the reason executable.

    Adding ``"agent"`` to ``_MUTATING`` looks like the delegation consent gate
    and is not one: ``_rule_key`` falls through to an ARGS-BLIND
    ``f"tool:{tool_name}"``, so one "Yes, for this session" would approve every
    profile against every task for the rest of the run. The gate therefore lives
    in ``aelix_agents/consent.py``, keyed on what actually varies.
    """

    assert "agent" not in _MUTATING
    assert _rule_key("agent", {"profile": "a", "task": "x"}) == _rule_key(
        "agent", {"profile": "b", "task": "y"}
    )
    assert _rule_key("agent", {"profile": "a"}) == "tool:agent"
