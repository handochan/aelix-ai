"""#698 (ADR-0290) — a raised cancel count alone stops nothing.

On 3.11 and 3.12 a stdlib ``TaskGroup`` whose child fails after its body leaves the count
of the task it ran on one up, with no cancel behind it, and the turn died of that with a
traceback. Rows on the ``stale`` fixture run with a real ``TaskGroup`` (a stale count only
below 3.13), a simulation that is stale on every version, and the shape of aiohttp <= 3.10.5.
Rows on ``stale_or_not`` run with no stale count and the first two, and the limit rows with
the one shape they name. What still stops a turn: a cancel delivered to the loop, and one the
harness says it sent, swallowed or not.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.agent import Agent, AgentOptions
from aelix_agent_core.harness.core import AgentHarnessError
from aelix_agent_core.loop import _CUT_TEXT, agent_loop
from aelix_agent_core.types import AgentContext, AgentLoopConfig, AgentState, AgentTool
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, TextDeltaEvent
from aelix_ai.tools import ToolCancelled, ToolExecutionContext, ToolResult
from aelix_coding_agent import model_refresh
from aelix_coding_agent.model_refresh import ModelCatalogRefresher

from tests.harness._cancelled_turn import (
    MODEL,
    NOT_STARTED,
    Provider,
    Run,
    finished_tool,
    held_tool,
    results_of,
    settle,
    start,
    text_of,
    until,
)


def _mode(sequential: bool) -> str | None:
    return "sequential" if sequential else None


# === What leaves a stale count ==============================================


async def _simulated() -> None:
    """On every version: a cancel caught and gone on from with no ``uncancel()``."""

    task = asyncio.current_task()
    assert task is not None
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.sleep(0)


async def _task_group() -> None:
    """The real thing: a child fails after the body has finished, and that is caught."""

    async def fails() -> None:
        await asyncio.sleep(0)
        raise ValueError("child failed")

    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(fails())
    except* ValueError:
        pass


async def _timeout() -> None:
    """aiohttp <= 3.10.5's shape: its timer cancels the task, ``TimeoutError`` comes out."""

    task = asyncio.current_task()
    assert task is not None
    asyncio.get_running_loop().call_soon(task.cancel)
    try:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise TimeoutError from None
    except TimeoutError:
        pass


_MAKERS = {"simulated": _simulated, "task_group": _task_group, "timeout": _timeout}


class Stale:
    """Leaves the count up on the task it runs on, and says whether it really did."""

    def __init__(self, how: str) -> None:
        self.how = how
        self.rises: list[int] = []

    @property
    def expected_rise(self) -> int:
        if self.how == "none" or (self.how == "task_group" and sys.version_info >= (3, 13)):
            return 0
        return 1

    async def leave(self) -> None:
        task = asyncio.current_task()
        assert task is not None
        before = task.cancelling()
        if self.how != "none":
            await _MAKERS[self.how]()
        self.rises.append(task.cancelling() - before)

    def check(self) -> None:
        assert self.rises, "the stale count was never made"
        assert set(self.rises) == {self.expected_rise}, self.rises


@pytest.fixture(params=list(_MAKERS))
def stale(request: pytest.FixtureRequest) -> Iterator[Stale]:
    made = Stale(request.param)
    yield made
    made.check()


@pytest.fixture(params=["none", "simulated", "task_group"])
def stale_or_not(request: pytest.FixtureRequest) -> Iterator[Stale]:
    """The control row: with no stale count this is what the base does."""

    made = Stale(request.param)
    yield made
    made.check()


def _stale_tool(name: str, stale: Stale, *, sequential: bool = True) -> AgentTool:
    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        await stale.leave()
        return ToolResult(content=[TextContent(text=name.upper())])

    return AgentTool(name=name, execute=execute, execution_mode=_mode(sequential))


def _never(name: str, *, sequential: bool = True) -> AgentTool:
    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        raise AssertionError(f"{name} ran")

    return AgentTool(name=name, execute=execute, execution_mode=_mode(sequential))


def _leave_on_tool_call(run: Run, stale: Stale, *, tool: str = "one") -> None:
    async def hook(event: Any, _ctx: Any) -> None:
        if event.tool_name == tool:
            await stale.leave()

    run.harness.hooks.on("tool_call", hook)


