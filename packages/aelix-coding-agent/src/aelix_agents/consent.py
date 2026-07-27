"""Spawn-time consent — ADR-0197 §(i). The ONLY module here that prompts.

This is the single file in ``aelix_agents`` allowed to touch ``ctx.ui`` /
``ctx.has_ui``. It contains NO spawn code and NO argv code, and product-core
contains no copy of any of it: consent is extension policy, and the seam
Protocol in ``subagent_contract.py`` deliberately carries no grant parameter
(pinned by ``test_protocol_has_no_consent_parameter`` and
``test_product_core_never_prompts_for_spawn_consent``).

THE PROBLEM IS AN EMPTY GATE, NOT UX. Measured on the shipped ladder:
``_MUTATING`` (``builtin/permission.py:64``) is
``['bash','create_file','edit','execute_command','sh','shell','write','write_file']``
— ``"agent"`` is NOT in it, so an ``agent`` tool call falls into the
non-mutating branch at ``permission.py:324-325`` and is **silently allowed**.
Delegation is today the one action a model can take that starts a whole second
agent with real authority and asks nobody.

WHY NOT JUST ADD ``"agent"`` TO ``_MUTATING``. ``_rule_key``
(``permission.py:94-116``) falls through to an args-blind ``f"tool:{tool_name}"``
at ``:116``. One "allow for this session" would then approve EVERY profile
against EVERY task for the rest of the run. The gate has to live here, keyed on
what actually varies. ``builtin/permission.py`` is deliberately left alone.

WHY THE ``tool_call`` HOOK AND NOT ``execute()``. ``ToolExecutionContext``
(``aelix_ai/tools.py``) has four fields and no UI; and the kernel runs
``before_tool_call`` in the SEQUENTIAL prep phase (``loop.py:510-531``, driven
from ``:813-823``) while ``execute()`` runs under ``asyncio.gather``
(``loop.py:877``) with ``tool_execution = "parallel"`` by default
(``harness/core.py:247``). Two modals from one batch would collide on
``tui/chrome.py:518``'s single ``_modal`` slot — ``mount_modal`` overwrites
unconditionally, the first Future is orphaned, and the turn hangs. The hook
gives us the kernel's own serialisation for free; :data:`_CONSENT_LOCK` and the
tool's ``execution_mode="sequential"`` are the belt and braces.

WHEN THE DIALOG FIRES (owner decision, 2026-07-27). Not "every delegation with a
live UI" — only when write authority is at stake, which is :func:`consent_is_required`
and nothing else: ``grants_write_authority`` says the child could mutate
something without asking, OR :func:`_may_widen` says a human could grant it that.
A read-only, unwidenable delegation proceeds with no prompt, because
``build_options`` would render only ["Run read-only (plan)", "Cancel"] and a
confirmation with no real answer trains the human to dismiss the widget that also
carries the questions that matter. The predicate is DERIVED from
``builtin/permission.py``'s ladder (see :mod:`aelix_agents.posture`), never
spelled ``clamped is PLAN``.

AND THE WIDENING OPTION IS OFFERED ONLY TO A PROFILE THAT DECLARED IT NEEDS WRITE
AUTHORITY (same decision; :func:`_may_widen` constraint 0). ``approval_mode:
auto`` and ``ask`` declare; ``inherit`` and ``deny`` do not. So a read-only
delegation from an ordinary profile no longer prompts merely because a human
COULD have upgraded it — that upgrade is no longer on offer. A human cannot
opportunistically widen a non-declaring profile at spawn time; the way to grant
one writes is to edit the profile, which is a reviewable, signable artifact.
Residual R7 in ADR-0197 records the price: such a spawn is bounded by the clamp,
by the delegation caps, and by the statusline row — not by a human saying yes.

``ctx.has_ui`` IS TIME-VARYING — NEVER CACHE IT (finding OC-7). It is not a
mode. ``extensions/api.py:1062`` returns ``runtime.ui is not
HEADLESS_UI_CONTEXT`` (``:1082-1083``): ``False`` during
``harness.bootstrap()``, ``True`` after ``tui/shell.py`` binds the real UI,
re-pointed on every harness rebuild (``/new`` / ``/fork`` / ``/resume``), and
back to ``False`` on TUI exit. It is read here, live, immediately before
prompting, and nowhere else.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.builtin.permission_mode import PermissionMode

from aelix_agents.posture import (
    child_permission_mode,
    declares_write_authority,
    grants_write_authority,
    posture_rank,
)

if TYPE_CHECKING:
    from aelix_coding_agent.subagent_contract import ResolvedProfile

# ONE prompt at a time, process-wide. The kernel already serialises the
# ``tool_call`` hook (see the module docstring), so this only has to cover the
# second door — ``/agents run``, which is a REPL command — and any future
# caller that has not read this file. The precedent is
# ``builtin/permission.py:387``'s ``async with self._lock`` around its own
# modal. Module scope is correct: the resource being protected is the TUI's
# single ``_modal`` slot, which is also process-wide.
#
# Keyed by running loop rather than created once at import. ``asyncio.Lock``
# binds itself to the first loop that CONTENDS on it (``_LoopBoundMixin`` —
# the uncontended fast path never touches ``_get_loop``) and raises
# "bound to a different event loop" forever after. A process has exactly one
# loop, so in production this dict holds exactly one entry; a test session
# builds a fresh loop per test, and without this a single contended test would
# poison every later one.
_LOCK_BY_LOOP: dict[object, asyncio.Lock] = {}


def _consent_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _LOCK_BY_LOOP.get(loop)
    if lock is None:
        # One entry at a time — a retired loop's lock is unreachable and would
        # only be a leak.
        _LOCK_BY_LOOP.clear()
        lock = asyncio.Lock()
        _LOCK_BY_LOOP[loop] = lock
    return lock


TASK_PREVIEW_CHARS = 300
"""Budget for the model-authored task text in the dialog title (residual R2).

