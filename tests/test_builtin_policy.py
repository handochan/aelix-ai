"""Tests for the built-in PolicyExtension.

PolicyExtension is an ExtensionFactory callable — ``pol(aelix)`` registers the
handler. Tests exercise it both in isolation (via the HookBus directly) and
in a full AgentHarness integration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.builtin.policy import PolicyExtension
from aelix_coding_agent.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime

# ============================================================
# Helpers
# ============================================================


def _make_mock_stream(turn_finals: list[AssistantMessage]) -> Any:
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
                f"mock stream_fn exhausted at idx={i}"
            )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=turn_finals[i])

    return fn


def _make_event(tool_name: str) -> ToolCallHookEvent:
    """Build a minimal ToolCallHookEvent for unit testing the handler directly."""
    return ToolCallHookEvent(
        tool_call_id="t1",
        tool_name=tool_name,
        args={},
    )


def _build_ext_with_policy(policy: PolicyExtension) -> Extension:
    """Wire a PolicyExtension into an Extension object via ExtensionAPI."""
    runtime = _ExtensionRuntime()
    ext = Extension(name="policy_ext")
    api = ExtensionAPI(extension=ext, runtime=runtime)
    policy(api)
    return ext


# ============================================================
# Unit tests — handler in isolation
# ============================================================


async def test_allow_all_when_allow_tools_none() -> None:
    """With allow_tools=None, no tool is blocked (only deny_tools applies)."""
    policy = PolicyExtension(allow_tools=None, deny_tools=frozenset())
    result = policy._on_tool_call(_make_event("bash"), None)  # type: ignore[arg-type]
    assert result is None


async def test_deny_list_blocks_named_tool() -> None:
    """A tool in deny_tools is blocked regardless of allow_tools."""
    policy = PolicyExtension(deny_tools=frozenset({"bash"}))
    result = policy._on_tool_call(_make_event("bash"), None)  # type: ignore[arg-type]
    assert isinstance(result, ToolCallResult)
    assert result.block is True


async def test_allow_list_blocks_unlisted_tool() -> None:
    """With allow_tools={"echo"}, calling "bash" returns a blocking result."""
    policy = PolicyExtension(allow_tools=frozenset({"echo"}), deny_tools=frozenset())
    result = policy._on_tool_call(_make_event("bash"), None)  # type: ignore[arg-type]
    assert isinstance(result, ToolCallResult)
    assert result.block is True


async def test_deny_overrides_allow() -> None:
    """A tool in both allow_tools and deny_tools is blocked (deny wins)."""
    policy = PolicyExtension(
        allow_tools=frozenset({"bash"}),
        deny_tools=frozenset({"bash"}),
    )
    result = policy._on_tool_call(_make_event("bash"), None)  # type: ignore[arg-type]
    assert isinstance(result, ToolCallResult)
    assert result.block is True


async def test_block_returns_tool_call_result_block_true() -> None:
    """A blocked result has block=True and a non-empty reason."""
    policy = PolicyExtension(deny_tools=frozenset({"danger"}))
    result = policy._on_tool_call(_make_event("danger"), None)  # type: ignore[arg-type]
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason is not None
    assert len(result.reason) > 0


async def test_block_reason_propagated_to_tool_result_message() -> None:
    """The deny reason must appear in the synthesized tool-result message (is_error)."""

    async def noop_execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        # Should never be called — policy blocks it
        return ToolResult(content=[TextContent(text="should_not_reach")])

    noop = AgentTool(name="noop", execute=noop_execute)
    policy = PolicyExtension(deny_tools=frozenset({"noop"}))

    ext = _build_ext_with_policy(policy)

    stream = _make_mock_stream(
        [
            AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id="t1",
                        tool_name="noop",
                        input={},
                    )
                ],
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="done")],
                stop_reason="end_turn",
            ),
        ]
    )

    h = AgentHarness(
        AgentHarnessOptions(
            extensions=[ext],
            tools=[noop],
            stream_fn=stream,
        )
    )

    new_messages = await h.prompt("run noop")

    tool_results = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is True
    # Reason contains "noop" (policy message format)
    text = tool_results[0].content[0].text
    assert "noop" in text or "blocked" in text.lower() or "denied" in text.lower()


# ============================================================
# Integration test — full harness + PolicyExtension
# ============================================================


async def test_integration_with_harness_blocks_actual_tool_execution() -> None:
    """Full chain: PolicyExtension(deny_tools={"echo"}) prevents echo from running."""

    executed = {"count": 0}

    async def echo_execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        executed["count"] += 1
        return ToolResult(content=[TextContent(text="echoed")])

    echo = AgentTool(name="echo", execute=echo_execute)
    policy = PolicyExtension(deny_tools=frozenset({"echo"}))
    ext = _build_ext_with_policy(policy)

    stream = _make_mock_stream(
        [
            AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id="t1",
                        tool_name="echo",
                        input={"text": "ping"},
                    )
                ],
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            ),
        ]
    )

    h = AgentHarness(
        AgentHarnessOptions(
            extensions=[ext],
            tools=[echo],
            stream_fn=stream,
        )
    )

    await h.prompt("echo ping")

    # Tool must NOT have been executed
    assert executed["count"] == 0