def _streaming(run: Run) -> Any:
    """The provider's replies, with a start and an update for a listener to run in."""

    async def stream(_model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        run.provider.seen.append(list(context.messages))
        if len(run.provider.seen) == 1:
            message = AssistantMessage(content=list(run.provider.calls), stop_reason="tool_use")
        else:
            message = AssistantMessage(content=[TextContent(text="answer")])
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield TextDeltaEvent(delta="x")
        yield AssistantEndEvent(message=message)

    return stream


async def _cancel(run: Run, way: str) -> None:
    if way == "abort_until_idle":
        assert run.task is not None
        await run.harness.abort_until_idle()
        await settle(run.task)
    else:
        await run.cancel(way)


def _ended_as(run: Run, way: str) -> None:
    assert run.task is not None
    if way == "outer_cancel":
        assert run.task.cancelled(), "a cancel of prompt() comes back out of prompt()"
    else:
        assert not run.task.cancelled()
        assert run.harness.state.messages[-1].stop_reason == "aborted"
    assert run.harness.phase == "idle"


_WAYS = ["abort", "dispose", "outer_cancel", "abort_until_idle"]


# === A stale count stops nothing ============================================

_HOOKS = {"tool_call", "tool_result", "context"}
_RESULT_EVENTS = {"message_start", "message_end"}
_WHERE = [
    "tool",
    "tool_call",
    "tool_result",
    "context",
    "message_update",
    "tool_execution_start",
    "tool_execution_end",
    "message_start",
    "message_end",
    "turn_end",
]


@pytest.mark.parametrize("where", _WHERE)
@pytest.mark.parametrize("sequential", [True, False])
async def test_a_stale_count_stops_nothing_wherever_it_was_left(
    tmp_path: Path, stale: Stale, sequential: bool, where: str
) -> None:
    """The next call runs, the next request goes out and the answer arrives. Nothing is aborted."""

    first = (
        _stale_tool("one", stale, sequential=sequential)
        if where == "tool"
        else finished_tool("one", "ONE", sequential=sequential)
    )
    tools = [first, finished_tool("two", "TWO", sequential=sequential)]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    run.harness._options.stream_fn = _streaming(run)
    fired = {"done": False}

    async def hook(_event: Any, _ctx: Any) -> None:
        if not fired["done"]:
            fired["done"] = True
            await stale.leave()

    async def listener(event: Any) -> None:
        if event.type != where or fired["done"]:
            return
        if where in _RESULT_EVENTS and not isinstance(event.message, ToolResultMessage):
            return
        fired["done"] = True
        await stale.leave()

    if where in _HOOKS:
        run.harness.hooks.on(where, hook)
    elif where != "tool":
        run.harness.subscribe(listener)

    await settle(run.begin())

    assert run.task is not None and not run.task.cancelled()
    assert len(run.provider.seen) == 2, "the next request goes out"
    reopened = await run.reopened()
    assert results_of(reopened) == {"c1": ("ONE", False), "c2": ("TWO", False)}
    assert text_of(reopened[-1]) == "answer", "and its answer is in the file"
    assert run.harness.state.messages[-1].stop_reason != "aborted"
    assert run.types("agent_end") == ["agent_end"]
    assert run.harness.phase == "idle"


@pytest.mark.parametrize("hook_name", ["input", "before_agent_start"])
@pytest.mark.parametrize("then", ["answers", "abort", "outer_cancel_swallowed"])
async def test_a_stale_count_on_the_prompts_own_task_is_under_the_harnesss_read(
    tmp_path: Path, stale: Stale, hook_name: str, then: str
) -> None:
    """These hooks run on ``prompt()``'s task, ahead of the read: the turn runs, Esc still works."""

    reached = asyncio.Event()

    async def swallows(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ToolResult(content=[TextContent(text="CLEANUP DONE")])
        return ToolResult(content=[])

    if then == "answers":
        tools = [finished_tool("one", "ONE", sequential=True), _never("never", sequential=True)]
        calls = [("c1", "one")]
    elif then == "abort":
        tools = [held_tool("one", reached, sequential=True), _never("never")]
        calls = [("c1", "one"), ("c2", "never")]
    else:
        tools = [AgentTool(name="one", execute=swallows, execution_mode="sequential")]
        tools.append(_never("never"))
        calls = [("c1", "one"), ("c2", "never")]
    run = await start(tmp_path, calls, tools)
    on_its_task: list[bool] = []

    async def hook(_event: Any, _ctx: Any) -> None:
        on_its_task.append(asyncio.current_task() is run.task)
        await stale.leave()

    run.harness.hooks.on(hook_name, hook)
    task = run.begin()

    if then == "answers":
        await settle(task)
        assert not task.cancelled()
        assert len(run.provider.seen) == 2
        assert run.harness.state.messages[-1].stop_reason != "aborted"
    elif then == "abort":
        await asyncio.wait_for(reached.wait(), timeout=5)
        await run.cancel("abort")
        assert not task.cancelled(), "an Esc returns from prompt()"
        assert run.harness.state.messages[-1].stop_reason == "aborted"
        assert results_of(await run.reopened()) == {"c1": (_CUT_TEXT, True)}
    else:
        await asyncio.wait_for(reached.wait(), timeout=5)
        await run.cancel("outer_cancel")
        assert task.cancelled()
        assert run.started() == ["c1"]
        assert results_of(await run.reopened()) == {"c1": ("CLEANUP DONE", False)}
    assert on_its_task == [True]
    assert run.harness.phase == "idle"


# === A real cancel with a stale count already there =========================


@pytest.mark.parametrize("way", _WAYS)
async def test_a_real_cancel_after_a_stale_count_cuts_the_next_call_and_stops_the_batch(
    tmp_path: Path, stale: Stale, way: str
) -> None:
    reached = asyncio.Event()
    tools = [
        _stale_tool("one", stale),
        held_tool("two", reached, sequential=True),
        _never("three"),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two"), ("c3", "three")], tools)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await _cancel(run, way)

    _ended_as(run, way)
    assert run.started() == ["c1", "c2"]
    assert len(run.provider.seen) == 1
    assert results_of(await run.reopened()) == {"c1": ("ONE", False), "c2": (_CUT_TEXT, True)}


async def test_the_prompt_after_an_esc_with_a_stale_count_runs(
    tmp_path: Path, stale: Stale
) -> None:
    """Whatever said this turn was cancelled says nothing about the next one."""

    reached = asyncio.Event()
    tools = [_stale_tool("one", stale), held_tool("two", reached, sequential=True)]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel("abort")

    wire = results_of(await run.next_request())

    assert wire == {"c1": ("ONE", False), "c2": (_CUT_TEXT, True)}
    assert text_of(run.harness.state.messages[-1]) == "answer"


@pytest.mark.parametrize("way", _WAYS)
@pytest.mark.parametrize("swallowed_by", ["tool", "tool_call", "tool_execution_end", "message_end"])
async def test_a_cancel_swallowed_after_a_stale_count_still_stops_the_batch(
    tmp_path: Path, stale: Stale, swallowed_by: str, way: str
) -> None:
    """The count cannot say this, it was up already. Whoever sent the cancel does."""

    in_it = asyncio.Event()

    async def swallow() -> None:
        in_it.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()

    async def two(_args: Any, _ctx: Any) -> ToolResult:
        if swallowed_by == "tool":
            await swallow()
        return ToolResult(content=[TextContent(text="TWO")])

    tools = [
        _stale_tool("one", stale),
        AgentTool(name="two", execute=two, execution_mode="sequential"),
        _never("three"),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two"), ("c3", "three")], tools)

    async def hook(event: Any, _ctx: Any) -> None:
        if event.tool_name == "two":
            await swallow()

    async def listener(event: Any) -> None:
        if event.type != swallowed_by or in_it.is_set():
            return
        message = getattr(event, "message", None)
        if getattr(event, "tool_call_id", getattr(message, "tool_call_id", None)) == "c2":
            await swallow()

    if swallowed_by == "tool_call":
        run.harness.hooks.on("tool_call", hook)
    elif swallowed_by != "tool":
        run.harness.subscribe(listener)
    run.begin()
    await asyncio.wait_for(in_it.wait(), timeout=5)
    await _cancel(run, way)

    _ended_as(run, way)
    assert run.started() == ["c1", "c2"], "the later call stays unstarted"
    assert len(run.provider.seen) == 1, "no further request goes out"


@pytest.mark.parametrize("way", _WAYS)
async def test_a_real_cancel_after_a_stale_count_on_the_parallel_path(
    tmp_path: Path, stale: Stale, way: str
) -> None:
    """The stale count is the turn's, from Phase 1: the calls begin, and a cancel cuts them."""

    reached = asyncio.Event()
    tools = [held_tool("one", reached), finished_tool("two", "TWO")]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    _leave_on_tool_call(run, stale)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: "c2" in run.ended())
    await _cancel(run, way)

    _ended_as(run, way)
    assert len(run.provider.seen) == 1
    assert results_of(await run.reopened()) == {"c1": (_CUT_TEXT, True), "c2": ("TWO", False)}


async def test_a_cancel_swallowed_by_the_last_prepare_keeps_a_parallel_batch_from_starting(
    tmp_path: Path, stale: Stale
) -> None:
    """No check follows Phase 1's last hook but each call's first step, which reads the harness."""

    in_hook = asyncio.Event()
    tools = [_never("one", sequential=False), _never("two", sequential=False)]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    _leave_on_tool_call(run, stale)

    async def swallow(event: Any, _ctx: Any) -> None:
        if event.tool_name == "two":
            in_hook.set()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.Event().wait()

    run.harness.hooks.on("tool_call", swallow)
    run.begin()
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    await run.cancel("abort")

    assert results_of(await run.reopened()) == {}
    assert run.harness.state.messages[-1].stop_reason == "aborted"


async def test_one_esc_with_a_stale_count_is_not_read_as_a_second(
    tmp_path: Path, stale: Stale
) -> None:
    """A second cancel gives a call 50 ms and answers for it. This cleanup takes 300."""

    reached = asyncio.Event()

    async def cleans_up(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)
            return ToolResult(content=[TextContent(text="CLEANED UP")])
        return ToolResult(content=[])

    run = await start(tmp_path, [("c1", "one")], [AgentTool(name="one", execute=cleans_up)])
    _leave_on_tool_call(run, stale)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel("abort")

    assert results_of(await run.reopened()) == {"c1": ("CLEANED UP", False)}
    assert run.harness.state.messages[-1].stop_reason == "aborted"


# === Not a cancel, stale count or no =========================================


@pytest.mark.parametrize("kind", ["bare", "tool_cancelled"])
@pytest.mark.parametrize("sequential", [True, False])
async def test_a_cancel_nobody_sent_stays_a_tool_error_with_a_stale_count(
    tmp_path: Path, stale: Stale, sequential: bool, kind: str
) -> None:
    async def raises(_args: Any, _ctx: Any) -> ToolResult:
        if kind == "bare":
            raise asyncio.CancelledError()
        raise ToolCancelled(ToolResult(content=[TextContent(text="SAID THIS")]))

    first = (
        _stale_tool("one", stale) if sequential else finished_tool("one", "ONE", sequential=False)
    )
    tools = [first, AgentTool(name="raises", execute=raises, execution_mode=_mode(sequential))]
    run = await start(tmp_path, [("c1", "one"), ("c2", "raises")], tools)
    if not sequential:
        _leave_on_tool_call(run, stale)
    await settle(run.begin())

    text = "Tool 'raises' raised CancelledError" if kind == "bare" else "SAID THIS"
    assert results_of(await run.reopened()) == {"c1": ("ONE", False), "c2": (text, True)}
    assert len(run.provider.seen) == 2, "the model reads the error and answers"
    assert run.harness.state.messages[-1].stop_reason != "aborted"


@pytest.mark.parametrize(
    "broken", ["tool_execution_update", "tool_execution_end", "message_start", "message_end"]
)
@pytest.mark.parametrize("sequential", [True, False])
async def test_a_bare_cancelled_error_from_a_sink_with_a_stale_count_is_no_cancel(
    stale: Stale, sequential: bool, broken: str
) -> None:
    """As with the count at zero: a sink raising it costs what it did and stops nothing."""

    seen: list[Any] = []

    async def stream(_model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        seen.append(context.messages)
        if len(seen) > 1:
            yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="answer")]))
            return
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[ToolCallContent(tool_call_id="c1", tool_name="one", input={})],
                stop_reason="tool_use",
            )
        )

    async def emit(event: Any) -> None:
        if event.type == broken and (
            broken.startswith("tool_execution")
            or isinstance(getattr(event, "message", None), ToolResultMessage)
        ):
            raise asyncio.CancelledError()

    async def body() -> None:
        await stale.leave()
        await agent_loop(
            [UserMessage(content=[TextContent(text="go")])],
            AgentContext(system_prompt="", messages=[], tools=[_partial_tool()]),
            AgentLoopConfig(
                model=MODEL,
                convert_to_llm=lambda messages: messages,
                tool_execution="sequential" if sequential else "parallel",
            ),
            emit=emit,
            stream_fn=stream,
        )

    await asyncio.ensure_future(body())
    assert len(seen) == 2


