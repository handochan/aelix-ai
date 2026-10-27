"""#609 — with ``checkModelCatalog`` off, only ``aelix update --models`` checks the published catalog.

The automatic triggers (TUI and RPC start-up, the model pickers, a ``/model``
miss, ``/login``) ask ``model_refresh.auto_refresh_allowed`` off the settings
manager; the command asks ``refresh_allowed`` alone. The cache is installed
either way. Driven against the loopback catalog of
``tests/hosted_catalog_fixtures_434.py``; no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_ai import hosted_catalog as hc
from aelix_ai import models as catalog
from aelix_ai.oauth import AuthStorage
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.model_registry import ModelRegistry

from tests.hosted_catalog_fixtures_434 import (  # noqa: F401 - the fixture
    INDEX_PATH,
    NEW_OR_ID,
    CatalogServer,
    catalog_server,
    new_row,
    revision,
)

_ADDED = {"openrouter": {NEW_OR_ID: new_row()}}


def _manager(on: bool) -> SettingsManager:
    return SettingsManager.in_memory({"checkModelCatalog": on})


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from tests.env_sandbox import sandbox_home

    sandbox_home(monkeypatch, tmp_path / "home")
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    monkeypatch.chdir(tmp_path)
    return agent


async def _registry(agent: Path) -> ModelRegistry:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    return ModelRegistry.create(storage, str(agent / "models.json"))


def test_the_gate_reads_the_setting_and_an_embedder_without_one_is_on(
    catalog_server: CatalogServer,  # noqa: F811 - the source is on
) -> None:
    from aelix_coding_agent.model_refresh import auto_refresh_allowed, refresh_allowed

    assert refresh_allowed()
    assert auto_refresh_allowed(_manager(True)) and auto_refresh_allowed(None)
    assert not auto_refresh_allowed(_manager(False))


async def test_off_no_start_up_refresh_runs(
    agent: Path, catalog_server: CatalogServer  # noqa: F811
) -> None:
    from aelix_coding_agent.model_refresh import start_background_refresh

    catalog_server.publish(revision(add=_ADDED))
    registry = await _registry(agent)

    assert start_background_refresh(registry, trigger="startup", settings_manager=_manager(False)) is None
    assert catalog_server.log == []
    task = start_background_refresh(registry, trigger="startup", settings_manager=_manager(True))
    assert task is not None
    await task
    assert catalog.installed_overlay() is not None


async def test_off_a_model_miss_does_not_refresh(
    catalog_server: CatalogServer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from aelix_coding_agent.cli import model_switch
    from aelix_coding_agent.core import model_argument
    from aelix_coding_agent.core.model_argument import ArgumentResolution

    async def resolve(argument: str, **_k: Any) -> ArgumentResolution:
        return ArgumentResolution(error="no model 'x' is available", no_match=True)

    class Registry:
        def refresh_models(self) -> None:
            return None

    class Harness:
        current_model = None

    monkeypatch.setattr(model_argument, "resolve_model_argument", resolve)
    warned: list[str] = []

    switched = await model_switch.switch_model_argument(
        "x", harness=Harness(), model_registry=Registry(), settings_manager=_manager(False), warn=warned.append
    )

    assert switched.refusal is not None and warned == []
    assert catalog_server.log == []


async def test_off_the_command_still_checks_and_the_cache_is_still_installed(
    agent: Path, catalog_server: CatalogServer, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    from aelix_coding_agent.cli import entry

    (agent / "settings.json").write_text(json.dumps({"checkModelCatalog": False}), encoding="utf-8")
    raw = revision(add=_ADDED)
    catalog_server.publish(raw)

    assert await entry._async_main(["update", "--models"]) == 0
    assert "published" in capsys.readouterr().err
    assert len(catalog_server.requests(INDEX_PATH)) == 1

    catalog.install_overlay(None)
    registry = await _registry(agent)  # the next start's cache phase
    assert catalog.installed_overlay() == hc.revision_name(raw)
    assert registry.find("openrouter", NEW_OR_ID) is not None
