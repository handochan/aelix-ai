"""The model-facing ``agent`` tool — ADR-0197 §(k)/§(i).

WHAT IS AND IS NOT IN THIS FILE. The tool's SHAPE (name, schema, roster
description, execution mode), its ARGUMENT validation, and the rendering of a
:class:`~aelix_coding_agent.subagent_contract.SubagentResult` back to the model
live here. The CONSENT DECISION does not — it is taken in the extension's own
``tool_call`` hook (``extension.py``) and reaches ``execute()`` only as a
:class:`~aelix_agents.consent.SpawnGrant` popped out of a private dict. Three
measured reasons, all of which make the hook the only correct location:

1. **``execute()`` is not serialised.** The kernel runs ``before_tool_call`` in
   the SEQUENTIAL prep phase (``loop.py:514-535``, driven from ``:825-860``) but
   ``execute`` under ``asyncio.gather`` (``loop.py:893``), with
   ``tool_execution = "parallel"`` by default (``harness/core.py:271``). Two
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
   error result (``loop.py:522-535``). An ``execute()`` refusal is just another
   error string.

:data:`AGENT_TOOL_NAME` is imported from :mod:`aelix_agents.print_channel`
rather than declared here: the spawn path must subtract it from every child's
grant and must be able to do that without importing this module, which would
invert the dependency and drag the roster machinery into the spawn path.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolResult
from rich.cells import cell_len, set_cell_size

from aelix_agents.chain import TaskTooLarge, check_task_size, uses_previous
from aelix_agents.panel import _flatten
from aelix_agents.print_channel import AGENT_TOOL_NAME

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.subagent_contract import (
        ResolvedProfile,
        SubagentMode,
        SubagentResult,
    )

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

MIN_TIMEOUT_MS = 1000

MAX_TIMEOUT_MS = 1_800_000
"""Ceiling on a per-task wall clock, in ms — 30 minutes.

Deliberately EQUAL to ``batch.MAX_BATCH_WALL_MS``: one task and a whole batch are
bounded by the same number, so a model cannot buy more wall clock by splitting a
job across tasks or by merging tasks into one. Declared here and not only as the
schema's ``maximum`` because the kernel's ``validate_tool_arguments`` is
"additive, never strips" (``aelix_ai/tools.py``) — the schema is advice to the
provider and :func:`parse_agent_call` is the gate."""

MAX_PARALLEL_TASKS = 8
"""Tasks one ``agent`` call may carry (S3, and S7 clause 1).

Below ``runtime.MAX_DELEGATIONS_PER_PROMPT`` (12) ON PURPOSE: a fresh prompt can
always run one full batch and still holds four delegations, so the per-prompt
budget can never make a legal batch unrunnable from the very first call.

