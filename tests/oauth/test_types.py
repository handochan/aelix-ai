"""Sprint 6c · Phase 4.3 — OAuth types tests."""

from __future__ import annotations

from aelix_ai.oauth.types import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthPrompt,
    OAuthProvider,
    OAuthSelectOption,
    OAuthSelectPrompt,
)


def test_oauth_credentials_round_trip_no_extra() -> None:
    """Plain creds round-trip through ``to_json`` / ``from_json``."""

    creds = OAuthCredentials(refresh="r", access="a", expires=12345)
    d = creds.to_json()
    assert d == {"refresh": "r", "access": "a", "expires": 12345}
    rebuilt = OAuthCredentials.from_json(d)
    assert rebuilt == creds


def test_oauth_credentials_extras_preserved() -> None:
    """Pi parity: ``[key: string]: unknown`` extras survive round-trip."""

    creds = OAuthCredentials(
        refresh="r",
        access="a",
        expires=12345,
        extra={"scope": "openid", "endpoint": "https://example.com"},
    )
    d = creds.to_json()
    # Extras merged flat at top level (Pi shape).
    assert d["scope"] == "openid"
    assert d["endpoint"] == "https://example.com"
    assert d["refresh"] == "r"

    rebuilt = OAuthCredentials.from_json(d)
    assert rebuilt.refresh == "r"
    assert rebuilt.access == "a"
    assert rebuilt.expires == 12345
    assert rebuilt.extra == {"scope": "openid", "endpoint": "https://example.com"}


def test_oauth_credentials_from_json_coerces_expires() -> None:
    """``expires`` must be int even when JSON has a float (Pi number type)."""

    creds = OAuthCredentials.from_json(
        {"refresh": "r", "access": "a", "expires": 12345.0}
    )
    assert creds.expires == 12345
    assert isinstance(creds.expires, int)


def test_oauth_prompt_defaults() -> None:
    p = OAuthPrompt(message="hello")
    assert p.message == "hello"
    assert p.placeholder is None
    assert p.allow_empty is False


def test_oauth_auth_info_defaults() -> None:
    info = OAuthAuthInfo(url="https://example.com")
    assert info.url == "https://example.com"
    assert info.instructions is None


def test_oauth_select_prompt() -> None:
    sp = OAuthSelectPrompt(
        message="pick one",
        options=[OAuthSelectOption(id="a", label="A")],
    )
    assert sp.message == "pick one"
    assert sp.options[0].id == "a"
    assert sp.options[0].label == "A"


def test_oauth_login_callbacks_shape() -> None:
    """``OAuthLoginCallbacks`` accepts the minimal 2-callback shape."""

    def on_auth(_info: OAuthAuthInfo) -> None:
        return None

    def on_prompt(_p: OAuthPrompt) -> str:
        return "code"

    cb = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)
    assert cb.on_auth is on_auth
    assert cb.on_prompt is on_prompt
    assert cb.on_progress is None
    assert cb.on_manual_code_input is None
    assert cb.on_select is None
    assert cb.signal is None


def test_oauth_provider_protocol_is_runtime_checkable() -> None:
    """Pi parity: ``OAuthProviderInterface`` has 5 required + 1 optional method."""

    from aelix_ai.oauth.anthropic import ANTHROPIC_OAUTH_PROVIDER

    assert isinstance(ANTHROPIC_OAUTH_PROVIDER, OAuthProvider)


# === W4 m1 — clear error on missing required fields ===


def test_oauth_credentials_from_json_missing_required_raises_value_error() -> None:
    """W4 m1: missing required fields raise ``ValueError`` with the
    offending field names, not a bare ``KeyError`` from the dict.
    """

    import pytest

    with pytest.raises(ValueError) as ei:
        OAuthCredentials.from_json({"access": "a", "expires": 1})
    assert "refresh" in str(ei.value)
    assert "missing required fields" in str(ei.value).lower()

    with pytest.raises(ValueError) as ei2:
        OAuthCredentials.from_json({})
    msg = str(ei2.value).lower()
    for field in ("refresh", "access", "expires"):
        assert field in msg
