"""The kernel notice inbox (#722, ADR-0293).

A notice is placed in the session under an id derived from its ``notice_id``
before the model sees it, once, at a turn boundary, at most 64 KiB of bodies
per drain and always at least one. Nothing that clears the user's queues
touches it. On main ``a385c64a`` none of ``post_notice``, ``prompt_notices`` or
``unanswered_notice_at_leaf`` exists, so every test here fails there with an
``AttributeError``; the "naive" column in each docstring says what a simpler
inbox would get wrong.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import BeforeAgentStartHookEvent
from aelix_agent_core.harness.inbox import (
    NOTICE_DRAIN_BUDGET,
    Notice,
    cut_utf8,
    notice_entry_id,
)
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_agent_core.session.entries import CustomMessageEntry, MessageEntry
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import AssistantEndEvent, AssistantMessageEvent, AssistantStartEvent


class _Model:
    """A stream that records every context it is asked to answer."""

    def __init__(self) -> None:
        self.contexts: list[list[str]] = []
        self.gate: asyncio.Event | None = None

    async def __call__(
        self, _model: Any, context: Any, _options: Any = None
    ) -> AsyncIterator[AssistantMessageEvent]:
        texts: list[str] = []
        for message in context.messages:
            for block in getattr(message, "content", []) or []:
                if isinstance(block, TextContent):
                    texts.append(block.text)
        self.contexts.append(texts)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if self.gate is not None:
            await self.gate.wait()
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )


class _ToolThenWait:
    """Calls tool ``t`` once, then answers each later request once ``released`` is set."""

    def __init__(self, released: asyncio.Event) -> None:
        self.contexts: list[list[str]] = []
        self.released = released

    async def __call__(self, _model: Any, context: Any, _options: Any = None) -> AsyncIterator[AssistantMessageEvent]:
        from aelix_ai.messages import ToolCallContent

        self.contexts.append(
            [
                block.text
                for message in context.messages
                for block in getattr(message, "content", []) or []
                if isinstance(block, TextContent)
            ]
        )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if len(self.contexts) == 1:
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[ToolCallContent(tool_call_id="c1", tool_name="t", input={})],
                    stop_reason="tool_use",
                )
            )
            return
        await self.released.wait()
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )


async def _session(tmp_path: Path, name: str = "s.jsonl") -> Session:
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(tmp_path / name), cwd="/repo", session_id="s"
    )
    return Session(storage)


def _harness(model: _Model, session: Session | None) -> AgentHarness:
    return AgentHarness(AgentHarnessOptions(stream_fn=model, session=session, notice_inbox=True))


def _notice(n: int, body: str | None = None) -> Notice:
    return Notice(
        notice_id=f"job-{n}:final",
        custom_type="aelix.job_notice",
        body=body if body is not None else f"[notice {n}]",
        details={"v": 1, "job_id": f"job-{n}"},
        brief=f"job {n} done",
    )


async def test_a_notice_is_placed_before_the_model_sees_it_and_written_once(tmp_path: Path) -> None:
    """Naive: appending the notice at ``message_end`` like a steer line writes it a
    second time as a ``message`` entry next to the placed ``custom_message``."""

    model = _Model()
    session = await _session(tmp_path)
    harness = _harness(model, session)
    placed: list[str] = []
    assert harness.post_notice(_notice(1), on_placed=lambda _n, entry_id: placed.append(entry_id))
    assert harness.pending_notices == 1

    await harness.prompt("hello")

    # Ahead of the typed line, in the same request (Codex #8: a notice is never
    # placed after an input an Esc can still withdraw).
    assert model.contexts == [["[notice 1]", "hello"]]
    assert placed == [notice_entry_id("job-1:final")]
    entries = await session.get_entries()
    kinds = [(e.type, getattr(e, "id", "")) for e in entries]
    notices = [e for e in entries if isinstance(e, CustomMessageEntry)]
    assert len(notices) == 1 and notices[0].id == notice_entry_id("job-1:final")
    assert notices[0].details == {"v": 1, "job_id": "job-1", "notice_id": "job-1:final"}
    texts = [
        block.text
        for e in entries
        if isinstance(e, MessageEntry) and isinstance(e.message, UserMessage)
        for block in e.message.content
        if isinstance(block, TextContent)
    ]
    assert "[notice 1]" not in texts, f"the notice was appended again as a message: {kinds}"
    assert harness.pending_notices == 0 and harness.undelivered_notices() == []


async def test_a_second_process_adopts_a_placed_notice_and_shows_it_nowhere(tmp_path: Path) -> None:
    """Naive: a per-process "delivered" set lets a second harness on the same file
    (a restart that re-posts the notice) show it to the model again."""

    model = _Model()
    session = await _session(tmp_path)
    first = _harness(model, session)
    first.post_notice(_notice(1))
    await first.prompt("one")

    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(tmp_path / "s.jsonl")))
    model2 = _Model()
    second = _harness(model2, reopened)
    placed: list[str] = []
    second.post_notice(_notice(1), on_placed=lambda _n, entry_id: placed.append(entry_id))
    await second.prompt("two")

    assert placed == [notice_entry_id("job-1:final")], "adopted counts as placed"
    assert model2.contexts[0].count("[notice 1]") == 1, "read from the session, not shown twice"
    entries = await reopened.get_entries()
    assert sum(isinstance(e, CustomMessageEntry) for e in entries) == 1


async def test_a_drain_admits_at_most_the_budget_and_always_one(tmp_path: Path) -> None:
    """Naive: draining every pending notice at once sends 96 KiB in one request."""

    model = _Model()
    harness = _harness(model, await _session(tmp_path))
    chunk = NOTICE_DRAIN_BUDGET // 2 - 1024
    for n in range(3):
        harness.post_notice(_notice(n, body=f"[{n}]" + "x" * chunk))
    harness.post_notice(_notice(9, body="[9]" + "y" * (NOTICE_DRAIN_BUDGET * 2)))

    await harness.prompt("go")

    # Each model call is preceded by one boundary: two fit, the third waits, the
    # oversized one goes alone.
    firsts = [[t[:3] for t in ctx if t.startswith("[")] for ctx in model.contexts]
    assert firsts[0] == ["[0]", "[1]"]
    assert firsts[1] == ["[0]", "[1]", "[2]"]
    assert firsts[2] == ["[0]", "[1]", "[2]", "[9]"]
    assert harness.pending_notices == 0


async def test_nothing_that_clears_the_users_queues_touches_a_notice(tmp_path: Path) -> None:
    """Naive: keeping notices on the follow-up queue lets ``abort``, an Esc
    withdrawal, ``give_back_queued`` and ``take_queued_messages`` drop them."""

    model = _Model()
    model.gate = asyncio.Event()
    harness = _harness(model, await _session(tmp_path))
    turn = asyncio.ensure_future(harness.prompt("long"))
    while not model.contexts:
        await asyncio.sleep(0)
    harness.post_notice(_notice(1))
    await harness.abort(withdraw_unanswered=True)
    await asyncio.wait_for(turn, 5)
    await harness.give_back_queued()
    harness.take_queued_messages()

    assert harness.pending_notices == 1
    assert [n.notice_id for n in harness.undelivered_notices()] == ["job-1:final"]


async def test_prompt_notices_places_first_then_runs_a_notice_turn(tmp_path: Path) -> None:
    model = _Model()
    session = await _session(tmp_path)
    harness = _harness(model, session)
    sources: list[str] = []
    harness.hooks.on("before_agent_start", lambda e, _c: sources.append(e.source))

    assert (await harness.prompt_notices()).reason == "nothing"
    harness.post_notice(_notice(1))
    result = await harness.prompt_notices()

    assert result.started and result.placed == 1
    assert sources == ["notice"]
    assert model.contexts == [["[notice 1]"]]
    assert not await harness.unanswered_notice_at_leaf()
    entries = await session.get_entries()
    assert [e.type for e in entries][-2:] == ["custom_message", "message"]


async def test_prompt_notices_is_refused_when_held_busy_or_disposed(tmp_path: Path) -> None:
    model = _Model()
    harness = _harness(model, await _session(tmp_path))
    harness.post_notice(_notice(1))
    harness.hold_turns("route undecided")
    assert (await harness.prompt_notices()).reason == "held"
    harness.hold_turns(None)

    model.gate = asyncio.Event()
    turn = asyncio.ensure_future(harness.prompt("busy"))
    while not model.contexts:
        await asyncio.sleep(0)
    assert (await harness.prompt_notices()).reason == "busy"
    model.gate.set()
    await turn
    assert harness.pending_notices == 0, "the running turn's boundary took it"


async def test_a_notice_left_unanswered_at_the_leaf_gets_a_continue_turn(tmp_path: Path) -> None:
    """A turn cut off after the placement leaves the notice as the leaf's last
    message; ``prompt_notices`` answers it with nothing new placed."""

    model = _Model()
    model.gate = asyncio.Event()
    session = await _session(tmp_path)
    harness = _harness(model, session)
    harness.post_notice(_notice(1))
    turn = asyncio.ensure_future(harness.prompt_notices())
    while not model.contexts:
        await asyncio.sleep(0)
    await harness.abort()
    await turn
    assert await harness.unanswered_notice_at_leaf()

    model.gate = None
    result = await harness.prompt_notices()
    assert result.started and result.placed == 0
    assert model.contexts[-1] == ["[notice 1]"]
    assert not await harness.unanswered_notice_at_leaf()


async def test_an_id_taken_by_another_payload_is_held_not_written(tmp_path: Path) -> None:
    model = _Model()
    session = await _session(tmp_path)
    await session.place_custom_message(
        notice_entry_id("job-1:final"), "other", "someone else's", True, {"notice_id": "different"}
    )
    harness = _harness(model, session)
    harness.post_notice(_notice(1))
    await harness.prompt("go")

    assert harness.held_notices() == {"job-1:final": "placement_conflict"}
    assert "[notice 1]" not in model.contexts[0]
    entries = await session.get_entries()
    assert sum(isinstance(e, CustomMessageEntry) for e in entries) == 1


async def test_a_cancelled_placement_returns_the_notice_and_the_next_one_adopts(
    tmp_path: Path,
) -> None:
    """A cancel landing in the placement's write propagates; the claim goes back to
    pending, and the next placement of the same id adopts what landed."""

    model = _Model()
    session = await _session(tmp_path)
    harness = _harness(model, session)
    real = session.place_custom_message

    async def lands_then_cancelled(*args: Any, **kwargs: Any) -> Any:
        await real(*args, **kwargs)
        raise asyncio.CancelledError

    session.place_custom_message = lands_then_cancelled  # type: ignore[method-assign]
    harness.post_notice(_notice(1))
    with pytest.raises(asyncio.CancelledError):
        await harness.prompt_notices()
    assert harness.pending_notices == 1
    session.place_custom_message = real  # type: ignore[method-assign]

    result = await harness.prompt_notices()
    assert result.started and result.placed == 0, "adopted: the context has it from the file"
    assert model.contexts[-1] == ["[notice 1]"]
    entries = await session.get_entries()
    assert sum(isinstance(e, CustomMessageEntry) for e in entries) == 1


async def test_listeners_hear_queued_placed_released_and_idle(tmp_path: Path) -> None:
    model = _Model()
    harness = _harness(model, await _session(tmp_path))
    heard: list[str] = []
    harness.add_notice_listener(heard.append)
    harness.hold_turns("x")
    harness.hold_turns(None)
    harness.post_notice(_notice(1))
    await harness.prompt_notices()

    assert heard[:2] == ["released", "queued"]
    assert "placed" in heard and heard[-1] == "idle"


async def test_no_session_harness_keeps_the_notice_in_live_state() -> None:
    model = _Model()
    harness = _harness(model, None)
    harness.post_notice(_notice(1))
    await harness.prompt("hi")
    assert model.contexts == [["[notice 1]", "hi"]]
    assert not await harness.unanswered_notice_at_leaf()


def test_before_agent_start_event_accepts_notice_source() -> None:
    assert BeforeAgentStartHookEvent(source="notice").source == "notice"


# === #722 fix round 1 =========================================================


async def test_with_the_inbox_off_the_kernel_is_what_it_was(tmp_path: Path) -> None:
    """Codex #1: off by default — posting and a notice turn raise, the readers are
    empty, and a typed turn's request and file are those of a harness without it.

    Fails on ``a80e81fa``: ``post_notice`` accepted the notice and the ordinary
    prompt sent ``NEW-BEHAVIOR`` to the model with the gate off.
    """

    from aelix_agent_core.harness.core import AgentHarnessError

    model = _Model()
    session = await _session(tmp_path)
    harness = AgentHarness(AgentHarnessOptions(stream_fn=model, session=session))
    assert not harness.notices_enabled
    with pytest.raises(AgentHarnessError) as raised:
        harness.post_notice(_notice(1, body="NEW-BEHAVIOR"))
    assert raised.value.code == "invalid_state"
    with pytest.raises(AgentHarnessError):
        await harness.prompt_notices()
    with pytest.raises(AgentHarnessError):
        harness.add_notice_listener(lambda _c: None)
    assert harness.pending_notices == 0 and harness.undelivered_notices() == []
    assert harness.held_notices() == {}

    await harness.prompt("ordinary product prompt")
    await harness.steer("a steer")  # queued after the turn: the next drain is the queue's own
    await harness.prompt("second")

    assert model.contexts[0] == ["ordinary product prompt"]
    assert "NEW-BEHAVIOR" not in [t for c in model.contexts for t in c]
    assert not await harness.unanswered_notice_at_leaf()
    entries = await session.get_entries()
    assert not any(isinstance(e, CustomMessageEntry) for e in entries)


async def test_a_notice_whose_line_landed_then_raised_mid_run_reaches_that_run(tmp_path: Path) -> None:
    """Codex #2: the placement's write lands and raises; the retry adopts the
    entry. The run whose context was read before it must still be given it.

    Fails on ``a80e81fa``: the adoption returned no message, so the follow-up
    boundary had nothing to show, the notice was marked placed (pending 0), and
    the model never saw it in that run.
    """

    from tests.session.test_append_resolution_611 import _enospc, _PlanFs, _whole

    fs = _PlanFs()
    session = Session(
        await JsonlSessionStorage.create(fs, str(tmp_path / "s.jsonl"), cwd="/repo", session_id="s")
    )
    model = _Model()
    model.gate = asyncio.Event()
    harness = _harness(model, session)
    turn = asyncio.ensure_future(harness.prompt("go"))
    while not model.contexts:
        await asyncio.sleep(0)
    # Posted while the answer streams: placed at the boundary after it, and
    # that write (the third: the typed line, the answer, the notice) lands and raises.
    harness.post_notice(_notice(1, body="DURABLE-AND-SHOWN"))
    fs.arm(_whole, _enospc(), skip=1)
    model.gate.set()
    await asyncio.wait_for(turn, 5)

    assert len(model.contexts) == 2, model.contexts
    assert model.contexts[1].count("DURABLE-AND-SHOWN") == 1
    entries = await session.get_entries()
    assert [e.id for e in entries if isinstance(e, CustomMessageEntry)] == [
        notice_entry_id("job-1:final")
    ]
    assert harness.pending_notices == 0
    assert not await harness.unanswered_notice_at_leaf(), "the run answered it"


async def test_a_notice_waiting_when_the_user_types_goes_first_and_an_esc_keeps_one_delivery(
    tmp_path: Path,
) -> None:
    """Codex #8: the line is withdrawn, the notice stays — once in the whole file.

    Fails on ``a80e81fa``: the notice was placed after the typed line, and the
    withdrawal's replay appended it again under a random id (two deliveries of
    one ``notice_id``; only the copy on the current path).
    """

    model = _Model()
    model.gate = asyncio.Event()
    session = await _session(tmp_path)
    harness = _harness(model, session)
    harness.post_notice(_notice(1, body="NOTICE-SURVIVES"))
    turn = asyncio.ensure_future(harness.prompt("withdraw this input"))
    while not model.contexts:
        await asyncio.sleep(0)
    assert model.contexts[0] == ["NOTICE-SURVIVES", "withdraw this input"], "one request"
    await harness.abort(withdraw_unanswered=True)
    await asyncio.wait_for(turn, 5)

    entries = await session.get_entries()
    deliveries = [
        e.id
        for e in entries
        if isinstance(e, CustomMessageEntry) and (e.details or {}).get("notice_id") == "job-1:final"
    ]
    assert deliveries == [notice_entry_id("job-1:final")]
    branch = await session.get_branch()
    assert [e.id for e in branch if isinstance(e, CustomMessageEntry)] == deliveries
    assert not any(
        isinstance(e, MessageEntry) and isinstance(e.message, UserMessage) for e in branch
    ), "the typed line was withdrawn"


async def test_a_notice_placed_after_an_answer_is_never_withdrawn_or_given_back(tmp_path: Path) -> None:
    """Codex #8 and verify V06: a notice placed at the boundary after a tool batch,
    then a steer, then an Esc before the steer is answered. The steer is given
    back; the notice is not (it is not the user's input) and stays on the path,
    once in the file.

    A placed notice counted as the user's input (V06) gives its text back to the
    user's editor here.
    """

    from aelix_agent_core.types import AgentTool
    from aelix_ai.tools import ToolResult

    released = asyncio.Event()
    session = await _session(tmp_path)
    model = _ToolThenWait(released)
    harness_box: list[AgentHarness] = []

    async def tool(_args: Any, _ctx: Any = None) -> ToolResult:
        harness_box[0].post_notice(_notice(1, body="BACKGROUND-RESULT"))
        await harness_box[0].steer("a steer the user typed")
        return ToolResult(content=[TextContent(text="tool done")])

    harness = AgentHarness(
        AgentHarnessOptions(
            stream_fn=model,
            session=session,
            tools=[AgentTool(name="t", execute=tool)],
            notice_inbox=True,
        )
    )
    harness_box.append(harness)
    given_back: list[str] = []

    def listen(event: Any) -> None:
        if getattr(event, "type", None) == "input_withdrawn":
            for message in event.messages:
                given_back.extend(
                    b.text for b in getattr(message, "content", []) if isinstance(b, TextContent)
                )

    harness.subscribe(listen)
    turn = asyncio.ensure_future(harness.prompt("use the tool"))
    while len(model.contexts) < 2:
        await asyncio.sleep(0)
    assert model.contexts[1][-2:] == ["BACKGROUND-RESULT", "a steer the user typed"]
    await harness.abort(withdraw_unanswered=True)
    released.set()
    await asyncio.wait_for(turn, 5)

    assert given_back == ["a steer the user typed"]
    entries = await session.get_entries()
    deliveries = [
        e.id
        for e in entries
        if isinstance(e, CustomMessageEntry) and (e.details or {}).get("notice_id") == "job-1:final"
    ]
    assert deliveries == [notice_entry_id("job-1:final")]
    branch_ids = [e.id for e in await session.get_branch()]
    assert deliveries[0] in branch_ids


async def test_a_drain_charges_each_notice_its_placement_line(tmp_path: Path) -> None:
    """Codex #10: the budget counts the line ``finalize`` may add, and the kernel
    cuts that line to what it charged.

    Fails on ``a80e81fa``: five 12.5 KiB notices whose hints added 512 bytes
    each were all claimed (64,230 bytes at claim time) and the first request
    carried 66,790 bytes of them.
    """

    model = _Model()
    harness = _harness(model, await _session(tmp_path))
    body = "[n]" + "x" * 12_840

    async def hint() -> str:
        return "h" * 1000

    for n in range(5):
        harness.post_notice(
            Notice(notice_id=f"n{n}", custom_type="t", body=body, finalize=hint, finalize_max=512)
        )
    await harness.prompt_notices()
    first = [t for t in model.contexts[0] if t.startswith("[n]")]
    assert sum(len(t.encode()) for t in first) <= NOTICE_DRAIN_BUDGET
    assert len(first) == 4
    assert all(t.endswith("\n" + "h" * 511) for t in first), "the line is cut to what was charged"


async def test_a_failed_placement_write_keeps_the_notice_for_the_next_boundary(tmp_path: Path) -> None:
    """Verify V08: a write that fails (nothing landed) releases the notice to the
    front of the queue; it is placed by the next boundary, never dropped."""

    model = _Model()
    session = await _session(tmp_path)
    harness = _harness(model, session)
    real = session.place_custom_message
    calls = {"n": 0}

    async def refuses_once(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk refused (injected)")
        return await real(*args, **kwargs)

    session.place_custom_message = refuses_once  # type: ignore[method-assign]
    harness.post_notice(_notice(1))
    first = await harness.prompt_notices()
    assert not first.started and first.reason == "nothing"
    assert harness.pending_notices == 1, "released, not dropped"
    second = await harness.prompt_notices()
    assert second.started and second.placed == 1
    assert model.contexts[-1] == ["[notice 1]"]


async def test_a_run_lets_go_of_the_notice_messages_it_no_longer_needs(tmp_path: Path) -> None:
    """Verify N6: the identities a run needed for its ``message_end`` are dropped at
    its release; a harness with a session keeps none of them for its lifetime."""

    model = _Model()
    harness = _harness(model, await _session(tmp_path))
    for n in range(3):
        harness.post_notice(_notice(n))
        await harness.prompt(f"turn {n}")
    assert harness._notice_messages == {}


async def test_a_notice_that_arrives_before_the_answer_waits_and_an_esc_never_duplicates_it(
    tmp_path: Path,
) -> None:
    """Codex #8, the other order: a notice posted after the typed line is committed
    and before the model answers it (here from ``before_agent_start``) waits for the
    boundary after the answer. An Esc before that answer withdraws the line and
    leaves the notice pending — never placed after the line and replayed."""

    model = _Model()
    model.gate = asyncio.Event()
    session = await _session(tmp_path)
    harness = _harness(model, session)

    async def late(_event: Any, _ctx: Any) -> None:
        harness.post_notice(_notice(1, body="ARRIVED-LATE"))

    harness.hooks.on("before_agent_start", late)
    turn = asyncio.ensure_future(harness.prompt("withdraw me"))
    while not model.contexts:
        await asyncio.sleep(0)
    assert "ARRIVED-LATE" not in model.contexts[0], "not placed after the unanswered line"
    await harness.abort(withdraw_unanswered=True)
    await asyncio.wait_for(turn, 5)
    entries = await session.get_entries()
    assert not any(
        isinstance(e, CustomMessageEntry) and (e.details or {}).get("notice_id") == "job-1:final"
        for e in entries
    )
    assert harness.pending_notices == 1, "still owed"
    model.gate.set()
    result = await harness.prompt_notices()
    assert result.started and result.placed == 1
    deliveries = [
        e.id
        for e in await session.get_entries()
        if isinstance(e, CustomMessageEntry) and (e.details or {}).get("notice_id") == "job-1:final"
    ]
    assert deliveries == [notice_entry_id("job-1:final")]


async def test_a_notice_whose_write_failed_keeps_its_place_in_the_queue(tmp_path: Path) -> None:
    """Verify V03: a released notice goes back to the FRONT — arrival order holds
    across a failed write, ahead of a notice the budget had left pending."""

    model = _Model()
    session = await _session(tmp_path)
    harness = _harness(model, session)
    real = session.place_custom_message
    calls = {"n": 0}

    async def refuses_once(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk refused (injected)")
        return await real(*args, **kwargs)

    session.place_custom_message = refuses_once  # type: ignore[method-assign]
    big = NOTICE_DRAIN_BUDGET // 2 + 1024
    harness.post_notice(_notice(1, body="[first]" + "a" * big))
    harness.post_notice(_notice(2, body="[second]" + "b" * big))
    assert not (await harness.prompt_notices()).started  # only the first was claimed, and it failed
    await harness.prompt_notices()
    assert model.contexts[-1][0].startswith("[first]"), "the first still goes first"


# === #722 fix round 2 =========================================================


async def _placed_by_an_earlier_process(tmp_path: Path, body: str) -> Session:
    """A session file in which an earlier harness placed notice 1 and answered it; reopened."""

    first = _harness(_Model(), await _session(tmp_path))
    first.post_notice(_notice(1, body=body))
    await first.prompt("one")
    return Session(await JsonlSessionStorage.open(LocalFileSystem(), str(tmp_path / "s.jsonl")))


async def test_a_notice_reposted_mid_run_that_the_runs_context_holds_is_shown_once(tmp_path: Path) -> None:
    """Codex #2's other half (verify SF3): an entry a boundary adopts is given to
    the run "unless the context it was built from held it". Here it did — an
    earlier process placed it, and this run's context was read from the file —
    so the re-post mid-run adds nothing to the request and starts no turn.

    With the run never recording what its context holds (verify R02b) the
    adopted entry is shown again: a second request carrying it twice.
    """

    session = await _placed_by_an_earlier_process(tmp_path, "ALREADY-PLACED")
    model = _Model()
    model.gate = asyncio.Event()
    harness = _harness(model, session)
    turn = asyncio.ensure_future(harness.prompt("two"))
    while not model.contexts:
        await asyncio.sleep(0)
    assert harness.post_notice(_notice(1, body="ALREADY-PLACED")), "this harness had not seen it"
    model.gate.set()
    await asyncio.wait_for(turn, 5)

    assert model.contexts == [["ALREADY-PLACED", "one", "ok", "two"]]
    assert harness.pending_notices == 0
    entries = await session.get_entries()
    assert sum(isinstance(e, CustomMessageEntry) for e in entries) == 1


async def test_an_adopted_notice_off_the_leaf_is_not_where_an_esc_returns(tmp_path: Path) -> None:
    """An adopted entry becomes the point a withdrawal returns to only when it is
    the leaf. Here it is far back (an earlier process placed it), re-posted from a
    tool next to a steer; an Esc before the steer is answered takes the steer back
    and leaves the rest of the path as it was.

    An adopted entry made ``safe_leaf`` wherever it is (verify R03) sends the
    withdrawal back to that old entry: everything after it is appended again with
    fresh ids, the user's line a second time.
    """

    from aelix_agent_core.types import AgentTool
    from aelix_ai.tools import ToolResult

    session = await _placed_by_an_earlier_process(tmp_path, "PLACED-EARLIER")
    released = asyncio.Event()
    model = _ToolThenWait(released)
    harness_box: list[AgentHarness] = []

    async def tool(_args: Any, _ctx: Any = None) -> ToolResult:
        harness_box[0].post_notice(_notice(1, body="PLACED-EARLIER"))
        await harness_box[0].steer("a steer the user typed")
        return ToolResult(content=[TextContent(text="tool done")])

    harness = AgentHarness(
        AgentHarnessOptions(
            stream_fn=model,
            session=session,
            tools=[AgentTool(name="t", execute=tool)],
            notice_inbox=True,
        )
    )
    harness_box.append(harness)
    turn = asyncio.ensure_future(harness.prompt("use the tool"))
    while len(model.contexts) < 2:
        await asyncio.sleep(0)
    assert model.contexts[1].count("PLACED-EARLIER") == 1
    assert model.contexts[1][-1] == "a steer the user typed"
    await harness.abort(withdraw_unanswered=True)
    released.set()
    await asyncio.wait_for(turn, 5)

    def lines(entries: list[Any]) -> list[str]:
        return [
            block.text
            for e in entries
            if isinstance(e, MessageEntry) and isinstance(e.message, UserMessage)
            for block in e.message.content
            if isinstance(block, TextContent)
        ]

    assert lines(await session.get_entries()).count("use the tool") == 1, "nothing was appended again"
    branch = lines(await session.get_branch())
    assert "use the tool" in branch and "a steer the user typed" not in branch


async def test_a_notice_id_this_harness_knows_is_not_posted_again() -> None:
    """``post_notice`` is ``False`` for an id already pending or placed here, and
    queues nothing. Without the check (verify V04) a session-less harness, which
    has no file to adopt from, shows the same notice twice."""

    model = _Model()
    harness = _harness(model, None)
    assert harness.post_notice(_notice(1)) is True
    assert harness.post_notice(_notice(1)) is False, "pending"
    assert harness.pending_notices == 1
    await harness.prompt("hi")
    assert harness.post_notice(_notice(1)) is False, "placed"
    assert harness.pending_notices == 0
    await harness.prompt("again")
    assert [t for c in model.contexts for t in c].count("[notice 1]") == 2, "one placement, in both requests"
    assert model.contexts[-1].count("[notice 1]") == 1


async def test_a_notice_is_not_claimed_once_an_abort_is_asked(tmp_path: Path) -> None:
    """Nothing is claimed once an abort is asked. An Esc that lands while the
    prompt's ``input`` hook runs ends the prompt before any request; the notice
    waiting ahead of the line stays pending, unplaced, for the next turn. A claim
    that ignored the abort (verify V05) placed it for a request never sent."""

    model = _Model()
    session = await _session(tmp_path)
    harness = _harness(model, session)

    async def esc_while_the_hook_runs(_event: Any, _ctx: Any) -> None:
        asyncio.ensure_future(harness.abort())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    harness.hooks.on("input", esc_while_the_hook_runs)
    harness.post_notice(_notice(1, body="WAITING"))
    await asyncio.wait_for(harness.prompt("hi"), 5)

    assert model.contexts == []
    assert harness.pending_notices == 1
    assert not any(isinstance(e, CustomMessageEntry) for e in await session.get_entries())


def test_a_cut_never_passes_its_byte_limit_at_a_multibyte_character() -> None:
    """``cut_utf8`` drops a character the limit splits. Replacing it with U+FFFD
    (verify R05) puts the cut up to 2 bytes over the limit the drain charged."""

    text = "a" + "é" * 3 + "한" * 3 + "😀" * 2
    size = len(text.encode("utf-8"))
    for limit in range(size + 2):
        cut = cut_utf8(text, limit)
        assert len(cut.encode("utf-8")) <= limit, (limit, cut)
        assert text.startswith(cut) and "\ufffd" not in cut
        assert len(text[: len(cut) + 1].encode("utf-8")) > limit or cut == text, "nothing more fits"
