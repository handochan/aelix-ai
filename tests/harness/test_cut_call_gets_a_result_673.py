"""#673 (ADR-0290) — a call a cancel cut gets a result saying so, with what it printed.

The model must be able to tell a call that was cut from one that never began: cut
reads as stopped and possibly partly run, never begun stays the wire transform's
"No result provided", which now means only that.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import tracemalloc
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarnessError
from aelix_agent_core.harness.hooks import MessageEndEventResult, ToolResultPatch
from aelix_agent_core.loop import _CUT_TEXT, agent_loop
from aelix_agent_core.types import (
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    InputWithdrawnEvent,
)
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import AssistantEndEvent
from aelix_ai.tools import ToolCancelled, ToolResult
from aelix_coding_agent.tools.bash import BashToolDetails, create_bash_tool
from aelix_coding_agent.tui.render import EventRenderer

from tests.harness._cancelled_turn import (
    CANCEL_WAYS,
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
    until_real,
)

_BATCH = [("c1", "one"), ("c2", "held"), ("c3", "later")]


def _batch(reached: asyncio.Event, *, sequential: bool) -> list[AgentTool]:
    return [
        finished_tool("one", "ONE-RESULT", sequential=sequential),
        held_tool("held", reached, sequential=sequential),
        finished_tool("later", "LATER-RESULT", sequential=sequential),
    ]


def _says_what_it_did(
    text: str, *, details: Any = None, is_error: bool = True, name: str = "says"
) -> tuple[AgentTool, asyncio.Event]:
    reached = asyncio.Event()

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        try:
            reached.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError as cancel:
            raise ToolCancelled(
                ToolResult(content=[TextContent(text=text)], details=details, is_error=is_error)
            ) from cancel
        return ToolResult(content=[TextContent(text="never")])

    return AgentTool(name=name, execute=execute), reached


# === The table ================================================================


@pytest.mark.parametrize("attached", [False, True])
@pytest.mark.parametrize("sequential", [False, True])
@pytest.mark.parametrize("way", CANCEL_WAYS)
async def test_a_cut_call_reads_as_stopped_whoever_stopped_the_turn(
    tmp_path: Path, way: str, sequential: bool, attached: bool
) -> None:
    reached = asyncio.Event()
    run = await start(
        tmp_path, _BATCH, _batch(reached, sequential=sequential), attached=attached
    )
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: "c1" in run.ended())
    await run.cancel(way)

    state = results_of(run.harness.state.messages)
    assert state["c1"] == ("ONE-RESULT", False)
    assert state["c2"] == (_CUT_TEXT, True)
    if attached:
        on_disk = results_of(await run.reopened())
        assert on_disk["c1"] == ("ONE-RESULT", False)
        assert on_disk["c2"] == (_CUT_TEXT, True)
    if sequential:
        assert "c3" not in state
        assert run.started() == ["c1", "c2"]
    else:
        # A parallel batch has every call running by then: c3 finished.
        assert state["c3"] == ("LATER-RESULT", False)


async def test_the_wire_tells_finished_cut_and_never_started_apart(tmp_path: Path) -> None:
    reached = asyncio.Event()
    run = await start(tmp_path, _BATCH, _batch(reached, sequential=True))
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    wire = results_of(await run.next_request())
    assert wire == {
        "c1": ("ONE-RESULT", False),
        "c2": (_CUT_TEXT, True),
        "c3": (NOT_STARTED, True),
    }
    assert NOT_STARTED not in _CUT_TEXT


@pytest.mark.parametrize("attached", [False, True])
async def test_a_parallel_batch_commits_finished_and_cut_in_source_order(
    tmp_path: Path, attached: bool
) -> None:
    reached = asyncio.Event()
    tools = [
        held_tool("held", reached),
        finished_tool("one", "ONE-RESULT"),
        finished_tool("two", "TWO-RESULT"),
    ]
    run = await start(
        tmp_path, [("c1", "held"), ("c2", "one"), ("c3", "two")], tools, attached=attached
    )
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: set(run.ended()) == {"c2", "c3"})
    await run.cancel()

    assert order_of(run.harness.state.messages) == ["c1", "c2", "c3"]
    if attached:
        assert order_of(await run.reopened()) == ["c1", "c2", "c3"]
    assert run.ended() == ["c2", "c3", "c1"], "the end event stays in completion order"


@pytest.mark.parametrize("sequential", [False, True])
async def test_every_begun_call_pairs_its_events_once_and_ends_before_its_result(
    tmp_path: Path, sequential: bool
) -> None:
    reached = asyncio.Event()
    run = await start(tmp_path, _BATCH, _batch(reached, sequential=sequential))
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    for call_id in run.started():
        assert run.started().count(call_id) == 1
        assert run.ended().count(call_id) == 1
    end_at = {
        e.tool_call_id: i for i, e in enumerate(run.events) if e.type == "tool_execution_end"
    }
    commit_at = {
        e.message.tool_call_id: i
        for i, e in enumerate(run.events)
        if e.type == "message_end" and isinstance(e.message, ToolResultMessage)
    }
    assert set(end_at) == set(commit_at)
    for call_id, at in end_at.items():
        assert at < commit_at[call_id]
    cut = next(e for e in run.events if e.type == "tool_execution_end" and e.tool_call_id == "c2")
    assert cut.is_error is True
    assert cut.result.content == [TextContent(text=_CUT_TEXT)]


async def test_results_close_the_turn_after_them_as_an_abort_with_one_line(tmp_path: Path) -> None:
    """What the renderer draws is the card, then one abort line, and nothing doubled."""

    reached = asyncio.Event()
    run = await start(tmp_path, _BATCH, _batch(reached, sequential=True))
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    ends = [e for e in run.events if e.type == "turn_end"]
    assert [e.message.stop_reason for e in ends] == ["aborted"]
    assert ends[0].tool_results == []
    types = [e.type for e in run.events]
    last_commit = max(
        i
        for i, e in enumerate(run.events)
        if e.type == "message_end" and isinstance(e.message, ToolResultMessage)
    )
    assert last_commit < types.index("turn_end") < types.index("agent_end")

    commits: list[Any] = []
    renderer = EventRenderer(commit=commits.append, set_tail=lambda _t: None, width=100)
    for event in run.events:
        renderer.on_agent_event(event)
    drawn = [str(getattr(c, "plain", c)) for c in commits]
    assert sum("Operation aborted" in line for line in drawn) == 1


# === ADR-0286 ================================================================


@pytest.mark.parametrize("attached", [False, True])
async def test_a_withdrawing_abort_during_a_tool_keeps_the_input_and_the_results(
    tmp_path: Path, attached: bool
) -> None:
    reached = asyncio.Event()
    run = await start(
        tmp_path, _BATCH, _batch(reached, sequential=True), attached=attached
    )
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.steer("STEER")
    await run.cancel("withdrawing_abort")

    # The input stays, since the call answered it. Only the steer the abort cleared
    # comes back, and it never reached the context behind the results.
    withdrawn = [e for e in run.events if isinstance(e, InputWithdrawnEvent)]
    assert [([text_of(m) for m in e.messages], e.prompt_withdrawn) for e in withdrawn] == [
        (["STEER"], False)
    ]
    agent_end = next(e for e in run.events if e.type == "agent_end")
    for messages in [run.harness.state.messages, agent_end.messages]:
        assert [text_of(m) for m in messages if isinstance(m, UserMessage)] == ["KEEP"]
        results = results_of(messages)
        assert results["c1"] == ("ONE-RESULT", False)
        assert results["c2"] == (_CUT_TEXT, True)
    assert run.harness._next_turn_queue == []
    if attached:
        reopened = await run.reopened()
        assert [text_of(m) for m in reopened if isinstance(m, UserMessage)] == ["KEEP"]
        assert order_of(reopened) == ["c1", "c2"]


@pytest.mark.parametrize("sequential", [False, True])
async def test_agent_end_after_an_abort_carries_the_results(
    tmp_path: Path, sequential: bool
) -> None:
    reached = asyncio.Event()
    run = await start(tmp_path, _BATCH, _batch(reached, sequential=sequential))
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: "c1" in run.ended())
    await run.cancel()

    agent_end = next(e for e in run.events if e.type == "agent_end")
    assert results_of(agent_end.messages)["c2"] == (_CUT_TEXT, True)
    assert agent_end.messages[-1].stop_reason == "aborted"


# === What a tool can say =====================================================


@pytest.mark.parametrize("attached", [False, True])
@pytest.mark.parametrize("sequential", [False, True])
async def test_tool_cancelled_says_what_the_tool_did(
    tmp_path: Path, sequential: bool, attached: bool
) -> None:
    tool, reached = _says_what_it_did("WROTE 3 OF 5 ROWS", details={"rows": 3})
    tool = AgentTool(
        name=tool.name,
        execute=tool.execute,
        execution_mode="sequential" if sequential else None,
    )
    run = await start(tmp_path, [("c1", "says")], [tool], attached=attached)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    assert results_of(run.harness.state.messages) == {"c1": ("WROTE 3 OF 5 ROWS", True)}
    end = next(e for e in run.events if e.type == "tool_execution_end")
    assert end.result.details == {"rows": 3}
    if attached:
        assert results_of(await run.reopened()) == {"c1": ("WROTE 3 OF 5 ROWS", True)}


async def test_tool_cancelled_can_not_make_a_cut_call_a_success(tmp_path: Path) -> None:
    tool, reached = _says_what_it_did("ALL GOOD", is_error=False)
    run = await start(tmp_path, [("c1", "says")], [tool])
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    assert results_of(await run.reopened()) == {"c1": ("ALL GOOD", True)}


def test_tool_cancelled_is_still_a_cancellation() -> None:
    assert issubclass(ToolCancelled, asyncio.CancelledError)
    result = ToolResult(content=[TextContent(text="x")])
    assert ToolCancelled(result).result is result
    assert ToolCancelled().result is None


# === Redaction ===============================================================


def _secret_tool() -> tuple[AgentTool, asyncio.Event]:
    return _says_what_it_did("SECRET-OUTPUT", details={"secret": "SECRET-OUTPUT"})


def _no_secret(run_messages: list[Any]) -> None:
    assert "SECRET" not in str([text_of(m) for m in run_messages if isinstance(m, ToolResultMessage)])


async def test_a_tool_result_hook_redacts_a_cut_result(tmp_path: Path) -> None:
    tool, reached = _secret_tool()
    run = await start(tmp_path, [("c1", "says")], [tool])

    def redact(event: Any, _ctx: Any) -> ToolResultPatch:
        assert event.is_error is True
        assert event.content == [TextContent(text="SECRET-OUTPUT")]
        return ToolResultPatch(content=[TextContent(text="REDACTED")], details={})

    run.harness.hooks.on("tool_result", redact)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    assert results_of(await run.reopened()) == {"c1": ("REDACTED", True)}
    end = next(e for e in run.events if e.type == "tool_execution_end")
    assert end.result.content == [TextContent(text="REDACTED")]
    assert end.result.details == {}


async def test_a_tool_result_hook_can_not_make_a_cut_call_a_success(tmp_path: Path) -> None:
    tool, reached = _secret_tool()
    run = await start(tmp_path, [("c1", "says")], [tool])
    run.harness.hooks.on(
        "tool_result",
        lambda _e, _c: ToolResultPatch(content=[TextContent(text="FINE")], is_error=False),
    )
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    assert results_of(await run.reopened()) == {"c1": ("FINE", True)}


@pytest.mark.parametrize("failing", ["hook", "after_tool_call"])
async def test_a_redaction_hook_that_raises_on_a_cut_result_withholds_the_output(
    tmp_path: Path, failing: str
) -> None:
    tool, reached = _secret_tool()

    def boom(*_a: Any) -> Any:
        raise RuntimeError("the hook broke")

    options = {"after_tool_call": boom} if failing == "after_tool_call" else {}
    run = await start(tmp_path, [("c1", "says")], [tool], **options)
    if failing == "hook":
        run.harness.hooks.on("tool_result", boom)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    messages = await run.reopened()
    assert results_of(messages) == {"c1": (_CUT_TEXT, True)}
    _no_secret(messages)
    end = next(e for e in run.events if e.type == "tool_execution_end")
    assert end.result.content == [TextContent(text=_CUT_TEXT)]
    assert end.result.details is None
    # The cancel stays the cancel: the abort's close-out ran, not the failure's.
    assert [e.message.stop_reason for e in run.events if e.type == "turn_end"] == ["aborted"]


async def test_a_redaction_hook_held_by_a_second_cancel_withholds_the_output(
    tmp_path: Path,
) -> None:
    tool, reached = _secret_tool()
    run = await start(tmp_path, [("c1", "says")], [tool])
    in_hook = asyncio.Event()

    async def park(_event: Any, _ctx: Any) -> None:
        in_hook.set()
        await asyncio.Event().wait()

    run.harness.hooks.on("tool_result", park)
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    assert not task.done()
    await run.harness.abort()
    await settle(task)

    messages = await run.reopened()
    assert results_of(messages) == {"c1": (_CUT_TEXT, True)}
    _no_secret(messages)


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_first_cancel_in_a_finished_calls_redaction_hook_withholds_the_output(
    tmp_path: Path, sequential: bool
) -> None:
    """The tool finished, so the output is real, and no hook has seen it. It stays out."""

    tool = finished_tool("one", "SECRET-OUTPUT", sequential=sequential)
    run = await start(tmp_path, [("c1", "one")], [tool])
    in_hook = asyncio.Event()

    async def park(_event: Any, _ctx: Any) -> None:
        in_hook.set()
        await asyncio.Event().wait()

    run.harness.hooks.on("tool_result", park)
    task = run.begin()
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    await run.harness.abort()
    await settle(task)

    messages = await run.reopened()
    assert results_of(messages) == {"c1": (_CUT_TEXT, True)}
    _no_secret(messages)
    assert len(run.provider.seen) == 1, "the cancel stays the cancel"


async def test_a_hook_raising_on_a_call_that_was_not_cut_still_fails_the_turn(
    tmp_path: Path,
) -> None:
    """Nothing changes without a cancel: this one still reaches the failure close-out."""

    tool = finished_tool("one", "OUTPUT")
    run = await start(tmp_path, [("c1", "one")], [tool])

    def boom(_event: Any, _ctx: Any) -> None:
        raise RuntimeError("the hook broke")

    run.harness.hooks.on("tool_result", boom)
    with pytest.raises(AgentHarnessError):
        await run.harness.prompt("KEEP")
    assert results_of(run.harness.state.messages) == {}


async def test_a_message_end_replacement_holds_on_a_cut_result(tmp_path: Path) -> None:
    tool, reached = _secret_tool()
    run = await start(tmp_path, [("c1", "says")], [tool])

    def redact(event: Any, _ctx: Any) -> MessageEndEventResult | None:
        message = event.message
        if isinstance(message, ToolResultMessage):
            return MessageEndEventResult(
                message=ToolResultMessage(
                    tool_call_id=message.tool_call_id,
                    content=[TextContent(text="REPLACED")],
                    is_error=True,
                )
            )
        return None

    run.harness.hooks.on("message_end", redact)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    for messages in (run.harness.state.messages, await run.reopened()):
        assert results_of(messages) == {"c1": ("REPLACED", True)}


# === Updates =================================================================


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_late_on_partial_is_dropped_and_no_update_follows_the_end(
    tmp_path: Path, sequential: bool
) -> None:
    captured: dict[str, Any] = {}
    reached = asyncio.Event()

    async def execute(_args: Any, ctx: Any) -> ToolResult:
        captured["on_partial"] = ctx.on_partial
        ctx.on_partial(ToolResult(content=[TextContent(text="SO FAR")]))
        reached.set()
        await asyncio.Event().wait()
        return ToolResult(content=[])

    tool = AgentTool(
        name="partial",
        execute=execute,
        execution_mode="sequential" if sequential else None,
    )
    run = await start(tmp_path, [("c1", "partial")], [tool])
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    types = run.types("tool_execution_update", "tool_execution_end")
    assert types == ["tool_execution_update", "tool_execution_end"]
    captured["on_partial"](ToolResult(content=[TextContent(text="LATE")]))
    await asyncio.sleep(0.01)
    assert run.types("tool_execution_update", "tool_execution_end") == types


async def test_a_cut_call_waits_for_its_pending_updates_before_its_end(tmp_path: Path) -> None:
    reached = asyncio.Event()
    update_may_finish = asyncio.Event()
    order: list[str] = []

    async def execute(_args: Any, ctx: Any) -> ToolResult:
        ctx.on_partial(ToolResult(content=[TextContent(text="SO FAR")]))
        await asyncio.sleep(0)
        reached.set()
        await asyncio.Event().wait()
        return ToolResult(content=[])

    run = await start(tmp_path, [("c1", "partial")], [AgentTool(name="partial", execute=execute)])

    async def listener(event: Any) -> None:
        if event.type == "tool_execution_update":
            await update_may_finish.wait()
            order.append("update handled")
        elif event.type == "tool_execution_end":
            order.append("end")

    run.harness.subscribe(listener)
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await asyncio.sleep(0.01)
    assert order == [], "the end waits for the update"
    update_may_finish.set()
    await settle(task)
    assert order == ["update handled", "end"]


async def test_a_second_cancel_gets_past_a_held_update(tmp_path: Path) -> None:
    reached = asyncio.Event()

    async def execute(_args: Any, ctx: Any) -> ToolResult:
        ctx.on_partial(ToolResult(content=[TextContent(text="SO FAR")]))
        await asyncio.sleep(0)
        reached.set()
        await asyncio.Event().wait()
        return ToolResult(content=[])

    run = await start(tmp_path, [("c1", "partial")], [AgentTool(name="partial", execute=execute)])

    async def listener(event: Any) -> None:
        if event.type == "tool_execution_update":
            await asyncio.Event().wait()

    run.harness.subscribe(listener)
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await asyncio.sleep(0.01)
    assert not task.done()
    await run.harness.abort()
    await settle(task)
    assert results_of(await run.reopened()) == {"c1": (_CUT_TEXT, True)}


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_first_cancel_in_a_finished_calls_update_drain_keeps_its_result(
    tmp_path: Path, sequential: bool
) -> None:
    """The tool returned and a listener holds its last update. The result stands, the cancel too."""

    in_update = asyncio.Event()

    async def execute(_args: Any, ctx: Any) -> ToolResult:
        ctx.on_partial(ToolResult(content=[TextContent(text="SO FAR")]))
        return ToolResult(content=[TextContent(text="FINISHED")])

    tool = AgentTool(
        name="partial", execute=execute, execution_mode="sequential" if sequential else None
    )
    run = await start(tmp_path, [("c1", "partial")], [tool])

    async def listener(event: Any) -> None:
        if event.type == "tool_execution_update":
            in_update.set()
            await asyncio.Event().wait()

    run.harness.subscribe(listener)
    task = run.begin()
    await asyncio.wait_for(in_update.wait(), timeout=5)
    await run.harness.abort()
    await settle(task)

    assert results_of(await run.reopened()) == {"c1": ("FINISHED", False)}
    assert len(run.provider.seen) == 1, "the cancel stays the cancel"


async def test_a_sink_that_fails_on_the_way_out_does_not_replace_the_cancel() -> None:
    """The loop with a sink of its own: every failure stays behind the cancel."""

    reached = asyncio.Event()
    seen: list[str] = []

    async def execute(_args: Any, ctx: Any) -> ToolResult:
        ctx.on_partial(ToolResult(content=[TextContent(text="SO FAR")]))
        await asyncio.sleep(0)
        reached.set()
        await asyncio.Event().wait()
        return ToolResult(content=[])

    async def stream(_model: Any, _context: Any, _options: Any) -> AsyncIterator[Any]:
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[ToolCallContent(tool_call_id="c1", tool_name="partial", input={})],
                stop_reason="tool_use",
            )
        )

    async def emit(event: Any) -> None:
        seen.append(event.type)
        if event.type == "tool_execution_update" or (
            event.type in ("tool_execution_end", "message_start")
            and "tool_execution_start" in seen
        ):
            raise RuntimeError(f"sink broke on {event.type}")

    config = AgentLoopConfig(
        model=MODEL, convert_to_llm=lambda messages: messages, tool_execution="parallel"
    )
    context = AgentContext(
        system_prompt="",
        messages=[],
        tools=[AgentTool(name="partial", execute=execute)],
    )
    task = asyncio.create_task(
        agent_loop(
            [UserMessage(content=[TextContent(text="go")])],
            context,
            config,
            emit=emit,
            stream_fn=stream,
        )
    )
    await asyncio.wait_for(reached.wait(), timeout=5)
    task.cancel()
    await asyncio.wait([task], timeout=5)
    assert task.cancelled()
    assert seen[-3:] == ["tool_execution_end", "message_start", "message_end"]


# === The way out =============================================================


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_tool_stuck_in_its_cleanup_gets_the_next_cancel(
    tmp_path: Path, sequential: bool
) -> None:
    """``abort()`` repeating its ``cancel()`` is the way out of a stuck tool."""

    reached = asyncio.Event()
    in_cleanup = asyncio.Event()

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        try:
            reached.set()
            await asyncio.Event().wait()
        finally:
            in_cleanup.set()
            await asyncio.Event().wait()
        return ToolResult(content=[])

    mode = "sequential" if sequential else None
    tools = [
        finished_tool("one", "ONE-RESULT", sequential=sequential),
        AgentTool(name="stuck", execute=execute, execution_mode=mode),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "stuck")], tools)
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: "c1" in run.ended())
    await run.harness.abort()
    await asyncio.wait_for(in_cleanup.wait(), timeout=5)
    await asyncio.sleep(0.01)
    assert not task.done()
    await run.harness.abort()
    await settle(task)

    assert results_of(await run.reopened()) == {
        "c1": ("ONE-RESULT", False),
        "c2": (_CUT_TEXT, True),
    }


@pytest.mark.parametrize("way", ["abort", "outer_cancel"])
@pytest.mark.parametrize("turns", range(10))
@pytest.mark.parametrize("sequential", [False, True])
async def test_any_loop_turn_of_the_cancel_tells_every_call_apart(
    tmp_path: Path, turns: int, sequential: bool, way: str
) -> None:
    """Three states, one loop turn at a time: began with a result, finished with its own."""

    began: list[str] = []
    finished: list[str] = []

    def stepping(name: str) -> AgentTool:
        async def execute(_args: Any, _ctx: Any) -> ToolResult:
            began.append(name)
            for _ in range(3):
                await asyncio.sleep(0)
            finished.append(name)
            return ToolResult(content=[TextContent(text=f"{name}-RESULT")])

        return AgentTool(
            name=name, execute=execute, execution_mode="sequential" if sequential else None
        )

    tools = [stepping("one"), stepping("two")]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    in_first = asyncio.Event()
    run.harness.subscribe(
        lambda event: in_first.set() if event.type == "tool_execution_start" else None
    )
    run.begin()
    await asyncio.wait_for(in_first.wait(), timeout=5)
    for _ in range(turns):
        await asyncio.sleep(0)
    await run.cancel(way)

    results = results_of(await run.reopened())
    for name, call_id in (("one", "c1"), ("two", "c2")):
        if name in finished:
            assert results[call_id] == (f"{name}-RESULT", False)
        elif name in began:
            assert results[call_id] == (_CUT_TEXT, True)
        else:
            assert call_id not in results
        if name in began:
            assert run.ended().count(call_id) == 1
    assert run.types("agent_end") in (["agent_end"], [])
    assert order_of(await run.reopened()) == sorted(results)


# === bash ====================================================================


async def _run_bash(tmp_path: Path, command: str, reached: Any, **options: Any) -> Any:
    tool = create_bash_tool(str(tmp_path), options or None)
    run = await start(tmp_path, [("c1", "bash")], [tool])
    run.provider.calls[0].input["command"] = command
    task = run.begin()
    await until_real(reached)
    await run.harness.abort()
    await settle(task)
    return run


class _CutOps:
    """Operations that print, then run until cancelled. No process, so no platform."""

    def __init__(self, *printed: bytes) -> None:
        self.printed = printed
        self.reached = asyncio.Event()

    async def exec(self, _command: str, _cwd: str, *, on_data: Any, **_kwargs: Any) -> Any:
        for chunk in self.printed:
            on_data(chunk)
        self.reached.set()
        await asyncio.Event().wait()


async def _cut_through_operations(tmp_path: Path, ops: _CutOps, **options: Any) -> ToolResult:
    tool = create_bash_tool(str(tmp_path), {"operations": ops, **options})
    task = asyncio.create_task(tool.execute({"command": "x"}, _no_ctx()))
    await asyncio.wait_for(ops.reached.wait(), timeout=5)
    task.cancel()
    with pytest.raises(ToolCancelled) as cut:
        await task
    return cut.value.result


def _no_ctx() -> Any:
    from aelix_ai.tools import ToolExecutionContext

    return ToolExecutionContext()


async def test_bash_reports_what_it_printed_and_the_stop(tmp_path: Path) -> None:
    result = await _cut_through_operations(tmp_path, _CutOps(b"STARTED\n", b"MORE\n"))

    assert result.is_error is True
    assert result.content == [
        TextContent(
            text="STARTED\nMORE\n\n\nCommand aborted: the user stopped the turn while it ran, "
            "so it may have partly run."
        )
    ]
    assert isinstance(result.details, BashToolDetails)
    assert result.details.exit_code is None


async def test_bash_with_no_output_reports_the_stop_alone(tmp_path: Path) -> None:
    result = await _cut_through_operations(tmp_path, _CutOps())
    assert result.content == [
        TextContent(
            text="Command aborted: the user stopped the turn while it ran, "
            "so it may have partly run."
        )
    ]


async def test_a_cut_commands_output_is_decoded_like_any_other(tmp_path: Path) -> None:
    """The kill can stop a chunk mid-character: the ragged tail rule applies here too."""

    result = await _cut_through_operations(tmp_path, _CutOps("ok: 가나".encode()[:-1]))
    assert result.content[0].text.startswith("ok: 가�")  # type: ignore[union-attr]


async def test_a_cut_commands_output_is_truncated_like_the_exit_path(tmp_path: Path) -> None:
    printed = b"".join(b"line %d\n" % n for n in range(1, 21))
    cut = await _cut_through_operations(tmp_path, _CutOps(printed), max_lines=3)

    class _Exits:
        async def exec(self, _command: str, _cwd: str, *, on_data: Any, **_kwargs: Any) -> Any:
            from aelix_coding_agent.tools.bash import ExecExitResult

            on_data(printed)
            return ExecExitResult(exit_code=None)

    tool = create_bash_tool(str(tmp_path), {"operations": _Exits(), "max_lines": 3})
    exited = await tool.execute({"command": "x"}, _no_ctx())

    cut_text = cut.content[0].text  # type: ignore[union-attr]
    exit_text = exited.content[0].text  # type: ignore[union-attr]
    assert "line 20" in cut_text and "line 1\n" not in cut_text
    assert cut_text.endswith("Command aborted: the user stopped the turn while it ran, so it may have partly run.")
    assert exit_text.endswith("Command aborted")
    assert isinstance(cut.details, BashToolDetails) and isinstance(exited.details, BashToolDetails)
    assert cut.details.truncation == exited.details.truncation
    # One body, whatever the temp file is called.
    assert cut_text.replace(cut.details.full_output_path or "", "<path>").rsplit("\n\n", 1)[0] == (
        exit_text.replace(exited.details.full_output_path or "", "<path>").rsplit("\n\n", 1)[0]
    )
    assert Path(cut.details.full_output_path or "").read_text(encoding="utf-8") == printed.decode()


async def test_a_cut_at_the_cap_is_still_read_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(create_bash_tool.__globals__, "_CUT_WHOLE_READ_BYTES", 400)
    printed = b"".join(b"%07d\n" % n for n in range(50))
    assert len(printed) == 400
    cut = await _cut_through_operations(tmp_path, _CutOps(printed), max_lines=3, max_bytes=90)

    assert isinstance(cut.details, BashToolDetails) and cut.details.truncation is not None
    assert cut.details.truncation.original_lines == 50
    assert "of 50" in cut.content[0].text  # type: ignore[union-attr]


class _FloodSpies:
    """What the cut path read, by size: the point of the cap is what it does not touch."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.decoded: list[int] = []
        self.split: list[int] = []
        import aelix_coding_agent.tools._truncate as truncate_module
        import aelix_coding_agent.tools.bash as bash_module

        real_decode = bash_module.decode_child_output
        real_split = truncate_module.split_lines_for_counting

        def decode(data: bytes, **kwargs: Any) -> str:
            self.decoded.append(len(data))
            return real_decode(data, **kwargs)

        def split(text: str) -> list[str]:
            self.split.append(len(text))
            return real_split(text)

        monkeypatch.setattr(bash_module, "decode_child_output", decode)
        monkeypatch.setattr(bash_module, "split_lines_for_counting", split)
        monkeypatch.setattr(truncate_module, "split_lines_for_counting", split)
        monkeypatch.setattr(bash_module, "_CUT_WHOLE_READ_BYTES", 10_000)


