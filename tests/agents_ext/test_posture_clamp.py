"""The child permission CLAMP — ``aelix_agents.posture`` (ADR-0197 §(e)).

Pure tests: no process, no UI, no filesystem. The clamp is a total function
over a five-value lattice, so it is pinned exhaustively — all 80 cells of
5 parent postures x 4 ``approval_mode`` values x 2 scopes x 2 ``has_ui`` values
are asserted literally in :func:`test_full_matrix_snapshot`. Any future edit to
the clamp has to state its intent by editing that table.

ONE EXCEPTION TO "PURE", and it is deliberate. :func:`grants_write_authority`
is a CLAIM ABOUT ANOTHER MODULE — "a child launched at this posture can mutate
something without asking" — and ADR-0197 §(i) keys the consent dialog on it, so
a wrong answer there silently stops showing a human a spawn that can write.
:func:`test_write_authority_matches_the_real_permission_ladder` therefore drives
the REAL :class:`PermissionExtension`, configured exactly as ``cli/entry.py``
configures a delegated child, instead of restating the ladder in a table.

The security findings each named test closes are cited in its docstring; they
are the reason these are not merely round-trip tests.
"""

from __future__ import annotations

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult
from aelix_agents.posture import (
    child_permission_mode,
    declares_write_authority,
    grants_write_authority,
    posture_rank,
)
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture

# Every posture, tightest first. Written out rather than derived from
# ``CYCLE_ORDER`` (``builtin/permission_mode.py:71-77``) on purpose: the cycle
# is a shift+tab UX rotation and carries NO authority ordering — it would rank
# PLAN above AUTO_ACCEPT.
_PARENTS = [
    PermissionMode.PLAN,
    PermissionMode.DEFAULT,
    PermissionMode.AUTO_ACCEPT,
    PermissionMode.AUTO,
    PermissionMode.YOLO,
]

_APPROVAL_MODES = ["inherit", "ask", "auto", "deny"]

_SCOPES = ["user", "project"]


