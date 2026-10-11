"""#365: fatal request identity terminates before mutable message tools/queues."""

from __future__ import annotations

from dataclasses import replace

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import MessageEndEventResult
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent, UserMessage
from aelix_ai.tools import ToolResult

from tests.cli.test_provider_ownership_wire_365 import wire as wire
from tests.model_registry.test_provider_ownership_365 import row


def _texts(messages):
    return [
        part.text
        for message in messages
        for part in message.content
        if isinstance(part, TextContent)
    ]


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses"])
@pytest.mark.parametrize("queue", ["follow_up", "steer", "tool"])
async def test_fatal_snapshot_stops_before_tools_and_preserves_queued_inputs(
    tmp_path, wire, api, queue
):
    calls = 0
    tools = []

    def auth(model):
        nonlocal calls
        calls += 1
        attempt = calls
        checks = 0

        def validate(request):
            nonlocal checks
            checks += 1
            if attempt == 1 and checks == 2:
                raise RuntimeError("503 429 fatal snapshot")

        return {"apiKey": "B-fake", "validate": validate}

    async def execute(args, ctx):
        tools.append("executed")
        return ToolResult(content=[TextContent(text="result")])

    harness = AgentHarness(
        AgentHarnessOptions(
            model=replace(row(), api=api),
            get_api_key_and_headers=auth,
            tools=[AgentTool(name="work", execute=execute)],
        )
    )

    async def enqueue(event, ctx):
        if calls == 1 and queue != "tool":
            await getattr(harness, queue)("queued-one")
            await getattr(harness, queue)("queued-two")

    def rewrite(event, ctx):
        if calls == 1 and isinstance(event.message, AssistantMessage):
            return MessageEndEventResult(
                message=replace(
                    event.message,
                    stop_reason="tool_use" if queue == "tool" else "stop",
                    content=[ToolCallContent(tool_call_id="fake", tool_name="work", input={})]
                    if queue == "tool"
                    else [],
                )
            )

    harness.hooks.on("before_provider_payload", enqueue)
    harness.hooks.on("message_end", rewrite)
    await harness.next_turn("committed-before")
    await harness.prompt("first")
    assert calls == 1 and wire == [] and tools == []
    user_messages = [
        message for message in harness.state.messages if isinstance(message, UserMessage)
    ]
    assert _texts(user_messages) == ["committed-before", "first"]
    if queue != "tool":
        pending = harness._follow_up_queue if queue == "follow_up" else harness._steering_queue
        assert _texts(pending._messages) == ["queued-one", "queued-two"]
    # A distinct user prompt is allowed to create its own auth snapshot. The
    # fake SDK refuses with 401; pending follow-ups remain preserved as usual.
    await harness.prompt("second")
    assert calls == 2 and len(wire) == 1 and wire[0]["tag"] == "B"
    assert tools == []
    user_messages = [
        message for message in harness.state.messages if isinstance(message, UserMessage)
    ]
    assert _texts(user_messages).count("committed-before") == 1
    assert _texts(user_messages).count("first") == 1
    if queue == "steer":
        assert _texts(user_messages)[-1:] == ["queued-one"]
        assert _texts(harness._steering_queue._messages) == ["queued-two"]
        await harness.prompt("third")
        assert calls == 3 and len(wire) == 2
        user_messages = [
            message for message in harness.state.messages if isinstance(message, UserMessage)
        ]
        assert _texts(user_messages)[-1:] == ["queued-two"]
        assert harness._steering_queue._messages == []
    elif queue == "follow_up":
        assert _texts(harness._follow_up_queue._messages) == ["queued-one", "queued-two"]


@pytest.mark.parametrize("stop", [None, False, True])
async def test_low_level_response_boundary_preserves_default_tool_behavior(stop):
    from aelix_agent_core.default_convert import default_convert_to_llm
    from aelix_agent_core.loop import agent_loop
    from aelix_agent_core.types import AgentContext, AgentLoopConfig
    from aelix_ai.streaming import AssistantDoneEvent

    executed = []
    follow_drains = []

    async def execute(args, ctx):
        executed.append("work")
        return ToolResult(content=[], terminate=True)

    async def stream(model, context, options):
        yield AssistantDoneEvent(
            message=AssistantMessage(
                content=[ToolCallContent(tool_call_id="call", tool_name="work", input={})],
                stop_reason="tool_use",
            )
        )

    async def emit(event):
        return None

    def follow_up():
        follow_drains.append("drained")
        return []

    config = AgentLoopConfig(
        row(),
        default_convert_to_llm,
        get_follow_up_messages=follow_up,
        should_stop_after_response=(lambda message: stop) if stop is not None else None,
    )
    result = await agent_loop(
        [UserMessage(content=[TextContent(text="hi")])],
        AgentContext(messages=[], tools=[AgentTool(name="work", execute=execute)]),
        config,
        emit=emit,
        stream_fn=stream,
    )
    assert executed == ([] if stop else ["work"])
    assert follow_drains == ([] if stop else ["drained"])
    assert isinstance(result[1], AssistantMessage) and result[1].stop_reason == "tool_use"
