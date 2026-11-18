"""#123 (ADR-0306), the parent side: the command wire into a delegated child's stdin.

``build_child_argv(inbox=True)`` carries ``--command-inbox=1`` and ``PrintChannel``
passes it for every row; the child's stdin is a pipe the row's
:class:`~aelix_agents.command_wire.CommandWire` writes; the stdout pump hands the
child's ``response`` lines to the wire and everything else to the reducer; a
request still open when the run ends fails ``unknown``; the wire's two bounds are
its own; the reducer keeps the ``user`` texts a consumed steer makes, which is
how a wrap-up is judged consumed; and ``build_result`` turns a consumed wrap-up
into ``aborted`` with its reason and a never-delivered one into a note.

Fails on ``a1606f58``: ``build_child_argv`` takes no ``inbox``, ``RunningChild``
has no ``wire``, ``aelix_agents.command_wire`` does not exist, the child's stdin
is ``DEVNULL``, ``_StreamState`` has no ``user_texts`` or ``fingerprints`` and
``build_result`` takes no ``wrap_up``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import command_wire as cw
from aelix_agents.command_wire import (
    ENDED_UNANSWERED,
    INBOX_BROKEN,
    NOT_STARTED,
    CommandWire,
    Reply,
    WrapUp,
    new_wrap_up,
    wrap_up_consumed,
)
from aelix_agents.envelope import REASON_STOPPED, WRAP_UP_REASON, build_result
from aelix_agents.print_channel import PrintChannel, RunningChild, build_child_argv
from aelix_agents.stream import Fingerprint, _StreamState, reduce_event, reduce_line, stuck_of
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.cli.args import COMMAND_INBOX_FLAG, COMMAND_INBOX_VERSION, parse_args

from tests.agents_ext.test_print_channel_spawn import _plan, _profile, _stub, _stub_channel, within
from tests.jobs.conftest import wait_for

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the stub children's pipes are POSIX here")


# === the argv and the pipe ==========================================================


def test_build_child_argv_carries_the_versioned_flag_only_when_asked(tmp_path: Path) -> None:
    kwargs: dict[str, Any] = dict(
        prompt_path=str(tmp_path / "p.md"),
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
    )
    flag = f"{COMMAND_INBOX_FLAG}={COMMAND_INBOX_VERSION}"
    argv = build_child_argv(_profile(), inbox=True, **kwargs)
    assert argv[-1] == flag, "after --no-agents, the last token"
    parsed = parse_args(argv[4:])
    assert parsed.command_inbox == COMMAND_INBOX_VERSION and parsed.diagnostics == []
    assert flag not in build_child_argv(_profile(), **kwargs), "a plain build is what it was"


@posix_only
async def test_the_channel_asks_for_the_inbox_on_every_row_and_gives_it_a_wire(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    script = _stub("start()\nsay('x')\ndone()\n")

    def build(*_a: Any, **kw: Any) -> list[str]:
        seen.append(kw)
        return [sys.executable, "-c", script]

    row = RunningChild(id="sub-wire", profile="scout")
    result = await within(PrintChannel(grace=2.0, argv_builder=build).run(_plan(tmp_path), child=row), bound=30.0, what="the run")
    assert result.ok is True
    assert seen[0]["inbox"] is True
    assert isinstance(row.wire, CommandWire)
    assert row.wire.closed, "closed at the end of the run"
    assert (await row.wire.request("steer", {"message": "late"})).outcome == "refused"


# === the wire's bounds and outcomes ===============================================


class _Writer:
    """A stand-in for the child's stdin ``StreamWriter``."""

    def __init__(self, *, closing: bool = False, drain_hangs: bool = False, drain_raises: Exception | None = None):
        self.lines: list[str] = []
        self.closed = False
        self.transport = type("T", (), {"is_closing": lambda _s: closing})()
        self._hangs = drain_hangs
        self._raises = drain_raises

    def write(self, data: bytes) -> None:
        self.lines.append(data.decode())

    async def drain(self) -> None:
        if self._hangs:
            await asyncio.sleep(3600)
        if self._raises is not None:
            raise self._raises

    def close(self) -> None:
        self.closed = True


def _response(request_id: str, *, success: bool = True, data: Any = None, error: str = "no") -> str:
    body: dict[str, Any] = {"type": "response", "command": "steer", "id": request_id, "success": success}
    body["data" if success else "error"] = (data if data is not None else {"phase": "idle"}) if success else error
    return json.dumps(body)


