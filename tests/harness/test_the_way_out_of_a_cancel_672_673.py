"""#672, #673 (ADR-0290) — how a cancelled turn ends, and what is not a cancel.

What stops a batch is the task's own cancel count, not the exception anyone saw: a
``CancelledError`` nobody sent is a tool error and the turn goes on, and a cancel somebody
swallowed still stops the batch and the next request. A failure in one call commits the
others first. A second cancel stops waiting for a tool that ignores the first, and a
further cancel or one ``dispose()`` gets past a hook parked anywhere on the way out.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core import loop as loop_module
from aelix_agent_core.harness import core as core_module
from aelix_agent_core.harness.core import AgentHarnessError
from aelix_agent_core.harness.hooks import ToolResultPatch
from aelix_agent_core.loop import _CUT_TEXT
from aelix_agent_core.types import AgentTool
from aelix_agents.reaper import DEFAULT_GRACE_SECONDS
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.providers._transform_messages import transform_messages
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, TextDeltaEvent
from aelix_ai.tools import ToolCancelled, ToolExecutionContext, ToolResult
from aelix_coding_agent.tools.write import create_write_tool

from tests.harness._cancelled_turn import (
    MODEL,
    NOT_STARTED,
    finished_tool,
    held_tool,
    order_of,
    results_of,
    settle,
    start,
    text_of,
    until,
)


def _mode(sequential: bool) -> str | None:
    return "sequential" if sequential else None


async def _turns(count: int = 20) -> None:
    for _ in range(count):
        await asyncio.sleep(0)


async def _ignore_cancels_until(released: asyncio.Event) -> None:
    """On a clock, so that a red test ends: the loop's shutdown waits for this as anyone does."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 12
    while not released.is_set() and loop.time() < deadline:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(0.002)


def _ignores_every_cancel(
    reached: asyncio.Event, released: asyncio.Event, *, partial_after: bool = False
) -> AgentTool:
    async def execute(_args: Any, ctx: ToolExecutionContext) -> ToolResult:
        reached.set()
        await _ignore_cancels_until(released)
        if partial_after and ctx.on_partial is not None:
            ctx.on_partial(ToolResult(content=[TextContent(text="LATE-UPDATE")]))
        return ToolResult(content=[TextContent(text="RAN ANYWAY")])

    return AgentTool(name="ignores", execute=execute)


# === F3: a CancelledError nobody sent ========================================


def _raises_it(kind: str, sequential: bool) -> AgentTool:
    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        if kind == "bare":
            raise asyncio.CancelledError()
        if kind == "awaited":
            # How a real tool gets here: it awaits something somebody else cancelled.
            inner = asyncio.ensure_future(asyncio.sleep(30))
            inner.cancel()
            await inner
        if kind == "tool_cancelled":
            raise ToolCancelled(ToolResult(content=[TextContent(text="SAID THIS")]))
        raise ToolCancelled()

    return AgentTool(name="raises", execute=execute, execution_mode=_mode(sequential))


_RAISES_TEXT = {
    "bare": "Tool 'raises' raised CancelledError",
    "awaited": "Tool 'raises' raised CancelledError",
    "tool_cancelled": "SAID THIS",
    "tool_cancelled_empty": "Tool 'raises' raised CancelledError",
}


@pytest.mark.parametrize("kind", _RAISES_TEXT)
@pytest.mark.parametrize("sequential", [False, True])
async def test_a_cancel_nobody_sent_is_a_tool_error_and_the_turn_goes_on(
    tmp_path: Path, sequential: bool, kind: str
) -> None:
    tools = [_raises_it(kind, sequential), finished_tool("next", "NEXT", sequential=sequential)]
    run = await start(tmp_path, [("c1", "raises"), ("c2", "next")], tools)
    run.begin()
    await settle(run.task)  # type: ignore[arg-type]

    assert run.task is not None and not run.task.cancelled()
    assert results_of(await run.reopened()) == {
        "c1": (_RAISES_TEXT[kind], True),
        "c2": ("NEXT", False),
    }
    assert len(run.provider.seen) == 2, "the model reads the error and answers"
    assert "aborted" not in str(run.types("turn_end"))
    assert run.harness.state.messages[-1].stop_reason != "aborted"


@pytest.mark.parametrize("kind", ["bare", "tool_cancelled"])
async def test_a_sibling_is_not_left_waiting_for_a_cancel_nobody_sent(
    tmp_path: Path, kind: str
) -> None:
    """Codex's probe: a sibling on an ordinary event, and no cancel coming. It held the turn."""

    may_finish = asyncio.Event()
    cancels: list[str] = []

    async def sibling(_args: Any, _ctx: Any) -> ToolResult:
        try:
            await may_finish.wait()
        except asyncio.CancelledError:
            cancels.append("sibling")
            raise
        return ToolResult(content=[TextContent(text="SIBLING")])

    tools = [_raises_it(kind, False), AgentTool(name="sibling", execute=sibling)]
    run = await start(tmp_path, [("c1", "raises"), ("c2", "sibling")], tools)
    run.begin()
    await until(lambda: "c1" in run.ended())
    assert run.task is not None and not run.task.done(), "the sibling is working, not stuck"
    may_finish.set()
    await settle(run.task)

    assert cancels == []
    assert results_of(await run.reopened())["c2"] == ("SIBLING", False)
    assert len(run.provider.seen) == 2


# === F4: a cancel somebody swallowed =========================================


