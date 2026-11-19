"""The ``full`` and ``turn`` rigs' delegated children, for #612's fixtures (ADR-0307, ADR-0308; contract r5 §9.5).

What a host reaches is the product's own: a real
:class:`~aelix_agents.child_events.ChildEventHub` fed by a real
``_SubagentRuntimeImpl._publish`` from real ``RunningChild`` rows reduced by the
real stream reducer, and a real :class:`~aelix_agents.host_controls.HostControls`
whose steer and wrap-up are the runtime's own doors (``_door_refusal``'s states,
``Delivery.of``'s words). Only what a rig cannot have is scripted:

* no child process — a row's inbox is a :class:`ScriptedWire` that answers every
  line with the reply its row was given (``queued``, ``unknown``, ``unknown``
  with the child ended, ``not_sent``), and :meth:`RigRuntime.stop` marks the row
  stopped and publishes its terminal frame where the real door would kill and
  reap a process;
* no job service behind a job row — its stop is recorded and returns at once,
  as the job service's does before its stop legs (so the row still reads
  ``running``);
* no model — the rows' events are fed by hand (:meth:`RigChildren.feed`).

The ``children`` setup (:meth:`RigChildren.populate`) lays out one row per state
a fixture reads; ``agents/run`` with a task named in :data:`SCRIPTS`, ``hold``
or ``ask`` drives a row while the call runs (:meth:`RigChildren.spawn`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

#: The row ids the ``children`` setup lays out (the fixtures name them).
RUNNING = "sub-running"  # an attached inbox; every line is queued while streaming
UNKNOWN = "sub-unknown"  # every line's delivery is unknown (no acknowledgement)
ENDED_OPEN = "sub-ended-open"  # the prompt returned with the line open
NOT_SENT = "sub-not-sent"  # the line could not be written
STARTING = "sub-starting"  # registered, no process yet
STOPPING = "sub-stopping"  # a stop is under way
BROKEN = "sub-broken"  # the inbox is broken
JOB = "sub-job"  # a background (spawned) row: its stop is the job's
ASKING = "sub-asking"  # an approval request is open
DONE = "sub-done"  # finished: kept as ended, out of the registry
CHATTY = "sub-chatty"  # more events than the ring keeps
TOMB = "sub-tomb"  # ended long ago: only its tombstone is kept
HELD = "sub-held"  # the child a held ``agents/run`` runs

PROFILE = "reviewer"
PARENT_CALL = "call-parent-1"
JOB_ID = "job-rig-1"


class ScriptedWire:
    """A command inbox whose every request is answered ``reply`` (``CommandWire``'s interface)."""

    def __init__(self, reply: Any, *, broken: bool = False) -> None:
        self.reply = reply
        self.broken = broken
        self.attached = True
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def request(self, verb: str, payload: dict[str, Any]) -> Any:
        self.sent.append((verb, payload))
        return self.reply


def _reply(outcome: str, *, phase: str | None = None, ended: bool = False) -> Any:
    from aelix_agents.command_wire import Reply

    data = {"taken": True, "phase": phase} if phase is not None else None
    error = "no acknowledgement within the bound" if outcome == "unknown" and not ended else None
    return Reply(outcome, data=data, error=error, ended=ended)  # type: ignore[arg-type]


def _start(call_id: str, name: str = "read", args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "tool_execution_start",
        "tool_name": name,
        "tool_call_id": call_id,
        "args": args if args is not None else {"path": "a.py"},
    }


def _end(call_id: str, *, error: bool = False, result: Any = None) -> dict[str, Any]:
    return {
        "type": "tool_execution_end",
        "tool_call_id": call_id,
        "is_error": error,
        "result": result if result is not None else {"content": [{"type": "text", "text": "ok"}]},
    }


def _say(text: str) -> dict[str, Any]:
    return {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        },
    }


AGENT_START = {"type": "agent_start"}
AGENT_END = {"type": "agent_end"}


def _rig_runtime_class() -> type:
    from aelix_agents.child_approvals import withdraw_approval
    from aelix_agents.runtime import _SubagentRuntimeImpl

    class RigRuntime(_SubagentRuntimeImpl):  # type: ignore[misc, valid-type]
        """``_SubagentRuntimeImpl`` whose ``stop`` ends a row that has no process.

        The real door kills and reaps the child, and its run's ``finally``
        publishes the terminal frame; here the row is marked as the door marks
        it and published as that ``finally`` would (``end: stopped``).
        """

        async def stop(self, id: str) -> None:  # noqa: A002 — the Protocol's spelling
            child = self._children.pop(id, None)
            if child is None:
                return
            withdraw_approval(child)
            child.stopped = True
            child.state = "stopped"
            self._publish(child, child.stream, None, final=True)
            stopped = self.rig_stopped.get(id)
            if stopped is not None:
                stopped.set()

    return RigRuntime


