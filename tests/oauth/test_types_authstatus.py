"""Sprint 6e · Phase 4.5 — AuthStatus / AuthSource / FallbackResolver type tests."""

from __future__ import annotations

import dataclasses
from typing import get_args

import pytest
from aelix_ai.oauth.types import AuthSource, AuthStatus, FallbackResolver


def test_auth_status_default_unconfigured() -> None:
    s = AuthStatus(configured=False)
    assert s.configured is False
    assert s.source is None
    assert s.label is None


def test_auth_status_is_frozen() -> None:
    """Pi parity: ``AuthStatus`` is immutable (frozen dataclass)."""

    s = AuthStatus(configured=True, source="stored")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.configured = False  # type: ignore[misc]


def test_auth_source_enum_matches_pi_six_values() -> None:
    """Pi parity: ``auth-storage.ts:38`` has 6 source values."""

    values = set(get_args(AuthSource))
    assert values == {
        "stored",
        "runtime",
        "environment",
        "fallback",
        "models_json_key",
        "models_json_command",
    }


def test_auth_status_with_label() -> None:
    s = AuthStatus(configured=False, source="environment", label="OPENAI_API_KEY")
    assert s.source == "environment"
    assert s.label == "OPENAI_API_KEY"


def test_fallback_resolver_type_alias_callable() -> None:
    """``FallbackResolver`` is a callable type alias."""

    def resolver(provider: str) -> str | None:
        return "key" if provider == "openai" else None

    # Smoke: assignment honors the alias (Pyright would catch mismatches).
    fr: FallbackResolver = resolver
    assert fr("openai") == "key"
    assert fr("other") is None
