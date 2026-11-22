"""#612 (ADR-0308, contract r5 §7.1, §7.4, §7.6) — the parent's router for a child's approval requests.

One :class:`~aelix_agents.child_approvals.ChildApprovals` per channel row. The
stdout pump hands it every ``approval_request`` line before the wire and the
reducer, and it starts a task — the pump never waits for an answer. The task
asks the arbiter and writes the answer on the row's wire; the hub records the
``approval`` steps, and the row's note is what ``/jobs`` shows.

Fails on ``567753c0``: ``aelix_agents.child_approvals`` does not exist, and the
pump, ``RunningChild``, ``runtime.wrap_up`` and every stop door know nothing of it.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agents import command_wire as cw
from aelix_agents.child_approvals import (
    ANOTHER_OPEN_REASON,
    NOT_SENT_NOTE,
    UNKNOWN_NOTE,
    UNREADABLE_NOTE,
    UNREADABLE_REASON,
    WRAP_UP_REASON,
    ChildApprovals,
)
from aelix_agents.child_events import ChildEventHub, wire_call_id
from aelix_agents.print_channel import RunningChild, _end_of_run, _pump_stdout, abort_child
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_agents.stream import LineAssembler, _StreamState
from aelix_coding_agent.builtin.permission import CHILD_NO_APPROVER_REASON
from aelix_coding_agent.subagent_contract import ChildApprovalRequest

from tests.agents_ext.test_agent_background_611 import jobs_on  # noqa: F401 — the fixture
from tests.event_waits import wait_until


def _line(request_id: str | None = "ap-1", **fields: Any) -> str:
    body: dict[str, Any] = {"type": "approval_request"}
    if request_id is not None:
        body["id"] = request_id
    body.update(
        {"toolCallId": "call_1", "toolName": "bash", "kind": "bash", "args": {"command": "make"},
         "yesLabel": None, "redirectLabel": None}
    )
    body.update(fields)
    return json.dumps(body)


class _Wire:
    """Records each request; answers ``outcome`` at once, or once ``gate`` opens."""

    def __init__(self, outcome: str = "queued") -> None:
        self.outcome = outcome
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.gate: asyncio.Event | None = None
        self.attached = True
        self.broken = False
        self.written = asyncio.Event()

    async def request(self, command: str, payload: dict[str, Any], **_kw: Any) -> cw.Reply:
        self.requests.append((command, dict(payload)))
        self.written.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.outcome == "queued":
            return cw.Reply("queued", data={"taken": True} if command == "approval" else {"phase": "streaming"})
        return cw.Reply(self.outcome, error="x")  # type: ignore[arg-type]

    def take(self, line: str) -> bool:
        return line.startswith(cw.RESPONSE_PREFIX)

    def approvals(self) -> list[dict[str, Any]]:
        return [payload for command, payload in self.requests if command == "approval"]


class _Arbiter:
    def __init__(self, answer: tuple[str, str | None] = ("yes", None)) -> None:
        self.answer = answer
        self.asked: list[ChildApprovalRequest] = []
        self.gate: asyncio.Event | None = None
        self.cancelled = 0

    async def __call__(self, request: ChildApprovalRequest) -> tuple[str, str | None]:
        self.asked.append(request)
        try:
            if self.gate is not None:
                await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return self.answer


class _Hub:
    epoch = "e-1"

    def __init__(self) -> None:
        self.steps: list[tuple[str, str, str, str]] = []

    def approval(self, child_id: str, call_id: str, tool_name: str, step: str) -> None:
        self.steps.append((child_id, call_id, tool_name, step))


def _row(wire: Any = None) -> RunningChild:
    row = RunningChild(id="sub-1", profile="general-purpose", parent_tool_call_id="call_parent")
    row.wire = wire if wire is not None else _Wire()
    row.proc = object()
    row.asks_parent = True
    return row


def _router(row: RunningChild, arbiter: Any = None, hub: Any = None) -> tuple[ChildApprovals, list[int]]:
    published: list[int] = []
    router = ChildApprovals(
        row,
        decide=arbiter if arbiter is not None else _Arbiter(),
        hub=hub,
        publish=lambda: published.append(1),
        cwd="/repo/sub",
        parent_cwd="/repo",
    )
    row.approvals = router
    return router, published


async def _settled(router: ChildApprovals) -> None:
    await wait_until(lambda: not router._tasks, what="the router's tasks to end")


# === routing and answers =========================================================


async def test_a_request_is_asked_and_its_answer_written_with_the_steps_and_the_note() -> None:
    row, hub, arbiter = _row(), _Hub(), _Arbiter(("yes", None))
    router, published = _router(row, arbiter, hub)
    router.offer(_line())
    assert row.approval_note == "awaiting approval: bash" and published
    await _settled(router)
    (request,) = arbiter.asked
    assert (request.child_id, request.profile, request.tool_name, request.kind) == ("sub-1", "general-purpose", "bash", "bash")
    assert request.call_id == wire_call_id("call_1") and request.parent_tool_call_id == "call_parent"
    assert request.epoch == "e-1" and request.elsewhere == "sub" and not request.background
    assert request.requester == "general-purpose agent sub-1 asks · in sub"
    assert row.wire.approvals() == [{"request": "ap-1", "decision": "yes", "reason": None}]
    assert [s[3] for s in hub.steps] == ["asked", "allowed"]
    assert hub.steps[0][1] == "call_1", "the hub maps the raw id itself"
    assert row.approval_note is None


@pytest.mark.parametrize("parent_id", ["h:literal", "x" * 129, "\0" * 40], ids=["h-prefixed", "long", "escaping"])
async def test_the_requests_parent_call_id_is_the_one_aelix_child_carries(parent_id: str) -> None:
    """Fix round 1, verify V-N1: ``childApproval.child.parentToolCallId`` is in the ``wire_call_id`` form.

    ``aelix_child`` and ``children/list`` carry ``wire_call_id(parent_tool_call_id)``;
    on ``111e02d7`` the request carried the raw id, so the two differed for an id
    in the ``h:`` namespace or over 128 bytes on the wire.
    """

    hub = ChildEventHub()
    row = _row()
    row.parent_tool_call_id = parent_id
    hub.publish(None, row, None)
    arbiter = _Arbiter(("no", None))
    _router(row, arbiter, None)
    row.approvals.offer(_line())
    await _settled(row.approvals)
    (request,) = arbiter.asked
    assert request.parent_tool_call_id == hub.rows()[0]["parentToolCallId"] == wire_call_id(parent_id)
    assert request.parent_tool_call_id != parent_id


@pytest.mark.parametrize(("answer", "step"), [(("no", None), "denied"), (("cancel", "r"), "denied"), (("yes_session", None), "allowed")])
async def test_the_step_is_the_decision_sent(answer: tuple[str, str | None], step: str) -> None:
    row, hub = _row(), _Hub()
    router, _ = _router(row, _Arbiter(answer), hub)
    router.offer(_line())
    await _settled(router)
    assert [s[3] for s in hub.steps] == ["asked", step]
    assert row.wire.approvals()[0]["decision"] == answer[0]


async def test_no_arbiter_and_one_that_raises_answer_cancel_with_a_reason() -> None:
    async def broken(_request: Any) -> tuple[str, str | None]:
        raise RuntimeError("boom")

    for decide, reason in ((None, CHILD_NO_APPROVER_REASON), (broken, "the approval prompt failed (RuntimeError)")):
        row = _row()
        router = ChildApprovals(row, decide=decide)
        router.offer(_line())
        await _settled(router)
        assert row.wire.approvals() == [{"request": "ap-1", "decision": "cancel", "reason": reason}]


async def test_the_pump_diverts_a_request_and_keeps_reducing_while_its_answer_pends() -> None:
    arbiter = _Arbiter()
    arbiter.gate = asyncio.Event()
    row = _row()
    router, _ = _router(row, arbiter)
    state = _StreamState()
    lines = [
        _line(),
        json.dumps({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "still going"}]}}),
    ]
    reader = asyncio.StreamReader()
    reader.feed_data(("\n".join(lines) + "\n").encode())
    reader.feed_eof()
    seen: list[str | None] = []
    await asyncio.wait_for(_pump_stdout(reader, LineAssembler(), state, lambda s: seen.append(s.last_text), row.wire, router), 5)
    assert seen == ["still going"], "the request never reached the reducer; the next line did, unanswered"
    assert router.open_request is not None and row.wire.approvals() == []
    arbiter.gate.set()
    await _settled(router)
    assert row.wire.approvals()[0]["decision"] == "yes"


async def test_a_second_request_while_one_is_open_and_an_unreadable_one_are_answered_cancel() -> None:
    arbiter = _Arbiter()
    arbiter.gate = asyncio.Event()
    row, hub = _row(), _Hub()
    router, _ = _router(row, arbiter, hub)
    router.offer(_line("ap-1"))
    router.offer(_line("ap-2"))
    router.offer(_line("ap-3", toolName=7))
    await wait_until(lambda: len(row.wire.approvals()) == 2, what="the two at-once answers")
    assert row.wire.approvals() == [
        {"request": "ap-2", "decision": "cancel", "reason": ANOTHER_OPEN_REASON},
        {"request": "ap-3", "decision": "cancel", "reason": UNREADABLE_REASON},
    ]
    assert len(arbiter.asked) == 1 and [s[3] for s in hub.steps] == ["asked"]
    arbiter.gate.set()
    await _settled(router)


async def test_a_line_with_no_readable_id_cannot_be_answered_and_the_row_says_so() -> None:
    """K15."""

    row = _row()
    router, _ = _router(row)
    router.offer(_line(None))
    router.offer("{not json")
    assert row.wire.requests == [] and row.approval_note == UNREADABLE_NOTE


@pytest.mark.parametrize(("outcome", "step", "note"), [("unknown", "allowed", UNKNOWN_NOTE), ("not_sent", "withdrawn", NOT_SENT_NOTE), ("refused", "allowed", None)])
async def test_an_answer_of_unknown_or_no_delivery_is_never_written_twice_and_the_row_says_so(
    outcome: str, step: str, note: str | None
) -> None:
    row, hub = _row(_Wire(outcome)), _Hub()
    router, _ = _router(row, _Arbiter(("yes", None)), hub)
    router.offer(_line())
    await _settled(router)
    assert len(row.wire.approvals()) == 1
    assert [s[3] for s in hub.steps] == ["asked", step]
    assert row.approval_note == note


_MISSING = object()


@pytest.mark.parametrize(
    ("kind", "shown"),
    [
        ("bash", "bash"), ("write", "write"), ("edit", "edit"), ("other", "other"),
        ("exec", "other"), ("BASH", "other"), ("", "other"), (None, "other"), (3, "other"), (_MISSING, "other"),
    ],
)
async def test_a_kind_the_dialog_does_not_draw_is_asked_as_other(kind: Any, shown: str) -> None:
    """V-F5 A12 (fix round 1): the request's ``kind`` picks how the dialog draws the call; one
    the dialogs do not know (or none) is ``other``, which shows every argument raw. Passes on
    ``111e02d7`` (it pins the normalisation the mutant removed)."""

    row, arbiter = _row(), _Arbiter(("no", None))
    router, _ = _router(row, arbiter)
    line = json.loads(_line())
    if kind is _MISSING:
        del line["kind"]
    else:
        line["kind"] = kind
    router.offer(json.dumps(line))
    await _settled(router)
    (request,) = arbiter.asked
    assert request.kind == shown


class _Stdin:
    """A child's stdin: what reached it, and a drain that never blocks."""

    transport = None

    def __init__(self) -> None:
        self.lines: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.lines.append(data)

    async def drain(self) -> None:
        return None


