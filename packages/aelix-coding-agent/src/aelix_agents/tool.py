"""The model-facing ``agent`` tool — ADR-0197 §(k)/§(i).

WHAT IS AND IS NOT IN THIS FILE. The tool's SHAPE (name, schema, roster
description, execution mode), its ARGUMENT validation, and the rendering of a
:class:`~aelix_coding_agent.subagent_contract.SubagentResult` back to the model
live here. The CONSENT DECISION does not — it is taken in the extension's own
``tool_call`` hook (``extension.py``) and reaches ``execute()`` only as a
:class:`~aelix_agents.consent.SpawnGrant` popped out of a private dict. Three
measured reasons, all of which make the hook the only correct location:

1. **``execute()`` is not serialised.** The kernel runs ``before_tool_call`` in
   the SEQUENTIAL prep phase (``loop.py:526-547``, driven from ``:837-872``) but
   ``execute`` under ``asyncio.gather`` (``loop.py:905``), with
   ``tool_execution = "parallel"`` by default (``harness/core.py:290``). Two
   modals from one batch collide on ``tui/chrome.py:524``'s single ``_modal``
   slot: ``mount_modal`` (``:1511``) overwrites unconditionally, the first
   Future is orphaned, and the turn hangs until Ctrl+C.
2. **``execute()`` has no UI.** :class:`ToolExecutionContext`
   (``aelix_ai/tools.py:54-86``) has exactly four fields — ``tool_call_id``,
   ``signal``, ``on_partial``, ``model`` — and no ``ui`` and no ``has_ui``.
   Both live on :class:`ExtensionContext`, which hooks receive and ``execute``
   does not.
3. **``execute()`` has no first-class refusal.** A hook returning
   ``ToolCallResult(block=True, reason=…)`` is handled by
   ``harness/hooks.py::_reducer_tool_call`` (``:1419-1439``) — sequential,
   FIRST BLOCK WINS — and the kernel renders it as a model-readable immediate
   error result (``loop.py:534-547``). An ``execute()`` refusal is just another
   error string.

:data:`AGENT_TOOL_NAME` is imported from :mod:`aelix_agents.print_channel`
rather than declared here: the spawn path must subtract it from every child's
grant and must be able to do that without importing this module, which would
invert the dependency and drag the roster machinery into the spawn path.
"""

from __future__ import annotations

import dataclasses
import json
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolResult
from aelix_coding_agent.tools.provenance import builtin_provenance, mark_builtin
from rich.cells import cell_len, set_cell_size

from aelix_agents.chain import MAX_TASK_BYTES, TaskTooLarge, check_task_size, uses_previous
from aelix_agents.envelope import (
    MAX_ERROR_NOTE_BYTES,
    NO_OUTPUT,
    REASON_UNSPECIFIED,
    cap_summary,
    child_line,
    child_prose,
    count_trail_calls,
    error_repeats_summary,
    error_says_only_the_reason,
    says_nothing,
    summary_is_the_error,
)
from aelix_agents.panel import _flatten
from aelix_agents.print_channel import AGENT_TOOL_NAME

# The resume preamble's reserve, from the module that writes the preamble. An
# acyclic edge: ``runtime``'s own imports never reach this module (#610 lane A).
from aelix_agents.runtime import RESUME_PREAMBLE_RESERVE

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.subagent_contract import (
        ResolvedProfile,
        SubagentMode,
        SubagentResult,
    )

    from aelix_agents.child_session import ResumeTarget
    from aelix_agents.consent import SpawnGrant

MAX_ROSTER = 24
"""How many profiles the description may advertise. A roster is prompt text on
EVERY turn of the parent session, so its cost is paid per request, forever."""

ROSTER_DESCRIPTION_CHARS = 160
"""Per-profile budget. A profile's ``description`` is author-controlled text; an
unbounded one would silently push the parent's own system prompt out of the
context window."""

ROSTER_MAX_BYTES = 4096
"""Hard ceiling on the rendered roster, applied after the per-entry budget so a
pathological set of 24 maximal descriptions still cannot blow the tool
description out."""

DESCRIPTION_HEAD_MAX_BYTES = 2048
"""Hard ceiling on the fixed contract text, applied beside the roster's own
:data:`ROSTER_MAX_BYTES` so the WHOLE tool description stays bounded.

MEASURED, not guessed. P2's head was 530 bytes; the P3 head is ~1.1 KiB; 2048 is
1.8× that and 3.9× P2's, i.e. room for one more mode before the next author has
to re-litigate the number. The whole description is therefore at most
``DESCRIPTION_HEAD_MAX_BYTES + ROSTER_MAX_BYTES + separators ≈ 6.2 KiB ≈ 1 550
tokens`` — re-sent on EVERY request of the parent session, so a 40-turn session
pays ~62 000 tokens of description alone. That is the whole reason there is a cap
at all, and why both halves are pinned by a test rather than by good intentions."""

MAX_PARALLEL_TASKS = 8
"""Tasks one ``agent`` call may carry (S3, and S7 clause 1).

Below ``runtime.MAX_DELEGATIONS_PER_PROMPT`` (12) ON PURPOSE: a fresh prompt can
always run one full batch and still holds four delegations, so the per-prompt
budget can never make a legal batch unrunnable from the very first call.

A batch above this is a malformed CALL, not a batch to be trimmed:
:func:`parse_agent_call` raises, the extension's ``tool_call`` hook turns that
into a blocked call the model reads (``extension.py:939-942``), and NO process is
created. Trimming to the first eight would silently drop work the model believes
it delegated — the failure mode S7 clause 1 exists to forbid."""

_MODES: tuple[SubagentMode, ...] = ("single", "parallel", "chain")
"""The one spelling of the mode set, shared by the schema's ``enum`` and by
:func:`parse_agent_call`. Two hand-maintained copies is how a schema starts
advertising a mode the parser refuses. Mirrors ``SubagentMode``
(``subagent_contract.py:74``), which is imported for TYPE CHECKING only —
evaluating it at runtime would pull a product-core symbol into this module's
import graph to save one tuple."""

AgentAction = Literal["run", "resume"]
"""What one ``agent`` call does (#610 D1, ADR-0277).

``run`` is everything this tool did before #610 — delegate new work, in one of
:data:`_MODES`. ``resume`` continues ONE agent this conversation delegated
earlier, on its own session file, inside the limits it ran with."""

_ACTIONS: tuple[AgentAction, ...] = ("run", "resume")
"""The one spelling of the action set — :data:`_MODES`' rule, for :data:`_MODES`'
reason: the schema's ``enum`` and :func:`parse_agent_call` read this tuple, so the
schema cannot advertise an action the parser refuses. A value outside it is
REFUSED, never run as ``run``: before #610 the kernel validator delivered an
unknown ``action`` untouched and the parser ignored it, so a model that asked to
resume got a fresh delegation and believed it had resumed (#610 design D1) — and
#611's background actions, sent to a build that does not know them, must not
start the foreground run they were not."""

RESUME_TASK_MAX_BYTES = MAX_TASK_BYTES - RESUME_PREAMBLE_RESERVE
"""The most a resume's instruction may carry, in UTF-8 bytes (#610 D1).

Smaller than :data:`~aelix_agents.chain.MAX_TASK_BYTES` by exactly
:data:`~aelix_agents.runtime.RESUME_PREAMBLE_RESERVE`, because the child's task is
the preamble that says it is being resumed, then this instruction
(``runtime.resume_task``): an instruction at this limit still makes a task inside
the argv bound ``MAX_TASK_BYTES`` defends. Refused HERE, with this number in the
message, rather than at the full task limit — a model told "64 KiB" for an
instruction the hook then refused at the preamble's expense could not act on the
refusal (critique m7)."""

