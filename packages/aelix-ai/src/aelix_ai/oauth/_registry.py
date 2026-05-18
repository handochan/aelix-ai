"""OAuth provider registry — Sprint 6c · Phase 4.3 · §G.

Pi parity: ``packages/ai/src/utils/oauth/index.ts:29-95`` (SHA 734e08e).

Mirrors the Pi ``Map<string, OAuthProviderInterface>`` registry with
``register`` / ``unregister`` / ``reset`` / ``get`` helpers. Built-in
providers (Sprint 6c: Anthropic only; Sprint 6e: + Copilot + Codex)
restore on ``unregister`` rather than vanish — same Pi behavior.

This is a SEPARATE registry from :mod:`aelix_ai.api_registry` (ADR-0045):
OAuth providers are different from API providers — one OAuth provider
(e.g. Copilot) can target multiple APIs.
"""

from __future__ import annotations

from typing import Final

from aelix_ai.oauth.anthropic import ANTHROPIC_OAUTH_PROVIDER
from aelix_ai.oauth.types import OAuthProvider

# Sprint 6c: Anthropic only. Sprint 6e adds Copilot + Codex (deferred
# per spec §J / ADR-0053).
_BUILT_IN_OAUTH_PROVIDERS: Final[list[OAuthProvider]] = [
    ANTHROPIC_OAUTH_PROVIDER,
]

_oauth_registry: dict[str, OAuthProvider] = {
    p.id: p for p in _BUILT_IN_OAUTH_PROVIDERS
}


def get_oauth_provider(provider_id: str) -> OAuthProvider | None:
    """Pi parity: ``index.ts:47-49`` ``getOAuthProvider``."""

    return _oauth_registry.get(provider_id)


def register_oauth_provider(provider: OAuthProvider) -> None:
    """Pi parity: ``index.ts:54-56`` ``registerOAuthProvider``."""

    _oauth_registry[provider.id] = provider


def unregister_oauth_provider(provider_id: str) -> None:
    """Pi parity: ``index.ts:64-71`` ``unregisterOAuthProvider``.

    If ``provider_id`` matches a built-in, restore the built-in
    implementation. Custom providers are removed entirely.
    """

    for built_in in _BUILT_IN_OAUTH_PROVIDERS:
        if built_in.id == provider_id:
            _oauth_registry[provider_id] = built_in
            return
    _oauth_registry.pop(provider_id, None)


def reset_oauth_providers() -> None:
    """Pi parity: ``index.ts:76-81`` ``resetOAuthProviders``."""

    _oauth_registry.clear()
    for provider in _BUILT_IN_OAUTH_PROVIDERS:
        _oauth_registry[provider.id] = provider


def get_oauth_providers() -> list[OAuthProvider]:
    """Pi parity: ``index.ts:86-88`` ``getOAuthProviders``."""

    return list(_oauth_registry.values())


# Sprint 6c forward-compat (spec §J binding clause): any future PR that
# lands a Copilot/Codex provider MUST drop it from this set in the same
# PR (enforced by ``tests/pi_parity/test_phase_4_3_strict_superset.py``).
_OAUTH_DEFERRED_PROVIDERS: Final[dict[str, str]] = {
    "github-copilot": "ADR-0053 — Sprint 6e",
    "openai-codex": "ADR-0053 — Sprint 6e",
}

# Sprint 6c forward-compat: Phase 4 features deferred beyond OAuth.
_PHASE_4_DEFERRED_FEATURES: Final[dict[str, str]] = {
    "rpc-mode": "ADR-0054 — Sprint 6d",
    # Sprint 6c W6 (W5 P-95): Pi's auth-storage cascade
    # (runtime-override → env → fallback resolver per
    # ``coding-agent/src/core/auth-storage.ts:455-516``) is owned by
    # ADR-0053 and ships in Sprint 6e alongside Copilot/Codex.
    "auth-storage-layered-resolution": (
        "ADR-0053 — Sprint 6e (runtime-override + env + fallback "
        "resolver per Pi auth-storage.ts:455-516)"
    ),
}


__all__ = [
    "_OAUTH_DEFERRED_PROVIDERS",
    "_PHASE_4_DEFERRED_FEATURES",
    "get_oauth_provider",
    "get_oauth_providers",
    "register_oauth_provider",
    "reset_oauth_providers",
    "unregister_oauth_provider",
]
