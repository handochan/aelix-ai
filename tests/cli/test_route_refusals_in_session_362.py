"""#362 / ADR-0250 — the in-session paths that take a model string refuse as the launch does.

``/agents use <profile>`` and ``/model``'s UNDECIDED fallback (a registry whose
introspection failed) both hand a string to the launch resolver. With a registry,
an ambiguous or unknown string is now pi's refusal, before anything switches —
on ``aa026d08`` an OpenRouter key made both an OpenRouter id, and without one the
profile was applied with its model silently skipped. Without a registry the
resolver sees no credential and no user-defined provider, so it is not asked to
refuse (the caller's own gates stay in charge).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.cli.model_switch import switch_model_argument
from aelix_coding_agent.model_registry import ModelRegistry

from tests.tui.test_agents_command import _Bench, _write_profile

_AMBIGUOUS = 'Model "gpt-4o-mini" is ambiguous across providers'


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    path = tmp_path / "agent"
    path.mkdir()
    (path / "models.json").write_text("{}", encoding="utf-8")
    (path / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(path))
    return path


async def _registry(agent: Path) -> ModelRegistry:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    return ModelRegistry.create(storage, str(agent / "models.json"))


async def test_agents_use_refuses_an_ambiguous_profile_model(agent: Path, tmp_path: Path) -> None:
    bench = _Bench(tmp_path)
    bench.service.model_registry = await _registry(agent)
    _write_profile(
        bench.user_agents, "scout", "name: scout\ndescription: d\nmodel: gpt-4o-mini", "b"
    )
    before = bench.harness.current_model
    out = await bench.run("/agents use scout")
    assert _AMBIGUOUS in out
    assert bench.harness.current_model is before
    assert bench.service.active is None


class _BrokenRegistry:
    """A registry whose introspection fails: ``/model`` resolution is UNDECIDED."""

    def get_all(self) -> list[Any]:
        raise RuntimeError("introspection failed")

    def get_available(self) -> list[Any]:
        raise RuntimeError("introspection failed")


class _Harness:
    current_model = None

    def __init__(self) -> None:
        self.set_calls: list[Any] = []

    async def set_model(self, model: Any) -> None:
        self.set_calls.append(model)


async def test_model_undecided_fallback_refuses_with_the_routes_error(agent: Path) -> None:
    harness = _Harness()
    switched = await switch_model_argument(
        "gpt-4o-mini",
        harness=harness,
        model_registry=_BrokenRegistry(),
        settings_manager=None,
        warn=lambda _line: None,
    )
    assert harness.set_calls == []
    assert switched.refusal is not None and _AMBIGUOUS in switched.refusal