async def _cut_flood(
    tmp_path: Path, chunks: list[bytes], *, max_bytes: int = 500, max_lines: int = 2000
) -> tuple[ToolResult, str]:
    cut = await _cut_through_operations(
        tmp_path, _CutOps(*chunks), max_lines=max_lines, max_bytes=max_bytes
    )
    assert isinstance(cut.details, BashToolDetails)
    text = cut.content[0].text  # type: ignore[union-attr]
    return cut, text


async def _whole_file(cut: ToolResult, size: int) -> bytes:
    assert isinstance(cut.details, BashToolDetails) and cut.details.full_output_path
    path = Path(cut.details.full_output_path)
    await until_real(lambda: path.exists() and path.stat().st_size == size)
    return path.read_bytes()


async def test_a_flood_is_cut_without_being_read_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verifier's blocker: ``yes`` for 0.25 s froze the loop for 3.5 s and took 10 GB."""

    spies = _FloodSpies(monkeypatch)
    chunks = [b"y\n" * 500 for _ in range(50)]
    total = sum(map(len, chunks))
    cut, text = await _cut_flood(tmp_path, chunks)

    assert max(spies.decoded) <= 4 * 500
    assert max(spies.split) <= 4 * 500
    assert await _whole_file(cut, total) == b"".join(chunks)
    assert isinstance(cut.details, BashToolDetails)
    info = cut.details.truncation
    assert info is not None and info.truncated
    assert info.original_lines == 0, "nobody counted them, and nothing says otherwise"
    assert info.original_bytes == total
    assert info.kept_bytes <= 500
    assert text.startswith("y\ny\n")
    assert "whose lines were not counted because the command was cut" in text
    assert cut.details.full_output_path in text
    assert text.endswith(
        "Command aborted: the user stopped the turn while it ran, so it may have partly run."
    )
    assert cut.is_error is True


