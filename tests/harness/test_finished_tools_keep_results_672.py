"""#672 (ADR-0290) — a cancelled turn keeps the results of the calls that finished.

Owner's decision behind it (#654, final): what already ran stays. The next request
and the reopened session carry a finished call's real result, in source order,
whatever ended the turn, and whichever path the batch took.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import MessageEndEventResult, ToolCallResult
from aelix_agent_core.loop import _CUT_TEXT, agent_loop
from aelix_agent_core.types import AgentContext, AgentLoopConfig, AgentTool
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import AssistantEndEvent
from aelix_ai.tools import ToolResult
from aelix_coding_agent.tools.ls import create_ls_tool
from aelix_coding_agent.tools.read import create_read_tool

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

_BATCH = [("c1", "one"), ("c2", "two"), ("c3", "held")]


def _batch(reached: asyncio.Event, *, sequential: bool = False) -> list[AgentTool]:
    return [
        finished_tool("one", "ONE-RESULT", sequential=sequential),
        finished_tool("two", "TWO-RESULT", sequential=sequential),
        held_tool("held", reached, sequential=sequential),
    ]


@pytest.mark.parametrize("attached", [False, True])
@pytest.mark.parametrize("way", CANCEL_WAYS)
async def test_a_parallel_batch_keeps_what_finished(
    tmp_path: Path, way: str, attached: bool
) -> None:
    reached = asyncio.Event()
    run = await start(tmp_path, _BATCH, _batch(reached), attached=attached)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: run.ended() == ["c1", "c2"])
    await run.cancel(way)

    state = results_of(run.harness.state.messages)
    assert state["c1"] == ("ONE-RESULT", False)
    assert state["c2"] == ("TWO-RESULT", False)
    assert order_of(run.harness.state.messages)[:2] == ["c1", "c2"]
    if attached:
        on_disk = results_of(await run.reopened())
        assert on_disk["c1"] == ("ONE-RESULT", False)
        assert on_disk["c2"] == ("TWO-RESULT", False)


@pytest.mark.parametrize("way", ["abort", "withdrawing_abort"])
async def test_the_next_request_carries_the_real_results(tmp_path: Path, way: str) -> None:
    reached = asyncio.Event()
    run = await start(tmp_path, _BATCH, _batch(reached))
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: run.ended() == ["c1", "c2"])
    await run.cancel(way)

    wire = results_of(await run.next_request())
    assert wire["c1"] == ("ONE-RESULT", False)
    assert wire["c2"] == ("TWO-RESULT", False)


async def test_real_read_and_ls_stay_while_a_third_call_runs(tmp_path: Path) -> None:
    """The batch the issue measured, with a fake in place of the grep held on a FIFO."""

    (tmp_path / "notes.txt").write_bytes(b"NOTES-CONTENT\n")
    reached = asyncio.Event()
    tools = [
        create_read_tool(str(tmp_path)),
        create_ls_tool(str(tmp_path)),
        held_tool("grep", reached),
    ]
    calls = [("c1", "read", {"path": "notes.txt"}), ("c2", "ls", {"path": "."}), ("c3", "grep", {})]
    run = await start(tmp_path, [(i, n) for i, n, _ in calls], tools)
    for call, (_, _, args) in zip(run.provider.calls, calls, strict=True):
        call.input.update(args)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until_real(lambda: set(run.ended()) == {"c1", "c2"})
    await run.harness.abort(withdraw_unanswered=True)
    await settle(run.task)  # type: ignore[arg-type]

    for messages in (await run.reopened(), await run.next_request()):
        results = results_of(messages)
        assert results["c1"] == ("NOTES-CONTENT\n", False)
        assert results["c2"][1] is False
        assert "notes.txt" in results["c2"][0]


async def test_source_order_holds_when_calls_finish_out_of_order(tmp_path: Path) -> None:
    first_may_finish = asyncio.Event()
    reached = asyncio.Event()

    async def slow(_args: Any, _ctx: Any) -> ToolResult:
        await first_may_finish.wait()
        return ToolResult(content=[TextContent(text="SLOW")])

    async def fast(_args: Any, _ctx: Any) -> ToolResult:
        return ToolResult(content=[TextContent(text="FAST")])

    tools = [
        AgentTool(name="slow", execute=slow),
        AgentTool(name="fast", execute=fast),
        held_tool("held", reached),
    ]
    run = await start(tmp_path, [("c1", "slow"), ("c2", "fast"), ("c3", "held")], tools)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: run.ended() == ["c2"])
    first_may_finish.set()
    await until(lambda: run.ended() == ["c2", "c1"])
    await run.cancel()

    assert order_of(run.harness.state.messages)[:2] == ["c1", "c2"]
    assert order_of(await run.reopened())[:2] == ["c1", "c2"]


@pytest.mark.parametrize("attached", [False, True])
async def test_a_sequential_batch_keeps_what_finished(tmp_path: Path, attached: bool) -> None:
    reached = asyncio.Event()
    run = await start(
        tmp_path, _BATCH, _batch(reached, sequential=True), attached=attached
    )
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    assert results_of(run.harness.state.messages)["c1"] == ("ONE-RESULT", False)
    assert results_of(run.harness.state.messages)["c2"] == ("TWO-RESULT", False)


async def test_a_later_call_of_a_sequential_batch_never_starts(tmp_path: Path) -> None:
    reached = asyncio.Event()
    ran: list[str] = []

    async def later(_args: Any, _ctx: Any) -> ToolResult:
        ran.append("later")
        return ToolResult(content=[TextContent(text="LATER")])

    tools = [
        finished_tool("one", "ONE-RESULT", sequential=True),
        held_tool("held", reached, sequential=True),
        AgentTool(name="later", execute=later, execution_mode="sequential"),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "held"), ("c3", "later")], tools)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    assert ran == []
    assert run.started()[-1] == "c2"
    wire = results_of(await run.next_request())
    assert wire["c1"] == ("ONE-RESULT", False)
    assert wire["c3"] == (NOT_STARTED, True)


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_cancel_in_a_finished_calls_end_event_does_not_cost_its_result(
    tmp_path: Path, sequential: bool
) -> None:
    """A listener holds ``tool_execution_end``. The call ran, so its result commits."""

    tools = [finished_tool("one", "ONE-RESULT", sequential=sequential)]
    run = await start(tmp_path, [("c1", "one")], tools)
    in_end = asyncio.Event()

    async def hold_the_end(event: Any) -> None:
        if event.type == "tool_execution_end":
            in_end.set()
            await asyncio.Event().wait()

    run.harness.subscribe(hold_the_end)
    run.begin()
    await asyncio.wait_for(in_end.wait(), timeout=5)
    await run.cancel()

    assert results_of(await run.reopened()) == {"c1": ("ONE-RESULT", False)}


async def test_a_cancel_in_phase_1_keeps_an_immediate_and_starts_nothing(tmp_path: Path) -> None:
    """Blocked and unknown calls have real results by then. The rest never began."""

    in_prep = asyncio.Event()
    ran: list[str] = []

    async def never(_args: Any, _ctx: Any) -> ToolResult:
        ran.append("ran")
        return ToolResult(content=[TextContent(text="NEVER")])

    tools = [
        finished_tool("blocked", "NEVER-RUNS"),
        AgentTool(name="parks", execute=never),
        AgentTool(name="later", execute=never),
    ]
    calls = [("c1", "ghost"), ("c2", "blocked"), ("c3", "parks"), ("c4", "later")]
    run = await start(tmp_path, calls, tools)

    async def policy(event: Any, _ctx: Any) -> ToolCallResult | None:
        if event.tool_name == "blocked":
            return ToolCallResult(block=True, reason="POLICY-REFUSED")
        if event.tool_name == "parks":
            in_prep.set()
            await asyncio.Event().wait()
        return None

    run.harness.hooks.on("tool_call", policy)
    run.begin()
    await asyncio.wait_for(in_prep.wait(), timeout=5)
    await run.cancel()

    assert ran == []
    wire = results_of(await run.next_request())
    assert wire["c1"] == ("Unknown tool: ghost", True)
    assert wire["c2"] == ("POLICY-REFUSED", True)
    assert wire["c3"] == (NOT_STARTED, True)
    assert wire["c4"] == (NOT_STARTED, True)


async def test_a_cancel_before_the_calls_first_step_writes_nothing(tmp_path: Path) -> None:
    """The one-turn window: ``cancel()`` lands while the tasks are scheduled, not yet run.

    The cancel is queued ahead of the tasks' first steps from the last ``tool_call``
    hook of Phase 1, so no ``execute`` begins. The trigger is the race's own.
    """

    ran: list[str] = []

    async def never(_args: Any, _ctx: Any) -> ToolResult:
        ran.append("ran")
        return ToolResult(content=[TextContent(text="NEVER")])

    tools = [AgentTool(name="one", execute=never), AgentTool(name="two", execute=never)]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)

    def queue_the_cancel(event: Any, _ctx: Any) -> None:
        if event.tool_name == "two":
            turn = run.harness._current_turn_task
            assert turn is not None
            asyncio.get_running_loop().call_soon(turn.cancel)

    run.harness.hooks.on("tool_call", queue_the_cancel)
    run.begin()
    await settle(run.task)  # type: ignore[arg-type]

    assert ran == []
    assert results_of(run.harness.state.messages) == {}
    assert results_of(await run.next_request()) == {
        "c1": (NOT_STARTED, True),
        "c2": (NOT_STARTED, True),
    }


async def test_a_call_still_unwinding_when_a_sibling_never_began_is_not_lost(
    tmp_path: Path,
) -> None:
    """c1 cancels the turn from inside itself, so c2 never begins while c1 is still working.

    c1 swallows the cancel, takes one more await and returns: what it returned is its result.
    """

    holder: dict[str, Any] = {}

    async def swallows(_args: Any, _ctx: Any) -> ToolResult:
        turn = holder["run"].harness._current_turn_task
        turn.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        return ToolResult(content=[TextContent(text="SWALLOWED")])

    async def never(_args: Any, _ctx: Any) -> ToolResult:
        raise AssertionError("cancelled before its first step")

    tools = [AgentTool(name="one", execute=swallows), AgentTool(name="two", execute=never)]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    holder["run"] = run
    run.begin()
    await settle(run.task)  # type: ignore[arg-type]

    assert results_of(run.harness.state.messages) == {"c1": ("SWALLOWED", False)}


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_message_end_replacement_holds_on_the_way_out(
    tmp_path: Path, sequential: bool
) -> None:
    reached = asyncio.Event()
    run = await start(tmp_path, _BATCH, _batch(reached, sequential=sequential))

    def redact(event: Any, _ctx: Any) -> MessageEndEventResult | None:
        message = event.message
        if isinstance(message, ToolResultMessage) and text_of(message) == "TWO-RESULT":
            return MessageEndEventResult(
                message=ToolResultMessage(
                    tool_call_id=message.tool_call_id,
                    content=[TextContent(text="REDACTED")],
                )
            )
        return None

    run.harness.hooks.on("message_end", redact)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: "c2" in run.ended())
    await run.cancel()

    for messages in (run.harness.state.messages, await run.reopened()):
        assert results_of(messages)["c2"] == ("REDACTED", False)
        assert "TWO-RESULT" not in str([text_of(m) for m in messages])


async def test_a_second_cancel_on_the_way_out_skips_only_the_result_it_landed_in(
    tmp_path: Path,
) -> None:
    """Esc Esc leaves. The hook held c1's ``message_end``, so c1 goes, and c2 still commits."""

    reached = asyncio.Event()
    run = await start(tmp_path, _BATCH, _batch(reached))
    in_hook = asyncio.Event()

    async def park_on_the_first(event: Any, _ctx: Any) -> None:
        message = event.message
        if isinstance(message, ToolResultMessage) and message.tool_call_id == "c1":
            in_hook.set()
            await asyncio.Event().wait()

    run.harness.hooks.on("message_end", park_on_the_first)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: run.ended() == ["c1", "c2"])
    await run.harness.abort()
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    assert not run.task.done()  # type: ignore[union-attr]
    await run.harness.abort()
    await settle(run.task)  # type: ignore[arg-type]

    results = results_of(await run.reopened())
    assert "c1" not in results
    assert results["c2"] == ("TWO-RESULT", False)
    assert run.types("agent_end") == ["agent_end"]
    wire = results_of(await run.next_request())
    assert wire["c1"] == (NOT_STARTED, True)
    assert wire["c2"] == ("TWO-RESULT", False)


