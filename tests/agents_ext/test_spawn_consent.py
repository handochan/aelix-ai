"""``aelix_agents.consent`` — the spawn-time gate (ADR-0197 §(i)).

Everything here is driven against a fake :class:`ExtensionContext` whose
``has_ui`` is a PROPERTY backed by a mutable flag (so a test can flip it between
calls) and whose ``ui.select`` is an async spy. No process is ever created and
no real TUI is involved — the modal's rendering is explicitly out of scope
(residual R3, covered by a manual live smoke).

The gate exists because the shipped one is EMPTY: ``"agent"`` is not in
``builtin/permission.py``'s ``_MUTATING``, so a model-driven ``agent`` call is
silently allowed at ``permission.py:502-504``. Adding it there is not the fix —
``_rule_key`` falls through to an args-blind ``f"tool:{tool_name}"``, so one
"allow this session" would approve every profile against every task.

THE RULE THESE TESTS PIN (owner decision, 2026-07-27; ADR-0197 §(i)). With a
live UI the dialog fires **only when write authority is at stake** — when the
clamp GRANTS WRITE AUTHORITY, or when a human COULD widen it. A delegation that
is read-only and cannot be widened proceeds with no prompt, because a modal
offering only ["Run read-only (plan)", "Cancel"] has no real answer and teaches
the human to dismiss the widget that also asks the questions that matter.

AND "COULD BE WIDENED" IS A PROPERTY OF THE PROFILE (same decision, second
amendment). The widening option is offered only when the profile DECLARED it
needs write authority — ``approval_mode: auto`` or ``ask``. ``inherit`` and
``deny`` declare nothing, so an ordinary read-only delegation renders no dialog
at all rather than one whose extra option exists purely because a human could
theoretically have taken it. A human can no longer opportunistically upgrade a
non-declaring profile at spawn time; the way to grant a profile writes is to edit
the profile, which is a reviewable, signable artifact.

Several tests below were rewritten for those two changes rather than deleted, and
each says so in its docstring; :func:`_dialog_expected` restates the rule from
first principles so the assertions are not a paraphrase of the implementation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aelix_agents.consent import (
    CANCEL_OPTION,
    DIALOG_ROW_CHARS,
    TASK_PREVIEW_CHARS,
    SpawnGrant,
    build_consent_title,
    build_options,
    contains_control_chars,
    request_spawn_consent,
)
from aelix_agents.posture import (
    child_permission_mode,
    grants_write_authority,
    posture_rank,
)
from aelix_agents.print_channel import resolve_child_cwd
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.subagent_contract import ResolvedProfile

_PARENTS = [
    PermissionMode.PLAN,
    PermissionMode.DEFAULT,
    PermissionMode.AUTO_ACCEPT,
    PermissionMode.AUTO,
    PermissionMode.YOLO,
]


_DECLARING = ("auto", "ask")
"""The ``approval_mode`` values that DECLARE the profile needs write authority.