# --- the literal matrix ----------------------------------------------------
# (approval_mode, parent.value, scope, has_ui) -> expected mode value.
_MATRIX: dict[tuple[str, str, str, bool], str] = {
    # ``deny`` — always plan, for every parent, scope and UI state. The
    # profile author asked for a read-only child and gets one.
    ("deny", "default", "user", False): "plan",
    ("deny", "default", "user", True): "plan",
    ("deny", "default", "project", False): "plan",
    ("deny", "default", "project", True): "plan",
    ("deny", "plan", "user", False): "plan",
    ("deny", "plan", "user", True): "plan",
    ("deny", "plan", "project", False): "plan",
    ("deny", "plan", "project", True): "plan",
    ("deny", "auto-accept-edits", "user", False): "plan",
    ("deny", "auto-accept-edits", "user", True): "plan",
    ("deny", "auto-accept-edits", "project", False): "plan",
    ("deny", "auto-accept-edits", "project", True): "plan",
    ("deny", "auto", "user", False): "plan",
    ("deny", "auto", "user", True): "plan",
    ("deny", "auto", "project", False): "plan",
    ("deny", "auto", "project", True): "plan",
    ("deny", "yolo", "user", False): "plan",
    ("deny", "yolo", "user", True): "plan",
    ("deny", "yolo", "project", False): "plan",
    ("deny", "yolo", "project", True): "plan",
    # ``inherit`` — the baseline. DEFAULT collapses to PLAN (a child has no
    # prompt channel, so "prompt" is a denial with no reason attached), AUTO
    # collapses to AUTO_ACCEPT (AUTO's extra authority is bash classification),
    # YOLO passes through (the user already chose it for this whole session).
    ("inherit", "default", "user", False): "plan",
    ("inherit", "default", "user", True): "plan",
    ("inherit", "default", "project", False): "plan",
    ("inherit", "default", "project", True): "plan",
    ("inherit", "plan", "user", False): "plan",
    ("inherit", "plan", "user", True): "plan",
    ("inherit", "plan", "project", False): "plan",
    ("inherit", "plan", "project", True): "plan",
    ("inherit", "auto-accept-edits", "user", False): "auto-accept-edits",
    ("inherit", "auto-accept-edits", "user", True): "auto-accept-edits",
    ("inherit", "auto-accept-edits", "project", False): "auto-accept-edits",
    ("inherit", "auto-accept-edits", "project", True): "auto-accept-edits",
    ("inherit", "auto", "user", False): "auto-accept-edits",
    ("inherit", "auto", "user", True): "auto-accept-edits",
    ("inherit", "auto", "project", False): "auto-accept-edits",
    ("inherit", "auto", "project", True): "auto-accept-edits",
    ("inherit", "yolo", "user", False): "yolo",
    ("inherit", "yolo", "user", True): "yolo",
    ("inherit", "yolo", "project", False): "yolo",
    ("inherit", "yolo", "project", True): "yolo",
    # ``ask`` — the ONLY row ``has_ui`` changes (finding OC-10). With a UI it
    # is the inherit baseline, which the consent dialog then offers to raise;
    # without one there is nobody to ask, so it collapses to PLAN exactly like
    # ``deny``. It never refuses the spawn.
    ("ask", "default", "user", False): "plan",
    ("ask", "default", "user", True): "plan",
    ("ask", "default", "project", False): "plan",
    ("ask", "default", "project", True): "plan",
    ("ask", "plan", "user", False): "plan",
    ("ask", "plan", "user", True): "plan",
    ("ask", "plan", "project", False): "plan",
    ("ask", "plan", "project", True): "plan",
    ("ask", "auto-accept-edits", "user", False): "plan",
    ("ask", "auto-accept-edits", "user", True): "auto-accept-edits",
    ("ask", "auto-accept-edits", "project", False): "plan",
    ("ask", "auto-accept-edits", "project", True): "auto-accept-edits",
    ("ask", "auto", "user", False): "plan",
    ("ask", "auto", "user", True): "auto-accept-edits",
    ("ask", "auto", "project", False): "plan",
    ("ask", "auto", "project", True): "auto-accept-edits",
    ("ask", "yolo", "user", False): "plan",
    ("ask", "yolo", "user", True): "yolo",
    ("ask", "yolo", "project", False): "plan",
    ("ask", "yolo", "project", True): "yolo",
    # ``auto`` — THE B4 ROW. A DEFAULT parent yields ``plan``, NOT
    # ``auto-accept-edits``: a profile cannot lift a prompt-for-everything
    # session into unattended repo-wide writes. The ceiling everywhere else is
    # AUTO_ACCEPT, including under a YOLO parent — ``auto`` requests
    # AUTO_ACCEPT and the clamp can only lower it.
    ("auto", "default", "user", False): "plan",
    ("auto", "default", "user", True): "plan",
    ("auto", "default", "project", False): "plan",
    ("auto", "default", "project", True): "plan",
    ("auto", "plan", "user", False): "plan",
    ("auto", "plan", "user", True): "plan",
    ("auto", "plan", "project", False): "plan",
    ("auto", "plan", "project", True): "plan",
    ("auto", "auto-accept-edits", "user", False): "auto-accept-edits",
    ("auto", "auto-accept-edits", "user", True): "auto-accept-edits",
    ("auto", "auto-accept-edits", "project", False): "auto-accept-edits",
    ("auto", "auto-accept-edits", "project", True): "auto-accept-edits",
    ("auto", "auto", "user", False): "auto-accept-edits",
    ("auto", "auto", "user", True): "auto-accept-edits",
    ("auto", "auto", "project", False): "auto-accept-edits",
    ("auto", "auto", "project", True): "auto-accept-edits",
    ("auto", "yolo", "user", False): "auto-accept-edits",
    ("auto", "yolo", "user", True): "auto-accept-edits",
    # THE OC-6 CELL. Under the pre-fix ``requested = fallback`` ASSIGNMENT this
    # was "yolo" — the project-scope ban made the checked-in repo file WIDER
    # than the same profile at user scope. The rank-min form keeps it at the
    # user-scope answer.
    ("auto", "yolo", "project", False): "auto-accept-edits",
    ("auto", "yolo", "project", True): "auto-accept-edits",
}


