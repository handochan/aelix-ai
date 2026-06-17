"""ADR-0135 (P0 #1) — reasoning/thinking state → provider wiring (Layer 1).

Before ADR-0135 the entire thinking stack was a no-op: ``loop.py`` built
``SimpleStreamOptions`` without ``reasoning`` and ``AgentLoopConfig`` had no
``reasoning`` field, so ``core.py:_make_stream_fn``'s ``reasoning=options.reasoning``
was always ``None``. These tests prove the per-turn ``thinking_level`` snapshot
now reaches the provider adapter via ``SimpleStreamOptions.reasoning``.

Pi parity: the coding-agent harness passes ``streamOptions.reasoning`` as the
raw thinking-level string; ``"off"`` collapses to ``None``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core import (
    AgentContext,
    AgentLoopConfig,
    agent_loop,
    default_convert_to_llm,
)
from aelix_agent_core import AgentTool
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
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


def _capturing_stream(sink: dict[str, Any]) -> Any:
    """A stream_fn that records the ``reasoning`` it received per call."""

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        sink.setdefault("reasoning_seen", []).append(options.reasoning)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")],
                stop_reason="end_turn",
            )
        )

    return fn


async def _drain(events_sink: list[Any]) -> Any:
    async def emit(event: Any) -> None:
        events_sink.append(event)

    return emit


# === loop layer: config.reasoning → SimpleStreamOptions.reasoning ===========


async def test_loop_forwards_config_reasoning_to_stream_options() -> None:
    sink: dict[str, Any] = {}
    config = AgentLoopConfig(
        model=Model(id="mock", provider="mock"),
        convert_to_llm=default_convert_to_llm,
        reasoning="high",
    )
    await agent_loop(
        [UserMessage(content=[TextContent(text="hi")])],
        AgentContext(),
        config,
        emit=await _drain([]),
        stream_fn=_capturing_stream(sink),
    )
    assert sink["reasoning_seen"] == ["high"]


async def test_loop_reasoning_none_by_default() -> None:
    sink: dict[str, Any] = {}
    config = AgentLoopConfig(
        model=Model(id="mock", provider="mock"),
        convert_to_llm=default_convert_to_llm,
    )
    await agent_loop(
        [UserMessage(content=[TextContent(text="hi")])],
        AgentContext(),
        config,
        emit=await _drain([]),
        stream_fn=_capturing_stream(sink),
    )
    assert sink["reasoning_seen"] == [None]


# === harness layer: thinking_level → provider options.reasoning =============


async def test_harness_snapshots_thinking_level_into_reasoning() -> None:
    sink: dict[str, Any] = {}
    h = AgentHarness(AgentHarnessOptions(stream_fn=_capturing_stream(sink)))
    await h.set_thinking_level("high")
    await h.prompt("hi")
    assert sink["reasoning_seen"] == ["high"]


async def test_harness_off_maps_to_none() -> None:
    sink: dict[str, Any] = {}
    h = AgentHarness(AgentHarnessOptions(stream_fn=_capturing_stream(sink)))
    await h.set_thinking_level("off")
    await h.prompt("hi")
    assert sink["reasoning_seen"] == [None]


async def test_harness_default_thinking_level_is_none() -> None:
    sink: dict[str, Any] = {}
    h = AgentHarness(AgentHarnessOptions(stream_fn=_capturing_stream(sink)))
    await h.prompt("hi")
    # No thinking level set (default) → reasoning omitted (None).
    assert sink["reasoning_seen"] == [None]


async def test_harness_off_then_level_same_session() -> None:
    """The off→None / level→string boundary toggles correctly across turns
    within one harness instance."""

    sink: dict[str, Any] = {}
    h = AgentHarness(AgentHarnessOptions(stream_fn=_capturing_stream(sink)))
    await h.set_thinking_level("off")
    await h.prompt("a")
    await h.set_thinking_level("high")
    await h.prompt("b")
    await h.set_thinking_level("off")
    await h.prompt("c")
    assert sink["reasoning_seen"] == [None, "high", None]


async def test_set_thinking_level_mid_turn_does_not_affect_current_turn() -> None:
    """A set_thinking_level during an in-flight turn applies only on the NEXT
    prompt() — config.reasoning is snapshotted once per _run()."""

    sink: dict[str, Any] = {"reasoning_seen": []}
    holder: dict[str, Any] = {}
    call = {"i": 0}

    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        sink["reasoning_seen"].append(options.reasoning)
        i = call["i"]
        call["i"] += 1
        if i == 0:
            # Mutate mid-turn — must NOT affect this turn's later rounds.
            await holder["h"].set_thinking_level("high")
            yield AssistantStartEvent(partial=AssistantMessage(content=[]))
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[
                        ToolCallContent(
                            tool_call_id="t1", tool_name="noop", input={}
                        )
                    ],
                    stop_reason="tool_use",
                )
            )
        else:
            yield AssistantStartEvent(partial=AssistantMessage(content=[]))
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[TextContent(text="done")], stop_reason="end_turn"
                )
            )

    async def noop_execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        return ToolResult(content=[TextContent(text="ok")])

    h = AgentHarness(
        AgentHarnessOptions(
            stream_fn=fn, tools=[AgentTool(name="noop", execute=noop_execute)]
        )
    )
    holder["h"] = h
    await h.set_thinking_level("low")
    await h.prompt("hi")
    # Two stream rounds in ONE turn; both see "low" despite the mid-turn change.
    assert sink["reasoning_seen"] == ["low", "low"]


async def test_harness_reasoning_reread_each_turn() -> None:
    """Proves per-turn re-read (no prepare_next_turn dependency): a level
    change between prompts takes effect on the next prompt()."""

    sink: dict[str, Any] = {}
    h = AgentHarness(AgentHarnessOptions(stream_fn=_capturing_stream(sink)))
    await h.set_thinking_level("low")
    await h.prompt("a")
    await h.set_thinking_level("high")
    await h.prompt("b")
    assert sink["reasoning_seen"] == ["low", "high"]