def _real_wire() -> tuple[cw.CommandWire, _Stdin]:
    wire, stdin = cw.CommandWire("sub-1"), _Stdin()
    wire.attach(stdin)
    return wire, stdin


def _break(wire: cw.CommandWire, how: str) -> None:
    if how == "broken":  # an earlier write past its bound (ADR-0306 §3.6)
        wire.broken = True
    else:  # the child's end closed it
        wire.closed = True


@pytest.mark.parametrize("how", ["broken", "closed"])
async def test_an_answer_the_wire_refuses_unwritten_is_recorded_not_sent(how: str) -> None:
    """#612 fix round 1, P-M1: the wire breaks while the human decides. ``CommandWire.request``
    refuses the answer without writing it; the row says so, as for ``not_sent``.

    Fails on ``111e02d7``: steps ``['asked', 'allowed']`` and no note, nothing written."""

    wire, stdin = _real_wire()
    row, hub, arbiter = _row(wire), _Hub(), _Arbiter(("yes", None))
    arbiter.gate = asyncio.Event()
    router, _ = _router(row, arbiter, hub)
    router.offer(_line())
    await wait_until(lambda: arbiter.asked, what="the human to be asked")
    _break(wire, how)
    arbiter.gate.set()
    await _settled(router)
    assert stdin.lines == [], "the refused answer was written"
    assert [s[3] for s in hub.steps] == ["asked", "withdrawn"]
    assert row.approval_note == NOT_SENT_NOTE
    assert router.open_request is None