async def test_a_flood_costs_no_copy_of_itself(tmp_path: Path) -> None:
    """The real cap, and memory measured: over it nothing joins, decodes or splits it all."""

    head = b"y\n" * (16 * 1024 * 1024)
    tail = [b"z\n" * 500 for _ in range(1000)]
    ops = _CutOps(head, *tail)
    tool = create_bash_tool(str(tmp_path), {"operations": ops})
    task = asyncio.create_task(tool.execute({"command": "x"}, _no_ctx()))
    await asyncio.wait_for(ops.reached.wait(), timeout=5)

    tracemalloc.start()
    try:
        task.cancel()
        with pytest.raises(ToolCancelled) as cut:
            await task
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < len(head) // 4, f"the cut path allocated {peak} bytes"
    await _whole_file(cut.value.result, len(head) + 1_000_000)


async def test_a_floods_kept_lines_come_from_its_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spies = _FloodSpies(monkeypatch)
    lines = [b"line %06d\n" % n for n in range(20_000)]
    cut, text = await _cut_flood(tmp_path, [b"".join(lines)], max_lines=3)

    assert max(spies.decoded) <= 4 * 500, "one huge chunk is read no further than many small"
    assert text.startswith("line 019997\nline 019998\nline 019999")
    assert "the last 3 lines" in text
    await _whole_file(cut, sum(map(len, lines)))


