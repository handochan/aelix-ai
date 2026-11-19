"""#123 (ADR-0306 §3, §4, §6): the runtime doors, the kind's control adapter, the service's control.

``runtime.steer`` / ``wrap_up`` / ``tighten_all`` write to a live row's inbox and
answer per state; ``JobService.control`` routes a verb to the kind's adapter and
refuses a kind that declares none; ``DelegationKindAdapter.control`` resolves a
spawned job's row and a Ctrl+B'd one; a wrap-up ends ``aborted`` with its reason
and the child's text; ``tighten_all`` tightens a looser row, leaves a tighter one,
and fails closed — a refusal, a wrong echo or no answer kills the child with the
floor reason.

Fails on ``a1606f58``: none of ``steer``, ``wrap_up``, ``tighten_all``,
``JobService.control``, ``KindCapabilities.controls`` or the adapter's ``control``
exists.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import command_wire as cw
from aelix_agents.command_wire import CommandWire
from aelix_agents.jobs_client import describe_delivery
from aelix_agents.print_channel import PrintChannel, RunningChild
from aelix_agents.runtime import (
    FLOOR_REASON,
    RESUME_HINT,
    Delivery,
    SpawnRecordMeta,
    SubagentHost,
    _door_refusal,
    _SubagentRuntimeImpl,
)
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.jobs.contract import KindCapabilities
from aelix_coding_agent.jobs.service import JobNotFound

from tests.agents_ext._detach_rig import reap_children  # noqa: F401 — the fixture
from tests.agents_ext.test_agent_background_611 import _rig, jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_child_session_records import _grant, _parent, _resolved
from tests.agents_ext.test_print_channel_spawn import _stub
from tests.event_waits import wait_until
from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stub children's pipes are POSIX here")

_INBOX_STUB = _stub(
    """
    def applied(word):
        path = os.environ.get("STUB_APPLIED")
        if path:
            with open(path, "a") as f:
                f.write(word + "\\n")

    applied("launch " + (sys.argv[1] if len(sys.argv) > 1 else "?"))
    start()
    emit({"type": "tool_execution_start", "tool_name": "read", "tool_call_id": "c1", "args": {}})
    say("halfway")
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        req = json.loads(line)
        kind = req["type"]
        if kind == "set_permission_mode":
            how = os.environ.get("STUB_TIGHTEN", "echo")
            if how == "refuse":
                emit({"type": "response", "command": kind, "id": req["id"], "success": False, "error": "nope"})
            elif how == "wrong":
                emit({"type": "response", "command": kind, "id": req["id"], "success": True, "data": {"mode": "yolo"}})
            elif how != "silent":
                applied(req["mode"])
                emit({"type": "response", "command": kind, "id": req["id"], "success": True, "data": {"mode": req["mode"]}})
            continue
        emit({"type": "response", "command": kind, "id": req["id"], "success": True,
              "data": {"phase": "streaming", "kind": kind}})
        crowd = os.environ.get("STUB_FINAL") if req.get("final") else None
        if crowd != "unread":  # "unread": it ends on its own with the final still queued
            emit({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": req["message"]}]}})
        for n in range(8 if crowd == "crowd" else 0):  # steeringMode="all": eight notes drained with it
            emit({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "note %d" % n}]}})
        if req.get("final") or req["message"] == "FINISH":
            break
    emit({"type": "tool_execution_end", "tool_call_id": "c1", "result": {"content": [{"type": "text", "text": "r"}]}})
    if os.environ.get("STUB_END") == "error":  # its model's call failed: the run ends on the error
        emit({"type": "message_end", "message": {
            "role": "assistant", "content": [], "stop_reason": "error", "error_message": "boom",
            "provider": "stub", "model": "stub-1"}})
        done()
        sys.exit(1)
    say(os.environ.get("STUB_ANSWER", "all done"))
    done()
    if os.environ.get("AELIX_JOBS_PROTOCOL") == "1":
        emit({"type": "aelix_print_done"})
    """
)


_DEAF_STUB = _stub(
    """
    start()
    say("halfway")
    time.sleep(120)
    """
)
"""A child that never reads its stdin: a line larger than the pipe is not taken."""

_ENDING_STUB = _stub(
    """
    start()
    say("halfway")
    sys.stdin.readline()
    say("all done")
    done()
    """
)
"""A child that ends with a request open: it takes one line off its stdin and
exits without answering it, as a print child does for a line written after its
prompt returned (its pump is ended before its dispose; fix round 1 F3)."""


def _channel(*, script: str = _INBOX_STUB, kind: type[PrintChannel] = PrintChannel, **env: str) -> PrintChannel:
    """A channel on the stub. The stub's ``argv[1]`` is the posture the launch gave it."""

    base = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME", "TMPDIR", "SYSTEMROOT")}
    return kind(
        grace=1.0,
        argv_builder=lambda *_a, **k: [sys.executable, "-c", script, k["permission_mode"].value],
        env_builder=lambda *_a, **_k: {**base, **env},
    )


def _applied(path: Path) -> list[str]:
    """What the stub child wrote down: ``launch <mode>``, then each mode it applied and echoed."""

    return path.read_text(encoding="utf-8").split("\n")[:-1] if path.exists() else []