@pytest.mark.parametrize("how", ["broken", "closed"])
async def test_a_request_on_a_wire_that_cannot_answer_asks_no_one_and_is_recorded_not_sent(how: str) -> None:
    """P-M1: the wire is broken (or closed) when the request arrives — no answer could be
    written, so no dialog opens; the row says the answer was not sent.

    Fails on ``111e02d7``: the human is asked and the step after is ``allowed``."""

    wire, stdin = _real_wire()
    _break(wire, how)
    row, hub, arbiter = _row(wire), _Hub(), _Arbiter(("yes", None))
    router, _ = _router(row, arbiter, hub)
    router.offer(_line())
    await _settled(router)
    assert arbiter.asked == [], "a human was asked for an answer that could not be sent"
    assert stdin.lines == []
    assert [s[3] for s in hub.steps] == ["asked", "withdrawn"]
    assert row.approval_note == NOT_SENT_NOTE
    assert router.open_request is None


async def test_an_answer_written_before_the_child_ended_stays_of_unknown_delivery() -> None:
    """Guard for P-M1's line (passes on ``111e02d7``): an answer WRITTEN and still awaiting its
    acknowledgement when the child's end closes the wire (``fail_pending``: ``unknown``,
    ``ended``) may have been read — it is the decision sent, of unknown delivery, never
    ``not_sent``. Only a ``refused`` reply the wire gave unwritten is."""

    wire, stdin = _real_wire()
    row, hub = _row(wire), _Hub()
    router, _ = _router(row, _Arbiter(("yes", None)), hub)
    router.offer(_line())
    await wait_until(lambda: stdin.lines, what="the answer's write")
    wire.close()
    await _settled(router)
    assert len(stdin.lines) == 1
    assert [s[3] for s in hub.steps] == ["asked", "allowed"]
    assert row.approval_note == UNKNOWN_NOTE


