"""#679 — a ``/reload`` that succeeds keeps the model and thinking level the session runs on.

pi's ``AgentSession.reload`` (``packages/coding-agent/src/core/agent-session.ts``)
touches neither: the same session keeps its model and level. aelix rebuilds the
harness, and the CLI factory built it from the launch inputs — ``--thinking`` or
``off`` for the level, and, when the launch named a model (``--model``,
``--provider``, ``--api-key``, a profile), that model over a ``set_model`` choice.
A reload that failed and was "kept" (ADR-0289 D3) already put both back; one that
succeeded did not.

These drive the real ``_async_main`` with ``run_rpc_mode`` stubbed: the test
makes the choices through the harness, as RPC ``set_model`` and
``set_thinking_level`` do, and reloads through the runtime, as
``resources/reload``, the TUI ``/reload`` and an extension's ``ctx.reload()`` do.
Hermetic: fake keys, an isolated agent dir and home, no network.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli.test_session_model_restore_376 import (  # noqa: F401 — ``env`` is a fixture
    _HAIKU,
    _SONNET,
    _ident,
    _sessions,
    _settings,
    env,
)

Steps = Callable[[Any, Any, dict[str, Any]], Awaitable[None]]


def _rpc(monkeypatch: pytest.MonkeyPatch, steps: Steps) -> dict[str, Any]:
    from aelix_coding_agent import modes

    seen: dict[str, Any] = {}

    async def _run(
        harness: Any, *, model_registry: Any, runtime_host: Any, harness_factory: Any, **_host: Any
    ) -> None:
        await steps(runtime_host, model_registry, seen)

    monkeypatch.setattr(modes, "run_rpc_mode", _run)
    return seen


def _state(runtime: Any) -> tuple[tuple[str, str], str]:
    harness = runtime.harness
    return _ident(harness.current_model), harness.state.thinking_level


async def _pick_sonnet_high(runtime: Any, registry: Any, seen: dict[str, Any]) -> None:
    harness = runtime.harness
    sonnet = next(m for m in registry.get_available() if _ident(m) == _SONNET)
    await harness.set_model(sonnet)
    await harness.set_thinking_level("high")
    seen["before"] = _state(runtime)
    await runtime.reload()
    seen["after"] = _state(runtime)


@pytest.mark.parametrize(
    "launch",
    [
        pytest.param(["--provider", "anthropic", "--model", "claude-haiku-4-5"], id="model-flag"),
        pytest.param([], id="settings-default"),
    ],
)
async def test_a_reload_keeps_the_picked_model_and_level(
    env: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    launch: list[str],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-haiku-4-5")
    seen = _rpc(monkeypatch, _pick_sonnet_high)

    code = await entry_mod._async_main(["--session-dir", _sessions(env), "--mode", "rpc", *launch])

    assert code == 0
    assert seen["before"] == (_SONNET, "high")
    assert seen["after"] == (_SONNET, "high")


async def test_a_reload_keeps_the_settings_default_level(
    env: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``defaultThinkingLevel`` is applied at launch only; the reload kept ``off`` before."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(
        env,
        defaultProvider="anthropic",
        defaultModel="claude-sonnet-4-5",
        defaultThinkingLevel="medium",
    )

    async def _reload(runtime: Any, _registry: Any, seen: dict[str, Any]) -> None:
        seen["before"] = _state(runtime)
        await runtime.reload()
        seen["after"] = _state(runtime)

    seen = _rpc(monkeypatch, _reload)
    code = await entry_mod._async_main(["--session-dir", _sessions(env), "--mode", "rpc"])

    assert code == 0
    assert seen["before"] == (_SONNET, "medium")
    assert seen["after"] == (_SONNET, "medium")


async def test_a_reload_records_no_model_or_level_change(
    env: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing changed, so the session file gains no ``model_change`` or level entry."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-haiku-4-5")

    async def _steps(runtime: Any, registry: Any, seen: dict[str, Any]) -> None:
        harness = runtime.harness
        sonnet = next(m for m in registry.get_available() if _ident(m) == _SONNET)
        await harness.set_model(sonnet)
        await harness.set_thinking_level("high")
        session = runtime.session
        seen["types_before"] = [e.type for e in await session.get_entries()]
        await runtime.reload()
        seen["types_after"] = [e.type for e in await session.get_entries()]

    seen = _rpc(monkeypatch, _steps)
    code = await entry_mod._async_main(
        ["--session-dir", _sessions(env), "--mode", "rpc", "--model", "claude-haiku-4-5"]
    )

    assert code == 0
    assert seen["types_after"] == seen["types_before"]
