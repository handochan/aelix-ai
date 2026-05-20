"""Pi parity: ``rpc-mode.ts:622-653`` — ``get_commands`` RPC handler.

Sprint 6h₁ §G integration coverage. Validates aggregation across the 3
sources (extension / prompt / skill), the ``"skill:"`` name prefix,
and the wire envelope shape.
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
from aelix_coding_agent.rpc.rpc_mode import _handle_get_commands
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandGetCommands,
    RpcSuccessResponse,
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


async def test_empty_sources_yields_empty_commands() -> None:
    """Pi parity: 3 empty sources → ``data.commands == []``."""

    harness = _make_harness()
    try:
        response = await _handle_get_commands(
            harness, RpcCommandGetCommands(id="r1")
        )
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_commands"
        assert response.id == "r1"
        assert response.data == {"commands": []}
    finally:
        await harness.dispose()


async def test_extension_commands_present() -> None:
    """Pi parity: extension commands → ``{name, description, source: 'extension', sourceInfo}``.

    Sprint 6h₁ W6 (P-225 BLOCKING): ``sourceInfo`` matches Pi
    ``source-info.ts:1-12`` ``{path, source, scope, origin}`` shape
    (plus optional ``baseDir``).
    """

    from aelix_coding_agent.extensions.api import ExtensionSourceInfo

    ext = Extension(
        name="my-ext",
        source_info=ExtensionSourceInfo(
            source="project",
            base_dir="/proj/ext",
            identifier="my-ext",
        ),
    )
    ext.commands["greet"] = RegisteredCommand(
        name="greet",
        handler=lambda **_: None,
        description="Say hi",
        source="my-ext",
    )
    harness = _make_harness(extensions=[ext])
    try:
        response = await _handle_get_commands(
            harness, RpcCommandGetCommands(id="r2")
        )
        assert isinstance(response, RpcSuccessResponse)
        assert response.data is not None
        commands = response.data["commands"]
        assert len(commands) == 1
        assert commands[0]["name"] == "greet"
        assert commands[0]["source"] == "extension"
        assert commands[0]["description"] == "Say hi"
        # Pi-shape sourceInfo wire fields.
        source_info = commands[0]["sourceInfo"]
        assert source_info["path"] == "/proj/ext"
        assert source_info["source"] == "my-ext"
        assert source_info["scope"] == "user"
        assert source_info["origin"] == "top-level"
        assert source_info["baseDir"] == "/proj/ext"
    finally:
        await harness.dispose()


async def test_prompt_templates_in_response() -> None:
    """Pi parity: prompt templates → ``{name, description, source: 'prompt'}``."""

    harness = _make_harness()
    try:
        harness.set_prompt_templates(
            [PromptTemplate(name="commit", description="Commit", content="body")]
        )
        response = await _handle_get_commands(
            harness, RpcCommandGetCommands(id="r3")
        )
        assert isinstance(response, RpcSuccessResponse)
        assert response.data is not None
        commands = response.data["commands"]
        assert len(commands) == 1
        assert commands[0]["name"] == "commit"
        assert commands[0]["source"] == "prompt"
        assert commands[0]["description"] == "Commit"
    finally:
        await harness.dispose()


async def test_skill_names_are_prefixed() -> None:
    """Pi parity (rpc-mode.ts:645): ``name: 'skill:' + skill.name``."""

    harness = _make_harness()
    try:
        harness.set_skills(
            [
                Skill(
                    name="my-skill",
                    description="A skill",
                    content="body",
                    file_path="/p/my-skill/SKILL.md",
                )
            ]
        )
        response = await _handle_get_commands(
            harness, RpcCommandGetCommands(id="r4")
        )
        assert isinstance(response, RpcSuccessResponse)
        assert response.data is not None
        commands = response.data["commands"]
        assert len(commands) == 1
        assert commands[0]["name"] == "skill:my-skill"
        assert commands[0]["source"] == "skill"
        assert commands[0]["description"] == "A skill"
        # Pi-shape sourceInfo wire (W6 P-225): {path, source, scope, origin}.
        source_info = commands[0]["sourceInfo"]
        assert source_info["path"] == "/p/my-skill/SKILL.md"
        assert source_info["source"] == "/p/my-skill/SKILL.md"
        assert source_info["scope"] == "user"
        assert source_info["origin"] == "top-level"
    finally:
        await harness.dispose()


async def test_three_sources_aggregated_in_order() -> None:
    """Pi parity: extension → prompt → skill insertion order."""

    ext = Extension(name="ext")
    ext.commands["ec"] = RegisteredCommand(
        name="ec", handler=lambda **_: None, description="extension command", source="ext"
    )
    harness = _make_harness(extensions=[ext])
    try:
        harness.set_prompt_templates(
            [PromptTemplate(name="pt", description="prompt template", content="b")]
        )
        harness.set_skills(
            [
                Skill(
                    name="sk",
                    description="skill",
                    content="b",
                    file_path="/p/sk/SKILL.md",
                )
            ]
        )
        response = await _handle_get_commands(
            harness, RpcCommandGetCommands(id="r5")
        )
        assert isinstance(response, RpcSuccessResponse)
        assert response.data is not None
        commands = response.data["commands"]
        assert [c["name"] for c in commands] == ["ec", "pt", "skill:sk"]
        assert [c["source"] for c in commands] == ["extension", "prompt", "skill"]
    finally:
        await harness.dispose()


async def test_get_commands_via_dispatch_table() -> None:
    """Closure: ``build_dispatch_table()['get_commands']`` resolves to the
    real handler — not a deferred stub."""

    from aelix_coding_agent.rpc.rpc_mode import build_dispatch_table
    from aelix_coding_agent.rpc.rpc_types import RpcErrorResponse

    harness = _make_harness()
    try:
        table = build_dispatch_table()
        response = await table["get_commands"](
            harness, RpcCommandGetCommands(id="r6")
        )
        # Sprint 6h₁: get_commands is no longer a deferred stub.
        assert not isinstance(response, RpcErrorResponse)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_commands"
    finally:
        await harness.dispose()