async def test_a_refusal_the_child_gave_still_records_the_decision_sent() -> None:
    """Guard (passes on ``111e02d7``): a ``refused`` the CHILD answered ("not pending") reached
    it — the decision sent is the step, not ``withdrawn``."""

    wire, stdin = _real_wire()
    row, hub = _row(wire), _Hub()
    router, _ = _router(row, _Arbiter(("no", None)), hub)
    router.offer(_line())
    await wait_until(lambda: stdin.lines, what="the answer's write")
    (written,) = stdin.lines
    request_id = json.loads(written)["id"]
    assert wire.take(json.dumps({"type": "response", "id": request_id, "success": False, "error": "not pending"}))
    await _settled(router)
    assert [s[3] for s in hub.steps] == ["asked", "denied"]
    assert row.approval_note is None


# === wrap-up ==================================================================


def _runtime_with(row: RunningChild) -> _SubagentRuntimeImpl:
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: ".", session=lambda: None))
    runtime._children[row.id] = row
    return runtime


async def test_a_wrap_up_answers_the_open_request_cancel_first_then_writes_the_final_steer() -> None:
    arbiter = _Arbiter()
    arbiter.gate = asyncio.Event()
    row, hub = _row(), _Hub()
    router, _ = _router(row, arbiter, hub)
    runtime = _runtime_with(row)
    router.offer(_line())
    await wait_until(lambda: arbiter.asked, what="the dialog to open")
    delivery = await runtime.wrap_up(row.id)
    assert delivery.outcome == "queued"
    assert [c for c, _ in row.wire.requests] == ["approval", "steer"]
    assert row.wire.requests[0][1] == {"request": "ap-1", "decision": "cancel", "reason": WRAP_UP_REASON}
    assert row.wire.requests[1][1]["final"] is True
    await wait_until(lambda: arbiter.cancelled == 1, what="the dialog to be closed")
    assert [s[3] for s in hub.steps] == ["asked", "denied"]
    # While it stays recorded, the next request is answered at once, unasked.
    router.offer(_line("ap-2"))
    await _settled(router)
    assert row.wire.approvals()[-1] == {"request": "ap-2", "decision": "cancel", "reason": WRAP_UP_REASON}
    assert len(arbiter.asked) == 1


async def test_a_wrap_up_taken_back_as_not_sent_lifts_the_at_once_cancel() -> None:
    from aelix_agents.runtime import _record_wrap_up

    row = _row()
    arbiter = _Arbiter()
    router, _ = _router(row, arbiter)
    runtime = _runtime_with(row)
    _record_wrap_up(runtime, row, cw.new_wrap_up("user"))
    router.offer(_line("ap-1"))
    await _settled(router)
    assert row.wire.approvals()[-1]["reason"] == WRAP_UP_REASON and arbiter.asked == []
    _record_wrap_up(runtime, row, None)
    router.offer(_line("ap-2"))
    await _settled(router)
    assert len(arbiter.asked) == 1 and row.wire.approvals()[-1] == {"request": "ap-2", "decision": "yes", "reason": None}


