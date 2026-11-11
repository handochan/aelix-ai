"""``/jobs``: list, detail, result, stop and send, for every kind (#722, ADR-0293 §monitoring).

::

    /jobs                 the current session's jobs
    /jobs <id>            one job: kind, title, state, owner, times, its rows, its result
    /jobs result <id>     the job's output, paged into the transcript (not to the model)
    /jobs stop <id>       stop it (a user's action: no consent)
    /jobs send            give the model the waiting results now

The list's rows are what the ``job`` tool's ``list`` returns, so the user and
the model see one table. An id may be given by any unique prefix.
"""

from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.jobs.contract import TERMINAL_STATES, JobSnapshot, OutputSlice
from aelix_coding_agent.jobs.notice import format_elapsed

if TYPE_CHECKING:
    from aelix_coding_agent.jobs.service import JobService

FOOTER = "/jobs <id> · /jobs result <id> · /jobs stop <id> · /jobs send"
_PAGE = 8 * 1024


def _width() -> int:
    return max(60, shutil.get_terminal_size((100, 24)).columns - 2)


def short_id(job_id: str) -> str:
    return job_id[:6] + "…" if len(job_id) > 7 else job_id


def _state_cell(snap: JobSnapshot) -> str:
    return f"{snap.state} ◆" if snap.result_waiting else snap.state


def _activity(service: JobService, snap: JobSnapshot, width: int) -> str:
    if snap.state == "unresolved":
        return snap.unresolved_reason or "not confirmed stopped"
    if snap.result_waiting:
        return "result waiting"
    if snap.state in TERMINAL_STATES:
        return snap.brief
    rows = service.render_rows(snap, width)
    lead = (
        f"{format_elapsed(snap.last_activity_ago)} ago"
        if snap.last_activity_ago is not None
        else ""
    )
    if snap.quiet:
        lead = f"quiet {lead}"
    row = rows[0] if rows else ""
    return " · ".join(p for p in (lead, row) if p)


def list_rows(service: JobService, width: int | None = None) -> list[str]:
    """The table ``/jobs`` prints and ``job(action="list")`` returns; ``[]`` when there are no jobs."""

    snaps = service.snapshots()
    if not snaps:
        return []
    width = width or _width()
    header = f"  {'ID':<10}  {'KIND':<10} {'STATE':<12} {'AGE':<7} {'ACTIVITY':<26} TITLE"
    rows = [header]
    for snap in snaps:
        age = format_elapsed(snap.elapsed) if snap.state not in TERMINAL_STATES else "—"
        activity = _activity(service, snap, 26)
        line = (
            f"  {short_id(snap.job_id):<10}  {snap.label[:10]:<10} {_state_cell(snap):<12} "
            f"{age:<7} {activity[:26]:<26} {snap.title}"
        )
        rows.append(line[:width])
    return rows


def find(service: JobService, ident: str) -> JobSnapshot | str:
    """The job ``ident`` names (an id or a unique prefix of one), or why none."""

    ident = ident.strip().rstrip("…")
    if not ident:
        return "name a job: /jobs <id>"
    exact = service.snapshot(ident)
    if exact is not None:
        return exact
    matches = [s for s in service.snapshots() if s.job_id.startswith(ident)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return f"no job {ident} in this session"
    return f"{ident} names {len(matches)} jobs; give more of the id"


def detail(service: JobService, snap: JobSnapshot, width: int | None = None) -> list[str]:
    width = width or _width()
    created = (
        datetime.fromtimestamp(snap.created).strftime("%Y-%m-%d %H:%M:%S") if snap.created else "—"
    )
    lines = [
        f'{snap.label} "{snap.title}" — {snap.state}',
        f"  id        {snap.job_id}",
        f"  kind      {snap.kind}",
        f"  owner     {snap.owner}",
        f"  started   {created}",
        f"  elapsed   {format_elapsed(snap.elapsed)}",
    ]
    if snap.last_activity_ago is not None:
        quiet = " (quiet)" if snap.quiet else ""
        lines.append(f"  activity  {format_elapsed(snap.last_activity_ago)} ago{quiet}")
    if snap.unresolved_reason:
        lines.append(f"  why       {snap.unresolved_reason}")
    if snap.brief:
        lines.append(f"  result    {snap.brief}")
    notice = {
        "none": "no notice",
        "pending": "waiting to go to the model (/jobs send)",
        "delivered": "given to the model",
    }[snap.notice_state]
    lines.append(f"  notice    {notice}")
    lines.extend(f"  {row}" for row in service.render_rows(snap, width - 2))
    if snap.state in TERMINAL_STATES:
        lines.append(f"  /jobs result {short_id(snap.job_id)}")
    elif snap.state != "unresolved":
        lines.append(f"  /jobs stop {short_id(snap.job_id)}")
    return lines


def result_text(service: JobService, snap: JobSnapshot, offset: int = 0) -> str:
    out = service.output(snap.job_id, offset=offset, limit=_PAGE)
    if isinstance(out, str):
        return out
    assert isinstance(out, OutputSlice)
    text = out.text
    if out.more:
        text += f"\n… more: /jobs result {short_id(snap.job_id)} {out.continue_at}"
    return text or "(empty output)"


class JobsCommand:
    """The ``/jobs`` handler. ``send`` is the host's when it has one (the TUI's waker)."""

    def __init__(
        self, service: JobService, *, send: Callable[[], Awaitable[str] | str] | None = None
    ) -> None:
        self._service = service
        self.send = send

    async def __call__(self, args: str, _ctx: Any = None) -> str:
        words = args.split()
        service = self._service
        if not words:
            rows = list_rows(service)
            if not rows:
                return "No background jobs in this session."
            return "\n".join([*rows, f"  {FOOTER}"])
        head, rest = words[0], words[1:]
        if head == "send":
            return await self._send()
        if head in ("result", "stop"):
            if not rest:
                return f"usage: /jobs {head} <id>"
            found = find(service, rest[0])
            if isinstance(found, str):
                return found
            if head == "result":
                offset = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 0
                return result_text(service, found, offset)
            if found.state in TERMINAL_STATES:
                return f"{short_id(found.job_id)} already ended: {found.state}"
            snap = await service.stop(found.job_id, reason="user")
            state = snap.state if snap is not None else "unknown"
            return f"Stopped {short_id(found.job_id)}: {state}"
        found = find(service, head)
        if isinstance(found, str):
            return found
        return "\n".join(detail(service, found))

    async def _send(self) -> str:
        waiting = sum(1 for s in self._service.snapshots() if s.result_waiting)
        waiting += self._service.extension_waiting()
        if waiting == 0:
            return "No results are waiting."
        send = self.send if self.send is not None else self._service.send_hook
        if send is not None:
            said = send()
            if not isinstance(said, str):
                said = await said
            return said
        result = await self._service.send_waiting()
        if result.started:
            return f"Sent {waiting} waiting result{'s' if waiting != 1 else ''} to the model."
        return f"Not sent: {result.reason}"


__all__ = ["FOOTER", "JobsCommand", "detail", "find", "list_rows", "result_text", "short_id"]
