"""Issue #23 — the TUI half of first-run onboarding.

``entry.should_offer_first_run_login`` decides; ``run_tui(first_run_login=...)``
performs. This file drives the real ``run_tui`` headlessly (pipe input +
DummyOutput under ``create_app_session``, the ``test_run_tui_smoke`` scaffolding)
and covers the three things that make or break the feature:

  1. it does NOT mount anything when the flag is False (the configured user);
  2. it mounts the wizard when the flag is True, and cancelling leaves a
     DEFINED state — a message and a working REPL, not a hang;
  3. it closes the POST-LOGIN CLIFF: ``run_login`` never calls ``set_model``, so
     without step 3 an onboarded user still sat on the ``api="unknown"`` startup
     model and got "No provider registered for api='unknown'" on their first
     message — #23 would look fixed and would not be.

Headless is NOT the gate for this feature (``chrome.focus()`` swallows
exceptions, so a pre-run focus failure is silent). The live tmux run in the PR
is the gate; these tests pin the wiring and the failure modes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.providers._env_api_keys import ENV_API_KEYS
from aelix_ai.streaming import Model
from aelix_coding_agent.model_registry import ModelRegistry
from aelix_coding_agent.tui import login_wizard
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput


@pytest.fixture(autouse=True)
def _scrub_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry must be genuinely credential-free before onboarding runs."""

    for names in ENV_API_KEYS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)


# === Minimal fakes (same shape as tests/tui/test_run_tui_smoke.py) ==========


class _FakeHooks:
    async def emit(self, event: object) -> None:
        return None


class _FakeExtRuntime:
    def bind_ui(self, ui: object) -> None:
        return None


class FakeHarness:
    def __init__(self) -> None:
        self.bootstrapped = 0
        self.prompts: list[tuple[str, str]] = []
        self.runtime = _FakeExtRuntime()
        self.hooks = _FakeHooks()
        self.session = None
        self.current_model: Model | None = None
        self.set_models: list[Model] = []

    async def bootstrap(self) -> None:
        self.bootstrapped += 1

    def subscribe(self, listener: object):
        return lambda: None

    async def prompt(self, text: str, *, source: str = "interactive", images=None):
        self.prompts.append((text, source))
        return []

    async def reload_resources(self) -> None:
        return None

    async def abort(self) -> None:
        return None

    async def set_model(self, model: Model) -> None:
        self.set_models.append(model)
        self.current_model = model


class FakeRuntime:
    def __init__(self, harness: FakeHarness) -> None:
        self._harness = harness
        self.disposed = 0

    @property
    def harness(self) -> FakeHarness:
        return self._harness

    def set_rebind_session(self, cb) -> None:
        return None

    async def reload(self) -> None:
        return None

    async def dispose(self) -> None:
        self.disposed += 1


async def _wait(predicate, *, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


@asynccontextmanager
async def _harness_chrome() -> AsyncGenerator[
    tuple[FakeRuntime, AelixChrome, PipeInput]
]:
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        yield FakeRuntime(FakeHarness()), AelixChrome(), pipe


def _spy_commits(chrome: AelixChrome) -> list[str]:
    """Record the plain text of every renderable committed to scrollback."""

    commits: list[str] = []
    orig_single = chrome.print_above
    orig_many = chrome.print_above_many

    def _plain(renderable: object) -> str:
        rows = getattr(renderable, "renderables", None)
        if rows is not None:
            return "\n".join(_plain(r) for r in rows).strip("\n")
        text = getattr(renderable, "plain", None)
        return text if isinstance(text, str) else str(renderable)

    async def _capture_single(renderable: object) -> None:
        commits.append(_plain(renderable))
        await orig_single(renderable)

    async def _capture_many(renderables: list[object], **kwargs: object) -> None:
        for r in renderables:
            commits.append(_plain(r))
        await orig_many(renderables, **kwargs)  # type: ignore[arg-type]

    chrome.print_above = _capture_single  # type: ignore[method-assign]
    chrome.print_above_many = _capture_many  # type: ignore[method-assign]
    return commits


async def _auth(tmp_path: Path) -> AuthStorage:
    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    return auth


def _launch(
    runtime: FakeRuntime,
    chrome: AelixChrome,
    **kwargs: Any,
) -> asyncio.Task[int]:
    return asyncio.ensure_future(
        run_tui(
            runtime,  # type: ignore[arg-type]
            cwd=".",
            chrome=chrome,
            install_signal_handlers=False,
            **kwargs,
        )
    )


# === 1. The flag is False → nothing happens =================================


async def test_no_modal_when_the_flag_is_false(tmp_path: Path) -> None:
    """The configured user's launch. Also the default, so every existing caller
    and every existing test keeps its exact behaviour."""

    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome, auth_storage=await _auth(tmp_path))
        await _wait(lambda: chrome.app.is_running)
        await asyncio.sleep(0.25)  # give a stray onboarding task time to mount
        assert not chrome.is_modal_open()
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)

    assert code == 0
    assert not any("No provider credentials found" in c for c in commits), commits