async def test_every_result_a_hook_holds_needs_its_own_cancel_and_the_turn_still_ends(
    tmp_path: Path,
) -> None:
    reached = asyncio.Event()
    run = await start(tmp_path, _BATCH, _batch(reached))
    parked: list[str] = []

    async def park_every_result(event: Any, _ctx: Any) -> None:
        if isinstance(event.message, ToolResultMessage):
            parked.append(event.message.tool_call_id)
            await asyncio.Event().wait()

    run.harness.hooks.on("message_end", park_every_result)
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await until(lambda: run.ended() == ["c1", "c2"])
    # Each cancel has to reach the next hold before the next one lands: two inside one step of
    # the task are one cancel, and on Windows up to 3.12 ``sleep(0.01)`` can be that short.
    for _ in range(100):
        if task.done():
            break
        await run.harness.abort()
        await asyncio.sleep(0.02)
    await settle(task)
    assert parked[:2] == ["c1", "c2"]


@pytest.mark.parametrize("turns", range(8))
@pytest.mark.parametrize("sequential", [False, True])
async def test_any_loop_turn_of_the_cancel_gives_one_result_per_finished_call(
    tmp_path: Path, turns: int, sequential: bool
) -> None:
    """The cancel walks across the batch one loop turn at a time.

    ``gather``'s cancellation and its loop-turn counts differ across Python versions.
    Wherever it lands: a call that finished has its real result once, and the turn ends.
    """

    finished: list[str] = []

    def stepping(name: str) -> AgentTool:
        async def execute(_args: Any, _ctx: Any) -> ToolResult:
            for _ in range(2):
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
    await run.cancel()

    results = results_of(await run.reopened())
    for name, call_id in (("one", "c1"), ("two", "c2")):
        if name in finished:
            assert results[call_id] == (f"{name}-RESULT", False)
        assert run.ended().count(call_id) <= 1
    assert run.types("agent_end") == ["agent_end"]


