"""Pi parity: AgentHarness extension_runner / prompt_templates / skills surface.

Sprint 6h₁ §G integration coverage for the harness aggregation hooks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.prompt_templates import PromptTemplate
from aelix_agent_core.harness.skills import Skill
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.extensions.api import Extension, RegisteredCommand


def _quiet_stream_fn() -> Any:
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


def _make_harness(extensions: list[Extension] | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            extensions=extensions or [],
        )
    )


async def test_extension_runner_property_exposes_runner() -> None:
    ext = Extension(name="x")
    ext.commands["c"] = RegisteredCommand(
        name="c", handler=lambda **_: None, description="d", source="x"
    )
    harness = _make_harness(extensions=[ext])
    try:
        # Sprint 6h₁ W6 (P-224): runner returns ResolvedCommand.
        cmds = harness.extension_runner.get_registered_commands()
        assert [r.invocation_name for r in cmds] == ["c"]
        assert [r.command.name for r in cmds] == ["c"]
    finally:
        await harness.dispose()


async def test_prompt_templates_default_empty_and_roundtrip() -> None:
    harness = _make_harness()
    try:
        assert harness.prompt_templates == []
        templates = [
            PromptTemplate(name="t1", description="d1", content="c1"),
            PromptTemplate(name="t2", description="d2", content="c2"),
        ]
        harness.set_prompt_templates(templates)
        assert [t.name for t in harness.prompt_templates] == ["t1", "t2"]
    finally:
        await harness.dispose()


async def test_skills_default_empty_and_roundtrip() -> None:
    harness = _make_harness()
    try:
        assert harness.skills == []
        skills = [
            Skill(
                name="s1",
                description="d",
                content="c",
                file_path="/p/s1/SKILL.md",
            )
        ]
        harness.set_skills(skills)
        assert [s.name for s in harness.skills] == ["s1"]
    finally:
        await harness.dispose()


async def test_set_prompt_templates_makes_defensive_copy() -> None:
    """Mutating the input list after the setter must not affect the harness."""

    harness = _make_harness()
    try:
        templates = [PromptTemplate(name="t1", description="d", content="c")]
        harness.set_prompt_templates(templates)
        templates.append(PromptTemplate(name="t2", description="d", content="c"))
        assert [t.name for t in harness.prompt_templates] == ["t1"]
    finally:
        await harness.dispose()