@pytest.mark.parametrize("sequential", [True, False])
async def test_a_bare_cancelled_error_from_after_tool_call_with_a_stale_count_is_no_cancel(
    tmp_path: Path, stale_or_not: Stale, sequential: bool
) -> None:
    """Its output is withheld, as it would be with the count at zero, and the model answers."""

    async def after(_ctx: Any) -> None:
        raise asyncio.CancelledError()

    tools = [_stale_tool("one", stale_or_not, sequential=sequential)]
    run = await start(tmp_path, [("c1", "one")], tools, after_tool_call=after)
    await settle(run.begin())

    assert run.task is not None and not run.task.cancelled()
    assert results_of(await run.reopened()) == {"c1": (_CUT_TEXT, True)}
    assert len(run.provider.seen) == 2, "the model reads it and answers"
    assert run.harness.state.messages[-1].stop_reason != "aborted"


@pytest.mark.parametrize("sequential", [True, False])
async def test_a_hook_failing_with_a_stale_count_reaches_the_caller_as_with_none(
    tmp_path: Path, stale_or_not: Stale, sequential: bool
) -> None:
    """Nothing is withheld that would not be: it used to read as a failure during a cancel."""

    async def after(ctx: Any) -> None:
        if ctx.tool_call.tool_call_id == "c1":
            raise AgentHarnessError("hook", "the redactor broke")

    tools = [_stale_tool("one", stale_or_not, sequential=sequential)]
    run = await start(tmp_path, [("c1", "one")], tools, after_tool_call=after)
    task = run.begin()
    await asyncio.wait([task], timeout=5)

    with pytest.raises(AgentHarnessError, match="the redactor broke"):
        task.result()
    assert results_of(await run.reopened()) == {}
    assert run.events[-1].type == "agent_end"
    wire = results_of(await run.next_request())
    assert wire == {"c1": (NOT_STARTED, True)}


_SINKS = ["tool_execution_update", "tool_execution_end", "message_start", "message_end"]


def _breaking_sink(broken: str) -> Any:
    async def emit(event: Any) -> None:
        if event.type == broken and (
            broken.startswith("tool_execution")
            or isinstance(getattr(event, "message", None), ToolResultMessage)
        ):
            raise RuntimeError(f"sink broke on {event.type}")

    return emit


def _one_batch_then_text() -> Any:
    """Text after the batch, so that a failure swallowed here fails a test and spins no turn."""

    replies = {"count": 0}

    async def stream(_model: Any, _context: Any, _options: Any) -> AsyncIterator[Any]:
        replies["count"] += 1
        content: list[Any] = [ToolCallContent(tool_call_id="c1", tool_name="one", input={})]
        if replies["count"] > 1:
            content = [TextContent(text="answer")]
        yield AssistantEndEvent(message=AssistantMessage(content=content, stop_reason="tool_use"))

    return stream


def _partial_tool(before: Any = None) -> AgentTool:
    async def execute(_args: Any, ctx: ToolExecutionContext) -> ToolResult:
        if before is not None:
            await before()
        if ctx.on_partial is not None:
            ctx.on_partial(ToolResult(content=[TextContent(text="PARTIAL")]))
        return ToolResult(content=[TextContent(text="ONE")])

    return AgentTool(name="one", execute=execute)


@pytest.mark.parametrize("broken", _SINKS)
@pytest.mark.parametrize("sequential", [True, False])
async def test_a_sink_failing_with_a_stale_count_reaches_the_caller_as_with_none(
    stale_or_not: Stale, sequential: bool, broken: str
) -> None:
    async def body() -> None:
        await stale_or_not.leave()
        await agent_loop(
            [UserMessage(content=[TextContent(text="go")])],
            AgentContext(system_prompt="", messages=[], tools=[_partial_tool()]),
            AgentLoopConfig(
                model=MODEL,
                convert_to_llm=lambda messages: messages,
                tool_execution="sequential" if sequential else "parallel",
            ),
            emit=_breaking_sink(broken),
            stream_fn=_one_batch_then_text(),
        )

    with pytest.raises(RuntimeError, match=f"sink broke on {broken}"):
        await asyncio.ensure_future(body())


