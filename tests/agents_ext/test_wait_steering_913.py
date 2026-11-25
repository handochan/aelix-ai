"""#913: a pending parent steer releases wait at the usual tool-batch boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import contextmanager, suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions, TurnSeal
from aelix_agent_core.harness.inbox import is_notice_details
from aelix_agent_core.runtime.agent_session_runtime import create_agent_session_runtime
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_agent_core.types import AgentTool
from aelix_agents import jobs_client
from aelix_agents.jobs_client import DelegationJobs
from aelix_ai.messages import AssistantMessage, ImageContent, TextContent, ToolCallContent
from aelix_ai.streaming import AssistantEndEvent, AssistantMessageEvent, AssistantStartEvent, Model
from aelix_ai.tools import ToolResult
from aelix_coding_agent.jobs.facade import JobsFacade

from tests.agents_ext.test_agent_background_611 import (
    _FakeService,
    _rig,
    _script,
    _settled,
    _spawned_id,
)
from tests.agents_ext.test_agent_background_611 import (
    jobs_on as jobs_on,
)
from tests.agents_ext.test_stopped_child_reports_604 import _message_text, _tool_call
from tests.agents_ext.test_tool_and_security import _text
from tests.jobs.conftest import fast_service, make_harness, wait_for

pytestmark = pytest.mark.usefixtures("own_agent_dir")
_EARLY = "Wait ended early because input is pending."
_STEER = "unique parent steer"


def _spy(harness: AgentHarness, monkeypatch: pytest.MonkeyPatch) -> set[asyncio.Future[None]]:
    """Trace registration and cleanup, letting the harness do its usual work."""
    listening: set[asyncio.Future[None]] = set()
    watch = getattr(harness, "watch_steering", None)
    if watch is None:
        return listening

    @contextmanager
    def tracked() -> Any:
        with watch() as wake:
            listening.add(wake)
            try:
                yield wake
            finally:
                listening.remove(wake)

    monkeypatch.setattr(harness, "watch_steering", tracked)
    return listening


async def _join(task: asyncio.Task[Any]) -> Any:
    async with asyncio.timeout(1.5):
        return await task


async def _cancel(task: asyncio.Task[Any]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await _join(task)


@pytest.mark.usefixtures("jobs_on")
@pytest.mark.parametrize("mode", ["one-at-a-time", "all"])
@pytest.mark.parametrize("queue_first", [False, True], ids=["in-flight", "prequeued"])
async def test_wait_delivers_steers_once_before_the_child_notice(
    tmp_path: Path, mode: str, queue_first: bool
) -> None:
    gate = tmp_path / "release"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    seen: list[tuple[list[Any], str]] = []
    in_wait = asyncio.Event()
    sibling_started = asyncio.Event()
    sibling_release = asyncio.Event()
    wait_ended = asyncio.Event()
    lead = _tool_call({"action": "wait"})
    # Agent calls serialize the batch. Exercise the sibling on either side of
    # the wait and check the next provider request still follows both results.
    sibling_call = ToolCallContent(tool_call_id="sibling", tool_name="sibling", input={})
    lead.content.insert(1 if queue_first else 0, sibling_call)

    async def sibling(_args: Any, _ctx: Any) -> ToolResult:
        sibling_started.set()
        await sibling_release.wait()
        return ToolResult(content=[TextContent(text="sibling done")])

    async def stream(
        _model: Any, context: Any, _options: Any
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append((list(context.messages), context.system_prompt))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        reply = lead if len(seen) == 1 else AssistantMessage(content=[TextContent(text="ack")])
        yield AssistantEndEvent(message=reply)

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            system_prompt="stable parent prefix",
            session=rig.session,
            extensions=[rig.extension],
            runtime=rig.runtime,
            stream_fn=stream,
            tools=[AgentTool(name="sibling", execute=sibling)],
            notice_inbox=True,
        )
    )
    rig.ext.wait_steering = lambda: harness
    await rig.service.bind(harness)
    harness.set_steering_mode(mode)

    def event(event: Any) -> None:
        if getattr(event, "tool_call_id", None) == "call-1":
            if event.type == "tool_execution_start":
                in_wait.set()
            elif event.type == "tool_execution_end":
                wait_ended.set()

    harness.subscribe(event)
    image = ImageContent(data="AQ==", mime_type="image/png")

    async def submit() -> None:
        await harness.follow_up("explicit follow-up")
        await harness.steer(_STEER, images=[image])
        await harness.steer("second parent steer")

    turn = None
    follow_up = None
    try:
        _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "child work"}))
        if queue_first:
            # The initial loop boundary drains prequeued steers, so here queue
            # while the first provider request is already outstanding.
            async def prequeue_on_start(event: Any) -> None:
                if event.type == "tool_execution_start" and event.tool_call_id == "call-1":
                    await submit()

            harness.subscribe(prequeue_on_start)
        turn = asyncio.create_task(harness.prompt("trigger wait"))
        async with asyncio.timeout(4):
            if not queue_first:
                await sibling_started.wait()
                sibling_release.set()
            await in_wait.wait()
        assert not wait_ended.is_set() or queue_first
        if not queue_first:
            await harness.follow_up("explicit follow-up")
            # The sibling has already finished. Follow-up still cannot advance the parent.
            await asyncio.sleep(0)
            assert len(seen) == 1 and not wait_ended.is_set()
            await harness.steer(_STEER, images=[image])
            await harness.steer("second parent steer")
        async with asyncio.timeout(1.5):
            await wait_ended.wait()
        if queue_first:
            async with asyncio.timeout(1.5):
                await sibling_started.wait()
            assert len(seen) == 1, "sibling still holds the tool batch"
            sibling_release.set()
        await _join(turn)

        assert rig.jobs()[0].state == "running" and not gate.exists()
        next_messages = seen[1][0]
        first = [i for i, msg in enumerate(next_messages) if _message_text(msg) == _STEER]
        wait = next(msg for msg in next_messages if _EARLY in _message_text(msg))
        assert wait.role == "toolResult" and not wait.is_error
        assert len(first) == 1 and next_messages[first[0]].role == "user"
        assert image in next_messages[first[0]].content
        assert next_messages.index(wait) < first[0]
        assert (
            next(msg for msg in next_messages if _message_text(msg) == "sibling done").role
            == "toolResult"
        )
        assert any(_message_text(msg) == "second parent steer" for msg in next_messages) == (
            mode == "all"
        )
        assert all(_message_text(msg) != "explicit follow-up" for msg in next_messages)
        assert {prefix for _, prefix in seen} == {"stable parent prefix"}

        gate.touch()
        await _settled(rig)
        follow_up = asyncio.create_task(harness.prompt("place notice"))
        await _join(follow_up)
        stored = (await rig.session.build_context()).messages
        assert [
            _message_text(m)
            for m in stored
            if _message_text(m) in (_STEER, "second parent steer", "explicit follow-up")
        ] == [_STEER, "second parent steer", "explicit follow-up"]
        entries = await rig.session.get_entries()
        notices = [
            e for e in entries if e.type == "custom_message" and is_notice_details(e.details)
        ]
        assert len(notices) == 1 and "the background answer" in notices[0].content
        assert sum("the background answer" in _message_text(m) for m in seen[-1][0]) == 1
    finally:
        gate.touch()
        if turn is not None and not turn.done():
            await _cancel(turn)
        if follow_up is not None and not follow_up.done():
            await _cancel(follow_up)
        await rig.service.shutdown()
        await harness.dispose()


class _Changes(_FakeService):
    def __init__(self) -> None:
        super().__init__("running")
        self.attention = False
        self.watching = asyncio.Queue[asyncio.Future[None]]()

    def changed(self) -> asyncio.Future[None]:
        future = super().changed()
        self.watching.put_nowait(future)
        return future

    def snapshot(self, job_id: str) -> Any:
        return replace(super().snapshot(job_id), attention_waiting=self.attention)

    async def subscribed(self) -> asyncio.Future[None]:
        async with asyncio.timeout(1.5):
            return await self.watching.get()


async def _wait(service: _Changes, harness: AgentHarness, jobs: DelegationJobs) -> tuple[str, Any]:
    # The same test runs against the untouched base, which has no steering source.
    kwargs = {"steering": harness} if hasattr(harness, "watch_steering") else {}
    return await jobs_client.wait_for_agents(service, jobs, ["job"], requested=30, **kwargs)


async def test_withdrawn_or_other_session_input_cannot_release_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _Changes()
    harness, other = make_harness(None), make_harness(None)
    listening = _spy(harness, monkeypatch)
    wait = asyncio.create_task(_wait(service, harness, DelegationJobs()))
    try:
        changed = await service.subscribed()
        await other.steer("different session")
        await harness.follow_up("after the turn")
        await asyncio.sleep(0)
        assert not changed.done() and not wait.done()
        await harness.steer("withdrawn before recheck")
        steer, follow_up, next_turn = harness.take_queued_messages()
        assert [_message_text(m) for m in steer] == ["withdrawn before recheck"]
        harness.adopt_queued_messages([], follow_up, next_turn)
        assert (await service.subscribed()) is not changed
        assert changed.cancelled() and not wait.done()
        await harness.steer(_STEER)
        text, _ = await _join(wait)
        assert text.startswith(_EARLY) and _STEER not in text
        assert not listening and not harness._steering_waiters
        assert other.pending_message_count == 1
    finally:
        await _cancel(wait)


async def test_lifting_a_turn_seal_wakes_a_wait_with_a_queued_steer() -> None:
    service = _Changes()
    harness = make_harness(None)
    gate = TurnSeal("replacing session")
    jobs = DelegationJobs()
    waiting = asyncio.create_task(_wait(service, harness, jobs))
    try:
        first = await service.subscribed()
        harness.hold_turns(gate)
        await harness.steer(_STEER)
        assert (await service.subscribed()) is not first
        assert not waiting.done()
        harness.release_turns(gate, None)
        _, outcome = await _join(waiting)
        assert outcome == "steer" and not harness._steering_waiters
    finally:
        await _cancel(waiting)


@pytest.mark.usefixtures("jobs_on")
async def test_job_change_before_its_waiter_starts_is_observed() -> None:
    backing = fast_service()
    facade = JobsFacade(backing)
    loop = asyncio.get_running_loop()

    class SettlingJob(_FakeService):
        def snapshot(self, job_id: str) -> Any:
            if self.state == "running":
                loop.call_soon(self.settle)
            return super().snapshot(job_id)

        def settle(self) -> None:
            self.state = "unresolved"
            backing._touch()

        changed = staticmethod(facade.changed)

    result = jobs_client.wait_for_agents(
        SettlingJob("running"), DelegationJobs(), ["job"], requested=30
    )
    text, empty = await _join(asyncio.create_task(result))
    assert empty is False and "An unresolved agent" in text


@pytest.mark.parametrize("outcome", ["done", "stuck", "unresolved"])
async def test_existing_job_outcome_wins_a_simultaneous_steer(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    service = _Changes()
    harness = make_harness(None)
    listening = _spy(harness, monkeypatch)
    jobs = DelegationJobs()
    wait = asyncio.create_task(_wait(service, harness, jobs))
    try:
        await service.subscribed()
        await harness.steer(_STEER)
        if outcome == "stuck":
            service.attention = True
        else:
            service.state = outcome
        service.touch()
        if outcome == "done":
            posting = await service.subscribed()
            await asyncio.sleep(0)
            assert not wait.done() and not posting.done()
            service.notice_state = "pending"
            service.touch()
        text, empty = await _join(wait)
        assert not empty and _EARLY not in text
        assert {
            "done": "right after this call",
            "stuck": "marked stuck",
            "unresolved": "An unresolved agent",
        }[outcome] in text
        assert not listening and not getattr(harness, "_steering_waiters", ())

        # The still-pending steer is eligible on a new wait. A stuck notice
        # that already released a wait can never interrupt this one.
        service.state = "running"
        text, _ = await _join(asyncio.create_task(_wait(service, harness, jobs)))
        assert text.startswith(_EARLY)
    finally:
        await _cancel(wait)


@pytest.mark.usefixtures("jobs_on")
async def test_clamped_waits_and_steer_preserve_empty_streak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = tmp_path / "release"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    rig.ext.wait_steering = lambda: rig.harness
    listening = _spy(rig.harness, monkeypatch)
    monkeypatch.setattr(jobs_client, "WAIT_MIN_SECONDS", 0.06)
    monkeypatch.setattr(jobs_client, "WAIT_MAX_SECONDS", 30)
    try:
        _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "child work"}))
        first = await _join(asyncio.create_task(rig.call({"action": "wait", "timeout": 0.01})))
        assert "raised to 0 s" in _text(first) and "Still running" in _text(first)
        await rig.harness.steer(_STEER)
        second = await _join(asyncio.create_task(rig.call({"action": "wait", "timeout": 2000})))
        assert not second.is_error and _EARLY in _text(second)
        assert "cut to 30 s" in _text(second) and "Waited 30 s" not in _text(second)
        assert not listening and not rig.harness._steering_waiters
        rig.harness.take_queued_messages()
        third = await _join(asyncio.create_task(rig.call({"action": "wait", "timeout": 0.01})))
        assert "raised to 0 s" in _text(third) and not third.is_error
        fourth = await _join(asyncio.create_task(rig.call({"action": "wait"})))
        assert fourth.is_error and "Wait refused" in _text(fourth)
    finally:
        gate.touch()
        await rig.service.shutdown()


async def test_steer_during_subscription_and_deadline_after_a_stale_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _Changes()
    harness = make_harness(None)
    other = make_harness(None)
    await other.steer(_STEER)
    batch, _, _ = other.take_queued_messages()
    watch = getattr(harness, "watch_steering", None)
    if watch is not None:

        @contextmanager
        def enqueue_during_subscription() -> Any:
            with watch() as wake:
                harness.adopt_queued_messages(batch, [], [])
                yield wake

        monkeypatch.setattr(harness, "watch_steering", enqueue_during_subscription)
    text, _ = await _join(asyncio.create_task(_wait(service, harness, DelegationJobs())))
    assert text.startswith(_EARLY)
    monkeypatch.setattr(harness, "watch_steering", watch)
    harness.take_queued_messages()
    loop = asyncio.get_running_loop()
    clock = loop.time
    offset = 0.0
    monkeypatch.setattr(loop, "time", lambda: clock() + offset)
    try:
        waiting = asyncio.create_task(_wait(service, harness, DelegationJobs()))
        first = await service.subscribed()
        await harness.steer("stale at the deadline")
        harness.take_queued_messages()
        offset = 30.0
        text, empty = await _join(waiting)
        assert empty is True and _EARLY not in text and first.cancelled()
        assert service.watching.empty()
    finally:
        offset = 0


async def test_a_pending_steer_wins_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _Changes()
    harness = make_harness(None)
    loop = asyncio.get_running_loop()
    clock = loop.time
    offset = 0.0
    monkeypatch.setattr(loop, "time", lambda: clock() + offset)
    waiting = asyncio.create_task(_wait(service, harness, DelegationJobs()))
    try:
        await service.subscribed()
        offset = 30.0
        await harness.steer(_STEER)
        _, outcome = await _join(waiting)
        assert outcome == "steer"
        assert harness.pending_message_count == 1
    finally:
        offset = 0
        await _cancel(waiting)


async def test_extension_steering_gets_the_same_content_free_wait_result() -> None:
    service = _Changes()
    harness = make_harness(None)
    waiting = asyncio.create_task(_wait(service, harness, DelegationJobs()))
    try:
        await service.subscribed()
        harness._action_send_message(_STEER, deliver_as="steer")
        text, outcome = await _join(waiting)
        assert outcome == "steer"
        assert text.splitlines()[0] == _EARLY and _STEER not in text
        assert [_message_text(m) for m in harness.take_queued_messages()[0]] == [_STEER]
    finally:
        await _cancel(waiting)


async def test_an_extension_abort_takes_precedence_over_pending_steering() -> None:
    service = _Changes()
    harness = make_harness(None)
    waiting = asyncio.create_task(_wait(service, harness, DelegationJobs()))
    try:
        first = await service.subscribed()
        harness._mark_abort()
        await harness.steer(_STEER)
        assert harness.pending_message_count == 1
        assert not harness.has_pending_steering()
        assert (await service.subscribed()) is not first
        assert not waiting.done()
    finally:
        await _cancel(waiting)


@pytest.mark.usefixtures("jobs_on")
@pytest.mark.parametrize("interruption", ["esc", "new-session"])
async def test_interruption_releases_listener_and_keeps_steer_out_of_next_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    gate = tmp_path / "release"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    seen: list[list[Any]] = []
    in_wait = asyncio.Event()
    withdrawn: list[str] = []

    async def stream(
        _model: Any, context: Any, _options: Any
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(list(context.messages))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        reply = (
            _tool_call({"action": "wait"})
            if len(seen) == 1
            else AssistantMessage(content=[TextContent(text="ack")])
        )
        yield AssistantEndEvent(message=reply)

    def build(session: Any, *, extensions: bool) -> AgentHarness:
        return AgentHarness(
            AgentHarnessOptions(
                model=Model(id="mock", provider="mock"),
                session=session,
                stream_fn=stream,
                extensions=[rig.extension] if extensions else [],
                runtime=rig.runtime if extensions else None,
                notice_inbox=True,
            )
        )

    harness = build(rig.session, extensions=True)
    listening = _spy(harness, monkeypatch)
    await rig.service.bind(harness)
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "s"))

    async def factory(session: Any, **_kwargs: Any) -> AgentHarness:
        return build(session, extensions=False)

    runtime = await create_agent_session_runtime(harness, factory, repo=repo, fs=fs)
    rig.ext.wait_steering = lambda: runtime.harness

    def started(event: Any) -> None:
        if event.type == "tool_execution_start":
            in_wait.set()
        if event.type == "input_withdrawn":
            withdrawn.extend(_message_text(msg) for msg in event.messages)

    harness.subscribe(started)
    turn = None
    try:
        _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "child work"}))
        turn = asyncio.create_task(harness.prompt("trigger wait"))
        async with asyncio.timeout(4):
            await in_wait.wait()
        await wait_for(lambda: bool(listening), timeout=1.5)
        await harness.steer(_STEER)
        if interruption == "esc":
            await harness.abort(withdraw_unanswered=True)
        else:
            await runtime.new_session()
        await _join(turn)
        assert not listening and not harness._steering_waiters
        assert len(seen) == 1
        await _join(asyncio.create_task(runtime.harness.prompt("next request")))
        assert all(
            _STEER not in _message_text(msg) and _EARLY not in _message_text(msg)
            for msg in seen[-1]
        )
        old = (await rig.session.build_context()).messages
        assert all(
            _STEER not in _message_text(msg) and _EARLY not in _message_text(msg) for msg in old
        )
        assert rig.jobs()[0].state == "running"
        cancelled = [m for m in old if m.role == "toolResult" and m.tool_call_id == "call-1"]
        assert len(cancelled) == 1 and cancelled[0].is_error
        assert _message_text(cancelled[0]).startswith("Cancelled:")
        assert withdrawn.count(_STEER) == 1
    finally:
        gate.touch()
        if turn is not None and not turn.done():
            await _cancel(turn)
        await rig.service.shutdown()
        await runtime.dispose()