async def test_ids_are_numbered_per_row_and_a_response_resolves_its_request() -> None:
    wire = CommandWire("sub-1")
    writer = _Writer()
    wire.attach(writer)
    first = asyncio.ensure_future(wire.request("steer", {"message": "a"}))
    second = asyncio.ensure_future(wire.request("follow_up", {"message": "b"}))
    await asyncio.sleep(0)
    assert [json.loads(line)["id"] for line in writer.lines] == ["sub-1-1", "sub-1-2"]
    assert json.loads(writer.lines[0]) == {"type": "steer", "id": "sub-1-1", "message": "a"}
    assert wire.pending == 2
    assert wire.take(_response("sub-1-2", data={"phase": "streaming"})) is True
    assert wire.take(_response("sub-1-1", success=False, error="disposed")) is True
    assert await second == Reply("queued", data={"phase": "streaming"})
    assert (await second).phase == "streaming"
    assert await first == Reply("refused", error="disposed")
    assert wire.pending == 0


def test_take_reads_only_response_lines() -> None:
    wire = CommandWire("sub-1")
    assert wire.take('{"type": "message_end", "message": {}}') is False
    assert wire.take('{"type": "agent_end"}') is False
    assert wire.take("not json") is False
    assert wire.take('{"type": "response", "id": "nobody", "success": true}') is True, "taken, not a stream event"
    assert wire.take('{"type": "response"') is False, "a cut line is left to the reducer, which skips it"
    assert wire.take('{"id": "x", "type": "response", "success": true}') is False, "only the child's own spelling, type first"


async def test_no_answer_within_the_bound_is_unknown_and_a_later_answer_is_dropped() -> None:
    wire = CommandWire("sub-1")
    writer = _Writer()
    wire.attach(writer)
    reply = await wire.request("steer", {"message": "a"}, timeout=0.05)
    assert reply.outcome == "unknown" and "did not answer within 0.05 s" in (reply.error or "")
    assert wire.pending == 0 and wire.broken is False, "a slow answer does not break the wire"
    assert wire.take(_response("sub-1-1")) is True


async def test_a_write_that_cannot_drain_in_time_is_unknown_and_breaks_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cw, "WRITE_SECONDS", 0.05)
    wire = CommandWire("sub-1")
    wire.attach(_Writer(drain_hangs=True))
    reply = await wire.request("steer", {"message": "a"})
    assert reply.outcome == "unknown" and "did not take the line" in (reply.error or "")
    assert wire.broken is True
    assert await wire.request("steer", {"message": "b"}) == Reply("refused", error=INBOX_BROKEN)


async def test_a_failed_write_after_the_first_byte_may_have_left_is_unknown() -> None:
    wire = CommandWire("sub-1")
    wire.attach(_Writer(drain_raises=BrokenPipeError()))
    reply = await wire.request("steer", {"message": "a"})
    assert reply.outcome == "unknown" and "BrokenPipeError" in (reply.error or "")
    assert wire.broken is True


async def test_not_sent_only_before_any_byte_could_leave() -> None:
    wire = CommandWire("sub-1")
    assert await wire.request("steer", {"message": "a"}) == Reply("not_sent", error=NOT_STARTED)
    wire.attach(_Writer(closing=True))
    reply = await wire.request("steer", {"message": "a"})
    assert reply.outcome == "not_sent" and wire.broken is False and wire.pending == 0


async def test_close_fails_every_open_request_unknown_and_closes_the_writer() -> None:
    wire = CommandWire("sub-1")
    writer = _Writer()
    wire.attach(writer)
    waiting = asyncio.ensure_future(wire.request("steer", {"message": "a"}))
    await asyncio.sleep(0)
    wire.close()
    assert await waiting == Reply("unknown", error=ENDED_UNANSWERED, ended=True)
    assert writer.closed and wire.closed
    assert await wire.request("steer", {"message": "b"}) == Reply("refused", error=ENDED_UNANSWERED, ended=True)
    wire.close()  # idempotent


# === a stub child on the wire =======================================================

_ANSWERING = _stub(
    """
    start()
    emit({"type": "tool_execution_start", "tool_name": "read", "tool_call_id": "c1", "args": {"path": "a"}})
    line = sys.stdin.readline()
    req = json.loads(line)
    emit({"type": "response", "command": req["type"], "id": req["id"], "success": True,
          "data": {"phase": "streaming", "echo": req.get("message")}})
    emit({"type": "tool_execution_end", "tool_call_id": "c1", "result": {"content": [{"type": "text", "text": "r"}]}})
    emit({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": req.get("message")}]}})
    say("the answer")
    done()
    """
)

