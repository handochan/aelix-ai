"""#362 / ADR-0250 guard 1 — the model picked after ``/login`` is not a ``.env`` key's choice.

After a login the TUI (``tui/shell.py``) asks pi's ``find_initial_model`` for a
model when the current one cannot run. That port takes the first runnable model
of ``get_available()`` in ``DEFAULT_MODEL_PER_PROVIDER`` order — and
``get_available()`` counts a key a cwd ``.env`` supplied. Measured on
``9ca53a4f`` (``/tmp/362-work/design/probe_initial_model.out``): a stored Codex
login plus a ``.env`` ``ANTHROPIC_API_KEY`` picked ``anthropic claude-opus-4-7``.
The shell now hands the port a view that leaves out every provider only a
``.env`` authenticates while the user holds a credential of their own for any
provider (rows or not), and whose ``find`` offers only what it offers — the
saved-default arm reads ``find`` (Codex's second cross-review of ``a0edf615``,
F1 and F3, below).
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv, register_providers
from aelix_coding_agent.core.model_resolver import find_initial_model
from aelix_coding_agent.model_registry import ModelRegistry


def _jwt(payload: dict[str, Any]) -> str:
    def enc(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"


_CODEX = {
    "openai-codex": {
        "type": "oauth",
        "refresh": "r",
        "access": _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "a"}}),
        "expires": int(time.time() * 1000) + 10**12,
    }
}


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    path = tmp_path / "agent"
    path.mkdir()
    (path / "models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(path))
    register_providers()
    return path


async def _pick(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, auth: dict[str, Any], dotenv: str
) -> tuple[Any, Any]:
    (agent / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
    if dotenv:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".env").write_text(dotenv, encoding="utf-8")
        for name in [line.partition("=")[0] for line in dotenv.splitlines()] + [
            "AELIX_DOTENV_ADMITTED"
        ]:
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
        load_dotenv(str(project / ".env"))
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    from aelix_coding_agent.tui.shell import _RouteAuthView

    view: Any = _RouteAuthView(registry)
    viewed = await find_initial_model(
        default_provider=None, default_model_id=None, model_registry=view
    )
    raw = await find_initial_model(
        default_provider=None, default_model_id=None, model_registry=registry
    )
    return viewed.model, raw.model


async def test_a_dotenv_key_does_not_pick_the_model_after_login(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    viewed, raw = await _pick(agent, monkeypatch, tmp_path, _CODEX, "ANTHROPIC_API_KEY=planted\n")
    # The port alone still prefers the .env's vendor — the measured vector.
    assert raw is not None and raw.provider == "anthropic"
    assert viewed is not None and viewed.provider == "openai-codex"


async def test_a_dotenv_only_session_still_gets_a_model(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing of the user's own competes, so the view offers everything (ADR-0250 §6)."""

    viewed, raw = await _pick(agent, monkeypatch, tmp_path, {}, "ANTHROPIC_API_KEY=planted\n")
    assert viewed is not None and raw is not None
    assert (viewed.provider, viewed.id) == (raw.provider, raw.id) == (raw.provider, raw.id)
    assert viewed.provider == "anthropic"


# --- Codex's second cross-review of a0edf615 (F1, F3) -----------------------------------


async def _view(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, auth: dict[str, Any], dotenv: str
) -> tuple[Any, ModelRegistry]:
    (agent / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
    if dotenv:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".env").write_text(dotenv, encoding="utf-8")
        for name in [line.partition("=")[0] for line in dotenv.splitlines()] + [
            "AELIX_DOTENV_ADMITTED"
        ]:
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
        load_dotenv(str(project / ".env"))
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    from aelix_coding_agent.tui.shell import _RouteAuthView

    return _RouteAuthView(registry), registry


_OWN_OR = {"openrouter": {"type": "api_key", "key": "own-login-fake"}}


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_a_saved_default_only_a_dotenv_key_authenticates_is_skipped(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_dotenv: bool
) -> None:
    """F1 (``probe_login.py``): the saved-default arm reads ``find``, which offered everything.

    A project ``.aelix/settings.json`` names ``google-vertex/gemini-3.1-pro-preview``
    (the repo's choice); ``/login`` stored the user's OpenRouter key. ``a0edf615``
    with ``.env`` ``GOOGLE_CLOUD_API_KEY``: ``aiplatform.googleapis.com`` on the
    file's key; without it: ``openrouter.ai`` on the user's. Now the view's
    ``find`` offers only what its ``get_available`` offers, so the default is
    skipped as an unrunnable one is, and both land on OpenRouter.
    """

    dotenv = "GOOGLE_CLOUD_API_KEY=project-vertex-fake\n" if with_dotenv else ""
    view, registry = await _view(agent, monkeypatch, tmp_path, _OWN_OR, dotenv)
    if with_dotenv:
        # The registry alone would run it — the measured vector.
        assert registry.find("google-vertex", "gemini-3.1-pro-preview") in registry.get_available()
    assert view.find("google-vertex", "gemini-3.1-pro-preview") is None
    picked = await find_initial_model(
        default_provider="google-vertex",
        default_model_id="gemini-3.1-pro-preview",
        model_registry=view,
    )
    assert picked.model is not None and picked.model.provider == "openrouter"
    assert (await registry.get_api_key_and_headers(picked.model)).api_key == "own-login-fake"


