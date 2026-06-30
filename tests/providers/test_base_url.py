"""Unit tests for base-URL placeholder expansion (pi cloudflare-auth parity)."""

from __future__ import annotations

import pytest
from aelix_ai.providers._base_url import (
    expand_base_url,
    has_unexpanded_placeholders,
    unexpanded_placeholder_names,
)

_CF = (
    "https://gateway.ai.cloudflare.com/v1/"
    "{CLOUDFLARE_ACCOUNT_ID}/{CLOUDFLARE_GATEWAY_ID}/openai"
)


def test_expand_returns_input_when_no_placeholder() -> None:
    assert expand_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    assert expand_base_url(None) is None
    assert expand_base_url("") == ""


def test_expand_substitutes_set_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-123")
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw-456")
    assert expand_base_url(_CF) == (
        "https://gateway.ai.cloudflare.com/v1/acct-123/gw-456/openai"
    )
    assert not has_unexpanded_placeholders(_CF)
    assert unexpanded_placeholder_names(_CF) == []


def test_unset_env_var_leaves_token_and_flags_unexpanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw-456")
    expanded = expand_base_url(_CF)
    assert "{CLOUDFLARE_ACCOUNT_ID}" in expanded
    assert "gw-456" in expanded
    assert has_unexpanded_placeholders(_CF)
    assert unexpanded_placeholder_names(_CF) == ["CLOUDFLARE_ACCOUNT_ID"]


def test_empty_env_var_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw-456")
    assert has_unexpanded_placeholders(_CF)
    assert "CLOUDFLARE_ACCOUNT_ID" in unexpanded_placeholder_names(_CF)
