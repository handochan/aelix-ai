"""RPC ``get_available_models`` handler — Sprint 6f W2 (ADR-0065 §G.3).

Pi parity: ``rpc-mode.ts::handle_get_available_models``. Returns
``{models: [...]}`` filtered through :meth:`ModelRegistry.get_available`
(configured-auth check).
"""

from __future__ import annotations

import os
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
from aelix_coding_agent.rpc.rpc_mode import _handle_get_available_models
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandGetAvailableModels,
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


async def test_get_available_models_returns_filtered_list(tmp_path: Path) -> None:
    harness = _make_harness()
    s = AuthStorage(path=tmp_path / "auth.json")
    await s.load()
    await s.set_api_key("anthropic", "sk-a")
    registry = ModelRegistry.in_memory(s)
    try:
        cmd = RpcCommandGetAvailableModels(id="r1")
        response = await _handle_get_available_models(harness, registry, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_available_models"
        assert response.id == "r1"
        assert isinstance(response.data, dict)
        assert "models" in response.data
        models = response.data["models"]
        assert isinstance(models, list)
        assert len(models) >= 1
        # Every returned entry is Pi-shape camelCase.
        first = models[0]
        assert "id" in first
        assert "provider" in first
        assert "thinkingLevelMap" in first
        assert "maxTokens" in first
        # Only anthropic has a stored API key → all returned models are anthropic.
        for m in models:
            assert m["provider"] == "anthropic"
    finally:
        await harness.dispose()


async def test_get_available_models_empty_when_no_auth(tmp_path: Path) -> None:
    keys_to_clear = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    saved = {k: os.environ.pop(k, None) for k in keys_to_clear}
    try:
        harness = _make_harness()
        s = AuthStorage(path=tmp_path / "auth.json")
        await s.load()
        registry = ModelRegistry.in_memory(s)
        try:
            cmd = RpcCommandGetAvailableModels(id="r2")
            response = await _handle_get_available_models(harness, registry, cmd)
            assert isinstance(response, RpcSuccessResponse)
            assert isinstance(response.data, dict)
            assert response.data["models"] == []
        finally:
            await harness.dispose()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def test_get_available_models_none_registry_returns_error(
    tmp_path: Path,
) -> None:
    harness = _make_harness()
    try:
        cmd = RpcCommandGetAvailableModels(id="r3")
        response = await _handle_get_available_models(harness, None, cmd)
        assert isinstance(response, RpcErrorResponse)
        assert "ModelRegistry" in response.error
    finally:
        await harness.dispose()
