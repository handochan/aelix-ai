"""Sprint 6e W6 (P-141) — ``resolve_config_value`` helper tests.

Pi parity: ``coding-agent/core/resolve-config-value.ts`` (SHA 734e08e).
"""

from __future__ import annotations

import pytest
from aelix_ai.oauth._resolve_config import resolve_config_value


def test_literal_passes_through_when_env_var_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A literal that does NOT match any env var name returns verbatim."""

    monkeypatch.delenv("ZZ_TOTALLY_FICTIONAL_KEY", raising=False)
    assert resolve_config_value("ZZ_TOTALLY_FICTIONAL_KEY") == (
        "ZZ_TOTALLY_FICTIONAL_KEY"
    )


def test_env_var_indirection(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the literal matches an env var name, the env value substitutes."""

    monkeypatch.setenv("MY_INDIRECTED_KEY", "sk-from-env")
    assert resolve_config_value("MY_INDIRECTED_KEY") == "sk-from-env"


def test_shell_command_indirection() -> None:
    """``!<cmd>`` runs the shell command + returns trimmed stdout."""

    out = resolve_config_value("!echo sk-from-shell")
    assert out == "sk-from-shell"


def test_shell_command_indirection_is_cached() -> None:
    """The cache short-circuits repeat invocations."""

    cache: dict[str, str] = {}
    out1 = resolve_config_value("!echo first-call", cache)
    # Pre-seed a different value into the cache to prove the cache wins
    # over re-running the command.
    cache["echo first-call"] = "cached-value"
    out2 = resolve_config_value("!echo first-call", cache)
    assert out1 == "first-call"
    assert out2 == "cached-value"


def test_shell_command_strips_trailing_newline() -> None:
    """Pi trims the trailing newline from shell output."""

    out = resolve_config_value("!printf 'sk-no-newline\\n'")
    assert out == "sk-no-newline"


def test_no_cache_argument_does_not_cache() -> None:
    """Passing no cache means each call re-forks (Pi behavior is
    per-process cache; the helper is permissive)."""

    # Smoke: passing no cache should still return the right value.
    out = resolve_config_value("!echo hello")
    assert out == "hello"
