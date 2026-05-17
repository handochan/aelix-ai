"""Tests for AgentHarness — phase machine, queue semantics, tool merging, hooks.

Mock stream helpers are module-level functions following the pattern from
tests/test_agent_loop.py:_make_mock_stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from aelix.agent.types import (
    AfterToolCallResult,
    AgentEvent,
    AgentTool,
)
from aelix.ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
)
from aelix.ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix.ai.tools import ToolExecutionContext, ToolResult
from aelix.extensions.api import Extension, _ExtensionRuntime
from aelix.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix.harness.hooks import (
    ToolCallHookEvent,
    ToolResultPatch,
)

# ============================================================
# Shared stream helpers
# ============================================================


def _make_mock_stream(turn_finals: list[AssistantMessage]) -> Any:
    """Return a stream_fn that yields one start+end per turn."""

    idx = {"i": 0}

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        i = idx["i"]
        idx["i"] += 1
        if i >= len(turn_finals):
            raise AssertionError(
                f"mock stream_fn exhausted at idx={i} "
                f"(script length={len(turn_finals)})"
            )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=turn_finals[i])

    return fn


def _text_msg(text: str, stop_reason: str = "end_turn") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        stop_reason=stop_reason,
    )


def _tool_call_msg(tool_name: str, tool_call_id: str, input: dict) -> AssistantMessage:
    return AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                input=input,
            )
        ],
        stop_reason="tool_use",
    )


def _simple_harness(**kwargs: Any) -> AgentHarness:
    """Convenience: create harness with a single end_turn stream."""
    if "stream_fn" not in kwargs:
        kwargs["stream_fn"] = _make_mock_stream([_text_msg("ok")])
    return AgentHarness(AgentHarnessOptions(**kwargs))


async def _make_echo_tool(executed: dict | None = None) -> AgentTool:
    async def echo_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        if executed is not None:
            executed["count"] = executed.get("count", 0) + 1
            executed["last_args"] = dict(args)
        return ToolResult(content=[TextContent(text=f"echoed: {args.get('text', '')}")])

    return AgentTool(name="echo", execute=echo_execute)


# ============================================================
# Phase machine tests
# ============================================================


async def test_idle_initial_phase() -> None:
    h = _simple_harness()
    assert h.phase == "idle"
    assert h.is_idle is True


async def test_prompt_transitions_idle_to_turn_to_idle() -> None:
    phases_seen: list[str] = []

    async def listener(event: AgentEvent) -> None:
        phases_seen.append(h.phase)

    h = _simple_harness()
    h.subscribe(listener)
    assert h.phase == "idle"
    await h.prompt("hello")
    assert h.phase == "idle"
    # During the turn at least one event saw "turn"
    assert "turn" in phases_seen


async def test_prompt_when_busy_raises_busy_error() -> None:
    """Second prompt while first is in-flight raises AgentHarnessError('busy')."""

    # Use a gate to hold the first prompt mid-stream so we can attempt a second
    gate = asyncio.Event()

    async def slow_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("done"))

    h = AgentHarness(AgentHarnessOptions(stream_fn=slow_stream))

    # Start first prompt in background
    first_task = asyncio.create_task(h.prompt("first"))
    # Give event loop a tick so _run() sets phase="turn"
    await asyncio.sleep(0)

    with pytest.raises(AgentHarnessError) as exc_info:
        await h.prompt("second")
    assert exc_info.value.code == "busy"

    # Unblock and clean up
    gate.set()
    await first_task


# ============================================================
# Steer / follow_up queue semantics (D.1.10 Pi parity)
# ============================================================


async def test_steer_when_idle_enqueues_for_next_prompt() -> None:
    """steer() while idle must NOT raise; next prompt must drain the steering message."""

    messages_seen: list[str] = []

    async def recording_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        # Record messages that arrived in the context
        for msg in context.messages:
            if hasattr(msg, "content"):
                for block in msg.content:
                    if isinstance(block, TextContent):
                        messages_seen.append(block.text)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("ok"))

    h = AgentHarness(AgentHarnessOptions(stream_fn=recording_stream))

    # Must not raise even though harness is idle
    await h.steer("steering thought")
    await h.prompt("main prompt")

    assert "steering thought" in messages_seen


async def test_steer_during_turn_queues_message() -> None:
    """steer() during an active turn enqueues a message (no raise)."""

    gate = asyncio.Event()
    steer_raised: list[Exception] = []

    async def slow_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("ok"))

    h = AgentHarness(AgentHarnessOptions(stream_fn=slow_stream))
    first_task = asyncio.create_task(h.prompt("go"))
    await asyncio.sleep(0)  # let turn start

    try:
        await h.steer("steer while busy")
    except Exception as exc:
        steer_raised.append(exc)

    gate.set()
    await first_task

    assert steer_raised == [], "steer() must never raise during a turn"


async def test_follow_up_when_idle_enqueues_for_next_prompt() -> None:
    """follow_up() while idle must NOT raise; next prompt must drain the follow-up."""

    messages_seen: list[str] = []

    async def recording_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        for msg in context.messages:
            if hasattr(msg, "content"):
                for block in msg.content:
                    if isinstance(block, TextContent):
                        messages_seen.append(block.text)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("ok"))

    h = AgentHarness(AgentHarnessOptions(stream_fn=recording_stream))

    await h.follow_up("follow-up thought")
    await h.prompt("main prompt")

    assert "follow-up thought" in messages_seen


# ============================================================
# Abort
# ============================================================


async def test_abort_during_turn_clears_queues() -> None:
    """abort() must drain both queues."""

    gate = asyncio.Event()

    async def slow_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("done"))

    h = AgentHarness(AgentHarnessOptions(stream_fn=slow_stream))
    task = asyncio.create_task(h.prompt("go"))
    await asyncio.sleep(0)

    await h.steer("s1")
    await h.follow_up("f1")
    await h.abort()

    gate.set()
    await task

    # After abort, queues are cleared — draining yields nothing
    steering = h._steering_queue.drain()
    follow_up = h._follow_up_queue.drain()
    assert steering == []
    assert follow_up == []


# ============================================================
# Dispose (D.1.13 M-4)
# ============================================================


async def test_dispose_runs_all_extension_cleanups() -> None:
    """dispose() must run every extension cleanup."""

    cleaned: list[str] = []

    def make_ext(name: str) -> Extension:
        ext = Extension(name=name)
        ext.cleanups.append(lambda: cleaned.append(name))
        return ext

    ext_a = make_ext("ext_a")
    ext_b = make_ext("ext_b")

    runtime = _ExtensionRuntime()
    h = AgentHarness(
        AgentHarnessOptions(
            extensions=[ext_a, ext_b],
            stream_fn=_make_mock_stream([_text_msg("ok")]),
            runtime=runtime,
        )
    )

    await h.dispose()
    assert "ext_a" in cleaned
    assert "ext_b" in cleaned


async def test_dispose_during_turn_aborts_first_then_cleans_lifo() -> None:
    """dispose() while in a turn: abort fires, wait_for_idle, cleanups run LIFO."""

    order: list[str] = []
    gate = asyncio.Event()

    async def slow_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("done"))

    ext_a = Extension(name="ext_a")
    ext_a.cleanups.append(lambda: order.append("cleanup_a"))
    ext_b = Extension(name="ext_b")
    ext_b.cleanups.append(lambda: order.append("cleanup_b"))

    h = AgentHarness(
        AgentHarnessOptions(
            extensions=[ext_a, ext_b],
            stream_fn=slow_stream,
        )
    )

    task = asyncio.create_task(h.prompt("go"))
    await asyncio.sleep(0)
    assert h.phase == "turn"

    # Unblock turn then dispose
    gate.set()

    dispose_task = asyncio.create_task(h.dispose())
    await asyncio.gather(task, dispose_task, return_exceptions=True)

    # Cleanups ran in LIFO: ext_b registered after ext_a, so cleanup_b first
    assert "cleanup_a" in order
    assert "cleanup_b" in order
    assert order.index("cleanup_b") < order.index("cleanup_a")


# ============================================================
# Re-entrancy guard
# ============================================================


async def test_hook_handler_reentry_busy_raises() -> None:
    """A hook handler calling await harness.prompt() must get a busy error."""

    reentry_error: list[AgentHarnessError] = []

    gate = asyncio.Event()

    async def blocking_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        await gate.wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("ok"))

    h = AgentHarness(AgentHarnessOptions(stream_fn=blocking_stream))

    async def reentrant_handler(event: Any, ctx: Any) -> None:
        try:
            await h.prompt("reentrant")
        except AgentHarnessError as exc:
            reentry_error.append(exc)

    h.hooks.on("agent_start", reentrant_handler)

    gate.set()
    await h.prompt("initial")

    assert len(reentry_error) == 1
    assert reentry_error[0].code == "busy"


# ============================================================
# Subscribe / lifecycle events
# ============================================================


async def test_subscribe_receives_lifecycle_events_in_order() -> None:
    """Subscriber must see agent_start before turn_start before agent_end."""

    events: list[AgentEvent] = []
    h = _simple_harness()
    h.subscribe(lambda e: events.append(e))

    await h.prompt("hi")

    types = [e.type for e in events]
    assert "agent_start" in types
    assert "agent_end" in types
    assert types.index("agent_start") < types.index("agent_end")


async def test_handler_ordering_within_event_matches_registration_order() -> None:
    """Handlers for the same event fire in insertion order."""

    fired: list[int] = []

    h = AgentHarness(
        AgentHarnessOptions(stream_fn=_make_mock_stream([_text_msg("ok")]))
    )

    h.hooks.on("agent_start", lambda e, ctx: fired.append(1))
    h.hooks.on("agent_start", lambda e, ctx: fired.append(2))
    h.hooks.on("agent_start", lambda e, ctx: fired.append(3))

    await h.prompt("hi")

    assert fired == [1, 2, 3]


# ============================================================
# Tool merge rules
# ============================================================


async def test_application_supplied_tools_merge_with_extension_tools() -> None:
    """Both extension tools and app tools are available after merge."""

    async def ext_execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="from_ext")])

    async def app_execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="from_app")])

    ext = Extension(name="ext_with_tool")
    ext.tools["ext_tool"] = AgentTool(name="ext_tool", execute=ext_execute)

    app_tool = AgentTool(name="app_tool", execute=app_execute)

    h = AgentHarness(
        AgentHarnessOptions(
            extensions=[ext],
            tools=[app_tool],
            stream_fn=_make_mock_stream([_text_msg("ok")]),
        )
    )

    tool_names = {t.name for t in h.state.tools}
    assert "ext_tool" in tool_names
    assert "app_tool" in tool_names


async def test_application_supplied_tool_overrides_extension_tool_with_same_name() -> None:
    """App-supplied tool wins over extension tool with same name (D.1.13 M-9)."""

    async def ext_execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="from_ext")])

    async def app_execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="from_app")])

    ext = Extension(name="ext_with_echo")
    ext.tools["echo"] = AgentTool(name="echo", execute=ext_execute)

    app_tool = AgentTool(name="echo", execute=app_execute)

    h = AgentHarness(
        AgentHarnessOptions(
            extensions=[ext],
            tools=[app_tool],
            stream_fn=_make_mock_stream([_text_msg("ok")]),
        )
    )

    merged = {t.name: t for t in h.state.tools}
    assert merged["echo"].execute is app_execute


async def test_first_extension_wins_collision_between_two_extensions() -> None:
    """When two extensions register the same tool name, the first one wins."""

    async def execute_a(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="from_a")])

    async def execute_b(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="from_b")])

    ext_a = Extension(name="ext_a")
    ext_a.tools["echo"] = AgentTool(name="echo", execute=execute_a)

    ext_b = Extension(name="ext_b")
    ext_b.tools["echo"] = AgentTool(name="echo", execute=execute_b)

    # ext_a listed first
    h = AgentHarness(
        AgentHarnessOptions(
            extensions=[ext_a, ext_b],
            stream_fn=_make_mock_stream([_text_msg("ok")]),
        )
    )

    merged = {t.name: t for t in h.state.tools}
    # First extension wins: ext_a's execute
    assert merged["echo"].execute is execute_a


# ============================================================
# D.1.5 — arg mutation visibility
# ============================================================


async def test_tool_call_hook_arg_mutation_visible_to_tool_execute() -> None:
    """A tool_call hook that mutates event.args['foo'] must be seen by tool.execute."""

    seen_in_tool: dict = {}
    executed = asyncio.Event()

    async def patching_handler(event: ToolCallHookEvent, ctx: Any) -> None:
        event.args["foo"] = 42

    async def capturing_tool(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        seen_in_tool.update(args)
        executed.set()
        return ToolResult(content=[TextContent(text="done")])

    tool = AgentTool(name="cap_tool", execute=capturing_tool)

    stream = _make_mock_stream(
        [
            _tool_call_msg("cap_tool", "t1", {"original": "value"}),
            _text_msg("finished"),
        ]
    )

    h = AgentHarness(
        AgentHarnessOptions(
            tools=[tool],
            stream_fn=stream,
        )
    )
    h.hooks.on("tool_call", patching_handler)

    await h.prompt("run cap_tool")

    assert seen_in_tool.get("foo") == 42


async def test_tool_call_hook_arg_mutation_visible_to_after_tool_call() -> None:
    """Args mutated by a tool_call hook must be visible in after_tool_call callback."""

    seen_in_after: dict = {}

    async def patching_handler(event: ToolCallHookEvent, ctx: Any) -> None:
        event.args["injected"] = "hook_value"

    async def noop_tool(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="ok")])

    def after_tool_call_cb(ctx: Any) -> None:
        seen_in_after.update(ctx.args)

    tool = AgentTool(name="noop", execute=noop_tool)

    stream = _make_mock_stream(
        [
            _tool_call_msg("noop", "t1", {}),
            _text_msg("finished"),
        ]
    )

    h = AgentHarness(
        AgentHarnessOptions(
            tools=[tool],
            stream_fn=stream,
            after_tool_call=after_tool_call_cb,
        )
    )
    h.hooks.on("tool_call", patching_handler)

    await h.prompt("run noop")

    assert seen_in_after.get("injected") == "hook_value"


# ============================================================
# D.1.6 — composition order: hook patch first, app override on top
# ============================================================


# ============================================================
# C-1: Lifecycle handler exceptions must not break the loop
# ============================================================


async def test_lifecycle_handler_exception_does_not_break_prompt() -> None:
    """A lifecycle hook handler that raises RuntimeError must not abort prompt()."""

    h = AgentHarness(
        AgentHarnessOptions(stream_fn=_make_mock_stream([_text_msg("ok")]))
    )

    def exploding_handler(event: Any, ctx: Any) -> None:
        raise RuntimeError("lifecycle handler exploded")

    h.hooks.on("turn_start", exploding_handler)

    # Must complete successfully despite the handler raising.
    messages = await h.prompt("hello")
    assert messages  # got a response back
    assert h.phase == "idle"


# ============================================================
# C-2: prompt() re-entrancy race
# ============================================================


async def test_prompt_race_during_before_agent_start_raises_busy() -> None:
    """Concurrent prompt() calls: exactly one succeeds, exactly one raises busy."""

    async def slow_before_agent_start(event: Any, ctx: Any) -> None:
        # Simulate a slow before_agent_start handler so the second call arrives
        # while the first is still in the pre-run phase.
        await asyncio.sleep(0.01)

    h = AgentHarness(
        AgentHarnessOptions(stream_fn=_make_mock_stream([_text_msg("ok")]))
    )
    h.hooks.on("before_agent_start", slow_before_agent_start)

    results = await asyncio.gather(
        h.prompt("first"),
        h.prompt("second"),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, list)]
    errors = [r for r in results if isinstance(r, AgentHarnessError)]

    assert len(successes) == 1, f"Expected exactly 1 success, got: {results}"
    assert len(errors) == 1, f"Expected exactly 1 busy error, got: {results}"
    assert errors[0].code == "busy"


# ============================================================
# Hook patch + app callback composition
# ============================================================


async def test_hook_patch_then_app_callback_composition_order() -> None:
    """Hook returns ToolResultPatch; app after_tool_call returns AfterToolCallResult.
    Hook applied first, then app overrides — app's non-None fields win.
    """

    async def noop_tool(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="original")])

    # Hook patches content to "from_hook"
    async def tool_result_hook(event: Any, ctx: Any) -> ToolResultPatch:
        return ToolResultPatch(content=[TextContent(text="from_hook")])

    # App callback patches content to "from_app" — wins over hook
    def after_tool_call_cb(ctx: Any) -> AfterToolCallResult:
        return AfterToolCallResult(content=[TextContent(text="from_app")])

    tool = AgentTool(name="noop", execute=noop_tool)

    stream = _make_mock_stream(
        [
            _tool_call_msg("noop", "t1", {}),
            _text_msg("finished"),
        ]
    )

    h = AgentHarness(
        AgentHarnessOptions(
            tools=[tool],
            stream_fn=stream,
            after_tool_call=after_tool_call_cb,
        )
    )
    h.hooks.on("tool_result", tool_result_hook)

    new_messages = await h.prompt("run noop")

    # Find the ToolResultMessage in new messages
    tool_results = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    # App override wins — final content should be "from_app"
    assert tool_results[0].content[0].text == "from_app"


# ============================================================
# Queue drain: steering + follow_up across multi-turn
# ============================================================


async def test_harness_drains_steering_and_follow_up_across_multi_turn() -> None:
    """Both steering and follow_up queues drain in the correct turn positions.

    Steering messages are injected between turns (before the LLM sees the next
    context). Follow-up messages are appended after the final assistant message.
    This test verifies both queues are drained and their contents appear in the
    context seen by the stream function.
    """

    # Capture the text of every UserMessage/TextContent block seen per turn.
    turns_seen: list[list[str]] = []

    async def recording_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        turn_texts: list[str] = []
        for msg in context.messages:
            if hasattr(msg, "content"):
                for block in msg.content:
                    if isinstance(block, TextContent):
                        turn_texts.append(block.text)
        turns_seen.append(turn_texts)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=_text_msg("ok"))

    h = AgentHarness(
        AgentHarnessOptions(
            stream_fn=recording_stream,
            steering_mode="all",
            follow_up_mode="all",
        )
    )

    # Enqueue both queues before prompt — they should drain during the run.
    await h.steer("steer-msg")
    await h.follow_up("follow-up-msg")

    await h.prompt("initial-prompt")

    # The initial prompt turn must have seen "initial-prompt".
    assert any("initial-prompt" in t for t in turns_seen[0])
    # The steering message is prepended as a turn input (before the LLM call).
    all_seen = [text for turn in turns_seen for text in turn]
    assert "steer-msg" in all_seen, f"steering message not found in {all_seen}"
    assert "follow-up-msg" in all_seen, f"follow-up message not found in {all_seen}"


# ============================================================
# P-1: Lifecycle close-out on hook fail
# ============================================================


async def test_lifecycle_emits_close_out_when_hook_raises() -> None:
    """When a mutation hook (tool_call) raises, harness must emit agent_end before re-raising."""

    events_seen: list[str] = []
    raise_error: list[bool] = [True]

    async def tool_execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="ok")])

    tool = AgentTool(name="trigger", execute=tool_execute)

    stream = _make_mock_stream(
        [
            _tool_call_msg("trigger", "t1", {}),
        ]
    )

    h = AgentHarness(
        AgentHarnessOptions(tools=[tool], stream_fn=stream)
    )

    async def raising_handler(event: Any, ctx: Any) -> None:
        if raise_error[0]:
            raise RuntimeError("hook intentionally raises")

    h.hooks.on("tool_call", raising_handler)
    h.subscribe(lambda e: events_seen.append(e.type))

    with pytest.raises(AgentHarnessError) as exc_info:
        await h.prompt("run trigger")

    assert exc_info.value.code == "hook"
    # Pi parity: agent_end must have been observed via subscribe even on hook failure
    assert "agent_end" in events_seen, f"agent_end not seen in {events_seen}"
    # Harness must return to idle after the failure
    assert h.phase == "idle"
