"""#800 (ADR-0307 D3): a session change or a quit seals the outgoing harness and settles its turn first.

pi's ``teardownCurrent`` and ``dispose`` begin with ``await this.session.abort()`` "so the
aborted turn (including tool results) is persisted to the outgoing session before it is
replaced". ``AgentSessionRuntime._seal_and_settle`` is that step 0, for every reason but
``reload``: the outgoing harness's turns are held, its turn is aborted and settled, waited
for at most ``SWAP_SETTLE_SECONDS``, and then ``session_shutdown``, invalidate and
``harness.dispose()`` run as before. A switch to the outgoing session's own file opens it
again after the teardown, so the installed session continues from what the teardown wrote.

The shapes are ``.omc/probes/612/design-r5/swap/swap_probe.py`` and
``giveback/giveback_probe.py``: the real ``agent`` tool and agents extension, a fake child
channel, a scripted parent model, and a real ``AgentSessionRuntime`` on JSONL sessions.

Contract r5 §12.3, L1. Fail on 6dd682cd: test 5 (Esc then ``/new`` hands the report back as
withdrawn input), test 6 (a switch to the current file: the installed session lacks the
report and a later append takes the old turn's tail off the branch), test 7 (``/new``
during a delegation: the old harness asks its model again and the outgoing file has no
report), test 8 (an ``abort`` hook that parks holds ``/new``), test 9 (between the settle
and the teardown the outgoing harness answers ``busy``, not ``held``). Guards (pass on
both): a vetoed ``/new`` neither seals nor aborts; ``reload`` waits for idle and cancels
nothing.

The "fix round 1" section is Codex's review C-1 to C-4 and verify round 1's F3: each test
there names how it fails on the previous head ``deb7cdd7`` (the first five), or is a guard
that kills a mutant of the round's lines (ADR-0307, How it is held).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness import core as harness_core
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    TurnSeal,
)
from aelix_agent_core.harness.hooks import SessionBeforeSwitchResult
from aelix_agent_core.runtime import agent_session_runtime as asr
from aelix_agent_core.runtime.agent_session_runtime import create_agent_session_runtime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionStorage,
    LocalFileSystem,
    Session,
    SessionWriterLock,
)
from aelix_agents import runtime as agents_runtime
from aelix_agents.aggregate import CANCEL_REPORT_HEAD
from aelix_agents.stream import reduce_event
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, Model
from aelix_coding_agent.subagent_contract import SubagentResult

from tests.agents_ext.test_child_session_records import _parent
from tests.agents_ext.test_stopped_child_reports_604 import (
    _NO_PROCESS,
    _message,
    _message_text,
    _Parking,
    _scripted,
    _tool_call,
)
from tests.agents_ext.test_tool_and_security import _bench, _FakeUI, _write_profile
from tests.runtime.test_session_lock_handoff import _answer, _repo, _runtime_on

MODEL = Model(id="mock", provider="mock")


def _reports(messages: list[Any]) -> list[str]:
    return [m.role for m in messages if CANCEL_REPORT_HEAD in _message_text(m)]


async def _reopened(path: Path) -> list[Any]:
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))
    return list((await reopened.build_context()).messages)


class _StoppableChannel:
    """A child that says it found the bug and runs until it is stopped.

    A stop through ``abort_child`` (the ``session_shutdown`` door, which is how the
    base ended a delegation on a swap) makes it return an ordinary aborted
    envelope; a cancel of the turn unwinds it as an Esc does.
    """

    def __init__(self) -> None:
        self.arrived = asyncio.Event()
        self.stopped = asyncio.Event()

    async def run(self, plan: Any, *, child: Any, on_stream: Any = None) -> SubagentResult:
        child.proc = _NO_PROCESS
        child.state = "running"
        for event in ({"type": "agent_start"}, _message("CHILD FOUND THE BUG")):
            reduce_event(child.stream, event)
        self.arrived.set()
        await self.stopped.wait()
        child.state = "stopped"
        return SubagentResult(
            id=child.id,
            profile=child.profile,
            status="aborted",
            ok=False,
            summary="work done",
            reason="stopped",
            started=True,
        )


async def _delegating_host(
    tmp_path: Path, channel: Any, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, AgentHarness, list[list[Any]], list[Any], Path, asyncio.Task[Any]]:
    """A runtime host whose harness is running a delegation to ``channel``."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, channel=channel)
    session, repo, path = await _parent(tmp_path, cwd=bench.cwd)
    host_box: list[Any] = []
    # ``cli/entry.py``'s ``_live_session_of``: the runtime host's session now.
    bench.ext.session = lambda: host_box[0].session if host_box else session
    bench.ext.background_host = lambda: False
    seen: list[list[Any]] = []
    events: list[Any] = []
    harness = AgentHarness(
        AgentHarnessOptions(
            model=MODEL,
            session=session,
            cwd=str(bench.cwd),
            extensions=[bench.extension],
            runtime=bench.runtime,
            stream_fn=_scripted(_tool_call({"profile": "scout", "task": "look"}), seen),
        )
    )
    harness.subscribe(events.append)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session, cwd=str(bench.cwd)))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    host_box.append(host)

    async def stop(child: Any) -> None:
        child.stopped = True
        if isinstance(channel, _StoppableChannel):
            channel.stopped.set()
        await asyncio.sleep(0.05)

    monkeypatch.setattr(agents_runtime, "abort_child", stop)
    prompt = asyncio.ensure_future(harness.prompt("delegate"))
    await asyncio.wait_for(channel.arrived.wait(), 10)
    return host, harness, seen, events, path, prompt


