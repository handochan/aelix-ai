"""Spawn-time consent — ADR-0197 §(i). The ONLY module here that prompts.

This is the single file in ``aelix_agents`` allowed to touch ``ctx.ui`` /
``ctx.has_ui``. It contains NO spawn code and NO argv code, and product-core
contains no copy of any of it: consent is extension policy, and the seam
Protocol in ``subagent_contract.py`` deliberately carries no grant parameter
(pinned by ``test_protocol_has_no_consent_parameter`` and
``test_product_core_never_prompts_for_spawn_consent``).

THE PROBLEM IS AN EMPTY GATE, NOT UX. Measured on the shipped ladder:
``_MUTATING`` (``builtin/permission.py:77``) is
``['bash','create_file','edit','execute_command','sh','shell','write','write_file']``
— ``"agent"`` is NOT in it, so an ``agent`` tool call falls into the
non-mutating branch at ``permission.py:556-557`` and is **silently allowed**.
Delegation is today the one action a model can take that starts a whole second
agent with real authority and asks nobody.

WHY NOT JUST ADD ``"agent"`` TO ``_MUTATING``. ``_rule_key``
(``permission.py:222-244``) falls through to an args-blind ``f"tool:{tool_name}"``
at ``:116``. One "allow for this session" would then approve EVERY profile
against EVERY task for the rest of the run. The gate has to live here, keyed on
what actually varies. ``builtin/permission.py`` is deliberately left alone.

WHY THE ``tool_call`` HOOK AND NOT ``execute()``. ``ToolExecutionContext``
(``aelix_ai/tools.py``) has four fields and no UI; and the kernel runs
``before_tool_call`` in the SEQUENTIAL prep phase (``loop.py:521-542``, driven
from ``:813-823``) while ``execute()`` runs under ``asyncio.gather``
(``loop.py:900``) with ``tool_execution = "parallel"`` by default
(``harness/core.py:271``). Two modals from one batch would collide on
``tui/chrome.py:524``'s single ``_modal`` slot — ``mount_modal`` overwrites
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

ONE DIALOG PER TOOL CALL, RENDERING THE WHOLE BATCH (P3, decision S4). A
``mode="parallel"`` / ``mode="chain"`` call carries up to
``tool.MAX_PARALLEL_TASKS`` tasks and takes exactly ONE grant, which every member
then runs under. That is not the session memo this file removed (see the block
comment below :data:`CANCEL_OPTION`): every member is inside the single tool call
the hook already validated and every member's task is on screen, whereas the memo
covered LATER calls whose tasks and directories nobody had seen. The price is
that the dialog now has a HEIGHT BUDGET it can exceed — ``ctx.ui.select``
composes title and options into one non-wrapping, non-scrolling ``Window``
(``tui/context.py:140-218``, ``:475-478``) and ``tui/overlay.py``'s
``_CappedContainer`` bottom-truncates it (``overlay.py:221-231``), so a tall
enough batch would push ``Cancel`` off screen. :func:`batch_dialog_fits` measures
the composition against the live terminal and a call that would not fit is
REFUSED rather than rendered half-way; see §3.7 of the P3 plan.

``ctx.has_ui`` IS TIME-VARYING — NEVER CACHE IT (finding OC-7). It is not a
mode. ``extensions/api.py:1154-1155`` returns ``runtime.ui is not
HEADLESS_UI_CONTEXT`` (``:1082-1083``): ``False`` during
``harness.bootstrap()``, ``True`` after ``tui/shell.py`` binds the real UI,
re-pointed on every harness rebuild (``/new`` / ``/fork`` / ``/resume``), and
back to ``False`` on TUI exit. It is read here, live, immediately before
prompting, and nowhere else.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
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
    from aelix_coding_agent.subagent_contract import ResolvedProfile, SubagentMode

# ONE prompt at a time, process-wide. The kernel already serialises the
# ``tool_call`` hook (see the module docstring), so this only has to cover the
# second door — ``/agents run``, which is a REPL command — and any future
# caller that has not read this file. The precedent is
# ``builtin/permission.py:626``'s ``async with self._lock`` around its own
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

BATCH_TASK_PREVIEW_CHARS = 72
"""Per-member task budget in a MULTI-task dialog (P3 §3.7).

Deliberately NOT :data:`TASK_PREVIEW_CHARS`, and deliberately smaller. The modal
does not wrap (``tui/context.py:475-478`` builds its ``Window`` with
``wrap_lines`` left at its default ``False``), so on an 80-column terminal a
300-character preview would render as ONE row of which about 72 characters are
visible and the remainder is INVISIBLY clipped — the silent drop S4 forbids, and
it would also make the row count no longer equal the member count. 72 is one
visible row at 80 columns once the ``[k/N] `` prefix is paid for, so what is on
screen and what was budgeted are the same thing. This is a deliberate amendment
to ADR-0197 residual R2's "truncate the task to 300 characters" for multi-task
dialogs; single-task dialogs keep 300 unchanged."""

BATCH_HEADER_ROWS = 6
"""Rows a batch title spends before the first task row (P3 §3.7).

The heading, ``Profile:``, ``Source:``, ``Directory:``, ``Permission:`` and the
``Tasks (…)`` label — see :func:`build_batch_consent_title`, which is the only
producer of them and whose row count is pinned against this number by
``test_batch_consent.py``. The single-task body's two blank spacer rows are
dropped for batches: they buy two members, and a batch is the case where rows are
scarce. ``Source:`` keeps a row of its own — it is the one line the model cannot
influence (ADR-0197 residual R2 mitigation 1), and folding it in beside
``Profile:`` would risk it being clipped HORIZONTALLY instead.

``mode="chain"`` spends one more (§3.1.1 mitigation 2), and so does a spawn whose
model is knowable (``Model:``). BOTH are charged by :func:`_extra_header_rows`,
which both the renderer and :func:`batch_dialog_fits` call so the measured
composition and the rendered one cannot drift. They are conditional rather than
folded into this constant on purpose: a composition that draws neither must cost
neither, or a dialog with no model to name silently loses a member to a row it
never shows."""

MIN_TERMINAL_ROWS = 24
"""The terminal height assumed when the real one cannot be measured (P3 §3.7).

The POSIX default and the height of a split tmux pane, i.e. the smallest size a
real user is plausibly at — and the size at which the composition first clips.
Used as the ``fallback`` of :func:`shutil.get_terminal_size` and as the floor for
a degenerate measurement, so an unmeasurable terminal is treated as a SMALL one
(fewer members admitted), never as an unbounded one."""

_RESERVE_ESTIMATE = 6
"""Rows ``tui/overlay.py`` reserves BELOW the modal, as estimated from here.

``_modal_cap`` is ``max(_MODAL_MIN_HEIGHT, rows - _reserve_rows(chrome))``
(``overlay.py:135-151``) and ``_reserve_rows`` returns at least
``_MODAL_RESERVE_ROWS = 5`` (``overlay.py:56``), growing with a multi-line input
buffer and a multi-row footer (``overlay.py:159-194``). The chrome is not
reachable from an extension, so this is the shipped floor of 5 plus one row of
slack for the grown footer. Erring HIGH is the safe direction: it admits one
member fewer than the live cap would, never one more."""

_MIN_CAP = 3
"""``tui/overlay.py``'s ``_MODAL_MIN_HEIGHT`` (``overlay.py:60``).

