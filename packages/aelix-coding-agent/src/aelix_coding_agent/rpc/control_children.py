"""The RPC ``children/`` family: a host's view of the delegated children (#612, ADR-0307).

Six methods (contract r5 §3.1, a proposal; owner Q4 2026-11-18 "전부 열기 (권장)"
for ``children/notices``):

* ``children/list`` and ``children/events`` read the build's child-event hub,
  lock-free (``READ_ONLY_METHODS``);
* ``children/steer``, ``children/wrap_up`` and ``children/stop`` reach #123's
  doors through the seam's optional ``controls`` (``CONCURRENT_METHODS``,
  admitted by :meth:`RpcControlPlane._concurrent`);
* ``children/notices`` runs one notice turn for what the job service holds
  (``MUTATING_METHODS`` and ``CANCELLABLE_METHODS``), the host's wake after an
  ``aelix_notice`` frame.

Product core never imports ``aelix_agents``: every answer here is the seam's
JSON-ready data (``SubagentControls``), and a request the controls cannot
serve is their :class:`ValueError`. A ``refused`` ``Delivery`` is an error
response with its reason (ADR-0302 D2.3); its state word is what a fixture pins.

AN INTERVENTION A SESSION CHANGE INTERRUPTS ANSWERS BY ITS STAGE (contract r5
§3.4). Admission, the parameters and the call into the controls run in one
step with no ``await``. A steer's door writes its line before its own first
suspension (``CommandWire.request``), so a cancel lands only once the line was
written; it may have been read, and the answer is ``unknown`` with the reason
saying why it ended. A wrap-up's door has one suspension before its final
steer: the ``cancel`` it writes to an open approval request first waits for its
acknowledgement (ADR-0308). A cancel landing there takes the wrap-up back and
raises a cancel whose ``not_sent`` is true (``SubagentControls.wrap_up``),
answered as the error "… before this request was sent" (#612 fix round 1, V-F1).
A third-party ``controls`` that awaits before writing and raises a plain cancel
gets ``unknown``, the answer that never claims a line was not sent. A stop
answers the state the hub holds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.agents.sendable import unsendable_refusal

if TYPE_CHECKING:
    from aelix_coding_agent.rpc.control import RpcControlPlane

NO_DELEGATED_AGENTS = "This RPC host has no delegated agents"
NO_JOB_SERVICE = "This RPC host has no job service"
SESSION_CHANGE_IN_PROGRESS = "a session change is in progress; retry when it has finished"
SETTLE_GRACE_SECONDS = 1.0
"""How long a session change waits for the interventions it cancelled (contract r5 §3.4, a proposal)."""
STEER_MODES = ("steer", "follow_up")

_UNANSWERED = {
    "session": "a session change began before the agent answered",
    "shutdown": "the RPC host began shutting down before the agent answered",
}
_NOT_SENT = {
    "session": "a session change began before this request was sent",
    "shutdown": "the RPC host began shutting down before this request was sent",
}
_DELIVERY_KEYS = ("outcome", "phase", "verb", "ended", "reason")


@dataclass
class Intervention:
    """One admitted ``CONCURRENT_METHODS`` request (contract r5 §3.4)."""

    method: str
    task: asyncio.Task[Any] | None
    generation: int
    """The harness binding that admitted it."""
    controls: Any
    """That generation's controls, the only ones this request ever calls."""
    why: str | None = None
    """``session`` or ``shutdown`` once the plane cancelled it; ``None`` otherwise."""


# === parameters ===================================================================


def _child_id(params: dict[str, Any]) -> str:
    value = params.get("id")
    if not isinstance(value, str) or not value:
        raise ValueError("id must be a non-empty string naming a delegated agent")
    return value


def _text(params: dict[str, Any]) -> str:
    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    if len(text) > 100_000:  # ``commands/run``'s and ``agents/run``'s bound
        raise ValueError("text must fit in 100000 characters")
    # A NUL or half of a character would break the child's inbox line (#627).
    refusal = unsendable_refusal(text, where="The text", resend="the text")
    if refusal is not None:
        raise ValueError(refusal)
    return text