def _swallows(how: str, reached: asyncio.Event, *, sequential: bool = False) -> AgentTool:
    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if how == "raises":
                raise RuntimeError("cleanup broke") from None
            return ToolResult(content=[TextContent(text="CLEANUP DONE")])
        return ToolResult(content=[])

    return AgentTool(name="swallows", execute=execute, execution_mode=_mode(sequential))


@pytest.mark.parametrize(
    ("how", "text", "is_error"),
    [
        ("returns", "CLEANUP DONE", False),
        ("raises", "Tool 'swallows' raised: cleanup broke", True),
    ],
)
async def test_a_tool_swallowing_the_cancel_does_not_start_a_later_write(
    tmp_path: Path, how: str, text: str, is_error: bool
) -> None:
    """Codex's probe with the real write tool, which is what makes the batch sequential."""

    target = tmp_path / "AFTER-ESC.txt"
    reached = asyncio.Event()
    tools = [_swallows(how, reached), create_write_tool(str(tmp_path))]
    run = await start(tmp_path, [("c1", "swallows"), ("c2", "write")], tools)
    run.provider.calls[1].input.update({"path": target.name, "content": "WROTE AFTER ABORT"})
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    assert not target.exists()
    assert run.started() == ["c1"]
    assert len(run.provider.seen) == 1, "no further model request goes out"
    assert results_of(await run.reopened()) == {"c1": (text, is_error)}
    wire = results_of(await run.next_request())
    assert wire["c1"] == (text, is_error)
    assert wire["c2"] == (NOT_STARTED, True)
    assert not target.exists()


async def test_a_parallel_tool_swallowing_the_cancel_ends_the_turn_too(tmp_path: Path) -> None:
    reached = asyncio.Event()
    tools = [_swallows("returns", reached), finished_tool("one", "ONE")]
    run = await start(tmp_path, [("c1", "swallows"), ("c2", "one")], tools)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: "c2" in run.ended())
    await run.cancel()

    assert len(run.provider.seen) == 1
    assert results_of(await run.reopened()) == {
        "c1": ("CLEANUP DONE", False),
        "c2": ("ONE", False),
    }
    assert run.harness.state.messages[-1].stop_reason == "aborted"


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_tool_call_hook_swallowing_the_cancel_starts_nothing(
    tmp_path: Path, sequential: bool
) -> None:
    ran: list[str] = []

    def counting(name: str) -> AgentTool:
        async def execute(_args: Any, _ctx: Any) -> ToolResult:
            ran.append(name)
            return ToolResult(content=[TextContent(text=name)])

        return AgentTool(name=name, execute=execute, execution_mode=_mode(sequential))

    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], [counting("one"), counting("two")])
    in_hook = asyncio.Event()

    async def swallow(_event: Any, _ctx: Any) -> None:
        in_hook.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()

    run.harness.hooks.on("tool_call", swallow)
    run.begin()
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    await run.cancel()

    assert ran == []
    assert len(run.provider.seen) == 1
    assert results_of(await run.reopened()) == {}


def _swallowing_listener(
    event_type: str, role: str | None, in_it: asyncio.Event
) -> Any:
    fired = {"done": False}

    async def listener(event: Any) -> None:
        if event.type != event_type or fired["done"]:
            return
        message = getattr(event, "message", None)
        if role is not None and getattr(message, "role", None) != role:
            return
        fired["done"] = True
        in_it.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()

    return listener