async def _finish(*tasks: asyncio.Future[Any]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# === tests that fail on the base ==================================================


async def test_esc_then_new_gives_no_report_back_as_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract r5 §12.3 test 5. The report no longer sits on the next-turn queue, so a
    swap after an Esc hands nothing back (ADR-0286's ``input_withdrawn``; the TUI
    puts those lines in the input box). On 6dd682cd one ``input_withdrawn`` event
    carries ``[aelix system report — not user input] …``."""

    host, harness, _seen, events, _path, prompt = await _delegating_host(
        tmp_path, _Parking(), monkeypatch
    )
    try:
        await harness.abort(withdraw_unanswered=True)
        await asyncio.wait_for(prompt, 10)
        before = len(events)
        await asyncio.wait_for(host.new_session(), 20)
        withdrawn = [
            _message_text(m)
            for e in events[before:]
            if getattr(e, "type", "") == "input_withdrawn"
            for m in e.messages
        ]
        assert [t for t in withdrawn if CANCEL_REPORT_HEAD in t] == []
    finally:
        await _finish(prompt)
        await host.dispose()


async def test_a_switch_to_the_current_file_keeps_the_report_on_the_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract r5 §12.3 test 6. ``switch_session`` opens its target before the
    teardown; when that is the outgoing file it is opened again after it, so the
    installed session holds the cancelled call's result and an append through it
    keeps the old turn's tail on the branch. On 6dd682cd the installed session has no
    report and the reopened branch after an append is ``user, assistant, user``."""

    channel = _StoppableChannel()
    host, _harness, seen, _events, path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    try:
        await asyncio.wait_for(host.switch_session(str(path)), 20)
        await asyncio.wait_for(prompt, 20)
        installed = list((await host.session.build_context()).messages)
        await host.session.append_message(UserMessage(content=[TextContent(text="after")]))
        branch = await _reopened(path)
        assert [m.role for m in branch] == ["user", "assistant", "toolResult", "user"]
        assert _reports(branch) == ["toolResult"]
        assert _reports(installed) == ["toolResult"]
        assert len(seen) == 1, "the old harness asked its model again"
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


async def test_new_during_a_delegation_settles_the_turn_into_the_outgoing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract r5 §12.3 test 7. The turn is aborted before ``session_shutdown``, so the
    ``agent`` call ends cancelled with the report as its result, in the outgoing
    session's file, and the old harness makes no second request. On 6dd682cd the
    shutdown handler stops the child, the call returns its ordinary envelope, the old
    harness asks its model again (two requests) and the file holds no report."""

    channel = _StoppableChannel()
    host, _harness, seen, _events, path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    try:
        await asyncio.wait_for(host.new_session(), 20)
        await asyncio.wait_for(prompt, 20)
        assert _reports(await _reopened(path)) == ["toolResult"]
        assert len(seen) == 1, "the old harness asked its model again"
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


async def test_an_abort_hook_that_parks_does_not_hold_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract r5 §12.3 test 8. The settle is waited for at most
    ``SWAP_SETTLE_SECONDS`` and left running past it; its first abort cancelled the
    turn task before the hook parked, so ``dispose()`` finds the harness idle. With
    the bound shortened to 0.5 s the swap returns within 1.5 s. On 6dd682cd
    ``dispose()`` awaits the parked hook inside its own abort: still waiting."""

    monkeypatch.setattr(asr, "SWAP_SETTLE_SECONDS", 0.5, raising=False)
    monkeypatch.setattr(harness_core, "_REABORT_GRACE_SECONDS", 0.1)
    session, repo, _path = await _parent(tmp_path)
    gate, release = asyncio.Event(), asyncio.Event()

    async def silent_model(*_args: Any) -> Any:
        await gate.wait()
        if False:  # pragma: no cover - an async generator that never yields
            yield None

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=silent_model))

    async def parked_abort(*_args: Any) -> None:
        await release.wait()

    harness.hooks.on("abort", parked_abort)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    prompt = asyncio.ensure_future(harness.prompt("work"))
    await asyncio.sleep(0.1)
    assert harness.phase == "turn"
    swap = asyncio.ensure_future(host.new_session())
    try:
        done, _ = await asyncio.wait({swap}, timeout=1.5)
        assert swap in done, "the swap is still waiting on the parked abort hook"
        swap.result()
        assert host.harness is not harness
    finally:
        release.set()
        gate.set()
        await asyncio.sleep(0)
        await _finish(swap, prompt)
        await asyncio.wait_for(asyncio.gather(*asr.__dict__.get("_SETTLES", ()), return_exceptions=True), 5)
        await host.dispose()