Spelled out here rather than imported from ``posture.py`` for the same reason
:func:`_dialog_expected` is: these tests must state the policy, not echo the
implementation of it. The per-value justification lives beside
``_DECLARES_WRITE_AUTHORITY``; ``test_posture_clamp.py`` pins the two lists
against ``agents/profile.py``'s own ``_APPROVAL_MODES`` so a fifth value cannot
be added without a decision.
"""

_NON_DECLARING = ("inherit", "deny")


def _dialog_expected(clamp: PermissionMode, scope: str, approval: str) -> bool:
    """Should a spawn at ``clamp`` from a ``scope`` profile open a dialog?

    Written out here rather than imported so these tests state the POLICY
    independently: write authority is at stake (``grants_write_authority``,
    itself pinned against the real ``PermissionExtension`` ladder in
    ``test_posture_clamp.py``), or a human could widen it — which needs the
    profile to have DECLARED it needs write authority, a non-project scope, and a
    clamp strictly below the ``auto-accept-edits`` ceiling. Assumes a live UI;
    with none, nothing ever prompts.

    ``approval`` became a parameter with the 2026-07-27 second amendment. Before
    it, widening was a property of the (scope, clamp) pair alone — which is
    exactly the rule that made a read-only ``inherit`` delegation render a modal.
    """

    may_widen = (
        approval in _DECLARING
        and scope != "project"
        and posture_rank(PermissionMode.AUTO_ACCEPT) > posture_rank(clamp)
    )
    return grants_write_authority(clamp) or may_widen


class _SelectSpy:
    """Async stand-in for ``ExtensionUIContext.select``.

    Records every ``(title, options)`` pair and returns whatever ``answers``
    yields — including ``None``, which is what Esc produces
    (``tui/context.py:429``).
    """

    def __init__(self, *answers: object) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[str, list[str]]] = []
        self.concurrent = False
        self._inside = 0
        self.raises: BaseException | None = None
        self.delay = 0.0

    async def select(self, title: str, options: list[str], opts: object = None) -> object:
        self._inside += 1
        if self._inside > 1:
            self.concurrent = True
        try:
            self.calls.append((title, list(options)))
            if self.delay:
                await asyncio.sleep(self.delay)
            else:
                await asyncio.sleep(0)
            if self.raises is not None:
                raise self.raises
            if not self._answers:
                return None
            return self._answers.pop(0)
        finally:
            self._inside -= 1


class _FakeCtx:
    """Minimal :class:`ExtensionContext` surface: ``has_ui`` + ``ui``.

    ``has_ui`` is a property over ``self.flag`` on purpose. The real one
    (``extensions/api.py:1155`` → ``:1175-1176``) is
    ``runtime.ui is not HEADLESS_UI_CONTEXT`` — a TIME-VARYING value, not a
    mode: ``False`` during ``harness.bootstrap()``, ``True`` after the TUI
    binds, re-pointed on every harness rebuild, and ``False`` again on exit
    (finding OC-7). A fake with a plain attribute could not express that.
    """

    def __init__(self, *, has_ui: bool, ui: _SelectSpy | None = None) -> None:
        self.flag = has_ui
        self.ui = ui if ui is not None else _SelectSpy()

    @property
    def has_ui(self) -> bool:
        return self.flag


def _resolved(
    *,
    scope: str = "user",
    approval_mode: str = "inherit",
    name: str = "scout",
    source_path: str = "/home/alice/.aelix/agents/scout.md",
    model: str | None = None,
) -> ResolvedProfile:
    profile = AgentProfile(
        name=name,
        description="A scouting agent.",
        body="You are a scout.",
        file_path=source_path,
        scope=scope,  # type: ignore[arg-type]
        approval_mode=approval_mode,  # type: ignore[arg-type]
        model=model,
    )
    return ResolvedProfile(
        name=name, profile=profile, source_path=source_path, scope=scope
    )


def _declaring(**kwargs: str | None) -> ResolvedProfile:
    """A profile that DECLARES it needs write authority (``approval_mode: auto``).

    Since the 2026-07-27 second amendment this is the ONLY kind of profile whose
    read-only spawn opens a dialog, so every test below that is about the dialog
    itself — its body, its options, its answers, its serialisation — has to use
    one. ``_resolved()`` keeps ``inherit`` as its default because that is what a
    real profile with no ``approval_mode:`` line parses to, and it is now the
    case that does NOT prompt.
    """

    kwargs.setdefault("approval_mode", "auto")
    return _resolved(**kwargs)  # type: ignore[arg-type]


# --- the headless path ------------------------------------------------------


@pytest.mark.parametrize("parent", _PARENTS)
async def test_no_ui_returns_clamp_without_prompting(parent: PermissionMode) -> None:
    """Headless is a silent DOWNGRADE, never a refusal.

    There is nobody to ask, so the clamp stands, widening never happens, and no
    new failure path appears. This is what keeps ``-p`` / ``--mode json`` / RPC
    delegation working exactly as §(e) describes.
    """

    ctx = _FakeCtx(has_ui=False)
    resolved = _resolved()
    grant = await request_spawn_consent(ctx, resolved, "do a thing", parent, cwd="/w")

    assert ctx.ui.calls == []
    assert grant.consented is True
    assert grant.widened is False
    assert grant.mode is child_permission_mode("inherit", parent, "user", has_ui=False)


async def test_headless_ask_downgrades_to_plan_and_never_refuses() -> None:
    """FINDING OC-10 — ``ask`` with no UI clamps to ``plan`` and still spawns."""

    ctx = _FakeCtx(has_ui=False)
    grant = await request_spawn_consent(
        ctx, _resolved(approval_mode="ask"), "t", PermissionMode.YOLO, cwd="/w"
    )
    assert grant.consented is True
    assert grant.mode is PermissionMode.PLAN
    assert ctx.ui.calls == []


async def test_has_ui_is_read_live_not_cached() -> None:
    """THE OC-7 PIN — ``ctx.has_ui`` must be read at PROMPT time, every time.

    The same context object answers differently before and after the TUI binds
    (and again after it exits). A value captured at construction — or memoised
    across calls — would either prompt into a dead UI or silently skip the gate
    on a live one.
    """

    spy = _SelectSpy("Cancel", "Cancel")
    ctx = _FakeCtx(has_ui=False, ui=spy)
    resolved = _resolved()

    await request_spawn_consent(ctx, resolved, "t", PermissionMode.AUTO_ACCEPT, cwd="/w")
    assert spy.calls == []

    ctx.flag = True
    await request_spawn_consent(ctx, resolved, "t", PermissionMode.AUTO_ACCEPT, cwd="/w")
    assert len(spy.calls) == 1

    ctx.flag = False
    await request_spawn_consent(ctx, resolved, "t", PermissionMode.AUTO_ACCEPT, cwd="/w")
    assert len(spy.calls) == 1


# --- the dialog body --------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "approval", "parent", "expected_mode"),
    [
        # Write-capable clamp: the dialog fires because authority is at stake.
        ("project", "inherit", PermissionMode.AUTO_ACCEPT, "auto-accept-edits"),
        # Read-only clamp on a DECLARING profile: the dialog still fires.
        ("user", "auto", PermissionMode.DEFAULT, "plan"),
    ],
    ids=["project-write-capable", "user-declaring-widenable"],
)
async def test_dialog_shows_source_path_and_scope(
    scope: str, approval: str, parent: PermissionMode, expected_mode: str
) -> None:
    """The source path is the ONE line in the dialog the model cannot influence.

    ``ExtensionUIDialogOptions`` (``extensions/ext_ui.py:55-67``) carries only
    ``signal`` and ``timeout`` and the extension-facing ``select`` has no
    body/detail parameter, so every piece of context rides the ``title``
    string. Showing ``resolved.source_path`` is mandatory (residual R2): the
    task text below it is model-authored and therefore injectable, while the
    path names a human-owned file the user can go and read.

    REWRITTEN TWICE FOR THE 2026-07-27 POLICY, not weakened. It used to drive a
    PROJECT-scoped profile under a DEFAULT parent — the combination that produces
    no dialog at all (read-only clamp, and project scope can never be widened) —
    so the assertion would have been made against a modal that is deliberately no
    longer shown. The replacement's second case then had to gain
    ``approval_mode: auto``: with the widening option now restricted to declaring
    profiles, a plain ``inherit`` under a DEFAULT parent renders nothing either.
    Both cases that DO prompt are exercised, and the scope + clamp assertions are
    kept per case rather than dropped: the point of the test is that the body
    names the identity, and it has to hold for a project profile as much as for a
    user one.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _resolved(
        scope=scope,
        approval_mode=approval,
        name="helper",
        source_path="/repo/.aelix/agents/helper.md",
    )
    await request_spawn_consent(ctx, resolved, "task", parent, cwd="/repo")

    title = spy.calls[0][0]
    assert "/repo/.aelix/agents/helper.md" in title
    assert scope in title
    assert "helper" in title
    assert "/repo" in title
    assert expected_mode in title


async def test_task_text_is_truncated_to_300_chars() -> None:
    """RESIDUAL R2 — the task is written by the model, not by the user.

    Two independent reasons to bound it. Injection: a long task can bury the
    real instruction below the fold so the human approves something they never
    read. Rendering: ``tui/overlay.py``'s ``_CappedContainer`` CLIPS rather
    than scrolls, so an unbounded title can push the options — including
    Cancel — off screen.

    Driven with a DECLARING profile since the 2026-07-27 second amendment: a
    plain ``inherit`` under a DEFAULT parent no longer renders a dialog, so there
    would be no title to assert against.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    task = "A" * 5000
    await request_spawn_consent(
        ctx, _declaring(), task, PermissionMode.DEFAULT, cwd="/w"
    )

    title = spy.calls[0][0]
    assert "A" * (TASK_PREVIEW_CHARS + 1) not in title
    assert "A" * 50 in title
    assert "…" in title
    assert len(title) < 700


async def test_task_newlines_are_collapsed() -> None:
    """The character budget alone does not bound the dialog's HEIGHT.

    A task made of 300 newlines would otherwise render as a 300-row modal and
    hide the options underneath it.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    await request_spawn_consent(
        ctx, _declaring(), "\n".join(["x"] * 200), PermissionMode.DEFAULT, cwd="/w"
    )
    title = spy.calls[0][0]
    assert len(title.splitlines()) < 15


# --- declining --------------------------------------------------------------


async def test_esc_declines() -> None:
    """``select`` returns ``None`` on Esc (``tui/context.py:429``)."""

    spy = _SelectSpy(None)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    grant = await request_spawn_consent(
        ctx, _resolved(), "t", PermissionMode.AUTO_ACCEPT, cwd="/w"
    )
    assert grant.consented is False
    assert grant.widened is False


async def test_cancel_option_declines() -> None:
    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    grant = await request_spawn_consent(
        ctx, _resolved(), "t", PermissionMode.AUTO_ACCEPT, cwd="/w"
    )
    assert grant.consented is False


async def test_a_raising_dialog_declines_and_never_allows() -> None:
    """DENY ON ERROR — the single worst failure mode this file could have.

    ``select`` can raise ``NotImplementedError`` (the headless binding, which is
    genuinely reachable if the UI is re-bound between the ``has_ui`` check and
    the call) or anything a TUI implementation chooses to raise. A refusal that
    turns into an ALLOW because a dialog threw would silently spawn an agent
    nobody approved.
    """

    spy = _SelectSpy()
    spy.raises = NotImplementedError("headless binding")
    ctx = _FakeCtx(has_ui=True, ui=spy)
    grant = await request_spawn_consent(
        ctx, _resolved(), "t", PermissionMode.YOLO, cwd="/w"
    )
    assert grant.consented is False
    assert grant.widened is False