A batch above this is a malformed CALL, not a batch to be trimmed:
:func:`parse_agent_call` raises, the extension's ``tool_call`` hook turns that
into a blocked call the model reads (``extension.py:613-616``), and NO process is
created. Trimming to the first eight would silently drop work the model believes
it delegated — the failure mode S7 clause 1 exists to forbid."""

_MODES: tuple[SubagentMode, ...] = ("single", "parallel", "chain")
"""The one spelling of the mode set, shared by the schema's ``enum`` and by
:func:`parse_agent_call`. Two hand-maintained copies is how a schema starts
advertising a mode the parser refuses. Mirrors ``SubagentMode``
(``subagent_contract.py:71``), which is imported for TYPE CHECKING only —
evaluating it at runtime would pull a product-core symbol into this module's
import graph to save one tuple."""

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
                "one string."
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
        "timeout_ms": {
            "type": "integer",
            "minimum": MIN_TIMEOUT_MS,
            "maximum": MAX_TIMEOUT_MS,
            "description": (
                "Optional per-task wall clock. One call is additionally capped "
                "in total, however many tasks it carries."
            ),
        },
    },
    # ``required`` names ONLY ``profile``, and that is not laxity. The real rule
    # is CONDITIONAL — ``task`` for mode=single, ``tasks`` for the other two —
    # and JSON Schema's ``if``/``then`` and ``oneOf`` are not reliably honoured
    # across the provider tool-schema validators aelix targets, so declaring the
    # conditional here would be a rule enforced on some providers and silently
    # absent on others. It is enforced in ``parse_agent_call`` instead, whose
    # refusal the ``tool_call`` hook turns into a blocked call the model reads
    # (``extension.py:613-616``) with no process created.
    #
    # The P2 argument shape is unaffected: ``{"profile": …, "task": …}`` still
    # satisfies ``required``, ``mode`` defaults to "single", and the parse
    # normalizes ``task`` into a 1-tuple.
    "required": ["profile"],
}

_DESCRIPTION_HEAD = (
    "Delegate self-contained tasks to subagents — separate aelix processes, each "
    "running under its own agent profile, its own system prompt and its own "
    "context window.\n"
    "\n"
    "Each one runs to completion and returns ONE summary. There is no "
    "conversation: a child cannot ask you a question and you cannot steer it "
    "mid-run, so every task must be complete on its own. A delegated agent is "
    "READ-ONLY unless the user explicitly approves more at a prompt, and it can "
    "never hold a tool you do not hold, delegate further, or run outside your "
    "working directory.\n"
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
    "is never trimmed to fit. Delegate read-heavy work first."
)

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
    (``consent.py:530-570``) and the same ``_may_widen``
    (``consent.py:446-527``), which is what makes ONE consent decision for the
    whole batch coherent. :class:`~aelix_agents.consent.SpawnGrant` is singular
    by construction (``consent.py:248-255``: one ``profile``, one
    ``source_path``, one ``mode``).
    """

    profile: str
    tasks: tuple[str, ...]
    """ALWAYS at least one, ALWAYS a tuple. ``mode="single"`` normalizes ``task``
    into a 1-tuple at parse time so the single and batch paths downstream are one
    code path rather than two that must be kept in agreement."""
    mode: SubagentMode = "single"
    cwd: str | None = None
    timeout_ms: int | None = None

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
    ``harness/core.py:3944-3946`` explicitly permits a later handler to mutate
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