async def _spawn(tmp_path: Path, channel: PrintChannel, mode: PermissionMode) -> tuple[Any, Any]:
    session, _repo, _path = await _parent(tmp_path)
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path / "p"), session=lambda: session),
        channel=channel,
    )
    task = asyncio.ensure_future(
        runtime.spawn_granted(_grant(mode), _resolved(), "go", record=SpawnRecordMeta(tool_call_id="tc-1", mode="single", index=0))
    )
    return runtime, task


async def _halfway(runtime: Any, task: Any) -> RunningChild:
    await wait_until(
        lambda: any(r.stream.last_text == "halfway" for r in runtime._children.values()) or task.done(),
        what="the child to be live on its inbox",
    )
    assert not task.done(), task.result()
    (row,) = runtime._children.values()
    return row


async def _live(
    tmp_path: Path, *, mode: PermissionMode = PermissionMode.YOLO, script: str = _INBOX_STUB, **env: str
) -> tuple[Any, Any, RunningChild]:
    runtime, task = await _spawn(tmp_path, _channel(script=script, kind=PrintChannel, **env), mode)
    return runtime, task, await _halfway(runtime, task)


# === the doors, per state ===========================================================


async def test_a_steer_is_queued_and_consumed_and_a_follow_up_goes_as_one(tmp_path: Path) -> None:
    runtime, task, row = await _live(tmp_path)
    try:
        delivery = await runtime.steer(row.id, "NOTE", origin="model")
        assert delivery == Delivery("queued", phase="streaming", reason=None, origin="model")
        later = await runtime.steer(row.id, "LATER", mode="follow_up")
        assert later.outcome == "queued"
        await wait_for(lambda: list(row.stream.user_texts) == ["NOTE", "LATER"])
        assert row.granted_mode is PermissionMode.YOLO
        with pytest.raises(ValueError):
            await runtime.steer(row.id, "x", mode="prompt")
    finally:
        await runtime.steer(row.id, "FINISH")
        result = await asyncio.wait_for(task, 30)
    assert result.ok is True and result.summary == "all done"
    assert runtime._children == {}
    refused = await runtime.steer(row.id, "too late")
    assert refused == Delivery("refused", reason=RESUME_HINT)
    # #822: a wrap-up carries no text, so its refusal names none.
    assert (await runtime.wrap_up(row.id)).reason == (
        'the agent has finished or was stopped; to go on, use action="resume"'
    )


class _Stdin:
    """A stand-in for a child's stdin ``StreamWriter`` that takes every line."""

    transport = None

    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


async def _ended_replies() -> tuple[cw.Reply, cw.Reply]:
    """The wire's two answers at a child's end: for a request open then, and for one made after."""

    wire = CommandWire("r")
    wire.attach(_Stdin())
    waiting = asyncio.ensure_future(wire.request("steer", {"message": "a"}))
    await asyncio.sleep(0)
    wire.close()
    return await waiting, await wire.request("steer", {"message": "b"})


@pytest.mark.parametrize(
    ("door", "tail"),
    [
        ("steer", 'to go on, use action="resume" with this text as its next instruction'),
        ("wrap_up", 'to go on, use action="resume"'),
    ],
)
async def test_a_request_open_when_the_child_ends_is_unknown_and_names_resume(
    tmp_path: Path, door: str, tail: str
) -> None:
    """F3: the child's end answers the request, and the reason says how to go on.

    Fails on ``8d7ee999``: the reason was ``the agent ended before answering``
    and nothing else.
    """

    runtime, task, row = await _live(tmp_path, script=_ENDING_STUB)
    sent = runtime.steer(row.id, "NOTE", origin="model") if door == "steer" else runtime.wrap_up(row.id, origin="model")
    delivery = await asyncio.wait_for(sent, 30)
    result = await asyncio.wait_for(task, 30)
    assert delivery.outcome == "unknown", "a line read and not acknowledged before the exit is possible"
    assert delivery.reason == f"{cw.ENDED_UNANSWERED}; {tail}"
    assert delivery.ended is True
    # The envelope judges the wrap-up the child never answered: its model did not read it.
    wrap_note = "a wrap-up was requested by the model but never reached the model"
    assert (result.ok, result.note) == (True, wrap_note if door == "wrap_up" else None)
    after = await runtime.steer(row.id, "again")
    assert after == Delivery("refused", reason=RESUME_HINT), "the row is gone: the same way on"


async def test_the_model_is_told_to_resume_an_agent_that_ended_with_its_note_open(tmp_path: Path) -> None:
    """``extension._send_text`` on the door's own ``Delivery`` (F3).

    Fails on ``8d7ee999``: the note read "may or may not have reached the agent:
    the agent ended before answering. Do not resend it; check with
    action="status", or wait for its notice."
    """

    from aelix_agents.extension import _send_text

    runtime, task, row = await _live(tmp_path, script=_ENDING_STUB)
    delivery = await asyncio.wait_for(runtime.steer(row.id, "NOTE", origin="model"), 30)
    await asyncio.wait_for(task, 30)
    text, failed = _send_text(delivery, agent_id="ab12cd", verb="steer")
    body = json.loads(text)
    assert failed is False and body["outcome"] == "unknown" and body["delivered_to"] == "ab12cd"
    assert body["note"] == (
        "the agent ended before it answered, so it most likely never read the note. "
        'To continue the agent, use action="resume" and put the note in its task.'
    )
    wrap, _ = _send_text(delivery, agent_id="ab12cd", verb="wrap_up")
    assert json.loads(wrap)["note"] == (
        "the agent ended before it answered, so it most likely never read the wrap-up. "
        'To continue the agent, use action="resume".'
    )


