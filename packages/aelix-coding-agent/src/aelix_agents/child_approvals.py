"""The parent's router for a delegated child's approval requests (#612, ADR-0308).

A row launched with ``--command-inbox=2`` (``SpawnPlan.asks_parent``) has one
:class:`ChildApprovals`, built beside its command wire and carried by a Ctrl+B
move as the wire is (both live on the :class:`~aelix_agents.print_channel.RunningChild`).
The child writes ``{"type": "approval_request", …}`` on its stdout where its gate
would ask a human; the stdout pump hands that line to :meth:`ChildApprovals.offer`
before the reducer and the wire's correlator, and :meth:`offer` never awaits: it
starts one task (#612's body: "펌프 안에서 승인 응답을 기다리지 않는다").

The task asks the parent's ARBITER (``PermissionExtension.decide_for_child``: the
TUI's dialog, or an RPC host that opted in; this process's posture only refuses)
and writes the answer on the child's inbox as an ordinary
``CommandWire.request("approval", …)``, serialised with steer, follow-up, wrap-up
and tighten (ADR-0306's 5 s write and 30 s ack bounds). It is never written twice.

How an open request ends, one reason per cause (contract r5 §7.1, §7.5):

* the answer is written — ``aelix_child`` ``approval`` step ``allowed`` or
  ``denied`` (the decision SENT), the row's note cleared, or, when its delivery is
  ``unknown``, "the answer's delivery is unknown; it may have been read" (K12), or,
  ``not_sent``, step ``withdrawn`` and "the answer was not sent; stop or resume it"
  (the child stays held at its gate: it runs nothing). An answer the wire refuses
  unwritten — broken (``INBOX_BROKEN``) or closed (``ended``) — is ``not_sent`` too,
  and a request that arrives while the wire is so is recorded that way at once,
  with no one asked (#612 fix round 1, P-M1);
* a wrap-up is recorded on the row — the open request is answered ``cancel`` "the
  agent was asked to wrap up" BEFORE the final steer is written
  (:meth:`ChildApprovals.wrap_up`), and while the wrap-up stays recorded every later
  request is answered so at once, without a dialog; a wrap-up cancelled while that
  ``cancel`` waits for its acknowledgement closes the request as an answer of
  ``unknown`` delivery and is taken back (#612 fix round 1, V-F1) — the ``cancel``,
  already written, may have been read, its reason naming the wrap-up that is no
  longer recorded (fix round 2, F4);
* the row is stopped, cancelled or ends (:meth:`ChildApprovals.withdraw`) — with no
  answer written yet the dialog is withdrawn (its task cancelled: the TUI unmounts
  it, the RPC future is dropped) and the step is ``withdrawn``; once the answer's
  write began the step is that decision;
* a second request while one is open, or a line with an id that cannot be read,
  is answered ``cancel`` at once; a line with no readable id cannot be answered
  and the row says so (K15).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aelix_coding_agent.builtin.permission import CHILD_NO_APPROVER_REASON
from aelix_coding_agent.modes.print_inbox import APPROVAL_DECISIONS, APPROVAL_REQUEST_TYPE
from aelix_coding_agent.subagent_contract import ChildApprovalRequest

from aelix_agents.child_events import job_id_of, wire_call_id
from aelix_agents.command_wire import INBOX_BROKEN

logger = logging.getLogger(__name__)

APPROVAL_PREFIX = '{"type": "' + APPROVAL_REQUEST_TYPE + '"'
"""How a child begins every request line (``json.dumps``' default separators, ``type``
first); the one cheap test the pump makes before anything parses a line."""

WRAP_UP_REASON = "the agent was asked to wrap up"
ANOTHER_OPEN_REASON = "another approval request is open"
UNREADABLE_REASON = "the approval request could not be read"
UNREADABLE_NOTE = "an approval request could not be read; stop it"
UNKNOWN_NOTE = "the answer's delivery is unknown; it may have been read"
NOT_SENT_NOTE = "the answer was not sent; stop or resume it"
_APPROVING = frozenset({"yes", "yes_session", "redirect"})
_KINDS = frozenset({"bash", "write", "edit", "other"})

Decide = Callable[[ChildApprovalRequest], Awaitable[tuple[str, "str | None"]]]


@dataclass
class _Open:
    """The one request open on a row: what was asked, its task, and how far its answer got."""

    request: ChildApprovalRequest
    raw_call_id: str
    task: asyncio.Task[None] | None = None
    writing: str | None = None
    """The decision whose write began, once :meth:`ChildApprovals._write` started it."""
    ended: bool = False
    """Its last ``approval`` step was emitted."""


def _wire_id_or_none(raw: Any) -> str | None:
    return wire_call_id(raw) if isinstance(raw, str) and raw else None


def _step_of(decision: str) -> str:
    return "allowed" if decision in _APPROVING else "denied"


def _refuses_unwritten(wire: Any) -> bool:
    """A wire that answers every request ``refused`` without writing it (``CommandWire.request``)."""

    return bool(getattr(wire, "broken", False) or getattr(wire, "closed", False))


def _outcome_of(reply: Any) -> str:
    """The answer's delivery: ``refused`` unwritten (a broken or closed wire) is ``not_sent`` (P-M1).

    A ``refused`` the child gave (it no longer waits: "not pending") stays
    ``refused``: the line reached it.
    """

    outcome = getattr(reply, "outcome", "not_sent")
    if outcome == "refused" and (getattr(reply, "ended", False) or getattr(reply, "error", None) == INBOX_BROKEN):
        return "not_sent"
    return outcome


class ChildApprovals:
    """One channel row's approval requests: offered by the pump, asked of the arbiter, answered on the wire."""

    def __init__(
        self,
        row: Any,
        *,
        decide: Decide | None,
        hub: Any = None,
        publish: Callable[[], None] | None = None,
        cwd: str = "",
        parent_cwd: str = "",
    ) -> None:
        self.row = row
        self._decide = decide
        self._hub = hub
        self._publish = publish
        self._cwd = cwd
        self._parent_cwd = parent_cwd
        self._open: _Open | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.closed = False

    @property
    def open_request(self) -> ChildApprovalRequest | None:
        return None if self._open is None else self._open.request

    # ── the pump's side ─────────────────────────────────────────────

    def offer(self, line: str) -> None:
        """One ``approval_request`` line from the child's stdout. Synchronous; never raises."""

        try:
            self._offer(line)
        except Exception:  # noqa: BLE001 — a malformed request never costs the stream
            logger.debug("an approval request of %s could not be routed", self.row.id, exc_info=True)

    def _offer(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except ValueError:
            payload = None
        request_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(request_id, str) or not request_id:
            self._note(UNREADABLE_NOTE)  # K15: nothing names the request to answer
            return
        if self.closed:
            return
        assert isinstance(payload, dict)
        request = self._request(request_id, payload)
        if request is None:
            self._answer_now(request_id, UNREADABLE_REASON)
        elif self._open is not None:
            self._answer_now(request_id, ANOTHER_OPEN_REASON)
        elif self.row.wrap_up is not None:
            self._answer_now(request_id, WRAP_UP_REASON)
        else:
            opened = _Open(request, raw_call_id=str(payload.get("toolCallId")))
            self._open = opened
            self._step(opened, "asked")
            if _refuses_unwritten(self.row.wire):
                # P-M1: no answer could be written, so no human is asked for one.
                self._settle(opened, "cancel", "not_sent")
                return
            self._note(f"awaiting approval: {request.tool_name}")
            opened.task = self._start(self._ask(opened))

    def _request(self, request_id: str, payload: dict[str, Any]) -> ChildApprovalRequest | None:
        tool_name, args, call_id = payload.get("toolName"), payload.get("args"), payload.get("toolCallId")
        if not isinstance(tool_name, str) or not isinstance(args, dict) or not isinstance(call_id, str):
            return None
        kind = payload.get("kind")
        yes_label, redirect_label = payload.get("yesLabel"), payload.get("redirectLabel")
        row = self.row
        job_id = job_id_of(row)
        return ChildApprovalRequest(
            request_id=request_id,
            child_id=row.id,
            profile=row.profile,
            tool_name=tool_name,
            kind=kind if kind in _KINDS else "other",
            args=args,
            call_id=wire_call_id(call_id),
            yes_label=yes_label if isinstance(yes_label, str) else None,
            redirect_label=redirect_label if isinstance(redirect_label, str) else None,
            session_id=row.session_id,
            job_id=job_id,
            # The form ``aelix_child`` and ``children/list`` carry (fix round 1, V-N1).
            parent_tool_call_id=_wire_id_or_none(row.parent_tool_call_id),
            background=job_id is not None,
            cwd=self._cwd,
            parent_cwd=self._parent_cwd,
            epoch=getattr(self._hub, "epoch", None),
        )

    def _start(self, coro: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _answer_now(self, request_id: str, reason: str) -> None:
        """``cancel`` ``reason``, with no dialog and no step: the request never opened."""

        wire = self.row.wire
        if wire is not None:
            self._start(self._send(wire, request_id, "cancel", reason))

    @staticmethod
    async def _send(wire: Any, request_id: str, decision: str, reason: str | None) -> Any:
        return await wire.request("approval", {"request": request_id, "decision": decision, "reason": reason})

    # ── asking and answering ────────────────────────────────────────

    async def _ask(self, opened: _Open) -> None:
        try:
            if self._decide is None:
                decision, reason = "cancel", CHILD_NO_APPROVER_REASON
            else:
                decision, reason = await self._decide(opened.request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — an arbiter that raised answers cancel
            decision, reason = "cancel", f"the approval prompt failed ({type(exc).__name__})"
        if decision not in APPROVAL_DECISIONS:
            decision = "cancel"
        # No await between the arbiter's last posture check and the write: the
        # wire writes before its first suspension (``CommandWire.request``).
        await self._write(opened, decision, reason if isinstance(reason, str) else None)

    async def _write(self, opened: _Open, decision: str, reason: str | None) -> None:
        opened.writing = decision
        wire = self.row.wire
        try:
            reply = None if wire is None else await self._send(wire, opened.request.request_id, decision, reason)
        except asyncio.CancelledError:
            # The line was written before the wire's first suspension and may have
            # been read: settled as an ``unknown`` delivery, then the cancel goes
            # on (#612 fix round 1, V-F1 — a wrap-up cancelled in this wait).
            self._settle(opened, decision, "unknown")
            raise
        self._settle(opened, decision, _outcome_of(reply))

    def _settle(self, opened: _Open, decision: str, outcome: str) -> None:
        if outcome == "not_sent":
            self._step(opened, "withdrawn")
            note: str | None = NOT_SENT_NOTE
        else:
            self._step(opened, _step_of(decision))
            note = UNKNOWN_NOTE if outcome == "unknown" else None
        if self._open is opened:
            self._open = None
            self._note(note)

    async def wrap_up(self) -> None:
        """A wrap-up was recorded: answer the open request ``cancel`` first, before the final steer.

        The caller (``runtime.wrap_up``) awaits this before it writes the steer, so
        the two lines reach the child in that order on the one wire. An answer
        whose write already began is left as it is: its line is ahead of the steer.
        Cancelled while it waits for the acknowledgement, the request is closed
        with step ``denied`` (the ``cancel`` was written) and the row's note says
        its delivery is unknown; the caller takes the wrap-up back.
        """

        opened = self._open
        if opened is None or opened.writing is not None:
            return
        if opened.task is not None:
            opened.task.cancel()
        await self._write(opened, "cancel", WRAP_UP_REASON)

    def withdraw(self) -> None:
        """The row is being stopped, cancelled or ended: no answer will be asked for any more.

        Synchronous, idempotent, never raises. With no answer written, the dialog's
        task is cancelled (the TUI unmounts it, the RPC future is dropped) and the
        step is ``withdrawn``; once the answer's write began, the step is that
        decision, emitted now, before the stop.
        """

        opened = self._open
        if opened is None:
            return
        self._open = None
        with contextlib.suppress(Exception):
            if opened.writing is None:
                if opened.task is not None:
                    opened.task.cancel()
                self._step(opened, "withdrawn")
            else:
                self._step(opened, _step_of(opened.writing))
        self._note(None)

    def close(self) -> None:
        """The run ended (``print_channel._end_of_run``): withdraw, then answer nothing more."""

        self.withdraw()
        self.closed = True

    # ── what the row shows ──────────────────────────────────────────

    def _step(self, opened: _Open, step: str) -> None:
        if opened.ended:
            return
        if step != "asked":
            opened.ended = True
        hub = self._hub
        if hub is not None:
            with contextlib.suppress(Exception):
                hub.approval(self.row.id, opened.raw_call_id, opened.request.tool_name, step)

    def _note(self, note: str | None) -> None:
        self.row.approval_note = note
        if self._publish is not None:
            with contextlib.suppress(Exception):
                self._publish()


def withdraw_approval(row: Any) -> None:
    """:meth:`ChildApprovals.withdraw` for a row that may have no router. Never raises."""

    approvals = getattr(row, "approvals", None)
    if approvals is not None:
        with contextlib.suppress(Exception):
            approvals.withdraw()


__all__ = [
    "ANOTHER_OPEN_REASON",
    "APPROVAL_PREFIX",
    "NOT_SENT_NOTE",
    "UNKNOWN_NOTE",
    "UNREADABLE_NOTE",
    "UNREADABLE_REASON",
    "WRAP_UP_REASON",
    "ChildApprovals",
    "withdraw_approval",
]
