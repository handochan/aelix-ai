"""#367 verify round 1 — the post-login pick does not land on a held launch's refused provider.

A launch model naming a provider an extension registers in ``session_start`` is
refused (ADR-0249 §2.3 amended, ADR-0250 §2.11); interactive holds the harness
on ``Model(id, provider)`` with ``api='unknown'``. That placeholder cannot run,
so a ``/login`` reaches the shell's post-login pick (#23), which asks
``find_initial_model`` with the saved settings default first — and when the
launch was a settings ``defaultProvider`` / ``defaultModel`` pair naming that
provider, the default IS the refused launch input, now in the registry. On
dcc78170 the pick switched to it (``run_tui`` had no hold to read), so the next
prompt went to the provider though nobody picked a model. Now it asks again
without the saved default, and selects nothing when even that lands there.

Drives the real ``run_tui`` headlessly (the onboarding-shell scaffolding).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _polling import quit_within as _quit_within  # sibling helper (prepend mode)
from _polling import wait_until  # sibling helper (pytest prepend import mode)
from aelix_ai.providers._env_api_keys import ENV_API_KEYS
from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.runtime_bootstrap import (
    LateRoute,
    LateRouteHold,
    register_providers,
)
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput
from aelix_coding_agent.tui import login_wizard

from tests.tui.test_first_run_onboarding_shell import (
    _auth,
    _harness_chrome,
    _launch,
    _spy_commits,
)

_REASON = (
    "The launch model \"sessext/m1\" names provider 'sessext', which an extension "
    "registered while a session was starting (for example in a session_start handler), "
    "after the launch model was chosen. "
    "Register 'sessext' in the extension's setup() (its factory) to use it at launch."
)


@pytest.fixture(autouse=True)
def _scrub_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for names in ENV_API_KEYS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    # ``is_runnable`` fails open with no api adapter registered, and the
    # post-login pick runs only for a current model that cannot run.
    register_providers()


def _registry(auth: Any) -> ModelRegistry:
    """The registry as ``session_start`` left it: ``sessext`` with its own key."""

    registry = ModelRegistry(auth, None)
    registry.register_provider(
        "sessext",
        ProviderConfigInput(
            name="session-start probe",
            api_key="ext-fake-literal",
            models={
                "m1": Model(
                    id="m1",
                    provider="sessext",
                    api="openai-completions",
                    base_url="http://127.0.0.1:9/ext/v1",
                )
            },
        ),
    )
    return registry


async def _login_while_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    login_provider: str | None,
    *,
    hold_made: bool = True,
) -> tuple[list[Model], list[str]]:
    auth = await _auth(tmp_path)
    registry = _registry(auth)

    async def _fake_login(**kwargs: Any) -> None:
        if login_provider is not None:
            await kwargs["auth_storage"].set_api_key(login_provider, "sk-test-fake")

    monkeypatch.setattr(login_wizard, "run_login", _fake_login)
    placeholder = Model(id="m1", provider="sessext")
    # The launch saw no user-defined provider; ``sessext`` arrived in a
    # ``session_start`` handler, so it is the late one.
    hold = LateRoute(registry, launch_providers=frozenset(), session_start_providers={"sessext"})
    if hold_made:
        hold.last = LateRouteHold(placeholder, _REASON)
    settings = SettingsManager.in_memory({"defaultProvider": "sessext", "defaultModel": "m1"})

    async with _harness_chrome() as (runtime, chrome, pipe):
        runtime.harness.current_model = placeholder
        commits = _spy_commits(chrome)
        task = _launch(
            runtime,
            chrome,
            auth_storage=auth,
            model_registry=registry,
            settings_manager=settings,
            late_route=hold,
        )
        await wait_until(lambda: chrome.app.is_running)
        try:
            pipe.send_text("/login\n")
            await wait_until(
                lambda: (
                    bool(runtime.harness.set_models)
                    or any(c.startswith("Logged in") for c in commits)
                )
            )
        finally:
            pipe.send_text("/quit\n")
            await _quit_within(task)
    return runtime.harness.set_models, commits


async def test_a_login_while_held_picks_the_provider_logged_into(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_models, _commits = await _login_while_held(tmp_path, monkeypatch, "anthropic")
    assert [m.provider for m in set_models] == ["anthropic"], set_models


async def test_a_login_while_held_with_nothing_else_selects_nothing_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_models, commits = await _login_while_held(tmp_path, monkeypatch, None)
    assert set_models == []
    assert any(
        c.startswith("Logged in. " + _REASON) and c.endswith("Use /model to select a model.")
        for c in commits
    ), commits


async def test_a_login_before_any_hold_was_made_says_why_in_the_rule_s_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#367 verify round 8 (non-blocking a). ``LateRoute.landing_reason``
    falls back to its own text when no hold has been made in this process
    (``last`` is ``None``) - the pick lands on a late provider before any
    re-resolution held one. No row pinned that text; this one does, in the
    round-9 wording ("while a session was starting", true whatever registered
    the provider in that window)."""

    set_models, commits = await _login_while_held(tmp_path, monkeypatch, None, hold_made=False)
    assert set_models == []
    fallback = (
        "Provider 'sessext' was registered by an extension while a session was starting "
        "(for example in a session_start handler), after the launch route was chosen; it is "
        "used only when you pick it. Register 'sessext' in the extension's setup() (its "
        "factory) to use it at launch."
    )
    assert f"Logged in. {fallback} Use /model to select a model." in commits, commits


async def test_a_prompt_while_held_repeats_the_hold_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#367 round 3 (non-blocking). A prompt on the held placeholder is refused
    before any turn; it used to say the generic "could not be resolved to a
    known API protocol ... models.json" text, which points at the wrong cure.
    It repeats the hold's reason and its remedy instead."""

    auth = await _auth(tmp_path)
    registry = _registry(auth)
    placeholder = Model(id="m1", provider="sessext")
    hold = LateRoute(registry, launch_providers=frozenset(), session_start_providers={"sessext"})
    hold.last = LateRouteHold(placeholder, _REASON)

    async with _harness_chrome() as (runtime, chrome, pipe):
        runtime.harness.current_model = placeholder
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome, auth_storage=auth, model_registry=registry, late_route=hold)
        await wait_until(lambda: chrome.app.is_running)
        try:
            pipe.send_text("hi\n")
            await wait_until(lambda: any(c.startswith("✖ ") for c in commits))
        finally:
            pipe.send_text("/quit\n")
            await _quit_within(task)
    refusals = [c for c in commits if c.startswith("✖ ")]
    assert refusals == [
        f"✖ {_REASON} No prompt will be sent for it; run /model to select a model."
    ], refusals
    assert runtime.harness.prompts == [], runtime.harness.prompts