def test_the_models_words_for_an_agent_that_is_alive_and_silent_keep_do_not_resend() -> None:
    """Guard: the other ``unknown`` — no answer within the bound — reads as before."""

    from aelix_agents.extension import _send_text

    silent = Delivery("unknown", reason="the agent did not answer within 30 s")
    note = json.loads(_send_text(silent, agent_id="ab12cd", verb="steer")[0])["note"]
    assert note == (
        "the note may or may not have reached the agent: the agent did not answer within 30 s. "
        'Do not resend it; check with action="status", or wait for its notice.'
    )


_QUEUED_NOTES = {
    "steer": "queued; the agent reads it after the tool calls it is already running, never in the middle of one",
    "follow_up": "queued; the agent reads it after its current answer, as a new turn",
    "wrap_up": (
        "queued; the agent gets one more reply with no tools, then its result comes "
        "back as a notice, stopped, with what it said"
    ),
}
_BETWEEN_TURNS = ". It is between turns now, so it reads the line at its next turn, if one comes"


@pytest.mark.parametrize("verb", ["steer", "follow_up", "wrap_up"])
def test_the_models_words_for_a_queued_line_say_when_the_agent_is_between_turns(verb: str) -> None:
    """Fix round 1 V6 (mutant EX5): the whole tool result of a ``queued`` line, in each phase.

    A guard on ``8d7ee999``; it fails when the idle sentence is never added.
    """

    from aelix_agents.extension import _send_text

    idle = _send_text(Delivery("queued", phase="idle", origin="model"), agent_id="ab12cd", verb=verb)
    assert (json.loads(idle[0]), idle[1]) == (
        {"delivered_to": "ab12cd", "outcome": "queued", "phase": "idle", "note": _QUEUED_NOTES[verb] + _BETWEEN_TURNS},
        False,
    )
    streaming = _send_text(Delivery("queued", phase="streaming", origin="model"), agent_id="ab12cd", verb=verb)
    assert (json.loads(streaming[0]), streaming[1]) == (
        {"delivered_to": "ab12cd", "outcome": "queued", "phase": "streaming", "note": _QUEUED_NOTES[verb]},
        False,
    )


async def test_the_models_words_for_a_line_that_was_not_sent_differ_from_a_refusals() -> None:
    """Fix round 1 V6 (mutant EX6): the whole tool result of the wire's own ``not_sent``, and of a refusal.

    A guard on ``8d7ee999``; it fails when a line that was never written reads "refused".
    """

    from aelix_agents.extension import _send_text

    reply = await CommandWire("r").request("steer", {"message": "a"})  # no stdin attached: nothing left
    not_sent = Delivery.of(reply, origin="model")
    assert (not_sent.outcome, not_sent.reason) == ("not_sent", "the agent has not started yet; retry")
    assert _send_text(not_sent, agent_id="ab12cd", verb="steer") == (
        "The note was not sent: the agent has not started yet; retry.",
        True,
    )
    assert _send_text(not_sent, agent_id="ab12cd", verb="wrap_up") == (
        "The wrap-up was not sent: the agent has not started yet; retry.",
        True,
    )
    refused = Delivery("refused", reason="the agent is stopping")
    assert _send_text(refused, agent_id="ab12cd", verb="follow_up") == (
        "The note was refused: the agent is stopping.",
        True,
    )


async def test_the_humans_sentence_for_an_ended_agent_names_resume_too() -> None:
    """``describe_delivery`` — ``/jobs steer``, ``/jobs wrap`` and the panel's note (F3).

    Fails on ``8d7ee999``: "Unknown whether the wrap-up arrived: the agent ended
    before answering." and "Not taken: the agent ended before answering."
    """

    open_at_the_end, made_after = await _ended_replies()
    note = describe_delivery(Delivery.of(open_at_the_end, origin="user"), what="the note")
    late = Delivery.of(made_after, origin="user")
    assert (note, describe_delivery(late, what="the note")) == (
        "Unknown whether the note arrived: the agent ended before answering; to go on, use "
        'action="resume" with this text as its next instruction.',
        f"Not taken: {RESUME_HINT}.",
    )
    assert late.outcome == "refused", "nothing was written: a refusal, the same way on"
    wrap = describe_delivery(Delivery.of(open_at_the_end, origin="user", verb="wrap_up"), what="the wrap-up")
    assert wrap == 'Unknown whether the wrap-up arrived: the agent ended before answering; to go on, use action="resume".'


def test_door_refusals_name_the_state() -> None:
    assert _door_refusal(None) == RESUME_HINT
    row = RunningChild(id="r", profile="p")
    assert "not started" in str(_door_refusal(row))
    row.wire = CommandWire("r")
    row.proc = object()
    assert "not started" in str(_door_refusal(row)), "a wire with no stdin yet"
    row.wire.attach(object())
    assert _door_refusal(row) is None
    row.wire.broken = True
    assert "stop or resume" in str(_door_refusal(row))
    row.wire.broken = False
    row.stopped = True
    assert _door_refusal(row) == "the agent is stopping"
    row.stopped, row.eager_kill = False, True  # V6 (mutant RT6): the kill began, the mark has not landed
    assert _door_refusal(row) == "the agent is stopping"