class RigChildren:
    """One harness build's children: the hub, the runtime holding the rows, and the host controls."""

    def __init__(self) -> None:
        from aelix_agents.child_events import ChildEventHub
        from aelix_agents.host_controls import HostControls
        from aelix_agents.runtime import SubagentHost

        self.hub = ChildEventHub()
        self.runtime = _rig_runtime_class()(
            host=SubagentHost(cwd=lambda: "/rig"), channel=SimpleNamespace(), events=self.hub
        )
        self.runtime.rig_stopped = {}
        self.job_stops: list[str] = []
        self.controls = HostControls(self.hub, stop_job=self._stop_job)
        self.mark: Callable[[str], None] | None = None
        self.permissions: Any = None

    async def _stop_job(self, job_id: str) -> None:
        """The job service's stop, asked by the user: recorded; it returns before the job's legs."""

        self.job_stops.append(job_id)

    # === rows ===================================================================

    def row(
        self,
        child_id: str,
        *,
        reply: Any = None,
        process: bool = True,
        broken: bool = False,
        stopping: bool = False,
        job: bool = False,
        state: str = "running",
    ) -> Any:
        """A live row in the registry, published once (its first ``state`` frame)."""

        from aelix_agents.print_channel import RunningChild

        child = RunningChild(
            id=child_id,
            profile=PROFILE,
            parent_tool_call_id=PARENT_CALL,
            state=state,  # type: ignore[arg-type]
        )
        if process:
            child.proc = SimpleNamespace(pid=None, returncode=None)
            child.wire = ScriptedWire(reply or _reply("queued", phase="streaming"), broken=broken)
        child.stopped = stopping
        if job:
            child.owner = SimpleNamespace(job_id=JOB_ID)
        self.runtime._children[child.id] = child
        self.runtime._publish(child, child.stream, None)
        return child

    def feed(self, child: Any, *events: dict[str, Any]) -> None:
        """Each event reduced and then published, as the stdout pump does per line."""

        from aelix_agents.stream import reduce_event

        for event in events:
            reduce_event(child.stream, event)
            self.runtime._publish(child, child.stream, None)

    def finish(self, child: Any, *, summary: str = "done") -> None:
        """The run returned ``ok``: out of the registry, its terminal frame published."""

        from aelix_coding_agent.subagent_contract import SubagentResult

        self.runtime._children.pop(child.id, None)
        child.state = "done"
        child.envelope = SubagentResult(
            id=child.id, profile=child.profile, ok=True, status="ok", summary=summary
        )
        self.runtime._publish(child, child.stream, None, final=True)

    def populate(self) -> None:
        """The ``children`` setup: one row per state a fixture reads."""

        from aelix_agents.child_events import ENDED_KEPT

        running = self.row(RUNNING)
        self.feed(running, AGENT_START, _start("call-1"), _end("call-1"), _start("call-2", "grep"))
        self.feed(running, _say("looking at a.py"))
        self.row(UNKNOWN, reply=_reply("unknown"))
        self.row(ENDED_OPEN, reply=_reply("unknown", ended=True))
        self.row(NOT_SENT, reply=_reply("not_sent"))
        self.row(STARTING, process=False, state="starting")
        self.row(STOPPING, stopping=True)
        self.row(BROKEN, broken=True)
        self.row(JOB, job=True)
        asking = self.row(ASKING)
        self.feed(asking, AGENT_START, _start("call-7", "bash", {"command": "make"}))
        self.hub.approval(ASKING, "call-7", "bash", "asked")
        chatty = self.row(CHATTY)
        self.feed(chatty, *(_say(f"line {n}") for n in range(300)))
        tomb = self.row(TOMB)
        self.finish(tomb)
        for n in range(ENDED_KEPT):
            self.finish(self.row(f"sub-gone-{n}"))
        done = self.row(DONE)
        self.feed(done, AGENT_START, _start("call-9"), _end("call-9"), _say("all done"), AGENT_END)
        self.finish(done, summary="all done")

    # === agents/run on the fake ===================================================

    async def spawn(self, task: str) -> tuple[bool, str, str]:
        """``agents/run``'s child for a scripted task: ``(ok, status, summary)``."""

        if task == "hold":
            return await self._held()
        if task == "ask":
            return await self._ask()
        script = SCRIPTS[task[len("script:") :]]
        script(self)
        return (True, "ok", f"did: {task}")

    async def _held(self) -> tuple[bool, str, str]:
        """A child that runs until it is stopped (``children/stop`` ends the ``agents/run``)."""

        stopped = self.runtime.rig_stopped[HELD] = asyncio.Event()
        child = self.row(HELD)
        try:
            if self.mark is not None:
                self.mark("agents_run_entered")
            await stopped.wait()
            return (False, "aborted", "stopped")
        finally:
            if HELD in self.runtime._children:
                await self.runtime.stop(child.id)

    async def _ask(self) -> tuple[bool, str, str]:
        """A child's ``bash`` call that asks through this process's arbiter (ADR-0308)."""

        from aelix_coding_agent.subagent_contract import ChildApprovalRequest

        child = self.row("sub-asks")
        self.feed(child, AGENT_START, _start("call-ask", "bash", {"command": "make"}))
        request = ChildApprovalRequest(
            request_id="ap-1",
            child_id=child.id,
            profile=child.profile,
            tool_name="bash",
            kind="bash",
            args={"command": "make"},
            call_id="call-ask",
            parent_tool_call_id=PARENT_CALL,
            cwd="/rig",
            parent_cwd="/rig",
            epoch=self.hub.epoch,
        )
        decision, reason = await self.permissions.decide_for_child(request)
        ran = decision in ("yes", "yes_session")
        self.feed(child, _end("call-ask", error=not ran), AGENT_END)
        self.finish(child)
        return (True, "ok", "ran: make" if ran else f"refused: {decision} ({reason})")


