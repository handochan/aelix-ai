"""Pi-parity drift detector for setter emit sites (Sprint 3b, spec §F.3).

Pins the Pi-verified emit sites at SHA ``734e08e`` (ADR-0034) and asserts
that each Aelix setter emits exactly the events Pi emits — no more, no less.
P-4 spec verdict: setters do NOT emit ``queue_update`` (only enqueue paths
do); 3 setters emit (model/thinking/resources), 5 do not.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
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
from aelix_ai.tools import ToolExecutionContext, ToolResult

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "pi_setter_emit_sites_734e08e.json"
)

# Pi → Aelix setter method name map (Pi camelCase → Aelix snake_case).
_PI_TO_AELIX = {
    "setModel": "set_model",
    "setThinkingLevel": "set_thinking_level",
    "setActiveTools": "set_active_tools",
    "setSteeringMode": "set_steering_mode",
    "setFollowUpMode": "set_follow_up_mode",
    "setResources": "set_resources",
    "setStreamOptions": "set_stream_options",
    "setTools": "set_tools",
}

# Every Pi-verified own-event name we want to listen for in the drift test.
_ALL_OWN_EVENTS = (
    "queue_update",
    "save_point",
    "abort",
    "settled",
    "before_provider_request",
    "before_provider_payload",
    "after_provider_response",
    "session_compact",
    "session_before_tree",
    "session_tree",
    "model_select",
    "thinking_level_select",
    "resources_update",
)


async def _noop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(content=[TextContent(text="ok")])


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


def _build_harness() -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            tools=[
                AgentTool(name="a", execute=_noop_execute),
                AgentTool(name="b", execute=_noop_execute),
            ],
            stream_fn=_stream(),
        )
    )


async def _call_setter(h: AgentHarness, aelix_name: str) -> None:
    if aelix_name == "set_model":
        await h.set_model(Model(api="anthropic", id="claude-x"))
    elif aelix_name == "set_thinking_level":
        await h.set_thinking_level("high")
    elif aelix_name == "set_active_tools":
        await h.set_active_tools(["a"])
    elif aelix_name == "set_steering_mode":
        # Sprint 6h₂ (P-248): sync setter.
        h.set_steering_mode("all")
    elif aelix_name == "set_follow_up_mode":
        # Sprint 6h₂ (P-248): sync setter.
        h.set_follow_up_mode("all")
    elif aelix_name == "set_resources":
        await h.set_resources({"k": 1})
    elif aelix_name == "set_stream_options":
        await h.set_stream_options({"timeout": 5})
    elif aelix_name == "set_tools":
        await h.set_tools([AgentTool(name="x", execute=_noop_execute)])
    else:
        raise AssertionError(f"Unknown Aelix setter: {aelix_name!r}")


async def test_setter_emit_sites_match_pi_734e08e() -> None:
    """For every Pi setter, Aelix emits exactly the same own-event set."""

    fixture = json.loads(_FIXTURE.read_text())
    expected: dict[str, list[str]] = {
        pi_name: spec["emits"] for pi_name, spec in fixture["setters"].items()
    }

    for pi_name, expected_events in expected.items():
        aelix_name = _PI_TO_AELIX[pi_name]
        h = _build_harness()
        captured: dict[str, int] = {ev: 0 for ev in _ALL_OWN_EVENTS}
        for ev in _ALL_OWN_EVENTS:
            h.hooks.on(ev, lambda e, _c, _ev=ev, _cap=captured: _cap.__setitem__(_ev, _cap[_ev] + 1))  # type: ignore[arg-type, call-overload]

        await _call_setter(h, aelix_name)

        emitted = sorted([ev for ev, count in captured.items() if count > 0])
        assert emitted == sorted(expected_events), (
            f"Pi {pi_name}: expected emits={expected_events!r}, "
            f"Aelix emitted={emitted!r}"
        )