async def test_a_row_whose_kill_began_refuses_both_doors_before_it_is_marked_stopped(tmp_path: Path) -> None:
    """Fix round 1 V6 (mutant RT6): ``eager_kill`` alone is a stopping row (ADR-0306 D4's state table).

    ``PrintChannel._eager_abort`` sets it with no ``stopped`` (a cancelled run's
    kill). A guard on ``8d7ee999``; under the mutant the note is written to the
    child and the wrap-up is recorded on a row that is being killed.
    """

    runtime, task, row = await _live(tmp_path)
    try:
        row.eager_kill = True
        assert row.stopped is False
        steered = await asyncio.wait_for(runtime.steer(row.id, "NOTE", origin="model"), 30)
        wrapped = await asyncio.wait_for(runtime.wrap_up(row.id, origin="model"), 30)
        assert (steered.outcome, steered.reason) == ("refused", "the agent is stopping")
        assert (wrapped.outcome, wrapped.reason) == ("refused", "the agent is stopping")
        assert row.wrap_up is None, "nothing recorded"
        assert list(row.stream.user_texts) == [] and row.wire.pending == 0, "nothing written"
    finally:
        row.eager_kill = False
        await runtime.steer(row.id, "FINISH")
        result = await asyncio.wait_for(task, 30)
    assert result.ok is True and result.summary == "all done"


async def test_a_wrap_up_is_a_final_steer_and_the_run_ends_aborted_with_the_childs_text(tmp_path: Path) -> None:
    seen: list[Any] = []
    runtime, task, row = await _live(tmp_path)
    runtime.host = SubagentHost(cwd=runtime.host.cwd, session=runtime.host.session, on_progress=seen.append)
    delivery = await runtime.wrap_up(row.id, origin="user")
    assert delivery.outcome == "queued"
    assert row.wrap_up is not None and row.wrap_up.origin == "user" and row.wrap_up.text == cw.WRAP_UP_TEXT
    assert any(p.wrapping_up for p in seen), "the panel sees it"
    again = await runtime.wrap_up(row.id)
    assert again.outcome == "refused" and "already" in str(again.reason)
    result = await asyncio.wait_for(task, 30)
    assert result.status == "aborted" and result.ok is False
    assert result.reason == "wrapped up at the user's request"
    assert result.summary == "all done", "the child's own last text, 'all done' or not"
    assert result.note is None


async def test_a_wrap_up_that_was_not_sent_leaves_the_row_free_for_another(tmp_path: Path) -> None:
    """Fix round 1 N1: the request is recorded before the write, and un-recorded when nothing was written.

    The parent's end of the child's stdin is closing, so no byte can leave:
    ``not_sent``. On ``8d7ee999`` the row stayed latched — the second request
    was refused "a wrap-up was already requested for this agent", the panel
    kept "wrapping up", and the run's envelope said "a wrap-up was requested by
    the user but never reached the model" about a wrap-up nobody sent.
    """

    seen: list[Any] = []
    runtime, task, row = await _live(tmp_path)
    runtime.host = SubagentHost(cwd=runtime.host.cwd, session=runtime.host.session, on_progress=seen.append)
    assert row.proc is not None and row.proc.stdin is not None
    row.proc.stdin.close()  # the stub ends on the EOF, by itself
    first = await runtime.wrap_up(row.id)
    second = await runtime.wrap_up(row.id)
    assert [(d.outcome, d.reason) for d in (first, second)] == [("not_sent", "the agent's inbox is closed")] * 2
    assert row.wrap_up is None and row.stream.wrap_up_head is None
    assert seen[-1].wrapping_up is False, "the panel's mark went with it"
    result = await asyncio.wait_for(task, 30)
    assert (result.ok, result.summary, result.note) == (True, "all done", None)