Two jobs. Prompt-injection: a long task can bury the real instruction below the
fold, so the human approves something they never read. And rendering: our
dialog is already taller than the shipped approval dialog, and
``tui/overlay.py``'s ``_CappedContainer`` clips rather than scrolls, so an
unbounded title can push the OPTIONS off screen — which would make Cancel
unreachable."""

CANCEL_OPTION = "Cancel"

# DO NOT ADD A "don't ask again for this profile this session" RUNG HERE.
#
# It was implemented once and REMOVED (P2 review, HIGH #2/#5). ADR-0197:613-616
# is verbatim: "No persistence, and no session memo. A grant is per-spawn: P2
# asks every time. Nothing is written to disk, and nothing is memoized for the
# session ... A per-session memo is P3", repeated in the ADR's *Deferred* list
# at :826 and published in ``docs/decisions/README.md``. Shipping the rung made
# the code and an accepted ADR disagree in the PERMISSIVE direction.
#
# It is also the wrong shape as drafted. Any memo can only be keyed on things
# that identify the DECISION, and the only key available at this layer is
# ``resolved.source_path`` — which is neither the task nor the cwd, both of
# which :func:`build_consent_title` puts on screen. Measured on the removed
# implementation with a parent at ``auto-accept-edits``: ONE dialog covered
# FOUR spawns, three of them in directories the MODEL chose, on tasks the MODEL
# wrote, unattended. That is verbatim the failure finding OC-1 exists to
# prevent. When the rung lands in P3 it must key on
# ``(profile, source_path, granted mode)`` AND re-show the dialog whenever the
# cwd or the resolved posture differs from the one that was on screen.


@dataclass(frozen=True)
class SpawnGrant:
    """One human decision about one spawn. Never persisted, never reused."""

    profile: str
    source_path: str
    scope: str
    mode: PermissionMode
    """The posture the child will ACTUALLY run under."""
    widened: bool
    """True only when a human lifted it above the clamp."""
    consented: bool
    """False = declined / Esc / no answer / the dialog itself failed."""


def _truncate_task(task: str, limit: int = TASK_PREVIEW_CHARS) -> str:
    """Flatten and bound the model-authored task text.

    Whitespace is collapsed before truncating: the character budget alone does
    not bound the dialog's HEIGHT, and a task consisting of 300 newlines would
    otherwise render as a 300-row modal. Both properties matter — see
    :data:`TASK_PREVIEW_CHARS`.
    """

    flat = " ".join(task.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _may_widen(resolved: ResolvedProfile, clamped: PermissionMode, *, has_ui: bool) -> bool:
    """The constraints on bounded widening (finding OC-3, owner amendment).

    Widening is offered IFF all of them hold:

    0. THE PROFILE DECLARES IT NEEDS WRITE AUTHORITY —
       :func:`~aelix_agents.posture.declares_write_authority` of
       ``resolved.profile.approval_mode`` (owner decision, 2026-07-27). This
       constraint is numbered 0 because it is of a different kind from the five
       below: they BOUND how far a widening may go, this one decides whether the
       option exists at all. ``auto`` and ``ask`` declare; ``inherit`` and
       ``deny`` do not, and ``deny`` must never widen — the per-value reasoning
       lives beside :data:`~aelix_agents.posture._DECLARES_WRITE_AUTHORITY`, in
       the module that owns the vocabulary.

       Authority follows DECLARED intent. A human may no longer opportunistically
       upgrade a non-declaring profile at spawn time; the way to grant a profile
       writes is to edit the profile, which is a reviewable (ADR-0189: signable)
       artifact rather than a decision taken under a modal in the middle of a
       turn. The price is stated as residual R7 and not hidden: an ordinary
       read-only delegation now runs with no per-spawn human confirmation.

       Note what this does NOT touch. A clamp that ALREADY grants write authority
       — the shift+tab case finding OC-1 exists for — never reached this function
       in the first place: :func:`consent_is_required` asks
       ``grants_write_authority`` about it directly, and that half of the rule is
       unchanged.
    1. ``resolved.scope != "project"``. A project-scoped profile can NEVER be
       widened, dialog or no dialog. This preserves finding B4 verbatim: the
       vulnerability was *a repo file widening silently*, and a human answering
       a modal is not that. Note the deliberate asymmetry with §(f), which
       refuses project-scoped IDENTITIES on the model-driven door outright —
       that door is stricter still, because there the model chose the name.
    2. ``has_ui`` is True AT THE MOMENT OF THE PROMPT (live read, OC-7).
    3. The ceiling is EXACTLY ``AUTO_ACCEPT``. ``AUTO`` and ``YOLO`` are never
       offered by any dialog, for any parent posture — so bash in the child is
       still gated by the child's own ladder even after a human says yes.
    4. It would not be a no-op: ``AUTO_ACCEPT`` must be strictly looser than
       ``clamped``, or the option is a lie.
    5. (Structural, not checked here.) ``.aelix`` is in
       ``_SENSITIVE_DIR_COMPONENTS`` (``builtin/permission.py``, finding OC-5),
       AND ``_is_auto_allowable_write`` judges the SYMLINK-RESOLVED target as
       well as the lexical one (P2 review, HIGH #1 — before that fix a
       checked-in ``docs -> .aelix`` symlink walked straight through the
       component check, measured end-to-end with a real widened child that
       landed ``.aelix/agents/pwned.md`` and was discovered by the next run).
       Shipping widening without BOTH halves is what would let a writable child
       author the parent's NEXT identity or project extension — a write-to-exec
       escalation that ``--no-approve`` cannot touch, because it only stops a
       child LOADING such a file, never writing one.
    """

    if not declares_write_authority(resolved.profile.approval_mode):
        return False
    if resolved.scope == "project":
        return False
    if not has_ui:
        return False
    return posture_rank(PermissionMode.AUTO_ACCEPT) > posture_rank(clamped)


def consent_is_required(
    resolved: ResolvedProfile, clamped: PermissionMode, *, has_ui: bool
) -> bool:
    """Is there anything to ask a human about this spawn? THE SINGLE SOURCE OF TRUTH.

    Both doors call exactly this function, and neither may re-derive it:
    :func:`request_spawn_consent` (which serves ``/agents run`` and, through the
    hook, the model door) and ``AgentsExtension._grant_for``'s pre-filter. The
    two were previously spelled differently — the pre-filter read
    ``not grants_write_authority(want) and approval_mode != "ask"`` while the
    dialog read ``grants_write_authority(clamped) or _may_widen(...)`` — and
    agreeing "by inspection" is exactly how a gate drifts open on one door.

    Two disjuncts, and they answer different questions:

    * :func:`~aelix_agents.posture.grants_write_authority` — the child could
      MUTATE something without asking anybody. Unaffected by the 2026-07-27
      amendment; this is finding OC-1's shift+tab case and it always prompts.
    * :func:`_may_widen` — the child cannot, but a human COULD grant it that,
      because the profile DECLARED it needs write authority. Constraint 0.

    Everything else is a read-only child that no answer could change, and
    :func:`request_spawn_consent` returns the clamp for it without prompting.
    """

    return grants_write_authority(clamped) or _may_widen(
        resolved, clamped, has_ui=has_ui
    )


def build_consent_title(
    resolved: ResolvedProfile, task: str, clamped: PermissionMode, *, cwd: str
) -> str:
    """The whole dialog body, because there is nowhere else to put it.

    ``ExtensionUIDialogOptions`` (``extensions/ext_ui.py:55-67``) carries only
    ``signal`` and ``timeout`` — the extension-facing ``select``
    (``ext_ui.py:186-193``) has no body/detail parameter at all. So every piece
    of context rides the ``title`` string as a multi-line block.

    ``resolved.source_path`` is ALWAYS shown and is deliberately the second
    line: the task text below it is model-authored and therefore injectable,
    whereas the path is a human-owned file the user can go and read. It is the
    only line in this dialog the model cannot influence.
    """

    return "\n".join(
        [
            f"Delegate to agent '{resolved.name}'?",
            "",
            f"Profile:    {resolved.name} ({resolved.scope} scope)",
            f"Source:     {resolved.source_path}",
            f"Directory:  {cwd}",
            f"Permission: {clamped.value}",
            "",
            "Task (written by the model, not by you):",
            _truncate_task(task),
        ]
    )


def _baseline_label(clamped: PermissionMode) -> str:
    """Label for the first option — the grant that takes the clamp unchanged.

    CORRECTION to the drafted copy, and it matters. The draft read
    ``f"Run read-only ({clamped.value})"`` unconditionally, which is accurate
    only when the clamp IS ``plan`` — the DEFAULT-parent case, i.e. the common
    one. But under an AUTO_ACCEPT / AUTO / YOLO parent with ``approval_mode:
    inherit`` the clamp is that same posture, and a button reading "Run
    read-only" that actually grants ``yolo`` would be the single most dangerous
    string in this dialog: it invites a human to approve unattended shell
    execution while believing they approved a reader.

    The wording is therefore kept verbatim exactly where it is true, and states
    the real posture everywhere else. ``clamped.value`` is in both branches, so
    the granted authority is always on screen either way.
    """

    if clamped is PermissionMode.PLAN:
        return f"Run read-only ({clamped.value})"
    return f"Run with the inherited posture ({clamped.value})"


def build_options(clamped: PermissionMode, *, may_widen: bool) -> list[str]:
    """The dialog's options, in order. Cancel is always last and always present.

    Two or three options, never four. Order is load-bearing: the baseline first
    (the safe answer is the default landing spot), the one permitted escalation
    second, Cancel last. There is deliberately no "don't ask again" rung — see
    the block comment beside :data:`CANCEL_OPTION`.
    """

    options = [_baseline_label(clamped)]
    if may_widen:
        options.append(
            f"Allow file edits for this run ({PermissionMode.AUTO_ACCEPT.value})"
        )
    options.append(CANCEL_OPTION)
    return options


async def _ask(ui: Any, title: str, options: list[str]) -> str | None:
    """Run the modal. ANY failure is a decline.

    Deny-on-error is the whole point of this wrapper. ``select`` can raise
    ``NotImplementedError`` (the headless binding — reachable if ``has_ui``
    flipped between the check and the call, which it genuinely can: the UI is
    re-bound on every harness rebuild), or anything a TUI implementation
    chooses to raise. A refusal that turns into an ALLOW because a dialog threw
    is the single worst failure mode this file can have.

    ``BaseException`` is deliberately NOT caught: a ``CancelledError`` means the
    turn is being aborted, and the correct response to that is to propagate, not
    to synthesise a decision the human never made.
    """

    try:
        answer = await ui.select(title, options)
    except Exception:  # noqa: BLE001 — deny on error, never allow
        return None
    if not isinstance(answer, str):
        # ``None`` is Esc (``tui/context.py:255-262``). Anything else is a
        # misbehaving implementation, and it is treated identically.
        return None
    return answer


async def request_spawn_consent(
    ctx: Any,
    resolved: ResolvedProfile,
    task: str,
    parent: PermissionMode,
    *,
    cwd: str,
) -> SpawnGrant:
    """Take one human decision about one spawn. Never raises, never spawns.

    ``ctx`` is the :class:`ExtensionContext` — typed ``Any`` so this module does
    not import the extension API just to name it, and because the only two
    members it uses (``has_ui``, ``ui``) are read reflectively anyway.

    P2 ASKS EVERY TIME. There is no session memo and no persistence: the grant
    this returns is spent by exactly one spawn and nothing about it survives.
    See the block comment beside :data:`CANCEL_OPTION` for why the rung that was
    briefly implemented here was removed, and ADR-0197:613-616 for the contract.

    THE DIALOG FIRES ONLY WHEN WRITE AUTHORITY IS AT STAKE (owner decision,
    2026-07-27; ADR-0197 §(i)). A delegation that is read-only AND cannot be
    widened proceeds with no prompt at all. The reasoning is the one this whole
    module is built around: a modal that appears when there is no real choice
    trains click-through, and the habit it trains then endangers the prompts
    that DO matter — the write-capable spawn and the widenable one, which are
    rendered by this same widget. "Read-only" is asked of
    :func:`~aelix_agents.posture.grants_write_authority`, which is walked off
    ``builtin/permission.py``'s real ladder, NOT of ``clamped is PLAN``.

    AND "COULD BE WIDENED" IS ASKED OF THE PROFILE, NOT OF THE HUMAN (owner
    decision, 2026-07-27, constraint 0). The widening option is offered only when
    the profile itself DECLARED it needs write authority — ``approval_mode:
    auto`` or ``ask``. A scout with no ``approval_mode`` under a ``default``
    parent therefore renders NO dialog and spawns read-only, where it previously
    rendered one purely because a human could theoretically have upgraded it.
    Authority follows declared intent; the way to give a profile writes is to
    edit the profile.

    Flow:

    1. Compute the clamp with a LIVE ``ctx.has_ui`` (OC-7). ``has_ui`` feeds the
       clamp because ``approval_mode: ask`` means "ask the human", and whether
       there IS a human is precisely what this value answers.
    2. NO LIVE UI → return the clamp, consented, un-widened, WITHOUT prompting.
       There is nobody to ask, so widening never happens headlessly and no new
       refusal path appears: ``-p`` / ``--mode json`` / RPC delegation keeps
       working exactly as §(e) describes. Headless is a silent DOWNGRADE, never
       a refusal.
    3. NOTHING AT STAKE (:func:`consent_is_required` is False) → return the
       clamp, consented, un-widened, WITHOUT prompting. The child cannot mutate
       and no answer could change that.
    4. Otherwise → recompute ``may_widen``, build the options and prompt, under
       the process-wide lock.

    RESIDUAL, stated rather than hidden. Step 3 means a read-only delegation now
    happens with NO per-spawn human confirmation. What bounds it instead: the
    clamp itself (a ``plan`` child is refused every mutating tool at branch (b)
    of the child's own ladder, headless or not),
    :data:`~aelix_agents.runtime.MAX_DELEGATIONS_PER_PROMPT` and
    :data:`~aelix_agents.runtime.MAX_LIVE_CHILDREN`, and the fact that the run
    is VISIBLE while it happens — the statusline row and the ``subagent_*``
    events (§(l)). It is a confirmation that was removed, not an audit trail.

    Fail-closed by construction, and by ALLOW-LIST rather than by deny-list:
    only an answer that IS one of the strings :func:`build_options` rendered can
    consent. Esc gives ``None``, ``Cancel`` is explicit, an exception inside the
    dialog gives ``None`` via :func:`_ask`, and anything else — a ``str`` from a
    misbehaving ``ctx.ui`` implementation, an option from a stale render — is a
    decline. See the comment at the branch itself for what the deny-list form
    measured.
    """

    # LIVE read — once, here, and never stored on self. See the module
    # docstring: this value flips during bootstrap and again on TUI exit.
    has_ui = bool(getattr(ctx, "has_ui", False))
    clamped = child_permission_mode(
        resolved.profile.approval_mode, parent, resolved.scope, has_ui=has_ui
    )

    def _grant(
        mode: PermissionMode, *, widened: bool, consented: bool
    ) -> SpawnGrant:
        return SpawnGrant(
            profile=resolved.name,
            source_path=resolved.source_path,
            scope=resolved.scope,
            mode=mode,
            widened=widened,
            consented=consented,
        )

    if not has_ui:
        return _grant(clamped, widened=False, consented=True)

    if not consent_is_required(resolved, clamped, has_ui=has_ui):
        # NOTHING IS AT STAKE — no dialog. The child cannot mutate anything
        # (:func:`grants_write_authority`, walked off the real ladder) and no
        # answer could change that — either the profile never declared it needs
        # write authority (constraint 0), or it did and something else in
        # :func:`_may_widen` forbids the option. ``build_options`` would render
        # exactly ["Run read-only (plan)", "Cancel"]: a modal whose only real
        # answer is "yes". Showing it teaches the human to dismiss THIS dialog,
        # which is the same widget that asks the question that does matter — the
        # write-capable and the widenable cases below.
        #
        # The grant is identical to the one the baseline option would have
        # produced, which is the point: skipping the prompt changes what the
        # human is asked, never what the child gets.
        return _grant(clamped, widened=False, consented=True)
    # ORDER IS THE RULE (see step 3 of the docstring). The early-out above
    # consults ``_may_widen`` through :func:`consent_is_required`, so a read-only
    # clamp on a DECLARING profile — which a human could lift — still opens the
    # dialog. Recomputed here (both functions are pure) rather than threaded
    # through a flag, so there is exactly one spelling of the widening rule.
    may_widen = _may_widen(resolved, clamped, has_ui=has_ui)
    options = build_options(clamped, may_widen=may_widen)
    title = build_consent_title(resolved, task, clamped, cwd=cwd)

    async with _consent_lock():
        answer = await _ask(ctx.ui, title, options)

    # ALLOW-LIST, not a deny-list, and that ordering is the whole gate (P2
    # review, MEDIUM #1). The earlier form tested only ``None`` and
    # ``CANCEL_OPTION`` and let every other ``str`` fall through to the grant at
    # the bottom — so an answer matching NO offered option was CONSENT. Measured
    # on the shipped wiring with the answer ``"<<< user pressed something weird
    # >>>"``::
    #
    #     parent=yolo               -> consented=True mode=yolo
    #     parent=auto               -> consented=True mode=auto-accept-edits
    #     parent=auto-accept-edits  -> consented=True mode=auto-accept-edits
    #
    # i.e. a write- and bash-capable child started from a string nobody was ever
    # shown. Not reachable through the shipped TUI (``tui/context.py::select``
    # returns ``items[idx][1]`` or ``None``), but ``ctx.ui`` is a public Protocol
    # seam (``extensions/ext_ui.py:186-193``) that ``bind_ui`` lets any host or
    # extension supply — the planned Web UI is exactly such a host — and this
    # module's whole contract is that ANY unexpected input is a decline. It is
    # the same rule :func:`_ask` already applies to exceptions and to non-``str``
    # answers; there is no reason for a wrong ``str`` to be treated better than a
    # wrong type.
    if answer not in options or answer == CANCEL_OPTION:
        return _grant(clamped, widened=False, consented=False)
    # Identity comparison against the rendered option, not a substring match:
    # the option strings are built right here, and a ``"auto-accept" in answer``
    # test would be steerable by a profile named "auto-accept-edits". The
    # comparison is additionally gated on the option having been OFFERED, so an
    # answer nobody was shown can never take the widening branch.
    widened = may_widen and answer == options[1]
    return _grant(
        PermissionMode.AUTO_ACCEPT if widened else clamped,
        widened=widened,
        consented=True,
    )


__all__ = [
    "CANCEL_OPTION",
    "SpawnGrant",
    "TASK_PREVIEW_CHARS",
    "build_consent_title",
    "build_options",
    "consent_is_required",
    "request_spawn_consent",
]
