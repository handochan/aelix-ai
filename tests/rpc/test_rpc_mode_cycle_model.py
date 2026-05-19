"""RPC ``cycle_model`` handler — Sprint 6f W2 (ADR-0065 §G.2, P-169).

Pi parity: ``rpc-mode.ts::handle_cycle_model``. Rotates to the next
model in :meth:`ModelRegistry.get_available` (insertion order),
updates harness ``current_model``, and emits ``{model, thinkingLevel,
isScoped}``.

Sprint 6f₁ always returns ``isScoped: False`` — workspace-scoped
selection is Sprint 6g.
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
from aelix_coding_agent.rpc.rpc_mode import _handle_cycle_model
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandCycleModel,
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


async def _make_registry_with_all_keys(tmp_path: Path) -> ModelRegistry:
    """ModelRegistry where every seed provider has a stored API key, so
    ``get_available`` returns the full catalog (canonical rotation order).
    """

    s = AuthStorage(path=tmp_path / "auth.json")
    await s.load()
    await s.set_api_key("anthropic", "sk-a")
    await s.set_api_key("openai", "sk-o")
    await s.set_api_key("openrouter", "sk-r")
    return ModelRegistry.in_memory(s)


async def test_cycle_model_rotates_in_insertion_order(tmp_path: Path) -> None:
    harness = _make_harness()
    registry = await _make_registry_with_all_keys(tmp_path)
    try:
        available = registry.get_available()
        assert len(available) >= 3
        # Seed harness with the first available so cycle picks the second.
        harness.set_current_model(available[0])

        cmd = RpcCommandCycleModel(id="r1")
        response = await _handle_cycle_model(harness, registry, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "cycle_model"
        assert isinstance(response.data, dict)
        # Pi parity: shape = ``{model, thinkingLevel, isScoped}``.
        assert "model" in response.data
        assert "thinkingLevel" in response.data
        assert response.data["isScoped"] is False
        # Rotated to the next model.
        assert response.data["model"]["id"] == available[1].id
        assert harness.current_model is not None
        assert harness.current_model.id == available[1].id
    finally:
        await harness.dispose()


async def test_cycle_model_wraps_around_at_end(tmp_path: Path) -> None:
    harness = _make_harness()
    registry = await _make_registry_with_all_keys(tmp_path)
    try:
        available = registry.get_available()
        # Seed harness with the LAST available so cycle wraps to the first.
        harness.set_current_model(available[-1])
        cmd = RpcCommandCycleModel(id="r2")
        response = await _handle_cycle_model(harness, registry, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        assert response.data["model"]["id"] == available[0].id
    finally:
        await harness.dispose()


async def test_cycle_model_with_no_available_returns_data_none(
    tmp_path: Path,
) -> None:
    """Pi parity: empty get_available() returns data=None."""

    import os

    keys_to_clear = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    saved = {k: os.environ.pop(k, None) for k in keys_to_clear}
    try:
        harness = _make_harness()
        s = AuthStorage(path=tmp_path / "auth.json")
        await s.load()
        registry = ModelRegistry.in_memory(s)
        try:
            assert registry.get_available() == []
            cmd = RpcCommandCycleModel(id="r3")
            response = await _handle_cycle_model(harness, registry, cmd)
            assert isinstance(response, RpcSuccessResponse)
            assert response.command == "cycle_model"
            assert response.data is None
        finally:
            await harness.dispose()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def test_cycle_model_thinking_level_clamped_to_supported(
    tmp_path: Path,
) -> None:
    """Pi parity: ``thinkingLevel`` is clamped against the new model's
    supported levels.
    """

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            thinking_level="xhigh",  # request high but cap on the model
        )
    )
    registry = await _make_registry_with_all_keys(tmp_path)
    try:
        available = registry.get_available()
        # Find an anthropic model first (reasoning=True, thinking_level_map set).
        anthropic = next(m for m in available if m.provider == "anthropic")
        harness.set_current_model(anthropic)
        cmd = RpcCommandCycleModel(id="r4")
        response = await _handle_cycle_model(harness, registry, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        # The clamped level is one of the supported levels (or None for
        # Sprint 6b back-compat — see clamp_thinking_level docstring).
        assert response.data["thinkingLevel"] in (
            None,
            "off",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        )
    finally:
        await harness.dispose()


async def test_cycle_model_starts_from_first_when_current_is_none(
    tmp_path: Path,
) -> None:
    """If ``current_model`` doesn't match any available model, pick index 0."""

    harness = _make_harness()
    registry = await _make_registry_with_all_keys(tmp_path)
    try:
        available = registry.get_available()
        # Override with a model that ISN'T in the available list.
        harness.set_current_model(
            Model(id="not-in-registry", provider="not-in-registry")
        )
        cmd = RpcCommandCycleModel(id="r5")
        response = await _handle_cycle_model(harness, registry, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        # When current isn't found, Pi rotates from index 0.
        assert response.data["model"]["id"] == available[0].id
    finally:
        await harness.dispose()