async def test_a_flood_cut_mid_character_at_both_ends_reads_clean_inside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its window starts mid-character and the kill can stop one: neither reaches the body."""

    _FloodSpies(monkeypatch)
    # One long line, so the body is cut out of the window too and shows its own end.
    flood = "가" * 20_000
    data = flood.encode()[:-1]
    cut, text = await _cut_flood(tmp_path, [data])

    body = text.split("\n\n[Showing", 1)[0]
    assert set(body) <= {"가", "\ufffd"}, "no mojibake from a fallback code page"
    assert body.endswith("\ufffd")
    assert isinstance(cut.details, BashToolDetails) and cut.details.truncation is not None
    assert cut.details.truncation.last_line_partial
    assert "of a line" in text
    await _whole_file(cut, len(data))


async def test_a_floods_file_holds_the_raw_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FloodSpies(monkeypatch)
    chunks = [bytes(range(256)) * 20 for _ in range(4)]
    cut, _text = await _cut_flood(tmp_path, chunks)

    assert await _whole_file(cut, 256 * 80) == b"".join(chunks)


async def test_a_floods_file_that_can_not_be_opened_does_not_replace_the_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FloodSpies(monkeypatch)
    monkeypatch.setattr(
        "aelix_coding_agent.tools.bash.tempfile.gettempdir", lambda: str(tmp_path / "no" / "dir")
    )
    ops = _CutOps(b"y\n" * 10_000)
    tool = create_bash_tool(str(tmp_path), {"operations": ops, "max_bytes": 500})
    task = asyncio.create_task(tool.execute({"command": "x"}, _no_ctx()))
    await asyncio.wait_for(ops.reached.wait(), timeout=5)
    task.cancel()
    await asyncio.wait([task], timeout=5)

    assert task.cancelled(), "a bare cancel: the loop's default text says the stop"


async def test_a_flood_that_fills_the_disk_keeps_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """What the file could not hold is the file's loss alone: the model has the tail."""

    _FloodSpies(monkeypatch)
    import aelix_coding_agent.tools.bash as bash_module

    class _Full:
        def __enter__(self) -> _Full:
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

        def write(self, _data: bytes) -> int:
            threads.append(threading.current_thread())
            raise OSError("no space left on device")

    threads: list[threading.Thread] = []
    wrote = asyncio.Event()
    loop = asyncio.get_running_loop()
    real_debug = bash_module._LOG.debug

    def debug(message: str, *args: Any, **kwargs: Any) -> None:
        real_debug(message, *args, **kwargs)
        if "full output failed" in message:
            loop.call_soon_threadsafe(wrote.set)

    monkeypatch.setattr(bash_module._LOG, "debug", debug)
    monkeypatch.setattr(bash_module.Path, "open", lambda *_a, **_k: _Full())
    cut, text = await _cut_flood(tmp_path, [b"y\n" * 10_000])
    await asyncio.wait_for(wrote.wait(), timeout=5)

    assert threads and threads[0] is not threading.main_thread(), "not on the event loop"
    assert threads[0].daemon, "a SIGTERM does not wait for it"
    assert cut.is_error is True
    assert "Command aborted" in text
    assert text.startswith("y\n")


