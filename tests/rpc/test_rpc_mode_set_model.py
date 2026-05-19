"""RPC ``set_model`` handler — Sprint 6f W2 (ADR-0065 §G.1, P-168).

Pi parity: ``rpc-mode.ts::handle_set_model``. Looks up the model via
:meth:`ModelRegistry.find`, mutates
:meth:`AgentHarness.set_current_model`, and returns the Pi-shape
``Model<Api>`` dict on success.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.model_registry import ModelRegistry
from aelix_coding_agent.rpc.rpc_mode import _handle_set_model
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandSetModel,
    RpcErrorResponse,
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
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _make_harness() -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
        )
    )


async def _make_registry(tmp_path: Path) -> ModelRegistry:
    """ModelRegistry where the seed providers have stored API keys, so
    ``get_available`` returns the full catalog (Sprint 6f W6 P-172:
    ``set_model`` searches ``get_available()`` not ``find()``).
    """

    s = AuthStorage(path=tmp_path / "auth.json")
    await s.load()
    await s.set_api_key("anthropic", "sk-a")
    await s.set_api_key("openai", "sk-o")
    await s.set_api_key("openrouter", "sk-r")
    return ModelRegistry.in_memory(s)


async def test_set_model_returns_camel_case_model_dict(tmp_path: Path) -> None:
    harness = _make_harness()
    registry = await _make_registry(tmp_path)
    try:
        cmd = RpcCommandSetModel(
            provider="anthropic", model_id="claude-sonnet-4-5", id="r1"
        )
        response = await _handle_set_model(harness, registry, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "set_model"
        assert response.id == "r1"
        assert isinstance(response.data, dict)
        # Pi-shape camelCase keys.
        assert response.data["id"] == "claude-sonnet-4-5"
        assert response.data["provider"] == "anthropic"
        assert "thinkingLevelMap" in response.data
        assert "maxTokens" in response.data
        assert "contextWindow" in response.data
        assert "baseUrl" in response.data
        # The harness's current_model now reflects the new model.
        assert harness.current_model is not None
        assert harness.current_model.id == "claude-sonnet-4-5"
    finally:
        await harness.dispose()


async def test_set_model_unknown_model_returns_error(tmp_path: Path) -> None:
    harness = _make_harness()
    registry = await _make_registry(tmp_path)
    try:
        cmd = RpcCommandSetModel(
            provider="anthropic", model_id="non-existent", id="r2"
        )
        response = await _handle_set_model(harness, registry, cmd)
        assert isinstance(response, RpcErrorResponse)
        assert response.command == "set_model"
        assert response.id == "r2"
        assert "Model not found" in response.error
    finally:
        await harness.dispose()


async def test_set_model_unknown_provider_returns_error(tmp_path: Path) -> None:
    harness = _make_harness()
    registry = await _make_registry(tmp_path)
    try:
        cmd = RpcCommandSetModel(
            provider="bogus-provider", model_id="x", id="r3"
        )
        response = await _handle_set_model(harness, registry, cmd)
        assert isinstance(response, RpcErrorResponse)
    finally:
        await harness.dispose()


async def test_set_model_with_none_registry_returns_error(tmp_path: Path) -> None:
    harness = _make_harness()
    try:
        cmd = RpcCommandSetModel(
            provider="anthropic", model_id="claude-sonnet-4-5", id="r4"
        )
        response = await _handle_set_model(harness, None, cmd)
        assert isinstance(response, RpcErrorResponse)
        assert "ModelRegistry" in response.error
    finally:
        await harness.dispose()