The floor ``_modal_cap`` never goes below, so the arithmetic here has to have it
too. It matters only on an absurdly short terminal, where every batch is refused
anyway — which is the correct answer there."""

CANCEL_OPTION = "Cancel"

BATCH_TOO_TALL_REASON = (
    "Delegation refused, and the user was NOT asked: a consent dialog listing "
    "{n_tasks} tasks would not fit on the user's terminal, and this gate never "
    "shows a batch it cannot show in full. Nothing was started. {advice}"
)
"""What the model reads when :func:`batch_dialog_fits` says no (P3 §3.7).

Carried on :attr:`SpawnGrant.reason` so the ``tool_call`` hook can block the call
with THIS text instead of the "the user declined" text — the two are different
events and telling the model the user said no when the user was never asked would
be a lie it cannot act on. It is phrased as an instruction because the model can
actually fix it: the same work in smaller calls renders, and is consented, in
full."""

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
#
# P3 NOTE: A BATCH IS NOT A MEMO, and :func:`request_spawn_consent_batch` is not
# the rung above. A memo let a LATER tool call skip the dialog — its tasks and
# its cwd were chosen after the human had answered and were never on screen. A
# batch is ONE tool call, already validated by the hook and frozen into
# ``PendingSpawn`` (``tool.py:304-328``), whose every task and whose one cwd are
# rendered before the human answers — and if they cannot all be rendered, the
# call is REFUSED (:func:`batch_dialog_fits`) rather than partly shown. Nothing
# is memoised and the grant is still spent by exactly this one call.


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
    reason: str = ""
    """WHY a non-consented grant is non-consented, when it was not a human.

    Empty for every P2 path, including a real decline: an empty reason means "the
    user said no", which the caller already has its own copy for
    (``extension.py``'s ``_DECLINED``, whose "do not retry it" wording is only
    true of a human answer). It is set by exactly one branch —
    :func:`request_spawn_consent_batch` refusing a batch whose dialog would not
    fit (:data:`BATCH_TOO_TALL_REASON`) — where no dialog was shown at all and
    the model CAN act on the refusal by splitting the call.

    STILL ONE PROFILE, ONE MODE, ONE DECISION (S3): this adds a caption to the
    decision, not a second decision. Trailing and defaulted so every existing
    constructor — ``extension.py:785-792``, this module's own ``_grant`` — is
    unchanged."""

    disclosure: str = ""
    """What to TELL the user, when nobody was asked and the child can still write.

    Set only where :func:`disclosure_is_required` says so — the YOLO cells that
    stopped prompting on 2026-08-19 (#196). Empty everywhere else, including
    every read-only spawn that has always been silent and every spawn a human
    actually answered.

    It rides on the grant rather than being emitted here because BOTH doors
    reach a grant and only one of them reaches this module: ``_grant_for``'s
    pre-filter returns its own :class:`SpawnGrant` without calling
    :func:`request_spawn_consent_batch` at all, so a disclosure emitted inside
    this file would be skipped on the model door — the exact door that matters.
    One producer per grant, one consumer, nothing to keep in agreement."""


DIALOG_ROW_CHARS = 80
"""What "one visible row" means. The narrowest terminal §3.7 assumes.

THE BUDGET THAT MATTERS IS THE ROW'S, NOT THE FIELD'S. ``Profile:`` carries TWO
sanitised values plus fixed text, so per-field limits that each fit a row do not
compose into a row that fits. The per-field constants below are therefore chosen
so that every row they build stays inside this number, and
``test_batch_consent.py`` asserts the ROW rather than the fields."""

DIALOG_FIELD_CHARS = 68
"""Budget for a field that is ALONE on its row — ``Source:``, ``Directory:``.

:data:`DIALOG_ROW_CHARS` minus the 12-character label prefix
(``"Permission: "``, the widest). Deliberately not
:data:`BATCH_TASK_PREVIEW_CHARS` (72): that budget is paid after a ``[k/N] ``
prefix, this one after a longer label, and spelling them as one constant would
make one of the two rows wrong."""

_NAME_FIELD_CHARS = 40
"""Budget for ``resolved.name``, which SHARES a row and appears on two of them.

``Profile:    <name> (<scope> scope)`` is 12 + name + 2 + scope + 7, and
``Delegate 8 tasks to agent '<name>'?`` is 27 + name + 2; both fit
:data:`DIALOG_ROW_CHARS` at 40. It is not tighter because a name is a filename
stem the user chose and truncating a legitimate one hides which profile is being
approved — the identity question this dialog exists to answer."""

_SCOPE_FIELD_CHARS = 16
"""Budget for ``resolved.scope``, the other half of the ``Profile:`` row.

The shipped vocabulary is ``builtin`` / ``user`` / ``project`` (8 characters at
the widest), so this bounds a value that is in practice a literal — it is
sanitised and bounded at all only because ``ResolvedProfile`` is a plain
dataclass and nothing structurally stops a caller putting a filename in it."""

_CONTROL_CHARS = frozenset(
    chr(code)
    for code in [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)]
)
"""C0, DEL and C1 — every code point that can steer a terminal by itself.

C1 is in the set because ``\\x9b`` IS a CSI: prompt_toolkit's ``ANSI`` parser is
not the only consumer of these strings and a one-byte CSI is the same primitive
as ``\\x1b[``. ``\\n`` and the other whitespace controls are here too even though
:func:`_sanitize_field` collapses them first — the set is what makes
:func:`contains_control_chars` a complete predicate for
``print_channel.resolve_child_cwd``'s belt, and a partial one would be worse
than none."""

_CONTROL_KILL = dict.fromkeys(ord(char) for char in _CONTROL_CHARS)
"""``str.translate`` table that DELETES every control character.

Delete rather than escape: what survives ``\\x1b[8m`` is the inert literal
``[8m``, which renders as itself and is visibly odd — whereas an escaped
spelling would spend columns the one-row budget below does not have."""


def contains_control_chars(value: str) -> bool:
    """Does this string carry anything that can steer a terminal? (F1 belt)

    Exported so ``print_channel.resolve_child_cwd`` can refuse such a ``cwd``
    outright without owning a second spelling of the set. The dialog does not
    NEED that belt — every field it renders goes through :func:`_sanitize_field`
    — but a directory whose name is an escape sequence is not somewhere a
    delegated child should be asked to run in the first place, and the two
    defences fail independently.
    """

    return any(char in _CONTROL_CHARS for char in value)


def _sanitize_field(value: object, *, limit: int = DIALOG_FIELD_CHARS) -> str:
    """Make one interpolated value SAFE TO PUT IN THE DIALOG. (F1, CRITICAL)

    Every value this module interpolates is attacker-reachable. ``cwd`` is
    model-chosen and ``resolve_child_cwd`` (``print_channel.py:437-487``) validates
    only containment and is-a-directory — POSIX permits any byte but ``/`` and
    NUL in a path component — and ``resolved.name`` / ``resolved.source_path``
    come from a filename, which permits the same. ``ctx.ui.select`` then does two
    things with the composed title: it SPLITS IT ON ``\\n`` into rows and it
    ANSI-PARSES it (``tui/context.py:140-218``). A single directory of 150 bytes
    was demonstrated end-to-end to render a coherent, benign dialog — right
    ``Directory:``, right ``Permission: plan``, two innocuous task rows — while
    hiding the REAL permission row and the REAL tasks behind ``\\x1b[8m`` (SGR 8,
    which prompt_toolkit honours), so the human approved ``auto-accept-edits``
    over two tasks that were never on screen.

    Three properties, in this order, and all three are load-bearing:

    1. whitespace COLLAPSED (``" ".join(x.split())``, the same flattening
       :func:`_truncate_task` already does) — this is what makes
       :data:`BATCH_HEADER_ROWS` true BY CONSTRUCTION rather than by hope, and
       therefore what makes :func:`batch_dialog_fits` price the composition it
       will actually draw;
    2. control characters DELETED (:data:`_CONTROL_KILL`) — no ESC means no SGR,
       no hidden text and no cursor movement;
    3. bounded to :data:`DIALOG_FIELD_CHARS` — one VISIBLE row, so the row that
       was counted is the row that is read. The modal does not wrap
       (``tui/context.py:475-478``), so without this an over-long field is
       clipped invisibly.
    """

    flat = " ".join(str(value).split()).translate(_CONTROL_KILL)
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _sanitize_path(value: object, *, limit: int = DIALOG_FIELD_CHARS) -> str:
    """:func:`_sanitize_field` for a PATH — elides the MIDDLE, and that is security.

    Same three properties; only the truncation differs, and the difference is not
    cosmetic. The modal does not wrap (``tui/context.py:475-478``), so an
    over-long ``Directory:`` row is clipped by the terminal at column 80 WITH NO
    MARKER — the human sees a plausible prefix and cannot tell there is more.
    Containment (``print_channel.resolve_child_cwd``) only guarantees the child
    runs somewhere inside the parent tree; WHERE inside is the question this row
    exists to answer, and it is answered by the TAIL. A model asking for
    ``<50 characters of innocuous-looking directory>/.aelix`` would render, under
    plain clipping or under tail truncation, as the innocuous part alone.

    Eliding the middle keeps both ends inside a budget that fits one row, so the
    last component — the one being consented to — is always on screen and the
    ``…`` says out loud that something was dropped. The cost is that a genuinely
    long path is no longer shown in full; the alternative was showing its least
    informative half and calling it complete.
    """

    # Flattened and control-stripped exactly as :func:`_sanitize_field` does it
    # — the length test has to be applied to the RENDERED string, not to the
    # input, or a path padded with newlines would measure long and be elided
    # when it renders short.
    full = " ".join(str(value).split()).translate(_CONTROL_KILL)
    if len(full) <= limit:
        return full
    head = (limit - 1) // 2
    tail = limit - 1 - head
    return full[:head] + "…" + full[-tail:]


def _truncate_task(task: str, limit: int = TASK_PREVIEW_CHARS) -> str:
    """Flatten and bound the model-authored task text.

    Whitespace is collapsed before truncating: the character budget alone does
    not bound the dialog's HEIGHT, and a task consisting of 300 newlines would
    otherwise render as a 300-row modal. Both properties matter — see
    :data:`TASK_PREVIEW_CHARS`.

    Control characters are deleted for the reason :func:`_sanitize_field` states
    at length: ``str.split`` treats ``\\n`` as whitespace but NOT ``\\x1b``, so
    flattening alone would still let a model-authored task hide the rows below it
    behind SGR 8. The character budget is applied after the deletion, so the
    limit counts what is rendered.
    """

    return _sanitize_field(task, limit=limit)


_WIDENABLE_MODES: frozenset[str] = frozenset({"single", "parallel"})
"""Topologies whose dialog may offer the widening rung — constraint 6.

An ALLOW-LIST, like ``posture._DECLARES_WRITE_AUTHORITY`` and like this module's
answer check, and for the same reason: a mode this file has never heard of — a
future topology, a caller that skipped ``parse_agent_call`` — must inherit the
answer that grants nothing. A deny-list spelled ``mode != "chain"`` would hand
the widening rung to every topology added after this one by default."""


def _may_widen(
    resolved: ResolvedProfile,
    clamped: PermissionMode,
    *,
    has_ui: bool,
    mode: str = "single",
) -> bool:
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
    6. ``mode`` is in :data:`_WIDENABLE_MODES` — i.e. NOT ``"chain"`` (P3
       §3.1.1 mitigation 3). This one bounds the dialog by what the human can
       actually read. In a chain, step ``k >= 2``'s prompt is
       ``render_step(task_k, summary_{k-1})``: text minted MID-CALL by a child
       that has itself read ``cwd`` content an attacker may control, appended to
       the child's argv after the human answered. It is therefore not on screen
       and cannot be — no dialog can show text that does not exist yet.

       The asymmetry is exactly the one constraint 1 already applies to project
       scope: a human may CONFIRM authority that already exists, but this dialog
       may never MANUFACTURE write authority for a prompt a process will write
       later. A chain under an already write-capable parent still runs
       write-capable — that authority came from the human's own posture, and
       ``grants_write_authority`` (not this function) is what put it on screen.
       Cost in the common case is zero, because the common case is zero dialogs.
    """

    if mode not in _WIDENABLE_MODES:
        return False
    if not declares_write_authority(resolved.profile.approval_mode):
        return False
    if resolved.scope == "project":
        return False
    if not has_ui:
        return False
    return posture_rank(PermissionMode.AUTO_ACCEPT) > posture_rank(clamped)


def consent_is_required(
    resolved: ResolvedProfile,
    clamped: PermissionMode,
    parent: PermissionMode,
    *,
    has_ui: bool,
) -> bool:
    """Is there anything to ask a human about this spawn? THE SINGLE SOURCE OF TRUTH.

    Both doors call exactly this function, and neither may re-derive it:
    :func:`request_spawn_consent` (which serves ``/agents run`` and, through the
    hook, the model door) and ``AgentsExtension._grant_for``'s pre-filter. The
    two were previously spelled differently — the pre-filter read
    ``not grants_write_authority(want) and approval_mode != "ask"`` while the
    dialog read ``grants_write_authority(clamped) or _may_widen(...)`` — and
    agreeing "by inspection" is exactly how a gate drifts open on one door.

    A YOLO PARENT IS NOT ASKED (owner decision, 2026-08-19; #196). This
    REVERSES one cell of finding OC-1, whose reasoning was "shift+tab was
    consent to the PARENT's tool calls, with a tool card on screen — not consent
    to an unattended child process the model just chose". What changed is not
    the reasoning but the measurement of the widget it produced: under a YOLO
    parent ``build_options`` renders exactly two rows —
    ``["Run with the inherited posture (yolo)", "Cancel"]`` — the same arity and
    the same absence of a substantive second answer as the dialog the
    2026-07-27 amendment abolished, and ADR-0197's own test condemns it: "a
    dialog whose only substantive answer is 'yes' is not a gate; it is practice
    at dismissing gates". A YOLO user meets that widget on every delegation and
    nowhere else, because it is the only prompt YOLO does not suppress.

    ONLY YOLO, and the asymmetry is the point. ``auto-accept-edits`` says
    auto-accept EDITS and ``auto`` says route bash through the classifier;
    neither says "do not ask me about anything", so both keep prompting and the
    ``grants_write_authority`` half is untouched for them. YOLO's own docstring
    is unambiguous — "skip the permission PROMPT for all mutating tools ... YOLO
    bypasses the prompt, NOT the floor" — and this is the last prompt that was
    not skipped.

    WHAT THIS COSTS, STATED RATHER THAN HIDDEN. Measured: an
    ``auto-accept-edits`` or ``auto`` child is refused a write to ``.aelix/``
    and a ``yolo`` child is not, so a delegated YOLO child can author the
    parent's next identity or project extension. This dialog was the only
    moment in the product where a human learned BEFORE the fact that a
    ``yolo``-posture child was about to exist, and R7's accounting does not
    cover the gap: its largest bound is the clamp, and under YOLO the clamp
    bounds nothing. :func:`disclosure_is_required` is what replaces it — not a
    prompt, but not silence either.

    Everything else holds: the clamp, the guardrail floor inside the child, the
    delegation caps, the statusline row, the headless downgrade, the ceiling
    that no dialog ever hands out ``yolo``, and the project-scope widening ban.

    Two disjuncts, and they answer different questions:

    * :func:`~aelix_agents.posture.grants_write_authority` — the child could
      MUTATE something without asking anybody. Unaffected by the 2026-07-27
      amendment; this is finding OC-1's shift+tab case and it always prompts.
    * :func:`_may_widen` — the child cannot, but a human COULD grant it that,
      because the profile DECLARED it needs write authority. Constraint 0.

    Everything else is a read-only child that no answer could change, and
    :func:`request_spawn_consent` returns the clamp for it without prompting.

    IT TAKES NO ``mode``, AND THAT IS THE DECISION (P3 §3.1.1). ``_may_widen``'s
    constraint 6 removes the widening RUNG from a chain's dialog; it does not
    remove the DIALOG. Threading ``mode`` through here would do the latter — a
    declaring profile under a ``default`` parent clamps to ``plan``, so with
    ``mode="chain"`` both disjuncts would be False and a chain of eight
    model-written tasks, each of which will additionally be handed text minted by
    the previous child, would start with no human in the loop at all. The
    question this predicate asks is "could a human's answer change what runs",
    and for a chain it can: ``Cancel`` is a real answer here in a way it is not
    for a single read-only spawn, which is what the two-option early-out at
    :func:`request_spawn_consent` exists to suppress. So the chain dialog fires,
    and it offers exactly ["Run read-only (plan)", "Cancel"].
    """

    if parent is PermissionMode.YOLO:
        return False
    return grants_write_authority(clamped) or _may_widen(
        resolved, clamped, has_ui=has_ui
    )


def disclosure_is_required(parent: PermissionMode, clamped: PermissionMode) -> bool:
    """Was a write-capable spawn allowed through WITHOUT anyone being asked?

    Exactly the gap :func:`consent_is_required`'s YOLO early-out opens, and
    nothing else. Deliberately NOT "whenever the dialog did not fire": the
    common case — a read-only child from a profile that declared nothing — has
    always spawned silently and announcing it would put a line in the
    transcript on every delegation, which is the scrollback equivalent of the
    click-through trainer the dialog was removed for.

    ``grants_write_authority(clamped)`` rather than ``clamped is
    PermissionMode.YOLO`` because a YOLO parent with an ``approval_mode: auto``
    profile clamps to ``auto-accept-edits``, which is a child that can still
    write without asking; and because a ``deny`` profile clamps to ``plan``
    under any parent, which is a child that cannot, and never needed the
    dialog either.
    """

    return parent is PermissionMode.YOLO and grants_write_authority(clamped)


def build_disclosure_line(
    resolved: ResolvedProfile, clamped: PermissionMode, *, task_count: int
) -> str:
    """The one line that replaces the dialog. Never blocks, never asks.

    EVERY INTERPOLATED VALUE IS SANITISED, for the same reason
    :func:`build_consent_title` sanitises: the profile name comes from a
    filename and the path from disk, so both are strings an attacker can put
    ``\n`` and ``\x1b`` into, and this text lands in a Rich-rendered
    transcript. :func:`_sanitize_field` is the shared spelling.

    The task text is NOT here. It is model-authored, it is the longest and
    least trustworthy thing in the batch, and a one-line disclosure that wraps
    is a one-line disclosure that pushes the rest of the turn off screen. The
    COUNT is what a human needs to reconcile this line against what happens
    next.
    """

    plural = "" if task_count == 1 else "s"
    return (
        f"delegating to {_sanitize_field(resolved.name)} at {clamped.value} "
        f"— {task_count} task{plural}, from "
        f"{_sanitize_field(resolved.source_path)}"
    )


def build_consent_title(
    resolved: ResolvedProfile,
    task: str,
    clamped: PermissionMode,
    *,
    cwd: str,
    model: str | None = None,
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

    EVERY INTERPOLATED VALUE IS SANITISED (F1, CRITICAL — and this door is the
    P2 one, so the hole is pre-existing rather than a P3 regression). ``cwd`` is
    model-chosen and the profile fields come from a filename, so all four are
    strings an attacker can put ``\\n`` and ``\\x1b`` into; the composed title is
    newline-split into rows AND ANSI-parsed by ``ctx.ui.select``
    (``tui/context.py:140-218``). :func:`_sanitize_field` is what makes this a
    nine-row body for every input rather than for well-behaved ones — see its
    docstring for the demonstrated forgery.

    ``model`` is what the child will be LAUNCHED with (:func:`_model_row`), and
    it sits below ``Permission:`` rather than beside ``Profile:`` so that
    ``Source:`` keeps the second body row it was given on purpose. Omitted, and
    costing no row, whenever there is nothing to name.
    """

    rows = [
        f"Delegate to agent '{_sanitize_field(resolved.name, limit=_NAME_FIELD_CHARS)}'?",
        "",
        f"Profile:    {_sanitize_field(resolved.name, limit=_NAME_FIELD_CHARS)} "
        f"({_sanitize_field(resolved.scope, limit=_SCOPE_FIELD_CHARS)} scope)",
        f"Source:     {_sanitize_path(resolved.source_path)}",
        f"Directory:  {_sanitize_path(cwd)}",
        # ``clamped`` is a ``PermissionMode`` and its ``.value`` is a
        # product-core literal, so it is the one field here no attacker
        # reaches. Sanitised anyway: this row is the one the F1 forgery
        # imitated, and "every field on this screen went through one helper"
        # is a property worth being able to state without a caveat.
        f"Permission: {_sanitize_field(clamped.value)}",
    ]
    model_row = _model_row(model)
    if model_row is not None:
        rows.append(model_row)
    rows += [
        "",
        "Task (written by the model, not by you):",
        _truncate_task(task),
    ]
    return "\n".join(rows)


def _model_row(model: object) -> str | None:
    """The ``Model:`` row, or ``None`` when there is nothing to name.

    WHY THE DIALOG SAYS IT AT ALL. Approving a spawn is approving a process that
    will read this repository and — under a widened grant — write to it, and the
    model it runs on is both the price of that and, when a profile declares no
    ``model:``, a value the human never chose. Every other fact the child's
    identity is made of is already on this screen; the one that decides what
    actually answers was not, and a consent screen that cannot state it is asking
    for approval of something it has not described.

    NOT THE CHILD'S WORD — it does not exist yet. This is
    ``resolver.child_model_id``: the id that will be on the child's argv, which
    is the only thing knowable before the process exists. ``None`` whenever the
    child will run its own cascade instead, and then there is no row: a
    ``Model: unknown`` line would be a fact-shaped way of saying nothing.

    ONE FUNCTION, TWO CALLERS, and that is load-bearing rather than tidy. Both
    the renderers that EMIT this row and :func:`_extra_header_rows`, which PAYS
    for it, ask this — the same discipline the chain row already gets, for the
    same reason: a row emitted but not budgeted is a row that pushes ``Cancel``
    off the bottom of a short terminal. Sanitising here rather than at the two
    call sites is what makes "emitted" and "charged" the same predicate even for
    a value that sanitises to nothing.

    ``isinstance`` rather than truthiness: the value crosses the ``Any``-typed
    host seam, and a non-``str`` must cost the dialog a row, never a raise.
    """

    if not isinstance(model, str):
        return None
    flat = _sanitize_field(model, limit=DIALOG_FIELD_CHARS)
    return f"Model:      {flat}" if flat else None


def _reject_str_batch(tasks: object) -> None:
    """``str`` IS a ``Sequence[str]`` — the annotation is not the guard.

    Every batch entry point calls this FIRST, before ``len()`` (a ``str`` has
    one), before iteration (a ``str`` iterates into characters) and before any
    dialog. This is not defensive padding: an earlier draft of P3 re-typed
    :func:`request_spawn_consent`'s ``task`` parameter to ``Sequence[str]``, and
    because ``str`` satisfies that annotation, ``/agents run scout "review the
    auth module"`` (``runtime.py:547-550``, which passes a bare ``str``) would
    have type-checked green and rendered *"Delegate 23 tasks to agent 'scout'?"*
    with the rows ``[1/23] r``, ``[2/23] e``, … — 23 rows on the one door a human
    typed, blowing the §3.7 height budget and clipping ``Cancel`` off screen. The
    single-task signatures were kept for that reason; this guard is what makes
    the batch signatures safe against the same mistake in a future caller.
    """

    if isinstance(tasks, str):
        raise TypeError(
            "tasks must be a tuple of task strings, not a str — a str is a "
            "Sequence[str] and would render one dialog row per CHARACTER. Pass "
            "(task,) for one task, or use request_spawn_consent()."
        )


def _chain_row(mode: str, total: int) -> str | None:
    """§3.1.1 mitigation 2, verbatim — or ``None`` for a topology without it.

    What reaches step k >= 2 is ``render_step(task_k, summary_{k-1})``: text a
    CHILD wrote after this dialog was answered, so the human is told that the
    rows below are not the whole of what will be sent. At N=8 the sentence is 81
    columns and its final character may be clipped on an exactly-80-column
    terminal; that is horizontal, costs no row, and the sentence reads without it.

    A function returning the ROW rather than a boolean, for the reason
    :func:`_model_row` states: :func:`_extra_header_rows` charges for exactly
    what this emits, by asking this.
    """

    if mode != "chain":
        return None
    return (
        f"Steps 2-{total} also receive text written by an earlier agent, "
        "which is not shown here."
    )


def _extra_header_rows(mode: str, model: str | None = None) -> int:
    """Header rows a batch title spends beyond :data:`BATCH_HEADER_ROWS`.

    Exactly one function, called by BOTH :func:`build_batch_consent_title` (which
    emits the rows) and :func:`batch_dialog_fits` (which pays for them). Two
    hand-maintained copies of "does this add a row" is how a measured height and
    a rendered height drift apart, and here that drift is what puts ``Cancel``
    off screen.

    TWO CONDITIONAL ROWS, and each is charged by asking the very function that
    emits it — so a model that sanitises to nothing is un-emitted and unpaid-for
    by the SAME decision rather than by two that happen to agree today.
    ``_chain_row``'s text depends on the member count, which this function does
    not have and does not need: only whether there is a row.

    ``model`` is positional-or-keyword with a default so the arithmetic of every
    model-less composition — which is every one this constant was measured
    against — is unchanged, and the row is priced only where it is drawn.
    """

    return sum(
        row is not None for row in (_chain_row(mode, 2), _model_row(model))
    )


def build_batch_consent_title(
    resolved: ResolvedProfile,
    tasks: tuple[str, ...],
    clamped: PermissionMode,
    *,
    cwd: str,
    mode: SubagentMode,
    model: str | None = None,
) -> str:
    """The whole dialog body for a MULTI-task call — every member on screen (S4).

    Shaped like :func:`build_consent_title` and for the same reason (there is
    nowhere else to put it: ``ExtensionUIDialogOptions`` carries no body field),
    with three differences, all of them height:

    * the two blank spacer rows are dropped — they cost two members;
    * each task gets ONE row, ``[k/N] `` plus at most
      :data:`BATCH_TASK_PREVIEW_CHARS` characters of flattened text, so the row
      count equals the member count and every member is visible rather than
      horizontally clipped;
    * ``mode="chain"`` adds the §3.1.1 mitigation-2 row.

    The row count is exactly ``BATCH_HEADER_ROWS + _extra_header_rows(mode,
    model) + len(tasks)``, which is what :func:`batch_dialog_fits` measures.
    Nothing here may add a row without changing that arithmetic —
    ``test_batch_consent.py`` pins the two together.

    THE ``Model:`` ROW COSTS A MEMBER, AND THAT IS THE TRADE MADE KNOWINGLY. On
    an 80x24 terminal with three options a parallel batch admits 4 members with
    it rather than 5 (a chain, 3 rather than 4); larger terminals are unaffected,
    and a refused batch is told how to split itself
    (:data:`BATCH_TOO_TALL_REASON`) rather than being rendered short. Approving
    up to eight processes without being told what will answer is the worse of the
    two, and it is the one this row buys out of.

    ``resolved.source_path`` is on its own line for the same reason as in the
    single-task body: it is the one line the model cannot influence. Every task
    row below it is model-authored, and the label says so.
    """

    _reject_str_batch(tasks)
    total = len(tasks)
    rows = [
        # "tasks" is always plural: the one-task case never reaches this
        # renderer (:func:`request_spawn_consent_batch` delegates it to the
        # single-task dialog byte-identically).
        f"Delegate {total} tasks to agent "
        f"'{_sanitize_field(resolved.name, limit=_NAME_FIELD_CHARS)}'?",
        # EVERY INTERPOLATED VALUE GOES THROUGH ONE HELPER (F1, CRITICAL). Not
        # cosmetic: :data:`BATCH_HEADER_ROWS` is a CONSTANT that
        # :func:`batch_dialog_fits` charges for, and a raw ``cwd`` containing
        # newlines made the emitted row count 22 against a budget of 10 — the
        # composition the budget approved was not the composition drawn, and the
        # rows that fell off the bottom included ``Cancel``. With every field
        # flattened, control-stripped and bounded to one visible row, the
        # constant is true BY CONSTRUCTION and the property test in
        # ``test_batch_consent.py`` quantifies it over hostile input rather than
        # over an example.
        f"Profile:    {_sanitize_field(resolved.name, limit=_NAME_FIELD_CHARS)} "
        f"({_sanitize_field(resolved.scope, limit=_SCOPE_FIELD_CHARS)} scope)",
        f"Source:     {_sanitize_path(resolved.source_path)}",
        f"Directory:  {_sanitize_path(cwd)}",
        f"Permission: {_sanitize_field(clamped.value)}",
    ]
    # The two conditional header rows, each emitted by the function
    # :func:`_extra_header_rows` charges for — so what is drawn and what was
    # priced cannot disagree.
    rows += [
        row for row in (_model_row(model), _chain_row(mode, total)) if row is not None
    ]
    rows.append("Tasks (written by the model, not by you):")
    rows.extend(
        f"[{index}/{total}] {_truncate_task(task, BATCH_TASK_PREVIEW_CHARS)}"
        for index, task in enumerate(tasks, start=1)
    )
    return "\n".join(rows)


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


# --- the height budget (P3 §3.7) --------------------------------------------
#
# THE BOUNDED QUANTITY IS THE COMPOSED MODAL HEIGHT, NOT THE TITLE. The
# arithmetic, all of it read off the shipped TUI:
#
#   * ``AelixTUIContext.select`` composes title AND options into ONE window:
#     ``_picker_frame`` returns ``[title, rule, *body, rule, hint]``
#     for a MULTI-ROW title, which is this dialog's case and the only case the
#     arithmetic below has to hold for. GitHub #48 gave a SINGLE-row title its
#     own shape — the label rides the top rule, one row shorter — precisely
#     because this budget must not move: nine rows of title cannot ride a rule,
#     so that arm was left byte-shaped as it was
#     (``tui/context.py:140-218``). ``build()`` returns a single
#     ``Window(FormattedTextControl(...), dont_extend_height=True)``
#     (``:419-422``). ``wrap_lines`` is left False and the control supplies no
#     ``get_cursor_position``, so prompt-toolkit has nothing to scroll TO: the
#     overflow is BOTTOM TRUNCATION. ``select``'s own ``viewport = 8`` (``:303``)
#     scrolls the OPTIONS list and does nothing for a tall title.
#   * ``body`` is the option rows plus one counter row (``tui/context.py:419``).
#   * ``_CappedContainer.preferred_height`` clamps the lot to ``_modal_cap``
#     (``tui/overlay.py:221-231``).
#
# so   composed = title + 1 divider + options + 1 counter + 1 divider + 1 hint
#               = title_rows + option_rows + 4
# and  cap(rows) = max(_MIN_CAP, rows - reserve).
#
# Today's single-task dialog is 9 + 3 + 4 = 16 against a cap of 19 at 80x24,
# which is why this has never bitten. A naive 8-task batch is 16 + 3 + 4 = 23:
# the bottom four rows — the hint, the closing divider, the counter, and the LAST
# OPTION, which ``build_options`` guarantees is ``Cancel`` — are simply not
# drawn. It bites from N >= 5. Esc still works (``tui/context.py:453-454``), but a
# ``down, Enter`` from a row that was never on screen would grant AUTO_ACCEPT to
# eight children unseen. That is verbatim the failure S4 calls non-negotiable.
#
# THE STRUCTURAL FIX IS NOT AVAILABLE HERE. ``tui/approval_dialog.py:505-521``
# already solves this shape — ``HSplit([scrollable_body, spacer, options])`` with
# the options at ``Dimension.exact(n)`` so "the security-critical deny option is
# ALWAYS visible even when the diff body is far taller than the cap"
# (``:277-285``) — and ADR-0197 residual R3 named it as the mitigation for this
# dialog. It is not taken because ``ctx.ui.select`` is product-core and P3
# decision S2 sets the product-core delta for this phase at ZERO. R3 stays OPEN
# and is restated in ADR-0199; it is the natural companion to the P4 work that
# will touch these surfaces anyway.
#
# SO: compose short, measure the composition, and REFUSE what will not fit.


def _ioctl_rows() -> int | None:
    """The height the OS reports for the real output, or ``None``. (F4)

    ``sys.__stdout__`` and not ``sys.stdout``: the double-underscore pair is the
    interpreter's original stream, which is the one prompt_toolkit's output was
    built over — a test harness or an extension that replaced ``sys.stdout`` has
    not moved the terminal. ``stderr`` is tried second because a piped stdout
    (``aelix … | tee``) still leaves a tty on fd 2, and prompt_toolkit's own
    output falls back the same way.

    ``None`` means "not a terminal at all", which is the headless and the
    captured-test case; the caller then has nothing to cross-check and keeps
    :func:`shutil.get_terminal_size`'s answer.
    """

    for stream in (sys.__stdout__, sys.__stderr__):
        try:
            rows = os.get_terminal_size(stream.fileno()).lines  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            # AttributeError: pythonw / a replaced ``__stdout__`` of None.
            # ValueError: a closed file. OSError: not a tty (ENOTTY / EBADF).
            continue
        if rows > 0:
            return rows
    return None


def _terminal_rows() -> int:
    """The terminal height the fit check is measured against.

    ``shutil`` is stdlib and reachable from ``aelix_agents`` with no product-core
    edit, which is why the measurement is taken here rather than asked of the
    chrome (``tui/overlay.py``'s ``_modal_cap`` is not reachable from an
    extension).

    ``$LINES`` MAY ONLY MAKE THIS SMALLER (finding F4). ``shutil.get_terminal_size``
    consults ``$COLUMNS``/``$LINES`` BEFORE it asks the OS; the renderer this
    function exists to predict does not — prompt_toolkit asks its output. A
    stale or exported ``LINES`` (a tmux or CI wrapper, a shell that exported it
    before a resize) therefore made the fit check measure a terminal that is not
    the one being drawn to, and measured ``LINES=200`` admits an 8-task chain
    onto a 24-row screen with ``Cancel`` off it. Taking the MINIMUM of the two
    readings, rather than dropping ``$LINES`` outright, keeps the environment
    authoritative wherever there is no tty to contradict it — headless, piped,
    and a captured test run — while making the disagreement resolve in the only
    direction this file is allowed to err in.

    LIVE, not clamped to :data:`MIN_TERMINAL_ROWS`. The P3 plan's smoke item 2 is
    explicit that enlarging the terminal must make the same eight-task call
    render, so a hard ceiling of 24 here — which would refuse eight members on
    every terminal forever — is not what §3.7 asks for. The floor stays: a
    degenerate or unavailable measurement is treated as a SMALL terminal, because
    admitting one member too few costs a split call and admitting one too many
    costs an unseen ``Cancel``.
    """

    lines = shutil.get_terminal_size(fallback=(80, MIN_TERMINAL_ROWS)).lines
    measured = _ioctl_rows()
    if measured is not None:
        lines = min(lines, measured)
    return lines if lines > 0 else MIN_TERMINAL_ROWS


def batch_dialog_fits(
    n_tasks: int, n_options: int, *, mode: str, rows: int, model: str | None = None
) -> bool:
    """Would a batch dialog of this shape be drawn IN FULL? (P3 §3.7)

    Pure arithmetic over the composition described above, so it can be asserted
    at every N without a terminal. ``rows`` is the terminal height, NOT the cap;
    the cap is derived here so the one place that knows ``_RESERVE_ESTIMATE`` is
    the one place that knows ``_MIN_CAP``.

    At 80x24 with three options this admits **5** members, or **4** for a chain:
    ``cap(24) = max(_MIN_CAP, 24 - _RESERVE_ESTIMATE) = 18``, and
    ``header + n_tasks + n_options + 4 <= 18`` solves to ``n <= 5`` for parallel
    (header 6) and ``n <= 4`` for chain (header 7).

    THOSE TWO NUMBERS ARE MEASURED, NOT DERIVED BY HAND. An earlier draft of this
    docstring — and P3 plan §3.7, which it was copied from — said 6 and 5, having
    taken the cap from the CHROME's own floor (``_MODAL_RESERVE_ROWS = 5``, so 19
    at 24 rows) rather than from :data:`_RESERVE_ESTIMATE`, which is deliberately
    one row stricter, and then subtracting the reserve a second time. The
    behaviour was always the conservative one; only the comment was wrong. If
    either constant changes, re-measure rather than re-deriving.

    A legal 8-task batch under a widenable or write-capable profile is therefore
    REFUSED on a short terminal — that is S4 working, not S4 failing, and
    :data:`BATCH_TOO_TALL_REASON` tells the model how to proceed. It is asked only
    when a dialog is required at all; the common case is zero dialogs and pays
    nothing.

    ``model`` is charged only when a ``Model:`` row will actually be drawn, so a
    delegation whose model is unknowable admits exactly what it always did. When
    it IS drawn the admitted counts drop by one — 4 parallel, 3 chain at 80x24 —
    which is the trade :func:`build_batch_consent_title` states.
    """

    header = BATCH_HEADER_ROWS + _extra_header_rows(mode, model)
    return header + n_tasks + n_options + 4 <= max(_MIN_CAP, rows - _RESERVE_ESTIMATE)


def _max_batch_members(
    n_options: int, *, mode: str, rows: int, model: str | None = None
) -> int:
    """The largest ``n_tasks`` :func:`batch_dialog_fits` admits for this shape.

    The same equation solved for ``n_tasks``, so the number in the refusal
    message is the number the check will actually accept on the retry — a "split
    it up" that suggests a size which is then refused again is worse than no
    number at all. ``test_batch_consent.py`` pins the two against each other
    across the whole grid rather than trusting the algebra. ``model`` therefore
    has to reach here too: a suggestion priced without the row the retry will
    draw is exactly the refused-again case.
    """

    header = BATCH_HEADER_ROWS + _extra_header_rows(mode, model)
    return max(0, max(_MIN_CAP, rows - _RESERVE_ESTIMATE) - header - n_options - 4)


def _too_tall_reason(n_tasks: int, *, admits: int) -> str:
    """The model-readable refusal. States what was refused and what to do."""

    if admits >= 2:
        advice = (
            f"Split the work into separate agent calls of at most {admits} "
            "tasks each and send them one at a time."
        )
    else:
        # Below two members a batch cannot be split into a smaller BATCH, so the
        # instruction has to name the other topology rather than a number the
        # check would refuse again.
        advice = (
            "This terminal is too short for any batch dialog: send the tasks as "
            "separate single-task agent calls (mode='single')."
        )
    return BATCH_TOO_TALL_REASON.format(n_tasks=n_tasks, advice=advice)


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
        # ``None`` is Esc (``tui/context.py:375-382``). Anything else is a
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
    model: str | None = None,
) -> SpawnGrant:
    """Take one human decision about one spawn. Never raises, never spawns.

    ``ctx`` is the :class:`ExtensionContext` — typed ``Any`` so this module does
    not import the extension API just to name it, and because the only two
    members it uses (``has_ui``, ``ui``) are read reflectively anyway.

    ``model`` is what the child will be LAUNCHED with — ``resolver.child_model_id``,
    the id that will be on its argv — and it exists only to be SHOWN. It reaches
    :func:`build_consent_title` and nothing else: it does not touch the clamp, the
    options, the grant, or what is sent to the child. ``None`` (the default, and
    every pre-existing caller) renders exactly the dialog that shipped.

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
    decline. The branch itself is :func:`_decide` — shared with the batch door so
    there is one spelling of it — and its comment records what the deny-list form
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
            disclosure=(
                build_disclosure_line(resolved, mode, task_count=1)
                if consented and has_ui and disclosure_is_required(parent, mode)
                else ""
            ),
        )

    if not has_ui:
        # NO disclosure here even under YOLO: ``disclosure_is_required`` is
        # about a human who was not asked, and headless has no human to tell.
        # ``build_disclosure_line`` would compose a string nothing renders —
        # every ``ui.*`` raises in print/json/rpc mode (``headless_ui.py``) —
        # and the honest record of a headless spawn is the ``subagent_*``
        # events, which fire either way.
        return _grant(clamped, widened=False, consented=True)

    if not consent_is_required(resolved, clamped, parent, has_ui=has_ui):
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
    title = build_consent_title(resolved, task, clamped, cwd=cwd, model=model)

    async with _consent_lock():
        answer = await _ask(ctx.ui, title, options)

    mode, widened, consented = _decide(answer, options, clamped, may_widen=may_widen)
    return _grant(mode, widened=widened, consented=consented)


def _decide(
    answer: str | None,
    options: list[str],
    clamped: PermissionMode,
    *,
    may_widen: bool,
) -> tuple[PermissionMode, bool, bool]:
    """Turn a dialog answer into ``(mode, widened, consented)``. THE GATE.

    ONE spelling, called by both :func:`request_spawn_consent` and
    :func:`request_spawn_consent_batch`. The batch door is the one where an
    answer buys up to eight children, so it is the last place that should carry
    a second, hand-maintained copy of this rule — the module docstring's own
    argument about ``consent_is_required`` applies verbatim.

    Fail-closed by ALLOW-LIST rather than by deny-list: only an answer that IS
    one of the strings :func:`build_options` rendered can consent. Esc gives
    ``None``, ``Cancel`` is explicit, an exception inside the dialog gives
    ``None`` via :func:`_ask`, and anything else is a decline.
    """

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
        return clamped, False, False
    # Identity comparison against the rendered option, not a substring match:
    # the option strings are built right here, and a ``"auto-accept" in answer``
    # test would be steerable by a profile named "auto-accept-edits". The
    # comparison is additionally gated on the option having been OFFERED, so an
    # answer nobody was shown can never take the widening branch.
    widened = may_widen and answer == options[1]
    return (
        PermissionMode.AUTO_ACCEPT if widened else clamped,
        widened,
        True,
    )


async def request_spawn_consent_batch(
    ctx: Any,
    resolved: ResolvedProfile,
    tasks: tuple[str, ...],
    parent: PermissionMode,
    *,
    cwd: str,
    mode: SubagentMode,
    model: str | None = None,
) -> SpawnGrant:
    """ONE human decision about ONE tool call, however many children it starts.

    Decision S4. A ``mode="parallel"`` / ``"chain"`` call is one dialog that
    renders EVERY member's task and the one cwd they all share, and the single
    grant it produces is what the executor spends on each member. It is NOT the
    session memo this module removed — see the P3 note in the block comment below
    :data:`CANCEL_OPTION` — because every task here is inside the call the hook
    already validated and every one of them is on screen before the human answers.

    ONE PROFILE, ONE MODE, ONE DECISION (S3): :class:`SpawnGrant` stays singular,
    which is what keeps "the human approved exactly what runs" checkable.

    The flow is :func:`request_spawn_consent`'s, with two additions:

    * ``len(tasks) == 1`` is delegated to the single-task door verbatim, so a
      one-member batch is indistinguishable from a P2 dialog — same body, same
      options, same 300-character preview. A one-step chain has no step 2, so
      §3.1.1's unshown-text problem does not exist for it and the widening rung
      is offered exactly as it would be for ``mode="single"``.
    * ``len(tasks) >= 2`` measures the composed modal height against the live
      terminal FIRST (:func:`batch_dialog_fits`). A batch whose dialog would be
      bottom-truncated is REFUSED — non-consented, with
      :data:`BATCH_TOO_TALL_REASON` on :attr:`SpawnGrant.reason`, no dialog
      shown, nothing narrowed and nothing started. Silently rendering the first
      six of eight members, or rendering all eight with ``Cancel`` off screen, are
      both the failure S4 declares non-negotiable.

    ``model`` is the same display-only value the single-task door takes, with one
    consequence that is NOT display-only: the row it adds makes the dialog taller,
    so :func:`batch_dialog_fits` charges for it and a short terminal admits one
    member fewer. That can only ever REFUSE a batch, never show one short.

    Never raises on any input a human or a model can produce. It DOES raise
    ``TypeError`` for a ``str`` ``tasks`` and ``ValueError`` for an empty one:
    both are programming errors in a caller — ``AgentCall.tasks`` is "ALWAYS at
    least one, ALWAYS a tuple" (``tool.py:271``) — and both would otherwise
    produce a dialog that misdescribes what is about to run.
    """

    _reject_str_batch(tasks)
    if not tasks:
        raise ValueError(
            "tasks is empty: there is no delegation to consent to. "
            "AgentCall guarantees at least one task (tool.py:271)."
        )
    if len(tasks) == 1:
        # Byte-identical to P2, deliberately: the batch renderer's shorter
        # preview and its plural heading would be a gratuitous behaviour change
        # for the shape that is already shipped and already tested.
        return await request_spawn_consent(
            ctx, resolved, tasks[0], parent, cwd=cwd, model=model
        )

    # LIVE read (OC-7), exactly as the single-task door does it.
    has_ui = bool(getattr(ctx, "has_ui", False))
    clamped = child_permission_mode(
        resolved.profile.approval_mode, parent, resolved.scope, has_ui=has_ui
    )

    def _grant(
        granted: PermissionMode, *, widened: bool, consented: bool, reason: str = ""
    ) -> SpawnGrant:
        return SpawnGrant(
            profile=resolved.name,
            source_path=resolved.source_path,
            scope=resolved.scope,
            mode=granted,
            widened=widened,
            consented=consented,
            reason=reason,
            # ONE line for the whole call, carrying the member COUNT — the same
            # arithmetic decision S4 already made for the dialog it replaces:
            # a batch is one decision about one tool call, so it is one
            # disclosure, not N.
            disclosure=(
                build_disclosure_line(resolved, granted, task_count=len(tasks))
                if consented and has_ui and disclosure_is_required(parent, granted)
                else ""
            ),
        )

    if not has_ui:
        # Headless is a silent DOWNGRADE, never a refusal (step 2 of
        # :func:`request_spawn_consent`), and it is also where the height budget
        # does not apply: there is no modal to truncate. A ``-p`` batch runs at
        # the clamp whatever N is.
        return _grant(clamped, widened=False, consented=True)

    if not consent_is_required(resolved, clamped, parent, has_ui=has_ui):
        # THE COMMON CASE, AND IT COSTS NOTHING. A DEFAULT parent clamps to
        # ``plan``, ``grants_write_authority`` is False and a non-declaring
        # profile cannot be widened, so a fan-out of eight read-only children
        # renders no dialog at all — and therefore has no height, and can never
        # be refused for not fitting. Asked with the same predicate as every
        # other door; see :func:`consent_is_required` for why it takes no
        # ``mode``.
        return _grant(clamped, widened=False, consented=True)

    may_widen = _may_widen(resolved, clamped, has_ui=has_ui, mode=mode)
    options = build_options(clamped, may_widen=may_widen)

    rows = _terminal_rows()
    if not batch_dialog_fits(
        len(tasks), len(options), mode=mode, rows=rows, model=model
    ):
        # THE S4 REFUSAL, AND IT IS A LIVE PATH. Measured BEFORE the title is
        # built and before the lock is taken: there is nothing to show, so no
        # modal is opened and no other prompt is blocked behind this one. The
        # grant is non-consented, so the hook blocks the call — the executor is
        # never reached, no cwd is entered and no process is created.
        return _grant(
            clamped,
            widened=False,
            consented=False,
            reason=_too_tall_reason(
                len(tasks),
                admits=_max_batch_members(
                    len(options), mode=mode, rows=rows, model=model
                ),
            ),
        )

    title = build_batch_consent_title(
        resolved, tasks, clamped, cwd=cwd, mode=mode, model=model
    )

    budgeted = BATCH_HEADER_ROWS + _extra_header_rows(mode, model) + len(tasks)
    if len(title.splitlines()) > budgeted:
        # BELT ON F1, AND IT IS CHEAP. Everything above already makes the row
        # count true by construction — :func:`_sanitize_field` flattens every
        # interpolated value and :func:`_truncate_task` every task — so this
        # branch is unreachable today and the property test says so at every N.
        # It is here because the failure it catches is silent and severe: the
        # check above priced a CONSTANT header, and if any future field escapes
        # the helper the difference between what was priced and what will be
        # drawn is rows falling off the bottom, ``Cancel`` among them. Measuring
        # the actual string closes that gap by construction instead of by review,
        # and the answer to a disagreement is the refusal path that already
        # exists rather than a dialog nobody can trust.
        return _grant(
            clamped,
            widened=False,
            consented=False,
            reason=_too_tall_reason(
                len(tasks),
                admits=_max_batch_members(
                    len(options), mode=mode, rows=rows, model=model
                ),
            ),
        )

    async with _consent_lock():
        answer = await _ask(ctx.ui, title, options)

    granted, widened, consented = _decide(
        answer, options, clamped, may_widen=may_widen
    )
    return _grant(granted, widened=widened, consented=consented)


__all__ = [
    "BATCH_HEADER_ROWS",
    "BATCH_TASK_PREVIEW_CHARS",
    "BATCH_TOO_TALL_REASON",
    "CANCEL_OPTION",
    "DIALOG_FIELD_CHARS",
    "DIALOG_ROW_CHARS",
    "MIN_TERMINAL_ROWS",
    "SpawnGrant",
    "TASK_PREVIEW_CHARS",
    "batch_dialog_fits",
    "build_batch_consent_title",
    "build_consent_title",
    "build_options",
    "consent_is_required",
    "disclosure_is_required",
    "build_disclosure_line",
    "contains_control_chars",
    "request_spawn_consent",
    "request_spawn_consent_batch",
]
