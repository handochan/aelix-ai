"""Sprint 6h₇c §D (Phase 5a-iii-γ, ADR-0093) — ``_rebuild_tool_registry`` tests.

Pi parity (partial): tool merge step of ``agent-session.ts:_buildRuntime``
(P-450). Sprint 6h₇c extracts only the tool merge from ``__init__``;
the full ``_buildRuntime`` extraction (extension runner re-create +
active tool filter refresh + flagValues restore) stays inline as a
Phase 5b carry-forward.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
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
from aelix_coding_agent.extensions.api import Extension


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _tool(name: str) -> AgentTool:
    async def execute(args: dict[str, Any], ctx: Any) -> Any:
        return None

    return AgentTool(
        name=name,
        description=f"tool {name}",
        parameters={},
        execute=execute,
    )


def _new_harness(
    *,
    tools: list[AgentTool] | None = None,
    extensions: list[Extension] | None = None,
) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            tools=tools or [],
            extensions=extensions or [],
        )
    )


def test_rebuild_returns_empty_when_no_tools_or_extensions() -> None:
    harness = _new_harness()

    result = harness._rebuild_tool_registry()

    assert result == []


def test_rebuild_includes_options_only_tools() -> None:
    t1 = _tool("t1")
    t2 = _tool("t2")
    harness = _new_harness(tools=[t1, t2])

    result = harness._rebuild_tool_registry()

    assert [t.name for t in result] == ["t1", "t2"]


def test_rebuild_includes_extension_only_tools() -> None:
    ext = Extension(name="ext")
    t1 = _tool("ext_t1")
    t2 = _tool("ext_t2")
    ext.tools[t1.name] = t1
    ext.tools[t2.name] = t2
    harness = _new_harness(extensions=[ext])

    result = harness._rebuild_tool_registry()

    assert sorted([t.name for t in result]) == ["ext_t1", "ext_t2"]


def test_rebuild_options_override_extension_on_name_collision() -> None:
    """Application-supplied tools win on name collision (D.1.13 M-9)."""

    ext = Extension(name="ext")
    ext_tool = _tool("shared")
    ext.tools[ext_tool.name] = ext_tool

    options_tool = _tool("shared")
    harness = _new_harness(tools=[options_tool], extensions=[ext])

    result = harness._rebuild_tool_registry()

    assert len(result) == 1
    assert result[0] is options_tool  # options override won.


def test_rebuild_first_extension_wins_on_inter_extension_collision() -> None:
    """``setdefault`` collects the FIRST extension's tool for a given name."""

    ext1 = Extension(name="ext1")
    ext1_tool = _tool("shared")
    ext1.tools[ext1_tool.name] = ext1_tool

    ext2 = Extension(name="ext2")
    ext2_tool = _tool("shared")
    ext2.tools[ext2_tool.name] = ext2_tool

    harness = _new_harness(extensions=[ext1, ext2])

    result = harness._rebuild_tool_registry()

    assert len(result) == 1
    assert result[0] is ext1_tool  # first extension wins.


def test_rebuild_matches_init_state_tools() -> None:
    """The extracted method produces the same list as the inline init pass."""

    ext = Extension(name="ext")
    ext_t = _tool("ext_only")
    ext.tools[ext_t.name] = ext_t

    opt_t = _tool("opt_only")

    harness = _new_harness(tools=[opt_t], extensions=[ext])

    init_tools = list(harness.state.tools)
    re_rebuild = harness._rebuild_tool_registry()

    assert [t.name for t in init_tools] == [t.name for t in re_rebuild]


def test_rebuild_is_idempotent_when_called_twice() -> None:
    """Calling ``_rebuild_tool_registry`` repeatedly is a pure function."""

    ext = Extension(name="ext")
    ext.tools["e"] = _tool("e")
    harness = _new_harness(tools=[_tool("o")], extensions=[ext])

    first = harness._rebuild_tool_registry()
    second = harness._rebuild_tool_registry()

    assert [t.name for t in first] == [t.name for t in second]


def test_rebuild_preserves_extension_then_options_ordering() -> None:
    """Iteration order: extensions first (insertion order), then options."""

    ext = Extension(name="ext")
    ext.tools["a"] = _tool("a")
    ext.tools["b"] = _tool("b")

    opt_c = _tool("c")
    opt_d = _tool("d")
    harness = _new_harness(tools=[opt_c, opt_d], extensions=[ext])

    result = harness._rebuild_tool_registry()

    assert [t.name for t in result] == ["a", "b", "c", "d"]