async def test_a_wrap_up_whose_answer_is_unknown_stays_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N1's bound: only a ``not_sent`` is taken back.

    The line left for a child that never reads its stdin, so nobody knows
    whether its model will read it: the row keeps the request, a second is
    refused, and the envelope is what says it never reached the model. A guard
    on ``8d7ee999``; it fails when any answer but ``queued`` takes the record back.
    """

    from aelix_agents.print_channel import abort_child

    monkeypatch.setattr(cw, "ACK_SECONDS", 0.2)
    runtime, task, row = await _live(tmp_path, script=_DEAF_STUB)
    first = await runtime.wrap_up(row.id)
    second = await runtime.wrap_up(row.id)
    assert (first.outcome, first.reason) == ("unknown", "the agent did not answer within 0.2 s")
    assert (second.outcome, second.reason) == ("refused", "a wrap-up was already requested for this agent")
    assert row.wrap_up is not None, "still recorded"
    await abort_child(row)
    result = await asyncio.wait_for(task, 30)
    assert result.status == "aborted"
    assert result.note == "a wrap-up was requested by the user but never reached the model"


async def test_each_door_names_what_it_sent_refusals_included(tmp_path: Path) -> None:
    """Fix round 1 N2: ``Delivery.verb`` is the door's own word.

    On ``8d7ee999`` both doors left the default ``steer`` and the job adapter
    patched it afterwards with ``dataclasses.replace``.
    """

    runtime, task, row = await _live(tmp_path)
    try:
        note = await runtime.steer(row.id, "NOTE")
        later = await runtime.steer(row.id, "LATER", mode="follow_up")
    finally:
        wrapped = await runtime.wrap_up(row.id)
        twice = await runtime.wrap_up(row.id)
        await asyncio.wait_for(task, 30)
    gone = [await runtime.steer(row.id, "x", mode="follow_up"), await runtime.wrap_up(row.id)]
    assert [(d.outcome, d.verb) for d in (note, later, wrapped, twice, *gone)] == [
        ("queued", "steer"),
        ("queued", "follow_up"),
        ("queued", "wrap_up"),
        ("refused", "wrap_up"),
        ("refused", "follow_up"),
        ("refused", "wrap_up"),
    ]


async def test_a_steer_in_the_wrap_ups_words_before_the_request_is_not_its_reading(tmp_path: Path) -> None:
    """F4 through the door: the child read a steer with these words, then ended with the final unread.

    At ``8d7ee999`` the earlier steer's text made the wrap-up "consumed": the
    run was ``aborted`` "wrapped up at the user's request" although it ended on
    its own answer.
    """

    runtime, task, row = await _live(tmp_path, STUB_FINAL="unread", STUB_ANSWER="done on its own")
    assert (await runtime.steer(row.id, cw.WRAP_UP_TEXT)).outcome == "queued"
    await wait_for(lambda: cw.WRAP_UP_TEXT[:200] in row.stream.user_texts)
    assert (await runtime.wrap_up(row.id, origin="user")).outcome == "queued"
    result = await asyncio.wait_for(task, 30)
    assert (result.status, result.reason) == ("ok", None), "the run's own end"
    assert result.note == "a wrap-up was requested by the user but never reached the model"


async def test_a_wrap_up_queued_behind_an_error_end_is_reported_as_never_read(tmp_path: Path) -> None:
    """F9 (contract r2 §4.2's precedence, through the door): queued, never read, the run ended on an error.

    The child answered the final ``success: true``, read nothing and ended on
    its model's error. The result keeps that end — ``error``, the provider's
    words — and its note says the wrap-up never reached the model. Until fix
    round 1 this was tested on the pure envelope function only.
    """

    runtime, task, row = await _live(tmp_path, STUB_FINAL="unread", STUB_END="error")
    assert (await runtime.wrap_up(row.id, origin="user")).outcome == "queued"
    result = await asyncio.wait_for(task, 30)
    assert cw.WRAP_UP_TEXT[:200] not in row.stream.user_texts, "the premise: the final was not read"
    assert (result.status, result.ok) == ("error", False), "the run's own end, not 'aborted'"
    assert result.reason != "wrapped up at the user's request" and "boom" in str(result.error)
    assert result.note == "a wrap-up was requested by the user but never reached the model"


async def test_the_notes_drained_with_a_wrap_up_do_not_hide_that_it_was_read(tmp_path: Path) -> None:
    """F4 through the door: nine ``user`` messages follow the request, the final first.

    At ``8d7ee999`` the eight after it evicted its head from the window and the
    run was ``ok`` with "never reached the model".
    """

    runtime, task, row = await _live(tmp_path, STUB_FINAL="crowd")
    assert (await runtime.wrap_up(row.id, origin="model")).outcome == "queued"
    result = await asyncio.wait_for(task, 30)
    assert len(row.stream.user_texts) == 8 and cw.WRAP_UP_TEXT[:200] not in row.stream.user_texts, "the premise"
    assert (result.status, result.reason, result.note) == ("aborted", "wrapped up at the model's request", None)
    assert result.ok is False and result.summary == "all done"


async def test_tighten_all_tightens_a_looser_row_and_leaves_a_tighter_one(tmp_path: Path) -> None:
    record = tmp_path / "applied.txt"
    runtime, task, row = await _live(tmp_path, mode=PermissionMode.AUTO_ACCEPT, STUB_APPLIED=str(record))
    try:
        assert runtime.tighten_all(PermissionMode.YOLO) == [], "a loosening tightens nothing"
        assert runtime.tighten_all(PermissionMode.AUTO_ACCEPT) == [], "the same rank sends nothing"
        assert runtime.tighten_all(PermissionMode.AUTO) == [], "auto is auto-accept-edits for a child: the same rank"
        tasks = runtime.tighten_all(PermissionMode.PLAN)
        assert len(tasks) == 1
        await asyncio.wait_for(asyncio.gather(*tasks), 10)
        assert row.granted_mode is PermissionMode.PLAN
        assert row.stopped is False and row.abort_reason is None
        assert runtime.tighten_all(PermissionMode.PLAN) == [], "already at the floor"
        assert runtime.tighten_all(PermissionMode.DEFAULT) == [], "default is plan for a child: already there"
    finally:
        await runtime.steer(row.id, "FINISH")
        result = await asyncio.wait_for(task, 30)
    assert result.ok is True
    # What the CHILD applied, not the parent's bookkeeping (F9): one tighten, to plan.
    assert _applied(record) == ["launch auto-accept-edits", "plan"]


@pytest.mark.parametrize(
    ("parent", "sent"),
    [(PermissionMode.DEFAULT, "plan"), (PermissionMode.AUTO, "auto-accept-edits"), (PermissionMode.AUTO_ACCEPT, "auto-accept-edits")],
)
async def test_a_tighten_sends_the_mode_a_child_spawned_under_that_posture_would_start_at(
    tmp_path: Path, parent: PermissionMode, sent: str
) -> None:
    """One clamp (F1, VN5): ``default`` is ``plan`` for a child and ``auto`` is ``auto-accept-edits``."""

    record = tmp_path / "applied.txt"
    runtime, task, row = await _live(tmp_path, STUB_APPLIED=str(record))
    try:
        tasks = runtime.tighten_all(parent)
        assert len(tasks) == 1
        await asyncio.wait_for(asyncio.gather(*tasks), 10)
        assert row.granted_mode is PermissionMode(sent) and row.abort_reason is None
    finally:
        await runtime.steer(row.id, "FINISH")
        result = await asyncio.wait_for(task, 30)
    assert result.ok is True
    assert _applied(record) == ["launch yolo", sent]


class _HeldChannel(PrintChannel):
    """A channel whose ``run`` waits at its first line: the row is registered, the plan frozen, no argv built."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, plan: Any, *, child: Any = None, on_stream: Any = None) -> Any:
        self.entered.set()
        await self.release.wait()
        return await super().run(plan, child=child, on_stream=on_stream)


async def test_a_tighten_before_the_launch_is_in_the_childs_argv(tmp_path: Path) -> None:
    """F1: a row with no process yet. The tighten sends nothing; the launch reads the row."""

    record = tmp_path / "applied.txt"
    channel = _channel(kind=_HeldChannel, STUB_APPLIED=str(record))
    assert isinstance(channel, _HeldChannel)
    runtime, task = await _spawn(tmp_path, channel, PermissionMode.YOLO)
    await asyncio.wait_for(channel.entered.wait(), 10)
    (row,) = runtime._children.values()
    assert row.proc is None and row.granted_mode is PermissionMode.YOLO
    assert runtime.tighten_all(PermissionMode.PLAN) == [], "no process: nothing to send to"
    channel.release.set()
    row = await _halfway(runtime, task)
    await runtime.steer(row.id, "FINISH")
    result = await asyncio.wait_for(task, 30)
    assert result.ok is True
    assert _applied(record) == ["launch plan"], "launched at the tighter mode, and not told twice"


async def test_a_tighten_during_the_spawn_reaches_the_child_once_its_inbox_is_attached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: the argv is built (``yolo``), the process is not there yet. The tighten is sent after the attach."""

    record = tmp_path / "applied.txt"
    spawn = asyncio.create_subprocess_exec
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(*argv: Any, **kwargs: Any) -> Any:
        if _INBOX_STUB in argv:
            entered.set()
            await release.wait()
        return await spawn(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", held)
    runtime, task = await _spawn(tmp_path, _channel(STUB_APPLIED=str(record)), PermissionMode.YOLO)
    await asyncio.wait_for(entered.wait(), 10)
    (row,) = runtime._children.values()
    assert row.proc is None
    assert runtime.tighten_all(PermissionMode.PLAN) == [], "no process: nothing to send to"
    release.set()
    row = await _halfway(runtime, task)
    await runtime.steer(row.id, "FINISH")
    result = await asyncio.wait_for(task, 30)
    assert result.ok is True
    assert _applied(record) == ["launch yolo", "plan"]
    assert row.abort_reason is None


async def test_a_tighten_on_a_row_whose_inbox_broke_kills_it_with_the_floor_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: a real write timeout breaks the inbox; no line can reach the child, so the tighten fails closed."""

    monkeypatch.setattr(cw, "WRITE_SECONDS", 0.3)
    runtime, task, row = await _live(tmp_path, script=_DEAF_STUB)
    delivery = await runtime.steer(row.id, "x" * (4 << 20))
    assert delivery.outcome == "unknown" and "did not take the line" in str(delivery.reason)
    assert row.wire.broken is True and row.stopped is False
    tasks = runtime.tighten_all(PermissionMode.PLAN)
    assert len(tasks) == 1, "a broken inbox is not skipped"
    await asyncio.wait_for(asyncio.gather(*tasks), 15)
    result = await asyncio.wait_for(task, 30)
    assert row.abort_reason == FLOOR_REASON
    assert result.status == "aborted" and result.reason == FLOOR_REASON


async def test_the_result_reports_the_posture_the_child_ended_at(tmp_path: Path) -> None:
    """F10: tightened mid-run, the result (and the footer the model reads) said the launch mode.

    The mode is the tighter one once the child echoed it; a child that never
    echoed one — here: killed because it refused — reports the mode it was
    launched with. Fails on ``8d7ee999``: ``yolo`` in the first leg.
    """

    from aelix_agents.tool import _usage_line

    runtime, task, row = await _live(tmp_path)
    await asyncio.wait_for(asyncio.gather(*runtime.tighten_all(PermissionMode.PLAN)), 10)
    await runtime.steer(row.id, "FINISH")
    result = await asyncio.wait_for(task, 30)
    assert result.ok is True and result.permission_mode == "plan", "what the child held when it ended"
    assert " · ok · plan · " in _usage_line(result), _usage_line(result)

    (tmp_path / "second").mkdir()
    runtime, task, row = await _live(tmp_path / "second", STUB_TIGHTEN="refuse")
    await asyncio.wait_for(asyncio.gather(*runtime.tighten_all(PermissionMode.PLAN)), 15)
    killed = await asyncio.wait_for(task, 30)
    assert (killed.status, killed.reason) == ("aborted", FLOOR_REASON)
    assert killed.permission_mode == "yolo", "it never held plan: the launch mode"


async def test_a_tighten_skips_a_row_that_is_stopping_and_only_lowers_one_with_no_process() -> None:
    """The row states with nothing to write to: being killed (untouched), no process yet (lowered, nothing sent)."""

    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: ".", session=lambda: None), channel=_channel())
    stopping = RunningChild(id="s", profile="p", granted_mode=PermissionMode.YOLO)
    stopping.proc, stopping.stopped = object(), True
    killed = RunningChild(id="k", profile="p", granted_mode=PermissionMode.YOLO)
    killed.proc, killed.eager_kill = object(), True
    bare = RunningChild(id="b", profile="p", granted_mode=PermissionMode.YOLO)
    runtime._children.update(s=stopping, k=killed, b=bare)
    assert runtime.tighten_all(PermissionMode.PLAN) == []
    assert stopping.granted_mode is PermissionMode.YOLO and stopping.abort_reason is None
    assert killed.granted_mode is PermissionMode.YOLO and killed.abort_reason is None
    assert bare.granted_mode is PermissionMode.PLAN and bare.abort_reason is None