@pytest.mark.parametrize(
    ("event_type", "role", "calls_start"),
    [
        ("message_start", "assistant", False),
        ("message_update", "assistant", False),
        ("message_end", "assistant", False),
        ("tool_execution_start", None, False),
        ("tool_execution_end", None, True),
        ("message_start", "toolResult", True),
        ("message_end", "toolResult", True),
        ("turn_end", None, True),
    ],
)
@pytest.mark.parametrize("sequential", [False, True])
async def test_a_listener_swallowing_the_cancel_stops_the_next_step(
    tmp_path: Path, sequential: bool, event_type: str, role: str | None, calls_start: bool
) -> None:
    """Every site of the loop a swallowed cancel could get past, with a later call to start."""

    tools = [
        finished_tool("one", "ONE", sequential=sequential),
        finished_tool("two", "TWO", sequential=sequential),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    in_it = asyncio.Event()
    run.harness.subscribe(_swallowing_listener(event_type, role, in_it))

    async def stream(_model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        run.provider.seen.append(list(context.messages))
        message = AssistantMessage(content=list(run.provider.calls), stop_reason="tool_use")
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield TextDeltaEvent(delta="x")
        yield AssistantEndEvent(message=message)

    run.harness._options.stream_fn = stream
    run.begin()
    await asyncio.wait_for(in_it.wait(), timeout=5)
    await run.cancel()

    assert len(run.provider.seen) == 1, "no further model request goes out"
    started = run.started()
    if not calls_start:
        assert started == (["c1"] if event_type == "tool_execution_start" else [])
        assert results_of(run.harness.state.messages) == {}
        if event_type in ("message_start", "message_update"):
            assert not [m for m in run.harness.state.messages if m.role == "assistant" and m.content]
    elif sequential and event_type != "turn_end":
        assert started == ["c1"], "the later call stays unstarted"
    assert run.harness.state.messages[-1].stop_reason == "aborted"
    assert run.harness.phase == "idle"


async def test_a_cancel_swallowed_at_an_immediate_stops_a_sequential_batch(tmp_path: Path) -> None:
    tools = [finished_tool("two", "TWO", sequential=True)]
    run = await start(tmp_path, [("c1", "no-such-tool"), ("c2", "two")], tools)
    in_it = asyncio.Event()
    run.harness.subscribe(_swallowing_listener("tool_execution_end", None, in_it))
    run.begin()
    await asyncio.wait_for(in_it.wait(), timeout=5)
    await run.cancel()

    assert run.started() == ["c1"]
    assert "Unknown tool" in results_of(await run.reopened())["c1"][0]


# === F5: a failure in one call ===============================================


def _failure_batch(
    running: asyncio.Event, cleaning: asyncio.Event, release: asyncio.Event
) -> list[AgentTool]:
    async def held(_args: Any, _ctx: Any) -> ToolResult:
        running.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            # On a clock, so that a red test ends: the loop's shutdown waits for this too.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(release.wait(), 12)
            raise
        return ToolResult(content=[])

    return [
        finished_tool("one", "SECRET"),
        finished_tool("two", "TWO-RESULT"),
        AgentTool(name="held", execute=held),
    ]


_FAILURE_CALLS = [("c1", "one"), ("c2", "two"), ("c3", "held")]


async def test_a_failure_in_one_call_commits_the_others_then_raises(tmp_path: Path) -> None:
    """Nobody cancelled. It closed the turn with nothing committed and a sibling still running."""

    running, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def after(ctx: Any) -> None:
        if ctx.tool_call.tool_call_id == "c1":
            await until(lambda: "c2" in run.ended() and running.is_set())
            raise AgentHarnessError("hook", "the redactor broke")

    run = await start(
        tmp_path, _FAILURE_CALLS, _failure_batch(running, cleaning, release), after_tool_call=after
    )
    task = run.begin()
    await asyncio.wait_for(cleaning.wait(), timeout=5)
    await _turns()
    assert not task.done(), "it waits for the call it cancelled, as for a cancel"
    assert run.types("agent_end") == []
    release.set()
    await asyncio.wait([task], timeout=5)

    with pytest.raises(AgentHarnessError, match="the redactor broke"):
        task.result()
    results = results_of(await run.reopened())
    assert results == {"c2": ("TWO-RESULT", False), "c3": (_CUT_TEXT, True)}
    assert order_of(await run.reopened()) == ["c2", "c3"]
    assert "SECRET" not in str(results)
    types = [e.type for e in run.events]
    assert types[-1] == "agent_end", "nothing follows the end of the turn"
    assert types.count("agent_end") == 1
    assert run.ended().count("c3") == 1
    wire = results_of(await run.next_request())
    assert wire["c1"] == (NOT_STARTED, True)
    assert "SECRET" not in str(wire)


async def test_a_hook_raising_out_of_a_cancel_withholds_its_output_and_the_turn_is_aborted(
    tmp_path: Path,
) -> None:
    """Codex's probe. The cancel stays the cancel, and the batch waits for the call cleaning up."""

    running, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    in_hook = asyncio.Event()

    async def after(ctx: Any) -> None:
        if ctx.tool_call.tool_call_id == "c1":
            in_hook.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise AgentHarnessError("hook", "failed alongside the cancel") from None

    run = await start(
        tmp_path, _FAILURE_CALLS, _failure_batch(running, cleaning, release), after_tool_call=after
    )
    task = run.begin()
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    await asyncio.wait_for(running.wait(), timeout=5)
    await until(lambda: "c2" in run.ended())
    await run.harness.abort()
    await asyncio.wait_for(cleaning.wait(), timeout=5)
    await _turns()
    assert not task.done()
    assert run.types("agent_end") == []
    release.set()
    await settle(task)

    assert results_of(await run.reopened()) == {
        "c1": (_CUT_TEXT, True),
        "c2": ("TWO-RESULT", False),
        "c3": (_CUT_TEXT, True),
    }
    assert run.events[-1].type == "agent_end"
    assert run.harness.state.messages[-1].stop_reason == "aborted"


async def test_a_sink_failing_on_the_way_up_does_not_replace_the_failure_or_cost_the_rest() -> None:
    """The loop with a sink of its own: c2's commit breaks, and c3 still commits."""

    from aelix_agent_core.loop import agent_loop
    from aelix_agent_core.types import AgentContext, AgentLoopConfig

    async def stream(_model: Any, _context: Any, _options: Any) -> AsyncIterator[Any]:
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[
                    ToolCallContent(tool_call_id=c, tool_name=c, input={})
                    for c in ("c1", "c2", "c3")
                ],
                stop_reason="tool_use",
            )
        )

    async def after(ctx: Any) -> None:
        if ctx.tool_call.tool_call_id == "c1":
            raise AgentHarnessError("hook", "the original failure")

    committed: list[str] = []

    async def emit(event: Any) -> None:
        call_id = getattr(getattr(event, "message", None), "tool_call_id", None)
        if event.type == "message_end" and call_id is not None:
            if call_id == "c2":
                raise RuntimeError("sink broke")
            committed.append(call_id)

    config = AgentLoopConfig(
        model=MODEL,
        convert_to_llm=lambda messages: messages,
        tool_execution="parallel",
        after_tool_call=after,
    )
    tools = [finished_tool(c, c.upper()) for c in ("c1", "c2", "c3")]
    context = AgentContext(system_prompt="", messages=[], tools=tools)
    prompt = UserMessage(content=[TextContent(text="go")])
    with pytest.raises(AgentHarnessError, match="the original failure"):
        await agent_loop([prompt], context, config, emit=emit, stream_fn=stream)

    assert committed == ["c3"]