_SILENT = _stub(
    """
    start()
    time.sleep(0.3)
    say("ended without answering")
    done()
    """
)


@posix_only
async def test_a_response_line_reaches_the_wire_not_the_reducer_and_a_consumed_text_is_kept(
    tmp_path: Path,
) -> None:
    row = RunningChild(id="sub-ans", profile="scout")
    taps: list[int] = []
    run = asyncio.ensure_future(
        _stub_channel(_ANSWERING).run(_plan(tmp_path), child=row, on_stream=lambda _s: taps.append(1))
    )
    await wait_for(lambda: row.wire is not None and row.wire.attached)
    reply = await within(row.wire.request("steer", {"message": "NOTE"}), bound=10.0, what="the reply")
    assert reply == Reply("queued", data={"phase": "streaming", "echo": "NOTE"})
    result = await within(run, bound=30.0, what="the run")
    assert result.ok is True and result.summary == "the answer"
    assert "response" not in (result.tool_trail or ""), "the reducer never saw the response line"
    # The stub writes 8 stream lines and ONE response: the progress tap fires per
    # stream line only (the response went to the wire, not through the reducer).
    assert len(taps) == 8, taps
    assert list(row.stream.user_texts) == ["NOTE"]
    # A text the child consumed with no wrap-up asked is no wrap-up's reading,
    # whatever a later request's words are (fix round 1, F4).
    assert wrap_up_consumed(WrapUp("user", "NOTE", 0.0), row.stream) is False
    assert wrap_up_consumed(WrapUp("user", "OTHER", 0.0), row.stream) is False


@posix_only
async def test_a_request_still_open_when_the_child_ends_is_unknown_never_not_delivered(
    tmp_path: Path,
) -> None:
    row = RunningChild(id="sub-silent", profile="scout")
    run = asyncio.ensure_future(_stub_channel(_SILENT).run(_plan(tmp_path), child=row))
    await wait_for(lambda: row.wire is not None and row.wire.attached)
    pending = asyncio.ensure_future(row.wire.request("steer", {"message": "NOTE"}))
    result = await within(run, bound=30.0, what="the run")
    assert result.ok is True
    reply = await within(pending, bound=10.0, what="the close-out reply")
    assert reply == Reply("unknown", error=ENDED_UNANSWERED, ended=True)
    assert "not delivered" not in (reply.error or "")
    assert row.wire.closed


# === consumed texts, fingerprints, stuck ============================================


def test_the_reducer_keeps_user_texts_and_fingerprints_apart_from_activity() -> None:
    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    reduce_line(state, json.dumps({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "x" * 400}]}}))
    assert list(state.user_texts) == ["x" * 200], "the head, bounded"
    long_a = {"path": "p", "query": "a" * 500}
    long_b = {"path": "p", "query": "a" * 499 + "b"}
    for n, (args, text, is_error) in enumerate(
        [(long_a, "same", False), (long_b, "same", False), (long_a, "same", True), (long_a, "other", False)], start=1
    ):
        reduce_event(state, {"type": "tool_execution_start", "tool_name": "grep", "tool_call_id": f"c{n}", "args": args})
        reduce_event(
            state,
            {"type": "tool_execution_end", "tool_call_id": f"c{n}", "is_error": is_error,
             "result": {"content": [{"type": "text", "text": text}]}},
        )
    prints = list(state.fingerprints)
    assert [p.name for p in prints] == ["grep"] * 4
    assert prints[0].args != prints[1].args, "arguments that differ past the display clip differ here"
    assert prints[0].args == prints[2].args and prints[0].result == prints[1].result
    assert prints[2].is_error is True and prints[3].result != prints[0].result
    assert len({a.args for a in state.recent}) == 1, "the display window collapses them (Codex M12)"
    assert state.turns == 0, "a user message is not a turn"


def _fp(**kw: Any) -> Fingerprint:
    base = dict(name="read", args="a", result="r", is_error=False)
    base.update(kw)
    return Fingerprint(**base)


def test_stuck_of_repeat_needs_four_identical_and_not_four_with_different_results() -> None:
    assert stuck_of([_fp()] * 4) == "repeat"
    assert stuck_of([_fp(result="x"), _fp(), _fp(), _fp(), _fp()]) == "repeat", "the last four"
    assert stuck_of([_fp(result=str(n)) for n in range(4)]) is None
    assert stuck_of([_fp()] * 3) is None
    assert stuck_of([_fp(), _fp(), _fp(), _fp(args="b")]) is None
    assert stuck_of([]) is None


