"""The child permission-posture CLAMP — ADR-0197 §(e).

PURE. No ``asyncio``, no ``os``, no ``subprocess``, no filesystem, no UI. The
only import is the posture enum itself, so this module can be reasoned about
(and unit-tested) as a total function over a five-value lattice.

THE SHIPPED GUARANTEE, in full. The short form — *"a child never gains a
permission mode looser than the parent's"* — is true of THIS function and is
NOT the whole product rule. What ships is one clause longer:

    A child never gains a permission mode looser than the parent's UNLESS a
    human, at the parent's own TUI, explicitly granted it for that one spawn —
    and then never above ``auto-accept-edits``, and never for a project-scoped
    profile.

The second half lives in :mod:`aelix_agents.consent` (ADR-0197 §(i)). This
module computes the FLOOR that a spawn gets when nobody answers a dialog. Both
halves must be read together; do not let the shorter sentence survive anywhere.

Why a clamp and not a lookup table (P2 review finding B4). The obvious shape —
map ``approval_mode`` to a posture and hand it to the child — lets a profile
declaring ``approval_mode: auto`` lift a DEFAULT (prompt-for-everything) parent
into a child that auto-accepts repo-wide writes with no human in the loop. The
child cannot compensate: ``builtin/permission.py:580-585`` returns ``None``
(allow) for an AUTO_ACCEPT in-cwd write roughly thirty lines ABOVE the
``if not ctx.has_ui:`` headless branch at ``:382-383``, so the child-only
headless floor (``PermissionExtension.headless_default``) never runs for
exactly the writes that matter. The posture the child is LAUNCHED with is
therefore the real guarantee, and it is computed here, in the parent.
"""

from __future__ import annotations

from aelix_coding_agent.builtin.permission_mode import PermissionMode

# The total order the clamp is taken over: PLAN (tightest) → YOLO (loosest).
# It is a rank map rather than the shipped ``CYCLE_ORDER``
# (``builtin/permission_mode.py:71-77``) on purpose — the cycle is a UX
# rotation (DEFAULT → AUTO_ACCEPT → PLAN → YOLO → AUTO) and carries no
# authority ordering at all. Reusing it here would silently rank PLAN above
# AUTO_ACCEPT.
_RANK: dict[PermissionMode, int] = {
    PermissionMode.PLAN: 0,
    PermissionMode.DEFAULT: 1,
    PermissionMode.AUTO_ACCEPT: 2,
    PermissionMode.AUTO: 3,
    PermissionMode.YOLO: 4,
}

# ``inherit`` (and ``ask`` WITH a live parent UI) baseline. DEFAULT collapses to
# PLAN because "prompt" is meaningless in a process with no prompt channel; AUTO
# collapses to AUTO_ACCEPT because AUTO's extra authority is bash classification
# (ADR-0158) and a child must not run classified shell commands unattended.
_INHERIT_BASELINE: dict[PermissionMode, PermissionMode] = {
    PermissionMode.DEFAULT: PermissionMode.PLAN,
    PermissionMode.PLAN: PermissionMode.PLAN,
    PermissionMode.AUTO_ACCEPT: PermissionMode.AUTO_ACCEPT,
    PermissionMode.AUTO: PermissionMode.AUTO_ACCEPT,
    PermissionMode.YOLO: PermissionMode.YOLO,
}


def posture_rank(mode: PermissionMode) -> int:
    """Authority rank of ``mode`` — 0 (PLAN) to 4 (YOLO).

    Exported so :mod:`aelix_agents.consent` can decide whether offering a
    widening option would be a no-op (ADR-0197 §(i) constraint 4) without
    reaching into this module's private table.
    """

    return _RANK[mode]


