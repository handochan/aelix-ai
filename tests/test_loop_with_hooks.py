"""End-to-end integration tests: load_extensions → AgentHarness → prompt.

Spec references: Sprint 1 Phase 1.2 Spec §E + §D.1.12.

These tests drive the full stack:
  load_extensions([...]) → AgentHarness(options) → harness.prompt(...)

and verify that hook events (tool_call, tool_result, before_agent_start,
context) produce the correct observable effects in the loop's state.messages
and return value.

The mock stream helper is identical to the pattern in tests/test_agent_loop.py
so both test files stay coherent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix.agent.default_convert import default_convert_to_llm
from aelix.agent.loop import agent_loop
from aelix.agent.types import AgentContext, AgentLoopConfig, AgentTool, BeforeToolCallResult
from aelix.ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
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
from aelix.builtin.guardrail import GuardrailExtension
from aelix.builtin.policy import PolicyExtension
from aelix.extensions.api import ExtensionAPI, ExtensionContext
from aelix.extensions.loader import load_extensions
from aelix.harness.core import AgentHarness, AgentHarnessOptions
from aelix.harness.hooks import (
    BeforeAgentStartResult,
    ContextResult,
    ToolCallResult,
    ToolResultPatch,
)

# ===========================================================================
# Mock stream helpers (mirrors test_agent_loop.py pattern exactly)
# ===========================================================================


def _make_mock_stream(turn_finals: list[AssistantMessage]) -> Any:
    """Return a stream_fn that yields one start+end per turn from ``turn_finals``."""

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
                f"mock stream_fn ran out of turns at idx={i} "
                f"(script length={len(turn_finals)}); loop took an extra turn"
            )
        final = turn_finals[i]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=final)

    return fn


def _tool_call_message(tool_name: str, tool_id: str = "t1", **input_kwargs: Any) -> AssistantMessage:
    return AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id=tool_id,
                tool_name=tool_name,
                input=input_kwargs,
            )
        ],
        stop_reason="tool_use",
    )


def _text_message(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        stop_reason="end_turn",
    )


def _emit_collector() -> tuple[list[Any], Any]:
    events: list[Any] = []

    async def emit(event: Any) -> None:
        events.append(event)

    return events, emit


async def _make_echo_tool() -> AgentTool:
    async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text=f"echoed: {args.get('text', '')}")])

    return AgentTool(
        name="echo",
        description="Echoes text.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        execute=execute,
    )


# ===========================================================================
# test_tool_call_hook_blocks_echo
# ===========================================================================


async def test_tool_call_hook_blocks_echo() -> None:
    """Extension subscribed to tool_call returns block=True; tool_result is is_error=True."""

    echo_tool = await _make_echo_tool()

    # Inline factory that blocks the "echo" tool.
    def blocking_factory(aelix: ExtensionAPI) -> None:
        def on_tool_call(event: Any, ctx: ExtensionContext) -> ToolCallResult:
            if event.tool_name == "echo":
                return ToolCallResult(block=True, reason="[test] echo is not permitted")
            return ToolCallResult(block=False)

        aelix.on("tool_call", on_tool_call)

    result = await load_extensions([blocking_factory])

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            extensions=result.extensions,
            runtime=result.runtime,
            tools=[echo_tool],
            stream_fn=_make_mock_stream(
                [
                    _tool_call_message("echo", text="ping"),
                    _text_message("done"),
                ]
            ),
        )
    )

    msgs = await harness.prompt("call echo")

    tool_results = [m for m in msgs if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1, "expected exactly one tool result message"
    tr = tool_results[0]
    assert tr.is_error is True, "blocked tool result must be is_error=True"
    assert "[test] echo is not permitted" in tr.content[0].text


# ===========================================================================
# test_tool_result_hook_rewrites_output
# ===========================================================================


async def test_tool_result_hook_rewrites_output() -> None:
    """Extension subscribed to tool_result patches content to '[redacted]'."""

    echo_tool = await _make_echo_tool()

    def redact_factory(aelix: ExtensionAPI) -> None:
        def on_tool_result(event: Any, ctx: ExtensionContext) -> ToolResultPatch:
            return ToolResultPatch(content=[TextContent(text="[redacted]")])

        aelix.on("tool_result", on_tool_result)

    result = await load_extensions([redact_factory])

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            extensions=result.extensions,
            runtime=result.runtime,
            tools=[echo_tool],
            stream_fn=_make_mock_stream(
                [
                    _tool_call_message("echo", text="hello"),
                    _text_message("ok"),
                ]
            ),
        )
    )

    msgs = await harness.prompt("call echo")

    tool_results = [m for m in msgs if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    tr = tool_results[0]
    assert len(tr.content) == 1
    assert tr.content[0].text == "[redacted]"


# ===========================================================================
# test_tool_result_hook_chains_patches
# ===========================================================================


async def test_tool_result_hook_chains_patches() -> None:
    """Two tool_result hooks: H1 sets content, H2 sets is_error=True; both apply."""

    echo_tool = await _make_echo_tool()

    def h1_factory(aelix: ExtensionAPI) -> None:
        def on_tool_result(event: Any, ctx: ExtensionContext) -> ToolResultPatch:
            return ToolResultPatch(content=[TextContent(text="h1-content")])

        aelix.on("tool_result", on_tool_result)

    def h2_factory(aelix: ExtensionAPI) -> None:
        def on_tool_result(event: Any, ctx: ExtensionContext) -> ToolResultPatch:
            return ToolResultPatch(is_error=True)

        aelix.on("tool_result", on_tool_result)

    result = await load_extensions([h1_factory, h2_factory])

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            extensions=result.extensions,
            runtime=result.runtime,
            tools=[echo_tool],
            stream_fn=_make_mock_stream(
                [
                    _tool_call_message("echo", text="x"),
                    _text_message("done"),
                ]
            ),
        )
    )

    msgs = await harness.prompt("go")

    tool_results = [m for m in msgs if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    tr = tool_results[0]
    # H1 set content to "h1-content", H2 set is_error=True; both must be visible.
    assert tr.content[0].text == "h1-content"
    assert tr.is_error is True


# ===========================================================================
# test_before_agent_start_injects_system_message
# ===========================================================================


async def test_before_agent_start_injects_system_message() -> None:
    """Extension returns BeforeAgentStartResult with messages; they appear before the user prompt."""

    injected_text = "injected-context-msg"

    def inject_factory(aelix: ExtensionAPI) -> None:
        def on_before_agent_start(event: Any, ctx: ExtensionContext) -> BeforeAgentStartResult:
            return BeforeAgentStartResult(
                messages=[UserMessage(content=[TextContent(text=injected_text)])]
            )

        aelix.on("before_agent_start", on_before_agent_start)

    def capture_factory(aelix: ExtensionAPI) -> None:
        def on_context(event: Any, ctx: ExtensionContext) -> None:
            pass  # capture hook registered to verify context is called

        aelix.on("context", on_context)

    result2 = await load_extensions([inject_factory, capture_factory])

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            extensions=result2.extensions,
            runtime=result2.runtime,
            stream_fn=_make_mock_stream([_text_message("reply")]),
        )
    )

    await harness.prompt("user-prompt")

    # The injected message must appear before the user prompt in harness.state.messages.
    all_msgs = harness.state.messages
    texts = [
        c.text
        for m in all_msgs
        if isinstance(m, UserMessage)
        for c in m.content
        if isinstance(c, TextContent)
    ]
    assert injected_text in texts, f"injected text not found; got texts={texts}"
    # Injected message must appear before the user prompt.
    injected_idx = texts.index(injected_text)
    user_idx = texts.index("user-prompt")
    assert injected_idx < user_idx, "injected message must precede user prompt"


# ===========================================================================
# test_context_hook_filters_messages
# ===========================================================================


async def test_context_hook_filters_messages() -> None:
    """Extension drops every message except the last; LLM sees only one message."""

    llm_call_messages: list[list[Any]] = []

    # We capture what convert_to_llm receives by wrapping it.
    original_convert = default_convert_to_llm

    def capturing_convert(messages: list[Any]) -> list[Any]:
        result = original_convert(messages)
        llm_call_messages.append(list(result))
        return result

    def filter_factory(aelix: ExtensionAPI) -> None:
        def on_context(event: Any, ctx: ExtensionContext) -> ContextResult:
            # Keep only the last message.
            if event.messages:
                return ContextResult(messages=[event.messages[-1]])
            return ContextResult(messages=[])

        aelix.on("context", on_context)

    result = await load_extensions([filter_factory])

    # Prime the harness with 2 initial messages so the filter has something to drop.
    initial_msgs = [
        UserMessage(content=[TextContent(text="old message 1")]),
        UserMessage(content=[TextContent(text="old message 2")]),
    ]

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            extensions=result.extensions,
            runtime=result.runtime,
            initial_messages=initial_msgs,
            convert_to_llm=capturing_convert,
            stream_fn=_make_mock_stream([_text_message("ok")]),
        )
    )

    await harness.prompt("new prompt")

    # LLM must have been called with only 1 message (the filter kept last only).
    assert llm_call_messages, "convert_to_llm was never called"
    first_call = llm_call_messages[0]
    assert len(first_call) == 1, (
        f"context hook should have filtered to 1 message; got {len(first_call)}"
    )


# ===========================================================================
# test_phase_1_1_callbacks_still_work_without_harness
# ===========================================================================


async def test_phase_1_1_callbacks_still_work_without_harness() -> None:
    """Direct agent_loop with before_tool_call callback path works (Phase 1.1 regression)."""

    executed = {"count": 0}

    async def run_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        executed["count"] += 1
        return ToolResult(content=[TextContent(text="never")])

    run_tool = AgentTool(
        name="run",
        description="runs",
        parameters={"type": "object", "properties": {}, "required": []},
        execute=run_execute,
    )

    turn1 = AssistantMessage(
        content=[ToolCallContent(tool_call_id="t1", tool_name="run", input={})],
        stop_reason="tool_use",
    )
    turn2 = _text_message("ok")

    events, emit = _emit_collector()

    async def block_all(ctx: Any) -> BeforeToolCallResult:
        return BeforeToolCallResult(block=True, reason="not allowed in 1.1 test")

    config = AgentLoopConfig(
        model=Model(id="mock", provider="mock"),
        convert_to_llm=default_convert_to_llm,
        before_tool_call=block_all,
    )

    new_messages = await agent_loop(
        [UserMessage(content=[TextContent(text="run")])],
        AgentContext(tools=[run_tool]),
        config,
        emit=emit,
        stream_fn=_make_mock_stream([turn1, turn2]),
    )

    # Tool must NOT have been executed.
    assert executed["count"] == 0, "tool should have been blocked by before_tool_call"
    tool_results = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is True
    assert "not allowed in 1.1 test" in tool_results[0].content[0].text


# ===========================================================================
# D.1.12 full-chain tests
# ===========================================================================


async def test_full_chain_policy_blocks_real_tool_call() -> None:
    """load_extensions([PolicyExtension(deny_tools={'echo'})]) → harness → echo blocked."""

    echo_tool = await _make_echo_tool()

    result = await load_extensions([PolicyExtension(deny_tools=frozenset({"echo"}))])
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            extensions=result.extensions,
            runtime=result.runtime,
            tools=[echo_tool],
            stream_fn=_make_mock_stream(
                [
                    _tool_call_message("echo", text="blocked?"),
                    _text_message("finished"),
                ]
            ),
        )
    )

    msgs = await harness.prompt("call echo")

    tool_results = [m for m in msgs if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1, "expected one tool result"
    tr = tool_results[0]
    assert tr.is_error is True, "policy block must produce is_error=True"
    assert "echo" in tr.content[0].text, f"block reason should mention tool name; got: {tr.content[0].text}"
    # Also verify it accumulates in state.messages.
    state_tool_results = [m for m in harness.state.messages if isinstance(m, ToolResultMessage)]
    assert state_tool_results, "blocked tool result must appear in state.messages"


async def test_full_chain_guardrail_blocks_rm_rf_in_real_loop() -> None:
    """GuardrailExtension blocks a bash call with 'rm -rf /tmp/x'."""

    # Register a no-op bash tool so the loop has something to try to execute.
    bash_executed = {"count": 0}

    async def bash_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        bash_executed["count"] += 1
        return ToolResult(content=[TextContent(text="executed")])

    bash_tool = AgentTool(
        name="bash",
        description="Runs bash commands.",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=bash_execute,
    )

    result = await load_extensions([GuardrailExtension()])
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            extensions=result.extensions,
            runtime=result.runtime,
            tools=[bash_tool],
            stream_fn=_make_mock_stream(
                [
                    AssistantMessage(
                        content=[
                            ToolCallContent(
                                tool_call_id="t1",
                                tool_name="bash",
                                input={"command": "rm -rf /tmp/x"},
                            )
                        ],
                        stop_reason="tool_use",
                    ),
                    _text_message("done"),
                ]
            ),
        )
    )

    msgs = await harness.prompt("delete /tmp/x")

    # The guardrail should have blocked execution.
    assert bash_executed["count"] == 0, "bash execute must not have been called when guardrail blocks"
    tool_results = [m for m in msgs if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    tr = tool_results[0]
    assert tr.is_error is True
    assert "guardrail" in tr.content[0].text.lower(), (
        f"block reason should mention guardrail; got: {tr.content[0].text}"
    )


async def test_full_chain_two_extensions_one_patches_args_other_blocks() -> None:
    """Extension A patches event.args['text']='patched'; Extension B blocks if args contain 'patched'.

    Verifies D.1.5: dict reference flows across handler boundaries so B sees A's mutation.
    """

    executed = {"count": 0}

    async def guarded_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        executed["count"] += 1
        return ToolResult(content=[TextContent(text=f"echoed: {args.get('text', '')}")])

    guarded_echo = AgentTool(
        name="echo",
        description="guarded echo",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        execute=guarded_execute,
    )

    # Extension A: mutates args in place.
    def ext_a_factory(aelix: ExtensionAPI) -> None:
        def on_tool_call(event: Any, ctx: ExtensionContext) -> None:
            event.args["text"] = "patched"

        aelix.on("tool_call", on_tool_call)

    # Extension B: blocks if args["text"] == "patched".
    def ext_b_factory(aelix: ExtensionAPI) -> None:
        def on_tool_call(event: Any, ctx: ExtensionContext) -> ToolCallResult:
            if event.args.get("text") == "patched":
                return ToolCallResult(block=True, reason="[test] blocked due to patched arg")
            return ToolCallResult(block=False)

        aelix.on("tool_call", on_tool_call)

    # A registered before B — A must run first so B sees the mutation.
    result = await load_extensions([ext_a_factory, ext_b_factory])

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            extensions=result.extensions,
            runtime=result.runtime,
            tools=[guarded_echo],
            stream_fn=_make_mock_stream(
                [
                    _tool_call_message("echo", text="original"),
                    _text_message("done"),
                ]
            ),
        )
    )

    msgs = await harness.prompt("test arg mutation")

    # B must have blocked based on A's mutation.
    assert executed["count"] == 0, "tool execute must not have run; B should have blocked"
    tool_results = [m for m in msgs if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    tr = tool_results[0]
    assert tr.is_error is True
    assert "[test] blocked due to patched arg" in tr.content[0].text