async def test_a_wrap_up_cancelled_while_its_approval_cancel_awaits_the_ack_is_taken_back() -> None:
    """#612 fix round 1, V-F1 (verify r1's probe ``wrapup/…cancelled_in_approval_cancel``).

    The cancel lands while the open request's ``cancel`` waits for the child's
    acknowledgement — before the final steer's write began. The request closes
    with its step and note, the wrap-up is taken back, the cancel comes out as
    ``InterventionNotSent``, and a later wrap-up and request are served again.
    On ``111e02d7`` the wrap-up stays recorded, the router keeps the request
    open (steps ``['asked']``), the retry is refused "already requested" and
    the next request is answered "another approval request is open".
    """

    arbiter = _Arbiter()
    arbiter.gate = asyncio.Event()
    row, hub = _row(), _Hub()
    router, _ = _router(row, arbiter, hub)
    runtime = _runtime_with(row)
    router.offer(_line("ap-1"))
    await wait_until(lambda: arbiter.asked, what="the dialog to open")
    row.wire.gate = asyncio.Event()  # the child has not acknowledged the cancel line yet
    wrap = asyncio.ensure_future(runtime.wrap_up(row.id))
    await wait_until(lambda: [c for c, _ in row.wire.requests] == ["approval"], what="the cancel line written")
    wrap.cancel()
    with pytest.raises(asyncio.CancelledError) as raised:
        await wrap
    row.wire.gate = None
    assert [c for c, _ in row.wire.requests] == ["approval"], "no final steer was written"
    assert row.wrap_up is None and row.stream.wrap_up_head is None
    assert type(raised.value).__name__ == "InterventionNotSent"
    assert router.open_request is None
    assert [s[3] for s in hub.steps] == ["asked", "denied"]
    assert row.approval_note == UNKNOWN_NOTE
    again = await runtime.wrap_up(row.id)
    assert again.outcome == "queued" and [c for c, _ in row.wire.requests] == ["approval", "steer"]
    # A wrap-up recorded again answers the next request at once; taken back, it is asked.
    from aelix_agents.runtime import _record_wrap_up

    _record_wrap_up(runtime, row, None)
    router.offer(_line("ap-2"))
    await wait_until(lambda: len(arbiter.asked) == 2, what="the next request asked")
    arbiter.gate.set()
    await _settled(router)
    assert row.wire.approvals()[-1] == {"request": "ap-2", "decision": "yes", "reason": None}


async def test_a_cancel_of_an_answers_write_closes_the_request_as_an_unknown_delivery() -> None:
    """#612 fix round 1: ``_write`` cancelled in its acknowledgement wait settles before the cancel goes on."""

    wire = _Wire()
    wire.gate = asyncio.Event()
    row, hub = _row(wire), _Hub()
    router, _ = _router(row, _Arbiter(("no", "not now")), hub)
    router.offer(_line())
    await asyncio.wait_for(wire.written.wait(), 5)
    (task,) = router._tasks
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert router.open_request is None and row.approval_note == UNKNOWN_NOTE
    assert [s[3] for s in hub.steps] == ["asked", "denied"]


# === withdraw: stops, cancels, ends =============================================


async def test_a_stop_before_any_answer_withdraws_the_dialog_and_writes_nothing() -> None:
    arbiter = _Arbiter()
    arbiter.gate = asyncio.Event()
    row, hub = _row(), _Hub()
    row.proc = None  # ``abort_child``'s no-process branch: nothing to signal here
    router, _ = _router(row, arbiter, hub)
    router.offer(_line())
    await wait_until(lambda: arbiter.asked, what="the dialog to open")
    await abort_child(row)
    await _settled(router)
    assert arbiter.cancelled == 1 and row.wire.approvals() == []
    assert [s[3] for s in hub.steps] == ["asked", "withdrawn"] and row.approval_note is None


async def test_a_stop_after_the_answers_write_began_records_the_decision_not_withdrawn() -> None:
    """§7 review L5."""

    wire = _Wire()
    wire.gate = asyncio.Event()
    row, hub = _row(wire), _Hub()
    router, _ = _router(row, _Arbiter(("yes", None)), hub)
    router.offer(_line())
    await asyncio.wait_for(wire.written.wait(), 5)
    router.withdraw()
    assert [s[3] for s in hub.steps] == ["asked", "allowed"]
    wire.gate.set()
    await _settled(router)
    assert [s[3] for s in hub.steps] == ["asked", "allowed"], "not emitted twice"


async def test_the_runs_end_withdraws_and_answers_nothing_more() -> None:
    arbiter = _Arbiter()
    arbiter.gate = asyncio.Event()
    row, hub = _row(), _Hub()
    row.owner = object()  # a service-owned row: ``_end_of_run`` leaves its directory alone
    router, _ = _router(row, arbiter, hub)
    router.offer(_line())
    await wait_until(lambda: arbiter.asked, what="the dialog to open")
    _end_of_run(row, 1.0)
    router.offer(_line("ap-2"))
    await _settled(router)
    assert [s[3] for s in hub.steps] == ["asked", "withdrawn"] and router.closed
    assert row.wire.approvals() == []