def test_full_matrix_snapshot() -> None:
    """All 80 cells, pinned literally.

    Both directions are asserted: every computed cell matches the table, AND
    the table has no stale rows. A snapshot that only checks one direction
    silently rots when the input space grows.
    """

    computed = {
        (approval, parent.value, scope, has_ui): child_permission_mode(
            approval, parent, scope, has_ui=has_ui
        ).value
        for approval in _APPROVAL_MODES
        for parent in _PARENTS
        for scope in _SCOPES
        for has_ui in (False, True)
    }
    assert len(computed) == 80
    assert computed == _MATRIX


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", _SCOPES)
@pytest.mark.parametrize("has_ui", [False, True])
def test_approval_mode_deny_maps_to_plan(
    parent: PermissionMode, scope: str, has_ui: bool
) -> None:
    """``deny`` is unconditional — no parent, scope or UI state relaxes it."""

    assert child_permission_mode("deny", parent, scope, has_ui=has_ui) is (
        PermissionMode.PLAN
    )


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", _SCOPES)
def test_approval_mode_inherit_never_widens(parent: PermissionMode, scope: str) -> None:
    """The core invariant: the FLOOR is never looser than the parent."""

    child = child_permission_mode("inherit", parent, scope)
    assert posture_rank(child) <= posture_rank(parent)


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", _SCOPES)
@pytest.mark.parametrize("approval", _APPROVAL_MODES)
@pytest.mark.parametrize("has_ui", [False, True])
def test_no_approval_mode_ever_widens(
    parent: PermissionMode, scope: str, approval: str, has_ui: bool
) -> None:
    """The clamp holds for EVERY input, not just ``inherit``.

    Stated as a property over the whole space so a new ``approval_mode`` value
    cannot be added without either satisfying the clamp or failing here.
    """

    child = child_permission_mode(approval, parent, scope, has_ui=has_ui)
    assert posture_rank(child) <= posture_rank(parent)


@pytest.mark.parametrize("parent", _PARENTS)
def test_approval_mode_auto_never_widens(parent: PermissionMode) -> None:
    """THE B4 PIN.

    ``approval_mode: auto`` is the vector: a profile file declaring it must not
    lift a DEFAULT (prompt-for-every-mutating-tool) parent into a child that
    auto-accepts writes. Under a DEFAULT parent the answer is ``plan``, and it
    is never ``auto-accept-edits``.

    The child cannot compensate for a wrong answer here: ``permission.py``'s
    AUTO_ACCEPT in-cwd-write branch returns "allow" roughly thirty lines ABOVE
    the ``not ctx.has_ui`` headless floor, so the floor never runs for exactly
    the writes that matter. The posture the child is LAUNCHED with is the
    guarantee.
    """

    child = child_permission_mode("auto", parent, "user")
    assert posture_rank(child) <= posture_rank(parent)
    if parent is PermissionMode.DEFAULT:
        assert child is PermissionMode.PLAN
    assert posture_rank(child) <= posture_rank(PermissionMode.AUTO_ACCEPT)


@pytest.mark.parametrize("parent", _PARENTS)
def test_project_scope_profile_cannot_widen_posture(parent: PermissionMode) -> None:
    """A checked-in repo profile never buys more authority than ``inherit``."""

    project = child_permission_mode("auto", parent, "project")
    inherited = child_permission_mode("inherit", parent, "user")
    assert posture_rank(project) <= posture_rank(inherited)


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("approval", _APPROVAL_MODES)
@pytest.mark.parametrize("has_ui", [False, True])
def test_project_scope_ban_is_rank_min_not_assignment(
    parent: PermissionMode, approval: str, has_ui: bool
) -> None:
    """FINDING OC-6 — the ban is a rank-MIN, not an assignment.

    A project-scoped profile must never be looser than the SAME profile at user
    scope. The discriminating case is ``approval_mode="auto"`` under a YOLO
    parent: user scope answers ``auto-accept-edits``, and the pre-fix
    ``requested = fallback`` assignment answered ``yolo`` for the project file —
    i.e. the ban made the repo's own file WIDER than the user's.

    Honest note for future readers: in its corrected form this clause changes no
    answer versus deleting it outright (``auto`` requests AUTO_ACCEPT, which is
    already <= every parent loose enough to widen). It is a tested defensive
    invariant, not a load-bearing one, and that is not a reason to delete it —
    it is what stays true if a later edit raises what ``auto`` may request. The
    project-scope guarantee that bites today is §(i)'s absolute
    "never offer widening for a project-scoped profile" dialog rule.
    """

    project = child_permission_mode(approval, parent, "project", has_ui=has_ui)
    user = child_permission_mode(approval, parent, "user", has_ui=has_ui)
    assert posture_rank(project) <= posture_rank(user)


