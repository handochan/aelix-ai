"""Unit tests for the /login + /logout auth wizard (Sprint WP-8, Feature 1).

Drives :func:`run_login` / :func:`run_logout` with a :class:`FakeAuthStorage`
that records ``set_api_key`` / ``logout`` / ``login`` plus scripted fake dialog
callables. Covers every login method (OAuth callback wiring, built-in API key,
custom provider) and the degrade paths (cancel / empty key / unknown provider /
empty logout / persistence failure) — all without prompt-toolkit.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_ai.oauth.types import (
    AuthSource,
    AuthStatus,
    OAuthAuthInfo,
    OAuthLoginCallbacks,
    OAuthPrompt,
    OAuthSelectOption,
    OAuthSelectPrompt,
)
from aelix_ai.streaming import Model
from aelix_coding_agent.tui.login_wizard import run_login, run_logout

# ── Test doubles ────────────────────────────────────────────────────────────


class FakeAuthStorage:
    """Records auth mutations; OAuth ``login`` runs the callbacks it is given."""

    def __init__(
        self,
        *,
        stored: list[str] | None = None,
        login_raises: BaseException | None = None,
        set_key_raises: BaseException | None = None,
        logout_raises: BaseException | None = None,
        status_source: AuthSource | None = "stored",
    ) -> None:
        self._stored = list(stored or [])
        self.set_api_key_calls: list[tuple[str, str]] = []
        self.logout_calls: list[str] = []
        self.login_calls: list[tuple[str, OAuthLoginCallbacks]] = []
        self.loaded = False
        self._login_raises = login_raises
        self._set_key_raises = set_key_raises
        self._logout_raises = logout_raises
        self._status_source = status_source

    async def load(self) -> None:
        self.loaded = True

    def list(self) -> list[str]:
        return list(self._stored)

    async def set_api_key(self, provider: str, key: str) -> None:
        if self._set_key_raises is not None:
            raise self._set_key_raises
        self.set_api_key_calls.append((provider, key))
        if provider not in self._stored:
            self._stored.append(provider)

    async def login(self, provider_id: str, callbacks: OAuthLoginCallbacks) -> None:
        self.login_calls.append((provider_id, callbacks))
        if self._login_raises is not None:
            raise self._login_raises
        if provider_id not in self._stored:
            self._stored.append(provider_id)

    async def logout(self, provider: str) -> None:
        if self._logout_raises is not None:
            raise self._logout_raises
        self.logout_calls.append(provider)
        if provider in self._stored:
            self._stored.remove(provider)

    async def get_auth_status(self, provider: str) -> AuthStatus:
        return AuthStatus(configured=True, source=self._status_source)


def _plain(renderable: object) -> str:
    # Panels expose .renderable.plain; Text exposes .plain; fall back to str.
    inner = getattr(renderable, "renderable", None)
    if inner is not None:
        return getattr(inner, "plain", str(inner))
    return getattr(renderable, "plain", str(renderable))


def _style(renderable: object) -> str:
    return str(getattr(renderable, "style", "") or "")


class _ScriptedSelect:
    """A fake ``select`` that returns queued answers (None = Esc)."""

    def __init__(self, answers: list[str | None]) -> None:
        self._answers = list(answers)
        self.titles: list[str] = []
        self.options_seen: list[list[str]] = []

    async def __call__(
        self, title: str, options: list[str], *_a: Any, **_k: Any
    ) -> str | None:
        self.titles.append(title)
        self.options_seen.append(list(options))
        if not self._answers:
            raise AssertionError(f"select called more times than scripted ({title!r})")
        return self._answers.pop(0)


class _ScriptedInput:
    """A fake ``prompt_input`` returning queued answers (None = Esc)."""

    def __init__(self, answers: list[str | None]) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    async def __call__(self, message: str, *_a: Any, **_k: Any) -> str | None:
        self.prompts.append(message)
        if not self._answers:
            raise AssertionError(f"input called more times than scripted ({message!r})")
        return self._answers.pop(0)


async def _confirm_yes(*_a: Any, **_k: Any) -> bool:
    return True


async def _confirm_no(*_a: Any, **_k: Any) -> bool:
    return False


def _notify_sink(messages: list[tuple[str, str]]):
    def _notify(message: str, kind: str = "info") -> None:
        messages.append((message, kind))

    return _notify


async def _select_unreachable(*_a: Any, **_k: Any) -> str | None:
    raise AssertionError("select must not be called on this path")


async def _input_unreachable(*_a: Any, **_k: Any) -> str | None:
    raise AssertionError("input must not be called on this path")


# ── run_login: method selection ──────────────────────────────────────────────


async def test_login_cancel_at_method_select_does_nothing() -> None:
    storage = FakeAuthStorage()
    committed: list[object] = []
    await run_login(
        auth_storage=storage,
        select=_ScriptedSelect([None]),  # Esc at method choice
        prompt_input=_input_unreachable,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert storage.set_api_key_calls == []
    assert storage.login_calls == []
    assert committed == []


async def test_login_no_auth_storage_degrades() -> None:
    committed: list[object] = []
    await run_login(
        auth_storage=None,
        select=_select_unreachable,
        prompt_input=_input_unreachable,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert any("unavailable" in _plain(c) for c in committed)
    assert any("red" in _style(c) for c in committed)


# ── run_login: API-key path ───────────────────────────────────────────────────


async def test_login_api_key_stores_key_and_confirms() -> None:
    storage = FakeAuthStorage()
    committed: list[object] = []
    select = _ScriptedSelect([
        "Using an API key (built-in provider)",  # method
        "openai",  # provider
    ])
    api_input = _ScriptedInput(["sk-secret-123"])

    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=api_input,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert storage.set_api_key_calls == [("openai", "sk-secret-123")]
    assert any("API key stored for openai" in _plain(c) for c in committed)
    # The auth-status confirmation line is committed (best-effort).
    assert any("auth source: stored" in _plain(c) for c in committed)
    # The provider list is the sorted ENV_API_KEYS keys (offered to select).
    provider_options = select.options_seen[1]
    assert "openai" in provider_options
    assert provider_options == sorted(provider_options)


async def test_login_api_key_trims_whitespace() -> None:
    storage = FakeAuthStorage()
    select = _ScriptedSelect([
        "Using an API key (built-in provider)",
        "anthropic",
    ])
    api_input = _ScriptedInput(["   sk-trim-me   "])
    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=api_input,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=lambda _c: None,
    )
    assert storage.set_api_key_calls == [("anthropic", "sk-trim-me")]


async def test_login_api_key_empty_does_not_store() -> None:
    storage = FakeAuthStorage()
    committed: list[object] = []
    select = _ScriptedSelect([
        "Using an API key (built-in provider)",
        "openai",
    ])
    api_input = _ScriptedInput(["   "])  # whitespace only
    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=api_input,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert storage.set_api_key_calls == []
    assert any("no API key entered" in _plain(c) for c in committed)


async def test_login_api_key_cancel_provider_does_nothing() -> None:
    storage = FakeAuthStorage()
    committed: list[object] = []
    select = _ScriptedSelect([
        "Using an API key (built-in provider)",
        None,  # Esc at provider choice
    ])
    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=_input_unreachable,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert storage.set_api_key_calls == []
    assert committed == []


async def test_login_api_key_persistence_failure_degrades() -> None:
    storage = FakeAuthStorage(set_key_raises=RuntimeError("disk full"))
    committed: list[object] = []
    select = _ScriptedSelect([
        "Using an API key (built-in provider)",
        "openai",
    ])
    api_input = _ScriptedInput(["sk-x"])
    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=api_input,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert any("failed to store key" in _plain(c) for c in committed)
    assert any("disk full" in _plain(c) for c in committed)


# ── run_login: OAuth path + callback wiring ───────────────────────────────────


async def test_login_oauth_calls_login_with_callbacks_bundle() -> None:
    storage = FakeAuthStorage()
    committed: list[object] = []
    # The OAuth provider list (anthropic / github-copilot / openai-codex) is the
    # second select; pick the first offered label.
    captured: dict[str, Any] = {}

    async def select(title: str, options: list[str], *_a: Any, **_k: Any) -> str | None:
        if title == "Add a provider":
            return "Using OAuth (sign in to a subscription / account)"
        captured["oauth_options"] = list(options)
        return options[0]  # first OAuth provider name

    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=_ScriptedInput([]),
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    # login was called with the chosen provider id + a real callbacks bundle.
    assert len(storage.login_calls) == 1
    provider_id, callbacks = storage.login_calls[0]
    assert provider_id  # a non-empty provider id
    assert isinstance(callbacks, OAuthLoginCallbacks)
    # The callbacks are all wired (none of the optional ones left None).
    assert callbacks.on_auth is not None
    assert callbacks.on_prompt is not None
    assert callbacks.on_progress is not None
    assert callbacks.on_manual_code_input is not None
    assert callbacks.on_select is not None
    # Success line committed.
    assert any("signed in to" in _plain(c) for c in committed)
    # The OAuth provider names were offered (anthropic among the built-ins).
    assert any("nthropic" in o or "opilot" in o or "odex" in o
               for o in captured["oauth_options"])


async def test_login_oauth_callbacks_route_to_dialogs(monkeypatch: Any) -> None:
    # Drive the wired callbacks directly to prove on_auth/on_prompt/on_select/
    # on_progress/on_manual_code_input route to the injected dialog callables.
    # ``on_auth`` best-effort launches a browser; in a headless/CI box the
    # platform launcher (e.g. the VS Code ``$BROWSER`` helper) can BLOCK rather
    # than raise, which would hang the suite. Stub it so the callback returns.
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *_a, **_k: True)
    storage = FakeAuthStorage()
    committed: list[object] = []
    notes: list[tuple[str, str]] = []
    select_answers: dict[str, Any] = {}

    async def select(title: str, options: list[str], *_a: Any, **_k: Any) -> str | None:
        if title == "Add a provider":
            return "Using OAuth (sign in to a subscription / account)"
        if title == "Sign in with":
            return options[0]
        # An on_select prompt from inside the login flow.
        select_answers["sub_title"] = title
        select_answers["sub_options"] = list(options)
        return options[0]
    prompt_input = _ScriptedInput(["typed-answer", "manual-code"])

    async def fake_login(provider_id: str, callbacks: OAuthLoginCallbacks) -> None:
        storage.login_calls.append((provider_id, callbacks))
        # Narrow the Optional callbacks before invoking (the wizard wires all of
        # them, but the type is Optional on the bundle) — mirrors the narrowing
        # the other OAuth test performs via its ``is not None`` assertions.
        assert callbacks.on_auth is not None
        assert callbacks.on_prompt is not None
        assert callbacks.on_manual_code_input is not None
        assert callbacks.on_progress is not None
        assert callbacks.on_select is not None
        # Exercise every callback the way a real provider flow would.
        await _maybe(callbacks.on_auth(OAuthAuthInfo(url="https://example/auth",
                                                     instructions="do the thing")))
        prompt_result = await _maybe(
            callbacks.on_prompt(OAuthPrompt(message="device code?"))
        )
        select_answers["prompt_result"] = prompt_result
        code = await _maybe(callbacks.on_manual_code_input())
        select_answers["manual_result"] = code
        await _maybe(callbacks.on_progress("polling..."))
        sel = await _maybe(
            callbacks.on_select(
                OAuthSelectPrompt(
                    message="which account?",
                    options=[
                        OAuthSelectOption(id="acct-1", label="Account One"),
                        OAuthSelectOption(id="acct-2", label="Account Two"),
                    ],
                )
            )
        )
        select_answers["select_result"] = sel

    storage.login = fake_login  # type: ignore[assignment]

    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=prompt_input,
        confirm=_confirm_yes,
        notify=_notify_sink(notes),
        commit=committed.append,
    )
    # on_auth committed a panel containing the URL + instructions.
    assert any("https://example/auth" in _plain(c) for c in committed)
    assert any("do the thing" in _plain(c) for c in committed)
    # on_prompt + on_manual_code_input routed through prompt_input.
    assert select_answers["prompt_result"] == "typed-answer"
    assert select_answers["manual_result"] == "manual-code"
    # on_progress routed through notify.
    assert ("polling...", "info") in notes
    # on_select returned the OAuthSelectOption.id of the chosen label.
    assert select_answers["select_result"] == "acct-1"
    assert select_answers["sub_options"] == ["Account One", "Account Two"]


async def _maybe(value: Any) -> Any:
    """Await ``value`` if it is awaitable (callbacks are sync-or-async)."""
    if hasattr(value, "__await__"):
        return await value
    return value


async def test_login_oauth_cancel_provider_does_not_login() -> None:
    storage = FakeAuthStorage()
    committed: list[object] = []

    async def select(title: str, options: list[str], *_a: Any, **_k: Any) -> str | None:
        if title == "Add a provider":
            return "Using OAuth (sign in to a subscription / account)"
        return None  # Esc at provider choice

    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=_input_unreachable,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert storage.login_calls == []


async def test_login_oauth_unknown_provider_runtimeerror_degrades() -> None:
    storage = FakeAuthStorage(login_raises=RuntimeError("Unknown OAuth provider: x"))
    committed: list[object] = []

    async def select(title: str, options: list[str], *_a: Any, **_k: Any) -> str | None:
        if title == "Add a provider":
            return "Using OAuth (sign in to a subscription / account)"
        return options[0]

    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=_ScriptedInput([]),
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert len(storage.login_calls) == 1
    assert any("Unknown OAuth provider" in _plain(c) for c in committed)
    assert not any("signed in to" in _plain(c) for c in committed)


async def test_login_oauth_generic_failure_degrades() -> None:
    storage = FakeAuthStorage(login_raises=ValueError("network down"))
    committed: list[object] = []

    async def select(title: str, options: list[str], *_a: Any, **_k: Any) -> str | None:
        if title == "Add a provider":
            return "Using OAuth (sign in to a subscription / account)"
        return options[0]

    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=_ScriptedInput([]),
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert any("OAuth login failed" in _plain(c) for c in committed)
    assert any("network down" in _plain(c) for c in committed)


# ── run_login: custom provider path ───────────────────────────────────────────


async def test_login_custom_stores_key_with_honest_models_note() -> None:
    storage = FakeAuthStorage()
    committed: list[object] = []
    select = _ScriptedSelect([
        "Custom provider (OpenAI / Anthropic / Gemini-compatible endpoint)",
        "OpenAI-compatible",
    ])
    custom_input = _ScriptedInput([
        "my-endpoint",  # provider id
        "https://host/v1",  # base url
        "sk-custom",  # api key
    ])
    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=custom_input,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert storage.set_api_key_calls == [("my-endpoint", "sk-custom")]
    # The honest note explains the model still needs models.json / --models.
    note = " ".join(_plain(c) for c in committed)
    assert "models.json" in note
    assert "--models" in note
    # Does NOT falsely claim the model is ready/selectable.
    assert "ready to use" not in note.lower()


async def test_login_custom_cancel_protocol_does_nothing() -> None:
    storage = FakeAuthStorage()
    committed: list[object] = []
    select = _ScriptedSelect([
        "Custom provider (OpenAI / Anthropic / Gemini-compatible endpoint)",
        None,  # Esc at protocol choice
    ])
    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=_input_unreachable,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert storage.set_api_key_calls == []
    assert committed == []


async def test_login_custom_empty_provider_id_aborts() -> None:
    storage = FakeAuthStorage()
    committed: list[object] = []
    select = _ScriptedSelect([
        "Custom provider (OpenAI / Anthropic / Gemini-compatible endpoint)",
        "Gemini-compatible",
    ])
    custom_input = _ScriptedInput(["   "])  # empty provider id
    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=custom_input,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert storage.set_api_key_calls == []
    assert any("no provider id" in _plain(c) for c in committed)


async def test_login_custom_empty_key_aborts() -> None:
    storage = FakeAuthStorage()
    committed: list[object] = []
    select = _ScriptedSelect([
        "Custom provider (OpenAI / Anthropic / Gemini-compatible endpoint)",
        "Anthropic-compatible",
    ])
    custom_input = _ScriptedInput(["my-id", "https://h/v1", ""])  # empty key
    await run_login(
        auth_storage=storage,
        select=select,
        prompt_input=custom_input,
        confirm=_confirm_yes,
        notify=_notify_sink([]),
        commit=committed.append,
    )
    assert storage.set_api_key_calls == []
    assert any("no API key entered" in _plain(c) for c in committed)


# ── run_logout ────────────────────────────────────────────────────────────────


async def test_logout_empty_store_reports_none() -> None:
    storage = FakeAuthStorage(stored=[])
    committed: list[object] = []
    await run_logout(
        auth_storage=storage,
        select=_select_unreachable,
        confirm=_confirm_yes,
        commit=committed.append,
    )
    assert storage.loaded is True  # load() was called before list()
    assert storage.logout_calls == []
    assert any("No stored credentials." in _plain(c) for c in committed)


async def test_logout_removes_selected_after_confirm() -> None:
    storage = FakeAuthStorage(stored=["openai", "anthropic"])
    committed: list[object] = []
    select = _ScriptedSelect(["anthropic"])
    await run_logout(
        auth_storage=storage,
        select=select,
        confirm=_confirm_yes,
        commit=committed.append,
    )
    assert storage.logout_calls == ["anthropic"]
    assert any("Removed stored credentials for anthropic" in _plain(c) for c in committed)
    # The picker offered the stored ids, sorted.
    assert select.options_seen[0] == ["anthropic", "openai"]


async def test_logout_warns_when_env_key_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # After removing the stored cred, /logout WARNS when the provider still has
    # an API key in the environment (a source it can't delete) so the user knows
    # why its models stay available.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    storage = FakeAuthStorage(stored=["anthropic"])
    committed: list[object] = []
    await run_logout(
        auth_storage=storage,
        select=_ScriptedSelect(["anthropic"]),
        confirm=_confirm_yes,
        commit=committed.append,
    )
    assert storage.logout_calls == ["anthropic"]
    text = " ".join(_plain(c) for c in committed)
    assert "still has an API key in your environment" in text
    assert "ANTHROPIC_API_KEY" in text


async def test_logout_no_env_key_no_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No surviving env key → clean removal, no env warning.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    storage = FakeAuthStorage(stored=["anthropic"])
    committed: list[object] = []
    await run_logout(
        auth_storage=storage,
        select=_ScriptedSelect(["anthropic"]),
        confirm=_confirm_yes,
        commit=committed.append,
    )
    text = " ".join(_plain(c) for c in committed)
    assert "Removed stored credentials for anthropic" in text
    assert "environment" not in text


async def test_logout_cancel_at_select_does_not_remove() -> None:
    storage = FakeAuthStorage(stored=["openai"])
    committed: list[object] = []
    await run_logout(
        auth_storage=storage,
        select=_ScriptedSelect([None]),  # Esc
        confirm=_confirm_yes,
        commit=committed.append,
    )
    assert storage.logout_calls == []


async def test_logout_declined_confirm_does_not_remove() -> None:
    storage = FakeAuthStorage(stored=["openai"])
    committed: list[object] = []
    await run_logout(
        auth_storage=storage,
        select=_ScriptedSelect(["openai"]),
        confirm=_confirm_no,  # user declines
        commit=committed.append,
    )
    assert storage.logout_calls == []


async def test_logout_removal_failure_degrades() -> None:
    storage = FakeAuthStorage(
        stored=["openai"], logout_raises=RuntimeError("locked")
    )
    committed: list[object] = []
    await run_logout(
        auth_storage=storage,
        select=_ScriptedSelect(["openai"]),
        confirm=_confirm_yes,
        commit=committed.append,
    )
    assert any("failed to remove credentials" in _plain(c) for c in committed)
    assert any("locked" in _plain(c) for c in committed)


async def test_logout_no_auth_storage_degrades() -> None:
    committed: list[object] = []
    await run_logout(
        auth_storage=None,
        select=_select_unreachable,
        confirm=_confirm_yes,
        commit=committed.append,
    )
    assert any("unavailable" in _plain(c) for c in committed)


class _FakeRegistryForLogout:
    """Registry double for the logout cascade: exposes ``_models_json_path``, a
    ``get_all()`` catalog (consulted by the registry-aware allow-list prune), and
    records ``_load_models()`` calls (the reload that flips has_configured_auth)."""

    def __init__(self, path: str, models: list[Model] | None = None) -> None:
        self._models_json_path = path
        self._models = list(models or [])
        self.load_models_calls = 0

    def _load_models(self) -> None:
        self.load_models_calls += 1

    def get_all(self) -> list[Model]:
        return list(self._models)


async def test_logout_cascades_to_models_json_and_settings(tmp_path: Any) -> None:
    # S1: /logout must de-authorize across all three files. A custom provider
    # persisted by /login has its apiKey in models.json (which keeps
    # has_configured_auth True). Logout must STRIP that apiKey (keeping the model
    # defs), reload the registry, and prune the provider's canonical scoped-models
    # entries — while leaving OTHER providers' blocks/secrets and allow-list entries
    # intact.
    import json

    from aelix_ai.settings import SettingsManager

    models_json = tmp_path / "models.json"
    models_json.write_text(
        json.dumps(
            {
                "providers": {
                    "myco": {
                        "name": "myco",
                        "baseUrl": "https://h/v1",
                        "api": "openai-completions",
                        "apiKey": "sk-secret",
                        "models": [{"id": "m1"}],
                    },
                    "other": {
                        "name": "other",
                        "baseUrl": "https://o/v1",
                        "api": "openai-completions",
                        "apiKey": "sk-keep",
                        "models": [{"id": "x"}],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    storage = FakeAuthStorage(stored=["myco"])
    registry = _FakeRegistryForLogout(
        str(models_json),
        models=[
            Model(id="m1", provider="myco"),
            Model(id="x", provider="other"),
            Model(id="gpt-4o", provider="openai"),
        ],
    )
    sm = SettingsManager.in_memory(
        {"enabledModels": ["myco/m1", "other/x", "openai/gpt-4o"]}
    )
    committed: list[object] = []

    await run_logout(
        auth_storage=storage,
        select=_ScriptedSelect(["myco"]),
        confirm=_confirm_yes,
        commit=committed.append,
        model_registry=registry,
        settings_manager=sm,
    )

    # auth.json removal happened.
    assert storage.logout_calls == ["myco"]
    data = json.loads(models_json.read_text(encoding="utf-8"))
    # myco block KEPT (model defs preserved) but its plaintext apiKey stripped.
    assert "myco" in data["providers"]
    assert "apiKey" not in data["providers"]["myco"]
    assert data["providers"]["myco"]["models"] == [{"id": "m1"}]
    # other provider's block + secret untouched.
    assert data["providers"]["other"]["apiKey"] == "sk-keep"
    # Registry reloaded so has_configured_auth reflects the removal in-session.
    assert registry.load_models_calls == 1
    # settings.json scoped allow-list pruned of myco/*; others kept.
    assert sm.get_enabled_models() == ["other/x", "openai/gpt-4o"]
    text = " ".join(_plain(c) for c in committed)
    assert "cleared" in text and "myco" in text


async def test_logout_preserves_hand_authored_override_block_without_apikey(
    tmp_path: Any,
) -> None:
    # A built-in provider logged in via API key stores ONLY in auth.json. Its
    # models.json block (if any) is purely hand-authored (modelOverrides, no apiKey)
    # — logout must NOT touch it (no whole-block deletion, no reload/message).
    import json

    from aelix_ai.settings import SettingsManager

    original = {
        "providers": {
            "openai": {
                "name": "openai",
                "modelOverrides": {"gpt-4o": {"contextWindow": 999}},
            }
        }
    }
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps(original), encoding="utf-8")

    storage = FakeAuthStorage(stored=["openai"])
    registry = _FakeRegistryForLogout(str(models_json))
    committed: list[object] = []

    await run_logout(
        auth_storage=storage,
        select=_ScriptedSelect(["openai"]),
        confirm=_confirm_yes,
        commit=committed.append,
        model_registry=registry,
        settings_manager=SettingsManager.in_memory({}),
    )

    assert storage.logout_calls == ["openai"]
    # Hand-authored block is byte-for-byte preserved; no reload; no "cleared" note.
    assert json.loads(models_json.read_text(encoding="utf-8")) == original
    assert registry.load_models_calls == 0
    assert "cleared" not in " ".join(_plain(c) for c in committed)


async def test_logout_removes_pure_credential_block_entirely(tmp_path: Any) -> None:
    # A block whose ONLY substantive content was the apiKey (no models/baseUrl/etc)
    # is a pure credential holder → stripping the key leaves nothing, so the whole
    # block is dropped rather than left as an invalid empty entry.
    import json

    from aelix_ai.settings import SettingsManager

    models_json = tmp_path / "models.json"
    models_json.write_text(
        json.dumps(
            {"providers": {"acme": {"name": "acme", "api": "openai-completions",
                                     "apiKey": "sk-x"}}}
        ),
        encoding="utf-8",
    )
    storage = FakeAuthStorage(stored=["acme"])
    registry = _FakeRegistryForLogout(str(models_json))
    committed: list[object] = []

    await run_logout(
        auth_storage=storage,
        select=_ScriptedSelect(["acme"]),
        confirm=_confirm_yes,
        commit=committed.append,
        model_registry=registry,
        settings_manager=SettingsManager.in_memory({}),
    )

    data = json.loads(models_json.read_text(encoding="utf-8"))
    assert "acme" not in data["providers"]
    assert registry.load_models_calls == 1


async def test_logout_without_registry_or_settings_touches_only_auth() -> None:
    # Back-compat: omitting model_registry/settings_manager keeps the pre-cascade
    # behavior (auth.json only) and never raises.
    storage = FakeAuthStorage(stored=["openai"])
    committed: list[object] = []
    await run_logout(
        auth_storage=storage,
        select=_ScriptedSelect(["openai"]),
        confirm=_confirm_yes,
        commit=committed.append,
    )
    assert storage.logout_calls == ["openai"]
    assert any("Removed stored credentials for openai" in _plain(c) for c in committed)
