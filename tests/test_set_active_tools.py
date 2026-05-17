"""F-9 acceptance tests — ``set_active_tools`` is non-destructive.

Spec reference: Sprint 2 / Phase 1.3 spec §E F-9.

Pre-fix bug: ``_action_set_active_tools`` mutated ``self._state.tools`` by
filtering it down, permanently dropping every tool not in the new set. After
the fix, ``set_active_tools`` writes to ``AgentState.active_tool_names`` and
never touches the registered tool list.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.tools import ToolExecutionContext, ToolResult


async def _noop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(content=[TextContent(text="ok")])


def _tools(*names: str) -> list[AgentTool]:
    return [AgentTool(name=n, execute=_noop_execute) for n in names]


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")],
                stop_reason="end_turn",
            )
        )

    return fn


def test_set_active_tools_filters_without_dropping_tools() -> None:
    """After narrowing to [a], expanding to [a, b] must still see b in tools."""

    h = AgentHarness(AgentHarnessOptions(tools=_tools("a", "b", "c"), stream_fn=_stream()))

    h._action_set_active_tools(["a"])
    assert h._action_get_active_tools() == ["a"]
    # Registered tools must NOT have been pruned — the filter is non-destructive.
    assert {t.name for t in h.state.tools} == {"a", "b", "c"}

    # Expanding the active set must re-include b (impossible if b were dropped).
    h._action_set_active_tools(["a", "b"])
    assert set(h._action_get_active_tools()) == {"a", "b"}
    assert {t.name for t in h.state.tools} == {"a", "b", "c"}


def test_set_active_tools_none_means_all_active() -> None:
    """Default ``active_tool_names is None`` exposes every registered tool."""

    h = AgentHarness(AgentHarnessOptions(tools=_tools("a", "b", "c"), stream_fn=_stream()))

    assert h.state.active_tool_names is None
    assert set(h._action_get_active_tools()) == {"a", "b", "c"}


def test_set_active_tools_unknown_name_raises() -> None:
    """Unknown names produce AgentHarnessError('invalid_argument', ...)."""

    h = AgentHarness(AgentHarnessOptions(tools=_tools("a"), stream_fn=_stream()))

    with pytest.raises(AgentHarnessError) as exc_info:
        h._action_set_active_tools(["does_not_exist"])
    assert exc_info.value.code == "invalid_argument"
    # The state must not have been partially mutated by a rejected call.
    assert h.state.active_tool_names is None


async def test_set_active_tools_filters_tools_reaching_agent_loop() -> None:
    """Verify AgentHarness._run filters AgentContext.tools by active_tool_names.

    Pi parity: only the named subset is visible to the loop / tool dispatcher.
    Captures the tools list as seen by stream_fn (provider request time).
    """
    captured_tool_names: list[list[str]] = []

    def capturing_stream() -> Any:
        async def fn(
            model: Model,
            context: Any,
            options: SimpleStreamOptions,
        ) -> AsyncIterator[AssistantMessageEvent]:
            # Record which tool names reached the provider call.
            captured_tool_names.append([t.name for t in context.tools])
            yield AssistantStartEvent(partial=AssistantMessage(content=[]))
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[TextContent(text="done")],
                    stop_reason="end_turn",
                )
            )

        return fn

    h = AgentHarness(
        AgentHarnessOptions(tools=_tools("a", "b", "c"), stream_fn=capturing_stream())
    )
    h._action_set_active_tools(["b"])

    await h.prompt("hi")

    assert captured_tool_names == [["b"]], (
        f"only 'b' should reach the loop; got {captured_tool_names}"
    )
