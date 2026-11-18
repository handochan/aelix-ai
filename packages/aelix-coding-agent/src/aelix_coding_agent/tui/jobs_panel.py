"""The ``/jobs`` panel: the session's background jobs over the generic list panel (#776, ADR-0299).

Bare ``/jobs`` in the TUI opens it (owner OQ-S3-1, 2026-11-12: "B: /jobs로 여는
패널 (권장)"); ``/jobs list`` and every other host keep the table. The keys are
the owner's OQ-S3-2 (2026-11-12: "↑↓·Enter·r·s s·Esc/q (권장)"):

* ``↑``/``↓`` select; the selection is held by job id;
* ``Enter`` writes the job's detail — what ``/jobs <id>`` prints — above the panel;
* ``r`` closes the panel and pages the result into the transcript, as
  ``/jobs result <id>`` does;
* ``s`` ``s`` stops the selected job (below);
* ``m`` closes the panel with ``/jobs steer <id> `` in the input, for the human
  to finish the note (#123, ADR-0306 §3.1); ``w`` ``w`` asks the selected job
  to wrap up, armed exactly as ``s`` ``s`` is; on a job whose kind takes
  neither, both leave the kind's refusal on the hint row;
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

from rich.cells import cell_len

from aelix_coding_agent.jobs import commands
from aelix_coding_agent.jobs.contract import TERMINAL_STATES, JobSnapshot
from aelix_coding_agent.jobs.indicator import indicator_text
from aelix_coding_agent.jobs.notice import format_elapsed
from aelix_coding_agent.jobs.service import JobNotFound, control_refusal
from aelix_coding_agent.tui.list_panel import Column, ListPanel, PanelLayout, PanelRow
from aelix_coding_agent.tui.selection_panel import fit, flat

if TYPE_CHECKING:
    from aelix_coding_agent.jobs.service import JobService

ARM_SECONDS = 3.0
TALL = 40
"""From this many terminal rows the panel has its detail area."""

WAITING = "◆ waiting results go when this closes"

KEY_ROWS = (
    "↑↓ select · enter detail · r result · m note · s s stop · w w wrap · esc close",
    "↑↓ select · enter · r · m note · s s stop · w w wrap · esc",
    "↑↓ · enter · r · m · s s · w w · esc",
)
"""The key row, fullest first — 78, 58 and 36 cells; :meth:`JobsPanel.hint` shows the first that fits."""

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
    verb: str = "stop"
    """``stop`` (``s s``) or ``wrap`` (``w w``, #123): a second key of the OTHER
    letter arms afresh, never acts on the first's arm."""


class Prefill(str):
    """What ``m`` closes the panel with: text for the input box, not for the transcript (#123)."""


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
            keys={
                "enter": self.on_enter,
                "r": self.on_result,
                "s": self.on_stop,
                "m": self.on_message,
                "w": self.on_wrap,
            },
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
            word = "wrap up" if self.arm.verb == "wrap" else "stop"
            return f"press {self.arm.verb[0]} again to {word} {self.arm.job_id[:6]}"
        # A note (``stopping <id>``) takes the keys' place, not the waiting text's: that
        # one says what Esc is about to send (#776 fix round 1, verify F6).
        tail = f"   {WAITING}" if self._waiting() else ""
        if self.note:
            # V5 — every note is fitted here, to the cells the waiting text leaves it,
            # ending in ``…`` when it is cut; no writer slices one by length.
            room = width - cell_len(tail)
            return f"{fit(flat(self.note), room) if room > 0 else self.note}{tail}"
        # F11 — the fullest key row that fits, the waiting text counted in (cells).
        room = width - cell_len(tail)
        keys = next((row for row in KEY_ROWS if cell_len(row) <= room), KEY_ROWS[-1])
        return f"{keys}{tail}"

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
        self._armed_key("stop", row)

    def on_wrap(self, row: PanelRow | None) -> None:
        """``w`` ``w``: the wrap-up, armed as the stop is (#123, ADR-0306 §3.1)."""

        self._armed_key("wrap", row)

    def _armed_key(self, verb: str, row: PanelRow | None) -> None:
        self.note = ""
        self._check_arm({s.job_id: s for s in self.service.snapshots()})
        arm = self.arm
        if arm is not None and arm.verb == verb and row is not None and row.key == arm.job_id:
            self.disarm()
            if verb == "wrap":
                self._wrap(arm.job_id)
            else:
                self._stop(arm.job_id)
            return
        snap = self.service.snapshot(row.key) if row is not None else None
        if snap is None or snap.state in TERMINAL_STATES:
            self.note = f"nothing to {verb}" if snap is None else f"{snap.job_id[:6]} has ended"
            return
        self.arm = Arm(self.service.owner, snap.job_id, self.clock(), self.service.harness, verb)
        self._redraw_at_expiry()

    def _wrap(self, job_id: str) -> None:
        """Ask the job to wrap up through the service's control; the answer is the note."""

        async def go() -> None:
            try:
                answer = await self.service.control(job_id, "wrap_up", {"origin": "user"})
            except JobNotFound:
                answer = f"{job_id[:6]} has ended"
            except Exception as exc:  # noqa: BLE001 — one line on the panel, never a traceback
                answer = f"wrap-up failed: {type(exc).__name__}"
            self.note = answer
            self.list.refresh()

        self.note = f"wrapping up {job_id[:6]}"
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(go())
        self.list.refresh()

    def on_message(self, row: PanelRow | None) -> None:
        """``m``: close with ``/jobs steer <id> `` for the input box (#123).

        A job whose kind declares no ``steer`` keeps the panel open, and the
        note is the sentence ``/jobs steer`` would answer (fix round 1, V7).
        """

        snap = self.service.snapshot(row.key) if row is not None else None
        if snap is None or snap.state in TERMINAL_STATES:
            self.note = "nothing to message" if snap is None else f"{snap.job_id[:6]} has ended"
            return
        registration = self.service.registry.get(snap.kind)
        caps = registration.adapter.capabilities if registration is not None else None
        if caps is not None and "steer" not in caps.controls:
            self.note = control_refusal(caps, "steer")
            return
        self.list.close(Prefill(f"/jobs steer {snap.job_id[:6]} "))

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


__all__ = ["ARM_SECONDS", "COLUMNS", "TALL", "WAITING", "Arm", "JobsPanel", "Prefill", "layout_for", "panel_row"]
