"""Phase 1.1 regression tests — verify the Agent/agent_loop surface is intact.

Spec reference: Sprint 1 Phase 1.2 Spec §D.1.11 (H-8).

These tests protect against silent breakage of Phase 1.1 contracts by:

1. Pinning the public parameter names of Agent.__init__ and AgentOptions.
2. Asserting Agent instances do not expose a `hooks` attribute even after
   aelix_agent_core.harness is imported (import-side-effect safety).
3. Re-running a Phase 1.1 before_tool_call blocking scenario inline to
   prove the callback path was not quietly rewired by Phase 1.2.
4. Confirming the aelix_agent_core.types import path is unchanged.
5. Confirming aelix_agent_core.__all__ is a superset of the Phase 1.1 surface.
6. Confirming the __main__._run demo executes cleanly.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from typing import Any

import aelix_agent_core as _agent_module
import aelix_agent_core.harness  # noqa: F401 — import to trigger any harness side-effects before assertions
from aelix_agent_core import (
    AgentContext,
    AgentLoopConfig,
    AgentOptions,
    AgentTool,
    BeforeToolCallResult,
    agent_loop,
    default_convert_to_llm,
)
from aelix_agent_core.agent import Agent
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
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

# ===========================================================================
# Phase 1.1 parameter name snapshots
# ===========================================================================

# The exact parameter names (positional + keyword) that Agent.__init__ must
# expose. This list must NOT change without a deliberate breaking-change PR.
_EXPECTED_AGENT_INIT_PARAMS = frozenset({"self", "options"})

# The exact parameter names of AgentOptions.__init__ (dataclass __init__).
_EXPECTED_AGENT_OPTIONS_PARAMS = frozenset(
    {
        "self",
        "initial_state",
        "convert_to_llm",
        "transform_context",
        "stream_fn",
        "get_api_key",
        "before_tool_call",
        "after_tool_call",
        "prepare_next_turn",
        "should_stop_after_turn",
        "steering_mode",
        "follow_up_mode",
    }
)

# The Phase 1.1 __all__ surface — aelix.agent must export at least these.
_PHASE_1_1_ALL = frozenset(
    {
        "AfterToolCallContext",
        "AfterToolCallResult",
        "Agent",
        "AgentContext",
        "AgentEndEvent",
        "AgentEvent",
        "AgentEventSink",
        "AgentLoopConfig",
        "AgentLoopTurnUpdate",
        "AgentMessage",
        "AgentOptions",
        "AgentStartEvent",
        "AgentState",
        "AgentTool",
        "BeforeToolCallContext",
        "BeforeToolCallResult",
        "MessageEndEvent",
        "MessageStartEvent",
        "MessageUpdateEvent",
        "QueueMode",
        "ShouldStopAfterTurnContext",
        "ToolExecutionEndEvent",
        "ToolExecutionMode",
        "ToolExecutionStartEvent",
        "ToolExecutionUpdateEvent",
        "TurnEndEvent",
        "TurnStartEvent",
        "agent_loop",
        "agent_loop_continue",
        "default_convert_to_llm",
    }
)


# ===========================================================================
# Shared mock stream helper (mirrors test_agent_loop.py exactly)
# ===========================================================================


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
                f"mock stream_fn ran out of turns at idx={i}; "
                f"loop took an extra turn (script length={len(turn_finals)})"
            )
        final = turn_finals[i]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=final)

    return fn


def _emit_collector() -> tuple[list[Any], Any]:
    events: list[Any] = []

    async def emit(event: Any) -> None:
        events.append(event)

    return events, emit


# ===========================================================================
# D.1.11: signature tests
# ===========================================================================


def test_agent_signature_unchanged() -> None:
    """Agent.__init__ parameter names match the Phase 1.1 snapshot."""

    sig = inspect.signature(Agent.__init__)
    actual_params = frozenset(sig.parameters.keys())
    assert actual_params == _EXPECTED_AGENT_INIT_PARAMS, (
        f"Agent.__init__ parameter names changed.\n"
        f"  Expected: {sorted(_EXPECTED_AGENT_INIT_PARAMS)}\n"
        f"  Actual:   {sorted(actual_params)}"
    )


def test_agent_options_signature_unchanged() -> None:
    """AgentOptions.__init__ parameter names match the Phase 1.1 snapshot."""

    sig = inspect.signature(AgentOptions.__init__)
    actual_params = frozenset(sig.parameters.keys())
    assert actual_params == _EXPECTED_AGENT_OPTIONS_PARAMS, (
        f"AgentOptions.__init__ parameter names changed.\n"
        f"  Expected: {sorted(_EXPECTED_AGENT_OPTIONS_PARAMS)}\n"
        f"  Actual:   {sorted(actual_params)}"
    )


# ===========================================================================
# D.1.11: Agent must NOT expose a hooks attribute
# ===========================================================================


def test_agent_has_no_hooks_attribute() -> None:
    """Agent() instance does not have a 'hooks' attribute even after harness import.

    aelix_agent_core.harness is already imported at the top of this module to prove
    that importing the harness package does not silently leak a hooks attribute
    onto the Phase 1.1 Agent class.
    """

    agent = Agent()
    assert not hasattr(agent, "hooks"), (
        "Agent must not expose 'hooks'; that attribute belongs to AgentHarness only. "
        "Importing aelix.harness must not mutate the Agent class."
    )


# ===========================================================================
# D.1.11: callback path intact (before_tool_call blocking)
# ===========================================================================


async def test_agent_loop_callback_path_intact() -> None:
    """Reproduce the Phase 1.1 before_tool_call block scenario via direct agent_loop.

    This is a direct re-run of the test_before_tool_call_block scenario from
    test_agent_loop.py, executed inline here to prove that Phase 1.2 did not
    quietly rewrite the callback path.
    """

    executed = {"count": 0}

    async def run_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        executed["count"] += 1
        return ToolResult(content=[TextContent(text="should never appear")])

    run_tool = AgentTool(
        name="run",
        description="runs something",
        parameters={"type": "object", "properties": {}, "required": []},
        execute=run_execute,
    )

    turn1 = AssistantMessage(
        content=[ToolCallContent(tool_call_id="t1", tool_name="run", input={})],
        stop_reason="tool_use",
    )
    turn2 = AssistantMessage(
        content=[TextContent(text="ok")],
        stop_reason="end_turn",
    )

    events, emit = _emit_collector()

    async def block_all(ctx: Any) -> BeforeToolCallResult:
        return BeforeToolCallResult(block=True, reason="phase-1.1-block")

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

    assert executed["count"] == 0, "tool.execute must not have been called when blocked"

    tool_results = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1, "expected exactly one blocked tool result"
    assert tool_results[0].is_error is True
    assert "phase-1.1-block" in tool_results[0].content[0].text


# ===========================================================================
# D.1.11: module path for types unchanged
# ===========================================================================


def test_agent_state_module_path_unchanged() -> None:
    """aelix_agent_core.types still exports AgentState, AgentLoopConfig, BeforeToolCallContext."""

    from aelix_agent_core.types import AgentLoopConfig as _ALC  # noqa: PLC0415
    from aelix_agent_core.types import AgentState as _AS  # noqa: PLC0415
    from aelix_agent_core.types import BeforeToolCallContext as _BTC  # noqa: PLC0415

    # These are the real classes — just confirm the import path is intact.
    assert _AS.__name__ == "AgentState"
    assert _ALC.__name__ == "AgentLoopConfig"
    assert _BTC.__name__ == "BeforeToolCallContext"


# ===========================================================================
# D.1.11: public surface unchanged (superset check)
# ===========================================================================


def test_agent_module_public_surface_unchanged() -> None:
    """aelix_agent_core.__all__ is a superset of (or equal to) the Phase 1.1 surface.

    Phase 1.2 may ADD names to __all__ (additive extensions are fine), but
    must not REMOVE any Phase 1.1 name.
    """

    actual_all = frozenset(getattr(_agent_module, "__all__", []))
    missing = _PHASE_1_1_ALL - actual_all
    assert not missing, (
        f"aelix_agent_core.__all__ is missing Phase 1.1 names: {sorted(missing)}"
    )


# ===========================================================================
# D.1.11: demo runs clean
# ===========================================================================


async def test_existing_demo_runs_clean() -> None:
    """aelix.__main__._run() executes without raising any exception."""

    from aelix.__main__ import _run  # noqa: PLC0415

    # _run() uses a mock stream internally; no LLM or network required.
    await _run()


# ===========================================================================
# F-12: static umbrella surface (no __getattr__ proxy)
# ===========================================================================


def test_umbrella_has_no_getattr_proxy() -> None:
    """aelix umbrella must use static re-exports — no lazy __getattr__."""

    import aelix  # noqa: PLC0415

    # The umbrella is now a plain re-export package. Phase 1.2's lazy
    # ``__getattr__`` (which deferred AgentHarness/PolicyExtension imports)
    # has been replaced by direct ``from aelix_*`` imports at module top.
    assert "__getattr__" not in aelix.__dict__, (
        "aelix.__init__.py must not define __getattr__; static re-exports only."
    )


def test_umbrella_static_attributes_resolve_to_workspace_packages() -> None:
    """Umbrella attributes point to the new aelix_* workspace packages."""

    import aelix  # noqa: PLC0415

    assert aelix.AgentHarness.__module__ == "aelix_agent_core.harness.core"
    assert aelix.AgentHarnessOptions.__module__ == "aelix_agent_core.harness.core"
    assert aelix.PolicyExtension.__module__ == "aelix_coding_agent.builtin.policy"
    assert aelix.GuardrailExtension.__module__ == "aelix_coding_agent.builtin.guardrail"
    assert aelix.Agent.__module__ == "aelix_agent_core.agent"


# ===========================================================================
# Risk-1 regression: agent-core must not require coding-agent at runtime
# ===========================================================================


def test_agent_core_does_not_require_coding_agent() -> None:
    """Importing aelix_agent_core must not pull aelix_coding_agent in."""

    import sys  # noqa: PLC0415

    sys.modules.pop("aelix_coding_agent", None)
    from aelix_agent_core.harness import HookBus, HookEvent  # noqa: PLC0415,F401

    assert "aelix_coding_agent" not in sys.modules, (
        "aelix_agent_core.harness imports must not load aelix_coding_agent."
    )