def test_project_scope_auto_under_yolo_parent_is_auto_accept() -> None:
    """The single cell that discriminates rank-min from assignment."""

    assert (
        child_permission_mode("auto", PermissionMode.YOLO, "project")
        is PermissionMode.AUTO_ACCEPT
    )


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", _SCOPES)
@pytest.mark.parametrize("approval", _APPROVAL_MODES)
@pytest.mark.parametrize("has_ui", [False, True])
def test_clamped_default_is_tightened_to_plan(
    parent: PermissionMode, scope: str, approval: str, has_ui: bool
) -> None:
    """DEFAULT is never handed to a child, on any path.

    DEFAULT means "prompt", and a delegated child has no prompt channel. PLAN
    denies the same set of tools but does so with a model-actionable reason, and
    it does not depend on ``PermissionExtension.headless_default`` having been
    flipped correctly by ``cli/entry.py``. Strictly tighter, never looser.
    """

    assert (
        child_permission_mode(approval, parent, scope, has_ui=has_ui)
        is not PermissionMode.DEFAULT
    )


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", _SCOPES)
def test_ask_without_ui_is_plan(parent: PermissionMode, scope: str) -> None:
    """FINDING OC-10 — ``ask`` with nobody to ask collapses to ``plan``.

    It does NOT refuse the spawn. The old "``ask`` => spawn refused" rule
    existed only because there was nowhere to ask; §(i) creates somewhere, in
    the parent, and the no-UI path degrades silently instead of erroring.
    """

    assert child_permission_mode("ask", parent, scope, has_ui=False) is (
        PermissionMode.PLAN
    )


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", _SCOPES)
def test_ask_with_ui_matches_inherit_baseline(
    parent: PermissionMode, scope: str
) -> None:
    """FINDING OC-10 — with a UI, ``ask`` IS the inherit baseline.

    That baseline is what the consent dialog renders as its read-only option
    and then offers to raise; the raise itself lives in ``consent.py``, never
    here.
    """

    assert child_permission_mode(
        "ask", parent, scope, has_ui=True
    ) is child_permission_mode("inherit", parent, scope, has_ui=True)


def test_unknown_approval_mode_falls_back_to_inherit() -> None:
    """An unrecognised value must not crash and must not widen.

    ``AgentProfile.approval_mode`` is validated at parse time, so this is
    defence in depth for a hand-constructed profile or a future value that
    reaches the clamp before the clamp learns about it. The fall-through is the
    ``inherit`` branch, which is already clamped.
    """

    for parent in _PARENTS:
        child = child_permission_mode("something-new", parent, "user")
        assert child is child_permission_mode("inherit", parent, "user")
        assert posture_rank(child) <= posture_rank(parent)


def test_posture_rank_is_a_total_order_over_every_mode() -> None:
    """Every :class:`PermissionMode` member is ranked, with no ties.

    A mode missing from the rank table would raise ``KeyError`` deep inside the
    clamp at spawn time; a duplicate rank would make "never looser" ambiguous.
    """

    ranks = [posture_rank(mode) for mode in PermissionMode]
    assert sorted(ranks) == list(range(len(PermissionMode)))
    assert posture_rank(PermissionMode.PLAN) < posture_rank(PermissionMode.DEFAULT)
    assert posture_rank(PermissionMode.DEFAULT) < posture_rank(
        PermissionMode.AUTO_ACCEPT
    )
    assert posture_rank(PermissionMode.AUTO_ACCEPT) < posture_rank(PermissionMode.AUTO)
    assert posture_rank(PermissionMode.AUTO) < posture_rank(PermissionMode.YOLO)


# --- grants_write_authority, measured against the REAL ladder ----------------


class _ChildCtx:
    """A delegated child's :class:`ExtensionContext` surface, as it really is.

    ``has_ui`` is False because the child runs ``--mode json -p --no-session``
    with no terminal, and a ``select`` that is ever reached would hang a real
    child on a UI that does not exist — so it raises instead of returning.
    """

    def __init__(self, cwd: str) -> None:
        self.has_ui = False
        self.cwd = cwd
        self.ui = self

    async def select(self, title: str, options: list[str], opts: object = None) -> str:
        raise AssertionError(f"a delegated child must never prompt ({title!r})")


def _child_permission_extension(mode: PermissionMode) -> PermissionExtension:
    """Exactly what ``cli/entry.py`` builds when ``subagent_depth() > 0``."""

    return PermissionExtension(
        posture=PermissionPosture(mode=mode), headless_default="block"
    )