AGENT_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "profile": {
            "type": "string",
            "description": (
                "Agent profile name (see the roster below). ONE profile per "
                "call — every task in this call runs under it."
            ),
        },
        "mode": {
            "type": "string",
            "enum": list(_MODES),
            "default": "single",
            "description": (
                "single (default): one task, in 'task'. "
                "parallel: every task in 'tasks' at once, independently. "
                "chain: the tasks in 'tasks' in order, each able to insert the "
                "previous one's summary with {previous}."
            ),
        },
        "task": {
            "type": "string",
            "description": (
                "mode=single only. The complete task. The child has NO "
                "conversation history and cannot ask you anything: state the "
                "goal, the relevant paths and the definition of done in this "
                "one string. With action=resume it is instead the NEXT "
                "instruction for that agent, which keeps its own conversation."
            ),
        },
        "tasks": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": MAX_PARALLEL_TASKS,
            "description": (
                f"mode=parallel or chain. 1 to {MAX_PARALLEL_TASKS} complete "
                "tasks, each self-contained exactly as 'task' is. Asking for "
                "more is refused and nothing runs."
            ),
        },
        "cwd": {
            "type": "string",
            "description": (
                "Optional working directory for every task in this call; must "
                "be inside the parent's working directory."
            ),
        },
        "action": {
            "type": "string",
            "enum": list(_ACTIONS),
            "default": "run",
            "description": (
                "run (default): delegate new work, as 'mode' describes. "
                "resume: continue ONE agent you delegated earlier, in its own "
                "conversation — pass its 'id', the 'profile' it ran as, and its "
                "next instruction in 'task'; no 'mode', 'tasks' or 'cwd'."
            ),
        },
        "id": {
            "type": "string",
            "description": (
                "action=resume only: which agent to continue — the id its "
                'result shows, on its "Session:" line or after "session" in a '
                'batch member\'s "did not finish" heading. Not a file path.'
            ),
        },
        # NO ``timeout_ms`` (#604, ADR-0276). A delegated agent has no time
        # limit: the 10-minute default and the 30-minute caps cut off children
        # that were still doing the work, and the human — Esc, a signal,
        # ``/agents run``'s own interrupt — is the bound. A call that still
        # carries the key is not refused; ``parse_agent_call`` explains why.
    },
    # ``required`` names ONLY ``profile``, and that is not laxity. The real rule
    # is CONDITIONAL — ``task`` for mode=single, ``tasks`` for the other two —
    # and JSON Schema's ``if``/``then`` and ``oneOf`` are not reliably honoured
    # across the provider tool-schema validators aelix targets, so declaring the
    # conditional here would be a rule enforced on some providers and silently
    # absent on others. It is enforced in ``parse_agent_call`` instead, whose
    # refusal the ``tool_call`` hook turns into a blocked call the model reads
    # (``extension.py:939-942``) with no process created.
    #
    # The P2 argument shape is unaffected: ``{"profile": …, "task": …}`` still
    # satisfies ``required``, ``mode`` defaults to "single", and the parse
    # normalizes ``task`` into a 1-tuple.
    #
    # ``action="resume"`` (#610 D1) kept it too: a resume names its profile as
    # well, and the hook only COMPARES it with the one the agent ran as. The
    # agent is never rebuilt from the caller's value — OpenHands rebuilt a
    # resumed agent from the caller's ``subagent_type`` — and a wrong id meets a
    # profile that does not match before it meets a dialog.
    "required": ["profile"],
}

_DESCRIPTION_HEAD = (
    "Delegate self-contained tasks to subagents — separate aelix processes, each "
    "running under its own agent profile, its own system prompt and its own "
    "context window.\n"
    "\n"
    "Each one runs to completion and returns ONE summary. A child cannot ask you "
    "a question and you cannot steer it mid-run, so every task must be complete "
    "on its own. A delegated agent is "
    "READ-ONLY unless the user's own permission posture already grants more, or "
    "they approve more at a prompt, and it can never hold a tool you do not "
    "hold, delegate further, or run outside your working directory.\n"
    "\n"
    "ONE call carries ONE profile and one or more tasks:\n"
    '- mode="single" (the default): pass \'task\'. One child.\n'
    '- mode="parallel": pass \'tasks\' — independent jobs run at once, at most '
    f"{MAX_PARALLEL_TASKS} per call. Results come back in the order you listed "
    "them.\n"
    '- mode="chain": pass \'tasks\' — run in order; write {previous} anywhere in '
    "a task to insert the previous task's summary. Step 1 may not use it. The "
    "chain stops at the first failure and returns what did run.\n"
    "\n"
    "Asking for more tasks than the limit is REFUSED and nothing runs — the call "
    "is never trimmed to fit. Delegate read-heavy work first.\n"
    "\n"
    'All of that is action="run", the default. action="resume" continues ONE '
    "agent that finished or was stopped: pass the 'id' its result shows (on its "
    '"Session:" line, or after "session" in a batch member\'s "did not finish" '
    "heading), the 'profile' it ran as, and its next instruction as 'task'. "
    "It goes on in its own conversation, on the same profile and model, and its "
    "limits never widen. Resume it rather than delegating the same work again "
    "from scratch."
)
"""The fixed contract text the roster is appended to.

#610 D3 took "There is no conversation:" out of its second paragraph — false
once an agent can be resumed into the one it had — and added the last paragraph,
which names both actions the way the list above names every mode
(``test_the_contract_text_stays_inside_its_own_budget`` pins both sets)."""

def _no_profiles_message() -> str:
    """The "nothing to delegate to" refusal, naming the COMPUTED directory.

    NOT a constant with a literal ``~/.aelix/agent/agents``. The user agent root
    moves with ``AELIX_CODING_AGENT_DIR`` (``cli/config.py::get_agent_dir``), so
    a hardcoded path tells a user who set that variable to write a file in a
    directory aelix will never read. #117 shipped exactly that bug once, in a
    signpost written for exactly this purpose.

    Resolved at CALL time, not at import: the directory comes from the
    environment and this module is imported long before the CLI has settled it.

    Reachable but rare since the bundled tier ships a starter profile
    (``discovery.builtin_agents_dir``) — it takes a user who has deleted it or
    whose install is partial, which is precisely when a wrong path costs the
    most.
    """

    from aelix_coding_agent.agents.discovery import user_agents_dir

    return (
        "No agent profiles are available. Ask the user to create one at "
        f"{user_agents_dir()}/<name>.md before delegating."
    )


class AgentCallError(ValueError):
    """A malformed ``agent`` call. The message is written FOR THE MODEL.

    Raised during hook-time validation, so the refusal reaches the model as a
    blocked tool call and no process is ever created.
    """


