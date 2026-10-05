"""#369 / ADR-0252 — the TUI side: the post-``/login`` pick and ``/trust``.

1. The model picked after ``/login`` is persisted to GLOBAL settings, so it may
   only start from the user's own saved default. On ``5dee21d1`` it read the
   MERGED pair: in a pty (``.omc/probes/369-live/impl/base_tui.out``,
   ``f1-login-openai``) a repo's ``openai``/``gpt-4o-mini`` was picked after the
   user logged in to OpenAI and written into the user's global settings, from
   where it chose the model in every later directory. An untrusted repo's pair
   is no longer read at all; this pins the TRUSTED case, which the gate does not
   cover — trusting a repo applies its settings to its own launches, it does
   not make them the user's defaults everywhere.

2. ``/trust`` saves a decision for the next launch and says so, as pi does
   (``interactive-mode.ts:5299`` @ ``b223082bb``). On ``5dee21d1`` it said "Run
   /reload to apply it to project-local resources.", which a pty measurement
   showed false (``base_trustreload.out``: untrusted, ``/trust`` → Trust,
   ``/reload``, ``/hello`` was still "Unknown command"), and it offered two
   "this session only" answers that therefore changed nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.model_registry import ModelRegistry

from tests.tui.test_post_login_pick_362 import agent  # noqa: F401 — fixture

_PAIR = {"defaultProvider": "openai", "defaultModel": "gpt-4o-mini"}


async def test_the_login_pick_does_not_launder_a_trusted_repos_pair(
    agent: Path,  # noqa: F811 — the imported fixture
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aelix_ai.settings import SettingsManager
    from aelix_coding_agent.tui import login_wizard
    from test_first_run_onboarding_shell import (
        _harness_chrome,
        _launch,
        _quit_within,
        _spy_commits,
        _wait,
    )

    project = tmp_path / "project"
    (project / ".aelix").mkdir(parents=True)
    (project / ".aelix" / "settings.json").write_text(json.dumps(_PAIR), encoding="utf-8")
    (agent / "settings.json").write_text(json.dumps({"checkForUpdates": False}), encoding="utf-8")
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    # TRUSTED: the project scope is read (the launch would run this pair).
    settings = SettingsManager.create(cwd=str(project), agent_dir=agent, project_trusted=True)
    assert settings.get_default_model() == "gpt-4o-mini"

    async def _fake_login(**kwargs: Any) -> None:
        await kwargs["auth_storage"].set_api_key("openai", "own-openai-login-fake")

    monkeypatch.setattr(login_wizard, "run_login", _fake_login)
    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(
            runtime,
            chrome,
            auth_storage=storage,
            model_registry=registry,
            settings_manager=settings,
        )
        await _wait(lambda: chrome.app.is_running)
        try:
            pipe.send_text("/login\n")
            await _wait(lambda: bool(runtime.harness.set_models))
            await _wait(lambda: any("model →" in c for c in commits))
        finally:
            pipe.send_text("/quit\n")
            code = await _quit_within(task)

    assert code == 0
    chosen = runtime.harness.set_models[-1]
    assert chosen.provider == "openai"
    assert chosen.id != "gpt-4o-mini", "the repo's pair was picked"
    saved = json.loads((agent / "settings.json").read_text(encoding="utf-8"))
    assert saved.get("defaultModel") == chosen.id
    assert saved.get("defaultModel") != "gpt-4o-mini"


async def test_the_trust_command_says_restart_and_offers_no_session_only_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.cli.project_trust import ProjectTrustStore
    from aelix_coding_agent.tui.shell import run_tui

    from tests.tui.test_run_tui_smoke import _spy_commits
    from tests.tui.test_run_tui_statusline_settings import _harness_chrome, _quit_within, _wait

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    project = tmp_path / "project"
    (project / ".aelix").mkdir(parents=True)
    (project / ".aelix" / "settings.json").write_text(json.dumps(_PAIR), encoding="utf-8")

    offered: list[list[str]] = []
    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        import asyncio

        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=str(project),
                chrome=chrome,
                install_signal_handlers=False,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(runtime.harness.runtime.bound))
        ctx = runtime.harness.runtime.bound[0]

        async def _select(title: str, options: list[str], **_kwargs: Any) -> str:
            offered.append(list(options))
            return "Trust"

        ctx.select = _select  # type: ignore[method-assign]
        pipe.send_text("/trust\n")
        await _wait(lambda: any("Project trusted" in c for c in commits))
        pipe.send_text("/quit\n")
        code = await _quit_within(task)

    assert code == 0
    assert offered and not [o for o in offered[0] if "this session only" in o], offered
    line = next(c for c in commits if "Project trusted" in c)
    assert line == f"Project trusted (saved): {project}\nRestart aelix for this to take effect."
    assert ProjectTrustStore(agent_dir).get(project) is True