@pytest.mark.parametrize("mode", _PARENTS)
async def test_write_authority_matches_the_real_permission_ladder(
    mode: PermissionMode, tmp_path: object
) -> None:
    """THE PREDICATE PIN — ``grants_write_authority`` is not a taste judgement.

    ADR-0197 §(i) fires the consent dialog only when write authority is at
    stake, so the predicate has to mean what ``builtin/permission.py`` actually
    does to a delegated child. This drives the REAL ``_on_tool_call`` with the
    child's real configuration (``headless_default="block"``, no UI) and asserts
    the two agree on every posture, in both directions:

    * ``grants_write_authority`` True  → an ordinary in-cwd write is ALLOWED
      with nobody asked (``result is None``);
    * ``grants_write_authority`` False → that same write is BLOCKED.

    ``default`` is the cell worth naming. It is on the no-authority side not by
    fiat but because a DEFAULT child would have to ASK, and asking is exactly
    what ``headless_default="block"`` refuses — branch (d). If the ladder is
    ever reordered so that stops being true, this fails here rather than
    silently removing a dialog somebody needed.
    """

    cwd = str(tmp_path)
    event = ToolCallHookEvent(
        tool_call_id="t1", tool_name="write", args={"path": "src/app.py"}
    )
    result = await _child_permission_extension(mode)._on_tool_call(
        event, _ChildCtx(cwd)  # type: ignore[arg-type]
    )
    allowed_without_asking = result is None
    assert allowed_without_asking is grants_write_authority(mode), mode
    if not allowed_without_asking:
        assert isinstance(result, ToolCallResult)
        assert result.block is True


async def test_a_no_authority_child_cannot_run_bash_either(tmp_path: object) -> None:
    """The complement, on the other mutating family.

    ``grants_write_authority`` is named for writes because that is the authority
    the dialog offers to grant, but the postures it excludes deny bash as well —
    PLAN at branch (b), DEFAULT at the headless floor. A predicate that let a
    bash-capable child through un-asked would be the same hole under a different
    tool name.
    """

    event = ToolCallHookEvent(
        tool_call_id="t1", tool_name="bash", args={"command": "echo hi"}
    )
    for mode in (PermissionMode.PLAN, PermissionMode.DEFAULT):
        assert grants_write_authority(mode) is False
        result = await _child_permission_extension(mode)._on_tool_call(
            event, _ChildCtx(str(tmp_path))  # type: ignore[arg-type]
        )
        assert isinstance(result, ToolCallResult), mode
        assert result.block is True


def test_write_authority_is_monotone_in_the_rank_ladder() -> None:
    """Once a posture grants authority, every looser one does too.

    Expressed as a property so a new :class:`PermissionMode` cannot be inserted
    into ``_RANK`` and land on the wrong side by accident: the predicate is a
    threshold on the rank order, and a non-monotone answer would mean the
    threshold had been replaced by a set literal somewhere.
    """

    answers = [grants_write_authority(mode) for mode in _PARENTS]
    assert answers == sorted(answers)
    assert grants_write_authority(PermissionMode.PLAN) is False
    assert grants_write_authority(PermissionMode.DEFAULT) is False
    assert grants_write_authority(PermissionMode.AUTO_ACCEPT) is True


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", _SCOPES)
@pytest.mark.parametrize("approval", _APPROVAL_MODES)
@pytest.mark.parametrize("has_ui", [False, True])
def test_the_clamp_never_grants_authority_the_parent_lacks(
    parent: PermissionMode, scope: str, approval: str, has_ui: bool
) -> None:
    """The two modules composed: no clamp output can out-authorise its parent.

    The dialog's early-out reads ``grants_write_authority(clamp)``, so this is
    the statement that keeps it honest — a read-only PARENT can never produce a
    write-capable clamp, and therefore can never produce a spawn that skips the
    dialog *because* it was write-capable.
    """

    child = child_permission_mode(approval, parent, scope, has_ui=has_ui)
    if not grants_write_authority(parent):
        assert grants_write_authority(child) is False


# --- declares_write_authority: the per-value pin -----------------------------
#
# The owner's 2026-07-27 amendment turns on ONE question — does the profile
# itself declare it needs write authority — so each of the four values gets an
# assertion of its own rather than one table row.


def test_declares_write_authority_covers_the_whole_vocabulary() -> None:
    """Every value ``agents/profile.py`` can parse has a decided answer.

    Read out of ``_APPROVAL_MODES`` in the profile module itself, not restated
    here, so a fifth value cannot be added to the on-disk format without this
    test failing and forcing the decision to be made deliberately. That is the
    whole point: an undecided value would silently inherit ``False`` from the
    allow-list, and "declares nothing" is the safe answer only if somebody chose
    it.
    """

    from aelix_coding_agent.agents.profile import _APPROVAL_MODES as SHIPPED

    decided = {"inherit": False, "ask": True, "auto": True, "deny": False}
    assert set(SHIPPED) == set(decided)
    for value, expected in decided.items():
        assert declares_write_authority(value) is expected, value