def _absent(value: object) -> bool:
    """Is this argument ABSENT — including the empty shapes a provider invents?

    Same argument as :func:`_parse_mode`'s ``null`` clause, one step further: a
    provider that materialises every declared property of the schema sends
    SOMETHING for the ones the model omitted, and ``null`` is only its most
    common spelling. An omitted string comes back as ``""`` and an omitted array
    as ``[]`` from providers that fill in type-appropriate zero values.

    Used ONLY for the ``task``/``tasks`` presence pair that drives the two
    mutual-exclusion refusals. That is the case where a materialised empty is
    read as the model ASSERTING the key, producing a refusal it cannot act on —
    ``{"mode": "parallel", "tasks": [...], "task": ""}`` is answered with
    "mode='parallel' takes 'tasks', not 'task'" about a ``task`` the model never
    wrote, so it re-issues the identical call.

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
    :class:`AgentCallError` into a blocked call (``extension.py:613-616``) and
    the kernel renders it as a model-readable immediate error result
    (``loop.py:522-535``). An oversize batch is therefore a refused CALL and is
    never trimmed to the first :data:`MAX_PARALLEL_TASKS` (S7 clause 1).
    """

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

    timeout_ms = args.get("timeout_ms")
    if timeout_ms is not None:
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
            raise AgentCallError("'timeout_ms' must be an integer number of ms.")
        if timeout_ms < MIN_TIMEOUT_MS:
            raise AgentCallError(
                f"'timeout_ms' must be at least {MIN_TIMEOUT_MS}."
            )
        # PER TASK, not per call: the call is additionally bounded in total by
        # the batch executor's own deadline, so a batch cannot buy N × this.
        if timeout_ms > MAX_TIMEOUT_MS:
            raise AgentCallError(
                f"'timeout_ms' must be at most {MAX_TIMEOUT_MS} "
                f"({MAX_TIMEOUT_MS // 60_000} minutes) per task."
            )

    if args.get("background"):
        raise AgentCallError(
            "Background delegation is not available: a background child has no "
            "owner to reap it and no channel to report through. Call agent() "
            "and wait for the result."
        )

    return AgentCall(
        profile=profile.strip(),
        tasks=tasks,
        mode=mode,
        cwd=cwd.strip() if isinstance(cwd, str) else None,
        timeout_ms=timeout_ms,
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
    one, and mirrors ``tools/bash.py:708``. The kernel's ``_execute_tool_calls``
    downgrades the WHOLE BATCH to sequential when any tool in it declares this
    (kernel ``types.py:47-56``; ``loop.py:699-709``), which closes the
    concurrent-modal hazard described in the module docstring at zero
    product-core cost. It is belt-and-braces with the hook location and with
    ``consent.py``'s process-wide lock — and because the description is
    re-injected with :func:`dataclasses.replace` on every
    ``before_agent_start``, ``test_execution_mode_survives_dataclasses_replace``
    pins that the re-injection cannot silently drop it.
    """

    return AgentTool(
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


def with_description(tool: AgentTool, description: str) -> AgentTool:
    """Re-stamp the roster onto a frozen tool.

    :class:`AgentTool` is a frozen dataclass and ``register_tool`` fixes the
    description at registration time, so a roster that changed (a new profile
    file, a ``/reload``) can only be published by replacing the tool. Done from
    a ``before_agent_start`` handler (``harness/core.py:1299``), which runs
    BEFORE the per-turn ``AgentContext`` is built (``:4117-4133``) — a
    ``turn_start`` handler would already be too late for the current turn.
    """

    return dataclasses.replace(tool, description=description)


def format_partial(text: str) -> ToolResult:
    """A streaming partial for the parent's own tool card (``ctx.on_partial``)."""

    return ToolResult(content=[TextContent(text=text)])


USAGE_LINE_MAX_CELLS = 76
"""What one line of a tool card is, in TERMINAL CELLS — the renderer's own unit.

CELLS, NOT CHARACTERS, and the distinction is the whole of finding M1. A CJK
ideograph or an emoji occupies two cells and one character, so the two counts
diverge by a factor of two on exactly the input a hostile child would choose.
This module measured characters while ``tui/render.py`` measured cells
(``rich.cells.cell_len`` / ``set_cell_size``, ``render.py:160-163``): a 60-CJK
model flattened to 40 characters — inside the budget, so nothing was dropped —
and 80 cells, so the renderer cut INSIDE the model term and took the status and
the posture with it. See :func:`_usage_line`.

NOT a policy this module chose. ``tui/render.py``'s ``_truncate_lines`` caps
every card line at ``max_line_width = 76`` cells and appends ``…``
(``render.py:144-164``), and that number is FIXED: it does not grow with the
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
(``stream.py:561-563`` → ``envelope.py:384``), which makes it attacker-supplied
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
    ``aggregate._member_block`` (``aggregate.py:170-196``) rendering a member
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

    ``set_cell_size`` is the renderer's own primitive (``render.py:161``), so a
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


def render_subagent_result(result: SubagentResult) -> ToolResult:
    """Fold the envelope into what the parent's model reads.

    ``details`` carries the UNCAPPED raw material (finding B8) so the truncation
    marker in ``summary`` is true on both doors — the tool card can show
    everything the 50 KiB cap removed without the model paying for it.

    ``is_error`` follows ``result.ok`` and nothing else. The envelope has
    already tightened its own outcome (``envelope.build_result``: a child that
    exits 0 while its stream carries ``stop_reason: "error"`` is a failure), so
    re-deriving a verdict here could only disagree with it.
    """

    body = result.summary or "(no output)"
    notes: list[str] = []
    if result.error and result.error not in body:
        notes.append(f"Error: {result.error}")
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
    "MAX_TIMEOUT_MS",
    "MIN_TIMEOUT_MS",
    "ROSTER_DESCRIPTION_CHARS",
    "ROSTER_MAX_BYTES",
    "AgentCall",
    "AgentCallError",
    "PendingSpawn",
    "build_description",
    "build_roster",
    "create_agent_tool",
    "format_partial",
    "parse_agent_call",
    "render_roster_for",
    "render_subagent_result",
    "with_description",
]