class _EchoWire:
    """A wire that records each request and answers ``queued`` with the mode echoed."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def request(self, kind: str, payload: dict[str, Any]) -> Any:
        self.requests.append((kind, dict(payload)))
        return cw.Reply("queued", data={"mode": payload.get("mode")})


@pytest.mark.parametrize("why", ["stopped", "eager_kill", None], ids=["stopped", "eager-kill", "control"])
async def test_a_late_tighten_reaches_no_row_that_is_being_killed(why: str | None) -> None:
    """Fix round 2, V-F4 (mutant R03: ``tighten_late``'s ``if child.stopped or
    child.eager_kill: return`` -> ``if False:``). A tighten that landed while the
    row had no process is sent once the inbox is attached, unless the row is
    being killed: then nothing is written to it. The control leg (neither) is
    sent the tighter mode."""

    from aelix_agents import print_channel as pc

    wire = _EchoWire()
    row = RunningChild(id="r", profile="p", granted_mode=PermissionMode.PLAN)
    row.applied_mode, row.wire = PermissionMode.YOLO, wire
    if why is not None:
        setattr(row, why, True)
    pc.tighten_late(row)
    await asyncio.wait_for(asyncio.gather(*list(pc._LATE_TIGHTENS)), 5)
    if why is None:
        assert wire.requests == [("set_permission_mode", {"mode": "plan"})]
        assert row.applied_mode is PermissionMode.PLAN
    else:
        assert wire.requests == [], "nothing is written to a row that is being killed"
        assert row.applied_mode is PermissionMode.YOLO and row.abort_reason is None


async def test_a_tighten_kills_a_row_with_a_process_and_no_inbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix round 2, V-F4 (mutant R05: ``reply = None if wire is None else await
    wire.request(...)`` -> ``reply = await wire.request(...)``). No product path
    builds such a row; ADR-0291's note and ADR-0306 D8 name it: it fails
    closed, killed with the floor reason, and the tighten does not raise."""

    from aelix_agents import print_channel as pc

    killed: list[str | None] = []

    async def abort(child: Any, **_kw: Any) -> None:
        killed.append(child.abort_reason)

    monkeypatch.setattr(pc, "abort_child", abort)
    row = RunningChild(id="r", profile="p", state="running")
    row.proc = object()
    assert row.wire is None, "the premise: a process and no inbox"
    await pc.tighten_child(row, PermissionMode.PLAN)
    assert killed == [FLOOR_REASON]