@dataclass(frozen=True)
class AgentCall:
    """The validated arguments of one ``agent`` tool call.

    ONE profile, N tasks (S3). Heterogeneous batches — a different profile per
    task — are P4 ``aelix-team`` work, and the reason is not conservatism: with
    one profile every member shares the same clamp
    (``posture.child_permission_mode``), the same ``consent_is_required``
    (``consent.py:591-682``) and the same ``_may_widen``
    (``consent.py:493-588``), which is what makes ONE consent decision for the
    whole batch coherent. :class:`~aelix_agents.consent.SpawnGrant` is singular
    by construction (``consent.py:268-275``: one ``profile``, one
    ``source_path``, one ``mode``).
    """

    profile: str
    tasks: tuple[str, ...]
    """ALWAYS at least one, ALWAYS a tuple. ``mode="single"`` normalizes ``task``
    into a 1-tuple at parse time so the single and batch paths downstream are one
    code path rather than two that must be kept in agreement."""
    mode: SubagentMode = "single"
    cwd: str | None = None
    action: AgentAction = "run"
    """``"resume"`` continues the agent :attr:`id` names (#610 D1). A resume is
    ONE task — its next instruction — with ``mode="single"`` and no ``cwd``, by
    construction of :func:`parse_agent_call`, so every reader of the single
    shape reads a resume correctly; only the hook and ``execute()`` branch on it."""
    id: str | None = None
    """``action="resume"`` only: the child SESSION id the model passed — the id
    on a result's ``Session:`` line, never a path (the parser refuses a
    path-shaped one). ``None`` when it named none: the hook answers that with
    the list of agents it could resume (#610 D7), which a parser refusal could
    not carry."""

    @property
    def task(self) -> str:
        """The single-task spelling. RAISES for a batch.

        A property rather than a field so there is exactly one storage location
        for the tasks — a ``task`` field beside ``tasks`` is a second copy that
        can disagree with the first.

        It raises rather than returning ``tasks[0]`` because a caller that reads
        "the task" off a batch would spawn member 1 and silently drop the rest:
        the model would be told its eight tasks were delegated and one child
        would exist. ``TypeError`` and not
        :class:`AgentCallError` — an ``AgentCallError`` message is written FOR
        THE MODEL and this is a PROGRAMMING error in a caller that has not been
        taught about batches, which must surface as a bug and not as a refusal
        the model reads and works around.
        """

        if len(self.tasks) != 1:
            raise TypeError(
                f"AgentCall carries {len(self.tasks)} tasks (mode={self.mode!r}); "
                "read '.tasks' and run every member."
            )
        return self.tasks[0]


@dataclass(frozen=True)
class PendingSpawn:
    """What the ``tool_call`` hook approved, waiting for ``execute()``.

    Keyed by ``tool_call_id`` in the extension's private dict.
    :attr:`ToolCallHookEvent.tool_call_id` and
    :attr:`ToolExecutionContext.tool_call_id` are the SAME id, which is what
    makes the hand-off possible without touching the transcript.

    The whole CALL is carried, not just the grant, and that is a security
    property rather than an optimisation: ``event.args`` is the same mutable
    dict the tool receives (``harness/hooks.py`` D.1.5, and
    ``harness/core.py:4705-4707`` explicitly permits a later handler to mutate
    it). The human approved the tasks, the profile and the directory that were on
    screen; re-reading them from ``args`` in ``execute()`` would let anything
    that ran in between substitute different ones — and under fan-out that is a
    whole batch of substituted tasks behind one approved dialog, not one.
    """

    grant: SpawnGrant
    resolved: ResolvedProfile
    call: AgentCall
    cwd: str
    """The CONTAINED child working directory, already resolved against the
    parent's (``print_channel.resolve_child_cwd``) and shown in the dialog."""
    resume: ResumeTarget | None = None
    """The agent an ``action="resume"`` call continues (#610 D12), as the hook
    found and checked it — so ``execute()`` resumes the child the human was
    shown, with the limits that were on screen, and never looks the id up
    again. ``None`` for every ``run``; trailing and defaulted so no ``run``
    constructor changes."""


def _parse_mode(raw: object) -> SubagentMode:
    """Rule 2. Absent (or an explicit ``null``) means ``"single"``.

    An explicit ``null`` is treated as absent throughout this parser rather than
    as a bad value, because a provider that materialises every declared property
    of the schema sends ``null`` for the ones the model omitted — refusing those
    would refuse the single most common well-formed call there is.
    """

    if raw is None:
        return "single"
    if raw not in _MODES:
        raise AgentCallError(
            f"mode={raw!r} is not a mode. Use one of: {', '.join(_MODES)}."
        )
    return raw  # type: ignore[return-value]  # narrowed by the _MODES membership test


def _parse_action(raw: object) -> AgentAction:
    """#610 D1. Absent (or an explicit ``null``) means ``"run"``.

    :func:`_parse_mode`'s rule exactly, ``null`` clause included: a provider that
    materialises every declared property sends ``action: null`` on every plain
    delegation. ``""`` is REFUSED, as ``mode=""`` is — an empty string names no
    action, and reading it as ``run`` would substitute a default for a value the
    model chose. So is every value outside :data:`_ACTIONS`; see there for why
    an unknown action must never run as ``run``.
    """

    if raw is None:
        return "run"
    if raw not in _ACTIONS:
        raise AgentCallError(
            f"action={raw!r} is not an action. Use one of: {', '.join(_ACTIONS)}."
        )
    return raw  # type: ignore[return-value]  # narrowed by the _ACTIONS membership test


_ID_IS_FOR_RESUME = (
    "'id' is only for action='resume'. Pass action='resume' to continue that "
    "agent, or leave 'id' out to delegate new work."
)
"""A ``run`` that carries an ``id`` (#610 D1). Before #610 the key was ignored
and the call delegated anew; a model that believed it had continued an agent
would then reason about a conversation the new child never had. Both fixes are
named, because which one the model meant is not knowable from here."""

_BACKGROUND_REFUSED = (
    "Background delegation is not available: a background child has no "
    "owner to reap it and no channel to report through. Call agent() "
    "and wait for the result."
)
"""``background`` is refused for either action: it names something that would
NOT happen (see the ``timeout_ms`` note in :func:`parse_agent_call`)."""

_RESUME_NEEDS_PROFILE = (
    "action='resume' needs 'profile': the profile that agent ran as — the last "
    "line of its result names it ([agent <profile> · …])."
)
_RESUME_NEEDS_TASK = (
    "action='resume' needs 'task': the next instruction for that agent. It keeps "
    "its own conversation, so say what to do next rather than the whole job "
    "again."
)
_RESUME_TASK_NOT_TEXT = (
    "'task' holds half of a character (a lone surrogate), which cannot be sent to "
    "the agent. Resend the instruction as plain text."
)
_RESUME_TASK_HAS_NUL = (
    "'task' holds a NUL character (U+0000), which cannot be sent to the agent. "
    "Resend the instruction without it."
)
_RESUME_ID_NOT_A_STRING = "'id' must be the agent's Session id, as a string."
_RESUME_ID_IS_A_PATH = (
    "'id' is the agent's Session id — the id its result shows, on its "
    '"Session:" line or after "session" in a batch member\'s "did not finish" '
    "heading — not a file path."
)


def _parse_resume_id(raw: object) -> str | None:
    """The id a resume names, or ``None`` when it names none (#610 D1).

    A MISSING id is not refused here, and that is the point: the hook answers
    it with the agents this conversation could resume (D7), which a parser
    refusal has no way to carry. A blank one is the same omission.

    A PATH-SHAPED id is refused: a separator of either platform, or a
    ``.jsonl`` name. The id is a session id and nothing else (ADR-0243 §5: no
    model text carries a session path); a model that guessed a path learns the
    rule rather than "unknown id". An absolute path always carries a separator,
    so the separator test is the absolute-path test too.
    """

    if _absent(raw):
        return None
    if not isinstance(raw, str):
        raise AgentCallError(_RESUME_ID_NOT_A_STRING)
    child_id = raw.strip()
    if not child_id:
        return None
    if "/" in child_id or "\\" in child_id or child_id.lower().endswith(".jsonl"):
        raise AgentCallError(_RESUME_ID_IS_A_PATH)
    return child_id


