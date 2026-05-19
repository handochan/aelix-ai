"""Sprint 6e · Phase 4.5 closure pin (ADR-0063).

Pi parity invariant: every Pi-verified surface in the Phase 4.5 scope
(Copilot + Codex OAuth providers, AuthStorage layered cascade,
modify_models Protocol wire-up) has a corresponding binding in Aelix.

Closure date: **2026-05-19**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from aelix_ai.oauth import (
    _OAUTH_DEFERRED_PROVIDERS,
    GITHUB_COPILOT_OAUTH_PROVIDER,
    OPENAI_CODEX_OAUTH_PROVIDER,
    AuthSource,
    AuthStatus,
    AuthStorage,
    get_oauth_providers,
)
from aelix_ai.oauth.anthropic import ANTHROPIC_OAUTH_PROVIDER
from aelix_ai.oauth.github_copilot import (
    CLIENT_ID as COPILOT_CLIENT_ID,
)
from aelix_ai.oauth.github_copilot import (
    COPILOT_HEADERS,
    DEFAULT_DOMAIN,
    INITIAL_POLL_INTERVAL_MULTIPLIER,
    SLOW_DOWN_POLL_INTERVAL_MULTIPLIER,
)
from aelix_ai.oauth.openai_codex import (
    CALLBACK_PATH as CODEX_CALLBACK_PATH,
)
from aelix_ai.oauth.openai_codex import (
    CALLBACK_PORT as CODEX_CALLBACK_PORT,
)
from aelix_ai.oauth.openai_codex import (
    CLIENT_ID as CODEX_CLIENT_ID,
)
from aelix_ai.oauth.openai_codex import (
    JWT_CLAIM_PATH,
    SCOPE,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> dict:
    return json.loads(
        (_FIXTURES / "pi_oauth_copilot_codex_734e08e.json").read_text()
    )


# === §A — Full Pi 3-provider built-in set is LIVE ===


def test_three_oauth_providers_live() -> None:
    """Sprint 6e closure: Anthropic + Copilot + Codex all live."""

    providers = get_oauth_providers()
    ids = {p.id for p in providers}
    assert ids == {"anthropic", "github-copilot", "openai-codex"}


def test_deferred_providers_empty() -> None:
    """Sprint 6e: ``_OAUTH_DEFERRED_PROVIDERS`` is fully drained."""

    assert _OAUTH_DEFERRED_PROVIDERS == {}


# === §B — modify_models Protocol attribute ===


def test_anthropic_modify_models_is_none_or_callable() -> None:
    """Anthropic doesn't need modify_models (no base URL injection)."""

    attr = getattr(ANTHROPIC_OAUTH_PROVIDER, "modify_models", None)
    assert attr is None or callable(attr)


def test_copilot_modify_models_is_callable() -> None:
    """Copilot wires modify_models (Pi P-132 first consumer)."""

    assert callable(GITHUB_COPILOT_OAUTH_PROVIDER.modify_models)


def test_codex_modify_models_is_none_or_callable() -> None:
    """Codex doesn't need modify_models (account_id flows via header)."""

    attr = getattr(OPENAI_CODEX_OAUTH_PROVIDER, "modify_models", None)
    assert attr is None or callable(attr)


# === §C — AuthStorage exposes 12 cascade methods ===


def test_auth_storage_exposes_12_cascade_methods() -> None:
    """Pi parity: ``auth-storage.ts:191-516`` 12 cascade methods."""

    required = {
        "set_runtime_api_key",
        "remove_runtime_api_key",
        "set_fallback_resolver",
        "has_auth",
        "get_auth_status",
        "list",
        "has",
        "get_all",
        "drain_errors",
        "login",
        "logout",
        "get_api_key_cascade",
    }
    for name in required:
        assert hasattr(AuthStorage, name), f"AuthStorage missing {name}"


def test_auth_storage_preserves_sprint_6c_methods() -> None:
    """Sprint 6c contract MUST NOT regress."""

    required_6c = {
        "load",
        "save",
        "get_oauth",
        "set_oauth",
        "get_api_key",
        "set_api_key",
        "remove",
        "get_oauth_api_key",
    }
    for name in required_6c:
        assert hasattr(AuthStorage, name), f"Sprint 6c method {name} lost"


# === §D — AuthStatus + AuthSource shape ===


def test_auth_status_source_enum_matches_pi_6_values() -> None:
    """Pi parity: ``auth-storage.ts:38`` enum (6 values)."""

    fixture = _load_fixture()
    pi_enum = set(fixture["auth_status_source_enum"])
    aelix_values = set(get_args(AuthSource))
    assert aelix_values == pi_enum


def test_auth_status_dataclass_shape() -> None:
    """``AuthStatus`` has ``configured`` + optional ``source`` + ``label``."""

    s = AuthStatus(configured=True, source="stored", label="x")
    assert s.configured is True
    assert s.source == "stored"
    assert s.label == "x"


# === §E — Copilot constants match fixture ===


def test_copilot_constants_match_fixture() -> None:
    fixture = _load_fixture()
    copilot = fixture["copilot_oauth"]
    # CLIENT_ID decoded from base64-encoded form in the fixture.
    import base64

    expected_client = base64.b64decode(copilot["client_id_base64"]).decode("ascii")
    assert expected_client == COPILOT_CLIENT_ID
    assert copilot["copilot_headers"] == COPILOT_HEADERS
    assert (
        copilot["poll_intervals"]["INITIAL_MULTIPLIER"]
        == INITIAL_POLL_INTERVAL_MULTIPLIER
    )
    assert (
        copilot["poll_intervals"]["SLOW_DOWN_MULTIPLIER"]
        == SLOW_DOWN_POLL_INTERVAL_MULTIPLIER
    )
    assert copilot["enterprise_domain"]["default_host"] == DEFAULT_DOMAIN


# === §F — Codex constants match fixture ===


def test_codex_constants_match_fixture() -> None:
    fixture = _load_fixture()
    codex = fixture["codex_oauth"]
    assert codex["client_id"] == CODEX_CLIENT_ID
    assert codex["callback_port"] == CODEX_CALLBACK_PORT
    assert codex["callback_path"] == CODEX_CALLBACK_PATH
    assert codex["scope"] == SCOPE
    assert codex["jwt_claim_path"] == JWT_CLAIM_PATH


def test_pi_3_provider_set_total_equals_3() -> None:
    """Sprint 6e closure: live ∪ deferred MUST equal {3 Pi providers}."""

    live = {p.id for p in get_oauth_providers()}
    deferred = set(_OAUTH_DEFERRED_PROVIDERS.keys())
    expected = {"anthropic", "github-copilot", "openai-codex"}
    assert live | deferred == expected
    assert not (live & deferred), "no provider may be both live and deferred"