# === the service and the kind's adapter ============================================


async def test_service_control_refuses_a_kind_with_no_controls_and_another_sessions_job(tmp_path: Path) -> None:
    service = fast_service()
    _sleeper.register(service)
    mine = make_harness(await make_session(tmp_path, "a.jsonl", session_id="A"))
    await service.bind(mine)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 5})
    try:
        answer = await service.control(job_id, "steer", {"text": "x"})
        # V7 — the sleeper declares no controls and no hint: the sentence in a human's words, whole.
        assert answer == "this kind of job does not take a message"
        assert KindCapabilities().controls == ()
        with pytest.raises(JobNotFound):
            await service.control("nope", "steer", {"text": "x"})
        # F9 — a REAL job of another session, still running: the id exists in
        # the service and only the owner check refuses it ("nope" was refused
        # by the lookup alone, so the check could be removed unseen).
        await service.bind(make_harness(await make_session(tmp_path, "b.jsonl", session_id="B")))
        assert service.owner == "B" and service.snapshot(job_id, any_owner=True).state == "running"  # type: ignore[union-attr]
        with pytest.raises(JobNotFound):
            await service.control(job_id, "steer", {"text": "x"})
        await service.bind(mine)
    finally:
        await service.stop(job_id)
        ended = await service.control(job_id, "steer", {"text": "x"})
        assert ended in (f"{job_id[:6]} already ended: stopped", f"{job_id[:6]} has ended: nothing runs to take a message")
        await service.shutdown()


