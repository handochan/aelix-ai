"""The TUI's side of background jobs: the waker, the footer segment, the restored line (#722, ADR-0293).

Only while background jobs are active (``run_tui`` builds this only when the CLI
hands it the job service). Nothing here names a kind of job.

**The waker.** A job that ends while the user is idle leaves a notice in the
harness's inbox; the waker puts a :class:`~aelix_coding_agent.tui.chrome.NoticeWake`
in the input queue so the input loop — parked at ``get_input`` — runs a notice
turn exactly as it runs a typed line. It re-checks its gates whenever something
they read changes: a notice queued, turns released, the harness gone idle, a
modal opened or closed, the loop parked again, a job's state, a restore. The
gates, all of which must hold when the item is queued AND again when the loop
takes it:

* G1 — the input loop is parked and nothing runs (no turn, no command);
* G2 — no dialog is open: an approval, a select, a confirmation or any other
  modal. Owner decision 1 (2026-11-07): "Waiting for the user = an open dialog
  only", and a notice never closes or answers one — it waits for the next
  boundary after the dialog is gone;
* G3 — the harness is not held, disposed or stopped, and no reload or session
  change is running now (``AgentSessionRuntime.in_transition``). Not the
  latest reload's ``reload_phase``: that stays ``done`` — or the step a failed
  reload stopped at — for the rest of the session (verify B1);
* G5 — no Esc withdrawal is still unwinding;
* G7 — a notice of THIS session is pending, and it is live — a result of a job
  this process ran, or one an extension posted (``ExtensionAPI.post_notice``):
  a restored one never starts a turn on its own (owner OQ7) — it goes with the
  user's next message, or now with ``/jobs send`` (which forces G7).

**The restored-results line**, once per session restore with results waiting:
``◆ 2 background results are waiting. They go to the model with your next
message, or run /jobs send to send them now.``
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.jobs.indicator import indicator_text
from aelix_coding_agent.tui.chrome import NoticeWake

if TYPE_CHECKING:
    from aelix_coding_agent.jobs.service import JobService

WAKE = NoticeWake()


def restored_line(count: int) -> str:
    if count == 1:
        return (
            "◆ 1 background result is waiting. It goes to the model with your next "
            "message, or run /jobs send to send it now."
        )
    return (
        f"◆ {count} background results are waiting. They go to the model with your next "
        "message, or run /jobs send to send them now."
    )


class JobsUi:
    """One per TUI. ``chrome`` and ``runtime_host`` are read live; the harness changes on every rebuild."""

    def __init__(
        self,
        service: JobService,
        *,
        chrome: Any,
        runtime_host: Any,
        commit: Callable[[str], None] | None = None,
    ) -> None:
        self.service = service
        self.chrome = chrome
        self.runtime_host = runtime_host
        self.commit = commit
        self.parked = False
        self.busy = False
        self.queued = False
        self.force = False
        # A notice turn that placed nothing while notices stay pending (a write
        # that keeps failing, a refused model): no new wake until something changes.
        self.stalled = False
        # The restored-results line waits for the banner and the replayed
        # transcript (``run_tui`` sets this once they are committed).
        self.ready = False
        self._detach_harness: Callable[[], None] | None = None
        self._attached: Any = None
        self._restored_said: set[str] = set()
        self._detach_service = service.add_listener(self.on_service_change)
        self._detach_placed = service.add_placed_listener(self._placed)
        self.attach(runtime_host.harness)

    # --- wiring ----------------------------------------------------------------

    def attach(self, harness: Any) -> None:
        """Listen to ``harness``'s inbox (each build has its own)."""

        if harness is self._attached:
            return
        if self._detach_harness is not None:
            self._detach_harness()
        self._attached = harness
        add = getattr(harness, "add_notice_listener", None)
        self._detach_harness = add(self._on_notice_change) if add is not None else None

    def _on_notice_change(self, change: str) -> None:
        if change in ("queued", "released"):
            self.stalled = False
        self.maybe_wake()

    def after_turn(self, result: Any) -> None:
        """The notice turn ended: stall when it changed nothing and notices remain."""

        harness = self.runtime_host.harness
        placed = getattr(result, "placed", 0) if getattr(result, "started", False) else 0
        self.stalled = placed == 0 and bool(harness.pending_notices)

    def on_service_change(self) -> None:
        """A job changed, or the service was bound to a rebuilt harness: the footer, the restored line, the wake."""

        self.attach(self.runtime_host.harness)
        self.stalled = False
        invalidate = getattr(self.chrome, "invalidate", None)
        if invalidate is not None:
            invalidate()
        if self.ready:
            text = self.restored_text()
            if text is not None and self.commit is not None:
                self.commit(text)
        self.maybe_wake()

    def close(self) -> None:
        for detach in (self._detach_harness, self._detach_service, self._detach_placed):
            if detach is not None:
                detach()

    def is_wake(self, item: object) -> bool:
        return isinstance(item, NoticeWake)

    # --- the gates ---------------------------------------------------------------

    def gates(self, *, at_dequeue: bool = False) -> str | None:
        """``None`` when every gate holds, else the first that does not (for tests and logs)."""

        host = self.runtime_host
        harness = host.harness
        if not at_dequeue and not self.parked:
            return "G1: not parked"
        if self.busy or getattr(self.chrome, "running", False) or not getattr(harness, "is_idle", True):
            return "G1: something runs"
        if self.chrome.is_modal_open():
            return "G2: a dialog is open"
        if getattr(host, "stopped", None) is not None or getattr(host, "in_transition", False):
            return "G3: stopped or reloading"
        if getattr(harness, "turns_held", None) is not None or getattr(harness, "disposed", False):
            return "G3: held or disposed"
        if getattr(harness, "withdraw_pending", False):
            return "G5: an Esc is unwinding"
        if not harness.pending_notices:
            return "G7: nothing pending"
        if self.stalled and not self.force:
            return "G7: the last notice turn placed nothing"
        if not self.force and not self.service.has_live_pending():
            return "G7: only restored results (they wait for /jobs send)"
        return None

    def maybe_wake(self) -> bool:
        """Queue the wake when every gate holds and none is queued; whether it did."""

        self.attach(self.runtime_host.harness)
        if self.queued or self.gates() is not None:
            return False
        self.queued = True
        self.chrome.queue_wake(WAKE)
        return True

    def take(self) -> bool:
        """The input loop took the item: whether the gates still hold (else it is dropped)."""

        self.queued = False
        allowed = self.gates(at_dequeue=True) is None
        if allowed:
            self.force = False
        return allowed

    def send_now(self) -> str:
        """``/jobs send``: wake for what waits, restored results included."""

        waiting = self.runtime_host.harness.pending_notices
        if not waiting:
            return "No results are waiting."
        self.force = True
        if not self.queued:
            self.queued = True
            self.chrome.queue_wake(WAKE)
        noun = "result" if waiting == 1 else "results"
        return f"Sending {waiting} waiting {noun} to the model."

    # --- surfaces ----------------------------------------------------------------

    def footer(self, width: int) -> str | None:
        return indicator_text(
            self.service.snapshots(),
            width=max(10, width // 3),
            extra_waiting=self.service.extension_waiting(),
        )

    def restored_text(self) -> str | None:
        """The restored-results line, once per session; ``None`` when nothing waits or it was said."""

        owner = self.service.owner
        count = self.service.restored_waiting()
        if owner is None or count == 0 or owner in self._restored_said:
            return None
        self._restored_said.add(owner)
        return restored_line(count)

    def _placed(self, notice: Any) -> None:
        if self.commit is not None and getattr(notice, "brief", ""):
            self.commit(f"◆ background result: {notice.brief}")


__all__ = ["WAKE", "JobsUi", "restored_line"]