@pytest.mark.parametrize("parent", _PARENTS)
async def test_a_nonsense_answer_declines(parent: PermissionMode) -> None:
    """Anything that is not one of the rendered options is treated as Esc.

    THE STRING CASE IS THE ONE THAT MATTERED, and this test used to let it
    through (P2 review, MEDIUM #1): it asserted only ``widened is False`` for a
    ``str``, which was true of a full GRANT at the clamp. ``consent.py`` tested
    ``answer is None or answer == CANCEL_OPTION`` — a deny-list — so every other
    string fell past both branches onto ``_grant(clamped, consented=True)``.
    Measured before the fix, with ``answer="<<< user pressed something weird
    >>>"``: ``parent=yolo -> consented=True mode=yolo``, i.e. an unattended
    bash-capable child authorised by a string nobody was shown.

    Both halves are asserted now, for every parent posture, because "did it
    widen" and "did it consent at all" are different questions and only the
    second one is the gate.

    Driven with a DECLARING profile since the 2026-07-27 second amendment, so
    that all five parents actually reach the dialog: under ``plan`` and
    ``default`` an ``inherit`` profile clamps to ``plan`` and is no longer asked
    anything, and a test asserting "the answer was refused" against a modal that
    was never shown would pass for the wrong reason.
    """

    for answer in (
        object(),
        42,
        ["Cancel"],
        "Run read-only (nonsense)",
        "<<< user pressed something weird >>>",
        "",
        "cancel",  # wrong case: not the rendered option
    ):
        spy = _SelectSpy(answer)
        ctx = _FakeCtx(has_ui=True, ui=spy)
        grant = await request_spawn_consent(
            ctx, _declaring(), "t", parent, cwd="/w"
        )
        assert spy.calls, (parent, answer)
        assert grant.consented is False, (parent, answer)
        assert grant.widened is False, (parent, answer)


async def test_cancellation_propagates_rather_than_becoming_a_decision() -> None:
    """``BaseException`` is deliberately NOT swallowed.

    A ``CancelledError`` means the turn is being aborted; synthesising a
    decision the human never made would be worse than propagating.
    """

    spy = _SelectSpy()
    spy.raises = asyncio.CancelledError()
    ctx = _FakeCtx(has_ui=True, ui=spy)
    with pytest.raises(asyncio.CancelledError):
        await request_spawn_consent(
            ctx, _resolved(), "t", PermissionMode.AUTO_ACCEPT, cwd="/w"
        )


# --- bounded widening -------------------------------------------------------


@pytest.mark.parametrize("parent", _PARENTS)
async def test_project_scope_never_offers_widening(parent: PermissionMode) -> None:
    """THE B4-PRESERVED PIN.

    A project-scoped profile can never be widened, dialog or no dialog. B4's
    subject was *a repo file widening silently*, and a human answering a modal
    is not that — but a checked-in file must not be able to REQUEST the modal
    that widens it either. Note the deliberate asymmetry with §(f), which
    refuses project-scoped IDENTITIES on the model-driven door outright.

    REWRITTEN FOR THE 2026-07-27 POLICY CHANGE, and STRICTLY STRONGER than
    before. It used to assert a two-option dialog for every parent; under the
    new rule the two read-only parents (``plan`` / ``default``) get NO dialog,
    because a project profile can never be widened and a read-only child has
    nothing to authorise. The B4 property is now asserted on the GRANT — the
    thing that actually reaches ``SpawnPlan.permission_mode`` — in both
    branches, so "never widened" holds whether or not a human was asked. The
    old form could only speak about the branch that rendered options.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _resolved(scope="project", approval_mode="auto")
    grant = await request_spawn_consent(ctx, resolved, "t", parent, cwd="/w")

    clamp = child_permission_mode("auto", parent, "project", has_ui=True)
    # The invariant that holds on EVERY branch: a repo file never buys authority.
    assert grant.widened is False
    assert grant.mode is clamp

    # ``approval_mode: auto`` is the strongest DECLARATION a profile can make, so
    # this is the sharpest form of the ban: even a repo file that asks for write
    # authority in as many words gets no widening option.
    if not _dialog_expected(clamp, "project", "auto"):
        # Read-only + unwidenable → no question to ask, and the spawn proceeds
        # at the clamp. Cancel was queued and never consumed.
        assert spy.calls == []
        assert grant.consented is True
        return

    options = spy.calls[0][1]
    # Exactly the two-option (no-widening) form. Asserted as an equality against
    # the un-widened builder rather than as a substring search: under an
    # AUTO_ACCEPT or YOLO parent the CLAMP itself is already
    # ``auto-accept-edits``/``yolo``, so its own label legitimately names that
    # posture. What must be absent is the extra GRANT option.
    assert options == build_options(clamp, may_widen=False)
    assert len(options) == 2
    assert options[-1] == CANCEL_OPTION


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", ["user", "project", "explicit"])
@pytest.mark.parametrize("approval", ["inherit", "ask", "auto", "deny"])
async def test_widening_ceiling_is_auto_accept_edits(
    parent: PermissionMode, scope: str, approval: str
) -> None:
    """The dialog NEVER GRANTS more than ``auto-accept-edits``, for any parent.

    Stated as a property of the WIDENING option, not of the option list's text:
    the first option always carries the clamp, and under a YOLO parent the clamp
    legitimately IS ``yolo`` (that is the parent's own posture being inherited,
    not the dialog raising anything). What the ceiling forbids is a dialog that
    hands out ``auto`` or ``yolo`` as an UPGRADE — which is what keeps bash in
    the child gated by the child's own ladder even after a human says yes.

    REWRITTEN FOR THE 2026-07-27 POLICY CHANGE, then extended by its second
    amendment. Twenty-five of the 60 cells no longer render a dialog at all —
    project scope with a read-only clamp, plus every ``inherit`` / ``deny``
    profile whose clamp grants no write authority. Rather than dropping them from
    the parametrisation — which would have quietly shrunk the space this ceiling
    is asserted over — they assert the ceiling on the GRANT: no dialog means no
    widening, which is the ceiling holding by construction. All 60 cells are
    still exercised.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _resolved(scope=scope, approval_mode=approval)
    grant = await request_spawn_consent(ctx, resolved, "t", parent, cwd="/w")

    clamp = child_permission_mode(approval, parent, scope, has_ui=True)
    if not _dialog_expected(clamp, scope, approval):
        assert spy.calls == []
        assert grant.widened is False
        assert grant.mode is clamp
        assert posture_rank(grant.mode) <= posture_rank(parent)
        return

    options = spy.calls[0][1]
    assert options[0] == build_options(clamp, may_widen=False)[0]
    assert options[-1] == CANCEL_OPTION
    assert len(options) in (2, 3)
    if len(options) == 3:
        # The ONLY upgrade any dialog may offer.
        assert options[1] == (
            f"Allow file edits for this run ({PermissionMode.AUTO_ACCEPT.value})"
        )
        # ... and it is only rendered when it actually grants something, to a
        # non-project profile that DECLARED it needs write authority.
        assert posture_rank(clamp) < posture_rank(PermissionMode.AUTO_ACCEPT)
        assert scope != "project"
        assert approval in _DECLARING