async def test_a_cancel_in_a_finished_calls_end_event_still_ends_the_turn(tmp_path: Path) -> None:
    """The result commits and the cancel goes on: no later call runs, no next request."""

    ran: list[str] = []

    async def later(_args: Any, _ctx: Any) -> ToolResult:
        ran.append("later")
        return ToolResult(content=[])

    tools = [
        finished_tool("one", "ONE-RESULT", sequential=True),
        AgentTool(name="later", execute=later, execution_mode="sequential"),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "later")], tools)
    in_end = asyncio.Event()

    async def hold_the_end(event: Any) -> None:
        if event.type == "tool_execution_end":
            in_end.set()
            await asyncio.Event().wait()

    run.harness.subscribe(hold_the_end)
    run.begin()
    await asyncio.wait_for(in_end.wait(), timeout=5)
    await run.cancel()

    assert ran == []
    assert len(run.provider.seen) == 1


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_cancel_in_an_immediates_end_event_keeps_its_result_and_ends_the_turn(
    tmp_path: Path, sequential: bool
) -> None:
    ran: list[str] = []

    async def later(_args: Any, _ctx: Any) -> ToolResult:
        ran.append("later")
        return ToolResult(content=[])

    tools = [AgentTool(name="later", execute=later, execution_mode="sequential" if sequential else None)]
    run = await start(tmp_path, [("c1", "ghost"), ("c2", "later")], tools)
    in_end = asyncio.Event()

    async def hold_the_end(event: Any) -> None:
        if event.type == "tool_execution_end":
            in_end.set()
            await asyncio.Event().wait()

    run.harness.subscribe(hold_the_end)
    run.begin()
    await asyncio.wait_for(in_end.wait(), timeout=5)
    await run.cancel()

    assert ran == []
    assert len(run.provider.seen) == 1
    assert results_of(await run.reopened()) == {"c1": ("Unknown tool: ghost", True)}


