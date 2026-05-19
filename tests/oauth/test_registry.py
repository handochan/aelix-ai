"""Sprint 6c · Phase 4.3 — OAuth registry tests."""

from __future__ import annotations

import pytest
from aelix_ai.oauth._registry import (
    _OAUTH_DEFERRED_PROVIDERS,
    _PHASE_4_DEFERRED_FEATURES,
    get_oauth_provider,
    get_oauth_providers,
    register_oauth_provider,
    reset_oauth_providers,
    unregister_oauth_provider,
)
from aelix_ai.oauth.anthropic import ANTHROPIC_OAUTH_PROVIDER
from aelix_ai.oauth.types import (
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthProvider,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_oauth_providers()
    yield
    reset_oauth_providers()


def test_anthropic_is_built_in() -> None:
    """Sprint 6e: Anthropic + Copilot + Codex all built-in."""

    p = get_oauth_provider("anthropic")
    assert p is ANTHROPIC_OAUTH_PROVIDER


def test_get_oauth_providers_returns_all() -> None:
    """Sprint 6e: 3 built-in providers (Anthropic + Copilot + Codex)."""

    providers = get_oauth_providers()
    ids = {p.id for p in providers}
    assert ids == {"anthropic", "github-copilot", "openai-codex"}
    assert len(providers) == 3


def test_get_oauth_provider_unknown_returns_none() -> None:
    assert get_oauth_provider("nonexistent") is None


def test_register_custom_provider() -> None:
    """Registering a custom provider makes it findable."""

    class _Custom:
        id = "custom"
        name = "Custom OAuth"
        uses_callback_server = False

        async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
            return OAuthCredentials(refresh="r", access="a", expires=1)

        async def refresh_token(
            self, credentials: OAuthCredentials
        ) -> OAuthCredentials:
            return credentials

        def get_api_key(self, credentials: OAuthCredentials) -> str:
            return credentials.access

    custom = _Custom()
    assert isinstance(custom, OAuthProvider)
    register_oauth_provider(custom)
    assert get_oauth_provider("custom") is custom
    # Sprint 6e: 3 built-ins + 1 custom = 4.
    assert len(get_oauth_providers()) == 4


def test_unregister_custom_removes_completely() -> None:
    """A custom provider unregisters fully (Pi parity index.ts:64-71)."""

    class _Custom:
        id = "custom"
        name = "Custom"
        uses_callback_server = False

        async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
            return OAuthCredentials(refresh="r", access="a", expires=1)

        async def refresh_token(
            self, credentials: OAuthCredentials
        ) -> OAuthCredentials:
            return credentials

        def get_api_key(self, credentials: OAuthCredentials) -> str:
            return credentials.access

    register_oauth_provider(_Custom())
    assert get_oauth_provider("custom") is not None
    unregister_oauth_provider("custom")
    assert get_oauth_provider("custom") is None


def test_unregister_built_in_restores_built_in() -> None:
    """Pi parity: unregistering a built-in restores the original."""

    class _Replacement:
        id = "anthropic"
        name = "Fake Anthropic"
        uses_callback_server = False

        async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
            return OAuthCredentials(refresh="r", access="a", expires=1)

        async def refresh_token(
            self, credentials: OAuthCredentials
        ) -> OAuthCredentials:
            return credentials

        def get_api_key(self, credentials: OAuthCredentials) -> str:
            return credentials.access

    register_oauth_provider(_Replacement())
    assert get_oauth_provider("anthropic").name == "Fake Anthropic"
    unregister_oauth_provider("anthropic")
    assert get_oauth_provider("anthropic") is ANTHROPIC_OAUTH_PROVIDER


def test_reset_oauth_providers_restores_state() -> None:
    """``reset_oauth_providers`` purges customs + restores built-ins."""

    class _Custom:
        id = "custom"
        name = "Custom"
        uses_callback_server = False

        async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
            return OAuthCredentials(refresh="r", access="a", expires=1)

        async def refresh_token(
            self, credentials: OAuthCredentials
        ) -> OAuthCredentials:
            return credentials

        def get_api_key(self, credentials: OAuthCredentials) -> str:
            return credentials.access

    register_oauth_provider(_Custom())
    # Sprint 6e: 3 built-ins + 1 custom = 4.
    assert len(get_oauth_providers()) == 4
    reset_oauth_providers()
    assert len(get_oauth_providers()) == 3
    assert get_oauth_provider("custom") is None


def test_deferred_providers_drained() -> None:
    """Sprint 6e closure: ``_OAUTH_DEFERRED_PROVIDERS`` is now empty."""

    assert _OAUTH_DEFERRED_PROVIDERS == {}


def test_phase_4_deferred_features_set_populated() -> None:
    """Spec §J forward-compat: RPC mode landed in Sprint 6d (closed)."""

    assert "rpc-mode" in _PHASE_4_DEFERRED_FEATURES
    assert "ADR-" in _PHASE_4_DEFERRED_FEATURES["rpc-mode"]