async def test_the_outgoing_harness_is_held_once_its_turn_settled(
    tmp_path: Path,
) -> None:
    """Contract r5 §12.3 test 9. From step 0 on, no prompt and no notice turn starts on
    the outgoing harness: a ``session_shutdown`` handler (it runs after the settle)
    finds ``prompt`` refused with the seal's reason and a notice turn ``held``. On
    6dd682cd the turn is still running there, so both answer ``busy``."""

    session, repo, _path = await _parent(tmp_path)
    gate = asyncio.Event()

    async def silent_model(*_args: Any) -> Any:
        await gate.wait()
        if False:  # pragma: no cover - an async generator that never yields
            yield None

    harness = AgentHarness(
        AgentHarnessOptions(model=MODEL, session=session, stream_fn=silent_model, notice_inbox=True)
    )
    seen: dict[str, Any] = {}

    async def on_shutdown(_event: Any, _ctx: Any) -> None:
        try:
            await harness.prompt("a turn the teardown would only abort")
        except AgentHarnessError as exc:
            seen["prompt"] = (exc.code, str(exc))
        seen["notices"] = (await harness.prompt_notices()).reason
        seen["phase"] = harness.phase

    harness.hooks.on("session_shutdown", on_shutdown)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    prompt = asyncio.ensure_future(harness.prompt("work"))
    await asyncio.sleep(0.1)
    assert harness.phase == "turn"
    try:
        await asyncio.wait_for(host.new_session(), 20)
        assert (seen["notices"], seen["prompt"][0]) == ("held", "invalid_state")
        assert "the session is being replaced" in seen["prompt"][1]
        assert seen["phase"] == "idle"
    finally:
        gate.set()
        await _finish(prompt)
        await host.dispose()