# === 2. The flag is True → the wizard opens, and cancelling is defined ======


async def test_wizard_opens_and_esc_leaves_a_usable_repl(tmp_path: Path) -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(
            runtime,
            chrome,
            auth_storage=await _auth(tmp_path),
            first_run_login=True,
        )
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: chrome.is_modal_open())
        assert any("No provider credentials found" in c for c in commits), commits

        # Esc at the wizard's first prompt: run_login returns without writing.
        pipe.send_text("\x1b")
        await _wait(lambda: not chrome.is_modal_open())
        await _wait(
            lambda: any("Run /login when you're ready" in c for c in commits)
        )

        # ...and the REPL is live, not hung.
        pipe.send_text("still here\n")
        await _wait(
            lambda: runtime.harness.prompts == [("still here", "interactive")]
        )
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)

    assert code == 0
    assert runtime.disposed == 1


async def test_onboarding_runs_exactly_once(tmp_path: Path) -> None:
    """It is a straight-line await before the input loop, so nothing re-arms it.

    /new rebuilds the session in place; it must not re-open the wizard.
    """

    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(
            runtime,
            chrome,
            auth_storage=await _auth(tmp_path),
            first_run_login=True,
        )
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: chrome.is_modal_open())
        pipe.send_text("\x1b")
        await _wait(lambda: not chrome.is_modal_open())

        pipe.send_text("/new\n")
        await asyncio.sleep(0.4)
        assert not chrome.is_modal_open()
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    assert sum("No provider credentials found" in c for c in commits) == 1, commits


# === 3. Failure isolation ===================================================


async def test_a_raising_login_never_keeps_the_user_out_of_the_repl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("wizard exploded")

    monkeypatch.setattr(login_wizard, "run_login", _boom)

    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(
            runtime,
            chrome,
            auth_storage=await _auth(tmp_path),
            first_run_login=True,
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("still here\n")
        await _wait(
            lambda: runtime.harness.prompts == [("still here", "interactive")]
        )
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)

    assert code == 0


# === 4. The post-login cliff ================================================


async def test_a_successful_login_selects_a_runnable_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE test for #23's real failure mode.

    ``run_login`` stores a credential and stops. Without the follow-through the
    harness stays on its unusable startup model and the very first message dies
    with "No provider registered for api='unknown'". Assert that onboarding
    reloads the registry and hands the harness a runnable model.
    """

    auth = await _auth(tmp_path)
    registry = ModelRegistry(auth, None)
    assert registry.get_available() == [], "must start credential-free"

    async def _fake_login(**kwargs: Any) -> None:
        # Exactly what the API-key sub-flow does on success.
        await kwargs["auth_storage"].set_api_key("anthropic", "sk-test")

    monkeypatch.setattr(login_wizard, "run_login", _fake_login)

    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(
            runtime,
            chrome,
            auth_storage=auth,
            model_registry=registry,
            first_run_login=True,
        )
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(runtime.harness.set_models))
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)

    assert code == 0
    chosen = runtime.harness.set_models[-1]
    assert chosen.provider == "anthropic", chosen
    from aelix_coding_agent.core.runnable_models import is_runnable

    assert is_runnable(chosen), f"onboarding must not hand over a dead model: {chosen}"


async def test_a_cancelled_login_selects_nothing_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Esc through the wizard writes nothing, so no model may be forced on the
    user — and the message must name the cure."""

    auth = await _auth(tmp_path)
    registry = ModelRegistry(auth, None)

    async def _cancelled_login(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(login_wizard, "run_login", _cancelled_login)

    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(
            runtime,
            chrome,
            auth_storage=auth,
            model_registry=registry,
            first_run_login=True,
        )
        await _wait(lambda: chrome.app.is_running)
        await _wait(
            lambda: any("Run /login when you're ready" in c for c in commits)
        )
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)

    assert code == 0
    assert runtime.harness.set_models == []
