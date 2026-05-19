"""AgentHarness ``current_model`` property + setter — Sprint 6f W2 (P-168).

Pi parity: ``coding-agent/src/core/agent-harness`` — Pi RPC ``set_model``
mutates ``current_model``, ``get_state`` reads it. Sprint 6f W2 adds
the public surface so the RPC handlers can wire through.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


def _quiet_stream_fn() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _make_harness(initial: Model | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=initial or Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
        )
    )


async def test_current_model_defaults_to_initial_constructor_model() -> None:
    """Before any ``set_current_model`` call, ``current_model`` matches options.model."""

    initial = Model(id="initial", provider="anthropic", api="anthropic-messages")
    harness = _make_harness(initial)
    try:
        assert harness.current_model is not None
        assert harness.current_model.id == "initial"
        assert harness.current_model.provider == "anthropic"
    finally:
        await harness.dispose()


async def test_set_current_model_overrides_initial() -> None:
    harness = _make_harness()
    try:
        override = Model(
            id="claude-sonnet-4-5", provider="anthropic", api="anthropic-messages"
        )
        harness.set_current_model(override)
        assert harness.current_model is not None
        assert harness.current_model.id == "claude-sonnet-4-5"
    finally:
        await harness.dispose()


async def test_current_model_persists_across_multiple_reads() -> None:
    harness = _make_harness()
    try:
        m = Model(id="x", provider="p")
        harness.set_current_model(m)
        first = harness.current_model
        second = harness.current_model
        third = harness.current_model
        assert first is m
        assert second is m
        assert third is m
    finally:
        await harness.dispose()


async def test_set_current_model_does_not_emit_model_select_hook() -> None:
    """Pi parity: the RPC ``set_model`` path does NOT fire the
    :class:`ModelSelectHookEvent` (only :meth:`AgentHarness.set_model`
    does). The runtime-host owns its own observation path.
    """

    from aelix_agent_core.harness.hooks import ModelSelectHookEvent
    from aelix_coding_agent.extensions.api import Extension

    fired: list[Any] = []

    def handler(event: ModelSelectHookEvent) -> None:
        fired.append(event)

    ext = Extension(
        name="probe",
        handlers={"model_select": [handler]},
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="initial", provider="p"),
            stream_fn=_quiet_stream_fn(),
            extensions=[ext],
        )
    )
    try:
        harness.set_current_model(Model(id="other", provider="p"))
        assert fired == [], "set_current_model must not fire model_select"
        assert harness.current_model is not None
        assert harness.current_model.id == "other"
    finally:
        await harness.dispose()


async def test_current_model_consistent_with_set_current_model_None_baseline() -> None:
    """Edge case: harness constructed with ``Model()`` default — current_model
    still returns the default sentinel instance (not None).
    """

    harness = AgentHarness(
        AgentHarnessOptions(stream_fn=_quiet_stream_fn())
    )
    try:
        assert harness.current_model is not None  # default sentinel Model()
        assert harness.current_model.id == "unknown"  # Model() default
    finally:
        await harness.dispose()