async def test_a_swap_cancelled_while_it_settles_lifts_the_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A swap interrupted in step 0 did not happen: the harness it leaves installed
    is not held, and the next prompt runs on it. (On 6dd682cd there is no seal.)"""

    monkeypatch.setattr(asr, "SWAP_SETTLE_SECONDS", 30.0, raising=False)
    session, repo, _path = await _parent(tmp_path)
    gate, release = asyncio.Event(), asyncio.Event()

    async def model(*_args: Any) -> Any:
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=model))

    async def parked_abort(*_args: Any) -> None:
        await release.wait()

    harness.hooks.on("abort", parked_abort)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    prompt = asyncio.ensure_future(harness.prompt("work"))
    await asyncio.sleep(0.1)
    swap = asyncio.ensure_future(host.new_session())
    try:
        await asyncio.sleep(0.3)
        assert not swap.done()
        swap.cancel()
        await asyncio.gather(swap, return_exceptions=True)
        release.set()
        gate.set()
        await asyncio.wait_for(asyncio.gather(prompt, return_exceptions=True), 10)
        await asyncio.wait_for(asyncio.gather(*asr.__dict__.get("_SETTLES", ()), return_exceptions=True), 10)
        assert host.harness is harness
        await asyncio.wait_for(harness.prompt("the next prompt"), 10)
        assert harness.state.messages[-1].content[0].text == "done"
    finally:
        release.set()
        gate.set()
        await _finish(swap, prompt)
        await host.dispose()


# === fix round 1 (Codex review C-1 to C-4): fail on deb7cdd7 =======================


@pytest.mark.parametrize("choice", ["read_only", "fork"])
async def test_a_switch_to_a_file_another_writer_holds_keeps_the_answer(
    tmp_path: Path, choice: str
) -> None:
    """Codex C-1. On a read-only view of a file another writer holds, a switch to that
    same file asks again; the answer stands. "Read only" cannot append to it, and "fork
    and continue" writes its own file under its own lock; the original is unchanged.
    On deb7cdd7 the switch opened the original again, writable, and both answers
    appended to it under the other writer's lock (the fork's runtime held the fork's
    lock while writing the original)."""

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    await theirs.append_message(UserMessage(content=[TextContent(text="the owner's line")]))
    path = theirs.session_file
    assert path is not None
    owner = SessionWriterLock(path)
    assert owner.try_acquire() is True
    host, _lock = await _runtime_on(repo, fs, mine)
    try:
        host.set_on_session_contended(lambda _p: _answer("read_only"))
        await host.switch_session(path)
        host.set_on_session_contended(lambda _p: _answer(choice))
        await host.switch_session(path)
        before = Path(path).read_bytes()
        line = UserMessage(content=[TextContent(text="probe")])
        if choice == "read_only":
            with pytest.raises(Exception, match="read-only|read only|readonly"):
                await host.session.append_message(line)
            assert host.writer_lock is None
        else:
            live = host.session.session_file
            assert live is not None and live != path
            assert host.writer_lock is not None and host.writer_lock.session_path == live
            await host.session.append_message(line)
        assert Path(path).read_bytes() == before, "the other writer's file was appended to"
    finally:
        await host.dispose()
        owner.release()


async def test_a_swap_cancelled_in_a_session_shutdown_handler_leaves_a_working_harness(
    tmp_path: Path,
) -> None:
    """Codex C-2, first shape. A ``new_session`` cancelled inside an awaited
    ``session_shutdown`` handler (after step 0 settled the turn) did not happen: the
    outgoing harness stays installed and undisposed, and the next prompt runs on it.
    On deb7cdd7 every later prompt failed with "the session is being replaced"."""

    session, repo, _path = await _parent(tmp_path)
    called, entered, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def model(*_args: Any) -> Any:
        called.set()
        await finish.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="completed")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=model))

    async def park(event: Any, _ctx: Any) -> None:
        if event.reason == "new":
            entered.set()
            await asyncio.Event().wait()

    harness.hooks.on("session_shutdown", park)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    turn = asyncio.ensure_future(harness.prompt("first"))
    swap: asyncio.Future[Any] | None = None
    try:
        await asyncio.wait_for(called.wait(), 10)
        swap = asyncio.ensure_future(host.new_session())
        await asyncio.wait_for(entered.wait(), 10)
        swap.cancel()
        await asyncio.gather(swap, return_exceptions=True)
        finish.set()
        await asyncio.wait_for(turn, 10)
        assert host.harness is harness and not harness.disposed
        assert harness.turns_held is None
        await asyncio.wait_for(harness.prompt("second"), 10)
        assert _message_text(harness.state.messages[-1]) == "completed"
    finally:
        finish.set()
        await _finish(turn, *([swap] if swap is not None else []))
        await host.dispose()


async def test_a_swap_cancelled_while_the_first_abort_hook_parks_cannot_abort_the_next_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex C-2, second shape. A swap cancelled while the settle's first ``abort`` hook
    parks stops and joins that settle before it lifts the seal, so releasing the hook
    later re-aborts nothing: the next prompt runs to its answer. On deb7cdd7 the old
    settle went on after the cancel, and its re-abort ended the next prompt while the
    model was still parked."""

    monkeypatch.setattr(harness_core, "_REABORT_GRACE_SECONDS", 0.05)
    session, repo, _path = await _parent(tmp_path)
    first_live, second_live, first_abort, release_abort, finish = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    requests = 0
    aborts = 0

    async def model(*_args: Any) -> Any:
        nonlocal requests
        requests += 1
        (first_live if requests == 1 else second_live).set()
        await finish.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="completed")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=model))

    async def pause_first_abort(*_args: Any) -> None:
        nonlocal aborts
        aborts += 1
        if aborts == 1:
            first_abort.set()
            await release_abort.wait()

    harness.hooks.on("abort", pause_first_abort)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    first = asyncio.ensure_future(harness.prompt("first"))
    await asyncio.wait_for(first_live.wait(), 10)
    swap = asyncio.ensure_future(host.new_session())
    second: asyncio.Future[Any] | None = None
    try:
        await asyncio.wait_for(first_abort.wait(), 10)
        swap.cancel()
        await asyncio.gather(swap, return_exceptions=True)
        await asyncio.wait_for(first, 10)
        assert harness.turns_held is None
        second = asyncio.ensure_future(harness.prompt("second"))
        await asyncio.wait_for(second_live.wait(), 10)
        release_abort.set()
        done, _ = await asyncio.wait({second}, timeout=0.5)
        assert second not in done, "an old settle aborted the next prompt"
        finish.set()
        await asyncio.wait_for(second, 10)
        assert _message_text(harness.state.messages[-1]) == "completed"
        assert aborts == 1
    finally:
        finish.set()
        release_abort.set()
        await _finish(first, swap, *([second] if second is not None else []))
        await asyncio.wait_for(asyncio.gather(*asr._SETTLES, return_exceptions=True), 10)
        await host.dispose()