# The lowest posture at which a DELEGATED CHILD can mutate something WITHOUT
# asking anybody. Not a taste judgement — walked off ``_on_tool_call``
# (``builtin/permission.py``) for a child, i.e. ``ctx.has_ui = False`` and
# ``headless_default = "block"`` (the two settings ``cli/entry.py`` gives a
# process with ``subagent_depth() > 0``). Branch letters are the ladder's own:
#
#   PLAN         branch (b) blocks EVERY mutating tool before the read-only
#                short-circuit and before the headless branch — so it holds on
#                the non-interactive path too.                    -> no authority
#   DEFAULT      no branch above (d) matches, so control reaches the headless
#                branch and ``headless_default="block"`` blocks. DEFAULT means
#                "ask", the child has no channel to ask through, and asking is
#                what is blocked.                                 -> no authority
#   AUTO_ACCEPT  branch (f) ``return None`` — an in-cwd, non-sensitive write is
#                ALLOWED, ~30 lines above the headless branch, which is finding
#                B4 exactly.                                      -> AUTHORITY
#   AUTO         branch (g) does the same for writes, and additionally allows a
#                bash command the classifier rates ALLOW.         -> AUTHORITY
#   YOLO         branch (e) ``return None`` for every mutating tool.
#                                                                 -> AUTHORITY
#
# Pinned against the REAL ``PermissionExtension`` by
# ``tests/agents_ext/test_posture_clamp.py::test_write_authority_matches_the_real_permission_ladder``
# — if the ladder is ever reordered, that test fails here rather than silently
# changing which delegations are shown to a human.
_WRITE_AUTHORITY_FLOOR = PermissionMode.AUTO_ACCEPT


def grants_write_authority(mode: PermissionMode) -> bool:
    """Can a child launched at ``mode`` mutate anything WITHOUT asking?

    The question ADR-0197 §(i)'s consent rule is keyed on: the dialog fires only
    when write authority is actually at stake. Expressed against the rank ladder
    rather than as a set literal so a future :class:`PermissionMode` slotted
    between ``DEFAULT`` and ``AUTO_ACCEPT`` inherits the safe answer, and a
    future one above ``AUTO_ACCEPT`` inherits the dangerous one — which is the
    direction that must never be guessed wrong.

    DELIBERATELY NOT ``mode is PermissionMode.PLAN``. The clamp
    (:func:`child_permission_mode`) tightens a clamped ``DEFAULT`` to ``PLAN``
    today, so the two spellings agree on every value the clamp can currently
    produce — but they are different QUESTIONS, and only this one survives that
    tightening being relaxed. ``DEFAULT`` belongs on the no-authority side on
    its own merits: see :data:`_WRITE_AUTHORITY_FLOOR`.
    """

    return _RANK[mode] >= _RANK[_WRITE_AUTHORITY_FLOOR]


# Which ``approval_mode`` values are a DECLARATION that the profile needs write
# authority — ADR-0197 §(i), owner amendment 2026-07-27. This is the whole
# vocabulary of ``agents/profile.py:84``'s ``_APPROVAL_MODES``, and every value
# is justified here rather than left to the reader:
#
#   "auto"    DECLARES. The profile literally asks for its writes to be
#             auto-approved. There is no other reading of the value.
#   "ask"     DECLARES. The author asked for a human decision AT SPAWN TIME, and
#             the only decision this dialog offers is how much authority to
#             grant — so ``ask`` is a request for exactly that question to be
#             put to a human. It must stay on this side or the value becomes
#             INERT: its clamp under a ``default`` parent is ``plan``, so
#             without the declaration the §(i) early-out would suppress the very
#             dialog ``ask`` exists to open, making it indistinguishable from
#             ``inherit`` and re-opening the "validated, not read" deferral at
#             ``agents/profile.py:239-243`` that finding OC-8 closed.
#   "inherit" DOES NOT. It asks for no more authority than the parent already
#             has. When the parent IS loose the clamp itself grants write
#             authority and the dialog fires through
#             :func:`grants_write_authority` — never through the widening
#             option — so nothing is lost by refusing to volunteer one here.
#             This is the owner's headline case: an ordinary read-only
#             delegation renders no dialog at all.
#   "deny"    DOES NOT, and MUST NOT — it is the explicit opposite. Its clamp is
#             ``PLAN`` for every parent, and a dialog offering to widen it would
#             contradict the file it was read from.
#
# ALLOW-LIST, not a deny-list: ``child_permission_mode`` takes a bare ``str``,
# and anything unrecognised (a future value, a caller that skipped
# ``parse_profile``'s validation) declares nothing and cannot widen.
_DECLARES_WRITE_AUTHORITY: frozenset[str] = frozenset({"auto", "ask"})