async def test_a_saved_default_the_user_can_run_is_still_picked(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The filtered ``find`` keeps the arm working for a default the view offers."""

    view, _registry = await _view(
        agent, monkeypatch, tmp_path, _OWN_OR, "GOOGLE_CLOUD_API_KEY=project-vertex-fake\n"
    )
    picked = await find_initial_model(
        default_provider="openrouter",
        default_model_id="openai/gpt-4o-mini",
        model_registry=view,
    )
    assert picked.model is not None
    assert (picked.model.provider, picked.model.id) == ("openrouter", "openai/gpt-4o-mini")


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_an_own_credential_on_a_provider_with_no_models_keeps_the_pick_off_the_dotenv(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_dotenv: bool
) -> None:
    """F3 in the view: ``auth.json`` holds a key for a provider registered with no models.

    ``a0edf615``'s view fell back to everything when no own provider had rows,
    so with a ``.env`` ``ANTHROPIC_API_KEY`` the pick was ``anthropic``. Now
    nothing is picked either way (the user is told to /model).
    """

    from aelix_coding_agent.model_registry import ProviderConfigInput

    dotenv = "ANTHROPIC_API_KEY=planted\n" if with_dotenv else ""
    view, registry = await _view(
        agent,
        monkeypatch,
        tmp_path,
        {"private-seat": {"type": "api_key", "key": "own-seat-fake"}},
        dotenv,
    )
    registry.register_provider("private-seat", ProviderConfigInput(name="seat, no models"))
    assert view.get_available() == []
    picked = await find_initial_model(
        default_provider=None, default_model_id=None, model_registry=view
    )
    assert picked.model is None


# === verify round 4, B2: the call site, through the real /login ===================


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_the_login_command_picks_through_the_route_auth_view(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_dotenv: bool
) -> None:
    """The wiring in ``tui/shell.py`` (``find_initial_model(model_registry=_RouteAuthView(…))``).

    Every row above calls the view directly, so replacing the call site's view
    with the raw registry (verify round 4's sabotage V1c) left them all green
    while Codex's ``probe_login.py`` went to ``aiplatform.googleapis.com`` on
    the ``.env`` key again. This drives the real ``run_tui`` and the real
    ``/login`` command (``test_first_run_onboarding_shell``'s headless
    scaffolding; only ``run_login`` is replaced by what its API-key sub-flow
    stores): the saved settings default names ``openai/gpt-4o-mini`` and, in
    one half, the project's ``.env`` carries ``OPENAI_API_KEY``; ``/login``
    stores the user's own OpenRouter key. The model set on the harness, and the
    bearer the registry hands its request, are the user's in both halves.

    #369 moved the pair from the project's ``.aelix/settings.json`` to the
    user's GLOBAL settings: the post-login pick now reads the global pair only
    (a project pair never reaches it, ``test_project_settings_trust_369.py``),
    so a project pair would leave the saved-default arm empty and the
    raw-registry sabotage above green. In global settings the arm still offers
    ``openai/gpt-4o-mini``, and only the view keeps it off the ``.env`` key.
    """

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
    project.mkdir()
    (agent / "settings.json").write_text(
        json.dumps(
            {"checkForUpdates": False, "defaultProvider": "openai", "defaultModel": "gpt-4o-mini"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    if with_dotenv:
        (project / ".env").write_text("OPENAI_API_KEY=project-openai-fake\n", encoding="utf-8")
        for name in ("OPENAI_API_KEY", "AELIX_DOTENV_ADMITTED"):
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
        load_dotenv(str(project / ".env"))
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    settings = SettingsManager.create(cwd=str(project), agent_dir=agent)
    assert settings.get_default_provider() == "openai"

    async def _fake_login(**kwargs: Any) -> None:
        # What the API-key sub-flow stores on success.
        await kwargs["auth_storage"].set_api_key("openrouter", "own-login-fake")

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
            # The "model →" line is committed after set_model; quitting first can
            # leave it unprinted (seen once in ~70 runs before this wait).
            await _wait(lambda: any("model →" in c for c in commits))
        finally:
            pipe.send_text("/quit\n")
            code = await _quit_within(task)

    assert code == 0
    chosen = runtime.harness.set_models[-1]
    if with_dotenv:
        # The raw registry would run the project's pair on the .env key — the vector.
        assert registry.find("openai", "gpt-4o-mini") in registry.get_available()
    assert chosen.provider == "openrouter", (chosen.provider, chosen.id, commits)
    assert "openrouter.ai" in chosen.base_url
    assert (await registry.get_api_key_and_headers(chosen)).api_key == "own-login-fake"
    assert any("model →" in c for c in commits), commits