async def test_a_shutdown_handlers_triggered_send_is_queued_not_started(
    tmp_path: Path,
) -> None:
    """Codex C-3. After step 0 settled the turn, a ``session_shutdown`` handler's
    ``send_message(trigger_turn=True)`` on the sealed harness is queued, as it was while
    that turn ran (the base), and handed back after the teardown: no prompt is claimed
    and no ``before_agent_start`` handler runs, so one that parks cannot hold the swap.
    On deb7cdd7 it ran a refused turn whose ``before_agent_start`` parked the swap."""

    session, repo, _path = await _parent(tmp_path)
    running, started, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def model(*_args: Any) -> Any:
        running.set()
        await finish.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="completed")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=model))
    events: list[Any] = []
    harness.subscribe(events.append)

    async def startup(event: Any, _ctx: Any) -> None:
        if event.prompt == "extension shutdown message":
            started.set()
            await finish.wait()

    async def shutdown(event: Any, _ctx: Any) -> None:
        if event.reason == "new":
            harness._action_send_message("extension shutdown message", trigger_turn=True)
            await asyncio.sleep(0)

    harness.hooks.on("before_agent_start", startup)
    harness.hooks.on("session_shutdown", shutdown)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    turn = asyncio.ensure_future(harness.prompt("work"))
    swap: asyncio.Future[Any] | None = None
    try:
        await asyncio.wait_for(running.wait(), 10)
        swap = asyncio.ensure_future(host.new_session())
        done, _ = await asyncio.wait({swap}, timeout=3)
        assert swap in done, "the swap waits on a refused turn's before_agent_start"
        swap.result()
        assert not started.is_set(), "a turn was claimed on the sealed harness"
        withdrawn = [
            _message_text(m)
            for e in events
            if getattr(e, "type", "") == "input_withdrawn"
            for m in e.messages
        ]
        assert withdrawn == ["extension shutdown message"]
    finally:
        finish.set()
        await _finish(turn, *([swap] if swap is not None else []))
        await host.dispose()


async def test_an_import_of_the_current_file_keeps_the_report_on_the_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex C-4. ``import_from_jsonl`` of the file the session is on (no copy made)
    opens it again after the teardown, as ``switch_session`` does, so the installed
    session holds the cancelled call's result and an append keeps it on the branch. On
    deb7cdd7 the report was on disk but not in the installed session, and off the
    branch after one append."""

    channel = _StoppableChannel()
    host, outgoing, _seen, _events, path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    try:
        assert str(path) == outgoing.session.session_file
        await asyncio.wait_for(host.import_from_jsonl(str(path)), 20)
        await asyncio.wait_for(prompt, 20)
        installed = list((await host.session.build_context()).messages)
        await host.session.append_message(UserMessage(content=[TextContent(text="after")]))
        assert _reports(installed) == ["toolResult"]
        assert _reports(await _reopened(path)) == ["toolResult"]
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
async def test_a_switch_to_another_spelling_of_the_current_file_keeps_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify F3 (mutant R9: the file compared without resolving). A switch to a
    symlink of the outgoing session's file is a switch to that file: it is opened
    again after the teardown, so the installed session holds the report (the live RPC
    case was ``/tmp`` against ``/private/tmp``). Not on Windows, as its siblings
    (verify round 2 F5)."""

    channel = _StoppableChannel()
    host, _harness, _seen, _events, path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(path)
    try:
        await asyncio.wait_for(host.switch_session(str(alias)), 20)
        await asyncio.wait_for(prompt, 20)
        assert _reports(list((await host.session.build_context()).messages)) == ["toolResult"]
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


async def test_a_switch_from_a_session_with_no_file_opens_its_target(tmp_path: Path) -> None:
    """Verify F3 (mutant R12: ``or`` for ``and`` in the same-file guard). The outgoing
    session has no file (in-memory storage), so the target is not its file and the
    switch opens it once, as before #800."""

    from aelix_agent_core.session.memory_storage import MemorySessionStorage

    target, repo, path = await _parent(tmp_path)
    await target.append_message(UserMessage(content=[TextContent(text="on disk")]))
    harness = AgentHarness(
        AgentHarnessOptions(model=MODEL, session=Session(MemorySessionStorage()))
    )

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    try:
        await asyncio.wait_for(host.switch_session(str(path)), 20)
        assert host.session.session_file == str(path)
        texts = [_message_text(m) for m in (await host.session.build_context()).messages]
        assert texts == ["on disk"]
    finally:
        await host.dispose()


