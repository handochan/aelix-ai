"""ModelRegistry runtime tests — Sprint 6f W2 (ADR-0065 §H).

Pi parity: ``packages/coding-agent/src/core/model-registry.ts``
(SHA 734e08e). Covers the 14 public methods of
:class:`aelix_coding_agent.model_registry.ModelRegistry`.
"""

from __future__ import annotations

import os
from pathlib import Path

from aelix_ai.oauth import AuthStorage
from aelix_ai.oauth.types import OAuthCredentials
from aelix_ai.streaming import Model
from aelix_coding_agent.model_registry import (
    ModelRegistry,
    ProviderConfigInput,
    ResolvedRequestAuth,
)


def _fresh_auth_storage(tmp_path: Path) -> AuthStorage:
    """Build an :class:`AuthStorage` over an empty auth.json in ``tmp_path``."""

    auth_path = tmp_path / "auth.json"
    return AuthStorage(path=auth_path)


async def _ready_storage(tmp_path: Path) -> AuthStorage:
    s = _fresh_auth_storage(tmp_path)
    await s.load()
    return s


# === Constructor + factories ===================================================


async def test_constructor_loads_models_from_seed_catalog(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry(s)
    assert len(r.get_all()) >= 10  # seed catalog has 10+ models.


async def test_create_factory_matches_constructor(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.create(s)
    assert isinstance(r, ModelRegistry)
    assert len(r.get_all()) >= 10


async def test_in_memory_factory_passes_none_path(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    assert isinstance(r, ModelRegistry)


async def test_constructor_with_nonexistent_models_json_loads_builtins(
    tmp_path: Path,
) -> None:
    """P0 #4 (ADR-0140): a ``models_json_path`` that doesn't exist loads
    built-ins only — the loader returns an empty custom result, no raise.
    (Pre-0140 this raised :class:`NotImplementedError`.)
    """

    s = await _ready_storage(tmp_path)
    r = ModelRegistry(s, models_json_path=str(tmp_path / "nope.json"))
    assert len(r.get_all()) >= 10
    assert r.get_error() is None


async def test_create_factory_with_explicit_path_loads_builtins(
    tmp_path: Path,
) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.create(s, models_json_path=str(tmp_path / "nope.json"))
    assert isinstance(r, ModelRegistry)
    assert len(r.get_all()) >= 10


# === Model access ==============================================================


async def test_get_all_returns_seed_catalog(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    all_models = r.get_all()
    providers = {m.provider for m in all_models}
    assert "anthropic" in providers
    assert "openai" in providers
    assert "openrouter" in providers


async def test_get_all_is_a_copy(tmp_path: Path) -> None:
    """Pi parity: ``getAll`` returns a snapshot — caller mutations don't
    affect the registry's internal list.
    """

    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    first = r.get_all()
    first.clear()  # mutate caller's copy
    assert len(r.get_all()) > 0


async def test_get_available_filters_to_configured_auth(tmp_path: Path) -> None:
    """Pi parity: ``getAvailable`` skips models without configured auth."""

    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    # With an empty AuthStorage and no env vars set, no provider has auth.
    # Clear any leaked env vars from the test environment.
    keys_to_clear = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    saved = {k: os.environ.pop(k, None) for k in keys_to_clear}
    try:
        available = r.get_available()
        assert available == []
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def test_get_available_includes_models_with_stored_api_key(
    tmp_path: Path,
) -> None:
    s = await _ready_storage(tmp_path)
    await s.set_api_key("anthropic", "sk-test")
    r = ModelRegistry.in_memory(s)
    available = r.get_available()
    # Every Anthropic model now passes the auth filter.
    assert any(m.provider == "anthropic" for m in available)


async def test_find_returns_model_when_present(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    m = r.find("anthropic", "claude-sonnet-4-5")
    assert m is not None
    assert m.id == "claude-sonnet-4-5"


async def test_find_returns_none_for_unknown(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    assert r.find("anthropic", "non-existent") is None
    assert r.find("nope", "anything") is None


# === Auth resolution ===========================================================


async def test_has_configured_auth_true_when_stored(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    await s.set_api_key("openai", "sk-test")
    r = ModelRegistry.in_memory(s)
    m = r.find("openai", "gpt-4o")
    assert m is not None
    assert r.has_configured_auth(m) is True


async def test_has_configured_auth_false_when_no_layer(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    m = r.find("openai", "gpt-4o")
    assert m is not None
    keys_to_clear = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    saved = {k: os.environ.pop(k, None) for k in keys_to_clear}
    try:
        assert r.has_configured_auth(m) is False
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def test_has_configured_auth_true_via_runtime_override(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    s.set_runtime_api_key("anthropic", "sk-runtime")
    r = ModelRegistry.in_memory(s)
    m = r.find("anthropic", "claude-sonnet-4-5")
    assert m is not None
    assert r.has_configured_auth(m) is True


async def test_logout_fully_deauthorizes_and_drops_from_available(
    tmp_path: Path,
) -> None:
    # End-to-end regression for the "/logout didn't remove" report: a provider
    # authed via BOTH a stored key AND a runtime override must be fully
    # de-authorized by logout, so its models leave get_available() / the picker.
    s = await _ready_storage(tmp_path)
    await s.set_api_key("anthropic", "sk-stored")
    s.set_runtime_api_key("anthropic", "sk-runtime")
    r = ModelRegistry.in_memory(s)
    m = r.find("anthropic", "claude-sonnet-4-5")
    assert m is not None
    # Clear env keys so the env layer doesn't independently keep it configured
    # (the test runner sources .env, which may hold ANTHROPIC_API_KEY).
    keys = ["ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        assert r.has_configured_auth(m) is True  # authed before logout
        assert m in r.get_available()
        await s.logout("anthropic")
        assert r.has_configured_auth(m) is False  # fully de-authorized
        assert m not in r.get_available()  # models leave the picker
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def test_get_api_key_and_headers_returns_ok_true(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    await s.set_api_key("anthropic", "sk-test-123")
    r = ModelRegistry.in_memory(s)
    m = r.find("anthropic", "claude-sonnet-4-5")
    assert m is not None
    resolved = await r.get_api_key_and_headers(m)
    assert isinstance(resolved, ResolvedRequestAuth)
    assert resolved.ok is True
    assert resolved.api_key == "sk-test-123"
    assert resolved.headers == {}


async def test_get_api_key_and_headers_ok_true_with_no_key_when_missing(
    tmp_path: Path,
) -> None:
    """P0 #4 (ADR-0140): pi-faithful — a provider with NO resolvable key
    returns ``ok=True`` with ``api_key=None`` (OAuth-only providers attach
    their bearer via ``model.headers``). Pre-0140 this returned ``ok=False``.
    """

    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    m = r.find("anthropic", "claude-sonnet-4-5")
    assert m is not None
    keys_to_clear = ["ANTHROPIC_API_KEY"]
    saved = {k: os.environ.pop(k, None) for k in keys_to_clear}
    try:
        resolved = await r.get_api_key_and_headers(m)
        assert resolved.ok is True
        assert resolved.api_key is None
        assert resolved.headers == {}
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def test_get_api_key_for_provider_returns_stored(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    await s.set_api_key("openai", "sk-openai")
    r = ModelRegistry.in_memory(s)
    key = await r.get_api_key_for_provider("openai")
    assert key == "sk-openai"


async def test_get_provider_auth_status_reports_stored(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    await s.set_api_key("anthropic", "sk-test")
    r = ModelRegistry.in_memory(s)
    status = await r.get_provider_auth_status("anthropic")
    assert status.configured is True
    assert status.source == "stored"


async def test_is_using_oauth_returns_true_for_oauth_entry(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    creds = OAuthCredentials(refresh="r", access="a", expires=2**31)
    await s.set_oauth("anthropic", creds)
    r = ModelRegistry.in_memory(s)
    m = r.find("anthropic", "claude-sonnet-4-5")
    assert m is not None
    assert r.is_using_oauth(m) is True


async def test_is_using_oauth_false_for_api_key_entry(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    await s.set_api_key("anthropic", "sk-test")
    r = ModelRegistry.in_memory(s)
    m = r.find("anthropic", "claude-sonnet-4-5")
    assert m is not None
    assert r.is_using_oauth(m) is False


async def test_is_using_oauth_false_for_unknown_oauth_provider(
    tmp_path: Path,
) -> None:
    """openai has no OAuth provider in the registry → False."""

    s = await _ready_storage(tmp_path)
    await s.set_api_key("openai", "sk-test")
    r = ModelRegistry.in_memory(s)
    m = r.find("openai", "gpt-4o")
    assert m is not None
    assert r.is_using_oauth(m) is False


# === Lifecycle =================================================================


async def test_refresh_rebuilds_models(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    first = r.get_all()
    r.refresh()
    second = r.get_all()
    assert len(first) == len(second)
    # Same provider/id pairs.
    assert {(m.provider, m.id) for m in first} == {
        (m.provider, m.id) for m in second
    }


async def test_get_error_returns_none_initially(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    assert r.get_error() is None


# === Dynamic registration ======================================================


async def test_register_provider_stores_config(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    config = ProviderConfigInput(
        api_key="sk-custom",
        headers={"X-Custom": "1"},
    )
    r.register_provider("custom-provider", config)
    assert "custom-provider" in r._registered_providers


async def test_unregister_provider_removes_config(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    r.register_provider("custom-provider", ProviderConfigInput(api_key="x"))
    r.unregister_provider("custom-provider")
    assert "custom-provider" not in r._registered_providers


async def test_register_provider_makes_provider_satisfy_has_configured_auth(
    tmp_path: Path,
) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    r.register_provider(
        "custom-provider", ProviderConfigInput(api_key="sk-custom")
    )
    fake_model = Model(id="x", provider="custom-provider")
    assert r.has_configured_auth(fake_model) is True


# === Display ===================================================================


async def test_get_provider_display_name_for_builtin(tmp_path: Path) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    assert r.get_provider_display_name("anthropic") == "Anthropic"
    assert r.get_provider_display_name("openai") == "OpenAI"
    assert r.get_provider_display_name("openrouter") == "OpenRouter"


async def test_get_provider_display_name_fallback_titlecase(
    tmp_path: Path,
) -> None:
    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    assert r.get_provider_display_name("my-custom") == "My-Custom"


# === Public method count guardrail ============================================


def test_model_registry_exposes_14_public_methods() -> None:
    """Pi parity (P-165): ModelRegistry has 14 Pi public methods.

    Closure pin: every method in the binding-spec §E roster is present.
    """

    expected = {
        # Factories.
        "create",
        "in_memory",
        # Model access.
        "get_all",
        "get_available",
        "find",
        # Auth resolution.
        "has_configured_auth",
        "get_api_key_and_headers",
        "get_api_key_for_provider",
        "get_provider_auth_status",
        "is_using_oauth",
        # Lifecycle.
        "refresh",
        "get_error",
        # Dynamic registration.
        "register_provider",
        "unregister_provider",
        # Display.
        "get_provider_display_name",
    }
    actual = {
        name
        for name in dir(ModelRegistry)
        if not name.startswith("_") and callable(getattr(ModelRegistry, name))
    }
    missing = expected - actual
    assert not missing, f"Missing methods: {missing}"