async def test_a_tool_error_still_cancels_no_sibling(tmp_path: Path) -> None:
    """A tool's error is a result and never reaches any of this. ADR-0021's rule stands."""

    may_finish = asyncio.Event()

    async def fails(_args: Any, _ctx: Any) -> ToolResult:
        raise RuntimeError("tool error")

    async def sibling(_args: Any, _ctx: Any) -> ToolResult:
        await may_finish.wait()
        return ToolResult(content=[TextContent(text="SIBLING")])

    tools = [AgentTool(name="fails", execute=fails), AgentTool(name="sibling", execute=sibling)]
    run = await start(tmp_path, [("c1", "fails"), ("c2", "sibling")], tools)
    run.begin()
    await until(lambda: "c1" in run.ended())
    may_finish.set()
    await settle(run.task)  # type: ignore[arg-type]

    assert results_of(await run.reopened())["c2"] == ("SIBLING", False)


async def test_a_second_cancel_stops_the_wait_for_a_failures_siblings(tmp_path: Path) -> None:
    """The batch's own cancel of the siblings counts as the first. The cancel then wins."""

    reached, released = asyncio.Event(), asyncio.Event()

    async def after(ctx: Any) -> None:
        if ctx.tool_call.tool_call_id == "c1":
            await until(reached.is_set)
            raise AgentHarnessError("hook", "broke")

    tools = [finished_tool("one", "ONE"), _ignores_every_cancel(reached, released)]
    run = await start(tmp_path, [("c1", "one"), ("c2", "ignores")], tools, after_tool_call=after)
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await asyncio.sleep(0.02)
    assert not task.done()
    await run.harness.abort()
    await asyncio.wait([task], timeout=5)
    released.set()

    assert task.done() and not task.cancelled()
    assert task.exception() is None, "a failure on the way out does not replace the cancel"
    assert results_of(await run.reopened()) == {"c2": (_CUT_TEXT, True)}


# === F6: a second cancel stops waiting =======================================


async def test_a_tool_ignoring_cancel_holds_a_parallel_turn_for_one_cancel_only(
    tmp_path: Path,
) -> None:
    reached, released = asyncio.Event(), asyncio.Event()
    tools = [
        _ignores_every_cancel(reached, released, partial_after=True),
        finished_tool("one", "ONE-RESULT"),
    ]
    run = await start(tmp_path, [("c1", "ignores"), ("c2", "one")], tools)
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: "c2" in run.ended(), turns=20_000)
    await run.harness.abort()
    await asyncio.sleep(0.02)
    assert not task.done(), "the first cancel waits: the records are the point"
    await run.harness.abort()
    await settle(task)

    expected = {"c1": (_CUT_TEXT, True), "c2": ("ONE-RESULT", False)}
    assert results_of(await run.reopened()) == expected
    assert order_of(await run.reopened()) == ["c1", "c2"]
    assert run.ended().count("c1") == 1
    assert run.events[-1].type == "agent_end"

    # The straggler ends now, and everything it does goes nowhere.
    seen = len(run.events)
    state = list(run.harness.state.messages)
    released.set()
    await asyncio.sleep(0.02)
    await _turns()
    assert len(run.events) == seen, "no event after agent_end"
    assert run.harness.state.messages == state, "no state write"
    assert results_of(await run.reopened()) == expected
    assert not loop_module._STRAGGLERS


async def test_a_straggler_runs_no_hook_when_it_ends(tmp_path: Path) -> None:
    reached, released = asyncio.Event(), asyncio.Event()
    after_calls: list[str] = []
    tool_results: list[str] = []

    async def after(ctx: Any) -> None:
        after_calls.append(ctx.tool_call.tool_call_id)

    tools = [_ignores_every_cancel(reached, released)]
    run = await start(tmp_path, [("c1", "ignores")], tools, after_tool_call=after)
    run.harness.hooks.on("tool_result", lambda event, _ctx: tool_results.append(event.tool_call_id))
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await asyncio.sleep(0.01)
    await run.harness.abort()
    await settle(task)
    released.set()
    await asyncio.sleep(0.02)

    assert after_calls == []
    assert tool_results == []