async def test_a_job_rows_stop_withdraws_first() -> None:
    from aelix_agents.jobs_client import AdoptedRunner

    arbiter = _Arbiter()
    arbiter.gate = asyncio.Event()
    row, hub = _row(), _Hub()
    router, _ = _router(row, arbiter, hub)
    router.offer(_line())
    await wait_until(lambda: arbiter.asked, what="the dialog to open")
    runner = AdoptedRunner.__new__(AdoptedRunner)
    runner._stopped_by, runner._row, runner._stop = None, row, None
    attribution = "stopped by the RPC host (children/stop)"
    runner._ctx = SimpleNamespace(stop_attribution=attribution)

    class _Runtime:
        async def stop(self, _id: str) -> None:
            return None

    runner._runtime = _Runtime()
    runner.request_stop("user")
    assert [s[3] for s in hub.steps] == ["asked", "withdrawn"] and row.stopped
    assert row._stop_attribution == attribution
    await runner._stop
    await _settled(router)


async def test_host_controls_stop_withdraws_before_either_door_runs() -> None:
    """The lead's call after part A: ``children/stop`` on a row waiting for approval withdraws first."""

    from aelix_agents.host_controls import HostControls

    arbiter = _Arbiter()
    arbiter.gate = asyncio.Event()
    hub = ChildEventHub()
    row = _row()
    row.proc = None
    row.state = "running"
    runtime = _runtime_with(row)
    runtime.events = hub
    hub.publish(runtime, row, None, ())
    router, _ = _router(row, arbiter, hub)
    router.offer(_line())
    await wait_until(lambda: arbiter.asked, what="the dialog to open")
    assert hub.rows()[0]["approval"] == {"callId": wire_call_id("call_1"), "toolName": "bash"}
    controls = HostControls(hub)

    class _Door:
        """A runtime whose stop door does nothing: only the controls' own withdraw can act."""

        async def stop(self, _id: str) -> None:
            return None

    controls._live = lambda _id: (_Door(), row)  # type: ignore[method-assign]
    await controls.stop(row.id)
    steps = [e["data"]["step"] for e in hub.events(row.id, None)["events"] if e["event"] == "approval"]
    assert steps == ["asked", "withdrawn"]
    assert hub.rows()[0]["approval"] is None
    await _settled(router)


# === what the surfaces show ====================================================


def test_the_progress_snapshot_and_the_job_rows_carry_the_note() -> None:
    from aelix_agents.jobs_client import _doing_row
    from aelix_coding_agent.jobs.contract import JobSnapshot

    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: ".", session=lambda: None))
    row = _row()
    row.approval_note = "awaiting approval: bash"
    seen: list[Any] = []
    runtime._publish(row, row.stream, seen.append)
    assert seen[0].approval == "awaiting approval: bash"
    snap = JobSnapshot.__new__(JobSnapshot)
    object.__setattr__(snap, "state", "running")
    assert _doing_row(snap, {"tool": "bash", "approval": "awaiting approval: bash"}) == "awaiting approval: bash"
    assert _doing_row(snap, {"tool": "bash"}) == "running bash"


# === the whole pipe on a real child =============================================


@pytest.mark.skipif(sys.platform == "win32", reason="a real child's pipes are POSIX here")
@pytest.mark.parametrize(("answer", "ran"), [(("yes", None), True), (("no", None), False)])
async def test_a_real_child_launched_by_the_channel_asks_the_arbiter_and_runs_what_it_approved(
    tmp_path: Path, answer: tuple[str, str | None], ran: bool
) -> None:
    """The channel launches ``=2`` for a row that asks, the pump hands the request to the router,
    the arbiter answers, the wire writes the answer, the child runs (or refuses) the call."""

    from aelix_agents.print_channel import PrintChannel

    from tests.agents_ext.test_print_channel_spawn import _plan
    from tests.cli.test_command_inbox_123 import (
        _child_setup,
        _ModelServer,
        _text_turn,
        _texts,
        _tool_turn,
    )

    def script(n: int) -> Any:
        return _tool_turn("bash", {"command": "echo approved > out.txt"}) if n == 1 else _text_turn("done")

    with _ModelServer(script=script) as server:
        cwd, env = _child_setup(tmp_path, server)
        env["AELIX_SUBAGENT_DEPTH"] = "1"

        def argv(*_a: Any, **k: Any) -> list[str]:
            inbox = "2" if k.get("asks_parent") else "1"
            return [
                sys.executable, "-P", "-m", "aelix_coding_agent", "--mode", "json", "-p",
                f"--command-inbox={inbox}", "--model", "mockm", "--provider", "mockp", "--no-session",
                "--no-extensions", "--permission-mode", k["permission_mode"].value, "--no-agents",
                "Task: write approved to out.txt",
            ]

        channel = PrintChannel(grace=2.0, argv_builder=argv, env_builder=lambda *_a, **_k: dict(env))
        from aelix_coding_agent.builtin.permission_mode import PermissionMode

        plan = _plan(tmp_path, cwd=str(cwd), parent_cwd=str(cwd), permission_mode=PermissionMode.DEFAULT, asks_parent=True)
        row = RunningChild(id=plan.id, profile="scout")
        arbiter = _Arbiter(answer)
        hub = _Hub()
        row.approvals = ChildApprovals(row, decide=arbiter, hub=hub, cwd=str(cwd), parent_cwd=str(cwd))
        result = await asyncio.wait_for(channel.run(plan, child=row), 90)
        tool = [t for t in _texts(server.requests[1]) if t.startswith("tool:")]
    assert len(arbiter.asked) == 1 and arbiter.asked[0].args == {"command": "echo approved > out.txt"}
    assert [s[3] for s in hub.steps] == ["asked", "allowed" if ran else "denied"]
    assert (cwd / "out.txt").exists() is ran
    assert result.status == "ok", result
    assert tool == (["tool:(no output)"] if ran else ["tool:Denied by the user."])


