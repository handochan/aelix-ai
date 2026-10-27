"""#609 — the automatic catalog triggers of a running TUI or RPC session read ``checkModelCatalog``.

``tests/test_manual_catalog_refresh_609.py`` calls the gates themselves; these
launch ``run_tui`` and ``run_rpc_mode`` over a registry whose ``refresh_models``
only records its trigger, so a call site that stops passing the settings
manager, or stops asking, fails here (review round 1: removing four of the
six gates left every test green). The drive covers start-up, both pickers, a
``/model`` miss, ``/login`` and RPC start-up; the miss's own gate and #136's
backfill are pinned at ``switch_model_argument`` level as well. With the setting on, the same drive reaches
each trigger, so the off run cannot pass by never getting there. No network:
the URL is a loopback the fake never dials.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.models_store import ModelsRefreshResult
from aelix_ai.oauth import AuthStorage
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.model_registry import ModelRegistry
from aelix_coding_agent.tui import login_wizard
from aelix_coding_agent.tui.shell import run_tui

# The onboarding module's fakes, whose harness takes ``set_model`` (the smoke
# harness's does not, so a ``/model <id>`` there stops at "unavailable").
from tests.tui.test_first_run_onboarding_shell import (
    _harness_chrome,
    _quit_within,
    _spy_commits,
    _wait,
)

_TUI_TRIGGERS = ["startup", "model-picker", "scoped-models-picker", "model-command", "login"]


@pytest.fixture
def triggers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The source is on (a loopback URL), and each ``refresh_models`` call records its trigger."""

    monkeypatch.setenv("AELIX_MODEL_CATALOG_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("AELIX_OFFLINE", raising=False)
    monkeypatch.delenv("PI_OFFLINE", raising=False)
    monkeypatch.delenv("AELIX_SUBAGENT_DEPTH", raising=False)
    seen: list[str] = []

    async def _record(self: ModelRegistry, **kwargs: Any) -> ModelsRefreshResult:
        seen.append(kwargs["trigger"])
        return ModelsRefreshResult()

    monkeypatch.setattr(ModelRegistry, "refresh_models", _record)
    return seen


async def _drive_tui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, on: bool, seen: list[str]
) -> None:
    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    await auth.set_api_key("anthropic", "sk-test")
    registry = ModelRegistry(auth, None)

    logins: list[str] = []

    async def _fake_login(**kwargs: Any) -> None:
        await kwargs["auth_storage"].set_api_key("openai", "sk-test")
        logins.append("openai")

    monkeypatch.setattr(login_wizard, "run_login", _fake_login)
    settings = SettingsManager.in_memory({"checkModelCatalog": on})

    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=registry,
                auth_storage=auth,
                settings_manager=settings,
            )
        )
        try:
            await _wait(lambda: chrome.app.is_running)
            # Each trigger in turn, waiting for its record when it should fire
            # (and for its modal either way), so no run is shared with the last.
            await asyncio.sleep(0.2)
            for command, trigger in (("/model", "model-picker"), ("/scoped-models", "scoped-models-picker")):
                pipe.send_text(f"{command}\n")
                await _wait(lambda: chrome.is_modal_open(), what=f"the {command} picker")
                if on:
                    await _wait(lambda t=trigger: t in seen, what=f"the {trigger} refresh")
                else:
                    await asyncio.sleep(0.2)
                pipe.send_text("\x1b")
                await _wait(lambda: not chrome.is_modal_open(), what=f"the {command} picker to close")
            # A /model that matches nothing (its warning is the command's end).
            pipe.send_text("/model anthropic/nosuchmodel-609\n")
            await _wait(lambda: any("nosuchmodel-609" in c for c in commits), what="the /model miss")
            if on:
                await _wait(lambda: "model-command" in seen, what="the /model miss refresh")
            pipe.send_text("/login\n")
            if on:
                await _wait(lambda: "login" in seen, what="the /login refresh")
            else:
                await _wait(lambda: bool(logins), what="the login")
                await asyncio.sleep(0.3)
        finally:
            pipe.send_text("/quit\n")
            await _quit_within(task)


async def test_off_no_tui_trigger_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, triggers: list[str]
) -> None:
    await _drive_tui(tmp_path, monkeypatch, on=False, seen=triggers)
    assert triggers == []


async def test_on_the_same_drive_reaches_every_tui_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, triggers: list[str]
) -> None:
    await _drive_tui(tmp_path, monkeypatch, on=True, seen=triggers)
    assert sorted(set(triggers)) == sorted(_TUI_TRIGGERS)


@pytest.mark.parametrize("on", [False, True])
async def test_rpc_start_up_reads_the_setting(
    tmp_path: Path, triggers: list[str], on: bool
) -> None:
    from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

    from tests.rpc.test_rpc_mode_stdin_stdout import _build_harness

    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    stdin = asyncio.StreamReader()
    stdin.feed_eof()
    await asyncio.wait_for(
        run_rpc_mode(
            _build_harness(),
            model_registry=ModelRegistry(auth, None),
            stdin=stdin,
            stdout_write=lambda _data: None,
            install_signal_handlers=False,
            settings_manager=SettingsManager.in_memory({"checkModelCatalog": on}),
        ),
        timeout=10.0,
    )
    await asyncio.sleep(0)
    assert triggers == (["rpc-startup"] if on else [])