@pytest.mark.parametrize("broken", _SINKS)
@pytest.mark.parametrize("sequential", [True, False])
async def test_a_sink_failing_on_a_cancelled_turn_does_not_replace_the_cancel(
    sequential: bool, broken: str
) -> None:
    """``cancel_requested`` says the turn was cancelled, and nothing delivered it on the way."""

    said = {"cancelled": False}
    committed: list[str] = []
    sink = _breaking_sink(broken)

    async def before() -> None:
        said["cancelled"] = True

    async def emit(event: Any) -> None:
        await sink(event)
        if event.type == "message_end" and isinstance(event.message, ToolResultMessage):
            committed.append(text_of(event.message))

    loop_run = agent_loop(
        [UserMessage(content=[TextContent(text="go")])],
        AgentContext(system_prompt="", messages=[], tools=[_partial_tool(before)]),
        AgentLoopConfig(
            model=MODEL,
            convert_to_llm=lambda messages: messages,
            tool_execution="sequential" if sequential else "parallel",
            cancel_requested=lambda: said["cancelled"],
        ),
        emit=emit,
        stream_fn=_one_batch_then_text(),
    )

    with pytest.raises(asyncio.CancelledError):
        await asyncio.ensure_future(loop_run)
    # The result is lost only where the commit itself broke (ADR-0290 §5).
    assert committed == ([] if broken == "message_end" else ["ONE"])


@pytest.mark.parametrize("broken", ["tool_execution_end", "message_start", "message_end"])
async def test_a_sink_failing_on_a_refused_call_of_a_cancelled_turn_does_not_replace_the_cancel(
    broken: str,
) -> None:
    """The sequential path's other branch: a call with no tool has a result and nothing to run."""

    said = {"cancelled": False}
    committed: list[bool] = []
    sink = _breaking_sink(broken)

    async def emit(event: Any) -> None:
        if event.type == "tool_execution_start":
            said["cancelled"] = True
        await sink(event)
        if event.type == "message_end" and isinstance(event.message, ToolResultMessage):
            committed.append(event.message.is_error)

    loop_run = agent_loop(
        [UserMessage(content=[TextContent(text="go")])],
        AgentContext(system_prompt="", messages=[], tools=[]),
        AgentLoopConfig(
            model=MODEL,
            convert_to_llm=lambda messages: messages,
            tool_execution="sequential",
            cancel_requested=lambda: said["cancelled"],
        ),
        emit=emit,
        stream_fn=_one_batch_then_text(),
    )

    with pytest.raises(asyncio.CancelledError):
        await asyncio.ensure_future(loop_run)
    assert committed == ([] if broken == "message_end" else [True])


async def test_a_failure_in_one_call_still_cuts_the_others_and_raises(
    tmp_path: Path, stale_or_not: Stale
) -> None:
    """ADR-0290's "A failure in one call": the wait cancels the sibling, then the failure goes up."""

    running, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def held(_args: Any, _ctx: Any) -> ToolResult:
        running.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(release.wait(), 12)
            raise
        return ToolResult(content=[])

    async def after(ctx: Any) -> None:
        if ctx.tool_call.tool_call_id == "c1":
            await until(lambda: "c2" in run.ended() and running.is_set())
            raise AgentHarnessError("hook", "the redactor broke")

    tools = [
        finished_tool("one", "SECRET"),
        finished_tool("two", "TWO"),
        AgentTool(name="held", execute=held),
    ]
    run = await start(
        tmp_path, [("c1", "one"), ("c2", "two"), ("c3", "held")], tools, after_tool_call=after
    )
    _leave_on_tool_call(run, stale_or_not)
    task = run.begin()
    await asyncio.wait_for(cleaning.wait(), timeout=5)
    assert not task.done(), "it waits for the call it cancelled"
    release.set()
    await asyncio.wait([task], timeout=5)

    with pytest.raises(AgentHarnessError, match="the redactor broke"):
        task.result()
    assert results_of(await run.reopened()) == {"c2": ("TWO", False), "c3": (_CUT_TEXT, True)}


async def test_a_hook_raising_in_a_call_the_failure_cut_is_withheld(tmp_path: Path) -> None:
    """A sibling takes the wait's cancel, swallows it and returns: the wait knows it sent one."""

    running, in_call = asyncio.Event(), asyncio.Event()

    async def swallows(_args: Any, _ctx: Any) -> ToolResult:
        running.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()
        in_call.set()
        return ToolResult(content=[TextContent(text="SECRET")])

    async def after(ctx: Any) -> None:
        if ctx.tool_call.tool_call_id == "c1":
            await until(running.is_set)
            raise AgentHarnessError("hook", "the original failure")
        raise AgentHarnessError("hook", "the sibling's")

    tools = [finished_tool("one", "ONE"), AgentTool(name="two", execute=swallows)]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools, after_tool_call=after)
    task = run.begin()
    await asyncio.wait([task], timeout=5)

    with pytest.raises(AgentHarnessError, match="the original failure"):
        task.result()
    assert results_of(await run.reopened()) == {"c2": (_CUT_TEXT, True)}
    assert in_call.is_set()


# === uncancel() (ADR-0290 limit 15) ==========================================


def _takes_it_back(reached: asyncio.Event, how: str, *, sequential: bool = True) -> AgentTool:
    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            assert task is not None
            task.uncancel()
            if how == "raises":
                raise
            return ToolResult(content=[TextContent(text="TOOK IT BACK")])
        return ToolResult(content=[])

    return AgentTool(name="one", execute=execute, execution_mode=_mode(sequential))


_TAKEN_BACK = {"returns": ("TOOK IT BACK", False), "raises": (_CUT_TEXT, True)}


@pytest.mark.parametrize("way", ["abort", "outer_cancel"])
@pytest.mark.parametrize("how", list(_TAKEN_BACK))
@pytest.mark.parametrize("sequential", [True, False])
async def test_a_tool_calling_uncancel_takes_no_cancel_back_from_the_harness(
    tmp_path: Path, sequential: bool, how: str, way: str
) -> None:
    reached = asyncio.Event()
    tools = [
        _takes_it_back(reached, how, sequential=sequential),
        _never("two", sequential=sequential),
    ]
    calls = [("c1", "one"), ("c2", "two")] if sequential else [("c1", "one")]
    run = await start(tmp_path, calls, tools)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel(way)

    _ended_as(run, way)
    assert run.started() == ["c1"]
    assert len(run.provider.seen) == 1
    assert results_of(await run.reopened()) == {"c1": _TAKEN_BACK[how]}


# === ctx.abort() (#545): no cancel, and it does not stop the batch ==========