async def test_widening_not_offered_when_it_would_be_a_noop() -> None:
    """An option that grants nothing is a lie, and must not be rendered.

    Under an AUTO_ACCEPT parent the clamp is already ``auto-accept-edits``.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    await request_spawn_consent(
        ctx, _resolved(), "t", PermissionMode.AUTO_ACCEPT, cwd="/w"
    )
    assert len(spy.calls[0][1]) == 2


async def test_widening_not_offered_above_the_ceiling() -> None:
    """A YOLO parent with ``inherit`` clamps to YOLO — nothing to raise."""

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    await request_spawn_consent(ctx, _resolved(), "t", PermissionMode.YOLO, cwd="/w")
    assert len(spy.calls[0][1]) == 2


async def test_widening_grant_records_widened_true() -> None:
    """The one path that raises authority above the clamp.

    ``approval_mode: auto`` under a DEFAULT parent is the canonical shape of it
    since the 2026-07-27 second amendment: the profile declared it needs write
    authority, finding B4's clamp refused to grant it silently, and one human
    answer is what closes the gap.
    """

    options = build_options(PermissionMode.PLAN, may_widen=True)
    spy = _SelectSpy(options[1])
    ctx = _FakeCtx(has_ui=True, ui=spy)
    grant = await request_spawn_consent(
        ctx, _declaring(), "t", PermissionMode.DEFAULT, cwd="/w"
    )
    assert grant.mode is PermissionMode.AUTO_ACCEPT
    assert grant.widened is True
    assert grant.consented is True
    assert spy.calls[0][1] == options


async def test_read_only_choice_uses_the_clamp() -> None:
    options = build_options(PermissionMode.PLAN, may_widen=True)
    spy = _SelectSpy(options[0])
    ctx = _FakeCtx(has_ui=True, ui=spy)
    grant = await request_spawn_consent(
        ctx, _declaring(), "t", PermissionMode.DEFAULT, cwd="/w"
    )
    assert grant.mode is PermissionMode.PLAN
    assert grant.widened is False
    assert grant.consented is True


def test_build_options_shape() -> None:
    """Cancel is always last and always present, widening or not."""

    narrow = build_options(PermissionMode.PLAN, may_widen=False)
    assert narrow == ["Run read-only (plan)", CANCEL_OPTION]
    wide = build_options(PermissionMode.PLAN, may_widen=True)
    assert wide[0] == "Run read-only (plan)"
    assert wide[1] == "Allow file edits for this run (auto-accept-edits)"
    assert wide[2] == CANCEL_OPTION


@pytest.mark.parametrize(
    "clamp",
    [PermissionMode.AUTO_ACCEPT, PermissionMode.AUTO, PermissionMode.YOLO],
)
def test_a_writable_clamp_is_never_labelled_read_only(clamp: PermissionMode) -> None:
    """CORRECTED DIALOG COPY — the label must not lie about the grant.

    The drafted string was ``f"Run read-only ({clamped.value})"``
    unconditionally. That is true for the ``plan`` clamp (the DEFAULT-parent
    case) and FALSE for every other one: under an AUTO_ACCEPT or YOLO parent
    with ``approval_mode: inherit`` the clamp is that same posture, so a button
    reading "Run read-only" would actually grant writes — or, at YOLO,
    unattended shell execution. The granted posture is on screen in both
    branches; only the verb changes.
    """

    label = build_options(clamp, may_widen=False)[0]
    assert "read-only" not in label
    assert clamp.value in label
    assert build_options(PermissionMode.PLAN, may_widen=False)[0] == (
        "Run read-only (plan)"
    )


# --- WHEN the dialog fires (owner decision, 2026-07-27) ---------------------
#
# The five discriminating cases. (a) and (b) are the two halves of the change;
# (c), (d) and (e) are the properties it must NOT have moved.


async def test_read_only_and_unwidenable_does_not_prompt() -> None:
    """(a) THE POLICY CHANGE. No authority at stake → no dialog.

    A project-scoped profile under a DEFAULT parent clamps to ``plan`` and can
    never be widened (constraint 1), so ``build_options`` would have rendered
    exactly ["Run read-only (plan)", "Cancel"] — a modal whose only real answer
    is "yes". It is not shown. The grant is byte-for-byte the one the baseline
    option would have produced, which is the whole safety argument: what changed
    is what the human is ASKED, never what the child GETS.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _resolved(scope="project", source_path="/repo/.aelix/agents/x.md")
    grant = await request_spawn_consent(ctx, resolved, "t", PermissionMode.DEFAULT, cwd="/w")

    assert spy.calls == []
    assert grant.mode is PermissionMode.PLAN
    assert grant.widened is False
    assert grant.consented is True
    # And the identity is still carried, because the statusline / event row and
    # the ``execute()`` re-check are what the human sees instead of a modal.
    assert grant.source_path == "/repo/.aelix/agents/x.md"
    assert grant.scope == "project"


@pytest.mark.parametrize("approval", _DECLARING)
@pytest.mark.parametrize("scope", ["user", "explicit"])
async def test_a_declaring_read_only_profile_still_prompts(
    scope: str, approval: str
) -> None:
    """(b) THE ORDERING PIN — the early-out is evaluated AFTER ``_may_widen``.

    A DECLARING profile under a DEFAULT parent clamps to ``plan``, so a naive
    "read-only ⇒ skip" would silently swallow it. But this one CAN be lifted to
    ``auto-accept-edits`` by one human answer, and an option that exists must be
    offered — otherwise the only way to let a child write would be shift+tab,
    which raises the WHOLE SESSION and hands that posture to every subsequent
    delegation (the reason read-only-only was rejected, OC-3).

    Both non-project scopes: ``explicit`` (``--agent-file``) is a path the human
    typed and must not be treated as project.

    REWRITTEN AND RENAMED FOR THE 2026-07-27 SECOND AMENDMENT (it was
    ``test_read_only_but_widenable_still_prompts``). It used to parametrise over
    all four ``approval_mode`` values, asserting that a read-only user-scoped
    spawn prompts whatever the profile said — which is precisely the rule the
    owner replaced. The two DECLARING values keep the old assertions verbatim;
    the two that declare nothing moved to
    :func:`test_a_non_declaring_read_only_profile_renders_no_dialog`, which is
    the other half of the same parametrisation and asserts the opposite outcome.
    Nothing was dropped.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _resolved(scope=scope, approval_mode=approval)
    await request_spawn_consent(ctx, resolved, "t", PermissionMode.DEFAULT, cwd="/w")

    assert len(spy.calls) == 1
    options = spy.calls[0][1]
    assert options == build_options(PermissionMode.PLAN, may_widen=True)
    assert len(options) == 3


@pytest.mark.parametrize("approval", _NON_DECLARING)
@pytest.mark.parametrize("scope", ["user", "explicit"])
@pytest.mark.parametrize("parent", _PARENTS)
async def test_a_non_declaring_read_only_profile_renders_no_dialog(
    parent: PermissionMode, scope: str, approval: str
) -> None:
    """(b′) THE 2026-07-27 SECOND AMENDMENT, stated as its own case.

    ``inherit`` asks for no more authority than the parent already has; ``deny``
    asks for less. Neither declares that it needs write authority, so the
    widening option does not exist for them and — wherever the clamp itself
    grants none — there is nothing left to ask. The human sees no modal and the
    spawn proceeds read-only.

    Driven over EVERY parent posture, including the loose ones, because the two
    halves of the rule have to stay separable: under ``auto-accept-edits`` /
    ``auto`` / ``yolo`` an ``inherit`` profile still prompts, and it prompts
    through ``grants_write_authority`` rather than through the widening option
    this test is about. ``deny`` clamps to ``plan`` under all five and therefore
    never prompts at all.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _resolved(scope=scope, approval_mode=approval)
    grant = await request_spawn_consent(ctx, resolved, "t", parent, cwd="/w")

    clamp = child_permission_mode(approval, parent, scope, has_ui=True)
    assert grant.widened is False
    if grants_write_authority(clamp):
        # The other half of the rule, untouched by this amendment.
        assert len(spy.calls) == 1
        assert spy.calls[0][1] == build_options(clamp, may_widen=False)
        return
    assert spy.calls == []
    assert grant.mode is clamp
    assert grant.consented is True