def test_auto_declares_because_it_literally_asks_for_writes() -> None:
    """``approval_mode: auto`` is the unambiguous case, and the reason B4 exists.

    The profile asks for its writes to be auto-approved. Under a tight parent the
    clamp refuses to grant that silently (finding B4), which is exactly the
    situation the dialog is for: the profile declared the need, and only a human
    can satisfy it.
    """

    assert declares_write_authority("auto") is True
    assert child_permission_mode("auto", PermissionMode.DEFAULT, "user") is (
        PermissionMode.PLAN
    )


def test_ask_declares_or_it_would_be_inert() -> None:
    """``ask`` must stay on the declaring side, and this is the argument.

    ``ask`` means "put this to a human at spawn time", and the only question this
    dialog puts to a human is how much authority to grant. Its clamp under a
    ``default`` parent is ``plan`` — no write authority — so if ``ask`` declared
    nothing, ADR-0197 §(i)'s early-out would suppress the very dialog the value
    exists to open. It would become byte-for-byte indistinguishable from
    ``inherit``, re-opening the "validated, not read" deferral at
    ``agents/profile.py``'s ``approval_mode`` that finding OC-8 closed.
    """

    assert declares_write_authority("ask") is True
    assert child_permission_mode("ask", PermissionMode.DEFAULT, "user", has_ui=True) is (
        PermissionMode.PLAN
    )
    assert grants_write_authority(PermissionMode.PLAN) is False


def test_inherit_declares_nothing() -> None:
    """The owner's headline case: an ordinary delegation asks for nothing extra.

    ``inherit`` requests no MORE authority than the parent already has. When the
    parent IS loose the clamp itself is write-capable and the dialog fires
    through :func:`grants_write_authority` — never through a widening option — so
    refusing to volunteer one loses nothing. Both halves are asserted here
    because the second is what makes the first safe.
    """

    assert declares_write_authority("inherit") is False
    assert grants_write_authority(
        child_permission_mode("inherit", PermissionMode.AUTO_ACCEPT, "user")
    ) is True
    assert grants_write_authority(
        child_permission_mode("inherit", PermissionMode.DEFAULT, "user")
    ) is False


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", _SCOPES)
def test_deny_never_declares_and_never_reaches_authority(
    parent: PermissionMode, scope: str
) -> None:
    """``deny`` IS THE EXPLICIT OPPOSITE, and both layers have to agree.

    A dialog offering to widen a ``deny`` profile would contradict the file it
    was read from. Two independent guarantees, asserted together because either
    one alone would be enough today and neither is enough on its own tomorrow:
    the value declares nothing (so no widening option is ever built), and its
    clamp is ``PLAN`` under every parent and in every scope (so there is no
    authority to inherit either).
    """

    assert declares_write_authority("deny") is False
    child = child_permission_mode("deny", parent, scope, has_ui=True)
    assert child is PermissionMode.PLAN
    assert grants_write_authority(child) is False


@pytest.mark.parametrize(
    "value", ["", "AUTO", "Auto", "yes", "always", "auto-accept-edits", "widen"]
)
def test_an_unrecognised_approval_mode_declares_nothing(value: str) -> None:
    """ALLOW-LIST, not deny-list — the fail-closed direction.

    ``child_permission_mode`` and :func:`declares_write_authority` both take a
    bare ``str``. ``parse_profile`` rejects unknown values today, but a caller
    that skipped it — a future format, a hand-built ``AgentProfile`` in a test,
    a case-mangled value — must not be able to buy a widening option with a
    string the vocabulary never contained.
    """

    assert declares_write_authority(value) is False


def test_explicit_scope_is_not_treated_as_project() -> None:
    """``--agent-file`` (scope ``"explicit"``) is a path the HUMAN typed.

    ``ProfileScope`` (``agents/profile.py:52``) has four values, not two. Only
    the literal ``"project"`` triggers the widening ban — treating ``explicit``
    as project would silently downgrade a user pointing at their own file.
    """

    assert child_permission_mode(
        "auto", PermissionMode.YOLO, "explicit"
    ) is child_permission_mode("auto", PermissionMode.YOLO, "user")
