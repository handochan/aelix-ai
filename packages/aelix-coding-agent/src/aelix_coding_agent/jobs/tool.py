"""The ``job`` and ``job_stop`` tools: the model's view of the session's background jobs (#722, ADR-0293 §monitoring).

``job(action="list")`` returns the rows ``/jobs`` prints; ``status`` one job's
detail; ``result`` its output, a page at a time. ``job_stop(id)`` stops one when
its kind lets the model control it here (``job_tool_control``), and otherwise
answers with the kind's own hint (a kind may route control through its own
tool). Both see only the current session's jobs: an id of another session's
job is not found (Codex #5).

**Two tools, because the permission gate reads one marking per tool object**
(``tools/provenance.py``, ADR-0293 §10). ``job`` only reads, so it is marked
``read_only`` and runs in every posture, plan mode included. ``job_stop``
changes what runs, so it carries no marking: the gate treats it as any tool
aelix did not mark (ADR-0253) and, outside ``auto``'s bash classifier, as it
treats ``bash`` — with an approval UI it asks in ``default``,
``auto-accept-edits`` and ``auto``; ``plan`` blocks it everywhere; ``yolo``
allows it, and so does a host with no approval UI (``-p``, json, RPC without
a host approval hook) outside ``plan``, except in a delegated child, which
blocks it. A user's ``/jobs stop`` asks nothing
(a user's own action).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolResult

from aelix_coding_agent.jobs import commands
from aelix_coding_agent.jobs.contract import TERMINAL_STATES, OutputSlice
from aelix_coding_agent.tools.provenance import mark_builtin

if TYPE_CHECKING:
    from aelix_coding_agent.jobs.service import JobService

JOB_TOOL_NAME = "job"
JOB_STOP_TOOL_NAME = "job_stop"

JOB_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "status", "result"],
            "description": "list: every background job of this session; status: one job; result: its output.",
        },
        "id": {
            "type": "string",
            "description": "The job id (or a unique prefix), for status and result.",
        },
        "offset": {
            "type": "integer",
            "description": "result: where to continue reading, from the last page's hint.",
        },
    },
    "required": ["action"],
}

JOB_STOP_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "The job id (or a unique prefix) to stop."},
    },
    "required": ["id"],
}

_PAGE = 16 * 1024


def _text(text: str, *, error: bool = False) -> ToolResult:
    return ToolResult(content=[TextContent(text=text)], is_error=error)


def create_job_tool(service: JobService) -> AgentTool:
    """The read-only ``job`` tool: ``list``, ``status``, ``result``."""

    async def execute(args: dict[str, Any], _ctx: Any = None) -> ToolResult:
        action = args.get("action")
        if action == "list":
            rows = commands.list_rows(service, width=160)
            return _text("\n".join(rows) if rows else "No background jobs in this session.")
        if action == "stop":
            return _text(f"use {JOB_STOP_TOOL_NAME}(id=…) to stop a job", error=True)
        if action not in ("status", "result"):
            return _text(f"unknown action {action!r}: use list, status or result", error=True)
        ident = args.get("id")
        if not isinstance(ident, str) or not ident.strip():
            return _text(f"job(action={action!r}) needs an id", error=True)
        found = commands.find(service, ident)
        if isinstance(found, str):
            return _text(found, error=True)
        if action == "status":
            return _text("\n".join(commands.detail(service, found, width=160)))
        offset = args.get("offset")
        start = offset if isinstance(offset, int) and offset >= 0 else 0
        out = service.output(found.job_id, offset=start, limit=_PAGE)
        if isinstance(out, str):
            error = out.startswith("could not read the result")
            return _text(out, error=error)
        assert isinstance(out, OutputSlice)
        tail = (
            f'\n… more: job(action="result", id="{found.job_id}", offset={out.offset + len(out.text)})'
            if out.more
            else ""
        )
        return _text((out.text or "(empty output)") + tail)

    tool = AgentTool(
        name=JOB_TOOL_NAME,
        prompt_snippet="List, inspect and read this session's background jobs",
        description=(
            "See this session's background jobs: list them, or read one's status or its "
            "output. A job that ends also sends you a notice once, so do not poll; use "
            f"this when you need a job's state or its full output now. {JOB_STOP_TOOL_NAME} stops one."
        ),
        parameters=dict(JOB_TOOL_PARAMETERS),
        execute=execute,
    )
    return mark_builtin(tool, "read_only")


def create_job_stop_tool(service: JobService) -> AgentTool:
    """``job_stop``: stop one of this session's jobs. Unmarked: gated as any tool aelix did not mark."""

    async def execute(args: dict[str, Any], _ctx: Any = None) -> ToolResult:
        ident = args.get("id")
        if not isinstance(ident, str) or not ident.strip():
            return _text(f"{JOB_STOP_TOOL_NAME} needs an id", error=True)
        found = commands.find(service, ident)
        if isinstance(found, str):
            return _text(found, error=True)
        registration = service.registry.get(found.kind)
        caps = registration.adapter.capabilities if registration is not None else None
        if caps is not None and not caps.job_tool_control:
            return _text(
                caps.control_hint or "this kind of job is not controlled by this tool",
                error=True,
            )
        if found.state in TERMINAL_STATES:
            return _text(f"{found.job_id} already ended: {found.state}")
        snap = await service.stop(found.job_id, reason="model")
        return _text(f"{found.job_id}: {snap.state if snap is not None else 'unknown'}")

    return AgentTool(
        name=JOB_STOP_TOOL_NAME,
        prompt_snippet="Stop one of this session's background jobs",
        description="Stop one of this session's background jobs, by its id from job(action=\"list\").",
        parameters=dict(JOB_STOP_TOOL_PARAMETERS),
        execute=execute,
    )


__all__ = [
    "JOB_STOP_TOOL_NAME",
    "JOB_STOP_TOOL_PARAMETERS",
    "JOB_TOOL_NAME",
    "JOB_TOOL_PARAMETERS",
    "create_job_stop_tool",
    "create_job_tool",
]