def _mode(params: dict[str, Any]) -> str:
    mode = params.get("mode")
    if mode is None:
        return "steer"
    if mode not in STEER_MODES:
        raise ValueError("mode must be steer or follow_up")
    return str(mode)


def _after_seq(params: dict[str, Any]) -> int | None:
    value = params.get("afterSeq")
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError("afterSeq must be an integer")
    return value


# === reads and the wake ============================================================


async def children_call(plane: RpcControlPlane, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """``children/list``, ``children/events``, ``children/notices``."""

    if method == "children/notices":
        return await _notices(plane)
    controls = plane.controls
    if controls is None:
        raise ValueError(NO_DELEGATED_AGENTS)
    if method == "children/list":
        return {"epoch": controls.epoch, "children": controls.list()}
    if method == "children/events":
        return dict(controls.events(_child_id(params), _after_seq(params)))
    raise ValueError(f"Unsupported runtime control method: {method}")


async def _notices(plane: RpcControlPlane) -> dict[str, Any]:
    """One notice turn for what waits (``/jobs send``'s door), and what it left waiting.

    ``placed`` is ``PromptNoticesResult``'s: what the turn placed before its
    first model call, one drain budget (64 KiB). Each boundary inside the turn
    claims another, so one call can place more than ``placed`` says (fix round 1,
    Codex C-M9; narrowed from contract r5 §3.1, the lead's call). ``remaining``,
    the inbox's count after the turn, is the host's cue to call again (Codex 12).
    """

    jobs = plane.jobs
    if jobs is None:
        raise ValueError(NO_JOB_SERVICE)
    result = await jobs.send_waiting()
    harness = plane.harness
    remaining = harness.pending_notices if getattr(harness, "notices_enabled", False) is True else 0
    return {
        "started": result.started,
        "placed": result.placed,
        "reason": result.reason,
        "remaining": int(remaining),
    }


# === interventions =================================================================


async def intervene(record: Intervention, params: dict[str, Any]) -> dict[str, Any]:
    """``children/steer``, ``children/wrap_up`` or ``children/stop`` on ``record``'s controls."""

    child_id = _child_id(params)
    controls = record.controls
    if record.method == "children/stop":
        return await _stop(record, child_id)
    if record.method == "children/steer":
        text, mode = _text(params), _mode(params)
        return await _deliver(record, lambda: controls.steer(child_id, text, mode), mode)
    return await _deliver(record, lambda: controls.wrap_up(child_id), "wrap_up")


async def _deliver(
    record: Intervention, call: Callable[[], Awaitable[dict[str, Any]]], verb: str
) -> dict[str, Any]:
    try:
        answer = await call()
    except asyncio.CancelledError as cancel:
        if record.why is None:
            raise
        _uncancel()
        if getattr(cancel, "not_sent", False) is True:
            raise ValueError(_NOT_SENT[record.why]) from None
        return {
            "outcome": "unknown",
            "phase": None,
            "verb": verb,
            "ended": False,
            "reason": _UNANSWERED[record.why],
        }
    if answer.get("outcome") == "refused":
        raise ValueError(answer.get("reason") or "refused")
    return {key: answer.get(key) for key in _DELIVERY_KEYS}


async def _stop(record: Intervention, child_id: str) -> dict[str, Any]:
    try:
        answer = await record.controls.stop(child_id)
    except asyncio.CancelledError:
        if record.why is None:
            raise
        state = _state_now(record.controls, child_id)
        if state is None:
            raise
        _uncancel()
        return {"state": state}
    return {"state": answer.get("state")}


def _state_now(controls: Any, child_id: str) -> str | None:
    """The state the hub holds for a stop a session change interrupted."""

    for row in controls.list():
        if row.get("id") == child_id:
            return row.get("state")
    return None


def _uncancel() -> None:
    """The plane's own cancel is answered, not propagated: take it back off the task."""

    task = asyncio.current_task()
    if task is not None:
        task.uncancel()


__all__ = [
    "NO_DELEGATED_AGENTS",
    "NO_JOB_SERVICE",
    "SESSION_CHANGE_IN_PROGRESS",
    "SETTLE_GRACE_SECONDS",
    "STEER_MODES",
    "Intervention",
    "children_call",
    "intervene",
]