def _parse_resume(args: Mapping[str, Any]) -> AgentCall:
    """``action="resume"`` — ONE agent, its next instruction, nothing else (#610 D1).

    A resume continues an agent where and as it ran: the directory it ran in,
    one child, in the foreground. So every key that would choose otherwise is
    REFUSED rather than ignored — ``tasks``, a ``mode`` other than ``single``,
    ``cwd``, ``background`` — for the reason this module refuses
    ``background``: an ignored key is a model believing something happened that
    did not. Exclusions are checked before the ``task`` requirement, as
    :func:`parse_agent_call` does for ``task``/``tasks``: a model that wrote
    ``tasks`` is told about ``tasks``, not that ``task`` is missing.

    Absence follows :func:`_absent` for ``tasks`` (the presence question, as in
    a ``run``), while ``mode`` and ``cwd`` keep the run path's rule that their
    empty string is a value: ``mode=""`` is refused there too, and ``cwd`` has
    no value a resume could use.

    The instruction is held to :data:`RESUME_TASK_MAX_BYTES`, not
    ``MAX_TASK_BYTES``, and the refusal states that number. ``profile`` is
    required as it is for a ``run``; the hook compares it with the profile the
    agent ran as and never builds the agent from it.
    """

    profile = args.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        raise AgentCallError(_RESUME_NEEDS_PROFILE)
    child_id = _parse_resume_id(args.get("id"))
    if not _absent(args.get("tasks")):
        raise AgentCallError(
            "action='resume' takes no 'tasks': it gives ONE agent its next "
            "instruction, in 'task'."
        )
    mode = args.get("mode")
    if mode is not None and mode != "single":
        raise AgentCallError(
            f"action='resume' takes no mode={mode!r}: it continues ONE agent, so "
            "leave 'mode' out."
        )
    if args.get("cwd") is not None:
        raise AgentCallError(
            "action='resume' takes no 'cwd': the agent goes on in the directory "
            "it ran in."
        )
    if args.get("background"):
        raise AgentCallError(_BACKGROUND_REFUSED)
    task = args.get("task")
    if not isinstance(task, str) or not task.strip():
        raise AgentCallError(_RESUME_NEEDS_TASK)
    # A LONE SURROGATE IS REFUSED HERE, where a refusal costs nothing. It is a
    # legal ``str`` that a call's JSON can carry (``"\ud83d"``, half an emoji),
    # and it cannot be encoded into the child's argv: let through, it would fail
    # the spawn after the budget was charged and the run recorded.
    try:
        size = len(task.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise AgentCallError(_RESUME_TASK_NOT_TEXT) from exc
    # NUL, for the same reason (#610 F7): UTF-8 encodes it, but no argv can
    # carry it — ``create_subprocess_exec`` raises "embedded null byte" after
    # the budget was charged and four records written (Codex review 9).
    if "\x00" in task:
        raise AgentCallError(_RESUME_TASK_HAS_NUL)
    if size > RESUME_TASK_MAX_BYTES:
        raise AgentCallError(
            f"'task': the instruction is {size} bytes; a resume instruction may "
            f"be at most {RESUME_TASK_MAX_BYTES} bytes — {MAX_TASK_BYTES} for any "
            f"task, less {RESUME_PREAMBLE_RESERVE} for the note aelix puts in "
            "front of it. Shorten it."
        )
    return AgentCall(
        profile=profile.strip(),
        tasks=(task,),
        mode="single",
        cwd=None,
        action="resume",
        id=child_id,
    )


def _absent(value: object) -> bool:
    """Is this argument ABSENT — including the empty shapes a provider invents?

    Same argument as :func:`_parse_mode`'s ``null`` clause, one step further: a
    provider that materialises every declared property of the schema sends
    SOMETHING for the ones the model omitted, and ``null`` is only its most
    common spelling. An omitted string comes back as ``""`` and an omitted array
    as ``[]`` from providers that fill in type-appropriate zero values.

    Used ONLY for presence questions: the ``task``/``tasks`` pair that drives the
    two mutual-exclusion refusals, and — #610 D1 — the ``id`` a ``run`` must not
    carry and the ``tasks`` a ``resume`` must not. That is the case where a
    materialised empty is read as the model ASSERTING the key, producing a
    refusal it cannot act on — ``{"mode": "parallel", "tasks": [...], "task":
    ""}`` is answered with "mode='parallel' takes 'tasks', not 'task'" about a
    ``task`` the model never wrote, so it re-issues the identical call; and every
    plain delegation from such a provider carries ``id: ""``.

    DELIBERATELY NOT used for ``cwd`` or ``mode``. There an empty string is the
    OPERATIVE argument rather than evidence about presence: ``cwd=""`` names no
    directory and ``mode=""`` names no mode, both are refused today
    (``test_rule_7_a_blank_cwd_is_refused``,
    ``test_rule_2_an_unknown_mode_is_refused``), and turning either into
    "absent" would silently substitute a default for a value the model chose.
    Likewise a NON-empty ``task``/``tasks`` that is merely blank (``"   "``) is
    presence, and is refused below on its contents.
    """

    return value is None or value == "" or value == []


def _parse_tasks(raw: object, *, mode: SubagentMode) -> tuple[str, ...]:
    """Rule 4 — the ``tasks`` list for ``parallel`` / ``chain``."""

    if not isinstance(raw, list):
        raise AgentCallError(
            f"mode={mode!r} needs 'tasks': a list of complete task strings, one "
            "per agent."
        )
    if not raw:
        raise AgentCallError("'tasks' must contain at least one task.")
    # THE COUNT IS CHECKED BEFORE THE CONTENTS, and before anything else that
    # could be slow or allocate: an oversize batch is refused on the cheapest
    # possible evidence, and the model reads the limit rather than a complaint
    # about tasks[47] that only makes sense once it has counted them itself.
    if len(raw) > MAX_PARALLEL_TASKS:
        raise AgentCallError(
            f"{len(raw)} tasks is more than one call may carry — the limit is "
            f"{MAX_PARALLEL_TASKS}. NOTHING was started and the list was not "
            "trimmed to fit: issue several agent() calls, or give one agent a "
            "bigger task."
        )
    tasks: list[str] = []
    for index, task in enumerate(raw, start=1):
        if not isinstance(task, str) or not task.strip():
            raise AgentCallError(
                f"tasks[{index}] is empty. Every task must describe a whole job "
                "on its own — the child has no conversation history to fall "
                "back on."
            )
        tasks.append(task)
    return tuple(tasks)


def parse_agent_call(args: Mapping[str, Any]) -> AgentCall:
    """Validate one ``agent`` call. Raises :class:`AgentCallError`.

    The kernel's own ``validate_tool_arguments`` already ran, but it PRESERVES
    unknown keys (``aelix_ai/tools.py`` — "additive, never strips"), so a model
    that invents ``background`` gets it delivered here and is rejected
    explicitly rather than ignored: silently dropping ``background: true`` would
    let a model believe it had started something it had not. For the same reason
    this function — not the schema — is where the CONDITIONAL requirement
    (``task`` xor ``tasks``) is enforced; see ``required`` in
    :data:`AGENT_TOOL_PARAMETERS` for why the schema cannot express it portably.

    EVERY refusal here happens BEFORE a process exists, which is the whole point
    of validating at hook time: the ``tool_call`` hook turns an
    :class:`AgentCallError` into a blocked call (``extension.py:939-942``) and
    the kernel renders it as a model-readable immediate error result
    (``loop.py:534-547``). An oversize batch is therefore a refused CALL and is
    never trimmed to the first :data:`MAX_PARALLEL_TASKS` (S7 clause 1).

    ``action`` IS READ FIRST (#610 D1): a ``resume`` takes its own, narrower
    parse (:func:`_parse_resume`), and a ``run`` takes the parse below, which
    answers every call without an ``id`` exactly as it did before #610.
    """

    action = _parse_action(args.get("action"))
    if action == "resume":
        return _parse_resume(args)
    if not _absent(args.get("id")):
        raise AgentCallError(_ID_IS_FOR_RESUME)

    profile = args.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        raise AgentCallError("'profile' is required and must be a profile name.")

    mode = _parse_mode(args.get("mode"))
    # :func:`_absent` and not ``in args``: an explicit ``null`` — and the ``""``
    # / ``[]`` a materialising provider sends for the same omission — is absence,
    # not a value the model asserted. Presence is computed once, up front,
    # because the two mutual-exclusion refusals below both need it.
    has_task = not _absent(args.get("task"))
    has_tasks = not _absent(args.get("tasks"))

    if mode == "single":
        # THE EXCLUSION IS CHECKED BEFORE THE REQUIREMENT, deliberately. The
        # common mistake is a model that wrote 'tasks' and forgot 'mode'; the
        # requirement check would answer that with "'task' is required", which
        # is true and useless. This message names the actual fix. A call with
        # NEITHER key still falls through to the requirement check below, so no
        # refusal is lost by the ordering.
        if has_tasks:
            raise AgentCallError(
                "'tasks' is for mode='parallel' or mode='chain'. For one task "
                "pass 'task'; to run these tasks set 'mode'."
            )
        task = args.get("task")
        if not isinstance(task, str) or not task.strip():
            raise AgentCallError(
                "'task' is required and must describe the whole job — the child "
                "has no conversation history to fall back on."
            )
        # Rule 3's normalization: one storage shape for both doors (§4.2).
        tasks = (task,)
    else:
        if has_task:
            raise AgentCallError(
                f"mode={mode!r} takes 'tasks', not 'task'. Use one or the other."
            )
        tasks = _parse_tasks(args.get("tasks"), mode=mode)

    # Rule 5. BYTES, at parse time, on every submitted task. Without it a 5 MB
    # task string reaches ``create_subprocess_exec`` and fails with an opaque
    # ``OSError: [Errno 7] Argument list too long`` that the model reads as an
    # unexplained spawn failure — a P2 latent defect this closes for free. The
    # executor re-applies it to each RENDERED chain step, which is the case a
    # parse-time check alone cannot catch (``chain.check_task_size``).
    for index, task in enumerate(tasks, start=1):
        try:
            check_task_size(task)
        except TaskTooLarge as exc:
            where = "'task'" if mode == "single" else f"tasks[{index}]"
            raise AgentCallError(f"{where}: {exc}") from exc

    # Rule 6. Step 1 has no previous output, so an empty substitution would
    # silently change what the instruction says — an error the model reads
    # rather than a value it never notices (§3.2). ``uses_previous`` ignores
    # ``{{previous}}``, so a step-1 task that writes ABOUT the token is legal.
    if mode == "chain" and uses_previous(tasks[0]):
        raise AgentCallError(
            "tasks[1] uses {previous}, but there is no previous step. Write the "
            "first task out in full, or escape the token as {{previous}} if you "
            "meant to talk about it."
        )

    cwd = args.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
        raise AgentCallError("'cwd' must be a directory path inside your own.")

    # ``timeout_ms`` IS NEVER READ, AND A CALL CARRYING IT STILL RUNS (#604,
    # ADR-0276). The schema no longer offers it, but a model that learned it
    # from an older session, or a provider replaying an old call, can still
    # send it, and the kernel validator delivers unknown keys untouched (see
    # the docstring). Refusing would cost a round trip and teach the model
    # nothing it can act on; ignoring loses nothing, because there is no time
    # limit left for the value to set. ``background`` is the opposite case: it
    # names something that would NOT happen, so it is refused below.
    if args.get("background"):
        raise AgentCallError(_BACKGROUND_REFUSED)

    return AgentCall(
        profile=profile.strip(),
        tasks=tasks,
        mode=mode,
        cwd=cwd.strip() if isinstance(cwd, str) else None,
    )


def _truncate(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def build_roster(profiles: Iterable[AgentProfile]) -> str:
    """Render the profile roster the tool description advertises.

    A DELIBERATE DIVERGENCE FROM PI, which does not do this: ``agents.ts`` still
    exports ``formatAgentList`` but ``index.ts`` never imports it, so pi's parent
    model has to guess profile names. Naming them is the difference between one
    delegation and three failed ones.

    Bounded twice — per entry and in total — because this string is re-sent on
    every request of the parent session.
    """

    lines: list[str] = []
    total = 0
    for profile in list(profiles)[:MAX_ROSTER]:
        description = _truncate(
            profile.description or "(no description)", ROSTER_DESCRIPTION_CHARS
        )
        line = f"- {profile.name} ({profile.scope}): {description}"
        encoded = len(line.encode("utf-8")) + 1
        if total + encoded > ROSTER_MAX_BYTES:
            break
        lines.append(line)
        total += encoded
    return "\n".join(lines)


def build_description(roster: str) -> str:
    """The full tool description: the contract, then the roster."""

    body = roster.strip()
    if not body:
        return f"{_DESCRIPTION_HEAD}\n\n{_no_profiles_message()}"
    return f"{_DESCRIPTION_HEAD}\n\nAvailable profiles:\n{body}"


def create_agent_tool(
    *,
    description: str,
    execute: Any,
) -> AgentTool:
    """Build the ``agent`` tool.

    ``execution_mode="sequential"`` IS A SECURITY SETTING, not a performance
    one, and mirrors what ``tools/bash.py``'s ``create_bash_tool`` declares. The
    kernel's ``_execute_tool_calls``
    downgrades the WHOLE BATCH to sequential when any tool in it declares this
    (kernel ``types.py:47-56``; ``loop.py:711-721``), which closes the
    concurrent-modal hazard described in the module docstring at zero
    product-core cost. It is belt-and-braces with the hook location and with
    ``consent.py``'s process-wide lock — and because the description is
    re-injected with :func:`dataclasses.replace` on every
    ``before_agent_start``, ``test_execution_mode_survives_dataclasses_replace``
    pins that the re-injection cannot silently drop it.
    """

    tool = AgentTool(
        name=AGENT_TOOL_NAME,
        # Issue #120 — the other tool the old hard-coded sentence omitted. It
        # is default-OFF and depth-gated, so when it is absent the derived list
        # simply does not carry this line; that is the whole difference between
        # a derived list and the literal it replaced, which named seven tools
        # unconditionally and these two never.
        prompt_snippet="Delegate a scoped sub-task to a subagent",
        description=description,
        parameters=dict(AGENT_TOOL_PARAMETERS),
        execute=execute,
        execution_mode="sequential",
    )
    # ADR-0253 (#188): ``delegation`` provenance keeps ADR-0197's treatment —
    # the permission gate neither prompts for nor blocks THIS object; spawn
    # consent is ``consent.py``'s. A third-party tool named ``agent`` gets no
    # such pass: it is an unknown tool, and unknown tools are mutating.
    return mark_builtin(tool, "delegation")


def with_description(tool: AgentTool, description: str) -> AgentTool:
    """Re-stamp the roster onto a frozen tool.

    :class:`AgentTool` is a frozen dataclass and ``register_tool`` fixes the
    description at registration time, so a roster that changed (a new profile
    file, a ``/reload``) can only be published by replacing the tool. Done from
    a ``before_agent_start`` handler (``harness/core.py:1522``), which runs
    BEFORE the per-turn ``AgentContext`` is built (``:4117-4133``) — a
    ``turn_start`` handler would already be too late for the current turn.
    """

    # The replacement is a NEW object, so it carries no provenance of its own
    # (ADR-0253): carry the original's over, or the gate would treat this
    # turn's ``agent`` tool as an unknown, mutating one — fail-closed, but a
    # regression. Carried, never invented: a tool aelix did not build stays
    # unmarked through the copy.
    replaced = dataclasses.replace(tool, description=description)
    provenance = builtin_provenance(tool)
    return replaced if provenance is None else mark_builtin(replaced, provenance)


def format_partial(text: str) -> ToolResult:
    """A streaming partial for the parent's own tool card (``ctx.on_partial``)."""

    return ToolResult(content=[TextContent(text=text)])


USAGE_LINE_MAX_CELLS = 76
"""What one line of a tool card is, in TERMINAL CELLS — the renderer's own unit.

CELLS, NOT CHARACTERS, and the distinction is the whole of finding M1. A CJK
ideograph or an emoji occupies two cells and one character, so the two counts
diverge by a factor of two on exactly the input a hostile child would choose.
This module measured characters while ``tui/render.py`` measured cells
(``rich.cells.cell_len`` / ``set_cell_size``, ``render.py:340-343``): a 60-CJK
model flattened to 40 characters — inside the budget, so nothing was dropped —
and 80 cells, so the renderer cut INSIDE the model term and took the status and
the posture with it. See :func:`_usage_line`.

NOT a policy this module chose. ``tui/render.py``'s ``_truncate_lines`` caps
every card line at ``max_line_width = 76`` cells and appends ``…``
(``render.py:324-344``), and that number is FIXED: it does not grow with the
terminal, so a wider window buys this line nothing. Measured on a real
delegation at both 120 and 200 columns, the footer came out identically cut at
76 cells, losing its last field.

So the bound is real whether or not this function respects it, and the only
question is WHICH field pays for it. Left to the renderer the answer is
"whatever is last", which is a layout accident; :func:`_usage_line` spends the
budget deliberately instead, and the fields it drops are dropped whole rather
than sliced mid-token.

Spelled here rather than imported: ``render.py`` is product-core and the number
is private to it. A drift makes this line one field shorter or one field
truncated — visibly wrong, never wrong in the permissive direction — and
``test_the_result_card_shows_the_whole_model`` measures the two against each
other through the real renderer rather than trusting this comment."""

_USAGE_FIELD_MAX_CELLS = 40
"""Bound on each free-text term — the profile name and the model — in CELLS.

``SubagentResult.model`` is read verbatim off the child's own ``message_end``
(``stream.py:1048-1050`` → ``envelope.py:1048``), which makes it attacker-supplied
exactly like ``current_tool``; ``profile`` is a filename stem and is unbounded
for a duller reason. 40 fits every real provider id (the longest in the shipped
registry is 32) while denying either one the ability to spend the whole line."""

_ELLIPSIS = "…"
"""One cell, and the same marker ``panel._flatten`` and ``tui/render.py`` use, so
a term shortened here is not visually distinguishable from one the renderer
shortened."""

_USAGE_FIELD_MIN_CELLS = 6
"""How far a free-text term may be squeezed before the squeezing stops.

Reached only after every optional field has already gone, so it is the last
lever between a wide term and a head field falling off the card. Small enough
that the head ALWAYS fits: ``"[agent " + 6 + " · " + 6 + " · " + 13 + " · " +
17 + "]"`` is 58 cells against a budget of 76, taking the widest status
(``did not start``) and the widest posture (``auto-accept-edits``). Big enough
that what survives is still recognisable as a name rather than an ellipsis."""

# Fields dropped, in this order, when the composed line will not fit
# :data:`USAGE_LINE_MAX_CELLS`. LOWEST VALUE FIRST, and the survivors keep their
# natural order — this is a drop list, not a render order.
#
# ``turns`` goes first: it is the least actionable number here and the card's
# own body already shows what the child did. ``tokens`` is next because it is by
# far the widest field (``45231 in / 3821 out`` is 19 characters) and the cost
# beneath it is the same fact in the unit anyone actually checks. Cost and
# elapsed are last because they are what a reader scans a finished delegation
# for. Nothing before them in the line can be dropped at all: profile, model,
# status and permission are the identity and the outcome, and a footer that
# elides those is not a shorter footer, it is a different one.
_USAGE_DROP_ORDER = ("turns", "tokens", "cost", "elapsed")


def _usage_line(result: SubagentResult, *, status: str | None = None) -> str:
    """The ``[agent … ]`` footer both renderers print.

    ``status`` overrides ``result.status`` and exists for exactly one caller:
    ``aggregate._member_block`` (``aggregate.py:249-310``) rendering a member
    that NEVER STARTED. Its envelope carries ``status="error"`` like every other
    refusal, so without the override the footer says ``error`` one line under a
    heading that says ``did not start``. Keyword-only with a ``None`` default so
    the single-mode path — pinned byte-for-byte by
    ``test_the_p2_argument_shape_is_unchanged`` — cannot be changed by adding it.

    THE MODEL LEADS, AND THAT IS THE FIX (live QA). It used to sit second-to-last,
    which on a real delegation meant it was the field ``tui/render.py`` cut:
    ``[agent explorer · ok · yolo · 1 turns · 3533 in / 4 out · $0.0036 · claude-…``
    at 76 cells, identically at 120 and at 200 columns. It now follows the
    profile, which is where the live status row has always put it
    (``progress.format_status_row``) — so the two surfaces read the same way —
    and the line composes itself against :data:`USAGE_LINE_MAX_CELLS` so the
    fields that go are chosen (:data:`_USAGE_DROP_ORDER`) rather than sliced.

    AND THE BUDGET IS COUNTED IN CELLS, which is what makes that ordering safe
    (finding M1). The model is the CHILD's own bytes, and it now sits ahead of
    the status and the posture — so if this function measured the line in a unit
    the renderer does not use, the child could choose a string that fits the
    budget on paper and overflows it on screen, pushing the two facts that
    describe its own behaviour off the card. Measured, before the fix: a 60-CJK
    ``model`` is 40 characters (nothing dropped) and 80 cells (renderer cuts
    mid-term), and the drawn card lost both ``error`` and ``yolo``.

    So when nothing optional is left to drop, the FREE-TEXT TERMS are squeezed
    rather than the line being handed to the renderer to cut — the model first,
    because it is the least trustworthy value on the row, and the profile after
    it. :data:`_USAGE_FIELD_MIN_CELLS` is low enough that the head always fits,
    which makes "no value on this row can hide the status or the posture" a
    property rather than a hope.

    A RESULT THAT FITS IS UNCHANGED, byte for byte. Every no-model footer the
    suite pins — ``[agent scout · ok · plan · 0.0s]`` and its siblings — is well
    inside the budget and renders exactly as it did, because the model term is
    still omitted when there is none and nothing is dropped while there is room.
    """

    usage = result.usage
    profile = _usage_field(result.profile)
    model = _usage_field(result.model)

    tail: list[tuple[str, str]] = []
    if usage.turns:
        tail.append(("turns", f"{usage.turns} turns"))
    if usage.input or usage.output:
        tail.append(("tokens", f"{usage.input} in / {usage.output} out"))
    if usage.cost:
        tail.append(("cost", f"${usage.cost:.4f}"))
    tail.append(("elapsed", f"{result.elapsed_ms / 1000:.1f}s"))

    def compose(dropped: set[str]) -> str:
        # The model term is GATED, and not stylistically.
        # ``test_the_p2_argument_shape_is_unchanged`` pins the single-mode footer
        # byte-for-byte against a stub channel whose result carries no model; an
        # ungated segment breaks it. Gated, it stays green and every REAL
        # delegation gains the one fact that made a silent model substitution
        # impossible to notice.
        head = [f"agent {profile}", *([model] if model else []), status or result.status]
        if result.permission_mode:
            head.append(result.permission_mode)
        kept = [text for name, text in tail if name not in dropped]
        return "[" + " · ".join([*head, *kept]) + "]"

    dropped: set[str] = set()
    text = compose(dropped)
    for name in _USAGE_DROP_ORDER:
        if cell_len(text) <= USAGE_LINE_MAX_CELLS:
            return text
        dropped.add(name)
        text = compose(dropped)

    # Nothing optional is left. Squeeze the free-text terms — the MODEL first,
    # because it is the one value here the child chose — instead of letting the
    # renderer take the tail, which at this point IS the status and the posture.
    if model:
        model = _squeeze(model, _room_for(model, text))
        text = compose(dropped)
    if cell_len(text) > USAGE_LINE_MAX_CELLS:
        profile = _squeeze(profile, _room_for(profile, text))
        text = compose(dropped)
    return text


def _usage_field(value: object) -> str:
    """One free-text term: de-fanged, whitespace-collapsed, bounded in CELLS.

    ``model`` is the child's own bytes; ``profile`` is a filename stem, which
    POSIX lets contain ``\\n`` just as happily. This string is joined into the
    tool result's TEXT, which ``tui/render.py`` splits on newlines into card rows
    — so either one could otherwise add rows to the card and carry a raw ESC into
    them. ``_flatten`` is the same helper the panel gives every child-authored
    string; its own bound is in characters, so the cell bound is applied after it.

    ``isinstance`` rather than truthiness, matching ``consent._model_row``: the
    channel is an injectable seam, and a raise out of ``render_subagent_result``
    is exactly the shape ``test_a_poisoned_usage_line_does_not_orphan_the_child``
    exists to prevent. A non-``str`` costs the row a term, never an exception.
    """

    if not isinstance(value, str):
        return ""
    return _squeeze(
        _flatten(value, limit=_USAGE_FIELD_MAX_CELLS), _USAGE_FIELD_MAX_CELLS
    )


def _squeeze(value: str, cells: int) -> str:
    """``value`` in at most ``cells`` terminal cells, ellipsised if it was cut.

    ``set_cell_size`` is the renderer's own primitive (``render.py:341``), so a
    term this function shortens is shortened exactly as far as the renderer would
    have measured it — no off-by-one between the budget and the draw.
    """

    if cell_len(value) <= cells:
        return value
    return set_cell_size(value, max(cells - 1, 0)) + _ELLIPSIS


def _room_for(field: str, text: str) -> int:
    """Cells ``field`` may keep for ``text`` to land inside the budget.

    Floored at :data:`_USAGE_FIELD_MIN_CELLS`: past that the term stops being a
    name, and the head is guaranteed to fit at the floor anyway.
    """

    overflow = cell_len(text) - USAGE_LINE_MAX_CELLS
    return max(cell_len(field) - overflow, _USAGE_FIELD_MIN_CELLS)


UNFINISHED_HEAD = "The delegated agent did not finish: {reason}"
"""The first line a model reads about a started child that did not finish
(ADR-0276 D11). The status used to come LAST, in the footer, after a summary
that read like an answer."""

DO_NOT_REDELEGATE = (
    "Do not delegate the same task again from scratch — it already did the work "
    "below. Check the changed files and its tool calls first."
)
"""The phase-0 retry guidance (ADR-0276 D11), kept for a child with no session.

Printed ONLY when there is work to point at — a tool call or a written file. A
child that failed on its first request (no API key, a bad model id) did nothing,
and "it already did the work" would be as false of it as it is of a refusal
(D18, design critique 5): the right move there is to fix the cause and retry.

#610 D4 replaced it with :data:`RESUME_GUIDANCE` wherever there is something to
resume. This sentence is left for the child that has nothing to resume — a
parent with no session file, or a child file that could not be allocated — where
"continue it" would name a call that can only be refused."""

RESUME_GUIDANCE = (
    "Do not delegate the same task again from scratch — it already did the work "
    "below. Check the changed files and its tool calls; if it should go on, "
    'continue it with agent(action="resume", profile={profile}, id={id}, task=…).'
)
"""What #610 D4 put where :data:`DO_NOT_REDELEGATE` was, for a child WITH a session.

The call is spelled out, with the profile and the id filled in, because the
model's next useful move is that call and the values are on this result; the
instruction is the one thing left to the model. Printed under exactly the
condition :data:`DO_NOT_REDELEGATE` was. A batch member's block gets neither —
``aggregate._unfinished_member_block`` keeps to the facts, by design — and its
``session`` and the tool description carry the rest."""


def call_literal(value: object) -> str:
    """``value`` as the model would write it in a call: a JSON string on one line.

    For the values this module and the hook put INTO a call the model is asked
    to make (#610 D4). The profile is a filename stem, which may hold a newline
    or a quote, and the id comes back off a record: each is folded onto one line
    (:func:`~aelix_agents.envelope.child_line`), stripped of lone surrogates —
    a stem that is not valid UTF-8 decodes to them, and they cannot be encoded
    into a request — and quoted, so what the model copies is exactly one
    argument. Not bounded: a name cut short is a name that no longer matches.
    """

    text = child_line(value) if isinstance(value, str) else ""
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cs")
    return json.dumps(text, ensure_ascii=False)


def retry_guidance(result: SubagentResult) -> str:
    """The line under a started child's "did not finish" heading (#610 D4).

    :data:`RESUME_GUIDANCE` when the child has a session to resume, naming its
    profile and its id; :data:`DO_NOT_REDELEGATE` when it has none.
    """

    if not result.session_id:
        return DO_NOT_REDELEGATE
    return RESUME_GUIDANCE.format(
        profile=call_literal(result.profile), id=call_literal(result.session_id)
    )

NO_TEXT_YET = "(it wrote no text before it stopped — see its tool calls)"
"""What stands for the partial answer of a child that wrote none of its own."""

NO_TEXT = "(it wrote no text before it stopped)"
"""The same, for a child that made no tool call either: there is nothing to see
(a child that failed on its first request — no API key, a bad model id)."""

MAX_FILE_LIST_BYTES = 2048
"""The single-mode budget for the written-file lists, labels included (D11)."""

FILES_VIA_BASH = "files changed through bash or other tools are not listed"
"""The limit every file list states, because nothing can lift it: what a shell
command writes is not in its arguments (ADR-0276 D8), and only the writer tools
are read — a file an MCP or extension tool wrote is not listed either."""


def render_file_lists(
    changed: Sequence[str],
    unconfirmed: Sequence[str],
    overflow: int,
    *,
    budget: int,
) -> list[str]:
    """The written-file block: one path per line, inside ``budget`` bytes.

    ONE PATH PER LINE because a comma-joined list is ambiguous for a path that
    contains ", " (design critique 8); the paths were collapsed onto one line
    each by the reducer, so the line count is structural. Labels are paid for out
    of the same budget, and a path that does not fit is counted into the closing
    ``(+N more files; …)`` with the ones the reducer already could not keep —
    never dropped in silence. The closing line is always there: what ``bash``
    (or any tool but the writers) wrote is never listed, and a list that looks
    complete would say otherwise. Each path is folded onto one line again here
    (:func:`~aelix_agents.envelope.child_line`), whoever built the result.
    """

    lines: list[str] = []
    used = 0
    hidden = overflow
    sections = (("Files changed:", changed), ("Unconfirmed (stopped mid-write):", unconfirmed))
    for label, paths in sections:
        if not paths:
            continue
        label_cost = len(label.encode("utf-8", "surrogatepass")) + 1
        for index, path in enumerate(paths):
            line = f"  {child_line(path)}"
            cost = len(line.encode("utf-8", "surrogatepass")) + 1
            extra = label_cost if index == 0 else 0
            if used + extra + cost > budget:
                hidden += len(paths) - index
                break
            if extra:
                lines.append(label)
            lines.append(line)
            used += extra + cost
    if not lines and not hidden:
        return [f"Files changed: none recorded ({FILES_VIA_BASH})"]
    if hidden:
        lines.append(f"(+{hidden} more files; {FILES_VIA_BASH})")
    else:
        lines.append(f"({FILES_VIA_BASH})")
    return lines


def did_work(result: SubagentResult) -> bool:
    """Is there anything to point the parent at — a tool call or a written file?"""

    return bool(
        result.tool_trail
        or result.changed_files
        or result.unconfirmed_files
        or result.files_overflow
    )


def _render_unfinished(result: SubagentResult) -> str:
    """The D11 template: a STARTED child that did not finish (ADR-0276).

    Reason first, then what to do with it, then the evidence — its partial
    answer, the error that answer does not already say (capped, D17), its tool
    calls, the files it wrote and the session that holds the rest. Never the
    session's PATH (ADR-0243 §5): the id names it without putting a home
    directory into the model's context.

    EVERY CHILD-AUTHORED PART IS CLEANED (#604 review) — the reason and the
    session id onto one line (:func:`~aelix_agents.envelope.child_line`), the
    answer, the error, the note and the trail through
    :func:`~aelix_agents.envelope.child_prose`. Which blocks appear is decided
    on the values as they came — whether the summary is the error, whether the
    error repeats it — with two exceptions taken on what the model will read
    (#604 review round 2): a partial answer with nothing left once cleaned is
    no text (:func:`~aelix_agents.envelope.says_nothing`), and the error is
    compared with the reason as both are shown. The same text is what the
    cancel report quotes (D14).

    The ``Error:`` block is left out when the summary already says it, and
    when it is one line that IS the reason: a one-line provider 400 was printed
    twice, as the heading's reason and again as the note under it.
    """

    summary = result.summary or NO_OUTPUT
    reason = result.reason or REASON_UNSPECIFIED
    worked = did_work(result)
    head = UNFINISHED_HEAD.format(reason=child_line(reason))
    # #610 D4 — "continue it with agent(action="resume", …)" when it has a
    # session, the phase-0 sentence when it has none; same condition either way.
    blocks = [f"{head}\n{retry_guidance(result)}" if worked else head]
    error = result.error
    if (
        summary == NO_OUTPUT
        or summary_is_the_error(summary, error, truncated=result.truncated)
        or says_nothing(summary, truncated=result.truncated)
    ):
        # The summary is the fallback chain's, not the child's — or nothing the
        # model could read once cleaned: say so, and let the error be printed
        # as the error.
        blocks.append(f"Partial result:\n{NO_TEXT_YET if result.tool_trail else NO_TEXT}")
        summary = NO_OUTPUT
    else:
        blocks.append(f"Partial result:\n{child_prose(summary)}")
    if (
        error
        and not error_repeats_summary(summary, error, truncated=result.truncated)
        and not error_says_only_the_reason(error, result.reason)
    ):
        note, _cut, _lost = cap_summary(child_prose(error), MAX_ERROR_NOTE_BYTES)
        blocks.append(f"Error: {note}")
    if result.note:
        # Never set on a failure by the bundled envelope (D4's note is a
        # success's), and printed if a third-party one sets it: a fact the
        # envelope carries is not dropped by the renderer.
        blocks.append(f"Note: {child_prose(result.note)}.")
    if result.tool_trail:
        trail = child_prose(result.tool_trail)
        blocks.append(f"Tool calls ({count_trail_calls(trail)}):\n{trail}")
    if worked:
        blocks.append(
            "\n".join(
                render_file_lists(
                    result.changed_files,
                    result.unconfirmed_files,
                    result.files_overflow,
                    budget=MAX_FILE_LIST_BYTES,
                )
            )
        )
    if result.session_id:
        blocks.append(f"Session: {child_line(result.session_id)}")
    blocks.extend(_tail_notes(result))
    blocks.append(_usage_line(result))
    return "\n\n".join(blocks)


def _tail_notes(result: SubagentResult) -> list[str]:
    """The notes every rendering ends with, before the footer."""

    notes: list[str] = []
    if result.dropped_tools:
        notes.append(
            "Tools not granted to this agent: "
            + ", ".join(result.dropped_tools)
            + " (it holds no tool you do not hold)."
        )
    if result.dropped_lines:
        notes.append(
            f"{result.dropped_lines} oversize output line(s) were dropped."
        )
    return notes


def render_subagent_result(result: SubagentResult) -> ToolResult:
    """Fold the envelope into what the parent's model reads.

    ``details`` carries the UNCAPPED raw material (finding B8), so a live tool
    card can show what the 50 KiB cap removed without the model paying for it.
    It is never persisted (#168); the durable copy is the child's session file,
    which is what the truncation marker points at when there is one (#199).

    ``is_error`` follows ``result.ok`` and nothing else. The envelope has
    already tightened its own outcome (``envelope.build_result``: a child that
    exits 0 while its stream carries ``stop_reason: "error"`` is a failure), so
    re-deriving a verdict here could only disagree with it.

    TWO TEMPLATES (ADR-0276 D11, D18). A child that STARTED and did not finish
    gets :func:`_render_unfinished` — reason, partial answer, error, tool calls,
    files and session. Every other result — a success, a refusal, a decline, a
    child that never got a process — renders as it always did: a success needs
    none of that, and "it did the work below" is false of a child that never
    ran. ONE line changed that (#610 D2, amending D18): a success with a session
    names it, ``Session: <id>`` above the footer, because that id is what
    ``agent(action="resume")`` takes and a finished agent is one a model may
    want to continue. Nothing else of the run's evidence joins it, and a result
    with no session — every refusal and decline — is byte for byte as before.

    The ``Error:`` note is left out when it would only repeat the summary —
    including when the summary is that error cut at ``output_cap``
    (:func:`~aelix_agents.envelope.error_repeats_summary`) — and is capped at
    :data:`~aelix_agents.envelope.MAX_ERROR_NOTE_BYTES` (D17). Below the cap,
    which is every refusal, nothing changes.
    """

    if result.started and not result.ok:
        text = _render_unfinished(result)
    else:
        body = result.summary or "(no output)"
        notes: list[str] = []
        if result.error and not error_repeats_summary(
            body, result.error, truncated=result.truncated
        ):
            note, _cut, _lost = cap_summary(result.error, MAX_ERROR_NOTE_BYTES)
            notes.append(f"Error: {note}")
        if result.note:
            # A fact about a run that SUCCEEDED (ADR-0276 D4) — labelled ``Note:``
            # and never ``Error:``, so a finished answer is not read as a failure.
            notes.append(f"Note: {result.note}.")
        notes.extend(_tail_notes(result))
        if result.ok and result.session_id:
            # #610 D2 — after the summary and its notes, above the footer: the
            # id a resume takes. Only a success: a failure that started has the
            # template above, and one that never started has nothing to resume.
            notes.append(f"Session: {child_line(result.session_id)}")
        text = "\n\n".join([body, *notes, _usage_line(result)])
    return ToolResult(
        content=[TextContent(text=text)],
        details=result.details,
        is_error=not result.ok,
    )


def render_roster_for(profiles: Sequence[AgentProfile]) -> str:
    """Convenience for the "unknown profile" refusal path."""

    roster = build_roster(profiles)
    return roster or _no_profiles_message()


__all__ = [
    "AGENT_TOOL_NAME",
    "AGENT_TOOL_PARAMETERS",
    "DESCRIPTION_HEAD_MAX_BYTES",
    "MAX_PARALLEL_TASKS",
    "MAX_ROSTER",
    "RESUME_TASK_MAX_BYTES",
    "ROSTER_DESCRIPTION_CHARS",
    "ROSTER_MAX_BYTES",
    "AgentAction",
    "AgentCall",
    "AgentCallError",
    "PendingSpawn",
    "build_description",
    "build_roster",
    "call_literal",
    "create_agent_tool",
    "format_partial",
    "parse_agent_call",
    "render_roster_for",
    "render_subagent_result",
    "with_description",
]
