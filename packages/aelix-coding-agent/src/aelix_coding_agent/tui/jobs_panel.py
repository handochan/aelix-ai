"""The ``/jobs`` panel: the session's background jobs over the generic list panel (#776, ADR-0299).

Bare ``/jobs`` in the TUI opens it (owner OQ-S3-1, 2026-11-12: "B: /jobs로 여는
패널 (권장)"); ``/jobs list`` and every other host keep the table. The keys are
the owner's OQ-S3-2 (2026-11-12: "↑↓·Enter·r·s s·Esc/q (권장)"):

* ``↑``/``↓`` select; the selection is held by job id;
* ``Enter`` writes the job's detail — what ``/jobs <id>`` prints — above the panel;
* ``r`` closes the panel and pages the result into the transcript, as
  ``/jobs result <id>`` does;
* ``s`` ``s`` stops the selected job (below);
* ``Esc``/``q`` close it. Neither ends a running turn: the panel has the focus.

Nothing here names a kind: every row is the service's snapshot and its kind's
``render_rows`` — a state mark, the short id, the label, row 0, the age and the
activity (row 1, else ``Ns ago`` / ``quiet Nm``).

**The ``s s`` arm** (contract r2 §4.2). The first ``s`` arms ``(owner, job id)``
of the selected job for :data:`ARM_SECONDS`; the second stops THAT job, and only
while the arm holds: its owner is the session's now, the service has not been
bound to another harness since (a session change or a reload), the job is still
listed and not ended, and the time is not up. Moving the selection, the armed
job leaving the list (an ended job no longer kept, #769) or ending, a dialog
over the panel, and the panel closing each drop the arm. A second ``s`` with no
arm arms again; it never stops.

**The wake.** The panel is a modal, so a notice turn waits while it is open (G2,
ADR-0293 §11); the key row says so while a result waits, and closing it runs
the waker through the chrome's modal change.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.jobs import commands
from aelix_coding_agent.jobs.contract import TERMINAL_STATES, JobSnapshot
from aelix_coding_agent.jobs.indicator import indicator_text
from aelix_coding_agent.jobs.notice import format_elapsed
from aelix_coding_agent.jobs.service import JobNotFound
from aelix_coding_agent.tui.list_panel import Column, ListPanel, PanelLayout, PanelRow

if TYPE_CHECKING:
    from aelix_coding_agent.jobs.service import JobService

ARM_SECONDS = 3.0
TALL = 40
"""From this many terminal rows the panel has its detail area."""

WAITING = "◆ waiting results go when this closes"

class _NoTimer:
    def cancel(self) -> None:
        pass

_MARK = {
    "starting": "●",
    "running": "●",
    "stopping": "○",
    "unresolved": "!",
    "done": "✓",
    "recovered": "✓",
    "failed": "✗",
    "lost": "✗",
    "stopped": "■",
}

COLUMNS = (
    Column(6),  # the short id
    Column(8),  # the label
    Column(None, min=20),  # row 0
    Column(9, drop=1),  # the state
    Column(6),  # the age
    Column(26),  # the activity
)


def layout_for(rows: int) -> PanelLayout:
    """3 job rows; 3 detail rows too from :data:`TALL` terminal rows (#776)."""

    return PanelLayout(rows=3, detail=3 if rows >= TALL else 0)


def mark(snap: JobSnapshot) -> str:
    return "◆" if snap.result_waiting else _MARK.get(snap.state, "?")


def activity(snap: JobSnapshot, rows: list[str]) -> str:
    """Row 1 when the kind has one, else how long since the job last did something; an
    ended job's brief, as the table shows it."""

    if snap.state == "unresolved":
        return snap.unresolved_reason or "not confirmed stopped"
    if snap.result_waiting:
        return "result waiting"
    if snap.state in TERMINAL_STATES:
        return snap.brief or (rows[1] if len(rows) > 1 else "")
    ago = snap.last_activity_ago
    lead = "" if ago is None else f"quiet {int(ago // 60)}m" if snap.quiet else f"{format_elapsed(ago)} ago"
    if len(rows) > 1:
        return f"{lead} · {rows[1]}" if snap.quiet else rows[1]
    return lead


def detail(snap: JobSnapshot, rows: list[str]) -> tuple[str, ...]:
    """Why it is unresolved, the kind's rows after the first, its notice."""

    lines = [f"why: {snap.unresolved_reason}"] if snap.unresolved_reason else []
    lines.extend(rows[1:])
    if snap.notice_state == "pending":
        lines.append("◆ its result waits for the model")
    elif snap.notice_state == "delivered":
        lines.append("its result went to the model")
    return tuple(lines) or (f"id {snap.job_id}",)


def panel_row(service: JobService, snap: JobSnapshot, width: int = 200) -> PanelRow:
    rows = service.render_rows(snap, width)
    head = rows[0] if rows else snap.title
    age = format_elapsed(snap.elapsed)
    columns = (snap.job_id[:6], snap.label, head, snap.state, age, activity(snap, rows))
    return PanelRow(snap.job_id, columns, mark(snap), detail(snap, rows))


@dataclass(frozen=True)
class Arm:
    owner: str | None
    job_id: str
    at: float
    harness: Any


class JobsPanel:
    """One open panel. ``emit`` writes a block of text above it (the transcript)."""

    def __init__(
        self,
        service: JobService,
        chrome: Any,
        *,
        emit: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        rows: int | None = None,
        interval: float = 1.0,
    ) -> None:
        self.service = service
        self.chrome = chrome
        self.emit = emit
        self.clock = clock
        self.arm: Arm | None = None
        self.note = ""
        self._expiry: asyncio.TimerHandle | _NoTimer = _NoTimer()
        self.list = ListPanel(
            chrome,
            layout=layout_for(rows if rows is not None else _terminal_rows(chrome)),
            rows=self.rows,
            columns=COLUMNS,
            title=self.title,
            right=self.counts,
            hint=self.hint,
            keys={"enter": self.on_enter, "r": self.on_result, "s": self.on_stop},
            on_move=self.disarm,
            on_lost=self.disarm,
            interval=interval,
        )

    # --- what it shows -----------------------------------------------------------

    def rows(self) -> list[PanelRow]:
        """The jobs read now; an arm whose job ended, left the list or lost its owner goes."""

        snaps = self.service.snapshots()
        self._check_arm({s.job_id: s for s in snaps})
        return [panel_row(self.service, snap) for snap in snaps]

    def title(self) -> str:
        return f"Background jobs · {len(self.list.model.rows)}"

    def counts(self) -> str:
        text = indicator_text(self.service.snapshots(), extra_waiting=self.service.extension_waiting())
        return text[2:] if text else ""

    def hint(self, width: int) -> str:
        if self.arm is not None:
            return f"press s again to stop {self.arm.job_id[:6]}"
        # A note (``stopping <id>``) takes the keys' place, not the waiting text's: that
        # one says what Esc is about to send (#776 fix round 1, verify F6).
        keys = self.note or (
            "↑↓ select · enter detail · r result · s s stop · esc close"
            if width >= 100
            else "↑↓ · enter · r · s s stop · esc"
        )
        return f"{keys}   {WAITING}" if self._waiting() else keys

    def _waiting(self) -> bool:
        return self.service.extension_waiting() > 0 or any(
            s.result_waiting for s in self.service.snapshots()
        )

    # --- the arm -----------------------------------------------------------------

    def disarm(self) -> None:
        """Drop the arm (and its expiry redraw) and the note it left."""

        self.arm = None
        self.note = ""
        self._expiry.cancel()

    def on_modal_change(self) -> None:
        """A dialog opened or closed: one over the panel drops the arm."""

        if self.arm is not None and not self.list.on_top():
            self.disarm()

    def _check_arm(self, live: dict[str, JobSnapshot]) -> None:
        arm = self.arm
        if arm is None:
            return
        snap = live.get(arm.job_id)
        if (
            snap is None
            or snap.state in TERMINAL_STATES
            or arm.owner != self.service.owner
            or arm.harness is not self.service.harness
            or self.clock() - arm.at >= ARM_SECONDS
            or not self.list.on_top()
        ):
            self.disarm()

    # --- keys --------------------------------------------------------------------

    def on_stop(self, row: PanelRow | None) -> None:
        self.note = ""
        self._check_arm({s.job_id: s for s in self.service.snapshots()})
        arm = self.arm
        if arm is not None and row is not None and row.key == arm.job_id:
            self.disarm()
            self._stop(arm.job_id)
            return
        snap = self.service.snapshot(row.key) if row is not None else None
        if snap is None or snap.state in TERMINAL_STATES:
            self.note = "nothing to stop" if snap is None else f"{snap.job_id[:6]} has ended"
            return
        self.arm = Arm(self.service.owner, snap.job_id, self.clock(), self.service.harness)
        self._redraw_at_expiry()

    def _redraw_at_expiry(self) -> None:
        """Read the jobs again when the arm runs out, so "press s again" does not outlive it."""

        with contextlib.suppress(RuntimeError):  # no running loop: the next redraw drops it
            self._expiry = asyncio.get_running_loop().call_later(ARM_SECONDS + 0.05, self.list.refresh)

    def _stop(self, job_id: str) -> None:
        try:
            asked = self.service.request_stop(job_id, reason="user")  # "stopped by the user (/jobs)"
        except JobNotFound:
            asked = False
        self.note = f"stopping {job_id[:6]}" if asked else f"{job_id[:6]} has ended"
        self.list.refresh()

    def on_enter(self, row: PanelRow | None) -> None:
        self.note = ""
        self.disarm()
        snap = self.service.snapshot(row.key) if row is not None else None
        if snap is not None and self.emit is not None:
            self.emit("\n".join(commands.detail(self.service, snap)))

    def on_result(self, row: PanelRow | None) -> None:
        snap = self.service.snapshot(row.key) if row is not None else None
        if snap is not None:
            self.list.close(commands.result_text(self.service, snap))

    # --- lifecycle -----------------------------------------------------------------

    async def run(self) -> str | None:
        """Open it; once closed, what ``r`` paged (``None`` for a plain close)."""

        try:
            return await self.list.run()
        finally:
            self.disarm()


def _terminal_rows(chrome: Any) -> int:
    try:
        return int(chrome.app.output.get_size().rows)
    except Exception:  # noqa: BLE001 — no size yet: the short panel
        return 24


__all__ = ["ARM_SECONDS", "COLUMNS", "TALL", "WAITING", "JobsPanel", "layout_for", "panel_row"]
