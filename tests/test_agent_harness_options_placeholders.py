"""Tests for F-6 placeholder fields on :class:`AgentHarnessOptions`.

Sprint 3b wires 4 of the 7 placeholders into ``AgentState`` (resources,
thinking_level, active_tool_names, stream_options). Three remain inert:
``session`` (Phase 2.2 — ADR-0022), ``env`` (Phase 4 — ExecutionEnv ADR
TBD), ``get_api_key_and_headers`` (Phase 4 — ADR-0038 provider).
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.streaming import Model
from aelix_ai.tools import ToolExecutionContext, ToolResult


async def _noop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(content=[TextContent(text="ok")])


def test_harness_options_default_placeholders_none() -> None:
    """All 7 new placeholder fields default to ``None``."""

    options = AgentHarnessOptions(model=Model())
    assert options.session is None
    assert options.env is None
    assert options.resources is None
    assert options.thinking_level is None
    assert options.active_tool_names is None
    assert options.get_api_key_and_headers is None
    assert options.stream_options is None


def test_harness_options_accepts_each_placeholder() -> None:
    """Constructing with each placeholder set explicitly does not raise."""

    sentinel_session = object()

    def fake_get_api_key_and_headers(_model: Model) -> None:
        return None

    options = AgentHarnessOptions(
        model=Model(),
        session=sentinel_session,
        env={"X": "1"},
        # Sprint 3b: resources is now ``dict[str, Any] | None`` (was list).
        resources={"resA": "v"},
        thinking_level="medium",
        active_tool_names=["x"],
        get_api_key_and_headers=fake_get_api_key_and_headers,
        stream_options={"timeout": 30},
    )

    assert options.session is sentinel_session
    assert options.env == {"X": "1"}
    assert options.resources == {"resA": "v"}
    assert options.thinking_level == "medium"
    assert options.active_tool_names == ["x"]
    assert options.get_api_key_and_headers is fake_get_api_key_and_headers
    assert options.stream_options == {"timeout": 30}


# === Sprint 3b — wired field flow assertions (§D.1) ===================


def test_resources_flows_into_agent_state() -> None:
    h = AgentHarness(AgentHarnessOptions(resources={"k": 1}))
    assert h.state.resources == {"k": 1}


def test_thinking_level_flows_into_agent_state() -> None:
    h = AgentHarness(AgentHarnessOptions(thinking_level="high"))
    assert h.state.thinking_level == "high"


def test_stream_options_flows_into_agent_state() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_options={"timeout": 5}))
    assert h.state.stream_options == {"timeout": 5}


def test_active_tool_names_flows_via_f9_validator() -> None:
    tool = AgentTool(name="a", execute=_noop_execute)
    h = AgentHarness(
        AgentHarnessOptions(tools=[tool], active_tool_names=["a"])
    )
    assert h.state.active_tool_names == ["a"]