@pytest.mark.parametrize("where", ["tool_call", "tool", "tool_execution_end"])
@pytest.mark.parametrize("sequential", [True, False])
async def test_ctx_abort_stops_no_batch_stale_count_or_not(
    tmp_path: Path, stale_or_not: Stale, sequential: bool, where: str
) -> None:
    """Measured on ``c7daf997``: every call runs and the next request goes out, flag set."""

    holder: dict[str, Run] = {}

    async def one(_args: Any, _ctx: Any) -> ToolResult:
        await stale_or_not.leave()
        if where == "tool":
            holder["run"].harness._mark_abort()
        return ToolResult(content=[TextContent(text="ONE")])

    tools = [
        AgentTool(name="one", execute=one, execution_mode=_mode(sequential)),
        finished_tool("two", "TWO", sequential=sequential),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    holder["run"] = run

    def hook(event: Any, _ctx: Any) -> None:
        if event.tool_name == "one":
            run.harness._mark_abort()

    def listener(event: Any) -> None:
        if event.type == "tool_execution_end":
            run.harness._mark_abort()

    if where == "tool_call":
        run.harness.hooks.on("tool_call", hook)
    elif where == "tool_execution_end":
        run.harness.subscribe(listener)
    await settle(run.begin())

    assert run.task is not None and not run.task.cancelled()
    assert run.harness._abort_requested
    assert run.started() == ["c1", "c2"]
    assert len(run.provider.seen) == 2
    assert results_of(await run.reopened()) == {"c1": ("ONE", False), "c2": ("TWO", False)}
    assert run.harness.state.messages[-1].stop_reason != "aborted"


@pytest.mark.parametrize("sequential", [True, False])
async def test_an_abort_made_on_the_turns_own_task_stops_the_batch(
    tmp_path: Path, stale_or_not: Stale, sequential: bool
) -> None:
    """``abort()`` from a hook: no await to hand the cancel over before the next read of it."""

    tools = [_never("one", sequential=sequential), _never("two", sequential=sequential)]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)

    async def hook(event: Any, _ctx: Any) -> None:
        if event.tool_name == "one":
            await stale_or_not.leave()
            await run.harness.abort()

    run.harness.hooks.on("tool_call", hook)
    await settle(run.begin())

    assert results_of(await run.reopened()) == {}
    assert len(run.provider.seen) == 1
    assert run.harness.state.messages[-1].stop_reason == "aborted"


# === Limits this leaves (ADR-0290) ==========================================


async def test_a_tool_leaving_a_stale_count_and_raising_the_error_itself_reads_as_cut(
    tmp_path: Path,
) -> None:
    """Limit 14. The rise is across this very call, and nothing tells the two apart."""

    stale = Stale("simulated")

    async def one(_args: Any, _ctx: Any) -> ToolResult:
        await stale.leave()
        raise asyncio.CancelledError()

    tools = [AgentTool(name="one", execute=one, execution_mode="sequential")]
    run = await start(tmp_path, [("c1", "one")], tools)
    await settle(run.begin())

    stale.check()
    assert run.task is not None and run.task.cancelled()
    assert results_of(await run.reopened()) == {"c1": (_CUT_TEXT, True)}


async def test_a_hook_cancelling_its_own_task_ahead_of_a_tool_reads_as_the_tools_error(
    tmp_path: Path,
) -> None:
    """Limit 14. Requested before the read at the tool, delivered at its first await."""

    async def one(_args: Any, _ctx: Any) -> ToolResult:
        await asyncio.sleep(0)
        return ToolResult(content=[TextContent(text="NEVER")])

    tools = [AgentTool(name="one", execute=one, execution_mode="sequential")]
    run = await start(tmp_path, [("c1", "one")], tools)

    def hook(_event: Any, _ctx: Any) -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()

    run.harness.hooks.on("tool_call", hook)
    await settle(run.begin())

    assert results_of(await run.reopened()) == {"c1": ("Tool 'one' raised CancelledError", True)}
    assert len(run.provider.seen) == 2


@pytest.mark.parametrize(
    ("sequential", "tool_awaits", "hook_awaits", "result", "requests"),
    [
        # The next await is the next request's first, and ``Provider`` counts ahead of that.
        (True, False, False, ("TOOL RAN", False), 2),
        (False, True, False, (_CUT_TEXT, True), 1),
        (False, False, False, ("TOOL RAN", False), 1),
        (True, True, True, None, 1),
        (False, True, True, None, 1),
    ],
)
async def test_a_hook_cancelling_its_own_task_ends_the_turn_where_it_is_next_delivered(
    tmp_path: Path,
    sequential: bool,
    tool_awaits: bool,
    hook_awaits: bool,
    result: Any,
    requests: int,
) -> None:
    """Limit 14. Under every baseline, so the call begins unless the hook itself awaits."""

    async def one(_args: Any, _ctx: Any) -> ToolResult:
        if tool_awaits:
            await asyncio.sleep(0)
        return ToolResult(content=[TextContent(text="TOOL RAN")])

    tools = [AgentTool(name="one", execute=one, execution_mode=_mode(sequential))]
    run = await start(tmp_path, [("c1", "one")], tools)

    async def hook(_event: Any, _ctx: Any) -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        if hook_awaits:
            await asyncio.sleep(0)

    run.harness.hooks.on("tool_call", hook)
    await settle(run.begin())

    assert run.task is not None and run.task.cancelled()
    assert results_of(await run.reopened()) == ({} if result is None else {"c1": result})
    assert len(run.provider.seen) == requests


@pytest.mark.parametrize("sequential", [True, False])
async def test_a_hook_cancelling_its_own_task_lets_the_batch_begin(
    tmp_path: Path, sequential: bool
) -> None:
    """Limit 14 and §3's exception. A sequential batch goes on; a parallel one has begun."""

    began: list[str] = []

    def tool(name: str) -> AgentTool:
        async def execute(_args: Any, _ctx: Any) -> ToolResult:
            began.append(name)
            await asyncio.sleep(0)
            return ToolResult(content=[TextContent(text=name.upper())])

        return AgentTool(name=name, execute=execute, execution_mode=_mode(sequential))

    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], [tool("one"), tool("two")])
    last_prepared = "one" if sequential else "two"

    def hook(event: Any, _ctx: Any) -> None:
        if event.tool_name == last_prepared:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()

    run.harness.hooks.on("tool_call", hook)
    await settle(run.begin())

    assert began == ["one", "two"]
    assert run.task is not None
    if sequential:
        assert not run.task.cancelled()
        assert len(run.provider.seen) == 2
        assert results_of(await run.reopened()) == {
            "c1": ("Tool 'one' raised CancelledError", True),
            "c2": ("TWO", False),
        }
    else:
        assert run.task.cancelled()
        assert len(run.provider.seen) == 1
        assert results_of(await run.reopened()) == {"c1": (_CUT_TEXT, True), "c2": (_CUT_TEXT, True)}


