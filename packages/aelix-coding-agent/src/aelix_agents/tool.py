"""The model-facing ``agent`` tool — ADR-0197 §(k)/§(i).

WHAT IS AND IS NOT IN THIS FILE. The tool's SHAPE (name, schema, roster
description, execution mode), its ARGUMENT validation, and the rendering of a
:class:`~aelix_coding_agent.subagent_contract.SubagentResult` back to the model
live here. The CONSENT DECISION does not — it is taken in the extension's own
``tool_call`` hook (``extension.py``) and reaches ``execute()`` only as a
:class:`~aelix_agents.consent.SpawnGrant` popped out of a private dict. Three
measured reasons, all of which make the hook the only correct location:

1. **``execute()`` is not serialised.** The kernel runs ``before_tool_call`` in
   the SEQUENTIAL prep phase (``loop.py:510-531``, driven from ``:809-844``) but
   ``execute`` under ``asyncio.gather`` (``loop.py:877``), with
   ``tool_execution = "parallel"`` by default (``harness/core.py:247``). Two
   modals from one batch collide on ``tui/chrome.py:518``'s single ``_modal``
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
   error result (``loop.py:518-531``). An ``execute()`` refusal is just another
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

from aelix_agents.print_channel import AGENT_TOOL_NAME

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.subagent_contract import (
        ResolvedProfile,
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

MIN_TIMEOUT_MS = 1000

AGENT_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "profile": {
            "type": "string",
            "description": "Agent profile name (see the roster below).",
        },
        "task": {
            "type": "string",
            "description": (
                "The complete task. The child has NO conversation history "
                "and cannot ask you anything: state the goal, the relevant "
                "paths and the definition of done in this one string."
            ),
        },
        "cwd": {
            "type": "string",
            "description": (
                "Optional working directory; must be inside the parent's "
                "working directory."
            ),
        },
        "timeout_ms": {"type": "integer", "minimum": MIN_TIMEOUT_MS},
    },
    "required": ["profile", "task"],
}

_DESCRIPTION_HEAD = (
    "Delegate one self-contained task to a subagent — a second aelix process "
    "running under its own agent profile, its own system prompt and its own "
    "context window.\n"
    "\n"
    "It runs to completion and returns ONE summary. There is no conversation: "
    "the child cannot ask you a question and you cannot steer it mid-run, so "
    "'task' must be complete on its own. A delegated agent is READ-ONLY unless "
    "the user explicitly approves more at a prompt, and it can never hold a "
    "tool you do not hold, delegate further, or run outside your working "
    "directory."
)

_NO_PROFILES = (
    "No agent profiles are available. Ask the user to create one under "
    "~/.aelix/agent/agents/<name>.md before delegating."
)


class AgentCallError(ValueError):
    """A malformed ``agent`` call. The message is written FOR THE MODEL.

    Raised during hook-time validation, so the refusal reaches the model as a
    blocked tool call and no process is ever created.
    """


@dataclass(frozen=True)
class AgentCall:
    """The validated arguments of one ``agent`` tool call."""

    profile: str
    task: str
    cwd: str | None = None
    timeout_ms: int | None = None


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
    ``harness/core.py:3723-3725`` explicitly permits a later handler to mutate
    it). The human approved the task, the profile and the directory that were on
    screen; re-reading them from ``args`` in ``execute()`` would let anything
    that ran in between substitute different ones.
    """

    grant: SpawnGrant
    resolved: ResolvedProfile
    call: AgentCall
    cwd: str
    """The CONTAINED child working directory, already resolved against the
    parent's (``print_channel.resolve_child_cwd``) and shown in the dialog."""


def parse_agent_call(args: Mapping[str, Any]) -> AgentCall:
    """Validate one ``agent`` call. Raises :class:`AgentCallError`.

    The kernel's own ``validate_tool_arguments`` already ran, but it PRESERVES
    unknown keys (``aelix_ai/tools.py`` — "additive, never strips"), so a model
    that invents ``background`` or ``mode`` gets them delivered here. Both are
    rejected explicitly rather than ignored: silently dropping ``background:
    true`` would let a model believe it had started something it had not.
    """

    profile = args.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        raise AgentCallError("'profile' is required and must be a profile name.")
    task = args.get("task")
    if not isinstance(task, str) or not task.strip():
        raise AgentCallError(
            "'task' is required and must describe the whole job — the child "
            "has no conversation history to fall back on."
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

    if args.get("background"):
        raise AgentCallError(
            "Background delegation is not available: a background child has no "
            "owner to reap it and no channel to report through. Call agent() "
            "and wait for the result."
        )
    mode = args.get("mode")
    if mode is not None and mode != "single":
        raise AgentCallError(
            f"mode={mode!r} is not available — one task per call. Issue "
            "several agent() calls if you need several agents."
        )

    return AgentCall(
        profile=profile.strip(),
        task=task,
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
        return f"{_DESCRIPTION_HEAD}\n\n{_NO_PROFILES}"
    return f"{_DESCRIPTION_HEAD}\n\nAvailable profiles:\n{body}"


def create_agent_tool(
    *,
    description: str,
    execute: Any,
) -> AgentTool:
    """Build the ``agent`` tool.

    ``execution_mode="sequential"`` IS A SECURITY SETTING, not a performance
    one, and mirrors ``tools/bash.py:574``. The kernel's ``_execute_tool_calls``
    downgrades the WHOLE BATCH to sequential when any tool in it declares this
    (kernel ``types.py:47-56``; ``loop.py:683-693``), which closes the
    concurrent-modal hazard described in the module docstring at zero
    product-core cost. It is belt-and-braces with the hook location and with
    ``consent.py``'s process-wide lock — and because the description is
    re-injected with :func:`dataclasses.replace` on every
    ``before_agent_start``, ``test_execution_mode_survives_dataclasses_replace``
    pins that the re-injection cannot silently drop it.
    """

    return AgentTool(
        name=AGENT_TOOL_NAME,
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
    a ``before_agent_start`` handler (``harness/core.py:1242``), which runs
    BEFORE the per-turn ``AgentContext`` is built (``:4117-4133``) — a
    ``turn_start`` handler would already be too late for the current turn.
    """

    return dataclasses.replace(tool, description=description)


def format_partial(text: str) -> ToolResult:
    """A streaming partial for the parent's own tool card (``ctx.on_partial``)."""

    return ToolResult(content=[TextContent(text=text)])


def _usage_line(result: SubagentResult) -> str:
    usage = result.usage
    parts = [
        f"agent {result.profile}",
        result.status,
    ]
    if result.permission_mode:
        parts.append(result.permission_mode)
    if usage.turns:
        parts.append(f"{usage.turns} turns")
    if usage.input or usage.output:
        parts.append(f"{usage.input} in / {usage.output} out")
    if usage.cost:
        parts.append(f"${usage.cost:.4f}")
    parts.append(f"{result.elapsed_ms / 1000:.1f}s")
    return "[" + " · ".join(parts) + "]"


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
    return roster or _NO_PROFILES


__all__ = [
    "AGENT_TOOL_NAME",
    "AGENT_TOOL_PARAMETERS",
    "MAX_ROSTER",
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