def test_stuck_of_error_loop_needs_three_identical_errors() -> None:
    assert stuck_of([_fp(is_error=True)] * 3) == "error loop"
    assert stuck_of([_fp(), _fp(is_error=True), _fp(is_error=True), _fp(is_error=True)]) == "error loop"
    assert stuck_of([_fp(is_error=True), _fp(is_error=True), _fp(is_error=True, result="other")]) is None
    assert stuck_of([_fp(is_error=True)] * 4) == "repeat", "four identical errors are the repeat pattern first"


# === the wrap-up in the envelope ====================================================


def _user(text: str) -> dict[str, Any]:
    return {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _asked(state: _StreamState, origin: str = "user") -> WrapUp:
    """Record a wrap-up on ``state`` as ``runtime.wrap_up`` does on a row's, before it writes the line."""

    request = new_wrap_up(origin)
    state.wrap_up_head = request.head
    return request


def _finished(summary: str, state: _StreamState | None = None) -> _StreamState:
    state = _StreamState() if state is None else state
    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, _user(cw.WRAP_UP_TEXT))
    reduce_event(state, {"type": "message_end", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": summary}]}})
    reduce_event(state, {"type": "agent_end"})
    return state


def test_a_consumed_wrap_up_is_aborted_with_its_reason_and_the_childs_text_even_all_done() -> None:
    state = _StreamState()
    wrap_up = _asked(state)
    _finished("all done", state)
    assert wrap_up_consumed(wrap_up, state) is True
    result = build_result(id="p", profile="p", state=state, outcome="ok", exit_code=0, wrap_up=wrap_up, wrap_up_consumed=True)
    assert result.ok is False and result.status == "aborted"
    assert result.reason == WRAP_UP_REASON.format(origin="user") == "wrapped up at the user's request"
    assert result.summary == "all done" and result.note is None
    model_asked = build_result(
        id="p", profile="p", state=state, outcome="ok", exit_code=0, wrap_up=new_wrap_up("model"), wrap_up_consumed=True
    )
    assert model_asked.reason == "wrapped up at the model's request"


def test_the_wrap_up_text_read_before_the_request_is_not_the_wrap_ups_reading() -> None:
    """F4, the first wrong answer: a task or a steer with the wrap-up's words, then the request.

    At ``8d7ee999`` consumption was ``head in user_texts``, so the earlier
    message made the wrap-up "consumed" and the run ``aborted`` though the
    child's model never read a final steer.
    """

    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, _user(cw.WRAP_UP_TEXT))  # the task itself, or a steer, in these words
    wrap_up = _asked(state)
    assert wrap_up_consumed(wrap_up, state) is False, "nothing before the request counts"
    reduce_event(state, {"type": "message_end", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "done on its own"}]}})
    reduce_event(state, {"type": "agent_end"})
    result = build_result(
        id="p", profile="p", state=state, outcome="ok", exit_code=0, wrap_up=wrap_up, wrap_up_consumed=wrap_up_consumed(wrap_up, state)
    )
    assert result.status == "ok" and result.note == "a wrap-up was requested by the user but never reached the model"
    reduce_event(state, _user(cw.WRAP_UP_TEXT))  # the final steer itself, drained after the request
    assert wrap_up_consumed(wrap_up, state) is True