@pytest.mark.parametrize("hook_name", ["input", "before_agent_start"])
@pytest.mark.parametrize("sent_by", ["cancel", "timeout"])
async def test_a_cancel_of_prompt_a_hook_swallows_before_the_turn_begins_goes_on(
    tmp_path: Path, hook_name: str, sent_by: str
) -> None:
    """Limit 20, as on the base. It is under the read a stale count from these hooks has to be under."""

    run = await start(tmp_path, [("c1", "one")], [finished_tool("one", "ONE")])
    parked = asyncio.Event()

    async def hook(_event: Any, _ctx: Any) -> None:
        parked.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()

    run.harness.hooks.on(hook_name, hook)

    async def under_a_timeout() -> None:
        scope = asyncio.timeout(None)
        loop = asyncio.get_running_loop()

        async def expire_once_parked() -> None:
            await parked.wait()
            scope.reschedule(loop.time())

        watcher = asyncio.ensure_future(expire_once_parked())
        try:
            async with scope:
                await run.harness.prompt("KEEP")
        finally:
            watcher.cancel()

    if sent_by == "cancel":
        task = run.begin()
        await asyncio.wait_for(parked.wait(), timeout=5)
        task.cancel()
    else:
        task = asyncio.ensure_future(under_a_timeout())
    await settle(task)

    assert not task.cancelled()
    assert len(run.provider.seen) == 2
    assert results_of(await run.reopened()) == {"c1": ("ONE", False)}


# === Agent and a direct agent_loop: no harness to say so ====================


class _AgentRun:
    def __init__(
        self, tools: list[AgentTool], calls: list[tuple[str, str]], **options: Any
    ) -> None:
        self.provider = Provider(calls)
        self.agent = Agent(
            AgentOptions(
                initial_state=AgentState(model=MODEL, tools=tools),
                stream_fn=self.provider,
                **options,
            )
        )
        self.events: list[Any] = []
        self.agent.subscribe(self.events.append)

    def started(self) -> list[str]:
        return [e.tool_call_id for e in self.events if e.type == "tool_execution_start"]

    def committed(self) -> list[str]:
        return [
            e.message.tool_call_id
            for e in self.events
            if e.type == "message_end" and isinstance(e.message, ToolResultMessage)
        ]


async def test_agent_a_stale_count_on_the_callers_task_before_the_run_stops_nothing(
    stale: Stale,
) -> None:
    """ADR-0290's old limit 17: a host's next prompt got ``CancelledError`` with no request."""

    run = _AgentRun(
        [finished_tool("one", "ONE"), finished_tool("two", "TWO")],
        [("c1", "one"), ("c2", "two")],
    )

    async def body() -> list[Any]:
        await stale.leave()
        return await run.agent.prompt("go")

    out = await asyncio.ensure_future(body())

    assert len(run.provider.seen) == 2
    assert text_of(out[-1]) == "answer"


@pytest.mark.parametrize("sequential", [True, False])
async def test_agent_a_stale_count_from_a_tool_or_a_listener_stops_nothing(
    stale: Stale, sequential: bool
) -> None:
    run = _AgentRun(
        [
            _stale_tool("one", stale, sequential=sequential),
            finished_tool("two", "TWO", sequential=sequential),
        ],
        [("c1", "one"), ("c2", "two")],
    )
    fired = {"done": False}

    async def listener(event: Any) -> None:
        if event.type == "message_end" and not fired["done"]:
            fired["done"] = True
            await stale.leave()

    run.agent.subscribe(listener)
    out = await asyncio.ensure_future(run.agent.prompt("go"))

    assert run.started() == ["c1", "c2"]
    assert len(run.provider.seen) == 2
    assert text_of(out[-1]) == "answer"


@pytest.mark.parametrize("sequential", [True, False])
async def test_agent_a_real_cancel_of_the_caller_during_a_tool_stops_and_raises(
    stale_or_not: Stale, sequential: bool
) -> None:
    reached = asyncio.Event()
    run = _AgentRun(
        [held_tool("one", reached, sequential=sequential), _never("two", sequential=sequential)],
        [("c1", "one")] if not sequential else [("c1", "one"), ("c2", "two")],
    )

    async def body() -> None:
        await stale_or_not.leave()
        await run.agent.prompt("go")

    task = asyncio.ensure_future(body())
    await asyncio.wait_for(reached.wait(), timeout=5)
    task.cancel()
    await settle(task)

    assert task.cancelled()
    assert run.started() == ["c1"]
    assert run.committed() == ["c1"], "the cut call keeps a record"
    assert len(run.provider.seen) == 1


_SITES = [
    "tool_execution_update",
    "after_tool_call",
    "tool_execution_end",
    "message_start",
    "message_end",
]


@pytest.mark.parametrize("site", _SITES)
@pytest.mark.parametrize("sequential", [True, False])
async def test_agent_a_cancel_the_loop_caught_on_its_way_is_remembered(
    stale_or_not: Stale, sequential: bool, site: str
) -> None:
    """Each place the loop catches a cancel and goes on. No harness says so; this is the only record."""

    parked = asyncio.Event()

    async def park() -> None:
        parked.set()
        await asyncio.Event().wait()

    async def one(_args: Any, ctx: ToolExecutionContext) -> ToolResult:
        if site == "tool_execution_update" and ctx.on_partial is not None:
            ctx.on_partial(ToolResult(content=[TextContent(text="PARTIAL")]))
        return ToolResult(content=[TextContent(text="ONE")])

    async def after(_ctx: Any) -> None:
        if site == "after_tool_call" and not parked.is_set():
            await park()

    tools = [
        AgentTool(name="one", execute=one, execution_mode=_mode(sequential)),
        finished_tool("two", "TWO", sequential=sequential),
    ]
    run = _AgentRun(tools, [("c1", "one"), ("c2", "two")], after_tool_call=after)

    async def listener(event: Any) -> None:
        if event.type != site or parked.is_set():
            return
        if site.startswith("tool_execution") or isinstance(event.message, ToolResultMessage):
            await park()

    run.agent.subscribe(listener)

    async def body() -> None:
        await stale_or_not.leave()
        await run.agent.prompt("go")

    task = asyncio.ensure_future(body())
    await asyncio.wait_for(parked.wait(), timeout=5)
    await asyncio.sleep(0)
    task.cancel()
    await settle(task)

    assert task.cancelled(), "the cancel goes on up"
    assert len(run.provider.seen) == 1, "no further request goes out"
    if sequential:
        assert run.started() == ["c1"], "the later call stays unstarted"