@pytest.mark.parametrize("declaring", _DECLARING)
@pytest.mark.parametrize("silent", _NON_DECLARING)
async def test_the_declaration_is_the_only_difference(
    declaring: str, silent: str
) -> None:
    """(b″) THE DISCRIMINATING PAIR — same parent, same scope, same clamp.

    Two spawns identical in every input the previous rule looked at: the same
    ``default`` parent, the same ``user`` scope, the same ``plan`` clamp, the
    same task and directory. The only difference is the line in the profile. One
    renders a dialog offering the bounded widening; the other renders nothing.

    This is the test that fails if the declaration check is deleted, weakened to
    "any non-``deny`` value", or accidentally keyed on the clamp — which is what
    every other assertion in this file would still tolerate.
    """

    parent = PermissionMode.DEFAULT
    assert child_permission_mode(declaring, parent, "user", has_ui=True) is (
        child_permission_mode(silent, parent, "user", has_ui=True)
    )

    loud_spy = _SelectSpy(CANCEL_OPTION)
    loud = await request_spawn_consent(
        _FakeCtx(has_ui=True, ui=loud_spy),
        _resolved(approval_mode=declaring),
        "t",
        parent,
        cwd="/w",
    )
    quiet_spy = _SelectSpy(CANCEL_OPTION)
    quiet = await request_spawn_consent(
        _FakeCtx(has_ui=True, ui=quiet_spy),
        _resolved(approval_mode=silent),
        "t",
        parent,
        cwd="/w",
    )

    assert len(loud_spy.calls) == 1
    assert loud_spy.calls[0][1] == build_options(PermissionMode.PLAN, may_widen=True)
    assert loud.consented is False  # Cancel was a real answer to a real question.

    assert quiet_spy.calls == []
    assert quiet.mode is PermissionMode.PLAN
    assert quiet.widened is False
    assert quiet.consented is True


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", ["user", "project", "explicit"])
async def test_deny_never_widens(parent: PermissionMode, scope: str) -> None:
    """``deny`` IS THE EXPLICIT OPPOSITE OF A DECLARATION, and it must stay that way.

    The most load-bearing single value in the amendment: a profile whose author
    wrote ``approval_mode: deny`` must never be handed a dialog offering to
    widen it, in any scope, under any parent posture — including ``yolo``, where
    every other profile inherits real authority. Its clamp is ``plan``
    everywhere, so no dialog is rendered at all and the grant is read-only.

    Asserted on the GRANT as well as on the option list, because "no widening
    option was offered" and "no widening happened" are different claims and only
    the second one bounds the child.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    grant = await request_spawn_consent(
        ctx, _resolved(scope=scope, approval_mode="deny"), "t", parent, cwd="/w"
    )

    assert child_permission_mode("deny", parent, scope, has_ui=True) is (
        PermissionMode.PLAN
    )
    assert spy.calls == []
    assert grant.mode is PermissionMode.PLAN
    assert grant.widened is False
    assert grant.consented is True


@pytest.mark.parametrize("parent", [PermissionMode.AUTO_ACCEPT, PermissionMode.YOLO])
@pytest.mark.parametrize("scope", ["user", "project", "explicit"])
async def test_a_write_capable_clamp_always_prompts(
    parent: PermissionMode, scope: str
) -> None:
    """(c) UNCHANGED, and the case the whole gate exists for (finding OC-1).

    A user who pressed shift+tab once for their OWN convenience must not thereby
    hand every future delegation an unattended write-capable child. This holds
    at project scope too, where widening is banned — the dialog there offers
    "take the inherited posture, or cancel", and cancelling is a real answer
    because the alternative is a child that can write.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    clamp = child_permission_mode("inherit", parent, scope, has_ui=True)
    assert grants_write_authority(clamp)

    grant = await request_spawn_consent(
        ctx, _resolved(scope=scope), "t", parent, cwd="/w"
    )
    assert len(spy.calls) == 1
    assert grant.consented is False


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", ["user", "project", "explicit"])
@pytest.mark.parametrize("approval", ["inherit", "ask", "auto", "deny"])
async def test_headless_never_prompts_whatever_the_clamp(
    parent: PermissionMode, scope: str, approval: str
) -> None:
    """(d) UNCHANGED — the headless path never depended on the new rule.

    The whole 60-cell space, asserted for the one property that matters to every
    existing ``-p`` / ``--mode json`` / RPC user: no prompt, no refusal, and the
    clamp applies exactly as ``child_permission_mode`` computed it with
    ``has_ui=False``.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=False, ui=spy)
    grant = await request_spawn_consent(
        ctx, _resolved(scope=scope, approval_mode=approval), "t", parent, cwd="/w"
    )

    assert spy.calls == []
    assert grant.consented is True
    assert grant.widened is False
    assert grant.mode is child_permission_mode(approval, parent, scope, has_ui=False)


@pytest.mark.parametrize("answer", [CANCEL_OPTION, None, "not an option"])
async def test_declining_a_widenable_read_only_spawn_still_blocks(
    answer: object,
) -> None:
    """(e) UNCHANGED — the early-out must not turn a decline into a grant.

    The dangerous shape of this change would be a skip placed AFTER the dialog:
    "read-only, so consent anyway". This drives the case that DOES prompt — a
    DECLARING profile at a read-only clamp, since the second amendment — and
    proves the refusal still reaches the caller: Cancel, Esc and a string nobody
    was shown alike.
    """

    spy = _SelectSpy(answer)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    grant = await request_spawn_consent(
        ctx, _declaring(), "t", PermissionMode.DEFAULT, cwd="/w"
    )
    assert len(spy.calls) == 1
    assert grant.consented is False
    assert grant.widened is False


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("scope", ["user", "project", "explicit"])
@pytest.mark.parametrize("approval", ["inherit", "ask", "auto", "deny"])
async def test_the_prompt_decision_matches_the_rule_over_the_whole_matrix(
    parent: PermissionMode, scope: str, approval: str
) -> None:
    """All 60 live-UI cells: a dialog appears IFF authority is at stake.

    The exhaustive form, so the rule cannot be satisfied case-by-case while
    drifting somewhere in the middle of the lattice. ``_dialog_expected``
    restates the policy from ``grants_write_authority`` + the widening
    constraints rather than calling into ``consent.py``, so this is a
    comparison against the RULE, not against the implementation of it.

    UPDATED FOR THE 2026-07-27 SECOND AMENDMENT by giving ``_dialog_expected``
    the ``approval_mode`` it now depends on. The parametrisation is unchanged and
    still covers the whole space; what moved is the expected answer in 14 of the
    cells — every ``inherit`` / ``deny`` profile whose clamp grants no write
    authority.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _resolved(scope=scope, approval_mode=approval)
    grant = await request_spawn_consent(ctx, resolved, "t", parent, cwd="/w")

    clamp = child_permission_mode(approval, parent, scope, has_ui=True)
    expected = _dialog_expected(clamp, scope, approval)
    assert bool(spy.calls) is expected, (parent, scope, approval, clamp)
    if not expected:
        # The skipped cells are exactly the harmless ones: a child that cannot
        # mutate, launched at the clamp, with nothing a human could have added.
        assert grants_write_authority(clamp) is False
        assert grant.mode is clamp
        assert grant.widened is False
        assert grant.consented is True