async def test_eight_notes_drained_with_the_final_do_not_evict_its_reading() -> None:
    """F4, the second wrong answer, through the kernel: ``steeringMode="all"`` drains nine messages at once.

    The final and eight notes are queued, one prompt runs: the kernel's latch
    fires (the one model call has no tools) and the child's stream carries nine
    ``user`` messages after the prompt's. At ``8d7ee999`` the eight notes
    pushed the final's head out of the window of eight and the run was reported
    ``ok`` with "never reached the model".
    """

    from dataclasses import asdict

    from aelix_agent_core import AgentTool
    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.messages import AssistantMessage, TextContent
    from aelix_ai.streaming import AssistantEndEvent
    from aelix_ai.tools import ToolResult

    tools_seen: list[int] = []

    async def model(_model: Any, context: Any, _options: Any) -> Any:
        tools_seen.append(len(context.tools))
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="all done")]))

    async def tool(_args: Any, _ctx: Any) -> ToolResult:
        return ToolResult(content=[])

    harness = AgentHarness(
        AgentHarnessOptions(stream_fn=model, tools=[AgentTool(name="t", execute=tool)], steering_mode="all")
    )
    harness.state.auto_compaction_enabled = False
    state = _StreamState()
    harness.subscribe(lambda event: reduce_event(state, asdict(event)))
    wrap_up = _asked(state)
    try:
        await harness.steer(wrap_up.text, final=True)
        for n in range(8):
            await harness.steer(f"note {n}")
        await harness.prompt("go")
    finally:
        await harness.dispose()
    assert tools_seen == [0], "the kernel read the final: its one call had no tools"
    assert wrap_up.head not in state.user_texts, "the premise: the window of eight no longer holds it"
    assert wrap_up_consumed(wrap_up, state) is True
    result = build_result(
        id="p", profile="p", state=state, outcome="ok", exit_code=0, wrap_up=wrap_up, wrap_up_consumed=wrap_up_consumed(wrap_up, state)
    )
    assert result.status == "aborted" and result.reason == "wrapped up at the user's request" and result.note is None


def test_a_wrap_up_never_consumed_keeps_the_runs_own_verdict_and_says_so() -> None:
    wrap_up = new_wrap_up("user")
    finished = build_result(id="p", profile="p", state=_finished("done on its own"), outcome="ok", exit_code=0, wrap_up=wrap_up)
    assert finished.ok is True and finished.status == "ok"
    assert finished.note == "a wrap-up was requested by the user but never reached the model"
    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    killed = build_result(id="p", profile="p", state=state, outcome="aborted", exit_code=-9, wrap_up=wrap_up)
    assert killed.status == "aborted" and killed.reason == REASON_STOPPED, "a kill keeps its own reason"
    assert killed.note is not None and "never reached" in killed.note


def test_a_consumed_wrap_up_on_a_killed_run_keeps_the_kill() -> None:
    state = _finished("partial")
    result = build_result(id="p", profile="p", state=state, outcome="aborted", exit_code=-9, wrap_up=new_wrap_up("user"), wrap_up_consumed=True)
    assert result.status == "aborted" and result.reason == REASON_STOPPED and result.note is None


def test_a_consumed_wrap_up_keeps_the_reason_its_caller_gave() -> None:
    """Fix round 1 V6 (mutant EV3): ``build_result``'s contract, at its own boundary.

    A run proposed ``ok`` that carries a caller's reason and a consumed wrap-up
    is ``aborted`` with THAT reason, not the wrap-up's. No caller in the product
    sends this shape at this head: ``PrintChannel.run`` passes ``row.abort_reason``,
    which ``tighten_child`` sets in the same step as ``abort_child``'s ``stopped``,
    so a floor kill arrives as ``aborted`` and returns before this line (the test
    above). A guard on ``8d7ee999``.
    """

    state = _StreamState()
    wrap_up = _asked(state)
    _finished("all done", state)
    result = build_result(
        id="p",
        profile="p",
        state=state,
        outcome="ok",
        exit_code=0,
        reason="permission floor could not be applied",
        wrap_up=wrap_up,
        wrap_up_consumed=wrap_up_consumed(wrap_up, state),
    )
    assert (result.ok, result.status, result.reason) == (False, "aborted", "permission floor could not be applied")
    assert (result.summary, result.note) == ("all done", None)
    plain = build_result(
        id="p", profile="p", state=state, outcome="ok", exit_code=0, wrap_up=wrap_up, wrap_up_consumed=True
    )
    assert plain.reason == "wrapped up at the user's request", "no reason given: the wrap-up's own"


def test_the_wrap_ups_text_is_true_for_either_origin() -> None:
    """Fix round 1 VN2: one text, whoever asked.

    On ``8d7ee999`` a wrap-up the parent MODEL sent told the child "The user
    has asked you to stop".
    """

    assert [cw.new_wrap_up(origin).text for origin in ("user", "model")] == [cw.WRAP_UP_TEXT] * 2
    assert cw.WRAP_UP_TEXT == (
        "Wrap up now. You have been asked to stop here. Do not call any more tools. In one or two "
        "sentences, say what you did and what is left undone, then end your reply."
    )


def test_no_wrap_up_changes_nothing() -> None:
    state = _finished("fine")
    result = build_result(id="p", profile="p", state=state, outcome="ok", exit_code=0)
    assert result.ok is True and result.note is None and result.reason is None