async def test_an_interrupted_swap_puts_back_the_gate_the_harness_had(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify F3 (mutant R10: the gate before the seal read as ``None``). A turn runs
    on a harness whose turns are held (the CLI holds them while it decides a route);
    a swap cancelled in step 0 puts that hold back, not an open gate."""

    monkeypatch.setattr(asr, "SWAP_SETTLE_SECONDS", 30.0, raising=False)
    session, repo, _path = await _parent(tmp_path)
    gate, release = asyncio.Event(), asyncio.Event()

    async def model(*_args: Any) -> Any:
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=model))

    async def parked_abort(*_args: Any) -> None:
        await release.wait()

    harness.hooks.on("abort", parked_abort)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    prompt = asyncio.ensure_future(harness.prompt("work"))
    await asyncio.sleep(0.1)
    harness.hold_turns("held by the launch")
    swap = asyncio.ensure_future(host.new_session())
    try:
        await asyncio.sleep(0.3)
        assert harness.turns_held == "the session is being replaced"
        swap.cancel()
        await asyncio.gather(swap, return_exceptions=True)
        assert harness.turns_held == "held by the launch"
        assert not isinstance(harness.turns_held, TurnSeal)
    finally:
        release.set()
        gate.set()
        await _finish(swap, prompt)
        await host.dispose()


async def test_a_quit_cancelled_in_a_session_shutdown_handler_leaves_a_working_harness(
    tmp_path: Path,
) -> None:
    """``dispose`` undoes step 0 too when it is interrupted before the invalidation:
    the harness still takes prompts (Codex C-2's shape, on the quit path)."""

    session, repo, _path = await _parent(tmp_path)
    called, entered, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def model(*_args: Any) -> Any:
        called.set()
        await finish.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="completed")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=model))
    parked = asyncio.Event()

    async def park(event: Any, _ctx: Any) -> None:
        if event.reason == "quit" and not parked.is_set():
            parked.set()
            entered.set()
            await asyncio.Event().wait()

    harness.hooks.on("session_shutdown", park)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    turn = asyncio.ensure_future(harness.prompt("first"))
    quit_: asyncio.Future[Any] | None = None
    try:
        await asyncio.wait_for(called.wait(), 10)
        quit_ = asyncio.ensure_future(host.dispose())
        await asyncio.wait_for(entered.wait(), 10)
        quit_.cancel()
        await asyncio.gather(quit_, return_exceptions=True)
        finish.set()
        await asyncio.wait_for(turn, 10)
        assert harness.turns_held is None and not harness.disposed
        await asyncio.wait_for(harness.prompt("second"), 10)
        assert _message_text(harness.state.messages[-1]) == "completed"
    finally:
        finish.set()
        await _finish(turn, *([quit_] if quit_ is not None else []))
        await host.dispose()


@pytest.mark.parametrize("ui", [False, True])
async def test_a_failed_reopen_goes_on_with_the_first_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    ui: bool,
) -> None:
    """A same-file switch whose second open fails is told and goes on with the target
    it opened before the teardown, as before #800: the swap is not undone. Verify
    round 2 F3: told at DEBUG with the exception, and as one stderr line only with no
    UI (``ui``: one is bound to the outgoing harness), never as a WARNING, which with
    no handler reaches ``logging.lastResort`` and paints over a live TUI. On 9acd9ea0
    it was a WARNING and no stderr line."""

    channel = _StoppableChannel()
    host, _harness, _seen, _events, path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    if ui:
        _harness.runtime.bind_ui(_FakeUI())
    caplog.set_level(logging.DEBUG, logger=asr.__name__)
    opened = 0
    real_open = host._repo.open

    async def second_fails(*args: Any, **kwargs: Any) -> Any:
        nonlocal opened
        opened += 1
        if opened == 2:
            raise OSError("the second open failed")
        return await real_open(*args, **kwargs)

    monkeypatch.setattr(host._repo, "open", second_fails)
    try:
        result = await asyncio.wait_for(host.switch_session(str(path)), 20)
        await asyncio.wait_for(prompt, 20)
        assert result.cancelled is False and opened == 2
        assert host.session.session_file == str(path)
        assert host.harness is not _harness
        (record,) = [r for r in caplog.records if "opening the session file again" in r.getMessage()]
        assert record.levelno == logging.DEBUG
        assert record.exc_info
        exc_type, exc, traceback = record.exc_info
        assert exc_type is OSError
        assert str(exc) == "the second open failed"
        assert traceback is not None
        stderr = [line for line in capsys.readouterr().err.splitlines() if line]
        assert stderr == ([] if ui else [record.getMessage()])
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


async def test_a_stop_after_the_seal_refuses_a_triggered_send_again() -> None:
    """``stop`` (a failed teardown, #663) ends the seal's queueing: a ``trigger_turn``
    send to a stopped harness is the refused turn it always was, not a queued line;
    under the seal itself it is queued."""

    harness = AgentHarness(AgentHarnessOptions(model=MODEL))
    harness.hold_turns(TurnSeal("the session is being replaced"))
    harness._action_send_message("queued under the seal", trigger_turn=True)
    assert [_message_text(m) for m in harness._next_turn_queue] == ["queued under the seal"]
    harness._next_turn_queue.clear()
    harness.stop("stopped")
    harness._action_send_message("refused once stopped", trigger_turn=True)
    await asyncio.sleep(0)
    await asyncio.wait_for(harness.wait_for_idle(), 10)
    assert harness._next_turn_queue == []


# === fix round 2 (verify round 2 F2, F3, F4, F5): fail on 9acd9ea0 unless a guard ==


