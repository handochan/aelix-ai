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


# === P0 #7 Wave 2 (item 3) — register_tool refresh ===
#
# Pi parity (loader.ts:217-225 + agent-session.ts:2238-2326,
# _refreshToolRegistry no-options path). ``register_tool`` calls
# ``runtime.refreshTools()`` with NO options, so newly-registered tools
# auto-activate on top of the previous active set — even when an explicit
# active filter already exists.


def _api_for(harness: AgentHarness, ext: Extension) -> Any:
    """Build an ExtensionAPI bound to the harness's already-bound runtime.

    Mirrors what a hook handler sees: the same ``_ExtensionRuntime`` the
    harness installed its real action table onto via ``bind_core``, so
    ``register_tool`` routes through the live ``_refresh_extension_tools``.
    """

    from aelix_coding_agent.extensions.api import ExtensionAPI

    return ExtensionAPI(ext, harness.runtime)


def test_register_tool_refresh_adds_to_state_and_active_none_case() -> None:
    """(a) register_tool from a handler → tool in state.tools AND active set.

    ``active_tool_names is None`` (every registered tool active) before the
    refresh; afterwards the new tool is present and active.
    """

    from aelix_coding_agent.extensions.api import _ExtensionRuntime

    ext = Extension(name="ext")
    ext.tools["seed"] = _tool("seed")
    rt = _ExtensionRuntime()
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            tools=[],
            extensions=[ext],
            runtime=rt,
        )
    )

    # Default active filter: None ⇒ all registered tools active.
    assert harness.state.active_tool_names is None
    assert [t.name for t in harness.state.tools] == ["seed"]

    api = _api_for(harness, ext)
    api.register_tool(_tool("added"))

    names = [t.name for t in harness.state.tools]
    assert "added" in names  # rebuilt registry contains the new tool.
    # pi materializes the active set; after a refresh it is a concrete list.
    assert harness.state.active_tool_names is not None
    assert "added" in harness.state.active_tool_names
    assert "seed" in harness.state.active_tool_names


def test_register_tool_refresh_auto_activates_over_explicit_filter() -> None:
    """(b) Explicit active filter present → new tool added ON TOP of it.

    This is the pi-correct behavior (``else if (!options?.activeToolNames)``
    branch fires because register_tool passes NO options). The recon's
    "NOT auto-activated with an explicit filter" claim was WRONG.
    """

    from aelix_coding_agent.extensions.api import _ExtensionRuntime

    ext = Extension(name="ext")
    ext.tools["a"] = _tool("a")
    ext.tools["b"] = _tool("b")
    rt = _ExtensionRuntime()
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            tools=[],
            extensions=[ext],
            # Explicit filter: only "a" active (drops "b").
            active_tool_names=["a"],
            runtime=rt,
        )
    )

    assert harness.state.active_tool_names == ["a"]

    api = _api_for(harness, ext)
    api.register_tool(_tool("c"))

    # Previous active ("a") ∪ newly-registered ("c"); "b" stays inactive
    # because it was neither active before nor newly registered.
    assert harness.state.active_tool_names is not None
    assert set(harness.state.active_tool_names) == {"a", "c"}
    assert "b" not in harness.state.active_tool_names
    assert "c" in [t.name for t in harness.state.tools]


def test_register_tool_pre_bind_refresh_is_noop() -> None:
    """(c) Pre-bind/default refresh_tools is a NO-OP → register_tool valid.

    During extension setup the runtime action table is the default (refresh
    is ``lambda: None``), so register_tool stores the tool without raising.
    """

    from aelix_coding_agent.extensions.api import (
        ExtensionAPI,
        _ExtensionRuntime,
    )

    rt = _ExtensionRuntime()  # not bound to any harness yet.
    ext = Extension(name="ext")
    api = ExtensionAPI(ext, rt)

    # Must not raise even though no harness has bound the real refresh action.
    api.register_tool(_tool("pre_bind"))

    assert "pre_bind" in ext.tools


def test_refresh_drops_stale_active_name_for_removed_tool() -> None:
    """(d) A previously-active tool removed from the registry doesn't linger.

    pi ``filter(isAllowedTool)`` / our "filter to names still present" keeps
    the materialized active set free of stale names. Removing the extension
    tool then refreshing must drop it from active_tool_names.
    """

    from aelix_coding_agent.extensions.api import _ExtensionRuntime

    ext = Extension(name="ext")
    ext.tools["keep"] = _tool("keep")
    ext.tools["drop"] = _tool("drop")
    rt = _ExtensionRuntime()
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            tools=[],
            extensions=[ext],
            runtime=rt,
        )
    )

    # Both active under the None (all-active) default.
    assert set(harness._action_get_active_tools()) == {"keep", "drop"}

    api = _api_for(harness, ext)
    # Force-materialize the active set to include "drop" via a refresh, then
    # remove "drop" from the extension and refresh again.
    api.register_tool(_tool("extra"))
    assert "drop" in (harness.state.active_tool_names or [])

    del ext.tools["drop"]
    harness._refresh_extension_tools()

    assert harness.state.active_tool_names is not None
    assert "drop" not in harness.state.active_tool_names
    assert "drop" not in [t.name for t in harness.state.tools]
    assert "keep" in harness.state.active_tool_names