# --- no persistence ---------------------------------------------------------


async def test_grant_is_not_persisted_between_spawns() -> None:
    """P2 ASKS EVERY TIME — three identical spawns, three dialogs.

    This is the shape the PRODUCTION callers use, and there is no other: neither
    ``request_spawn_consent`` nor ``build_options`` takes a memo any more (P2
    review, HIGH #2/#5 — see the section at the end of this file). Nothing is
    written to disk either: a persisted per-profile store remains P3, and when
    it lands it must key on ``(profile name, source_path, granted mode)`` —
    never on a ``tool:agent`` rule key, for the args-blind ``_rule_key`` reason
    in the module docstring.
    """

    options = build_options(PermissionMode.PLAN, may_widen=True)
    spy = _SelectSpy(options[1], options[1], options[1])
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _declaring()
    for _ in range(3):
        grant = await request_spawn_consent(
            ctx, resolved, "same task", PermissionMode.DEFAULT, cwd="/w"
        )
        assert grant.widened is True
    assert len(spy.calls) == 3


async def test_a_decline_does_not_poison_the_next_spawn() -> None:
    """And an approval does not carry forward either."""

    options = build_options(PermissionMode.PLAN, may_widen=True)
    spy = _SelectSpy(CANCEL_OPTION, options[0])
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _declaring()
    first = await request_spawn_consent(
        ctx, resolved, "t", PermissionMode.DEFAULT, cwd="/w"
    )
    second = await request_spawn_consent(
        ctx, resolved, "t", PermissionMode.DEFAULT, cwd="/w"
    )
    assert first.consented is False
    assert second.consented is True


# --- serialisation ----------------------------------------------------------


async def test_concurrent_requests_are_serialized() -> None:
    """Two modals must never be open at once.

    ``tui/chrome.py:524``'s ``_modal`` is a SINGLE slot: ``mount_modal``
    overwrites unconditionally, the first Future is orphaned, and the turn
    hangs until Ctrl+C. The kernel already serialises the ``tool_call`` hook
    (``loop.py:829-839``), and the tool additionally declares
    ``execution_mode="sequential"``, but the lock is what covers the
    ``/agents run`` door and any future caller.
    """

    options = build_options(PermissionMode.PLAN, may_widen=True)
    spy = _SelectSpy(options[0], options[0])
    spy.delay = 0.05
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _declaring()
    await asyncio.gather(
        request_spawn_consent(ctx, resolved, "a", PermissionMode.DEFAULT, cwd="/w"),
        request_spawn_consent(ctx, resolved, "b", PermissionMode.DEFAULT, cwd="/w"),
    )
    assert len(spy.calls) == 2
    assert spy.concurrent is False


# --- the grant object -------------------------------------------------------


async def test_grant_carries_the_identity_it_approved() -> None:
    """Downstream must be able to prove WHICH identity was approved.

    The grant is popped by ``tool_call_id`` in ``execute()``; if it did not
    carry the profile and its source path, a spawner could pair one human
    decision with a different profile entirely.
    """

    spy = _SelectSpy(build_options(PermissionMode.PLAN, may_widen=True)[0])
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _declaring(name="auditor", source_path="/u/.aelix/agents/auditor.md")
    grant = await request_spawn_consent(
        ctx, resolved, "t", PermissionMode.DEFAULT, cwd="/w"
    )
    assert spy.calls, "this test is about the grant a real ANSWER produced"
    assert grant.profile == "auditor"
    assert grant.source_path == "/u/.aelix/agents/auditor.md"
    assert grant.scope == "user"


def test_grant_is_frozen() -> None:
    """A grant is a decision, not a mutable bag: nothing may edit it after the fact."""

    grant = SpawnGrant(
        profile="p",
        source_path="/x.md",
        scope="user",
        mode=PermissionMode.PLAN,
        widened=False,
        consented=True,
    )
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError is a dataclass detail
        grant.mode = PermissionMode.YOLO  # type: ignore[misc]


# --- NO session rung, and no memo (P2 review, HIGH #2/#5) --------------------
#
# A "don't ask again for this profile this session" rung was implemented and
# REMOVED. ADR-0197:613-616 is verbatim — "No persistence, and no session memo.
# A grant is per-spawn: P2 asks every time" — repeated in the ADR's *Deferred*
# list at :826 and published in ``docs/decisions/README.md``. The removed rung
# was keyed on ``resolved.source_path`` ALONE, i.e. on neither the task nor the
# cwd, both of which ``build_consent_title`` puts on screen: measured with a
# parent at ``auto-accept-edits``, ONE dialog covered FOUR spawns, three of them
# in directories the MODEL chose, on tasks the MODEL wrote, unattended.
#
# These tests pin the ABSENCE. They are the reason a re-implementation cannot
# land quietly.


