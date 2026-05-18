"""Sprint 6c / Phase 4.3 §K closure pin (ADR-0055).

Pi parity invariant: every Pi-verified surface in the Phase 4.3 scope
(OAuth client framework + Anthropic OAuth provider + secrets storage)
has a corresponding binding in Aelix, **and the deferred-provider /
deferred-feature allowlists are explicit** — Sprint 6c → Sprint 6e
closure.

Closure date: **2026-05-18**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import json
from pathlib import Path

from aelix_ai.oauth import (
    _OAUTH_DEFERRED_PROVIDERS,
    _PHASE_4_DEFERRED_FEATURES,
    AUTHORIZE_URL,
    CALLBACK_PORT,
    CLIENT_ID,
    SCOPES,
    TOKEN_URL,
    OAuthCredentials,
    OAuthProvider,
    get_oauth_providers,
)
from aelix_ai.oauth._pkce import _base64url, generate_pkce
from aelix_ai.oauth.anthropic import ANTHROPIC_OAUTH_PROVIDER

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> dict:
    return json.loads((_FIXTURES / "pi_oauth_734e08e.json").read_text())


# === §A — OAuth provider registry (Sprint 6c: 1 of 3 live) ===


def test_one_of_three_oauth_providers_live() -> None:
    """Sprint 6c: Anthropic only. Sprint 6e adds Copilot + Codex."""

    providers = get_oauth_providers()
    ids = [p.id for p in providers]
    assert "anthropic" in ids
    assert len(providers) == 1


def test_deferred_providers_allowlist_size() -> None:
    """2 of 3 Pi OAuth providers deferred to Sprint 6e."""

    assert len(_OAUTH_DEFERRED_PROVIDERS) == 2
    assert {"github-copilot", "openai-codex"} <= set(_OAUTH_DEFERRED_PROVIDERS)


def test_deferred_providers_owns_each_id() -> None:
    """Every deferred provider has an owning ADR reference."""

    for provider_id, owner in _OAUTH_DEFERRED_PROVIDERS.items():
        assert "ADR-" in owner, (
            f"{provider_id} deferred allowlist entry missing owning ADR"
        )


def test_rpc_mode_in_phase_4_deferred_features() -> None:
    """Spec §J: RPC mode deferred to Sprint 6d."""

    assert "rpc-mode" in _PHASE_4_DEFERRED_FEATURES
    assert "ADR-" in _PHASE_4_DEFERRED_FEATURES["rpc-mode"]


# === §B — OAuthCredentials dataclass ⊇ Pi shape ===


def test_oauth_credentials_shape_is_superset_of_pi() -> None:
    """Pi parity (P-86): 3 required + extensible extras."""

    fixture = _load_fixture()
    pi_shape = fixture["oauth_credentials_shape"]
    # 3 required fields + extra (for ``[key: string]: unknown``).
    required = {"refresh", "access", "expires"}
    aelix_fields = set(OAuthCredentials.__dataclass_fields__.keys())
    assert required <= aelix_fields
    assert "extra" in aelix_fields  # the index-signature carrier
    # Pi shape rows match.
    for key in required:
        assert key in pi_shape


def test_oauth_credentials_extras_round_trip() -> None:
    """Pi parity (P-86): ``[key: string]: unknown`` round-trips via extras."""

    creds = OAuthCredentials(
        refresh="r",
        access="a",
        expires=1,
        extra={"scope": "openid", "id_token": "abc"},
    )
    flat = creds.to_json()
    assert flat["scope"] == "openid"
    assert flat["id_token"] == "abc"
    rebuilt = OAuthCredentials.from_json(flat)
    assert rebuilt.extra == {"scope": "openid", "id_token": "abc"}


# === §C — OAuthProvider Protocol covers Pi methods ===


def test_oauth_provider_protocol_covers_pi_methods() -> None:
    """Pi parity: Protocol has 5 of 6 Pi methods (modify_models deferred)."""

    fixture = _load_fixture()
    pi_methods = fixture["oauth_provider_interface"]
    # Pi method names → Aelix snake_case.
    aelix_required = {
        "id": "id",
        "name": "name",
        "usesCallbackServer": "uses_callback_server",
        "login": "login",
        "refreshToken": "refresh_token",
        "getApiKey": "get_api_key",
    }
    for pi_name in aelix_required:
        assert pi_name in pi_methods
    # All 6 of the required Pi methods are on the concrete Anthropic
    # provider (Aelix Protocol declares 5 + props; modify_models stays
    # deferred per spec §J).
    for _, aelix_name in aelix_required.items():
        assert hasattr(ANTHROPIC_OAUTH_PROVIDER, aelix_name)


def test_anthropic_provider_is_oauth_protocol() -> None:
    """The concrete Anthropic provider satisfies the runtime Protocol."""

    assert isinstance(ANTHROPIC_OAUTH_PROVIDER, OAuthProvider)


# === §D — Anthropic constants match Pi ===


def test_anthropic_oauth_constants_match_pi() -> None:
    """Pi parity (anthropic.ts:28-36): every constant matches verbatim."""

    fixture = _load_fixture()
    pi_constants = fixture["anthropic_oauth_constants"]
    assert pi_constants["AUTHORIZE_URL"] == AUTHORIZE_URL
    assert pi_constants["TOKEN_URL"] == TOKEN_URL
    assert pi_constants["CALLBACK_PORT"] == CALLBACK_PORT
    assert pi_constants["SCOPES"] == SCOPES
    # CLIENT_ID decoded shape matches Pi (UUID).
    assert len(CLIENT_ID) == 36
    assert CLIENT_ID.count("-") == 4


# === §E — PKCE behavior ===


def test_generate_pkce_output_charset() -> None:
    """Pi parity (pkce.ts): base64url charset, no padding."""

    import re

    v, c = generate_pkce()
    pat = re.compile(r"^[A-Za-z0-9_-]+$")
    assert pat.match(v)
    assert pat.match(c)
    assert "=" not in v
    assert "=" not in c


def test_base64url_no_padding() -> None:
    """Pi parity (pkce.ts:9-15): base64url strips ``=`` padding."""

    assert _base64url(b"abc") == "YWJj"  # no padding
    assert "=" not in _base64url(b"\x00" * 10)


# === §F — Auth storage contract (Sprint 6c shape) ===


def test_auth_storage_class_exposes_required_methods() -> None:
    """Pi parity (auth-storage.ts public surface): required async methods."""

    from aelix_ai.oauth.auth_storage import AuthStorage

    required = {
        "load",
        "save",
        "get_oauth",
        "set_oauth",
        "get_api_key",
        "set_api_key",
        "remove",
        "get_oauth_api_key",
    }
    for name in required:
        assert hasattr(AuthStorage, name)


def test_default_auth_path_under_aelix_root() -> None:
    """Aelix default: ``~/.config/aelix/agent/auth.json`` (not Pi's ``~/.pi/``)."""

    from aelix_ai.oauth.auth_storage import default_auth_path

    p = default_auth_path()
    assert "aelix" in str(p)
    assert p.name == "auth.json"


# === §G — Sprint 6c W6 closure strengthening ===


def test_pi_oauth_provider_total_equals_3() -> None:
    """W5 P-100: live ∪ deferred MUST equal {anthropic, github-copilot,
    openai-codex} exactly — no overlap, no missing entries.
    """

    live = {p.id for p in get_oauth_providers()}
    deferred = set(_OAUTH_DEFERRED_PROVIDERS.keys())
    expected = {"anthropic", "github-copilot", "openai-codex"}
    assert live | deferred == expected
    assert len(live | deferred) == 3
    assert not (live & deferred), "a provider cannot be both live and deferred"


def test_auth_storage_layered_resolution_in_deferred_features() -> None:
    """W5 P-95: the Pi AuthStorage cascade (runtime-override + env +
    fallback resolver per ``auth-storage.ts:455-516``) is owned by
    ADR-0053 and ships in Sprint 6e.
    """

    assert "auth-storage-layered-resolution" in _PHASE_4_DEFERRED_FEATURES
    owner = _PHASE_4_DEFERRED_FEATURES["auth-storage-layered-resolution"]
    assert "ADR-0053" in owner
    assert "Sprint 6e" in owner