async def test_an_answer_the_arbiter_cannot_give_is_a_cancel() -> None:
    row = _row()
    router, _ = _router(row, _Arbiter(("maybe", None)))
    router.offer(_line())
    await _settled(router)
    assert row.wire.approvals() == [{"request": "ap-1", "decision": "cancel", "reason": None}]


async def test_a_wrap_up_after_the_answers_write_began_writes_no_second_answer() -> None:
    wire = _Wire()
    wire.gate = asyncio.Event()
    row = _row(wire)
    router, _ = _router(row, _Arbiter(("yes", None)))
    router.offer(_line())
    await asyncio.wait_for(wire.written.wait(), 5)
    await asyncio.wait_for(router.wrap_up(), 2)
    assert wire.approvals() == [{"request": "ap-1", "decision": "yes", "reason": None}]
    wire.gate.set()
    await _settled(router)


def test_a_second_cancel_of_the_parents_turn_withdraws_before_the_kill() -> None:
    """``PrintChannel._eager_abort`` (Esc twice on the parent's call)."""

    from aelix_agents.print_channel import PrintChannel

    arbiter = _Arbiter()
    row, hub = _row(), _Hub()

    class _Owner:
        stopped = 0

        def stop_now(self) -> None:
            self.stopped += 1

    row.owner = _Owner()
    router, _ = _router(row, arbiter, hub)
    router._open = __import__("aelix_agents.child_approvals", fromlist=["_Open"])._Open(
        router._request("ap-1", json.loads(_line())),  # type: ignore[arg-type]
        raw_call_id="call_1",
    )
    PrintChannel._eager_abort(object(), row)
    assert [s[3] for s in hub.steps] == ["withdrawn"] and row.owner.stopped == 1


async def test_a_spawned_rows_job_stop_withdraws_first() -> None:
    from aelix_agents.jobs_client import DelegationRunner

    arbiter = _Arbiter()
    arbiter.gate = asyncio.Event()
    row, hub = _row(), _Hub()
    router, _ = _router(row, arbiter, hub)
    router.offer(_line())
    await wait_until(lambda: arbiter.asked, what="the dialog to open")
    runner = DelegationRunner.__new__(DelegationRunner)
    runner._stopped_by, runner._own = None, None
    attribution = "stopped by the RPC host (children/stop)"
    runner._ctx = SimpleNamespace(stop_attribution=attribution)
    runner._runtime = _runtime_with(row)
    runner.request_stop("user")
    assert [s[3] for s in hub.steps] == ["asked", "withdrawn"] and row.stopped
    assert row._stop_attribution == attribution
    await _settled(router)


def test_the_jobs_progress_and_its_status_line_carry_the_note() -> None:
    from aelix_agents.jobs_client import DelegationJobs, _job_progress, _status_line
    from aelix_coding_agent.subagent_contract import SubagentProgress

    sent: list[dict[str, Any]] = []

    class _Ctx:
        def progress(self, snapshot: dict[str, Any]) -> None:
            sent.append(snapshot)

    _job_progress(_Ctx(), SubagentProgress(id="sub-1", profile="p", state="running", approval="awaiting approval: bash"), None)
    assert sent[0]["approval"] == "awaiting approval: bash"

    class _Snap:
        job_id, label, state, elapsed, quiet = "j1", "agent", "running", 3.0, False
        progress = {"tool": "bash", "approval": "awaiting approval: bash"}

    line = _status_line(DelegationJobs(), _Snap())  # type: ignore[arg-type]
    assert "awaiting approval: bash" in line and "running bash" not in line


# === through the agents extension: the model's ``run`` and ``spawn`` ================