async def _swallowing_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, AgentHarness, Any, dict[str, Any]]:
    """A runtime host whose harness's first ``abort`` hook swallows its own cancel and
    parks until ``state["release"]`` is set; ``model`` parks every request on
    ``state["finish"]`` and answers "completed". The settle bound is 0.3 s."""

    monkeypatch.setattr(harness_core, "_REABORT_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(asr, "SWAP_SETTLE_SECONDS", 0.3)
    session, repo, _path = await _parent(tmp_path)
    state: dict[str, Any] = {
        "live": [asyncio.Event() for _ in range(4)],
        "finish": asyncio.Event(),
        "first_abort": asyncio.Event(),
        "release": asyncio.Event(),
        "requests": 0,
        "aborts": 0,
    }

    async def model(*_args: Any) -> Any:
        state["requests"] += 1
        state["live"][state["requests"] - 1].set()
        await state["finish"].wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="completed")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=model))

    async def swallowing_first_abort(*_args: Any) -> None:
        state["aborts"] += 1
        if state["aborts"] == 1:
            state["first_abort"].set()
            while not state["release"].is_set():
                try:
                    await state["release"].wait()
                except asyncio.CancelledError:
                    continue

    harness.hooks.on("abort", swallowing_first_abort)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    return host, harness, model, state