@pytest.mark.parametrize("parent", _PARENTS)
async def test_no_fourth_option_is_ever_offered(parent: PermissionMode) -> None:
    """Two or three options, for every parent posture. Never four.

    ``build_options`` is the whole vocabulary of this dialog, so a rung that
    reappears has to appear here first.

    A DECLARING profile since the 2026-07-27 second amendment, so that all five
    parents reach the dialog and the count is asserted over the widest option
    list the code can produce.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    await request_spawn_consent(ctx, _declaring(), "t", parent, cwd="/w")

    options = spy.calls[0][1]
    assert 2 <= len(options) <= 3
    assert options[-1] == CANCEL_OPTION
    assert not any("ask again" in option.lower() for option in options)
    assert not any("remember" in option.lower() for option in options)


def test_the_module_exports_no_session_memo() -> None:
    """No type, no constant, no helper — nothing to wire back up by accident."""

    import aelix_agents.consent as consent_module

    for banned in ("SessionGrantMemo", "REMEMBER_OPTION", "_may_remember"):
        assert not hasattr(consent_module, banned), banned
    assert not any("REMEMBER" in name for name in consent_module.__all__)


def test_request_spawn_consent_takes_no_memo_parameter() -> None:
    """The signature pin.

    The removed rung reached production through a keyword argument that only the
    MODEL-DRIVEN door passed (``extension.py``), while ``/agents run`` passed
    nothing — so the model-chosen delegation asked less often than the
    user-typed one, inverted relative to risk (finding OC-1). There is now no
    parameter to pass.
    """

    import inspect

    params = inspect.signature(request_spawn_consent).parameters
    assert "memo" not in params
    # ``model`` joined the set when the dialog learned to name what will answer.
    # It is the OPPOSITE shape to the removed rung: it adds a fact the human is
    # told, and can only ever add one — no caller can shorten the dialog with it,
    # and nothing it carries reaches the grant.
    assert set(params) == {"ctx", "resolved", "task", "parent", "cwd", "model"}


def test_build_options_takes_no_remember_flag() -> None:
    import inspect

    params = inspect.signature(build_options).parameters
    assert set(params) == {"clamped", "may_widen"}


@pytest.mark.parametrize("parent", _PARENTS)
async def test_approving_one_spawn_never_suppresses_the_next(
    parent: PermissionMode,
) -> None:
    """P2 ASKS EVERY TIME — through the PRODUCTION call shape, for every parent.

    The removed rung's own regression test could not observe it, because it
    called ``request_spawn_consent`` without the ``memo`` argument the shipped
    caller passed. This one drives exactly what ``extension.py`` and
    ``runtime.py`` drive, so there is no shape left in which a standing grant
    could hide.
    """

    # Answer whatever the baseline option happens to be for this parent — its
    # label differs by posture, its INDEX never does (``build_options``). The
    # clamp is computed with the SAME ``approval_mode`` the profile carries: a
    # DECLARING profile since the 2026-07-27 second amendment, so that the two
    # read-only parents reach the dialog at all, and ``auto`` clamps differently
    # from ``inherit`` under a YOLO parent.
    baseline = build_options(
        child_permission_mode("auto", parent, "user", has_ui=True), may_widen=False
    )[0]
    spy = _SelectSpy(baseline, baseline, baseline)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _declaring()

    for task in ("summarise the README", "rewrite src/", "and again"):
        grant = await request_spawn_consent(ctx, resolved, task, parent, cwd="/w")
        assert grant.consented is True

    assert len(spy.calls) == 3


# --- ONE TASK IS NOT A SEQUENCE OF ONE-CHARACTER TASKS (P3 WP-4) -------------
#
# P3 adds a BATCH door (``request_spawn_consent_batch``) beside this one. An
# earlier draft of that work re-typed THIS function's ``task`` parameter to
# ``Sequence[str]`` instead of adding a second entry point — and ``str``
# SATISFIES ``Sequence[str]``, so ``/agents run scout "review the auth module"``
# (``runtime.py:539-544`` passes a bare ``str``) would have type-checked green
# while the renderer iterated the string: *"Delegate 23 tasks to agent 'scout'?"*
# with the rows ``[1/23] r``, ``[2/23] e``, … — 23 rows on the one door a human
# typed, past the height budget, with ``Cancel`` clipped off the bottom by
# ``tui/overlay.py``'s ``_CappedContainer``. No test rendered that dialog, so
# nothing would have caught it.
#
# The single-task signatures were therefore kept, and these two tests are what
# makes "kept" checkable: one pins the SHAPE of the rendered body, the other
# drives the ``/agents run`` door end to end for the first time.


async def test_the_single_task_dialog_renders_exactly_one_task_body() -> None:
    """One task in, one task block out — never one block per character."""

    task = "review the auth module for injection bugs"
    title = build_consent_title(
        _declaring(), task, PermissionMode.PLAN, cwd="/w"
    )

    lines = title.splitlines()
    assert task in title
    assert lines[-1] == task, "the task is the last row, verbatim and alone"
    assert len(lines) == 9, lines
    assert sum(1 for line in lines if line.startswith("Task")) == 1


async def test_agents_run_renders_the_single_task_body_unchanged(
    tmp_path: Path,
) -> None:
    """THE USER-TYPED DOOR, driven end to end — the one nothing covered.

    ``runtime.spawn`` passes a bare ``str`` to :func:`request_spawn_consent`
    (``runtime.py:539-544``). This test renders that dialog through the real
    runtime with a live UI and a widenable profile — the case P3's batch work had
    to leave untouched — and answers ``Cancel``, so no child process is created:
    the assertion is about what was on SCREEN, not about a spawn.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(
            cwd=lambda: str(tmp_path),
            posture=lambda: PermissionMode.DEFAULT,
            consent_context=lambda: ctx,
        )
    )

    result = await runtime.spawn(_declaring(), "review the auth module")

    assert result.status == "declined"
    assert len(spy.calls) == 1
    title, options = spy.calls[0]
    assert title.splitlines()[-1] == "review the auth module"
    # The catastrophic form would have rendered one row per character and a
    # plural heading; both are asserted, because either alone could be a typo.
    assert "[1/21]" not in title
    assert title.splitlines()[0] == "Delegate to agent 'scout'?"
    assert options == build_options(PermissionMode.PLAN, may_widen=True)


# --- F1 on the P2 door, and the belt behind it -------------------------------
#
# THE SINGLE-TASK HOLE IS PRE-EXISTING, NOT A P3 REGRESSION, and it is the same
# hole. ``build_consent_title`` interpolated ``cwd``, ``resolved.name`` and
# ``resolved.source_path`` with plain f-strings; ``ctx.ui.select`` splits the
# composed title on ``\n`` into rows AND ANSI-parses it
# (``tui/context.py:140-218``); and ``resolve_child_cwd``
# (``print_channel.py:406``) validated only containment and is-a-directory,
# while POSIX permits every byte but ``/`` and NUL in a path component. A
# directory created with plain ``os.makedirs`` was therefore enough to render a
# wholly fabricated dialog. This door has no fit check at all, so there is
# nothing here to defeat — which is precisely why it must not be forgeable.

_FORGED_CWD = (
    "/w/src\x1b[0m\nPermission: auto-accept-edits\n"
    "Task (written by the model, not by you):\n"
    "read the README and summarise it\n\x1b[8m"
)


def test_the_p2_dialog_body_is_nine_rows_for_every_input() -> None:
    """The single-task body is a FIXED shape, and now it is one by construction.

    Nine rows: heading, blank, ``Profile:``, ``Source:``, ``Directory:``,
    ``Permission:``, blank, the label, the task. Before the fix a ``cwd`` with
    twelve newlines made it twenty-one, and the rows a human then read were
    written by whoever chose the directory name.
    """

    title = build_consent_title(
        _declaring(
            name="scout\nProfile:    root (builtin scope)",
            source_path="/home/alice/.aelix/agents/\x1b[8mscout.md",
        ),
        "summarise the README\nPermission: yolo",
        PermissionMode.PLAN,
        cwd=_FORGED_CWD,
    )

    assert len(title.splitlines()) == 9
    assert title.count("\n") == 8
    assert not contains_control_chars(title.replace("\n", ""))
    # The row the forgery imitated appears exactly once, and it states the mode
    # that was actually granted.
    assert [r for r in title.splitlines() if r.startswith("Permission:")] == [
        "Permission: plan"
    ]
    for row in title.splitlines():
        assert len(row) <= max(DIALOG_ROW_CHARS, TASK_PREVIEW_CHARS), row