async def test_a_hook_failing_on_the_way_out_withholds_the_output_and_keeps_the_cancel(
    tmp_path: Path,
) -> None:
    """c1 cancels the turn from inside itself and swallows it. Its hook then raises."""

    holder: dict[str, Any] = {}

    async def swallows(_args: Any, _ctx: Any) -> ToolResult:
        holder["run"].harness._current_turn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        return ToolResult(content=[TextContent(text="SECRET")])

    async def never(_args: Any, _ctx: Any) -> ToolResult:
        raise AssertionError("cancelled before its first step")

    tools = [
        AgentTool(name="one", execute=swallows),
        AgentTool(name="two", execute=never),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    holder["run"] = run

    def boom(_event: Any, _ctx: Any) -> None:
        raise RuntimeError("the hook broke")

    run.harness.hooks.on("tool_result", boom)
    task = run.begin()
    await asyncio.wait([task], timeout=5)

    assert task.cancelled(), "the cancel stays the cancel"
    assert results_of(run.harness.state.messages) == {"c1": (_CUT_TEXT, True)}


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_first_cancel_in_an_ordinary_commit_skips_that_result_and_ends_the_turn(
    tmp_path: Path, sequential: bool
) -> None:
    """Nothing was cancelled until the hook held c1's ``message_end``. A limit ADR-0290 states."""

    tools = [
        finished_tool("one", "ONE-RESULT", sequential=sequential),
        finished_tool("two", "TWO-RESULT", sequential=sequential),
    ]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    in_hook = asyncio.Event()

    async def park_on_the_first(event: Any, _ctx: Any) -> None:
        message = event.message
        if isinstance(message, ToolResultMessage) and message.tool_call_id == "c1":
            in_hook.set()
            await asyncio.Event().wait()

    run.harness.hooks.on("message_end", park_on_the_first)
    task = run.begin()
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    await run.harness.abort()
    await settle(task)

    assert len(run.provider.seen) == 1, "the cancel stays the cancel"
    results = results_of(await run.reopened())
    assert "c1" not in results
    if sequential:
        assert run.started() == ["c1"]
        assert "c2" not in results
    else:
        assert results["c2"] == ("TWO-RESULT", False)


async def test_a_further_cancel_reaches_a_call_stuck_in_its_cleanup(tmp_path: Path) -> None:
    """Nothing but the wait can hand the cancel on.

    c1 cancels the turn from inside itself, so c2 never begins, with c1 still in its
    cleanup, stuck until cancelled again.
    """

    holder: dict[str, Any] = {}
    in_cleanup = asyncio.Event()

    async def stuck(_args: Any, _ctx: Any) -> ToolResult:
        holder["run"].harness._current_turn_task.cancel()
        try:
            await asyncio.Event().wait()
        finally:
            in_cleanup.set()
            await asyncio.Event().wait()
        return ToolResult(content=[])

    async def never(_args: Any, _ctx: Any) -> ToolResult:
        raise AssertionError("cancelled before its first step")

    tools = [AgentTool(name="one", execute=stuck), AgentTool(name="two", execute=never)]
    run = await start(tmp_path, [("c1", "one"), ("c2", "two")], tools)
    holder["run"] = run
    task = run.begin()
    await asyncio.wait_for(in_cleanup.wait(), timeout=5)
    await asyncio.sleep(0.01)
    assert not task.done()
    turn = run.harness._current_turn_task
    assert turn is not None
    turn.cancel()
    await asyncio.wait([task], timeout=5)
    assert task.cancelled()


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_sink_failing_without_a_cancel_still_reaches_the_caller(sequential: bool) -> None:
    """Nothing changes when nobody cancelled: a sink that raises propagates, as it always did."""

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        return ToolResult(content=[TextContent(text="RESULT")])

    async def stream(_model: Any, _context: Any, _options: Any) -> AsyncIterator[Any]:
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[ToolCallContent(tool_call_id="c1", tool_name="one", input={})],
                stop_reason="tool_use",
            )
        )

    for broken in ("tool_execution_end", "message_start", "message_end"):
        async def emit(event: Any, broken: str = broken) -> None:
            if event.type == broken and (
                broken == "tool_execution_end"
                or isinstance(getattr(event, "message", None), ToolResultMessage)
            ):
                raise RuntimeError(f"sink broke on {event.type}")

        config = AgentLoopConfig(
            model=MODEL,
            convert_to_llm=lambda messages: messages,
            tool_execution="sequential" if sequential else "parallel",
        )
        context = AgentContext(
            system_prompt="", messages=[], tools=[AgentTool(name="one", execute=execute)]
        )
        with pytest.raises(RuntimeError, match=f"sink broke on {broken}"):
            await agent_loop(
                [UserMessage(content=[TextContent(text="go")])],
                context,
                config,
                emit=emit,
                stream_fn=stream,
            )