def declares_write_authority(approval_mode: str) -> bool:
    """Does the PROFILE itself declare that it needs write authority?

    The owner's rule (ADR-0197 §(i), 2026-07-27): the spawn-time dialog offers
    the bounded widening option ONLY when the profile declared it needs write
    authority. A human can no longer opportunistically upgrade a non-declaring
    profile at spawn time; the way to grant writes is to edit the profile, which
    is a reviewable — and, under ADR-0189, signable — artifact.

    Consumed by :func:`aelix_agents.consent._may_widen` (the dialog) and, through
    :func:`aelix_agents.consent.consent_is_required`, by
    ``AgentsExtension._grant_for`` (the model door's pre-filter), so both doors
    read this one table. See :data:`_DECLARES_WRITE_AUTHORITY` for the
    per-value justification.
    """

    return approval_mode in _DECLARES_WRITE_AUTHORITY


def child_permission_mode(
    approval_mode: str, parent: PermissionMode, scope: str, *, has_ui: bool = False
) -> PermissionMode:
    """The FLOOR posture a child runs under — ADR-0197 §(e).

    Never returns a mode looser than ``parent``. This is the floor only: the
    spawn-time consent dialog (§2i) may raise the RESULT one notch, to at most
    ``AUTO_ACCEPT``, and only on an explicit human answer.

    ``has_ui`` defaults to False — the conservative direction. It exists solely
    for ``approval_mode == "ask"``: with a live parent UI, ``ask`` produces the
    ``inherit`` baseline that the dialog then offers to raise; with no UI there
    is nobody to ask, so it collapses to PLAN exactly like ``deny``
    (finding OC-10 — ``ask`` no longer refuses the spawn).

    ``scope`` is the profile's EFFECTIVE scope
    (``agents/profile.py:52`` — ``"bundled"`` / ``"user"`` / ``"project"`` / ``"explicit"``).
    Only the literal ``"project"`` triggers the widening ban; ``"explicit"``
    (``--agent-file``) is a path the human typed, so it is treated as
    user-authored.
    """

    if approval_mode == "deny":
        requested = PermissionMode.PLAN
    elif approval_mode == "auto":
        requested = PermissionMode.AUTO_ACCEPT
    elif approval_mode == "ask" and not has_ui:
        requested = PermissionMode.PLAN
    else:  # "inherit", or "ask" WITH a live UI (the dialog's baseline)
        requested = _INHERIT_BASELINE[parent]
    # SECURITY (B4): a PROJECT-scoped profile may never WIDEN. The repo does not
    # get to grant itself consent; ``auto`` from a project file collapses to the
    # ``inherit`` result.
    #
    # CORRECTION (OC-6): this is a rank-MIN, not an assignment. Executing the
    # previous ``requested = fallback`` form gave an ``auto``/YOLO-parent
    # project profile ``yolo`` while the same user-scope profile got
    # ``auto-accept-edits`` — the ban made the checked-in file WIDER than the
    # user's own. Substitute only when the fallback is strictly tighter.
    #
    # HONEST NOTE (measured over all 5 parents during implementation): in its
    # corrected rank-min form this clause changes no answer, because ``auto``
    # requests AUTO_ACCEPT and AUTO_ACCEPT is already <= every parent loose
    # enough to widen. It is kept as a TESTED DEFENSIVE INVARIANT — it is the
    # thing that stays true if a future edit raises what ``auto`` may request.
    # The project-scope guarantee that actually bites today is §(i)'s absolute
    # "never offer widening for a project-scoped profile" dialog rule.
    if scope == "project" and approval_mode == "auto":
        fallback = child_permission_mode("inherit", parent, "user", has_ui=has_ui)
        if _RANK[fallback] < _RANK[requested]:
            requested = fallback
    # The clamp itself.
    clamped = requested if _RANK[requested] <= _RANK[parent] else parent
    # DEFAULT in a child means "prompt", and the child has no prompt. PLAN gives
    # the same denial with a model-actionable reason and does not depend on
    # ``headless_default`` being set correctly. Strictly tighter, never looser.
    return PermissionMode.PLAN if clamped is PermissionMode.DEFAULT else clamped


__all__ = [
    "child_permission_mode",
    "declares_write_authority",
    "grants_write_authority",
    "posture_rank",
]