async def test_a_stragglers_failure_is_read_and_goes_nowhere(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    reached, released = asyncio.Event(), asyncio.Event()

    async def ignores_then_fails(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        await _ignore_cancels_until(released)
        raise RuntimeError("much later")

    tools = [AgentTool(name="ignores", execute=ignores_then_fails)]
    run = await start(tmp_path, [("c1", "ignores")], tools)
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await asyncio.sleep(0.01)
    await run.harness.abort()
    await settle(task)
    seen = len(run.events)
    released.set()
    await asyncio.sleep(0.02)

    assert len(run.events) == seen
    assert not loop_module._STRAGGLERS
    assert "was never retrieved" not in caplog.text


async def test_a_call_stuck_in_a_hook_a_second_cancel_gets_past_gives_the_default_text(
    tmp_path: Path,
) -> None:
    """The hook ignores the cancel too. Its output never reached anything, so it is withheld."""

    in_hook, released = asyncio.Event(), asyncio.Event()

    async def after(_ctx: Any) -> None:
        in_hook.set()
        await _ignore_cancels_until(released)

    tools = [finished_tool("one", "SECRET")]
    run = await start(tmp_path, [("c1", "one")], tools, after_tool_call=after)
    task = run.begin()
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    await run.harness.abort()
    await asyncio.sleep(0.01)
    await run.harness.abort()
    await settle(task)
    released.set()
    await asyncio.sleep(0.02)

    assert results_of(await run.reopened()) == {"c1": (_CUT_TEXT, True)}
    assert run.ended() == ["c1"]


async def test_an_end_event_a_listener_ignores_the_cancel_in_goes_out_once_with_its_result(
    tmp_path: Path,
) -> None:
    in_listener, released = asyncio.Event(), asyncio.Event()

    async def listener(event: Any) -> None:
        if event.type == "tool_execution_end" and event.tool_call_id == "c1":
            in_listener.set()
            await _ignore_cancels_until(released)

    tools = [finished_tool("one", "ONE-RESULT"), finished_tool("two", "TWO-RESULT")]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    run.harness.subscribe(listener)
    task = run.begin()
    await asyncio.wait_for(in_listener.wait(), timeout=5)
    await until(lambda: "c2" in run.ended())
    await run.harness.abort()
    await asyncio.sleep(0.01)
    await run.harness.abort()
    await settle(task)
    released.set()
    await asyncio.sleep(0.02)

    assert run.ended().count("c1") == 1
    assert results_of(await run.reopened()) == {
        "c1": ("ONE-RESULT", False),
        "c2": ("TWO-RESULT", False),
    }


async def test_a_tool_that_ends_on_the_second_cancel_keeps_what_it_says(tmp_path: Path) -> None:
    """The second cancel reaches it, and it has the grace to say so."""

    reached = asyncio.Event()

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as cancel:
            raise ToolCancelled(ToolResult(content=[TextContent(text="STOPPED AT LAST")])) from cancel
        return ToolResult(content=[])

    run = await start(tmp_path, [("c1", "stubborn")], [AgentTool(name="stubborn", execute=execute)])
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await _turns()
    assert not task.done()
    await run.harness.abort()
    await settle(task)

    assert results_of(await run.reopened()) == {"c1": ("STOPPED AT LAST", True)}
    assert run.ended() == ["c1"]


async def test_two_cancels_in_one_loop_turn_count_as_two(tmp_path: Path) -> None:
    reached, released = asyncio.Event(), asyncio.Event()
    run = await start(
        tmp_path, [("c1", "ignores")], [_ignores_every_cancel(reached, released)]
    )
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await run.harness.abort()
    await settle(task)
    released.set()

    assert results_of(await run.reopened()) == {"c1": (_CUT_TEXT, True)}


async def test_a_tool_ignoring_cancel_holds_a_sequential_turn_and_each_cancel_reaches_it(
    tmp_path: Path,
) -> None:
    """Nothing runs beside it, so there is no record the wait could cost, and no way past it."""

    reached, released = asyncio.Event(), asyncio.Event()
    cancels: list[int] = []

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        deadline = asyncio.get_running_loop().time() + 12
        while not released.is_set() and asyncio.get_running_loop().time() < deadline:
            try:
                await asyncio.sleep(0.002)
            except asyncio.CancelledError:
                cancels.append(1)
        return ToolResult(content=[TextContent(text="RAN ANYWAY")])

    tool = AgentTool(name="ignores", execute=execute, execution_mode="sequential")
    run = await start(tmp_path, [("c1", "ignores")], [tool])
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    for _ in range(3):
        await run.harness.abort()
        await asyncio.sleep(0.01)
    assert not task.done()
    assert len(cancels) == 3
    released.set()
    await settle(task)

    assert results_of(await run.reopened()) == {"c1": ("RAN ANYWAY", False)}
    assert len(run.provider.seen) == 1


# === F7a: the close-out ======================================================


def _parks_at(event_type: str, parked: asyncio.Event, cancels: list[str]) -> Any:
    async def listener(event: Any) -> None:
        if event.type != event_type:
            return
        parked.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancels.append(event_type)
            raise

    return listener


@pytest.mark.parametrize("event_type", ["turn_end", "agent_end"])
async def test_another_esc_gets_past_a_listener_parked_in_the_close_out(
    tmp_path: Path, event_type: str
) -> None:
    reached, parked = asyncio.Event(), asyncio.Event()
    cancels: list[str] = []
    run = await start(tmp_path, [("c1", "held")], [held_tool("held", reached)])
    run.harness.subscribe(_parks_at(event_type, parked, cancels))
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await asyncio.wait_for(parked.wait(), timeout=5)
    await _turns()
    assert not task.done()
    await run.harness.abort()
    await settle(task)

    assert cancels == [event_type]
    assert run.harness.phase == "idle"
    assert results_of(await run.reopened()) == {"c1": (_CUT_TEXT, True)}
    if event_type == "turn_end":
        assert run.types("agent_end") == ["agent_end"], "the next event still goes out"


async def test_a_hook_parked_on_both_close_out_events_takes_a_cancel_each(tmp_path: Path) -> None:
    reached = asyncio.Event()
    parked: list[str] = []
    run = await start(tmp_path, [("c1", "held")], [held_tool("held", reached)])

    async def park(event: Any, _ctx: Any) -> None:
        if event.type == "turn_end" and event.message.stop_reason != "aborted":
            return
        parked.append(event.type)
        await asyncio.Event().wait()

    run.harness.hooks.on("turn_end", park)
    run.harness.hooks.on("agent_end", park)
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await until(lambda: parked == ["turn_end"])
    await run.harness.abort()
    await until(lambda: parked == ["turn_end", "agent_end"])
    assert not task.done()
    await run.harness.abort()
    await settle(task)

    assert task.exception() is None


async def test_an_outer_cancel_during_the_close_out_still_propagates(tmp_path: Path) -> None:
    reached, parked = asyncio.Event(), asyncio.Event()
    cancels: list[str] = []
    run = await start(tmp_path, [("c1", "held")], [held_tool("held", reached)])
    run.harness.subscribe(_parks_at("turn_end", parked, cancels))
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await asyncio.wait_for(parked.wait(), timeout=5)
    task.cancel()
    await asyncio.wait([task], timeout=5)

    assert task.cancelled(), "a cancel of the prompt itself is not an abort"
    assert cancels == ["turn_end"]
    assert run.types("agent_end") == [], "it did not move on to the next event"
    assert run.harness.phase == "idle"


async def test_a_cancelled_prompt_during_an_abort_keeps_returning(tmp_path: Path) -> None:
    """Both before the close-out began, as today: the abort path returns and nothing parks."""

    reached = asyncio.Event()
    run = await start(tmp_path, [("c1", "held")], [held_tool("held", reached)])
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    task.cancel()
    await asyncio.wait([task], timeout=5)

    assert task.done()
    assert run.types("agent_end") == ["agent_end"]


# === A second abort at every loop turn over the close-out =====================


def _unwinds_in(turns: int, reached: asyncio.Event, *, sequential: bool) -> AgentTool:
    """Its cleanup takes ``turns`` loop turns, whatever lands in them, then the cancel goes on."""

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            with contextlib.suppress(asyncio.CancelledError):
                await _turns(turns)
            raise
        return ToolResult(content=[])

    return AgentTool(
        name="t", execute=execute, execution_mode="sequential" if sequential else None
    )


@pytest.mark.parametrize("cleanup_turns", [0, 3])
@pytest.mark.parametrize("sequential", [False, True])
async def test_a_second_abort_at_any_loop_turn_costs_neither_terminator(
    tmp_path: Path, sequential: bool, cleanup_turns: int
) -> None:
    """An emit task must not be reachable before its first step, where a cancel ends it unrun.

    The offsets are counted, not assumed: they run past the whole way out of this very shape,
    so that a change in how many turns it takes cannot leave the close-out outside the sweep.
    """

    async def second_abort_after(turns: int | None, folder: str) -> tuple[Any, int]:
        (tmp_path / folder).mkdir()
        reached = asyncio.Event()
        tool = _unwinds_in(cleanup_turns, reached, sequential=sequential)
        run = await start(tmp_path / folder, [("c1", "t")], [tool])
        hooked: list[str] = []
        for name in ("turn_end", "agent_end"):
            run.harness.hooks.on(name, lambda event, _ctx: hooked.append(event.type))
        task = run.begin()
        await asyncio.wait_for(reached.wait(), timeout=5)
        await run.harness.abort()
        span = 0
        if turns is None:
            while not task.done():
                await asyncio.sleep(0)
                span += 1
        else:
            await _turns(turns)
            await run.harness.abort()
        await settle(task)
        assert task.exception() is None, folder
        assert run.types("turn_end", "agent_end") == ["turn_end", "agent_end"], folder
        assert hooked == ["turn_end", "agent_end"], folder
        assert run.harness.phase == "idle", folder
        assert run.harness.state.messages[-1].stop_reason == "aborted", folder
        return run, span

    _, span = await second_abort_after(None, "one-abort")
    assert span > 0
    for turns in range(span + 3):
        await second_abort_after(turns, f"second-after-{turns}")


# === F7b: dispose ============================================================


@pytest.mark.parametrize(
    ("event", "kept"),
    [
        ("tool_result", _CUT_TEXT),
        ("tool_execution_end", "SAID"),
        ("message_end", None),
    ],
)
async def test_one_dispose_gets_past_a_hook_parked_on_a_cut_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: str, kept: str | None
) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._REABORT_GRACE_SECONDS", 0.02)
    reached = asyncio.Event()

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as cancel:
            raise ToolCancelled(ToolResult(content=[TextContent(text="SAID")])) from cancel
        return ToolResult(content=[])

    run = await start(tmp_path, [("c1", "t")], [AgentTool(name="t", execute=execute)])
    parked = asyncio.Event()

    async def park(hook_event: Any, _ctx: Any) -> None:
        message = getattr(hook_event, "message", None)
        if message is not None and not isinstance(message, ToolResultMessage):
            return
        parked.set()
        await asyncio.Event().wait()

    run.harness.hooks.on(event, park)
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await asyncio.wait_for(run.harness.dispose(), timeout=5)

    assert parked.is_set()
    assert task.done()
    assert run.harness.phase == "idle"
    results = results_of(run.harness.state.messages)
    if kept is None:
        assert results == {}, "the result of a call whose hook is parked may be lost"
    else:
        assert results == {"c1": (kept, True)}


def _cleans_up_in(seconds: float, reached: asyncio.Event, cleanup: list[str]) -> AgentTool:
    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.append("begun")
            await asyncio.sleep(seconds)
            cleanup.append("finished")
        return ToolResult(content=[])

    return AgentTool(name="t", execute=execute)


async def test_one_dispose_lets_a_tools_cleanup_finish_inside_the_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._REABORT_GRACE_SECONDS", 1.0)
    reached = asyncio.Event()
    cleanup: list[str] = []
    run = await start(tmp_path, [("c1", "t")], [_cleans_up_in(0.05, reached, cleanup)])
    aborts: list[int] = []
    run.harness.hooks.on("abort", lambda _event, _ctx: aborts.append(1))
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await asyncio.wait_for(run.harness.dispose(), timeout=5)

    assert task.done()
    assert cleanup == ["begun", "finished"]
    assert aborts == [1], "nothing was left to abort again"


async def test_one_dispose_cuts_a_cleanup_longer_than_the_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._REABORT_GRACE_SECONDS", 0.02)
    reached = asyncio.Event()
    cleanup: list[str] = []
    run = await start(tmp_path, [("c1", "t")], [_cleans_up_in(60.0, reached, cleanup)])
    aborts: list[int] = []
    run.harness.hooks.on("abort", lambda _event, _ctx: aborts.append(1))
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await asyncio.wait_for(run.harness.dispose(), timeout=5)

    assert task.done()
    assert cleanup == ["begun"]
    assert aborts[:2] == [1, 1]
    assert results_of(await run.reopened()) == {"c1": (_CUT_TEXT, True)}


def test_the_wait_between_re_aborts_fits_inside_the_delegation_reapers_grace() -> None:
    """Past the reaper's grace the re-abort never lands: the child is SIGKILLed first (#220)."""

    assert core_module._REABORT_GRACE_SECONDS == 2.0
    assert core_module._REABORT_GRACE_SECONDS < DEFAULT_GRACE_SECONDS


async def test_dispose_aborts_once_when_the_turn_ends_on_the_first(tmp_path: Path) -> None:
    reached = asyncio.Event()
    run = await start(tmp_path, [("c1", "held")], [held_tool("held", reached)])
    aborts: list[int] = []
    run.harness.hooks.on("abort", lambda _event, _ctx: aborts.append(1))
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.dispose()

    assert task.done()
    assert aborts == [1]


async def test_dispose_stops_aborting_after_a_few_rounds_and_waits_as_it_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What no cancel can end stays for the delegation reaper to escalate past (#220)."""

    monkeypatch.setattr("aelix_agent_core.harness.core._REABORT_GRACE_SECONDS", 0.005)
    reached, released = asyncio.Event(), asyncio.Event()

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        await _ignore_cancels_until(released)
        return ToolResult(content=[])

    tool = AgentTool(name="ignores", execute=execute, execution_mode="sequential")
    run = await start(tmp_path, [("c1", "ignores")], [tool])
    aborts: list[int] = []
    run.harness.hooks.on("abort", lambda _event, _ctx: aborts.append(1))
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    disposing = asyncio.ensure_future(run.harness.dispose())
    await asyncio.sleep(0.2)

    assert not disposing.done()
    from aelix_agent_core.harness.core import _REABORTS

    assert len(aborts) == 1 + _REABORTS
    released.set()
    await asyncio.wait_for(disposing, timeout=5)
    assert task.done()


# === F8: a cancel in message_start ===========================================


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_cancel_in_a_results_message_start_does_not_cost_it(
    tmp_path: Path, sequential: bool
) -> None:
    tools = [
        finished_tool("one", "ONE-RESULT", sequential=sequential),
        finished_tool("two", "TWO-RESULT", sequential=sequential),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    in_listener = asyncio.Event()

    async def park_on_c1(event: Any) -> None:
        if event.type == "message_start" and getattr(event.message, "tool_call_id", "") == "c1":
            in_listener.set()
            await asyncio.Event().wait()

    run.harness.subscribe(park_on_c1)
    run.begin()
    await asyncio.wait_for(in_listener.wait(), timeout=5)
    await run.cancel()

    results = results_of(await run.reopened())
    assert results["c1"] == ("ONE-RESULT", False)
    assert len(run.provider.seen) == 1, "and it is still a cancel"
    if sequential:
        assert "c2" not in results, "a later call stays unstarted"
    else:
        assert results["c2"] == ("TWO-RESULT", False)


async def test_a_listener_parked_on_every_message_start_takes_a_cancel_each_and_costs_nothing(
    tmp_path: Path,
) -> None:
    """The verifier's probe: all three lost, on four cancels."""

    tools = [finished_tool(n, n.upper()) for n in ("one", "two", "three")]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two"), ("c3", "three")], tools)
    parked: list[str] = []

    async def park(event: Any) -> None:
        if event.type == "message_start" and isinstance(event.message, ToolResultMessage):
            parked.append(event.message.tool_call_id)
            await asyncio.Event().wait()

    run.harness.subscribe(park)
    task = run.begin()
    for count in (1, 2, 3):
        await until(lambda n=count: len(parked) == n)  # type: ignore[misc]
        await run.harness.abort()
    await settle(task)

    assert results_of(await run.reopened()) == {
        "c1": ("ONE", False),
        "c2": ("TWO", False),
        "c3": ("THREE", False),
    }
    assert order_of(await run.reopened()) == ["c1", "c2", "c3"]


async def test_a_cancel_in_message_end_still_costs_that_result(tmp_path: Path) -> None:
    """Limit 3: nothing can tell whether a cancelled sink got as far as the commit."""

    run = await start(tmp_path, [("c1", "one")], [finished_tool("one", "ONE-RESULT")])
    in_hook = asyncio.Event()

    async def park(event: Any, _ctx: Any) -> None:
        if isinstance(event.message, ToolResultMessage):
            in_hook.set()
            await asyncio.Event().wait()

    run.harness.hooks.on("message_end", park)
    run.begin()
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    await run.cancel()

    assert results_of(await run.reopened()) == {}


# === F9: the wire's order ====================================================


def _assistant(*call_ids: str) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCallContent(tool_call_id=c, tool_name=f"t{c}", input={}) for c in call_ids],
        stop_reason="tool_use",
    )


def _result(call_id: str, text: str = "REAL") -> ToolResultMessage:
    return ToolResultMessage(tool_call_id=call_id, content=[TextContent(text=text)])


def _wire_ids(*messages: Any) -> list[tuple[str, str]]:
    return [
        (m.tool_call_id, text_of(m))
        for m in transform_messages(list(messages), MODEL)
        if isinstance(m, ToolResultMessage)
    ]


def test_a_batch_with_a_hole_reads_in_the_order_the_assistant_called() -> None:
    assert _wire_ids(_assistant("c1", "c2", "c3"), _result("c2")) == [
        ("c1", NOT_STARTED),
        ("c2", "REAL"),
        ("c3", NOT_STARTED),
    ]


def test_a_hole_before_the_next_input_reads_the_same() -> None:
    messages = [
        _assistant("c1", "c2", "c3"),
        _result("c3"),
        _result("c1"),
        UserMessage(content=[TextContent(text="next")]),
    ]
    out = transform_messages(messages, MODEL)
    assert [type(m).__name__ for m in out] == [
        "AssistantMessage",
        "ToolResultMessage",
        "ToolResultMessage",
        "ToolResultMessage",
        "UserMessage",
    ]
    assert [m.tool_call_id for m in out if isinstance(m, ToolResultMessage)] == ["c1", "c2", "c3"]


def test_a_batch_with_no_hole_keeps_the_order_it_arrived_in() -> None:
    assert _wire_ids(_assistant("c1", "c2"), _result("c2"), _result("c1")) == [
        ("c2", "REAL"),
        ("c1", "REAL"),
    ]


def test_only_the_batch_with_the_hole_moves() -> None:
    wire = _wire_ids(
        _assistant("c1", "c2"),
        _result("c2"),
        _result("c1"),
        UserMessage(content=[TextContent(text="next")]),
        _assistant("c3", "c4"),
        _result("c4"),
    )
    assert wire == [("c2", "REAL"), ("c1", "REAL"), ("c3", NOT_STARTED), ("c4", "REAL")]


def test_a_result_the_batch_did_not_call_stays_after_it() -> None:
    assert _wire_ids(_assistant("c1", "c2"), _result("stray"), _result("c2")) == [
        ("c1", NOT_STARTED),
        ("c2", "REAL"),
        ("stray", "REAL"),
    ]


async def test_an_immediate_ahead_of_a_held_prepare_reads_in_call_order(tmp_path: Path) -> None:
    """Codex's probe: c1 prepared, c2 an unknown tool, c3 waiting in its ``tool_call`` hook."""

    tools = [finished_tool("one", "ONE"), finished_tool("three", "THREE")]
    run = await start(tmp_path, [("c1", "one"), ("c2", "no-such-tool"), ("c3", "three")], tools)
    in_hook = asyncio.Event()

    async def hold_c3(event: Any, _ctx: Any) -> None:
        if event.tool_call_id == "c3":
            in_hook.set()
            await asyncio.Event().wait()

    run.harness.hooks.on("tool_call", hold_c3)
    run.begin()
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    await run.cancel()

    assert list(results_of(await run.reopened())) == ["c2"]
    wire = await run.next_request()
    assert order_of(wire) == ["c1", "c2", "c3"]
    assert results_of(wire)["c1"] == (NOT_STARTED, True)
    assert results_of(wire)["c3"] == (NOT_STARTED, True)
    assert "Unknown tool" in results_of(wire)["c2"][0]


# === F2 + F10: what a hook and the loop read =================================


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_redactor_that_handles_only_errors_sees_a_cut_result_as_one(
    tmp_path: Path, sequential: bool
) -> None:
    """Codex's probe: the guide's own example leaves ``is_error`` False, and it was read so."""

    reached = asyncio.Event()

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        try:
            reached.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError as cancel:
            raise ToolCancelled(
                ToolResult(content=[TextContent(text="SECRET-PARTIAL")], details={"s": "SECRET"})
            ) from cancel
        return ToolResult(content=[])

    contexts: list[bool] = []

    async def after(ctx: Any) -> None:
        contexts.append(ctx.is_error)

    tool = AgentTool(name="t", execute=execute, execution_mode=_mode(sequential))
    run = await start(tmp_path, [("c1", "t")], [tool], after_tool_call=after)
    seen: list[bool] = []

    def redact(event: Any, _ctx: Any) -> ToolResultPatch | None:
        seen.append(event.is_error)
        if event.is_error:
            return ToolResultPatch(content=[TextContent(text="REDACTED")], details={})
        return None

    run.harness.hooks.on("tool_result", redact)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    assert seen == [True]
    assert contexts == [True]
    assert results_of(await run.reopened()) == {"c1": ("REDACTED", True)}
    wire = results_of(await run.next_request())
    assert "SECRET" not in str(wire)
    assert wire["c1"] == ("REDACTED", True)


@pytest.mark.parametrize("arguments", [(), (None,), ("just some text",)])
@pytest.mark.parametrize("sequential", [False, True])
async def test_a_tool_cancelled_with_no_result_gets_the_default_text(
    tmp_path: Path, sequential: bool, arguments: tuple[Any, ...]
) -> None:
    reached = asyncio.Event()

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        try:
            reached.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError as cancel:
            raise ToolCancelled(*arguments) from cancel
        return ToolResult(content=[])

    tools = [
        finished_tool("one", "ONE-RESULT", sequential=sequential),
        AgentTool(name="t", execute=execute, execution_mode=_mode(sequential)),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "t")], tools)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: "c1" in run.ended())
    await run.cancel()

    assert results_of(await run.reopened()) == {
        "c1": ("ONE-RESULT", False),
        "c2": (_CUT_TEXT, True),
    }