@pytest.mark.usefixtures("jobs_on")
async def test_the_adapter_reaches_a_spawned_rows_inbox(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, channel=_channel(AELIX_JOBS_PROTOCOL="1"), posture=PermissionMode.YOLO)
    await rig.prompt_started()
    spawned = await rig.call({"action": "spawn", "profile": "scout", "task": "count files"})
    assert not spawned.is_error, spawned.content[0].text  # type: ignore[union-attr]
    (job_id,) = [s.job_id for s in rig.jobs()]
    await wait_for(lambda: (rig.service.snapshot(job_id).progress or {}).get("last_text") == "halfway")  # type: ignore[union-attr]
    try:
        answer = await rig.service.control(job_id, "steer", {"text": "NOTE", "origin": "user"})
        assert answer.startswith("Queued the note:"), answer
        follow = await rig.service.control(job_id, "steer", {"text": "LATER", "mode": "follow_up"})
        assert follow.startswith("Queued the follow-up:"), follow
        assert await rig.service.control(job_id, "steer", {"text": ""}) == "steer needs a message"
        # A verb with no word of its own is shown as written, with the agent kind's hint (V7).
        assert (await rig.service.control(job_id, "status", {})) == (
            'this kind of job does not take status; use agent(action="status"|"wait"|"stop"|"result", id=…)'
        )
        wrapped = await rig.service.control(job_id, "wrap_up", {"origin": "user"})
        assert wrapped.startswith("Queued the wrap-up:"), wrapped
        await wait_for(lambda: rig.service.snapshot(job_id).state in ("done", "failed", "stopped"), timeout=30)  # type: ignore[union-attr]
        snap = rig.service.snapshot(job_id)
        assert snap is not None and snap.state == "stopped", "a wrapped-up agent is not done (gemini-cli#22323)"
        assert snap.payload.get("envelope_status") == "aborted"
        assert snap.brief == "all done", "the brief is the child's own last line"
        out = rig.service.output(job_id, offset=0, limit=8192)
        text = out if isinstance(out, str) else out.text
        assert "wrapped up at the user's request" in text, text
    finally:
        await rig.service.shutdown()


def test_describe_delivery_words() -> None:
    assert describe_delivery(Delivery("queued", phase="streaming"), what="the note").startswith("Queued the note: the agent reads it after")
    assert "next one" in describe_delivery(Delivery("queued", phase="idle"), what="the note")
    assert describe_delivery(Delivery("refused", reason="r"), what="x") == "Not taken: r."
    assert describe_delivery(Delivery("not_sent", reason="r"), what="x") == "Not sent: r."
    assert describe_delivery(Delivery("unknown", reason="r"), what="the note") == "Unknown whether the note arrived: r."
    assert json.dumps({"ok": True})  # the file is importable on every platform


# === a Ctrl+B'd row (Codex H9) ======================================================


@pytest.mark.usefixtures("jobs_on", "reap_children")
async def test_the_adapter_reaches_a_ctrl_b_moved_rows_inbox(tmp_path: Path) -> None:
    """The human's ``/jobs steer`` after Ctrl+B (owner Q2, 2026-11-18): the moved run's
    row is the call's, held by an ``AdoptedRunner``, and the adapter finds it there."""

    from tests.agents_ext._detach_rig import finish, parked_call, row_of, tui_rig, within
    from tests.agents_ext.test_tool_and_security import _text

    record = tmp_path / "applied.txt"
    rig = await tui_rig(
        tmp_path, channel=_channel(AELIX_JOBS_PROTOCOL="1", STUB_APPLIED=str(record)), posture=PermissionMode.YOLO
    )
    turn = await parked_call(rig)
    row = row_of(rig)
    await wait_for(lambda: row.stream.last_text == "halfway")
    assert rig.jobs_ui.request_detach()
    text = _text(await within(turn))
    assert "Moved to the background" in text, text
    (job_id,) = [s.job_id for s in rig.jobs()]
    try:
        answer = await rig.service.control(job_id, "steer", {"text": "NOTE", "origin": "user"})
        assert answer.startswith("Queued the note:"), answer
        await wait_for(lambda: "NOTE" in row.stream.user_texts)
        # W5 on a moved row (guard): the posture's listener reaches it like any row.
        rig.ext.posture.set(PermissionMode.PLAN)
        await wait_for(lambda: _applied(record) == ["launch yolo", "plan"])
        wrapped = await rig.service.control(job_id, "wrap_up", {"origin": "user"})
        assert wrapped.startswith("Queued the wrap-up:"), wrapped
        await wait_for(lambda: rig.service.snapshot(job_id).state in ("done", "failed", "stopped"), timeout=30)  # type: ignore[union-attr]
        snap = rig.service.snapshot(job_id)
        assert snap is not None and snap.state == "stopped" and snap.payload.get("envelope_status") == "aborted"
    finally:
        await finish(rig)