async def test_agent_a_tool_swallowing_a_cancel_on_the_sequential_path_goes_undetected() -> None:
    """The limit: the count alone is all that is left of it, and it stops nothing."""

    reached = asyncio.Event()

    async def swallows(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()
        return ToolResult(content=[TextContent(text="CLEANUP DONE")])

    run = _AgentRun(
        [
            AgentTool(name="one", execute=swallows, execution_mode="sequential"),
            finished_tool("two", "TWO", sequential=True),
        ],
        [("c1", "one"), ("c2", "two")],
    )
    task = asyncio.ensure_future(run.agent.prompt("go"))
    await asyncio.wait_for(reached.wait(), timeout=5)
    task.cancel()
    await settle(task)

    assert not task.cancelled()
    assert run.started() == ["c1", "c2"]
    assert len(run.provider.seen) == 2


async def test_agent_a_tool_swallowing_a_cancel_on_the_parallel_path_stops_it() -> None:
    """There the cancel lands in the wait, which the loop owns, and the tool takes the next one."""

    reached = asyncio.Event()

    async def swallows(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()
        return ToolResult(content=[TextContent(text="CLEANUP DONE")])

    run = _AgentRun([AgentTool(name="one", execute=swallows)], [("c1", "one")])
    task = asyncio.ensure_future(run.agent.prompt("go"))
    await asyncio.wait_for(reached.wait(), timeout=5)
    task.cancel()
    await settle(task)

    assert task.cancelled()
    assert len(run.provider.seen) == 1


_ON_THE_CALLERS_TASK = {"before_tool_call", "tool_execution_start", "message_end"}


@pytest.mark.parametrize(
    "who", [*_ON_THE_CALLERS_TASK, "tool", "tool_execution_end", "after_tool_call"]
)
@pytest.mark.parametrize("sequential", [True, False])
async def test_agent_a_swallowed_cancel_stops_only_where_the_loop_saw_it(
    sequential: bool, who: str
) -> None:
    """Limit 19. What runs on the caller's task hides it; a parallel call's task has the wait above it."""

    reached = asyncio.Event()
    fired = {"done": False}

    async def swallow() -> None:
        fired["done"] = True
        reached.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()

    async def one(_args: Any, _ctx: Any) -> ToolResult:
        if who == "tool":
            await swallow()
        return ToolResult(content=[TextContent(text="ONE")])

    async def hook(_ctx: Any) -> None:
        if not fired["done"]:
            await swallow()

    hooks: dict[str, Any] = {who: hook} if who in ("before_tool_call", "after_tool_call") else {}
    run = _AgentRun(
        [
            AgentTool(name="one", execute=one, execution_mode=_mode(sequential)),
            finished_tool("two", "TWO", sequential=sequential),
        ],
        [("c1", "one"), ("c2", "two")],
        **hooks,
    )

    async def listener(event: Any) -> None:
        if fired["done"] or event.type != who:
            return
        if who != "message_end" or isinstance(event.message, ToolResultMessage):
            await swallow()

    run.agent.subscribe(listener)
    task = asyncio.ensure_future(run.agent.prompt("go"))
    await asyncio.wait_for(reached.wait(), timeout=5)
    await asyncio.sleep(0)
    task.cancel()
    await settle(task)

    if sequential or who in _ON_THE_CALLERS_TASK:
        assert not task.cancelled()
        assert len(run.provider.seen) == 2
    else:
        assert task.cancelled()
        assert len(run.provider.seen) == 1


@pytest.mark.parametrize("how", ["returns", "raises"])
async def test_agent_a_tool_calling_uncancel_takes_the_cancel_back(how: str) -> None:
    reached = asyncio.Event()
    run = _AgentRun(
        [_takes_it_back(reached, how), finished_tool("two", "TWO", sequential=True)],
        [("c1", "one"), ("c2", "two")],
    )
    task = asyncio.ensure_future(run.agent.prompt("go"))
    await asyncio.wait_for(reached.wait(), timeout=5)
    task.cancel()
    await settle(task)

    assert not task.cancelled()
    assert run.started() == ["c1", "c2"]
    assert len(run.provider.seen) == 2
    texts = [
        text_of(e.message)
        for e in run.events
        if e.type == "message_end" and isinstance(e.message, ToolResultMessage)
    ]
    assert texts[0] == ("TOOK IT BACK" if how == "returns" else "Tool 'one' raised CancelledError")


async def test_agent_a_cancel_the_loop_caught_does_not_outlive_its_run() -> None:
    """``Agent`` runs in its caller's task, so the next prompt shares the record, and the count."""

    parked = asyncio.Event()

    async def listener(event: Any) -> None:
        if (
            event.type == "message_end"
            and isinstance(event.message, ToolResultMessage)
            and not parked.is_set()
        ):
            parked.set()
            await asyncio.Event().wait()

    run = _AgentRun([finished_tool("one", "ONE")], [("c1", "one")])
    run.agent.subscribe(listener)

    async def body() -> list[Any]:
        with contextlib.suppress(asyncio.CancelledError):
            await run.agent.prompt("first")
        task = asyncio.current_task()
        assert task is not None and task.cancelling() == 1, "caught with no uncancel()"
        return await run.agent.prompt("second")

    task = asyncio.ensure_future(body())
    await asyncio.wait_for(parked.wait(), timeout=5)
    task.cancel()
    out = await asyncio.wait_for(task, timeout=5)

    assert len(run.provider.seen) == 2
    assert text_of(out[-1]) == "answer"


# === The record of a caught cancel belongs to one run =======================


class _Inner:
    """A small ``Agent`` something asks on the task it runs on. Its tool waits to be cancelled."""

    def __init__(self, *, sequential: bool = True) -> None:
        self.reached = asyncio.Event()
        self.run = _AgentRun(
            [held_tool("inner", self.reached, sequential=sequential)], [("i1", "inner")]
        )


async def _inner_under_timeout(inner: _Inner) -> str:
    scope = asyncio.timeout(None)
    loop = asyncio.get_running_loop()

    async def expire_once_reached() -> None:
        await inner.reached.wait()
        scope.reschedule(loop.time())

    watcher = asyncio.ensure_future(expire_once_reached())
    try:
        async with scope:
            await inner.run.agent.prompt("ask")
    except TimeoutError:
        return "taken back"
    finally:
        watcher.cancel()
    return "answered"


async def _inner_under_wait_for(inner: _Inner) -> str:
    try:
        await asyncio.wait_for(inner.run.agent.prompt("ask"), 0.05)
    except TimeoutError:
        return "taken back"
    return "answered"


async def _inner_in_a_failing_group(inner: _Inner) -> str:
    async def fails() -> None:
        await inner.reached.wait()
        raise ValueError("watchdog")

    said = "answered"
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(fails())
            await inner.run.agent.prompt("ask")
    except* ValueError:
        said = "taken back"
    return said


_SCOPES = {
    "timeout": _inner_under_timeout,
    "wait_for": _inner_under_wait_for,
    "task_group": _inner_in_a_failing_group,
}


@pytest.mark.parametrize("inner_sequential", [True, False])
@pytest.mark.parametrize("scope", list(_SCOPES))
@pytest.mark.parametrize("where", ["tool", "hook", "listener"])
@pytest.mark.parametrize("sequential", [True, False])
@pytest.mark.parametrize("host", ["harness", "agent"])
async def test_a_nested_run_a_scope_took_the_cancel_back_from_stops_no_outer_turn(
    tmp_path: Path, host: str, sequential: bool, where: str, scope: str, inner_sequential: bool
) -> None:
    """The inner run caught that cancel on the shared task, and nobody cancelled the outer one."""

    inner = _Inner(sequential=inner_sequential)
    asked: list[str] = []

    async def ask() -> None:
        if not asked:
            asked.append("asking")
            asked[0] = await _SCOPES[scope](inner)

    async def one(_args: Any, _ctx: Any) -> ToolResult:
        if where == "tool":
            await ask()
        return ToolResult(content=[TextContent(text="ONE")])

    tools = [
        AgentTool(name="one", execute=one, execution_mode=_mode(sequential)),
        finished_tool("two", "TWO", sequential=sequential),
    ]
    calls = [("c1", "one"), ("c2", "two")]

    async def before(*_args: Any) -> None:
        if where == "hook":
            await ask()

    async def listener(event: Any) -> None:
        if where == "listener" and event.type == "tool_execution_start":
            await ask()

    if host == "harness":
        run = await start(tmp_path, calls, tools)
        run.harness.hooks.on("tool_call", before)
        run.harness.subscribe(listener)
        await settle(run.begin())
        assert run.task is not None and not run.task.cancelled()
        assert results_of(await run.reopened()) == {"c1": ("ONE", False), "c2": ("TWO", False)}
        assert len(run.provider.seen) == 2
        assert run.harness.state.messages[-1].stop_reason != "aborted"
    else:
        agent_run = _AgentRun(tools, calls, before_tool_call=before)
        agent_run.agent.subscribe(listener)
        out = await asyncio.wait_for(agent_run.agent.prompt("go"), timeout=10)
        assert agent_run.started() == ["c1", "c2"]
        assert agent_run.committed() == ["c1", "c2"]
        assert len(agent_run.provider.seen) == 2
        assert text_of(out[-1]) == "answer"

    assert asked == ["taken back"]
    if scope != "wait_for":  # no clock in the others: the inner call was really under way
        assert inner.reached.is_set()


_ERASERS = [
    ("tool", "after_tool_call"),
    ("tool", "message_end"),
    ("tool_execution_end", "message_end"),
]


@pytest.mark.parametrize(("caught_in", "nested_in"), _ERASERS)
@pytest.mark.parametrize("sequential", [True, False])
@pytest.mark.parametrize("host", ["harness", "agent"])
async def test_a_nested_run_does_not_erase_a_cancel_the_outer_run_caught(
    tmp_path: Path, host: str, sequential: bool, caught_in: str, nested_in: str
) -> None:
    """After the cancel, something on the way out asks a small ``Agent``, and it answers."""

    reached = asyncio.Event()
    inner = _AgentRun([], [])
    answers: list[str] = []

    async def ask() -> None:
        if not answers:
            answers.append("asking")
            await inner.agent.prompt("log it")
            answers[0] = "returned"

    async def one(_args: Any, _ctx: Any) -> ToolResult:
        if caught_in == "tool":
            reached.set()
            await asyncio.Event().wait()
        return ToolResult(content=[TextContent(text="ONE")])

    async def after(_ctx: Any) -> None:
        if nested_in == "after_tool_call":
            await ask()

    async def listener(event: Any) -> None:
        if (
            caught_in == "tool_execution_end"
            and event.type == "tool_execution_end"
            and not reached.is_set()
        ):
            reached.set()
            await asyncio.Event().wait()
        if (
            nested_in == "message_end"
            and event.type == "message_end"
            and isinstance(event.message, ToolResultMessage)
        ):
            await ask()

    tools = [
        AgentTool(name="one", execute=one, execution_mode=_mode(sequential)),
        _never("two", sequential=sequential),
    ]
    calls = [("c1", "one"), ("c2", "two")] if sequential else [("c1", "one")]

    if host == "harness":
        run = await start(tmp_path, calls, tools, after_tool_call=after)
        run.harness.subscribe(listener)
        run.begin()
        await asyncio.wait_for(reached.wait(), timeout=5)
        await asyncio.sleep(0)
        await run.cancel("abort")
        assert run.started() == ["c1"]
        assert len(run.provider.seen) == 1, "no further request goes out"
        assert run.harness.state.messages[-1].stop_reason == "aborted"
    else:
        agent_run = _AgentRun(tools, calls, after_tool_call=after)
        agent_run.agent.subscribe(listener)
        task = asyncio.ensure_future(agent_run.agent.prompt("go"))
        await asyncio.wait_for(reached.wait(), timeout=5)
        await asyncio.sleep(0)
        task.cancel()
        await settle(task)
        assert task.cancelled(), "the cancel goes on up"
        assert agent_run.started() == ["c1"]
        assert len(agent_run.provider.seen) == 1, "no further request goes out"

    assert answers == ["returned"], "the nested run is its own, and ran to its end"
    assert len(inner.provider.seen) == 1


# === A wait for a shared catalog refresh ====================================


@pytest.fixture
def _online(monkeypatch: pytest.MonkeyPatch) -> None:
    """``refresh`` returns ``None`` at once when offline, which would pass these for nothing."""

    monkeypatch.setattr(model_refresh, "refresh_allowed", lambda: True)


class _Registry:
    """A registry whose catalog run ends cancelled, or never, and which reads no network."""

    def __init__(self, *, ends_cancelled: bool) -> None:
        self.ends_cancelled = ends_cancelled
        self.ran = False

    async def refresh_models(self, **_kwargs: Any) -> None:
        self.ran = True
        await asyncio.sleep(0)
        if self.ends_cancelled:
            raise asyncio.CancelledError("the shared run")
        await asyncio.Event().wait()


@pytest.mark.usefixtures("_online")
async def test_refresh_a_shared_run_ending_cancelled_is_no_cancel_of_a_stale_caller(
    stale_or_not: Stale,
) -> None:
    registry = _Registry(ends_cancelled=True)

    async def body() -> Any:
        await stale_or_not.leave()
        return await ModelCatalogRefresher().refresh(registry, trigger="test")  # type: ignore[arg-type]

    assert await asyncio.ensure_future(body()) is None
    assert registry.ran


@pytest.mark.usefixtures("_online")
async def test_refresh_a_timeout_stays_a_timeout_with_a_stale_caller(stale_or_not: Stale) -> None:
    registry = _Registry(ends_cancelled=False)

    async def body() -> Any:
        await stale_or_not.leave()
        return await ModelCatalogRefresher().refresh(registry, trigger="test", timeout=0.05)  # type: ignore[arg-type]

    assert await asyncio.ensure_future(body()) is None
    assert registry.ran


@pytest.mark.usefixtures("_online")
async def test_refresh_a_cancel_of_a_stale_caller_goes_on_up(stale_or_not: Stale) -> None:
    registry = _Registry(ends_cancelled=False)

    async def body() -> None:
        await stale_or_not.leave()
        await ModelCatalogRefresher().refresh(registry, trigger="test")  # type: ignore[arg-type]

    task = asyncio.ensure_future(body())
    await until(lambda: registry.ran)
    await asyncio.sleep(0)
    task.cancel()
    await settle(task)

    assert task.cancelled()


@pytest.mark.usefixtures("_online")
async def test_a_tool_waiting_on_a_shared_refresh_that_ends_cancelled_lets_the_turn_go_on(
    tmp_path: Path, stale: Stale
) -> None:
    """The refresh said a cancel came and nobody had sent one: the loop stopped the turn on it."""

    refreshed: list[Any] = []

    async def one(_args: Any, _ctx: Any) -> ToolResult:
        await stale.leave()
        refresher = ModelCatalogRefresher()
        refreshed.append(await refresher.refresh(_Registry(ends_cancelled=True), trigger="test"))  # type: ignore[arg-type]
        return ToolResult(content=[TextContent(text="ONE")])

    tools = [AgentTool(name="one", execute=one, execution_mode="sequential")]
    run = await start(tmp_path, [("c1", "one")], tools)
    await settle(run.begin())

    assert run.task is not None and not run.task.cancelled()
    assert refreshed == [None]
    assert results_of(await run.reopened()) == {"c1": ("ONE", False)}
    assert len(run.provider.seen) == 2
