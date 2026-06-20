"""Sprint 6e W6 (P-141) — ``resolve_config_value`` helper tests.

Pi parity: ``coding-agent/core/resolve-config-value.ts`` (SHA 734e08e).
"""

from __future__ import annotations

import pytest
from aelix_ai.oauth._resolve_config import (
    resolve_config_value,
    resolve_config_value_or_throw,
    resolve_config_value_uncached,
    resolve_headers_or_throw,
)


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


def test_no_cache_argument_re_executes_each_call(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """With no cache, each call re-forks — proven by a counter file that
    increments per invocation (a cached value would stay "1")."""

    counter = tmp_path / "n"
    cmd = f"!printf x >> {counter}; wc -c < {counter} | tr -d ' '"
    out1 = resolve_config_value(cmd)
    out2 = resolve_config_value(cmd)
    assert out1 == "1"
    assert out2 == "2"  # re-executed, not cached


# === ADR-0140 review hardening — bounded output + timeout =====================


def test_uncached_command_output_cap_returns_none() -> None:
    """A runaway producer exceeds the ~1 MB cap and is killed → None
    (mirrors Pi's execSync ENOBUFS→undefined; prevents OOM)."""

    assert resolve_config_value_uncached("!yes") is None


def test_uncached_command_timeout_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.oauth._resolve_config as rc

    monkeypatch.setattr(rc, "_COMMAND_TIMEOUT", 0.3)
    assert resolve_config_value_uncached("!sleep 5") is None


def test_cached_command_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    import aelix_ai.oauth._resolve_config as rc

    monkeypatch.setattr(rc, "_COMMAND_TIMEOUT", 0.3)
    with pytest.raises(subprocess.CalledProcessError):
        resolve_config_value("!sleep 5")


# === P0 #4 (ADR-0140) — request-time wrappers ==================================
# Pi parity: resolveConfigValueUncached / resolveConfigValueOrThrow /
# resolveHeadersOrThrow. Differ from resolve_config_value: command failures
# return None (not raise), empty env falls back to the literal.


def test_uncached_literal_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZZ_FICTIONAL_UNCACHED", raising=False)
    assert resolve_config_value_uncached("ZZ_FICTIONAL_UNCACHED") == (
        "ZZ_FICTIONAL_UNCACHED"
    )


def test_uncached_empty_env_falls_back_to_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pi ``process.env[config] || config`` — an env var set to "" → literal.
    monkeypatch.setenv("EMPTY_ENV_KEY", "")
    assert resolve_config_value_uncached("EMPTY_ENV_KEY") == "EMPTY_ENV_KEY"


def test_uncached_env_indirection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SET_ENV_KEY", "resolved")
    assert resolve_config_value_uncached("SET_ENV_KEY") == "resolved"


def test_uncached_command_returns_value() -> None:
    assert resolve_config_value_uncached("!printf done") == "done"


def test_uncached_failed_command_returns_none() -> None:
    # Non-zero exit / empty output → None (Pi catches + returns undefined).
    assert resolve_config_value_uncached("!false") is None
    assert resolve_config_value_uncached("!true") is None


def test_or_throw_returns_resolved_value() -> None:
    assert resolve_config_value_or_throw("!printf k", "test") == "k"


def test_or_throw_raises_for_failed_command() -> None:
    with pytest.raises(ValueError, match="from shell command: false"):
        resolve_config_value_or_throw("!false", "API key for x")


def test_headers_or_throw_resolves_each_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HDR_ENV", "hdr-secret")
    out = resolve_headers_or_throw(
        {"X-Lit": "literalval", "X-Env": "HDR_ENV"}, "provider \"x\""
    )
    assert out == {"X-Lit": "literalval", "X-Env": "hdr-secret"}


def test_headers_or_throw_none_returns_none() -> None:
    assert resolve_headers_or_throw(None, "x") is None
    assert resolve_headers_or_throw({}, "x") is None


def test_headers_or_throw_raises_on_failed_command() -> None:
    with pytest.raises(ValueError, match='header "X-Bad"'):
        resolve_headers_or_throw({"X-Bad": "!false"}, 'provider "x"')