_ASKING_STUB_BODY = """
start()
emit({"type": "tool_execution_start", "tool_name": "bash", "tool_call_id": "c1", "args": {"command": "make"}})
emit({"type": "approval_request", "id": "ap-1", "toolCallId": "c1", "toolName": "bash", "kind": "bash",
      "args": {"command": "make"}, "yesLabel": None, "redirectLabel": None})
req = json.loads(sys.stdin.readline())
emit({"type": "response", "command": req["type"], "id": req["id"], "success": True, "data": {"taken": True}})
emit({"type": "tool_execution_end", "tool_call_id": "c1", "result": {"content": [{"type": "text", "text": "r"}]}})
say("decided " + str(req.get("decision")) + " " + sys.argv[1] + " " + sys.argv[2])
done()
if os.environ.get("AELIX_JOBS_PROTOCOL") == "1":
    emit({"type": "aelix_print_done"})
"""


def _asking_channel() -> Any:
    """A stub child that asks once, reads the answer off its inbox, says it, and names its argv's posture and inbox."""

    import os

    from aelix_agents.print_channel import PrintChannel

    from tests.agents_ext.test_print_channel_spawn import _stub

    script = _stub(_ASKING_STUB_BODY)
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME", "TMPDIR", "SYSTEMROOT")}

    def argv(*_a: Any, **k: Any) -> list[str]:
        full = build_child_argv(*_a, **k)
        return [sys.executable, "-c", script, k["permission_mode"].value, full[-1]]

    from aelix_agents.print_channel import build_child_argv

    return PrintChannel(grace=1.0, argv_builder=argv, env_builder=lambda *_a, **_k: {**base, "AELIX_JOBS_PROTOCOL": "1"})


@pytest.mark.skipif(sys.platform == "win32", reason="the stub child's pipes are POSIX here")
@pytest.mark.usefixtures("jobs_on")
@pytest.mark.parametrize("action", ["run", "spawn"])
async def test_the_models_run_and_spawn_ask_this_processs_arbiter(tmp_path: Path, action: str) -> None:
    """``extension.py`` reads the channel (``asks_parent``) and hands the arbiter to the foreground
    runtime's host and to a background run's launch; the row asks it and the child reads its answer."""

    import dataclasses

    from tests.agents_ext.test_agent_background_611 import _rig, _settled, _spawned_id
    from tests.agents_ext.test_tool_and_security import _text

    arbiter = _Arbiter(("yes_session", None))
    rig = await _rig(tmp_path, channel=_asking_channel())
    rig.ext.asks_parent = lambda: True
    rig.ext.decide_for_child = arbiter
    assert rig.ext.runtime is not None
    # The build's host as ``_host()`` makes it (the arbiter is read there), with the
    # job facade's admission ``_register_kind`` put on it.
    built = rig.ext.runtime.host
    rig.ext.runtime.host = dataclasses.replace(rig.ext._host(), reserve=built.reserve, claimed=built.claimed)
    try:
        await rig.prompt_started()
        args: dict[str, Any] = {"profile": "scout", "task": "build it"}
        if action == "spawn":
            args["action"] = "spawn"
        result = await asyncio.wait_for(rig.call(args), 30)
        if action == "spawn":
            _spawned_id(result)
            snap = await _settled(rig)
            said = snap.brief
        else:
            said = _text(result)
        assert "decided yes_session default --command-inbox=2" in said, said
        (asked,) = arbiter.asked
        assert asked.tool_name == "bash" and asked.background is (action == "spawn")
    finally:
        await rig.service.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="the stub child's pipes are POSIX here")
@pytest.mark.usefixtures("jobs_on")
async def test_the_users_run_door_reads_the_extensions_channel_too(tmp_path: Path) -> None:
    """``/agents run`` and RPC ``agents/run`` reach ``runtime.spawn``, whose consent reads the host's
    ``asks_parent`` — the extension's getter (``_host()``)."""

    import dataclasses

    from tests.agents_ext.test_agent_background_611 import _rig
    from tests.agents_ext.test_print_channel_spawn import _resolved as _channel_resolved

    arbiter = _Arbiter(("no", None))
    rig = await _rig(tmp_path, channel=_asking_channel())
    rig.ext.asks_parent = lambda: True
    rig.ext.decide_for_child = arbiter
    assert rig.ext.runtime is not None
    built = rig.ext.runtime.host
    rig.ext.runtime.host = dataclasses.replace(rig.ext._host(), reserve=built.reserve, claimed=built.claimed)
    try:
        result = await asyncio.wait_for(rig.ext.runtime.spawn(_channel_resolved(), "build it"), 30)
        assert "decided no default --command-inbox=2" in result.summary, result.summary
    finally:
        await rig.service.shutdown()