async def test_output_that_can_not_be_assembled_does_not_replace_the_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_disk(_raw: str) -> str:
        raise OSError("no space left on device")

    monkeypatch.setitem(create_bash_tool.__globals__, "_write_full_output", no_disk)
    ops = _CutOps(b"".join(b"line %d\n" % n for n in range(1, 21)))
    tool = create_bash_tool(str(tmp_path), {"operations": ops, "max_lines": 3})
    task = asyncio.create_task(tool.execute({"command": "x"}, _no_ctx()))
    await asyncio.wait_for(ops.reached.wait(), timeout=5)
    task.cancel()
    await asyncio.wait([task], timeout=5)

    assert task.cancelled(), "a bare cancel: the loop's default text says the stop"


def _alive(marker: str) -> bool:
    out = subprocess.run(
        ["ps", "-axo", "command"], capture_output=True, encoding="utf-8", check=True
    ).stdout
    return any(marker in line and "ps -axo" not in line for line in out.splitlines())


@pytest.mark.skipif(sys.platform == "win32", reason="ps, and a POSIX shell's sleep")
async def test_a_real_command_is_killed_and_the_model_reads_what_it_printed(
    tmp_path: Path,
) -> None:
    marker = f"sleep 37.{os.getpid()}"
    run = await _run_bash(
        tmp_path,
        f"echo STARTED673; echo PARTIAL > partial.txt; {marker}; echo DONE > finished.txt",
        # Not the file alone: a cancel right as the next command spawns can leave it
        # running, a rare orphan on the base too and not what this test is about.
        lambda: (tmp_path / "partial.txt").exists() and _alive(marker),
    )

    assert (tmp_path / "partial.txt").exists()
    assert not (tmp_path / "finished.txt").exists()
    await until_real(lambda: not _alive(marker))
    wire = results_of(await run.next_request())["c1"]
    assert wire[1] is True
    assert "STARTED673" in wire[0]
    assert wire[0].endswith(
        "Command aborted: the user stopped the turn while it ran, so it may have partly run."
    )
    assert run.ended() == ["c1"]
    assert not (tmp_path / "finished.txt").exists()


# === The renderer ============================================================


async def test_the_card_shows_the_stop_and_the_abort_line_follows_it(tmp_path: Path) -> None:
    ops = _CutOps(b"PRINTED SO FAR\n")
    tool = create_bash_tool(str(tmp_path), {"operations": ops})
    run = await start(tmp_path, [("c1", "bash")], [tool])
    run.provider.calls[0].input["command"] = "x"
    task = run.begin()
    await asyncio.wait_for(ops.reached.wait(), timeout=5)
    await run.harness.abort()
    await settle(task)

    commits: list[Any] = []
    renderer = EventRenderer(commit=commits.append, set_tail=lambda _t: None, width=100)
    for event in run.events:
        renderer.on_agent_event(event)
    from io import StringIO

    from rich.console import Console

    buffer = StringIO()
    console = Console(file=buffer, width=100)
    for commit in commits:
        console.print(commit)
    drawn = buffer.getvalue()
    assert "PRINTED SO FAR" in drawn
    assert "Command aborted: the user stopped the turn" in drawn
    assert drawn.index("Command aborted") < drawn.index("Operation aborted")
    assert drawn.count("Operation aborted") == 1