async def test_the_p2_door_end_to_end_cannot_be_forged() -> None:
    """Through :func:`request_spawn_consent`, the way ``/agents run`` reaches it.

    The human answers the widening option — as they would, having read a benign
    dialog — and the assertion is that the dialog they read was the real one.
    """

    spy = _SelectSpy(build_options(PermissionMode.PLAN, may_widen=True)[1])
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent(
        ctx,
        _declaring(),
        "delete every branch except main",
        PermissionMode.DEFAULT,
        cwd=_FORGED_CWD,
    )

    assert grant.consented and grant.widened
    title = spy.calls[0][0]
    assert "\x1b" not in title
    assert title.splitlines()[-1] == "delete every branch except main"
    assert "read the README" not in title


@pytest.mark.parametrize(
    "component",
    [
        "sub\ndir",  # rows
        "sub\x1b[8mdir",  # SGR 8 — hidden text, the forgery's primitive
        "sub\rdir",  # carriage return: overwrite the row in place
        "sub\x9bdir",  # C1: the one-byte spelling of ESC-[
        "sub\x07dir",  # BEL, which no whitespace rule touches
    ],
)
def test_resolve_child_cwd_refuses_a_directory_that_can_steer_the_terminal(
    tmp_path: Path, component: str
) -> None:
    """The BELT (F1). ``consent`` sanitising is the fix; this is the second lock.

    A model-chosen ``cwd`` is the value that reaches
    :func:`~aelix_agents.consent.build_consent_title`, and the reviewer created
    the payload with a plain ``os.makedirs`` — no privileges, and well inside
    ext4's 255-byte component limit. Refusing it here means the string never
    exists to be rendered, and the two defences fail independently.

    Contained and a real directory, so the ONLY thing this test can be failing
    on is the character check.
    """

    (tmp_path / component).mkdir()

    with pytest.raises(ValueError, match="control character"):
        resolve_child_cwd(component, str(tmp_path))


def test_resolve_child_cwd_still_accepts_an_ordinary_directory(
    tmp_path: Path,
) -> None:
    """The belt must not become a new refusal path for legitimate delegation.

    Awkward-but-legal names — spaces, dots, unicode — are NOT control characters
    and must still resolve, or the check would be a functional regression dressed
    as a fix.
    """

    for component in ("sub dir", "sub.dir-2", "서브디렉터리", "a'b\"c"):
        (tmp_path / component).mkdir()
        assert resolve_child_cwd(component, str(tmp_path)) == str(
            (tmp_path / component).resolve()
        )


# === the dialog names the model (live QA, 2026-08-07) =========================
#
# The dialog listed Profile / Source / Directory / Permission / Task — confirmed
# at 200 columns, so this was never truncation. Approving a spawn is approving a
# process that reads this repository and, under a widened grant, writes to it;
# WHICH MODEL ANSWERS is both the price of that and, for a profile that declares
# no ``model:``, a value the human never chose. Every other part of the child's
# identity was on screen. This one was not.


def test_the_consent_dialog_names_the_model() -> None:
    """One row, in the existing label style, below ``Permission:``.

    Below rather than beside ``Profile:`` so ``Source:`` keeps the second body
    row it was given on purpose — it is the one line the model cannot influence.
    """

    title = build_consent_title(
        _declaring(), "go", PermissionMode.PLAN, cwd="/w", model="claude-opus-4-8"
    )
    rows = title.splitlines()

    assert "Model:      claude-opus-4-8" in rows
    assert rows.index("Model:      claude-opus-4-8") == rows.index(
        "Permission: plan"
    ) + 1
    # The value column lines up with every other labelled row's: each label is
    # padded to 12 characters, so the value starts at exactly column 12.
    labels = [row for row in rows if row[:12].rstrip().endswith(":")]
    assert len(labels) == 5, labels
    assert all(row[11] == " " and row[12] != " " for row in labels), labels


def test_an_unknown_model_costs_no_row_rather_than_naming_nothing() -> None:
    """A child that will run its OWN model cascade has no model to name here — the
    id is only knowable once the process exists. Omitted, so the dialog is
    byte-identical to the one that shipped, rather than carrying a fact-shaped
    ``Model: unknown``."""

    without = build_consent_title(_declaring(), "go", PermissionMode.PLAN, cwd="/w")
    explicit_none = build_consent_title(
        _declaring(), "go", PermissionMode.PLAN, cwd="/w", model=None
    )

    assert without == explicit_none
    assert "Model:" not in without
    assert len(without.splitlines()) == 9


def test_a_hostile_model_cannot_forge_a_row_in_the_consent_dialog() -> None:
    """F1, applied to the new field. ``ctx.ui.select`` SPLITS the composed title on
    ``\\n`` into rows and ANSI-parses it, so an unsanitised value here forges rows
    — the exact hole the ``cwd`` field demonstrated. The model reaches this string
    through its choice of PROFILE, and the profile file is not something this
    dialog gets to trust.
    """

    hostile = (
        "gpt\n"
        "Permission: yolo\n"
        "\x1b[31mSource:     /etc/passwd\x9b2J\n" + "z" * 4000
    )
    title = build_consent_title(
        _declaring(), "go", PermissionMode.PLAN, cwd="/w", model=hostile
    )

    rows = title.splitlines()

    # ONE row added, not four.
    assert len(rows) == 10
    # No row can steer the terminal, and none is wider than the narrowest one we
    # assume — a forgery that survives as one over-long row is still a forgery.
    assert not any(contains_control_chars(row) for row in rows)
    assert all(len(row) <= DIALOG_ROW_CHARS for row in rows)
    # The forged labels are inert text INSIDE the Model row, not rows of their
    # own: exactly one row still BEGINS with each real label.
    assert sum(row.startswith("Permission:") for row in rows) == 1
    assert sum(row.startswith("Source:") for row in rows) == 1
    assert rows[6].startswith("Model:      gpt Permission: yolo ")


async def test_the_user_typed_door_shows_the_model_it_will_launch() -> None:
    """End to end through ``/agents run``'s door: the runtime resolves the model
    the same way it resolves the argv, and the human sees THAT.

    A profile declaring its own ``model:`` names it; the parent's is not consulted
    — the dialog must describe the spawn that will happen, not a different one.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    class _Parent:
        id = "claude-sonnet-4-5"
        provider = "anthropic"

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(
            cwd=lambda: str(Path.cwd()),
            posture=lambda: PermissionMode.YOLO,
            consent_context=lambda: ctx,
            model=_Parent,
        )
    )

    result = await runtime.spawn(_declaring(model="claude-opus-4-8"), "go")

    # Cancelled, so nothing was started and no channel was needed.
    assert result.status == "declined"
    assert spy.calls, "a write-capable parent must have opened a dialog"
    assert "Model:      claude-opus-4-8" in spy.calls[0][0].splitlines()
    # The PARENT's model is not what will run, so it is not what is shown.
    assert "claude-sonnet-4-5" not in spy.calls[0][0]


async def test_a_profile_with_no_model_shows_the_one_it_will_inherit() -> None:
    """The other half of the emission table. A profile declaring neither ``model:``
    nor ``provider:`` inherits the parent's run-scope model onto its argv — so
    that is the model the human is approving, and the dialog says so."""

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    class _Parent:
        id = "claude-sonnet-4-5"
        provider = "anthropic"

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(
            cwd=lambda: str(Path.cwd()),
            posture=lambda: PermissionMode.YOLO,
            consent_context=lambda: ctx,
            model=_Parent,
        )
    )

    await runtime.spawn(_declaring(), "go")

    assert "Model:      claude-sonnet-4-5" in spy.calls[0][0].splitlines()