# === the scripts an ``agents/run`` task names (``script:<name>``) ===============


def _states(children: RigChildren) -> None:
    child = children.row("sub-states", state="starting")
    child.state = "running"
    children.feed(child, AGENT_START)
    children.finish(child)


def _tools(children: RigChildren) -> None:
    child = children.row("sub-tools")
    children.feed(child, AGENT_START, _start("call-1"), _end("call-1"), _start("call-2"))
    children.feed(child, _end("call-2", error=True), AGENT_END)
    children.finish(child)


def _text(children: RigChildren) -> None:
    child = children.row("sub-text")
    children.feed(child, AGENT_START, _say("half way"))
    children.finish(child)


def _stuck(children: RigChildren) -> None:
    child = children.row("sub-stuck")
    children.feed(child, AGENT_START)
    for n in range(4):
        children.feed(child, _start(f"call-{n}", "read"), _end(f"call-{n}"))
    children.finish(child)


def _wrap_up(children: RigChildren, *, take_back: bool) -> None:
    from aelix_agents.command_wire import new_wrap_up
    from aelix_agents.runtime import _record_wrap_up

    child = children.row("sub-wrap-back" if take_back else "sub-wrap")
    _record_wrap_up(children.runtime, child, new_wrap_up("user"))
    if take_back:
        _record_wrap_up(children.runtime, child, None)
    children.finish(child)


def _approval(children: RigChildren) -> None:
    child = children.row("sub-approval")
    children.feed(child, AGENT_START, _start("call-a", "bash", {"command": "make"}))
    children.hub.approval(child.id, "call-a", "bash", "asked")
    children.hub.approval(child.id, "call-a", "bash", "allowed")
    children.feed(child, _end("call-a"), AGENT_END)
    children.finish(child)


def _receipt(children: RigChildren) -> None:
    child = children.row("sub-receipt")
    child.session_id = "child-session-1"
    children.runtime._publish_identity(child)
    children.finish(child)


def _five_open(children: RigChildren) -> None:
    child = children.row("sub-five")
    children.feed(child, AGENT_START, *(_start(f"call-{n}") for n in range(5)), AGENT_END)
    children.finish(child)


SCRIPTS: dict[str, Callable[[RigChildren], None]] = {
    "states": _states,
    "tools": _tools,
    "text": _text,
    "stuck": _stuck,
    "wrap_up": lambda children: _wrap_up(children, take_back=False),
    "wrap_up_taken_back": lambda children: _wrap_up(children, take_back=True),
    "approval": _approval,
    "receipt": _receipt,
    "five_open": _five_open,
}