@pytest.mark.parametrize("kind", ["new", "quit"])
async def test_an_abort_hook_that_swallows_the_cancel_keeps_the_seal_until_the_settle_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Verify round 2 F2. A swap or quit cancelled while the settle's first ``abort``
    hook parks, and that hook swallows the settle's cancel, so the settle outlives the
    bounded join. The seal stays while it lives (a prompt is refused with its reason)
    and the gate goes back when it ends: the next prompt then runs to its answer, and
    nothing aborts it. On 9acd9ea0 the gate went back at the bound with the settle
    alive, and its re-abort ended the next prompt (``turns_held`` was ``None``)."""

    host, harness, _model, state = await _swallowing_host(tmp_path, monkeypatch)
    first = asyncio.ensure_future(harness.prompt("first"))
    op: asyncio.Future[Any] | None = None
    third: asyncio.Future[Any] | None = None
    try:
        await asyncio.wait_for(state["live"][0].wait(), 10)
        op = asyncio.ensure_future(host.new_session() if kind == "new" else host.dispose())
        await asyncio.wait_for(state["first_abort"].wait(), 10)
        op.cancel()
        await asyncio.gather(op, return_exceptions=True)
        await asyncio.wait_for(first, 10)
        assert host.harness is harness and not harness.disposed
        assert isinstance(harness.turns_held, TurnSeal), "the gate went back with the settle alive"
        with pytest.raises(AgentHarnessError) as refused:
            await harness.prompt("second")
        assert refused.value.code == "invalid_state"
        state["release"].set()
        await asyncio.wait_for(asyncio.gather(*asr._SETTLES, return_exceptions=True), 10)
        for _ in range(100):
            if harness.turns_held is None:
                break
            await asyncio.sleep(0.01)
        assert harness.turns_held is None
        third = asyncio.ensure_future(harness.prompt("third"))
        await asyncio.wait_for(state["live"][1].wait(), 10)
        done, _ = await asyncio.wait({third}, timeout=0.3)
        assert third not in done, "the old settle aborted the next prompt"
        state["finish"].set()
        await asyncio.wait_for(third, 10)
        assert _message_text(harness.state.messages[-1]) == "completed"
        assert state["aborts"] == 1
    finally:
        state["finish"].set()
        state["release"].set()
        await _finish(first, *[t for t in (op, third) if t is not None])
        await asyncio.wait_for(asyncio.gather(*asr._SETTLES, return_exceptions=True), 10)
        await host.dispose()


async def test_a_hold_set_while_the_settle_lives_is_kept_when_it_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seal an interrupted swap keeps goes back to the earlier gate only while it
    is still the seal: a hold set meanwhile (the CLI's route hold) is not lifted when
    the settle ends. A guard for the round's ``turns_held is seal.gate`` check."""

    host, harness, _model, state = await _swallowing_host(tmp_path, monkeypatch)
    first = asyncio.ensure_future(harness.prompt("first"))
    op: asyncio.Future[Any] | None = None
    try:
        await asyncio.wait_for(state["live"][0].wait(), 10)
        op = asyncio.ensure_future(host.new_session())
        await asyncio.wait_for(state["first_abort"].wait(), 10)
        op.cancel()
        await asyncio.gather(op, return_exceptions=True)
        await asyncio.wait_for(first, 10)
        harness.hold_turns("held by the launch")
        state["release"].set()
        await asyncio.wait_for(asyncio.gather(*asr._SETTLES, return_exceptions=True), 10)
        await asyncio.sleep(0.05)
        assert harness.turns_held == "held by the launch"
    finally:
        state["finish"].set()
        state["release"].set()
        await _finish(first, *([op] if op is not None else []))
        await asyncio.wait_for(asyncio.gather(*asr._SETTLES, return_exceptions=True), 10)
        harness.hold_turns(None)
        await host.dispose()


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param(
            "switch_alias",
            marks=pytest.mark.skipif(
                sys.platform == "win32", reason="symlinks need privileges on Windows"
            ),
        ),
        "import_same",
    ],
)
async def test_on_a_runtime_that_manages_writer_locks_a_same_file_swap_keeps_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Verify round 2 F4 (mutants V-RR5: the lock's path compared with the target as
    spelled; V-RI2: the import's lock passed as ``None``). The CLI's runtime manages
    writer locks (``entry.py`` calls ``set_writer_lock``) and holds the current file's:
    a switch to a symlink of that file, or an import of it, is opened again after the
    teardown under that lock, so the installed session holds the report and an append
    keeps it on the branch. Guards: they pass on 9acd9ea0, and under either mutant no
    report is installed and the append takes it off the branch."""

    channel = _StoppableChannel()
    host, _harness, _seen, _events, path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    lock = SessionWriterLock(str(path))
    assert lock.try_acquire()
    host.set_writer_lock(lock)
    try:
        if kind == "switch_alias":
            alias = tmp_path / "alias.jsonl"
            alias.symlink_to(path)
            await asyncio.wait_for(host.switch_session(str(alias)), 20)
        else:
            await asyncio.wait_for(host.import_from_jsonl(str(path)), 20)
        await asyncio.wait_for(prompt, 20)
        installed = list((await host.session.build_context()).messages)
        await host.session.append_message(UserMessage(content=[TextContent(text="after")]))
        assert _reports(installed) == ["toolResult"]
        roles = [m.role for m in await _reopened(path)]
        assert roles == ["user", "assistant", "toolResult", "user"]
        assert host.writer_lock is lock
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


# === guards: pass on the base and after ===========================================


async def test_a_vetoed_new_neither_seals_nor_aborts(tmp_path: Path) -> None:
    """The veto (``session_before_switch``) runs before the teardown, so step 0 never
    runs for a swap an extension cancelled: the turn goes on and takes prompts after."""

    session, repo, _path = await _parent(tmp_path)
    gate = asyncio.Event()
    seen: list[list[Any]] = []

    async def gated_model(*_args: Any) -> Any:
        seen.append([])
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=gated_model))

    async def veto(_event: Any, _ctx: Any) -> SessionBeforeSwitchResult:
        return SessionBeforeSwitchResult(cancel=True)

    harness.hooks.on("session_before_switch", veto)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    prompt = asyncio.ensure_future(harness.prompt("work"))
    await asyncio.sleep(0.1)
    try:
        result = await asyncio.wait_for(host.new_session(), 10)
        assert result.cancelled is True
        assert harness.phase == "turn" and not prompt.done(), "the vetoed swap aborted the turn"
        gate.set()
        await asyncio.wait_for(prompt, 10)
        assert host.harness is harness
        await asyncio.wait_for(harness.prompt("still takes prompts"), 10)
    finally:
        gate.set()
        await _finish(prompt)
        await host.dispose()


async def test_reload_waits_for_idle_and_cancels_nothing(tmp_path: Path) -> None:
    """``reload`` takes no step 0: it waits for the turn to end on its own."""

    session, repo, _path = await _parent(tmp_path)
    gate = asyncio.Event()
    aborts: list[Any] = []

    async def gated_model(*_args: Any) -> Any:
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=gated_model))

    async def on_abort(event: Any, _ctx: Any) -> None:
        aborts.append(event)

    harness.hooks.on("abort", on_abort)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    prompt = asyncio.ensure_future(harness.prompt("work"))
    await asyncio.sleep(0.1)
    reload = asyncio.ensure_future(host.reload())
    try:
        await asyncio.sleep(0.3)
        assert not reload.done() and not prompt.done(), "reload did not wait for the turn"
        gate.set()
        await asyncio.wait_for(prompt, 10)
        await asyncio.wait_for(reload, 20)
        assert aborts == [], "reload aborted the turn"
        messages = list((await session.build_context()).messages)
        assert [_message_text(m) for m in messages if m.role == "assistant"] == ["done"]
    finally:
        gate.set()
        await _finish(prompt, reload)
        await host.dispose()


def test_the_settle_bound_is_ten_seconds() -> None:
    assert asr.SWAP_SETTLE_SECONDS == 10.0


async def test_a_quit_during_a_turn_settles_it_before_session_shutdown(tmp_path: Path) -> None:
    """``dispose`` takes step 0 too (pi's ``dispose`` begins with ``abort()``): a
    ``session_shutdown`` handler sees the turn already settled."""

    session, repo, _path = await _parent(tmp_path)
    gate = asyncio.Event()

    async def silent_model(*_args: Any) -> Any:
        await gate.wait()
        if False:  # pragma: no cover - an async generator that never yields
            yield None

    harness = AgentHarness(AgentHarnessOptions(model=MODEL, session=session, stream_fn=silent_model))
    phases: list[str] = []

    async def on_shutdown(_event: Any, _ctx: Any) -> None:
        phases.append(harness.phase)

    harness.hooks.on("session_shutdown", on_shutdown)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=MODEL, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    prompt = asyncio.ensure_future(harness.prompt("work"))
    await asyncio.sleep(0.1)
    try:
        await asyncio.wait_for(host.dispose(), 10)
        assert phases == ["idle"]
    finally:
        gate.set()
        await _finish(prompt)
