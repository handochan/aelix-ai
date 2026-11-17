"""``/jobs``: list, detail, result, stop and send, for every kind (#722, ADR-0293 §monitoring).

::

    /jobs                 the current session's jobs: the panel in the TUI (#776), else the table
    /jobs list            the table
    /jobs <id>            one job: kind, title, state, owner, times, its rows, its result
    /jobs result <id>     the job's output, paged into the transcript (not to the model)
    /jobs stop <id>       stop it (a user's action: no consent)
    /jobs send            give the model the waiting results now

The list's rows are what the ``job`` tool's ``list`` returns, so the user and
the model see one table. An id may be given by any unique prefix.

The table's ACTIVITY is a kind's second row — what the job does now — never its
first, which is its title again (#776). The full table needs 93 columns; below,
AGE goes, then ACTIVITY narrows, before TITLE gets fewer than 20 cells. Every cell is measured
in terminal cells (``jobs/cells.py``).
"""

from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.jobs.cells import cut, pad
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
    row = rows[1] if len(rows) > 1 else ""
    return " · ".join(p for p in (lead, row) if p)


TITLE_MIN = 20
"""The cells TITLE keeps before the table gives up AGE, then ACTIVITY's width (#776)."""

_ACTIVITY = 26


def _table_layout(width: int) -> tuple[bool, int]:
    """``(show AGE, ACTIVITY's cells)`` at ``width``: the full table while TITLE keeps
    :data:`TITLE_MIN` cells (from 93 columns); below, AGE goes and then ACTIVITY narrows,
    to no fewer than 12 cells, before TITLE falls under :data:`TITLE_MIN` cells."""

    full = 2 + 10 + 2 + 10 + 1 + 12 + 1 + 8 + _ACTIVITY + 1
    if width >= 100 or width - full >= TITLE_MIN:
        return True, _ACTIVITY
    short = width - (full - 8)
    return False, max(12, _ACTIVITY - max(0, TITLE_MIN - short))


def _table_row(service: JobService, snap: JobSnapshot, show_age: bool, activity: int) -> str:
    age = format_elapsed(snap.elapsed) if snap.state not in TERMINAL_STATES else "—"
    aged = f"{age:<7} " if show_age else ""
    return (
        f"  {short_id(snap.job_id):<10}  {pad(snap.label, 10)} {_state_cell(snap):<12} "
        f"{aged}{pad(_activity(service, snap, activity), activity)} {snap.title}"
    )


def list_rows(service: JobService, width: int | None = None) -> list[str]:
    """The table ``/jobs`` prints and ``job(action="list")`` returns; ``[]`` when there are no jobs."""

    snaps = service.snapshots()
    if not snaps:
        return []
    width = width or _width()
    show_age, activity = _table_layout(width)
    aged = f"{'AGE':<7} " if show_age else ""
    header = f"  {'ID':<10}  {'KIND':<10} {'STATE':<12} {aged}{'ACTIVITY':<{activity}} TITLE"
    rows = [header]
    rows.extend(cut(_table_row(service, snap, show_age, activity), width) for snap in snaps)
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
        if not words or words[0] == "list":
            return await self._list(panel=not words)
        head, rest = words[0], words[1:]
        if head == "send":
            return await self._send()
        if head in ("result", "stop"):
            return await self._act(head, rest)
        found = find(service, head)
        if isinstance(found, str):
            return found
        return "\n".join(detail(service, found))

    async def _list(self, *, panel: bool) -> str:
        """Bare ``/jobs`` opens the host's panel when it has one (the TUI, #776); else, and for
        ``/jobs list``, the table. With no job there is nothing to open: one line says so."""

        rows = list_rows(self._service)
        if not rows:
            return "No background jobs in this session."
        hook = self._service.panel_hook if panel else None
        if hook is not None:
            return await hook() or ""
        return "\n".join([*rows, f"  {FOOTER}"])

    async def _act(self, head: str, rest: list[str]) -> str:
        service = self._service
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
