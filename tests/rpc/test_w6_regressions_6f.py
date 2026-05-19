"""Sprint 6f W6 regression suite — Pi parity model-command lockdown.

Pins the W4 + W5 BLOCKING + MAJOR finds (P-170 / P-171 / P-172 / P-187
/ P-179 / P-182 / P-181 / P-184) so a future regression mechanically
trips. Owning ADR: ADR-0066 (Phase 4.6 strict superset closure).

Pi pin (ADR-0034): ``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.models import clamp_thinking_level, get_models
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
from aelix_coding_agent.rpc.rpc_mode import (
    _handle_cycle_model,
    _handle_get_available_models,
    _handle_set_model,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandCycleModel,
    RpcCommandGetAvailableModels,
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


def _make_harness(initial: Model | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=initial or Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
        )
    )


async def _make_registry_with_all_keys(tmp_path: Path) -> ModelRegistry:
    s = AuthStorage(path=tmp_path / "auth.json")
    await s.load()
    await s.set_api_key("anthropic", "sk-a")
    await s.set_api_key("openai", "sk-o")
    await s.set_api_key("openrouter", "sk-r")
    return ModelRegistry.in_memory(s)


# === P-170: cycle_model returns None when len(available) <= 1 ================


async def test_cycle_model_with_single_available_returns_null(
    tmp_path: Path,
) -> None:
    """Pi parity (P-170, ``agent-session.ts:1476``): rotation against a
    single-model list is a no-op (returns ``data: None``).
    """

    keys_to_clear = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    saved = {k: os.environ.pop(k, None) for k in keys_to_clear}
    try:
        # Register a custom in-memory provider configuration so the
        # registry surfaces exactly one available model. Easiest path:
        # store ONLY the anthropic key, then narrow the catalog by
        # over-registering a single-model custom provider config
        # (Sprint 6g closure). Simpler approach for this test: stub
        # ``get_available`` directly on a real registry instance.
        harness = _make_harness()
        s = AuthStorage(path=tmp_path / "auth.json")
        await s.load()
        await s.set_api_key("anthropic", "sk-a")
        registry = ModelRegistry.in_memory(s)
        # Narrow to exactly one model so the <=1 guard triggers.
        anthropic_models = [m for m in registry.get_all() if m.provider == "anthropic"]
        single_model = anthropic_models[0]
        registry.get_available = lambda: [single_model]  # type: ignore[method-assign]
        try:
            cmd = RpcCommandCycleModel(id="r1")
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


# === P-171: _handle_cycle_model persists clamped thinking_level ===============


async def test_cycle_model_persists_clamped_thinking_level(
    tmp_path: Path,
) -> None:
    """Pi parity (P-171, ``agent-session.ts:1490``): the clamped level is
    written back to harness state via ``set_thinking_level`` BEFORE the
    handler returns. The Sprint 6f W2 path only computed it for the
    response payload.
    """

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            thinking_level="xhigh",  # request high, expect clamp
        )
    )
    registry = await _make_registry_with_all_keys(tmp_path)
    try:
        available = registry.get_available()
        anthropic = next(m for m in available if m.provider == "anthropic")
        harness.set_current_model(anthropic)
        # Pre-conditions: harness's stored thinking_level is the
        # requested "xhigh"; anthropic seed model does NOT support
        # xhigh so the clamp should produce something else.
        assert harness.state.thinking_level == "xhigh"
        cmd = RpcCommandCycleModel(id="r1")
        response = await _handle_cycle_model(harness, registry, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        clamped = response.data["thinkingLevel"]
        # Pi parity: harness state was updated to the clamped value.
        assert harness.state.thinking_level == clamped
    finally:
        await harness.dispose()


# === P-172: set_model searches get_available (auth-filtered) =================


async def test_set_model_unconfigured_provider_returns_not_found(
    tmp_path: Path,
) -> None:
    """Pi parity (P-172, ``rpc-mode.ts:454-459``): searching the
    auth-filtered ``getAvailable()`` list means a request for a model
    whose provider has no configured auth surfaces as ``Model not
    found:`` rather than a stale-auth selection.
    """

    keys_to_clear = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    saved = {k: os.environ.pop(k, None) for k in keys_to_clear}
    try:
        harness = _make_harness()
        s = AuthStorage(path=tmp_path / "auth.json")
        await s.load()
        # NO API keys stored — every seed model fails the available filter.
        registry = ModelRegistry.in_memory(s)
        try:
            cmd = RpcCommandSetModel(
                provider="anthropic", model_id="claude-sonnet-4-5", id="r1"
            )
            response = await _handle_set_model(harness, registry, cmd)
            assert isinstance(response, RpcErrorResponse)
            assert response.command == "set_model"
            # Pi parity: exact error string.
            assert response.error == (
                "Model not found: anthropic/claude-sonnet-4-5"
            )
        finally:
            await harness.dispose()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


# === P-187: set_current_model writes _state.model directly ===================


async def test_set_current_model_propagates_to_state_model() -> None:
    """Pi parity (P-187, ``agent-session.ts:1423``): the setter writes
    ``state.model`` directly so downstream provider calls (lines
    955, 1087, 1644, 2230, 2240, 2343) that read ``_state.model``
    see the new model immediately. No ``_current_model_override``
    indirection.
    """

    harness = _make_harness()
    try:
        new_model = Model(id="claude-sonnet-4-5", provider="anthropic")
        harness.set_current_model(new_model)
        # Direct: ``_state.model`` now is the new model.
        assert harness._state.model is new_model
        # Property reads through.
        assert harness.current_model is new_model
    finally:
        await harness.dispose()


async def test_set_current_model_rejects_none() -> None:
    """W4 m4 / P-187: ``set_current_model(None)`` must raise rather
    than silently clearing state — Pi signature is non-nullable.
    """

    harness = _make_harness()
    try:
        with pytest.raises(ValueError, match="non-None"):
            harness.set_current_model(None)  # type: ignore[arg-type]
    finally:
        await harness.dispose()


# === P-182: cycle_model None thinking_level coerced to "off" =================


async def test_cycle_model_none_thinking_level_coerced_to_off(
    tmp_path: Path,
) -> None:
    """Pi parity (P-182, ``agent-session.ts:1490``): when
    ``state.thinking_level`` is :data:`None`, coerce to ``"off"``
    before clamping so the response carries a real Pi-shape level
    rather than propagating ``None``.
    """

    harness = _make_harness()
    # Force the None state explicitly — the harness defaults to "off",
    # but the Pi cycle_model code path must still cope if a Sprint 6a
    # extension cleared the level back to None at runtime.
    harness._state.thinking_level = None  # noqa: SLF001
    assert harness.state.thinking_level is None
    registry = await _make_registry_with_all_keys(tmp_path)
    try:
        available = registry.get_available()
        anthropic = next(m for m in available if m.provider == "anthropic")
        harness.set_current_model(anthropic)
        cmd = RpcCommandCycleModel(id="r1")
        response = await _handle_cycle_model(harness, registry, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        # Coerced + clamped — non-None Pi-shape level.
        clamped = response.data["thinkingLevel"]
        assert clamped is not None
        assert clamped in {"off", "minimal", "low", "medium", "high", "xhigh"}
    finally:
        await harness.dispose()


# === P-179: clamp_thinking_level(None) Sprint 6b back-compat =================


def test_clamp_thinking_level_none_returns_none_back_compat() -> None:
    """Sprint 6b back-compat (P-179): ``None`` propagates through.
    Non-Pi but documented so OpenAI-completions adapters can preserve
    "no reasoning effort requested" semantics.
    """

    model = next(iter(get_models("anthropic")))
    assert clamp_thinking_level(model, None) is None


# === P-181: exact Pi error-string assertions ==================================


async def test_set_model_miss_exact_pi_error_string(
    tmp_path: Path,
) -> None:
    """Pi parity (P-181, ``rpc-mode.ts:454-459``): exact error string."""

    keys_to_clear = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    saved = {k: os.environ.pop(k, None) for k in keys_to_clear}
    try:
        harness = _make_harness()
        s = AuthStorage(path=tmp_path / "auth.json")
        await s.load()
        await s.set_api_key("anthropic", "sk-a")
        registry = ModelRegistry.in_memory(s)
        try:
            cmd = RpcCommandSetModel(
                provider="anthropic", model_id="does-not-exist", id="r1"
            )
            response = await _handle_set_model(harness, registry, cmd)
            assert isinstance(response, RpcErrorResponse)
            assert response.error == (
                "Model not found: anthropic/does-not-exist"
            )
        finally:
            await harness.dispose()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def test_cycle_model_empty_data_none_exact_pi(tmp_path: Path) -> None:
    """Pi parity (P-181, ``agent-session.ts:1476``): empty available list
    returns ``data=None`` (Pi ``undefined`` ↔ Aelix :data:`None`).
    """

    keys_to_clear = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    saved = {k: os.environ.pop(k, None) for k in keys_to_clear}
    try:
        harness = _make_harness()
        s = AuthStorage(path=tmp_path / "auth.json")
        await s.load()
        registry = ModelRegistry.in_memory(s)
        try:
            assert registry.get_available() == []
            cmd = RpcCommandCycleModel(id="r1")
            response = await _handle_cycle_model(harness, registry, cmd)
            assert isinstance(response, RpcSuccessResponse)
            assert response.data is None
        finally:
            await harness.dispose()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def test_get_available_models_empty_models_list_exact_pi(
    tmp_path: Path,
) -> None:
    """Pi parity (P-181): empty available → ``{"models": []}``."""

    keys_to_clear = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    saved = {k: os.environ.pop(k, None) for k in keys_to_clear}
    try:
        harness = _make_harness()
        s = AuthStorage(path=tmp_path / "auth.json")
        await s.load()
        registry = ModelRegistry.in_memory(s)
        try:
            cmd = RpcCommandGetAvailableModels(id="r1")
            response = await _handle_get_available_models(harness, registry, cmd)
            assert isinstance(response, RpcSuccessResponse)
            assert response.data == {"models": []}
        finally:
            await harness.dispose()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


# === P-175: ModelRegistry._load_error cleared between refresh calls =========


async def test_refresh_clears_previous_load_error(tmp_path: Path) -> None:
    """Pi parity (P-175, ``model-registry.ts::loadModels``): the load
    error from a previous pass is cleared at the top of every
    ``_load_models`` call so a successful reload drops the stale
    message.
    """

    s = AuthStorage(path=tmp_path / "auth.json")
    await s.load()
    registry = ModelRegistry.in_memory(s)
    # Inject a stale error from a hypothetical prior pass.
    registry._load_error = "stale modify_models failure"
    assert registry.get_error() == "stale modify_models failure"
    # A successful refresh (no modify_models registered) must clear it.
    registry.refresh()
    assert registry.get_error() is None

